"""Public data-provider API.

This module intentionally exports only the public callable entrypoints and keeps
compatibility surface explicit at a dedicated boundary.
"""

from __future__ import annotations

from .data_providers_client import fetch_daily, fetch_fundamentals, load_basic

__all__ = ["fetch_daily", "load_basic", "fetch_fundamentals"]
