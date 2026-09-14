"""Point-in-time candidate-pool contracts for DailyWatch20.

The market-data package owns exact-date discovery, snapshot integrity and source
lineage. Alpha and application layers consume the resulting immutable view.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pandas as pd

from market_data_platform.contract import load_current_contract
from market_data_platform.research_views.daily_watch20_candidate_pool_part01 import (
    CANDIDATE_POOL_MODES,
    THS_HOT_CLOSE_CUTOFF_MINUTE,
    THS_HOT_MAX_SNAPSHOT_FALLBACK_MINUTES,
    CandidatePoolMode,
    DailyWatch20CandidatePool,
    _date_key,
    _load_ths_hot_strict,
    _load_validated_partition,
    _positive_snapshot_pool,
    _select_latest_complete_snapshot,
    _sha256_file,
    _v2_snapshot_from_v1,
    candidate_pool_policy_id,
)

from .daily_watch20_candidate_pool_v2 import (
    THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS,
    THSHotV2NoCompleteMinute,
    select_latest_batch_fallback_v2,
    select_latest_sparse_minute_v2,
    validate_v2_snapshot_rank_policy,
)
from .daily_watch20_candidate_pool_v3 import (
    THS_HOT_V3_MAX_SNAPSHOT_SYMBOLS,
    THSHotV3NoCompleteMinute,
    select_latest_batch_fallback_v3,
    select_latest_sparse_minute_v3,
    validate_v3_snapshot_rank_policy,
)


def _current_asset_root(data_root: Path, dataset: str) -> Path:
    contract_path = data_root / "metadata" / "current_assets" / "a_share_current.json"
    payload = load_current_contract(contract_path)
    if payload is None:
        raise FileNotFoundError(f"A-share current contract not found: {contract_path}")
    entry = payload.get("assets", {}).get(dataset)
    if not isinstance(entry, dict):
        raise ValueError(f"A-share current contract is missing asset: {dataset}")
    if entry.get("availability") != "available":
        reason = str(entry.get("availability_reason") or "asset is unavailable")
        raise RuntimeError(f"A-share current asset is unavailable: {dataset}: {reason}")
    value = entry.get("resolved_path") or entry.get("alias_path")
    if not value:
        raise ValueError(f"A-share current contract has no path for asset: {dataset}")
    path = Path(str(value)).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"A-share current asset directory does not exist: {path}")
    return path


def _load_ths_hot_strict_v2(
    root: Path,
    *,
    source_date: str,
    min_symbols: int,
    snapshot_min_symbols: int,
) -> DailyWatch20CandidatePool:
    raw, files, symbol_col, rank_times = _load_validated_partition(root, source_date)
    try:
        v1_snapshot = _select_latest_complete_snapshot(
            raw.copy(),
            rank_times=rank_times,
            symbol_col=symbol_col,
            snapshot_min_symbols=snapshot_min_symbols,
        )
    except RuntimeError:
        try:
            snapshot = select_latest_sparse_minute_v2(
                raw,
                rank_times=rank_times,
                symbol_col=symbol_col,
                snapshot_min_symbols=snapshot_min_symbols,
                close_cutoff_minute=THS_HOT_CLOSE_CUTOFF_MINUTE,
                max_snapshot_fallback_minutes=THS_HOT_MAX_SNAPSHOT_FALLBACK_MINUTES,
            )
        except THSHotV2NoCompleteMinute:
            snapshot = select_latest_batch_fallback_v2(
                raw,
                rank_times=rank_times,
                symbol_col=symbol_col,
                snapshot_min_symbols=snapshot_min_symbols,
                close_cutoff_minute=THS_HOT_CLOSE_CUTOFF_MINUTE,
                max_snapshot_fallback_minutes=THS_HOT_MAX_SNAPSHOT_FALLBACK_MINUTES,
            )
    else:
        if v1_snapshot.snapshot_unique_symbols > THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS:
            raise RuntimeError(
                "strict THS-hot v2 snapshot has too many symbols: "
                f"{v1_snapshot.snapshot_unique_symbols}, maximum is "
                f"{THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS}"
            )
        snapshot = _v2_snapshot_from_v1(v1_snapshot)

    validate_v2_snapshot_rank_policy(snapshot)
    pool, non_positive_rows = _positive_snapshot_pool(snapshot.frame, min_symbols=min_symbols)
    file_receipts = tuple((path, _sha256_file(path)) for path in files)
    return DailyWatch20CandidatePool(
        mode="ths_hot_strict_v2",
        source_date=source_date,
        frame=pool.reset_index(drop=True),
        policy_id=candidate_pool_policy_id(
            "ths_hot_strict_v2",
            ths_hot_min_symbols=min_symbols,
            ths_hot_snapshot_min_symbols=snapshot_min_symbols,
        ),
        root=root,
        min_symbols=min_symbols,
        raw_rows=snapshot.raw_rows,
        snapshot_rows=snapshot.snapshot_rows,
        snapshot_minute=snapshot.snapshot_minute.strftime("%Y-%m-%d %H:%M"),
        latest_observed_minute=snapshot.latest_observed_minute.strftime("%Y-%m-%d %H:%M"),
        skipped_incomplete_snapshots=snapshot.skipped_incomplete_snapshots,
        snapshot_time_min=snapshot.snapshot_time_min,
        snapshot_time_max=snapshot.snapshot_time_max,
        duplicate_symbol_rows_removed=snapshot.duplicate_symbol_rows_removed,
        out_of_scope_rows_removed=snapshot.out_of_scope_rows_removed,
        non_positive_rows_removed=non_positive_rows,
        snapshot_unique_symbols=snapshot.snapshot_unique_symbols,
        snapshot_min_symbols=snapshot_min_symbols,
        require_positive_change=True,
        files=file_receipts,
        assembly_path=snapshot.assembly_path,
        deduplicated_snapshot_rows=snapshot.snapshot_unique_symbols,
        missing_ranks=snapshot.missing_ranks,
        rank_ties=snapshot.rank_ties,
        component_time_start=snapshot.component_time_start,
        component_time_end=snapshot.component_time_end,
        component_span_seconds=snapshot.component_span_seconds,
    )


def _load_ths_hot_strict_v3(
    root: Path,
    *,
    source_date: str,
    min_symbols: int,
    snapshot_min_symbols: int,
) -> DailyWatch20CandidatePool:
    raw, files, symbol_col, rank_times = _load_validated_partition(root, source_date)
    try:
        v1_snapshot = _select_latest_complete_snapshot(
            raw.copy(),
            rank_times=rank_times,
            symbol_col=symbol_col,
            snapshot_min_symbols=snapshot_min_symbols,
        )
    except RuntimeError:
        try:
            snapshot = select_latest_sparse_minute_v3(
                raw,
                rank_times=rank_times,
                symbol_col=symbol_col,
                snapshot_min_symbols=snapshot_min_symbols,
                close_cutoff_minute=THS_HOT_CLOSE_CUTOFF_MINUTE,
                max_snapshot_fallback_minutes=THS_HOT_MAX_SNAPSHOT_FALLBACK_MINUTES,
            )
        except THSHotV3NoCompleteMinute:
            snapshot = select_latest_batch_fallback_v3(
                raw,
                rank_times=rank_times,
                symbol_col=symbol_col,
                snapshot_min_symbols=snapshot_min_symbols,
                close_cutoff_minute=THS_HOT_CLOSE_CUTOFF_MINUTE,
                max_snapshot_fallback_minutes=THS_HOT_MAX_SNAPSHOT_FALLBACK_MINUTES,
            )
    else:
        if v1_snapshot.snapshot_unique_symbols > THS_HOT_V3_MAX_SNAPSHOT_SYMBOLS:
            raise RuntimeError(
                "strict THS-hot v3 snapshot has too many symbols: "
                f"{v1_snapshot.snapshot_unique_symbols}, maximum is "
                f"{THS_HOT_V3_MAX_SNAPSHOT_SYMBOLS}"
            )
        snapshot = _v2_snapshot_from_v1(v1_snapshot)

    validate_v3_snapshot_rank_policy(snapshot)
    pool, non_positive_rows = _positive_snapshot_pool(snapshot.frame, min_symbols=min_symbols)
    file_receipts = tuple((path, _sha256_file(path)) for path in files)
    return DailyWatch20CandidatePool(
        mode="ths_hot_strict_v3",
        source_date=source_date,
        frame=pool.reset_index(drop=True),
        policy_id=candidate_pool_policy_id(
            "ths_hot_strict_v3",
            ths_hot_min_symbols=min_symbols,
            ths_hot_snapshot_min_symbols=snapshot_min_symbols,
        ),
        root=root,
        min_symbols=min_symbols,
        raw_rows=snapshot.raw_rows,
        snapshot_rows=snapshot.snapshot_rows,
        snapshot_minute=snapshot.snapshot_minute.strftime("%Y-%m-%d %H:%M"),
        latest_observed_minute=snapshot.latest_observed_minute.strftime("%Y-%m-%d %H:%M"),
        skipped_incomplete_snapshots=snapshot.skipped_incomplete_snapshots,
        snapshot_time_min=snapshot.snapshot_time_min,
        snapshot_time_max=snapshot.snapshot_time_max,
        duplicate_symbol_rows_removed=snapshot.duplicate_symbol_rows_removed,
        out_of_scope_rows_removed=snapshot.out_of_scope_rows_removed,
        non_positive_rows_removed=non_positive_rows,
        snapshot_unique_symbols=snapshot.snapshot_unique_symbols,
        snapshot_min_symbols=snapshot_min_symbols,
        require_positive_change=True,
        files=file_receipts,
        assembly_path=snapshot.assembly_path,
        deduplicated_snapshot_rows=snapshot.snapshot_unique_symbols,
        missing_ranks=snapshot.missing_ranks,
        rank_ties=snapshot.rank_ties,
        component_time_start=snapshot.component_time_start,
        component_time_end=snapshot.component_time_end,
        component_span_seconds=snapshot.component_span_seconds,
    )


def load_daily_watch20_candidate_pool(  # noqa: PLR0913
    data_root: str | Path,
    *,
    source_date: str,
    mode: CandidatePoolMode = "all_market",
    ths_hot_root: str | Path | None = None,
    ths_hot_min_symbols: int = 20,
    ths_hot_snapshot_min_symbols: int = 80,
    dc_concept_root: str | Path | None = None,
    dc_concept_cons_root: str | Path | None = None,
    limit_list_root: str | Path | None = None,
    moneyflow_root: str | Path | None = None,
) -> DailyWatch20CandidatePool:
    """Load the requested pool; strict mode never falls back to the full market."""

    expected = _date_key(source_date, field="source_date")
    if mode not in CANDIDATE_POOL_MODES:
        raise ValueError(f"unsupported DailyWatch20 candidate pool mode: {mode}")
    if mode in {"dc_concept_strict_v1", "dc_concept_composite_strict_v1"}:
        from .daily_watch20_candidate_pool_dc_concept import load_dc_concept_strict_v1

        return load_dc_concept_strict_v1(
            data_root,
            source_date=expected,
            dc_concept_root=dc_concept_root,
            dc_concept_cons_root=dc_concept_cons_root,
            limit_list_root=limit_list_root,
            moneyflow_root=moneyflow_root,
            min_symbols=ths_hot_min_symbols,
            mode=mode,
        )
    if mode == "all_market":
        return DailyWatch20CandidatePool(
            mode=mode,
            source_date=expected,
            frame=pd.DataFrame(columns=cast(Any, ["symbol"])),
            policy_id=candidate_pool_policy_id(mode),
        )
    try:
        min_symbols = int(ths_hot_min_symbols)
    except (TypeError, ValueError) as exc:
        raise ValueError("ths_hot_min_symbols must be an integer") from exc
    if min_symbols < 20:
        raise ValueError("ths_hot_min_symbols must be at least 20")
    try:
        snapshot_min_symbols = int(ths_hot_snapshot_min_symbols)
    except (TypeError, ValueError) as exc:
        raise ValueError("ths_hot_snapshot_min_symbols must be an integer") from exc
    if snapshot_min_symbols < min_symbols:
        raise ValueError("ths_hot_snapshot_min_symbols must be at least ths_hot_min_symbols")
    if mode in {"ths_hot_strict_v2", "ths_hot_strict_v3"} and (
        snapshot_min_symbols > THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS
    ):
        raise ValueError(
            "ths_hot_snapshot_min_symbols must not exceed "
            f"{THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS} in sparse strict modes"
        )
    base = Path(data_root).expanduser().resolve()
    root = (
        Path(ths_hot_root).expanduser().resolve()
        if ths_hot_root is not None
        else _current_asset_root(base, "ths_hot")
    )
    if mode == "ths_hot_strict_v2":
        return _load_ths_hot_strict_v2(
            root,
            source_date=expected,
            min_symbols=min_symbols,
            snapshot_min_symbols=snapshot_min_symbols,
        )
    if mode == "ths_hot_strict_v3":
        return _load_ths_hot_strict_v3(
            root,
            source_date=expected,
            min_symbols=min_symbols,
            snapshot_min_symbols=snapshot_min_symbols,
        )
    return _load_ths_hot_strict(
        root,
        source_date=expected,
        min_symbols=min_symbols,
        snapshot_min_symbols=snapshot_min_symbols,
    )


def restrict_daily_watch20_candidates(
    candidates: pd.DataFrame,
    pool: DailyWatch20CandidatePool,
    *,
    required_symbols: int = 20,
) -> pd.DataFrame:
    """Apply the pool before portfolio selection, with no synthetic fill."""

    if not pool.restricted:
        return candidates.copy()
    if "symbol" not in candidates.columns:
        raise ValueError("DailyWatch20 candidates are missing symbol")
    work = candidates.copy()
    work["symbol"] = cast(pd.Series, work["symbol"]).astype(str)
    restricted = work.merge(pool.frame, on="symbol", how="inner", validate="one_to_one")
    if len(restricted) < required_symbols:
        raise RuntimeError(
            f"strict {pool.mode} pool has too few model-eligible candidates: "
            f"{len(restricted)}, requires at least {required_symbols}; full-market fill is disabled"
        )
    return restricted
