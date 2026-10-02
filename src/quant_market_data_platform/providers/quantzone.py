"""Optional QuantZone SDK boundary; no supplier credentials in diagnostics."""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Mapping
from importlib.metadata import version
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from quant_market_data_platform.configuration import (
    ConfigurationError,
    PlatformConfig,
    resolve_environment,
)
from quant_market_data_platform.quantzone_plan import (
    SDK_VERSION,
    FactorDownloadPlan,
    build_factor_plan,
    fixed_date,
)


class FactorClient(Protocol):
    def get_quota(self) -> dict[str, Any]: ...
    def list_factors(self) -> list[dict[str, Any]]: ...
    def list_stocks(self) -> Any: ...
    def get_factors(self, **kwargs: Any) -> Any: ...
    def close(self) -> None: ...


def safe_supplier_error(error: Exception) -> ConfigurationError:
    known = {
        "AuthError",
        "ApiKeyRevokedError",
        "IpNotAllowedError",
        "ApiKeyRateLimitError",
        "QuotaExceededError",
        "NetworkError",
        "SignatureError",
        "SDKVersionError",
        "QuantError",
    }
    category = type(error).__name__
    return ConfigurationError(
        "QuantZone request failed: " + (category if category in known else "supplier_error")
    )


def create_client(config: PlatformConfig, inherited: Mapping[str, str]) -> FactorClient:
    environment = resolve_environment(config, inherited)
    access, secret = (
        environment.get("QUANTZONE_ACCESS_KEY"),
        environment.get("QUANTZONE_SIGN_SECRET"),
    )
    if not access or not secret:
        raise ConfigurationError("QuantZone credentials are not configured")
    endpoint = environment.get("QUANTZONE_BASE_URL", "")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError("QuantZone base URL requires an explicit HTTPS service address")
    plan = build_factor_plan(config, inherited)
    sdk = importlib.import_module("quantzone")
    if version("quantzone") != SDK_VERSION:
        raise ConfigurationError("Installed QuantZone SDK does not match the pinned version")
    try:
        return cast(
            FactorClient,
            sdk.QuantZone(
                access_key=access, sign_secret=secret, base_url=endpoint, timeout=plan.timeout
            ),
        )
    except Exception as error:
        raise safe_supplier_error(error) from None


def _symbol_map(stocks: Any, requested: set[str]) -> dict[str, str]:
    if "ukey" not in stocks.columns:
        raise ConfigurationError("QuantZone stock catalog lacks ukey identifiers")
    matches: dict[str, set[str]] = {}
    for identifier in stocks["ukey"]:
        if not isinstance(identifier, str) or not re.fullmatch(r"\d{6}\.(XSHE|XSHG)", identifier):
            raise ConfigurationError("Invalid stock catalog identifier")
        code, exchange = identifier.split(".")
        matches.setdefault(code, set()).add(code + (".SZ" if exchange == "XSHE" else ".SH"))
    result: dict[str, str] = {}
    for identifier in requested:
        code, exchange = identifier.split(".")
        candidates = matches.get(code, set())
        if len(candidates) != 1:
            raise ConfigurationError("Missing or ambiguous stock catalog identifier")
        symbol = next(iter(candidates))
        if symbol != code + (".SZ" if exchange == "XSHE" else ".SH"):
            raise ConfigurationError("Requested stock exchange disagrees with catalog")
        result[code] = symbol
    return result


def _factor_coverage(
    catalog: list[dict[str, Any]], plan: FactorDownloadPlan
) -> dict[str, dict[str, str]]:
    requested = {factor for batch in plan.batches for factor in batch.factors}
    result: dict[str, dict[str, str]] = {}
    for entry in catalog:
        factor = entry.get("factor")
        if factor not in requested:
            continue
        if factor in result:
            raise ConfigurationError("Duplicate factor catalog identifier")
        start, end = fixed_date(entry.get("startDate")), fixed_date(entry.get("endDate"))
        selected = [batch for batch in plan.batches if factor in batch.factors]
        if start > min(batch.start_date for batch in selected) or end < max(
            batch.end_date for batch in selected
        ):
            raise ConfigurationError("Requested dates exceed factor catalog coverage")
        result[factor] = {"start_date": str(start), "end_date": str(end)}
    if set(result) != requested:
        raise ConfigurationError("Requested factor is absent from the current catalog")
    return result


def check_quantzone(client: FactorClient, plan: FactorDownloadPlan) -> dict[str, Any]:
    try:
        quota = client.get_quota()
        catalog = client.list_factors()
        stocks = client.list_stocks()
    except Exception as error:
        raise safe_supplier_error(error) from None
    available = quota.get("available_bytes")
    if (
        (not isinstance(available, (int, float)) or isinstance(available, bool))
        or not math.isfinite(available)
        or available <= 0
    ):
        raise ConfigurationError("QuantZone available quota must be positive")
    return {
        "quota_available": True,
        "sdk_version": SDK_VERSION,
        "symbol_map": _symbol_map(
            stocks, {stock for batch in plan.batches for stock in batch.ukeys}
        ),
        "factor_coverage": _factor_coverage(catalog, plan),
    }
