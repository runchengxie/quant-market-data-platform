"""Read-only published A-share daily research frames for downstream consumers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pandas as pd

from market_data_platform.published_assets import PublishedAssetContract


@dataclass(frozen=True, slots=True)
class AShareResearchAssets:
    """Published current assets needed by generic A-share daily research."""

    data_root: Path
    current_contract: Path
    daily_clean: Path
    instruments: Path
    daily_as_of: str


def _normalized_as_of(value: object, *, asset_key: str) -> str:
    text = str(value or "").replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"Published asset {asset_key!r} has no valid YYYYMMDD as_of value")
    return text


def resolve_a_share_research_assets(
    data_root: str | Path | None = None,
) -> AShareResearchAssets:
    """Resolve generic daily research inputs through the published current contract."""

    contract = PublishedAssetContract.load_current(data_root, market="a_share")
    daily_clean, instruments = contract.require_assets(("daily_clean", "instruments"))
    return AShareResearchAssets(
        data_root=contract.artifacts_root,
        current_contract=contract.path,
        daily_clean=daily_clean.resolved_path,
        instruments=instruments.resolved_path,
        daily_as_of=_normalized_as_of(daily_clean.as_of, asset_key="daily_clean"),
    )


def load_a_share_research_daily(
    assets: AShareResearchAssets,
    *,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load the owner-published daily research columns from ``daily_clean``."""

    data_dir = assets.daily_clean / "data"
    if not data_dir.is_dir() or not any(data_dir.glob("*.parquet")):
        raise FileNotFoundError(f"Published daily_clean data directory is missing: {data_dir}")

    columns = ["trade_date", "symbol", "close", "turnover_rate", "total_mv"]
    frame = pd.read_parquet(data_dir, columns=columns)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["symbol"] = frame["symbol"].astype(str)
    frame = frame.dropna(subset=["trade_date", "symbol"])
    if end_date is not None:
        cutoff = pd.Timestamp(end_date)
        frame = frame.loc[cast(pd.Series, frame["trade_date"]).le(cutoff)]
    return frame.sort_values(["trade_date", "symbol"], kind="mergesort").reset_index(drop=True)


def load_a_share_research_instruments(assets: AShareResearchAssets) -> pd.DataFrame:
    """Load published instruments and normalize the industry label used by research callers."""

    frame = pd.read_parquet(assets.instruments)
    if "symbol" not in frame.columns:
        raise ValueError("Published instruments asset is missing symbol")
    frame = frame.copy()
    frame["symbol"] = frame["symbol"].astype(str)
    if "industry_name" not in frame.columns and "industry" in frame.columns:
        frame["industry_name"] = frame["industry"]
    return frame.drop_duplicates("symbol", keep="last").reset_index(drop=True)


__all__ = [
    "AShareResearchAssets",
    "load_a_share_research_daily",
    "load_a_share_research_instruments",
    "resolve_a_share_research_assets",
]
