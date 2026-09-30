from __future__ import annotations

import argparse

from .tushare_cli_backfill import add_tushare_backfill_parser, add_tushare_refresh_parser
from .tushare_cli_clean import add_tushare_clean_parsers, add_tushare_universe_parsers

# Backward-compatible module-level exports expected by existing callers/tests.
from .tushare_cli_common import *  # noqa: F403
from .tushare_cli_constraints import add_tushare_constraint_parsers
from .tushare_cli_core import add_tushare_core_mirror_parsers
from .tushare_cli_fundamentals import add_tushare_fundamentals_parsers
from .tushare_cli_reference import add_tushare_reference_parsers
from .tushare_cli_research import add_tushare_research_asset_parsers


def add_tushare_parser(subparsers: argparse._SubParsersAction) -> None:
    from .cli_tushare import handle_tushare

    parser = subparsers.add_parser("tushare", help="TuShare A-share mirror/export helpers.")
    parser.set_defaults(handler=handle_tushare)
    tushare_subparsers = parser.add_subparsers(dest="tushare_command", required=True)
    add_tushare_core_mirror_parsers(tushare_subparsers)
    add_tushare_backfill_parser(tushare_subparsers)
    add_tushare_refresh_parser(tushare_subparsers)
    add_tushare_clean_parsers(tushare_subparsers)
    add_tushare_universe_parsers(tushare_subparsers)
    add_tushare_research_asset_parsers(tushare_subparsers)
    add_tushare_fundamentals_parsers(tushare_subparsers)
    add_tushare_reference_parsers(tushare_subparsers)
    add_tushare_constraint_parsers(tushare_subparsers)
