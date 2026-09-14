from __future__ import annotations

import pandas as pd

from market_data_platform.audit_tushare_minute_availability import (
    probe_symbol_frame,
    summarize_probe,
)


def _frame(day: str, rows: int = 241) -> pd.DataFrame:
    times = pd.date_range(f"{day} 09:30:00", periods=rows, freq="min")
    return pd.DataFrame({"ts_code": ["000001.SZ"] * rows, "trade_time": times})


def test_probe_symbol_frame_marks_complete_241_grid() -> None:
    result = probe_symbol_frame(_frame("20160104"), symbol="000001.SZ", trade_date="20160104")
    assert result == {
        "symbol": "000001.SZ",
        "trade_date": "20160104",
        "rows": 241,
        "complete_241": True,
        "status": "complete",
    }


def test_probe_symbol_frame_marks_empty_response_as_inconclusive() -> None:
    result = probe_symbol_frame(pd.DataFrame(), symbol="000001.SZ", trade_date="20160103")
    assert result["rows"] == 0
    assert result["complete_241"] is False
    assert result["status"] == "empty"


def test_summary_only_calls_date_confirmed_when_all_symbols_complete() -> None:
    probes = [
        probe_symbol_frame(_frame("20160104"), symbol="000001.SZ", trade_date="20160104"),
        probe_symbol_frame(_frame("20160104"), symbol="600000.SH", trade_date="20160104"),
    ]
    summary = summarize_probe(probes)
    assert summary["earliest_confirmed_date"] == "20160104"
    assert summary["confirmed_dates"] == ["20160104"]
