"""Completed acquisition has durable evidence, without certifying data quality."""

import os
import sqlite3
from datetime import timedelta

import pytest
from test_minute_history_status import NOW, fixture, put

from quant_market_data_platform import minute_history_status as monitor


def completed(tmp_path, monkeypatch):
    root, runtime = fixture(tmp_path, quota_date="20261018")
    original = monitor.inventory

    def inventory(*args):
        return {
            **original(*args),
            "settled_dates": 1,
            "remaining_open_dates": 0,
            "deferred_dates": 0,
        }

    monkeypatch.setattr(monitor, "inventory", inventory)
    put(
        runtime / "download-complete.json",
        {
            "status": "first_pass_complete_with_gaps",
            "result": {
                "status": "download_pass_complete_with_gaps",
                "reason": "reverse_history_is_complete_or_exhausted",
                "deferred_dates": 0,
                "semantic_quarantine_dates": [],
                "quality_pending": True,
            },
        },
    )
    # Terminal classification still requires a readable ledger and a real canonical queue.
    db = root / "metadata/tushare/minute_quota/minute_quota.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE request_attempts (quota_date TEXT, consumer TEXT, state TEXT, "
            "committed_rows INTEGER, updated_at TEXT)"
        )
    put(root / "metadata/minute_backfill/reverse_scheduler/deferred_missing.json", {"dates": {}})
    os.utime(runtime / "download-complete.json", (NOW.timestamp(), NOW.timestamp()))
    return root, runtime


def test_completed_acquisition_survives_preflight_expiry_and_day_rollover(tmp_path, monkeypatch):
    root, runtime = completed(tmp_path, monkeypatch)
    result = monitor.history_status(root, runtime, now=NOW + timedelta(days=2))
    assert result["state"] == "download_complete_with_gaps"
    assert result["provenance"]["fresh"] is False
    assert result["pipeline"]["coverage"]["full_market_ready"] is False
    assert result["pipeline"]["semantic_audit"]["audited_dates"] == 0


@pytest.mark.parametrize(
    "change",
    ["missing_marker", "bad_marker", "open_dates", "unknown_queue", "active", "warning"],
)
def test_completion_does_not_hide_unexpected_staleness(tmp_path, monkeypatch, change):
    root, runtime = completed(tmp_path, monkeypatch)
    if change == "missing_marker":
        (runtime / "download-complete.json").unlink()
    elif change == "bad_marker":
        put(runtime / "download-complete.json", {"status": "first_pass_complete_with_gaps"})
    elif change in {"open_dates", "unknown_queue"}:
        original = monitor.inventory
        field, value = (
            ("remaining_open_dates", 1) if change == "open_dates" else ("deferred_dates", None)
        )
        monkeypatch.setattr(monitor, "inventory", lambda *args: {**original(*args), field: value})
    elif change == "active":
        monkeypatch.setattr(monitor, "_writer_active", lambda *_: True)
    else:
        (runtime / "last-main-exit.json").write_text("[]")
    assert monitor.history_status(root, runtime, now=NOW)["state"] == "unknown_stale_evidence"


def test_audit_block_still_overrides_completed_acquisition(tmp_path, monkeypatch):
    root, runtime = completed(tmp_path, monkeypatch)
    put(runtime / "semantic-audit-blocked.json", {"acceptance": "/audit/report.json"})
    assert monitor.history_status(root, runtime, now=NOW)["state"] == "audit_blocked"


def test_watch_keeps_quarantine_warning_after_resolving_staleness(tmp_path, monkeypatch):
    import json

    from quant_market_data_platform import minute_semantic_quarantine

    root, runtime = completed(tmp_path, monkeypatch)
    marker_path = runtime / "download-complete.json"
    marker = json.loads(marker_path.read_text())
    marker["result"]["semantic_quarantine_dates"] = ["20190509"]
    put(marker_path, marker)
    os.utime(marker_path, (NOW.timestamp(), NOW.timestamp()))
    monkeypatch.setattr(
        minute_semantic_quarantine,
        "quarantine_status",
        lambda *_: {
            "errors": [],
            "dates": ["20190509"],
            "marker_quarantined": True,
            "blocked": False,
        },
    )
    state = tmp_path / "monitor"
    put(state / "status.json", {"active_alert": "unknown_stale_evidence:expired"})
    result = monitor.watch_history(root, runtime, state, now=NOW)
    assert result["state"] == "download_complete_with_gaps"
    assert result["active_alert"] == "semantic_quarantine:20190509"
    events = [json.loads(line) for line in (state / "alerts.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["resolved", "opened"]
    assert events[-1]["severity"] == "warning"
