from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/operations/prepare_a_share_evening_data.py"
SPEC = importlib.util.spec_from_file_location("prepare_evening", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_evening_producer_has_required_and_premium_dataset_tiers() -> None:
    names = {name for name, _command, _path, required in MODULE.DATASETS if required}
    assert names == {"daily", "adj_factor", "daily_basic", "limit_status"}
    assert "ths_hot" in {name for name, *_rest in MODULE.DATASETS}


def test_latest_completed_daily_date_avoids_using_future_calendar_date(tmp_path: Path) -> None:
    daily = (
        tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_latest/data/trade_date=20260831"
    )
    daily.mkdir(parents=True)
    assert MODULE._latest_completed_daily_date(tmp_path) == "20260831"


def test_latest_completed_daily_date_uses_current_contract_target(tmp_path: Path) -> None:
    target = tmp_path / "immutable-daily"
    daily = target / "data" / "trade_date=20260907"
    daily.mkdir(parents=True)
    contract = tmp_path / "metadata/current_assets/a_share_current.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(
        json.dumps(
            {"assets": {"daily": {"availability": "available", "resolved_path": str(target)}}}
        ),
        encoding="utf-8",
    )
    assert MODULE._latest_completed_daily_date(tmp_path) == "20260907"


def test_latest_completed_daily_date_does_not_lag_stale_current_contract(tmp_path: Path) -> None:
    canonical = (
        tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_latest/data/trade_date=20260910"
    )
    canonical.mkdir(parents=True)
    target = tmp_path / "immutable-daily"
    (target / "data" / "trade_date=20260909").mkdir(parents=True)
    contract = tmp_path / "metadata/current_assets/a_share_current.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(
        json.dumps(
            {"assets": {"daily": {"availability": "available", "resolved_path": str(target)}}}
        ),
        encoding="utf-8",
    )
    assert MODULE._latest_completed_daily_date(tmp_path) == "20260910"


def test_evening_producer_writes_receipt_and_reports_missing_required_data(
    tmp_path, monkeypatch
) -> None:
    def fake_run(_command, out_dir, date, _token_env):
        out_dir.mkdir(parents=True, exist_ok=True)
        if out_dir.name == "a_share_all_daily_latest":
            target = out_dir / "data" / f"trade_date={date}" / "part.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"parquet")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(MODULE, "_run", fake_run)
    receipt, status = MODULE.prepare("20260831", tmp_path, "TUSHARE_TOKEN", False)
    assert status == 1
    assert receipt.exists()
    assert '"schema_version": "market_data_platform.a_share_evening_data.v1"' in receipt.read_text()


def test_index_refresh_allows_existing_mirror_output(tmp_path, monkeypatch) -> None:
    captured: list[list[str]] = []

    def fake_run(command, **_kwargs):
        captured.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)

    result = MODULE._run_index(tmp_path / "index_daily", "20260909", "TUSHARE_TOKEN")

    assert result.returncode == 0
    assert "--skip-existing" in captured[0]


def _margin_fixture(tmp_path, monkeypatch, *, open_days, available, dataset="margin"):
    import pandas as pd

    calendar = tmp_path / "assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet"
    calendar.parent.mkdir(parents=True)
    pd.DataFrame(
        {
            "exchange": ["SSE"] * len(open_days),
            "cal_date": open_days,
            "is_open": [1] * len(open_days),
        }
    ).to_parquet(calendar, index=False)
    monkeypatch.setattr(
        MODULE,
        "DATASETS",
        (
            (
                dataset,
                "mirror-a-share-" + dataset.replace("_", "-"),
                f"{dataset}/a_share_all_{dataset}_latest",
                False,
            ),
        ),
    )
    calls = []

    def fake_run(_command, out_dir, date, _token_env):
        calls.append(date)
        if date in available:
            target = MODULE._partition(out_dir, date)
            target.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {
                    "trade_date": [date] * 3,
                    **(
                        {"exchange_id": ["SSE", "SZSE", "BSE"]}
                        if dataset == "margin"
                        else {"ts_code": ["600000.SH", "000001.SZ", "920001.BJ"]}
                    ),
                }
            ).to_parquet(target, index=False)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_index(out_dir, date, token_env):
        path = out_dir / "data/part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"index fixture")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(MODULE, "_run", fake_run)
    monkeypatch.setattr(MODULE, "_run_index", fake_index)
    return calls


def _margin_row(path):
    return next(
        row for row in json.loads(path.read_text())["datasets"] if row["dataset"] == "margin"
    )


def test_delayed_margin_recovers_previous_session_without_claiming_target_ready(
    tmp_path, monkeypatch
):
    calls = _margin_fixture(
        tmp_path,
        monkeypatch,
        open_days=["20261016", "20261019", "20261020", "20261021"],
        available={"20261020"},
    )
    receipt, status = MODULE.prepare("20261021", tmp_path, "TUSHARE_TOKEN_2", True)
    row = _margin_row(receipt)
    assert status == 0
    assert calls == ["20261019", "20261020", "20261021"]
    assert row["status"] == "expected_delay"
    assert row["requested_target_date"] == "20261021"
    assert row["as_of"] == "20261020"
    assert row["target_ready"] is False
    assert row["recovered_dates"] == ["20261020"]


def test_next_evening_revisits_yesterdays_empty_response(tmp_path, monkeypatch):
    available = {"20261019"}
    calls = _margin_fixture(
        tmp_path,
        monkeypatch,
        open_days=["20261015", "20261016", "20261019", "20261020", "20261021"],
        available=available,
    )
    first, _ = MODULE.prepare("20261020", tmp_path, "TUSHARE_TOKEN_2", True)
    assert _margin_row(first)["as_of"] == "20261019"
    calls.clear()
    available.add("20261020")
    second, _ = MODULE.prepare("20261021", tmp_path, "TUSHARE_TOKEN_2", True)
    assert calls == ["20261020", "20261021"]
    assert _margin_row(second)["recovered_dates"] == ["20261020"]


def test_holiday_recovery_uses_open_sessions_but_delay_threshold_is_calendar_days(
    tmp_path, monkeypatch
):
    calls = _margin_fixture(
        tmp_path,
        monkeypatch,
        open_days=["20260929", "20260930", "20261008"],
        available={"20260930"},
    )
    receipt, _ = MODULE.prepare("20261008", tmp_path, "TUSHARE_TOKEN_2", True)
    row = _margin_row(receipt)
    assert calls == ["20260929", "20260930", "20261008"]
    assert row["as_of"] == "20260930"
    assert row["status"] == "stale"
    assert row["target_ready"] is False


def test_missing_calendar_does_not_guess_previous_weekdays(tmp_path, monkeypatch):
    calls = _margin_fixture(
        tmp_path, monkeypatch, open_days=["20261019", "20261020", "20261021"], available=set()
    )
    (tmp_path / "assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet").unlink()
    receipt, _ = MODULE.prepare("20261021", tmp_path, "TUSHARE_TOKEN_2", True)
    row = _margin_row(receipt)
    assert calls == ["20261021"]
    assert row["status"] == "failed"
    assert row["recovery_window_error"]


def test_delayed_recovery_never_overwrites_existing_positive_partition(tmp_path, monkeypatch):
    import pandas as pd

    calls = _margin_fixture(
        tmp_path, monkeypatch, open_days=["20261019", "20261020", "20261021"], available=set()
    )
    out = tmp_path / "assets/tushare/a_share/margin/a_share_all_margin_latest"
    path = MODULE._partition(out, "20261020")
    path.parent.mkdir(parents=True)
    pd.DataFrame({"trade_date": ["20261020"], "exchange_id": ["SSE"]}).to_parquet(path, index=False)
    before = path.read_bytes()
    receipt, _ = MODULE.prepare("20261021", tmp_path, "TUSHARE_TOKEN_2", True)
    assert calls == ["20261019", "20261021"]
    assert path.read_bytes() == before
    assert _margin_row(receipt)["recovered_dates"] == []


def test_margin_detail_uses_same_delayed_policy_and_records_market_observations(
    tmp_path, monkeypatch
):
    _margin_fixture(
        tmp_path,
        monkeypatch,
        open_days=["20261019", "20261020", "20261021"],
        available={"20261020"},
        dataset="margin_detail",
    )
    receipt, _ = MODULE.prepare("20261021", tmp_path, "TUSHARE_TOKEN_2", True)
    row = json.loads(receipt.read_text())["datasets"][0]
    assert row["dataset"] == "margin_detail"
    assert row["status"] == "expected_delay"
    assert row["latest_available_rows"] == 3
    assert row["market_coverage"] == {"SH": 1, "SZ": 1, "BJ": 1}
    assert row["market_completeness"] == "not_established"


def test_corrupt_existing_margin_partition_is_preserved_and_not_trusted(tmp_path, monkeypatch):
    calls = _margin_fixture(
        tmp_path, monkeypatch, open_days=["20261019", "20261020", "20261021"], available=set()
    )
    out = tmp_path / "assets/tushare/a_share/margin/a_share_all_margin_latest"
    path = MODULE._partition(out, "20261020")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"corrupt existing input")
    receipt, _ = MODULE.prepare("20261021", tmp_path, "TUSHARE_TOKEN_2", True)
    row = _margin_row(receipt)
    assert calls == ["20261019", "20261021"]
    assert path.read_bytes() == b"corrupt existing input"
    assert row["status"] == "failed"
    assert row["as_of"] is None
    assert row["date_results"][1]["status"] == "invalid"


def test_old_nonempty_margin_is_stale_instead_of_expected_delay(tmp_path, monkeypatch):
    import pandas as pd

    _margin_fixture(
        tmp_path, monkeypatch, open_days=["20261019", "20261020", "20261021"], available=set()
    )
    out = tmp_path / "assets/tushare/a_share/margin/a_share_all_margin_latest"
    old = MODULE._partition(out, "20260930")
    old.parent.mkdir(parents=True)
    pd.DataFrame({"trade_date": ["20260930"], "exchange_id": ["SSE"]}).to_parquet(old, index=False)
    receipt, _ = MODULE.prepare("20261021", tmp_path, "TUSHARE_TOKEN_2", True)
    row = _margin_row(receipt)
    assert row["status"] == "stale"
    assert row["as_of"] == "20260930"
    assert row["lag_calendar_days"] == 21
    assert row["target_ready"] is False


def test_delayed_request_failure_is_preserved_and_stops_same_route(tmp_path, monkeypatch):
    calls = _margin_fixture(
        tmp_path, monkeypatch, open_days=["20261019", "20261020", "20261021"], available=set()
    )
    original = MODULE._run

    def failed(command, out_dir, date, token_env):
        original(command, out_dir, date, token_env)
        return type("Result", (), {"returncode": 2, "stdout": "", "stderr": "permission denied"})()

    monkeypatch.setattr(MODULE, "_run", failed)
    receipt, _ = MODULE.prepare("20261021", tmp_path, "TUSHARE_TOKEN_2", True)
    row = _margin_row(receipt)
    assert calls == ["20261019"]
    assert row["status"] == "failed"
    assert row["command_status"] == 2
    assert "permission denied" in row["output_tail"]


def test_target_ready_requires_a_valid_target_partition(tmp_path, monkeypatch):
    _margin_fixture(
        tmp_path,
        monkeypatch,
        open_days=["20261019", "20261020", "20261021"],
        available={"20261021"},
    )
    receipt, _ = MODULE.prepare("20261021", tmp_path, "TUSHARE_TOKEN_2", True)
    row = _margin_row(receipt)
    assert row["status"] == "ready"
    assert row["target_ready"] is True
    assert row["as_of"] == "20261021"
    assert row["lag_calendar_days"] == 0


def test_malformed_calendar_does_not_send_guessed_dates(tmp_path, monkeypatch):
    calls = _margin_fixture(
        tmp_path, monkeypatch, open_days=["20261019", "2026-10-20", "20261021"], available=set()
    )
    receipt, _ = MODULE.prepare("20261021", tmp_path, "TUSHARE_TOKEN_2", True)
    assert calls == ["20261021"]
    assert _margin_row(receipt)["recovery_window_error"] == "malformed local SSE calendar"


def test_duplicate_keys_in_existing_margin_are_not_treated_as_positive(tmp_path, monkeypatch):
    import pandas as pd

    calls = _margin_fixture(
        tmp_path, monkeypatch, open_days=["20261019", "20261020", "20261021"], available=set()
    )
    out = tmp_path / "assets/tushare/a_share/margin/a_share_all_margin_latest"
    path = MODULE._partition(out, "20261020")
    path.parent.mkdir(parents=True)
    pd.DataFrame({"trade_date": ["20261020"] * 2, "exchange_id": ["SSE"] * 2}).to_parquet(
        path, index=False
    )
    before = path.read_bytes()
    receipt, _ = MODULE.prepare("20261021", tmp_path, "TUSHARE_TOKEN_2", True)
    row = _margin_row(receipt)
    assert calls == ["20261019", "20261021"]
    assert row["status"] == "failed"
    assert row["as_of"] is None
    assert path.read_bytes() == before
