"""Provider and market boundary helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

SUPPORTED_MARKETS = {"a_share"}


@dataclass(frozen=True)
class MarketSpec:
    market: str
    canonical_suffixes: tuple[str, ...]


MARKET_SPECS = {
    "a_share": MarketSpec(
        market="a_share",
        canonical_suffixes=(".SH", ".SZ"),
    ),
}


def normalize_market(market: str | None, *, default: str | None = "a_share") -> str | None:
    fallback = None if default is None else str(default).strip().lower() or None
    value = str(market).strip().lower() if market is not None else None
    return value or fallback


def resolve_provider(data_cfg: Mapping | None, *, default: str | None = "tushare") -> str | None:
    if not isinstance(data_cfg, Mapping):
        return default
    raw = data_cfg.get("provider", default)
    if raw is None:
        return None
    value = str(raw).strip().lower()
    return value or default


def fundamentals_provider_supported(provider: str, market: str) -> bool:
    return False


def require_supported_market(market: str) -> str:
    normalized_market = normalize_market(market)
    if normalized_market not in SUPPORTED_MARKETS:
        supported = ", ".join(sorted(SUPPORTED_MARKETS))
        raise ValueError(
            f"Unsupported market '{normalized_market}'. Supported markets: {supported}."
        )
    return normalized_market
