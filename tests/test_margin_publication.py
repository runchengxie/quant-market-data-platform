"""Offline native financing version publication and pointer CAS."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from quant_market_data_platform.margin_publication import (
    MarginSwitchOptions,
    prepare_margin_version,
    switch_margin_version,
)


def _switch(**kwargs):
    return switch_margin_version(MarginSwitchOptions(**kwargs))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "candidate"
    part = root / "data/trade_date=20260930/part.parquet"
    part.parent.mkdir(parents=True)
    pd.DataFrame(
        {"trade_date": ["20260930"], "exchange_id": ["SSE"], "rzye": [1], "rqye": [2]}
    ).to_parquet(part, index=False)
    frame = pd.read_parquet(part)
    for column in ("rzmre", "rzche", "rqmcl", "rzrqye", "rqyl"):
        frame[column] = 0
    frame.to_parquet(part, index=False)
    (root / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "tushare.margin.v1",
                "dataset": "margin",
                "provider": "tushare",
                "market": "a_share",
                "status": "completed",
                "query": {"start_date": "20260930", "end_date": "20260930"},
                "totals": {"rows": 1, "files": 1},
                "lineage": {"observed_at": None},
            }
        )
    )
    lineage = tmp_path / "lineage.json"
    lineage.write_text(
        json.dumps(
            {
                "schema_version": "tushare.margin.sources.v1",
                "historical_pit_certified": False,
                "inputs": [{"path": str(part), "sha256": sha(part), "observed_at": None}],
            }
        )
    )
    return root, lineage


def prepare(candidate, tmp_path):
    root, lineage = candidate
    return prepare_margin_version(candidate=root, catalog=tmp_path / "catalog", lineage=lineage)


def test_immutable_prepare_and_dryrun_adoption(candidate, tmp_path):
    receipt = prepare(candidate, tmp_path)
    target = Path(receipt["version"])
    assert receipt["status"] == "prepared"
    assert not receipt["historical_pit_certified"]
    assert (target / "manifest.yml").is_file()
    alias = tmp_path / "catalog/current/margin"
    result = _switch(
        catalog=tmp_path / "catalog",
        dataset="margin",
        target=target,
        target_sha256=receipt["receipt_sha256"],
        expected=None,
    )
    assert result["dry_run"] and not alias.exists()
    _switch(
        catalog=tmp_path / "catalog",
        dataset="margin",
        target=target,
        target_sha256=receipt["receipt_sha256"],
        expected=None,
        apply=True,
    )
    assert alias.resolve() == target


def test_rejects_reuse_and_source_mutation(candidate, tmp_path):
    prepare(candidate, tmp_path)
    with pytest.raises(ValueError, match="exists"):
        prepare(candidate, tmp_path)
    root, _ = candidate
    part = next(root.rglob("*.parquet"))
    part.write_bytes(b"changed")
    with pytest.raises(ValueError):
        prepare(candidate, tmp_path)


def test_adoption_cas_and_tamper(candidate, tmp_path):
    r = prepare(candidate, tmp_path)
    target = Path(r["version"])
    alias = tmp_path / "catalog/current/margin"
    with pytest.raises(ValueError, match="CAS"):
        _switch(
            catalog=tmp_path / "catalog",
            dataset="margin",
            target=target,
            target_sha256=r["receipt_sha256"],
            expected="wrong",
            apply=True,
        )
    part = next(target.rglob("*.parquet"))
    part.write_bytes(b"changed")
    with pytest.raises(ValueError, match="inventory"):
        _switch(
            catalog=tmp_path / "catalog",
            dataset="margin",
            target=target,
            target_sha256=r["receipt_sha256"],
            expected=None,
            apply=True,
        )
    assert not alias.exists()


def test_rejects_physical_alias(candidate, tmp_path):
    r = prepare(candidate, tmp_path)
    alias = tmp_path / "catalog/current/margin"
    alias.mkdir(parents=True)
    (alias / "old").write_text("keep")
    with pytest.raises(ValueError, match="physical"):
        _switch(
            catalog=tmp_path / "catalog",
            dataset="margin",
            target=Path(r["version"]),
            target_sha256=r["receipt_sha256"],
            expected=None,
            apply=True,
        )
    assert (alias / "old").read_text() == "keep"


def test_duplicate_keys_rejected(candidate, tmp_path):
    root, _ = candidate
    part = next(root.rglob("*.parquet"))
    f = pd.read_parquet(part)
    pd.concat([f, f]).to_parquet(part, index=False)
    lineage = candidate[1]
    payload = json.loads(lineage.read_text())
    payload["inputs"][0]["sha256"] = sha(part)
    lineage.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicate"):
        prepare(candidate, tmp_path)


def test_rollback_and_contract(candidate, tmp_path):
    from quant_market_data_platform.margin_publication import export_margin_contract
    from quant_market_data_platform.published_assets import PublishedAssetContract

    first = prepare(candidate, tmp_path)
    cat = tmp_path / "catalog"
    first_path = Path(first["version"])
    _switch(
        catalog=cat,
        dataset="margin",
        target=first_path,
        target_sha256=first["receipt_sha256"],
        expected=None,
        apply=True,
    )
    root, lineage = candidate
    payload = json.loads(lineage.read_text())
    payload["note"] = "separate immutable version"
    lineage.write_text(json.dumps(payload))
    second = prepare(candidate, tmp_path)
    _switch(
        catalog=cat,
        dataset="margin",
        target=Path(second["version"]),
        target_sha256=second["receipt_sha256"],
        expected=str(first_path),
        expected_current_sha256=first["receipt_sha256"],
        apply=True,
    )
    _switch(
        catalog=cat,
        dataset="margin",
        target=first_path,
        target_sha256=first["receipt_sha256"],
        expected=second["version"],
        expected_current_sha256=second["receipt_sha256"],
        operation="rollback",
        apply=True,
    )
    output = cat / "explicit-contract.json"
    output.write_text(json.dumps(export_margin_contract(catalog=cat)))
    asset = PublishedAssetContract.load(output).asset("margin")
    assert asset.resolved_path == first_path
    assert Path(second["version"]).is_dir()


def test_receipt_failure_restores_only_owned_pointer(candidate, tmp_path, monkeypatch):
    import quant_market_data_platform.margin_transaction as module

    first = prepare(candidate, tmp_path)

    def fail(path, value):
        raise OSError("receipt write failed")

    monkeypatch.setattr(module, "write_json_manifest", fail)
    with pytest.raises(OSError, match="receipt write"):
        _switch(
            catalog=tmp_path / "catalog",
            dataset="margin",
            target=Path(first["version"]),
            target_sha256=first["receipt_sha256"],
            expected=None,
            apply=True,
        )
    assert not (tmp_path / "catalog/current/margin").is_symlink()


def test_catalog_contention(candidate, tmp_path):
    from quant_market_data_platform.margin_publication import catalog_lock

    prepare(candidate, tmp_path)
    with catalog_lock(tmp_path / "catalog"), pytest.raises(BlockingIOError):
        prepare(candidate, tmp_path)


def test_artifacts_cannot_live_in_checkout(candidate, tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: elsewhere")
    with pytest.raises(ValueError, match="checkout"):
        prepare_margin_version(
            candidate=candidate[0], catalog=checkout / "catalog", lineage=candidate[1]
        )


def test_catalog_symlink_parent_rejected(candidate, tmp_path):
    r = prepare(candidate, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "catalog/current").symlink_to(elsewhere)
    with pytest.raises(ValueError, match="physical"):
        _switch(
            catalog=tmp_path / "catalog",
            dataset="margin",
            target=Path(r["version"]),
            target_sha256=r["receipt_sha256"],
            expected=None,
            apply=True,
        )
    assert list(elsewhere.iterdir()) == []


def test_lineage_must_cover_candidate_partitions(candidate, tmp_path):
    unrelated = tmp_path / "unrelated"
    unrelated.write_text("not a parquet source")
    lineage = candidate[1]
    payload = json.loads(lineage.read_text())
    payload["inputs"][0].update(path=str(unrelated), sha256=sha(unrelated))
    lineage.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="cover"):
        prepare(candidate, tmp_path)


def test_cli_prepare_and_explicit_dryrun(candidate, tmp_path, capsys):
    from quant_market_data_platform.cli import main

    catalog = tmp_path / "catalog"
    assert (
        main(
            [
                "margin",
                "prepare",
                "--candidate",
                str(candidate[0]),
                "--lineage",
                str(candidate[1]),
                "--catalog",
                str(catalog),
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert (
        main(
            [
                "margin",
                "adopt",
                "--catalog",
                str(catalog),
                "--dataset",
                "margin",
                "--target",
                receipt["version"],
                "--target-sha256",
                receipt["receipt_sha256"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["dry_run"]
    assert not (catalog / "current/margin").exists()


def test_dryrun_and_export_never_initialize_catalog(tmp_path):
    from quant_market_data_platform.margin_publication import export_margin_contract

    catalog = tmp_path / "absent"
    with pytest.raises(ValueError, match="existing"):
        export_margin_contract(catalog=catalog)
    assert not catalog.exists()


def test_pending_intent_requires_explicit_recovery(candidate, tmp_path, monkeypatch):
    import quant_market_data_platform.margin_transaction as module
    from quant_market_data_platform.margin_publication import export_margin_contract

    first = prepare(candidate, tmp_path)
    catalog = tmp_path / "catalog"
    original = module.os.replace

    def killed(source, destination):
        original(source, destination)
        if Path(destination) == catalog / "current/margin":
            raise SystemExit("simulated process death")

    with monkeypatch.context() as patcher:
        patcher.setattr(module.os, "replace", killed)
        with pytest.raises(SystemExit):
            _switch(
                catalog=catalog,
                dataset="margin",
                target=Path(first["version"]),
                target_sha256=first["receipt_sha256"],
                expected=None,
                apply=True,
            )
    with pytest.raises(ValueError, match="pending"):
        export_margin_contract(catalog=catalog)
    result = module.recover_margin_catalog(catalog=catalog, apply=False)
    assert result["dry_run"]
    module.recover_margin_catalog(catalog=catalog, apply=True)
    assert not (catalog / "current/margin").exists()


@pytest.mark.parametrize(
    "damage", ["missing_numeric", "text_numeric", "invalid_day", "renamed_version"]
)
def test_schema_and_version_path_binding(candidate, tmp_path, damage):
    import shutil

    root, lineage = candidate
    if damage == "renamed_version":
        r = prepare(candidate, tmp_path)
        copied = Path(r["version"]).with_name("renamed")
        shutil.copytree(r["version"], copied)
        with pytest.raises(ValueError, match="identity"):
            _switch(
                catalog=tmp_path / "catalog",
                dataset="margin",
                target=copied,
                target_sha256=r["receipt_sha256"],
                expected=None,
            )
        return
    part = next(root.rglob("*.parquet"))
    frame = pd.read_parquet(part)
    if damage == "missing_numeric":
        frame = frame[["trade_date", "exchange_id"]]
    elif damage == "text_numeric":
        frame["rzye"] = "1"
    else:
        frame["trade_date"] = "20260230"
        part.parent.rename(part.parent.with_name("trade_date=20260230"))
        part = next(root.rglob("*.parquet"))
    frame.to_parquet(part, index=False)
    payload = json.loads(lineage.read_text())
    payload["inputs"][0].update(path=str(part), sha256=sha(part))
    lineage.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        prepare(candidate, tmp_path)


def test_existing_dryrun_and_export_leave_all_files_unchanged(candidate, tmp_path):
    from quant_market_data_platform.margin_publication import export_margin_contract

    r = prepare(candidate, tmp_path)
    catalog = tmp_path / "catalog"
    before = {str(p.relative_to(catalog)): sha(p) for p in catalog.rglob("*") if p.is_file()}
    _switch(
        catalog=catalog,
        dataset="margin",
        target=Path(r["version"]),
        target_sha256=r["receipt_sha256"],
        expected=None,
    )
    export_margin_contract(catalog=catalog)
    assert before == {
        str(p.relative_to(catalog)): sha(p) for p in catalog.rglob("*") if p.is_file()
    }
    (catalog / ".lock").unlink()
    with pytest.raises(ValueError, match="existing"):
        export_margin_contract(catalog=catalog)
    assert not (catalog / ".lock").exists()


def test_final_receipt_failure_restores_pointer(candidate, tmp_path, monkeypatch):
    import quant_market_data_platform.margin_transaction as module

    r = prepare(candidate, tmp_path)
    original = module.write_json_manifest

    def fail_receipt(path, payload):
        if path.parent.name == "receipts":
            raise OSError("final receipt failed")
        original(path, payload)

    monkeypatch.setattr(module, "write_json_manifest", fail_receipt)
    with pytest.raises(OSError, match="final receipt"):
        _switch(
            catalog=tmp_path / "catalog",
            dataset="margin",
            target=Path(r["version"]),
            target_sha256=r["receipt_sha256"],
            expected=None,
            apply=True,
        )
    assert not (tmp_path / "catalog/current/margin").exists()
    assert json.loads((tmp_path / "catalog/intent.json").read_text())["status"] == "rolled_back"


def test_foreign_current_target_is_rejected(candidate, tmp_path):
    local = prepare(candidate, tmp_path)
    foreign = prepare_margin_version(
        candidate=candidate[0], catalog=tmp_path / "other-catalog", lineage=candidate[1]
    )
    alias = tmp_path / "catalog/current/margin"
    alias.parent.mkdir()
    alias.symlink_to(foreign["version"])
    with pytest.raises(ValueError, match="catalog"):
        _switch(
            catalog=tmp_path / "catalog",
            dataset="margin",
            target=Path(local["version"]),
            target_sha256=local["receipt_sha256"],
            expected=foreign["version"],
            expected_current_sha256=foreign["receipt_sha256"],
            apply=True,
        )
    assert alias.readlink() == Path(foreign["version"])


def test_flush_failure_does_not_leave_prepared_receipt(candidate, tmp_path, monkeypatch):
    import quant_market_data_platform.margin_publication as module

    def fail(path):
        raise OSError("flush failed")

    monkeypatch.setattr(module, "sync_version", fail)
    with pytest.raises(OSError, match="flush failed"):
        prepare(candidate, tmp_path)
    assert list((tmp_path / "catalog").rglob("publication-receipt.json")) == []


def test_base_cli_without_posix_lock_module():
    import os
    import subprocess
    import sys

    program = (
        "import sys; sys.modules['fcntl']=None; "
        "from quant_market_data_platform.cli import main; main(['--help'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], env=os.environ, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    program = program.replace("['--help']", "['config', '--help']")
    result = subprocess.run(
        [sys.executable, "-c", program], env=os.environ, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_margin_expected_error_is_nonzero_without_traceback(tmp_path, capsys):
    from quant_market_data_platform.cli import main

    assert main(["margin", "contract", "--catalog", str(tmp_path / "absent")]) != 0
    output = capsys.readouterr()
    assert "existing" in output.err and "Traceback" not in output.err


def test_relative_current_link_rejected_before_intent(candidate, tmp_path):
    import os

    r = prepare(candidate, tmp_path)
    alias = tmp_path / "catalog/current/margin"
    alias.parent.mkdir()
    relative = os.path.relpath(r["version"], alias.parent)
    alias.symlink_to(relative)
    with pytest.raises(ValueError, match="absolute"):
        _switch(
            catalog=tmp_path / "catalog",
            dataset="margin",
            target=Path(r["version"]),
            target_sha256=r["receipt_sha256"],
            expected=relative,
            expected_current_sha256=r["receipt_sha256"],
            apply=True,
        )
    assert str(alias.readlink()) == relative
    assert not (tmp_path / "catalog/intent.json").exists()


def test_future_source_observation_rejected(candidate, tmp_path):
    from datetime import UTC, datetime, timedelta

    lineage = candidate[1]
    payload = json.loads(lineage.read_text())
    payload["inputs"][0]["observed_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    lineage.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="future"):
        prepare(candidate, tmp_path)


@pytest.mark.parametrize("damage", ["current_payload", "old_payload", "unknown_pointer"])
def test_explicit_rollback_preserves_damaged_version(candidate, tmp_path, damage):
    first = prepare(candidate, tmp_path)
    cat = tmp_path / "catalog"
    _switch(
        catalog=cat,
        dataset="margin",
        target=Path(first["version"]),
        target_sha256=first["receipt_sha256"],
        expected=None,
        apply=True,
    )
    lineage = candidate[1]
    payload = json.loads(lineage.read_text())
    payload["revision"] = 2
    lineage.write_text(json.dumps(payload))
    second = prepare(candidate, tmp_path)
    _switch(
        catalog=cat,
        dataset="margin",
        target=Path(second["version"]),
        target_sha256=second["receipt_sha256"],
        expected=first["version"],
        expected_current_sha256=first["receipt_sha256"],
        apply=True,
    )
    if damage == "unknown_pointer":
        (cat / "current/margin").unlink()
        (cat / "current/margin").symlink_to(tmp_path / "unknown")
    else:
        damaged = second if damage == "current_payload" else first
        next(Path(damaged["version"]).rglob("*.parquet")).write_bytes(b"damaged")
    request = {
        "catalog": cat,
        "dataset": "margin",
        "target": Path(first["version"]),
        "target_sha256": first["receipt_sha256"],
        "expected": second["version"],
        "expected_current_sha256": second["receipt_sha256"],
        "operation": "rollback",
        "apply": True,
    }

    if damage == "current_payload":
        _switch(**request)
        assert (cat / "current/margin").resolve() == Path(first["version"])
        assert next(Path(second["version"]).rglob("*.parquet")).read_bytes() == b"damaged"
    else:
        with pytest.raises(ValueError):
            _switch(**request)


def test_upstream_certification_claim_cannot_be_inherited(candidate, tmp_path):
    manifest = candidate[0] / "manifest.yml"
    raw = yaml.safe_load(manifest.read_text())
    raw["historical_pit_certified"] = True
    raw["full_market_ready"] = True
    manifest.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="certification"):
        prepare(candidate, tmp_path)


def test_symbolic_candidate_and_target_rejected(candidate, tmp_path):
    linked = tmp_path / "linked-candidate"
    linked.symlink_to(candidate[0])
    with pytest.raises(ValueError, match="physical"):
        prepare_margin_version(candidate=linked, catalog=tmp_path / "catalog", lineage=candidate[1])
    r = prepare(candidate, tmp_path)
    redirected = tmp_path / "catalog/versions/margin/redirect"
    redirected.symlink_to(r["version"])
    with pytest.raises(ValueError, match="physical"):
        _switch(
            catalog=tmp_path / "catalog",
            dataset="margin",
            target=redirected,
            target_sha256=r["receipt_sha256"],
            expected=None,
        )
