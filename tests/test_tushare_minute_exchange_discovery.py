"""Exchange scope must survive provider universe discovery."""

from __future__ import annotations

import pandas as pd
import pytest

from market_data_platform.providers import tushare_a_share_mins as mins
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy


class DailyUniverse:
    def daily(self, *, trade_date: str, fields: str, limit: int, offset: int) -> pd.DataFrame:
        symbols = ["600000.SH", "000001.SZ", "430047.BJ"] if offset == 0 else []
        return pd.DataFrame({"ts_code": symbols, "trade_date": [trade_date] * len(symbols)})

    daily_basic = daily


@pytest.mark.parametrize(
    ("exchange", "expected"),
    [
        ("SH_SZ", {"600000.SH", "000001.SZ"}),
        ("SH", {"600000.SH"}),
        ("SZ", {"000001.SZ"}),
        ("BJ", {"430047.BJ"}),
        (None, {"600000.SH", "000001.SZ", "430047.BJ"}),
    ],
)
def test_discovery_preserves_the_requested_exchanges(
    exchange: str | None, expected: set[str]
) -> None:
    history = pd.DataFrame({"ts_code": ["600000.SH", "000001.SZ", "430047.BJ"]})
    result = mins._traded_symbols_from_provider(
        DailyUniverse(),
        trade_date="20241128",
        policy=TushareRequestPolicy(attempts=1),
        stock_history=history,
        exchange=exchange,
    )
    assert result == expected
