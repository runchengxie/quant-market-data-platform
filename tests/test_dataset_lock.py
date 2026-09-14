from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from market_data_platform import dataset_lock
from market_data_platform.dataset_lock import (
    LOCK_PROTOCOL,
    DatasetLockError,
    exclusive_file_lock,
)


def test_stale_legacy_lock_is_reused_in_place_and_then_held_by_flock(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "dataset.lock"
    lock_path.write_text(
        json.dumps({"pid": 2_000_000_000, "hostname": socket.gethostname()}),
        encoding="utf-8",
    )
    original_inode = lock_path.stat().st_ino

    with exclusive_file_lock(lock_path, operation="first"):
        owner = json.loads(lock_path.read_text(encoding="utf-8"))
        assert owner["lock_protocol"] == LOCK_PROTOCOL
        assert owner["status"] == "owned"
        assert lock_path.stat().st_ino == original_inode
        with pytest.raises(DatasetLockError, match="lock is held"):
            with exclusive_file_lock(lock_path, operation="concurrent"):
                pass

    released = json.loads(lock_path.read_text(encoding="utf-8"))
    assert released["status"] == "released"
    assert lock_path.stat().st_ino == original_inode


def test_release_never_removes_or_rewrites_a_replacement_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "dataset.lock"
    displaced = tmp_path / "displaced.lock"
    replacement = {"pid": os.getpid(), "hostname": "replacement-owner"}

    with exclusive_file_lock(lock_path, operation="owner", remove_on_release=True):
        lock_path.rename(displaced)
        lock_path.write_text(json.dumps(replacement), encoding="utf-8")

    assert json.loads(lock_path.read_text(encoding="utf-8")) == replacement
    displaced_owner = json.loads(displaced.read_text(encoding="utf-8"))
    assert displaced_owner["status"] == "owned"


def test_unreadable_legacy_lock_is_conservatively_rejected(tmp_path: Path) -> None:
    lock_path = tmp_path / "dataset.lock"
    lock_path.write_text("not-json", encoding="utf-8")
    inode = lock_path.stat().st_ino

    with pytest.raises(DatasetLockError, match="unreadable legacy"):
        with exclusive_file_lock(lock_path, operation="must-not-take-over"):
            pass

    assert lock_path.stat().st_ino == inode
    assert lock_path.read_text(encoding="utf-8") == "not-json"


def test_inode_check_failure_releases_descriptor_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "dataset.lock"
    original_check = dataset_lock._descriptor_is_current_path

    def fail_inode_check(descriptor: int, path: Path) -> bool:
        raise OSError("inode check failed")

    monkeypatch.setattr(dataset_lock, "_descriptor_is_current_path", fail_inode_check)
    with pytest.raises(OSError, match="inode check failed"):
        with exclusive_file_lock(lock_path, operation="failing-check"):
            pass

    monkeypatch.setattr(dataset_lock, "_descriptor_is_current_path", original_check)
    with exclusive_file_lock(lock_path, operation="after-failing-check"):
        pass


def test_flock_excludes_a_separate_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "dataset.lock"
    program = """
import sys
from market_data_platform.dataset_lock import exclusive_file_lock
with exclusive_file_lock(sys.argv[1], operation="child"):
    print("ready", flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", program, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(DatasetLockError, match="lock is held"):
            with exclusive_file_lock(lock_path, operation="parent"):
                pass
    finally:
        assert child.stdin is not None
        child.stdin.write("release\n")
        child.stdin.flush()
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, (stdout, stderr)
