"""Canonical lexical proofs and atomic evidence files."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import pyarrow as pa


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def logical_hash(table: pa.Table) -> str:
    """Hash all lexical fields in record order with deterministic batch boundaries."""
    table = table.combine_chunks().replace_schema_metadata(None)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table, max_chunksize=65536)
    return hashlib.sha256(sink.getvalue()).hexdigest()


def save(path: Path, value) -> None:
    if path.is_symlink() or path.with_name(path.name + ".tmp").is_symlink():
        raise ValueError("Refusing linked evidence or temporary paths")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load(path: Path):
    if path.is_symlink():
        raise ValueError("Refusing linked evidence input")
    return json.loads(path.read_text(encoding="utf-8"))
