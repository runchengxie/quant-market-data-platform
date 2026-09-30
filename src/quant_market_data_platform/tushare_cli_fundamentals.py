from __future__ import annotations

import argparse

from .tushare_cli_common import ENTITLEMENT_MODES, FUNDAMENTALS_DATASETS, add_token_env_argument


def add_tushare_fundamentals_parsers(subparsers: argparse._SubParsersAction) -> None:
    _add_tushare_fundamentals_plan_parsers(subparsers)
    _add_tushare_fundamentals_state_parsers(subparsers)
    _add_tushare_fundamentals_asset_parsers(subparsers)


def _add_tushare_fundamentals_plan_parsers(subparsers: argparse._SubParsersAction) -> None:
    specs = subparsers.add_parser(
        "list-a-share-fundamentals-specs",
        help="List platform-native TuShare A 股基本面 dataset specs.",
    )
    specs.set_defaults(tushare_command="list-a-share-fundamentals-specs")

    plan = subparsers.add_parser(
        "plan-a-share-fundamentals",
        help="Plan TuShare A 股基本面 query units without provider calls or writes.",
    )
    plan.add_argument("--dataset", dest="datasets", action="append", choices=FUNDAMENTALS_DATASETS)
    plan.add_argument("--start-date", required=True)
    plan.add_argument("--end-date", required=True)
    plan.add_argument("--entitlement-mode", choices=ENTITLEMENT_MODES, default="vip_batch")
    plan.add_argument("--symbol", dest="symbols", action="append")
    plan.add_argument("--symbols-file")

    download = subparsers.add_parser(
        "download-a-share-fundamentals",
        help="Run restartable raw TuShare A 股基本面 acquisition for one dataset.",
    )
    download.add_argument("--dataset", required=True, choices=FUNDAMENTALS_DATASETS)
    download.add_argument("--out-dir", required=True)
    download.add_argument("--start-date", required=True)
    download.add_argument("--end-date", required=True)
    download.add_argument("--entitlement-mode", choices=ENTITLEMENT_MODES, default="vip_batch")
    download.add_argument("--symbol", dest="symbols", action="append")
    download.add_argument("--symbols-file")
    download.add_argument("--run-id")
    download.add_argument("--retry-attempts", type=int, default=3)
    download.add_argument("--retry-backoff-seconds", type=float, default=0.0)
    download.add_argument(
        "--request-interval-seconds",
        type=float,
        default=0.0,
        help="Minimum delay between TuShare API requests for low-frequency entitlements.",
    )
    download.add_argument("--page-size", type=int, default=5000)
    download.add_argument("--max-pages", type=int, default=100)
    download.add_argument("--stale-after-days", type=int)
    add_token_env_argument(download)


def _add_tushare_fundamentals_state_parsers(subparsers: argparse._SubParsersAction) -> None:
    state = subparsers.add_parser(
        "check-a-share-fundamentals-state",
        help="Print a persisted TuShare A 股基本面 restart state file.",
    )
    state.add_argument("--state-file", required=True)

    failures = subparsers.add_parser(
        "list-a-share-fundamentals-failures",
        help="Print a machine-readable TuShare A 股基本面 failure report.",
    )
    failures.add_argument("--failure-file", required=True)

    compact = subparsers.add_parser(
        "compact-a-share-fundamentals-raw",
        help="Compact restartable raw TuShare A 股基本面 parquet parts.",
    )
    compact.add_argument("--raw-dir", required=True)
    compact.add_argument("--out-dir", required=True)

    event_pit = subparsers.add_parser(
        "build-a-share-announcement-event-pit",
        help="Build research-only announcement-time PIT events from raw TuShare data.",
    )
    event_pit.add_argument("--dataset", required=True, choices=FUNDAMENTALS_DATASETS)
    event_pit.add_argument("--raw-dir", required=True)
    event_pit.add_argument("--out-dir", required=True)
    event_pit.add_argument(
        "--value-column",
        dest="value_columns",
        action="append",
        default=[],
        help="Optional value column projection; repeat for multiple columns.",
    )
    event_pit.add_argument(
        "--report-type",
        dest="report_types",
        action="append",
        default=[],
        help="Optional report type filter; repeat to include multiple types.",
    )


def _add_tushare_fundamentals_asset_parsers(subparsers: argparse._SubParsersAction) -> None:
    normalize = subparsers.add_parser(
        "normalize-a-share-fundamentals",
        help="Build normalized TuShare A 股基本面 assets while preserving raw provenance.",
    )
    normalize.add_argument("--dataset", required=True, choices=FUNDAMENTALS_DATASETS)
    normalize.add_argument("--raw-dir", required=True)
    normalize.add_argument("--out-dir", required=True)

    validate_normalized = subparsers.add_parser(
        "validate-a-share-normalized-fundamentals",
        help="Validate normalized TuShare A 股基本面 assets.",
    )
    validate_normalized.add_argument("--asset-dir", required=True)
    validate_normalized.add_argument("--target-date")

    pit = subparsers.add_parser(
        "build-a-share-fundamentals-pit",
        help="Build PIT TuShare A 股基本面 assets from normalized inputs.",
    )
    pit.add_argument("--normalized-dir", dest="normalized_dirs", action="append", required=True)
    pit.add_argument("--out-dir", required=True)
    pit.add_argument("--field-map", dest="field_mappings", action="append", required=True)
    pit.add_argument("--available-delay-days", type=int, default=1)
    pit.add_argument("--max-observation-age-days", type=int, default=3)
    pit.add_argument("--bucket-count", type=int, default=128)
    pit.add_argument("--batch-rows", type=int, default=65536)
    pit.add_argument("--memory-soft-limit-mb", type=float, default=2048.0)
    pit.add_argument("--memory-hard-limit-mb", type=float, default=1024.0)

    validate_pit = subparsers.add_parser(
        "validate-a-share-fundamentals-pit",
        help="Validate PIT TuShare A 股基本面 assets and disclosure semantics.",
    )
    validate_pit.add_argument("--asset-dir", required=True)
    validate_pit.add_argument("--target-date")
    validate_pit.add_argument("--batch-rows", type=int, default=65536)
    validate_pit.add_argument("--memory-soft-limit-mb", type=float, default=2048.0)
    validate_pit.add_argument("--memory-hard-limit-mb", type=float, default=1024.0)

    publish = subparsers.add_parser(
        "publish-a-share-fundamentals",
        help="Publish normalized and PIT TuShare A 股基本面 aliases after validation.",
    )
    publish.add_argument("--artifacts-root")
    publish.add_argument("--normalized-dir", dest="normalized_dirs", action="append", required=True)
    publish.add_argument("--pit-dir", required=True)
    publish.add_argument("--target-date", required=True)

    publish_pit = subparsers.add_parser(
        "publish-a-share-pit-fundamentals",
        help="Publish the PIT TuShare A 股基本面 alias after PIT validation.",
    )
    publish_pit.add_argument("--artifacts-root")
    publish_pit.add_argument("--pit-dir", required=True)
    publish_pit.add_argument("--target-date", required=True)


__all__ = ["add_tushare_fundamentals_parsers"]
