from __future__ import annotations

import argparse

from .cli_tushare_backfill import _handle_tushare_backfill_or_refresh
from .cli_tushare_constraints import _handle_tushare_constraints
from .cli_tushare_core import _handle_tushare_core
from .cli_tushare_daily import _handle_tushare_daily_and_universe
from .cli_tushare_fundamentals import (
    _handle_tushare_fundamentals_pit,
    _handle_tushare_fundamentals_raw,
)
from .cli_tushare_reference import _handle_tushare_reference_raw
from .cli_tushare_research import _handle_tushare_research_assets


def handle_tushare(args: argparse.Namespace) -> int:
    for handler in (
        _handle_tushare_core,
        _handle_tushare_backfill_or_refresh,
        _handle_tushare_daily_and_universe,
        _handle_tushare_fundamentals_raw,
        _handle_tushare_fundamentals_pit,
        _handle_tushare_research_assets,
        _handle_tushare_reference_raw,
        _handle_tushare_constraints,
    ):
        result = handler(args)
        if result is not None:
            return result
    raise ValueError(f"Unknown tushare command: {args.tushare_command}")


__all__ = ["handle_tushare"]
