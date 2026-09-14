"""Partition-key helpers and monotonic progress tracking for the campaign runner."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from market_data_platform._campaign_common import (
    COMPLETENESS_FILENAME,
    CampaignAccountingError,
    PartitionKey,
    _read_json,
)


def _partition_key(data_root: str | Path, trade_date: str) -> PartitionKey:
    return Path(data_root).expanduser().resolve(), str(trade_date)


def _phase_partition_keys(phase: dict[str, Any]) -> tuple[PartitionKey, ...]:
    return tuple(
        _partition_key(lane["data_root"], trade_date)
        for _lane_name, lane in sorted(phase["lanes"].items())
        for trade_date in lane["dates"]
    )


def _campaign_partition_keys(manifest: dict[str, Any]) -> tuple[PartitionKey, ...]:
    roots_by_date: dict[str, Path] = {}

    def add(key: PartitionKey) -> None:
        data_root, trade_date = key
        previous = roots_by_date.get(trade_date)
        if previous is not None and previous != data_root:
            raise CampaignAccountingError(
                f"Campaign date {trade_date} has more than one mutable data root: "
                f"{previous}, {data_root}"
            )
        roots_by_date[trade_date] = data_root

    for trade_date, item in sorted(manifest["baseline"]["partial"].items()):
        add(_partition_key(item["data_root"], trade_date))
    for day in manifest["days"]:
        for phase in day["phases"]:
            for key in _phase_partition_keys(phase):
                add(key)
    return tuple((roots_by_date[trade_date], trade_date) for trade_date in sorted(roots_by_date))


def _checkpoint_rows(key: PartitionKey) -> int:
    data_root, trade_date = key
    sidecar = data_root / f"trade_date={trade_date}" / COMPLETENESS_FILENAME
    if not sidecar.exists():
        return 0
    payload = _read_json(sidecar)
    if not isinstance(payload, dict):
        raise CampaignAccountingError(f"Minute sidecar must contain an object: {sidecar}")
    if str(payload.get("trade_date", "")) != trade_date:
        raise CampaignAccountingError(f"Minute sidecar trade_date mismatch: {sidecar}")
    if payload.get("status") not in {"partial", "complete"}:
        raise CampaignAccountingError(f"Minute sidecar has invalid status: {sidecar}")
    partition = payload.get("partition")
    if partition is None:
        if payload.get("completed_symbols"):
            raise CampaignAccountingError(
                f"Minute sidecar has completed symbols without partition rows: {sidecar}"
            )
        return 0
    if not isinstance(partition, dict):
        raise CampaignAccountingError(f"Minute sidecar partition must be an object: {sidecar}")
    rows = partition.get("rows", 0)
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
        raise CampaignAccountingError(f"Minute sidecar has invalid partition rows: {sidecar}")
    return rows


class _CampaignProgress:
    """Track monotonic persisted rows added by one budgeted invocation."""

    def __init__(
        self,
        manifest: dict[str, Any],
        *,
        max_new_rows: int,
        max_runtime_seconds: float,
    ) -> None:
        if max_new_rows < 1:
            raise ValueError("max_new_rows must be at least 1")
        if max_runtime_seconds < 0:
            raise ValueError("max_runtime_seconds must be non-negative")
        self.keys = _campaign_partition_keys(manifest)
        self.baseline = {key: _checkpoint_rows(key) for key in self.keys}
        self.observed = dict(self.baseline)
        self.max_new_rows = max_new_rows
        self.carried_new_rows = 0
        self.started_monotonic = time.monotonic()
        self.deadline_monotonic = self.started_monotonic + max_runtime_seconds

    @property
    def baseline_rows(self) -> int:
        return sum(self.baseline.values())

    @property
    def new_rows(self) -> int:
        return sum(self.observed[key] - self.baseline[key] for key in self.keys)

    @property
    def quota_window_new_rows(self) -> int:
        return self.carried_new_rows + self.new_rows

    def carry_forward(self, rows: int) -> None:
        if rows < 0:
            raise CampaignAccountingError("Carried quota-window rows must be non-negative")
        self.carried_new_rows = rows

    def refresh(self, keys: Iterable[PartitionKey] | None = None) -> int:
        selected = self.keys if keys is None else tuple(keys)
        for key in selected:
            if key not in self.observed:
                raise CampaignAccountingError(f"Unregistered campaign partition: {key}")
            rows = _checkpoint_rows(key)
            if rows < self.observed[key]:
                data_root, trade_date = key
                raise CampaignAccountingError(
                    "Campaign partition rows moved backwards for "
                    f"{trade_date} at {data_root}: {self.observed[key]} -> {rows}"
                )
            self.observed[key] = rows
        return self.new_rows

    def stop_reason(self, keys: Iterable[PartitionKey] = ()) -> str | None:
        if time.monotonic() >= self.deadline_monotonic:
            return "runtime"
        self.refresh(keys)
        if self.quota_window_new_rows >= self.max_new_rows:
            return "row_budget"
        return None


def _progress_stop_check(
    progress: _CampaignProgress, keys: Iterable[PartitionKey]
) -> Callable[[], str | None]:
    active_keys = tuple(keys)

    def check() -> str | None:
        return progress.stop_reason(active_keys)

    return check


__all__ = [
    "_CampaignProgress",
    "_campaign_partition_keys",
    "_checkpoint_rows",
    "_partition_key",
    "_phase_partition_keys",
    "_progress_stop_check",
]
