#!/usr/bin/env python3
"""Read-only overlap audit for canonical Guan and local TuShare minute bars."""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from market_data_platform.providers.a_share_minute_fusion import (
    CANONICAL_MINUTE_COLUMNS,
    MINUTE_KEY_COLUMNS,
    normalize_tushare_partition,
)

SCHEMA_VERSION = "a_share.minute_overlap_audit.v1"
_PARTITION_PATTERN = re.compile(r"trade_date=(\d{8})$")
_BATCH_PATTERN = re.compile(r"minute_(\d{8})_batch\d+\.parquet$", re.IGNORECASE)
_ALIGNMENTS = {
    "direct": 0,
    "guan_minus_1m": -1,
    "guan_plus_1m": 1,
}


class MinuteOverlapAuditError(RuntimeError):
    """Raised for invalid audit inputs or source partitions."""


@dataclass
class _ErrorAccumulator:
    count: int = 0
    exact_count: int = 0
    absolute_error_sum: float = 0.0
    absolute_error_max: float = 0.0
    relative_count: int = 0
    relative_error_sum: float = 0.0
    relative_error_max: float = 0.0
    zero_reference_count: int = 0

    def update(self, stats: dict[str, Any]) -> None:
        self.count += int(stats["count"])
        self.exact_count += int(stats["exact_count"])
        self.absolute_error_sum += float(stats["absolute_error_sum"])
        self.absolute_error_max = max(self.absolute_error_max, float(stats["max_abs_error"] or 0))
        self.relative_count += int(stats["relative_count"])
        self.relative_error_sum += float(stats["relative_error_sum"])
        self.relative_error_max = max(
            self.relative_error_max,
            float(stats["max_abs_relative_error"] or 0),
        )
        self.zero_reference_count += int(stats["zero_reference_count"])

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "exact_count": self.exact_count,
            "exact_rate": self.exact_count / self.count if self.count else None,
            "mean_abs_error": (self.absolute_error_sum / self.count if self.count else None),
            "max_abs_error": self.absolute_error_max if self.count else None,
            "relative_count": self.relative_count,
            "mean_abs_relative_error": (
                self.relative_error_sum / self.relative_count if self.relative_count else None
            ),
            "max_abs_relative_error": (self.relative_error_max if self.relative_count else None),
            "zero_reference_count": self.zero_reference_count,
        }


@dataclass
class _AlignmentAccumulator:
    common_keys: int = 0
    common_symbol_days: int = 0
    close: _ErrorAccumulator | None = None
    vol: _ErrorAccumulator | None = None

    def __post_init__(self) -> None:
        if self.close is None:
            self.close = _ErrorAccumulator()
        if self.vol is None:
            self.vol = _ErrorAccumulator()

    def update(self, stats: dict[str, Any]) -> None:
        self.common_keys += int(stats["common_keys"])
        self.common_symbol_days += int(stats["common_symbols"])
        assert self.close is not None
        assert self.vol is not None
        self.close.update(stats["close_error"])
        self.vol.update(stats["vol_error"])

    def as_dict(self) -> dict[str, Any]:
        assert self.close is not None
        assert self.vol is not None
        return {
            "common_keys": self.common_keys,
            "common_symbol_days": self.common_symbol_days,
            "close_error": self.close.as_dict(),
            "vol_error": self.vol.as_dict(),
        }


def _validate_date(value: str | None, *, name: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not re.fullmatch(r"\d{8}", text):
        raise ValueError(f"{name} must use YYYYMMDD, got {value!r}")
    pd.to_datetime(text, format="%Y%m%d", errors="raise")
    return text


def _file_stat(path: Path) -> dict[str, Any]:
    value = path.stat()
    return {
        "path": str(path),
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
    }


def _discover_canonical(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not root.is_dir():
        raise MinuteOverlapAuditError(f"Canonical directory not found: {root}")
    for child in root.iterdir():
        matched = _PARTITION_PATTERN.fullmatch(child.name)
        path = child / "part-00000.parquet"
        if matched is not None and child.is_dir() and path.is_file():
            result[matched.group(1)] = path
    return dict(sorted(result.items()))


def _discover_batches(root: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    if not root.is_dir():
        raise MinuteOverlapAuditError(f"TuShare batch directory not found: {root}")
    for path in sorted(root.glob("minute_*_batch*.parquet")):
        matched = _BATCH_PATTERN.fullmatch(path.name)
        if matched is not None:
            result.setdefault(matched.group(1), []).append(path)
    return result


def discover_overlap_dates(
    canonical_dir: str | Path,
    tushare_batch_dir: str | Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    exclude_dates: Sequence[str] = (),
) -> list[str]:
    """Return dates available from both sources inside the requested range."""
    start = _validate_date(start_date, name="start_date")
    end = _validate_date(end_date, name="end_date")
    if start is not None and end is not None and start > end:
        raise ValueError("start_date must not be after end_date")
    canonical = _discover_canonical(Path(canonical_dir).expanduser())
    batches = _discover_batches(Path(tushare_batch_dir).expanduser())
    excluded = _validated_exclude_dates(exclude_dates)
    dates = sorted(set(canonical) & set(batches))
    ranged_dates = [
        date for date in dates if (start is None or date >= start) and (end is None or date <= end)
    ]
    unexpected_exclusions = excluded.difference(ranged_dates)
    if unexpected_exclusions:
        raise MinuteOverlapAuditError(
            "Excluded dates are not present in the in-range source overlap: "
            f"{sorted(unexpected_exclusions)}"
        )
    return [date for date in ranged_dates if date not in excluded]


def _validated_exclude_dates(values: Sequence[str]) -> set[str]:
    dates = [_validate_date(value, name="exclude_date") for value in values]
    normalized = [date for date in dates if date is not None]
    if len(normalized) != len(set(normalized)):
        raise ValueError("exclude_dates must not contain duplicates")
    return set(normalized)


def _load_canonical(path: Path, trade_date: str) -> pd.DataFrame:
    frame = normalize_tushare_partition(path)
    _validate_source_frame(frame, trade_date, label=f"canonical Guan {path}")
    return frame


def _load_tushare(paths: Sequence[Path], trade_date: str) -> pd.DataFrame:
    if not paths:
        raise MinuteOverlapAuditError(f"No TuShare batches for {trade_date}")
    frames = [pq.ParquetFile(path).read().to_pandas() for path in paths]
    frame = normalize_tushare_partition(pd.concat(frames, ignore_index=True))
    _validate_source_frame(frame, trade_date, label=f"TuShare batches for {trade_date}")
    return frame


def _validate_source_frame(frame: pd.DataFrame, trade_date: str, *, label: str) -> None:
    if frame.empty:
        raise MinuteOverlapAuditError(f"{label} is empty")
    if frame.duplicated(list(MINUTE_KEY_COLUMNS)).any():
        raise MinuteOverlapAuditError(f"{label} contains duplicate minute keys")
    if frame.loc[:, list(CANONICAL_MINUTE_COLUMNS)].isna().any(axis=None):
        raise MinuteOverlapAuditError(f"{label} contains null canonical values")
    numeric = frame.loc[:, list(CANONICAL_MINUTE_COLUMNS[2:])].to_numpy(dtype="float64")
    if not np.isfinite(numeric).all():
        raise MinuteOverlapAuditError(f"{label} contains non-finite canonical values")
    times = pd.to_datetime(frame["trade_time"], errors="coerce")
    invalid = times.isna() | times.dt.strftime("%Y%m%d").ne(trade_date)
    if invalid.any():
        raise MinuteOverlapAuditError(f"{label} contains invalid or wrong-date timestamps")


def _time_grid(frame: pd.DataFrame) -> list[str]:
    times = pd.to_datetime(frame["trade_time"], errors="raise")
    return sorted(times.dt.strftime("%H:%M:%S").unique().tolist())


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    array = np.asarray(values, dtype="float64")
    finite = array[np.isfinite(array)]
    if finite.size == 0:
        return {
            "count": int(array.size),
            "finite_count": 0,
            "min": None,
            "p01": None,
            "p10": None,
            "median": None,
            "mean": None,
            "p90": None,
            "p99": None,
            "max": None,
        }
    quantiles = np.quantile(finite, [0.01, 0.10, 0.50, 0.90, 0.99])
    return {
        "count": int(array.size),
        "finite_count": int(finite.size),
        "min": float(finite.min()),
        "p01": float(quantiles[0]),
        "p10": float(quantiles[1]),
        "median": float(quantiles[2]),
        "mean": float(finite.mean()),
        "p90": float(quantiles[3]),
        "p99": float(quantiles[4]),
        "max": float(finite.max()),
    }


def _ratio(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0:
        return float("nan")
    return numerator / denominator


def _security_day_totals(
    guan: pd.DataFrame,
    tushare: pd.DataFrame,
    symbols: list[str],
) -> tuple[list[dict[str, Any]], list[float], list[float]]:
    columns = ["vol", "amount"]
    guan_totals = guan.groupby("ts_code", observed=True)[columns].sum()
    tushare_totals = tushare.groupby("ts_code", observed=True)[columns].sum()
    records: list[dict[str, Any]] = []
    vol_ratios: list[float] = []
    amount_ratios: list[float] = []
    for symbol in symbols:
        guan_vol = float(guan_totals.at[symbol, "vol"])
        guan_amount = float(guan_totals.at[symbol, "amount"])
        tushare_vol = float(tushare_totals.at[symbol, "vol"])
        tushare_amount = float(tushare_totals.at[symbol, "amount"])
        vol_ratio = _ratio(guan_vol, tushare_vol)
        amount_ratio = _ratio(guan_amount, tushare_amount)
        vol_ratios.append(vol_ratio)
        amount_ratios.append(amount_ratio)
        records.append(
            {
                "ts_code": symbol,
                "guan_vol": guan_vol,
                "tushare_vol": tushare_vol,
                "guan_over_tushare_vol_ratio": (vol_ratio if np.isfinite(vol_ratio) else None),
                "guan_amount": guan_amount,
                "tushare_amount": tushare_amount,
                "guan_over_tushare_amount_ratio": (
                    amount_ratio if np.isfinite(amount_ratio) else None
                ),
            }
        )
    return records, vol_ratios, amount_ratios


def _error_stats(candidate: np.ndarray, reference: np.ndarray) -> dict[str, Any]:
    candidate = np.asarray(candidate, dtype="float64")
    reference = np.asarray(reference, dtype="float64")
    absolute = np.abs(candidate - reference)
    exact = np.isclose(candidate, reference, rtol=0.0, atol=1e-12)
    valid_reference = np.abs(reference) > 0
    relative = absolute[valid_reference] / np.abs(reference[valid_reference])
    if absolute.size:
        absolute_quantiles = np.quantile(absolute, [0.50, 0.90, 0.99])
    else:
        absolute_quantiles = [np.nan, np.nan, np.nan]
    if relative.size:
        relative_quantiles = np.quantile(relative, [0.50, 0.90, 0.99])
    else:
        relative_quantiles = [np.nan, np.nan, np.nan]
    return {
        "count": int(absolute.size),
        "exact_count": int(exact.sum()),
        "exact_rate": float(exact.mean()) if exact.size else None,
        "absolute_error_sum": float(absolute.sum()),
        "mean_abs_error": float(absolute.mean()) if absolute.size else None,
        "median_abs_error": float(absolute_quantiles[0]) if absolute.size else None,
        "p90_abs_error": float(absolute_quantiles[1]) if absolute.size else None,
        "p99_abs_error": float(absolute_quantiles[2]) if absolute.size else None,
        "max_abs_error": float(absolute.max()) if absolute.size else None,
        "relative_count": int(relative.size),
        "relative_error_sum": float(relative.sum()),
        "mean_abs_relative_error": float(relative.mean()) if relative.size else None,
        "median_abs_relative_error": (float(relative_quantiles[0]) if relative.size else None),
        "p90_abs_relative_error": float(relative_quantiles[1]) if relative.size else None,
        "p99_abs_relative_error": float(relative_quantiles[2]) if relative.size else None,
        "max_abs_relative_error": float(relative.max()) if relative.size else None,
        "zero_reference_count": int((~valid_reference).sum()),
    }


def _alignment_frame(
    guan: pd.DataFrame,
    tushare: pd.DataFrame,
    *,
    shift_minutes: int,
) -> pd.DataFrame:
    guan_side = guan.loc[:, ["ts_code", "trade_time", "close", "vol"]].rename(
        columns={"close": "guan_close", "vol": "guan_vol"}
    )
    guan_side["aligned_time"] = pd.to_datetime(guan_side["trade_time"]) + pd.Timedelta(
        minutes=shift_minutes
    )
    tushare_side = tushare.loc[:, ["ts_code", "trade_time", "close", "vol"]].rename(
        columns={
            "trade_time": "aligned_time",
            "close": "tushare_close",
            "vol": "tushare_vol",
        }
    )
    return guan_side.merge(
        tushare_side,
        on=["ts_code", "aligned_time"],
        how="inner",
        validate="one_to_one",
    )


def _alignment_stats(frame: pd.DataFrame, *, shift_minutes: int) -> dict[str, Any]:
    return {
        "guan_shift_minutes": shift_minutes,
        "common_keys": len(frame),
        "common_symbols": int(frame["ts_code"].nunique()),
        "close_error": _error_stats(
            frame["guan_close"].to_numpy(),
            frame["tushare_close"].to_numpy(),
        ),
        "vol_error": _error_stats(
            frame["guan_vol"].to_numpy(),
            frame["tushare_vol"].to_numpy(),
        ),
    }


def _best_alignment_by_symbol(
    aligned: dict[str, pd.DataFrame],
    symbols: list[str],
) -> dict[str, int]:
    winners: Counter[str] = Counter()
    for symbol in symbols:
        scores: dict[str, float] = {}
        for name, frame in aligned.items():
            selected = frame.loc[frame["ts_code"].eq(symbol)]
            if selected.empty:
                continue
            scores[name] = float(np.abs(selected["guan_close"] - selected["tushare_close"]).mean())
        if not scores:
            winners["insufficient"] += 1
            continue
        minimum = min(scores.values())
        best = [name for name, value in scores.items() if np.isclose(value, minimum)]
        winners[best[0] if len(best) == 1 else "tie"] += 1
    return {name: winners.get(name, 0) for name in (*_ALIGNMENTS, "tie", "insufficient")}


def _audit_one_date(
    trade_date: str,
    canonical_path: Path,
    batch_paths: Sequence[Path],
) -> tuple[dict[str, Any], list[float], list[float]]:
    guan_all = _load_canonical(canonical_path, trade_date)
    tushare_all = _load_tushare(batch_paths, trade_date)
    common_symbols = sorted(set(guan_all["ts_code"]) & set(tushare_all["ts_code"]))
    if not common_symbols:
        raise MinuteOverlapAuditError(f"No common symbols for overlap date {trade_date}")
    guan = guan_all.loc[guan_all["ts_code"].isin(common_symbols)].copy()
    tushare = tushare_all.loc[tushare_all["ts_code"].isin(common_symbols)].copy()
    security_days, vol_ratios, amount_ratios = _security_day_totals(
        guan,
        tushare,
        common_symbols,
    )
    aligned = {
        name: _alignment_frame(guan, tushare, shift_minutes=shift)
        for name, shift in _ALIGNMENTS.items()
    }
    alignment_stats = {
        name: _alignment_stats(frame, shift_minutes=_ALIGNMENTS[name])
        for name, frame in aligned.items()
    }
    guan_grid = _time_grid(guan)
    tushare_grid = _time_grid(tushare)
    record = {
        "trade_date": trade_date,
        "inputs": {
            "canonical": _file_stat(canonical_path),
            "tushare_batches": [_file_stat(path) for path in batch_paths],
        },
        "sources": {
            "guan": {
                "total_rows": len(guan_all),
                "total_symbols": int(guan_all["ts_code"].nunique()),
                "compared_rows": len(guan),
                "compared_symbols": len(common_symbols),
                "time_grid": guan_grid,
            },
            "tushare": {
                "total_rows": len(tushare_all),
                "total_symbols": int(tushare_all["ts_code"].nunique()),
                "compared_rows": len(tushare),
                "compared_symbols": len(common_symbols),
                "time_grid": tushare_grid,
            },
            "common_symbols": common_symbols,
            "time_grid_comparison": {
                "common": sorted(set(guan_grid) & set(tushare_grid)),
                "guan_only": sorted(set(guan_grid) - set(tushare_grid)),
                "tushare_only": sorted(set(tushare_grid) - set(guan_grid)),
            },
        },
        "security_day_totals": security_days,
        "security_day_ratio_distribution": {
            "guan_over_tushare_vol": _distribution(vol_ratios),
            "guan_over_tushare_amount": _distribution(amount_ratios),
        },
        "alignments": alignment_stats,
        "best_alignment_by_symbol_close_mae": _best_alignment_by_symbol(
            aligned,
            common_symbols,
        ),
    }
    del guan_all, tushare_all, guan, tushare, aligned
    gc.collect()
    return record, vol_ratios, amount_ratios


def _atomic_write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validate_output_location(output_path: Path, source_roots: Sequence[Path]) -> None:
    resolved_output = output_path.resolve()
    if any(resolved_output.is_relative_to(root.resolve()) for root in source_roots):
        raise MinuteOverlapAuditError(
            "output_json must be outside both read-only source directories"
        )


def audit_minute_overlap(  # noqa: PLR0913
    canonical_dir: str | Path,
    tushare_batch_dir: str | Path,
    output_json: str | Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    exclude_dates: Sequence[str] = (),
) -> dict[str, Any]:
    """Audit all overlapping dates without modifying either source dataset."""
    start = _validate_date(start_date, name="start_date")
    end = _validate_date(end_date, name="end_date")
    if start is not None and end is not None and start > end:
        raise ValueError("start_date must not be after end_date")
    canonical_root = Path(canonical_dir).expanduser()
    batch_root = Path(tushare_batch_dir).expanduser()
    output_path = Path(output_json).expanduser()
    _validate_output_location(output_path, (canonical_root, batch_root))
    canonical = _discover_canonical(canonical_root)
    batches = _discover_batches(batch_root)
    overlap_dates = discover_overlap_dates(
        canonical_root,
        batch_root,
        start_date=start,
        end_date=end,
        exclude_dates=exclude_dates,
    )
    if not overlap_dates:
        raise MinuteOverlapAuditError("No overlapping dates found for the requested range")

    daily: list[dict[str, Any]] = []
    all_vol_ratios: list[float] = []
    all_amount_ratios: list[float] = []
    alignment_totals = {name: _AlignmentAccumulator() for name in _ALIGNMENTS}
    best_alignment_totals: Counter[str] = Counter()
    for trade_date in overlap_dates:
        record, vol_ratios, amount_ratios = _audit_one_date(
            trade_date,
            canonical[trade_date],
            batches[trade_date],
        )
        daily.append(record)
        all_vol_ratios.extend(vol_ratios)
        all_amount_ratios.extend(amount_ratios)
        for name, stats in record["alignments"].items():
            alignment_totals[name].update(stats)
        best_alignment_totals.update(record["best_alignment_by_symbol_close_mae"])

    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "generated_at": datetime.now(UTC).isoformat(),
        "diagnostic_only": True,
        "mutation_performed": False,
        "interpretation": {
            "ratio_direction": "guan_over_tushare",
            "reference_for_errors": "tushare",
            "alignment_candidates": {
                name: {"guan_shift_minutes": shift} for name, shift in _ALIGNMENTS.items()
            },
            "warning": (
                "Alignment statistics are diagnostic only. They do not establish a universal "
                "minute shift and must not be used to rewrite either source or canonical data."
            ),
        },
        "inputs": {
            "canonical_dir": str(canonical_root),
            "tushare_batch_dir": str(batch_root),
            "start_date": start,
            "end_date": end,
            "excluded_overlap_dates": sorted(_validated_exclude_dates(exclude_dates)),
            "canonical_dates": len(canonical),
            "tushare_dates": len(batches),
            "overlap_dates": overlap_dates,
        },
        "summary": {
            "date_count": len(daily),
            "date_min": overlap_dates[0],
            "date_max": overlap_dates[-1],
            "security_day_ratio_distribution": {
                "guan_over_tushare_vol": _distribution(all_vol_ratios),
                "guan_over_tushare_amount": _distribution(all_amount_ratios),
            },
            "alignments": {
                name: accumulator.as_dict() for name, accumulator in alignment_totals.items()
            },
            "best_alignment_by_symbol_day_close_mae": {
                name: best_alignment_totals.get(name, 0)
                for name in (*_ALIGNMENTS, "tie", "insufficient")
            },
        },
        "daily": daily,
    }
    _atomic_write_json(payload, output_path)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Guan/TuShare minute overlap and alignment audit.",
    )
    parser.add_argument("--canonical-dir", required=True)
    parser.add_argument("--tushare-batch-dir", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument(
        "--exclude-date",
        action="append",
        default=[],
        help="Exclude one known non-Guan overlap date; repeat once per date.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = audit_minute_overlap(
        args.canonical_dir,
        args.tushare_batch_dir,
        args.output_json,
        start_date=args.start_date,
        end_date=args.end_date,
        exclude_dates=args.exclude_date,
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
