from __future__ import annotations

import json

import pytest
import yaml

from market_data_platform.cli import build_parser, main
from market_data_platform.contract import build_current_contract, write_current_contract
from market_data_platform.paths import (
    candidate_asset_paths,
    current_contract_path,
)
from market_data_platform.providers.tushare_a_share_research import (
    IndustryChangesColumnMap,
    build_a_share_industry_changes,
    build_a_share_pit_fundamentals,
    download_a_share_industry_membership,
    validate_a_share_industry_changes,
    validate_a_share_pit_fundamentals,
)
from market_data_platform.registry import build_dataset_registry_rows


def test_build_and_validate_a_share_pit_fundamentals_asset(tmp_path):
    pd = pytest.importorskip("pandas")
    source = tmp_path / "fundamentals.csv"
    pd.DataFrame(
        [
            {
                "ts_code": "600519.SH",
                "end_date": "20251231",
                "ann_date": "20260330",
                "roe": 0.31,
                "debt_to_assets": 0.12,
            },
            {
                "ts_code": "000001.SZ",
                "end_date": "20251231",
                "ann_date": "20260401",
                "roe": 0.11,
                "debt_to_assets": 0.62,
            },
        ]
    ).to_csv(source, index=False)
    out_dir = tmp_path / "pit"

    manifest = build_a_share_pit_fundamentals(
        source_file=source,
        out_dir=out_dir,
        available_delay_days=2,
        field_maps=["roe=return_on_equity"],
        min_rows=2,
        min_symbols=2,
    )

    assert manifest["schema_version"] == "tushare.a_share.pit_fundamentals.v1"
    assert manifest["semantics"]["field_mappings"]["roe"] == "return_on_equity"
    data = pd.read_parquet(out_dir / "data" / "part.parquet")
    assert data["available_date"].tolist() == ["20260401", "20260403"]
    assert data["trade_date"].tolist() == ["20260401", "20260403"]
    assert "return_on_equity" in data.columns
    result = validate_a_share_pit_fundamentals(
        asset_dir=out_dir,
        min_rows=2,
        min_symbols=2,
    )
    assert result["status"] == "passed"


def test_build_and_validate_a_share_industry_changes_asset(tmp_path):
    pd = pytest.importorskip("pandas")
    source = tmp_path / "industry.csv"
    pd.DataFrame(
        [
            {
                "symbol": "600519.SH",
                "start_date": "20240101",
                "end_date": "",
                "industry_code": "801120",
                "industry_name": "食品饮料",
            },
            {
                "symbol": "000001.SZ",
                "start_date": "20240101",
                "end_date": "20251231",
                "industry_code": "801780",
                "industry_name": "银行",
            },
        ]
    ).to_csv(source, index=False)
    out_dir = tmp_path / "industry"

    manifest = build_a_share_industry_changes(
        source_file=source,
        out_dir=out_dir,
        industry_system="sw2021",
        min_rows=2,
        min_symbols=2,
    )

    assert manifest["schema_version"] == "licensed.a_share.industry_changes.v1"
    assert manifest["semantics"]["historical_membership"] is True
    data = pd.read_parquet(out_dir / "data" / "part.parquet")
    assert data["industry_system"].unique().tolist() == ["sw2021"]
    result = validate_a_share_industry_changes(
        asset_dir=out_dir,
        min_rows=2,
        min_symbols=2,
    )
    assert result["status"] == "passed"


def test_build_a_share_industry_changes_from_tushare_interval_fields(tmp_path):
    pd = pytest.importorskip("pandas")
    source = tmp_path / "tushare_index_member_all.csv"
    pd.DataFrame(
        [
            {
                "ts_code": "600519.SH",
                "l3_code": "801123",
                "l3_name": "白酒",
                "in_date": "20210101",
                "out_date": "",
                "is_new": "Y",
            },
            {
                "ts_code": "000001.SZ",
                "l3_code": "801784",
                "l3_name": "股份制银行",
                "in_date": "20140101",
                "out_date": "20211210",
                "is_new": "N",
            },
        ]
    ).to_csv(source, index=False)
    out_dir = tmp_path / "industry_tushare"

    manifest = build_a_share_industry_changes(
        source_file=source,
        out_dir=out_dir,
        columns=IndustryChangesColumnMap(
            effective_date="in_date",
            end_date="out_date",
            industry_code="l3_code",
            industry_name="l3_name",
        ),
        industry_system="sw2021_l3",
        provider="tushare",
        min_rows=2,
        min_symbols=2,
    )

    assert manifest["schema_version"] == "licensed.a_share.industry_changes.v1"
    assert manifest["provider"] == "tushare"
    assert manifest["query"]["start_date"] == "20140101"
    assert manifest["query"]["end_date"] == "20211210"
    data = pd.read_parquet(out_dir / "data" / "part.parquet")
    assert data["symbol"].tolist() == ["000001.SZ", "600519.SH"]
    assert data["effective_date"].tolist() == ["20140101", "20210101"]
    assert data["end_date"].tolist() == ["20211210", ""]
    assert data["industry_system"].unique().tolist() == ["sw2021_l3"]
    result = validate_a_share_industry_changes(
        asset_dir=out_dir,
        min_rows=2,
        min_symbols=2,
    )
    assert result["status"] == "passed"


class FakeIndustryClient:
    def __init__(self) -> None:
        self.calls = []

    def index_classify(self, *, level, src):
        assert level == "L3"
        assert src == "SW2021"
        return self._pd().DataFrame(
            [
                {"index_code": "850111.SI", "industry_name": "种植业", "level": "L3"},
                {"index_code": "850121.SI", "industry_name": "渔业", "level": "L3"},
            ]
        )

    def index_member_all(self, **kwargs):
        self.calls.append(kwargs)
        l3_code = kwargs["l3_code"]
        is_new = kwargs["is_new"]
        rows = {
            ("850111.SI", "Y"): [
                {
                    "l1_code": "801010.SI",
                    "l1_name": "农林牧渔",
                    "l2_code": "801011.SI",
                    "l2_name": "种植业",
                    "l3_code": "850111.SI",
                    "l3_name": "种子",
                    "ts_code": "600313.SH",
                    "name": "农发种业",
                    "in_date": "20211213",
                    "out_date": "",
                    "is_new": "Y",
                }
            ],
            ("850111.SI", "N"): [
                {
                    "l1_code": "801010.SI",
                    "l1_name": "农林牧渔",
                    "l2_code": "801011.SI",
                    "l2_name": "种植业",
                    "l3_code": "850111.SI",
                    "l3_name": "种子",
                    "ts_code": "000998.SZ",
                    "name": "隆平高科",
                    "in_date": "20140101",
                    "out_date": "20211212",
                    "is_new": "N",
                }
            ],
            ("850121.SI", "Y"): [
                {
                    "l1_code": "801010.SI",
                    "l1_name": "农林牧渔",
                    "l2_code": "801012.SI",
                    "l2_name": "渔业",
                    "l3_code": "850121.SI",
                    "l3_name": "水产养殖",
                    "ts_code": "300094.SZ",
                    "name": "国联水产",
                    "in_date": "20211213",
                    "out_date": "",
                    "is_new": "Y",
                }
            ],
        }
        return self._pd().DataFrame(rows.get((l3_code, is_new), []))

    @staticmethod
    def _pd():
        return pytest.importorskip("pandas")


def test_download_a_share_industry_membership_source_then_build_changes(tmp_path):
    source_dir = tmp_path / "membership"
    client = FakeIndustryClient()
    manifest = download_a_share_industry_membership(
        out_dir=source_dir,
        client=client,
        request_interval_seconds=0,
        min_rows=3,
        min_symbols=3,
    )

    assert manifest["schema_version"] == "tushare.a_share.industry_membership_source.v1"
    assert manifest["query"]["queries"] == 4
    assert manifest["query"]["empty_queries"] == 1
    assert manifest["totals"]["rows"] == 3
    assert (source_dir / "classifications.parquet").is_file()
    assert (source_dir / "data" / "part.parquet").is_file()
    assert len(client.calls) == 4

    out_dir = tmp_path / "industry_changes"
    changes = build_a_share_industry_changes(
        source_file=source_dir / "data" / "part.parquet",
        out_dir=out_dir,
        columns=IndustryChangesColumnMap(
            effective_date="in_date",
            end_date="out_date",
            industry_code="l3_code",
            industry_name="l3_name",
        ),
        industry_system="sw2021_l3",
        provider="tushare",
        min_rows=3,
        min_symbols=3,
    )
    assert changes["provider"] == "tushare"
    assert changes["totals"]["rows"] == 3
    assert validate_a_share_industry_changes(asset_dir=out_dir, min_rows=3)["status"] == "passed"


def test_build_a_share_industry_changes_rejects_current_snapshot_without_history(tmp_path):
    pd = pytest.importorskip("pandas")
    source = tmp_path / "current_industry_snapshot.csv"
    pd.DataFrame(
        [
            {
                "symbol": "600519.SH",
                "industry_code": "801120",
                "industry_name": "食品饮料",
            }
        ]
    ).to_csv(source, index=False)

    with pytest.raises(ValueError, match="Missing effective date column"):
        build_a_share_industry_changes(
            source_file=source,
            out_dir=tmp_path / "industry",
            industry_system="sw2021",
        )


RESEARCH_ASSET_EXPECTED_NAMES = {
    "pit_fundamentals": "a_share_all_pit_fundamentals_latest",
    "industry_changes": "a_share_all_industry_changes_latest",
    "moneyflow": "a_share_all_moneyflow_latest",
    "moneyflow_dc": "a_share_all_moneyflow_dc_latest",
    "moneyflow_hsgt": "a_share_all_moneyflow_hsgt_latest",
    "flow_ownership_features": "a_share_all_flow_ownership_features_latest",
    "top_inst": "a_share_all_top_inst_latest",
    "ths_hot": "a_share_all_ths_hot_latest",
    "dc_concept": "a_share_all_dc_concept_latest",
    "dc_concept_cons": "a_share_all_dc_concept_cons_latest",
    "kpl_list": "a_share_all_kpl_list_latest",
    "kpl_concept_cons": "a_share_all_kpl_concept_cons_latest",
    "limit_step": "a_share_all_limit_step_latest",
    "limit_cpt_list": "a_share_all_limit_cpt_list_latest",
    "report_rc": "a_share_all_report_rc_latest",
    "stk_surv": "a_share_all_stk_surv_latest",
    "broker_recommend": "a_share_all_broker_recommend_latest",
    "top_inst_events": "a_share_all_top_inst_events_latest",
    "hotspot_features": "a_share_all_hotspot_features_latest",
    "fund_portfolio": "a_share_all_fund_portfolio_latest",
    "fund_portfolio_features": "a_share_all_fund_portfolio_features_latest",
    "top10_holders": "a_share_all_top10_holders_latest",
    "top10_floatholders": "a_share_all_top10_floatholders_latest",
    "stk_holdertrade": "a_share_all_stk_holdertrade_latest",
    "holder_structure_features": "a_share_all_holder_structure_features_latest",
    "holdertrade_events": "a_share_all_holdertrade_events_latest",
    "hsgt_market_features": "a_share_all_hsgt_market_features_latest",
}

RESEARCH_ASSET_DATASETS = {
    "moneyflow": "moneyflow",
    "moneyflow_dc": "moneyflow_dc",
    "moneyflow_hsgt": "moneyflow_hsgt",
    "flow_ownership_features": "flow_ownership_features",
    "top_inst": "top_inst",
    "ths_hot": "ths_hot",
    "dc_concept": "dc_concept",
    "dc_concept_cons": "dc_concept_cons",
    "kpl_list": "kpl_list",
    "kpl_concept_cons": "kpl_concept_cons",
    "limit_step": "limit_step",
    "limit_cpt_list": "limit_cpt_list",
    "report_rc": "report_rc",
    "stk_surv": "stk_surv",
    "broker_recommend": "broker_recommend",
    "top_inst_events": "top_inst_events",
    "hotspot_features": "hotspot_features",
    "fund_portfolio": "fund_portfolio",
    "fund_portfolio_features": "fund_portfolio_features",
    "top10_holders": "top10_holders",
    "top10_floatholders": "top10_floatholders",
    "stk_holdertrade": "stk_holdertrade",
    "holder_structure_features": "holder_structure_features",
    "holdertrade_events": "holdertrade_events",
    "hsgt_market_features": "hsgt_market_features",
    "pit_fundamentals": "pit_fundamentals",
    "industry_changes": "industry_changes",
}

RESEARCH_DATASET_SOURCES = {
    "a_share_moneyflow": "tushare",
    "a_share_moneyflow_dc": "tushare",
    "a_share_moneyflow_hsgt": "tushare",
    "a_share_flow_ownership_features": "derived",
    "a_share_top_inst": "tushare",
    "a_share_ths_hot": "tushare",
    "a_share_dc_concept": "tushare",
    "a_share_dc_concept_cons": "tushare",
    "a_share_kpl_list": "tushare",
    "a_share_kpl_concept_cons": "tushare",
    "a_share_limit_step": "tushare",
    "a_share_limit_cpt_list": "tushare",
    "a_share_report_rc": "tushare",
    "a_share_stk_surv": "tushare",
    "a_share_broker_recommend": "tushare",
    "a_share_top_inst_events": "derived",
    "a_share_hotspot_features": "derived",
    "a_share_fund_portfolio": "tushare",
    "a_share_fund_portfolio_features": "derived",
    "a_share_top10_holders": "tushare",
    "a_share_top10_floatholders": "tushare",
    "a_share_stk_holdertrade": "tushare",
    "a_share_holder_structure_features": "derived",
    "a_share_holdertrade_events": "derived",
    "a_share_hsgt_market_features": "derived",
    "a_share_pit_fundamentals": "tushare",
    "a_share_industry_changes": "licensed_extract",
}


def _write_research_asset_snapshot(assets, key, dataset):
    snapshot = assets[key].parent / f"{assets[key].name}_snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "dataset": dataset,
                "provider": "licensed_extract" if key == "industry_changes" else "tushare",
                "status": "completed",
                "query": {"start_date": "20240101", "end_date": "20260529"},
                "totals": {"rows": 2, "symbols": 2, "files": 1},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assets[key].symlink_to(snapshot.name)


def test_a_share_research_asset_contract_keys_and_registry_rows(tmp_path):
    root = tmp_path / "market-data"
    assets = candidate_asset_paths(root, market="a_share", provider="tushare")
    for key, expected_name in RESEARCH_ASSET_EXPECTED_NAMES.items():
        assert assets[key].name == expected_name

    for key, dataset in RESEARCH_ASSET_DATASETS.items():
        _write_research_asset_snapshot(assets, key, dataset)

    contract = build_current_contract(
        root,
        market="a_share",
        provider="tushare",
        target_date="20260529",
    )
    write_current_contract(current_contract_path(root, market="a_share"), contract)
    rows = {row["dataset_name"]: row for row in build_dataset_registry_rows(contract)}

    for key in RESEARCH_ASSET_DATASETS:
        assert contract["assets"][key]["exists"] is True
    for dataset_name, source in RESEARCH_DATASET_SOURCES.items():
        assert rows[dataset_name]["source"] == source


RESEARCH_ASSET_COMMAND_CASES = (
    (
        [
            "build-a-share-pit-fundamentals",
            "--source-file",
            "source.csv",
            "--out-dir",
            "out",
            "--field-map",
            "roe=return_on_equity",
        ],
        {
            "tushare_command": "build-a-share-pit-fundamentals",
            "field_maps": ["roe=return_on_equity"],
        },
    ),
    (
        [
            "build-a-share-flow-ownership-features",
            "--moneyflow-dir",
            "moneyflow",
            "--daily-dir",
            "daily",
            "--daily-basic-dir",
            "daily-basic",
            "--out-dir",
            "out",
            "--window",
            "20",
        ],
        {"tushare_command": "build-a-share-flow-ownership-features", "windows": [20]},
    ),
    (
        [
            "build-a-share-hotspot-features",
            "--daily-basic-dir",
            "daily-basic",
            "--ths-hot-dir",
            "ths-hot",
            "--dc-concept-dir",
            "dc-concept",
            "--dc-concept-cons-dir",
            "dc-concept-cons",
            "--kpl-list-dir",
            "kpl-list",
            "--out-dir",
            "out",
            "--start-date",
            "20240101",
            "--end-date",
            "20240131",
            "--kpl-concept-cons-dir",
            "kpl-concept-cons",
        ],
        {
            "tushare_command": "build-a-share-hotspot-features",
            "kpl_concept_cons_dir": "kpl-concept-cons",
        },
    ),
    (
        [
            "build-a-share-top-inst-events",
            "--top-inst-dir",
            "top-inst",
            "--daily-dir",
            "daily",
            "--out-dir",
            "out",
            "--window",
            "20",
        ],
        {"tushare_command": "build-a-share-top-inst-events", "top_inst_dir": "top-inst"},
    ),
    (
        [
            "build-a-share-holdertrade-events",
            "--stk-holdertrade-dir",
            "stk-holdertrade",
            "--daily-basic-dir",
            "daily-basic",
            "--out-dir",
            "out",
            "--amount-window",
            "20",
            "--count-window",
            "60",
        ],
        {
            "tushare_command": "build-a-share-holdertrade-events",
            "stk_holdertrade_dir": "stk-holdertrade",
        },
    ),
    (
        [
            "build-a-share-hsgt-market-features",
            "--moneyflow-hsgt-dir",
            "moneyflow-hsgt",
            "--out-dir",
            "out",
            "--window",
            "20",
        ],
        {
            "tushare_command": "build-a-share-hsgt-market-features",
            "moneyflow_hsgt_dir": "moneyflow-hsgt",
        },
    ),
    (
        [
            "build-a-share-fund-portfolio-features",
            "--fund-portfolio-dir",
            "fund-portfolio",
            "--daily-basic-dir",
            "daily-basic",
            "--out-dir",
            "out",
            "--available-delay-days",
            "1",
        ],
        {
            "tushare_command": "build-a-share-fund-portfolio-features",
            "fund_portfolio_dir": "fund-portfolio",
        },
    ),
    (
        [
            "build-a-share-holder-structure-features",
            "--top10-holders-dir",
            "holders",
            "--top10-floatholders-dir",
            "floatholders",
            "--daily-basic-dir",
            "daily-basic",
            "--out-dir",
            "out",
            "--available-delay-days",
            "1",
        ],
        {
            "tushare_command": "build-a-share-holder-structure-features",
            "top10_holders_dir": "holders",
        },
    ),
)


@pytest.mark.parametrize(("argv", "expected"), RESEARCH_ASSET_COMMAND_CASES)
def test_a_share_research_asset_commands_are_exposed(argv, expected):
    parsed = build_parser().parse_args(["tushare", *argv])
    for name, value in expected.items():
        assert getattr(parsed, name) == value


def test_a_share_pit_fundamentals_cli_round_trip(tmp_path, capsys):
    pd = pytest.importorskip("pandas")
    source = tmp_path / "fundamentals.csv"
    pd.DataFrame(
        [{"symbol": "600519.SH", "end_date": "20251231", "ann_date": "20260330", "roe": 0.3}]
    ).to_csv(source, index=False)
    out_dir = tmp_path / "pit"
    assert (
        main(
            [
                "tushare",
                "build-a-share-pit-fundamentals",
                "--source-file",
                str(source),
                "--out-dir",
                str(out_dir),
                "--field-map",
                "roe=return_on_equity",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset"] == "pit_fundamentals"

    assert (
        main(
            [
                "tushare",
                "validate-a-share-pit-fundamentals",
                "--asset-dir",
                str(out_dir),
            ]
        )
        == 0
    )
    validation = json.loads(capsys.readouterr().out)
    assert validation["status"] == "passed"
