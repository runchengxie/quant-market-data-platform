"""Completed acquisition remains observable without weakening evidence integrity."""

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_market_data_platform import _minute_history_inventory
from quant_market_data_platform.minute_history_status import history_status, watch_history

NOW = datetime(2026, 10, 23, 7, tzinfo=UTC)


def put(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


@pytest.fixture
def completed(tmp_path, monkeypatch):
    root, runtime = tmp_path / "data", tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setattr(
        _minute_history_inventory, "_current_publication", lambda *_: {"date_min": "20160105"}
    )
    calendar = root / "assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet"
    calendar.parent.mkdir(parents=True)
    pq.write_table(pa.table({"cal_date": ["20160104", "20160105"], "is_open": [1, 1]}), calendar)
    put(
        root
        / (
            "staging/tushare_minute_backfill_reverse/runs/run/data/"
            "trade_date=20160104/_minute_mirror.json"
        ),
        {"status": "complete", "trade_date": "20160104"},
    )
    queue = root / "metadata/minute_backfill/reverse_scheduler/deferred_missing.json"
    put(queue, {"dates": {}})
    put(
        runtime / "runtime-preflight.json",
        {
            "generated_at": (NOW - timedelta(hours=13)).isoformat(),
            "quota_date": "20261022",
            "available_requests": 100,
            "free_disk_bytes": 10**15,
        },
    )
    marker = runtime / "download-complete.json"
    put(
        marker,
        {
            "status": "first_pass_complete_with_gaps",
            "result": {
                "status": "download_pass_complete_with_gaps",
                "deferred_dates": 0,
                "deferred_queue": str(queue),
                "semantic_quarantine_dates": [],
                "quality_pending": True,
            },
        },
    )
    os.utime(marker, (NOW.timestamp() - 3600, NOW.timestamp() - 3600))
    db = root / "metadata/tushare/minute_quota/minute_quota.sqlite3"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE request_attempts (quota_date TEXT, consumer TEXT, state TEXT, "
            "committed_rows INTEGER, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO request_attempts VALUES (?,?,?,?,?)",
            (
                "20261022",
                "reverse_backfill",
                "committed",
                241,
                (NOW - timedelta(hours=14)).isoformat(),
            ),
        )
    return root, runtime, db


def test_completed_pass_does_not_need_live_preflight_or_writer(completed, tmp_path):
    root, runtime, db = completed
    before = db.read_bytes()
    result = watch_history(root, runtime, tmp_path / "monitor", now=NOW)
    assert result["state"] == "download_pass_complete_with_gaps"
    assert result["progress"]["remaining_open_dates"] == 0
    assert result["pipeline"]["coverage"]["full_market_ready"] is False
    assert result["writer_active"] is False
    assert result["download_completion"] == "first_pass_complete_with_gaps"
    assert result["active_alert"] is None
    assert db.read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing_ledger",
        "invalid_ledger",
        "future_activity",
        "future_preflight",
        "future_quota",
        "future_marker",
        "missing_queue",
        "queue_mismatch",
        "open_dates",
        "invalid_marker",
    ],
)
def test_completion_cannot_mask_missing_invalid_or_future_evidence(completed, fault):
    root, runtime, db = completed
    if fault == "missing_ledger":
        db.unlink()
    elif fault == "invalid_ledger":
        db.write_bytes(b"not sqlite")
    elif fault == "future_activity":
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO request_attempts VALUES (?,?,?,?,?)",
                (
                    "20261023",
                    "reverse_backfill",
                    "committed",
                    1,
                    (NOW + timedelta(minutes=5)).isoformat(),
                ),
            )
    elif fault in {"future_preflight", "future_quota"}:
        p = runtime / "runtime-preflight.json"
        x = json.loads(p.read_text())
        x["generated_at" if fault == "future_preflight" else "quota_date"] = (
            (NOW + timedelta(minutes=5)).isoformat() if fault == "future_preflight" else "20261024"
        )
        put(p, x)
    elif fault == "future_marker":
        os.utime(runtime / "download-complete.json", (NOW.timestamp() + 300, NOW.timestamp() + 300))
    elif fault == "missing_queue":
        (root / "metadata/minute_backfill/reverse_scheduler/deferred_missing.json").unlink()
    elif fault == "queue_mismatch":
        put(
            root / "metadata/minute_backfill/reverse_scheduler/deferred_missing.json",
            {"dates": {"20160104": {}}},
        )
    elif fault == "open_dates":
        (
            root
            / (
                "staging/tushare_minute_backfill_reverse/runs/run/data/"
                "trade_date=20160104/_minute_mirror.json"
            )
        ).unlink()
    else:
        put(
            runtime / "download-complete.json",
            {"status": "first_pass_complete_with_gaps", "result": {"status": "complete"}},
        )
    assert history_status(root, runtime, now=NOW)["state"] == "unknown_stale_evidence"


def test_completion_does_not_bypass_unresolved_audit_block(completed):
    root, runtime, _ = completed
    put(runtime / "semantic-audit-blocked.json", {"acceptance": "unresolved"})
    assert history_status(root, runtime, now=NOW)["state"] == "audit_blocked"


def test_completed_pass_keeps_semantic_quarantine_alert(completed, tmp_path, monkeypatch):
    from quant_market_data_platform import minute_semantic_quarantine

    root, runtime, _ = completed
    monkeypatch.setattr(
        minute_semantic_quarantine,
        "quarantine_status",
        lambda *_: {
            "blocked": False,
            "dates": ["20190509"],
            "errors": [],
            "marker_quarantined": True,
        },
    )
    path = runtime / "download-complete.json"
    marker = json.loads(path.read_text())
    marker["result"]["semantic_quarantine_dates"] = ["20190509"]
    put(path, marker)
    os.utime(path, (NOW.timestamp() - 3600, NOW.timestamp() - 3600))
    result = watch_history(root, runtime, tmp_path / "monitor", now=NOW)
    assert result["state"] == "download_pass_complete_with_gaps"
    assert result["active_alert"] == "semantic_quarantine:20190509"
    assert result["pipeline"]["coverage"]["full_market_ready"] is False


def test_completed_pass_keeps_storage_protection(completed, monkeypatch):
    from types import SimpleNamespace

    from quant_market_data_platform import minute_history_status

    root, runtime, _ = completed
    monkeypatch.setattr(
        minute_history_status.shutil, "disk_usage", lambda _: SimpleNamespace(free=0)
    )
    assert history_status(root, runtime, now=NOW)["state"] == "storage_wait"
