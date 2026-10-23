from __future__ import annotations

import argparse
import importlib.util
import json
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
    archive.begin_attempt(root, archive.resolved_retry_parameters(args))
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
            api_url=args.api_url,
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


def test_archive_failure_receipt_preserves_phase_key_and_completed_raw(tmp_path, monkeypatch):
    from quant_market_data_platform.providers.tushare_a_share_fundamentals_support import (
        FieldValidationError,
    )

    args = _archive_args(tmp_path)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    key = "('603093.SH', '20260331', '1', '20260421', '2026-10-20T18:45:10+00:00')"

    def raw(_args, _root, dataset):
        return _child(root, f"raw/{dataset}")

    def normalize(*_):
        state = json.loads((root / "archive_state.json").read_text())
        assert state["status"] == "running"
        assert state["phase"] == "normalized"
        raise FieldValidationError(
            f"Normalized fundamentals contain an ambiguous same-day revision for {key}."
        )

    monkeypatch.setattr(archive, "_archive_raw_dataset", raw)
    monkeypatch.setattr(archive, "_archive_normalized_dataset", normalize)
    with pytest.raises(FieldValidationError):
        archive.archive_vintage(args)
    state = json.loads((root / "archive_state.json").read_text())
    assert state["status"] == "failed"
    assert state["phase"] == "normalized"
    assert state["dataset"] == "income"
    assert state["error"] == {"type": "FieldValidationError", "conflicting_key": key}
    assert len(state["completed_children"]) == len(args.datasets)
    assert all(child["manifest_sha256"] for child in state["completed_children"])
    assert not (root / "manifest.yml").exists()
    assert not list(root.glob(".archive_state.*"))


def test_archive_failure_receipt_excludes_arbitrary_exception_text(tmp_path, monkeypatch):
    args = _archive_args(tmp_path)

    def raw(*_, **__):
        raise RuntimeError("request token=private-example")

    monkeypatch.setattr(archive, "_archive_raw_dataset", raw)
    with pytest.raises(RuntimeError, match="private-example"):
        archive.archive_vintage(args)
    text = (archive.snapshot_root(tmp_path, args.snapshot_date) / "archive_state.json").read_text()
    state = json.loads(text)
    assert state["error"] == {"type": "RuntimeError"}
    assert state["phase"] == "raw"
    assert "private-example" not in text


def test_archive_sealed_success_does_not_rewrite_state(tmp_path, monkeypatch):
    args = _archive_args(tmp_path)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    root.mkdir(parents=True)
    (root / "SEALED.json").write_text("fixture")
    state = root / "archive_state.json"
    state.write_text("original diagnostic receipt")
    monkeypatch.setattr(archive, "verify_sealed_snapshot", lambda _: {"status": "completed"})
    assert archive.archive_vintage(args) == {"status": "completed"}
    assert state.read_text() == "original diagnostic receipt"


def test_archive_state_replace_failure_preserves_previous_receipt(tmp_path, monkeypatch):
    state = tmp_path / "archive_state.json"
    state.write_text("previous complete JSON")

    def fail_replace(*_):
        raise OSError("fixture replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="fixture replace failure"):
        archive._write_archive_state(tmp_path, {"status": "running"})
    assert state.read_text() == "previous complete JSON"
    assert not list(tmp_path.glob(".archive_state.*"))


def test_archive_retry_replaces_failed_state_with_completed_receipt(tmp_path, monkeypatch):
    args = _archive_args(tmp_path)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    archive.begin_attempt(root, archive.resolved_retry_parameters(args))
    (root / "archive_state.json").write_text('{"status":"failed","error":{"type":"Old"}}')
    monkeypatch.setattr(
        archive, "_archive_raw_dataset", lambda _a, _r, name: _child(root, f"raw/{name}")
    )
    monkeypatch.setattr(
        archive,
        "_archive_normalized_dataset",
        lambda _a, _r, name, _raw: _child(root, f"normalized/{name}"),
    )
    monkeypatch.setattr(archive, "_archive_pit", lambda *_: _child(root, "pit"))
    assert archive.archive_vintage(args)["status"] == "completed"
    state = json.loads((root / "archive_state.json").read_text())
    assert state["status"] == state["phase"] == "completed"
    assert state["dataset"] is None
    assert "error" not in state
    assert len(state["completed_children"]) == 2 * len(args.datasets) + 1
    archive.verify_sealed_snapshot(root)


def test_legacy_unsealed_retry_is_read_only_and_unproven(tmp_path, monkeypatch):
    args = _archive_args(tmp_path)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    root.mkdir(parents=True)
    receipt = root / "archive_state.json"
    receipt.write_text('{"status":"failed","error":{"type":"Old"}}')
    original = receipt.read_bytes()
    monkeypatch.setattr(archive, "_archive_raw_dataset", lambda *_: pytest.fail("provider blocked"))
    with pytest.raises(RuntimeError, match="identity.*unproven"):
        archive.archive_vintage(args)
    assert receipt.read_bytes() == original
    assert sorted(p.name for p in root.iterdir()) == ["archive_state.json"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_url", "https://other.example"),
        ("field_mappings", ("roe=changed",)),
        ("available_delay_days", 2),
        ("max_observation_age_days", 4),
    ],
)
def test_retry_rejects_changed_semantics_before_writes(tmp_path, monkeypatch, field, value):
    args = _archive_args(tmp_path)

    def fail(*_):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(archive, "_archive_raw_dataset", fail)
    with pytest.raises(RuntimeError, match="fixture failure"):
        archive.archive_vintage(args)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    setattr(args, field, value)
    with pytest.raises(RuntimeError, match="identity.*changed"):
        archive.archive_vintage(args)
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_retry_preserves_each_attempt_and_sanitized_parameters(tmp_path, monkeypatch):
    args = _archive_args(tmp_path)

    def fail(*_):
        raise RuntimeError("token=secret-example")

    monkeypatch.setattr(archive, "_archive_raw_dataset", fail)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            archive.archive_vintage(args)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    attempts = sorted((root / "attempts").iterdir())
    assert len(attempts) == 2
    for attempt in attempts:
        state = json.loads((attempt / "archive_state.json").read_text())
        assert state["status"] == "failed"
        identity = json.loads((attempt / "parameters.json").read_text())
        assert identity["source_revision"]
        assert identity["semantic"]["max_observation_age_days"] == 3
        assert identity["execution"]["batch_rows"] == 65536
    evidence = "\n".join(p.read_text() for p in root.rglob("*.json"))
    assert "secret-example" not in evidence
    assert args.api_url not in evidence


def test_completed_raw_endpoint_mismatch_blocks_before_provider(tmp_path, monkeypatch):
    from quant_market_data_platform.providers import tushare_a_share_fundamentals_part02 as raw_api
    from quant_market_data_platform.providers.tushare_a_share_fundamentals import (
        RawFundamentalsDownloadOptions,
    )
    from quant_market_data_platform.providers.tushare_a_share_fundamentals_support import (
        asset_manifest_payload,
    )

    raw = _child(tmp_path, "raw")
    manifest = asset_manifest_payload(raw)
    manifest.update(
        dataset="income",
        query={
            "start_date": "20200101",
            "end_date": "20200102",
            "requested_report_types": ["provider_default"],
        },
        entitlement_mode="vip_batch",
        source_run_id="fixture",
        api_url="https://api.tushare.pro",
    )
    write_manifest(raw / "manifest.yml", manifest)
    seal_manifest(raw / "manifest.yml")
    options = RawFundamentalsDownloadOptions(
        dataset="income",
        out_dir=raw,
        start_date="20200101",
        end_date="20200102",
        entitlement_mode="vip_batch",
        run_id="fixture",
        api_url="https://other.example",
    )
    with pytest.raises(Exception, match="different identity"):
        raw_api._completed_immutable_snapshot(options)
    from dataclasses import replace

    options = replace(options, api_url="HTTPS://API.TUSHARE.PRO:443/")
    reused = raw_api._completed_immutable_snapshot(options)
    assert reused is not None
    assert reused["status"] == "completed"


def test_derived_reuse_requires_explicit_matching_semantics(tmp_path):
    derived = _child(tmp_path, "normalized")
    with pytest.raises(RuntimeError, match="identity.*unproven"):
        archive._reuse_derived_snapshot(
            derived, expected_source_hashes=set(), expected_identity={"delay": 1}
        )


@pytest.mark.parametrize("change", ["semantics", "source", "payload"])
def test_derived_retry_rejects_changed_inputs(tmp_path, change):
    from quant_market_data_platform.providers.tushare_a_share_fundamentals_support import (
        asset_manifest_payload,
    )

    derived = _child(tmp_path, "normalized")
    manifest = asset_manifest_payload(derived)
    manifest["source_integrity"] = {"manifest_sha256": "source-a"}
    write_manifest(derived / "manifest.yml", manifest)
    seal_manifest(derived / "manifest.yml")
    archive.record_derived_identity(
        derived, {"mapping": ["roe=roe"], "delay": 1, "age": 3, "revision": "a"}
    )
    identity = {"mapping": ["roe=roe"], "delay": 1, "age": 3, "revision": "a"}
    assert archive._reuse_derived_snapshot(
        derived, expected_source_hashes={"source-a"}, expected_identity=identity
    )
    sources = {"source-a"}
    if change == "semantics":
        identity["delay"] = 2
    elif change == "source":
        sources = {"source-b"}
    else:
        (derived / "data/part.bin").write_bytes(b"corrupt")
    with pytest.raises((RuntimeError, PitProvenanceError)):
        archive._reuse_derived_snapshot(
            derived, expected_source_hashes=sources, expected_identity=identity
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:secret@example.com",
        "https://example.com?token=secret",
        "https://example.com#secret",
    ],
)
def test_archive_rejects_credential_bearing_endpoint_without_echo(endpoint):
    with pytest.raises(ValueError) as error:
        archive._validate_api_url(endpoint)
    assert "secret" not in str(error.value)


def test_retry_rejects_changed_code_revision(tmp_path, monkeypatch):
    args = _archive_args(tmp_path)
    parameters = archive.resolved_retry_parameters(args)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    archive.begin_attempt(root, parameters)
    parameters["semantic"]["source_revision"] = "different"
    with pytest.raises(RuntimeError, match="identity.*changed"):
        archive.begin_attempt(root, parameters)


def test_invalid_endpoint_port_does_not_echo_input():
    with pytest.raises(ValueError) as error:
        archive._validate_api_url("https://example.com:secret-port")
    assert "secret-port" not in str(error.value)


@pytest.mark.parametrize("kind", ["raw", "derived"])
def test_sealed_child_with_lost_completion_metadata_cannot_be_rebuilt(tmp_path, kind):
    child = _child(tmp_path, kind)
    (child / "manifest.yml").unlink()
    with pytest.raises(RuntimeError, match="[Ss]ealed|seal"):
        if kind == "derived":
            archive._reuse_derived_snapshot(
                child, expected_source_hashes=set(), expected_identity={}
            )
        else:
            from quant_market_data_platform.providers import (
                tushare_a_share_fundamentals_part02 as raw_api,
            )
            from quant_market_data_platform.providers.tushare_a_share_fundamentals import (
                RawFundamentalsDownloadOptions,
            )

            raw_api._completed_immutable_snapshot(
                RawFundamentalsDownloadOptions(
                    dataset="income",
                    out_dir=child,
                    entitlement_mode="vip_batch",
                    start_date="20200101",
                    end_date="20200102",
                )
            )


def test_retry_mapping_order_preserves_effective_duplicate_source_semantics(tmp_path):
    args = _archive_args(tmp_path)
    args.field_mappings = ("roe=a", "roe=b")
    parameters = archive.resolved_retry_parameters(args)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    archive.begin_attempt(root, parameters)
    args.field_mappings = ("roe=b", "roe=a")
    with pytest.raises(RuntimeError, match="identity.*changed"):
        archive.begin_attempt(root, archive.resolved_retry_parameters(args))


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://exa mple.com",
        "https://example.com/a b",
        "https://bad_host.example",
        "https://-bad.example",
        "https://example.com\n/token",
    ],
)
def test_endpoint_rejects_whitespace_and_invalid_hostname_without_echo(endpoint):
    with pytest.raises(ValueError) as error:
        archive._validate_api_url(endpoint)
    assert endpoint not in str(error.value)


@pytest.mark.parametrize("dependency", ["pandas", "pyarrow", "tushare"])
def test_retry_identity_rejects_actual_runtime_dependency_change(tmp_path, monkeypatch, dependency):
    from importlib import metadata

    args = _archive_args(tmp_path)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    archive.begin_attempt(root, archive.resolved_retry_parameters(args))
    original = metadata.version
    monkeypatch.setattr(
        metadata,
        "version",
        lambda name: "changed-version" if name == dependency else original(name),
    )
    with pytest.raises(RuntimeError, match="identity.*changed"):
        archive.begin_attempt(root, archive.resolved_retry_parameters(args))


def test_retry_identity_requires_runtime_versions_for_required_dependencies(tmp_path, monkeypatch):
    from importlib import metadata

    original = metadata.version

    def version(name):
        if name == "pandas":
            raise metadata.PackageNotFoundError(name)
        return original(name)

    monkeypatch.setattr(metadata, "version", version)
    with pytest.raises(RuntimeError, match="runtime.*unproven"):
        archive.resolved_retry_parameters(_archive_args(tmp_path))


def test_retry_identity_rejects_python_runtime_change(tmp_path, monkeypatch):
    from quant_market_data_platform.providers import tushare_fundamentals_retry as retry

    args = _archive_args(tmp_path)
    root = archive.snapshot_root(tmp_path, args.snapshot_date)
    archive.begin_attempt(root, archive.resolved_retry_parameters(args))
    monkeypatch.setattr(retry.platform, "python_version", lambda: "different")
    with pytest.raises(RuntimeError, match="identity.*changed"):
        archive.begin_attempt(root, archive.resolved_retry_parameters(args))
