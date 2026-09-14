"""Formal point-in-time views and validation for A-share fundamentals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from market_data_platform.parquet_scanning import ParquetBatchScanner
from market_data_platform.providers.tushare_a_share_fundamentals_pit_part01 import (
    DEFAULT_PIT_BATCH_ROWS,
    PitAsOfPanel,
    PitFundamentalsEvents,
    _apply_panel_event,
    _as_of_audit,
    _assert_view_schema,
    _field_record_state,
    _observation_state,
    _PanelState,
    _state_columns,
    _validate_provenance_policy,
    _with_visibility_tokens,
    load_pit_fundamentals_events,
    load_pit_fundamentals_events_from_vintages,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    PIT_SOURCE_COLUMNS,
    assert_unambiguous_pit_events,
    asset_integrity_checks,
    asset_manifest_payload,
    asset_parquet_files,
    bundle_observation_state,
    date_token,
    manifest_as_of_date,
    manifest_max_observation_age_days,
    manifest_seal_checks,
    manifest_source_bundle_observations,
    manifest_source_vintage_dates,
    schema_columns,
)
from market_data_platform.providers.tushare_common import pandas
from market_data_platform.runtime_memory import MemoryPolicy


def _panel_rows(
    state: _PanelState,
    *,
    as_of_date: str,
    bundle_available_date: str | None,
    value_columns: Sequence[str],
) -> list[dict[str, Any]]:
    rows = []
    for symbol in sorted(state.latest_available_date):
        row: dict[str, Any] = {
            "symbol": symbol,
            "as_of_date": as_of_date,
            "bundle_available_date": bundle_available_date,
            "latest_report_period": state.latest_report_period[symbol],
            "latest_available_date": state.latest_available_date[symbol],
        }
        symbol_fields = state.field_records.get(symbol, {})
        for field_name in value_columns:
            selected = symbol_fields.get(field_name)
            if selected is not None:
                row.update(_field_record_state(selected[1], field_name=field_name))
        rows.append(row)
    return rows


def _ordered_visible_events(
    events: PitFundamentalsEvents,
    *,
    max_as_of_date: str,
    policy: str,
) -> Any:
    frame = _with_visibility_tokens(events.frame)
    frame["_visibility_token"] = frame["_available_token"]
    if policy == "require_observed":
        has_provenance = (frame["_retrieval_token"] != "") & (
            frame["_bundle_available_token"] != ""
        )
        frame.loc[has_provenance, "_visibility_token"] = frame.loc[
            has_provenance,
            ["_available_token", "_retrieval_token", "_bundle_available_token"],
        ].max(axis=1)
        frame.loc[~has_provenance, "_visibility_token"] = ""
    visible = frame.loc[
        (frame["_visibility_token"] != "") & (frame["_visibility_token"] <= max_as_of_date)
    ].copy()
    assert_unambiguous_pit_events(visible, events.value_columns)
    return visible.sort_values(
        [
            "_visibility_token",
            "_retrieval_order_token",
            "symbol",
            "report_period",
            "disclosure_date",
        ],
        kind="mergesort",
    ).reset_index(drop=True)


def _materialize_panel(
    events: PitFundamentalsEvents,
    *,
    dates: Sequence[str],
    provenance_policy: str,
) -> tuple[Any, dict[str, Any], Any, dict[str, dict[str, Any]]]:
    pd = pandas()
    policy = _validate_provenance_policy(provenance_policy)
    _assert_view_schema(events, policy=policy)
    ordered = _ordered_visible_events(
        events,
        max_as_of_date=dates[-1],
        policy=policy,
    )
    records = ordered.to_dict("records")
    state = _PanelState()
    output_rows: list[dict[str, Any]] = []
    observation_by_date: dict[str, dict[str, Any]] = {}
    cursor = 0
    for as_of_date in dates:
        observation = _observation_state(events, as_of_date)
        observation_by_date[as_of_date] = observation
        while cursor < len(records) and records[cursor]["_visibility_token"] <= as_of_date:
            _apply_panel_event(state, records[cursor], events.value_columns)
            cursor += 1
        if policy != "require_observed" or (
            observation["revision_covered"] and observation["freshness_verified"]
        ):
            output_rows.extend(
                _panel_rows(
                    state,
                    as_of_date=as_of_date,
                    bundle_available_date=observation["bundle_available_date"],
                    value_columns=events.value_columns,
                )
            )
    frame = pd.DataFrame(output_rows, columns=_state_columns(events.value_columns))
    return frame, observation_by_date[dates[-1]], ordered, observation_by_date


def load_pit_fundamentals_as_of_panel(
    *,
    asset_dir: str | Path,
    as_of_dates: Sequence[str],
    provenance_policy: str = "require_observed",
    fields: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
) -> PitAsOfPanel:
    """Read projected events once and materialize an audited multi-date state panel."""

    dates = sorted(dict.fromkeys(date_token(value) for value in as_of_dates))
    if not dates or any(not value for value in dates):
        raise ValueError("as_of_dates must contain valid YYYYMMDD dates.")
    events = load_pit_fundamentals_events(
        asset_dir=asset_dir,
        fields=fields,
        symbols=symbols,
    )
    policy = _validate_provenance_policy(provenance_policy)
    frame, observation, visible, observation_by_date = _materialize_panel(
        events,
        dates=dates,
        provenance_policy=policy,
    )
    latest_state = frame.loc[frame["as_of_date"] == dates[-1]].copy()
    audit = _as_of_audit(
        events,
        metadata={
            "as_of_date": dates[-1],
            "provenance_policy": policy,
            "observation_state": observation,
        },
        visible=visible,
        state=latest_state,
    )
    audit.update(
        {
            "schema_version": "market_data_platform.pit_as_of_panel.v1",
            "as_of_date": dates[-1],
            "as_of_dates": dates,
            "as_of_start_date": dates[0],
            "as_of_end_date": dates[-1],
            "observation_by_as_of_date": observation_by_date,
        }
    )
    audit["coverage"] = dict(audit["coverage"]) | {
        "as_of_dates": len(dates),
        "panel_rows": int(len(frame)),
        "dates_with_state": int(frame["as_of_date"].nunique()) if not frame.empty else 0,
        "panel_field_state_rows": {
            field_name: int(frame[field_name].notna().sum()) for field_name in events.value_columns
        },
    }
    frame.attrs["pit_audit"] = audit
    return PitAsOfPanel(frame=frame, audit=audit)


def load_pit_fundamentals_as_of_panel_from_vintages(
    *,
    asset_dirs: Sequence[str | Path],
    as_of_dates: Sequence[str],
    provenance_policy: str = "require_observed",
    fields: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
) -> PitAsOfPanel:
    """Materialize an audited multi-date panel from immutable PIT vintages."""

    dates = sorted(dict.fromkeys(date_token(value) for value in as_of_dates))
    if not dates or any(not value for value in dates):
        raise ValueError("as_of_dates must contain valid YYYYMMDD dates.")
    events = load_pit_fundamentals_events_from_vintages(
        asset_dirs=asset_dirs,
        fields=fields,
        symbols=symbols,
    )
    policy = _validate_provenance_policy(provenance_policy)
    frame, observation, visible, observation_by_date = _materialize_panel(
        events,
        dates=dates,
        provenance_policy=policy,
    )
    latest_state = frame.loc[frame["as_of_date"] == dates[-1]].copy()
    audit = _as_of_audit(
        events,
        metadata={
            "as_of_date": dates[-1],
            "provenance_policy": policy,
            "observation_state": observation,
        },
        visible=visible,
        state=latest_state,
    )
    audit.update(
        {
            "schema_version": "market_data_platform.pit_as_of_panel.v1",
            "as_of_date": dates[-1],
            "as_of_dates": dates,
            "as_of_start_date": dates[0],
            "as_of_end_date": dates[-1],
            "observation_by_as_of_date": observation_by_date,
        }
    )
    audit["coverage"] = dict(audit["coverage"]) | {
        "as_of_dates": len(dates),
        "panel_rows": int(len(frame)),
        "dates_with_state": int(frame["as_of_date"].nunique()) if not frame.empty else 0,
        "panel_field_state_rows": {
            field_name: int(frame[field_name].notna().sum()) for field_name in events.value_columns
        },
    }
    frame.attrs["pit_audit"] = audit
    return PitAsOfPanel(frame=frame, audit=audit)


@dataclass
class _ValidationStats:
    rows: int = 0
    symbols: set[str] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)
    duplicate_count: int = 0
    event_key_duplicate_count: int = 0
    missing_provenance_count: int = 0
    seen_keys: set[tuple[str, ...]] = field(default_factory=set)
    seen_event_keys: set[tuple[str, ...]] = field(default_factory=set)
    availability_ok: bool = True
    disclosure_ok: bool = True


def _duplicate_key_count(
    frame: Any,
    *,
    columns: Sequence[str],
    seen: set[tuple[str, ...]],
) -> int:
    duplicates = 0
    for row in frame[list(columns)].itertuples(index=False, name=None):
        key = tuple(str(value) for value in row)
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def _update_validation_stats(stats: _ValidationStats, frame: Any) -> None:
    pd = pandas()
    stats.rows += int(len(frame))
    stats.symbols.update(frame["symbol"].dropna().astype(str).unique().tolist())
    stats.dates.update(frame["available_date"].dropna().astype(str).unique().tolist())
    disclosure = pd.to_datetime(frame["disclosure_date"], format="%Y%m%d", errors="coerce")
    available = pd.to_datetime(frame["available_date"], format="%Y%m%d", errors="coerce")
    report_period = pd.to_datetime(frame["report_period"], format="%Y%m%d", errors="coerce")
    stats.availability_ok = stats.availability_ok and bool((available >= disclosure).all())
    stats.disclosure_ok = stats.disclosure_ok and bool((disclosure >= report_period).all())
    provenance = tuple(column for column in PIT_SOURCE_COLUMNS if column in frame)
    if provenance:
        missing = (
            frame.loc[:, provenance]
            .fillna("")
            .astype(str)
            .apply(lambda row: row.str.strip().eq("").any(), axis=1)
        )
        stats.missing_provenance_count += int(missing.sum())
    stats.duplicate_count += _duplicate_key_count(
        frame,
        columns=(
            "symbol",
            "report_period",
            "available_date",
            "_source_dataset",
            *provenance,
        ),
        seen=stats.seen_keys,
    )
    stats.event_key_duplicate_count += _duplicate_key_count(
        frame,
        columns=("symbol", "trade_date", "report_period", *provenance),
        seen=stats.seen_event_keys,
    )


def _scan_validation_frames(
    *,
    files: Sequence[Path],
    required: set[str],
    batch_rows: int,
    memory_policy: MemoryPolicy,
) -> tuple[_ValidationStats, list[str], ParquetBatchScanner]:
    stats = _ValidationStats()
    missing: list[str] = []
    scanner = ParquetBatchScanner(
        columns=sorted(required),
        batch_rows=batch_rows,
        memory_policy=memory_policy,
        stage="pit_fundamentals_validate",
    )
    for _, frame in scanner.iter_frames(files):
        if frame.empty:
            continue
        batch_missing = required - set(frame.columns)
        if batch_missing:
            missing = sorted(batch_missing)
            break
        _update_validation_stats(stats, frame)
    return stats, missing, scanner


def _validation_checks(
    *,
    stats: _ValidationStats,
    missing: Sequence[str],
    value_columns: Sequence[str],
    metadata: Mapping[str, Any],
) -> list[dict[str, Any]]:
    schema_version = str(metadata.get("schema_version") or "")
    expected_target = metadata.get("target_date")
    observation = metadata.get("observation_state")
    observation = observation if isinstance(observation, Mapping) else {}
    checks: list[dict[str, Any]] = [
        {"id": "required_pit_columns", "passed": not missing, "missing": list(missing)},
        {"id": "non_empty", "passed": stats.rows > 0},
    ]
    if not missing:
        checks.extend(
            [
                {"id": "availability_delay_semantics", "passed": stats.availability_ok},
                {"id": "disclosure_after_report_period", "passed": stats.disclosure_ok},
                {
                    "id": "duplicate_keys",
                    "passed": stats.duplicate_count == 0,
                    "duplicates": stats.duplicate_count,
                },
                {
                    "id": "unique_symbol_trade_date_report_period_retrieval",
                    "passed": stats.event_key_duplicate_count == 0,
                    "duplicates": stats.event_key_duplicate_count,
                },
                {
                    "id": "source_retrieval_provenance",
                    "passed": not schema_version.endswith(".v2")
                    or stats.missing_provenance_count == 0,
                    "missing": stats.missing_provenance_count,
                    "legacy_compatible": not schema_version.endswith(".v2"),
                },
                {"id": "symbol_coverage", "passed": bool(stats.symbols)},
                {"id": "date_coverage", "passed": bool(stats.dates)},
                {
                    "id": "value_field_mapping",
                    "passed": bool(value_columns),
                    "fields": list(value_columns),
                },
            ]
        )
    if expected_target:
        checks.extend(
            [
                {
                    "id": "revision_safe_schema",
                    "passed": schema_version == "tushare.a_share.fundamentals.pit.v2",
                    "actual_schema_version": schema_version or None,
                },
                {
                    "id": "observation_vintage_supports_target_date",
                    "passed": bool(
                        observation.get("revision_covered")
                        and observation.get("freshness_verified")
                    ),
                    "actual_as_of_date": metadata.get("as_of_date"),
                    "target_date": expected_target,
                    "observation_state": dict(observation),
                },
            ]
        )
    return checks


def validate_pit_fundamentals(
    *,
    asset_dir: str | Path,
    target_date: str | None = None,
    batch_rows: int = DEFAULT_PIT_BATCH_ROWS,
    memory_policy: MemoryPolicy | None = None,
) -> dict[str, Any]:
    root = Path(asset_dir).expanduser().resolve()
    manifest = asset_manifest_payload(root)
    schema_version = str(manifest.get("schema_version") or "")
    as_of_date = manifest_as_of_date(manifest)
    expected_target = date_token(target_date) or None
    observation = (
        bundle_observation_state(
            manifest_source_vintage_dates(manifest),
            as_of_date=expected_target,
            max_age_days=manifest_max_observation_age_days(manifest),
            source_bundle_observations=manifest_source_bundle_observations(manifest),
        )
        if expected_target
        else None
    )
    files = asset_parquet_files(root)
    required = {
        "symbol",
        "trade_date",
        "report_period",
        "disclosure_date",
        "available_date",
        "_source_dataset",
    }
    if schema_version.endswith(".v2"):
        required.update(PIT_SOURCE_COLUMNS)
    available_columns = schema_columns(files) if files else set()
    missing = sorted(required - available_columns)
    value_columns = sorted(available_columns - required - set(PIT_SOURCE_COLUMNS))
    scanner = ParquetBatchScanner(columns=[])
    stats = _ValidationStats()
    if not missing:
        stats, batch_missing, scanner = _scan_validation_frames(
            files=files,
            required=required,
            batch_rows=batch_rows,
            memory_policy=memory_policy or MemoryPolicy(),
        )
        missing = batch_missing or missing
    checks = _validation_checks(
        stats=stats,
        missing=missing,
        value_columns=value_columns,
        metadata={
            "schema_version": schema_version,
            "as_of_date": as_of_date,
            "target_date": expected_target,
            "observation_state": observation,
        },
    )
    integrity = asset_integrity_checks(root, manifest.get("integrity"))
    checks.append(
        {
            "id": "content_integrity",
            "passed": all(integrity.values()),
            "details": integrity,
        }
    )
    seal = manifest_seal_checks(root)
    checks.append(
        {
            "id": "manifest_seal",
            "passed": all(seal.values()),
            "details": seal,
        }
    )
    return {
        "status": "passed" if all(row["passed"] for row in checks) else "failed",
        "checks": checks,
        "totals": {
            "rows": stats.rows,
            "symbols": len(stats.symbols),
            "files": len(files),
        },
        "as_of_date": as_of_date,
        "observation_state": observation,
        "target_date": expected_target,
        "scan": scanner.telemetry.to_dict(),
    }
