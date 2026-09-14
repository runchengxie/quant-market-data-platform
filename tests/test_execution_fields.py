from __future__ import annotations

from market_data_platform.execution_fields import ensure_execution_daily_fields


def test_ensure_execution_daily_fields_adds_rqdata_fields() -> None:
    data_cfg: dict[str, object] = {"rqdata": {"fields": ["close"]}}

    ensure_execution_daily_fields(
        data_cfg=data_cfg,
        provider="rqdata",
        required_columns={"open", "medadv20_amount", "close"},
    )

    assert data_cfg["rqdata"] == {
        "fields": ["close", "total_turnover", "open"],
    }


def test_ensure_execution_daily_fields_leaves_non_rqdata_untouched() -> None:
    data_cfg: dict[str, object] = {}
    ensure_execution_daily_fields(
        data_cfg=data_cfg,
        provider="tushare",
        required_columns={"open", "amount"},
    )
    assert data_cfg == {}
