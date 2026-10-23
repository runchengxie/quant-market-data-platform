"""Operational dates and incident status must remain distinct."""

import json
from datetime import UTC, datetime

import pytest

from quant_market_data_platform.operations_overview import OverviewOptions, build_overview

NOW = datetime(2026, 10, 23, 13, tzinfo=UTC)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def minute(root, day="20261023"):
    version = root / "assets/derived/a_share/minute_1m_tushare_v1_test"
    version.mkdir(parents=True)
    (version.parent / "minute_1m_tushare").symlink_to(version)
    write(
        version / "_operational_receipt.json",
        {
            "schema_version": "a_share.minute_tushare_operational_version.v1",
            "status": "published_operational_version",
            "output_dir": str(version),
            "quality_status": "tushare_native_baseline_required",
            "summary": {"date_max": day, "dates": 1, "rows": 241},
        },
    )


def test_current_receipt_does_not_claim_quality_or_erase_failed_service(tmp_path, monkeypatch):
    minute(tmp_path)
    monkeypatch.setattr(
        "quant_market_data_platform.operations_overview.service_states",
        lambda: {"daily": {"Result": "exit-code", "ExecMainStatus": "75"}},
    )
    result = build_overview(
        tmp_path, "20261023", now=NOW, options=OverviewOptions(with_systemd=True)
    )
    assert result["minute"]["status"] == "current"
    assert result["minute"]["quality_status"] == "tushare_native_baseline_required"
    assert result["services"]["daily"]["ExecMainStatus"] == "75"
    assert result["needs_attention"]


def test_missing_sources_are_unknown_not_success(tmp_path):
    result = build_overview(tmp_path, "20261023", now=NOW)
    assert result["minute"]["status"] == "missing"
    assert result["index_recovery"]["status"] == "no_samples"
    assert result["needs_attention"]


def test_lag_and_stale_history_remain_visible(tmp_path):
    minute(tmp_path, "20261022")
    write(
        tmp_path / "metadata/minute_backfill/reverse_scheduler/monitor/status.json",
        {
            "generated_at": "2026-10-22T00:00:00Z",
            "state": "download_complete_with_gaps",
            "progress": {"full_market_ready": False, "semantic_quarantine_dates": ["20190509"]},
        },
    )
    result = build_overview(tmp_path, "20261023", now=NOW)
    assert result["minute"]["status"] == "lagging"
    assert result["history"]["status"] == "stale"
    assert result["history"]["progress"]["semantic_quarantine_dates"] == ["20190509"]


def test_bad_future_or_naive_timestamps_are_invalid(tmp_path):
    p = tmp_path / "metadata/minute_backfill/reverse_scheduler/monitor/status.json"
    for stamp in ["2026-10-24T00:00:00Z", "2026-10-23T00:00:00"]:
        write(p, {"generated_at": stamp, "state": "running", "progress": {}})
        assert build_overview(tmp_path, "20261023", now=NOW)["history"]["status"] == "invalid"


def test_index_malformed_records_do_not_become_success(tmp_path):
    base = tmp_path / "reports/market-intel/index-recovery"
    write(base / "run/summary.json", {"schema_version": 1, "date": "20261023"})
    result = build_overview(tmp_path, "20261023", now=NOW)["index_recovery"]
    assert result["status"] == "invalid"
    assert result["invalid_records"] == 1


def test_supplemental_evidence_never_echoes_unselected_fields(tmp_path):
    p = tmp_path / "evidence.json"
    write(
        p,
        {
            "schema_version": "statement-partial-research.v1",
            "status": "partial_reconstructed",
            "conflict_event_count": 10,
            "secret": "must-not-appear",
        },
    )
    result = build_overview(
        tmp_path, "20261023", now=NOW, options=OverviewOptions(evidence={"fundamentals": p})
    )
    assert result["evidence"]["fundamentals"]["declared"]["conflict_event_count"] == 10
    assert "must-not-appear" not in json.dumps(result)
    assert result["evidence"]["fundamentals"]["status"] == "unverified"


@pytest.mark.parametrize("day", ["20260230", "2026-10-23", "nonsense"])
def test_invalid_target_rejected(tmp_path, day):
    with pytest.raises(ValueError):
        build_overview(tmp_path, day, now=NOW)


def test_index_missing_date_is_invalid_not_crash(tmp_path):
    write(
        tmp_path / "reports/market-intel/index-recovery/run/summary.json",
        {
            "schema_version": 1,
            "ended_utc": NOW.isoformat(),
            "outcome": "complete",
            "exit_code": 0,
            "final_missing_symbols": [],
        },
    )
    assert build_overview(tmp_path, "20261023", now=NOW)["index_recovery"]["status"] == "invalid"


def test_failed_history_cannot_override_gaps_with_ready_flag(tmp_path):
    write(
        tmp_path / "metadata/minute_backfill/reverse_scheduler/monitor/status.json",
        {
            "generated_at": NOW.isoformat(),
            "state": "failed",
            "progress": {
                "full_market_ready": True,
                "remaining_open_dates": 20,
                "semantic_quarantine_dates": ["20190509"],
            },
        },
    )
    assert build_overview(tmp_path, "20261023", now=NOW)["history"]["status"] != "reported_ready"


def test_future_minute_receipt_is_invalid(tmp_path):
    minute(tmp_path, "20990101")
    assert build_overview(tmp_path, "20261023", now=NOW)["minute"]["status"] == "invalid"


def test_large_receipt_is_rejected_before_json_decode(tmp_path, monkeypatch):
    minute(tmp_path)
    p = tmp_path / "assets/derived/a_share/minute_1m_tushare/_operational_receipt.json"
    p.write_text("x" * (8 * 1024 * 1024 + 1))
    assert build_overview(tmp_path, "20261023", now=NOW)["minute"]["status"] == "invalid"


def test_nested_supplemental_values_are_not_echoed(tmp_path):
    p = tmp_path / "extra.json"
    write(p, {"status": {"secret": "do-not-print"}})
    result = build_overview(
        tmp_path, "20261023", now=NOW, options=OverviewOptions(evidence={"extra": p})
    )
    assert "do-not-print" not in json.dumps(result)


def test_boolean_counts_do_not_report_ready(tmp_path):
    write(
        tmp_path / "metadata/minute_backfill/reverse_scheduler/monitor/status.json",
        {
            "generated_at": NOW.isoformat(),
            "state": "download_complete",
            "progress": {
                "full_market_ready": True,
                "remaining_open_dates": False,
                "deferred_dates": False,
                "semantic_quarantine_dates": [],
            },
        },
    )
    assert build_overview(tmp_path, "20261023", now=NOW)["history"]["status"] != "reported_ready"


def test_index_boolean_version_and_exit_code_are_invalid(tmp_path):
    write(
        tmp_path / "reports/market-intel/index-recovery/run/summary.json",
        {
            "schema_version": True,
            "exit_code": False,
            "outcome": "complete",
            "date": "20261023",
            "ended_utc": NOW.isoformat(),
            "final_missing_symbols": [],
        },
    )
    assert build_overview(tmp_path, "20261023", now=NOW)["index_recovery"]["status"] == "invalid"


def test_overflow_json_number_is_invalid_evidence(tmp_path):
    p = tmp_path / "overflow.json"
    p.write_text('{"event_count": 1e999}')
    result = build_overview(
        tmp_path, "20261023", now=NOW, options=OverviewOptions(evidence={"extra": p})
    )
    assert result["evidence"]["extra"]["status"] == "invalid"
    json.dumps(result, allow_nan=False)


def test_complete_index_record_is_observed_without_claiming_uptime(tmp_path):
    write(
        tmp_path / "reports/market-intel/index-recovery/run/summary.json",
        {
            "schema_version": 1,
            "exit_code": 0,
            "outcome": "complete",
            "date": "20261023",
            "ended_utc": NOW.isoformat(),
            "final_missing_symbols": [],
            "recovered_indices": ["HSI"],
            "confirmed_429": True,
            "duration_seconds": 2.5,
        },
    )
    result = build_overview(tmp_path, "20261023", now=NOW)["index_recovery"]
    assert result["status"] == "observed"
    assert result["runs_considered"] == result["complete_runs"] == 1
    assert result["scope"] == "latest_20_recorded_runs_not_uptime"


def test_copied_alias_must_match_version_receipt(tmp_path):
    minute(tmp_path)
    alias = tmp_path / "assets/derived/a_share/minute_1m_tushare"
    value = json.loads((alias / "_operational_receipt.json").read_text())
    alias.unlink()
    value["summary"]["rows"] = 242
    write(alias / "_operational_receipt.json", value)
    assert build_overview(tmp_path, "20261023", now=NOW)["minute"]["status"] == "invalid"


def test_contract_stale_date_and_current_data_are_both_visible(tmp_path):
    minute(tmp_path)
    alias = tmp_path / "assets/derived/a_share/minute_1m_tushare"
    write(
        tmp_path / "metadata/current_assets/a_share_current.json",
        {
            "assets": {
                "minute_1m_tushare": {
                    "alias_path": str(alias),
                    "resolved_path": str(alias.resolve()),
                    "availability": "available",
                    "as_of": "20261022",
                }
            },
        },
    )
    result = build_overview(tmp_path, "20261023", now=NOW)
    assert result["minute"]["status"] == "current"
    assert result["minute"]["contract_date_matches"] is False
    assert result["needs_attention"]


def test_summary_scan_is_bounded(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(Path, "glob", lambda *_: iter([tmp_path / str(i) for i in range(1001)]))
    result = build_overview(tmp_path, "20261023", now=NOW)["index_recovery"]
    assert result["status"] == "scan_limit_exceeded"
    assert result["runs_considered"] is None
