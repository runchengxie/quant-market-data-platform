"""Source-neutral normalization and fusion for A-share minute bars.

The canonical on-disk contract is deliberately small: eight columns, one row
per ``(ts_code, trade_time)``, and no Hive partition columns embedded in the
Parquet file.  Source attribution is returned as build statistics rather than
stored as a ninth data column.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

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
        """Return JSON-serializable normalization statistics."""
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
        """Return statistics grouped by source for manifests and logs."""
        return {
            "input_rows": {
                "guan": self.guan_input_rows,
                "tushare": self.tushare_input_rows,
            },
            "unique_rows": {
                "guan": self.guan_unique_rows,
                "tushare": self.tushare_unique_rows,
            },
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
        """Return JSON-serializable build statistics."""
        return asdict(self)


@dataclass(frozen=True)
class GuanDealAggregationResult:
    """Canonical Guan bars and deal-source aggregation statistics."""

    frame: pd.DataFrame
    stats: GuanDealAggregationStats


def _empty_canonical_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": pd.Series(dtype="string"),
            "trade_time": pd.Series(dtype="datetime64[ns]"),
            "open": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "vol": pd.Series(dtype="float64"),
            "amount": pd.Series(dtype="float64"),
        }
    )


def _project_source(source: pd.DataFrame | str | Path, *, label: str) -> pd.DataFrame:
    if isinstance(source, pd.DataFrame):
        missing = set(CANONICAL_MINUTE_COLUMNS).difference(source.columns)
        if missing:
            raise ValueError(f"{label} is missing canonical columns: {sorted(missing)}")
        return source.loc[:, list(CANONICAL_MINUTE_COLUMNS)].copy()

    path = Path(source)
    schema_names = set(pq.ParquetFile(path).schema_arrow.names)
    missing = set(CANONICAL_MINUTE_COLUMNS).difference(schema_names)
    if missing:
        raise ValueError(f"{label} partition {path} is missing columns: {sorted(missing)}")
    return pd.read_parquet(path, columns=list(CANONICAL_MINUTE_COLUMNS))


def _canonicalize_frame(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    if frame.empty:
        return _empty_canonical_frame()

    work = frame.loc[:, list(CANONICAL_MINUTE_COLUMNS)].copy()
    work["ts_code"] = work["ts_code"].astype("string").str.strip().str.upper()
    trade_time = pd.to_datetime(work["trade_time"], errors="coerce")
    if isinstance(trade_time.dtype, pd.DatetimeTZDtype):
        trade_time = trade_time.dt.tz_localize(None)
    work["trade_time"] = trade_time.astype("datetime64[ns]")

    numeric_columns = CANONICAL_MINUTE_COLUMNS[2:]
    for column in numeric_columns:
        work[column] = pd.to_numeric(work[column], errors="coerce").astype("float64")

    invalid_counts = {
        column: int(work[column].isna().sum())
        for column in CANONICAL_MINUTE_COLUMNS
        if work[column].isna().any()
    }
    empty_codes = int(work["ts_code"].str.len().eq(0).sum())
    if empty_codes:
        invalid_counts["ts_code"] = invalid_counts.get("ts_code", 0) + empty_codes
    if invalid_counts:
        raise ValueError(f"{label} contains null or invalid canonical values: {invalid_counts}")

    return work.loc[:, list(CANONICAL_MINUTE_COLUMNS)].reset_index(drop=True)


def normalize_legacy_guan_partition(
    source: pd.DataFrame | str | Path,
    *,
    unit_profile: LegacyGuanUnitProfile,
) -> pd.DataFrame:
    """Normalize one legacy Guan minute partition with an audited unit profile.

    No default profile is provided because historical legacy partitions use
    different unit regimes.  For known 100x partitions, pass
    :data:`LEGACY_GUAN_HUNDRED_X_UNITS`, which divides both ``vol`` and
    ``amount`` by 100.
    """
    return normalize_legacy_guan_partition_with_stats(
        source,
        unit_profile=unit_profile,
    ).frame


def normalize_legacy_guan_partition_with_stats(
    source: pd.DataFrame | str | Path,
    *,
    unit_profile: LegacyGuanUnitProfile,
) -> LegacyGuanNormalizationResult:
    """Normalize legacy Guan bars and account for source-specific cleanup."""
    frame = _project_source(source, label="legacy Guan")
    input_rows = len(frame)
    numeric_columns = list(CANONICAL_MINUTE_COLUMNS[2:])
    empty_placeholders = frame.loc[:, numeric_columns].isna().all(axis=1)
    if empty_placeholders.any():
        frame = frame.loc[~empty_placeholders].copy()
    frame["vol"] = pd.to_numeric(frame["vol"], errors="coerce") * unit_profile.volume_scale
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce") * unit_profile.amount_scale
    canonical = _canonicalize_frame(frame, label=f"legacy Guan ({unit_profile.name})")

    repaired_high = canonical[["open", "close", "high"]].max(axis=1)
    repaired_low = canonical[["open", "close", "low"]].min(axis=1)
    repaired = canonical["high"].ne(repaired_high) | canonical["low"].ne(repaired_low)
    canonical["high"] = repaired_high
    canonical["low"] = repaired_low
    zero_flow = canonical["vol"].eq(0) & canonical["amount"].eq(0)
    stats = LegacyGuanNormalizationStats(
        input_rows=input_rows,
        dropped_empty_rows=int(empty_placeholders.sum()),
        repaired_ohlc_rows=int(repaired.sum()),
        zero_flow_rows=int(zero_flow.sum()),
        output_rows=len(canonical),
    )
    return LegacyGuanNormalizationResult(frame=canonical, stats=stats)


def normalize_tushare_partition(source: pd.DataFrame | str | Path) -> pd.DataFrame:
    """Project a TuShare minute partition to the canonical eight columns."""
    frame = _project_source(source, label="TuShare")
    return _canonicalize_frame(frame, label="TuShare")


def _deduplicate_source(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    canonical = _canonicalize_frame(frame, label=label)
    return canonical.drop_duplicates(subset=list(MINUTE_KEY_COLUMNS), keep="last")


def fuse_minute_frames(
    guan_frame: pd.DataFrame,
    tushare_frame: pd.DataFrame,
) -> MinuteFusionResult:
    """Fuse canonical source frames, selecting TuShare on overlapping keys."""
    guan_input_rows = len(guan_frame)
    tushare_input_rows = len(tushare_frame)
    guan = _deduplicate_source(guan_frame, label="Guan fusion input")
    tushare = _deduplicate_source(tushare_frame, label="TuShare fusion input")

    guan_index = pd.MultiIndex.from_frame(guan.loc[:, list(MINUTE_KEY_COLUMNS)])
    tushare_index = pd.MultiIndex.from_frame(tushare.loc[:, list(MINUTE_KEY_COLUMNS)])
    overlap = guan_index.isin(tushare_index)
    guan_selected = guan.loc[~overlap]
    fused = pd.concat([guan_selected, tushare], ignore_index=True)
    fused = _canonicalize_frame(fused, label="fused minute output")
    fused = fused.sort_values(list(MINUTE_KEY_COLUMNS), kind="stable").reset_index(drop=True)

    if fused.duplicated(subset=list(MINUTE_KEY_COLUMNS)).any():
        raise RuntimeError("Minute fusion produced duplicate canonical keys")

    stats = MinuteFusionStats(
        guan_input_rows=guan_input_rows,
        guan_unique_rows=len(guan),
        tushare_input_rows=tushare_input_rows,
        tushare_unique_rows=len(tushare),
        overlap_rows=int(overlap.sum()),
        guan_output_rows=len(guan_selected),
        tushare_output_rows=len(tushare),
        output_rows=len(fused),
    )
    return MinuteFusionResult(frame=fused, stats=stats)


def _canonical_arrow_table(frame: pd.DataFrame, *, label: str) -> pa.Table:
    canonical = _canonicalize_frame(frame, label=label)
    table = pa.Table.from_pandas(
        canonical,
        schema=CANONICAL_MINUTE_SCHEMA,
        preserve_index=False,
        safe=True,
    )
    return table.replace_schema_metadata(None)


def write_canonical_minute_partition(
    frame: pd.DataFrame,
    output_path: str | Path,
) -> Path:
    """Atomically replace one canonical Parquet partition."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = _canonical_arrow_table(frame, label="minute partition write")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        pq.write_table(table, temporary_path, compression="zstd")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return path


def fuse_and_write_minute_partition(
    guan_frame: pd.DataFrame,
    tushare_frame: pd.DataFrame,
    output_path: str | Path,
) -> MinuteFusionStats:
    """Fuse two canonical source frames, atomically write, and return source stats."""
    result = fuse_minute_frames(guan_frame, tushare_frame)
    write_canonical_minute_partition(result.frame, output_path)
    return result.stats


def _trade_date_from_deal_path(path: Path, trade_date: str | None) -> str:
    value = trade_date
    if value is None:
        matched = _DEAL_FILE_PATTERN.search(path.name)
        if matched is None:
            raise ValueError(
                f"Cannot derive trade date from {path.name!r}; pass trade_date='YYYYMMDD'"
            )
        value = matched.group(1)
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError(f"Invalid trade date {value!r}; expected YYYYMMDD")
    pd.to_datetime(value, format="%Y%m%d", errors="raise")
    return value


def _normalize_symbol_mapping(symbol_to_ts_code: Mapping[str, str] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if symbol_to_ts_code is None:
        return normalized
    for raw_symbol, raw_ts_code in symbol_to_ts_code.items():
        symbol = str(raw_symbol).strip().split(".", maxsplit=1)[0].zfill(6)
        ts_code = str(raw_ts_code).strip().upper()
        if not re.fullmatch(r"\d{6}", symbol):
            raise ValueError(f"Invalid symbol mapping key: {raw_symbol!r}")
        if _TS_CODE_PATTERN.fullmatch(ts_code) is None:
            raise ValueError(f"Invalid ts_code mapping value: {raw_ts_code!r}")
        previous = normalized.get(symbol)
        if previous is not None and previous != ts_code:
            raise ValueError(
                f"Conflicting ts_code mappings for symbol {symbol}: {previous}, {ts_code}"
            )
        normalized[symbol] = ts_code
    return normalized


def _fallback_ts_code(symbol: str) -> str | None:
    if symbol.startswith(("600", "601", "603", "605", "688", "689")):
        exchange = "SH"
    elif symbol.startswith(("43", "83", "87", "88", "92")):
        exchange = "BJ"
    elif symbol.startswith(("000", "001", "002", "003", "300", "301", "302")):
        exchange = "SZ"
    else:
        return None
    return f"{symbol}.{exchange}"


def _resolve_deal_symbols(
    raw_codes: pd.Series,
    mapping: Mapping[str, str],
) -> tuple[pd.Series, pd.Series]:
    numeric = pd.to_numeric(raw_codes, errors="coerce")
    integral = numeric.notna() & numeric.ge(0) & numeric.le(999_999) & numeric.mod(1).eq(0)
    symbols = pd.Series(pd.NA, index=raw_codes.index, dtype="string")
    symbols.loc[integral] = numeric.loc[integral].astype("int64").astype("string").str.zfill(6)

    ts_codes = symbols.map(mapping).astype("string")
    resolution = pd.Series("unresolved", index=raw_codes.index, dtype="string")
    mapped = ts_codes.notna()
    resolution.loc[mapped] = "mapping"
    fallback_candidates = symbols.notna() & ~mapped
    fallback_values = symbols.loc[fallback_candidates].map(_fallback_ts_code).astype("string")
    fallback = fallback_values.notna()
    fallback_index = fallback_values.index[fallback]
    ts_codes.loc[fallback_index] = fallback_values.loc[fallback_index]
    resolution.loc[fallback_index] = "fallback"
    return ts_codes, resolution


def _deal_time_components(raw_times: pd.Series) -> tuple[pd.Series, pd.Series]:
    numeric = pd.to_numeric(raw_times, errors="coerce")
    integral = numeric.notna() & numeric.ge(0) & numeric.mod(1).eq(0)
    values = numeric.fillna(-1).astype("int64")
    milliseconds = values.mod(1_000)
    seconds = values.floordiv(1_000).mod(100)
    minutes = values.floordiv(100_000).mod(100)
    hours = values.floordiv(10_000_000)
    valid_clock = (
        integral
        & hours.between(0, 23)
        & minutes.between(0, 59)
        & seconds.between(0, 59)
        & milliseconds.between(0, 999)
    )
    millis_since_midnight = ((hours * 60 + minutes) * 60 + seconds) * 1_000 + milliseconds
    return millis_since_midnight, valid_clock
