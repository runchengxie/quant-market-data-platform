#!/usr/bin/env python3
"""Detach hardlinked Parquet files inside an explicit A-share minute v3 version."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.dataset_lock import DatasetLockError, minute_dataset_lock

RECEIPT_SCHEMA = "a_share.minute_1m.hardlink_detach.v1"
COPY_BUFFER_BYTES = 8 * 1024 * 1024
TREE_PATTERN = "trade_date=YYYYMMDD/part-*.parquet"

_TARGET_VERSION_PATTERN = re.compile(r"^minute_1m_v3(?:_[A-Za-z0-9.-]+)?$")
_SOURCE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PARTITION_DIR_PATTERN = re.compile(r"^trade_date=(\d{8})$")
_PART_FILE_PATTERN = re.compile(r"^part-.+\.parquet$")
_TEMP_FILE_PATTERN = re.compile(r"^\.part-.+\.parquet\.detach-[0-9a-f]{32}\.tmp$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_STAT_IDENTITY_FIELDS = (
    "device",
    "inode",
    "mode",
    "size_bytes",
    "mtime_ns",
    "ctime_ns",
    "nlink",
)


class HardlinkDetachError(RuntimeError):
    """Raised when a v3 version cannot be detached without ambiguity."""


class HardlinkDetachVerificationError(HardlinkDetachError):
    """Raised when copied bytes or final link counts do not verify."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(payload: Mapping[str, Any], path: Path) -> None:
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


def _checkpoint(payload: dict[str, Any], path: Path) -> None:
    payload["updated_at"] = _utc_now()
    _atomic_write_json(payload, path)


def _absolute_endpoint(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.parent.resolve() / path.name


def _validate_source_version(source_version: str, *, target_version: str) -> str:
    value = source_version.strip()
    if _SOURCE_VERSION_PATTERN.fullmatch(value) is None or value in {"minute_1m", target_version}:
        raise HardlinkDetachError(
            "--source-version must be an immutable version label distinct from current and target"
        )
    return value


def _validate_version_root(value: str | Path) -> Path:
    root = _absolute_endpoint(value)
    if _TARGET_VERSION_PATTERN.fullmatch(root.name) is None:
        raise HardlinkDetachError(
            "Hardlink detach accepts only an explicit minute_1m_v3_* --version-dir"
        )
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise HardlinkDetachError(f"Cannot inspect v3 version directory {root}: {exc}") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink():
        raise HardlinkDetachError(f"V3 version must be a real directory, not a symlink: {root}")
    if root.resolve(strict=True) != root:
        raise HardlinkDetachError(f"V3 version path resolves through indirection: {root}")
    return root


def _validate_receipt_path(value: str | Path, *, version_root: Path) -> Path:
    path = _absolute_endpoint(value)
    if path == version_root or path.is_relative_to(version_root):
        raise HardlinkDetachError("Detach receipt must be outside --version-dir")
    if path.is_symlink():
        raise HardlinkDetachError(f"Detach receipt must not be a symlink: {path}")
    if path.exists() and not path.is_file():
        raise HardlinkDetachError(f"Detach receipt is not a regular file: {path}")
    return path


def _stat_payload(value: os.stat_result) -> dict[str, int]:
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "mode": value.st_mode,
        "size_bytes": value.st_size,
        "atime_ns": value.st_atime_ns,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
        "nlink": value.st_nlink,
    }


def _stat_matches(expected: Mapping[str, Any], actual: os.stat_result) -> bool:
    current = _stat_payload(actual)
    return all(expected.get(field) == current[field] for field in _STAT_IDENTITY_FIELDS)


def _validate_date(value: str) -> None:
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise HardlinkDetachError(f"Invalid trade-date directory: trade_date={value}") from exc
    if parsed.strftime("%Y%m%d") != value:
        raise HardlinkDetachError(f"Invalid trade-date directory: trade_date={value}")


def _scan_tree(  # noqa: PLR0912
    version_root: Path,
) -> tuple[dict[str, tuple[Path, os.stat_result]], list[Path], list[str]]:
    files: dict[str, tuple[Path, os.stat_result]] = {}
    temporary_files: list[Path] = []
    dates: list[str] = []
    try:
        with os.scandir(version_root) as entries:
            root_entries = sorted(entries, key=lambda entry: entry.name)
    except OSError as exc:
        raise HardlinkDetachError(f"Cannot scan v3 version {version_root}: {exc}") from exc

    for entry in root_entries:
        matched = _PARTITION_DIR_PATTERN.fullmatch(entry.name)
        if matched is None or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            raise HardlinkDetachError(f"Unexpected v3 version entry: {entry.path}")
        trade_date = matched.group(1)
        _validate_date(trade_date)
        dates.append(trade_date)
        partition_dir = Path(entry.path)
        try:
            with os.scandir(partition_dir) as entries:
                partition_entries = sorted(entries, key=lambda item: item.name)
        except OSError as exc:
            raise HardlinkDetachError(
                f"Cannot scan partition directory {partition_dir}: {exc}"
            ) from exc

        partition_file_count = 0
        for child in partition_entries:
            path = Path(child.path)
            if _TEMP_FILE_PATTERN.fullmatch(child.name):
                if child.is_symlink() or not child.is_file(follow_symlinks=False):
                    raise HardlinkDetachError(f"Unsafe detach temporary entry: {path}")
                temporary_files.append(path)
                continue
            if (
                _PART_FILE_PATTERN.fullmatch(child.name) is None
                or child.is_symlink()
                or not child.is_file(follow_symlinks=False)
            ):
                raise HardlinkDetachError(f"Unexpected partition entry: {path}")
            file_stat = child.stat(follow_symlinks=False)
            if not stat.S_ISREG(file_stat.st_mode):
                raise HardlinkDetachError(f"Partition payload is not a regular file: {path}")
            relative = path.relative_to(version_root).as_posix()
            files[relative] = (path, file_stat)
            partition_file_count += 1
        if partition_file_count == 0:
            raise HardlinkDetachError(f"Partition contains no part-*.parquet file: {partition_dir}")

    if not files:
        raise HardlinkDetachError(f"V3 version contains no partition Parquet files: {version_root}")
    inode_paths: dict[tuple[int, int], list[str]] = {}
    for relative, (_path, file_stat) in files.items():
        inode_paths.setdefault((file_stat.st_dev, file_stat.st_ino), []).append(relative)
    duplicate_inodes = [paths for paths in inode_paths.values() if len(paths) > 1]
    if duplicate_inodes:
        raise HardlinkDetachError(
            "V3 version contains partition paths sharing one inode internally: "
            f"{duplicate_inodes[:5]}"
        )
    return files, temporary_files, dates


def _cleanup_temporary_files(paths: Sequence[Path]) -> int:
    touched_directories: set[Path] = set()
    removed = 0
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        touched_directories.add(path.parent)
        removed += 1
    for directory in sorted(touched_directories):
        _fsync_directory(directory)
    return removed


def _tree_layout(
    inventory: Mapping[str, tuple[Path, os.stat_result]],
    dates: Sequence[str],
) -> dict[str, Any]:
    path_inventory = [
        {"relative_path": relative, "size_bytes": file_stat.st_size}
        for relative, (_path, file_stat) in sorted(inventory.items())
    ]
    encoded = json.dumps(path_inventory, ensure_ascii=True, separators=(",", ":"))
    filenames = Counter(Path(relative).name for relative in inventory)
    return {
        "pattern": TREE_PATTERN,
        "partition_dir_count": len(set(dates)),
        "partition_file_count": len(inventory),
        "date_min": min(dates),
        "date_max": max(dates),
        "total_bytes": sum(file_stat.st_size for _path, file_stat in inventory.values()),
        "part_filenames": dict(sorted(filenames.items())),
        "path_size_inventory_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def _new_payload(
    *,
    source_version: str,
    version_root: Path,
    inventory: Mapping[str, tuple[Path, os.stat_result]],
    dates: Sequence[str],
    temporary_file_count: int,
) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    for relative, (_path, file_stat) in sorted(inventory.items()):
        files[relative] = {
            "relative_path": relative,
            "status": "planned_detach" if file_stat.st_nlink > 1 else "already_private",
            "before": _stat_payload(file_stat),
            **({"after": _stat_payload(file_stat)} if file_stat.st_nlink == 1 else {}),
        }
    return {
        "schema_version": RECEIPT_SCHEMA,
        "status": "planned",
        "created_at": _utc_now(),
        "source_version": source_version,
        "target_version": version_root.name,
        "version_dir": str(version_root),
        "policy": {
            "scope": "trade_date=*/part-*.parquet_with_st_nlink_gt_1_only",
            "copy_location": "same_directory_temporary",
            "replacement": "os.replace",
            "durability": "fsync_copy_then_replace_then_fsync_parent",
            "final_required_nlink": 1,
            "current_and_v2_mutation": "forbidden",
        },
        "tree_layout": _tree_layout(inventory, dates),
        "cleanup": {
            "stale_temporary_files_detected": temporary_file_count,
            "stale_temporary_files_removed": 0,
        },
        "summary": {},
        "files": files,
    }


def _load_receipt(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HardlinkDetachError(f"Cannot read detach receipt {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HardlinkDetachError(f"Detach receipt is not a JSON object: {path}")
    return payload


def _validate_receipt_identity(
    payload: Mapping[str, Any],
    *,
    source_version: str,
    version_root: Path,
    tree_layout: Mapping[str, Any],
    inventory: Mapping[str, tuple[Path, os.stat_result]],
) -> None:
    expected_identity = {
        "schema_version": RECEIPT_SCHEMA,
        "source_version": source_version,
        "target_version": version_root.name,
        "version_dir": str(version_root),
    }
    mismatched = [
        field for field, expected in expected_identity.items() if payload.get(field) != expected
    ]
    if mismatched:
        raise HardlinkDetachError(
            f"Detach receipt belongs to different inputs: fields={mismatched}"
        )
    if payload.get("tree_layout") != tree_layout:
        raise HardlinkDetachError("V3 version tree layout changed after detach planning")
    files = payload.get("files")
    if not isinstance(files, dict) or set(files) != set(inventory):
        raise HardlinkDetachError("Detach receipt file inventory differs from the v3 version tree")
    if payload.get("status") not in {"planned", "in_progress", "failed", "passed"}:
        raise HardlinkDetachError(f"Detach receipt has invalid status: {payload.get('status')!r}")


def _entry_state(  # noqa: C901,PLR0912
    entry: Mapping[str, Any],
    current: os.stat_result,
    *,
    relative: str,
) -> str:
    if entry.get("relative_path") != relative:
        raise HardlinkDetachError(f"Detach receipt has a mismatched relative path: {relative}")
    before = entry.get("before")
    if not isinstance(before, Mapping):
        raise HardlinkDetachError(f"Detach receipt lacks before stat for {relative}")
    status_value = entry.get("status")
    if not isinstance(status_value, str):
        raise HardlinkDetachError(f"Detach receipt has invalid file status for {relative}")
    status_text = status_value
    if status_text in {"planned_detach", "already_private"}:
        before_nlink = int(before.get("nlink", 0))
        if (status_text == "already_private" and before_nlink != 1) or (
            status_text == "planned_detach" and before_nlink <= 1
        ):
            raise HardlinkDetachError(f"Detach receipt has invalid initial nlink: {relative}")
        if status_text == "already_private" and entry.get("after") != before:
            raise HardlinkDetachError(
                f"Private partition receipt has inconsistent stats: {relative}"
            )
        if not _stat_matches(before, current):
            raise HardlinkDetachError(f"Partition changed after detach planning: {relative}")
        return status_text
    if status_text == "replace_pending_verification":
        source_sha256 = entry.get("source_sha256")
        if (
            int(before.get("nlink", 0)) <= 1
            or _SHA256_PATTERN.fullmatch(str(source_sha256)) is None
            or entry.get("copied_bytes") != before.get("size_bytes")
        ):
            raise HardlinkDetachError(f"Pending partition receipt is incomplete: {relative}")
        if current.st_nlink > 1:
            if not _stat_matches(before, current):
                raise HardlinkDetachError(
                    f"Pending partition changed before replacement: {relative}"
                )
            return "planned_detach"
        if current.st_nlink == 1 and current.st_ino != int(before.get("inode", -1)):
            return "recover_replaced"
        raise HardlinkDetachError(f"Pending detach has an ambiguous inode state: {relative}")
    if status_text == "detached":
        after = entry.get("after")
        if not isinstance(after, Mapping) or not _stat_matches(after, current):
            raise HardlinkDetachError(f"Detached partition changed after its receipt: {relative}")
        source_sha256 = entry.get("source_sha256")
        target_sha256 = entry.get("target_sha256")
        if (
            int(before.get("nlink", 0)) <= 1
            or int(after.get("nlink", 0)) != 1
            or after.get("inode") == before.get("inode")
            or after.get("size_bytes") != before.get("size_bytes")
            or entry.get("copied_bytes") != before.get("size_bytes")
            or _SHA256_PATTERN.fullmatch(str(source_sha256)) is None
            or _SHA256_PATTERN.fullmatch(str(target_sha256)) is None
            or not hmac.compare_digest(str(source_sha256), str(target_sha256))
            or entry.get("sha256_preserved") is not True
        ):
            raise HardlinkDetachError(f"Detached partition lacks preserved SHA-256: {relative}")
        return status_text
    raise HardlinkDetachError(f"Unsupported detach file status {status_text!r}: {relative}")


def _sha256_regular_file(path: Path, *, expected_inode: int) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or initial.st_ino != expected_inode:
            raise HardlinkDetachError(
                f"Partition inode changed before SHA-256 verification: {path}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(COPY_BUFFER_BYTES), b""):
                digest.update(chunk)
        final = os.fstat(descriptor)
        path_stat = path.lstat()
        if (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns) != (
            initial.st_dev,
            initial.st_ino,
            initial.st_size,
            initial.st_mtime_ns,
        ) or (path_stat.st_dev, path_stat.st_ino) != (initial.st_dev, initial.st_ino):
            raise HardlinkDetachError(f"Partition changed during SHA-256 verification: {path}")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _copy_to_temporary(
    source: Path,
    temporary: Path,
    *,
    before: Mapping[str, Any],
) -> tuple[str, int]:
    source_descriptor = os.open(
        source,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    destination_descriptor = -1
    digest = hashlib.sha256()
    copied = 0
    try:
        initial = os.fstat(source_descriptor)
        if not _stat_matches(before, initial) or not stat.S_ISREG(initial.st_mode):
            raise HardlinkDetachError(f"Partition changed before hardlink detach: {source}")
        destination_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
        with (
            os.fdopen(source_descriptor, "rb", closefd=False) as source_handle,
            os.fdopen(destination_descriptor, "wb", closefd=False) as destination_handle,
        ):
            while chunk := source_handle.read(COPY_BUFFER_BYTES):
                digest.update(chunk)
                destination_handle.write(chunk)
                copied += len(chunk)
            destination_handle.flush()
        os.fchmod(destination_descriptor, stat.S_IMODE(initial.st_mode))
        os.utime(
            temporary,
            ns=(int(before["atime_ns"]), int(before["mtime_ns"])),
            follow_symlinks=False,
        )
        os.fsync(destination_descriptor)

        final_source = os.fstat(source_descriptor)
        path_stat = source.lstat()
        if not _stat_matches(before, final_source) or not _stat_matches(before, path_stat):
            raise HardlinkDetachError(f"Partition changed while it was copied: {source}")
        temporary_stat = temporary.lstat()
        if (
            not stat.S_ISREG(temporary_stat.st_mode)
            or temporary_stat.st_nlink != 1
            or temporary_stat.st_size != int(before["size_bytes"])
            or copied != int(before["size_bytes"])
        ):
            raise HardlinkDetachVerificationError(
                f"Temporary detached copy has invalid size or link count: {temporary}"
            )
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        os.close(source_descriptor)
    return digest.hexdigest(), copied


def _receipt_summary(payload: Mapping[str, Any]) -> dict[str, int]:
    entries = payload["files"]
    initial_shared = [entry for entry in entries.values() if int(entry["before"]["nlink"]) > 1]
    detached = [entry for entry in entries.values() if entry["status"] == "detached"]
    private = [entry for entry in entries.values() if entry["status"] == "already_private"]
    remaining = [entry for entry in initial_shared if entry["status"] != "detached"]
    return {
        "file_count": len(entries),
        "total_bytes": sum(int(entry["before"]["size_bytes"]) for entry in entries.values()),
        "initial_shared_file_count": len(initial_shared),
        "initial_shared_bytes": sum(int(entry["before"]["size_bytes"]) for entry in initial_shared),
        "initial_private_file_count": len(entries) - len(initial_shared),
        "detached_file_count": len(detached),
        "detached_bytes": sum(int(entry["before"]["size_bytes"]) for entry in detached),
        "already_private_file_count": len(private),
        "sha256_preserved_file_count": sum(
            entry.get("sha256_preserved") is True for entry in detached
        ),
        "remaining_shared_file_count": len(remaining),
        "remaining_shared_bytes": sum(int(entry["before"]["size_bytes"]) for entry in remaining),
        "verified_nlink_one_file_count": len(private) + len(detached),
    }


def _refresh_summary(
    payload: dict[str, Any],
    inventory: Mapping[str, tuple[Path, os.stat_result]],
) -> None:
    remaining = [file_stat for _path, file_stat in inventory.values() if file_stat.st_nlink > 1]
    summary = _receipt_summary(payload)
    summary.update(
        {
            "remaining_shared_file_count": len(remaining),
            "remaining_shared_bytes": sum(file_stat.st_size for file_stat in remaining),
            "verified_nlink_one_file_count": len(inventory) - len(remaining),
        }
    )
    payload["summary"] = summary


def _recover_replaced_entry(
    *,
    path: Path,
    relative: str,
    entry: dict[str, Any],
    receipt_path: Path,
    payload: dict[str, Any],
) -> None:
    current = path.lstat()
    before = entry.get("before")
    if not isinstance(before, Mapping):
        raise HardlinkDetachError(f"Pending receipt lacks before stat: {relative}")
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or current.st_ino == int(before.get("inode", -1))
        or current.st_dev != int(before.get("device", -1))
        or current.st_size != int(before.get("size_bytes", -1))
        or stat.S_IMODE(current.st_mode) != stat.S_IMODE(int(before.get("mode", -1)))
        or current.st_mtime_ns != int(before.get("mtime_ns", -1))
    ):
        raise HardlinkDetachError(f"Pending detached copy has invalid metadata: {relative}")
    source_sha256 = entry.get("source_sha256")
    if not isinstance(source_sha256, str) or _SHA256_PATTERN.fullmatch(source_sha256) is None:
        raise HardlinkDetachError(f"Pending receipt lacks source SHA-256: {relative}")
    _fsync_directory(path.parent)
    target_sha256 = _sha256_regular_file(path, expected_inode=current.st_ino)
    after = path.lstat()
    preserved = hmac.compare_digest(source_sha256, target_sha256)
    entry.update(
        {
            "after": _stat_payload(after),
            "target_sha256": target_sha256,
            "sha256_preserved": preserved,
            "verified_at": _utc_now(),
        }
    )
    if not preserved:
        _checkpoint(payload, receipt_path)
        raise HardlinkDetachVerificationError(
            f"Recovered detached copy does not preserve SHA-256: {relative}"
        )
    entry["status"] = "detached"
    payload["summary"] = _receipt_summary(payload)
    _checkpoint(payload, receipt_path)


def _detach_entry(
    *,
    path: Path,
    relative: str,
    entry: dict[str, Any],
    receipt_path: Path,
    payload: dict[str, Any],
) -> None:
    before = entry["before"]
    temporary = path.parent / f".{path.name}.detach-{uuid.uuid4().hex}.tmp"
    replaced = False
    try:
        source_sha256, copied_bytes = _copy_to_temporary(path, temporary, before=before)
        entry.update(
            {
                "status": "replace_pending_verification",
                "source_sha256": source_sha256,
                "copied_bytes": copied_bytes,
                "copy_completed_at": _utc_now(),
            }
        )
        _checkpoint(payload, receipt_path)
        if not _stat_matches(before, path.lstat()):
            raise HardlinkDetachError(f"Partition changed before atomic replacement: {relative}")
        os.replace(temporary, path)
        replaced = True
        _fsync_directory(path.parent)
        _recover_replaced_entry(
            path=path,
            relative=relative,
            entry=entry,
            receipt_path=receipt_path,
            payload=payload,
        )
    finally:
        if temporary.exists():
            temporary.unlink()
            _fsync_directory(temporary.parent)
        if replaced and path.exists() and path.lstat().st_nlink != 1:
            raise HardlinkDetachVerificationError(
                f"Atomic replacement did not detach the partition inode: {relative}"
            )


def _result(payload: Mapping[str, Any], receipt_path: Path) -> dict[str, Any]:
    summary = payload["summary"]
    return {
        "status": payload["status"],
        "source_version": payload["source_version"],
        "target_version": payload["target_version"],
        "version_dir": payload["version_dir"],
        "receipt": str(receipt_path),
        **summary,
    }


def detach_version_hardlinks(  # noqa: C901,PLR0912,PLR0915
    *,
    source_version: str,
    version_dir: str | Path,
    receipt: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Detach shared Parquet inodes in one explicit, non-current v3 version."""
    version_root = _validate_version_root(version_dir)
    source_label = _validate_source_version(source_version, target_version=version_root.name)
    receipt_path = _validate_receipt_path(receipt, version_root=version_root)

    try:
        with minute_dataset_lock(version_root, operation="detach-a-share-minute-v3-hardlinks"):
            inventory, stale_temporaries, dates = _scan_tree(version_root)
            removed_temporaries = 0
            if stale_temporaries and not dry_run:
                removed_temporaries = _cleanup_temporary_files(stale_temporaries)
                inventory, remaining_temporaries, dates = _scan_tree(version_root)
                if remaining_temporaries:
                    raise HardlinkDetachError(
                        "Detach temporary cleanup left files inside the version tree"
                    )

            layout = _tree_layout(inventory, dates)
            payload = _load_receipt(receipt_path)
            if payload is None:
                payload = _new_payload(
                    source_version=source_label,
                    version_root=version_root,
                    inventory=inventory,
                    dates=dates,
                    temporary_file_count=len(stale_temporaries),
                )
            else:
                _validate_receipt_identity(
                    payload,
                    source_version=source_label,
                    version_root=version_root,
                    tree_layout=layout,
                    inventory=inventory,
                )
                cleanup = payload.get("cleanup")
                if not isinstance(cleanup, dict):
                    raise HardlinkDetachError("Detach receipt has invalid cleanup accounting")
                cleanup["stale_temporary_files_detected"] = int(
                    cleanup.get("stale_temporary_files_detected", 0)
                ) + len(stale_temporaries)
            payload["cleanup"]["stale_temporary_files_removed"] = (
                int(payload["cleanup"].get("stale_temporary_files_removed", 0))
                + removed_temporaries
            )

            for relative, (_path, file_stat) in inventory.items():
                entry = payload["files"][relative]
                if not isinstance(entry, dict):
                    raise HardlinkDetachError(
                        f"Detach receipt has malformed file entry: {relative}"
                    )
                _entry_state(entry, file_stat, relative=relative)

            _refresh_summary(payload, inventory)
            if dry_run:
                if payload.get("status") != "passed" or stale_temporaries:
                    payload["status"] = "planned"
                    payload["dry_run"] = True
                    payload.pop("error", None)
                _checkpoint(payload, receipt_path)
                return _result(payload, receipt_path)

            if payload.get("status") == "passed":
                if any(file_stat.st_nlink != 1 for _path, file_stat in inventory.values()):
                    raise HardlinkDetachVerificationError(
                        "Passed detach receipt no longer has st_nlink == 1 for every partition"
                    )
                if removed_temporaries:
                    _checkpoint(payload, receipt_path)
                return _result(payload, receipt_path)

            payload["status"] = "in_progress"
            payload["dry_run"] = False
            payload["started_at"] = payload.get("started_at", _utc_now())
            payload.pop("error", None)
            _checkpoint(payload, receipt_path)

            current_relative: str | None = None
            try:
                total = len(inventory)
                for index, (relative, (path, _planned_stat)) in enumerate(
                    sorted(inventory.items()), start=1
                ):
                    current_relative = relative
                    entry = payload["files"][relative]
                    state = _entry_state(entry, path.lstat(), relative=relative)
                    if state in {"already_private", "detached"}:
                        continue
                    print(f"[detach] {index}/{total} {relative}", file=sys.stderr, flush=True)
                    if state == "recover_replaced":
                        _recover_replaced_entry(
                            path=path,
                            relative=relative,
                            entry=entry,
                            receipt_path=receipt_path,
                            payload=payload,
                        )
                    else:
                        _detach_entry(
                            path=path,
                            relative=relative,
                            entry=entry,
                            receipt_path=receipt_path,
                            payload=payload,
                        )

                final_inventory, final_temporaries, final_dates = _scan_tree(version_root)
                if final_temporaries:
                    raise HardlinkDetachVerificationError(
                        "Detach completed with temporary files still present"
                    )
                if _tree_layout(final_inventory, final_dates) != layout:
                    raise HardlinkDetachVerificationError(
                        "V3 version tree layout changed during hardlink detach"
                    )
                remaining = [
                    relative
                    for relative, (_path, file_stat) in final_inventory.items()
                    if file_stat.st_nlink != 1
                ]
                if remaining:
                    raise HardlinkDetachVerificationError(
                        f"Partitions remain hardlinked after detach: {remaining[:10]}"
                    )
                for relative, (path, _file_stat) in final_inventory.items():
                    state = _entry_state(
                        payload["files"][relative],
                        path.lstat(),
                        relative=relative,
                    )
                    if state not in {"already_private", "detached"}:
                        raise HardlinkDetachVerificationError(
                            f"Partition lacks a complete detach receipt: {relative}"
                        )
                _refresh_summary(payload, final_inventory)
                payload["status"] = "passed"
                payload["completed_at"] = _utc_now()
                payload.pop("error", None)
                _checkpoint(payload, receipt_path)
                return _result(payload, receipt_path)
            except BaseException as exc:
                payload["status"] = "failed"
                payload["failed_at"] = _utc_now()
                payload["error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc) or type(exc).__name__,
                    "relative_path": current_relative,
                }
                try:
                    current_inventory, _temporaries, _dates = _scan_tree(version_root)
                    _refresh_summary(payload, current_inventory)
                except Exception:
                    pass
                _checkpoint(payload, receipt_path)
                raise
    except DatasetLockError as exc:
        raise HardlinkDetachError(f"Minute dataset lock rejected detach: {exc}") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-version",
        required=True,
        help="Immutable source version label used to create the v3 hardlink clone.",
    )
    parser.add_argument(
        "--version-dir",
        required=True,
        help="Explicit real minute_1m_v3_* directory to detach in place.",
    )
    parser.add_argument("--receipt", required=True, help="Atomic progress and evidence JSON path.")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = detach_version_hardlinks(
            source_version=args.source_version,
            version_dir=args.version_dir,
            receipt=args.receipt,
            dry_run=args.dry_run,
        )
    except (HardlinkDetachError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
