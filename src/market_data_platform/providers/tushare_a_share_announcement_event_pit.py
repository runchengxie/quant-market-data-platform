"""Research-only announcement-time event history for TuShare fundamentals."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_fundamentals_part02 import (
    _raw_frames_with_retrieval,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    asset_manifest_payload,
    asset_parquet_files,
    build_asset_integrity,
    date_token,
    seal_manifest,
)
from market_data_platform.providers.tushare_common import (
    normalize_ts_code,
    pandas,
    write_manifest,
)

EVENT_SCHEMA_VERSION = "tushare.a_share.fundamentals.announcement_event_pit.v1"

REPORT_TYPE_POLICIES: dict[str, tuple[str, ...] | None] = {
    "standard": ("1",),
    "diagnostic_including_type5": ("1", "5"),
    "all": None,
    "dataset_native": None,
}


@dataclass(frozen=True)
class AnnouncementEventPitOptions:
    """Inputs for a non-production announcement-time event asset."""

    raw_dir: str | Path
    out_dir: str | Path
    dataset: str
    value_columns: tuple[str, ...] | None = None
    include_report_types: tuple[str, ...] | None = None


def _first_date(row: Any) -> str:
    return date_token(row.get("f_ann_date")) or date_token(row.get("ann_date"))


def build_announcement_event_pit(  # noqa: PLR0915
    options: AnnouncementEventPitOptions,
) -> dict[str, Any]:
    """Build an event-preserving financial asset from one immutable raw asset.

    This asset deliberately does not collapse provider revisions or report types.
    It supports announcement-time research, but does not claim a complete vendor
    revision history because TuShare does not document one.
    """

    pd = pandas()
    source = Path(options.raw_dir).expanduser().resolve()
    output = Path(options.out_dir).expanduser().resolve()
    manifest = asset_manifest_payload(source)
    if manifest.get("schema_version") != "tushare.a_share.fundamentals.raw.v2":
        raise ValueError("announcement event PIT requires a raw.v2 TuShare asset")
    if manifest.get("status") != "completed" or manifest.get("immutable_snapshot") is not True:
        raise ValueError("announcement event PIT requires a completed immutable raw asset")

    files = asset_parquet_files(source)
    if not files:
        raise FileNotFoundError(f"No raw parquet files found in {source}")
    frames = _raw_frames_with_retrieval(source, manifest)
    frame = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    required = {"ts_code", "end_date"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"raw asset is missing required event columns: {missing}")

    frame = frame.copy()
    frame["symbol"] = frame["ts_code"].map(normalize_ts_code)
    frame["report_period"] = frame["end_date"].map(date_token)
    frame["available_date"] = frame.apply(_first_date, axis=1)
    frame["disclosure_date"] = frame["available_date"]
    frame["_source_dataset"] = options.dataset
    frame["_source_raw_asset"] = str(source)
    frame["_source_run_id"] = str(manifest.get("source_run_id") or source.name)
    frame["pit_class"] = "announcement_event_pit"

    requested_types = {str(value) for value in options.include_report_types or ()}
    excluded_report_types = 0
    if requested_types and "report_type" in frame:
        included = frame["report_type"].astype(str).isin(requested_types)
        excluded_report_types = int((~included).sum())
        frame = frame.loc[included].copy()

    missing_availability = frame["available_date"].eq("")
    dropped_missing_availability = int(missing_availability.sum())
    frame = frame.loc[~missing_availability & frame["symbol"].ne("")].copy()

    identity_columns = [
        "_source_dataset",
        "ts_code",
        "symbol",
        "end_date",
        "report_period",
        "ann_date",
        "f_ann_date",
        "available_date",
        "report_type",
        "update_flag",
    ]
    identity_columns = [column for column in identity_columns if column in frame]
    value_columns = list(options.value_columns or ())
    if not value_columns:
        excluded = set(identity_columns) | {
            "symbol",
            "report_period",
            "available_date",
            "disclosure_date",
            "pit_class",
            "_source_retrieved_at",
        }
        value_columns = [column for column in frame.columns if column not in excluded]
    unknown = sorted(set(value_columns) - set(frame.columns))
    if unknown:
        raise ValueError(f"raw asset is missing requested value columns: {unknown}")

    hash_columns = [column for column in frame.columns if not column.startswith("__")]
    row_fingerprint = pd.util.hash_pandas_object(
        frame.loc[:, hash_columns].astype("string"), index=False
    )
    frame["row_hash"] = row_fingerprint.map(lambda value: f"{int(value):016x}")
    event_identity = frame.loc[:, [*identity_columns, "row_hash"]].astype("string")
    event_fingerprint = pd.util.hash_pandas_object(event_identity, index=False)
    frame["event_id"] = event_fingerprint.map(lambda value: f"event-{int(value):016x}")
    frame["revision_id"] = frame["row_hash"].str[:16]
    output_columns = [
        "symbol",
        "ts_code",
        "report_period",
        "end_date",
        "ann_date",
        "f_ann_date",
        "disclosure_date",
        "available_date",
        "report_type",
        "update_flag",
        "pit_class",
        "event_id",
        "revision_id",
        "row_hash",
        "_source_dataset",
        "_source_raw_asset",
        "_source_run_id",
        "_source_retrieved_at",
        *value_columns,
    ]
    output_columns = list(dict.fromkeys(column for column in output_columns if column in frame))
    sort_columns = [
        column
        for column in ["symbol", "report_period", "available_date", "report_type", "update_flag"]
        if column in frame
    ]
    frame = (
        frame.loc[:, output_columns]
        .sort_values(sort_columns, kind="mergesort")
        .reset_index(drop=True)
    )

    data_path = output / "data" / "part.parquet"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(data_path, index=False)
    result = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "dataset": "announcement_event_pit",
        "source_dataset": options.dataset,
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "pit_class": "announcement_event_pit",
        "immutable_snapshot": True,
        "output_dir": str(output),
        "source_raw_dir": str(source),
        "source_run_id": manifest.get("source_run_id"),
        "integrity": build_asset_integrity(output, [data_path]),
        "semantics": {
            "available_date_rule": "f_ann_date_then_ann_date",
            "complete_revision_history": False,
            "all_report_types_preserved": not bool(requested_types),
            "update_flag_not_collapsed": True,
            "research_only": True,
        },
        "filters": {
            "include_report_types": sorted(requested_types) or None,
            "excluded_report_type_rows": excluded_report_types,
            "dropped_missing_availability_rows": dropped_missing_availability,
        },
        "totals": {
            "rows": int(len(frame)),
            "symbols": int(frame["symbol"].nunique()) if not frame.empty else 0,
            "files": 1,
        },
        "columns": output_columns,
    }
    write_manifest(output / "manifest.yml", result)
    seal_manifest(output / "manifest.yml")
    (output / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return result


def _report_type_policy_types(policy: str) -> tuple[str, ...] | None:
    try:
        return REPORT_TYPE_POLICIES[policy]
    except KeyError as exc:
        supported = ", ".join(sorted(REPORT_TYPE_POLICIES))
        raise ValueError(f"Unknown report_type policy {policy!r}; use: {supported}") from exc


def select_announcement_events_as_of(
    frame: Any,
    *,
    as_of_date: str,
    report_type_policy: str = "standard",
    value_columns: Sequence[str] | None = None,
) -> Any:
    """Select the latest visible event for each report period under an explicit policy.

    The function is deliberately research-only: it preserves the selected provider
    report type and revision metadata, and makes no claim that the source contains
    a complete historical vendor revision chain.
    """

    pd = pandas()
    allowed_types = _report_type_policy_types(report_type_policy)
    required = {"symbol", "report_period", "available_date"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"announcement event frame is missing required columns: {missing}")
    if "report_type" not in frame and report_type_policy not in {"all", "dataset_native"}:
        raise ValueError(
            "report_type is unavailable; use report_type_policy='dataset_native' explicitly"
        )
    selected = frame.copy()
    selected["_available_date_sort"] = selected["available_date"].astype("string")
    selected = selected.loc[selected["_available_date_sort"] <= str(as_of_date)].copy()
    if allowed_types is not None and "report_type" in selected:
        selected = selected.loc[selected["report_type"].astype("string").isin(allowed_types)].copy()
    if selected.empty:
        columns = list(value_columns) if value_columns else list(frame.columns)
        return frame.iloc[0:0].loc[:, [column for column in columns if column in frame]]

    update_flag = selected.get("update_flag", pd.Series(0, index=selected.index, dtype="int64"))
    selected["_update_flag_sort"] = pd.to_numeric(update_flag, errors="coerce").fillna(-1)
    selected["_event_id_sort"] = selected.get(
        "event_id", pd.Series(selected.index, index=selected.index, dtype="string")
    ).astype("string")
    keys = ["symbol", "report_period"]
    if report_type_policy != "standard" and "report_type" in selected:
        keys.append("report_type")
    selected = selected.sort_values(
        [*keys, "_available_date_sort", "_update_flag_sort", "_event_id_sort"],
        kind="mergesort",
    )
    selected = selected.drop_duplicates(keys, keep="last")
    selected = selected.sort_values(keys, kind="mergesort").reset_index(drop=True)
    helper_columns = [
        "_available_date_sort",
        "_update_flag_sort",
        "_event_id_sort",
    ]
    selected = selected.drop(columns=[column for column in helper_columns if column in selected])
    if value_columns is not None:
        requested = [column for column in value_columns if column in selected]
        metadata = [
            column for column in [*keys, "available_date", "event_id"] if column in selected
        ]
        selected = selected.loc[:, list(dict.fromkeys([*metadata, *requested]))]
    return selected


def load_announcement_event_as_of_panel(
    asset_dir: str | Path,
    *,
    as_of_date: str,
    report_type_policy: str = "standard",
    value_columns: Sequence[str] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Load one event asset and materialize its announcement-time as-of panel."""

    pd = pandas()
    root = Path(asset_dir).expanduser().resolve()
    files = asset_parquet_files(root)
    if not files:
        raise FileNotFoundError(f"No event parquet files found in {root}")
    frame = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True, sort=False)
    visible = frame.loc[frame["available_date"].astype("string") <= str(as_of_date)]
    panel = select_announcement_events_as_of(
        frame,
        as_of_date=as_of_date,
        report_type_policy=report_type_policy,
        value_columns=value_columns,
    )
    audit = {
        "as_of_date": str(as_of_date),
        "report_type_policy": report_type_policy,
        "visible_event_rows": int(len(visible)),
        "panel_rows": int(len(panel)),
        "selected_revision_rows": int(len(panel)),
    }
    return panel, audit


def load_announcement_event_fundamental_panel(
    asset_dirs: Mapping[str, str | Path],
    *,
    as_of_date: str,
    report_type_policies: Mapping[str, str] | None = None,
    value_columns: Mapping[str, str | Sequence[str]] | None = None,
    annual_only: bool = True,
) -> tuple[Any, dict[str, Any]]:
    """Join component as-of panels into one research-only fundamental panel."""

    if not asset_dirs:
        raise ValueError("fundamental panel requires at least one component asset")
    policies = dict(report_type_policies or {})
    components: list[Any] = []
    component_audits: dict[str, Any] = {}
    for dataset, asset_dir in asset_dirs.items():
        requested = value_columns.get(dataset) if value_columns else None
        policy = policies.get(dataset, "standard")
        try:
            component, audit = load_announcement_event_as_of_panel(
                asset_dir,
                as_of_date=as_of_date,
                report_type_policy=policy,
                value_columns=((requested,) if isinstance(requested, str) else requested),
            )
        except ValueError as exc:
            if "report_type is unavailable" not in str(exc):
                raise
            if dataset not in policies:
                raise ValueError(
                    f"{dataset} has no report_type; configure report_type_policies explicitly"
                ) from exc
            raise
        if annual_only:
            component = component.loc[
                component["report_period"].astype("string").str.endswith("1231")
            ].copy()
        available_col = f"available_date_{dataset}"
        component = component.rename(columns={"available_date": available_col})
        keep = ["symbol", "report_period", available_col]
        requested_names = [requested] if isinstance(requested, str) else list(requested or ())
        keep.extend(column for column in requested_names if column in component)
        keep = list(dict.fromkeys(keep))
        component = component.loc[:, keep]
        if component.duplicated(["symbol", "report_period"]).any():
            raise ValueError(f"{dataset} as-of panel has duplicate annual component rows")
        components.append(component)
        component_audits[dataset] = audit

    result = components[0]
    for component in components[1:]:
        result = result.merge(
            component,
            on=["symbol", "report_period"],
            how="inner",
            validate="one_to_one",
        )
    availability_columns = [
        column for column in result.columns if column.startswith("available_date_")
    ]
    result["panel_available_date"] = result[availability_columns].max(axis=1)
    result = result.sort_values(["report_period", "symbol"], kind="mergesort").reset_index(
        drop=True
    )
    audit = {
        "schema_version": "tushare.a_share.fundamentals.announcement_event_panel.v1",
        "as_of_date": str(as_of_date),
        "report_type_policies": policies,
        "annual_only": annual_only,
        "component_count": len(asset_dirs),
        "component_audits": component_audits,
        "panel_rows": int(len(result)),
        "symbols": int(result["symbol"].nunique()) if not result.empty else 0,
        "report_periods": int(result["report_period"].nunique()) if not result.empty else 0,
        "research_only": True,
        "complete_revision_history": False,
    }
    return result, audit


__all__ = [
    "AnnouncementEventPitOptions",
    "REPORT_TYPE_POLICIES",
    "build_announcement_event_pit",
    "load_announcement_event_as_of_panel",
    "load_announcement_event_fundamental_panel",
    "select_announcement_events_as_of",
]
