from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .symbols import PROVIDER_SYMBOL_PRIORITY, drop_legacy_symbol_columns, ensure_symbol_columns


@dataclass(frozen=True)
class FundamentalsCacheFileRequest:
    cache_dir: Path
    market: str
    provider: str
    symbol: str
    start_date: str
    end_date: str
    tag: str | None
    fundamentals_cfg: Mapping


def sanitize_cache_tag(tag: object | None) -> str | None:
    if not tag:
        return None
    text = str(tag).strip()
    if not text:
        return None
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_"})
    return cleaned or None


def cache_tag(data_cfg: Mapping | None) -> str | None:
    if not isinstance(data_cfg, Mapping):
        return None
    tag = data_cfg.get("cache_tag") or data_cfg.get("cache_version")
    return sanitize_cache_tag(tag)


def basic_cache_file(
    cache_dir: Path,
    market: str,
    provider: str,
    symbols: Iterable[str] | None,
    tag: str | None = None,
) -> Path:
    prefix = f"{market}_{provider}"
    if tag:
        prefix = f"{prefix}_{tag}"
    if symbols:
        normalized = "|".join(sorted(str(sym) for sym in symbols))
        digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]
        return cache_dir / f"{prefix}_basic_{digest}.parquet"
    return cache_dir / f"{prefix}_basic.parquet"


def fundamentals_cache_file(request: FundamentalsCacheFileRequest) -> Path:
    prefix = f"{request.market}_{request.provider}"
    if request.tag:
        prefix = f"{prefix}_{request.tag}"
    cache_payload = {
        "endpoint": request.fundamentals_cfg.get("endpoint"),
        "fields": request.fundamentals_cfg.get("fields"),
        "params": request.fundamentals_cfg.get("params") or {},
        "column_map": request.fundamentals_cfg.get("column_map") or {},
    }
    cache_digest = hashlib.md5(
        json.dumps(cache_payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return request.cache_dir / (
        f"{prefix}_fundamentals_{request.symbol}_{request.start_date}_"
        f"{request.end_date}_{cache_digest}.parquet"
    )


def drop_legacy_symbol_aliases(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None:
        return None
    return drop_legacy_symbol_columns(df)


def read_symbol_cache(
    cache_file: Path,
    *,
    symbol: str | None,
    context: str,
) -> pd.DataFrame | None:
    if not cache_file.exists():
        return None
    cached = pd.read_parquet(cache_file)
    if cached is None or cached.empty:
        return cached
    cached = ensure_symbol_columns(
        cached,
        context=context,
        priority=PROVIDER_SYMBOL_PRIORITY,
    )
    if symbol is not None:
        cached = cached.copy()
        cached["symbol"] = symbol
    return drop_legacy_symbol_aliases(cached)


def write_parquet_cache(frame: pd.DataFrame, cache_file: Path) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    frame.copy(deep=True).to_parquet(cache_file)
