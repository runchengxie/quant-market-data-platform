from __future__ import annotations

import collections
import pathlib
import shutil
from typing import Any, cast

import pyarrow as pa
import pyarrow.compute as arrow_compute
import pyarrow.parquet as pq

from .identity import add_identity, bucket_for
from .integrity import Sink, load, save, sha_file, volume
from .progress import recover, verify_part

# Arrow generates compute kernel attributes dynamically at import time.
pc = cast(Any, arrow_compute)
P = pathlib.Path


def append_input(item, args, job, sinks, work):
    source = P(args.source)
    month_rows = 0
    month_volume = 0
    checked = []
    p = source / item["file"]
    if not p.stat().st_mtime_ns == item["mtime_ns"]:
        raise ValueError("Integrity check failed")
    if not sha_file(p) == item["sha256"]:
        raise ValueError("Input hash differs: " + str(p))
    pf = pq.ParquetFile(p, page_checksum_verification=True)
    if not pf.metadata.num_rows == item["rows"]:
        raise ValueError("Integrity check failed")
    selected = 0
    for rg in range(pf.num_row_groups):
        mask = None
        if args.symbols:
            ticks = pf.read_row_group(rg, columns=["Ticker"])["Ticker"]
            mask = pc.is_in(ticks, value_set=pa.array(args.symbols))
            if not pc.any(mask).as_py():
                continue
        t = pf.read_row_group(rg)
        if mask is not None:
            t = t.filter(mask)
        if not pc.all(pc.equal(t["Date"], item["date"])).as_py():
            raise ValueError("Integrity check failed")
        t = add_identity(t)
        selected += t.num_rows
        month_rows += t.num_rows
        month_volume += volume(t)
        unique = pc.unique(t["Ticker"])
        which = pc.index_in(t["Ticker"], value_set=unique)
        lookup = pa.array(
            [bucket_for(x, args.buckets) for x in unique.to_pylist()], type=pa.int32()
        )
        buckets = pc.take(lookup, which)
        for b in pc.unique(buckets).to_pylist():
            indexes = pc.indices_nonzero(pc.equal(buckets, b))
            piece = t.take(indexes)
            if b not in sinks:
                sinks[b] = Sink(work / f"bucket-{b:03}.parquet", t.schema, args.stage_rows, 1)
            sinks[b].append(piece)
        job.update(active_source=item["file"], active_month_rows=month_rows)
    pf.close()
    checked.append({"file": item["file"], "sha256": item["sha256"], "selected_rows": selected})
    return (month_rows, month_volume, checked[0])


def build_month(month, parts, args, job, totals):
    completed, completed_rows, total_months, expected = totals
    if shutil.disk_usage(P(args.output)).free < 20 * (1 << 30):
        raise OSError("Less than 20 GiB free")
    out = P(args.output)
    finaldir = out / "staging" / f"month={month}"
    work = out / "work" / f"stage-{month}"
    cp = out / "checkpoints/stage" / f"{month}.json"
    recover(work, out, "stage-work-" + month)
    recover(finaldir, out, "stage-publish-" + month)
    work.mkdir(parents=True)
    sinks = {}
    month_rows = 0
    month_volume = 0
    checked = []
    job.update(
        phase="staging",
        active_month=month,
        completed_months=completed,
        total_months=total_months,
        staged_verified_rows=completed_rows,
        active_month_rows=0,
        expected_rows=expected,
    )
    for item in parts:
        count, amount, proof = append_input(item, args, job, sinks, work)
        month_rows += count
        month_volume += amount
        checked.append(proof)
    for sink in sinks.values():
        sink.close()
    verified = []
    for b, sink in sorted(sinks.items()):
        job.update(phase="stage_readback", active_bucket=b)
        info = sink.verify()
        info.update(bucket=b, file=str((finaldir / sink.path.name).relative_to(out)))
        verified.append(info)
    if not (
        sum(p["rows"] for p in verified) == month_rows
        and sum(p["volume"] for p in verified) == month_volume
    ):
        raise ValueError("Integrity check failed")
    finaldir.parent.mkdir(exist_ok=True)
    work.rename(finaldir)
    result = {
        "month": month,
        "rows": month_rows,
        "volume": month_volume,
        "inputs": checked,
        "parts": verified,
        "status": "verified",
    }
    save(cp, result)
    return result


def build_stages(args, job, source, inputs, expected):
    out = P(args.output)
    stages = []
    by_month = collections.defaultdict(list)
    for r in inputs:
        by_month[r["date"][:6]].append(r)
    completed_rows = 0
    for month, parts in sorted(by_month.items()):
        cp = out / "checkpoints/stage" / f"{month}.json"
        if cp.exists():
            result = load(cp)
            for info in result["parts"]:
                verify_part(out / info["file"], info)
        else:
            result = build_month(
                month,
                parts,
                args,
                job,
                (len(stages), completed_rows, len(by_month), expected),
            )
        stages.append(result)
        completed_rows += result["rows"]
    if completed_rows != expected:
        raise ValueError("Staged row total differs from source")
    job.update(
        completed_months=len(stages), total_months=len(stages), staged_verified_rows=completed_rows
    )
    return stages
