"""Regression coverage for rejected checkpoints and auxiliary path escapes."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from quant_market_data_platform.l2_storage.profile import profile_directory


def source(root: Path):
    for symbol in ["000001.SZ", "600000.SH"]:
        for name in ["行情.csv", "逐笔委托.csv", "逐笔成交.csv"]:
            p = root / "20260914" / symbol / name
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("w", encoding="gb18030", newline="") as f:
                w = csv.writer(f)
                w.writerow(["万得代码", "自然日", "时间", "编号"])
                w.writerow([symbol, "20260914", "100000000", "0007"])
    return profile_directory(root, "20260914")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "substituted", "row_count"])
def test_cached_profile_cannot_replace_full_extracted_inventory(tmp_path, monkeypatch, mutation):
    from quant_market_data_platform.l2_storage import build as builder

    inputs = tmp_path / "input"
    p = source(inputs)
    cached = json.loads(json.dumps(p))
    if mutation == "missing":
        cached["members"].pop()
    if mutation == "duplicate":
        cached["members"].append(cached["members"][0])
    if mutation == "substituted":
        cached["members"][0]["path"] = "20260914/000002.SZ/行情.csv"
    if mutation == "row_count":
        cached["members"][0]["rows"] = 99
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "profile.json").write_text(json.dumps(cached))
    archive = tmp_path / "20260914.7z"
    archive.write_bytes(b"fixture")
    # The test isolates cache acceptance after the external extraction boundary.
    monkeypatch.setattr(builder, "extract", lambda *args: inputs)
    with pytest.raises(ValueError):
        builder.build(
            archive, tmp_path / "output", scratch, "unused", hashlib.sha256(b"fixture").hexdigest()
        )
    assert not (tmp_path / "output" / "manifest.json").exists()


def test_resume_rejects_changed_persisted_profile_before_publication(tmp_path):
    from quant_market_data_platform.l2_storage.build import build_csv

    p = source(tmp_path / "input")
    out = tmp_path / "output"
    build_csv(p, out, "a" * 64)
    (out / "manifest.json").unlink()  # model interruption immediately before publication
    cached = json.loads((out / "profile.json").read_text())
    cached["members"][0]["rows"] = 99
    (out / "profile.json").write_text(json.dumps(cached))
    with pytest.raises(ValueError):
        build_csv(p, out, "a" * 64)
    assert not (out / "manifest.json").exists()


def test_completed_resume_rejects_changed_source_csv(tmp_path):
    from quant_market_data_platform.l2_storage.build import build_csv

    p = source(tmp_path / "input")
    out = tmp_path / "output"
    build_csv(p, out, "a" * 64)
    f = Path(p["source_root"]) / p["members"][0]["path"]
    f.write_bytes(f.read_bytes().replace(b"0007", b"0008"))
    with pytest.raises(ValueError):
        build_csv(p, out, "a" * 64)


@pytest.mark.parametrize("auxiliary", ["partial", "json_tmp"])
def test_auxiliary_symlink_cannot_overwrite_external_sentinel(tmp_path, auxiliary):
    from quant_market_data_platform.l2_storage.build import build_csv
    from quant_market_data_platform.l2_storage.proof import save

    victim = tmp_path / "victim"
    victim.write_text("preserve")
    if auxiliary == "json_tmp":
        target = tmp_path / "proof.json"
        target.with_name("proof.json.tmp").symlink_to(victim)
        with pytest.raises(ValueError):
            save(target, {"x": 1})
    else:
        p = source(tmp_path / "input")
        out = tmp_path / "output"
        manifest = build_csv(p, out, "a" * 64)
        (out / "manifest.json").unlink()
        target = out / manifest["files"][0]["path"]
        target.unlink()
        target.with_suffix(".partial").symlink_to(victim)
        with pytest.raises(ValueError):
            build_csv(p, out, "a" * 64)
    assert victim.read_text() == "preserve"


def test_extraction_log_symlink_is_rejected_without_touching_target(tmp_path, monkeypatch):
    from quant_market_data_platform.l2_storage import archive as intake

    victim = tmp_path / "victim"
    victim.write_text("preserve")
    root = tmp_path / "scratch"
    root.mkdir()
    (root / "extraction.log").symlink_to(victim)
    a = tmp_path / "20260914.7z"
    a.write_bytes(b"fixture")
    listing = (
        "header\n----------\nPath = 20260914/000001.SZ/行情.csv\n"
        "Size = 1\nAttributes = A -rw-r--r--\nEncrypted = -\n\n"
    )
    monkeypatch.setattr(intake.subprocess, "check_output", lambda *args, **kwargs: listing)
    # Existing vulnerable code invokes a real failing command after truncating its log.
    with pytest.raises(ValueError):
        intake.extract(a, root, "/bin/false", hashlib.sha256(b"fixture").hexdigest())
    assert victim.read_text() == "preserve"


@pytest.mark.parametrize("location", ["output", "scratch", "alias"])
def test_source_checkout_locations_are_rejected_before_creating_runtime_paths(tmp_path, location):
    from quant_market_data_platform.l2_storage.build import build

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    archive = tmp_path / "20260914.7z"
    archive.write_bytes(b"fixture")
    out = (
        repo / "out"
        if location == "output"
        else alias / "out"
        if location == "alias"
        else tmp_path / "out"
    )
    scratch = repo / "scratch" if location == "scratch" else tmp_path / "scratch"
    with pytest.raises(ValueError):
        build(archive, out, scratch, "/bin/false", hashlib.sha256(b"fixture").hexdigest())
    assert not out.exists() and not scratch.exists()


def test_empty_git_cache_directory_is_not_a_source_checkout(tmp_path):
    from quant_market_data_platform.l2_storage.guards import outside_source_tree

    (tmp_path / ".git").mkdir()
    outside_source_tree(tmp_path / "data" / "future-output")


def test_empty_member_is_accounted_for_in_build_and_independent_verification(tmp_path):
    from quant_market_data_platform.l2_storage.build import build_csv
    from quant_market_data_platform.l2_storage.reader import scan, verify

    inputs = tmp_path / "input"
    p = source(inputs)
    empty = inputs / p["members"][0]["path"]
    with empty.open("r", encoding="gb18030") as f:
        header = f.readline()
    empty.write_text(header, encoding="gb18030")
    p = profile_directory(inputs, "20260914")
    out = tmp_path / "output"
    build_csv(p, out, "a" * 64)
    assert verify(out)["members"] == 6
    assert verify(out)["rows"] == 5
    assert sum(scan(out, k).count_rows() for k in ["trades", "orders", "snapshots"]) == 5


def test_low_space_preserves_inputs_without_verified_publication(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from quant_market_data_platform.l2_storage import build as builder

    p = source(tmp_path / "input")
    out = tmp_path / "output"
    monkeypatch.setattr(builder.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match="space"):
        builder.build_csv(p, out, "a" * 64)
    assert not (out / "manifest.json").exists()
    assert all((Path(p["source_root"]) / m["path"]).exists() for m in p["members"])
