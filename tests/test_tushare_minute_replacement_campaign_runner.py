from __future__ import annotations

import fcntl
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from market_data_platform import tushare_minute_replacement_campaign_reconcile as reconcile
from market_data_platform import tushare_minute_replacement_campaign_runner as runner


@pytest.fixture(autouse=True)
def _stable_quota_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner,
        "_quota_window_clock",
        lambda _timezone_name: ("2026-01-02", 86_400.0),
    )


def _manifest(tmp_path: Path, *trade_dates: str) -> dict[str, Any]:
    return {
        "schema_version": runner.SCHEMA_VERSION,
        "baseline": {"partial": {}},
        "config": {
            "blocker_services": [],
            "marketdata_bin": "marketdata",
            "repo_root": str(tmp_path),
            "stagger_seconds": 0,
            "token_env": "TEST_TUSHARE_TOKEN",
        },
        "ledger_path": str(tmp_path / "ledger.json"),
        "lock_path": str(tmp_path / "campaign.lock"),
        "days": [
            {
                "day": index,
                "phases": [
                    {
                        "name": "main",
                        "lanes": {
                            "a": {
                                "data_root": str(tmp_path / f"day-{index:02d}"),
                                "dates": [trade_date],
                                "plan_path": str(tmp_path / f"day-{index:02d}.plan.json"),
                                "receipt_path": str(tmp_path / f"day-{index:02d}.receipt.json"),
                            }
                        },
                    }
                ],
            }
            for index, trade_date in enumerate(trade_dates, start=1)
        ],
    }


def _complete_receipt() -> dict[str, Any]:
    return {
        "rows": 241,
        "symbols": 1,
        "partition_sha256": "partition",
        "sidecar_sha256": "sidecar",
        "universe_rule": "test",
        "market_symbol_counts": {"SH": 1, "SZ": 0, "BJ": 0},
    }


def _write_checkpoint_rows(
    data_root: Path,
    trade_date: str,
    rows: int,
    *,
    status: str = "partial",
) -> None:
    sidecar = data_root / f"trade_date={trade_date}" / runner.COMPLETENESS_FILENAME
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps(
            {
                "trade_date": trade_date,
                "status": status,
                "completed_symbols": ["S"] if rows else [],
                "expected_symbols": ["S"],
                "partition": {"rows": rows},
            }
        ),
        encoding="utf-8",
    )


def test_campaign_progress_counts_only_rows_added_by_this_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day_one = "20260102"
    day_two = "20260105"
    manifest = _manifest(tmp_path, day_one, day_two)
    _write_checkpoint_rows(tmp_path / "day-01", day_one, 100)
    monkeypatch.setattr(runner.time, "monotonic", lambda: 1_000.0)
    progress = runner._CampaignProgress(
        manifest,
        max_new_rows=50,
        max_runtime_seconds=600,
    )

    _write_checkpoint_rows(tmp_path / "day-01", day_one, 120)
    _write_checkpoint_rows(tmp_path / "day-02", day_two, 31)

    assert progress.baseline_rows == 100
    assert progress.refresh() == 51
    assert progress.new_rows == 51
    assert progress.stop_reason() == "row_budget"


def test_campaign_progress_fails_closed_if_persisted_rows_move_backwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trade_date = "20260102"
    manifest = _manifest(tmp_path, trade_date)
    _write_checkpoint_rows(tmp_path / "day-01", trade_date, 100)
    monkeypatch.setattr(runner.time, "monotonic", lambda: 1_000.0)
    progress = runner._CampaignProgress(
        manifest,
        max_new_rows=1_000,
        max_runtime_seconds=600,
    )
    _write_checkpoint_rows(tmp_path / "day-01", trade_date, 99)

    with pytest.raises(runner.CampaignAccountingError, match="moved backwards"):
        progress.refresh()


@pytest.mark.parametrize("stop_reason", ["runtime", "row_budget"])
def test_run_commands_interrupts_each_active_lane_once_for_internal_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop_reason: str,
) -> None:
    class FakeChild:
        def __init__(self) -> None:
            self.signals: list[int] = []
            self.exited = False

        def poll(self) -> int | None:
            return 0 if self.exited else None

        def send_signal(self, signum: int) -> None:
            self.signals.append(signum)
            if signum == runner.signal.SIGINT:
                self.exited = True

        def wait(self) -> int:
            assert self.exited
            return 0

    children: list[FakeChild] = []

    def fake_popen(_command: list[str], *, cwd: str) -> FakeChild:
        assert cwd == str(tmp_path)
        child = FakeChild()
        children.append(child)
        return child

    checks = 0

    def stop_check() -> str | None:
        nonlocal checks
        checks += 1
        return stop_reason if checks >= 3 else None

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)

    result = runner._run_commands(
        {"config": {"repo_root": str(tmp_path)}},
        [("a", ["lane-a"]), ("b", ["lane-b"])],
        dry_run=False,
        stagger_seconds=0,
        stop_check=stop_check,
        poll_seconds=1,
        interrupt_grace_seconds=300,
    )

    assert result.stop_reason == stop_reason
    assert result.exit_codes == {"a": 0, "b": 0}
    assert len(children) == 2
    assert [child.signals for child in children] == [
        [runner.signal.SIGINT],
        [runner.signal.SIGINT],
    ]


def test_run_commands_does_not_start_a_lane_when_internal_stop_is_already_due(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_popen(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("an already-due stop must prevent lane startup")

    monkeypatch.setattr(runner.subprocess, "Popen", fail_popen)

    result = runner._run_commands(
        {"config": {"repo_root": str(tmp_path)}},
        [("a", ["lane-a"]), ("b", ["lane-b"])],
        dry_run=False,
        stagger_seconds=0,
        stop_check=lambda: "runtime",
        poll_seconds=1,
        interrupt_grace_seconds=300,
    )

    assert result.stop_reason == "runtime"
    assert result.exit_codes == {}


def test_run_commands_reaps_started_lane_when_later_spawn_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeChild:
        def __init__(self) -> None:
            self.signals: list[int] = []
            self.waited = False
            self.exited = False

        def poll(self) -> int | None:
            return 0 if self.exited else None

        def send_signal(self, signum: int) -> None:
            self.signals.append(signum)
            self.exited = True

        def wait(self, timeout: float | None = None) -> int:
            assert timeout is not None
            assert self.exited
            self.waited = True
            return 0

    first_child = FakeChild()
    starts = 0

    def fake_popen(_command: list[str], *, cwd: str) -> FakeChild:
        nonlocal starts
        assert cwd == str(tmp_path)
        starts += 1
        if starts == 2:
            raise OSError("lane b failed to start")
        return first_child

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)

    with pytest.raises(OSError, match="lane b"):
        runner._run_commands(
            {"config": {"repo_root": str(tmp_path)}},
            [("a", ["lane-a"]), ("b", ["lane-b"])],
            dry_run=False,
            stagger_seconds=0,
            stop_check=None,
            poll_seconds=1,
            interrupt_grace_seconds=300,
        )

    assert first_child.signals == [runner.signal.SIGINT]
    assert first_child.waited is True


def _stub_partition_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    complete_dates: set[str],
    complete_on_attempt: int = 1,
) -> list[str]:
    run_dates: list[str] = []

    def fake_validated_date(_data_root: Path, trade_date: str) -> dict[str, Any] | None:
        return _complete_receipt() if trade_date in complete_dates else None

    def fake_run_phase(
        _manifest: dict[str, Any], phase: dict[str, Any], *, dry_run: bool, **_kwargs: Any
    ) -> runner._PhaseRunResult:
        assert dry_run is False
        trade_date = str(phase["lanes"]["a"]["dates"][0])
        run_dates.append(trade_date)
        if run_dates.count(trade_date) >= complete_on_attempt:
            complete_dates.add(trade_date)
        return runner._PhaseRunResult({"a": 0})

    monkeypatch.setattr(runner, "_validated_date", fake_validated_date)
    monkeypatch.setattr(runner, "_run_phase", fake_run_phase)
    return run_dates


def _stub_budgeted_phase_rows(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows_by_date: dict[str, int],
    complete_dates: set[str] | None = None,
) -> list[str]:
    completed = complete_dates if complete_dates is not None else set()
    run_dates: list[str] = []

    def fake_validated_date(_data_root: Path, trade_date: str) -> dict[str, Any] | None:
        return _complete_receipt() if trade_date in completed else None

    def fake_run_phase(
        _manifest: dict[str, Any],
        phase: dict[str, Any],
        *,
        dry_run: bool,
        stop_check: Any = None,
        **_kwargs: Any,
    ) -> runner._PhaseRunResult:
        assert dry_run is False
        lane = phase["lanes"]["a"]
        trade_date = str(lane["dates"][0])
        run_dates.append(trade_date)
        _write_checkpoint_rows(
            Path(lane["data_root"]),
            trade_date,
            rows_by_date[trade_date],
            status="complete",
        )
        completed.add(trade_date)
        stop_reason = stop_check() if stop_check is not None else None
        return runner._PhaseRunResult({"a": 0}, stop_reason)

    monkeypatch.setattr(runner, "_validated_date", fake_validated_date)
    monkeypatch.setattr(runner, "_run_phase", fake_run_phase)
    return run_dates


@pytest.mark.parametrize("ledger_knows_day_one", [False, True])
def test_advance_one_day_skips_complete_day_and_runs_next(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ledger_knows_day_one: bool,
) -> None:
    day_one = "20260102"
    day_two = "20260105"
    run_dates = _stub_partition_state(monkeypatch, complete_dates={day_one})
    ledger: dict[str, Any] = {"days": {}}
    if ledger_knows_day_one:
        ledger["days"]["01"] = {"status": "complete", "phases": {}}

    outcome = runner._advance_one_day(
        _manifest(tmp_path, day_one, day_two),
        ledger,
        tmp_path / "ledger.json",
        dry_run=False,
    )

    assert outcome.state == "day_complete"
    assert outcome.day_key == "02"
    assert outcome.stop_reason is None
    assert run_dates == [day_two]
    assert ledger["days"]["01"]["status"] == "complete"
    assert ledger["days"]["02"]["status"] == "complete"


def test_run_next_still_advances_only_one_new_campaign_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day_one = "20260102"
    day_two = "20260105"
    day_three = "20260106"
    manifest = _manifest(tmp_path, day_one, day_two, day_three)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_dates = _stub_partition_state(monkeypatch, complete_dates=set())

    status = runner.run_next(SimpleNamespace(manifest=manifest_path, dry_run=False))

    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert status == 0
    assert run_dates == [day_one]
    assert ledger["days"]["01"]["status"] == "complete"
    assert "02" not in ledger["days"]
    assert "03" not in ledger["days"]


def test_run_budgeted_crosses_campaign_days_until_soft_row_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dates = ("20260102", "20260105", "20260106", "20260107")
    manifest = _manifest(tmp_path, *dates)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_dates = _stub_budgeted_phase_rows(
        monkeypatch,
        rows_by_date={
            dates[0]: 30_000_000,
            dates[1]: 30_000_000,
            dates[2]: 8_000_000,
            dates[3]: 1,
        },
    )

    status = runner.run_budgeted(
        SimpleNamespace(
            manifest=manifest_path,
            dry_run=False,
            max_new_rows=67_000_000,
            max_runtime_seconds=11_700,
            poll_seconds=10,
            interrupt_grace_seconds=300,
        )
    )

    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert status == 0
    assert run_dates == list(dates[:3])
    assert ledger["days"]["01"]["status"] == "complete"
    assert ledger["days"]["02"]["status"] == "complete"
    assert ledger["days"]["03"]["phases"]["main"]["status"] == "complete"
    assert "04" not in ledger["days"]
    finished = [event for event in ledger["events"] if event["status"] == "budgeted_run_finished"]
    assert finished[-1]["stop_reason"] == "row_budget"
    assert finished[-1]["new_rows"] == 68_000_000


def test_run_budgeted_carries_usage_across_invocations_in_one_quota_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dates = ("20260102", "20260105", "20260106")
    manifest = _manifest(tmp_path, *dates)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_dates = _stub_budgeted_phase_rows(
        monkeypatch,
        rows_by_date=dict.fromkeys(dates, 30_000_000),
    )
    args = SimpleNamespace(
        manifest=manifest_path,
        dry_run=False,
        max_new_rows=50_000_000,
        max_runtime_seconds=11_700,
        poll_seconds=10,
        interrupt_grace_seconds=300,
        quota_timezone="Asia/Shanghai",
        quota_reset_guard_seconds=1200,
    )

    assert runner.run_budgeted(args) == 0
    assert run_dates == list(dates[:2])
    assert runner.run_budgeted(args) == 0
    assert run_dates == list(dates[:2])

    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    finished = [event for event in ledger["events"] if event["status"] == "budgeted_run_finished"]
    assert finished[-1]["new_rows"] == 0
    assert finished[-1]["quota_window_new_rows"] == 60_000_000


def test_run_budgeted_rebases_completed_rows_after_quota_date_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dates = ("20260102", "20260105", "20260106")
    manifest = _manifest(tmp_path, *dates)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_dates = _stub_budgeted_phase_rows(
        monkeypatch,
        rows_by_date=dict.fromkeys(dates, 30_000_000),
    )
    quota_dates = iter(("2026-01-02", "2026-01-03"))
    monkeypatch.setattr(
        runner,
        "_quota_window_clock",
        lambda _timezone_name: (next(quota_dates), 86_400.0),
    )
    args = SimpleNamespace(
        manifest=manifest_path,
        dry_run=False,
        max_new_rows=50_000_000,
        max_runtime_seconds=11_700,
        poll_seconds=10,
        interrupt_grace_seconds=300,
        quota_timezone="Asia/Shanghai",
        quota_reset_guard_seconds=1200,
    )

    assert runner.run_budgeted(args) == 0
    assert run_dates == list(dates[:2])
    assert runner.run_budgeted(args) == 0
    assert run_dates == list(dates)

    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert set(ledger["quota_windows"]) == {
        "2026-01-02:TEST_TUSHARE_TOKEN",
        "2026-01-03:TEST_TUSHARE_TOKEN",
    }


def test_run_budgeted_stops_on_partial_without_progress_and_retries_next_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day_one = "20260102"
    day_two = "20260105"
    manifest = _manifest(tmp_path, day_one, day_two)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_dates = _stub_partition_state(
        monkeypatch,
        complete_dates=set(),
        complete_on_attempt=999,
    )
    args = SimpleNamespace(
        manifest=manifest_path,
        dry_run=False,
        max_new_rows=67_000_000,
        max_runtime_seconds=11_700,
        poll_seconds=10,
        interrupt_grace_seconds=300,
    )

    assert runner.run_budgeted(args) == runner.RETRYABLE_EXIT_CODE
    first_ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert run_dates == [day_one]
    assert first_ledger["days"]["01"]["phases"]["main"]["status"] == "partial"
    assert "02" not in first_ledger["days"]
    first_finished = [
        event for event in first_ledger["events"] if event["status"] == "budgeted_run_finished"
    ]
    assert first_finished[-1]["stop_reason"] == "phase_incomplete"
    assert first_finished[-1]["new_rows"] == 0

    assert runner.run_budgeted(args) == runner.RETRYABLE_EXIT_CODE
    assert run_dates == [day_one, day_one]
    assert runner.run_budgeted(args) == runner.RETRYABLE_EXIT_CODE
    assert run_dates == [day_one, day_one]


def test_run_budgeted_dry_run_previews_one_phase_without_writing_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    day_one = "20260102"
    day_two = "20260105"
    manifest = _manifest(tmp_path, day_one, day_two)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(
        runner,
        "_validated_date",
        lambda _data_root, trade_date: _complete_receipt() if trade_date == day_one else None,
    )

    def fail_popen(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("dry-run must not start a lane process")

    monkeypatch.setattr(runner.subprocess, "Popen", fail_popen)

    status = runner.run_budgeted(
        SimpleNamespace(
            manifest=manifest_path,
            dry_run=True,
            max_new_rows=67_000_000,
            max_runtime_seconds=11_700,
            poll_seconds=10,
            interrupt_grace_seconds=300,
        )
    )

    output = capsys.readouterr().out
    assert status == 0
    assert not (tmp_path / "ledger.json").exists()
    assert not (tmp_path / "campaign.lock").exists()
    assert output.count(str(tmp_path / "day-02.plan.json")) == 1
    assert str(tmp_path / "day-01.plan.json") not in output


def test_advance_one_day_retries_first_partial_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day_one = "20260102"
    day_two = "20260105"
    day_three = "20260106"
    run_dates = _stub_partition_state(
        monkeypatch,
        complete_dates={day_one},
        complete_on_attempt=2,
    )
    manifest = _manifest(tmp_path, day_one, day_two, day_three)
    ledger: dict[str, Any] = {"days": {}}

    first = runner._advance_one_day(
        manifest,
        ledger,
        tmp_path / "ledger.json",
        dry_run=False,
    )
    second = runner._advance_one_day(
        manifest,
        ledger,
        tmp_path / "ledger.json",
        dry_run=False,
    )

    assert first.state == "phase_incomplete"
    assert first.day_key == "02"
    assert first.stop_reason is None
    assert second.state == "day_complete"
    assert second.day_key == "02"
    assert run_dates == [day_two, day_two]
    assert ledger["days"]["02"]["status"] == "complete"
    assert "03" not in ledger["days"]


def test_run_next_marks_campaign_complete_when_all_days_are_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    day_one = "20260102"
    day_two = "20260105"
    manifest = _manifest(tmp_path, day_one, day_two)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_dates = _stub_partition_state(
        monkeypatch,
        complete_dates={day_one, day_two},
    )

    status = runner.run_next(SimpleNamespace(manifest=manifest_path, dry_run=False))

    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert status == 0
    assert run_dates == []
    assert ledger["status"] == "complete"
    assert ledger["days"]["01"]["status"] == "complete"
    assert ledger["days"]["02"]["status"] == "complete"
    assert "campaign complete" in capsys.readouterr().out
    readiness = json.loads((tmp_path / "acquisition-readiness.json").read_text(encoding="utf-8"))
    assert readiness["acquisition_complete"] is True
    assert readiness["structural_ready"] is True
    assert readiness["promotion_ready"] is False
    assert readiness["cutover_performed"] is False
    assert readiness["ledger_sha256_at_reconciliation"] == runner._sha256(tmp_path / "ledger.json")


def test_reconcile_readiness_replaces_stale_marker_without_changing_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trade_date = "20260102"
    manifest = _manifest(tmp_path, trade_date)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _stub_partition_state(monkeypatch, complete_dates={trade_date})
    ledger_path = tmp_path / "ledger.json"
    ledger = runner._load_ledger(ledger_path, manifest_path)
    ledger["status"] = "complete"
    runner._write_ledger(ledger_path, ledger)
    ledger_before = ledger_path.read_bytes()
    (tmp_path / "acquisition-readiness.json").write_text(
        json.dumps({"schema_version": runner.READINESS_SCHEMA_VERSION}),
        encoding="utf-8",
    )

    status = reconcile.reconcile_readiness(SimpleNamespace(manifest=manifest_path))

    readiness = json.loads((tmp_path / "acquisition-readiness.json").read_text(encoding="utf-8"))
    assert status == 0
    assert ledger_path.read_bytes() == ledger_before
    assert readiness["ledger_sha256_at_reconciliation"] == runner._sha256(ledger_path)
    assert "dates=1 rows=241" in capsys.readouterr().out


def _valid_readiness_payload(
    manifest_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    ledger_path = Path(manifest["ledger_path"])
    ledger_path.write_text("{}\n", encoding="utf-8")
    return {
        "schema_version": runner.READINESS_SCHEMA_VERSION,
        "manifest_path": str(manifest_path),
        "manifest_sha256": runner._sha256(manifest_path),
        "ledger_path": str(ledger_path),
        "ledger_sha256_at_reconciliation": runner._sha256(ledger_path),
        "acquisition_complete": True,
        "structural_ready": True,
        "semantic_audit": "pending",
        "promotion_ready": False,
        "cutover_performed": False,
        "writes_production": False,
        "dates_complete": 1,
        "rows": 241,
        "date_receipts_sha256": "a" * 64,
    }


def test_validated_readiness_accepts_a_complete_reconciliation_summary(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "20260102")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    payload = _valid_readiness_payload(manifest_path, manifest)
    (tmp_path / "acquisition-readiness.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    assert runner._validated_readiness(manifest_path, manifest) == payload


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("structural_ready", False),
        ("semantic_audit", "complete"),
        ("promotion_ready", True),
        ("writes_production", True),
        ("dates_complete", 0),
        ("rows", -1),
        ("date_receipts_sha256", "invalid"),
        ("ledger_sha256_at_reconciliation", "0" * 64),
    ],
)
def test_validated_readiness_rejects_incomplete_or_stale_summary(
    tmp_path: Path,
    field: str,
    invalid_value: Any,
) -> None:
    manifest = _manifest(tmp_path, "20260102")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    payload = _valid_readiness_payload(manifest_path, manifest)
    payload[field] = invalid_value
    (tmp_path / "acquisition-readiness.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(runner.CampaignFatalError, match="invalid or stale"):
        runner._validated_readiness(manifest_path, manifest)


def _write_lane_receipt(
    lane: dict[str, Any],
    *,
    status: str,
    budget_violation: bool = False,
) -> None:
    Path(lane["receipt_path"]).write_text(
        json.dumps(
            {
                "plan_id": lane["plan_id"],
                "status": status,
                "output": {"writes_production": False},
                "segments": [
                    {
                        "segment_id": "segment-1",
                        "budget_violation": budget_violation,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_immutable_lane_receipt_requires_an_explicit_staging_only_output(tmp_path: Path) -> None:
    lane = {
        "plan_id": "immutable-plan",
        "receipt_path": str(tmp_path / "lane.receipt.json"),
    }
    Path(lane["receipt_path"]).write_text(
        json.dumps({"plan_id": lane["plan_id"], "status": "complete", "segments": []}),
        encoding="utf-8",
    )

    with pytest.raises(runner.CampaignFatalError, match="not staging-only"):
        runner._load_lane_receipt("a", lane, required=True)


@pytest.mark.parametrize(
    ("status", "budget_violation"),
    [("failed", False), ("complete", True)],
)
def test_complete_sidecar_cannot_mask_fatal_lane_receipt(
    tmp_path: Path,
    status: str,
    budget_violation: bool,
) -> None:
    lane = {
        "plan_id": "immutable-plan",
        "receipt_path": str(tmp_path / "lane.receipt.json"),
    }
    phase = {"lanes": {"a": lane}}
    _write_lane_receipt(lane, status=status, budget_violation=budget_violation)

    with pytest.raises(runner.CampaignFatalError):
        runner._validate_phase_outcome(
            phase,
            runner._PhaseRunResult({"a": 0}),
            sidecars_complete=True,
        )


def test_expected_parent_sigint_checkpoint_is_not_a_lane_failure(tmp_path: Path) -> None:
    lane = {
        "plan_id": "immutable-plan",
        "receipt_path": str(tmp_path / "lane.receipt.json"),
    }
    _write_lane_receipt(lane, status="interrupted")

    runner._validate_phase_outcome(
        {"lanes": {"a": lane}},
        runner._PhaseRunResult({"a": 130}, "schedule_drain"),
        sidecars_complete=False,
    )


def test_hard_stop_without_lane_receipt_is_retryable_not_fatal(tmp_path: Path) -> None:
    lane = {
        "plan_id": "immutable-plan",
        "receipt_path": str(tmp_path / "missing.receipt.json"),
    }

    with pytest.raises(runner.CampaignRetryableError, match="no receipt"):
        runner._validate_phase_outcome(
            {"lanes": {"a": lane}},
            runner._PhaseRunResult({"a": -runner.signal.SIGKILL}, "schedule_hard_stop"),
            sidecars_complete=False,
        )


def test_run_commands_dynamically_drains_deterministic_lane_near_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeChild:
        def __init__(self, lane_name: str) -> None:
            self.lane_name = lane_name
            self.exit_code: int | None = None
            self.polls = 0
            self.signals: list[int] = []

        def poll(self) -> int | None:
            self.polls += 1
            if self.lane_name == "a" and self.polls >= 6:
                self.exit_code = 0
            return self.exit_code

        def send_signal(self, signum: int) -> None:
            self.signals.append(signum)
            if signum == runner.signal.SIGINT:
                self.exit_code = 130

        def wait(self) -> int:
            assert self.exit_code is not None
            return self.exit_code

    children: dict[str, FakeChild] = {}

    def fake_popen(command: list[str], *, cwd: str) -> FakeChild:
        assert cwd == str(tmp_path)
        child = FakeChild(command[0])
        children[command[0]] = child
        return child

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    result = runner._run_commands(
        {"config": {"repo_root": str(tmp_path)}},
        [("a", ["a"]), ("b", ["b"])],
        dry_run=False,
        stagger_seconds=0,
        stop_check=None,
        poll_seconds=0.001,
        interrupt_grace_seconds=30,
        single_lane_check=lambda: True,
    )

    assert result.exit_codes == {"a": 0, "b": 130}
    assert result.intentional_checkpoint_lanes == ("b",)
    assert children["a"].signals == []
    assert children["b"].signals == [runner.signal.SIGINT]


def test_shared_quota_receipt_maps_to_normal_campaign_checkpoint(tmp_path: Path) -> None:
    lane = {
        "plan_id": "immutable-plan",
        "receipt_path": str(tmp_path / "lane.receipt.json"),
    }
    Path(lane["receipt_path"]).write_text(
        json.dumps(
            {
                "plan_id": lane["plan_id"],
                "status": "partial",
                "output": {"writes_production": False},
                "segments": [
                    {
                        "segment_id": "segment-1",
                        "error": {"type": "MinuteQuotaPoolClosed"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    reason = runner._validate_phase_outcome(
        {"lanes": {"a": lane}},
        runner._PhaseRunResult({"a": 1}),
        sidecars_complete=False,
    )

    assert reason == "shared_quota_exhausted"


def test_campaign_forwards_canonical_shared_quota_controls() -> None:
    arguments = runner._minute_quota_args(
        {
            "minute_quota_mode": "enforce",
            "minute_quota_db": "/data/quota.sqlite3",
            "minute_quota_limit_rows": 80_000_000,
            "minute_quota_safety_rows": 5_000_000,
            "minute_quota_gate": "requests",
            "minute_quota_limit_requests": 10_000,
            "minute_quota_burst_limit_requests": 20_000,
            "minute_quota_safety_requests": 500,
            "minute_quota_allow_burst": True,
        }
    )

    assert arguments == [
        "--minute-quota-mode",
        "enforce",
        "--minute-quota-db",
        "/data/quota.sqlite3",
        "--minute-quota-limit-rows",
        "80000000",
        "--minute-quota-safety-rows",
        "5000000",
        "--minute-quota-gate",
        "requests",
        "--minute-quota-limit-requests",
        "10000",
        "--minute-quota-burst-limit-requests",
        "20000",
        "--minute-quota-safety-requests",
        "500",
        "--minute-quota-allow-burst",
        "--minute-quota-consumer",
        "replacement_campaign",
    ]


def test_campaign_quota_controls_fall_back_to_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDP_TUSHARE_MINUTE_QUOTA_GATE", "requests")
    monkeypatch.setenv("MDP_TUSHARE_MINUTE_QUOTA_ALLOW_BURST", "false")

    arguments = runner._minute_quota_args({})

    assert arguments == [
        "--minute-quota-gate",
        "requests",
        "--no-minute-quota-allow-burst",
        "--minute-quota-consumer",
        "replacement_campaign",
    ]


def test_runtime_config_uses_stable_mdp_root_for_immutable_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stable_root = tmp_path / "production" / "market-data-platform"
    (stable_root / ".venv" / "bin").mkdir(parents=True)
    monkeypatch.setenv("MDP_DIR", str(stable_root))

    immutable = {
        "repo_root": "/retired/release",
        "marketdata_bin": "/retired/release/.venv/bin/marketdata",
    }

    runtime = runner._runtime_campaign_config(immutable, SimpleNamespace())

    assert runtime["repo_root"] == str(stable_root.resolve())
    assert runtime["marketdata_bin"] == str(
        (stable_root / ".venv" / "bin" / "marketdata").resolve()
    )
    assert immutable["repo_root"] == "/retired/release"


def test_runtime_config_can_opt_in_to_audited_provider_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TUSHARE_MINUTE_PROVIDER_NO_DATA_EXCEPTIONS",
        "/stable/provider_no_data_exceptions.v1.json",
    )

    runtime = runner._runtime_campaign_config({}, SimpleNamespace())

    assert runtime["provider_no_data_exceptions_path"] == (
        "/stable/provider_no_data_exceptions.v1.json"
    )


def test_runtime_config_filters_only_matching_blockers_without_mutating_manifest() -> None:
    immutable = {
        "blocker_services": ["daily.service", "factor.service", "other.service"],
        "minute_quota_mode": "enforce",
    }
    args = SimpleNamespace(
        ignore_blocker_service=["factor.service", "missing.service"],
        minute_quota_allow_burst=True,
    )

    runtime = runner._runtime_campaign_config(immutable, args)

    assert runtime["blocker_services"] == ["daily.service", "other.service"]
    assert runtime["minute_quota_allow_burst"] is True
    assert immutable["blocker_services"] == [
        "daily.service",
        "factor.service",
        "other.service",
    ]


def test_run_window_uses_wall_clock_for_drain_and_hard_stop() -> None:
    timezone = runner.ZoneInfo("Asia/Shanghai")
    window = runner._RunWindow(
        timezone=timezone,
        start=runner._parse_clock("00:45", option="start"),
        drain=runner._parse_clock("04:15", option="drain"),
        stop=runner._parse_clock("04:20", option="stop"),
    )

    assert window.start_state(datetime(2026, 7, 17, 0, 44, tzinfo=timezone)) == "window_not_open"
    assert window.start_state(datetime(2026, 7, 17, 0, 45, tzinfo=timezone)) is None
    assert window.stop_reason(datetime(2026, 7, 17, 4, 15, tzinfo=timezone)) == "schedule_drain"
    assert window.stop_reason(datetime(2026, 7, 17, 4, 20, tzinfo=timezone)) == "schedule_hard_stop"


def test_near_cap_runs_only_one_pending_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path, "20260102")
    phase = manifest["days"][0]["phases"][0]
    phase["lanes"]["b"] = {
        "data_root": str(tmp_path / "day-01-b"),
        "dates": ["20260105"],
        "plan_path": str(tmp_path / "day-01-b.plan.json"),
        "receipt_path": str(tmp_path / "day-01-b.receipt.json"),
    }
    completed: set[str] = set()
    started: list[list[str]] = []

    monkeypatch.setattr(
        runner,
        "_validated_date",
        lambda _root, trade_date: _complete_receipt() if trade_date in completed else None,
    )

    def fake_run_phase(
        _manifest: dict[str, Any], selected: dict[str, Any], **_kwargs: Any
    ) -> runner._PhaseRunResult:
        lanes = list(selected["lanes"])
        started.append(lanes)
        lane = selected["lanes"][lanes[0]]
        trade_date = lane["dates"][0]
        completed.add(trade_date)
        _write_checkpoint_rows(Path(lane["data_root"]), trade_date, 241, status="complete")
        return runner._PhaseRunResult({lanes[0]: 0})

    monkeypatch.setattr(runner, "_run_phase", fake_run_phase)
    progress = runner._CampaignProgress(
        manifest,
        max_new_rows=1_000,
        max_runtime_seconds=600,
    )
    progress.carry_forward(900)
    outcome = runner._advance_one_day(
        manifest,
        {"days": {}},
        tmp_path / "ledger.json",
        dry_run=False,
        progress=progress,
        single_lane_threshold_rows=200,
    )

    assert started == [["a"]]
    assert outcome.state == "stopped"
    assert outcome.stop_reason == "single_lane_checkpoint"


def test_status_json_reports_progress_quota_and_next_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    trade_date = "20260102"
    manifest = _manifest(tmp_path, trade_date)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _write_checkpoint_rows(tmp_path / "day-01", trade_date, 241, status="complete")
    ledger = runner._load_ledger(tmp_path / "ledger.json", manifest_path)
    ledger["quota_windows"] = {"2026-01-02:TEST_TUSHARE_TOKEN": {"high_water_new_rows": 241}}
    runner._write_ledger(tmp_path / "ledger.json", ledger)
    monkeypatch.setattr(
        runner,
        "_quota_window_clock",
        lambda _timezone: ("2026-01-02", 86_400.0),
    )

    assert (
        runner.campaign_status(
            SimpleNamespace(
                manifest=manifest_path,
                json=True,
                max_new_rows=1_000,
                quota_timezone="Asia/Shanghai",
                run_window_start="00:45",
                run_window_drain="04:15",
                run_window_stop="04:20",
            )
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["progress"]["complete_dates"] == 1
    assert payload["quota"]["remaining_rows"] == 759
    assert payload["next_window"]
    assert payload["eta"]["available"] is False
    assert payload["eta"]["reason"]
    assert payload["promotion_ready"] is False


def test_status_does_not_report_stale_active_run_as_running_after_lock_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _manifest(tmp_path, "20260102")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    ledger = runner._load_ledger(tmp_path / "ledger.json", manifest_path)
    ledger["active_run"] = {
        "run_id": "terminated-run",
        "started_at": runner._now(),
        "heartbeat_at": runner._now(),
    }
    runner._write_ledger(tmp_path / "ledger.json", ledger)
    monkeypatch.setattr(runner, "_lock_is_held", lambda _path: False)

    assert (
        runner.campaign_status(
            SimpleNamespace(
                manifest=manifest_path,
                json=True,
                max_new_rows=1_000,
                quota_timezone="Asia/Shanghai",
                run_window_start="00:45",
                run_window_drain="04:15",
                run_window_stop="04:20",
            )
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["health"]["state"] == "stalled"
    assert payload["health"]["reason"] == "active_run_without_lock"
    assert payload["health"]["lock_held"] is False


def test_status_estimates_eta_from_legacy_started_finished_pairs() -> None:
    samples = runner._recent_throughput_samples(
        [
            {"status": "budgeted_run_started", "at": "2026-07-17T00:00:00+00:00"},
            {
                "status": "budgeted_run_finished",
                "at": "2026-07-17T01:00:00+00:00",
                "new_rows": 10_000_000,
            },
        ]
    )

    assert samples == [10_000_000]


@pytest.mark.parametrize(
    "corrupt",
    [
        {"trade_date": "19990101", "status": "partial", "partition": {"rows": 1}},
        {"trade_date": "20260102", "status": "mystery", "partition": {"rows": 1}},
        {"trade_date": "20260102", "status": "partial", "partition": {"rows": -1}},
    ],
)
def test_status_inventory_fails_closed_on_corrupt_sidecar(
    tmp_path: Path, corrupt: dict[str, Any]
) -> None:
    manifest = _manifest(tmp_path, "20260102")
    sidecar = tmp_path / "day-01" / "trade_date=20260102" / runner.COMPLETENESS_FILENAME
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps(corrupt), encoding="utf-8")

    with pytest.raises(runner.CampaignAccountingError):
        runner._checkpoint_inventory(manifest)


def test_blocker_is_degraded_retryable_not_systemd_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path, "20260102")
    manifest["config"]["blocker_services"] = ["backup.service"]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(runner, "_active_blockers", lambda _services: ["backup.service:active"])

    status = runner.run_budgeted(
        SimpleNamespace(
            manifest=manifest_path,
            dry_run=False,
            max_new_rows=67_000_000,
            max_runtime_seconds=600,
            poll_seconds=10,
            interrupt_grace_seconds=300,
        )
    )

    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert status == runner.RETRYABLE_EXIT_CODE
    assert ledger["health"]["state"] == "degraded"
    assert ledger["health"]["no_progress_streak"] == 1
    assert ledger["health"]["last_reason"] == "blocked_by_service"

    assert (
        runner.run_budgeted(
            SimpleNamespace(
                manifest=manifest_path,
                dry_run=False,
                max_new_rows=67_000_000,
                max_runtime_seconds=600,
                poll_seconds=10,
                interrupt_grace_seconds=300,
            )
        )
        == runner.RETRYABLE_EXIT_CODE
    )
    stalled = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert stalled["health"]["state"] == "stalled"
    assert stalled["health"]["no_progress_streak"] == 2

    assert (
        runner.run_budgeted(
            SimpleNamespace(
                manifest=manifest_path,
                dry_run=False,
                max_new_rows=67_000_000,
                max_runtime_seconds=600,
                poll_seconds=10,
                interrupt_grace_seconds=300,
            )
        )
        == runner.RETRYABLE_EXIT_CODE
    )
    fused = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert fused["events"][-1]["status"] == "no_progress_fuse"
    assert fused["health"]["no_progress_streak"] == 2


def test_lock_skip_is_retryable_and_does_not_clobber_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest(tmp_path, "20260102")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    lock_path = tmp_path / "campaign.lock"
    lock_path.touch()

    with lock_path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = runner.run_next(SimpleNamespace(manifest=manifest_path, dry_run=False))

    assert status == runner.RETRYABLE_EXIT_CODE
    assert "retry later" in capsys.readouterr().err
    assert not (tmp_path / "ledger.json").exists()
