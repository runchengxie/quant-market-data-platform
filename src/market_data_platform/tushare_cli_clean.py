from __future__ import annotations

import argparse


def add_tushare_clean_parsers(subparsers: argparse._SubParsersAction) -> None:
    clean = subparsers.add_parser(
        "build-a-share-daily-clean",
        help="Build the TuShare A 股 daily_clean asset from raw daily and optional overlays.",
    )
    clean.add_argument("--daily-dir", required=True)
    clean.add_argument("--out-dir", required=True)
    clean.add_argument("--adj-factor-dir")
    clean.add_argument("--daily-basic-dir")
    clean.add_argument("--limit-status-dir")
    clean.add_argument("--suspend-dir")
    clean.add_argument("--instruments-file")
    clean.add_argument("--min-rows", type=int, default=1)
    clean.add_argument("--min-symbols", type=int, default=1)
    clean.add_argument(
        "--batch-trade-dates",
        type=int,
        default=120,
        help="Trade-date partitions per memory-managed staging batch (default: 120).",
    )
    clean.add_argument(
        "--memory-soft-limit-mb",
        type=float,
        default=2048.0,
        help="Flush the current batch when MemAvailable falls below this value; <=0 disables.",
    )
    clean.add_argument(
        "--memory-hard-limit-mb",
        type=float,
        default=1024.0,
        help="Abort when MemAvailable falls below this value; <=0 disables.",
    )

    validate = subparsers.add_parser(
        "validate-a-share-daily-clean",
        help="Run quality gates for a TuShare A 股 daily_clean asset.",
    )
    validate.add_argument("--daily-clean-dir", required=True)
    validate.add_argument("--min-rows", type=int, default=1)
    validate.add_argument("--min-symbols", type=int, default=1)
    validate.add_argument("--require-valuation", action="store_true")
    validate.add_argument("--require-limit-status", action="store_true")
    validate.add_argument("--profile", choices=("baseline", "research"), default="baseline")
    validate.add_argument("--trade-cal-file")
    validate.add_argument(
        "--fail-on-severity",
        choices=("none", "info", "warning", "error"),
        default="error",
    )
    validate.add_argument("--max-warning-rate", type=float, default=0.0)
    validate.add_argument("--pct-chg-tolerance", type=float, default=0.05)
    validate.add_argument("--batch-rows", type=int, default=65536)
    validate.add_argument("--memory-soft-limit-mb", type=float, default=2048.0)
    validate.add_argument("--memory-hard-limit-mb", type=float, default=1024.0)
    validate.add_argument("--out")


def add_tushare_universe_parsers(subparsers: argparse._SubParsersAction) -> None:
    universe = subparsers.add_parser(
        "build-a-share-universe",
        help="Build a PIT A 股 full-market universe from a local TuShare daily_clean asset.",
    )
    universe.add_argument("--artifacts-root")
    universe.add_argument("--daily-clean-dir")
    universe.add_argument("--start-date", required=True)
    universe.add_argument("--end-date", required=True)
    universe.add_argument("--rebalance-frequency", default="M")
    universe.add_argument("--lookback-days", type=int, default=60)
    universe.add_argument("--min-window-days", type=int, default=30)
    universe.add_argument("--top-quantile", type=float, default=0.0)
    universe.add_argument("--min-turnover", type=float, default=0.0)
    universe.add_argument("--out")
    universe.add_argument("--latest-out")
    universe.add_argument("--meta-out")
    universe.add_argument("--min-rows", type=int, default=1)
    universe.add_argument("--min-symbols", type=int, default=1)
    universe.add_argument("--min-rebalance-dates", type=int, default=1)
    universe.add_argument("--force", action="store_true")

    validate = subparsers.add_parser(
        "validate-a-share-universe",
        help="Run quality gates for a TuShare A 股 full-market universe.",
    )
    validate.add_argument("--by-date-file", required=True)
    validate.add_argument("--latest-symbols-file", required=True)
    validate.add_argument("--meta-file", required=True)
    validate.add_argument("--expected-as-of")
    validate.add_argument("--min-rows", type=int, default=1)
    validate.add_argument("--min-symbols", type=int, default=1)
    validate.add_argument("--min-rebalance-dates", type=int, default=1)
    validate.add_argument("--out")


__all__ = ["add_tushare_clean_parsers", "add_tushare_universe_parsers"]
