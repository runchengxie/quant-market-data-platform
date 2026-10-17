"""Only dated nontrading evidence may explain daily/basic mismatches."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from quant_market_data_platform.providers import tushare_a_share_mins as mins
from quant_market_data_platform.providers._minute_nontrading import verified_nontrading_symbols
from quant_market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy

POLICY = TushareRequestPolicy(attempts=1)


class EvidencePro:
    def __init__(self, *, timing: Any = None, event: str = "S", date: str = "20200918"):
        self.timing, self.event, self.date = timing, event, date

    def daily(self, **kwargs: Any) -> pd.DataFrame:
        return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [kwargs["trade_date"]]})

    def daily_basic(self, **kwargs: Any) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000029.SZ", "000670.SZ"],
                "trade_date": [kwargs["trade_date"]] * 3,
            }
        )

    def suspend_d(self, **kwargs: Any) -> pd.DataFrame:
        assert kwargs["trade_date"] == "20200918"
        return pd.DataFrame(
            {
                "ts_code": ["000029.SZ"],
                "trade_date": [self.date],
                "suspend_type": [self.event],
                "suspend_timing": [self.timing],
            }
        )


def test_daily_basic_nontrading_rows_are_explained_without_synthetic_bars() -> None:
    history = pd.DataFrame({"ts_code": ["000001.SZ", "000029.SZ", "000670.SZ"]})
    symbols = mins._traded_symbols_from_provider(
        EvidencePro(),
        trade_date="20200918",
        policy=POLICY,
        stock_history=history,
    )
    assert symbols == {"000001.SZ"}
    evidence = history.attrs["verified_nontrading"]
    assert evidence["symbols"] == {
        "000029.SZ": "tushare_suspend_d_full_day",
        "000670.SZ": "issuer_listing_pause",
    }


@pytest.mark.parametrize("timing,event", [("09:30-10:00", "S"), (None, "R")])
def test_intraday_or_resumption_does_not_explain_a_missing_stock(timing: Any, event: str) -> None:
    evidence = verified_nontrading_symbols(
        EvidencePro(timing=timing, event=event),
        missing={"000029.SZ"},
        trade_date="20200918",
        policy=POLICY,
    )
    assert evidence == {}


def test_wrong_date_evidence_is_rejected() -> None:
    with pytest.raises(ValueError, match="different date"):
        verified_nontrading_symbols(
            EvidencePro(date="20200917"),
            missing={"000029.SZ"},
            trade_date="20200918",
            policy=POLICY,
        )


@pytest.mark.parametrize(
    "code,start,end",
    [
        ("000670.SZ", "20200407", "20220822"),
        ("000792.SZ", "20200522", "20210810"),
        ("000995.SZ", "20190513", "20201216"),
    ],
)
def test_listing_pause_interval_never_excludes_resumption_day(
    code: str, start: str, end: str
) -> None:
    # No suspend_d support means only documented listing intervals can explain it.
    assert verified_nontrading_symbols(object(), missing={code}, trade_date=start, policy=POLICY)
    assert not verified_nontrading_symbols(object(), missing={code}, trade_date=end, policy=POLICY)


def test_unknown_missing_symbol_still_blocks_daily_crosscheck() -> None:
    class UnexplainedPro(EvidencePro):
        def daily_basic(self, **kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                {"ts_code": ["000001.SZ", "000002.SZ"], "trade_date": [kwargs["trade_date"]] * 2}
            )

    with pytest.raises(RuntimeError, match="missing_from_daily=.*000002.SZ"):
        mins._traded_symbols_from_provider(
            UnexplainedPro(),
            trade_date="20200918",
            policy=POLICY,
            stock_history=pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]}),
        )
