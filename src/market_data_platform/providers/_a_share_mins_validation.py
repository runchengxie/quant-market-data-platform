"""Validation and normalization helpers for the A-share minute mirror.

These are pure, framework-agnostic checks over minute-bar frames and symbol
strings.  They were split out of ``_a_share_mins_partition`` so the per-partition
I/O and persistence logic can stay independently inspectable and testable.  All
names are re-exported from ``_a_share_mins_partition`` to keep the public import
contract unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from market_data_platform.providers._a_share_mins_constants import (
    DEFAULT_MINS_FIELDS,
    EXPECTED_MINUTES_OF_DAY,
    MINUTE_BARS_PER_DAY,
)

__all__ = [
    "_is_cn_stock_ts_code",
    "_normalize_stock_dates",
    "_normalize_minute_frame",
    "_has_expected_minute_grid",
    "_has_valid_ohlcv",
    "_validate_batch_frame",
    "_complete_symbols",
]


def _is_cn_stock_ts_code(value: str) -> bool:
    if "." not in value:
        return False
    code, exchange = value.rsplit(".", 1)
    return len(code) == 6 and code.isdigit() and exchange in {"SH", "SZ", "BJ"}


def _normalize_stock_dates(values: pd.Series) -> pd.Series:
    normalized = values.astype("string").fillna("").str.strip()
    normalized = normalized.str.replace("-", "", regex=False).str.replace(r"\.0$", "", regex=True)
    return normalized.where(normalized.str.fullmatch(r"\d{8}"), "")


def _normalize_minute_frame(frame: pd.DataFrame, *, context: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=pd.Index(DEFAULT_MINS_FIELDS))

    missing = [field for field in DEFAULT_MINS_FIELDS if field not in frame.columns]
    if missing:
        raise ValueError(f"{context} missing fields: {missing}")

    normalized = frame.loc[:, DEFAULT_MINS_FIELDS].copy()
    normalized["ts_code"] = normalized["ts_code"].astype(str).str.strip().str.upper()
    normalized["trade_time"] = pd.to_datetime(normalized["trade_time"], errors="raise")
    for column in DEFAULT_MINS_FIELDS[2:]:
        normalized[column] = pd.to_numeric(normalized[column], errors="raise").astype(float)
    return normalized


def _has_expected_minute_grid(trade_times: pd.Series) -> bool:
    if not trade_times.eq(trade_times.dt.floor("min")).all():
        return False
    minutes = trade_times.dt.hour * 60 + trade_times.dt.minute
    return set(minutes.astype(int)) == EXPECTED_MINUTES_OF_DAY


def _has_valid_ohlcv(group: pd.DataFrame) -> bool:
    numeric = group.loc[:, DEFAULT_MINS_FIELDS[2:]].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        return False
    prices = group[["open", "close", "high", "low"]]
    if not prices.gt(0).all().all():
        return False
    if not (
        group["high"].ge(prices[["open", "close", "low"]].max(axis=1)).all()
        and group["low"].le(prices[["open", "close", "high"]].min(axis=1)).all()
    ):
        return False
    return bool(group["vol"].ge(0).all() and group["amount"].ge(0).all())


def _validate_batch_frame(
    frame: pd.DataFrame, *, requested_symbols: list[str], trade_date: str
) -> tuple[pd.DataFrame, set[str], dict[str, str]]:
    normalized = _normalize_minute_frame(frame, context=f"TuShare minute response for {trade_date}")
    requested = set(requested_symbols)
    unexpected = sorted(set(normalized["ts_code"]) - requested)
    if unexpected:
        raise ValueError(
            f"TuShare minute response for {trade_date} returned unexpected symbols: {unexpected}"
        )
    valid: set[str] = set()
    issues: dict[str, str] = {}
    for symbol in requested_symbols:
        group = normalized[normalized["ts_code"] == symbol]
        if group.empty:
            issues[symbol] = "missing from response"
            continue
        dates = group["trade_time"].dt.strftime("%Y%m%d")
        if not dates.eq(trade_date).all():
            issues[symbol] = "contains bars outside requested trade_date"
            continue
        if group["trade_time"].duplicated().any():
            issues[symbol] = "contains duplicate trade_time keys"
            continue
        if len(group) != MINUTE_BARS_PER_DAY:
            issues[symbol] = f"expected {MINUTE_BARS_PER_DAY} bars, received {len(group)}"
            continue
        if not _has_expected_minute_grid(group["trade_time"]):
            issues[symbol] = "does not match the expected A-share 1min session grid"
            continue
        if not _has_valid_ohlcv(group):
            issues[symbol] = "contains invalid OHLCV values"
            continue
        valid.add(symbol)
    return normalized, valid, issues


def _complete_symbols(frame: pd.DataFrame, *, trade_date: str) -> set[str]:
    if frame.empty:
        return set()
    complete: set[str] = set()
    for symbol, group in frame.groupby("ts_code", sort=False):
        dates = group["trade_time"].dt.strftime("%Y%m%d")
        if (
            len(group) == MINUTE_BARS_PER_DAY
            and dates.eq(trade_date).all()
            and not group["trade_time"].duplicated().any()
            and _has_expected_minute_grid(group["trade_time"])
            and _has_valid_ohlcv(group)
        ):
            complete.add(str(symbol))
    return complete
