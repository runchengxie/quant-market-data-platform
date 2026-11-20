from __future__ import annotations

import json
from pathlib import Path

import pytest

from quant_market_data_platform.l2_storage.__main__ import main
from quant_market_data_platform.l2_storage.build import build_csv
from quant_market_data_platform.l2_storage.profile import profile_directory


def accepted_day(base: Path, receipts: Path, day: str) -> Path:
    source = base / "source" / day / "000001.SZ" / "逐笔成交.csv"
    source.parent.mkdir(parents=True)
    source.write_text(
        f"万得代码,自然日,时间,价格\n000001.SZ,{day},100000000,12000\n",
        encoding="gb18030",
    )
    root = base / "output"
    digest = "a" * 64
    build_csv(profile_directory(base / "source", day), root, digest)
    receipts.mkdir(exist_ok=True)
    receipt = receipts / f"{day}-layout-acceptance.json"
    receipt.write_text(
        json.dumps(
            {
                "date": day,
                "status": "full_field_and_query_checks_passed",
                "archive_sha256": digest,
                "output": str(root),
                "verification": {"status": "verified"},
            }
        )
    )
    return receipt


def query(receipts: Path, *days: str, count: bool = True) -> list[str]:
    args = [
        "query-accepted",
        "--receipt-dir",
        str(receipts),
        "--modality",
        "trades",
        "--symbol",
        "000001.SZ",
        "--start",
        "100000000",
        "--stop",
        "100500000",
    ]
    for day in days:
        args.extend(["--date", day])
    if count:
        args.append("--count")
    return args


def test_accepted_query_counts_and_groups_by_requested_date(tmp_path, capsys):
    receipts = tmp_path / "receipts"
    accepted_day(tmp_path / "one", receipts, "20260914")
    accepted_day(tmp_path / "two", receipts, "20260915")
    assert main(query(receipts, "20260915", "20260914")) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"date": "20260915", "rows": 1},
        {"date": "20260914", "rows": 1},
    ]
    assert main(query(receipts, "20260914", count=False)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result[0]["date"] == "20260914"
    assert result[0]["records"][0]["价格"] == 12000


@pytest.mark.parametrize(
    "issue", ["missing", "duplicate", "date", "digest", "status", "wrong_output"]
)
def test_accepted_query_rejects_entire_request_on_invalid_date(tmp_path, capsys, issue):
    receipts = tmp_path / "receipts"
    first = accepted_day(tmp_path / "one", receipts, "20260914")
    if issue != "missing":
        second = accepted_day(tmp_path / "two", receipts, "20260915")
        if issue == "duplicate":
            (receipts / "20260915-other-acceptance.json").write_text(second.read_text())
        else:
            data = json.loads(second.read_text())
            field, value = {
                "date": ("date", "20260914"),
                "digest": ("archive_sha256", "b" * 64),
                "status": ("status", "building"),
                "wrong_output": ("output", json.loads(first.read_text())["output"]),
            }[issue]
            data[field] = value
            second.write_text(json.dumps(data))
    assert main(query(receipts, "20260914", "20260915")) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "20260915" in output.err
