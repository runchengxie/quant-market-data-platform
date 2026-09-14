from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from market_data_platform.providers.tushare_common import TushareRequestPolicy


def build_tushare_a_share_universe_from_args(args: argparse.Namespace) -> dict[str, Any]:
    from market_data_platform.providers.tushare_a_share_universe import (
        AShareUniverseBuildOptions,
    )
    from market_data_platform.providers.tushare_a_share_universe import (
        build_a_share_universe as build_tushare_a_share_universe,
    )

    return build_tushare_a_share_universe(
        AShareUniverseBuildOptions(
            artifacts_root=args.artifacts_root,
            daily_clean_dir=args.daily_clean_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            rebalance_frequency=args.rebalance_frequency,
            lookback_days=args.lookback_days,
            min_window_days=args.min_window_days,
            top_quantile=args.top_quantile,
            min_turnover=args.min_turnover,
            out=args.out,
            latest_out=args.latest_out,
            meta_out=args.meta_out,
            min_rows=args.min_rows,
            min_symbols=args.min_symbols,
            min_rebalance_dates=args.min_rebalance_dates,
            force=args.force,
        )
    )


def tushare_request_policy_from_args(args: argparse.Namespace) -> TushareRequestPolicy:
    from market_data_platform.providers.tushare_common import request_policy

    return request_policy(
        disable_proxy=not args.use_proxy,
        request_attempts=args.retry_attempts,
        retry_sleep_seconds=args.retry_sleep_seconds,
        retry_max_sleep_seconds=args.retry_max_sleep_seconds,
        quota_cooldown_seconds=args.quota_cooldown_seconds,
    )


def run_tushare_a_share_backfill_from_args(args: argparse.Namespace) -> dict[str, Any]:
    from market_data_platform.tushare_backfill import (
        AShareHistoryBackfillOptions,
        run_a_share_history_backfill,
    )

    return run_a_share_history_backfill(
        AShareHistoryBackfillOptions(
            artifacts_root=args.artifacts_root,
            start_date=args.start_date,
            end_date=args.end_date,
            datasets=args.datasets,
            segment=args.segment,
            skip_existing=not args.no_skip_existing,
            sync_latest=args.sync_latest,
            dry_run=args.dry_run,
            continue_on_error=args.continue_on_error,
            token_env=args.token_env,
            api_url=args.api_url,
            request_policy=_tushare_request_policy_from_args(args),
        )
    )


def symbols_from_args(args: argparse.Namespace) -> list[str]:
    symbols = list(args.symbols or [])
    if args.symbols_file:
        path = Path(args.symbols_file).expanduser().resolve()
        symbols.extend(
            line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    return symbols


def print_tushare_summary(summary: Any) -> int:
    import json

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def print_status_summary(summary: dict[str, Any]) -> int:
    import json

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


def _tushare_request_policy_from_args(args: argparse.Namespace) -> TushareRequestPolicy:
    return tushare_request_policy_from_args(args)
