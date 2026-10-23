"""Read-only, versioned exchange-overlay validation for installed CLI consumers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VALIDATION_SCHEMA = "marketdata.minute_exchange_overlay.v1"
RECEIPT_FIELDS = (
    "schema_version",
    "trade_date",
    "partition_path",
    "sidecar_path",
    "partition_sha256",
    "sidecar_sha256",
    "rows",
    "symbols",
    "market_symbol_counts",
    "expected_bars_per_symbol",
    "universe_hash",
    "universe_rule",
    "universe_source",
    "mirror_generated_at",
)


def add_minute_validation_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "validate-minute-partition",
        help="Validate a hash-bound SH/SZ minute overlay without fetching or changing data.",
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--exchange", required=True, choices=("SH", "SZ"))
    parser.set_defaults(handler=validate_minute_partition)


def validate_minute_partition(args: argparse.Namespace) -> int:
    from .providers.tushare_a_share_mins import UNIVERSE_RULE, validate_complete_minute_partition

    try:
        root = Path(args.data_root).expanduser().resolve()
        receipt = validate_complete_minute_partition(
            root / f"trade_date={args.trade_date}",
            trade_date=args.trade_date,
            require_full_universe=False,
        )
        exchange = args.exchange
        if receipt["universe_rule"] != f"{UNIVERSE_RULE}:exchange={exchange}":
            raise ValueError(f"{exchange} minute overlay uses an unexpected universe rule")
        counts = receipt["market_symbol_counts"]
        if counts.get(exchange, 0) <= 0:
            raise ValueError(f"{exchange} minute overlay contains no {exchange} symbols")
        if any(counts.get(market, 0) != 0 for market in {"SH", "SZ", "BJ"} - {exchange}):
            raise ValueError(f"{exchange} minute overlay contains out-of-scope symbols")
    except (ValueError, OSError) as exc:
        print(f"minute validation failed: {exc}", file=sys.stderr)
        return 1
    result = {key: receipt[key] for key in RECEIPT_FIELDS}
    result.update(validation_schema_version=VALIDATION_SCHEMA, exchange=exchange)
    print(json.dumps(result, sort_keys=True))
    return 0
