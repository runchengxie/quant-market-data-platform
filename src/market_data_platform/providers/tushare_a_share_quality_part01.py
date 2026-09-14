from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pandas as pd
import yaml

from market_data_platform.parquet_scanning import ParquetBatchScanner
from market_data_platform.runtime_memory import (
    DEFAULT_MEMORY_HARD_AVAILABLE_MB,
    DEFAULT_MEMORY_SOFT_AVAILABLE_MB,
    DEFAULT_TARGET_BATCH_ROWS,
    MemoryPolicy,
)

from .tushare_a_share_daily_schema import LIMIT_COLUMNS, PRICE_COLUMNS

BASELINE_PROFILE = "baseline"

RESEARCH_PROFILE = "research"

VALIDATION_PROFILES = (BASELINE_PROFILE, RESEARCH_PROFILE)

FAIL_ON_SEVERITIES = ("none", "info", "warning", "error")

SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}

SYMBOL_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")

TRADE_DATE_RE = re.compile(r"^\d{8}$")

SAMPLE_LIMIT = 20

BASELINE_REQUIRED_COLUMNS = {
    "symbol",
    "trade_date",
    *PRICE_COLUMNS,
    "vol",
    "amount",
    "tr_close",
    "is_st",
    "is_suspended",
    "is_limit_up",
    "is_limit_down",
}

RESEARCH_REQUIRED_COLUMNS = {
    "pe_ttm",
    "pb",
    "total_mv",
    "turnover_rate",
    *LIMIT_COLUMNS,
    "board",
    "listed_days",
}

PROJECTED_COLUMNS = sorted(
    {
        *BASELINE_REQUIRED_COLUMNS,
        *RESEARCH_REQUIRED_COLUMNS,
        "pct_chg",
    }
)


@dataclass(frozen=True)
class DailyCleanValidationOptions:
    daily_clean_dir: str | Path
    min_rows: int = 1
    min_symbols: int = 1
    require_valuation: bool = False
    require_limit_status: bool = False
    profile: str = BASELINE_PROFILE
    trade_cal_file: str | Path | None = None
    fail_on_severity: str = "error"
    max_warning_rate: float = 0.0
    pct_chg_tolerance: float = 0.05
    batch_rows: int = DEFAULT_TARGET_BATCH_ROWS
    memory_soft_limit_mb: float | None = DEFAULT_MEMORY_SOFT_AVAILABLE_MB
    memory_hard_limit_mb: float | None = DEFAULT_MEMORY_HARD_AVAILABLE_MB
    out: str | Path | None = None

    @classmethod
    def from_legacy_kwargs(
        cls,
        *,
        daily_clean_dir: str | Path,
        **legacy_kwargs: Any,
    ) -> DailyCleanValidationOptions:
        profile_value = _normalize_profile(legacy_kwargs.get("profile", BASELINE_PROFILE))
        threshold = _normalize_fail_on_severity(legacy_kwargs.get("fail_on_severity", "error"))
        max_warning_rate = float(legacy_kwargs.get("max_warning_rate", 0.0))
        if max_warning_rate < 0:
            raise ValueError("max_warning_rate must be >= 0.")
        pct_chg_tolerance = float(legacy_kwargs.get("pct_chg_tolerance", 0.05))
        if pct_chg_tolerance < 0:
            raise ValueError("pct_chg_tolerance must be >= 0.")
        return cls(
            daily_clean_dir=Path(daily_clean_dir).expanduser().resolve(),
            min_rows=int(legacy_kwargs.get("min_rows", 1)),
            min_symbols=int(legacy_kwargs.get("min_symbols", 1)),
            require_valuation=bool(legacy_kwargs.get("require_valuation", False)),
            require_limit_status=bool(legacy_kwargs.get("require_limit_status", False)),
            profile=profile_value,
            trade_cal_file=legacy_kwargs.get("trade_cal_file"),
            fail_on_severity=threshold,
            max_warning_rate=max_warning_rate,
            pct_chg_tolerance=pct_chg_tolerance,
            batch_rows=int(legacy_kwargs.get("batch_rows", DEFAULT_TARGET_BATCH_ROWS)),
            memory_soft_limit_mb=legacy_kwargs.get(
                "memory_soft_limit_mb",
                DEFAULT_MEMORY_SOFT_AVAILABLE_MB,
            ),
            memory_hard_limit_mb=legacy_kwargs.get(
                "memory_hard_limit_mb",
                DEFAULT_MEMORY_HARD_AVAILABLE_MB,
            ),
            out=legacy_kwargs.get("out"),
        )

    def required_columns(self) -> set[str]:
        required = set(BASELINE_REQUIRED_COLUMNS)
        if self.require_valuation:
            required.update({"pe_ttm", "pb", "total_mv", "turnover_rate"})
        if self.require_limit_status:
            required.update(LIMIT_COLUMNS)
        if self.profile == RESEARCH_PROFILE:
            required.update(RESEARCH_REQUIRED_COLUMNS)
        return required


@dataclass
class _Accumulator:
    rows: int = 0
    files: int = 0
    symbols: set[str] = field(default_factory=set)
    trade_dates: set[str] = field(default_factory=set)
    columns: set[str] = field(default_factory=set)
    start_date: str | None = None
    end_date: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    samples: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    previous_key_by_file: dict[str, tuple[str, str]] = field(default_factory=dict)

    def increment(
        self,
        check: str,
        mask: pd.Series,
        frame: pd.DataFrame,
        *,
        sample_columns: tuple[str, ...] = ("symbol", "trade_date"),
    ) -> None:
        affected = int(mask.fillna(False).sum())
        if affected <= 0:
            return
        self.counts[check] = self.counts.get(check, 0) + affected
        samples = self.samples.setdefault(check, [])
        if len(samples) >= SAMPLE_LIMIT:
            return
        available = [column for column in sample_columns if column in frame.columns]
        sample = cast(pd.DataFrame, frame.loc[mask.fillna(False), available].head(SAMPLE_LIMIT))
        rows = sample.where(pd.notna(sample), None).to_dict("records")
        samples.extend(rows[: SAMPLE_LIMIT - len(samples)])


@dataclass(frozen=True)
class _BuildChecksRequest:
    accumulator: _Accumulator
    profile: str
    required_columns: set[str]
    manifest: dict[str, Any] | None
    trade_calendar_path: str | None
    trade_calendar_dates: set[str]
    max_warning_rate: float


@dataclass(frozen=True)
class _ValidationReportRequest:
    options: DailyCleanValidationOptions
    root: Path
    manifest_path: Path
    trade_calendar_path: str | None
    accumulator: _Accumulator
    checks: list[dict[str, Any]]
    policy: MemoryPolicy
    scanner: ParquetBatchScanner
    unreadable: list[dict[str, str]]


def _normalize_fail_on_severity(value: str) -> str:
    text = str(value or "error").strip().lower()
    if text not in FAIL_ON_SEVERITIES:
        raise ValueError("fail_on_severity must be one of: none, info, warning, error.")
    return text


def _normalize_profile(value: str) -> str:
    text = str(value or BASELINE_PROFILE).strip().lower()
    if text not in VALIDATION_PROFILES:
        raise ValueError(f"profile must be one of: {', '.join(VALIDATION_PROFILES)}.")
    return text


def _safe_bool(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index, dtype=bool)
    values = frame[column]
    if values.dtype == bool:
        return cast(pd.Series, values.fillna(False))
    normalized = values.astype(str).str.strip().str.lower()
    return normalized.isin({"1", "true", "yes", "y"})


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return cast(pd.Series, pd.to_numeric(frame[column], errors="coerce"))


def _board_from_symbol(symbol: str) -> str:
    text = str(symbol).upper()
    if text.endswith(".BJ"):
        return "BSE"
    if text.startswith("688") and text.endswith(".SH"):
        return "STAR"
    if text.startswith("300") and text.endswith(".SZ"):
        return "CHINEXT"
    return "MAIN"


def _load_manifest(root: Path) -> tuple[Path, dict[str, Any] | None]:
    path = root / "manifest.yml"
    if not path.is_file():
        return path, None
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return path, payload if isinstance(payload, dict) else None


def _load_trade_calendar(path: str | Path | None) -> tuple[str | None, set[str]]:
    if path is None:
        return None, set()
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"A 股 trade calendar file not found: {resolved}")
    frame = (
        pd.read_parquet(resolved)
        if resolved.suffix.lower() == ".parquet"
        else pd.read_csv(resolved)
    )
    date_column = "cal_date" if "cal_date" in frame.columns else "trade_date"
    if date_column not in frame.columns:
        raise ValueError("A 股 trade calendar is missing cal_date/trade_date.")
    if "is_open" in frame.columns:
        open_mask = frame["is_open"].astype(str).str.strip().isin({"1", "true", "True"})
        frame = cast(pd.DataFrame, frame.loc[open_mask])
    dates = {
        str(value).strip().replace("-", "")[:8]
        for value in frame[date_column].dropna().tolist()
        if TRADE_DATE_RE.fullmatch(str(value).strip().replace("-", "")[:8])
    }
    return str(resolved), dates


def _record_common_checks(accumulator: _Accumulator, frame: pd.DataFrame, *, path: Path) -> None:
    accumulator.rows += int(len(frame))
    accumulator.columns.update(str(column) for column in frame.columns)
    if frame.empty:
        return
    symbols = (
        frame["symbol"].astype(str)
        if "symbol" in frame.columns
        else pd.Series("", index=frame.index)
    )
    trade_dates = (
        frame["trade_date"].astype(str)
        if "trade_date" in frame.columns
        else pd.Series("", index=frame.index)
    )
    accumulator.symbols.update(symbols.loc[symbols != ""].unique().tolist())
    accumulator.trade_dates.update(trade_dates.loc[trade_dates != ""].unique().tolist())
    if not trade_dates.empty:
        batch_start = str(trade_dates.min())
        batch_end = str(trade_dates.max())
        accumulator.start_date = (
            batch_start
            if accumulator.start_date is None
            else min(accumulator.start_date, batch_start)
        )
        accumulator.end_date = (
            batch_end if accumulator.end_date is None else max(accumulator.end_date, batch_end)
        )

    accumulator.increment("symbol_format", ~symbols.str.fullmatch(SYMBOL_RE, na=False), frame)
    accumulator.increment(
        "trade_date_format",
        ~trade_dates.str.fullmatch(TRADE_DATE_RE, na=False),
        frame,
    )
    keys = list(zip(symbols, trade_dates, strict=False))
    previous = accumulator.previous_key_by_file.get(str(path))
    duplicate_mask: list[bool] = []
    order_mask: list[bool] = []
    for key in keys:
        duplicate_mask.append(previous == key)
        order_mask.append(previous is not None and previous > key)
        previous = key
    if previous is not None:
        accumulator.previous_key_by_file[str(path)] = previous
    accumulator.increment(
        "duplicate_symbol_trade_date",
        pd.Series(duplicate_mask, index=frame.index),
        frame,
    )
    accumulator.increment(
        "symbol_trade_date_order",
        pd.Series(order_mask, index=frame.index),
        frame,
    )

    for column in PRICE_COLUMNS:
        numeric = _numeric(frame, column)
        accumulator.increment(
            f"numeric_{column}",
            numeric.isna(),
            frame,
            sample_columns=("symbol", "trade_date", column),
        )
    for column in ("vol", "amount"):
        numeric = _numeric(frame, column)
        accumulator.increment(
            f"non_negative_{column}",
            numeric < 0,
            frame,
            sample_columns=("symbol", "trade_date", column),
        )

    suspended = _safe_bool(frame, "is_suspended")
    positive_prices = pd.Series(False, index=frame.index, dtype=bool)
    for column in PRICE_COLUMNS:
        positive_prices |= _numeric(frame, column) <= 0
    accumulator.increment(
        "positive_non_suspended_prices",
        ~suspended & positive_prices,
        frame,
        sample_columns=("symbol", "trade_date", *PRICE_COLUMNS, "is_suspended"),
    )

    open_price = _numeric(frame, "open")
    high = _numeric(frame, "high")
    low = _numeric(frame, "low")
    close = _numeric(frame, "close")
    invalid_ohlc = (high < pd.concat([open_price, close, low], axis=1).max(axis=1)) | (
        low > pd.concat([open_price, close, high], axis=1).min(axis=1)
    )
    accumulator.increment(
        "ohlc_bounds",
        ~suspended & invalid_ohlc,
        frame,
        sample_columns=("symbol", "trade_date", *PRICE_COLUMNS, "is_suspended"),
    )


def _record_research_checks(
    accumulator: _Accumulator,
    frame: pd.DataFrame,
    *,
    pct_chg_tolerance: float,
) -> None:
    close = _numeric(frame, "close")
    pre_close = _numeric(frame, "pre_close")
    up_limit = _numeric(frame, "up_limit")
    down_limit = _numeric(frame, "down_limit")
    is_limit_up = _safe_bool(frame, "is_limit_up")
    is_limit_down = _safe_bool(frame, "is_limit_down")
    accumulator.increment(
        "limit_price_order",
        up_limit.notna() & down_limit.notna() & (up_limit < down_limit),
        frame,
        sample_columns=("symbol", "trade_date", "up_limit", "down_limit"),
    )
    accumulator.increment(
        "limit_up_flag_consistency",
        is_limit_up != (close >= up_limit).fillna(False),
        frame,
        sample_columns=("symbol", "trade_date", "close", "up_limit", "is_limit_up"),
    )
    accumulator.increment(
        "limit_down_flag_consistency",
        is_limit_down != (close <= down_limit).fillna(False),
        frame,
        sample_columns=("symbol", "trade_date", "close", "down_limit", "is_limit_down"),
    )
    suspended = _safe_bool(frame, "is_suspended")
    zero_trading = (_numeric(frame, "vol").fillna(0) <= 0) | (
        _numeric(frame, "amount").fillna(0) <= 0
    )
    accumulator.increment(
        "suspension_consistency",
        zero_trading & ~suspended,
        frame,
        sample_columns=("symbol", "trade_date", "vol", "amount", "is_suspended"),
    )
    listed_days = _numeric(frame, "listed_days")
    accumulator.increment(
        "listed_days",
        listed_days.isna() | (listed_days < 0),
        frame,
        sample_columns=("symbol", "trade_date", "listed_days"),
    )
    if "board" in frame.columns:
        expected_board = frame["symbol"].astype(str).map(_board_from_symbol)
        accumulator.increment(
            "board_classification",
            frame["board"].astype(str) != expected_board,
            frame,
            sample_columns=("symbol", "trade_date", "board"),
        )
    if "pct_chg" in frame.columns:
        pct_chg = _numeric(frame, "pct_chg")
        implied = (close / pre_close - 1) * 100
        mismatch = (
            pre_close.notna()
            & (pre_close != 0)
            & pct_chg.notna()
            & ((pct_chg - implied).abs() > pct_chg_tolerance)
        )
        accumulator.increment(
            "pct_chg_consistency",
            mismatch,
            frame,
            sample_columns=("symbol", "trade_date", "close", "pre_close", "pct_chg"),
        )


def _check_row(
    *,
    check: str,
    severity: str,
    message: str,
    affected: int,
    samples: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    return {
        "check": check,
        "severity": severity,
        "status": "failed" if affected else "passed",
        "message": message,
        "affected_rows": int(affected),
        "sample_rows": samples.get(check, []),
    }
