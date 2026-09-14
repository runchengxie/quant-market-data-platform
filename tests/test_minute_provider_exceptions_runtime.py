from __future__ import annotations

import json
from pathlib import Path

import pytest

from market_data_platform.providers._minute_provider_exceptions import (
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
