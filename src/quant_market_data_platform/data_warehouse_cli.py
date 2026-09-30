from __future__ import annotations

import argparse

PRESET_CHOICES = ("generic", "industry-labels", "pit-fundamentals")
ARTIFACTS_ROOT_HELP = "Default: DATA_PLATFORM_ROOT or artifacts/."


def add_catalog_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--artifacts-root",
        default=None,
        help=f"Artifacts root to scan. {ARTIFACTS_ROOT_HELP}",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=(
            "Optional SQLite catalog output path. "
            "Default: DATA_PLATFORM_METADATA_DB_PATH or "
            "<artifacts_root>/metadata/catalog.sqlite."
        ),
    )
    parser.add_argument(
        "--summary-out",
        default=None,
        help="Optional CSV export of catalog rows. Default: <db_path parent>/catalog_summary.csv.",
    )


def add_materialize_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--artifacts-root",
        default=None,
        help=f"Artifacts root used for default outputs. {ARTIFACTS_ROOT_HELP}",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Logical materialization name; also used for the DuckDB view name.",
    )
    parser.add_argument(
        "--market",
        default="a_share",
        help="Market tag written into the standardized manifest. Default: a_share.",
    )
    parser.add_argument(
        "--preset",
        default="generic",
        choices=PRESET_CHOICES,
        help="Column default preset. Default: generic.",
    )
    parser.add_argument(
        "--dataset-name",
        help=(
            "Logical dataset group under artifacts/standardized/<market>/. "
            "Default comes from --preset."
        ),
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--asset-dir",
        help="Mirror asset directory that contains data/*.parquet.",
    )
    source_group.add_argument(
        "--file",
        help="Input flat file (.parquet or .csv).",
    )
    parser.add_argument(
        "--date-col",
        help="Input date column name. Default comes from --preset.",
    )
    parser.add_argument(
        "--symbol-col",
        help="Input symbol column name. Default comes from --preset.",
    )
    parser.add_argument(
        "--frequency",
        default="D",
        help="Output sampling frequency: D, M, or Q. Default: D.",
    )
    parser.add_argument(
        "--out-root",
        default=None,
        help="Standardized layer root. Default: <artifacts_root>/standardized.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing standardized output directory.",
    )


def add_query_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--artifacts-root",
        default=None,
        help=(
            "Artifacts root used for default metadata and standardized paths. "
            f"{ARTIFACTS_ROOT_HELP}"
        ),
    )
    parser.add_argument(
        "--sql",
        help="SQL statement executed against DuckDB after standardized views are refreshed.",
    )
    parser.add_argument(
        "--sql-file",
        help="Path to a .sql file executed against DuckDB.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=(
            "DuckDB database path. Default: DATA_PLATFORM_WAREHOUSE_DB_PATH or "
            "<artifacts_root>/metadata/warehouse.duckdb."
        ),
    )
    parser.add_argument(
        "--standardized-root",
        default=None,
        help=(
            "Standardized layer root scanned for manifest-backed views. "
            "Default: <artifacts_root>/standardized."
        ),
    )
    parser.add_argument(
        "--format",
        default="text",
        choices=("text", "json", "csv", "parquet"),
        help="Output format. Default: text.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output file. Required for --format parquet.",
    )
