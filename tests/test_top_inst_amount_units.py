"""Provider yuan amounts must match derived turnover units."""

import pandas as pd
import pytest

from quant_market_data_platform.providers.tushare_a_share_ownership_features import (
    build_a_share_top_inst_events,
)


@pytest.mark.parametrize("net_buy", [10000.0, None])
def test_top_inst_yuan_amounts_convert_to_ten_thousand_yuan(tmp_path, net_buy):
    raw = tmp_path / "raw/data/trade_date=20260930/part.parquet"
    daily = tmp_path / "daily/data/trade_date=20260930/part.parquet"
    raw.parent.mkdir(parents=True)
    daily.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260930",
                "exalter": "institution",
                "buy": 20000.0,
                "sell": 10000.0,
                "net_buy": net_buy,
                "buy_rate": 20.0,
                "sell_rate": 10.0,
            }
        ]
    ).to_parquet(raw, index=False)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260930",
                "amount": 100.0,
            }
        ]
    ).to_parquet(daily, index=False)
    output = tmp_path / "features"
    manifest = build_a_share_top_inst_events(
        top_inst_dir=tmp_path / "raw",
        daily_dir=tmp_path / "daily",
        out_dir=output,
    )
    row = pd.read_parquet(output / "data/trade_date=20260930/part.parquet").iloc[0]
    assert row["top_inst_buy"] == pytest.approx(2.0)
    assert row["top_inst_sell"] == pytest.approx(1.0)
    assert row["top_inst_net_buy"] == pytest.approx(1.0)
    assert row["top_inst_net_buy_to_amount"] == pytest.approx(0.1)
    assert row["top_inst_net_buy_20d"] == pytest.approx(1.0)
    assert row["top_inst_net_buy_20d_to_amount"] == pytest.approx(0.1)
    assert row["top_inst_buy_rate"] == pytest.approx(20.0)
    assert row["top_inst_event_count"] == 1.0
    assert "divided by 10000" in manifest["semantics"]["amount_unit"]
