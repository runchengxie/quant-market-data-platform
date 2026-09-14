"""Capacity holds, pool closure, and status operations for minute quota accounting."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from market_data_platform._tushare_minute_quota_config import (
    MINUTE_QUOTA_TIMEZONE,
    MinuteQuotaError,
    MinuteQuotaExceeded,
    MinuteQuotaPoolClosed,
    quota_date_at,
    timestamp,
    utc_now,
    validate_consumer,
)
from market_data_platform._tushare_minute_quota_db import (
    QuotaStoreContext,
    charged_requests,
    charged_rows,
    consumer_breakdown,
    ensure_pool,
    pool_close_reason,
    promote_expired,
    transaction,
    usage,
)


@dataclass(frozen=True)
class _HoldSpec:
    rows: int
    request_slots: int
    consumer: str
    note: str | None
    instant: datetime
    quota_date: str
    hold_id: str
    stamp: str


@dataclass(frozen=True)
class _HoldCapacity:
    available_rows: int
    available_requests: int
    incremental_rows: int
    incremental_requests: int
    close_reason: str | None


@dataclass(frozen=True)
class _StatusSnapshot:
    usage: dict[str, int]
    consumers: list[dict[str, int | str]]
    active_request_holds: list[dict[str, int | str]]
    closed_at: str | None
    closed_reason: str | None


def _hold_spec(
    rows: int,
    request_slots: int,
    consumer: str,
    note: str | None,
    now: datetime | None,
) -> _HoldSpec:
    if rows < 0 or request_slots < 0 or (rows == 0 and request_slots == 0):
        raise ValueError("minute quota hold must reserve positive rows or requests")
    resolved_consumer = str(consumer).strip()
    validate_consumer(resolved_consumer)
    instant = utc_now(now)
    return _HoldSpec(
        rows=rows,
        request_slots=request_slots,
        consumer=resolved_consumer,
        note=note,
        instant=instant,
        quota_date=quota_date_at(instant),
        hold_id=uuid.uuid4().hex,
        stamp=timestamp(instant),
    )


def _consumer_usage_and_targets(
    consumers: list[dict[str, int | str]],
    consumer: str,
) -> tuple[int, int, int, int]:
    consumer_usage = next(
        (item for item in consumers if item["consumer"] == consumer),
        None,
    )
    if consumer_usage is None:
        return 0, 0, 0, 0
    request_rows = sum(
        int(consumer_usage[key]) for key in ("committed_rows", "reserved_rows", "uncertain_rows")
    )
    request_count = sum(
        int(consumer_usage[key])
        for key in ("committed_requests", "reserved_requests", "uncertain_requests")
    )
    return (
        request_rows,
        int(consumer_usage["hold_target_rows"]),
        request_count,
        int(consumer_usage["hold_target_requests"]),
    )


def _hold_capacity(
    connection: sqlite3.Connection,
    store: QuotaStoreContext,
    spec: _HoldSpec,
) -> _HoldCapacity:
    usage_summary = usage(connection, store, quota_date=spec.quota_date)
    consumers = consumer_breakdown(connection, store, quota_date=spec.quota_date)
    available = store.config.limit_rows - store.config.safety_rows - charged_rows(usage_summary)
    available_requests = store.config.effective_limit_requests - charged_requests(usage_summary)
    request_rows, existing_target, request_count, existing_request_target = (
        _consumer_usage_and_targets(consumers, spec.consumer)
    )
    existing_residual = max(existing_target - request_rows, 0)
    new_residual = max(existing_target + spec.rows - request_rows, 0)
    existing_request_residual = max(existing_request_target - request_count, 0)
    new_request_residual = max(
        existing_request_target + spec.request_slots - request_count,
        0,
    )
    return _HoldCapacity(
        available_rows=available,
        available_requests=available_requests,
        incremental_rows=new_residual - existing_residual,
        incremental_requests=new_request_residual - existing_request_residual,
        close_reason=pool_close_reason(connection, store, quota_date=spec.quota_date),
    )


def _validate_hold_capacity(
    store: QuotaStoreContext,
    spec: _HoldSpec,
    capacity: _HoldCapacity,
) -> None:
    if store.config.mode != "enforce":
        return
    if capacity.close_reason is not None:
        raise MinuteQuotaPoolClosed(
            quota_date=spec.quota_date,
            consumer=spec.consumer,
            reason=capacity.close_reason,
        )
    rows_exceeded = (
        store.config.gate in {"rows", "dual"}
        and capacity.incremental_rows > capacity.available_rows
    )
    requests_exceeded = (
        store.config.gate in {"requests", "dual"}
        and capacity.incremental_requests > capacity.available_requests
    )
    if rows_exceeded or requests_exceeded:
        unit = "requests" if requests_exceeded else "rows"
        raise MinuteQuotaExceeded(
            quota_date=spec.quota_date,
            requested_rows=spec.rows,
            available_rows=max(capacity.available_rows, 0),
            unit=unit,
            requested_requests=spec.request_slots,
            available_requests=max(capacity.available_requests, 0),
            consumer=spec.consumer,
        )


def _insert_hold(
    connection: sqlite3.Connection,
    store: QuotaStoreContext,
    spec: _HoldSpec,
) -> None:
    connection.execute(
        """
        INSERT INTO quota_holds(
            hold_id, quota_date, token_fingerprint, consumer, rows, request_slots,
            state, note, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (
            spec.hold_id,
            spec.quota_date,
            store.token_fingerprint,
            spec.consumer,
            spec.rows,
            spec.request_slots,
            spec.note,
            spec.stamp,
            spec.stamp,
        ),
    )


def create_hold(
    store: QuotaStoreContext,
    rows: int,
    *,
    consumer: str,
    note: str | None = None,
    now: datetime | None = None,
) -> str:
    """Set an additional daily capacity target for another minute consumer."""

    spec = _hold_spec(rows, 0, consumer, note, now)
    with transaction(store) as connection:
        ensure_pool(connection, store, quota_date=spec.quota_date, now=spec.instant)
        promote_expired(connection, store, quota_date=spec.quota_date, now=spec.instant)
        capacity = _hold_capacity(connection, store, spec)
        _validate_hold_capacity(store, spec, capacity)
        _insert_hold(connection, store, spec)
    return spec.hold_id


def create_request_hold(
    store: QuotaStoreContext,
    requests: int,
    *,
    consumer: str,
    note: str | None = None,
    now: datetime | None = None,
) -> str:
    """Idempotently acquire at least this request-slot target for one consumer."""

    spec = _hold_spec(0, requests, consumer, note, now)
    with transaction(store) as connection:
        ensure_pool(connection, store, quota_date=spec.quota_date, now=spec.instant)
        promote_expired(connection, store, quota_date=spec.quota_date, now=spec.instant)
        existing = connection.execute(
            """
            SELECT hold_id, request_slots FROM quota_holds
             WHERE quota_date = ? AND token_fingerprint = ? AND consumer = ?
               AND state = 'active' AND request_slots > 0
             ORDER BY created_at, hold_id
            """,
            (spec.quota_date, store.token_fingerprint, spec.consumer),
        ).fetchall()
        existing_total = sum(int(row["request_slots"]) for row in existing)
        if existing_total < requests:
            incremental = _hold_spec(
                0,
                requests - existing_total,
                consumer,
                note,
                now,
            )
            capacity = _hold_capacity(connection, store, incremental)
            _validate_hold_capacity(store, incremental, capacity)
        if not existing:
            _insert_hold(connection, store, spec)
            return spec.hold_id
        canonical_id = str(existing[0]["hold_id"])
        canonical_target = max(existing_total, requests)
        connection.execute(
            """
            UPDATE quota_holds
               SET request_slots = ?, note = COALESCE(?, note), updated_at = ?
             WHERE hold_id = ? AND token_fingerprint = ?
            """,
            (
                canonical_target,
                note,
                spec.stamp,
                canonical_id,
                store.token_fingerprint,
            ),
        )
        if len(existing) > 1:
            redundant_ids = [str(row["hold_id"]) for row in existing[1:]]
            placeholders = ",".join("?" for _value in redundant_ids)
            connection.execute(
                f"UPDATE quota_holds SET state = 'released', updated_at = ? "
                f"WHERE hold_id IN ({placeholders}) AND token_fingerprint = ?",
                (spec.stamp, *redundant_ids, store.token_fingerprint),
            )
        return canonical_id


def _validate_close_reason(reason: str) -> str:
    resolved_reason = str(reason).strip()
    if not resolved_reason or len(resolved_reason) > 128:
        raise ValueError("minute quota close reason must contain 1-128 characters")
    if any(ord(character) < 32 for character in resolved_reason):
        raise ValueError("minute quota close reason cannot contain control characters")
    return resolved_reason


def close_pool(
    store: QuotaStoreContext,
    reason: str,
    *,
    quota_date: str | None = None,
    now: datetime | None = None,
) -> None:
    """Close a quota day after an unambiguous provider exhaustion signal."""

    resolved_reason = _validate_close_reason(reason)
    instant = utc_now(now)
    resolved_date = quota_date or quota_date_at(instant)
    with transaction(store) as connection:
        ensure_pool(connection, store, quota_date=resolved_date, now=instant)
        connection.execute(
            """
            UPDATE quota_pools SET closed_at = ?, closed_reason = ?
             WHERE quota_date = ? AND token_fingerprint = ?
            """,
            (timestamp(instant), resolved_reason, resolved_date, store.token_fingerprint),
        )


def release_hold(
    store: QuotaStoreContext,
    hold_id: str,
    *,
    now: datetime | None = None,
) -> None:
    """Release a previously created future-consumer hold."""

    with transaction(store) as connection:
        row = connection.execute(
            "SELECT state FROM quota_holds WHERE hold_id = ? AND token_fingerprint = ?",
            (hold_id, store.token_fingerprint),
        ).fetchone()
        if row is None:
            raise MinuteQuotaError(f"unknown minute quota hold: {hold_id}")
        if str(row["state"]) == "released":
            return
        cursor = connection.execute(
            """
            UPDATE quota_holds SET state = 'released', updated_at = ?
             WHERE hold_id = ? AND token_fingerprint = ? AND state = 'active'
            """,
            (timestamp(now), hold_id, store.token_fingerprint),
        )
        if cursor.rowcount != 1:
            raise MinuteQuotaError(f"inactive minute quota hold: {hold_id}")


def _status_snapshot(
    store: QuotaStoreContext,
    quota_date: str,
    instant: datetime,
) -> _StatusSnapshot:
    with transaction(store) as connection:
        pool_row = connection.execute(
            """
            SELECT closed_at, closed_reason FROM quota_pools
             WHERE quota_date = ? AND token_fingerprint = ?
            """,
            (quota_date, store.token_fingerprint),
        ).fetchone()
        if pool_row is not None:
            # Status is read-only with respect to pool policy: validate an
            # existing pool, but never let observation create today's policy.
            ensure_pool(connection, store, quota_date=quota_date, now=instant)
            promote_expired(connection, store, quota_date=quota_date, now=instant)
        usage_summary = usage(connection, store, quota_date=quota_date)
        consumers = consumer_breakdown(connection, store, quota_date=quota_date)
        active_request_holds = [
            {
                "hold_id": str(row["hold_id"]),
                "consumer": str(row["consumer"]),
                "request_slots": int(row["request_slots"]),
                "created_at": str(row["created_at"]),
            }
            for row in connection.execute(
                """
                SELECT hold_id, consumer, request_slots, created_at FROM quota_holds
                 WHERE quota_date = ? AND token_fingerprint = ?
                   AND state = 'active' AND request_slots > 0
                 ORDER BY consumer, created_at, hold_id
                """,
                (quota_date, store.token_fingerprint),
            ).fetchall()
        ]
    return _StatusSnapshot(
        usage=usage_summary,
        consumers=consumers,
        active_request_holds=active_request_holds,
        closed_at=None if pool_row is None else pool_row["closed_at"],
        closed_reason=None if pool_row is None else pool_row["closed_reason"],
    )


def quota_status(
    store: QuotaStoreContext,
    *,
    quota_date: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a token-safe summary and conservatively settle expired leases."""

    instant = utc_now(now)
    resolved_date = quota_date or quota_date_at(instant)
    if len(resolved_date) != 8 or not resolved_date.isdigit():
        raise ValueError("quota_date must use YYYYMMDD")
    snapshot = _status_snapshot(store, resolved_date, instant)
    charged = charged_rows(snapshot.usage)
    available = store.config.limit_rows - store.config.safety_rows - charged
    charged_request_slots = charged_requests(snapshot.usage)
    available_requests = store.config.effective_limit_requests - charged_request_slots
    return {
        "schema_version": 2,
        "quota_date": resolved_date,
        "timezone": MINUTE_QUOTA_TIMEZONE,
        "token_fingerprint": store.token_fingerprint[:16],
        "mode": store.config.mode,
        "gate": store.config.gate,
        "limit_rows": store.config.limit_rows,
        "safety_rows": store.config.safety_rows,
        "limit_requests": store.config.limit_requests,
        "burst_limit_requests": store.config.burst_limit_requests,
        "safety_requests": store.config.safety_requests,
        "allow_burst": store.config.allow_burst,
        "effective_limit_requests": store.config.effective_limit_requests,
        **snapshot.usage,
        "consumers": snapshot.consumers,
        "active_request_holds": snapshot.active_request_holds,
        "charged_rows": charged,
        "available_rows": max(available, 0),
        "over_limit_rows": max(-available, 0),
        "charged_requests": charged_request_slots,
        "available_requests": max(available_requests, 0),
        "over_limit_requests": max(-available_requests, 0),
        "closed_at": snapshot.closed_at,
        "closed_reason": snapshot.closed_reason,
        "database_path": str(store.database_path),
    }
