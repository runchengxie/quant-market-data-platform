"""Independent validation must reject changed acquired bytes."""

from datetime import UTC, datetime
from pathlib import Path

import pytest


def test_review_rejects_unknown_policy_before_writing(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot_review import review_etf_snapshot

    with pytest.raises(ValueError, match="policy"):
        review_etf_snapshot(
            tmp_path / "manifest.json",
            reviewer="acquisition",
            policy_sha256="0" * 64,
            now=datetime.now(UTC),
            output=tmp_path / "review.json",
        )
    assert not (tmp_path / "review.json").exists()


def _make_snapshot(tmp_path: Path) -> tuple[Path, dict]:
    import hashlib
    import json

    import pandas as pd

    files = {}
    for name, values in {
        "calendar": {"cal_date": ["20261016"], "is_open": [1]},
        "daily": {
            "ts_code": ["510300.SH"],
            "trade_date": ["20261016"],
            "close": [4.0],
            "amount": [20000.0],
        },
        "factors": {"ts_code": ["510300.SH"], "trade_date": ["20261016"], "adj_factor": [1.0]},
        "panel": {"trade_date": ["20261016"], "510300.SH": [4.0]},
    }.items():
        path = tmp_path / f"{name}.parquet"
        pd.DataFrame(values).to_parquet(path, index=False)
        files[name] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "marketdata.etf_snapshot.v1",
                "status": "unreviewed",
                "symbols": ["510300.SH"],
                "start_date": "20261016",
                "end_date": "20261016",
                "baseline_sha256": None,
                "capture_started_at": "2026-10-16T10:30:00+00:00",
                "capture_completed_at": "2026-10-16T10:31:00+00:00",
                "vendor_publication_at": None,
                "request_count": 3,
                "files": files,
                "reasons": [],
            }
        )
    )
    return manifest, files


def test_review_accepts_hash_bound_snapshot_and_rejects_changed_bytes(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot_review import (
        POLICY_SHA256,
        REVIEWER,
        review_etf_snapshot,
    )

    manifest, files = _make_snapshot(tmp_path)
    receipt = tmp_path / "review.json"
    now = datetime(2026, 10, 16, 11, tzinfo=UTC)
    result = review_etf_snapshot(
        manifest, reviewer=REVIEWER, policy_sha256=POLICY_SHA256, now=now, output=receipt
    )
    assert result["status"] == "accepted"
    assert result["observed_by"] == "2026-10-16T10:31:00+00:00"
    assert result["panel_sha256"] == files["panel"]["sha256"]
    original = receipt.read_bytes()
    review_etf_snapshot(
        manifest, reviewer=REVIEWER, policy_sha256=POLICY_SHA256, now=now, output=receipt
    )
    assert receipt.read_bytes() == original
    (tmp_path / "panel.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError):
        review_etf_snapshot(
            manifest,
            reviewer=REVIEWER,
            policy_sha256=POLICY_SHA256,
            now=now,
            output=tmp_path / "changed-review.json",
        )


def test_review_cli_rejects_acquisition_identity(tmp_path: Path) -> None:
    from quant_market_data_platform.cli import main

    with pytest.raises(ValueError, match="policy"):
        main(
            [
                "tushare",
                "review-etf-snapshot",
                "--manifest",
                str(tmp_path / "missing.json"),
                "--reviewer",
                "acquisition",
                "--policy-sha256",
                "0" * 64,
                "--output",
                str(tmp_path / "receipt.json"),
            ]
        )
    assert not (tmp_path / "receipt.json").exists()


def test_preclose_acquisition_cannot_be_approved_by_slow_factor_completion(tmp_path: Path) -> None:
    import json

    from quant_market_data_platform.etf_snapshot_review import (
        POLICY_SHA256,
        REVIEWER,
        review_etf_snapshot,
    )

    manifest, _ = _make_snapshot(tmp_path)
    value = json.loads(manifest.read_bytes())
    value["capture_started_at"] = "2026-10-16T14:59:00+08:00"
    value["capture_completed_at"] = "2026-10-16T15:01:00+08:00"
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="close"):
        review_etf_snapshot(
            manifest,
            reviewer=REVIEWER,
            policy_sha256=POLICY_SHA256,
            now=datetime(2026, 10, 16, 11, tzinfo=UTC),
            output=tmp_path / "review.json",
        )
