"""Historical industry-label expansion helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from .symbols import DEFAULT_SYMBOL_PRIORITY, canonicalize_symbol_columns


def _rename_configured_columns(
    frame: pd.DataFrame,
    column_map: Mapping[str, Any] | None,
) -> pd.DataFrame:
    column_map = column_map if isinstance(column_map, Mapping) else {}
    if not column_map:
        return frame
    rename_map = {
        str(source): str(standard)
        for standard, source in column_map.items()
        if source in frame.columns and standard not in frame.columns
    }
    return frame.rename(columns=rename_map) if rename_map else frame


def expand_effective_industry_to_panel_dates(
    industry_df: pd.DataFrame,
    *,
    panel_df: pd.DataFrame,
    industry_cfg: Mapping[str, Any],
) -> pd.DataFrame:
    """Expand effective-date industry labels onto the panel's trading dates."""
    work = _rename_configured_columns(industry_df.copy(), industry_cfg.get("column_map"))
    if "trade_date" in work.columns or "effective_date" not in work.columns:
        return pd.DataFrame()
    work = canonicalize_symbol_columns(
        work,
        context="Industry data",
        priority=DEFAULT_SYMBOL_PRIORITY,
    )
    if "symbol" not in work.columns:
        return pd.DataFrame()
    work["effective_date"] = pd.to_datetime(work["effective_date"], errors="coerce")
    work = work.dropna(subset=["effective_date", "symbol"]).copy()
    if work.empty:
        return pd.DataFrame()
    if "end_date" in work.columns:
        work["end_date"] = pd.to_datetime(work["end_date"], errors="coerce")
    else:
        work["end_date"] = pd.NaT
    work["symbol"] = work["symbol"].astype(str).str.strip()
    work = work.sort_values(["symbol", "effective_date"]).drop_duplicates(
        subset=["symbol", "effective_date"],
        keep="last",
    )

    panel_keys = panel_df.loc[:, ["trade_date", "symbol"]].drop_duplicates().copy()
    panel_keys["trade_date"] = pd.to_datetime(panel_keys["trade_date"], errors="coerce")
    panel_keys = panel_keys.dropna(subset=["trade_date", "symbol"])
    expanded_frames: list[pd.DataFrame] = []
    for symbol, left in panel_keys.groupby("symbol", sort=False):
        right = work[work["symbol"] == str(symbol)].copy()
        if right.empty:
            continue
        left_sorted = left.sort_values("trade_date")
        right_sorted = right.sort_values("effective_date")
        merged = pd.merge_asof(
            left_sorted,
            right_sorted,
            left_on="trade_date",
            right_on="effective_date",
            direction="backward",
            suffixes=("", "_industry"),
        )
        if "symbol_industry" in merged.columns:
            merged = merged.drop(columns=["symbol_industry"])
        end_date = pd.to_datetime(merged.get("end_date"), errors="coerce")
        merged = merged[end_date.isna() | (merged["trade_date"] <= end_date)].copy()
        if not merged.empty:
            expanded_frames.append(merged)
    if not expanded_frames:
        return pd.DataFrame()
    expanded = pd.concat(expanded_frames, ignore_index=True)
    expanded["trade_date"] = pd.to_datetime(expanded["trade_date"], errors="coerce").dt.normalize()
    return expanded.sort_values(["symbol", "trade_date"]).reset_index(drop=True)


__all__ = ["expand_effective_industry_to_panel_dates"]
