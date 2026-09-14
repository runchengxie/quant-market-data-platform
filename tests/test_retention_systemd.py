from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_retention_runner_is_owned_by_data_platform_repository() -> None:
    runner = REPO_ROOT / "scripts" / "operations" / "market_data_platform_retention.sh"

    assert runner.is_file()
    text = runner.read_text(encoding="utf-8")
    assert "research-workspace" not in text
    assert "MARKET_DATA_ROOT" in text
    assert "MARKETDATA_CLI" in text


def test_retention_units_are_renderable_and_use_canonical_paths() -> None:
    service_path = REPO_ROOT / "scripts" / "systemd" / "market-data-platform-retention.service"
    timer_path = REPO_ROOT / "scripts" / "systemd" / "market-data-platform-retention.timer"
    service = service_path.read_text(encoding="utf-8")
    timer = timer_path.read_text(encoding="utf-8")

    assert "@MDP_DIR@" in service
    assert "@DATA_PLATFORM_ROOT@" in service
    assert "@MARKETDATA_CLI@" in service
    assert "research-workspace" not in service
    assert "Persistent=true" in timer


def test_systemd_renderer_can_render_retention_units(tmp_path: Path) -> None:
    from scripts.operations.render_tushare_minute_campaign_units import render_units

    output_dir = tmp_path / "systemd"
    args = argparse.Namespace(
        template_root=REPO_ROOT / "scripts" / "systemd",
        template_glob="market-data-platform-retention.*",
        home=tmp_path / "home",
        mdp_dir=REPO_ROOT,
        data_platform_root=tmp_path / "data" / "quant" / "market-data-platform",
        campaign_manifest=tmp_path / "unused-manifest.json",
        logs_dir=tmp_path / "logs",
        marketdata_cli=tmp_path / "bin" / "marketdata",
        output_dir=output_dir,
        dry_run=False,
    )

    rendered = render_units(args)

    assert {path.name for path in rendered} == {
        "market-data-platform-retention.service",
        "market-data-platform-retention.timer",
    }
    rendered_service = (output_dir / "market-data-platform-retention.service").read_text(
        encoding="utf-8"
    )
    assert "@" not in rendered_service
    assert str(args.marketdata_cli) in rendered_service


def test_retention_runner_scans_real_versioned_directories(tmp_path: Path) -> None:
    root = tmp_path / "market-data-platform"
    daily = root / "assets" / "tushare" / "a_share" / "daily"
    inputs = root / "assets" / "tushare" / "a_share" / "daily_clean_inputs"
    inventory = root / "metadata" / "lifecycle" / "inventory.json"
    cli = tmp_path / "marketdata"
    for path in (
        daily / "a_share_all_20150101_20260908_daily_clean",
        daily / "a_share_all_20150101_20260907_daily_clean",
        daily / "zz-unrelated-directory",
        inputs / "a_share_all_20150101_20260908",
        inputs / "zz-unrelated-directory",
    ):
        path.mkdir(parents=True)
        (path / "part.txt").write_text("fixture\n", encoding="utf-8")
    inventory.parent.mkdir(parents=True)
    inventory.write_text("{}\n", encoding="utf-8")
    cli.write_text(
        "#!/bin/sh\n"
        'out=""; latest=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        "    --out) out=$2; shift 2 ;;\n"
        "    --latest-link) latest=$2; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        ': > "$out"\n'
        'ln -sfn "$(basename "$out")" "$latest"\n',
        encoding="utf-8",
    )
    cli.chmod(0o755)

    result = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "operations" / "market_data_platform_retention.sh"),
            "dry-run",
        ],
        env={
            **os.environ,
            "MARKET_DATA_ROOT": str(root),
            "MARKETDATA_CLI": str(cli),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "would-delete" not in result.stdout
    assert (root / "metadata" / "retention" / "governance-latest.tsv").is_symlink()
