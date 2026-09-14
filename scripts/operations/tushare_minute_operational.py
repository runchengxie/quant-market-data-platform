#!/usr/bin/env python3
"""Assemble and promote a TuShare-native operational minute dataset."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from market_data_platform.tushare_minute_operational import (
    OperationalAssembly,
    assemble_operational_version,
    promote_operational_alias,
)


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _assemble(args: argparse.Namespace) -> int:
    payload = assemble_operational_version(
        OperationalAssembly(
            base_receipt=args.base_receipt,
            incremental_roots=tuple(args.incremental_root),
            trade_calendar=args.trade_calendar,
            end_date=args.end_date,
            output_dir=args.output_dir,
            receipt_json=args.receipt_json,
            repair_dates=tuple(args.repair_date),
        )
    )
    print(
        "TuShare operational version published: "
        f"dates={payload['summary']['dates']} "
        f"date_max={payload['summary']['date_max']} "
        f"rows={payload['summary']['rows']}"
    )
    return 0


def _promote(args: argparse.Namespace) -> int:
    payload = promote_operational_alias(
        args.version_receipt,
        args.alias,
        args.legacy_alias,
        args.receipt_json,
    )
    print(
        "TuShare operational alias promoted: "
        f"alias={payload['alias_path']} "
        f"legacy={payload['legacy_canonical']['resolved_path']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    assemble = commands.add_parser("assemble")
    assemble.add_argument("--base-receipt", type=_path, required=True)
    assemble.add_argument("--incremental-root", type=_path, action="append", required=True)
    assemble.add_argument("--trade-calendar", type=_path, required=True)
    assemble.add_argument("--end-date", required=True)
    assemble.add_argument("--output-dir", type=_path, required=True)
    assemble.add_argument("--receipt-json", type=_path, required=True)
    assemble.add_argument(
        "--repair-date",
        action="append",
        default=[],
        help="Missing historical YYYYMMDD date to add; repeat for multiple dates.",
    )
    assemble.set_defaults(func=_assemble)
    promote = commands.add_parser("promote")
    promote.add_argument("--version-receipt", type=_path, required=True)
    promote.add_argument("--alias", type=_path, required=True)
    promote.add_argument("--legacy-alias", type=_path, required=True)
    promote.add_argument("--receipt-json", type=_path, required=True)
    promote.set_defaults(func=_promote)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
