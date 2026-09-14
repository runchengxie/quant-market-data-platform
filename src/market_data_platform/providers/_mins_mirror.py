"""TuShare A-share minute mirror: day-resolution and mirror orchestration.

This submodule holds the higher-level mirror orchestration symbols (per-date
resolution, per-date fetch, trading-date resolution, the public
``mirror_minute_bars`` entry point) used by the public re-export shell
``tushare_a_share_mins``.  It imports the lower-level fetch/validation helpers
from ``_mins_fetch``; ``_mins_fetch`` must never import back from here, so the
two submodules and the shell can be imported without a circular dependency.

Only symbols are moved here from the original single-file ``tushare_a_share_mins``;
no function logic is changed.
"""

from __future__ import annotations

import gc
import os
import time
from collections.abc import Mapping
from typing import Any, cast

import pandas as pd

from market_data_platform.providers._a_share_mins_constants import (
    DEFAULT_MINS_FIELDS,
    MinsMirrorOptions,
    _MinuteProgress,
)
from market_data_platform.providers._a_share_mins_partition import (
    _complete_symbols,
    _is_cn_stock_ts_code,
    _partition_binding_matches,
    _partition_files,
    _persist_progress,
    _quarantine_partition_files,
    _redacted_error_message,
    _sidecar_identity_matches,
)
from market_data_platform.providers._a_share_mins_universe import (
    _build_mins_quota_ledger,
    _build_output_dir,
    _chunks,
    _parse_symbols,
    _validate_mirror_options,
)
from market_data_platform.providers.tushare_a_share_dates import _validate_date
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
from market_data_platform.tushare_minute_quota import (
    AmbiguousMinuteRequestError,
    MinuteQuotaExceeded,
    MinuteQuotaPoolClosed,
)

from ._mins_fetch import (
    _build_day_progress,
    _DayResolution,
    _fetch_validated_minute_batch,
    _is_bound_complete_partition,
    _is_resumable_partial_sidecar,
    _MinutePartitionIncompleteError,
    _read_or_quarantine_partition,
    _requires_quarantine,
)


class MinuteMirrorIncompleteDatesError(RuntimeError):
    """A multi-date mirror isolated partial dates and completed the remaining dates."""

    def __init__(self, partial_dates: list[str], result: dict[str, Any]) -> None:
        self.partial_dates = tuple(partial_dates)
        self.result = result
        super().__init__(
            "Minute mirror completed with partial dates: " + ",".join(self.partial_dates)
        )


def _try_bound_complete_skip(  # noqa: PLR0913
    *,
    options: MinsMirrorOptions,
    sidecar: Any,
    sidecar_identity: bool,
    sidecar_binding: bool,
    expected_symbols: set[str],
    progress: _MinuteProgress,
    trade_date: str,
) -> _DayResolution | None:
    """Fast-path: a bound, complete partition already covers the universe.

    Returns a skip resolution when ``skip_existing`` is set and the existing
    bound partition is complete for every expected symbol; otherwise ``None``.
    """
    if not (options.skip_existing and sidecar_identity and sidecar is not None):
        return None
    partition = sidecar.get("partition")
    partition_complete = (
        set(partition.get("complete_symbols", [])) if isinstance(partition, dict) else set()
    )
    if _is_bound_complete_partition(
        sidecar,
        expected_symbols=expected_symbols,
        sidecar_binding=sidecar_binding,
    ) and expected_symbols.issubset(partition_complete):
        print(f"[mins] {trade_date}: bound complete partition exists; skipping", flush=True)
        return _DayResolution(
            skip=True,
            progress=progress,
            expected_symbols=expected_symbols,
            existing=pd.DataFrame(columns=pd.Index(DEFAULT_MINS_FIELDS)),
            completed_symbols=set(),
            sidecar=sidecar,
            sidecar_identity=sidecar_identity,
            sidecar_binding=sidecar_binding,
        )
    return None


def _try_validated_existing_skip(  # noqa: PLR0913
    *,
    options: MinsMirrorOptions,
    expected_symbols: set[str],
    completed_symbols: set[str],
    existing: pd.DataFrame,
    progress: _MinuteProgress,
    sidecar: Any,
    sidecar_identity: bool,
    sidecar_binding: bool,
    trade_date: str,
) -> _DayResolution | None:
    """Fast-path: an existing partition already holds every expected symbol.

    Returns a skip resolution when ``skip_existing`` is set and the completed
    symbols cover the universe; otherwise ``None``.
    """
    if not (options.skip_existing and expected_symbols.issubset(completed_symbols)):
        return None
    _persist_progress(
        progress,
        completed_symbols=completed_symbols,
        frames=[existing],
        status="complete",
    )
    print(f"[mins] {trade_date}: validated existing partition; skipping", flush=True)
    return _DayResolution(
        skip=True,
        progress=progress,
        expected_symbols=expected_symbols,
        existing=existing,
        completed_symbols=completed_symbols,
        sidecar=sidecar,
        sidecar_identity=sidecar_identity,
        sidecar_binding=sidecar_binding,
    )


def _mirror_resolve_day(  # noqa: PLR0913
    pro: Any,
    options: MinsMirrorOptions,
    policy: Any,
    output_dir: Any,
    trade_date: str,
    explicit_symbols: list[str],
    exchange: str | None,
    stock_history: pd.DataFrame,
) -> _DayResolution:
    """Resolve one trade date's universe and prepare its partition for mirroring.

    Handles sidecar identity/binding checks, quarantine of untrusted partitions,
    completion-resume bookkeeping, and the two early-skip fast paths.  Returns a
    :class:`_DayResolution` whose ``skip`` flag tells the caller to skip the date.
    """
    sidecar, progress, expected_symbols, universe_rule, universe_source = _build_day_progress(
        pro,
        options,
        policy,
        output_dir,
        trade_date,
        explicit_symbols,
        exchange,
        stock_history,
    )

    part_dir = progress.part_dir
    sidecar_identity = _sidecar_identity_matches(
        sidecar,
        trade_date=trade_date,
        freq=options.freq,
        universe_hash=progress.universe_hash,
        universe_rule=universe_rule,
    )
    sidecar_binding = bool(
        sidecar_identity and sidecar is not None and _partition_binding_matches(sidecar, part_dir)
    )

    skip = _try_bound_complete_skip(
        options=options,
        sidecar=sidecar,
        sidecar_identity=sidecar_identity,
        sidecar_binding=sidecar_binding,
        expected_symbols=expected_symbols,
        progress=progress,
        trade_date=trade_date,
    )
    if skip is not None:
        return skip

    files = _partition_files(part_dir)
    if _requires_quarantine(files, sidecar, sidecar_binding):
        quarantined = _quarantine_partition_files(part_dir)
    else:
        quarantined = []
    existing = _read_or_quarantine_partition(part_dir, files, quarantined)
    _complete_symbols(existing, trade_date=trade_date)

    if not options.skip_existing:
        completed_symbols: set[str] = set()
    elif _is_resumable_partial_sidecar(
        sidecar, sidecar_identity=sidecar_identity, sidecar_binding=sidecar_binding
    ):
        # A partial sidecar is authoritative. Old rows can remain in a force-refresh
        # partition, but only symbols completed by that run may be resumed as done.
        completed_symbols = {str(value) for value in sidecar["completed_symbols"]}
        completed_symbols.intersection_update(
            _complete_symbols(existing, trade_date=trade_date), expected_symbols
        )
    else:
        completed_symbols = set()

    if quarantined and options.skip_existing:
        _persist_progress(
            progress,
            completed_symbols=set(),
            frames=[],
            status="partial",
            error={
                "type": "UntrustedPartitionQuarantined",
                "files": [path.name for path in quarantined],
            },
        )

    if not options.skip_existing:
        # Invalidate an older complete sidecar before the first force-refresh
        # request. An external interruption can no longer expose old data as
        # if the forced refresh had completed.
        _persist_progress(
            progress,
            completed_symbols=set(),
            frames=[existing],
            status="partial",
            error={"type": "ForceRefreshStarted"},
        )

    skip = _try_validated_existing_skip(
        options=options,
        expected_symbols=expected_symbols,
        completed_symbols=completed_symbols,
        existing=existing,
        progress=progress,
        sidecar=sidecar,
        sidecar_identity=sidecar_identity,
        sidecar_binding=sidecar_binding,
        trade_date=trade_date,
    )
    if skip is not None:
        return skip

    return _DayResolution(
        skip=False,
        progress=progress,
        expected_symbols=expected_symbols,
        existing=existing,
        completed_symbols=completed_symbols,
        sidecar=sidecar,
        sidecar_identity=sidecar_identity,
        sidecar_binding=sidecar_binding,
    )


def _mirror_fetch_day(  # noqa: PLR0913
    pro: Any,
    options: MinsMirrorOptions,
    policy: Any,
    quota_ledger: Any,
    trade_date: str,
    completed_symbols: set[str],
    existing: pd.DataFrame,
    progress: _MinuteProgress,
) -> tuple[list[pd.DataFrame], set[str], int, int]:
    """Fetch one trade date's pending batches.

    Returns the list of valid per-batch frames, the updated completed-symbol
    set, the planned-batch request count, and fallback request count.
    """
    pending_symbols = sorted(set(progress.expected_symbols) - completed_symbols)
    print(
        f"[mins] {trade_date}: fetching {len(pending_symbols)}/"
        f"{len(progress.expected_symbols)} stocks in batches of {options.batch_size} ...",
        flush=True,
    )
    bars: list[pd.DataFrame] = []
    processed_symbols = 0
    next_gc = options.gc_frequency
    requests_made = 0
    fallback_requests_made = 0
    unresolved_issues: dict[str, str] = {}
    for batch in _chunks(pending_symbols, options.batch_size):
        try:
            frames, valid_symbols, issues, request_calls = _fetch_validated_minute_batch(
                pro,
                batch,
                trade_date,
                options.freq,
                policy,
                quota_ledger,
                cooldown_seconds=options.cooldown_seconds,
            )
        except BaseException as exc:
            _persist_progress(
                progress,
                completed_symbols=completed_symbols,
                frames=[existing, *bars],
                status="partial",
                error={
                    "type": type(exc).__name__,
                    "message": _redacted_error_message(
                        exc,
                        secrets=(os.environ.get(options.token_env), options.api_url),
                    ),
                    "symbols": batch,
                },
            )
            if not isinstance(exc, Exception):
                raise
            if isinstance(
                exc,
                (AmbiguousMinuteRequestError, MinuteQuotaExceeded, MinuteQuotaPoolClosed),
            ):
                raise
            raise RuntimeError(
                f"Minute mirror stopped with a recoverable partial partition for {trade_date}; "
                f"failed symbols={','.join(batch)}"
            ) from exc

        requests_made += 1
        fallback_requests_made += max(0, request_calls - 1)
        bars.extend(frame for frame in frames if not frame.empty)
        completed_symbols.update(valid_symbols)
        if issues:
            unresolved_issues.update(issues)
        processed_symbols += len(batch)
        try:
            if options.cooldown_seconds > 0:
                time.sleep(options.cooldown_seconds)
            if processed_symbols >= next_gc:
                gc.collect()
                next_gc += options.gc_frequency
        except (KeyboardInterrupt, SystemExit):
            _persist_progress(
                progress,
                completed_symbols=completed_symbols,
                frames=[existing, *bars],
                status="partial",
                error={"type": "InterruptedAfterBatch", "symbols": batch},
            )
            raise

    if unresolved_issues:
        day_df, _partition = _persist_progress(
            progress,
            completed_symbols=completed_symbols,
            frames=[existing, *bars],
            status="partial",
            error={
                "type": "IncompleteBatch",
                "issues": unresolved_issues,
                "symbols": sorted(unresolved_issues),
                "fallback_requests_made": fallback_requests_made,
            },
        )
        raise _MinutePartitionIncompleteError(
            trade_date,
            unresolved_issues,
            requests_made=requests_made,
            fallback_requests_made=fallback_requests_made,
            persisted_rows=len(day_df),
        )

    return bars, completed_symbols, requests_made, fallback_requests_made


def _resolve_mirror_trading_dates(
    pro: Any,
    options: MinsMirrorOptions,
    policy: TushareRequestPolicy,
) -> list[str]:
    """Resolve the calendar trading dates to mirror.

    When ``options.trading_dates`` is provided it is validated directly;
    otherwise the provider is queried for the ``[start_date, end_date]`` window.
    """
    # Resolve ``_trading_dates_from_provider`` through the public re-export shell
    # so that tests can monkeypatch ``mins._trading_dates_from_provider``.
    from market_data_platform.providers import tushare_a_share_mins as _shell

    if options.trading_dates is None:
        return _shell._trading_dates_from_provider(
            pro,
            start_date=options.start_date,
            end_date=options.end_date,
            policy=policy,
        )
    cal_dates = [_validate_date(str(value)) for value in options.trading_dates]
    if len(cal_dates) != len(set(cal_dates)) or cal_dates not in (
        sorted(cal_dates),
        sorted(cal_dates, reverse=True),
    ):
        raise ValueError("trading_dates must be sorted and unique")
    outside_range = [
        value for value in cal_dates if value < options.start_date or value > options.end_date
    ]
    if outside_range:
        raise ValueError(f"trading_dates outside requested range: {outside_range}")
    return cal_dates


def _mirror_one_trade_date(  # noqa: PLR0913
    pro: Any,
    options: MinsMirrorOptions,
    policy: TushareRequestPolicy,
    quota_ledger: Any,
    output_dir: Any,
    trade_date: str,
    explicit_symbols: list[str],
    exchange: str | None,
    stock_history: pd.DataFrame,
) -> tuple[int, int, int, int]:
    """Mirror one date and return bars, planned requests, fallback requests, skipped.

    ``skipped`` is 1 when the day is already complete and skipped, else 0.
    """
    resolution = _mirror_resolve_day(
        pro,
        options,
        policy,
        output_dir,
        trade_date,
        explicit_symbols,
        exchange,
        stock_history,
    )
    if resolution.skip:
        return 0, 0, 0, 1

    bars, completed_symbols, day_requests, fallback_requests = _mirror_fetch_day(
        pro,
        options,
        policy,
        quota_ledger,
        trade_date,
        resolution.completed_symbols,
        resolution.existing,
        resolution.progress,
    )
    day_df, partition = _persist_progress(
        resolution.progress,
        completed_symbols=completed_symbols,
        frames=[resolution.existing, *bars],
        status="complete",
    )
    assert partition is not None
    print(
        f"  → {len(day_df):,} bars written to "
        f"{resolution.progress.part_dir / 'part-00000.parquet'}",
        flush=True,
    )
    gc.collect()
    return len(day_df), day_requests, fallback_requests, 0


def mirror_minute_bars(options: MinsMirrorOptions | None = None, **kwargs: Any) -> dict[str, Any]:
    """Mirror A-share minute-level OHLCV bars from TuShare.

    Returns a dict with keys: ``dates_fetched``, ``dates_skipped``,
    ``total_bars``, ``output_dir``.
    """
    if options is None:
        options = MinsMirrorOptions(**kwargs)
    if isinstance(options, dict):
        options = MinsMirrorOptions(**cast(Mapping[str, Any], options))

    exchange = _validate_mirror_options(options)
    if options.request_policy is not None:
        policy = options.request_policy
    else:
        policy = TushareRequestPolicy()

    # Imported lazily so tests can monkeypatch ``get_tushare_client`` on the
    # ``tushare_a_share`` module, mirroring the original single-file layout.
    from market_data_platform.providers.tushare_a_share import get_tushare_client

    pro = get_tushare_client(
        token_env=options.token_env,
        api_url=options.api_url,
        disable_proxy=policy.disable_proxy,
        request_timeout_seconds=policy.request_timeout_seconds,
    )
    quota_ledger = _build_mins_quota_ledger(options)

    output_dir = _build_output_dir(options.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    explicit_symbols = _parse_symbols(options.symbols)
    invalid_symbols = [symbol for symbol in explicit_symbols if not _is_cn_stock_ts_code(symbol)]
    if invalid_symbols:
        raise ValueError(f"Invalid A-share symbols: {invalid_symbols}")

    stock_history: pd.DataFrame | None = None
    cal_dates = _resolve_mirror_trading_dates(pro, options, policy)

    # Resolve ``_stock_history_from_provider`` through the public re-export
    # shell so that tests can monkeypatch ``mins._stock_history_from_provider``
    # (the original single-module global).
    from market_data_platform.providers import tushare_a_share_mins as _shell

    total_bars = 0
    dates_fetched = 0
    dates_skipped = 0
    requests_made = 0
    fallback_requests_made = 0
    partial_dates: list[str] = []

    for trade_date in cal_dates:
        if stock_history is None:
            stock_history = _shell._stock_history_from_provider(pro, policy)
        try:
            bars, day_requests, day_fallback_requests, skipped = _mirror_one_trade_date(
                pro,
                options,
                policy,
                quota_ledger,
                output_dir,
                trade_date,
                explicit_symbols,
                exchange,
                stock_history,
            )
        except _MinutePartitionIncompleteError as exc:
            if not options.continue_on_partial_dates:
                raise
            total_bars += exc.persisted_rows
            requests_made += exc.requests_made
            fallback_requests_made += exc.fallback_requests_made
            partial_dates.append(trade_date)
            continue
        total_bars += bars
        requests_made += day_requests
        fallback_requests_made += day_fallback_requests
        dates_skipped += skipped
        dates_fetched += 0 if skipped else 1

    result = {
        "dates_fetched": dates_fetched,
        "dates_skipped": dates_skipped,
        "dates_partial": len(partial_dates),
        "partial_dates": partial_dates,
        "requests_made": requests_made,
        "fallback_requests_made": fallback_requests_made,
        "total_bars": total_bars,
        "output_dir": str(output_dir),
    }
    if partial_dates:
        raise MinuteMirrorIncompleteDatesError(partial_dates, result)
    return result
