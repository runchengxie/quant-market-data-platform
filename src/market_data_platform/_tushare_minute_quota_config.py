"""Configuration, identity, and clock helpers for minute quota accounting."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo

from market_data_platform.paths import resolve_artifacts_root

MINUTE_QUOTA_TIMEZONE = "Asia/Shanghai"
DEFAULT_MINUTE_QUOTA_LIMIT_ROWS = 80_000_000
DEFAULT_MINUTE_QUOTA_SAFETY_ROWS = 10_000_000
DEFAULT_MINUTE_QUOTA_GATE = "rows"
DEFAULT_MINUTE_QUOTA_LIMIT_REQUESTS = 10_000
DEFAULT_MINUTE_QUOTA_BURST_LIMIT_REQUESTS = 20_000
DEFAULT_MINUTE_QUOTA_SAFETY_REQUESTS = 500
DEFAULT_MINUTE_QUOTA_ALLOW_BURST = False
DEFAULT_MINUTE_QUOTA_LEASE_SECONDS = 15 * 60
DEFAULT_MINUTE_QUOTA_BUSY_TIMEOUT_MS = 30_000
DEFAULT_MINUTE_QUOTA_CONSUMER = "minute-mirror"
DEFAULT_MINUTE_QUOTA_RELATIVE_PATH = (
    Path("metadata") / "tushare" / "minute_quota" / "minute_quota.sqlite3"
)

MINUTE_QUOTA_MODE_ENV = "MDP_TUSHARE_MINUTE_QUOTA_MODE"
MINUTE_QUOTA_DB_ENV = "MDP_TUSHARE_MINUTE_QUOTA_DB"
MINUTE_QUOTA_CONSUMER_ENV = "MDP_TUSHARE_MINUTE_QUOTA_CONSUMER"
MINUTE_QUOTA_LIMIT_ROWS_ENV = "MDP_TUSHARE_MINUTE_QUOTA_LIMIT_ROWS"
MINUTE_QUOTA_SAFETY_ROWS_ENV = "MDP_TUSHARE_MINUTE_QUOTA_SAFETY_ROWS"
MINUTE_QUOTA_GATE_ENV = "MDP_TUSHARE_MINUTE_QUOTA_GATE"
MINUTE_QUOTA_LIMIT_REQUESTS_ENV = "MDP_TUSHARE_MINUTE_QUOTA_LIMIT_REQUESTS"
MINUTE_QUOTA_BURST_LIMIT_REQUESTS_ENV = "MDP_TUSHARE_MINUTE_QUOTA_BURST_LIMIT_REQUESTS"
MINUTE_QUOTA_SAFETY_REQUESTS_ENV = "MDP_TUSHARE_MINUTE_QUOTA_SAFETY_REQUESTS"
MINUTE_QUOTA_ALLOW_BURST_ENV = "MDP_TUSHARE_MINUTE_QUOTA_ALLOW_BURST"
MINUTE_QUOTA_HMAC_KEY_ENV = "MDP_TUSHARE_MINUTE_QUOTA_HMAC_KEY"

MinuteQuotaMode = Literal["off", "observe", "enforce"]
MinuteQuotaGate = Literal["rows", "requests", "dual"]
_VALID_MODES = frozenset({"off", "observe", "enforce"})
_VALID_GATES = frozenset({"rows", "requests", "dual"})
_QUOTA_TZ = ZoneInfo(MINUTE_QUOTA_TIMEZONE)


class MinuteQuotaError(RuntimeError):
    """Base class for minute quota accounting failures."""


class MinuteQuotaExceeded(MinuteQuotaError):
    """Raised before a request when an enforced pool cannot reserve it."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        quota_date: str,
        requested_rows: int,
        available_rows: int,
        consumer: str,
        unit: Literal["rows", "requests"] = "rows",
        requested_requests: int = 0,
        available_requests: int = 0,
    ) -> None:
        self.quota_date = quota_date
        self.requested_rows = requested_rows
        self.available_rows = available_rows
        self.unit = unit
        self.requested_requests = requested_requests
        self.available_requests = available_requests
        self.consumer = consumer
        detail = (
            f"requested_rows={requested_rows}, available_rows={available_rows}"
            if unit == "rows"
            else (
                f"requested_requests={requested_requests}, available_requests={available_requests}"
            )
        )
        super().__init__(
            f"TuShare minute quota reservation refused for {consumer} on {quota_date}: {detail}"
        )


class MinuteQuotaPoolClosed(MinuteQuotaError):
    """Raised when a provider-confirmed condition closed the quota day."""

    def __init__(self, *, quota_date: str, consumer: str, reason: str) -> None:
        self.quota_date = quota_date
        self.consumer = consumer
        self.reason = reason
        super().__init__(
            f"TuShare minute quota pool is closed for {consumer} on {quota_date}: {reason}"
        )


class AmbiguousMinuteRequestError(MinuteQuotaError):
    """A physical minute request may have reached TuShare but returned no evidence."""


class MinuteQuotaConfigurationError(MinuteQuotaError):
    """Raised when consumers disagree about a quota pool's immutable policy."""


@dataclass(frozen=True)
class MinuteQuotaRequestPolicy:
    """Resolved request-count gate policy for one shared quota pool."""

    gate: MinuteQuotaGate = DEFAULT_MINUTE_QUOTA_GATE
    limit_requests: int = DEFAULT_MINUTE_QUOTA_LIMIT_REQUESTS
    burst_limit_requests: int = DEFAULT_MINUTE_QUOTA_BURST_LIMIT_REQUESTS
    safety_requests: int = DEFAULT_MINUTE_QUOTA_SAFETY_REQUESTS
    allow_burst: bool = DEFAULT_MINUTE_QUOTA_ALLOW_BURST

    def __post_init__(self) -> None:
        if self.gate not in _VALID_GATES:
            raise ValueError(f"minute quota gate must be one of {sorted(_VALID_GATES)}")
        if self.limit_requests <= 0:
            raise ValueError("minute quota limit_requests must be positive")
        if self.burst_limit_requests < self.limit_requests:
            raise ValueError("minute quota burst_limit_requests must be at least limit_requests")
        if not 0 <= self.safety_requests < self.burst_limit_requests:
            raise ValueError(
                "minute quota safety_requests must be non-negative and below burst_limit_requests"
            )
        if self.burst_limit_requests - self.safety_requests < self.limit_requests:
            raise ValueError(
                "minute quota burst capacity after safety must preserve limit_requests"
            )

    @property
    def effective_limit_requests(self) -> int:
        """Return the base pool or the safety-adjusted optional burst pool."""

        if not self.allow_burst:
            return self.limit_requests
        return self.burst_limit_requests - self.safety_requests


@dataclass(frozen=True)
class MinuteQuotaRequestOverrides:
    """Optional request-policy values layered over standard environment settings."""

    gate: str | None = None
    limit_requests: int | None = None
    burst_limit_requests: int | None = None
    safety_requests: int | None = None
    allow_burst: bool | str | None = None


@dataclass(frozen=True)
class MinuteQuotaConfig:
    """Resolved runtime configuration for the shared minute quota ledger."""

    mode: MinuteQuotaMode = "off"
    database_path: Path | None = None
    consumer: str = DEFAULT_MINUTE_QUOTA_CONSUMER
    limit_rows: int = DEFAULT_MINUTE_QUOTA_LIMIT_ROWS
    safety_rows: int = DEFAULT_MINUTE_QUOTA_SAFETY_ROWS
    request_policy: MinuteQuotaRequestPolicy = MinuteQuotaRequestPolicy()
    lease_seconds: int = DEFAULT_MINUTE_QUOTA_LEASE_SECONDS
    busy_timeout_ms: int = DEFAULT_MINUTE_QUOTA_BUSY_TIMEOUT_MS

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(f"minute quota mode must be one of {sorted(_VALID_MODES)}")
        if self.mode != "off" and self.database_path is None:
            raise ValueError("minute quota database_path is required unless mode=off")
        validate_consumer(self.consumer)
        if self.limit_rows <= 0:
            raise ValueError("minute quota limit_rows must be positive")
        if not 0 <= self.safety_rows < self.limit_rows:
            raise ValueError("minute quota safety_rows must be non-negative and below limit_rows")
        if self.lease_seconds <= 0:
            raise ValueError("minute quota lease_seconds must be positive")
        if self.busy_timeout_ms <= 0:
            raise ValueError("minute quota busy_timeout_ms must be positive")

    @property
    def gate(self) -> MinuteQuotaGate:
        """Return the request policy's active gate."""

        return self.request_policy.gate

    @property
    def limit_requests(self) -> int:
        """Return the base daily request limit."""

        return self.request_policy.limit_requests

    @property
    def burst_limit_requests(self) -> int:
        """Return the authorized daily burst ceiling."""

        return self.request_policy.burst_limit_requests

    @property
    def safety_requests(self) -> int:
        """Return the safety reserve below the burst ceiling."""

        return self.request_policy.safety_requests

    @property
    def allow_burst(self) -> bool:
        """Return whether this consumer may use burst capacity."""

        return self.request_policy.allow_burst

    @property
    def effective_limit_requests(self) -> int:
        """Return the base pool or the safety-adjusted optional burst pool."""

        return self.request_policy.effective_limit_requests


def _optional_int(value: int | str | None, *, default: int, name: str) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _optional_bool(value: bool | str | None, *, default: bool, name: str) -> bool:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _resolve_request_policy(
    overrides: MinuteQuotaRequestOverrides | None,
) -> MinuteQuotaRequestPolicy:
    values = overrides or MinuteQuotaRequestOverrides()
    resolved_gate = (
        str(values.gate or os.environ.get(MINUTE_QUOTA_GATE_ENV, DEFAULT_MINUTE_QUOTA_GATE))
        .strip()
        .lower()
    )
    return MinuteQuotaRequestPolicy(
        gate=cast(MinuteQuotaGate, resolved_gate),
        limit_requests=_optional_int(
            values.limit_requests
            if values.limit_requests is not None
            else os.environ.get(MINUTE_QUOTA_LIMIT_REQUESTS_ENV),
            default=DEFAULT_MINUTE_QUOTA_LIMIT_REQUESTS,
            name="minute quota limit requests",
        ),
        burst_limit_requests=_optional_int(
            values.burst_limit_requests
            if values.burst_limit_requests is not None
            else os.environ.get(MINUTE_QUOTA_BURST_LIMIT_REQUESTS_ENV),
            default=DEFAULT_MINUTE_QUOTA_BURST_LIMIT_REQUESTS,
            name="minute quota burst limit requests",
        ),
        safety_requests=_optional_int(
            values.safety_requests
            if values.safety_requests is not None
            else os.environ.get(MINUTE_QUOTA_SAFETY_REQUESTS_ENV),
            default=DEFAULT_MINUTE_QUOTA_SAFETY_REQUESTS,
            name="minute quota safety requests",
        ),
        allow_burst=_optional_bool(
            values.allow_burst
            if values.allow_burst is not None
            else os.environ.get(MINUTE_QUOTA_ALLOW_BURST_ENV),
            default=DEFAULT_MINUTE_QUOTA_ALLOW_BURST,
            name="minute quota allow burst",
        ),
    )


def resolve_minute_quota_config(  # noqa: PLR0913
    *,
    mode: str | None = None,
    database_path: str | Path | None = None,
    consumer: str | None = None,
    limit_rows: int | None = None,
    safety_rows: int | None = None,
    request_overrides: MinuteQuotaRequestOverrides | None = None,
) -> MinuteQuotaConfig:
    """Resolve CLI/Python overrides over environment defaults."""

    resolved_mode = str(mode or os.environ.get(MINUTE_QUOTA_MODE_ENV, "off")).strip().lower()
    configured_path = database_path or os.environ.get(MINUTE_QUOTA_DB_ENV)
    resolved_path = (
        Path(configured_path).expanduser()
        if configured_path
        else resolve_artifacts_root() / DEFAULT_MINUTE_QUOTA_RELATIVE_PATH
    )
    resolved_consumer = str(
        consumer or os.environ.get(MINUTE_QUOTA_CONSUMER_ENV, DEFAULT_MINUTE_QUOTA_CONSUMER)
    ).strip()
    resolved_limit = _optional_int(
        limit_rows if limit_rows is not None else os.environ.get(MINUTE_QUOTA_LIMIT_ROWS_ENV),
        default=DEFAULT_MINUTE_QUOTA_LIMIT_ROWS,
        name="minute quota limit rows",
    )
    resolved_safety = _optional_int(
        safety_rows if safety_rows is not None else os.environ.get(MINUTE_QUOTA_SAFETY_ROWS_ENV),
        default=DEFAULT_MINUTE_QUOTA_SAFETY_ROWS,
        name="minute quota safety rows",
    )
    return MinuteQuotaConfig(
        mode=cast(MinuteQuotaMode, resolved_mode),
        database_path=resolved_path,
        consumer=resolved_consumer,
        limit_rows=resolved_limit,
        safety_rows=resolved_safety,
        request_policy=_resolve_request_policy(request_overrides),
    )


def quota_date_at(now: datetime | None = None) -> str:
    """Return the provider quota date in Asia/Shanghai as ``YYYYMMDD``."""

    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("quota accounting timestamps must be timezone-aware")
    return instant.astimezone(_QUOTA_TZ).strftime("%Y%m%d")


def token_fingerprint(token: str, *, key: bytes) -> str:
    """Build a stable opaque identity without persisting the credential."""

    value = str(token).strip()
    if not value:
        raise ValueError("TuShare token is empty")
    if not key:
        raise ValueError("minute quota HMAC key is empty")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def validate_consumer(consumer: str) -> None:
    if not consumer or len(consumer) > 128:
        raise ValueError("minute quota consumer must contain 1-128 characters")
    if any(ord(character) < 32 for character in consumer):
        raise ValueError("minute quota consumer cannot contain control characters")


def utc_now(now: datetime | None = None) -> datetime:
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("quota accounting timestamps must be timezone-aware")
    return instant.astimezone(UTC)


def timestamp(now: datetime | None = None) -> str:
    return utc_now(now).isoformat(timespec="microseconds")


def _fingerprint_key_path(database_path: Path) -> Path:
    return database_path.with_name(f".{database_path.name}.hmac-key")


def read_or_create_fingerprint_key(database_path: Path) -> bytes:
    configured = os.environ.get(MINUTE_QUOTA_HMAC_KEY_ENV)
    if configured:
        return configured.encode("utf-8")

    key_path = _fingerprint_key_path(database_path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path = key_path.with_name(
        f".{key_path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    try:
        descriptor = os.open(candidate_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(secrets.token_bytes(32))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(candidate_path, key_path)
            except FileExistsError:
                pass
        finally:
            candidate_path.unlink(missing_ok=True)
    except BaseException:
        candidate_path.unlink(missing_ok=True)
        raise
    key = key_path.read_bytes()
    if len(key) < 32:
        raise MinuteQuotaConfigurationError(f"minute quota HMAC key is invalid: {key_path}")
    return key
