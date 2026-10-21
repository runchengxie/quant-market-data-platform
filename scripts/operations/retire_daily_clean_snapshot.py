#!/usr/bin/env python3
"""Retire one explicitly reviewed duplicate input snapshot; default is dry-run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quant_market_data_platform.snapshot_retirement import retire_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--peer-map", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = retire_snapshot(
            args.artifacts_root,
            args.snapshot,
            args.peer_map,
            args.archive_dir,
            apply=args.apply,
            writers_stopped=args.writers_stopped,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"retirement refused: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
