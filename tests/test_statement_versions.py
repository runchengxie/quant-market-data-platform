"""Observed timestamps bound financial statement visibility."""

import hashlib
import json

import pandas as pd
import pytest

from quant_market_data_platform.statement_versions import (
    build_statement_version_ledger,
    read_statement_observations,
)


def test_visibility_and_immutable_output(tmp_path):
    raw = tmp_path / "raw.parquet"
    pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "end_date": ["20231231"],
            "ann_date": ["20240301"],
            "report_type": ["5"],
            "revenue": [12.0],
        }
    ).to_parquet(raw)
    manifest = tmp_path / "receipt.json"
    entry = {
        "endpoint": "income_vip",
        "path": str(raw),
        "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        "retrieved_at": "2026-10-02T08:00:00+00:00",
    }
    manifest.write_text(json.dumps({"status": "completed", "entries": [entry]}))
    output = tmp_path / "ledger"
    result = build_statement_version_ledger(source_manifests=[str(manifest)], out_dir=str(output))
    assert result["rows"] == 1
    frame = pd.read_parquet(output / "part-00000.parquet")
    assert frame.available_from.tolist() == ["20261003"]
    assert result["historical_revision_completeness"] is False
    assert read_statement_observations(
        asset_dir=str(output), as_of="20250101", report_type="5"
    ).empty
    assert (
        len(read_statement_observations(asset_dir=str(output), as_of="20261003", report_type="5"))
        == 1
    )
    with pytest.raises(FileExistsError):
        build_statement_version_ledger(source_manifests=[str(manifest)], out_dir=str(output))
    raw.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        build_statement_version_ledger(
            source_manifests=[str(manifest)], out_dir=str(tmp_path / "bad")
        )
    assert json.loads((tmp_path / "bad" / "manifest.json").read_text())["status"] == "failed"


def test_query_report_type_expansion():
    from quant_market_data_platform.providers.tushare_a_share_fundamentals import plan_query_units

    units = plan_query_units(
        dataset="income",
        start_date="20240331",
        end_date="20240331",
        entitlement_mode="vip_batch",
        report_types=("1", "4", "5"),
    )
    assert {unit.params["report_type"] for unit in units} == {"1", "4", "5"}
