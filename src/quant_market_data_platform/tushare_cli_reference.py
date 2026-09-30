"""Subcommand parsers for the reference TuShare A 股 datasets."""

from __future__ import annotations

import argparse

from .tushare_cli_common import add_token_env_argument


def add_tushare_reference_parsers(subparsers: argparse._SubParsersAction) -> None:
    specs = subparsers.add_parser(
        "list-a-share-reference-specs",
        help="List reference TuShare A 股 dataset specs (stock_st/index_weight/listed_company).",
    )
    specs.set_defaults(tushare_command="list-a-share-reference-specs")

    download = subparsers.add_parser(
        "download-a-share-reference",
        help="Download one reference TuShare A 股 dataset to out-dir.",
    )
    download.add_argument(
        "--dataset",
        required=True,
        choices=("stock_st", "index_weight", "stock_company", "stk_managers", "share_float"),
    )
    download.add_argument("--out-dir", required=True)
    download.add_argument("--start-date", required=True)
    download.add_argument("--end-date", required=True)
    download.add_argument("--index-code", help="index_code for index_weight (default 000300.SH)")
    download.add_argument("--exchange", help="exchange for stock_company")
    download.add_argument("--run-id")
    download.add_argument("--request-interval-seconds", type=float, default=0.2)
    download.add_argument("--retries", type=int, default=5)
    add_token_env_argument(download)

    normalize = subparsers.add_parser(
        "normalize-a-share-reference",
        help="Build daily index_weight_daily from monthly index_weight (drift weights).",
    )
    normalize.add_argument("--raw-dir", required=True)
    normalize.add_argument("--out-dir", required=True)
    normalize.add_argument("--trade-cal", help="Published trade calendar parquet")
    normalize.add_argument(
        "--end-date", help="Last trade date to carry the latest snapshot through"
    )

    publish = subparsers.add_parser(
        "publish-a-share-reference",
        help="Publish reference datasets into the standard MDP asset tree.",
    )
    publish.add_argument("--artifacts-root")
    publish.add_argument("--raw-dir", required=True)
    publish.add_argument("--target-date", required=True)
    publish.add_argument("--allow-partial", action="store_true")


__all__ = ["add_tushare_reference_parsers"]
