"""Production builder for Guan annual A-share minute-bar files.

The source Parquet is read exactly once.  That scan writes a partitioned raw
staging dataset containing canonical values and audit flags.  All subsequent
cleanup accounting, per-day compaction, validation, and promotion operate on
staging files, never on the annual source again.
"""

from __future__ import annotations

import shutil
from dataclasses import asdict
from types import ModuleType
from typing import Any

from market_data_platform.dataset_lock import (
    minute_dataset_lock,
)
from market_data_platform.providers.guan_annual_minbar_part01 import (
    ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION,
    AnnualMinbarBuildOptions,
    _cleanup_stale_annual_temps,
    _configure_connection,
    _dataset_lock,
    _load_manifest,
    _require_duckdb,
    _save_manifest,
    _utc_now,
)
from market_data_platform.providers.guan_annual_minbar_part03 import (
    _annual_build_context,
    _AnnualBuildContext,
    _AnnualContractState,
    _AnnualPromotionResult,
    _AnnualStagingResult,
    _complete_manifest_is_resumable,
    _manifest_contract_state,
    _prepare_staging,
    _promotion_action,
    _skipped_complete_result,
)


def _promote_staging(
    connection: Any,
    context: _AnnualBuildContext,
    manifest: dict[str, Any],
    staging: _AnnualStagingResult,
    contract: _AnnualContractState,
) -> _AnnualPromotionResult:
    entry = staging.entry
    entry["status"] = "promoting"
    _save_manifest(manifest, context.manifest_path)
    promoted: list[str] = []
    preserved: list[str] = []
    replaced_invalid: list[str] = []
    try:
        for date in staging.validation["dates"]:
            staged = context.canonical_root / f"trade_date={date}" / "part-00000.parquet"
            destination = context.output_root / f"trade_date={date}" / "part-00000.parquet"
            action, _ = _promotion_action(
                connection,
                date=date,
                destination=destination,
                options=context.options,
                contract_changed=contract.changed,
            )
            if action == "preserve_valid":
                preserved.append(date)
            else:
                from market_data_platform.providers import guan_annual_minbar as _annual_shell

                _annual_shell._atomic_promote(staged, destination)
                promoted.append(date)
                if action == "replace_invalid":
                    replaced_invalid.append(date)
            entry["promoted_dates"] = promoted
            entry["preserved_dates"] = preserved
            entry["replaced_invalid_dates"] = replaced_invalid
            _save_manifest(manifest, context.manifest_path)
    except Exception as exc:
        entry["status"] = "promotion_failed"
        entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["failed_at"] = _utc_now()
        _save_manifest(manifest, context.manifest_path)
        raise
    return _AnnualPromotionResult(
        promoted=promoted,
        preserved=preserved,
        replaced_invalid=replaced_invalid,
    )


def _complete_build(
    context: _AnnualBuildContext,
    manifest: dict[str, Any],
    staging: _AnnualStagingResult,
    promotion: _AnnualPromotionResult,
) -> dict[str, Any]:
    staging.entry.update(
        {
            "status": "complete",
            "completed_at": _utc_now(),
            "promotion_policy": context.options.promotion_policy,
            "force": context.options.force,
            "replace_dates": list(context.options.replace_dates),
            "promoted_dates": promotion.promoted,
            "preserved_dates": promotion.preserved,
            "replaced_invalid_dates": promotion.replaced_invalid,
            "staging_path": None if not context.options.keep_staging else str(context.work_root),
        }
    )
    _save_manifest(manifest, context.manifest_path)
    if not context.options.keep_staging:
        shutil.rmtree(context.work_root, ignore_errors=True)
        year_staging = context.work_root.parent
        try:
            year_staging.rmdir()
        except OSError:
            pass
    return {
        "year": context.options.year,
        "status": "complete",
        "source_fingerprint": context.fingerprint,
        "unit_profile": asdict(context.profile),
        "transform_contract_version": ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION,
        "memory_limit": context.memory_limit,
        "source_stats": staging.source_stats,
        "staging_validation": staging.validation,
        "promoted_dates": promotion.promoted,
        "preserved_dates": promotion.preserved,
        "replaced_invalid_dates": promotion.replaced_invalid,
        "manifest_path": str(context.manifest_path),
        "source_full_scans": 0 if staging.resumed else 1,
    }


def _run_locked_build(
    context: _AnnualBuildContext,
    duckdb: ModuleType,
) -> dict[str, Any]:
    manifest = _load_manifest(context.manifest_path)
    manifest["transform_contract_version"] = ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION
    existing_entry = manifest["years"].get(str(context.options.year))
    contract = _manifest_contract_state(existing_entry, context.fingerprint["id"])
    connection = duckdb.connect(database=":memory:")
    try:
        _configure_connection(
            connection,
            context.options,
            context.work_root / "duckdb-tmp",
            resolved_memory_limit=context.resolved_memory_limit,
        )
        if _complete_manifest_is_resumable(
            connection,
            entry=existing_entry,
            fingerprint_id=context.fingerprint["id"],
            output_root=context.output_root,
            options=context.options,
        ):
            return _skipped_complete_result(context, existing_entry)
        staging = _prepare_staging(
            connection,
            context,
            manifest,
            existing_entry,
            contract,
        )
        promotion = _promote_staging(connection, context, manifest, staging, contract)
        return _complete_build(context, manifest, staging, promotion)
    finally:
        connection.close()


def build_guan_annual_minbar(options: AnnualMinbarBuildOptions) -> dict[str, Any]:
    """Build, validate, and atomically promote one Guan annual minbar file."""
    context = _annual_build_context(options)
    duckdb = _require_duckdb()

    with (
        minute_dataset_lock(
            context.output_root,
            operation=f"build-guan-annual-minutes:{options.year}",
        ),
        _dataset_lock(context.output_root),
    ):
        _cleanup_stale_annual_temps(
            output_root=context.output_root,
            work_root=context.work_root,
            manifest_path=context.manifest_path,
        )
        return _run_locked_build(context, duckdb)
