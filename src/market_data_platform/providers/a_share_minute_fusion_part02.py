"""Compatibility facade for Guan deal aggregation helpers."""

from market_data_platform.standardize.fusion.a_share_minute import guan_deals as _impl


def __getattr__(name: str):
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))
