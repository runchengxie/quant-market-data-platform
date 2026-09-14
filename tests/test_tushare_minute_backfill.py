from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from market_data_platform import tushare_minute_backfill as backfill
from market_data_platform.cli import build_parser
from market_data_platform.providers.tushare_a_share_mins import (
    MinsMirrorOptions,
    MinuteMirrorIncompleteDatesError,
)
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy


def _write_sources(
    tmp_path: Path,
    *,
    dates: list[str],
    instruments: list[tuple[str, str, str]],
) -> tuple[Path, Path]:
    trade_cal = tmp_path / "trade_cal.parquet"
    pd.DataFrame({"cal_date": dates, "is_open": [1] * len(dates)}).to_parquet(
        trade_cal, index=False
    )
    instrument_path = tmp_path / "instruments.parquet"
    pd.DataFrame(
        [
            {
                "ts_code": ts_code,
                "list_date": list_date,
                "delist_date": delist_date,
                "curr_type": "CNY",
            }
            for ts_code, list_date, delist_date in instruments
        ]
    ).to_parquet(instrument_path, index=False)
    return trade_cal, instrument_path


def _plan_options(
    tmp_path: Path,
    *,
    dates: list[str],
    instruments: list[tuple[str, str, str]],
    scope: backfill.MinuteBackfillScope = "all-a",
    segment: backfill.MinuteBackfillSegment = "month",
    **overrides: object,
) -> backfill.MinuteBackfillPlanOptions:
    trade_cal, instrument_path = _write_sources(
        tmp_path,
        dates=dates,
        instruments=instruments,
    )
    values: dict[str, Any] = {
        "start_date": min(dates),
        "end_date": max(dates),
        "scope": scope,
        "trade_cal_path": trade_cal,
        "instruments_path": instrument_path,
        "backfill_root": tmp_path / "backfill",
        "plan_path": tmp_path / f"{scope}.plan.json",
        "segment": segment,
        "batch_size": 2,
        "cooldown_seconds": 0.5,
        "token_env": "TEST_TUSHARE_TOKEN",
        "api_url": "https://minute.example.test/api",
        "request_policy": TushareRequestPolicy(
            attempts=2,
            retry_sleep_seconds=1,
            retry_max_sleep_seconds=2,
            quota_cooldown_seconds=3,
        ),
    }
    values.update(overrides)
    return backfill.MinuteBackfillPlanOptions(**values)


def test_sh_sz_plan_excludes_bj_symbols(tmp_path: Path) -> None:
    options = _plan_options(
        tmp_path,
        dates=["20240102"],
        instruments=[
            ("000001.SZ", "19910101", ""),
            ("600000.SH", "19990101", ""),
            ("920001.BJ", "20211115", ""),
        ],
        scope="sh-sz-only",
    )

    plan = backfill.build_minute_backfill_plan(options)

    assert plan["identity"]["scope"] == "sh-sz-only"
    assert plan["identity"]["exchange_filter"] == "SH_SZ"
    assert plan["summary"]["instrument_stats"]["scope_symbols"] == 2


def test_bj_plan_uses_bse_era_dynamic_intervals_and_request_budget(tmp_path: Path) -> None:
    options = _plan_options(
        tmp_path,
        dates=["20211112", "20211115", "20211116", "20211117"],
        instruments=[
            ("000001.SZ", "19910101", ""),
            ("920001.BJ", "20211115", ""),
            ("920002.BJ", "20211115", "20211116"),
            ("920003.BJ", "20211116", ""),
        ],
        scope="bj-only",
        request_budget=4,
    )

    plan = backfill.build_minute_backfill_plan(options)

    assert plan["identity"]["exchange_filter"] == "BJ"
    assert plan["identity"]["query"]["effective_start_date"] == "20211115"
    assert plan["identity"]["segments"][0]["dates"] == ["20211115", "20211116"]
    assert plan["summary"]["eligible_dates"] == 3
    assert plan["summary"]["selected_dates"] == 2
    assert plan["summary"]["active_interval_estimated_minute_requests"] == 3
    assert plan["summary"]["minute_request_upper_bound"] == 4
    assert plan["summary"]["minute_requests_per_date_upper_bound"] == 2
    assert plan["summary"]["truncated_by"] == "request_budget"
    assert plan["output"]["writes_production"] is False
    assert options.plan_path is not None
    serialized = Path(options.plan_path).read_text(encoding="utf-8")
    assert "TEST_TUSHARE_TOKEN" in serialized
    assert "private-token" not in serialized
    assert plan["security"]["contains_token"] is False


def test_all_a_plan_supports_year_segments_and_max_dates(tmp_path: Path) -> None:
    options = _plan_options(
        tmp_path,
        dates=["20231229", "20240102", "20240103"],
        instruments=[
            ("000001.SZ", "19910101", ""),
            ("600000.SH", "19990101", ""),
            ("920001.BJ", "20211115", ""),
        ],
        scope="all-a",
        segment="year",
        max_dates=2,
    )

    plan = backfill.build_minute_backfill_plan(options)

    assert plan["identity"]["exchange_filter"] is None
    assert [segment["period"] for segment in plan["identity"]["segments"]] == ["2023", "2024"]
    assert plan["summary"]["selected_dates"] == 2
    assert plan["summary"]["omitted_dates"] == 1
    assert plan["summary"]["truncated_by"] == "max_dates"


def test_reverse_plan_processes_latest_open_dates_and_segments_first(tmp_path: Path) -> None:
    options = _plan_options(
        tmp_path,
        dates=["20220712", "20220713", "20220714", "20220715", "20220801"],
        instruments=[("000001.SZ", "19910101", "")],
        date_order="descending",
    )

    plan = backfill.build_minute_backfill_plan(options)

    assert plan["identity"]["query"]["date_order"] == "descending"
    assert [date for segment in plan["identity"]["segments"] for date in segment["dates"]] == [
        "20220801",
        "20220715",
        "20220714",
        "20220713",
        "20220712",
    ]
    assert [segment["period"] for segment in plan["identity"]["segments"]] == ["202208", "202207"]
    assert plan["identity"]["segments"][0]["start_date"] == "20220801"
    assert plan["identity"]["segments"][0]["end_date"] == "20220801"
    assert plan["identity"]["segments"][1]["start_date"] == "20220712"
    assert plan["identity"]["segments"][1]["end_date"] == "20220715"


def test_plan_batch_size_accepts_33_and_rejects_34(tmp_path: Path) -> None:
    instruments = [(f"{index:06d}.SZ", "19910101", "") for index in range(1, 35)]
    options = _plan_options(
        tmp_path,
        dates=["20240102"],
        instruments=instruments,
        batch_size=33,
    )

    plan = backfill.build_minute_backfill_plan(options)

    assert plan["identity"]["limits"]["batch_size"] == 33
    assert plan["summary"]["minute_requests_per_date_upper_bound"] == 2

    rejected = _plan_options(
        tmp_path,
        dates=["20240102"],
        instruments=instruments,
        batch_size=34,
    )
    with pytest.raises(ValueError, match="batch_size must be between 1 and 33 for 1min data"):
        backfill.build_minute_backfill_plan(rejected)


def test_dates_file_selects_non_contiguous_open_dates_and_is_fingerprinted(
    tmp_path: Path,
) -> None:
    dates_file = tmp_path / "promotion-plan.json"
    dates_file.write_text(
        '{"schema_version":"a_share.minute_tushare_full_day_plan.v1",'
        '"phase":"production","dates":["20240102","20240201"]}\n',
        encoding="utf-8",
    )
    options = _plan_options(
        tmp_path,
        dates=["20240102", "20240103", "20240201"],
        instruments=[("000001.SZ", "19910101", "")],
        dates_path=dates_file,
    )

    plan = backfill.build_minute_backfill_plan(options)

    assert plan["identity"]["query"]["date_selection"] == "explicit_file"
    assert plan["identity"]["segments"][0]["dates"] == ["20240102"]
    assert plan["identity"]["segments"][1]["dates"] == ["20240201"]
    assert plan["identity"]["sources"]["dates_file"]["sha256"]
    assert plan["summary"]["calendar_open_dates"] == 3
    assert plan["summary"]["requested_dates"] == 2


def test_dates_file_rejects_dates_outside_local_open_calendar(tmp_path: Path) -> None:
    dates_file = tmp_path / "gap-dates.json"
    dates_file.write_text('["20240102", "20240106"]\n', encoding="utf-8")
    options = _plan_options(
        tmp_path,
        dates=["20240102", "20240103"],
        instruments=[("000001.SZ", "19910101", "")],
        dates_path=dates_file,
    )

    with pytest.raises(ValueError, match="must be open dates"):
        backfill.build_minute_backfill_plan(options)


def test_unreliable_zero_active_estimate_does_not_drop_an_explicit_open_date(
    tmp_path: Path,
) -> None:
    dates_file = tmp_path / "promotion-plan.json"
    dates_file.write_text('{"dates":["20211115"]}\n', encoding="utf-8")
    options = _plan_options(
        tmp_path,
        dates=["20211115"],
        instruments=[("920001.BJ", "20220101", "")],
        scope="bj-only",
        dates_path=dates_file,
    )

    plan = backfill.build_minute_backfill_plan(options)

    assert plan["summary"]["selected_dates"] == 1
    assert plan["identity"]["segments"][0]["estimated_active_symbols_min"] == 0
    assert plan["identity"]["segments"][0]["minute_request_upper_bound"] == 1


def test_runner_dry_run_writes_receipt_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = backfill.build_minute_backfill_plan(
        _plan_options(
            tmp_path,
            dates=["20240102"],
            instruments=[("000001.SZ", "19910101", "")],
        )
    )
    monkeypatch.delenv("TEST_TUSHARE_TOKEN", raising=False)
    receipt_path = tmp_path / "dry-run.receipt.json"

    receipt = backfill.run_minute_backfill(
        backfill.MinuteBackfillRunOptions(
            plan_path=plan["plan_path"],
            receipt_path=receipt_path,
            api_url="https://minute.example.test/api",
            dry_run=True,
        )
    )

    assert receipt["status"] == "dry_run"
    assert receipt["fetch_vintage"] is None
    assert receipt_path.is_file()
    assert not Path(plan["output"]["data_dir"]).exists()


def test_endpoint_identifier_drops_credentials_query_and_fragment() -> None:
    identifier = backfill._endpoint_identifier(
        "https://user:password@minute.example.test:8443/api?token=hidden#fragment"
    )

    assert identifier == "https://minute.example.test:8443/api"
    assert "password" not in identifier
    assert "hidden" not in identifier


def _campaign_quota_run_options(
    tmp_path: Path,
    *,
    plan_path: str | Path,
) -> backfill.MinuteBackfillRunOptions:
    return backfill.MinuteBackfillRunOptions(
        plan_path=plan_path,
        receipt_path=tmp_path / "run.receipt.json",
        api_url="https://minute.example.test/api",
        minute_quota_mode="observe",
        minute_quota_db=tmp_path / "quota.sqlite3",
        minute_quota_consumer="campaign",
        minute_quota_limit_rows=80_000_000,
        minute_quota_safety_rows=1_000_000,
        minute_quota_gate="requests",
        minute_quota_limit_requests=10_000,
        minute_quota_burst_limit_requests=20_000,
        minute_quota_safety_requests=500,
        minute_quota_allow_burst=True,
    )


def test_runner_resumes_failed_segment_and_redacts_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = backfill.build_minute_backfill_plan(
        _plan_options(
            tmp_path,
            dates=["20240131", "20240201"],
            instruments=[("000001.SZ", "19910101", "")],
        )
    )
    token = "private-token-value"
    monkeypatch.setenv("TEST_TUSHARE_TOKEN", token)
    calls: list[list[str]] = []

    def first_run(options: MinsMirrorOptions) -> dict[str, object]:
        assert options.exchange is None
        assert options.trading_dates is not None
        assert options.minute_quota_mode == "observe"
        assert options.minute_quota_consumer == "campaign"
        assert options.minute_quota_gate == "requests"
        assert options.minute_quota_limit_requests == 10_000
        assert options.minute_quota_burst_limit_requests == 20_000
        assert options.minute_quota_safety_requests == 500
        assert options.minute_quota_allow_burst is True
        calls.append(options.trading_dates)
        if options.trading_dates == ["20240201"]:
            raise RuntimeError(f"provider failed with {token}")
        return {
            "dates_fetched": 1,
            "dates_skipped": 0,
            "requests_made": 1,
            "total_bars": 241,
            "output_dir": str(options.output_dir),
        }

    monkeypatch.setattr(backfill, "mirror_minute_bars", first_run)
    receipt_path = tmp_path / "run.receipt.json"
    run_options = _campaign_quota_run_options(tmp_path, plan_path=plan["plan_path"])

    partial = backfill.run_minute_backfill(run_options)

    assert partial["status"] == "partial"
    assert calls == [["20240131"], ["20240201"]]
    assert token not in receipt_path.read_text(encoding="utf-8")
    assert partial["segments"][1]["error"]["message"] == "provider failed with <redacted>"
    fetch_vintage = partial["fetch_vintage"]

    resumed_calls: list[list[str]] = []

    def resumed(options: MinsMirrorOptions) -> dict[str, object]:
        assert options.trading_dates is not None
        resumed_calls.append(options.trading_dates)
        return {
            "dates_fetched": 1,
            "dates_skipped": 0,
            "requests_made": 1,
            "total_bars": 241,
            "output_dir": str(options.output_dir),
        }

    monkeypatch.setattr(backfill, "mirror_minute_bars", resumed)
    complete = backfill.run_minute_backfill(run_options)

    assert complete["status"] == "complete"
    assert resumed_calls == [["20240201"]]
    assert complete["fetch_vintage"] == fetch_vintage
    assert complete["totals"]["segments_complete"] == 2
    assert complete["totals"]["dates_complete"] == 2
    assert complete["totals"]["actual_successful_minute_requests"] == 2


def test_runner_stops_if_actual_requests_exceed_plan_upper_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = backfill.build_minute_backfill_plan(
        _plan_options(
            tmp_path,
            dates=["20240102"],
            instruments=[("000001.SZ", "19910101", "")],
        )
    )
    monkeypatch.setenv("TEST_TUSHARE_TOKEN", "test-token")
    monkeypatch.setattr(
        backfill,
        "mirror_minute_bars",
        lambda options: {
            "dates_fetched": 1,
            "dates_skipped": 0,
            "requests_made": 2,
            "total_bars": 241,
            "output_dir": str(options.output_dir),
        },
    )

    receipt = backfill.run_minute_backfill(
        backfill.MinuteBackfillRunOptions(
            plan_path=plan["plan_path"],
            receipt_path=tmp_path / "over-budget.receipt.json",
            api_url="https://minute.example.test/api",
        )
    )

    assert receipt["status"] == "failed"
    assert "exceeded" in receipt["segments"][0]["error"]["message"]
    with pytest.raises(backfill.MinuteBackfillBudgetError, match="prior request-budget"):
        backfill.run_minute_backfill(
            backfill.MinuteBackfillRunOptions(
                plan_path=plan["plan_path"],
                receipt_path=tmp_path / "over-budget.receipt.json",
                api_url="https://minute.example.test/api",
            )
        )


def test_runner_continues_later_segments_after_isolated_partial_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = backfill.build_minute_backfill_plan(
        _plan_options(
            tmp_path,
            dates=["20240131", "20240201"],
            instruments=[("000001.SZ", "19910101", "")],
        )
    )
    monkeypatch.setenv("TEST_TUSHARE_TOKEN", "test-token")
    calls: list[list[str]] = []

    def mirror(options: MinsMirrorOptions) -> dict[str, object]:
        assert options.trading_dates is not None
        assert options.continue_on_partial_dates is True
        calls.append(options.trading_dates)
        result: dict[str, object] = {
            "dates_fetched": 0,
            "dates_skipped": 0,
            "dates_partial": 1,
            "partial_dates": options.trading_dates,
            "requests_made": 1,
            "fallback_requests_made": 1,
            "total_bars": 120,
            "output_dir": str(options.output_dir),
        }
        if options.trading_dates == ["20240131"]:
            raise MinuteMirrorIncompleteDatesError(options.trading_dates, result)
        return {
            **result,
            "dates_fetched": 1,
            "dates_partial": 0,
            "partial_dates": [],
            "fallback_requests_made": 0,
            "total_bars": 241,
        }

    monkeypatch.setattr(backfill, "mirror_minute_bars", mirror)
    receipt = backfill.run_minute_backfill(
        _campaign_quota_run_options(tmp_path, plan_path=plan["plan_path"])
    )

    assert calls == [["20240131"], ["20240201"]]
    assert receipt["status"] == "partial"
    assert [segment["status"] for segment in receipt["segments"]] == [
        "partial",
        "complete",
    ]
    assert receipt["segments"][0]["result"]["partial_dates"] == ["20240131"]
    assert receipt["segments"][0]["result"]["fallback_requests_made"] == 1
    assert receipt["totals"]["actual_successful_minute_requests"] == 1
    assert receipt["totals"]["fallback_successful_minute_requests"] == 0
    assert receipt["totals"]["actual_attempted_minute_requests"] == 3
    assert receipt["totals"]["fallback_attempted_minute_requests"] == 1
    assert receipt["totals"]["persisted_bars"] == 361
    assert receipt["totals"]["dates_partial"] == 1


def _plan_backfill_cli_args(tmp_path: Path) -> list[str]:
    return [
        "tushare",
        "plan-a-share-minute-backfill",
        "--scope",
        "bj-only",
        "--start-date",
        "20211115",
        "--end-date",
        "20211231",
        "--trade-cal",
        str(tmp_path / "cal.parquet"),
        "--instruments",
        str(tmp_path / "instruments.parquet"),
        "--backfill-root",
        str(tmp_path / "backfill"),
        "--plan",
        str(tmp_path / "plan.json"),
        "--dates-file",
        str(tmp_path / "dates.txt"),
        "--request-budget",
        "100",
        "--max-dates",
        "5",
        "--segment",
        "month",
    ]


def _run_backfill_cli_args() -> list[str]:
    return [
        "tushare",
        "run-a-share-minute-backfill",
        "--plan",
        "plan.json",
        "--receipt",
        "receipt.json",
        "--minute-quota-mode",
        "enforce",
        "--minute-quota-db",
        "/tmp/minute-quota.sqlite3",
        "--minute-quota-consumer",
        "replacement_campaign",
        "--minute-quota-limit-rows",
        "80000000",
        "--minute-quota-safety-rows",
        "1000000",
        "--minute-quota-gate",
        "requests",
        "--minute-quota-limit-requests",
        "10000",
        "--minute-quota-burst-limit-requests",
        "20000",
        "--minute-quota-safety-requests",
        "500",
        "--minute-quota-allow-burst",
    ]


def test_minute_backfill_cli_exposes_budget_and_single_worker(tmp_path: Path) -> None:
    parser = build_parser()
    parsed = parser.parse_args(_plan_backfill_cli_args(tmp_path))

    assert parsed.request_budget == 100
    assert parsed.dates_file == str(tmp_path / "dates.txt")
    assert parsed.max_dates == 5
    assert parsed.workers == 1
    assert parsed.cooldown_seconds == 1.0
    run_parsed = parser.parse_args(_run_backfill_cli_args())
    assert run_parsed.minute_quota_mode == "enforce"
    assert run_parsed.minute_quota_consumer == "replacement_campaign"
    assert run_parsed.minute_quota_limit_rows == 80_000_000
    assert run_parsed.minute_quota_safety_rows == 1_000_000
    assert run_parsed.minute_quota_gate == "requests"
    assert run_parsed.minute_quota_limit_requests == 10_000
    assert run_parsed.minute_quota_burst_limit_requests == 20_000
    assert run_parsed.minute_quota_safety_requests == 500
    assert run_parsed.minute_quota_allow_burst is True
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "tushare",
                "run-a-share-minute-backfill",
                "--plan",
                "plan.json",
                "--receipt",
                "receipt.json",
                "--workers",
                "2",
            ]
        )
