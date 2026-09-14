"""Backward-compatible facade for the canonical minute fusion API."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pyarrow.parquet as pq

from market_data_platform.standardize.fusion import a_share_minute as _impl
from market_data_platform.standardize.fusion.a_share_minute import *  # noqa: F403
from market_data_platform.standardize.fusion.a_share_minute import fusion as _fusion_impl


def _try_import_polars() -> ModuleType | None:
    try:
        return importlib.import_module("polars")
    except ModuleNotFoundError as exc:
        if exc.name == "polars":
            return None
        raise


def aggregate_guan_deal_file(*args, **kwargs):
    """Keep the historical monkeypatch seam while delegating to standardize."""
    fusion_impl = cast(Any, _fusion_impl)
    original = fusion_impl._try_import_polars
    fusion_impl._try_import_polars = _try_import_polars
    try:
        return _fusion_impl.aggregate_guan_deal_file(*args, **kwargs)
    finally:
        fusion_impl._try_import_polars = original
