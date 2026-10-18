from datetime import UTC, datetime

import pandas as pd
import pytest

from quant_market_data_platform import overseas_forward as capture


class Client:
    def index_global(self, **kwargs):
        return pd.DataFrame({"trade_date": ["20261016"], "close": [100]})

    def fx_daily(self, **kwargs):
        raise TimeoutError("sensitive request details")

    def trade_cal(self, **kwargs):
        return pd.DataFrame({"cal_date": ["20261019"], "is_open": [1]})


def test_receipts_never_replace_and_failures_do_not_leak(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "now", lambda: datetime(2026, 10, 19, 0, 44, tzinfo=UTC))
    first = capture.capture(tmp_path, "scheduled", Client())
    second = capture.capture(tmp_path, "capture_probe", Client())
    import json

    receipt = json.loads((first / "receipt.json").read_text())
    assert receipt["timely_capture"]
    assert receipt["sources"]["usdcnh"]["status"] == "failed"
    assert "sensitive" not in (first / "receipt.json").read_text()
    assert not json.loads((second / "receipt.json").read_text())["timely_capture"]
    with pytest.raises(FileExistsError):
        capture.seal(first / "receipt.json", {})


def test_late_capture_is_ineligible(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "now", lambda: datetime(2026, 10, 19, 0, 46, tzinfo=UTC))
    import json

    run = capture.capture(tmp_path, "scheduled", Client())
    receipt = json.loads((run / "receipt.json").read_text())
    assert receipt["late"] and not receipt["timely_capture"]
