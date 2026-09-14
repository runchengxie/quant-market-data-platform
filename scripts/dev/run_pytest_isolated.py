#!/usr/bin/env python3
"""Run pytest in small file batches so each process releases its memory."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH_SIZE = 8


def discover_test_files(repo_root: Path = REPO_ROOT) -> list[Path]:
    return sorted((repo_root / "tests").glob("test_*.py"))


def batches(items: Sequence[Path], size: int) -> Iterator[list[Path]]:
    if size < 1:
        raise ValueError("batch size must be positive")
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Arguments after -- are passed to pytest.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.batch_size < 1:
        print("ERROR: --batch-size must be positive", file=sys.stderr)
        return 2

    test_files = discover_test_files()
    if not test_files:
        print("ERROR: no tests/test_*.py files found", file=sys.stderr)
        return 2

    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]

    groups = list(batches(test_files, args.batch_size))
    for index, group in enumerate(groups, start=1):
        relative_files = [path.relative_to(REPO_ROOT).as_posix() for path in group]
        print(f"[pytest batch {index}/{len(groups)}] {' '.join(relative_files)}", flush=True)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *relative_files, *pytest_args],
            cwd=REPO_ROOT,
            check=False,
        )
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
