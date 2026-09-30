from __future__ import annotations

import argparse

from .tushare_cli_common import (
    BACKFILL_DATASETS,
    BACKFILL_SEGMENTS,
    add_provider_runtime_arguments,
    add_token_env_argument,
)


def add_tushare_backfill_parser(subparsers: argparse._SubParsersAction) -> None:
    backfill = subparsers.add_parser(
        "backfill-a-share-history",
        help="Plan or run segmented TuShare A 股 raw history backfill.",
    )
    backfill.add_argument("--artifacts-root")
    backfill.add_argument("--start-date", required=True)
    backfill.add_argument("--end-date", required=True)
    backfill.add_argument(
        "--dataset",
        dest="datasets",
        action="append",
        choices=BACKFILL_DATASETS,
        help=(
            "Dataset to backfill; repeat for multiple datasets. Defaults to all raw daily datasets."
        ),
    )
    backfill.add_argument(
        "--segment",
        default="month",
        choices=BACKFILL_SEGMENTS,
        help="Backfill request segment size (default: month).",
    )
    backfill.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Refetch partitions even when trade_date parquet files already exist.",
    )
    backfill.add_argument(
        "--sync-latest",
        action="store_true",
        help="Point canonical latest aliases at completed backfill snapshots.",
    )
    backfill.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue remaining segments/datasets after a provider or write error.",
    )
    backfill.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the backfill plan without provider calls or writes.",
    )
    add_token_env_argument(backfill)
    add_provider_runtime_arguments(backfill)


def add_tushare_refresh_parser(subparsers: argparse._SubParsersAction) -> None:
    refresh = subparsers.add_parser(
        "plan-a-share-current-refresh",
        help="Plan an A 股 current refresh without provider calls, writes, or alias updates.",
    )
    refresh.add_argument("--artifacts-root")
    refresh.add_argument("--start-date", required=True)
    refresh.add_argument("--end-date", required=True)
    refresh.add_argument(
        "--dataset",
        dest="datasets",
        action="append",
        choices=BACKFILL_DATASETS,
        help="Raw dataset to include; repeatable. Defaults to all required datasets.",
    )
    refresh.add_argument(
        "--segment",
        default="month",
        choices=BACKFILL_SEGMENTS,
        help="Backfill request segment size (default: month).",
    )

    promote = subparsers.add_parser(
        "promote-a-share-current",
        help="Gate and publish a prepared TuShare A 股 current release.",
    )
    promote.add_argument("--artifacts-root")
    promote.add_argument("--start-date", required=True)
    promote.add_argument("--end-date", required=True)
    promote.add_argument(
        "--segment",
        default="month",
        choices=BACKFILL_SEGMENTS,
        help="Backfill segment size used to infer default raw snapshot paths.",
    )
    promote.add_argument("--daily-dir")
    promote.add_argument("--adj-factor-dir")
    promote.add_argument("--daily-basic-dir")
    promote.add_argument("--limit-status-dir")
    promote.add_argument("--daily-clean-dir")
    promote.add_argument("--universe-by-date")
    promote.add_argument("--universe-symbols")
    promote.add_argument("--universe-meta")
    promote.add_argument("--daily-clean-baseline-report")
    promote.add_argument("--daily-clean-research-report")
    promote.add_argument("--universe-validation-report")
    promote.add_argument("--current-health-report")
    promote.add_argument("--evidence-out")
    promote.add_argument(
        "--required-asset",
        dest="required_assets",
        action="append",
        help="Contract asset key required by the promotion gate. Repeatable.",
    )
    promote.add_argument(
        "--fail-on-severity",
        choices=("none", "info", "warning", "error"),
        default="warning",
    )
    promote.add_argument(
        "--apply",
        action="store_true",
        help="Publish aliases, current contract, registry, health report, and evidence.",
    )


__all__ = ["add_tushare_backfill_parser", "add_tushare_refresh_parser"]
