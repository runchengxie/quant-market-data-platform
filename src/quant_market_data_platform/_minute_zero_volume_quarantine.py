"""Exact multi-stock reproduction for acquisition-only whole-date quarantine."""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq

from quant_market_data_platform._minute_semantic_registration import (
    _entry,
    _failed_record,
    _source_partition,
    _store_entry,
)
from quant_market_data_platform.minute_semantic_quarantine import SCHEMA_V2, ZERO_VOLUME
from quant_market_data_platform.providers._a_share_mins_validation import _validate_batch_frame
from quant_market_data_platform.providers.a_share_minute_price_flow import price_flow_diagnostics


def register(  # noqa: PLR0913 - explicit evidence registration interface
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
    if not reason.strip():
        raise ValueError("quarantine reason is required")
    if (
        not isinstance(symbols, list)
        or not symbols
        or any(not isinstance(s, str) or not s for s in symbols)
        or len(set(symbols)) != len(symbols)
    ):
        raise ValueError("quarantine requires distinct failure symbols")
    symbols = sorted(symbols)
    acceptance, requery = acceptance.resolve(strict=True), requery.resolve(strict=True)
    record = _failed_record(root, runtime, acceptance, trade_date)
    partition, sidecar, source = _source_partition(root, record, trade_date)
    flow = price_flow_diagnostics(source)
    if flow["notional_beyond_hard_price_guard_rows"]:
        raise ValueError("zero-volume quarantine cannot waive hard notional failures")
    failures = source[source.vol.eq(0) & source.amount.gt(0)]
    if failures.empty or set(failures.ts_code) != set(symbols):
        raise ValueError("quarantine symbols do not cover all zero-volume failures")
    issues = record["issues"]
    if (
        not isinstance(issues, dict)
        or any(type(v) is not int or v < 0 for v in issues.values())
        or {k: v for k, v in issues.items() if v} != {ZERO_VOLUME: len(failures)}
    ):
        raise ValueError("quarantine cannot waive other audit issues")
    fresh, valid, problems = _validate_batch_frame(
        pq.ParquetFile(requery).read().to_pandas(),
        requested_symbols=symbols,
        trade_date=trade_date,
    )
    original = source[source.ts_code.isin(symbols)]
    order = ["ts_code", "trade_time"]
    if (
        problems
        or valid != set(symbols)
        or not original.sort_values(order)
        .reset_index(drop=True)
        .equals(fresh.sort_values(order).reset_index(drop=True))
    ):
        raise ValueError("requery must reproduce exactly every complete failing stock-day")
    entry = _entry(root, record, partition, sidecar, acceptance, requery, trade_date, reason)
    entry.update(anomaly_kind=ZERO_VOLUME, scope="trade_date", symbols=symbols)
    return _store_entry(root, entry, schema=SCHEMA_V2, dry_run=dry_run)
