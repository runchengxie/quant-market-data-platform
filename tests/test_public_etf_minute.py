from __future__ import annotations

import hashlib
import json
import socket
import sys
import tomllib
import types
from pathlib import Path
from typing import cast

import pandas as pd
import pytest
import yaml

import market_data_platform.providers.public_etf_minute as provider


def _fixture_frame(*trade_times: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["512880.SH"] * len(trade_times),
            "trade_time": [pd.Timestamp(value) for value in trade_times],
            "open": [1.0] * len(trade_times),
            "close": [1.01] * len(trade_times),
            "high": [1.02] * len(trade_times),
            "low": [0.99] * len(trade_times),
            "vol": [100.0] * len(trade_times),
            "amount": [10000.0] * len(trade_times),
        }
    )


def test_public_etf_minute_contract_is_explicit() -> None:
    from market_data_platform.providers.public_etf_minute import (
        ETF_MINUTE_COLUMNS,
        ETF_MINUTE_NETWORK_MODES,
        ETF_MINUTE_PERIODS,
        ETF_MINUTE_SOURCES,
    )

    assert ETF_MINUTE_COLUMNS == (
        "ts_code",
        "trade_time",
        "open",
        "close",
        "high",
        "low",
        "vol",
        "amount",
    )
    assert ETF_MINUTE_NETWORK_MODES == ("system", "direct")
    assert ETF_MINUTE_PERIODS == ("1", "5", "15", "30", "60")
    assert ETF_MINUTE_SOURCES == ("auto", "eastmoney", "sina")


def test_public_etf_minute_dependencies_are_optional() -> None:
    payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    extra = payload["project"]["optional-dependencies"]["etf-minute-public"]

    assert extra == [
        "akshare>=1.18.94,<2",
        "pandas>=2.2",
        "pyarrow>=25.0.0",
    ]
    assert "akshare" not in payload["project"]["dependencies"]


def test_normalize_etf_minute_frame_uses_stable_schema() -> None:
    raw = pd.DataFrame(
        {
            "时间": ["20260824 09:30:00", "20260824 09:30:00"],
            "开盘": [1.0, 1.0],
            "收盘": [1.01, 1.01],
            "最高": [1.02, 1.02],
            "最低": [0.99, 0.99],
            "成交量": [100, 100],
            "成交额": [10000, 10000],
        }
    )

    result = provider.normalize_etf_minute_frame(raw, "512880.SH")

    assert list(result.columns) == list(provider.ETF_MINUTE_COLUMNS)
    assert len(result) == 1
    assert result.loc[0, "ts_code"] == "512880.SH"
    assert result.loc[0, "trade_time"] == pd.Timestamp("2026-08-24 09:30:00")


def test_normalize_sina_frame_keeps_amount_nullable() -> None:
    raw = pd.DataFrame(
        {
            "day": ["2026-08-24 09:45:00"],
            "open": [1.0],
            "close": [1.01],
            "high": [1.02],
            "low": [0.99],
            "volume": [100],
        }
    )

    result = provider.normalize_etf_minute_frame(raw, "512880.SH")

    assert len(result) == 1
    assert result["amount"].isna().all()


def test_normalize_ts_code_infers_exchange_and_rejects_bad_values() -> None:
    assert provider.normalize_ts_code("512880") == "512880.SH"
    assert provider.normalize_ts_code("159915.sz") == "159915.SZ"

    with pytest.raises(ValueError, match="后缀"):
        provider.normalize_ts_code("512880.BJ")
    with pytest.raises(ValueError, match="6 位"):
        provider.normalize_ts_code("51288")


def test_fetch_rejects_invalid_period_source_and_date_range() -> None:
    with pytest.raises(ValueError, match="period"):
        provider.fetch_etf_minute_range(
            "512880.SH", "20260824", "20260824", period="2", source="auto"
        )
    with pytest.raises(ValueError, match="source"):
        provider.fetch_etf_minute_range(
            "512880.SH", "20260824", "20260824", period="1", source="other"
        )
    with pytest.raises(ValueError, match="1 分钟"):
        provider.fetch_etf_minute_range(
            "512880.SH", "20260824", "20260824", period="1", source="sina"
        )
    with pytest.raises(ValueError, match="晚于"):
        provider.fetch_etf_minute_range(
            "512880.SH", "20260825", "20260824", period="1", source="auto"
        )


def test_direct_network_mode_rejects_synthetic_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.2.26", port))]

    monkeypatch.setattr(provider.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(RuntimeError, match="synthetic"):
        provider.fetch_etf_minute_range(
            "512880.SH",
            "20260824",
            "20260824",
            period="5",
            source="sina",
            network_mode="direct",
        )


def test_direct_curl_strips_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(provider.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(provider.subprocess, "run", fake_run)

    assert provider._curl_json("https://example.test", {}, network_mode="direct") == {}

    command = cast(list[str], captured["command"])
    child_env = cast(dict[str, str], captured["env"])
    assert command[command.index("--noproxy") + 1] == "*"
    assert all(
        key not in child_env
        for key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        )
    )
    assert child_env["NO_PROXY"] == "*"


def test_direct_fetch_uses_curl_instead_of_akshare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, str] = {}

    def fake_curl(ts_code, start_date, end_date, *, period, network_mode):
        calls["network_mode"] = network_mode
        return pd.DataFrame(
            {
                "时间": ["2026-08-24 09:45:00"],
                "开盘": [1.0],
                "收盘": [1.01],
                "最高": [1.02],
                "最低": [0.99],
                "成交量": [100],
                "成交额": [10000],
            }
        )

    monkeypatch.setattr(provider, "_direct_network_preflight", lambda: None)
    monkeypatch.setattr(provider, "_fetch_eastmoney_with_curl", fake_curl)
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(
            fund_etf_hist_min_em=lambda **kwargs: pytest.fail("direct mode used AKShare")
        ),
    )

    result, selected_source = provider.fetch_etf_minute_range(
        "512880.SH",
        "20260824",
        "20260824",
        period="1",
        source="eastmoney",
        network_mode="direct",
    )

    assert selected_source == "eastmoney-curl"
    assert calls == {"network_mode": "direct"}
    assert len(result) == 1


def test_fetch_uses_akshare_source_and_filters_requested_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = pd.DataFrame(
        {
            "时间": ["2026-08-23 09:30:00", "2026-08-24 09:30:00"],
            "开盘": [1.0, 1.1],
            "收盘": [1.0, 1.1],
            "最高": [1.0, 1.1],
            "最低": [1.0, 1.1],
            "成交量": [100, 110],
            "成交额": [10000, 11000],
        }
    )
    calls: list[dict[str, str]] = []

    def fund_etf_hist_min_em(**kwargs: str) -> pd.DataFrame:
        calls.append(kwargs)
        return raw.copy()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(fund_etf_hist_min_em=fund_etf_hist_min_em),
    )

    result, selected_source = provider.fetch_etf_minute_range(
        "512880.SH", "20260824", "20260824", period="1", source="auto"
    )

    assert selected_source == "eastmoney-akshare"
    assert result["trade_time"].dt.strftime("%Y%m%d").tolist() == ["20260824"]
    assert calls == [
        {
            "symbol": "512880",
            "period": "1",
            "start_date": "20260824 09:30:00",
            "end_date": "20260824 15:00:00",
        }
    ]


def test_fetch_uses_sina_source_with_nullable_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        provider,
        "_fetch_sina_with_curl",
        lambda *args, **kwargs: pd.DataFrame(
            {
                "day": ["2026-08-24 09:45:00"],
                "open": [1.0],
                "close": [1.01],
                "high": [1.02],
                "low": [0.99],
                "volume": [100],
            }
        ),
    )

    result, selected_source = provider.fetch_etf_minute_range(
        "512880.SH", "20260824", "20260824", period="15", source="sina"
    )

    assert selected_source == "sina-curl"
    assert result["amount"].isna().all()


def test_fetch_falls_back_to_sina_after_eastmoney_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def always_fail(**kwargs: str) -> pd.DataFrame:
        raise ConnectionError("eastmoney unavailable")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(fund_etf_hist_min_em=always_fail),
    )
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_with_curl",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("curl unavailable")),
    )
    monkeypatch.setattr(
        provider,
        "_fetch_sina_with_curl",
        lambda *args, **kwargs: pd.DataFrame(
            {
                "day": ["2026-08-24 09:45:00"],
                "open": [1.0],
                "close": [1.01],
                "high": [1.02],
                "low": [0.99],
                "volume": [100],
            }
        ),
    )

    result, selected_source = provider.fetch_etf_minute_range(
        "512880.SH", "20260824", "20260824", period="15", source="auto"
    )

    assert selected_source == "sina-curl"
    assert len(result) == 1


def test_mirror_public_etf_minute_writes_versioned_partition_and_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        provider,
        "fetch_etf_minute_range",
        lambda *args, **kwargs: (_fixture_frame("2026-08-24 09:30:00"), "eastmoney-akshare"),
    )

    result = provider.mirror_public_etf_minute(
        provider.EtfMinuteMirrorOptions(
            symbols=("512880.SH",),
            start_date="20260824",
            end_date="20260824",
            period="1",
            network_mode="direct",
            output_dir=tmp_path / "etf_minute_1m" / "v1",
        )
    )

    output = tmp_path / "etf_minute_1m" / "v1"
    part = output / "trade_date=20260824" / "part-00000.parquet"
    manifest = yaml.safe_load((output / "manifest.yml").read_text(encoding="utf-8"))
    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))

    assert result["status"] == "completed"
    assert part.is_file()
    assert manifest["totals"]["rows"] == 1
    assert manifest["sources"] == ["eastmoney-akshare"]
    assert manifest["query"]["network_mode"] == "direct"
    assert receipt["query"]["network_mode"] == "direct"
    assert receipt["files"][0]["sha256"] == hashlib.sha256(part.read_bytes()).hexdigest()


def test_mirror_public_etf_minute_skips_existing_partition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "etf_minute_1m" / "v1"
    part = output / "trade_date=20260824" / "part-00000.parquet"
    part.parent.mkdir(parents=True)
    _fixture_frame("2026-08-24 09:30:00").to_parquet(part, index=False)

    def unexpected_fetch(*args: object, **kwargs: object) -> tuple[pd.DataFrame, str]:
        raise AssertionError("existing partitions must be skipped")

    monkeypatch.setattr(provider, "fetch_etf_minute_range", unexpected_fetch)

    result = provider.mirror_public_etf_minute(
        provider.EtfMinuteMirrorOptions(
            symbols=("512880.SH",),
            start_date="20260824",
            end_date="20260824",
            output_dir=output,
        )
    )

    assert result["status"] == "completed"
    assert result["totals"]["dates_skipped"] == 1


def test_mirror_public_etf_minute_records_empty_dates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        provider,
        "fetch_etf_minute_range",
        lambda *args, **kwargs: (_fixture_frame("2026-08-24 09:30:00"), "eastmoney-akshare"),
    )

    result = provider.mirror_public_etf_minute(
        provider.EtfMinuteMirrorOptions(
            symbols=("512880.SH",),
            start_date="20260824",
            end_date="20260825",
            output_dir=tmp_path / "v1",
        )
    )

    assert result["status"] == "completed"
    assert result["totals"]["dates_written"] == 1
    assert result["totals"]["dates_empty"] == 1
    assert result["empty_dates"] == ["20260825"]


def test_mirror_public_etf_minute_records_failed_fetch_without_partition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_fetch(*args: object, **kwargs: object) -> tuple[pd.DataFrame, str]:
        raise ConnectionError("provider unavailable")

    monkeypatch.setattr(provider, "fetch_etf_minute_range", fail_fetch)
    output = tmp_path / "v1"

    result = provider.mirror_public_etf_minute(
        provider.EtfMinuteMirrorOptions(
            symbols=("512880.SH",),
            start_date="20260824",
            end_date="20260824",
            output_dir=output,
        )
    )

    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["failed_dates"] == ["20260824"]
    assert not (output / "trade_date=20260824" / "part-00000.parquet").exists()
    assert receipt["status"] == "failed"
