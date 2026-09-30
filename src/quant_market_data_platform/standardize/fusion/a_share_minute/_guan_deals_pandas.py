"""Bounded pandas/common implementation details for Guan deal aggregation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .normalize import (
    _canonicalize_frame,
    _deal_time_components,
    _empty_canonical_frame,
    _normalize_symbol_mapping,
    _resolve_deal_symbols,
    _trade_date_from_deal_path,
)
from .schema import (
    _AFTERNOON_START_MS,
    _CLOSING_AUCTION_END_MS,
    _CONTINUOUS_CLOSE_MS,
    _DEAL_REQUIRED_COLUMNS,
    _MORNING_END_MS,
    _MORNING_START_MS,
    _OPENING_AUCTION_START_MS,
    MINUTE_KEY_COLUMNS,
    GuanDealAggregationResult,
    GuanDealAggregationStats,
)


def _deal_minute_buckets(raw_times: pd.Series, trade_date: str) -> tuple[pd.Series, pd.Series]:
    millis, valid_clock = _deal_time_components(raw_times)
    opening_auction = millis.between(_OPENING_AUCTION_START_MS, _MORNING_START_MS - 1)
    morning = millis.between(_MORNING_START_MS, _MORNING_END_MS)
    afternoon = millis.between(_AFTERNOON_START_MS, _CLOSING_AUCTION_END_MS)
    in_session = valid_clock & (opening_auction | morning | afternoon)

    minute_number = millis.add(59_999).floordiv(60_000)
    minute_number = minute_number.clip(
        lower=_MORNING_START_MS // 60_000 + 1,
        upper=_CONTINUOUS_CLOSE_MS // 60_000,
    )
    minute_number.loc[opening_auction] = _MORNING_START_MS // 60_000
    morning_close = (_MORNING_END_MS - 999) // 60_000
    minute_number.loc[morning] = minute_number.loc[morning].clip(upper=morning_close)
    afternoon_open = _AFTERNOON_START_MS // 60_000
    minute_number.loc[afternoon] = minute_number.loc[afternoon].clip(lower=afternoon_open + 1)
    minute_number.loc[millis.gt(_CONTINUOUS_CLOSE_MS)] = _CONTINUOUS_CLOSE_MS // 60_000

    buckets = pd.Series(pd.NaT, index=raw_times.index, dtype="datetime64[ns]")
    day = pd.to_datetime(trade_date, format="%Y%m%d", errors="raise")
    buckets.loc[in_session] = day + pd.to_timedelta(minute_number.loc[in_session], unit="m")
    return buckets, in_session


def _validate_deal_trading_day(frame: pd.DataFrame, trade_date: str, path: Path) -> None:
    values = pd.to_numeric(frame["TradingDay"], errors="coerce")
    valid = values.notna() & values.mod(1).eq(0) & values.eq(int(trade_date))
    if not valid.all():
        examples = frame.loc[~valid, "TradingDay"].astype(str).drop_duplicates().tolist()[:3]
        raise ValueError(f"{path} contains invalid TradingDay values for {trade_date}: {examples}")


def _aggregate_deal_batch(
    frame: pd.DataFrame,
    *,
    trade_date: str,
    symbol_mapping: Mapping[str, str],
    stream_offset: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    buckets, in_session = _deal_minute_buckets(frame["DealTime"], trade_date)
    ts_codes, resolution = _resolve_deal_symbols(frame["SecuCode"], symbol_mapping)
    prices_cents = pd.to_numeric(frame["Price"], errors="coerce")
    volumes = pd.to_numeric(frame["Volume"], errors="coerce")
    valid_values = prices_cents.notna() & prices_cents.gt(0) & volumes.notna() & volumes.gt(0)
    eligible = in_session & valid_values & ts_codes.notna()

    counts = {
        "session_rows": int(in_session.sum()),
        "eligible_rows": int(eligible.sum()),
        "mapping_rows": int((eligible & resolution.eq("mapping")).sum()),
        "fallback_rows": int((eligible & resolution.eq("fallback")).sum()),
        "unresolved_rows": int((in_session & valid_values & ts_codes.isna()).sum()),
        "invalid_value_rows": int((in_session & ~valid_values).sum()),
    }
    if not eligible.any():
        return pd.DataFrame(), counts

    positions = np.arange(len(frame), dtype="int64") + stream_offset
    work = pd.DataFrame(
        {
            "ts_code": ts_codes.loc[eligible].astype("string"),
            "trade_time": buckets.loc[eligible],
            "price": prices_cents.loc[eligible].astype("float64") / 100.0,
            "vol": volumes.loc[eligible].astype("float64"),
            "amount": (
                prices_cents.loc[eligible].astype("float64")
                * volumes.loc[eligible].astype("float64")
                / 100.0
            ),
            "_deal_time": pd.to_numeric(frame.loc[eligible, "DealTime"], errors="coerce").astype(
                "int64"
            ),
            "_biz_index": pd.to_numeric(frame.loc[eligible, "BizIndex"], errors="coerce")
            .fillna(-1)
            .astype("int64"),
            "_stream_order": positions[eligible.to_numpy()],
        }
    )
    keys = list(MINUTE_KEY_COLUMNS)
    work = work.sort_values(keys + ["_deal_time", "_biz_index", "_stream_order"], kind="stable")
    grouped = work.groupby(keys, sort=False, observed=True)
    partial = grouped.agg(
        open=("price", "first"),
        close=("price", "last"),
        high=("price", "max"),
        low=("price", "min"),
        vol=("vol", "sum"),
        amount=("amount", "sum"),
        _first_deal_time=("_deal_time", "first"),
        _first_biz_index=("_biz_index", "first"),
        _first_stream_order=("_stream_order", "first"),
        _last_deal_time=("_deal_time", "last"),
        _last_biz_index=("_biz_index", "last"),
        _last_stream_order=("_stream_order", "last"),
    )
    return partial.reset_index(), counts


def _compact_deal_partials(partials: list[pd.DataFrame]) -> pd.DataFrame:
    if not partials:
        return pd.DataFrame()
    combined = pd.concat(partials, ignore_index=True)
    keys = list(MINUTE_KEY_COLUMNS)

    totals = (
        combined.groupby(keys, sort=False, observed=True)
        .agg(
            high=("high", "max"),
            low=("low", "min"),
            vol=("vol", "sum"),
            amount=("amount", "sum"),
        )
        .reset_index()
    )
    opens = (
        combined.sort_values(
            keys + ["_first_deal_time", "_first_biz_index", "_first_stream_order"],
            kind="stable",
        )
        .drop_duplicates(keys, keep="first")
        .loc[
            :,
            keys + ["open", "_first_deal_time", "_first_biz_index", "_first_stream_order"],
        ]
    )
    closes = (
        combined.sort_values(
            keys + ["_last_deal_time", "_last_biz_index", "_last_stream_order"],
            kind="stable",
        )
        .drop_duplicates(keys, keep="last")
        .loc[
            :,
            keys + ["close", "_last_deal_time", "_last_biz_index", "_last_stream_order"],
        ]
    )
    return totals.merge(opens, on=keys, how="left", validate="one_to_one").merge(
        closes,
        on=keys,
        how="left",
        validate="one_to_one",
    )


def _combine_deal_partials(partials: list[pd.DataFrame]) -> pd.DataFrame:
    compacted = _compact_deal_partials(partials)
    if compacted.empty:
        return _empty_canonical_frame()
    return _canonicalize_frame(compacted, label="Guan deal aggregation")


@dataclass(frozen=True)
class _DealAggregationRequest:
    source_path: str | Path
    symbol_to_ts_code: Mapping[str, str] | None
    trade_date: str | None
    batch_row_groups: int
    compaction_row_groups: int
    compaction_rows: int
    max_aggregated_rows: int | None


@dataclass(frozen=True)
class _DealAggregationContext:
    request: _DealAggregationRequest
    path: Path
    trade_date: str
    symbol_mapping: Any
    parquet_file: Any


@dataclass
class _DealAggregationState:
    pending_partials: list[Any]
    accumulator: Any
    pending_rows: int
    pending_row_groups: int
    partial_compactions: int
    row_group_batches: int
    counters: dict[str, int]
    stream_offset: int


def _deal_source_identity(request: _DealAggregationRequest) -> tuple[Path, str, dict[str, str]]:
    path = Path(request.source_path)
    trade_date = _trade_date_from_deal_path(path, request.trade_date)
    symbol_mapping = _normalize_symbol_mapping(request.symbol_to_ts_code)
    return path, trade_date, symbol_mapping


def _validate_deal_aggregation_context(context: _DealAggregationContext) -> None:
    missing = set(_DEAL_REQUIRED_COLUMNS).difference(context.parquet_file.schema_arrow.names)
    if missing:
        raise ValueError(f"Guan deal file {context.path} is missing columns: {sorted(missing)}")
    request = context.request
    if request.batch_row_groups < 1:
        raise ValueError("batch_row_groups must be at least 1")
    if request.compaction_row_groups < 1:
        raise ValueError("compaction_row_groups must be at least 1")
    if request.compaction_rows < 1:
        raise ValueError("compaction_rows must be at least 1")
    if request.max_aggregated_rows is not None and request.max_aggregated_rows < 1:
        raise ValueError("max_aggregated_rows must be at least 1 when set")


def _new_deal_aggregation_state(accumulator: Any) -> _DealAggregationState:
    return _DealAggregationState(
        pending_partials=[],
        accumulator=accumulator,
        pending_rows=0,
        pending_row_groups=0,
        partial_compactions=0,
        row_group_batches=0,
        counters={
            "input_rows": 0,
            "session_rows": 0,
            "eligible_rows": 0,
            "mapping_rows": 0,
            "fallback_rows": 0,
            "unresolved_rows": 0,
            "invalid_value_rows": 0,
        },
        stream_offset=0,
    )


def _accumulate_deal_counts(state: _DealAggregationState, counts: Mapping[str, int]) -> None:
    for key, value in counts.items():
        state.counters[key] += value


def _raise_if_aggregation_too_large(actual_rows: int, maximum_rows: int | None) -> None:
    if maximum_rows is not None and actual_rows > maximum_rows:
        raise MemoryError(
            "Guan deal aggregation exceeded max_aggregated_rows "
            f"({actual_rows:,} > {maximum_rows:,})"
        )


def _compact_pandas_partials_if_needed(
    state: _DealAggregationState,
    request: _DealAggregationRequest,
) -> None:
    should_compact = (
        state.pending_row_groups >= request.compaction_row_groups
        or state.pending_rows >= request.compaction_rows
    )
    if not should_compact:
        return
    to_compact = state.pending_partials
    if not state.accumulator.empty:
        to_compact = [state.accumulator, *to_compact]
    state.accumulator = _compact_deal_partials(to_compact)
    state.pending_partials = []
    state.pending_rows = 0
    state.pending_row_groups = 0
    state.partial_compactions += 1
    _raise_if_aggregation_too_large(len(state.accumulator), request.max_aggregated_rows)


def _run_pandas_deal_batches(
    context: _DealAggregationContext,
    state: _DealAggregationState,
) -> None:
    request = context.request
    parquet_file = context.parquet_file
    for row_group_start in range(0, parquet_file.num_row_groups, request.batch_row_groups):
        row_groups = list(
            range(
                row_group_start,
                min(row_group_start + request.batch_row_groups, parquet_file.num_row_groups),
            )
        )
        frame = parquet_file.read_row_groups(
            row_groups,
            columns=list(_DEAL_REQUIRED_COLUMNS),
        ).to_pandas()
        state.row_group_batches += 1
        _validate_deal_trading_day(frame, context.trade_date, context.path)
        state.counters["input_rows"] += len(frame)
        partial, group_counts = _aggregate_deal_batch(
            frame,
            trade_date=context.trade_date,
            symbol_mapping=context.symbol_mapping,
            stream_offset=state.stream_offset,
        )
        state.stream_offset += len(frame)
        _accumulate_deal_counts(state, group_counts)
        if not partial.empty:
            state.pending_partials.append(partial)
            state.pending_rows += len(partial)
        state.pending_row_groups += len(row_groups)
        del frame, partial
        _compact_pandas_partials_if_needed(state, request)


def _pandas_deal_output(
    state: _DealAggregationState,
    request: _DealAggregationRequest,
) -> pd.DataFrame:
    final_partials = state.pending_partials
    if not state.accumulator.empty:
        final_partials = [state.accumulator, *final_partials]
    output = _combine_deal_partials(final_partials)
    del final_partials
    state.accumulator = pd.DataFrame()
    state.pending_partials = []
    _raise_if_aggregation_too_large(len(output), request.max_aggregated_rows)
    return output.sort_values(list(MINUTE_KEY_COLUMNS), kind="stable").reset_index(drop=True)


def _deal_aggregation_result(
    context: _DealAggregationContext,
    state: _DealAggregationState,
    output: pd.DataFrame,
    *,
    engine: str,
) -> GuanDealAggregationResult:
    stats = GuanDealAggregationStats(
        source_path=str(context.path),
        trade_date=context.trade_date,
        engine=engine,
        row_groups=context.parquet_file.num_row_groups,
        row_group_batches=state.row_group_batches,
        output_rows=len(output),
        output_symbols=int(output["ts_code"].nunique()),
        output_traded_symbols=int(output.loc[output["vol"].gt(0), "ts_code"].nunique()),
        output_vol_sum=float(output["vol"].sum()),
        output_amount_sum=float(output["amount"].sum()),
        unit_profile="guan_deal_price_cents_volume_shares",
        partial_compactions=state.partial_compactions,
        **state.counters,
    )
    return GuanDealAggregationResult(frame=output, stats=stats)


def _aggregate_guan_deal_file_pandas(
    request: _DealAggregationRequest,
) -> GuanDealAggregationResult:
    """Aggregate a Guan deal file with the bounded pandas implementation."""
    path, trade_date, symbol_mapping = _deal_source_identity(request)
    parquet_file = pq.ParquetFile(path)
    context = _DealAggregationContext(
        request=request,
        path=path,
        trade_date=trade_date,
        symbol_mapping=symbol_mapping,
        parquet_file=parquet_file,
    )
    _validate_deal_aggregation_context(context)
    state = _new_deal_aggregation_state(pd.DataFrame())
    _run_pandas_deal_batches(context, state)
    output = _pandas_deal_output(state, request)
    return _deal_aggregation_result(context, state, output, engine="pandas")


def _polars_symbol_mapping(pl: ModuleType, mapping: Mapping[str, str]) -> Any:
    return pl.DataFrame(
        {"symbol": list(mapping), "ts_code": list(mapping.values())},
        schema={"symbol": pl.String, "ts_code": pl.String},
    )


@dataclass(frozen=True)
class _PolarsDealBatchContext:
    pl: ModuleType
    trade_date: str
    symbol_mapping: Any
    path: Path
