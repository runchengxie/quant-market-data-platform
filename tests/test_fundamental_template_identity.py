"""Template identity survives normalization, coalescing, and flat PIT reads."""

import json

import pytest
import yaml

from quant_market_data_platform.providers.tushare_a_share_fundamentals import (
    FieldValidationError,
    PitBuildOptions,
    build_normalized_fundamentals,
    build_pit_fundamentals,
    load_pit_fundamentals_as_of_panel,
    load_pit_fundamentals_as_of_panel_from_vintages,
    load_pit_fundamentals_as_of_view,
    validate_pit_fundamentals,
)
from quant_market_data_platform.providers.tushare_a_share_fundamentals_part02 import (
    _coalesce_pit_frame,
)
from quant_market_data_platform.providers.tushare_a_share_fundamentals_support import (
    assert_unambiguous_pit_events,
    build_asset_integrity,
    seal_manifest,
)
from tests.test_tushare_a_share_fundamentals import (
    _write_complete_raw_manifest,
    _write_normalized_asset,
)


def test_normalization_preserves_templates_before_revision_selection(tmp_path):
    pd = pytest.importorskip("pandas")
    raw = tmp_path / "template-raw"
    part = raw / "data" / "income" / "part.parquet"
    part.parent.mkdir(parents=True)
    base: dict[str, object] = {
        "ts_code": "603093.SH",
        "ann_date": "20260421",
        "f_ann_date": "20260421",
        "end_date": "20260331",
        "report_type": "1",
    }
    pd.DataFrame(
        [
            base | {"comp_type": " 2 ", "update_flag": "0", "rd_exp": None},
            base | {"comp_type": "7", "update_flag": "0", "rd_exp": 10.0},
            base | {"comp_type": "7", "update_flag": "1", "rd_exp": 20.0},
        ]
    ).to_parquet(part, index=False)
    _write_complete_raw_manifest(
        raw,
        source_run_id="templates",
        parts=[
            {
                "path": str(part),
                "retrieved_at": "2026-04-22T01:00:00+00:00",
            }
        ],
    )
    output = tmp_path / "normalized-templates"
    manifest = build_normalized_fundamentals(dataset="income", raw_dir=raw, out_dir=output)
    result = pd.read_parquet(output / "data/part.parquet").sort_values("comp_type")
    assert result.comp_type.tolist() == ["2", "7"]
    assert result.rd_exp.iloc[1] == 20.0
    assert pd.isna(result.rd_exp.iloc[0])
    assert "comp_type" in manifest["semantics"]["dedupe_keys"]
    assert manifest["semantics"]["statement_template_identity"] == "comp_type"
    with pytest.raises(FieldValidationError, match="multiple company templates"):
        build_pit_fundamentals(
            PitBuildOptions(
                normalized_dirs=[output],
                out_dir=tmp_path / "pit-templates",
                field_mappings=["rd_exp=rd_exp"],
                batch_rows=1,
            )
        )


@pytest.mark.parametrize("values", [(None, 20.0), (20.0, 20.0), (10.0, 20.0)])
def test_pit_rejects_multiple_templates_across_parts_and_batches(tmp_path, values):
    pd = pytest.importorskip("pandas")
    base: dict[str, object] = {
        "symbol": "603093.SH",
        "report_period": "20260331",
        "disclosure_date": "20260421",
        "_source_dataset": "income",
        "_source_raw_asset": "raw-income",
        "_source_run_id": "income-run",
        "_source_retrieved_at": "2026-04-22T01:00:00+00:00",
    }
    source = _write_normalized_asset(
        tmp_path,
        "template-input",
        [
            base | {"comp_type": "2", "rd_exp": values[0]},
            base | {"comp_type": "7", "rd_exp": values[1]},
        ],
    )
    frame = pd.read_parquet(source / "data/part.parquet")
    for index in range(2):
        frame.iloc[index : index + 1].to_parquet(
            source / f"data/split-{index}.parquet", index=False
        )
    (source / "data/part.parquet").unlink()
    # Refresh fixture integrity after splitting, without altering its semantic contract.
    manifest = yaml.safe_load((source / "manifest.yml").read_text())
    manifest["integrity"] = build_asset_integrity(
        source, sorted((source / "data").glob("*.parquet"))
    )
    (source / "manifest.yml").write_text(yaml.safe_dump(manifest))
    seal_manifest(source / "manifest.yml")
    with pytest.raises(FieldValidationError, match="multiple company templates"):
        build_pit_fundamentals(
            PitBuildOptions(
                normalized_dirs=[source],
                out_dir=tmp_path / "pit",
                field_mappings=["rd_exp=rd_exp"],
                batch_rows=1,
            )
        )


@pytest.mark.parametrize("panel", [False, True])
@pytest.mark.parametrize(
    "second_retrieval", ["2026-04-22T01:00:00+00:00", "2026-04-22T02:00:00+00:00"]
)
def test_flat_pit_queries_reject_template_ambiguity_with_null_values(
    tmp_path, panel, second_retrieval
):
    base: dict[str, object] = {
        "symbol": "603093.SH",
        "report_period": "20260331",
        "disclosure_date": "20260421",
        "available_date": "20260422",
        "trade_date": "20260422",
        "_source_dataset": "income",
        "_source_raw_asset": "raw-income",
        "_source_run_id": "income-run",
        "_source_retrieved_at": "2026-04-22T01:00:00+00:00",
    }
    source = _write_normalized_asset(
        tmp_path,
        "pit-query-templates",
        [
            base | {"_source_comp_type": "2", "rd_exp": None},
            base
            | {"_source_comp_type": "7", "rd_exp": 20.0, "_source_retrieved_at": second_retrieval},
        ],
    )
    with pytest.raises(FieldValidationError, match="multiple company templates"):
        if panel:
            load_pit_fundamentals_as_of_panel(
                asset_dir=source,
                as_of_dates=["20260422"],
                fields=["rd_exp"],
                provenance_policy="allow_unverified",
            )
        else:
            load_pit_fundamentals_as_of_view(
                asset_dir=source,
                as_of_date="20260422",
                fields=["rd_exp"],
                provenance_policy="allow_unverified",
            )


def test_streamed_pit_validation_rejects_templates_across_retrievals(tmp_path):
    base: dict[str, object] = {
        "symbol": "603093.SH",
        "report_period": "20260331",
        "disclosure_date": "20260421",
        "available_date": "20260422",
        "trade_date": "20260422",
        "_source_dataset": "income",
        "_source_raw_asset": "raw-income",
        "_source_run_id": "run",
    }
    source = _write_normalized_asset(
        tmp_path,
        "validation-templates",
        [
            base
            | {
                "_source_comp_type": "2",
                "rd_exp": None,
                "_source_retrieved_at": "2026-04-22T01:00:00+00:00",
            },
            base
            | {
                "_source_comp_type": "7",
                "rd_exp": 20.0,
                "_source_retrieved_at": "2026-04-22T02:00:00+00:00",
            },
        ],
    )
    validation = validate_pit_fundamentals(asset_dir=source, batch_rows=1)
    checks = {row["id"]: row for row in validation["checks"]}
    assert checks["unique_statement_template_per_event"]["passed"] is False


def test_single_template_pit_preserves_optional_field_provenance(tmp_path):
    from quant_market_data_platform.providers.tushare_a_share_fundamentals_pit import (
        load_pit_fundamentals_events,
    )

    base: dict[str, object] = {
        "symbol": "603093.SH",
        "report_period": "20260331",
        "disclosure_date": "20260421",
        "_source_dataset": "income",
        "_source_raw_asset": "raw-income",
        "_source_run_id": "run",
        "_source_retrieved_at": "2026-04-22T01:00:00+00:00",
        "comp_type": "7",
        "rd_exp": 20.0,
    }
    source = _write_normalized_asset(tmp_path, "single-template", [base])
    pit = tmp_path / "pit"
    build_pit_fundamentals(
        PitBuildOptions(
            normalized_dirs=[source],
            out_dir=pit,
            field_mappings=["rd_exp=rd_exp"],
            batch_rows=1,
        )
    )
    events = load_pit_fundamentals_events(asset_dir=pit)
    assert events.value_columns == ("rd_exp",)
    assert events.frame._source_comp_type.map(json.loads).tolist() == [{"income": "7"}]
    view = load_pit_fundamentals_as_of_view(asset_dir=pit, as_of_date="20260422")
    assert view.frame["rd_exp__source_comp_type"].map(json.loads).tolist() == [{"income": "7"}]


def test_separately_coalesced_vintages_preserve_source_template_association(tmp_path):
    pd = pytest.importorskip("pandas")
    base: dict[str, object] = {
        "symbol": "603093.SH",
        "report_period": "20260331",
        "disclosure_date": "20260421",
        "available_date": "20260422",
        "trade_date": "20260422",
        "_source_raw_asset": "raw",
        "_source_run_id": "run",
        "_source_bundle_retrieval_start_date": "20260422",
        "_source_bundle_available_date": "20260422",
        "rd_exp": 20.0,
    }
    vintages = []
    for timestamp, templates in [("01", ("2", "7")), ("02", ("7", "2"))]:
        rows = [
            base
            | {
                "_source_dataset": dataset,
                "_source_comp_type": template,
                "_source_retrieved_at": f"2026-04-22T{timestamp}:00:00+00:00",
            }
            for dataset, template in zip(("income", "balancesheet"), templates, strict=True)
        ]
        vintages.append(_coalesce_pit_frame(pd.DataFrame(rows), ["rd_exp"]))
    assert json.loads(vintages[0].iloc[0]._source_comp_type) == {"income": "2", "balancesheet": "7"}
    assert json.loads(vintages[1].iloc[0]._source_comp_type) == {"income": "7", "balancesheet": "2"}
    with pytest.raises(FieldValidationError, match="multiple company templates"):
        assert_unambiguous_pit_events(pd.concat(vintages, ignore_index=True), ["rd_exp"])

    assets = [
        _write_normalized_asset(tmp_path, f"vintage-{index}", vintage.to_dict("records"))
        for index, vintage in enumerate(vintages)
    ]
    with pytest.raises(FieldValidationError, match="multiple company templates"):
        load_pit_fundamentals_as_of_panel_from_vintages(
            asset_dirs=assets,
            as_of_dates=["20260422"],
            fields=["rd_exp"],
            provenance_policy="allow_unverified",
        )
    combined = _write_normalized_asset(tmp_path, "combined", pd.concat(vintages).to_dict("records"))
    validation = validate_pit_fundamentals(asset_dir=combined, batch_rows=1)
    checks = {row["id"]: row for row in validation["checks"]}
    assert checks["unique_statement_template_per_event"]["passed"] is False
