from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from market_data_platform.artifacts import configured_data_input_path, resolve_data_input_path
from market_data_platform.manifest import load_manifest_summary
from market_data_platform.paths import (
    candidate_asset_paths,
    current_contract_path,
    normalize_market,
    normalize_provider,
    resolve_artifacts_root,
)


def infer_manifest_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    candidates: list[Path] = []
    if path.is_dir():
        candidates.append(path / "manifest.yml")
        candidates.append(path / "_operational_receipt.json")
    else:
        candidates.append(path.with_name(f"{path.stem}.manifest.yml"))
        candidates.append(path.parent / "manifest.yml")
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def describe_input_path(
    value: object | None,
    *,
    current_contract: Mapping[str, Any] | None = None,
    current_contract_path: Path | None = None,
) -> dict[str, Any] | None:
    """Describe a configured input path and its current-contract reference."""
    value_text = str(value).strip() if value is not None else ""
    if not value_text:
        return None
    configured_path = configured_data_input_path(value_text)
    resolved_path = resolve_data_input_path(value_text)
    manifest_path = infer_manifest_path(configured_path)
    manifest = load_manifest_summary(manifest_path) if manifest_path is not None else None
    if manifest_path is not None and isinstance(manifest, Mapping):
        manifest = dict(manifest)
        manifest.setdefault("manifest_path", str(manifest_path))
        if "provider" not in manifest:
            try:
                payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                payload = None
            if isinstance(payload, Mapping) and payload.get("provider"):
                manifest["provider"] = str(payload["provider"]).strip()
    current_reference = None
    matched = match_current_contract_entry(
        current_contract,
        configured_path=configured_path,
        resolved_path=resolved_path,
    )
    if matched is not None and current_contract_path is not None:
        asset_key, entry = matched
        contract_raw = current_contract.get("contract") if current_contract else None
        contract_meta = contract_raw if isinstance(contract_raw, Mapping) else {}
        current_reference = {
            "contract_name": str(contract_meta.get("name") or "hk_current"),
            "contract_path": str(current_contract_path),
            "asset_key": asset_key,
            "alias_path": entry.get("alias_path"),
            "resolved_path": entry.get("resolved_path"),
            "manifest_path": entry.get("manifest_path"),
            "manifest": entry.get("manifest"),
            "as_of": entry.get("as_of"),
        }
    return {
        "raw": value_text,
        "configured_path": str(configured_path),
        "resolved_path": str(resolved_path),
        "path_kind": _path_kind(configured_path),
        "exists": configured_path.exists(),
        "is_symlink": configured_path.is_symlink(),
        "points_to_latest_name": "latest" in value_text.lower(),
        "manifest_path": str(manifest_path) if manifest_path is not None else None,
        "manifest": manifest,
        "current_contract": current_reference,
    }


def match_current_contract_entry(
    contract: Mapping[str, Any] | None,
    *,
    configured_path: Path | None,
    resolved_path: Path | None,
) -> tuple[str, dict[str, Any]] | None:
    """Find the current-contract asset matching a configured or resolved path."""
    if not isinstance(contract, Mapping):
        return None
    assets = contract.get("assets")
    if not isinstance(assets, Mapping):
        return None
    configured_text = str(configured_path) if configured_path is not None else None
    resolved_text = str(resolved_path) if resolved_path is not None else None
    for asset_key, entry in assets.items():
        if not isinstance(entry, Mapping):
            continue
        alias_path = str(entry.get("alias_path") or "").strip() or None
        contract_resolved = str(entry.get("resolved_path") or "").strip() or None
        if configured_text and alias_path and configured_text == alias_path:
            return str(asset_key), dict(entry)
        if resolved_text and contract_resolved and resolved_text == contract_resolved:
            return str(asset_key), dict(entry)
    return None


def load_current_contract(path: str | Path) -> dict[str, Any] | None:
    """Load a current-contract mapping without mutating platform state."""
    contract_path = Path(path).expanduser().resolve()
    if not contract_path.exists():
        return None
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def path_kind(path: Path | None) -> str | None:
    """Classify a path for read-only provenance reporting."""
    if path is None:
        return None
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    if not path.exists():
        return "missing"
    return "other"


def _detect_as_of(value: object | None) -> str | None:
    text = str(value or "")
    matches = re.findall(r"20\d{6}", text)
    return matches[-1] if matches else None


def _path_kind(path: Path) -> str:
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "missing"


def _requires_explicit_fundamentals_as_of(manifest: Mapping[str, Any]) -> bool:
    schema_version = str(manifest.get("schema_version") or "")
    return any(
        marker in schema_version
        for marker in (
            ".fundamentals.normalized.",
            ".fundamentals.pit.",
        )
    )


def describe_current_path(path: Path) -> dict[str, Any]:
    alias_path = path.expanduser()
    if not alias_path.is_absolute():
        alias_path = alias_path.absolute()
    resolved_path = alias_path.resolve(strict=False)
    manifest_path = infer_manifest_path(alias_path)
    manifest = load_manifest_summary(manifest_path) if manifest_path is not None else None
    as_of = None
    requires_explicit_as_of = False
    if isinstance(manifest, Mapping):
        requires_explicit_as_of = _requires_explicit_fundamentals_as_of(manifest)
        if requires_explicit_as_of:
            schema_version = str(manifest.get("schema_version") or "")
            if schema_version.endswith((".normalized.v2", ".pit.v2")):
                as_of = str(manifest.get("as_of_date") or "").strip() or None
        else:
            as_of = (
                str(manifest.get("as_of_date") or manifest.get("query_end_date") or "").strip()
                or None
            )
    if not as_of and not requires_explicit_as_of:
        as_of = _detect_as_of(resolved_path.name)
    available = alias_path.exists()
    empty_manifest = (
        isinstance(manifest, Mapping)
        and isinstance(manifest.get("totals"), Mapping)
        and manifest["totals"].get("rows") == 0
    )
    missing_manifest = available and alias_path.is_dir() and manifest is None
    expected_files = (
        manifest.get("totals", {}).get("files")
        if isinstance(manifest, Mapping) and isinstance(manifest.get("totals"), Mapping)
        else None
    )
    actual_files = len(tuple(alias_path.rglob("*.parquet"))) if alias_path.is_dir() else None
    file_count_mismatch = (
        available
        and alias_path.is_dir()
        and isinstance(expected_files, int)
        and expected_files != actual_files
    )
    published = (
        available and not empty_manifest and not missing_manifest and not file_count_mismatch
    )
    return {
        "alias_path": str(alias_path),
        "exists": available,
        "availability": "available" if published else "unavailable",
        "availability_reason": (
            "Published manifest contains zero rows."
            if empty_manifest
            else "Manifest file count does not match stored Parquet files."
            if file_count_mismatch
            else "Published directory has no manifest."
            if missing_manifest
            else None
            if available
            else "No published asset exists at the configured path."
        ),
        "next_action": (
            None
            if published
            else "refresh_or_retire"
            if empty_manifest
            else "republish_consistently"
            if file_count_mismatch
            else "republish_with_manifest"
            if missing_manifest
            else "publish_or_retire"
        ),
        "is_symlink": alias_path.is_symlink(),
        "path_kind": _path_kind(alias_path),
        "resolved_path": str(resolved_path),
        "resolved_name": resolved_path.name,
        "manifest_path": str(manifest_path) if manifest_path is not None else None,
        "manifest": manifest,
        "as_of": as_of,
    }


def build_current_contract(
    artifacts_root: str | Path | None = None,
    *,
    market: str | None = None,
    provider: str | None = None,
    generated_by: str | None = None,
    target_date: str | None = None,
) -> dict[str, Any]:
    root = resolve_artifacts_root(artifacts_root)
    market = normalize_market(market)
    provider = normalize_provider(provider, market=market)
    contract_path = current_contract_path(root, market=market)
    contract_name = f"{market}_current"
    return {
        "contract": {
            "name": contract_name,
            "market": market,
            "provider": provider,
            "version": 1,
            "artifacts_root": str(root),
            "contract_path": str(contract_path),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "generated_by": generated_by,
            "target_date": target_date,
        },
        "assets": {
            asset_key: describe_current_path(path)
            for asset_key, path in candidate_asset_paths(
                root,
                market=market,
                provider=provider,
            ).items()
        },
    }


def write_current_contract(path: str | Path, payload: Mapping[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
