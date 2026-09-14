from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).parents[1] / "scripts/operations/archive_staging.py"
    spec = importlib.util.spec_from_file_location("archive_staging", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_one_is_dry_run_by_default(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "staging" / "probe"
    source.mkdir(parents=True)
    (source / "manifest.yml").write_text(f"output_dir: {source}\n", encoding="utf-8")

    result = module.archive_one(source, tmp_path / "archive" / "probe", apply=False)

    assert result["status"] == "planned"
    assert source.exists()


def test_archive_one_moves_entities_and_rewrites_manifest(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "staging" / "probe"
    destination = tmp_path / "archive" / "probe"
    source.mkdir(parents=True)
    (source / "manifest.yml").write_text(f"output_dir: {source}\n", encoding="utf-8")

    result = module.archive_one(source, destination, apply=True)

    assert result["status"] == "archived"
    assert not source.exists()
    assert destination.joinpath("manifest.yml").read_text(encoding="utf-8") == (
        f"output_dir: {destination}\n"
    )


def test_archive_one_rejects_lock_and_symlink(tmp_path: Path) -> None:
    module = _module()
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "job.lock").touch()
    with pytest.raises(module.StagingArchiveError, match="lock"):
        module.archive_one(locked, tmp_path / "archive-locked", apply=False)

    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "target").write_text("x", encoding="utf-8")
    (linked / "alias").symlink_to(linked / "target")
    with pytest.raises(module.StagingArchiveError, match="symlink"):
        module.archive_one(linked, tmp_path / "archive-linked", apply=False)
