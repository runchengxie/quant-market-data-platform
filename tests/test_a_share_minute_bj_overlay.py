from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
import pytest

from market_data_platform.providers import tushare_a_share_mins as mins
from market_data_platform.providers.a_share_minute_bj_overlay import (
    BJ_OVERLAY_PLAN_SCHEMA,
    BJ_OVERLAY_RECEIPT_SCHEMA,
    BJ_UNIVERSE_RULE,
    discover_tushare_bj_partitions,
    load_bj_overlay_plan,
    materialize_tushare_bj_overlay,
)
from market_data_platform.providers.a_share_minute_coverage import (
    ANNUAL_FULL_SH_SZ,
    DEAL_FULL_SH_SZ,
    CoverageRequirements,
    apply_bj_overlay_coverage,
    audit_minute_coverage,
    classify_minute_coverage,
)
from market_data_platform.providers.a_share_minute_fusion import (
    CANONICAL_MINUTE_SCHEMA,
    write_canonical_minute_partition,
)
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy


def _bar(
    date: str,
    *,
    symbol: str,
    clock: str,
    close: float = 10.0,
    amount_multiplier: float = 1.0,
) -> dict[str, object]:
    return {
        "ts_code": symbol,
        "trade_time": pd.Timestamp(f"{date[:4]}-{date[4:6]}-{date[6:]} {clock}"),
        "open": close,
        "close": close,
        "high": close + 0.1,
        "low": close - 0.1,
        "vol": 100.0,
        "amount": close * 100.0 * amount_multiplier,
    }


def _write_base(root: Path, date: str, *, symbol: str = "000001.SZ") -> Path:
    path = root / f"trade_date={date}" / "part-00000.parquet"
    frame = pd.DataFrame(
        [
            _bar(date, symbol=symbol, clock="09:30:00"),
            _bar(date, symbol=symbol, clock="15:00:00", close=10.2),
        ]
    )
    write_canonical_minute_partition(frame, path)
    return path


def _write_bj_mirror(  # noqa: PLR0913
    root: Path,
    date: str,
    *,
    symbols: tuple[str, ...] = ("920001.BJ", "920002.BJ"),
    universe_rule: str = BJ_UNIVERSE_RULE,
    universe_source: str = "test-dynamic-daily+exchange_filter(BJ)",
    amount_multiplier: float = 1.0,
) -> Path:
    times = [
        *pd.date_range(f"{date} 09:30:00", periods=121, freq="min"),
        *pd.date_range(f"{date} 13:01:00", periods=120, freq="min"),
    ]
    frame = pd.DataFrame(
        [
            _bar(
                date,
                symbol=symbol,
                clock=trade_time.strftime("%H:%M:%S"),
                close=20.0,
                amount_multiplier=amount_multiplier,
            )
            for symbol in symbols
            for trade_time in times
        ]
    )
    part_dir = root / f"trade_date={date}"
    mins._atomic_write_partition(frame, part_dir, trade_date=date)
    expected_symbols = set(symbols)
    mins._write_completeness(
        mins._MinuteProgress(
            part_dir=part_dir,
            trade_date=date,
            freq=mins.DEFAULT_FREQ,
            universe_hash=mins._universe_hash(expected_symbols, rule=universe_rule),
            universe_rule=universe_rule,
            universe_source=universe_source,
            expected_symbols=frozenset(expected_symbols),
            request_policy=TushareRequestPolicy(),
        ),
        completed_symbols=expected_symbols,
        partition=mins._partition_state(part_dir, trade_date=date),
        status="complete",
    )
    return part_dir


def _base_expected(root: Path, date: str, *, deal: bool = False) -> dict[str, object]:
    path = root / f"trade_date={date}" / "part-00000.parquet"
    frame = pd.read_parquet(path)
    frame = frame.loc[~frame["ts_code"].str.endswith(".BJ")]
    result: dict[str, object] = {
        "rows": len(frame),
        "symbols": int(frame["ts_code"].nunique()),
        "unit_profile": "guan_deal_price_cents_volume_shares" if deal else "guan_lots_yuan",
        "time_min": str(frame["trade_time"].min()),
        "time_max": str(frame["trade_time"].max()),
    }
    if deal:
        result.update(
            {
                "traded_symbols": int(frame.loc[frame["vol"].gt(0), "ts_code"].nunique()),
                "vol_sum": float(frame["vol"].sum()),
                "amount_sum": float(frame["amount"].sum()),
                "output_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return result


def _rebind_sidecar_to_current_partition(part_dir: Path) -> None:
    part_path = part_dir / "part-00000.parquet"
    sidecar_path = part_dir / mins.COMPLETENESS_FILENAME
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    stat = part_path.stat()
    payload["partition"]["files"][0].update(
        {
            "rows": pq.ParquetFile(part_path).metadata.num_rows,
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": hashlib.sha256(part_path.read_bytes()).hexdigest(),
        }
    )
    sidecar_path.write_text(json.dumps(payload), encoding="utf-8")


def _materialize(  # noqa: PLR0913
    output_root: Path,
    source_root: Path,
    dates: list[str],
    *,
    annual: list[str] | None = None,
    deal: list[str] | None = None,
    full: list[str] | None = None,
    receipt: Path | None = None,
    dry_run: bool = False,
    base_expected: dict[str, dict[str, object]] | None = None,
) -> dict[str, Any]:
    deal_dates = set(deal or [])
    return materialize_tushare_bj_overlay(
        output_root,
        annual_full_dates=annual if annual is not None else dates,
        deal_full_dates=deal or [],
        tushare_full_dates=full or [],
        bj_partitions=discover_tushare_bj_partitions(source_root),
        overlay_dates=dates,
        base_expected_stats=(
            base_expected
            if base_expected is not None
            else {
                date: _base_expected(output_root, date, deal=date in deal_dates) for date in dates
            }
        ),
        receipt_path=receipt,
        dry_run=dry_run,
    )


def test_overlay_appends_disjoint_bj_rows_and_writes_bound_receipt(tmp_path: Path) -> None:
    annual_date = "20220104"
    deal_date = "20260608"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    for date in (annual_date, deal_date):
        _write_base(output_root, date)
        _write_bj_mirror(source_root, date)
    receipt = tmp_path / "bj-overlay.json"

    result = _materialize(
        output_root,
        source_root,
        [annual_date, deal_date],
        annual=[annual_date],
        deal=[deal_date],
        receipt=receipt,
    )

    assert result["schema_version"] == BJ_OVERLAY_RECEIPT_SCHEMA
    assert result["status"] == "passed"
    assert result["phase"] == "pilot"
    assert result["policy"]["source_validation"] == "passed"
    assert result["policy"]["base_validation"] == "independent_manifest_reconciliation_passed"
    assert (
        result["policy"]["tushare_price_flow_validation"]["two_percent_vwap_diagnostic"][
            "disposition"
        ]
        == "accepted_diagnostic"
    )
    assert (
        result["policy"]["tushare_price_flow_validation"]["hard_notional_guard"]["disposition"]
        == "fatal"
    )
    assert result["summary"]["written_dates"] == 2
    assert result["summary"]["annual_overlay_dates"] == 1
    assert result["summary"]["deal_overlay_dates"] == 1
    assert all(action["status"] == "written_bj_overlay" for action in result["actions"])
    for date in (annual_date, deal_date):
        path = output_root / f"trade_date={date}" / "part-00000.parquet"
        frame = pd.read_parquet(path)
        assert len(frame) == 2 + 2 * 241
        assert set(frame["ts_code"]) == {"000001.SZ", "920001.BJ", "920002.BJ"}
        assert not frame.duplicated(["ts_code", "trade_time"]).any()
        assert pq.ParquetFile(path).schema_arrow.equals(CANONICAL_MINUTE_SCHEMA)
        action = next(item for item in result["actions"] if item["date"] == date)
        assert action["output_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        source_receipt = result["source_receipts"][date]
        assert source_receipt["universe_rule"] == BJ_UNIVERSE_RULE
        assert source_receipt["sidecar_sha256"]
        assert source_receipt["partition_sha256"]
        assert source_receipt["flow_diagnostics"] == {
            "extreme_vwap_unit_scale_rows": 0,
            "notional_beyond_hard_price_guard_rows": 0,
            "positive_volume_zero_amount_rows": 0,
            "vwap_beyond_source_guard_rows": 0,
            "vwap_outside_ohlc_rows": 0,
            "zero_volume_nonzero_amount_rows": 0,
        }
        assert source_receipt["session_grid_validation"]["status"] == "passed"
        assert result["base_receipts"][date]["status"] == "passed"
    assert result["base_receipts"][annual_date]["hash_validation"] == (
        "not_available_in_source_manifest"
    )
    assert result["base_receipts"][deal_date]["hash_validation"] == ("matched_source_manifest")
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "passed"


def test_overlay_is_idempotent_when_exact_bj_rows_are_already_present(tmp_path: Path) -> None:
    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    target = _write_base(output_root, date)
    _write_bj_mirror(source_root, date)
    _materialize(output_root, source_root, [date])
    first_sha = hashlib.sha256(target.read_bytes()).hexdigest()

    second = _materialize(output_root, source_root, [date])

    assert second["summary"]["written_dates"] == 0
    assert second["summary"]["skipped_already_overlayed_dates"] == 1
    assert second["actions"][0]["status"] == "skipped_already_overlayed"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == first_sha


@pytest.mark.parametrize(
    ("rule", "source", "message"),
    [
        (
            f"{mins.EXPLICIT_UNIVERSE_RULE}:exchange=BJ",
            "explicit+exchange_filter(BJ)",
            "dynamic daily universe rule",
        ),
        (BJ_UNIVERSE_RULE, "not-dynamic", "dynamic exchange filter"),
    ],
)
def test_overlay_rejects_non_dynamic_bj_sidecars(
    tmp_path: Path,
    rule: str,
    source: str,
    message: str,
) -> None:
    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    _write_base(output_root, date)
    _write_bj_mirror(
        source_root,
        date,
        universe_rule=rule,
        universe_source=source,
    )

    with pytest.raises(ValueError, match=message):
        _materialize(output_root, source_root, [date])


def test_overlay_rejects_wrong_exchange_even_with_bj_rule(tmp_path: Path) -> None:
    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    _write_base(output_root, date)
    _write_bj_mirror(source_root, date, symbols=("000001.SZ",))

    with pytest.raises(ValueError, match="not a pure Beijing-market universe"):
        _materialize(output_root, source_root, [date])


def test_overlay_accepts_one_share_integer_amount_beyond_two_percent_diagnostic(
    tmp_path: Path,
) -> None:
    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    target = _write_base(output_root, date)
    before = target.read_bytes()
    part_dir = _write_bj_mirror(source_root, date)
    part_path = part_dir / "part-00000.parquet"
    changed = pd.read_parquet(part_path)
    changed.loc[0, ["open", "close", "high", "low"]] = 3.76
    changed.loc[0, "vol"] = 1.0
    changed.loc[0, "amount"] = 4.0
    mins._atomic_write_partition(changed, part_dir, trade_date=date)
    _rebind_sidecar_to_current_partition(part_dir)

    result = _materialize(output_root, source_root, [date])

    source_receipt = result["source_receipts"][date]
    assert source_receipt["accepted_diagnostics"] == {
        "vwap_beyond_source_guard_rows": 1,
        "vwap_outside_ohlc_rows": 1,
    }
    assert source_receipt["flow_diagnostics"]["notional_beyond_hard_price_guard_rows"] == 0
    assert target.read_bytes() != before


def test_overlay_rejects_large_notional_beyond_hard_guard(tmp_path: Path) -> None:
    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    target = _write_base(output_root, date)
    before = target.read_bytes()
    part_dir = _write_bj_mirror(source_root, date)
    part_path = part_dir / "part-00000.parquet"
    changed = pd.read_parquet(part_path)
    changed.loc[0, ["open", "close", "high", "low"]] = 3.76
    changed.loc[0, "vol"] = 1_000.0
    changed.loc[0, "amount"] = 4_200.0
    mins._atomic_write_partition(changed, part_dir, trade_date=date)
    _rebind_sidecar_to_current_partition(part_dir)

    with pytest.raises(ValueError, match="notional_beyond_hard_price_guard_rows"):
        _materialize(output_root, source_root, [date])
    assert target.read_bytes() == before


def test_overlay_records_bj_vwap_diagnostic_inside_two_percent_guard(
    tmp_path: Path,
) -> None:
    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    _write_base(output_root, date)
    _write_bj_mirror(source_root, date, amount_multiplier=1.01)

    result = _materialize(output_root, source_root, [date])

    diagnostics = result["source_receipts"][date]["flow_diagnostics"]
    assert diagnostics["vwap_outside_ohlc_rows"] == 2 * 241
    assert diagnostics["vwap_beyond_source_guard_rows"] == 0
    assert result["status"] == "passed"


def test_overlay_rejects_tampered_partition_hash_before_mutating_base(tmp_path: Path) -> None:
    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    target = _write_base(output_root, date)
    before = target.read_bytes()
    source = _write_bj_mirror(source_root, date) / "part-00000.parquet"
    changed = pd.read_parquet(source)
    changed.loc[0, "close"] = 99.0
    changed.to_parquet(source, index=False)

    with pytest.raises(ValueError, match="changed after its sidecar"):
        _materialize(output_root, source_root, [date])
    assert target.read_bytes() == before


def test_overlay_rechecks_exact_bj_session_grid_from_parquet(tmp_path: Path) -> None:
    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    target = _write_base(output_root, date)
    before = target.read_bytes()
    part_dir = _write_bj_mirror(source_root, date)
    part_path = part_dir / "part-00000.parquet"
    changed = pd.read_parquet(part_path)
    changed.loc[0, "trade_time"] = pd.Timestamp("2022-01-04 12:59:00")
    mins._atomic_write_partition(changed, part_dir, trade_date=date)
    _rebind_sidecar_to_current_partition(part_dir)

    with pytest.raises(ValueError, match="exact 241-bar session grid"):
        _materialize(output_root, source_root, [date])
    assert target.read_bytes() == before


def test_overlay_rejects_base_with_missing_row_against_annual_receipt(tmp_path: Path) -> None:
    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    target = _write_base(output_root, date)
    expected = _base_expected(output_root, date)
    changed = pd.read_parquet(target).iloc[1:].reset_index(drop=True)
    write_canonical_minute_partition(changed, target)
    before = target.read_bytes()
    _write_bj_mirror(source_root, date)

    with pytest.raises(ValueError, match="base_row_mismatch"):
        _materialize(
            output_root,
            source_root,
            [date],
            base_expected={date: expected},
        )
    assert target.read_bytes() == before


def test_overlay_rejects_scaled_deal_base_flow_against_manifest(tmp_path: Path) -> None:
    date = "20260608"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    target = _write_base(output_root, date)
    expected = _base_expected(output_root, date, deal=True)
    changed = pd.read_parquet(target)
    changed[["vol", "amount"]] *= 100.0
    write_canonical_minute_partition(changed, target)
    before = target.read_bytes()
    _write_bj_mirror(source_root, date)

    with pytest.raises(ValueError, match="base_vol_sum_mismatch"):
        _materialize(
            output_root,
            source_root,
            [date],
            annual=[],
            deal=[date],
            base_expected={date: expected},
        )
    assert target.read_bytes() == before


def test_overlay_rejects_deal_base_hash_change_with_matching_shape_and_flow(
    tmp_path: Path,
) -> None:
    date = "20260608"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    target = _write_base(output_root, date)
    expected = _base_expected(output_root, date, deal=True)
    changed = pd.read_parquet(target)
    changed.loc[0, ["open", "close", "high", "low"]] += 0.01
    write_canonical_minute_partition(changed, target)
    before = target.read_bytes()
    _write_bj_mirror(source_root, date)

    with pytest.raises(ValueError, match="base_output_sha256_mismatch"):
        _materialize(
            output_root,
            source_root,
            [date],
            annual=[],
            deal=[date],
            base_expected={date: expected},
        )
    assert target.read_bytes() == before


def test_all_dates_are_preflighted_before_first_partition_is_written(tmp_path: Path) -> None:
    first = "20220104"
    second = "20220105"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    first_target = _write_base(output_root, first)
    _write_base(output_root, second)
    before = first_target.read_bytes()
    _write_bj_mirror(source_root, first)
    _write_bj_mirror(
        source_root,
        second,
        universe_rule=f"{mins.EXPLICIT_UNIVERSE_RULE}:exchange=BJ",
    )

    with pytest.raises(ValueError, match="dynamic daily universe rule"):
        _materialize(output_root, source_root, [first, second])
    assert first_target.read_bytes() == before


def test_overlay_rejects_ineligible_pre_bse_and_already_full_a_dates(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot precede 20211115"):
        materialize_tushare_bj_overlay(
            tmp_path,
            annual_full_dates=["20211112"],
            bj_partitions={},
            overlay_dates=["20211112"],
            base_expected_stats={},
            dry_run=True,
        )

    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    _write_base(output_root, date)
    source = _write_bj_mirror(source_root, date)
    with pytest.raises(ValueError, match="already contain BJ"):
        materialize_tushare_bj_overlay(
            output_root,
            annual_full_dates=[date],
            tushare_full_dates=[date],
            bj_partitions={date: source},
            overlay_dates=[date],
            base_expected_stats={date: _base_expected(output_root, date)},
            dry_run=True,
        )
    with pytest.raises(ValueError, match="only complete Guan"):
        materialize_tushare_bj_overlay(
            output_root,
            annual_full_dates=[],
            deal_full_dates=[],
            bj_partitions={date: source},
            overlay_dates=[date],
            base_expected_stats={date: _base_expected(output_root, date)},
            dry_run=True,
        )


def test_dry_run_and_plan_loader_do_not_mutate_partition(tmp_path: Path) -> None:
    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    target = _write_base(output_root, date)
    before = target.read_bytes()
    source = _write_bj_mirror(source_root, date)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": BJ_OVERLAY_PLAN_SCHEMA,
                "phase": "production",
                "dates": [date],
            }
        ),
        encoding="utf-8",
    )
    receipt = tmp_path / "planned.json"

    plan = load_bj_overlay_plan(plan_path)
    result = materialize_tushare_bj_overlay(
        output_root,
        annual_full_dates=[date],
        bj_partitions={date: source},
        overlay_dates=plan.dates,
        phase=plan.phase,
        plan_sha256=plan.sha256,
        base_expected_stats={date: _base_expected(output_root, date)},
        receipt_path=receipt,
        dry_run=True,
    )

    assert result["status"] == "planned"
    assert result["phase"] == "production"
    assert result["plan_sha256"] == hashlib.sha256(plan_path.read_bytes()).hexdigest()
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "planned"
    assert target.read_bytes() == before


def test_coverage_marks_overlay_sources_and_preserves_pre_bse_point_in_time_full(
    tmp_path: Path,
) -> None:
    pre_bse = "20211112"
    annual_date = "20220104"
    deal_date = "20260608"
    expectations = apply_bj_overlay_coverage(
        classify_minute_coverage(
            [pre_bse, annual_date, deal_date],
            annual_dates=[pre_bse, annual_date],
            deal_dates=[deal_date],
            tushare_dates=[],
        ),
        bj_overlay_dates=[annual_date, deal_date],
    )
    by_date = {item.trade_date: item for item in expectations}

    assert by_date[pre_bse].tier == ANNUAL_FULL_SH_SZ
    assert by_date[pre_bse].market_scope == "SH_SZ"
    assert by_date[pre_bse].is_full_a_share is True
    assert by_date[annual_date].tier == ANNUAL_FULL_SH_SZ
    assert by_date[annual_date].market_scope == "SH_SZ_BJ"
    assert by_date[annual_date].overlay_sources == ("tushare_bj_overlay",)
    assert by_date[deal_date].tier == DEAL_FULL_SH_SZ
    assert by_date[deal_date].market_scope == "SH_SZ_BJ"

    with pytest.raises(ValueError, match="already contain BJ"):
        apply_bj_overlay_coverage(
            classify_minute_coverage(
                [annual_date],
                annual_dates=[annual_date],
                deal_dates=[],
                tushare_dates=[],
                tushare_full_dates=[annual_date],
            ),
            tushare_full_dates=[annual_date],
            bj_overlay_dates=[annual_date],
        )


def test_coverage_audit_counts_pre_bse_and_overlay_days_as_full_a_share(
    tmp_path: Path,
) -> None:
    pre_bse = "20211112"
    post_bse = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    pre_path = _write_base(output_root, pre_bse)
    _write_base(output_root, post_bse)
    _write_bj_mirror(source_root, post_bse)
    overlay = _materialize(output_root, source_root, [post_bse])
    pre_frame = pd.read_parquet(pre_path)
    pre_numeric = pre_frame[["open", "close", "high", "low", "vol", "amount"]]
    expected_stats = {
        pre_bse: {
            "rows": len(pre_frame),
            "symbols": int(pre_frame["ts_code"].nunique()),
            "traded_symbols": int(pre_frame.loc[pre_frame["vol"].gt(0), "ts_code"].nunique()),
            "vol_sum": float(pre_numeric["vol"].sum()),
            "amount_sum": float(pre_numeric["amount"].sum()),
            "unit_profile": "guan_lots_yuan",
            "time_min": str(pre_frame["trade_time"].min()),
            "time_max": str(pre_frame["trade_time"].max()),
        },
        post_bse: overlay["expected_partition_stats"][post_bse],
    }

    result = audit_minute_coverage(
        output_root,
        trade_dates=[pre_bse, post_bse],
        annual_dates=[pre_bse, post_bse],
        deal_dates=[],
        tushare_dates=[],
        expected_partition_stats=expected_stats,
        input_lineage={"tushare_bj_overlay_dates": [post_bse]},
        requirements=CoverageRequirements(
            expected_trade_dates=2,
            expected_annual_full_sh_sz_dates=2,
            expected_deal_full_sh_sz_dates=0,
            expected_guan_partial_session_dates=0,
            expected_partial_top200_dates=0,
            require_full_source_stats=True,
        ),
    )

    assert result["status"] == "passed", result["failures"]
    assert result["coverage_status"] == "full_a_share"
    assert result["summary"]["full_a_share_dates"] == 2
    assert result["summary"]["tushare_bj_overlay_dates"] == 1
    assert result["summary"]["bj_overlay_date_count"] == 1
    assert result["inputs"]["bj_overlay_dates"] == [post_bse]
    assert result["policy"]["fixed_241_bar_completion"] is False
    assert result["policy"]["tushare_components_fixed_241"] is True
    by_date = {item["date"]: item for item in result["daily"]}
    assert by_date[pre_bse]["market_scope"] == "SH_SZ"
    assert by_date[post_bse]["market_scope"] == "SH_SZ_BJ"
    assert by_date[post_bse]["overlay_sources"] == ["tushare_bj_overlay"]


def test_coverage_rechecks_bj_hard_guard_without_granting_guan_exemption(
    tmp_path: Path,
) -> None:
    date = "20220104"
    output_root = tmp_path / "version"
    source_root = tmp_path / "bj"
    _write_base(output_root, date)
    _write_bj_mirror(source_root, date)
    overlay = _materialize(output_root, source_root, [date])
    path = output_root / f"trade_date={date}" / "part-00000.parquet"
    changed = pd.read_parquet(path)
    bj_rows = changed["ts_code"].str.endswith(".BJ")
    changed.loc[bj_rows, "amount"] *= 1.20
    write_canonical_minute_partition(changed, path)
    expected = dict(overlay["expected_partition_stats"][date])
    expected["amount_sum"] = float(changed["amount"].sum())

    result = audit_minute_coverage(
        output_root,
        trade_dates=[date],
        annual_dates=[date],
        deal_dates=[],
        tushare_dates=[],
        expected_partition_stats={date: expected},
        input_lineage={"tushare_bj_overlay_dates": [date]},
        requirements=CoverageRequirements(
            expected_trade_dates=1,
            expected_annual_full_sh_sz_dates=1,
            expected_deal_full_sh_sz_dates=0,
            expected_guan_partial_session_dates=0,
            expected_partial_top200_dates=0,
            require_full_source_stats=True,
        ),
    )

    assert result["status"] == "failed"
    fatal = result["daily"][0]["fatal_issues"]
    assert fatal["notional_beyond_hard_price_guard_rows"] == 2 * 241
