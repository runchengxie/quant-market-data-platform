"""Validated, date-scoped provider no-data exclusions for acquisition only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from market_data_platform._tushare_minute_campaign_readiness import (
    file_sha256,
)

SCHEMA_VERSION = "market_data_platform.minute_provider_no_data_exceptions.v1"


class ProviderNoDataExceptionError(ValueError):
    """Raised when an exception policy is not safe to use at runtime."""


@dataclass(frozen=True)
class ProviderNoDataExclusions:
    trade_date: str
    codes: tuple[str, ...]
    policy_sha256: str
    path: str


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderNoDataExceptionError(f"Cannot read provider no-data policy: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ProviderNoDataExceptionError(f"Invalid provider no-data policy schema: {path}")
    return payload


def load_provider_no_data_exclusions(
    path: str | Path,
    *,
    trade_date: str,
) -> ProviderNoDataExclusions:
    """Load exclusions only when explicitly enabled for acquisition.

    This policy can reduce the acquisition request universe for a known provider
    omission. It never enables synthetic bars or canonical/promotion readiness.
    """
    policy_path = Path(path).expanduser().resolve()
    payload = _read(policy_path)
    policy = payload.get("policy")
    exceptions = payload.get("exceptions")
    if not isinstance(policy, dict) or not isinstance(exceptions, list):
        raise ProviderNoDataExceptionError(f"Malformed provider no-data policy: {policy_path}")
    if policy.get("allow_acquisition_exclusion") is not True:
        raise ProviderNoDataExceptionError(
            "Provider no-data policy does not explicitly allow acquisition exclusion"
        )
    if any(
        policy.get(key) is not False
        for key in (
            "allow_exclusion_from_provider_verified_universe",
            "allow_synthetic_bars",
            "allow_complete_partition_promotion",
        )
    ):
        raise ProviderNoDataExceptionError(
            f"Provider no-data policy weakens fail-closed guarantees: {policy_path}"
        )
    codes: set[str] = set()
    for item in exceptions:
        if not isinstance(item, dict):
            raise ProviderNoDataExceptionError(f"Malformed provider exception: {policy_path}")
        code = str(item.get("ts_code", "")).strip().upper()
        dates = item.get("observed_dates")
        if not code or not isinstance(dates, list) or not dates:
            raise ProviderNoDataExceptionError(f"Malformed provider exception: {policy_path}")
        if any(len(str(value)) != 8 or not str(value).isdigit() for value in dates):
            raise ProviderNoDataExceptionError(f"Invalid provider exception dates: {policy_path}")
        if str(trade_date) in {str(value) for value in dates}:
            if (
                item.get("http_status") != 200
                or item.get("provider_code") != 0
                or item.get("provider_items") != 0
            ):
                raise ProviderNoDataExceptionError(
                    f"Provider exception lacks no-data evidence: {code} {trade_date}"
                )
            if code in codes:
                raise ProviderNoDataExceptionError(f"Duplicate provider exception: {code}")
            codes.add(code)
    return ProviderNoDataExclusions(
        trade_date=str(trade_date),
        codes=tuple(sorted(codes)),
        policy_sha256=file_sha256(policy_path),
        path=str(policy_path),
    )


__all__ = [
    "ProviderNoDataExceptionError",
    "ProviderNoDataExclusions",
    "load_provider_no_data_exclusions",
]
