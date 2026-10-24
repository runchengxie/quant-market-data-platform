#!/usr/bin/env python3
"""Run the daily TuShare-native operational minute update."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from quant_market_data_platform.tushare_minute_operational_daily import (
    OperationalDailyOptions,
    inspect_operational_daily,
    run_operational_daily,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--token-env", default="TUSHARE_TOKEN_2")
    fallback = parser.add_mutually_exclusive_group()
    fallback.add_argument(
        "--fallback-token-env",
        default=os.environ.get("TUSHARE_OPERATIONAL_FALLBACK_TOKEN_ENV", "TUSHARE_TOKEN") or None,
    )
    fallback.add_argument(
        "--no-fallback",
        action="store_const",
        const=None,
        dest="fallback_token_env",
        help="Do not try a second credential after an incomplete primary download.",
    )
    parser.add_argument("--target-date")
    parser.add_argument("--api-url")
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Read published state and history without writes.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    operation = inspect_operational_daily if args.status_only else run_operational_daily
    payload = operation(
        OperationalDailyOptions(
            artifacts_root=args.artifacts_root,
            token_env=args.token_env,
            fallback_token_env=args.fallback_token_env,
            target_date=args.target_date,
            api_url=args.api_url,
        )
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 75 if payload["status"] in {"download_incomplete", "behind"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
