"""Stateful TuShare A-share fundamentals ingestion and PIT publication."""

from __future__ import annotations

import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from market_data_platform.parquet_scanning import ParquetBatchScanner
from market_data_platform.providers.tushare_a_share_fundamentals_part01 import (
    DATASET_SPECS,
    DEFAULT_FUNDAMENTALS_MAX_OBSERVATION_AGE_DAYS,
    DEFAULT_PIT_BATCH_ROWS,
    DEFAULT_PIT_BUCKET_COUNT,
    STATEMENT_DATASETS,
    SUPPORTED_STATEMENT_COMP_TYPES,
)
from market_data_platform.providers.tushare_a_share_fundamentals_part02 import (
    _assert_unambiguous_normalized_rows,
    _first_present,
    _revision_safe_raw_inputs,
    _select_latest_provider_update,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitAsOfPanel as PitAsOfPanel,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitAsOfView as PitAsOfView,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitFundamentalsEvents as PitFundamentalsEvents,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of as load_pit_fundamentals_as_of,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of_panel as load_pit_fundamentals_as_of_panel,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of_view as load_pit_fundamentals_as_of_view,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_events as load_pit_fundamentals_events,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    PIT_SOURCE_COLUMNS,
    FieldValidationError,
    PitProvenanceError,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    asset_integrity_checks as _asset_integrity_checks,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    asset_manifest_payload as _asset_manifest_payload,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    asset_parquet_files as _asset_parquet_files,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    build_asset_integrity as _build_asset_integrity,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    bundle_observation_state as _bundle_observation_state,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    date_token as _date_token,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    date_values as _date_values,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    file_sha256 as _file_sha256,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    manifest_as_of_date as _manifest_as_of_date,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    manifest_max_observation_age_days as _manifest_max_observation_age_days,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    manifest_seal_checks as _manifest_seal_checks,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    manifest_source_bundle_observations as _manifest_source_bundle_observations,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    manifest_source_vintage_dates as _manifest_source_vintage_dates,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    require_asset_integrity as _require_asset_integrity,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    require_mutable_asset_output as _require_mutable_asset_output,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    schema_columns as _schema_columns,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    seal_manifest as _seal_manifest,
)
from market_data_platform.providers.tushare_common import (
    normalize_ts_code,
    pandas,
    write_manifest,
)
from market_data_platform.runtime_memory import MemoryPolicy


def _norm_integrity(
    source: Path,
    output: Path,
    raw_payload: Mapping[str, Any],
    observation: Mapping[str, Any],
    part: Path,
) -> dict[str, Any]:
    return {
        "immutable_snapshot": True,
        "source_integrity": {
            "manifest_sha256": _file_sha256(source / "manifest.yml"),
            "content_aggregate_sha256": raw_payload["integrity"]["aggregate_sha256"],
        },
        "integrity": _build_asset_integrity(output, [part]),
        "validity": {
            "observed_from": observation["bundle_available_date"],
            "freshness_valid_through": observation["freshness_valid_through_date"],
        },
        "revision_safety": {
            "observation_class": "observed_vintage",
            "revision_safe_from": observation["bundle_available_date"],
            "historical_periods_before_first_observation": "reconstructed_pit",
        },
    }


def _normalized_integrity_validation_checks(
    asset_dir: str | Path,
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    integrity = _asset_integrity_checks(asset_dir, manifest.get("integrity"))
    manifest_seal = _manifest_seal_checks(asset_dir)
    return [
        {
            "id": "content_integrity",
            "passed": all(integrity.values()),
            "details": integrity,
        },
        {
            "id": "manifest_seal",
            "passed": all(manifest_seal.values()),
            "details": manifest_seal,
        },
    ]


def _write_normalized_manifest(output: Path, manifest: dict[str, Any]) -> None:
    manifest_path = output / "manifest.yml"
    write_manifest(manifest_path, manifest)
    _seal_manifest(manifest_path)


def _filter_statement_company_types(dataset: str, frame: Any, dropped: dict[str, int]) -> Any:
    if dataset not in STATEMENT_DATASETS or "comp_type" not in frame:
        return frame
    company_type = frame["comp_type"].fillna("").astype(str).str.strip()
    unsupported = company_type.ne("") & ~company_type.isin(SUPPORTED_STATEMENT_COMP_TYPES)
    dropped["unsupported_comp_type"] = int(unsupported.sum())
    frame = frame.loc[~unsupported].copy()
    if frame.empty or "comp_type" not in frame:
        return frame
    # Prefer consolidated statements when the provider returns both consolidated
    # and parent-company rows for the same reported observation. Keep a
    # non-consolidated row only when no consolidated row exists for that key.
    key_columns = [
        column for column in ("ts_code", "end_date", "report_type", "f_ann_date") if column in frame
    ]
    if key_columns:
        consolidated_keys = frame.loc[frame["comp_type"].eq("1"), key_columns].drop_duplicates()
        if not consolidated_keys.empty:
            marker = frame.merge(
                consolidated_keys.assign(_has_consolidated=True),
                on=key_columns,
                how="left",
            )["_has_consolidated"].fillna(False)
            prefer_consolidated = marker & frame["comp_type"].ne("1")
            dropped["non_consolidated_with_consolidated"] = int(prefer_consolidated.sum())
            frame = frame.loc[~prefer_consolidated].copy()
    return frame


def _filter_normalized_report_rows(
    dataset: str,
    spec: Any,
    frame: Any,
    dropped: dict[str, int],
) -> Any:
    if spec.standard_report_type and "report_type" in frame:
        supported = frame["report_type"].astype(str) == spec.standard_report_type
        dropped["unsupported_report_type"] = int((~supported).sum())
        frame = frame.loc[supported].copy()
    return _filter_statement_company_types(dataset, frame, dropped)


def build_normalized_fundamentals(
    *,
    dataset: str,
    raw_dir: str | Path,
    out_dir: str | Path,
) -> dict[str, Any]:
    pd = pandas()
    spec = DATASET_SPECS[dataset]
    source = Path(raw_dir).expanduser().resolve()
    output = Path(out_dir).expanduser().resolve()
    _require_mutable_asset_output(output)
    raw_payload = _asset_manifest_payload(source)
    raw_completeness, observation, frames = _revision_safe_raw_inputs(source, raw_payload)
    frame = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    source_rows, dropped = int(len(frame)), {}
    if "ts_code" not in frame:
        raise FieldValidationError(f"{dataset} raw asset is missing ts_code.")
    frame["symbol"] = frame["ts_code"].map(normalize_ts_code)
    invalid_symbols = frame["symbol"] == ""
    dropped["invalid_symbol"] = int(invalid_symbols.sum())
    frame = frame.loc[~invalid_symbols].copy()
    for column in spec.date_fields:
        if column in frame:
            frame[column] = frame[column].map(_date_token)
    report_period_col = _first_present(frame, spec.report_period_fields)
    if report_period_col:
        frame["report_period"] = frame[report_period_col].map(_date_token)
    disclosure_col = _first_present(frame, spec.disclosure_fields)
    if disclosure_col:
        frame["disclosure_date"] = frame[disclosure_col].map(_date_token)
    frame = _filter_normalized_report_rows(dataset, spec, frame, dropped)
    keys = ["symbol", *[key for key in spec.dedupe_keys if key != "ts_code" and key in frame]]
    if "_source_retrieved_at" in frame:
        keys.append("_source_retrieved_at")
    frame, dropped["superseded_update_flag"], dropped["superseded_ann_date"] = (
        _select_latest_provider_update(frame, keys)
    )
    duplicate_rows = int(frame.duplicated(subset=keys, keep="last").sum()) if keys else 0
    dropped["duplicate_key"] = duplicate_rows
    _assert_unambiguous_normalized_rows(frame, keys)
    frame = frame.drop_duplicates(subset=keys, keep="last") if keys else frame
    frame["_source_dataset"] = dataset
    frame["_source_raw_asset"] = str(source)
    frame["_source_run_id"] = raw_payload.get("source_run_id") or source.name
    frame["_source_bundle_retrieval_start_date"] = observation["retrieval_start_date"]
    frame["_source_bundle_available_date"] = observation["bundle_available_date"]
    raw_query = raw_payload.get("query")
    raw_query = raw_query if isinstance(raw_query, Mapping) else {}
    report_period_query_start = _date_token(raw_query.get("start_date")) or None
    report_period_query_end = _date_token(raw_query.get("end_date")) or None
    part = output / "data" / "part.parquet"
    part.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(part, index=False)
    integrity_metadata = _norm_integrity(source, output, raw_payload, observation, part)
    manifest = {
        "schema_version": "tushare.a_share.fundamentals.normalized.v2",
        "dataset": "normalized_fundamentals",
        "source_dataset": dataset,
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        **integrity_metadata,
        "source_raw_status": raw_payload.get("status"),
        "source_raw_completeness": raw_completeness,
        "output_dir": str(output),
        "source_raw_dir": str(source),
        "as_of_date": observation["freshness_valid_through_date"],
        "bundle_available_date": observation["bundle_available_date"],
        "source_observation": observation,
        "observed_vintage_dates": observation["observed_vintage_dates"],
        "freshness_policy": {
            "max_observation_age_days": DEFAULT_FUNDAMENTALS_MAX_OBSERVATION_AGE_DAYS,
        },
        "source_retrieved_at": observation["retrieval_timestamps"],
        "query": {
            "start_date": report_period_query_start,
            "end_date": report_period_query_end,
        },
        "report_period_query": {
            "start_date": report_period_query_start,
            "end_date": report_period_query_end,
        },
        "semantics": {
            "raw_preserves_all_report_types": True,
            "normalized_standard_report_type": spec.standard_report_type,
            "dedupe_keys": keys,
            "source_observation_provenance": "_source_retrieved_at",
            "query_range_semantics": "report_period_not_freshness",
            "freshness_semantics": "raw_retrieval_vintage",
            "ambiguous_same_day_revisions": "fail_closed",
            "provider_update_flag": "highest_numeric_flag_wins_within_observation",
            "provider_ann_date": "latest_announcement_date_wins_after_update_flag",
        },
        "dropped_rows": dropped,
        "totals": {"rows": int(len(frame)), "source_rows": source_rows, "files": 1},
        "columns": sorted(frame.columns),
    }
    _write_normalized_manifest(output, manifest)
    return manifest


def validate_normalized_fundamentals(
    *,
    asset_dir: str | Path,
    target_date: str | None = None,
    batch_rows: int = DEFAULT_PIT_BATCH_ROWS,
    memory_policy: MemoryPolicy | None = None,
) -> dict[str, Any]:
    files = _asset_parquet_files(asset_dir)
    columns = _schema_columns(files) if files else set()
    manifest = _asset_manifest_payload(asset_dir)
    schema_version = str(manifest.get("schema_version") or "")
    as_of_date = _manifest_as_of_date(manifest)
    source_vintages = _manifest_source_vintage_dates(manifest)
    source_bundle_observations = _manifest_source_bundle_observations(manifest)
    max_observation_age_days = _manifest_max_observation_age_days(manifest)
    expected_target = _date_token(target_date) or None
    observation_state = (
        _bundle_observation_state(
            source_vintages,
            as_of_date=expected_target,
            max_age_days=max_observation_age_days,
            source_bundle_observations=source_bundle_observations,
        )
        if expected_target
        else None
    )
    required = {"symbol", *PIT_SOURCE_COLUMNS}
    missing = sorted(required - columns)
    rows = 0
    missing_provenance = 0
    symbols: set[str] = set()
    projected = [column for column in ("symbol", *PIT_SOURCE_COLUMNS) if column in columns]
    scanner = ParquetBatchScanner(
        columns=projected,
        batch_rows=batch_rows,
        memory_policy=memory_policy or MemoryPolicy(),
        stage="normalized_fundamentals_validate",
    )
    for _, frame in scanner.iter_frames(files):
        rows += int(len(frame))
        if "symbol" in frame:
            symbols.update(frame["symbol"].dropna().astype(str).unique().tolist())
        provenance = [column for column in PIT_SOURCE_COLUMNS if column in frame]
        if provenance:
            missing_source = (
                frame.loc[:, provenance]
                .fillna("")
                .astype(str)
                .apply(lambda row: row.str.strip().eq("").any(), axis=1)
            )
            missing_provenance += int(missing_source.sum())
    checks = [
        {"id": "required_columns", "passed": not missing, "missing": missing},
        {"id": "non_empty", "passed": bool(rows)},
        *_normalized_integrity_validation_checks(asset_dir, manifest),
        {
            "id": "complete_raw_bundle_provenance",
            "passed": bool(
                manifest.get("source_raw_status") == "completed"
                and isinstance(manifest.get("source_raw_completeness"), Mapping)
                and manifest["source_raw_completeness"].get("production_eligible") is True
            ),
        },
        {
            "id": "source_retrieval_provenance",
            "passed": not schema_version.endswith(".v2")
            or (set(PIT_SOURCE_COLUMNS) <= columns and missing_provenance == 0),
            "missing": missing_provenance,
            "legacy_compatible": not schema_version.endswith(".v2"),
        },
    ]
    if "symbol" in columns:
        checks.append({"id": "symbol_coverage", "passed": bool(symbols)})
    if expected_target:
        checks.extend(
            [
                {
                    "id": "revision_provenance_schema",
                    "passed": schema_version == "tushare.a_share.fundamentals.normalized.v2",
                    "actual_schema_version": schema_version or None,
                },
                {
                    "id": "observation_vintage_supports_target_date",
                    "passed": bool(
                        observation_state
                        and observation_state["revision_covered"]
                        and observation_state["freshness_verified"]
                    ),
                    "actual_as_of_date": as_of_date,
                    "target_date": expected_target,
                    "observation_state": observation_state,
                },
            ]
        )
    return {
        "status": "passed" if all(row["passed"] for row in checks) else "failed",
        "checks": checks,
        "totals": {
            "rows": rows,
            "symbols": len(symbols),
            "files": len(files),
        },
        "as_of_date": as_of_date,
        "observation_state": observation_state,
        "target_date": expected_target,
        "scan": scanner.telemetry.to_dict(),
    }


NORMALIZED_UNION_COMPONENTS_DIR = "components"


def _normalized_component_name(source: Path, manifest: Mapping[str, Any]) -> str:
    return str(manifest.get("source_dataset") or "").strip() or source.name


def _component_retrieval_timestamps(manifest: Mapping[str, Any]) -> list[str]:
    values = manifest.get("source_retrieved_at")
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        return [str(value).strip() for value in values if str(value).strip()]
    return []


def build_normalized_fundamentals_union(
    *,
    normalized_dirs: Iterable[str | Path],
    out_dir: str | Path,
) -> dict[str, Any]:
    """Assemble immutable normalized v2 components into one publishable asset.

    The composite output keeps each component's own manifest and data under
    ``components/<source_dataset>/`` and writes a top-level normalized v2 manifest so
    the whole tree is a single ``normalized_fundamentals`` asset that passes the same
    validation gates as a single-dataset snapshot.
    """
    sources = [Path(path).expanduser().resolve() for path in normalized_dirs]
    if not sources:
        raise ValueError("At least one normalized fundamentals directory is required.")
    if len({str(source) for source in sources}) != len(sources):
        raise ValueError("normalized_dirs must not contain duplicate paths.")
    source_manifests = {str(source): _asset_manifest_payload(source) for source in sources}
    _require_revision_safe_normalized_sources(sources, source_manifests)
    output = Path(out_dir).expanduser().resolve()
    _require_mutable_asset_output(output)
    components_root = output / NORMALIZED_UNION_COMPONENTS_DIR
    if components_root.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing normalized union components dir: {components_root}"
        )
    names: dict[str, Path] = {}
    for source in sources:
        name = _normalized_component_name(source, source_manifests[str(source)])
        if name in names:
            raise ValueError(f"Duplicate normalized component name: {name}")
        names[name] = source
    for name in sorted(names):
        shutil.copytree(names[name], components_root / name)
    files = sorted(
        path for name in sorted(names) for path in _asset_parquet_files(components_root / name)
    )
    if not files:
        raise FileNotFoundError("No normalized fundamentals parquet files found.")
    columns = _schema_columns(files)
    symbols: set[str] = set()
    union_rows = 0
    scanner = ParquetBatchScanner(
        columns=["symbol"],
        batch_rows=DEFAULT_PIT_BATCH_ROWS,
        memory_policy=MemoryPolicy(),
        stage="normalized_fundamentals_union_symbols",
    )
    for _, frame in scanner.iter_frames(files):
        union_rows += int(len(frame))
        if "symbol" in frame:
            symbols.update(frame["symbol"].dropna().astype(str).unique().tolist())

    provenance = _normalized_union_provenance(source_manifests, names)
    manifest = _normalized_union_manifest(
        output=output,
        components=sorted(names.items()),
        files=files,
        provenance=provenance,
        scan={
            "columns": columns,
            "symbols": symbols,
            "rows": union_rows,
            "telemetry": scanner.telemetry.to_dict(),
        },
    )
    _write_normalized_manifest(output, manifest)
    return manifest


def _normalized_union_provenance(
    source_manifests: Mapping[str, Mapping[str, Any]],
    names: Mapping[str, Path],
) -> dict[str, Any]:
    source_integrity: dict[str, Any] = {}
    source_observed_vintage_dates: dict[str, list[str]] = {}
    source_bundle_observations: dict[str, list[dict[str, Any]]] = {}
    source_retrieved_at: dict[str, list[str]] = {}
    source_observation: dict[str, dict[str, Any]] = {}
    source_raw_completeness: dict[str, dict[str, Any]] = {}
    component_metadata: dict[str, dict[str, Any]] = {}
    as_of_dates: list[str] = []
    bundle_dates: list[str] = []
    query_starts: list[str] = []
    query_ends: list[str] = []
    max_observation_age_days_values: list[int] = []
    totals_source_rows = 0
    for name in sorted(names):
        source = names[name]
        manifest = source_manifests[str(source)]
        source_integrity[str(source)] = {
            "manifest_sha256": _file_sha256(source / "manifest.yml"),
            "content_aggregate_sha256": manifest["integrity"]["aggregate_sha256"],
        }
        vintage_dates = _date_values(manifest.get("observed_vintage_dates"))
        source_observed_vintage_dates[name] = vintage_dates
        source_bundle_observations.update(_manifest_source_bundle_observations(manifest))
        source_retrieved_at[name] = _component_retrieval_timestamps(manifest)
        observation = manifest.get("source_observation")
        source_observation[name] = dict(observation) if isinstance(observation, Mapping) else {}
        completeness = manifest.get("source_raw_completeness")
        source_raw_completeness[name] = (
            dict(completeness) if isinstance(completeness, Mapping) else {}
        )
        as_of = _date_token(manifest.get("as_of_date"))
        if as_of:
            as_of_dates.append(as_of)
        bundle = _date_token(manifest.get("bundle_available_date"))
        if bundle:
            bundle_dates.append(bundle)
        query = manifest.get("query")
        if isinstance(query, Mapping):
            if start := _date_token(query.get("start_date")):
                query_starts.append(start)
            if end := _date_token(query.get("end_date")):
                query_ends.append(end)
        age = _manifest_max_observation_age_days(manifest)
        if age is not None:
            max_observation_age_days_values.append(age)
        totals = manifest.get("totals")
        component_rows = 0
        component_files = 0
        if isinstance(totals, Mapping):
            component_rows = int(totals.get("rows") or 0)
            component_files = int(totals.get("files") or 0)
            totals_source_rows += int(totals.get("source_rows") or 0)
        component_metadata[name] = {
            "source_dataset": name,
            "source_dir": str(source),
            "rows": component_rows,
            "files": component_files,
            "bundle_available_date": bundle or None,
            "as_of_date": as_of or None,
            "observed_vintage_dates": vintage_dates,
        }
    return {
        "source_integrity": source_integrity,
        "source_observed_vintage_dates": source_observed_vintage_dates,
        "source_bundle_observations": source_bundle_observations,
        "source_retrieved_at": source_retrieved_at,
        "source_observation": source_observation,
        "source_raw_completeness": source_raw_completeness,
        "component_metadata": component_metadata,
        "as_of_dates": as_of_dates,
        "bundle_dates": bundle_dates,
        "query_starts": query_starts,
        "query_ends": query_ends,
        "max_observation_age_days_values": max_observation_age_days_values,
        "totals_source_rows": totals_source_rows,
    }


def _normalized_union_manifest(
    *,
    output: Path,
    components: Sequence[tuple[str, Path]],
    files: Sequence[Path],
    provenance: Mapping[str, Any],
    scan: Mapping[str, Any],
) -> dict[str, Any]:
    bundle_dates = list(provenance["bundle_dates"])
    as_of_dates = list(provenance["as_of_dates"])
    query_starts = list(provenance["query_starts"])
    query_ends = list(provenance["query_ends"])
    max_observation_age_days_values = list(provenance["max_observation_age_days_values"])
    source_observed_vintage_dates = provenance["source_observed_vintage_dates"]
    bundle_available_date = max(bundle_dates) if bundle_dates else None
    freshness_valid_through_date = min(as_of_dates) if as_of_dates else None
    max_observation_age_days = (
        min(max_observation_age_days_values) if max_observation_age_days_values else None
    )
    observed_vintage_dates = sorted(
        {value for values in source_observed_vintage_dates.values() for value in values}
    )
    query_start = min(query_starts) if query_starts else None
    query_end = max(query_ends) if query_ends else None
    component_metadata = dict(provenance["component_metadata"])
    union_rows = int(scan["rows"] or 0)
    symbols = scan["symbols"]
    columns = scan["columns"]
    return {
        "schema_version": "tushare.a_share.fundamentals.normalized.v2",
        "dataset": "normalized_fundamentals",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "immutable_snapshot": True,
        "output_dir": str(output),
        "source_normalized_dirs": [str(source) for _, source in components],
        "components": component_metadata,
        "source_integrity": provenance["source_integrity"],
        "integrity": _build_asset_integrity(output, files),
        "validity": {
            "observed_from": bundle_available_date,
            "freshness_valid_through": freshness_valid_through_date,
        },
        "revision_safety": {
            "observation_class": "observed_vintage_union",
            "revision_safe_from": bundle_available_date,
            "historical_periods_before_first_observation": "reconstructed_pit",
        },
        "source_raw_status": "completed",
        "source_raw_completeness": {
            "production_eligible": True,
            "components": provenance["source_raw_completeness"],
        },
        "as_of_date": freshness_valid_through_date,
        "bundle_available_date": bundle_available_date,
        "observed_vintage_dates": observed_vintage_dates,
        "source_observation": provenance["source_observation"],
        "source_observed_vintage_dates": source_observed_vintage_dates,
        "source_bundle_observations": provenance["source_bundle_observations"],
        "source_retrieved_at": provenance["source_retrieved_at"],
        "freshness_policy": {"max_observation_age_days": max_observation_age_days},
        "freshness_valid_through_date": freshness_valid_through_date,
        "query": {"start_date": query_start, "end_date": query_end},
        "semantics": {
            "union": True,
            "components": [name for name, _ in components],
            "source_observation_provenance": "_source_retrieved_at",
            "freshness_semantics": "latest_component_vintage_at_or_before_as_of",
            "query_range_semantics": "report_period_not_freshness",
            "ambiguous_same_day_revisions": "fail_closed",
        },
        "totals": {
            "rows": union_rows,
            "symbols": len(symbols),
            "files": len(files),
            "source_rows": int(provenance["totals_source_rows"] or 0),
        },
        "columns": sorted(columns),
        "coverage": {
            "components": component_metadata,
            "bundle_available_date": bundle_available_date,
            "freshness_valid_through_date": freshness_valid_through_date,
            "rows": union_rows,
            "symbols": len(symbols),
        },
        "runtime": {"union_scan": scan["telemetry"]},
    }


def _field_mappings(rows: Iterable[str] | Mapping[str, str]) -> dict[str, str]:
    if isinstance(rows, Mapping):
        return {str(source): str(target) for source, target in rows.items()}
    mappings = {}
    for row in rows:
        if "=" not in row:
            raise ValueError(f"Expected field mapping as source=target, got: {row}")
        source, target = (part.strip() for part in row.split("=", 1))
        if not source or not target:
            raise ValueError(f"Invalid field mapping: {row}")
        mappings[source] = target
    return mappings


@dataclass(frozen=True)
class PitBuildOptions:
    normalized_dirs: Iterable[str | Path]
    out_dir: str | Path
    field_mappings: Iterable[str] | Mapping[str, str]
    available_delay_days: int = 1
    max_observation_age_days: int = DEFAULT_FUNDAMENTALS_MAX_OBSERVATION_AGE_DAYS
    bucket_count: int = DEFAULT_PIT_BUCKET_COUNT
    batch_rows: int = DEFAULT_PIT_BATCH_ROWS
    memory_policy: MemoryPolicy | None = None


@dataclass
class PitBuildContext:
    sources: list[Path]
    files: list[Path]
    output: Path
    staging: Path
    data_dir: Path
    quarantine_dir: Path
    mappings: dict[str, str]
    value_columns: list[str]
    projected_columns: list[str]
    output_columns: list[str]
    available_delay_days: int
    bucket_count: int
    batch_rows: int
    memory_policy: MemoryPolicy
    telemetry: dict[str, Any]
    source_observation: dict[str, Any]
    source_observed_vintage_dates: dict[str, list[str]]
    source_bundle_observations: dict[str, list[dict[str, Any]]]
    source_retrieved_at: dict[str, Any]
    latest_observation_state: dict[str, Any]
    freshness_valid_through_date: str | None
    max_observation_age_days: int


@dataclass
class PitOutputStats:
    rows: int = 0
    symbols: set[str] = field(default_factory=set)
    query_start_date: str | None = None
    query_end_date: str | None = None


def _validate_pit_build_options(
    *,
    available_delay_days: int,
    max_observation_age_days: int,
    bucket_count: int,
    mappings: Mapping[str, str],
) -> None:
    if available_delay_days < 0:
        raise ValueError("available_delay_days must be non-negative.")
    if bucket_count < 1:
        raise ValueError("bucket_count must be positive.")
    if max_observation_age_days < 0:
        raise ValueError("max_observation_age_days must be non-negative.")
    if not mappings:
        raise ValueError("At least one PIT field mapping is required.")
    if len(set(mappings.values())) != len(mappings):
        raise ValueError("PIT field mapping targets must be unique.")


def _validate_pit_source_columns(files: Sequence[Path], mappings: Mapping[str, str]) -> None:
    required = {"symbol", "report_period", "disclosure_date", *PIT_SOURCE_COLUMNS}
    available_columns = _schema_columns(files)
    missing = sorted(required - available_columns)
    if missing:
        raise FieldValidationError(f"Normalized fundamentals are missing PIT columns: {missing}")
    missing_values = sorted(set(mappings) - available_columns)
    if missing_values:
        raise FieldValidationError(
            f"Normalized fundamentals are missing value fields: {missing_values}"
        )


def _pit_projected_columns(mappings: Mapping[str, str]) -> list[str]:
    return list(
        dict.fromkeys(
            [
                "symbol",
                "report_period",
                "disclosure_date",
                "_source_dataset",
                "_source_raw_asset",
                "_source_run_id",
                "_source_retrieved_at",
                "_source_bundle_retrieval_start_date",
                "_source_bundle_available_date",
                *mappings.keys(),
            ]
        )
    )


def _pit_output_columns(mappings: Mapping[str, str]) -> list[str]:
    return [
        "symbol",
        "trade_date",
        "report_period",
        "disclosure_date",
        "available_date",
        "_source_dataset",
        "_source_raw_asset",
        "_source_run_id",
        "_source_retrieved_at",
        "_source_bundle_retrieval_start_date",
        "_source_bundle_available_date",
        *mappings,
    ]


def _pit_telemetry(
    *,
    files: Sequence[Path],
    bucket_count: int,
    batch_rows: int,
    memory_policy: MemoryPolicy,
) -> dict[str, Any]:
    return {
        "mode": "bucketed_streaming",
        "bucket_count": int(bucket_count),
        "batch_rows": int(batch_rows),
        "input_files": len(files),
        "input_batches": 0,
        "input_rows": 0,
        "usable_rows": 0,
        "quarantined_rows": 0,
        "invalid_disclosure_order_rows": 0,
        "staging_files": 0,
        "output_files": 0,
        "memory_policy": memory_policy.to_dict() | memory_policy.batch_rows_to_dict(),
        "memory_samples": [],
    }


def _reset_pit_output_dirs(output: Path) -> tuple[Path, Path, Path]:
    staging = output / "_staging_pit_buckets"
    data_dir = output / "data"
    quarantine_dir = output / "quarantine"
    for path in (staging, data_dir, quarantine_dir):
        if path.exists():
            shutil.rmtree(path)
    return staging, data_dir, quarantine_dir


def _normalized_source_component(source: Path, manifest: Mapping[str, Any]) -> str:
    return str(manifest.get("source_dataset") or source)


def _source_observed_vintages(
    sources: Sequence[Path],
    manifests: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[str]]:
    vintages: dict[str, set[str]] = {}
    for source in sources:
        manifest = manifests[str(source)]
        component = _normalized_source_component(source, manifest)
        vintages.setdefault(component, set()).update(
            _date_values(manifest.get("observed_vintage_dates"))
        )
    return {component: sorted(values) for component, values in vintages.items()}


def _source_bundle_observation_ladder(
    sources: Sequence[Path],
    manifests: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    ladders: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        manifest = manifests[str(source)]
        for component, rows in _manifest_source_bundle_observations(manifest).items():
            ladders.setdefault(component, []).extend(dict(row) for row in rows)
    return {
        component: sorted(
            rows,
            key=lambda row: (row["bundle_available_date"], row["retrieval_start_date"]),
        )
        for component, rows in ladders.items()
    }


def _require_revision_safe_normalized_sources(
    sources: Sequence[Path],
    manifests: Mapping[str, Mapping[str, Any]],
) -> None:
    for source in sources:
        _require_asset_integrity(source, manifests[str(source)])
    invalid = [
        str(source)
        for source in sources
        if manifests[str(source)].get("schema_version")
        != "tushare.a_share.fundamentals.normalized.v2"
        or not _date_values(manifests[str(source)].get("observed_vintage_dates"))
        or manifests[str(source)].get("source_raw_status") != "completed"
        or manifests[str(source)].get("immutable_snapshot") is not True
        or not isinstance(manifests[str(source)].get("source_integrity"), Mapping)
        or not isinstance(manifests[str(source)].get("source_raw_completeness"), Mapping)
        or manifests[str(source)]["source_raw_completeness"].get("production_eligible") is not True
        or not _manifest_source_bundle_observations(manifests[str(source)])
        or manifests[str(source)].get("as_of_date") is None
    ]
    if invalid:
        raise PitProvenanceError(
            "PIT v2 requires normalized v2 sources with observed vintage dates: "
            + ", ".join(invalid)
        )
