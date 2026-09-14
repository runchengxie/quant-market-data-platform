from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_detach_script() -> ModuleType:
    path = ROOT / "scripts" / "operations" / "detach_a_share_minute_v3.py"
    spec = importlib.util.spec_from_file_location("detach_a_share_minute_v3", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


detach = _load_detach_script()


def _receipt(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _version_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    parent = tmp_path / "a_share"
    source = parent / "minute_1m_v2_20260710"
    target = parent / "minute_1m_v3_20260711"
    receipt = tmp_path / "metadata" / "minute_1m_v3_20260711.detach.json"
    source.mkdir(parents=True)
    target.mkdir()
    return source, target, receipt


def _hardlinked_partition(
    source: Path, target: Path, date: str, content: bytes
) -> tuple[Path, Path]:
    source_path = source / f"trade_date={date}" / "part-00000.parquet"
    target_path = target / f"trade_date={date}" / "part-00000.parquet"
    source_path.parent.mkdir()
    target_path.parent.mkdir()
    source_path.write_bytes(content)
    os.link(source_path, target_path)
    return source_path, target_path


def _private_partition(target: Path, date: str, content: bytes) -> Path:
    path = target / f"trade_date={date}" / "part-00000.parquet"
    path.parent.mkdir()
    path.write_bytes(content)
    return path


def test_dry_run_freezes_inventory_then_apply_detaches_only_shared_files(
    tmp_path: Path,
) -> None:
    source, target, receipt_path = _version_paths(tmp_path)
    source_file, shared = _hardlinked_partition(source, target, "20260708", b"shared-bytes")
    private = _private_partition(target, "20260709", b"private-bytes")
    shared_inode = shared.stat().st_ino
    private_inode = private.stat().st_ino

    planned = detach.detach_version_hardlinks(
        source_version=source.name,
        version_dir=target,
        receipt=receipt_path,
        dry_run=True,
    )

    assert planned["status"] == "planned"
    assert planned["initial_shared_file_count"] == 1
    assert planned["remaining_shared_file_count"] == 1
    assert shared.stat().st_ino == shared_inode
    assert shared.stat().st_nlink == 2
    assert private.stat().st_ino == private_inode
    planned_receipt = _receipt(receipt_path)
    assert planned_receipt["schema_version"] == detach.RECEIPT_SCHEMA
    assert planned_receipt["source_version"] == source.name
    assert planned_receipt["target_version"] == target.name
    assert planned_receipt["version_dir"] == str(target.resolve())
    assert planned_receipt["tree_layout"] == {
        "pattern": "trade_date=YYYYMMDD/part-*.parquet",
        "partition_dir_count": 2,
        "partition_file_count": 2,
        "date_min": "20260708",
        "date_max": "20260709",
        "total_bytes": len(b"shared-bytes") + len(b"private-bytes"),
        "part_filenames": {"part-00000.parquet": 2},
        "path_size_inventory_sha256": planned_receipt["tree_layout"]["path_size_inventory_sha256"],
    }

    completed = detach.detach_version_hardlinks(
        source_version=source.name,
        version_dir=target,
        receipt=receipt_path,
    )

    assert completed["status"] == "passed"
    assert completed["detached_file_count"] == 1
    assert completed["already_private_file_count"] == 1
    assert completed["remaining_shared_file_count"] == 0
    assert completed["sha256_preserved_file_count"] == 1
    assert shared.read_bytes() == source_file.read_bytes() == b"shared-bytes"
    assert shared.stat().st_ino != shared_inode
    assert shared.stat().st_nlink == source_file.stat().st_nlink == 1
    assert private.stat().st_ino == private_inode

    passed_receipt = _receipt(receipt_path)
    entry = passed_receipt["files"]["trade_date=20260708/part-00000.parquet"]
    expected_sha = hashlib.sha256(b"shared-bytes").hexdigest()
    assert entry["before"]["inode"] == shared_inode
    assert entry["before"]["nlink"] == 2
    assert entry["before"]["size_bytes"] == len(b"shared-bytes")
    assert entry["after"]["inode"] == shared.stat().st_ino
    assert entry["after"]["nlink"] == 1
    assert entry["source_sha256"] == entry["target_sha256"] == expected_sha
    assert entry["sha256_preserved"] is True

    detached_inode = shared.stat().st_ino
    repeated = detach.detach_version_hardlinks(
        source_version=source.name,
        version_dir=target,
        receipt=receipt_path,
    )
    assert repeated["status"] == "passed"
    assert shared.stat().st_ino == detached_inode


def test_failure_checkpoints_completed_files_and_resume_skips_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, receipt_path = _version_paths(tmp_path)
    _hardlinked_partition(source, target, "20260708", b"first")
    _hardlinked_partition(source, target, "20260709", b"second")
    original_detach = detach._detach_entry
    calls = 0

    def fail_second(**kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise detach.HardlinkDetachError("simulated second-file failure")
        original_detach(**kwargs)

    monkeypatch.setattr(detach, "_detach_entry", fail_second)
    with pytest.raises(detach.HardlinkDetachError, match="simulated second-file failure"):
        detach.detach_version_hardlinks(
            source_version=source.name,
            version_dir=target,
            receipt=receipt_path,
        )

    failed = _receipt(receipt_path)
    assert failed["status"] == "failed"
    assert failed["summary"]["detached_file_count"] == 1
    assert failed["summary"]["remaining_shared_file_count"] == 1
    first_target = target / "trade_date=20260708" / "part-00000.parquet"
    first_detached_inode = first_target.stat().st_ino
    assert not list(target.rglob("*.tmp"))

    monkeypatch.setattr(detach, "_detach_entry", original_detach)
    resumed = detach.detach_version_hardlinks(
        source_version=source.name,
        version_dir=target,
        receipt=receipt_path,
    )

    assert resumed["status"] == "passed"
    assert resumed["detached_file_count"] == 2
    assert resumed["remaining_shared_file_count"] == 0
    assert first_target.stat().st_ino == first_detached_inode


def test_resume_recovers_replace_completed_before_final_receipt_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, receipt_path = _version_paths(tmp_path)
    _source_file, target_file = _hardlinked_partition(
        source,
        target,
        "20260709",
        b"recoverable-replace",
    )
    original_hash = detach._sha256_regular_file

    def fail_post_replace(*args: object, **kwargs: object) -> str:
        raise detach.HardlinkDetachError("simulated failure after replace")

    monkeypatch.setattr(detach, "_sha256_regular_file", fail_post_replace)
    with pytest.raises(detach.HardlinkDetachError, match="failure after replace"):
        detach.detach_version_hardlinks(
            source_version=source.name,
            version_dir=target,
            receipt=receipt_path,
        )

    replaced_inode = target_file.stat().st_ino
    assert target_file.stat().st_nlink == 1
    interrupted = _receipt(receipt_path)
    entry = interrupted["files"]["trade_date=20260709/part-00000.parquet"]
    assert interrupted["status"] == "failed"
    assert entry["status"] == "replace_pending_verification"
    assert entry["source_sha256"] == hashlib.sha256(b"recoverable-replace").hexdigest()

    monkeypatch.setattr(detach, "_sha256_regular_file", original_hash)
    resumed = detach.detach_version_hardlinks(
        source_version=source.name,
        version_dir=target,
        receipt=receipt_path,
    )

    assert resumed["status"] == "passed"
    assert target_file.stat().st_ino == replaced_inode
    recovered = _receipt(receipt_path)["files"]["trade_date=20260709/part-00000.parquet"]
    assert recovered["status"] == "detached"
    assert recovered["sha256_preserved"] is True


def test_apply_removes_only_recognized_stale_temporaries(tmp_path: Path) -> None:
    source, target, receipt_path = _version_paths(tmp_path)
    _source_file, target_file = _hardlinked_partition(source, target, "20260709", b"bytes")
    stale = target_file.parent / ".part-00000.parquet.detach-0123456789abcdef0123456789abcdef.tmp"
    stale.write_bytes(b"stale-copy")

    result = detach.detach_version_hardlinks(
        source_version=source.name,
        version_dir=target,
        receipt=receipt_path,
    )

    assert result["status"] == "passed"
    assert not stale.exists()
    receipt = _receipt(receipt_path)
    assert receipt["cleanup"] == {
        "stale_temporary_files_detected": 1,
        "stale_temporary_files_removed": 1,
    }


def test_rejects_partition_paths_sharing_an_inode_inside_target(tmp_path: Path) -> None:
    source, target, receipt_path = _version_paths(tmp_path)
    _source_file, first = _hardlinked_partition(source, target, "20260708", b"bytes")
    second = target / "trade_date=20260709" / "part-00000.parquet"
    second.parent.mkdir()
    os.link(first, second)

    with pytest.raises(detach.HardlinkDetachError, match="sharing one inode internally"):
        detach.detach_version_hardlinks(
            source_version=source.name,
            version_dir=target,
            receipt=receipt_path,
            dry_run=True,
        )


def test_rejects_current_symlink_v2_target_and_receipt_inside_version(tmp_path: Path) -> None:
    source, target, receipt_path = _version_paths(tmp_path)
    _hardlinked_partition(source, target, "20260709", b"bytes")

    with pytest.raises(detach.HardlinkDetachError, match="receipt must be outside"):
        detach.detach_version_hardlinks(
            source_version=source.name,
            version_dir=target,
            receipt=target / "detach.json",
            dry_run=True,
        )

    with pytest.raises(detach.HardlinkDetachError, match="only an explicit minute_1m_v3"):
        detach.detach_version_hardlinks(
            source_version=source.name,
            version_dir=source,
            receipt=receipt_path,
            dry_run=True,
        )

    current = target.parent / "minute_1m"
    current.symlink_to(target.name)
    with pytest.raises(detach.HardlinkDetachError, match="immutable current minute version"):
        detach.detach_version_hardlinks(
            source_version=source.name,
            version_dir=target,
            receipt=receipt_path,
            dry_run=True,
        )

    current.unlink()
    linked_target = target.parent / "minute_1m_v3_symlink"
    linked_target.symlink_to(target.name)
    with pytest.raises(detach.HardlinkDetachError, match="real directory"):
        detach.detach_version_hardlinks(
            source_version=source.name,
            version_dir=linked_target,
            receipt=receipt_path,
            dry_run=True,
        )
