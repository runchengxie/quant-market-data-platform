"""Shared constants and dataclasses for the TuShare A-share minute mirror.

This leaf module holds the module-level constants and dataclasses used by the
minute-mirror submodules (``_a_share_mins_universe`` and ``_a_share_mins_partition``)
and by the public re-export shell ``tushare_a_share_mins``.  It must not import
from either submodule, so that the submodules and the shell can both import from
it without creating a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa

from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy

MINS_OUTPUT_SUBDIR = Path("assets") / "tushare" / "a_share" / "mins"
DEFAULT_FREQ = "1min"
DEFAULT_MINS_FIELDS = (
    "ts_code",
    "trade_time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
)
MINUTE_BARS_PER_DAY = 241  # TuShare includes a 09:30-labelled bar
DEFAULT_MINS_BATCH_SIZE = 20
MINS_RESPONSE_ROW_LIMIT = 8_000
# A full 1min stock-day has exactly 241 rows.  33 * 241 = 7953 fits, while
# 34 * 241 = 8194 would exceed the endpoint's current per-response row limit.
MAX_MINS_BATCH_SIZE = MINS_RESPONSE_ROW_LIMIT // MINUTE_BARS_PER_DAY
COMPLETENESS_FILENAME = "_minute_mirror.json"
COMPLETENESS_SCHEMA_VERSION = "tushare.a_share.minute_partition.v3"
UNIVERSE_RULE = (
    "daily_crosschecked_daily_basic_SH_SZ_allow_daily_only_BJ_intersect_stock_basic:L,D,P,G:"
    "list_date<=trade_date<=delist_date_or_open:exclude_B_share:v1"
)
HISTORICAL_CODE_TRANSITION_UNIVERSE_RULE = f"{UNIVERSE_RULE}:historical_code_transitions=v1"
FULL_DAILY_UNIVERSE_RULES = frozenset({UNIVERSE_RULE, HISTORICAL_CODE_TRANSITION_UNIVERSE_RULE})
EXPLICIT_UNIVERSE_RULE = "explicit_symbols_validated_by_CNY_stock_basic_and_daily:v2"
STOCK_LIST_STATUSES = ("L", "D", "P", "G")
# TuShare's historical daily endpoints retain the pre-change code while the
# live stock_basic master only exposes the successor.  Keep the source-native
# historical code and use an explicit interval to avoid double-counting when
# both aliases are returned for an old trading day.
HISTORICAL_CODE_TRANSITIONS = (
    {
        "historical": "300114.SZ",
        "successor": "302132.SZ",
        "historical_last_trade_date": "20250214",
        "successor_first_trade_date": "20250217",
    },
)
DAILY_PAGE_SIZE = 5_000
MAX_DAILY_PAGES = 10
PARTITION_KEY = ("ts_code", "trade_time")
EXPECTED_MINUTES_OF_DAY = frozenset(
    [*range(9 * 60 + 30, 11 * 60 + 31), *range(13 * 60 + 1, 15 * 60 + 1)]
)
MINS_ARROW_SCHEMA = pa.schema(
    [
        pa.field("ts_code", pa.string(), nullable=False),
        pa.field("trade_time", pa.timestamp("ns"), nullable=False),
        pa.field("open", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("vol", pa.float64()),
        pa.field("amount", pa.float64()),
    ]
)


@dataclass
class MinsMirrorOptions:
    """Options for mirroring minute-level OHLCV bars."""

    start_date: str  # YYYYMMDD
    end_date: str  # YYYYMMDD
    freq: str = DEFAULT_FREQ
    symbols: list[str] | None = None  # If None, mirror each date's traded A-share universe
    output_dir: str | Path | None = None  # Override default output path
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    request_policy: TushareRequestPolicy | None = None
    skip_existing: bool = True  # Resume complete/partial partitions by default
    batch_size: int = DEFAULT_MINS_BATCH_SIZE  # Conservative default; validated max is 33
    cooldown_seconds: float = 0.3  # Sleep between API calls
    gc_frequency: int = 100  # gc.collect() every N stocks
    exchange: str | None = None  # Optional daily traded-universe suffix filter: SH/SZ/BJ
    trading_dates: list[str] | None = None  # Optional immutable local-calendar selection
    continue_on_partial_dates: bool = False  # Backfills may isolate one bad day and continue
    provider_no_data_exceptions_path: str | Path | None = None
    minute_quota_mode: str | None = None  # off/observe/enforce; unset resolves from environment
    minute_quota_db: str | Path | None = None  # Shared across every consumer of the token
    minute_quota_consumer: str | None = None
    minute_quota_limit_rows: int | None = None
    minute_quota_safety_rows: int | None = None
    minute_quota_gate: str | None = None
    minute_quota_limit_requests: int | None = None
    minute_quota_burst_limit_requests: int | None = None
    minute_quota_safety_requests: int | None = None
    minute_quota_allow_burst: bool | str | None = None


@dataclass(frozen=True)
class _MinuteProgress:
    part_dir: Path
    trade_date: str
    freq: str
    universe_hash: str
    universe_rule: str
    universe_source: str
    expected_symbols: frozenset[str]
    request_policy: TushareRequestPolicy


@dataclass(frozen=True)
class _UniverseInputs:
    stock_history: object  # pd.DataFrame; typed loosely to avoid a pandas import cycle here
    trade_date: str
    active_symbols: set[str]
    traded_symbols: set[str]
    explicit_symbols: list[str]
    exchange: str | None


@dataclass(frozen=True)
class _ResolvedUniverse:
    symbols: set[str]
    rule: str
    source: str
