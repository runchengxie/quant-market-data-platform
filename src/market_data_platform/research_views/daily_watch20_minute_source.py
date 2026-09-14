"""Canonical minute-source discovery and cache-delta contracts for DailyWatch20."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .daily_watch20_data import DailyWatch20Assets

MINUTE_SOURCE_CONTRACT = "canonical_minute_1m.hive.v1"


@dataclass(frozen=True, slots=True)
class MinutePartitionState:
    """One exact-date source partition and its content-independent file identity."""

    trade_date: str
    fingerprint: str
    files: tuple[Path, ...]
    file_metadata: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class MinuteSourceCatalog:
    """Resolved canonical/overlay minute partitions for one requested date range."""

    source_contract: str
    start_date: str
    end_date: str
    canonical_root: Path
    overlay_roots: tuple[Path, ...]
    minute_version: str
    coverage_path: Path | None
    coverage_sha256: str | None
    partitions: Mapping[str, MinutePartitionState]
    minute_dataset: str = "legacy"
    provider: str = "guan"

    @property
    def dates(self) -> tuple[str, ...]:
        return tuple(sorted(self.partitions))

    @property
    def files(self) -> tuple[Path, ...]:
        return tuple(
            path for trade_date in self.dates for path in self.partitions[trade_date].files
        )

    def source_record(self) -> dict[str, Any]:
        return {
            "source_contract": self.source_contract,
            "minute_version": self.minute_version,
            "minute_dataset": self.minute_dataset,
            "provider": self.provider,
            "minute_path": str(self.canonical_root),
            "coverage_path": str(self.coverage_path) if self.coverage_path else None,
            "coverage_sha256": self.coverage_sha256,
            "overlay_roots": [str(root) for root in self.overlay_roots],
        }

    def partition_records(self) -> dict[str, dict[str, Any]]:
        source = self.source_record()
        snapshot_id = hashlib.sha256(
            json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        return {
            trade_date: {
                "source_snapshot_id": snapshot_id,
                "sources": sorted(
                    {(str(item["source_kind"]), str(item["root"])) for item in state.file_metadata}
                ),
                "fingerprint": state.fingerprint,
                "files": list(state.file_metadata),
            }
            for trade_date, state in sorted(self.partitions.items())
        }


@dataclass(frozen=True, slots=True)
class MinuteCacheDelta:
    """Dates that must be recomputed or removed from a derived minute cache."""

    changed_dates: frozenset[str]
    removed_dates: frozenset[str]
    reused_dates: frozenset[str]

    @property
    def rebuild_required(self) -> bool:
        return bool(self.changed_dates or self.removed_dates)


def _date_key(value: object, *, field: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"{field} must be YYYYMMDD")
    return text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_overlay_roots(roots: Sequence[str | Path]) -> tuple[Path, ...]:
    resolved = tuple(sorted({Path(root).expanduser().resolve() for root in roots}))
    for root in resolved:
        if not root.is_dir():
            raise FileNotFoundError(f"Minute overlay root does not exist: {root}")
    return resolved


def _root_partition_states(
    root: Path,
    *,
    source_kind: str,
    start_date: str,
    end_date: str,
) -> dict[str, MinutePartitionState]:
    states: dict[str, MinutePartitionState] = {}
    for directory in sorted(root.glob("trade_date=*")):
        trade_date = directory.name.removeprefix("trade_date=")
        if len(trade_date) != 8 or not trade_date.isdigit():
            continue
        if not start_date <= trade_date <= end_date:
            continue
        files = tuple(sorted(directory.glob("part-*.parquet")))
        if not files:
            raise RuntimeError(f"Minute partition has no parquet parts: {directory}")
        metadata: list[dict[str, Any]] = []
        for path in files:
            stat = path.stat()
            metadata.append(
                {
                    "root": str(root),
                    "source_kind": source_kind,
                    "relative_path": str(path.relative_to(root)),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        states[trade_date] = MinutePartitionState(
            trade_date=trade_date,
            fingerprint=hashlib.sha256(encoded).hexdigest(),
            files=files,
            file_metadata=tuple(metadata),
        )
    return states


def scan_daily_watch20_minute_sources(
    assets: DailyWatch20Assets,
    *,
    start_date: str,
    end_date: str,
    overlay_roots: Sequence[str | Path] = (),
) -> MinuteSourceCatalog:
    """Resolve exact-date minute partitions; complete overlay sets replace canonical dates."""

    start = _date_key(start_date, field="start_date")
    end = _date_key(end_date, field="end_date")
    if end < start:
        raise ValueError("end_date must not precede start_date")
    overlays = _normalise_overlay_roots(overlay_roots)
    canonical = _root_partition_states(
        assets.minute_current,
        source_kind=("canonical" if assets.minute_dataset == "legacy" else "tushare_operational"),
        start_date=start,
        end_date=end,
    )
    overlays_by_date: dict[str, list[MinutePartitionState]] = {}
    for root in overlays:
        for trade_date, state in _root_partition_states(
            root,
            source_kind="overlay",
            start_date=start,
            end_date=end,
        ).items():
            overlays_by_date.setdefault(trade_date, []).append(state)

    states: dict[str, MinutePartitionState] = {}
    for trade_date in sorted(set(canonical) | set(overlays_by_date)):
        overlay_states = overlays_by_date.get(trade_date)
        if overlay_states and len(overlay_states) != len(overlays):
            present = {str(state.file_metadata[0]["root"]) for state in overlay_states}
            missing = [str(root) for root in overlays if str(root) not in present]
            raise RuntimeError(
                f"Incomplete minute overlay set for {trade_date}; missing roots: {missing}"
            )
        if overlay_states:
            selected = overlay_states
        else:
            canonical_state = canonical.get(trade_date)
            if canonical_state is None:
                raise RuntimeError(f"No canonical minute partition for {trade_date}")
            selected = [canonical_state]
        files = tuple(path for state in selected for path in state.files)
        metadata = tuple(item for state in selected for item in state.file_metadata)
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        states[trade_date] = MinutePartitionState(
            trade_date=trade_date,
            fingerprint=hashlib.sha256(encoded).hexdigest(),
            files=files,
            file_metadata=metadata,
        )
    if not states:
        raise RuntimeError(f"No minute partitions in requested range {start}..{end}")
    coverage = assets.minute_coverage
    return MinuteSourceCatalog(
        source_contract=MINUTE_SOURCE_CONTRACT,
        start_date=start,
        end_date=end,
        canonical_root=assets.minute_current,
        overlay_roots=overlays,
        minute_version=assets.minute_current.name,
        coverage_path=coverage,
        coverage_sha256=_sha256_file(coverage) if coverage else None,
        partitions=states,
        minute_dataset=assets.minute_dataset,
        provider=assets.minute_provider,
    )


def minute_cache_delta(
    catalog: MinuteSourceCatalog,
    cached_partitions: Mapping[str, Mapping[str, Any]] | None,
) -> MinuteCacheDelta:
    """Compare a cache receipt's partition fingerprints with the current source catalog."""

    cached = dict(cached_partitions or {})
    current_dates = set(catalog.partitions)
    cached_dates = set(cached)
    changed = {
        date
        for date, state in catalog.partitions.items()
        if date not in cached or cached[date].get("fingerprint") != state.fingerprint
    }
    # A cached date that is absent from the current source catalog must always be
    # dropped from the derived cache, regardless of where it sits relative to the
    # requested window. The most common case is the 1100-day window rolling forward
    # by one trading day: the oldest cached head (e.g. 20230717) falls just *below*
    # the new catalog.start_date (e.g. 20230718) and would otherwise be retained,
    # causing the frame to disagree with the catalog and fail the exact-match binding.
    # A narrower requested window (cached wider than catalog) is likewise resolved by
    # dropping the now-out-of-window dates, because the derived frame is contractually
    # required to equal the catalog date set exactly. Dropping is cheap (O(dropped));
    # it must NOT degrade to a full rebuild, which would re-pin the head one day ahead
    # and force a 1100-day rebuild on every subsequent run.
    removed = cached_dates - current_dates
    reused = current_dates - changed
    return MinuteCacheDelta(
        changed_dates=frozenset(changed),
        removed_dates=frozenset(removed),
        reused_dates=frozenset(reused),
    )


def cached_source_partitions(receipt: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Extract validated source-partition metadata from a prior derived-cache receipt."""

    if receipt is None:
        return {}
    raw = receipt.get("source_partitions")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for date, entry in raw.items():
        if not isinstance(date, str) or not isinstance(entry, Mapping):
            continue
        fingerprint = entry.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            continue
        result[date] = dict(cast(Mapping[str, Any], entry))
    return result


__all__ = [
    "MINUTE_SOURCE_CONTRACT",
    "MinuteCacheDelta",
    "MinutePartitionState",
    "MinuteSourceCatalog",
    "cached_source_partitions",
    "minute_cache_delta",
    "scan_daily_watch20_minute_sources",
]
