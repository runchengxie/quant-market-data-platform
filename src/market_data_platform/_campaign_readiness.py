"""Readiness marker, campaign locations, and checkpoint inventory for the campaign runner."""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from market_data_platform._campaign_common import (
    COMPLETENESS_FILENAME,
    READINESS_SCHEMA_VERSION,
    CampaignAccountingError,
    CampaignFatalError,
    _atomic_write_json,
    _now,
    _read_json,
    _readiness_summary_is_valid,
    _sha256,
)
from market_data_platform._campaign_progress import _checkpoint_rows
from market_data_platform._campaign_receipts import (
    _date_receipt,
    _load_lane_receipt,
    _validate_completed_phase_receipts,
)


def _runner():
    return sys.modules["market_data_platform.tushare_minute_replacement_campaign_runner"]


def _readiness_path(manifest_path: Path, manifest: dict[str, Any]) -> Path:
    configured = manifest.get("readiness_path")
    return (
        Path(str(configured)) if configured else manifest_path.parent / "acquisition-readiness.json"
    )


def _no_data_exception_path(manifest_path: Path, manifest: dict[str, Any]) -> Path:
    configured = manifest.get("provider_no_data_exceptions_path")
    return (
        Path(str(configured))
        if configured
        else manifest_path.parent / "provider_no_data_exceptions.v1.json"
    )


def _validated_no_data_exceptions(
    manifest_path: Path, manifest: dict[str, Any]
) -> dict[str, Any] | None:
    path = _no_data_exception_path(manifest_path, manifest)
    if not path.exists():
        return None
    payload = _read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        "market_data_platform.minute_provider_no_data_exceptions.v1"
    ):
        raise CampaignFatalError(f"Invalid provider no-data exception policy: {path}")
    policy = payload.get("policy")
    exceptions = payload.get("exceptions")
    if not isinstance(policy, dict) or not isinstance(exceptions, list):
        raise CampaignFatalError(f"Malformed provider no-data exception policy: {path}")
    protected = (
        "allow_exclusion_from_provider_verified_universe",
        "allow_synthetic_bars",
        "allow_complete_partition_promotion",
    )
    if any(policy.get(key) is not False for key in protected):
        raise CampaignFatalError(
            f"Provider no-data exception policy weakens fail-closed guarantees: {path}"
        )
    codes: set[str] = set()
    for item in exceptions:
        if not isinstance(item, dict) or not str(item.get("ts_code", "")).strip():
            raise CampaignFatalError(f"Malformed provider no-data exception entry: {path}")
        code = str(item["ts_code"]).strip().upper()
        if code in codes:
            raise CampaignFatalError(f"Duplicate provider no-data exception: {code}")
        codes.add(code)
    return {"path": str(path), "sha256": _sha256(path), "count": len(exceptions)}


def _validated_readiness(manifest_path: Path, manifest: dict[str, Any]) -> dict[str, Any] | None:
    path = _readiness_path(manifest_path, manifest)
    if not path.exists():
        return None
    payload = _read_json(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != READINESS_SCHEMA_VERSION
        or payload.get("manifest_sha256") != _sha256(manifest_path)
        or payload.get("acquisition_complete") is not True
        or payload.get("structural_ready") is not True
        or payload.get("semantic_audit") != "pending"
        or payload.get("promotion_ready") is not False
        or payload.get("cutover_performed") is not False
        or payload.get("writes_production") is not False
        or not _readiness_summary_is_valid(
            payload,
            manifest_path=manifest_path,
            ledger_path=Path(str(manifest["ledger_path"])),
            expected_dates=len(_campaign_locations(manifest)),
        )
    ):
        raise CampaignFatalError(f"Campaign readiness marker is invalid or stale: {path}")
    return payload


def _campaign_locations(manifest: dict[str, Any]) -> dict[str, Path]:
    locations: dict[str, Path] = {}

    def add(trade_date: str, data_root: str | Path) -> None:
        root = Path(data_root).expanduser().resolve()
        previous = locations.get(trade_date)
        if previous is not None and previous != root:
            raise CampaignFatalError(
                f"Campaign date {trade_date} resolves to multiple roots: {previous}, {root}"
            )
        locations[trade_date] = root

    for group in ("complete", "partial"):
        for trade_date, item in manifest.get("baseline", {}).get(group, {}).items():
            add(str(trade_date), item["data_root"])
    for day in manifest.get("days", []):
        for phase in day.get("phases", []):
            for lane in phase.get("lanes", {}).values():
                for trade_date in lane.get("dates", []):
                    add(str(trade_date), lane["data_root"])
    target_count = manifest.get("target", {}).get("dates")
    if isinstance(target_count, int) and len(locations) != target_count:
        raise CampaignFatalError(
            "Campaign target reconciliation mismatch: "
            f"expected={target_count} actual={len(locations)}"
        )
    return locations


def _write_readiness_marker(
    manifest_path: Path,
    manifest: dict[str, Any],
    ledger_path: Path,
) -> dict[str, Any]:
    exception_policy = _validated_no_data_exceptions(manifest_path, manifest)
    receipts: dict[str, dict[str, Any]] = {}
    total_rows = 0
    for trade_date, data_root in sorted(_campaign_locations(manifest).items()):
        receipt = _runner()._validated_date(data_root, trade_date)
        if receipt is None:
            raise CampaignFatalError(
                f"Campaign completion reconciliation found an incomplete date: {trade_date}"
            )
        date_receipt = _date_receipt(receipt)
        receipts[trade_date] = date_receipt
        total_rows += int(date_receipt["rows"])
    for day in manifest.get("days", []):
        for phase in day.get("phases", []):
            if not _validate_completed_phase_receipts(phase):
                raise CampaignFatalError(
                    "Campaign completion reconciliation found a non-complete lane receipt"
                )
            for lane_name, lane in phase.get("lanes", {}).items():
                _load_lane_receipt(
                    lane_name,
                    lane,
                    required=lane.get("plan_id") is not None,
                )
    payload: dict[str, Any] = {
        "schema_version": READINESS_SCHEMA_VERSION,
        "generated_at": _now(),
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "ledger_path": str(ledger_path),
        "ledger_sha256_at_reconciliation": _sha256(ledger_path),
        "acquisition_complete": True,
        "structural_ready": True,
        "semantic_audit": "pending",
        "promotion_ready": False,
        "cutover_performed": False,
        "writes_production": False,
        "provider_no_data_exceptions": exception_policy,
        "dates_complete": len(receipts),
        "rows": total_rows,
        "date_receipts_sha256": hashlib.sha256(
            json.dumps(receipts, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    _atomic_write_json(_readiness_path(manifest_path, manifest), payload)
    return payload


def _checkpoint_inventory(manifest: dict[str, Any]) -> dict[str, Any]:
    locations = _campaign_locations(manifest)
    immutable_complete = set(manifest.get("baseline", {}).get("complete", {}))
    complete = len(immutable_complete)
    partial = 0
    rows = sum(
        int(item.get("rows", 0))
        for item in manifest.get("baseline", {}).get("complete", {}).values()
    )
    complete_mutable_rows: list[int] = []
    incomplete_expected_rows = 0
    next_item: dict[str, Any] | None = None
    for trade_date, data_root in sorted(locations.items()):
        if trade_date in immutable_complete:
            continue
        sidecar = data_root / f"trade_date={trade_date}" / COMPLETENESS_FILENAME
        if not sidecar.exists():
            if next_item is None:
                next_item = {
                    "trade_date": trade_date,
                    "status": "missing",
                    "data_root": str(data_root),
                }
            continue
        payload = _read_json(sidecar)
        if not isinstance(payload, dict):
            raise CampaignAccountingError(f"Minute sidecar must contain an object: {sidecar}")
        if str(payload.get("trade_date", "")) != trade_date:
            raise CampaignAccountingError(f"Minute sidecar trade_date mismatch: {sidecar}")
        state = str(payload.get("status", ""))
        if state not in {"partial", "complete"}:
            raise CampaignAccountingError(f"Minute sidecar has invalid status: {sidecar}")
        partition_rows = _checkpoint_rows((data_root, trade_date))
        rows += partition_rows
        if state == "complete":
            complete += 1
            complete_mutable_rows.append(partition_rows)
        else:
            partial += 1
            expected_symbols = len(payload.get("expected_symbols") or [])
            incomplete_expected_rows += max(0, expected_symbols * 241 - partition_rows)
            if next_item is None:
                next_item = {
                    "trade_date": trade_date,
                    "status": state,
                    "data_root": str(data_root),
                    "rows": partition_rows,
                    "completed_symbols": len(payload.get("completed_symbols") or []),
                    "expected_symbols": expected_symbols,
                }
    remaining = max(0, len(locations) - complete - partial)
    average_rows = int(statistics.median(complete_mutable_rows)) if complete_mutable_rows else 0
    estimated_remaining_rows = incomplete_expected_rows + remaining * average_rows
    return {
        "target_dates": len(locations),
        "complete_dates": complete,
        "partial_dates": partial,
        "remaining_dates": remaining,
        "persisted_rows": rows,
        "estimated_remaining_rows": estimated_remaining_rows or None,
        "next": next_item,
    }


__all__ = [
    "_campaign_locations",
    "_checkpoint_inventory",
    "_readiness_path",
    "_no_data_exception_path",
    "_validated_no_data_exceptions",
    "_validated_readiness",
    "_write_readiness_marker",
]
