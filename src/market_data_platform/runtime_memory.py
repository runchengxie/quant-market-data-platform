from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MEMORY_SOFT_AVAILABLE_MB = 2048.0
DEFAULT_MEMORY_HARD_AVAILABLE_MB = 1024.0
DEFAULT_AUTO_MEMORY_FALLBACK_MB = 2048
DEFAULT_AUTO_MEMORY_CAP_MB = 16384
DEFAULT_AUTO_MEMORY_ROUND_MB = 256
DEFAULT_MIN_BATCH_ROWS = 4096
DEFAULT_TARGET_BATCH_ROWS = 65536
DEFAULT_MAX_BATCH_ROWS = 131072

_MEMORY_VALUE_PATTERN = re.compile(
    r"^\s*(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>B|KB|KIB|MB|MIB|GB|GIB|TB|TIB)?\s*$",
    re.IGNORECASE,
)
_MEMORY_UNIT_TO_MB = {
    None: 1.0,
    "B": 1.0 / 1024 / 1024,
    "KB": 1.0 / 1024,
    "KIB": 1.0 / 1024,
    "MB": 1.0,
    "MIB": 1.0,
    "GB": 1024.0,
    "GIB": 1024.0,
    "TB": 1024.0 * 1024.0,
    "TIB": 1024.0 * 1024.0,
}


@dataclass(frozen=True)
class MemorySnapshot:
    """Effective process memory plus optional source telemetry in MiB."""

    available_mb: float | None
    rss_mb: float | None
    total_mb: float | None = None
    system_available_mb: float | None = None
    system_total_mb: float | None = None
    cgroup_available_mb: float | None = None
    cgroup_limit_mb: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        payload = {
            "available_mb": self.available_mb,
            "rss_mb": self.rss_mb,
            "total_mb": self.total_mb,
        }
        source_values = (
            self.system_available_mb,
            self.system_total_mb,
            self.cgroup_available_mb,
            self.cgroup_limit_mb,
        )
        if any(value is not None for value in source_values):
            payload.update(
                {
                    "system_available_mb": self.system_available_mb,
                    "system_total_mb": self.system_total_mb,
                    "cgroup_available_mb": self.cgroup_available_mb,
                    "cgroup_limit_mb": self.cgroup_limit_mb,
                }
            )
        return payload


@dataclass(frozen=True)
class MemoryPolicy:
    soft_available_mb: float | None = DEFAULT_MEMORY_SOFT_AVAILABLE_MB
    hard_available_mb: float | None = DEFAULT_MEMORY_HARD_AVAILABLE_MB
    min_batch_rows: int = DEFAULT_MIN_BATCH_ROWS
    target_batch_rows: int = DEFAULT_TARGET_BATCH_ROWS
    max_batch_rows: int = DEFAULT_MAX_BATCH_ROWS

    def __post_init__(self) -> None:
        soft = self.soft_available_mb
        hard = self.hard_available_mb
        if soft is not None and soft <= 0:
            object.__setattr__(self, "soft_available_mb", None)
        if hard is not None and hard <= 0:
            object.__setattr__(self, "hard_available_mb", None)
        soft = self.soft_available_mb
        hard = self.hard_available_mb
        if soft is not None and hard is not None and soft < hard:
            raise ValueError("memory soft available limit must be >= hard available limit.")
        minimum = max(1, int(self.min_batch_rows))
        target = max(minimum, int(self.target_batch_rows))
        maximum = max(target, int(self.max_batch_rows))
        object.__setattr__(self, "min_batch_rows", minimum)
        object.__setattr__(self, "target_batch_rows", target)
        object.__setattr__(self, "max_batch_rows", maximum)

    def should_flush(self, snapshot: MemorySnapshot) -> bool:
        return (
            self.soft_available_mb is not None
            and snapshot.available_mb is not None
            and snapshot.available_mb < self.soft_available_mb
        )

    def hard_exceeded(self, snapshot: MemorySnapshot) -> bool:
        return (
            self.hard_available_mb is not None
            and snapshot.available_mb is not None
            and snapshot.available_mb < self.hard_available_mb
        )

    def require_safe(self, *, label: str) -> MemorySnapshot:
        snapshot = read_memory_snapshot()
        if self.hard_exceeded(snapshot):
            raise MemoryError(
                f"{label} stopped because available memory is below hard limit: "
                f"available_mb={snapshot.available_mb:.1f} "
                f"rss_mb={snapshot.rss_mb} "
                f"hard_available_mb={self.hard_available_mb:.1f}."
            )
        return snapshot

    def choose_batch_rows(
        self,
        snapshot: MemorySnapshot,
        *,
        current_rows: int | None = None,
        estimated_bytes_per_row: float | None = None,
    ) -> int:
        if self.hard_exceeded(snapshot):
            raise MemoryError(
                "batch sizing stopped because available memory is below hard limit: "
                f"available_mb={snapshot.available_mb:.1f} "
                f"rss_mb={snapshot.rss_mb} "
                f"hard_available_mb={self.hard_available_mb:.1f}."
            )
        rows = self.target_batch_rows if current_rows is None else int(current_rows)
        rows = min(self.max_batch_rows, max(self.min_batch_rows, rows))
        if snapshot.available_mb is None:
            return rows
        if self.should_flush(snapshot):
            return max(self.min_batch_rows, rows // 2)
        if estimated_bytes_per_row and estimated_bytes_per_row > 0:
            available_bytes = snapshot.available_mb * 1024 * 1024
            memory_budget = available_bytes * 0.05
            estimated_rows = int(memory_budget / estimated_bytes_per_row)
            return min(self.max_batch_rows, max(self.min_batch_rows, estimated_rows))
        return rows

    def to_dict(self) -> dict[str, float | None]:
        return {
            "soft_available_mb": self.soft_available_mb,
            "hard_available_mb": self.hard_available_mb,
        }

    def batch_rows_to_dict(self) -> dict[str, int]:
        return {
            "min_batch_rows": self.min_batch_rows,
            "target_batch_rows": self.target_batch_rows,
            "max_batch_rows": self.max_batch_rows,
        }


def read_memory_snapshot(
    *,
    meminfo_path: str | Path = "/proc/meminfo",
    process_status_path: str | Path = "/proc/self/status",
    proc_cgroup_path: str | Path = "/proc/self/cgroup",
    cgroup_v2_root: str | Path = "/sys/fs/cgroup",
    cgroup_v1_memory_root: str | Path = "/sys/fs/cgroup/memory",
) -> MemorySnapshot:
    """Read an effective memory snapshot bounded by the current cgroup."""
    system_total_bytes, system_available_bytes = _read_meminfo_bytes(meminfo_path)
    cgroup_limit_bytes, cgroup_available_bytes = _read_cgroup_memory_bytes(
        proc_cgroup_path=proc_cgroup_path,
        cgroup_v2_root=cgroup_v2_root,
        cgroup_v1_memory_root=cgroup_v1_memory_root,
    )
    total_bytes = _minimum_known_bytes(system_total_bytes, cgroup_limit_bytes)
    available_bytes = _minimum_known_bytes(system_available_bytes, cgroup_available_bytes)
    if total_bytes is not None and available_bytes is not None:
        available_bytes = min(total_bytes, available_bytes)
    return MemorySnapshot(
        available_mb=_bytes_to_mb(available_bytes),
        rss_mb=_bytes_to_mb(_read_process_rss_bytes(process_status_path)),
        total_mb=_bytes_to_mb(total_bytes),
        system_available_mb=_bytes_to_mb(system_available_bytes),
        system_total_mb=_bytes_to_mb(system_total_bytes),
        cgroup_available_mb=_bytes_to_mb(cgroup_available_bytes),
        cgroup_limit_mb=_bytes_to_mb(cgroup_limit_bytes),
    )


def choose_memory_budget_mb(
    requested: str | int | float | None = "auto",
    *,
    snapshot: MemorySnapshot | None = None,
) -> int:
    """Resolve an explicit or conservative automatic memory budget in MiB.

    Explicit values always win.  Automatic budgets reserve at least 2 GiB or
    25% of effective memory when telemetry allows, and are also bounded by half
    of effective memory, roughly 65% of currently available memory, and a
    16 GiB cap.  The effective values already account for cgroup constraints.
    """
    if requested is not None and not (
        isinstance(requested, str) and requested.strip().lower() == "auto"
    ):
        return _parse_explicit_memory_mb(requested)

    current = read_memory_snapshot() if snapshot is None else snapshot
    total = current.total_mb
    available = current.available_mb
    if total is None and available is None:
        return DEFAULT_AUTO_MEMORY_FALLBACK_MB

    candidates = [float(DEFAULT_AUTO_MEMORY_CAP_MB)]
    if total is not None and total > 0:
        reserve = max(2048.0, total * 0.25)
        candidates.extend((total * 0.5, max(DEFAULT_AUTO_MEMORY_ROUND_MB, total - reserve)))
    else:
        reserve = 2048.0

    if available is not None:
        if available <= 0:
            return 1
        candidates.append(available * 0.65)
        candidates.append(max(DEFAULT_AUTO_MEMORY_ROUND_MB, available - reserve))

    raw_budget = min(candidates)
    rounded = math.floor(raw_budget / DEFAULT_AUTO_MEMORY_ROUND_MB) * DEFAULT_AUTO_MEMORY_ROUND_MB
    if available is not None and available > 0:
        available_floor = math.floor(available / DEFAULT_AUTO_MEMORY_ROUND_MB)
        available_floor *= DEFAULT_AUTO_MEMORY_ROUND_MB
        if available_floor > 0:
            rounded = min(rounded, available_floor)
        else:
            rounded = min(rounded, max(1, int(available)))
    return max(1, rounded)


def _parse_explicit_memory_mb(requested: str | int | float) -> int:
    if isinstance(requested, bool):
        raise ValueError("memory budget must be a positive size, not a boolean")
    if isinstance(requested, (int, float)):
        value_mb = float(requested)
    else:
        matched = _MEMORY_VALUE_PATTERN.fullmatch(requested)
        if matched is None:
            raise ValueError(f"Unsupported explicit memory budget: {requested!r}")
        value = float(matched.group("value"))
        unit = matched.group("unit")
        value_mb = value * _MEMORY_UNIT_TO_MB[unit.upper() if unit else None]
    if not math.isfinite(value_mb) or value_mb <= 0:
        raise ValueError(f"memory budget must be finite and positive, got {requested!r}")
    resolved = int(value_mb)
    if resolved < 1:
        raise ValueError(f"memory budget resolves below 1 MiB: {requested!r}")
    return resolved


def _bytes_to_mb(value: int | None) -> float | None:
    return round(value / 1024 / 1024, 1) if value is not None else None


def _read_meminfo_bytes(
    path: str | Path = "/proc/meminfo",
) -> tuple[int | None, int | None]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None
    total: int | None = None
    available: int | None = None
    for line in lines:
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                total = int(parts[1]) * 1024
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                available = int(parts[1]) * 1024
    return total, available


def _read_mem_available_bytes(path: str | Path = "/proc/meminfo") -> int | None:
    """Compatibility wrapper for callers that only need MemAvailable."""
    return _read_meminfo_bytes(path)[1]


def _read_cgroup_memory_bytes(
    *,
    proc_cgroup_path: str | Path = "/proc/self/cgroup",
    cgroup_v2_root: str | Path = "/sys/fs/cgroup",
    cgroup_v1_memory_root: str | Path = "/sys/fs/cgroup/memory",
) -> tuple[int | None, int | None]:
    """Return the strictest cgroup memory limit and remaining headroom."""
    try:
        lines = Path(proc_cgroup_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None

    v2_result: tuple[int | None, int | None] | None = None
    for line in lines:
        hierarchy, controllers, raw_path = _parse_cgroup_line(line)
        if hierarchy == "0" and not controllers:
            v2_result = _read_cgroup_hierarchy_bytes(
                root=Path(cgroup_v2_root),
                raw_path=raw_path,
                limit_filename="memory.max",
                usage_filename="memory.current",
                unlimited_threshold=None,
            )
            if any(value is not None for value in v2_result):
                return v2_result
            break

    for line in lines:
        _, controllers, raw_path = _parse_cgroup_line(line)
        if "memory" in controllers.split(","):
            return _read_cgroup_hierarchy_bytes(
                root=Path(cgroup_v1_memory_root),
                raw_path=raw_path,
                limit_filename="memory.limit_in_bytes",
                usage_filename="memory.usage_in_bytes",
                unlimited_threshold=1 << 60,
            )
    return v2_result if v2_result is not None else (None, None)


def _parse_cgroup_line(line: str) -> tuple[str, str, str]:
    parts = line.split(":", maxsplit=2)
    if len(parts) != 3:
        return "", "", ""
    return parts[0], parts[1], parts[2]


def _read_cgroup_hierarchy_bytes(
    *,
    root: Path,
    raw_path: str,
    limit_filename: str,
    usage_filename: str,
    unlimited_threshold: int | None,
) -> tuple[int | None, int | None]:
    limits: list[int] = []
    headrooms: list[int] = []
    for directory in _cgroup_directories(root, raw_path):
        limit = _read_cgroup_integer(directory / limit_filename)
        if limit is None or (unlimited_threshold is not None and limit >= unlimited_threshold):
            continue
        limits.append(limit)
        usage = _read_cgroup_integer(directory / usage_filename)
        if usage is not None:
            headrooms.append(max(0, limit - usage))
    return (
        min(limits) if limits else None,
        min(headrooms) if headrooms else None,
    )


def _cgroup_directories(root: Path, raw_path: str) -> tuple[Path, ...]:
    root = root.resolve()
    relative_parts = tuple(
        part for part in Path(raw_path.lstrip("/")).parts if part not in {"", "."}
    )
    if ".." in relative_parts:
        return ()
    current = root.joinpath(*relative_parts)
    if current != root and root not in current.parents:
        return ()

    directories: list[Path] = []
    while True:
        directories.append(current)
        if current == root:
            break
        current = current.parent
    return tuple(directories)


def _read_cgroup_integer(path: Path) -> int | None:
    try:
        raw_value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw_value.isdigit():
        return None
    return int(raw_value)


def _minimum_known_bytes(*values: int | None) -> int | None:
    known = [value for value in values if value is not None]
    return min(known) if known else None


def _read_process_rss_bytes(path: str | Path = "/proc/self/status") -> int | None:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return None
