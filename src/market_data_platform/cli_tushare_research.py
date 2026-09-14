from __future__ import annotations

import argparse
from collections.abc import Callable

from .cli_tushare_common import print_status_summary, print_tushare_summary


def _handle_tushare_research_assets(args: argparse.Namespace) -> int | None:
    handlers = _tushare_research_asset_handlers()
    handler = handlers.get(args.tushare_command)
    return handler(args) if handler is not None else None


def _tushare_research_asset_handlers() -> dict[str, Callable[[argparse.Namespace], int]]:
    return {
        "build-a-share-pit-fundamentals": _handle_tushare_pit_fundamentals_build,
        "validate-a-share-pit-fundamentals": _handle_tushare_pit_fundamentals_validate,
        "download-a-share-industry-membership": _handle_tushare_industry_download,
        "build-a-share-industry-changes": _handle_tushare_industry_build,
        "validate-a-share-industry-changes": _handle_tushare_industry_validate,
        "build-a-share-flow-ownership-features": _handle_tushare_flow_features_build,
        "validate-a-share-flow-ownership-features": _handle_tushare_flow_features_validate,
        "build-a-share-hotspot-features": _handle_tushare_hotspot_features_build,
        "validate-a-share-hotspot-features": _handle_tushare_hotspot_features_validate,
        "build-a-share-fund-portfolio-features": _handle_tushare_fund_features_build,
        "validate-a-share-fund-portfolio-features": _handle_tushare_fund_features_validate,
        "build-a-share-fund-top10-portfolio-features": _handle_tushare_fund_top10_features_build,
        "validate-a-share-fund-top10-portfolio-features": (
            _handle_tushare_fund_top10_features_validate
        ),
        "build-a-share-holder-structure-features": _handle_tushare_holder_features_build,
        "validate-a-share-holder-structure-features": _handle_tushare_holder_features_validate,
        "build-a-share-top-inst-events": _handle_tushare_top_inst_events_build,
        "validate-a-share-top-inst-events": _handle_tushare_top_inst_events_validate,
        "build-a-share-holdertrade-events": _handle_tushare_holdertrade_events_build,
        "validate-a-share-holdertrade-events": _handle_tushare_holdertrade_events_validate,
        "build-a-share-hsgt-market-features": _handle_tushare_hsgt_features_build,
        "validate-a-share-hsgt-market-features": _handle_tushare_hsgt_features_validate,
    }


def _handle_tushare_pit_fundamentals_build(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_research import (
        build_a_share_pit_fundamentals as build_tushare_a_share_pit_fundamentals,
    )

    summary = build_tushare_a_share_pit_fundamentals(
        source_file=args.source_file,
        out_dir=args.out_dir,
        report_period_col=args.report_period_col,
        disclosure_date_col=args.disclosure_date_col,
        available_delay_days=args.available_delay_days,
        field_maps=args.field_maps,
        provider=args.provider,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_tushare_summary(summary)


def _handle_tushare_pit_fundamentals_validate(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_research import (
        validate_a_share_pit_fundamentals as validate_tushare_a_share_pit_fundamentals,
    )

    summary = validate_tushare_a_share_pit_fundamentals(
        asset_dir=args.asset_dir,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_status_summary(summary)


def _handle_tushare_industry_validate(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_research import (
        validate_a_share_industry_changes as validate_tushare_a_share_industry_changes,
    )

    summary = validate_tushare_a_share_industry_changes(
        asset_dir=args.asset_dir,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_status_summary(summary)


def _handle_tushare_flow_features_build(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_flow_features import (
        build_a_share_flow_ownership_features as build_tushare_a_share_flow_features,
    )

    summary = build_tushare_a_share_flow_features(
        moneyflow_dir=args.moneyflow_dir,
        daily_dir=args.daily_dir,
        daily_basic_dir=args.daily_basic_dir,
        industry_dir=args.industry_dir,
        out_dir=args.out_dir,
        windows=args.windows,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_tushare_summary(summary)


def _handle_tushare_flow_features_validate(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_flow_validation import (
        validate_a_share_flow_ownership_features as validate_tushare_a_share_flow_features,
    )

    summary = validate_tushare_a_share_flow_features(
        asset_dir=args.asset_dir,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_status_summary(summary)


def _handle_tushare_hotspot_features_build(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_hotspot_features import (
        build_a_share_hotspot_features as build_tushare_a_share_hotspot_features,
    )

    summary = build_tushare_a_share_hotspot_features(
        daily_basic_dir=args.daily_basic_dir,
        ths_hot_dir=args.ths_hot_dir,
        dc_concept_dir=args.dc_concept_dir,
        dc_concept_cons_dir=args.dc_concept_cons_dir,
        kpl_list_dir=args.kpl_list_dir,
        out_dir=args.out_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        kpl_concept_cons_dir=args.kpl_concept_cons_dir,
        limit_step_dir=args.limit_step_dir,
        report_rc_dir=args.report_rc_dir,
        stk_surv_dir=args.stk_surv_dir,
        broker_recommend_dir=args.broker_recommend_dir,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_tushare_summary(summary)


def _handle_tushare_hotspot_features_validate(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_hotspot_features import (
        validate_a_share_hotspot_features as validate_tushare_a_share_hotspot_features,
    )

    summary = validate_tushare_a_share_hotspot_features(
        asset_dir=args.asset_dir,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_status_summary(summary)


def _handle_tushare_fund_features_build(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_ownership_features import (
        build_a_share_fund_portfolio_features as build_tushare_a_share_fund_features,
    )

    summary = build_tushare_a_share_fund_features(
        fund_portfolio_dir=args.fund_portfolio_dir,
        daily_basic_dir=args.daily_basic_dir,
        out_dir=args.out_dir,
        available_delay_days=args.available_delay_days,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_tushare_summary(summary)


def _handle_tushare_fund_features_validate(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_ownership_features import (
        validate_a_share_fund_portfolio_features as validate_tushare_a_share_fund_features,
    )

    summary = validate_tushare_a_share_fund_features(
        asset_dir=args.asset_dir,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_status_summary(summary)


def _handle_tushare_fund_top10_features_build(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_ownership_features import (
        build_a_share_fund_top10_portfolio_features as build_tushare_a_share_fund_top10_features,
    )

    summary = build_tushare_a_share_fund_top10_features(
        fund_portfolio_dir=args.fund_portfolio_dir,
        daily_basic_dir=args.daily_basic_dir,
        out_dir=args.out_dir,
        available_delay_days=args.available_delay_days,
        top_n=args.top_n,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_tushare_summary(summary)


def _handle_tushare_fund_top10_features_validate(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_ownership_features import (
        validate_a_share_fund_top10_portfolio_features as validate_tushare_fund_top10_features,
    )

    summary = validate_tushare_fund_top10_features(
        asset_dir=args.asset_dir,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_status_summary(summary)


def _handle_tushare_holder_features_build(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_ownership_features import (
        build_a_share_holder_structure_features as build_tushare_a_share_holder_features,
    )

    summary = build_tushare_a_share_holder_features(
        top10_holders_dir=args.top10_holders_dir,
        top10_floatholders_dir=args.top10_floatholders_dir,
        daily_basic_dir=args.daily_basic_dir,
        out_dir=args.out_dir,
        available_delay_days=args.available_delay_days,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_tushare_summary(summary)


def _handle_tushare_holder_features_validate(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_ownership_features import (
        validate_a_share_holder_structure_features as validate_tushare_a_share_holder_features,
    )

    summary = validate_tushare_a_share_holder_features(
        asset_dir=args.asset_dir,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_status_summary(summary)


def _handle_tushare_top_inst_events_build(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_ownership_features import (
        build_a_share_top_inst_events as build_tushare_a_share_top_inst_events,
    )

    summary = build_tushare_a_share_top_inst_events(
        top_inst_dir=args.top_inst_dir,
        daily_dir=args.daily_dir,
        out_dir=args.out_dir,
        window=args.window,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_tushare_summary(summary)


def _handle_tushare_top_inst_events_validate(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_ownership_features import (
        validate_a_share_top_inst_events as validate_tushare_a_share_top_inst_events,
    )

    summary = validate_tushare_a_share_top_inst_events(
        asset_dir=args.asset_dir,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_status_summary(summary)


def _handle_tushare_holdertrade_events_build(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_ownership_features import (
        build_a_share_holdertrade_events as build_tushare_a_share_holdertrade_events,
    )

    summary = build_tushare_a_share_holdertrade_events(
        stk_holdertrade_dir=args.stk_holdertrade_dir,
        daily_basic_dir=args.daily_basic_dir,
        out_dir=args.out_dir,
        amount_window=args.amount_window,
        count_window=args.count_window,
        available_delay_days=args.available_delay_days,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_tushare_summary(summary)


def _handle_tushare_holdertrade_events_validate(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_ownership_features import (
        validate_a_share_holdertrade_events as validate_tushare_a_share_holdertrade_events,
    )

    summary = validate_tushare_a_share_holdertrade_events(
        asset_dir=args.asset_dir,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_status_summary(summary)


def _handle_tushare_hsgt_features_build(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_hsgt_features import (
        build_a_share_hsgt_market_features as build_tushare_a_share_hsgt_features,
    )

    summary = build_tushare_a_share_hsgt_features(
        moneyflow_hsgt_dir=args.moneyflow_hsgt_dir,
        out_dir=args.out_dir,
        windows=args.windows,
        min_rows=args.min_rows,
    )
    return print_tushare_summary(summary)


def _handle_tushare_hsgt_features_validate(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_hsgt_features import (
        validate_a_share_hsgt_market_features as validate_tushare_a_share_hsgt_features,
    )

    summary = validate_tushare_a_share_hsgt_features(
        asset_dir=args.asset_dir,
        min_rows=args.min_rows,
    )
    return print_status_summary(summary)


def _handle_tushare_industry_build(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_research import (
        IndustryChangesColumnMap,
    )
    from market_data_platform.providers.tushare_a_share_research import (
        build_a_share_industry_changes as build_tushare_a_share_industry_changes,
    )

    summary = build_tushare_a_share_industry_changes(
        source_file=args.source_file,
        out_dir=args.out_dir,
        columns=IndustryChangesColumnMap(
            effective_date=args.effective_date_col,
            end_date=args.end_date_col,
            industry_code=args.industry_code_col,
            industry_name=args.industry_name_col,
        ),
        industry_system=args.industry_system,
        provider=args.provider,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_tushare_summary(summary)


def _handle_tushare_industry_download(args: argparse.Namespace) -> int:
    from market_data_platform.providers.tushare_a_share_research import (
        download_a_share_industry_membership as download_tushare_a_share_industry_membership,
    )

    summary = download_tushare_a_share_industry_membership(
        out_dir=args.out_dir,
        src=args.src,
        level=args.level,
        is_new_flags=args.is_new_flags or ("Y", "N"),
        token_env=args.token_env,
        api_url=args.api_url,
        request_interval_seconds=args.request_interval_seconds,
        min_rows=args.min_rows,
        min_symbols=args.min_symbols,
    )
    return print_tushare_summary(summary)


__all__ = ["_handle_tushare_research_assets"]
