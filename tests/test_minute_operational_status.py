"""Read-only operational state and the actual daily journal integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_tushare_minute_operational import _installed_operational_alias, _trade_calendar

from quant_market_data_platform import tushare_minute_operational_daily as daily


def _options(root: Path, target: str = "20260710") -> daily.OperationalDailyOptions:
    _installed_operational_alias(root, date_max="20260710")
    calendar = root / "assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet"
    calendar.parent.mkdir(parents=True)
    _trade_calendar(calendar, ["20260710", "20260713"])
    return daily.OperationalDailyOptions(artifacts_root=root, target_date=target)


def _run_record(root: Path, name: str, status: str) -> None:
    path = root / f"metadata/minute_operational/history/{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "a_share.minute_operational_run.v1",
                "run_id": name,
                "started_at": f"2026-07-10T10:00:0{name}+00:00",
                "finished_at": f"2026-07-10T10:00:0{name}+00:00",
                "status": status,
                "target_date": "20260710",
                "attempts": [],
            }
        )
    )


def test_status_is_read_only_and_current_without_history(tmp_path):
    options = _options(tmp_path)
    result = daily.inspect_operational_daily(options)
    assert result["status"] == "current"
    assert result["date_max"] == result["target_date"] == "20260710"
    assert result["last_run"] is None
    assert not (tmp_path / "metadata/minute_operational/history").exists()
    assert not (tmp_path / "metadata/current_assets").exists()


def test_status_reports_missing_target_without_downloading(tmp_path, monkeypatch):
    options = _options(tmp_path, "20260713")
    monkeypatch.setattr(daily, "_run_daily_plan", lambda *a: pytest.fail("provider called"))
    result = daily.inspect_operational_daily(options)
    assert result["status"] == "behind"
    assert result["date_max"] == "20260710"
    assert result["target_date"] == "20260713"


def test_current_asset_does_not_erase_a_failed_invocation(tmp_path):
    options = _options(tmp_path)
    _run_record(tmp_path, "1", "download_incomplete")
    result = daily.inspect_operational_daily(options)
    assert result["status"] == "current_with_failed_run"
    assert result["last_failed_run"]["run_id"] == "1"


def test_later_success_demonstrates_recovery_without_erasing_failure(tmp_path):
    options = _options(tmp_path)
    _run_record(tmp_path, "1", "download_incomplete")
    _run_record(tmp_path, "2", "noop")
    result = daily.inspect_operational_daily(options)
    assert result["status"] == "recovered_after_failure"
    assert result["last_failed_run"]["run_id"] == "1"
    assert result["last_run"]["run_id"] == "2"


def test_invalid_history_fails_closed(tmp_path):
    options = _options(tmp_path)
    _run_record(tmp_path, "1", "failed")
    path = tmp_path / "metadata/minute_operational/history/2.json"
    path.write_text("not-json")
    with pytest.raises(ValueError, match="history"):
        daily.inspect_operational_daily(options)


def test_actual_noop_has_a_journal_but_preserves_return_contract(tmp_path):
    options = _options(tmp_path)
    payload = daily.run_operational_daily(options)
    assert payload == {
        "status": "noop",
        "reason": "operational_version_is_current",
        "date_max": "20260710",
    }
    records = list((tmp_path / "metadata/minute_operational/history").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["status"] == "noop" and record["finished_at"]
    assert record["result"]["date_max"] == "20260710"


def test_disabled_fallback_attempts_only_primary_and_preserves_failure(tmp_path, monkeypatch):
    original = _options(tmp_path, "20260713")
    options = daily.OperationalDailyOptions(
        artifacts_root=original.artifacts_root,
        target_date=original.target_date,
        fallback_token_env=None,
    )
    calls = []
    monkeypatch.setattr(daily, "_build_daily_plan", lambda *a, **k: {})

    def partial(attempt_options, paths, plan_path, receipt_path):
        calls.append(attempt_options.token_env)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps({"status": "partial", "segments": []}))
        return {"status": "partial"}

    monkeypatch.setattr(daily, "_run_daily_plan", partial)
    result = daily.run_operational_daily(options)
    assert result["status"] == "download_incomplete"
    assert calls == ["TUSHARE_TOKEN_2"]
    records = list((tmp_path / "metadata/minute_operational/history").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["fallback_enabled"] is False
    assert record["status"] == "download_incomplete"


def test_interrupt_is_retained_as_interrupted_not_completed_running(tmp_path):
    from quant_market_data_platform._minute_operational_history import (
        read_run_history,
        record_operational_run,
    )

    options = _options(tmp_path)

    @record_operational_run
    def interrupted(_options):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        interrupted(options)
    record = read_run_history(tmp_path)[0]
    assert record["status"] == "interrupted"
    assert record["finished_at"]
    assert daily.inspect_operational_daily(options)["status"] == "current_with_failed_run"


def test_overlapping_success_before_failure_finished_is_not_recovery(tmp_path):
    options = _options(tmp_path)
    _run_record(tmp_path, "1", "failed")
    _run_record(tmp_path, "2", "noop")
    history = tmp_path / "metadata/minute_operational/history"
    failed = json.loads((history / "1.json").read_text())
    failed.update(started_at="2026-07-10T10:00:00+00:00", finished_at="2026-07-10T10:10:00+00:00")
    (history / "1.json").write_text(json.dumps(failed))
    success = json.loads((history / "2.json").read_text())
    success.update(started_at="2026-07-10T10:05:00+00:00", finished_at="2026-07-10T10:06:00+00:00")
    (history / "2.json").write_text(json.dumps(success))
    assert daily.inspect_operational_daily(options)["status"] == "current_with_failed_run"


def test_unfinished_success_record_cannot_prove_recovery(tmp_path):
    options = _options(tmp_path)
    _run_record(tmp_path, "1", "failed")
    _run_record(tmp_path, "2", "noop")
    path = tmp_path / "metadata/minute_operational/history/2.json"
    payload = json.loads(path.read_text())
    payload["finished_at"] = None
    path.write_text(json.dumps(payload))
    assert daily.inspect_operational_daily(options)["status"] == "current_with_failed_run"


def test_automatic_target_survives_an_exception_before_a_return_payload(tmp_path, monkeypatch):
    from quant_market_data_platform._minute_operational_history import read_run_history

    original = _options(tmp_path)
    options = daily.OperationalDailyOptions(artifacts_root=original.artifacts_root)

    def fail_before_download(*args, **kwargs):
        raise RuntimeError("provider planning failed")

    monkeypatch.setattr(daily, "_build_daily_plan", fail_before_download)
    with pytest.raises(RuntimeError, match="planning failed"):
        daily.run_operational_daily(options)
    record = read_run_history(tmp_path)[0]
    assert record["status"] == "failed"
    assert record["target_date"] == "20260713"
