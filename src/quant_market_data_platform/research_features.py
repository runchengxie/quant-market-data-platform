"""Research-ready market and microstructure features with explicit data requirements."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BarBuildConfig:
    kind: str
    threshold: float
    price_col: str = "price"
    volume_col: str = "volume"
    time_col: str = "timestamp"
    symbol_col: str = "symbol"

    def __post_init__(self) -> None:
        if self.kind not in {"tick", "volume", "dollar"}:
            raise ValueError("kind must be one of: tick, volume, dollar")
        if self.threshold <= 0:
            raise ValueError("threshold must be > 0")


def tick_rule(prices: pd.Series, *, initial_side: int = 1) -> pd.Series:
    """Classify trade aggressor side from price changes."""

    if initial_side not in {-1, 1}:
        raise ValueError("initial_side must be -1 or 1")
    values = pd.to_numeric(prices, errors="coerce")
    changes = values.diff()
    side = pd.Series(np.nan, index=values.index, dtype=float)
    side.loc[changes > 0] = 1.0
    side.loc[changes < 0] = -1.0
    if not side.empty:
        side.iloc[0] = float(initial_side)
    return side.ffill().fillna(float(initial_side)).astype(np.int8).rename("aggressor_side")


def build_activity_bars(
    trades: pd.DataFrame,
    *,
    config: BarBuildConfig,
) -> pd.DataFrame:
    """Aggregate trades into tick, volume, or dollar bars by symbol."""

    required = (config.symbol_col, config.time_col, config.price_col, config.volume_col)
    _require_columns(trades, required, "trades")
    data = trades[list(required)].copy()
    data[config.time_col] = pd.to_datetime(data[config.time_col], errors="coerce", utc=True)
    data[config.price_col] = pd.to_numeric(data[config.price_col], errors="coerce")
    data[config.volume_col] = pd.to_numeric(data[config.volume_col], errors="coerce")
    data = data.dropna().sort_values([config.symbol_col, config.time_col], kind="mergesort")
    if bool((data[config.price_col] <= 0).any()) or bool((data[config.volume_col] <= 0).any()):
        raise ValueError("trade price and volume must be positive")

    records: list[dict[str, object]] = []
    for symbol, group in data.groupby(config.symbol_col, sort=False):
        group = group.reset_index(drop=True)
        accumulator = 0.0
        start = 0
        bar_number = 0
        for position, row in group.iterrows():
            if config.kind == "tick":
                increment = 1.0
            elif config.kind == "volume":
                increment = float(row[config.volume_col])
            else:
                increment = float(row[config.price_col]) * float(row[config.volume_col])
            accumulator += increment
            if accumulator < config.threshold and position < len(group) - 1:
                continue
            window = group.iloc[start : position + 1]
            prices = window[config.price_col].astype(float)
            volumes = window[config.volume_col].astype(float)
            notional = prices * volumes
            records.append(
                {
                    "symbol": symbol,
                    "bar_number": bar_number,
                    "bar_start": window.iloc[0][config.time_col],
                    "bar_end": window.iloc[-1][config.time_col],
                    "open": float(prices.iloc[0]),
                    "high": float(prices.max()),
                    "low": float(prices.min()),
                    "close": float(prices.iloc[-1]),
                    "volume": float(volumes.sum()),
                    "notional": float(notional.sum()),
                    "vwap": float(notional.sum() / volumes.sum()),
                    "trade_count": int(len(window)),
                    "bar_kind": config.kind,
                    "bar_threshold": float(config.threshold),
                }
            )
            bar_number += 1
            start = position + 1
            accumulator = 0.0
    return pd.DataFrame.from_records(records)


def parkinson_volatility(
    high: pd.Series,
    low: pd.Series,
    *,
    window: int = 20,
    annualization: float = 252.0,
) -> pd.Series:
    """Estimate volatility from the high-low range."""

    if window <= 1 or annualization <= 0:
        raise ValueError("window and annualization must be positive")
    high_values = pd.to_numeric(high, errors="coerce")
    low_values = pd.to_numeric(low, errors="coerce")
    log_range_sq = np.square(np.log(high_values / low_values))
    variance = log_range_sq.rolling(window, min_periods=window).mean() / (4.0 * np.log(2.0))
    return np.sqrt(variance * annualization).rename("parkinson_volatility")


def corwin_schultz_spread(
    high: pd.Series,
    low: pd.Series,
    *,
    window: int = 20,
) -> pd.Series:
    """Estimate effective bid-ask spread from high-low prices."""

    if window <= 1:
        raise ValueError("window must be > 1")
    high_values = pd.to_numeric(high, errors="coerce")
    low_values = pd.to_numeric(low, errors="coerce")
    log_hl_sq = np.square(np.log(high_values / low_values))
    beta = (log_hl_sq + log_hl_sq.shift(1)).rolling(window, min_periods=window).mean()
    two_day_high = pd.concat([high_values, high_values.shift(1)], axis=1).max(axis=1)
    two_day_low = pd.concat([low_values, low_values.shift(1)], axis=1).min(axis=1)
    gamma = np.square(np.log(two_day_high / two_day_low))
    denominator = 3.0 - 2.0 * np.sqrt(2.0)
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / denominator - np.sqrt(gamma / denominator)
    alpha = alpha.clip(lower=0.0)
    spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    return spread.rename("corwin_schultz_spread")


def amihud_illiquidity(
    close: pd.Series,
    amount: pd.Series,
    *,
    window: int = 20,
) -> pd.Series:
    """Estimate daily Amihud illiquidity from absolute returns per notional."""

    if window <= 1:
        raise ValueError("window must be > 1")
    prices = pd.to_numeric(close, errors="coerce")
    notional = pd.to_numeric(amount, errors="coerce").replace(0.0, np.nan)
    daily = prices.pct_change().abs().div(notional)
    return daily.rolling(window, min_periods=window).mean().rename("amihud_illiquidity")


def build_daily_microstructure_features(  # noqa: PLR0913
    daily: pd.DataFrame,
    *,
    symbol_col: str = "symbol",
    time_col: str = "trade_date",
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
    amount_col: str = "amount",
    window: int = 20,
) -> pd.DataFrame:
    """Build low-frequency liquidity features that are valid from OHLCV data."""

    required = (symbol_col, time_col, high_col, low_col, close_col, amount_col)
    _require_columns(daily, required, "daily")
    data = daily[list(required)].copy()
    data[time_col] = pd.to_datetime(data[time_col], errors="coerce")
    data = data.sort_values([symbol_col, time_col], kind="mergesort")
    outputs: list[pd.DataFrame] = []
    for symbol, group in data.groupby(symbol_col, sort=False):
        group = group.copy()
        group["parkinson_volatility"] = parkinson_volatility(
            group[high_col],
            group[low_col],
            window=window,
        )
        group["corwin_schultz_spread"] = corwin_schultz_spread(
            group[high_col],
            group[low_col],
            window=window,
        )
        group["amihud_illiquidity"] = amihud_illiquidity(
            group[close_col],
            group[amount_col],
            window=window,
        )
        group["turnover_shock"] = (
            pd.to_numeric(group[amount_col], errors="coerce")
            / pd.to_numeric(group[amount_col], errors="coerce")
            .rolling(window, min_periods=window)
            .median()
            - 1.0
        )
        group["symbol"] = symbol
        outputs.append(group)
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()


def feature_receipt(
    frame: pd.DataFrame,
    *,
    feature_columns: list[str],
    source_contract: str,
    asof: object,
) -> dict[str, object]:
    """Build a compact lineage receipt for published feature assets."""

    missing = [column for column in feature_columns if column not in frame.columns]
    if missing:
        raise ValueError(f"feature columns not found: {', '.join(missing)}")
    payload = frame[feature_columns].to_csv(index=False)
    return {
        "schema_version": 1,
        "contract": "market_data_platform.research_features.v1",
        "source_contract": source_contract,
        "asof": str(pd.Timestamp(cast(Any, asof))),
        "rows": len(frame),
        "features": feature_columns,
        "feature_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "null_ratio": {column: float(frame[column].isna().mean()) for column in feature_columns},
    }


def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{name} is missing required columns: {', '.join(missing)}")


__all__ = [
    "BarBuildConfig",
    "amihud_illiquidity",
    "build_activity_bars",
    "build_daily_microstructure_features",
    "corwin_schultz_spread",
    "feature_receipt",
    "parkinson_volatility",
    "tick_rule",
]
