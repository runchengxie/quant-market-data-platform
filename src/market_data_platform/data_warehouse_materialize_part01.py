from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .data_warehouse_models import (
    FREQUENCY_ALIASES,
    PRESET_DEFAULTS,
    MaterializeManifestInputs,
    MaterializeStats,
)
from .symbols import resolve_symbol_series


def _timestamp_now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def _read_git_value(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def _git_metadata(repo_root: Path) -> dict | None:
    commit = _read_git_value(repo_root, "rev-parse", "HEAD")
    if not commit:
        return None
    short_commit = _read_git_value(repo_root, "rev-parse", "--short", "HEAD")
    branch = _read_git_value(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    status = _read_git_value(repo_root, "status", "--short")
    return {
        "commit": commit,
        "short_commit": short_commit,
        "branch": branch,
        "is_dirty": bool(status),
    }


def _coerce_frequency(value: str | None) -> str:
    text = str(value or "D").strip().upper()
    if text not in FREQUENCY_ALIASES:
        raise SystemExit("frequency must be one of D, M, or Q.")
    return FREQUENCY_ALIASES[text]


def _sanitize_identifier(text: str) -> str:
    value = re.sub(r"[^0-9A-Za-z_]+", "_", str(text or "").strip()).strip("_").lower()
    if not value:
        value = "dataset"
    if value[0].isdigit():
        value = f"v_{value}"
    return value


def _source_manifest_for_file(path: Path) -> Path | None:
    candidate = path.with_name(f"{path.stem}.manifest.yml")
    if candidate.exists():
        return candidate
    return None


def _infer_source_manifest(*, asset_dir: Path | None, file_path: Path | None) -> Path | None:
    if asset_dir is not None:
        candidate = asset_dir / "manifest.yml"
        if candidate.exists():
            return candidate
    if file_path is not None:
        return _source_manifest_for_file(file_path)
    return None


def _collect_input_files(
    *,
    asset_dir: Path | None,
    file_path: Path | None,
) -> tuple[list[Path], str]:
    if asset_dir is not None:
        data_dir = asset_dir / "data"
        if not data_dir.exists():
            raise SystemExit(f"Asset directory is missing data/: {asset_dir}")
        files = sorted(path for path in data_dir.glob("*.parquet") if path.is_file())
        if not files:
            raise SystemExit(f"No parquet files found under {data_dir}")
        return files, "asset_dir"
    if file_path is not None:
        if not file_path.exists():
            raise SystemExit(f"Input file not found: {file_path}")
        return [file_path], "file"
    raise SystemExit("Provide either --asset-dir or --file.")


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise SystemExit(f"Unsupported input file type: {path}")


def _parse_trade_date(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    fallback_mask = parsed.isna()
    if fallback_mask.any():
        parsed.loc[fallback_mask] = pd.to_datetime(text.loc[fallback_mask], errors="coerce")
    return parsed.dt.normalize()


def _resample_frequency(frame: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frequency == "D" or frame.empty:
        return frame
    work = frame.sort_values(["symbol", "trade_date"]).copy()
    if frequency == "M":
        work["_bucket"] = work["trade_date"].dt.to_period("M")
    elif frequency == "Q":
        work["_bucket"] = work["trade_date"].dt.to_period("Q")
    else:  # pragma: no cover - guarded by _coerce_frequency
        raise SystemExit(f"Unsupported frequency: {frequency}")
    return (
        work.groupby(["symbol", "_bucket"], sort=False, group_keys=False)
        .tail(1)
        .drop(columns=["_bucket"])
        .reset_index(drop=True)
    )


def _normalize_frame(
    frame: pd.DataFrame,
    *,
    source_path: Path,
    date_col: str,
    symbol_col: str,
    frequency: str,
) -> tuple[pd.DataFrame, dict[str, int]]:
    if date_col not in frame.columns:
        raise SystemExit(f"Missing date column {date_col!r} in {source_path}")

    work = frame.copy()
    resolved_symbol_col = symbol_col if symbol_col in work.columns else None
    temp_symbol_col: str | None = None
    if resolved_symbol_col is None:
        if symbol_col != "symbol":
            raise SystemExit(f"Missing symbol column {symbol_col!r} in {source_path}")
        temp_symbol_col = "__resolved_symbol__"
        work[temp_symbol_col] = resolve_symbol_series(
            work,
            context=f"Materialize input {source_path}",
        )
        resolved_symbol_col = temp_symbol_col
    for reserved, alias in (
        ("trade_date", "source_trade_date"),
        ("symbol", "source_symbol"),
        ("trade_date_key", "source_trade_date_key"),
        ("_source_file", "source_source_file"),
        ("trade_year", "source_trade_year"),
    ):
        if reserved in work.columns and reserved not in {date_col, symbol_col}:
            work = work.rename(columns={reserved: alias})
    source_date_col = date_col
    source_symbol_col = resolved_symbol_col
    if date_col == "trade_date":
        work = work.rename(columns={"trade_date": "source_trade_date"})
        source_date_col = "source_trade_date"
    if resolved_symbol_col == "symbol":
        work = work.rename(columns={"symbol": "source_symbol"})
        source_symbol_col = "source_symbol"

    parsed_dates = _parse_trade_date(work[source_date_col])
    rows_missing_date_dropped = int(parsed_dates.isna().sum())
    work.insert(0, "trade_date", parsed_dates)
    work = work[work["trade_date"].notna()].copy()

    normalized_symbol = work[source_symbol_col].astype(str).str.strip()
    rows_missing_symbol_dropped = int((normalized_symbol == "").sum())
    work.insert(1, "trade_date_key", work["trade_date"].dt.strftime("%Y%m%d"))
    work.insert(2, "symbol", normalized_symbol)
    if temp_symbol_col is not None:
        work = work.drop(columns=[temp_symbol_col], errors="ignore")
    work = work[work["symbol"] != ""].copy()
    work.insert(3, "_source_file", str(source_path))
    work = work.sort_values(["symbol", "trade_date"]).reset_index(drop=True)

    duplicate_rows_dropped = int(
        work.duplicated(subset=["trade_date", "symbol"], keep="last").sum()
    )
    work = work.drop_duplicates(subset=["trade_date", "symbol"], keep="last").reset_index(drop=True)
    work = _resample_frequency(work, frequency)
    work["trade_year"] = work["trade_date"].dt.strftime("%Y")
    return work, {
        "rows_missing_date_dropped": rows_missing_date_dropped,
        "rows_missing_symbol_dropped": rows_missing_symbol_dropped,
        "duplicate_rows_dropped": duplicate_rows_dropped,
    }


def _write_partitioned_parquet(
    frame: pd.DataFrame,
    *,
    output_data_dir: Path,
    part_index: int,
) -> int:
    if frame.empty:
        empty_dir = output_data_dir / "trade_year=empty"
        empty_dir.mkdir(parents=True, exist_ok=True)
        empty_path = empty_dir / f"part-{part_index:05d}.parquet"
        frame.drop(columns=["trade_year"], errors="ignore").to_parquet(empty_path, index=False)
        return 1

    files_written = 0
    for trade_year, part in frame.groupby("trade_year", sort=True):
        year_dir = output_data_dir / f"trade_year={trade_year}"
        year_dir.mkdir(parents=True, exist_ok=True)
        part_path = year_dir / f"part-{part_index + files_written:05d}.parquet"
        part.drop(columns=["trade_year"], errors="ignore").to_parquet(part_path, index=False)
        files_written += 1
    return files_written


def _build_materialize_manifest(inputs: MaterializeManifestInputs) -> dict:
    name = inputs.name
    dataset = inputs.dataset
    market = inputs.market
    preset = inputs.preset
    frequency = inputs.frequency
    source_mode = inputs.source_mode
    asset_dir = inputs.asset_dir
    file_path = inputs.file_path
    source_manifest = inputs.source_manifest
    output_dir = inputs.output_dir
    output_data_dir = inputs.output_data_dir
    view_name = inputs.view_name
    date_col = inputs.date_col
    symbol_col = inputs.symbol_col
    column_dtypes = inputs.column_dtypes
    stats = inputs.stats
    return {
        "name": name,
        "created_at": _timestamp_now(),
        "status": "completed",
        "layer": "standardized",
        "dataset": dataset,
        "market": market,
        "view_name": view_name,
        "frequency": frequency,
        "source_asset_dir": str(asset_dir) if asset_dir is not None else None,
        "source_file": str(file_path) if file_path is not None else None,
        "source_manifest": str(source_manifest) if source_manifest is not None else None,
        "source": {
            "mode": source_mode,
            "preset": preset,
            "asset_dir": str(asset_dir) if asset_dir is not None else None,
            "file": str(file_path) if file_path is not None else None,
            "source_manifest": str(source_manifest) if source_manifest is not None else None,
            "date_col": date_col,
            "symbol_col": symbol_col,
        },
        "output_root": str(output_dir),
        "output_glob": str((output_data_dir / "**" / "*.parquet").resolve()),
        "partitioning": {"columns": ["trade_year"]},
        "columns": list(column_dtypes.keys()),
        "column_dtypes": dict(column_dtypes),
        "totals": {
            "input_files": stats.input_files,
            "input_rows": stats.input_rows,
            "output_rows": stats.output_rows,
            "output_files": stats.output_files,
            "symbols": stats.symbols,
            "trade_dates": stats.trade_dates,
            "trade_date_min": stats.trade_date_min,
            "trade_date_max": stats.trade_date_max,
        },
        "quality": {
            "rows_missing_date_dropped": stats.rows_missing_date_dropped,
            "rows_missing_symbol_dropped": stats.rows_missing_symbol_dropped,
            "duplicate_rows_dropped": stats.duplicate_rows_dropped,
        },
        "git": _git_metadata(Path.cwd().resolve()),
    }


def _materialize_column_defaults(args) -> tuple[str, str, str]:
    preset = str(getattr(args, "preset", "generic") or "generic").strip().lower()
    if preset not in PRESET_DEFAULTS:
        raise SystemExit(f"Unsupported preset: {preset}")
    defaults = PRESET_DEFAULTS[preset]
    dataset = str(getattr(args, "dataset_name", None) or defaults["dataset"]).strip()
    date_col = str(getattr(args, "date_col", None) or defaults["date_col"]).strip()
    symbol_col = str(getattr(args, "symbol_col", None) or defaults["symbol_col"]).strip()
    if not dataset:
        raise SystemExit("dataset-name must not be empty.")
    return dataset, date_col, symbol_col


@dataclass
class _MaterializeAccumulator:
    input_files: int
    input_rows: int = 0
    output_rows: int = 0
    output_files: int = 0
    symbols_seen: set[str] = field(default_factory=set)
    trade_date_keys: set[str] = field(default_factory=set)
    date_min: str | None = None
    date_max: str | None = None
    rows_missing_date_dropped: int = 0
    rows_missing_symbol_dropped: int = 0
    duplicate_rows_dropped: int = 0
    column_dtypes: dict[str, str] = field(default_factory=dict)

    def to_stats(self) -> MaterializeStats:
        return MaterializeStats(
            input_files=self.input_files,
            input_rows=self.input_rows,
            output_rows=self.output_rows,
            output_files=self.output_files,
            symbols=len(self.symbols_seen),
            trade_dates=len(self.trade_date_keys),
            trade_date_min=self.date_min,
            trade_date_max=self.date_max,
            rows_missing_date_dropped=self.rows_missing_date_dropped,
            rows_missing_symbol_dropped=self.rows_missing_symbol_dropped,
            duplicate_rows_dropped=self.duplicate_rows_dropped,
        )


def _record_normalized_materialize_output(
    accumulator: _MaterializeAccumulator,
    normalized: pd.DataFrame,
    *,
    output_data_dir: Path,
    part_index: int,
) -> None:
    if not accumulator.column_dtypes:
        accumulator.column_dtypes = {
            column: str(dtype)
            for column, dtype in normalized.drop(columns=["trade_year"]).dtypes.items()
        }

    accumulator.output_rows += int(len(normalized))
    accumulator.symbols_seen.update(normalized["symbol"].astype(str).unique().tolist())
    accumulator.trade_date_keys.update(normalized["trade_date_key"].astype(str).unique().tolist())
    file_date_min = normalized["trade_date_key"].min()
    file_date_max = normalized["trade_date_key"].max()
    if file_date_min is not None and (
        accumulator.date_min is None or file_date_min < accumulator.date_min
    ):
        accumulator.date_min = str(file_date_min)
    if file_date_max is not None and (
        accumulator.date_max is None or file_date_max > accumulator.date_max
    ):
        accumulator.date_max = str(file_date_max)
    accumulator.output_files += _write_partitioned_parquet(
        normalized,
        output_data_dir=output_data_dir,
        part_index=part_index,
    )


def _write_empty_materialized_output(
    accumulator: _MaterializeAccumulator,
    *,
    output_data_dir: Path,
) -> None:
    empty_frame = pd.DataFrame(
        columns=pd.Index(["trade_date", "trade_date_key", "symbol", "_source_file"])
    )
    accumulator.column_dtypes = {column: str(dtype) for column, dtype in empty_frame.dtypes.items()}
    _write_partitioned_parquet(
        empty_frame.assign(trade_year="empty"),
        output_data_dir=output_data_dir,
        part_index=0,
    )
    accumulator.output_files = 1


def _materialize_input_files(
    input_files: list[Path],
    *,
    output_data_dir: Path,
    date_col: str,
    symbol_col: str,
    frequency: str,
) -> _MaterializeAccumulator:
    accumulator = _MaterializeAccumulator(input_files=len(input_files))
    for index, input_path in enumerate(input_files):
        frame = _read_table(input_path)
        accumulator.input_rows += int(len(frame))
        normalized, quality = _normalize_frame(
            frame,
            source_path=input_path,
            date_col=date_col,
            symbol_col=symbol_col,
            frequency=frequency,
        )
        accumulator.rows_missing_date_dropped += quality["rows_missing_date_dropped"]
        accumulator.rows_missing_symbol_dropped += quality["rows_missing_symbol_dropped"]
        accumulator.duplicate_rows_dropped += quality["duplicate_rows_dropped"]
        if normalized.empty:
            continue
        _record_normalized_materialize_output(
            accumulator,
            normalized,
            output_data_dir=output_data_dir,
            part_index=accumulator.output_files + index,
        )
    if not accumulator.column_dtypes:
        _write_empty_materialized_output(accumulator, output_data_dir=output_data_dir)
    return accumulator
