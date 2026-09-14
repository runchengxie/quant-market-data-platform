"""Universe resolution and fetch helpers for the A-share minute mirror.

These are internal helpers for ``tushare_a_share_mins``.  They include the three
functions monkeypatched by the test-suite (``_stock_history_from_provider``,
``_traded_symbols_from_provider`` and ``_trading_dates_from_provider``), which are
re-exported from the public module so existing ``monkeypatch.setattr(mins, ...)``
calls keep working.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pandas as pd

from market_data_platform.paths import resolve_artifacts_root
from market_data_platform.providers._a_share_mins_constants import (
    DAILY_PAGE_SIZE,
    EXPLICIT_UNIVERSE_RULE,
    HISTORICAL_CODE_TRANSITION_UNIVERSE_RULE,
    HISTORICAL_CODE_TRANSITIONS,
    MAX_MINS_BATCH_SIZE,
    MINS_OUTPUT_SUBDIR,
    STOCK_LIST_STATUSES,
    UNIVERSE_RULE,
    _ResolvedUniverse,
    _UniverseInputs,
)
from market_data_platform.providers._a_share_mins_partition import (
    _is_cn_stock_ts_code,
    _normalize_stock_dates,
)
from market_data_platform.tushare_minute_quota import (
    MinuteQuotaLedger,
    MinuteQuotaRequestOverrides,
    build_minute_quota_ledger,
)

__all__ = [
    "_build_output_dir",
    "_chunks",
    "validate_mins_batch_size",
    "_parse_symbols",
    "_stock_history_from_provider",
    "_apply_historical_code_transitions",
    "_historical_symbols",
    "_trading_dates_from_provider",
    "_resolve_explicit_universe",
    "_resolve_default_universe",
    "_resolve_minute_universe",
    "_build_mins_quota_ledger",
    "_validate_mirror_options",
]


def _build_output_dir(custom: str | Path | None = None) -> Path:
    if custom:
        return Path(custom)
    root = resolve_artifacts_root()
    return root / MINS_OUTPUT_SUBDIR


def _chunks(symbols: list[str], size: int) -> list[list[str]]:
    return [symbols[index : index + size] for index in range(0, len(symbols), size)]


def validate_mins_batch_size(batch_size: int) -> None:
    """Validate a 1min symbol batch against the endpoint response row cap."""
    if not 1 <= batch_size <= MAX_MINS_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_MINS_BATCH_SIZE} for 1min data")


def _parse_symbols(values: list[str] | None) -> list[str]:
    if not values:
        return []
    symbols = {
        item.strip().upper() for value in values for item in str(value).split(",") if item.strip()
    }
    return sorted(symbols)


def _stock_history_frames(
    pro: Any,
    policy: Any,
) -> tuple[list[pd.DataFrame], dict[str, int]]:
    from market_data_platform.providers.tushare_a_share import _call_tushare_api

    frames: list[pd.DataFrame] = []
    status_counts: dict[str, int] = {}
    fields = "ts_code,list_status,list_date,delist_date,curr_type"
    for status in STOCK_LIST_STATUSES:
        response = _call_tushare_api(
            lambda status=status: pro.stock_basic(exchange="", list_status=status, fields=fields),
            policy=policy,
        )
        if response is None:
            raise RuntimeError(f"TuShare stock_basic status={status} returned None")
        status_counts[status] = len(response)
        if response.empty:
            continue
        frame = response.copy()
        if "list_status" not in frame.columns:
            frame["list_status"] = status
        missing = {"ts_code", "list_date", "delist_date", "curr_type"} - set(frame.columns)
        if missing:
            raise ValueError(f"stock_basic status={status} missing fields: {sorted(missing)}")
        frames.append(frame[["ts_code", "list_status", "list_date", "delist_date", "curr_type"]])
    return frames, status_counts


def _insert_historical_code_aliases(
    history: pd.DataFrame,
    all_stock_symbols: set[str],
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    historical_aliases: list[str] = []
    for transition in HISTORICAL_CODE_TRANSITIONS:
        historical = transition["historical"]
        successor = transition["successor"]
        successor_rows = history.loc[history["ts_code"].eq(successor)]
        if successor_rows.empty:
            continue
        if historical not in set(history["ts_code"]):
            alias = successor_rows.iloc[[0]].copy()
            alias.loc[:, "ts_code"] = historical
            alias.loc[:, "list_status"] = "D"
            alias.loc[:, "delist_date"] = transition["historical_last_trade_date"]
            history = pd.concat([history, alias], ignore_index=True)
            all_stock_symbols.add(historical)
            historical_aliases.append(historical)
        history.loc[history["ts_code"].eq(historical), "list_status"] = "D"
        history.loc[history["ts_code"].eq(historical), "delist_date"] = transition[
            "historical_last_trade_date"
        ]
        history.loc[history["ts_code"].eq(successor), "list_date"] = transition[
            "successor_first_trade_date"
        ]
    return history, tuple(historical_aliases)


def _stock_history_from_provider(pro: Any, policy: Any) -> pd.DataFrame:
    """Load listed, delisted, paused and approved stocks for point-in-time universes."""
    frames, status_counts = _stock_history_frames(pro, policy)
    if not frames:
        raise RuntimeError("TuShare stock_basic returned no L/D/P/G universe rows")
    history = pd.concat(frames, ignore_index=True)
    history["ts_code"] = history["ts_code"].astype(str).str.strip().str.upper()
    history["list_status"] = history["list_status"].astype(str).str.strip().str.upper()
    history["curr_type"] = history["curr_type"].astype(str).str.strip().str.upper()
    raw_list_date = history["list_date"].astype("string").fillna("").str.strip()
    raw_delist_date = history["delist_date"].astype("string").fillna("").str.strip()
    history["list_date"] = _normalize_stock_dates(history["list_date"])
    history["delist_date"] = _normalize_stock_dates(history["delist_date"])
    if (raw_list_date.ne("") & history["list_date"].eq("")).any():
        raise ValueError("stock_basic returned an invalid non-empty list_date")
    if (raw_delist_date.ne("") & history["delist_date"].eq("")).any():
        raise ValueError("stock_basic returned an invalid non-empty delist_date")
    missing_required_list_date = history["list_status"].ne("G") & history["list_date"].eq("")
    if missing_required_list_date.any():
        raise ValueError("stock_basic returned L/D/P rows without list_date")
    if (history["list_status"].eq("D") & history["delist_date"].eq("")).any():
        raise ValueError("stock_basic returned D rows without delist_date")
    history = history[history["ts_code"].ne("") & history["list_status"].isin(STOCK_LIST_STATUSES)]
    if history["curr_type"].eq("").any():
        raise ValueError("stock_basic returned rows without curr_type")
    all_stock_symbols = set(history["ts_code"])
    non_a_stock_symbols = set(history.loc[history["curr_type"].ne("CNY"), "ts_code"])
    canonical_codes = history["ts_code"].map(_is_cn_stock_ts_code)
    excluded_noncanonical_symbols = set(history.loc[~canonical_codes, "ts_code"])
    history = history[history["curr_type"].eq("CNY") & canonical_codes].copy()
    history, historical_aliases = _insert_historical_code_aliases(history, all_stock_symbols)
    history = history.drop_duplicates().reset_index(drop=True)
    history.attrs["status_counts"] = status_counts
    history.attrs["all_stock_symbols"] = all_stock_symbols
    history.attrs["non_a_stock_symbols"] = non_a_stock_symbols
    history.attrs["excluded_noncanonical_symbols"] = excluded_noncanonical_symbols
    history.attrs["historical_code_aliases"] = tuple(historical_aliases)
    return history


def _apply_historical_code_transitions(
    symbols: set[str], *, trade_date: str
) -> tuple[set[str], tuple[str, ...]]:
    normalized = set(symbols)
    applied: list[str] = []
    for transition in HISTORICAL_CODE_TRANSITIONS:
        historical = transition["historical"]
        successor = transition["successor"]
        aliases = {historical, successor}
        if normalized.isdisjoint(aliases):
            continue
        normalized.difference_update(aliases)
        if trade_date <= transition["historical_last_trade_date"]:
            normalized.add(historical)
        elif trade_date >= transition["successor_first_trade_date"]:
            normalized.add(successor)
        else:
            raise RuntimeError(
                f"Trading date {trade_date} falls inside an unresolved code-transition gap: "
                f"{historical}->{successor}"
            )
        applied.append(f"{historical}->{successor}")
    return normalized, tuple(applied)


def _historical_symbols(history: pd.DataFrame, *, trade_date: str) -> set[str]:
    active = history[
        history["list_date"].ne("")
        & history["list_date"].le(trade_date)
        & (history["delist_date"].eq("") | history["delist_date"].ge(trade_date))
    ]
    return set(active["ts_code"])


def _trading_dates_from_provider(
    pro: Any,
    *,
    start_date: str,
    end_date: str,
    policy: Any,
) -> list[str]:
    from market_data_platform.providers.tushare_a_share import _call_tushare_api

    response = _call_tushare_api(
        lambda: pro.trade_cal(
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
            fields="cal_date,is_open",
        ),
        policy=policy,
    )
    if response is None or response.empty:
        raise RuntimeError("TuShare trade_cal returned no calendar rows")
    missing = {"cal_date", "is_open"} - set(response.columns)
    if missing:
        raise ValueError(f"trade_cal missing fields: {sorted(missing)}")
    dates = _normalize_stock_dates(response["cal_date"])
    is_open = pd.to_numeric(response["is_open"], errors="raise")
    expected_dates = set(pd.date_range(start_date, end_date, freq="D").strftime("%Y%m%d"))
    actual_dates = set(dates)
    if dates.eq("").any() or dates.duplicated().any() or actual_dates != expected_dates:
        raise RuntimeError(
            "TuShare trade_cal coverage mismatch: "
            f"missing={sorted(expected_dates - actual_dates)} "
            f"extra={sorted(actual_dates - expected_dates)}"
        )
    if not is_open.isin([0, 1]).all():
        raise ValueError("TuShare trade_cal is_open must contain only 0 or 1")
    return sorted(dates[is_open.eq(1)])


def _resolve_explicit_universe(inputs: _UniverseInputs) -> _ResolvedUniverse:
    stock_history = cast(pd.DataFrame, inputs.stock_history)
    symbols = set(inputs.explicit_symbols)
    wrong_exchange = {
        symbol
        for symbol in symbols
        if inputs.exchange and not symbol.endswith(f".{inputs.exchange}")
    }
    if wrong_exchange:
        raise ValueError(
            f"Explicit symbols do not match exchange={inputs.exchange}: {sorted(wrong_exchange)}"
        )
    checks = (
        (symbols - set(stock_history["ts_code"]), "are not CNY A-share stocks"),
        (symbols - inputs.active_symbols, "are outside active listing intervals"),
        (symbols - inputs.traded_symbols, "did not trade"),
    )
    for invalid, message in checks:
        if invalid:
            raise ValueError(
                f"Explicit symbols {message} on {inputs.trade_date}: {sorted(invalid)}"
            )
    return _ResolvedUniverse(
        symbols=symbols,
        rule=EXPLICIT_UNIVERSE_RULE,
        source="options.symbols+daily~daily_basic_SH_SZ+CNY_stock_basic",
    )


def _resolve_default_universe(inputs: _UniverseInputs) -> _ResolvedUniverse:
    stock_history = cast(pd.DataFrame, inputs.stock_history)
    symbols = set(inputs.traded_symbols)
    provider_verified_only = set(stock_history.attrs.get("provider_verified_only_symbols", set()))
    outside_active = symbols - inputs.active_symbols
    prelisting_bj = set(
        stock_history.loc[
            stock_history["ts_code"].isin(outside_active)
            & stock_history["ts_code"].str.endswith(".BJ")
            & stock_history["list_date"].gt(inputs.trade_date),
            "ts_code",
        ]
    )
    provider_verified_outside_active = outside_active & provider_verified_only
    invalid_outside_active = outside_active - prelisting_bj - provider_verified_outside_active
    if invalid_outside_active:
        raise RuntimeError(
            f"Daily traded universe for {inputs.trade_date} contains symbols outside "
            f"stock_basic active intervals: {sorted(invalid_outside_active)}"
        )
    symbols.intersection_update(inputs.active_symbols | provider_verified_outside_active)
    counts = stock_history.attrs.get("status_counts", {})
    count_text = ",".join(f"{status}:{counts.get(status, 0)}" for status in STOCK_LIST_STATUSES)
    rule = UNIVERSE_RULE
    source = (
        f"tushare.daily~daily_basic_SH_SZ(limit={DAILY_PAGE_SIZE},BJ=daily)+"
        f"stock_basic({count_text})"
    )
    applied = [
        f"{transition['historical']}->{transition['successor']}"
        for transition in HISTORICAL_CODE_TRANSITIONS
        if transition["historical"] in symbols
        and inputs.trade_date <= transition["historical_last_trade_date"]
    ]
    if applied:
        rule = HISTORICAL_CODE_TRANSITION_UNIVERSE_RULE
        source = f"{source}+code_transitions({','.join(applied)})"
    if prelisting_bj:
        source = f"{source}+exclude_prelisting_BJ({len(prelisting_bj)})"
    if provider_verified_outside_active:
        source = f"{source}+historical_provider_symbols({len(provider_verified_outside_active)})"
    return _ResolvedUniverse(symbols=symbols, rule=rule, source=source)


def _resolve_minute_universe(inputs: _UniverseInputs) -> _ResolvedUniverse:
    resolved = (
        _resolve_explicit_universe(inputs)
        if inputs.explicit_symbols
        else _resolve_default_universe(inputs)
    )
    symbols, rule, source = resolved.symbols, resolved.rule, resolved.source
    if inputs.exchange is not None:
        rule = f"{rule}:exchange={inputs.exchange}"
        source = f"{source}+exchange_filter({inputs.exchange})"
    if not symbols:
        raise RuntimeError(f"No active A-share symbols resolved for {inputs.trade_date}")
    return _ResolvedUniverse(symbols=symbols, rule=rule, source=source)


def _build_mins_quota_ledger(options: Any) -> MinuteQuotaLedger | None:
    return build_minute_quota_ledger(
        token=os.environ.get(options.token_env, ""),
        mode=options.minute_quota_mode,
        database_path=options.minute_quota_db,
        consumer=options.minute_quota_consumer,
        limit_rows=options.minute_quota_limit_rows,
        safety_rows=options.minute_quota_safety_rows,
        request_overrides=MinuteQuotaRequestOverrides(
            gate=options.minute_quota_gate,
            limit_requests=options.minute_quota_limit_requests,
            burst_limit_requests=options.minute_quota_burst_limit_requests,
            safety_requests=options.minute_quota_safety_requests,
            allow_burst=options.minute_quota_allow_burst,
        ),
    )


def _validate_mirror_options(options: Any) -> str | None:
    from market_data_platform.providers.tushare_a_share_dates import _validate_date

    _validate_date(options.start_date)
    _validate_date(options.end_date)
    if options.start_date > options.end_date:
        raise ValueError(f"start_date {options.start_date} > end_date {options.end_date}")
    if options.freq != "1min":
        raise ValueError("The hardened minute mirror currently supports only freq=1min")
    validate_mins_batch_size(options.batch_size)
    if options.gc_frequency <= 0:
        raise ValueError("gc_frequency must be positive")
    exchange = str(options.exchange or "").strip().upper() or None
    if exchange not in {None, "SH", "SZ", "BJ"}:
        raise ValueError("exchange must be one of SH, SZ, or BJ")
    if exchange is not None and options.output_dir is None:
        raise ValueError("exchange-filtered minute mirrors require an explicit output_dir")
    return exchange
