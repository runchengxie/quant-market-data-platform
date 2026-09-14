"""Configuration constants and option objects for TuShare A-share mirrors."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_options_part01 import (
    DEFAULT_ADJ_FACTOR_FIELDS,
    DEFAULT_DAILY_BASIC_FIELDS,
    DEFAULT_DAILY_FIELDS,
    DEFAULT_DC_CONCEPT_CONS_FIELDS,
    DEFAULT_DC_CONCEPT_FIELDS,
    DEFAULT_DISABLE_PROXY,
    DEFAULT_FUND_ADJ_FIELDS,
    DEFAULT_FUND_DAILY_FIELDS,
    DEFAULT_HSGT_TOP10_FIELDS,
    DEFAULT_KPL_CONCEPT_CONS_FIELDS,
    DEFAULT_KPL_LIST_FIELDS,
    DEFAULT_LIMIT_CPT_LIST_FIELDS,
    DEFAULT_LIMIT_LIST_THS_FIELDS,
    DEFAULT_LIMIT_STATUS_FIELDS,
    DEFAULT_LIMIT_STEP_FIELDS,
    DEFAULT_MARGIN_DETAIL_FIELDS,
    DEFAULT_MARGIN_FIELDS,
    DEFAULT_MONEYFLOW_DC_FIELDS,
    DEFAULT_MONEYFLOW_FIELDS,
    DEFAULT_MONEYFLOW_HSGT_FIELDS,
    DEFAULT_MONEYFLOW_THS_FIELDS,
    DEFAULT_QUOTA_COOLDOWN_SECONDS,
    DEFAULT_REQUEST_ATTEMPTS,
    DEFAULT_RETRY_MAX_SLEEP_SECONDS,
    DEFAULT_RETRY_SLEEP_SECONDS,
    DEFAULT_STK_AUCTION_FIELDS,
    DEFAULT_THS_HOT_FIELDS,
    DEFAULT_TOP_INST_FIELDS,
)

DEFAULT_TRADE_DATE_FIELDS = {
    "daily": DEFAULT_DAILY_FIELDS,
    "adj_factor": DEFAULT_ADJ_FACTOR_FIELDS,
    "fund_daily": DEFAULT_FUND_DAILY_FIELDS,
    "fund_adj": DEFAULT_FUND_ADJ_FIELDS,
    "daily_basic": DEFAULT_DAILY_BASIC_FIELDS,
    "limit_status": DEFAULT_LIMIT_STATUS_FIELDS,
    "moneyflow": DEFAULT_MONEYFLOW_FIELDS,
    "moneyflow_dc": DEFAULT_MONEYFLOW_DC_FIELDS,
    "moneyflow_hsgt": DEFAULT_MONEYFLOW_HSGT_FIELDS,
    "top_inst": DEFAULT_TOP_INST_FIELDS,
    "ths_hot": DEFAULT_THS_HOT_FIELDS,
    "dc_concept": DEFAULT_DC_CONCEPT_FIELDS,
    "dc_concept_cons": DEFAULT_DC_CONCEPT_CONS_FIELDS,
    "kpl_list": DEFAULT_KPL_LIST_FIELDS,
    "kpl_concept_cons": DEFAULT_KPL_CONCEPT_CONS_FIELDS,
    "limit_step": DEFAULT_LIMIT_STEP_FIELDS,
    "limit_cpt_list": DEFAULT_LIMIT_CPT_LIST_FIELDS,
    "stk_auction_o": DEFAULT_STK_AUCTION_FIELDS,
    "stk_auction_c": DEFAULT_STK_AUCTION_FIELDS,
    "moneyflow_ths": DEFAULT_MONEYFLOW_THS_FIELDS,
    "limit_list_ths": DEFAULT_LIMIT_LIST_THS_FIELDS,
    "margin_detail": DEFAULT_MARGIN_DETAIL_FIELDS,
    "margin": DEFAULT_MARGIN_FIELDS,
    "hsgt_top10": DEFAULT_HSGT_TOP10_FIELDS,
}

TRADE_DATE_REQUIRED_FIELDS = {
    "moneyflow_hsgt": ("trade_date",),
    "dc_concept": ("theme_code", "trade_date"),
    "dc_concept_cons": ("ts_code", "theme_code", "trade_date"),
    "kpl_concept_cons": ("ts_code", "con_code", "trade_date"),
}

TRADE_DATE_DEFAULT_REQUEST_OPTIONS = {
    "ths_hot": {"market": "热股", "is_new": "Y"},
}


@dataclass(frozen=True)
class TushareRequestPolicy:
    attempts: int = DEFAULT_REQUEST_ATTEMPTS
    retry_sleep_seconds: float = DEFAULT_RETRY_SLEEP_SECONDS
    retry_max_sleep_seconds: float = DEFAULT_RETRY_MAX_SLEEP_SECONDS
    quota_cooldown_seconds: float = DEFAULT_QUOTA_COOLDOWN_SECONDS
    disable_proxy: bool = DEFAULT_DISABLE_PROXY
    request_timeout_seconds: float | None = None


@dataclass(frozen=True)
class TradeDateMirrorOptions:
    dataset: str
    out_dir: str | Path
    start_date: str
    end_date: str
    market: str = "a_share"
    fields: Iterable[str] | None = None
    skip_existing: bool = False
    request_interval_seconds: float = 0.0
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    request_policy: TushareRequestPolicy | None = None
    query_options: dict[str, Any] = dataclass_field(default_factory=dict)
    request_options: dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class FundPortfolioMirrorOptions:
    out_dir: str | Path
    start_date: str
    end_date: str
    fields: Iterable[str] | None = None
    skip_existing: bool = False
    page_size: int = 8000
    max_pages_per_period: int = 1000
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    request_policy: TushareRequestPolicy | None = None
    request_options: dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class Top10HolderMirrorOptions:
    dataset: str
    api_name: str
    out_dir: str | Path
    symbols: Iterable[str]
    start_date: str
    end_date: str
    fields: Iterable[str] | None = None
    skip_existing: bool = False
    request_interval_seconds: float = 0.0
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    request_policy: TushareRequestPolicy | None = None
    request_options: dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class StkHoldertradeMirrorOptions:
    out_dir: str | Path
    symbols: Iterable[str]
    start_date: str
    end_date: str
    fields: Iterable[str] | None = None
    skip_existing: bool = False
    request_interval_seconds: float = 0.0
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    request_policy: TushareRequestPolicy | None = None
    request_options: dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class DateRangeEventMirrorOptions:
    dataset: str
    api_name: str
    out_dir: str | Path
    start_date: str
    end_date: str
    fields: Iterable[str] | None = None
    skip_existing: bool = False
    request_interval_seconds: float = 0.0
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    request_policy: TushareRequestPolicy | None = None
    query_options: dict[str, Any] = dataclass_field(default_factory=dict)
    request_options: dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class MonthMirrorOptions:
    dataset: str
    api_name: str
    out_dir: str | Path
    start_date: str
    end_date: str
    fields: Iterable[str] | None = None
    skip_existing: bool = False
    request_interval_seconds: float = 0.0
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    request_policy: TushareRequestPolicy | None = None
    query_options: dict[str, Any] = dataclass_field(default_factory=dict)
    request_options: dict[str, Any] = dataclass_field(default_factory=dict)


DEFAULT_INDEX_DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "close",
    "open",
    "high",
    "low",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)


@dataclass(frozen=True)
class IndexDailyMirrorOptions:
    index_codes: tuple[str, ...]
    out_dir: str | Path
    start_date: str
    end_date: str
    fields: Iterable[str] | None = None
    request_interval_seconds: float = 0.0
    skip_existing: bool = False
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    request_policy: TushareRequestPolicy | None = None
    query_options: dict[str, Any] = dataclass_field(default_factory=dict)
    request_options: dict[str, Any] = dataclass_field(default_factory=dict)
