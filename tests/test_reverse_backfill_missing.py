"""Missing stock-days must not block the first download pass."""

import json
from pathlib import Path

import pytest

from quant_market_data_platform import tushare_minute_reverse_backfill as scheduler


def _partial(root: Path, date: str, *, issue="missing from response", status="partial"):
    import pandas as pd

    from quant_market_data_platform.providers import tushare_a_share_mins as mins

    path = root / "staging/tushare_minute_backfill_reverse/runs/run/data" / f"trade_date={date}"
    path.mkdir(parents=True, exist_ok=True)
    times = [
        *pd.date_range(f"{date} 09:30", periods=121, freq="min"),
        *pd.date_range(f"{date} 13:01", periods=120, freq="min"),
    ]
    frame = pd.DataFrame(
        {
            "ts_code": ["000043.SZ"] * 241,
            "trade_time": times,
            "open": 10.0,
            "close": 10.0,
            "high": 10.0,
            "low": 10.0,
            "vol": 1.0,
            "amount": 10.0,
        }
    )
    mins._atomic_write_partition(frame, path, trade_date=date)
    partition = mins._partition_state(path, trade_date=date)
    sidecar = path / "_minute_mirror.json"
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": "tushare.a_share.minute_partition.v3",
                "trade_date": date,
                "status": status,
                "freq": "1min",
                "expected_bars_per_symbol": 241,
                "expected_symbols": ["000043.SZ", "001914.SZ"],
                "completed_symbols": ["000043.SZ"],
                "missing_request_symbols": ["001914.SZ"],
                "partition": partition,
                "error": {"type": "IncompleteBatch", "issues": {"001914.SZ": issue}},
            }
        )
    )
    return sidecar


def _setup(root: Path, monkeypatch):
    metadata = root / "metadata/minute_backfill/reverse_scheduler"
    plan = metadata / "plans/reverse_20191213_20191213.plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text(json.dumps({"identity": {"segments": [{"dates": ["20191213"]}]}}))
    receipt = metadata / "receipts/reverse_20191213_20191213.receipt.json"
    receipt.parent.mkdir()
    receipt.write_text(
        json.dumps(
            {
                "status": "partial",
                "plan_path": str(plan),
                "output": {
                    "run_root": str(root / "staging/tushare_minute_backfill_reverse/runs/run")
                },
                "segments": [
                    {
                        "status": "partial",
                        "error": {"type": "MinuteMirrorIncompleteDatesError"},
                        "result": {"partial_dates": ["20191213"]},
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(scheduler, "_current_min_date", lambda _: "20220715")
    monkeypatch.setattr(scheduler, "_load_open_dates", lambda *a, **k: ["20191212", "20191213"])
    monkeypatch.setattr(scheduler, "build_minute_backfill_plan", lambda _: None)
    return metadata, plan


def test_missing_day_is_queued_and_next_date_downloads(tmp_path, monkeypatch):
    metadata, old_plan = _setup(tmp_path, monkeypatch)
    sidecar = _partial(tmp_path, "20191213")
    original = sidecar.read_bytes()
    calls = []
    monkeypatch.setattr(
        scheduler, "run_minute_backfill", lambda o: calls.append(o) or {"status": "complete"}
    )
    scheduler.run_reverse_backfill(scheduler.ReverseBackfillOptions(artifacts_root=tmp_path))
    assert calls[0].plan_path != old_plan
    assert calls[0].plan_path.name == "reverse_20191212_20191212.plan.json"
    queue = json.loads((metadata / "deferred_missing.json").read_text())
    assert queue["dates"]["20191213"]["missing_symbols"] == ["001914.SZ"]
    assert queue["writes_production"] is False
    assert sidecar.read_bytes() == original


def test_first_pass_exhaustion_with_gaps_is_not_complete(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _partial(tmp_path, "20191213")
    monkeypatch.setattr(scheduler, "_load_open_dates", lambda *a, **k: ["20191213"])
    monkeypatch.setattr(
        scheduler, "run_minute_backfill", lambda _: pytest.fail("must not retry gap")
    )
    result = scheduler.run_reverse_backfill(
        scheduler.ReverseBackfillOptions(artifacts_root=tmp_path)
    )
    assert result["status"] == "download_pass_complete_with_gaps"
    assert result["deferred_dates"] == 1


def test_explicit_gap_retry_resumes_existing_partial(tmp_path, monkeypatch):
    _, plan = _setup(tmp_path, monkeypatch)
    _partial(tmp_path, "20191213")
    calls = []
    monkeypatch.setattr(
        scheduler, "run_minute_backfill", lambda o: calls.append(o) or {"status": "partial"}
    )
    scheduler.run_reverse_backfill(
        scheduler.ReverseBackfillOptions(artifacts_root=tmp_path, retry_deferred=True)
    )
    assert calls[0].plan_path == plan


@pytest.mark.parametrize("issue", ["invalid OHLC", "request failed"])
def test_other_failures_keep_resumable_plan(tmp_path, monkeypatch, issue):
    _, plan = _setup(tmp_path, monkeypatch)
    _partial(tmp_path, "20191213", issue=issue)
    calls = []
    monkeypatch.setattr(
        scheduler, "run_minute_backfill", lambda o: calls.append(o) or {"status": "partial"}
    )
    result = scheduler.run_reverse_backfill(
        scheduler.ReverseBackfillOptions(artifacts_root=tmp_path)
    )
    assert calls[0].plan_path == plan
    assert result["status"] == "partial"


def test_new_missing_batch_returns_deferred_and_preserves_receipt(tmp_path, monkeypatch):
    metadata, _ = _setup(tmp_path, monkeypatch)

    def download(_):
        _partial(tmp_path, "20191213")
        return {"status": "partial"}

    monkeypatch.setattr(scheduler, "run_minute_backfill", download)
    result = scheduler.run_reverse_backfill(
        scheduler.ReverseBackfillOptions(artifacts_root=tmp_path)
    )
    assert result["status"] == "deferred_missing"
    assert (
        json.loads((metadata / "receipts/reverse_20191213_20191213.receipt.json").read_text())[
            "status"
        ]
        == "partial"
    )


def test_dry_run_does_not_write_missing_queue(tmp_path, monkeypatch):
    metadata, _ = _setup(tmp_path, monkeypatch)
    _partial(tmp_path, "20191213")
    monkeypatch.setattr(scheduler, "run_minute_backfill", lambda _: {"status": "dry_run"})
    scheduler.run_reverse_backfill(
        scheduler.ReverseBackfillOptions(artifacts_root=tmp_path, dry_run=True)
    )
    assert not (metadata / "deferred_missing.json").exists()


@pytest.mark.parametrize("damage", ["missing_file", "corrupt_file", "schema", "symbols"])
def test_invalid_partition_is_not_deferred(tmp_path, monkeypatch, damage):
    _, plan = _setup(tmp_path, monkeypatch)
    sidecar = _partial(tmp_path, "20191213")
    if damage == "missing_file":
        (sidecar.parent / "part-00000.parquet").unlink()
    elif damage == "corrupt_file":
        (sidecar.parent / "part-00000.parquet").write_bytes(b"invalid parquet")
    else:
        payload = json.loads(sidecar.read_text())
        if damage == "schema":
            payload["schema_version"] = "unknown"
        else:
            payload["missing_request_symbols"] = ["not-a-stock"]
        sidecar.write_text(json.dumps(payload))
    calls = []
    monkeypatch.setattr(
        scheduler, "run_minute_backfill", lambda o: calls.append(o) or {"status": "partial"}
    )
    result = scheduler.run_reverse_backfill(
        scheduler.ReverseBackfillOptions(artifacts_root=tmp_path)
    )
    assert calls[0].plan_path == plan
    assert result["status"] == "partial"


def test_newer_global_failure_overrides_old_missing_evidence(tmp_path, monkeypatch):
    import os

    metadata, plan = _setup(tmp_path, monkeypatch)
    _partial(tmp_path, "20191213")
    original = metadata / "receipts/reverse_20191213_20191213.receipt.json"
    payload = json.loads(original.read_text())
    payload["plan_path"] = str(plan)
    payload["output"]["run_root"] = str(
        tmp_path / "staging/tushare_minute_backfill_reverse/runs/newer"
    )
    payload["segments"][0]["error"] = {"type": "ConnectionError"}
    failure = metadata / "receipts/newer.receipt.json"
    failure.write_text(json.dumps(payload))
    modified = original.stat().st_mtime_ns + 1_000_000
    os.utime(failure, ns=(modified, modified))
    monkeypatch.setattr(scheduler, "run_minute_backfill", lambda _: {"status": "partial"})
    result = scheduler.run_reverse_backfill(
        scheduler.ReverseBackfillOptions(artifacts_root=tmp_path)
    )
    assert result["status"] == "partial"
    assert result["deferred_dates"] == 0


def test_mixed_batch_keeps_unattempted_dates_after_deferring_gap(tmp_path, monkeypatch):
    metadata, plan = _setup(tmp_path, monkeypatch)
    plan.write_text(
        json.dumps({"identity": {"segments": [{"dates": ["20191213", "20191212", "20191211"]}]}})
    )
    _partial(tmp_path, "20191213")
    monkeypatch.setattr(scheduler, "_complete_dates", lambda *a, **k: {"20191212"})
    monkeypatch.setattr(
        scheduler,
        "_load_open_dates",
        lambda *a, **k: ["20191210", "20191211", "20191212", "20191213"],
    )
    calls = []
    monkeypatch.setattr(
        scheduler, "run_minute_backfill", lambda o: calls.append(o) or {"status": "complete"}
    )
    scheduler.run_reverse_backfill(
        scheduler.ReverseBackfillOptions(artifacts_root=tmp_path, max_dates=3)
    )
    selected = json.loads((metadata / "dates/reverse_20191210_20191211.json").read_text())["dates"]
    assert selected == ["20191211", "20191210"]
    assert calls[0].plan_path.name == "reverse_20191210_20191211.plan.json"
