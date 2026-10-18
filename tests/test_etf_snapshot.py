"""Bounded acquisition must retain failures without admitting partial data."""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest


def test_snapshot_budget_exhaustion_retains_incomplete(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    class Client:
        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": ["20261016"], "is_open": [1]})
            return pd.DataFrame(
                {
                    "ts_code": ["510300.SH"],
                    "trade_date": ["20261016"],
                    "close": [4.0],
                    "adj_factor": [1.0],
                }
            )

    result = acquire_etf_snapshot(
        symbols=("510300.SH",),
        start_date="20261016",
        end_date="20261016",
        baseline_manifest=None,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        max_requests=1,
        now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
        client=Client(),
    )
    assert result["status"] == "incomplete"
    assert result["request_count"] == 1
    assert (tmp_path / "snapshot" / "manifest.json").is_file()
    assert not (tmp_path / "snapshot" / "panel.parquet").exists()


def test_snapshot_rejects_foreign_symbols_before_network(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    with pytest.raises(ValueError, match="symbol"):
        acquire_etf_snapshot(
            symbols=("AAPL.US",),
            start_date="20261016",
            end_date="20261016",
            baseline_manifest=None,
            output=tmp_path / "snapshot",
            token_env="TUSHARE_TOKEN",
            api_url=None,
            now=lambda: datetime.now(UTC),
            client=object(),
        )
    assert not (tmp_path / "snapshot").exists()


def test_snapshot_is_symbol_bounded_and_hash_bound(tmp_path: Path) -> None:
    import hashlib

    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    calls: list[tuple[str, dict]] = []

    class Client:
        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            calls.append((api, kwargs))
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": ["20261016"], "is_open": [1]})
            values = {"ts_code": [kwargs["ts_code"]], "trade_date": ["20261016"]}
            values.update(
                {"close": [4.0], "amount": [20000.0]}
                if api == "fund_daily"
                else {"adj_factor": [2.0]}
            )
            return pd.DataFrame(values)

    clock = datetime(2026, 10, 16, 11, tzinfo=UTC)
    result = acquire_etf_snapshot(
        symbols=("510300.SH", "511260.SH"),
        start_date="20261016",
        end_date="20261016",
        baseline_manifest=None,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        now=lambda: clock,
        client=Client(),
    )
    assert result["status"] == "unreviewed"
    assert result["request_count"] == 5
    assert result["capture_completed_at"] == clock.isoformat()
    assert {kwargs["ts_code"] for api, kwargs in calls if api != "trade_cal"} == {
        "510300.SH",
        "511260.SH",
    }
    for record in result["files"].values():
        assert (
            hashlib.sha256((tmp_path / "snapshot" / record["path"]).read_bytes()).hexdigest()
            == record["sha256"]
        )
    panel = pd.read_parquet(tmp_path / "snapshot" / "panel.parquet")
    assert panel.loc[0, "510300.SH"] == 4.0


def test_transient_attempts_count_against_budget(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    class Client:
        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            raise OSError("connection reset")

    result = acquire_etf_snapshot(
        symbols=("510300.SH",),
        start_date="20261016",
        end_date="20261016",
        baseline_manifest=None,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        max_requests=2,
        now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
        client=Client(),
    )
    assert result["request_count"] == 2
    assert result["status"] == "incomplete"
    assert result["reasons"] == ["request_budget_exhausted"]


def test_deadline_stops_retry_before_cutoff(tmp_path: Path) -> None:
    from datetime import timedelta

    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    clock = datetime(2026, 10, 16, 10, 59, 59, tzinfo=UTC)

    class Client:
        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            raise OSError("timeout with confidential credential")

    result = acquire_etf_snapshot(
        symbols=("510300.SH",),
        start_date="20261016",
        end_date="20261016",
        baseline_manifest=None,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        now=lambda: clock,
        deadline=clock + timedelta(seconds=1),
        client=Client(),
    )
    assert result["request_count"] == 1
    assert result["reasons"] == ["acquisition_deadline_exceeded"]
    assert "confidential" not in (tmp_path / "snapshot" / "manifest.json").read_text()


def test_snapshot_rejects_missing_calendar_session(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    class Client:
        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": ["20261015", "20261016"], "is_open": [1, 1]})
            data = {"ts_code": ["510300.SH"], "trade_date": ["20261016"]}
            data.update(
                {"close": [4.0], "amount": [20000.0]}
                if api == "fund_daily"
                else {"adj_factor": [1.0]}
            )
            return pd.DataFrame(data)

    result = acquire_etf_snapshot(
        symbols=("510300.SH",),
        start_date="20261015",
        end_date="20261016",
        baseline_manifest=None,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
        client=Client(),
    )
    assert result["status"] == "incomplete"
    assert "missing_calendar_coverage" in result["reasons"]


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    output = tmp_path / "snapshot"
    output.mkdir()
    (output / "manifest.json").write_text("preserved")
    with pytest.raises(FileExistsError):
        acquire_etf_snapshot(
            symbols=("510300.SH",),
            start_date="20261016",
            end_date="20261016",
            baseline_manifest=None,
            output=output,
            token_env="TUSHARE_TOKEN",
            api_url=None,
            now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
            client=object(),
        )
    assert (output / "manifest.json").read_text() == "preserved"


def test_cli_dry_run_never_loads_credentials_or_creates_output(tmp_path: Path, capsys) -> None:
    import json

    from quant_market_data_platform.cli import main

    output = tmp_path / "snapshot"
    status = main(
        [
            "tushare",
            "snapshot-etf-daily",
            "--symbols",
            "510300.SH",
            "511260.SH",
            "--start-date",
            "20261016",
            "--end-date",
            "20261016",
            "--output",
            str(output),
            "--dry-run",
            "--token-env",
            "UNSET_TEST_TOKEN",
        ]
    )
    assert status == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["max_requests"] == 96
    assert report["symbols"] == ["510300.SH", "511260.SH"]
    assert not output.exists()


def test_baseline_hash_change_fails_before_network(tmp_path: Path) -> None:
    import json

    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "daily.parquet").write_bytes(b"changed")
    manifest = baseline / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "marketdata.etf_snapshot.v1",
                "status": "unreviewed",
                "symbols": ["510300.SH"],
                "start_date": "20261015",
                "end_date": "20261015",
                "files": {"daily": {"path": "daily.parquet", "sha256": "0" * 64}},
            }
        )
    )
    result = acquire_etf_snapshot(
        symbols=("510300.SH",),
        start_date="20261016",
        end_date="20261016",
        baseline_manifest=manifest,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
        client=object(),
    )
    assert result["status"] == "incomplete"
    assert result["request_count"] == 0
    assert result["reasons"] == ["baseline_hash_mismatch"]


@pytest.mark.parametrize(
    "next_day,expected_status",
    [
        ("20261016", "unreviewed"),
        ("20261019", "incomplete"),
    ],
)
def test_baseline_factor_rescaling_rebuilds_all_history(
    tmp_path: Path,
    next_day: str,
    expected_status: str,
) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    class Client:
        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            day = kwargs["start_date"]
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": [day], "is_open": [1]})
            if api == "fund_adj":
                dates = pd.date_range(kwargs["start_date"], kwargs["end_date"]).strftime("%Y%m%d")
                return pd.DataFrame(
                    {
                        "ts_code": ["510300.SH"] * len(dates),
                        "trade_date": dates,
                        "adj_factor": [1.0 if d == "20261015" else 2.0 for d in dates],
                    }
                )
            values = {"ts_code": ["510300.SH"], "trade_date": [day]}
            values.update(
                {"close": [4.0], "amount": [20000.0]}
                if api == "fund_daily"
                else {"adj_factor": [1.0 if day == "20261015" else 2.0]}
            )
            return pd.DataFrame(values)

    def acquire(day: str, output: Path, baseline: Path | None):
        return acquire_etf_snapshot(
            symbols=("510300.SH",),
            start_date=day,
            end_date=day,
            baseline_manifest=baseline,
            output=output,
            token_env="TUSHARE_TOKEN",
            api_url=None,
            now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
            client=Client(),
        )

    assert acquire("20261015", tmp_path / "old", None)["status"] == "unreviewed"
    result = acquire(next_day, tmp_path / "new", tmp_path / "old" / "manifest.json")
    assert result["status"] == expected_status
    if expected_status == "incomplete":
        return
    frame = pd.read_parquet(tmp_path / "new" / "panel.parquet")
    assert frame["510300.SH"].tolist() == [2.0, 4.0]
    assert result["start_date"] == "20261015"


def test_internal_endpoint_failover_cannot_escape_request_budget(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot
    from quant_market_data_platform.providers._client import _TushareEndpointFailover

    class Client:
        attempts = 0

        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            self.attempts += 1
            raise OSError("connection reset")

    raw = Client()
    wrapped = _TushareEndpointFailover(raw, ("https://one.invalid", "https://two.invalid"))
    result = acquire_etf_snapshot(
        symbols=("510300.SH",),
        start_date="20261016",
        end_date="20261016",
        baseline_manifest=None,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        max_requests=1,
        now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
        client=wrapped,
    )
    assert result["request_count"] == 1
    assert raw.attempts == 1


def test_network_timeout_is_capped_by_remaining_deadline(tmp_path: Path) -> None:
    from datetime import timedelta

    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    class Client:
        observed_timeout = None

        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            self.observed_timeout = getattr(self, "_DataApi__timeout", None)
            raise ValueError("no permission")

    client = Client()
    clock = datetime(2026, 10, 16, 10, 59, 55, tzinfo=UTC)
    acquire_etf_snapshot(
        symbols=("510300.SH",),
        start_date="20261016",
        end_date="20261016",
        baseline_manifest=None,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        now=lambda: clock,
        deadline=clock + timedelta(seconds=5),
        client=client,
    )
    assert client.observed_timeout == 5.0


@pytest.mark.parametrize("fault", ["duplicate_factor", "missing_factor", "missing_amount"])
def test_invalid_raw_pairs_never_publish_admissible_manifest(tmp_path: Path, fault: str) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    class Client:
        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": ["20261016"], "is_open": [1]})
            values = {"ts_code": ["510300.SH"], "trade_date": ["20261016"]}
            if api == "fund_daily":
                values["close"] = [4.0]
                if fault != "missing_amount":
                    values["amount"] = [20000.0]
            else:
                if fault == "missing_factor":
                    return pd.DataFrame()
                values["adj_factor"] = [1.0]
            frame = pd.DataFrame(values)
            return (
                pd.concat([frame, frame])
                if api == "fund_adj" and fault == "duplicate_factor"
                else frame
            )

    result = acquire_etf_snapshot(
        symbols=("510300.SH",),
        start_date="20261016",
        end_date="20261016",
        baseline_manifest=None,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
        client=Client(),
    )
    assert result["status"] == "incomplete"
    assert result["reasons"]


def test_repeated_page_stops_without_consuming_full_budget(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    class Client:
        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": ["20261016"], "is_open": [1]})
            return pd.DataFrame(
                {
                    "ts_code": ["510300.SH"] * 5000,
                    "trade_date": ["20261016"] * 5000,
                    "close": [4.0] * 5000,
                    "amount": [20000.0] * 5000,
                }
            )

    result = acquire_etf_snapshot(
        symbols=("510300.SH",),
        start_date="20261016",
        end_date="20261016",
        baseline_manifest=None,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
        client=Client(),
    )
    assert result["status"] == "incomplete"
    assert result["request_count"] <= 3
    assert result["reasons"] == ["repeated_provider_page"]


def test_failed_factor_request_retains_received_daily_page(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    class Client:
        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": ["20261016"], "is_open": [1]})
            if api == "fund_adj":
                raise ValueError("permission denied")
            return pd.DataFrame(
                {
                    "ts_code": ["510300.SH"],
                    "trade_date": ["20261016"],
                    "close": [4.0],
                    "amount": [20000.0],
                }
            )

    result = acquire_etf_snapshot(
        symbols=("510300.SH",),
        start_date="20261016",
        end_date="20261016",
        baseline_manifest=None,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
        client=Client(),
    )
    assert result["status"] == "incomplete"
    page = tmp_path / "snapshot/raw-pages/fund_daily-510300.SH-0.parquet"
    assert pd.read_parquet(page).close.tolist() == [4.0]


def test_revised_historical_factors_block_incremental_publication(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    class Client:
        revised = False

        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            dates = pd.date_range(kwargs["start_date"], kwargs["end_date"]).strftime("%Y%m%d")
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": dates, "is_open": 1})
            data = {"ts_code": ["510300.SH"] * len(dates), "trade_date": dates}
            data.update(
                {"close": [4.0] * len(dates), "amount": [20000.0] * len(dates)}
                if api == "fund_daily"
                else {"adj_factor": [2.0 if self.revised else 1.0] * len(dates)}
            )
            return pd.DataFrame(data)

    client = Client()

    def acquire(start, end, name, baseline=None):
        return acquire_etf_snapshot(
            symbols=("510300.SH",),
            start_date=start,
            end_date=end,
            baseline_manifest=baseline,
            output=tmp_path / name,
            token_env="TUSHARE_TOKEN",
            api_url=None,
            now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
            client=client,
        )

    assert acquire("20261015", "20261015", "old")["status"] == "unreviewed"
    client.revised = True
    fresh = acquire("20261015", "20261016", "fresh")
    assert fresh["status"] == "unreviewed"
    result = acquire("20261016", "20261016", "incremental", tmp_path / "old/manifest.json")
    assert result["status"] == "incomplete"
    assert result["reasons"] == ["historical_factor_revision"]


def test_factor_endpoint_paginates_after_2000_rows(tmp_path: Path) -> None:
    from quant_market_data_platform.etf_snapshot import acquire_etf_snapshot

    dates = pd.bdate_range("2017-01-02", periods=2001).strftime("%Y%m%d")

    class Client:
        def query(self, api: str, **kwargs: object) -> pd.DataFrame:
            if api == "trade_cal":
                days = pd.date_range(dates[0], dates[-1]).strftime("%Y%m%d")
                return pd.DataFrame({"cal_date": days, "is_open": [int(d in dates) for d in days]})
            size = 2000 if api == "fund_adj" else 5000
            offset = kwargs["offset"]
            assert isinstance(offset, int)
            selected = dates[offset : offset + size]
            data = {"ts_code": ["510300.SH"] * len(selected), "trade_date": selected}
            data.update(
                {"close": [4.0] * len(selected), "amount": [20000.0] * len(selected)}
                if api == "fund_daily"
                else {"adj_factor": [1.0] * len(selected)}
            )
            return pd.DataFrame(data)

    result = acquire_etf_snapshot(
        symbols=("510300.SH",),
        start_date=dates[0],
        end_date=dates[-1],
        baseline_manifest=None,
        output=tmp_path / "snapshot",
        token_env="TUSHARE_TOKEN",
        api_url=None,
        now=lambda: datetime(2026, 10, 16, 11, tzinfo=UTC),
        client=Client(),
    )
    assert result["status"] == "unreviewed"
    assert len(pd.read_parquet(tmp_path / "snapshot/panel.parquet")) == 2001
