"""Hash-bound, read-only evidence for ambiguous normalized statement events.

This deliberately has no PIT writer, publication adapter, or category selector.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from itertools import groupby
from pathlib import Path
from typing import Any

from quant_market_data_platform.statement_selection import (
    _IDENTITY,
    _date,
    _file_identity,
    _sha,
    _source_matches,
    _stream_digest,
    _timestamp,
    _validate_identity,
    _verify_scan,
)

_KEYS = ["symbol", "report_period", "disclosure_date"]
_METADATA = {
    *_KEYS,
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "report_type",
    "comp_type",
    "end_type",
    "update_flag",
}


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event(dataset: str, key: tuple, rows: list[dict], fields: tuple[str, ...], delay: int) -> dict:
    candidates = sorted(
        [{"comp_type": row["comp_type"], "row_sha256": _digest(row), "row": row} for row in rows],
        key=lambda candidate: (candidate["comp_type"], candidate["row_sha256"]),
    )
    differing = [field for field in fields if len({_digest(row[field]) for row in rows}) != 1]
    available = _date(key[2]) + timedelta(days=delay)
    observed = max(_timestamp(row["_source_retrieved_at"]) for row in rows)
    # Calendar disclosure availability is an assumption, not historical observation evidence.
    usable = max(available.replace(tzinfo=UTC), observed)
    return {
        "dataset": dataset,
        **dict(zip(_KEYS, key, strict=True)),
        "candidate_comp_types": sorted({row["comp_type"] for row in rows}),
        "candidates": candidates,
        "status": "projection_conflict" if differing else "projection_consensus",
        "differing_fields": differing,
        "common_values": None if differing else {field: rows[0][field] for field in fields},
        "blocks_fallback": bool(differing),
        "disclosure_available_date": available.strftime("%Y%m%d"),
        "observed_at": observed.isoformat(),
        "usable_at": usable.isoformat(),
        "time_basis": "reconstructed; usable_at is not historical PIT certification",
    }


def _read_candidates(stream: Any, fields: tuple[str, ...]) -> tuple[int, list[dict]]:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(stream)
    schema = parquet.schema_arrow
    if not set(_IDENTITY).issubset(schema.names):
        raise ValueError("Missing statement identity or observation columns")
    for field in fields:
        if field not in schema.names:
            raise ValueError(f"Missing projected field: {field}")
        kind = schema.field(field).type
        if not (pa.types.is_integer(kind) or pa.types.is_floating(kind) or pa.types.is_null(kind)):
            raise ValueError(f"Projected field must be numeric or null: {field}")
    batches = [
        batch.to_pandas() for batch in parquet.iter_batches(batch_size=16384, columns=_IDENTITY)
    ]
    identities = (
        pd.concat(batches, ignore_index=True)
        if batches
        else pd.DataFrame(columns=pd.Index(_IDENTITY))
    )
    if identities.empty:
        return 0, []
    _validate_identity(identities)
    counts = identities.groupby(_KEYS)["comp_type"].transform("nunique")
    indices = set(identities.index[counts > 1])
    rows, offset = [], 0
    for batch in parquet.iter_batches(batch_size=4096):
        selected = sorted(i - offset for i in indices if offset <= i < offset + batch.num_rows)
        if selected:
            # Arrow validity and Python integer precision survive; no pandas coercion.
            rows.extend(batch.take(selected).to_pylist())
        offset += batch.num_rows
    return len(identities), rows


def _scan(source: Path, expected: str, fields: tuple[str, ...]) -> tuple[int, list[dict]]:
    with source.open("rb") as stream:
        identity = _file_identity(os.fstat(stream.fileno()))
        alias = _file_identity(source.lstat())
        if not _source_matches(source, stream, identity, alias):
            raise ValueError("Normalized source changed while opening")
        if _stream_digest(stream) != expected:
            raise ValueError("Normalized source checksum mismatch")
        stream.seek(0)
        try:
            return _read_candidates(stream, fields)
        finally:
            _verify_scan(source, stream, identity, alias, expected)


def audit_projection_consensus(
    *,
    dataset: str,
    source: Path,
    expected_sha256: str,
    fields: tuple[str, ...],
    available_delay_days: int,
) -> dict[str, Any]:
    """Audit every multi-category event in one immutable normalized Parquet.

    Exact, type-sensitive JSON equality: NULL equals NULL, never zero. An absent
    projected column is an error. Inputs are verified before and after scanning.
    This returns evidence only for ambiguous events, not a complete research asset.
    """
    if dataset not in {"income", "balancesheet", "cashflow"}:
        raise ValueError("Unsupported statement dataset")
    if (
        not isinstance(fields, tuple)
        or not fields
        or any(
            not isinstance(f, str) or not f or f.startswith("_") or f in _METADATA for f in fields
        )
        or len(set(fields)) != len(fields)
    ):
        raise ValueError("Projection requires explicit unique financial fields")
    if type(available_delay_days) is not int or not 1 <= available_delay_days <= 365:
        raise ValueError("available_delay_days must be an integer in [1, 365]")
    expected = _sha(expected_sha256)
    row_count, candidates = _scan(source, expected, fields)

    def event_key(row: dict) -> tuple:
        return tuple(row[key] for key in _KEYS)

    events = [
        _event(dataset, key, list(group), fields, available_delay_days)
        for key, group in groupby(sorted(candidates, key=event_key), key=event_key)
    ]
    report = {
        "schema_version": "statement-projection-consensus.v1",
        "source": {"dataset": dataset, "path": str(source.resolve()), "sha256": expected},
        "projection_fields": list(fields),
        "null_semantics": "exact-json-types; null equals null; absent column rejected",
        "available_delay_days": available_delay_days,
        "availability_timezone": "UTC",
        "scope": "multi-category events only; not a complete asset or selector certificate",
        "historical_pit_certified": False,
        "published_asset": False,
        "complete_current_eligible": False,
        "normalized_row_count": row_count,
        "candidate_row_count": len(candidates),
        "events": events,
        "consensus_event_count": sum(e["status"] == "projection_consensus" for e in events),
        "conflict_event_count": sum(e["status"] == "projection_conflict" for e in events),
    }
    report["proof_sha256"] = _digest(report)
    report["audit_created_at"] = datetime.now(UTC).isoformat()
    return report
