from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

ARTIFACTS_ROOT_HELP = "Default: DATA_PLATFORM_ROOT or artifacts/."

FREQUENCY_ALIASES = {
    "D": "D",
    "DAY": "D",
    "DAILY": "D",
    "M": "M",
    "MONTH": "M",
    "MONTHLY": "M",
    "Q": "Q",
    "QUARTER": "Q",
    "QUARTERLY": "Q",
}

PRESET_DEFAULTS: dict[str, dict[str, str]] = {
    "pit-fundamentals": {
        "dataset": "pit_fundamentals",
        "date_col": "trade_date",
        "symbol_col": "symbol",
    },
    "industry-labels": {
        "dataset": "industry_labels",
        "date_col": "trade_date",
        "symbol_col": "symbol",
    },
    "generic": {
        "dataset": "generic",
        "date_col": "trade_date",
        "symbol_col": "symbol",
    },
}


@dataclass(frozen=True)
class CatalogArtifact:
    artifact_id: str
    layer: str
    dataset: str | None
    market: str | None
    name: str
    path: str
    manifest_path: str
    status: str | None
    output_format: str | None
    created_at: str | None
    start_value: str | None
    end_value: str | None
    row_count: int | None
    symbol_count: int | None
    trade_date_count: int | None
    file_count: int | None
    total_bytes: int | None
    frequency: str | None
    source_asset_dir: str | None
    source_manifest: str | None
    view_name: str | None
    metadata_json: str


@dataclass(frozen=True)
class CatalogLineage:
    artifact_id: str
    relation: str
    source_path: str


@dataclass(frozen=True)
class MaterializeStats:
    input_files: int
    input_rows: int
    output_rows: int
    output_files: int
    symbols: int
    trade_dates: int
    trade_date_min: str | None
    trade_date_max: str | None
    rows_missing_date_dropped: int
    rows_missing_symbol_dropped: int
    duplicate_rows_dropped: int


@dataclass(frozen=True)
class MaterializeManifestInputs:
    name: str
    dataset: str
    market: str
    preset: str
    frequency: str
    source_mode: str
    asset_dir: Path | None
    file_path: Path | None
    source_manifest: Path | None
    output_dir: Path
    output_data_dir: Path
    view_name: str
    date_col: str
    symbol_col: str
    column_dtypes: Mapping[str, str]
    stats: MaterializeStats
