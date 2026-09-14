from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from market_data_platform import cli, cli_data
from market_data_platform.providers import (
    a_share_minute_bj_overlay,
    a_share_minute_build,
    a_share_minute_coverage,
    guan_annual_minbar,
)


def test_source_audit_lineage_binds_verified_mobile_promotion(tmp_path: Path) -> None:
    provider_root = tmp_path / "raw" / "source_native" / "guan_mobile"
    deal_root = provider_root / "deal"
    deal_root.mkdir(parents=True)
    receipt = tmp_path / "promotion.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "guan.mobile_raw_promotion.v1",
                "status": "complete",
                "verification_status": "verified",
                "verification": {
                    "status": "verified",
                },
                "provider_root": str(provider_root),
                "entries": [{"verification_status": "verified"}],
                "source_duplicates": [{"verification_status": "verified_exact_duplicate"}],
            }
        ),
        encoding="utf-8",
    )

    lineage = cli_data._source_audit_lineage([receipt], guan_deal_dir=deal_root)

    assert lineage["guan_mobile_promotion_receipt"] == str(receipt)
    assert lineage["guan_mobile_promotion_receipt_sha256"] == cli_data._sha256_file(receipt)
    assert lineage["guan_mobile_promotion_verification_status"] == "verified"
    assert lineage["guan_mobile_provider_root"] == str(provider_root)
    assert lineage["source_audit_receipts"] == [
        {
            "path": str(receipt),
            "sha256": cli_data._sha256_file(receipt),
            "schema_version": "guan.mobile_raw_promotion.v1",
            "status": "complete",
            "verification_status": "verified",
        }
    ]

    with pytest.raises(ValueError, match="outside the verified mobile provider root"):
        cli_data._source_audit_lineage(
            [receipt],
            guan_deal_dir=tmp_path / "unrelated-deal",
        )


def test_full_day_cli_lineage_binds_plan_receipt_status_and_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    date = "20260709"
    plan_path = tmp_path / "plan.json"
    receipt_path = tmp_path / "receipt.json"
    plan_path.write_text("plan", encoding="utf-8")
    args = argparse.Namespace(
        tushare_full_day_dir=str(tmp_path / "full"),
        tushare_full_plan=str(plan_path),
        tushare_full_receipt=str(receipt_path),
        out_dir=str(tmp_path / "version"),
        dry_run=True,
    )
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        a_share_minute_coverage,
        "load_tushare_full_day_plan",
        lambda _path: a_share_minute_coverage.TushareFullDayPlan(
            phase="pilot",
            dates=(date,),
            sha256=cli_data._sha256_file(plan_path),
        ),
    )
    monkeypatch.setattr(
        a_share_minute_coverage,
        "discover_tushare_full_day_partitions",
        lambda _root: {date: tmp_path / "full" / f"trade_date={date}"},
    )

    def fake_materialize(output_dir, **kwargs):
        captured.append({"output_dir": output_dir, **kwargs})
        receipt_path.write_text("receipt", encoding="utf-8")
        return {
            "status": "planned",
            "phase": "pilot",
            "policy": {"source_validation": "passed"},
            "summary": {
                "top200_replacement_dates": 1,
                "guan_partial_session_replacement_dates": 0,
                "missing_replacement_dates": 0,
            },
        }

    monkeypatch.setattr(
        a_share_minute_coverage,
        "materialize_tushare_full_days",
        fake_materialize,
    )
    lineage: dict[str, object] = {}

    result, dates = cli_data._materialize_tushare_full_day_plan(
        args,
        cli_data._FullDayPlanContext(
            trade_dates=[date],
            annual_dates=set(),
            deal_dates=set(),
            tushare_dates={date},
            annual_partial_session_dates=set(),
            deal_override_dates=set(),
            evidence_lineage=lineage,
        ),
    )

    assert result is not None
    assert result["status"] == "planned"
    assert dates == {date}
    assert captured[0]["plan_sha256"] == cli_data._sha256_file(plan_path)
    assert lineage["tushare_full_plan_sha256"] == cli_data._sha256_file(plan_path)
    assert lineage["tushare_full_receipt_sha256"] == cli_data._sha256_file(receipt_path)
    assert lineage["tushare_full_status"] == "planned"
    assert lineage["tushare_full_policy"] == {"source_validation": "passed"}


def test_finalize_minute_coverage_defaults_through_20260714() -> None:
    args = cli.build_parser().parse_args(
        [
            "data",
            "finalize-a-share-minute-coverage",
            "--trade-cal",
            "trade-cal.parquet",
            "--annual-manifest",
            "annual.json",
            "--deal-manifest",
            "deal.json",
            "--guan-deal-dir",
            "deal",
            "--tushare-batch-dir",
            "batches",
            "--out-dir",
            "output",
            "--manifest",
            "coverage.json",
            "--overlap-audit",
            "overlap.json",
        ]
    )

    assert args.end_date == "20260714"


def test_full_day_requirements_use_actual_residual_partial_date_sets() -> None:
    top200_dates = {f"202601{day:02d}" for day in range(1, 31)}
    replaced_guan_dates = {"20260423", "20260424"}
    residual_guan_date = "20260425"
    deal_override_date = "20260426"
    partial_materialization = {
        "actions": [{"date": date, "status": "written_partial"} for date in top200_dates]
        + [
            {"date": residual_guan_date, "status": "audit_only"},
            {"date": deal_override_date, "status": "audit_only"},
        ]
    }
    base = a_share_minute_coverage.CoverageRequirements(
        expected_trade_dates=100,
        expected_annual_full_sh_sz_dates=60,
        expected_deal_full_sh_sz_dates=10,
        expected_guan_partial_session_dates=4,
        expected_partial_top200_dates=28,
        expected_accepted_zero_volume_nonzero_amount_rows=7,
        expected_accepted_positive_volume_zero_amount_rows=1,
        require_full_source_stats=True,
    )
    full_day_dates = top200_dates | replaced_guan_dates

    result = cli_data._coverage_requirements_with_full_days(
        base,
        partial_materialization,
        full_day_dates,
        annual_partial_session_dates=(
            replaced_guan_dates | {residual_guan_date, deal_override_date}
        ),
        deal_override_dates={deal_override_date},
    )

    assert result.expected_tushare_full_a_share_dates == 32
    assert result.expected_partial_top200_dates == 0
    assert result.expected_guan_partial_session_dates == 1
    assert result.expected_accepted_zero_volume_nonzero_amount_rows == 7
    assert result.expected_accepted_positive_volume_zero_amount_rows == 1


def test_full_day_requirements_count_all_persisting_partial_action_statuses() -> None:
    result = cli_data._coverage_requirements_with_full_days(
        a_share_minute_coverage.PRODUCTION_COVERAGE_REQUIREMENTS,
        {
            "actions": [
                {"date": "20260709", "status": "written_partial"},
                {"date": "20260710", "status": "planned_partial"},
                {"date": "20260713", "status": "skipped_existing_partial"},
                {"date": "20260714", "status": "audit_only"},
            ]
        },
        {"20260709", "20260710"},
        annual_partial_session_dates=set(),
        deal_override_dates=set(),
    )

    assert result.expected_partial_top200_dates == 1


def test_bj_overlay_cli_group_is_atomic_and_forwards_explicit_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    incomplete = argparse.Namespace(
        tushare_bj_overlay_dir=str(tmp_path / "bj"),
        tushare_bj_overlay_plan=None,
        tushare_bj_overlay_receipt=None,
    )
    with pytest.raises(ValueError, match="must be supplied together"):
        cli_data._has_complete_tushare_bj_overlay_args(incomplete)

    date = "20220104"
    plan_path = tmp_path / "plan.json"
    receipt_path = tmp_path / "receipt.json"
    plan_path.write_text("plan", encoding="utf-8")
    args = argparse.Namespace(
        tushare_bj_overlay_dir=str(tmp_path / "bj"),
        tushare_bj_overlay_plan=str(plan_path),
        tushare_bj_overlay_receipt=str(receipt_path),
        out_dir=str(tmp_path / "version"),
        dry_run=True,
    )
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        a_share_minute_bj_overlay,
        "load_bj_overlay_plan",
        lambda _path: a_share_minute_bj_overlay.BJOverlayPlan(
            (date,),
            sha256=cli_data._sha256_file(plan_path),
        ),
    )
    monkeypatch.setattr(
        a_share_minute_bj_overlay,
        "discover_tushare_bj_partitions",
        lambda _root: {date: tmp_path / "bj" / f"trade_date={date}"},
    )

    def fake_materialize(output_dir, **kwargs):
        captured.append({"output_dir": output_dir, **kwargs})
        receipt_path.write_text("receipt", encoding="utf-8")
        return {
            "status": "planned",
            "phase": "pilot",
            "policy": {"source_validation": "passed"},
            "summary": {"date_count": 1},
        }

    monkeypatch.setattr(
        a_share_minute_bj_overlay,
        "materialize_tushare_bj_overlay",
        fake_materialize,
    )
    lineage: dict[str, object] = {}

    result, dates = cli_data._materialize_tushare_bj_overlay_plan(
        args,
        cli_data._BJOverlayPlanContext(
            annual_full_dates={date},
            deal_full_dates=set(),
            tushare_full_dates=set(),
            base_expected_stats={date: {"rows": 2, "symbols": 1}},
            evidence_lineage=lineage,
        ),
    )

    assert result is not None
    assert result["status"] == "planned"
    assert dates == {date}
    assert captured[0]["overlay_dates"] == (date,)
    assert captured[0]["phase"] == "pilot"
    assert captured[0]["dry_run"] is True
    assert lineage["tushare_bj_overlay_dates"] == [date]
    assert lineage["tushare_bj_overlay_plan_sha256"]
    assert lineage["tushare_bj_overlay_receipt_sha256"]


def test_bj_overlay_cli_rejects_plan_bytes_changed_during_materialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    date = "20220104"
    plan_path = tmp_path / "plan.json"
    receipt_path = tmp_path / "receipt.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": a_share_minute_bj_overlay.BJ_OVERLAY_PLAN_SCHEMA,
                "phase": "production",
                "dates": [date],
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        tushare_bj_overlay_dir=str(tmp_path / "bj"),
        tushare_bj_overlay_plan=str(plan_path),
        tushare_bj_overlay_receipt=str(receipt_path),
        out_dir=str(tmp_path / "version"),
        dry_run=True,
    )
    monkeypatch.setattr(
        a_share_minute_bj_overlay,
        "discover_tushare_bj_partitions",
        lambda _root: {date: tmp_path / "bj" / f"trade_date={date}"},
    )

    def fake_materialize(output_dir, **kwargs):
        receipt_path.write_text("receipt", encoding="utf-8")
        plan_path.write_text(plan_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return {
            "status": "planned",
            "policy": {"source_validation": "passed"},
            "summary": {"date_count": 1},
        }

    monkeypatch.setattr(
        a_share_minute_bj_overlay,
        "materialize_tushare_bj_overlay",
        fake_materialize,
    )

    with pytest.raises(RuntimeError, match="plan changed during materialization"):
        cli_data._materialize_tushare_bj_overlay_plan(
            args,
            cli_data._BJOverlayPlanContext(
                annual_full_dates={date},
                deal_full_dates=set(),
                tushare_full_dates=set(),
                base_expected_stats={date: {"rows": 2, "symbols": 1}},
                evidence_lineage={},
            ),
        )


def test_build_guan_annual_minutes_runs_years_strictly_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []

    def fake_build(options):
        calls.append(options)
        return {
            "status": "complete",
            "source_full_scans": 1,
            "staging_validation": {"dates": [f"{options.year}0102"]},
            "promoted_dates": [f"{options.year}0102"],
            "preserved_dates": [],
            "replaced_invalid_dates": [],
        }

    monkeypatch.setattr(guan_annual_minbar, "build_guan_annual_minbar", fake_build)

    exit_code = cli.main(
        [
            "data",
            "build-guan-annual-minutes",
            "--minbar-dir",
            str(tmp_path / "minute"),
            "--years",
            "2016-2017,2019",
            "--out-dir",
            str(tmp_path / "output"),
            "--manifest",
            str(tmp_path / "annual.json"),
            "--staging-root",
            str(tmp_path / "staging"),
        ]
    )

    assert exit_code == 0
    assert [options.year for options in calls] == [2016, 2017, 2019]
    assert [options.source_path.name for options in calls] == [
        "minbar_2016.parquet",
        "minbar_2017.parquet",
        "minbar_2019.parquet",
    ]
    assert all(options.threads == 3 for options in calls)
    assert all(options.memory_limit == "auto" for options in calls)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "complete"
    assert payload["year_count"] == 3
    assert payload["date_count"] == 3
    assert payload["source_full_scans"] == 3


def test_build_guan_annual_minutes_preserves_explicit_memory_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []

    def fake_build(options):
        calls.append(options)
        return {
            "status": "complete",
            "source_full_scans": 1,
            "memory_limit": {
                "requested": options.memory_limit,
                "resolved": "8192MiB",
                "resolved_mb": 8192,
                "snapshot": {},
            },
            "staging_validation": {"dates": ["20250102"]},
            "promoted_dates": ["20250102"],
            "preserved_dates": [],
            "replaced_invalid_dates": [],
        }

    monkeypatch.setattr(guan_annual_minbar, "build_guan_annual_minbar", fake_build)

    exit_code = cli.main(
        [
            "data",
            "build-guan-annual-minutes",
            "--minbar-dir",
            str(tmp_path / "minute"),
            "--years",
            "2025",
            "--out-dir",
            str(tmp_path / "output"),
            "--manifest",
            str(tmp_path / "annual.json"),
            "--staging-root",
            str(tmp_path / "staging"),
            "--memory-limit",
            "8GB",
        ]
    )

    assert exit_code == 0
    assert calls[0].memory_limit == "8GB"
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["memory_limit"]["requested"] == "8GB"


def test_build_guan_annual_minutes_stops_on_first_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[int] = []

    def fake_build(options):
        calls.append(options.year)
        if options.year == 2017:
            raise RuntimeError("source validation failed")
        return {
            "status": "complete",
            "source_full_scans": 1,
            "staging_validation": {"dates": ["20160104"]},
            "promoted_dates": ["20160104"],
        }

    monkeypatch.setattr(guan_annual_minbar, "build_guan_annual_minbar", fake_build)

    exit_code = cli.main(
        [
            "data",
            "build-guan-annual-minutes",
            "--minbar-dir",
            str(tmp_path / "minute"),
            "--years",
            "2016-2018",
            "--out-dir",
            str(tmp_path / "output"),
            "--manifest",
            str(tmp_path / "annual.json"),
            "--staging-root",
            str(tmp_path / "staging"),
        ]
    )

    assert exit_code == 1
    assert calls == [2016, 2017]
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["failed_year"] == 2017
    assert payload["completed_years"] == [2016]
    assert payload["error"] == {
        "type": "RuntimeError",
        "message": "source validation failed",
    }


def test_build_guan_deal_minutes_never_passes_tushare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured = []
    annual_date = "20260424"

    monkeypatch.setattr(
        a_share_minute_coverage,
        "load_annual_minbar_manifest",
        lambda *args: a_share_minute_coverage.AnnualMinbarCoverageResult(
            dates={annual_date},
            stats={},
            full_dates={annual_date},
            partial_session_dates=set(),
        ),
    )

    def fake_build(options):
        captured.append(options)
        return {
            "status": "passed",
            "output_dir": str(options.output_dir),
            "validation": {
                "partition_count": 2,
                "rows": 200,
                "date_min": options.start_date,
                "date_max": options.end_date,
                "invalid_dates": [],
            },
        }

    monkeypatch.setattr(a_share_minute_build, "build_fused_minute_dataset", fake_build)

    exit_code = cli.main(
        [
            "data",
            "build-guan-deal-minutes",
            "--legacy-input-dir",
            str(tmp_path / "output"),
            "--annual-manifest",
            str(tmp_path / "annual.json"),
            "--guan-deal-dir",
            str(tmp_path / "deal"),
            "--instruments",
            str(tmp_path / "instruments.parquet"),
            "--out-dir",
            str(tmp_path / "output"),
            "--manifest",
            str(tmp_path / "deal.json"),
            "--start-date",
            "20260608",
            "--end-date",
            "20260708",
        ]
    )

    assert exit_code == 0
    assert len(captured) == 1
    options = captured[0]
    assert options.tushare_batch_dir is None
    assert options.guan_deal_start_date == "20260608"
    assert options.deal_engine == "polars"
    assert options.legacy_workers == 1
    assert options.protected_dates == (annual_date,)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "passed"
    assert payload["tushare_batch_dir"] is None
    assert payload["protected_annual_date_count"] == 1


def test_build_guan_deal_minutes_requires_and_persists_exact_annual_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    annual_date = "20260302"
    captured = []
    monkeypatch.setattr(
        a_share_minute_coverage,
        "load_annual_minbar_manifest",
        lambda *args: a_share_minute_coverage.AnnualMinbarCoverageResult(
            dates={annual_date},
            stats={},
            full_dates=set(),
            partial_session_dates={annual_date},
        ),
    )
    monkeypatch.setattr(
        a_share_minute_coverage,
        "discover_guan_deal_files",
        lambda *args: {annual_date: tmp_path / f"deal_{annual_date}.parquet"},
    )

    def fake_build(options):
        captured.append(options)
        return {
            "status": "passed",
            "output_dir": str(options.output_dir),
            "validation": {
                "partition_count": 1,
                "rows": 2,
                "date_min": annual_date,
                "date_max": annual_date,
                "invalid_dates": [],
            },
        }

    monkeypatch.setattr(a_share_minute_build, "build_fused_minute_dataset", fake_build)
    arguments = [
        "data",
        "build-guan-deal-minutes",
        "--legacy-input-dir",
        str(tmp_path / "output"),
        "--annual-manifest",
        str(tmp_path / "annual.json"),
        "--guan-deal-dir",
        str(tmp_path / "deal"),
        "--instruments",
        str(tmp_path / "instruments.parquet"),
        "--out-dir",
        str(tmp_path / "output"),
        "--manifest",
        str(tmp_path / "deal.json"),
        "--start-date",
        annual_date,
        "--end-date",
        annual_date,
    ]

    assert cli.main([*arguments, "--annual-override-date", annual_date]) == 0

    assert captured[0].protected_dates == (annual_date,)
    assert captured[0].annual_override_dates == (annual_date,)
    payload = json.loads(capsys.readouterr().out)
    assert payload["annual_override_date_count"] == 1
    assert payload["annual_override_dates"] == [annual_date]


def test_build_guan_deal_minutes_rejects_implicit_annual_overlap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    annual_date = "20260302"
    monkeypatch.setattr(
        a_share_minute_coverage,
        "load_annual_minbar_manifest",
        lambda *args: a_share_minute_coverage.AnnualMinbarCoverageResult(
            dates={annual_date},
            stats={},
            full_dates=set(),
            partial_session_dates={annual_date},
        ),
    )
    monkeypatch.setattr(
        a_share_minute_coverage,
        "discover_guan_deal_files",
        lambda *args: {annual_date: tmp_path / f"deal_{annual_date}.parquet"},
    )

    exit_code = cli.main(
        [
            "data",
            "build-guan-deal-minutes",
            "--legacy-input-dir",
            str(tmp_path / "output"),
            "--annual-manifest",
            str(tmp_path / "annual.json"),
            "--guan-deal-dir",
            str(tmp_path / "deal"),
            "--instruments",
            str(tmp_path / "instruments.parquet"),
            "--out-dir",
            str(tmp_path / "output"),
            "--manifest",
            str(tmp_path / "deal.json"),
            "--start-date",
            annual_date,
            "--end-date",
            annual_date,
        ]
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert "missing=['20260302']" in payload["error"]["message"]


def test_finalize_minute_coverage_materializes_before_strict_audit(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    annual_date = "20160104"
    deal_date = "20260608"
    partial_date = "20260709"
    materialize_calls = []
    audit_calls = []

    monkeypatch.setattr(
        a_share_minute_coverage,
        "load_open_trade_dates",
        lambda *args, **kwargs: [annual_date, deal_date, partial_date],
    )
    monkeypatch.setattr(
        a_share_minute_coverage,
        "load_annual_minbar_manifest",
        lambda *args: a_share_minute_coverage.AnnualMinbarCoverageResult(
            dates={annual_date},
            stats={
                annual_date: a_share_minute_coverage.ExpectedPartitionStats(
                    rows=1,
                    symbols=1,
                )
            },
            full_dates=set(),
            partial_session_dates={annual_date},
        ),
    )
    monkeypatch.setattr(
        a_share_minute_coverage,
        "discover_guan_deal_files",
        lambda *args: {
            annual_date: Path("deal_20160104.parquet"),
            deal_date: Path("deal_20260608.parquet"),
        },
    )
    deal_expected_stats = a_share_minute_coverage.ExpectedPartitionStats(
        rows=2,
        symbols=2,
        traded_symbols=2,
        vol_sum=200.0,
        amount_sum=2_000.0,
        unit_profile="guan_deal_price_cents_volume_shares",
    )
    monkeypatch.setattr(
        a_share_minute_coverage,
        "load_guan_deal_manifest",
        lambda *args: a_share_minute_coverage.GuanDealCoverageResult(
            dates={annual_date, deal_date},
            stats={annual_date: deal_expected_stats, deal_date: deal_expected_stats},
            override_dates={annual_date},
        ),
    )
    monkeypatch.setattr(
        a_share_minute_coverage,
        "discover_tushare_minute_batches",
        lambda *args: {
            annual_date: [Path("annual-reference.parquet")],
            deal_date: [Path("deal-reference.parquet")],
            partial_date: [Path("partial.parquet")],
        },
    )

    def fake_materialize(output_dir, **kwargs):
        materialize_calls.append((output_dir, kwargs))
        return {
            "status": "completed",
            "written_partial_dates": [partial_date],
            "protected_full_dates": [annual_date, deal_date],
            "protected_partial_session_dates": [],
            "protected_guan_dates": [annual_date, deal_date],
            "outside_calendar_batch_dates": [],
            "uncovered_dates": [],
            "actions": [],
        }

    def fake_audit(output_dir, **kwargs):
        audit_calls.append((output_dir, kwargs))
        return {
            "status": "passed",
            "quality_status": "passed",
            "coverage_status": "known_partial",
            "summary": {"calendar_dates": 3},
            "known_gaps": {"partial_top200_dates": [partial_date]},
            "failures": {},
        }

    monkeypatch.setattr(
        a_share_minute_coverage,
        "materialize_tushare_partial_dates",
        fake_materialize,
    )
    monkeypatch.setattr(a_share_minute_coverage, "audit_minute_coverage", fake_audit)
    monkeypatch.setattr(
        a_share_minute_coverage,
        "validate_overlap_audit",
        lambda path, **kwargs: json.loads(Path(path).read_text(encoding="utf-8")),
    )
    deal_manifest = tmp_path / "deal.json"
    annual_manifest = tmp_path / "annual.json"
    annual_manifest.write_text("annual", encoding="utf-8")
    deal_manifest.write_text("deal", encoding="utf-8")
    overlap_audit = tmp_path / "overlap.json"
    overlap_audit.write_text(
        json.dumps(
            {
                "status": "passed",
                "diagnostic_only": True,
                "mutation_performed": False,
                "summary": {"date_count": 2},
                "inputs": {"overlap_dates": [annual_date, deal_date]},
            }
        ),
        encoding="utf-8",
    )
    repair_audit = tmp_path / "repair.json"
    repair_audit.write_text("{}", encoding="utf-8")
    source_audit = tmp_path / "source-audit.json"
    source_audit.write_text("{}", encoding="utf-8")

    exit_code = cli.main(
        [
            "data",
            "finalize-a-share-minute-coverage",
            "--trade-cal",
            str(tmp_path / "trade-cal.parquet"),
            "--annual-manifest",
            str(annual_manifest),
            "--deal-manifest",
            str(deal_manifest),
            "--guan-deal-dir",
            str(tmp_path / "deal"),
            "--tushare-batch-dir",
            str(tmp_path / "batches"),
            "--out-dir",
            str(tmp_path / "output"),
            "--manifest",
            str(tmp_path / "coverage.json"),
            "--overlap-audit",
            str(overlap_audit),
            "--repair-audit",
            str(repair_audit),
            "--source-audit",
            str(source_audit),
        ]
    )

    assert exit_code == 0
    assert len(materialize_calls) == 1
    assert len(audit_calls) == 1
    materialize_options = materialize_calls[0][1]
    assert materialize_options["annual_dates"] == {annual_date}
    assert set(materialize_options["deal_dates"]) == {annual_date, deal_date}
    assert materialize_options["partial_session_annual_dates"] == {annual_date}
    assert materialize_options["deal_override_dates"] == {annual_date}
    assert materialize_options["expected_symbols"] == 200
    audit_options = audit_calls[0][1]
    assert audit_options["requirements"] is a_share_minute_coverage.PRODUCTION_COVERAGE_REQUIREMENTS
    assert set(audit_options["deal_dates"]) == {annual_date, deal_date}
    assert audit_options["partial_session_annual_dates"] == {annual_date}
    assert audit_options["deal_override_dates"] == {annual_date}
    assert audit_options["expected_partition_stats"][deal_date].rows == 2
    assert audit_options["expected_partition_stats"][deal_date].symbols == 2
    assert audit_options["expected_partition_stats"][deal_date].vol_sum == 200.0
    assert audit_options["input_lineage"]["deal_expected_stats"] == "deal_build_manifest"
    assert audit_options["input_lineage"]["annual_manifest_sha256"]
    assert audit_options["input_lineage"]["deal_manifest_sha256"]
    assert audit_options["input_lineage"]["overlap_audit"] == str(overlap_audit)
    assert audit_options["input_lineage"]["overlap_audit_date_count"] == 2
    assert audit_options["input_lineage"]["reasonix_repair_audit"] == str(repair_audit)
    assert audit_options["input_lineage"]["source_audits"] == [str(source_audit)]
    assert audit_options["input_lineage"]["source_audit_receipts"] == [
        {
            "path": str(source_audit),
            "sha256": cli_data._sha256_file(source_audit),
            "schema_version": None,
            "status": None,
            "verification_status": None,
        }
    ]
    assert audit_options["input_lineage"]["original_annual_deal_overlap_dates"] == [annual_date]
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "passed"
    assert payload["materialization"] == {
        "written_partial_dates": [partial_date],
        "protected_full_date_count": 2,
        "protected_partial_session_date_count": 0,
        "protected_guan_date_count": 2,
    }


def test_finalize_minute_coverage_requires_complete_full_day_argument_group(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        [
            "data",
            "finalize-a-share-minute-coverage",
            "--trade-cal",
            str(tmp_path / "trade-cal.parquet"),
            "--annual-manifest",
            str(tmp_path / "annual.json"),
            "--deal-manifest",
            str(tmp_path / "deal.json"),
            "--guan-deal-dir",
            str(tmp_path / "deal"),
            "--tushare-batch-dir",
            str(tmp_path / "batches"),
            "--tushare-full-day-dir",
            str(tmp_path / "full-days"),
            "--out-dir",
            str(tmp_path / "output"),
            "--manifest",
            str(tmp_path / "coverage.json"),
            "--overlap-audit",
            str(tmp_path / "overlap.json"),
        ]
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert "must be supplied together" in payload["error"]["message"]


@pytest.mark.parametrize("years", ["2018-2016", "2015", "2016,2016", "2016,"])
def test_build_guan_annual_minutes_rejects_invalid_year_expression(
    years: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "data",
                "build-guan-annual-minutes",
                "--minbar-dir",
                "/tmp/minute",
                "--years",
                years,
                "--out-dir",
                "/tmp/out",
                "--manifest",
                "/tmp/manifest.json",
                "--staging-root",
                "/tmp/staging",
            ]
        )

    assert exc.value.code == 2
    assert "argument --years" in capsys.readouterr().err
