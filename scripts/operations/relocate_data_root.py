#!/usr/bin/env python3
"""Rewrite stale absolute data-root references without following symlinks."""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

TEXT_SUFFIXES = frozenset({".json", ".jsonl", ".md", ".tsv", ".txt", ".yml", ".yaml"})


def find_references(root: Path, old_root: str) -> list[Path]:
    """Return regular text files below root that still mention old_root."""
    matches: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if old_root in text:
            matches.append(path)
    return sorted(matches)


def rewrite_file(path: Path, old_root: str, new_root: str) -> int:
    """Rewrite one file atomically and return the number of replacements."""
    original = path.read_text(encoding="utf-8")
    updated = original.replace(old_root, new_root)
    count = original.count(old_root)
    if updated == original:
        return 0
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(updated)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(mode & 0o7777)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="New data root to scan")
    parser.add_argument("--old-root", required=True)
    parser.add_argument("--new-root", required=True)
    parser.add_argument("--report", type=Path, help="Write a tab-separated migration report")
    parser.add_argument("--apply", action="store_true", help="Write changes")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    old_root = args.old_root.rstrip("/")
    new_root = args.new_root.rstrip("/")
    if old_root == new_root:
        raise SystemExit("old and new roots must differ")
    files = find_references(args.root, old_root)
    total = 0
    report_lines = ["path\treplacements\tapplied"]
    for path in files:
        count = path.read_text(encoding="utf-8").count(old_root)
        print(f"{path}\t{count}")
        if args.apply:
            total += rewrite_file(path, old_root, new_root)
        report_lines.append(f"{path}\t{count}\t{args.apply}")
    print(f"files={len(files)} replacements={total} applied={args.apply}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            "# generated_at="
            + datetime.now(UTC).isoformat()
            + f"\n# old_root={old_root}\n# new_root={new_root}\n"
            + "\n".join(report_lines)
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
