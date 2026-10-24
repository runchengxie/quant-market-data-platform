"""Ownership, immutable plans and atomic publication for offline repacking."""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import re
from typing import Any

import pyarrow as pa

from .identity import symbol_folder
from .integrity import load, save, sha_file

P = pathlib.Path
VERSION = "security-year-delta-zstd12-v1"


def relative_file(root, name):
    relative = pathlib.PurePosixPath(name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or ":" in name:
        raise ValueError("Unsafe manifest path")
    result = root.joinpath(*relative.parts).resolve()
    if not result.is_relative_to(root / "parquet"):
        raise ValueError("Manifest file must be under parquet")
    return result


def destination(row):
    if not re.fullmatch(r"[0-9a-f]+", row["token"]) or not re.fullmatch(r"\d{4}", row["year"]):
        raise ValueError("Invalid security/year partition")
    return (
        P("parquet")
        / symbol_folder(row["symbol"])
        / ("security=" + row["token"])
        / ("year=" + row["year"])
        / "data.parquet"
    )


def plan(source):
    if load(source / "summary.json")["status"] != "complete":
        raise ValueError("Source must be complete")
    with (source / "file_index.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows: list[dict[str, Any]] = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("Empty source index")
    targets = set()
    inputs = set()
    for row in rows:
        path = relative_file(source, row["file"])
        if path in inputs:
            raise ValueError("Duplicate input file")
        inputs.add(path)
        target = destination(row).as_posix().casefold()
        if target in targets:
            raise ValueError("Duplicate output partition")
        targets.add(target)
        for name in ("rows", "volume", "bytes"):
            row[name] = int(row[name])
    if inputs != {p.resolve() for p in (source / "parquet").rglob("*.parquet")}:
        raise ValueError("Source index/file inventory differ")
    if sum(row["rows"] for row in rows) != load(source / "summary.json")["rows"]:
        raise ValueError("Source summary/index rows differ")
    return rows


def configuration(source):
    modules = sorted(P(__file__).parent.glob("*.py"))
    return {
        "version": VERSION,
        "source": str(source),
        "source_index_sha256": sha_file(source / "file_index.csv"),
        "source_summary_sha256": sha_file(source / "summary.json"),
        "implementation_sha256": hashlib.sha256(
            "".join(p.name + sha_file(p) for p in modules).encode()
        ).hexdigest(),
        "pyarrow": pa.__version__,
        "zstd_level": 12,
        "row_group_rows": 262144,
        "max_input_uncompressed_bytes": 2 << 30,
        "max_decoded_bytes": 1 << 30,
        "available_memory_fraction": "1/6",
        "encoding": "delta-strings-integers-byte-stream-split-floats",
    }


def own_output(source, output):
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("Source/output must be disjoint")
    if output.exists() and any(output.iterdir()):
        marker = output / "repack_owner.json"
        if not marker.is_file() or load(marker) != {"version": VERSION, "source": str(source)}:
            raise ValueError("Output is not owned by this repacker")
        return
    output.mkdir(parents=True, exist_ok=True)
    save(output / "repack_owner.json", {"version": VERSION, "source": str(source)})


def write_index(output, rows):
    names = [
        "symbol",
        "research_key",
        "settlement_basis",
        "year",
        "token",
        "file",
        "rows",
        "volume",
        "bytes",
        "sha256",
    ]
    temp = output / "file_index.csv.tmp"
    with temp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(output / "file_index.csv")


def config_digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
