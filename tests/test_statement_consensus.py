"""Projection consensus cannot select categories or erase unresolved events."""

import hashlib
import importlib

import pandas as pd
import pytest


def _source(tmp_path, **changes):
    rows = [
        {
            "symbol": "000001.SZ",
            "report_period": "20231231",
            "disclosure_date": "20240401",
            "comp_type": c,
            "report_type": "1",
            "_source_retrieved_at": "2026-10-22T10:00:00+00:00",
            "revenue": 10.0,
            "rd_exp": None,
            "update_flag": c,
        }
        for c in ["1", "7"]
    ]
    rows[1].update(changes)
    path = tmp_path / "source.parquet"
    pd.DataFrame(rows).to_parquet(path)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _audit(path, sha, fields=("revenue", "rd_exp")):
    module = importlib.import_module("quant_market_data_platform.statement_consensus")
    return module.audit_projection_consensus(
        dataset="income", source=path, expected_sha256=sha, fields=fields, available_delay_days=1
    )


def test_common_values_keep_all_candidates_and_late_observation(tmp_path):
    path, sha = _source(tmp_path)
    report = _audit(path, sha)
    event = report["events"][0]
    assert event["common_values"] == {"revenue": 10.0, "rd_exp": None}
    assert event["candidate_comp_types"] == ["1", "7"]
    assert len(event["candidates"]) == 2
    assert event["usable_at"] == "2026-10-22T10:00:00+00:00"
    assert event["status"] == "projection_consensus"
    assert report["historical_pit_certified"] is False
    assert report["published_asset"] is False
    assert "selected_comp_type" not in str(report)


@pytest.mark.parametrize("changes", [{"revenue": 20.0}, {"rd_exp": 0.0}])
def test_conflicts_are_retained_without_partial_fill_or_fallback(tmp_path, changes):
    path, sha = _source(tmp_path, **changes)
    event = _audit(path, sha)["events"][0]
    assert event["status"] == "projection_conflict"
    assert event["common_values"] is None
    assert event["blocks_fallback"] is True
    assert len(event["candidates"]) == 2


def test_projection_and_metadata_change_proof_identity(tmp_path):
    path, sha = _source(tmp_path)
    initial = _audit(path, sha)
    assert _audit(path, sha, ("revenue",))["proof_sha256"] != initial["proof_sha256"]
    path, sha2 = _source(tmp_path, update_flag="changed")
    assert _audit(path, sha2)["proof_sha256"] != initial["proof_sha256"]
    with pytest.raises(ValueError, match="checksum"):
        _audit(path, sha)


@pytest.mark.parametrize("fields", [(), ("revenue", "revenue"), ("missing",), ("comp_type",)])
def test_invalid_fields_rejected(tmp_path, fields):
    path, sha = _source(tmp_path)
    with pytest.raises(ValueError):
        _audit(path, sha, fields)


def test_missing_observation_rejected(tmp_path):
    path, sha = _source(tmp_path, _source_retrieved_at=None)
    with pytest.raises(ValueError, match="identity|observation"):
        _audit(path, sha)


def test_added_category_and_prior_period_are_not_dropped(tmp_path):
    path, sha = _source(tmp_path)
    original = _audit(path, sha)
    frame = pd.read_parquet(path)
    extra = frame.iloc[[0]].copy()
    extra["comp_type"] = "2"
    extra["revenue"] = 30.0
    prior = frame.copy()
    prior["report_period"] = "20230930"
    prior["disclosure_date"] = "20231030"
    pd.concat([frame, extra, prior], ignore_index=True).to_parquet(path)
    report = _audit(path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert report["proof_sha256"] != original["proof_sha256"]
    assert report["events"][0]["common_values"]["revenue"] == 10.0
    latest = report["events"][-1]
    assert latest["common_values"] is None
    assert latest["blocks_fallback"] is True
    assert latest["candidate_comp_types"] == ["1", "2", "7"]
    assert len(latest["candidates"]) == 3


def test_single_category_source_is_not_misrepresented_as_full_asset(tmp_path):
    path, _ = _source(tmp_path)
    pd.read_parquet(path).iloc[:1].to_parquet(path)
    report = _audit(path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert report["events"] == []
    assert report["normalized_row_count"] == 1
    assert report["complete_current_eligible"] is False


def test_nullable_large_integer_candidates_keep_exact_values(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path, _ = _source(tmp_path)
    rows = pd.read_parquet(path).to_dict("records")
    for row in rows:
        row["rd_exp"] = None
    rows[0]["revenue"], rows[1]["revenue"] = 2**53, 2**53 + 1
    nullable = [dict(row, symbol="000002.SZ", revenue=None) for row in rows]
    pq.write_table(pa.Table.from_pylist(rows + nullable), path)
    report = _audit(path, hashlib.sha256(path.read_bytes()).hexdigest())
    event = report["events"][0]
    assert event["status"] == "projection_conflict"
    assert [c["row"]["revenue"] for c in event["candidates"]] == [2**53, 2**53 + 1]


def test_arrow_nan_is_rejected_instead_of_becoming_null(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path, _ = _source(tmp_path)
    rows = pd.read_parquet(path).to_dict("records")
    rows[0]["revenue"], rows[1]["revenue"] = float("nan"), None
    for row in rows:
        row["rd_exp"] = None
    pq.write_table(pa.Table.from_pylist(rows), path)
    with pytest.raises(ValueError, match="JSON|range|finite"):
        _audit(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_schema_and_rows_use_one_verified_descriptor(tmp_path, monkeypatch):
    import quant_market_data_platform.statement_consensus as module

    path, sha = _source(tmp_path)
    original = module._read_candidates

    def replace_path_after_open(stream, fields):
        replacement = tmp_path / "replacement.parquet"
        frame = pd.read_parquet(path)
        frame["revenue"] = "not numeric"
        frame.to_parquet(replacement)
        replacement.replace(path)
        return original(stream, fields)

    monkeypatch.setattr(module, "_read_candidates", replace_path_after_open)
    with pytest.raises(ValueError, match="changed"):
        _audit(path, sha)


def test_string_projection_schema_rejected_on_bound_input(tmp_path):
    path, _ = _source(tmp_path)
    frame = pd.read_parquet(path)
    frame["revenue"] = "not numeric"
    frame.to_parquet(path)
    with pytest.raises(ValueError, match="numeric"):
        _audit(path, hashlib.sha256(path.read_bytes()).hexdigest())
