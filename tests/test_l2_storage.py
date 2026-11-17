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


def test_build_routes_shared_bucket_keeps_duplicates_and_original_rows(tmp_path):
    from quant_market_data_platform.l2_storage.build import build_csv
    from quant_market_data_platform.l2_storage.profile import profile_directory
    from quant_market_data_platform.l2_storage.reader import scan, verify

    source = tmp_path / "input"
    csv_member(
        source,
        "000001.SZ",
        [
            ["000001.SZ", "20260914", "100500000", "0007", "12000", ""],
            ["000001.SZ", "20260914", "100000000", "8", "12001", "X"],
            ["000001.SZ", "20260914", "100000000", "8", "12001", "X"],
        ],
    )
    csv_member(
        source,
        "600000.SH",
        [
            ["600000.SH", "20260914", "100000000", "9", "14000", "N/A"],
        ],
    )
    root = tmp_path / "output"
    profile = profile_directory(source, "20260914")
    result = build_csv(profile, root, "a" * 64, options={"buckets": 1, "row_group_rows": 2})
    assert result["status"] == "verified"
    table = scan(root, "trades", ["000001.SZ"], 100000000, 100500000).to_table()
    assert table["编号"].to_pylist() == ["8", "8"]
    assert table["_source_row"].to_pylist() == [1, 2]
    assert table["未知字段"].to_pylist() == ["X", "X"]
    assert scan(root, "trades").count_rows() == 4
    assert scan(root, "trades", ["000002.SZ"]).count_rows() == 0
    assert verify(root)["rows"] == 4
    assert build_csv(profile, root, "a" * 64, options={"buckets": 1, "row_group_rows": 2}) == result
    with pytest.raises(ValueError):
        build_csv(profile, root, "b" * 64, options={"buckets": 1, "row_group_rows": 2})


def test_builder_rejects_unknown_output_and_changed_source(tmp_path):
    from quant_market_data_platform.l2_storage.build import build_csv
    from quant_market_data_platform.l2_storage.profile import profile_directory

    source = tmp_path / "input"
    p = csv_member(source, "000001.SZ", [["000001.SZ", "20260914", "100000000", "7", "1", ""]])
    profile = profile_directory(source, "20260914")
    unknown = tmp_path / "unknown"
    unknown.mkdir()
    (unknown / "other.txt").write_text("preserve")
    with pytest.raises(ValueError):
        build_csv(profile, unknown, "a" * 64)
    p.write_text(p.read_text(encoding="gb18030").replace(",7,", ",8,"), encoding="gb18030")
    with pytest.raises(ValueError):
        build_csv(profile, tmp_path / "output", "a" * 64)
    assert (unknown / "other.txt").read_text() == "preserve"


def test_verifier_rejects_corrupted_output_and_manifest_escape(tmp_path):
    import json

    from quant_market_data_platform.l2_storage.build import build_csv
    from quant_market_data_platform.l2_storage.profile import profile_directory
    from quant_market_data_platform.l2_storage.reader import scan, verify

    source = tmp_path / "input"
    csv_member(source, "000001.SZ", [["000001.SZ", "20260914", "100000000", "7", "1", ""]])
    root = tmp_path / "output"
    build_csv(profile_directory(source, "20260914"), root, "a" * 64)
    parquet = next(root.rglob("*.parquet"))
    with parquet.open("ab") as f:
        f.write(b"corruption")
    with pytest.raises(ValueError):
        verify(root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["path"] = "../outside.parquet"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        scan(root, "trades")


def test_empty_csv_and_overflow_identifiers_are_lossless(tmp_path):
    from quant_market_data_platform.l2_storage.profile import profile_directory

    csv_member(tmp_path, "000001.SZ", [])
    csv_member(
        tmp_path,
        "600000.SH",
        [
            ["600000.SH", "20260914", "100000000", "18446744073709551616", "-0", ""],
        ],
    )
    p = profile_directory(tmp_path, "20260914")
    assert "编号" not in p["modalities"]["trades"]["integer_columns"]
    assert "价格" not in p["modalities"]["trades"]["integer_columns"]
    assert sorted(m["rows"] for m in p["members"]) == [0, 1]


def test_profile_guard_rejects_oversized_input_before_parsing(tmp_path, monkeypatch):
    from quant_market_data_platform.l2_storage import profile

    p = csv_member(tmp_path, "000001.SZ", [])
    monkeypatch.setattr(profile, "MAX_RAW_BYTES", 1)
    with pytest.raises(ValueError, match="guard"):
        profile.read_csv(p)


def test_verifier_rejects_duplicate_catalog_entry(tmp_path):
    import json

    from quant_market_data_platform.l2_storage.build import build_csv
    from quant_market_data_platform.l2_storage.profile import profile_directory
    from quant_market_data_platform.l2_storage.reader import verify

    source = tmp_path / "input"
    csv_member(source, "000001.SZ", [["000001.SZ", "20260914", "100000000", "7", "1", ""]])
    root = tmp_path / "output"
    build_csv(profile_directory(source, "20260914"), root, "a" * 64)
    p = root / "manifest.json"
    record = json.loads(p.read_text())
    record["files"].append(record["files"][0])
    p.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        verify(root)


def test_cli_help_and_verify_failure_are_reviewable(tmp_path):
    import subprocess
    import sys

    command = [sys.executable, "-m", "quant_market_data_platform.l2_storage"]
    result = subprocess.run([*command, "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "build" in result.stdout and "verify" in result.stdout
    result = subprocess.run(
        [*command, "verify", "--root", str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "ERROR" in result.stderr
