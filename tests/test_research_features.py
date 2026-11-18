from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_market_data_platform.cli import main
from quant_market_data_platform.research_features import (
    BarBuildConfig,
    build_activity_bars,
    build_daily_microstructure_features,
    tick_rule,
)


def test_activity_bars_use_trading_activity_thresholds() -> None:
    trades = pd.DataFrame(
        {
            "symbol": ["A"] * 5,
            "timestamp": pd.date_range(
                "2024-01-01 09:30",
                periods=5,
                freq="min",
                tz="Asia/Shanghai",
            ),
            "price": [10.0, 10.1, 10.2, 10.1, 10.3],
            "volume": [100, 200, 300, 100, 300],
        }
    )
    bars = build_activity_bars(
        trades,
        config=BarBuildConfig(kind="volume", threshold=500),
    )
    assert len(bars) == 2
    assert bars.iloc[0]["volume"] == 600
    assert bars.iloc[0]["trade_count"] == 3
    assert np.isclose(
        bars.iloc[0]["vwap"],
        (10.0 * 100 + 10.1 * 200 + 10.2 * 300) / 600,
    )


@pytest.mark.parametrize("kind,threshold", [("tick", 2), ("volume", 5), ("dollar", 50)])
@pytest.mark.parametrize("numeric_dtype", ["Float64", "string"])
def test_activity_bars_preserve_boundaries_and_order(kind, threshold, numeric_dtype) -> None:
    trades = pd.DataFrame(
        {
            "asset": ["B", "A", "A", "A", "A", "A", "B"],
            "time": [
                "2024-01-01 09:30",
                "2024-01-01 09:32",
                "2024-01-01 09:30",
                "2024-01-01 09:30",
                "invalid",
                "2024-01-01 09:33",
                "2024-01-01 09:31",
            ],
            "px": pd.Series([20, 12, 10, 11, 99, None, 21], dtype=numeric_dtype),
            "qty": pd.Series([1, 1, 2, 3, 1, 1, 1], dtype=numeric_dtype),
        },
        index=[0, 1, 2, 3, 4, 5, 6],
    )
    bars = build_activity_bars(
        trades,
        config=BarBuildConfig(
            kind=kind,
            threshold=threshold,
            symbol_col="asset",
            time_col="time",
            price_col="px",
            volume_col="qty",
        ),
    )
    assert bars["symbol"].tolist() == ["A", "A", "B"]
    assert bars["bar_number"].tolist() == [0, 1, 0]
    assert bars["trade_count"].tolist() == [2, 1, 2]
    assert bars["open"].tolist() == [10, 12, 20]
    assert bars["close"].tolist() == [11, 12, 21]
    assert bars["volume"].tolist() == [5, 1, 2]
    assert bars["notional"].tolist() == [53, 12, 41]
    assert bars["bar_start"].tolist() == list(
        pd.to_datetime(["2024-01-01 09:30", "2024-01-01 09:32", "2024-01-01 09:30"], utc=True)
    )


@pytest.mark.parametrize("kind", ["tick", "volume", "dollar"])
def test_activity_bars_empty_input(kind) -> None:
    trades = pd.DataFrame(columns=["symbol", "timestamp", "price", "volume"])
    assert build_activity_bars(trades, config=BarBuildConfig(kind=kind, threshold=2)).empty


def test_tick_rule_carries_forward_zero_price_changes() -> None:
    prices = pd.Series([10.0, 10.1, 10.1, 10.0])
    assert tick_rule(prices).tolist() == [1, 1, 1, -1]


def _daily_frame() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=40)
    return pd.DataFrame(
        {
            "symbol": ["A"] * 40,
            "trade_date": dates,
            "high": np.linspace(10.1, 12.0, 40),
            "low": np.linspace(9.9, 11.7, 40),
            "close": np.linspace(10.0, 11.9, 40),
            "amount": np.linspace(1_000_000, 2_000_000, 40),
        }
    )


def test_daily_features_only_use_available_ohlcv_columns() -> None:
    features = build_daily_microstructure_features(_daily_frame(), window=10)
    assert features["parkinson_volatility"].notna().sum() > 0
    assert features["corwin_schultz_spread"].dropna().ge(0).all()
    assert features["amihud_illiquidity"].notna().sum() > 0


def test_research_feature_cli_writes_artifact_and_receipt(tmp_path: Path) -> None:
    source = tmp_path / "daily.csv"
    output = tmp_path / "research_features.csv"
    receipt = tmp_path / "research_features.receipt.json"
    _daily_frame().to_csv(source, index=False)

    exit_code = main(
        [
            "research-features",
            "daily",
            "--input",
            str(source),
            "--output",
            str(output),
            "--receipt",
            str(receipt),
            "--source-contract",
            "tushare.a_share.daily_clean.v1",
            "--asof",
            "2024-02-09",
            "--window",
            "10",
        ]
    )

    assert exit_code == 0
    assert output.is_file()
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["contract"] == "market_data_platform.research_features.v1"
    assert payload["rows"] == 40
