"""Public facade for shared request-level TuShare minute quota accounting.

The provider quota is accounted by the credential that was actually used and
the provider's Asia/Shanghai quota day. Credentials are never persisted: a
database-local HMAC key produces a stable, opaque fingerprint instead.

Every outbound minute request reserves one request slot and its worst-case row
count. A response commits the actual returned row count without releasing the
request slot, while an ambiguous failure or expired lease remains uncertain.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from market_data_platform import _tushare_minute_quota_config as _quota_config
from market_data_platform._tushare_minute_quota_config import (
    DEFAULT_MINUTE_QUOTA_CONSUMER,
    DEFAULT_MINUTE_QUOTA_LIMIT_ROWS,
    DEFAULT_MINUTE_QUOTA_SAFETY_ROWS,
    MINUTE_QUOTA_MODE_ENV,
    MINUTE_QUOTA_TIMEZONE,
    AmbiguousMinuteRequestError,
    MinuteQuotaConfig,
    MinuteQuotaConfigurationError,
    MinuteQuotaError,
    MinuteQuotaExceeded,
    MinuteQuotaPoolClosed,
    MinuteQuotaRequestOverrides,
    MinuteQuotaRequestPolicy,
    quota_date_at,
    read_or_create_fingerprint_key,
    resolve_minute_quota_config,
    token_fingerprint,
)
from market_data_platform._tushare_minute_quota_db import (
    QuotaStoreContext,
    initialize_database,
)
from market_data_platform._tushare_minute_quota_holds import (
    close_pool as close_quota_pool,
)
from market_data_platform._tushare_minute_quota_holds import (
    create_hold as create_quota_hold,
)
from market_data_platform._tushare_minute_quota_holds import (
    create_request_hold as create_quota_request_hold,
)
from market_data_platform._tushare_minute_quota_holds import quota_status
from market_data_platform._tushare_minute_quota_holds import (
    release_hold as release_quota_hold,
)
from market_data_platform._tushare_minute_quota_requests import RequestTransition, reserve_rows
from market_data_platform._tushare_minute_quota_requests import (
    transition_request as transition_quota_request,
)

DEFAULT_MINUTE_QUOTA_BUSY_TIMEOUT_MS = _quota_config.DEFAULT_MINUTE_QUOTA_BUSY_TIMEOUT_MS
DEFAULT_MINUTE_QUOTA_GATE = _quota_config.DEFAULT_MINUTE_QUOTA_GATE
DEFAULT_MINUTE_QUOTA_LIMIT_REQUESTS = _quota_config.DEFAULT_MINUTE_QUOTA_LIMIT_REQUESTS
DEFAULT_MINUTE_QUOTA_BURST_LIMIT_REQUESTS = _quota_config.DEFAULT_MINUTE_QUOTA_BURST_LIMIT_REQUESTS
DEFAULT_MINUTE_QUOTA_SAFETY_REQUESTS = _quota_config.DEFAULT_MINUTE_QUOTA_SAFETY_REQUESTS
DEFAULT_MINUTE_QUOTA_ALLOW_BURST = _quota_config.DEFAULT_MINUTE_QUOTA_ALLOW_BURST
DEFAULT_MINUTE_QUOTA_LEASE_SECONDS = _quota_config.DEFAULT_MINUTE_QUOTA_LEASE_SECONDS
DEFAULT_MINUTE_QUOTA_RELATIVE_PATH = _quota_config.DEFAULT_MINUTE_QUOTA_RELATIVE_PATH
MINUTE_QUOTA_CONSUMER_ENV = _quota_config.MINUTE_QUOTA_CONSUMER_ENV
MINUTE_QUOTA_DB_ENV = _quota_config.MINUTE_QUOTA_DB_ENV
MINUTE_QUOTA_HMAC_KEY_ENV = _quota_config.MINUTE_QUOTA_HMAC_KEY_ENV
MINUTE_QUOTA_LIMIT_ROWS_ENV = _quota_config.MINUTE_QUOTA_LIMIT_ROWS_ENV
MINUTE_QUOTA_SAFETY_ROWS_ENV = _quota_config.MINUTE_QUOTA_SAFETY_ROWS_ENV
MINUTE_QUOTA_GATE_ENV = _quota_config.MINUTE_QUOTA_GATE_ENV
MINUTE_QUOTA_LIMIT_REQUESTS_ENV = _quota_config.MINUTE_QUOTA_LIMIT_REQUESTS_ENV
MINUTE_QUOTA_BURST_LIMIT_REQUESTS_ENV = _quota_config.MINUTE_QUOTA_BURST_LIMIT_REQUESTS_ENV
MINUTE_QUOTA_SAFETY_REQUESTS_ENV = _quota_config.MINUTE_QUOTA_SAFETY_REQUESTS_ENV
MINUTE_QUOTA_ALLOW_BURST_ENV = _quota_config.MINUTE_QUOTA_ALLOW_BURST_ENV
MinuteQuotaMode = _quota_config.MinuteQuotaMode
MinuteQuotaGate = _quota_config.MinuteQuotaGate


class MinuteQuotaReservation:
    """A durable reservation for exactly one outbound provider attempt."""

    def __init__(self, ledger: MinuteQuotaLedger, reservation_id: str) -> None:
        self._ledger = ledger
        self.reservation_id = reservation_id
        self._settled = False

    @property
    def settled(self) -> bool:
        """Whether this process has explicitly settled the attempt."""

        return self._settled

    def commit(self, actual_rows: int, *, now: datetime | None = None) -> None:
        """Replace the worst-case reservation with the returned row count."""

        self._ledger._transition_request(
            self.reservation_id,
            target_state="committed",
            actual_rows=actual_rows,
            now=now,
        )
        self._settled = True

    def mark_uncertain(self, *, error_kind: str | None = None, now: datetime | None = None) -> None:
        """Keep the full reservation charged after an ambiguous attempt."""

        self._ledger._transition_request(
            self.reservation_id,
            target_state="uncertain",
            error_kind=error_kind,
            now=now,
        )
        self._settled = True

    def release(self, *, reason: str = "not_sent", now: datetime | None = None) -> None:
        """Release only when the caller can prove the request was not sent."""

        self._ledger._transition_request(
            self.reservation_id,
            target_state="released",
            error_kind=reason,
            now=now,
        )
        self._settled = True


class MinuteQuotaLedger:
    """SQLite-backed, process-safe quota gate for one actual credential."""

    def __init__(
        self,
        config: MinuteQuotaConfig,
        *,
        token: str,
        fingerprint_key: bytes | None = None,
    ) -> None:
        if config.mode == "off":
            raise ValueError("an off minute quota config does not create a ledger")
        assert config.database_path is not None
        self.config = config
        self.database_path = config.database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        key = fingerprint_key or read_or_create_fingerprint_key(self.database_path)
        self.token_fingerprint = token_fingerprint(token, key=key)
        self._store = QuotaStoreContext(
            config=config,
            database_path=self.database_path,
            token_fingerprint=self.token_fingerprint,
        )
        initialize_database(self._store, key_id=hashlib.sha256(key).hexdigest())

    @classmethod
    def from_env(  # noqa: PLR0913
        cls,
        *,
        token_env: str = "TUSHARE_TOKEN",
        mode: str | None = None,
        database_path: str | Path | None = None,
        consumer: str | None = None,
        limit_rows: int | None = None,
        safety_rows: int | None = None,
        request_overrides: MinuteQuotaRequestOverrides | None = None,
    ) -> MinuteQuotaLedger | None:
        """Build from an exported token and standard quota environment settings."""

        config = resolve_minute_quota_config(
            mode=mode,
            database_path=database_path,
            consumer=consumer,
            limit_rows=limit_rows,
            safety_rows=safety_rows,
            request_overrides=request_overrides,
        )
        if config.mode == "off":
            return None
        token = os.environ.get(token_env, "").strip()
        if not token:
            raise MinuteQuotaConfigurationError(
                f"TuShare token not found in environment variable {token_env}"
            )
        return cls(config, token=token)

    def reserve(
        self,
        rows: int,
        *,
        consumer: str | None = None,
        now: datetime | None = None,
    ) -> MinuteQuotaReservation:
        """Atomically reserve worst-case rows before one provider attempt."""

        reservation_id = reserve_rows(self._store, rows, consumer=consumer, now=now)
        return MinuteQuotaReservation(self, reservation_id)

    @contextmanager
    def attempt(
        self,
        rows: int,
        *,
        consumer: str | None = None,
        now: datetime | None = None,
    ) -> Iterator[MinuteQuotaReservation]:
        """Conservatively settle forgotten or exceptional provider attempts."""

        reservation = self.reserve(rows, consumer=consumer, now=now)
        try:
            yield reservation
        except BaseException as exc:
            if not reservation.settled:
                reservation.mark_uncertain(error_kind=type(exc).__name__)
            raise
        else:
            if not reservation.settled:
                reservation.mark_uncertain(error_kind="unsettled_context")

    def _transition_request(
        self,
        reservation_id: str,
        *,
        target_state: Literal["committed", "uncertain", "released"],
        actual_rows: int | None = None,
        error_kind: str | None = None,
        now: datetime | None = None,
    ) -> None:
        transition_quota_request(
            self._store,
            reservation_id,
            RequestTransition(
                target_state=target_state,
                actual_rows=actual_rows,
                error_kind=error_kind,
                now=now,
            ),
        )

    def create_hold(
        self,
        rows: int,
        *,
        consumer: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> str:
        """Set a residual daily capacity target for another minute consumer."""

        return create_quota_hold(
            self._store,
            rows,
            consumer=consumer,
            note=note,
            now=now,
        )

    def create_request_hold(
        self,
        requests: int,
        *,
        consumer: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> str:
        """Set a residual daily request-slot target for another consumer."""

        return create_quota_request_hold(
            self._store,
            requests,
            consumer=consumer,
            note=note,
            now=now,
        )

    def close_pool(
        self,
        reason: str,
        *,
        quota_date: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Close a quota day after an unambiguous provider exhaustion signal."""

        close_quota_pool(self._store, reason, quota_date=quota_date, now=now)

    def release_hold(self, hold_id: str, *, now: datetime | None = None) -> None:
        """Release a previously created future-consumer hold."""

        release_quota_hold(self._store, hold_id, now=now)

    def status(
        self,
        *,
        quota_date: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return a token-safe summary and settle expired leases conservatively."""

        return quota_status(self._store, quota_date=quota_date, now=now)


def build_minute_quota_ledger(  # noqa: PLR0913
    *,
    token: str,
    mode: str | None = None,
    database_path: str | Path | None = None,
    consumer: str | None = None,
    limit_rows: int | None = None,
    safety_rows: int | None = None,
    request_overrides: MinuteQuotaRequestOverrides | None = None,
) -> MinuteQuotaLedger | None:
    """Resolve configuration and build a ledger only when accounting is enabled."""

    config = resolve_minute_quota_config(
        mode=mode,
        database_path=database_path,
        consumer=consumer,
        limit_rows=limit_rows,
        safety_rows=safety_rows,
        request_overrides=request_overrides,
    )
    if config.mode == "off":
        return None
    return MinuteQuotaLedger(config, token=token)


__all__ = [
    "DEFAULT_MINUTE_QUOTA_CONSUMER",
    "DEFAULT_MINUTE_QUOTA_GATE",
    "DEFAULT_MINUTE_QUOTA_LIMIT_REQUESTS",
    "DEFAULT_MINUTE_QUOTA_BURST_LIMIT_REQUESTS",
    "DEFAULT_MINUTE_QUOTA_SAFETY_REQUESTS",
    "DEFAULT_MINUTE_QUOTA_ALLOW_BURST",
    "DEFAULT_MINUTE_QUOTA_LIMIT_ROWS",
    "DEFAULT_MINUTE_QUOTA_SAFETY_ROWS",
    "MINUTE_QUOTA_MODE_ENV",
    "MINUTE_QUOTA_GATE_ENV",
    "MINUTE_QUOTA_LIMIT_REQUESTS_ENV",
    "MINUTE_QUOTA_BURST_LIMIT_REQUESTS_ENV",
    "MINUTE_QUOTA_SAFETY_REQUESTS_ENV",
    "MINUTE_QUOTA_ALLOW_BURST_ENV",
    "MINUTE_QUOTA_TIMEZONE",
    "MinuteQuotaConfig",
    "MinuteQuotaGate",
    "MinuteQuotaMode",
    "AmbiguousMinuteRequestError",
    "MinuteQuotaConfigurationError",
    "MinuteQuotaError",
    "MinuteQuotaExceeded",
    "MinuteQuotaLedger",
    "MinuteQuotaPoolClosed",
    "MinuteQuotaRequestOverrides",
    "MinuteQuotaRequestPolicy",
    "MinuteQuotaReservation",
    "build_minute_quota_ledger",
    "quota_date_at",
    "resolve_minute_quota_config",
    "token_fingerprint",
]
