"""Frame normalization helpers for TuShare API responses."""

from __future__ import annotations

import importlib
from typing import Any

from market_data_platform.standardize.normalize import normalize_ts_code


def _pandas() -> Any:
    return importlib.import_module("pandas")


_normalize_ts_code = normalize_ts_code


def _normalize_date_column(frame: Any, column: str) -> None:
    if column not in frame.columns:
        return
    values = frame[column].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    frame[column] = values.str.replace("-", "", regex=False).str[:8]


def _prepare_frame(frame: Any) -> Any:
    pd = _pandas()
    df = pd.DataFrame(frame).copy()
    if df.empty:
        return df
    if "ts_code" in df.columns:
        df["ts_code"] = df["ts_code"].map(_normalize_ts_code)
        df["symbol"] = df["ts_code"]
    for column in ("trade_date", "cal_date", "pretrade_date", "list_date", "delist_date"):
        _normalize_date_column(df, column)
    df["platform_market"] = "a_share"
    return df


def _prepare_fund_portfolio_frame(frame: Any) -> Any:
    pd = _pandas()
    df = pd.DataFrame(frame).copy()
    if df.empty:
        return df
    if "ts_code" in df.columns:
        df["ts_code"] = df["ts_code"].astype(str).str.strip().str.upper()
    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].map(_normalize_ts_code)
    for column in ("ann_date", "end_date"):
        _normalize_date_column(df, column)
    df["platform_market"] = "a_share"
    return df


def _prepare_top10_holder_frame(frame: Any) -> Any:
    df = _prepare_frame(frame)
    if df.empty:
        return df
    for column in ("ann_date", "end_date"):
        _normalize_date_column(df, column)
    return df


def _prepare_stk_holdertrade_frame(frame: Any) -> Any:
    df = _prepare_frame(frame)
    if df.empty:
        return df
    for column in ("ann_date", "begin_date", "close_date"):
        _normalize_date_column(df, column)
    return df


def _prepare_event_frame(frame: Any) -> Any:
    df = _prepare_frame(frame)
    if df.empty:
        return df
    for column in (
        "report_date",
        "surv_date",
        "ann_date",
        "end_date",
        "start_date",
        "begin_date",
        "close_date",
    ):
        _normalize_date_column(df, column)
    if "lead_stock_code" in df.columns:
        df["lead_stock_code"] = df["lead_stock_code"].map(_normalize_ts_code)
    return df
