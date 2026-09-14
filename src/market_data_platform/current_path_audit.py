from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

DATE_SCOPED_ASSETS = frozenset(
    {
        "adj_factor",
        "daily",
        "daily_basic",
        "daily_clean",
        "limit_status",
    }
)


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _reference_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink" if path.exists() else "broken_symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "missing"


def _safe_resolve(path: Path) -> tuple[Path, str | None]:
    try:
        return path.resolve(strict=path.is_symlink()), None
    except (OSError, RuntimeError) as exc:
        return path.absolute(), f"{type(exc).__name__}: {exc}"


def _manifest_end_date(entry: Mapping[str, Any]) -> str | None:
    manifest = _mapping(entry.get("manifest"))
    value = manifest.get("query_end_date") or entry.get("as_of")
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[:8] if len(digits) >= 8 else None


def _resolved_name_date(path: Path) -> str | None:
    matches = re.findall(r"(?<!\d)(\d{8})(?!\d)", path.name)
    return matches[-1] if matches else None


def _latest_semantics(path: Path, reference_kind: str) -> str:
    if reference_kind in {"broken_symlink", "missing"}:
        return "missing"
    if "latest" not in path.name.lower():
        return "not_latest_named"
    if reference_kind == "symlink":
        return "stable_symlink"
    if reference_kind == "file":
        return "direct_file"
    if reference_kind == "directory":
        return "legacy_mutable_directory"
    return "missing"


def _issue(
    *,
    asset: str,
    check: str,
    message: str,
    severity: str = "warning",
) -> dict[str, str]:
    return {
        "asset": asset,
        "check": check,
        "severity": severity,
        "message": message,
    }


def _audit_asset(asset: str, raw_entry: Mapping[str, Any]) -> dict[str, Any]:
    entry = _mapping(raw_entry)
    path_text = str(entry.get("alias_path") or entry.get("resolved_path") or "").strip()
    if not path_text:
        issue = _issue(
            asset=asset,
            check="missing_current_asset",
            message="Current contract candidate has no alias_path or resolved_path",
        )
        return {
            "alias_path": "",
            "reference_kind": "missing",
            "latest_semantics": "missing",
            "resolved_path": "",
            "manifest_end_date": _manifest_end_date(entry),
            "resolved_name_date": None,
            "issues": [issue],
        }
    alias_path = Path(path_text)
    kind = _reference_kind(alias_path)
    resolved_path, resolution_error = _safe_resolve(alias_path)
    latest_semantics = _latest_semantics(alias_path, kind)
    issues: list[dict[str, str]] = []

    if kind in {"broken_symlink", "missing"}:
        issues.append(
            _issue(
                asset=asset,
                check="missing_current_asset",
                message=f"Current contract candidate is missing: {alias_path}",
            )
        )
    if resolution_error is not None:
        issues.append(
            _issue(
                asset=asset,
                check="unresolvable_current_asset",
                message=f"Current contract candidate cannot be resolved: {resolution_error}",
            )
        )
    if latest_semantics == "legacy_mutable_directory":
        issues.append(
            _issue(
                asset=asset,
                check="mutable_latest_directory",
                message=(
                    "A latest-named directory is mutable in place; new publications should use "
                    f"an immutable version plus a symlink: {alias_path}"
                ),
            )
        )
    if kind == "symlink" and resolved_path.is_dir() and "latest" in resolved_path.name.lower():
        issues.append(
            _issue(
                asset=asset,
                check="symlink_to_mutable_latest_directory",
                message=(
                    "The stable alias resolves to another latest-named directory; "
                    f"publish an immutable final target: {alias_path} -> {resolved_path}"
                ),
            )
        )

    recorded_resolved_text = str(entry.get("resolved_path") or "").strip()
    if recorded_resolved_text:
        recorded_resolved_path, recorded_resolution_error = _safe_resolve(
            Path(recorded_resolved_text)
        )
        if recorded_resolution_error is not None:
            issues.append(
                _issue(
                    asset=asset,
                    check="unresolvable_recorded_path",
                    message=(
                        "The contract's recorded resolved_path cannot be resolved: "
                        f"{recorded_resolution_error}"
                    ),
                )
            )
        elif kind not in {"broken_symlink", "missing"} and recorded_resolved_path != resolved_path:
            issues.append(
                _issue(
                    asset=asset,
                    check="recorded_resolved_path_mismatch",
                    message=(
                        f"Contract records {recorded_resolved_path}, while the alias resolves to "
                        f"{resolved_path}: {alias_path}"
                    ),
                )
            )

    manifest_end_date = _manifest_end_date(entry)
    resolved_name_date = _resolved_name_date(resolved_path)
    if (
        asset in DATE_SCOPED_ASSETS
        and kind not in {"broken_symlink", "missing"}
        and manifest_end_date is not None
        and resolved_name_date is not None
        and manifest_end_date != resolved_name_date
    ):
        issues.append(
            _issue(
                asset=asset,
                check="resolved_name_date_drift",
                message=(
                    f"Resolved name ends at {resolved_name_date}, while the manifest ends at "
                    f"{manifest_end_date}: {resolved_path}"
                ),
            )
        )

    return {
        "alias_path": str(alias_path),
        "reference_kind": kind,
        "latest_semantics": latest_semantics,
        "resolved_path": str(resolved_path),
        "manifest_end_date": manifest_end_date,
        "resolved_name_date": resolved_name_date,
        "issues": issues,
    }


def audit_current_contract_paths(
    contract: Mapping[str, Any],
    *,
    contract_path: str | Path | None = None,
) -> dict[str, Any]:
    """Audit current-path availability and compatibility semantics without mutating assets."""
    contract_meta = _mapping(contract.get("contract"))
    assets = _mapping(contract.get("assets"))
    audited = {
        asset: _audit_asset(asset, raw_entry)
        for asset, raw_entry in sorted(assets.items())
        if isinstance(raw_entry, Mapping)
    }
    issues = [issue for entry in audited.values() for issue in entry["issues"]]
    kinds = Counter(entry["reference_kind"] for entry in audited.values())
    semantics = Counter(entry["latest_semantics"] for entry in audited.values())
    return {
        "schema_version": "market_data_platform.current_path_audit.v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "contract_path": str(contract_path or contract_meta.get("contract_path") or ""),
        "market": contract_meta.get("market"),
        "provider": contract_meta.get("provider"),
        "target_date": contract_meta.get("target_date"),
        "summary": {
            "assets": len(audited),
            "reference_kinds": dict(sorted(kinds.items())),
            "latest_semantics": dict(sorted(semantics.items())),
            "missing_assets": kinds.get("missing", 0) + kinds.get("broken_symlink", 0),
            "date_drift_assets": sum(
                issue["check"] == "resolved_name_date_drift" for issue in issues
            ),
            "mutable_final_target_assets": sum(
                issue["check"] == "symlink_to_mutable_latest_directory" for issue in issues
            ),
            "issues": len(issues),
        },
        "issues": issues,
        "assets": audited,
    }
