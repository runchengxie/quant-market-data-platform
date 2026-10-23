from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from test_tushare_a_share_mins import TRADE_DATE, _bars, _install_fake_api, _options

from quant_market_data_platform.providers import tushare_a_share_mins as mins


@pytest.fixture
def overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _install_fake_api(monkeypatch, lambda **kwargs: _bars(kwargs["ts_code"].split(",")))
    mins.mirror_minute_bars(replace(_options(tmp_path, []), exchange="SZ"))
    return tmp_path


def _call(
    root: Path, *, exchange: str = "SZ", date: str = TRADE_DATE
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "quant_market_data_platform.cli",
            "tushare",
            "validate-minute-partition",
            "--data-root",
            str(root),
            "--trade-date",
            date,
            "--exchange",
            exchange,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_cli_validates_real_partition_without_changing_inputs(overlay: Path) -> None:
    before = {p: p.read_bytes() for p in overlay.rglob("*") if p.is_file()}
    result = _call(overlay)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    expected = mins.validate_complete_minute_partition(
        overlay / f"trade_date={TRADE_DATE}", trade_date=TRADE_DATE, require_full_universe=False
    )
    expected.pop("expected_symbols")
    expected.pop("request_policy")
    expected.update(
        validation_schema_version="marketdata.minute_exchange_overlay.v1", exchange="SZ"
    )
    assert receipt == expected
    assert receipt["validation_schema_version"] == "marketdata.minute_exchange_overlay.v1"
    assert receipt["schema_version"] == "tushare.a_share.minute_partition.promotion.v1"
    assert receipt["trade_date"] == TRADE_DATE
    assert receipt["exchange"] == "SZ"
    assert receipt["rows"] == 9 * 241
    assert receipt["market_symbol_counts"] == {"SH": 0, "SZ": 9, "BJ": 0}
    assert {p: p.read_bytes() for p in overlay.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "failure",
    ["exchange", "date", "sidecar", "parquet", "invalid-date", "traversal", "unsupported"],
)
def test_cli_rejects_invalid_evidence_without_emitting_receipt(overlay: Path, failure: str) -> None:
    exchange, date = "SZ", TRADE_DATE
    partition = overlay / f"trade_date={TRADE_DATE}"
    if failure == "exchange":
        exchange = "SH"
    elif failure == "date":
        date = "20260707"
    elif failure == "invalid-date":
        date = "20260230"
    elif failure == "traversal":
        date = "../../20260706"
    elif failure == "unsupported":
        exchange = "BJ"
    elif failure == "sidecar":
        (partition / mins.COMPLETENESS_FILENAME).unlink()
    else:
        path = partition / "part-00000.parquet"
        frame = pd.read_parquet(path)
        frame.loc[0, "close"] = 123.0
        frame.to_parquet(path, index=False)
    result = _call(overlay, exchange=exchange, date=date)
    assert result.returncode != 0
    assert not result.stdout.strip()
