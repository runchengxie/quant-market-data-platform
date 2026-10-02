"""QuantZone CLI with optional runtime imports."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path

from quant_market_data_platform.cli_config import selected_config
from quant_market_data_platform.configuration import PlatformConfig
from quant_market_data_platform.providers.quantzone import (
    FactorClient,
    check_quantzone,
    create_client,
)
from quant_market_data_platform.quantzone_plan import (
    EVIDENCE,
    FactorDownloadPlan,
    build_factor_plan,
)


def _configured_client(
    config: PlatformConfig, plan: FactorDownloadPlan, *, no_proxy: bool
) -> FactorClient:
    """Construct the SDK client with an explicitly selected proxy policy."""
    if not no_proxy:
        return create_client(config, os.environ, plan=plan)
    names = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    previous = {name: os.environ.pop(name) for name in names if name in os.environ}
    try:
        return create_client(config, os.environ, plan=plan)
    finally:
        os.environ.update(previous)


def _check(args: argparse.Namespace) -> int:
    config = selected_config(args.config)
    plan = build_factor_plan(config, os.environ, job=args.job)
    client = _configured_client(config, plan, no_proxy=args.no_proxy)
    try:
        print(json.dumps(check_quantzone(client, plan), sort_keys=True))
    finally:
        client.close()
    return 0


def _download(args: argparse.Namespace) -> int:
    config = selected_config(args.config)
    plan = build_factor_plan(config, os.environ, job=args.job)
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

    client = _configured_client(config, plan, no_proxy=args.no_proxy)
    print(run_factor_download(plan, client, resume=args.resume))
    return 0


def add_quantzone_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("quantzone", help="Bounded QuantZone factor acquisition")
    commands = parser.add_subparsers(dest="quantzone_command", required=True)
    for name, handler in (("check", _check), ("download-factors", _download)):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path)
        command.add_argument("--job", type=Path, help="Separate acquisition job JSON")
        command.add_argument(
            "--no-proxy",
            action="store_true",
            help="Connect directly without inherited HTTP or SOCKS proxies",
        )
        if name == "download-factors":
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--resume", type=Path)
        command.set_defaults(handler=handler)
