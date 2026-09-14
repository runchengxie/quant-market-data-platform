from __future__ import annotations

import json

from market_data_platform.cli import main
from market_data_platform.contract import build_current_contract, write_current_contract
from market_data_platform.paths import (
    candidate_asset_paths,
    current_contract_path,
    dataset_registry_path,
    resolve_artifacts_root,
)
from market_data_platform.published_assets import PublishedAssetContract
from market_data_platform.registry import (
    build_combined_dataset_registry_rows,
    build_dataset_registry_rows,
    render_combined_dataset_registry_csv,
    render_dataset_registry_csv,
)


def test_shared_paths_resolve_from_explicit_root(tmp_path):
    root = tmp_path / "market-data"

    assert resolve_artifacts_root(root) == root.resolve()
    assert (
        current_contract_path(root)
        == root.resolve() / "metadata" / "current_assets" / "a_share_current.json"
    )
    assert dataset_registry_path(root) == root.resolve() / "metadata" / "dataset_registry.csv"


def test_current_contract_path_preserves_legacy_hk_layout(tmp_path):
    root = tmp_path / "market-data"

    assert (
        current_contract_path(root, market="hk")
        == root.resolve() / "metadata" / "current_assets" / "hk_current.json"
    )

    assets = candidate_asset_paths(root)
    assert (
        assets["daily_clean"]
        == root.resolve()
        / "assets"
        / "tushare"
        / "a_share"
        / "daily"
        / "a_share_all_daily_clean_latest"
    )
    assert (
        assets["instruments"]
        == root.resolve()
        / "assets"
        / "tushare"
        / "a_share"
        / "instruments"
        / "a_share_all_instruments_latest.parquet"
    )


def test_a_share_paths_and_contract_use_market_specific_layout(tmp_path):
    root = tmp_path / "market-data"

    assert (
        current_contract_path(root, market="a_share")
        == root.resolve() / "metadata" / "current_assets" / "a_share_current.json"
    )

    assets = candidate_asset_paths(root, market="a_share")
    assert (
        assets["daily_clean"]
        == root.resolve()
        / "assets"
        / "tushare"
        / "a_share"
        / "daily"
        / "a_share_all_daily_clean_latest"
    )
    assert (
        assets["instruments"]
        == root.resolve()
        / "assets"
        / "tushare"
        / "a_share"
        / "instruments"
        / "a_share_all_instruments_latest.parquet"
    )
    assert assets["limit_status"].name == "a_share_limit_status_latest"

    contract = build_current_contract(root, market="a_share", target_date="20260522")
    assert contract["contract"]["name"] == "a_share_current"
    assert contract["contract"]["market"] == "a_share"
    assert (
        contract["contract"]["contract_path"]
        .replace("\\", "/")
        .endswith("metadata/current_assets/a_share_current.json")
    )
    assert "daily_clean" in contract["assets"]


def test_current_contract_includes_tushare_minute_operational_asset(tmp_path):
    root = tmp_path / "market-data"
    operational = root / "assets" / "derived" / "a_share" / "minute_1m_tushare_v1_20260903"
    operational.mkdir(parents=True)
    (operational / "_operational_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "a_share.minute_tushare_operational_version.v1",
                "status": "published_operational_version",
                "dataset": "a_share_minute_1m_tushare",
                "provider": "tushare",
                "output_dir": str(operational),
                "summary": {"date_min": "20220715", "date_max": "20260903"},
            }
        ),
        encoding="utf-8",
    )
    alias = root / "assets" / "derived" / "a_share" / "minute_1m_tushare"
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(operational.name, target_is_directory=True)

    assets = candidate_asset_paths(root, market="a_share", provider="tushare")
    assert assets["minute_1m_tushare"] == alias

    contract = build_current_contract(root, market="a_share", provider="tushare")
    minute = contract["assets"]["minute_1m_tushare"]
    assert minute["exists"] is True
    assert minute["resolved_path"] == str(operational.resolve())
    assert minute["manifest_path"] == str((operational / "_operational_receipt.json").resolve())
    assert minute["as_of"] == "20260903"
    assert minute["manifest"]["query_start_date"] == "20220715"
    assert minute["manifest"]["query_end_date"] == "20260903"

    contract_path = current_contract_path(root)
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    published = PublishedAssetContract.load_current(root)
    assert published.asset("minute_1m_tushare").manifest["schema_version"] == (
        "a_share.minute_tushare_operational_version.v1"
    )


def test_tushare_a_share_paths_and_registry_source_are_provider_specific(tmp_path):
    root = tmp_path / "market-data"
    assets = candidate_asset_paths(root, market="a_share", provider="tushare")
    assert (
        assets["daily"]
        == root.resolve() / "assets" / "tushare" / "a_share" / "daily" / "a_share_all_daily_latest"
    )
    assert assets["trade_cal"].name == "a_share_trade_cal_latest.parquet"
    assert "daily_basic" in assets

    snapshot = root / "assets" / "tushare" / "a_share" / "daily" / "a_share_all_20260522_daily"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.yml").write_text(
        "\n".join(
            [
                "dataset: daily",
                "provider: tushare",
                "status: completed",
                "query:",
                "  start_date: '20260521'",
                "  end_date: '20260522'",
                "totals:",
                "  rows: 10",
                "  symbols: 5",
            ]
        ),
        encoding="utf-8",
    )
    assets["daily"].parent.mkdir(parents=True, exist_ok=True)
    assets["daily"].symlink_to(snapshot.name)

    contract = build_current_contract(
        root,
        market="a_share",
        provider="tushare",
        target_date="20260522",
    )
    daily = next(
        row
        for row in build_dataset_registry_rows(contract)
        if row["dataset_name"] == "a_share_daily"
    )
    rows_by_name = {row["dataset_name"]: row for row in build_dataset_registry_rows(contract)}
    assert contract["contract"]["provider"] == "tushare"
    assert daily["source"] == "tushare"
    assert "a_share_instruments" not in rows_by_name
    assert "a_share_limit_status" not in rows_by_name


def test_build_current_contract_reads_daily_manifest(tmp_path):
    root = tmp_path / "market-data"
    snapshot = root / "assets" / "tushare" / "a_share" / "daily" / "a_share_all_20260409_daily"
    (snapshot / "data").mkdir(parents=True)
    (snapshot / "data" / "data.parquet").write_text("demo", encoding="utf-8")
    (snapshot / "manifest.yml").write_text(
        "\n".join(
            [
                "schema_version: daily.v1",
                "row_count: 10",
                "symbol_count: 2",
                "date_range:",
                "  start: '20250401'",
                "  end: '20260409'",
            ]
        ),
        encoding="utf-8",
    )
    alias = candidate_asset_paths(root, market="a_share", provider="tushare")["daily"]
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(snapshot.name)

    contract = build_current_contract(
        root, market="a_share", provider="tushare", target_date="20260409"
    )

    entry = contract["assets"]["daily"]
    assert entry["exists"] is True
    assert entry["as_of"] == "20260409"
    assert entry["manifest"]["dataset"] == "daily"
    assert entry["manifest"]["totals"]["rows"] == 10


def test_dataset_registry_is_derived_from_current_contract(tmp_path):
    root = tmp_path / "market-data"
    snapshot = root / "assets" / "tushare" / "a_share" / "daily" / "a_share_all_20260409_daily"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.yml").write_text(
        "\n".join(
            [
                "dataset: daily",
                "provider: tushare",
                "status: completed",
                "query:",
                "  start_date: '20240101'",
                "  end_date: '20260409'",
                "totals:",
                "  rows: 12",
                "  symbols_written: 3",
            ]
        ),
        encoding="utf-8",
    )
    alias = candidate_asset_paths(root, market="a_share", provider="tushare")["daily"]
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(snapshot.name)

    contract = build_current_contract(
        root, market="a_share", provider="tushare", target_date="20260409"
    )
    rows = build_dataset_registry_rows(contract)
    csv_text = render_dataset_registry_csv(contract)

    daily = next(row for row in rows if row["dataset_name"] == "a_share_daily")
    assert daily["version"] == "20260409"
    assert daily["records"] == "12"
    assert daily["symbols"] == "3"
    assert daily["date_range"] == "2024-01-01 to 2026-04-09"
    assert daily["path"].endswith("assets/tushare/a_share/daily/a_share_all_daily_latest")
    assert "a_share_all_20260409_daily" not in daily["path"]
    assert "a_share_current_contract" in csv_text


def test_current_contract_uses_query_date_as_as_of(tmp_path):
    root = tmp_path / "market-data"
    snapshot = root / "assets" / "tushare" / "a_share" / "daily" / "a_share_all_20260522_daily"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.yml").write_text(
        "\n".join(
            [
                "dataset: daily",
                "provider: tushare",
                "status: completed",
                "query:",
                "  start_date: '20240101'",
                "  end_date: '20260522'",
                "totals:",
                "  rows: 12",
                "  symbols_written: 3",
            ]
        ),
        encoding="utf-8",
    )
    alias = candidate_asset_paths(root, market="a_share", provider="tushare")["daily"]
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(snapshot.name)

    contract = build_current_contract(
        root, market="a_share", provider="tushare", target_date="20260522"
    )
    rows = build_dataset_registry_rows(contract)

    entry = contract["assets"]["daily"]
    daily_row = next(row for row in rows if row["dataset_name"] == "a_share_daily")
    assert entry["as_of"] == "20260522"
    assert daily_row["version"] == "20260522"
    assert daily_row["date_range"] == "2024-01-01 to 2026-05-22"


def test_current_contract_prefers_manifest_as_of_date_over_coverage_end(tmp_path):
    root = tmp_path / "market-data"
    snapshot = (
        root
        / "assets"
        / "tushare"
        / "a_share"
        / "industry_changes"
        / "a_share_all_industry_changes_sw2021_l3_20260603"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.yml").write_text(
        "\n".join(
            [
                "dataset: industry_changes",
                "provider: tushare",
                "status: completed",
                "as_of_date: '20260603'",
                "query:",
                "  start_date: '19840509'",
                "  end_date: '20260304'",
                "totals:",
                "  rows: 7763",
                "  symbols: 5834",
            ]
        ),
        encoding="utf-8",
    )
    alias = candidate_asset_paths(root, market="a_share", provider="tushare")["industry_changes"]
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(snapshot.name)

    contract = build_current_contract(
        root,
        market="a_share",
        provider="tushare",
        target_date="20260529",
    )
    entry = contract["assets"]["industry_changes"]

    assert entry["as_of"] == "20260603"
    assert entry["manifest"]["query_end_date"] == "20260304"


def test_a_share_dataset_registry_uses_contract_market(tmp_path):
    root = tmp_path / "market-data"
    snapshot = root / "assets" / "tushare" / "a_share" / "daily" / "a_share_all_20260522_daily"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.yml").write_text(
        "\n".join(
            [
                "dataset: daily",
                "provider: tushare",
                "status: completed",
                "query:",
                "  start_date: '20240101'",
                "  end_date: '20260522'",
                "totals:",
                "  rows: 12",
                "  symbols_written: 3",
            ]
        ),
        encoding="utf-8",
    )
    alias = candidate_asset_paths(root, market="a_share", provider="tushare")["daily"]
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(snapshot.name)

    contract = build_current_contract(
        root, market="a_share", provider="tushare", target_date="20260522"
    )
    rows = build_dataset_registry_rows(contract)
    csv_text = render_dataset_registry_csv(contract)

    daily = next(row for row in rows if row["dataset_name"] == "a_share_daily")
    assert daily["market"] == "a_share"
    assert daily["version"] == "20260522"
    assert daily["records"] == "12"
    assert "a_share_current_contract" in csv_text


def test_combined_dataset_registry_includes_a_share_contract(tmp_path):
    root = tmp_path / "market-data"
    a_share_contract = build_current_contract(root, market="a_share", target_date="20260522")

    rows = build_combined_dataset_registry_rows([a_share_contract])
    csv_text = render_combined_dataset_registry_csv([a_share_contract])

    dataset_names = {row["dataset_name"] for row in rows}
    assert "a_share_current_contract" in dataset_names
    assert "a_share_daily" not in dataset_names
    assert "# Dataset Registry for current A-share research data assets." in csv_text


def test_dataset_registry_omits_assets_explicitly_marked_missing(tmp_path):
    root = tmp_path / "market-data"
    contract = build_current_contract(
        root,
        market="a_share",
        provider="tushare",
        target_date="20260522",
    )
    contract["assets"]["instruments"]["exists"] = True
    contract["assets"]["instruments"]["alias_path"] = "   "
    contract["assets"]["instruments"]["resolved_path"] = ""

    rows = build_dataset_registry_rows(contract)
    contract_row = next(row for row in rows if row["dataset_name"] == "a_share_current_contract")
    dataset_names = {row["dataset_name"] for row in rows}

    assert dataset_names == {"a_share_current_contract"}
    assert contract_row["records"].startswith("0 available / ")
    assert contract_row["symbols"] == "0"
    assert "a_share_moneyflow_dc" not in dataset_names


def test_cli_registry_build_combines_existing_a_share_contract(tmp_path):
    root = tmp_path / "market-data"
    a_share_contract = build_current_contract(root, market="a_share", target_date="20260522")
    write_current_contract(current_contract_path(root, market="a_share"), a_share_contract)

    assert main(["registry", "build", "--artifacts-root", str(root)]) == 0

    registry_text = dataset_registry_path(root).read_text(encoding="utf-8")
    assert "a_share_current_contract" in registry_text


def test_cli_contract_inspect_reports_missing_a_share_tushare_assets(tmp_path, capsys):
    root = tmp_path / "market-data"
    assets = candidate_asset_paths(root, market="a_share", provider="tushare")
    snapshot = root / "assets" / "tushare" / "a_share" / "daily" / "a_share_all_20260522_daily"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.yml").write_text(
        "\n".join(
            [
                "dataset: daily",
                "provider: tushare",
                "status: completed",
                "query:",
                "  start_date: '20260521'",
                "  end_date: '20260522'",
                "totals:",
                "  rows: 10",
                "  symbols: 5",
            ]
        ),
        encoding="utf-8",
    )
    assets["daily"].parent.mkdir(parents=True, exist_ok=True)
    assets["daily"].symlink_to(snapshot.name)
    contract = build_current_contract(
        root,
        market="a_share",
        provider="tushare",
        target_date="20260522",
    )
    write_current_contract(current_contract_path(root, market="a_share"), contract)

    result = main(
        [
            "contract",
            "inspect",
            "--artifacts-root",
            str(root),
            "--market",
            "a_share",
            "--provider",
            "tushare",
            "--target-date",
            "20260522",
            "--asset",
            "daily",
            "--asset",
            "adj_factor",
            "--format",
            "json",
            "--fail-on-severity",
            "error",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["summary"]["missing_assets"] == 1
    assert payload["summary"]["stale_assets"] == 0
    assert payload["quality_verdict"]["overall_severity"] == "warning"
    assert payload["quality_verdict"]["gate_status"] == "pass"
    assert payload["assets"]["daily"]["exists"] is True
    assert payload["assets"]["adj_factor"]["exists"] is False


def test_cli_contract_inspect_reports_manifest_coverage_start_warning(tmp_path, capsys):
    root = tmp_path / "market-data"
    assets = candidate_asset_paths(root, market="a_share", provider="tushare")
    snapshot = root / "assets" / "tushare" / "a_share" / "daily" / "a_share_all_20260522_daily"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.yml").write_text(
        "\n".join(
            [
                "dataset: daily",
                "provider: tushare",
                "status: completed",
                "query:",
                "  start_date: '20240102'",
                "  end_date: '20260522'",
                "totals:",
                "  rows: 10",
                "  symbols: 5",
            ]
        ),
        encoding="utf-8",
    )
    assets["daily"].parent.mkdir(parents=True, exist_ok=True)
    assets["daily"].symlink_to(snapshot.name)
    contract = build_current_contract(
        root,
        market="a_share",
        provider="tushare",
        target_date="20260522",
    )
    write_current_contract(current_contract_path(root, market="a_share"), contract)

    result = main(
        [
            "contract",
            "inspect",
            "--artifacts-root",
            str(root),
            "--market",
            "a_share",
            "--provider",
            "tushare",
            "--asset",
            "daily",
            "--require-start-date",
            "20200101",
            "--format",
            "json",
            "--fail-on-severity",
            "warning",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 2
    assert payload["summary"]["effective_start_date"] == "20240102"
    assert payload["summary"]["effective_end_date"] == "20260522"
    assert payload["assets"]["daily"]["manifest_totals"]["rows"] == 10
    assert payload["quality_checks"] == [
        {
            "check": "asset_manifest_starts_after_required_date",
            "field": "daily",
            "asset_key": "daily",
            "severity": "warning",
            "actual_start_date": "20240102",
            "required_start_date": "20200101",
        }
    ]
