"""Backward-compatible facade for minute dataset materialization."""

from __future__ import annotations

from typing import Any, cast

from market_data_platform.standardize.materialize.a_share_minute import (
    MinuteFusionBuildOptions,
    validate_fused_minute_dataset,
)
from market_data_platform.standardize.materialize.a_share_minute import (
    build_fused_minute_dataset as _build_fused_minute_dataset,
)
from market_data_platform.standardize.materialize.a_share_minute.options import (
    DEFAULT_FUSED_MINUTE_MANIFEST_SUBPATH,
    DEFAULT_FUSED_MINUTE_SUBDIR,
)

aggregate_guan_deal_file = __import__(
    "market_data_platform.providers.a_share_minute_fusion",
    fromlist=["aggregate_guan_deal_file"],
).aggregate_guan_deal_file


def build_fused_minute_dataset(options):
    """Delegate while preserving the historical aggregate monkeypatch seam."""
    validation = __import__(
        "market_data_platform.standardize.materialize.a_share_minute.validation_adapter",
        fromlist=["aggregate_guan_deal_file"],
    )
    validation_impl = cast(Any, validation)
    original = validation_impl.aggregate_guan_deal_file
    validation_impl.aggregate_guan_deal_file = aggregate_guan_deal_file
    try:
        return _build_fused_minute_dataset(options)
    finally:
        validation_impl.aggregate_guan_deal_file = original


__all__ = [
    "DEFAULT_FUSED_MINUTE_MANIFEST_SUBPATH",
    "DEFAULT_FUSED_MINUTE_SUBDIR",
    "MinuteFusionBuildOptions",
    "aggregate_guan_deal_file",
    "build_fused_minute_dataset",
    "validate_fused_minute_dataset",
]
