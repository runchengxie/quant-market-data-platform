"""TuShare A-share minute-level OHLCV mirror provider.

Downloads 1-minute OHLCV bars via ``pro.mins`` and stores them as
Hive-partitioned Parquet under ``assets/tushare/a_share/mins/``.

Data format (per bar)
---------------------
ts_code, trade_time, open, close, high, low, vol, amount

Storage layout
--------------
mins/trade_date=YYYYMMDD/part-XXXXX.parquet

Each partition contains all minute bars for the A-shares present in that day's
paginated ``daily`` traded universe, validated against point-in-time stock
listing intervals.
Incremental refresh uses a per-partition completeness sidecar and only treats a
symbol as complete after exactly 241 unique bars pass date/key validation.

Rate limits
-----------
TuShare minute requests support comma-separated stock batches.  At the default
batch size of 20, a full market day (~5000 stocks) requires ~250 API calls.  The
validated maximum is 33 stocks: 33 * 241 = 7953 rows stays below the endpoint's
8000-row response limit.

Usage
-----
.. code-block:: bash

    marketdata tushare mirror mins \\
        --start 20260101 --end 20260424 \\
        --symbols 000001.SZ,000002.SZ \\
        --freq 1min

    # Or via Python:
    from market_data_platform.providers.tushare_a_share_mins import (
        mirror_minute_bars,
        MinsMirrorOptions,
    )
    result = mirror_minute_bars(
        MinsMirrorOptions(
            start_date="20260101",
            end_date="20260424",
            freq="1min",
        )
    )

This module is a public re-export shell.  The implementation was split into the
``_mins_mirror`` (orchestration / day resolution) and ``_mins_fetch`` (fetch and
validation helpers) submodules; all previously importable names are re-exported
here so the public contract is unchanged.
"""

from __future__ import annotations

from datetime import datetime

from market_data_platform.providers._a_share_mins_constants import (
    COMPLETENESS_FILENAME,
    COMPLETENESS_SCHEMA_VERSION,
    DAILY_PAGE_SIZE,
    DEFAULT_FREQ,
    DEFAULT_MINS_BATCH_SIZE,
    DEFAULT_MINS_FIELDS,
    EXPECTED_MINUTES_OF_DAY,
    EXPLICIT_UNIVERSE_RULE,
    FULL_DAILY_UNIVERSE_RULES,
    HISTORICAL_CODE_TRANSITION_UNIVERSE_RULE,
    HISTORICAL_CODE_TRANSITIONS,
    MAX_DAILY_PAGES,
    MAX_MINS_BATCH_SIZE,
    MINS_ARROW_SCHEMA,
    MINS_OUTPUT_SUBDIR,
    MINS_RESPONSE_ROW_LIMIT,
    MINUTE_BARS_PER_DAY,
    PARTITION_KEY,
    STOCK_LIST_STATUSES,
    UNIVERSE_RULE,
    MinsMirrorOptions,
    _MinuteProgress,
)
from market_data_platform.providers._a_share_mins_partition import (
    _atomic_write_json,
    _atomic_write_partition,
    _complete_symbols,
    _exact_partition_receipt,
    _has_expected_minute_grid,
    _has_valid_ohlcv,
    _is_cn_stock_ts_code,
    _normalize_minute_frame,
    _normalize_stock_dates,
    _partition_binding_matches,
    _partition_files,
    _partition_files_match,
    _partition_state,
    _persist_progress,
    _prepare_partition_frame,
    _quarantine_partition_files,
    _read_completeness,
    _read_existing_partition,
    _redacted_error_message,
    _sha256_file,
    _sidecar_identity_matches,
    _sidecar_symbol_inventory,
    _universe_hash,
    _validate_batch_frame,
    _write_completeness,
    validate_complete_minute_partition,
)
from market_data_platform.providers._a_share_mins_universe import (
    _apply_historical_code_transitions,
    _build_mins_quota_ledger,
    _build_output_dir,
    _chunks,
    _historical_symbols,
    _parse_symbols,
    _resolve_default_universe,
    _resolve_explicit_universe,
    _resolve_minute_universe,
    _stock_history_from_provider,
    _trading_dates_from_provider,
    _validate_mirror_options,
    validate_mins_batch_size,
)
from market_data_platform.providers._mins_fetch import (
    _build_day_progress,
    _DayResolution,
    _fetch_minute_batch,
    _fetch_validated_minute_batch,
    _is_bound_complete_partition,
    _is_resumable_partial_sidecar,
    _MinutePartitionIncompleteError,
    _paged_date_symbol_rows,
    _paged_date_symbols_from_provider,
    _read_or_quarantine_partition,
    _requires_quarantine,
    _traded_symbols_from_provider,
)
from market_data_platform.providers._mins_mirror import (
    MinuteMirrorIncompleteDatesError,
    _mirror_fetch_day,
    _mirror_one_trade_date,
    _mirror_resolve_day,
    _resolve_mirror_trading_dates,
    _try_bound_complete_skip,
    _try_validated_existing_skip,
    mirror_minute_bars,
)

__all__ = [
    "MinsMirrorOptions",
    "MINS_OUTPUT_SUBDIR",
    "DEFAULT_FREQ",
    "DEFAULT_MINS_FIELDS",
    "MINUTE_BARS_PER_DAY",
    "DEFAULT_MINS_BATCH_SIZE",
    "MINS_RESPONSE_ROW_LIMIT",
    "MAX_MINS_BATCH_SIZE",
    "COMPLETENESS_FILENAME",
    "COMPLETENESS_SCHEMA_VERSION",
    "UNIVERSE_RULE",
    "HISTORICAL_CODE_TRANSITION_UNIVERSE_RULE",
    "FULL_DAILY_UNIVERSE_RULES",
    "EXPLICIT_UNIVERSE_RULE",
    "STOCK_LIST_STATUSES",
    "HISTORICAL_CODE_TRANSITIONS",
    "DAILY_PAGE_SIZE",
    "MAX_DAILY_PAGES",
    "PARTITION_KEY",
    "EXPECTED_MINUTES_OF_DAY",
    "MINS_ARROW_SCHEMA",
    "datetime",
    "_normalize_minute_frame",
    "_has_expected_minute_grid",
    "_has_valid_ohlcv",
    "_validate_batch_frame",
    "_partition_files",
    "_sha256_file",
    "_redacted_error_message",
    "_quarantine_partition_files",
    "_read_existing_partition",
    "_complete_symbols",
    "_prepare_partition_frame",
    "_read_completeness",
    "_sidecar_identity_matches",
    "_atomic_write_json",
    "_atomic_write_partition",
    "_partition_state",
    "_partition_files_match",
    "_partition_binding_matches",
    "_sidecar_symbol_inventory",
    "_exact_partition_receipt",
    "_universe_hash",
    "_write_completeness",
    "_persist_progress",
    "_is_cn_stock_ts_code",
    "_normalize_stock_dates",
    "_build_output_dir",
    "_chunks",
    "_parse_symbols",
    "_fetch_minute_batch",
    "_stock_history_from_provider",
    "_apply_historical_code_transitions",
    "_historical_symbols",
    "_trading_dates_from_provider",
    "_traded_symbols_from_provider",
    "_resolve_explicit_universe",
    "_resolve_default_universe",
    "_resolve_minute_universe",
    "_build_mins_quota_ledger",
    "_validate_mirror_options",
    "_resolve_mirror_trading_dates",
    "_mirror_one_trade_date",
    "_mirror_resolve_day",
    "_mirror_fetch_day",
    "_try_bound_complete_skip",
    "_try_validated_existing_skip",
    "_DayResolution",
    "_MinutePartitionIncompleteError",
    "_build_day_progress",
    "_fetch_validated_minute_batch",
    "_is_bound_complete_partition",
    "_is_resumable_partial_sidecar",
    "_read_or_quarantine_partition",
    "_requires_quarantine",
    "_paged_date_symbol_rows",
    "_paged_date_symbols_from_provider",
    "_MinuteProgress",
    "MinuteMirrorIncompleteDatesError",
    "mirror_minute_bars",
    "validate_complete_minute_partition",
    "validate_mins_batch_size",
]
