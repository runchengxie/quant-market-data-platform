"""Compatibility facade for the standardized A-share daily schema."""

from market_data_platform.standardize.schema.a_share_daily import (
    LIMIT_COLUMNS,
    PRICE_COLUMNS,
    VALUATION_COLUMNS,
)

__all__ = ["LIMIT_COLUMNS", "PRICE_COLUMNS", "VALUATION_COLUMNS"]
