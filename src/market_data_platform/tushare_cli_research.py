from __future__ import annotations

import argparse

from .tushare_cli_common import add_token_env_argument


def add_tushare_research_asset_parsers(subparsers: argparse._SubParsersAction) -> None:
    _add_tushare_industry_research_parsers(subparsers)
    _add_tushare_flow_feature_parsers(subparsers)
    _add_tushare_hotspot_feature_parsers(subparsers)
    _add_tushare_fund_portfolio_feature_parsers(subparsers)
    _add_tushare_holder_structure_feature_parsers(subparsers)
    _add_tushare_holder_event_feature_parsers(subparsers)
    _add_tushare_hsgt_feature_parsers(subparsers)


def _add_tushare_industry_research_parsers(subparsers: argparse._SubParsersAction) -> None:
    industry_download = subparsers.add_parser(
        "download-a-share-industry-membership",
        help="Download TuShare 申万 A 股行业成分 source data for industry_changes.",
    )
    industry_download.add_argument("--out-dir", required=True)
    industry_download.add_argument("--src", default="SW2021")
    industry_download.add_argument("--level", choices=("L1", "L2", "L3"), default="L3")
    industry_download.add_argument(
        "--is-new",
        dest="is_new_flags",
        action="append",
        choices=("Y", "N"),
        help="TuShare is_new flag to download; repeatable. Default downloads Y and N.",
    )
    industry_download.add_argument("--request-interval-seconds", type=float, default=0.1)
    industry_download.add_argument("--min-rows", type=int, default=1)
    industry_download.add_argument("--min-symbols", type=int, default=1)
    add_token_env_argument(industry_download)

    pit = subparsers.add_parser(
        "build-a-share-pit-fundamentals",
        help="Build a PIT A 股 fundamentals asset from a licensed local extract.",
    )
    pit.add_argument("--source-file", required=True)
    pit.add_argument("--out-dir", required=True)
    pit.add_argument("--report-period-col", default="end_date")
    pit.add_argument("--disclosure-date-col", default="ann_date")
    pit.add_argument("--available-delay-days", type=int, default=1)
    pit.add_argument(
        "--field-map",
        dest="field_maps",
        action="append",
        help="Rename a value field as source=target; repeatable.",
    )
    pit.add_argument("--provider", default="tushare")
    pit.add_argument("--min-rows", type=int, default=1)
    pit.add_argument("--min-symbols", type=int, default=1)

    validate_pit = subparsers.add_parser(
        "validate-a-share-pit-fundamentals",
        help="Validate a normalized PIT A 股 fundamentals asset.",
    )
    validate_pit.add_argument("--asset-dir", required=True)
    validate_pit.add_argument("--min-rows", type=int, default=1)
    validate_pit.add_argument("--min-symbols", type=int, default=1)

    industry = subparsers.add_parser(
        "build-a-share-industry-changes",
        help="Build historical A 股 industry membership from a licensed local extract.",
    )
    industry.add_argument("--source-file", required=True)
    industry.add_argument("--out-dir", required=True)
    industry.add_argument("--effective-date-col", default="start_date")
    industry.add_argument("--end-date-col", default="end_date")
    industry.add_argument("--industry-code-col", default="industry_code")
    industry.add_argument("--industry-name-col", default="industry_name")
    industry.add_argument("--industry-system", default="sw")
    industry.add_argument("--provider", default="licensed_extract")
    industry.add_argument("--min-rows", type=int, default=1)
    industry.add_argument("--min-symbols", type=int, default=1)

    validate_industry = subparsers.add_parser(
        "validate-a-share-industry-changes",
        help="Validate normalized historical A 股 industry membership.",
    )
    validate_industry.add_argument("--asset-dir", required=True)
    validate_industry.add_argument("--min-rows", type=int, default=1)
    validate_industry.add_argument("--min-symbols", type=int, default=1)


def _add_tushare_flow_feature_parsers(subparsers: argparse._SubParsersAction) -> None:
    flow = subparsers.add_parser(
        "build-a-share-flow-ownership-features",
        help="Build rolling A 股 moneyflow and ownership-style feature parquet.",
    )
    flow.add_argument("--moneyflow-dir", required=True)
    flow.add_argument("--out-dir", required=True)
    flow.add_argument(
        "--daily-dir",
        help="Optional TuShare daily asset; daily.amount is converted to ten-thousand CNY.",
    )
    flow.add_argument(
        "--daily-basic-dir",
        help="Optional TuShare daily_basic asset for circ_mv denominators.",
    )
    flow.add_argument(
        "--industry-dir",
        help="Optional industry_changes asset for within-industry zscore features.",
    )
    flow.add_argument(
        "--window",
        dest="windows",
        action="append",
        type=int,
        help="Rolling window in trading days; repeatable. Default: 5, 20, 60.",
    )
    flow.add_argument("--min-rows", type=int, default=1)
    flow.add_argument("--min-symbols", type=int, default=1)

    validate_flow = subparsers.add_parser(
        "validate-a-share-flow-ownership-features",
        help="Validate a derived A 股 moneyflow and ownership feature asset.",
    )
    validate_flow.add_argument("--asset-dir", required=True)
    validate_flow.add_argument("--min-rows", type=int, default=1)
    validate_flow.add_argument("--min-symbols", type=int, default=1)


def _add_tushare_hotspot_feature_parsers(subparsers: argparse._SubParsersAction) -> None:
    hotspot = subparsers.add_parser(
        "build-a-share-hotspot-features",
        help="Build PIT A 股热点题材特征 from hot-list, concept, KPL, and confirmation assets.",
    )
    hotspot.add_argument("--daily-basic-dir", required=True)
    hotspot.add_argument("--ths-hot-dir", required=True)
    hotspot.add_argument("--dc-concept-dir", required=True)
    hotspot.add_argument("--dc-concept-cons-dir", required=True)
    hotspot.add_argument("--kpl-list-dir", required=True)
    hotspot.add_argument("--out-dir", required=True)
    hotspot.add_argument("--start-date", required=True)
    hotspot.add_argument("--end-date", required=True)
    hotspot.add_argument("--kpl-concept-cons-dir")
    hotspot.add_argument("--limit-step-dir")
    hotspot.add_argument("--report-rc-dir")
    hotspot.add_argument("--stk-surv-dir")
    hotspot.add_argument("--broker-recommend-dir")
    hotspot.add_argument("--min-rows", type=int, default=1)
    hotspot.add_argument("--min-symbols", type=int, default=1)

    validate_hotspot = subparsers.add_parser(
        "validate-a-share-hotspot-features",
        help="Validate derived A 股热点题材 feature asset.",
    )
    validate_hotspot.add_argument("--asset-dir", required=True)
    validate_hotspot.add_argument("--min-rows", type=int, default=1)
    validate_hotspot.add_argument("--min-symbols", type=int, default=1)


def _add_tushare_fund_portfolio_feature_parsers(
    subparsers: argparse._SubParsersAction,
) -> None:
    fund_features = subparsers.add_parser(
        "build-a-share-fund-portfolio-features",
        help="Build PIT A 股 public-fund stock ownership features from fund_portfolio.",
    )
    fund_features.add_argument("--fund-portfolio-dir", required=True)
    fund_features.add_argument("--out-dir", required=True)
    fund_features.add_argument(
        "--daily-basic-dir",
        help="Optional TuShare daily_basic asset for market-value/share denominators.",
    )
    fund_features.add_argument("--available-delay-days", type=int, default=1)
    fund_features.add_argument("--min-rows", type=int, default=1)
    fund_features.add_argument("--min-symbols", type=int, default=1)

    validate_fund_features = subparsers.add_parser(
        "validate-a-share-fund-portfolio-features",
        help="Validate derived A 股 public-fund ownership feature asset.",
    )
    validate_fund_features.add_argument("--asset-dir", required=True)
    validate_fund_features.add_argument("--min-rows", type=int, default=1)
    validate_fund_features.add_argument("--min-symbols", type=int, default=1)

    top10_features = subparsers.add_parser(
        "build-a-share-fund-top10-portfolio-features",
        help=(
            "Build PIT A 股 public-fund ownership features after normalizing every "
            "disclosure to its largest stock positions."
        ),
    )
    top10_features.add_argument("--fund-portfolio-dir", required=True)
    top10_features.add_argument("--out-dir", required=True)
    top10_features.add_argument(
        "--daily-basic-dir",
        help="Optional TuShare daily_basic asset for market-value/share denominators.",
    )
    top10_features.add_argument("--available-delay-days", type=int, default=1)
    top10_features.add_argument("--top-n", type=int, default=10)
    top10_features.add_argument("--min-rows", type=int, default=1)
    top10_features.add_argument("--min-symbols", type=int, default=1)

    validate_top10_features = subparsers.add_parser(
        "validate-a-share-fund-top10-portfolio-features",
        help="Validate consistent-scope A 股 public-fund top-N ownership features.",
    )
    validate_top10_features.add_argument("--asset-dir", required=True)
    validate_top10_features.add_argument("--min-rows", type=int, default=1)
    validate_top10_features.add_argument("--min-symbols", type=int, default=1)


def _add_tushare_holder_structure_feature_parsers(
    subparsers: argparse._SubParsersAction,
) -> None:
    holder_features = subparsers.add_parser(
        "build-a-share-holder-structure-features",
        help="Build PIT A 股 shareholder-structure features from top10 holders assets.",
    )
    holder_features.add_argument("--top10-holders-dir", required=True)
    holder_features.add_argument("--out-dir", required=True)
    holder_features.add_argument("--top10-floatholders-dir")
    holder_features.add_argument(
        "--daily-basic-dir",
        help=(
            "Optional TuShare daily_basic asset for trading calendar alignment and"
            " circ_mv denominators."
        ),
    )
    holder_features.add_argument("--available-delay-days", type=int, default=1)
    holder_features.add_argument("--min-rows", type=int, default=1)
    holder_features.add_argument("--min-symbols", type=int, default=1)

    validate_holder_features = subparsers.add_parser(
        "validate-a-share-holder-structure-features",
        help="Validate derived A 股 shareholder-structure feature asset.",
    )
    validate_holder_features.add_argument("--asset-dir", required=True)
    validate_holder_features.add_argument("--min-rows", type=int, default=1)
    validate_holder_features.add_argument("--min-symbols", type=int, default=1)


def _add_tushare_holder_event_feature_parsers(
    subparsers: argparse._SubParsersAction,
) -> None:
    top_inst_events = subparsers.add_parser(
        "build-a-share-top-inst-events",
        help="Build sparse A 股 institutional trading event features from top_inst.",
    )
    top_inst_events.add_argument("--top-inst-dir", required=True)
    top_inst_events.add_argument("--out-dir", required=True)
    top_inst_events.add_argument(
        "--daily-dir",
        help="Optional TuShare daily asset; daily.amount is converted to ten-thousand CNY.",
    )
    top_inst_events.add_argument("--window", type=int, default=20)
    top_inst_events.add_argument("--min-rows", type=int, default=1)
    top_inst_events.add_argument("--min-symbols", type=int, default=1)

    validate_top_inst_events = subparsers.add_parser(
        "validate-a-share-top-inst-events",
        help="Validate derived A 股 institutional trading event feature asset.",
    )
    validate_top_inst_events.add_argument("--asset-dir", required=True)
    validate_top_inst_events.add_argument("--min-rows", type=int, default=1)
    validate_top_inst_events.add_argument("--min-symbols", type=int, default=1)

    holdertrade_events = subparsers.add_parser(
        "build-a-share-holdertrade-events",
        help="Build sparse A 股 shareholder increase/decrease event features.",
    )
    holdertrade_events.add_argument("--stk-holdertrade-dir", required=True)
    holdertrade_events.add_argument("--out-dir", required=True)
    holdertrade_events.add_argument(
        "--daily-basic-dir",
        help="Optional TuShare daily_basic asset for trading calendar and circ_mv.",
    )
    holdertrade_events.add_argument("--amount-window", type=int, default=20)
    holdertrade_events.add_argument("--count-window", type=int, default=60)
    holdertrade_events.add_argument("--available-delay-days", type=int, default=1)
    holdertrade_events.add_argument("--min-rows", type=int, default=1)
    holdertrade_events.add_argument("--min-symbols", type=int, default=1)

    validate_holdertrade_events = subparsers.add_parser(
        "validate-a-share-holdertrade-events",
        help="Validate derived A 股 shareholder increase/decrease event features.",
    )
    validate_holdertrade_events.add_argument("--asset-dir", required=True)
    validate_holdertrade_events.add_argument("--min-rows", type=int, default=1)
    validate_holdertrade_events.add_argument("--min-symbols", type=int, default=1)


def _add_tushare_hsgt_feature_parsers(subparsers: argparse._SubParsersAction) -> None:
    hsgt_features = subparsers.add_parser(
        "build-a-share-hsgt-market-features",
        help="Build market-level A 股 Connect moneyflow regime features.",
    )
    hsgt_features.add_argument("--moneyflow-hsgt-dir", required=True)
    hsgt_features.add_argument("--out-dir", required=True)
    hsgt_features.add_argument(
        "--window",
        dest="windows",
        action="append",
        type=int,
        help="Rolling window in trading days; repeatable. Default: 5, 20, 60.",
    )
    hsgt_features.add_argument("--min-rows", type=int, default=1)

    validate_hsgt_features = subparsers.add_parser(
        "validate-a-share-hsgt-market-features",
        help="Validate derived A 股 Connect moneyflow regime feature asset.",
    )
    validate_hsgt_features.add_argument("--asset-dir", required=True)
    validate_hsgt_features.add_argument("--min-rows", type=int, default=1)


__all__ = ["add_tushare_research_asset_parsers"]
