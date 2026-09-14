from __future__ import annotations

import argparse
import json

from .paths import (
    SUPPORTED_MARKETS,
    candidate_asset_paths,
    current_contract_path,
    dataset_registry_path,
    normalize_provider,
    resolve_artifacts_root,
)

MARKET_CHOICES = tuple(market for market in ("a_share",) if market in SUPPORTED_MARKETS)


def add_paths_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("paths", help="Print shared data platform paths.")
    parser.set_defaults(handler=handle_paths)
    parser.add_argument("--artifacts-root")
    parser.add_argument("--market", default="a_share", choices=MARKET_CHOICES)
    parser.add_argument("--provider", choices=("tushare",))
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle_paths(args: argparse.Namespace) -> int:
    root = resolve_artifacts_root(args.artifacts_root)
    provider = normalize_provider(args.provider, market=args.market)
    payload = {
        "artifacts_root": str(root),
        "market": args.market,
        "provider": provider,
        "current_contract": str(current_contract_path(root, market=args.market)),
        "dataset_registry": str(dataset_registry_path(root)),
        "assets": {
            key: str(path)
            for key, path in candidate_asset_paths(
                root,
                market=args.market,
                provider=provider,
            ).items()
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"artifacts_root: {payload['artifacts_root']}")
        print(f"market: {payload['market']}")
        print(f"provider: {payload['provider']}")
        print(f"current_contract: {payload['current_contract']}")
        print(f"dataset_registry: {payload['dataset_registry']}")
        for key, path in payload["assets"].items():
            print(f"{key}: {path}")
    return 0
