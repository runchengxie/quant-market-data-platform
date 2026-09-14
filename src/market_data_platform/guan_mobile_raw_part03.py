"""Promote verified Guan mobile archives into an isolated provider-native tree."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from market_data_platform.dataset_lock import DatasetLockError, exclusive_file_lock
from market_data_platform.guan_mobile_raw_part01 import (
    LOCK_FILENAME,
    PROMOTION_SCHEMA,
    GuanMobilePromotionVerificationError,
    _absolute_path,
    _atomic_write_json,
    _json_sha256,
    _load_json,
    _receipt_path,
    _regular_file,
    _sha256_file,
    _stat_payload,
    _utc_now,
)
from market_data_platform.guan_mobile_raw_part02 import (
    _receipt_artifact_path,
)


def _promoted_entry_stat_failure(
    logical_key: Any,
    *,
    source: Path,
    destination: Path,
    storage_strategy: str,
    expected_size: int,
) -> dict[str, Any] | None:
    if not _regular_file(source) or not _regular_file(destination):
        return {"logical_key": logical_key, "type": "missing_or_nonregular"}
    source_stat = source.stat(follow_symlinks=False)
    destination_stat = destination.stat(follow_symlinks=False)
    if storage_strategy not in {"hardlink", "copy", "preexisting_same_sha"}:
        return {"logical_key": logical_key, "type": "invalid_storage_strategy"}
    if storage_strategy == "hardlink" and (source_stat.st_dev, source_stat.st_ino) != (
        destination_stat.st_dev,
        destination_stat.st_ino,
    ):
        return {"logical_key": logical_key, "type": "hardlink_identity_mismatch"}
    if destination_stat.st_size != expected_size:
        return {"logical_key": logical_key, "type": "size_mismatch"}
    return None


def _verify_promoted_entry(
    entry: dict[str, Any],
    *,
    source_root: Path,
    provider_root: Path,
) -> tuple[dict[str, Any] | None, int]:
    logical_key = entry.get("logical_key")
    try:
        source = _receipt_artifact_path(entry["incoming_path"], root=source_root)
        destination = _receipt_artifact_path(entry["organized_path"], root=provider_root)
        expected_sha256 = str(entry["sha256"])
        expected_size = int(entry["size"])
        storage_strategy = str(entry["storage_strategy"])
    except (KeyError, TypeError, ValueError, GuanMobilePromotionVerificationError) as exc:
        return {"logical_key": logical_key, "type": "invalid_receipt_entry", "message": str(exc)}, 0
    stat_failure = _promoted_entry_stat_failure(
        logical_key,
        source=source,
        destination=destination,
        storage_strategy=storage_strategy,
        expected_size=expected_size,
    )
    if stat_failure is not None:
        return stat_failure, 0
    destination_sha256 = _sha256_file(destination)
    if destination_sha256 != expected_sha256:
        return {
            "logical_key": logical_key,
            "type": "destination_sha256_mismatch",
            "expected_sha256": expected_sha256,
            "actual_sha256": destination_sha256,
        }, 0
    if storage_strategy == "copy":
        source_sha256 = _sha256_file(source)
        if source_sha256 != expected_sha256:
            return {
                "logical_key": logical_key,
                "type": "source_sha256_mismatch",
                "expected_sha256": expected_sha256,
                "actual_sha256": source_sha256,
            }, 0
    entry["verification_status"] = "verified"
    entry["verified_at"] = _utc_now()
    entry["verified_destination_stat"] = _stat_payload(destination)
    return None, expected_size


def _verify_source_duplicate(
    duplicate: dict[str, Any],
    *,
    source_root: Path,
) -> dict[str, Any] | None:
    try:
        path = _receipt_artifact_path(duplicate["path"], root=source_root)
        actual_sha256 = _sha256_file(path) if _regular_file(path) else None
    except (KeyError, GuanMobilePromotionVerificationError) as exc:
        actual_sha256 = None
        path = Path(str(duplicate.get("path", "")))
        duplicate["verification_status"] = "failed"
        return {
            "logical_key": duplicate.get("logical_key"),
            "type": "invalid_source_duplicate_entry",
            "path": str(path),
            "message": str(exc),
        }
    if actual_sha256 != duplicate.get("sha256"):
        duplicate["verification_status"] = "failed"
        return {
            "logical_key": duplicate.get("logical_key"),
            "type": "source_duplicate_sha256_mismatch",
            "path": str(path),
            "actual_sha256": actual_sha256,
        }
    duplicate["verification_status"] = "verified_exact_duplicate"
    duplicate["verified_at"] = _utc_now()
    return None


def _archive_anchor_failure(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    path = Path(str(payload.get("archive_manifest", ""))).expanduser()
    expected = payload.get("archive_manifest_sha256")
    actual = _json_sha256(path) if _regular_file(path) else None
    if actual == expected:
        return None
    return {
        "type": "archive_manifest_sha256_mismatch",
        "path": str(path),
        "expected_sha256": expected,
        "actual_sha256": actual,
    }


def _verification_inventory(
    payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    strategy = payload.get("strategy")
    entries = payload.get("entries")
    duplicates = payload.get("source_duplicates")
    if strategy not in {"hardlink", "copy"} or not isinstance(entries, list):
        raise GuanMobilePromotionVerificationError("Promotion receipt structure is invalid")
    if not isinstance(duplicates, list) or not all(isinstance(item, dict) for item in entries):
        raise GuanMobilePromotionVerificationError("Promotion receipt inventory is invalid")
    if not all(isinstance(item, dict) for item in duplicates):
        raise GuanMobilePromotionVerificationError("Promotion duplicate inventory is invalid")
    return entries, duplicates


def verify_guan_mobile_promotion(receipt: str | Path) -> dict[str, Any]:
    """Rehash all organized artifacts and validate the lineage relocation mapping."""
    receipt_path = _receipt_path(receipt)
    payload = _load_json(receipt_path, label="promotion receipt")
    if payload.get("schema_version") != PROMOTION_SCHEMA or payload.get("status") != "complete":
        raise GuanMobilePromotionVerificationError(
            "Promotion receipt must be complete before verification"
        )
    entries, duplicates = _verification_inventory(payload)

    source_root = _absolute_path(str(payload["source_dir"]))
    provider_root = _absolute_path(str(payload["provider_root"]))
    lock_path = provider_root / LOCK_FILENAME
    failures = [failure for failure in [_archive_anchor_failure(payload)] if failure]
    verified_bytes = 0
    try:
        with exclusive_file_lock(lock_path, operation="verify-guan-mobile-provider-native-raw"):
            for entry in entries:
                failure, artifact_bytes = _verify_promoted_entry(
                    entry,
                    source_root=source_root,
                    provider_root=provider_root,
                )
                if failure is not None:
                    entry["verification_status"] = "failed"
                    failures.append(failure)
                verified_bytes += artifact_bytes
            for duplicate in duplicates:
                failure = _verify_source_duplicate(duplicate, source_root=source_root)
                if failure is not None:
                    failures.append(failure)

            payload["verification_status"] = "failed" if failures else "verified"
            payload["verified_at"] = _utc_now()
            payload["verification"] = {
                "status": payload["verification_status"],
                "content_validation": "verified" if not failures else "failed",
                "verified_artifacts": sum(
                    entry.get("verification_status") == "verified" for entry in entries
                ),
                "verified_source_duplicates": sum(
                    duplicate.get("verification_status") == "verified_exact_duplicate"
                    for duplicate in duplicates
                ),
                "verified_bytes": verified_bytes,
                "failures": failures,
                "receipt_update": "atomic_replace_and_parent_fsync",
            }
            _atomic_write_json(payload, receipt_path)
    except DatasetLockError as exc:
        raise GuanMobilePromotionVerificationError(
            f"Another raw promotion holds {lock_path}: {exc}"
        ) from None
    if failures:
        raise GuanMobilePromotionVerificationError(
            f"Promotion verification found {len(failures)} failure(s); see {receipt_path}"
        )
    return payload
