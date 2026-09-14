"""Build and validate the source-neutral A-share one-minute dataset."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from market_data_platform.standardize.fusion.a_share_minute import (
    CANONICAL_MINUTE_SCHEMA,
    MinuteAggregationEngine,
)

DEFAULT_FUSED_MINUTE_SUBDIR = Path("assets") / "derived" / "a_share" / "minute_1m"

DEFAULT_FUSED_MINUTE_MANIFEST_SUBPATH = (
    Path("metadata") / "minute_fusion" / "a_share_minute_1m.manifest.json"
)

_PARTITION_PATTERN = re.compile(r"trade_date=(\d{8})$")

_DEAL_PATTERN = re.compile(r"deal_(\d{8})\.parquet$", re.IGNORECASE)

_TUSHARE_BATCH_PATTERN = re.compile(
    r"minute_(\d{8})_batch\d+\.parquet$",
    re.IGNORECASE,
)

_TS_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")

_DEAL_CHECKPOINT_SCHEMA_VERSION = "a_share.minute_1m.deal_checkpoint.v1"

_DEAL_TRANSFORM_CONTRACT_VERSION = "guan.deal_minute.transform.v2"


def _file_inventory(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


@dataclass(frozen=True)
class MinuteFusionBuildOptions:
    """Inputs and audited unit rules for one resumable fusion build."""

    legacy_input_dir: str | Path
    output_dir: str | Path
    manifest_path: str | Path
    start_date: str = "20160101"
    end_date: str = "20991231"
    legacy_guan_end_date: str = "20260424"
    hundred_x_start_date: str = "20260101"
    hundred_x_end_date: str = "20260424"
    guan_deal_dir: str | Path | None = None
    guan_deal_start_date: str = "20260601"
    tushare_batch_dir: str | Path | None = None
    instruments_path: str | Path | None = None
    resume: bool = True
    dry_run: bool = False
    legacy_workers: int = 1
    deal_engine: MinuteAggregationEngine = "auto"
    deal_batch_row_groups: int | None = None
    protected_dates: tuple[str, ...] = ()
    annual_override_dates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_build_date_options(self)
        _validate_build_runtime_options(self)
        _validate_build_override_options(self)


def _validate_date(value: str, *, name: str) -> None:
    if not re.fullmatch(r"\d{8}", str(value)):
        raise ValueError(f"{name} must use YYYYMMDD, got {value!r}")
    pd.to_datetime(str(value), format="%Y%m%d", errors="raise")


def _validate_build_date_options(options: MinuteFusionBuildOptions) -> None:
    for name in (
        "start_date",
        "end_date",
        "legacy_guan_end_date",
        "hundred_x_start_date",
        "hundred_x_end_date",
        "guan_deal_start_date",
    ):
        _validate_date(getattr(options, name), name=name)
    if options.start_date > options.end_date:
        raise ValueError("start_date must not be after end_date")
    if options.hundred_x_start_date > options.hundred_x_end_date:
        raise ValueError("hundred_x_start_date must not be after hundred_x_end_date")


def _validate_build_runtime_options(options: MinuteFusionBuildOptions) -> None:
    if options.legacy_workers < 1:
        raise ValueError("legacy_workers must be at least 1")
    if options.deal_engine not in {"auto", "pandas", "polars"}:
        raise ValueError(f"Unsupported deal_engine: {options.deal_engine!r}")
    if options.deal_batch_row_groups is not None and options.deal_batch_row_groups < 1:
        raise ValueError("deal_batch_row_groups must be at least 1 when set")


def _validate_build_override_options(options: MinuteFusionBuildOptions) -> None:
    for protected_date in options.protected_dates:
        _validate_date(protected_date, name="protected_date")
    if len(set(options.protected_dates)) != len(options.protected_dates):
        raise ValueError("protected_dates must not contain duplicates")
    for override_date in options.annual_override_dates:
        _validate_date(override_date, name="annual_override_date")
    if len(set(options.annual_override_dates)) != len(options.annual_override_dates):
        raise ValueError("annual_override_dates must not contain duplicates")
    outside_range = sorted(
        date
        for date in options.annual_override_dates
        if not options.start_date <= date <= options.end_date
    )
    if outside_range:
        raise ValueError(f"annual_override_dates must be inside the build range: {outside_range}")
    unprotected = sorted(set(options.annual_override_dates).difference(options.protected_dates))
    if unprotected:
        raise ValueError(
            f"annual_override_dates must identify protected annual dates: {unprotected}"
        )


def _in_range(date: str, options: MinuteFusionBuildOptions) -> bool:
    return options.start_date <= date <= options.end_date


def _expanded_path(value: str | Path) -> Path:
    return Path(value).expanduser()


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


def _deal_checkpoint_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(f".{manifest_path.name}.deal-checkpoint.json")


def _deal_checkpoint_contract(
    options: MinuteFusionBuildOptions,
    source_inventory: _MinuteSourceInventory,
    *,
    instruments_path: Path | None,
) -> dict[str, Any]:
    legacy_root = _expanded_path(options.legacy_input_dir)
    output_root = _expanded_path(options.output_dir)
    manifest_path = _expanded_path(options.manifest_path)
    return {
        "transform_contract_version": _DEAL_TRANSFORM_CONTRACT_VERSION,
        "start_date": options.start_date,
        "end_date": options.end_date,
        "guan_deal_start_date": options.guan_deal_start_date,
        "deal_engine": options.deal_engine,
        "deal_batch_row_groups": options.deal_batch_row_groups,
        "deal_dates": sorted(source_inventory.deal_sources),
        "annual_override_dates": sorted(options.annual_override_dates),
        "protected_dates": sorted(options.protected_dates),
        "legacy_input_dir": str(legacy_root),
        "output_dir": str(output_root),
        "manifest_path": str(manifest_path),
        "instruments": _file_inventory(instruments_path) if instruments_path is not None else None,
        "legacy_sources": {
            date: _file_inventory(path) for date, path in sorted(source_inventory.legacy.items())
        },
    }


def _output_checkpoint_signature(path: Path) -> dict[str, Any]:
    parquet_file = pq.ParquetFile(path)
    if not parquet_file.schema_arrow.equals(CANONICAL_MINUTE_SCHEMA):
        raise ValueError(f"Cannot checkpoint non-canonical deal output: {path}")
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "rows": int(parquet_file.metadata.num_rows),
        "sha256": _sha256_file(path),
    }


def _override_receipt_is_valid(action: Mapping[str, Any], date: str) -> bool:
    return (
        action.get("replacement_policy") == "explicit_whole_day_deal_only"
        and action.get("replaced_source") == "guan_annual_minbar"
        and isinstance(session := action.get("session_validation"), Mapping)
        and session.get("valid") is True
        and session.get("time_min") == f"{date[:4]}-{date[4:6]}-{date[6:]} 09:30:00"
        and session.get("time_max") == f"{date[:4]}-{date[4:6]}-{date[6:]} 15:00:00"
        and int(session.get("opening_bar_rows", 0)) >= 1
        and int(session.get("closing_bar_rows", 0)) >= 1
        and isinstance(issues := session.get("issues"), Mapping)
        and not any(int(value) for value in issues.values())
    )


def _checkpoint_action_metadata_is_current(
    action: Mapping[str, Any],
    *,
    date: str,
    source: Path,
    whole_day_override: bool,
) -> bool:
    return (
        action.get("date") == date
        and action.get("status") == "written"
        and action.get("source_signature") == _file_inventory(source)
        and whole_day_override == bool(action.get("whole_day_annual_override", False))
        and (not whole_day_override or _override_receipt_is_valid(action, date))
    )


def _checkpoint_output_is_current(
    aggregation: Mapping[str, Any],
    signature: Mapping[str, Any],
    output_path: Path,
) -> bool:
    if not output_path.is_file() or signature.get("path") != str(output_path):
        return False
    try:
        parquet_file = pq.ParquetFile(output_path)
        stat = output_path.stat()
    except (OSError, ValueError):
        return False
    if not parquet_file.schema_arrow.equals(CANONICAL_MINUTE_SCHEMA):
        return False
    rows = int(parquet_file.metadata.num_rows)
    return (
        rows == int(signature.get("rows", -1))
        and rows == int(aggregation.get("output_rows", -2))
        and stat.st_size == int(signature.get("size", -1))
        and stat.st_mtime_ns == int(signature.get("mtime_ns", -1))
        and isinstance(expected_sha256 := signature.get("sha256"), str)
        and _sha256_file(output_path) == expected_sha256
    )


def _checkpoint_action_is_current(
    action: Mapping[str, Any],
    *,
    date: str,
    source: Path,
    output_path: Path,
    whole_day_override: bool,
) -> bool:
    if not _checkpoint_action_metadata_is_current(
        action,
        date=date,
        source=source,
        whole_day_override=whole_day_override,
    ):
        return False
    aggregation = action.get("aggregation")
    signature = action.get("output_signature")
    if not isinstance(aggregation, Mapping) or not isinstance(signature, Mapping):
        return False
    return _checkpoint_output_is_current(aggregation, signature, output_path)


def _load_deal_checkpoint(
    path: Path,
    *,
    contract_fingerprint: str,
) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != _DEAL_CHECKPOINT_SCHEMA_VERSION
        or payload.get("contract_fingerprint") != contract_fingerprint
    ):
        return {}
    actions = payload.get("completed_actions")
    if not isinstance(actions, Mapping):
        return {}
    return {
        str(date): dict(action) for date, action in actions.items() if isinstance(action, Mapping)
    }


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
