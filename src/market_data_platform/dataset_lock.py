"""Cross-command locks for the versioned A-share minute dataset."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOCK_PROTOCOL = "fcntl-flock.v1"


class DatasetLockError(RuntimeError):
    """Raised when another process owns a dataset lock."""


def minute_dataset_lock_path(output_dir: str | Path) -> Path:
    """Return the lock shared by every minute version below one parent."""
    return Path(output_dir).expanduser().resolve().parent / ".a-share-minute-dataset.lock"


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_owner(descriptor: int) -> dict[str, Any] | None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    raw = os.read(descriptor, 64 * 1024)
    if not raw:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_owner(descriptor: int, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n").encode()
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.ftruncate(descriptor, 0)
    remaining = memoryview(encoded)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("Could not write dataset lock ownership metadata")
        remaining = remaining[written:]
    os.fsync(descriptor)


def _descriptor_is_current_path(descriptor: int, lock_path: Path) -> bool:
    try:
        path_stat = os.stat(lock_path, follow_symlinks=False)
    except FileNotFoundError:
        return False
    descriptor_stat = os.fstat(descriptor)
    return (path_stat.st_dev, path_stat.st_ino) == (
        descriptor_stat.st_dev,
        descriptor_stat.st_ino,
    )


def _legacy_owner_is_stale(owner: dict[str, Any] | None) -> bool:
    """Recognize only a provably dead lock from the pre-flock implementation."""
    if owner is None or owner.get("lock_protocol") == LOCK_PROTOCOL:
        return owner is not None
    if owner.get("hostname") != socket.gethostname():
        return False
    try:
        pid = int(owner["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    return pid > 0 and not _pid_is_alive(pid)


def _owner_summary(owner: dict[str, Any] | None) -> str:
    if owner is None:
        return "unknown owner"
    return (
        f"pid={owner.get('pid')!r}, hostname={owner.get('hostname')!r}, "
        f"operation={owner.get('operation')!r}"
    )


def _unlock_and_close(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _open_locked_descriptor(path: Path) -> int | None:
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in {errno.EACCES, errno.EAGAIN}:
            _unlock_and_close(descriptor)
            raise
        try:
            owner = _read_owner(descriptor)
        finally:
            _unlock_and_close(descriptor)
        raise DatasetLockError(f"Dataset lock is held at {path}: {_owner_summary(owner)}") from None

    try:
        is_current_path = _descriptor_is_current_path(descriptor, path)
    except Exception:
        _unlock_and_close(descriptor)
        raise
    if is_current_path:
        return descriptor
    _unlock_and_close(descriptor)
    return None


def _validate_previous_owner(descriptor: int, path: Path) -> None:
    previous_owner = _read_owner(descriptor)
    if (
        previous_owner is not None
        and previous_owner.get("lock_protocol") != LOCK_PROTOCOL
        and not _legacy_owner_is_stale(previous_owner)
    ):
        raise DatasetLockError(
            f"Legacy dataset lock may still be active at {path}: {_owner_summary(previous_owner)}"
        )
    if previous_owner is None and path.stat().st_size:
        raise DatasetLockError(f"Dataset lock has unreadable legacy ownership metadata: {path}")


def _acquire_descriptor(path: Path, *, operation: str, token: str) -> int:
    for _ in range(8):
        descriptor = _open_locked_descriptor(path)
        if descriptor is None:
            continue
        try:
            _validate_previous_owner(descriptor, path)
            _write_owner(
                descriptor,
                {
                    "lock_protocol": LOCK_PROTOCOL,
                    "owner_token": token,
                    "status": "owned",
                    "pid": os.getpid(),
                    "hostname": socket.gethostname(),
                    "operation": operation,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
        except Exception:
            _unlock_and_close(descriptor)
            raise
        return descriptor
    raise DatasetLockError(f"Dataset lock path was replaced repeatedly: {path}")


def _release_descriptor(
    descriptor: int,
    path: Path,
    *,
    token: str,
    remove_on_release: bool,
) -> None:
    try:
        owner = _read_owner(descriptor)
        if owner is None or owner.get("owner_token") != token:
            return
        if not _descriptor_is_current_path(descriptor, path):
            return
        if remove_on_release:
            # Unlink at the end of the critical section while this inode is
            # still locked. A replacement path is never removed.
            path.unlink()
            return
        owner["status"] = "released"
        owner["released_at"] = datetime.now(UTC).isoformat()
        _write_owner(descriptor, owner)
    finally:
        _unlock_and_close(descriptor)


@contextmanager
def exclusive_file_lock(
    lock_path: str | Path,
    *,
    operation: str,
    remove_on_release: bool = False,
) -> Iterator[Path]:
    """Hold an advisory lock fd for the whole critical section.

    The compatibility check is deliberately conservative. A live or remote
    pre-flock owner is rejected even though it does not hold an advisory lock.
    Dead legacy locks are reused in place, never unlinked during takeover.
    """
    requested_path = Path(lock_path).expanduser()
    path = requested_path.parent.resolve() / requested_path.name
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    descriptor = _acquire_descriptor(path, operation=operation, token=token)

    try:
        yield path
    finally:
        _release_descriptor(
            descriptor,
            path,
            token=token,
            remove_on_release=remove_on_release,
        )


@contextmanager
def minute_dataset_lock(
    output_dir: str | Path,
    *,
    operation: str,
) -> Iterator[Path]:
    """Acquire the shared minute dataset lock."""
    lock_path = minute_dataset_lock_path(output_dir)
    with exclusive_file_lock(lock_path, operation=operation) as acquired:
        current = lock_path.parent / "minute_1m"
        resolved_output = Path(output_dir).expanduser().resolve()
        if (
            operation != "cutover-a-share-minute-current"
            and current.is_symlink()
            and current.resolve() == resolved_output
        ):
            raise DatasetLockError(
                f"Refusing to mutate the immutable current minute version: {resolved_output}"
            )
        yield acquired


__all__ = [
    "DatasetLockError",
    "LOCK_PROTOCOL",
    "exclusive_file_lock",
    "minute_dataset_lock",
    "minute_dataset_lock_path",
]
