"""Frame-normalization and standardization helpers for provider data."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from .artifacts import resolve_data_input_path
from .data_provider_contracts import resolve_provider
from .symbols import (
    PROVIDER_SYMBOL_PRIORITY,
    ensure_symbol_columns,
    normalize_symbol_standard_name,
)

FUNDAMENTAL_COLUMN_CANDIDATES = {
    "trade_date": ["trade_date", "date", "trade_dt", "trade_day"],
    "symbol": ["symbol", "ts_code", "ticker", "code", "sec_code", "tscode", "order_book_id"],
}

DEFAULT_COLUMN_MAPS = {
    "a_share": {
        "trade_date": "trade_date",
        "symbol": "symbol",
        "close": "close",
        "vol": "volume",
        "amount": "total_turnover",
    },
}

COLUMN_CANDIDATES = {
    "trade_date": ["trade_date", "date", "trade_dt", "trade_day"],
    "symbol": ["symbol", "ts_code", "ticker", "code", "sec_code", "tscode"],
    "close": ["close", "close_price", "adj_close", "close_adj", "cls"],
    "vol": ["vol", "volume", "trade_vol", "volume_traded"],
    "amount": ["amount", "turnover", "total_turnover", "trade_value", "value"],
}

FUNDAMENTAL_REQUIRED_COLUMNS = ("trade_date", "symbol")
REQUIRED_DAILY_COLUMNS = ("trade_date", "symbol", "close", "vol")


def _normalize_trade_date_series(series: pd.Series) -> pd.Series:
    if series.empty:
        return series.astype(str)
    if pd.api.types.is_datetime64_any_dtype(series):
        return series.dt.strftime("%Y%m%d")
    parsed = pd.to_datetime(series.astype(str), errors="coerce")
    return parsed.dt.strftime("%Y%m%d")


def _ensure_trade_date_str(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "trade_date" not in df.columns:
        return df
    df = df.copy()
    df["trade_date"] = _normalize_trade_date_series(df["trade_date"])
    return df[df["trade_date"].notna()].copy()


def _is_small_leading_calendar_gap(
    start_date: str,
    cached_min: str,
    *,
    max_gap_days: int = 7,
) -> bool:
    """Treat tiny left-edge gaps as likely non-trading days."""
    try:
        start_ts = pd.to_datetime(str(start_date), format="%Y%m%d", errors="raise")
        cached_min_ts = pd.to_datetime(str(cached_min), format="%Y%m%d", errors="raise")
    except Exception:
        return False
    gap_days = int((cached_min_ts.normalize() - start_ts.normalize()).days)
    return 0 < gap_days <= int(max_gap_days)


def _force_symbol_value(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    out["symbol"] = str(symbol).strip()
    return out


def _apply_column_map(df: pd.DataFrame, column_map: Mapping[str, str]) -> pd.DataFrame:
    rename_map = {}
    for standard, source in column_map.items():
        normalized_standard = normalize_symbol_standard_name(standard)
        if (
            source in df.columns
            and normalized_standard != source
            and normalized_standard not in df.columns
        ):
            rename_map[source] = normalized_standard
    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def _infer_missing_columns(df: pd.DataFrame) -> pd.DataFrame:
    for standard, candidates in COLUMN_CANDIDATES.items():
        if standard in df.columns:
            continue
        for candidate in candidates:
            if candidate in df.columns:
                df = df.rename(columns={candidate: standard})
                break
    return df


def _infer_fundamental_columns(df: pd.DataFrame) -> pd.DataFrame:
    for standard, candidates in FUNDAMENTAL_COLUMN_CANDIDATES.items():
        if standard in df.columns:
            continue
        for candidate in candidates:
            if candidate in df.columns:
                df = df.rename(columns={candidate: standard})
                break
    return df


def _standardize_fundamentals_frame(
    df: pd.DataFrame,
    column_map: Mapping[str, str],
    symbol: str,
) -> pd.DataFrame:
    df = _apply_column_map(df, column_map)
    df = _infer_fundamental_columns(df)
    if "symbol" not in df.columns:
        df = df.copy()
        df["symbol"] = symbol
    df = ensure_symbol_columns(
        df,
        context="Fundamentals data",
        priority=PROVIDER_SYMBOL_PRIORITY,
    )
    df = _force_symbol_value(df, symbol)
    from .provider_cache import drop_legacy_symbol_aliases

    cleaned = drop_legacy_symbol_aliases(df)
    assert cleaned is not None
    df = cleaned
    missing = [col for col in FUNDAMENTAL_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Fundamentals data missing required columns: {missing}")
    return df


def _standardize_daily_frame(
    df: pd.DataFrame,
    market: str,
    data_cfg: Mapping,
    symbol: str,
) -> pd.DataFrame:
    df = _apply_column_map(df, _merge_column_map(market, data_cfg))
    df = _infer_missing_columns(df)
    if "symbol" not in df.columns:
        df = df.copy()
        df["symbol"] = symbol
    df = ensure_symbol_columns(
        df,
        context="Daily data",
        priority=PROVIDER_SYMBOL_PRIORITY,
    )
    df = _force_symbol_value(df, symbol)
    missing = [col for col in REQUIRED_DAILY_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Daily data missing required columns: {missing}")
    from .provider_cache import drop_legacy_symbol_aliases

    cleaned = drop_legacy_symbol_aliases(df)
    assert cleaned is not None
    return cleaned


def _merge_column_map(market: str, data_cfg: Mapping) -> dict[str, str]:
    merged = dict(DEFAULT_COLUMN_MAPS.get(market, {}))
    cfg_map = data_cfg.get("column_map") if isinstance(data_cfg, Mapping) else None
    if isinstance(cfg_map, Mapping):
        for key, value in cfg_map.items():
            if value:
                merged[normalize_symbol_standard_name(key)] = str(value)
    return merged


def _input_tr_close_series(
    frame: pd.DataFrame,
    *,
    trade_dates: pd.Series,
) -> pd.Series | None:
    if "tr_close" not in frame.columns:
        return None
    tr_close = pd.to_numeric(frame["tr_close"], errors="coerce")
    tr_close = tr_close.where(trade_dates.notna())
    tr_close.name = "tr_close"
    return tr_close


def _rqdata_adjust_type(data_cfg: Mapping | None) -> str | None:
    if not isinstance(data_cfg, Mapping):
        return None
    rq_cfg = data_cfg.get("rqdata")
    if not isinstance(rq_cfg, Mapping) or "adjust_type" not in rq_cfg:
        return None
    value = str(rq_cfg.get("adjust_type") or "").strip().lower()
    return value or None


def _tr_close_meta(
    source: str,
    *,
    configured_local_ex_factors: bool,
    local_ex_factors_available: bool | None,
    adjust_type: str | None,
) -> dict[str, object]:
    return {
        "source": source,
        "configured_local_ex_factors": configured_local_ex_factors,
        "local_ex_factors_available": local_ex_factors_available,
        "adjust_type": adjust_type,
    }


def _missing_ex_factor_tr_close_payload(
    close: pd.Series,
    input_tr_close: pd.Series | None,
    *,
    adjust_type: str | None,
) -> tuple[pd.Series, dict[str, object]]:
    if input_tr_close is not None:
        return input_tr_close, _tr_close_meta(
            "input_frame_missing_ex_factors",
            configured_local_ex_factors=True,
            local_ex_factors_available=False,
            adjust_type=adjust_type,
        )
    return close.rename("tr_close"), _tr_close_meta(
        "close_fallback_missing_ex_factors",
        configured_local_ex_factors=True,
        local_ex_factors_available=False,
        adjust_type=adjust_type,
    )


def _local_ex_factor_tr_close(
    close: pd.Series,
    trade_dates: pd.Series,
    ex_factors: pd.DataFrame,
) -> pd.Series:
    ex_dates = ex_factors["ex_date"].to_numpy(dtype="datetime64[ns]")
    trade_values = trade_dates.to_numpy(dtype="datetime64[ns]")
    ex_cum_values = ex_factors["ex_cum_factor"].to_numpy(dtype=float)
    idx = np.searchsorted(ex_dates, trade_values, side="right") - 1
    period_cum = np.ones(len(close), dtype=float)
    valid_idx = idx >= 0
    period_cum[valid_idx] = ex_cum_values[idx[valid_idx]]
    tr_close = close * pd.Series(period_cum, index=close.index, dtype=float)
    tr_close = tr_close.where(trade_dates.notna())
    tr_close.name = "tr_close"
    return tr_close


def _resolve_local_path(path_text: object, *, label: str) -> Path | None:
    if path_text in {None, ""}:
        return None
    path = resolve_data_input_path(str(path_text))
    if not path.exists():
        raise SystemExit(f"{label} not found: {path}")
    return path


def _provider_local_cfg(data_cfg: Mapping | None, provider: str | None = None) -> Mapping | None:
    if not isinstance(data_cfg, Mapping):
        return None
    selected = str(provider or resolve_provider(data_cfg) or "").strip().lower()
    provider_cfg = data_cfg.get(selected) if selected else None
    return provider_cfg if isinstance(provider_cfg, Mapping) else None


def _resolve_local_ex_factors_dir(data_cfg: Mapping | None) -> Path | None:
    if not isinstance(data_cfg, Mapping):
        return None
    provider = resolve_provider(data_cfg)
    provider_cfg = _provider_local_cfg(data_cfg, provider)
    candidates = []
    if isinstance(provider_cfg, Mapping):
        candidates.extend([provider_cfg.get("ex_factors_dir"), provider_cfg.get("ex_factor_dir")])
    candidates.append(data_cfg.get("ex_factors_dir"))
    candidates.append(data_cfg.get("ex_factor_dir"))
    label = f"Local {str(provider or 'provider').upper()} ex-factors asset path"
    for candidate in candidates:
        if not candidate:
            continue
        root = _resolve_local_path(candidate, label=label)
        if root is not None:
            return root
    return None


def _normalize_reference_date_frame(
    frame: pd.DataFrame,
    *,
    date_col: str,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=pd.Index([date_col]))
    work = frame.copy()
    if date_col not in work.columns:
        if isinstance(work.index, pd.MultiIndex) and date_col in work.index.names:
            work = work.reset_index()
        elif work.index.name == date_col:
            work = work.reset_index()
    if date_col not in work.columns:
        return pd.DataFrame(columns=pd.Index([date_col]))
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    return work[work[date_col].notna()].copy()


def _load_local_ex_factors_frame(symbol: str, data_cfg: Mapping | None) -> pd.DataFrame | None:
    asset_dir = _resolve_local_ex_factors_dir(data_cfg)
    if asset_dir is None:
        return None
    asset_path = asset_dir / "data" / f"{symbol}.parquet"
    if not asset_path.exists():
        return pd.DataFrame(columns=pd.Index(["ex_date", "ex_cum_factor"]))
    frame = _normalize_reference_date_frame(pd.read_parquet(asset_path), date_col="ex_date")
    if frame.empty:
        return pd.DataFrame(columns=pd.Index(["ex_date", "ex_cum_factor"]))
    if "ex_cum_factor" not in frame.columns:
        if "ex_factor" not in frame.columns:
            return pd.DataFrame(columns=pd.Index(["ex_date", "ex_cum_factor"]))
        frame["ex_cum_factor"] = pd.to_numeric(frame["ex_factor"], errors="coerce").cumprod()
    frame["ex_cum_factor"] = pd.to_numeric(frame["ex_cum_factor"], errors="coerce")
    frame = frame[(np.isfinite(frame["ex_cum_factor"])) & (frame["ex_cum_factor"] > 0)][
        ["ex_date", "ex_cum_factor"]
    ].copy()
    if frame.empty:
        return pd.DataFrame(columns=pd.Index(["ex_date", "ex_cum_factor"]))
    frame = frame.sort_values("ex_date").drop_duplicates(subset=["ex_date"], keep="last")
    return frame.reset_index(drop=True)


def _build_tr_close_payload(
    frame: pd.DataFrame,
    *,
    symbol: str,
    data_cfg: Mapping | None,
) -> tuple[pd.Series | None, dict[str, object] | None]:
    if (
        frame is None
        or frame.empty
        or "close" not in frame.columns
        or "trade_date" not in frame.columns
    ):
        return None, None
    close = pd.to_numeric(frame["close"], errors="coerce")
    trade_dates = pd.to_datetime(frame["trade_date"], errors="coerce")
    input_tr_close = _input_tr_close_series(frame, trade_dates=trade_dates)
    adjust_type = _rqdata_adjust_type(data_cfg)

    ex_factors = _load_local_ex_factors_frame(symbol, data_cfg)
    if ex_factors is not None:
        if ex_factors.empty:
            return _missing_ex_factor_tr_close_payload(
                close,
                input_tr_close,
                adjust_type=adjust_type,
            )
        return _local_ex_factor_tr_close(close, trade_dates, ex_factors), _tr_close_meta(
            "local_ex_factors",
            configured_local_ex_factors=True,
            local_ex_factors_available=True,
            adjust_type=adjust_type,
        )

    if adjust_type in {"pre", "post", "pre_volume", "post_volume"}:
        return close.rename("tr_close"), _tr_close_meta(
            "provider_adjusted_price",
            configured_local_ex_factors=False,
            local_ex_factors_available=None,
            adjust_type=adjust_type,
        )
    if input_tr_close is not None:
        return input_tr_close, _tr_close_meta(
            "input_frame",
            configured_local_ex_factors=False,
            local_ex_factors_available=None,
            adjust_type=adjust_type,
        )
    return None, _tr_close_meta(
        "unavailable",
        configured_local_ex_factors=False,
        local_ex_factors_available=None,
        adjust_type=adjust_type,
    )


def _augment_daily_frame(
    df: pd.DataFrame,
    *,
    market: str,
    symbol: str,
    data_cfg: Mapping | None,
) -> tuple[pd.DataFrame, bool]:
    if df is None or df.empty:
        return df, False
    out = df.copy()
    changed = False

    tr_close, tr_close_meta = _build_tr_close_payload(out, symbol=symbol, data_cfg=data_cfg)
    if tr_close is not None:
        existing = out["tr_close"].copy() if "tr_close" in out.columns else None
        if existing is None or not existing.equals(tr_close):
            out["tr_close"] = tr_close
            changed = True
    if tr_close_meta is not None:
        out.attrs["tr_close_meta"] = {"symbol": symbol, **tr_close_meta}

    return out, changed
