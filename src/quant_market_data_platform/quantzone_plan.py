"""Deterministic, bounded QuantZone factor acquisition plans."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

from quant_market_data_platform.configuration import (
    ConfigurationError,
    PlatformConfig,
    resolve_environment,
)

SDK_VERSION = "0.10.0"
EVIDENCE = {
    "pit_availability": "unknown",
    "revision_safety": "unknown",
    "public_redistribution": "not_authorized",
}


@dataclass(frozen=True)
class FactorBatch:
    key: str
    ukeys: tuple[str, ...]
    factors: tuple[str, ...]
    start_date: date
    end_date: date


@dataclass(frozen=True)
class FactorDownloadPlan:
    query_identity: str
    batches: tuple[FactorBatch, ...]
    root: Path
    timeout: int
    sdk_version: str = SDK_VERSION
    snapshot: dict[str, Any] = field(default_factory=dict, repr=False)


def object_setting(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ConfigurationError(f"{name} must be an object")
    return cast(dict[str, object], value)


def positive_integer(value: object, name: str, maximum: int) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise ConfigurationError(f"{name} must be a positive integer within its supported limit")
    return value


def fixed_date(value: object) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ConfigurationError("Dates must be fixed ISO calendar dates")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ConfigurationError("Invalid calendar date") from None


def _identifiers(value: object, pattern: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigurationError("Query requires non-empty explicit identifier lists")
    if any(not isinstance(item, str) or not re.fullmatch(pattern, item) for item in value):
        raise ConfigurationError("Invalid query identifier")
    result = cast(list[str], value)
    if len(set(result)) != len(result):
        raise ConfigurationError("Duplicate query identifier")
    return tuple(sorted(result))


def _output_root(
    config: PlatformConfig, settings: dict[str, object], inherited: Mapping[str, str]
) -> Path:
    environment = resolve_environment(config, inherited)
    if not environment.get("DATA_PLATFORM_ROOT"):
        raise ConfigurationError("DATA_PLATFORM_ROOT is required for downloads")
    base = Path(environment["DATA_PLATFORM_ROOT"]).expanduser().resolve()
    if settings.get("format") != "parquet" or settings.get("immutable_runs") is not True:
        raise ConfigurationError("Downloads require immutable Parquet runs")
    relative = settings.get("relative_directory")
    if not isinstance(relative, str):
        raise ConfigurationError("Output requires a relative research directory")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.parts[:1] != ("research",):
        raise ConfigurationError("Output must remain under the data root research directory")
    root = (base / path).resolve()
    if not root.is_relative_to(base) or root == base:
        raise ConfigurationError("Output directory escapes the data root")
    return root


def _batches(query: dict[str, object], settings: dict[str, object]) -> tuple[FactorBatch, ...]:
    ukeys = _identifiers(query.get("ukeys"), r"\d{6}\.(XSHE|XSHG)")
    factors = _identifiers(query.get("factor"), r"[A-Za-z_][A-Za-z0-9_]*")
    if set(factors) & {"symbol", "trade_date", "ukey", "date"}:
        raise ConfigurationError("Factor name conflicts with research projection identifiers")
    start, end = fixed_date(query.get("start_date")), fixed_date(query.get("end_date"))
    if end < start:
        raise ConfigurationError("Query end_date must not precede start_date")
    days = positive_integer(settings.get("calendar_days"), "calendar_days", 365)
    stocks = positive_integer(settings.get("max_symbols"), "max_symbols", 100)
    width = positive_integer(settings.get("max_factors"), "max_factors", 20)
    batches: list[FactorBatch] = []
    while start <= end:
        stop = min(end, start + timedelta(days=days - 1))
        for offset in range(0, len(ukeys), stocks):
            for column in range(0, len(factors), width):
                selected = (ukeys[offset : offset + stocks], factors[column : column + width])
                key = hashlib.sha256(
                    json.dumps([*selected, str(start), str(stop)]).encode()
                ).hexdigest()[:20]
                batches.append(FactorBatch(key, *selected, start, stop))
        start = stop + timedelta(days=1)
    return tuple(batches)


def build_factor_plan(
    config: PlatformConfig, inherited: Mapping[str, str], *, job: Path | None = None
) -> FactorDownloadPlan:
    from quant_market_data_platform.configuration_sources import quantzone_settings

    settings = quantzone_settings(config, job)
    if settings.get("sdk_version") != SDK_VERSION:
        raise ConfigurationError("QuantZone requires the pinned supported SDK version")
    timeout = positive_integer(settings.get("timeout_seconds"), "timeout_seconds", 60)
    retry = object_setting(settings.get("retry"), "retry")
    if type(retry.get("max_attempts")) is not int or retry.get("max_attempts") != 1:
        raise ConfigurationError("QuantZone downloads require exactly one attempt per request")
    if settings.get("evidence") != EVIDENCE:
        raise ConfigurationError("QuantZone evidence must retain the accepted unknown states")
    batches = _batches(
        object_setting(settings.get("query"), "query"),
        object_setting(settings.get("batch"), "batch"),
    )
    root = _output_root(config, object_setting(settings.get("output"), "output"), inherited)
    identity = hashlib.sha256(
        json.dumps(
            {"sdk_version": SDK_VERSION, "batches": [batch.key for batch in batches]},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    snapshot = {
        key: settings[key]
        for key in (
            "sdk_version",
            "timeout_seconds",
            "query",
            "batch",
            "retry",
            "output",
            "evidence",
        )
    }
    fields = {
        "query": {"ukeys", "factor", "start_date", "end_date"},
        "batch": {"calendar_days", "max_symbols", "max_factors"},
        "retry": {"max_attempts"},
        "output": {"relative_directory", "format", "immutable_runs"},
    }
    for name, keys in fields.items():
        if set(object_setting(snapshot[name], name)) != keys:
            raise ConfigurationError("Unexpected acquisition job field")
    return FactorDownloadPlan(identity, batches, root, timeout, snapshot=snapshot)
