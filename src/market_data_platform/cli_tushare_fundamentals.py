from __future__ import annotations

import argparse
from collections.abc import Callable

from .cli_tushare_common import (
    print_status_summary,
    print_tushare_summary,
    symbols_from_args,
)


def _handle_tushare_fundamentals_raw(args: argparse.Namespace) -> int | None:
    handlers = _tushare_fundamentals_raw_handlers()
    handler = handlers.get(args.tushare_command)
    return handler(args) if handler is not None else None


def _tushare_fundamentals_raw_handlers() -> dict[str, Callable[[argparse.Namespace], int]]:
    return {
        "list-a-share-fundamentals-specs": _handle_tushare_fundamentals_specs,
        "plan-a-share-fundamentals": _handle_tushare_fundamentals_plan,
        "download-a-share-fundamentals": _handle_tushare_fundamentals_download,
        "check-a-share-fundamentals-state": _handle_tushare_fundamentals_state,
        "list-a-share-fundamentals-failures": _handle_tushare_fundamentals_failures,
        "compact-a-share-fundamentals-raw": _handle_tushare_fundamentals_compact,
        "normalize-a-share-fundamentals": _handle_tushare_fundamentals_normalize,
        "validate-a-share-normalized-fundamentals": (
            _handle_tushare_fundamentals_validate_normalized
        ),
        "build-a-share-announcement-event-pit": _handle_tushare_announcement_event_pit,
    }


def _handle_tushare_fundamentals_specs(_args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_fundamentals import (
        dataset_specs_payload as tushare_a_share_fundamentals_specs_payload,
    )

    return print_tushare_summary(tushare_a_share_fundamentals_specs_payload())


def _handle_tushare_fundamentals_plan(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_fundamentals import (
        build_download_plan as build_tushare_a_share_fundamentals_download_plan,
    )

    summary = build_tushare_a_share_fundamentals_download_plan(
        datasets=args.datasets,
        start_date=args.start_date,
        end_date=args.end_date,
        entitlement_mode=args.entitlement_mode,
        symbols=symbols_from_args(args),
    )
    return print_tushare_summary(summary)


def _handle_tushare_fundamentals_state(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_fundamentals import (
        read_download_state as read_tushare_a_share_fundamentals_state,
    )

    return print_tushare_summary(read_tushare_a_share_fundamentals_state(args.state_file))


def _handle_tushare_fundamentals_failures(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_fundamentals import (
        read_failure_report as read_tushare_a_share_fundamentals_failures,
    )

    return print_tushare_summary(read_tushare_a_share_fundamentals_failures(args.failure_file))


def _handle_tushare_fundamentals_compact(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_fundamentals import (
        compact_raw_fundamentals as compact_tushare_a_share_raw_fundamentals,
    )

    summary = compact_tushare_a_share_raw_fundamentals(
        raw_dir=args.raw_dir,
        out_dir=args.out_dir,
    )
    return print_tushare_summary(summary)


def _handle_tushare_fundamentals_normalize(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_fundamentals import (
        build_normalized_fundamentals as build_tushare_a_share_normalized_fundamentals,
    )

    summary = build_tushare_a_share_normalized_fundamentals(
        dataset=args.dataset,
        raw_dir=args.raw_dir,
        out_dir=args.out_dir,
    )
    return print_tushare_summary(summary)


def _handle_tushare_fundamentals_validate_normalized(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_fundamentals import (
        validate_normalized_fundamentals as validate_tushare_a_share_normalized_fundamentals,
    )

    return print_status_summary(
        validate_tushare_a_share_normalized_fundamentals(
            asset_dir=args.asset_dir,
            target_date=args.target_date,
        )
    )


def _handle_tushare_announcement_event_pit(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_fundamentals import (
        AnnouncementEventPitOptions,
        build_announcement_event_pit,
    )

    summary = build_announcement_event_pit(
        AnnouncementEventPitOptions(
            raw_dir=args.raw_dir,
            out_dir=args.out_dir,
            dataset=args.dataset,
            value_columns=tuple(args.value_columns) or None,
            include_report_types=tuple(args.report_types) or None,
        )
    )
    return print_tushare_summary(summary)


def _handle_tushare_fundamentals_download(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_fundamentals import (
        RawFundamentalsDownloadOptions,
    )
    from market_data_platform.providers.tushare_a_share_fundamentals import (
        download_raw_fundamentals as download_tushare_a_share_raw_fundamentals,
    )

    summary = download_tushare_a_share_raw_fundamentals(
        RawFundamentalsDownloadOptions(
            dataset=args.dataset,
            out_dir=args.out_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            entitlement_mode=args.entitlement_mode,
            symbols=symbols_from_args(args),
            token_env=args.token_env,
            api_url=args.api_url,
            run_id=args.run_id,
            retry_attempts=args.retry_attempts,
            retry_backoff_seconds=args.retry_backoff_seconds,
            request_interval_seconds=args.request_interval_seconds,
            page_size=args.page_size,
            max_pages=args.max_pages,
            stale_after_days=args.stale_after_days,
        )
    )
    return print_tushare_summary(summary)


def _handle_tushare_fundamentals_pit(args: argparse.Namespace) -> int | None:
    if args.tushare_command == "build-a-share-fundamentals-pit":
        from market_data_platform.providers.tushare_a_share_fundamentals import (
            PitBuildOptions,
        )
        from market_data_platform.providers.tushare_a_share_fundamentals import (
            build_pit_fundamentals as build_tushare_a_share_fundamentals_pit,
        )
        from market_data_platform.runtime_memory import MemoryPolicy

        summary = build_tushare_a_share_fundamentals_pit(
            PitBuildOptions(
                normalized_dirs=args.normalized_dirs,
                out_dir=args.out_dir,
                field_mappings=args.field_mappings,
                available_delay_days=args.available_delay_days,
                max_observation_age_days=args.max_observation_age_days,
                bucket_count=args.bucket_count,
                batch_rows=args.batch_rows,
                memory_policy=MemoryPolicy(
                    soft_available_mb=args.memory_soft_limit_mb,
                    hard_available_mb=args.memory_hard_limit_mb,
                ),
            )
        )
        return print_tushare_summary(summary)
    if args.tushare_command == "validate-a-share-fundamentals-pit":
        from market_data_platform.providers.tushare_a_share_fundamentals import (
            validate_pit_fundamentals as validate_tushare_a_share_fundamentals_pit,
        )
        from market_data_platform.runtime_memory import MemoryPolicy

        summary = validate_tushare_a_share_fundamentals_pit(
            asset_dir=args.asset_dir,
            target_date=args.target_date,
            batch_rows=args.batch_rows,
            memory_policy=MemoryPolicy(
                soft_available_mb=args.memory_soft_limit_mb,
                hard_available_mb=args.memory_hard_limit_mb,
            ),
        )
        return print_status_summary(summary)
    if args.tushare_command == "publish-a-share-fundamentals":
        from market_data_platform.providers.tushare_a_share_fundamentals import (
            publish_fundamentals_assets as publish_tushare_a_share_fundamentals_assets,
        )

        summary = publish_tushare_a_share_fundamentals_assets(
            artifacts_root=args.artifacts_root,
            normalized_dirs=args.normalized_dirs,
            pit_dir=args.pit_dir,
            target_date=args.target_date,
        )
        return print_tushare_summary(summary)
    if args.tushare_command == "publish-a-share-pit-fundamentals":
        from market_data_platform.providers.tushare_a_share_fundamentals import (
            publish_pit_fundamentals_asset as publish_tushare_a_share_pit_fundamentals_asset,
        )

        summary = publish_tushare_a_share_pit_fundamentals_asset(
            artifacts_root=args.artifacts_root,
            pit_dir=args.pit_dir,
            target_date=args.target_date,
        )
        return print_tushare_summary(summary)
    return None


__all__ = ["_handle_tushare_fundamentals_raw", "_handle_tushare_fundamentals_pit"]
