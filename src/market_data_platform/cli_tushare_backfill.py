from __future__ import annotations

import argparse

from .cli_tushare_common import (
    print_tushare_summary,
    run_tushare_a_share_backfill_from_args,
)


def _handle_tushare_backfill_or_refresh(args: argparse.Namespace) -> int | None:
    if args.tushare_command == "backfill-a-share-history":
        return print_tushare_summary(run_tushare_a_share_backfill_from_args(args))
    if args.tushare_command == "plan-a-share-current-refresh":
        from market_data_platform.tushare_refresh import build_a_share_current_refresh_plan

        summary = build_a_share_current_refresh_plan(
            artifacts_root=args.artifacts_root,
            start_date=args.start_date,
            end_date=args.end_date,
            datasets=args.datasets,
            segment=args.segment,
        )
        return print_tushare_summary(summary)
    if args.tushare_command == "promote-a-share-current":
        from market_data_platform.tushare_refresh import run_a_share_current_promotion

        summary = run_a_share_current_promotion(
            artifacts_root=args.artifacts_root,
            start_date=args.start_date,
            end_date=args.end_date,
            segment=args.segment,
            daily_dir=args.daily_dir,
            adj_factor_dir=args.adj_factor_dir,
            daily_basic_dir=args.daily_basic_dir,
            limit_status_dir=args.limit_status_dir,
            daily_clean_dir=args.daily_clean_dir,
            universe_by_date=args.universe_by_date,
            universe_symbols=args.universe_symbols,
            universe_meta=args.universe_meta,
            daily_clean_baseline_report=args.daily_clean_baseline_report,
            daily_clean_research_report=args.daily_clean_research_report,
            universe_validation_report=args.universe_validation_report,
            current_health_report=args.current_health_report,
            evidence_out=args.evidence_out,
            apply=args.apply,
            fail_on_severity=args.fail_on_severity,
            required_assets=args.required_assets,
        )
        return print_tushare_summary(summary)
    return None


__all__ = ["_handle_tushare_backfill_or_refresh"]
