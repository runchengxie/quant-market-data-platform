"""Thin public entrypoint for data provider functionality."""

from __future__ import annotations

from . import data_providers_client as _client
from .data_providers_public_api import fetch_daily, fetch_fundamentals, load_basic

# Backward-compatible private and implementation exports remain available via core.
__all__ = [name for name in vars(_client) if not name.startswith("__")]
for _name in __all__:
    globals()[_name] = getattr(_client, _name)
