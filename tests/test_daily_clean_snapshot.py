from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from quant_market_data_platform.daily_clean_snapshot import build_daily_clean_snapshot

DATASETS = ("daily", "adj_factor", "daily_basic", "limit_status")


def _sources(root: Path) -> dict[str, Path]:
    sources = {}
    for name in DATASETS:
        alias = f"a_share_{name}_latest" if name == "limit_status" else f"a_share_all_{name}_latest"
        source = root / "assets/tushare/a_share" / name / alias
        source.mkdir(parents=True)
        for day in ("20260928", "20260929", "20260930"):
            part = source / "data" / f"trade_date={day}"
            part.mkdir(parents=True)
            pq.write_table(
                pa.table({"ts_code": ["000001.SZ"], "trade_date": [day]}), part / "part.parquet"
            )
        (source / "manifest.yml").write_text(
            yaml.safe_dump({"status": "completed", "totals": {"rows": 3, "files": 3}})
        )
        sources[name] = source
    return sources


def test_snapshot_pins_filtered_bytes_and_manifest(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    output = tmp_path / "assets/tushare/a_share/daily_clean_inputs/attempt"
    receipt = build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output)
    assert receipt["status"] == "completed"
    assert json.loads((output / "snapshot_receipt.json").read_text()) == receipt
    for dataset, source in sources.items():
        assert not (output / dataset / "data/trade_date=20260928").exists()
        original = source / "data/trade_date=20260929/part.parquet"
        captured = output / dataset / "data/trade_date=20260929/part.parquet"
        assert not captured.is_symlink()
        assert original.stat().st_ino != captured.stat().st_ino
        before = captured.read_bytes()
        original.write_bytes(b"changed")
        assert captured.read_bytes() == before
        manifest = yaml.safe_load((output / dataset / "manifest.yml").read_text())
        assert manifest["totals"]["rows"] == 2
        assert manifest["totals"]["files"] == 2
        assert manifest["query"]["start_date"] == "20260929"
        entry = receipt["datasets"][dataset]["files"][0]
        assert entry["sha256"] == hashlib.sha256(before).hexdigest()
        assert receipt["datasets"][dataset]["source_manifest_sha256"]


def test_snapshot_never_overwrites_existing_output(tmp_path: Path) -> None:
    _sources(tmp_path)
    output = tmp_path / "inputs"
    output.mkdir()
    (output / "keep").write_text("evidence")
    with pytest.raises(FileExistsError):
        build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output)
    assert (output / "keep").read_text() == "evidence"


def test_snapshot_rejects_missing_end_partition_before_writing(tmp_path: Path) -> None:
    _sources(tmp_path)
    output = tmp_path / "inputs"
    with pytest.raises(ValueError, match="end-date partition"):
        build_daily_clean_snapshot(tmp_path, "20260929", "20261001", output)
    assert not output.exists()


def test_snapshot_rejects_source_change_without_completed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quant_market_data_platform import daily_clean_snapshot as module

    sources = _sources(tmp_path)
    output = tmp_path / "inputs"
    copy = module._copy_file

    def mutate(source: Path, destination: Path) -> None:
        copy(source, destination)
        (sources["daily"] / "manifest.yml").write_text("status: running\n")

    monkeypatch.setattr(module, "_copy_file", mutate)
    with pytest.raises(ValueError, match="source changed"):
        build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output)
    assert not (output / "snapshot_receipt.json").exists()


def test_snapshot_rejects_output_under_symlink_ancestor(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    alias = tmp_path / "output-alias"
    alias.symlink_to(sources["daily"], target_is_directory=True)
    with pytest.raises(ValueError, match="alias indirection"):
        build_daily_clean_snapshot(tmp_path, "20260929", "20260930", alias / "attempt")
    assert not (sources["daily"] / "attempt").exists()


def test_copy_uses_independent_fallback_when_reflink_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from quant_market_data_platform import daily_clean_snapshot as module

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_bytes(b"payload")
    monkeypatch.setattr(module.shutil, "which", lambda _: "/non-gnu/cp")
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1)
    )
    module._copy_file(source, destination)
    assert source.read_bytes() == destination.read_bytes()
    assert source.stat().st_ino != destination.stat().st_ino


def test_snapshot_detects_rewrite_even_with_restored_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from quant_market_data_platform import daily_clean_snapshot as module

    sources = _sources(tmp_path)
    output = tmp_path / "inputs"
    copy = module._copy_file
    original = sources["daily"] / "data/trade_date=20260929/part.parquet"
    stamp = original.stat()

    def mutate(source: Path, destination: Path) -> None:
        copy(source, destination)
        if source == original:
            payload = original.read_bytes()
            original.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
            os.utime(original, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    monkeypatch.setattr(module, "_copy_file", mutate)
    with pytest.raises(ValueError, match="source changed"):
        build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output)
    assert not (output / "snapshot_receipt.json").exists()


def _donor(tmp_path):
    sources = _sources(tmp_path)
    inputs = tmp_path / "assets/tushare/a_share/daily_clean_inputs"
    donor = inputs / "a_share_all_20260929_20260930.20260930T230000.100"
    build_daily_clean_snapshot(tmp_path, "20260929", "20260930", donor)
    return sources, donor, inputs / "a_share_all_20260929_20260930.20261001T230000.101"


def test_reuse_links_immutable_snapshot_but_isolates_raw_and_metadata(tmp_path):
    sources, donor, output = _donor(tmp_path)
    receipt = build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output, reuse_from=donor)
    relative = Path("daily/data/trade_date=20260929/part.parquet")
    assert (donor / relative).stat().st_ino == (output / relative).stat().st_ino
    raw = sources["daily"] / "data/trade_date=20260929/part.parquet"
    assert raw.stat().st_ino != (output / relative).stat().st_ino
    before = (output / relative).read_bytes()
    raw.write_bytes(b"changed")
    assert (output / relative).read_bytes() == before
    assert (donor / relative).read_bytes() == before
    assert receipt["storage"]["reused_files"] == 8
    assert receipt["storage"]["raw_isolation_files"] == 12
    assert receipt["storage"]["raw_isolation_passes"] == 2
    for relative in ("snapshot_receipt.json", "daily/manifest.yml"):
        assert (donor / relative).stat().st_ino != (output / relative).stat().st_ino


def test_reuse_copies_changed_source(tmp_path):
    sources, donor, output = _donor(tmp_path)
    raw = sources["daily"] / "data/trade_date=20260929/part.parquet"
    pq.write_table(pa.table({"ts_code": ["changed"], "trade_date": ["20260929"]}), raw)
    receipt = build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output, reuse_from=donor)
    relative = "daily/data/trade_date=20260929/part.parquet"
    assert (output / relative).stat().st_ino != (donor / relative).stat().st_ino
    assert receipt["storage"]["reused_files"] == 7
    assert receipt["storage"]["copied_files"] == 1


@pytest.mark.parametrize("invalid", ["writable", "retired", "unexpected", "hash", "alias"])
def test_reuse_rejects_invalid_donor_before_output(tmp_path, invalid):
    _, donor, output = _donor(tmp_path)
    shard = donor / "daily/data/trade_date=20260929/part.parquet"
    if invalid == "writable":
        shard.chmod(0o644)
    elif invalid == "retired":
        (donor / "retirement_receipt.json").write_text("{}")
    elif invalid == "unexpected":
        (donor / "extra").write_text("unexpected")
    elif invalid == "hash":
        shard.chmod(0o644)
        shard.write_bytes(b"corrupt")
        shard.chmod(0o444)
    elif invalid == "alias":
        alias = donor.parent / "latest"
        alias.symlink_to(donor)
        donor = alias
    with pytest.raises(ValueError):
        build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output, reuse_from=donor)
    assert not output.exists()


@pytest.mark.parametrize("error_number", [18, 13])
def test_reuse_only_cross_device_link_error_falls_back(tmp_path, monkeypatch, error_number):
    import os

    _, donor, output = _donor(tmp_path)

    def fail_link(*args, **kwargs):
        raise OSError(error_number, "link failure")

    monkeypatch.setattr(os, "link", fail_link)
    if error_number == 13:
        with pytest.raises(OSError):
            build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output, reuse_from=donor)
        assert not (output / "snapshot_receipt.json").exists()
    else:
        receipt = build_daily_clean_snapshot(
            tmp_path, "20260929", "20260930", output, reuse_from=donor
        )
        assert receipt["storage"]["reused_files"] == 0
        assert receipt["storage"]["copied_files"] == 8


def test_latest_completed_reuse_skips_retired_and_copies_when_none(tmp_path):
    _, donor, output = _donor(tmp_path)
    receipt = build_daily_clean_snapshot(
        tmp_path, "20260929", "20260930", output, reuse_latest_completed=True
    )
    assert receipt["storage"]["reused_files"] == 8
    (output / "retirement_receipt.json").write_text("{}")
    (donor / "retirement_receipt.json").write_text("{}")
    next_output = output.with_name("a_share_all_20260929_20260930.20261002T230000.102")
    receipt = build_daily_clean_snapshot(
        tmp_path, "20260929", "20260930", next_output, reuse_latest_completed=True
    )
    assert receipt["storage"]["reused_files"] == 0


def test_reuse_refuses_donor_shard_shared_with_mutable_raw(tmp_path):
    import os

    sources, donor, output = _donor(tmp_path)
    raw = sources["daily"] / "data/trade_date=20260929/part.parquet"
    shard = donor / "daily/data/trade_date=20260929/part.parquet"
    shard.unlink()
    os.link(raw, shard)
    shard.chmod(0o444)
    with pytest.raises(ValueError, match="mutable raw"):
        build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output, reuse_from=donor)
    assert not (output / "snapshot_receipt.json").exists()


def test_snapshot_cli_reuse_options_and_actual_capture(tmp_path, capsys):
    from quant_market_data_platform.cli import build_parser

    _, donor, output = _donor(tmp_path)
    parser = build_parser()
    args = parser.parse_args(
        [
            "governance",
            "snapshot-clean-inputs",
            "--artifacts-root",
            str(tmp_path),
            "--start-date",
            "20260929",
            "--end-date",
            "20260930",
            "--out-dir",
            str(output),
            "--reuse-from",
            str(donor),
        ]
    )
    assert args.handler(args) == 0
    assert (
        json.loads((output / "snapshot_receipt.json").read_text())["storage"]["reused_files"] == 8
    )
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "governance",
                "snapshot-clean-inputs",
                "--start-date",
                "20260929",
                "--end-date",
                "20260930",
                "--out-dir",
                str(output),
                "--reuse-from",
                str(donor),
                "--reuse-latest-completed",
            ]
        )


def test_latest_reuse_refuses_corrupt_completed_candidate(tmp_path):
    _, donor, output = _donor(tmp_path)
    (donor / "extra").write_text("unexpected")
    with pytest.raises(ValueError, match="inventory"):
        build_daily_clean_snapshot(
            tmp_path, "20260929", "20260930", output, reuse_latest_completed=True
        )
    assert not output.exists()


def test_reuse_linked_snapshot_survives_removing_another_directory_entry(tmp_path):
    _, donor, output = _donor(tmp_path)
    build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output, reuse_from=donor)
    relative = "daily/data/trade_date=20260929/part.parquet"
    before = (output / relative).read_bytes()
    (donor / relative).unlink()
    assert (output / relative).read_bytes() == before


def test_reuse_detects_donor_manifest_mutation(tmp_path, monkeypatch):
    import os

    _, donor, output = _donor(tmp_path)
    original_link = os.link
    manifest = donor / "daily/manifest.yml"

    def mutate(*args, **kwargs):
        original_link(*args, **kwargs)
        manifest.chmod(0o644)
        manifest.write_text("status: running\n")
        manifest.chmod(0o444)

    monkeypatch.setattr(os, "link", mutate)
    with pytest.raises(ValueError, match="donor inventory or metadata changed"):
        build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output, reuse_from=donor)
    assert not (output / "snapshot_receipt.json").exists()


def test_reuse_rejects_incomplete_dataset_manifest(tmp_path):
    _, donor, output = _donor(tmp_path)
    manifest = donor / "daily/manifest.yml"
    manifest.chmod(0o644)
    manifest.write_text("status: running\n")
    manifest.chmod(0o444)
    with pytest.raises(ValueError, match="manifest"):
        build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output, reuse_from=donor)
    assert not output.exists()


def test_reuse_rejects_donor_linked_to_raw_outside_capture_dates(tmp_path):
    import os

    sources, donor, output = _donor(tmp_path)
    shard = donor / "daily/data/trade_date=20260929/part.parquet"
    outside_raw = sources["adj_factor"] / "data/trade_date=20260928/part.parquet"
    outside_raw.write_bytes(shard.read_bytes())
    shard.unlink()
    os.link(outside_raw, shard)
    shard.chmod(0o444)
    with pytest.raises(ValueError, match="mutable raw"):
        build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output, reuse_from=donor)
    assert not (output / "snapshot_receipt.json").exists()


def test_reuse_final_check_detects_new_raw_inode_alias_outside_dates(tmp_path, monkeypatch):
    import os

    sources, donor, output = _donor(tmp_path)
    outside_raw = sources["adj_factor"] / "data/trade_date=20260928/part.parquet"
    shard = donor / "daily/data/trade_date=20260929/part.parquet"
    original_link = os.link
    changed = False

    def link_and_alias(*args, **kwargs):
        nonlocal changed
        original_link(*args, **kwargs)
        if not changed:
            changed = True
            outside_raw.unlink()
            original_link(shard, outside_raw)

    monkeypatch.setattr(os, "link", link_and_alias)
    with pytest.raises(ValueError):
        build_daily_clean_snapshot(tmp_path, "20260929", "20260930", output, reuse_from=donor)
    assert not (output / "snapshot_receipt.json").exists()
