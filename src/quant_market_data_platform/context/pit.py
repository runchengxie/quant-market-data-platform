from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

import pandas as pd

from .models import validate_context_observations


@dataclass(frozen=True)
class ContextPITPanel:
    frame: pd.DataFrame
    audit: Mapping[str, Any]


def _utc_timestamp(value: str | datetime | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")  # ty: ignore[invalid-return-type]
    return timestamp.tz_convert("UTC")


def _latest_series_rows(selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return selected.copy()
    ordered = selected.sort_values(
        ["series_id", "period_end", "available_at", "revision_number"], kind="stable"
    )
    return ordered.drop_duplicates("series_id", keep="last").reset_index(drop=True)


def select_context_as_of(
    observations: pd.DataFrame,
    *,
    as_of: str | datetime | pd.Timestamp,
    series_ids: Sequence[str] | None = None,
    require_revision_covered: bool = False,
    max_staleness_days: int | None = None,
) -> ContextPITPanel:
    frame = validate_context_observations(observations)
    as_of_ts = _utc_timestamp(as_of)
    requested = tuple(
        dict.fromkeys(str(value).strip() for value in (series_ids or ()) if str(value).strip())
    )
    if requested:
        frame = frame.loc[frame["series_id"].isin(requested)].copy()

    visible = frame.loc[
        (frame["available_at"] <= as_of_ts) & (frame["source_retrieved_at"] <= as_of_ts)
    ].copy()
    visible = visible.sort_values(
        [
            "series_id",
            "period_end",
            "available_at",
            "revision_number",
            "source_retrieved_at",
            "vintage_id",
        ],
        kind="stable",
    )
    selected = visible.drop_duplicates(["series_id", "period_end"], keep="last").reset_index(
        drop=True
    )

    present = set(selected["series_id"].astype(str)) if not selected.empty else set()
    missing = [series_id for series_id in requested if series_id not in present]
    if requested and missing and require_revision_covered:
        raise ValueError(f"context PIT is missing requested series: {', '.join(missing)}")

    latest = _latest_series_rows(selected)
    if latest.empty:
        max_age = None
        stale_series: list[str] = []
    else:
        latest_ages = (as_of_ts - latest["available_at"]).dt.total_seconds() / 86400.0
        max_age = float(latest_ages.max())
        if max_staleness_days is None:
            stale_series = []
        else:
            stale_mask = latest_ages > float(max_staleness_days)
            stale_series = sorted(set(latest.loc[stale_mask, "series_id"].astype(str)))

    reconstructed_series = sorted(
        set(selected.loc[selected["reconstructed"], "series_id"].astype(str))
    )
    revision_covered = bool(
        not selected.empty
        and not missing
        and not reconstructed_series
        and bool(selected["revision_covered"].all())
    )
    freshness_verified = bool(not selected.empty and not stale_series)
    if require_revision_covered and not revision_covered:
        raise ValueError("context PIT is not revision-covered for the requested as-of state")

    selected_vintages = {
        f"{row.series_id}|{pd.Timestamp(row.period_end).isoformat()}": str(row.vintage_id)  # ty: ignore[unresolved-attribute]
        for row in selected.itertuples(index=False)
    }
    audit = MappingProxyType(
        {
            "as_of": as_of_ts.isoformat(),
            "revision_covered": revision_covered,
            "freshness_verified": freshness_verified,
            "series_missing": missing,
            "series_stale": stale_series,
            "selected_vintages": selected_vintages,
            "max_observation_age": max_age,
            "reconstructed_series": reconstructed_series,
        }
    )
    return ContextPITPanel(frame=selected, audit=audit)
