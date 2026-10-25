"""Real Parquet handles must close before failed offline jobs can be recovered."""

from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_market_data_platform.offline_equities import integrity, staging


def _input(root: Path, name: str, *, date: str = "20260102") -> dict:
    path = root / name
    table = pa.table(
        {
            "Date": [date],
            "Ticker": ["WOLF"],
            "TimeBarStart": ["09:30"],
            "AuditCandidateSecurityKey": [""],
            "AuditTotalVolume": [7],
        }
    )
    pq.write_table(table, path, write_page_checksum=True)
    return {
        "file": name,
        "date": "20260102",
        "mtime_ns": path.stat().st_mtime_ns,
        "sha256": integrity.sha_file(path),
        "rows": 1,
    }


def _track_readers(monkeypatch) -> list:
    opened = []
    factory = pq.ParquetFile

    def open_reader(*args, **kwargs):
        reader = factory(*args, **kwargs)
        opened.append(reader)
        return reader

    monkeypatch.setattr(pq, "ParquetFile", open_reader)
    return opened


@pytest.mark.parametrize("failure", ["rows", "date", "progress", None])
def test_input_reader_closes_on_integrity_and_progress_errors(tmp_path, monkeypatch, failure):
    item = _input(tmp_path, "input.parquet", date="20260103" if failure == "date" else "20260102")
    if failure == "rows":
        item["rows"] = 2
    readers = _track_readers(monkeypatch)
    sinks = {}

    def update(**_):
        if failure == "progress":
            raise OSError("progress write failed")

    args = SimpleNamespace(source=str(tmp_path), symbols=[], buckets=1, stage_rows=1)
    try:
        if failure:
            with pytest.raises((ValueError, OSError)):
                staging.append_input(item, args, SimpleNamespace(update=update), sinks, tmp_path)
        else:
            assert staging.append_input(
                item, args, SimpleNamespace(update=update), sinks, tmp_path
            ) == (1, 7, {"file": item["file"], "sha256": item["sha256"], "selected_rows": 1})
        assert len(readers) == 1
        assert readers[0].closed
    finally:
        for reader in readers:
            reader.close()
        for sink in sinks.values():
            sink.close()


def test_readback_closes_reader_on_corrupt_proof(tmp_path, monkeypatch):
    sink = integrity.Sink(
        tmp_path / "sink.parquet", pa.schema([("AuditTotalVolume", pa.int64())]), 1, 1
    )
    sink.append(pa.table({"AuditTotalVolume": [7]}))
    sink.close()
    sink.proofs[0]["sha256"] = "0" * 64
    readers = _track_readers(monkeypatch)
    try:
        with pytest.raises(ValueError):
            sink.verify()
        assert len(readers) == 1
        assert readers[0].closed
    finally:
        for reader in readers:
            reader.close()


def test_sink_closes_writer_when_pending_flush_fails(tmp_path, monkeypatch):
    sink = integrity.Sink(
        tmp_path / "sink.parquet", pa.schema([("AuditTotalVolume", pa.int64())]), 2, 1
    )
    sink.append(pa.table({"AuditTotalVolume": [7, 8]}))
    sink.append(pa.table({"AuditTotalVolume": [9]}))
    assert sink.writer is not None

    def fail_write(*_, **__):
        raise OSError("injected Parquet write failure")

    # Inject only the external write failure; buffer and writer lifecycle are real.
    monkeypatch.setattr(sink.writer, "write_table", fail_write)
    try:
        with pytest.raises(OSError, match="injected Parquet write failure"):
            sink.close()
        assert not sink.writer.is_open
    finally:
        sink.writer.close()


def test_failed_month_closes_open_writers_without_publishing(tmp_path, monkeypatch):
    # This unit case exercises writer cleanup; integration tests retain the real disk guard.
    monkeypatch.setattr(staging.shutil, "disk_usage", lambda _: SimpleNamespace(free=20 << 30))
    first = _input(tmp_path, "first.parquet")
    second = _input(tmp_path, "second.parquet", date="20260103")
    sinks = []
    factory = staging.Sink

    def create_sink(*args, **kwargs):
        sink = factory(*args, **kwargs)
        sinks.append(sink)
        return sink

    monkeypatch.setattr(staging, "Sink", create_sink)
    args = SimpleNamespace(
        source=str(tmp_path), output=str(tmp_path), symbols=[], buckets=1, stage_rows=1
    )
    job = SimpleNamespace(update=lambda **_: None)
    try:
        with pytest.raises(ValueError, match="Integrity check failed"):
            staging.build_month("202601", [first, second], args, job, (0, 0, 1, 2))
        assert len(sinks) == 1
        assert not sinks[0].writer.is_open
        assert not (tmp_path / "staging/month=202601").exists()
        assert not (tmp_path / "checkpoints/stage/202601.json").exists()
        assert list((tmp_path / "work/stage-202601").glob("*.parquet"))
    finally:
        for sink in sinks:
            sink.close()


def test_failed_input_preserved_when_one_bucket_flush_also_fails(tmp_path, monkeypatch):
    sinks = [
        integrity.Sink(
            tmp_path / f"sink-{i}.parquet", pa.schema([("AuditTotalVolume", pa.int64())]), 2, 1
        )
        for i in range(2)
    ]
    for sink in sinks:
        sink.append(pa.table({"AuditTotalVolume": [7, 8]}))
        sink.append(pa.table({"AuditTotalVolume": [9]}))

    def fail_first_write(*_, **__):
        raise OSError("injected first bucket flush failure")

    def fail_second_write(*_, **__):
        raise OSError("injected second bucket flush failure")

    monkeypatch.setattr(sinks[0].writer, "write_table", fail_first_write)
    monkeypatch.setattr(sinks[1].writer, "write_table", fail_second_write)
    try:
        with pytest.raises(ValueError, match="original input integrity failure") as raised:
            with staging._staging_sinks() as owned:
                owned.update(enumerate(sinks))
                raise ValueError("original input integrity failure")
        assert any("first bucket flush failure" in note for note in raised.value.__notes__)
        assert any("second bucket flush failure" in note for note in raised.value.__notes__)
        assert all(sink.writer is not None and not sink.writer.is_open for sink in sinks)
    finally:
        for sink in sinks:
            if sink.writer is not None:
                sink.writer.close()


def test_pending_flush_error_preserved_when_writer_close_also_fails(tmp_path, monkeypatch):
    sink = integrity.Sink(
        tmp_path / "sink.parquet", pa.schema([("AuditTotalVolume", pa.int64())]), 2, 1
    )
    sink.append(pa.table({"AuditTotalVolume": [7, 8]}))
    sink.append(pa.table({"AuditTotalVolume": [9]}))
    assert sink.writer is not None
    original_close = sink.writer.close

    def fail_write(*_, **__):
        raise OSError("original flush failure")

    def fail_close():
        original_close()
        raise OSError("injected close failure")

    monkeypatch.setattr(sink.writer, "write_table", fail_write)
    monkeypatch.setattr(sink.writer, "close", fail_close)
    try:
        with pytest.raises(OSError, match="original flush failure") as raised:
            sink.close()
        assert any("injected close failure" in note for note in raised.value.__notes__)
        assert not sink.writer.is_open
    finally:
        original_close()
