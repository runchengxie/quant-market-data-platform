"""Retirement must preflight every shard before deleting any input."""

import fcntl
import hashlib
import json
from pathlib import Path

import pytest

from quant_market_data_platform.snapshot_retirement import retire_snapshot


def fixture(tmp_path):
    root = tmp_path / "artifacts"
    inputs = root / "assets/tushare/a_share/daily_clean_inputs"
    snapshots = [
        inputs / f"a_share_all_20150101_2026101{i}.20261018T230333.1044974" for i in (4, 6)
    ]
    rows = []
    for snapshot in snapshots:
        datasets = {}
        for dataset in ("daily", "adj_factor", "daily_basic", "limit_status"):
            path = snapshot / dataset / "data/trade_date=20150105/part.parquet"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"fixture shard")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            datasets[dataset] = {
                "files": [
                    {
                        "path": "data/trade_date=20150105/part.parquet",
                        "bytes": path.stat().st_size,
                        "sha256": digest,
                    }
                ]
            }
            (snapshot / dataset / "manifest.yml").write_text("status: completed\n")
            if snapshot == snapshots[1]:
                rows.append(
                    {
                        "path": str(path),
                        "peer": str(snapshots[0] / path.relative_to(snapshot)),
                        "bytes": path.stat().st_size,
                        "sha256": digest,
                    }
                )
        (snapshot / "snapshot_receipt.json").write_text(
            json.dumps(
                {
                    "schema_version": "a_share.daily_clean_input_snapshot.v1",
                    "status": "completed",
                    "datasets": datasets,
                }
            )
        )
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps(rows))
    return root, snapshots[1], mapping, tmp_path / "archive", rows


def test_dry_run(tmp_path):
    root, target, mapping, archive, rows = fixture(tmp_path)
    result = retire_snapshot(root, target, mapping, archive)
    assert result["status"] == "dry_run"
    assert all(Path(row["path"]).exists() for row in rows)
    assert not archive.exists()


def test_apply_preserves_metadata_and_donors(tmp_path):
    root, target, mapping, archive, rows = fixture(tmp_path)
    result = retire_snapshot(root, target, mapping, archive, apply=True, writers_stopped=True)
    assert result["status"] == "completed"
    assert all(not Path(row["path"]).exists() and Path(row["peer"]).exists() for row in rows)
    assert len(list(archive.glob("metadata/**/*.*"))) == 5
    assert (target / "retirement_receipt.json").exists()
    assert json.loads((archive / "retirement_receipt.json").read_text())["removed"] == 4
    with pytest.raises((ValueError, FileExistsError)):
        retire_snapshot(root, target, mapping, archive, apply=True, writers_stopped=True)


@pytest.mark.parametrize(
    "failure", ["hash", "reference", "symlink", "peer", "unexpected", "lock", "writers"]
)
def test_rejects_without_removing_any_shard(tmp_path, failure):
    root, target, mapping, archive, rows = fixture(tmp_path)
    held = None
    if failure == "hash":
        Path(rows[-1]["path"]).write_bytes(b"corrupted")
    elif failure == "reference":
        manifest = root / "assets/tushare/a_share/daily_clean/version/manifest.yml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(f"input: {target}\n")
    elif failure == "symlink":
        peer = Path(rows[-1]["peer"])
        peer.rename(peer.with_suffix(".original"))
        peer.symlink_to(peer.with_suffix(".original"))
    elif failure == "peer":
        Path(rows[-1]["peer"]).unlink()
    elif failure == "unexpected":
        (target / "unexpected").write_text("do not remove")
    elif failure == "lock":
        lock = root / ".locks/a_share_current_publish.lock"
        lock.parent.mkdir()
        held = lock.open("w")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises((ValueError, FileNotFoundError, BlockingIOError)):
            retire_snapshot(
                root, target, mapping, archive, apply=True, writers_stopped=failure != "writers"
            )
        assert all(Path(row["path"]).exists() for row in rows)
    finally:
        if held:
            held.close()


def test_other_retirement_targets_cannot_be_donors(tmp_path):
    root, target, mapping, archive, rows = fixture(tmp_path)
    rows.append(dict(rows[0], path=rows[0]["peer"], peer=rows[0]["path"]))
    mapping.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match="retained distinct"):
        retire_snapshot(root, target, mapping, archive, apply=True, writers_stopped=True)
    assert all(Path(row["path"]).exists() for row in rows)


def test_partial_unlink_failure_records_reconstructable_progress(tmp_path, monkeypatch):
    root, target, mapping, archive, rows = fixture(tmp_path)
    original = Path.unlink
    blocked = Path(rows[1]["path"])

    def fail_second_shard(path, *args, **kwargs):
        if path == blocked:
            raise OSError("simulated filesystem failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second_shard)
    with pytest.raises(OSError, match="simulated"):
        retire_snapshot(root, target, mapping, archive, apply=True, writers_stopped=True)
    receipt = json.loads((archive / "retirement_receipt.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["removed"] == 1
    assert receipt["files"] == rows
    target_marker = json.loads((target / "retirement_receipt.json").read_text())
    assert target_marker["status"] == "failed"
    assert (target / "snapshot_receipt.json").exists()
    assert all(
        (target / d / "manifest.yml").exists()
        for d in ("daily", "adj_factor", "daily_basic", "limit_status")
    )
    assert all(Path(row["peer"]).exists() for row in rows)


def test_current_contract_reference_blocks_retirement(tmp_path):
    root, target, mapping, archive, rows = fixture(tmp_path)
    contract = root / "metadata/current_assets/a_share_current.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(json.dumps({"input_snapshot": str(target.relative_to(root))}))
    with pytest.raises(ValueError, match="reference"):
        retire_snapshot(root, target, mapping, archive)
    assert all(Path(row["path"]).exists() for row in rows)


def test_real_daily_manifest_reference_blocks_retirement(tmp_path):
    root, target, mapping, archive, rows = fixture(tmp_path)
    manifest = root / "assets/tushare/a_share/daily/daily_clean_v1/manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(f"input: {target}\n")
    with pytest.raises(ValueError, match="reference"):
        retire_snapshot(root, target, mapping, archive)
    assert all(Path(row["path"]).exists() for row in rows)


def test_broken_retirement_marker_blocks_retirement(tmp_path):
    root, target, mapping, archive, _ = fixture(tmp_path)
    (target / "retirement_receipt.json").symlink_to(target / "missing")
    with pytest.raises(ValueError, match="already retired"):
        retire_snapshot(root, target, mapping, archive)


def test_prior_retirement_donor_cannot_be_retired(tmp_path):
    root, target, mapping, archive, rows = fixture(tmp_path)
    prior = target.parent / "a_share_all_20150101_20261010.20261018T230333.1044974"
    prior.mkdir()
    (prior / "retirement_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "a_share.daily_clean_input_retirement.v1",
                "status": "completed",
                "files": [{"peer": rows[0]["path"]}],
            }
        )
    )
    with pytest.raises(ValueError, match="restore donor"):
        retire_snapshot(root, target, mapping, archive, apply=True, writers_stopped=True)
    assert all(Path(row["path"]).exists() for row in rows)


@pytest.mark.parametrize("kind", ["parent", "relative", "missing"])
def test_noncanonical_prior_restore_dependency_is_rejected(tmp_path, kind):
    root, target, mapping, archive, rows = fixture(tmp_path)
    prior = target.parent / "a_share_all_20150101_20261010.20261018T230333.1044974"
    prior.mkdir()
    if kind == "parent":
        peer = str(prior / ".." / target.name / Path(rows[0]["path"]).relative_to(target))
    elif kind == "relative":
        peer = str(Path(rows[0]["path"]).relative_to(root))
    else:
        peer = str(target.parent / "missing" / "shard.parquet")
    (prior / "retirement_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "a_share.daily_clean_input_retirement.v1",
                "status": "completed",
                "files": [{"peer": peer}],
            }
        )
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        retire_snapshot(root, target, mapping, archive, apply=True, writers_stopped=True)
    assert all(Path(row["path"]).exists() for row in rows)
