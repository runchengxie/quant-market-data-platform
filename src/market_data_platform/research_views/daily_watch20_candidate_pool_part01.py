"""Point-in-time candidate-pool contracts for DailyWatch20.

The market-data package owns exact-date discovery, snapshot integrity and source
lineage. Alpha and application layers consume the resulting immutable view.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .daily_watch20_candidate_pool_v2 import (
    THS_HOT_V2_BATCH_GAP_SECONDS,
    THS_HOT_V2_MAX_COMPONENT_SPAN_SECONDS,
    THS_HOT_V2_MAX_MISSING_RANKS,
    THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS,
    THS_HOT_V2_REQUIRE_RANK_ONE,
    THS_HOT_V2_REQUIRED_TOP_RANKS,
    THSHotV2Snapshot,
)
from .daily_watch20_candidate_pool_v3 import (
    THS_HOT_V3_BATCH_GAP_SECONDS,
    THS_HOT_V3_MAX_COMPONENT_SPAN_SECONDS,
    THS_HOT_V3_MAX_MISSING_RANKS,
    THS_HOT_V3_MAX_SNAPSHOT_SYMBOLS,
    THS_HOT_V3_REQUIRE_RANK_ONE,
    THS_HOT_V3_REQUIRED_TOP_RANKS,
)

CandidatePoolMode = Literal[
    "all_market",
    "ths_hot_strict",
    "ths_hot_strict_v2",
    "ths_hot_strict_v3",
    "dc_concept_strict_v1",
    "dc_concept_composite_strict_v1",
]

CANDIDATE_POOL_MODES: tuple[CandidatePoolMode, ...] = (
    "all_market",
    "ths_hot_strict",
    "ths_hot_strict_v2",
    "ths_hot_strict_v3",
    "dc_concept_strict_v1",
    "dc_concept_composite_strict_v1",
)

THS_HOT_SOURCE = "tushare.ths_hot"

THS_HOT_POOL_POLICY_SCHEMA = "daily_watch20.ths_hot_positive_close.v1"

THS_HOT_POOL_POLICY_SCHEMA_V2 = "daily_watch20.ths_hot_positive_close.v2"

THS_HOT_POOL_POLICY_SCHEMA_V3 = "daily_watch20.ths_hot_positive_close.v3"

ALL_MARKET_POOL_POLICY_ID = "daily_watch20.all_market.v1"

THS_HOT_CLOSE_CUTOFF_MINUTE = 15 * 60

THS_HOT_MAX_SNAPSHOT_FALLBACK_MINUTES = 60


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_pool_policy_id(
    mode: CandidatePoolMode,
    *,
    ths_hot_min_symbols: int = 20,
    ths_hot_snapshot_min_symbols: int = 80,
) -> str:
    """Return the stable policy identity used for idempotency checks."""

    if mode == "all_market":
        return ALL_MARKET_POOL_POLICY_ID
    if mode in {"dc_concept_strict_v1", "dc_concept_composite_strict_v1"}:
        # Import lazily because the DC loader imports the pool dataclass from
        # this module.  Keeping the dispatch here makes the public policy
        # identity available to freshness checks without a circular import.
        from .daily_watch20_candidate_pool_dc_concept import (
            candidate_pool_policy_id_dc_concept,
        )

        return candidate_pool_policy_id_dc_concept(mode, min_symbols=ths_hot_min_symbols)
    if mode not in {"ths_hot_strict", "ths_hot_strict_v2", "ths_hot_strict_v3"}:
        raise ValueError(f"unsupported DailyWatch20 candidate pool mode: {mode}")
    schema = {
        "ths_hot_strict": THS_HOT_POOL_POLICY_SCHEMA,
        "ths_hot_strict_v2": THS_HOT_POOL_POLICY_SCHEMA_V2,
        "ths_hot_strict_v3": THS_HOT_POOL_POLICY_SCHEMA_V3,
    }[mode]
    sparse_constants = {
        "ths_hot_strict_v2": (
            THS_HOT_V2_BATCH_GAP_SECONDS,
            THS_HOT_V2_MAX_COMPONENT_SPAN_SECONDS,
            THS_HOT_V2_MAX_MISSING_RANKS,
            THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS,
            THS_HOT_V2_REQUIRE_RANK_ONE,
            THS_HOT_V2_REQUIRED_TOP_RANKS,
        ),
        "ths_hot_strict_v3": (
            THS_HOT_V3_BATCH_GAP_SECONDS,
            THS_HOT_V3_MAX_COMPONENT_SPAN_SECONDS,
            THS_HOT_V3_MAX_MISSING_RANKS,
            THS_HOT_V3_MAX_SNAPSHOT_SYMBOLS,
            THS_HOT_V3_REQUIRE_RANK_ONE,
            THS_HOT_V3_REQUIRED_TOP_RANKS,
        ),
    }.get(mode)
    sparse_suffix = ""
    if sparse_constants is not None:
        batch_gap, max_span, max_missing, max_symbols, require_rank_one, required_top = (
            sparse_constants
        )
        sparse_suffix = (
            f":batch_gap_seconds={batch_gap}"
            f":max_component_span_seconds={max_span}"
            f":max_missing_ranks={max_missing}"
            f":max_snapshot_symbols={max_symbols}"
            f":require_rank_one={str(require_rank_one).lower()}"
            f":required_top_ranks={required_top}"
        )
    return (
        f"{schema}:"
        f"min_symbols={int(ths_hot_min_symbols)}:"
        f"snapshot_min_symbols={int(ths_hot_snapshot_min_symbols)}:"
        f"close_cutoff_minute={THS_HOT_CLOSE_CUTOFF_MINUTE}:"
        f"max_snapshot_fallback_minutes={THS_HOT_MAX_SNAPSHOT_FALLBACK_MINUTES}"
        f"{sparse_suffix}"
    )


def _date_key(value: object, *, field: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"{field} must be YYYYMMDD")
    try:
        parsed = pd.Timestamp(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid date") from exc
    if pd.isna(parsed):
        raise ValueError(f"{field} must be a valid date")
    return text


@dataclass(frozen=True)
class DailyWatch20CandidatePool:
    """One exact-date universe restriction and its audit metadata."""

    mode: CandidatePoolMode
    source_date: str
    frame: pd.DataFrame
    policy_id: str = ALL_MARKET_POOL_POLICY_ID
    root: Path | None = None
    min_symbols: int = 0
    raw_rows: int = 0
    snapshot_rows: int = 0
    snapshot_minute: str | None = None
    latest_observed_minute: str | None = None
    skipped_incomplete_snapshots: int = 0
    snapshot_time_min: str | None = None
    snapshot_time_max: str | None = None
    duplicate_symbol_rows_removed: int = 0
    out_of_scope_rows_removed: int = 0
    non_positive_rows_removed: int = 0
    snapshot_unique_symbols: int = 0
    snapshot_min_symbols: int = 0
    require_positive_change: bool = False
    files: tuple[tuple[Path, str], ...] = ()
    assembly_path: str | None = None
    deduplicated_snapshot_rows: int = 0
    missing_ranks: tuple[int, ...] = ()
    rank_ties: int = 0
    component_time_start: str | None = None
    component_time_end: str | None = None
    component_span_seconds: int | None = None

    @property
    def restricted(self) -> bool:
        return self.mode in {
            "ths_hot_strict",
            "ths_hot_strict_v2",
            "ths_hot_strict_v3",
            "dc_concept_strict_v1",
            "dc_concept_composite_strict_v1",
        }

    @property
    def symbols(self) -> tuple[str, ...]:
        if self.frame.empty:
            return ()
        return tuple(cast(pd.Series, self.frame["symbol"]).astype(str))

    def receipt_summary(
        self, *, eligible_intersection_symbols: int | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": self.mode,
            "policy_id": self.policy_id,
            "source_date": self.source_date,
            "restricted": self.restricted,
            "fail_closed": self.restricted,
            "eligible_intersection_symbols": eligible_intersection_symbols,
        }
        if not self.restricted:
            payload.update(
                {
                    "source": None,
                    "root": None,
                    "pool_symbols": None,
                    "reason": "full-market research universe",
                }
            )
            return payload
        source = (
            "tushare.dc_concept+dc_concept_cons+limit_list_ths+moneyflow_ths"
            if self.mode
            in {
                "dc_concept_strict_v1",
                "dc_concept_composite_strict_v1",
            }
            else THS_HOT_SOURCE
        )
        payload.update(
            {
                "source": source,
                "root": str(self.root),
                "min_symbols": self.min_symbols,
                "snapshot_min_symbols": self.snapshot_min_symbols,
                "close_cutoff_minute": THS_HOT_CLOSE_CUTOFF_MINUTE,
                "max_snapshot_fallback_minutes": THS_HOT_MAX_SNAPSHOT_FALLBACK_MINUTES,
                "raw_rows": self.raw_rows,
                "snapshot_rows": self.snapshot_rows,
                "snapshot_unique_symbols": self.snapshot_unique_symbols,
                "snapshot_minute": self.snapshot_minute,
                "latest_observed_minute": self.latest_observed_minute,
                "skipped_incomplete_snapshots": self.skipped_incomplete_snapshots,
                "snapshot_time_min": self.snapshot_time_min,
                "snapshot_time_max": self.snapshot_time_max,
                "pool_symbols": len(self.frame),
                "duplicate_symbol_rows_removed": self.duplicate_symbol_rows_removed,
                "out_of_scope_rows_removed": self.out_of_scope_rows_removed,
                "positive_change_only": self.require_positive_change,
                "non_positive_rows_removed": self.non_positive_rows_removed,
                "files": [{"path": str(path), "sha256": digest} for path, digest in self.files],
            }
        )
        if self.mode in {"ths_hot_strict_v2", "ths_hot_strict_v3"}:
            if self.mode == "ths_hot_strict_v2":
                policy_constants = (
                    THS_HOT_V2_BATCH_GAP_SECONDS,
                    THS_HOT_V2_MAX_COMPONENT_SPAN_SECONDS,
                    THS_HOT_V2_MAX_MISSING_RANKS,
                    THS_HOT_V2_MAX_SNAPSHOT_SYMBOLS,
                    THS_HOT_V2_REQUIRE_RANK_ONE,
                    THS_HOT_V2_REQUIRED_TOP_RANKS,
                )
            else:
                policy_constants = (
                    THS_HOT_V3_BATCH_GAP_SECONDS,
                    THS_HOT_V3_MAX_COMPONENT_SPAN_SECONDS,
                    THS_HOT_V3_MAX_MISSING_RANKS,
                    THS_HOT_V3_MAX_SNAPSHOT_SYMBOLS,
                    THS_HOT_V3_REQUIRE_RANK_ONE,
                    THS_HOT_V3_REQUIRED_TOP_RANKS,
                )
            batch_gap, max_span, max_missing, max_symbols, require_rank_one, required_top = (
                policy_constants
            )
            payload.update(
                {
                    "assembly_path": self.assembly_path,
                    "deduplicated_snapshot_rows": self.deduplicated_snapshot_rows,
                    "missing_ranks": list(self.missing_ranks),
                    "rank_coverage_status": ("complete" if not self.missing_ranks else "degraded"),
                    "rank_ties": self.rank_ties,
                    "component_time_start": self.component_time_start,
                    "component_time_end": self.component_time_end,
                    "component_span_seconds": self.component_span_seconds,
                    "batch_gap_seconds": batch_gap,
                    "max_component_span_seconds": max_span,
                    "max_missing_ranks": max_missing,
                    "max_snapshot_symbols": max_symbols,
                    "require_rank_one": require_rank_one,
                    "required_top_ranks": required_top,
                }
            )
        return payload


def _read_partition(files: tuple[Path, ...]) -> pd.DataFrame:
    frames = [pq.ParquetFile(path).read().to_pandas() for path in files]
    return pd.concat(frames, ignore_index=True, sort=False)


@dataclass(frozen=True)
class _THSHotSnapshot:
    frame: pd.DataFrame
    raw_rows: int
    snapshot_rows: int
    snapshot_minute: pd.Timestamp
    latest_observed_minute: pd.Timestamp
    skipped_incomplete_snapshots: int
    snapshot_time_min: str
    snapshot_time_max: str
    out_of_scope_rows_removed: int
    snapshot_unique_symbols: int


def _load_validated_partition(
    root: Path,
    source_date: str,
) -> tuple[pd.DataFrame, tuple[Path, ...], str, pd.Series]:
    partition = root / "data" / f"trade_date={source_date}"
    files = tuple(sorted(partition.rglob("*.parquet"))) if partition.is_dir() else ()
    if not files:
        raise FileNotFoundError(
            f"strict THS-hot candidate pool has no exact-date parquet partition: {partition}"
        )
    raw = _read_partition(files)
    required = {
        "data_type",
        "pct_change",
        "platform_market",
        "rank",
        "rank_time",
        "trade_date",
    }
    missing = sorted(required - set(raw.columns))
    symbol_col = (
        "symbol" if "symbol" in raw.columns else "ts_code" if "ts_code" in raw.columns else None
    )
    if symbol_col is None:
        missing.append("symbol|ts_code")
    if missing:
        raise RuntimeError(f"strict THS-hot candidate pool is missing columns: {missing}")
    assert symbol_col is not None
    if raw.empty:
        raise RuntimeError(f"strict THS-hot candidate pool is empty for {source_date}")

    row_dates = cast(pd.Series, raw["trade_date"]).astype(str).str.replace("-", "", regex=False)
    if set(row_dates) != {source_date}:
        raise RuntimeError("strict THS-hot candidate pool contains non-exact-date rows")
    if set(cast(pd.Series, raw["data_type"]).astype(str)) != {"热股"}:
        raise RuntimeError("strict THS-hot candidate pool data_type must be 热股")
    if set(cast(pd.Series, raw["platform_market"]).astype(str)) != {"a_share"}:
        raise RuntimeError("strict THS-hot candidate pool platform_market must be a_share")
    rank_times = cast(pd.Series, pd.to_datetime(raw["rank_time"], errors="coerce"))
    if rank_times.isna().any():
        raise RuntimeError("strict THS-hot candidate pool contains invalid rank_time values")
    if set(rank_times.dt.strftime("%Y%m%d")) != {source_date}:
        raise RuntimeError("strict THS-hot rank_time does not match source_date")
    return raw, files, symbol_col, rank_times


def _select_latest_complete_snapshot(
    raw: pd.DataFrame,
    *,
    rank_times: pd.Series,
    symbol_col: str,
    snapshot_min_symbols: int,
) -> _THSHotSnapshot:
    snapshot_minutes = rank_times.dt.floor("min")
    raw["_candidate_symbol"] = (
        cast(pd.Series, raw[symbol_col]).astype("string").str.strip().str.upper()
    )
    raw["_snapshot_minute"] = snapshot_minutes
    in_scope_raw = cast(pd.Series, raw["_candidate_symbol"]).str.fullmatch(
        r"\d{6}\.(?:SH|SZ)", na=False
    )
    snapshot_symbol_counts = (
        raw.loc[in_scope_raw].groupby("_snapshot_minute", sort=True)["_candidate_symbol"].nunique()
    )
    complete_minutes = snapshot_symbol_counts.loc[
        snapshot_symbol_counts.ge(snapshot_min_symbols)
    ].index
    if len(complete_minutes) == 0:
        largest = int(snapshot_symbol_counts.max()) if not snapshot_symbol_counts.empty else 0
        raise RuntimeError(
            "strict THS-hot partition has no complete snapshot: "
            f"largest has {largest} symbols, requires at least {snapshot_min_symbols}"
        )
    latest_observed_minute = cast(pd.Timestamp, snapshot_minutes.max())
    latest_minute = cast(pd.Timestamp, complete_minutes.max())
    minute_of_day = latest_minute.hour * 60 + latest_minute.minute
    if minute_of_day < THS_HOT_CLOSE_CUTOFF_MINUTE:
        raise RuntimeError(
            "strict THS-hot partition has no close snapshot at or after "
            f"{THS_HOT_CLOSE_CUTOFF_MINUTE // 60:02d}:"
            f"{THS_HOT_CLOSE_CUTOFF_MINUTE % 60:02d}"
        )
    fallback_minutes = int((latest_observed_minute - latest_minute).total_seconds() // 60)
    if fallback_minutes > THS_HOT_MAX_SNAPSHOT_FALLBACK_MINUTES:
        raise RuntimeError(
            "strict THS-hot latest complete snapshot is too far behind the latest observation: "
            f"{fallback_minutes} minutes"
        )

    snapshot_mask = snapshot_minutes.eq(latest_minute)
    snapshot = cast(pd.DataFrame, raw.loc[snapshot_mask]).copy()
    snapshot["_rank_time"] = rank_times.loc[snapshot_mask].to_numpy()
    ranks = cast(pd.Series, pd.to_numeric(snapshot["rank"], errors="coerce"))
    if ranks.isna().any() or bool(ranks.le(0).any()) or bool(ranks.mod(1).ne(0).any()):
        raise RuntimeError("strict THS-hot latest snapshot contains invalid ranks")
    rank_values = set(ranks.astype(int))
    if len(rank_values) != len(snapshot) or rank_values != set(range(1, len(snapshot) + 1)):
        raise RuntimeError("strict THS-hot latest snapshot ranks must be unique and continuous")
    snapshot["ths_hot_rank"] = ranks.astype(int)
    snapshot["symbol"] = cast(pd.Series, snapshot["_candidate_symbol"])
    snapshot = snapshot.sort_values(["ths_hot_rank", "_rank_time"], kind="mergesort").reset_index(
        drop=True
    )
    snapshot_rows = len(snapshot)
    snapshot_time_min = cast(pd.Timestamp, snapshot["_rank_time"].min()).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    snapshot_time_max = cast(pd.Timestamp, snapshot["_rank_time"].max()).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    in_scope = cast(pd.Series, snapshot["symbol"]).str.fullmatch(r"\d{6}\.(?:SH|SZ)", na=False)
    out_of_scope = int((~in_scope).sum())
    snapshot = cast(pd.DataFrame, snapshot.loc[in_scope]).copy()
    if snapshot["symbol"].duplicated(keep="first").any():
        raise RuntimeError("strict THS-hot latest snapshot contains duplicate symbols")
    snapshot_unique_symbols = len(snapshot)
    if snapshot_unique_symbols < snapshot_min_symbols:
        raise RuntimeError(
            "strict THS-hot latest snapshot is incomplete after validation: "
            f"{snapshot_unique_symbols} symbols, requires at least {snapshot_min_symbols}"
        )
    return _THSHotSnapshot(
        frame=snapshot,
        raw_rows=len(raw),
        snapshot_rows=snapshot_rows,
        snapshot_minute=latest_minute,
        latest_observed_minute=latest_observed_minute,
        skipped_incomplete_snapshots=int((snapshot_symbol_counts.index > latest_minute).sum()),
        snapshot_time_min=snapshot_time_min,
        snapshot_time_max=snapshot_time_max,
        out_of_scope_rows_removed=out_of_scope,
        snapshot_unique_symbols=snapshot_unique_symbols,
    )


def _positive_snapshot_pool(
    snapshot: pd.DataFrame,
    *,
    min_symbols: int,
) -> tuple[pd.DataFrame, int]:
    pct_change = cast(pd.Series, pd.to_numeric(snapshot["pct_change"], errors="coerce"))
    finite = pd.Series(np.isfinite(pct_change.to_numpy(dtype=float)), index=pct_change.index)
    positive = pct_change.gt(0).fillna(False) & finite
    non_positive_rows = int((~positive).sum())
    positive_snapshot = cast(pd.DataFrame, snapshot.loc[positive]).copy()
    positive_snapshot["ths_hot_pct_change"] = pct_change.loc[positive].astype(float)
    if len(positive_snapshot) < min_symbols:
        raise RuntimeError(
            "strict THS-hot latest snapshot is too small after validation: "
            f"{len(positive_snapshot)} symbols, requires at least {min_symbols}"
        )
    pool = positive_snapshot[["symbol", "ths_hot_rank", "ths_hot_pct_change"]].copy()
    pool["ths_hot_rank_time"] = positive_snapshot["_rank_time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return pool, non_positive_rows


def _v2_snapshot_from_v1(snapshot: _THSHotSnapshot) -> THSHotV2Snapshot:
    start = pd.Timestamp(snapshot.snapshot_time_min)
    end = pd.Timestamp(snapshot.snapshot_time_max)
    rank_values = set(cast(pd.Series, snapshot.frame["ths_hot_rank"]).astype(int))
    missing_ranks = tuple(sorted(set(range(1, max(rank_values) + 1)) - rank_values))
    return THSHotV2Snapshot(
        frame=snapshot.frame,
        assembly_path="v1_fast_path",
        raw_rows=snapshot.raw_rows,
        snapshot_rows=snapshot.snapshot_rows,
        snapshot_minute=snapshot.snapshot_minute,
        latest_observed_minute=snapshot.latest_observed_minute,
        skipped_incomplete_snapshots=snapshot.skipped_incomplete_snapshots,
        snapshot_time_min=snapshot.snapshot_time_min,
        snapshot_time_max=snapshot.snapshot_time_max,
        duplicate_symbol_rows_removed=0,
        out_of_scope_rows_removed=snapshot.out_of_scope_rows_removed,
        snapshot_unique_symbols=snapshot.snapshot_unique_symbols,
        missing_ranks=missing_ranks,
        rank_ties=0,
        component_time_start=snapshot.snapshot_time_min,
        component_time_end=snapshot.snapshot_time_max,
        component_span_seconds=int((end - start).total_seconds()),
    )


def _load_ths_hot_strict(
    root: Path,
    *,
    source_date: str,
    min_symbols: int,
    snapshot_min_symbols: int,
) -> DailyWatch20CandidatePool:
    raw, files, symbol_col, rank_times = _load_validated_partition(root, source_date)
    snapshot = _select_latest_complete_snapshot(
        raw,
        rank_times=rank_times,
        symbol_col=symbol_col,
        snapshot_min_symbols=snapshot_min_symbols,
    )
    pool, non_positive_rows = _positive_snapshot_pool(
        snapshot.frame,
        min_symbols=min_symbols,
    )
    file_receipts = tuple((path, _sha256_file(path)) for path in files)
    return DailyWatch20CandidatePool(
        mode="ths_hot_strict",
        source_date=source_date,
        frame=pool.reset_index(drop=True),
        policy_id=candidate_pool_policy_id(
            "ths_hot_strict",
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
        duplicate_symbol_rows_removed=0,
        out_of_scope_rows_removed=snapshot.out_of_scope_rows_removed,
        non_positive_rows_removed=non_positive_rows,
        snapshot_unique_symbols=snapshot.snapshot_unique_symbols,
        snapshot_min_symbols=snapshot_min_symbols,
        require_positive_change=True,
        files=file_receipts,
    )
