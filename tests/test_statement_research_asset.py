"""Independent consumers cannot hide conflicts or expand the declared scope."""

import hashlib
import json
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_market_data_platform.statement_research_asset import (
    build_research_asset,
    read_research_event,
)


def _asset(tmp_path, *, conflict=False, observed="2026-10-22T10:00:00+00:00"):
    rows = [
        {
            "symbol": "000001.SZ",
            "report_period": "20231231",
            "disclosure_date": "20240401",
            "comp_type": c,
            "report_type": "1",
            "_source_retrieved_at": observed,
            "revenue": 2**53 + (int(c == "7") if conflict else 0),
            "rd_exp": None,
        }
        for c in ["1", "7"]
    ]
    source = tmp_path / "source.parquet"
    pq.write_table(pa.Table.from_pylist(rows), source)
    specs = [
        {
            "dataset": "income",
            "source": source,
            "expected_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "fields": ("revenue", "rd_exp"),
            "available_delay_days": 1,
        }
    ]
    asset = build_research_asset(sources=specs, code_revision="a" * 40)
    path = tmp_path / "asset.json"
    path.write_text(json.dumps(asset, allow_nan=False))
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), asset, specs


def _read(path, sha, **changes):
    args: dict[str, Any] = {
        "expected_sha256": sha,
        "accept_partial_reconstructed": True,
        "dataset": "income",
        "symbol": "000001.SZ",
        "report_period": "20231231",
        "disclosure_date": "20240401",
        "as_of": "2026-10-22T10:00:00+00:00",
    }
    args.update(changes)
    args["event_key"] = tuple(
        args.pop(k) for k in ("dataset", "symbol", "report_period", "disclosure_date")
    )
    return read_research_event(path, **args)


def test_exact_values_missingness_lineage_and_opt_in(tmp_path):
    path, sha, asset, specs = _asset(tmp_path)
    result = _read(path, sha)
    assert result["status"] == "available"
    assert result["values"] == {"revenue": 2**53, "rd_exp": None}
    assert result["missing_fields"] == ["rd_exp"]
    assert len(result["event"]["candidates"]) == 2
    assert result["source"]["sha256"] == specs[0]["expected_sha256"]
    assert build_research_asset(sources=specs, code_revision="a" * 40) == asset
    with pytest.raises(ValueError, match="acceptance"):
        _read(path, sha, accept_partial_reconstructed=False)


def test_conflict_interval_boundary_and_out_of_scope_never_fall_back(tmp_path):
    path, sha, _, _ = _asset(tmp_path, conflict=True)
    assert _read(path, sha, as_of="2026-10-22T09:59:59+00:00")["status"] == "not_yet_available"
    for date in ["2026-10-22T10:00:00+00:00", "2030-01-01T00:00:00+00:00"]:
        result = _read(path, sha, as_of=date)
        assert result["status"] == "conflict"
        assert result["values"] is None
        assert result["blocks_fallback"] is True
        assert result["event"]["active_interval"]["end"] is None
    for changed in [
        {"report_period": "20240930"},
        {"disclosure_date": "20240402"},
        {"symbol": "missing"},
        {"dataset": "cashflow"},
    ]:
        result = _read(path, sha, **changed)
        assert result["status"] == "out_of_scope"
        assert result["values"] is None


def test_disclosure_delay_also_limits_availability(tmp_path):
    path, sha, _, _ = _asset(tmp_path, observed="2024-04-01T10:00:00+00:00")
    assert _read(path, sha, as_of="2024-04-01T23:59:59Z")["status"] == "not_yet_available"
    assert _read(path, sha, as_of="2024-04-02T08:00:00+08:00")["status"] == "available"
    with pytest.raises(ValueError, match="timezone"):
        _read(path, sha, as_of="2026-10-22")


def test_artifact_and_source_mutation_fail_closed(tmp_path):
    path, sha, _, specs = _asset(tmp_path)
    path.write_text(path.read_text().replace('"partial": true', '"partial": false'))
    with pytest.raises(ValueError, match="checksum"):
        _read(path, sha)
    with pytest.raises(ValueError, match="version"):
        _read(path, hashlib.sha256(path.read_bytes()).hexdigest())
    specs[0]["source"].write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        build_research_asset(sources=specs, code_revision="a" * 40)


def test_before_availability_exposes_no_future_candidate_values(tmp_path):
    path, sha, _, _ = _asset(tmp_path)
    result = _read(path, sha, as_of="2020-01-01T00:00:00Z")
    assert set(result) == {
        "version_sha256",
        "partial",
        "reconstructed",
        "status",
        "blocks_fallback",
        "values",
        "usable_at",
    }
    assert result["values"] is None
