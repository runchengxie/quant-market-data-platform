"""Conservative, inode-aware retention planning for local data artifacts.

This module only inventories paths and classifies retention candidates.  It
does not unlink, rename, or otherwise mutate any artifact.
"""

from __future__ import annotations

import errno
import fnmatch
import json
import os
import re
import stat
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

PlanAction: TypeAlias = Literal["keep", "retire_candidate", "review", "missing"]

ExplicitDisposition: TypeAlias = Literal["keep", "retire_candidate", "review"]

INVENTORY_SCHEMA_VERSION = "market_data_platform.lifecycle_inventory.v1"


@dataclass(frozen=True, slots=True)
class ExplicitPathRule:
    """Plan one exact path, normally as a review or retirement candidate."""

    name: str
    path: str | Path
    disposition: ExplicitDisposition = "review"

    def __post_init__(self) -> None:
        _validate_rule_name(self.name)
        if isinstance(self.path, str) and not self.path.strip():
            raise ValueError("explicit path must not be empty")
        if self.disposition not in {"keep", "retire_candidate", "review"}:
            raise ValueError(f"Unsupported explicit disposition: {self.disposition}")


@dataclass(frozen=True, slots=True)
class RetainNewestRule:
    """Keep the newest direct child directories and flag older versions."""

    name: str
    parent: str | Path
    pattern: str
    retain_newest: int

    def __post_init__(self) -> None:
        _validate_rule_name(self.name)
        if self.retain_newest < 0:
            raise ValueError("retain_newest must be non-negative")
        if not self.pattern or Path(self.pattern).name != self.pattern:
            raise ValueError("retain-newest pattern must match direct child names only")


@dataclass(frozen=True, slots=True)
class JsonStatusRule:
    """Flag JSON files whose top-level ``status`` is explicitly eligible."""

    name: str
    parent: str | Path
    pattern: str
    candidate_statuses: frozenset[str]

    def __post_init__(self) -> None:
        _validate_rule_name(self.name)
        _validate_relative_glob(self.pattern)
        if any(not status_value for status_value in self.candidate_statuses):
            raise ValueError("candidate_statuses must contain non-empty strings")


GovernanceRule: TypeAlias = ExplicitPathRule | RetainNewestRule | JsonStatusRule


@dataclass(frozen=True, slots=True)
class PathUsage:
    """Regular-file storage observed below one path.

    ``logical_bytes`` counts every regular-file path. ``allocated_bytes``
    counts each ``(device, inode)`` once. ``reclaimable_bytes`` counts only
    inodes for which every hard link is inside the measured path.
    """

    logical_bytes: int
    allocated_bytes: int
    reclaimable_bytes: int
    files: int
    unique_inodes: int
    external_hardlink_inodes: int


@dataclass(frozen=True, slots=True)
class PlanItem:
    """One deterministic, non-mutating retention decision."""

    rule: str
    action: PlanAction
    path: Path
    usage: PathUsage
    reason: str
    status: str | None = None


@dataclass(slots=True)
class _InodeUsage:
    allocated_bytes: int
    observed_nlink: int
    links_inside: int = 0


_EMPTY_USAGE = PathUsage(
    logical_bytes=0,
    allocated_bytes=0,
    reclaimable_bytes=0,
    files=0,
    unique_inodes=0,
    external_hardlink_inodes=0,
)


def _validate_rule_name(name: str) -> None:
    if not name.strip():
        raise ValueError("rule name must not be empty")


def _validate_relative_glob(pattern: str) -> None:
    pattern_path = Path(pattern)
    if not pattern or pattern_path.is_absolute() or ".." in pattern_path.parts:
        raise ValueError("JSON status pattern must be a relative glob below its parent")


def _rooted_path(root: Path, value: str | Path) -> Path:
    requested = Path(value).expanduser()
    joined = requested if requested.is_absolute() else root / requested
    path = Path(os.path.abspath(joined))
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Governance path escapes the configured root: {path}") from exc
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Governance path resolves outside the configured root: {path} -> {resolved}"
        ) from exc
    return path


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _regular_file_stats(path: Path) -> Iterator[os.stat_result]:
    root_stat = path.lstat()
    if stat.S_ISREG(root_stat.st_mode):
        yield root_stat
        return
    if not stat.S_ISDIR(root_stat.st_mode):
        return

    root_device = root_stat.st_dev
    pending = [path]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                entry_stat = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(entry_stat.st_mode):
                    if entry_stat.st_dev != root_device:
                        raise OSError(
                            errno.EXDEV,
                            "inventory stopped at a filesystem boundary",
                            entry.path,
                        )
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(entry_stat.st_mode):
                    yield entry_stat


def _measure_path(path: Path) -> PathUsage:
    logical_bytes = 0
    files = 0
    inodes: dict[tuple[int, int], _InodeUsage] = {}
    for file_stat in _regular_file_stats(path):
        files += 1
        logical_bytes += file_stat.st_size
        key = (file_stat.st_dev, file_stat.st_ino)
        inode = inodes.get(key)
        if inode is None:
            inode = _InodeUsage(
                allocated_bytes=file_stat.st_blocks * 512,
                observed_nlink=file_stat.st_nlink,
            )
            inodes[key] = inode
        else:
            # A concurrent link removal must never make the plan less
            # conservative than an earlier observation from the same scan.
            inode.observed_nlink = max(inode.observed_nlink, file_stat.st_nlink)
        inode.links_inside += 1

    allocated_bytes = sum(inode.allocated_bytes for inode in inodes.values())
    reclaimable_bytes = sum(
        inode.allocated_bytes
        for inode in inodes.values()
        if inode.links_inside == inode.observed_nlink
    )
    external_hardlink_inodes = sum(
        inode.links_inside != inode.observed_nlink for inode in inodes.values()
    )
    return PathUsage(
        logical_bytes=logical_bytes,
        allocated_bytes=allocated_bytes,
        reclaimable_bytes=reclaimable_bytes,
        files=files,
        unique_inodes=len(inodes),
        external_hardlink_inodes=external_hardlink_inodes,
    )


def _safe_measure_path(path: Path) -> tuple[PathUsage, str | None]:
    try:
        return _measure_path(path), None
    except OSError as exc:
        return _EMPTY_USAGE, f"inventory failed: {type(exc).__name__}: {exc}"


def _sibling_symlink_targets(parent: Path) -> set[Path]:
    targets: set[Path] = set()
    try:
        with os.scandir(parent) as entries:
            for entry in entries:
                if not entry.is_symlink():
                    continue
                try:
                    targets.add(Path(entry.path).resolve(strict=False))
                except (OSError, RuntimeError):
                    continue
    except (FileNotFoundError, NotADirectoryError):
        pass
    return targets


def _is_sibling_symlink_target(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    return resolved in _sibling_symlink_targets(path.parent)


def _paths_overlap(first: Path, second: Path) -> bool:
    if first == second or first.is_relative_to(second) or second.is_relative_to(first):
        return True
    first_resolved = first.resolve(strict=False)
    second_resolved = second.resolve(strict=False)
    return (
        first_resolved == second_resolved
        or first_resolved.is_relative_to(second_resolved)
        or second_resolved.is_relative_to(first_resolved)
    )


def _is_protected_path(path: Path, protected_paths: Sequence[Path]) -> bool:
    return any(_paths_overlap(path, protected_path) for protected_path in protected_paths)


def _with_inventory_result(
    *,
    rule: str,
    action: PlanAction,
    path: Path,
    reason: str,
    status_value: str | None = None,
) -> PlanItem:
    usage, inventory_error = _safe_measure_path(path)
    if inventory_error is not None:
        reason = f"{reason}; {inventory_error}"
        if action == "retire_candidate":
            action = "review"
    return PlanItem(
        rule=rule,
        action=action,
        path=path,
        usage=usage,
        reason=reason,
        status=status_value,
    )


def _plan_explicit_path(
    root: Path,
    rule: ExplicitPathRule,
    protected_paths: Sequence[Path],
) -> list[PlanItem]:
    path = _rooted_path(root, rule.path)
    if path == root:
        raise ValueError(f"Explicit governance rule cannot target the configured root: {rule.name}")
    if not _lexists(path):
        return [
            PlanItem(
                rule=rule.name,
                action="missing",
                path=path,
                usage=_EMPTY_USAGE,
                reason="explicit path does not exist",
            )
        ]

    action: PlanAction = rule.disposition
    reason = f"explicit path classified as {rule.disposition}"
    if action != "keep" and _is_protected_path(path, protected_paths):
        action = "keep"
        reason = "protected because it overlaps a declared current or kept path"
    elif action == "retire_candidate" and _is_sibling_symlink_target(path):
        action = "keep"
        reason = "protected because a sibling symlink targets this path"
    return [
        _with_inventory_result(
            rule=rule.name,
            action=action,
            path=path,
            reason=reason,
        )
    ]


def _direct_child_directories(root: Path, parent: Path, pattern: str) -> list[Path]:
    try:
        with os.scandir(parent) as entries:
            paths = [
                Path(entry.path)
                for entry in entries
                if fnmatch.fnmatchcase(entry.name, pattern) and entry.is_dir(follow_symlinks=False)
            ]
    except (FileNotFoundError, NotADirectoryError):
        return []
    for path in paths:
        _rooted_path(root, path)
    return sorted(paths, key=_version_sort_key, reverse=True)


def _version_sort_key(path: Path) -> tuple[str, str]:
    dates = re.findall(r"(?<!\d)(\d{8})(?!\d)", path.name)
    return (dates[-1] if dates else "", path.name)


def _plan_retain_newest(
    root: Path,
    rule: RetainNewestRule,
    protected_paths: Sequence[Path],
) -> list[PlanItem]:
    parent = _rooted_path(root, rule.parent)
    candidates = _direct_child_directories(root, parent, rule.pattern)
    protected_targets = _sibling_symlink_targets(parent)
    items: list[PlanItem] = []
    for index, path in enumerate(candidates):
        if _is_protected_path(path, protected_paths):
            action: PlanAction = "keep"
            reason = "protected because it overlaps a declared current or kept path"
        elif index < rule.retain_newest:
            action = "keep"
            reason = f"within newest {rule.retain_newest} matching versions"
        elif path.resolve(strict=False) in protected_targets:
            action = "keep"
            reason = "protected because a sibling symlink targets this path"
        else:
            action = "retire_candidate"
            reason = f"older than newest {rule.retain_newest} matching versions"
        items.append(
            _with_inventory_result(
                rule=rule.name,
                action=action,
                path=path,
                reason=reason,
            )
        )
    return items


def _load_json_status(path: Path) -> tuple[str | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid JSON status file: {type(exc).__name__}: {exc}"
    if not isinstance(payload, Mapping):
        return None, "JSON status file must contain a top-level object"
    status_value = payload.get("status")
    if not isinstance(status_value, str) or not status_value:
        return None, "JSON status file has no non-empty string status"
    return status_value, None


def _json_status_paths(root: Path, parent: Path, pattern: str) -> list[Path]:
    paths: list[Path] = []
    try:
        candidates = parent.glob(pattern)
        for path in candidates:
            try:
                path_stat = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(path_stat.st_mode):
                _rooted_path(root, path)
                paths.append(path)
    except (FileNotFoundError, NotADirectoryError):
        return []
    return sorted(paths, key=lambda path: str(path))


def _plan_json_status(
    root: Path,
    rule: JsonStatusRule,
    protected_paths: Sequence[Path],
) -> list[PlanItem]:
    parent = _rooted_path(root, rule.parent)
    items: list[PlanItem] = []
    for path in _json_status_paths(root, parent, rule.pattern):
        status_value, status_error = _load_json_status(path)
        if _is_protected_path(path, protected_paths):
            action: PlanAction = "keep"
            reason = "protected because it overlaps a declared current or kept path"
        elif status_error is not None:
            action = "review"
            reason = status_error
        elif status_value in rule.candidate_statuses:
            action = "retire_candidate"
            reason = f"JSON status {status_value!r} is eligible"
        else:
            action = "keep"
            reason = f"JSON status {status_value!r} is not eligible"
        items.append(
            _with_inventory_result(
                rule=rule.name,
                action=action,
                path=path,
                reason=reason,
                status_value=status_value,
            )
        )
    return items


def plan_data_governance(
    root: str | Path,
    rules: Sequence[GovernanceRule],
    *,
    protected_paths: Sequence[str | Path] = (),
) -> list[PlanItem]:
    """Build a deterministic retention plan without changing the filesystem."""

    normalized_root = Path(root).expanduser().resolve()
    declared_keep_paths = [
        rule.path
        for rule in rules
        if isinstance(rule, ExplicitPathRule) and rule.disposition == "keep"
    ]
    normalized_protected_paths = tuple(
        _rooted_path(normalized_root, path) for path in (*declared_keep_paths, *protected_paths)
    )
    items: list[PlanItem] = []
    for rule in rules:
        if isinstance(rule, ExplicitPathRule):
            items.extend(_plan_explicit_path(normalized_root, rule, normalized_protected_paths))
        elif isinstance(rule, RetainNewestRule):
            items.extend(_plan_retain_newest(normalized_root, rule, normalized_protected_paths))
        elif isinstance(rule, JsonStatusRule):
            items.extend(_plan_json_status(normalized_root, rule, normalized_protected_paths))
        else:
            raise TypeError(f"Unsupported governance rule: {type(rule).__name__}")
    return items


def _validate_inventory_schema(payload: Mapping[str, object]) -> None:
    schema_version = payload.get("schema_version")
    if schema_version != INVENTORY_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported lifecycle inventory schema: {schema_version!r}; "
            f"expected {INVENTORY_SCHEMA_VERSION!r}"
        )


def protected_paths_from_inventory(payload: Mapping[str, object]) -> list[str]:
    """Return paths whose lifecycle entries explicitly require retention."""

    _validate_inventory_schema(payload)
    raw_entries = payload.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ValueError("lifecycle inventory entries must be a list")

    protected: list[str] = []
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, Mapping):
            raise ValueError(f"entries[{index}] must be an object")
        disposition = raw_entry.get("disposition")
        if disposition != "keep":
            continue
        path = raw_entry.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"entries[{index}].path must be a non-empty string")
        protected.append(path)
    return protected
