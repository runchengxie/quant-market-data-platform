"""Lossless board-flow archive export, verification and restoration to new targets."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from quant_market_data_platform import _board_flow_archive_contract as contract
from quant_market_data_platform._board_flow_archive_contract import (
    BATCH_ROWS,
    DAILY,
    FORMAT,
    TABLES,
    TYPES,
    ManifestSnapshot,
    capture_manifest,
    check_schema,
    check_snapshot,
    digest,
    file_path,
    verified_batches,
)
from quant_market_data_platform.board_flow_store import _SCHEMA


def _new_target(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)


def _publish_new(source: Path, destination: Path) -> None:
    """Never overwrite a concurrently created target; fail closed if unsupported."""
    if os.name == "nt":
        os.rename(source, destination)
    else:
        library = ctypes.CDLL(None, use_errno=True)
        rename = getattr(library, "renameat2", None)
        if rename is None:
            raise OSError("Atomic no-replace rename is unavailable on this host")
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
        if result:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(destination))


def _query(item: dict) -> str:
    columns = ",".join(f'"{name}"' for name in item["columns"])
    order = ",".join(f'"{name}"' for name in item["key"])
    where = " WHERE trade_date=?" if item["date"] is not None else ""
    return f'SELECT {columns} FROM "{item["table"]}"{where} ORDER BY {order}'


def _parameters(item: dict) -> tuple:
    return (item["date"],) if item["date"] is not None else ()


def _arrow(rows: list | tuple, item: dict) -> pa.Table:
    values = list(zip(*rows, strict=True)) if rows else [[] for _ in item["columns"]]
    for column, kind in zip(values, item["types"], strict=True):
        for value in column:
            _check_value(value, kind)
    table = pa.Table.from_arrays(
        [
            pa.array(value, type=TYPES[kind])
            for value, kind in zip(values, item["types"], strict=True)
        ],
        names=item["columns"],
    )
    converted = list(zip(*(column.to_pylist() for column in table.columns), strict=True))
    if converted != [tuple(row) for row in rows]:
        raise ValueError("SQLite values cannot be represented losslessly in the archive")
    return table


def _check_value(value, kind: str) -> None:
    if value is None:
        return
    allowed = {"TEXT": {str}, "INTEGER": {int}, "REAL": {int, float}}
    if type(value) not in allowed[kind]:
        raise ValueError("SQLite storage type does not match the declared archive type")
    if kind == "REAL" and type(value) is int and float(value) != value:
        raise ValueError("SQLite REAL value cannot be represented losslessly")


def verify_archive(root: Path) -> dict:
    """Verify the complete file inventory without opening the live SQLite store."""
    root = Path(root).resolve(strict=True)
    snapshot = capture_manifest(root)
    _verify_snapshot(root, snapshot)
    return _summary(snapshot)


def _verify_snapshot(root: Path, snapshot: ManifestSnapshot) -> None:
    for item in snapshot.data["files"]:
        for _ in verified_batches(root, item):
            pass
    check_snapshot(root, snapshot)


def _summary(snapshot: ManifestSnapshot) -> dict:
    return {
        "format": FORMAT,
        "files": len(snapshot.data["files"]),
        "rows": sum(item["rows"] for item in snapshot.data["files"]),
        "manifest_sha256": snapshot.sha256,
    }


def _restore_into(root: Path, manifest: dict, output: Path) -> None:
    with sqlite3.connect(output) as connection:
        connection.executescript(_SCHEMA.replace("PRAGMA journal_mode=WAL;", ""))
        for item in manifest["files"]:
            columns = ",".join(f'"{name}"' for name in item["columns"])
            placeholders = ",".join("?" for _ in item["columns"])
            for batch in verified_batches(root, item):
                connection.executemany(
                    f'INSERT INTO "{item["table"]}" ({columns}) VALUES ({placeholders})',
                    zip(*(column.to_pylist() for column in batch.columns), strict=True),
                )
        for name in ("user_version", "application_id"):
            connection.execute(f"PRAGMA {name}={manifest[name]}")
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Restored SQLite integrity check failed")
        _compare_restored(connection, root, manifest)


def _compare_restored(connection: sqlite3.Connection, root: Path, manifest: dict) -> None:
    for item in manifest["files"]:
        cursor = connection.execute(_query(item), _parameters(item))
        for batch in verified_batches(root, item):
            if not _arrow(cursor.fetchmany(batch.num_rows), item).equals(
                pa.Table.from_batches([batch])
            ):
                raise ValueError("Restored values differ from archive")
        if cursor.fetchone() is not None:
            raise ValueError("Restored row count differs from archive")


def restore_archive(root: Path, destination: Path) -> dict:
    """Restore only to a new path after validating every artifact; never execute manifest SQL."""
    root, destination = Path(root).resolve(strict=True), Path(destination).absolute()
    if destination.resolve().is_relative_to(root):
        raise ValueError("Cannot restore inside the immutable input archive")
    _new_target(destination)
    return _restore_checked(root, capture_manifest(root), destination)


def _restore_checked(root: Path, snapshot: ManifestSnapshot, destination: Path) -> dict:
    result = _summary(snapshot)
    _verify_snapshot(root, snapshot)
    with tempfile.TemporaryDirectory(
        prefix=".board-flow-restore-", dir=destination.parent
    ) as stage:
        output = Path(stage) / "restored.sqlite3"
        try:
            _restore_into(root, snapshot.data, output)
        except sqlite3.Error as error:
            raise ValueError("Restored SQLite violates the archive schema") from error
        check_snapshot(root, snapshot)
        _publish_new(output, destination)
    return {**result, "destination": str(destination)}


def _export_item(connection: sqlite3.Connection, stage: Path, item: dict) -> dict:
    path = file_path(stage, item)
    path.parent.mkdir(exist_ok=True)
    schema = pa.schema(
        [(name, TYPES[kind]) for name, kind in zip(item["columns"], item["types"], strict=True)]
    )
    cursor, count = connection.execute(_query(item), _parameters(item)), 0
    with pq.ParquetWriter(path, schema, compression="zstd", compression_level=3) as writer:
        while rows := cursor.fetchmany(BATCH_ROWS):
            table = _arrow(rows, item)
            writer.write_table(table)
            count += table.num_rows
    return {**item, "rows": count, "bytes": path.stat().st_size, "sha256": digest(path)}


def _snapshot(source: Path, stage: Path) -> dict:
    with sqlite3.connect(source.resolve(strict=True).as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        objects = connection.execute(
            "SELECT type,sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()
        if any(kind not in {"table", "index"} for kind, _ in objects):
            raise ValueError("Unsupported board-flow source objects")
        tables, indexes = (
            [sql for kind, sql in objects if kind == value] for value in ("table", "index")
        )
        info = check_schema(tables, indexes)
        manifest = {
            "format": FORMAT,
            "producer": _producer_metadata(),
            "snapshot_started_utc": datetime.now(UTC).isoformat(),
            "source_file": source.name,
            "tables_sql": tables,
            "indexes_sql": indexes,
            "files": [],
        }
        for name in ("user_version", "application_id"):
            manifest[name] = connection.execute(f"PRAGMA {name}").fetchone()[0]
        for name in sorted(TABLES):
            dates = (
                [
                    row[0]
                    for row in connection.execute(
                        f'SELECT DISTINCT trade_date FROM "{name}" ORDER BY trade_date'
                    )
                ]
                if name in DAILY
                else [None]
            )
            if not dates:
                raise ValueError("V1 archive requires a partition for each daily table")
            for day in dates:
                item = {"table": name, "date": day, **info[name]}
                manifest["files"].append(_export_item(connection, stage, item))
    return manifest


def _producer_metadata() -> dict[str, str]:
    result = {
        "name": "quant-market-data-platform",
        "archive_module_sha256": digest(Path(__file__)),
        "contract_module_sha256": digest(Path(contract.__file__)),
        "schema_sha256": contract.SCHEMA_SHA256,
    }
    try:
        result["package_version"] = version("quant-market-data-platform")
    except PackageNotFoundError:
        pass
    return result


def export_archive(source: Path, destination: Path) -> dict:
    """Export a consistent read-only SQLite snapshot, then publish a verified new archive."""
    source, destination = Path(source), Path(destination).absolute()
    _new_target(destination)
    stage = Path(tempfile.mkdtemp(prefix=".board-flow-archive-", dir=destination.parent))
    try:
        try:
            manifest = _snapshot(source, stage)
        except sqlite3.Error as error:
            raise ValueError("Cannot read the supported board-flow SQLite snapshot") from error
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        snapshot = capture_manifest(stage)
        with tempfile.TemporaryDirectory(
            prefix=".board-flow-check-", dir=destination.parent
        ) as check:
            _restore_checked(stage, snapshot, Path(check) / "restored.sqlite3")
        check_snapshot(stage, snapshot)
        _publish_new(stage, destination)
        return {**_summary(snapshot), "destination": str(destination)}
    finally:
        if stage.exists():
            shutil.rmtree(stage)
