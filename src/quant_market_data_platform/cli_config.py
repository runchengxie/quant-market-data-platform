"""Inspect private configuration and replace a process using resolved settings."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from quant_market_data_platform.configuration import (
    ConfigurationError,
    PlatformConfig,
    load_config,
    resolve_config_path,
    resolve_environment,
)


def selected_config(path: Path | None) -> PlatformConfig:
    selected = path if path is not None else resolve_config_path(os.environ)
    if selected is None:
        raise ConfigurationError(
            "Select a private JSON configuration with --config or DATA_PLATFORM_CONFIG"
        )
    return load_config(selected)


def _check(args: argparse.Namespace) -> int:
    config = selected_config(args.config)
    environment = resolve_environment(config, os.environ)
    print(
        json.dumps(
            {
                "schema_version": 1,
                "valid": True,
                "configured": {
                    name: bool(environment.get(name)) for name in sorted(config.environment)
                },
            },
            sort_keys=True,
        )
    )
    return 0


def _run(args: argparse.Namespace) -> int:
    command: list[str] = args.argv
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise ConfigurationError("config run requires a command after --")
    config = selected_config(args.config)
    try:
        os.execvpe(command[0], command, resolve_environment(config, os.environ))
    except OSError:
        raise ConfigurationError("Cannot execute configured command") from None
    return 0


def add_config_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("config", help="Private JSON configuration")
    commands = parser.add_subparsers(dest="config_command", required=True)
    for name, handler in (("check", _check), ("run", _run)):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path)
        if name == "run":
            command.add_argument("argv", nargs=argparse.REMAINDER)
        command.set_defaults(handler=handler)
