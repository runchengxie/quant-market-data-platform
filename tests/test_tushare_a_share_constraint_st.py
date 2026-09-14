from __future__ import annotations

from pathlib import Path

import pandas as pd

from market_data_platform.providers import tushare_a_share_constraints as constraints


def _write_st_namechange_fixture(tmp_path: Path) -> Path:
    namechange = tmp_path / "namechange.parquet"
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "name": "ST甲",
                "start_date": "20240102",
                "end_date": "20240103",
                "ann_date": "20240102",
            },
            {
                "ts_code": "000002.SZ",
                "name": "*ST乙",
                "start_date": "20240103",
                "end_date": None,
                "ann_date": "20240103",
            },
            {
                "ts_code": "000003.SZ",
                "name": "BEST科技",
                "start_date": "20240102",
                "end_date": None,
                "ann_date": "20240102",
            },
            {
                "ts_code": "000002.SZ",
                "name": "乙股份",
                "start_date": "20240105",
                "end_date": None,
                "ann_date": "20240104",
            },
            {
                "ts_code": "000004.SZ",
                "name": "*ST退",
                "start_date": "20240102",
                "end_date": None,
                "ann_date": "20240102",
            },
        ]
    ).to_parquet(namechange, index=False)
    return namechange


def _write_st_market_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    trade_cal = tmp_path / "trade_cal.parquet"
    pd.DataFrame(
        {
            "cal_date": ["20240102", "20240103", "20240104", "20240105"],
            "is_open": [1, 1, 1, 1],
        }
    ).to_parquet(trade_cal, index=False)
    instruments = tmp_path / "instruments.parquet"
    pd.DataFrame(
        [
            {
                "ts_code": code,
                "list_date": "20200101",
                "delist_date": "20240104" if code == "000004.SZ" else None,
            }
            for code in ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]
        ]
    ).to_parquet(instruments, index=False)
    stock_st = tmp_path / "stock_st.parquet"
    pd.DataFrame(
        [
            {"trade_date": date, "ts_code": symbol}
            for date, symbol in [
                ("20240102", "000001.SZ"),
                ("20240103", "000001.SZ"),
                ("20240103", "000002.SZ"),
                ("20240104", "000002.SZ"),
                ("20240102", "000004.SZ"),
                ("20240103", "000004.SZ"),
            ]
        ]
    ).to_parquet(stock_st, index=False)
    return trade_cal, instruments, stock_st


def test_build_reconstructed_st_history_matches_dated_stock_st(tmp_path: Path) -> None:
    namechange = _write_st_namechange_fixture(tmp_path)
    trade_cal, instruments, stock_st = _write_st_market_fixture(tmp_path)

    summary = constraints.build_reconstructed_st_history(
        constraints.ReconstructedSTOptions(
            namechange_path=namechange,
            trade_cal_path=trade_cal,
            instruments_path=instruments,
            stock_st_path=stock_st,
            out_dir=tmp_path / "built",
            start_date="20240101",
            end_date="20240105",
            min_precision=1.0,
            min_recall=1.0,
        )
    )

    history = pd.read_parquet(summary["history_path"])
    assert summary["status"] == "passed"
    assert summary["quality_status"] == "complete"
    assert summary["pit_class"] == "reconstructed_pit"
    assert summary["revision_safe"] is False
    assert len(history) == 6
    assert set(history["ts_code"]) == {"000001.SZ", "000002.SZ", "000004.SZ"}
    assert summary["cross_validation"]["precision"] == 1.0
    assert summary["cross_validation"]["recall"] == 1.0


def test_st_cross_validation_canonicalizes_historical_bse_codes(tmp_path: Path) -> None:
    namechange = tmp_path / "namechange.parquet"
    pd.DataFrame(
        [
            {
                "ts_code": "920305.BJ",
                "name": "*ST云创",
                "start_date": "20250506",
                "end_date": "20250507",
                "ann_date": "20250429",
            }
        ]
    ).to_parquet(namechange, index=False)
    trade_cal = tmp_path / "trade_cal.parquet"
    pd.DataFrame({"cal_date": ["20250506", "20250507"], "is_open": [1, 1]}).to_parquet(
        trade_cal, index=False
    )
    instruments = tmp_path / "instruments.parquet"
    pd.DataFrame(
        [{"ts_code": "920305.BJ", "list_date": "20210826", "delist_date": None}]
    ).to_parquet(instruments, index=False)
    stock_st = tmp_path / "stock_st.parquet"
    pd.DataFrame(
        [{"trade_date": date, "ts_code": "835305.BJ"} for date in ("20250506", "20250507")]
    ).to_parquet(stock_st, index=False)

    summary = constraints.build_reconstructed_st_history(
        constraints.ReconstructedSTOptions(
            namechange_path=namechange,
            trade_cal_path=trade_cal,
            instruments_path=instruments,
            stock_st_path=stock_st,
            out_dir=tmp_path / "built",
            start_date="20250506",
            end_date="20250507",
            min_precision=1.0,
            min_recall=1.0,
        )
    )

    assert summary["status"] == "passed"
    assert summary["cross_validation"]["canonicalized_bse_alias_rows"] == 2
