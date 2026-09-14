"""Point-in-time DC concept candidate pool for DailyWatch20 research."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from market_data_platform.contract import load_current_contract

from .daily_watch20_candidate_pool_part01 import (
    DailyWatch20CandidatePool,
    _date_key,
    _sha256_file,
)

DC_CONCEPT_STRICT_V1 = "dc_concept_strict_v1"
DC_CONCEPT_COMPOSITE_STRICT_V1 = "dc_concept_composite_strict_v1"
DC_CONCEPT_SOURCE = "tushare.dc_concept+dc_concept_cons+limit_list_ths+moneyflow_ths"
DC_CONCEPT_POLICY_SCHEMA_V1 = "daily_watch20.dc_concept_positive_strength.v1"


def _current_asset_root(base: Path, dataset: str) -> Path:
    contract = base.parents[2] / "metadata" / "current_assets" / "a_share_current.json"
    payload = load_current_contract(contract)
    if payload is None:
        raise FileNotFoundError(f"A-share current contract not found: {contract}")
    entry = payload.get("assets", {}).get(dataset)
    if not isinstance(entry, dict):
        raise ValueError(f"A-share current contract is missing asset: {dataset}")
    if entry.get("availability") != "available":
        reason = str(entry.get("availability_reason") or "asset is unavailable")
        raise RuntimeError(f"A-share current asset is unavailable: {dataset}: {reason}")
    value = entry.get("resolved_path") or entry.get("alias_path")
    if not value:
        raise ValueError(f"A-share current contract has no path for asset: {dataset}")
    return Path(str(value)).expanduser().resolve()


def candidate_pool_policy_id_dc_concept(
    mode: str = DC_CONCEPT_STRICT_V1, *, min_symbols: int = 20
) -> str:
    if mode not in {DC_CONCEPT_STRICT_V1, DC_CONCEPT_COMPOSITE_STRICT_V1}:
        raise ValueError(f"unsupported DC concept candidate pool mode: {mode}")
    policy = (
        "daily_watch20.dc_concept_composite_strength.v1"
        if mode == DC_CONCEPT_COMPOSITE_STRICT_V1
        else DC_CONCEPT_POLICY_SCHEMA_V1
    )
    return f"{policy}:min_symbols={int(min_symbols)}:ranked_stock_strength=v1"


def _partition(root: Path, source_date: str) -> tuple[pd.DataFrame, tuple[Path, ...]]:
    directory = root / "data" / f"trade_date={source_date}"
    files = tuple(sorted(directory.glob("*.parquet")))
    if not files:
        raise FileNotFoundError(f"dc concept exact-date partition is missing: {directory}")
    frame = pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
    if frame.empty:
        raise RuntimeError(f"dc concept exact-date partition is empty: {directory}")
    if "trade_date" not in frame:
        raise ValueError(f"dc concept partition is missing trade_date: {directory}")
    actual_dates = set(frame["trade_date"].astype(str).str.replace("-", ""))
    if actual_dates != {source_date}:
        raise ValueError(f"dc concept partition contains an unexpected trade date: {directory}")
    return frame, files


def load_dc_concept_strict_v1(  # noqa: PLR0913
    data_root: str | Path,
    *,
    source_date: str,
    dc_concept_root: str | Path | None = None,
    dc_concept_cons_root: str | Path | None = None,
    limit_list_root: str | Path | None = None,
    moneyflow_root: str | Path | None = None,
    min_symbols: int = 20,
    mode: str = DC_CONCEPT_STRICT_V1,
) -> DailyWatch20CandidatePool:
    """Build a deterministic research-only pool from exact-date DC assets."""
    expected = _date_key(source_date, field="source_date")
    if mode not in {DC_CONCEPT_STRICT_V1, DC_CONCEPT_COMPOSITE_STRICT_V1}:
        raise ValueError(f"unsupported DC concept candidate pool mode: {mode}")
    if min_symbols < 20:
        raise ValueError("dc_concept min_symbols must be at least 20")
    base = Path(data_root).expanduser().resolve() / "assets" / "tushare" / "a_share"
    roots = {
        "concept": Path(dc_concept_root).expanduser().resolve()
        if dc_concept_root
        else _current_asset_root(base, "dc_concept"),
        "members": Path(dc_concept_cons_root).expanduser().resolve()
        if dc_concept_cons_root
        else _current_asset_root(base, "dc_concept_cons"),
        "limits": Path(limit_list_root).expanduser().resolve()
        if limit_list_root
        else _current_asset_root(base, "limit_list_ths"),
        "moneyflow": Path(moneyflow_root).expanduser().resolve()
        if moneyflow_root
        else _current_asset_root(base, "moneyflow_ths"),
    }
    concept, concept_files = _partition(roots["concept"], expected)
    members, member_files = _partition(roots["members"], expected)
    limits, limit_files = _partition(roots["limits"], expected)
    moneyflow, moneyflow_files = _partition(roots["moneyflow"], expected)
    required = {
        "concept": {"theme_code", "name", "pct_change", "main_change", "hot", "z_t_num"},
        "members": {"theme_code", "ts_code"},
        "limits": {"ts_code", "limit_type"},
        "moneyflow": {"ts_code", "net_amount"},
    }
    for label, columns in required.items():
        frame = {
            "concept": concept,
            "members": members,
            "limits": limits,
            "moneyflow": moneyflow,
        }[label]
        missing = sorted(columns - set(frame.columns))
        if missing:
            raise ValueError(f"dc concept {label} is missing columns: {missing}")

    concept = concept.copy()
    for column in ("pct_change", "main_change", "hot", "z_t_num"):
        concept[column] = pd.to_numeric(concept[column], errors="coerce").fillna(0.0)
    concept["concept_score"] = (
        concept["pct_change"].clip(lower=0)
        + concept["main_change"].clip(lower=0) / 1e8
        + concept["z_t_num"].clip(lower=0) * 0.5
        + concept["hot"].clip(lower=0) / 10000
    )
    concept = concept[concept["concept_score"].gt(0)]
    if concept.empty:
        raise RuntimeError(f"dc concept has no positive-strength themes for {expected}")

    members = members[["theme_code", "ts_code"]].drop_duplicates().copy()
    members["ts_code"] = members["ts_code"].astype(str).str.strip().str.upper()
    stock = members.merge(concept[["theme_code", "concept_score"]], on="theme_code", how="inner")
    stock = stock.groupby("ts_code", as_index=False)["concept_score"].max()
    limits = limits.copy()
    limits["ts_code"] = limits["ts_code"].astype(str).str.strip().str.upper()
    limit_counts = (
        limits[limits["limit_type"].astype(str).str.contains("涨停", na=False)]
        .groupby("ts_code")
        .size()
    )
    moneyflow = moneyflow.copy()
    moneyflow["ts_code"] = moneyflow["ts_code"].astype(str).str.strip().str.upper()
    moneyflow["net_amount"] = pd.to_numeric(moneyflow["net_amount"], errors="coerce").fillna(0.0)
    flow = moneyflow.groupby("ts_code")["net_amount"].sum()
    stock["limit_up_count"] = stock["ts_code"].map(limit_counts).fillna(0)
    stock["moneyflow_net_amount"] = stock["ts_code"].map(flow).fillna(0.0)
    stock["dc_concept_score"] = (
        stock["concept_score"]
        + stock["limit_up_count"] * 0.5
        + stock["moneyflow_net_amount"].clip(lower=0) / 1e5
    )
    stock = stock[stock["dc_concept_score"].map(math.isfinite) & stock["dc_concept_score"].gt(0)]
    stock = stock.sort_values(["dc_concept_score", "ts_code"], ascending=[False, True]).reset_index(
        drop=True
    )
    if len(stock) < min_symbols:
        raise RuntimeError(
            f"dc concept strict pool too small for {expected}: {len(stock)} < {min_symbols}"
        )
    stock["symbol"] = stock["ts_code"]
    stock["dc_concept_rank"] = stock.index + 1
    files = (*concept_files, *member_files, *limit_files, *moneyflow_files)
    return DailyWatch20CandidatePool(
        mode=mode,
        source_date=expected,
        frame=stock[
            [
                "symbol",
                "dc_concept_rank",
                "dc_concept_score",
                "limit_up_count",
                "moneyflow_net_amount",
            ]
        ].head(max(min_symbols, 100)),
        policy_id=candidate_pool_policy_id_dc_concept(mode, min_symbols=min_symbols),
        root=roots["concept"],
        min_symbols=min_symbols,
        raw_rows=sum(len(frame) for frame in (concept, members, limits, moneyflow)),
        snapshot_rows=len(stock),
        snapshot_unique_symbols=len(stock),
        snapshot_min_symbols=min_symbols,
        require_positive_change=False,
        files=tuple((path, _sha256_file(path)) for path in files),
        assembly_path="dc_concept_strength_with_limit_and_moneyflow",
        deduplicated_snapshot_rows=len(stock),
    )
