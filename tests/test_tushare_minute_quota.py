from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from market_data_platform.tushare_minute_quota import (
    DEFAULT_MINUTE_QUOTA_SAFETY_ROWS,
    MinuteQuotaConfig,
    MinuteQuotaConfigurationError,
    MinuteQuotaExceeded,
    MinuteQuotaGate,
    MinuteQuotaLedger,
    MinuteQuotaMode,
    MinuteQuotaPoolClosed,
    MinuteQuotaRequestPolicy,
    quota_date_at,
)

FINGERPRINT_KEY = b"test-only-stable-hmac-key-32-bytes-long"
TOKEN = "private-test-token-that-must-not-be-stored"
NOW = datetime(2026, 7, 17, 4, 0, tzinfo=UTC)


def test_default_safety_reserves_ten_million_rows() -> None:
    assert DEFAULT_MINUTE_QUOTA_SAFETY_ROWS == 10_000_000


def _request_policy(
    *,
    gate: MinuteQuotaGate = "rows",
    limit_requests: int = 10_000,
    burst_limit_requests: int = 20_000,
    safety_requests: int = 500,
    allow_burst: bool = False,
) -> MinuteQuotaRequestPolicy:
    return MinuteQuotaRequestPolicy(
        gate=gate,
        limit_requests=limit_requests,
        burst_limit_requests=burst_limit_requests,
        safety_requests=safety_requests,
        allow_burst=allow_burst,
    )


def _ledger(  # noqa: PLR0913
    tmp_path: Path,
    *,
    token: str = TOKEN,
    mode: MinuteQuotaMode = "enforce",
    limit_rows: int = 1_000,
    safety_rows: int = 0,
    request_policy: MinuteQuotaRequestPolicy | None = None,
    lease_seconds: int = 60,
) -> MinuteQuotaLedger:
    return MinuteQuotaLedger(
        MinuteQuotaConfig(
            mode=mode,
            database_path=tmp_path / "minute-quota.sqlite3",
            consumer="test-consumer",
            limit_rows=limit_rows,
            safety_rows=safety_rows,
            request_policy=request_policy or MinuteQuotaRequestPolicy(),
            lease_seconds=lease_seconds,
        ),
        token=token,
        fingerprint_key=FINGERPRINT_KEY,
    )


def test_quota_date_uses_asia_shanghai_midnight() -> None:
    assert quota_date_at(datetime(2026, 7, 17, 15, 59, 59, tzinfo=UTC)) == "20260717"
    assert quota_date_at(datetime(2026, 7, 17, 16, 0, tzinfo=UTC)) == "20260718"
    with pytest.raises(ValueError, match="timezone-aware"):
        quota_date_at(datetime(2026, 7, 17, 16, 0))


def test_token_identity_is_stable_distinct_and_never_persisted(tmp_path: Path) -> None:
    first = _ledger(tmp_path, token=TOKEN)
    same = _ledger(tmp_path, token=TOKEN)
    other = _ledger(tmp_path, token="another-private-token")

    assert first.token_fingerprint == same.token_fingerprint
    assert first.token_fingerprint != other.token_fingerprint

    reservation = first.reserve(241, now=NOW)
    reservation.commit(240, now=NOW)
    database_bytes = (tmp_path / "minute-quota.sqlite3").read_bytes()
    assert TOKEN.encode() not in database_bytes


def test_database_rejects_a_different_hmac_identity_key(tmp_path: Path) -> None:
    _ledger(tmp_path)

    with pytest.raises(MinuteQuotaConfigurationError, match="HMAC key"):
        MinuteQuotaLedger(
            MinuteQuotaConfig(
                mode="enforce",
                database_path=tmp_path / "minute-quota.sqlite3",
                limit_rows=1_000,
                safety_rows=0,
            ),
            token=TOKEN,
            fingerprint_key=b"a-different-test-hmac-key-32-bytes",
        )


def test_same_token_day_rejects_inconsistent_pool_policy(tmp_path: Path) -> None:
    first = _ledger(tmp_path, limit_rows=1_000)
    first.reserve(1, now=NOW)
    inconsistent = _ledger(tmp_path, limit_rows=999)

    with pytest.raises(MinuteQuotaConfigurationError, match="pool policy mismatch"):
        inconsistent.reserve(1, now=NOW)


def test_status_on_fresh_database_is_token_safe_and_does_not_lock_policy(
    tmp_path: Path,
) -> None:
    status = _ledger(tmp_path).status(now=NOW)

    assert status["charged_rows"] == 0
    assert status["charged_requests"] == 0
    assert status["consumers"] == []
    assert len(status["token_fingerprint"]) == 16
    assert TOKEN not in repr(status)
    with sqlite3.connect(tmp_path / "minute-quota.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM quota_pools").fetchone() == (0,)

    request_gate = _ledger(tmp_path, request_policy=_request_policy(gate="requests"))
    request_gate.reserve(1, now=NOW)
    assert request_gate.status(now=NOW)["gate"] == "requests"


def test_concurrent_bootstrap_uses_one_atomic_database_local_key(tmp_path: Path) -> None:
    config = MinuteQuotaConfig(
        mode="observe",
        database_path=tmp_path / "bootstrap" / "minute-quota.sqlite3",
        limit_rows=1_000,
        safety_rows=0,
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        fingerprints = list(
            pool.map(
                lambda _index: MinuteQuotaLedger(config, token=TOKEN).token_fingerprint,
                range(16),
            )
        )

    assert len(set(fingerprints)) == 1
    assert config.database_path is not None
    key_path = config.database_path.with_name(f".{config.database_path.name}.hmac-key")
    assert key_path.stat().st_mode & 0o777 == 0o600


def test_reserve_is_atomic_across_concurrent_workers(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, limit_rows=1_000)

    def reserve_once(_index: int) -> bool:
        try:
            ledger.reserve(100, now=NOW)
        except MinuteQuotaExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = list(pool.map(reserve_once, range(24)))

    assert sum(accepted) == 10
    status = ledger.status(now=NOW)
    assert status["reserved_rows"] == 1_000
    assert status["rejected_attempts"] == 14
    assert status["available_rows"] == 0

    with sqlite3.connect(tmp_path / "minute-quota.sqlite3") as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2


def test_expired_or_interrupted_attempts_remain_uncertain(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, lease_seconds=1)
    ledger.reserve(241, now=NOW)

    expired = ledger.status(now=NOW + timedelta(seconds=2))
    assert expired["reserved_rows"] == 0
    assert expired["uncertain_rows"] == 241
    assert expired["reserved_requests"] == 0
    assert expired["uncertain_requests"] == 1

    with pytest.raises(KeyboardInterrupt):
        with ledger.attempt(100, now=NOW + timedelta(seconds=2)):
            raise KeyboardInterrupt

    interrupted = ledger.status(now=NOW + timedelta(seconds=2))
    assert interrupted["uncertain_rows"] == 341
    assert interrupted["uncertain_requests"] == 2


def test_proven_not_sent_attempt_can_be_explicitly_released(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    reservation = ledger.reserve(241, now=NOW)
    reservation.release(reason="local_preflight_failed", now=NOW)

    status = ledger.status(now=NOW)
    assert status["reserved_rows"] == 0
    assert status["uncertain_rows"] == 0
    assert status["charged_rows"] == 0


def test_observe_mode_records_an_over_limit_attempt_without_rejecting(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, mode="observe", limit_rows=100)
    reservation = ledger.reserve(241, now=NOW)
    reservation.commit(200, now=NOW)

    status = ledger.status(now=NOW)
    assert status["committed_rows"] == 200
    assert status["over_limit_rows"] == 100
    assert status["rejected_attempts"] == 0


def test_each_asia_shanghai_day_has_an_independent_pool(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, limit_rows=300)
    before_midnight = datetime(2026, 7, 17, 15, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 7, 17, 16, 1, tzinfo=UTC)

    ledger.reserve(300, now=before_midnight)
    ledger.reserve(300, now=after_midnight)

    old_day = ledger.status(quota_date="20260717", now=after_midnight)
    assert old_day["reserved_rows"] == 0
    assert old_day["uncertain_rows"] == 300
    assert ledger.status(quota_date="20260718", now=after_midnight)["reserved_rows"] == 300


def test_context_without_commit_is_not_silently_released(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    with ledger.attempt(241, now=NOW):
        pass

    assert ledger.status(now=NOW)["uncertain_rows"] == 241


def test_confirmed_exhaustion_can_close_only_the_current_pool(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.close_pool("provider_daily_quota_exhausted", now=NOW)

    with pytest.raises(MinuteQuotaPoolClosed, match="provider_daily_quota_exhausted"):
        ledger.reserve(1, now=NOW)

    status = ledger.status(now=NOW)
    assert status["closed_reason"] == "provider_daily_quota_exhausted"
    assert status["rejected_attempts"] == 1

    next_day = NOW + timedelta(days=1)
    ledger.reserve(1, now=next_day)
    assert ledger.status(now=next_day)["closed_reason"] is None


def test_schema_v1_database_migrates_additively_and_keeps_rows_default(
    tmp_path: Path,
) -> None:
    database = tmp_path / "minute-quota.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE quota_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO quota_meta(key, value) VALUES ('schema_version', '1');
            CREATE TABLE quota_pools (
                quota_date TEXT NOT NULL, token_fingerprint TEXT NOT NULL,
                limit_rows INTEGER NOT NULL, safety_rows INTEGER NOT NULL,
                timezone TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY (quota_date, token_fingerprint)
            );
            CREATE TABLE request_attempts (
                reservation_id TEXT PRIMARY KEY, quota_date TEXT NOT NULL,
                token_fingerprint TEXT NOT NULL, consumer TEXT NOT NULL,
                state TEXT NOT NULL, requested_rows INTEGER NOT NULL,
                reserved_rows INTEGER NOT NULL, committed_rows INTEGER NOT NULL DEFAULT 0,
                over_limit INTEGER NOT NULL DEFAULT 0, error_kind TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, lease_expires_at TEXT
            );
            CREATE TABLE quota_holds (
                hold_id TEXT PRIMARY KEY, quota_date TEXT NOT NULL,
                token_fingerprint TEXT NOT NULL, consumer TEXT NOT NULL,
                rows INTEGER NOT NULL, state TEXT NOT NULL, note TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )

    ledger = _ledger(tmp_path)
    reservation = ledger.reserve(100, now=NOW)
    reservation.commit(80, now=NOW)
    status = ledger.status(now=NOW)

    assert status["schema_version"] == 2
    assert status["gate"] == "rows"
    assert status["committed_rows"] == 80
    assert status["committed_requests"] == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM quota_meta WHERE key = 'schema_version'"
        ).fetchone() == ("2",)
        pool_columns = {row[1] for row in connection.execute("PRAGMA table_info(quota_pools)")}
        hold_columns = {row[1] for row in connection.execute("PRAGMA table_info(quota_holds)")}
    assert {"gate", "limit_requests", "burst_limit_requests", "safety_requests"}.issubset(
        pool_columns
    )
    assert "request_slots" in hold_columns
