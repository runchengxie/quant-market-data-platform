from __future__ import annotations

import argparse

from .cli_tushare_common import (
    build_tushare_a_share_universe_from_args,
    print_status_summary,
    print_tushare_summary,
)


def _handle_tushare_daily_and_universe(args: argparse.Namespace) -> int | None:
    if args.tushare_command == "build-a-share-daily-clean":
        from market_data_platform.standardize.tushare.a_share_daily import (
            build_a_share_daily_clean as build_tushare_a_share_daily_clean,
        )

        summary = build_tushare_a_share_daily_clean(
            daily_dir=args.daily_dir,
            adj_factor_dir=args.adj_factor_dir,
            daily_basic_dir=args.daily_basic_dir,
            limit_status_dir=args.limit_status_dir,
            suspend_dir=args.suspend_dir,
            instruments_file=args.instruments_file,
            out_dir=args.out_dir,
            min_rows=args.min_rows,
            min_symbols=args.min_symbols,
            batch_trade_dates=args.batch_trade_dates,
            memory_soft_limit_mb=args.memory_soft_limit_mb,
            memory_hard_limit_mb=args.memory_hard_limit_mb,
        )
        return print_tushare_summary(summary)
    if args.tushare_command == "validate-a-share-daily-clean":
        return _handle_tushare_daily_clean_validation(args)
    if args.tushare_command == "build-a-share-universe":
        return print_tushare_summary(build_tushare_a_share_universe_from_args(args))
    if args.tushare_command == "validate-a-share-universe":
        from market_data_platform.providers.tushare_a_share_universe import (
            validate_a_share_universe as validate_tushare_a_share_universe,
        )

        summary = validate_tushare_a_share_universe(
            by_date_file=args.by_date_file,
            latest_symbols_file=args.latest_symbols_file,
            meta_file=args.meta_file,
            expected_as_of=args.expected_as_of,
            min_rows=args.min_rows,
            min_symbols=args.min_symbols,
            min_rebalance_dates=args.min_rebalance_dates,
            out=args.out,
        )
        return print_status_summary(summary)
    return None


def _handle_tushare_daily_clean_validation(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_clean import (
        validate_a_share_daily_clean as validate_tushare_a_share_daily_clean,
    )

    summary = validate_tushare_a_share_daily_clean(
        daily_clean_dir=args.daily_clean_dir,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
        require_valuation=args.require_valuation,
        require_limit_status=args.require_limit_status,
        profile=args.profile,
        trade_cal_file=args.trade_cal_file,
        fail_on_severity=args.fail_on_severity,
        max_warning_rate=args.max_warning_rate,
        pct_chg_tolerance=args.pct_chg_tolerance,
        batch_rows=args.batch_rows,
        memory_soft_limit_mb=args.memory_soft_limit_mb,
        memory_hard_limit_mb=args.memory_hard_limit_mb,
        out=args.out,
    )
    return print_status_summary(summary)


__all__ = ["_handle_tushare_daily_and_universe"]
