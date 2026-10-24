from __future__ import annotations

import argparse
import json
import os
import pathlib
import time

import pyarrow as pa

from .conversion import run
from .integrity import load

P = pathlib.Path


def main():
    ap = argparse.ArgumentParser(
        description="Offline lossless by-security ZSTD6 mirror; new output version only."
    )
    for option in ["source", "output", "identity-root"]:
        ap.add_argument("--" + option, required=True)
    ap.add_argument("--dates", nargs="*")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--buckets", type=int, default=128)
    ap.add_argument("--stage-rows", type=int, default=8192)
    ap.add_argument("--final-rows", type=int, default=65536)
    args = ap.parse_args()
    if min(args.buckets, args.stage_rows, args.final_rows) < 1:
        ap.error("Bucket and row-group counts must be positive")
    args.source = str(P(args.source).resolve())
    args.output = str(P(args.output).resolve())
    args.identity_root = str(P(args.identity_root).resolve())
    source, output = P(args.source), P(args.output)
    if source.is_relative_to(output) or output.is_relative_to(source):
        ap.error("Source and output directories must be disjoint")
    pa.set_cpu_count(2)
    pa.set_io_thread_count(2)
    out = P(args.output)
    out.mkdir(parents=True, exist_ok=True)
    lock = out / "RUNNING.lock"
    acquired = False
    import psutil

    try:
        if lock.exists():
            old = load(lock)
            try:
                p = psutil.Process(old["pid"])
                if abs(p.create_time() - old["create_time"]) < 0.01 and p.is_running():
                    raise RuntimeError("Another job is active")
            except psutil.NoSuchProcess:
                pass
            lock.rename(out / f"RUNNING.stale-{time.time_ns()}.json")
        with lock.open("x", encoding="utf-8") as f:
            json.dump(
                {
                    "pid": os.getpid(),
                    "create_time": psutil.Process().create_time(),
                    "version": "security-year-lossless-v2",
                },
                f,
            )
        acquired = True
        run(args)
    finally:
        if acquired and lock.exists() and (load(lock)["pid"] == os.getpid()):
            lock.unlink()


if __name__ == "__main__":
    main()
