from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    PitProvenanceError,
    build_asset_integrity,
    seal_manifest,
)
from market_data_platform.providers.tushare_common import write_manifest


def _load_script():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "operations"
        / "archive_tushare_fundamentals_vintage.py"
    )
    spec = importlib.util.spec_from_file_location("archive_tushare_fundamentals_vintage", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


archive = _load_script()


def _child(root: Path, relative: str) -> Path:
    child = root / relative
    data = child / "data" / "part.bin"
    data.parent.mkdir(parents=True)
    data.write_bytes(relative.encode("utf-8"))
    write_manifest(
        child / "manifest.yml",
        {
            "status": "completed",
            "immutable_snapshot": True,
            "observed_vintage_dates": ["20260802"],
            "integrity": build_asset_integrity(child, [data]),
        },
    )
    seal_manifest(child / "manifest.yml")
    return child


def test_archive_requires_explicit_api_url_and_keeps_daily_core_pit_defaults() -> None:
    args = archive._prepare_args(
        archive.build_parser().parse_args(
            ["--artifacts-root", "/tmp/data", "--snapshot-date", "20260802"]
        )
    )

    assert args.api_url is None
    assert args.token_env == "TUSHARE_TOKEN_2"
    assert args.datasets == ("income", "balancesheet", "cashflow", "fina_indicator")
    assert args.start_date == "20150101"
    assert args.end_date == "20260802"
    assert args.observation_frequency == "daily"
    assert "roe=roe" in args.field_mappings
    assert "n_cashflow_act=n_cashflow_act" in args.field_mappings
    assert "grossprofit_margin=grossprofit_margin" in args.field_mappings
    assert "netprofit_yoy=netprofit_yoy" in args.field_mappings
    assert "or_yoy=or_yoy" in args.field_mappings
    assert "q_sales_yoy=q_sales_yoy" in args.field_mappings


def test_archive_accepts_explicit_public_tushare_endpoint() -> None:
    archive._validate_api_url("https://api.tushare.pro")


def test_seal_verification_detects_child_content_tampering(tmp_path: Path) -> None:
    root = archive.snapshot_root(tmp_path, "20260802")
    raw = _child(root, "raw/income")
    normalized = _child(root, "normalized/income")
    pit = _child(root, "pit")
    args = argparse.Namespace(
        snapshot_date="20260802",
        start_date="20150101",
        end_date="20260802",
        datasets=("income",),
        api_url="https://api.tushare.pro",
        token_env="TUSHARE_TOKEN_2",
        observation_frequency="daily",
    )

    archive._seal_snapshot(args, root, [raw], [normalized], pit)
    manifest = archive.verify_sealed_snapshot(root)
    assert manifest["publication"]["latest_switched"] is False
    assert manifest["revision_safety"]["observation_frequency"] == "daily"

    (raw / "data" / "part.bin").write_bytes(b"tampered")
    with pytest.raises(PitProvenanceError, match="content integrity"):
        archive.verify_sealed_snapshot(root)
