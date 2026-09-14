"""Local filesystem cache/resolver helpers for data providers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import pandas as pd

from .data_provider_contracts import resolve_provider
from .data_providers_frames import (
    _ensure_trade_date_str,
    _provider_local_cfg,
    _resolve_local_path,
    _standardize_daily_frame,
)
from .provider_cache import drop_legacy_symbol_aliases
from .symbols import (
    PROVIDER_SYMBOL_PRIORITY,
    ensure_symbol_columns,
    normalize_symbol_for_market,
)


def _resolve_local_daily_asset_dir(data_cfg: Mapping | None) -> Path | None:
    if not isinstance(data_cfg, Mapping):
        return None
    provider = resolve_provider(data_cfg)
    provider_cfg = _provider_local_cfg(data_cfg, provider)
    candidates = []
    if isinstance(provider_cfg, Mapping):
        candidates.extend([provider_cfg.get("daily_asset_dir"), provider_cfg.get("asset_dir")])
    candidates.extend([data_cfg.get("daily_asset_dir"), data_cfg.get("asset_dir")])
    label = f"Local {str(provider or 'provider').upper()} daily asset path"
    for candidate in candidates:
        root = _resolve_local_path(candidate, label=label) if candidate else None
        if root is None:
            continue
        if (root / "data").exists():
            return root
        if root.name == "data" and root.is_dir():
            return root.parent
        raise SystemExit(f"{label} is missing data/: {root}")
    return None


def _resolve_local_instruments_file(data_cfg: Mapping | None) -> Path | None:
    if not isinstance(data_cfg, Mapping):
        return None
    provider = resolve_provider(data_cfg)
    provider_cfg = _provider_local_cfg(data_cfg, provider)
    candidates = []
    if isinstance(provider_cfg, Mapping):
        candidates.extend([provider_cfg.get("instruments_file"), provider_cfg.get("basic_file")])
    candidates.extend([data_cfg.get("instruments_file"), data_cfg.get("basic_file")])
    label = f"Local {str(provider or 'provider').upper()} instruments file"
    for candidate in candidates:
        resolved = _resolve_local_path(candidate, label=label) if candidate else None
        if resolved is not None:
            return resolved
    return None


def _read_local_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise SystemExit(f"Unsupported local asset file type: {path}")


def _load_daily_from_local_asset(
    market: str,
    symbol: str,
    start_date: str,
    end_date: str,
    data_cfg: Mapping,
) -> pd.DataFrame | None:
    asset_dir = _resolve_local_daily_asset_dir(data_cfg)
    if asset_dir is None:
        return None
    asset_path = asset_dir / "data" / f"{symbol}.parquet"
    if not asset_path.exists():
        return pd.DataFrame()
    frame = pd.read_parquet(asset_path)
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = _standardize_daily_frame(frame, market, data_cfg, symbol)
    frame = _ensure_trade_date_str(frame)
    if frame is None or frame.empty:
        return pd.DataFrame()
    mask = (frame["trade_date"] >= str(start_date)) & (frame["trade_date"] <= str(end_date))
    return frame.loc[mask].copy()


def _load_basic_from_local_asset(
    market: str,
    symbols: Iterable[str] | None,
    data_cfg: Mapping,
) -> pd.DataFrame | None:
    instruments_file = _resolve_local_instruments_file(data_cfg)
    if instruments_file is None:
        return None
    work = _read_local_table(instruments_file)
    if work is None or work.empty:
        return pd.DataFrame()
    work = work.copy()
    if "name" not in work.columns:
        for candidate in ("symbol", "eng_symbol", "abbrev_symbol", "order_book_id", "ts_code"):
            if candidate in work.columns:
                work["name"] = work[candidate]
                break
    if (
        "order_book_id" in work.columns
        and "symbol" not in work.columns
        and "ts_code" not in work.columns
    ):
        work["symbol"] = work["order_book_id"]
    if "listed_date" in work.columns and "list_date" not in work.columns:
        work["list_date"] = work["listed_date"]
    work = ensure_symbol_columns(
        work,
        context="Local instruments file",
        priority=PROVIDER_SYMBOL_PRIORITY,
    )
    work = drop_legacy_symbol_aliases(work)
    assert work is not None
    required = ["symbol", "name", "list_date"]
    missing = [column for column in required if column not in work.columns]
    if missing:
        raise SystemExit(
            f"Local instruments file is missing required columns {missing}: {instruments_file}"
        )
    work = work[["symbol", "name", "list_date"]].copy()
    work["symbol"] = work["symbol"].map(
        lambda value: normalize_symbol_for_market(value, market=market)
    )
    work["list_date"] = pd.to_datetime(work["list_date"], errors="coerce").dt.strftime("%Y%m%d")
    if symbols:
        work = work[work["symbol"].isin(list(symbols))].copy()
    return work.reset_index(drop=True)
