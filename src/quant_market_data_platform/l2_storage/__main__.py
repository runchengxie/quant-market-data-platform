"""Explicit pilot CLI; no source deletion or canonical publication commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .build import build
from .reader import scan, verify


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    convert = commands.add_parser("build", help="Build an isolated verified complete-day dataset")
    for name in ["archive", "output", "scratch", "extractor", "expected-sha256"]:
        convert.add_argument("--" + name, required=True)
    convert.add_argument("--buckets", type=int, default=64)
    convert.add_argument("--row-group-rows", type=int, default=65536)
    convert.add_argument("--zstd-level", type=int, default=6)
    check = commands.add_parser("verify", help="Recheck every output file and source-member proof")
    check.add_argument("--root", type=Path, required=True)
    query = commands.add_parser("query", help="Query native fields with security/time routing")
    query.add_argument("--root", type=Path, required=True)
    query.add_argument("--modality", choices=["orders", "trades", "snapshots"], required=True)
    query.add_argument("--symbol", action="append")
    query.add_argument("--start", type=int)
    query.add_argument("--stop", type=int)
    query.add_argument("--limit", type=int, default=20)
    query.add_argument("--count", action="store_true")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "build":
            result = build(
                Path(args.archive),
                Path(args.output),
                Path(args.scratch),
                args.extractor,
                args.expected_sha256,
                buckets=args.buckets,
                row_group_rows=args.row_group_rows,
                zstd_level=args.zstd_level,
            )
        elif args.command == "verify":
            result = verify(args.root)
        else:
            if args.limit < 0:
                raise ValueError("Query display limit must be nonnegative")
            scanner = scan(args.root, args.modality, args.symbol, args.start, args.stop)
            result = (
                {"rows": scanner.count_rows()}
                if args.count
                else scanner.head(args.limit).to_pylist()
            )
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
