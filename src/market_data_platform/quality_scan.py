"""Resumable CPU scans for raw Parquet quality reports."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from market_data_platform.quality import profile_parquet

_CHECKPOINT_VERSION = 3
_FINDING_FIELDS = {
    "date_mismatch": "trading_day_mismatch_rows",
    "duplicate_id": "duplicate_id_rows",
    "nonpositive": "nonpositive",
    "timestamp_order": "timestamp_backwards",
}


@dataclass(frozen=True)
class QualityScanOptions:
    root: str | Path
    checkpoint: str | Path
    output: str | Path | None = None
    pattern: str = "*.parquet"
    batch_size: int = 262_144
    max_tracked_ids: int = 1_000_000


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sequence_quality(report: dict[str, Any]) -> dict[str, Any]:
    ordering = report.get("ordering", {})
    if not isinstance(ordering, dict):
        return {}
    quality = ordering.get("sequence_quality", {})
    return quality if isinstance(quality, dict) else {}


def _finding_categories(report: dict[str, Any]) -> list[str]:
    categories = []
    for category, field in _FINDING_FIELDS.items():
        value = report.get(field, 0)
        if isinstance(value, dict):
            has_finding = any(int(count) > 0 for count in value.values())
        else:
            has_finding = int(value) > 0
        if has_finding:
            categories.append(category)
    if report.get("missing_columns"):
        categories.append("missing_columns")
    if any(int(count) > 0 for count in report.get("nulls", {}).values()):
        categories.append("nulls")
    sequence = _sequence_quality(report)
    if any(
        int(sequence.get(field, 0) or 0) > 0
        for field in ("non_numeric_rows", "duplicate_rows", "backwards_rows", "gap_events")
    ) or bool(sequence.get("tracking_truncated")):
        categories.append("sequence_order")
    return categories


def _summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    category_files = Counter(
        category for report in reports for category in report["finding_categories"]
    )
    return {
        "files_with_findings": sum(bool(report["finding_categories"]) for report in reports),
        "finding_files_by_category": dict(sorted(category_files.items())),
        "rows": sum(int(report["rows"]) for report in reports),
        "duplicate_id_rows": sum(int(report["duplicate_id_rows"]) for report in reports),
        "timestamp_backwards": sum(int(report["timestamp_backwards"]) for report in reports),
        "trading_day_mismatch_rows": sum(
            int(report["trading_day_mismatch_rows"]) for report in reports
        ),
        "sequence_duplicate_rows": sum(
            int(_sequence_quality(report).get("duplicate_rows", 0) or 0) for report in reports
        ),
        "sequence_backwards_rows": sum(
            int(_sequence_quality(report).get("backwards_rows", 0) or 0) for report in reports
        ),
        "sequence_gap_events": sum(
            int(_sequence_quality(report).get("gap_events", 0) or 0) for report in reports
        ),
        "timestamp_fallback_files": sum(
            report.get("ordering", {}).get("ordering_mode") == "timestamp_fallback"
            for report in reports
            if isinstance(report.get("ordering", {}), dict)
        ),
    }


def scan_parquet_tree(options: QualityScanOptions) -> dict[str, Any]:
    """Profile every matching Parquet file, persisting progress after each file."""
    root_path = Path(options.root).expanduser().resolve()
    checkpoint_path = Path(options.checkpoint).expanduser().resolve()
    output_path = (
        Path(options.output).expanduser().resolve() if options.output is not None else None
    )
    files = sorted(path for path in root_path.rglob(options.pattern) if path.is_file())
    checkpoint_payload: dict[str, Any] = {"version": _CHECKPOINT_VERSION, "completed": {}}
    if checkpoint_path.exists():
        saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if saved.get("version") == _CHECKPOINT_VERSION:
            checkpoint_payload = saved
    completed = checkpoint_payload.setdefault("completed", {})
    reports: list[dict[str, Any]] = []
    reused = 0
    for path in files:
        key = str(path)
        saved = completed.get(key)
        if (
            saved is not None
            and saved.get("size") == path.stat().st_size
            and saved.get("mtime_ns") == path.stat().st_mtime_ns
        ):
            report = saved["report"]
            reused += 1
        else:
            report = profile_parquet(
                path,
                batch_size=options.batch_size,
                max_tracked_ids=options.max_tracked_ids,
            )
            report["relative_path"] = str(path.relative_to(root_path))
            report["finding_categories"] = _finding_categories(report)
            completed[key] = {
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
                "report": report,
            }
            _write_json(checkpoint_path, checkpoint_payload)
        reports.append(report)
    payload = {
        "status": "complete",
        "root": str(root_path),
        "pattern": options.pattern,
        "files_discovered": len(files),
        "files_scanned": len(reports),
        "files_reused_from_checkpoint": reused,
        "summary": _summary(reports),
        "reports": reports,
    }
    if output_path is not None:
        _write_json(output_path, payload)
    return payload
