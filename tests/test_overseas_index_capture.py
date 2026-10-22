import hashlib
import json

import pandas as pd
import pytest

from quant_market_data_platform import overseas_index_capture as capture


class Client:
    def __init__(self):
        self.calls = []

    def index_global(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["ts_code"] == "N225":
            raise PermissionError("credential=secret")
        if kwargs["ts_code"] == "KS11":
            return pd.DataFrame({"ts_code": [], "trade_date": [], "close": []})
        return pd.DataFrame(
            {"ts_code": ["HSI", "HSI"], "trade_date": ["20261020", "20261021"], "close": [100, 101]}
        )


def test_capture_binds_raw_bytes_and_preserves_partial_failures(tmp_path):
    client = Client()
    path = capture.capture(tmp_path, "20261021", ["HSI", "N225", "KS11"], client=client)
    data = json.loads(path.read_text())
    assert data["protocol"] == "overseas-index-operational-v1"
    assert data["status"] == "partial"
    assert len(client.calls) == 3
    assert client.calls[0] == {"ts_code": "HSI", "start_date": "20260930", "end_date": "20261021"}
    hsi = data["sources"]["HSI"]
    assert hsi["rows"] == 2
    assert hashlib.sha256((path.parent / hsi["file"]).read_bytes()).hexdigest() == hsi["sha256"]
    assert data["sources"]["N225"]["error_class"] == "PermissionError"
    assert data["sources"]["KS11"]["status"] == "empty"
    assert "secret" not in path.read_text()
    assert (path.parent / "started.json").is_file()
    assert (
        data["started_utc"]
        <= hsi["request_started_utc"]
        <= hsi["request_completed_utc"]
        <= data["completed_utc"]
    )


@pytest.mark.parametrize(
    "day,symbols",
    [
        ("bad", ["HSI"]),
        ("20260230", ["HSI"]),
        ("20261021", []),
        ("20261021", ["BAD"]),
        ("20261021", ["HSI", "HSI"]),
    ],
)
def test_invalid_input_creates_nothing(tmp_path, day, symbols):
    with pytest.raises(ValueError):
        capture.capture(tmp_path, day, symbols, client=Client())
    assert not list(tmp_path.iterdir())


def test_unique_runs_and_complete_status(tmp_path):
    a = capture.capture(tmp_path, "20261021", ["HSI"], client=Client())
    raw = a.read_bytes()
    b = capture.capture(tmp_path, "20261021", ["HSI"], client=Client())
    assert a != b and a.read_bytes() == raw
    assert json.loads(raw)["status"] == "complete"


def test_missing_client_seals_failed_receipt(tmp_path, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("credential=secret")

    monkeypatch.setattr(capture, "get_tushare_client", fail)
    path = capture.capture(tmp_path, "20261021", ["HSI"])
    assert json.loads(path.read_text())["status"] == "failed"
    assert "secret" not in path.read_text()


@pytest.mark.parametrize(
    "error,category",
    [
        (Exception("抱歉，您没有访问该接口的权限 token=secret"), "permission"),
        (TimeoutError("secret transport"), "transport"),
        (RuntimeError("secret unknown"), "provider"),
    ],
)
def test_failure_category_is_sanitized(tmp_path, error, category):
    class FailedClient:
        def index_global(self, **kwargs):
            raise error

    path = capture.capture(tmp_path, "20261021", ["HSI"], client=FailedClient())
    assert json.loads(path.read_text())["sources"]["HSI"]["failure_category"] == category
    assert "secret" not in path.read_text()
