"""Stateful TuShare A-share fundamentals ingestion and PIT publication."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_fundamentals_part01 import (
    DEFAULT_FUNDAMENTALS_MAX_OBSERVATION_AGE_DAYS,
    QueryUnit,
    RawFundamentalsDownloadContext,
    RawFundamentalsDownloadOptions,
    _fetch_query_unit,
    _load_json,
    _now,
    _safe_part_name,
    _schema_hash,
    _state_default,
    _write_json,
    plan_query_units,
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
    DuplicatePageError,
    FieldValidationError,
    FundamentalsDownloadError,
    PitProvenanceError,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    assert_unambiguous_pit_events as _assert_unambiguous_pit_events,
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
    date_token as _date_token,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    observation_contract as _observation_contract,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    raw_bundle_completeness as _raw_bundle_completeness,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    require_asset_integrity as _require_asset_integrity,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    seal_manifest as _seal_manifest,
)
from market_data_platform.providers.tushare_common import (
    configure_tushare_client_api_url,
    get_tushare_client,
    pandas,
    resolve_tushare_api_url,
    write_manifest,
)


def _failure_default(dataset: str, run_id: str) -> dict[str, Any]:
    return {
        "schema_version": "tushare.a_share.fundamentals.failures.v1",
        "dataset": dataset,
        "source_run_id": run_id,
        "updated_at": _now(),
        "failed_units": [],
        "skipped_datasets": [],
        "entitlement_failures": [],
        "stale_refresh_windows": [],
    }


def _is_stale(row: Mapping[str, Any], *, stale_after_days: int | None) -> bool:
    if stale_after_days is None or stale_after_days < 0:
        return False
    retrieved_at = str(row.get("retrieved_at") or "")
    if not retrieved_at:
        return True
    retrieved = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    return datetime.now(UTC) - retrieved > timedelta(days=stale_after_days)


def _watermark(plan: list[str], units: Mapping[str, Any]) -> str | None:
    watermark = None
    for unit_id in plan:
        row = units.get(unit_id)
        if not isinstance(row, Mapping) or row.get("status") != "completed":
            break
        watermark = unit_id
    return watermark


def _failure_kind(error: Exception) -> str:
    message = str(error).lower()
    if any(
        word in message
        for word in (
            "rate limit",
            "too many requests",
            "frequency",
            "频率",
            "频次",
            "限频",
            "超限",
            "每分钟",
        )
    ):
        return "rate_limit"
    if any(word in message for word in ("permission", "privilege", "vip", "积分", "权限")):
        return "entitlement_failure"
    if isinstance(error, DuplicatePageError):
        return "duplicate_page"
    if isinstance(error, FieldValidationError):
        return "field_validation"
    return "provider_error"


def _download_context(options: RawFundamentalsDownloadOptions) -> RawFundamentalsDownloadContext:
    if options.retry_attempts < 1:
        raise ValueError("retry_attempts must be positive.")
    if options.request_interval_seconds < 0:
        raise ValueError("request_interval_seconds must be non-negative.")
    output = Path(options.out_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_run_id = options.run_id or (
        f"{options.dataset}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    units = plan_query_units(
        dataset=options.dataset,
        start_date=options.start_date,
        end_date=options.end_date,
        entitlement_mode=options.entitlement_mode,
        symbols=options.symbols,
    )
    state_path = output / "state.json"
    failure_path = output / "failures.json"
    api_url = resolve_tushare_api_url(options.api_url, token_env=options.token_env)
    client = options.client or get_tushare_client(token_env=options.token_env, api_url=api_url)
    configure_tushare_client_api_url(client, api_url)
    return RawFundamentalsDownloadContext(
        options=options,
        output=output,
        source_run_id=source_run_id,
        units=units,
        state_path=state_path,
        failure_path=failure_path,
        state=_load_json(state_path, _state_default(options.dataset, source_run_id, units)),
        failures=_load_json(failure_path, _failure_default(options.dataset, source_run_id)),
        client=client,
        parts=[],
    )


def _reuse_completed_unit(context: RawFundamentalsDownloadContext, unit: QueryUnit) -> bool:
    existing = context.state["units"].get(unit.unit_id, {})
    if existing.get("status") != "completed":
        return False
    if not _is_stale(existing, stale_after_days=context.options.stale_after_days):
        context.parts.append(existing)
        return True
    context.failures["stale_refresh_windows"].append(
        {"unit_id": unit.unit_id, "retrieved_at": existing.get("retrieved_at")}
    )
    return False


def _completed_unit_row(
    context: RawFundamentalsDownloadContext,
    unit: QueryUnit,
    frame: Any,
    *,
    attempts: int,
) -> dict[str, Any]:
    part_path = (
        context.output / "data" / context.options.dataset / f"{_safe_part_name(unit)}.parquet"
    )
    part_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(part_path, index=False)
    retrieved_at = _now()
    integrity = _build_asset_integrity(context.output, [part_path])["files"][0]
    return {
        "unit_id": unit.unit_id,
        "status": "completed",
        "dataset": context.options.dataset,
        "endpoint": unit.endpoint,
        "query_parameters": unit.params,
        "request_started_at": context.request_started_at,
        "request_completed_at": retrieved_at,
        "retrieved_at": retrieved_at,
        "schema_hash": _schema_hash(frame),
        "content_sha256": integrity["sha256"],
        "bytes": integrity["bytes"],
        "rows": int(len(frame)),
        "entitlement_mode": context.options.entitlement_mode,
        "source_run_id": context.source_run_id,
        "path": str(part_path),
        "attempts": attempts,
    }


def _clear_failure_for_unit(context: RawFundamentalsDownloadContext, unit_id: str) -> None:
    for key in ("failed_units", "entitlement_failures"):
        rows = context.failures.get(key, [])
        if isinstance(rows, list):
            context.failures[key] = [row for row in rows if row.get("unit_id") != unit_id]


def _failed_unit_row(
    context: RawFundamentalsDownloadContext,
    unit: QueryUnit,
    error: Exception,
) -> dict[str, Any]:
    return {
        "unit_id": unit.unit_id,
        "status": "failed",
        "dataset": context.options.dataset,
        "endpoint": unit.endpoint,
        "query_parameters": unit.params,
        "error_kind": _failure_kind(error),
        "error": str(error),
        "attempts": context.options.retry_attempts,
    }


def _query_unit_row(
    context: RawFundamentalsDownloadContext,
    unit: QueryUnit,
    *,
    attempt: int,
) -> dict[str, Any]:
    context.request_started_at = _now()
    frame = _fetch_query_unit(
        context,
        unit,
        page_size=context.options.page_size,
        max_pages=context.options.max_pages,
    )
    return _completed_unit_row(context, unit, frame, attempts=attempt)


def _download_query_unit_with_retries(
    context: RawFundamentalsDownloadContext,
    unit: QueryUnit,
) -> dict[str, Any]:
    options = context.options
    last_error: Exception | None = None
    for attempt in range(1, options.retry_attempts + 1):
        try:
            return _query_unit_row(context, unit, attempt=attempt)
        except Exception as exc:  # Provider failures are persisted for restart.
            last_error = exc
            if attempt < options.retry_attempts and options.retry_backoff_seconds > 0:
                time.sleep(options.retry_backoff_seconds * attempt)
    assert last_error is not None
    return _failed_unit_row(context, unit, last_error)


def _download_query_unit(context: RawFundamentalsDownloadContext, unit: QueryUnit) -> None:
    row = _download_query_unit_with_retries(context, unit)
    context.state["units"][unit.unit_id] = row
    _clear_failure_for_unit(context, unit.unit_id)
    if row["status"] == "failed":
        context.failures["failed_units"].append(row)
    else:
        context.parts.append(row)
        return
    if row["error_kind"] == "entitlement_failure":
        context.failures["entitlement_failures"].append(row)


def _persist_download_progress(context: RawFundamentalsDownloadContext) -> None:
    context.state["updated_at"] = _now()
    context.state["contiguous_watermark"] = _watermark(
        context.state["plan"],
        context.state["units"],
    )
    context.failures["updated_at"] = _now()
    _write_json(context.state_path, context.state)
    _write_json(context.failure_path, context.failures)


def _raw_fundamentals_manifest(context: RawFundamentalsDownloadContext) -> dict[str, Any]:
    options = context.options
    status = "completed" if not context.failures["failed_units"] else "partial"
    integrity_files = [Path(part["path"]) for part in context.parts]
    integrity_files.extend((context.state_path, context.failure_path))
    integrity = _build_asset_integrity(context.output, integrity_files)
    observed_vintage_dates = sorted(
        {token for part in context.parts if (token := _date_token(part.get("retrieved_at")))}
    )
    return {
        "schema_version": "tushare.a_share.fundamentals.raw.v2",
        "dataset": options.dataset,
        "endpoint": sorted({part["endpoint"] for part in context.parts}),
        "market": "a_share",
        "provider": "tushare",
        "status": status,
        "immutable_snapshot": status == "completed",
        "output_dir": str(context.output),
        "query": {
            "start_date": _date_token(options.start_date),
            "end_date": _date_token(options.end_date),
            "query_parameters": [unit.params for unit in context.units],
            "fetch_granularity": sorted({unit.granularity for unit in context.units}),
        },
        "retrieved_at": _now(),
        "observed_vintage_dates": observed_vintage_dates[-1:],
        "schema_hashes": sorted({part["schema_hash"] for part in context.parts}),
        "entitlement_mode": options.entitlement_mode,
        "api_url": resolve_tushare_api_url(options.api_url, token_env=options.token_env),
        "source_run_id": context.source_run_id,
        "totals": {
            "rows": sum(int(part["rows"]) for part in context.parts),
            "files": len(context.parts),
            "query_units": len(context.units),
            "failed_units": len(context.failures["failed_units"]),
        },
        "state_file": str(context.state_path),
        "failure_report": str(context.failure_path),
        "parts": context.parts,
        "integrity": integrity,
        "revision_safety": {
            "observation_class": "observed_vintage",
            "revision_safe_from": observed_vintage_dates[-1] if observed_vintage_dates else None,
            "historical_periods_before_first_observation": "reconstructed_pit",
        },
    }


def _completed_immutable_snapshot(
    options: RawFundamentalsDownloadOptions,
) -> dict[str, Any] | None:
    output = Path(options.out_dir).expanduser().resolve()
    manifest = _asset_manifest_payload(output)
    if manifest.get("status") != "completed" or manifest.get("immutable_snapshot") is not True:
        return None
    raw_query = manifest.get("query")
    query = raw_query if isinstance(raw_query, Mapping) else {}
    identity_matches = (
        manifest.get("dataset") == options.dataset
        and query.get("start_date") == _date_token(options.start_date)
        and query.get("end_date") == _date_token(options.end_date)
        and manifest.get("entitlement_mode") == options.entitlement_mode
        and (options.run_id is None or manifest.get("source_run_id") == options.run_id)
    )
    if not identity_matches:
        raise FundamentalsDownloadError(
            f"Completed immutable fundamentals snapshot has a different identity: {output}"
        )
    _require_asset_integrity(output, manifest)
    if options.stale_after_days is not None:
        raise FundamentalsDownloadError(
            "Completed fundamentals snapshots cannot be refreshed in place; use a new out_dir."
        )
    return manifest


def download_raw_fundamentals(options: RawFundamentalsDownloadOptions) -> dict[str, Any]:
    if completed := _completed_immutable_snapshot(options):
        return completed
    context = _download_context(options)
    for unit in context.units:
        if not _reuse_completed_unit(context, unit):
            _download_query_unit(context, unit)
        _persist_download_progress(context)
    manifest = _raw_fundamentals_manifest(context)
    manifest_path = context.output / "manifest.yml"
    write_manifest(manifest_path, manifest)
    _seal_manifest(manifest_path)
    return manifest


def read_download_state(path: str | Path) -> dict[str, Any]:
    return _load_json(Path(path).expanduser().resolve(), {})


def read_failure_report(path: str | Path) -> dict[str, Any]:
    return _load_json(Path(path).expanduser().resolve(), {})


def _raw_frames(asset_dir: str | Path) -> list[Any]:
    pd = pandas()
    files = _asset_parquet_files(asset_dir)
    return [pd.read_parquet(path) for path in files]


def _raw_frames_with_retrieval(
    asset_dir: str | Path,
    manifest: Mapping[str, Any],
) -> list[Any]:
    pd = pandas()
    root = Path(asset_dir).expanduser().resolve()
    part_retrieval: dict[Path, str] = {}
    parts = manifest.get("parts")
    if isinstance(parts, Sequence) and not isinstance(parts, (str, bytes)):
        for row in parts:
            if not isinstance(row, Mapping):
                continue
            raw_path = str(row.get("path") or "").strip()
            retrieved_at = str(row.get("retrieved_at") or "").strip()
            if raw_path and retrieved_at:
                path = Path(raw_path).expanduser()
                resolved_path = (
                    (root / path).resolve() if not path.is_absolute() else path.resolve()
                )
                part_retrieval[resolved_path] = retrieved_at
    frames = []
    for path in _asset_parquet_files(root):
        retrieved_at = part_retrieval.get(path.resolve())
        if not retrieved_at:
            raise PitProvenanceError(
                f"Raw parquet lacks exact completed-part retrieval provenance: {path}"
            )
        frame = pd.read_parquet(path)
        frame["_source_retrieved_at"] = retrieved_at
        frames.append(frame)
    return frames


def _write_parquet_part(frame: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _last_present(values: Any) -> Any:
    pd = pandas()
    present = values[pd.notna(values)]
    return present.iloc[-1] if not present.empty else None


def _join_unique(values: Any) -> str:
    pd = pandas()
    unique = sorted({str(value) for value in values if pd.notna(value) and str(value)})
    return ";".join(unique)


def _empty_pit_frame(value_columns: Sequence[str]) -> Any:
    pd = pandas()
    return pd.DataFrame(
        columns=[
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
            *value_columns,
        ]
    )


def _coalesce_pit_frame(frame: Any, value_columns: Sequence[str]) -> Any:
    pd = pandas()
    if frame.empty:
        return _empty_pit_frame(value_columns)
    _assert_unambiguous_pit_events(frame, value_columns)
    group_columns = [
        "symbol",
        "trade_date",
        "report_period",
        "_source_retrieved_at",
        "_source_bundle_retrieval_start_date",
        "_source_bundle_available_date",
    ]
    sort_columns = ["symbol", "trade_date", "report_period", "disclosure_date", "_source_dataset"]
    frame = frame.sort_values(sort_columns, kind="mergesort")
    if not frame.duplicated(subset=group_columns, keep=False).any():
        output_columns = list(_empty_pit_frame(value_columns).columns)
        return (
            frame.loc[:, output_columns]
            .sort_values(group_columns, kind="mergesort")
            .reset_index(drop=True)
        )

    def _coalesce_trade_date(group: Any) -> Any:
        row = {
            "symbol": group["symbol"].iloc[0],
            "trade_date": group["trade_date"].iloc[0],
            "report_period": _last_present(group["report_period"]),
            "disclosure_date": _last_present(group["disclosure_date"]),
            "available_date": _last_present(group["available_date"]),
            "_source_dataset": _join_unique(group["_source_dataset"]),
            "_source_raw_asset": _join_unique(group["_source_raw_asset"]),
            "_source_run_id": _join_unique(group["_source_run_id"]),
            "_source_retrieved_at": _join_unique(group["_source_retrieved_at"]),
            "_source_bundle_retrieval_start_date": _join_unique(
                group["_source_bundle_retrieval_start_date"]
            ),
            "_source_bundle_available_date": _join_unique(group["_source_bundle_available_date"]),
        }
        for column in value_columns:
            row[column] = _last_present(group[column])
        return pd.Series(row)

    coalesced_rows = [
        _coalesce_trade_date(group) for _, group in frame.groupby(group_columns, sort=False)
    ]
    if not coalesced_rows:
        return _empty_pit_frame(value_columns)
    return (
        pd.DataFrame(coalesced_rows)
        .reset_index(drop=True)
        .sort_values(group_columns, kind="mergesort")
    )


def compact_raw_fundamentals(*, raw_dir: str | Path, out_dir: str | Path) -> dict[str, Any]:
    pd = pandas()
    source = Path(raw_dir).expanduser().resolve()
    output = Path(out_dir).expanduser().resolve()
    frames = _raw_frames(source)
    frame = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    part = output / "data" / "part.parquet"
    part.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(part, index=False)
    manifest = {
        "schema_version": "tushare.a_share.fundamentals.raw_compact.v1",
        "dataset": "fundamentals_raw_compact",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(output),
        "source_raw_dir": str(source),
        "schema_hash": _schema_hash(frame),
        "totals": {"rows": int(len(frame)), "files": 1, "source_files": len(frames)},
    }
    write_manifest(output / "manifest.yml", manifest)
    return manifest


def _first_present(frame: Any, columns: Iterable[str]) -> str | None:
    return next((column for column in columns if column in frame.columns), None)


def _assert_unambiguous_normalized_rows(frame: Any, keys: Sequence[str]) -> None:
    if frame.empty or not keys:
        return
    duplicates = frame[frame.duplicated(subset=list(keys), keep=False)]
    compare_columns = [
        column
        for column in frame.columns
        if column not in keys and column != "ann_date" and not column.startswith("_")
    ]
    for key, group in duplicates.groupby(list(keys), dropna=False, sort=False):
        canonical = group.loc[:, compare_columns].astype("string").fillna("<NA>")
        if len(canonical.drop_duplicates()) > 1:
            identity = key if isinstance(key, tuple) else (key,)
            raise FieldValidationError(
                "Normalized fundamentals contain an ambiguous same-day revision for "
                f"{tuple(str(value) for value in identity)}."
            )


def _select_latest_provider_update(frame: Any, keys: Sequence[str]) -> tuple[Any, int, int]:
    pd = pandas()
    if frame.empty or "update_flag" not in frame or not keys:
        return frame, 0, 0
    update_rank = pd.to_numeric(frame["update_flag"], errors="coerce").fillna(-1)
    ranked = frame.assign(_provider_update_rank=update_rank)
    latest_rank = ranked.groupby(list(keys), dropna=False)["_provider_update_rank"].transform("max")
    superseded_update = ranked["_provider_update_rank"] < latest_rank
    ranked = ranked.loc[~superseded_update].copy()
    if "ann_date" not in ranked:
        return ranked.drop(columns="_provider_update_rank"), int(superseded_update.sum()), 0
    ann_rank = pd.to_numeric(ranked["ann_date"], errors="coerce").fillna(-1)
    ranked["_provider_ann_rank"] = ann_rank
    latest_ann = ranked.groupby(list(keys), dropna=False)["_provider_ann_rank"].transform("max")
    superseded_ann = ranked["_provider_ann_rank"] < latest_ann
    selected = ranked.loc[~superseded_ann].drop(
        columns=["_provider_update_rank", "_provider_ann_rank"]
    )
    return selected, int(superseded_update.sum()), int(superseded_ann.sum())


def _revision_safe_raw_inputs(
    source: Path,
    raw_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
    completeness = _raw_bundle_completeness(source, raw_payload)
    if (
        raw_payload.get("schema_version") != "tushare.a_share.fundamentals.raw.v2"
        or raw_payload.get("immutable_snapshot") is not True
        or completeness["production_eligible"] is not True
    ):
        failed = [name for name, passed in completeness["checks"].items() if not passed]
        raise PitProvenanceError(
            "Normalized v2 requires a complete observed raw bundle; failed checks: "
            + ", ".join(failed)
        )
    observation = _observation_contract(
        raw_payload,
        max_age_days=DEFAULT_FUNDAMENTALS_MAX_OBSERVATION_AGE_DAYS,
    )
    return completeness, observation, _raw_frames_with_retrieval(source, raw_payload)
