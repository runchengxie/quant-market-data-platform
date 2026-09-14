from __future__ import annotations

from typing import Any, cast

import pytest

from market_data_platform.providers.tushare_a_share_flow_features import (
    build_a_share_flow_ownership_features,
)
from market_data_platform.providers.tushare_a_share_flow_validation import (
    validate_a_share_flow_ownership_features,
)


def _write_trade_date_part(root, trade_date, frame):
    path = root / "data" / f"trade_date={trade_date}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


MONEYFLOW_ROWS_BY_DATE = {
    "20260102": [
        {
            "ts_code": "600519.SH",
            "trade_date": "20260102",
            "buy_elg_amount": 30.0,
            "sell_elg_amount": 10.0,
            "buy_lg_amount": 20.0,
            "sell_lg_amount": 5.0,
            "net_mf_amount": 25.0,
        },
        {
            "ts_code": "000001.SZ",
            "trade_date": "20260102",
            "buy_elg_amount": 10.0,
            "sell_elg_amount": 12.0,
            "buy_lg_amount": 8.0,
            "sell_lg_amount": 9.0,
            "net_mf_amount": -3.0,
        },
    ],
    "20260105": [
        {
            "ts_code": "600519.SH",
            "trade_date": "20260105",
            "buy_elg_amount": 50.0,
            "sell_elg_amount": 20.0,
            "buy_lg_amount": 30.0,
            "sell_lg_amount": 10.0,
            "net_mf_amount": 40.0,
        },
        {
            "ts_code": "000001.SZ",
            "trade_date": "20260105",
            "buy_elg_amount": 12.0,
            "sell_elg_amount": 7.0,
            "buy_lg_amount": 10.0,
            "sell_lg_amount": 8.0,
            "net_mf_amount": 9.0,
        },
    ],
}

INDUSTRY_ROWS = [
    {
        "symbol": "600519.SH",
        "effective_date": "20200101",
        "end_date": "",
        "industry_system": "sw2021",
        "industry_code": "801120",
        "industry_name": "食品饮料",
    },
    {
        "symbol": "000001.SZ",
        "effective_date": "20200101",
        "end_date": "",
        "industry_system": "sw2021",
        "industry_code": "801120",
        "industry_name": "食品饮料",
    },
]


def test_build_and_validate_a_share_flow_ownership_features_asset(tmp_path):
    pd = pytest.importorskip("pandas")
    moneyflow_dir = tmp_path / "moneyflow"
    daily_dir = tmp_path / "daily"
    daily_basic_dir = tmp_path / "daily_basic"
    industry_dir = tmp_path / "industry_changes"
    for trade_date, rows in MONEYFLOW_ROWS_BY_DATE.items():
        _write_trade_date_part(moneyflow_dir, trade_date, pd.DataFrame(rows))
        _write_trade_date_part(
            daily_dir,
            trade_date,
            pd.DataFrame(
                [
                    {"ts_code": "600519.SH", "trade_date": trade_date, "amount": 1000.0},
                    {"ts_code": "000001.SZ", "trade_date": trade_date, "amount": 500.0},
                ]
            ),
        )
        _write_trade_date_part(
            daily_basic_dir,
            trade_date,
            pd.DataFrame(
                [
                    {"ts_code": "600519.SH", "trade_date": trade_date, "circ_mv": 2000.0},
                    {"ts_code": "000001.SZ", "trade_date": trade_date, "circ_mv": 1000.0},
                ]
            ),
        )

    industry_path = industry_dir / "data" / "part.parquet"
    industry_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(INDUSTRY_ROWS).to_parquet(industry_path, index=False)

    out_dir = tmp_path / "flow_features"
    manifest = build_a_share_flow_ownership_features(
        moneyflow_dir=moneyflow_dir,
        daily_dir=daily_dir,
        daily_basic_dir=daily_basic_dir,
        industry_dir=industry_dir,
        out_dir=out_dir,
        windows=[2, 20],
        min_rows=4,
        min_symbols=2,
    )

    assert manifest["schema_version"] == "tushare.a_share.flow_ownership_features.v1"
    assert manifest["semantics"]["daily_amount_conversion"] == (
        "daily.amount divided by 10 when daily_dir supplies amount"
    )
    part_columns = pd.read_parquet(
        out_dir / "data" / "trade_date=20260102" / "part.parquet"
    ).columns
    assert "trade_date" not in part_columns
    data = pd.read_parquet(out_dir / "data")
    data["trade_date"] = data["trade_date"].astype(str)
    data = data.sort_values(["trade_date", "symbol"])
    latest = data[(data["trade_date"] == "20260105") & (data["symbol"] == "600519.SH")].iloc[0]
    latest_peer = data[(data["trade_date"] == "20260105") & (data["symbol"] == "000001.SZ")].iloc[0]
    assert latest["available_date"] == "20260105"
    assert latest["mf_net_amount_2d_to_amount"] == pytest.approx((25.0 + 40.0) / 200.0)
    assert latest["mf_elg_net_amount_2d_to_amount"] == pytest.approx(
        ((30.0 - 10.0) + (50.0 - 20.0)) / 200.0
    )
    assert latest["mf_net_amount_20d_to_float_mv"] == pytest.approx(65.0 / 2000.0)
    assert latest["mf_net_amount_20d_cs_rank"] == pytest.approx(1.0)
    assert latest_peer["mf_net_amount_20d_cs_rank"] == pytest.approx(0.5)
    assert latest["mf_net_amount_20d_cs_zscore"] == pytest.approx(1.0)
    assert latest_peer["mf_net_amount_20d_cs_zscore"] == pytest.approx(-1.0)
    assert latest["mf_net_amount_20d_industry_zscore"] == pytest.approx(1.0)
    assert latest_peer["mf_net_amount_20d_industry_zscore"] == pytest.approx(-1.0)

    result: dict[str, Any] = cast(
        dict[str, Any],
        validate_a_share_flow_ownership_features(
            asset_dir=out_dir,
            min_rows=4,
            min_symbols=2,
        ),
    )
    assert result["status"] == "passed"
    feature_check = next(
        check for check in result["checks"] if check["id"] == "feature_columns_present"
    )
    assert "mf_net_amount_20d_cs_rank" in feature_check["features"]
    assert "mf_net_amount_20d_cs_zscore" in feature_check["features"]
    assert "mf_net_amount_20d_industry_zscore" in feature_check["features"]


def test_flow_builder_accepts_moneyflow_dc_net_amount_alias(tmp_path):
    pd = pytest.importorskip("pandas")
    moneyflow_dir = tmp_path / "moneyflow_dc"
    daily_dir = tmp_path / "daily"

    _write_trade_date_part(
        moneyflow_dir,
        "20260102",
        pd.DataFrame(
            [
                {
                    "ts_code": "600519.SH",
                    "trade_date": "20260102",
                    "net_amount": 15.0,
                    "buy_elg_amount": 6.0,
                    "buy_lg_amount": 4.0,
                }
            ]
        ),
    )
    _write_trade_date_part(
        daily_dir,
        "20260102",
        pd.DataFrame([{"ts_code": "600519.SH", "trade_date": "20260102", "amount": 1000.0}]),
    )

    out_dir = tmp_path / "flow_features"
    build_a_share_flow_ownership_features(
        moneyflow_dir=moneyflow_dir,
        daily_dir=daily_dir,
        out_dir=out_dir,
        windows=[1],
        min_rows=1,
        min_symbols=1,
    )
    part = pd.read_parquet(out_dir / "data").iloc[0]
    assert str(part["trade_date"]) == "20260102"
    assert part["mf_net_amount_1d_to_amount"] == pytest.approx(15.0 / 100.0)
    assert part["mf_elg_net_amount_1d_to_amount"] == pytest.approx(6.0 / 100.0)
    assert part["mf_lg_net_amount_1d_to_amount"] == pytest.approx(4.0 / 100.0)
