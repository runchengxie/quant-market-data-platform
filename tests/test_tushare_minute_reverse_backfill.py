import pytest

from quant_market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
from quant_market_data_platform.tushare_minute_backfill_part01 import _policy_payload
from quant_market_data_platform.tushare_minute_reverse_backfill import (
    _resume_paths,
    _scheduler_plan_stem,
    _select_reverse_dates,
)


def test_reverse_selector_walks_newest_to_oldest_and_skips_complete_dates() -> None:
    assert _select_reverse_dates(
        open_dates=["20220707", "20220708", "20220711", "20220712", "20220713", "20220714"],
        current_min_date="20220715",
        complete_dates={"20220714", "20220713", "20220711"},
        max_dates=2,
    ) == ["20220712", "20220708"]


def test_scheduler_plan_stem_is_stable_for_resume() -> None:
    assert _scheduler_plan_stem(["20220712", "20220708"]) == "reverse_20220708_20220712"


def test_selector_returns_empty_when_history_is_exhausted() -> None:
    assert (
        _select_reverse_dates(
            open_dates=["20220715"],
            current_min_date="20220715",
            complete_dates=set(),
            max_dates=1,
        )
        == []
    )


def test_resume_ignores_plan_larger_than_scheduler_window(tmp_path) -> None:
    plan_path = tmp_path / "plans/reverse_20220701_20220703.plan.json"
    receipt_path = tmp_path / "receipts/reverse_20220701_20220703.receipt.json"
    plan_path.parent.mkdir(parents=True)
    receipt_path.parent.mkdir(parents=True)
    plan_path.write_text(
        '{"identity": {"segments": [{"dates": ["20220703", "20220702", "20220701"]}]} }',
        encoding="utf-8",
    )
    receipt_path.write_text('{"status": "interrupted"}', encoding="utf-8")

    assert _resume_paths(tmp_path, max_dates=1) is None


def test_resume_keeps_incomplete_plan_within_scheduler_window(tmp_path) -> None:
    plan_path = tmp_path / "plans/reverse_20220703_20220703.plan.json"
    receipt_path = tmp_path / "receipts/reverse_20220703_20220703.receipt.json"
    plan_path.parent.mkdir(parents=True)
    receipt_path.parent.mkdir(parents=True)
    plan_path.write_text(
        '{"identity": {"segments": [{"dates": ["20220703"]}]} }',
        encoding="utf-8",
    )
    receipt_path.write_text('{"status": "interrupted"}', encoding="utf-8")

    assert _resume_paths(tmp_path, max_dates=1) == (plan_path, receipt_path)


def test_legacy_request_policy_payload_keeps_original_shape() -> None:
    assert "request_timeout_seconds" not in _policy_payload(TushareRequestPolicy())


@pytest.mark.parametrize("enable_burst", [False, True])
def test_reverse_burst_is_opt_in_at_shared_quota_gate(tmp_path, monkeypatch, enable_burst):
    import json

    from quant_market_data_platform import tushare_minute_reverse_backfill as scheduler
    from quant_market_data_platform.tushare_minute_quota import (
        MinuteQuotaConfig,
        MinuteQuotaExceeded,
        MinuteQuotaLedger,
        MinuteQuotaRequestPolicy,
    )

    metadata = tmp_path / "metadata/minute_backfill/reverse_scheduler"
    (metadata / "plans").mkdir(parents=True)
    (metadata / "plans/reverse_20220207_20220207.plan.json").write_text(
        json.dumps({"identity": {"segments": [{"dates": ["20220207"]}]}})
    )
    database = tmp_path / "metadata/tushare/minute_quota/minute_quota.sqlite3"
    config = MinuteQuotaConfig(
        mode="enforce",
        database_path=database,
        consumer="reverse_backfill",
        request_policy=MinuteQuotaRequestPolicy(
            gate="requests", limit_requests=10, burst_limit_requests=20, safety_requests=1
        ),
    )
    ledger = MinuteQuotaLedger(config, token="fixture")
    for _ in range(10):
        ledger.reserve(1).commit(1)

    def provider_step(options):
        burst_ledger = MinuteQuotaLedger(
            MinuteQuotaConfig(
                mode="enforce",
                database_path=database,
                consumer="reverse_backfill",
                request_policy=MinuteQuotaRequestPolicy(
                    gate="requests",
                    limit_requests=10,
                    burst_limit_requests=20,
                    safety_requests=1,
                    allow_burst=options.minute_quota_allow_burst,
                ),
            ),
            token="fixture",
        )
        burst_ledger.reserve(1).commit(1)
        return {"status": "complete", "summary": {"dates": 1, "rows": 1}}

    monkeypatch.setattr(scheduler, "run_minute_backfill", provider_step)
    options = scheduler.ReverseBackfillOptions(
        artifacts_root=tmp_path,
        bj_missing_policy="error",
        **({"minute_quota_allow_burst": True} if enable_burst else {}),
    )
    if enable_burst:
        assert scheduler.run_reverse_backfill(options)["status"] == "complete"
        assert ledger.status()["committed_requests"] == 11
    else:
        with pytest.raises(MinuteQuotaExceeded):
            scheduler.run_reverse_backfill(options)
        assert ledger.status()["committed_requests"] == 10


def test_reverse_cli_passes_response_size_and_pacing_to_scheduler(monkeypatch, capsys):
    import json

    from scripts.operations import tushare_minute_reverse_backfill as command

    def planned_step(options):
        return {
            "status": "dry_run",
            "batch_size": options.batch_size,
            "cooldown_seconds": options.cooldown_seconds,
            "allow_burst": options.minute_quota_allow_burst,
        }

    monkeypatch.setattr(command, "run_reverse_backfill", planned_step)
    assert (
        command.main(
            [
                "--artifacts-root",
                "/fixture",
                "--batch-size",
                "33",
                "--cooldown-seconds",
                "1.2",
                "--minute-quota-allow-burst",
                "--dry-run",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "status": "dry_run",
        "batch_size": 33,
        "cooldown_seconds": 1.2,
        "allow_burst": True,
    }
