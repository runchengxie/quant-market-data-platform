from __future__ import annotations

import pandas as pd

from market_data_platform.industry_history import expand_effective_industry_to_panel_dates


def test_expand_effective_industry_to_panel_dates_respects_validity_window() -> None:
    panel = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2020-01-02", "2020-01-06", "2020-01-08"]),
            "symbol": ["000001.SZ"] * 3,
        }
    )
    industry = pd.DataFrame(
        {
            "symbol": ["000001.SZ", "000001.SZ"],
            "effective_date": pd.to_datetime(["2020-01-01", "2020-01-07"]),
            "end_date": pd.to_datetime(["2020-01-06", "2020-01-31"]),
            "industry": ["old", "new"],
        }
    )

    expanded = expand_effective_industry_to_panel_dates(
        industry,
        panel_df=panel,
        industry_cfg={},
    )

    assert expanded["industry"].tolist() == ["old", "old", "new"]
    assert expanded["trade_date"].tolist() == list(
        pd.to_datetime(["2020-01-02", "2020-01-06", "2020-01-08"])
    )
