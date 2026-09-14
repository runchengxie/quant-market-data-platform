"""SQLite persistence primitives for shared minute quota accounting."""

from __future__ import annotations

import hmac
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from market_data_platform._tushare_minute_quota_config import (
    MINUTE_QUOTA_TIMEZONE,
    MinuteQuotaConfig,
    MinuteQuotaConfigurationError,
    timestamp,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS quota_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO quota_meta(key, value) VALUES ('schema_version', '2');

CREATE TABLE IF NOT EXISTS quota_pools (
    quota_date TEXT NOT NULL,
    token_fingerprint TEXT NOT NULL,
    limit_rows INTEGER NOT NULL,
    safety_rows INTEGER NOT NULL,
    gate TEXT NOT NULL DEFAULT 'rows',
    limit_requests INTEGER NOT NULL DEFAULT 10000,
    burst_limit_requests INTEGER NOT NULL DEFAULT 20000,
    safety_requests INTEGER NOT NULL DEFAULT 500,
    timezone TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    closed_reason TEXT,
    PRIMARY KEY (quota_date, token_fingerprint)
);

CREATE TABLE IF NOT EXISTS request_attempts (
    reservation_id TEXT PRIMARY KEY,
    quota_date TEXT NOT NULL,
    token_fingerprint TEXT NOT NULL,
    consumer TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('reserved', 'committed', 'uncertain', 'released', 'rejected')
    ),
    requested_rows INTEGER NOT NULL,
    reserved_rows INTEGER NOT NULL,
    committed_rows INTEGER NOT NULL DEFAULT 0,
    over_limit INTEGER NOT NULL DEFAULT 0,
    error_kind TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    lease_expires_at TEXT,
    FOREIGN KEY (quota_date, token_fingerprint)
        REFERENCES quota_pools(quota_date, token_fingerprint)
);
CREATE INDEX IF NOT EXISTS request_attempts_pool_state_idx
    ON request_attempts(quota_date, token_fingerprint, state);
CREATE INDEX IF NOT EXISTS request_attempts_consumer_idx
    ON request_attempts(quota_date, consumer);

CREATE TABLE IF NOT EXISTS quota_holds (
    hold_id TEXT PRIMARY KEY,
    quota_date TEXT NOT NULL,
    token_fingerprint TEXT NOT NULL,
    consumer TEXT NOT NULL,
    rows INTEGER NOT NULL,
    request_slots INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL CHECK (state IN ('active', 'released')),
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (quota_date, token_fingerprint)
        REFERENCES quota_pools(quota_date, token_fingerprint)
);
CREATE INDEX IF NOT EXISTS quota_holds_pool_state_idx
    ON quota_holds(quota_date, token_fingerprint, state);
"""

_CONSUMER_BREAKDOWN_SQL = """
WITH request_usage AS (
    SELECT consumer,
           COALESCE(SUM(CASE WHEN state = 'committed' THEN committed_rows ELSE 0 END), 0)
               AS committed_rows,
           COALESCE(SUM(CASE WHEN state = 'reserved' THEN reserved_rows ELSE 0 END), 0)
               AS reserved_rows,
           COALESCE(SUM(CASE WHEN state = 'uncertain' THEN reserved_rows ELSE 0 END), 0)
               AS uncertain_rows,
           COALESCE(SUM(CASE WHEN state = 'committed' THEN 1 ELSE 0 END), 0)
               AS committed_requests,
           COALESCE(SUM(CASE WHEN state = 'reserved' THEN 1 ELSE 0 END), 0)
               AS reserved_requests,
           COALESCE(SUM(CASE WHEN state = 'uncertain' THEN 1 ELSE 0 END), 0)
               AS uncertain_requests,
           COALESCE(SUM(CASE WHEN state = 'released' THEN 1 ELSE 0 END), 0)
               AS released_requests,
           COUNT(*) AS attempts,
           COALESCE(SUM(CASE WHEN state = 'rejected' THEN 1 ELSE 0 END), 0)
               AS rejected_attempts
      FROM request_attempts
     WHERE quota_date = ? AND token_fingerprint = ?
     GROUP BY consumer
),
hold_usage AS (
    SELECT consumer,
           COALESCE(SUM(CASE WHEN state = 'active' THEN rows ELSE 0 END), 0)
               AS hold_target_rows,
           COALESCE(SUM(CASE WHEN state = 'active' THEN request_slots ELSE 0 END), 0)
               AS hold_target_requests,
           COALESCE(SUM(CASE WHEN state = 'active' THEN 1 ELSE 0 END), 0)
               AS active_holds
      FROM quota_holds
     WHERE quota_date = ? AND token_fingerprint = ?
     GROUP BY consumer
),
consumers AS (
    SELECT consumer FROM request_usage
    UNION
    SELECT consumer FROM hold_usage
)
SELECT consumers.consumer,
       COALESCE(request_usage.committed_rows, 0) AS committed_rows,
       COALESCE(request_usage.reserved_rows, 0) AS reserved_rows,
       COALESCE(request_usage.uncertain_rows, 0) AS uncertain_rows,
       COALESCE(request_usage.committed_requests, 0) AS committed_requests,
       COALESCE(request_usage.reserved_requests, 0) AS reserved_requests,
       COALESCE(request_usage.uncertain_requests, 0) AS uncertain_requests,
       COALESCE(request_usage.released_requests, 0) AS released_requests,
       MAX(
           COALESCE(hold_usage.hold_target_rows, 0)
           - COALESCE(request_usage.committed_rows, 0)
           - COALESCE(request_usage.reserved_rows, 0)
           - COALESCE(request_usage.uncertain_rows, 0),
           0
       ) AS hold_rows,
       MAX(
           COALESCE(hold_usage.hold_target_requests, 0)
           - COALESCE(request_usage.committed_requests, 0)
           - COALESCE(request_usage.reserved_requests, 0)
           - COALESCE(request_usage.uncertain_requests, 0),
           0
       ) AS hold_requests,
       COALESCE(hold_usage.hold_target_rows, 0) AS hold_target_rows,
       COALESCE(hold_usage.hold_target_requests, 0) AS hold_target_requests,
       COALESCE(request_usage.attempts, 0) AS attempts,
       COALESCE(request_usage.rejected_attempts, 0) AS rejected_attempts,
       COALESCE(hold_usage.active_holds, 0) AS active_holds
  FROM consumers
  LEFT JOIN request_usage USING (consumer)
  LEFT JOIN hold_usage USING (consumer)
 ORDER BY consumers.consumer
"""

USAGE_KEYS = (
    "committed_rows",
    "reserved_rows",
    "uncertain_rows",
    "hold_rows",
    "hold_target_rows",
    "committed_requests",
    "reserved_requests",
    "uncertain_requests",
    "released_requests",
    "hold_requests",
    "hold_target_requests",
    "attempts",
    "rejected_attempts",
    "active_holds",
)
CHARGED_USAGE_KEYS = ("committed_rows", "reserved_rows", "uncertain_rows", "hold_rows")
CHARGED_REQUEST_USAGE_KEYS = (
    "committed_requests",
    "reserved_requests",
    "uncertain_requests",
    "hold_requests",
)


@dataclass(frozen=True)
class QuotaStoreContext:
    config: MinuteQuotaConfig
    database_path: Path
    token_fingerprint: str


def connect(store: QuotaStoreContext) -> sqlite3.Connection:
    connection = sqlite3.connect(
        store.database_path,
        timeout=store.config.busy_timeout_ms / 1_000,
        isolation_level=None,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={store.config.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        deadline = time.monotonic() + store.config.busy_timeout_ms / 1_000
        while True:
            try:
                row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
                if row is None or str(row[0]).lower() != "wal":
                    raise MinuteQuotaConfigurationError(
                        "minute quota database could not enable WAL journal mode"
                    )
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        connection.execute("PRAGMA synchronous=FULL")
        return connection
    except BaseException:
        connection.close()
        raise


@contextmanager
def transaction(store: QuotaStoreContext) -> Iterator[sqlite3.Connection]:
    with closing(connect(store)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")


def _apply_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA_SQL)
    pool_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(quota_pools)").fetchall()
    }
    if "closed_at" not in pool_columns:
        connection.execute("ALTER TABLE quota_pools ADD COLUMN closed_at TEXT")
    if "closed_reason" not in pool_columns:
        connection.execute("ALTER TABLE quota_pools ADD COLUMN closed_reason TEXT")
    for name, declaration in (
        ("gate", "TEXT NOT NULL DEFAULT 'rows'"),
        ("limit_requests", "INTEGER NOT NULL DEFAULT 10000"),
        ("burst_limit_requests", "INTEGER NOT NULL DEFAULT 20000"),
        ("safety_requests", "INTEGER NOT NULL DEFAULT 500"),
    ):
        if name not in pool_columns:
            connection.execute(f"ALTER TABLE quota_pools ADD COLUMN {name} {declaration}")
    hold_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(quota_holds)").fetchall()
    }
    if "request_slots" not in hold_columns:
        connection.execute(
            "ALTER TABLE quota_holds ADD COLUMN request_slots INTEGER NOT NULL DEFAULT 0"
        )
    connection.execute(
        "INSERT OR REPLACE INTO quota_meta(key, value) VALUES ('schema_version', '2')"
    )


def initialize_database(store: QuotaStoreContext, *, key_id: str) -> None:
    with closing(connect(store)) as connection:
        _apply_schema(connection)
        connection.execute(
            "INSERT OR IGNORE INTO quota_meta(key, value) VALUES ('hmac_key_id', ?)",
            (key_id,),
        )
        row = connection.execute(
            "SELECT value FROM quota_meta WHERE key = 'hmac_key_id'"
        ).fetchone()
        if row is None or not hmac.compare_digest(str(row["value"]), key_id):
            raise MinuteQuotaConfigurationError(
                "minute quota HMAC key does not match the existing database"
            )


def ensure_pool(
    connection: sqlite3.Connection,
    store: QuotaStoreContext,
    *,
    quota_date: str,
    now: datetime,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO quota_pools(
            quota_date, token_fingerprint, limit_rows, safety_rows,
            gate, limit_requests, burst_limit_requests, safety_requests,
            timezone, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            quota_date,
            store.token_fingerprint,
            store.config.limit_rows,
            store.config.safety_rows,
            store.config.gate,
            store.config.limit_requests,
            store.config.burst_limit_requests,
            store.config.safety_requests,
            MINUTE_QUOTA_TIMEZONE,
            timestamp(now),
        ),
    )
    row = connection.execute(
        """
        SELECT limit_rows, safety_rows, gate, limit_requests,
               burst_limit_requests, safety_requests, timezone FROM quota_pools
         WHERE quota_date = ? AND token_fingerprint = ?
        """,
        (quota_date, store.token_fingerprint),
    ).fetchone()
    assert row is not None
    actual = (
        int(row["limit_rows"]),
        int(row["safety_rows"]),
        str(row["gate"]),
        int(row["limit_requests"]),
        int(row["burst_limit_requests"]),
        int(row["safety_requests"]),
        str(row["timezone"]),
    )
    expected = (
        store.config.limit_rows,
        store.config.safety_rows,
        store.config.gate,
        store.config.limit_requests,
        store.config.burst_limit_requests,
        store.config.safety_requests,
        MINUTE_QUOTA_TIMEZONE,
    )
    if actual != expected:
        raise MinuteQuotaConfigurationError(
            "minute quota pool policy mismatch for "
            f"{quota_date}/{store.token_fingerprint[:16]}: "
            f"existing={actual}, expected={expected}"
        )


def promote_expired(
    connection: sqlite3.Connection,
    store: QuotaStoreContext,
    *,
    quota_date: str,
    now: datetime,
) -> None:
    stamp = timestamp(now)
    connection.execute(
        """
        UPDATE request_attempts
           SET state = 'uncertain', error_kind = 'lease_expired', updated_at = ?
         WHERE quota_date = ? AND token_fingerprint = ? AND state = 'reserved'
           AND lease_expires_at <= ?
        """,
        (stamp, quota_date, store.token_fingerprint, stamp),
    )


def _breakdown_item(row: sqlite3.Row) -> dict[str, int | str]:
    item: dict[str, int | str] = {
        "consumer": str(row["consumer"]),
        **{key: int(row[key]) for key in USAGE_KEYS},
    }
    item["charged_rows"] = sum(int(item[key]) for key in CHARGED_USAGE_KEYS)
    return item


def consumer_breakdown(
    connection: sqlite3.Connection,
    store: QuotaStoreContext,
    *,
    quota_date: str,
) -> list[dict[str, int | str]]:
    rows = connection.execute(
        _CONSUMER_BREAKDOWN_SQL,
        (quota_date, store.token_fingerprint, quota_date, store.token_fingerprint),
    ).fetchall()
    return [_breakdown_item(row) for row in rows]


def usage(
    connection: sqlite3.Connection,
    store: QuotaStoreContext,
    *,
    quota_date: str,
) -> dict[str, int]:
    breakdown = consumer_breakdown(connection, store, quota_date=quota_date)
    return {key: sum(int(item[key]) for item in breakdown) for key in USAGE_KEYS}


def charged_rows(usage_summary: dict[str, int]) -> int:
    return sum(usage_summary[key] for key in CHARGED_USAGE_KEYS)


def charged_requests(usage_summary: dict[str, int]) -> int:
    return sum(usage_summary[key] for key in CHARGED_REQUEST_USAGE_KEYS)


def pool_close_reason(
    connection: sqlite3.Connection,
    store: QuotaStoreContext,
    *,
    quota_date: str,
) -> str | None:
    row = connection.execute(
        """
        SELECT closed_reason FROM quota_pools
         WHERE quota_date = ? AND token_fingerprint = ?
        """,
        (quota_date, store.token_fingerprint),
    ).fetchone()
    return None if row is None or row["closed_reason"] is None else str(row["closed_reason"])
