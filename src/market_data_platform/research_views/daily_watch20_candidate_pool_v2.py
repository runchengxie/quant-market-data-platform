"""Strict PIT v2 snapshot assembly for DailyWatch20 candidate pools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
import pandas as pd

THS_HOT_V2_BATCH_GAP_SECONDS = 15
THS_HOT_V2_MAX_COMPONENT_SPAN_SECONDS = 180
THS_HOT_V2_MAX_MISSING_RANKS = 2
THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS = 100
THS_HOT_V2_REQUIRE_RANK_ONE = True
THS_HOT_V2_REQUIRED_TOP_RANKS = 20


class THSHotV2NoCompleteMinute(RuntimeError):
    """The exact-date partition has no minute with the required symbol count."""


@dataclass(frozen=True)
class THSHotV2Snapshot:
    """One validated v2 assembly plus its audit metadata."""

    frame: pd.DataFrame
    assembly_path: str
    raw_rows: int
    snapshot_rows: int
    snapshot_minute: pd.Timestamp
    latest_observed_minute: pd.Timestamp
    skipped_incomplete_snapshots: int
    snapshot_time_min: str
    snapshot_time_max: str
    duplicate_symbol_rows_removed: int
    out_of_scope_rows_removed: int
    snapshot_unique_symbols: int
    missing_ranks: tuple[int, ...]
    rank_ties: int
    component_time_start: str
    component_time_end: str
    component_span_seconds: int


def _prepare_rows(
    raw: pd.DataFrame,
    *,
    rank_times: pd.Series,
    symbol_col: str,
) -> pd.DataFrame:
    work = raw.copy()
    work["_source_row"] = np.arange(len(work), dtype=np.int64)
    work["_candidate_symbol"] = (
        cast(pd.Series, work[symbol_col]).astype("string").str.strip().str.upper()
    )
    work["_rank_time"] = rank_times.to_numpy()
    work["_snapshot_minute"] = rank_times.dt.floor("min").to_numpy()
    work["_in_scope"] = cast(pd.Series, work["_candidate_symbol"]).str.fullmatch(
        r"\d{6}\.(?:SH|SZ)", na=False
    )
    return work


def _component_times(frame: pd.DataFrame) -> tuple[str, str, int]:
    start = cast(pd.Timestamp, frame["_rank_time"].min())
    end = cast(pd.Timestamp, frame["_rank_time"].max())
    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        end.strftime("%Y-%m-%d %H:%M:%S"),
        int((end - start).total_seconds()),
    )


def _validated_missing_ranks(
    frame: pd.DataFrame,
    *,
    required_top_ranks: int = THS_HOT_V2_REQUIRED_TOP_RANKS,
    policy_version: str = "v2",
) -> tuple[int, ...]:
    rank_col = "_rank_value" if "_rank_value" in frame.columns else "ths_hot_rank"
    if rank_col not in frame.columns:
        raise RuntimeError(f"strict THS-hot {policy_version} snapshot has no validated rank column")
    ranks = cast(pd.Series, pd.to_numeric(frame[rank_col], errors="coerce"))
    invalid_ranks = (
        ranks.isna() | ranks.le(0) | ranks.gt(THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS) | ranks.mod(1).ne(0)
    )
    if bool(invalid_ranks.any()):
        raise RuntimeError(f"strict THS-hot {policy_version} snapshot contains invalid ranks")
    rank_values = set(ranks.astype(int))
    if len(rank_values) != len(frame):
        raise RuntimeError(f"strict THS-hot {policy_version} snapshot contains rank ties")
    if THS_HOT_V2_REQUIRE_RANK_ONE and 1 not in rank_values:
        raise RuntimeError(f"strict THS-hot {policy_version} snapshot is missing required rank 1")
    missing_required = tuple(sorted(set(range(1, required_top_ranks + 1)) - rank_values))
    if missing_required:
        raise RuntimeError(
            f"strict THS-hot {policy_version} snapshot is missing required top ranks: "
            f"{list(missing_required)}"
        )
    max_rank = max(rank_values)
    missing_ranks = tuple(sorted(set(range(1, max_rank + 1)) - rank_values))
    if len(missing_ranks) > THS_HOT_V2_MAX_MISSING_RANKS:
        raise RuntimeError(
            f"strict THS-hot {policy_version} snapshot has too many missing ranks: "
            f"{list(missing_ranks)}, maximum is {THS_HOT_V2_MAX_MISSING_RANKS}"
        )
    return missing_ranks


def _validate_snapshot_rank_policy(
    snapshot: THSHotV2Snapshot,
    *,
    required_top_ranks: int,
    policy_version: str,
) -> None:
    frame = snapshot.frame
    if frame.empty:
        raise RuntimeError(f"strict THS-hot {policy_version} snapshot is empty")
    if len(frame) != snapshot.snapshot_unique_symbols:
        raise RuntimeError(
            f"strict THS-hot {policy_version} snapshot symbol-count metadata mismatch"
        )
    if snapshot.rank_ties != 0:
        raise RuntimeError(f"strict THS-hot {policy_version} snapshot rank-tie metadata is invalid")
    if "symbol" not in frame.columns or cast(pd.Series, frame["symbol"]).duplicated().any():
        raise RuntimeError(f"strict THS-hot {policy_version} snapshot contains duplicate symbols")
    missing_ranks = _validated_missing_ranks(
        frame,
        required_top_ranks=required_top_ranks,
        policy_version=policy_version,
    )
    if missing_ranks != snapshot.missing_ranks:
        raise RuntimeError(
            f"strict THS-hot {policy_version} snapshot missing-rank metadata mismatch: "
            f"expected {list(missing_ranks)}, recorded {list(snapshot.missing_ranks)}"
        )


def validate_v2_snapshot_rank_policy(snapshot: THSHotV2Snapshot) -> None:
    """Fail closed unless an assembled snapshot satisfies and records v2 rank semantics."""

    _validate_snapshot_rank_policy(
        snapshot,
        required_top_ranks=THS_HOT_V2_REQUIRED_TOP_RANKS,
        policy_version="v2",
    )


def _assemble_sparse_unique(
    selected: pd.DataFrame,
    *,
    snapshot_min_symbols: int,
    require_raw_unique_symbols: bool,
    required_top_ranks: int,
    policy_version: str,
) -> tuple[pd.DataFrame, int, int, tuple[int, ...], int]:
    in_scope = cast(pd.Series, selected["_in_scope"]).fillna(False).astype(bool)
    out_of_scope_rows = int((~in_scope).sum())
    work = cast(pd.DataFrame, selected.loc[in_scope]).copy()
    if work.empty:
        raise RuntimeError("strict THS-hot v2 snapshot has no in-scope symbols")

    ranks = cast(pd.Series, pd.to_numeric(work["rank"], errors="coerce"))
    invalid_ranks = (
        ranks.isna() | ranks.le(0) | ranks.gt(THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS) | ranks.mod(1).ne(0)
    )
    if bool(invalid_ranks.any()):
        raise RuntimeError("strict THS-hot v2 snapshot contains invalid ranks")
    work["_rank_value"] = ranks.astype(int)

    pct_change = cast(pd.Series, pd.to_numeric(work["pct_change"], errors="coerce"))
    work["_pct_value"] = pct_change
    for _, same_observation in work.groupby(
        ["_candidate_symbol", "_rank_time"], sort=False, dropna=False
    ):
        if (
            same_observation["_rank_value"].nunique(dropna=False) > 1
            or same_observation["_pct_value"].nunique(dropna=False) > 1
        ):
            raise RuntimeError(
                "strict THS-hot v2 snapshot contains conflicting same-time symbol rows"
            )

    duplicate_symbol_rows = len(work) - int(work["_candidate_symbol"].nunique())
    if require_raw_unique_symbols and duplicate_symbol_rows:
        raise RuntimeError("strict THS-hot v2 batch fallback contains repeated symbols")
    deduplicated = (
        work.sort_values(
            ["_candidate_symbol", "_rank_time", "_source_row"],
            kind="mergesort",
        )
        .drop_duplicates("_candidate_symbol", keep="last")
        .copy()
    )
    snapshot_unique_symbols = len(deduplicated)
    if snapshot_unique_symbols < snapshot_min_symbols:
        raise RuntimeError(
            "strict THS-hot v2 snapshot is incomplete after deduplication: "
            f"{snapshot_unique_symbols} symbols, requires at least {snapshot_min_symbols}"
        )
    rank_ties = snapshot_unique_symbols - int(deduplicated["_rank_value"].nunique())
    if rank_ties:
        raise RuntimeError(f"strict THS-hot v2 snapshot contains rank ties: {rank_ties}")
    if snapshot_unique_symbols > THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS:
        raise RuntimeError(
            "strict THS-hot v2 snapshot has too many symbols: "
            f"{snapshot_unique_symbols}, maximum is {THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS}"
        )
    missing_ranks = _validated_missing_ranks(
        deduplicated,
        required_top_ranks=required_top_ranks,
        policy_version=policy_version,
    )

    deduplicated["ths_hot_rank"] = cast(pd.Series, deduplicated["_rank_value"]).astype(int)
    deduplicated["symbol"] = cast(pd.Series, deduplicated["_candidate_symbol"])
    deduplicated = deduplicated.sort_values(
        ["ths_hot_rank", "_rank_time", "symbol"], kind="mergesort"
    ).reset_index(drop=True)
    return (
        deduplicated,
        duplicate_symbol_rows,
        out_of_scope_rows,
        missing_ranks,
        rank_ties,
    )


def select_latest_sparse_minute_v2(  # noqa: PLR0913
    raw: pd.DataFrame,
    *,
    rank_times: pd.Series,
    symbol_col: str,
    snapshot_min_symbols: int,
    close_cutoff_minute: int,
    max_snapshot_fallback_minutes: int,
    required_top_ranks: int = THS_HOT_V2_REQUIRED_TOP_RANKS,
    policy_version: str = "v2",
) -> THSHotV2Snapshot:
    """Assemble the latest qualifying minute without rank-gap imputation."""

    work = _prepare_rows(raw, rank_times=rank_times, symbol_col=symbol_col)
    in_scope = cast(pd.Series, work["_in_scope"]).fillna(False).astype(bool)
    snapshot_symbol_counts = (
        work.loc[in_scope].groupby("_snapshot_minute", sort=True)["_candidate_symbol"].nunique()
    )
    complete_minutes = snapshot_symbol_counts.loc[
        snapshot_symbol_counts.ge(snapshot_min_symbols)
    ].index
    if len(complete_minutes) == 0:
        largest = int(snapshot_symbol_counts.max()) if not snapshot_symbol_counts.empty else 0
        raise THSHotV2NoCompleteMinute(
            "strict THS-hot v2 partition has no complete minute snapshot: "
            f"largest has {largest} symbols, requires at least {snapshot_min_symbols}"
        )

    latest_observed_minute = cast(pd.Timestamp, work["_snapshot_minute"].max())
    latest_minute = cast(pd.Timestamp, complete_minutes.max())
    minute_of_day = latest_minute.hour * 60 + latest_minute.minute
    if minute_of_day < close_cutoff_minute:
        raise RuntimeError(
            "strict THS-hot v2 partition has no close snapshot at or after "
            f"{close_cutoff_minute // 60:02d}:{close_cutoff_minute % 60:02d}"
        )
    fallback_minutes = int((latest_observed_minute - latest_minute).total_seconds() // 60)
    if fallback_minutes > max_snapshot_fallback_minutes:
        raise RuntimeError(
            "strict THS-hot v2 latest complete minute is too far behind the latest observation: "
            f"{fallback_minutes} minutes"
        )

    selected = cast(pd.DataFrame, work.loc[work["_snapshot_minute"].eq(latest_minute)]).copy()
    (
        snapshot,
        duplicate_rows,
        out_of_scope_rows,
        missing_ranks,
        rank_ties,
    ) = _assemble_sparse_unique(
        selected,
        snapshot_min_symbols=snapshot_min_symbols,
        require_raw_unique_symbols=False,
        required_top_ranks=required_top_ranks,
        policy_version=policy_version,
    )
    component_start, component_end, component_span = _component_times(selected)
    return THSHotV2Snapshot(
        frame=snapshot,
        assembly_path="minute_sparse_unique",
        raw_rows=len(raw),
        snapshot_rows=len(selected),
        snapshot_minute=latest_minute,
        latest_observed_minute=latest_observed_minute,
        skipped_incomplete_snapshots=int((snapshot_symbol_counts.index > latest_minute).sum()),
        snapshot_time_min=component_start,
        snapshot_time_max=component_end,
        duplicate_symbol_rows_removed=duplicate_rows,
        out_of_scope_rows_removed=out_of_scope_rows,
        snapshot_unique_symbols=len(snapshot),
        missing_ranks=missing_ranks,
        rank_ties=rank_ties,
        component_time_start=component_start,
        component_time_end=component_end,
        component_span_seconds=component_span,
    )


def select_latest_batch_fallback_v2(  # noqa: PLR0913
    raw: pd.DataFrame,
    *,
    rank_times: pd.Series,
    symbol_col: str,
    snapshot_min_symbols: int,
    close_cutoff_minute: int,
    max_snapshot_fallback_minutes: int,
    required_top_ranks: int = THS_HOT_V2_REQUIRED_TOP_RANKS,
    policy_version: str = "v2",
) -> THSHotV2Snapshot:
    """Assemble one clean cross-minute legacy component when no minute qualifies."""

    work = _prepare_rows(raw, rank_times=rank_times, symbol_col=symbol_col)
    in_scope = cast(pd.Series, work["_in_scope"]).fillna(False).astype(bool)
    observations = cast(pd.DataFrame, work.loc[in_scope]).sort_values(
        ["_rank_time", "_source_row"], kind="mergesort"
    )
    if observations.empty:
        raise RuntimeError("strict THS-hot v2 partition has no in-scope batch observations")
    gap_seconds = cast(pd.Series, observations["_rank_time"]).diff().dt.total_seconds()
    observations = observations.copy()
    observations["_component"] = gap_seconds.gt(THS_HOT_V2_BATCH_GAP_SECONDS).cumsum()

    components: list[tuple[int, pd.DataFrame, int]] = []
    for component_index, (_, component) in enumerate(observations.groupby("_component", sort=True)):
        unique_symbols = int(component["_candidate_symbol"].nunique())
        components.append((component_index, component.copy(), unique_symbols))
    complete = [item for item in components if item[2] >= snapshot_min_symbols]
    if not complete:
        largest = max((item[2] for item in components), default=0)
        raise RuntimeError(
            "strict THS-hot v2 partition has no complete batch fallback: "
            f"largest has {largest} symbols, requires at least {snapshot_min_symbols}"
        )
    component_id, selected, _ = max(
        complete,
        key=lambda item: (cast(pd.Timestamp, item[1]["_rank_time"].max()), item[0]),
    )
    del component_id
    component_start, component_end, component_span = _component_times(selected)
    selected_end = cast(pd.Timestamp, pd.Timestamp(component_end))
    minute_of_day = selected_end.hour * 60 + selected_end.minute
    if minute_of_day < close_cutoff_minute:
        raise RuntimeError(
            "strict THS-hot v2 partition has no close batch at or after "
            f"{close_cutoff_minute // 60:02d}:{close_cutoff_minute % 60:02d}"
        )
    if component_span > THS_HOT_V2_MAX_COMPONENT_SPAN_SECONDS:
        raise RuntimeError(
            f"strict THS-hot v2 batch fallback exceeds maximum span: {component_span} seconds"
        )

    latest_observed = cast(pd.Timestamp, work["_rank_time"].max())
    fallback_minutes = int((latest_observed - selected_end).total_seconds() // 60)
    if fallback_minutes > max_snapshot_fallback_minutes:
        raise RuntimeError(
            "strict THS-hot v2 latest complete batch is too far behind the latest observation: "
            f"{fallback_minutes} minutes"
        )
    (
        snapshot,
        duplicate_rows,
        out_of_scope_rows,
        missing_ranks,
        rank_ties,
    ) = _assemble_sparse_unique(
        selected,
        snapshot_min_symbols=snapshot_min_symbols,
        require_raw_unique_symbols=True,
        required_top_ranks=required_top_ranks,
        policy_version=policy_version,
    )
    later_incomplete = sum(
        cast(pd.Timestamp, item[1]["_rank_time"].max()) > selected_end
        for item in components
        if item[2] < snapshot_min_symbols
    )
    return THSHotV2Snapshot(
        frame=snapshot,
        assembly_path="batch_fallback",
        raw_rows=len(raw),
        snapshot_rows=len(selected),
        snapshot_minute=selected_end.floor("min"),
        latest_observed_minute=latest_observed.floor("min"),
        skipped_incomplete_snapshots=later_incomplete,
        snapshot_time_min=component_start,
        snapshot_time_max=component_end,
        duplicate_symbol_rows_removed=duplicate_rows,
        out_of_scope_rows_removed=out_of_scope_rows,
        snapshot_unique_symbols=len(snapshot),
        missing_ranks=missing_ranks,
        rank_ties=rank_ties,
        component_time_start=component_start,
        component_time_end=component_end,
        component_span_seconds=component_span,
    )


__all__ = [
    "THS_HOT_V2_BATCH_GAP_SECONDS",
    "THS_HOT_V2_MAX_COMPONENT_SPAN_SECONDS",
    "THS_HOT_V2_MAX_MISSING_RANKS",
    "THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS",
    "THS_HOT_V2_REQUIRED_TOP_RANKS",
    "THS_HOT_V2_REQUIRE_RANK_ONE",
    "THSHotV2NoCompleteMinute",
    "THSHotV2Snapshot",
    "select_latest_batch_fallback_v2",
    "select_latest_sparse_minute_v2",
    "validate_v2_snapshot_rank_policy",
]
