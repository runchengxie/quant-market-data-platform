from __future__ import annotations

from quant_market_data_platform.research_views.daily_watch20_runtime_preflight import (
    require_minute_cache_rollover_support,
)


def test_installed_minute_cache_dependency_supports_rolling_window() -> None:
    require_minute_cache_rollover_support()
