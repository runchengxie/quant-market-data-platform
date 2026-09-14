"""Production builder for Guan annual A-share minute-bar files.

The source Parquet is read exactly once.  That scan writes a partitioned raw
staging dataset containing canonical values and audit flags.  All subsequent
cleanup accounting, per-day compaction, validation, and promotion operate on
staging files, never on the annual source again.
"""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from market_data_platform.providers.guan_annual_minbar_part01 import (
    ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION,
    AnnualMinbarBuildOptions,
    AnnualMinbarUnitProfile,
    AnnualMinbarValidationError,
    _expanded,
    _save_manifest,
    _utc_now,
    source_fingerprint,
    unit_profile_for_year,
)
from market_data_platform.providers.guan_annual_minbar_part02 import (
    _compact_raw_dates,
    _partition_validation,
    _raw_staging_stats,
    _source_fatal_issues,
    _validate_staging_year,
)
from market_data_platform.runtime_memory import (
    choose_memory_budget_mb,
)


def _complete_manifest_is_resumable(
    connection: Any,
    *,
    entry: dict[str, Any] | None,
    fingerprint_id: str,
    output_root: Path,
    options: AnnualMinbarBuildOptions,
) -> bool:
    if (
        not options.resume
        or options.force
        or options.replace_dates
        or options.promotion_policy == "replace_all"
        or not entry
        or entry.get("status") != "complete"
        or entry.get("transform_contract_version") != ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION
        or entry.get("source_fingerprint", {}).get("id") != fingerprint_id
    ):
        return False
    dates = entry.get("dates")
    if not isinstance(dates, list) or not dates:
        return False
    return all(
        _partition_validation(
            connection,
            output_root / f"trade_date={date}" / "part-00000.parquet",
            str(date),
        )["valid"]
        for date in dates
    )


def _reusable_staging(
    connection: Any,
    *,
    entry: dict[str, Any] | None,
    fingerprint_id: str,
    expected_root: Path,
    year: int,
) -> tuple[dict[str, Any], dict[str, int]] | None:
    if not entry or entry.get("status") not in {
        "staging_validated",
        "promoting",
        "promotion_failed",
    }:
        return None
    if entry.get("source_fingerprint", {}).get("id") != fingerprint_id:
        return None
    if entry.get("transform_contract_version") != ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION:
        return None
    recorded = Path(str(entry.get("staging_path", ""))).resolve()
    if recorded != expected_root.resolve() or not recorded.is_dir():
        return None
    source_stats = entry.get("source_stats")
    if not isinstance(source_stats, dict) or "candidate_rows" not in source_stats:
        return None
    validation = _validate_staging_year(
        connection,
        canonical_root=recorded / "canonical",
        year=year,
        expected_rows=int(source_stats["candidate_rows"]),
    )
    return validation, {str(key): int(value) for key, value in source_stats.items()}


def _promotion_action(
    connection: Any,
    *,
    date: str,
    destination: Path,
    options: AnnualMinbarBuildOptions,
    contract_changed: bool,
) -> tuple[str, dict[str, Any] | None]:
    if contract_changed:
        return "replace_changed_contract", None
    if options.force or options.promotion_policy == "replace_all" or date in options.replace_dates:
        return "replace", None
    if not destination.is_file():
        return "missing", None
    validation = _partition_validation(connection, destination, date)
    if validation["valid"]:
        return "preserve_valid", validation
    return "replace_invalid", validation


def _previous_attempts_for_new_entry(existing_entry: Any) -> list[dict[str, Any]]:
    if not isinstance(existing_entry, dict) or existing_entry.get("status") == "complete":
        return []
    previous = existing_entry.get("previous_attempts", [])
    attempts = [dict(item) for item in previous if isinstance(item, dict)]
    evidence_fields = (
        "status",
        "source_stats",
        "fatal_issues",
        "started_at",
        "failed_at",
        "error",
    )
    attempts.append({field: existing_entry.get(field) for field in evidence_fields})
    return attempts


@dataclass(frozen=True)
class _AnnualBuildContext:
    options: AnnualMinbarBuildOptions
    source: Path
    output_root: Path
    manifest_path: Path
    profile: AnnualMinbarUnitProfile
    resolved_memory_limit: str
    memory_limit: dict[str, Any]
    fingerprint: dict[str, Any]
    work_root: Path
    canonical_root: Path
    raw_root: Path


@dataclass(frozen=True)
class _AnnualContractState:
    source_fingerprint_changed: bool
    transform_contract_changed: bool

    @property
    def changed(self) -> bool:
        return self.source_fingerprint_changed or self.transform_contract_changed


@dataclass(frozen=True)
class _AnnualStagingResult:
    entry: dict[str, Any]
    validation: dict[str, Any]
    source_stats: dict[str, Any]
    resumed: bool


@dataclass(frozen=True)
class _AnnualPromotionResult:
    promoted: list[str]
    preserved: list[str]
    replaced_invalid: list[str]


def _annual_build_context(options: AnnualMinbarBuildOptions) -> _AnnualBuildContext:
    source = _expanded(options.source_path)
    output_root = _expanded(options.output_dir)
    manifest_path = _expanded(options.manifest_path)
    staging_root = (
        _expanded(options.staging_root)
        if options.staging_root is not None
        else output_root / ".annual-minbar-staging"
    )
    profile = unit_profile_for_year(options.year)
    from market_data_platform.providers import guan_annual_minbar as _annual_shell

    memory_snapshot = _annual_shell.read_memory_snapshot()
    resolved_memory_mb = choose_memory_budget_mb(options.memory_limit, snapshot=memory_snapshot)
    resolved_memory_limit = f"{resolved_memory_mb}MiB"
    memory_limit = {
        "requested": options.memory_limit,
        "resolved": resolved_memory_limit,
        "resolved_mb": resolved_memory_mb,
        "snapshot": memory_snapshot.to_dict(),
    }
    fingerprint = source_fingerprint(source)
    work_root = staging_root / f"year={options.year}" / fingerprint["id"][:16]
    return _AnnualBuildContext(
        options=options,
        source=source,
        output_root=output_root,
        manifest_path=manifest_path,
        profile=profile,
        resolved_memory_limit=resolved_memory_limit,
        memory_limit=memory_limit,
        fingerprint=fingerprint,
        work_root=work_root,
        canonical_root=work_root / "canonical",
        raw_root=work_root / "raw",
    )


def _manifest_contract_state(
    existing_entry: Any,
    fingerprint_id: Any,
) -> _AnnualContractState:
    existing_fingerprint_id = (
        existing_entry.get("source_fingerprint", {}).get("id")
        if isinstance(existing_entry, dict)
        else None
    )
    source_fingerprint_changed = bool(
        (existing_fingerprint_id and existing_fingerprint_id != fingerprint_id)
        or (
            isinstance(existing_entry, dict)
            and existing_entry.get("status") != "complete"
            and existing_entry.get("source_fingerprint_changed") is True
        )
    )
    transform_contract_changed = bool(
        (
            isinstance(existing_entry, dict)
            and existing_entry.get("transform_contract_version")
            != ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION
        )
        or (
            isinstance(existing_entry, dict)
            and existing_entry.get("status") != "complete"
            and existing_entry.get("transform_contract_changed") is True
        )
    )
    return _AnnualContractState(
        source_fingerprint_changed=source_fingerprint_changed,
        transform_contract_changed=transform_contract_changed,
    )


def _skipped_complete_result(
    context: _AnnualBuildContext,
    entry: dict[str, Any],
) -> dict[str, Any]:
    return {
        "year": context.options.year,
        "status": "skipped_complete",
        "source_fingerprint": context.fingerprint,
        "dates": entry["dates"],
        "manifest_path": str(context.manifest_path),
        "source_full_scans": 0,
        "memory_limit": entry.get("memory_limit", context.memory_limit),
    }


def _start_new_staging_entry(
    context: _AnnualBuildContext,
    manifest: dict[str, Any],
    existing_entry: Any,
    contract: _AnnualContractState,
) -> dict[str, Any]:
    if context.work_root.exists():
        shutil.rmtree(context.work_root)
    context.raw_root.mkdir(parents=True, exist_ok=True)
    previous_attempts = _previous_attempts_for_new_entry(existing_entry)
    entry: dict[str, Any] = {
        "year": context.options.year,
        "status": "scanning_source",
        "started_at": _utc_now(),
        "source_fingerprint": context.fingerprint,
        "source_full_scans": 0,
        "unit_profile": asdict(context.profile),
        "transform_contract_version": ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION,
        "transform_contract_changed": contract.transform_contract_changed,
        "memory_limit": context.memory_limit,
        "staging_path": str(context.work_root),
        "source_fingerprint_changed": contract.source_fingerprint_changed,
    }
    if previous_attempts:
        entry["previous_attempts"] = previous_attempts
    manifest["years"][str(context.options.year)] = entry
    _save_manifest(manifest, context.manifest_path)
    return entry


def _scan_raw_source(
    connection: Any,
    context: _AnnualBuildContext,
    manifest: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    try:
        from market_data_platform.providers import guan_annual_minbar as _annual_shell

        _annual_shell._copy_source_to_raw_staging(
            connection,
            source=context.source,
            raw_root=context.raw_root,
            year=context.options.year,
            profile=context.profile,
        )
        entry["source_full_scans"] = 1
        source_stats = _raw_staging_stats(connection, context.raw_root)
    except Exception as exc:
        entry["status"] = "source_scan_failed"
        entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["failed_at"] = _utc_now()
        _save_manifest(manifest, context.manifest_path)
        raise
    entry["source_stats"] = source_stats
    fatal = _source_fatal_issues(source_stats)
    if fatal:
        entry["status"] = "source_validation_failed"
        entry["fatal_issues"] = fatal
        entry["failed_at"] = _utc_now()
        _save_manifest(manifest, context.manifest_path)
        if not context.options.keep_staging:
            shutil.rmtree(context.work_root, ignore_errors=True)
        raise AnnualMinbarValidationError(
            f"Annual Guan source failed validation for {context.options.year}: {fatal}"
        )
    entry["status"] = "source_validated"
    _save_manifest(manifest, context.manifest_path)
    return source_stats


def _compact_and_validate_staging(
    connection: Any,
    context: _AnnualBuildContext,
    manifest: dict[str, Any],
    entry: dict[str, Any],
    source_stats: dict[str, Any],
) -> dict[str, Any]:
    try:
        dates = _compact_raw_dates(
            connection,
            raw_root=context.raw_root,
            canonical_root=context.canonical_root,
            year=context.options.year,
        )
        validation = _validate_staging_year(
            connection,
            canonical_root=context.canonical_root,
            year=context.options.year,
            expected_rows=source_stats["candidate_rows"],
        )
        if set(dates) != set(validation["dates"]):
            raise AnnualMinbarValidationError(
                f"Staging date inventory changed during compaction for {context.options.year}"
            )
    except Exception as exc:
        entry["status"] = "staging_validation_failed"
        entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["failed_at"] = _utc_now()
        _save_manifest(manifest, context.manifest_path)
        raise
    shutil.rmtree(context.raw_root, ignore_errors=True)
    entry.update(
        {
            "status": "staging_validated",
            "staging_validation": validation,
            "dates": validation["dates"],
            "promoted_dates": [],
            "preserved_dates": [],
            "replaced_invalid_dates": [],
        }
    )
    _save_manifest(manifest, context.manifest_path)
    return validation


def _build_new_staging(
    connection: Any,
    context: _AnnualBuildContext,
    manifest: dict[str, Any],
    existing_entry: Any,
    contract: _AnnualContractState,
) -> _AnnualStagingResult:
    entry = _start_new_staging_entry(context, manifest, existing_entry, contract)
    source_stats = _scan_raw_source(connection, context, manifest, entry)
    validation = _compact_and_validate_staging(
        connection,
        context,
        manifest,
        entry,
        source_stats,
    )
    return _AnnualStagingResult(
        entry=entry,
        validation=validation,
        source_stats=source_stats,
        resumed=False,
    )


def _resume_staging(
    context: _AnnualBuildContext,
    manifest: dict[str, Any],
    existing_entry: dict[str, Any],
    resumed: tuple[dict[str, Any], dict[str, int]],
    contract: _AnnualContractState,
) -> _AnnualStagingResult:
    validation, source_stats = resumed
    entry = existing_entry
    entry["status"] = "staging_validated"
    entry["memory_limit"] = context.memory_limit
    entry["source_fingerprint_changed"] = contract.source_fingerprint_changed
    entry["transform_contract_version"] = ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION
    entry["transform_contract_changed"] = contract.transform_contract_changed
    entry["staging_validation"] = validation
    entry["dates"] = validation["dates"]
    entry.setdefault("promoted_dates", [])
    entry.setdefault("preserved_dates", [])
    entry.setdefault("replaced_invalid_dates", [])
    _save_manifest(manifest, context.manifest_path)
    return _AnnualStagingResult(
        entry=entry,
        validation=validation,
        source_stats=source_stats,
        resumed=True,
    )


def _prepare_staging(
    connection: Any,
    context: _AnnualBuildContext,
    manifest: dict[str, Any],
    existing_entry: Any,
    contract: _AnnualContractState,
) -> _AnnualStagingResult:
    resumed = None
    if context.options.resume and not context.options.force:
        resumed = _reusable_staging(
            connection,
            entry=existing_entry,
            fingerprint_id=context.fingerprint["id"],
            expected_root=context.work_root,
            year=context.options.year,
        )
    if resumed is None:
        return _build_new_staging(
            connection,
            context,
            manifest,
            existing_entry,
            contract,
        )
    return _resume_staging(context, manifest, existing_entry, resumed, contract)
