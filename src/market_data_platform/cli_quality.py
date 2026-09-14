"""Public quality-data command group."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _profile(args: argparse.Namespace) -> int:
    from market_data_platform.quality import profile_parquet

    payload = {
        "status": "complete",
        "reports": [
            profile_parquet(
                path,
                batch_size=args.batch_size,
                max_tracked_ids=args.max_tracked_ids,
            )
            for path in args.file
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output is None:
        print(text)
    else:
        args.output.expanduser().resolve().write_text(text + "\n", encoding="utf-8")
    return 0


def _integrity(args: argparse.Namespace) -> int:
    from market_data_platform.quality import profile_l2_integrity

    payload = profile_l2_integrity(
        args.orders,
        args.trades,
        batch_size=args.batch_size,
        max_order_ids=args.max_order_ids,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output is None:
        print(text)
    else:
        args.output.expanduser().resolve().write_text(text + "\n", encoding="utf-8")
    return 0


def _scan(args: argparse.Namespace) -> int:
    from market_data_platform.quality_scan import QualityScanOptions, scan_parquet_tree

    payload = scan_parquet_tree(
        QualityScanOptions(
            root=args.root,
            checkpoint=args.checkpoint,
            output=args.output,
            pattern=args.pattern,
            batch_size=args.batch_size,
            max_tracked_ids=args.max_tracked_ids,
        )
    )
    if args.output is None:
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


def _pilot(args: argparse.Namespace) -> int:
    from market_data_platform.quality_pilot import L2PilotOptions, run_l2_pilot

    payload = run_l2_pilot(
        L2PilotOptions(
            files=args.file,
            output_dir=args.output_dir,
            batch_size=args.batch_size,
            max_rows_per_file=args.max_rows_per_file,
        )
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _gate(args: argparse.Namespace) -> int:
    from market_data_platform.l2_quality_gate import L2GateOptions, run_l2_quality_gate

    root = args.root.expanduser().resolve()
    run_id = args.run_id or f"{args.dataset_id}:{root.name}"
    payload = run_l2_quality_gate(
        L2GateOptions(
            root=root,
            checkpoint=args.checkpoint,
            output=args.output,
            dataset_id=args.dataset_id,
            provider=args.provider,
            run_id=run_id,
            pattern=args.pattern,
            batch_size=args.batch_size,
            max_tracked_ids=args.max_tracked_ids,
            pilot_manifest=args.pilot_manifest,
            dataset_contract=args.dataset_contract,
            scan_output=args.scan_output,
        )
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


def add_quality_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "quality",
        help="Run read-only structural checks on explicit market-data files.",
    )
    commands = parser.add_subparsers(dest="quality_command", required=True)
    profile = commands.add_parser("profile", help="Profile Parquet quality without modifying it.")
    profile.add_argument("--file", action="append", required=True, type=Path)
    profile.add_argument("--output", type=Path, default=None)
    profile.add_argument("--batch-size", type=int, default=262_144)
    profile.add_argument("--max-tracked-ids", type=int, default=1_000_000)
    profile.set_defaults(handler=_profile)
    scan = commands.add_parser("scan", help="Scan a Parquet tree with resumable checkpoints.")
    scan.add_argument("--root", required=True, type=Path)
    scan.add_argument("--checkpoint", required=True, type=Path)
    scan.add_argument("--output", type=Path, default=None)
    scan.add_argument("--pattern", default="*.parquet")
    scan.add_argument("--batch-size", type=int, default=262_144)
    scan.add_argument("--max-tracked-ids", type=int, default=1_000_000)
    scan.set_defaults(handler=_scan)
    pilot = commands.add_parser(
        "pilot", help="Create a low-copy keep/tag/exclude pilot output for explicit files."
    )
    pilot.add_argument("--file", action="append", required=True, type=Path)
    pilot.add_argument("--output-dir", required=True, type=Path)
    pilot.add_argument("--batch-size", type=int, default=262_144)
    pilot.add_argument("--max-rows-per-file", type=int, default=None)
    pilot.set_defaults(handler=_pilot)
    gate = commands.add_parser(
        "gate", help="Scan a raw L2 partition and emit a production/research/quarantine DQ receipt."
    )
    gate.add_argument("--root", required=True, type=Path)
    gate.add_argument("--checkpoint", required=True, type=Path)
    gate.add_argument("--output", required=True, type=Path)
    gate.add_argument("--dataset-id", required=True)
    gate.add_argument("--provider", required=True)
    gate.add_argument("--run-id", default=None)
    gate.add_argument("--dataset-contract", type=Path, default=None)
    gate.add_argument("--pilot-manifest", type=Path, default=None)
    gate.add_argument("--scan-output", type=Path, default=None)
    gate.add_argument("--pattern", default="*.parquet")
    gate.add_argument("--batch-size", type=int, default=262_144)
    gate.add_argument("--max-tracked-ids", type=int, default=1_000_000)
    gate.set_defaults(handler=_gate)
    integrity = commands.add_parser(
        "integrity", help="Check trade order references against an order file."
    )
    integrity.add_argument("--orders", required=True, type=Path)
    integrity.add_argument("--trades", required=True, type=Path)
    integrity.add_argument("--output", type=Path, default=None)
    integrity.add_argument("--batch-size", type=int, default=262_144)
    integrity.add_argument("--max-order-ids", type=int, default=2_000_000)
    integrity.set_defaults(handler=_integrity)
