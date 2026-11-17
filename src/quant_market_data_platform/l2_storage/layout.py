"""Versioned routing, lexical restoration and guarded file paths."""

from __future__ import annotations

import hashlib
import json
import re
import zlib
from pathlib import Path
from typing import Any, cast

import numpy as np
import pyarrow as pa
import pyarrow.compute as arrow_compute

from .profile import clock

pc = cast(Any, arrow_compute)
VERSION = "l2.storage.pilot.v2"


def bucket(symbol: str, count: int) -> int:
    return zlib.crc32(symbol.encode("utf-8")) % count


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def schema(info):
    return pa.schema([(n, pa.string()) for n in info["columns"]])


def typed(table: pa.Table, info, source_symbol: str) -> pa.Table:
    result = table
    for name in info["integer_columns"]:
        i = result.schema.get_field_index(name)
        result = result.set_column(i, name, pc.cast(result[name], pa.int64(), safe=True))
    result = result.append_column("_time", clock(table))
    result = result.append_column(
        "_source_row", pa.array(np.arange(table.num_rows), type=pa.int64())
    )
    return result.append_column(
        "_source_symbol", pa.repeat(pa.scalar(source_symbol), table.num_rows)
    )


def lexical(table: pa.Table, info) -> pa.Table:
    return table.sort_by([("_source_row", "ascending")]).select(info["columns"]).cast(schema(info))


def file_path(kind: str, number: int) -> str:
    return f"data/{kind}/bucket-{number:04d}.parquet"


def safe_file(root: Path, relative: str) -> Path:
    if not re.fullmatch(r"data/(orders|trades|snapshots)/bucket-\d{4}\.parquet", relative):
        raise ValueError("Invalid output catalog path")
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Output path escapes dataset root")
    return path


def writer_options(table: pa.Table, config):
    encodings = {}
    for field in table.schema:
        if pa.types.is_integer(field.type):
            encodings[field.name] = "DELTA_BINARY_PACKED"
        elif pa.types.is_string(field.type):
            encodings[field.name] = "DELTA_BYTE_ARRAY"
    return {
        "compression": "zstd",
        "compression_level": config["zstd_level"],
        "use_dictionary": False,
        "column_encoding": encodings,
        "write_statistics": True,
        "write_page_checksum": True,
    }
