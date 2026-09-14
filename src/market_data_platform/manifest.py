from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _manifest_payload(manifest_path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"Manifest is not a mapping: {manifest_path}")
    return _mapping(payload)


def _text_or_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _infer_dataset(payload: Mapping[str, Any], schema_version: str) -> str | None:
    dataset = _text_or_none(payload.get("dataset"))
    if dataset is None and schema_version:
        return schema_version.split(".", 1)[0]
    return dataset


def _set_integer_total(
    totals: dict[str, Any],
    key: str,
    value: object,
) -> None:
    if value is not None and str(value).isdigit():
        totals.setdefault(key, int(str(value)))


def _apply_legacy_totals(
    payload: Mapping[str, Any],
    totals: dict[str, Any],
) -> None:
    _set_integer_total(totals, "rows", payload.get("row_count"))
    _set_integer_total(totals, "symbols", payload.get("symbol_count"))
    files = payload.get("files")
    if isinstance(files, list):
        totals.setdefault("files", len(files))


def _output_dir(payload: Mapping[str, Any], manifest_path: Path) -> str | None:
    output_dir = _text_or_none(payload.get("output_dir"))
    if output_dir is None and payload.get("source_path") is not None:
        return str(manifest_path.parent)
    return output_dir


def _first_query_text(
    query: Mapping[str, Any],
    keys: tuple[str, ...],
) -> str | None:
    for key in keys:
        value = _text_or_none(query.get(key))
        if value:
            return value
    return None


def _first_text(values: tuple[object, ...]) -> str | None:
    for value in values:
        text = _text_or_none(value)
        if text:
            return text
    return None


def _summary_dates(
    payload: Mapping[str, Any],
    query: Mapping[str, Any],
    date_range: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> tuple[object, object, str | None]:
    query_start_date = _first_query_text(query, ("start_date", "start", "from"))
    if not query_start_date:
        query_start_date = date_range.get("start") or summary.get("date_min")

    query_end_date = _first_query_text(
        query,
        ("end_date", "date", "mapping_date", "as_of_date"),
    )
    if not query_end_date:
        query_end_date = date_range.get("end") or summary.get("date_max")

    as_of_date = _first_text(
        (
            payload.get("as_of_date"),
            query.get("as_of_date"),
            query.get("mapping_date"),
            query_end_date,
            summary.get("date_max"),
        ),
    )
    return query_start_date, query_end_date, as_of_date


def load_manifest_summary(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve()
    payload = _manifest_payload(manifest_path)
    query = _mapping(payload.get("query"))
    totals = _mapping(payload.get("totals"))
    date_range = _mapping(payload.get("date_range"))
    summary = _mapping(payload.get("summary"))
    schema_version = str(payload.get("schema_version") or "").strip()
    dataset = _infer_dataset(payload, schema_version)
    _apply_legacy_totals(payload, totals)
    output_dir = _output_dir(payload, manifest_path)
    query_start_date, query_end_date, as_of_date = _summary_dates(
        payload,
        query,
        date_range,
        summary,
    )

    return {
        "manifest_path": str(manifest_path),
        "dataset": dataset,
        "provider": str(payload.get("provider") or "").strip() or None,
        "schema_version": schema_version or None,
        "status": str(payload.get("status") or "").strip() or None,
        "output_dir": output_dir,
        "snapshot_name": Path(output_dir).name if output_dir else manifest_path.parent.name,
        "query_start_date": query_start_date,
        "query_end_date": query_end_date,
        "as_of_date": as_of_date,
        "totals": totals,
    }
