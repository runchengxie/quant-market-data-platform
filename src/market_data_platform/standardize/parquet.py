"""Parquet loading helpers for standardization and research data stages."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def read_parquet_parts(asset_dir: str | Path, *, label: str) -> pd.DataFrame:
    root = Path(asset_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"{label} asset directory not found: {root}")
    data_root = root / "data" if (root / "data").exists() else root
    files = sorted(data_root.glob("**/*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(path) for path in files]
    frames = [frame for frame in frames if frame is not None and not frame.empty]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def parquet_columns(path: str | Path) -> list[str] | None:
    """Return physical and Hive partition columns when the schema is readable."""

    try:
        import pyarrow.parquet as pq
    except ImportError:
        return None
    source = Path(path)
    schema_path = source
    if source.is_dir():
        candidates = sorted(source.glob("**/*.parquet"))
        if not candidates:
            return None
        schema_path = candidates[0]
    try:
        columns = list(pq.read_schema(schema_path).names)
    except Exception:
        return None
    if source.is_dir():
        columns = list(dict.fromkeys([*columns, *hive_partition_columns(source)]))
    return columns


def hive_partition_columns(path: str | Path) -> list[str]:
    """Return partition field names encoded as ``name=value`` path components."""

    source = Path(path)
    if not source.is_dir():
        return []
    columns: list[str] = []
    for file_path in sorted(source.glob("**/*.parquet")):
        for part in file_path.relative_to(source).parts[:-1]:
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            if name and value:
                columns.append(name)
        if columns:
            break
    return list(dict.fromkeys(columns))


def hive_partition_values(path: str | Path) -> dict[str, str]:
    """Return partition values encoded in a Parquet file path."""

    values: dict[str, str] = {}
    for part in Path(path).parts:
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        if name and value:
            values[name] = value
    return values


def csv_columns(path: str | Path) -> list[str] | None:
    """Read only the header of a CSV file, returning ``None`` on inspection errors."""

    try:
        return list(pd.read_csv(path, nrows=0).columns)
    except Exception:
        return None


def select_available_columns(
    requested_columns: Sequence[str],
    available_columns: Iterable[str] | None,
) -> list[str]:
    """Keep requested columns that are available while preserving request order."""

    requested = list(dict.fromkeys(str(column) for column in requested_columns if str(column)))
    if not requested:
        return []
    if available_columns is None:
        return requested
    available = set(available_columns)
    return [column for column in requested if column in available]


def read_parquet_dataset_compat(
    path: str | Path,
    *,
    columns: Sequence[str] | None = None,
    label: str = "data",
) -> pd.DataFrame:
    """Read a Parquet file or dataset, with a per-file Hive fallback."""

    source = Path(path)
    try:
        return pd.read_parquet(source, columns=list(columns) if columns else None)
    except Exception as exc:
        if not source.is_dir():
            raise
        files = sorted(source.glob("**/*.parquet"))
        if not files:
            raise
        logger.warning(
            "Could not read %s parquet dataset directory %s as one dataset (%s); "
            "falling back to per-file reads.",
            label,
            source,
            exc,
        )
        frames = [
            read_parquet_file_with_partitions(file_path, columns=columns) for file_path in files
        ]
        if not frames:
            return pd.DataFrame(columns=pd.Index(list(columns) if columns else []))
        return pd.concat(frames, ignore_index=True)


def read_parquet_file_with_partitions(
    path: str | Path,
    *,
    columns: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Read one Parquet file and materialize missing Hive partition columns."""

    source = Path(path)
    file_columns = parquet_columns(source)
    partition_values = hive_partition_values(source)
    if columns:
        requested = list(dict.fromkeys(columns))
        read_columns = [
            column for column in requested if file_columns is None or column in file_columns
        ]
        frame = pd.read_parquet(source, columns=read_columns or None)
        for column in requested:
            if column not in frame.columns and column in partition_values:
                frame[column] = partition_values[column]
        return frame.loc[:, [column for column in requested if column in frame.columns]]
    frame = pd.read_parquet(source)
    for column, value in partition_values.items():
        if column not in frame.columns:
            frame[column] = value
    return frame
