"""Runtime client and retry/error handling for the TuShare A-share provider."""

from __future__ import annotations

import importlib
import os
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from typing import Any

from market_data_platform.providers._env import (
    _load_tushare_env_files,
    _normalize_api_url,
    _resolve_token,
    resolve_tushare_api_url,
    resolve_tushare_api_urls,
)
from market_data_platform.providers.tushare_a_share_options import (
    DEFAULT_DISABLE_PROXY,
    DEFAULT_TOKEN_ENV_KEYS,
    NO_PROXY_ENV_KEYS,
    PROXY_ENV_KEYS,
    TushareRequestPolicy,
)


def _require_module(name: str, *, install_hint: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise RuntimeError(f"{name} is required for this command. {install_hint}") from exc


def _pandas() -> Any:
    return _require_module("pandas", install_hint="Install the tushare optional dependencies.")


from market_data_platform.providers._frame import _prepare_frame  # noqa: E402


@contextmanager
def _tushare_proxy_env(*, disable_proxy: bool):
    if not disable_proxy:
        yield
        return

    keys = (*PROXY_ENV_KEYS, *NO_PROXY_ENV_KEYS)
    saved = {key: os.environ.get(key) for key in keys}
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    for key in NO_PROXY_ENV_KEYS:
        os.environ[key] = "*"
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _provider_error_text(error: Exception) -> str:
    return str(error) or error.__class__.__name__


def _is_quota_error(error: Exception) -> bool:
    message = _provider_error_text(error).lower()
    return (
        "频率超限" in message
        or "访问频率已超速" in message
        or "超速" in message
        or "冷却" in message
        or "rate limit" in message
        or "too many requests" in message
        or "官方限速" in message
        or "增加等待几秒重试" in message
        or "请求过于频繁" in message
        or "quota" in message
    )


def _is_daily_quota_exhausted(error: Exception) -> bool:
    """Recognize only explicit provider messages that close the current quota day."""

    message = _provider_error_text(error).lower()
    fragments = (
        "今日请求次数已达上限",
        "今日访问次数已达上限",
        "今日请求次数已用完",
        "单日请求次数已达上限",
        "单日总容量已达上限",
        "每日请求次数已耗尽",
        "每日请求次数已用完",
        "daily request limit exceeded",
        "daily request capacity exhausted",
        "daily quota exhausted",
    )
    return any(fragment in message for fragment in fragments)


def _is_retryable_provider_error(error: Exception) -> bool:
    from market_data_platform.tushare_minute_quota import (
        AmbiguousMinuteRequestError,
        MinuteQuotaExceeded,
        MinuteQuotaPoolClosed,
    )

    if isinstance(
        error,
        (AmbiguousMinuteRequestError, MinuteQuotaExceeded, MinuteQuotaPoolClosed),
    ):
        return False
    if _is_daily_quota_exhausted(error):
        return False
    if isinstance(error, OSError) and _provider_error_text(error).strip().upper() == "ERROR.":
        return True
    if _is_quota_error(error):
        return True
    message = _provider_error_text(error).lower()
    retryable_fragments = (
        "timed out",
        "timeout",
        "proxyerror",
        "proxy error",
        "max retries exceeded",
        "response ended prematurely",
        "connection aborted",
        "connection reset",
        "remote disconnected",
        "temporarily unavailable",
        "temporary failure",
        "502",
        "503",
        "504",
    )
    return any(fragment in message for fragment in retryable_fragments)


def _retry_sleep_seconds(
    policy: TushareRequestPolicy,
    *,
    attempt_index: int,
    error: Exception,
) -> float:
    if _is_quota_error(error):
        return policy.quota_cooldown_seconds
    sleep = policy.retry_sleep_seconds * (2 ** max(0, attempt_index - 1))
    return min(sleep, policy.retry_max_sleep_seconds)


def _call_tushare_api(
    call: Callable[[], Any],
    *,
    policy: TushareRequestPolicy,
) -> Any:
    for attempt in range(1, policy.attempts + 1):
        try:
            with _tushare_proxy_env(disable_proxy=policy.disable_proxy):
                return call()
        except Exception as exc:
            if attempt >= policy.attempts or not _is_retryable_provider_error(exc):
                raise
            sleep_seconds = _retry_sleep_seconds(policy, attempt_index=attempt, error=exc)
            if sleep_seconds > 0.0:
                time.sleep(sleep_seconds)
    raise RuntimeError("unreachable TuShare retry state")


def _call_tushare_endpoint(client: Any, api_name: str, api_kwargs: dict[str, Any]) -> Any:
    endpoint = getattr(client, api_name, None)
    if callable(endpoint):
        return endpoint(**api_kwargs)
    query = getattr(client, "query", None)
    if callable(query):
        return query(api_name, **api_kwargs)
    raise AttributeError(f"TuShare client does not expose {api_name} or query().")


def _call_trade_date_endpoint_frame(
    client: Any,
    api_name: str,
    api_kwargs: dict[str, Any],
    *,
    policy: TushareRequestPolicy,
) -> Any:
    return _prepare_frame(
        _call_tushare_api(
            lambda api=api_name, call_kwargs=dict(api_kwargs): _call_tushare_endpoint(
                client, api, call_kwargs
            ),
            policy=policy,
        )
    )


def _configure_tushare_client_api_url(client: Any, api_url: str | None) -> Any:
    resolved = _normalize_api_url(api_url)
    if resolved:
        client._DataApi__http_url = resolved
    return client


class _TushareEndpointFailover:
    """Proxy a TuShare client and rotate endpoints on transport failures only."""

    def __init__(self, client: Any, api_urls: tuple[str | None, ...]) -> None:
        self._client = client
        self._api_urls = api_urls or (None,)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_client", "_api_urls"} or "_client" not in self.__dict__:
            object.__setattr__(self, name, value)
        else:
            setattr(self._client, name, value)

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._client, name)
        if not callable(attribute) or len(self._api_urls) <= 1:
            return attribute

        def call_with_failover(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            for api_url in self._api_urls:
                _configure_tushare_client_api_url(self._client, api_url)
                try:
                    return attribute(*args, **kwargs)
                except Exception as exc:
                    last_error = exc
                    if not _is_retryable_provider_error(exc):
                        raise
            assert last_error is not None
            raise last_error

        return call_with_failover


def _redact_error(error: Exception, token: str) -> str:
    message = str(error) or error.__class__.__name__
    return message.replace(token, "<redacted>") if token else message


def get_tushare_client(
    *,
    token: str | None = None,
    token_env: str = "TUSHARE_TOKEN",
    api_url: str | None = None,
    disable_proxy: bool = DEFAULT_DISABLE_PROXY,
    request_timeout_seconds: float | None = None,
) -> Any:
    ts = _require_module("tushare", install_hint="Install with the tushare optional extra.")
    resolved_api_url = resolve_tushare_api_url(api_url, token_env=token_env)
    api_urls = resolve_tushare_api_urls(api_url, token_env=token_env)
    client_options: dict[str, Any] = {"token": _resolve_token(token, token_env)}
    if request_timeout_seconds is not None:
        timeout = float(request_timeout_seconds)
        if timeout <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        client_options["timeout"] = timeout
    with _tushare_proxy_env(disable_proxy=disable_proxy):
        client = ts.pro_api(**client_options)
    configured = _configure_tushare_client_api_url(client, resolved_api_url)
    return _TushareEndpointFailover(configured, api_urls)


def verify_tushare_tokens(
    *,
    env_keys: Iterable[str] | None = None,
    api_url: str | None = None,
    disable_proxy: bool = DEFAULT_DISABLE_PROXY,
    tushare_module: Any | None = None,
) -> dict[str, Any]:
    _load_tushare_env_files()
    ts = tushare_module or _require_module(
        "tushare",
        install_hint="Install with the tushare optional extra.",
    )
    results: list[dict[str, Any]] = []
    for env_key in env_keys or DEFAULT_TOKEN_ENV_KEYS:
        key = str(env_key).strip()
        token = str(os.environ.get(key) or "").strip()
        resolved_api_url = resolve_tushare_api_url(api_url, token_env=key)
        if not token:
            results.append(
                {
                    "env": key,
                    "configured": False,
                    "valid": False,
                    "api_url": resolved_api_url,
                    "error": "not set",
                }
            )
            continue
        try:
            with _tushare_proxy_env(disable_proxy=disable_proxy):
                client = ts.pro_api(token=token)
                _configure_tushare_client_api_url(client, resolved_api_url)
                response = client.trade_cal(
                    exchange="",
                    start_date="20200101",
                    end_date="20200110",
                )
            if response is None:
                raise RuntimeError("TuShare returned no trade_cal response")
        except Exception as exc:  # Provider errors are reported without exposing the token.
            results.append(
                {
                    "env": key,
                    "configured": True,
                    "valid": False,
                    "api_url": resolved_api_url,
                    "error": _redact_error(exc, token),
                }
            )
            continue
        results.append(
            {
                "env": key,
                "configured": True,
                "valid": True,
                "api_url": resolved_api_url,
            }
        )
    valid_count = sum(1 for result in results if result["valid"])
    return {
        "provider": "tushare",
        "checked": len(results),
        "valid_tokens": valid_count,
        "results": results,
    }
