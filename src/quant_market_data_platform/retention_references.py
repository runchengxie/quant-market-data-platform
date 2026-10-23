"""Conservatively protect versions cited by retained evidence or symlinks."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .data_governance_part01 import PlanItem

_TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
    ".xml",
    ".log",
}
_EXCLUDED = {Path("metadata/retention"), Path("metadata/lifecycle")}
_MAX_TEXT_BYTES = 16 * 1024 * 1024


def _overlaps(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _scan_references(
    root: Path, candidates: Sequence[Path], *, validate: bool = False
) -> tuple[dict[Path, str], list[str]]:
    references: dict[Path, str] = {}
    errors: list[str] = []
    if not root.is_dir():
        return references, [f"reference root is not a readable directory: {root}"]
    for directory, dirs, files in os.walk(
        root, followlinks=False, onerror=lambda e: errors.append(str(e))
    ):
        parent = Path(directory)
        dirs[:] = [name for name in dirs if (parent / name).relative_to(root) not in _EXCLUDED]
        for name in [*dirs, *files]:
            path = parent / name
            try:
                if path.is_symlink():
                    target = path.resolve(strict=False)
                    for candidate in candidates:
                        if not path.is_relative_to(candidate) and _overlaps(target, candidate):
                            references.setdefault(candidate, f"retained symlink: {path}")
                elif path.is_file() and path.suffix in _TEXT_SUFFIXES:
                    _scan_text(root, path, candidates, references, validate=validate)
            except (OSError, RuntimeError, UnicodeError, ValueError, yaml.YAMLError) as exc:
                errors.append(f"{path}: {exc}")
    return references, errors


def _scan_text(
    root: Path,
    path: Path,
    candidates: Sequence[Path],
    references: dict[Path, str],
    *,
    validate: bool = False,
) -> None:
    # Basename matching also protects historical references to a renamed root.
    # False positives retain data; missing references could destroy provenance.
    if path.stat().st_size > _MAX_TEXT_BYTES:
        raise OSError(f"retained text exceeds reference scan limit: {path}")
    text = path.read_text(encoding="utf-8")
    if validate and path.suffix == ".json":
        json.loads(text)
    elif validate and path.suffix in {".yaml", ".yml"}:
        yaml.safe_load(text)
    elif validate and path.suffix == ".jsonl":
        for line in text.splitlines():
            if line.strip():
                json.loads(line)
    for candidate in candidates:
        if not path.is_relative_to(candidate) and candidate.name in text:
            references.setdefault(candidate, f"retained evidence: {path}")


def protect_retained_references(
    root: Path, items: Sequence[PlanItem], *, reference_roots: Sequence[str | Path] = ()
) -> list[PlanItem]:
    """Keep referenced candidates and fail closed when the scan is incomplete."""
    candidates = [item.path for item in items if item.action == "retire_candidate"]
    if not candidates:
        return list(items)
    references: dict[Path, str] = {}
    errors: list[str] = []
    for source in dict.fromkeys([root, *(Path(p).expanduser().resolve() for p in reference_roots)]):
        found, failures = _scan_references(source, candidates, validate=source != root)
        references.update(found)
        errors.extend(failures)
    result: list[PlanItem] = []
    for item in items:
        if item.action != "retire_candidate":
            result.append(item)
        elif item.path in references:
            result.append(replace(item, action="keep", reason=references[item.path]))
        elif errors:
            result.append(
                replace(item, action="review", reason=f"reference scan incomplete: {errors[0]}")
            )
        else:
            result.append(item)
    return result
