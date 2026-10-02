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
