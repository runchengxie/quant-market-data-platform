"""Shared provenance and observation primitives for A-share fundamentals."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import yaml

from market_data_platform.file_receipts import file_sha256

SHA256_HEX_LENGTH = 64


class FundamentalsDownloadError(RuntimeError):
    """Base error for a failed raw fundamentals query unit."""


class DuplicatePageError(FundamentalsDownloadError):
    """Raised when a paginated provider repeats the same page."""


class FieldValidationError(FundamentalsDownloadError):
    """Raised when a provider response misses required fields."""


class PitProvenanceError(FieldValidationError):
    """Raised when an as-of view cannot prove observability at the cutoff."""


PIT_SOURCE_COLUMNS = (
    "_source_dataset",
    "_source_raw_asset",
    "_source_run_id",
    "_source_retrieved_at",
    "_source_bundle_retrieval_start_date",
    "_source_bundle_available_date",
)


def date_token(value: object) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _valid_retrieval_timestamp(value: object) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _parse_date(value: str):
    token = date_token(value)
    if len(token) != 8:
        raise ValueError(f"Expected YYYYMMDD date, got: {value}")
    return datetime.strptime(token, "%Y%m%d").date()


def asset_parquet_files(asset_dir: str | Path) -> list[Path]:
    root = Path(asset_dir).expanduser().resolve()
    data_root = root / "data" if (root / "data").exists() else root
    return sorted(data_root.glob("**/*.parquet"))


def asset_manifest_payload(asset_dir: str | Path) -> dict[str, Any]:
    root = Path(asset_dir).expanduser().resolve()
    path = root / "manifest.yml" if root.is_dir() else root.parent / "manifest.yml"
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def seal_manifest(path: str | Path) -> dict[str, str]:
    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Cannot seal missing manifest: {manifest_path}")
    payload = {
        "schema_version": "tushare.a_share.fundamentals.manifest_seal.v1",
        "manifest": manifest_path.name,
        "manifest_sha256": file_sha256(manifest_path),
    }
    seal_path = manifest_path.with_name("manifest.seal.json")
    seal_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def manifest_seal_checks(asset_dir: str | Path) -> dict[str, bool]:
    root = Path(asset_dir).expanduser().resolve()
    manifest_path = root / "manifest.yml"
    seal_path = root / "manifest.seal.json"
    seal = _json_mapping(seal_path)
    declared_hash = str(seal.get("manifest_sha256") or "")
    return {
        "manifest_exists": manifest_path.is_file(),
        "seal_exists": seal_path.is_file(),
        "seal_schema_valid": (
            seal.get("schema_version") == "tushare.a_share.fundamentals.manifest_seal.v1"
        ),
        "manifest_hash_matches": manifest_path.is_file()
        and len(declared_hash) == SHA256_HEX_LENGTH
        and file_sha256(manifest_path) == declared_hash,
    }


def require_manifest_seal(asset_dir: str | Path) -> None:
    checks = manifest_seal_checks(asset_dir)
    if all(checks.values()):
        return
    failed = ", ".join(name for name, passed in checks.items() if not passed)
    raise PitProvenanceError(f"Asset manifest seal failed: {failed}")


def build_asset_integrity(
    asset_dir: str | Path,
    files: Sequence[str | Path],
) -> dict[str, Any]:
    root = Path(asset_dir).expanduser().resolve()
    receipts = []
    for value in sorted({Path(path).expanduser().resolve() for path in files}):
        try:
            relative = value.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Integrity file is outside asset root: {value}") from exc
        if not value.is_file():
            raise FileNotFoundError(f"Integrity file is missing: {value}")
        receipts.append(
            {
                "path": relative.as_posix(),
                "bytes": value.stat().st_size,
                "sha256": file_sha256(value),
            }
        )
    canonical = json.dumps(receipts, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {
        "algorithm": "sha256",
        "aggregate_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "files": receipts,
    }


def asset_integrity_checks(
    asset_dir: str | Path,
    integrity: object,
) -> dict[str, bool]:
    root = Path(asset_dir).expanduser().resolve()
    payload = integrity if isinstance(integrity, Mapping) else {}
    raw_receipts = payload.get("files")
    receipts = (
        [dict(row) for row in raw_receipts if isinstance(row, Mapping)]
        if isinstance(raw_receipts, Sequence) and not isinstance(raw_receipts, (str, bytes))
        else []
    )
    declared_paths = [str(row.get("path") or "").strip() for row in receipts]
    valid_paths = all(
        path and not Path(path).is_absolute() and ".." not in Path(path).parts
        for path in declared_paths
    )
    resolved = [(root / path).resolve() for path in declared_paths] if valid_paths else []
    inside_root = valid_paths and all(path.is_relative_to(root) for path in resolved)
    hashes_declared = all(
        len(str(row.get("sha256") or "")) == SHA256_HEX_LENGTH for row in receipts
    )
    files_match = (
        bool(receipts)
        and inside_root
        and hashes_declared
        and all(
            path.is_file()
            and path.stat().st_size == int(row.get("bytes", -1))
            and file_sha256(path) == str(row.get("sha256"))
            for path, row in zip(resolved, receipts, strict=True)
        )
    )
    canonical = json.dumps(receipts, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    aggregate = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "algorithm_sha256": payload.get("algorithm") == "sha256",
        "receipts_nonempty": bool(receipts),
        "paths_unique": bool(declared_paths) and len(set(declared_paths)) == len(declared_paths),
        "paths_safe": bool(inside_root),
        "file_hashes_match": files_match,
        "aggregate_hash_matches": payload.get("aggregate_sha256") == aggregate,
    }


def require_asset_integrity(asset_dir: str | Path, payload: Mapping[str, Any]) -> None:
    require_manifest_seal(asset_dir)
    checks = asset_integrity_checks(asset_dir, payload.get("integrity"))
    if all(checks.values()):
        return
    failed = ", ".join(name for name, passed in checks.items() if not passed)
    raise PitProvenanceError(f"Asset content integrity failed: {failed}")


def require_mutable_asset_output(asset_dir: str | Path) -> None:
    root = Path(asset_dir).expanduser().resolve()
    payload = asset_manifest_payload(root)
    if payload.get("status") != "completed" or payload.get("immutable_snapshot") is not True:
        return
    require_asset_integrity(root, payload)
    raise FileExistsError(f"Completed asset snapshot is immutable; use a new out_dir: {root}")


def _resolved_manifest_path(root: Path, value: object) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return ((root / path) if not path.is_absolute() else path).resolve()


def _json_mapping(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def _raw_integrity_checks(
    root: Path,
    payload: Mapping[str, Any],
    parts: Sequence[Mapping[str, Any]],
    resolved_parts: Sequence[Path],
    part_hashes: Sequence[str],
) -> dict[str, bool]:
    hashes_declared = bool(part_hashes) and all(
        len(value) == SHA256_HEX_LENGTH for value in part_hashes
    )
    hashes_match = len(resolved_parts) == len(parts) and all(
        path.is_file() and file_sha256(path) == part_hash
        for path, part_hash in zip(resolved_parts, part_hashes, strict=True)
    )
    return {
        "parts_have_content_sha256": hashes_declared,
        "part_content_hashes_match": hashes_match,
        "asset_integrity_valid": all(
            asset_integrity_checks(root, payload.get("integrity")).values()
        ),
        "manifest_seal_valid": all(manifest_seal_checks(root).values()),
    }


def raw_bundle_completeness(
    asset_dir: str | Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return strict proof that every planned raw query unit is a complete, observed part."""

    root = Path(asset_dir).expanduser().resolve()
    raw_parts = payload.get("parts")
    parts = (
        [dict(row) for row in raw_parts if isinstance(row, Mapping)]
        if isinstance(raw_parts, Sequence) and not isinstance(raw_parts, (str, bytes))
        else []
    )
    part_paths = [_resolved_manifest_path(root, row.get("path")) for row in parts]
    resolved_parts = [path for path in part_paths if path is not None]
    part_units = [str(row.get("unit_id") or "").strip() for row in parts]
    part_timestamps = [str(row.get("retrieved_at") or "").strip() for row in parts]
    part_hashes = [str(row.get("content_sha256") or "").strip() for row in parts]
    actual_files = set(asset_parquet_files(root))
    declared_files = set(resolved_parts)
    totals = payload.get("totals")
    totals = totals if isinstance(totals, Mapping) else {}
    query = payload.get("query")
    query = query if isinstance(query, Mapping) else {}
    query_parameters = query.get("query_parameters")
    query_parameters = (
        query_parameters
        if isinstance(query_parameters, Sequence) and not isinstance(query_parameters, (str, bytes))
        else []
    )
    state = _json_mapping(_resolved_manifest_path(root, payload.get("state_file")))
    failures = _json_mapping(_resolved_manifest_path(root, payload.get("failure_report")))
    plan = state.get("plan")
    plan = (
        [str(value) for value in plan]
        if isinstance(plan, Sequence) and not isinstance(plan, (str, bytes))
        else []
    )
    units = state.get("units")
    units = units if isinstance(units, Mapping) else {}
    failed_units = failures.get("failed_units")
    failed_units = (
        failed_units
        if isinstance(failed_units, Sequence) and not isinstance(failed_units, (str, bytes))
        else ["invalid_failure_report"]
    )
    parts_by_unit = {
        str(row.get("unit_id") or "").strip(): row
        for row in parts
        if str(row.get("unit_id") or "").strip()
    }
    checks = {
        "manifest_status_completed": payload.get("status") == "completed",
        "manifest_failed_units_zero": totals.get("failed_units") == 0,
        "parts_nonempty": bool(parts),
        "parts_all_completed": all(row.get("status") == "completed" for row in parts),
        "parts_have_unique_paths": len(resolved_parts) == len(parts)
        and len(declared_files) == len(parts),
        "parts_have_unique_units": all(part_units) and len(set(part_units)) == len(part_units),
        "parts_have_retrieval_timestamps": all(part_timestamps),
        "parts_have_valid_retrieval_timestamps": all(
            _valid_retrieval_timestamp(value) for value in part_timestamps
        ),
        **_raw_integrity_checks(root, payload, parts, resolved_parts, part_hashes),
        "parts_match_actual_parquet_files": declared_files == actual_files,
        "manifest_counts_match_parts": totals.get("files") == len(parts)
        and totals.get("query_units") == len(parts)
        and len(query_parameters) == len(parts),
        "state_plan_matches_parts": bool(plan)
        and len(plan) == len(parts)
        and set(plan) == set(part_units),
        "state_units_complete": set(units) == set(plan)
        and all(
            isinstance(units.get(unit_id), Mapping) and units[unit_id].get("status") == "completed"
            for unit_id in plan
        ),
        "state_units_match_manifest_parts": set(parts_by_unit) == set(plan)
        and all(
            _resolved_manifest_path(root, units[unit_id].get("path"))
            == _resolved_manifest_path(root, parts_by_unit[unit_id].get("path"))
            and str(units[unit_id].get("retrieved_at") or "").strip()
            == str(parts_by_unit[unit_id].get("retrieved_at") or "").strip()
            and str(units[unit_id].get("content_sha256") or "").strip()
            == str(parts_by_unit[unit_id].get("content_sha256") or "").strip()
            for unit_id in plan
            if isinstance(units.get(unit_id), Mapping)
        ),
        "state_watermark_complete": bool(plan) and state.get("contiguous_watermark") == plan[-1],
        "failure_report_clear": not failed_units,
    }
    return {
        "schema_version": "tushare.a_share.fundamentals.raw_completeness.v1",
        "production_eligible": all(checks.values()),
        "checks": checks,
        "declared_part_count": len(parts),
        "actual_parquet_count": len(actual_files),
        "planned_query_unit_count": len(plan),
    }


def manifest_as_of_date(payload: Mapping[str, Any]) -> str | None:
    query = payload.get("query")
    query_mapping = query if isinstance(query, Mapping) else {}
    for value in (
        payload.get("as_of_date"),
        query_mapping.get("as_of_date"),
        query_mapping.get("end_date"),
        payload.get("query_end_date"),
    ):
        token = date_token(value)
        if token:
            return token
    return None


def manifest_retrieved_at(payload: Mapping[str, Any]) -> str:
    retrieved_at = payload.get("retrieved_at")
    if isinstance(retrieved_at, str) and retrieved_at.strip():
        return retrieved_at.strip()
    source_retrieved_at = payload.get("source_retrieved_at")
    if isinstance(source_retrieved_at, str) and source_retrieved_at.strip():
        return source_retrieved_at.strip()
    timestamps = manifest_retrieval_timestamps(payload, include_fallback=False)
    return timestamps[-1] if timestamps else ""


def manifest_retrieval_timestamps(
    payload: Mapping[str, Any],
    *,
    include_fallback: bool = True,
) -> list[str]:
    parts = payload.get("parts")
    if isinstance(parts, Sequence) and not isinstance(parts, (str, bytes)):
        timestamps = sorted(
            {
                str(row.get("retrieved_at") or "").strip()
                for row in parts
                if isinstance(row, Mapping) and str(row.get("retrieved_at") or "").strip()
            }
        )
        if timestamps:
            return timestamps
    source_values = payload.get("source_retrieved_at")
    if isinstance(source_values, Sequence) and not isinstance(source_values, (str, bytes)):
        timestamps = sorted({str(value).strip() for value in source_values if str(value).strip()})
        if timestamps:
            return timestamps
    if not include_fallback:
        return []
    fallback = manifest_retrieved_at(payload)
    return [fallback] if fallback else []


def date_values(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return sorted({token for item in value if (token := date_token(item))})


def manifest_source_vintage_dates(payload: Mapping[str, Any]) -> dict[str, list[str]]:
    components = payload.get("source_observed_vintage_dates")
    if isinstance(components, Mapping):
        return {str(source): date_values(values) for source, values in components.items()}
    observed = date_values(payload.get("observed_vintage_dates"))
    component = str(payload.get("source_dataset") or "asset")
    return {component: observed} if observed else {}


def _bundle_observation_row(value: Mapping[str, Any]) -> dict[str, Any] | None:
    available = date_token(value.get("bundle_available_date"))
    retrieval_start = date_token(value.get("retrieval_start_date"))
    retrieval_end = date_token(value.get("retrieval_end_date"))
    if not available or not retrieval_start or not retrieval_end:
        return None
    if retrieval_start > retrieval_end or retrieval_end > available:
        return None
    return {
        "bundle_available_date": available,
        "retrieval_start_date": retrieval_start,
        "retrieval_end_date": retrieval_end,
        "retrieval_span_days": days_between(retrieval_start, retrieval_end),
    }


def manifest_source_bundle_observations(
    payload: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Read the per-component complete-bundle ladder needed for oldest-part freshness."""

    declared = payload.get("source_bundle_observations")
    if isinstance(declared, Mapping):
        result: dict[str, list[dict[str, Any]]] = {}
        for source, values in declared.items():
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                continue
            rows = [
                row
                for value in values
                if isinstance(value, Mapping)
                and (row := _bundle_observation_row(value)) is not None
            ]
            if rows:
                result[str(source)] = sorted(
                    rows,
                    key=lambda row: (row["bundle_available_date"], row["retrieval_start_date"]),
                )
        return result
    observation = payload.get("source_observation")
    component = str(payload.get("source_dataset") or "asset")
    if isinstance(observation, Mapping):
        row = _bundle_observation_row(observation)
        if row is not None:
            return {component: [row]}
    return {}


def manifest_max_observation_age_days(payload: Mapping[str, Any]) -> int | None:
    policy = payload.get("freshness_policy")
    policy = policy if isinstance(policy, Mapping) else {}
    value = policy.get("max_observation_age_days")
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def days_between(start_date: str, end_date: str) -> int:
    return (_parse_date(end_date) - _parse_date(start_date)).days


def add_days(value: str, days: int) -> str:
    return (_parse_date(value) + timedelta(days=days)).strftime("%Y%m%d")


def bundle_observation_state(
    source_vintages: Mapping[str, Sequence[str]],
    *,
    as_of_date: str,
    max_age_days: int | None,
    source_bundle_observations: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    latest_by_source = {
        source: max((value for value in values if value <= as_of_date), default=None)
        for source, values in source_vintages.items()
    }
    missing_sources = sorted(source for source, value in latest_by_source.items() if value is None)
    observed = [value for value in latest_by_source.values() if value]
    bundle_ladder = source_bundle_observations or {}
    selected_start_by_source: dict[str, str | None] = {}
    selected_span_by_source: dict[str, int | None] = {}
    for source, completion in latest_by_source.items():
        matching = [
            row
            for row in bundle_ladder.get(source, ())
            if date_token(row.get("bundle_available_date")) == completion
        ]
        starts = [date_token(row.get("retrieval_start_date")) for row in matching]
        starts = [value for value in starts if value]
        selected_start = min(starts) if starts else None
        selected_start_by_source[source] = selected_start
        selected_span_by_source[source] = (
            days_between(selected_start, completion)
            if selected_start is not None and completion is not None
            else None
        )
    missing_bundle_proofs = sorted(
        source
        for source, completion in latest_by_source.items()
        if completion is not None and selected_start_by_source.get(source) is None
    )
    selected_starts = [value for value in selected_start_by_source.values() if value]
    oldest = min(selected_starts) if selected_starts else None
    bundle_available = max(observed) if observed and not missing_sources else None
    age_days = days_between(oldest, as_of_date) if oldest else None
    revision_covered = bool(source_vintages) and not missing_sources and not missing_bundle_proofs
    freshness_verified = bool(
        revision_covered
        and max_age_days is not None
        and age_days is not None
        and age_days <= max_age_days
    )
    return {
        "as_of_date": as_of_date,
        "latest_observed_vintage_by_source": latest_by_source,
        "selected_bundle_retrieval_start_by_source": selected_start_by_source,
        "selected_bundle_retrieval_span_days_by_source": selected_span_by_source,
        "missing_observation_sources": missing_sources,
        "missing_bundle_observation_proofs": missing_bundle_proofs,
        "oldest_component_retrieval_date": oldest,
        "bundle_available_date": bundle_available,
        "observation_age_days": age_days,
        "max_observation_age_days": max_age_days,
        "revision_covered": revision_covered,
        "freshness_verified": freshness_verified,
    }


def observation_contract(
    payload: Mapping[str, Any],
    *,
    max_age_days: int,
) -> dict[str, Any]:
    timestamps = manifest_retrieval_timestamps(payload)
    dates = sorted({token for value in timestamps if (token := date_token(value))})
    declared_vintages = date_values(payload.get("observed_vintage_dates"))
    observed_vintages = dates[-1:] if dates else []
    if declared_vintages and declared_vintages != observed_vintages:
        raise PitProvenanceError(
            "declared observed_vintage_dates do not match completed raw part retrievals"
        )
    latest_vintage = observed_vintages[-1] if observed_vintages else None
    retrieval_start = dates[0] if dates else None
    valid_through = add_days(retrieval_start, max_age_days) if retrieval_start else None
    freshness_valid_through = (
        valid_through
        if valid_through and latest_vintage and valid_through >= latest_vintage
        else None
    )
    return {
        "retrieval_start_date": retrieval_start,
        "retrieval_end_date": dates[-1] if dates else None,
        "retrieval_span_days": days_between(dates[0], dates[-1]) if dates else None,
        "bundle_available_date": latest_vintage,
        "observed_vintage_dates": observed_vintages,
        "freshness_valid_through_date": freshness_valid_through,
        "max_observation_age_days": max_age_days,
        "retrieval_timestamps": timestamps,
    }


def schema_columns(paths: Sequence[Path]) -> set[str]:
    columns: set[str] = set()
    for path in paths:
        columns.update(pq.read_schema(path).names)
    return columns


def assert_unambiguous_pit_events(frame: Any, value_columns: Sequence[str]) -> None:
    keys = [
        "symbol",
        "report_period",
        "available_date",
        "_source_dataset",
        "_source_retrieved_at",
    ]
    if frame.empty or not set(keys) <= set(frame.columns):
        return
    duplicate_rows = frame[frame.duplicated(subset=keys, keep=False)]
    for key, group in duplicate_rows.groupby(keys, dropna=False, sort=False):
        conflicts = [
            column
            for column in value_columns
            if column in group and group[column].dropna().nunique(dropna=True) > 1
        ]
        if conflicts:
            identity = ", ".join(str(value) for value in key)
            raise FieldValidationError(
                "PIT fundamentals contain an ambiguous same-day revision for "
                f"({identity}); conflicting fields: {', '.join(conflicts)}."
            )
