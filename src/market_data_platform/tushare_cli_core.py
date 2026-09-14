from __future__ import annotations

import argparse

from .tushare_cli_common import (
    add_provider_runtime_arguments,
    add_token_env_argument,
    add_tushare_date_mirror_parser,
)


def add_tushare_core_mirror_parsers(tushare_subparsers: argparse._SubParsersAction) -> None:
    _add_tushare_token_verification_parser(tushare_subparsers)
    _add_tushare_instruments_parser(tushare_subparsers)
    _add_tushare_trade_calendar_parser(tushare_subparsers)
    _add_tushare_date_mirror_parsers(tushare_subparsers)
    _add_etf_history_parsers(tushare_subparsers)
    _add_tushare_index_daily_parser(tushare_subparsers)
    _add_tushare_hotspot_mirror_parsers(tushare_subparsers)
    _add_tushare_minute_backfill_parsers(tushare_subparsers)
    _add_tushare_minute_chunk_parser(tushare_subparsers)
    _add_tushare_fund_portfolio_mirror_parser(tushare_subparsers)
    _add_tushare_holder_mirror_parsers(tushare_subparsers)
    _add_tushare_minute_quota_status_parser(tushare_subparsers)


def _add_minute_quota_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--minute-quota-mode", choices=("off", "observe", "enforce"))
    parser.add_argument("--minute-quota-db")
    parser.add_argument("--minute-quota-consumer")
    parser.add_argument("--minute-quota-limit-rows", type=int)
    parser.add_argument("--minute-quota-safety-rows", type=int)
    parser.add_argument("--minute-quota-gate", choices=("rows", "requests", "dual"))
    parser.add_argument("--minute-quota-limit-requests", type=int)
    parser.add_argument("--minute-quota-burst-limit-requests", type=int)
    parser.add_argument("--minute-quota-safety-requests", type=int)
    parser.add_argument(
        "--minute-quota-allow-burst",
        action=argparse.BooleanOptionalAction,
        default=None,
    )


def _add_tushare_minute_quota_status_parser(
    tushare_subparsers: argparse._SubParsersAction,
) -> None:
    status = tushare_subparsers.add_parser(
        "minute-quota-status",
        help=(
            "Show the token-safe shared minute ledger; expired request leases are "
            "conservatively reconciled to uncertain."
        ),
    )
    status.add_argument("--token-env", default="TUSHARE_TOKEN")
    status.add_argument("--minute-quota-mode", choices=("observe", "enforce"))
    status.add_argument("--minute-quota-db")
    status.add_argument("--minute-quota-limit-rows", type=int)
    status.add_argument("--minute-quota-safety-rows", type=int)
    status.add_argument("--minute-quota-gate", choices=("rows", "requests", "dual"))
    status.add_argument("--minute-quota-limit-requests", type=int)
    status.add_argument("--minute-quota-burst-limit-requests", type=int)
    status.add_argument("--minute-quota-safety-requests", type=int)
    status.add_argument(
        "--minute-quota-allow-burst",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    status.add_argument("--quota-date", help="Asia/Shanghai quota date in YYYYMMDD form.")
    status.add_argument("--json", action="store_true", help="Print the complete JSON status.")


def _add_tushare_token_verification_parser(
    tushare_subparsers: argparse._SubParsersAction,
) -> None:
    verify = tushare_subparsers.add_parser(
        "verify-token",
        help="Verify one or more tokens without printing tokens or account quota data.",
    )
    verify.add_argument(
        "--env",
        dest="env_keys",
        action="append",
        help="Token environment variable; repeat for multiple tokens.",
    )
    verify.add_argument(
        "--api-url",
        help=(
            "Override the TuShare SDK API URL for verification. Defaults to "
            "TUSHARE_API_URL_<suffix> matching each --env, then TUSHARE_API_URL."
        ),
    )
    verify.add_argument(
        "--use-proxy",
        action="store_true",
        help="Allow HTTP(S)/ALL proxy environment variables for TuShare verification.",
    )


def _add_tushare_instruments_parser(tushare_subparsers: argparse._SubParsersAction) -> None:
    instruments = tushare_subparsers.add_parser(
        "export-a-share-instruments",
        help="Export A-share instrument master from stock_basic.",
    )
    instruments.add_argument("--out", required=True)
    instruments.add_argument("--symbols-out")
    instruments.add_argument("--list-status", dest="list_statuses", nargs="+")
    instruments.add_argument("--fields", nargs="+")
    add_token_env_argument(instruments)
    add_provider_runtime_arguments(instruments)


def _add_tushare_trade_calendar_parser(
    tushare_subparsers: argparse._SubParsersAction,
) -> None:
    trade_cal = tushare_subparsers.add_parser(
        "mirror-a-share-trade-cal",
        help="Mirror the A-share trading calendar from trade_cal.",
    )
    trade_cal.add_argument("--out", required=True)
    trade_cal.add_argument("--start-date", required=True)
    trade_cal.add_argument("--end-date", required=True)
    trade_cal.add_argument("--exchange", default="")
    add_token_env_argument(trade_cal)
    add_provider_runtime_arguments(trade_cal)


def _add_tushare_date_mirror_parsers(tushare_subparsers: argparse._SubParsersAction) -> None:
    add_tushare_date_mirror_parser(
        tushare_subparsers,
        command="mirror-a-share-daily",
        description="Mirror unadjusted A-share daily bars, partitioned by trade date.",
    )


def _add_etf_history_parsers(tushare_subparsers: argparse._SubParsersAction) -> None:
    backfill = tushare_subparsers.add_parser(
        "backfill-etf-history", help="Backfill ETF daily bars and adjustment factors by month."
    )
    backfill.add_argument("--artifacts-root", required=True)
    backfill.add_argument("--start-date", required=True)
    backfill.add_argument("--end-date", required=True)
    backfill.add_argument("--dry-run", action="store_true")
    add_token_env_argument(backfill)
    add_provider_runtime_arguments(backfill)

    validate = tushare_subparsers.add_parser(
        "validate-etf-daily-pair", help="Validate ETF daily bars and adjustment-factor pairing."
    )
    validate.add_argument("--daily-dir", required=True)
    validate.add_argument("--adj-factor-dir", required=True)

    build = tushare_subparsers.add_parser(
        "build-etf-daily-forward-adjusted", help="Build forward-adjusted ETF daily bars."
    )
    build.add_argument("--daily-dir", required=True)
    build.add_argument("--adj-factor-dir", required=True)
    build.add_argument("--out-dir", required=True)
    add_tushare_date_mirror_parser(
        tushare_subparsers,
        command="mirror-a-share-adj-factor",
        description="Mirror A-share adjustment factors, partitioned by trade date.",
    )
    add_tushare_date_mirror_parser(
        tushare_subparsers,
        command="mirror-etf-daily",
        description="Mirror exchange-traded fund daily bars from fund_daily by trade date.",
    )
    add_tushare_date_mirror_parser(
        tushare_subparsers,
        command="mirror-etf-adj-factor",
        description="Mirror exchange-traded fund adjustment factors by trade date.",
    )
    add_tushare_date_mirror_parser(
        tushare_subparsers,
        command="mirror-a-share-daily-basic",
        description="Mirror A-share daily valuation metrics, partitioned by trade date.",
    )
    add_tushare_date_mirror_parser(
        tushare_subparsers,
        command="mirror-a-share-limit-status",
        description="Mirror A-share daily limit prices into the limit_status asset.",
    )
    add_tushare_date_mirror_parser(
        tushare_subparsers,
        command="mirror-a-share-moneyflow",
        description="Mirror A-share daily moneyflow, partitioned by trade date.",
    )
    add_tushare_date_mirror_parser(
        tushare_subparsers,
        command="mirror-a-share-moneyflow-dc",
        description="Mirror A-share Eastmoney-style daily moneyflow by trade date.",
    )
    add_tushare_date_mirror_parser(
        tushare_subparsers,
        command="mirror-a-share-moneyflow-hsgt",
        description="Mirror Shanghai/Shenzhen-Hong Kong Connect moneyflow by trade date.",
    )
    add_tushare_date_mirror_parser(
        tushare_subparsers,
        command="mirror-a-share-top-inst",
        description="Mirror A-share top institutional trading events by trade date.",
    )


def _add_tushare_index_daily_parser(tushare_subparsers: argparse._SubParsersAction) -> None:
    index_daily = tushare_subparsers.add_parser(
        "mirror-a-share-index-daily",
        help="Mirror TuShare index_daily bars for one or more index codes.",
    )
    index_daily.add_argument("--out-dir", required=True)
    index_daily.add_argument(
        "--index-code",
        action="append",
        required=True,
        help="Index code such as 000300.SH. Repeat to fetch multiple indices.",
    )
    index_daily.add_argument("--start-date", required=True)
    index_daily.add_argument("--end-date", required=True)
    index_daily.add_argument("--fields", nargs="+")
    index_daily.add_argument(
        "--skip-existing",
        action="store_true",
        help="Allow refreshing an existing mirror output directory.",
    )
    add_token_env_argument(index_daily)
    add_provider_runtime_arguments(index_daily)


def _add_tushare_hotspot_mirror_parsers(
    tushare_subparsers: argparse._SubParsersAction,
) -> None:
    _add_tushare_mins_mirror_parser(tushare_subparsers)
    ths_hot = tushare_subparsers.add_parser(
        "mirror-a-share-ths-hot",
        help="Mirror 同花顺 A 股热榜 by trade date.",
    )
    ths_hot.add_argument("--out-dir", required=True)
    ths_hot.add_argument("--start-date", required=True)
    ths_hot.add_argument("--end-date", required=True)
    ths_hot.add_argument("--fields", nargs="+")
    ths_hot.add_argument("--skip-existing", action="store_true")
    ths_hot.add_argument("--request-interval-seconds", type=float, default=0.0)
    ths_hot.add_argument("--market", default="热股")
    ths_hot.add_argument("--is-new", default="Y")
    add_token_env_argument(ths_hot)
    add_provider_runtime_arguments(ths_hot)

    kpl_list = tushare_subparsers.add_parser(
        "mirror-a-share-kpl-list",
        help="Mirror 开盘啦 A 股涨停/炸板榜单 by trade date.",
    )
    kpl_list.add_argument("--out-dir", required=True)
    kpl_list.add_argument("--start-date", required=True)
    kpl_list.add_argument("--end-date", required=True)
    kpl_list.add_argument("--fields", nargs="+")
    kpl_list.add_argument("--skip-existing", action="store_true")
    kpl_list.add_argument("--request-interval-seconds", type=float, default=0.0)
    kpl_list.add_argument(
        "--tag",
        dest="tags",
        action="append",
        help="Optional 开盘啦 board tag, e.g. 涨停 or 炸板. Repeat to combine tags.",
    )
    add_token_env_argument(kpl_list)
    add_provider_runtime_arguments(kpl_list)

    ths_index = tushare_subparsers.add_parser(
        "mirror-a-share-ths-index",
        help="Mirror 同花顺概念指数目录 as a single-file snapshot.",
    )
    ths_index.add_argument("--out-dir", required=True)
    ths_index.add_argument("--fields", nargs="+")
    ths_index.add_argument("--src", default="THS")
    add_token_env_argument(ths_index)
    add_provider_runtime_arguments(ths_index)

    ths_member = tushare_subparsers.add_parser(
        "mirror-a-share-ths-member",
        help="Mirror 同花顺概念成分 by concept code.",
    )
    ths_member.add_argument("--out-dir", required=True)
    ths_member.add_argument("--fields", nargs="+")
    ths_member.add_argument("--request-interval-seconds", type=float, default=0.1)
    add_token_env_argument(ths_member)
    add_provider_runtime_arguments(ths_member)

    for command, description in (
        ("mirror-a-share-dc-concept", "Mirror 东方财富 A 股题材行情 by trade date."),
        ("mirror-a-share-dc-concept-cons", "Mirror 东方财富 A 股题材成分 by trade date."),
        ("mirror-a-share-kpl-concept-cons", "Mirror 开盘啦 A 股题材成分 by trade date."),
        ("mirror-a-share-limit-step", "Mirror A 股连板天梯 by trade date."),
        ("mirror-a-share-limit-cpt-list", "Mirror A 股涨停最强板块 by trade date."),
        ("mirror-a-share-stk-auction-open", "Mirror A 股开盘集合竞价数据 by trade date."),
        ("mirror-a-share-stk-auction-close", "Mirror A 股收盘集合竞价数据 by trade date."),
        ("mirror-a-share-report-rc", "Mirror 券商盈利预测/研报数据 by calendar event date."),
        ("mirror-a-share-stk-surv", "Mirror A 股机构调研 events by calendar event date."),
        ("mirror-a-share-broker-recommend", "Mirror 券商月度金股 by month."),
        ("mirror-a-share-moneyflow-ths", "Mirror 同花顺 A 股资金流向 by trade date."),
        ("mirror-a-share-limit-list-ths", "Mirror 同花顺 A 股涨跌停明细 by trade date."),
        ("mirror-a-share-margin-detail", "Mirror A 股融资融券交易明细 by trade date."),
        ("mirror-a-share-margin", "Mirror A 股融资融券交易汇总 by trade date."),
        ("mirror-a-share-hsgt-top10", "Mirror 沪深港通十大成交股 by trade date."),
    ):
        parser = tushare_subparsers.add_parser(command, help=description)
        parser.add_argument("--out-dir", required=True)
        parser.add_argument("--start-date", required=True)
        parser.add_argument("--end-date", required=True)
        parser.add_argument("--fields", nargs="+")
        parser.add_argument("--skip-existing", action="store_true")
        parser.add_argument("--request-interval-seconds", type=float, default=0.0)
        add_token_env_argument(parser)
        add_provider_runtime_arguments(parser)


def _add_tushare_mins_mirror_parser(tushare_subparsers: argparse._SubParsersAction) -> None:
    mins = tushare_subparsers.add_parser(
        "mirror-a-share-mins",
        help="Mirror A-share 1-minute OHLCV bars from pro.mins, partitioned by trade date.",
    )
    mins.add_argument("--start-date", required=True)
    mins.add_argument("--end-date", required=True)
    mins.add_argument("--freq", choices=["1min"], default="1min")
    mins.add_argument("--symbols", nargs="+")
    mins.add_argument(
        "--exchange",
        choices=("SH", "SZ", "BJ"),
        help="Filter each date's dynamically resolved traded universe by exchange suffix.",
    )
    mins.add_argument("--out-dir")
    resume = mins.add_mutually_exclusive_group()
    resume.add_argument(
        "--skip-existing",
        dest="skip_existing",
        action="store_true",
        default=True,
        help="Resume complete or partial partitions (default).",
    )
    resume.add_argument(
        "--force",
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Refresh every requested symbol even when a complete partition exists.",
    )
    mins.add_argument("--cooldown-seconds", type=float, default=0.3)
    mins.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Stocks per 1min request (default: 20; validated maximum: 33).",
    )
    mins.add_argument("--gc-frequency", type=int, default=100)
    add_token_env_argument(mins)
    add_provider_runtime_arguments(mins)
    _add_minute_quota_runtime_arguments(mins)


def _add_tushare_minute_backfill_parsers(
    tushare_subparsers: argparse._SubParsersAction,
) -> None:
    plan = tushare_subparsers.add_parser(
        "plan-a-share-minute-backfill",
        help="Create an immutable, offline BJ-only or full-A minute backfill plan.",
    )
    plan.add_argument("--scope", choices=("bj-only", "sh-sz-only", "all-a"), required=True)
    plan.add_argument("--start-date", required=True)
    plan.add_argument("--end-date", required=True)
    plan.add_argument("--trade-cal", required=True)
    plan.add_argument("--instruments", required=True)
    plan.add_argument(
        "--dates-file",
        help="Optional newline/comma text or JSON list of non-contiguous target trade dates.",
    )
    plan.add_argument("--backfill-root", required=True)
    plan.add_argument("--plan", required=True)
    plan.add_argument("--segment", choices=("month", "year"), default="year")
    plan.add_argument(
        "--date-order",
        choices=("ascending", "descending"),
        default="ascending",
        help="Process selected trading dates newest-first when descending.",
    )
    plan.add_argument("--request-budget", type=int)
    plan.add_argument("--max-dates", type=int)
    plan.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Stocks per 1min request (default: 20; validated maximum: 33).",
    )
    plan.add_argument("--cooldown-seconds", type=float, default=1.0)
    plan.add_argument("--workers", type=int, choices=(1,), default=1)
    plan.add_argument("--dry-run", action="store_true")
    add_token_env_argument(plan)
    add_provider_runtime_arguments(plan)

    run = tushare_subparsers.add_parser(
        "run-a-share-minute-backfill",
        help="Run one immutable minute backfill plan sequentially with a resumable receipt.",
    )
    run.add_argument("--plan", required=True)
    run.add_argument("--receipt", required=True)
    run.add_argument("--token-env")
    run.add_argument("--api-url")
    run.add_argument("--provider-no-data-exceptions")
    run.add_argument("--workers", type=int, choices=(1,), default=1)
    run.add_argument("--dry-run", action="store_true")
    _add_minute_quota_runtime_arguments(run)


def _add_tushare_minute_chunk_parser(
    tushare_subparsers: argparse._SubParsersAction,
) -> None:
    chunk = tushare_subparsers.add_parser(
        "run-a-share-minute-full-day-chunk",
        help="Download the next bounded chunk of missing dates from a full-day fusion plan.",
    )
    chunk.add_argument("--plan", required=True)
    chunk.add_argument("--full-day-dir", required=True)
    chunk.add_argument("--receipt-dir", required=True)
    chunk.add_argument("--max-dates", type=int, default=3)
    chunk.add_argument("--cooldown-seconds", type=float, default=1.0)
    chunk.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Stocks per 1min request (default: 20; validated maximum: 33).",
    )
    chunk.add_argument("--gc-frequency", type=int, default=100)
    chunk.add_argument("--dry-run", action="store_true")
    add_token_env_argument(chunk)
    add_provider_runtime_arguments(chunk)


def _add_tushare_fund_portfolio_mirror_parser(
    tushare_subparsers: argparse._SubParsersAction,
) -> None:
    fund_portfolio = tushare_subparsers.add_parser(
        "mirror-a-share-fund-portfolio",
        help="Mirror A-share public fund stock holdings, partitioned by report period.",
    )
    fund_portfolio.add_argument("--out-dir", required=True)
    fund_portfolio.add_argument("--start-date", required=True)
    fund_portfolio.add_argument("--end-date", required=True)
    fund_portfolio.add_argument("--fields", nargs="+")
    fund_portfolio.add_argument("--skip-existing", action="store_true")
    fund_portfolio.add_argument("--page-size", type=int, default=8000)
    fund_portfolio.add_argument("--max-pages-per-period", type=int, default=1000)
    add_token_env_argument(fund_portfolio)
    add_provider_runtime_arguments(fund_portfolio)


def _add_tushare_holder_mirror_parsers(tushare_subparsers: argparse._SubParsersAction) -> None:
    for command, description in (
        (
            "mirror-a-share-top10-holders",
            "Mirror A-share top-10 shareholder data by stock symbol.",
        ),
        (
            "mirror-a-share-top10-floatholders",
            "Mirror A-share top-10 floating shareholder data by stock symbol.",
        ),
        (
            "mirror-a-share-stk-holdertrade",
            "Mirror A-share shareholder increase/decrease events by stock symbol.",
        ),
    ):
        holders = tushare_subparsers.add_parser(command, help=description)
        holders.add_argument("--out-dir", required=True)
        holders.add_argument("--start-date", required=True)
        holders.add_argument("--end-date", required=True)
        holders.add_argument("--symbol", dest="symbols", action="append")
        holders.add_argument("--symbols-file")
        holders.add_argument("--fields", nargs="+")
        holders.add_argument("--skip-existing", action="store_true")
        holders.add_argument("--request-interval-seconds", type=float, default=0.0)
        add_token_env_argument(holders)
        add_provider_runtime_arguments(holders)


__all__ = ["add_tushare_core_mirror_parsers"]
