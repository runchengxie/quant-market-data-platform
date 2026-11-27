"""Hash-bound semantic debt: acquisition may continue, publication may not."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "tushare.minute.semantic_quarantine.v1"
SCHEMA_V2 = "tushare.minute.semantic_quarantine.v2"
ZERO_VOLUME = "zero_volume_nonzero_amount_rows"
REGISTRY = Path("metadata/minute_backfill/reverse_scheduler/semantic_quarantine.json")
PASSED = {"passed_full_market", "passed_requested_universe_only", "passed_sh_sz_with_bj_omissions"}


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def reference(root: Path, path: Path) -> dict[str, str]:
    path = path.resolve(strict=True)
    return {"path": str(path.relative_to(root.resolve())), "sha256": file_hash(path)}


def bound_path(root: Path, ref: dict) -> Path:
    path = (root / ref["path"]).resolve(strict=True)
    path.relative_to(root.resolve())
    if file_hash(path) != ref["sha256"]:
        raise ValueError("quarantine evidence hash changed")
    return path


def _validate_registry_entry(date: Any, entry: Any, schema: str) -> None:
    if not isinstance(date, str) or len(date) != 8 or not date.isdigit():
        raise ValueError("invalid semantic quarantine date")
    if not isinstance(entry, dict) or entry.get("trade_date") != date:
        raise ValueError("invalid semantic quarantine entry")
    if entry.get("status") != "quarantined":
        raise ValueError("invalid semantic quarantine disposition")
    if any(key in entry for key in ("anomaly_kind", "symbols", "scope")):
        _validate_zero_volume_entry(entry, schema)
    elif not entry.get("symbol"):
        raise ValueError("invalid semantic quarantine disposition")
    for name in ("partition", "sidecar", "acceptance", "requery"):
        ref = entry.get(name)
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            raise ValueError("invalid semantic quarantine reference")
        if not isinstance(ref.get("sha256"), str) or len(ref["sha256"]) != 64:
            raise ValueError("invalid semantic quarantine digest")


def _validate_zero_volume_entry(entry: dict, schema: str) -> None:
    symbols, issues = entry.get("symbols"), entry.get("issues")
    if (
        schema != SCHEMA_V2
        or entry.get("anomaly_kind") != ZERO_VOLUME
        or entry.get("scope") != "trade_date"
        or "symbol" in entry
        or not isinstance(symbols, list)
        or not symbols
        or any(
            not isinstance(s, str) or re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s) is None for s in symbols
        )
        or symbols != sorted(set(symbols))
        or entry.get("publication_allowed") is not False
        or entry.get("source_values_modified") is not False
        or not isinstance(entry.get("reason"), str)
        or not entry["reason"].strip()
        or not isinstance(issues, dict)
        or any(type(v) is not int or v < 0 for v in issues.values())
        or {k for k, v in issues.items() if v} != {ZERO_VOLUME}
        or issues[ZERO_VOLUME] < len(symbols)
    ):
        raise ValueError("invalid semantic quarantine disposition")


def load_registry(root: Path) -> dict:
    path = root / REGISTRY
    if not path.exists():
        return {"schema_version": SCHEMA, "dates": {}}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("schema_version") not in (SCHEMA, SCHEMA_V2):
        raise ValueError("invalid semantic quarantine registry")
    dates = data.get("dates")
    if not isinstance(dates, dict):
        raise ValueError("invalid semantic quarantine dates")
    for date, entry in dates.items():
        _validate_registry_entry(date, entry, data["schema_version"])
    return data


def _additional_markers(marker: Path) -> list[Path]:
    return sorted((marker.parent / "semantic-audit-additional-blocks").glob("*.json"))


def _acceptance_path(root: Path, marker: Path) -> Path:
    block = json.loads(marker.read_text())
    acceptance = Path(block["acceptance"]).resolve(strict=True)
    acceptance.relative_to(root.resolve())
    return acceptance


def _covered(root: Path, marker: Path, entries: dict) -> bool:
    acceptance = _acceptance_path(root, marker)
    digest = file_hash(acceptance)
    report = json.loads(acceptance.read_text())
    partitions = report["partitions"]
    if not isinstance(partitions, list) or not partitions:
        return False
    failures = 0
    for record in partitions:
        if record.get("status") in PASSED:
            if record.get("binding_matches") is not True or any(record["issues"].values()):
                return False
            continue
        failures += 1
        entry = entries.get(record.get("trade_date"), {})
        if not (
            record.get("status") == "failed_semantics"
            and record.get("binding_matches") is True
            and entry.get("acceptance", {}).get("sha256") == digest
            and (root / entry["acceptance"]["path"]).resolve() == acceptance
            and entry.get("issues") == record.get("issues")
            and entry["partition"]["sha256"] == record.get("partition_sha256")
            and entry["sidecar"]["sha256"] == record.get("sidecar_sha256")
            and (root / entry["partition"]["path"]).resolve() == Path(record["path"]).resolve()
            and (root / entry["sidecar"]["path"]).resolve() == Path(record["sidecar"]).resolve()
        ):
            return False
    return failures > 0


def quarantine_status(root: Path, marker: Path | None) -> dict[str, Any]:
    """Fail closed for unknown blocks, malformed registries or changed evidence."""
    root = root.resolve()
    result: dict[str, Any] = {
        "blocked": False,
        "dates": [],
        "errors": [],
        "marker_quarantined": False,
    }
    try:
        entries = load_registry(root)["dates"]
        result["dates"] = sorted(entries)
        for entry in entries.values():
            for name in ("partition", "sidecar", "acceptance", "requery"):
                bound_path(root, entry[name])
        if marker is not None:
            if marker.exists() or marker.is_symlink():
                result["marker_quarantined"] = _covered(root, marker, entries)
                result["blocked"] = not result["marker_quarantined"]
            if not result["blocked"]:
                result["blocked"] = any(
                    not _covered(root, extra, entries) for extra in _additional_markers(marker)
                )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result.update(blocked=True, errors=[f"invalid quarantine evidence: {type(exc).__name__}"])
    return result


def reject_quarantined_dates(path: Path, dates: list[str]) -> None:
    """Reject affected publication dates using the owning artifact root registry."""
    lexical, resolved = path.absolute(), path.resolve()
    roots = dict.fromkeys((lexical, *lexical.parents, resolved, *resolved.parents))
    for root in roots:
        if not (root / REGISTRY).exists():
            continue
        entries = load_registry(root)["dates"]
        affected = sorted(set(dates).intersection(entries))
        if affected:
            raise ValueError(
                f"Semantically quarantined minute dates cannot be published: {affected}"
            )


def register_quarantine(  # noqa: PLR0913 - explicit evidence registration interface
    root: Path,
    runtime: Path,
    acceptance: Path,
    requery: Path,
    *,
    trade_date: str,
    symbol: str,
    reason: str,
) -> dict:
    """Explicitly register reproduced source evidence under the shared writer lock."""
    from quant_market_data_platform._minute_history_lock import history_writer_lock
    from quant_market_data_platform._minute_semantic_registration import register

    with history_writer_lock(root):
        return register(
            root.resolve(),
            runtime,
            acceptance,
            requery,
            trade_date=trade_date,
            symbol=symbol,
            reason=reason,
        )


def register_zero_volume_quarantine(  # noqa: PLR0913 - explicit evidence registration interface
    root: Path,
    runtime: Path,
    acceptance: Path,
    requery: Path,
    *,
    trade_date: str,
    symbols: list[str],
    reason: str,
    dry_run: bool = False,
) -> dict:
    """Quarantine a date using exact requery evidence for every zero-volume failure."""
    from quant_market_data_platform._minute_history_lock import history_writer_lock
    from quant_market_data_platform._minute_zero_volume_quarantine import register

    if dry_run:
        return register(
            root.resolve(),
            runtime,
            acceptance,
            requery,
            trade_date=trade_date,
            symbols=symbols,
            reason=reason,
            dry_run=True,
        )
    with history_writer_lock(root):
        return register(
            root.resolve(),
            runtime,
            acceptance,
            requery,
            trade_date=trade_date,
            symbols=symbols,
            reason=reason,
        )
