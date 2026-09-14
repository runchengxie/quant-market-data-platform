"""Request reservation and settlement operations for minute quota accounting."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from market_data_platform._tushare_minute_quota_config import (
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
class _ReservationSpec:
    rows: int
    consumer: str
    instant: datetime
    quota_date: str
    reservation_id: str
    created_at: str
    lease_expires_at: str


@dataclass(frozen=True)
class _ReservationDecision:
    available_rows: int
    available_requests: int
    consumer_hold_rows: int
    consumer_hold_requests: int
    incremental_rows: int
    incremental_requests: int
    close_reason: str | None
    rejection_unit: Literal["rows", "requests"] | None
    rejected: bool

    @property
    def closed(self) -> bool:
        return self.close_reason is not None


@dataclass(frozen=True)
class RequestTransition:
    target_state: Literal["committed", "uncertain", "released"]
    actual_rows: int | None = None
    error_kind: str | None = None
    now: datetime | None = None


def _reservation_spec(
    store: QuotaStoreContext,
    rows: int,
    consumer: str | None,
    now: datetime | None,
) -> _ReservationSpec:
    if rows <= 0:
        raise ValueError("minute quota reservation rows must be positive")
    resolved_consumer = str(consumer or store.config.consumer).strip()
    validate_consumer(resolved_consumer)
    instant = utc_now(now)
    return _ReservationSpec(
        rows=rows,
        consumer=resolved_consumer,
        instant=instant,
        quota_date=quota_date_at(instant),
        reservation_id=uuid.uuid4().hex,
        created_at=timestamp(instant),
        lease_expires_at=timestamp(instant + timedelta(seconds=store.config.lease_seconds)),
    )


def _consumer_hold(
    consumers: list[dict[str, int | str]],
    consumer: str,
) -> tuple[int, int]:
    item = next((item for item in consumers if item["consumer"] == consumer), None)
    if item is None:
        return 0, 0
    return int(item["hold_rows"]), int(item["hold_requests"])


def _reservation_decision(
    connection: sqlite3.Connection,
    store: QuotaStoreContext,
    spec: _ReservationSpec,
) -> _ReservationDecision:
    usage_summary = usage(connection, store, quota_date=spec.quota_date)
    consumers = consumer_breakdown(connection, store, quota_date=spec.quota_date)
    available = store.config.limit_rows - store.config.safety_rows - charged_rows(usage_summary)
    available_requests = store.config.effective_limit_requests - charged_requests(usage_summary)
    hold_rows, hold_requests = _consumer_hold(consumers, spec.consumer)
    incremental_rows = max(spec.rows - hold_rows, 0)
    incremental_requests = max(1 - hold_requests, 0)
    close_reason = pool_close_reason(connection, store, quota_date=spec.quota_date)
    enforce = store.config.mode == "enforce"
    closed = enforce and close_reason is not None
    rows_exceeded = store.config.gate in {"rows", "dual"} and incremental_rows > available
    requests_exceeded = (
        store.config.gate in {"requests", "dual"} and incremental_requests > available_requests
    )
    rejection_unit: Literal["rows", "requests"] | None = None
    if requests_exceeded:
        rejection_unit = "requests"
    elif rows_exceeded:
        rejection_unit = "rows"
    return _ReservationDecision(
        available_rows=available,
        available_requests=available_requests,
        consumer_hold_rows=hold_rows,
        consumer_hold_requests=hold_requests,
        incremental_rows=incremental_rows,
        incremental_requests=incremental_requests,
        close_reason=close_reason if closed else None,
        rejection_unit=rejection_unit,
        rejected=closed or (enforce and rejection_unit is not None),
    )


def _insert_attempt(
    connection: sqlite3.Connection,
    store: QuotaStoreContext,
    spec: _ReservationSpec,
    decision: _ReservationDecision,
) -> None:
    connection.execute(
        """
        INSERT INTO request_attempts(
            reservation_id, quota_date, token_fingerprint, consumer, state,
            requested_rows, reserved_rows, committed_rows, over_limit,
            error_kind, created_at, updated_at, lease_expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
        """,
        (
            spec.reservation_id,
            spec.quota_date,
            store.token_fingerprint,
            spec.consumer,
            "rejected" if decision.rejected else "reserved",
            spec.rows,
            0 if decision.rejected else spec.rows,
            int(decision.rejection_unit is not None),
            (
                "pool_closed"
                if decision.closed
                else ("quota_exceeded" if decision.rejected else None)
            ),
            spec.created_at,
            spec.created_at,
            None if decision.rejected else spec.lease_expires_at,
        ),
    )


def _raise_reservation_rejection(
    spec: _ReservationSpec,
    decision: _ReservationDecision,
) -> None:
    if decision.closed:
        assert decision.close_reason is not None
        raise MinuteQuotaPoolClosed(
            quota_date=spec.quota_date,
            consumer=spec.consumer,
            reason=decision.close_reason,
        )
    if decision.rejected:
        unit = decision.rejection_unit or "rows"
        raise MinuteQuotaExceeded(
            quota_date=spec.quota_date,
            requested_rows=spec.rows,
            available_rows=max(decision.available_rows + decision.consumer_hold_rows, 0),
            unit=unit,
            requested_requests=1,
            available_requests=max(
                decision.available_requests + decision.consumer_hold_requests,
                0,
            ),
            consumer=spec.consumer,
        )


def reserve_rows(
    store: QuotaStoreContext,
    rows: int,
    *,
    consumer: str | None = None,
    now: datetime | None = None,
) -> str:
    """Atomically reserve worst-case rows before one provider attempt."""

    spec = _reservation_spec(store, rows, consumer, now)
    with transaction(store) as connection:
        ensure_pool(connection, store, quota_date=spec.quota_date, now=spec.instant)
        promote_expired(connection, store, quota_date=spec.quota_date, now=spec.instant)
        decision = _reservation_decision(connection, store, spec)
        _insert_attempt(connection, store, spec, decision)
    _raise_reservation_rejection(spec, decision)
    return spec.reservation_id


def transition_request(
    store: QuotaStoreContext,
    reservation_id: str,
    transition: RequestTransition,
) -> None:
    if transition.target_state == "committed" and (
        transition.actual_rows is None or transition.actual_rows < 0
    ):
        raise ValueError("actual_rows must be non-negative when committing a request")
    instant = utc_now(transition.now)
    with transaction(store) as connection:
        row = connection.execute(
            """
            SELECT state FROM request_attempts
             WHERE reservation_id = ? AND token_fingerprint = ?
            """,
            (reservation_id, store.token_fingerprint),
        ).fetchone()
        if row is None:
            raise MinuteQuotaError(f"unknown minute quota reservation: {reservation_id}")
        current_state = str(row["state"])
        if current_state == transition.target_state:
            return
        if current_state not in {"reserved", "uncertain"}:
            raise MinuteQuotaError(
                f"cannot transition minute quota reservation {reservation_id} "
                f"from {current_state} to {transition.target_state}"
            )
        connection.execute(
            """
            UPDATE request_attempts
               SET state = ?, committed_rows = ?, error_kind = ?,
                   updated_at = ?, lease_expires_at = NULL
             WHERE reservation_id = ? AND token_fingerprint = ?
            """,
            (
                transition.target_state,
                (int(transition.actual_rows or 0) if transition.target_state == "committed" else 0),
                transition.error_kind,
                timestamp(instant),
                reservation_id,
                store.token_fingerprint,
            ),
        )
