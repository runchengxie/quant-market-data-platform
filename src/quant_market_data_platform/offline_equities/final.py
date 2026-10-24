from __future__ import annotations

import json
import pathlib
import urllib.parse
from typing import Any, cast

import pyarrow as pa
import pyarrow.compute as arrow_compute
import pyarrow.parquet as pq

from .identity import bucket_for, security_token, symbol_folder
from .integrity import Sink, fingerprint, load, save, volume
from .progress import recover, verify_part

# Arrow generates compute kernel attributes dynamically at import time.
pc = cast(Any, arrow_compute)
P = pathlib.Path


def prepare_bucket(b, args, config):
    out = P(args.output)
    work = out / "work" / f"final-{b:03}"
    recover(work, out, f"final-work-{b}")
    work.mkdir(parents=True)
    root = out / "parquet"
    if root.exists():
        for d in root.iterdir():
            if not d.is_dir() or not d.name.startswith("symbol="):
                raise ValueError("Unexpected published directory")
            symbol = urllib.parse.unquote(d.name[7:])
            if bucket_for(symbol, args.buckets) == b:
                owner = d / "_layout_owner.json"
                if not (
                    owner.exists()
                    and load(owner)
                    == {
                        "script_sha256": config["script_sha256"],
                        "source_manifest_sha256": config["source_manifest_sha256"],
                        "bucket": b,
                    }
                ):
                    raise ValueError("Unknown directory ownership: " + str(d))
                recover(d, out, f"final-publish-{b}-{d.name}")
    return work


def append_combos(t, buffers, work, args, config):
    sinks, details = buffers
    year = pc.utf8_slice_codeunits(t["Date"], start=0, stop=4)
    combos = pc.binary_join_element_wise(
        t["Ticker"], t["ResearchKey"], t["AuditSettlementBasis"], year, pa.scalar("\x1f")
    )
    for combo in pc.unique(combos).to_pylist():
        sym, key, basis, yr = combo.split("\x1f")
        token = security_token(sym, key, basis)
        if combo not in sinks:
            path = work / symbol_folder(sym) / f"security={token}" / f"year={yr}" / "data.parquet"
            for d in details.values():
                if d["token"] == token:
                    if not (d["symbol"], d["research_key"], d["settlement_basis"]) == (
                        sym,
                        key,
                        basis,
                    ):
                        raise ValueError("Partition token collision")
            symbol_dir = path.parents[2]
            symbol_dir.mkdir(parents=True, exist_ok=True)
            save(
                symbol_dir / "_layout_owner.json",
                {
                    "script_sha256": config["script_sha256"],
                    "source_manifest_sha256": config["source_manifest_sha256"],
                    "bucket": bucket_for(sym, args.buckets),
                },
            )
            sinks[combo] = Sink(path, t.schema, args.final_rows, 6, ordered=True)
            details[combo] = {
                "symbol": sym,
                "research_key": key,
                "settlement_basis": basis,
                "year": int(yr),
                "token": token,
            }
        indexes = pc.indices_nonzero(pc.equal(combos, combo))
        sinks[combo].append(t.take(indexes))
    while sum(s.buffered for s in sinks.values()) > 1000000:
        max(sinks.values(), key=lambda s: s.buffered).flush()


def route_bucket(b, args, job, stages, context):
    work, config = context
    out = P(args.output)
    sinks = {}
    details = {}
    rows = vol = 0
    for stage in stages:
        for info in stage["parts"]:
            if info["bucket"] != b:
                continue
            pf = pq.ParquetFile(out / info["file"], page_checksum_verification=True)
            if not pf.num_row_groups == len(info["groups"]):
                raise ValueError("Integrity check failed")
            for i, proof in enumerate(info["groups"]):
                t = pf.read_row_group(i)
                if not (fingerprint(t) == proof["sha256"] and t.num_rows == proof["rows"]):
                    raise ValueError("Integrity check failed")
                rows += t.num_rows
                vol += volume(t)
                append_combos(t, (sinks, details), work, args, config)
                job.update(active_bucket_rows=rows)
            pf.close()
    for sink in sinks.values():
        sink.close()
    return (sinks, details, rows, vol)


def publish_bucket(b, args, job, work, buffers):
    sinks, details, rows, vol = buffers
    out = P(args.output)
    root = out / "parquet"
    cp = out / "checkpoints/final" / f"{b:03}.json"
    parts = []
    for combo, sink in sinks.items():
        job.update(phase="security_readback", active_symbol=details[combo]["symbol"])
        info = sink.verify()
        relative = sink.path.relative_to(work)
        info.update(details[combo])
        info["file"] = str(P("parquet") / relative)
        parts.append(info)
    if not (sum(p["rows"] for p in parts) == rows and sum(p["volume"] for p in parts) == vol):
        raise ValueError("Integrity check failed")
    root.mkdir(exist_ok=True)
    for d in sorted(work.iterdir()):
        dest = root / d.name
        if dest.exists():
            raise ValueError("Integrity check failed")
        d.rename(dest)
    result = {"bucket": b, "rows": rows, "volume": vol, "parts": parts, "status": "verified"}
    save(cp, result)
    return result


def build_final(args, job, config, stages):
    out = P(args.output)
    final_results = []
    buckets_present = sorted({p["bucket"] for s in stages for p in s["parts"]})
    for b in buckets_present:
        cp = out / "checkpoints/final" / f"{b:03}.json"
        if cp.exists():
            result = load(cp)
            for info in result["parts"]:
                verify_part(out / info["file"], info)
        else:
            work = prepare_bucket(b, args, config)
            job.update(
                phase="by_security",
                active_bucket=b,
                completed_buckets=len(final_results),
                total_buckets=len(buckets_present),
                final_verified_rows=sum(r["rows"] for r in final_results),
                active_bucket_rows=0,
            )
            sinks, details, rows, vol = route_bucket(b, args, job, stages, (work, config))
            result = publish_bucket(b, args, job, work, (sinks, details, rows, vol))
            del sinks
            pa.default_memory_pool().release_unused()
        final_results.append(result)
        print(
            "BUCKET_DONE "
            + json.dumps(
                {
                    "bucket": b,
                    "rows": result["rows"],
                    "final_verified_rows": sum(r["rows"] for r in final_results),
                }
            ),
            flush=True,
        )
    return final_results
