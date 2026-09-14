"""Source-neutral normalization and fusion for A-share minute bars.

The canonical on-disk contract is deliberately small: eight columns, one row
per ``(ts_code, trade_time)``, and no Hive partition columns embedded in the
Parquet file.  Source attribution is returned as build statistics rather than
stored as a ninth data column.
"""

from __future__ import annotations

import gc
import importlib
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .guan_deals import (
    _accumulate_deal_counts,
    _aggregate_guan_deal_file_pandas,
    _deal_aggregation_result,
    _deal_source_identity,
    _DealAggregationContext,
    _DealAggregationRequest,
    _DealAggregationState,
    _new_deal_aggregation_state,
    _polars_symbol_mapping,
    _PolarsDealBatchContext,
    _raise_if_aggregation_too_large,
    _validate_deal_aggregation_context,
)
from .normalize import (
    _AFTERNOON_START_MS,
    _CLOSING_AUCTION_END_MS,
    _CONTINUOUS_CLOSE_MS,
    _DEAL_REQUIRED_COLUMNS,
    _MORNING_END_MS,
    _MORNING_START_MS,
    _OPENING_AUCTION_START_MS,
    CANONICAL_MINUTE_COLUMNS,
    CANONICAL_MINUTE_SCHEMA,
    DEFAULT_BATCH_ROW_GROUPS,
    DEFAULT_COMPACTION_ROW_GROUPS,
    DEFAULT_COMPACTION_ROWS,
    DEFAULT_MAX_AGGREGATED_ROWS,
    DEFAULT_POLARS_BATCH_ROW_GROUPS,
    LEGACY_GUAN_CANONICAL_UNITS,
    LEGACY_GUAN_HUNDRED_X_UNITS,
    MINUTE_KEY_COLUMNS,
    GuanDealAggregationResult,
    GuanDealAggregationStats,
    LegacyGuanNormalizationResult,
    LegacyGuanNormalizationStats,
    LegacyGuanUnitProfile,
    MinuteAggregationEngine,
    MinuteFusionResult,
    MinuteFusionStats,
    _canonicalize_frame,
    _empty_canonical_frame,
    fuse_and_write_minute_partition,
    fuse_minute_frames,
    normalize_legacy_guan_partition,
    normalize_legacy_guan_partition_with_stats,
    normalize_tushare_partition,
    write_canonical_minute_partition,
)

__all__ = [
    "CANONICAL_MINUTE_COLUMNS",
    "CANONICAL_MINUTE_SCHEMA",
    "DEFAULT_BATCH_ROW_GROUPS",
    "DEFAULT_COMPACTION_ROW_GROUPS",
    "DEFAULT_COMPACTION_ROWS",
    "DEFAULT_MAX_AGGREGATED_ROWS",
    "DEFAULT_POLARS_BATCH_ROW_GROUPS",
    "GuanDealAggregationResult",
    "GuanDealAggregationStats",
    "LEGACY_GUAN_CANONICAL_UNITS",
    "LEGACY_GUAN_HUNDRED_X_UNITS",
    "LegacyGuanNormalizationResult",
    "LegacyGuanNormalizationStats",
    "LegacyGuanUnitProfile",
    "MINUTE_KEY_COLUMNS",
    "MinuteAggregationEngine",
    "MinuteFusionResult",
    "MinuteFusionStats",
    "aggregate_guan_deal_file",
    "fuse_and_write_minute_partition",
    "fuse_minute_frames",
    "normalize_legacy_guan_partition",
    "normalize_legacy_guan_partition_with_stats",
    "normalize_tushare_partition",
    "write_canonical_minute_partition",
]


def _try_import_polars() -> ModuleType | None:
    try:
        return importlib.import_module("polars")
    except ModuleNotFoundError as exc:
        if exc.name == "polars":
            return None
        raise


def _aggregate_polars_deal_batch(
    context: _PolarsDealBatchContext,
    table: pa.Table,
    stream_offset: int,
) -> tuple[Any, dict[str, int]]:
    """Aggregate one Arrow batch of guan deal ticks into minute OHLCV rows.

    The function is a single linear polars pipeline split into clearly labeled
    stages below (cast -> validate -> clock -> session -> symbol map -> aggregate).
    Intermediate columns are intentionally kept on the frame and reused across
    stages, so the stages are not extracted into separate helpers.
    """
    pl = context.pl
    trade_date = context.trade_date
    symbol_mapping = context.symbol_mapping
    path = context.path
    # Stage 1: lift raw string/int columns into typed helper columns.
    frame = pl.from_arrow(table).with_row_index("_stream_order", offset=stream_offset)
    frame = frame.with_columns(
        pl.col("TradingDay").cast(pl.Float64, strict=False).alias("_trading_day"),
        pl.col("DealTime").cast(pl.Float64, strict=False).alias("_deal_time_numeric"),
        pl.col("Price").cast(pl.Float64, strict=False).alias("_price_cents"),
        pl.col("Volume").cast(pl.Float64, strict=False).alias("_vol"),
        pl.col("SecuCode").cast(pl.Float64, strict=False).alias("_secu_code_numeric"),
        pl.col("BizIndex").cast(pl.Float64, strict=False).alias("_biz_index_numeric"),
    )
    # Stage 2: TradingDay must match the partition date, else the batch is corrupt.
    valid_trading_day = (
        pl.col("_trading_day").is_not_null()
        & pl.col("_trading_day").is_finite()
        & (pl.col("_trading_day") == float(int(trade_date)))
    )
    invalid_trading_days = int(frame.select((~valid_trading_day).sum()).item())
    if invalid_trading_days:
        examples = (
            frame.filter(~valid_trading_day)
            .select("TradingDay")
            .unique()
            .head(3)
            .to_series()
            .to_list()
        )
        raise ValueError(f"{path} contains invalid TradingDay values for {trade_date}: {examples}")

    # Stage 3: validate DealTime / SecuCode / BizIndex, derive typed integer columns.
    valid_deal_time = (
        pl.col("_deal_time_numeric").is_not_null()
        & pl.col("_deal_time_numeric").is_finite()
        & (pl.col("_deal_time_numeric") >= 0)
        & (pl.col("_deal_time_numeric") == pl.col("_deal_time_numeric").floor())
    )
    valid_secu_code = (
        pl.col("_secu_code_numeric").is_not_null()
        & pl.col("_secu_code_numeric").is_finite()
        & pl.col("_secu_code_numeric").is_between(0, 999_999)
        & (pl.col("_secu_code_numeric") == pl.col("_secu_code_numeric").floor())
    )
    frame = frame.with_columns(
        pl.when(valid_deal_time)
        .then(pl.col("_deal_time_numeric"))
        .otherwise(-1)
        .cast(pl.Int64)
        .alias("_deal_time"),
        pl.when(valid_secu_code)
        .then(pl.col("_secu_code_numeric"))
        .otherwise(-1)
        .cast(pl.Int64)
        .alias("_secu_code"),
        pl.when(
            pl.col("_biz_index_numeric").is_not_null() & pl.col("_biz_index_numeric").is_finite()
        )
        .then(pl.col("_biz_index_numeric"))
        .otherwise(-1)
        .cast(pl.Int64)
        .alias("_biz_index"),
        pl.col("_stream_order").cast(pl.Int64),
        valid_deal_time.alias("_valid_deal_time"),
        valid_secu_code.alias("_valid_secu_code"),
    )

    # Stage 4: decode deal time into ms-since-midnight and validate the trading clock.
    hours = pl.col("_deal_time") // 10_000_000
    minutes = (pl.col("_deal_time") // 100_000) % 100
    seconds = (pl.col("_deal_time") // 1_000) % 100
    milliseconds = pl.col("_deal_time") % 1_000
    millis_since_midnight = ((hours * 60 + minutes) * 60 + seconds) * 1_000 + milliseconds
    valid_clock = (
        pl.col("_valid_deal_time")
        & hours.is_between(0, 23)
        & minutes.is_between(0, 59)
        & seconds.is_between(0, 59)
        & milliseconds.is_between(0, 999)
    )
    valid_values = (
        pl.col("_price_cents").is_not_null()
        & pl.col("_price_cents").is_finite()
        & (pl.col("_price_cents") > 0)
        & pl.col("_vol").is_not_null()
        & pl.col("_vol").is_finite()
        & (pl.col("_vol") > 0)
    )
    frame = frame.with_columns(
        millis_since_midnight.alias("_millis"),
        valid_clock.alias("_valid_clock"),
        valid_values.alias("_valid_values"),
    )

    # Stage 5: classify each row into a trading session and assign a minute number.
    opening_auction = pl.col("_millis").is_between(_OPENING_AUCTION_START_MS, _MORNING_START_MS - 1)
    morning = pl.col("_millis").is_between(_MORNING_START_MS, _MORNING_END_MS)
    afternoon = pl.col("_millis").is_between(_AFTERNOON_START_MS, _CLOSING_AUCTION_END_MS)
    in_session = pl.col("_valid_clock") & (opening_auction | morning | afternoon)

    minute_number = ((pl.col("_millis") + 59_999) // 60_000).clip(
        lower_bound=_MORNING_START_MS // 60_000 + 1,
        upper_bound=_CONTINUOUS_CLOSE_MS // 60_000,
    )
    morning_close = (_MORNING_END_MS - 999) // 60_000
    afternoon_open = _AFTERNOON_START_MS // 60_000
    minute_number = (
        pl.when(opening_auction)
        .then(_MORNING_START_MS // 60_000)
        .when(morning)
        .then(minute_number.clip(upper_bound=morning_close))
        .when(afternoon)
        .then(minute_number.clip(lower_bound=afternoon_open + 1))
        .otherwise(minute_number)
    )
    minute_number = (
        pl.when(pl.col("_millis") > _CONTINUOUS_CLOSE_MS)
        .then(_CONTINUOUS_CLOSE_MS // 60_000)
        .otherwise(minute_number)
    )
    frame = frame.with_columns(
        in_session.alias("_in_session"),
        minute_number.alias("_minute_number"),
        pl.when(pl.col("_valid_secu_code"))
        .then(pl.col("_secu_code").cast(pl.String).str.pad_start(6, "0"))
        .otherwise(pl.lit(None, dtype=pl.String))
        .alias("symbol"),
    ).join(symbol_mapping, on="symbol", how="left")

    # Stage 6: map symbol to ts_code via the lookup table, with exchange-suffix fallback.
    fallback_ts_code = (
        pl.when(pl.col("symbol").str.contains(r"^(600|601|603|605|688|689)"))
        .then(pl.col("symbol") + pl.lit(".SH"))
        .when(pl.col("symbol").str.contains(r"^(43|83|87|88|92)"))
        .then(pl.col("symbol") + pl.lit(".BJ"))
        .when(pl.col("symbol").str.contains(r"^(000|001|002|003|300|301|302)"))
        .then(pl.col("symbol") + pl.lit(".SZ"))
        .otherwise(pl.lit(None, dtype=pl.String))
    )
    frame = frame.with_columns(
        pl.col("ts_code").is_not_null().alias("_mapped"),
        fallback_ts_code.alias("_fallback_ts_code"),
    ).with_columns(
        pl.coalesce("ts_code", "_fallback_ts_code").alias("ts_code"),
    )

    # Stage 7: count eligibility buckets, then aggregate eligible rows into minute OHLCV.
    eligible = pl.col("_in_session") & pl.col("_valid_values") & pl.col("ts_code").is_not_null()
    counts_row = frame.select(
        pl.col("_in_session").sum().alias("session_rows"),
        eligible.sum().alias("eligible_rows"),
        (eligible & pl.col("_mapped")).sum().alias("mapping_rows"),
        (eligible & ~pl.col("_mapped")).sum().alias("fallback_rows"),
        (pl.col("_in_session") & pl.col("_valid_values") & pl.col("ts_code").is_null())
        .sum()
        .alias("unresolved_rows"),
        (pl.col("_in_session") & ~pl.col("_valid_values")).sum().alias("invalid_value_rows"),
    ).row(0, named=True)

    day = pd.to_datetime(trade_date, format="%Y%m%d", errors="raise").to_pydatetime()
    work = frame.filter(eligible).with_columns(
        (pl.col("_price_cents") / 100.0).alias("_price"),
        (pl.col("_price_cents") * pl.col("_vol") / 100.0).alias("_amount"),
        (pl.lit(day, dtype=pl.Datetime("ns")) + pl.duration(minutes=pl.col("_minute_number")))
        .cast(pl.Datetime("ns"))
        .alias("trade_time"),
    )
    order = ["_deal_time", "_biz_index", "_stream_order"]
    partial = work.group_by(list(MINUTE_KEY_COLUMNS)).agg(
        pl.col("_price").sort_by(order).first().alias("open"),
        pl.col("_price").sort_by(order).last().alias("close"),
        pl.col("_price").max().alias("high"),
        pl.col("_price").min().alias("low"),
        pl.col("_vol").sum().alias("vol"),
        pl.col("_amount").sum().alias("amount"),
        pl.col("_deal_time").sort_by(order).first().alias("_first_deal_time"),
        pl.col("_biz_index").sort_by(order).first().alias("_first_biz_index"),
        pl.col("_stream_order").sort_by(order).first().alias("_first_stream_order"),
        pl.col("_deal_time").sort_by(order).last().alias("_last_deal_time"),
        pl.col("_biz_index").sort_by(order).last().alias("_last_biz_index"),
        pl.col("_stream_order").sort_by(order).last().alias("_last_stream_order"),
    )
    return partial, {key: int(value) for key, value in counts_row.items()}


def _compact_polars_deal_partials(pl: ModuleType, partials: list[Any]) -> Any:
    combined = pl.concat(partials, how="vertical", rechunk=False)
    first_order = ["_first_deal_time", "_first_biz_index", "_first_stream_order"]
    last_order = ["_last_deal_time", "_last_biz_index", "_last_stream_order"]
    return combined.group_by(list(MINUTE_KEY_COLUMNS)).agg(
        pl.col("open").sort_by(first_order).first().alias("open"),
        pl.col("close").sort_by(last_order).last().alias("close"),
        pl.col("high").max().alias("high"),
        pl.col("low").min().alias("low"),
        pl.col("vol").sum().alias("vol"),
        pl.col("amount").sum().alias("amount"),
        pl.col("_first_deal_time").sort_by(first_order).first(),
        pl.col("_first_biz_index").sort_by(first_order).first(),
        pl.col("_first_stream_order").sort_by(first_order).first(),
        pl.col("_last_deal_time").sort_by(last_order).last(),
        pl.col("_last_biz_index").sort_by(last_order).last(),
        pl.col("_last_stream_order").sort_by(last_order).last(),
    )


def _polars_aggregation_context(
    pl: ModuleType,
    request: _DealAggregationRequest,
) -> _DealAggregationContext:
    path, trade_date, normalized_mapping = _deal_source_identity(request)
    symbol_mapping = _polars_symbol_mapping(pl, normalized_mapping)
    parquet_file = pq.ParquetFile(path)
    context = _DealAggregationContext(
        request=request,
        path=path,
        trade_date=trade_date,
        symbol_mapping=symbol_mapping,
        parquet_file=parquet_file,
    )
    _validate_deal_aggregation_context(context)
    return context


def _compact_polars_partials_if_needed(
    pl: ModuleType,
    state: _DealAggregationState,
    request: _DealAggregationRequest,
) -> None:
    should_compact = (
        state.pending_row_groups >= request.compaction_row_groups
        or state.pending_rows >= request.compaction_rows
    )
    if not should_compact or (not state.pending_partials and state.accumulator is None):
        return
    to_compact = state.pending_partials
    if state.accumulator is not None:
        to_compact = [state.accumulator, *to_compact]
    state.accumulator = _compact_polars_deal_partials(pl, to_compact)
    state.pending_partials = []
    state.pending_rows = 0
    state.pending_row_groups = 0
    state.partial_compactions += 1
    _raise_if_aggregation_too_large(
        state.accumulator.height,
        request.max_aggregated_rows,
    )


def _run_polars_deal_batches(
    pl: ModuleType,
    context: _DealAggregationContext,
    state: _DealAggregationState,
) -> None:
    request = context.request
    parquet_file = context.parquet_file
    batch_context = _PolarsDealBatchContext(
        pl=pl,
        trade_date=context.trade_date,
        symbol_mapping=context.symbol_mapping,
        path=context.path,
    )
    for row_group_start in range(0, parquet_file.num_row_groups, request.batch_row_groups):
        row_groups = list(
            range(
                row_group_start,
                min(row_group_start + request.batch_row_groups, parquet_file.num_row_groups),
            )
        )
        table = parquet_file.read_row_groups(row_groups, columns=list(_DEAL_REQUIRED_COLUMNS))
        state.row_group_batches += 1
        state.counters["input_rows"] += table.num_rows
        partial, group_counts = _aggregate_polars_deal_batch(
            batch_context,
            table,
            state.stream_offset,
        )
        state.stream_offset += table.num_rows
        _accumulate_deal_counts(state, group_counts)
        if partial.height:
            state.pending_partials.append(partial)
            state.pending_rows += partial.height
        state.pending_row_groups += len(row_groups)
        del table, partial
        _compact_polars_partials_if_needed(pl, state, request)
        gc.collect()


def _polars_deal_output(
    pl: ModuleType,
    state: _DealAggregationState,
    request: _DealAggregationRequest,
) -> pd.DataFrame:
    final_partials = state.pending_partials
    if state.accumulator is not None:
        final_partials = [state.accumulator, *final_partials]
    if final_partials:
        compacted = _compact_polars_deal_partials(pl, final_partials)
        polars_output = compacted.select(list(CANONICAL_MINUTE_COLUMNS)).sort(
            list(MINUTE_KEY_COLUMNS)
        )
        table = polars_output.to_arrow().cast(CANONICAL_MINUTE_SCHEMA)
        output = _canonicalize_frame(table.to_pandas(), label="Guan deal aggregation (polars)")
    else:
        output = _empty_canonical_frame()
    _raise_if_aggregation_too_large(len(output), request.max_aggregated_rows)
    return output


def _aggregate_guan_deal_file_polars(
    pl: ModuleType,
    request: _DealAggregationRequest,
) -> GuanDealAggregationResult:
    context = _polars_aggregation_context(pl, request)
    state = _new_deal_aggregation_state(None)
    _run_polars_deal_batches(pl, context, state)
    output = _polars_deal_output(pl, state, request)
    return _deal_aggregation_result(context, state, output, engine="polars")


def aggregate_guan_deal_file(  # noqa: PLR0913
    source_path: str | Path,
    *,
    symbol_to_ts_code: Mapping[str, str] | None = None,
    trade_date: str | None = None,
    engine: MinuteAggregationEngine = "auto",
    batch_row_groups: int | None = None,
    compaction_row_groups: int = DEFAULT_COMPACTION_ROW_GROUPS,
    compaction_rows: int = DEFAULT_COMPACTION_ROWS,
    max_aggregated_rows: int | None = DEFAULT_MAX_AGGREGATED_ROWS,
) -> GuanDealAggregationResult:
    """Stream a Guan deal file into canonical one-minute bars.

    ``engine='auto'`` uses the bounded Polars fast path when the optional
    dependency is installed and otherwise falls back to pandas.  Explicitly
    selecting ``polars`` without the dependency raises an actionable error.
    Polars reads 16 Parquet row groups per batch by default, while pandas keeps
    its four-row-group default.  Both engines periodically compact partial
    bars and preserve deterministic OHLC ordering by ``DealTime``, ``BizIndex``,
    and source row position.

    Price is converted from cents to yuan, volume remains in shares, and amount
    is calculated in yuan.  Real opening-auction executions from 09:25 are
    assigned to 09:30; continuous trades are end-labelled from 09:31 and 13:01.
    Closing-auction records through 15:00:59 are retained in the 15:00 bar.
    No empty or synthetic bars are created.
    """
    if engine not in ("auto", "pandas", "polars"):
        raise ValueError(f"Unsupported Guan deal aggregation engine: {engine!r}")

    polars_module = _try_import_polars() if engine in ("auto", "polars") else None
    if engine == "polars" and polars_module is None:
        raise RuntimeError(
            "Polars aggregation was requested but polars is not installed. "
            "Install the 'minute-fusion' optional dependency."
        )
    resolved_engine = "polars" if polars_module is not None else "pandas"
    resolved_batch_row_groups = batch_row_groups
    if resolved_batch_row_groups is None:
        resolved_batch_row_groups = (
            DEFAULT_POLARS_BATCH_ROW_GROUPS
            if resolved_engine == "polars"
            else DEFAULT_BATCH_ROW_GROUPS
        )
    request = _DealAggregationRequest(
        source_path=source_path,
        symbol_to_ts_code=symbol_to_ts_code,
        trade_date=trade_date,
        batch_row_groups=resolved_batch_row_groups,
        compaction_row_groups=compaction_row_groups,
        compaction_rows=compaction_rows,
        max_aggregated_rows=max_aggregated_rows,
    )

    if resolved_engine == "polars":
        assert polars_module is not None
        return _aggregate_guan_deal_file_polars(polars_module, request)
    return _aggregate_guan_deal_file_pandas(request)
