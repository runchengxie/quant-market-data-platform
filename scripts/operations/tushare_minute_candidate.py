#!/usr/bin/env python3
"""Freeze, audit, and publish a staged TuShare minute candidate."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from market_data_platform.minute_candidate import build_candidate_inventory
from market_data_platform.minute_candidate_audit import audit_candidate_semantics
from market_data_platform.minute_candidate_publish import publish_candidate


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _inventory(args: argparse.Namespace) -> int:
    payload = build_candidate_inventory(
        args.campaign_manifest,
        args.canonical_coverage,
        args.output_json,
    )
    print(
        "candidate inventory passed: "
        f"dates={payload['summary']['dates']} rows={payload['summary']['rows']}"
    )
    return 0


def _audit(args: argparse.Namespace) -> int:
    payload = audit_candidate_semantics(
        args.inventory,
        args.output_json,
        args.work_dir,
        threads=args.threads,
    )
    print(
        "candidate semantic audit complete: "
        f"dates={payload['summary']['dates']} "
        f"cutover_checks={payload['cutover_gates']['automated_checks_passed']}"
    )
    return 0


def _publish(args: argparse.Namespace) -> int:
    payload = publish_candidate(
        args.inventory,
        args.semantic_audit,
        args.output_dir,
        args.receipt_json,
    )
    print(
        "candidate published without canonical cutover: "
        f"dates={payload['summary']['dates']} rows={payload['summary']['rows']} "
        f"quality={payload['quality_status']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--campaign-manifest", type=_path, required=True)
    inventory.add_argument("--canonical-coverage", type=_path, required=True)
    inventory.add_argument("--output-json", type=_path, required=True)
    inventory.set_defaults(func=_inventory)
    audit = subparsers.add_parser("audit")
    audit.add_argument("--inventory", type=_path, required=True)
    audit.add_argument("--output-json", type=_path, required=True)
    audit.add_argument("--work-dir", type=_path, required=True)
    audit.add_argument("--threads", type=int, default=4)
    audit.set_defaults(func=_audit)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--inventory", type=_path, required=True)
    publish.add_argument("--semantic-audit", type=_path, required=True)
    publish.add_argument("--output-dir", type=_path, required=True)
    publish.add_argument("--receipt-json", type=_path, required=True)
    publish.set_defaults(func=_publish)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
