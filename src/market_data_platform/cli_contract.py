from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .contract import build_current_contract, write_current_contract
from .contract_health import (
    ContractInspectionOptions,
    current_contract_health_exit_code,
    inspect_current_contract,
    write_current_contract_health_report,
)
from .paths import (
    SUPPORTED_MARKETS,
    SUPPORTED_PROVIDERS_BY_MARKET,
    current_contract_path,
    dataset_registry_path,
    resolve_artifacts_root,
)
from .registry import (
    render_combined_dataset_registry_csv,
    write_combined_dataset_registry,
)

MARKET_CHOICES = tuple(sorted(SUPPORTED_MARKETS))
PROVIDER_CHOICES = tuple(
    sorted(
        {provider for providers in SUPPORTED_PROVIDERS_BY_MARKET.values() for provider in providers}
    )
)
REGISTRY_MARKET_CHOICES = ("all", *MARKET_CHOICES)


def add_contract_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("contract", help="Current contract helpers.")
    contract_subparsers = parser.add_subparsers(dest="contract_command", required=True)
    build = contract_subparsers.add_parser(
        "build",
        help="Build <market>_current.json from standard paths.",
    )
    build.set_defaults(handler=handle_contract_build)
    build.add_argument("--artifacts-root")
    build.add_argument("--market", default="a_share", choices=MARKET_CHOICES)
    build.add_argument("--provider", choices=PROVIDER_CHOICES)
    build.add_argument("--target-date")
    build.add_argument("--generated-by", default="marketdata contract build")
    build.add_argument(
        "--out",
        help="Default: <artifacts-root>/metadata/current_assets/<market>_current.json",
    )
    build.add_argument(
        "--registry-out",
        help="Default: <artifacts-root>/metadata/dataset_registry.csv",
    )
    build.add_argument(
        "--no-registry",
        action="store_true",
        help="Only write <market>_current.json; skip dataset_registry.csv.",
    )
    build.add_argument(
        "--dry-run",
        action="store_true",
        help="Print contract JSON without writing it.",
    )
    inspect = contract_subparsers.add_parser(
        "inspect",
        help="Inspect a current contract for missing assets and stale as-of dates.",
    )
    inspect.set_defaults(handler=handle_contract_inspect)
    inspect.add_argument("--artifacts-root")
    inspect.add_argument("--market", default="a_share", choices=MARKET_CHOICES)
    inspect.add_argument("--provider", choices=PROVIDER_CHOICES)
    inspect.add_argument("--current-contract")
    inspect.add_argument("--target-date")
    inspect.add_argument(
        "--require-start-date",
        help="Report a warning when an asset manifest starts after this YYYYMMDD date.",
    )
    inspect.add_argument(
        "--asset",
        action="append",
        default=[],
        help="Only inspect selected asset key(s). Repeatable.",
    )
    inspect.add_argument(
        "--fail-on-severity",
        choices=("none", "info", "warning", "error"),
        default="none",
    )
    inspect.add_argument("--out")
    inspect.add_argument("--format", choices=("text", "json"), default="text")


def add_registry_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("registry", help="Dataset registry helpers.")
    registry_subparsers = parser.add_subparsers(dest="registry_command", required=True)
    build = registry_subparsers.add_parser(
        "build",
        help="Build dataset_registry.csv from current contracts.",
    )
    build.set_defaults(handler=handle_registry_build)
    build.add_argument("--artifacts-root")
    build.add_argument("--market", default="all", choices=REGISTRY_MARKET_CHOICES)
    build.add_argument(
        "--contract",
        help=(
            "Use one explicit current contract. Default: combine existing "
            "metadata/current_assets/*_current.json files."
        ),
    )
    build.add_argument(
        "--out",
        help="Default: <artifacts-root>/metadata/dataset_registry.csv",
    )
    build.add_argument(
        "--dry-run",
        action="store_true",
        help="Print registry CSV without writing it.",
    )


def _load_contract(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Contract is not a JSON object: {path}")
    return payload


def _contract_market(contract: dict[str, object]) -> str:
    meta = contract.get("contract")
    if not isinstance(meta, dict):
        return "a_share"
    return str(meta.get("market") or "a_share").strip().lower() or "a_share"


def _with_contract_path(payload: dict[str, Any], output: Path) -> dict[str, Any]:
    normalized = dict(payload)
    meta = dict(normalized.get("contract") or {})
    meta["contract_path"] = str(output)
    normalized["contract"] = meta
    return normalized


def _load_registry_contracts(
    root: Path,
    *,
    market: str,
    explicit_contract: str | None = None,
    override_payload: dict[str, Any] | None = None,
) -> list[dict[str, object]]:
    if explicit_contract is not None:
        return [_load_contract(Path(explicit_contract).expanduser().resolve())]

    markets = MARKET_CHOICES if market == "all" else (market,)
    contracts: list[dict[str, object]] = []
    override_market = _contract_market(override_payload) if override_payload is not None else None
    for candidate_market in markets:
        if override_payload is not None and candidate_market == override_market:
            contracts.append(override_payload)
            continue
        path = current_contract_path(root, market=candidate_market)
        if path.exists():
            contracts.append(_load_contract(path))
    if not contracts:
        raise FileNotFoundError(
            f"No current contracts found under {root / 'metadata' / 'current_assets'}."
        )
    return contracts


def handle_contract_build(args: argparse.Namespace) -> int:
    root = resolve_artifacts_root(args.artifacts_root)
    output = current_contract_path(root, market=args.market)
    if args.out is not None:
        output = Path(args.out).expanduser().resolve()
    registry_output = dataset_registry_path(root)
    if args.registry_out is not None:
        registry_output = Path(args.registry_out).expanduser().resolve()
    payload = build_current_contract(
        root,
        market=args.market,
        provider=args.provider,
        generated_by=args.generated_by,
        target_date=args.target_date,
    )
    payload = _with_contract_path(payload, output)
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    write_current_contract(output, payload)
    print(f"current_contract: {output}")
    if not args.no_registry:
        contracts = _load_registry_contracts(
            root,
            market="all",
            override_payload=payload,
        )
        write_combined_dataset_registry(registry_output, contracts)
        print(f"dataset_registry: {registry_output}")
    return 0


def handle_contract_inspect(args: argparse.Namespace) -> int:
    payload = inspect_current_contract(
        ContractInspectionOptions(
            artifacts_root=args.artifacts_root,
            market=args.market,
            provider=args.provider,
            current_contract=args.current_contract,
            target_date=args.target_date,
            required_start_date=args.require_start_date,
            assets=args.asset,
            fail_on_severity=args.fail_on_severity,
        )
    )
    write_current_contract_health_report(
        payload,
        output=args.out,
        output_format=args.format,
    )
    return current_contract_health_exit_code(payload)


def handle_registry_build(args: argparse.Namespace) -> int:
    root = resolve_artifacts_root(args.artifacts_root)
    output = dataset_registry_path(root)
    if args.out is not None:
        output = Path(args.out).expanduser().resolve()
    contracts = _load_registry_contracts(
        root,
        market=args.market,
        explicit_contract=args.contract,
    )
    if args.dry_run:
        print(render_combined_dataset_registry_csv(contracts), end="")
        return 0
    write_combined_dataset_registry(output, contracts)
    print(str(output))
    return 0
