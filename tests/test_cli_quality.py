from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform import cli


def test_quality_profile_cli_writes_report(tmp_path: Path) -> None:
    source = tmp_path / "order_2024-01-02.parquet"
    output = tmp_path / "quality.json"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "ticker": "000001",
                    "TradingDay": 20240102,
                    "time_ms": -1,
                    "Price": 100,
                    "Volume": 10,
                    "OrderID": 1,
                }
            ]
        ),
        source,
    )

    assert cli.main(["quality", "profile", "--file", str(source), "--output", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "complete"
    assert payload["reports"][0]["rows"] == 1


def test_quality_command_is_in_public_help(capsys) -> None:
    try:
        cli.main(["quality", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "profile" in output
    assert "gate" in output


def test_quality_integrity_cli_writes_report(tmp_path: Path) -> None:
    orders = tmp_path / "order_2024-01-02.parquet"
    trades = tmp_path / "trades_2024-01-02.parquet"
    output = tmp_path / "integrity.json"
    pq.write_table(pa.Table.from_pylist([{"OrderID": 1}]), orders)
    pq.write_table(
        pa.Table.from_pylist([{"BuyID": 1, "SellID": 2}]),
        trades,
    )

    assert (
        cli.main(
            [
                "quality",
                "integrity",
                "--orders",
                str(orders),
                "--trades",
                str(trades),
                "--output",
                str(output),
            ]
        )
        == 0
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["unknown_buy_id_rows"] == 0
    assert payload["unknown_sell_id_rows"] == 1
