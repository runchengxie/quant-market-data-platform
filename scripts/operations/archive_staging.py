#!/usr/bin/env python3
"""Move explicitly approved staging directories into the archive as entities."""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

LOCK_NAMES = frozenset({".lock", ".lockfile"})
TEXT_SUFFIXES = frozenset({".json", ".jsonl", ".md", ".tsv", ".txt", ".yml", ".yaml"})


class StagingArchiveError(RuntimeError):
    """Raised when a staging directory fails a safety gate."""


def _files(source: Path) -> list[Path]:
    result: list[Path] = []
    for path in source.rglob("*"):
        if path.is_symlink():
            raise StagingArchiveError(f"staging directory contains a symlink: {path}")
        if path.is_file():
            result.append(path)
    return sorted(result)


def _rewrite_text(path: Path, old: str, new: str) -> None:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    if old not in original:
        return
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(original.replace(old, new))
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(mode & 0o7777)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def archive_one(source: Path, destination: Path, apply: bool) -> dict[str, object]:
    if not source.is_dir() or source.is_symlink():
        raise StagingArchiveError(f"source is not a real directory: {source}")
    if destination.exists():
        raise StagingArchiveError(f"archive destination already exists: {destination}")
    files = _files(source)
    locks = [
        path
        for path in files
        if path.name in LOCK_NAMES or path.name.endswith((".lock", ".lockfile"))
    ]
    if locks:
        raise StagingArchiveError(f"staging directory has lock files: {locks[0]}")
    size = sum(path.stat().st_size for path in files)
    item = {
        "source": str(source),
        "destination": str(destination),
        "file_count": len(files),
        "bytes": size,
        "status": "planned",
    }
    if not apply:
        return item
    destination.parent.mkdir(parents=True, exist_ok=True)
    old_text = str(source)
    new_text = str(destination)
    for path in files:
        _rewrite_text(path, old_text, new_text)
    os.replace(source, destination)
    item["status"] = "archived"
    return item


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--archive-date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("names", nargs="+")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.artifacts_root.resolve()
    staging = root / "staging"
    archive = root / "archive" / "staging" / args.archive_date
    items = []
    for name in args.names:
        if Path(name).name != name or name in {"", ".", ".."}:
            raise SystemExit(f"invalid staging directory name: {name}")
        items.append(archive_one(staging / name, archive / name, args.apply))
    for item in items:
        print("{source}\t{destination}\t{file_count}\t{bytes}\t{status}".format(**item))
    print(f"items={len(items)} applied={args.apply}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
