"""TuShare A-share minute mirror: fetch and validation helpers.

This submodule holds the lower-level fetch / pagination / partition-validation
symbols used by the public re-export shell ``tushare_a_share_mins`` and by the
orchestration submodule ``_mins_mirror``.  It must not import from ``_mins_mirror``
so that the two submodules and the shell can be imported without a circular
dependency.

Only symbols are moved here from the original single-file ``tushare_a_share_mins``;
no function logic is changed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC
from typing import Any

import pandas as pd

from market_data_platform.providers._a_share_mins_constants import (
    DEFAULT_MINS_FIELDS,
    MINUTE_BARS_PER_DAY,
    MinsMirrorOptions,
    _MinuteProgress,
    _UniverseInputs,
)
from market_data_platform.providers._a_share_mins_partition import (
    _is_cn_stock_ts_code,
    _normalize_stock_dates,
    _quarantine_partition_files,
    _read_completeness,
    _read_existing_partition,
    _universe_hash,
    _validate_batch_frame,
)
from market_data_platform.providers._a_share_mins_universe import (
    _apply_historical_code_transitions,
    _historical_symbols,
    _resolve_minute_universe,
)
from market_data_platform.providers._bse_code_mapping import bse_minute_request_symbol
from market_data_platform.providers._minute_provider_exceptions import (
    load_provider_no_data_exclusions,
)
from market_data_platform.tushare_minute_quota import (
    AmbiguousMinuteRequestError,
    MinuteQuotaPoolClosed,
    quota_date_at,
)


class _MinutePartitionIncompleteError(RuntimeError):
    """One date persisted useful rows but still has unresolved symbols."""

    def __init__(
        self,
        trade_date: str,
        issues: dict[str, str],
        *,
        requests_made: int,
        fallback_requests_made: int,
        persisted_rows: int,
    ) -> None:
        self.trade_date = trade_date
        self.issues = issues
        self.requests_made = requests_made
        self.fallback_requests_made = fallback_requests_made
        self.persisted_rows = persisted_rows
        super().__init__(f"Minute mirror received an incomplete batch for {trade_date}: {issues}")


@dataclass
class _DayResolution:
    skip: bool
    progress: _MinuteProgress
    expected_symbols: set[str]
    existing: pd.DataFrame
    completed_symbols: set[str]
    sidecar: dict[str, Any] | None
    sidecar_identity: bool
    sidecar_binding: bool


def _is_bound_complete_partition(
    sidecar: dict[str, Any],
    *,
    expected_symbols: set[str],
    sidecar_binding: bool,
) -> bool:
    recorded_complete = {str(value) for value in sidecar.get("completed_symbols", [])}
    partition = sidecar.get("partition")
    if not isinstance(partition, dict):
        return False
    partition_complete = {str(value) for value in partition.get("complete_symbols", [])}
    partition_symbols = {str(value) for value in partition.get("symbols", [])}
    return bool(
        sidecar.get("status") == "complete"
        and expected_symbols.issubset(recorded_complete)
        and expected_symbols.issubset(partition_complete)
        and partition_symbols == expected_symbols
        and sidecar_binding
    )


def _is_resumable_partial_sidecar(
    sidecar: dict[str, Any] | None,
    *,
    sidecar_identity: bool,
    sidecar_binding: bool,
) -> bool:
    return bool(
        sidecar_identity
        and sidecar_binding
        and sidecar is not None
        and sidecar.get("status") == "partial"
    )


def _requires_quarantine(
    files: list[Any],
    sidecar: dict[str, Any] | None,
    sidecar_binding: bool,
) -> bool:
    return bool(files and (sidecar is None or not sidecar_binding))


def _read_or_quarantine_partition(
    part_dir: Any,
    files: list[Any],
    quarantined: list[Any],
) -> pd.DataFrame:
    try:
        if files:
            return _read_existing_partition(part_dir)
        return pd.DataFrame(columns=pd.Index(DEFAULT_MINS_FIELDS))
    except Exception:
        quarantined.extend(_quarantine_partition_files(part_dir))
        return pd.DataFrame(columns=pd.Index(DEFAULT_MINS_FIELDS))


def _build_day_progress(  # noqa: PLR0913
    pro: Any,
    options: MinsMirrorOptions,
    policy: Any,
    output_dir: Any,
    trade_date: str,
    explicit_symbols: list[str],
    exchange: str | None,
    stock_history: pd.DataFrame,
) -> tuple[Any, _MinuteProgress, set[str], str, str]:
    """Read the sidecar and resolve the expected universe + progress record.

    Returns ``(sidecar, progress, expected_symbols, universe_rule, universe_source)``
    so the caller can run skip checks without recomputing the universe.
    """
    # Resolve ``_traded_symbols_from_provider`` through the public re-export shell
    # so that tests can monkeypatch ``mins._traded_symbols_from_provider``.
    from market_data_platform.providers import tushare_a_share_mins as _shell

    part_dir = output_dir / f"trade_date={trade_date}"
    sidecar = _read_completeness(part_dir)
    active_symbols = _historical_symbols(stock_history, trade_date=trade_date)
    traded_symbols = _shell._traded_symbols_from_provider(
        pro,
        trade_date=trade_date,
        policy=policy,
        stock_history=stock_history,
        exchange=exchange,
    )
    universe = _resolve_minute_universe(
        _UniverseInputs(
            stock_history=stock_history,
            trade_date=trade_date,
            active_symbols=active_symbols,
            traded_symbols=traded_symbols,
            explicit_symbols=explicit_symbols,
            exchange=exchange,
        )
    )
    expected_symbols = universe.symbols
    universe_rule = universe.rule
    universe_source = universe.source
    if options.provider_no_data_exceptions_path is not None:
        exclusions = load_provider_no_data_exclusions(
            options.provider_no_data_exceptions_path,
            trade_date=trade_date,
        )
        expected_symbols.difference_update(exclusions.codes)
        universe_rule = (
            f"{universe_rule}:provider_no_data_exceptions={exclusions.policy_sha256[:16]}"
        )
        universe_source = f"{universe_source}:audited_provider_no_data_exclusions"
    if not expected_symbols:
        raise ValueError(f"No acquisition symbols remain for {trade_date}")
    progress = _MinuteProgress(
        part_dir=part_dir,
        trade_date=trade_date,
        freq=options.freq,
        universe_hash=_universe_hash(expected_symbols, rule=universe_rule),
        universe_rule=universe_rule,
        universe_source=universe_source,
        expected_symbols=frozenset(expected_symbols),
        request_policy=policy,
    )
    return sidecar, progress, expected_symbols, universe_rule, universe_source


def _fetch_validated_minute_batch(  # noqa: PLR0913
    pro: Any,
    symbols: list[str],
    trade_date: str,
    freq: str,
    policy: Any,
    quota_ledger: Any | None,
    *,
    cooldown_seconds: float,
) -> tuple[list[pd.DataFrame], set[str], dict[str, str], int]:
    """Fetch a batch and bisect incomplete responses down to individual symbols."""

    request_symbols = [bse_minute_request_symbol(symbol, trade_date) for symbol in symbols]
    raw = _fetch_minute_batch(
        pro,
        request_symbols,
        trade_date,
        freq,
        policy,
        quota_ledger,
    )
    if not raw.empty and "ts_code" in raw.columns:
        canonical_by_request = dict(zip(request_symbols, symbols, strict=True))
        raw = raw.copy()
        response_symbols = raw["ts_code"].astype(str).str.strip().str.upper()
        raw["ts_code"] = response_symbols.map(canonical_by_request).fillna(response_symbols)
    frame, valid_symbols, issues = _validate_batch_frame(
        raw,
        requested_symbols=symbols,
        trade_date=trade_date,
    )
    valid_frame = frame[frame["ts_code"].isin(valid_symbols)]
    frames = [valid_frame] if not valid_frame.empty else []
    request_calls = 1
    if not issues or len(symbols) == 1 or not valid_symbols:
        return frames, valid_symbols, issues, request_calls

    unresolved_symbols = [symbol for symbol in symbols if symbol in issues]
    midpoint = max(1, len(unresolved_symbols) // 2)
    children = [unresolved_symbols[:midpoint], unresolved_symbols[midpoint:]]
    unresolved: dict[str, str] = {}
    for child in children:
        if not child:
            continue
        if cooldown_seconds > 0:
            time.sleep(cooldown_seconds)
        child_frames, child_valid, child_issues, child_calls = _fetch_validated_minute_batch(
            pro,
            child,
            trade_date,
            freq,
            policy,
            quota_ledger,
            cooldown_seconds=cooldown_seconds,
        )
        frames.extend(child_frames)
        valid_symbols.update(child_valid)
        unresolved.update(child_issues)
        request_calls += child_calls
    return frames, valid_symbols, unresolved, request_calls


def _fetch_minute_batch(  # noqa: PLR0913
    pro: Any,
    symbols: list[str],
    trade_date: str,
    freq: str,
    policy: Any,
    quota_ledger: Any | None = None,
) -> pd.DataFrame:
    """Fetch one batch with the shared retry and proxy policy."""
    # Resolve ``datetime`` through the public re-export shell so that tests can
    # monkeypatch ``mins.datetime`` (the original single-module global).
    from market_data_platform.providers import tushare_a_share_mins as _shell
    from market_data_platform.providers.tushare_a_share import (
        _call_tushare_api,
        _is_daily_quota_exhausted,
    )

    start_dt = f"{trade_date} 09:00:00"
    end_dt = f"{trade_date} 15:30:00"
    joined_symbols = ",".join(symbols)
    request_quota_date: str | None = None

    def physical_request() -> Any:
        return pro.stk_mins(
            ts_code=joined_symbols,
            freq=freq,
            start_date=start_dt,
            end_date=end_dt,
            fields=",".join(DEFAULT_MINS_FIELDS),
            limit=8_000,
        )

    def request() -> Any:
        nonlocal request_quota_date
        if quota_ledger is None:
            return physical_request()
        request_instant = _shell.datetime.now(UTC)
        request_quota_date = quota_date_at(request_instant)
        with quota_ledger.attempt(
            len(symbols) * MINUTE_BARS_PER_DAY, now=request_instant
        ) as attempt:
            raw = physical_request()
            attempt.commit(0 if raw is None else len(raw))
            return raw

    try:
        df = _call_tushare_api(request, policy=policy)
    except Exception as exc:
        if _is_daily_quota_exhausted(exc) and quota_ledger is not None:
            reason = "provider_daily_request_capacity_exhausted"
            if request_quota_date is None:
                raise AmbiguousMinuteRequestError(
                    "TuShare reported daily exhaustion without a quota reservation"
                ) from exc
            quota_ledger.close_pool(reason, quota_date=request_quota_date)
            raise MinuteQuotaPoolClosed(
                quota_date=request_quota_date,
                consumer=quota_ledger.config.consumer,
                reason=reason,
            ) from exc
        if isinstance(exc, OSError) and str(exc).strip().upper() == "ERROR.":
            raise AmbiguousMinuteRequestError(
                "TuShare minute quota or transport error hidden by the SDK (ERROR.)"
            ) from exc
        raise
    return pd.DataFrame() if df is None else df.copy()


def _paged_date_symbol_rows(
    pro: Any,
    *,
    api_name: str,
    trade_date: str,
    policy: Any,
) -> pd.DataFrame:
    # Resolve the pagination bounds from the public re-export shell so that
    # tests (and callers) can monkeypatch ``DAILY_PAGE_SIZE`` /
    # ``MAX_DAILY_PAGES`` on the ``tushare_a_share_mins`` module, matching the
    # original single-module layout where these were module globals.
    from market_data_platform.providers import tushare_a_share_mins as _shell
    from market_data_platform.providers.tushare_a_share import _call_tushare_api

    api = getattr(pro, api_name)
    pages: list[pd.DataFrame] = []
    for page_index in range(_shell.MAX_DAILY_PAGES):
        offset = page_index * _shell.DAILY_PAGE_SIZE
        response = _call_tushare_api(
            lambda offset=offset: api(
                trade_date=trade_date,
                fields="ts_code,trade_date",
                limit=_shell.DAILY_PAGE_SIZE,
                offset=offset,
            ),
            policy=policy,
        )
        if response is None:
            raise RuntimeError(f"TuShare {api_name} returned None for {trade_date} offset={offset}")
        if response.empty:
            break
        if len(response) > _shell.DAILY_PAGE_SIZE:
            raise RuntimeError(
                f"TuShare {api_name} exceeded requested page size for {trade_date}: {len(response)}"
            )
        pages.append(response.copy())
        if len(response) < _shell.DAILY_PAGE_SIZE:
            break
    else:
        raise RuntimeError(f"TuShare {api_name} pagination exceeded {_shell.MAX_DAILY_PAGES} pages")

    if pages:
        return pd.concat(pages, ignore_index=True)
    raise RuntimeError(f"TuShare {api_name} returned no traded universe for {trade_date}")


def _paged_date_symbols_from_provider(
    pro: Any,
    *,
    api_name: str,
    trade_date: str,
    policy: Any,
) -> set[str]:
    response = _paged_date_symbol_rows(
        pro,
        api_name=api_name,
        trade_date=trade_date,
        policy=policy,
    )
    missing = {"ts_code", "trade_date"} - set(response.columns)
    if missing:
        raise ValueError(f"TuShare {api_name} for {trade_date} missing fields: {sorted(missing)}")
    dates = _normalize_stock_dates(response["trade_date"])
    if not dates.eq(trade_date).all():
        raise ValueError(f"TuShare {api_name} returned rows outside {trade_date}")
    symbols = response["ts_code"].astype(str).str.strip().str.upper()
    if symbols.duplicated().any():
        raise RuntimeError(
            f"TuShare {api_name} pagination returned duplicate symbols for {trade_date}"
        )
    invalid = sorted({symbol for symbol in symbols if not _is_cn_stock_ts_code(symbol)})
    if invalid:
        raise ValueError(f"TuShare {api_name} returned invalid stock codes: {invalid}")
    return set(symbols)


def _traded_symbols_from_provider(
    pro: Any,
    *,
    trade_date: str,
    policy: Any,
    stock_history: pd.DataFrame,
    exchange: str | None = None,
) -> set[str]:
    daily = _paged_date_symbols_from_provider(
        pro, api_name="daily", trade_date=trade_date, policy=policy
    )
    daily_basic = _paged_date_symbols_from_provider(
        pro, api_name="daily_basic", trade_date=trade_date, policy=policy
    )
    if exchange is not None:
        suffixes = (".SH", ".SZ") if exchange == "SH_SZ" else (f".{exchange}",)
        daily = {symbol for symbol in daily if symbol.endswith(suffixes)}
        daily_basic = {symbol for symbol in daily_basic if symbol.endswith(suffixes)}
    daily, _daily_transitions = _apply_historical_code_transitions(daily, trade_date=trade_date)
    daily_basic, _daily_basic_transitions = _apply_historical_code_transitions(
        daily_basic, trade_date=trade_date
    )
    all_stock_symbols = set(
        stock_history.attrs.get("all_stock_symbols", set(stock_history["ts_code"]))
    )
    unknown_daily = daily - all_stock_symbols
    provider_verified_only = unknown_daily & daily_basic
    unverified_unknown = unknown_daily - provider_verified_only
    if unverified_unknown:
        raise RuntimeError(
            "TuShare daily returned symbols absent from the CNY stock_basic master: "
            f"{sorted(unverified_unknown)}"
        )
    # Historical daily endpoints can retain valid delisted symbols that the
    # current stock_basic snapshot no longer publishes.  Accept only the
    # intersection of daily and daily_basic, and carry it explicitly to the
    # universe resolver; an unknown symbol present in only one endpoint still
    # fails closed above/below.
    stock_history.attrs["provider_verified_only_symbols"] = provider_verified_only
    a_share_master = set(stock_history["ts_code"])
    daily.intersection_update(a_share_master)
    daily_basic.intersection_update(a_share_master)
    daily.update(provider_verified_only)
    missing_from_daily = daily_basic - daily
    unexplained_daily_only = {
        symbol
        for symbol in daily - daily_basic
        if symbol not in provider_verified_only and not symbol.endswith(".BJ")
    }
    if missing_from_daily or unexplained_daily_only:
        raise RuntimeError(
            f"TuShare daily universe mismatch for {trade_date}: "
            f"missing_from_daily={sorted(missing_from_daily)} "
            f"unexplained_daily_only={sorted(unexplained_daily_only)}"
        )
    if not daily:
        raise RuntimeError(f"Daily endpoints returned no CNY A-share symbols for {trade_date}")
    return daily
