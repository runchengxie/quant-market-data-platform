from __future__ import annotations

import pytest

from market_data_platform.providers.tushare_a_share_ownership_features import (
    build_a_share_fund_portfolio_features,
    build_a_share_holder_structure_features,
    build_a_share_holdertrade_events,
    build_a_share_top_inst_events,
    validate_a_share_fund_portfolio_features,
    validate_a_share_holder_structure_features,
    validate_a_share_holdertrade_events,
    validate_a_share_top_inst_events,
)


def _write_period_part(root, period, frame):
    path = root / "data" / f"end_date={period}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def _write_daily_basic_part(root, trade_date, frame):
    path = root / "data" / f"trade_date={trade_date}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def _write_symbol_part(root, symbol, frame):
    path = root / "data" / f"symbol={symbol}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def _read_feature_asset(out_dir):
    pd = pytest.importorskip("pandas")
    data = pd.read_parquet(out_dir / "data")
    data["trade_date"] = data["trade_date"].astype(str)
    return data.sort_values(["trade_date", "symbol"])


FUND_PORTFOLIO_ROWS_BY_PERIOD = {
    "20260331": [
        {
            "ts_code": "000001.OF",
            "ann_date": "20260420",
            "end_date": "20260331",
            "symbol": "600519.SH",
            "mkv": 100_000_000.0,
            "amount": 10_000_000.0,
            "stk_mkv_ratio": 2.0,
            "stk_float_ratio": 0.5,
        },
        {
            "ts_code": "000002.OF",
            "ann_date": "20260420",
            "end_date": "20260331",
            "symbol": "600519.SH",
            "mkv": 50_000_000.0,
            "amount": 5_000_000.0,
            "stk_mkv_ratio": 1.0,
            "stk_float_ratio": 0.25,
        },
        {
            "ts_code": "000002.OF",
            "ann_date": "20260420",
            "end_date": "20260331",
            "symbol": "000001.SZ",
            "mkv": 20_000_000.0,
            "amount": 2_000_000.0,
            "stk_mkv_ratio": 0.5,
            "stk_float_ratio": 0.1,
        },
    ],
    "20260630": [
        {
            "ts_code": "000001.OF",
            "ann_date": "20260720",
            "end_date": "20260630",
            "symbol": "000001.SZ",
            "mkv": 30_000_000.0,
            "amount": 3_000_000.0,
            "stk_mkv_ratio": 0.8,
            "stk_float_ratio": 0.2,
        },
        {
            "ts_code": "000002.OF",
            "ann_date": "20260720",
            "end_date": "20260630",
            "symbol": "000001.SZ",
            "mkv": 40_000_000.0,
            "amount": 4_000_000.0,
            "stk_mkv_ratio": 1.0,
            "stk_float_ratio": 0.3,
        },
    ],
}

FUND_DAILY_BASIC_ROWS_BY_DATE = {
    trade_date: [
        {
            "ts_code": "600519.SH",
            "trade_date": trade_date,
            "total_mv": 30_000.0,
            "circ_mv": 20_000.0,
            "float_share": 2_000.0,
        },
        {
            "ts_code": "000001.SZ",
            "trade_date": trade_date,
            "total_mv": 10_000.0,
            "circ_mv": 8_000.0,
            "float_share": 1_000.0,
        },
    ]
    for trade_date in ("20260421", "20260721")
}

TOP10_HOLDER_ROWS = [
    {
        "ts_code": "600519.SH",
        "ann_date": "20260420",
        "end_date": "20251231",
        "holder_name": "李四",
        "hold_amount": 300.0,
        "hold_ratio": 2.0,
        "hold_float_ratio": 2.4,
        "hold_change": 30.0,
        "holder_type": "个人",
    },
    {
        "ts_code": "600519.SH",
        "ann_date": "20260420",
        "end_date": "20260331",
        "holder_name": "中国证券金融股份有限公司",
        "hold_amount": 1000.0,
        "hold_ratio": 5.0,
        "hold_float_ratio": 6.0,
        "hold_change": 100.0,
        "holder_type": "机构",
    },
    {
        "ts_code": "600519.SH",
        "ann_date": "20260420",
        "end_date": "20260331",
        "holder_name": "张三",
        "hold_amount": 200.0,
        "hold_ratio": 1.0,
        "hold_float_ratio": 1.2,
        "hold_change": -20.0,
        "holder_type": "个人",
    },
    {
        "ts_code": "600519.SH",
        "ann_date": "20260720",
        "end_date": "20260630",
        "holder_name": "中国证券金融股份有限公司",
        "hold_amount": 900.0,
        "hold_ratio": 4.5,
        "hold_float_ratio": 5.4,
        "hold_change": -100.0,
        "holder_type": "机构",
    },
]

TOP10_FLOAT_HOLDER_ROWS = [
    {
        "ts_code": "600519.SH",
        "ann_date": "20260420",
        "end_date": "20260331",
        "holder_name": "中央汇金资产管理有限责任公司",
        "hold_amount": 800.0,
        "hold_ratio": 4.0,
        "hold_float_ratio": 4.8,
        "hold_change": 0.0,
        "holder_type": "机构",
    }
]

TOP_INST_ROWS_BY_DATE = {
    "20260420": [
        {
            "trade_date": "20260420",
            "ts_code": "600519.SH",
            "exalter": "机构专用A",
            "buy": 100.0,
            "buy_rate": 10.0,
            "sell": 40.0,
            "sell_rate": 4.0,
            "net_buy": 60.0,
        },
        {
            "trade_date": "20260420",
            "ts_code": "600519.SH",
            "exalter": "机构专用B",
            "buy": 0.0,
            "buy_rate": 0.0,
            "sell": 30.0,
            "sell_rate": 3.0,
            "net_buy": -30.0,
        },
        {
            "trade_date": "20260420",
            "ts_code": "000001.SZ",
            "exalter": "机构专用",
            "buy": 10.0,
            "buy_rate": 1.0,
            "sell": 0.0,
            "sell_rate": 0.0,
            "net_buy": 10.0,
        },
    ],
    "20260422": [
        {
            "trade_date": "20260422",
            "ts_code": "600519.SH",
            "exalter": "机构专用A",
            "buy": 20.0,
            "buy_rate": 2.0,
            "sell": 0.0,
            "sell_rate": 0.0,
            "net_buy": 20.0,
        }
    ],
}


def test_build_and_validate_a_share_fund_portfolio_features_asset(tmp_path):
    pd = pytest.importorskip("pandas")
    fund_portfolio_dir = tmp_path / "fund_portfolio"
    daily_basic_dir = tmp_path / "daily_basic"

    for period, rows in FUND_PORTFOLIO_ROWS_BY_PERIOD.items():
        _write_period_part(fund_portfolio_dir, period, pd.DataFrame(rows))
    for trade_date, rows in FUND_DAILY_BASIC_ROWS_BY_DATE.items():
        _write_daily_basic_part(
            daily_basic_dir,
            trade_date,
            pd.DataFrame(rows),
        )

    out_dir = tmp_path / "fund_portfolio_features"
    manifest = build_a_share_fund_portfolio_features(
        fund_portfolio_dir=fund_portfolio_dir,
        daily_basic_dir=daily_basic_dir,
        out_dir=out_dir,
        min_rows=4,
        min_symbols=2,
    )

    assert manifest["schema_version"] == "tushare.a_share.fund_portfolio_features.v1"
    assert manifest["semantics"]["point_in_time"] is True
    assert manifest["provenance"]["revision_safe"] is False
    assert (
        "trade_date"
        not in pd.read_parquet(out_dir / "data" / "trade_date=20260421" / "part.parquet").columns
    )
    data = _read_feature_asset(out_dir)
    first = data[data["trade_date"] == "20260421"]
    latest = data[data["trade_date"] == "20260721"]

    maotai = first[first["symbol"] == "600519.SH"].iloc[0]
    assert maotai["available_date"] == "20260421"
    assert maotai["fund_count_holding_stock"] == 2.0
    assert maotai["fund_hold_mv_to_float_mv"] == pytest.approx(150_000_000 / 200_000_000)
    assert maotai["fund_hold_amount_to_float_share"] == pytest.approx(15_000_000 / 20_000_000)

    exited = latest[latest["symbol"] == "600519.SH"].iloc[0]
    assert exited["fund_count_holding_stock"] == 0.0
    assert exited["fund_hold_mv"] == 0.0
    assert exited["fund_hold_mv_to_float_mv_qoq_change"] == pytest.approx(-0.75)

    pingan = latest[latest["symbol"] == "000001.SZ"].iloc[0]
    assert pingan["fund_count_holding_stock"] == 2.0
    assert pingan["fund_hold_mv_to_total_mv"] == pytest.approx(70_000_000 / 100_000_000)

    result = validate_a_share_fund_portfolio_features(
        asset_dir=out_dir,
        min_rows=4,
        min_symbols=2,
    )
    assert result["status"] == "passed"


def test_build_fund_features_rejects_conflicting_duplicate_holdings(tmp_path):
    pd = pytest.importorskip("pandas")
    fund_portfolio_dir = tmp_path / "fund_portfolio"
    daily_basic_dir = tmp_path / "daily_basic"
    rows = [
        {
            "ts_code": "000001.OF",
            "ann_date": "20260420",
            "end_date": "20260331",
            "symbol": "600519.SH",
            "mkv": 100.0,
            "amount": 10.0,
            "stk_mkv_ratio": 2.0,
            "stk_float_ratio": 0.5,
        },
        {
            "ts_code": "000001.OF",
            "ann_date": "20260420",
            "end_date": "20260331",
            "symbol": "600519.SH",
            "mkv": 101.0,
            "amount": 10.0,
            "stk_mkv_ratio": 2.0,
            "stk_float_ratio": 0.5,
        },
    ]
    _write_period_part(fund_portfolio_dir, "20260331", pd.DataFrame(rows))
    _write_daily_basic_part(
        daily_basic_dir,
        "20260421",
        pd.DataFrame(
            [
                {
                    "ts_code": "600519.SH",
                    "trade_date": "20260421",
                    "total_mv": 1_000.0,
                    "circ_mv": 800.0,
                    "float_share": 100.0,
                }
            ]
        ),
    )

    with pytest.raises(ValueError, match="conflicting duplicate logical keys"):
        build_a_share_fund_portfolio_features(
            fund_portfolio_dir=fund_portfolio_dir,
            daily_basic_dir=daily_basic_dir,
            out_dir=tmp_path / "out",
        )


def test_build_and_validate_a_share_holder_structure_features_asset(tmp_path):
    pd = pytest.importorskip("pandas")
    holders_dir = tmp_path / "top10_holders"
    floatholders_dir = tmp_path / "top10_floatholders"
    daily_basic_dir = tmp_path / "daily_basic"

    _write_symbol_part(holders_dir, "600519.SH", pd.DataFrame(TOP10_HOLDER_ROWS))
    _write_symbol_part(floatholders_dir, "600519.SH", pd.DataFrame(TOP10_FLOAT_HOLDER_ROWS))
    for trade_date in ("20260421", "20260721"):
        _write_daily_basic_part(
            daily_basic_dir,
            trade_date,
            pd.DataFrame([{"ts_code": "600519.SH", "trade_date": trade_date}]),
        )

    out_dir = tmp_path / "holder_structure_features"
    manifest = build_a_share_holder_structure_features(
        top10_holders_dir=holders_dir,
        top10_floatholders_dir=floatholders_dir,
        daily_basic_dir=daily_basic_dir,
        out_dir=out_dir,
        min_rows=2,
        min_symbols=1,
    )
    assert manifest["schema_version"] == "tushare.a_share.holder_structure_features.v1"
    assert (
        "trade_date"
        not in pd.read_parquet(out_dir / "data" / "trade_date=20260421" / "part.parquet").columns
    )
    data = _read_feature_asset(out_dir)
    first_part = data[data["trade_date"] == "20260421"]
    assert not any(column.endswith(("_x", "_y")) for column in first_part.columns)
    assert first_part.duplicated(["trade_date", "symbol"]).sum() == 0
    first = first_part.iloc[0]
    assert first["top10_report_period"] == "20260331"
    assert first["top10_holder_count"] == 2.0
    assert first["top10_inst_holder_count"] == 1.0
    assert first["top10_concentration"] == pytest.approx(6.0)
    assert first["top10_inst_hold_ratio"] == pytest.approx(5.0)
    assert first["top10_float_holder_concentration"] == pytest.approx(4.0)
    assert first["top10_float_inst_hold_ratio"] == pytest.approx(4.0)
    assert first["top10_days_since_holder_report"] == pytest.approx(21.0)

    latest = data[data["trade_date"] == "20260721"].iloc[0]
    assert latest["top10_inst_hold_ratio_qoq_change"] == pytest.approx(-0.5)

    result = validate_a_share_holder_structure_features(
        asset_dir=out_dir,
        min_rows=2,
        min_symbols=1,
    )
    assert result["status"] == "passed"


def test_build_and_validate_a_share_top_inst_events_asset(tmp_path):
    pd = pytest.importorskip("pandas")
    top_inst_dir = tmp_path / "top_inst"
    daily_dir = tmp_path / "daily"

    for trade_date, rows in TOP_INST_ROWS_BY_DATE.items():
        _write_daily_basic_part(top_inst_dir, trade_date, pd.DataFrame(rows))
    for trade_date in ("20260420", "20260421", "20260422"):
        _write_daily_basic_part(
            daily_dir,
            trade_date,
            pd.DataFrame(
                [
                    {"ts_code": "600519.SH", "trade_date": trade_date, "amount": 10000.0},
                    {"ts_code": "000001.SZ", "trade_date": trade_date, "amount": 5000.0},
                ]
            ),
        )

    out_dir = tmp_path / "top_inst_events"
    manifest = build_a_share_top_inst_events(
        top_inst_dir=top_inst_dir,
        daily_dir=daily_dir,
        out_dir=out_dir,
        window=3,
        min_rows=6,
        min_symbols=2,
    )

    assert manifest["schema_version"] == "tushare.a_share.top_inst_events.v1"
    data = _read_feature_asset(out_dir)
    first_part = data[data["trade_date"] == "20260420"]
    first = first_part[first_part["symbol"] == "600519.SH"].iloc[0]
    assert first["top_inst_event_count"] == 2.0
    assert first["top_inst_buy"] == pytest.approx(100.0)
    assert first["top_inst_sell"] == pytest.approx(70.0)
    assert first["top_inst_net_buy"] == pytest.approx(30.0)
    assert first["top_inst_exalter_count"] == 2
    assert first["top_inst_net_buy_to_amount"] == pytest.approx(30.0 / 1000.0)

    middle = data[data["trade_date"] == "20260421"]
    middle_maotai = middle[middle["symbol"] == "600519.SH"].iloc[0]
    assert middle_maotai["top_inst_event_count"] == 0.0
    assert middle_maotai["top_inst_is_event_3d"] == 1.0
    assert middle_maotai["top_inst_days_since_event"] == 1.0

    latest = data[data["trade_date"] == "20260422"]
    latest_maotai = latest[latest["symbol"] == "600519.SH"].iloc[0]
    assert latest_maotai["top_inst_net_buy_3d"] == pytest.approx(50.0)
    assert latest_maotai["top_inst_buy_count_3d"] == pytest.approx(2.0)
    assert latest_maotai["top_inst_sell_count_3d"] == pytest.approx(2.0)
    assert latest_maotai["top_inst_days_since_event"] == 0.0

    result = validate_a_share_top_inst_events(
        asset_dir=out_dir,
        min_rows=6,
        min_symbols=2,
    )
    assert result["status"] == "passed"


def test_build_and_validate_a_share_holdertrade_events_asset(tmp_path):
    pd = pytest.importorskip("pandas")
    holdertrade_dir = tmp_path / "stk_holdertrade"
    daily_basic_dir = tmp_path / "daily_basic"

    _write_symbol_part(
        holdertrade_dir,
        "600519.SH",
        pd.DataFrame(
            [
                {
                    "ts_code": "600519.SH",
                    "ann_date": "20260420",
                    "holder_name": "控股股东",
                    "holder_type": "控股股东",
                    "in_de": "增持",
                    "change_vol": 10000.0,
                    "avg_price": 20.0,
                    "begin_date": "20260401",
                    "close_date": "20260420",
                },
                {
                    "ts_code": "600519.SH",
                    "ann_date": "20260422",
                    "holder_name": "控股股东",
                    "holder_type": "控股股东",
                    "in_de": "减持",
                    "change_vol": 5000.0,
                    "avg_price": 10.0,
                    "begin_date": "20260422",
                    "close_date": "20260422",
                },
            ]
        ),
    )
    for trade_date in ("20260421", "20260422", "20260423"):
        _write_daily_basic_part(
            daily_basic_dir,
            trade_date,
            pd.DataFrame(
                [
                    {
                        "ts_code": "600519.SH",
                        "trade_date": trade_date,
                        "circ_mv": 1000.0,
                    }
                ]
            ),
        )

    out_dir = tmp_path / "holdertrade_events"
    manifest = build_a_share_holdertrade_events(
        stk_holdertrade_dir=holdertrade_dir,
        daily_basic_dir=daily_basic_dir,
        out_dir=out_dir,
        amount_window=20,
        count_window=60,
        min_rows=3,
        min_symbols=1,
    )

    assert manifest["schema_version"] == "tushare.a_share.holdertrade_events.v1"
    data = _read_feature_asset(out_dir)
    first = data[data["trade_date"] == "20260421"].iloc[0]
    assert first["holdertrade_event_count"] == 1.0
    assert first["holdertrade_buy_count_60d"] == pytest.approx(1.0)
    assert first["holdertrade_sell_count_60d"] == pytest.approx(0.0)
    assert first["holdertrade_net_amount_20d_to_float_mv"] == pytest.approx(20.0 / 1000.0)
    assert first["days_since_holdertrade"] == 0.0

    middle = data[data["trade_date"] == "20260422"].iloc[0]
    assert middle["holdertrade_event_count"] == 0.0
    assert middle["holdertrade_buy_count_60d"] == pytest.approx(1.0)
    assert middle["days_since_holdertrade"] == 1.0

    latest = data[data["trade_date"] == "20260423"].iloc[0]
    assert latest["holdertrade_event_count"] == 1.0
    assert latest["holdertrade_buy_count_60d"] == pytest.approx(1.0)
    assert latest["holdertrade_sell_count_60d"] == pytest.approx(1.0)
    assert latest["holdertrade_net_amount_20d_to_float_mv"] == pytest.approx(15.0 / 1000.0)
    assert latest["days_since_holdertrade"] == 0.0

    result = validate_a_share_holdertrade_events(
        asset_dir=out_dir,
        min_rows=3,
        min_symbols=1,
    )
    assert result["status"] == "passed"
