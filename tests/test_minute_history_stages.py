"""History phases must not turn scoped acceptance or missing evidence into publication."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from quant_market_data_platform.minute_history_status import history_status

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def observe(tmp_path):
    root, runtime = tmp_path / "data", tmp_path / "runtime"
    root.mkdir(exist_ok=True)
    put(
        runtime / "runtime-preflight.json",
        {
            "generated_at": NOW.isoformat(),
            "quota_date": "20261021",
            "available_requests": 0,
        },
    )
    return root, runtime


def test_empty_queue_and_scoped_audit_do_not_certify_or_publish_history(tmp_path):
    root, runtime = observe(tmp_path)
    metadata = root / "metadata/minute_backfill/reverse_scheduler"
    put(metadata / "deferred_missing.json", {"dates": {}})
    put(runtime / "latest-run-result.json", {"deferred_dates": 111})
    sidecar = (
        root / "staging/tushare_minute_backfill_reverse/runs/a/data/"
        "trade_date=20170414/_minute_mirror.json"
    )
    put(sidecar, {"trade_date": "20170414", "status": "complete"})
    put(
        metadata / "recovery-audit.json",
        {
            "partitions": [
                {
                    "sidecar": str(sidecar),
                    "sidecar_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
                    "binding_matches": True,
                    "status": "passed_requested_universe_only",
                    "issues": {"null_rows": 0},
                }
            ]
        },
    )
    asset = root / "assets/derived/a_share/minute_1m_tushare"
    put(
        asset / "_operational_receipt.json",
        {
            "output_dir": str(asset),
            "schema_version": "a_share.minute_tushare_operational_version.v1",
            "status": "published_operational_version",
            "summary": {
                "date_min": "20261013",
                "date_max": "20261021",
                "dates": 7,
                "rows": 100,
                "market_scope": "SH_SZ_BJ",
            },
        },
    )
    result = history_status(root, runtime, now=NOW)
    stages = result["pipeline"]
    assert result["state"] == "quota_wait"
    assert stages["acquisition"]["deferred_dates"] == 0
    assert stages["acquisition"]["settled_dates"] == 1
    assert stages["semantic_audit"]["audited_dates"] == 1
    assert stages["coverage"]["full_market_audited_dates"] == 0
    assert stages["coverage"]["full_market_ready"] is False
    assert stages["publication"]["scope"] == "current_published_asset"
    assert stages["publication"]["date_min"] == "20261013"
    assert stages["publication"]["dates"] == 7


def test_absent_publication_and_queue_are_unknown_counts(tmp_path):
    root, runtime = observe(tmp_path)
    stages = history_status(root, runtime, now=NOW)["pipeline"]
    assert stages["acquisition"]["deferred_dates"] is None
    assert stages["publication"]["dates"] is None
    assert stages["publication"]["evidence"] == "unavailable"


@pytest.mark.parametrize("dates", [None, [], {"not-a-date": {}}])
def test_invalid_queue_does_not_display_a_successful_zero(tmp_path, dates):
    root, runtime = observe(tmp_path)
    put(root / "metadata/minute_backfill/reverse_scheduler/deferred_missing.json", {"dates": dates})
    result = history_status(root, runtime, now=NOW)
    assert result["pipeline"]["acquisition"]["deferred_dates"] is None
    assert result["state"] == "unknown_stale_evidence"
    assert result["evidence_warnings"]


def test_untrusted_operational_receipt_cannot_supply_published_counts(tmp_path):
    root, runtime = observe(tmp_path)
    put(
        root / "assets/derived/a_share/minute_1m_tushare/_operational_receipt.json",
        {
            "summary": {"dates": 999, "date_min": "20160101"},
        },
    )
    result = history_status(root, runtime, now=NOW)
    assert result["pipeline"]["publication"]["dates"] is None
    assert result["state"] == "unknown_stale_evidence"
