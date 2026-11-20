"""Explicit pilot CLI; no source deletion or canonical publication commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .accepted import scan_accepted
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
    convert.add_argument(
        "--identity-policy", choices=["strict", "preserve-source-labels"], default="strict"
    )
    check = commands.add_parser("verify", help="Recheck every output file and source-member proof")
    check.add_argument("--root", type=Path, required=True)
    query = commands.add_parser("query", help="Query native fields with security/time routing")
    query.add_argument("--root", type=Path, required=True)
    accepted = commands.add_parser(
        "query-accepted", help="Query specific dates with verified acceptance receipts"
    )
    accepted.add_argument("--receipt-dir", type=Path, action="append", required=True)
    accepted.add_argument("--date", action="append", required=True)
    for query_parser in (query, accepted):
        query_parser.add_argument(
            "--modality", choices=["orders", "trades", "snapshots"], required=True
        )
        query_parser.add_argument("--symbol", action="append")
        query_parser.add_argument("--start", type=int)
        query_parser.add_argument("--stop", type=int)
        query_parser.add_argument("--limit", type=int, default=20)
        query_parser.add_argument("--count", action="store_true")
    return result


def single_result(args):
    scanner = scan(args.root, args.modality, args.symbol, args.start, args.stop)
    return {"rows": scanner.count_rows()} if args.count else scanner.head(args.limit).to_pylist()


def accepted_result(args):
    scanners = scan_accepted(
        args.date, args.receipt_dir, args.modality, args.symbol, (args.start, args.stop)
    )
    if args.count:
        return [{"date": day, "rows": scanner.count_rows()} for day, scanner in scanners]
    return [
        {"date": day, "records": scanner.head(args.limit).to_pylist()} for day, scanner in scanners
    ]


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
                identity_policy=args.identity_policy,
            )
        elif args.command == "verify":
            result = verify(args.root)
        else:
            if args.limit < 0:
                raise ValueError("Query display limit must be nonnegative")
            if args.command == "query-accepted":
                result = accepted_result(args)
            else:
                result = single_result(args)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
