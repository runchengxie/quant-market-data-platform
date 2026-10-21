"""Gap repair spends requests only for immutable, queued stock-days."""

from pathlib import Path

import pytest

from quant_market_data_platform.minute_gap_repair import GapRepairOptions, run_gap_repair


def test_empty_queue_does_not_initialize_quota(tmp_path: Path):
    result = run_gap_repair(GapRepairOptions(tmp_path, tmp_path / "runtime"))
    assert result["status"] == "empty"
    assert not (tmp_path / "metadata/tushare").exists()


def test_audit_block_precedes_queue_read(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "semantic-audit-blocked.json").write_text("{}")
    queue = tmp_path / "metadata/minute_backfill/reverse_scheduler/deferred_missing.json"
    queue.parent.mkdir(parents=True)
    queue.write_text("invalid")
    assert run_gap_repair(GapRepairOptions(tmp_path, runtime))["status"] == "audit_blocked"


def _queued(tmp_path, monkeypatch):
    import json

    from test_reverse_backfill_missing import _partial, _setup

    from quant_market_data_platform._reverse_backfill_missing import missing_queue

    sidecar = _partial(tmp_path, "20191213")
    _setup(tmp_path, monkeypatch)
    payload = json.loads(sidecar.read_text())
    from quant_market_data_platform.providers._a_share_mins_partition import _universe_hash

    payload.update(
        universe_hash=_universe_hash(set(payload["expected_symbols"]), rule="explicit_symbols"),
        universe_rule="explicit_symbols",
        universe_source="test",
    )
    sidecar.write_text(json.dumps(payload))
    metadata = tmp_path / "metadata/minute_backfill/reverse_scheduler"
    missing_queue(
        tmp_path / "staging/tushare_minute_backfill_reverse",
        set(),
        report_path=metadata / "deferred_missing.json",
    )
    return sidecar


def _bars():
    import pandas as pd

    times = [
        *pd.date_range("20191213 09:30", periods=121, freq="min"),
        *pd.date_range("20191213 13:01", periods=120, freq="min"),
    ]
    return pd.DataFrame(
        {
            "ts_code": ["001914.SZ"] * 241,
            "trade_time": times,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "vol": 1.0,
            "amount": 10.0,
        }
    )


class Ledger:
    def __init__(self, used=0):
        self.used = used

    def status(self, **kwargs):
        return {"consumers": [{"consumer": "minute_gap_repair", "committed_requests": self.used}]}


def test_singleton_success_preserves_source(tmp_path, monkeypatch):
    from quant_market_data_platform import _minute_gap_repair_artifacts as artifacts
    from quant_market_data_platform import minute_gap_repair as worker
    from quant_market_data_platform.providers._a_share_mins_partition import (
        validate_complete_minute_partition,
    )

    source = _queued(tmp_path, monkeypatch)
    original = {p: p.read_bytes() for p in source.parent.iterdir()}
    monkeypatch.setattr(worker, "_ledger", lambda _: Ledger())
    calls = []

    def fetch(options, job, code, ledger):
        calls.append((job["date"], code))
        return _bars()

    monkeypatch.setattr(artifacts, "_fetch", fetch)
    result = run_gap_repair(GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0))
    assert result["status"] == "candidate_ready"
    assert calls == [("20191213", "001914.SZ")]
    validate_complete_minute_partition(
        result["candidate"], trade_date="20191213", require_full_universe=False
    )
    assert all(p.read_bytes() == value for p, value in original.items())
    result2 = run_gap_repair(GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0))
    assert result2["status"] == "candidate_ready"
    assert result2["candidate"] == result["candidate"]
    assert len(calls) == 1


def test_daily_cap_and_cooldown(tmp_path, monkeypatch):
    import pandas as pd

    from quant_market_data_platform import _minute_gap_repair_artifacts as artifacts
    from quant_market_data_platform import minute_gap_repair as worker

    _queued(tmp_path, monkeypatch)
    options = GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0)
    monkeypatch.setattr(worker, "_ledger", lambda _: Ledger(500))
    monkeypatch.setattr(artifacts, "_fetch", lambda *args: pytest.fail("must not fetch"))
    assert run_gap_repair(options)["status"] == "daily_cap"
    monkeypatch.setattr(worker, "_ledger", lambda _: Ledger())
    monkeypatch.setattr(artifacts, "_fetch", lambda *args: pd.DataFrame())
    assert run_gap_repair(options)["status"] == "missing_response"
    assert run_gap_repair(options)["status"] == "cooldown"


def test_bad_bars_block_lane_and_keep_evidence(tmp_path, monkeypatch):
    from quant_market_data_platform import _minute_gap_repair_artifacts as artifacts
    from quant_market_data_platform import minute_gap_repair as worker

    _queued(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "_ledger", lambda _: Ledger())
    frame = _bars()
    frame.loc[0, "amount"] = 900.0
    monkeypatch.setattr(artifacts, "_fetch", lambda *args: frame)
    options = GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0)
    assert run_gap_repair(options)["status"] == "audit_blocked"
    assert list((tmp_path / "staging/minute_gap_repair").rglob("raw.parquet"))
    assert run_gap_repair(options)["status"] == "audit_blocked"


def test_real_ledger_daily_budget_persists_and_resets(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from quant_market_data_platform import minute_gap_repair as worker

    monkeypatch.setenv("TUSHARE_TOKEN_2", "test-only-token")
    options = GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0)
    ledger = worker._ledger(options)
    now = datetime(2026, 10, 18, 17, 0, tzinfo=UTC)
    monkeypatch.setattr(worker, "_now", lambda: now)
    with ledger.attempt(241, now=now) as attempt:
        attempt.commit(241)
    assert worker._daily_used(worker._ledger(options)) == 1
    tomorrow = now + timedelta(days=1)
    monkeypatch.setattr(worker, "_now", lambda: tomorrow)
    assert worker._daily_used(worker._ledger(options)) == 0


def test_real_fetch_is_singleton_and_consumes_ledger(tmp_path, monkeypatch):
    from quant_market_data_platform import minute_gap_repair as worker
    from quant_market_data_platform.providers import tushare_a_share as provider

    _queued(tmp_path, monkeypatch)
    monkeypatch.setenv("TUSHARE_TOKEN_2", "test-only-token")
    calls = []

    class Client:
        def stk_mins(self, **kwargs):
            calls.append(kwargs)
            return _bars()

    monkeypatch.setattr(provider, "get_tushare_client", lambda **kwargs: Client())
    options = GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0)
    assert run_gap_repair(options)["status"] == "candidate_ready"
    assert [call["ts_code"] for call in calls] == ["001914.SZ"]
    assert worker._daily_used(worker._ledger(options)) == 1


def test_writer_collision_does_not_read_queue(tmp_path):
    from quant_market_data_platform._minute_history_lock import history_writer_lock

    with history_writer_lock(tmp_path):
        assert (
            run_gap_repair(GapRepairOptions(tmp_path, tmp_path / "runtime"))["status"]
            == "writer_busy"
        )


def test_three_attempt_cap_survives_restart(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    import pandas as pd

    from quant_market_data_platform import _minute_gap_repair_artifacts as artifacts
    from quant_market_data_platform import minute_gap_repair as worker

    _queued(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "_ledger", lambda _: Ledger())
    calls = []

    def empty(*args):
        calls.append(1)
        return pd.DataFrame()

    monkeypatch.setattr(artifacts, "_fetch", empty)
    options = GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0)
    now = datetime(2026, 10, 18, 17, 0, tzinfo=UTC)
    for day in range(4):
        monkeypatch.setattr(worker, "_now", lambda day=day: now + timedelta(days=day))
        result = run_gap_repair(options)
    assert result["status"] == "attempts_exhausted"
    assert len(calls) == 3


def test_unknown_queue_never_constructs_ledger(tmp_path, monkeypatch):
    import json

    from quant_market_data_platform import minute_gap_repair as worker

    queue = tmp_path / "metadata/minute_backfill/reverse_scheduler/deferred_missing.json"
    queue.parent.mkdir(parents=True)
    queue.write_text(
        json.dumps(
            {
                "schema_version": "tushare.minute.deferred_missing.v1",
                "dates": {
                    "20191213": {"missing_symbols": ["001914.SZ"], "sidecars": ["/tmp/unknown"]}
                },
            }
        )
    )
    monkeypatch.setattr(worker, "_ledger", lambda _: pytest.fail("must not build ledger"))
    assert (
        run_gap_repair(GapRepairOptions(tmp_path, tmp_path / "runtime"))["status"]
        == "no_eligible_queue"
    )


def test_resume_patch_without_another_request(tmp_path, monkeypatch):
    from quant_market_data_platform import _minute_gap_repair_artifacts as artifacts
    from quant_market_data_platform import minute_gap_repair as worker

    _queued(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "_ledger", lambda _: Ledger())
    monkeypatch.setattr(artifacts, "_fetch", lambda *args: _bars())
    real_assemble = artifacts.assemble
    invocations = []

    def crash_after_patch(options, job, state):
        invocations.append(1)
        if len(invocations) == 2:
            raise RuntimeError("simulated crash after durable patch state")
        return real_assemble(options, job, state)

    monkeypatch.setattr(artifacts, "assemble", crash_after_patch)
    options = GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0)
    with pytest.raises(RuntimeError):
        run_gap_repair(options)
    monkeypatch.setattr(artifacts, "assemble", real_assemble)
    monkeypatch.setattr(artifacts, "_fetch", lambda *args: pytest.fail("must reuse patch"))
    monkeypatch.setattr(worker, "_ledger", lambda _: pytest.fail("must not initialize ledger"))
    assert run_gap_repair(options)["status"] == "candidate_ready"


def test_cached_patch_tampering_blocks_without_api(tmp_path, monkeypatch):
    from quant_market_data_platform import _minute_gap_repair_artifacts as artifacts
    from quant_market_data_platform import minute_gap_repair as worker

    _queued(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "_ledger", lambda _: Ledger())
    monkeypatch.setattr(artifacts, "_fetch", lambda *args: _bars())
    real_assemble = artifacts.assemble
    invocations = []

    def crash_after_patch(options, job, state):
        invocations.append(1)
        if len(invocations) == 2:
            raise RuntimeError("simulated crash")
        return real_assemble(options, job, state)

    monkeypatch.setattr(artifacts, "assemble", crash_after_patch)
    options = GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0)
    with pytest.raises(RuntimeError):
        run_gap_repair(options)
    patch = next((tmp_path / "staging/minute_gap_repair").rglob("patch.parquet"))
    patch.write_bytes(b"tampered")
    monkeypatch.setattr(artifacts, "assemble", real_assemble)
    monkeypatch.setattr(worker, "_ledger", lambda _: pytest.fail("must not initialize ledger"))
    assert run_gap_repair(options)["status"] == "audit_blocked"


def test_failed_final_validation_leaves_no_complete_candidate(tmp_path, monkeypatch):
    import json

    from quant_market_data_platform import _minute_gap_repair_artifacts as artifacts
    from quant_market_data_platform import minute_gap_repair as worker

    _queued(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "_ledger", lambda _: Ledger())
    monkeypatch.setattr(artifacts, "_fetch", lambda *args: _bars())

    def fail(*args, **kwargs):
        raise ValueError("forced validator failure")

    monkeypatch.setattr(artifacts, "validate_complete_minute_partition", fail)
    result = run_gap_repair(GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0))
    assert result["status"] == "audit_blocked"
    assert not list((tmp_path / "staging/tushare_minute_backfill_reverse/runs").glob("repair-*"))
    sidecars = list(
        (tmp_path / "staging/minute_gap_repair/candidates").rglob("_minute_mirror.json")
    )
    assert sidecars
    assert all(json.loads(path.read_text())["status"] != "complete" for path in sidecars)
    assert list((tmp_path / "staging/minute_gap_repair").rglob("raw.parquet"))


@pytest.mark.parametrize("entry", [None, {"attempts": "bad"}, {"attempts": -1}])
def test_invalid_state_is_closed_without_ledger(tmp_path, monkeypatch, entry):
    import json

    from quant_market_data_platform import minute_gap_repair as worker

    _queued(tmp_path, monkeypatch)
    state = tmp_path / "metadata/minute_backfill/reverse_scheduler/gap_repair/state.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"jobs": {"bad-key": entry}}))
    monkeypatch.setattr(worker, "_ledger", lambda _: pytest.fail("invalid state may not fetch"))
    assert (
        run_gap_repair(GapRepairOptions(tmp_path, tmp_path / "runtime"))["status"]
        == "invalid_state"
    )


def test_local_quota_rejection_does_not_consume_attempt(tmp_path, monkeypatch):
    import json

    from quant_market_data_platform import _minute_gap_repair_artifacts as artifacts
    from quant_market_data_platform import minute_gap_repair as worker
    from quant_market_data_platform.tushare_minute_quota import MinuteQuotaExceeded

    _queued(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "_ledger", lambda _: Ledger())

    def exhausted(*args):
        raise MinuteQuotaExceeded(
            quota_date="20261019",
            requested_rows=241,
            available_rows=0,
            consumer="minute_gap_repair",
        )

    monkeypatch.setattr(artifacts, "_fetch", exhausted)
    result = run_gap_repair(GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0))
    assert result["status"] == "quota_exhausted"
    state = json.loads(
        (tmp_path / "metadata/minute_backfill/reverse_scheduler/gap_repair/state.json").read_text()
    )
    assert next(iter(state["jobs"].values()))["attempts"] == 0


def test_unrepresentable_raw_response_still_blocks_and_retains_json(tmp_path, monkeypatch):
    from quant_market_data_platform import _minute_gap_repair_artifacts as artifacts
    from quant_market_data_platform import minute_gap_repair as worker

    _queued(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "_ledger", lambda _: Ledger())
    frame = _bars()
    frame["vol"] = frame["vol"].astype(object)
    frame.loc[0, "vol"] = "malformed-number"
    monkeypatch.setattr(artifacts, "_fetch", lambda *args: frame)
    result = run_gap_repair(GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0))
    assert result["status"] == "audit_blocked"
    assert list((tmp_path / "staging/minute_gap_repair").rglob("raw.json"))


@pytest.mark.parametrize("persisted_candidate", [False, True])
def test_outdated_provider_universe_is_preserved_without_retrying_successor_code(
    tmp_path, monkeypatch, persisted_candidate
):
    import json

    from quant_market_data_platform import minute_gap_repair as worker
    from quant_market_data_platform._reverse_backfill_missing import missing_queue
    from quant_market_data_platform.providers._a_share_mins_constants import (
        HISTORICAL_CODE_TRANSITION_UNIVERSE_RULE,
    )
    from quant_market_data_platform.providers._a_share_mins_partition import _universe_hash

    source = _queued(tmp_path, monkeypatch)
    payload = json.loads(source.read_text())
    payload["universe_rule"] = HISTORICAL_CODE_TRANSITION_UNIVERSE_RULE
    payload["universe_hash"] = _universe_hash(
        set(payload["expected_symbols"]), rule=payload["universe_rule"]
    )
    source.write_text(json.dumps(payload))
    missing_queue(
        tmp_path / "staging/tushare_minute_backfill_reverse",
        set(),
        report_path=tmp_path / "metadata/minute_backfill/reverse_scheduler/deferred_missing.json",
    )
    candidate_files = {}
    if persisted_candidate:
        from quant_market_data_platform import _minute_gap_repair_artifacts as artifacts

        with monkeypatch.context() as old_worker:
            old_worker.setattr(worker, "_require_native_provider_universe", lambda *args: None)
            old_worker.setattr(worker, "_ledger", lambda _: Ledger())
            old_worker.setattr(artifacts, "_fetch", lambda *args: _bars())
            previous = run_gap_repair(
                GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0)
            )
        assert previous["status"] == "candidate_ready"
        candidate_files = {
            path: path.read_bytes()
            for path in Path(previous["candidate"]).iterdir()
            if path.is_file()
        }
    original = source.read_bytes()
    monkeypatch.setattr(worker, "_ledger", lambda _: pytest.fail("must not retry an invalid code"))
    result = run_gap_repair(GapRepairOptions(tmp_path, tmp_path / "runtime", min_free_bytes=0))
    assert result["status"] == "no_eligible_queue"
    assert result["skipped"] == [
        {"date": "20191213", "reason": "historical_code_transition_required"}
    ]
    assert source.read_bytes() == original
    assert not (tmp_path / "metadata/tushare").exists()
    assert all(path.read_bytes() == content for path, content in candidate_files.items())
