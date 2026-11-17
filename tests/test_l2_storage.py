from __future__ import annotations

import csv
from pathlib import Path

import pyarrow as pa
import pytest


def csv_member(root: Path, symbol: str, rows: list[list[str]], name="逐笔成交.csv") -> Path:
    path = root / "20260914" / symbol / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="gb18030", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["万得代码", "自然日", "时间", "编号", "价格", "未知字段"])
        writer.writerows(rows)
    return path


def test_profile_preserves_blanks_leading_zero_and_rare_values(tmp_path):
    from quant_market_data_platform.l2_storage.profile import profile_directory, read_csv

    a = csv_member(
        tmp_path,
        "000001.SZ",
        [
            ["000001.SZ", "20260914", "100000000", "0007", "12000", ""],
            ["000001.SZ", "20260914", "100000000", "8", "12000", "N/A"],
        ],
    )
    csv_member(
        tmp_path,
        "600000.SH",
        [
            ["600000.SH", "20260914", "100000001", "X", "13000", "0"],
        ],
    )
    result = profile_directory(tmp_path, "20260914")
    info = result["modalities"]["trades"]
    assert "编号" not in info["integer_columns"]
    assert "未知字段" not in info["integer_columns"]
    assert "价格" in info["integer_columns"]
    assert "万得代码" not in info["integer_columns"]
    assert sum(m["rows"] for m in result["members"]) == 3
    table = read_csv(a)
    assert table["未知字段"].to_pylist() == ["", "N/A"]
    assert table["编号"].to_pylist() == ["0007", "8"]
    assert table.schema.field("价格").type == pa.string()


@pytest.mark.parametrize("change", ["symbol", "date", "clock", "header"])
def test_profile_rejects_inconsistent_member(tmp_path, change):
    from quant_market_data_platform.l2_storage.profile import profile_directory

    values = ["000001.SZ", "20260914", "100000000", "7", "12000", ""]
    if change == "symbol":
        values[0] = "600000.SH"
    if change == "date":
        values[1] = "20260915"
    if change == "clock":
        values[2] = "106000000"
    csv_member(tmp_path, "000001.SZ", [values])
    if change == "header":
        p = csv_member(tmp_path, "600000.SH", [["600000.SH", *values[1:]]])
        p.write_text(
            p.read_text(encoding="gb18030").replace("未知字段", "另一个字段"), encoding="gb18030"
        )
    with pytest.raises(ValueError):
        profile_directory(tmp_path, "20260914")


@pytest.mark.parametrize(
    "path",
    ["../bad.csv", "/bad.csv", "20260915/000001.SZ/行情.csv", "20260914/000001.SZ/other.txt"],
)
def test_archive_inventory_rejects_unsafe_or_unaccounted_members(path):
    from quant_market_data_platform.l2_storage.archive import parse_listing

    listing = (
        f"header\n----------\nPath = {path}\nSize = 1\nAttributes = A -rw-r--r--\nEncrypted = -\n\n"
    )
    with pytest.raises(ValueError):
        parse_listing(listing, "20260914")


def test_archive_inventory_rejects_links_and_duplicates():
    from quant_market_data_platform.l2_storage.archive import parse_listing

    entry = (
        "Path = 20260914/000001.SZ/行情.csv\nSize = 10\n"
        "Attributes = A -rw-r--r--\nEncrypted = -\n\n"
    )
    for content in [
        entry + entry,
        entry.replace("Encrypted = -", "Symbolic Link = /tmp/x"),
        entry.replace("Encrypted = -", "Encrypted = +"),
    ]:
        with pytest.raises(ValueError):
            parse_listing("header\n----------\n" + content, "20260914")
