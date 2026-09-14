import pandas as pd

from market_data_platform import data_providers_client
from market_data_platform.data_providers_public_api import fetch_daily, load_basic
from market_data_platform.providers import tushare_a_share


def test_provider_modules_are_owned_by_provider_namespace():
    assert tushare_a_share.DEFAULT_TOKEN_ENV_KEYS == ("TUSHARE_TOKEN", "TUSHARE_TOKEN_2")


def _daily_frame(symbol: str, start: str, end: str, *, close_offset: float = 0.0) -> pd.DataFrame:
    dates = pd.date_range(pd.to_datetime(start), pd.to_datetime(end), freq="D")
    rows = []
    for idx, trade_date in enumerate(dates):
        rows.append(
            {
                "trade_date": trade_date.strftime("%Y%m%d"),
                "symbol": symbol,
                "close": close_offset + float(idx + 1),
                "vol": 1000.0 + idx,
                "amount": 10000.0 + idx,
            }
        )
    return pd.DataFrame(rows)


def test_fetch_daily_symbol_cache_refresh_window_merges_monotonic(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    symbol = "AAA"
    cache_file = cache_dir / "a_share_tushare_daily_AAA.parquet"

    cached = _daily_frame(symbol, "20200101", "20200105", close_offset=0.0)
    cached.to_parquet(cache_file)

    fetch_ranges = []

    def fake_fetch(request):
        fetch_ranges.append((request.start_date, request.end_date))
        return _daily_frame(
            request.symbol,
            request.start_date,
            request.end_date,
            close_offset=100.0,
        )

    monkeypatch.setattr(data_providers_client, "_fetch_daily_from_provider", fake_fetch)

    data_cfg = {
        "provider": "tushare",
        "cache_mode": "symbol",
        "cache_refresh_days": 2,
        "cache_refresh_on_hit": False,
    }
    result = fetch_daily(
        "a_share",
        symbol,
        "20200102",
        "20200107",
        cache_dir,
        client=None,
        data_cfg=data_cfg,
    )

    assert fetch_ranges == [("20200104", "20200107")]
    assert result["trade_date"].tolist() == [
        "20200102",
        "20200103",
        "20200104",
        "20200105",
        "20200106",
        "20200107",
    ]
    assert result["trade_date"].is_monotonic_increasing
    assert result["trade_date"].nunique() == len(result)

    merged = pd.read_parquet(cache_file).sort_values("trade_date").reset_index(drop=True)
    refreshed_close = float(merged.loc[merged["trade_date"] == "20200104", "close"].iloc[0])
    assert refreshed_close > 100.0


def test_fetch_daily_symbol_cache_refresh_on_hit_triggers_tail_refresh(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    symbol = "AAA"
    cache_file = cache_dir / "a_share_tushare_daily_AAA.parquet"

    cached = _daily_frame(symbol, "20200101", "20200105", close_offset=0.0)
    cached.to_parquet(cache_file)

    fetch_ranges = []

    def fake_fetch(request):
        fetch_ranges.append((request.start_date, request.end_date))
        return _daily_frame(
            request.symbol,
            request.start_date,
            request.end_date,
            close_offset=200.0,
        )

    monkeypatch.setattr(data_providers_client, "_fetch_daily_from_provider", fake_fetch)

    data_cfg = {
        "provider": "tushare",
        "cache_mode": "symbol",
        "cache_refresh_days": 2,
        "cache_refresh_on_hit": True,
    }
    result = fetch_daily(
        "a_share",
        symbol,
        "20200102",
        "20200105",
        cache_dir,
        client=None,
        data_cfg=data_cfg,
    )

    assert fetch_ranges == [("20200104", "20200105")]
    assert result["trade_date"].tolist() == [
        "20200102",
        "20200103",
        "20200104",
        "20200105",
    ]


def test_fetch_daily_symbol_cache_skips_small_leading_calendar_gap(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    symbol = "AAA"
    cache_file = cache_dir / "a_share_tushare_daily_AAA.parquet"

    cached = _daily_frame(symbol, "20200102", "20200105", close_offset=0.0)
    cached.to_parquet(cache_file)

    fetch_ranges = []

    def fake_fetch(request):
        fetch_ranges.append((request.start_date, request.end_date))
        return _daily_frame(
            request.symbol,
            request.start_date,
            request.end_date,
            close_offset=300.0,
        )

    monkeypatch.setattr(data_providers_client, "_fetch_daily_from_provider", fake_fetch)

    data_cfg = {
        "provider": "tushare",
        "cache_mode": "symbol",
        "cache_refresh_days": 0,
        "cache_refresh_on_hit": False,
    }
    result = fetch_daily(
        "a_share",
        symbol,
        "20200101",
        "20200105",
        cache_dir,
        client=None,
        data_cfg=data_cfg,
    )

    assert fetch_ranges == []
    assert result["trade_date"].tolist() == [
        "20200102",
        "20200103",
        "20200104",
        "20200105",
    ]


def test_fetch_daily_symbol_cache_fetches_large_leading_gap(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    symbol = "AAA"
    cache_file = cache_dir / "a_share_tushare_daily_AAA.parquet"

    cached = _daily_frame(symbol, "20200110", "20200112", close_offset=0.0)
    cached.to_parquet(cache_file)

    fetch_ranges = []

    def fake_fetch(request):
        fetch_ranges.append((request.start_date, request.end_date))
        return _daily_frame(
            request.symbol,
            request.start_date,
            request.end_date,
            close_offset=400.0,
        )

    monkeypatch.setattr(data_providers_client, "_fetch_daily_from_provider", fake_fetch)

    data_cfg = {
        "provider": "tushare",
        "cache_mode": "symbol",
        "cache_refresh_days": 0,
        "cache_refresh_on_hit": False,
    }
    result = fetch_daily(
        "a_share",
        symbol,
        "20200101",
        "20200112",
        cache_dir,
        client=None,
        data_cfg=data_cfg,
    )

    assert fetch_ranges == [("20200101", "20200110")]
    assert result["trade_date"].tolist() == [
        "20200101",
        "20200102",
        "20200103",
        "20200104",
        "20200105",
        "20200106",
        "20200107",
        "20200108",
        "20200109",
        "20200110",
        "20200111",
        "20200112",
    ]


def test_fetch_daily_reads_from_local_asset_dir_without_remote_fetch(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = tmp_path / "daily_assets"
    data_dir = asset_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    symbol = "AAA"

    pd.DataFrame(
        {
            "trade_date": ["20200101", "20200102", "20200103"],
            "symbol": [symbol, symbol, symbol],
            "close": [10.0, 11.0, 12.0],
            "volume": [100.0, 110.0, 120.0],
            "total_turnover": [1000.0, 1100.0, 1200.0],
        }
    ).to_parquet(data_dir / f"{symbol}.parquet")

    result = fetch_daily(
        "a_share",
        symbol,
        "20200102",
        "20200103",
        cache_dir,
        client=None,
        data_cfg={
            "provider": "tushare",
            "cache_mode": "symbol",
            "cache_refresh_days": 0,
            "cache_refresh_on_hit": False,
            "column_map": {
                "trade_date": "trade_date",
                "close": "close",
                "vol": "volume",
                "amount": "total_turnover",
            },
            "tushare": {
                "daily_asset_dir": str(asset_dir),
            },
        },
    )

    assert result["trade_date"].tolist() == ["20200102", "20200103"]
    assert result["close"].tolist() == [11.0, 12.0]


def test_fetch_daily_local_asset_prefers_ts_code_over_order_book_id(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = tmp_path / "daily_assets"
    data_dir = asset_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    symbol = "00001.HK"

    pd.DataFrame(
        {
            "trade_date": ["20200102", "20200103"],
            "ts_code": [symbol, symbol],
            "order_book_id": ["00001.XHKG", "00001.XHKG"],
            "open": [10.0, 10.5],
            "close": [10.2, 10.7],
            "volume": [100.0, 120.0],
            "total_turnover": [1000.0, 1284.0],
        }
    ).to_parquet(data_dir / f"{symbol}.parquet")

    result = fetch_daily(
        "a_share",
        symbol,
        "20200102",
        "20200103",
        cache_dir,
        client=None,
        data_cfg={
            "provider": "tushare",
            "cache_mode": "symbol",
            "cache_refresh_days": 0,
            "cache_refresh_on_hit": False,
            "column_map": {
                "trade_date": "trade_date",
                "close": "close",
                "vol": "volume",
                "amount": "total_turnover",
            },
            "tushare": {
                "daily_asset_dir": str(asset_dir),
            },
        },
    )

    assert result["symbol"].tolist() == [symbol, symbol]
    assert "ts_code" not in result.columns


def test_fetch_daily_derives_tr_close_from_local_ex_factors(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = tmp_path / "daily_assets"
    ex_dir = tmp_path / "ex_factors"
    (asset_dir / "data").mkdir(parents=True, exist_ok=True)
    (ex_dir / "data").mkdir(parents=True, exist_ok=True)
    symbol = "AAA"

    pd.DataFrame(
        {
            "trade_date": ["20200101", "20200102", "20200103", "20200106"],
            "symbol": [symbol, symbol, symbol, symbol],
            "close": [10.0, 10.0, 8.0, 9.0],
            "volume": [100.0, 100.0, 100.0, 100.0],
            "total_turnover": [1000.0, 1000.0, 800.0, 900.0],
        }
    ).to_parquet(asset_dir / "data" / f"{symbol}.parquet")
    pd.DataFrame(
        {
            "ex_date": [pd.Timestamp("2020-01-03")],
            "ex_cum_factor": [1.25],
        }
    ).to_parquet(ex_dir / "data" / f"{symbol}.parquet")

    result = fetch_daily(
        "a_share",
        symbol,
        "20200101",
        "20200106",
        cache_dir,
        client=None,
        data_cfg={
            "provider": "tushare",
            "cache_mode": "symbol",
            "cache_refresh_days": 0,
            "cache_refresh_on_hit": False,
            "column_map": {
                "trade_date": "trade_date",
                "close": "close",
                "vol": "volume",
                "amount": "total_turnover",
            },
            "tushare": {
                "daily_asset_dir": str(asset_dir),
                "ex_factors_dir": str(ex_dir),
            },
        },
    )

    assert result["tr_close"].round(4).tolist() == [10.0, 10.0, 10.0, 11.25]
    assert result.attrs["tr_close_meta"]["source"] == "local_ex_factors"


def test_load_basic_from_local_asset_accepts_name_fallback_columns(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    instruments_file = tmp_path / "a_share_instruments.parquet"

    pd.DataFrame(
        {
            "ts_code": ["600000.XSHG", "000001.XSHE"],
            "symbol": ["PF Bank", "Ping An Bank"],
            "listed_date": ["1999-11-10", "1991-04-03"],
            "eng_symbol": ["PF BANK", "PING AN"],
        }
    ).to_parquet(instruments_file)

    result = load_basic(
        "a_share",
        cache_dir,
        client=None,
        data_cfg={
            "provider": "tushare",
            "tushare": {
                "instruments_file": str(instruments_file),
            },
        },
        symbols=["600000.SH"],
    )
    assert result is not None

    assert result["symbol"].tolist() == ["600000.SH"]
    assert result["name"].tolist() == ["PF Bank"]
    assert result["list_date"].tolist() == ["19991110"]


def test_load_basic_from_local_asset_normalizes_a_share_order_book_id(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    instruments_file = tmp_path / "a_share_instruments.parquet"

    pd.DataFrame(
        {
            "symbol": ["600000.SH", "000001.SZ"],
            "order_book_id": ["600000.XSHG", "000001.XSHE"],
            "name": ["PF Bank", "Ping An Bank"],
            "listed_date": ["1999-11-10", "1991-04-03"],
        }
    ).to_parquet(instruments_file)

    result = load_basic(
        "a_share",
        cache_dir,
        client=None,
        data_cfg={
            "provider": "tushare",
            "tushare": {
                "instruments_file": str(instruments_file),
            },
        },
        symbols=["600000.SH"],
    )
    assert result is not None

    assert result["symbol"].tolist() == ["600000.SH"]
    assert result["name"].tolist() == ["PF Bank"]
    assert result["list_date"].tolist() == ["19991110"]


def test_load_basic_from_local_asset_normalizes_a_share_order_book_id_via_ts_code(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    instruments_file = tmp_path / "a_share_instruments.parquet"

    pd.DataFrame(
        {
            "order_book_id": ["600000.XSHG", "000001.XSHE"],
            "name": ["PF Bank", "Ping An Bank"],
            "listed_date": ["1999-11-10", "1991-04-03"],
        }
    ).to_parquet(instruments_file)

    result = load_basic(
        "a_share",
        cache_dir,
        client=None,
        data_cfg={
            "provider": "tushare",
            "tushare": {
                "instruments_file": str(instruments_file),
            },
        },
        symbols=["600000.SH"],
    )
    assert result is not None

    assert result["symbol"].tolist() == ["600000.SH"]
    assert result["name"].tolist() == ["PF Bank"]
    assert result["list_date"].tolist() == ["19991110"]


def test_fetch_daily_backfills_tr_close_for_existing_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ex_dir = tmp_path / "ex_factors"
    (ex_dir / "data").mkdir(parents=True, exist_ok=True)
    symbol = "AAA"
    cache_file = cache_dir / "a_share_tushare_daily_AAA.parquet"

    pd.DataFrame(
        {
            "trade_date": ["20200101", "20200102", "20200103"],
            "ts_code": [symbol, symbol, symbol],
            "symbol": [symbol, symbol, symbol],
            "close": [10.0, 10.0, 8.0],
            "vol": [100.0, 100.0, 100.0],
            "amount": [1000.0, 1000.0, 800.0],
        }
    ).to_parquet(cache_file)
    pd.DataFrame(
        {
            "ex_date": [pd.Timestamp("2020-01-03")],
            "ex_cum_factor": [1.25],
        }
    ).to_parquet(ex_dir / "data" / f"{symbol}.parquet")

    result = fetch_daily(
        "a_share",
        symbol,
        "20200101",
        "20200103",
        cache_dir,
        client=None,
        data_cfg={
            "provider": "tushare",
            "cache_mode": "symbol",
            "cache_refresh_days": 0,
            "cache_refresh_on_hit": False,
            "tushare": {
                "ex_factors_dir": str(ex_dir),
            },
        },
    )

    assert result["tr_close"].round(4).tolist() == [10.0, 10.0, 10.0]
    assert result.attrs["tr_close_meta"]["source"] == "local_ex_factors"
    cached = pd.read_parquet(cache_file)
    assert "tr_close" in cached.columns
    assert cached["tr_close"].round(4).tolist() == [10.0, 10.0, 10.0]


def test_fetch_daily_preserves_input_tr_close_when_ex_factors_missing(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = tmp_path / "daily_assets"
    ex_dir = tmp_path / "ex_factors"
    (asset_dir / "data").mkdir(parents=True, exist_ok=True)
    (ex_dir / "data").mkdir(parents=True, exist_ok=True)
    symbol = "AAA"

    pd.DataFrame(
        {
            "trade_date": ["20200101", "20200102", "20200103"],
            "symbol": [symbol, symbol, symbol],
            "close": [10.0, 10.0, 8.0],
            "tr_close": [10.0, 10.0, 10.0],
            "volume": [100.0, 100.0, 100.0],
            "total_turnover": [1000.0, 1000.0, 800.0],
        }
    ).to_parquet(asset_dir / "data" / f"{symbol}.parquet")

    result = fetch_daily(
        "a_share",
        symbol,
        "20200101",
        "20200103",
        cache_dir,
        client=None,
        data_cfg={
            "provider": "tushare",
            "cache_mode": "symbol",
            "cache_refresh_days": 0,
            "cache_refresh_on_hit": False,
            "column_map": {
                "trade_date": "trade_date",
                "close": "close",
                "vol": "volume",
                "amount": "total_turnover",
            },
            "tushare": {
                "daily_asset_dir": str(asset_dir),
                "ex_factors_dir": str(ex_dir),
            },
        },
    )

    assert result["tr_close"].round(4).tolist() == [10.0, 10.0, 10.0]
    assert result.attrs["tr_close_meta"]["source"] == "input_frame_missing_ex_factors"
