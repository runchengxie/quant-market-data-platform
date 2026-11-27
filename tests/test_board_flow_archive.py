from __future__ import annotations

import builtins
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_market_data_platform import board_flow_archive as archive
from quant_market_data_platform import board_flow_store as store
from quant_market_data_platform.providers.eastmoney_board_flow import BoardRow, MinuteFlowRow


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "source.sqlite3"
    conn = store.connect(path)
    board = BoardRow("BK0001", "Example", "concept", 1.25, 1.0, 0.5, 50.0)
    store.upsert_catalog(conn, [board], seen_at="2026-11-11T09:31:00+08:00")
    store.snapshot_catalog(
        conn, [board], trade_date="2026-11-11", captured_at="2026-11-11T09:31:00+08:00"
    )
    store.append_snapshot(
        conn, [board], trade_date="2026-11-11", captured_at="2026-11-11T09:31:00+08:00"
    )
    store.upsert_minutes(
        conn,
        [
            MinuteFlowRow(
                "BK0001", "concept", datetime(2026, 11, 11, 9, 31), 1.25, -2, 0.75, 1, 0.25
            )
        ],
        archived_at="2026-11-11T15:10:00+08:00",
    )
    conn.commit()
    conn.close()
    return path


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_lossless_export_verify_restore_without_source_changes(tmp_path: Path) -> None:
    source = _source(tmp_path)
    before = _hash(source)
    destination = tmp_path / "archive"
    exported = archive.export_archive(source, destination)
    assert exported["format"] == "board-flow-parquet-v1"
    assert archive.verify_archive(destination)["rows"] == 5
    restored = tmp_path / "restored.sqlite3"
    archive.restore_archive(destination, restored)
    assert _hash(source) == before
    with sqlite3.connect(source) as left, sqlite3.connect(restored) as right:
        for name in archive.TABLES:
            assert (
                left.execute(f'SELECT * FROM "{name}"').fetchall()
                == right.execute(f'SELECT * FROM "{name}"').fetchall()
            )
        assert right.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_existing_outputs_are_never_replaced(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "archive"
    archive.export_archive(source, destination)
    digest = _hash(destination / "manifest.json")
    with pytest.raises(FileExistsError):
        archive.export_archive(source, destination)
    restored = tmp_path / "restored.sqlite3"
    restored.write_bytes(b"user content")
    with pytest.raises(FileExistsError):
        archive.restore_archive(destination, restored)
    assert restored.read_bytes() == b"user content"
    assert _hash(destination / "manifest.json") == digest


@pytest.mark.parametrize("mutation", ["hash", "schema", "duplicate", "path", "rows", "types"])
def test_archive_corruption_fails_before_restore_publication(tmp_path: Path, mutation: str) -> None:
    destination = tmp_path / "archive"
    archive.export_archive(_source(tmp_path), destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "hash":
        manifest["files"][0]["sha256"] = "0" * 64
    elif mutation == "schema":
        manifest["tables_sql"][0] = "CREATE TABLE stolen (secret TEXT)"
    elif mutation == "duplicate":
        manifest["files"].append(manifest["files"][0])
    elif mutation == "path":
        manifest["files"][0]["table"] = "../outside"
    elif mutation == "rows":
        manifest["files"][0]["rows"] += 1
    else:
        manifest["files"][0]["types"][0] = "REAL"
    manifest_path.write_text(json.dumps(manifest))
    output = tmp_path / "bad.sqlite3"
    with pytest.raises(ValueError):
        archive.restore_archive(destination, output)
    assert not output.exists()


@pytest.mark.parametrize("mutation", ["symlink", "duplicate_key", "wrong_date", "unsorted"])
def test_verified_rows_and_paths_are_bound(tmp_path: Path, mutation: str) -> None:
    destination = tmp_path / "archive"
    archive.export_archive(_source(tmp_path), destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    item = next(item for item in manifest["files"] if item["table"] == "flow_minute")
    path = destination / "flow_minute" / "2026-11-11.parquet"
    if mutation == "symlink":
        copied = tmp_path / "outside.parquet"
        path.rename(copied)
        path.symlink_to(copied)
    else:
        table = pq.ParquetFile(path).read()
        if mutation == "wrong_date":
            table = table.set_column(0, "trade_date", pa.array(["2026-11-12"]))
        else:
            extra = table.to_pylist()[0]
            if mutation == "unsorted":
                extra["trade_time"] = "2026-11-11 09:30:00"
            table = pa.concat_tables([table, pa.Table.from_pylist([extra], schema=table.schema)])
        pq.write_table(table, path)
        item.update(rows=table.num_rows, bytes=path.stat().st_size, sha256=_hash(path))
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        archive.verify_archive(destination)


def test_export_rejects_unknown_source_schema_without_changes(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE user_data (value TEXT)")
    before = _hash(source)
    with pytest.raises(ValueError):
        archive.export_archive(source, tmp_path / "archive")
    assert _hash(source) == before
    assert not (tmp_path / "archive").exists()


def test_export_interruption_is_not_published_and_retry_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "archive"
    original = archive._export_item

    def interrupt(*args, **kwargs):
        raise OSError("simulated write interruption")

    monkeypatch.setattr(archive, "_export_item", interrupt)
    with pytest.raises(OSError, match="simulated"):
        archive.export_archive(source, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".board-flow-archive-*"))
    monkeypatch.setattr(archive, "_export_item", original)
    archive.export_archive(source, target)
    assert archive.verify_archive(target)["rows"] == 5


def test_competing_output_is_preserved_at_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "archive"
    original = archive._publish_new

    def race(stage: Path, destination: Path) -> None:
        if destination == target:
            destination.mkdir()
            (destination / "user.txt").write_text("do not replace")
        original(stage, destination)

    monkeypatch.setattr(archive, "_publish_new", race)
    with pytest.raises(FileExistsError):
        archive.export_archive(source, target)
    assert (target / "user.txt").read_text() == "do not replace"


def test_cli_archive_commands_preserve_their_input(tmp_path: Path, capsys) -> None:
    from quant_market_data_platform import cli

    source = _source(tmp_path)
    target, restored = tmp_path / "archive", tmp_path / "restored.sqlite3"
    assert (
        cli.main(["board-flow", "archive-export", "--db", str(source), "--out", str(target)]) == 0
    )
    assert cli.main(["board-flow", "archive-verify", "--archive", str(target)]) == 0
    assert (
        cli.main(
            ["board-flow", "archive-restore", "--archive", str(target), "--out", str(restored)]
        )
        == 0
    )
    assert (
        cli.main(
            ["board-flow", "archive-restore", "--archive", str(target), "--out", str(restored)]
        )
        == 1
    )
    assert '"status": "failed"' in capsys.readouterr().out


def test_restore_does_not_write_inside_immutable_archive(tmp_path: Path) -> None:
    target = tmp_path / "archive"
    archive.export_archive(_source(tmp_path), target)
    with pytest.raises(ValueError, match="inside"):
        archive.restore_archive(target, target / "restored.sqlite3")
    assert not (target / "restored.sqlite3").exists()


def test_restore_interruption_leaves_no_published_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "archive"
    archive.export_archive(_source(tmp_path), target)

    def interrupt(*args, **kwargs):
        raise OSError("simulated restore interruption")

    monkeypatch.setattr(archive, "_compare_restored", interrupt)
    output = tmp_path / "restored.sqlite3"
    with pytest.raises(OSError, match="simulated"):
        archive.restore_archive(target, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".board-flow-restore-*"))


def test_verify_rejects_unlisted_parquet_and_keeps_missing_dates_missing(tmp_path: Path) -> None:
    target = tmp_path / "archive"
    archive.export_archive(_source(tmp_path), target)
    assert sorted(path.name for path in (target / "flow_minute").iterdir()) == [
        "2026-11-11.parquet"
    ]
    (target / "unlisted.parquet").write_bytes(b"not in manifest")
    with pytest.raises(ValueError, match="unlisted"):
        archive.verify_archive(target)


def test_future_store_schema_cannot_silently_redefine_v1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quant_market_data_platform import _board_flow_archive_contract as contract

    source = _source(tmp_path)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE future(value TEXT)")
    monkeypatch.setattr(contract, "_SCHEMA", contract._SCHEMA + "CREATE TABLE future(value TEXT);")
    with pytest.raises(ValueError, match="v1"):
        archive.export_archive(source, tmp_path / "archive")


def test_missing_optional_arrow_is_a_cli_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from quant_market_data_platform import cli

    original = builtins.__import__

    def unavailable(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "quant_market_data_platform.board_flow_archive" or (
            name == "quant_market_data_platform" and "board_flow_archive" in fromlist
        ):
            raise ModuleNotFoundError("missing optional pyarrow", name="pyarrow")
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    assert cli.main(["board-flow", "archive-verify", "--archive", str(tmp_path)]) == 1
    assert "pyarrow is required" in capsys.readouterr().err


def test_export_keeps_one_snapshot_while_an_independent_writer_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    original, changed = archive._export_item, False

    def export_and_write(connection, stage, item):
        nonlocal changed
        result = original(connection, stage, item)
        if not changed:
            with sqlite3.connect(source) as writer:
                writer.execute("UPDATE board_catalog SET board_name='New observation'")
                writer.execute("UPDATE flow_minute SET main_net_yuan=99")
            changed = True
        return result

    monkeypatch.setattr(archive, "_export_item", export_and_write)
    target = tmp_path / "archive"
    archive.export_archive(source, target)
    catalog = pq.ParquetFile(target / "board_catalog/all.parquet").read().to_pylist()
    minute = pq.ParquetFile(target / "flow_minute/2026-11-11.parquet").read().to_pylist()
    assert changed and catalog[0]["board_name"] == "Example"
    assert minute[0]["main_net_yuan"] == 1.25
    with sqlite3.connect(source) as writer:
        assert writer.execute("SELECT main_net_yuan FROM flow_minute").fetchone() == (99.0,)


@pytest.mark.parametrize("installed", [True, False])
def test_producer_identity_uses_actual_files_and_remains_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed: bool
) -> None:
    from quant_market_data_platform import _board_flow_archive_contract as contract

    if not installed:

        def unavailable(_):
            raise archive.PackageNotFoundError("quant-market-data-platform")

        monkeypatch.setattr(archive, "version", unavailable)
    target = tmp_path / "archive"
    archive.export_archive(_source(tmp_path), target)
    path = target / "manifest.json"
    manifest = json.loads(path.read_text())
    producer = manifest["producer"]
    assert producer["name"] == "quant-market-data-platform"
    assert producer["archive_module_sha256"] == _hash(Path(archive.__file__))
    assert producer["contract_module_sha256"] == _hash(Path(contract.__file__))
    assert producer["schema_sha256"] == contract.SCHEMA_SHA256
    assert ("package_version" in producer) is installed
    assert "source_revision" not in producer
    del manifest["producer"]
    path.write_text(json.dumps(manifest))
    assert archive.verify_archive(target)["rows"] == 5


@pytest.mark.parametrize("field", ["text_blob", "integer_real"])
def test_export_rejects_sqlite_values_that_arrow_would_silently_convert(
    tmp_path: Path, field: str
) -> None:
    source = _source(tmp_path)
    with sqlite3.connect(source) as connection:
        if field == "text_blob":
            connection.execute(
                "UPDATE board_catalog SET board_name=?", (sqlite3.Binary(b"Example"),)
            )
        else:
            connection.execute(
                "INSERT INTO collection_run VALUES (?,?,?,?,?,?,?,?,?)",
                ("run", "archive", "2026-11-11", "start", "finish", "ok", 1, 1.5, "{}"),
            )
    before = _hash(source)
    target = tmp_path / "archive"
    with pytest.raises(ValueError, match="type|lossless"):
        archive.export_archive(source, target)
    assert not target.exists() and _hash(source) == before


@pytest.mark.parametrize("nested", [False, True])
def test_verify_rejects_duplicate_json_keys_at_every_level(tmp_path: Path, nested: bool) -> None:
    target = tmp_path / "archive"
    archive.export_archive(_source(tmp_path), target)
    path = target / "manifest.json"
    text = path.read_text()
    if nested:
        text = text.replace(
            '"name": "quant-market-data-platform"',
            '"name":"ignored","name":"quant-market-data-platform"',
            1,
        )
    else:
        text = text.replace("{\n", '{\n"format":"ignored-first-identity",\n', 1)
    path.write_text(text)
    with pytest.raises(ValueError, match="Duplicate"):
        archive.verify_archive(target)


@pytest.mark.parametrize("operation", ["verify", "restore", "export"])
@pytest.mark.parametrize("mutation", ["manifest", "parquet", "inventory"])
def test_input_drift_never_reports_success_or_publishes_a_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, mutation: str
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "archive"
    if operation != "export":
        archive.export_archive(source, target)
    original, changed = archive.verified_batches, False

    def drift(root, item):
        nonlocal changed
        yield from original(root, item)
        if not changed:
            if mutation == "manifest":
                path = root / "manifest.json"
                data = json.loads(path.read_text())
                data["source_file"] = "changed-after-first-file.sqlite3"
                path.write_text(json.dumps(data))
            elif mutation == "parquet":
                path = root / item["table"] / ((item["date"] or "all") + ".parquet")
                with path.open("ab") as stream:
                    stream.write(b"changed after per-file posthash")
            else:
                (root / "unlisted.parquet").write_bytes(b"new unlisted input")
            changed = True

    monkeypatch.setattr(archive, "verified_batches", drift)
    output = tmp_path / "restored.sqlite3"
    with pytest.raises(ValueError, match="manifest|Manifest|file|unlisted"):
        if operation == "verify":
            archive.verify_archive(target)
        elif operation == "restore":
            archive.restore_archive(target, output)
        else:
            archive.export_archive(source, target)
    assert changed and not output.exists()
    if operation == "export":
        assert not target.exists()
