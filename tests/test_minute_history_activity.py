"""Live read-only quota activity must distinguish active batches from stale snapshots."""

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from quant_market_data_platform.minute_history_status import history_status

NOW = datetime(2026, 10, 21, 3, 35, tzinfo=UTC)
pytestmark = pytest.mark.usefixtures("history_disk_space")


def setup_activity(tmp_path, *, age=5, rows=241, day="20261021", consumer="reverse_backfill"):
    root, runtime = tmp_path / "data", tmp_path / "runtime"
    root.mkdir()
    runtime.mkdir()
    (runtime / "runtime-preflight.json").write_text(
        json.dumps(
            {
                "generated_at": (NOW - timedelta(minutes=40)).isoformat(),
                "quota_date": "20261021",
                "available_requests": 100,
                "free_disk_bytes": 10**15,
            }
        )
    )
    db = root / "metadata/tushare/minute_quota/minute_quota.sqlite3"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE request_attempts (quota_date TEXT, consumer TEXT, state TEXT, "
            "committed_rows INTEGER, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO request_attempts VALUES (?, ?, 'committed', ?, ?)",
            (day, consumer, rows, (NOW - timedelta(seconds=age)).isoformat()),
        )
    return root, runtime, db


@contextmanager
def held_writer(root):
    lock = root / "metadata/minute_backfill/reverse_scheduler/writer.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("wb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
        fcntl.flock(handle, fcntl.LOCK_UN)


def test_recent_real_rows_and_writer_keep_long_batch_running_without_refreshing_snapshot(tmp_path):
    root, runtime, db = setup_activity(tmp_path)
    before = db.read_bytes()
    with held_writer(root):
        result = history_status(root, runtime, now=NOW)
    assert result["state"] == "running"
    assert result["provenance"]["fresh"] is False
    assert result["activity_provenance"]["fresh"] is True
    assert result["activity_provenance"]["age_seconds"] == 5
    assert result["progress"]["returned_rows_today"] == 241
    assert db.read_bytes() == before
    assert result["writes_production"] is False


def test_recent_rows_without_writer_do_not_hide_stale_preflight(tmp_path):
    root, runtime, _ = setup_activity(tmp_path)
    assert history_status(root, runtime, now=NOW)["state"] == "unknown_stale_evidence"


@pytest.mark.parametrize(
    "options",
    [
        {"age": 601},
        {"age": -61},
        {"rows": 0},
        {"day": "20261020"},
        {"consumer": "another_job"},
    ],
)
def test_unusable_activity_does_not_mask_missing_evidence(tmp_path, options):
    root, runtime, _ = setup_activity(tmp_path, **options)
    with held_writer(root):
        result = history_status(root, runtime, now=NOW)
    assert result["state"] == "unknown_stale_evidence"


def test_live_progress_does_not_bypass_invalid_quota_snapshot(tmp_path):
    root, runtime, _ = setup_activity(tmp_path)
    path = runtime / "runtime-preflight.json"
    snapshot = json.loads(path.read_text())
    snapshot["available_requests"] = "100"
    path.write_text(json.dumps(snapshot))
    with held_writer(root):
        result = history_status(root, runtime, now=NOW)
    assert result["state"] == "unknown_stale_evidence"


def test_live_progress_does_not_bypass_semantic_audit_block(tmp_path):
    root, runtime, _ = setup_activity(tmp_path)
    (runtime / "semantic-audit-blocked.json").write_text(json.dumps({"acceptance": "blocked"}))
    with held_writer(root):
        result = history_status(root, runtime, now=NOW)
    assert result["state"] == "audit_blocked"


def test_live_progress_still_observes_current_low_disk_space(tmp_path, history_disk_space):
    root, runtime, _ = setup_activity(tmp_path)
    history_disk_space.free = 0
    with held_writer(root):
        result = history_status(root, runtime, now=NOW)
    assert result["state"] == "storage_wait"
