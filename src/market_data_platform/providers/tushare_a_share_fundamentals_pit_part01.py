"""Formal point-in-time views and validation for A-share fundamentals."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from market_data_platform.parquet_scanning import ParquetBatchScanner
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    PIT_SOURCE_COLUMNS,
    FieldValidationError,
    PitProvenanceError,
    assert_unambiguous_pit_events,
    asset_manifest_payload,
    asset_parquet_files,
    bundle_observation_state,
    date_token,
    date_values,
    manifest_max_observation_age_days,
    manifest_source_bundle_observations,
    manifest_source_vintage_dates,
    require_asset_integrity,
    schema_columns,
)
from market_data_platform.providers.tushare_common import pandas

DEFAULT_PIT_BATCH_ROWS = 65536


def _validate_provenance_policy(value: str) -> str:
    policy = str(value or "").strip().lower()
    if policy not in {"require_observed", "allow_unverified"}:
        raise ValueError("provenance_policy must be 'require_observed' or 'allow_unverified'.")
    return policy


def _state_columns(value_columns: Sequence[str]) -> list[str]:
    columns = [
        "symbol",
        "as_of_date",
        "bundle_available_date",
        "latest_report_period",
        "latest_available_date",
    ]
    provenance = (
        "report_period",
        "available_date",
        "disclosure_date",
        "source_dataset",
        "source_raw_asset",
        "source_run_id",
        "source_retrieved_at",
        "source_bundle_retrieval_start_date",
        "source_bundle_available_date",
        "revision_id",
    )
    for field_name in value_columns:
        columns.append(field_name)
        columns.extend(f"{field_name}__{suffix}" for suffix in provenance)
    return columns


def _field_record_state(latest: Mapping[str, Any], *, field_name: str) -> dict[str, Any]:
    revision_payload = "|".join(
        str(latest.get(column, ""))
        for column in (
            "symbol",
            "report_period",
            "disclosure_date",
            "available_date",
            "_source_dataset",
            "_source_raw_asset",
            "_source_run_id",
            "_source_retrieved_at",
            "_source_bundle_retrieval_start_date",
            "_source_bundle_available_date",
            field_name,
        )
    )
    return {
        field_name: latest[field_name],
        f"{field_name}__report_period": latest["report_period"],
        f"{field_name}__available_date": latest["available_date"],
        f"{field_name}__disclosure_date": latest["disclosure_date"],
        f"{field_name}__source_dataset": latest["_source_dataset"],
        f"{field_name}__source_raw_asset": latest.get("_source_raw_asset", ""),
        f"{field_name}__source_run_id": latest["_source_run_id"],
        f"{field_name}__source_retrieved_at": latest.get("_source_retrieved_at", ""),
        f"{field_name}__source_bundle_retrieval_start_date": latest.get(
            "_source_bundle_retrieval_start_date", ""
        ),
        f"{field_name}__source_bundle_available_date": latest.get(
            "_source_bundle_available_date", ""
        ),
        f"{field_name}__revision_id": hashlib.sha256(revision_payload.encode("utf-8")).hexdigest()[
            :16
        ],
    }


def _field_state(source: Any, *, field_name: str) -> dict[str, Any]:
    visible = source.loc[source[field_name].notna()].sort_values(
        [
            "report_period",
            "available_date",
            "disclosure_date",
            "_retrieval_order_token",
        ],
        kind="mergesort",
    )
    return {} if visible.empty else _field_record_state(visible.iloc[-1], field_name=field_name)


@dataclass(frozen=True)
class PitAsOfView:
    frame: Any
    audit: dict[str, Any]


@dataclass(frozen=True)
class PitAsOfPanel:
    frame: Any
    audit: dict[str, Any]


@dataclass(frozen=True)
class PitFundamentalsEvents:
    frame: Any
    manifest: dict[str, Any]
    asset_dir: Path
    files: tuple[Path, ...]
    value_columns: tuple[str, ...]
    asset_dirs: tuple[Path, ...] = ()
    source_manifests: tuple[dict[str, Any], ...] = ()

    def as_of(
        self,
        as_of_date: str,
        *,
        provenance_policy: str = "require_observed",
    ) -> PitAsOfView:
        return _materialize_as_of_view(
            self,
            as_of_date=as_of_date,
            provenance_policy=provenance_policy,
        )


def _observation_state(events: PitFundamentalsEvents, cutoff: str) -> dict[str, Any]:
    return bundle_observation_state(
        manifest_source_vintage_dates(events.manifest),
        as_of_date=cutoff,
        max_age_days=manifest_max_observation_age_days(events.manifest),
        source_bundle_observations=manifest_source_bundle_observations(events.manifest),
    )


def _assert_view_schema(events: PitFundamentalsEvents, *, policy: str) -> None:
    schema_version = str(events.manifest.get("schema_version") or "")
    if policy == "require_observed" and schema_version != "tushare.a_share.fundamentals.pit.v2":
        raise PitProvenanceError(
            "require_observed needs tushare.a_share.fundamentals.pit.v2; "
            "legacy assets are fail-closed."
        )
    if policy == "require_observed":
        if events.asset_dirs and events.source_manifests:
            for asset_dir, manifest in zip(events.asset_dirs, events.source_manifests, strict=True):
                require_asset_integrity(asset_dir, manifest)
        else:
            require_asset_integrity(events.asset_dir, events.manifest)
    required_bundle_columns = {
        "_source_bundle_retrieval_start_date",
        "_source_bundle_available_date",
    }
    missing = sorted(required_bundle_columns - set(events.frame.columns))
    if policy == "require_observed" and missing:
        raise PitProvenanceError(
            "require_observed needs complete-bundle provenance columns: " + ", ".join(missing)
        )


def _latest_retrieval_timestamp(value: object) -> str:
    return max(
        (part.strip() for part in str(value or "").split(";") if part.strip()),
        default="",
    )


def _with_visibility_tokens(frame: Any) -> Any:
    result = frame.copy()
    result["_available_token"] = result["available_date"].map(date_token)
    retrieval_values = (
        result["_source_retrieved_at"]
        if "_source_retrieved_at" in result
        else pandas().Series("", index=result.index)
    )
    result["_retrieval_order_token"] = retrieval_values.map(_latest_retrieval_timestamp)
    result["_retrieval_token"] = result["_retrieval_order_token"].map(date_token)
    bundle_values = (
        result["_source_bundle_available_date"]
        if "_source_bundle_available_date" in result
        else pandas().Series("", index=result.index)
    )
    result["_bundle_available_token"] = bundle_values.map(_latest_retrieval_timestamp).map(
        date_token
    )
    return result


def _visible_events(
    events: PitFundamentalsEvents,
    *,
    cutoff: str,
    policy: str,
    observation_state: Mapping[str, Any],
) -> Any:
    frame = _with_visibility_tokens(events.frame)
    visible = (frame["_available_token"] != "") & (frame["_available_token"] <= cutoff)
    if policy == "require_observed":
        visible &= (frame["_retrieval_token"] != "") & (frame["_retrieval_token"] <= cutoff)
        visible &= (frame["_bundle_available_token"] != "") & (
            frame["_bundle_available_token"] <= cutoff
        )
        if not (
            observation_state.get("revision_covered")
            and observation_state.get("freshness_verified")
        ):
            visible &= False
    return frame.loc[visible].copy()


def _retrieval_date_coverage(frame: Any) -> tuple[str | None, str | None]:
    if frame.empty or "_source_retrieved_at" not in frame:
        return None, None
    dates = sorted(
        token
        for value in frame["_source_retrieved_at"]
        for part in str(value or "").split(";")
        if (token := date_token(part))
    )
    return (dates[0], dates[-1]) if dates else (None, None)


def _as_of_audit(
    events: PitFundamentalsEvents,
    *,
    metadata: Mapping[str, Any],
    visible: Any,
    state: Any,
) -> dict[str, Any]:
    cutoff = str(metadata["as_of_date"])
    policy = str(metadata["provenance_policy"])
    observation_state = metadata["observation_state"]
    observation_state = observation_state if isinstance(observation_state, Mapping) else {}
    retrieved_start, retrieved_end = _retrieval_date_coverage(visible)
    available = visible["available_date"].map(date_token) if not visible.empty else None
    revision_safe = bool(policy == "require_observed" and observation_state.get("revision_covered"))
    freshness_verified = bool(revision_safe and observation_state.get("freshness_verified"))
    return {
        "schema_version": "market_data_platform.pit_as_of_view.v1",
        "as_of_date": cutoff,
        **dict(observation_state),
        "provenance_policy": policy,
        "revision_safety": "revision_safe" if revision_safe else "legacy_unverified",
        "revision_safe": revision_safe,
        "freshness_verified": freshness_verified,
        "production_eligible": revision_safe and freshness_verified,
        "asset_schema_version": events.manifest.get("schema_version"),
        "source_observed_vintage_dates": events.manifest.get("source_observed_vintage_dates", {}),
        "source_observation": events.manifest.get("source_observation", {}),
        "source_retrieved_at": events.manifest.get("source_retrieved_at", {}),
        "coverage": {
            "files": len(events.files),
            "loaded_events": int(len(events.frame)),
            "visible_events": int(len(visible)),
            "loaded_symbols": int(events.frame["symbol"].nunique())
            if not events.frame.empty
            else 0,
            "state_rows": int(len(state)),
            "selected_fields": list(events.value_columns),
            "field_state_rows": {
                field_name: int(state[field_name].notna().sum())
                for field_name in events.value_columns
            },
            "visible_available_start_date": (
                str(available[available != ""].min())
                if available is not None and bool((available != "").any())
                else None
            ),
            "visible_available_end_date": (
                str(available[available != ""].max())
                if available is not None and bool((available != "").any())
                else None
            ),
            "visible_source_retrieved_start_date": retrieved_start,
            "visible_source_retrieved_end_date": retrieved_end,
        },
    }


def _state_frame(
    events: PitFundamentalsEvents,
    *,
    cutoff: str,
    bundle_available_date: str | None,
    visible: Any,
) -> Any:
    pd = pandas()
    if visible.empty:
        return pd.DataFrame(columns=_state_columns(events.value_columns))
    assert_unambiguous_pit_events(visible, events.value_columns)
    rows = []
    for symbol, symbol_events in visible.groupby("symbol", sort=True):
        row: dict[str, Any] = {
            "symbol": symbol,
            "as_of_date": cutoff,
            "bundle_available_date": bundle_available_date,
            "latest_report_period": str(symbol_events["report_period"].max()),
            "latest_available_date": str(symbol_events["available_date"].max()),
        }
        for field_name in events.value_columns:
            row.update(_field_state(symbol_events, field_name=field_name))
        rows.append(row)
    return pd.DataFrame(rows, columns=_state_columns(events.value_columns))


def _materialize_as_of_view(
    events: PitFundamentalsEvents,
    *,
    as_of_date: str,
    provenance_policy: str,
) -> PitAsOfView:
    cutoff = date_token(as_of_date)
    if not cutoff:
        raise ValueError(f"Expected YYYYMMDD as_of_date, got: {as_of_date}")
    policy = _validate_provenance_policy(provenance_policy)
    _assert_view_schema(events, policy=policy)
    observation = _observation_state(events, cutoff)
    visible = _visible_events(
        events,
        cutoff=cutoff,
        policy=policy,
        observation_state=observation,
    )
    state = _state_frame(
        events,
        cutoff=cutoff,
        bundle_available_date=observation.get("bundle_available_date"),
        visible=visible,
    )
    audit = _as_of_audit(
        events,
        metadata={
            "as_of_date": cutoff,
            "provenance_policy": policy,
            "observation_state": observation,
        },
        visible=visible,
        state=state,
    )
    state.attrs["pit_audit"] = audit
    return PitAsOfView(frame=state, audit=audit)


def load_pit_fundamentals_events(
    *,
    asset_dir: str | Path,
    fields: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
) -> PitFundamentalsEvents:
    """Load a reusable, column-projected PIT event set."""

    pd = pandas()
    files = asset_parquet_files(asset_dir)
    if not files:
        raise FileNotFoundError(f"No PIT fundamentals parquet files found in {asset_dir}.")
    available_columns = schema_columns(files)
    metadata = {
        "symbol",
        "trade_date",
        "report_period",
        "disclosure_date",
        "available_date",
        *PIT_SOURCE_COLUMNS,
    }
    available_fields = sorted(available_columns - metadata)
    requested_fields = list(dict.fromkeys(str(field) for field in fields or available_fields))
    unknown_fields = sorted(set(requested_fields) - set(available_fields))
    if unknown_fields:
        raise FieldValidationError(f"Unknown PIT fundamentals fields: {unknown_fields}")
    projected = sorted((metadata & available_columns) | set(requested_fields))
    scanner = ParquetBatchScanner(columns=projected, stage="pit_fundamentals_as_of_load")
    selected_symbols = {str(symbol) for symbol in symbols or () if str(symbol)}
    frames = []
    for _, frame in scanner.iter_frames(files):
        if selected_symbols and "symbol" in frame:
            frame = frame.loc[frame["symbol"].astype(str).isin(selected_symbols)]
        if not frame.empty:
            frames.append(frame)
    combined = (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame(columns=projected)
    )
    required = {
        "symbol",
        "report_period",
        "disclosure_date",
        "available_date",
        "_source_dataset",
        "_source_run_id",
    }
    missing = sorted(required - set(combined.columns))
    if missing:
        raise FieldValidationError(f"PIT fundamentals are missing as-of columns: {missing}")
    return PitFundamentalsEvents(
        frame=combined,
        manifest=asset_manifest_payload(asset_dir),
        asset_dir=Path(asset_dir).expanduser().resolve(),
        files=tuple(files),
        value_columns=tuple(requested_fields),
    )


def load_pit_fundamentals_events_from_vintages(
    *,
    asset_dirs: Sequence[str | Path],
    fields: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
) -> PitFundamentalsEvents:
    """Load events from several immutable PIT vintages into one revision ladder.

    Each input directory is loaded with the normal schema and provenance checks.  The
    resulting event set retains every retrieval vintage; the as-of materializer then
    selects the visible revision using the combined observation and bundle ladders.
    """

    directories = tuple(dict.fromkeys(Path(value).expanduser().resolve() for value in asset_dirs))
    if not directories:
        raise ValueError("asset_dirs must contain at least one PIT asset directory")
    loaded = [
        load_pit_fundamentals_events(asset_dir=directory, fields=fields, symbols=symbols)
        for directory in directories
    ]
    pd = pandas()
    value_columns = tuple(fields or loaded[0].value_columns)
    frames = [event.frame for event in loaded if not event.frame.empty]
    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    manifests = [event.manifest for event in loaded]
    source_vintages: dict[str, set[str]] = {}
    source_bundles: dict[str, list[dict[str, Any]]] = {}
    observed: set[str] = set()
    for manifest in manifests:
        for source, dates in manifest_source_vintage_dates(manifest).items():
            source_vintages.setdefault(source, set()).update(dates)
        for source, rows in manifest_source_bundle_observations(manifest).items():
            source_bundles.setdefault(source, []).extend(dict(row) for row in rows)
        observed.update(date_values(manifest.get("observed_vintage_dates")))
    manifest = dict(manifests[-1])
    manifest["source_observed_vintage_dates"] = {
        source: sorted(dates) for source, dates in sorted(source_vintages.items())
    }
    manifest["source_bundle_observations"] = {
        source: sorted(
            rows,
            key=lambda row: (row["bundle_available_date"], row["retrieval_start_date"]),
        )
        for source, rows in sorted(source_bundles.items())
    }
    manifest["observed_vintage_dates"] = sorted(observed)
    return PitFundamentalsEvents(
        frame=combined,
        manifest=manifest,
        asset_dir=directories[0].parent,
        files=tuple(file for event in loaded for file in event.files),
        value_columns=value_columns,
        asset_dirs=directories,
        source_manifests=tuple(manifests),
    )


def load_pit_fundamentals_as_of_view(
    *,
    asset_dir: str | Path,
    as_of_date: str,
    provenance_policy: str = "require_observed",
    fields: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
) -> PitAsOfView:
    events = load_pit_fundamentals_events(
        asset_dir=asset_dir,
        fields=fields,
        symbols=symbols,
    )
    return events.as_of(as_of_date, provenance_policy=provenance_policy)


def load_pit_fundamentals_as_of(
    *,
    asset_dir: str | Path,
    as_of_date: str,
    provenance_policy: str = "require_observed",
    fields: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
) -> Any:
    """Return a field-wise state frame at a calendar cutoff; audit is in ``attrs``."""

    return load_pit_fundamentals_as_of_view(
        asset_dir=asset_dir,
        as_of_date=as_of_date,
        provenance_policy=provenance_policy,
        fields=fields,
        symbols=symbols,
    ).frame


@dataclass
class _PanelState:
    field_records: dict[
        str,
        dict[str, tuple[tuple[str, str, str, str], Mapping[str, Any]]],
    ] = field(default_factory=dict)
    latest_report_period: dict[str, str] = field(default_factory=dict)
    latest_available_date: dict[str, str] = field(default_factory=dict)


def _event_priority(record: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        date_token(record.get("report_period")),
        date_token(record.get("available_date")),
        date_token(record.get("disclosure_date")),
        str(
            record.get("_retrieval_order_token")
            or _latest_retrieval_timestamp(record.get("_source_retrieved_at"))
        ),
    )


def _apply_panel_event(
    state: _PanelState,
    record: Mapping[str, Any],
    value_columns: Sequence[str],
) -> None:
    pd = pandas()
    symbol = str(record["symbol"])
    report_period, available_date, _, _ = _event_priority(record)
    state.latest_report_period[symbol] = max(
        report_period,
        state.latest_report_period.get(symbol, ""),
    )
    state.latest_available_date[symbol] = max(
        available_date,
        state.latest_available_date.get(symbol, ""),
    )
    symbol_fields = state.field_records.setdefault(symbol, {})
    priority = _event_priority(record)
    for field_name in value_columns:
        value = record.get(field_name)
        if not pd.notna(value):
            continue
        current = symbol_fields.get(field_name)
        if current is None or priority > current[0]:
            symbol_fields[field_name] = (priority, record)
