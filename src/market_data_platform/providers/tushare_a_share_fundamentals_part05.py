"""Stateful TuShare A-share fundamentals ingestion and PIT publication."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_fundamentals_part03 import (
    build_normalized_fundamentals_union,
    validate_normalized_fundamentals,
)
from market_data_platform.providers.tushare_a_share_fundamentals_part04 import (
    _replace_symlink,
    _require_fundamentals_validation,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitAsOfPanel as PitAsOfPanel,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitAsOfView as PitAsOfView,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitFundamentalsEvents as PitFundamentalsEvents,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of as load_pit_fundamentals_as_of,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of_panel as load_pit_fundamentals_as_of_panel,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of_view as load_pit_fundamentals_as_of_view,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_events as load_pit_fundamentals_events,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    validate_pit_fundamentals,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    asset_manifest_payload as _asset_manifest_payload,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    date_token as _date_token,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    file_sha256 as _file_sha256,
)


def _normalized_snapshot_name(artifacts_root: Path, normalized_dirs: Sequence[Path]) -> Path:
    bundle_tokens = [
        _date_token(_asset_manifest_payload(source).get("bundle_available_date"))
        for source in normalized_dirs
    ]
    bundle_token = max(bundle_tokens) if bundle_tokens else ""
    if not bundle_token:
        raise ValueError(
            "Cannot derive a normalized fundamentals snapshot vintage from component manifests."
        )
    family_dir = artifacts_root / "assets" / "tushare" / "a_share" / "normalized_fundamentals"
    return family_dir / f"a_share_all_normalized_fundamentals_{bundle_token}"


def _same_source_lineage(existing: dict[str, Any], normalized_dirs: Sequence[Path]) -> bool:
    expected = {
        _file_sha256(source / "manifest.yml")
        for source in normalized_dirs
        if (source / "manifest.yml").is_file()
    }
    raw = existing.get("source_integrity")
    actual = (
        {
            str(value.get("manifest_sha256"))
            for value in raw.values()
            if isinstance(value, Mapping) and value.get("manifest_sha256")
        }
        if isinstance(raw, Mapping)
        else set()
    )
    return bool(expected) and expected == actual


def publish_fundamentals_assets(
    *,
    artifacts_root: str | Path,
    normalized_dirs: Iterable[str | Path],
    pit_dir: str | Path,
    target_date: str,
) -> dict[str, Any]:
    from market_data_platform.contract import build_current_contract, write_current_contract
    from market_data_platform.paths import (
        candidate_asset_paths,
        current_contract_path,
        dataset_registry_path,
    )
    from market_data_platform.registry import write_combined_dataset_registry

    root = Path(artifacts_root).expanduser().resolve()
    normalized = [Path(path).expanduser().resolve() for path in normalized_dirs]
    if not normalized:
        raise ValueError("At least one --normalized-dir is required.")
    pit = Path(pit_dir).expanduser().resolve()
    expected_target = _date_token(target_date)
    normalized_validation = {
        str(source): validate_normalized_fundamentals(
            asset_dir=source,
            target_date=expected_target,
        )
        for source in normalized
    }
    for source, validation in normalized_validation.items():
        _require_fundamentals_validation(
            validation,
            label=f"Normalized fundamentals {source}",
        )
    pit_validation = validate_pit_fundamentals(
        asset_dir=pit,
        target_date=expected_target,
    )
    _require_fundamentals_validation(pit_validation, label="PIT fundamentals")
    aliases = candidate_asset_paths(root, market="a_share", provider="tushare")
    composite_validation = None
    if len(normalized) == 1:
        normalized_target = normalized[0]
    else:
        composite_dir = _normalized_snapshot_name(root, normalized)
        existing = _asset_manifest_payload(composite_dir)
        if existing.get("status") == "completed" and existing.get("immutable_snapshot") is True:
            if not _same_source_lineage(existing, normalized):
                raise ValueError(
                    f"Existing immutable normalized snapshot lineage changed: {composite_dir}"
                )
        else:
            build_normalized_fundamentals_union(
                normalized_dirs=normalized,
                out_dir=composite_dir,
            )
        composite_validation = validate_normalized_fundamentals(
            asset_dir=composite_dir,
            target_date=expected_target,
        )
        _require_fundamentals_validation(
            composite_validation,
            label="Normalized fundamentals union",
        )
        normalized_target = composite_dir
    _replace_symlink(aliases["normalized_fundamentals"], normalized_target)
    _replace_symlink(aliases["pit_fundamentals"], pit)
    contract = build_current_contract(
        root,
        market="a_share",
        provider="tushare",
        generated_by="marketdata tushare publish-a-share-fundamentals",
        target_date=expected_target,
    )
    contract_path = current_contract_path(root, market="a_share")
    write_current_contract(contract_path, contract)
    contracts = [contract]
    write_combined_dataset_registry(dataset_registry_path(root), contracts)
    return {
        "status": "published",
        "normalized_validation": normalized_validation,
        "composite_validation": composite_validation,
        "pit_validation": pit_validation,
        "normalized_asset": str(normalized_target),
        "current_contract": str(contract_path),
        "dataset_registry": str(dataset_registry_path(root)),
    }


def publish_pit_fundamentals_asset(
    *,
    artifacts_root: str | Path,
    pit_dir: str | Path,
    target_date: str,
) -> dict[str, Any]:
    from market_data_platform.contract import build_current_contract, write_current_contract
    from market_data_platform.paths import (
        candidate_asset_paths,
        current_contract_path,
        dataset_registry_path,
    )
    from market_data_platform.registry import write_combined_dataset_registry

    root = Path(artifacts_root).expanduser().resolve()
    pit = Path(pit_dir).expanduser().resolve()
    expected_target = _date_token(target_date)
    pit_validation = validate_pit_fundamentals(
        asset_dir=pit,
        target_date=expected_target,
    )
    _require_fundamentals_validation(pit_validation, label="PIT fundamentals")
    aliases = candidate_asset_paths(root, market="a_share", provider="tushare")
    normalized_alias = aliases["normalized_fundamentals"]
    if not normalized_alias.exists():
        raise ValueError(
            "PIT fundamentals promotion requires an existing normalized_fundamentals alias."
        )
    normalized_validation = validate_normalized_fundamentals(
        asset_dir=normalized_alias,
        target_date=expected_target,
    )
    _require_fundamentals_validation(normalized_validation, label="Normalized fundamentals")
    _replace_symlink(aliases["pit_fundamentals"], pit)
    contract = build_current_contract(
        root,
        market="a_share",
        provider="tushare",
        generated_by="marketdata tushare publish-a-share-pit-fundamentals",
        target_date=expected_target,
    )
    contract_path = current_contract_path(root, market="a_share")
    write_current_contract(contract_path, contract)
    contracts = [contract]
    write_combined_dataset_registry(dataset_registry_path(root), contracts)
    return {
        "status": "published",
        "normalized_validation": normalized_validation,
        "pit_validation": pit_validation,
        "current_contract": str(contract_path),
        "dataset_registry": str(dataset_registry_path(root)),
    }
