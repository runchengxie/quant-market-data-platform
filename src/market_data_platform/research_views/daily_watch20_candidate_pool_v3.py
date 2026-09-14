"""Strict PIT v3 ranking policy for DailyWatch20 candidate pools.

V3 keeps the v2 sparse snapshot assembly and audit evidence while allowing up
to two source-native rank gaps anywhere after rank one.  TuShare rank remains
an eligibility provenance field rather than a model feature, so a bounded gap
does not get imputed or renumbered.
"""

from __future__ import annotations

import pandas as pd

from .daily_watch20_candidate_pool_v2 import (
    THS_HOT_V2_BATCH_GAP_SECONDS,
    THS_HOT_V2_MAX_COMPONENT_SPAN_SECONDS,
    THS_HOT_V2_MAX_MISSING_RANKS,
    THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS,
    THSHotV2NoCompleteMinute,
    THSHotV2Snapshot,
    _validate_snapshot_rank_policy,
    select_latest_batch_fallback_v2,
    select_latest_sparse_minute_v2,
)

THS_HOT_V3_BATCH_GAP_SECONDS = THS_HOT_V2_BATCH_GAP_SECONDS
THS_HOT_V3_MAX_COMPONENT_SPAN_SECONDS = THS_HOT_V2_MAX_COMPONENT_SPAN_SECONDS
THS_HOT_V3_MAX_MISSING_RANKS = THS_HOT_V2_MAX_MISSING_RANKS
THS_HOT_V3_MAX_SNAPSHOT_SYMBOLS = THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS
THS_HOT_V3_REQUIRE_RANK_ONE = True
THS_HOT_V3_REQUIRED_TOP_RANKS = 1

THSHotV3NoCompleteMinute = THSHotV2NoCompleteMinute
THSHotV3Snapshot = THSHotV2Snapshot


def select_latest_sparse_minute_v3(  # noqa: PLR0913
    raw: pd.DataFrame,
    *,
    rank_times: pd.Series,
    symbol_col: str,
    snapshot_min_symbols: int,
    close_cutoff_minute: int,
    max_snapshot_fallback_minutes: int,
) -> THSHotV3Snapshot:
    """Assemble the latest qualifying minute under v3 rank coverage."""

    return select_latest_sparse_minute_v2(
        raw,
        rank_times=rank_times,
        symbol_col=symbol_col,
        snapshot_min_symbols=snapshot_min_symbols,
        close_cutoff_minute=close_cutoff_minute,
        max_snapshot_fallback_minutes=max_snapshot_fallback_minutes,
        required_top_ranks=THS_HOT_V3_REQUIRED_TOP_RANKS,
        policy_version="v3",
    )


def select_latest_batch_fallback_v3(  # noqa: PLR0913
    raw: pd.DataFrame,
    *,
    rank_times: pd.Series,
    symbol_col: str,
    snapshot_min_symbols: int,
    close_cutoff_minute: int,
    max_snapshot_fallback_minutes: int,
) -> THSHotV3Snapshot:
    """Assemble one clean cross-minute component under v3 rank coverage."""

    return select_latest_batch_fallback_v2(
        raw,
        rank_times=rank_times,
        symbol_col=symbol_col,
        snapshot_min_symbols=snapshot_min_symbols,
        close_cutoff_minute=close_cutoff_minute,
        max_snapshot_fallback_minutes=max_snapshot_fallback_minutes,
        required_top_ranks=THS_HOT_V3_REQUIRED_TOP_RANKS,
        policy_version="v3",
    )


def validate_v3_snapshot_rank_policy(snapshot: THSHotV3Snapshot) -> None:
    """Validate v3 rank-one anchoring and bounded source-native gaps."""

    _validate_snapshot_rank_policy(
        snapshot,
        required_top_ranks=THS_HOT_V3_REQUIRED_TOP_RANKS,
        policy_version="v3",
    )


__all__ = [
    "THS_HOT_V3_BATCH_GAP_SECONDS",
    "THS_HOT_V3_MAX_COMPONENT_SPAN_SECONDS",
    "THS_HOT_V3_MAX_MISSING_RANKS",
    "THS_HOT_V3_MAX_SNAPSHOT_SYMBOLS",
    "THS_HOT_V3_REQUIRED_TOP_RANKS",
    "THS_HOT_V3_REQUIRE_RANK_ONE",
    "THSHotV3NoCompleteMinute",
    "THSHotV3Snapshot",
    "select_latest_batch_fallback_v3",
    "select_latest_sparse_minute_v3",
    "validate_v3_snapshot_rank_policy",
]
