"""Re-export surface for the split modules of cli_data."""

import argparse

from market_data_platform.cli_data_part01 import (
    _add_annual_minute_parser,
    _add_deal_minute_parser,
    _add_minute_fusion_parser,
    _add_public_etf_minute_parser,
    _add_warehouse_data_parsers,
    _BJOverlayPlanContext,
    _coverage_requirements_with_full_days,
    _CoverageCommandInputs,
    _CoverageMaterializations,
    _FullDayPlanContext,
    _has_complete_tushare_bj_overlay_args,
    _has_complete_tushare_full_day_args,
    _materialize_tushare_bj_overlay_plan,
    _materialize_tushare_full_day_plan,
    _parse_years,
    _print_json,
    _sha256_file,
    _source_audit_lineage,
)
from market_data_platform.cli_data_part02 import (
    _add_coverage_parser,
    _coverage_dry_run_payload,
    _handle_build_guan_annual_minutes,
    _handle_build_guan_deal_minutes,
    _handle_fuse_a_share_minutes,
    _handle_mirror_public_etf_minute,
    _handle_warehouse_data,
    _load_coverage_command_inputs,
    _materialize_coverage_sources,
)
from market_data_platform.cli_data_part03 import (
    _audit_finalized_coverage,
    _coverage_result_payload,
    _handle_finalize_a_share_minute_coverage,
    _require_coverage_command_dependencies,
)


def handle_data(args: argparse.Namespace) -> int:

    handlers = {
        "build-guan-annual-minutes": _handle_build_guan_annual_minutes,
        "build-guan-deal-minutes": _handle_build_guan_deal_minutes,
        "finalize-a-share-minute-coverage": _handle_finalize_a_share_minute_coverage,
        "fuse-a-share-minutes": _handle_fuse_a_share_minutes,
        "mirror-public-etf-minute": _handle_mirror_public_etf_minute,
    }
    handler = handlers.get(args.data_command, _handle_warehouse_data)
    return handler(args)


def add_data_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "data",
        help="Catalog, materialize, and query manifest-backed data assets.",
    )
    data_subparsers = parser.add_subparsers(dest="data_command", required=True)
    for subparser in _add_warehouse_data_parsers(data_subparsers):
        subparser.set_defaults(handler=handle_data)
    for subparser in _add_minute_fusion_parser(data_subparsers):
        subparser.set_defaults(handler=handle_data)
    for subparser in _add_annual_minute_parser(data_subparsers):
        subparser.set_defaults(handler=handle_data)
    for subparser in _add_deal_minute_parser(data_subparsers):
        subparser.set_defaults(handler=handle_data)
    for subparser in _add_public_etf_minute_parser(data_subparsers):
        subparser.set_defaults(handler=handle_data)
    for subparser in _add_coverage_parser(data_subparsers):
        subparser.set_defaults(handler=handle_data)
