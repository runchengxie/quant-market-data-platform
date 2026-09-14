from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from market_data_platform.providers.tushare_context import (
    TUSHARE_CONTEXT_ENDPOINTS,
    fetch_tushare_context_endpoint,
    normalize_tushare_context,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def query(self, api_name: str, **kwargs):
        self.calls.append((api_name, kwargs))
        if api_name == "shibor":
            return pd.DataFrame([{"date": "20260828", "on": 1.2, "3m": 1.4}])
        if api_name == "cn_schedule":
            return pd.DataFrame(
                [
                    {
                        "month": "202608",
                        "publish_date": "20260831",
                        "title": "采购经理人指数月度报告",
                        "issuing_org": "国家统计局",
                        "data_api": "cn_pmi",
                    }
                ]
            )
        raise AssertionError(api_name)


def test_tushare_context_endpoint_set_is_frozen():
    assert TUSHARE_CONTEXT_ENDPOINTS == (
        "shibor",
        "shibor_lpr",
        "cn_m",
        "sf_month",
        "cn_pmi",
        "cn_cpi",
        "cn_ppi",
        "cn_gdp",
        "cn_schedule",
    )


def test_fetch_tushare_context_uses_endpoint_specific_date_parameters():
    client = FakeClient()
    frame = fetch_tushare_context_endpoint(
        client,
        "shibor",
        start_date="20260801",
        end_date="20260828",
    )
    assert len(frame) == 1
    assert client.calls == [("shibor", {"start_date": "20260801", "end_date": "20260828"})]


def test_normalize_shibor_emits_stable_series_rows():
    raw = pd.DataFrame([{"date": "20260828", "on": 1.2, "3m": 1.4}])
    observations, releases = normalize_tushare_context(
        "shibor",
        raw,
        retrieved_at=datetime(2026, 8, 28, 4, tzinfo=UTC),
        source_hash="a" * 64,
    )
    assert set(observations["series_id"]) == {"rates.shibor_on", "rates.shibor_3m"}
    assert observations["reconstructed"].eq(False).all()
    assert observations["revision_covered"].eq(True).all()
    assert releases.empty


def test_normalize_tushare_context_accepts_uppercase_month_from_live_cn_pmi():
    raw = pd.DataFrame([{"MONTH": "202607", "PMI010000": 49.2}])
    observations, releases = normalize_tushare_context(
        "cn_pmi",
        raw,
        retrieved_at=datetime(2026, 8, 28, 4, tzinfo=UTC),
        source_hash="c" * 64,
    )
    assert observations.iloc[0]["series_id"] == "activity.pmi_manufacturing"
    assert observations.iloc[0]["value"] == 49.2
    assert releases.empty


def test_schedule_supplies_release_calendar_rows():
    raw = pd.DataFrame(
        [
            {
                "month": "202608",
                "publish_date": "20260831",
                "title": "采购经理人指数月度报告",
                "issuing_org": "国家统计局",
                "data_api": "cn_pmi",
            }
        ]
    )
    observations, releases = normalize_tushare_context(
        "cn_schedule",
        raw,
        retrieved_at=datetime(2026, 8, 28, 4, tzinfo=UTC),
        source_hash="b" * 64,
    )
    assert observations.empty
    assert releases.iloc[0]["data_api"] == "cn_pmi"
    assert releases.iloc[0]["publish_date"] == "20260831"
