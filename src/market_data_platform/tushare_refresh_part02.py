from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from market_data_platform.contract import (
    build_current_contract,
    write_current_contract,
)
from market_data_platform.contract_health import (
    ContractInspectionOptions,
    current_contract_health_exit_code,
    inspect_current_contract,
    write_current_contract_health_report,
)
from market_data_platform.paths import (
    current_contract_path,
    dataset_registry_path,
    resolve_artifacts_root,
)
from market_data_platform.registry import write_combined_dataset_registry
from market_data_platform.tushare_backfill import build_a_share_backfill_plan
from market_data_platform.tushare_refresh_part01 import (
    PROMOTION_RAW_DATASETS,
    PROMOTION_REQUIRED_ASSETS,
    AShareCurrentPromotionOptions,
    AShareCurrentRefreshPaths,
    _companion_manifest_path,
    _manifest_summary_for_path,
    _passed_report,
    _promotion_raw_sources,
    _resolve_refresh_paths,
    _resolved_optional_path,
)


def _require_file(path: Path, *, label: str, require_manifest: bool = False) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} file not found: {path}")
    payload = {"path": str(path), "size_bytes": str(path.stat().st_size)}
    manifest_path = _companion_manifest_path(path)
    if require_manifest and not manifest_path.is_file():
        raise FileNotFoundError(f"{label} manifest not found: {manifest_path}")
    if manifest_path.is_file():
        payload["manifest_path"] = str(manifest_path)
    return payload


def _require_existing_asset(path: Path, *, label: str) -> dict[str, str]:
    resolved = path.expanduser().resolve(strict=False)
    if not path.exists():
        raise FileNotFoundError(f"{label} current asset not found: {path}")
    return {
        "path": str(path),
        "resolved_path": str(resolved),
        "path_kind": "directory" if path.is_dir() else "file",
    }


def _replace_latest_symlink(*, alias_path: Path, target: Path) -> dict[str, str]:
    alias = alias_path.expanduser()
    target_path = target.expanduser().resolve(strict=True)
    alias.parent.mkdir(parents=True, exist_ok=True)
    temporary = alias.with_name(f".{alias.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError(f"Temporary latest alias already exists: {temporary}")
    if target_path.is_dir():
        shutil.copytree(target_path, temporary, symlinks=False)
    else:
        shutil.copy2(target_path, temporary)
    if alias.is_symlink() or alias.is_file():
        alias.unlink()
    elif alias.is_dir():
        shutil.rmtree(alias)
    temporary.replace(alias)
    return {"alias_path": str(alias), "target": str(target_path), "materialized": "true"}


def _copy_release_file(
    *,
    source: Path,
    destination: Path,
    copy_manifest: bool = False,
) -> dict[str, str]:
    source_path = source.expanduser().resolve(strict=True)
    destination_path = destination.expanduser()
    if source_path == destination_path:
        return {"destination": str(destination_path), "source": str(source_path), "action": "same"}
    if destination_path.is_symlink():
        destination_path.unlink()
    if destination_path.exists() and destination_path.is_dir():
        raise RuntimeError(f"Refusing to replace directory with release file: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(f".{destination_path.name}.tmp")
    shutil.copy2(source_path, temporary)
    temporary.replace(destination_path)
    payload = {
        "destination": str(destination_path),
        "source": str(source_path),
        "action": "copied",
    }
    if copy_manifest:
        source_manifest = _companion_manifest_path(source_path)
        destination_manifest = _companion_manifest_path(destination_path)
        manifest_update = _copy_release_file(
            source=source_manifest,
            destination=destination_manifest,
            copy_manifest=False,
        )
        payload["manifest_destination"] = manifest_update["destination"]
        payload["manifest_source"] = manifest_update["source"]
    return payload


def _publish_file_alias(*, alias_path: Path, target: Path) -> dict[str, str]:
    """Materialize a stable file entry from an immutable published file."""
    alias = alias_path.expanduser()
    target_path = target.expanduser().resolve(strict=True)
    alias.parent.mkdir(parents=True, exist_ok=True)
    if alias.is_symlink() or alias.is_file():
        alias.unlink()
    elif alias.exists():
        raise RuntimeError(f"Refusing to replace non-file current alias: {alias}")
    temporary = alias.with_name(f".{alias.name}.tmp")
    shutil.copy2(target_path, temporary)
    temporary.replace(alias)
    return {"alias_path": str(alias), "target": str(target_path), "materialized": "true"}


def _load_existing_contracts(
    root: Path,
    *,
    a_share_contract: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [dict(a_share_contract)]


def _promotion_required_assets(options: AShareCurrentPromotionOptions) -> tuple[str, ...]:
    selected = tuple(str(asset).strip() for asset in options.required_assets or ())
    return tuple(asset for asset in selected if asset) or PROMOTION_REQUIRED_ASSETS


def _build_promotion_context(options: AShareCurrentPromotionOptions) -> dict[str, Any]:
    raw_plan = build_a_share_backfill_plan(
        artifacts_root=options.artifacts_root,
        start_date=options.start_date,
        end_date=options.end_date,
        datasets=PROMOTION_RAW_DATASETS,
        segment=options.segment,
    )
    root = resolve_artifacts_root(options.artifacts_root)
    refresh_paths = _resolve_refresh_paths(
        root,
        raw_plan,
        start_date=options.start_date,
        end_date=options.end_date,
    )
    raw_sources = _promotion_raw_sources(raw_plan, options)
    daily_clean = _resolved_optional_path(options.daily_clean_dir, refresh_paths.daily_clean_output)
    universe_by_date = _resolved_optional_path(
        options.universe_by_date,
        refresh_paths.universe_by_date,
    )
    universe_symbols = _resolved_optional_path(
        options.universe_symbols,
        refresh_paths.universe_symbols,
    )
    universe_meta = _resolved_optional_path(options.universe_meta, refresh_paths.universe_meta)
    baseline_report = _resolved_optional_path(
        options.daily_clean_baseline_report,
        refresh_paths.daily_clean_baseline_validation_report,
    )
    research_report = _resolved_optional_path(
        options.daily_clean_research_report,
        refresh_paths.daily_clean_research_validation_report,
    )
    universe_report = _resolved_optional_path(
        options.universe_validation_report,
        refresh_paths.universe_validation_report,
    )
    current_health_report = _resolved_optional_path(
        options.current_health_report,
        refresh_paths.current_health_report,
    )
    evidence_out = _resolved_optional_path(options.evidence_out, refresh_paths.release_evidence)
    return {
        "root": root,
        "refresh_paths": refresh_paths,
        "raw_sources": raw_sources,
        "daily_clean": daily_clean,
        "universe_files": {
            "universe_by_date": universe_by_date,
            "universe_symbols": universe_symbols,
            "universe_meta": universe_meta,
        },
        "reports": {
            "daily_clean_baseline": baseline_report,
            "daily_clean_research": research_report,
            "universe_validation": universe_report,
        },
        "current_health_report": current_health_report,
        "evidence_out": evidence_out,
        "required_assets": _promotion_required_assets(options),
    }


def _validate_promotion_inputs(context: Mapping[str, Any]) -> dict[str, Any]:
    raw_sources = context["raw_sources"]
    if not isinstance(raw_sources, Mapping):
        raise ValueError("raw_sources must be a mapping.")
    reports = context["reports"]
    if not isinstance(reports, Mapping):
        raise ValueError("reports must be a mapping.")
    universe_files = context["universe_files"]
    if not isinstance(universe_files, Mapping):
        raise ValueError("universe_files must be a mapping.")
    refresh_paths = context["refresh_paths"]
    if not isinstance(refresh_paths, AShareCurrentRefreshPaths):
        raise ValueError("refresh_paths must be AShareCurrentRefreshPaths.")
    required_assets = tuple(str(asset) for asset in context["required_assets"])
    published_assets = {*PROMOTION_RAW_DATASETS, "daily_clean", *universe_files.keys()}
    preexisting_assets = {
        asset: _require_existing_asset(refresh_paths.aliases[asset], label=asset)
        for asset in required_assets
        if asset not in published_assets
    }
    raw_manifests = {
        dataset: _manifest_summary_for_path(Path(path), label=f"{dataset} raw asset")
        for dataset, path in raw_sources.items()
    }
    return {
        "raw_manifests": raw_manifests,
        "daily_clean_manifest": _manifest_summary_for_path(
            Path(context["daily_clean"]),
            label="daily_clean asset",
        ),
        "reports": {
            "daily_clean_baseline": _passed_report(
                Path(reports["daily_clean_baseline"]),
                label="daily_clean baseline validation",
            ),
            "daily_clean_research": _passed_report(
                Path(reports["daily_clean_research"]),
                label="daily_clean research validation",
            ),
            "universe_validation": _passed_report(
                Path(reports["universe_validation"]),
                label="universe validation",
            ),
        },
        "universe_files": {
            key: _require_file(Path(path), label=key, require_manifest=True)
            for key, path in universe_files.items()
        },
        "preexisting_assets": preexisting_assets,
    }


def _promotion_actions(context: Mapping[str, Any]) -> dict[str, Any]:
    refresh_paths = context["refresh_paths"]
    if not isinstance(refresh_paths, AShareCurrentRefreshPaths):
        raise ValueError("refresh_paths must be AShareCurrentRefreshPaths.")
    raw_sources = context["raw_sources"]
    universe_files = context["universe_files"]
    if not isinstance(raw_sources, Mapping) or not isinstance(universe_files, Mapping):
        raise ValueError("promotion context is malformed.")
    return {
        "alias_updates": {
            **{
                dataset: {
                    "alias_path": str(refresh_paths.aliases[dataset]),
                    "target": str(Path(path)),
                }
                for dataset, path in raw_sources.items()
            },
            "daily_clean": {
                "alias_path": str(refresh_paths.aliases["daily_clean"]),
                "target": str(context["daily_clean"]),
            },
        },
        "file_updates": {
            key: {"destination": str(refresh_paths.aliases[key]), "source": str(path)}
            for key, path in universe_files.items()
        },
        "current_contract": str(current_contract_path(context["root"], market="a_share")),
        "dataset_registry": str(dataset_registry_path(context["root"])),
        "current_health_report": str(context["current_health_report"]),
        "release_evidence": str(context["evidence_out"]),
    }


def _write_release_evidence(
    evidence_out: Path,
    payload: Mapping[str, Any],
) -> None:
    evidence_out.parent.mkdir(parents=True, exist_ok=True)
    evidence_out.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _apply_promotion(
    context: Mapping[str, Any],
    checks: Mapping[str, Any],
    options: AShareCurrentPromotionOptions,
) -> dict[str, Any]:
    root = Path(context["root"])
    refresh_paths = context["refresh_paths"]
    if not isinstance(refresh_paths, AShareCurrentRefreshPaths):
        raise ValueError("refresh_paths must be AShareCurrentRefreshPaths.")
    raw_sources = context["raw_sources"]
    universe_files = context["universe_files"]
    if not isinstance(raw_sources, Mapping) or not isinstance(universe_files, Mapping):
        raise ValueError("promotion context is malformed.")

    alias_updates = {
        dataset: _replace_latest_symlink(
            alias_path=refresh_paths.aliases[dataset],
            target=Path(path),
        )
        for dataset, path in raw_sources.items()
    }
    alias_updates["daily_clean"] = _replace_latest_symlink(
        alias_path=refresh_paths.aliases["daily_clean"],
        target=Path(context["daily_clean"]),
    )
    file_updates: dict[str, dict[str, Any]] = {
        key: _copy_release_file(
            source=Path(path),
            destination=(
                getattr(
                    refresh_paths,
                    f"universe_version_{key.removeprefix('universe_')}",
                    None,
                )
                or refresh_paths.aliases[key]
            ),
            copy_manifest=True,
        )
        for key, path in universe_files.items()
    }
    for key in universe_files:
        version_path = getattr(
            refresh_paths,
            f"universe_version_{key.removeprefix('universe_')}",
            refresh_paths.aliases[key],
        )
        file_updates[key]["alias"] = _publish_file_alias(
            alias_path=refresh_paths.aliases[key],
            target=version_path,
        )

    contract_output = current_contract_path(root, market="a_share")
    contract = build_current_contract(
        root,
        market="a_share",
        provider="tushare",
        generated_by="marketdata tushare promote-a-share-current",
        target_date=options.end_date,
    )
    write_current_contract(contract_output, contract)
    registry_output = dataset_registry_path(root)
    write_combined_dataset_registry(
        registry_output,
        _load_existing_contracts(root, a_share_contract=contract),
    )
    health_report = Path(context["current_health_report"])
    health = inspect_current_contract(
        ContractInspectionOptions(
            artifacts_root=root,
            market="a_share",
            provider="tushare",
            target_date=options.end_date,
            required_start_date=options.start_date,
            assets=list(context["required_assets"]),
            fail_on_severity=options.fail_on_severity,
        )
    )
    write_current_contract_health_report(health, output=health_report, output_format="json")
    exit_code = current_contract_health_exit_code(health)
    if exit_code:
        raise RuntimeError(f"A 股 current promotion health gate failed; see {health_report}")

    evidence_out = Path(context["evidence_out"])
    evidence = {
        "schema_version": "tushare.a_share.current_release.v1",
        "status": "passed",
        "dry_run": False,
        "market": "a_share",
        "provider": "tushare",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "artifacts_root": str(root),
        "query": {"start_date": options.start_date, "end_date": options.end_date},
        "required_assets": list(context["required_assets"]),
        "checks": checks,
        "publication": {
            "alias_updates": alias_updates,
            "file_updates": file_updates,
            "current_contract": str(contract_output),
            "dataset_registry": str(registry_output),
            "current_health_report": str(health_report),
            "release_evidence": str(evidence_out),
        },
        "quality_verdict": health.get("quality_verdict", {}),
    }
    _write_release_evidence(evidence_out, evidence)
    return evidence


def _run_a_share_current_promotion(options: AShareCurrentPromotionOptions) -> dict[str, Any]:
    context = _build_promotion_context(options)
    checks = _validate_promotion_inputs(context)
    actions = _promotion_actions(context)
    if not options.apply:
        return {
            "schema_version": "tushare.a_share.current_release.v1",
            "status": "ready",
            "dry_run": True,
            "market": "a_share",
            "provider": "tushare",
            "artifacts_root": str(context["root"]),
            "query": {"start_date": options.start_date, "end_date": options.end_date},
            "required_assets": list(context["required_assets"]),
            "checks": checks,
            "planned_publication": actions,
        }
    return _apply_promotion(context, checks, options)


def run_a_share_current_promotion(
    options: AShareCurrentPromotionOptions | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """Validate and optionally publish a TuShare A-share current release."""
    if options is not None:
        if legacy_options:
            raise TypeError("Pass either options or keyword promotion options, not both.")
        return _run_a_share_current_promotion(options)
    return _run_a_share_current_promotion(AShareCurrentPromotionOptions(**legacy_options))
