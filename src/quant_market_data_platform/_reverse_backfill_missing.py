"""Derive a repair queue without changing partition completeness or source data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from quant_market_data_platform.minute_candidate import _atomic_write_json, _read_json
from quant_market_data_platform.providers._a_share_mins_partition import (
    _is_cn_stock_ts_code,
    _partition_binding_matches,
)


def missing_receipt_dates(receipt: dict[str, Any]) -> set[str]:
    """Global failures and unfinished segments must retain normal resume behavior."""
    if receipt.get("status") != "partial" or not receipt.get("segments"):
        return set()
    dates: set[str] = set()
    for segment in receipt["segments"]:
        if segment.get("status") == "complete":
            continue
        if (
            segment.get("status") != "partial"
            or segment.get("error", {}).get("type") != "MinuteMirrorIncompleteDatesError"
        ):
            return set()
        dates.update(segment.get("result", {}).get("partial_dates", []))
    return dates


def _missing_symbols(path: Path, payload: dict[str, Any]) -> list[str]:
    if (
        payload.get("schema_version") != "tushare.a_share.minute_partition.v3"
        or payload.get("status") != "partial"
        or payload.get("freq") != "1min"
    ):
        return []
    expected = set(payload.get("expected_symbols", []))
    completed = set(payload.get("completed_symbols", []))
    missing = set(payload.get("missing_request_symbols", []))
    partition = payload.get("partition") or {}
    if (
        not completed
        or not missing
        or not completed.issubset(expected)
        or missing != expected - completed
        or any(not _is_cn_stock_ts_code(code) for code in expected)
        or payload.get("error", {}).get("type") != "IncompleteBatch"
        or payload["error"].get("issues") != dict.fromkeys(missing, "missing from response")
        or set(partition.get("symbols", [])) != completed
        or set(partition.get("complete_symbols", [])) != completed
        or partition.get("rows") != 241 * len(completed)
        or payload.get("expected_bars_per_symbol") != 241
        or path.parent.name != f"trade_date={payload.get('trade_date')}"
        or not _partition_binding_matches(payload, path.parent)
    ):
        return []
    return sorted(missing)


def _receipt_index(staging: Path) -> tuple[dict[str, Any], dict[str, int]]:
    receipts: dict[str, Any] = {}
    failed: dict[str, int] = {}
    metadata = staging.parents[1] / "metadata/minute_backfill/reverse_scheduler/receipts"
    for path in metadata.glob("*.receipt.json"):
        try:
            receipt = _read_json(path)
            modified = path.stat().st_mtime_ns
            receipts[str(Path(receipt["output"]["run_root"]).resolve())] = (receipt, modified)
            if receipt.get("status") != "complete" and not missing_receipt_dates(receipt):
                plan = _read_json(Path(receipt["plan_path"]))
                for segment in plan["identity"]["segments"]:
                    for date in segment["dates"]:
                        failed[date] = max(failed.get(date, 0), modified)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return receipts, failed


def missing_queue(
    staging: Path,
    settled: set[str],
    *,
    report_path: Path | None = None,
    selected_dates: set[str] | None = None,
) -> dict[str, Any]:
    receipts, failed = _receipt_index(staging)
    latest: dict[str, Path] = {}
    for path in staging.glob("runs/*/data/trade_date=*/_minute_mirror.json"):
        date = path.parent.name.removeprefix("trade_date=")
        if selected_dates is not None and date not in selected_dates:
            continue
        if date not in latest or path.stat().st_mtime_ns > latest[date].stat().st_mtime_ns:
            latest[date] = path
    dates: dict[str, Any] = {}
    for date, path in sorted(latest.items()):
        if date in settled:
            continue
        try:
            receipt, modified = receipts.get(str(path.parents[2].resolve()), ({}, 0))
            symbols = _missing_symbols(path, _read_json(path))
            if (
                symbols
                and date in missing_receipt_dates(receipt)
                and modified >= failed.get(date, 0)
            ):
                dates[date] = {"missing_symbols": symbols, "sidecars": [str(path)]}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    queue = {
        "schema_version": "tushare.minute.deferred_missing.v1",
        "writes_production": False,
        "status": "pending_repair" if dates else "empty",
        "dates": dates,
    }
    if report_path is not None:
        _atomic_write_json(report_path, queue)
    return queue
