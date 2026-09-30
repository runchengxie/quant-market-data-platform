"""Shared TuShare provider utilities.

This module centralizes helpers that multiple TuShare provider modules already depend on
to avoid duplicated private helper logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .tushare_a_share import (
    DEFAULT_DISABLE_PROXY,
    DEFAULT_QUOTA_COOLDOWN_SECONDS,
    DEFAULT_REQUEST_ATTEMPTS,
    DEFAULT_RETRY_MAX_SLEEP_SECONDS,
    DEFAULT_RETRY_SLEEP_SECONDS,
    TRADE_DATE_APIS,
    TushareRequestPolicy,
    _configure_tushare_client_api_url,
    _normalize_ts_code,
    _pandas,
    _request_policy,
    _request_policy_payload,
    _validate_date,
    _write_frame,
    _write_manifest,
    get_tushare_client,
    mirror_a_share_trade_date_dataset,
    resolve_tushare_api_url,
)

normalize_ts_code = _normalize_ts_code
pandas = _pandas
validate_date = _validate_date
write_frame = _write_frame
configure_tushare_client_api_url = _configure_tushare_client_api_url
request_policy = _request_policy
request_policy_payload = _request_policy_payload


__all__ = [
    "DEFAULT_DISABLE_PROXY",
    "DEFAULT_QUOTA_COOLDOWN_SECONDS",
    "DEFAULT_REQUEST_ATTEMPTS",
    "DEFAULT_RETRY_MAX_SLEEP_SECONDS",
    "DEFAULT_RETRY_SLEEP_SECONDS",
    "TRADE_DATE_APIS",
    "normalize_ts_code",
    "pandas",
    "validate_date",
    "write_frame",
    "write_manifest",
    "configure_tushare_client_api_url",
    "request_policy",
    "request_policy_payload",
    "mirror_a_share_trade_date_dataset",
    "get_tushare_client",
    "resolve_tushare_api_url",
    "TushareRequestPolicy",
]


def write_tushare_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Explicit internal shim for writing standardized TuShare manifests."""
    _write_manifest(path, manifest)


write_manifest = _write_tushare_manifest = write_tushare_manifest
