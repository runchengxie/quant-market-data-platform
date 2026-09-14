from __future__ import annotations

from typing import Any, cast

import pytest

from market_data_platform.providers.tushare_a_share_hotspot_features import (
    build_a_share_hotspot_features,
    validate_a_share_hotspot_features,
)


def _write_trade_date_part(root, trade_date, frame):
    path = root / "data" / f"trade_date={trade_date}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def _write_month_part(root, month, frame):
    path = root / "data" / f"month={month}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def _asset_dirs(tmp_path) -> dict[str, object]:
    return {
        name: tmp_path / name
        for name in (
            "daily_basic",
            "ths_hot",
            "dc_concept",
            "dc_concept_cons",
            "kpl_list",
            "kpl_concept_cons",
            "limit_step",
            "report_rc",
            "stk_surv",
            "broker_recommend",
        )
    }


def _write_daily_grid(pd, dirs: dict[str, object]) -> None:
    for trade_date in ("20260102", "20260105"):
        _write_trade_date_part(
            dirs["daily_basic"],
            trade_date,
            pd.DataFrame(
                [
                    {"ts_code": "600519.SH", "trade_date": trade_date},
                    {"ts_code": "000001.SZ", "trade_date": trade_date},
                ]
            ),
        )


def _write_hot_list(pd, dirs: dict[str, object]) -> None:
    _write_trade_date_part(
        dirs["ths_hot"],
        "20260102",
        pd.DataFrame(
            [
                {"ts_code": "600519.SH", "trade_date": "20260102", "rank": 1, "hot": 100.0},
                {"ts_code": "000001.SZ", "trade_date": "20260102", "rank": 2, "hot": 50.0},
            ]
        ),
    )
    _write_trade_date_part(
        dirs["ths_hot"],
        "20260105",
        pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260105", "rank": 1, "hot": 80.0}]),
    )


def _write_theme_assets(pd, dirs: dict[str, object]) -> None:
    _write_trade_date_part(
        dirs["dc_concept"],
        "20260102",
        pd.DataFrame(
            [
                {
                    "trade_date": "20260102",
                    "theme_code": "T1",
                    "strength": 90.0,
                    "hot": 70.0,
                    "z_t_num": 3,
                    "lead_stock_code": "600519.SH",
                },
                {
                    "trade_date": "20260102",
                    "theme_code": "T2",
                    "strength": 10.0,
                    "hot": 30.0,
                    "z_t_num": 0,
                    "lead_stock_code": "000001.SZ",
                },
            ]
        ),
    )
    _write_trade_date_part(
        dirs["dc_concept_cons"],
        "20260102",
        pd.DataFrame(
            [
                {"ts_code": "600519.SH", "trade_date": "20260102", "theme_code": "T1"},
                {"ts_code": "000001.SZ", "trade_date": "20260102", "theme_code": "T2"},
            ]
        ),
    )
    _write_trade_date_part(
        dirs["kpl_concept_cons"],
        "20260102",
        pd.DataFrame(
            [
                {
                    "ts_code": "000111.KP",
                    "trade_date": "20260102",
                    "con_code": "600519.SH",
                    "hot_num": 7,
                }
            ]
        ),
    )


def _write_event_assets(pd, dirs: dict[str, object]) -> None:
    _write_trade_date_part(
        dirs["kpl_list"],
        "20260102",
        pd.DataFrame(
            [
                {"ts_code": "600519.SH", "trade_date": "20260102", "tag": "涨停"},
                {"ts_code": "000001.SZ", "trade_date": "20260102", "tag": "炸板"},
            ]
        ),
    )
    _write_trade_date_part(
        dirs["kpl_list"],
        "20260105",
        pd.DataFrame([{"ts_code": "600519.SH", "trade_date": "20260105", "tag": "涨停"}]),
    )
    _write_trade_date_part(
        dirs["limit_step"],
        "20260105",
        pd.DataFrame([{"ts_code": "600519.SH", "trade_date": "20260105", "nums": 2}]),
    )
    _write_trade_date_part(
        dirs["report_rc"],
        "20260102",
        pd.DataFrame(
            [
                {
                    "ts_code": "600519.SH",
                    "trade_date": "20260102",
                    "report_date": "20260102",
                    "rating": "买入",
                }
            ]
        ),
    )
    _write_trade_date_part(
        dirs["stk_surv"],
        "20260102",
        pd.DataFrame([{"ts_code": "600519.SH", "trade_date": "20260102", "surv_date": "20260102"}]),
    )
    _write_month_part(
        dirs["broker_recommend"],
        "202601",
        pd.DataFrame(
            [
                {"ts_code": "600519.SH", "month": "202601", "broker": "券商A"},
                {"ts_code": "600519.SH", "month": "202601", "broker": "券商B"},
            ]
        ),
    )


def _write_hotspot_feature_fixture(pd, tmp_path) -> dict[str, object]:
    dirs = _asset_dirs(tmp_path)
    _write_daily_grid(pd, dirs)
    _write_hot_list(pd, dirs)
    _write_theme_assets(pd, dirs)
    _write_event_assets(pd, dirs)
    return dirs


def test_build_and_validate_a_share_hotspot_features_asset(tmp_path):
    pd = pytest.importorskip("pandas")
    dirs = _write_hotspot_feature_fixture(pd, tmp_path)
    out_dir = tmp_path / "hotspot_features"

    manifest = build_a_share_hotspot_features(
        daily_basic_dir=dirs["daily_basic"],
        ths_hot_dir=dirs["ths_hot"],
        dc_concept_dir=dirs["dc_concept"],
        dc_concept_cons_dir=dirs["dc_concept_cons"],
        kpl_list_dir=dirs["kpl_list"],
        kpl_concept_cons_dir=dirs["kpl_concept_cons"],
        limit_step_dir=dirs["limit_step"],
        report_rc_dir=dirs["report_rc"],
        stk_surv_dir=dirs["stk_surv"],
        broker_recommend_dir=dirs["broker_recommend"],
        out_dir=out_dir,
        start_date="20260102",
        end_date="20260105",
        min_rows=4,
        min_symbols=2,
    )

    assert manifest["schema_version"] == "tushare.a_share.hotspot_features.v1"
    data = pd.read_parquet(out_dir / "data")
    data["trade_date"] = data["trade_date"].astype(str)
    first = data[(data["trade_date"] == "20260102") & (data["symbol"] == "600519.SH")].iloc[0]
    second = data[(data["trade_date"] == "20260105") & (data["symbol"] == "600519.SH")].iloc[0]
    peer = data[(data["trade_date"] == "20260102") & (data["symbol"] == "000001.SZ")].iloc[0]

    assert first["hot_rank_pct"] == pytest.approx(1.0)
    assert peer["hot_rank_pct"] == pytest.approx(0.0)
    assert first["hot_zscore"] == pytest.approx(1.0)
    assert second["days_since_hot"] == pytest.approx(1.0)
    assert first["theme_limit_up_count"] == pytest.approx(3.0)
    assert first["strong_theme_count"] == pytest.approx(2.0)
    assert first["is_theme_leader"] == pytest.approx(1.0)
    assert second["kpl_limit_up_count_5d"] == pytest.approx(2.0)
    assert peer["failed_board_count_5d"] == pytest.approx(1.0)
    assert second["report_rc_count_20d"] == pytest.approx(1.0)
    assert second["rating_buy_count_20d"] == pytest.approx(1.0)
    assert second["survey_count_20d"] == pytest.approx(1.0)
    assert second["broker_recommend_count"] == pytest.approx(2.0)

    result: dict[str, Any] = cast(
        dict[str, Any],
        validate_a_share_hotspot_features(asset_dir=out_dir, min_rows=4, min_symbols=2),
    )
    assert result["status"] == "passed"
    assert result["totals"]["feature_columns"] == 19
