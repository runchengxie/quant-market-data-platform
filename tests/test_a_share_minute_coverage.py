from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from market_data_platform.providers import tushare_a_share_mins as mins
from market_data_platform.providers.a_share_minute_coverage import (
    ANNUAL_FULL_SH_SZ,
    DEAL_FULL_SH_SZ,
    GUAN_PARTIAL_SESSION,
    MISSING,
    PRODUCTION_COVERAGE_REQUIREMENTS,
    TUSHARE_FULL_A_SHARE,
    TUSHARE_FULL_DAY_PLAN_SCHEMA_VERSION,
    TUSHARE_FULL_DAY_RECEIPT_SCHEMA_VERSION,
    TUSHARE_PARTIAL_TOP200,
    CoverageRequirements,
    ExpectedPartitionStats,
    MarketReferenceStats,
    audit_minute_coverage,
    classify_minute_coverage,
    discover_guan_deal_files,
    discover_tushare_full_day_partitions,
    discover_tushare_minute_batches,
    load_annual_minbar_manifest,
    load_guan_deal_manifest,
    load_open_trade_dates,
    load_tushare_full_day_plan,
    materialize_tushare_full_days,
    materialize_tushare_partial_dates,
    validate_overlap_audit,
)
from market_data_platform.providers.a_share_minute_fusion import (
    CANONICAL_MINUTE_SCHEMA,
    write_canonical_minute_partition,
)
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy


def _row(
    date: str,
    *,
    ts_code: str = "000001.SZ",
    clock: str = "09:31:00",
    close: float = 10.1,
) -> dict[str, object]:
    return {
        "ts_code": ts_code,
        "trade_time": f"{date[:4]}-{date[4:6]}-{date[6:]} {clock}",
        "open": 10.0,
        "close": close,
        "high": max(10.2, close),
        "low": 9.9,
        "vol": 100.0,
        "amount": 1_000.0,
    }


def _write_partition(root: Path, date: str, rows: list[dict[str, object]]) -> Path:
    path = root / f"trade_date={date}" / "part-00000.parquet"
    write_canonical_minute_partition(pd.DataFrame(rows), path)
    return path


def _write_batch(
    root: Path,
    date: str,
    *,
    batch: int = 1,
    symbols: tuple[str, ...] = ("000001.SZ", "600000.SH"),
    clocks: tuple[str, ...] = ("09:30:00", "09:31:00"),
) -> Path:
    rows = [
        {**_row(date, ts_code=symbol, clock=clock), "trade_date": date}
        for symbol in symbols
        for clock in clocks
    ]
    path = root / f"minute_{date}_batch{batch:03d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def _write_full_mirror_day(root: Path, date: str, symbols: tuple[str, ...]) -> Path:
    times = [
        *pd.date_range(f"{date} 09:30:00", periods=121, freq="min"),
        *pd.date_range(f"{date} 13:01:00", periods=120, freq="min"),
    ]
    frame = pd.DataFrame(
        [
            {
                **_row(date, ts_code=symbol, clock=trade_time.strftime("%H:%M:%S")),
                "trade_time": trade_time,
            }
            for symbol in symbols
            for trade_time in times
        ]
    )
    part_dir = root / f"trade_date={date}"
    mins._atomic_write_partition(frame, part_dir, trade_date=date)
    expected_symbols = set(symbols)
    universe_hash = mins._universe_hash(expected_symbols, rule=mins.UNIVERSE_RULE)
    mins._write_completeness(
        mins._MinuteProgress(
            part_dir=part_dir,
            trade_date=date,
            freq=mins.DEFAULT_FREQ,
            universe_hash=universe_hash,
            universe_rule=mins.UNIVERSE_RULE,
            universe_source="test-full-daily-universe",
            expected_symbols=frozenset(expected_symbols),
            request_policy=TushareRequestPolicy(),
        ),
        completed_symbols=expected_symbols,
        partition=mins._partition_state(part_dir, trade_date=date),
        status="complete",
    )
    return part_dir


def _small_requirements() -> CoverageRequirements:
    return CoverageRequirements(
        expected_trade_dates=4,
        expected_annual_full_sh_sz_dates=1,
        expected_deal_full_sh_sz_dates=1,
        expected_guan_partial_session_dates=1,
        expected_partial_top200_dates=1,
        require_full_source_stats=True,
    )


def test_classification_uses_date_level_priority_and_marks_overlaps_audit_only() -> None:
    dates = [
        "20260105",
        "20260302",
        "20260424",
        "20260608",
        "20260709",
        "20260710",
    ]

    plan = classify_minute_coverage(
        dates,
        annual_dates=["20260105", "20260302", "20260424"],
        deal_dates=["20260302", "20260424", "20260608"],
        tushare_dates=[
            "20260105",
            "20260302",
            "20260424",
            "20260608",
            "20260709",
        ],
        partial_session_annual_dates=["20260302", "20260424"],
        deal_override_dates=["20260302"],
        partial_symbols=2,
        partial_bars_per_symbol=2,
    )

    by_date = {item.trade_date: item for item in plan}
    assert by_date["20260105"].tier == ANNUAL_FULL_SH_SZ
    assert by_date["20260105"].audit_only_sources == ("tushare_top200",)
    assert by_date["20260302"].tier == DEAL_FULL_SH_SZ
    assert by_date["20260302"].audit_only_sources == (
        "guan_annual_minbar",
        "tushare_top200",
    )
    assert by_date["20260424"].tier == GUAN_PARTIAL_SESSION
    assert by_date["20260424"].canonical_source == "guan_annual_minbar"
    assert by_date["20260424"].is_full_sh_sz is False
    assert by_date["20260424"].has_guan_source is True
    assert by_date["20260424"].audit_only_sources == (
        "guan_deal",
        "tushare_top200",
    )
    assert by_date["20260608"].tier == DEAL_FULL_SH_SZ
    assert by_date["20260608"].audit_only_sources == ("tushare_top200",)
    assert by_date["20260709"].tier == TUSHARE_PARTIAL_TOP200
    assert by_date["20260709"].expected_rows == 4
    assert by_date["20260710"].tier == MISSING


def test_point_in_time_full_a_share_scope_counts_pre_bse_sh_sz_days() -> None:
    pre_bse = "20201116"
    post_bse = "20260427"

    plan = classify_minute_coverage(
        [pre_bse, post_bse],
        annual_dates=[pre_bse],
        deal_dates=[],
        tushare_dates=[post_bse],
        tushare_full_dates=[post_bse],
    )

    by_date = {item.trade_date: item for item in plan}
    assert by_date[pre_bse].tier == ANNUAL_FULL_SH_SZ
    assert by_date[pre_bse].market_scope == "SH_SZ"
    assert by_date[pre_bse].is_full_a_share is True
    assert by_date[post_bse].tier == TUSHARE_FULL_A_SHARE
    assert by_date[post_bse].market_scope == "SH_SZ_BJ"
    assert by_date[post_bse].is_full_a_share is True


def test_audit_counts_pre_bse_sh_sz_as_full_a_without_tushare_components(
    tmp_path: Path,
) -> None:
    trade_date = "20201116"
    output_root = tmp_path / "minute"
    rows = [_row(trade_date), _row(trade_date, clock="15:00:00")]
    _write_partition(output_root, trade_date, rows)

    result = audit_minute_coverage(
        output_root,
        trade_dates=[trade_date],
        annual_dates=[trade_date],
        deal_dates=[],
        tushare_dates=[],
        expected_partition_stats={
            trade_date: ExpectedPartitionStats(
                rows=2,
                symbols=1,
                traded_symbols=1,
                vol_sum=200.0,
                amount_sum=2_000.0,
                unit_profile="guan_shares_cents_times_shares",
            )
        },
        requirements=CoverageRequirements(
            expected_trade_dates=1,
            expected_annual_full_sh_sz_dates=1,
            expected_deal_full_sh_sz_dates=0,
            expected_guan_partial_session_dates=0,
            expected_partial_top200_dates=0,
            require_full_source_stats=True,
        ),
    )

    assert result["status"] == "passed"
    assert result["coverage_status"] == "full_a_share"
    assert result["summary"]["full_a_share_dates"] == 1
    assert result["policy"]["tushare_components_fixed_241"] is False
    assert result["known_gaps"]["bj_market"] == {
        "status": "covered_point_in_time",
        "affected_trade_dates": 0,
    }


def test_production_requirements_record_honest_session_tiers() -> None:
    assert PRODUCTION_COVERAGE_REQUIREMENTS == CoverageRequirements(
        expected_trade_dates=2_556,
        expected_annual_full_sh_sz_dates=2_430,
        expected_deal_full_sh_sz_dates=37,
        expected_guan_partial_session_dates=58,
        expected_partial_top200_dates=28,
        expected_accepted_zero_volume_nonzero_amount_rows=136_246,
        expected_accepted_positive_volume_zero_amount_rows=1,
        require_full_source_stats=True,
    )


def test_materialize_tushare_writes_only_dates_without_any_guan_tier(tmp_path: Path) -> None:
    guan_partial_date = "20260423"
    full_date = "20260424"
    tushare_partial_date = "20260427"
    batch_root = tmp_path / "batches"
    guan_partial_batch = _write_batch(batch_root, guan_partial_date)
    full_batch = _write_batch(batch_root, full_date)
    tushare_partial_batch = _write_batch(batch_root, tushare_partial_date)
    output_root = tmp_path / "output"
    full_path = _write_partition(output_root, full_date, [_row(full_date, close=12.0)])
    guan_partial_path = _write_partition(
        output_root,
        guan_partial_date,
        [_row(guan_partial_date, clock="14:57:00", close=13.0)],
    )
    full_before = full_path.read_bytes()
    guan_partial_before = guan_partial_path.read_bytes()

    result = materialize_tushare_partial_dates(
        output_root,
        trade_dates=[guan_partial_date, full_date, tushare_partial_date],
        annual_dates=[full_date],
        deal_dates=[],
        tushare_batches={
            guan_partial_date: [guan_partial_batch],
            full_date: [full_batch],
            tushare_partial_date: [tushare_partial_batch],
        },
        partial_session_annual_dates=[guan_partial_date],
        expected_symbols=2,
        expected_bars_per_symbol=2,
    )

    assert full_path.read_bytes() == full_before
    assert guan_partial_path.read_bytes() == guan_partial_before
    assert result["protected_full_dates"] == [full_date]
    assert result["protected_partial_session_dates"] == [guan_partial_date]
    assert result["protected_guan_dates"] == [guan_partial_date, full_date]
    assert result["written_partial_dates"] == [tushare_partial_date]
    assert [action["status"] for action in result["actions"]] == [
        "audit_only",
        "audit_only",
        "written_partial",
    ]
    partial_path = output_root / f"trade_date={tushare_partial_date}" / "part-00000.parquet"
    frame = pd.read_parquet(partial_path)
    assert len(frame) == 4
    assert frame["ts_code"].nunique() == 2


def test_materialize_tushare_preserves_and_records_source_vwap_diagnostic(
    tmp_path: Path,
) -> None:
    date = "20260427"
    batch = _write_batch(tmp_path / "batches", date)
    raw = pd.read_parquet(batch)
    raw.loc[0, ["open", "close", "high", "low"]] = 3.76
    raw.loc[0, "vol"] = 1.0
    raw.loc[0, "amount"] = 4.0
    raw.to_parquet(batch, index=False)
    output_root = tmp_path / "output"

    result = materialize_tushare_partial_dates(
        output_root,
        trade_dates=[date],
        annual_dates=[],
        deal_dates=[],
        tushare_batches={date: [batch]},
        expected_symbols=2,
        expected_bars_per_symbol=2,
    )

    action = result["actions"][0]
    assert action["status"] == "written_partial"
    assert action["accepted_diagnostics"] == {
        "vwap_beyond_source_guard_rows": 1,
        "vwap_outside_ohlc_rows": 1,
    }
    assert result["diagnostics"] == {
        "policy": "preserve_source_values_no_clipping",
        "tushare_price_flow_policy": {
            "source_values": "preserve_no_clipping",
            "ohlc_vwap_diagnostic": {
                "issue": "vwap_outside_ohlc_rows",
                "absolute_tolerance_cny": 0.01,
                "relative_to_max_abs_high_low": 1e-6,
                "disposition": "accepted_diagnostic",
            },
            "two_percent_vwap_diagnostic": {
                "issue": "vwap_beyond_source_guard_rows",
                "relative_tolerance": 0.02,
                "disposition": "accepted_diagnostic",
            },
            "hard_notional_guard": {
                "issue": "notional_beyond_hard_price_guard_rows",
                "relative_tolerance": 0.1,
                "absolute_tolerance_cny": 1.0,
                "lower_bound": "amount < low * vol * 0.90 - 1 CNY",
                "upper_bound": "amount > high * vol * 1.10 + 1 CNY",
                "disposition": "fatal",
            },
            "positive_volume_zero_amount": ("accepted_diagnostic_when_hard_notional_guard_passes"),
            "zero_volume_nonzero_amount": "fatal",
        },
        "accepted_diagnostics": {
            "vwap_beyond_source_guard_rows": 1,
            "vwap_outside_ohlc_rows": 1,
        },
        "dates_affected": [date],
        "by_date": {
            date: {
                "vwap_beyond_source_guard_rows": 1,
                "vwap_outside_ohlc_rows": 1,
            }
        },
        "hard_notional_guard": {
            "issue": "notional_beyond_hard_price_guard_rows",
            "rows": 0,
            "disposition": "fatal",
        },
    }
    written = pd.read_parquet(output_root / f"trade_date={date}" / "part-00000.parquet")
    assert written.loc[written["trade_time"].eq(raw.loc[0, "trade_time"]), "amount"].iloc[0] == 4.0


def test_materialize_tushare_rejects_large_notional_beyond_hard_guard(
    tmp_path: Path,
) -> None:
    date = "20260427"
    batch = _write_batch(tmp_path / "batches", date)
    raw = pd.read_parquet(batch)
    raw.loc[0, ["open", "close", "high", "low"]] = 3.76
    raw.loc[0, "vol"] = 1_000.0
    raw.loc[0, "amount"] = 4_200.0
    raw.to_parquet(batch, index=False)

    with pytest.raises(ValueError, match="notional_beyond_hard_price_guard_rows"):
        materialize_tushare_partial_dates(
            tmp_path / "output",
            trade_dates=[date],
            annual_dates=[],
            deal_dates=[],
            tushare_batches={date: [batch]},
            expected_symbols=2,
            expected_bars_per_symbol=2,
        )


def test_materialize_tushare_rejects_extreme_vwap_unit_scale(tmp_path: Path) -> None:
    date = "20260427"
    batch = _write_batch(tmp_path / "batches", date)
    raw = pd.read_parquet(batch)
    raw.loc[0, "amount"] = 100.0  # VWAP 1.0 is below the fatal 0.5 * low guard.
    raw.to_parquet(batch, index=False)

    with pytest.raises(ValueError, match="extreme_vwap_unit_scale_rows"):
        materialize_tushare_partial_dates(
            tmp_path / "output",
            trade_dates=[date],
            annual_dates=[],
            deal_dates=[],
            tushare_batches={date: [batch]},
            expected_symbols=2,
            expected_bars_per_symbol=2,
        )


def test_materialize_tushare_full_days_replaces_only_whole_eligible_dates(
    tmp_path: Path,
) -> None:
    guan_partial_date = "20260423"
    top200_date = "20260427"
    missing_date = "20260710"
    symbols = ("000001.SZ", "600000.SH", "920001.BJ")
    source_root = tmp_path / "full-mirror"
    sources = {
        date: _write_full_mirror_day(source_root, date, symbols)
        for date in (guan_partial_date, top200_date, missing_date)
    }
    output_root = tmp_path / "version"
    _write_partition(
        output_root,
        guan_partial_date,
        [_row(guan_partial_date, clock="14:57:00", close=33.0)],
    )
    _write_partition(output_root, top200_date, [_row(top200_date, close=44.0)])
    receipt_path = tmp_path / "full-day-receipt.json"

    result = materialize_tushare_full_days(
        output_root,
        trade_dates=[guan_partial_date, top200_date, missing_date],
        annual_dates=[],
        deal_dates=[],
        tushare_dates=[top200_date],
        tushare_full_partitions=sources,
        replacement_dates=[guan_partial_date, top200_date, missing_date],
        phase="production",
        plan_sha256="a" * 64,
        partial_session_annual_dates=[guan_partial_date],
        manifest_path=receipt_path,
    )

    assert result["status"] == "passed"
    assert result["schema_version"] == TUSHARE_FULL_DAY_RECEIPT_SCHEMA_VERSION
    assert result["plan_sha256"] == "a" * 64
    assert result["policy"]["intraday_source_merge"] == "forbidden"
    assert (
        result["policy"]["tushare_price_flow_validation"]["two_percent_vwap_diagnostic"][
            "disposition"
        ]
        == "accepted_diagnostic"
    )
    assert result["policy"]["tushare_price_flow_validation"]["hard_notional_guard"] == {
        "issue": "notional_beyond_hard_price_guard_rows",
        "relative_tolerance": 0.1,
        "absolute_tolerance_cny": 1.0,
        "lower_bound": "amount < low * vol * 0.90 - 1 CNY",
        "upper_bound": "amount > high * vol * 1.10 + 1 CNY",
        "disposition": "fatal",
    }
    assert result["summary"]["top200_replacement_dates"] == 1
    assert result["summary"]["guan_partial_session_replacement_dates"] == 1
    assert result["summary"]["missing_replacement_dates"] == 1
    assert result["policy"]["eligible_input_tiers"] == [
        "guan_partial_session",
        "missing",
        "tushare_partial_top200",
    ]
    assert [action["replaces_tier"] for action in result["actions"]] == [
        GUAN_PARTIAL_SESSION,
        TUSHARE_PARTIAL_TOP200,
        MISSING,
    ]
    assert [action["status"] for action in result["actions"]] == [
        "written_full_day",
        "written_full_day",
        "written_full_day",
    ]
    for date in (guan_partial_date, top200_date, missing_date):
        promoted = pd.read_parquet(output_root / f"trade_date={date}" / "part-00000.parquet")
        assert len(promoted) == len(symbols) * 241
        assert set(promoted["ts_code"]) == set(symbols)
        assert promoted.groupby("ts_code").size().eq(241).all()
        assert promoted["trade_time"].min().strftime("%H:%M:%S") == "09:30:00"
        assert promoted["trade_time"].max().strftime("%H:%M:%S") == "15:00:00"
        assert not promoted["close"].isin([33.0, 44.0]).any()
        source_receipt = result["source_receipts"][date]
        assert (
            source_receipt["tushare_price_flow_validation"]
            == result["policy"]["tushare_price_flow_validation"]
        )
        assert (
            source_receipt["price_flow_diagnostics"]["notional_beyond_hard_price_guard_rows"] == 0
        )

    expected_stats = result["expected_partition_stats"]
    coverage = audit_minute_coverage(
        output_root,
        trade_dates=[guan_partial_date, top200_date, missing_date],
        annual_dates=[],
        deal_dates=[],
        tushare_dates=[top200_date],
        tushare_full_dates=[guan_partial_date, top200_date, missing_date],
        partial_session_annual_dates=[guan_partial_date],
        expected_partition_stats=expected_stats,
        requirements=CoverageRequirements(
            expected_trade_dates=3,
            expected_annual_full_sh_sz_dates=0,
            expected_deal_full_sh_sz_dates=0,
            expected_tushare_full_a_share_dates=3,
            expected_guan_partial_session_dates=0,
            expected_partial_top200_dates=0,
            require_full_source_stats=True,
        ),
    )
    assert coverage["status"] == "passed", (
        coverage["failures"],
        [(item["date"], item["fatal_issues"]) for item in coverage["daily"]],
    )
    assert coverage["coverage_status"] == "full_a_share"
    assert coverage["summary"]["tushare_full_a_share_dates"] == 3
    assert coverage["summary"]["full_sh_sz_dates"] == 3
    assert coverage["summary"]["full_a_share_dates"] == 3
    assert {item["tier"] for item in coverage["daily"]} == {TUSHARE_FULL_A_SHARE}
    assert {item["market_scope"] for item in coverage["daily"]} == {"SH_SZ_BJ"}


def test_materialize_tushare_full_days_validates_all_sources_before_mutating(
    tmp_path: Path,
) -> None:
    first = "20260423"
    second = "20260424"
    source_root = tmp_path / "full-mirror"
    sources = {
        date: _write_full_mirror_day(source_root, date, ("000001.SZ", "920001.BJ"))
        for date in (first, second)
    }
    corrupt = sources[second] / "part-00000.parquet"
    changed = pd.read_parquet(corrupt)
    changed.loc[0, "close"] = 12.0
    changed.to_parquet(corrupt, index=False)
    output_root = tmp_path / "version"
    first_target = _write_partition(
        output_root,
        first,
        [_row(first, clock="14:57:00", close=33.0)],
    )
    first_before = first_target.read_bytes()

    with pytest.raises(ValueError, match="changed after its sidecar"):
        materialize_tushare_full_days(
            output_root,
            trade_dates=[first, second],
            annual_dates=[],
            deal_dates=[],
            tushare_dates=[],
            tushare_full_partitions=sources,
            replacement_dates=[first, second],
            phase="pilot",
            partial_session_annual_dates=[first, second],
        )

    assert first_target.read_bytes() == first_before


def test_materialize_tushare_full_days_rejects_complete_guan_target(
    tmp_path: Path,
) -> None:
    date = "20260424"
    source = _write_full_mirror_day(
        tmp_path / "full-mirror",
        date,
        ("000001.SZ", "920001.BJ"),
    )

    with pytest.raises(ValueError, match="only missing, top200, or Guan partial-session"):
        materialize_tushare_full_days(
            tmp_path / "version",
            trade_dates=[date],
            annual_dates=[date],
            deal_dates=[],
            tushare_dates=[],
            tushare_full_partitions={date: source},
            replacement_dates=[date],
            phase="pilot",
        )


def test_tushare_full_day_plan_is_explicit_and_discovers_hive_partitions(
    tmp_path: Path,
) -> None:
    dates = ["20260423", "20260427"]
    source_root = tmp_path / "full-mirror"
    for date in dates:
        (source_root / f"trade_date={date}").mkdir(parents=True)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": TUSHARE_FULL_DAY_PLAN_SCHEMA_VERSION,
                "phase": "pilot",
                "dates": dates,
            }
        ),
        encoding="utf-8",
    )

    plan = load_tushare_full_day_plan(plan_path)

    assert plan.phase == "pilot"
    assert plan.dates == tuple(dates)
    assert plan.sha256 == hashlib.sha256(plan_path.read_bytes()).hexdigest()
    assert discover_tushare_full_day_partitions(source_root) == {
        date: source_root / f"trade_date={date}" for date in dates
    }


def test_overlap_receipt_is_bound_to_current_files(tmp_path: Path) -> None:
    date = "20260424"
    output_root = tmp_path / "output"
    canonical = _write_partition(output_root, date, [_row(date)])
    batch_root = tmp_path / "batches"
    batch = _write_batch(batch_root, date)

    def receipt(path: Path) -> dict[str, object]:
        stat = path.stat()
        return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    audit_path = tmp_path / "overlap.json"
    audit_path.write_text(
        json.dumps(
            {
                "schema_version": "a_share.minute_overlap_audit.v1",
                "status": "passed",
                "diagnostic_only": True,
                "mutation_performed": False,
                "inputs": {
                    "canonical_dir": str(output_root),
                    "tushare_batch_dir": str(batch_root),
                    "overlap_dates": [date],
                },
                "summary": {"date_count": 1},
                "daily": [
                    {
                        "trade_date": date,
                        "inputs": {
                            "canonical": receipt(canonical),
                            "tushare_batches": [receipt(batch)],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = validate_overlap_audit(
        audit_path,
        output_dir=output_root,
        tushare_batch_dir=batch_root,
        expected_overlap_dates=[date],
        tushare_batches={date: [batch]},
    )
    assert payload["status"] == "passed"

    canonical.touch()
    with pytest.raises(ValueError, match="changed after auditing"):
        validate_overlap_audit(
            audit_path,
            output_dir=output_root,
            tushare_batch_dir=batch_root,
            expected_overlap_dates=[date],
            tushare_batches={date: [batch]},
        )


def test_audit_passes_exact_tiers_and_writes_manifest(tmp_path: Path) -> None:
    guan_partial_date = "20260423"
    annual_date = "20260424"
    deal_date = "20260608"
    partial_date = "20260709"
    output_root = tmp_path / "output"
    _write_partition(
        output_root,
        annual_date,
        [_row(annual_date), _row(annual_date, clock="15:00:00")],
    )
    _write_partition(
        output_root,
        guan_partial_date,
        [_row(guan_partial_date), _row(guan_partial_date, clock="14:57:00")],
    )
    _write_partition(
        output_root,
        deal_date,
        [
            _row(deal_date, ts_code=symbol, clock=clock)
            for symbol in ("000001.SZ", "600000.SH")
            for clock in ("09:30:00", "15:00:00")
        ],
    )
    partial_rows = [
        _row(partial_date, ts_code=symbol, clock=clock)
        for symbol in ("000001.SZ", "600000.SH")
        for clock in ("09:30:00", "09:31:00")
    ]
    _write_partition(output_root, partial_date, partial_rows)
    manifest_path = tmp_path / "metadata" / "coverage.json"

    payload = audit_minute_coverage(
        output_root,
        trade_dates=[guan_partial_date, annual_date, deal_date, partial_date],
        annual_dates=[annual_date],
        deal_dates=[deal_date],
        tushare_dates=[guan_partial_date, annual_date, deal_date, partial_date],
        partial_session_annual_dates=[guan_partial_date],
        expected_partition_stats={
            annual_date: ExpectedPartitionStats(
                rows=2,
                symbols=1,
                traded_symbols=1,
                vol_sum=200.0,
                amount_sum=2_000.0,
                unit_profile="lots_x100_amount_yuan",
            ),
            guan_partial_date: ExpectedPartitionStats(
                rows=2,
                symbols=1,
                traded_symbols=1,
                vol_sum=200.0,
                amount_sum=2_000.0,
                unit_profile="guan_shares_cents_times_shares",
                time_max=(
                    f"{guan_partial_date[:4]}-{guan_partial_date[4:6]}-"
                    f"{guan_partial_date[6:]} 14:57:00"
                ),
            ),
            deal_date: ExpectedPartitionStats(
                rows=4,
                symbols=2,
                traded_symbols=2,
                vol_sum=400.0,
                amount_sum=4_000.0,
                unit_profile="price_cents_volume_shares",
            ),
        },
        market_reference={
            annual_date: MarketReferenceStats(sh_sz_symbols=1, bj_symbols=1),
            deal_date: MarketReferenceStats(sh_sz_symbols=2, bj_symbols=1),
        },
        requirements=_small_requirements(),
        partial_symbols=2,
        partial_bars_per_symbol=2,
        audit_workers=2,
        input_lineage={"trade_cal": "/data/trade_cal.parquet"},
        manifest_path=manifest_path,
    )

    assert payload["status"] == "passed"
    assert payload["coverage_status"] == "known_partial"
    assert payload["summary"] == {
        "calendar_dates": 4,
        "date_min": guan_partial_date,
        "date_max": partial_date,
        "partition_files": 4,
        "partition_dirs": 4,
        "annual_full_sh_sz_dates": 1,
        "deal_full_sh_sz_dates": 1,
        "guan_partial_session_dates": 1,
        "full_sh_sz_dates": 2,
        "full_a_share_dates": 0,
        "tushare_partial_top200_dates": 1,
        "missing_source_dates": 0,
        "missing_output_dates": 0,
        "invalid_output_dates": 0,
        "orphan_output_dates": 0,
        "accepted_vwap_diagnostic_rows": 0,
        "accepted_vwap_diagnostic_dates": 0,
        "accepted_vwap_source_guard_rows": 0,
        "accepted_vwap_extreme_guard_rows": 0,
        "accepted_notional_hard_guard_rows": 0,
        "fatal_notional_hard_guard_rows": 0,
        "accepted_zero_volume_nonzero_amount_rows": 0,
        "accepted_positive_volume_zero_amount_rows": 0,
        "accepted_guan_zero_volume_nonzero_amount_rows": 0,
        "accepted_tushare_zero_volume_nonzero_amount_rows": 0,
        "accepted_guan_positive_volume_zero_amount_rows": 0,
        "accepted_tushare_positive_volume_zero_amount_rows": 0,
    }
    assert payload["failures"] == {
        "missing_source_dates": [],
        "missing_output_dates": [],
        "invalid_output_dates": [],
        "orphan_output_dates": [],
        "source_orphan_dates": {},
        "unexpected_partition_files": [],
        "missing_full_source_stats": [],
        "requirement_mismatches": [],
    }
    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert written["policy"]["overlap_policy"] == "audit_only_no_overwrite"
    assert written["known_gaps"]["bj_market"]["affected_trade_dates"] == 4
    assert written["known_gaps"]["guan_partial_session"] == {
        "status": "session_ends_at_14_57",
        "affected_trade_dates": 1,
        "dates": [guan_partial_date],
    }
    annual_record = next(row for row in written["daily"] if row["date"] == annual_date)
    assert annual_record["market_reference"]["bj_symbols"] == 1
    annual_path = output_root / f"trade_date={annual_date}" / "part-00000.parquet"
    assert annual_record["content_sha256"] == hashlib.sha256(annual_path.read_bytes()).hexdigest()
    assert written["inputs"] == {
        "annual_date_count": 2,
        "annual_full_date_count": 1,
        "annual_partial_session_date_count": 1,
        "deal_date_count": 1,
        "deal_override_date_count": 0,
        "tushare_date_count": 4,
        "annual_deal_overlap_dates": 0,
        "annual_tushare_audit_dates": 2,
        "deal_tushare_audit_dates": 1,
        "lineage": {"trade_cal": "/data/trade_cal.parquet"},
    }
    assert CANONICAL_MINUTE_SCHEMA.equals(
        pq.ParquetFile(
            output_root / f"trade_date={annual_date}" / "part-00000.parquet"
        ).schema_arrow
    )


def test_audit_accepts_annual_vwap_diagnostic_but_keeps_deal_strict(
    tmp_path: Path,
) -> None:
    annual_date = "20260424"
    deal_date = "20260608"
    output_root = tmp_path / "output"
    annual_rows = [
        _row(annual_date, clock="09:31:00"),
        _row(annual_date, clock="15:00:00"),
    ]
    annual_rows[0]["amount"] = 980.0
    deal_rows = [
        _row(deal_date, clock="09:30:00"),
        _row(deal_date, clock="15:00:00"),
    ]
    deal_rows[0]["amount"] = 980.0
    _write_partition(output_root, annual_date, annual_rows)
    _write_partition(output_root, deal_date, deal_rows)

    payload = audit_minute_coverage(
        output_root,
        trade_dates=[annual_date, deal_date],
        annual_dates=[annual_date],
        deal_dates=[deal_date],
        tushare_dates=[],
        expected_partition_stats={
            annual_date: ExpectedPartitionStats(
                rows=2,
                symbols=1,
                traded_symbols=1,
                vol_sum=200.0,
                amount_sum=1_980.0,
                unit_profile="guan_lots_yuan",
            ),
            deal_date: ExpectedPartitionStats(
                rows=2,
                symbols=1,
                traded_symbols=1,
                vol_sum=200.0,
                amount_sum=1_980.0,
            ),
        },
        requirements=CoverageRequirements(
            expected_trade_dates=2,
            expected_annual_full_sh_sz_dates=1,
            expected_deal_full_sh_sz_dates=1,
            expected_partial_top200_dates=0,
            require_full_source_stats=True,
        ),
    )

    records = {record["date"]: record for record in payload["daily"]}
    assert records[annual_date]["valid"] is True
    assert records[annual_date]["accepted_diagnostics"] == {"vwap_outside_ohlc_rows": 1}
    assert records[annual_date]["fatal_issues"] == {}
    assert records[deal_date]["valid"] is False
    assert records[deal_date]["accepted_diagnostics"] == {}
    assert records[deal_date]["fatal_issues"] == {"vwap_outside_ohlc_rows": 1}
    assert payload["diagnostics"]["vwap_outside_ohlc"]["by_tier"] == {
        "annual_full_sh_sz": {
            "rows": 1,
            "accepted_rows": 1,
            "fatal_rows": 0,
            "dates_affected": 1,
        },
        "deal_full_sh_sz": {
            "rows": 1,
            "accepted_rows": 0,
            "fatal_rows": 1,
            "dates_affected": 1,
        },
    }
    assert payload["summary"]["accepted_vwap_diagnostic_rows"] == 1
    assert payload["summary"]["accepted_vwap_diagnostic_dates"] == 1


def test_audit_records_extreme_annual_vwap_as_incomparable_price_flow_basis(
    tmp_path: Path,
) -> None:
    date = "20260424"
    rows = [_row(date, clock="09:31:00"), _row(date, clock="15:00:00")]
    rows[0]["amount"] = 100.0
    output_root = tmp_path / "output"
    _write_partition(output_root, date, rows)

    payload = audit_minute_coverage(
        output_root,
        trade_dates=[date],
        annual_dates=[date],
        deal_dates=[],
        tushare_dates=[],
        expected_partition_stats={
            date: ExpectedPartitionStats(
                rows=2,
                symbols=1,
                traded_symbols=1,
                vol_sum=200.0,
                amount_sum=1_100.0,
                unit_profile="guan_lots_yuan",
            )
        },
        requirements=CoverageRequirements(
            expected_trade_dates=1,
            expected_annual_full_sh_sz_dates=1,
            expected_deal_full_sh_sz_dates=0,
            expected_partial_top200_dates=0,
            require_full_source_stats=True,
        ),
    )

    record = payload["daily"][0]
    assert record["accepted_diagnostics"] == {
        "extreme_vwap_unit_scale_rows": 1,
        "notional_beyond_hard_price_guard_rows": 1,
        "vwap_beyond_source_guard_rows": 1,
        "vwap_outside_ohlc_rows": 1,
    }
    assert record["fatal_issues"] == {}
    assert payload["diagnostics"]["vwap_extreme_guard"]["accepted_rows"] == 1
    assert payload["status"] == "passed"


def test_audit_accepts_tushare_vwap_deviation_beyond_two_percent(tmp_path: Path) -> None:
    date = "20260424"
    rows = [_row(date, clock="09:31:00"), _row(date, clock="15:00:00")]
    rows[0]["amount"] = 960.0  # VWAP 9.6 is more than 2% below low 9.9.
    output_root = tmp_path / "output"
    _write_partition(output_root, date, rows)

    payload = audit_minute_coverage(
        output_root,
        trade_dates=[date],
        annual_dates=[],
        deal_dates=[],
        tushare_dates=[date],
        requirements=CoverageRequirements(
            expected_trade_dates=1,
            expected_annual_full_sh_sz_dates=0,
            expected_deal_full_sh_sz_dates=0,
            expected_partial_top200_dates=1,
        ),
        partial_symbols=1,
        partial_bars_per_symbol=2,
    )

    record = payload["daily"][0]
    assert record["accepted_diagnostics"] == {
        "vwap_beyond_source_guard_rows": 1,
        "vwap_outside_ohlc_rows": 1,
    }
    assert record["fatal_issues"] == {}
    assert payload["summary"]["accepted_vwap_source_guard_rows"] == 1
    assert payload["status"] == "passed"


def test_audit_rejects_tushare_large_notional_beyond_hard_guard(tmp_path: Path) -> None:
    date = "20260424"
    rows = [_row(date, clock="09:31:00"), _row(date, clock="15:00:00")]
    rows[0]["amount"] = 1_200.0
    output_root = tmp_path / "output"
    _write_partition(output_root, date, rows)

    payload = audit_minute_coverage(
        output_root,
        trade_dates=[date],
        annual_dates=[],
        deal_dates=[],
        tushare_dates=[date],
        requirements=CoverageRequirements(
            expected_trade_dates=1,
            expected_annual_full_sh_sz_dates=0,
            expected_deal_full_sh_sz_dates=0,
            expected_partial_top200_dates=1,
        ),
        partial_symbols=1,
        partial_bars_per_symbol=2,
    )

    record = payload["daily"][0]
    assert record["accepted_diagnostics"] == {
        "vwap_beyond_source_guard_rows": 1,
        "vwap_outside_ohlc_rows": 1,
    }
    assert record["fatal_issues"] == {"notional_beyond_hard_price_guard_rows": 1}
    assert payload["summary"]["fatal_notional_hard_guard_rows"] == 1
    assert payload["status"] == "failed"


def test_audit_rejects_comparable_annual_profile_beyond_two_percent(tmp_path: Path) -> None:
    date = "20260424"
    rows = [_row(date, clock="09:31:00"), _row(date, clock="14:57:00")]
    rows[0]["amount"] = 960.0
    output_root = tmp_path / "output"
    _write_partition(output_root, date, rows)

    payload = audit_minute_coverage(
        output_root,
        trade_dates=[date],
        annual_dates=[date],
        deal_dates=[],
        tushare_dates=[],
        partial_session_annual_dates=[date],
        expected_partition_stats={
            date: ExpectedPartitionStats(
                rows=2,
                symbols=1,
                traded_symbols=1,
                vol_sum=200.0,
                amount_sum=1_960.0,
                unit_profile="guan_shares_cents_times_shares",
                time_max=f"{date[:4]}-{date[4:6]}-{date[6:]} 14:57:00",
            )
        },
        requirements=CoverageRequirements(
            expected_trade_dates=1,
            expected_annual_full_sh_sz_dates=0,
            expected_deal_full_sh_sz_dates=0,
            expected_guan_partial_session_dates=1,
            expected_partial_top200_dates=0,
            require_full_source_stats=True,
        ),
    )

    record = payload["daily"][0]
    assert record["accepted_diagnostics"] == {"vwap_outside_ohlc_rows": 1}
    assert record["fatal_issues"] == {"vwap_beyond_source_guard_rows": 1}
    assert payload["status"] == "failed"


def test_audit_accepts_exact_known_lot_profile_flow_diagnostics(tmp_path: Path) -> None:
    date = "20220117"
    rows = [
        _row(date, clock="09:31:00"),
        {**_row(date, clock="09:32:00"), "vol": 0.0, "amount": 125.0},
        {**_row(date, clock="15:00:00"), "vol": 100.0, "amount": 0.0},
    ]
    output_root = tmp_path / "output"
    _write_partition(output_root, date, rows)

    payload = audit_minute_coverage(
        output_root,
        trade_dates=[date],
        annual_dates=[date],
        deal_dates=[],
        tushare_dates=[],
        expected_partition_stats={
            date: ExpectedPartitionStats(
                rows=3,
                symbols=1,
                traded_symbols=1,
                vol_sum=200.0,
                amount_sum=1_125.0,
                unit_profile="guan_lots_yuan",
            )
        },
        requirements=CoverageRequirements(
            expected_trade_dates=1,
            expected_annual_full_sh_sz_dates=1,
            expected_deal_full_sh_sz_dates=0,
            expected_partial_top200_dates=0,
            expected_accepted_zero_volume_nonzero_amount_rows=1,
            expected_accepted_positive_volume_zero_amount_rows=1,
            require_full_source_stats=True,
        ),
    )

    record = payload["daily"][0]
    assert record["accepted_diagnostics"] == {
        "notional_beyond_hard_price_guard_rows": 1,
        "positive_volume_zero_amount_rows": 1,
        "vwap_outside_ohlc_rows": 1,
        "zero_volume_nonzero_amount_rows": 1,
    }
    assert record["fatal_issues"] == {}
    assert payload["summary"]["accepted_zero_volume_nonzero_amount_rows"] == 1
    assert payload["summary"]["accepted_positive_volume_zero_amount_rows"] == 1
    assert payload["status"] == "passed"


def test_materialize_tushare_rejects_inconsistent_zero_flow(tmp_path: Path) -> None:
    date = "20260427"
    batch = _write_batch(tmp_path / "batches", date)
    raw = pd.read_parquet(batch)
    raw.loc[0, "vol"] = 0
    raw.loc[0, "amount"] = 125.0
    raw.to_parquet(batch, index=False)

    with pytest.raises(ValueError, match="zero_volume_nonzero_amount_rows"):
        materialize_tushare_partial_dates(
            tmp_path / "output",
            trade_dates=[date],
            annual_dates=[],
            deal_dates=[],
            tushare_batches={date: [batch]},
            expected_symbols=2,
            expected_bars_per_symbol=2,
        )


def test_materialize_tushare_accepts_rounded_zero_amount_only_inside_hard_guard(
    tmp_path: Path,
) -> None:
    date = "20260528"
    batch = _write_batch(tmp_path / "batches", date)
    raw = pd.read_parquet(batch)
    raw.loc[0, ["open", "close", "high", "low"]] = 0.36
    raw.loc[0, "vol"] = 1.0
    raw.loc[0, "amount"] = 0.0
    raw.to_parquet(batch, index=False)

    result = materialize_tushare_partial_dates(
        tmp_path / "output",
        trade_dates=[date],
        annual_dates=[],
        deal_dates=[],
        tushare_batches={date: [batch]},
        expected_symbols=2,
        expected_bars_per_symbol=2,
    )
    assert result["actions"][0]["accepted_diagnostics"] == {
        "positive_volume_zero_amount_rows": 1,
        "vwap_outside_ohlc_rows": 1,
    }

    raw.loc[0, ["open", "close", "high", "low"]] = 10.0
    raw.loc[0, "vol"] = 1_000.0
    raw.to_parquet(batch, index=False)
    with pytest.raises(ValueError, match="notional_beyond_hard_price_guard_rows"):
        materialize_tushare_partial_dates(
            tmp_path / "other-output",
            trade_dates=[date],
            annual_dates=[],
            deal_dates=[],
            tushare_batches={date: [batch]},
            expected_symbols=2,
            expected_bars_per_symbol=2,
        )


def test_audit_reports_missing_invalid_and_orphan_dates(tmp_path: Path) -> None:
    invalid_date = "20260424"
    missing_date = "20260427"
    orphan_date = "20260428"
    output_root = tmp_path / "output"
    duplicate = _row(invalid_date)
    _write_partition(output_root, invalid_date, [duplicate, duplicate])
    _write_partition(output_root, orphan_date, [_row(orphan_date)])

    payload = audit_minute_coverage(
        output_root,
        trade_dates=[invalid_date, missing_date],
        annual_dates=[invalid_date],
        deal_dates=[],
        tushare_dates=[missing_date],
        expected_partition_stats={
            invalid_date: ExpectedPartitionStats(rows=1, symbols=1),
        },
        requirements=CoverageRequirements(
            expected_trade_dates=2,
            expected_annual_full_sh_sz_dates=1,
            expected_deal_full_sh_sz_dates=0,
            expected_partial_top200_dates=1,
            require_full_source_stats=True,
        ),
        partial_symbols=2,
        partial_bars_per_symbol=2,
    )

    assert payload["status"] == "failed"
    assert payload["coverage_status"] == "incomplete"
    assert payload["failures"]["missing_output_dates"] == [missing_date]
    assert payload["failures"]["invalid_output_dates"] == [invalid_date]
    assert payload["failures"]["orphan_output_dates"] == [orphan_date]
    invalid = next(row for row in payload["daily"] if row["date"] == invalid_date)
    assert invalid["issues"]["duplicate_key_rows"] == 2
    assert invalid["issues"]["source_row_mismatch"] == 1


def test_audit_rejects_bad_partial_shape_and_requirement_counts(tmp_path: Path) -> None:
    partial_date = "20260709"
    output_root = tmp_path / "output"
    _write_partition(output_root, partial_date, [_row(partial_date)])

    payload = audit_minute_coverage(
        output_root,
        trade_dates=[partial_date],
        annual_dates=[],
        deal_dates=[],
        tushare_dates=[partial_date],
        requirements=CoverageRequirements(
            expected_trade_dates=2,
            expected_annual_full_sh_sz_dates=0,
            expected_deal_full_sh_sz_dates=0,
            expected_partial_top200_dates=1,
        ),
        partial_symbols=2,
        partial_bars_per_symbol=2,
    )

    assert payload["status"] == "failed"
    assert payload["failures"]["invalid_output_dates"] == [partial_date]
    assert payload["failures"]["requirement_mismatches"] == [
        {"check": "trade_dates", "actual": 1, "expected": 2}
    ]
    issues = payload["daily"][0]["issues"]
    assert issues["unexpected_row_count"] == 1
    assert issues["unexpected_symbol_count"] == 1
    assert issues["symbols_with_unexpected_bar_count"] == 1


def test_audit_rejects_wrong_guan_partial_session_maximum(tmp_path: Path) -> None:
    date = "20260423"
    output_root = tmp_path / "output"
    _write_partition(
        output_root,
        date,
        [_row(date), _row(date, clock="15:00:00")],
    )

    payload = audit_minute_coverage(
        output_root,
        trade_dates=[date],
        annual_dates=[],
        partial_session_annual_dates=[date],
        deal_dates=[],
        tushare_dates=[date],
        requirements=CoverageRequirements(
            expected_trade_dates=1,
            expected_annual_full_sh_sz_dates=0,
            expected_deal_full_sh_sz_dates=0,
            expected_guan_partial_session_dates=1,
            expected_partial_top200_dates=0,
        ),
    )

    assert payload["status"] == "failed"
    assert payload["summary"]["full_sh_sz_dates"] == 0
    assert payload["summary"]["guan_partial_session_dates"] == 1
    assert payload["daily"][0]["issues"]["unexpected_session_time_max"] == 1


def test_deal_manifest_requires_strict_whole_day_override_receipt(tmp_path: Path) -> None:
    date = "20260302"
    manifest = tmp_path / "deal_override.json"
    payload = {
        "schema_version": "a_share.minute_1m.fused.v1",
        "status": "passed",
        "options": {"annual_override_dates": [date]},
        "annual_overrides": {
            "dates": [date],
            "count": 1,
            "policy": "explicit_whole_day_deal_only",
        },
        "build_actions": {
            "guan_deal": [
                {
                    "date": date,
                    "status": "written",
                    "replacement_policy": "explicit_whole_day_deal_only",
                    "replaced_source": "guan_annual_minbar",
                    "session_validation": {
                        "profile": "guan_deal_full_session",
                        "time_min": "2026-03-02 09:30:00",
                        "time_max": "2026-03-02 15:00:00",
                        "opening_bar_rows": 10,
                        "opening_bar_symbols": 9,
                        "closing_bar_rows": 11,
                        "closing_bar_symbols": 10,
                        "issues": {
                            "unexpected_market_time_min": 0,
                            "unexpected_market_time_max": 0,
                            "missing_opening_execution_bar": 0,
                            "missing_closing_execution_bar": 0,
                        },
                        "valid": True,
                    },
                    "aggregation": {
                        "output_rows": 3,
                        "output_symbols": 2,
                        "output_traded_symbols": 2,
                        "output_vol_sum": 600.0,
                        "output_amount_sum": 6_000.0,
                        "unit_profile": "guan_deal_price_cents_volume_shares",
                    },
                }
            ]
        },
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    result = load_guan_deal_manifest(manifest)
    assert result.override_dates == {date}
    assert result.stats[date].time_min == "2026-03-02 09:30:00"
    assert result.stats[date].time_max == "2026-03-02 15:00:00"

    payload["build_actions"]["guan_deal"][0]["session_validation"]["closing_bar_rows"] = 0
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid full-session receipt"):
        load_guan_deal_manifest(manifest)


def test_production_input_discovery_reads_calendar_manifest_and_file_names(
    tmp_path: Path,
) -> None:
    calendar = tmp_path / "trade_cal.parquet"
    pd.DataFrame(
        {
            "cal_date": ["20260424", "20260425", "20260427", "20260608"],
            "is_open": [1, 0, 1, 1],
        }
    ).to_parquet(calendar, index=False)
    assert load_open_trade_dates(
        calendar,
        start_date="20260424",
        end_date="20260427",
    ) == ["20260424", "20260427"]

    annual_manifest = tmp_path / "annual.json"
    annual_manifest.write_text(
        json.dumps(
            {
                "schema_version": "guan.annual_minbar.v1",
                "years": {
                    "2026": {
                        "status": "complete",
                        "dates": ["20260423", "20260424"],
                        "unit_profile": {"name": "guan_shares_cents_times_shares"},
                        "staging_validation": {
                            "details": [
                                {
                                    "date": "20260423",
                                    "rows": 2,
                                    "symbols": 1,
                                    "time_min": "2026-04-23 09:31:00",
                                    "time_max": "2026-04-23 14:57:00",
                                },
                                {
                                    "date": "20260424",
                                    "rows": 2,
                                    "symbols": 1,
                                    "time_min": "2026-04-24 09:31:00",
                                    "time_max": "2026-04-24 15:00:00",
                                    "output_sha256": "a" * 64,
                                },
                            ]
                        },
                    },
                    "2025": {
                        "status": "promotion_failed",
                        "dates": ["20251231"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    annual_result = load_annual_minbar_manifest(annual_manifest)
    annual_dates, annual_stats = annual_result
    assert annual_dates == {"20260423", "20260424"}
    assert annual_result.full_dates == {"20260424"}
    assert annual_result.partial_session_dates == {"20260423"}
    assert annual_stats["20260424"] == ExpectedPartitionStats(
        rows=2,
        symbols=1,
        unit_profile="guan_shares_cents_times_shares",
        time_min="2026-04-24 09:31:00",
        time_max="2026-04-24 15:00:00",
        output_sha256="a" * 64,
    )

    deal_manifest = tmp_path / "deal.json"
    deal_manifest.write_text(
        json.dumps(
            {
                "schema_version": "a_share.minute_1m.fused.v1",
                "status": "passed",
                "build_actions": {
                    "guan_deal": [
                        {
                            "date": "20260608",
                            "status": "written",
                            "aggregation": {
                                "output_rows": 3,
                                "output_symbols": 2,
                                "output_traded_symbols": 2,
                                "output_vol_sum": 600.0,
                                "output_amount_sum": 6_000.0,
                                "unit_profile": "guan_deal_price_cents_volume_shares",
                            },
                            "output_signature": {"rows": 3, "sha256": "b" * 64},
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    deal_result = load_guan_deal_manifest(deal_manifest)
    deal_dates, deal_stats = deal_result
    assert deal_dates == {"20260608"}
    assert deal_result.override_dates == set()
    assert deal_stats["20260608"] == ExpectedPartitionStats(
        rows=3,
        symbols=2,
        traded_symbols=2,
        vol_sum=600.0,
        amount_sum=6_000.0,
        unit_profile="guan_deal_price_cents_volume_shares",
        output_sha256="b" * 64,
    )

    deal_root = tmp_path / "deals"
    month_root = deal_root / "202606"
    month_root.mkdir(parents=True)
    (month_root / "deal_20260608.parquet").touch()
    (deal_root / "deal_20260608(1).parquet").touch()
    assert discover_guan_deal_files(deal_root) == {"20260608": month_root / "deal_20260608.parquet"}

    duplicate_root = deal_root / "duplicate"
    duplicate_root.mkdir()
    (duplicate_root / "deal_20260608.parquet").touch()
    with pytest.raises(ValueError, match="Duplicate Guan deal date"):
        discover_guan_deal_files(deal_root)

    batch_root = tmp_path / "batches"
    batch_root.mkdir()
    first = batch_root / "minute_20260427_batch001.parquet"
    second = batch_root / "minute_20260427_batch002.parquet"
    ignored = batch_root / "minute_20260427_other.parquet"
    for path in (first, second, ignored):
        path.touch()
    assert discover_tushare_minute_batches(batch_root) == {"20260427": [first, second]}
