"""Manual field evidence cannot authorize an event template or historical holdings."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from test_statement_research_asset import _asset

from quant_market_data_platform import statement_field_adjudication as adjudication
from quant_market_data_platform.statement_research_asset import read_research_event

KEY = ("income", "000001.SZ", "20231231", "20240401", "revenue")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path):
    source, sha, original, _ = _asset(tmp_path, conflict=True)
    pdf = tmp_path / "evidence.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic test evidence")
    entry = {
        **dict(
            zip(("dataset", "symbol", "report_period", "disclosure_date"), KEY[:4], strict=True)
        ),
        "source_file": str(pdf),
        "sha256": _sha(pdf),
        "official_url": "https://example.test/asserted-source.pdf",
        "pdf_pages": [1],
        "fields": {"revenue": 9007199254740993, "rd_exp": None},
        "verified_at": "2026-10-22T11:00:00+00:00",
        "source_acquisition": {"retrieved_at": "2026-10-22T12:00:00+00:00"},
        "original_period_current_column": True,
        "historical_possession_certified": False,
        "whole_row_selector_certified": False,
        "field_semantics": "Manual reading of synthetic current-period column",
    }
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps([entry]))
    args = {
        "source": (source, sha),
        "ledger": (ledger, _sha(ledger)),
        "selections": (KEY,),
        "code_revision": "b" * 40,
    }
    return args, entry, original, pdf


def _build(tmp_path, args):
    result = adjudication.build_field_adjudicated_asset(**args)
    path = tmp_path / "adjudicated.json"
    path.write_text(json.dumps(result, allow_nan=False))
    return path, result


def _read(path, **changes):
    args = {
        "expected_sha256": _sha(path),
        "accept_manual_field_adjudication": True,
        "field_key": KEY,
        "as_of": "2099-01-01T00:00:00+00:00",
    }
    args.update(changes)
    return adjudication.read_adjudicated_field(path, **args)


def test_selected_field_keeps_original_conflict_candidates_and_time_boundary(tmp_path):
    args, _, original, _ = _fixture(tmp_path)
    before = datetime.now(UTC)
    path, asset = _build(tmp_path, args)
    assert before <= datetime.fromisoformat(asset["created_at"]) <= datetime.now(UTC)
    result = _read(path)
    assert result["status"] == "available"
    assert result["value"] == 9007199254740993
    assert result["event"]["common_values"] is None
    assert result["event"]["candidates"] == original["reports"][0]["events"][0]["candidates"]
    assert result["event"]["blocks_fallback"] is True
    assert result["whole_row_selector_certified"] is False
    boundary = datetime.fromisoformat(result["usable_at"])
    hidden = _read(path, as_of=(boundary - timedelta(microseconds=1)).isoformat())
    assert hidden["status"] == "not_yet_available"
    assert "event" not in hidden and "evidence" not in hidden and hidden["value"] is None
    assert _read(path, as_of=boundary.isoformat())["status"] == "available"
    assert _read(path, field_key=(*KEY[:4], "rd_exp"))["status"] == "unadjudicated"
    with pytest.raises(ValueError, match="acceptance"):
        _read(path, accept_manual_field_adjudication=False)
    with pytest.raises(ValueError):
        read_research_event(
            path,
            expected_sha256=_sha(path),
            accept_partial_reconstructed=True,
            event_key=KEY[:4],
            as_of="2099-01-01T00:00:00Z",
        )


@pytest.mark.parametrize(
    "change",
    [
        "unknown_event",
        "unknown_field",
        "common_field",
        "duplicate",
        "value",
        "naive",
        "pages",
        "certification",
        "duplicate_ledger",
    ],
)
def test_invalid_selection_or_manual_evidence_is_rejected(tmp_path, change):
    args, entry, _, _ = _fixture(tmp_path)
    if change == "unknown_event":
        args["selections"] = (("income", "missing", *KEY[2:]),)
    elif change == "unknown_field":
        args["selections"] = ((*KEY[:4], "unknown"),)
    elif change == "common_field":
        args["selections"] = ((*KEY[:4], "rd_exp"),)
    elif change == "duplicate":
        args["selections"] = (KEY, KEY)
    elif change == "value":
        entry["fields"]["revenue"] = 0
    elif change == "naive":
        entry["verified_at"] = "2026-10-22"
    elif change == "pages":
        entry["pdf_pages"] = []
    elif change == "certification":
        entry["whole_row_selector_certified"] = True
    rows = [entry, entry] if change == "duplicate_ledger" else [entry]
    args["ledger"][0].write_text(json.dumps(rows))
    args["ledger"] = (args["ledger"][0], _sha(args["ledger"][0]))
    with pytest.raises(ValueError):
        _build(tmp_path, args)


@pytest.mark.parametrize("target", ["pdf", "ledger", "source"])
def test_changed_inputs_rejected_at_build_and_consumption(tmp_path, target):
    args, _, _, pdf = _fixture(tmp_path)
    path, _ = _build(tmp_path, args)
    changed = {"pdf": pdf, "ledger": args["ledger"][0], "source": args["source"][0]}[target]
    changed.write_bytes(changed.read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum"):
        _read(path)
    with pytest.raises(ValueError, match="checksum"):
        _build(tmp_path, args)


def test_missing_pdf_rejected_and_future_evidence_delays_field(tmp_path):
    args, entry, _, pdf = _fixture(tmp_path)
    entry["verified_at"] = "2090-01-01T00:00:00+00:00"
    args["ledger"][0].write_text(json.dumps([entry]))
    args["ledger"] = (args["ledger"][0], _sha(args["ledger"][0]))
    path, _ = _build(tmp_path, args)
    assert _read(path, as_of="2089-12-31T23:59:59Z")["status"] == "not_yet_available"
    pdf.unlink()
    with pytest.raises(ValueError, match="missing"):
        _read(path)


def test_one_field_does_not_release_other_conflicts_and_null_never_means_zero(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quant_market_data_platform.statement_research_asset import build_research_asset

    args, entry, _, _ = _fixture(tmp_path)
    parquet = tmp_path / "source.parquet"
    rows = pq.read_table(parquet).to_pylist()
    rows[1]["rd_exp"] = 100
    pq.write_table(pa.Table.from_pylist(rows), parquet)
    original = build_research_asset(
        sources=[
            {
                "dataset": "income",
                "source": parquet,
                "expected_sha256": _sha(parquet),
                "fields": ("revenue", "rd_exp"),
                "available_delay_days": 1,
            }
        ],
        code_revision="a" * 40,
    )
    args["source"][0].write_text(json.dumps(original))
    args["source"] = (args["source"][0], _sha(args["source"][0]))
    path, _ = _build(tmp_path, args)
    assert _read(path)["value"] == 9007199254740993
    assert _read(path)["event"]["differing_fields"] == ["revenue", "rd_exp"]
    assert _read(path, field_key=(*KEY[:4], "rd_exp"))["status"] == "unadjudicated"
    args["selections"] = ((*KEY[:4], "rd_exp"),)
    path, _ = _build(tmp_path, args)
    result = _read(path, field_key=(*KEY[:4], "rd_exp"))
    assert result["status"] == "available" and result["value"] is None
    entry["fields"]["rd_exp"] = 0
    args["ledger"][0].write_text(json.dumps([entry]))
    args["ledger"] = (args["ledger"][0], _sha(args["ledger"][0]))
    with pytest.raises(ValueError, match="candidate"):
        _build(tmp_path, args)


def test_ledger_metadata_is_not_a_projected_financial_field(tmp_path):
    args, entry, _, _ = _fixture(tmp_path)
    entry["fields"]["comp_type"] = "1"
    args["ledger"][0].write_text(json.dumps([entry]))
    args["ledger"] = (args["ledger"][0], _sha(args["ledger"][0]))
    with pytest.raises(ValueError, match="field"):
        _build(tmp_path, args)
