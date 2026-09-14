from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

CATALOG_REQUIRED_COLUMNS = (
    "series_id",
    "source_id",
    "provider",
    "source_series_key",
    "name",
    "family",
    "frequency",
    "unit",
    "seasonal_adjustment",
    "value_semantics",
    "revision_policy",
    "availability_policy",
    "expected_release_lag",
    "max_staleness",
    "status",
)

OBSERVATION_REQUIRED_COLUMNS = (
    "series_id",
    "period_start",
    "period_end",
    "value",
    "unit",
    "published_at",
    "observed_at",
    "ingested_at",
    "source_retrieved_at",
    "available_at",
    "vintage_id",
    "revision_number",
    "source_hash",
    "revision_covered",
    "reconstructed",
)

_TIMESTAMP_COLUMNS = (
    "period_start",
    "period_end",
    "published_at",
    "observed_at",
    "ingested_at",
    "source_retrieved_at",
    "available_at",
)


@dataclass(frozen=True)
class ContextSeriesSpec:
    series_id: str
    source_id: str
    provider: str
    source_series_key: str
    name: str
    family: str
    frequency: str
    unit: str
    seasonal_adjustment: str
    value_semantics: str
    revision_policy: str
    availability_policy: str
    expected_release_lag: str
    max_staleness: int
    status: str = "active"


@dataclass(frozen=True)
class ContextValidationReport:
    rows: int
    series: int
    reconstructed_rows: int
    revision_covered_rows: int


def _require_columns(frame: pd.DataFrame, required: tuple[str, ...], *, name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{name} is missing required columns: {', '.join(missing)}")


def normalize_context_catalog(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame, CATALOG_REQUIRED_COLUMNS, name="context catalog")
    normalized = frame.loc[:, list(CATALOG_REQUIRED_COLUMNS)].copy()
    for column in CATALOG_REQUIRED_COLUMNS:
        if column == "max_staleness":
            continue
        normalized[column] = normalized[column].astype("string").str.strip()
    normalized["max_staleness"] = pd.to_numeric(normalized["max_staleness"], errors="raise")
    if normalized["series_id"].eq("").any():
        raise ValueError("context catalog series_id must be non-empty")
    if normalized["series_id"].duplicated().any():
        raise ValueError("context catalog contains duplicate series_id values")
    if normalized["max_staleness"].lt(0).any():
        raise ValueError("context catalog max_staleness must be non-negative")
    return normalized.reset_index(drop=True)


def validate_context_observations(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame, OBSERVATION_REQUIRED_COLUMNS, name="context observations")
    normalized = frame.copy()
    for column in _TIMESTAMP_COLUMNS:
        normalized[column] = pd.to_datetime(normalized[column], utc=True, errors="coerce")
    for required_timestamp in (
        "period_start",
        "period_end",
        "observed_at",
        "ingested_at",
        "source_retrieved_at",
        "available_at",
    ):
        if normalized[required_timestamp].isna().any():
            raise ValueError(f"context observations {required_timestamp} must be valid timestamps")
    normalized["series_id"] = normalized["series_id"].astype("string").str.strip()
    normalized["vintage_id"] = normalized["vintage_id"].astype("string").str.strip()
    normalized["source_hash"] = normalized["source_hash"].astype("string").str.strip()
    normalized["revision_number"] = pd.to_numeric(
        normalized["revision_number"], errors="raise", downcast="integer"
    )
    normalized["revision_covered"] = normalized["revision_covered"].astype(bool)
    normalized["reconstructed"] = normalized["reconstructed"].astype(bool)

    duplicate_key = ["series_id", "period_end", "vintage_id"]
    if normalized.duplicated(duplicate_key).any():
        raise ValueError("context observations contain duplicate series/period/vintage rows")
    if (normalized["period_start"] > normalized["period_end"]).any():
        raise ValueError("context observations period_start cannot exceed period_end")
    if normalized["revision_number"].lt(0).any():
        raise ValueError("context observations revision_number must be non-negative")
    if normalized["source_hash"].str.fullmatch(r"[0-9a-fA-F]{64}").fillna(False).eq(False).any():
        raise ValueError("context observations source_hash must be a SHA-256 hex digest")
    if (normalized["source_retrieved_at"] < normalized["observed_at"]).any():
        raise ValueError("context observations source_retrieved_at cannot precede observed_at")

    published = normalized["published_at"].notna()
    if (published & (normalized["available_at"] < normalized["published_at"])).any():
        raise ValueError("context observations available_at cannot precede published_at")
    if (normalized["reconstructed"] & normalized["revision_covered"]).any():
        raise ValueError("reconstructed context observations cannot be revision_covered")
    return normalized.reset_index(drop=True)


def summarize_context_validation(frame: pd.DataFrame) -> ContextValidationReport:
    normalized = validate_context_observations(frame)
    return ContextValidationReport(
        rows=len(normalized),
        series=int(normalized["series_id"].nunique()),
        reconstructed_rows=int(normalized["reconstructed"].sum()),
        revision_covered_rows=int(normalized["revision_covered"].sum()),
    )
