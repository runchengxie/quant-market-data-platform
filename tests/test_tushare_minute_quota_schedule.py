from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "operations" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schedule = _load_script("tushare_minute_quota_schedule.py")
renderer = _load_script("render_tushare_minute_campaign_units.py")


class _FakeLedger:
    def __init__(self) -> None:
        self.holds: dict[str, dict[str, Any]] = {}
        self.released: list[str] = []

    def status(self, *, quota_date: str) -> dict[str, Any]:
        return {
            "quota_date": quota_date,
            "token_fingerprint": "0123456789abcdef",
            "active_request_holds": [dict(item) for item in self.holds.values()],
        }

    def create_request_hold(self, requests: int, *, consumer: str, note: str) -> str:
        del note
        existing = self.holds.get(consumer)
        if existing is None:
            existing = {
                "hold_id": f"hold-{consumer}",
                "consumer": consumer,
                "request_slots": requests,
                "created_at": "2026-07-20T00:05:00+08:00",
            }
            self.holds[consumer] = existing
        else:
            existing["request_slots"] = max(int(existing["request_slots"]), requests)
        return str(existing["hold_id"])

    def release_hold(self, hold_id: str) -> None:
        if hold_id not in self.released:
            self.released.append(hold_id)
        for consumer, item in list(self.holds.items()):
            if item["hold_id"] == hold_id:
                del self.holds[consumer]


def _quota_args(tmp_path: Path) -> argparse.Namespace:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"config":{"token_env":"TUSHARE_TOKEN_2"}}', encoding="utf-8")
    return argparse.Namespace(
        manifest=manifest,
        minute_quota_db=tmp_path / "quota.sqlite3",
        state_root=tmp_path / "schedule",
        quota_timezone="Asia/Shanghai",
        minute_quota_limit_requests=10_000,
        minute_quota_burst_limit_requests=20_000,
        minute_quota_safety_requests=500,
        minute_quota_limit_rows=160_000_000,
        minute_quota_safety_rows=4_000_000,
        daily_consumer="daily_watch20",
        top200_consumer="top200_factor_observation",
        daily_hold_requests=300,
        top200_hold_requests=30,
        weekdays_only=True,
    )


def test_coordinator_is_idempotent_and_state_is_token_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _quota_args(tmp_path)
    ledger = _FakeLedger()
    monkeypatch.setattr(schedule, "_quota_date", lambda _timezone: "20260720")
    monkeypatch.setattr(schedule, "_is_weekday", lambda _timezone: True)

    def factory(*_args: object, **_kwargs: object) -> _FakeLedger:
        return ledger

    assert schedule.coordinate(args, ledger_factory=factory) == 0
    assert schedule.coordinate(args, ledger_factory=factory) == 0

    assert {key: value["request_slots"] for key, value in ledger.holds.items()} == {
        "daily_watch20": 300,
        "top200_factor_observation": 30,
    }
    states = list((tmp_path / "schedule" / "20260720").glob("*.json"))
    assert len(states) == 1
    payload = json.loads(states[0].read_text(encoding="utf-8"))
    assert payload["token_fingerprint"] == "0123456789abcdef"
    assert "TUSHARE_TOKEN_2" not in states[0].read_text(encoding="utf-8")
    assert payload["holds"]["daily_watch20"]["hold_id"] == "hold-daily_watch20"


def test_coordinator_defers_an_immutable_same_day_policy_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_data_platform.tushare_minute_quota import MinuteQuotaConfigurationError

    args = _quota_args(tmp_path)
    monkeypatch.setattr(schedule, "_quota_date", lambda _timezone: "20260719")

    class _MismatchedLedger:
        def status(self, *, quota_date: str) -> dict[str, Any]:
            del quota_date
            raise MinuteQuotaConfigurationError("minute quota pool policy mismatch")

    def factory(*_args: object, **_kwargs: object) -> _MismatchedLedger:
        return _MismatchedLedger()

    assert schedule.coordinate(args, ledger_factory=factory) == schedule.RETRYABLE_EXIT_CODE
    assert not list((tmp_path / "schedule" / "20260719").glob("*.json"))

    args.allow_burst = True
    assert schedule.schedule_status(args, ledger_factory=factory) == schedule.RETRYABLE_EXIT_CODE


def _write_marker(root: Path, evidence: Path, *, raw_complete: bool = True) -> Path:
    path = root / "20260720" / "top200_factor_observation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "quota_date": "20260720",
                "consumer": "top200_factor_observation",
                "trade_date": "20260720",
                "completed_at": "2026-07-20T21:05:00+08:00",
                "raw_complete": raw_complete,
                "evidence_path": str(evidence),
                "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return path


def test_raw_marker_requires_exact_identity_completion_and_hash(tmp_path: Path) -> None:
    evidence = tmp_path / "manifest.json"
    evidence.write_text('{"dates":["20260720"]}', encoding="utf-8")
    marker = _write_marker(tmp_path / "markers", evidence)

    payload = schedule.validate_raw_completeness_marker(
        marker,
        quota_date="20260720",
        consumer="top200_factor_observation",
    )
    assert payload["raw_complete"] is True

    _write_marker(tmp_path / "markers", evidence, raw_complete=False)
    with pytest.raises(ValueError, match="not complete"):
        schedule.validate_raw_completeness_marker(
            marker,
            quota_date="20260720",
            consumer="top200_factor_observation",
        )

    _write_marker(tmp_path / "markers", evidence)
    evidence.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        schedule.validate_raw_completeness_marker(
            marker,
            quota_date="20260720",
            consumer="top200_factor_observation",
        )


def test_release_ready_uses_marker_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _quota_args(tmp_path)
    args.raw_completeness_root = tmp_path / "markers"
    args.consumer = "top200_factor_observation"
    args.not_before_local = None
    args.deadline_local = None
    args.poll_seconds = 1
    ledger = _FakeLedger()
    monkeypatch.setattr(schedule, "_quota_date", lambda _timezone: "20260720")
    monkeypatch.setattr(schedule, "_is_weekday", lambda _timezone: True)
    monkeypatch.setattr(schedule, "_now", lambda: "2026-07-20T00:05:00+08:00")

    def factory(*_args: object, **_kwargs: object) -> _FakeLedger:
        return ledger

    schedule.coordinate(args, ledger_factory=factory)
    evidence = tmp_path / "top200-manifest.json"
    evidence.write_text('{"dates":["20260720"]}', encoding="utf-8")
    _write_marker(args.raw_completeness_root, evidence)

    assert schedule.release_ready(args, ledger_factory=factory) == 0
    assert schedule.release_ready(args, ledger_factory=factory) == 0
    assert schedule.coordinate(args, ledger_factory=factory) == 0
    assert ledger.released == ["hold-top200_factor_observation"]
    assert "daily_watch20" in ledger.holds
    assert "top200_factor_observation" not in ledger.holds


def test_await_marker_skips_weekend_without_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schedule, "_is_weekday", lambda _timezone: False)
    args = argparse.Namespace(
        raw_completeness_root=tmp_path,
        consumer="top200_factor_observation",
        weekdays_only=True,
        quota_timezone="Asia/Shanghai",
        not_before_local=None,
        deadline_local=None,
        poll_seconds=1,
    )
    assert schedule.await_marker(args) == 0


def test_await_marker_rejects_same_day_stale_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = tmp_path / "top200-manifest.json"
    evidence.write_text('{"dates":["20260718"]}', encoding="utf-8")
    marker = _write_marker(tmp_path / "markers", evidence)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["completed_at"] = "2026-07-20T20:59:59+08:00"
    marker.write_text(json.dumps(payload), encoding="utf-8")
    current = datetime(2026, 7, 20, 21, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(schedule, "_quota_now", lambda _timezone: current)
    args = argparse.Namespace(
        raw_completeness_root=tmp_path / "markers",
        consumer="top200_factor_observation",
        weekdays_only=True,
        quota_timezone="Asia/Shanghai",
        not_before_local="21:00",
        deadline_local=None,
        poll_seconds=1,
    )

    assert schedule.await_marker(args) == schedule.RETRYABLE_EXIT_CODE


def test_release_ready_rejects_marker_older_than_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _quota_args(tmp_path)
    args.raw_completeness_root = tmp_path / "markers"
    args.consumer = "top200_factor_observation"
    args.not_before_local = None
    args.deadline_local = None
    args.poll_seconds = 1
    ledger = _FakeLedger()
    monkeypatch.setattr(schedule, "_quota_date", lambda _timezone: "20260720")
    monkeypatch.setattr(schedule, "_is_weekday", lambda _timezone: True)

    def factory(*_args: object, **_kwargs: object) -> _FakeLedger:
        return ledger

    schedule.coordinate(args, ledger_factory=factory)
    state_path = next((tmp_path / "schedule" / "20260720").glob("*.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["holds"]["top200_factor_observation"]["created_at"] = "2026-07-20T22:00:00+08:00"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    evidence = tmp_path / "top200-manifest.json"
    evidence.write_text('{"dates":["20260720"]}', encoding="utf-8")
    _write_marker(args.raw_completeness_root, evidence)

    with pytest.raises(ValueError, match="predates its request hold"):
        schedule.release_ready(args, ledger_factory=factory)


def test_renderer_installs_every_template_without_unresolved_placeholders(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "tushare-minute-one.service").write_text(
        "ExecStart=@MDP_DIR@/run @CAMPAIGN_MANIFEST@ "
        "@DATA_PLATFORM_ROOT@ @HOME@ @HERMES_LOGS_DIR@\n",
        encoding="utf-8",
    )
    (templates / "tushare-minute-two.timer").write_text(
        "Description=no placeholders\n", encoding="utf-8"
    )
    args = argparse.Namespace(
        template_root=templates,
        home=tmp_path / "home",
        mdp_dir=tmp_path / "mdp",
        data_platform_root=tmp_path / "data",
        campaign_manifest=tmp_path / "campaign" / "manifest.json",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "units",
        dry_run=False,
    )

    rendered = renderer.render_units(args)

    assert {path.name for path in rendered} == {
        "tushare-minute-one.service",
        "tushare-minute-two.timer",
    }
    assert "@" not in (tmp_path / "units" / "tushare-minute-one.service").read_text(
        encoding="utf-8"
    )


def test_renderer_preserves_stable_mdp_symlink_in_runtime_paths(tmp_path: Path) -> None:
    version = tmp_path / "releases" / "abc" / "market-data-platform"
    version.mkdir(parents=True)
    stable = tmp_path / "current"
    stable.symlink_to(version, target_is_directory=True)
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "tushare-minute-one.service").write_text(
        "WorkingDirectory=@MDP_DIR@\n", encoding="utf-8"
    )
    args = argparse.Namespace(
        template_root=templates,
        home=tmp_path / "home",
        mdp_dir=stable,
        data_platform_root=tmp_path / "data",
        campaign_manifest=tmp_path / "campaign" / "manifest.json",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "units",
        dry_run=False,
    )

    renderer.render_units(args)

    rendered = (tmp_path / "units" / "tushare-minute-one.service").read_text(encoding="utf-8")
    assert str(stable) in rendered
    assert str(version) not in rendered
