#!/usr/bin/env python3
"""Run the daily TuShare-native operational minute update."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from market_data_platform.tushare_minute_operational_daily import (
    OperationalDailyOptions,
    run_operational_daily,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--token-env", default="TUSHARE_TOKEN_2")
    parser.add_argument("--fallback-token-env", default="TUSHARE_TOKEN")
    parser.add_argument("--target-date")
    parser.add_argument("--api-url")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_operational_daily(
        OperationalDailyOptions(
            artifacts_root=args.artifacts_root,
            token_env=args.token_env,
            fallback_token_env=args.fallback_token_env,
            target_date=args.target_date,
            api_url=args.api_url,
        )
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] != "download_incomplete" else 75


if __name__ == "__main__":
    raise SystemExit(main())
