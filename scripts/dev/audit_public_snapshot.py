"""Audit an exported repository tree before creating a public Git repository."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

PERSONAL_PATH = re.compile("/" + r"home/[A-Za-z0-9._-]+/")
FORBIDDEN_MARKERS = ("xiaodefa" + ".cn", "deep-learning-" + "tick-data-prediction")
EXCLUDED_DIRECTORIES = ("docs/archive", "docs/superpowers")
CREDENTIAL_NAMES = {".env", ".env.local", ".envrc", "secrets.env"}
DATA_DIRECTORIES = {"data", "artifacts", "reports"}
DATA_SUFFIXES = {".parquet", ".feather", ".pickle", ".pkl"}


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def audit_snapshot(root: Path, *, excluded_paths: tuple[str, ...] = ()) -> list[str]:
    """Return human-readable findings that make an export unsafe to publish."""

    root = root.expanduser().resolve()
    excluded = tuple(path.rstrip("/") for path in excluded_paths)
    findings: list[str] = []
    for directory in EXCLUDED_DIRECTORIES:
        path = root / directory
        if path.is_dir() and directory not in excluded:
            findings.append(f"excluded internal directory: {directory}")
    for path in sorted(root.rglob("*")):
        if ".git" in path.parts or ".venv" in path.parts:
            continue
        relative = _relative(path, root)
        if any(relative == entry or relative.startswith(f"{entry}/") for entry in excluded):
            continue
        if path.is_file() and path.name in CREDENTIAL_NAMES:
            findings.append(f"credential file: {relative}")
        if path.is_file() and path.suffix.lower() in DATA_SUFFIXES:
            findings.append(f"data artifact: {relative}")
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if PERSONAL_PATH.search(text) or any(marker in text for marker in FORBIDDEN_MARKERS):
                findings.append(f"personal path marker: {relative}")
        if path.is_dir() and path.name in DATA_DIRECTORIES:
            findings.append(f"data directory: {relative}")
    return sorted(dict.fromkeys(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--exclude-file", type=Path)
    args = parser.parse_args()
    excluded_paths = ()
    if args.exclude_file:
        excluded_paths = tuple(
            line.strip()
            for line in args.exclude_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    findings = audit_snapshot(args.root, excluded_paths=excluded_paths)
    if findings:
        print("public snapshot audit failed:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    print("public snapshot audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
