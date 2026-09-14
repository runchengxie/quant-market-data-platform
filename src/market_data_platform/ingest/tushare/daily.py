"""Stable TuShare A-share raw-ingest entry points.

The provider runtime is still implemented in ``market_data_platform.providers`` during the
staged migration. Consumers should use this module so the runtime can move without another
public import-path change.
"""

from market_data_platform.providers.tushare_a_share import (
    mirror_a_share_adj_factor,
    mirror_a_share_daily,
    mirror_a_share_daily_basic,
    mirror_a_share_limit_status,
)

__all__ = [
    "mirror_a_share_adj_factor",
    "mirror_a_share_daily",
    "mirror_a_share_daily_basic",
    "mirror_a_share_limit_status",
]
