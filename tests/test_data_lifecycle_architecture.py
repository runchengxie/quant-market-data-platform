from __future__ import annotations

import ast
from pathlib import Path

from market_data_platform.providers.a_share_minute_build import (
    MinuteFusionBuildOptions as legacy_minute_build_options,
)
from market_data_platform.providers.a_share_minute_build import (
    build_fused_minute_dataset as legacy_build_fused_minute_dataset,
)
from market_data_platform.providers.a_share_minute_build import (
    validate_fused_minute_dataset as legacy_validate_fused_minute_dataset,
)
from market_data_platform.providers.a_share_minute_fusion import (
    fuse_minute_frames as legacy_fuse_minute_frames,
)
from market_data_platform.providers.tushare_a_share_clean import (
    build_a_share_daily_clean as legacy_build_a_share_daily_clean,
)
from market_data_platform.standardize.fusion.a_share_minute import (
    aggregate_guan_deal_file,
    fuse_minute_frames,
)
from market_data_platform.standardize.materialize.a_share_minute import (
    MinuteFusionBuildOptions,
    build_fused_minute_dataset,
    validate_fused_minute_dataset,
)
from market_data_platform.standardize.tushare.a_share_daily import (
    build_a_share_daily_clean as standardized_build_a_share_daily_clean,
)


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
    return imports


def test_standardize_does_not_depend_on_provider_or_ingest_implementations() -> None:
    root = Path("src/market_data_platform/standardize")
    violations = [
        f"{path}:{line}:{module}"
        for path in sorted(root.rglob("*.py"))
        for line, module in _imported_modules(path)
        if module.startswith(("market_data_platform.providers", "market_data_platform.ingest"))
    ]

    assert violations == []


def test_legacy_daily_clean_build_is_standardize_facade() -> None:
    assert legacy_build_a_share_daily_clean is standardized_build_a_share_daily_clean


def test_legacy_minute_fusion_core_is_standardize_facade() -> None:
    assert legacy_fuse_minute_frames is fuse_minute_frames
    assert callable(aggregate_guan_deal_file)


def test_legacy_minute_build_preserves_standardized_contracts() -> None:
    assert legacy_minute_build_options is MinuteFusionBuildOptions
    assert legacy_validate_fused_minute_dataset is validate_fused_minute_dataset
    assert callable(legacy_build_fused_minute_dataset)
    assert callable(build_fused_minute_dataset)
