from __future__ import annotations

import json
from pathlib import Path

import pytest

from quant_market_data_platform.providers._minute_provider_exceptions import (
    ProviderNoDataExceptionError,
    load_provider_no_data_exclusions,
)


def _write_policy(
    path: Path, *, allow: bool = False, observed_dates: list[str] | None = None
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "market_data_platform.minute_provider_no_data_exceptions.v1",
                "provider": "tushare",
                "dataset": "stk_mins",
                "policy": {
                    "allow_acquisition_exclusion": allow,
                    "allow_exclusion_from_provider_verified_universe": False,
                    "allow_synthetic_bars": False,
                    "allow_complete_partition_promotion": False,
                    "requires_recheck_before_campaign_completion": True,
                },
                "exceptions": [
                    {
                        "ts_code": "001872.SZ",
                        "observed_dates": observed_dates or ["20160104"],
                        "http_status": 200,
                        "provider_code": 0,
                        "provider_items": 0,
                        "reason": "provider returned no minute rows",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_runtime_exclusions_are_date_scoped_and_hash_bound(tmp_path: Path) -> None:
    path = tmp_path / "exceptions.json"
    _write_policy(path, allow=True, observed_dates=["20160104", "20160106"])

    result = load_provider_no_data_exclusions(path, trade_date="20160104")

    assert result.codes == ("001872.SZ",)
    assert result.policy_sha256
    assert result.trade_date == "20160104"

    assert load_provider_no_data_exclusions(path, trade_date="20160105").codes == ()


def test_runtime_exclusions_remain_disabled_without_explicit_acquisition_flag(
    tmp_path: Path,
) -> None:
    path = tmp_path / "exceptions.json"
    _write_policy(path)

    with pytest.raises(ProviderNoDataExceptionError, match="acquisition exclusion"):
        load_provider_no_data_exclusions(path, trade_date="20160104")


def test_runtime_exclusions_reject_weakening_promotion_guards(tmp_path: Path) -> None:
    path = tmp_path / "exceptions.json"
    _write_policy(path, allow=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["policy"]["allow_complete_partition_promotion"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProviderNoDataExceptionError, match="fail-closed"):
        load_provider_no_data_exclusions(path, trade_date="20160104")


def _day_progress(tmp_path, monkeypatch, *, date, codes, policy_path):
    import pandas as pd

    from quant_market_data_platform.providers import tushare_a_share_mins as shell
    from quant_market_data_platform.providers._a_share_mins_constants import MinsMirrorOptions
    from quant_market_data_platform.providers._mins_fetch import _build_day_progress

    # The only external input is the provider's traded-symbol response.
    monkeypatch.setattr(shell, "_traded_symbols_from_provider", lambda *a, **k: set(codes))
    history = pd.DataFrame(
        {"ts_code": codes, "list_date": ["20100101"] * len(codes), "delist_date": [""] * len(codes)}
    )
    return _build_day_progress(
        None,
        MinsMirrorOptions(
            start_date=date, end_date=date, provider_no_data_exceptions_path=policy_path
        ),
        None,
        tmp_path,
        date,
        [],
        None,
        history,
    )


def test_inapplicable_policy_preserves_native_universe_identity(tmp_path, monkeypatch):
    path = tmp_path / "exceptions.json"
    _write_policy(path, allow=True)
    baseline = _day_progress(
        tmp_path, monkeypatch, date="20160105", codes=["001872.SZ", "600000.SH"], policy_path=None
    )
    actual = _day_progress(
        tmp_path, monkeypatch, date="20160105", codes=["001872.SZ", "600000.SH"], policy_path=path
    )
    assert actual[2:] == baseline[2:]
    assert actual[1].universe_hash == baseline[1].universe_hash


def test_applicable_policy_excludes_code_and_marks_scope(tmp_path, monkeypatch):
    path = tmp_path / "exceptions.json"
    _write_policy(path, allow=True)
    actual = _day_progress(
        tmp_path, monkeypatch, date="20160104", codes=["001872.SZ", "600000.SH"], policy_path=path
    )
    assert actual[2] == {"600000.SH"}
    assert ":provider_no_data_exceptions=" in actual[3]
    assert actual[4].endswith(":audited_provider_no_data_exclusions")


def test_applicable_policy_retains_scope_when_universe_intersection_empty(tmp_path, monkeypatch):
    path = tmp_path / "exceptions.json"
    _write_policy(path, allow=True)
    actual = _day_progress(
        tmp_path, monkeypatch, date="20160104", codes=["600000.SH"], policy_path=path
    )
    assert actual[2] == {"600000.SH"}
    assert ":provider_no_data_exceptions=" in actual[3]
    assert actual[4].endswith(":audited_provider_no_data_exclusions")


def test_invalid_policy_rejected_even_on_inapplicable_date(tmp_path, monkeypatch):
    path = tmp_path / "exceptions.json"
    _write_policy(path, allow=False)
    with pytest.raises(ProviderNoDataExceptionError, match="acquisition exclusion"):
        _day_progress(tmp_path, monkeypatch, date="20160105", codes=["600000.SH"], policy_path=path)
