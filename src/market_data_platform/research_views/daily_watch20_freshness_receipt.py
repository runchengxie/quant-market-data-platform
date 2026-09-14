"""Validate published minute-data coverage for DailyWatch20 freshness checks."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from market_data_platform.research_views.daily_watch20_data import DailyWatch20Assets


def _partition_file(
    assets: DailyWatch20Assets,
    trade_date: str,
) -> tuple[Path | None, str | None]:
    if assets.minute_date_min and trade_date < assets.minute_date_min:
        return None, f"canonical minute coverage starts after {trade_date}"
    if assets.minute_date_max and trade_date > assets.minute_date_max:
        return None, f"canonical minute coverage ends before {trade_date}"
    part_dir = assets.minute_current / f"trade_date={trade_date}"
    files = sorted(part_dir.glob("*.parquet")) if part_dir.is_dir() else []
    if len(files) != 1:
        return (
            None,
            f"canonical minute partition does not contain exactly one parquet file: {part_dir}",
        )
    return files[0], None


def _coverage_payload(
    assets: DailyWatch20Assets,
) -> tuple[dict[str, Any] | None, str | None]:
    coverage = assets.minute_coverage
    if coverage is None or not coverage.is_file():
        return None, "canonical minute coverage receipt is unavailable"
    try:
        payload = json.loads(coverage.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"canonical minute coverage receipt is invalid: {exc}"
    if assets.minute_dataset == "legacy":
        valid = (
            isinstance(payload, dict)
            and payload.get("status") == "passed"
            and payload.get("quality_status") == "passed"
            and payload.get("coverage_status") == "full_sh_sz"
        )
        if not valid:
            return None, "canonical minute coverage receipt is not passed full_sh_sz"
    else:
        summary = payload.get("summary") if isinstance(payload, dict) else None
        valid = (
            isinstance(payload, dict)
            and payload.get("schema_version") == "a_share.minute_tushare_operational_version.v1"
            and payload.get("status") == "published_operational_version"
            and payload.get("provider") == "tushare"
            and isinstance(summary, dict)
            and summary.get("market_scope") == "SH_SZ_BJ"
        )
        if not valid:
            return None, "TuShare operational minute receipt is invalid"
    return payload, None


def _positive_int(value: object) -> bool:
    try:
        return int(str(value or "0")) > 0
    except ValueError:
        return False


def _daily_audit_reason(
    assets: DailyWatch20Assets,
    payload: dict[str, Any],
    trade_date: str,
    partition_file: Path,
    *,
    sha256_file: Callable[[Path], str],
) -> str | None:
    daily = payload.get("daily")
    if not isinstance(daily, list):
        return "canonical minute coverage receipt has no daily audit"
    date_field = "date" if assets.minute_dataset == "legacy" else "trade_date"
    entry = next(
        (item for item in daily if isinstance(item, dict) and item.get(date_field) == trade_date),
        None,
    )
    if assets.minute_dataset == "tushare":
        return _tushare_daily_audit_reason(
            entry,
            trade_date,
            partition_file,
            sha256_file=sha256_file,
        )
    return _legacy_daily_audit_reason(
        entry,
        trade_date,
        partition_file,
        sha256_file=sha256_file,
    )


def _tushare_daily_audit_reason(
    entry: object,
    trade_date: str,
    partition_file: Path,
    *,
    sha256_file: Callable[[Path], str],
) -> str | None:
    if not isinstance(entry, dict) or not _positive_int(entry.get("symbols")):
        return f"TuShare operational daily audit is missing or invalid for {trade_date}"
    expected_hash = str(entry.get("content_sha256") or "")
    if not expected_hash or sha256_file(partition_file) != expected_hash:
        return f"TuShare operational minute partition hash mismatch for {trade_date}"
    return None


def _legacy_daily_audit_reason(
    entry: object,
    trade_date: str,
    partition_file: Path,
    *,
    sha256_file: Callable[[Path], str],
) -> str | None:
    if not isinstance(entry, dict) or entry.get("valid") is not True:
        return f"canonical minute daily audit is missing or invalid for {trade_date}"
    if entry.get("market_scope") not in {"SH_SZ", "SH_SZ_BJ"}:
        return f"canonical minute daily audit is not full SH/SZ for {trade_date}"
    if not _positive_int(entry.get("sh_sz_symbols")):
        return f"canonical minute daily audit contains no SH/SZ symbols for {trade_date}"
    expected_hash = str(entry.get("content_sha256") or "")
    if not expected_hash or sha256_file(partition_file) != expected_hash:
        return f"canonical minute partition hash does not match its audit for {trade_date}"
    return None


def canonical_unavailability_reason(
    assets: DailyWatch20Assets,
    trade_date: str,
    *,
    sha256_file: Callable[[Path], str],
) -> str | None:
    """Return a fail-closed reason for either published minute dataset."""

    partition_file, partition_reason = _partition_file(assets, trade_date)
    if partition_reason is not None or partition_file is None:
        return partition_reason
    payload, coverage_reason = _coverage_payload(assets)
    if coverage_reason is not None or payload is None:
        return coverage_reason
    return _daily_audit_reason(
        assets,
        payload,
        trade_date,
        partition_file,
        sha256_file=sha256_file,
    )


__all__ = ["canonical_unavailability_reason"]
