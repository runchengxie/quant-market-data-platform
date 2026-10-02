"""QuantZone CLI with optional runtime imports."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path

from quant_market_data_platform.cli_config import selected_config
from quant_market_data_platform.providers.quantzone import check_quantzone, create_client
from quant_market_data_platform.quantzone_plan import EVIDENCE, build_factor_plan


def _check(args: argparse.Namespace) -> int:
    config = selected_config(args.config)
    plan = build_factor_plan(config, os.environ)
    client = create_client(config, os.environ)
    try:
        print(json.dumps(check_quantzone(client, plan), sort_keys=True))
    finally:
        client.close()
    return 0


def _download(args: argparse.Namespace) -> int:
    config = selected_config(args.config)
    plan = build_factor_plan(config, os.environ)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "query_identity": plan.query_identity,
                    "batch_count": len(plan.batches),
                    "root": str(plan.root),
                    "evidence": EVIDENCE,
                },
                sort_keys=True,
            )
        )
        return 0
    run_factor_download = importlib.import_module(
        "quant_market_data_platform.quantzone_download"
    ).run_factor_download

    client = create_client(config, os.environ)
    print(run_factor_download(plan, client, resume=args.resume))
    return 0


def add_quantzone_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("quantzone", help="Bounded QuantZone factor acquisition")
    commands = parser.add_subparsers(dest="quantzone_command", required=True)
    for name, handler in (("check", _check), ("download-factors", _download)):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path)
        if name == "download-factors":
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--resume", type=Path)
        command.set_defaults(handler=handler)
