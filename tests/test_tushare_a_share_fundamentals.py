from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from market_data_platform.cli import build_parser
from market_data_platform.contract import build_current_contract, write_current_contract
from market_data_platform.contract_health import (
    ContractInspectionOptions,
    inspect_current_contract,
)
from market_data_platform.paths import candidate_asset_paths, current_contract_path
from market_data_platform.providers import (
    tushare_a_share_fundamentals,
    tushare_a_share_fundamentals_part02,
)
from market_data_platform.providers.tushare_a_share_fundamentals import (
    FUNDAMENTALS_DATASETS,
    FieldValidationError,
    PitBuildOptions,
    PitProvenanceError,
    RawFundamentalsDownloadOptions,
    build_download_plan,
    build_normalized_fundamentals,
    build_normalized_fundamentals_union,
    build_pit_fundamentals,
    dataset_specs_payload,
    download_raw_fundamentals,
    load_pit_fundamentals_as_of,
    load_pit_fundamentals_as_of_panel,
    load_pit_fundamentals_as_of_panel_from_vintages,
    load_pit_fundamentals_as_of_view,
    load_pit_fundamentals_events_from_vintages,
    publish_fundamentals_assets,
    publish_pit_fundamentals_asset,
    validate_normalized_fundamentals,
    validate_pit_fundamentals,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    FundamentalsDownloadError,
    asset_integrity_checks,
    build_asset_integrity,
    file_sha256,
    manifest_seal_checks,
    seal_manifest,
)


class FakeIncomeClient:
    def __init__(self, pd, *, mode: str = "normal") -> None:
        self.pd = pd
        self.mode = mode
        self.calls: list[dict[str, object]] = []

    def income_vip(self, **kwargs):
        self.calls.append(dict(kwargs))
        offset = int(kwargs["offset"])
        if self.mode == "missing_columns":
            return self.pd.DataFrame({"ts_code": ["600519.SH"]})
        if self.mode == "duplicate":
            offset = 0
        rows = [
            {
                "ts_code": "600519.SH",
                "ann_date": "20260401",
                "f_ann_date": "20260402",
                "end_date": "20251231",
                "report_type": "1",
                "revenue": 10.0,
            },
            {
                "ts_code": "000001.SZ",
                "ann_date": "20260403",
                "f_ann_date": "20260404",
                "end_date": "20251231",
                "report_type": "1",
                "revenue": 20.0,
            },
        ]
        return self.pd.DataFrame(rows[offset : offset + int(kwargs["limit"])])


class RejectingIncomeClient:
    def income_vip(self, **_kwargs):
        raise RuntimeError("VIP permission denied")


class RateLimitedIncomeClient:
    def income_vip(self, **_kwargs):
        raise RuntimeError("抱歉，您访问接口(income_vip)频率超限(1次/分钟)。")


class FailsOnceIncomeClient(FakeIncomeClient):
    def __init__(self, pd) -> None:
        super().__init__(pd)
        self.failed = False

    def income_vip(self, **kwargs):
        if not self.failed:
            self.failed = True
            raise RuntimeError("temporary provider failure")
        return super().income_vip(**kwargs)


class UpdatedIncomeClient:
    def __init__(self, pd) -> None:
        self.pd = pd

    def income_vip(self, **kwargs):
        if int(kwargs["offset"]) > 0:
            return self.pd.DataFrame()
        base = {
            "ts_code": "002410.SZ",
            "ann_date": "20191029",
            "f_ann_date": "20191029",
            "end_date": "20251231",
            "report_type": "1",
            "revenue": 100.0,
        }
        bse = {
            "ts_code": "833243.BJ",
            "f_ann_date": "20260301",
            "end_date": "20251231",
            "report_type": "1",
            "comp_type": "1",
            "update_flag": "1",
            "revenue": 50.0,
        }
        return self.pd.DataFrame(
            [
                base | {"comp_type": "2", "update_flag": "0", "ebit": 184.0},
                base | {"comp_type": "2", "update_flag": "1", "ebit": 226.0},
                base | {"comp_type": "7", "update_flag": "1", "ebit": 999.0},
                base
                | {
                    "ts_code": "002411.SZ",
                    "comp_type": "1",
                    "update_flag": "1",
                    "ebit": 10.0,
                },
                base
                | {
                    "ts_code": "002411.SZ",
                    "comp_type": "4",
                    "update_flag": "1",
                    "ebit": 20.0,
                },
                bse | {"ann_date": "20260201"},
                bse | {"ann_date": "20260301"},
            ]
        )


def _download_options(output: Path | str, **overrides: object) -> RawFundamentalsDownloadOptions:
    values: dict[str, Any] = {
        "dataset": "income",
        "out_dir": output,
        "start_date": "20251231",
        "end_date": "20251231",
        "entitlement_mode": "vip_batch",
    }
    values.update(overrides)
    return RawFundamentalsDownloadOptions(**values)


def test_fundamentals_specs_declare_required_dataset_and_entitlement_policy() -> None:
    specs = dataset_specs_payload()

    assert set(specs) == set(FUNDAMENTALS_DATASETS)
    assert specs["income"]["vip_api_name"] == "income_vip"
    assert specs["income"]["fallback_api_name"] == "income"
    assert specs["dividend"]["entitlement_policy"] == "per_symbol_only"
    assert specs["disclosure_date"]["disclosure_fields"] == ("actual_date", "ann_date")


def test_plan_reports_non_vip_symbol_requirement_as_skipped() -> None:
    plan = build_download_plan(
        datasets=["income", "dividend"],
        start_date="20251231",
        end_date="20251231",
        entitlement_mode="non_vip_fallback",
    )

    assert plan["totals"] == {"datasets": 0, "query_units": 0, "skipped_datasets": 2}
    assert {row["dataset"] for row in plan["skipped_datasets"]} == {"income", "dividend"}


def test_raw_download_paginates_writes_provenance_and_restarts(tmp_path) -> None:
    pd = pytest.importorskip("pandas")
    client = FakeIncomeClient(pd)
    output = tmp_path / "raw"

    options = _download_options(output, client=client, run_id="income-test", page_size=1)
    first = download_raw_fundamentals(options)
    first_call_count = len(client.calls)
    second = download_raw_fundamentals(options)

    assert first["status"] == "completed"
    assert first["immutable_snapshot"] is True
    assert first["totals"]["rows"] == 2
    assert first["schema_hashes"]
    assert first["integrity"]["aggregate_sha256"]
    assert first["parts"][0]["query_parameters"] == {"period": "20251231"}
    assert first["parts"][0]["source_run_id"] == "income-test"
    assert first["parts"][0]["request_started_at"]
    assert first["parts"][0]["request_completed_at"]
    assert first["parts"][0]["content_sha256"]
    assert len(client.calls) == first_call_count
    assert second["totals"]["rows"] == 2
    state = json.loads((output / "state.json").read_text(encoding="utf-8"))
    assert state["contiguous_watermark"]


@pytest.mark.parametrize(
    ("mode", "error_kind"),
    [("duplicate", "duplicate_page"), ("missing_columns", "field_validation")],
)
def test_raw_download_persists_machine_readable_failures(tmp_path, mode, error_kind) -> None:
    pd = pytest.importorskip("pandas")
    output = tmp_path / mode

    manifest = download_raw_fundamentals(
        _download_options(
            output,
            client=FakeIncomeClient(pd, mode=mode),
            retry_attempts=1,
            page_size=1,
        )
    )

    failures = json.loads((output / "failures.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert failures["failed_units"][0]["error_kind"] == error_kind
    assert manifest["totals"]["failed_units"] == 1


def test_raw_download_reports_entitlement_failure(tmp_path) -> None:
    output = tmp_path / "entitlement"

    manifest = download_raw_fundamentals(
        _download_options(output, client=RejectingIncomeClient(), retry_attempts=1)
    )

    failures = json.loads((output / "failures.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert failures["entitlement_failures"][0]["error_kind"] == "entitlement_failure"


def test_raw_download_reports_rate_limit_separately_from_entitlement(tmp_path) -> None:
    output = tmp_path / "rate-limit"

    manifest = download_raw_fundamentals(
        _download_options(output, client=RateLimitedIncomeClient(), retry_attempts=1)
    )

    failures = json.loads((output / "failures.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert failures["failed_units"][0]["error_kind"] == "rate_limit"
    assert failures["entitlement_failures"] == []


def test_raw_download_respects_request_interval(monkeypatch, tmp_path) -> None:
    pd = pytest.importorskip("pandas")
    output = tmp_path / "throttled"
    sleeps: list[float] = []
    current_time = [0.0]

    monkeypatch.setattr(
        tushare_a_share_fundamentals.time,
        "monotonic",
        lambda: current_time[0],
    )

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        current_time[0] += seconds

    monkeypatch.setattr(tushare_a_share_fundamentals.time, "sleep", fake_sleep)

    download_raw_fundamentals(
        _download_options(
            output,
            client=FakeIncomeClient(pd),
            page_size=1,
            request_interval_seconds=2.0,
        )
    )

    assert sleeps == [2.0, 2.0]


def test_raw_download_clears_prior_failure_after_restart_success(tmp_path) -> None:
    pd = pytest.importorskip("pandas")
    output = tmp_path / "restart-success"
    client = FailsOnceIncomeClient(pd)
    options = _download_options(output, client=client, retry_attempts=1)

    first = download_raw_fundamentals(options)
    second = download_raw_fundamentals(options)

    failures = json.loads((output / "failures.json").read_text(encoding="utf-8"))
    assert first["status"] == "partial"
    assert second["status"] == "completed"
    assert failures["failed_units"] == []
    assert failures["entitlement_failures"] == []


def test_raw_download_requires_new_directory_for_stale_completed_snapshot(tmp_path) -> None:
    pd = pytest.importorskip("pandas")
    client = FakeIncomeClient(pd)
    output = tmp_path / "stale"
    options = _download_options(output, client=client, run_id="stale-test", page_size=1)
    download_raw_fundamentals(options)
    first_call_count = len(client.calls)
    with pytest.raises(FundamentalsDownloadError, match="cannot be refreshed in place"):
        download_raw_fundamentals(replace(options, stale_after_days=1))

    assert len(client.calls) == first_call_count


def test_normalized_prefers_latest_provider_update_flag_within_observation(tmp_path) -> None:
    pd = pytest.importorskip("pandas")
    raw = tmp_path / "updated-raw"
    normalized = tmp_path / "updated-normalized"
    download_raw_fundamentals(
        _download_options(raw, client=UpdatedIncomeClient(pd), run_id="updated-income")
    )

    manifest = build_normalized_fundamentals(
        dataset="income",
        raw_dir=raw,
        out_dir=normalized,
    )
    frame = pd.read_parquet(normalized / "data" / "part.parquet")

    assert manifest["dropped_rows"]["superseded_update_flag"] == 1
    assert manifest["dropped_rows"]["superseded_ann_date"] == 1
    assert manifest["dropped_rows"]["unsupported_comp_type"] == 1
    updated = frame.loc[frame["symbol"] == "002410.SZ"]
    assert updated[["update_flag", "ebit"]].to_dict("records") == [
        {"update_flag": "1", "ebit": 226.0}
    ]
    consolidated = frame.loc[frame["symbol"] == "002411.SZ"]
    assert consolidated[["comp_type", "ebit"]].to_dict("records") == [
        {"comp_type": "1", "ebit": 10.0}
    ]
    assert manifest["dropped_rows"]["non_consolidated_with_consolidated"] == 1
    bse = frame.loc[frame["symbol"] == "833243.BJ"]
    assert bse[["ann_date", "revenue"]].to_dict("records") == [
        {"ann_date": "20260301", "revenue": 50.0}
    ]


def _write_complete_raw_manifest(
    root: Path,
    *,
    source_run_id: str,
    parts: list[dict[str, str]],
) -> None:
    completed: list[dict[str, Any]] = [
        {
            "unit_id": f"income:unit-{index}",
            "status": "completed",
            "path": row["path"],
            "retrieved_at": row["retrieved_at"],
            "request_started_at": row["retrieved_at"],
            "request_completed_at": row["retrieved_at"],
            "content_sha256": file_sha256(row["path"]),
            "bytes": Path(row["path"]).stat().st_size,
        }
        for index, row in enumerate(parts, start=1)
    ]
    plan = [row["unit_id"] for row in completed]
    state = {
        "plan": plan,
        "units": {row["unit_id"]: row for row in completed},
        "contiguous_watermark": plan[-1],
    }
    failures = {"failed_units": []}
    state_path = root / "state.json"
    failure_path = root / "failures.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    failure_path.write_text(json.dumps(failures), encoding="utf-8")
    integrity = build_asset_integrity(
        root,
        [*(Path(row["path"]) for row in completed), state_path, failure_path],
    )
    (root / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "tushare.a_share.fundamentals.raw.v2",
                "status": "completed",
                "immutable_snapshot": True,
                "source_run_id": source_run_id,
                "query": {
                    "start_date": "20250101",
                    "end_date": "20251231",
                    "query_parameters": [{} for _row in completed],
                },
                "totals": {
                    "files": len(completed),
                    "query_units": len(completed),
                    "failed_units": 0,
                },
                "state_file": str(state_path),
                "failure_report": str(failure_path),
                "parts": completed,
                "integrity": integrity,
            }
        ),
        encoding="utf-8",
    )
    seal_manifest(root / "manifest.yml")


def _write_raw_income(tmp_path):
    pd = pytest.importorskip("pandas")
    raw = tmp_path / "raw-income"
    (raw / "data" / "income").mkdir(parents=True)
    part_path = raw / "data" / "income" / "part.parquet"
    pd.DataFrame(
        [
            {
                "ts_code": "600519.SH",
                "ann_date": "20260401",
                "f_ann_date": "20260402",
                "end_date": "20251231",
                "report_type": "1",
                "revenue": 10.0,
            },
            {
                "ts_code": "600519.SH",
                "ann_date": "20260401",
                "f_ann_date": "20260402",
                "end_date": "20251231",
                "report_type": "2",
                "revenue": 11.0,
            },
            {
                "ts_code": "000001.SZ",
                "ann_date": "",
                "f_ann_date": "",
                "end_date": "20251231",
                "report_type": "1",
                "revenue": 20.0,
            },
            {
                "ts_code": "000002.SZ",
                "ann_date": "20240101",
                "f_ann_date": "20240101",
                "end_date": "20251231",
                "report_type": "1",
                "revenue": 30.0,
            },
        ]
    ).to_parquet(part_path, index=False)
    _write_complete_raw_manifest(
        raw,
        source_run_id="income-normalize-test",
        parts=[
            {
                "path": str(part_path),
                "retrieved_at": "2026-05-29T12:00:00+00:00",
            }
        ],
    )
    return raw


def _write_raw_income_bundle(
    tmp_path,
    name: str,
    rows: list[tuple[str, float, str]],
):
    pd = pytest.importorskip("pandas")
    raw = tmp_path / name
    data_dir = raw / "data" / "income"
    data_dir.mkdir(parents=True)
    parts = []
    for index, (symbol, revenue, retrieved_at) in enumerate(rows, start=1):
        path = data_dir / f"part-{index}.parquet"
        pd.DataFrame(
            [
                {
                    "ts_code": symbol,
                    "ann_date": "20260401",
                    "f_ann_date": "20260401",
                    "end_date": "20251231",
                    "report_type": "1",
                    "revenue": revenue,
                }
            ]
        ).to_parquet(path, index=False)
        parts.append({"path": str(path), "retrieved_at": retrieved_at})
    _write_complete_raw_manifest(raw, source_run_id=name, parts=parts)
    return raw


def test_normalized_and_pit_builders_preserve_provenance_and_quarantine(tmp_path) -> None:
    pd = pytest.importorskip("pandas")
    raw = _write_raw_income(tmp_path)
    normalized = tmp_path / "normalized"
    pit = tmp_path / "pit"

    normalized_manifest = build_normalized_fundamentals(
        dataset="income",
        raw_dir=raw,
        out_dir=normalized,
    )
    pit_manifest = build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=income_revenue"],
            available_delay_days=1,
        )
    )

    normalized_frame = pd.read_parquet(normalized / "data" / "part.parquet")
    pit_frame = pd.read_parquet(pit / "data")
    assert normalized_manifest["semantics"]["raw_preserves_all_report_types"] is True
    assert normalized_manifest["query"]["end_date"] == "20251231"
    assert normalized_manifest["observed_vintage_dates"] == ["20260529"]
    assert normalized_manifest["bundle_available_date"] == "20260529"
    assert normalized_manifest["as_of_date"] == "20260601"
    assert normalized_manifest["dropped_rows"]["unsupported_report_type"] == 1
    assert normalized_frame["_source_run_id"].unique().tolist() == ["income-normalize-test"]
    assert pit_manifest["totals"]["quarantined_rows"] == 2
    assert pit_frame["available_date"].tolist() == ["20260403"]
    assert pit_frame["income_revenue"].tolist() == [10.0]
    assert list((pit / "quarantine").glob("missing_disclosure_semantics-*.parquet"))
    assert list((pit / "quarantine").glob("invalid_disclosure_order-*.parquet"))
    assert validate_normalized_fundamentals(asset_dir=normalized)["status"] == "passed"
    assert validate_pit_fundamentals(asset_dir=pit)["status"] == "passed"
    with pytest.raises(FileExistsError, match="immutable"):
        build_normalized_fundamentals(dataset="income", raw_dir=raw, out_dir=normalized)
    with pytest.raises(FileExistsError, match="immutable"):
        build_pit_fundamentals(
            PitBuildOptions(
                normalized_dirs=[normalized],
                out_dir=pit,
                field_mappings=["revenue=income_revenue"],
            )
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "partial",
        "legacy_global_fallback",
        "extra_parquet",
        "part_content",
        "declared_vintage",
        "invalid_part_timestamp",
        "naive_part_timestamp",
        "empty_part_timestamp",
    ],
)
def test_normalized_v2_rejects_incomplete_or_tampered_raw_provenance(
    tmp_path,
    tamper: str,
) -> None:
    pd = pytest.importorskip("pandas")
    raw = _write_raw_income(tmp_path)
    manifest_path = raw / "manifest.yml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if tamper == "partial":
        manifest["status"] = "partial"
        manifest["totals"]["failed_units"] = 1
    elif tamper == "legacy_global_fallback":
        manifest["retrieved_at"] = manifest["parts"][0]["retrieved_at"]
        manifest.pop("parts")
    elif tamper == "extra_parquet":
        pd.DataFrame({"ts_code": ["000003.SZ"]}).to_parquet(
            raw / "data/income/unlisted.parquet", index=False
        )
    elif tamper == "part_content":
        part_path = Path(manifest["parts"][0]["path"])
        changed = pd.read_parquet(part_path)
        changed.loc[0, "revenue"] = 999.0
        changed.to_parquet(part_path, index=False)
    elif tamper == "declared_vintage":
        manifest["observed_vintage_dates"] = ["20260528"]
    else:
        invalid_timestamp = {
            "invalid_part_timestamp": "nonsense",
            "naive_part_timestamp": "2026-05-29T12:00:00",
            "empty_part_timestamp": "",
        }[tamper]
        manifest["parts"][0]["retrieved_at"] = invalid_timestamp
        state_path = raw / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["units"][state["plan"][0]]["retrieved_at"] = invalid_timestamp
        state_path.write_text(json.dumps(state), encoding="utf-8")
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    seal_manifest(manifest_path)

    error = r"parts_have_.*retrieval_timestamps" if tamper.endswith("part_timestamp") else None
    with pytest.raises(PitProvenanceError, match=error):
        build_normalized_fundamentals(
            dataset="income",
            raw_dir=raw,
            out_dir=tmp_path / f"normalized-{tamper}",
        )


def _write_normalized_asset(
    tmp_path,
    name: str,
    rows: list[dict[str, object]],
    *,
    max_observation_age_days: int = 3,
    bundle_start_date: str | None = None,
):
    pd = pytest.importorskip("pandas")
    root = tmp_path / name
    (root / "data").mkdir(parents=True)
    retrieved_dates = [
        str(row.get("_source_retrieved_at") or "")[:10].replace("-", "")
        for row in rows
        if row.get("_source_retrieved_at")
    ]
    retrieved_timestamps = sorted(
        {str(row.get("_source_retrieved_at")) for row in rows if row.get("_source_retrieved_at")}
    )
    observed_vintage_dates = sorted(set(retrieved_dates))
    latest_vintage = max(observed_vintage_dates) if observed_vintage_dates else None
    source_datasets = sorted(
        {str(row.get("_source_dataset")) for row in rows if row.get("_source_dataset")}
    )
    component = source_datasets[0] if len(source_datasets) == 1 else name
    if bundle_start_date is None:
        bundle_ladder = [
            {
                "retrieval_start_date": value,
                "retrieval_end_date": value,
                "bundle_available_date": value,
            }
            for value in observed_vintage_dates
        ]
    else:
        bundle_ladder = [
            {
                "retrieval_start_date": bundle_start_date,
                "retrieval_end_date": latest_vintage,
                "bundle_available_date": latest_vintage,
            }
        ]
    prepared_rows = []
    for raw_row in rows:
        row = dict(raw_row)
        retrieved_date = str(row.get("_source_retrieved_at") or "")[:10].replace("-", "")
        row["_source_bundle_retrieval_start_date"] = bundle_start_date or retrieved_date
        row["_source_bundle_available_date"] = (
            latest_vintage if bundle_start_date else retrieved_date
        )
        prepared_rows.append(row)
    part_path = root / "data" / "part.parquet"
    pd.DataFrame(prepared_rows).to_parquet(part_path, index=False)
    latest_start = bundle_ladder[-1]["retrieval_start_date"] if bundle_ladder else None
    valid_through = (
        (pd.Timestamp(latest_start) + pd.Timedelta(days=max_observation_age_days)).strftime(
            "%Y%m%d"
        )
        if latest_start and latest_vintage
        else None
    )
    valid_through = valid_through if valid_through and valid_through >= latest_vintage else None
    (root / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "tushare.a_share.fundamentals.normalized.v2",
                "status": "completed",
                "immutable_snapshot": True,
                "source_dataset": component,
                "source_raw_status": "completed",
                "source_raw_completeness": {"production_eligible": True},
                "source_integrity": {
                    "manifest_sha256": "0" * 64,
                    "content_aggregate_sha256": "1" * 64,
                },
                "integrity": build_asset_integrity(root, [part_path]),
                "as_of_date": valid_through,
                "bundle_available_date": latest_vintage,
                "source_observation": {
                    "retrieval_start_date": latest_start,
                    "retrieval_end_date": latest_vintage,
                    "bundle_available_date": latest_vintage,
                    "observed_vintage_dates": observed_vintage_dates,
                },
                "source_bundle_observations": {component: bundle_ladder},
                "observed_vintage_dates": observed_vintage_dates,
                "freshness_policy": {
                    "max_observation_age_days": max_observation_age_days,
                },
                "source_retrieved_at": retrieved_timestamps,
                "query": {"start_date": "19900101", "end_date": "20251231"},
            }
        ),
        encoding="utf-8",
    )
    seal_manifest(root / "manifest.yml")
    return root


def test_pit_builder_coalesces_multi_source_rows_by_symbol_trade_date(tmp_path) -> None:
    pd = pytest.importorskip("pandas")
    income = _write_normalized_asset(
        tmp_path,
        "income-normalized",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "income-run",
                "_source_retrieved_at": "2026-05-29T01:00:00+00:00",
                "revenue": 100.0,
                "n_income": 40.0,
            }
        ],
    )
    balancesheet = _write_normalized_asset(
        tmp_path,
        "balancesheet-normalized",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "balancesheet",
                "_source_raw_asset": "raw-balancesheet",
                "_source_run_id": "balancesheet-run",
                "_source_retrieved_at": "2026-05-29T01:00:00+00:00",
                "total_assets": 1000.0,
                "total_liab": 200.0,
            }
        ],
    )
    pit = tmp_path / "pit-combined"

    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[income, balancesheet],
            out_dir=pit,
            field_mappings=[
                "revenue=revenue",
                "n_income=net_profit",
                "total_assets=total_assets",
                "total_liab=total_liabilities",
            ],
        )
    )

    data = pd.read_parquet(pit / "data")
    assert len(data) == 1
    assert data.loc[0, "trade_date"] == "20260418"
    assert data.loc[0, "_source_dataset"] == "balancesheet;income"
    assert data.loc[0, "revenue"] == 100.0
    assert data.loc[0, "total_assets"] == 1000.0
    validation = validate_pit_fundamentals(asset_dir=pit)
    assert validation["status"] == "passed"
    assert {row["id"]: row for row in validation["checks"]}[
        "unique_symbol_trade_date_report_period_retrieval"
    ]["passed"]


def test_pit_coalesce_fast_path_skips_groupwise_aggregation(monkeypatch) -> None:
    pd = pytest.importorskip("pandas")
    rows = []
    for symbol in ("000001.SZ", "600519.SH"):
        rows.append(
            {
                "symbol": symbol,
                "trade_date": "20260418",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "available_date": "20260418",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "income-run",
                "_source_retrieved_at": "2026-04-18T01:00:00+00:00",
                "_source_bundle_retrieval_start_date": "20260418",
                "_source_bundle_available_date": "20260418",
                "revenue": 100.0,
            }
        )

    def fail_groupwise(_values):
        raise AssertionError("unique PIT keys should use the vectorized fast path")

    monkeypatch.setattr(tushare_a_share_fundamentals_part02, "_last_present", fail_groupwise)
    result = tushare_a_share_fundamentals_part02._coalesce_pit_frame(
        pd.DataFrame(rows), ["revenue"]
    )

    assert result["symbol"].tolist() == ["000001.SZ", "600519.SH"]


def test_pit_builder_keeps_same_day_report_periods_separate(tmp_path) -> None:
    pd = pytest.importorskip("pandas")
    normalized = _write_normalized_asset(
        tmp_path,
        "same-day-periods",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "income-run",
                "_source_retrieved_at": "2026-04-17T12:00:00+00:00",
                "revenue": 100.0,
            },
            {
                "symbol": "600519.SH",
                "report_period": "20260331",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "income-run",
                "_source_retrieved_at": "2026-04-17T12:00:00+00:00",
                "revenue": 30.0,
            },
        ],
    )
    pit = tmp_path / "same-day-periods-pit"

    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=revenue"],
        )
    )

    data = pd.read_parquet(pit / "data").sort_values("report_period").reset_index(drop=True)
    assert data[["report_period", "revenue"]].to_dict("records") == [
        {"report_period": "20251231", "revenue": 100.0},
        {"report_period": "20260331", "revenue": 30.0},
    ]
    checks = {row["id"]: row for row in validate_pit_fundamentals(asset_dir=pit)["checks"]}
    assert checks["unique_symbol_trade_date_report_period_retrieval"]["passed"]


def test_pit_bundle_uses_latest_component_vintage_without_exact_date_intersection(
    tmp_path,
) -> None:
    income = _write_normalized_asset(
        tmp_path,
        "income-vintage",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "income-run",
                "_source_retrieved_at": "2026-06-06T01:00:00+00:00",
                "revenue": 100.0,
            }
        ],
    )
    balancesheet = _write_normalized_asset(
        tmp_path,
        "balancesheet-vintage",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "balancesheet",
                "_source_raw_asset": "raw-balancesheet",
                "_source_run_id": "balancesheet-run",
                "_source_retrieved_at": "2026-06-07T01:00:00+00:00",
                "total_assets": 1000.0,
            }
        ],
    )
    pit = tmp_path / "split-vintage-pit"
    manifest = build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[income, balancesheet],
            out_dir=pit,
            field_mappings=["revenue=revenue", "total_assets=total_assets"],
        )
    )

    state = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260607")
    validation = validate_pit_fundamentals(asset_dir=pit, target_date="20260607")

    assert manifest["bundle_available_date"] == "20260607"
    assert manifest["oldest_component_retrieval_date"] == "20260606"
    assert state.loc[0, "revenue"] == 100.0
    assert state.loc[0, "total_assets"] == 1000.0
    assert state.attrs["pit_audit"]["observation_age_days"] == 1
    assert state.attrs["pit_audit"]["production_eligible"] is True
    assert validation["status"] == "passed"

    zero_age_pit = tmp_path / "split-vintage-zero-age-pit"
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[income, balancesheet],
            out_dir=zero_age_pit,
            field_mappings=["revenue=revenue", "total_assets=total_assets"],
            max_observation_age_days=0,
        )
    )
    zero_age_validation = validate_pit_fundamentals(
        asset_dir=zero_age_pit,
        target_date="20260607",
    )
    assert zero_age_validation["status"] == "failed"
    assert zero_age_validation["observation_state"]["revision_covered"] is True
    assert zero_age_validation["observation_state"]["freshness_verified"] is False


def test_pit_archive_ladder_unions_same_dataset_snapshot_vintages(tmp_path) -> None:
    first = _write_normalized_asset(
        tmp_path,
        "income-snapshot-20260418",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income-first",
                "_source_run_id": "income-first",
                "_source_retrieved_at": "2026-04-18T01:00:00+00:00",
                "revenue": 100.0,
            }
        ],
    )
    revision = _write_normalized_asset(
        tmp_path,
        "income-snapshot-20260421",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260420",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income-revision",
                "_source_run_id": "income-revision",
                "_source_retrieved_at": "2026-04-21T01:00:00+00:00",
                "revenue": 105.0,
            }
        ],
    )
    pit = tmp_path / "income-archive-ladder-pit"
    manifest = build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[first, revision],
            out_dir=pit,
            field_mappings=["revenue=revenue"],
        )
    )

    monday = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260420")
    tuesday = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260421")

    assert manifest["source_observed_vintage_dates"] == {"income": ["20260418", "20260421"]}
    assert monday.loc[0, "revenue"] == 100.0
    assert monday.attrs["pit_audit"]["missing_observation_sources"] == []
    assert tuesday.loc[0, "revenue"] == 105.0


def test_multi_vintage_event_loader_preserves_revision_ladder(tmp_path) -> None:
    first = _write_normalized_asset(
        tmp_path,
        "multi-loader-first-20260418",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income-first",
                "_source_run_id": "income-first",
                "_source_retrieved_at": "2026-04-18T01:00:00+00:00",
                "revenue": 100.0,
            }
        ],
    )
    revision = _write_normalized_asset(
        tmp_path,
        "multi-loader-revision-20260421",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260420",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income-revision",
                "_source_run_id": "income-revision",
                "_source_retrieved_at": "2026-04-21T01:00:00+00:00",
                "revenue": 105.0,
            }
        ],
    )
    first_pit = tmp_path / "multi-loader-first-pit"
    revision_pit = tmp_path / "multi-loader-revision-pit"
    for normalized, output in ((first, first_pit), (revision, revision_pit)):
        build_pit_fundamentals(
            PitBuildOptions(
                normalized_dirs=[normalized],
                out_dir=output,
                field_mappings=["revenue=revenue"],
            )
        )

    events = load_pit_fundamentals_events_from_vintages(
        asset_dirs=[first_pit, revision_pit],
        fields=["revenue"],
    )
    monday = events.as_of("20260420", provenance_policy="require_observed")
    tuesday = events.as_of("20260421", provenance_policy="require_observed")
    assert monday.frame.loc[0, "revenue"] == 100.0
    assert tuesday.frame.loc[0, "revenue"] == 105.0
    assert events.manifest["source_observed_vintage_dates"] == {"income": ["20260418", "20260421"]}
    panel = load_pit_fundamentals_as_of_panel_from_vintages(
        asset_dirs=[first_pit, revision_pit],
        as_of_dates=["20260420", "20260421"],
        fields=["revenue"],
    )
    assert panel.frame[["as_of_date", "revenue"]].to_dict("records") == [
        {"as_of_date": "20260420", "revenue": 100.0},
        {"as_of_date": "20260421", "revenue": 105.0},
    ]


def test_raw_multi_part_bundle_becomes_observed_at_latest_part_retrieval(tmp_path) -> None:
    pd = pytest.importorskip("pandas")
    raw = tmp_path / "raw-multi-part"
    data_dir = raw / "data" / "income"
    data_dir.mkdir(parents=True)
    first_path = data_dir / "part-first.parquet"
    second_path = data_dir / "part-second.parquet"
    pd.DataFrame(
        [
            {
                "ts_code": "600519.SH",
                "ann_date": "20260417",
                "f_ann_date": "20260417",
                "end_date": "20251231",
                "report_type": "1",
                "revenue": 100.0,
            }
        ]
    ).to_parquet(first_path, index=False)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "ann_date": "20260417",
                "f_ann_date": "20260417",
                "end_date": "20251231",
                "report_type": "1",
                "revenue": 50.0,
            }
        ]
    ).to_parquet(second_path, index=False)
    _write_complete_raw_manifest(
        raw,
        source_run_id="income-multi-part",
        parts=[
            {
                "path": str(first_path),
                "retrieved_at": "2026-04-18T01:00:00+00:00",
            },
            {
                "path": str(second_path),
                "retrieved_at": "2026-04-21T01:00:00+00:00",
            },
        ],
    )
    normalized = tmp_path / "normalized-multi-part"
    pit = tmp_path / "pit-multi-part"

    normalized_manifest = build_normalized_fundamentals(
        dataset="income",
        raw_dir=raw,
        out_dir=normalized,
    )
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=revenue"],
        )
    )

    before_bundle = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260420")
    complete_bundle = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260421")

    assert normalized_manifest["source_observation"]["retrieval_start_date"] == "20260418"
    assert normalized_manifest["source_observation"]["retrieval_end_date"] == "20260421"
    assert normalized_manifest["observed_vintage_dates"] == ["20260421"]
    assert before_bundle.empty
    assert before_bundle.attrs["pit_audit"]["revision_safe"] is False
    assert set(complete_bundle["symbol"]) == {"000001.SZ", "600519.SH"}


def test_oldest_part_controls_complete_bundle_freshness_boundaries(tmp_path) -> None:
    span_three_raw = _write_raw_income_bundle(
        tmp_path,
        "raw-span-three",
        [
            ("600519.SH", 100.0, "2026-04-01T01:00:00+00:00"),
            ("000001.SZ", 50.0, "2026-04-04T01:00:00+00:00"),
        ],
    )
    span_three_normalized = tmp_path / "normalized-span-three"
    span_three_pit = tmp_path / "pit-span-three"
    normalized_manifest = build_normalized_fundamentals(
        dataset="income",
        raw_dir=span_three_raw,
        out_dir=span_three_normalized,
    )
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[span_three_normalized],
            out_dir=span_three_pit,
            field_mappings=["revenue=revenue"],
        )
    )

    completion = load_pit_fundamentals_as_of(asset_dir=span_three_pit, as_of_date="20260404")
    after_completion = load_pit_fundamentals_as_of(asset_dir=span_three_pit, as_of_date="20260405")

    assert normalized_manifest["source_observation"]["retrieval_span_days"] == 3
    assert normalized_manifest["as_of_date"] == "20260404"
    assert completion.attrs["pit_audit"]["oldest_component_retrieval_date"] == "20260401"
    assert completion.attrs["pit_audit"]["observation_age_days"] == 3
    assert completion.attrs["pit_audit"]["production_eligible"] is True
    assert set(completion["symbol"]) == {"000001.SZ", "600519.SH"}
    assert after_completion.empty
    assert after_completion.attrs["pit_audit"]["observation_age_days"] == 4
    assert after_completion.attrs["pit_audit"]["production_eligible"] is False

    span_four_raw = _write_raw_income_bundle(
        tmp_path,
        "raw-span-four",
        [
            ("600519.SH", 100.0, "2026-04-01T01:00:00+00:00"),
            ("000001.SZ", 50.0, "2026-04-05T01:00:00+00:00"),
        ],
    )
    span_four_normalized = tmp_path / "normalized-span-four"
    span_four_manifest = build_normalized_fundamentals(
        dataset="income",
        raw_dir=span_four_raw,
        out_dir=span_four_normalized,
    )
    assert span_four_manifest["source_observation"]["retrieval_span_days"] == 4
    assert span_four_manifest["as_of_date"] is None
    with pytest.raises(PitProvenanceError, match="observed vintage dates"):
        build_pit_fundamentals(
            PitBuildOptions(
                normalized_dirs=[span_four_normalized],
                out_dir=tmp_path / "pit-span-four",
                field_mappings=["revenue=revenue"],
            )
        )


def test_incomplete_new_bundle_cannot_leak_partial_revisions(tmp_path) -> None:
    old_raw = _write_raw_income_bundle(
        tmp_path,
        "raw-old-complete",
        [
            ("600519.SH", 100.0, "2026-04-08T01:00:00+00:00"),
            ("000001.SZ", 50.0, "2026-04-08T01:00:00+00:00"),
        ],
    )
    new_raw = _write_raw_income_bundle(
        tmp_path,
        "raw-new-complete",
        [
            ("600519.SH", 110.0, "2026-04-10T01:00:00+00:00"),
            ("000001.SZ", 60.0, "2026-04-12T01:00:00+00:00"),
        ],
    )
    old_normalized = tmp_path / "normalized-old-complete"
    new_normalized = tmp_path / "normalized-new-complete"
    build_normalized_fundamentals(dataset="income", raw_dir=old_raw, out_dir=old_normalized)
    build_normalized_fundamentals(dataset="income", raw_dir=new_raw, out_dir=new_normalized)
    pit = tmp_path / "pit-atomic-bundles"
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[old_normalized, new_normalized],
            out_dir=pit,
            field_mappings=["revenue=revenue"],
        )
    )

    during_new_bundle = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260410")
    after_new_bundle = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260412")
    during = during_new_bundle.set_index("symbol")["revenue"].to_dict()
    after = after_new_bundle.set_index("symbol")["revenue"].to_dict()

    assert during == {"000001.SZ": 50.0, "600519.SH": 100.0}
    assert during_new_bundle.attrs["pit_audit"]["bundle_available_date"] == "20260408"
    assert after == {"000001.SZ": 60.0, "600519.SH": 110.0}
    assert after_new_bundle.attrs["pit_audit"]["bundle_available_date"] == "20260412"


def test_cross_component_freshness_uses_oldest_selected_bundle_part(tmp_path) -> None:
    income = _write_normalized_asset(
        tmp_path,
        "income-spanning-bundle",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260401",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "income-spanning",
                "_source_retrieved_at": "2026-04-10T01:00:00+00:00",
                "revenue": 100.0,
            }
        ],
        bundle_start_date="20260408",
    )
    balancesheet = _write_normalized_asset(
        tmp_path,
        "balancesheet-same-day-bundle",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260401",
                "_source_dataset": "balancesheet",
                "_source_raw_asset": "raw-balancesheet",
                "_source_run_id": "balancesheet-same-day",
                "_source_retrieved_at": "2026-04-11T01:00:00+00:00",
                "total_assets": 1000.0,
            }
        ],
    )
    pit = tmp_path / "pit-cross-component-oldest"
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[income, balancesheet],
            out_dir=pit,
            field_mappings=["revenue=revenue", "total_assets=total_assets"],
        )
    )

    boundary = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260411")
    stale = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260412")
    audit = boundary.attrs["pit_audit"]

    assert audit["selected_bundle_retrieval_start_by_source"] == {
        "balancesheet": "20260411",
        "income": "20260408",
    }
    assert audit["oldest_component_retrieval_date"] == "20260408"
    assert audit["observation_age_days"] == 3
    assert audit["production_eligible"] is True
    assert boundary.loc[0, "revenue"] == 100.0
    assert boundary.loc[0, "total_assets"] == 1000.0
    assert stale.empty
    assert stale.attrs["pit_audit"]["observation_age_days"] == 4
    assert stale.attrs["pit_audit"]["production_eligible"] is False


def test_pit_as_of_view_is_fieldwise_and_carries_weekend_event_forward(tmp_path) -> None:
    normalized = _write_normalized_asset(
        tmp_path,
        "weekend-revisions",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",  # Friday; available Saturday.
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "income-first",
                "_source_retrieved_at": "2026-04-18T01:00:00+00:00",
                "revenue": 100.0,
                "net_profit": 40.0,
            },
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260420",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income-revision",
                "_source_run_id": "income-revision",
                "_source_retrieved_at": "2026-04-21T01:00:00+00:00",
                "revenue": 105.0,
                "net_profit": None,
            },
        ],
    )
    pit = tmp_path / "weekend-revisions-pit"
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=revenue", "net_profit=net_profit"],
        )
    )

    friday = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260417")
    monday = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260420")
    tuesday = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260421")
    tuesday_view = load_pit_fundamentals_as_of_view(
        asset_dir=pit,
        as_of_date="20260421",
    )

    assert friday.empty
    assert monday.loc[0, "revenue"] == 100.0
    assert monday.loc[0, "revenue__available_date"] == "20260418"
    assert tuesday.loc[0, "revenue"] == 105.0
    assert tuesday.loc[0, "revenue__available_date"] == "20260421"
    assert tuesday.loc[0, "revenue__source_raw_asset"] == "raw-income-revision"
    assert tuesday.loc[0, "revenue__revision_id"]
    assert tuesday.loc[0, "net_profit"] == 40.0
    assert tuesday.loc[0, "net_profit__available_date"] == "20260418"
    assert tuesday.attrs["pit_audit"]["as_of_date"] == "20260421"
    assert tuesday.attrs["pit_audit"]["bundle_available_date"] == "20260421"
    assert tuesday.attrs["pit_audit"]["oldest_component_retrieval_date"] == "20260421"
    assert tuesday.attrs["pit_audit"]["provenance_policy"] == "require_observed"
    assert tuesday.attrs["pit_audit"]["revision_safe"] is True
    assert tuesday.attrs["pit_audit"]["freshness_verified"] is True
    assert tuesday.attrs["pit_audit"]["coverage"]["visible_events"] == 2
    assert tuesday_view.audit == tuesday.attrs["pit_audit"]
    assert tuesday_view.frame.equals(tuesday)

    panel = load_pit_fundamentals_as_of_panel(
        asset_dir=pit,
        as_of_dates=["20260420", "20260421"],
        fields=["revenue"],
        symbols=["600519.SH"],
    )
    assert panel.frame[["as_of_date", "revenue"]].to_dict("records") == [
        {"as_of_date": "20260420", "revenue": 100.0},
        {"as_of_date": "20260421", "revenue": 105.0},
    ]
    assert "net_profit" not in panel.frame
    assert panel.audit["as_of_date"] == "20260421"
    assert panel.audit["coverage"]["as_of_dates"] == 2


def test_pit_as_of_prefers_newer_period_over_later_old_period_revision(tmp_path) -> None:
    normalized = _write_normalized_asset(
        tmp_path,
        "period-priority",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260401",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "annual-first",
                "_source_retrieved_at": "2026-04-02T01:00:00+00:00",
                "revenue": 100.0,
            },
            {
                "symbol": "600519.SH",
                "report_period": "20260331",
                "disclosure_date": "20260420",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "quarterly",
                "_source_retrieved_at": "2026-04-21T01:00:00+00:00",
                "revenue": 30.0,
            },
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260421",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "annual-late-revision",
                "_source_retrieved_at": "2026-04-22T01:00:00+00:00",
                "revenue": 105.0,
            },
        ],
    )
    pit = tmp_path / "period-priority-pit"
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=revenue"],
        )
    )

    state = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260422")

    assert state.loc[0, "revenue"] == 30.0
    assert state.loc[0, "revenue__report_period"] == "20260331"
    assert state.loc[0, "revenue__source_run_id"] == "quarterly"


def test_pit_as_of_view_rejects_unobserved_historical_backfill_by_default(tmp_path) -> None:
    normalized = _write_normalized_asset(
        tmp_path,
        "late-backfill",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20191231",
                "disclosure_date": "20200330",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-late-backfill",
                "_source_run_id": "backfill-2026",
                "_source_retrieved_at": "2026-07-20T01:00:00+00:00",
                "revenue": 100.0,
            }
        ],
    )
    pit = tmp_path / "late-backfill-pit"
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=revenue"],
        )
    )

    strict = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20200401")

    legacy = load_pit_fundamentals_as_of(
        asset_dir=pit,
        as_of_date="20200401",
        provenance_policy="allow_unverified",
    )
    assert strict.empty
    assert strict.attrs["pit_audit"]["revision_safe"] is False
    assert strict.attrs["pit_audit"]["missing_observation_sources"] == ["income"]
    assert legacy.loc[0, "revenue"] == 100.0
    assert legacy.attrs["pit_audit"]["production_eligible"] is False


def test_pit_builder_rejects_conflicting_same_day_revision(tmp_path) -> None:
    normalized = _write_normalized_asset(
        tmp_path,
        "ambiguous-revision",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income-a",
                "_source_run_id": "income-run-a",
                "_source_retrieved_at": "2026-04-18T01:00:00+00:00",
                "revenue": 100.0,
            },
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income-b",
                "_source_run_id": "income-run-b",
                "_source_retrieved_at": "2026-04-18T01:00:00+00:00",
                "revenue": 101.0,
            },
        ],
    )

    with pytest.raises(FieldValidationError, match="ambiguous same-day revision"):
        build_pit_fundamentals(
            PitBuildOptions(
                normalized_dirs=[normalized],
                out_dir=tmp_path / "ambiguous-revision-pit",
                field_mappings=["revenue=revenue"],
            )
        )


def test_pit_as_of_orders_distinct_same_day_retrieval_vintages(tmp_path) -> None:
    pd = pytest.importorskip("pandas")
    normalized = _write_normalized_asset(
        tmp_path,
        "ordered-same-day-revision",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income-first",
                "_source_run_id": "income-first",
                "_source_retrieved_at": "2026-04-18T01:00:00+00:00",
                "revenue": 100.0,
            },
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income-second",
                "_source_run_id": "income-second",
                "_source_retrieved_at": "2026-04-18T02:00:00+00:00",
                "revenue": 101.0,
            },
        ],
    )
    pit = tmp_path / "ordered-same-day-revision-pit"
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=revenue"],
        )
    )

    events = pd.read_parquet(pit / "data")
    state = load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260418")

    assert len(events) == 2
    assert state.loc[0, "revenue"] == 101.0
    assert state.loc[0, "revenue__source_run_id"] == "income-second"


def test_publication_repoints_aliases_only_after_validation(tmp_path) -> None:
    raw = _write_raw_income(tmp_path)
    normalized = tmp_path / "normalized"
    pit = tmp_path / "pit"
    build_normalized_fundamentals(dataset="income", raw_dir=raw, out_dir=normalized)
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=income_revenue"],
        )
    )

    result = publish_fundamentals_assets(
        artifacts_root=tmp_path / "platform-root",
        normalized_dirs=[normalized],
        pit_dir=pit,
        target_date="20260529",
    )

    contract = json.loads(
        (
            tmp_path / "platform-root" / "metadata" / "current_assets" / "a_share_current.json"
        ).read_text(encoding="utf-8")
    )
    assert result["status"] == "published"
    assert contract["assets"]["normalized_fundamentals"]["exists"] is True
    assert contract["assets"]["pit_fundamentals"]["exists"] is True
    assert (tmp_path / "platform-root" / "metadata" / "dataset_registry.csv").read_text(
        encoding="utf-8"
    ).count("a_share_pit_fundamentals") == 1


def test_union_builder_assembles_multiple_normalized_components(tmp_path) -> None:
    income = _write_normalized_asset(
        tmp_path,
        "income-normalized",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "income-run",
                "_source_retrieved_at": "2026-05-29T01:00:00+00:00",
                "revenue": 100.0,
            }
        ],
    )
    balancesheet = _write_normalized_asset(
        tmp_path,
        "balancesheet-normalized",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "balancesheet",
                "_source_raw_asset": "raw-balancesheet",
                "_source_run_id": "balancesheet-run",
                "_source_retrieved_at": "2026-05-29T01:00:00+00:00",
                "total_assets": 1000.0,
            },
            {
                "symbol": "000001.SZ",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "balancesheet",
                "_source_raw_asset": "raw-balancesheet",
                "_source_run_id": "balancesheet-run",
                "_source_retrieved_at": "2026-05-29T01:00:00+00:00",
                "total_assets": 200.0,
            },
        ],
    )
    composite = tmp_path / "normalized-union"

    manifest = build_normalized_fundamentals_union(
        normalized_dirs=[income, balancesheet],
        out_dir=composite,
    )

    assert manifest["schema_version"] == "tushare.a_share.fundamentals.normalized.v2"
    assert manifest["dataset"] == "normalized_fundamentals"
    assert manifest["immutable_snapshot"] is True
    assert set(manifest["components"]) == {"income", "balancesheet"}
    for name in ("income", "balancesheet"):
        component_dir = composite / "components" / name
        assert (component_dir / "data" / "part.parquet").is_file()
        assert (component_dir / "manifest.yml").is_file()
        assert (component_dir / "manifest.seal.json").is_file()
    assert all(asset_integrity_checks(composite, manifest["integrity"]).values())
    assert all(manifest_seal_checks(composite).values())
    assert manifest["totals"]["rows"] == 3
    assert manifest["totals"]["symbols"] == 2

    validation = validate_normalized_fundamentals(
        asset_dir=composite,
        target_date="20260529",
    )
    assert validation["status"] == "passed"
    checks = {row["id"]: row for row in validation["checks"]}
    assert checks["observation_vintage_supports_target_date"]["passed"] is True


def test_publish_multiple_normalized_dirs_builds_composite_latest(tmp_path) -> None:
    income = _write_normalized_asset(
        tmp_path,
        "income-normalized",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "income-run",
                "_source_retrieved_at": "2026-05-29T01:00:00+00:00",
                "revenue": 100.0,
            }
        ],
    )
    balancesheet = _write_normalized_asset(
        tmp_path,
        "balancesheet-normalized",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "balancesheet",
                "_source_raw_asset": "raw-balancesheet",
                "_source_run_id": "balancesheet-run",
                "_source_retrieved_at": "2026-05-29T01:00:00+00:00",
                "total_assets": 1000.0,
            }
        ],
    )
    pit = tmp_path / "pit"
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[income, balancesheet],
            out_dir=pit,
            field_mappings=["revenue=revenue", "total_assets=total_assets"],
        )
    )

    result = publish_fundamentals_assets(
        artifacts_root=tmp_path / "platform-root",
        normalized_dirs=[income, balancesheet],
        pit_dir=pit,
        target_date="20260529",
    )

    assert result["status"] == "published"
    assert result["composite_validation"]["status"] == "passed"
    contract = json.loads(
        (
            tmp_path / "platform-root" / "metadata" / "current_assets" / "a_share_current.json"
        ).read_text(encoding="utf-8")
    )
    alias = candidate_asset_paths(tmp_path / "platform-root")["normalized_fundamentals"]
    entry = contract["assets"]["normalized_fundamentals"]
    assert entry["exists"] is True
    assert entry["is_symlink"] is True
    assert alias.is_symlink()
    resolved = alias.resolve()
    assert resolved.is_relative_to((tmp_path / "platform-root").resolve())
    assert resolved.name.startswith("a_share_all_normalized_fundamentals_")
    assert (resolved / "components" / "income" / "manifest.yml").is_file()
    assert (resolved / "components" / "balancesheet" / "manifest.yml").is_file()
    assert entry["manifest"]["dataset"] == "normalized_fundamentals"
    assert entry["manifest"]["schema_version"] == "tushare.a_share.fundamentals.normalized.v2"
    assert (
        validate_normalized_fundamentals(asset_dir=resolved, target_date="20260529")["status"]
        == "passed"
    )
    registry_text = (tmp_path / "platform-root" / "metadata" / "dataset_registry.csv").read_text(
        encoding="utf-8"
    )
    assert registry_text.count("a_share_normalized_fundamentals") == 1


def test_publish_missing_normalized_dataset_fails_before_alias_switch(tmp_path) -> None:
    income = _write_normalized_asset(
        tmp_path,
        "income-normalized",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "income-run",
                "_source_retrieved_at": "2026-05-29T01:00:00+00:00",
                "revenue": 100.0,
            }
        ],
    )
    pit = tmp_path / "pit"
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[income],
            out_dir=pit,
            field_mappings=["revenue=revenue"],
        )
    )
    missing = tmp_path / "missing-normalized"

    with pytest.raises(ValueError, match="Normalized fundamentals"):
        publish_fundamentals_assets(
            artifacts_root=tmp_path / "platform-root",
            normalized_dirs=[income, missing],
            pit_dir=pit,
            target_date="20260529",
        )
    aliases = candidate_asset_paths(tmp_path / "platform-root")
    assert not aliases["normalized_fundamentals"].is_symlink()
    assert not aliases["pit_fundamentals"].is_symlink()
    assert not (
        tmp_path / "platform-root" / "metadata" / "current_assets" / "a_share_current.json"
    ).exists()


def test_publish_invalid_normalized_component_fails_without_publication(tmp_path) -> None:
    income = _write_normalized_asset(
        tmp_path,
        "income-normalized",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "income",
                "_source_raw_asset": "raw-income",
                "_source_run_id": "income-run",
                "_source_retrieved_at": "2026-05-29T01:00:00+00:00",
                "revenue": 100.0,
            }
        ],
    )
    balancesheet = _write_normalized_asset(
        tmp_path,
        "balancesheet-normalized",
        [
            {
                "symbol": "600519.SH",
                "report_period": "20251231",
                "disclosure_date": "20260417",
                "_source_dataset": "balancesheet",
                "_source_raw_asset": "raw-balancesheet",
                "_source_run_id": "balancesheet-run",
                "_source_retrieved_at": "2026-05-29T01:00:00+00:00",
                "total_assets": 1000.0,
            }
        ],
    )
    (balancesheet / "data" / "part.parquet").unlink()
    pit = tmp_path / "pit"
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[income],
            out_dir=pit,
            field_mappings=["revenue=revenue"],
        )
    )

    with pytest.raises(ValueError, match="Normalized fundamentals"):
        publish_fundamentals_assets(
            artifacts_root=tmp_path / "platform-root",
            normalized_dirs=[income, balancesheet],
            pit_dir=pit,
            target_date="20260529",
        )
    aliases = candidate_asset_paths(tmp_path / "platform-root")
    assert not aliases["normalized_fundamentals"].is_symlink()
    assert not aliases["pit_fundamentals"].is_symlink()


def test_pit_only_publication_requires_but_does_not_repoint_normalized_alias(tmp_path) -> None:
    raw = _write_raw_income(tmp_path)
    normalized = tmp_path / "normalized"
    pit = tmp_path / "pit"
    build_normalized_fundamentals(dataset="income", raw_dir=raw, out_dir=normalized)
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=income_revenue"],
        )
    )
    aliases = candidate_asset_paths(
        tmp_path / "platform-root",
        market="a_share",
        provider="tushare",
    )
    normalized_alias = aliases["normalized_fundamentals"]
    normalized_alias.parent.mkdir(parents=True, exist_ok=True)
    normalized_alias.symlink_to(normalized)

    result = publish_pit_fundamentals_asset(
        artifacts_root=tmp_path / "platform-root",
        pit_dir=pit,
        target_date="20260529",
    )

    contract = json.loads(
        (
            tmp_path / "platform-root" / "metadata" / "current_assets" / "a_share_current.json"
        ).read_text(encoding="utf-8")
    )
    assert result["status"] == "published"
    assert contract["contract"]["generated_by"] == (
        "marketdata tushare publish-a-share-pit-fundamentals"
    )
    assert contract["assets"]["pit_fundamentals"]["exists"] is True
    assert contract["assets"]["normalized_fundamentals"]["exists"] is True
    assert normalized_alias.resolve() == normalized.resolve()
    assert (tmp_path / "platform-root" / "metadata" / "dataset_registry.csv").read_text(
        encoding="utf-8"
    ).count("a_share_pit_fundamentals") == 1


def test_pit_only_publication_rejects_missing_normalized_alias(tmp_path) -> None:
    raw = _write_raw_income(tmp_path)
    normalized = tmp_path / "normalized"
    pit = tmp_path / "pit"
    build_normalized_fundamentals(dataset="income", raw_dir=raw, out_dir=normalized)
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=income_revenue"],
        )
    )

    with pytest.raises(ValueError, match="requires an existing normalized_fundamentals"):
        publish_pit_fundamentals_asset(
            artifacts_root=tmp_path / "platform-root",
            pit_dir=pit,
            target_date="20260529",
        )


def test_fundamentals_target_date_freshness_is_fail_closed(tmp_path) -> None:
    raw = _write_raw_income(tmp_path)
    normalized = tmp_path / "normalized"
    pit = tmp_path / "pit"
    build_normalized_fundamentals(dataset="income", raw_dir=raw, out_dir=normalized)
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=income_revenue"],
        )
    )

    normalized_validation = validate_normalized_fundamentals(
        asset_dir=normalized,
        target_date="20260602",
    )
    pit_validation = validate_pit_fundamentals(
        asset_dir=pit,
        target_date="20260602",
    )

    assert normalized_validation["status"] == "failed"
    assert pit_validation["status"] == "failed"
    normalized_checks = {row["id"]: row for row in normalized_validation["checks"]}
    pit_checks = {row["id"]: row for row in pit_validation["checks"]}
    assert normalized_checks["observation_vintage_supports_target_date"]["passed"] is False
    assert pit_checks["observation_vintage_supports_target_date"]["passed"] is False
    assert normalized_validation["observation_state"]["observation_age_days"] == 4
    assert normalized_validation["observation_state"]["revision_covered"] is True
    assert normalized_validation["observation_state"]["freshness_verified"] is False
    with pytest.raises(ValueError, match="observation_vintage_supports_target_date"):
        publish_fundamentals_assets(
            artifacts_root=tmp_path / "platform-root",
            normalized_dirs=[normalized],
            pit_dir=pit,
            target_date="20260602",
        )
    aliases = candidate_asset_paths(
        tmp_path / "platform-root",
        market="a_share",
        provider="tushare",
    )
    assert not aliases["normalized_fundamentals"].is_symlink()
    assert not aliases["pit_fundamentals"].is_symlink()


def test_legacy_pit_schema_cannot_pass_production_target_validation(tmp_path) -> None:
    raw = _write_raw_income(tmp_path)
    normalized = tmp_path / "normalized"
    pit = tmp_path / "pit"
    build_normalized_fundamentals(dataset="income", raw_dir=raw, out_dir=normalized)
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[normalized],
            out_dir=pit,
            field_mappings=["revenue=income_revenue"],
        )
    )
    manifest_path = pit / "manifest.yml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "tushare.a_share.fundamentals.pit.v1"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    seal_manifest(manifest_path)

    validation = validate_pit_fundamentals(asset_dir=pit, target_date="20260529")

    checks = {row["id"]: row for row in validation["checks"]}
    assert validation["status"] == "failed"
    assert checks["revision_safe_schema"]["passed"] is False
    with pytest.raises(PitProvenanceError, match="legacy assets are fail-closed"):
        load_pit_fundamentals_as_of(asset_dir=pit, as_of_date="20260403")


def test_current_contract_flags_legacy_pit_as_stale_and_missing_normalized(
    tmp_path,
) -> None:
    root = tmp_path / "platform-root"
    snapshot = root / "snapshots" / "pit-query-through-20260529"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "tushare.a_share.fundamentals.pit.v1",
                "dataset": "pit_fundamentals",
                "status": "completed",
                "query": {"start_date": "19900101", "end_date": "20260529"},
            }
        ),
        encoding="utf-8",
    )
    aliases = candidate_asset_paths(root, market="a_share", provider="tushare")
    aliases["pit_fundamentals"].parent.mkdir(parents=True, exist_ok=True)
    aliases["pit_fundamentals"].symlink_to(snapshot)
    contract = build_current_contract(
        root,
        market="a_share",
        provider="tushare",
        target_date="20260529",
    )
    contract_path = current_contract_path(root, market="a_share")
    write_current_contract(contract_path, contract)

    health = inspect_current_contract(
        ContractInspectionOptions(
            artifacts_root=root,
            market="a_share",
            provider="tushare",
            target_date="20260529",
            assets=["pit_fundamentals", "normalized_fundamentals"],
        )
    )

    assert contract["assets"]["pit_fundamentals"]["as_of"] is None
    assert health["summary"]["missing_assets"] == 1
    assert health["summary"]["stale_assets"] == 1
    checks = {(row["check"], row["asset_key"]) for row in health["quality_checks"]}
    assert ("asset_as_of_missing_for_target_date", "pit_fundamentals") in checks
    assert ("current_asset_missing", "normalized_fundamentals") in checks


def test_fundamentals_cli_commands_are_exposed() -> None:
    parser = build_parser()

    plan = parser.parse_args(
        [
            "tushare",
            "plan-a-share-fundamentals",
            "--dataset",
            "income",
            "--start-date",
            "20250101",
            "--end-date",
            "20251231",
        ]
    )
    download = parser.parse_args(
        [
            "tushare",
            "download-a-share-fundamentals",
            "--dataset",
            "income",
            "--out-dir",
            "raw",
            "--start-date",
            "20250101",
            "--end-date",
            "20251231",
        ]
    )
    publish_pit = parser.parse_args(
        [
            "tushare",
            "publish-a-share-pit-fundamentals",
            "--pit-dir",
            "pit",
            "--target-date",
            "20260529",
        ]
    )

    assert plan.tushare_command == "plan-a-share-fundamentals"
    assert download.tushare_command == "download-a-share-fundamentals"
    assert publish_pit.tushare_command == "publish-a-share-pit-fundamentals"
