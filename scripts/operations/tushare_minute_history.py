#!/usr/bin/env python3
"""Inspect minute history, record local monitoring alerts, or repair queued gaps."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "watch", "repair"):
        command = commands.add_parser(name)
        command.add_argument("--artifacts-root", type=Path, required=True)
        command.add_argument("--runtime-root", type=Path, required=True)
        command.add_argument("--min-free-bytes", type=int, default=10 * 1024**3)
        if name == "repair":
            command.add_argument("--token-env", default="TUSHARE_TOKEN_2")
            command.add_argument("--api-url")
            command.add_argument("--daily-request-limit", type=int, default=500)
            command.add_argument("--max-attempts", type=int, default=3)
            command.add_argument("--cooldown-seconds", type=float, default=1.2)
            command.add_argument("--allow-burst", action="store_true")
            command.add_argument("--dry-run", action="store_true")
        else:
            command.add_argument("--stall-seconds", type=int, default=900)
        if name == "watch":
            command.add_argument("--state-root", type=Path, required=True)
    return parser


def _repair(args: argparse.Namespace) -> dict:
    from quant_market_data_platform.minute_gap_repair import GapRepairOptions, run_gap_repair

    return run_gap_repair(
        GapRepairOptions(
            artifacts_root=args.artifacts_root,
            runtime_root=args.runtime_root,
            token_env=args.token_env,
            api_url=args.api_url,
            daily_request_limit=args.daily_request_limit,
            max_attempts=args.max_attempts,
            cooldown_seconds=args.cooldown_seconds,
            allow_burst=args.allow_burst,
            min_free_bytes=args.min_free_bytes,
            dry_run=args.dry_run,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.min_free_bytes < 0:
        raise ValueError("min-free-bytes must be nonnegative")
    if args.command == "repair":
        result = _repair(args)
        unhealthy = result.get("status") in {
            "audit_blocked",
            "failed",
            "invalid_queue",
            "invalid_source",
            "invalid_state",
            "request_failed",
            "no_eligible_queue",
        }
    else:
        from quant_market_data_platform.minute_history_status import history_status, watch_history

        keywords = {"min_free_bytes": args.min_free_bytes, "stall_seconds": args.stall_seconds}
        if args.command == "watch":
            result = watch_history(
                args.artifacts_root, args.runtime_root, args.state_root, **keywords
            )
        else:
            result = history_status(args.artifacts_root, args.runtime_root, **keywords)
        unhealthy = result.get("state") in {"audit_blocked", "stalled", "unknown_stale_evidence"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 75 if unhealthy else 0


if __name__ == "__main__":
    raise SystemExit(main())
