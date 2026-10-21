from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

from quant_market_data_platform.providers.tushare_a_share_fundamentals_support import (
    PitProvenanceError,
    build_asset_integrity,
    seal_manifest,
)
from quant_market_data_platform.providers.tushare_common import write_manifest


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
    assert "rd_exp=rd_exp" in args.field_mappings
    assert "n_cashflow_act=n_cashflow_act" in args.field_mappings
    assert "c_paid_to_for_empl=employee_cash_paid" in args.field_mappings
    assert "payroll_payable=employee_compensation_payable" in args.field_mappings
    assert "st_cash_out_act=operating_cash_outflows" in args.field_mappings
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


def _archive_args(tmp_path):
    return archive._prepare_args(
        archive.build_parser().parse_args(
            [
                "--artifacts-root",
                str(tmp_path),
                "--snapshot-date",
                "20261021",
                "--api-url",
                "https://api.tushare.pro",
            ]
        )
    )


def test_normalization_conflict_preserves_all_selected_raw_before_failure(tmp_path, monkeypatch):
    from quant_market_data_platform.providers.tushare_a_share_fundamentals_support import (
        FieldValidationError,
    )

    args = _archive_args(tmp_path)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    events = []

    def download(options):
        assert options.start_date == args.start_date
        assert options.end_date == args.end_date
        assert options.request_interval_seconds == args.request_interval_seconds
        assert options.retry_attempts == args.retry_attempts
        assert options.page_size == args.page_size
        assert options.max_pages == args.max_pages
        assert options.run_id == f"{options.dataset}-{args.snapshot_date}"
        events.append(("download", options.dataset))
        _child(root, f"raw/{options.dataset}")
        return {"status": "completed"}

    def normalize(**kwargs):
        events.append(("normalize", kwargs["dataset"]))
        raise FieldValidationError("ambiguous same-day revision")

    monkeypatch.setattr(archive, "download_raw_fundamentals", download)
    monkeypatch.setattr(archive, "build_normalized_fundamentals", normalize)
    monkeypatch.setattr(archive, "_archive_pit", lambda *_: pytest.fail("PIT must not run"))
    monkeypatch.setattr(archive, "_seal_snapshot", lambda *_: pytest.fail("must not seal"))
    with pytest.raises(FieldValidationError, match="ambiguous same-day revision"):
        archive.archive_vintage(args)
    assert events == [("download", name) for name in args.datasets] + [("normalize", "income")]
    for name in args.datasets:
        assert (root / "raw" / name / "manifest.yml").is_file()
    assert not (root / "SEALED.json").exists()
    assert not (root / "pit").exists()


@pytest.mark.parametrize("raises", [False, True])
def test_raw_failure_prevents_normalization_and_seal(tmp_path, monkeypatch, raises):
    args = _archive_args(tmp_path)
    calls = []

    def download(options):
        calls.append(options.dataset)
        if raises:
            raise RuntimeError("provider unavailable")
        return {"status": "partial"}

    monkeypatch.setattr(archive, "download_raw_fundamentals", download)
    monkeypatch.setattr(
        archive, "build_normalized_fundamentals", lambda **_: pytest.fail("must not normalize")
    )
    monkeypatch.setattr(archive, "_archive_pit", lambda *_: pytest.fail("PIT must not run"))
    monkeypatch.setattr(archive, "_seal_snapshot", lambda *_: pytest.fail("must not seal"))
    with pytest.raises(RuntimeError, match="provider unavailable|snapshot is incomplete"):
        archive.archive_vintage(args)
    assert calls == ["income"]
    assert not (archive.snapshot_root(tmp_path, args.snapshot_date) / "SEALED.json").exists()


def test_sealed_archive_reentry_verifies_without_provider_requests(tmp_path, monkeypatch):
    args = _archive_args(tmp_path)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    raw = [_child(root, f"raw/{name}") for name in args.datasets]
    normalized = [_child(root, f"normalized/{name}") for name in args.datasets]
    pit = _child(root, "pit")
    archive._seal_snapshot(args, root, raw, normalized, pit)
    monkeypatch.setattr(
        archive, "download_raw_fundamentals", lambda *_: pytest.fail("must not request")
    )
    assert archive.archive_vintage(args)["status"] == "completed"
    (raw[0] / "data" / "part.bin").write_bytes(b"tampered")
    with pytest.raises(PitProvenanceError, match="content integrity"):
        archive.archive_vintage(args)


def test_completed_raw_reused_without_provider_context_on_unsealed_retry(tmp_path, monkeypatch):
    from quant_market_data_platform.providers import tushare_a_share_fundamentals_part02 as raw_api
    from quant_market_data_platform.providers.tushare_a_share_fundamentals_support import (
        FieldValidationError,
        asset_manifest_payload,
    )

    args = _archive_args(tmp_path)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    for name in args.datasets:
        raw = _child(root, f"raw/{name}")
        manifest = asset_manifest_payload(raw)
        manifest.update(
            dataset=name,
            query={
                "start_date": args.start_date,
                "end_date": args.end_date,
                "requested_report_types": ["provider_default"],
            },
            entitlement_mode="vip_batch",
            source_run_id=f"{name}-{args.snapshot_date}",
        )
        write_manifest(raw / "manifest.yml", manifest)
        seal_manifest(raw / "manifest.yml")
    monkeypatch.setattr(raw_api, "_download_context", lambda *_: pytest.fail("must reuse raw"))

    def conflict(**_):
        raise FieldValidationError("ambiguous same-day revision")

    monkeypatch.setattr(archive, "build_normalized_fundamentals", conflict)
    with pytest.raises(FieldValidationError, match="ambiguous same-day revision"):
        archive.archive_vintage(args)
    assert not (root / "SEALED.json").exists()
