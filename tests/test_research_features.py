from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from market_data_platform.cli import main
from market_data_platform.research_features import (
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
