"""Company templates must not be confused with consolidation scope."""

import pytest

from quant_market_data_platform.providers.tushare_a_share_fundamentals_part02 import (
    _assert_unambiguous_normalized_rows,
)
from quant_market_data_platform.providers.tushare_a_share_fundamentals_part03 import (
    _filter_statement_company_types,
)
from quant_market_data_platform.providers.tushare_a_share_fundamentals_support import (
    FieldValidationError,
)


@pytest.mark.parametrize("dataset", ["income", "balancesheet", "cashflow"])
def test_templates_survive_report_filter_index_gaps(dataset):
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "600000.SH", "600001.SH", "600002.SH", "600003.SH"],
            "comp_type": ["2", "1", "3", "4", "7"],
            "end_date": ["20260630"] * 5,
            "report_type": ["1"] * 5,
        },
        index=[1, 4, 7, 10, 13],
    )
    dropped = {}
    actual = _filter_statement_company_types(dataset, frame, dropped)
    pd.testing.assert_frame_equal(actual, frame)
    assert dropped["unsupported_comp_type"] == 0


def test_different_company_templates_are_not_consolidation_priority():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 2,
            "comp_type": ["1", "2"],
            "end_date": ["20260630"] * 2,
            "report_type": ["1"] * 2,
        }
    )
    actual = _filter_statement_company_types("income", frame, {})
    pd.testing.assert_frame_equal(actual, frame)
    with pytest.raises(FieldValidationError, match="ambiguous same-day revision"):
        _assert_unambiguous_normalized_rows(actual, ["ts_code", "end_date", "report_type"])


def test_unknown_template_is_rejected_without_dropping_other_rows():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"comp_type": ["1", "2", "7", "9", None]}, index=[3, 6, 9, 12, 15])
    dropped = {}
    actual = _filter_statement_company_types("income", frame, dropped)
    pd.testing.assert_frame_equal(actual, frame.drop(index=12))
    assert dropped == {"unsupported_comp_type": 1}


def test_diagnostic_quarantines_all_conflicts_and_preserves_observation(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    pd = pytest.importorskip("pandas")
    script = (
        Path(__file__).resolve().parents[1] / "scripts/operations/audit_fundamentals_templates.py"
    )
    spec = importlib.util.spec_from_file_location("template_audit", script)
    assert spec is not None and spec.loader is not None
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    observed = "2026-10-17T18:48:25+00:00"
    base = {
        "ann_date": "20260421",
        "f_ann_date": "20260421",
        "end_date": "20260331",
        "report_type": "1",
        "update_flag": "1",
        "_source_retrieved_at": observed,
    }
    frame = pd.DataFrame(
        [
            base | {"ts_code": "603093.SH", "comp_type": "2", "value": 100},
            base | {"ts_code": "603093.SH", "comp_type": "7", "value": 101},
            base | {"ts_code": "000001.SZ", "comp_type": "2", "value": 200},
        ]
    )
    monkeypatch.setattr(audit, "asset_manifest_payload", lambda _: {})
    monkeypatch.setattr(audit, "file_sha256", lambda _: "fixture-hash")
    monkeypatch.setattr(audit, "_revision_safe_raw_inputs", lambda *_: ({}, {}, [frame.copy()]))
    output = tmp_path / "diagnostic"
    receipt = audit.run(tmp_path / "source", output)
    assert receipt["published_asset"] is False
    assert receipt["historical_pit_certified"] is False
    for name, details in receipt["datasets"].items():
        assert details["quarantined_rows"] == 2
        assert details["diagnostic_rows"] == 1
        conflict = details["quarantined_keys"][0]
        assert conflict["templates"] == ["2", "7"]
        assert conflict["different_columns"] == ["comp_type", "value"]
        kept = pd.read_parquet(output / f"{name}_diagnostic.parquet")
        assert kept.ts_code.tolist() == ["000001.SZ"]
        assert kept._source_retrieved_at.tolist() == [observed]
        assert len(pd.read_parquet(output / f"{name}_quarantined_rows.parquet")) == 2
    with pytest.raises(ValueError, match="new directory"):
        audit.run(tmp_path / "source", output)


def test_diagnostic_accepts_failed_vintage_dataset_subset(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    pd = pytest.importorskip("pandas")
    script = (
        Path(__file__).resolve().parents[1] / "scripts/operations/audit_fundamentals_templates.py"
    )
    spec = importlib.util.spec_from_file_location("failed_template_audit", script)
    assert spec is not None and spec.loader is not None
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    vintage = tmp_path / "vintage"
    raw = vintage / "raw" / "income"
    raw.mkdir(parents=True)
    (raw / "manifest.yml").write_text("fixture: raw\n")
    observed = "2026-10-21T01:02:03+00:00"
    base = {
        "ts_code": "603093.SH",
        "ann_date": "20260421",
        "f_ann_date": "20260421",
        "end_date": "20260331",
        "report_type": "1",
        "update_flag": "1",
        "_source_retrieved_at": observed,
    }
    frame = pd.DataFrame(
        [base | {"comp_type": "2", "value": 100}, base | {"comp_type": "7", "value": 101}]
    )
    monkeypatch.setattr(audit, "asset_manifest_payload", lambda _: {})
    monkeypatch.setattr(
        audit, "_revision_safe_raw_inputs", lambda *_: ({}, {"observed": observed}, [frame.copy()])
    )
    output = tmp_path / "diagnostic"
    receipt = audit.run(vintage, output, datasets=("income",))
    assert set(receipt["datasets"]) == {"income"}
    details = receipt["datasets"]["income"]
    assert details["old_normalized_manifest_sha256"] is None
    assert details["raw_manifest_sha256"] == audit.file_sha256(raw / "manifest.yml")
    assert details["source_observation"] == {"observed": observed}
    pd.testing.assert_frame_equal(
        pd.read_parquet(output / "income_quarantined_rows.parquet")[frame.columns], frame
    )
    assert details["quarantined_rows"] == 2
    assert receipt["new_api_requests"] == 0
    assert receipt["published_asset"] is False
    assert receipt["historical_pit_certified"] is False
    assert not list(tmp_path.rglob("SEALED.json"))
    assert not (output / "manifest.yml").exists()
    assert not (vintage / "pit").exists()
    with pytest.raises(ValueError, match="new directory"):
        audit.run(vintage, output, datasets=("income",))
    with pytest.raises(ValueError, match="dataset"):
        audit.run(vintage, tmp_path / "invalid", datasets=("unknown",))
    assert not (tmp_path / "invalid").exists()
