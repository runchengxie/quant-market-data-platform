"""Source and record labels remain distinct, without canonicalizing identities."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


def member(root: Path, directory: str, actual: str, *, name="行情.csv"):
    path = root / "20260914" / directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="gb18030", newline="") as f:
        w = csv.writer(f)
        w.writerow(["万得代码", "自然日", "时间", "编号"])
        w.writerow([actual, "20260914", "100000000", "0007"])
    return path


def test_suffix_conflicts_require_explicit_lossless_policy(tmp_path):
    from quant_market_data_platform.l2_storage.profile import profile_directory

    member(tmp_path, "110075.SZ", "110075.SH")
    with pytest.raises(ValueError):
        profile_directory(tmp_path, "20260914")
    p = profile_directory(tmp_path, "20260914", identity_policy="preserve-source-labels")
    m = p["members"][0]
    assert m["symbol"] == "110075.SZ"
    assert m["record_symbol"] == "110075.SH"
    assert m["identity_status"] == "source_label_conflict"
    assert p["identity_policy"] == "preserve-source-labels"


@pytest.mark.parametrize("actual", ["110076.SH", "110075.XX", "", "110075.SH,other"])
def test_policy_still_rejects_unrelated_or_invalid_identifiers(tmp_path, actual):
    from quant_market_data_platform.l2_storage.profile import profile_directory

    member(tmp_path, "110075.SZ", actual)
    with pytest.raises(ValueError):
        profile_directory(tmp_path, "20260914", identity_policy="preserve-source-labels")


def test_mixed_record_identifiers_in_one_member_are_rejected(tmp_path):
    from quant_market_data_platform.l2_storage.profile import profile_directory

    p = member(tmp_path, "110075.SZ", "110075.SH")
    with p.open("a", encoding="gb18030", newline="") as f:
        csv.writer(f).writerow(["110075.SZ", "20260914", "100000001", "8"])
    with pytest.raises(ValueError):
        profile_directory(tmp_path, "20260914", identity_policy="preserve-source-labels")


@pytest.mark.parametrize("bucket_count", [1, 64])
def test_record_query_routes_all_provenance_buckets_without_merging_members(tmp_path, bucket_count):
    from quant_market_data_platform.l2_storage.build import build_csv
    from quant_market_data_platform.l2_storage.layout import bucket
    from quant_market_data_platform.l2_storage.profile import profile_directory
    from quant_market_data_platform.l2_storage.reader import scan, verify

    root = tmp_path / "input"
    member(root, "110075.SZ", "110075.SH")
    member(root, "110075.SH", "110075.SH")
    member(root, "110075.SZ", "110075.SZ", name="逐笔成交.csv")
    p = profile_directory(root, "20260914", identity_policy="preserve-source-labels")
    output = tmp_path / "output"
    if bucket_count == 64:
        assert bucket("110075.SZ", 64) != bucket("110075.SH", 64)
    build_csv(p, output, "a" * 64, options={"buckets": bucket_count})
    table = scan(output, "snapshots", ["110075.SH"], 100000000, 100500000).to_table()
    assert table.num_rows == 2
    assert table["万得代码"].to_pylist() == ["110075.SH", "110075.SH"]
    assert sorted(table["_source_symbol"].to_pylist()) == ["110075.SH", "110075.SZ"]
    assert table["_source_row"].to_pylist() == [0, 0]
    assert table["编号"].to_pylist() == ["0007", "0007"]
    assert scan(output, "snapshots", ["110075.SZ"]).count_rows() == 0
    assert scan(output, "trades", ["110075.SZ"]).count_rows() == 1
    assert verify(output)["rows"] == 3
    assert verify(output)["source_label_conflicts"] == 1


def test_unknown_identity_policy_does_not_silently_enable_exceptions(tmp_path):
    from quant_market_data_platform.l2_storage.profile import profile_directory

    member(tmp_path, "110075.SZ", "110075.SH")
    with pytest.raises(ValueError):
        profile_directory(tmp_path, "20260914", identity_policy="typo")


def test_independent_verify_rejects_suppressed_record_label_conflict(tmp_path):
    import json

    from quant_market_data_platform.l2_storage.build import build_csv
    from quant_market_data_platform.l2_storage.profile import profile_directory
    from quant_market_data_platform.l2_storage.proof import sha_file
    from quant_market_data_platform.l2_storage.reader import verify

    inputs = tmp_path / "input"
    member(inputs, "110075.SZ", "110075.SH")
    p = profile_directory(inputs, "20260914", identity_policy="preserve-source-labels")
    root = tmp_path / "output"
    build_csv(p, root, "a" * 64)
    profile_path = root / "profile.json"
    stored = json.loads(profile_path.read_text())
    stored["members"][0]["record_symbol"] = "110075.SZ"
    stored["members"][0]["identity_status"] = "matched"
    profile_path.write_text(json.dumps(stored))
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["profile_sha256"] = sha_file(profile_path)
    manifest["identity_summary"]["conflicting_members"] = 0
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify(root)


def test_cli_policy_preserves_all_labels_after_verified_extraction(tmp_path, monkeypatch, capsys):
    import json

    from quant_market_data_platform.l2_storage import build as builder
    from quant_market_data_platform.l2_storage.__main__ import main
    from quant_market_data_platform.l2_storage.reader import scan

    inputs = tmp_path / "input"
    for name in ["行情.csv", "逐笔委托.csv", "逐笔成交.csv"]:
        member(inputs, "110075.SZ", "110075.SH", name=name)
    # Isolate CLI policy handling after the external archive extraction boundary.
    monkeypatch.setattr(builder, "extract", lambda *args: inputs)
    root = tmp_path / "output"
    result = main(
        [
            "build",
            "--archive",
            str(tmp_path / "20260914.7z"),
            "--output",
            str(root),
            "--scratch",
            str(tmp_path / "scratch"),
            "--extractor",
            "unused",
            "--expected-sha256",
            "a" * 64,
            "--identity-policy",
            "preserve-source-labels",
        ]
    )
    assert result == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["config"]["identity_policy"] == "preserve-source-labels"
    assert manifest["identity_summary"]["conflicting_members"] == 3
    table = scan(root, "trades", ["110075.SH"]).to_table()
    assert table["万得代码"].to_pylist() == ["110075.SH"]
    assert table["_source_symbol"].to_pylist() == ["110075.SZ"]
