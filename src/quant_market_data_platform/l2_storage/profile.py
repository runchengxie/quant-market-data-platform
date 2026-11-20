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
    if not names or not names[0] or "" in names[1:-1] or len(set(names)) != len(names):
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


def normalized_symbols(values):
    if any(not isinstance(value, str) or not SYMBOL.fullmatch(value) for value in values):
        raise ValueError("Invalid record security identifier")
    return sorted(set(values))


def classify_symbols(source_symbol, record_symbols, policy):
    if policy not in IDENTITY_POLICIES:
        raise ValueError("Unknown identity policy")
    if not isinstance(record_symbols, list) or record_symbols != normalized_symbols(record_symbols):
        raise ValueError("Invalid record identifier set")
    if not record_symbols:
        return "empty"
    if record_symbols == [source_symbol]:
        return "matched"
    if policy == "preserve-source-labels" and all(
        value[:6] == source_symbol[:6] for value in record_symbols
    ):
        return "source_label_conflict"
    raise ValueError("Provider security differs from source member identity")


def record_identity(table, source_symbol, policy):
    if "万得代码" not in table.column_names:
        raise ValueError("Missing provider security column")
    counts = {
        item["values"]: item["counts"] for item in pc.value_counts(table["万得代码"]).to_pylist()
    }
    labels = normalized_symbols(list(counts))
    return {
        "record_symbols": labels,
        "record_symbol_counts": {label: counts[label] for label in labels},
        "identity_status": classify_symbols(source_symbol, labels, policy),
    }


def validate_record_counts(member):
    counts = member["record_symbol_counts"]
    if sorted(counts) != member["record_symbols"]:
        raise ValueError("Record counts differ from identifier set")
    if any(type(n) is not int or n <= 0 for n in counts.values()):
        raise ValueError("Record identifier counts must be positive integers")
    if (
        type(member["rows"]) is not int
        or member["rows"] < 0
        or sum(counts.values()) != member["rows"]
    ):
        raise ValueError("Record identifier counts differ from member rows")


def identity_summary(profile):
    policy = profile["identity_policy"]
    if policy not in IDENTITY_POLICIES:
        raise ValueError("Unknown identity policy")
    conflicts, mixed, conflicting_rows = 0, 0, 0
    for member in profile["members"]:
        validate_record_counts(member)
        status = classify_symbols(member["symbol"], member["record_symbols"], policy)
        if status != member["identity_status"]:
            raise ValueError("Inconsistent source/record identity evidence")
        conflicts += status == "source_label_conflict"
        mixed += len(member["record_symbols"]) > 1
        conflicting_rows += sum(
            n for code, n in member["record_symbol_counts"].items() if code != member["symbol"]
        )
    return {
        "policy": policy,
        "conflicting_members": conflicts,
        "mixed_record_label_members": mixed,
        "conflicting_rows": conflicting_rows,
        "canonical_mapping": "not_inferred",
    }


def check_date(table, date):
    if "自然日" not in table.column_names or (
        table.num_rows and not pc.all(pc.equal(table["自然日"], date)).as_py()
    ):
        raise ValueError("Provider natural date differs from source member date")


def inspect_member(root: Path, path: Path, date: str, identity_policy):
    relative = path.relative_to(root).as_posix()
    parts = member_path(relative, date).parts
    before = path.stat()
    table = read_csv(path)
    identity = record_identity(table, parts[1], identity_policy)
    check_date(table, date)
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
        try:
            proof, table = inspect_member(root, path, date, identity_policy)
        except (ValueError, KeyError) as exc:
            relative = path.relative_to(root).as_posix()
            raise ValueError(f"Source member {relative}: {exc}") from exc
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
