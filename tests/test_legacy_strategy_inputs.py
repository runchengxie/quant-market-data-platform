"""Explicit manifests never substitute asset classes or overstate PIT evidence."""

import hashlib
import importlib
import json

import pandas as pd
import pytest


def make_manifest(tmp_path, **changes):
    data = tmp_path / "panel.parquet"
    pd.DataFrame(
        {"trade_date": ["2020-01-01", "2020-01-02"], "symbol": ["A", "A"], "close": [10.0, 11.0]}
    ).to_parquet(data)
    fields = {
        "schema_version": "research.strategy_input.v1",
        "asset_kind": "etf_daily",
        "path": data.name,
        "sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
        "available_at": None,
    }
    fields.update(changes)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(fields))
    return manifest


def load(path, **kwargs):
    api = importlib.import_module(
        "quant_market_data_platform.research_views.legacy_strategy_inputs"
    )
    return api.load_strategy_panel(path, asset_kind="etf_daily", as_of="2020-01-02", **kwargs)


def test_unreceipted_inputs_remain_diagnostic(tmp_path):
    frame, receipt = load(make_manifest(tmp_path))
    assert len(frame) == 2
    assert receipt["strict_point_in_time"] is False
    assert receipt["evidence_stage"] == "diagnostic"


def test_wrong_asset_kind_never_falls_back(tmp_path):
    with pytest.raises(ValueError, match="asset_kind"):
        load(make_manifest(tmp_path, asset_kind="a_share_daily"))


def test_future_availability_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="available"):
        load(make_manifest(tmp_path, available_at="2020-01-03T00:00:00Z"))


def test_changed_asset_hash_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="sha256"):
        load(make_manifest(tmp_path, sha256="0" * 64))


def test_observations_after_cutoff_are_excluded(tmp_path):
    path = make_manifest(tmp_path)
    api = importlib.import_module(
        "quant_market_data_platform.research_views.legacy_strategy_inputs"
    )
    frame, _ = api.load_strategy_panel(path, asset_kind="etf_daily", as_of="2020-01-01")
    assert frame["trade_date"].max() == pd.Timestamp("2020-01-01")


def test_integer_trade_dates_are_calendar_dates(tmp_path):
    manifest = make_manifest(tmp_path)
    panel = tmp_path / "panel.parquet"
    pd.DataFrame({"trade_date": [20200101, 20200102], "symbol": ["A", "A"]}).to_parquet(panel)
    payload = json.loads(manifest.read_text())
    payload["sha256"] = hashlib.sha256(panel.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(payload))
    frame, _ = load(manifest)
    assert frame["trade_date"].tolist() == [pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02")]


def test_duplicate_persisted_indices_do_not_expand_date_assignment(tmp_path):
    manifest = make_manifest(tmp_path)
    panel = tmp_path / "panel.parquet"
    pd.DataFrame(
        {"trade_date": ["2020-01-01", "2020-01-03"], "symbol": ["A", "B"]}, index=pd.Index([0, 0])
    ).to_parquet(panel)
    payload = json.loads(manifest.read_text())
    payload["sha256"] = hashlib.sha256(panel.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(payload))
    frame, _ = load(manifest)
    assert frame["symbol"].tolist() == ["A"]
    assert frame["trade_date"].tolist() == [pd.Timestamp("2020-01-01")]
