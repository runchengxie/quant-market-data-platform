"""Private JSON configuration without optional provider dependencies."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import cast


class ConfigurationError(ValueError):
    """A configuration failure whose message contains no setting values."""


@dataclass(frozen=True)
class PlatformConfig:
    path: Path
    environment: Mapping[str, str | None] = field(repr=False)
    downloads: Mapping[str, object] = field(repr=False)
    providers: Mapping[str, object] = field(default_factory=dict, repr=False)
    jobs: Mapping[str, str] = field(default_factory=dict, repr=False)
    schema_version: int = 1


def resolve_config_path(environment: Mapping[str, str]) -> Path | None:
    explicit = environment.get("DATA_PLATFORM_CONFIG")
    if explicit is not None:
        if not explicit.strip():
            raise ConfigurationError("DATA_PLATFORM_CONFIG must select a file")
        return Path(explicit).expanduser().absolute()
    home = Path(environment.get("HOME") or Path.home())
    root = Path(environment.get("XDG_CONFIG_HOME") or home / ".config")
    path = root / "quant-market-data-platform" / "config.json"
    return path.absolute() if path.exists() or path.is_symlink() else None


def _validate_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError("Selected configuration must be a regular non-symlink file")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ConfigurationError("Selected configuration requires mode 0600")
    if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        raise ConfigurationError("Selected configuration owner must be the current user")


def _read_private_json(path: Path) -> str:
    try:
        original = path.lstat()
        _validate_metadata(original)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            opened = os.fstat(stream.fileno())
            _validate_metadata(opened)
            if (original.st_dev, original.st_ino) != (opened.st_dev, opened.st_ino):
                raise ConfigurationError("Selected configuration changed while opening")
            return stream.read()
    except (OSError, UnicodeError):
        raise ConfigurationError("Cannot read selected configuration file") from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_: str) -> object:
    raise ConfigurationError("Non-finite JSON constant")


def _validate_environment(value: object) -> dict[str, str | None]:
    if not isinstance(value, dict):
        raise ConfigurationError("environment must be an object")
    result: dict[str, str | None] = {}
    for key, setting in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigurationError("Invalid environment variable name")
        if setting is not None and not isinstance(setting, str):
            raise ConfigurationError("Environment values must be strings or null")
        if isinstance(setting, str) and "\x00" in setting:
            raise ConfigurationError("Environment values cannot contain NUL")
        result[key] = setting
    return result


def load_config(path: Path) -> PlatformConfig:
    path = path.expanduser().absolute()
    try:
        payload = json.loads(
            _read_private_json(path),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError:
        raise ConfigurationError("Selected configuration contains invalid JSON") from None
    if (
        isinstance(payload, dict)
        and type(payload.get("schema_version")) is int
        and payload["schema_version"] == 2
    ):
        from quant_market_data_platform.configuration_sources import load_split_config

        return load_split_config(path, payload)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "environment",
        "downloads",
    }:
        raise ConfigurationError("Configuration requires schema_version, environment, downloads")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ConfigurationError("Unsupported configuration schema_version")
    environment = _validate_environment(payload["environment"])
    if not isinstance(payload["downloads"], dict):
        raise ConfigurationError("downloads must be an object")
    return PlatformConfig(
        path,
        MappingProxyType(environment),
        MappingProxyType(cast(dict[str, object], payload["downloads"])),
    )


def _resolve_data_root(value: str, home: str) -> str:
    value = value.replace("${HOME}", home)
    if value == "~" or value.startswith("~/"):
        value = home + value[1:]
    if "${" in value:
        raise ConfigurationError("Unresolved DATA_PLATFORM_ROOT substitution")
    return value


def resolve_environment(config: PlatformConfig, inherited: Mapping[str, str]) -> dict[str, str]:
    result = dict(inherited)
    for name, value in config.environment.items():
        if value is not None:
            result.setdefault(name, value)
    if "DATA_PLATFORM_ROOT" in result:
        result["DATA_PLATFORM_ROOT"] = _resolve_data_root(
            result["DATA_PLATFORM_ROOT"], result.get("HOME") or str(Path.home())
        )
    result["DATA_PLATFORM_CONFIG"] = str(config.path)
    result["DATA_PLATFORM_CONFIG_LOADED"] = str(config.path)
    return result


def apply_config_environment(path: Path | None = None) -> Path | None:
    selected = path if path is not None else resolve_config_path(os.environ)
    if selected is None:
        return None
    config = load_config(selected)
    os.environ.update(resolve_environment(config, os.environ))
    return config.path
