from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
import socket
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_archive_script() -> ModuleType:
    path = ROOT / "scripts" / "operations" / "archive_guan_mobile.py"
    spec = importlib.util.spec_from_file_location("archive_guan_mobile", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


archive = _load_archive_script()


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "mobile" / "level2"
    destination = tmp_path / "enclosure" / "incoming" / "level2"
    manifest = tmp_path / "enclosure" / "incoming" / "archive-manifest.json"
    source.mkdir(parents=True)
    return source, destination, manifest


def _payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_archive_recursively_copies_all_regular_files_and_preserves_mtime(
    tmp_path: Path,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    nested = source / "nested"
    nested.mkdir()
    files = {
        source / "deal_20260318.parquet": b"first-deal",
        source / "deal_20260318(1).parquet": b"duplicate-name-is-still-a-file",
        nested / "snapshot_20260318.parquet": b"snapshot",
    }
    expected_mtime = 1_700_000_000_123_456_789
    for index, (path, content) in enumerate(files.items()):
        path.write_bytes(content)
        os.utime(path, ns=(expected_mtime + index, expected_mtime + index))
    (source / "ignored-link").symlink_to(source / "deal_20260318.parquet")

    result: dict[str, Any] = archive.archive_directory(source, destination, manifest)

    assert result == {
        "status": "complete",
        "file_count": 3,
        "bytes": sum(map(len, files.values())),
        "copied_file_count": 3,
        "skipped_file_count": 0,
        "manifest": str(manifest.resolve()),
    }
    for source_path, content in files.items():
        relative = source_path.relative_to(source)
        target = destination / relative
        assert target.read_bytes() == content
        assert target.stat().st_mtime_ns == source_path.stat().st_mtime_ns
    assert not (destination / "ignored-link").exists()

    receipt = _payload(manifest)
    assert receipt["status"] == "complete"
    assert receipt["file_count"] == 3
    assert receipt["bytes"] == sum(map(len, files.values()))
    duplicate = receipt["files"]["deal_20260318(1).parquet"]
    assert (
        duplicate["sha256"]
        == hashlib.sha256(files[source / "deal_20260318(1).parquet"]).hexdigest()
    )
    assert not list(destination.rglob("*.tmp"))


def test_resume_skips_completed_receipt_when_target_size_and_mtime_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"unchanged")
    first: dict[str, Any] = archive.archive_directory(source, destination, manifest)

    def unexpected_copy(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("a matching completed receipt must skip the copy")

    monkeypatch.setattr(archive, "_copy_one_file", unexpected_copy)
    second: dict[str, Any] = archive.archive_directory(source, destination, manifest)

    assert first["copied_file_count"] == 1
    assert second["copied_file_count"] == 0
    assert second["skipped_file_count"] == 1
    assert _payload(manifest)["completed_file_count"] == 1


def test_restart_resumes_from_per_file_checkpoint_after_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "a.parquet").write_bytes(b"first")
    (source / "b.parquet").write_bytes(b"second")
    original_copy = archive._copy_one_file
    calls = 0

    def fail_second(source_path: Path, destination_path: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise archive.ArchiveError("simulated interruption")
        return original_copy(source_path, destination_path)

    monkeypatch.setattr(archive, "_copy_one_file", fail_second)
    with pytest.raises(archive.ArchiveError, match="simulated interruption"):
        archive.archive_directory(source, destination, manifest)

    interrupted = _payload(manifest)
    assert interrupted["status"] == "failed"
    assert interrupted["files"]["a.parquet"]["status"] == "complete"
    assert "b.parquet" not in interrupted["files"]

    copied_after_restart: list[str] = []

    def record_copy(source_path: Path, destination_path: Path) -> dict[str, object]:
        copied_after_restart.append(source_path.name)
        return original_copy(source_path, destination_path)

    monkeypatch.setattr(archive, "_copy_one_file", record_copy)
    resumed: dict[str, Any] = archive.archive_directory(source, destination, manifest)

    assert copied_after_restart == ["b.parquet"]
    assert resumed["copied_file_count"] == 1
    assert resumed["skipped_file_count"] == 1
    assert (destination / "a.parquet").read_bytes() == b"first"
    assert (destination / "b.parquet").read_bytes() == b"second"


def test_changed_target_is_recopied_instead_of_skipped(tmp_path: Path) -> None:
    source, destination, manifest = _paths(tmp_path)
    source_file = source / "deal.parquet"
    source_file.write_bytes(b"canonical-source")
    archive.archive_directory(source, destination, manifest)
    target = destination / "deal.parquet"
    target.write_bytes(b"corrupt")

    result: dict[str, Any] = archive.archive_directory(source, destination, manifest)

    assert result["copied_file_count"] == 1
    assert target.read_bytes() == b"canonical-source"


def test_verify_rehashes_destination_and_records_corruption_failure(tmp_path: Path) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"abcdefgh")
    archive.archive_directory(source, destination, manifest)
    target = destination / "deal.parquet"
    receipt = _payload(manifest)["files"]["deal.parquet"]
    target.write_bytes(b"abcdEfgh")
    os.utime(
        target,
        ns=(int(receipt["destination_mtime_ns"]), int(receipt["destination_mtime_ns"])),
    )

    exit_code = archive.main(
        [
            "--source-dir",
            str(source),
            "--destination-dir",
            str(destination),
            "--manifest",
            str(manifest),
            "--verify",
        ]
    )

    assert exit_code == 1
    failed = _payload(manifest)
    assert failed["status"] == "verify_failed"
    assert failed["error"]["type"] == "ArchiveVerificationError"
    assert failed["error"]["relative_path"] == "deal.parquet"
    assert "sha256" in failed["error"]["message"]


def test_verify_success_updates_summary(tmp_path: Path) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"deal")
    (source / "order.parquet").write_bytes(b"orders")
    archive.archive_directory(source, destination, manifest)

    result: dict[str, Any] = archive.verify_archive(source, destination, manifest)

    assert result["status"] == "verified"
    assert result["file_count"] == 2
    assert result["bytes"] == 10
    payload = _payload(manifest)
    assert payload["status"] == "verified"
    assert payload["verified_file_count"] == 2


def test_source_change_during_stream_fails_without_replacing_existing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    source_file = source / "deal.parquet"
    source_file.write_bytes(b"initial-source")
    destination.mkdir(parents=True)
    target = destination / "deal.parquet"
    target.write_bytes(b"keep-existing-target")
    original_stream = archive._stream_copy

    def mutate_after_stream(
        source_handle: object, destination_handle: object, digest: object
    ) -> int:
        copied = original_stream(source_handle, destination_handle, digest)
        source_file.write_bytes(b"source-mutated-during-copy")
        return copied

    monkeypatch.setattr(archive, "_stream_copy", mutate_after_stream)

    with pytest.raises(archive.SourceChangedError, match="changed while"):
        archive.archive_directory(source, destination, manifest)

    assert target.read_bytes() == b"keep-existing-target"
    payload = _payload(manifest)
    assert payload["status"] == "failed"
    assert payload["error"]["type"] == "SourceChangedError"
    assert payload["error"]["relative_path"] == "deal.parquet"
    assert not list(destination.glob(".*.tmp"))


def test_dataset_lock_prevents_concurrent_archive(tmp_path: Path) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"deal")
    destination.mkdir(parents=True)
    (destination / archive.LOCK_FILENAME).write_text(
        '{"pid": 1, "hostname": "other-host"}', encoding="utf-8"
    )

    with pytest.raises(archive.ArchiveError, match="dataset lock"):
        archive.archive_directory(source, destination, manifest)


def test_live_local_legacy_lock_is_not_taken_over(tmp_path: Path) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"deal")
    destination.mkdir(parents=True)
    lock_path = destination / archive.LOCK_FILENAME
    owner = {"pid": os.getpid(), "hostname": socket.gethostname()}
    lock_path.write_text(json.dumps(owner), encoding="utf-8")
    inode = lock_path.stat().st_ino

    with pytest.raises(archive.ArchiveError, match="Legacy dataset lock may still be active"):
        archive.archive_directory(source, destination, manifest)

    assert lock_path.stat().st_ino == inode
    assert json.loads(lock_path.read_text(encoding="utf-8")) == owner


def test_killed_copy_uuid_temps_are_cleaned_but_foreign_temp_is_rejected(
    tmp_path: Path,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"deal")
    destination.mkdir(parents=True)
    stale_copy = destination / f".deal.parquet.{uuid.uuid4().hex}.tmp"
    stale_manifest = manifest.parent / f".{manifest.name}.{uuid.uuid4().hex}.tmp"
    stale_copy.write_bytes(b"partial-copy")
    stale_manifest.write_text("partial-manifest", encoding="utf-8")

    result: dict[str, Any] = archive.archive_directory(source, destination, manifest)

    assert result["status"] == "complete"
    assert not stale_copy.exists()
    assert not stale_manifest.exists()
    cleanup = _payload(manifest)["stale_temp_cleanup"]
    assert cleanup["removed_count"] == 2

    foreign = destination / f".not-a-source-file.{uuid.uuid4().hex}.tmp"
    foreign.write_bytes(b"must-not-delete")
    with pytest.raises(archive.ArchiveVerificationError, match="unexpected"):
        archive.verify_archive(source, destination, manifest)
    assert foreign.read_bytes() == b"must-not-delete"


def test_malformed_or_nonregular_tool_like_temp_is_not_deleted(tmp_path: Path) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"deal")
    destination.mkdir(parents=True)
    malformed = destination / ".deal.parquet.not-a-uuid.tmp"
    malformed.write_bytes(b"foreign")

    with pytest.raises(archive.ArchiveVerificationError, match="unexpected"):
        archive.archive_directory(source, destination, manifest)

    assert malformed.read_bytes() == b"foreign"


def test_cli_copy_writes_json_summary_and_returns_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"deal")

    result = archive.main(
        [
            "--source-dir",
            str(source),
            "--destination-dir",
            str(destination),
            "--manifest",
            str(manifest),
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "complete"
    assert summary["file_count"] == 1


def test_verify_rejects_failed_copy_with_only_a_receipt_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "a.parquet").write_bytes(b"first")
    (source / "b.parquet").write_bytes(b"second")
    original_copy = archive._copy_one_file
    calls = 0

    def fail_second(source_path: Path, destination_path: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise archive.ArchiveError("simulated interruption")
        return original_copy(source_path, destination_path)

    monkeypatch.setattr(archive, "_copy_one_file", fail_second)
    with pytest.raises(archive.ArchiveError, match="simulated interruption"):
        archive.archive_directory(source, destination, manifest)

    with pytest.raises(archive.ArchiveVerificationError, match="copy is not complete"):
        archive.verify_archive(source, destination, manifest)

    payload = _payload(manifest)
    assert payload["copy_status"] == "failed"
    assert payload["status"] == "verify_failed"
    assert set(payload["files"]) == {"a.parquet"}


def test_source_inventory_is_frozen_across_copy_and_verify(tmp_path: Path) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"deal")
    archive.archive_directory(source, destination, manifest)
    (source / "late-arrival.parquet").write_bytes(b"late")

    with pytest.raises(archive.SourceChangedError, match="Source inventory changed"):
        archive.verify_archive(source, destination, manifest)
    with pytest.raises(archive.SourceChangedError, match="Source inventory changed"):
        archive.archive_directory(source, destination, manifest)


def test_source_replacement_with_same_size_and_mtime_is_not_resumed(
    tmp_path: Path,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    source_file = source / "deal.parquet"
    source_file.write_bytes(b"old-data")
    archive.archive_directory(source, destination, manifest)
    source_mtime = source_file.stat().st_mtime_ns
    source_file.write_bytes(b"new-data")
    os.utime(source_file, ns=(source_mtime, source_mtime))
    with pytest.raises(archive.SourceChangedError, match="Source inventory changed"):
        archive.verify_archive(source, destination, manifest)
    with pytest.raises(archive.SourceChangedError, match="Source inventory changed"):
        archive.archive_directory(source, destination, manifest)

    assert (destination / "deal.parquet").read_bytes() == b"old-data"


def test_first_checkpoint_persists_full_source_stat_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    source_file = source / "deal.parquet"
    source_file.write_bytes(b"source")

    def stop_before_copy(*args: object, **kwargs: object) -> dict[str, object]:
        raise archive.ArchiveError("stop after first checkpoint")

    monkeypatch.setattr(archive, "_copy_one_file", stop_before_copy)
    with pytest.raises(archive.ArchiveError, match="first checkpoint"):
        archive.archive_directory(source, destination, manifest)

    payload = _payload(manifest)
    signature = payload["source_inventory"]["deal.parquet"]
    assert payload["source_inventory_schema"] == archive.SOURCE_INVENTORY_SCHEMA
    assert set(signature) == {"dev", "inode", "mode", "size", "mtime_ns", "ctime_ns"}
    source_stat = source_file.stat()
    assert signature["dev"] == source_stat.st_dev
    assert signature["inode"] == source_stat.st_ino
    assert signature["ctime_ns"] == source_stat.st_ctime_ns


def test_complete_legacy_manifest_is_safely_upgraded_before_verification(
    tmp_path: Path,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"source")
    archive.archive_directory(source, destination, manifest)
    payload = _payload(manifest)
    payload["source_inventory"] = {
        relative: {"size": item["size"], "mtime_ns": item["mtime_ns"]}
        for relative, item in payload["source_inventory"].items()
    }
    payload.pop("source_inventory_schema", None)
    payload.pop("source_inventory_captured_at", None)
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    result: dict[str, Any] = archive.verify_archive(source, destination, manifest)

    assert result["status"] == "verified"
    upgraded = _payload(manifest)
    assert upgraded["source_inventory_schema"] == archive.SOURCE_INVENTORY_SCHEMA
    assert upgraded["source_inventory_upgrade"]["strategy"] == "complete-receipts-anchored"
    assert "ctime_ns" in upgraded["source_inventory"]["deal.parquet"]


def test_partial_legacy_manifest_restarts_copy_from_full_stat_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "a.parquet").write_bytes(b"first")
    (source / "b.parquet").write_bytes(b"second")
    original_copy = archive._copy_one_file
    calls = 0

    def fail_second(source_path: Path, destination_path: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise archive.ArchiveError("simulated legacy interruption")
        return original_copy(source_path, destination_path)

    monkeypatch.setattr(archive, "_copy_one_file", fail_second)
    with pytest.raises(archive.ArchiveError, match="legacy interruption"):
        archive.archive_directory(source, destination, manifest)
    payload = _payload(manifest)
    payload["source_inventory"] = {
        relative: {"size": item["size"], "mtime_ns": item["mtime_ns"]}
        for relative, item in payload["source_inventory"].items()
    }
    payload.pop("source_inventory_schema", None)
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    recopied: list[str] = []

    def record_copy(source_path: Path, destination_path: Path) -> dict[str, object]:
        recopied.append(source_path.name)
        return original_copy(source_path, destination_path)

    monkeypatch.setattr(archive, "_copy_one_file", record_copy)
    result: dict[str, Any] = archive.archive_directory(source, destination, manifest)

    assert result["copied_file_count"] == 2
    assert recopied == ["a.parquet", "b.parquet"]
    upgraded = _payload(manifest)
    assert (
        upgraded["source_inventory_upgrade"]["strategy"] == "restart-copy-from-full-stat-snapshot"
    )
    assert upgraded["source_inventory_upgrade"]["discarded_receipt_count"] == 1


def test_restart_rejects_same_size_mtime_replacement_after_partial_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "a.parquet").write_bytes(b"first")
    second = source / "b.parquet"
    second.write_bytes(b"second")
    original_copy = archive._copy_one_file
    calls = 0

    def fail_second(source_path: Path, destination_path: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise archive.ArchiveError("simulated restart boundary")
        return original_copy(source_path, destination_path)

    monkeypatch.setattr(archive, "_copy_one_file", fail_second)
    with pytest.raises(archive.ArchiveError, match="restart boundary"):
        archive.archive_directory(source, destination, manifest)
    old_mtime = second.stat().st_mtime_ns
    second.write_bytes(b"SECOND")
    os.utime(second, ns=(old_mtime, old_mtime))

    monkeypatch.setattr(archive, "_copy_one_file", original_copy)
    with pytest.raises(archive.SourceChangedError, match="Source inventory changed"):
        archive.archive_directory(source, destination, manifest)

    assert (destination / "a.parquet").read_bytes() == b"first"
    assert not (destination / "b.parquet").exists()


def test_final_inventory_check_detects_a_processed_source_changing_later(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    first = source / "a.parquet"
    first.write_bytes(b"first")
    (source / "b.parquet").write_bytes(b"second")
    first_mtime = first.stat().st_mtime_ns
    original_copy = archive._copy_one_file

    def mutate_first_after_second(source_path: Path, destination_path: Path) -> dict[str, object]:
        receipt = original_copy(source_path, destination_path)
        if source_path.name == "b.parquet":
            first.write_bytes(b"FIRST")
            os.utime(first, ns=(first_mtime, first_mtime))
        return receipt

    monkeypatch.setattr(archive, "_copy_one_file", mutate_first_after_second)

    with pytest.raises(archive.SourceChangedError, match="while it was being archived"):
        archive.archive_directory(source, destination, manifest)


def test_verify_rejects_unexpected_destination_file(tmp_path: Path) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"deal")
    archive.archive_directory(source, destination, manifest)
    (destination / "not-from-source.parquet").write_bytes(b"unexpected")

    with pytest.raises(archive.ArchiveVerificationError, match="unexpected"):
        archive.verify_archive(source, destination, manifest)


def test_copy_rejects_unexpected_destination_file_before_streaming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"deal")
    destination.mkdir(parents=True)
    (destination / "not-from-source.parquet").write_bytes(b"unexpected")

    def unexpected_copy(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("destination inventory must fail before copying")

    monkeypatch.setattr(archive, "_copy_one_file", unexpected_copy)
    with pytest.raises(archive.ArchiveVerificationError, match="unexpected"):
        archive.archive_directory(source, destination, manifest)


def test_overlapping_destination_is_rejected_without_being_created(tmp_path: Path) -> None:
    source, _, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"deal")
    nested_destination = source / "must-not-be-created"

    with pytest.raises(archive.ArchiveError, match="must not overlap"):
        archive.archive_directory(source, nested_destination, manifest)

    assert not nested_destination.exists()


def test_failed_verification_forces_copy_to_replace_same_metadata_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "deal.parquet").write_bytes(b"abcdefgh")
    archive.archive_directory(source, destination, manifest)
    target = destination / "deal.parquet"
    receipt = _payload(manifest)["files"]["deal.parquet"]
    target.write_bytes(b"abcdEfgh")
    target_mtime = int(receipt["destination_mtime_ns"])
    os.utime(target, ns=(target_mtime, target_mtime))

    with pytest.raises(archive.ArchiveVerificationError, match="sha256"):
        archive.verify_archive(source, destination, manifest)
    original_copy = archive._copy_one_file
    copied: list[str] = []

    def record_copy(source_path: Path, destination_path: Path) -> dict[str, object]:
        copied.append(source_path.name)
        return original_copy(source_path, destination_path)

    monkeypatch.setattr(archive, "_copy_one_file", record_copy)
    archive.archive_directory(source, destination, manifest)

    assert copied == ["deal.parquet"]
    assert target.read_bytes() == b"abcdefgh"


def test_verify_detects_a_hashed_destination_changing_before_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    (source / "a.parquet").write_bytes(b"first")
    (source / "b.parquet").write_bytes(b"second")
    archive.archive_directory(source, destination, manifest)
    first_target = destination / "a.parquet"
    first_mtime = first_target.stat().st_mtime_ns
    original_hash = archive._hash_stable_file

    def mutate_first_after_hashing_second(path: Path) -> tuple[str, os.stat_result]:
        result = original_hash(path)
        if path.name == "b.parquet":
            first_target.write_bytes(b"FIRST")
            os.utime(first_target, ns=(first_mtime, first_mtime))
        return result

    monkeypatch.setattr(archive, "_hash_stable_file", mutate_first_after_hashing_second)

    with pytest.raises(archive.ArchiveVerificationError, match="changed after"):
        archive.verify_archive(source, destination, manifest)

    first_receipt = _payload(manifest)["files"]["a.parquet"]
    assert first_receipt["verification_status"] == "failed"


def test_unsupported_chmod_does_not_fail_content_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, manifest = _paths(tmp_path)
    source_file = source / "deal.parquet"
    source_file.write_bytes(b"deal")
    source_file.chmod(0o755)

    def unsupported_chmod(path: object, mode: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "operation not supported")

    monkeypatch.setattr(archive.os, "chmod", unsupported_chmod)
    result: dict[str, Any] = archive.archive_directory(source, destination, manifest)

    assert result["status"] == "complete"
    assert (destination / "deal.parquet").read_bytes() == b"deal"
    receipt = _payload(manifest)["files"]["deal.parquet"]
    assert receipt["source_mode"] == 0o755
    assert receipt["mode_preserved"] is False


def test_empty_source_cannot_produce_a_vacuously_verified_archive(tmp_path: Path) -> None:
    source, destination, manifest = _paths(tmp_path)

    with pytest.raises(archive.ArchiveError, match="no regular files"):
        archive.archive_directory(source, destination, manifest)

    payload = _payload(manifest)
    assert payload["copy_status"] == "failed"
