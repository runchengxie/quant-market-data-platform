"""Whole-modality lexical profiles; conversions must round-trip every value."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, cast

import numpy as np
import pyarrow as pa
import pyarrow.compute as arrow_compute
import pyarrow.csv as pacsv

from .archive import MODALITIES, SYMBOL, member_path
from .proof import logical_hash, sha_file

pc = cast(Any, arrow_compute)  # Arrow generates compute kernels dynamically.

MAX_RAW_BYTES = 512 << 20
MAX_DECODED_BYTES = 1 << 30
RESERVED = {"_source_row", "_time", "_source_symbol"}
IDENTITY_POLICIES = {"strict", "preserve-source-labels"}


def read_csv(path: Path) -> pa.Table:
    if path.stat().st_size > MAX_RAW_BYTES:
        raise ValueError(f"CSV exceeds 512 MiB raw-file guard: {path}")
    with path.open(encoding="gb18030", newline="") as handle:
        names = next(csv.reader(handle), [])
    if not names or len(set(names)) != len(names) or any(not n for n in names):
        raise ValueError(f"Missing or duplicate CSV headers: {path}")
    if RESERVED.intersection(names):
        raise ValueError("Provider columns collide with reserved provenance names")
    table = pacsv.read_csv(
        path,
        read_options=pacsv.ReadOptions(encoding="gb18030"),
        convert_options=pacsv.ConvertOptions(
            column_types={n: pa.string() for n in names}, strings_can_be_null=False, null_values=[]
        ),
    )
    if table.nbytes > MAX_DECODED_BYTES:
        raise ValueError("CSV exceeds 1 GiB decoded-table guard")
    return table


def integers(table: pa.Table) -> set[str]:
    eligible = set()
    for name in table.column_names:
        if name in {"万得代码", "交易所代码"}:
            continue
        try:
            values = pc.cast(table[name], pa.int64(), safe=True)
            if pc.cast(values, pa.string()).equals(table[name]):
                eligible.add(name)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            continue
    return eligible


def clock(table: pa.Table):
    values = pc.cast(table["时间"], pa.int64(), safe=True)
    numbers = values.to_numpy()
    if np.any(
        (numbers < 0)
        | (numbers // 10000000 > 23)
        | ((numbers // 100000) % 100 > 59)
        | ((numbers // 1000) % 100 > 59)
    ):
        raise ValueError("Unsupported HHMMSSmmm clock value")
    return values


def classify_symbol(source_symbol, record_symbol, policy):
    if policy not in IDENTITY_POLICIES:
        raise ValueError("Unknown identity policy")
    if record_symbol is None:
        return "empty"
    if not isinstance(record_symbol, str) or not SYMBOL.fullmatch(record_symbol):
        raise ValueError("Invalid record security identifier")
    if record_symbol == source_symbol:
        return "matched"
    if policy == "preserve-source-labels" and record_symbol[:6] == source_symbol[:6]:
        return "source_label_conflict"
    raise ValueError("Provider security differs from source member identity")


def record_identity(table, source_symbol, policy):
    if "万得代码" not in table.column_names:
        raise ValueError("Missing provider security column")
    values = pc.unique(table["万得代码"]).to_pylist()
    if len(values) > 1:
        raise ValueError("Multiple record identifiers in one source member")
    record_symbol = values[0] if values else None
    return {
        "record_symbol": record_symbol,
        "identity_status": classify_symbol(source_symbol, record_symbol, policy),
    }


def identity_summary(profile):
    policy = profile["identity_policy"]
    if policy not in IDENTITY_POLICIES:
        raise ValueError("Unknown identity policy")
    conflicts = 0
    for member in profile["members"]:
        status = classify_symbol(member["symbol"], member["record_symbol"], policy)
        if status != member["identity_status"] or (member["rows"] == 0) != (
            member["record_symbol"] is None
        ):
            raise ValueError("Inconsistent source/record identity evidence")
        conflicts += status == "source_label_conflict"
    return {"policy": policy, "conflicting_members": conflicts, "canonical_mapping": "not_inferred"}


def inspect_member(root: Path, path: Path, date: str, identity_policy):
    relative = path.relative_to(root).as_posix()
    parts = member_path(relative, date).parts
    before = path.stat()
    table = read_csv(path)
    identity = record_identity(table, parts[1], identity_policy)
    for field, value in [("自然日", date)]:
        if field not in table.column_names or (
            table.num_rows and not pc.all(pc.equal(table[field], value)).as_py()
        ):
            raise ValueError(f"Provider {field} differs from member identity: {relative}")
    clock(table)
    proof = {
        "path": relative,
        "symbol": parts[1],
        **identity,
        "modality": MODALITIES[parts[2]],
        "rows": table.num_rows,
        "bytes": before.st_size,
        "sha256": sha_file(path),
        "logical_sha256": logical_hash(table),
    }
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("Source CSV changed during profiling")
    return proof, table


def profile_directory(root: Path, date: str, identity_policy="strict") -> dict:
    if identity_policy not in IDENTITY_POLICIES:
        raise ValueError("Unknown identity policy")
    members, modalities = [], {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Source symlinks are not supported")
        if not path.is_file():
            continue
        proof, table = inspect_member(root, path, date, identity_policy)
        kind = proof["modality"]
        columns = table.column_names
        current = modalities.setdefault(kind, {"columns": columns, "integer_columns": set(columns)})
        if current["columns"] != columns:
            raise ValueError(f"Inconsistent headers in modality: {kind}")
        current["integer_columns"] &= integers(table)
        members.append(proof)
    if not members:
        raise ValueError("No CSV source members")
    for value in modalities.values():
        value["integer_columns"] = sorted(value["integer_columns"])
    return {
        "date": date,
        "source_root": str(root),
        "modalities": modalities,
        "members": members,
        "identity_policy": identity_policy,
    }
