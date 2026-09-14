from __future__ import annotations

import pandas as pd
import pytest

import market_data_platform.research_data_interface as module
from market_data_platform.research_data_interface import ResearchDataInterface


@pytest.mark.parametrize(
    "source_mode",
    ["provider_online_legacy", "provider_online", "online_provider", "provider"],
)
def test_archived_online_modes_are_rejected(tmp_path, source_mode):
    with pytest.raises(SystemExit, match="online provider reads are archived"):
        ResearchDataInterface(
            "a_share",
            {"provider": "tushare", "source_mode": source_mode},
            tmp_path / "cache",
        )


def test_fixed_artifact_requires_local_provider(tmp_path):
    with pytest.raises(SystemExit, match="requires data.provider=local_artifact"):
        ResearchDataInterface(
            "a_share",
            {"provider": "tushare", "source_mode": "fixed_scored_artifact"},
            tmp_path / "cache",
        )


def test_provider_reads_delegate_without_provider_client(tmp_path, monkeypatch):
    calls: list[tuple] = []

    def fake_fetch_daily(*args):
        calls.append(args)
        return pd.DataFrame({"symbol": [args[1]], "trade_date": ["20260529"]})

    monkeypatch.setattr(module, "fetch_daily", fake_fetch_daily)
    interface = ResearchDataInterface(
        "a_share",
        {"provider": "tushare", "source_mode": "platform_assets"},
        tmp_path / "cache",
    )

    result = interface.fetch_daily("600519.SH", "20260501", "20260529")

    assert result["symbol"].tolist() == ["600519.SH"]
    assert len(calls) == 1
    assert calls[0][0:4] == ("a_share", "600519.SH", "20260501", "20260529")
    assert calls[0][5] is None


def test_local_artifact_reads_and_normalizes_dates(tmp_path):
    path = tmp_path / "scored.csv"
    pd.DataFrame(
        {
            "date": ["20260528", "20260529"],
            "ts_code": ["600519.SH", "600519.SH"],
            "close": [100.0, 101.0],
        }
    ).to_csv(path, index=False)

    interface = ResearchDataInterface(
        "a_share",
        {
            "provider": "local_artifact",
            "source_mode": "fixed_scored_artifact",
            "scored_file": str(path),
        },
        tmp_path / "cache",
    )

    result = interface.fetch_daily("600519.SH", "20260529", "20260529")

    assert len(result) == 1
    assert result.iloc[0]["close"] == 101.0
    assert result.attrs["tr_close_meta"]["source"] == "local_artifact"


def test_unsupported_market_is_rejected_at_read_time(tmp_path):
    interface = ResearchDataInterface(
        "hk",
        {"provider": "tushare", "source_mode": "platform_assets"},
        tmp_path / "cache",
    )

    with pytest.raises(SystemExit, match="Supported markets: a_share"):
        interface.fetch_daily("00005.HK", "20260101", "20260131")
