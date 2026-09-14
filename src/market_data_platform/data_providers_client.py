"""Provider-agnostic data access helpers for the A-share research workflow."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import data_provider_contracts as _data_provider_contracts
from .data_provider_contracts import require_supported_market as _require_supported_market
from .data_provider_contracts import resolve_provider
from .data_providers_cache import (
    _load_basic_from_local_asset,
    _load_daily_from_local_asset,
)
from .data_providers_frames import (
    _augment_daily_frame,
    _ensure_trade_date_str,
    _force_symbol_value,
    _is_small_leading_calendar_gap,
)
from .provider_cache import (
    FundamentalsCacheFileRequest,
    basic_cache_file,
    cache_tag,
    drop_legacy_symbol_aliases,
    ensure_symbol_columns,
    fundamentals_cache_file,
    sanitize_cache_tag,
    write_parquet_cache,
)
from .symbols import PROVIDER_SYMBOL_PRIORITY

SUPPORTED_MARKETS = _data_provider_contracts.SUPPORTED_MARKETS
fundamentals_provider_supported = _data_provider_contracts.fundamentals_provider_supported

logger = logging.getLogger("market_data_platform.data_providers")

_basic_cache_file = basic_cache_file
_cache_tag = cache_tag
_fundamentals_cache_file = fundamentals_cache_file


@dataclass(frozen=True)
class _RangeDailyFetchRequest:
    provider: str
    market: str
    symbol: str
    start_date: str
    end_date: str
    cache_file: Path
    client: object
    data_cfg: Mapping


@dataclass(frozen=True)
class _ProviderDailyFetchRequest:
    provider: str
    market: str
    symbol: str
    start_date: str
    end_date: str
    client: object
    data_cfg: Mapping


@dataclass(frozen=True)
class _DailyRangeFramesRequest:
    provider: str
    market: str
    symbol: str
    fetch_ranges: Iterable[tuple[str, str]]
    client: object
    data_cfg: Mapping


@dataclass(frozen=True)
class _FinalizeSymbolDailyCacheRequest:
    market: str
    symbol: str
    start_date: str
    end_date: str
    cache_file: Path
    data_cfg: Mapping
    updated: bool


def _fetch_daily_from_provider(request: _ProviderDailyFetchRequest) -> pd.DataFrame:
    local_frame = _load_daily_from_local_asset(
        request.market,
        request.symbol,
        request.start_date,
        request.end_date,
        request.data_cfg,
    )
    if local_frame is not None:
        return local_frame
    raise ValueError(
        f"Unsupported online data provider '{request.provider}'. "
        "Configure provider-local platform assets (for example data.tushare.daily_asset_dir)."
    )


def _daily_cache_prefix(market: str, provider: str, data_cfg: Mapping) -> str:
    tag = cache_tag(data_cfg)
    prefix = f"{market}_{provider}"
    return f"{prefix}_{tag}" if tag else prefix


def _daily_cache_mode(data_cfg: Mapping) -> str:
    return (
        str(data_cfg.get("daily_cache_mode", data_cfg.get("cache_mode", "symbol"))).strip().lower()
    )


def _normalize_cached_daily(cached: pd.DataFrame, *, symbol: str) -> pd.DataFrame:
    cached = ensure_symbol_columns(
        cached,
        context="Cached daily data",
        priority=PROVIDER_SYMBOL_PRIORITY,
    )
    return _force_symbol_value(cached, symbol)


def _drop_legacy_daily_aliases(frame: pd.DataFrame | None) -> pd.DataFrame:
    result = drop_legacy_symbol_aliases(frame)
    return result if result is not None else pd.DataFrame()


def _read_range_daily_cache(
    cache_file: Path,
    *,
    market: str,
    symbol: str,
    data_cfg: Mapping,
) -> pd.DataFrame | None:
    if not cache_file.exists():
        return None
    cached = pd.read_parquet(cache_file)
    if cached is None or cached.empty:
        return _drop_legacy_daily_aliases(cached)
    cached = _normalize_cached_daily(cached, symbol=symbol)
    cached, cache_changed = _augment_daily_frame(
        cached,
        market=market,
        symbol=symbol,
        data_cfg=data_cfg,
    )
    if cache_changed:
        cached = cached.copy(deep=True)
        write_parquet_cache(cached, cache_file)
    return _drop_legacy_daily_aliases(cached)


def _fetch_and_write_range_daily(request: _RangeDailyFetchRequest) -> pd.DataFrame:
    df = _fetch_daily_from_provider(
        _ProviderDailyFetchRequest(
            provider=request.provider,
            market=request.market,
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date,
            client=request.client,
            data_cfg=request.data_cfg,
        )
    )
    if df is None or df.empty:
        return df
    df, _ = _augment_daily_frame(
        df,
        market=request.market,
        symbol=request.symbol,
        data_cfg=request.data_cfg,
    )
    df = df.copy(deep=True)
    write_parquet_cache(df, request.cache_file)
    return df


def _read_symbol_daily_cache(
    cache_file: Path,
    *,
    symbol: str,
) -> tuple[pd.DataFrame | None, list[str]]:
    if not cache_file.exists():
        return None, []
    cached = pd.read_parquet(cache_file)
    cached = _ensure_trade_date_str(cached)
    if cached is not None and not cached.empty:
        cached = _normalize_cached_daily(cached, symbol=symbol)
    if cached is None or cached.empty or "trade_date" not in cached.columns:
        return cached, []
    trade_dates = sorted(cached["trade_date"].unique().tolist())
    return (cached, trade_dates) if trade_dates else (None, [])


def _daily_symbol_fetch_ranges(
    cached: pd.DataFrame | None,
    trade_dates: list[str],
    *,
    start_date: str,
    end_date: str,
    data_cfg: Mapping,
) -> list[tuple[str, str]]:
    if cached is None or cached.empty or not trade_dates:
        return [(start_date, end_date)]

    refresh_days = max(0, int(data_cfg.get("cache_refresh_days", 0) or 0))
    refresh_on_hit = bool(data_cfg.get("cache_refresh_on_hit", False))
    cached_min, cached_max = trade_dates[0], trade_dates[-1]
    fetch_ranges: list[tuple[str, str]] = []

    if start_date < cached_min and not _is_small_leading_calendar_gap(start_date, cached_min):
        left_end = min(end_date, cached_min)
        if start_date <= left_end:
            fetch_ranges.append((start_date, left_end))

    if end_date > cached_max:
        refresh_start = cached_max
        if refresh_days > 0:
            refresh_start = trade_dates[max(0, len(trade_dates) - refresh_days)]
        fetch_ranges.append((max(refresh_start, start_date), end_date))
    elif refresh_on_hit and refresh_days > 0 and end_date >= cached_min:
        refresh_start = trade_dates[max(0, len(trade_dates) - refresh_days)]
        refresh_start = max(refresh_start, start_date)
        if refresh_start <= end_date:
            fetch_ranges.append((refresh_start, end_date))

    return fetch_ranges


def _fetch_daily_range_frames(request: _DailyRangeFramesRequest) -> list[pd.DataFrame]:
    new_frames: list[pd.DataFrame] = []
    for fetch_start, fetch_end in request.fetch_ranges:
        if fetch_start > fetch_end:
            continue
        df_new = _fetch_daily_from_provider(
            _ProviderDailyFetchRequest(
                provider=request.provider,
                market=request.market,
                symbol=request.symbol,
                start_date=fetch_start,
                end_date=fetch_end,
                client=request.client,
                data_cfg=request.data_cfg,
            )
        )
        if df_new is None or df_new.empty:
            continue
        df_new = _ensure_trade_date_str(df_new)
        if df_new is not None and not df_new.empty:
            new_frames.append(df_new)
    return new_frames


def _merge_daily_frames(
    cached: pd.DataFrame | None,
    new_frames: list[pd.DataFrame],
) -> tuple[pd.DataFrame, bool]:
    if cached is None or cached.empty:
        if not new_frames:
            return pd.DataFrame(), False
        merged = pd.concat(new_frames, ignore_index=True) if len(new_frames) > 1 else new_frames[0]
        return merged, True
    if new_frames:
        return pd.concat([cached, *new_frames], ignore_index=True), True
    return cached, False


def _finalize_symbol_daily_cache(
    merged: pd.DataFrame,
    request: _FinalizeSymbolDailyCacheRequest,
) -> pd.DataFrame:
    merged = _ensure_trade_date_str(merged)
    if merged is None or merged.empty:
        return pd.DataFrame()
    merged = ensure_symbol_columns(
        merged,
        context="Daily data",
        priority=PROVIDER_SYMBOL_PRIORITY,
    )
    merged = _force_symbol_value(merged, request.symbol)
    merged, augment_changed = _augment_daily_frame(
        merged,
        market=request.market,
        symbol=request.symbol,
        data_cfg=request.data_cfg,
    )

    if request.updated or augment_changed:
        merged = merged.drop_duplicates(subset=["symbol", "trade_date"], keep="last")
        merged.sort_values(["symbol", "trade_date"], inplace=True)
        merged = merged.copy(deep=True)
        write_parquet_cache(merged, request.cache_file)

    mask = (merged["trade_date"] >= request.start_date) & (merged["trade_date"] <= request.end_date)
    return _drop_legacy_daily_aliases(merged.loc[mask].copy())


def fetch_daily(  # noqa: PLR0913
    market: str,
    symbol: str,
    start_date: str,
    end_date: str,
    cache_dir: Path,
    client,
    data_cfg: Mapping | None = None,
) -> pd.DataFrame:
    market = _require_supported_market(market)
    data_cfg = data_cfg or {}
    provider = resolve_provider(data_cfg)
    assert provider is not None
    start_date = str(start_date).strip()
    end_date = str(end_date).strip()
    prefix = _daily_cache_prefix(market, provider, data_cfg)
    if _daily_cache_mode(data_cfg) in {"range", "window"}:
        cache_file = cache_dir / f"{prefix}_daily_{symbol}_{start_date}_{end_date}.parquet"
        cached = _read_range_daily_cache(
            cache_file,
            market=market,
            symbol=symbol,
            data_cfg=data_cfg,
        )
        if cached is not None:
            return cached
        return _fetch_and_write_range_daily(
            _RangeDailyFetchRequest(
                provider=provider,
                market=market,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                cache_file=cache_file,
                client=client,
                data_cfg=data_cfg,
            )
        )

    cache_file = cache_dir / f"{prefix}_daily_{symbol}.parquet"
    cached, trade_dates = _read_symbol_daily_cache(cache_file, symbol=symbol)
    fetch_ranges = _daily_symbol_fetch_ranges(
        cached,
        trade_dates,
        start_date=start_date,
        end_date=end_date,
        data_cfg=data_cfg,
    )
    new_frames = _fetch_daily_range_frames(
        _DailyRangeFramesRequest(
            provider=provider,
            market=market,
            symbol=symbol,
            fetch_ranges=fetch_ranges,
            client=client,
            data_cfg=data_cfg,
        )
    )
    merged, updated = _merge_daily_frames(cached, new_frames)
    return _finalize_symbol_daily_cache(
        merged,
        _FinalizeSymbolDailyCacheRequest(
            market=market,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            cache_file=cache_file,
            data_cfg=data_cfg,
            updated=updated,
        ),
    )


def load_basic(
    market: str,
    cache_dir: Path,
    client,
    data_cfg: Mapping | None = None,
    symbols: Iterable[str] | None = None,
) -> pd.DataFrame | None:
    market = _require_supported_market(market)
    data_cfg = data_cfg or {}
    provider = resolve_provider(data_cfg)
    assert provider is not None
    tag = cache_tag(data_cfg)
    cache_file = basic_cache_file(cache_dir, market, provider, symbols, tag=tag)
    if cache_file.exists():
        cached = pd.read_parquet(cache_file)
        if cached is None or cached.empty:
            return cached
        return drop_legacy_symbol_aliases(
            ensure_symbol_columns(
                cached,
                context="Cached basic data",
                priority=PROVIDER_SYMBOL_PRIORITY,
            )
        )

    local_basic = _load_basic_from_local_asset(market, symbols, data_cfg)
    if local_basic is not None:
        if local_basic is None or local_basic.empty:
            return local_basic
        # Ensure buffers are writable before parquet serialization.
        local_basic = local_basic.copy(deep=True)
        write_parquet_cache(local_basic, cache_file)
        return local_basic

    raise ValueError(
        f"Unsupported online data provider '{provider}'. "
        "Configure provider-local platform assets (for example data.tushare.instruments_file)."
    )


def fetch_fundamentals(  # noqa: PLR0913
    market: str,
    symbol: str,
    start_date: str,
    end_date: str,
    cache_dir: Path,
    client,
    data_cfg: Mapping | None = None,
    fundamentals_cfg: Mapping | None = None,
) -> pd.DataFrame | None:
    market = _require_supported_market(market)
    data_cfg = data_cfg or {}
    fundamentals_cfg = fundamentals_cfg or {}
    provider = (
        resolve_provider({"provider": fundamentals_cfg.get("provider")})
        if fundamentals_cfg.get("provider")
        else resolve_provider(data_cfg)
    )
    assert provider is not None
    tag = sanitize_cache_tag(
        fundamentals_cfg.get("cache_tag")
        or fundamentals_cfg.get("cache_version")
        or cache_tag(data_cfg)
    )
    cache_file = fundamentals_cache_file(
        FundamentalsCacheFileRequest(
            cache_dir=cache_dir,
            market=market,
            provider=provider,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            tag=tag,
            fundamentals_cfg=fundamentals_cfg,
        )
    )
    if cache_file.exists():
        cached = pd.read_parquet(cache_file)
        if cached is None or cached.empty:
            return cached
        cached = ensure_symbol_columns(
            cached,
            context="Cached fundamentals data",
            priority=PROVIDER_SYMBOL_PRIORITY,
        )
        cached = _force_symbol_value(cached, symbol)
        return drop_legacy_symbol_aliases(cached)

    raise ValueError("Fundamentals provider not supported. Use fundamentals.source=file.")
