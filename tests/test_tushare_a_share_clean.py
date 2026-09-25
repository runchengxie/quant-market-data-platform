from __future__ import annotations

import hashlib
import json

import pandas as pd
import yaml

from market_data_platform.providers import tushare_a_share_clean, tushare_a_share_quality
from market_data_platform.providers.tushare_a_share_clean import (
    build_a_share_daily_clean,
    validate_a_share_daily_clean,
)
from market_data_platform.standardize.tushare.a_share_daily import (
    build_a_share_daily_clean as standardized_build_a_share_daily_clean,
)
from market_data_platform.standardize.tushare.a_share_daily_part01 import _derive_st_flag


def test_legacy_daily_clean_build_is_a_compatibility_facade() -> None:
    assert build_a_share_daily_clean is standardized_build_a_share_daily_clean


def test_st_flag_uses_trade_date_history_instead_of_current_name() -> None:
    daily = pd.DataFrame(
        {
            "symbol": ["000001.SZ", "000001.SZ"],
            "trade_date": ["20240102", "20240103"],
        }
    )
    history = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240103"]})
    assert _derive_st_flag(daily, history).tolist() == [False, True]
    assert _derive_st_flag(daily, None).isna().all()


def test_daily_clean_uses_validated_st_history_across_dates(tmp_path) -> None:
    raw = tmp_path / "raw"
    out = tmp_path / "clean"
    instruments = tmp_path / "instruments.parquet"
    history = tmp_path / "st_history_reconstructed.parquet"
    for date in ("20240102", "20240103"):
        _write_part(
            pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": date,
                        "close": 10.0,
                        "pre_close": 10.0,
                        "open": 10.0,
                        "high": 10.0,
                        "low": 10.0,
                        "vol": 100.0,
                        "amount": 1000.0,
                    }
                ]
            ),
            raw,
            date,
        )
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "name": "ST金龙鱼",
                "list_date": "20200101",
            }
        ]
    ).to_parquet(instruments, index=False)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20240103",
            }
        ]
    ).to_parquet(history, index=False)
    history.with_name("st_history_reconstructed.receipt.json").write_text(
        json.dumps(
            {
                "quality_status": "complete",
                "start_date": "20240102",
                "end_date": "20240103",
                "history_sha256": hashlib.sha256(history.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    manifest = build_a_share_daily_clean(
        daily_dir=raw,
        instruments_file=instruments,
        st_history_file=history,
        out_dir=out,
        memory_soft_limit_mb=0,
        memory_hard_limit_mb=0,
    )
    rows = pd.read_parquet(out / "data" / "000001.SZ.parquet")
    assert rows["is_st"].tolist() == [False, True]
    assert manifest["inputs"]["st_history_file"] == str(history)


def _write_part(frame, root, trade_date):
    path = root / "data" / f"trade_date={trade_date}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _baseline_row(symbol="600519.SH", trade_date="20260522", **overrides):
    row = {
        "symbol": symbol,
        "trade_date": trade_date,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "pre_close": 100.0,
        "vol": 1000.0,
        "amount": 100000.0,
        "tr_close": 100.0,
        "is_st": False,
        "is_suspended": False,
        "is_limit_up": False,
        "is_limit_down": False,
    }
    row.update(overrides)
    return row


def _write_clean_manifest(  # noqa: PLR0913
    root,
    *,
    rows,
    symbols,
    files,
    start_date="20260522",
    end_date="20260522",
    st_history_file=None,
):
    (root / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "tushare.a_share.daily_clean.v1",
                "dataset": "daily_clean",
                "query": {"start_date": start_date, "end_date": end_date},
                "inputs": {"st_history_file": st_history_file},
                "totals": {"rows": rows, "symbols": symbols, "files": files},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_build_a_share_daily_clean_merges_adjustment_valuation_and_limit_status(tmp_path):
    pd = __import__("pandas")
    daily_dir = tmp_path / "raw_daily"
    adj_dir = tmp_path / "adj_factor"
    daily_basic_dir = tmp_path / "daily_basic"
    limit_dir = tmp_path / "limit_status"
    instruments = tmp_path / "instruments.parquet"
    out_dir = tmp_path / "daily_clean"

    _write_part(
        pd.DataFrame(
            {
                "ts_code": ["600519.SH", "000001.SZ"],
                "trade_date": ["20260522", "20260522"],
                "open": [100.0, 10.0],
                "high": [110.0, 10.0],
                "low": [99.0, 9.5],
                "close": [110.0, 9.5],
                "pre_close": [100.0, 10.0],
                "vol": [1000.0, 0.0],
                "amount": [110000.0, 0.0],
            }
        ),
        daily_dir,
        "20260522",
    )
    _write_part(
        pd.DataFrame(
            {
                "ts_code": ["600519.SH", "000001.SZ"],
                "trade_date": ["20260522", "20260522"],
                "adj_factor": [2.0, 1.0],
            }
        ),
        adj_dir,
        "20260522",
    )
    _write_part(
        pd.DataFrame(
            {
                "ts_code": ["600519.SH", "000001.SZ"],
                "trade_date": ["20260522", "20260522"],
                "turnover_rate": [1.2, 0.5],
                "pe_ttm": [25.0, 6.0],
                "pb": [9.0, 0.8],
                "total_mv": [2000000.0, 300000.0],
            }
        ),
        daily_basic_dir,
        "20260522",
    )
    _write_part(
        pd.DataFrame(
            {
                "ts_code": ["600519.SH", "000001.SZ"],
                "trade_date": ["20260522", "20260522"],
                "up_limit": [110.0, 11.0],
                "down_limit": [90.0, 9.5],
            }
        ),
        limit_dir,
        "20260522",
    )
    pd.DataFrame(
        {
            "ts_code": ["600519.SH", "000001.SZ"],
            "name": ["贵州茅台", "平安银行"],
            "list_date": ["20010827", "19910403"],
        }
    ).to_parquet(instruments, index=False)

    manifest = build_a_share_daily_clean(
        daily_dir=daily_dir,
        adj_factor_dir=adj_dir,
        daily_basic_dir=daily_basic_dir,
        limit_status_dir=limit_dir,
        instruments_file=instruments,
        out_dir=out_dir,
        min_rows=2,
        min_symbols=2,
    )

    assert manifest["dataset"] == "daily_clean"
    assert manifest["quality"]["limit_up_rows"] == 1
    assert manifest["quality"]["limit_down_rows"] == 1
    assert manifest["quality"]["suspended_rows"] == 1
    output = pd.read_parquet(out_dir / "data" / "600519.SH.parquet")
    assert output.loc[0, "tr_close"] == 110.0
    assert output.loc[0, "pe_ttm"] == 25.0
    assert bool(output.loc[0, "is_limit_up"]) is True
    assert output.loc[0, "board"] == "MAIN"
    manifest_payload = yaml.safe_load((out_dir / "manifest.yml").read_text(encoding="utf-8"))
    assert manifest_payload["schema_version"] == "tushare.a_share.daily_clean.v1"


def test_build_a_share_daily_clean_repairs_missing_pre_close_and_list_date(tmp_path):
    pd = __import__("pandas")
    daily_dir = tmp_path / "raw_daily"
    instruments = tmp_path / "instruments.parquet"
    out_dir = tmp_path / "daily_clean"

    for trade_date, bj_close, missing_close in (
        ("20150914", 21.24, 10.0),
        ("20150915", 21.05, 10.5),
    ):
        _write_part(
            pd.DataFrame(
                {
                    "ts_code": ["832317.BJ", "000022.SZ"],
                    "trade_date": [trade_date, trade_date],
                    "open": [21.05, 10.0],
                    "high": [21.30, 10.8],
                    "low": [21.00, 9.9],
                    "close": [bj_close, missing_close],
                    "pre_close": [None if trade_date == "20150914" else 21.24, missing_close],
                    "vol": [1000.0, 2000.0],
                    "amount": [21000.0, 21000.0],
                }
            ),
            daily_dir,
            trade_date,
        )
    pd.DataFrame(
        {
            "ts_code": ["832317.BJ"],
            "name": ["观典防务"],
            "list_date": ["20200727"],
        }
    ).to_parquet(instruments, index=False)

    build_a_share_daily_clean(
        daily_dir=daily_dir,
        instruments_file=instruments,
        out_dir=out_dir,
        min_rows=4,
        min_symbols=2,
    )

    bj = pd.read_parquet(out_dir / "data" / "832317.BJ.parquet")
    missing = pd.read_parquet(out_dir / "data" / "000022.SZ.parquet")
    assert bj["pre_close"].tolist() == [21.24, 21.24]
    assert bj["list_date"].tolist() == ["20150914", "20150914"]
    assert bj["listed_days"].tolist() == [0, 1]
    assert missing["list_date"].tolist() == ["20150914", "20150914"]
    assert missing["listed_days"].tolist() == [0, 1]


def test_validate_a_share_daily_clean_reports_required_overlay_columns(tmp_path):
    pd = __import__("pandas")
    root = tmp_path / "daily_clean"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    pd.DataFrame([_baseline_row()]).to_parquet(data_dir / "600519.SH.parquet", index=False)
    _write_clean_manifest(root, rows=1, symbols=1, files=1)

    summary = validate_a_share_daily_clean(
        daily_clean_dir=root,
        min_rows=1,
        min_symbols=1,
        require_valuation=True,
        require_limit_status=True,
    )

    assert summary["status"] == "failed"
    assert "pe_ttm" in summary["errors"][0]
    assert "up_limit" in summary["errors"][0]


def test_build_a_share_daily_clean_falls_back_to_raw_prices_without_adj_factor(tmp_path):
    pd = __import__("pandas")
    daily_dir = tmp_path / "raw_daily"
    out_dir = tmp_path / "daily_clean"

    _write_part(
        pd.DataFrame(
            {
                "ts_code": ["600519.SH"],
                "trade_date": ["20260522"],
                "open": [100.0],
                "high": [110.0],
                "low": [99.0],
                "close": [108.0],
                "pre_close": [100.0],
                "vol": [1000.0],
                "amount": [108000.0],
            }
        ),
        daily_dir,
        "20260522",
    )

    manifest = build_a_share_daily_clean(
        daily_dir=daily_dir,
        out_dir=out_dir,
        min_rows=1,
        min_symbols=1,
    )

    output = pd.read_parquet(out_dir / "data" / "600519.SH.parquet")
    assert manifest["quality"]["missing_tr_close"] == 0
    assert output.loc[0, "tr_close"] == 108.0
    assert output.loc[0, "adjustment_source"] == "raw_unadjusted"


def test_build_a_share_daily_clean_streams_trade_date_batches(monkeypatch, tmp_path):
    pd = __import__("pandas")
    daily_dir = tmp_path / "raw_daily"
    out_dir = tmp_path / "daily_clean"

    for trade_date, close in (("20260522", 108.0), ("20260525", 109.0)):
        _write_part(
            pd.DataFrame(
                {
                    "ts_code": ["600519.SH"],
                    "trade_date": [trade_date],
                    "open": [100.0],
                    "high": [110.0],
                    "low": [99.0],
                    "close": [close],
                    "pre_close": [100.0],
                    "vol": [1000.0],
                    "amount": [108000.0],
                }
            ),
            daily_dir,
            trade_date,
        )

    monkeypatch.setattr(
        tushare_a_share_clean,
        "_read_parquet_parts",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("non-streaming read")),
    )

    manifest = build_a_share_daily_clean(
        daily_dir=daily_dir,
        out_dir=out_dir,
        min_rows=2,
        min_symbols=1,
        batch_trade_dates=1,
        memory_soft_limit_mb=0,
        memory_hard_limit_mb=0,
    )

    output = pd.read_parquet(out_dir / "data" / "600519.SH.parquet")
    assert output["trade_date"].tolist() == ["20260522", "20260525"]
    assert output["close"].tolist() == [108.0, 109.0]
    assert manifest["build"]["mode"] == "streaming_trade_date_to_symbol"
    assert manifest["build"]["staging_batches"] == 2
    assert manifest["build"]["compaction_scan"]["mode"] == "streaming_parquet_writer"
    assert manifest["build"]["compaction_scan"]["batches_scanned"] == 2
    assert manifest["build"]["memory_policy"] == {
        "soft_available_mb": None,
        "hard_available_mb": None,
    }
    assert not (out_dir / "_daily_clean_staging").exists()


def test_validate_a_share_daily_clean_streams_symbol_parts(monkeypatch, tmp_path):
    pd = __import__("pandas")
    root = tmp_path / "daily_clean"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    for symbol in ("600519.SH", "000001.SZ"):
        pd.DataFrame([_baseline_row(symbol=symbol)]).to_parquet(
            data_dir / f"{symbol}.parquet",
            index=False,
        )
    _write_clean_manifest(root, rows=2, symbols=2, files=2)

    monkeypatch.setattr(
        tushare_a_share_clean,
        "_read_parquet_parts",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("non-streaming read")),
    )
    monkeypatch.setattr(
        tushare_a_share_quality.pd,
        "read_parquet",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("whole-file pandas read")),
    )

    summary = validate_a_share_daily_clean(
        daily_clean_dir=root,
        min_rows=2,
        min_symbols=2,
    )

    assert summary["status"] == "passed"
    assert summary["build"]["validation_mode"] == "projected_parquet_batch_scan"
    assert summary["totals"] == {"rows": 2, "symbols": 2}


def test_validate_a_share_daily_clean_reports_manifest_drift_and_ohlc_samples(tmp_path):
    pd = __import__("pandas")
    root = tmp_path / "daily_clean"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    pd.DataFrame([_baseline_row(high=98.0)]).to_parquet(
        data_dir / "600519.SH.parquet",
        index=False,
    )
    _write_clean_manifest(root, rows=2, symbols=1, files=1)

    summary = validate_a_share_daily_clean(daily_clean_dir=root)

    assert summary["status"] == "failed"
    checks = {row["check"]: row for row in summary["checks"]}
    assert checks["ohlc_bounds"]["affected_rows"] == 1
    assert checks["ohlc_bounds"]["sample_rows"][0]["symbol"] == "600519.SH"
    assert checks["manifest_reconciliation"]["mismatches"]["rows"] == {
        "expected": 2,
        "actual": 1,
    }


def test_validate_a_share_daily_clean_rejects_unproven_st_history(tmp_path):
    pd = __import__("pandas")
    root = tmp_path / "daily_clean"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    report = tmp_path / "reports" / "daily_clean.json"
    trade_cal = tmp_path / "trade_cal.parquet"
    pd.DataFrame(
        [
            _baseline_row(
                pe_ttm=25.0,
                pb=9.0,
                total_mv=2000000.0,
                turnover_rate=1.2,
                up_limit=110.0,
                down_limit=90.0,
                board="MAIN",
                listed_days=9000,
                pct_chg=0.0,
            )
        ]
    ).to_parquet(data_dir / "600519.SH.parquet", index=False)
    pd.DataFrame({"cal_date": ["20260522"], "is_open": [1]}).to_parquet(trade_cal, index=False)
    _write_clean_manifest(root, rows=1, symbols=1, files=1)

    summary = validate_a_share_daily_clean(
        daily_clean_dir=root,
        profile="research",
        trade_cal_file=trade_cal,
        out=report,
    )

    assert summary["status"] == "failed"
    assert summary["lineage"]["st_provenance"] == "unknown_no_dated_history"
    assert summary["lineage"]["daily_basic_provenance"] == (
        "daily_valuation_overlay_not_pit_fundamentals"
    )
    checks = {check["check"]: check for check in summary["checks"]}
    assert checks["st_provenance"]["status"] == "failed"
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "failed"


def test_validate_a_share_daily_clean_research_profile_rejects_limit_flag_mismatch(tmp_path):
    pd = __import__("pandas")
    root = tmp_path / "daily_clean"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    trade_cal = tmp_path / "trade_cal.parquet"
    pd.DataFrame(
        [
            _baseline_row(
                close=110.0,
                high=110.0,
                pe_ttm=25.0,
                pb=9.0,
                total_mv=2000000.0,
                turnover_rate=1.2,
                up_limit=110.0,
                down_limit=90.0,
                board="MAIN",
                listed_days=9000,
                is_limit_up=False,
            )
        ]
    ).to_parquet(data_dir / "600519.SH.parquet", index=False)
    pd.DataFrame({"cal_date": ["20260522"], "is_open": [1]}).to_parquet(trade_cal, index=False)
    _write_clean_manifest(root, rows=1, symbols=1, files=1)

    summary = validate_a_share_daily_clean(
        daily_clean_dir=root,
        profile="research",
        trade_cal_file=trade_cal,
    )

    checks = {row["check"]: row for row in summary["checks"]}
    assert summary["status"] == "failed"
    assert checks["limit_up_flag_consistency"]["affected_rows"] == 1


def test_validate_a_share_daily_clean_research_profile_requires_trade_calendar(tmp_path):
    pd = __import__("pandas")
    root = tmp_path / "daily_clean"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            _baseline_row(
                pe_ttm=25.0,
                pb=9.0,
                total_mv=2000000.0,
                turnover_rate=1.2,
                up_limit=110.0,
                down_limit=90.0,
                board="MAIN",
                listed_days=9000,
            )
        ]
    ).to_parquet(data_dir / "600519.SH.parquet", index=False)
    _write_clean_manifest(root, rows=1, symbols=1, files=1)

    summary = validate_a_share_daily_clean(daily_clean_dir=root, profile="research")

    checks = {row["check"]: row for row in summary["checks"]}
    assert summary["status"] == "failed"
    assert checks["trading_calendar_input"]["status"] == "failed"


def test_validate_a_share_daily_clean_warning_rate_is_configurable(tmp_path):
    pd = __import__("pandas")
    root = tmp_path / "daily_clean"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    trade_cal = tmp_path / "trade_cal.parquet"
    pd.DataFrame(
        [
            _baseline_row(
                close=101.0,
                high=101.0,
                pe_ttm=25.0,
                pb=9.0,
                total_mv=2000000.0,
                turnover_rate=1.2,
                up_limit=110.0,
                down_limit=90.0,
                board="MAIN",
                listed_days=9000,
                pct_chg=0.0,
            )
        ]
    ).to_parquet(data_dir / "600519.SH.parquet", index=False)
    pd.DataFrame({"cal_date": ["20260522"], "is_open": [1]}).to_parquet(trade_cal, index=False)
    _write_clean_manifest(root, rows=1, symbols=1, files=1, st_history_file="fixture")

    rejected = validate_a_share_daily_clean(
        daily_clean_dir=root,
        profile="research",
        trade_cal_file=trade_cal,
        fail_on_severity="warning",
        max_warning_rate=0.0,
    )
    tolerated = validate_a_share_daily_clean(
        daily_clean_dir=root,
        profile="research",
        trade_cal_file=trade_cal,
        fail_on_severity="warning",
        max_warning_rate=1.0,
    )

    assert rejected["status"] == "failed"
    assert "pct_chg_consistency" in rejected["quality_verdict"]["failing_checks"]
    assert tolerated["status"] == "passed"
