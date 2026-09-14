from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from market_data_platform.providers.tushare_a_share_universe_part01 import (
    AShareUniverseBuildOptions,
    _build_a_share_universe,
    _resolve_universe_build_options,
    _resolved_path,
    _UniverseValidationFrame,
    _validate_date,
)

from .tushare_common import normalize_ts_code


def build_a_share_universe(
    options: AShareUniverseBuildOptions | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """Build an A-share universe, accepting legacy keyword options for compatibility."""
    return _build_a_share_universe(_resolve_universe_build_options(options, legacy_options))


def _validate_universe_by_date_frame(by_date_path: Path) -> _UniverseValidationFrame:
    errors: list[str] = []
    universe = pd.read_csv(by_date_path)
    required = {"trade_date", "symbol", "liq_metric", "selected"}
    missing = sorted(required.difference(universe.columns))
    if missing:
        errors.append(f"universe_by_date is missing columns: {missing}")
        return _UniverseValidationFrame(
            universe=universe,
            errors=errors,
            missing_columns=missing,
            rows=int(len(universe)),
            symbols=0,
            rebalance_dates=0,
            duplicate_rows=0,
            actual_as_of=None,
        )

    universe["trade_date"] = universe["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    universe["symbol"] = universe["symbol"].map(normalize_ts_code)
    rows = int(len(universe))
    duplicate_rows = int(universe.duplicated(subset=["trade_date", "symbol"]).sum())
    if duplicate_rows:
        errors.append(f"universe_by_date has duplicate trade_date/symbol rows: {duplicate_rows}")
    return _UniverseValidationFrame(
        universe=universe,
        errors=errors,
        missing_columns=[],
        rows=rows,
        symbols=int(universe["symbol"].nunique()),
        rebalance_dates=int(universe["trade_date"].nunique()),
        duplicate_rows=duplicate_rows,
        actual_as_of=str(universe["trade_date"].max()) if rows else None,
    )


def _validate_latest_symbols(
    latest_path: Path,
    validation: _UniverseValidationFrame,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    latest_symbols = [
        normalize_ts_code(line)
        for line in latest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(latest_symbols) != len(set(latest_symbols)):
        errors.append("latest_symbols_file contains duplicate symbols")
    if not validation.missing_columns and validation.actual_as_of:
        expected_latest = set(
            validation.universe.loc[
                validation.universe["trade_date"] == validation.actual_as_of,
                "symbol",
            ]
            .astype(str)
            .tolist()
        )
        if set(latest_symbols) != expected_latest:
            errors.append(
                "latest_symbols_file does not match the latest universe_by_date partition"
            )
    return latest_symbols, errors


def _validate_universe_thresholds(
    validation: _UniverseValidationFrame,
    *,
    latest_symbol_count: int,
    min_rows: int,
    min_symbols: int,
    min_rebalance_dates: int,
) -> list[str]:
    errors: list[str] = []
    if validation.rows < min_rows:
        errors.append(f"rows={validation.rows} is below min_rows={min_rows}")
    if latest_symbol_count < min_symbols:
        errors.append(f"latest_symbols={latest_symbol_count} is below min_symbols={min_symbols}")
    if validation.rebalance_dates < min_rebalance_dates:
        errors.append(
            "rebalance_dates="
            f"{validation.rebalance_dates} is below min_rebalance_dates={min_rebalance_dates}"
        )
    return errors


def _validate_universe_meta(meta_path: Path, *, actual_as_of: str | None) -> list[str]:
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        return ["meta_file is not a mapping"]
    build = meta.get("build")
    if not isinstance(build, dict) or str(build.get("last_rebalance_date")) != str(actual_as_of):
        return ["meta_file last_rebalance_date does not match universe_by_date"]
    return []


def _write_universe_validation_report(payload: dict[str, Any], out: str | Path | None) -> None:
    if out is None:
        return
    output = Path(out).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_a_share_universe(  # noqa: PLR0913
    *,
    by_date_file: str | Path,
    latest_symbols_file: str | Path,
    meta_file: str | Path,
    expected_as_of: str | None = None,
    min_rows: int = 1,
    min_symbols: int = 1,
    min_rebalance_dates: int = 1,
    out: str | Path | None = None,
) -> dict[str, Any]:
    by_date_path = _resolved_path(by_date_file)
    latest_path = _resolved_path(latest_symbols_file)
    meta_path = _resolved_path(meta_file)
    validation = _validate_universe_by_date_frame(by_date_path)
    latest_symbols, latest_errors = _validate_latest_symbols(latest_path, validation)
    errors = [
        *validation.errors,
        *latest_errors,
        *_validate_universe_thresholds(
            validation,
            latest_symbol_count=len(latest_symbols),
            min_rows=min_rows,
            min_symbols=min_symbols,
            min_rebalance_dates=min_rebalance_dates,
        ),
    ]
    if expected_as_of is not None:
        expected = _validate_date(expected_as_of, label="expected-as-of")
        if validation.actual_as_of != expected:
            errors.append(
                f"actual_as_of={validation.actual_as_of} does not match expected_as_of={expected}"
            )
    errors.extend(_validate_universe_meta(meta_path, actual_as_of=validation.actual_as_of))
    payload = {
        "dataset": "universe",
        "market": "a_share",
        "provider": "tushare",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "totals": {
            "rows": validation.rows,
            "symbols": validation.symbols,
            "latest_symbols": len(latest_symbols),
            "rebalance_dates": validation.rebalance_dates,
            "duplicate_rows": validation.duplicate_rows,
        },
        "as_of": validation.actual_as_of,
    }
    _write_universe_validation_report(payload, out)
    return payload
