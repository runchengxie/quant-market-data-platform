from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from quant_market_data_platform.providers.tushare_reference_manifest import write_reference_manifest


@pytest.mark.parametrize("source_quality", ["complete", "partial"])
def test_reference_manifest_preserves_source_quality_and_semantics(tmp_path, source_quality):
    asset = tmp_path / "latest.parquet"
    asset.write_bytes(b"owner-validated-payload")
    receipt = tmp_path / "latest.receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "dataset": "margin_secs",
                "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                "rows": 1,
                "columns": ["ts_code"],
                "quality_status": "complete",
                "source_quality_status": source_quality,
                "target_date": "20260930",
                "semantics": "qualification_not_inventory",
                "revision_safe": False,
            }
        )
    )
    manifest = json.loads(write_reference_manifest(asset, receipt).read_text())
    assert manifest["status"] == ("completed" if source_quality == "complete" else "partial")
    assert manifest["semantics"] == "qualification_not_inventory"
    assert manifest["revision_safe"] is False
    assert manifest["as_of_date"] == "20260930"
    assert (
        manifest["lineage"]["owner_receipt_sha256"]
        == hashlib.sha256(receipt.read_bytes()).hexdigest()
    )
    previous = asset.with_suffix(".manifest.yml").read_bytes()
    asset.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash does not match"):
        write_reference_manifest(asset, receipt)
    assert asset.with_suffix(".manifest.yml").read_bytes() == previous


def test_missing_constraint_receipt_stays_partial(tmp_path):
    import pandas as pd

    from quant_market_data_platform.providers.tushare_constraint_publish import (
        publish_constraint_assets,
    )

    source = tmp_path / "source"
    source.mkdir()
    pd.DataFrame({"ts_code": ["000001.SZ"]}).to_parquet(source / "margin_secs.parquet", index=False)
    item = publish_constraint_assets(
        tmp_path / "lake", source, "20260930", allow_partial=True, datasets=["margin_secs"]
    )["published"][0]
    manifest = json.loads(Path(item["path"]).with_suffix(".manifest.yml").read_text())
    assert manifest["status"] == "partial"
    assert manifest["source_quality_status"] == "unknown"


def test_lineage_cannot_override_payload_hash_before_publication(tmp_path):
    from quant_market_data_platform.providers.tushare_a_share_reference import (
        publish_reference_asset,
    )

    with pytest.raises(ValueError, match="unsupported.*lineage"):
        publish_reference_asset(
            "margin_secs",
            tmp_path / "missing.parquet",
            tmp_path / "latest.parquet",
            "20260930",
            receipt_lineage={"sha256": "wrong"},
        )
    assert not (tmp_path / "latest.parquet").exists()


def test_manifest_parses_and_hashes_one_receipt_snapshot(tmp_path, monkeypatch):
    asset = tmp_path / "latest.parquet"
    asset.write_bytes(b"payload")
    receipt = tmp_path / "latest.receipt.json"
    original_bytes = json.dumps(
        {
            "dataset": "stock_st",
            "sha256": hashlib.sha256(b"payload").hexdigest(),
            "rows": 1,
            "columns": ["ts_code"],
            "quality_status": "complete",
            "target_date": "20260930",
        }
    ).encode()
    receipt.write_bytes(original_bytes)
    original_reader = Path.read_bytes
    reads = []

    def read_and_replace(path):
        data = original_reader(path)
        if path == receipt:
            reads.append(path)
            changed = json.loads(data)
            changed["quality_status"] = "partial"
            receipt.write_text(json.dumps(changed))
        return data

    monkeypatch.setattr(Path, "read_bytes", read_and_replace)
    manifest = json.loads(write_reference_manifest(asset, receipt).read_text())
    assert len(reads) == 1
    assert manifest["status"] == "completed"
    assert manifest["lineage"]["owner_receipt_sha256"] == hashlib.sha256(original_bytes).hexdigest()


def test_reference_manifest_distinguishes_version_date_from_coverage(tmp_path):
    asset = tmp_path / "latest.parquet"
    asset.write_bytes(b"validated-history")
    receipt = tmp_path / "latest.receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "dataset": "st_history_reconstructed",
                "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                "rows": 1,
                "columns": ["ts_code"],
                "quality_status": "complete",
                "source_quality_status": "complete",
                "target_date": "20261002",
                "end_date": "20260930",
                "pit_class": "reconstructed_pit",
                "revision_safe": False,
            }
        )
    )
    manifest = json.loads(write_reference_manifest(asset, receipt).read_text())
    assert manifest["as_of_date"] == "20260930"
    assert manifest["query"]["end_date"] == "20260930"
    assert manifest["version_date"] == "20261002"
    assert manifest["revision_safe"] is False
