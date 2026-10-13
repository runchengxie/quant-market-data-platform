from __future__ import annotations

import ssl

import pandas as pd
import pytest

from quant_market_data_platform.providers import _client
from quant_market_data_platform.providers._mins_fetch import _fetch_minute_batch
from quant_market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
from quant_market_data_platform.tushare_minute_quota import (
    MinuteQuotaConfig,
    MinuteQuotaLedger,
    MinuteQuotaRequestPolicy,
)


@pytest.mark.parametrize(
    "message",
    [
        "EOF occurred in violation of protocol",
        "SSLEOFError: peer closed the TLS connection",
        "UNEXPECTED_EOF_WHILE_READING",
    ],
)
def test_tls_eof_retries_with_the_configured_budget(monkeypatch, message):
    attempts = []
    sleeps = []
    monkeypatch.setattr(_client.time, "sleep", sleeps.append)

    def request():
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise ssl.SSLEOFError(8, message)
        return "response"

    result = _client._call_tushare_api(
        request,
        policy=TushareRequestPolicy(
            attempts=3, retry_sleep_seconds=0.25, retry_max_sleep_seconds=0.5
        ),
    )
    assert result == "response"
    assert attempts == [1, 2, 3]
    assert sleeps == [0.25, 0.5]


@pytest.mark.parametrize(
    "message",
    [
        "Max retries exceeded: CERTIFICATE_VERIFY_FAILED",
        "Max retries exceeded: certificate verify failed",
        "Max retries exceeded: hostname mismatch",
    ],
)
def test_certificate_failures_do_not_retry(monkeypatch, message):
    attempts = []
    monkeypatch.setattr(_client.time, "sleep", lambda _: pytest.fail("Unexpected retry"))

    def request():
        attempts.append(1)
        raise ssl.SSLError(1, message)

    with pytest.raises(ssl.SSLError, match="Max retries exceeded"):
        _client._call_tushare_api(request, policy=TushareRequestPolicy(attempts=3))
    assert attempts == [1]


def test_certificate_failure_does_not_rotate_configured_endpoints():
    calls = []

    class Client:
        _DataApi__http_url = ""

        def daily(self):
            calls.append(self._DataApi__http_url)
            raise ssl.SSLError(1, "Max retries exceeded: CERTIFICATE_VERIFY_FAILED")

    proxy = _client._TushareEndpointFailover(
        Client(), ("https://first.example.test", "https://second.example.test")
    )
    with pytest.raises(ssl.SSLError):
        proxy.daily()
    assert calls == ["https://first.example.test"]


def test_retried_minute_eof_keeps_ambiguous_attempt_charged(tmp_path):
    ledger = MinuteQuotaLedger(
        MinuteQuotaConfig(
            mode="enforce",
            database_path=tmp_path / "quota.sqlite3",
            consumer="reverse_backfill",
            request_policy=MinuteQuotaRequestPolicy(
                gate="requests", limit_requests=3, burst_limit_requests=6, safety_requests=1
            ),
        ),
        token="fixture-token",
    )

    class Client:
        calls = 0

        def stk_mins(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ssl.SSLEOFError(8, "EOF occurred in violation of protocol")
            return pd.DataFrame({"ts_code": ["000001.SZ"]})

    client = Client()
    frame = _fetch_minute_batch(
        client,
        ["000001.SZ"],
        "20220211",
        "1min",
        TushareRequestPolicy(attempts=2, retry_sleep_seconds=0),
        ledger,
    )
    status = ledger.status()
    assert len(frame) == 1
    assert client.calls == 2
    assert status["uncertain_requests"] == 1
    assert status["committed_requests"] == 1
    assert status["available_requests"] == 1
