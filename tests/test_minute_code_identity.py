from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from quant_market_data_platform.providers import _mins_fetch as fetch
from quant_market_data_platform.providers._minute_code_identity import (
    minute_request_symbol,
    validate_request_identities,
    validated_identity_lineage,
)
from quant_market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy


@pytest.mark.parametrize(
    ("date", "expected"),
    [
        ("19940323", "601607.SH"),
        ("19940324", "600849.SH"),
        ("20091231", "600849.SH"),
        ("20100204", "600849.SH"),
        ("20100304", "600849.SH"),
        ("20100305", "601607.SH"),
        ("20100308", "601607.SH"),
        ("20100309", "601607.SH"),
    ],
)
def test_verified_code_interval(date: str, expected: str) -> None:
    assert minute_request_symbol("601607.SH", date) == expected


def test_other_securities_and_bse_remain_separate() -> None:
    for symbol in ("600607.SH", "600842.SH", "600849.SH"):
        assert minute_request_symbol(symbol, "20091231") == symbol
    assert minute_request_symbol("920000.BJ", "20241128") == "832000.BJ"
    assert minute_request_symbol("920000.BJ", "20251009") == "920000.BJ"
    with pytest.raises(ValueError, match="collision"):
        validate_request_identities(["601607.SH", "600849.SH"], "20091231")


def _bars(symbol: str) -> pd.DataFrame:
    times = [
        *pd.date_range("2009-12-31 09:30", periods=121, freq="min"),
        *pd.date_range("2009-12-31 13:01", periods=120, freq="min"),
    ]
    return pd.DataFrame(
        {
            "ts_code": symbol,
            "trade_time": times,
            "open": 10.0,
            "close": 10.0,
            "high": 10.0,
            "low": 10.0,
            "vol": 100.0,
            "amount": 1000.0,
        }
    )


def test_request_translation_preserves_values_and_observed_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _bars("600849.SH")
    calls: list[list[str]] = []

    def fake(_pro: Any, symbols: list[str], *_args: Any) -> pd.DataFrame:
        calls.append(symbols)
        return source

    monkeypatch.setattr(fetch, "_fetch_minute_batch", fake)
    lineage: dict[str, Any] = {}
    frames, valid, issues, calls_count = fetch._fetch_validated_minute_batch(
        None,
        ["601607.SH"],
        "20091231",
        "1min",
        TushareRequestPolicy(),
        None,
        cooldown_seconds=0,
        code_identity=lineage,
    )
    assert calls == [["600849.SH"]]
    assert valid == {"601607.SH"} and not issues and calls_count == 1
    expected = source.assign(ts_code="601607.SH")
    pd.testing.assert_frame_equal(frames[0].reset_index(drop=True), expected)
    assert set(source["ts_code"]) == {"600849.SH"}
    assert lineage["601607.SH"]["response_symbols"] == ["600849.SH"]
    assert validated_identity_lineage({"code_identity": lineage}, valid, "20091231") == lineage
    with pytest.raises(ValueError, match="lineage"):
        validated_identity_lineage({}, valid, "20091231")


@pytest.mark.parametrize("split", [False, True])
def test_dual_response_codes_fail_closed(monkeypatch: pytest.MonkeyPatch, split: bool) -> None:
    old, canonical = _bars("600849.SH"), _bars("601607.SH")
    if split:
        old, canonical = old.iloc[:121], canonical.iloc[121:]
    monkeypatch.setattr(
        fetch,
        "_fetch_minute_batch",
        lambda *_args: pd.concat([old, canonical], ignore_index=True),
    )
    with pytest.raises(ValueError, match="Conflicting minute response identities"):
        fetch._fetch_validated_minute_batch(
            None,
            ["601607.SH"],
            "20091231",
            "1min",
            TushareRequestPolicy(),
            None,
            cooldown_seconds=0,
        )


def test_canonical_daily_universe_and_suspension_gate() -> None:
    from quant_market_data_platform.providers._a_share_mins_constants import _UniverseInputs
    from quant_market_data_platform.providers._a_share_mins_universe import _resolve_minute_universe

    history = pd.DataFrame(
        {"ts_code": ["601607.SH"], "list_date": ["19940324"], "delist_date": [""]}
    )
    inputs = _UniverseInputs(history, "20091231", {"601607.SH"}, {"601607.SH"}, [], None)
    assert _resolve_minute_universe(inputs).symbols == {"601607.SH"}
    suspended = _UniverseInputs(history, "20100204", {"601607.SH"}, set(), ["601607.SH"], None)
    with pytest.raises(ValueError, match="did not trade"):
        _resolve_minute_universe(suspended)


def test_request_collision_stops_before_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*_args: Any) -> pd.DataFrame:
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(fetch, "_fetch_minute_batch", unexpected)
    with pytest.raises(ValueError, match="collision"):
        fetch._fetch_validated_minute_batch(
            None,
            ["601607.SH", "600849.SH"],
            "20091231",
            "1min",
            TushareRequestPolicy(),
            None,
            cooldown_seconds=0,
        )
