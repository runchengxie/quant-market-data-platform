from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from market_data_platform.research_views.daily_watch20_data import (
    DailyWatch20Assets,
    load_daily_watch20_daily,
)


def _rows() -> pd.DataFrame:
    base = {
        "open": 10.0,
        "adj_open": 10.0,
        "up_limit": 11.0,
        "down_limit": 9.0,
        "tr_close": 10.1,
        "high": 10.2,
        "low": 9.9,
        "close": 10.1,
        "amount": 1_000_000.0,
        "turnover_rate": 1.2,
        "volume_ratio": 1.1,
        "total_mv": 100_000_000.0,
        "pb": 1.5,
        "pe_ttm": 12.0,
        "ps_ttm": 2.4,
        "listed_days": 300,
        "board": "main",
        "is_st": False,
        "is_suspended": False,
        "is_limit_up": False,
        "is_limit_down": False,
    }
    return pd.DataFrame(
        [
            {**base, "trade_date": "20260828", "symbol": "000001.SZ"},
            {
                **base,
                "trade_date": "20260831",
                "symbol": "000001.SZ",
                "pb": 1.6,
                "pe_ttm": 12.5,
                "ps_ttm": 2.5,
            },
        ]
    )


def _assets(tmp_path: Path) -> DailyWatch20Assets:
    daily = tmp_path / "daily"
    data_dir = daily / "data"
    data_dir.mkdir(parents=True)
    # A1's local RED/GREEN harness substitutes only the unavailable
    # DuckDB/PyArrow execution boundary; production tests still exercise the loader.
    (data_dir / "part.parquet").write_bytes(b"test-placeholder")
    marker = tmp_path / "marker"
    marker.write_text("x", encoding="utf-8")
    return DailyWatch20Assets(
        data_root=tmp_path,
        current_contract=marker,
        daily_clean=daily,
        instruments=marker,
        trade_cal=marker,
        minute_current=tmp_path,
        minute_coverage=None,
        daily_as_of="20260831",
        minute_date_min=None,
        minute_date_max=None,
    )


def _patch_duckdb(monkeypatch, source: pd.DataFrame, *, selected_date: str) -> None:
    class _FakeConnection:
        def __init__(self) -> None:
            self.selected = source.iloc[0:0].copy()

        def execute(self, query: str):
            if "SELECT" not in query:
                return self
            projection = query.split("SELECT", 1)[1].split("FROM", 1)[0]
            columns = [item.strip() for item in projection.split(",")]
            frame = source.loc[source["trade_date"].eq(selected_date), columns]
            self.selected = frame.reset_index(drop=True)
            return self

        def fetch_df(self) -> pd.DataFrame:
            return self.selected.copy()

        def close(self) -> None:
            return None

    class _FakeDuckDB:
        @staticmethod
        def connect() -> _FakeConnection:
            return _FakeConnection()

    monkeypatch.setattr(
        "market_data_platform.research_views.daily_watch20_data._duckdb",
        lambda: _FakeDuckDB(),
    )


def _patch_duckdb_result(monkeypatch, result: pd.DataFrame) -> None:
    class _FakeConnection:
        def execute(self, _query: str):
            return self

        def fetch_df(self) -> pd.DataFrame:
            return result.copy()

        def close(self) -> None:
            return None

    class _FakeDuckDB:
        @staticmethod
        def connect() -> _FakeConnection:
            return _FakeConnection()

    monkeypatch.setattr(
        "market_data_platform.research_views.daily_watch20_data._duckdb",
        lambda: _FakeDuckDB(),
    )


def test_daily_watch20_loader_exposes_all_three_valuation_inputs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    assets = _assets(tmp_path)
    _patch_duckdb(monkeypatch, _rows(), selected_date="20260828")

    loaded = load_daily_watch20_daily(
        assets,
        start_date="20260828",
        end_date="20260828",
        threads=1,
    )

    assert list(loaded["trade_date"].astype(str)) == ["20260828"]
    assert loaded.loc[0, ["pb", "pe_ttm", "ps_ttm"]].to_dict() == {
        "pb": 1.5,
        "pe_ttm": 12.0,
        "ps_ttm": 2.4,
    }
    assert not loaded.duplicated(["trade_date", "symbol"]).any()


def test_daily_watch20_loader_rejects_duplicate_stock_date_rows(
    monkeypatch,
    tmp_path: Path,
) -> None:
    first = _rows().iloc[[0]].copy()
    source = pd.concat([first, first], ignore_index=True)
    assets = _assets(tmp_path)
    _patch_duckdb(monkeypatch, source, selected_date="20260828")

    with pytest.raises(ValueError, match="duplicate stock-date"):
        load_daily_watch20_daily(
            assets,
            start_date="20260828",
            end_date="20260828",
            threads=1,
        )


def test_daily_watch20_loader_rejects_missing_valuation_result_columns(
    monkeypatch,
    tmp_path: Path,
) -> None:
    assets = _assets(tmp_path)
    result = _rows().iloc[[0]].drop(columns="ps_ttm")
    _patch_duckdb_result(monkeypatch, result)

    with pytest.raises(ValueError, match="missing columns.*ps_ttm"):
        load_daily_watch20_daily(
            assets,
            start_date="20260828",
            end_date="20260828",
            threads=1,
        )


def test_daily_watch20_loader_rejects_rows_outside_requested_date_range(
    monkeypatch,
    tmp_path: Path,
) -> None:
    assets = _assets(tmp_path)
    result = _rows().iloc[[1]].copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"])
    _patch_duckdb_result(monkeypatch, result)

    with pytest.raises(ValueError, match="escaped the requested date range"):
        load_daily_watch20_daily(
            assets,
            start_date="20260828",
            end_date="20260828",
            threads=1,
        )
