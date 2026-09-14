"""Child-process supervision, command building, and blocker checks for the campaign runner."""

from __future__ import annotations

import fcntl
import json
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from market_data_platform._campaign_common import (
    _AdvanceControls,
    _CommandRunOptions,
    _minute_quota_args,
    _PhaseRunResult,
)
from market_data_platform._campaign_progress import (
    PartitionKey,
    _progress_stop_check,
)


def _active_blockers(services: Sequence[str]) -> list[str]:
    active: list[str] = []
    for service in services:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", service],
            check=False,
            capture_output=True,
            text=True,
        )
        state = result.stdout.strip()
        if state in {"active", "activating", "reloading", "deactivating"}:
            active.append(f"{service}:{state}")
    return active


def _lock_is_held(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("r") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def _resume_command(manifest: dict[str, Any], trade_date: str, data_root: str) -> list[str]:
    config = manifest["config"]
    command = [
        config["marketdata_bin"],
        "tushare",
        "mirror-a-share-mins",
        "--start-date",
        trade_date,
        "--end-date",
        trade_date,
        "--out-dir",
        data_root,
        "--skip-existing",
        "--batch-size",
        str(config["batch_size"]),
        "--cooldown-seconds",
        str(config["cooldown_seconds"]),
        "--token-env",
        config["token_env"],
        "--retry-attempts",
        str(config["retry_attempts"]),
        "--retry-sleep-seconds",
        "2",
        "--retry-max-sleep-seconds",
        "30",
        "--quota-cooldown-seconds",
        str(config["quota_cooldown_seconds"]),
    ]
    return command + _minute_quota_args(config)


def _lane_command(manifest: dict[str, Any], lane: dict[str, Any]) -> list[str]:
    command = [
        manifest["config"]["marketdata_bin"],
        "tushare",
        "run-a-share-minute-backfill",
        "--plan",
        lane["plan_path"],
        "--receipt",
        lane["receipt_path"],
        "--token-env",
        manifest["config"]["token_env"],
    ]
    exception_path = manifest["config"].get("provider_no_data_exceptions_path")
    if exception_path:
        command.extend(["--provider-no-data-exceptions", str(exception_path)])
    return command + _minute_quota_args(manifest["config"])


def _dry_run_commands(commands: list[tuple[str, list[str]]]) -> dict[str, int]:
    for lane_name, command in commands:
        print(json.dumps({"lane": lane_name, "command": command}, ensure_ascii=False))
    return {lane_name: 0 for lane_name, _ in commands}


def _stop_started_children(
    children: Sequence[tuple[str, subprocess.Popen[Any]]], *, grace_seconds: float
) -> None:
    alive = [child for _lane_name, child in children if child.poll() is None]
    for child in alive:
        try:
            child.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace_seconds
    for child in alive:
        try:
            child.wait(timeout=max(0.0, deadline - time.monotonic()))
            continue
        except subprocess.TimeoutExpired:
            pass
        try:
            child.terminate()
            child.wait(timeout=30.0)
            continue
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            child.kill()
            child.wait()
        except ProcessLookupError:
            pass


class _CommandSupervisor:
    """Own child lifecycle, stop polling, and signal escalation for one phase."""

    def __init__(
        self,
        manifest: dict[str, Any],
        commands: list[tuple[str, list[str]]],
        options: _CommandRunOptions,
    ) -> None:
        self.repo_root = str(manifest["config"]["repo_root"])
        self.commands = commands
        self.options = options
        self.children: list[tuple[str, subprocess.Popen[Any]]] = []
        self.external_interrupted = False
        self.internal_stop_reason: str | None = None
        self.accounting_error: Exception | None = None
        self.interrupt_started: float | None = None
        self.terminate_started: float | None = None
        self.intentional_checkpoint_lanes: set[str] = set()
        self.lane_drain_started: dict[str, float] = {}
        self.lane_terminate_started: dict[str, float] = {}

    def _alive_named_children(self) -> list[tuple[str, subprocess.Popen[Any]]]:
        return [(name, child) for name, child in self.children if child.poll() is None]

    def _alive_children(self) -> list[subprocess.Popen[Any]]:
        return [child for _lane_name, child in self._alive_named_children()]

    def _signal_alive(self, signum: int) -> None:
        for child in self._alive_children():
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    def _request_interrupt(self) -> None:
        if self.interrupt_started is None:
            self.interrupt_started = time.monotonic()
            self._signal_alive(signal.SIGINT)

    def _maybe_checkpoint_one_lane(self) -> None:
        check = self.options.single_lane_check
        if (
            check is None
            or self.internal_stop_reason is not None
            or self.intentional_checkpoint_lanes
        ):
            return
        alive = self._alive_named_children()
        if len(alive) <= 1 or not check():
            return
        lane_name, child = sorted(alive, key=lambda item: item[0])[-1]
        try:
            child.send_signal(signal.SIGINT)
        except ProcessLookupError:
            return
        self.intentional_checkpoint_lanes.add(lane_name)
        self.lane_drain_started[lane_name] = time.monotonic()

    def _check_stop(self) -> None:
        if self.accounting_error is not None:
            return
        try:
            if self.options.heartbeat is not None:
                self.options.heartbeat()
            if self.options.hard_stop_check is not None and self.options.hard_stop_check():
                self.internal_stop_reason = "schedule_hard_stop"
                self._signal_alive(signal.SIGKILL)
                return
            self._maybe_checkpoint_one_lane()
            if self.options.stop_check is not None and self.internal_stop_reason is None:
                self.internal_stop_reason = self.options.stop_check()
        except Exception as exc:
            self.accounting_error = exc
            self.internal_stop_reason = "accounting_error"
        if self.internal_stop_reason is not None:
            self._request_interrupt()

    def _forward_interrupt(self, _signum: int, _frame: Any) -> None:
        self.external_interrupted = True
        self._request_interrupt()

    def _wait_for_stagger(self) -> None:
        deadline = time.monotonic() + self.options.stagger_seconds
        while time.monotonic() < deadline:
            self._check_stop()
            if self.external_interrupted or self.internal_stop_reason is not None:
                return
            remaining = deadline - time.monotonic()
            time.sleep(min(self.options.poll_seconds, max(0.0, remaining)))

    def _start_children(self) -> None:
        for index, (lane_name, command) in enumerate(self.commands):
            if index:
                self._wait_for_stagger()
            self._check_stop()
            if self.external_interrupted or self.internal_stop_reason is not None:
                return
            child = subprocess.Popen(command, cwd=self.repo_root)
            self.children.append((lane_name, child))

    def _escalate_lane_drains(self, now: float) -> None:
        for lane_name, child in self._alive_named_children():
            drain_started = self.lane_drain_started.get(lane_name)
            if drain_started is None:
                continue
            if (
                now - drain_started >= self.options.interrupt_grace_seconds
                and lane_name not in self.lane_terminate_started
            ):
                self.lane_terminate_started[lane_name] = now
                try:
                    child.send_signal(signal.SIGTERM)
                except ProcessLookupError:
                    continue
            terminate_at = self.lane_terminate_started.get(lane_name)
            if terminate_at is not None and now - terminate_at >= 30.0:
                try:
                    child.send_signal(signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def _escalate_global_stop(self, now: float) -> None:
        if (
            self.interrupt_started is not None
            and now - self.interrupt_started >= self.options.interrupt_grace_seconds
            and self.terminate_started is None
        ):
            self.terminate_started = now
            self._signal_alive(signal.SIGTERM)
        if self.terminate_started is not None and now - self.terminate_started >= 30.0:
            self._signal_alive(signal.SIGKILL)

    def _wait_for_children(self) -> None:
        while self._alive_children():
            self._check_stop()
            if self.external_interrupted:
                self._request_interrupt()
            now = time.monotonic()
            self._escalate_lane_drains(now)
            self._escalate_global_stop(now)
            if self._alive_children():
                interval = (
                    min(self.options.poll_seconds, 1.0)
                    if self.interrupt_started is not None
                    else self.options.poll_seconds
                )
                time.sleep(interval)

    def _run_supervised(self) -> dict[str, int]:
        previous = {
            signum: signal.signal(signum, self._forward_interrupt)
            for signum in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            self._start_children()
            self._wait_for_children()
            return {lane_name: child.wait() for lane_name, child in self.children}
        except BaseException:
            _stop_started_children(
                self.children,
                grace_seconds=self.options.interrupt_grace_seconds,
            )
            raise
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def run(self) -> _PhaseRunResult:
        exit_codes = self._run_supervised()
        if self.accounting_error is not None:
            raise self.accounting_error
        if self.external_interrupted:
            raise KeyboardInterrupt
        return _PhaseRunResult(
            exit_codes,
            self.internal_stop_reason,
            tuple(sorted(self.intentional_checkpoint_lanes)),
        )


def _run_commands(
    manifest: dict[str, Any],
    commands: list[tuple[str, list[str]]],
    *,
    options: _CommandRunOptions | None = None,
    **legacy_options: Any,
) -> _PhaseRunResult:
    if options is not None and legacy_options:
        raise TypeError("Pass command options either as a context or keyword values, not both")
    resolved = options or _CommandRunOptions(**legacy_options)
    if resolved.poll_seconds <= 0:
        raise ValueError("poll_seconds must be greater than 0")
    if resolved.interrupt_grace_seconds < 0:
        raise ValueError("interrupt_grace_seconds must be non-negative")
    if resolved.dry_run:
        return _PhaseRunResult(_dry_run_commands(commands))
    return _CommandSupervisor(manifest, commands, resolved).run()


def _run_phase(  # noqa: PLR0913
    manifest: dict[str, Any],
    phase: dict[str, Any],
    *,
    dry_run: bool,
    stop_check: Callable[[], str | None] | None = None,
    poll_seconds: float = 10.0,
    interrupt_grace_seconds: float = 300.0,
    heartbeat: Callable[[], None] | None = None,
    hard_stop_check: Callable[[], bool] | None = None,
    single_lane_check: Callable[[], bool] | None = None,
) -> _PhaseRunResult:
    commands = [
        (lane_name, _lane_command(manifest, lane))
        for lane_name, lane in sorted(phase["lanes"].items())
    ]
    return _run_commands(
        manifest,
        commands,
        dry_run=dry_run,
        stagger_seconds=float(manifest["config"]["stagger_seconds"]),
        stop_check=stop_check,
        poll_seconds=poll_seconds,
        interrupt_grace_seconds=interrupt_grace_seconds,
        heartbeat=heartbeat,
        hard_stop_check=hard_stop_check,
        single_lane_check=single_lane_check,
    )


def _combined_stop_check(
    controls: _AdvanceControls,
    active_keys: tuple[PartitionKey, ...],
) -> Callable[[], str | None] | None:
    progress_check = (
        _progress_stop_check(controls.progress, active_keys)
        if controls.progress is not None
        else None
    )
    if controls.extra_stop_check is None:
        return progress_check

    def check() -> str | None:
        extra_reason = (
            controls.extra_stop_check() if controls.extra_stop_check is not None else None
        )
        return extra_reason or (progress_check() if progress_check is not None else None)

    return check


__all__ = [
    "_CommandSupervisor",
    "_active_blockers",
    "_combined_stop_check",
    "_dry_run_commands",
    "_lane_command",
    "_lock_is_held",
    "_resume_command",
    "_run_commands",
    "_run_phase",
    "_stop_started_children",
]
