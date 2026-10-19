"""Semantic debt must remain unpublished while acquisition can advance."""

import json
from unittest.mock import patch

import pandas as pd
import pytest

from quant_market_data_platform import tushare_minute_reverse_backfill as scheduler
from quant_market_data_platform.minute_semantic_quarantine import (
    quarantine_status,
    register_quarantine,
    reject_quarantined_dates,
)
from quant_market_data_platform.providers._a_share_mins_constants import _MinuteProgress
from quant_market_data_platform.providers._a_share_mins_partition import (
    _persist_progress,
    _sha256_file,
    _universe_hash,
    validate_complete_minute_partition,
)
from quant_market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy


def fixture(root):
    date, code = "20190509", "002953.SZ"
    times = pd.date_range("2019-05-09 09:30", "2019-05-09 11:30", freq="min").append(
        pd.date_range("2019-05-09 13:01", "2019-05-09 15:00", freq="min")
    )
    f = pd.DataFrame(
        {
            "ts_code": code,
            "trade_time": times,
            "open": 15.15,
            "close": 15.15,
            "high": 15.15,
            "low": 15.15,
            "vol": 100.0,
            "amount": 1515.0,
        }
    )
    f.loc[1, ["vol", "amount"]] = [1800.0, 24355.0]
    part = (
        root / "staging/tushare_minute_backfill_reverse/runs/original/data" / f"trade_date={date}"
    )
    rule = "explicit_symbols"
    progress = _MinuteProgress(
        part_dir=part,
        trade_date=date,
        freq="1min",
        expected_symbols=frozenset([code]),
        universe_hash=_universe_hash({code}, rule=rule),
        universe_rule=rule,
        universe_source="test",
        request_policy=TushareRequestPolicy(),
    )
    _persist_progress(progress, completed_symbols={code}, frames=[f], status="complete")
    path = part / "part-00000.parquet"
    sidecar = part / "_minute_mirror.json"
    report = root / "reports/audit.json"
    report.parent.mkdir()
    issues = {"notional_beyond_hard_price_guard_rows": 1}
    from quant_market_data_platform.quality_a_share_minute import _frame_validation_issues

    issues.update(_frame_validation_issues(date, f))
    issues.update(
        non_241_bar_symbols=0,
        non_positive_price_rows=0,
        unexpected_symbols=0,
        inventory_mismatch_symbols=0,
        zero_volume_nonzero_amount_rows=0,
    )
    record = {
        "trade_date": date,
        "path": str(path),
        "sidecar": str(sidecar),
        "partition_sha256": _sha256_file(path),
        "sidecar_sha256": _sha256_file(sidecar),
        "status": "failed_semantics",
        "binding_matches": True,
        "issues": issues,
    }
    report.write_text(json.dumps({"partitions": [record]}))
    runtime = root / "runtime"
    runtime.mkdir()
    marker = runtime / "semantic-audit-blocked.json"
    marker.write_text(json.dumps({"acceptance": str(report), "audit_exit_code": 75}))
    probe = root / "reports/requery.parquet"
    f.to_parquet(probe, index=False)
    return runtime, report, probe, part


def register(root):
    runtime, report, probe, part = fixture(root)
    result = register_quarantine(
        root,
        runtime,
        report,
        probe,
        trade_date="20190509",
        symbol="002953.SZ",
        reason="Reproduced provider opening aggregation inconsistency",
    )
    return result, runtime, report, probe, part


def test_registration_preserves_originals_and_marker(tmp_path):
    runtime, report, probe, part = fixture(tmp_path)
    files = [*part.iterdir(), runtime / "semantic-audit-blocked.json", report, probe]
    before = {p: p.read_bytes() for p in files}
    result = register_quarantine(
        tmp_path,
        runtime,
        report,
        probe,
        trade_date="20190509",
        symbol="002953.SZ",
        reason="Verified immutable source inconsistency",
    )
    assert result["status"] == "quarantined"
    assert all(p.read_bytes() == body for p, body in before.items())
    gate = quarantine_status(tmp_path, runtime / "semantic-audit-blocked.json")
    assert gate["blocked"] is False
    assert gate["dates"] == ["20190509"]
    with pytest.raises(ValueError, match="quarantin"):
        validate_complete_minute_partition(part, trade_date="20190509", require_full_universe=False)
    with pytest.raises(ValueError, match="quarantin"):
        reject_quarantined_dates(tmp_path, ["20190509"])
    reject_quarantined_dates(tmp_path, ["20190508"])


@pytest.mark.parametrize("target", ["source", "sidecar", "probe", "audit"])
def test_changed_evidence_blocks_again(tmp_path, target):
    _, runtime, report, probe, part = register(tmp_path)
    path = {
        "source": part / "part-00000.parquet",
        "sidecar": part / "_minute_mirror.json",
        "probe": probe,
        "audit": report,
    }[target]
    path.write_bytes(path.read_bytes() + b" ")
    assert quarantine_status(tmp_path, runtime / "semantic-audit-blocked.json")["blocked"]


def test_unregistered_new_failure_still_blocks(tmp_path):
    _, runtime, _, _, _ = register(tmp_path)
    other = tmp_path / "reports/new-failure.json"
    other.write_text(json.dumps({"partitions": []}))
    (runtime / "semantic-audit-blocked.json").write_text(json.dumps({"acceptance": str(other)}))
    assert quarantine_status(tmp_path, runtime / "semantic-audit-blocked.json")["blocked"]


def test_requery_mismatch_cannot_be_quarantined(tmp_path):
    runtime, report, probe, _ = fixture(tmp_path)
    f = pd.read_parquet(probe)
    f.loc[1, "amount"] = 27270.0
    f.to_parquet(probe, index=False)
    with pytest.raises(ValueError, match="requery"):
        register_quarantine(
            tmp_path,
            runtime,
            report,
            probe,
            trade_date="20190509",
            symbol="002953.SZ",
            reason="Must reproduce",
        )
    assert quarantine_status(tmp_path, runtime / "semantic-audit-blocked.json")["blocked"]


def test_only_registered_acquisition_can_proceed(tmp_path):
    _, runtime, _, _, _ = register(tmp_path)
    with patch.object(scheduler, "_run_reverse_backfill", return_value={"status": "noop"}) as run:
        result = scheduler.run_reverse_backfill(
            scheduler.ReverseBackfillOptions(
                artifacts_root=tmp_path, audit_block_path=runtime / "semantic-audit-blocked.json"
            )
        )
    assert result["status"] == "download_pass_complete_with_gaps"
    assert result["semantic_quarantine_dates"] == ["20190509"]
    run.assert_called_once()


def test_invalid_registry_fails_closed_even_without_marker(tmp_path):
    p = tmp_path / "metadata/minute_backfill/reverse_scheduler/semantic_quarantine.json"
    p.parent.mkdir(parents=True)
    p.write_text("{broken")
    assert quarantine_status(tmp_path, None)["blocked"]
    with pytest.raises(ValueError):
        reject_quarantined_dates(tmp_path, ["20190508"])


def test_candidate_publication_refuses_quarantined_source(tmp_path):
    from quant_market_data_platform import minute_candidate_publish as publish

    _, _, _, _, part = register(tmp_path)
    inventory = {
        "partitions": [
            {
                "trade_date": "20190509",
                "candidate": {"partition_path": str(part / "part-00000.parquet")},
            }
        ]
    }
    with patch.object(publish, "_validated_candidate_evidence", return_value=(inventory, {})):
        with pytest.raises(ValueError, match="quarantin"):
            publish.publish_candidate(
                tmp_path / "inventory.json",
                tmp_path / "semantic.json",
                tmp_path / "candidate",
                tmp_path / "receipt.json",
            )
    assert not (tmp_path / "candidate").exists()


def test_monitor_keeps_quality_debt_visible_and_alerts_once(tmp_path):
    from datetime import UTC, datetime

    from quant_market_data_platform.minute_history_status import watch_history

    _, runtime, _, _, _ = register(tmp_path)
    now = datetime.now(UTC)
    from zoneinfo import ZoneInfo

    (runtime / "runtime-preflight.json").write_text(
        json.dumps(
            {
                "generated_at": now.isoformat(),
                "quota_date": now.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d"),
                "available_requests": 100,
                "blocked_by_audit": False,
                "free_disk_bytes": 1000000,
            }
        )
    )
    with patch(
        "quant_market_data_platform.minute_history_status.inventory",
        return_value={"settled_dates": 1, "independently_audited_dates": 0},
    ):
        a = watch_history(tmp_path, runtime, tmp_path / "monitor", now=now, min_free_bytes=0)
        b = watch_history(tmp_path, runtime, tmp_path / "monitor", now=now, min_free_bytes=0)
    assert a["state"] == b["state"] == "idle"
    assert a["progress"]["semantic_quarantine_dates"] == ["20190509"]
    events = (tmp_path / "monitor/alerts.jsonl").read_text().splitlines()
    assert len(events) == 1
    assert json.loads(events[0])["severity"] == "warning"


@pytest.mark.parametrize("change", ["other_symbol", "structural", "other_issue"])
def test_quarantine_rejects_uncovered_failures(tmp_path, change):
    runtime, report, probe, part = fixture(tmp_path)
    if change == "other_issue":
        a = json.loads(report.read_text())
        a["partitions"][0]["issues"]["wrong_date_rows"] = 1
        report.write_text(json.dumps(a))
    elif change == "other_symbol":
        with pytest.raises(ValueError, match="symbol"):
            register_quarantine(
                tmp_path,
                runtime,
                report,
                probe,
                trade_date="20190509",
                symbol="000001.SZ",
                reason="test",
            )
        return
    else:
        f = pd.read_parquet(probe)
        f.loc[4, "amount"] = -1
        f.to_parquet(probe, index=False)
    with pytest.raises(ValueError):
        register_quarantine(
            tmp_path,
            runtime,
            report,
            probe,
            trade_date="20190509",
            symbol="002953.SZ",
            reason="test",
        )


def test_quarantine_cli_is_explicit_and_credential_free(tmp_path, capsys):
    from scripts.operations.tushare_minute_history import main

    runtime, report, probe, _ = fixture(tmp_path)
    assert (
        main(
            [
                "quarantine",
                "--artifacts-root",
                str(tmp_path),
                "--runtime-root",
                str(runtime),
                "--acceptance",
                str(report),
                "--requery",
                str(probe),
                "--trade-date",
                "20190509",
                "--symbol",
                "002953.SZ",
                "--reason",
                "Reproduced supplier anomaly",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "quarantined"


def test_operational_promotion_rejects_before_alias_mutation(tmp_path):
    from quant_market_data_platform.tushare_minute_operational import (
        OPERATIONAL_VERSION_SCHEMA,
        promote_operational_alias,
    )

    register(tmp_path)
    output = tmp_path / "version"
    output.mkdir()
    receipt = {
        "schema_version": OPERATIONAL_VERSION_SCHEMA,
        "status": "published_operational_version",
        "output_dir": str(output),
        "daily": [{"trade_date": "20190509"}],
    }
    path = tmp_path / "version.json"
    body = json.dumps(receipt)
    path.write_text(body)
    (output / "_operational_receipt.json").write_text(body)
    alias = tmp_path / "assets/derived/a_share/minute_1m_tushare"
    alias.mkdir(parents=True)
    (alias / "keep").write_text("untouched")
    legacy = alias.with_name("minute_1m")
    legacy.mkdir()
    with pytest.raises(ValueError, match="quarantin"):
        promote_operational_alias(path, alias, legacy, tmp_path / "promotion.json")
    assert (alias / "keep").read_text() == "untouched"
    assert not (tmp_path / "promotion.json").exists()


def test_operational_assembly_rejects_quarantined_base(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quant_market_data_platform.tushare_minute_operational import (
        OperationalAssembly,
        assemble_operational_version,
    )

    _, _, _, _, part = register(tmp_path)
    receipt = {
        "schema_version": "a_share.minute_tushare_candidate.v1",
        "status": "published_candidate",
        "current_alias_mutated": False,
        "output_dir": str(part.parent),
        "daily": [
            {
                "trade_date": "20190509",
                "rows": 241,
                "symbols": 1,
                "content_sha256": _sha256_file(part / "part-00000.parquet"),
            }
        ],
    }
    path = tmp_path / "base.json"
    path.write_text(json.dumps(receipt))
    calendar = tmp_path / "calendar.parquet"
    pq.write_table(pa.table({"cal_date": ["20190509", "20190510"], "is_open": [1, 1]}), calendar)
    from test_tushare_minute_operational import _incremental_partition

    incremental = tmp_path / "incremental"
    _incremental_partition(incremental, "20190510")
    destination = tmp_path / "output"
    with pytest.raises(ValueError, match="quarantin"):
        assemble_operational_version(
            OperationalAssembly(
                path, (incremental,), calendar, "20190510", destination, tmp_path / "assembled.json"
            )
        )
    assert not destination.exists()


def test_new_repair_failure_remains_blocking_under_quarantined_marker(tmp_path):
    from quant_market_data_platform._minute_gap_repair_artifacts import _save_bad
    from quant_market_data_platform.minute_gap_repair import GapRepairOptions

    _, runtime, _, _, part = register(tmp_path)
    marker = runtime / "semantic-audit-blocked.json"
    original = marker.read_bytes()
    _save_bad(GapRepairOptions(tmp_path, runtime), part, ValueError("new semantic failure"))
    assert marker.read_bytes() == original
    assert quarantine_status(tmp_path, marker)["blocked"]
    with patch.object(scheduler, "_run_reverse_backfill") as run:
        assert (
            scheduler.run_reverse_backfill(
                scheduler.ReverseBackfillOptions(tmp_path, audit_block_path=marker)
            )["status"]
            == "audit_blocked"
        )
    run.assert_not_called()


def test_lexical_alias_registry_survives_external_symlink(tmp_path):
    root = tmp_path / "owner"
    root.mkdir()
    register(root)
    external = tmp_path / "external"
    external.mkdir()
    alias = root / "assets/derived/a_share/minute_1m_tushare"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="quarantin"):
        reject_quarantined_dates(alias, ["20190509"])


def test_candidate_destination_keeps_lexical_owner_registry(tmp_path):
    from quant_market_data_platform import minute_candidate_publish as publish

    root = tmp_path / "owner"
    root.mkdir()
    register(root)
    external = tmp_path / "external"
    external.mkdir()
    link = root / "published"
    link.symlink_to(external, target_is_directory=True)
    inventory = {
        "partitions": [
            {
                "trade_date": "20190509",
                "candidate": {"partition_path": str(external / "source.parquet")},
            }
        ]
    }
    with patch.object(publish, "_validated_candidate_evidence", return_value=(inventory, {})):
        with pytest.raises(ValueError, match="quarantin"):
            publish.publish_candidate(
                root / "i.json", root / "a.json", link / "candidate", root / "r.json"
            )
    assert not (external / "candidate").exists()
