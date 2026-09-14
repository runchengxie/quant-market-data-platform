from pathlib import Path

import pandas as pd

from market_data_platform.tushare_etf_history import (
    build_etf_daily_forward_adjusted,
    etf_join_code,
    validate_etf_daily_pair,
)


def _write(root: Path, name: str, frame: pd.DataFrame) -> None:
    path = root / "data" / f"trade_date={name}" / "part.parquet"
    path.parent.mkdir(parents=True)
    frame.to_parquet(path, index=False)


def test_bang_one_code_joins_and_builds_forward_adjustment(tmp_path: Path) -> None:
    daily, adj, out = tmp_path / "daily", tmp_path / "adj", tmp_path / "out"
    _write(
        daily,
        "20150105",
        pd.DataFrame(
            {
                "ts_code": ["1601231.SZ"],
                "trade_date": ["20150105"],
                "open": [10.0],
                "high": [10.0],
                "low": [10.0],
                "close": [10.0],
                "pre_close": [10.0],
            }
        ),
    )
    _write(
        adj,
        "20150105",
        pd.DataFrame({"ts_code": ["160123!1.SZ"], "trade_date": ["20150105"], "adj_factor": [2.0]}),
    )
    report = validate_etf_daily_pair(daily_dir=daily, adj_factor_dir=adj)
    assert report["status"] == "passed"
    assert report["daily_without_adj"] == 0
    manifest = build_etf_daily_forward_adjusted(daily_dir=daily, adj_factor_dir=adj, out_dir=out)
    frame = pd.read_parquet(out / "data/trade_date=20150105/part.parquet")
    assert manifest["totals"]["rows_missing_factor"] == 0
    assert frame.loc[0, "adj_close"] == 10.0


def test_etf_join_code_only_repairs_documented_pattern() -> None:
    assert etf_join_code("160123!1.SZ") == "1601231.SZ"
    assert etf_join_code("510300.SH") == "510300.SH"
