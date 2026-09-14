from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import cast

from market_data_platform.cli_backup import add_backup_parser
from market_data_platform.cli_context import add_context_parser
from market_data_platform.cli_contract import add_contract_parser, add_registry_parser
from market_data_platform.cli_data import add_data_parser
from market_data_platform.cli_governance import add_governance_parser
from market_data_platform.cli_paths import add_paths_parser
from market_data_platform.cli_quality import add_quality_parser
from market_data_platform.cli_research_features import add_research_features_parser
from market_data_platform.tushare_cli import add_tushare_parser

OPTIONAL_DEPENDENCIES = {
    "akshare",
    "duckdb",
    "pandas",
    "pyarrow",
    "tushare",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marketdata")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_paths_parser(subparsers)
    add_contract_parser(subparsers)
    add_registry_parser(subparsers)
    add_context_parser(subparsers)
    add_data_parser(subparsers)
    add_research_features_parser(subparsers)
    add_quality_parser(subparsers)
    add_governance_parser(subparsers)
    add_backup_parser(subparsers)
    add_tushare_parser(subparsers)
    return parser


def _is_optional_dependency_error(error: RuntimeError) -> bool:
    text = str(error)
    return "required for this command" in text and "Install" in text


def _extra_for_missing_dependency(args: argparse.Namespace, missing_name: str | None) -> str:
    command_extras = {
        "tushare": "tushare",
        "research-features": "research-features",
        "quality": "quality",
    }
    if command_extra := command_extras.get(args.command):
        return command_extra
    if args.command == "data" and missing_name == "duckdb":
        return "duckdb"
    if (
        args.command == "data"
        and missing_name == "akshare"
        and getattr(args, "data_command", None) == "mirror-public-etf-minute"
    ):
        return "etf-minute-public"
    if missing_name is None:
        return "dev"
    return {"tushare": "tushare"}.get(missing_name, "dev")


def _format_missing_dependency_error(
    args: argparse.Namespace,
    error: ModuleNotFoundError,
) -> str | None:
    missing_name = error.name
    if missing_name not in OPTIONAL_DEPENDENCIES:
        return None
    extra = _extra_for_missing_dependency(args, missing_name)
    return (
        f"{missing_name} is required for this command. "
        f"Install optional dependencies with `uv sync --extra {extra}`."
    )


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    handler = getattr(args, "handler", None)
    if callable(handler):
        typed_handler = cast(Callable[[argparse.Namespace], int | None], handler)
        return int(typed_handler(args) or 0)
    parser.error(f"Unknown command: {args.command}")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args, parser)
    except ModuleNotFoundError as exc:
        message = _format_missing_dependency_error(args, exc)
        if message is not None:
            print(message, file=sys.stderr)
            return 1
        raise
    except RuntimeError as exc:
        if _is_optional_dependency_error(exc):
            print(str(exc), file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
