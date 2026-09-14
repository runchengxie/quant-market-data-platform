"""Materialize the canonical A-share one-minute dataset."""

from .build import build_fused_minute_dataset, validate_fused_minute_dataset
from .options import MinuteFusionBuildOptions

__all__ = [
    "MinuteFusionBuildOptions",
    "build_fused_minute_dataset",
    "validate_fused_minute_dataset",
]
