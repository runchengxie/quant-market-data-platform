"""Stable data-quality receipt envelope used by ingestion and publication gates."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DQ_RECEIPT_SCHEMA = "market_data_platform.dq_receipt.v1"
ELIGIBILITY_ORDER = {"production": 0, "research_only": 1, "quarantine": 2}
VALID_STATUSES = {"passed", "failed"}


def worst_eligibility(*values: str) -> str:
    if not values:
        return "production"
    unknown = [value for value in values if value not in ELIGIBILITY_ORDER]
    if unknown:
        raise ValueError(f"Unsupported eligibility: {unknown[0]}")
    return max(values, key=ELIGIBILITY_ORDER.__getitem__)


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _duration_ms(started_at: str | None, finished_at: str | None) -> int | None:
    start = _parse_time(started_at)
    finish = _parse_time(finished_at)
    if start is None or finish is None:
        return None
    return max(0, round((finish - start).total_seconds() * 1000))


def build_dq_receipt(  # noqa: PLR0913
    *,
    dataset_id: str,
    provider: str,
    data_version: str | None,
    asset_schema_version: str,
    run_id: str,
    input_summary: Mapping[str, Any],
    quality: Mapping[str, Any],
    lineage: Mapping[str, Any],
    status: str,
    eligibility: str,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"Unsupported DQ status: {status}")
    if eligibility not in ELIGIBILITY_ORDER:
        raise ValueError(f"Unsupported eligibility: {eligibility}")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    started = started_at or now
    finished = finished_at or now
    return {
        "schema_version": DQ_RECEIPT_SCHEMA,
        "dataset": {
            "id": str(dataset_id),
            "provider": str(provider),
            "data_version": data_version,
            "schema_version": str(asset_schema_version),
        },
        "run_id": str(run_id),
        "input": dict(input_summary),
        "quality": dict(quality),
        "lineage": dict(lineage),
        "result": {"status": status, "eligibility": eligibility},
        "timing": {
            "started_at": started,
            "finished_at": finished,
            "duration_ms": _duration_ms(started, finished),
        },
    }


def write_dq_receipt(path: str | Path, payload: Mapping[str, Any]) -> None:
    output = Path(path).expanduser().resolve()
    if payload.get("schema_version") != DQ_RECEIPT_SCHEMA:
        raise ValueError(f"schema_version must be {DQ_RECEIPT_SCHEMA}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
