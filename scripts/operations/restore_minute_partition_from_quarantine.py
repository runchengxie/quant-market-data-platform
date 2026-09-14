#!/usr/bin/env python3
"""Safely restore one validated minute partition from quarantine.

The command is dry-run by default.  It only changes files with ``--apply``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def inspect_partition(
    *, partition_dir: Path, quarantine_file: Path, trade_date: str
) -> dict[str, Any]:
    sidecar_path = partition_dir / "_complete.json"
    if not sidecar_path.exists():
        raise FileNotFoundError(f"missing sidecar: {sidecar_path}")
    sidecar = _json(sidecar_path)
    expected = set(map(str, sidecar.get("expected_symbols") or []))
    if not expected:
        raise ValueError("sidecar has no expected_symbols")
    table = pq.read_table(quarantine_file, columns=["ts_code"])
    symbols = set(map(str, table.column("ts_code").to_pylist()))
    rows = table.num_rows
    if symbols != expected:
        raise ValueError(
            f"symbol set mismatch for {trade_date}: expected {len(expected)}, got {len(symbols)}"
        )
    if rows != len(expected) * 241:
        raise ValueError(f"row count {rows} is not {len(expected)}*241")
    return {
        "trade_date": trade_date,
        "rows": rows,
        "symbols": len(symbols),
        "partition_sha256": _sha256(quarantine_file),
        "sidecar_path": str(sidecar_path),
        "active_path": str(partition_dir / "part-00000.parquet"),
        "status": "validated",
    }


def restore(args: argparse.Namespace) -> int:
    partition_dir = args.partition_dir.expanduser().resolve()
    quarantine_file = args.quarantine_file.expanduser().resolve()
    active_path = partition_dir / "part-00000.parquet"
    result = inspect_partition(
        partition_dir=partition_dir,
        quarantine_file=quarantine_file,
        trade_date=args.trade_date,
    )
    if active_path.exists() and not args.allow_regression:
        active_rows = pq.read_metadata(active_path).num_rows
        if active_rows >= result["rows"]:
            raise ValueError(
                f"refusing regression: active has {active_rows} rows, restore has {result['rows']}"
            )
    if not args.apply:
        print(json.dumps({**result, "dry_run": True}, ensure_ascii=False, indent=2))
        return 0
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup = partition_dir / f"_backups/restore-{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    if active_path.exists():
        shutil.copy2(active_path, backup / active_path.name)
    shutil.copy2(quarantine_file, active_path)
    sidecar = _json(partition_dir / "_complete.json")
    sidecar.update({"status": "complete", "completed_symbols": sorted(sidecar["expected_symbols"])})
    sidecar.setdefault("partition", {}).update(
        {"rows": result["rows"], "sha256": result["partition_sha256"]}
    )
    _atomic_json(partition_dir / "_complete.json", sidecar)
    if args.ledger:
        ledger_path = args.ledger.expanduser().resolve()
        ledger = _json(ledger_path)
        ledger.setdefault("events", []).append(
            {"type": "manual_partition_restore", "at": datetime.now(UTC).isoformat(), **result}
        )
        _atomic_json(ledger_path, ledger)
    print(
        json.dumps(
            {**result, "backup": str(backup), "applied": True},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partition-dir", type=Path, required=True)
    parser.add_argument("--quarantine-file", type=Path, required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-regression", action="store_true")
    return restore(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
