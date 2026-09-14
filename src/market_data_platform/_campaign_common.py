"""Shared constants, exceptions, dataclasses, and I/O helpers for the campaign runner.

This module intentionally has no intra-package dependency on the other
``_campaign_*`` submodules so it can be imported first to form the dependency
base for the rest of the split.
"""

from __future__ import annotations

import json
import os
import signal
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from market_data_platform._tushare_minute_campaign_quota_args import (
    minute_quota_args as _minute_quota_args,
)
from market_data_platform._tushare_minute_campaign_readiness import (
    file_sha256 as _sha256,
)
from market_data_platform._tushare_minute_campaign_readiness import (
    readiness_summary_is_valid as _readiness_summary_is_valid,
)
from market_data_platform.providers.tushare_a_share_mins import (
    COMPLETENESS_FILENAME,
    validate_complete_minute_partition,
)

SCHEMA_VERSION = "tushare.minute_replacement_campaign.v1"
LEDGER_SCHEMA_VERSION = "tushare.minute_replacement_campaign.ledger.v1"
READINESS_SCHEMA_VERSION = "tushare.minute_replacement_campaign.readiness.v1"

RETRYABLE_EXIT_CODE = 75
FATAL_EXIT_CODE = 70
EXPECTED_CHECKPOINT_EXIT_CODES = {0, 130, -signal.SIGINT}
NO_PROGRESS_REASONS = {"phase_incomplete", "preflight_incomplete", "retryable_lane_error"}

PartitionKey = tuple[Path, str]
AdvanceState = Literal[
    "campaign_complete",
    "day_complete",
    "dry_run",
    "phase_incomplete",
    "stopped",
]


class CampaignAccountingError(RuntimeError):
    """Raised when persisted campaign progress cannot be measured safely."""


class CampaignRetryableError(RuntimeError):
    """Raised when a later safe-window tick may make progress."""


class CampaignFatalError(RuntimeError):
    """Raised when immutable or persisted campaign evidence is unsafe."""


@dataclass(frozen=True)
class _RunWindow:
    timezone: ZoneInfo
    start: datetime_time
    drain: datetime_time
    stop: datetime_time

    def _today(self, value: datetime_time, now: datetime) -> datetime:
        return datetime.combine(now.date(), value, tzinfo=self.timezone)

    def bounds(self, now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
        current = now or datetime.now(self.timezone)
        return (
            self._today(self.start, current),
            self._today(self.drain, current),
            self._today(self.stop, current),
        )

    def start_state(self, now: datetime | None = None) -> str | None:
        current = now or datetime.now(self.timezone)
        start, drain, _stop = self.bounds(current)
        if current < start:
            return "window_not_open"
        if current >= drain:
            return "window_closed"
        return None

    def stop_reason(self, now: datetime | None = None) -> str | None:
        current = now or datetime.now(self.timezone)
        _start, drain, stop = self.bounds(current)
        if current >= stop:
            return "schedule_hard_stop"
        if current >= drain:
            return "schedule_drain"
        return None

    def hard_stop_due(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(self.timezone)
        return current >= self.bounds(current)[2]

    def next_start(self, now: datetime | None = None) -> datetime:
        current = now or datetime.now(self.timezone)
        start, _drain, _stop = self.bounds(current)
        return start if current < start else start + timedelta(days=1)


@dataclass(frozen=True)
class _PhaseRunResult:
    exit_codes: dict[str, int]
    stop_reason: str | None = None
    intentional_checkpoint_lanes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AdvanceResult:
    state: AdvanceState
    day_key: str | None = None
    phase_name: str | None = None
    stop_reason: str | None = None


@dataclass(frozen=True)
class _CommandRunOptions:
    dry_run: bool = False
    stagger_seconds: float = 0.0
    stop_check: Callable[[], str | None] | None = None
    poll_seconds: float = 10.0
    interrupt_grace_seconds: float = 300.0
    heartbeat: Callable[[], None] | None = None
    hard_stop_check: Callable[[], bool] | None = None
    single_lane_check: Callable[[], bool] | None = None


@dataclass(frozen=True)
class _AdvanceControls:
    dry_run: bool = False
    progress: Any | None = None
    poll_seconds: float = 10.0
    interrupt_grace_seconds: float = 300.0
    extra_stop_check: Callable[[], str | None] | None = None
    heartbeat: Callable[[], None] | None = None
    hard_stop_check: Callable[[], bool] | None = None
    single_lane_threshold_rows: int = 0


@dataclass(frozen=True)
class _AdvanceContext:
    manifest: dict[str, Any]
    ledger: dict[str, Any]
    ledger_path: Path
    controls: _AdvanceControls


@dataclass(frozen=True)
class _PhaseExecution:
    day_key: str
    day_ledger: dict[str, Any]
    phase: dict[str, Any]
    execution_phase: dict[str, Any]
    single_lane_mode: bool
    active_keys: tuple[PartitionKey, ...]
    run_result: _PhaseRunResult


@dataclass(frozen=True)
class _PreflightAttempt:
    trade_date: str
    data_root: Path
    receipt: dict[str, Any] | None
    run_result: _PhaseRunResult


@dataclass(frozen=True)
class _FinishMeasurement:
    ending_inventory: dict[str, Any]
    measurement_error: str | None
    elapsed_seconds: float
    new_rows: int


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _quota_window_clock(timezone_name: str) -> tuple[str, float]:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown quota timezone: {timezone_name}") from exc
    now = datetime.now(timezone)
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.date().isoformat(), (next_midnight - now).total_seconds()


def _parse_clock(value: str, *, option: str) -> datetime_time:
    try:
        parsed = datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"{option} must use HH:MM in 24-hour time: {value}") from exc
    return parsed


def _build_run_window(args: Any, *, timezone_name: str) -> _RunWindow | None:
    values = (
        getattr(args, "run_window_start", None),
        getattr(args, "run_window_drain", None),
        getattr(args, "run_window_stop", None),
    )
    if not any(values):
        return None
    if not all(values):
        raise ValueError(
            "run window requires --run-window-start, --run-window-drain, and --run-window-stop"
        )
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown run-window timezone: {timezone_name}") from exc
    start = _parse_clock(str(values[0]), option="--run-window-start")
    drain = _parse_clock(str(values[1]), option="--run-window-drain")
    stop = _parse_clock(str(values[2]), option="--run-window-stop")
    if not start < drain < stop:
        raise ValueError("run window must satisfy start < drain < stop within one local day")
    return _RunWindow(timezone=timezone, start=start, drain=drain, stop=stop)


def _load_ledger(path: Path, manifest_path: Path) -> dict[str, Any]:
    if path.exists():
        ledger = _read_json(path)
        if not isinstance(ledger, dict) or ledger.get("schema_version") != LEDGER_SCHEMA_VERSION:
            raise CampaignAccountingError(f"Unsupported campaign ledger: {path}")
        if ledger.get("manifest_path") != str(manifest_path) or ledger.get(
            "manifest_sha256"
        ) != _sha256(manifest_path):
            raise CampaignAccountingError(
                f"Campaign ledger does not match the immutable manifest: {path}"
            )
        if not isinstance(ledger.get("events"), list) or not isinstance(ledger.get("days"), dict):
            raise CampaignAccountingError(f"Campaign ledger has invalid collections: {path}")
        return ledger
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "created_at": _now(),
        "updated_at": _now(),
        "preflight": {},
        "days": {},
        "events": [],
    }


def _write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    ledger["updated_at"] = _now()
    _atomic_write_json(path, ledger)


__all__ = [
    "AdvanceState",
    "COMPLETENESS_FILENAME",
    "EXPECTED_CHECKPOINT_EXIT_CODES",
    "FATAL_EXIT_CODE",
    "LEDGER_SCHEMA_VERSION",
    "NO_PROGRESS_REASONS",
    "PartitionKey",
    "READINESS_SCHEMA_VERSION",
    "RETRYABLE_EXIT_CODE",
    "SCHEMA_VERSION",
    "CampaignAccountingError",
    "CampaignFatalError",
    "CampaignRetryableError",
    "_AdvanceContext",
    "_AdvanceControls",
    "_AdvanceResult",
    "_CommandRunOptions",
    "_FinishMeasurement",
    "_PhaseExecution",
    "_PhaseRunResult",
    "_PreflightAttempt",
    "_RunWindow",
    "_atomic_write_json",
    "_build_run_window",
    "_load_ledger",
    "_minute_quota_args",
    "_now",
    "_parse_clock",
    "_quota_window_clock",
    "_read_json",
    "_readiness_summary_is_valid",
    "_sha256",
    "_write_ledger",
    "validate_complete_minute_partition",
]
