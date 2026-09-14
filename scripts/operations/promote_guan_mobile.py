#!/usr/bin/env python3
"""Promote a verified Guan mobile archive into provider-native enclosure raw."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from market_data_platform.guan_mobile_raw import (
    GuanMobilePromotionError,
    PromotionOptions,
    promote_guan_mobile,
    verify_guan_mobile_promotion,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    promote = subparsers.add_parser("promote", help="Plan or apply provider-native promotion.")
    promote.add_argument("--source-dir", required=True)
    promote.add_argument("--raw-root", required=True)
    promote.add_argument("--archive-manifest", required=True)
    promote.add_argument("--receipt", required=True)
    promote.add_argument(
        "--strategy",
        choices=("hardlink", "copy"),
        default="hardlink",
        help="Use zero-data hardlinks by default; copy must be requested explicitly.",
    )
    promote.add_argument("--dry-run", action="store_true")

    verify = subparsers.add_parser("verify", help="Rehash every promoted logical artifact.")
    verify.add_argument("--receipt", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify":
            payload = verify_guan_mobile_promotion(args.receipt)
        else:
            payload = promote_guan_mobile(
                PromotionOptions(
                    source_dir=args.source_dir,
                    raw_root=args.raw_root,
                    archive_manifest=args.archive_manifest,
                    receipt=args.receipt,
                    strategy=args.strategy,
                    dry_run=args.dry_run,
                )
            )
    except (GuanMobilePromotionError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
