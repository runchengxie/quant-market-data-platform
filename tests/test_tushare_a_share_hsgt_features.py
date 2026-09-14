from __future__ import annotations

import pytest

from market_data_platform.providers.tushare_a_share_hsgt_features import (
    build_a_share_hsgt_market_features,
    validate_a_share_hsgt_market_features,
)


def _write_trade_date_part(root, trade_date, frame):
    path = root / "data" / f"trade_date={trade_date}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def test_build_and_validate_a_share_hsgt_market_features_asset(tmp_path):
    pd = pytest.importorskip("pandas")
    hsgt_dir = tmp_path / "moneyflow_hsgt"
    rows_by_date = {
        "20260102": {"north_money": 10.0, "south_money": 3.0, "hgt": 6.0, "sgt": 4.0},
        "20260105": {"north_money": -2.0, "south_money": 1.0, "hgt": -1.0, "sgt": -1.0},
        "20260106": {"north_money": 8.0, "south_money": 2.0, "hgt": 5.0, "sgt": 3.0},
    }
    for trade_date, values in rows_by_date.items():
        _write_trade_date_part(
            hsgt_dir,
            trade_date,
            pd.DataFrame([{"trade_date": trade_date, **values}]),
        )

    out_dir = tmp_path / "hsgt_market_features"
    manifest = build_a_share_hsgt_market_features(
        moneyflow_hsgt_dir=hsgt_dir,
        out_dir=out_dir,
        windows=[2],
        min_rows=3,
    )

    assert manifest["schema_version"] == "tushare.a_share.hsgt_market_features.v1"
    latest = pd.read_parquet(out_dir / "data" / "trade_date=20260106" / "part.parquet").iloc[0]
    assert "symbol" not in latest.index
    assert latest["available_date"] == "20260106"
    assert latest["hsgt_north_money"] == pytest.approx(8.0)
    assert latest["hsgt_north_money_2d_sum"] == pytest.approx(6.0)
    assert latest["hsgt_south_money_2d_sum"] == pytest.approx(3.0)
    assert latest["hsgt_north_south_spread_2d_sum"] == pytest.approx(3.0)
    assert latest["hsgt_north_positive_ratio_2d"] == pytest.approx(0.5)

    result = validate_a_share_hsgt_market_features(asset_dir=out_dir, min_rows=3)
    assert result["status"] == "passed"
