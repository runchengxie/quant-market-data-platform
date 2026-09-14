"""CLI parsers for historical trading-constraint reference assets."""

from __future__ import annotations

import argparse

from .tushare_cli_common import add_token_env_argument


def add_tushare_constraint_parsers(subparsers: argparse._SubParsersAction) -> None:
    download = subparsers.add_parser(
        "download-a-share-constraint-reference",
        help="Download restartable trading-constraint history with receipts.",
    )
    download.add_argument(
        "--dataset",
        required=True,
        choices=(
            "namechange",
            "margin_secs",
            "margin_detail",
            "suspend_d",
            "st",
            "slb_sec_detail",
        ),
    )
    download.add_argument("--out-dir", required=True)
    download.add_argument("--start-date", required=True)
    download.add_argument("--end-date", required=True)
    download.add_argument("--page-size", type=int, default=5000)
    download.add_argument("--max-pages", type=int, default=100)
    download.add_argument("--request-interval-seconds", type=float, default=0.2)
    download.add_argument("--retries", type=int, default=5)
    add_token_env_argument(download)

    build = subparsers.add_parser(
        "build-a-share-st-history",
        help="Reconstruct daily ST status from namechange intervals and validate it.",
    )
    build.add_argument("--namechange", required=True)
    build.add_argument("--trade-cal", required=True)
    build.add_argument("--instruments", required=True)
    build.add_argument("--stock-st")
    build.add_argument("--out-dir", required=True)
    build.add_argument("--start-date", required=True)
    build.add_argument("--end-date", required=True)
    build.add_argument("--min-precision", type=float, default=0.90)
    build.add_argument("--min-recall", type=float, default=0.90)

    publish = subparsers.add_parser(
        "publish-a-share-constraint-reference",
        help="Publish constraint sources and reconstructed ST assets into the asset tree.",
    )
    publish.add_argument("--artifacts-root")
    publish.add_argument("--source-dir", required=True)
    publish.add_argument("--target-date", required=True)
    publish.add_argument("--allow-partial", action="store_true")


__all__ = ["add_tushare_constraint_parsers"]
