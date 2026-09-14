"""CLI handlers for historical trading-constraint reference assets."""

from __future__ import annotations

import argparse

from .cli_tushare_common import print_status_summary, print_tushare_summary


def _handle_tushare_constraints(args: argparse.Namespace) -> int | None:
    if args.tushare_command == "download-a-share-constraint-reference":
        return _handle_download(args)
    if args.tushare_command == "build-a-share-st-history":
        return _handle_st_build(args)
    if args.tushare_command == "publish-a-share-constraint-reference":
        return _handle_publish(args)
    return None


def _handle_download(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_constraints import (
        ConstraintDownloadOptions,
        download_constraint_reference,
    )

    summary = download_constraint_reference(
        ConstraintDownloadOptions(
            dataset=args.dataset,
            out_dir=args.out_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            token_env=args.token_env,
            api_url=args.api_url,
            page_size=args.page_size,
            max_pages=args.max_pages,
            request_interval_seconds=args.request_interval_seconds,
            retries=args.retries,
        )
    )
    return print_tushare_summary(summary)


def _handle_st_build(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_constraints import (
        ReconstructedSTOptions,
        build_reconstructed_st_history,
    )

    summary = build_reconstructed_st_history(
        ReconstructedSTOptions(
            namechange_path=args.namechange,
            trade_cal_path=args.trade_cal,
            instruments_path=args.instruments,
            stock_st_path=args.stock_st,
            out_dir=args.out_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            min_precision=args.min_precision,
            min_recall=args.min_recall,
        )
    )
    return print_status_summary(summary)


def _handle_publish(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_constraints import (
        publish_constraint_assets,
    )

    summary = publish_constraint_assets(
        args.artifacts_root,
        args.source_dir,
        args.target_date,
        allow_partial=args.allow_partial,
    )
    return print_tushare_summary(summary)


__all__ = ["_handle_tushare_constraints"]
