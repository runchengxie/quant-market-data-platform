"""Canonical schema and result contracts for A-share one-minute bars."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd
import pyarrow as pa

CANONICAL_MINUTE_COLUMNS = (
    "ts_code",
    "trade_time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
)

CANONICAL_MINUTE_SCHEMA = pa.schema(
    [
        pa.field("ts_code", pa.string()),
        pa.field("trade_time", pa.timestamp("ns")),
        pa.field("open", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("vol", pa.float64()),
        pa.field("amount", pa.float64()),
    ]
)

MINUTE_KEY_COLUMNS = ("ts_code", "trade_time")

_DEAL_FILE_PATTERN = re.compile(r"deal_(\d{8})\.parquet$", re.IGNORECASE)
_TS_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_DEAL_REQUIRED_COLUMNS = ("BizIndex", "DealTime", "Price", "SecuCode", "TradingDay", "Volume")
_OPENING_AUCTION_START_MS = (9 * 60 + 25) * 60_000
_MORNING_START_MS = (9 * 60 + 30) * 60_000
_MORNING_END_MS = (11 * 60 + 30) * 60_000 + 999
_AFTERNOON_START_MS = 13 * 60 * 60_000
_CONTINUOUS_CLOSE_MS = 15 * 60 * 60_000
_CLOSING_AUCTION_END_MS = _CONTINUOUS_CLOSE_MS + 59_999

DEFAULT_BATCH_ROW_GROUPS = 4
DEFAULT_POLARS_BATCH_ROW_GROUPS = 16
DEFAULT_COMPACTION_ROW_GROUPS = 4
DEFAULT_COMPACTION_ROWS = 1_000_000
DEFAULT_MAX_AGGREGATED_ROWS = 3_000_000

MinuteAggregationEngine = Literal["auto", "pandas", "polars"]


@dataclass(frozen=True)
class LegacyGuanUnitProfile:
    """Explicit multipliers for a previously materialized Guan minute partition."""

    name: str
    volume_scale: float
    amount_scale: float

    def __post_init__(self) -> None:
        for field_name, value in (
            ("volume_scale", self.volume_scale),
            ("amount_scale", self.amount_scale),
        ):
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"{field_name} must be a finite positive number, got {value!r}")


LEGACY_GUAN_CANONICAL_UNITS = LegacyGuanUnitProfile(
    name="already_canonical",
    volume_scale=1.0,
    amount_scale=1.0,
)
LEGACY_GUAN_HUNDRED_X_UNITS = LegacyGuanUnitProfile(
    name="hundred_x_to_canonical",
    volume_scale=0.01,
    amount_scale=0.01,
)


@dataclass(frozen=True)
class LegacyGuanNormalizationStats:
    """Row-level cleanup accounting for one legacy Guan partition."""

    input_rows: int
    dropped_empty_rows: int
    repaired_ohlc_rows: int
    zero_flow_rows: int
    output_rows: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class LegacyGuanNormalizationResult:
    """A normalized legacy partition and its cleanup statistics."""

    frame: pd.DataFrame
    stats: LegacyGuanNormalizationStats


@dataclass(frozen=True)
class MinuteFusionStats:
    """Source-level row accounting for one fused minute partition."""

    guan_input_rows: int
    guan_unique_rows: int
    tushare_input_rows: int
    tushare_unique_rows: int
    overlap_rows: int
    guan_output_rows: int
    tushare_output_rows: int
    output_rows: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_rows": {"guan": self.guan_input_rows, "tushare": self.tushare_input_rows},
            "unique_rows": {"guan": self.guan_unique_rows, "tushare": self.tushare_unique_rows},
            "output_rows_by_source": {
                "guan": self.guan_output_rows,
                "tushare": self.tushare_output_rows,
            },
            "overlap_rows": self.overlap_rows,
            "output_rows": self.output_rows,
        }


@dataclass(frozen=True)
class MinuteFusionResult:
    """A canonical fused frame and its source accounting."""

    frame: pd.DataFrame
    stats: MinuteFusionStats


@dataclass(frozen=True)
class GuanDealAggregationStats:
    """Streaming aggregation statistics for one Guan deal file."""

    source_path: str
    trade_date: str
    engine: str
    row_groups: int
    row_group_batches: int
    input_rows: int
    session_rows: int
    eligible_rows: int
    mapping_rows: int
    fallback_rows: int
    unresolved_rows: int
    invalid_value_rows: int
    output_rows: int
    output_symbols: int
    output_traded_symbols: int
    output_vol_sum: float
    output_amount_sum: float
    unit_profile: str
    partial_compactions: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GuanDealAggregationResult:
    """Canonical Guan bars and deal-source aggregation statistics."""

    frame: pd.DataFrame
    stats: GuanDealAggregationStats
