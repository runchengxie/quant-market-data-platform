"""Read-only mechanical audit of explicit company categories; never builds PIT."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

_DATASETS = {"income", "balancesheet", "cashflow"}
_KEYS = ["symbol", "report_period", "disclosure_date"]
_IDENTITY = [*_KEYS, "comp_type", "report_type", "_source_retrieved_at"]


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Required nonempty string")
    return value


def _date(value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("Dates must be YYYYMMDD strings")
    return datetime.strptime(value, "%Y%m%d")


def _timestamp(value: Any) -> datetime:
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid observation timestamp") from error
    if stamp.utcoffset() is None or stamp > datetime.now(UTC):
        raise ValueError("Observation timestamp must include timezone and cannot be future")
    return stamp


def _fields(payload: dict, allowed: set[str]) -> None:
    if set(payload) != allowed:
        raise ValueError(f"Unknown or missing contract fields: {sorted(set(payload) ^ allowed)}")


def _decision_key(decision: dict[str, Any], delay: int) -> tuple[str, ...]:
    _fields(
        decision,
        {
            "dataset",
            "symbol",
            "report_period",
            "disclosure_date",
            "available_date",
            "allowed_comp_types",
            "observed_at",
            "evidence",
            "reason",
        },
    )
    if decision["dataset"] not in _DATASETS:
        raise ValueError("Unsupported statement dataset")
    _text(decision["symbol"])
    period = _date(decision["report_period"])
    disclosure = _date(decision["disclosure_date"])
    if period > disclosure or _date(decision["available_date"]) != disclosure + timedelta(
        days=delay
    ):
        raise ValueError("available_date must equal valid disclosure_date plus calendar delay")
    observed = _timestamp(decision["observed_at"])
    evidence = decision["evidence"]
    _fields(evidence, {"uri", "sha256", "observed_at"})
    if not urlparse(_text(evidence["uri"])).scheme:
        raise ValueError("Evidence URI must have a scheme")
    _sha(evidence["sha256"])
    if _timestamp(evidence["observed_at"]) > observed:
        raise ValueError("Evidence timestamp cannot be after decision timestamp")
    _text(decision["reason"])
    allowed = decision["allowed_comp_types"]
    if not isinstance(allowed, list):
        raise ValueError("allowed_comp_types must be a list")
    for value in allowed:
        _text(value)
    return (
        decision["dataset"],
        *(decision[column] for column in _KEYS),
        decision["available_date"],
    )


def _contract(payload: dict[str, Any]) -> tuple[int, dict[tuple[str, ...], list[dict[str, Any]]]]:
    _fields(
        payload,
        {
            "schema_version",
            "policy_id",
            "policy_version",
            "available_delay_days",
            "normalized_inputs",
            "decisions",
        },
    )
    if payload["schema_version"] != "statement-selector.v1":
        raise ValueError("Unsupported selector schema_version")
    _text(payload["policy_id"])
    _text(payload["policy_version"])
    delay = payload["available_delay_days"]
    if type(delay) is not int or delay < 1:
        raise ValueError("available_delay_days must be a positive integer")
    if not isinstance(payload["decisions"], list):
        raise ValueError("decisions must be a list")
    decisions: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for decision in payload["decisions"]:
        decisions.setdefault(_decision_key(decision, delay), []).append(decision)
    return delay, decisions


def _validate_identity(frame: Any) -> None:
    if frame[_IDENTITY].isna().any().any():
        raise ValueError("Null statement identity or observation")
    if not frame["report_type"].eq("1").all():
        raise ValueError("Selector v1 accepts only normalized report_type=1")
    for column in _IDENTITY:
        for value in frame[column].unique():
            _text(value)
    for value in frame["_source_retrieved_at"].unique():
        _timestamp(value)
    for column in ["report_period", "disclosure_date"]:
        for value in frame[column].unique():
            _date(value)
    if (frame["report_period"] > frame["disclosure_date"]).any():
        raise ValueError("Report period cannot follow disclosure")


def _conflicts(source: Path) -> tuple[Any, Any]:
    """Scan identity batches, then materialize only conflicting financial rows."""
    import pandas as pd
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(source)
    if not set(_IDENTITY).issubset(parquet.schema_arrow.names):
        raise ValueError("Normalized source is missing statement identity or observation columns")
    batches = [
        batch.to_pandas() for batch in parquet.iter_batches(batch_size=16384, columns=_IDENTITY)
    ]
    frame = (
        pd.concat(batches, ignore_index=True)
        if batches
        else pd.DataFrame(columns=pd.Index(_IDENTITY))
    )
    if frame.empty:
        return frame, frame
    _validate_identity(frame)
    counts = frame.groupby(_KEYS)["comp_type"].transform("nunique")
    indices = set(frame.index[counts > 1])
    if not indices:
        return frame, frame.iloc[:0]
    groups, offset = [], 0
    for batch in parquet.iter_batches(batch_size=4096):
        rows = batch.num_rows
        selected = sorted(i - offset for i in indices if offset <= i < offset + rows)
        if selected:
            groups.append(batch.take(selected).to_pandas())
        offset += rows
    return frame, pd.concat(groups, ignore_index=True) if groups else frame.iloc[:0]


def _selection_error(group: Any, rules: list[dict[str, Any]], candidates: list[str]) -> str | None:
    if not rules:
        return "uncovered_event"
    if len(rules) > 1:
        return "duplicate_coverage"
    allowed = rules[0]["allowed_comp_types"]
    if len(allowed) != 1:
        return "ambiguous_selection"
    if allowed[0] not in candidates:
        return "absent_comp_type"
    if group["comp_type"].duplicated().any():
        return "duplicate_candidate_identity"
    return None


def _event(
    key: tuple[str, ...], group: Any, rules: list[dict[str, Any]], delay: int
) -> tuple[dict, list]:
    dataset, symbol, period, disclosure, available = key
    candidates = sorted(set(group["comp_type"]))
    row = {
        "dataset": dataset,
        "symbol": symbol,
        "report_period": period,
        "disclosure_date": disclosure,
        "available_date": available,
        "candidate_comp_types": candidates,
        "selected_comp_type": None,
        "rejected_comp_types": [],
        "source_observed_at": sorted(set(group["_source_retrieved_at"])),
        "differing_fields": sorted(
            column
            for column in group.select_dtypes("number")
            if group[column].nunique(dropna=False) > 1
        ),
    }
    source_times = [_timestamp(value) for value in row["source_observed_at"]]
    source_boundary = max(
        _date(disclosure).date(),
        max(stamp.astimezone(ZoneInfo("Asia/Shanghai")).date() for stamp in source_times),
    ) + timedelta(days=delay)
    row["observation_class"] = (
        "reconstructed" if source_boundary > _date(available).date() else "observed"
    )
    row["field_differences"] = json.loads(
        group[["comp_type", *row["differing_fields"]]].to_json(orient="records")
    )
    code = _selection_error(group, rules, candidates)
    if code:
        return row, [{"code": code, "event": list(key)}]
    decision = rules[0]
    decision_time = _timestamp(decision["observed_at"])
    if any(stamp > decision_time for stamp in source_times):
        return row, [{"code": "decision_precedes_source_observation", "event": list(key)}]
    stamps = [*source_times, decision_time, _timestamp(decision["evidence"]["observed_at"])]
    observed_date = max(stamp.astimezone(ZoneInfo("Asia/Shanghai")).date() for stamp in stamps)
    boundary = max(_date(disclosure).date(), observed_date)
    row.update(
        {
            "selected_comp_type": decision["allowed_comp_types"][0],
            "rejected_comp_types": sorted(set(candidates) - set(decision["allowed_comp_types"])),
            "decision_observed_at": decision["observed_at"],
            "evidence": decision["evidence"],
            "reason": decision["reason"],
            "observation_class": "reconstructed"
            if boundary + timedelta(days=delay) > _date(available).date()
            else "observed",
            "conservative_available_from_preview": (boundary + timedelta(days=delay)).strftime(
                "%Y%m%d"
            ),
        }
    )
    return row, []


def validate_statement_selector(*, selector_path: str | Path) -> dict[str, Any]:
    """Check mechanical coverage and lineage only; return an unpublishable preview."""
    path = Path(selector_path).expanduser().resolve()
    payload = json.loads(path.read_text())
    try:
        delay, decisions = _contract(payload)
        return _report(path, payload, delay, decisions)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"Malformed statement selector: {error}") from error


def _report(path: Path, payload: dict, delay: int, decisions: dict) -> dict[str, Any]:
    inputs = payload["normalized_inputs"]
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("normalized_inputs must be a nonempty list")
    report: dict[str, Any] = {
        "schema_version": "statement-selection-report.v1",
        "selector_sha256": _digest(path),
        "policy_id": payload["policy_id"],
        "policy_version": payload["policy_version"],
        "evidence_content_verified": False,
        "publication_ready": False,
        "validation_scope": "mechanical_identity_coverage_hash_time_only",
        "availability_preview": "conservative_only_not_existing_PIT_contract",
        "normalized_inputs": [],
        "decision_ledger": [],
        "errors": [],
    }
    seen_datasets, matched = set(), set()
    for entry in inputs:
        _fields(entry, {"dataset", "path", "sha256"})
        dataset = entry["dataset"]
        if dataset not in _DATASETS or dataset in seen_datasets:
            raise ValueError("Unsupported or duplicate normalized input dataset")
        seen_datasets.add(dataset)
        source = Path(entry["path"]).expanduser()
        source = source if source.is_absolute() else path.parent / source
        expected = _sha(entry["sha256"])
        if _digest(source) != expected:
            raise ValueError(f"Normalized source checksum mismatch: {dataset}")
        identity, conflicts = _conflicts(source)
        report["normalized_inputs"].append(
            {"dataset": dataset, "sha256": expected, "rows": len(identity)}
        )
        for event_key, group in conflicts.groupby(_KEYS, sort=True):
            available = (_date(event_key[2]) + timedelta(days=delay)).strftime("%Y%m%d")
            key = (dataset, *event_key, available)
            matched.add(key)
            row, errors = _event(key, group, decisions.get(key, []), delay)
            row["source_sha256"] = expected
            report["decision_ledger"].append(row)
            report["errors"].extend(errors)
    for key in sorted(decisions.keys() - matched):
        report["errors"].append({"code": "unmatched_decision", "event": list(key)})
    report["conflict_events"] = len(report["decision_ledger"])
    report["valid"] = not report["errors"]
    return report
