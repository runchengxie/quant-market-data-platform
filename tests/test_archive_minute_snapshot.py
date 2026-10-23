from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/operations/archive_minute_snapshot.py"


def module():
    spec = importlib.util.spec_from_file_location("minute_archive", SCRIPT)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    pq.write_table(
        pa.table({"trade_time": ["2026-07-14 09:31"], "close": [1.2]}), source / "20260714.parquet"
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text('{"days":["20260714"]}')
    contract = tmp_path / "contract.json"
    contract.write_text('{"version":"v1"}')
    plan = {
        "version": "v1",
        "entries": [
            {"source": str(source), "path": "dataset", "role": "minute"},
            {"source": str(coverage), "path": "evidence/coverage.json", "role": "coverage"},
            {"source": str(contract), "path": "evidence/contract.json", "role": "contract"},
        ],
    }
    return source, plan


def test_source_removed_restore_and_verify_are_read_only(tmp_path):
    api = module()
    source, plan = fixture(tmp_path)
    archive = tmp_path / "archive"
    api.archive_snapshot(plan, archive)
    before = {p.relative_to(archive): p.read_bytes() for p in archive.rglob("*") if p.is_file()}
    shutil.rmtree(source)
    (tmp_path / "coverage.json").unlink()
    (tmp_path / "contract.json").unlink()
    api.verify_snapshot(archive)
    restored = tmp_path / "restored"
    api.restore_snapshot(archive, restored)
    assert pq.read_table(restored / "dataset/20260714.parquet").num_rows == 1
    assert json.loads((restored / "evidence/contract.json").read_text())["version"] == "v1"
    assert before == {
        p.relative_to(archive): p.read_bytes() for p in archive.rglob("*") if p.is_file()
    }
    payload = restored / "dataset/20260714.parquet"
    assert payload.stat().st_nlink == 1
    assert payload.stat().st_ino != (archive / "payload/dataset/20260714.parquet").stat().st_ino
    payload.write_bytes(b"changed")
    api.verify_snapshot(archive)


@pytest.mark.parametrize("damage", ["missing", "extra", "corrupt", "link", "unsealed"])
def test_archive_damage_rejected_before_restore(tmp_path, damage):
    api = module()
    _, plan = fixture(tmp_path)
    archive = tmp_path / "archive"
    api.archive_snapshot(plan, archive)
    payload = archive / "payload/dataset/20260714.parquet"
    if damage == "missing":
        payload.unlink()
    elif damage == "extra":
        (archive / "payload/extra").write_text("unexpected")
    elif damage == "corrupt":
        payload.write_bytes(b"bad")
    elif damage == "link":
        payload.unlink()
        payload.symlink_to(tmp_path / "contract.json")
    else:
        (archive / "SEALED.json").unlink()
    with pytest.raises((ValueError, OSError)):
        api.restore_snapshot(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("path", ["../escape", "/absolute", "dataset/../escape"])
def test_plan_rejects_unsafe_paths(tmp_path, path):
    api = module()
    _, plan = fixture(tmp_path)
    plan["entries"][0]["path"] = path
    with pytest.raises(ValueError):
        api.archive_snapshot(plan, tmp_path / "archive")


def test_existing_target_and_source_links_rejected(tmp_path):
    api = module()
    source, plan = fixture(tmp_path)
    (source / "link").symlink_to(tmp_path / "coverage.json")
    with pytest.raises(ValueError):
        api.archive_snapshot(plan, tmp_path / "archive")
    (source / "link").unlink()
    archive = tmp_path / "good"
    api.archive_snapshot(plan, archive)
    target = tmp_path / "occupied"
    target.mkdir()
    with pytest.raises(FileExistsError):
        api.restore_snapshot(archive, target)


def test_copy_failure_never_seals_and_records_failure(tmp_path, monkeypatch):
    api = module()
    _, plan = fixture(tmp_path)

    def fail(*args):
        raise OSError("injected copy failure")

    monkeypatch.setattr(api, "_copy_file", fail)
    archive = tmp_path / "archive"
    with pytest.raises(OSError, match="injected"):
        api.archive_snapshot(plan, archive)
    assert not (archive / "SEALED.json").exists()
    assert json.loads((archive / "STATE.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("reserved", ["LOCK", "STATE.json", "RESTORED.json", "STATE.json/nested"])
def test_restore_control_names_cannot_be_payload(tmp_path, reserved):
    api = module()
    _, plan = fixture(tmp_path)
    plan["entries"][1]["path"] = reserved
    with pytest.raises(ValueError):
        api.archive_snapshot(plan, tmp_path / "archive")


def test_restore_failure_is_explicit_and_never_complete(tmp_path, monkeypatch):
    api = module()
    _, plan = fixture(tmp_path)
    archive = tmp_path / "archive"
    api.archive_snapshot(plan, archive)
    original = api._copy_file

    def fail(source, target):
        original(source, target)
        raise OSError("interrupted restore")

    monkeypatch.setattr(api, "_copy_file", fail)
    target = tmp_path / "restore"
    with pytest.raises(OSError, match="interrupted"):
        api.restore_snapshot(archive, target)
    assert not (target / "RESTORED.json").exists()
    assert json.loads((target / "STATE.json").read_text()) == {"status": "failed"}
    api.verify_snapshot(archive)


def test_source_mutation_during_copy_prevents_seal(tmp_path, monkeypatch):
    api = module()
    source, plan = fixture(tmp_path)
    original = api._copy_file

    def mutate(reader, target):
        original(reader, target)
        (source / "late-file").write_bytes(b"late")

    monkeypatch.setattr(api, "_copy_file", mutate)
    with pytest.raises(ValueError, match="changed"):
        api.archive_snapshot(plan, tmp_path / "archive")
    assert not (tmp_path / "archive/SEALED.json").exists()


def test_exclusive_archive_lock_blocks_verification(tmp_path):
    import fcntl

    api = module()
    _, plan = fixture(tmp_path)
    archive = tmp_path / "archive"
    api.archive_snapshot(plan, archive)
    with (archive / "LOCK").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            api.verify_snapshot(archive)


def test_empty_input_directory_rejected_instead_of_lost(tmp_path):
    api = module()
    source, plan = fixture(tmp_path)
    (source / "empty").mkdir()
    with pytest.raises(ValueError, match="empty"):
        api.archive_snapshot(plan, tmp_path / "archive")


def test_archive_payload_hardlink_rejected(tmp_path):
    import os

    api = module()
    _, plan = fixture(tmp_path)
    archive = tmp_path / "archive"
    api.archive_snapshot(plan, archive)
    os.link(archive / "payload/dataset/20260714.parquet", tmp_path / "shared")
    with pytest.raises(ValueError, match="link"):
        api.verify_snapshot(archive)


def test_payload_prefix_collision_rejected_before_destination_creation(tmp_path):
    api = module()
    _, plan = fixture(tmp_path)
    plan["entries"][1]["path"] = "dataset/20260714.parquet/child"
    with pytest.raises(ValueError, match="overlap"):
        api.archive_snapshot(plan, tmp_path / "archive")
    assert not (tmp_path / "archive").exists()


def test_archive_cannot_write_inside_selected_source_tree(tmp_path):
    api = module()
    source, plan = fixture(tmp_path)
    with pytest.raises(ValueError, match="overlap"):
        api.archive_snapshot(plan, source / "archive")
    assert not (source / "archive").exists()


def test_restore_cannot_write_inside_archive(tmp_path):
    api = module()
    _, plan = fixture(tmp_path)
    archive = tmp_path / "archive"
    api.archive_snapshot(plan, archive)
    with pytest.raises(ValueError, match="overlap"):
        api.restore_snapshot(archive, archive / "restored")
    assert not (archive / "restored").exists()
    api.verify_snapshot(archive)


def test_cli_runs_offline_restore_without_plan(tmp_path):
    import subprocess
    import sys

    api = module()
    source, plan = fixture(tmp_path)
    archive = tmp_path / "archive"
    api.archive_snapshot(plan, archive)
    shutil.rmtree(source)
    for name in ("coverage.json", "contract.json"):
        (tmp_path / name).unlink()
    target = tmp_path / "restored"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "restore",
            "--archive",
            str(archive),
            "--destination",
            str(target),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "schema": "minute.snapshot.v1",
        "version": "v1",
        "files": 3,
    }
    assert (target / "RESTORED.json").exists()


@pytest.mark.parametrize("damage", ["manifest", "seal", "metadata-extra"])
def test_archive_metadata_damage_rejected(tmp_path, damage):
    api = module()
    _, plan = fixture(tmp_path)
    archive = tmp_path / "archive"
    api.archive_snapshot(plan, archive)
    if damage == "manifest":
        (archive / "MANIFEST.json").write_text("{}")
    elif damage == "seal":
        (archive / "SEALED.json").write_text("{}")
    else:
        (archive / "unexpected").write_text("extra")
    with pytest.raises(ValueError):
        api.verify_snapshot(archive)


def test_destination_dotdot_cannot_bypass_source_overlap(tmp_path):
    api = module()
    source, plan = fixture(tmp_path)
    with pytest.raises(ValueError, match="overlap"):
        api.archive_snapshot(plan, source / ".." / "source" / "archive")
    assert not (source / "archive").exists()


def test_source_dotdot_cannot_bypass_overlap(tmp_path):
    api = module()
    source, plan = fixture(tmp_path)
    plan["entries"][0]["source"] = str(source / ".." / "source")
    with pytest.raises(ValueError, match="overlap"):
        api.archive_snapshot(plan, source / "archive")
    assert not (source / "archive").exists()
