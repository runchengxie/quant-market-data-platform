"""CLI handlers for the reference TuShare A 股 datasets.

Mirrors ``cli_tushare_fundamentals.py``: a thin routing layer that lazily imports provider
functions and prints JSON summaries via :func:`cli_tushare_common.print_tushare_summary`.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from .cli_tushare_common import print_tushare_summary


def _handle_tushare_reference_raw(args: argparse.Namespace) -> int | None:
    handlers = _tushare_reference_raw_handlers()
    handler = handlers.get(args.tushare_command)
    return handler(args) if handler is not None else None


def _tushare_reference_raw_handlers() -> dict[str, Callable[[argparse.Namespace], int]]:
    return {
        "list-a-share-reference-specs": _handle_tushare_reference_specs,
        "download-a-share-reference": _handle_tushare_reference_download,
        "normalize-a-share-reference": _handle_tushare_reference_normalize,
        "publish-a-share-reference": _handle_tushare_reference_publish,
    }


def _handle_tushare_reference_specs(_args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_reference import (
        dataset_specs_payload,
    )

    return print_tushare_summary(dataset_specs_payload())


def _handle_tushare_reference_download(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_reference import (
        RawReferenceDownloadOptions,
        download_raw_reference,
    )

    summary = download_raw_reference(
        RawReferenceDownloadOptions(
            dataset=args.dataset,
            out_dir=args.out_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            index_code=getattr(args, "index_code", None),
            exchange=getattr(args, "exchange", None),
            token_env=getattr(args, "token_env", "TUSHARE_TOKEN"),
            api_url=getattr(args, "api_url", None),
            run_id=getattr(args, "run_id", None),
            request_interval_seconds=args.request_interval_seconds,
            retries=args.retries,
        )
    )
    return print_tushare_summary(summary)


def _handle_tushare_reference_normalize(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_reference import (
        build_normalized_index_weight_daily,
    )

    summary = build_normalized_index_weight_daily(
        raw_dir=args.raw_dir,
        out_dir=args.out_dir,
        trade_cal_path=args.trade_cal,
        end_date=args.end_date,
    )
    return print_tushare_summary(summary)


def _handle_tushare_reference_publish(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_reference import (
        publish_reference_assets,
    )

    summary = publish_reference_assets(
        artifacts_root=args.artifacts_root,
        raw_dir=args.raw_dir,
        target_date=args.target_date,
        allow_partial=args.allow_partial,
    )
    return print_tushare_summary(summary)


__all__ = ["_handle_tushare_reference_raw"]
