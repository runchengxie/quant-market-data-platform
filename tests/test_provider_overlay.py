from __future__ import annotations

import pandas as pd

from market_data_platform.provider_overlay import select_daily_clean_overlay_columns


def test_select_daily_clean_overlay_columns_keeps_configured_values() -> None:
    panel = pd.DataFrame(
        {
            "trade_date": ["2020-01-01"],
            "symbol": ["000001.SZ"],
            "market_cap": [100.0],
            "pe_ttm": [12.0],
            "pb": [1.5],
            "custom_value": [3.0],
            "ignored": [4.0],
        }
    )

    selected = select_daily_clean_overlay_columns(
        panel,
        {"features": ["custom_value"]},
        "market_cap",
    )

    assert selected.columns.tolist() == [
        "trade_date",
        "symbol",
        "custom_value",
        "market_cap",
        "pb",
        "pe_ttm",
    ]
