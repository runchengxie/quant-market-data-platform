from __future__ import annotations

from market_data_platform.ingest.tushare.minute import (
    MinsMirrorOptions as IngestMinsMirrorOptions,
)
from market_data_platform.ingest.tushare.minute import (
    mirror_minute_bars as ingest_mirror_minute_bars,
)
from market_data_platform.providers.a_share_minute_build import (
    build_fused_minute_dataset as legacy_build_fused_minute_dataset,
)
from market_data_platform.providers.a_share_minute_build import (
    validate_fused_minute_dataset as legacy_validate_fused_minute_dataset,
)
from market_data_platform.providers.a_share_minute_fusion import (
    aggregate_guan_deal_file as legacy_aggregate_guan_deal_file,
)
from market_data_platform.providers.a_share_minute_fusion import (
    fuse_minute_frames as legacy_fuse_minute_frames,
)
from market_data_platform.providers.tushare_a_share_mins import (
    MinsMirrorOptions as LegacyMinsMirrorOptions,
)
from market_data_platform.providers.tushare_a_share_mins import (
    mirror_minute_bars as legacy_mirror_minute_bars,
)
from market_data_platform.standardize.fusion.a_share_minute import (
    fuse_minute_frames,
)
from market_data_platform.standardize.materialize.a_share_minute import (
    validate_fused_minute_dataset,
)


def test_legacy_fusion_exports_are_new_standardize_implementations() -> None:
    assert legacy_fuse_minute_frames is fuse_minute_frames
    assert legacy_aggregate_guan_deal_file.__module__.startswith("market_data_platform.providers")


def test_legacy_build_exports_are_new_materialize_implementations() -> None:
    assert legacy_build_fused_minute_dataset.__module__.startswith("market_data_platform.providers")
    assert legacy_validate_fused_minute_dataset is validate_fused_minute_dataset


def test_legacy_minute_ingest_exports_are_new_ingest_implementations() -> None:
    assert legacy_mirror_minute_bars is ingest_mirror_minute_bars
    assert LegacyMinsMirrorOptions is IngestMinsMirrorOptions
