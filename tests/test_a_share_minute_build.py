from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
import pytest

from market_data_platform.providers import a_share_minute_build
from market_data_platform.providers.a_share_minute_build import (
    MinuteFusionBuildOptions,
    build_fused_minute_dataset,
    validate_fused_minute_dataset,
)
from market_data_platform.providers.a_share_minute_fusion import (
    CANONICAL_MINUTE_COLUMNS,
    CANONICAL_MINUTE_SCHEMA,
    write_canonical_minute_partition,
)


def _trade_time(date: str, clock: str = "09:31:00") -> str:
    return f"{date[:4]}-{date[4:6]}-{date[6:]} {clock}"


def _minute_row(date: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ts_code": "000001.SZ",
        "trade_time": _trade_time(date),
        "open": 10.0,
        "close": 10.1,
        "high": 10.2,
        "low": 9.9,
        "vol": 100.0,
        "amount": 1_000.0,
    }
    row.update(overrides)
    return row


def _write_legacy_partition(
    root: Path,
    date: str,
    rows: list[dict[str, object]],
) -> Path:
    path = root / f"trade_date={date}" / "part-00000.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def _write_deal_file(root: Path, date: str) -> Path:
    path = root / f"deal_{date}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "BizIndex": [1, 2],
            "DealTime": [93_000_000, 93_000_000],
            "Price": [1_000, 2_000],
            "SecuCode": [1, 600_000],
            "TradingDay": [int(date), int(date)],
            "Volume": [100, 10],
        }
    ).to_parquet(path, index=False)
    return path


def _options(tmp_path: Path, **overrides: object) -> MinuteFusionBuildOptions:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir(exist_ok=True)
    values: dict[str, Any] = {
        "legacy_input_dir": legacy_root,
        "output_dir": tmp_path / "output",
        "manifest_path": tmp_path / "metadata" / "manifest.json",
        "legacy_workers": 1,
    }
    values.update(overrides)
    return MinuteFusionBuildOptions(**values)


@pytest.mark.parametrize("override", [False, True])
def test_materialization_worker_preserves_checkpoint_and_override_behavior(
    tmp_path: Path,
    override: bool,
) -> None:
    from market_data_platform.standardize.fusion.a_share_minute import aggregate_guan_deal_file
    from market_data_platform.standardize.materialize.a_share_minute.inventory import (
        _MinuteSourceInventory,
    )
    from market_data_platform.standardize.materialize.a_share_minute.workers import (
        _merge_deal_inputs,
    )

    date = "20260706"
    source = _write_deal_file(tmp_path / "deals", date)
    if override:
        deals = pd.read_parquet(source)
        deals["DealTime"] = 92_500_000
        closing = deals.copy()
        closing["BizIndex"] += 10
        closing["DealTime"] = 150_000_000
        pd.concat([deals, closing], ignore_index=True).to_parquet(source, index=False)
    inventory = _MinuteSourceInventory(
        legacy={},
        guan_deal={} if override else {date: source},
        override_guan_deal={date: source} if override else {},
        protected_guan_deal={},
        protected_output_dates=set(),
        tushare_batches={},
    )
    options = _options(tmp_path, guan_deal_start_date=date, deal_engine="pandas")
    receipts = []
    actions = _merge_deal_inputs(
        options,
        {},
        source_inventory=inventory,
        aggregate_deal=aggregate_guan_deal_file,
        checkpoint_action=receipts.append,
    )
    path = Path(options.output_dir) / f"trade_date={date}" / "part-00000.parquet"
    before = path.stat().st_mtime_ns
    assert actions == receipts
    assert actions[0]["fusion_priority"] == ["guan_deal"]
    assert actions[0]["whole_day_annual_override"] is override
    if override:
        assert actions[0]["fusion"] is None
        assert actions[0]["session_validation"]["valid"] is True
    resumed = _merge_deal_inputs(
        options,
        {},
        source_inventory=inventory,
        aggregate_deal=aggregate_guan_deal_file,
        resume_actions={date: actions[0]},
        checkpoint_action=receipts.append,
    )
    assert resumed[0]["checkpoint_status"] == "reused"
    assert len(receipts) == 1
    assert path.stat().st_mtime_ns == before


@pytest.mark.parametrize(
    "date,expected_close,priority",
    [
        ("20251231", 10.0, ["guan_deal", "guan_legacy"]),
        ("20260706", 50.0, ["tushare_existing", "guan_deal"]),
    ],
)
def test_materialization_worker_preserves_original_source_priority(
    tmp_path: Path,
    date: str,
    expected_close: float,
    priority: list[str],
) -> None:
    from market_data_platform.standardize.fusion.a_share_minute import aggregate_guan_deal_file
    from market_data_platform.standardize.materialize.a_share_minute.inventory import (
        _MinuteSourceInventory,
    )
    from market_data_platform.standardize.materialize.a_share_minute.workers import (
        _merge_deal_inputs,
    )

    source = _write_deal_file(tmp_path / "deals", date)
    original = tmp_path / "legacy" / f"trade_date={date}" / "part-00000.parquet"
    write_canonical_minute_partition(
        pd.DataFrame([_minute_row(date, open=50.0, close=50.0, low=50.0, high=50.0)]),
        original,
    )
    inventory = _MinuteSourceInventory(
        legacy={date: original},
        guan_deal={date: source},
        override_guan_deal={},
        protected_guan_deal={},
        protected_output_dates=set(),
        tushare_batches={},
    )
    options = _options(tmp_path, guan_deal_start_date=date, deal_engine="pandas")
    actions = _merge_deal_inputs(
        options,
        {},
        source_inventory=inventory,
        aggregate_deal=aggregate_guan_deal_file,
    )
    output = pd.read_parquet(Path(options.output_dir) / f"trade_date={date}" / "part-00000.parquet")
    assert actions[0]["fusion_priority"] == priority
    assert output.loc[output["ts_code"].eq("000001.SZ"), "close"].tolist() == [expected_close]


def test_dry_run_discovers_filtered_sources_without_writing(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    planned_legacy = legacy_root / "trade_date=20260115" / "part-00000.parquet"
    ignored_legacy = legacy_root / "trade_date=20251231" / "part-00000.parquet"
    for path in (planned_legacy, ignored_legacy):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    deal_root = tmp_path / "deals"
    deal_root.mkdir()
    (deal_root / "deal_20260706.parquet").touch()
    (deal_root / "deal_20260531.parquet").touch()

    tushare_root = tmp_path / "tushare"
    tushare_root.mkdir()
    (tushare_root / "minute_20260706_batch1.parquet").touch()
    (tushare_root / "minute_20260706_batch2.parquet").touch()
    (tushare_root / "minute_20260707_batch1.parquet").touch()
    (tushare_root / "minute_20260706_other.parquet").touch()

    options = _options(
        tmp_path,
        start_date="20260101",
        end_date="20260706",
        dry_run=True,
        guan_deal_dir=deal_root,
        tushare_batch_dir=tushare_root,
    )
    payload = build_fused_minute_dataset(options)

    assert payload["status"] == "planned"
    assert payload["validation"] == {
        "status": "not_run",
        "partition_count": 0,
        "partitions": [],
    }
    assert payload["build_actions"]["legacy"] == [
        {
            "date": "20260115",
            "status": "planned",
            "source": "guan_legacy:hundred_x_to_canonical",
        }
    ]
    assert payload["build_actions"]["guan_deal"] == [
        {"date": "20260706", "status": "planned", "source": "guan_deal"}
    ]
    assert payload["build_actions"]["tushare_batches"] == [
        {
            "date": "20260706",
            "status": "planned",
            "source": "tushare_batches",
            "files": 2,
        }
    ]
    assert not Path(options.output_dir).exists()
    assert not Path(options.manifest_path).exists()


def test_build_applies_date_dependent_legacy_scaling_and_audits_regimes(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "legacy"
    _write_legacy_partition(
        legacy_root,
        "20251231",
        [_minute_row("20251231", vol=123.0, amount=4_567.0)],
    )
    _write_legacy_partition(
        legacy_root,
        "20260115",
        [_minute_row("20260115", vol=12_300.0, amount=456_700.0)],
    )
    _write_legacy_partition(
        legacy_root,
        "20260424",
        [_minute_row("20260424", vol=321.0, amount=7_654.0)],
    )
    options = _options(
        tmp_path,
        start_date="20251231",
        end_date="20260424",
        hundred_x_start_date="20260101",
        hundred_x_end_date="20260331",
    )

    payload = build_fused_minute_dataset(options)

    expected_flows = {
        "20251231": (123.0, 4_567.0),
        "20260115": (123.0, 4_567.0),
        "20260424": (321.0, 7_654.0),
    }
    for date, (expected_vol, expected_amount) in expected_flows.items():
        output = pd.read_parquet(
            Path(options.output_dir) / f"trade_date={date}" / "part-00000.parquet"
        )
        assert output.loc[0, "vol"] == pytest.approx(expected_vol)
        assert output.loc[0, "amount"] == pytest.approx(expected_amount)

    legacy_regimes = [
        regime for regime in payload["unit_regimes"] if regime["source"] == "legacy_guan"
    ]
    assert legacy_regimes == [
        {
            "source": "legacy_guan",
            "start_date": "20251231",
            "end_date": "20251231",
            "profile": "already_canonical",
        },
        {
            "source": "legacy_guan",
            "start_date": "20260101",
            "end_date": "20260331",
            "profile": "hundred_x_to_canonical",
        },
        {
            "source": "legacy_guan",
            "start_date": "20260401",
            "end_date": "20260424",
            "profile": "already_canonical",
        },
    ]


def test_build_audits_dropped_legacy_empty_placeholders(tmp_path: Path) -> None:
    date = "20251231"
    empty = _minute_row(date)
    for column in CANONICAL_MINUTE_COLUMNS[2:]:
        empty[column] = None
    _write_legacy_partition(tmp_path / "legacy", date, [_minute_row(date), empty])
    options = _options(tmp_path, start_date=date, end_date=date)

    payload = build_fused_minute_dataset(options)

    action = payload["build_actions"]["legacy"][0]
    assert action["normalization"]["input_rows"] == 2
    assert action["normalization"]["dropped_empty_rows"] == 1
    assert action["rows"] == 1


def test_build_filters_non_equity_instrument_mapping_rows(tmp_path: Path) -> None:
    date = "20260706"
    deal_root = tmp_path / "deals"
    _write_deal_file(deal_root, date)
    instruments = tmp_path / "instruments.parquet"
    pd.DataFrame(
        {
            "symbol": ["000001.SZ", "600000.SH", "T00018.SH"],
            "ts_code": ["000001.SZ", "600000.SH", "T00018.SH"],
        }
    ).to_parquet(instruments, index=False)
    options = _options(
        tmp_path,
        start_date=date,
        end_date=date,
        guan_deal_dir=deal_root,
        instruments_path=instruments,
    )

    payload = build_fused_minute_dataset(options)

    assert payload["status"] == "passed"
    assert payload["source_priority"] == ["guan"]
    assert payload["symbol_mapping"]["input_rows"] == 3
    assert payload["symbol_mapping"]["dropped_invalid_rows"] == 1
    assert payload["symbol_mapping"]["mapping_entries"] == 2


def test_build_orchestrates_guan_then_tushare_with_tushare_priority(
    tmp_path: Path,
) -> None:
    date = "20260706"
    deal_root = tmp_path / "deals"
    _write_deal_file(deal_root, date)
    tushare_root = tmp_path / "tushare"
    tushare_root.mkdir()
    pd.DataFrame(
        [
            _minute_row(
                date,
                open=11.0,
                close=11.5,
                high=12.0,
                low=10.5,
                vol=15.0,
                amount=172.5,
            ),
            _minute_row(
                date,
                ts_code="000002.SZ",
                open=8.0,
                close=8.1,
                high=8.2,
                low=7.9,
                vol=20.0,
                amount=162.0,
            ),
        ]
    ).assign(trade_date=date, provider_extra="projected away").to_parquet(
        tushare_root / f"minute_{date}_batch1.parquet",
        index=False,
    )
    instruments = tmp_path / "instruments.parquet"
    pd.DataFrame(
        {
            "symbol": ["000001", "600000"],
            "ts_code": ["000001.SZ", "600000.SH"],
        }
    ).to_parquet(instruments, index=False)
    options = _options(
        tmp_path,
        start_date=date,
        end_date=date,
        guan_deal_dir=deal_root,
        tushare_batch_dir=tushare_root,
        instruments_path=instruments,
    )

    payload = build_fused_minute_dataset(options)
    output_path = Path(options.output_dir) / f"trade_date={date}" / "part-00000.parquet"
    output = pd.read_parquet(output_path)

    assert set(output["ts_code"]) == {"000001.SZ", "000002.SZ", "600000.SH"}
    overlap = output.loc[output["ts_code"].eq("000001.SZ")].iloc[0]
    assert overlap["close"] == pytest.approx(11.5)
    assert output.loc[output["ts_code"].eq("600000.SH"), "close"].iloc[0] == pytest.approx(20.0)
    assert not output.duplicated(["ts_code", "trade_time"]).any()
    assert payload["source_priority"] == ["tushare", "guan"]
    tushare_fusion = payload["build_actions"]["tushare_batches"][0]["fusion"]
    assert tushare_fusion["overlap_rows"] == 1
    assert tushare_fusion["output_rows_by_source"] == {"guan": 1, "tushare": 2}

    rerun = build_fused_minute_dataset(options)
    rerun_output = pd.read_parquet(output_path)
    pd.testing.assert_frame_equal(rerun_output, output)
    assert rerun["validation"]["status"] == "passed"
    assert not rerun_output.duplicated(["ts_code", "trade_time"]).any()


def test_rebuild_does_not_treat_previous_fused_output_as_a_source(tmp_path: Path) -> None:
    date = "20260706"
    deal_root = tmp_path / "deals"
    deal_path = _write_deal_file(deal_root, date)
    tushare_root = tmp_path / "tushare"
    tushare_root.mkdir()
    batch_path = tushare_root / f"minute_{date}_batch1.parquet"
    pd.DataFrame(
        [
            _minute_row(date, close=11.0, high=11.1),
            _minute_row(
                date,
                ts_code="000002.SZ",
                open=8.0,
                close=8.0,
                high=8.2,
                low=7.9,
            ),
        ]
    ).to_parquet(batch_path, index=False)
    options = _options(
        tmp_path,
        start_date=date,
        end_date=date,
        guan_deal_dir=deal_root,
        tushare_batch_dir=tushare_root,
    )
    build_fused_minute_dataset(options)

    deals = pd.read_parquet(deal_path)
    deals.loc[deals["SecuCode"].eq(600_000), "Price"] = 3_000
    deals.to_parquet(deal_path, index=False)
    pd.DataFrame([_minute_row(date, close=12.0, high=12.1)]).to_parquet(
        batch_path,
        index=False,
    )

    build_fused_minute_dataset(options)
    output = pd.read_parquet(Path(options.output_dir) / f"trade_date={date}" / "part-00000.parquet")

    assert output.loc[output["ts_code"].eq("000001.SZ"), "close"].iloc[0] == pytest.approx(12.0)
    assert output.loc[output["ts_code"].eq("600000.SH"), "close"].iloc[0] == pytest.approx(30.0)
    assert "000002.SZ" not in set(output["ts_code"])


def test_deal_resume_excludes_output_directory_as_an_input(tmp_path: Path) -> None:
    date = "20260706"
    deal_root = tmp_path / "deals"
    deal_path = _write_deal_file(deal_root, date)
    output_root = tmp_path / "output"
    output_root.mkdir()
    options = MinuteFusionBuildOptions(
        legacy_input_dir=output_root,
        output_dir=output_root,
        manifest_path=tmp_path / "metadata" / "deal.json",
        start_date=date,
        end_date=date,
        guan_deal_dir=deal_root,
        guan_deal_start_date=date,
        deal_engine="pandas",
    )

    first = build_fused_minute_dataset(options)
    deals = pd.read_parquet(deal_path)
    deals.loc[deals["SecuCode"].eq(600_000), "Price"] = 3_000
    deals.to_parquet(deal_path, index=False)
    second = build_fused_minute_dataset(options)

    output = pd.read_parquet(output_root / f"trade_date={date}" / "part-00000.parquet")
    assert output.loc[output["ts_code"].eq("600000.SH"), "close"].iloc[0] == pytest.approx(30.0)
    assert first["source_priority"] == ["guan"]
    assert second["source_priority"] == ["guan"]
    assert second["source_inventory"]["legacy"] == {
        "date_count": 0,
        "self_output_excluded": True,
        "files": {},
    }
    assert second["build_actions"]["guan_deal"][0]["fusion_priority"] == ["guan_deal"]


def test_deal_build_protects_annual_dates_at_date_level(tmp_path: Path) -> None:
    date = "20260302"
    deal_root = tmp_path / "deals"
    _write_deal_file(deal_root, date)
    output_root = tmp_path / "output"
    annual_path = output_root / f"trade_date={date}" / "part-00000.parquet"
    write_canonical_minute_partition(
        pd.DataFrame([_minute_row(date, close=77.0, high=77.0)]), annual_path
    )
    options = MinuteFusionBuildOptions(
        legacy_input_dir=output_root,
        output_dir=output_root,
        manifest_path=tmp_path / "metadata" / "deal.json",
        start_date=date,
        end_date=date,
        guan_deal_dir=deal_root,
        guan_deal_start_date=date,
        protected_dates=(date,),
        deal_engine="pandas",
    )

    payload = build_fused_minute_dataset(options)

    assert payload["status"] == "passed"
    assert pd.read_parquet(annual_path).loc[0, "close"] == pytest.approx(77.0)
    assert payload["build_actions"]["guan_deal"] == [
        {
            "date": date,
            "status": "audit_only_protected",
            "source": "guan_deal",
            "protected_source": "guan_annual_minbar",
        }
    ]
    assert payload["source_inventory"]["protected_guan_deal"]["date_count"] == 1


def test_explicit_annual_override_replaces_whole_day_with_strict_deal_partition(
    tmp_path: Path,
) -> None:
    date = "20260302"
    deal_root = tmp_path / "deals"
    deal_path = _write_deal_file(deal_root, date)
    deals = pd.read_parquet(deal_path)
    deals["DealTime"] = 92_500_000
    closing = deals.copy()
    closing["BizIndex"] += 10
    closing["DealTime"] = 150_000_000
    pd.concat([deals, closing], ignore_index=True).to_parquet(deal_path, index=False)

    output_root = tmp_path / "output"
    annual_path = output_root / f"trade_date={date}" / "part-00000.parquet"
    write_canonical_minute_partition(
        pd.DataFrame([_minute_row(date, close=77.0, high=77.0)]), annual_path
    )
    options = MinuteFusionBuildOptions(
        legacy_input_dir=output_root,
        output_dir=output_root,
        manifest_path=tmp_path / "metadata" / "deal.json",
        start_date=date,
        end_date=date,
        guan_deal_dir=deal_root,
        guan_deal_start_date=date,
        protected_dates=(date,),
        annual_override_dates=(date,),
        deal_engine="pandas",
    )

    payload = build_fused_minute_dataset(options)

    output = pd.read_parquet(annual_path)
    assert payload["status"] == "passed"
    assert set(output["trade_time"].dt.strftime("%H:%M:%S")) == {"09:30:00", "15:00:00"}
    assert 77.0 not in set(output["close"])
    action = payload["build_actions"]["guan_deal"][0]
    assert action["replacement_policy"] == "explicit_whole_day_deal_only"
    assert action["replaced_source"] == "guan_annual_minbar"
    assert action["fusion_priority"] == ["guan_deal"]
    assert action["fusion"] is None
    assert action["session_validation"]["time_min"] == f"{date[:4]}-{date[4:6]}-{date[6:]} 09:30:00"
    assert action["session_validation"]["time_max"] == f"{date[:4]}-{date[4:6]}-{date[6:]} 15:00:00"
    assert action["session_validation"]["opening_bar_symbols"] == 2
    assert action["session_validation"]["closing_bar_symbols"] == 2
    assert action["session_validation"]["valid"] is True
    assert payload["annual_overrides"] == {
        "policy": "explicit_whole_day_deal_only",
        "count": 1,
        "dates": [date],
    }
    assert payload["source_inventory"]["override_guan_deal"]["date_count"] == 1


def test_explicit_annual_override_rejects_truncated_deal_and_preserves_annual(
    tmp_path: Path,
) -> None:
    date = "20260302"
    deal_root = tmp_path / "deals"
    _write_deal_file(deal_root, date)
    output_root = tmp_path / "output"
    annual_path = output_root / f"trade_date={date}" / "part-00000.parquet"
    write_canonical_minute_partition(
        pd.DataFrame([_minute_row(date, close=77.0, high=77.0)]), annual_path
    )
    original = annual_path.read_bytes()
    options = MinuteFusionBuildOptions(
        legacy_input_dir=output_root,
        output_dir=output_root,
        manifest_path=tmp_path / "metadata" / "deal.json",
        start_date=date,
        end_date=date,
        guan_deal_dir=deal_root,
        guan_deal_start_date=date,
        protected_dates=(date,),
        annual_override_dates=(date,),
        deal_engine="pandas",
    )

    with pytest.raises(ValueError, match="whole-day annual override"):
        build_fused_minute_dataset(options)

    assert annual_path.read_bytes() == original
    assert not Path(options.manifest_path).exists()


def test_annual_override_must_be_explicitly_protected(tmp_path: Path) -> None:
    date = "20260302"
    with pytest.raises(ValueError, match="protected annual dates"):
        MinuteFusionBuildOptions(
            legacy_input_dir=tmp_path,
            output_dir=tmp_path / "output",
            manifest_path=tmp_path / "manifest.json",
            start_date=date,
            end_date=date,
            annual_override_dates=(date,),
        )


def test_deal_checkpoint_resumes_after_interruption_and_no_resume_rebuilds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_date = "20260608"
    second_date = "20260609"
    deal_root = tmp_path / "deals"
    _write_deal_file(deal_root, first_date)
    _write_deal_file(deal_root, second_date)
    options = _options(
        tmp_path,
        start_date=first_date,
        end_date=second_date,
        guan_deal_dir=deal_root,
        guan_deal_start_date=first_date,
        deal_engine="pandas",
    )
    real_aggregate = a_share_minute_build.aggregate_guan_deal_file
    first_calls: list[str] = []

    def interrupt_second(source_path, **kwargs):
        trade_date = str(kwargs["trade_date"])
        first_calls.append(trade_date)
        if trade_date == second_date:
            raise RuntimeError("simulated interruption")
        return real_aggregate(source_path, **kwargs)

    monkeypatch.setattr(a_share_minute_build, "aggregate_guan_deal_file", interrupt_second)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        build_fused_minute_dataset(options)

    checkpoint_path = Path(options.manifest_path).with_name(
        f".{Path(options.manifest_path).name}.deal-checkpoint.json"
    )
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert first_calls == [first_date, second_date]
    assert interrupted["status"] == "failed"
    assert set(interrupted["completed_actions"]) == {first_date}
    assert not Path(options.manifest_path).exists()

    resumed_calls: list[str] = []

    def track_resume(source_path, **kwargs):
        resumed_calls.append(str(kwargs["trade_date"]))
        return real_aggregate(source_path, **kwargs)

    monkeypatch.setattr(a_share_minute_build, "aggregate_guan_deal_file", track_resume)
    payload = build_fused_minute_dataset(options)

    assert resumed_calls == [second_date]
    assert payload["status"] == "passed"
    assert payload["deal_checkpoint"]["reused_dates"] == [first_date]
    assert payload["deal_checkpoint"]["written_dates"] == [second_date]
    assert [action["date"] for action in payload["build_actions"]["guan_deal"]] == [
        first_date,
        second_date,
    ]
    assert all(action["status"] == "written" for action in payload["build_actions"]["guan_deal"])
    completed = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert completed["status"] == "complete"
    assert set(completed["completed_actions"]) == {first_date, second_date}

    no_resume_calls: list[str] = []

    def track_no_resume(source_path, **kwargs):
        no_resume_calls.append(str(kwargs["trade_date"]))
        return real_aggregate(source_path, **kwargs)

    monkeypatch.setattr(a_share_minute_build, "aggregate_guan_deal_file", track_no_resume)
    build_fused_minute_dataset(replace(options, resume=False))
    assert no_resume_calls == [first_date, second_date]


def test_deal_checkpoint_rebuilds_tampered_canonical_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_date = "20260608"
    second_date = "20260609"
    deal_root = tmp_path / "deals"
    _write_deal_file(deal_root, first_date)
    _write_deal_file(deal_root, second_date)
    options = _options(
        tmp_path,
        start_date=first_date,
        end_date=second_date,
        guan_deal_dir=deal_root,
        guan_deal_start_date=first_date,
        deal_engine="pandas",
    )
    build_fused_minute_dataset(options)
    tampered_path = Path(options.output_dir) / f"trade_date={first_date}" / "part-00000.parquet"
    write_canonical_minute_partition(
        pd.DataFrame([_minute_row(first_date, close=99.0, high=99.0)]),
        tampered_path,
    )

    real_aggregate = a_share_minute_build.aggregate_guan_deal_file
    calls: list[str] = []

    def track_rebuild(source_path, **kwargs):
        calls.append(str(kwargs["trade_date"]))
        return real_aggregate(source_path, **kwargs)

    monkeypatch.setattr(a_share_minute_build, "aggregate_guan_deal_file", track_rebuild)
    payload = build_fused_minute_dataset(options)

    assert calls == [first_date]
    assert payload["deal_checkpoint"]["written_dates"] == [first_date]
    assert payload["deal_checkpoint"]["reused_dates"] == [second_date]
    rebuilt = pd.read_parquet(tampered_path)
    assert 99.0 not in set(rebuilt["close"])


def test_manifest_validation_and_canonical_schema(tmp_path: Path) -> None:
    date = "20251231"
    _write_legacy_partition(tmp_path / "legacy", date, [_minute_row(date)])
    options = _options(tmp_path, start_date=date, end_date=date)

    payload = build_fused_minute_dataset(options)
    output_path = Path(options.output_dir) / f"trade_date={date}" / "part-00000.parquet"
    persisted_manifest = json.loads(Path(options.manifest_path).read_text(encoding="utf-8"))

    assert persisted_manifest == payload
    assert payload["status"] == "passed"
    assert payload["validation"]["partition_count"] == 1
    assert payload["validation"]["rows"] == 1
    assert payload["validation"]["invalid_dates"] == []
    assert payload["validation"]["missing_dates"] == []
    assert payload["validation"]["orphan_dates"] == []
    assert payload["validation"]["partitions"][0]["valid"] is True
    inventory = payload["source_inventory"]
    assert inventory["expected_output_dates"] == [date]
    assert inventory["legacy"]["date_count"] == 1
    source_inventory = inventory["legacy"]["files"][date]
    assert source_inventory["path"] == str(
        Path(options.legacy_input_dir) / f"trade_date={date}" / "part-00000.parquet"
    )
    assert source_inventory["size"] > 0
    assert source_inventory["mtime_ns"] > 0
    assert pq.read_schema(output_path).equals(CANONICAL_MINUTE_SCHEMA)
    assert list(pd.read_parquet(output_path).columns) == list(CANONICAL_MINUTE_COLUMNS)
    assert not list(Path(options.manifest_path).parent.glob(".manifest.json.*.tmp"))

    invalid = pd.DataFrame(
        [
            _minute_row(
                date,
                trade_time=_trade_time("20260101"),
                open=10.0,
                close=12.0,
                high=11.0,
                low=10.5,
                vol=-1.0,
            )
        ]
    )
    invalid = pd.concat([invalid, invalid], ignore_index=True)
    write_canonical_minute_partition(invalid, output_path)

    validation = validate_fused_minute_dataset(options.output_dir)
    issues = validation["partitions"][0]["issues"]
    assert validation["status"] == "failed"
    assert validation["invalid_dates"] == [date]
    assert issues["duplicate_rows"] == 1
    assert issues["invalid_ohlc_rows"] == 2
    assert issues["negative_flow_rows"] == 2
    assert issues["wrong_date_rows"] == 2


def test_candidate_validation_preserves_existing_partition(tmp_path: Path) -> None:
    date = "20260706"
    output_path = tmp_path / "output" / f"trade_date={date}" / "part-00000.parquet"
    write_canonical_minute_partition(pd.DataFrame([_minute_row(date, close=7.0)]), output_path)
    original_bytes = output_path.read_bytes()

    tushare_root = tmp_path / "tushare"
    tushare_root.mkdir()
    pd.DataFrame([_minute_row(date, trade_time=_trade_time(date, "12:00:00"))]).to_parquet(
        tushare_root / f"minute_{date}_batch1.parquet",
        index=False,
    )
    options = _options(
        tmp_path,
        start_date=date,
        end_date=date,
        tushare_batch_dir=tushare_root,
    )

    with pytest.raises(ValueError, match="off_session_rows"):
        build_fused_minute_dataset(options)

    assert output_path.read_bytes() == original_bytes
    assert not Path(options.manifest_path).exists()


def test_validation_rejects_empty_and_reports_range_coverage(tmp_path: Path) -> None:
    empty = validate_fused_minute_dataset(tmp_path / "empty")
    assert empty["status"] == "failed"
    assert empty["empty_dataset"] is True

    output_root = tmp_path / "output"
    output_date = "20250102"
    expected_date = "20250103"
    write_canonical_minute_partition(
        pd.DataFrame([_minute_row(output_date)]),
        output_root / f"trade_date={output_date}" / "part-00000.parquet",
    )
    coverage = validate_fused_minute_dataset(
        output_root,
        expected_dates={expected_date},
        start_date=output_date,
        end_date=expected_date,
    )

    assert coverage["status"] == "failed"
    assert coverage["missing_dates"] == [expected_date]
    assert coverage["orphan_dates"] == [output_date]


def test_validation_checks_extended_canonical_contract(tmp_path: Path) -> None:
    date = "20251231"
    rows = [
        _minute_row(date),
        _minute_row(date),
        _minute_row(date, ts_code="000002.SZ", open=float("inf")),
        _minute_row(date, ts_code="000003.SZ", close=12.0, high=11.0),
        _minute_row(date, ts_code="000004.SZ", vol=-1.0),
        _minute_row(date, ts_code="000005.SZ", trade_time=_trade_time("20260101")),
        _minute_row(date, ts_code="BAD"),
        _minute_row(date, ts_code="000006.SZ", trade_time=_trade_time(date, "09:31:30")),
        _minute_row(date, ts_code="000007.SZ", trade_time=_trade_time(date, "12:00:00")),
    ]
    output_path = tmp_path / "output" / f"trade_date={date}" / "part-00000.parquet"
    write_canonical_minute_partition(pd.DataFrame(rows), output_path)

    validation = validate_fused_minute_dataset(tmp_path / "output")
    issues = validation["partitions"][0]["issues"]

    assert validation["status"] == "failed"
    assert issues["duplicate_rows"] == 1
    assert issues["non_finite_rows"] == 1
    assert issues["invalid_ohlc_rows"] >= 1
    assert issues["negative_flow_rows"] == 1
    assert issues["wrong_date_rows"] == 1
    assert issues["invalid_ts_code_rows"] == 1
    assert issues["non_minute_rows"] == 1
    assert issues["off_session_rows"] == 1


def test_build_expands_user_paths_and_rejects_missing_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    date = "20251231"
    _write_legacy_partition(home / "legacy", date, [_minute_row(date)])
    options = MinuteFusionBuildOptions(
        legacy_input_dir="~/legacy",
        output_dir="~/output",
        manifest_path="~/metadata/manifest.json",
        start_date=date,
        end_date=date,
    )

    payload = build_fused_minute_dataset(options)

    assert (home / "output" / f"trade_date={date}" / "part-00000.parquet").is_file()
    assert (home / "metadata" / "manifest.json").is_file()
    assert payload["options"]["legacy_input_dir"] == str(home / "legacy")
    assert payload["options"]["output_dir"] == str(home / "output")

    with pytest.raises(FileNotFoundError, match="legacy_input_dir does not exist"):
        build_fused_minute_dataset(
            MinuteFusionBuildOptions(
                legacy_input_dir="~/missing-legacy",
                output_dir="~/unused",
                manifest_path="~/unused.json",
            )
        )
    with pytest.raises(FileNotFoundError, match="guan_deal_dir does not exist"):
        build_fused_minute_dataset(
            MinuteFusionBuildOptions(
                legacy_input_dir="~/legacy",
                output_dir="~/unused",
                manifest_path="~/unused.json",
                guan_deal_dir="~/missing-deals",
            )
        )


def test_resume_skips_existing_legacy_partition_and_is_idempotent(tmp_path: Path) -> None:
    date = "20251231"
    source = _write_legacy_partition(
        tmp_path / "legacy",
        date,
        [_minute_row(date, close=10.1)],
    )
    options = _options(tmp_path, start_date=date, end_date=date, resume=True)

    first = build_fused_minute_dataset(options)
    output_path = Path(options.output_dir) / f"trade_date={date}" / "part-00000.parquet"
    first_bytes = output_path.read_bytes()
    pd.DataFrame([_minute_row(date, close=10.15)]).to_parquet(source, index=False)

    second = build_fused_minute_dataset(options)

    assert first["build_actions"]["legacy"][0]["status"] == "written"
    assert second["build_actions"]["legacy"][0] == {
        "date": date,
        "status": "skipped_existing",
        "source": "guan_legacy:already_canonical",
    }
    assert output_path.read_bytes() == first_bytes
    assert second["validation"]["status"] == "passed"

    rebuilt = build_fused_minute_dataset(replace(options, resume=False))
    assert rebuilt["build_actions"]["legacy"][0]["status"] == "written"
    assert pd.read_parquet(output_path).loc[0, "close"] == pytest.approx(10.15)
