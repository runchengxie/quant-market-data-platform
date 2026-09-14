"""Compatibility facade for the minute normalization implementation."""

from market_data_platform.standardize.fusion.a_share_minute import normalize as _impl


def __getattr__(name: str):
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))
