from __future__ import annotations

import hashlib
import json
import pathlib
import struct
import time
import traceback
from typing import Any, cast

import pyarrow as pa
import pyarrow.compute as arrow_compute
import pyarrow.parquet as pq

# Arrow generates compute kernel attributes dynamically at import time.
pc = cast(Any, arrow_compute)
P = pathlib.Path


def sha_file(p):
    h = hashlib.sha256()
    with P(p).open("rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""):
            h.update(b)
    return h.hexdigest()


def save(p, obj):
    p = P(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for i in range(20):
        try:
            tmp.replace(p)
            return
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.1)


def load(p):
    return json.loads(P(p).read_text(encoding="utf-8-sig"))


def fingerprint(table):
    """Canonical SHA256 of all logical values, types, validity and row order."""
    h = hashlib.sha256()
    h.update(struct.pack("<QQ", table.num_rows, table.num_columns))
    for field, column in zip(table.schema, table.columns, strict=True):
        a = column.combine_chunks()
        label = (field.name + "\x00" + str(a.type)).encode()
        h.update(struct.pack("<I", len(label)))
        h.update(label)
        h.update(struct.pack("<Q", a.null_count))
        if a.null_count:
            valid = pc.cast(pc.is_valid(a), pa.uint8())
            h.update(memoryview(valid.buffers()[1])[valid.offset : valid.offset + len(valid)])
            zero = (
                ""
                if pa.types.is_string(a.type) or pa.types.is_large_string(a.type)
                else False
                if pa.types.is_boolean(a.type)
                else 0
            )
            a = pc.fill_null(a, pa.scalar(zero, type=a.type))
        if pa.types.is_string(a.type) or pa.types.is_large_string(a.type):
            wide = pa.types.is_large_string(a.type)
            width = 8 if wide else 4
            fmt = "<q" if wide else "<i"
            lengths = pc.binary_length(a)
            h.update(
                memoryview(lengths.buffers()[1])[
                    lengths.offset * width : (lengths.offset + len(lengths)) * width
                ]
            )
            offsets = a.buffers()[1]
            begin = struct.unpack_from(fmt, offsets, a.offset * width)[0]
            end = struct.unpack_from(fmt, offsets, (a.offset + len(a)) * width)[0]
            if end > begin:
                h.update(memoryview(a.buffers()[2])[begin:end])
        else:
            if pa.types.is_boolean(a.type):
                a = pc.cast(a, pa.uint8())
            width = a.type.bit_width // 8
            if len(a):
                h.update(memoryview(a.buffers()[1])[a.offset * width : (a.offset + len(a)) * width])
    return h.hexdigest()


def check_order(table, previous):
    keys = pc.binary_join_element_wise(
        table["Date"], table["TimeBarStart"], pa.scalar("|")
    ).combine_chunks()
    if not len(keys):
        return previous
    first = keys[0].as_py()
    if previous is not None and first <= previous:
        raise ValueError(f"Non-increasing minute boundary: {previous} -> {first}")
    if len(keys) > 1 and pc.any(pc.less_equal(keys.slice(1), keys.slice(0, len(keys) - 1))).as_py():
        raise ValueError("Duplicate or out-of-order minute")
    return keys[-1].as_py()


def volume(t):
    return int(pc.sum(t["AuditTotalVolume"]).as_py() or 0)


class Sink:
    def __init__(self, path, schema, target, level, ordered=False):
        self.path = P(path)
        self.schema = schema
        self.target = target
        self.level = level
        self.ordered = ordered
        self.buffer = []
        self.buffered = 0
        self.writer = None
        self.proofs = []
        self.rows = 0
        self.volume = 0
        self.last = None

    def append(self, t):
        while t.num_rows:
            take = min(t.num_rows, self.target - self.buffered)
            self.buffer.append(t.slice(0, take))
            self.buffered += take
            t = t.slice(take)
            if self.buffered == self.target:
                self.flush()

    def flush(self):
        if not self.buffered:
            return
        t = pa.concat_tables(self.buffer)
        digest = fingerprint(t)
        if self.ordered:
            self.last = check_order(t, self.last)
        if self.writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.writer = pq.ParquetWriter(
                self.path,
                self.schema,
                compression="zstd",
                compression_level=self.level,
                use_dictionary=True,
                write_statistics=True,
                write_page_checksum=True,
            )
        self.writer.write_table(t, row_group_size=t.num_rows)
        self.proofs.append({"rows": t.num_rows, "sha256": digest})
        self.rows += t.num_rows
        self.volume += volume(t)
        self.buffer = []
        self.buffered = 0

    def close(self):
        error = None
        try:
            self.flush()
        except BaseException as exc:
            error = exc
            raise
        finally:
            if self.writer:
                try:
                    self.writer.close()
                except BaseException as cleanup_error:
                    if error is None:
                        raise
                    detail = "".join(traceback.format_exception_only(cleanup_error)).strip()
                    error.add_note(f"Parquet writer cleanup failed: {detail}")

    def verify(self):
        with pq.ParquetFile(self.path, page_checksum_verification=True) as pf:
            if not pf.schema_arrow.equals(self.schema, check_metadata=False):
                raise ValueError("Integrity check failed")
            if not pf.num_row_groups == len(self.proofs):
                raise ValueError("Integrity check failed")
            last = None
            for i, proof in enumerate(self.proofs):
                t = pf.read_row_group(i)
                if not (t.num_rows == proof["rows"] and fingerprint(t) == proof["sha256"]):
                    raise ValueError(str(self.path))
                if self.ordered:
                    last = check_order(t, last)
                if not all(
                    pf.metadata.row_group(i).column(j).compression == "ZSTD"
                    for j in range(pf.metadata.num_columns)
                ):
                    raise ValueError("Integrity check failed")
        return {
            "rows": self.rows,
            "volume": self.volume,
            "bytes": self.path.stat().st_size,
            "sha256": sha_file(self.path),
            "groups": self.proofs,
            "strict_time_order_verified": self.ordered,
        }
