"""Explicit opt-in catalog CLI; legacy mirror paths are never modified."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def add_margin_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("margin", help="Prepare and adopt opt-in financing versions.")
    commands = parser.add_subparsers(dest="margin_command", required=True)
    prepare = commands.add_parser("prepare", help="Prepare an immutable offline candidate.")
    prepare.add_argument("--candidate", type=Path, required=True)
    prepare.add_argument("--lineage", type=Path, required=True)
    for command in (
        prepare,
        commands.add_parser("adopt"),
        commands.add_parser("rollback"),
        commands.add_parser("contract"),
        commands.add_parser("recover"),
    ):
        command.add_argument("--catalog", type=Path, required=True)
        command.set_defaults(handler=handle_margin)
        if command.prog.split()[-1] == "recover":
            command.add_argument("--apply", action="store_true")
        if command.prog.split()[-1] in {"adopt", "rollback"}:
            command.add_argument("--dataset", choices=("margin", "margin_detail"), required=True)
            command.add_argument("--target", type=Path, required=True)
            command.add_argument("--target-sha256", required=True, help="Prepared receipt SHA256.")
            command.add_argument(
                "--expected-current", help="Exact old link; omit only for no current."
            )
            command.add_argument("--expected-current-sha256", help="Old prepared receipt SHA256.")
            command.add_argument("--apply", action="store_true")


def handle_margin(args: argparse.Namespace) -> int:
    try:
        return _run_margin(args)
    except ModuleNotFoundError as exc:
        if exc.name != "fcntl":
            raise
        print("ERROR: margin catalog operations require POSIX file locking", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _run_margin(args: argparse.Namespace) -> int:
    from .margin_publication import (
        MarginSwitchOptions,
        export_margin_contract,
        prepare_margin_version,
        recover_margin_catalog,
        switch_margin_version,
    )

    if args.margin_command == "prepare":
        result = prepare_margin_version(
            candidate=args.candidate, catalog=args.catalog, lineage=args.lineage
        )
    elif args.margin_command == "recover":
        result = recover_margin_catalog(catalog=args.catalog, apply=args.apply)
    elif args.margin_command == "contract":
        result = export_margin_contract(catalog=args.catalog)
    else:
        result = switch_margin_version(
            MarginSwitchOptions(
                catalog=args.catalog,
                dataset=args.dataset,
                target=args.target,
                target_sha256=args.target_sha256,
                expected=args.expected_current,
                expected_current_sha256=args.expected_current_sha256,
                apply=args.apply,
                operation=args.margin_command,
            )
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0
