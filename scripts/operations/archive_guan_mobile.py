#!/usr/bin/env python3
"""Atomically archive a Guan mobile-disk directory with SHA-256 receipts."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from market_data_platform.dataset_lock import DatasetLockError, exclusive_file_lock

MANIFEST_SCHEMA = "guan.mobile_archive.v2"
SOURCE_INVENTORY_SCHEMA = "full-stat.v1"
COPY_BUFFER_BYTES = 8 * 1024 * 1024
LOCK_FILENAME = ".archive-guan-mobile.lock"
_UUID_TEMP_SUFFIX = re.compile(r"^[0-9a-f]{32}$")
_IGNORABLE_CHMOD_ERRNOS = {
    errno.EINVAL,
    errno.ENOTSUP,
    errno.EOPNOTSUPP,
    errno.EPERM,
}


class ArchiveError(RuntimeError):
    """Base error for archive operations."""


class SourceChangedError(ArchiveError):
    """Raised when a source file changes while it is being copied."""


class ArchiveVerificationError(ArchiveError):
    """Raised when a destination file does not match its receipt."""


@dataclass(frozen=True)
class FileSignature:
    """Fields used to detect replacement or mutation during a copy."""

    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _signature(value: os.stat_result) -> FileSignature:
    return FileSignature(
        device=value.st_dev,
        inode=value.st_ino,
        mode=value.st_mode,
        size=value.st_size,
        mtime_ns=value.st_mtime_ns,
        ctime_ns=value.st_ctime_ns,
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_manifest(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": MANIFEST_SCHEMA,
            "created_at": _utc_now(),
            "files": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"Cannot read archive manifest {path}: {exc}") from exc
    if payload.get("schema_version") != MANIFEST_SCHEMA:
        raise ArchiveError(
            f"Unsupported archive manifest schema in {path}: {payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("files"), dict):
        raise ArchiveError(f"Archive manifest has an invalid files mapping: {path}")
    return payload


def _checkpoint(payload: dict[str, Any], path: Path) -> None:
    payload["updated_at"] = _utc_now()
    _atomic_write_manifest(payload, path)


@contextmanager
def _dataset_lock(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    lock_path = destination / LOCK_FILENAME
    try:
        with exclusive_file_lock(lock_path, operation="archive-guan-mobile"):
            yield
    except DatasetLockError as exc:
        raise ArchiveError(
            f"Another archive process holds the dataset lock: {lock_path}: {exc}"
        ) from None


def _validate_roots(source: Path, destination: Path, manifest: Path) -> None:
    if not source.is_dir():
        raise ArchiveError(f"Source directory does not exist or is not a directory: {source}")
    if (
        source == destination
        or source.is_relative_to(destination)
        or destination.is_relative_to(source)
    ):
        raise ArchiveError("Source and destination directories must not overlap")
    if manifest.is_relative_to(source):
        raise ArchiveError("Manifest must not be located inside the source directory")


def _discover_regular_files(source: Path) -> list[tuple[str, Path, os.stat_result]]:
    files: list[tuple[str, Path, os.stat_result]] = []
    pending = [source]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name, reverse=True)
        for entry in ordered:
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            file_stat = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(file_stat.st_mode):
                files.append((path.relative_to(source).as_posix(), path, file_stat))
    return sorted(files, key=lambda item: item[0])


def _source_inventory(
    files: Sequence[tuple[str, Path, os.stat_result]],
) -> dict[str, dict[str, int]]:
    inventory: dict[str, dict[str, int]] = {}
    for relative, _, file_stat in files:
        signature = _signature(file_stat)
        inventory[relative] = {
            "dev": signature.device,
            "inode": signature.inode,
            "mode": signature.mode,
            "size": signature.size,
            "mtime_ns": signature.mtime_ns,
            "ctime_ns": signature.ctime_ns,
        }
    return inventory


def _legacy_source_inventory(
    source_inventory: dict[str, dict[str, int]],
) -> dict[str, dict[str, int]]:
    return {
        relative: {
            "size": signature["size"],
            "mtime_ns": signature["mtime_ns"],
        }
        for relative, signature in source_inventory.items()
    }


def _is_legacy_source_inventory(value: Any) -> bool:
    return (
        bool(value)
        and isinstance(value, dict)
        and all(
            isinstance(signature, dict)
            and set(signature) == {"size", "mtime_ns"}
            and all(isinstance(item, int) for item in signature.values())
            for signature in value.values()
        )
    )


def _full_inventory(
    files: Sequence[tuple[str, Path, os.stat_result]],
) -> dict[str, FileSignature]:
    return {relative: _signature(file_stat) for relative, _, file_stat in files}


def _inventory_signatures(
    source_inventory: dict[str, dict[str, int]],
) -> dict[str, FileSignature]:
    try:
        return {
            relative: FileSignature(
                device=signature["dev"],
                inode=signature["inode"],
                mode=signature["mode"],
                size=signature["size"],
                mtime_ns=signature["mtime_ns"],
                ctime_ns=signature["ctime_ns"],
            )
            for relative, signature in source_inventory.items()
        }
    except (KeyError, TypeError) as exc:
        raise ArchiveError("Archive source inventory has invalid full-stat signatures") from exc


def _inventory_difference(expected: dict[str, Any], actual: dict[str, Any]) -> str:
    expected_paths = set(expected)
    actual_paths = set(actual)
    added = sorted(actual_paths - expected_paths)
    removed = sorted(expected_paths - actual_paths)
    changed = sorted(
        relative
        for relative in expected_paths & actual_paths
        if expected[relative] != actual[relative]
    )
    return f"added={added[:10]}, removed={removed[:10]}, changed={changed[:10]}"


def _assert_source_inventory(
    expected: Any,
    actual: dict[str, Any],
    *,
    context: str,
) -> None:
    if not isinstance(expected, dict):
        raise ArchiveError(f"Archive manifest has no valid source inventory: {context}")
    if expected != actual:
        raise SourceChangedError(
            f"Source inventory changed {context}: {_inventory_difference(expected, actual)}"
        )


def _discover_destination_entries(destination: Path) -> dict[str, os.stat_result]:
    entries_by_path: dict[str, os.stat_result] = {}
    pending = [destination]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name, reverse=True)
        for entry in ordered:
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
                continue
            entries_by_path[path.relative_to(destination).as_posix()] = entry.stat(
                follow_symlinks=False
            )
    return entries_by_path


def _assert_destination_inventory(
    destination: Path,
    manifest_path: Path,
    expected_paths: set[str],
    *,
    allow_missing: bool = False,
) -> None:
    entries = _discover_destination_entries(destination)
    reserved = {LOCK_FILENAME}
    if manifest_path.is_relative_to(destination):
        reserved.add(manifest_path.relative_to(destination).as_posix())
    actual_paths = set(entries) - reserved
    unexpected = sorted(actual_paths - expected_paths)
    missing = [] if allow_missing else sorted(expected_paths - actual_paths)
    non_regular = sorted(
        relative
        for relative in actual_paths & expected_paths
        if not stat.S_ISREG(entries[relative].st_mode)
    )
    if unexpected or missing or non_regular:
        raise ArchiveVerificationError(
            "Destination inventory does not match the source inventory: "
            f"unexpected={unexpected[:10]}, missing={missing[:10]}, "
            f"non_regular={non_regular[:10]}"
        )


def _destination_path(root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ArchiveError(f"Unsafe relative archive path: {relative!r}")
    target = root / relative_path
    if not target.resolve(strict=False).is_relative_to(root):
        raise ArchiveError(f"Archive path escapes through a destination symlink: {relative!r}")
    return target


def _is_uuid_temp_for_target(candidate: Path, target: Path) -> bool:
    prefix = f".{target.name}."
    suffix = ".tmp"
    name = candidate.name
    if not name.startswith(prefix) or not name.endswith(suffix):
        return False
    token = name[len(prefix) : -len(suffix)]
    return _UUID_TEMP_SUFFIX.fullmatch(token) is not None


def _cleanup_stale_archive_temps(
    destination: Path,
    manifest_path: Path,
    expected_paths: set[str],
) -> list[str]:
    """Remove only regular UUID temporaries belonging to known archive targets.

    Callers hold the archive flock, so a pre-existing tool-named temporary can
    only belong to an owner whose fd lock has already been released.
    """
    targets = [_destination_path(destination, relative) for relative in expected_paths]
    targets.append(manifest_path)
    removed: list[str] = []
    synced_parents: set[Path] = set()
    for target in targets:
        if not target.parent.is_dir():
            continue
        for candidate in target.parent.glob(f".{target.name}.*.tmp"):
            if not _is_uuid_temp_for_target(candidate, target):
                continue
            try:
                candidate_stat = candidate.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(candidate_stat.st_mode):
                continue
            candidate.unlink()
            removed.append(str(candidate))
            synced_parents.add(candidate.parent)
    for parent in synced_parents:
        _fsync_directory(parent)
    return sorted(removed)


def _stream_copy(source_handle: BinaryIO, destination_handle: BinaryIO, digest: Any) -> int:
    copied = 0
    while True:
        chunk = source_handle.read(COPY_BUFFER_BYTES)
        if not chunk:
            break
        destination_handle.write(chunk)
        digest.update(chunk)
        copied += len(chunk)
    return copied


def _same_signature(left: FileSignature, right: FileSignature) -> bool:
    return left == right


def _copy_one_file(source: Path, destination: Path) -> dict[str, Any]:
    initial = _signature(os.stat(source, follow_symlinks=False))
    if not stat.S_ISREG(initial.mode):
        raise ArchiveError(f"Source is no longer a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as destination_handle:
            opened = _signature(os.fstat(source_handle.fileno()))
            if not _same_signature(initial, opened):
                raise SourceChangedError(f"Source changed before it could be copied: {source}")
            copied = _stream_copy(source_handle, destination_handle, digest)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
            after_handle = _signature(os.fstat(source_handle.fileno()))

        after_path = _signature(os.stat(source, follow_symlinks=False))
        if not (
            _same_signature(initial, after_handle)
            and _same_signature(initial, after_path)
            and copied == initial.size
        ):
            raise SourceChangedError(f"Source changed while it was being copied: {source}")

        requested_mode = stat.S_IMODE(initial.mode)
        try:
            os.chmod(temporary, requested_mode)
        except OSError as exc:
            if exc.errno not in _IGNORABLE_CHMOD_ERRNOS:
                raise
        os.utime(temporary, ns=(initial.mtime_ns, initial.mtime_ns))
        with temporary.open("rb") as temporary_handle:
            os.fsync(temporary_handle.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
        destination_stat = os.stat(destination, follow_symlinks=False)
        if destination_stat.st_size != initial.size:
            raise ArchiveError(
                f"Destination size changed immediately after promotion: {destination}"
            )
        return {
            "status": "complete",
            "sha256": digest.hexdigest(),
            "size": initial.size,
            "source_mtime_ns": initial.mtime_ns,
            "destination_mtime_ns": destination_stat.st_mtime_ns,
            "source_mode": requested_mode,
            "destination_mode": stat.S_IMODE(destination_stat.st_mode),
            "mode_preserved": stat.S_IMODE(destination_stat.st_mode) == requested_mode,
            "source_signature": asdict(initial),
            "completed_at": _utc_now(),
        }
    finally:
        temporary.unlink(missing_ok=True)


def _receipt_can_resume(
    receipt: Any,
    *,
    source_stat: os.stat_result,
    destination: Path,
) -> bool:
    if not isinstance(receipt, dict) or receipt.get("status") != "complete":
        return False
    if receipt.get("verification_status") == "failed":
        return False
    try:
        target_stat = os.stat(destination, follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return False
    if not stat.S_ISREG(target_stat.st_mode):
        return False
    try:
        recorded_signature = FileSignature(**receipt["source_signature"])
    except (KeyError, TypeError):
        return False
    return (
        int(receipt.get("size", -1)) == source_stat.st_size == target_stat.st_size
        and int(receipt.get("source_mtime_ns", -1)) == source_stat.st_mtime_ns
        and int(receipt.get("destination_mtime_ns", -1)) == target_stat.st_mtime_ns
        and recorded_signature == _signature(source_stat)
    )


def _manifest_for_roots(
    manifest_path: Path,
    *,
    source: Path,
    destination: Path,
) -> dict[str, Any]:
    payload = _load_manifest(manifest_path)
    recorded_source = payload.get("source_dir")
    recorded_destination = payload.get("destination_dir")
    if recorded_source is not None and recorded_source != str(source):
        raise ArchiveError(
            f"Manifest belongs to a different source: {recorded_source!r} != {str(source)!r}"
        )
    if recorded_destination is not None and recorded_destination != str(destination):
        raise ArchiveError(
            "Manifest belongs to a different destination: "
            f"{recorded_destination!r} != {str(destination)!r}"
        )
    payload["source_dir"] = str(source)
    payload["destination_dir"] = str(destination)
    return payload


def _completed_receipts(
    payload: dict[str, Any],
    source_inventory: dict[str, dict[str, int]],
) -> dict[str, dict[str, Any]]:
    receipts = payload["files"]
    expected_paths = set(source_inventory)
    receipt_paths = set(receipts)
    missing = sorted(expected_paths - receipt_paths)
    unexpected = sorted(receipt_paths - expected_paths)
    incomplete: list[str] = []
    invalid: list[str] = []
    completed: dict[str, dict[str, Any]] = {}
    for relative in sorted(expected_paths & receipt_paths):
        receipt = receipts[relative]
        if not isinstance(receipt, dict) or receipt.get("status") != "complete":
            incomplete.append(relative)
            continue
        inventory_entry = source_inventory[relative]
        sha256 = receipt.get("sha256")
        if (
            receipt.get("relative_path") != relative
            or receipt.get("size") != inventory_entry["size"]
            or receipt.get("source_mtime_ns") != inventory_entry["mtime_ns"]
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
            or not isinstance(receipt.get("destination_mtime_ns"), int)
        ):
            invalid.append(relative)
            continue
        completed[relative] = receipt
    if missing or unexpected or incomplete or invalid:
        raise ArchiveVerificationError(
            "Archive receipts are incomplete or invalid: "
            f"missing={missing[:10]}, unexpected={unexpected[:10]}, "
            f"incomplete={incomplete[:10]}, invalid={invalid[:10]}"
        )
    return completed


def _assert_receipt_source_signatures(
    receipts: dict[str, dict[str, Any]],
    current_signatures: dict[str, FileSignature],
) -> None:
    invalid: list[str] = []
    changed: list[str] = []
    for relative, receipt in receipts.items():
        try:
            recorded = FileSignature(**receipt["source_signature"])
        except (KeyError, TypeError):
            invalid.append(relative)
            continue
        if recorded != current_signatures[relative]:
            changed.append(relative)
    if invalid:
        raise ArchiveVerificationError(
            f"Archive receipts have invalid source signatures: {invalid[:10]}"
        )
    if changed:
        raise SourceChangedError(
            f"Source files no longer match the signatures captured during copy: {changed[:10]}"
        )


def _freeze_or_upgrade_source_inventory(
    payload: dict[str, Any],
    current_inventory: dict[str, dict[str, int]],
) -> None:
    """Freeze full stat identities, with a safe upgrade for legacy v2 receipts."""
    recorded = payload.get("source_inventory")
    if recorded is None:
        payload["source_inventory"] = current_inventory
        payload["source_inventory_schema"] = SOURCE_INVENTORY_SCHEMA
        payload["source_inventory_captured_at"] = _utc_now()
        return
    if recorded == current_inventory:
        payload["source_inventory_schema"] = SOURCE_INVENTORY_SCHEMA
        return
    if not _is_legacy_source_inventory(recorded):
        _assert_source_inventory(
            recorded,
            current_inventory,
            context="since the manifest was created",
        )
        raise AssertionError("unreachable")

    _assert_source_inventory(
        recorded,
        _legacy_source_inventory(current_inventory),
        context="since the legacy manifest was created",
    )
    receipts = payload.get("files")
    if not isinstance(receipts, dict):
        raise ArchiveError("Archive manifest has an invalid files mapping")
    receipts_by_name: dict[str, Any] = receipts
    unexpected_receipts = sorted(set(receipts_by_name) - set(current_inventory))
    if unexpected_receipts:
        raise ArchiveError(
            "Legacy archive manifest contains receipts outside its frozen inventory: "
            f"{unexpected_receipts[:10]}"
        )

    if payload.get("copy_status") == "complete":
        completed = _completed_receipts(payload, current_inventory)
        _assert_receipt_source_signatures(
            completed,
            _inventory_signatures(current_inventory),
        )
        strategy = "complete-receipts-anchored"
        discarded_receipts = 0
    else:
        # A partial legacy manifest never captured ctime/device/inode for files
        # that were not copied. Re-copying all targets is the only way to adopt
        # the current full-stat snapshot without mixing two source snapshots.
        discarded_receipts = len(receipts)
        payload["files"] = {}
        strategy = "restart-copy-from-full-stat-snapshot"

    payload["source_inventory"] = current_inventory
    payload["source_inventory_schema"] = SOURCE_INVENTORY_SCHEMA
    payload["source_inventory_captured_at"] = _utc_now()
    payload["source_inventory_upgrade"] = {
        "at": _utc_now(),
        "from": "size-mtime.v0",
        "to": SOURCE_INVENTORY_SCHEMA,
        "strategy": strategy,
        "discarded_receipt_count": discarded_receipts,
    }


def _record_failure(
    payload: dict[str, Any],
    manifest_path: Path,
    *,
    status: str,
    error: Exception,
    relative_path: str | None,
) -> None:
    payload["status"] = status
    payload["error"] = {
        "type": type(error).__name__,
        "message": str(error),
        "relative_path": relative_path,
        "at": _utc_now(),
    }
    _checkpoint(payload, manifest_path)


def archive_directory(  # noqa: PLR0915
    source_dir: str | Path,
    destination_dir: str | Path,
    manifest: str | Path,
) -> dict[str, Any]:
    """Copy every regular source file and checkpoint a receipt after each one."""
    source = Path(source_dir).expanduser().resolve()
    destination = Path(destination_dir).expanduser().resolve()
    manifest_path = Path(manifest).expanduser().resolve()
    _validate_roots(source, destination, manifest_path)
    destination.mkdir(parents=True, exist_ok=True)

    with _dataset_lock(destination):
        payload = _manifest_for_roots(manifest_path, source=source, destination=destination)
        current_path: str | None = None
        try:
            _validate_roots(source, destination, manifest_path)
            inventory = _discover_regular_files(source)
            if not inventory:
                raise ArchiveError(f"Source contains no regular files: {source}")
            source_inventory = _source_inventory(inventory)
            initial_full_inventory = _full_inventory(inventory)
            _freeze_or_upgrade_source_inventory(payload, source_inventory)
            reserved_targets = {manifest_path, destination / LOCK_FILENAME}
            collisions = [
                relative
                for relative, _, _ in inventory
                if _destination_path(destination, relative).resolve() in reserved_targets
            ]
            if collisions:
                raise ArchiveError(f"Source paths collide with archive metadata: {collisions}")

            stale_receipts = sorted(set(payload["files"]) - set(source_inventory))
            if stale_receipts:
                raise ArchiveError(
                    "Manifest contains receipts outside the source inventory: "
                    f"{stale_receipts[:10]}"
                )
            removed_temps = _cleanup_stale_archive_temps(
                destination,
                manifest_path,
                set(source_inventory),
            )
            if removed_temps:
                payload["stale_temp_cleanup"] = {
                    "at": _utc_now(),
                    "removed_count": len(removed_temps),
                    "paths": removed_temps,
                }
            _assert_destination_inventory(
                destination,
                manifest_path,
                set(source_inventory),
                allow_missing=True,
            )

            payload.update(
                {
                    "status": "running",
                    "copy_status": "running",
                    "mode": "copy",
                    "started_at": _utc_now(),
                    "file_count": len(inventory),
                    "bytes": sum(item[2].st_size for item in inventory),
                    "completed_file_count": 0,
                    "completed_bytes": 0,
                    "skipped_file_count": 0,
                }
            )
            payload.pop("error", None)
            _checkpoint(payload, manifest_path)

            completed = 0
            completed_bytes = 0
            skipped = 0
            receipts = payload["files"]
            for relative, source_path, discovered_stat in inventory:
                current_path = relative
                destination_path = _destination_path(destination, relative)
                current_stat = os.stat(source_path, follow_symlinks=False)
                if _signature(current_stat) != _signature(discovered_stat):
                    raise SourceChangedError(f"Source changed after inventory: {source_path}")
                receipt = receipts.get(relative)
                if _receipt_can_resume(
                    receipt,
                    source_stat=current_stat,
                    destination=destination_path,
                ):
                    skipped += 1
                else:
                    receipt = _copy_one_file(source_path, destination_path)
                    receipt["relative_path"] = relative
                    receipts[relative] = receipt
                completed += 1
                completed_bytes += current_stat.st_size
                payload["completed_file_count"] = completed
                payload["completed_bytes"] = completed_bytes
                payload["skipped_file_count"] = skipped
                _checkpoint(payload, manifest_path)

            final_inventory = _discover_regular_files(source)
            final_full_inventory = _full_inventory(final_inventory)
            if initial_full_inventory != final_full_inventory:
                raise SourceChangedError(
                    "Source inventory changed while it was being archived: "
                    f"{_inventory_difference(initial_full_inventory, final_full_inventory)}"
                )
            completed_receipts = _completed_receipts(payload, source_inventory)
            _assert_receipt_source_signatures(
                completed_receipts,
                initial_full_inventory,
            )
            _assert_destination_inventory(
                destination,
                manifest_path,
                set(completed_receipts),
            )
            payload.update(
                {
                    "status": "complete",
                    "copy_status": "complete",
                    "completed_at": _utc_now(),
                    "completed_file_count": completed,
                    "completed_bytes": completed_bytes,
                    "skipped_file_count": skipped,
                }
            )
            _checkpoint(payload, manifest_path)
            return {
                "status": "complete",
                "file_count": len(inventory),
                "bytes": payload["bytes"],
                "copied_file_count": len(inventory) - skipped,
                "skipped_file_count": skipped,
                "manifest": str(manifest_path),
            }
        except Exception as exc:
            payload["copy_status"] = "failed"
            _record_failure(
                payload,
                manifest_path,
                status="failed",
                error=exc,
                relative_path=current_path,
            )
            raise


def _hash_stable_file(path: Path) -> tuple[str, os.stat_result]:
    before = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ArchiveVerificationError(f"Destination is not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(COPY_BUFFER_BYTES)
            if not chunk:
                break
            digest.update(chunk)
        after_handle = os.fstat(handle.fileno())
    after_path = os.stat(path, follow_symlinks=False)
    if _signature(before) != _signature(after_handle) or _signature(before) != _signature(
        after_path
    ):
        raise ArchiveVerificationError(f"Destination changed during verification: {path}")
    return digest.hexdigest(), after_path


def verify_archive(  # noqa: C901,PLR0912,PLR0915
    source_dir: str | Path,
    destination_dir: str | Path,
    manifest: str | Path,
) -> dict[str, Any]:
    """Re-hash every completed destination receipt and fail on any mismatch."""
    source = Path(source_dir).expanduser().resolve()
    destination = Path(destination_dir).expanduser().resolve()
    manifest_path = Path(manifest).expanduser().resolve()
    _validate_roots(source, destination, manifest_path)
    if not manifest_path.is_file():
        raise ArchiveVerificationError(f"Archive manifest does not exist: {manifest_path}")

    with _dataset_lock(destination):
        payload = _manifest_for_roots(manifest_path, source=source, destination=destination)
        current_path: str | None = None
        try:
            _validate_roots(source, destination, manifest_path)
            if payload.get("copy_status") != "complete":
                raise ArchiveVerificationError(
                    "Archive copy is not complete; resume the copy before verification"
                )
            source_files = _discover_regular_files(source)
            source_inventory = _source_inventory(source_files)
            source_full_inventory = _full_inventory(source_files)
            _freeze_or_upgrade_source_inventory(payload, source_inventory)
            removed_temps = _cleanup_stale_archive_temps(
                destination,
                manifest_path,
                set(source_inventory),
            )
            if removed_temps:
                payload["stale_temp_cleanup"] = {
                    "at": _utc_now(),
                    "removed_count": len(removed_temps),
                    "paths": removed_temps,
                }
            receipts = _completed_receipts(payload, source_inventory)
            _assert_receipt_source_signatures(receipts, source_full_inventory)
            expected_bytes = sum(item["size"] for item in source_inventory.values())
            if (
                payload.get("file_count") != len(source_inventory)
                or payload.get("bytes") != expected_bytes
                or payload.get("completed_file_count") != len(source_inventory)
                or payload.get("completed_bytes") != expected_bytes
            ):
                raise ArchiveVerificationError(
                    "Archive summary is inconsistent with the source inventory and receipts"
                )
            _assert_destination_inventory(destination, manifest_path, set(receipts))
            payload.update(
                {
                    "status": "verifying",
                    "mode": "verify",
                    "verify_started_at": _utc_now(),
                    "verified_file_count": 0,
                    "verified_bytes": 0,
                }
            )
            payload.pop("error", None)
            _checkpoint(payload, manifest_path)
            verified_bytes = 0
            verified_signatures: dict[str, FileSignature] = {}
            for index, relative in enumerate(sorted(receipts), start=1):
                current_path = relative
                receipt = receipts[relative]
                target = _destination_path(destination, relative)
                try:
                    actual_hash, target_stat = _hash_stable_file(target)
                except (FileNotFoundError, NotADirectoryError) as exc:
                    raise ArchiveVerificationError(f"Archived file is missing: {target}") from exc
                expected_size = int(receipt.get("size", -1))
                expected_mtime = int(receipt.get("destination_mtime_ns", -1))
                expected_hash = str(receipt.get("sha256", ""))
                mismatches: dict[str, Any] = {}
                if target_stat.st_size != expected_size:
                    mismatches["size"] = {"expected": expected_size, "actual": target_stat.st_size}
                if target_stat.st_mtime_ns != expected_mtime:
                    mismatches["mtime_ns"] = {
                        "expected": expected_mtime,
                        "actual": target_stat.st_mtime_ns,
                    }
                if actual_hash != expected_hash:
                    mismatches["sha256"] = {"expected": expected_hash, "actual": actual_hash}
                if mismatches:
                    raise ArchiveVerificationError(
                        f"Archived file failed verification: {relative}: {mismatches}"
                    )
                receipt["verified_at"] = _utc_now()
                receipt["verified_sha256"] = actual_hash
                receipt["verification_status"] = "verified"
                receipt.pop("verification_error", None)
                verified_signatures[relative] = _signature(target_stat)
                verified_bytes += target_stat.st_size
                payload["verified_file_count"] = index
                payload["verified_bytes"] = verified_bytes
                _checkpoint(payload, manifest_path)

            current_path = None
            final_source_files = _discover_regular_files(source)
            final_source_inventory = _full_inventory(final_source_files)
            if source_full_inventory != final_source_inventory:
                raise SourceChangedError(
                    "Source inventory changed during verification: "
                    f"{_inventory_difference(source_full_inventory, final_source_inventory)}"
                )
            _assert_destination_inventory(destination, manifest_path, set(receipts))
            final_destination_entries = _discover_destination_entries(destination)
            changed_destinations = sorted(
                relative
                for relative, expected_signature in verified_signatures.items()
                if _signature(final_destination_entries[relative]) != expected_signature
            )
            if changed_destinations:
                failure = {
                    "type": "ArchiveVerificationError",
                    "message": "Destination changed after its SHA-256 verification",
                    "at": _utc_now(),
                }
                for relative in changed_destinations:
                    receipts[relative]["verification_status"] = "failed"
                    receipts[relative]["verification_error"] = failure
                raise ArchiveVerificationError(
                    "Destination files changed after they were verified: "
                    f"{changed_destinations[:10]}"
                )
            payload.update(
                {
                    "status": "verified",
                    "verified_at": _utc_now(),
                    "file_count": len(receipts),
                    "bytes": verified_bytes,
                    "verified_file_count": len(receipts),
                    "verified_bytes": verified_bytes,
                }
            )
            _checkpoint(payload, manifest_path)
            return {
                "status": "verified",
                "file_count": len(receipts),
                "bytes": verified_bytes,
                "manifest": str(manifest_path),
            }
        except Exception as exc:
            receipt = payload["files"].get(current_path) if current_path is not None else None
            if isinstance(receipt, dict):
                receipt["verification_status"] = "failed"
                receipt["verification_error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "at": _utc_now(),
                }
            _record_failure(
                payload,
                manifest_path,
                status="verify_failed",
                error=exc,
                relative_path=current_path,
            )
            raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Atomically archive and verify a Guan mobile-disk directory.",
    )
    parser.add_argument("--source-dir", required=True, help="Mounted Guan source directory")
    parser.add_argument("--destination-dir", required=True, help="Durable archive destination")
    parser.add_argument("--manifest", required=True, help="Checkpoint and receipt JSON path")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Re-hash completed destination receipts instead of copying files",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.verify:
            result = verify_archive(args.source_dir, args.destination_dir, args.manifest)
        else:
            result = archive_directory(args.source_dir, args.destination_dir, args.manifest)
    except Exception as exc:
        print(f"archive_guan_mobile: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
