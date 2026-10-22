"""Explicit company category choices are audited without building PIT assets."""

import hashlib
import json

import pandas as pd
import pytest

from quant_market_data_platform.statement_selection import validate_statement_selector


@pytest.fixture
def selector(tmp_path):
    source = tmp_path / "normalized.parquet"
    pd.DataFrame(
        {
            "symbol": ["000001.SZ"] * 2,
            "report_period": ["20231231"] * 2,
            "disclosure_date": ["20240301"] * 2,
            "comp_type": ["2", "7"],
            "report_type": ["1"] * 2,
            "_source_retrieved_at": ["2026-10-01T08:00:00+00:00"] * 2,
            "revenue": [10.0, 20.0],
            "profit": [3.0, 3.0],
        }
    ).to_parquet(source, row_group_size=1)
    decision = {
        "symbol": "000001.SZ",
        "dataset": "income",
        "report_period": "20231231",
        "disclosure_date": "20240301",
        "available_date": "20240302",
        "allowed_comp_types": ["7"],
        "observed_at": "2026-10-03T08:00:00+00:00",
        "evidence": {
            "uri": "https://example.org/evidence",
            "sha256": "a" * 64,
            "observed_at": "2026-10-02T08:00:00+00:00",
        },
        "reason": "Fixture choice only; no economic policy is inferred.",
    }
    return tmp_path, {
        "schema_version": "statement-selector.v1",
        "policy_id": "fixture",
        "policy_version": "1",
        "available_delay_days": 1,
        "normalized_inputs": [
            {
                "dataset": "income",
                "path": str(source),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
        "decisions": [decision],
    }


def run(selector):
    root, payload = selector
    path = root / "selector.json"
    path.write_text(json.dumps(payload))
    return validate_statement_selector(selector_path=path)


def test_explicit_choice_preserves_provenance_and_reconstruction(selector):
    report = run(selector)
    assert report["valid"] is True
    assert report["evidence_content_verified"] is False
    event = report["decision_ledger"][0]
    assert event["selected_comp_type"] == "7"
    assert event["candidate_comp_types"] == ["2", "7"]
    assert event["rejected_comp_types"] == ["2"]
    assert event["differing_fields"] == ["revenue"]
    assert event["observation_class"] == "reconstructed"
    assert event["conservative_available_from_preview"] == "20261004"
    assert event["source_observed_at"] == ["2026-10-01T08:00:00+00:00"]
    assert event["decision_observed_at"] == "2026-10-03T08:00:00+00:00"
    assert event["evidence"]["observed_at"] == "2026-10-02T08:00:00+00:00"
    assert report["publication_ready"] is False


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "uncovered_event"),
        ("duplicate", "duplicate_coverage"),
        ("absent", "absent_comp_type"),
        ("multiple", "ambiguous_selection"),
        ("unmatched", "unmatched_decision"),
    ],
)
def test_rejects_incomplete_or_ambiguous_selection(selector, change, code):
    _, payload = selector
    decisions = payload["decisions"]
    if change == "missing":
        decisions.clear()
    elif change == "duplicate":
        decisions.append(dict(decisions[0]))
    elif change == "absent":
        decisions[0]["allowed_comp_types"] = ["9"]
    elif change == "multiple":
        decisions[0]["allowed_comp_types"] = ["2", "7"]
    else:
        decisions[0]["symbol"] = "999999.SZ"
    report = run(selector)
    assert report["valid"] is False
    assert code in {error["code"] for error in report["errors"]}
    assert all(row["selected_comp_type"] is None for row in report["decision_ledger"])


def test_hash_mismatch_rejected_before_parquet_read(selector, monkeypatch):
    selector[1]["normalized_inputs"][0]["sha256"] = "0" * 64

    def forbidden(*args, **kwargs):
        pytest.fail("must verify hash before reading parquet")

    import pyarrow.parquet as pq

    monkeypatch.setattr(pq, "ParquetFile", forbidden)
    with pytest.raises(ValueError, match="checksum"):
        run(selector)


@pytest.mark.parametrize("stamp", ["2026-10-03T08:00:00", "2099-01-01T00:00:00+00:00"])
def test_invalid_observation_timestamps_rejected(selector, stamp):
    selector[1]["decisions"][0]["observed_at"] = stamp
    with pytest.raises(ValueError, match="timestamp"):
        run(selector)


def test_available_boundary_cannot_be_backdated(selector):
    selector[1]["decisions"][0]["available_date"] = "20240301"
    with pytest.raises(ValueError, match="available_date"):
        run(selector)


def test_cli_emits_invalid_report_and_exit_one_without_writes(selector, capsys):
    from quant_market_data_platform.cli import main

    selector[1]["decisions"] = []
    run(selector)
    before = sorted(p.name for p in selector[0].iterdir())
    assert (
        main(
            [
                "tushare",
                "validate-a-share-statement-selector",
                "--selector",
                str(selector[0] / "selector.json"),
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["valid"] is False
    assert sorted(p.name for p in selector[0].iterdir()) == before


def test_projection_keys_and_unknown_contract_fields_rejected(selector):
    selector[1]["columns"] = ["profit"]
    with pytest.raises(ValueError, match="Unknown"):
        run(selector)


def test_source_observation_after_decision_cannot_claim_validity(selector):
    selector[1]["decisions"][0]["observed_at"] = "2026-09-30T08:00:00+00:00"
    selector[1]["decisions"][0]["evidence"]["observed_at"] = "2026-09-29T08:00:00+00:00"
    report = run(selector)
    assert report["valid"] is False
    assert report["errors"][0]["code"] == "decision_precedes_source_observation"


def test_late_report_type_is_not_treated_as_original_observation(selector):
    path = selector[0] / "normalized.parquet"
    frame = pd.read_parquet(path)
    frame.loc[1, "report_type"] = "4"
    frame.to_parquet(path)
    selector[1]["normalized_inputs"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="report_type=1"):
        run(selector)


def test_numeric_projection_cannot_hide_category_conflict(selector):
    selector[1]["decisions"] = []
    report = run(selector)
    assert report["conflict_events"] == 1
    # Equal profit does not make the two source categories interchangeable.
    assert report["decision_ledger"][0]["selected_comp_type"] is None
    assert "revenue" in report["decision_ledger"][0]["differing_fields"]


def test_observation_on_historical_available_day_is_still_reconstructed(selector):
    path = selector[0] / "normalized.parquet"
    frame = pd.read_parquet(path)
    frame["_source_retrieved_at"] = "2024-03-02T00:00:00+00:00"
    frame.to_parquet(path)
    selector[1]["normalized_inputs"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    decision = selector[1]["decisions"][0]
    decision["observed_at"] = "2024-03-02T01:00:00+00:00"
    decision["evidence"]["observed_at"] = "2024-03-02T00:00:00+00:00"
    assert run(selector)["decision_ledger"][0]["observation_class"] == "reconstructed"


def test_malformed_cli_contract_reports_exit_two(selector, capsys):
    from quant_market_data_platform.cli import main

    path = selector[0] / "bad.json"
    path.write_text("{}")
    assert main(["tushare", "validate-a-share-statement-selector", "--selector", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["valid"] is False
