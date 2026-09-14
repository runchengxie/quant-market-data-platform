from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_data_platform.tushare_minute_quota import (
    DEFAULT_MINUTE_QUOTA_BURST_LIMIT_REQUESTS,
    DEFAULT_MINUTE_QUOTA_LIMIT_REQUESTS,
    DEFAULT_MINUTE_QUOTA_SAFETY_REQUESTS,
    MinuteQuotaConfig,
    MinuteQuotaConfigurationError,
    MinuteQuotaExceeded,
    MinuteQuotaGate,
    MinuteQuotaLedger,
    MinuteQuotaMode,
    MinuteQuotaRequestPolicy,
    resolve_minute_quota_config,
)

FINGERPRINT_KEY = b"test-only-stable-hmac-key-32-bytes-long"
TOKEN = "private-test-token-that-must-not-be-stored"
NOW = datetime(2026, 7, 17, 4, 0, tzinfo=UTC)


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


def test_default_request_pool_preserves_base_and_safety_only_reduces_burst() -> None:
    assert DEFAULT_MINUTE_QUOTA_LIMIT_REQUESTS == 10_000
    assert DEFAULT_MINUTE_QUOTA_BURST_LIMIT_REQUESTS == 20_000
    assert DEFAULT_MINUTE_QUOTA_SAFETY_REQUESTS == 500
    assert MinuteQuotaConfig().effective_limit_requests == 10_000
    assert (
        MinuteQuotaConfig(
            request_policy=MinuteQuotaRequestPolicy(allow_burst=True)
        ).effective_limit_requests
        == 19_500
    )


def test_request_policy_resolves_from_standard_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MDP_TUSHARE_MINUTE_QUOTA_GATE", "requests")
    monkeypatch.setenv("MDP_TUSHARE_MINUTE_QUOTA_LIMIT_REQUESTS", "10000")
    monkeypatch.setenv("MDP_TUSHARE_MINUTE_QUOTA_BURST_LIMIT_REQUESTS", "20000")
    monkeypatch.setenv("MDP_TUSHARE_MINUTE_QUOTA_SAFETY_REQUESTS", "500")
    monkeypatch.setenv("MDP_TUSHARE_MINUTE_QUOTA_ALLOW_BURST", "true")

    config = resolve_minute_quota_config(
        mode="observe",
        database_path=tmp_path / "quota.sqlite3",
    )

    assert config.gate == "requests"
    assert config.limit_requests == 10_000
    assert config.burst_limit_requests == 20_000
    assert config.safety_requests == 500
    assert config.allow_burst is True
    assert config.effective_limit_requests == 19_500


def test_same_token_day_cannot_silently_switch_rows_pool_to_requests(tmp_path: Path) -> None:
    _ledger(tmp_path).reserve(1, now=NOW)
    request_gate = _ledger(tmp_path, request_policy=_request_policy(gate="requests"))

    with pytest.raises(MinuteQuotaConfigurationError, match="pool policy mismatch"):
        request_gate.reserve(1, now=NOW)


def test_commit_hold_release_and_enforced_rejection(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, limit_rows=1_000, safety_rows=100)
    hold_id = ledger.create_hold(300, consumer="watch20", note="daily reserve", now=NOW)
    reservation = ledger.reserve(500, now=NOW)
    reservation.commit(400, now=NOW)

    status = ledger.status(now=NOW)
    assert status["committed_rows"] == 400
    assert status["hold_rows"] == 300
    assert status["charged_rows"] == 700
    assert status["available_rows"] == 200

    with pytest.raises(MinuteQuotaExceeded) as error:
        ledger.reserve(201, consumer="campaign", now=NOW)
    assert error.value.available_rows == 200

    ledger.release_hold(hold_id, now=NOW)
    assert ledger.status(now=NOW)["available_rows"] == 500


def test_hold_is_a_residual_target_for_its_consumer(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, limit_rows=1_000)
    ledger.create_hold(300, consumer="watch20", now=NOW)

    reservation = ledger.reserve(200, consumer="watch20", now=NOW)
    status = ledger.status(now=NOW)

    assert status["reserved_rows"] == 200
    assert status["hold_target_rows"] == 300
    assert status["hold_rows"] == 100
    assert status["charged_rows"] == 300
    assert status["available_rows"] == 700
    watch20 = next(item for item in status["consumers"] if item["consumer"] == "watch20")
    assert watch20["charged_rows"] == 300
    reservation.commit(200, now=NOW)
    assert ledger.status(now=NOW)["charged_rows"] == 300


def test_request_gate_charges_one_slot_for_empty_small_and_full_responses(
    tmp_path: Path,
) -> None:
    ledger = _ledger(
        tmp_path,
        request_policy=_request_policy(
            gate="requests",
            limit_requests=3,
            burst_limit_requests=6,
            safety_requests=1,
        ),
    )

    for requested_rows, actual_rows in ((241, 0), (482, 1), (7_953, 7_953)):
        reservation = ledger.reserve(requested_rows, now=NOW)
        reservation.commit(actual_rows, now=NOW)

    status = ledger.status(now=NOW)
    assert status["committed_requests"] == 3
    assert status["charged_requests"] == 3
    assert status["committed_rows"] == 7_954
    assert status["available_requests"] == 0
    with pytest.raises(MinuteQuotaExceeded) as error:
        ledger.reserve(1, now=NOW)
    assert error.value.unit == "requests"
    assert error.value.requested_requests == 1
    assert error.value.available_requests == 0


def test_dual_gate_enforces_rows_and_request_slots_independently(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        limit_rows=2_000,
        safety_rows=0,
        request_policy=_request_policy(
            gate="dual",
            limit_requests=2,
            burst_limit_requests=4,
            safety_requests=0,
        ),
    )

    first = ledger.reserve(1_800, now=NOW)
    first.commit(1_800, now=NOW)
    with pytest.raises(MinuteQuotaExceeded) as row_error:
        ledger.reserve(201, now=NOW)
    assert row_error.value.unit == "rows"

    second = ledger.reserve(100, now=NOW)
    second.commit(100, now=NOW)
    with pytest.raises(MinuteQuotaExceeded) as request_error:
        ledger.reserve(1, now=NOW)
    assert request_error.value.unit == "requests"

    status = ledger.status(now=NOW)
    assert status["charged_rows"] == 1_900
    assert status["charged_requests"] == 2
    assert status["rejected_attempts"] == 2


def test_released_and_rejected_attempts_do_not_consume_request_slots(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        request_policy=_request_policy(
            gate="requests",
            limit_requests=1,
            burst_limit_requests=2,
            safety_requests=0,
        ),
    )
    released = ledger.reserve(7_953, now=NOW)
    released.release(now=NOW)
    committed = ledger.reserve(7_953, now=NOW)
    committed.commit(0, now=NOW)
    with pytest.raises(MinuteQuotaExceeded):
        ledger.reserve(1, now=NOW)

    status = ledger.status(now=NOW)
    assert status["charged_requests"] == 1
    assert status["released_requests"] == 1
    assert status["rejected_attempts"] == 1
    assert status["attempts"] == 3


def test_request_gate_is_atomic_across_concurrent_workers(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        request_policy=_request_policy(
            gate="requests",
            limit_requests=20,
            burst_limit_requests=40,
            safety_requests=5,
        ),
    )

    def reserve_once(_index: int) -> bool:
        try:
            ledger.reserve(1, now=NOW)
        except MinuteQuotaExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = list(pool.map(reserve_once, range(50)))

    assert sum(accepted) == 20
    status = ledger.status(now=NOW)
    assert status["reserved_requests"] == 20
    assert status["rejected_attempts"] == 30


def test_request_hold_is_residual_idempotent_and_release_is_idempotent(
    tmp_path: Path,
) -> None:
    ledger = _ledger(
        tmp_path,
        request_policy=_request_policy(
            gate="requests",
            limit_requests=5,
            burst_limit_requests=10,
            safety_requests=1,
        ),
    )
    hold_id = ledger.create_request_hold(2, consumer="priority", now=NOW)
    assert ledger.create_request_hold(2, consumer="priority", now=NOW) == hold_id

    for _index in range(3):
        ledger.reserve(1, consumer="campaign", now=NOW)
    with pytest.raises(MinuteQuotaExceeded):
        ledger.reserve(1, consumer="campaign", now=NOW)

    first = ledger.reserve(1, consumer="priority", now=NOW)
    first.commit(0, now=NOW)
    second = ledger.reserve(1, consumer="priority", now=NOW)
    second.commit(241, now=NOW)
    status = ledger.status(now=NOW)
    priority = next(item for item in status["consumers"] if item["consumer"] == "priority")
    assert status["charged_requests"] == 5
    assert priority["hold_target_requests"] == 2
    assert priority["hold_requests"] == 0
    assert status["active_request_holds"][0]["hold_id"] == hold_id

    ledger.release_hold(hold_id, now=NOW)
    ledger.release_hold(hold_id, now=NOW)
    assert ledger.status(now=NOW)["active_request_holds"] == []


def test_burst_authorization_expands_same_pool_only_to_safety_adjusted_ceiling(
    tmp_path: Path,
) -> None:
    base = _ledger(
        tmp_path,
        request_policy=_request_policy(
            gate="requests",
            limit_requests=3,
            burst_limit_requests=6,
            safety_requests=1,
        ),
    )
    for _index in range(3):
        base.reserve(1, now=NOW)
    with pytest.raises(MinuteQuotaExceeded):
        base.reserve(1, now=NOW)

    burst = _ledger(
        tmp_path,
        request_policy=_request_policy(
            gate="requests",
            limit_requests=3,
            burst_limit_requests=6,
            safety_requests=1,
            allow_burst=True,
        ),
    )
    burst.reserve(1, now=NOW)
    burst.reserve(1, now=NOW)
    with pytest.raises(MinuteQuotaExceeded):
        burst.reserve(1, now=NOW)

    status = burst.status(now=NOW)
    assert status["effective_limit_requests"] == 5
    assert status["charged_requests"] == 5
    assert status["available_requests"] == 0


def test_request_gate_resets_at_asia_shanghai_midnight(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        request_policy=_request_policy(
            gate="requests",
            limit_requests=1,
            burst_limit_requests=2,
            safety_requests=0,
        ),
    )
    before_midnight = datetime(2026, 7, 17, 15, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 7, 17, 16, 1, tzinfo=UTC)

    ledger.reserve(1, now=before_midnight)
    with pytest.raises(MinuteQuotaExceeded):
        ledger.reserve(1, now=before_midnight)
    ledger.reserve(1, now=after_midnight)

    assert ledger.status(quota_date="20260717", now=after_midnight)["charged_requests"] == 1
    assert ledger.status(now=after_midnight)["charged_requests"] == 1
