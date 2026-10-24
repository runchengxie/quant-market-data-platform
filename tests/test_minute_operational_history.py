"""Operational invocation evidence survives mutable download checkpoints."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

from quant_market_data_platform._minute_operational_history import (
    read_run_history,
    record_backfill_attempt,
    record_operational_run,
)


@dataclass(frozen=True)
class Options:
    artifacts_root: Path
    target_date: str | None = "20261023"
    fallback_token_env: str | None = "TUSHARE_TOKEN"


def _receipt(root: Path, *, status: str) -> Path:
    data = root / "staging/data"
    partition = data / "trade_date=20261023"
    partition.mkdir(parents=True, exist_ok=True)
    (partition / "_minute_mirror.json").write_text(
        json.dumps(
            {
                "status": status,
                "trade_date": "20261023",
                "missing_request_symbols": ["000001.SZ"],
                "error": {
                    "type": "Exception",
                    "message": "频率超限(1次/小时) https://private.example/secret-token",
                },
            }
        )
    )
    receipt = root / "runs/checkpoint.json"
    receipt.parent.mkdir(exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "status": status,
                "output": {"data_dir": str(data)},
                "totals": {"dates_complete": 0, "unsafe": "secret-token", "secret-key": 42},
                "segments": [
                    {
                        "status": status,
                        "start_date": "20261023",
                        "error": {"type": "RuntimeError", "message": "partial checkpoint"},
                    }
                ],
            }
        )
    )
    return receipt


def test_completed_resume_keeps_original_failure_and_returns_payload_unchanged(tmp_path):
    receipt = _receipt(tmp_path, status="partial")
    original = receipt.read_bytes()
    result = {"status": "download_incomplete", "target_date": "20261023"}

    @record_operational_run
    def invoke(options: Options):
        record_backfill_attempt("TUSHARE_TOKEN", receipt)
        return result

    assert invoke(Options(tmp_path)) is result
    first = read_run_history(tmp_path)[0]
    attempt = first["attempts"][0]
    assert attempt["totals"] == {"dates_complete": 0}
    assert attempt["segments"][0]["start_date"] == "20261023"
    assert attempt["receipt_sha256"] == hashlib.sha256(original).hexdigest()
    failure = attempt["partition_failures"][0]
    assert failure["error"]["category"] == "provider_rate_limit"
    assert failure["error"]["rate_limit_count"] == 1
    assert failure["error"]["rate_limit_unit"] == "hour"
    assert failure["missing_symbol_count"] == 1
    assert "secret-token" not in json.dumps(first)
    assert "private.example" not in json.dumps(first)
    frozen = json.dumps(first, sort_keys=True)
    _receipt(tmp_path, status="complete")
    result = {"status": "operational_canonical_updated", "date_max": "20261023"}
    invoke(Options(tmp_path, fallback_token_env=None))
    records = read_run_history(tmp_path)
    assert len(records) == 2
    assert json.dumps(records[0], sort_keys=True) == frozen
    assert records[1]["fallback_enabled"] is False
    assert records[1]["attempts"][0]["status"] == "complete"


def test_exception_is_recorded_without_error_message_and_context_resets(tmp_path):
    @record_operational_run
    def broken(options: Options):
        record_backfill_attempt("TUSHARE_TOKEN_2", tmp_path / "missing.json")
        raise RuntimeError("https://private.example secret-token")

    with pytest.raises(RuntimeError, match="secret-token"):
        broken(Options(tmp_path))
    record_backfill_attempt("OUTSIDE", tmp_path / "missing.json")
    record = read_run_history(tmp_path)[0]
    assert record["status"] == "failed"
    assert record["error_type"] == "RuntimeError"
    assert record["attempts"][0]["receipt_status"] == "missing"
    assert len(record["attempts"]) == 1
    assert "secret-token" not in json.dumps(record)


def test_nested_invocations_restore_outer_context(tmp_path):
    @record_operational_run
    def inner(options: Options):
        record_backfill_attempt("INNER", tmp_path / "missing.json")
        return {"status": "noop"}

    @record_operational_run
    def outer(options: Options):
        inner(options)
        record_backfill_attempt("OUTER", tmp_path / "missing.json")
        return {"status": "noop"}

    outer(Options(tmp_path))
    assert sorted(r["attempts"][0]["token_env"] for r in read_run_history(tmp_path)) == [
        "INNER",
        "OUTER",
    ]


def test_corrupt_checkpoint_is_explicit_snapshot_error(tmp_path):
    bad = tmp_path / "corrupt.json"
    bad.write_text("not-json")

    @record_operational_run
    def invoke(options: Options):
        record_backfill_attempt("TUSHARE_TOKEN", bad)
        return {"status": "download_incomplete", "secret": "must-not-copy"}

    invoke(Options(tmp_path))
    record = read_run_history(tmp_path)[0]
    assert record["attempts"][0]["snapshot_error"] == "JSONDecodeError"
    assert "must-not-copy" not in json.dumps(record)


def test_sidecar_scan_cannot_escape_artifacts_root(tmp_path):
    receipt = _receipt(tmp_path, status="partial")
    value = json.loads(receipt.read_text())
    value["output"]["data_dir"] = str(tmp_path.parent)
    receipt.write_text(json.dumps(value))

    @record_operational_run
    def invoke(options: Options):
        record_backfill_attempt("TUSHARE_TOKEN", receipt)
        return {"status": "download_incomplete"}

    invoke(Options(tmp_path))
    attempt = read_run_history(tmp_path)[0]["attempts"][0]
    assert attempt["snapshot_error"] == "data_dir_outside_artifacts_root"


def test_history_is_private_and_reading_missing_history_does_not_create_it(tmp_path):
    assert read_run_history(tmp_path) == []
    assert not list(tmp_path.iterdir())

    @record_operational_run
    def invoke(options: Options):
        return {"status": "noop"}

    invoke(Options(tmp_path))
    history = tmp_path / "metadata/minute_operational/history"
    assert stat.S_IMODE(history.stat().st_mode) == 0o700
    assert stat.S_IMODE(next(history.glob("*.json")).stat().st_mode) == 0o600


@pytest.mark.parametrize("content", ["not-json", '{"schema_version": "wrong"}'])
def test_history_reader_rejects_corrupt_or_wrong_schema(tmp_path, content):
    history = tmp_path / "metadata/minute_operational/history"
    history.mkdir(parents=True)
    (history / "broken.json").write_text(content)
    with pytest.raises(ValueError):
        read_run_history(tmp_path)


def test_non_date_target_is_not_copied_into_evidence(tmp_path):
    @record_operational_run
    def invoke(options: Options):
        return {"status": "noop", "target_date": options.target_date}

    invoke(Options(tmp_path, target_date="secret-token"))
    record = read_run_history(tmp_path)[0]
    assert record["target_date"] is None
    assert "secret-token" not in json.dumps(record)


def test_history_directory_escape_is_rejected_before_function_runs(tmp_path):
    root, foreign = tmp_path / "artifacts", tmp_path / "foreign"
    foreign.mkdir()
    history = root / "metadata/minute_operational/history"
    history.parent.mkdir(parents=True)
    history.symlink_to(foreign, target_is_directory=True)

    @record_operational_run
    def invoke(options: Options):
        pytest.fail("must reject escaped history before running the job")

    with pytest.raises(ValueError, match="escapes"):
        invoke(Options(root))
    with pytest.raises(ValueError, match="escapes"):
        read_run_history(root)
    assert not list(foreign.iterdir())


def test_history_file_escape_is_rejected_without_reading_foreign_json(tmp_path):
    history = tmp_path / "artifacts/metadata/minute_operational/history"
    history.mkdir(parents=True)
    foreign = tmp_path / "foreign.json"
    foreign.write_text("must-not-read")
    (history / "linked.json").symlink_to(foreign)
    with pytest.raises(ValueError, match="escapes"):
        read_run_history(tmp_path / "artifacts")


def test_context_resets_if_final_history_write_fails(tmp_path, monkeypatch):
    from quant_market_data_platform import _minute_operational_history as history_module

    original_write = history_module._atomic_write_json
    calls = 0

    def fail_final_write(path, value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk test failure")
        original_write(path, value)

    @record_operational_run
    def invoke(options: Options):
        return {"status": "noop"}

    monkeypatch.setattr(history_module, "_atomic_write_json", fail_final_write)
    with pytest.raises(OSError, match="disk test failure"):
        invoke(Options(tmp_path))
    record_backfill_attempt("OUTSIDE", tmp_path / "missing.json")
    assert calls == 2


def test_concurrent_calls_have_distinct_records_and_isolated_attempts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    @record_operational_run
    def invoke(options: Options):
        barrier.wait(timeout=5)
        assert options.fallback_token_env is not None
        record_backfill_attempt(options.fallback_token_env, tmp_path / "missing.json")
        return {"status": "noop"}

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                invoke, [Options(tmp_path, fallback_token_env=name) for name in ("A", "B")]
            )
        )
    records = read_run_history(tmp_path)
    assert len({record["run_id"] for record in records}) == 2
    assert all(len(record["attempts"]) == 1 for record in records)
    assert sorted(record["attempts"][0]["token_env"] for record in records) == ["A", "B"]


def _execution(root: Path, receipt: Path):
    from quant_market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
    from quant_market_data_platform.tushare_minute_backfill_part01 import _MinuteBackfillExecution

    return _MinuteBackfillExecution(
        receipt_path=receipt,
        run_root=root / "run",
        data_dir=root / "staging/data",
        token_env="TUSHARE_TOKEN_2",
        resolved_api_url=None,
        provider_no_data_exceptions_path=None,
        policy=TushareRequestPolicy(),
        exchange=None,
        secrets=[],
        limits={},
        minute_quota_mode=None,
        minute_quota_db=None,
        minute_quota_consumer=None,
        minute_quota_limit_rows=None,
        minute_quota_safety_rows=None,
        minute_quota_gate=None,
        minute_quota_limit_requests=None,
        minute_quota_burst_limit_requests=None,
        minute_quota_safety_requests=None,
        minute_quota_allow_burst=None,
    )


@pytest.mark.parametrize("mode", ["complete", "partial", "exception"])
def test_backfill_freezes_attempt_before_releasing_resume_lock(tmp_path, monkeypatch, mode):
    import fcntl

    from quant_market_data_platform import _minute_operational_history as history_module
    from quant_market_data_platform import tushare_minute_backfill_part02 as backfill

    receipt_path = _receipt(tmp_path, status="complete" if mode == "complete" else "partial")
    receipt = json.loads(receipt_path.read_text())
    receipt["segments"][0].update(segment_id="one", date_count=1, minute_request_upper_bound=1)
    execution = _execution(tmp_path, receipt_path)
    lock = execution.run_root / ".minute-backfill.lock"
    observed = []
    original_record = history_module.record_backfill_attempt

    def snapshot_while_locked(token_env, path):
        with lock.open("a+") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        original_record(token_env, path)
        observed.append(True)

    def load(**kwargs):
        if mode == "exception":
            raise RuntimeError("simulated load failure")
        return receipt

    def stop(execution, payload, planned, state):
        payload["status"] = "partial"
        backfill._save_receipt(payload, receipt_path)
        return "stopped"

    monkeypatch.setattr(history_module, "record_backfill_attempt", snapshot_while_locked)
    monkeypatch.setattr(backfill, "_load_or_create_receipt", load)
    monkeypatch.setattr(backfill, "_execute_planned_segment", stop)

    @record_operational_run
    def invoke(options: Options):
        backfill._execute_backfill_plan(
            execution,
            plan={"identity": {"segments": [{"segment_id": "one"}]}},
            plan_path=tmp_path / "plan.json",
        )
        return {"status": "noop"}

    if mode == "exception":
        with pytest.raises(RuntimeError, match="simulated load failure"):
            invoke(Options(tmp_path))
    else:
        invoke(Options(tmp_path))
    assert observed == [True]
    record = read_run_history(tmp_path)[0]
    snapshot = record["attempts"][0]
    expected = "complete" if mode == "complete" else "partial"
    assert snapshot["status"] == expected
    with lock.open("a+") as other:
        fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    receipt_path.write_text(json.dumps({"status": "complete", "replacement": True}))
    assert read_run_history(tmp_path)[0]["attempts"][0] == snapshot


def test_backfill_without_daily_journal_adds_no_history_files(tmp_path, monkeypatch):
    from quant_market_data_platform import tushare_minute_backfill_part02 as backfill

    receipt = _receipt(tmp_path, status="complete")
    expected = json.loads(receipt.read_text())
    monkeypatch.setattr(backfill, "_load_or_create_receipt", lambda **kwargs: expected)
    result = backfill._execute_backfill_plan(
        _execution(tmp_path, receipt), plan={"identity": {}}, plan_path=tmp_path / "plan.json"
    )
    assert result is expected
    assert not (tmp_path / "metadata/minute_operational/history").exists()


@pytest.mark.parametrize(
    ("started_at", "finished_at"),
    [
        ("not-a-date", None),
        ("2026-10-23T10:00:00", None),
        ("2026-10-23T10:00:00+00:00", "2026-10-23T11:00:00"),
        ("2026-10-23T10:00:00+00:00", "2026-10-23T09:59:59+00:00"),
        ("2026-10-23T10:00:00+00:00", []),
    ],
)
def test_history_reader_rejects_invalid_temporal_evidence(tmp_path, started_at, finished_at):
    history = tmp_path / "metadata/minute_operational/history"
    history.mkdir(parents=True)
    (history / "run.json").write_text(
        json.dumps(
            {
                "schema_version": "a_share.minute_operational_run.v1",
                "run_id": "run",
                "started_at": started_at,
                "finished_at": finished_at,
            }
        )
    )
    with pytest.raises(ValueError):
        read_run_history(tmp_path)


def test_history_reader_normalizes_offsets_without_modifying_evidence(tmp_path):
    history = tmp_path / "metadata/minute_operational/history"
    history.mkdir(parents=True)
    payloads = [
        {"run_id": "later", "started_at": "2026-10-23T02:00:00+00:00", "finished_at": None},
        {
            "run_id": "earlier",
            "started_at": "2026-10-23T09:00:00+08:00",
            "finished_at": "2026-10-23T09:30:00+08:00",
        },
    ]
    for payload in payloads:
        (history / f"{payload['run_id']}.json").write_text(
            json.dumps({"schema_version": "a_share.minute_operational_run.v1", **payload})
        )
    before = {path.name: path.read_bytes() for path in history.iterdir()}
    records = read_run_history(tmp_path)
    assert [record["run_id"] for record in records] == ["earlier", "later"]
    assert records[0]["started_at"] == "2026-10-23T01:00:00+00:00"
    assert records[0]["finished_at"] == "2026-10-23T01:30:00+00:00"
    assert records[1]["finished_at"] is None
    assert before == {path.name: path.read_bytes() for path in history.iterdir()}


def test_resolved_target_survives_exception_when_options_target_was_automatic(tmp_path):
    from quant_market_data_platform._minute_operational_history import set_operational_target

    @record_operational_run
    def invoke(options: Options):
        set_operational_target("20261023")
        raise RuntimeError("simulated plan failure")

    with pytest.raises(RuntimeError, match="simulated plan failure"):
        invoke(Options(tmp_path, target_date=None))
    assert read_run_history(tmp_path)[0]["target_date"] == "20261023"


@pytest.mark.parametrize("result", [{"status": "noop"}, {"status": "noop", "date_max": "20261024"}])
def test_resolved_target_is_not_replaced_by_newer_asset_extent(tmp_path, result):
    from quant_market_data_platform._minute_operational_history import set_operational_target

    @record_operational_run
    def invoke(options: Options):
        set_operational_target("20261023")
        return result

    invoke(Options(tmp_path, target_date=None))
    assert read_run_history(tmp_path)[0]["target_date"] == "20261023"
