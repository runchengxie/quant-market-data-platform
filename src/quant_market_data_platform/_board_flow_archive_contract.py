"""Fixed, data-only v1 archive contract; manifest SQL is never executed."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from quant_market_data_platform.board_flow_store import _SCHEMA

FORMAT = "board-flow-parquet-v1"
SCHEMA_SHA256 = "b0394330e3bb2b012f201588434cbcbf8e63ab1bbdea9494c3f7cb3090dac075"
DAILY = frozenset({"board_catalog_daily", "flow_snapshot", "flow_minute"})
TABLES = frozenset({*DAILY, "board_catalog", "collection_run", "schema_meta"})
TYPES = {"TEXT": pa.string(), "INTEGER": pa.int64(), "REAL": pa.float64()}
BATCH_ROWS = 32_768


@dataclass(frozen=True)
class ManifestSnapshot:
    data: dict
    content: bytes
    identity: tuple[int, int, int, int]
    sha256: str


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical() -> tuple[dict, list[str], list[str]]:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(_SCHEMA)
        objects = connection.execute(
            "SELECT type, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()
        info = {}
        for name in sorted(TABLES):
            columns = connection.execute(f'PRAGMA table_info("{name}")').fetchall()
            info[name] = {
                "columns": [row[1] for row in columns],
                "types": [row[2] for row in columns],
                "key": [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]],
            }
    tables = [s for t, s in objects if t == "table"]
    indexes = [s for t, s in objects if t == "index"]
    identity = {
        "tables": sorted(_sql_set(tables)),
        "indexes": sorted(_sql_set(indexes)),
        "columns": info,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != SCHEMA_SHA256:
        raise ValueError("Current store schema is unsupported by archive v1")
    return info, tables, indexes


def _sql_set(values: Any) -> set[str]:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError("Invalid archive schema description")
    result = {re.sub(r"\s+", "", value).lower().rstrip(";") for value in values}
    if len(result) != len(values):
        raise ValueError("Duplicate archive schema description")
    return result


def check_schema(tables: Any, indexes: Any) -> dict:
    info, expected_tables, expected_indexes = canonical()
    if _sql_set(tables) != _sql_set(expected_tables) or _sql_set(indexes) != _sql_set(
        expected_indexes
    ):
        raise ValueError("Archive requires the exact supported board-flow schema")
    return info


def file_path(root: Path, item: dict) -> Path:
    name, day = item.get("table"), item.get("date")
    if not isinstance(name, str) or name not in TABLES or (name in DAILY) != (day is not None):
        raise ValueError("Unexpected archive table or partition")
    if day is not None:
        if not isinstance(day, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) is None:
            raise ValueError("Invalid archive partition date")
        date.fromisoformat(day)
    path = root / name / f"{day or 'all'}.parquet"
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Archive data paths must not be symlinks")
    path.resolve().relative_to(root.resolve())
    return path


def load_manifest(root: Path) -> dict:
    return capture_manifest(root).data


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_mtime_ns, value.st_size


def _manifest_bytes(root: Path) -> tuple[bytes, tuple[int, int, int, int]]:
    path = root / "manifest.json"
    if path.is_symlink():
        raise ValueError("Archive manifest must not be a symlink")
    identity = _file_identity(path)
    content = path.read_bytes()
    if path.is_symlink() or identity != _file_identity(path):
        raise ValueError("Archive manifest changed while being read")
    return content, identity


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key in archive manifest")
        result[key] = value
    return result


def capture_manifest(root: Path) -> ManifestSnapshot:
    content, identity = _manifest_bytes(root)
    manifest = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_pairs)
    _validate_manifest(root, manifest)
    return ManifestSnapshot(manifest, content, identity, hashlib.sha256(content).hexdigest())


def check_snapshot(root: Path, snapshot: ManifestSnapshot) -> None:
    content, identity = _manifest_bytes(root)
    if content != snapshot.content or identity != snapshot.identity:
        raise ValueError("Archive manifest identity changed")
    info = check_schema(snapshot.data["tables_sql"], snapshot.data["indexes_sql"])
    _check_inventory(root, snapshot.data["files"], info)
    for item in snapshot.data["files"]:
        path = file_path(root, item)
        if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise ValueError("Archive file changed after validation")


def _validate_manifest(root: Path, manifest: Any) -> None:
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("Unknown board-flow archive format")
    info = check_schema(manifest.get("tables_sql"), manifest.get("indexes_sql"))
    for name in ("user_version", "application_id"):
        value = manifest.get(name)
        if type(value) is not int or not 0 <= value <= 2_147_483_647:
            raise ValueError("Invalid SQLite archive metadata")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("Archive has no file inventory")
    _check_inventory(root, files, info)


def _check_inventory(root: Path, files: list, info: dict) -> None:
    identities = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Invalid archive file entry")
        file_path(root, item)
        identity = (item["table"], item["date"])
        if identity in identities:
            raise ValueError("Duplicate archive partition")
        identities.add(identity)
        if any(item.get(key) != value for key, value in info[item["table"]].items()):
            raise ValueError("Archive column, type or key mismatch")
        if any(type(item.get(key)) is not int or item[key] < 0 for key in ("rows", "bytes")):
            raise ValueError("Invalid archive row or byte count")
        if not isinstance(item.get("sha256"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", item["sha256"]
        ):
            raise ValueError("Invalid archive digest")
    if {name for name, _ in identities} != TABLES:
        raise ValueError("Archive requires all six tables")
    expected = {file_path(root, item) for item in files}
    if set(root.rglob("*.parquet")) != expected:
        raise ValueError("Archive contains unlisted Parquet files")


def verified_batches(root: Path, item: dict):
    path = file_path(root, item)
    if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
        raise ValueError("Archive file size or SHA-256 mismatch")
    parquet = pq.ParquetFile(path)
    expected = pa.schema(
        [(name, TYPES[kind]) for name, kind in zip(item["columns"], item["types"], strict=True)]
    )
    if not parquet.schema_arrow.equals(expected) or parquet.metadata.num_rows != item["rows"]:
        raise ValueError("Parquet schema or row count mismatch")
    previous = None
    for batch in parquet.iter_batches(batch_size=BATCH_ROWS):
        values = dict(
            zip(batch.schema.names, (col.to_pylist() for col in batch.columns), strict=True)
        )
        for index in range(batch.num_rows):
            key = tuple(values[name][index] for name in item["key"])
            if any(value is None for value in key) or (previous is not None and key <= previous):
                raise ValueError("Archive primary keys must be non-null and strictly ordered")
            previous = key
            if item["date"] is not None and values["trade_date"][index] != item["date"]:
                raise ValueError("Archive row belongs to another partition")
        yield batch
    if digest(path) != item["sha256"]:
        raise ValueError("Archive changed while being read")
