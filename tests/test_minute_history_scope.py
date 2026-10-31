"""Configured history bounds must govern acquisition and monitoring together."""

import json

import pytest

from quant_market_data_platform import _minute_history_scope as scope


def test_default_bound_and_explicit_scope_are_persistent(tmp_path):
    assert scope.read_history_start(tmp_path) == "20160101"
    scope.write_history_start(tmp_path, "20090101")
    assert scope.read_history_start(tmp_path) == "20090101"
    assert scope.read_history_start(tmp_path, "20150101") == "20150101"
    assert scope.read_history_start(tmp_path) == "20090101"


@pytest.mark.parametrize("value", ["2009-01-01", "20090230", "", 20090101])
def test_invalid_scope_is_rejected_without_publication(tmp_path, value):
    with pytest.raises(ValueError):
        scope.write_history_start(tmp_path, value)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("payload", [{}, {"schema_version": "wrong", "start_date": "20090101"}])
def test_malformed_persisted_scope_is_not_treated_as_legacy(tmp_path, payload):
    (tmp_path / "history_scope.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        scope.read_history_start(tmp_path)


def test_resume_ignores_plans_below_configured_bound(tmp_path):
    from quant_market_data_platform.tushare_minute_reverse_backfill import _resume_paths

    p = tmp_path / "plans/reverse_20080102_20080102.plan.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"identity": {"segments": [{"dates": ["20080102"]}]}}))
    assert _resume_paths(tmp_path, start_date="20090101") is None


def test_monitor_counts_earlier_open_dates_only_when_scope_is_configured(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quant_market_data_platform._minute_history_inventory import _open_dates

    p = tmp_path / "assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet"
    p.parent.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {"cal_date": ["20080102", "20090105", "20151231", "20160104"], "is_open": [1, 1, 1, 1]}
        ),
        p,
    )
    warnings = []
    assert _open_dates(tmp_path, "20220715", warnings) == {"20160104"}
    scope.write_history_start(tmp_path / "metadata/minute_backfill/reverse_scheduler", "20090101")
    assert _open_dates(tmp_path, "20220715", warnings) == {"20090105", "20151231", "20160104"}
    assert warnings == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_scheduler_uses_earlier_bound_and_dry_run_preserves_stored_scope(
    tmp_path, monkeypatch, dry_run
):
    from quant_market_data_platform import tushare_minute_reverse_backfill as scheduler

    seen = {}
    monkeypatch.setattr(scheduler, "_current_min_date", lambda _: "20220715")

    def calendar(path, *, start_date, end_date):
        seen["start_date"] = start_date
        return []

    monkeypatch.setattr(scheduler, "_load_open_dates", calendar)
    options = scheduler.ReverseBackfillOptions(
        artifacts_root=tmp_path, history_start_date="20090101", dry_run=dry_run
    )
    assert scheduler.run_reverse_backfill(options)["status"] == "noop"
    assert seen["start_date"] == "20090101"
    metadata = tmp_path / "metadata/minute_backfill/reverse_scheduler"
    assert scope.read_history_start(metadata) == ("20160101" if dry_run else "20090101")


def test_cli_forwards_explicit_history_bound(monkeypatch, capsys):
    from scripts.operations import tushare_minute_reverse_backfill as command

    def run(options):
        assert options.history_start_date == "20090101"
        return {"status": "dry_run"}

    monkeypatch.setattr(command, "run_reverse_backfill", run)
    assert (
        command.main(
            ["--artifacts-root", "/synthetic", "--history-start-date", "20090101", "--dry-run"]
        )
        == 0
    )
