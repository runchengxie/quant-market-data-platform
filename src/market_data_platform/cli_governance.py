from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path

from .current_path_audit import audit_current_contract_paths
from .data_governance import (
    plan_data_governance,
    protected_paths_from_inventory,
    render_retention_tsv,
    rules_from_inventory,
)
from .paths import current_contract_path, resolve_artifacts_root

MARKET_CHOICES = ("a_share",)


def add_governance_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "governance",
        help="Audit current paths and build non-destructive retention plans.",
    )
    governance_subparsers = parser.add_subparsers(
        dest="governance_command",
        required=True,
    )

    audit = governance_subparsers.add_parser(
        "audit-current-paths",
        help="Audit current/latest path kinds, missing assets, and scoped date drift.",
    )
    audit.set_defaults(handler=handle_audit_current_paths)
    audit.add_argument("--artifacts-root")
    audit.add_argument("--market", default="a_share", choices=MARKET_CHOICES)
    audit.add_argument(
        "--contract",
        help="Default: <artifacts-root>/metadata/current_assets/<market>_current.json",
    )
    audit.add_argument("--out", help="Write JSON to this path instead of stdout.")

    retention = governance_subparsers.add_parser(
        "plan-retention",
        help="Build an inode-aware TSV plan without deleting or moving data.",
    )
    retention.set_defaults(handler=handle_plan_retention)
    retention.add_argument("--artifacts-root")
    retention.add_argument(
        "--inventory",
        help="Default: <artifacts-root>/metadata/lifecycle/inventory.json",
    )
    retention.add_argument("--out", help="Write TSV to this path instead of stdout.")
    retention.add_argument(
        "--latest-link",
        help="With --out, atomically point this symlink at the generated TSV.",
    )


def _load_json_object(path: Path, *, label: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} is not a JSON object: {path}")
    return {str(key): value for key, value in payload.items()}


def _write_or_print(
    rendered: str,
    output: str | None,
    *,
    allow_overwrite: bool = True,
) -> Path | None:
    if output is None:
        print(rendered, end="")
        return None
    output_path = Path(output).expanduser().absolute()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_symlink():
        raise FileExistsError(f"Refusing to write a report through a symlink: {output_path}")
    if allow_overwrite:
        output_path.write_text(rendered, encoding="utf-8")
    else:
        try:
            with output_path.open("x", encoding="utf-8") as handle:
                handle.write(rendered)
        except FileExistsError as exc:
            raise FileExistsError(
                f"Refusing to overwrite an existing retention report: {output_path}"
            ) from exc
    print(str(output_path))
    return output_path


def _validate_latest_link(link: str, target: Path) -> Path:
    link_path = Path(link).expanduser().absolute()
    if link_path == target:
        raise ValueError("latest link and report output must be different paths")
    if os.path.lexists(link_path) and not link_path.is_symlink():
        raise FileExistsError(f"Refusing to replace a non-symlink latest path: {link_path}")
    return link_path


def _update_relative_symlink(link: str, target: Path) -> Path:
    link_path = _validate_latest_link(link, target)
    link_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = link_path.with_name(f".{link_path.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(os.path.relpath(target, start=link_path.parent))
    temporary.replace(link_path)
    return link_path


def _current_contract_protected_paths(root: Path) -> list[str]:
    protected: list[str] = []
    contracts_root = root / "metadata" / "current_assets"
    for contract_path in sorted(contracts_root.glob("*_current.json")):
        payload = _load_json_object(contract_path, label="Current contract")
        assets = payload.get("assets")
        if not isinstance(assets, Mapping):
            continue
        for raw_entry in assets.values():
            if not isinstance(raw_entry, Mapping) or raw_entry.get("exists") is False:
                continue
            for key in ("alias_path", "resolved_path"):
                value = raw_entry.get(key)
                if isinstance(value, str) and value.strip():
                    protected.append(value)
    return protected


def handle_audit_current_paths(args: argparse.Namespace) -> int:
    root = resolve_artifacts_root(args.artifacts_root)
    contract = current_contract_path(root, market=args.market)
    if args.contract is not None:
        contract = Path(args.contract).expanduser().resolve()
    payload = _load_json_object(contract, label="Current contract")
    report = audit_current_contract_paths(payload, contract_path=contract)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_or_print(rendered, args.out)
    return 0


def handle_plan_retention(args: argparse.Namespace) -> int:
    root = resolve_artifacts_root(args.artifacts_root)
    inventory = root / "metadata" / "lifecycle" / "inventory.json"
    if args.inventory is not None:
        inventory = Path(args.inventory).expanduser().resolve()
    if args.latest_link is not None:
        if args.out is None:
            raise ValueError("--latest-link requires --out")
        _validate_latest_link(args.latest_link, Path(args.out).expanduser().absolute())
    payload = _load_json_object(inventory, label="Lifecycle inventory")
    rules = rules_from_inventory(payload)
    items = plan_data_governance(
        root,
        rules,
        protected_paths=[
            *_current_contract_protected_paths(root),
            *protected_paths_from_inventory(payload),
        ],
    )
    output = _write_or_print(
        render_retention_tsv(items),
        args.out,
        allow_overwrite=False,
    )
    if args.latest_link is not None:
        assert output is not None
        link = _update_relative_symlink(args.latest_link, output)
        print(f"latest_link: {link}")
    return 0


__all__ = [
    "add_governance_parser",
    "handle_audit_current_paths",
    "handle_plan_retention",
]
