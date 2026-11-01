from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_market_data_platform.quality_pilot import L2PilotOptions, run_l2_pilot


def test_pilot_writes_canonical_rows_and_sparse_labels(tmp_path: Path) -> None:
    source = tmp_path / "deal_2024-01-02.parquet"
    pq.write_table(
        pa.table(
            {
                "SecuCode": ["000001", "000001", None],
                "TradingDay": [20240102, 20240102, 20240102],
                "DealTime": [1, 2, 3],
                "DealID": [7, 8, 9],
                "Price": [100, 0, 101],
                "Volume": [10, 20, 30],
            }
        ),
        source,
    )

    report = run_l2_pilot(L2PilotOptions([source], tmp_path / "out"))

    assert report["summary"] == {"keep": 1, "tag": 1, "exclude": 1}
    canonical = pq.read_table(tmp_path / "out" / "canonical" / source.name)
    assert canonical.num_rows == 1
    labels = pq.read_table(tmp_path / "out" / "labels" / f"{source.stem}.parquet").to_pydict()
    assert labels["row_number"] == [1, 2]
    assert labels["decision"] == ["tag", "exclude"]
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["files_processed"] == 1


def test_pilot_limits_rows_per_file(tmp_path: Path) -> None:
    source = tmp_path / "order_2024-01-02.parquet"
    pq.write_table(
        pa.table(
            {
                "SecuCode": ["000001", "000001"],
                "TradingDay": [20240102, 20240102],
                "OrderTime": [1, 2],
                "OrderID": [7, 8],
                "Price": [100, 101],
                "Volume": [10, 20],
            }
        ),
        source,
    )

    report = run_l2_pilot(L2PilotOptions([source], tmp_path / "out", max_rows_per_file=1))

    assert report["reports"][0]["rows_read"] == 1
    assert report["summary"] == {"keep": 1, "tag": 0, "exclude": 0}


def _raw_file(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "SecuCode": ["000001", "000001"],
                "DealTime": [1, 2],
                "DealID": [7, 8],
                "Price": [100, 0],
            }
        ),
        path,
    )
    return path.read_bytes()


@pytest.mark.parametrize("alias", ["lexical", "symlink", "hardlink"])
def test_pilot_rejects_input_output_alias_before_modifying_raw(tmp_path, alias):
    output = tmp_path / "out"
    destination = output / "canonical" / "deal_x.parquet"
    if alias == "lexical":
        raw = destination
        original = _raw_file(raw)
    else:
        raw = tmp_path / "raw" / "deal_x.parquet"
        original = _raw_file(raw)
        destination.parent.mkdir(parents=True)
        if alias == "symlink":
            destination.symlink_to(raw)
        else:
            destination.hardlink_to(raw)
    with pytest.raises((ValueError, FileExistsError)):
        run_l2_pilot(L2PilotOptions([raw], output))
    assert raw.read_bytes() == original
    assert not (output / "manifest.json").exists()
    assert not (output / "labels").exists()


@pytest.mark.parametrize(
    "names", [("deal_same.parquet", "deal_same.parquet"), ("deal_same.parquet", "deal_same.pq")]
)
def test_pilot_rejects_canonical_or_label_name_collision_before_any_write(tmp_path, names):
    sources = [tmp_path / str(index) / name for index, name in enumerate(names)]
    originals = [_raw_file(path) for path in sources]
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="collid"):
        run_l2_pilot(L2PilotOptions(sources, output))
    assert not output.exists()
    assert [p.read_bytes() for p in sources] == originals


@pytest.mark.parametrize(
    "target", ["canonical/deal_x.parquet", "labels/deal_x.parquet", "manifest.json"]
)
def test_pilot_refuses_existing_targets_before_creating_any_other_output(tmp_path, target):
    raw = tmp_path / "raw" / "deal_x.parquet"
    original = _raw_file(raw)
    output = tmp_path / "out"
    existing = output / target
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"preserve existing output")
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError):
        run_l2_pilot(L2PilotOptions([raw], output))
    after = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    assert after == before
    assert raw.read_bytes() == original


@pytest.mark.parametrize("invalid", ["missing", "not-parquet"])
def test_pilot_preflights_later_input_before_any_output(tmp_path, invalid):
    first = tmp_path / "raw" / "deal_a.parquet"
    original = _raw_file(first)
    second = tmp_path / "raw" / "deal_b.parquet"
    if invalid == "not-parquet":
        second.write_bytes(b"not parquet")
    output = tmp_path / "out"
    with pytest.raises((ValueError, OSError)):
        run_l2_pilot(L2PilotOptions([first, second], output))
    assert not output.exists()
    assert first.read_bytes() == original


@pytest.mark.parametrize(
    "late_target", ["canonical/deal_x.parquet", "labels/deal_x.parquet", "manifest.json"]
)
def test_pilot_exclusive_outputs_preserve_a_late_arriving_destination(
    tmp_path, monkeypatch, late_target
):
    from quant_market_data_platform import quality_pilot

    raw = tmp_path / "raw" / "deal_x.parquet"
    original = _raw_file(raw)
    output = tmp_path / "out"
    target = output / late_target
    classify = quality_pilot._classify_batch

    def concurrent_destination(batch, kind, offset):
        result = classify(batch, kind, offset)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"another writer owns this output")
        return result

    monkeypatch.setattr(quality_pilot, "_classify_batch", concurrent_destination)
    with pytest.raises(FileExistsError):
        run_l2_pilot(L2PilotOptions([raw], output))
    assert target.read_bytes() == b"another writer owns this output"
    assert raw.read_bytes() == original


def test_pilot_keeps_fresh_leaf_contract_with_existing_directory_and_multiple_batches(tmp_path):
    sources = [tmp_path / "raw" / f"deal_{index}.parquet" for index in range(2)]
    originals = [_raw_file(path) for path in sources]
    output = tmp_path / "out"
    output.mkdir()
    sentinel = output / "unrelated.txt"
    sentinel.write_text("preserve")
    result = run_l2_pilot(L2PilotOptions(sources, output, batch_size=1))
    assert result["schema_version"] == "market_data_platform.l2_pilot.v1"
    assert result["files_processed"] == 2
    assert result["summary"] == {"keep": 2, "tag": 2, "exclude": 0}
    for path in sources:
        assert pq.read_table(output / "canonical" / path.name).num_rows == 1
        labels = pq.read_table(output / "labels" / f"{path.stem}.parquet").to_pydict()
        assert labels["row_number"] == [1] and labels["decision"] == ["tag"]
    assert sentinel.read_text() == "preserve"
    assert [path.read_bytes() for path in sources] == originals


def test_pilot_rejects_a_symlinked_output_subdirectory_before_writing(tmp_path):
    raw = tmp_path / "raw" / "deal_x.parquet"
    original = _raw_file(raw)
    outside = tmp_path / "outside"
    outside.mkdir()
    output = tmp_path / "out"
    output.mkdir()
    (output / "canonical").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="parent"):
        run_l2_pilot(L2PilotOptions([raw], output))
    assert not list(outside.iterdir())
    assert not (output / "manifest.json").exists()
    assert raw.read_bytes() == original


def test_pilot_cli_rejects_existing_manifest_without_success_output(tmp_path, capsys):
    from quant_market_data_platform.cli import main

    raw = tmp_path / "raw" / "deal_x.parquet"
    original = _raw_file(raw)
    output = tmp_path / "out"
    output.mkdir()
    manifest = output / "manifest.json"
    manifest.write_text("preserve prior manifest")
    with pytest.raises(FileExistsError):
        main(["quality", "pilot", "--file", str(raw), "--output-dir", str(output)])
    assert capsys.readouterr().out == ""
    assert manifest.read_text() == "preserve prior manifest"
    assert raw.read_bytes() == original
    assert not (output / "canonical").exists()


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_pilot_rejects_duplicate_input_identity_before_outputs(tmp_path, alias):
    raw = tmp_path / "raw" / "deal_x.parquet"
    original = _raw_file(raw)
    duplicate = tmp_path / "raw" / "deal_other.parquet"
    if alias == "symlink":
        duplicate.symlink_to(raw)
    else:
        duplicate.hardlink_to(raw)
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="aliases"):
        run_l2_pilot(L2PilotOptions([raw, duplicate], output))
    assert not output.exists()
    assert raw.read_bytes() == duplicate.read_bytes() == original
