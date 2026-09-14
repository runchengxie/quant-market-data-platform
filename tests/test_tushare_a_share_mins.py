from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pandas as pd
import pytest

from market_data_platform.cli import build_parser
from market_data_platform.cli_tushare_core import (
    _handle_minute_quota_status,
    _handle_mirror_mins,
)
from market_data_platform.providers import _a_share_mins_universe as _universe
from market_data_platform.providers import tushare_a_share_mins as mins
from market_data_platform.providers._bse_code_mapping import (
    BSE_HISTORICAL_BY_920,
    bse_minute_request_symbol,
)
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
from market_data_platform.tushare_minute_quota import (
    MinuteQuotaConfig,
    MinuteQuotaExceeded,
    MinuteQuotaLedger,
    MinuteQuotaPoolClosed,
    MinuteQuotaRequestPolicy,
)

TRADE_DATE = "20260706"
STOCK_HISTORY_COLUMNS = pd.Index(
    ["ts_code", "list_status", "list_date", "delist_date", "curr_type"]
)


def _bars(symbols: list[str]) -> pd.DataFrame:
    times = [
        *pd.date_range(f"{TRADE_DATE} 09:30:00", periods=121, freq="min"),
        *pd.date_range(f"{TRADE_DATE} 13:01:00", periods=120, freq="min"),
    ]
    rows = [(symbol, trade_time) for symbol in symbols for trade_time in times]
    return pd.DataFrame(
        {
            "ts_code": [symbol for symbol, _trade_time in rows],
            "trade_time": [trade_time for _symbol, trade_time in rows],
            "open": [10.0] * len(rows),
            "close": [10.1] * len(rows),
            "high": [10.2] * len(rows),
            "low": [9.9] * len(rows),
            "vol": [100.0] * len(rows),
            "amount": [1_000.0] * len(rows),
            "trade_date": [TRADE_DATE] * len(rows),
        }
    )


def _install_fake_api(
    monkeypatch: pytest.MonkeyPatch,
    pro_bar: Any,
    *,
    pro: Any | None = None,
    client_calls: list[dict[str, Any]] | None = None,
    mock_universe: bool = True,
) -> tuple[object, Any]:
    tushare_module: Any = ModuleType("tushare")
    tushare_module.pro_bar = pro_bar
    monkeypatch.setitem(sys.modules, "tushare", tushare_module)
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    monkeypatch.delenv("TUSHARE_TOKEN_2", raising=False)
    monkeypatch.setattr(
        mins,
        "_trading_dates_from_provider",
        lambda _pro, *, start_date, end_date, policy: [TRADE_DATE],
    )
    if mock_universe:
        symbols = [f"{index:06d}.SZ" for index in range(1, 10)]
        history = pd.DataFrame(
            {
                "ts_code": symbols,
                "list_status": ["L"] * len(symbols),
                "list_date": ["20100101"] * len(symbols),
                "delist_date": [""] * len(symbols),
                "curr_type": ["CNY"] * len(symbols),
            }
        )
        history.attrs["status_counts"] = {"L": len(symbols), "D": 0, "P": 0, "G": 0}
        history.attrs["all_stock_symbols"] = set(symbols)
        history.attrs["non_a_stock_symbols"] = set()
        monkeypatch.setattr(mins, "_stock_history_from_provider", lambda _pro, _policy: history)
        monkeypatch.setattr(
            mins,
            "_traded_symbols_from_provider",
            lambda _pro, *, trade_date, policy, stock_history, exchange=None: set(symbols),
        )

    if pro is None:
        pro = type("FakeMinutePro", (), {})()

    def stk_mins(**kwargs: Any) -> pd.DataFrame:
        return tushare_module.pro_bar(**kwargs)

    cast(Any, pro).stk_mins = stk_mins
    from market_data_platform.providers import tushare_a_share

    def get_client(**kwargs: Any) -> Any:
        if client_calls is not None:
            client_calls.append(kwargs)
        return pro

    monkeypatch.setattr(tushare_a_share, "get_tushare_client", get_client)
    return pro, tushare_module


def _options(
    output_dir: Path, symbols: list[str], *, batch_size: int = 2
) -> mins.MinsMirrorOptions:
    return mins.MinsMirrorOptions(
        start_date=TRADE_DATE,
        end_date=TRADE_DATE,
        symbols=symbols,
        output_dir=output_dir,
        skip_existing=True,
        batch_size=batch_size,
        cooldown_seconds=0,
        gc_frequency=100,
        request_policy=TushareRequestPolicy(
            attempts=1,
            retry_sleep_seconds=0,
            retry_max_sleep_seconds=0,
        ),
    )


def test_mirror_accepts_newest_first_trading_dates(tmp_path: Path) -> None:
    options = replace(
        _options(tmp_path, ["000001.SZ"]),
        start_date="20220707",
        end_date="20220711",
        trading_dates=["20220711", "20220708", "20220707"],
    )

    dates = mins._resolve_mirror_trading_dates(
        None,
        options,
        TushareRequestPolicy(),
    )

    assert dates == ["20220711", "20220708", "20220707"]


def _sidecar(output_dir: Path) -> dict[str, Any]:
    path = output_dir / f"trade_date={TRADE_DATE}" / mins.COMPLETENESS_FILENAME
    return json.loads(path.read_text(encoding="utf-8"))


def _partition(output_dir: Path) -> pd.DataFrame:
    path = output_dir / f"trade_date={TRADE_DATE}" / "part-00000.parquet"
    return pd.read_parquet(path)


def test_mirror_batches_requests_and_skips_complete_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    client_calls: list[dict[str, Any]] = []

    def fake_pro_bar(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs)
        return _bars(kwargs["ts_code"].split(","))

    pro, _tushare_module = _install_fake_api(monkeypatch, fake_pro_bar, client_calls=client_calls)
    symbols = ["000005.SZ", "000001.SZ", "000003.SZ", "000002.SZ", "000004.SZ"]
    options = _options(tmp_path, symbols, batch_size=2)

    result = mins.mirror_minute_bars(options)

    assert result["dates_fetched"] == 1
    assert result["requests_made"] == 3
    assert [call["ts_code"] for call in calls] == [
        "000001.SZ,000002.SZ",
        "000003.SZ,000004.SZ",
        "000005.SZ",
    ]
    assert all(call["limit"] == 8_000 and call["freq"] == "1min" for call in calls)
    assert client_calls[0]["disable_proxy"] is True
    assert list(_partition(tmp_path).columns) == list(mins.DEFAULT_MINS_FIELDS)
    assert _sidecar(tmp_path)["status"] == "complete"

    calls.clear()
    skipped = mins.mirror_minute_bars(options)

    assert skipped["dates_fetched"] == 0
    assert skipped["dates_skipped"] == 1
    assert skipped["requests_made"] == 0
    assert calls == []


def test_mirror_batch_size_uses_8000_row_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_pro_bar(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs["ts_code"])
        return _bars(kwargs["ts_code"].split(","))

    _install_fake_api(monkeypatch, fake_pro_bar)
    options = _options(tmp_path, [], batch_size=mins.MAX_MINS_BATCH_SIZE)

    result = mins.mirror_minute_bars(options)

    assert mins.DEFAULT_MINS_BATCH_SIZE == 20
    assert mins.MAX_MINS_BATCH_SIZE == 33
    assert mins.MAX_MINS_BATCH_SIZE * mins.MINUTE_BARS_PER_DAY == 7_953
    assert (mins.MAX_MINS_BATCH_SIZE + 1) * mins.MINUTE_BARS_PER_DAY > (
        mins.MINS_RESPONSE_ROW_LIMIT
    )
    assert result["requests_made"] == 1
    assert len(calls) == 1

    with pytest.raises(ValueError, match="batch_size must be between 1 and 33 for 1min data"):
        mins.mirror_minute_bars(replace(options, batch_size=34))


def test_complete_partition_promotion_receipt_requires_bound_full_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_pro_bar(**kwargs: Any) -> pd.DataFrame:
        return _bars(kwargs["ts_code"].split(","))

    _install_fake_api(monkeypatch, fake_pro_bar)
    mins.mirror_minute_bars(_options(tmp_path, [], batch_size=20))

    part_dir = tmp_path / f"trade_date={TRADE_DATE}"
    receipt = mins.validate_complete_minute_partition(
        part_dir,
        trade_date=TRADE_DATE,
    )

    assert receipt["rows"] == 9 * mins.MINUTE_BARS_PER_DAY
    assert receipt["symbols"] == 9
    assert receipt["market_symbol_counts"] == {"SH": 0, "SZ": 9, "BJ": 0}
    assert receipt["universe_rule"] == mins.UNIVERSE_RULE

    part_path = part_dir / "part-00000.parquet"
    changed = pd.read_parquet(part_path)
    changed.loc[0, "close"] = 11.0
    changed.to_parquet(part_path, index=False)
    with pytest.raises(ValueError, match="changed after its sidecar"):
        mins.validate_complete_minute_partition(part_dir, trade_date=TRADE_DATE)


def test_complete_partition_promotion_rejects_explicit_symbol_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_pro_bar(**kwargs: Any) -> pd.DataFrame:
        return _bars(kwargs["ts_code"].split(","))

    _install_fake_api(monkeypatch, fake_pro_bar)
    mins.mirror_minute_bars(_options(tmp_path, ["000001.SZ"], batch_size=20))

    with pytest.raises(ValueError, match="not a full daily universe mirror"):
        mins.validate_complete_minute_partition(
            tmp_path / f"trade_date={TRADE_DATE}",
            trade_date=TRADE_DATE,
        )


def test_legacy_partition_without_sidecar_is_fully_refetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    part_dir = tmp_path / f"trade_date={TRADE_DATE}"
    part_dir.mkdir(parents=True)
    # Legacy files embedded trade_date even though it is also a Hive partition key.
    _bars(["000001.SZ"]).to_parquet(part_dir / "part-00000.parquet", index=False)
    calls: list[str] = []

    def fake_pro_bar(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs["ts_code"])
        return _bars(kwargs["ts_code"].split(","))

    _install_fake_api(monkeypatch, fake_pro_bar)
    options = _options(tmp_path, ["000001.SZ", "000002.SZ", "000003.SZ"], batch_size=20)

    result = mins.mirror_minute_bars(options)

    assert result["requests_made"] == 1
    assert calls == ["000001.SZ,000002.SZ,000003.SZ"]
    assert set(_partition(tmp_path)["ts_code"]) == {
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
    }
    assert _sidecar(tmp_path)["missing_request_symbols"] == []


def test_full_legacy_partition_without_sidecar_is_not_implicitly_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    part_dir = tmp_path / f"trade_date={TRADE_DATE}"
    part_dir.mkdir(parents=True)
    _bars(["000001.SZ", "000002.SZ", "000009.SZ"])[list(mins.DEFAULT_MINS_FIELDS)].to_parquet(
        part_dir / "part-00000.parquet", index=False
    )

    calls: list[str] = []

    def fake_pro_bar(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs["ts_code"])
        return _bars(kwargs["ts_code"].split(","))

    _install_fake_api(monkeypatch, fake_pro_bar)
    result = mins.mirror_minute_bars(_options(tmp_path, ["000001.SZ", "000002.SZ"]))

    assert result["dates_fetched"] == 1
    assert result["requests_made"] == 1
    assert calls == ["000001.SZ,000002.SZ"]
    assert set(_partition(tmp_path)["ts_code"]) == {"000001.SZ", "000002.SZ"}
    assert list((part_dir / "_quarantine").glob("*.parquet"))
    sidecar = _sidecar(tmp_path)
    assert sidecar["status"] == "complete"
    assert sidecar["completed_symbols"] == ["000001.SZ", "000002.SZ"]


def test_failed_batch_persists_partial_progress_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def failing_pro_bar(**kwargs: Any) -> pd.DataFrame:
        symbols = kwargs["ts_code"]
        calls.append(symbols)
        if symbols == "000003.SZ":
            raise ConnectionError("simulated outage")
        return _bars(symbols.split(","))

    _pro, tushare_module = _install_fake_api(monkeypatch, failing_pro_bar)
    options = _options(tmp_path, ["000001.SZ", "000002.SZ", "000003.SZ"], batch_size=2)

    with pytest.raises(RuntimeError, match="recoverable partial partition"):
        mins.mirror_minute_bars(options)

    sidecar = _sidecar(tmp_path)
    assert sidecar["status"] == "partial"
    assert sidecar["completed_symbols"] == ["000001.SZ", "000002.SZ"]
    assert sidecar["missing_request_symbols"] == ["000003.SZ"]
    assert set(_partition(tmp_path)["ts_code"]) == {"000001.SZ", "000002.SZ"}

    resume_calls: list[str] = []

    def resumed_pro_bar(**kwargs: Any) -> pd.DataFrame:
        resume_calls.append(kwargs["ts_code"])
        return _bars(kwargs["ts_code"].split(","))

    tushare_module.pro_bar = resumed_pro_bar  # type: ignore[attr-defined]
    result = mins.mirror_minute_bars(options)

    assert result["dates_fetched"] == 1
    assert resume_calls == ["000003.SZ"]
    assert _sidecar(tmp_path)["status"] == "complete"
    assert set(_partition(tmp_path)["ts_code"]) == {
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
    }


def test_partial_sidecar_redacts_token_from_provider_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "test-token"

    def failing_pro_bar(**_kwargs: Any) -> pd.DataFrame:
        raise ConnectionError(f"transport echoed {secret}")

    _install_fake_api(monkeypatch, failing_pro_bar)

    with pytest.raises(RuntimeError, match="recoverable partial partition"):
        mins.mirror_minute_bars(_options(tmp_path, ["000001.SZ"]))

    serialized = json.dumps(_sidecar(tmp_path))
    assert secret not in serialized
    assert "<redacted>" in serialized


def test_omitted_symbol_response_is_recovered_by_smaller_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def incomplete_pro_bar(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs["ts_code"])
        requested = kwargs["ts_code"].split(",")
        return _bars(requested[:1])

    _install_fake_api(monkeypatch, incomplete_pro_bar)
    options = _options(tmp_path, ["000001.SZ", "000002.SZ"], batch_size=2)

    result = mins.mirror_minute_bars(options)

    assert calls == ["000001.SZ,000002.SZ", "000002.SZ"]
    assert result["requests_made"] == 1
    assert result["fallback_requests_made"] == 1
    assert _sidecar(tmp_path)["status"] == "complete"
    assert set(_partition(tmp_path)["ts_code"]) == {"000001.SZ", "000002.SZ"}


def test_historical_bse_request_uses_old_code_and_persists_920_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def historical_pro_bar(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs["ts_code"])
        frame = _bars(kwargs["ts_code"].split(","))
        frame["trade_time"] += pd.Timestamp("20241128") - pd.Timestamp(TRADE_DATE)
        frame["trade_date"] = "20241128"
        return frame

    _install_fake_api(monkeypatch, historical_pro_bar, mock_universe=False)
    history = pd.DataFrame(
        {
            "ts_code": ["920000.BJ"],
            "list_status": ["L"],
            "list_date": ["20201223"],
            "delist_date": [""],
            "curr_type": ["CNY"],
        }
    )
    history.attrs["status_counts"] = {"L": 1, "D": 0, "P": 0, "G": 0}
    history.attrs["all_stock_symbols"] = {"920000.BJ"}
    history.attrs["non_a_stock_symbols"] = set()
    monkeypatch.setattr(mins, "_stock_history_from_provider", lambda _pro, _policy: history)
    monkeypatch.setattr(
        mins,
        "_traded_symbols_from_provider",
        lambda _pro, *, trade_date, policy, stock_history, exchange=None: {"920000.BJ"},
    )
    options = replace(
        _options(tmp_path, ["920000.BJ"], batch_size=1),
        start_date="20241128",
        end_date="20241128",
        trading_dates=["20241128"],
    )

    result = mins.mirror_minute_bars(options)

    assert calls == ["832000.BJ"]
    assert result["dates_fetched"] == 1
    partition = pd.read_parquet(tmp_path / "trade_date=20241128" / "part-00000.parquet")
    assert set(partition["ts_code"]) == {"920000.BJ"}


def test_bse_historical_code_transition_dates() -> None:
    assert len(BSE_HISTORICAL_BY_920) == 248
    assert len(set(BSE_HISTORICAL_BY_920.values())) == 248
    assert bse_minute_request_symbol("920819.BJ", "20250505") == "833819.BJ"
    assert bse_minute_request_symbol("920819.BJ", "20250506") == "920819.BJ"
    assert bse_minute_request_symbol("920000.BJ", "20251008") == "832000.BJ"
    assert bse_minute_request_symbol("920000.BJ", "20251009") == "920000.BJ"
    assert bse_minute_request_symbol("920002.BJ", "20241128") == "920002.BJ"


def test_persistently_omitted_symbol_remains_partial_after_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def incomplete_pro_bar(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs["ts_code"])
        requested = kwargs["ts_code"].split(",")
        return _bars([symbol for symbol in requested if symbol != "000002.SZ"])

    _install_fake_api(monkeypatch, incomplete_pro_bar)
    options = _options(tmp_path, ["000001.SZ", "000002.SZ"], batch_size=2)

    with pytest.raises(RuntimeError, match="incomplete batch"):
        mins.mirror_minute_bars(options)

    assert calls == ["000001.SZ,000002.SZ", "000002.SZ"]
    sidecar = _sidecar(tmp_path)
    assert sidecar["status"] == "partial"
    assert sidecar["completed_symbols"] == ["000001.SZ"]
    assert sidecar["missing_request_symbols"] == ["000002.SZ"]
    assert sidecar["error"]["issues"] == {"000002.SZ": "missing from response"}
    assert sidecar["error"]["fallback_requests_made"] == 1


def test_fully_empty_batch_is_not_recursively_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def empty_pro_bar(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs["ts_code"])
        return _bars([])

    _install_fake_api(monkeypatch, empty_pro_bar)
    options = _options(tmp_path, ["000001.SZ", "000002.SZ"], batch_size=2)

    with pytest.raises(RuntimeError, match="incomplete batch"):
        mins.mirror_minute_bars(options)

    assert calls == ["000001.SZ,000002.SZ"]
    sidecar = _sidecar(tmp_path)
    assert sidecar["completed_symbols"] == []
    assert sidecar["missing_request_symbols"] == ["000001.SZ", "000002.SZ"]
    assert sidecar["error"]["fallback_requests_made"] == 0


def test_persistently_omitted_symbol_does_not_block_later_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def incomplete_pro_bar(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs["ts_code"])
        requested = kwargs["ts_code"].split(",")
        return _bars([symbol for symbol in requested if symbol != "000002.SZ"])

    _install_fake_api(monkeypatch, incomplete_pro_bar)
    options = _options(
        tmp_path,
        ["000001.SZ", "000002.SZ", "000003.SZ"],
        batch_size=2,
    )

    with pytest.raises(RuntimeError, match="incomplete batch"):
        mins.mirror_minute_bars(options)

    assert calls == ["000001.SZ,000002.SZ", "000002.SZ", "000003.SZ"]
    sidecar = _sidecar(tmp_path)
    assert sidecar["completed_symbols"] == ["000001.SZ", "000003.SZ"]
    assert sidecar["missing_request_symbols"] == ["000002.SZ"]


def test_multi_date_mirror_continues_after_isolated_incomplete_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    second_date = "20260707"

    def incomplete_first_day(**kwargs: Any) -> pd.DataFrame:
        trade_date = kwargs["start_date"][:10].replace("-", "")
        requested = kwargs["ts_code"].split(",")
        available = (
            [symbol for symbol in requested if symbol != "000002.SZ"]
            if trade_date == TRADE_DATE
            else requested
        )
        frame = _bars(available)
        if trade_date != TRADE_DATE:
            frame["trade_time"] = frame["trade_time"] + pd.Timedelta(days=1)
            frame["trade_date"] = second_date
        return frame

    _install_fake_api(monkeypatch, incomplete_first_day)
    options = replace(
        _options(tmp_path, ["000001.SZ", "000002.SZ"], batch_size=2),
        end_date=second_date,
        trading_dates=[TRADE_DATE, second_date],
        continue_on_partial_dates=True,
    )

    with pytest.raises(mins.MinuteMirrorIncompleteDatesError) as caught:
        mins.mirror_minute_bars(options)

    assert caught.value.partial_dates == (TRADE_DATE,)
    assert caught.value.result["dates_fetched"] == 1
    assert caught.value.result["dates_partial"] == 1
    assert caught.value.result["partial_dates"] == [TRADE_DATE]
    second_sidecar = json.loads(
        (tmp_path / f"trade_date={second_date}" / mins.COMPLETENESS_FILENAME).read_text()
    )
    assert second_sidecar["status"] == "complete"


def test_truncated_240_bar_symbol_remains_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def truncated_pro_bar(**kwargs: Any) -> pd.DataFrame:
        return _bars(kwargs["ts_code"].split(",")).iloc[:-1]

    _install_fake_api(monkeypatch, truncated_pro_bar)
    options = _options(tmp_path, ["000001.SZ"])

    with pytest.raises(RuntimeError, match="incomplete batch"):
        mins.mirror_minute_bars(options)

    sidecar = _sidecar(tmp_path)
    assert sidecar["status"] == "partial"
    assert sidecar["completed_symbols"] == []
    assert sidecar["error"]["issues"] == {"000001.SZ": "expected 241 bars, received 240"}


def test_wrong_241_bar_session_grid_remains_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def wrong_grid_pro_bar(**kwargs: Any) -> pd.DataFrame:
        frame = _bars(kwargs["ts_code"].split(","))
        frame.loc[frame.index[-1], "trade_time"] = pd.Timestamp(f"{TRADE_DATE} 12:00:00")
        return frame

    _pro, tushare_module = _install_fake_api(monkeypatch, wrong_grid_pro_bar)
    options = _options(tmp_path, ["000001.SZ"])

    with pytest.raises(RuntimeError, match="incomplete batch"):
        mins.mirror_minute_bars(options)

    assert _sidecar(tmp_path)["error"]["issues"] == {
        "000001.SZ": "does not match the expected A-share 1min session grid"
    }

    tushare_module.pro_bar = (  # type: ignore[attr-defined]
        lambda **kwargs: _bars(kwargs["ts_code"].split(","))
    )
    mins.mirror_minute_bars(options)
    assert _sidecar(tmp_path)["status"] == "complete"
    assert len(_partition(tmp_path)) == mins.MINUTE_BARS_PER_DAY


def test_stale_complete_sidecar_does_not_skip_changed_parquet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def pro_bar(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs["ts_code"])
        return _bars(kwargs["ts_code"].split(","))

    _install_fake_api(monkeypatch, pro_bar)
    options = _options(tmp_path, ["000001.SZ", "000002.SZ"], batch_size=2)
    mins.mirror_minute_bars(options)
    original_sidecar = _sidecar(tmp_path)

    part_path = tmp_path / f"trade_date={TRADE_DATE}" / "part-00000.parquet"
    changed = _partition(tmp_path)
    changed["close"] = 999.0
    mins._atomic_write_partition(changed, part_path.parent, trade_date=TRADE_DATE)
    stat = part_path.stat()
    original_sidecar["partition"]["files"][0].update(
        {
            "rows": len(changed),
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    )
    sidecar_path = tmp_path / f"trade_date={TRADE_DATE}" / mins.COMPLETENESS_FILENAME
    sidecar_path.write_text(json.dumps(original_sidecar), encoding="utf-8")
    assert mins._sha256_file(part_path) != original_sidecar["partition"]["files"][0]["sha256"]
    calls.clear()

    result = mins.mirror_minute_bars(options)

    assert result["requests_made"] == 1
    assert calls == ["000001.SZ,000002.SZ"]
    rebound = _sidecar(tmp_path)
    assert rebound["status"] == "complete"
    assert _partition(tmp_path)["close"].eq(10.1).all()
    assert list((part_path.parent / "_quarantine").glob("*.parquet"))


def test_historical_universe_uses_all_stock_statuses_and_point_in_time_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class HistoricalPro:
        def __init__(self) -> None:
            self.statuses: list[str] = []
            self.daily_calls = 0

        def stock_basic(self, *, exchange: str, list_status: str, fields: str) -> pd.DataFrame:
            assert exchange == ""
            assert "delist_date" in fields
            self.statuses.append(list_status)
            rows = {
                "L": [
                    ("000001.SZ", "L", "20100101", "", "CNY"),
                    ("000009.SZ", "L", "20260707", "", "CNY"),
                    ("201872.SZ", "L", "20100101", "", "HKD"),
                ],
                "D": [("000002.SZ", "D", "20100101", TRADE_DATE, "CNY")],
                "P": [("000003.SZ", "P", "20150101", "", "CNY")],
                "G": [("000004.SZ", "G", "20260710", "", "CNY")],
            }[list_status]
            return pd.DataFrame(
                rows,
                columns=STOCK_HISTORY_COLUMNS,
            )

        def daily(self, *, trade_date: str, fields: str, limit: int, offset: int) -> pd.DataFrame:
            self.daily_calls += 1
            assert trade_date == TRADE_DATE
            assert fields == "ts_code,trade_date"
            assert limit == mins.DAILY_PAGE_SIZE
            assert offset == 0
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": [TRADE_DATE, TRADE_DATE],
                }
            )

        daily_basic = daily

    minute_calls: list[str] = []

    def pro_bar(**kwargs: Any) -> pd.DataFrame:
        minute_calls.extend(kwargs["ts_code"].split(","))
        return _bars(kwargs["ts_code"].split(","))

    pro = HistoricalPro()
    _install_fake_api(monkeypatch, pro_bar, pro=pro, mock_universe=False)
    options = _options(tmp_path, [], batch_size=2)

    mins.mirror_minute_bars(options)

    assert pro.statuses == ["L", "D", "P", "G"]
    assert set(minute_calls) == {"000001.SZ", "000002.SZ"}
    sidecar = _sidecar(tmp_path)
    assert sidecar["universe_rule"] == mins.UNIVERSE_RULE
    assert sidecar["universe_source"].startswith(
        "tushare.daily~daily_basic_SH_SZ(limit=5000,BJ=daily)+stock_basic("
    )
    assert sidecar["expected_symbols"] == ["000001.SZ", "000002.SZ"]
    assert len(sidecar["universe_hash"]) == 64

    minute_request_count = len(minute_calls)
    mins.mirror_minute_bars(options)
    assert pro.statuses == ["L", "D", "P", "G", "L", "D", "P", "G"]
    assert pro.daily_calls == 4
    assert len(minute_calls) == minute_request_count


def test_exchange_filter_uses_dynamic_daily_universe_and_binds_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    minute_calls: list[str] = []

    def pro_bar(**kwargs: Any) -> pd.DataFrame:
        minute_calls.extend(kwargs["ts_code"].split(","))
        return _bars(kwargs["ts_code"].split(","))

    _install_fake_api(monkeypatch, pro_bar, mock_universe=False)
    history = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "920001.BJ", "920002.BJ"],
            "list_status": ["L", "L", "L"],
            "list_date": ["20100101", "20211115", "20220101"],
            "delist_date": ["", "", ""],
            "curr_type": ["CNY", "CNY", "CNY"],
        }
    )
    history.attrs["status_counts"] = {"L": 3, "D": 0, "P": 0, "G": 0}
    history.attrs["all_stock_symbols"] = set(history["ts_code"])
    monkeypatch.setattr(mins, "_stock_history_from_provider", lambda _pro, _policy: history)
    monkeypatch.setattr(
        mins,
        "_traded_symbols_from_provider",
        lambda _pro, *, trade_date, policy, stock_history, exchange=None: {
            symbol
            for symbol in history["ts_code"]
            if exchange is None or symbol.endswith(f".{exchange}")
        },
    )
    monkeypatch.setattr(
        _universe,
        "_trading_dates_from_provider",
        lambda *_args, **_kwargs: pytest.fail("planned trading_dates must avoid trade_cal API"),
    )
    options = replace(
        _options(tmp_path, []),
        exchange="BJ",
        trading_dates=[TRADE_DATE],
    )

    result = mins.mirror_minute_bars(options)

    assert result["requests_made"] == 1
    assert minute_calls == ["920001.BJ", "920002.BJ"]
    sidecar = _sidecar(tmp_path)
    assert sidecar["expected_symbols"] == ["920001.BJ", "920002.BJ"]
    assert sidecar["universe_rule"].endswith(":exchange=BJ")
    assert sidecar["universe_source"].endswith("+exchange_filter(BJ)")


def test_exchange_filter_refuses_the_default_full_a_output() -> None:
    with pytest.raises(ValueError, match="require an explicit output_dir"):
        mins.mirror_minute_bars(
            mins.MinsMirrorOptions(
                start_date=TRADE_DATE,
                end_date=TRADE_DATE,
                exchange="BJ",
            )
        )


def test_stock_history_filters_noncanonical_legacy_codes_but_keeps_raw_master() -> None:
    columns = STOCK_HISTORY_COLUMNS

    class HistoricalSpecialCodePro:
        def stock_basic(self, *, exchange: str, list_status: str, fields: str) -> pd.DataFrame:
            assert exchange == ""
            assert fields == ",".join(columns)
            rows = {
                "L": [("000001.SZ", "L", "19910403", "", "CNY")],
                "D": [("T600018.SH", "D", "20000719", "20061020", "CNY")],
                "P": [],
                "G": [],
            }[list_status]
            return pd.DataFrame(rows, columns=pd.Index(columns))

    history = mins._stock_history_from_provider(
        HistoricalSpecialCodePro(),
        TushareRequestPolicy(attempts=1, retry_sleep_seconds=0, retry_max_sleep_seconds=0),
    )

    assert history["ts_code"].tolist() == ["000001.SZ"]
    assert history.attrs["all_stock_symbols"] == {"000001.SZ", "T600018.SH"}
    assert history.attrs["excluded_noncanonical_symbols"] == {"T600018.SH"}


def test_empty_stock_status_cannot_hide_a_daily_traded_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class IncompleteHistoryPro:
        def stock_basic(self, *, exchange: str, list_status: str, fields: str) -> pd.DataFrame:
            if list_status == "L":
                return pd.DataFrame(
                    [("000001.SZ", "L", "20100101", "", "CNY")],
                    columns=STOCK_HISTORY_COLUMNS,
                )
            return pd.DataFrame(columns=STOCK_HISTORY_COLUMNS)

        def daily(self, *, trade_date: str, fields: str, limit: int, offset: int) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": [trade_date, trade_date],
                }
            )

        def daily_basic(
            self, *, trade_date: str, fields: str, limit: int, offset: int
        ) -> pd.DataFrame:
            return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [trade_date]})

    def unexpected_pro_bar(**_kwargs: Any) -> pd.DataFrame:
        raise AssertionError("inconsistent universe must fail before minute requests")

    _install_fake_api(
        monkeypatch,
        unexpected_pro_bar,
        pro=IncompleteHistoryPro(),
        mock_universe=False,
    )

    with pytest.raises(RuntimeError, match="absent from the CNY stock_basic master"):
        mins.mirror_minute_bars(_options(tmp_path, []))


def test_historical_daily_intersection_can_retain_provider_only_symbols(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class HistoricalProviderOnlyPro:
        def daily(self, *, trade_date: str, fields: str, limit: int, offset: int) -> pd.DataFrame:
            symbols = ["000001.SZ", "000022.SZ", "000043.SZ"] if offset == 0 else []
            return pd.DataFrame({"ts_code": symbols, "trade_date": [trade_date] * len(symbols)})

        daily_basic = daily

    def pro_bar(**kwargs: Any) -> pd.DataFrame:
        return _bars(kwargs["ts_code"].split(","))

    history = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "list_status": ["L"],
            "list_date": ["20100101"],
            "delist_date": [""],
            "curr_type": ["CNY"],
        }
    )
    history.attrs["status_counts"] = {"L": 1, "D": 0, "P": 0, "G": 0}
    history.attrs["all_stock_symbols"] = {"000001.SZ"}
    _install_fake_api(
        monkeypatch,
        pro_bar,
        pro=HistoricalProviderOnlyPro(),
        mock_universe=False,
    )
    monkeypatch.setattr(mins, "_stock_history_from_provider", lambda _pro, _policy: history)

    mins.mirror_minute_bars(_options(tmp_path, []))

    assert _sidecar(tmp_path)["expected_symbols"] == [
        "000001.SZ",
        "000022.SZ",
        "000043.SZ",
    ]


def test_daily_traded_universe_is_paginated(monkeypatch: pytest.MonkeyPatch) -> None:
    class PagedDailyPro:
        def __init__(self) -> None:
            self.offsets: list[int] = []

        def daily(self, *, trade_date: str, fields: str, limit: int, offset: int) -> pd.DataFrame:
            self.offsets.append(offset)
            symbols = {
                0: ["000001.SZ", "000002.SZ"],
                2: ["000003.SZ"],
            }.get(offset, [])
            return pd.DataFrame({"ts_code": symbols, "trade_date": [trade_date] * len(symbols)})

        daily_basic = daily

    monkeypatch.setattr(mins, "DAILY_PAGE_SIZE", 2)
    pro = PagedDailyPro()
    history = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"]})
    history.attrs["all_stock_symbols"] = set(history["ts_code"])

    symbols = mins._traded_symbols_from_provider(
        pro,
        trade_date=TRADE_DATE,
        policy=TushareRequestPolicy(attempts=1),
        stock_history=history,
    )

    assert symbols == {"000001.SZ", "000002.SZ", "000003.SZ"}
    assert pro.offsets == [0, 2, 0, 2]


def test_historical_code_transition_augments_live_master_and_prefers_date_code() -> None:
    class TransitionPro:
        def stock_basic(self, *, exchange: str, list_status: str, fields: str) -> pd.DataFrame:
            columns = STOCK_HISTORY_COLUMNS
            if list_status != "L":
                return pd.DataFrame(columns=pd.Index(columns))
            return pd.DataFrame(
                [("302132.SZ", "L", "20100827", "", "CNY")],
                columns=pd.Index(columns),
            )

        def _daily_frame(self, trade_date: str, offset: int) -> pd.DataFrame:
            symbols = ["300114.SZ", "302132.SZ"] if offset == 0 else []
            return pd.DataFrame({"ts_code": symbols, "trade_date": [trade_date] * len(symbols)})

        def daily(self, *, trade_date: str, fields: str, limit: int, offset: int) -> pd.DataFrame:
            return self._daily_frame(trade_date, offset)

        daily_basic = daily

    pro = TransitionPro()
    policy = TushareRequestPolicy(attempts=1)
    history = mins._stock_history_from_provider(pro, policy)
    alias = history.loc[history["ts_code"].eq("300114.SZ")].iloc[0]

    assert alias["list_status"] == "D"
    assert alias["list_date"] == "20100827"
    assert alias["delist_date"] == "20250214"
    assert "300114.SZ" in history.attrs["all_stock_symbols"]
    assert mins._traded_symbols_from_provider(
        pro,
        trade_date="20220715",
        policy=policy,
        stock_history=history,
    ) == {"300114.SZ"}
    assert mins._traded_symbols_from_provider(
        pro,
        trade_date="20250217",
        policy=policy,
        stock_history=history,
    ) == {"302132.SZ"}
    assert "300114.SZ" in mins._historical_symbols(history, trade_date="20250214")
    assert "302132.SZ" not in mins._historical_symbols(history, trade_date="20250214")
    assert "300114.SZ" not in mins._historical_symbols(history, trade_date="20250217")
    assert "302132.SZ" in mins._historical_symbols(history, trade_date="20250217")


def test_historical_code_transition_mirror_is_promotable_full_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trade_date = "20220715"
    minute_calls: list[str] = []

    class TransitionPro:
        def stock_basic(self, *, exchange: str, list_status: str, fields: str) -> pd.DataFrame:
            columns = STOCK_HISTORY_COLUMNS
            if list_status != "L":
                return pd.DataFrame(columns=pd.Index(columns))
            return pd.DataFrame(
                [("302132.SZ", "L", "20100827", "", "CNY")],
                columns=pd.Index(columns),
            )

        def _daily_frame(self, offset: int) -> pd.DataFrame:
            symbols = ["300114.SZ", "302132.SZ"] if offset == 0 else []
            return pd.DataFrame({"ts_code": symbols, "trade_date": [trade_date] * len(symbols)})

        def daily(self, *, trade_date: str, fields: str, limit: int, offset: int) -> pd.DataFrame:
            return self._daily_frame(offset)

        daily_basic = daily

    def pro_bar(**kwargs: Any) -> pd.DataFrame:
        symbols = kwargs["ts_code"].split(",")
        minute_calls.extend(symbols)
        frame = _bars(symbols)
        frame["trade_time"] = frame["trade_time"].map(
            lambda value: value.replace(year=2022, month=7, day=15)
        )
        frame["trade_date"] = trade_date
        return frame

    _install_fake_api(
        monkeypatch,
        pro_bar,
        pro=TransitionPro(),
        mock_universe=False,
    )
    options = replace(
        _options(tmp_path, [], batch_size=20),
        start_date=trade_date,
        end_date=trade_date,
        trading_dates=[trade_date],
    )

    mins.mirror_minute_bars(options)

    part_dir = tmp_path / f"trade_date={trade_date}"
    sidecar = json.loads((part_dir / mins.COMPLETENESS_FILENAME).read_text())
    partition = pd.read_parquet(part_dir / "part-00000.parquet")
    receipt = mins.validate_complete_minute_partition(part_dir, trade_date=trade_date)
    assert minute_calls == ["300114.SZ"]
    assert set(partition["ts_code"]) == {"300114.SZ"}
    assert sidecar["expected_symbols"] == ["300114.SZ"]
    assert sidecar["universe_rule"] == mins.HISTORICAL_CODE_TRANSITION_UNIVERSE_RULE
    assert receipt["universe_rule"] == mins.HISTORICAL_CODE_TRANSITION_UNIVERSE_RULE


def test_daily_crosscheck_ignores_non_master_b_shares_and_allows_daily_only_bj() -> None:
    class DualUniversePro:
        def __init__(self, daily: list[str], daily_basic: list[str]) -> None:
            self.daily_symbols = daily
            self.daily_basic_symbols = daily_basic

        def _frame(self, symbols: list[str], trade_date: str, offset: int) -> pd.DataFrame:
            page = symbols if offset == 0 else []
            return pd.DataFrame({"ts_code": page, "trade_date": [trade_date] * len(page)})

        def daily(self, *, trade_date: str, fields: str, limit: int, offset: int) -> pd.DataFrame:
            return self._frame(self.daily_symbols, trade_date, offset)

        def daily_basic(
            self, *, trade_date: str, fields: str, limit: int, offset: int
        ) -> pd.DataFrame:
            return self._frame(self.daily_basic_symbols, trade_date, offset)

    history = pd.DataFrame({"ts_code": ["000001.SZ", "920001.BJ"]})
    history.attrs["all_stock_symbols"] = set(history["ts_code"])
    policy = TushareRequestPolicy(attempts=1)

    symbols = mins._traded_symbols_from_provider(
        DualUniversePro(
            ["000001.SZ", "920001.BJ"],
            ["000001.SZ", "201872.SZ"],
        ),
        trade_date=TRADE_DATE,
        policy=policy,
        stock_history=history,
    )

    assert symbols == {"000001.SZ", "920001.BJ"}


def test_daily_crosscheck_rejects_silently_missing_sh_sz_stock() -> None:
    class ShortDailyPro:
        def daily(self, *, trade_date: str, fields: str, limit: int, offset: int) -> pd.DataFrame:
            symbols = ["000001.SZ"] if offset == 0 else []
            return pd.DataFrame({"ts_code": symbols, "trade_date": [trade_date] * len(symbols)})

        def daily_basic(
            self, *, trade_date: str, fields: str, limit: int, offset: int
        ) -> pd.DataFrame:
            symbols = ["000001.SZ", "000002.SZ"] if offset == 0 else []
            return pd.DataFrame({"ts_code": symbols, "trade_date": [trade_date] * len(symbols)})

    history = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]})
    history.attrs["all_stock_symbols"] = set(history["ts_code"])

    with pytest.raises(RuntimeError, match="missing_from_daily=.*000002.SZ"):
        mins._traded_symbols_from_provider(
            ShortDailyPro(),
            trade_date=TRADE_DATE,
            policy=TushareRequestPolicy(attempts=1),
            stock_history=history,
        )


def test_exchange_crosscheck_ignores_unknown_symbols_outside_selected_exchange() -> None:
    class MixedExchangePro:
        def _frame(self, symbols: list[str], trade_date: str, offset: int) -> pd.DataFrame:
            page = symbols if offset == 0 else []
            return pd.DataFrame({"ts_code": page, "trade_date": [trade_date] * len(page)})

        def daily(self, *, trade_date: str, fields: str, limit: int, offset: int) -> pd.DataFrame:
            return self._frame(["300114.SZ", "920001.BJ"], trade_date, offset)

        def daily_basic(
            self, *, trade_date: str, fields: str, limit: int, offset: int
        ) -> pd.DataFrame:
            return self._frame(["300114.SZ"], trade_date, offset)

    history = pd.DataFrame({"ts_code": ["920001.BJ"]})
    history.attrs["all_stock_symbols"] = {"920001.BJ"}

    symbols = mins._traded_symbols_from_provider(
        MixedExchangePro(),
        trade_date=TRADE_DATE,
        policy=TushareRequestPolicy(attempts=1),
        stock_history=history,
        exchange="BJ",
    )

    assert symbols == {"920001.BJ"}


def test_exchange_crosscheck_still_rejects_unknown_symbol_in_selected_exchange() -> None:
    class UnknownBeijingPro:
        def daily(self, *, trade_date: str, fields: str, limit: int, offset: int) -> pd.DataFrame:
            symbols = ["920999.BJ"] if offset == 0 else []
            return pd.DataFrame({"ts_code": symbols, "trade_date": [trade_date] * len(symbols)})

        def daily_basic(
            self, *, trade_date: str, fields: str, limit: int, offset: int
        ) -> pd.DataFrame:
            symbols = ["000001.SZ"] if offset == 0 else []
            return pd.DataFrame({"ts_code": symbols, "trade_date": [trade_date] * len(symbols)})

    history = pd.DataFrame({"ts_code": ["920001.BJ"]})
    history.attrs["all_stock_symbols"] = {"920001.BJ"}

    with pytest.raises(RuntimeError, match="absent from the CNY stock_basic master"):
        mins._traded_symbols_from_provider(
            UnknownBeijingPro(),
            trade_date=TRADE_DATE,
            policy=TushareRequestPolicy(attempts=1),
            stock_history=history,
            exchange="BJ",
        )


def test_dynamic_universe_excludes_prelisting_bj_daily_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    minute_calls: list[str] = []

    def pro_bar(**kwargs: Any) -> pd.DataFrame:
        minute_calls.extend(kwargs["ts_code"].split(","))
        return _bars(kwargs["ts_code"].split(","))

    _install_fake_api(monkeypatch, pro_bar, mock_universe=False)
    history = pd.DataFrame(
        {
            "ts_code": ["920001.BJ", "920999.BJ"],
            "list_status": ["L", "L"],
            "list_date": ["20211115", "20270101"],
            "delist_date": ["", ""],
            "curr_type": ["CNY", "CNY"],
        }
    )
    history.attrs["status_counts"] = {"L": 2, "D": 0, "P": 0, "G": 0}
    history.attrs["all_stock_symbols"] = set(history["ts_code"])
    monkeypatch.setattr(mins, "_stock_history_from_provider", lambda _pro, _policy: history)
    monkeypatch.setattr(
        mins,
        "_traded_symbols_from_provider",
        lambda _pro, *, trade_date, policy, stock_history, exchange=None: set(history["ts_code"]),
    )
    options = replace(
        _options(tmp_path, []),
        exchange="BJ",
        trading_dates=[TRADE_DATE],
    )

    result = mins.mirror_minute_bars(options)

    assert result["requests_made"] == 1
    assert minute_calls == ["920001.BJ"]
    sidecar = _sidecar(tmp_path)
    assert sidecar["expected_symbols"] == ["920001.BJ"]
    assert "+exclude_prelisting_BJ(1)+exchange_filter(BJ)" in sidecar["universe_source"]


def test_dynamic_universe_still_rejects_prelisting_sh_sz_daily_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_pro_bar(**_kwargs: Any) -> pd.DataFrame:
        raise AssertionError("invalid SH/SZ listing interval must fail before minute requests")

    _install_fake_api(monkeypatch, unexpected_pro_bar, mock_universe=False)
    history = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "list_status": ["L", "L"],
            "list_date": ["20100101", "20270101"],
            "delist_date": ["", ""],
            "curr_type": ["CNY", "CNY"],
        }
    )
    history.attrs["status_counts"] = {"L": 2, "D": 0, "P": 0, "G": 0}
    history.attrs["all_stock_symbols"] = set(history["ts_code"])
    monkeypatch.setattr(mins, "_stock_history_from_provider", lambda _pro, _policy: history)
    monkeypatch.setattr(
        mins,
        "_traded_symbols_from_provider",
        lambda _pro, *, trade_date, policy, stock_history, exchange=None: set(history["ts_code"]),
    )

    with pytest.raises(RuntimeError, match="outside stock_basic active intervals.*000002.SZ"):
        mins.mirror_minute_bars(replace(_options(tmp_path, []), trading_dates=[TRADE_DATE]))


def test_partial_sidecar_from_force_refresh_controls_resume_pending_symbols(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def seed_pro_bar(**kwargs: Any) -> pd.DataFrame:
        return _bars(kwargs["ts_code"].split(","))

    _pro, tushare_module = _install_fake_api(monkeypatch, seed_pro_bar)
    options = _options(tmp_path, ["000001.SZ", "000002.SZ"], batch_size=1)
    mins.mirror_minute_bars(options)

    force_calls: list[str] = []

    def force_pro_bar(**kwargs: Any) -> pd.DataFrame:
        symbol = kwargs["ts_code"]
        force_calls.append(symbol)
        if symbol == "000002.SZ":
            raise TimeoutError("simulated timeout")
        refreshed = _bars([symbol])
        refreshed["close"] = 10.15
        return refreshed

    tushare_module.pro_bar = force_pro_bar  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="recoverable partial partition"):
        mins.mirror_minute_bars(replace(options, skip_existing=False))

    partial = _sidecar(tmp_path)
    assert force_calls == ["000001.SZ", "000002.SZ"]
    assert partial["status"] == "partial"
    assert partial["completed_symbols"] == ["000001.SZ"]
    assert partial["partition"]["complete_symbols"] == ["000001.SZ", "000002.SZ"]

    resume_calls: list[str] = []

    def resume_pro_bar(**kwargs: Any) -> pd.DataFrame:
        resume_calls.append(kwargs["ts_code"])
        return _bars(kwargs["ts_code"].split(","))

    tushare_module.pro_bar = resume_pro_bar  # type: ignore[attr-defined]
    mins.mirror_minute_bars(options)

    assert resume_calls == ["000002.SZ"]
    assert _sidecar(tmp_path)["status"] == "complete"


def test_keyboard_interrupt_checkpoints_successful_batches_for_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pro, tushare_module = _install_fake_api(monkeypatch, lambda **_kwargs: pd.DataFrame())
    options = _options(tmp_path, ["000001.SZ", "000002.SZ"], batch_size=1)
    calls: list[str] = []

    def interrupted_pro_bar(**kwargs: Any) -> pd.DataFrame:
        symbol = kwargs["ts_code"]
        calls.append(symbol)
        if symbol == "000002.SZ":
            raise KeyboardInterrupt
        return _bars([symbol])

    tushare_module.pro_bar = interrupted_pro_bar  # type: ignore[attr-defined]
    with pytest.raises(KeyboardInterrupt):
        mins.mirror_minute_bars(options)

    assert calls == ["000001.SZ", "000002.SZ"]
    assert _sidecar(tmp_path)["completed_symbols"] == ["000001.SZ"]

    resume_calls: list[str] = []

    def resume_pro_bar(**kwargs: Any) -> pd.DataFrame:
        resume_calls.append(kwargs["ts_code"])
        return _bars(kwargs["ts_code"].split(","))

    tushare_module.pro_bar = resume_pro_bar  # type: ignore[attr-defined]
    mins.mirror_minute_bars(options)

    assert resume_calls == ["000002.SZ"]


def test_minute_cli_defaults_to_resume_parses_commas_and_all_skipped_is_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parser = build_parser()
    base = [
        "tushare",
        "mirror-a-share-mins",
        "--start-date",
        TRADE_DATE,
        "--end-date",
        TRADE_DATE,
        "--out-dir",
        str(tmp_path),
        "--minute-quota-mode",
        "observe",
        "--minute-quota-db",
        str(tmp_path / "quota.sqlite3"),
        "--minute-quota-consumer",
        "watch20",
        "--minute-quota-gate",
        "requests",
        "--minute-quota-limit-requests",
        "10000",
        "--minute-quota-burst-limit-requests",
        "20000",
        "--minute-quota-safety-requests",
        "500",
        "--minute-quota-allow-burst",
    ]
    parsed = parser.parse_args([*base, "--symbols", "000001.SZ,000002.SZ", "000003.SZ"])
    assert parsed.skip_existing is True
    assert parser.parse_args([*base, "--force"]).skip_existing is False
    assert parser.parse_args([*base, "--no-skip-existing"]).skip_existing is False

    captured: list[mins.MinsMirrorOptions] = []

    def fake_mirror(options: mins.MinsMirrorOptions) -> dict[str, Any]:
        captured.append(options)
        return {
            "dates_fetched": 0,
            "dates_skipped": 1,
            "requests_made": 0,
            "total_bars": 0,
            "output_dir": str(tmp_path),
        }

    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    monkeypatch.setattr(mins, "mirror_minute_bars", fake_mirror)

    assert _handle_mirror_mins(parsed) == 0
    assert captured[0].skip_existing is True
    assert captured[0].symbols == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert captured[0].minute_quota_mode == "observe"
    assert captured[0].minute_quota_db == str(tmp_path / "quota.sqlite3")
    assert captured[0].minute_quota_consumer == "watch20"
    assert captured[0].minute_quota_gate == "requests"
    assert captured[0].minute_quota_limit_requests == 10_000
    assert captured[0].minute_quota_burst_limit_requests == 20_000
    assert captured[0].minute_quota_safety_requests == 500
    assert captured[0].minute_quota_allow_burst is True

    monkeypatch.setenv("TUSHARE_TOKEN_2", "token-two")
    monkeypatch.setenv("TUSHARE_API_URL", "https://base.invalid")
    monkeypatch.setenv("TUSHARE_API_URL_2", "https://token-two.invalid")
    token2 = parser.parse_args([*base, "--token-env", "TUSHARE_TOKEN_2"])
    assert _handle_mirror_mins(token2) == 0
    assert captured[1].token_env == "TUSHARE_TOKEN_2"
    assert captured[1].api_url is None
    assert os.environ["TUSHARE_API_URL"] == "https://base.invalid"


def test_minute_quota_status_cli_prints_token_safe_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "private-status-token")
    quota_db = tmp_path / "quota.sqlite3"
    ledger = MinuteQuotaLedger.from_env(
        mode="observe",
        database_path=quota_db,
        consumer="campaign",
    )
    assert ledger is not None
    reservation = ledger.reserve(241)
    reservation.commit(240)

    parsed = build_parser().parse_args(
        [
            "tushare",
            "minute-quota-status",
            "--token-env",
            "TUSHARE_TOKEN",
            "--minute-quota-mode",
            "observe",
            "--minute-quota-db",
            str(quota_db),
            "--json",
        ]
    )

    assert _handle_minute_quota_status(parsed) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["committed_rows"] == 240
    assert payload["committed_requests"] == 1
    assert payload["charged_requests"] == 1
    assert payload["effective_limit_requests"] == 10_000
    assert len(payload["token_fingerprint"]) == 16
    assert "private-status-token" not in json.dumps(payload)


def test_minute_cli_loads_repository_env_before_token_precheck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from market_data_platform.providers import tushare_a_share

    parser = build_parser()
    parsed = parser.parse_args(
        [
            "tushare",
            "mirror-a-share-mins",
            "--start-date",
            TRADE_DATE,
            "--end-date",
            TRADE_DATE,
            "--out-dir",
            str(tmp_path),
            "--token-env",
            "TUSHARE_TOKEN_2",
            "--exchange",
            "BJ",
        ]
    )
    monkeypatch.delenv("TUSHARE_TOKEN_2", raising=False)

    def load_env() -> tuple[str, ...]:
        monkeypatch.setenv("TUSHARE_TOKEN_2", "private-test-token")
        return (".env.local",)

    captured: list[mins.MinsMirrorOptions] = []
    monkeypatch.setattr(tushare_a_share, "_load_tushare_env_files", load_env)
    monkeypatch.setattr(
        mins,
        "mirror_minute_bars",
        lambda options: (
            captured.append(options)
            or {
                "dates_fetched": 0,
                "dates_skipped": 1,
                "requests_made": 0,
                "total_bars": 0,
                "output_dir": str(tmp_path),
            }
        ),
    )

    assert _handle_mirror_mins(parsed) == 0
    assert captured[0].token_env == "TUSHARE_TOKEN_2"
    assert captured[0].exchange == "BJ"
    assert "private-test-token" not in capsys.readouterr().out


def test_trade_calendar_excludes_closed_natural_dates() -> None:
    class CalendarPro:
        def trade_cal(
            self,
            *,
            exchange: str,
            start_date: str,
            end_date: str,
            fields: str,
        ) -> pd.DataFrame:
            assert exchange == "SSE"
            assert start_date == "20260703"
            assert end_date == "20260706"
            assert fields == "cal_date,is_open"
            return pd.DataFrame(
                {
                    "cal_date": ["20260703", "20260704", "20260705", "20260706"],
                    "is_open": [1, 0, 0, 1],
                }
            )

    dates = mins._trading_dates_from_provider(
        CalendarPro(),
        start_date="20260703",
        end_date="20260706",
        policy=TushareRequestPolicy(attempts=1),
    )

    assert dates == ["20260703", "20260706"]


def test_trade_calendar_rejects_silently_truncated_coverage() -> None:
    class TruncatedCalendarPro:
        def trade_cal(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "cal_date": ["20260703", "20260704", "20260705"],
                    "is_open": [1, 0, 0],
                }
            )

    with pytest.raises(RuntimeError, match="coverage mismatch.*20260706"):
        mins._trading_dates_from_provider(
            TruncatedCalendarPro(),
            start_date="20260703",
            end_date="20260706",
            policy=TushareRequestPolicy(attempts=1),
        )


def test_generic_sdk_error_has_bounded_outer_retry_with_one_slot_per_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    def flaky_pro_bar(**kwargs: Any) -> pd.DataFrame:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("ERROR.")
        return _bars(kwargs["ts_code"].split(","))

    _install_fake_api(monkeypatch, flaky_pro_bar)
    options = replace(
        _options(tmp_path / "data", ["000001.SZ"]),
        minute_quota_mode="observe",
        minute_quota_db=tmp_path / "quota.sqlite3",
        minute_quota_consumer="campaign",
        request_policy=TushareRequestPolicy(
            attempts=2,
            retry_sleep_seconds=0,
            retry_max_sleep_seconds=0,
            quota_cooldown_seconds=0,
        ),
    )

    mins.mirror_minute_bars(options)

    assert attempts == 2
    ledger = MinuteQuotaLedger.from_env(
        mode="observe",
        database_path=tmp_path / "quota.sqlite3",
        consumer="status",
    )
    assert ledger is not None
    status = ledger.status()
    assert status["attempts"] == 2
    assert status["uncertain_rows"] == 241
    assert status["uncertain_requests"] == 1
    assert status["committed_requests"] == 1


def test_transient_official_rate_limit_retries_without_closing_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    def rate_limited(**kwargs: Any) -> pd.DataFrame:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("官方限速，在代码中增加等待几秒重试即可；每日可获取8000万条")
        return _bars(kwargs["ts_code"].split(","))

    pro, _module = _install_fake_api(monkeypatch, rate_limited)
    ledger = MinuteQuotaLedger(
        MinuteQuotaConfig(
            mode="observe",
            database_path=tmp_path / "quota.sqlite3",
            request_policy=MinuteQuotaRequestPolicy(gate="requests"),
        ),
        token="test-token",
        fingerprint_key=b"test-provider-quota-key-at-least-32-bytes",
    )

    raw = mins._fetch_minute_batch(
        pro,
        ["000001.SZ"],
        TRADE_DATE,
        "1min",
        TushareRequestPolicy(
            attempts=2,
            retry_sleep_seconds=0,
            retry_max_sleep_seconds=0,
            quota_cooldown_seconds=0,
        ),
        ledger,
    )

    assert len(raw) == 241
    status = ledger.status()
    assert attempts == 2
    assert status["uncertain_requests"] == 1
    assert status["committed_requests"] == 1
    assert status["closed_reason"] is None


def test_explicit_daily_request_exhaustion_closes_pool_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    def exhausted(**_kwargs: Any) -> pd.DataFrame:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("今日请求次数已达上限，请明日再试")

    pro, _module = _install_fake_api(monkeypatch, exhausted)
    ledger = MinuteQuotaLedger(
        MinuteQuotaConfig(
            mode="enforce",
            database_path=tmp_path / "quota.sqlite3",
            request_policy=MinuteQuotaRequestPolicy(gate="requests"),
        ),
        token="test-token",
        fingerprint_key=b"test-provider-quota-key-at-least-32-bytes",
    )

    with pytest.raises(MinuteQuotaPoolClosed, match="provider_daily_request_capacity_exhausted"):
        mins._fetch_minute_batch(
            pro,
            ["000001.SZ"],
            TRADE_DATE,
            "1min",
            TushareRequestPolicy(
                attempts=3,
                retry_sleep_seconds=0,
                retry_max_sleep_seconds=0,
                quota_cooldown_seconds=0,
            ),
            ledger,
        )

    status = ledger.status()
    assert attempts == 1
    assert status["uncertain_requests"] == 1
    assert status["closed_reason"] == "provider_daily_request_capacity_exhausted"


def test_daily_request_exhaustion_closes_the_reservation_day_across_midnight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_data_platform import _tushare_minute_quota_holds as quota_holds

    before_midnight = datetime(2026, 7, 17, 15, 59, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 7, 17, 16, 0, 1, tzinfo=UTC)

    class RequestClock:
        @classmethod
        def now(cls, timezone: object) -> datetime:
            assert timezone is UTC
            return before_midnight

    monkeypatch.setattr(mins, "datetime", RequestClock)
    monkeypatch.setattr(
        quota_holds,
        "utc_now",
        lambda now=None: after_midnight if now is None else now,
    )

    def exhausted(**_kwargs: Any) -> pd.DataFrame:
        raise RuntimeError("今日请求次数已达上限，请明日再试")

    pro, _module = _install_fake_api(monkeypatch, exhausted)
    ledger = MinuteQuotaLedger(
        MinuteQuotaConfig(
            mode="enforce",
            database_path=tmp_path / "quota.sqlite3",
            request_policy=MinuteQuotaRequestPolicy(gate="requests"),
        ),
        token="test-token",
        fingerprint_key=b"test-provider-quota-key-at-least-32-bytes",
    )

    with pytest.raises(MinuteQuotaPoolClosed) as error:
        mins._fetch_minute_batch(
            pro,
            ["000001.SZ"],
            TRADE_DATE,
            "1min",
            TushareRequestPolicy(attempts=1, quota_cooldown_seconds=0),
            ledger,
        )

    assert error.value.quota_date == "20260717"
    assert (
        ledger.status(quota_date="20260717", now=after_midnight)["closed_reason"]
        == "provider_daily_request_capacity_exhausted"
    )
    assert ledger.status(quota_date="20260718", now=after_midnight)["closed_reason"] is None


def test_shared_quota_accounts_every_physical_outer_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    def flaky_pro_bar(**kwargs: Any) -> pd.DataFrame:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("provider timed out")
        return _bars(kwargs["ts_code"].split(","))

    pro, _module = _install_fake_api(monkeypatch, flaky_pro_bar)
    ledger = MinuteQuotaLedger(
        MinuteQuotaConfig(
            mode="observe",
            database_path=tmp_path / "quota.sqlite3",
            limit_rows=10_000,
            safety_rows=0,
        ),
        token="test-token",
        fingerprint_key=b"test-provider-quota-key-at-least-32-bytes",
    )

    raw = mins._fetch_minute_batch(
        pro,
        ["000001.SZ"],
        TRADE_DATE,
        "1min",
        TushareRequestPolicy(
            attempts=2,
            retry_sleep_seconds=0,
            retry_max_sleep_seconds=0,
        ),
        ledger,
    )

    assert len(raw) == 241
    assert attempts == 2
    status = ledger.status()
    assert status["attempts"] == 2
    assert status["uncertain_rows"] == 241
    assert status["committed_rows"] == 241


def test_shared_quota_rejection_is_not_retried_or_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical_calls = 0

    def forbidden_pro_bar(**_kwargs: Any) -> pd.DataFrame:
        nonlocal physical_calls
        physical_calls += 1
        raise AssertionError("quota gate must run before the provider call")

    pro, _module = _install_fake_api(monkeypatch, forbidden_pro_bar)
    ledger = MinuteQuotaLedger(
        MinuteQuotaConfig(
            mode="enforce",
            database_path=tmp_path / "quota.sqlite3",
            limit_rows=240,
            safety_rows=0,
        ),
        token="test-token",
        fingerprint_key=b"test-provider-quota-key-at-least-32-bytes",
    )

    with pytest.raises(MinuteQuotaExceeded):
        mins._fetch_minute_batch(
            pro,
            ["000001.SZ"],
            TRADE_DATE,
            "1min",
            TushareRequestPolicy(
                attempts=3,
                retry_sleep_seconds=0,
                retry_max_sleep_seconds=0,
                quota_cooldown_seconds=0,
            ),
            ledger,
        )

    assert physical_calls == 0
    assert ledger.status()["rejected_attempts"] == 1


def test_mirror_preserves_shared_quota_error_type_in_partial_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical_calls = 0

    def forbidden_pro_bar(**_kwargs: Any) -> pd.DataFrame:
        nonlocal physical_calls
        physical_calls += 1
        return pd.DataFrame()

    _install_fake_api(monkeypatch, forbidden_pro_bar)
    quota_db = tmp_path / "quota.sqlite3"
    options = replace(
        _options(tmp_path / "data", ["000001.SZ"]),
        minute_quota_mode="enforce",
        minute_quota_db=quota_db,
        minute_quota_consumer="campaign",
        minute_quota_limit_rows=240,
        minute_quota_safety_rows=0,
        request_policy=TushareRequestPolicy(
            attempts=3,
            retry_sleep_seconds=0,
            retry_max_sleep_seconds=0,
            quota_cooldown_seconds=0,
        ),
    )

    with pytest.raises(MinuteQuotaExceeded):
        mins.mirror_minute_bars(options)

    sidecar = json.loads(
        (tmp_path / "data" / f"trade_date={TRADE_DATE}" / mins.COMPLETENESS_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert physical_calls == 0
    assert sidecar["status"] == "partial"
    assert sidecar["error"]["type"] == "MinuteQuotaExceeded"


def test_mirror_commits_returned_rows_before_content_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def incomplete_pro_bar(**kwargs: Any) -> pd.DataFrame:
        return _bars(kwargs["ts_code"].split(",")).iloc[:-1].copy()

    _install_fake_api(monkeypatch, incomplete_pro_bar)
    quota_db = tmp_path / "quota" / "minute.sqlite3"
    options = replace(
        _options(tmp_path / "data", ["000001.SZ"]),
        minute_quota_mode="observe",
        minute_quota_db=quota_db,
        minute_quota_consumer="campaign",
    )

    with pytest.raises(RuntimeError, match="incomplete batch"):
        mins.mirror_minute_bars(options)

    ledger = MinuteQuotaLedger.from_env(
        mode="observe",
        database_path=quota_db,
        consumer="status",
    )
    assert ledger is not None
    status = ledger.status()
    assert status["committed_rows"] == 240
    assert status["uncertain_rows"] == 0
    assert status["consumers"][0]["consumer"] == "campaign"


def test_token2_route_is_passed_without_overriding_its_api_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_calls: list[dict[str, Any]] = []

    def pro_bar(**kwargs: Any) -> pd.DataFrame:
        return _bars(kwargs["ts_code"].split(","))

    _install_fake_api(monkeypatch, pro_bar, client_calls=client_calls)
    monkeypatch.setenv("TUSHARE_TOKEN_2", "token-two")
    monkeypatch.setenv("TUSHARE_API_URL", "https://base.invalid")
    monkeypatch.setenv("TUSHARE_API_URL_2", "https://token-two.invalid")
    options = replace(
        _options(tmp_path, ["000001.SZ"]),
        token_env="TUSHARE_TOKEN_2",
        api_url=None,
    )

    mins.mirror_minute_bars(options)

    assert client_calls[0]["token_env"] == "TUSHARE_TOKEN_2"
    assert client_calls[0]["api_url"] is None


def test_explicit_etf_symbol_is_rejected_by_stock_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_pro_bar(**_kwargs: Any) -> pd.DataFrame:
        raise AssertionError("ETF must be rejected before minute requests")

    _install_fake_api(monkeypatch, unexpected_pro_bar)

    with pytest.raises(ValueError, match="not CNY A-share stocks.*510300.SH"):
        mins.mirror_minute_bars(_options(tmp_path, ["510300.SH"]))


def test_corrupt_bound_parquet_is_quarantined_and_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def pro_bar(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs["ts_code"])
        return _bars(kwargs["ts_code"].split(","))

    _install_fake_api(monkeypatch, pro_bar)
    options = _options(tmp_path, ["000001.SZ"])
    mins.mirror_minute_bars(options)
    part_path = tmp_path / f"trade_date={TRADE_DATE}" / "part-00000.parquet"
    part_path.write_bytes(b"broken parquet")
    calls.clear()

    result = mins.mirror_minute_bars(options)

    assert result["dates_fetched"] == 1
    assert calls == ["000001.SZ"]
    assert _sidecar(tmp_path)["status"] == "complete"
    assert list((part_path.parent / "_quarantine").glob("*.parquet"))
