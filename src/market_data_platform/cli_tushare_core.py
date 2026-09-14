from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable

from .cli_tushare_common import (
    print_tushare_summary,
    symbols_from_args,
    tushare_request_policy_from_args,
)


def _handle_tushare_core(args: argparse.Namespace) -> int | None:
    command_handlers = {
        "verify-token": _handle_verify_token,
        "export-a-share-instruments": _handle_export_a_share_instruments,
        "mirror-a-share-trade-cal": _handle_trade_cal,
        "mirror-a-share-ths-index": _handle_ths_index,
        "mirror-a-share-index-daily": _handle_index_daily,
        "mirror-a-share-ths-member": _handle_ths_member,
        "mirror-a-share-mins": _handle_mirror_mins,
        "minute-quota-status": _handle_minute_quota_status,
        "plan-a-share-minute-backfill": _handle_plan_minute_backfill,
        "run-a-share-minute-backfill": _handle_run_minute_backfill,
        "run-a-share-minute-full-day-chunk": _handle_run_minute_full_day_chunk,
        "backfill-etf-history": _handle_etf_history_backfill,
        "validate-etf-daily-pair": _handle_etf_pair_validation,
        "build-etf-daily-forward-adjusted": _handle_etf_forward_adjusted,
    }
    command_handler = command_handlers.get(args.tushare_command)
    if command_handler is not None:
        return command_handler(args)
    return _handle_date_mirror(args)


def _handle_verify_token(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share import verify_tushare_tokens

    summary = verify_tushare_tokens(
        env_keys=args.env_keys,
        api_url=args.api_url,
        disable_proxy=not args.use_proxy,
    )
    print_summary(summary)
    return 0 if summary["valid_tokens"] else 1


def _handle_export_a_share_instruments(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share import (
        export_a_share_instruments as export_tushare_a_share_instruments,
    )

    summary = export_tushare_a_share_instruments(
        out=args.out,
        list_statuses=args.list_statuses,
        fields=args.fields,
        symbols_out=args.symbols_out,
        token_env=args.token_env,
        api_url=args.api_url,
        request_policy=tushare_request_policy_from_args(args),
    )
    return print_tushare_summary(summary)


def _handle_trade_cal(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_trade_cal as mirror_tushare_a_share_trade_cal,
    )

    summary = mirror_tushare_a_share_trade_cal(
        out=args.out,
        start_date=args.start_date,
        end_date=args.end_date,
        exchange=args.exchange,
        token_env=args.token_env,
        api_url=args.api_url,
        request_policy=tushare_request_policy_from_args(args),
    )
    return print_tushare_summary(summary)


def _handle_etf_history_backfill(args: argparse.Namespace) -> int:
    from market_data_platform.tushare_etf_history import backfill_etf_history

    summary = backfill_etf_history(
        artifacts_root=args.artifacts_root,
        start_date=args.start_date,
        end_date=args.end_date,
        token_env=args.token_env,
        api_url=args.api_url,
        request_policy=tushare_request_policy_from_args(args),
        dry_run=args.dry_run,
    )
    return print_tushare_summary(summary)


def _handle_etf_pair_validation(args: argparse.Namespace) -> int:
    from market_data_platform.tushare_etf_history import validate_etf_daily_pair

    return print_tushare_summary(
        validate_etf_daily_pair(daily_dir=args.daily_dir, adj_factor_dir=args.adj_factor_dir)
    )


def _handle_etf_forward_adjusted(args: argparse.Namespace) -> int:
    from market_data_platform.tushare_etf_history import build_etf_daily_forward_adjusted

    return print_tushare_summary(
        build_etf_daily_forward_adjusted(
            daily_dir=args.daily_dir, adj_factor_dir=args.adj_factor_dir, out_dir=args.out_dir
        )
    )


def _handle_ths_index(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_ths_index as mirror_tushare_a_share_ths_index,
    )

    summary = mirror_tushare_a_share_ths_index(
        out_dir=args.out_dir,
        fields=args.fields,
        src=args.src,
        token_env=args.token_env,
        api_url=args.api_url,
        request_policy=tushare_request_policy_from_args(args),
    )
    return print_tushare_summary(summary)


def _handle_index_daily(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_index_daily as mirror_tushare_a_share_index_daily,
    )

    summary = mirror_tushare_a_share_index_daily(
        out_dir=args.out_dir,
        index_codes=tuple(args.index_code),
        start_date=args.start_date,
        end_date=args.end_date,
        fields=args.fields,
        skip_existing=args.skip_existing,
        token_env=args.token_env,
        api_url=args.api_url,
        request_policy=tushare_request_policy_from_args(args),
    )
    return print_tushare_summary(summary)


def _handle_ths_member(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_ths_member as mirror_tushare_a_share_ths_member,
    )
    from market_data_platform.providers.tushare_a_share_ths_member import (
        ThsMemberMirrorOptions,
    )

    summary = mirror_tushare_a_share_ths_member(
        ThsMemberMirrorOptions(
            out_dir=args.out_dir,
            fields=args.fields,
            token_env=args.token_env,
            api_url=args.api_url,
            request_policy=tushare_request_policy_from_args(args),
            request_interval_seconds=getattr(args, "request_interval_seconds", 0.1),
        )
    )
    return print_tushare_summary(summary)


def _handle_mirror_mins(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share import _load_tushare_env_files
    from market_data_platform.providers.tushare_a_share_mins import (
        MinsMirrorOptions,
        mirror_minute_bars,
    )

    from .cli_tushare_common import tushare_request_policy_from_args

    token_env = getattr(args, "token_env", None) or "TUSHARE_TOKEN"
    _load_tushare_env_files()
    token_value = os.environ.get(token_env, "")
    if not token_value:
        print(f"Token not found in env var {token_env}", file=sys.stderr)
        return 1

    symbols = (
        [
            symbol.strip()
            for value in args.symbols
            for symbol in str(value).split(",")
            if symbol.strip()
        ]
        if args.symbols
        else None
    )
    options = MinsMirrorOptions(
        start_date=args.start_date,
        end_date=args.end_date,
        freq=args.freq,
        symbols=symbols,
        output_dir=args.out_dir,
        token_env=token_env,
        api_url=getattr(args, "api_url", None),
        request_policy=tushare_request_policy_from_args(args),
        skip_existing=args.skip_existing,
        batch_size=args.batch_size,
        cooldown_seconds=args.cooldown_seconds,
        gc_frequency=args.gc_frequency,
        exchange=args.exchange,
        minute_quota_mode=args.minute_quota_mode,
        minute_quota_db=args.minute_quota_db,
        minute_quota_consumer=args.minute_quota_consumer,
        minute_quota_limit_rows=args.minute_quota_limit_rows,
        minute_quota_safety_rows=args.minute_quota_safety_rows,
        minute_quota_gate=args.minute_quota_gate,
        minute_quota_limit_requests=args.minute_quota_limit_requests,
        minute_quota_burst_limit_requests=args.minute_quota_burst_limit_requests,
        minute_quota_safety_requests=args.minute_quota_safety_requests,
        minute_quota_allow_burst=args.minute_quota_allow_burst,
    )
    result = mirror_minute_bars(options)
    print_summary(result)
    return 0


def _handle_minute_quota_status(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share import _load_tushare_env_files
    from market_data_platform.tushare_minute_quota import (
        MINUTE_QUOTA_MODE_ENV,
        MinuteQuotaLedger,
        MinuteQuotaRequestOverrides,
    )

    _load_tushare_env_files()
    configured_mode = args.minute_quota_mode or os.environ.get(MINUTE_QUOTA_MODE_ENV)
    if configured_mode in {None, "", "off"}:
        configured_mode = "observe"
    ledger = MinuteQuotaLedger.from_env(
        token_env=args.token_env,
        mode=configured_mode,
        database_path=args.minute_quota_db,
        consumer="status",
        limit_rows=args.minute_quota_limit_rows,
        safety_rows=args.minute_quota_safety_rows,
        request_overrides=MinuteQuotaRequestOverrides(
            gate=args.minute_quota_gate,
            limit_requests=args.minute_quota_limit_requests,
            burst_limit_requests=args.minute_quota_burst_limit_requests,
            safety_requests=args.minute_quota_safety_requests,
            allow_burst=args.minute_quota_allow_burst,
        ),
    )
    assert ledger is not None
    status = ledger.status(quota_date=args.quota_date)
    if args.json:
        print_summary(status)
    else:
        print(
            f"quota_date={status['quota_date']} token={status['token_fingerprint']} "
            f"requests={status['charged_requests']}/"
            f"{status['effective_limit_requests']} "
            f"request_holds={status['hold_requests']} "
            f"rows={status['charged_rows']} row_holds={status['hold_rows']} "
            f"available_requests={status['available_requests']} "
            f"available_rows={status['available_rows']} "
            f"closed={status['closed_reason'] or '-'}"
        )
    return 0


def _handle_plan_minute_backfill(args: argparse.Namespace) -> int:
    from market_data_platform.tushare_minute_backfill import (
        MinuteBackfillPlanOptions,
        build_minute_backfill_plan,
        summarize_minute_backfill_artifact,
    )

    result = build_minute_backfill_plan(
        MinuteBackfillPlanOptions(
            start_date=args.start_date,
            end_date=args.end_date,
            scope=args.scope,
            trade_cal_path=args.trade_cal,
            instruments_path=args.instruments,
            dates_path=args.dates_file,
            backfill_root=args.backfill_root,
            plan_path=args.plan,
            segment=args.segment,
            date_order=args.date_order,
            batch_size=args.batch_size,
            cooldown_seconds=args.cooldown_seconds,
            request_budget=args.request_budget,
            max_dates=args.max_dates,
            token_env=args.token_env,
            api_url=args.api_url,
            request_policy=tushare_request_policy_from_args(args),
            workers=args.workers,
            dry_run=args.dry_run,
        )
    )
    print_summary(summarize_minute_backfill_artifact(result))
    return 0


def _handle_run_minute_backfill(args: argparse.Namespace) -> int:
    from market_data_platform.tushare_minute_backfill import (
        MinuteBackfillRunOptions,
        run_minute_backfill,
        summarize_minute_backfill_artifact,
    )

    result = run_minute_backfill(
        MinuteBackfillRunOptions(
            plan_path=args.plan,
            receipt_path=args.receipt,
            token_env=args.token_env,
            api_url=args.api_url,
            provider_no_data_exceptions_path=args.provider_no_data_exceptions,
            dry_run=args.dry_run,
            workers=args.workers,
            minute_quota_mode=args.minute_quota_mode,
            minute_quota_db=args.minute_quota_db,
            minute_quota_consumer=args.minute_quota_consumer,
            minute_quota_limit_rows=args.minute_quota_limit_rows,
            minute_quota_safety_rows=args.minute_quota_safety_rows,
            minute_quota_gate=args.minute_quota_gate,
            minute_quota_limit_requests=args.minute_quota_limit_requests,
            minute_quota_burst_limit_requests=args.minute_quota_burst_limit_requests,
            minute_quota_safety_requests=args.minute_quota_safety_requests,
            minute_quota_allow_burst=args.minute_quota_allow_burst,
        )
    )
    print_summary(summarize_minute_backfill_artifact(result))
    return 0 if result["status"] in {"complete", "dry_run"} else 1


def _handle_run_minute_full_day_chunk(args: argparse.Namespace) -> int:
    from market_data_platform.tushare_minute_chunk import (
        MinuteFullDayChunkOptions,
        run_minute_full_day_chunk,
        summarize_minute_full_day_chunk,
    )

    result = run_minute_full_day_chunk(
        MinuteFullDayChunkOptions(
            plan_path=args.plan,
            source_root=args.full_day_dir,
            receipt_dir=args.receipt_dir,
            max_dates=args.max_dates,
            token_env=args.token_env,
            api_url=args.api_url,
            request_policy=tushare_request_policy_from_args(args),
            cooldown_seconds=args.cooldown_seconds,
            batch_size=args.batch_size,
            gc_frequency=args.gc_frequency,
            dry_run=args.dry_run,
        )
    )
    print_summary(summarize_minute_full_day_chunk(result))
    return 0 if result["status"] in {"complete", "dry_run", "noop"} else 1


def _handle_date_mirror(args: argparse.Namespace) -> int | None:
    mirror_commands = _tushare_date_mirror_commands()
    handler = mirror_commands.get(args.tushare_command)
    if handler is None:
        return None
    mirror_kwargs = _date_mirror_kwargs(args)
    query_options = _date_mirror_query_options(args)
    if query_options:
        mirror_kwargs["query_options"] = query_options
    summary = handler(**mirror_kwargs)
    return print_tushare_summary(summary)


def _date_mirror_kwargs(args: argparse.Namespace) -> dict[str, object]:
    mirror_kwargs: dict[str, object] = {
        "out_dir": args.out_dir,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "fields": args.fields,
        "skip_existing": args.skip_existing,
        "token_env": args.token_env,
        "api_url": args.api_url,
        "request_policy": tushare_request_policy_from_args(args),
    }
    if hasattr(args, "request_interval_seconds"):
        mirror_kwargs["request_interval_seconds"] = args.request_interval_seconds
    if args.tushare_command == "mirror-a-share-fund-portfolio":
        mirror_kwargs["page_size"] = args.page_size
        mirror_kwargs["max_pages_per_period"] = args.max_pages_per_period
    if args.tushare_command in {
        "mirror-a-share-top10-holders",
        "mirror-a-share-top10-floatholders",
        "mirror-a-share-stk-holdertrade",
    }:
        mirror_kwargs["symbols"] = symbols_from_args(args)
    return mirror_kwargs


def _date_mirror_query_options(args: argparse.Namespace) -> dict[str, object]:
    query_options: dict[str, object] = {}
    if hasattr(args, "market") and args.market:
        query_options["market"] = args.market
    if hasattr(args, "is_new") and args.is_new:
        query_options["is_new"] = args.is_new
    if hasattr(args, "tags") and args.tags:
        query_options["tag"] = args.tags
    return query_options


def _tushare_date_mirror_commands() -> dict[str, Callable[..., object]]:
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_adj_factor as mirror_tushare_a_share_adj_factor,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_broker_recommend as mirror_tushare_a_share_broker_recommend,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_daily as mirror_tushare_a_share_daily,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_daily_basic as mirror_tushare_a_share_daily_basic,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_dc_concept as mirror_tushare_a_share_dc_concept,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_dc_concept_cons as mirror_tushare_a_share_dc_concept_cons,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_fund_portfolio as mirror_tushare_a_share_fund_portfolio,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_hsgt_top10 as mirror_tushare_a_share_hsgt_top10,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_kpl_concept_cons as mirror_tushare_a_share_kpl_concept_cons,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_kpl_list as mirror_tushare_a_share_kpl_list,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_limit_cpt_list as mirror_tushare_a_share_limit_cpt_list,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_limit_list_ths as mirror_tushare_a_share_limit_list_ths,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_limit_status as mirror_tushare_a_share_limit_status,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_limit_step as mirror_tushare_a_share_limit_step,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_margin as mirror_tushare_a_share_margin,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_margin_detail as mirror_tushare_a_share_margin_detail,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_moneyflow as mirror_tushare_a_share_moneyflow,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_moneyflow_dc as mirror_tushare_a_share_moneyflow_dc,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_moneyflow_hsgt as mirror_tushare_a_share_moneyflow_hsgt,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_moneyflow_ths as mirror_tushare_a_share_moneyflow_ths,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_report_rc as mirror_tushare_a_share_report_rc,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_stk_auction_close as mirror_tushare_a_share_stk_auction_close,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_stk_auction_open as mirror_tushare_a_share_stk_auction_open,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_stk_holdertrade as mirror_tushare_a_share_stk_holdertrade,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_stk_surv as mirror_tushare_a_share_stk_surv,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_ths_hot as mirror_tushare_a_share_ths_hot,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_top10_floatholders as mirror_tushare_a_share_top10_floatholders,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_top10_holders as mirror_tushare_a_share_top10_holders,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_a_share_top_inst as mirror_tushare_a_share_top_inst,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_etf_adj_factor as mirror_tushare_etf_adj_factor,
    )
    from market_data_platform.providers.tushare_a_share import (
        mirror_etf_daily as mirror_tushare_etf_daily,
    )

    return {
        "mirror-a-share-daily": mirror_tushare_a_share_daily,
        "mirror-a-share-adj-factor": mirror_tushare_a_share_adj_factor,
        "mirror-etf-daily": mirror_tushare_etf_daily,
        "mirror-etf-adj-factor": mirror_tushare_etf_adj_factor,
        "mirror-a-share-daily-basic": mirror_tushare_a_share_daily_basic,
        "mirror-a-share-limit-status": mirror_tushare_a_share_limit_status,
        "mirror-a-share-moneyflow": mirror_tushare_a_share_moneyflow,
        "mirror-a-share-moneyflow-dc": mirror_tushare_a_share_moneyflow_dc,
        "mirror-a-share-moneyflow-hsgt": mirror_tushare_a_share_moneyflow_hsgt,
        "mirror-a-share-top-inst": mirror_tushare_a_share_top_inst,
        "mirror-a-share-ths-hot": mirror_tushare_a_share_ths_hot,
        "mirror-a-share-dc-concept": mirror_tushare_a_share_dc_concept,
        "mirror-a-share-dc-concept-cons": mirror_tushare_a_share_dc_concept_cons,
        "mirror-a-share-kpl-concept-cons": mirror_tushare_a_share_kpl_concept_cons,
        "mirror-a-share-kpl-list": mirror_tushare_a_share_kpl_list,
        "mirror-a-share-limit-step": mirror_tushare_a_share_limit_step,
        "mirror-a-share-limit-cpt-list": mirror_tushare_a_share_limit_cpt_list,
        "mirror-a-share-stk-auction-open": mirror_tushare_a_share_stk_auction_open,
        "mirror-a-share-stk-auction-close": mirror_tushare_a_share_stk_auction_close,
        "mirror-a-share-report-rc": mirror_tushare_a_share_report_rc,
        "mirror-a-share-stk-surv": mirror_tushare_a_share_stk_surv,
        "mirror-a-share-broker-recommend": mirror_tushare_a_share_broker_recommend,
        "mirror-a-share-fund-portfolio": mirror_tushare_a_share_fund_portfolio,
        "mirror-a-share-top10-holders": mirror_tushare_a_share_top10_holders,
        "mirror-a-share-top10-floatholders": mirror_tushare_a_share_top10_floatholders,
        "mirror-a-share-stk-holdertrade": mirror_tushare_a_share_stk_holdertrade,
        "mirror-a-share-moneyflow-ths": mirror_tushare_a_share_moneyflow_ths,
        "mirror-a-share-limit-list-ths": mirror_tushare_a_share_limit_list_ths,
        "mirror-a-share-margin-detail": mirror_tushare_a_share_margin_detail,
        "mirror-a-share-margin": mirror_tushare_a_share_margin,
        "mirror-a-share-hsgt-top10": mirror_tushare_a_share_hsgt_top10,
    }


def print_summary(summary: object) -> None:
    import json

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


__all__ = ["_handle_tushare_core"]
