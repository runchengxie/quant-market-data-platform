"""Provider valuation overlay data-shaping helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd


def _string_set(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        text = value.strip()
        return {text} if text else set()
    if not isinstance(value, Iterable):
        text = str(value).strip()
        return {text} if text else set()
    return {str(item).strip() for item in value if str(item).strip()}


def select_daily_clean_overlay_columns(
    panel_df: pd.DataFrame,
    provider_overlay_cfg: Mapping[str, Any],
    fundamentals_mcap_col: str,
) -> pd.DataFrame:
    """Select daily valuation fields already present in the research panel."""
    configured_features = _string_set(provider_overlay_cfg.get("features"))
    configured_fields = _string_set(provider_overlay_cfg.get("fields"))
    keep = (
        configured_features
        | configured_fields
        | {
            "market_cap",
            "pe_ttm",
            "pb",
            fundamentals_mcap_col,
        }
    )
    keep_columns = [
        "trade_date",
        "symbol",
        *sorted(column for column in keep if column in panel_df.columns),
    ]
    return panel_df.loc[:, list(dict.fromkeys(keep_columns))].copy()


__all__ = ["select_daily_clean_overlay_columns"]
