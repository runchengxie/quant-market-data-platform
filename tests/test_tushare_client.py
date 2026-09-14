from __future__ import annotations

from market_data_platform.providers import _client
from market_data_platform.providers.tushare_common import request_policy


class _FakeClient:
    _DataApi__http_url = ""


class _FakeTushare:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def pro_api(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeClient()


def test_get_tushare_client_passes_explicit_timeout(monkeypatch) -> None:
    fake_tushare = _FakeTushare()
    monkeypatch.setattr(_client, "_require_module", lambda *args, **kwargs: fake_tushare)

    _client.get_tushare_client(
        token="test-token",
        api_url="https://example.test",
        request_timeout_seconds=15,
    )

    assert fake_tushare.calls == [{"token": "test-token", "timeout": 15.0}]


def test_request_policy_validates_and_serializes_timeout() -> None:
    policy = request_policy(request_timeout_seconds=12.5)
    assert policy.request_timeout_seconds == 12.5


def test_request_policy_rejects_non_positive_timeout() -> None:
    import pytest

    with pytest.raises(ValueError, match="request_timeout_seconds"):
        request_policy(request_timeout_seconds=0)
