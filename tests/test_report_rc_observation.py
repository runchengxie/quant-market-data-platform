"""Observation availability, immutability and no-network preflight regressions."""

import argparse
import json
from pathlib import Path

import pandas as pd
import pytest

from quant_market_data_platform.providers import report_rc_history as history
from quant_market_data_platform.providers import report_rc_observation as observation


def options(root, **kwargs):
    return argparse.Namespace(
        artifacts_root=root,
        source_revision="a" * 40,
        lookback_days=270,
        token_env="TEST",
        interval=0,
        workers=1,
        dry_run=False,
        **kwargs,
    )


@pytest.fixture
def offline_capture(monkeypatch):
    stamp = "2026-11-01T18:30:00+00:00"
    monkeypatch.setattr(observation, "utc_now", lambda: stamp)
    monkeypatch.setattr(history, "utc_now", lambda: stamp)

    def backfill(args):
        root = Path(args.out_dir)
        months = []
        for start, end in history.month_ranges(args.start_date, args.end_date):
            proof = history.store_month(
                root / "raw" / f"month={start[:6]}",
                pd.DataFrame(),
                [0],
                {"start_date": start, "end_date": end, "fields": history.FIELDS},
                acquisition_revision=args.source_revision,
            )
            months.append({"month": start[:6], **proof})
        history.atomic_json(
            root / "manifest.json",
            {
                "status": "completed",
                "months": months,
                "total_rows": 0,
                "start_date": args.start_date,
                "end_date": args.end_date,
            },
        )

    monkeypatch.setattr(history, "backfill", backfill)


def test_local_observation_date_comes_from_actual_utc_clock(tmp_path, offline_capture):
    result = observation.capture_observation(options(tmp_path))
    assert result["observation_date"] == "20261102"
    assert result["started_at_utc"] == "2026-11-01T18:30:00+00:00"
    assert result["query"]["end_date"] == "20261102"
    assert result["status"] == "completed"
    assert observation.verify_observation(Path(result["observation_dir"]))["status"] == "completed"


def test_repeated_same_day_capture_keeps_both_attempts(tmp_path, offline_capture):
    first = observation.capture_observation(options(tmp_path))
    second = observation.capture_observation(options(tmp_path))
    assert first["observation_dir"] != second["observation_dir"]
    assert Path(first["observation_dir"], "observation.json").exists()
    assert Path(second["observation_dir"], "observation.json").exists()


def test_dry_run_neither_fetches_nor_creates_output(tmp_path, monkeypatch):
    def fail(args):
        raise AssertionError("network acquisition attempted")

    monkeypatch.setattr(history, "backfill", fail)
    args = options(tmp_path / "absent")
    args.dry_run = True
    assert observation.capture_observation(args)["status"] == "dry_run"
    assert not args.artifacts_root.exists()


def test_partial_collection_has_no_completed_observation(tmp_path, monkeypatch):
    def fail(args):
        raise RuntimeError("fixture acquisition failed")

    monkeypatch.setattr(history, "backfill", fail)
    with pytest.raises(RuntimeError, match="fixture acquisition"):
        observation.capture_observation(options(tmp_path))
    receipts = list(tmp_path.rglob("observation.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["status"] == "partial"


def test_verified_observation_rejects_changed_history_manifest(tmp_path, offline_capture):
    result = observation.capture_observation(options(tmp_path))
    root = Path(result["observation_dir"])
    (root / "history/manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="hash"):
        observation.verify_observation(root)


def test_verified_observation_rejects_backdated_clock(tmp_path, offline_capture):
    result = observation.capture_observation(options(tmp_path))
    root = Path(result["observation_dir"])
    result["finished_at_utc"] = "2020-01-01T00:00:00+00:00"
    (root / "observation.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="clock"):
        observation.verify_observation(root)


def test_capture_does_not_publish_completion_before_verification(
    tmp_path, offline_capture, monkeypatch
):
    real = history.verified_month

    def verify(path, query):
        observation_root = path.parents[2]
        status = json.loads((observation_root / "observation.json").read_text())["status"]
        assert status == "partial"
        return real(path, query)

    monkeypatch.setattr(history, "verified_month", verify)
    assert observation.capture_observation(options(tmp_path))["status"] == "completed"


def test_verified_observation_pins_monthly_acquisition_metadata(tmp_path, offline_capture):
    result = observation.capture_observation(options(tmp_path))
    root = Path(result["observation_dir"])
    proof_path = next((root / "history/raw").glob("*/receipt.json"))
    proof = json.loads(proof_path.read_text())
    proof["acquisition_revision"] = "b" * 40
    proof_path.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="metadata"):
        observation.verify_observation(root)
