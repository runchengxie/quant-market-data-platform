from __future__ import annotations

import argparse

DEFAULT_SNAPSHOTS_DIR = "artifacts/snapshots"
DEFAULT_CACHE_DIR = "artifacts/cache"
DEFAULT_UNIVERSE_DIR = "artifacts/assets/universe"


def add_backup_data_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preset",
        choices=("a_share_current",),
        default=None,
        help=(
            "Optional backup selection preset. "
            "`a_share_current` freezes the current A-share asset set declared by "
            "a_share_current.json."
        ),
    )
    parser.add_argument(
        "--out-root",
        default=DEFAULT_SNAPSHOTS_DIR,
        help=f"Snapshot root directory. Default: {DEFAULT_SNAPSHOTS_DIR}",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Snapshot folder name. Default: snapshot_<timestamp>",
    )
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        help="Config file to include. Repeatable.",
    )
    parser.add_argument(
        "--include-path",
        action="append",
        default=[],
        help="Extra file or directory to include. Repeatable.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help=f"Do not include {DEFAULT_CACHE_DIR}/.",
    )
    parser.add_argument(
        "--no-universe",
        action="store_true",
        help=f"Do not include {DEFAULT_UNIVERSE_DIR}/.",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip missing paths instead of failing.",
    )
