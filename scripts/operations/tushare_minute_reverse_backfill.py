#!/usr/bin/env python3
"""Run one bounded, staging-only reverse TuShare minute backfill step."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from quant_market_data_platform.tushare_minute_reverse_backfill import (
    ReverseBackfillOptions,
    run_reverse_backfill,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--token-env", default="TUSHARE_TOKEN_2")
    parser.add_argument("--api-url")
    parser.add_argument("--max-dates", type=int, default=1)
    parser.add_argument(
        "--history-start-date", help="Inclusive YYYYMMDD lower bound; persisted unless dry-run"
    )
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--cooldown-seconds", type=float, default=1.2)
    parser.add_argument("--minute-quota-allow-burst", action="store_true")
    parser.add_argument("--bj-missing-policy", choices=("report", "error"), default="report")
    parser.add_argument("--audit-block-path", type=Path)
    parser.add_argument("--retry-deferred", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_reverse_backfill(
        ReverseBackfillOptions(
            artifacts_root=args.artifacts_root,
            token_env=args.token_env,
            api_url=args.api_url,
            max_dates=args.max_dates,
            history_start_date=args.history_start_date,
            batch_size=args.batch_size,
            cooldown_seconds=args.cooldown_seconds,
            minute_quota_allow_burst=args.minute_quota_allow_burst,
            bj_missing_policy=args.bj_missing_policy,
            dry_run=args.dry_run,
            retry_deferred=args.retry_deferred,
            audit_block_path=args.audit_block_path,
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return (
        0
        if result["status"]
        in {
            "complete",
            "accepted_bj_missing",
            "deferred_missing",
            "download_pass_complete_with_gaps",
            "dry_run",
            "noop",
            "writer_busy",
        }
        else 75
    )


if __name__ == "__main__":
    raise SystemExit(main())
