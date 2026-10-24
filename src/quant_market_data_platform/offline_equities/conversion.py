from __future__ import annotations

import json
import pathlib

from .final import build_final
from .integrity import load, save, sha_file
from .progress import Job
from .source import make_config, source_plan
from .staging import build_stages

P = pathlib.Path


def release(args, job, config, stages, final_results):
    expected = config["expected_rows"]
    source = P(args.source)
    out = P(args.output)
    if not (
        sum(r["rows"] for r in final_results) == expected
        and sum(r["volume"] for r in final_results) == sum(s["volume"] for s in stages)
    ):
        raise ValueError("Integrity check failed")
    parts = [p for r in final_results for p in r["parts"]]
    import csv

    import duckdb

    with (out / "file_index.csv").open("w", encoding="utf-8-sig", newline="") as f:
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
        w = csv.DictWriter(f, fieldnames=names, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(parts, key=lambda p: (p["symbol"], p["research_key"], p["year"])))
    c = duckdb.connect(str(out / "catalog.duckdb"))
    pattern = str(out / "parquet/**/*.parquet").replace("\\", "/")
    c.execute(
        "CREATE OR REPLACE VIEW bars_by_security AS SELECT * FROM read_parquet('"
        + pattern.replace("'", "''")
        + "',hive_partitioning=true)"
    )
    c.close()
    final = {
        "status": "complete",
        "scope": "pilot" if args.symbols or args.dates else "full",
        "rows": expected,
        "volume": sum(r["volume"] for r in final_results),
        "parts": len(parts),
        "symbols": len({p["symbol"] for p in parts}),
        "security_segments": len({(p["symbol"], p["token"]) for p in parts}),
        "parquet_bytes": sum(p["bytes"] for p in parts),
        "all_columns_logical_sha256_readback_both_stages": True,
        "input_file_sha256_verified": True,
        "all_parquet_pages_checksum_verified": True,
        "strict_per_security_year_time_order": True,
        "zstd_level": 6,
        "source_manifest_unchanged": sha_file(source / "parquet_manifest.csv")
        == config["source_manifest_sha256"],
    }
    if not final["source_manifest_unchanged"]:
        raise ValueError("Integrity check failed")
    save(out / "summary.json", final)
    job.finish(
        **final,
        phase="complete",
        completed_months=len(stages),
        completed_buckets=len(final_results),
        staged_verified_rows=expected,
        final_verified_rows=expected,
        active_month_rows=0,
        active_bucket_rows=0,
    )
    print("COMPLETE " + json.dumps(final), flush=True)


def run(args):
    source = P(args.source)
    out = P(args.output)
    out.mkdir(parents=True, exist_ok=True)
    job = Job(out)
    try:
        _summary, inputs, expected = source_plan(args)
        config = make_config(args, source, inputs, expected)
        if (out / "run_config.json").exists():
            if load(out / "run_config.json") != config:
                raise ValueError(
                    "Configuration/input/code changed; "
                    "use a separate output or an audited migration"
                )
        else:
            save(out / "run_config.json", config)
        stages = build_stages(args, job, source, inputs, expected)
        results = build_final(args, job, config, stages)
        release(args, job, config, stages, results)
    except BaseException as exc:
        job.finish(status="failed", error=repr(exc))
        raise
