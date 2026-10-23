"""Physical trade-date partition extent, separate from ingestion request provenance.

The inventory proves stored partition extent, not trading-calendar completeness.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _partition_records(data_dir: Path) -> list[list[Any]]:
    if data_dir.resolve() != data_dir.absolute():
        raise ValueError("symlink in stored partition inventory")
    records: list[list[Any]] = []
    for path in sorted(data_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink in stored partition inventory")
        if path.suffix != ".parquet":
            continue
        relative = path.relative_to(data_dir)
        if len(relative.parts) != 2 or not relative.parts[0].startswith("trade_date="):
            raise ValueError("unsupported trade-date partition layout")
        day = relative.parts[0].removeprefix("trade_date=")
        if len(day) != 8 or datetime.strptime(day, "%Y%m%d").strftime("%Y%m%d") != day:
            raise ValueError("invalid stored partition date")
        stat = path.stat()
        records.append([relative.as_posix(), stat.st_size, stat.st_mtime_ns])
    return records


def trade_date_inventory(data_dir: Path) -> dict[str, Any]:
    try:
        records = _partition_records(data_dir)
        current = _partition_records(data_dir)
    except OSError as exc:
        raise ValueError("stored partition inventory changed or became unreadable") from exc
    if records != current:
        raise ValueError("stored partition inventory changed during scan")
    dates = {record[0].split("/", 1)[0].removeprefix("trade_date=") for record in records}
    if not records:
        raise ValueError("no stored trade-date partitions")
    digest = hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": "stored_trade_date_extent.v1",
        "method": "partition_inventory_size_mtime_sha256",
        "start_date": min(dates),
        "end_date": max(dates),
        "partition_count": len(dates),
        "files": len(records),
        "inventory_sha256": digest,
    }


def verified_stored_coverage(payload: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    evidence = payload.get("stored_coverage")
    if evidence is None:
        return {"status": "unknown"}
    try:
        current = trade_date_inventory(manifest_path.parent / "data")
        if not isinstance(evidence, dict) or evidence != current:
            return {"status": "invalid"}
    except (OSError, ValueError):
        return {"status": "invalid"}
    return {**current, "status": "verified"}
