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
