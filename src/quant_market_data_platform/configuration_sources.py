"""Separate shared credential references, connection settings and acquisition jobs."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import cast

from quant_market_data_platform.configuration import (
    ConfigurationError,
    PlatformConfig,
    _read_private_json,
    _reject_constant,
    _resolve_data_root,
    _unique_object,
    _validate_environment,
)


def read_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            _read_private_json(path),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError:
        raise ConfigurationError("Selected JSON contains invalid JSON") from None
    if not isinstance(value, dict):
        raise ConfigurationError("Selected JSON must be an object")
    return cast(dict[str, object], value)


def referenced_path(value: object, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ConfigurationError("Configuration reference must select a file")
    path = Path(_resolve_data_root(value, os.environ.get("HOME") or str(Path.home())))
    return (path if path.is_absolute() else base / path).absolute()


def load_split_config(path: Path, payload: dict[str, object]) -> PlatformConfig:
    if set(payload) != {"schema_version", "environment", "credentials", "providers", "jobs"}:
        raise ConfigurationError(
            "Split configuration requires environment, credentials, providers, jobs"
        )
    environment = _validate_environment(payload["environment"])
    refs = payload["credentials"]
    if not isinstance(refs, dict) or set(refs) != {"path", "keys"}:
        raise ConfigurationError("credentials requires path and keys")
    names = refs["keys"]
    if not isinstance(names, dict) or any(
        not isinstance(k, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
        or not isinstance(v, str)
        or not v
        for k, v in names.items()
    ):
        raise ConfigurationError(
            "Credential references require valid environment names and registry keys"
        )
    if environment.keys() & names.keys():
        raise ConfigurationError(
            "Credential references cannot duplicate inline environment settings"
        )
    registry = read_object(referenced_path(refs["path"], path.parent))
    for name, key in names.items():
        if key not in registry:
            raise ConfigurationError("Referenced credential registry key is missing")
        environment.update(_validate_environment({name: registry[key]}))
    providers, jobs = payload["providers"], payload["jobs"]
    if not isinstance(providers, dict) or not isinstance(jobs, dict):
        raise ConfigurationError("providers and jobs must be objects")
    if any(
        not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in jobs.items()
    ):
        raise ConfigurationError("jobs must map provider names to file references")
    return PlatformConfig(
        path,
        MappingProxyType(environment),
        MappingProxyType({}),
        MappingProxyType(providers),
        MappingProxyType(jobs),
        schema_version=2,
    )


def quantzone_settings(config: PlatformConfig, job: Path | None) -> dict[str, object]:
    if not config.providers and job is None:
        settings = config.downloads.get("quantzone")
        if not isinstance(settings, dict):
            raise ConfigurationError("Select a QuantZone acquisition job")
        return cast(dict[str, object], settings)
    source = (
        job
        if job is not None
        else referenced_path(config.jobs.get("quantzone"), config.path.parent)
    )
    payload = read_object(source.expanduser().absolute())
    if (
        set(payload) != {"schema_version", "quantzone"}
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
    ):
        raise ConfigurationError("Unsupported acquisition job schema")
    settings = payload["quantzone"]
    if not isinstance(settings, dict) or set(settings) != {
        "query",
        "batch",
        "retry",
        "output",
        "evidence",
    }:
        raise ConfigurationError("QuantZone job requires query, batch, retry, output, evidence")
    connection = config.providers.get("quantzone")
    if connection is None:
        legacy = config.downloads.get("quantzone", {})
        connection = {
            k: legacy[k]
            for k in ("sdk_version", "timeout_seconds")
            if isinstance(legacy, Mapping) and k in legacy
        }
    if not isinstance(connection, dict) or set(connection) != {"sdk_version", "timeout_seconds"}:
        raise ConfigurationError("QuantZone connection requires sdk_version and timeout_seconds")
    return {**settings, **connection}
