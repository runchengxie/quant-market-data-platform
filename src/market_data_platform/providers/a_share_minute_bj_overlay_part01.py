"""Append strictly validated TuShare Beijing-market bars to complete Guan days."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from market_data_platform.providers.a_share_minute_fusion import (
    CANONICAL_MINUTE_COLUMNS,
    CANONICAL_MINUTE_SCHEMA,
    MINUTE_KEY_COLUMNS,
    normalize_tushare_partition,
)
from market_data_platform.providers.a_share_minute_price_flow import (
    POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
    VWAP_OHLC_DIAGNOSTIC,
    VWAP_SOURCE_GUARD_ISSUE,
    price_flow_diagnostics,
    tushare_price_flow_policy,
)
from market_data_platform.providers.tushare_a_share_mins import (
    COMPLETENESS_SCHEMA_VERSION,
    EXPECTED_MINUTES_OF_DAY,
    MINUTE_BARS_PER_DAY,
    UNIVERSE_RULE,
    validate_complete_minute_partition,
)

BJ_OVERLAY_PLAN_SCHEMA = "a_share.minute_tushare_bj_overlay_plan.v1"

BJ_OVERLAY_RECEIPT_SCHEMA = "a_share.minute_tushare_bj_overlay_receipt.v2"

BSE_FIRST_TRADE_DATE = "20211115"

BJ_UNIVERSE_RULE = f"{UNIVERSE_RULE}:exchange=BJ"

_PARTITION_PATTERN = re.compile(r"trade_date=(\d{8})$")

_BJ_CODE_PATTERN = re.compile(r"^\d{6}\.BJ$")

_SH_SZ_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")

BJOverlayPhase = Literal["pilot", "production"]


@dataclass(frozen=True)
class BJOverlayPlan:
    """Explicit sorted set of Guan dates that may receive a BJ overlay."""

    dates: tuple[str, ...]
    phase: BJOverlayPhase = "pilot"
    sha256: str | None = None

    def __post_init__(self) -> None:
        normalized = tuple(_validate_date(value) for value in self.dates)
        if self.phase not in {"pilot", "production"}:
            raise ValueError(f"Unsupported BJ overlay phase: {self.phase!r}")
        if not normalized:
            raise ValueError("BJ overlay plan must contain at least one date")
        if normalized != tuple(sorted(set(normalized))):
            raise ValueError("BJ overlay plan dates must be sorted and unique")
        before_bse = [value for value in normalized if value < BSE_FIRST_TRADE_DATE]
        if before_bse:
            raise ValueError(
                f"BJ overlay dates cannot precede {BSE_FIRST_TRADE_DATE}: {before_bse}"
            )
        if self.sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ValueError("BJ overlay plan sha256 must be a lowercase SHA-256 digest")
        if self.phase == "production" and self.sha256 is None:
            raise ValueError("Production BJ overlay plans must be bound to plan bytes")
        object.__setattr__(self, "dates", normalized)


@dataclass(frozen=True)
class _BJOverlayMaterializationRequest:
    output_dir: str | Path
    annual_full_dates: Iterable[str]
    bj_partitions: Mapping[str, str | Path]
    overlay_dates: Iterable[str]
    base_expected_stats: Mapping[str, Any]
    deal_full_dates: Iterable[str] = ()
    tushare_full_dates: Iterable[str] = ()
    phase: BJOverlayPhase = "pilot"
    plan_sha256: str | None = None
    receipt_path: str | Path | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class _BJOverlayMaterializationContext:
    output_root: Path
    plan: BJOverlayPlan
    partitions: Mapping[str, Path]
    tier_by_date: Mapping[str, str]
    base_expected: Mapping[str, Mapping[str, Any]]

    @property
    def unused_source_dates(self) -> list[str]:
        return sorted(set(self.partitions) - set(self.plan.dates))


@dataclass(frozen=True)
class _BJOverlayPreflightEvidence:
    actions: list[dict[str, Any]]
    source_receipts: dict[str, dict[str, Any]]
    base_receipts: dict[str, dict[str, Any]]
    expected_stats: dict[str, dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_date(value: str) -> str:
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text) is None:
        raise ValueError(f"Expected YYYYMMDD date, got {value!r}")
    pd.to_datetime(text, format="%Y%m%d", errors="raise")
    return text


def _date_set(values: Iterable[str]) -> set[str]:
    return {_validate_date(value) for value in values}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(payload: Mapping[str, Any], path: Path) -> None:
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
            json.dump(payload, temporary, ensure_ascii=True, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def discover_tushare_bj_partitions(root: str | Path) -> dict[str, Path]:
    """Discover BJ-only v3 mirror day directories."""
    result: dict[str, Path] = {}
    for path in sorted(Path(root).expanduser().glob("trade_date=*")):
        matched = _PARTITION_PATTERN.fullmatch(path.name)
        if matched is not None and path.is_dir():
            result[matched.group(1)] = path
    return result


def load_bj_overlay_plan(path: str | Path) -> BJOverlayPlan:
    """Load an explicit BJ overlay plan from JSON."""
    plan_path = Path(path).expanduser()
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read BJ overlay plan {plan_path}: {exc}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != BJ_OVERLAY_PLAN_SCHEMA:
        raise ValueError(f"Unsupported BJ overlay plan schema: {plan_path}")
    dates = payload.get("dates")
    if not isinstance(dates, list) or not all(isinstance(value, str) for value in dates):
        raise ValueError(f"BJ overlay plan has malformed dates: {plan_path}")
    phase = payload.get("phase")
    if phase not in {"pilot", "production"}:
        raise ValueError(f"BJ overlay plan has an invalid phase: {plan_path}")
    return BJOverlayPlan(  # type: ignore[arg-type]
        dates=tuple(dates),
        phase=phase,
        sha256=_sha256_file(plan_path),
    )


def _frame_failure_counts(
    frame: pd.DataFrame,
    *,
    trade_date: str,
    market: str,
) -> dict[str, int]:
    numeric = frame.loc[:, list(CANONICAL_MINUTE_COLUMNS[2:])]
    codes = frame["ts_code"].astype("string")
    times = pd.to_datetime(frame["trade_time"], errors="coerce")
    invalid_ohlc = cast(
        pd.Series,
        numeric["high"].lt(numeric[["open", "close", "low"]].max(axis=1))
        | numeric["low"].gt(numeric[["open", "close", "high"]].min(axis=1)),
    )
    null_rows = cast(pd.Series, frame.isna().any(axis=1))
    expected_pattern = _BJ_CODE_PATTERN if market == "BJ" else _SH_SZ_CODE_PATTERN
    return {
        "empty_rows": int(frame.empty),
        "null_rows": int(null_rows.sum()),
        "non_finite_rows": int((~np.isfinite(numeric.to_numpy(dtype="float64"))).any(axis=1).sum()),
        "negative_flow_rows": int((numeric[["vol", "amount"]] < 0).any(axis=1).sum()),
        "invalid_ohlc_rows": int(invalid_ohlc.sum()),
        "duplicate_key_rows": int(frame.duplicated(list(MINUTE_KEY_COLUMNS), keep=False).sum()),
        "wrong_date_rows": int(times.dt.strftime("%Y%m%d").ne(trade_date).sum()),
        "non_minute_rows": int(
            (times.dt.second.ne(0) | times.dt.microsecond.ne(0) | times.dt.nanosecond.ne(0)).sum()
        ),
        "unexpected_market_rows": int((~codes.str.fullmatch(expected_pattern, na=False)).sum()),
    }


def _assert_valid_frame(
    frame: pd.DataFrame,
    *,
    trade_date: str,
    market: str,
) -> None:
    failures = {
        name: count
        for name, count in _frame_failure_counts(
            frame,
            trade_date=trade_date,
            market=market,
        ).items()
        if count
    }
    if failures:
        raise ValueError(f"{market} minute frame {trade_date} failed validation: {failures}")


def _bj_flow_diagnostics(frame: pd.DataFrame) -> dict[str, int]:
    numeric = frame.loc[:, ["high", "low", "vol", "amount"]]
    return price_flow_diagnostics(numeric)


def _read_base_partition(
    output_root: Path,
    *,
    trade_date: str,
) -> tuple[pd.DataFrame, Path, str]:
    path = output_root / f"trade_date={trade_date}" / "part-00000.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"Missing canonical base partition for BJ overlay: {path}")
    parquet_file = pq.ParquetFile(path)
    if not parquet_file.schema_arrow.equals(CANONICAL_MINUTE_SCHEMA):
        raise ValueError(f"Canonical base partition has the wrong schema: {path}")
    frame = parquet_file.read().to_pandas()
    if list(frame.columns) != list(CANONICAL_MINUTE_COLUMNS):
        raise ValueError(f"Canonical base partition has unexpected columns: {path}")
    return frame, path, _sha256_file(path)


def _read_bj_source(
    partition_dir: Path,
    *,
    trade_date: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    promotion = validate_complete_minute_partition(
        partition_dir,
        trade_date=trade_date,
        require_full_universe=False,
    )
    expected_symbols = {str(value) for value in promotion["expected_symbols"]}
    market_counts = promotion.get("market_symbol_counts")
    if promotion.get("universe_rule") != BJ_UNIVERSE_RULE:
        raise ValueError(
            f"BJ mirror {trade_date} must use the dynamic daily universe rule {BJ_UNIVERSE_RULE!r}"
        )
    if not str(promotion.get("universe_source", "")).endswith("+exchange_filter(BJ)"):
        raise ValueError(f"BJ mirror {trade_date} does not record the dynamic exchange filter")
    if (
        not expected_symbols
        or any(_BJ_CODE_PATTERN.fullmatch(symbol) is None for symbol in expected_symbols)
        or not isinstance(market_counts, Mapping)
        or int(market_counts.get("SH", -1)) != 0
        or int(market_counts.get("SZ", -1)) != 0
        or int(market_counts.get("BJ", -1)) != len(expected_symbols)
    ):
        raise ValueError(f"BJ mirror {trade_date} is not a pure Beijing-market universe")

    frame = normalize_tushare_partition(Path(str(promotion["partition_path"])))
    _assert_valid_frame(frame, trade_date=trade_date, market="BJ")
    flow_diagnostics = _bj_flow_diagnostics(frame)
    fatal_flow_issues = {
        name: count
        for name, count in flow_diagnostics.items()
        if name
        not in {
            VWAP_OHLC_DIAGNOSTIC,
            VWAP_SOURCE_GUARD_ISSUE,
            POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
        }
        and count
    }
    if fatal_flow_issues:
        raise ValueError(
            f"BJ mirror {trade_date} failed TuShare flow/VWAP guards: {fatal_flow_issues}"
        )
    actual_symbols = set(frame["ts_code"].astype(str))
    group_sizes = frame.groupby("ts_code", observed=True).size()
    minutes = frame["trade_time"].dt.hour * 60 + frame["trade_time"].dt.minute
    grid_by_symbol = cast(
        Iterable[tuple[Any, Any]],
        pd.DataFrame(
            {"ts_code": frame["ts_code"].astype(str), "minute": minutes.astype(int)}
        ).groupby("ts_code", observed=True)["minute"],
    )
    wrong_grid_symbols = sorted(
        symbol
        for symbol, values in grid_by_symbol
        if set(values.tolist()) != EXPECTED_MINUTES_OF_DAY
    )
    if (
        actual_symbols != expected_symbols
        or len(frame) != len(expected_symbols) * MINUTE_BARS_PER_DAY
        or not group_sizes.eq(MINUTE_BARS_PER_DAY).all()
        or wrong_grid_symbols
    ):
        raise ValueError(
            f"BJ mirror {trade_date} does not match the exact 241-bar session grid; "
            f"symbols={wrong_grid_symbols[:10]}"
        )
    compact = {key: value for key, value in promotion.items() if key != "expected_symbols"}
    compact.update(
        {
            "expected_symbol_count": len(expected_symbols),
            "sidecar_schema_version": COMPLETENESS_SCHEMA_VERSION,
            "selected_market_scope": "BJ",
            "selected_rows": len(frame),
            "selected_symbols": len(expected_symbols),
            "flow_diagnostics": flow_diagnostics,
            "accepted_diagnostics": {
                name: flow_diagnostics[name]
                for name in (
                    VWAP_OHLC_DIAGNOSTIC,
                    VWAP_SOURCE_GUARD_ISSUE,
                    POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
                )
                if flow_diagnostics[name]
            },
            "tushare_price_flow_validation": dict(tushare_price_flow_policy()),
            "session_grid_validation": {
                "status": "passed",
                "morning": "09:30-11:30",
                "afternoon": "13:01-15:00",
                "bars_per_symbol": MINUTE_BARS_PER_DAY,
            },
        }
    )
    return frame, compact


def _sorted_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values(list(MINUTE_KEY_COLUMNS), kind="stable").reset_index(drop=True)


def _frames_equal(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    try:
        pd.testing.assert_frame_equal(
            _sorted_frame(left),
            _sorted_frame(right),
            check_dtype=False,
            check_exact=True,
        )
    except AssertionError:
        return False
    return True


def _merged_stats(frame: pd.DataFrame, *, unit_profile: str) -> dict[str, Any]:
    numeric = frame.loc[:, list(CANONICAL_MINUTE_COLUMNS[2:])]
    traded = frame["vol"].gt(0)
    return {
        "rows": len(frame),
        "symbols": int(frame["ts_code"].nunique()),
        "traded_symbols": int(frame.loc[traded, "ts_code"].nunique()),
        "vol_sum": float(numeric["vol"].sum()),
        "amount_sum": float(numeric["amount"].sum()),
        "unit_profile": unit_profile,
        "time_min": str(frame["trade_time"].min()),
        "time_max": str(frame["trade_time"].max()),
    }


def _base_expected_value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _coerce_base_expected(value: Any, *, trade_date: str) -> dict[str, Any]:
    rows = _base_expected_value(value, "rows")
    symbols = _base_expected_value(value, "symbols")
    unit_profile = str(_base_expected_value(value, "unit_profile") or "").strip()
    if rows is None or symbols is None or not unit_profile:
        raise ValueError(f"Guan base evidence {trade_date} requires rows, symbols and unit_profile")
    output_sha256 = _base_expected_value(value, "output_sha256")
    if output_sha256 is not None:
        output_sha256 = str(output_sha256)
        if re.fullmatch(r"[0-9a-f]{64}", output_sha256) is None:
            raise ValueError(f"Guan base evidence {trade_date} has an invalid output SHA-256")
    result: dict[str, Any] = {
        "rows": int(rows),
        "symbols": int(symbols),
        "unit_profile": unit_profile,
        "output_sha256": output_sha256,
    }
    for name in ("traded_symbols", "vol_sum", "amount_sum", "time_min", "time_max"):
        raw = _base_expected_value(value, name)
        if raw is None:
            result[name] = None
        elif name == "traded_symbols":
            result[name] = int(raw)
        elif name in {"vol_sum", "amount_sum"}:
            result[name] = float(raw)
        else:
            result[name] = str(pd.Timestamp(raw))
    return result


def _prepare_base_evidence(
    plan: BJOverlayPlan,
    *,
    tier_by_date: Mapping[str, str],
    base_expected_stats: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    missing = sorted(set(plan.dates) - set(base_expected_stats))
    if missing:
        raise ValueError(f"BJ overlay requires independent Guan base evidence: {missing}")
    result: dict[str, dict[str, Any]] = {}
    for date in plan.dates:
        expected = _coerce_base_expected(base_expected_stats[date], trade_date=date)
        tier = tier_by_date[date]
        if tier == "annual_full_sh_sz":
            missing_fields = [name for name in ("time_min", "time_max") if expected[name] is None]
        else:
            missing_fields = [
                name
                for name in (
                    "traded_symbols",
                    "vol_sum",
                    "amount_sum",
                    "output_sha256",
                )
                if expected[name] is None
            ]
            expected["time_min"] = expected["time_min"] or str(pd.Timestamp(f"{date} 09:30:00"))
            expected["time_max"] = expected["time_max"] or str(pd.Timestamp(f"{date} 15:00:00"))
        if missing_fields:
            raise ValueError(
                f"Guan {tier} base evidence {date} lacks strict fields: {missing_fields}"
            )
        result[date] = expected
    return result


def _validate_base_evidence(
    frame: pd.DataFrame,
    *,
    trade_date: str,
    base_sha256: str,
    expected: Mapping[str, Any],
    already_overlayed: bool,
) -> dict[str, Any]:
    actual = _merged_stats(frame, unit_profile=str(expected["unit_profile"]))
    issues = {
        "base_row_mismatch": int(actual["rows"] != expected["rows"]),
        "base_symbol_mismatch": int(actual["symbols"] != expected["symbols"]),
        "base_time_min_mismatch": int(
            pd.Timestamp(actual["time_min"]) != pd.Timestamp(expected["time_min"])
        ),
        "base_time_max_mismatch": int(
            pd.Timestamp(actual["time_max"]) != pd.Timestamp(expected["time_max"])
        ),
    }
    for name in ("traded_symbols", "vol_sum", "amount_sum"):
        expected_value = expected[name]
        if expected_value is None:
            continue
        if name == "traded_symbols":
            mismatch = actual[name] != expected_value
        else:
            mismatch = not np.isclose(
                float(actual[name]),
                float(expected_value),
                rtol=1e-8,
                atol=1e-6,
            )
        issues[f"base_{name}_mismatch"] = int(mismatch)
    expected_sha256 = expected["output_sha256"]
    hash_status = "not_available_in_source_manifest"
    if expected_sha256 is not None and not already_overlayed:
        issues["base_output_sha256_mismatch"] = int(base_sha256 != expected_sha256)
        hash_status = "matched_source_manifest"
    elif expected_sha256 is not None:
        hash_status = "logical_reconciliation_after_existing_overlay"
    failures = {name: count for name, count in issues.items() if count}
    if failures:
        raise ValueError(f"Guan base {trade_date} failed independent reconciliation: {failures}")
    return {
        "status": "passed",
        "expected_stats": dict(expected),
        "actual_stats": actual,
        "canonical_partition_sha256": base_sha256,
        "hash_validation": hash_status,
    }
