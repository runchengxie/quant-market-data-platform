"""Build shared-ledger CLI arguments for minute replacement campaign lanes."""

from __future__ import annotations

import os
from typing import Any

_QUOTA_OPTIONS = (
    (
        "minute_quota_mode",
        "--minute-quota-mode",
        "MDP_TUSHARE_MINUTE_QUOTA_MODE",
    ),
    (
        "minute_quota_db",
        "--minute-quota-db",
        "MDP_TUSHARE_MINUTE_QUOTA_DB",
    ),
    (
        "minute_quota_limit_rows",
        "--minute-quota-limit-rows",
        "MDP_TUSHARE_MINUTE_QUOTA_LIMIT_ROWS",
    ),
    (
        "minute_quota_safety_rows",
        "--minute-quota-safety-rows",
        "MDP_TUSHARE_MINUTE_QUOTA_SAFETY_ROWS",
    ),
    (
        "minute_quota_gate",
        "--minute-quota-gate",
        "MDP_TUSHARE_MINUTE_QUOTA_GATE",
    ),
    (
        "minute_quota_limit_requests",
        "--minute-quota-limit-requests",
        "MDP_TUSHARE_MINUTE_QUOTA_LIMIT_REQUESTS",
    ),
    (
        "minute_quota_burst_limit_requests",
        "--minute-quota-burst-limit-requests",
        "MDP_TUSHARE_MINUTE_QUOTA_BURST_LIMIT_REQUESTS",
    ),
    (
        "minute_quota_safety_requests",
        "--minute-quota-safety-requests",
        "MDP_TUSHARE_MINUTE_QUOTA_SAFETY_REQUESTS",
    ),
)
_ALLOW_BURST_ENV = "MDP_TUSHARE_MINUTE_QUOTA_ALLOW_BURST"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _configured_value(config: dict[str, Any], key: str, environment: str) -> str | None:
    raw = config.get(key)
    if raw is None:
        raw = os.environ.get(environment)
    if raw is None or not str(raw).strip():
        return None
    return str(raw)


def _optional_boolean(raw: Any, *, name: str) -> bool | None:
    if raw is None or not str(raw).strip():
        return None
    if isinstance(raw, bool):
        return raw
    normalized = str(raw).strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean")


def minute_quota_args(config: dict[str, Any]) -> list[str]:
    """Forward optional shared-ledger controls without changing old manifests."""

    configured = [
        (option, value)
        for key, option, environment in _QUOTA_OPTIONS
        if (value := _configured_value(config, key, environment)) is not None
    ]
    raw_allow_burst = config.get("minute_quota_allow_burst")
    if raw_allow_burst is None:
        raw_allow_burst = os.environ.get(_ALLOW_BURST_ENV)
    allow_burst = _optional_boolean(raw_allow_burst, name="minute_quota_allow_burst")
    if not configured and allow_burst is None:
        return []

    arguments = [item for option, value in configured for item in (option, value)]
    if allow_burst is not None:
        arguments.append(
            "--minute-quota-allow-burst" if allow_burst else "--no-minute-quota-allow-burst"
        )
    arguments.extend(
        [
            "--minute-quota-consumer",
            str(config.get("minute_quota_consumer") or "replacement_campaign"),
        ]
    )
    return arguments
