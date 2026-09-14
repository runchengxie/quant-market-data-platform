from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from market_data_platform.providers._a_share_mins_constants import _MinuteProgress
from market_data_platform.providers._a_share_mins_partition import _persist_progress
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
from market_data_platform.tushare_minute_reverse_backfill import _complete_dates, _resume_paths


def _partial(root: Path, missing: str = "920001.BJ", issue: str = "missing from response") -> Path:
    part = root / "runs/one/data/trade_date=20220705"
    times = [
        *pd.date_range("2022-07-05 09:30", periods=121, freq="min"),
        *pd.date_range("2022-07-05 13:01", periods=120, freq="min"),
    ]
    frame = pd.DataFrame({"ts_code": "000001.SZ", "trade_time": times})
    for name in ("open", "close", "high", "low", "vol", "amount"):
        frame[name] = 10.0
    progress = _MinuteProgress(
        part,
        "20220705",
        "1min",
        "test",
        "test",
        "test",
        frozenset({"000001.SZ", missing}),
        TushareRequestPolicy(),
    )
    _persist_progress(
        progress,
        completed_symbols={"000001.SZ"},
        frames=[frame],
        status="partial",
        error={"type": "IncompleteBatch", "issues": {missing: issue}, "symbols": [missing]},
    )
    return part


def test_report_policy_settles_bj_only_gap_without_marking_data_complete(tmp_path: Path) -> None:
    part = _partial(tmp_path)
    original = (part / "_minute_mirror.json").read_bytes()
    assert _complete_dates(tmp_path, bj_missing_policy="report") == {"20220705"}
    assert (part / "_minute_mirror.json").read_bytes() == original
    assert _complete_dates(tmp_path, bj_missing_policy="error") == set()


@pytest.mark.parametrize(
    "missing,issue",
    [
        ("000002.SZ", "missing from response"),
        ("600001.SH", "missing from response"),
        ("920001.BJ", "invalid minute grid"),
    ],
)
def test_other_missing_or_invalid_data_remain_unsettled(tmp_path: Path, missing: str, issue: str):
    _partial(tmp_path, missing, issue)
    assert _complete_dates(tmp_path, bj_missing_policy="report") == set()


def test_changed_partition_cannot_be_accepted(tmp_path: Path) -> None:
    part = _partial(tmp_path)
    (part / "part-00000.parquet").write_bytes(b"corrupt")
    assert _complete_dates(tmp_path, bj_missing_policy="report") == set()


def test_resume_skips_settled_partial_day(tmp_path: Path) -> None:
    plan = tmp_path / "plans/reverse_20220705_20220705.plan.json"
    plan.parent.mkdir()
    plan.write_text(json.dumps({"identity": {"segments": [{"dates": ["20220705"]}]}}))
    assert _resume_paths(tmp_path, settled_dates={"20220705"}) is None
    assert _resume_paths(tmp_path) is not None


@pytest.mark.parametrize("policy,status", [("report", "accepted_bj_missing"), ("error", "partial")])
def test_scheduler_reports_bj_gap_and_preserves_raw_partial_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str, status: str
) -> None:
    from market_data_platform import tushare_minute_reverse_backfill as scheduler

    metadata = tmp_path / "metadata/minute_backfill/reverse_scheduler"
    plan = metadata / "plans/reverse_20220705_20220705.plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text(json.dumps({"identity": {"segments": [{"dates": ["20220705"]}]}}))

    def download(options):
        _partial(tmp_path / "staging/tushare_minute_backfill_reverse")
        options.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        options.receipt_path.write_text(json.dumps({"status": "partial"}))
        return {"status": "partial"}

    monkeypatch.setattr(scheduler, "run_minute_backfill", download)
    result = scheduler.run_reverse_backfill(
        scheduler.ReverseBackfillOptions(artifacts_root=tmp_path, bj_missing_policy=policy)
    )
    assert result["status"] == status
    assert json.loads(Path(result["receipt"]).read_text())["status"] == "partial"
    if policy == "report":
        report = json.loads(Path(result["bj_missing_report"]).read_text())
        assert report["partitions"][0]["missing_bj_symbols"] == ["920001.BJ"]
        assert report["full_market_complete"] is False
        assert scheduler._resume_paths(metadata, settled_dates={"20220705"}) is None


def test_cli_accepts_reported_bj_gap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from scripts.operations import tushare_minute_reverse_backfill as cli

    policies = []

    def run(options):
        policies.append(options.bj_missing_policy)
        return {"status": "accepted_bj_missing"}

    monkeypatch.setattr(cli, "run_reverse_backfill", run)
    assert cli.main(["--artifacts-root", str(tmp_path)]) == 0
    assert policies == ["report"]


def test_mixed_plan_is_replanned_instead_of_retrying_accepted_day(tmp_path: Path) -> None:
    plan = tmp_path / "plans/reverse_20220704_20220705.plan.json"
    plan.parent.mkdir()
    plan.write_text(json.dumps({"identity": {"segments": [{"dates": ["20220705", "20220704"]}]}}))
    original = plan.read_bytes()
    assert _resume_paths(tmp_path, max_dates=2, settled_dates={"20220705"}) is None
    assert plan.read_bytes() == original


def test_replanning_same_date_bounds_preserves_old_plan(tmp_path: Path, monkeypatch) -> None:
    from market_data_platform import tushare_minute_reverse_backfill as scheduler

    _partial(tmp_path / "staging/tushare_minute_backfill_reverse")
    metadata = tmp_path / "metadata/minute_backfill/reverse_scheduler"
    plan = metadata / "plans/reverse_20220704_20220706.plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        json.dumps({"identity": {"segments": [{"dates": ["20220706", "20220705", "20220704"]}]}})
    )
    original = plan.read_bytes()
    monkeypatch.setattr(scheduler, "_current_min_date", lambda alias: "20220707")
    monkeypatch.setattr(
        scheduler, "_load_open_dates", lambda *a, **k: ["20220704", "20220705", "20220706"]
    )
    planned_dates = []

    def build(options):
        planned_dates.extend(json.loads(options.dates_path.read_text())["dates"])
        options.plan_path.write_text(
            json.dumps({"identity": {"segments": [{"dates": planned_dates}]}})
        )

    monkeypatch.setattr(scheduler, "build_minute_backfill_plan", build)
    monkeypatch.setattr(scheduler, "run_minute_backfill", lambda options: {"status": "dry_run"})
    result = scheduler.run_reverse_backfill(
        scheduler.ReverseBackfillOptions(
            artifacts_root=tmp_path,
            max_dates=3,
            dry_run=True,
            api_url="https://example.test",
        )
    )
    assert planned_dates == ["20220706", "20220704"]
    assert "_remaining_" in result["plan"]
    assert plan.read_bytes() == original
    assert not (metadata / "bj_missing_report.json").exists()
