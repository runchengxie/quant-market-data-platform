"""TuShare A-share daily raw-to-canonical standardization entry point."""

from market_data_platform.standardize.tushare.a_share_daily_part02 import (
    build_a_share_daily_clean as build_a_share_daily_clean,
)

__all__ = ["build_a_share_daily_clean"]
