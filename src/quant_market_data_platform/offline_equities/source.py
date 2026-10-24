from __future__ import annotations

import hashlib
import pathlib
import re
from typing import Any

import duckdb
import pyarrow as pa

from .integrity import load, sha_file

P = pathlib.Path


def source_plan(args):
    source = P(args.source)
    summary = load(source / "summary.json")
    if not summary["status"] == "complete":
        raise ValueError("Integrity check failed")
    rows = manifest_rows(args, source, summary)
    for r in rows:
        if not re.fullmatch(r"\d{8}", r["date"]):
            raise ValueError("Expected eight-digit trading date")
        p = (source / r["file"]).resolve()
        if not p.is_relative_to((source / "parquet").resolve()):
            raise ValueError("Source part outside audited parquet root")
        st = p.stat()
        if not st.st_size == int(r["bytes"]):
            raise ValueError("Integrity check failed")
        r["mtime_ns"] = st.st_mtime_ns
        r["rows"] = int(r["rows"])
        r["bytes"] = int(r["bytes"])
    expected = selection_count(args, source) if args.symbols else sum(r["rows"] for r in rows)
    return (summary, rows, expected)


def implementation_digest():
    h = hashlib.sha256()
    for p in sorted(P(__file__).parent.glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def make_config(args, source, inputs, expected):
    return {
        "version": "security-year-lossless-v2",
        "script_sha256": implementation_digest(),
        "source": str(source),
        "source_manifest_sha256": sha_file(source / "parquet_manifest.csv"),
        "source_summary_sha256": sha_file(source / "summary.json"),
        "source_profiles_sha256": sha_file(source / "file_quality.parquet"),
        "dates": args.dates,
        "symbols": args.symbols,
        "buckets": args.buckets,
        "stage_row_group": args.stage_rows,
        "final_row_group": args.final_rows,
        "zstd_level": 6,
        "stage_zstd_level": 1,
        "max_buffered_rows": 1000000,
        "expected_rows": expected,
        "inputs": inputs,
        "pyarrow": pa.__version__,
        "duckdb": duckdb.__version__,
        "identity_rule": (
            "original candidate key else unresolved:ticker; WOLF split at20250929; "
            "folder also separates ticker and settlement basis"
        ),
        "identity_evidence_sha256": {
            str(p): sha_file(p)
            for p in [
                P(args.identity_root) / "wolf_identity_rule.json",
                P(args.identity_root) / "reference_inputs/security_identity_intervals.csv",
            ]
        },
        "research_scope": (
            "All physical raw/audit columns preserved; only ResearchKey added. "
            "No adjustment/filter/fill in full run. Unknown identities remain unresolved."
        ),
    }


def manifest_rows(args, source, summary):
    import csv

    rows: list[dict[str, Any]] = list(
        csv.DictReader((source / "parquet_manifest.csv").open(encoding="utf-8-sig"))
    )
    if int(summary.get("rows", -1)) != sum(int(row["rows"]) for row in rows):
        raise ValueError("Completed source summary differs from manifest")
    if "parquet_parts" in summary and int(summary["parquet_parts"]) != len(rows):
        raise ValueError("Completed source summary differs from manifest")
    missing = set(args.dates or ()) - {row["date"] for row in rows}
    if missing:
        raise ValueError("Requested dates absent from manifest: " + ", ".join(sorted(missing)))
    rows = [r for r in rows if not args.dates or r["date"] in args.dates]
    rows.sort(key=lambda r: (r["date"], r["lane"]))
    if not rows:
        raise ValueError("Empty manifest selection")
    return rows


def selection_count(args, source):
    with duckdb.connect() as c:
        c.read_parquet(str(source / "file_quality.parquet")).create_view("q")
        clauses = ["symbol IN (" + ",".join("?" for _ in args.symbols) + ")"]
        params = list(args.symbols)
        if args.dates:
            clauses.append("date IN (" + ",".join("?" for _ in args.dates) + ")")
            params.extend(args.dates)
        observed = {
            r[0]
            for r in c.execute(
                "SELECT DISTINCT symbol FROM q WHERE " + " AND ".join(clauses), params
            ).fetchall()
        }
        missing = set(args.symbols) - observed
        if missing:
            raise ValueError(
                "Requested symbols absent from source profiles: " + ", ".join(sorted(missing))
            )
        result = c.execute(
            "SELECT sum(TRY_CAST(rows AS BIGINT)) FROM q WHERE " + " AND ".join(clauses), params
        ).fetchone()
        if result is None or result[0] is None:
            raise ValueError("Selection contains no observed rows")
        return int(result[0])
