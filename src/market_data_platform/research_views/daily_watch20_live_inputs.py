"""Availability inspection for DailyWatch20 inputs without publication policy."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypedDict, Unpack, cast

import pandas as pd

from market_data_platform.providers.tushare_a_share_mins import (
    UNIVERSE_RULE,
    validate_complete_minute_partition,
)

from .daily_watch20_candidate_pool import (
    CandidatePoolMode,
    DailyWatch20CandidatePool,
    candidate_pool_policy_id,
    load_daily_watch20_candidate_pool,
)
from .daily_watch20_data import DailyWatch20Assets, load_open_trade_dates


@dataclass(frozen=True, slots=True)
class DailyWatch20InputAvailability:
    """Owner-native facts about one source date; no release decision is embedded."""

    status: str
    source_date: str
    signal_date: str
    required_minute_date: str
    minute_source: str | None
    daily_as_of: str
    daily_is_current: bool
    canonical_minute_date_max: str | None
    candidate_pool_mode: str
    candidate_pool_symbols: int | None
    candidate_pool_policy_id: str | None
    issues: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.status == "available"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DailyWatch20InputOptions:
    """Structured request options for one availability inspection."""

    source_date: str | None = None
    lag_trade_days: int = 0
    overlay_roots: dict[str, Path] | None = None
    candidate_pool_mode: CandidatePoolMode = "all_market"
    ths_hot_root: Path | None = None
    ths_hot_min_symbols: int = 20
    ths_hot_snapshot_min_symbols: int = 80
    candidate_pool: DailyWatch20CandidatePool | None = None


class _DailyWatch20InputOverrides(TypedDict, total=False):
    source_date: str | None
    lag_trade_days: int
    overlay_roots: dict[str, Path] | None
    candidate_pool_mode: CandidatePoolMode
    ths_hot_root: Path | None
    ths_hot_min_symbols: int
    ths_hot_snapshot_min_symbols: int
    candidate_pool: DailyWatch20CandidatePool | None


def _date_key(value: object, *, field: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"{field} must be YYYYMMDD")
    try:
        return cast(pd.Timestamp, pd.Timestamp(text)).strftime("%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid date") from exc


def _trade_date_at_lag(
    open_dates: pd.DatetimeIndex,
    source_date: str,
    lag_trade_days: int,
) -> str:
    lag = int(lag_trade_days)
    if lag < 0:
        raise ValueError("lag_trade_days must be non-negative")
    source = cast(pd.Timestamp, pd.Timestamp(source_date)).normalize()
    positions = open_dates.get_indexer([source])
    if positions[0] < 0:
        raise ValueError(f"source_date is not an open trade date: {source_date}")
    target_position = int(positions[0]) - lag
    if target_position < 0:
        raise ValueError(f"trade calendar has no date at lag {lag} before {source_date}")
    return cast(pd.Timestamp, open_dates[target_position]).strftime("%Y%m%d")


def _next_trade_date(open_dates: pd.DatetimeIndex, source_date: str) -> str:
    source = cast(pd.Timestamp, pd.Timestamp(source_date)).normalize()
    later = open_dates[open_dates > source]
    if later.empty:
        raise ValueError(f"trade calendar has no open date after {source_date}")
    return cast(pd.Timestamp, later[0]).strftime("%Y%m%d")


def required_minute_date(
    assets: DailyWatch20Assets,
    source_date: str,
    *,
    lag_trade_days: int,
) -> str:
    return _trade_date_at_lag(
        load_open_trade_dates(assets),
        _date_key(source_date, field="source_date"),
        lag_trade_days,
    )


def _partition_path(root: Path, trade_date: str) -> Path:
    return root / f"trade_date={trade_date}"


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_int(value: object) -> bool:
    try:
        return int(str(value or "0")) > 0
    except ValueError:
        return False


def _canonical_partition_file(
    assets: DailyWatch20Assets, trade_date: str
) -> tuple[Path | None, str | None]:
    if assets.minute_date_min and trade_date < assets.minute_date_min:
        return None, f"canonical minute coverage starts after {trade_date}"
    if assets.minute_date_max and trade_date > assets.minute_date_max:
        return None, f"canonical minute coverage ends before {trade_date}"
    part_dir = _partition_path(assets.minute_current, trade_date)
    files = sorted(part_dir.glob("*.parquet")) if part_dir.is_dir() else []
    if len(files) != 1:
        return (
            None,
            f"canonical minute partition does not contain exactly one parquet file: {part_dir}",
        )
    return files[0], None


def _canonical_coverage_payload(
    assets: DailyWatch20Assets,
) -> tuple[dict[str, Any] | None, str | None]:
    coverage = assets.minute_coverage
    if coverage is None or not coverage.is_file():
        return None, "canonical minute coverage receipt is unavailable"
    try:
        payload = json.loads(coverage.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"canonical minute coverage receipt is invalid: {exc}"
    if assets.minute_dataset == "legacy":
        valid = (
            isinstance(payload, dict)
            and payload.get("status") == "passed"
            and payload.get("quality_status") == "passed"
            and payload.get("coverage_status") == "full_sh_sz"
        )
        if not valid:
            return None, "canonical minute coverage receipt is not passed full_sh_sz"
    else:
        summary = payload.get("summary") if isinstance(payload, dict) else None
        valid = (
            isinstance(payload, dict)
            and payload.get("schema_version") == "a_share.minute_tushare_operational_version.v1"
            and payload.get("status") == "published_operational_version"
            and payload.get("provider") == "tushare"
            and isinstance(summary, dict)
            and summary.get("market_scope") == "SH_SZ_BJ"
        )
        if not valid:
            return None, "TuShare operational minute receipt is invalid"
    return payload, None


def _tushare_daily_audit_reason(
    entry: object,
    trade_date: str,
    partition_file: Path,
) -> str | None:
    if not isinstance(entry, dict) or not _positive_int(entry.get("symbols")):
        return f"TuShare operational daily audit is missing or invalid for {trade_date}"
    expected_hash = str(entry.get("content_sha256") or "")
    if not expected_hash or _sha256_file(partition_file) != expected_hash:
        return f"TuShare operational minute partition hash mismatch for {trade_date}"
    return None


def _legacy_daily_audit_reason(
    entry: object,
    trade_date: str,
    partition_file: Path,
) -> str | None:
    if not isinstance(entry, dict) or entry.get("valid") is not True:
        return f"canonical minute daily audit is missing or invalid for {trade_date}"
    if entry.get("market_scope") not in {"SH_SZ", "SH_SZ_BJ"}:
        return f"canonical minute daily audit is not full SH/SZ for {trade_date}"
    if not _positive_int(entry.get("sh_sz_symbols")):
        return f"canonical minute daily audit contains no SH/SZ symbols for {trade_date}"
    expected_hash = str(entry.get("content_sha256") or "")
    if not expected_hash or _sha256_file(partition_file) != expected_hash:
        return f"canonical minute partition hash does not match its audit for {trade_date}"
    return None


def _canonical_daily_audit_reason(
    assets: DailyWatch20Assets,
    payload: dict[str, Any],
    trade_date: str,
    partition_file: Path,
) -> str | None:
    daily = payload.get("daily")
    if not isinstance(daily, list):
        return "canonical minute coverage receipt has no daily audit"
    date_field = "date" if assets.minute_dataset == "legacy" else "trade_date"
    entry = next(
        (item for item in daily if isinstance(item, dict) and item.get(date_field) == trade_date),
        None,
    )
    if assets.minute_dataset == "tushare":
        return _tushare_daily_audit_reason(entry, trade_date, partition_file)
    return _legacy_daily_audit_reason(entry, trade_date, partition_file)


def _canonical_unavailability_reason(assets: DailyWatch20Assets, trade_date: str) -> str | None:
    partition_file, partition_reason = _canonical_partition_file(assets, trade_date)
    if partition_reason is not None or partition_file is None:
        return partition_reason
    payload, coverage_reason = _canonical_coverage_payload(assets)
    if coverage_reason is not None or payload is None:
        return coverage_reason
    return _canonical_daily_audit_reason(assets, payload, trade_date, partition_file)


def _validate_exchange_overlay(root: Path, trade_date: str, exchange: str) -> None:
    receipt = validate_complete_minute_partition(
        _partition_path(root, trade_date),
        trade_date=trade_date,
        require_full_universe=False,
    )
    expected_rule = f"{UNIVERSE_RULE}:exchange={exchange}"
    if receipt.get("universe_rule") != expected_rule:
        raise ValueError(
            f"{exchange} minute overlay uses unexpected universe rule: "
            f"{receipt.get('universe_rule')!r}"
        )
    counts = receipt.get("market_symbol_counts")
    if not isinstance(counts, dict) or int(counts.get(exchange, 0)) <= 0:
        raise ValueError(f"{exchange} minute overlay contains no {exchange} symbols")
    other = "SZ" if exchange == "SH" else "SH"
    if int(counts.get(other, 0)) != 0 or int(counts.get("BJ", 0)) != 0:
        raise ValueError(f"{exchange} minute overlay contains out-of-scope symbols")


def _resolve_input_options(
    options: DailyWatch20InputOptions | None,
    overrides: _DailyWatch20InputOverrides,
) -> DailyWatch20InputOptions:
    if options is not None and overrides:
        raise ValueError("options cannot be combined with individual availability arguments")
    return options or DailyWatch20InputOptions(**overrides)


def _load_candidate_pool(
    assets: DailyWatch20Assets,
    source: str,
    options: DailyWatch20InputOptions,
    issues: list[str],
) -> DailyWatch20CandidatePool | None:
    if options.candidate_pool is not None:
        return options.candidate_pool
    try:
        return load_daily_watch20_candidate_pool(
            assets.data_root,
            source_date=source,
            mode=options.candidate_pool_mode,
            ths_hot_root=options.ths_hot_root,
            ths_hot_min_symbols=options.ths_hot_min_symbols,
            ths_hot_snapshot_min_symbols=options.ths_hot_snapshot_min_symbols,
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        issues.append(f"candidate pool unavailable: {exc}")
        return None


def _candidate_pool_facts(
    pool: DailyWatch20CandidatePool | None,
    source: str,
    options: DailyWatch20InputOptions,
    issues: list[str],
) -> tuple[int | None, str | None]:
    if pool is None:
        return None, None
    expected_policy = candidate_pool_policy_id(
        options.candidate_pool_mode,
        ths_hot_min_symbols=options.ths_hot_min_symbols,
        ths_hot_snapshot_min_symbols=options.ths_hot_snapshot_min_symbols,
    )
    if pool.mode != options.candidate_pool_mode:
        issues.append("candidate pool mode does not match the requested mode")
    if pool.source_date != source:
        issues.append("candidate pool source_date does not match the requested date")
    if pool.policy_id != expected_policy:
        issues.append("candidate pool policy does not match the requested policy")
    pool_symbols = len(pool.frame) if pool.restricted else None
    return pool_symbols, pool.policy_id


def _overlay_issues(
    roots: dict[str, Path],
    required_date: str,
) -> list[str]:
    issues: list[str] = []
    for exchange in ("SH", "SZ"):
        root = roots.get(exchange)
        if root is None:
            issues.append(f"missing {exchange} minute overlay root for {required_date}")
            continue
        try:
            _validate_exchange_overlay(root.expanduser().resolve(), required_date, exchange)
        except (FileNotFoundError, OSError, ValueError) as exc:
            issues.append(f"invalid {exchange} minute overlay for {required_date}: {exc}")
    return issues


def _minute_source(
    assets: DailyWatch20Assets,
    required_date: str,
    overlay_roots: dict[str, Path] | None,
    issues: list[str],
) -> str | None:
    canonical_reason = _canonical_unavailability_reason(assets, required_date)
    if canonical_reason is None:
        return "canonical" if assets.minute_dataset == "legacy" else "tushare_operational"
    roots = overlay_roots or {}
    overlay_problems = _overlay_issues(roots, required_date)
    if not overlay_problems and all(exchange in roots for exchange in ("SH", "SZ")):
        return "tushare_sh_sz_overlay"
    issues.append(canonical_reason)
    issues.extend(overlay_problems)
    return None


def inspect_daily_watch20_input_availability(
    assets: DailyWatch20Assets,
    options: DailyWatch20InputOptions | None = None,
    **overrides: Unpack[_DailyWatch20InputOverrides],
) -> tuple[DailyWatch20CandidatePool | None, DailyWatch20InputAvailability]:
    """Inspect exact-date inputs while leaving currentness/release policy to the caller."""

    resolved = _resolve_input_options(options, overrides)
    source = _date_key(resolved.source_date or assets.daily_as_of, field="source_date")
    open_dates = load_open_trade_dates(assets)
    required = _trade_date_at_lag(open_dates, source, resolved.lag_trade_days)
    signal = _next_trade_date(open_dates, source)
    issues: list[str] = []
    loaded_pool = _load_candidate_pool(assets, source, resolved, issues)
    pool_symbols, pool_policy = _candidate_pool_facts(loaded_pool, source, resolved, issues)
    minute_source = _minute_source(assets, required, resolved.overlay_roots, issues)

    availability = DailyWatch20InputAvailability(
        status="available" if not issues and minute_source else "unavailable",
        source_date=source,
        signal_date=signal,
        required_minute_date=required,
        minute_source=minute_source,
        daily_as_of=assets.daily_as_of,
        daily_is_current=source == assets.daily_as_of,
        canonical_minute_date_max=assets.minute_date_max,
        candidate_pool_mode=resolved.candidate_pool_mode,
        candidate_pool_symbols=pool_symbols,
        candidate_pool_policy_id=pool_policy,
        issues=tuple(issues),
    )
    return loaded_pool, availability


__all__ = [
    "DailyWatch20InputAvailability",
    "DailyWatch20InputOptions",
    "inspect_daily_watch20_input_availability",
    "required_minute_date",
]
