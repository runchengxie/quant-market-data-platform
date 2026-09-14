"""Input discovery and source inventory for A-share minute materialization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from .options import MinuteFusionBuildOptions, _expanded_path, _in_range

_PARTITION_PATTERN = re.compile(r"trade_date=(\d{8})$")
_DEAL_PATTERN = re.compile(r"deal_(\d{8})\.parquet$", re.IGNORECASE)
_TUSHARE_BATCH_PATTERN = re.compile(
    r"minute_(\d{8})_batch\d+\.parquet$",
    re.IGNORECASE,
)


def _file_inventory(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


@dataclass(frozen=True)
class _MinuteSourceInventory:
    legacy: dict[str, Path]
    guan_deal: dict[str, Path]
    override_guan_deal: dict[str, Path]
    protected_guan_deal: dict[str, Path]
    protected_output_dates: set[str]
    tushare_batches: dict[str, list[Path]]
    legacy_self_output_excluded: bool = False

    @property
    def expected_dates(self) -> set[str]:
        return (
            set(self.legacy)
            | set(self.guan_deal)
            | set(self.override_guan_deal)
            | set(self.protected_guan_deal)
            | self.protected_output_dates
            | set(self.tushare_batches)
        )

    @property
    def deal_sources(self) -> dict[str, Path]:
        return dict(sorted({**self.guan_deal, **self.override_guan_deal}.items()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "expected_output_dates": sorted(self.expected_dates),
            "legacy": {
                "date_count": len(self.legacy),
                "self_output_excluded": self.legacy_self_output_excluded,
                "files": {date: _file_inventory(path) for date, path in self.legacy.items()},
            },
            "guan_deal": {
                "date_count": len(self.guan_deal),
                "files": {date: _file_inventory(path) for date, path in self.guan_deal.items()},
            },
            "override_guan_deal": {
                "date_count": len(self.override_guan_deal),
                "replacement_policy": "explicit_whole_day_deal_only",
                "files": {
                    date: _file_inventory(path) for date, path in self.override_guan_deal.items()
                },
            },
            "protected_guan_deal": {
                "date_count": len(self.protected_guan_deal),
                "files": {
                    date: _file_inventory(path) for date, path in self.protected_guan_deal.items()
                },
            },
            "protected_output_dates": {
                "date_count": len(self.protected_output_dates),
                "dates": sorted(self.protected_output_dates),
            },
            "tushare_batches": {
                "date_count": len(self.tushare_batches),
                "file_count": sum(len(paths) for paths in self.tushare_batches.values()),
                "files": {
                    date: [_file_inventory(path) for path in paths]
                    for date, paths in self.tushare_batches.items()
                },
            },
        }


def _require_input_dir(value: str | Path, *, name: str) -> Path:
    path = _expanded_path(value)
    if not path.exists():
        raise FileNotFoundError(f"{name} does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"{name} is not a directory: {path}")
    return path


def _require_input_file(value: str | Path, *, name: str) -> Path:
    path = _expanded_path(value)
    if not path.exists():
        raise FileNotFoundError(f"{name} does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"{name} is not a file: {path}")
    return path


def _partition_path(root: Path, trade_date: str) -> Path:
    return root / f"trade_date={trade_date}" / "part-00000.parquet"


def _discover_legacy_partitions(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not root.exists():
        return result
    for child in root.iterdir():
        matched = _PARTITION_PATTERN.fullmatch(child.name)
        if matched is None or not child.is_dir():
            continue
        part = child / "part-00000.parquet"
        if part.is_file():
            result[matched.group(1)] = part
    return dict(sorted(result.items()))


def _discover_deal_files(root: Path | None) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if root is None or not root.exists():
        return result
    for path in sorted(root.rglob("deal_*.parquet")):
        matched = _DEAL_PATTERN.fullmatch(path.name)
        if matched is not None:
            trade_date = matched.group(1)
            existing = result.get(trade_date)
            if existing is not None:
                raise ValueError(
                    "Duplicate Guan deal date below guan_deal_dir: "
                    f"{trade_date}: {existing}, {path}"
                )
            result[trade_date] = path
    return result


def _discover_tushare_batches(root: Path | None) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    if root is None or not root.exists():
        return result
    for path in sorted(root.glob("minute_*_batch*.parquet")):
        matched = _TUSHARE_BATCH_PATTERN.fullmatch(path.name)
        if matched is not None:
            result.setdefault(matched.group(1), []).append(path)
    return result


def _discover_source_inventory(
    options: MinuteFusionBuildOptions,
    *,
    legacy_root: Path,
    output_root: Path,
    guan_deal_root: Path | None,
    tushare_batch_root: Path | None,
) -> _MinuteSourceInventory:
    legacy_self_output_excluded = legacy_root.resolve() == output_root.resolve()
    legacy = (
        {}
        if legacy_self_output_excluded
        else {
            date: path
            for date, path in _discover_legacy_partitions(legacy_root).items()
            if _in_range(date, options)
        }
    )
    discovered_deal = {
        date: path
        for date, path in _discover_deal_files(guan_deal_root).items()
        if _in_range(date, options) and date >= options.guan_deal_start_date
    }
    override_dates = set(options.annual_override_dates)
    missing_override_sources = sorted(override_dates.difference(discovered_deal))
    if missing_override_sources:
        raise ValueError(
            "Explicit annual override dates have no in-range Guan deal file: "
            f"{missing_override_sources}"
        )
    protected_dates = set(options.protected_dates) | {
        date for date in legacy if date <= options.legacy_guan_end_date
    }
    protected_output_dates = {
        date
        for date in protected_dates
        if options.start_date <= date <= options.end_date and date not in override_dates
    }
    protected_guan_deal = {
        date: path
        for date, path in discovered_deal.items()
        if date in protected_dates and date not in override_dates
    }
    override_guan_deal = {
        date: path for date, path in discovered_deal.items() if date in override_dates
    }
    guan_deal = {
        date: path for date, path in discovered_deal.items() if date not in protected_dates
    }
    tushare_batches = {
        date: paths
        for date, paths in _discover_tushare_batches(tushare_batch_root).items()
        if _in_range(date, options)
    }
    return _MinuteSourceInventory(
        legacy=legacy,
        guan_deal=guan_deal,
        override_guan_deal=override_guan_deal,
        protected_guan_deal=protected_guan_deal,
        protected_output_dates=protected_output_dates,
        tushare_batches=tushare_batches,
        legacy_self_output_excluded=legacy_self_output_excluded,
    )


def _load_symbol_mapping(path: Path | None) -> tuple[dict[str, str], dict[str, Any]]:
    if path is None:
        return {}, {
            "source_path": None,
            "input_rows": 0,
            "dropped_invalid_rows": 0,
            "mapping_entries": 0,
        }
    parquet_file = pq.ParquetFile(path)
    required = {"symbol", "ts_code"}
    missing = required.difference(parquet_file.schema_arrow.names)
    if missing:
        raise ValueError(f"Instrument asset {path} is missing columns: {sorted(missing)}")
    frame = parquet_file.read(columns=["symbol", "ts_code"]).to_pandas()
    frame = frame.dropna(subset=["symbol", "ts_code"])
    input_rows = len(frame)
    frame["symbol"] = frame["symbol"].astype("string").str.strip().str.split(".", n=1).str[0]
    frame["ts_code"] = frame["ts_code"].astype("string").str.strip().str.upper()
    valid = frame["symbol"].str.fullmatch(r"\d{6}", na=False) & frame["ts_code"].str.fullmatch(
        r"\d{6}\.(?:SH|SZ|BJ)", na=False
    )
    frame = frame.loc[valid].drop_duplicates().copy()
    conflicts = frame.groupby("symbol", observed=True)["ts_code"].nunique().gt(1)
    if conflicts.any():
        examples = conflicts.index[conflicts].astype(str).tolist()[:3]
        raise ValueError(f"Instrument asset {path} has conflicting mappings: {examples}")
    mapping = dict(zip(frame["symbol"].astype(str), frame["ts_code"].astype(str), strict=False))
    return mapping, {
        "source_path": str(path),
        "input_rows": input_rows,
        "dropped_invalid_rows": input_rows - len(frame),
        "mapping_entries": len(mapping),
    }
