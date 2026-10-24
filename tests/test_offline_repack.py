import csv

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_market_data_platform.offline_equities.identity import symbol_folder
from quant_market_data_platform.offline_equities.integrity import fingerprint, load, save, sha_file
from quant_market_data_platform.offline_equities.repack import run


def source_fixture(root):
    root.mkdir()
    rows = []
    for symbol in ("TPC", "TpC"):
        path = root / "parquet" / symbol_folder(symbol) / "security=abc/year=2026/data.parquet"
        path.parent.mkdir(parents=True)
        table = pa.table(
            {
                "Ticker": [symbol] * 3,
                "Date": ["20260102"] * 3,
                "TimeBarStart": ["04:00", "04:01", "04:02"],
                "raw": ["1.000", "", None],
                "value": [-0.0, None, 2.5],
                "ns": pa.array([1, None, 123456789], type=pa.timestamp("ns")),
                "AuditTotalVolume": [1, 2, 3],
            }
        ).replace_schema_metadata({b"origin": b"exact"})
        pq.write_table(table, path)
        rows.append(
            {
                "symbol": symbol,
                "research_key": "key",
                "settlement_basis": "regular",
                "year": "2026",
                "token": "abc",
                "file": str(path.relative_to(root)),
                "rows": 3,
                "volume": 6,
                "bytes": path.stat().st_size,
                "sha256": sha_file(path),
            }
        )
    with (root / "file_index.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    save(root / "summary.json", {"status": "complete", "rows": 6})
    return rows


def test_exact_repack_and_resume(tmp_path):
    source = tmp_path / "source"
    entries = source_fixture(source)
    output = tmp_path / "output"
    result = run(source, output)
    assert result["rows"] == 6
    assert result["files"] == 2
    for before, after in zip(entries, result["index"], strict=True):
        original = pq.ParquetFile(source / before["file"]).read()
        restored = pq.ParquetFile(output / after["file"]).read()
        assert original.schema.equals(restored.schema, check_metadata=True)
        assert fingerprint(original) == fingerprint(restored)
    assert "symbol=T%70C" in result["index"][1]["file"]
    import duckdb

    with duckdb.connect(str(output / "catalog.duckdb"), read_only=True) as connection:
        assert connection.execute(
            "SELECT symbol, count(*) FROM bars_by_security GROUP BY symbol ORDER BY symbol"
        ).fetchall() == [("TPC", 3), ("TpC", 3)]
    stamps = [(output / row["file"]).stat().st_mtime_ns for row in result["index"]]
    assert run(source, output)["rows"] == 6
    assert stamps == [(output / row["file"]).stat().st_mtime_ns for row in result["index"]]


def test_corrupt_published_file_is_rejected(tmp_path):
    source = tmp_path / "source"
    source_fixture(source)
    output = tmp_path / "output"
    result = run(source, output)
    (output / result["index"][0]["file"]).write_bytes(b"bad")
    with pytest.raises(ValueError, match="integrity"):
        run(source, output)
    assert load(output / "progress.json")["status"] == "failed"
    assert load(output / "summary.json")["status"] == "failed"


def test_overlap_and_unknown_output_protected(tmp_path):
    source = tmp_path / "source"
    source_fixture(source)
    with pytest.raises(ValueError):
        run(source, source / "nested")
    output = tmp_path / "unknown"
    output.mkdir()
    (output / "precious").write_text("keep")
    with pytest.raises(ValueError):
        run(source, output)
    assert (output / "precious").read_text() == "keep"


def test_interrupted_publish_resumes_without_rewriting(tmp_path, monkeypatch):
    from quant_market_data_platform.offline_equities import repack

    source = tmp_path / "source"
    source_fixture(source)
    output = tmp_path / "output"
    original = repack.repack_file
    published = []

    def interrupt(*args):
        row = original(*args)
        published.append(row)
        raise KeyboardInterrupt("after atomic publish")

    monkeypatch.setattr(repack, "repack_file", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run(source, output)
    stamp = (output / published[0]["file"]).stat().st_mtime_ns
    monkeypatch.setattr(repack, "repack_file", original)
    assert run(source, output)["rows"] == 6
    assert (output / published[0]["file"]).stat().st_mtime_ns == stamp


def test_changed_source_fails_progress_and_preserves_output(tmp_path):
    source = tmp_path / "source"
    source_fixture(source)
    output = tmp_path / "output"
    run(source, output)
    summary = load(source / "summary.json")
    save(source / "summary.json", {**summary, "modified": True})
    with pytest.raises(ValueError, match="changed"):
        run(source, output)
    assert load(output / "progress.json")["status"] == "failed"


@pytest.mark.parametrize("name", ["../escape.parquet", "parquet/../../escape", "C:/escape"])
def test_traversal_rejected(tmp_path, name):
    from quant_market_data_platform.offline_equities.repack_io import relative_file

    with pytest.raises(ValueError):
        relative_file(tmp_path, name)


def test_unknown_partition_and_lock_refuse(tmp_path):
    from filelock import FileLock, Timeout

    from quant_market_data_platform.offline_equities.repack_io import destination, own_output

    source = tmp_path / "source"
    entries = source_fixture(source)
    output = tmp_path / "output"
    own_output(source.resolve(), output.resolve())
    with FileLock(str(output / "repack.lock")):
        with pytest.raises(Timeout):
            run(source, output)
    unknown = output / destination(entries[0])
    unknown.parent.mkdir(parents=True)
    unknown.write_bytes(b"keep")
    with pytest.raises(ValueError, match="Unknown"):
        run(source, output)
    assert unknown.read_bytes() == b"keep"


def test_dictionary_expansion_decoded_budget(tmp_path):
    from quant_market_data_platform.offline_equities.repack import read_bounded

    path = tmp_path / "repeated.parquet"
    pq.write_table(pa.table({"text": ["x" * 10000] * 100}), path, use_dictionary=True)
    with pytest.raises(ValueError, match="decoded"):
        read_bounded(path, 100000)
