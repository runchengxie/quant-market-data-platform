"""Promote verified Guan mobile archives into an isolated provider-native tree."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from market_data_platform.dataset_lock import DatasetLockError, exclusive_file_lock
from market_data_platform.guan_mobile_raw_part01 import (
    _AT_FDCWD,
    _RENAME_NOREPLACE,
    COPY_BUFFER_BYTES,
    LOCK_FILENAME,
    PROMOTION_SCHEMA,
    PROVIDER_RELATIVE_ROOT,
    SCHEMA_PROFILE,
    GuanMobilePromotionConflict,
    GuanMobilePromotionError,
    GuanMobilePromotionVerificationError,
    PromotionOptions,
    PromotionStrategy,
    _absolute_path,
    _atomic_write_json,
    _existing_target_status,
    _fsync_directory,
    _json_sha256,
    _load_json,
    _load_verified_archive,
    _native_layout_conflicts,
    _PromotionPaths,
    _PromotionPlanInventory,
    _receipt_path,
    _select_logical_sources,
    _SourceArtifact,
    _standardized_overlaps,
    _stat_payload,
    _target_path,
    _utc_now,
    _validate_roots,
)


def _base_payload(
    options: PromotionOptions,
    paths: _PromotionPaths,
) -> dict[str, Any]:
    return {
        "schema_version": PROMOTION_SCHEMA,
        "status": "planned" if options.dry_run else "preflight",
        "mode": "dry_run" if options.dry_run else "apply",
        "strategy": options.strategy,
        "schema_profile": SCHEMA_PROFILE,
        "source_dir": str(paths.source),
        "raw_root": str(paths.raw_root),
        "provider_root": str(paths.provider_root),
        "archive_manifest": str(paths.archive_manifest),
        "archive_manifest_sha256": _json_sha256(paths.archive_manifest),
        "receipt": str(paths.receipt),
        "policies": {
            "logical_key": {
                "daily": "dataset+trade_date",
                "auxiliary": "dataset+canonical_filename",
            },
            "same_key_same_sha256": "skip",
            "same_key_different_sha256": "fail_before_mutation",
            "incoming_mutation": "forbidden",
            "standardized_raw_mixing": "forbidden_provider_native_isolation",
            "hardlink_fallback": "none_use_explicit_copy_strategy",
        },
        "lineage": {
            "origin": "mobile_archive_via_verified_incoming",
            "guan_deal_dir": str(paths.provider_root / "deal"),
            "deal_discovery_pattern": "**/deal_YYYYMMDD.parquet",
            "builder_argument": f"--guan-deal-dir {paths.provider_root / 'deal'}",
        },
    }


def _planned_entry(
    artifact: _SourceArtifact,
    *,
    archive_source_dir: str,
    raw_root: Path,
    provider_root: Path,
    strategy: PromotionStrategy,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    target = _target_path(provider_root, artifact)
    target_status, target_stat, target_conflict = _existing_target_status(artifact, target)
    entry_status = (
        "existing_same_sha_skipped" if target_status == "same_sha" else f"planned_{strategy}"
    )
    source_stat = _stat_payload(artifact.path)
    same_inode = target_stat is not None and (
        source_stat["device"],
        source_stat["inode"],
    ) == (target_stat["device"], target_stat["inode"])
    storage_strategy = (
        ("hardlink" if same_inode else "preexisting_same_sha")
        if target_status == "same_sha"
        else strategy
    )
    return (
        {
            "logical_key": artifact.logical_key,
            "dataset": artifact.dataset,
            "trade_date": artifact.trade_date,
            "schema_profile": SCHEMA_PROFILE,
            "origin_mobile_path": str(Path(archive_source_dir) / artifact.relative_path),
            "incoming_relative_path": artifact.relative_path,
            "incoming_path": str(artifact.path),
            "organized_path": str(target),
            "sha256": artifact.sha256,
            "sha256_evidence": "verified_archive_manifest",
            "size": artifact.size,
            "source_stat": source_stat,
            "destination_stat": target_stat,
            "requested_strategy": strategy,
            "storage_strategy": storage_strategy,
            "status": "conflict" if target_status == "conflict" else entry_status,
            "standardized_overlaps": _standardized_overlaps(raw_root, artifact),
            "standardized_overlap_policy": "isolated_not_discoverable_as_standardized_raw",
        },
        target_conflict,
    )


def _plan_summary(
    inventory: _PromotionPlanInventory,
    strategy: PromotionStrategy,
) -> dict[str, Any]:
    planned_entries = [
        entry for entry in inventory.entries if str(entry["status"]).startswith("planned_")
    ]
    return {
        "archive_files": len(inventory.artifacts) + len(inventory.ignored),
        "supported_daily_files": sum(
            artifact.trade_date is not None for artifact in inventory.artifacts
        ),
        "auxiliary_files": sum(artifact.trade_date is None for artifact in inventory.artifacts),
        "logical_artifacts": len(inventory.entries),
        "planned_promotions": len(planned_entries),
        "existing_same_sha_skipped": sum(
            entry["status"] == "existing_same_sha_skipped" for entry in inventory.entries
        ),
        "exact_source_duplicates_skipped": len(inventory.duplicates),
        "ignored_archive_files": len(inventory.ignored),
        "standardized_schema_overlaps": sum(
            bool(entry["standardized_overlaps"]) for entry in inventory.entries
        ),
        "conflicts": len(inventory.conflicts),
        "logical_bytes": sum(int(entry["size"]) for entry in inventory.entries),
        "additional_data_bytes": (
            0 if strategy == "hardlink" else sum(int(entry["size"]) for entry in planned_entries)
        ),
    }


def _build_plan(options: PromotionOptions) -> dict[str, Any]:
    source = _absolute_path(options.source_dir)
    raw_root = _absolute_path(options.raw_root)
    archive_manifest_path = _absolute_path(options.archive_manifest)
    receipt_path = _receipt_path(options.receipt)
    provider_root = _validate_roots(
        source,
        raw_root,
        archive_manifest_path,
        receipt_path,
    )
    paths = _PromotionPaths(
        source=source,
        raw_root=raw_root,
        archive_manifest=archive_manifest_path,
        receipt=receipt_path,
        provider_root=provider_root,
    )
    archive_manifest, artifacts, ignored = _load_verified_archive(
        source,
        archive_manifest_path,
    )
    selected, duplicates, conflicts = _select_logical_sources(artifacts)
    conflicts.extend(_native_layout_conflicts(provider_root, selected))

    planned = [
        _planned_entry(
            artifact,
            archive_source_dir=str(archive_manifest["source_dir"]),
            raw_root=raw_root,
            provider_root=provider_root,
            strategy=options.strategy,
        )
        for artifact in selected
    ]
    entries = [entry for entry, _ in planned]
    for _, target_conflict in planned:
        if target_conflict is not None:
            conflicts.append(target_conflict)

    plan_inventory = _PromotionPlanInventory(
        artifacts=artifacts,
        entries=entries,
        duplicates=duplicates,
        ignored=ignored,
        conflicts=conflicts,
    )
    payload = _base_payload(options, paths)
    payload.update(
        {
            "archive_status": archive_manifest["status"],
            "archive_file_count": archive_manifest.get("file_count"),
            "entries": entries,
            "source_duplicates": duplicates,
            "ignored_archive_files": ignored,
            "conflicts": conflicts,
            "summary": _plan_summary(plan_inventory, options.strategy),
        }
    )
    return payload


def plan_guan_mobile_promotion(options: PromotionOptions) -> dict[str, Any]:
    """Build a no-write semantic union plan from a verified archive receipt."""
    return _build_plan(
        PromotionOptions(
            source_dir=options.source_dir,
            raw_root=options.raw_root,
            archive_manifest=options.archive_manifest,
            receipt=options.receipt,
            strategy=options.strategy,
            dry_run=True,
        )
    )


def _source_identity(entry: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    value = entry["source_stat"]
    if not isinstance(value, Mapping):
        raise GuanMobilePromotionError("Promotion entry has no source stat identity")
    return (
        int(value["device"]),
        int(value["inode"]),
        int(value["mode"]),
        int(value["size"]),
        int(value["mtime_ns"]),
    )


def _current_identity(path: Path) -> tuple[int, int, int, int, int]:
    value = path.stat(follow_symlinks=False)
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns)


def _hardlink_one(entry: Mapping[str, Any], raw_root: Path) -> dict[str, int]:
    source = Path(str(entry["incoming_path"]))
    destination = Path(str(entry["organized_path"]))
    if _current_identity(source) != _source_identity(entry):
        raise GuanMobilePromotionError(f"Incoming source changed during promotion: {source}")
    if source.stat().st_dev != raw_root.stat().st_dev:
        raise GuanMobilePromotionError(
            "Hardlink promotion requires _incoming and raw root on the same filesystem; "
            "rerun explicitly with --strategy copy"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.stat().st_dev != source.stat().st_dev:
        raise GuanMobilePromotionError(
            "Hardlink target directory is on another filesystem; rerun explicitly with "
            "--strategy copy"
        )
    try:
        os.link(source, destination, follow_symlinks=False)
    except FileExistsError as exc:
        raise GuanMobilePromotionError(
            f"Promotion target appeared after preflight: {destination}"
        ) from exc
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise GuanMobilePromotionError(
                "Filesystem rejected the hardlink; rerun explicitly with --strategy copy"
            ) from exc
        raise
    _fsync_directory(destination.parent)
    source_stat = source.stat(follow_symlinks=False)
    destination_stat = destination.stat(follow_symlinks=False)
    if (source_stat.st_dev, source_stat.st_ino) != (
        destination_stat.st_dev,
        destination_stat.st_ino,
    ):
        raise GuanMobilePromotionError(f"Hardlink identity check failed: {destination}")
    return _stat_payload(destination)


def _copy_one(entry: Mapping[str, Any]) -> dict[str, int]:
    source = Path(str(entry["incoming_path"]))
    destination = Path(str(entry["organized_path"]))
    if _current_identity(source) != _source_identity(entry):
        raise GuanMobilePromotionError(f"Incoming source changed during promotion: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as destination_handle:
            while chunk := source_handle.read(COPY_BUFFER_BYTES):
                destination_handle.write(chunk)
                digest.update(chunk)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        if _current_identity(source) != _source_identity(entry):
            raise GuanMobilePromotionError(f"Incoming source changed during copy: {source}")
        if digest.hexdigest() != entry["sha256"]:
            raise GuanMobilePromotionVerificationError(
                f"Incoming source SHA-256 no longer matches archive receipt: {source}"
            )
        source_stat = source.stat(follow_symlinks=False)
        os.chmod(temporary, stat.S_IMODE(source_stat.st_mode))
        os.utime(temporary, ns=(source_stat.st_mtime_ns, source_stat.st_mtime_ns))
        with temporary.open("rb") as temporary_handle:
            os.fsync(temporary_handle.fileno())
        try:
            _rename_noreplace(temporary, destination)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            raise GuanMobilePromotionError(
                f"Promotion target appeared after preflight: {destination}"
            ) from exc
        _fsync_directory(destination.parent)
        return _stat_payload(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _rename_noreplace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = libc.renameat2
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def _validate_existing_receipt(payload: Mapping[str, Any], path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    existing = _load_json(path, label="promotion receipt")
    if existing.get("schema_version") != PROMOTION_SCHEMA:
        raise GuanMobilePromotionError(f"Refusing to replace an unrelated receipt: {path}")
    identity_fields = (
        "source_dir",
        "raw_root",
        "provider_root",
        "archive_manifest",
        "archive_manifest_sha256",
        "strategy",
        "schema_profile",
    )
    mismatched = [field for field in identity_fields if existing.get(field) != payload.get(field)]
    if mismatched:
        raise GuanMobilePromotionError(
            f"Promotion receipt belongs to different inputs: fields={mismatched}"
        )
    return existing


def _merge_prior_promotion_evidence(
    payload: dict[str, Any],
    existing: Mapping[str, Any] | None,
) -> None:
    if existing is None:
        return
    prior_entries = existing.get("entries")
    if not isinstance(prior_entries, list):
        return
    prior_by_key = {
        str(entry.get("logical_key")): entry
        for entry in prior_entries
        if isinstance(entry, Mapping)
    }
    for entry in payload["entries"]:
        prior = prior_by_key.get(str(entry["logical_key"]))
        if prior is None:
            continue
        first_promoted_at = prior.get("first_promoted_at") or prior.get("promoted_at")
        if isinstance(first_promoted_at, str):
            entry["first_promoted_at"] = first_promoted_at


def promote_guan_mobile(options: PromotionOptions) -> dict[str, Any]:
    """Promote the verified semantic union, checkpointing after every atomic artifact."""
    if options.dry_run:
        return plan_guan_mobile_promotion(options)
    raw_root = _absolute_path(options.raw_root)
    provider_root = raw_root / PROVIDER_RELATIVE_ROOT
    lock_path = provider_root / LOCK_FILENAME
    try:
        lock_context = exclusive_file_lock(
            lock_path,
            operation="promote-guan-mobile-provider-native-raw",
        )
        with lock_context:
            payload = _build_plan(options)
            receipt_path = _receipt_path(options.receipt)
            existing = _validate_existing_receipt(payload, receipt_path)
            _merge_prior_promotion_evidence(payload, existing)
            prior_created_at = existing.get("created_at") if existing is not None else None
            payload["created_at"] = (
                prior_created_at if isinstance(prior_created_at, str) else _utc_now()
            )
            payload["run_started_at"] = _utc_now()
            if payload["conflicts"]:
                payload["status"] = "conflict"
                payload["completed_at"] = _utc_now()
                _atomic_write_json(payload, receipt_path)
                raise GuanMobilePromotionConflict(
                    f"Promotion preflight found {len(payload['conflicts'])} conflict(s); "
                    f"see {receipt_path}"
                )

            payload["status"] = "in_progress"
            _atomic_write_json(payload, receipt_path)
            for entry in payload["entries"]:
                if entry["status"] == "existing_same_sha_skipped":
                    continue
                try:
                    destination_stat = (
                        _hardlink_one(entry, raw_root)
                        if options.strategy == "hardlink"
                        else _copy_one(entry)
                    )
                except Exception as exc:
                    entry["status"] = "promotion_failed"
                    entry["error"] = {"type": type(exc).__name__, "message": str(exc)}
                    payload["status"] = "failed"
                    payload["error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "logical_key": entry["logical_key"],
                    }
                    payload["updated_at"] = _utc_now()
                    _atomic_write_json(payload, receipt_path)
                    raise
                entry["status"] = "promoted"
                entry["destination_stat"] = destination_stat
                entry["promoted_at"] = _utc_now()
                entry.setdefault("first_promoted_at", entry["promoted_at"])
                payload["updated_at"] = _utc_now()
                _atomic_write_json(payload, receipt_path)

            payload["status"] = "complete"
            payload["completed_at"] = _utc_now()
            payload["verification_status"] = "not_run"
            payload["summary"].update(
                {
                    "promoted": sum(entry["status"] == "promoted" for entry in payload["entries"]),
                    "existing_same_sha_skipped": sum(
                        entry["status"] == "existing_same_sha_skipped"
                        for entry in payload["entries"]
                    ),
                    "complete_logical_artifacts": len(payload["entries"]),
                }
            )
            _atomic_write_json(payload, receipt_path)
            return payload
    except DatasetLockError as exc:
        raise GuanMobilePromotionError(f"Another raw promotion holds {lock_path}: {exc}") from None


def _receipt_artifact_path(value: Any, *, root: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise GuanMobilePromotionVerificationError(
            f"Receipt artifact escapes its declared root: {path}"
        )
    return path
