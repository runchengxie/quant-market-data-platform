"""Check that the minute-cache rolling-window contract is available."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from market_data_platform.research_views.daily_watch20_minute_source import (
    MinuteSourceCatalog,
    minute_cache_delta,
)


def require_minute_cache_rollover_support() -> None:
    """Verify that an out-of-window cached head is removed incrementally."""

    catalog = cast(
        MinuteSourceCatalog,
        SimpleNamespace(
            start_date="20230720",
            end_date="20260724",
            partitions={
                "20230720": SimpleNamespace(fingerprint="head-current"),
                "20260724": SimpleNamespace(fingerprint="tail-current"),
            },
        ),
    )
    cached = {
        "20230719": {"fingerprint": "rolled-out-head"},
        "20230720": {"fingerprint": "head-current"},
    }

    delta = minute_cache_delta(catalog, cached)
    if delta.removed_dates != frozenset({"20230719"}):
        raise RuntimeError(
            "installed market-data-platform lacks the DailyWatch20 rolling-window cache fix"
        )
    if delta.changed_dates != frozenset({"20260724"}):
        raise RuntimeError("minute-cache rollover preflight returned an unexpected tail delta")


def main() -> int:
    require_minute_cache_rollover_support()
    print("[daily-watch20] minute-cache rollover dependency preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
