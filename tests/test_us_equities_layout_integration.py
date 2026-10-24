"""Offline layout regression fixtures; no production data or machine paths."""

import csv
import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_market_data_platform.offline_equities.identity import symbol_folder
from quant_market_data_platform.offline_equities.integrity import sha_file


def fixture(root, *, unsorted=False, extra_symbols=()):
    source = root / "source"
    evidence = root / "evidence"
    evidence.mkdir()
    (evidence / "reference_inputs").mkdir()
    (evidence / "wolf_identity_rule.json").write_text("{}", encoding="utf-8")
    (evidence / "reference_inputs/security_identity_intervals.csv").write_text(
        "symbol,start,end\n", encoding="utf-8"
    )
    entries, profiles = [], []
    symbols = ["BCPC", "BCpC", "TPC", "TpC", "WOLF", "BNY", "SKYD.WS", "SKYD.WSw"]
    symbols.extend(extra_symbols)
    for date in ["20250926", "20250929", "20260102"]:
        records = []
        for symbol in symbols:
            for i, minute in enumerate(["09:30", "09:31", "20:00"]):
                records.append(
                    {
                        "Date": date,
                        "Ticker": symbol,
                        "TimeBarStart": minute,
                        "AuditCandidateSecurityKey": ("old" if date < "20260101" else "new")
                        if symbol == "BNY"
                        else "",
                        "AuditSettlementBasis": "when_issued"
                        if symbol.endswith("w")
                        else "unverified",
                        "AuditTotalVolume": i,
                        "text": "汉字" if i == 2 else "" if i == 0 else None,
                        "number": None if i == 1 else 1.25,
                    }
                )
            profiles.append({"date": date, "symbol": symbol, "rows": "3"})
        if unsorted:
            records[0], records[1] = records[1], records[0]
        table = pa.Table.from_pylist(records)
        path = source / "parquet" / f"trade_date={date}" / "data.parquet"
        path.parent.mkdir(parents=True)
        pq.write_table(table, path, write_page_checksum=True, row_group_size=4)
        entries.append(
            {
                "date": date,
                "lane": "generic",
                "file": str(path.relative_to(source)),
                "rows": table.num_rows,
                "bytes": path.stat().st_size,
                "sha256": sha_file(path),
            }
        )
    with (source / "parquet_manifest.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(entries[0]))
        writer.writeheader()
        writer.writerows(entries)
    (source / "summary.json").write_text(
        json.dumps({"status": "complete", "rows": sum(e["rows"] for e in entries)}),
        encoding="utf-8",
    )
    pq.write_table(pa.Table.from_pylist(profiles), source / "file_quality.parquet")
    return source, evidence


def invoke(source, evidence, output, *, success=True, flags=()):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "quant_market_data_platform.offline_equities",
            "--source",
            str(source),
            "--identity-root",
            str(evidence),
            "--output",
            str(output),
            "--buckets",
            "4",
            "--stage-rows",
            "4",
            "--final-rows",
            "3",
            *flags,
        ],
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


def test_case_safe_lossless_layout_resume_and_recovery(tmp_path):
    source, evidence = fixture(tmp_path)
    output = tmp_path / "output"
    invoke(source, evidence, output)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["rows"] == 72
    connection = duckdb.connect()
    original = connection.read_parquet(
        str(source / "parquet/**/*.parquet"), hive_partitioning=False
    )
    final = connection.read_parquet(str(output / "parquet/**/*.parquet"), hive_partitioning=False)
    original.create_view("original")
    final.create_view("final")
    columns = ",".join(f'"{name}"' for name in original.columns)
    difference = connection.execute(
        "SELECT count(*) FROM (("
        f"SELECT {columns} FROM original EXCEPT ALL SELECT {columns} FROM final) "
        f"UNION ALL (SELECT {columns} FROM final EXCEPT ALL SELECT {columns} FROM original))"
    ).fetchone()
    assert difference is not None and difference[0] == 0
    rows = connection.execute(
        "SELECT DISTINCT Ticker,symbol FROM read_parquet(?,hive_partitioning=true)",
        [str(output / "parquet/**/*.parquet")],
    ).fetchall()
    assert len(rows) == 8
    assert all(ticker == partition for ticker, partition in rows)
    connection.close()
    invoke(source, evidence, output)
    assert json.loads((output / "summary.json").read_text(encoding="utf-8")) == summary
    checkpoint = next((output / "checkpoints/final").glob("*.json"))
    checkpoint.rename(checkpoint.with_suffix(".test-backup"))
    invoke(source, evidence, output)
    assert json.loads((output / "summary.json").read_text(encoding="utf-8")) == summary
    assert any((output / "recovery").iterdir())
    result = invoke(source, evidence, output, success=False, flags=("--buckets", "5"))
    assert "Configuration/input/code changed" in result.stderr
    assert json.loads((output / "progress.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_out_of_order_input_fails(tmp_path):
    source, evidence = fixture(tmp_path, unsorted=True)
    output = tmp_path / "output"
    invoke(source, evidence, output, success=False)
    assert not (output / "summary.json").exists()


def test_source_output_overlap_rejected_before_writing(tmp_path):
    source, evidence = fixture(tmp_path)
    result = invoke(source, evidence, source, success=False)
    assert "Source and output directories must be disjoint" in result.stderr
    assert not (source / "run_config.json").exists()


def test_manifest_escape_rejected(tmp_path):
    import shutil

    source, evidence = fixture(tmp_path)
    manifest = source / "parquet_manifest.csv"
    with manifest.open(encoding="utf-8") as stream:
        entries = list(csv.DictReader(stream))
    shutil.copy2(source / entries[0]["file"], tmp_path / "outside.parquet")
    entries[0]["file"] = "../outside.parquet"
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(entries[0]))
        writer.writeheader()
        writer.writerows(entries)
    result = invoke(source, evidence, tmp_path / "output", success=False)
    assert "Source part outside audited parquet root" in result.stderr


def test_unknown_requested_date_rejected(tmp_path):
    source, evidence = fixture(tmp_path)
    result = invoke(
        source,
        evidence,
        tmp_path / "output",
        success=False,
        flags=("--dates", "20250926", "19990101"),
    )
    assert "Requested dates absent from manifest" in result.stderr


def test_exact_symbol_selection_and_missing_symbol_rejection(tmp_path):
    source, evidence = fixture(tmp_path)
    output = tmp_path / "selected"
    invoke(source, evidence, output, flags=("--dates", "20250926", "--symbols", "BCPC", "BCpC"))
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert (summary["rows"], summary["symbols"], summary["scope"]) == (6, 2, "pilot")
    result = invoke(
        source,
        evidence,
        tmp_path / "missing",
        success=False,
        flags=("--symbols", "BCPC", "NONEXISTENT"),
    )
    assert "Requested symbols absent from source profiles" in result.stderr


def test_truncated_manifest_rejected(tmp_path):
    source, evidence = fixture(tmp_path)
    manifest = source / "parquet_manifest.csv"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    manifest.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    result = invoke(source, evidence, tmp_path / "output", success=False)
    assert "Completed source summary differs from manifest" in result.stderr


def test_partial_publish_recovery_with_encoded_symbols(tmp_path, monkeypatch):
    from quant_market_data_platform.offline_equities.__main__ import main

    source, evidence = fixture(tmp_path, extra_symbols=("A/B/C", "A\\B\\C"))
    output = tmp_path / "output"
    original_rename = Path.rename
    published = []

    def interrupt_after_one_directory(path, destination):
        if Path(destination).parent == output / "parquet":
            if published:
                raise OSError("Injected publication interruption")
            result = original_rename(path, destination)
            published.append(str(destination))
            return result
        return original_rename(path, destination)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", interrupt_after_one_directory)
        patch.setattr(
            sys,
            "argv",
            [
                "offline-equities",
                "--source",
                str(source),
                "--identity-root",
                str(evidence),
                "--output",
                str(output),
                "--buckets",
                "1",
                "--stage-rows",
                "4",
                "--final-rows",
                "3",
            ],
        )
        with pytest.raises(OSError, match="Injected publication interruption"):
            main()
    assert len(published) == 1
    assert not (output / "checkpoints/final/000.json").exists()
    invoke(source, evidence, output, flags=("--buckets", "1"))
    assert json.loads((output / "summary.json").read_text(encoding="utf-8"))["rows"] == 90


def test_symbol_folder_roundtrip_and_casefold_uniqueness():
    from urllib.parse import unquote

    symbols = ["BCPC", "BCpC", "TPC", "TpC", "SKYD.WS", "SKYD.WSw", "A/B", "A%2FB", "ABC", "ABC."]
    folders = [symbol_folder(symbol) for symbol in symbols]
    assert len({name.casefold() for name in folders}) == len(symbols)
    assert [unquote(name.removeprefix("symbol=")) for name in folders] == symbols
    assert all(not name.endswith(".") for name in folders)


@pytest.mark.parametrize("symbol", ["", ".", ".."])
def test_invalid_symbol_path(symbol):
    with pytest.raises(ValueError):
        symbol_folder(symbol)
