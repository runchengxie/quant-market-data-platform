"""Compatibility facade for TuShare A-share daily standardization helpers."""

from typing import Any

from market_data_platform.standardize.tushare import a_share_daily_part02 as _impl


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))
