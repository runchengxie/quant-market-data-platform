from __future__ import annotations

import argparse


def add_backup_parser(subparsers: argparse._SubParsersAction) -> None:
    from market_data_platform.backup_data_cli import add_backup_data_args

    parser = subparsers.add_parser(
        "backup-data",
        help=(
            "Create a private local snapshot of caches, universe files, configs, "
            "or HK current assets."
        ),
    )
    parser.set_defaults(handler=handle_backup_data)
    add_backup_data_args(parser)


def handle_backup_data(args: argparse.Namespace) -> int:
    from market_data_platform.backup_data import run_backup

    return run_backup(args)
