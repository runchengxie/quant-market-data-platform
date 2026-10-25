"""Read-only minute history state and local monitoring contracts."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quant_market_data_platform.minute_history_status import history_status, watch_history

NOW = datetime(2026, 10, 19, 1, tzinfo=UTC)
pytestmark = pytest.mark.usefixtures("history_disk_space")


def put(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def fixture(tmp_path: Path, **quota) -> tuple[Path, Path]:
    root, runtime = tmp_path / "data", tmp_path / "runtime"
    root.mkdir()
    put(
        runtime / "runtime-preflight.json",
        {
            "generated_at": NOW.isoformat(),
            "quota_date": "20261019",
            "available_requests": 100,
            "free_disk_bytes": 10**15,
            **quota,
        },
    )
    return root, runtime


def status(root, runtime, **kwargs):
    return history_status(root, runtime, now=NOW, **kwargs)


def test_audit_marker_overrides_quota_and_staleness(tmp_path):
    root, runtime = fixture(tmp_path, available_requests=0, quota_date="20261018")
    put(runtime / "semantic-audit-blocked.json", {"acceptance": "/audit/report.json"})
    result = status(root, runtime)
    assert result["state"] == "audit_blocked"
    assert "/audit/report.json" in result["reason"]
    assert result["writes_production"] is False


def test_quota_storage_stale_and_reset_states(tmp_path):
    root, runtime = fixture(tmp_path, available_requests=0)
    assert status(root, runtime)["state"] == "quota_wait"
    put(runtime / "runtime-preflight.json", {"quota_date": "20261018"})
    assert status(root, runtime)["state"] == "unknown_stale_evidence"
    fixture_path = runtime / "runtime-preflight.json"
    put(
        fixture_path,
        {
            "generated_at": NOW.isoformat(),
            "quota_date": "20261019",
            "available_requests": 1,
            "free_disk_bytes": 0,
        },
    )
    assert status(root, runtime)["state"] == "storage_wait"


def test_idle_requires_multiple_samples_and_elapsed_progress_window(tmp_path):
    root, runtime = fixture(tmp_path)
    first = status(root, runtime)
    assert first["state"] == "idle"
    second = status(root, runtime, previous=first)
    assert second["state"] == "idle"
    prior = {**second, "last_progress_at": (NOW - timedelta(seconds=1000)).isoformat()}
    assert status(root, runtime, previous=prior)["state"] == "idle"


def test_returned_rows_count_is_effective_progress(tmp_path):
    root, runtime = fixture(tmp_path)
    db = root / "metadata/tushare/minute_quota/minute_quota.sqlite3"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE request_attempts "
            "(quota_date TEXT, consumer TEXT, state TEXT, committed_rows INTEGER, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO request_attempts VALUES "
            "('20261019','reverse_backfill','committed',241,NULL)"
        )
    prior = {
        **status(root, runtime),
        "samples": 3,
        "last_progress_at": (NOW - timedelta(seconds=1000)).isoformat(),
    }
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE request_attempts SET committed_rows=482")
    result = status(root, runtime, previous=prior)
    assert result["state"] == "idle"
    assert result["progress"]["returned_rows_today"] == 482
    assert result["last_progress_at"] == NOW.isoformat()


def test_duplicate_dates_and_invalid_metadata(tmp_path):
    root, runtime = fixture(tmp_path)
    for run in ("old", "new"):
        put(
            root / f"staging/tushare_minute_backfill_reverse/runs/{run}/data/"
            "trade_date=20220101/_minute_mirror.json",
            {"status": "complete", "trade_date": "20220101"},
        )
    result = status(root, runtime)
    assert result["progress"]["metadata_complete_dates"] == 1
    assert result["progress"]["full_market_ready"] is False
    bad = (
        root / "staging/tushare_minute_backfill_reverse/runs/bad/data/"
        "trade_date=20220102/_minute_mirror.json"
    )
    bad.parent.mkdir(parents=True)
    bad.write_text("[]")
    result = status(root, runtime)
    assert result["evidence_warnings"]
    assert result["state"] == "unknown_stale_evidence"


def test_monitor_deduplicates_and_resolves_local_alerts(tmp_path):
    root, runtime = fixture(tmp_path)
    marker = runtime / "semantic-audit-blocked.json"
    put(marker, {"acceptance": "/audit/report.json"})
    state = tmp_path / "monitor"
    watch_history(root, runtime, state, now=NOW)
    watch_history(root, runtime, state, now=NOW)
    assert len((state / "alerts.jsonl").read_text().splitlines()) == 1
    marker.unlink()
    watch_history(root, runtime, state, now=NOW)
    alerts = [json.loads(line) for line in (state / "alerts.jsonl").read_text().splitlines()]
    assert alerts[-1]["event"] == "resolved"
    assert not (root / "metadata/minute_backfill/reverse_scheduler/writer.lock").exists()


def test_bj_and_audited_scopes_are_bound_to_matching_sidecar_versions(tmp_path):
    import hashlib

    root, runtime = fixture(tmp_path)
    sidecar = root / (
        "staging/tushare_minute_backfill_reverse/runs/old/data/"
        "trade_date=20220101/_minute_mirror.json"
    )
    put(sidecar, {"status": "partial", "trade_date": "20220101"})
    record = {
        "sidecar": str(sidecar),
        "trade_date": "20220101",
        "sidecar_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
    }
    metadata = root / "metadata/minute_backfill/reverse_scheduler"
    put(metadata / "bj_missing_report.json", {"partitions": [record, record]})
    put(
        metadata / "independent-audit.json",
        {
            "partitions": [
                {
                    **record,
                    "binding_matches": True,
                    "issues": [],
                    "status": "passed_requested_universe_only",
                }
            ]
        },
    )
    result = status(root, runtime)
    assert result["progress"]["metadata_complete_dates"] == 0
    assert result["progress"]["accepted_bj_omission_dates"] == 1
    assert result["progress"]["independently_audited_dates"] == 1
    put(sidecar, {"status": "partial", "trade_date": "20220101", "changed": True})
    result = status(root, runtime)
    assert result["progress"]["accepted_bj_omission_dates"] == 0
    assert result["progress"]["independently_audited_dates"] == 0
    assert result["evidence_warnings"]


def test_recent_exit_and_planned_restart(tmp_path):
    root, runtime = fixture(tmp_path)
    put(runtime / "last-main-exit.json", {"service_result": "exit-code", "at": NOW.isoformat()})
    assert status(root, runtime)["state"] == "cooldown"
    put(
        runtime / "last-main-exit.json",
        {"service_result": "signal", "at": NOW.isoformat(), "planned_restart": True},
    )
    assert status(root, runtime)["state"] == "idle"


def test_file_mtime_freshness_and_shanghai_midnight(tmp_path):
    import os

    root, runtime = fixture(tmp_path)
    preflight = runtime / "runtime-preflight.json"
    put(preflight, {"quota_date": "20261019", "available_requests": 100})
    os.utime(preflight, (NOW.timestamp(), NOW.timestamp()))
    assert status(root, runtime, min_free_bytes=0)["state"] == "idle"
    os.utime(preflight, (NOW.timestamp() - 601, NOW.timestamp() - 601))
    assert status(root, runtime)["state"] == "unknown_stale_evidence"
    put(preflight, {"quota_date": "20261018", "generated_at": NOW.isoformat()})
    assert status(root, runtime)["state"] == "unknown_stale_evidence"


def test_malformed_inventory_collections_are_unknown_not_crashes(tmp_path):
    root, runtime = fixture(tmp_path)
    metadata = root / "metadata/minute_backfill/reverse_scheduler"
    put(metadata / "bj_missing_report.json", {"partitions": None})
    result = status(root, runtime)
    assert result["state"] == "unknown_stale_evidence"
    assert result["evidence_warnings"]


def test_missing_quota_availability_is_unknown(tmp_path):
    root, runtime = fixture(tmp_path)
    put(
        runtime / "runtime-preflight.json",
        {"generated_at": NOW.isoformat(), "quota_date": "20261019", "free_disk_bytes": 10**15},
    )
    assert status(root, runtime)["state"] == "unknown_stale_evidence"


def test_real_runtime_acceptance_binds_zero_issue_mapping(tmp_path):
    import hashlib

    root, runtime = fixture(tmp_path)
    sidecar = root / (
        "staging/tushare_minute_backfill_reverse/runs/old/data/"
        "trade_date=20220101/_minute_mirror.json"
    )
    put(sidecar, {"status": "complete", "trade_date": "20220101"})
    record = {
        "sidecar": str(sidecar),
        "trade_date": "20220101",
        "path": "/minute.parquet",
        "sidecar_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
        "binding_matches": True,
        "issues": {"duplicate_rows": 0, "null_rows": 0},
        "status": "passed_requested_universe_only",
    }
    put(runtime / "batch-acceptance/one/acceptance.json", {"partitions": [record]})
    assert status(root, runtime)["progress"]["independently_audited_dates"] == 1
    put(
        runtime / "batch-acceptance/one/acceptance.json",
        {"partitions": [{**record, "sidecar_sha256": "invalid"}]},
    )
    result = status(root, runtime)
    assert result["progress"]["independently_audited_dates"] == 0
    assert result["progress"]["expired_audit_bindings"] == 1


def test_preflight_audit_flag_has_priority(tmp_path):
    root, runtime = fixture(tmp_path, blocked_by_audit=True, available_requests=0)
    assert status(root, runtime)["state"] == "audit_blocked"


def test_malformed_previous_snapshot_is_unknown(tmp_path):
    root, runtime = fixture(tmp_path)
    result = status(root, runtime, previous={"progress": None, "samples": "bad"})
    assert result["state"] == "unknown_stale_evidence"
    assert result["evidence_warnings"]


def test_wait_to_running_starts_fresh_stall_window(tmp_path):
    import fcntl

    root, runtime = fixture(tmp_path)
    lock = root / "metadata/minute_backfill/reverse_scheduler/writer.lock"
    lock.parent.mkdir(parents=True)
    previous = {
        **status(root, runtime),
        "state": "quota_wait",
        "samples": 9,
        "last_progress_at": (NOW - timedelta(seconds=5000)).isoformat(),
    }
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        result = status(root, runtime, previous=previous)
        assert result["state"] == "running"
        assert result["runnable_samples"] == 1
        prior = {
            **result,
            "runnable_samples": 2,
            "runnable_since": (NOW - timedelta(seconds=1000)).isoformat(),
        }
        assert status(root, runtime, previous=prior)["state"] == "stalled"


def test_backwards_counts_do_not_advance_last_progress(tmp_path):
    root, runtime = fixture(tmp_path)
    previous = status(root, runtime)
    old = (NOW - timedelta(seconds=100)).isoformat()
    previous["last_progress_at"] = old
    previous["progress"]["settled_dates"] = 2
    result = status(root, runtime, previous=previous)
    assert result["last_progress_at"] == old
    assert result["evidence_warnings"]


def test_watch_preserves_malformed_snapshot_warning(tmp_path):
    root, runtime = fixture(tmp_path)
    state = tmp_path / "monitor"
    state.mkdir()
    (state / "status.json").write_text("invalid")
    result = watch_history(root, runtime, state, now=NOW)
    assert result["state"] == "unknown_stale_evidence"


def test_failed_or_scopeless_audits_never_count(tmp_path):
    import hashlib

    root, runtime = fixture(tmp_path)
    sidecar = root / (
        "staging/tushare_minute_backfill_reverse/runs/old/data/"
        "trade_date=20220101/_minute_mirror.json"
    )
    put(sidecar, {"status": "complete", "trade_date": "20220101"})
    record = {
        "sidecar": str(sidecar),
        "sidecar_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
        "binding_matches": True,
        "issues": {"null_rows": 0},
    }
    put(
        runtime / "batch-acceptance/a/acceptance.json",
        {"partitions": [record, {**record, "status": "failed_semantics"}]},
    )
    result = status(root, runtime)
    assert result["progress"]["independently_audited_dates"] == 0
    assert not any(result["progress"]["audit_scope_dates"].values())


def test_current_receipt_validation_and_missing_calendar(tmp_path):
    root, runtime = fixture(tmp_path)
    alias = root / "assets/derived/a_share/minute_1m_tushare"
    put(
        alias / "_operational_receipt.json",
        {
            "output_dir": str(alias),
            "schema_version": "a_share.minute_tushare_operational_version.v1",
            "status": "published_operational_version",
            "summary": {"date_min": "20220101"},
        },
    )
    result = status(root, runtime)
    assert result["progress"]["current_min_date"] == "20220101"
    assert result["progress"]["remaining_open_dates"] is None
    put(alias / "_operational_receipt.json", {"summary": {"date_min": "20220101"}})
    result = status(root, runtime)
    assert result["progress"]["current_min_date"] is None
    assert result["evidence_warnings"]


def test_quota_day_reset_does_not_count_progress(tmp_path):
    root, runtime = fixture(tmp_path)
    prior = status(root, runtime)
    old = (NOW - timedelta(seconds=1000)).isoformat()
    prior["last_progress_at"] = old
    prior["quota"]["quota_date"] = "20261018"
    prior["progress"]["returned_rows_today"] = 1000
    result = status(root, runtime, previous=prior)
    assert result["last_progress_at"] == old
    assert not result["evidence_warnings"]


def test_expiring_previous_audit_is_informational_not_global_unknown(tmp_path):
    import hashlib

    root, runtime = fixture(tmp_path)
    sidecar = root / (
        "staging/tushare_minute_backfill_reverse/runs/old/data/"
        "trade_date=20220101/_minute_mirror.json"
    )
    put(sidecar, {"status": "complete", "trade_date": "20220101"})
    record = {
        "sidecar": str(sidecar),
        "sidecar_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
        "binding_matches": True,
        "issues": {"null_rows": 0},
        "status": "passed_requested_universe_only",
    }
    put(runtime / "batch-acceptance/a/acceptance.json", {"partitions": [record]})
    prior = status(root, runtime)
    old = (NOW - timedelta(seconds=1000)).isoformat()
    prior["last_progress_at"] = old
    put(sidecar, {"status": "complete", "trade_date": "20220101", "changed": True})
    result = status(root, runtime, previous=prior)
    assert result["state"] == "idle"
    assert result["progress"]["independently_audited_dates"] == 0
    assert result["progress"]["expired_audit_bindings"] == 1
    assert result["last_progress_at"] == old
    assert not result["evidence_warnings"]


def test_unhashable_audit_status_is_unknown_not_crash(tmp_path):
    import hashlib

    root, runtime = fixture(tmp_path)
    sidecar = root / (
        "staging/tushare_minute_backfill_reverse/runs/old/data/"
        "trade_date=20220101/_minute_mirror.json"
    )
    put(sidecar, {"status": "complete", "trade_date": "20220101"})
    record = {
        "sidecar": str(sidecar),
        "sidecar_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
        "binding_matches": True,
        "issues": {"null_rows": 0},
        "status": [],
    }
    put(runtime / "batch-acceptance/a/acceptance.json", {"partitions": [record]})
    result = status(root, runtime)
    assert result["state"] == "unknown_stale_evidence"
    assert result["evidence_warnings"]


def test_boolean_numeric_preflight_and_nonboolean_block_flag_are_invalid(tmp_path):
    root, runtime = fixture(tmp_path)
    for field, value in [
        ("available_requests", True),
        ("free_disk_bytes", True),
        ("blocked_by_audit", "false"),
    ]:
        put(
            runtime / "runtime-preflight.json",
            {
                "generated_at": NOW.isoformat(),
                "quota_date": "20261019",
                "available_requests": 1,
                "free_disk_bytes": 10**15,
                field: value,
            },
        )
        result = status(root, runtime)
        assert result["state"] == "unknown_stale_evidence"
        assert result["evidence_warnings"]
    put(runtime / "semantic-audit-blocked.json", {"acceptance": "/audit/report.json"})
    assert status(root, runtime)["state"] == "audit_blocked"


def test_expired_audit_records_count_unique_unresolved_dates_without_erasing_history(tmp_path):
    import hashlib

    root, runtime = fixture(tmp_path)
    records = []
    for date in ("20220101", "20220102"):
        sidecar = root / (
            f"staging/tushare_minute_backfill_reverse/runs/old/data/"
            f"trade_date={date}/_minute_mirror.json"
        )
        put(sidecar, {"status": "partial", "trade_date": date})
        records.append(
            {
                "sidecar": str(sidecar),
                "trade_date": date,
                "sidecar_sha256": "0" * 64,
                "status": "passed_requested_universe_only",
                "binding_matches": True,
                "issues": [],
            }
        )
    expired = runtime / "batch-acceptance/old/acceptance.json"
    put(expired, {"partitions": [records[0], records[0], records[1]]})
    old_bytes = expired.read_bytes()
    current = {
        **records[0],
        "sidecar_sha256": hashlib.sha256(Path(str(records[0]["sidecar"])).read_bytes()).hexdigest(),
    }
    put(runtime / "batch-acceptance/new/acceptance.json", {"partitions": [current]})
    progress = status(root, runtime)["progress"]
    assert progress["expired_audit_bindings"] == 3
    assert progress["expired_audit_dates"] == 2
    assert progress["superseded_expired_audit_dates"] == 1
    assert progress["unresolved_expired_audit_dates"] == 1
    assert progress["independently_audited_dates"] == 1
    assert expired.read_bytes() == old_bytes
    current["issues"] = {"duplicate_rows": 1}
    put(runtime / "batch-acceptance/new/acceptance.json", {"partitions": [current]})
    progress = status(root, runtime)["progress"]
    assert progress["superseded_expired_audit_dates"] == 0
    assert progress["unresolved_expired_audit_dates"] == 2
