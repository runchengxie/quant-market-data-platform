"""Compatibility facade for minute materialization options."""

from market_data_platform.standardize.materialize.a_share_minute import options as _impl


def __getattr__(name: str):
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))
