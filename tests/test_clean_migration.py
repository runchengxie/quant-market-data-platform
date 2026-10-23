import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from quant_market_data_platform.clean_migration import CleanMigrationTarget
from quant_market_data_platform.clean_publication import (
    plan_clean_migration,
    publish_clean,
    recover_clean,
)


def fixture(root: Path) -> tuple[Path, Path]:
    target = root / "version"
    (target / "data").mkdir(parents=True)
    (target / "data" / "part.parquet").write_bytes(b"payload")
    (target / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "dataset": "daily_clean",
                "status": "completed",
                "output_dir": str(target),
            }
        )
    )
    alias = root / "latest"
    shutil.copytree(target, alias)
    return alias, target


def test_physical_migration_requires_full_sha_equivalence_and_preserves_inode(tmp_path):
    alias, target = fixture(tmp_path)
    original_inode = alias.stat().st_ino
    plan = plan_clean_migration(root=tmp_path, alias=alias, target=target)
    assert plan["equivalent"] is True
    result = publish_clean(
        root=tmp_path,
        alias=alias,
        target=CleanMigrationTarget(target, plan["source_sha256"]),
        metadata=[],
        commit=lambda: None,
    )
    backup = Path(result["physical_backup"])
    assert alias.is_symlink()
    assert backup.is_dir()
    assert backup.stat().st_ino == original_inode
    assert (backup / "data" / "part.parquet").read_bytes() == b"payload"


def test_physical_migration_failure_restores_original_directory_and_json(tmp_path):
    alias, target = fixture(tmp_path)
    original_inode = alias.stat().st_ino
    metadata = tmp_path / "current.json"
    metadata.write_text("old")
    plan = plan_clean_migration(root=tmp_path, alias=alias, target=target)

    def fail():
        metadata.write_text("partial")
        raise RuntimeError("health failed")

    with pytest.raises(RuntimeError, match="health failed"):
        publish_clean(
            root=tmp_path,
            alias=alias,
            target=CleanMigrationTarget(target, plan["source_sha256"]),
            metadata=[metadata],
            commit=fail,
        )
    assert not alias.is_symlink()
    assert alias.stat().st_ino == original_inode
    assert metadata.read_text() == "old"


def test_physical_migration_interruption_is_recoverable(tmp_path):
    alias, target = fixture(tmp_path)
    original_inode = alias.stat().st_ino
    plan = plan_clean_migration(root=tmp_path, alias=alias, target=target)

    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        publish_clean(
            root=tmp_path,
            alias=alias,
            target=CleanMigrationTarget(target, plan["source_sha256"]),
            metadata=[],
            commit=interrupt,
        )
    recover_clean(root=tmp_path, alias=alias)
    assert not alias.is_symlink()
    assert alias.stat().st_ino == original_inode
    assert recover_clean(root=tmp_path, alias=alias)["status"] == "rolled_back"


def test_changed_physical_source_rejects_saved_plan(tmp_path):
    alias, target = fixture(tmp_path)
    plan = plan_clean_migration(root=tmp_path, alias=alias, target=target)
    (alias / "data" / "part.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="equivalen|changed"):
        publish_clean(
            root=tmp_path,
            alias=alias,
            target=CleanMigrationTarget(target, plan["source_sha256"]),
            metadata=[],
            commit=lambda: None,
        )
    assert not alias.is_symlink()


def prepared_promotion(root):
    from test_tushare_a_share import _prepare_current_promotion_inputs

    from quant_market_data_platform.tushare_refresh import run_a_share_current_promotion

    _prepare_current_promotion_inputs(root)
    target = root / "assets/tushare/a_share/daily/a_share_all_20240102_20260529_daily_clean"
    manifest = target / "manifest.yml"
    payload = yaml.safe_load(manifest.read_text())
    payload["output_dir"] = str(target)
    manifest.write_text(yaml.safe_dump(payload))
    (target / "data").mkdir()
    (target / "data" / "part.parquet").write_bytes(b"payload")
    run_a_share_current_promotion(
        artifacts_root=root,
        start_date="20240102",
        end_date="20260529",
        apply=True,
        fail_on_severity="error",
    )
    return target


def test_clean_only_promotion_migrates_contract_without_rewriting_raw_or_universe(tmp_path):
    import json

    from quant_market_data_platform.tushare_refresh import run_a_share_current_promotion

    target = prepared_promotion(tmp_path)
    raw = tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_latest/manifest.yml"
    universe = tmp_path / "assets/universe/a_share_all_full_by_date.csv"
    before = [(path.stat().st_ino, path.read_bytes()) for path in (raw, universe)]
    options: dict[str, Any] = {
        "artifacts_root": tmp_path,
        "start_date": "20240102",
        "end_date": "20260529",
        "clean_only_linked": True,
        "fail_on_severity": "error",
    }
    plan = run_a_share_current_promotion(**options)
    migration = plan["planned_publication"]["alias_updates"]["daily_clean"]
    result = run_a_share_current_promotion(
        **options, apply=True, clean_migration_sha256=migration["source_sha256"]
    )
    alias = tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_clean_latest"
    assert alias.is_symlink()
    assert [(path.stat().st_ino, path.read_bytes()) for path in (raw, universe)] == before
    assert list(result["publication"]["alias_updates"]) == ["daily_clean"]
    assert result["publication"]["file_updates"] == {}
    contract = json.loads((tmp_path / "metadata/current_assets/a_share_current.json").read_text())
    assert contract["assets"]["daily_clean"]["resolved_path"] == str(target)
    assert contract["assets"]["daily_clean"]["manifest_path"] == str(target / "manifest.yml")


def test_clean_only_cli_requires_explicit_switch():
    from quant_market_data_platform.cli import build_parser

    args = build_parser().parse_args(
        [
            "tushare",
            "promote-a-share-current",
            "--start-date",
            "20240102",
            "--end-date",
            "20260529",
            "--clean-only-linked",
            "--clean-migration-sha256",
            "a" * 64,
        ]
    )
    assert args.clean_only_linked is True
    assert args.clean_migration_sha256 == "a" * 64


@pytest.mark.parametrize(
    "failure",
    [
        "write_current_contract",
        "write_combined_dataset_registry",
        "write_current_contract_health_report",
        "_write_release_evidence",
    ],
)
def test_clean_only_failure_restores_all_declared_metadata(tmp_path, monkeypatch, failure):
    from quant_market_data_platform import tushare_refresh_part02 as promotion
    from quant_market_data_platform.tushare_refresh import run_a_share_current_promotion

    prepared_promotion(tmp_path)
    alias = tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_clean_latest"
    original_inode = alias.stat().st_ino
    paths = [
        tmp_path / "metadata/current_assets/a_share_current.json",
        tmp_path / "metadata/dataset_registry.csv",
        tmp_path / "reports/a_share_current_health_20260529.json",
        tmp_path / "reports/a_share_current_release_20240102_20260529.json",
    ]
    originals = [path.read_bytes() for path in paths]
    options: dict[str, Any] = {
        "artifacts_root": tmp_path,
        "start_date": "20240102",
        "end_date": "20260529",
        "clean_only_linked": True,
        "fail_on_severity": "error",
    }
    plan = run_a_share_current_promotion(**options)
    expected = plan["planned_publication"]["alias_updates"]["daily_clean"]["source_sha256"]
    original = getattr(promotion, failure)

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected after metadata write")

    monkeypatch.setattr(promotion, failure, fail)
    with pytest.raises(RuntimeError, match="injected"):
        run_a_share_current_promotion(**options, apply=True, clean_migration_sha256=expected)
    assert not alias.is_symlink()
    assert alias.stat().st_ino == original_inode
    assert [path.read_bytes() for path in paths] == originals


def test_preexisting_physical_backup_is_never_overwritten(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from quant_market_data_platform import clean_publication

    alias, target = fixture(tmp_path)
    plan = plan_clean_migration(root=tmp_path, alias=alias, target=target)
    backup = tmp_path / ".latest.physical-rollback.collision"
    backup.mkdir()
    inode = backup.stat().st_ino
    monkeypatch.setattr(clean_publication, "uuid4", lambda: SimpleNamespace(hex="collision"))
    with pytest.raises(ValueError, match="exists"):
        publish_clean(
            root=tmp_path,
            alias=alias,
            target=CleanMigrationTarget(target, plan["source_sha256"]),
            metadata=[],
            commit=lambda: None,
        )
    assert not alias.is_symlink()
    assert backup.stat().st_ino == inode


def test_committed_migration_has_explicit_rollback(tmp_path):
    from quant_market_data_platform.clean_publication import rollback_clean

    alias, target = fixture(tmp_path)
    original_inode = alias.stat().st_ino
    plan = plan_clean_migration(root=tmp_path, alias=alias, target=target)
    publish_clean(
        root=tmp_path,
        alias=alias,
        target=CleanMigrationTarget(target, plan["source_sha256"]),
        metadata=[],
        commit=lambda: None,
    )
    rollback_clean(root=tmp_path, alias=alias)
    assert not alias.is_symlink()
    assert alias.stat().st_ino == original_inode


def test_recurring_linked_promotion_publishes_new_universe_and_retains_journals(tmp_path):
    import json

    from quant_market_data_platform.tushare_refresh import run_a_share_current_promotion

    old_target = prepared_promotion(tmp_path)
    options: dict[str, Any] = {
        "artifacts_root": tmp_path,
        "start_date": "20240102",
        "end_date": "20260529",
        "fail_on_severity": "error",
    }
    migration = run_a_share_current_promotion(**options, clean_only_linked=True)
    digest = migration["planned_publication"]["alias_updates"]["daily_clean"]["source_sha256"]
    run_a_share_current_promotion(
        **options, clean_only_linked=True, clean_migration_sha256=digest, apply=True
    )
    target = old_target.with_name("new_version")
    shutil.copytree(old_target, target)
    manifest = target / "manifest.yml"
    payload = yaml.safe_load(manifest.read_text())
    payload["output_dir"] = str(target)
    manifest.write_text(yaml.safe_dump(payload))
    stage = tmp_path / "assets/universe/staging/a_share_all_20240102_20260529_full_by_date.csv"
    stage.write_text("trade_date,symbol,liq_metric,selected\n20260529,600519.SH,2.0,True\n")
    for _ in range(2):
        result = run_a_share_current_promotion(
            **options, clean_linked=True, daily_clean_dir=target, apply=True
        )
        receipt = result["publication"]["alias_updates"]["daily_clean"]
        journal = Path(receipt["journal"])
        assert journal.is_file()
        assert json.loads(journal.read_text())["status"] == "committed"
    alias = tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_clean_latest"
    assert alias.is_symlink()
    assert alias.resolve() == target
    assert (
        tmp_path / "assets/universe/a_share_all_full_by_date.csv"
    ).read_bytes() == stage.read_bytes()
    assert len(list(alias.parent.glob(".*.clean-publication-lock.archive.*"))) == 3


def test_hard_process_exit_recovers_physical_directory(tmp_path):
    import subprocess
    import sys

    alias, target = fixture(tmp_path)
    inode = alias.stat().st_ino
    plan = plan_clean_migration(root=tmp_path, alias=alias, target=target)
    code = f"""import os
from pathlib import Path
from quant_market_data_platform.clean_migration import CleanMigrationTarget
from quant_market_data_platform.clean_publication import publish_clean
publish_clean(root=Path({str(tmp_path)!r}), alias=Path({str(alias)!r}),
 target=CleanMigrationTarget(Path({str(target)!r}), {plan["source_sha256"]!r}),
 metadata=[], commit=lambda: os._exit(23))
"""
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 23
    assert alias.is_symlink()
    recover_clean(root=tmp_path, alias=alias)
    assert not alias.is_symlink()
    assert alias.stat().st_ino == inode


def test_failure_between_directory_rename_and_link_restores_original(tmp_path, monkeypatch):
    from quant_market_data_platform import clean_publication

    alias, target = fixture(tmp_path)
    inode = alias.stat().st_ino
    plan = plan_clean_migration(root=tmp_path, alias=alias, target=target)

    def fail(*_args, **_kwargs):
        raise PermissionError("link denied")

    monkeypatch.setattr(clean_publication, "_link", fail)
    with pytest.raises(PermissionError):
        publish_clean(
            root=tmp_path,
            alias=alias,
            target=CleanMigrationTarget(target, plan["source_sha256"]),
            metadata=[],
            commit=lambda: None,
        )
    assert not alias.is_symlink()
    assert alias.stat().st_ino == inode


def test_archived_successful_cli_migration_can_rollback(tmp_path):
    from quant_market_data_platform.clean_publication import main
    from quant_market_data_platform.tushare_refresh import run_a_share_current_promotion

    prepared_promotion(tmp_path)
    alias = tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_clean_latest"
    inode = alias.stat().st_ino
    options: dict[str, Any] = {
        "artifacts_root": tmp_path,
        "start_date": "20240102",
        "end_date": "20260529",
        "clean_only_linked": True,
        "fail_on_severity": "error",
    }
    plan = run_a_share_current_promotion(**options)
    digest = plan["planned_publication"]["alias_updates"]["daily_clean"]["source_sha256"]
    result = run_a_share_current_promotion(**options, apply=True, clean_migration_sha256=digest)
    journal = result["publication"]["alias_updates"]["daily_clean"]["journal"]
    assert (
        main(["--root", str(tmp_path), "--alias", str(alias), "--rollback", "--journal", journal])
        == 0
    )
    assert not alias.is_symlink()
    assert alias.stat().st_ino == inode


@pytest.mark.parametrize("unsafe", ["duplicate", "raw_asset", "input_report"])
def test_linked_metadata_destinations_cannot_overwrite_other_assets_or_validation(tmp_path, unsafe):
    from quant_market_data_platform.tushare_refresh import run_a_share_current_promotion

    prepared_promotion(tmp_path)
    options: dict[str, Any] = {
        "artifacts_root": tmp_path,
        "start_date": "20240102",
        "end_date": "20260529",
        "clean_only_linked": True,
    }
    if unsafe == "duplicate":
        options["evidence_out"] = tmp_path / "metadata/current_assets/a_share_current.json"
    elif unsafe == "raw_asset":
        options["current_health_report"] = (
            tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_latest/manifest.yml"
        )
    else:
        options["evidence_out"] = (
            tmp_path / "reports/a_share_daily_clean_baseline_validation_20240102_20260529.json"
        )
    alias = tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_clean_latest"
    inode = alias.stat().st_ino
    with pytest.raises(ValueError, match="metadata"):
        run_a_share_current_promotion(**options)
    assert not alias.is_symlink()
    assert alias.stat().st_ino == inode


@pytest.mark.parametrize("action", ["recover", "acknowledge"])
def test_journal_symlink_is_rejected_before_reading(tmp_path, action):
    import json

    from quant_market_data_platform.clean_publication import acknowledge_clean

    alias, _target = fixture(tmp_path)
    lock = tmp_path / ".latest.clean-publication-lock"
    lock.mkdir()
    other = tmp_path / "other.json"
    other.write_text(
        json.dumps({"root": str(tmp_path), "alias": str(alias), "status": "committed"})
    )
    (lock / "journal.json").symlink_to(other)
    handler = recover_clean if action == "recover" else acknowledge_clean
    with pytest.raises(ValueError, match="journal"):
        handler(root=tmp_path, alias=alias)
    assert other.exists()
    assert (lock / "journal.json").is_symlink()


@pytest.mark.parametrize("mode", ["clean_only_linked", "clean_linked"])
@pytest.mark.parametrize("apply", [False, True])
def test_outside_metadata_rejected_before_any_directory_or_asset_mutation(tmp_path, mode, apply):
    from quant_market_data_platform.tushare_refresh import run_a_share_current_promotion

    root = tmp_path / "root"
    target = prepared_promotion(root)
    alias = root / "assets/tushare/a_share/daily/a_share_all_daily_clean_latest"
    if mode == "clean_linked":
        alias.rename(alias.with_name("fixture_previous"))
        alias.symlink_to(target.name, target_is_directory=True)
    outside = tmp_path / "outside" / "created"
    raw = root / "assets/tushare/a_share/daily/a_share_all_daily_latest/manifest.yml"
    universe = root / "assets/universe/a_share_all_full_by_date.csv"
    before = [(path.stat().st_ino, path.read_bytes()) for path in (raw, universe)]
    options: dict[str, Any] = {
        "artifacts_root": root,
        "start_date": "20240102",
        "end_date": "20260529",
        mode: True,
        "apply": apply,
        "evidence_out": outside / "evidence.json",
    }
    with pytest.raises(ValueError, match="inside root"):
        run_a_share_current_promotion(**options)
    assert not outside.parent.exists()
    assert [(path.stat().st_ino, path.read_bytes()) for path in (raw, universe)] == before


@pytest.mark.parametrize("mode", ["clean_only_linked", "clean_linked"])
@pytest.mark.parametrize("apply", [False, True])
def test_directory_metadata_rejected_before_asset_mutation(tmp_path, mode, apply):
    from quant_market_data_platform.tushare_refresh import run_a_share_current_promotion

    target = prepared_promotion(tmp_path)
    alias = tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_clean_latest"
    if mode == "clean_linked":
        alias.rename(alias.with_name("fixture_previous"))
        alias.symlink_to(target.name, target_is_directory=True)
    destination = tmp_path / "reports/health-directory"
    destination.mkdir()
    raw = tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_latest/manifest.yml"
    universe = tmp_path / "assets/universe/a_share_all_full_by_date.csv"
    before = [(path.stat().st_ino, path.read_bytes()) for path in (raw, universe)]
    with pytest.raises(ValueError, match="metadata outputs must be physical files"):
        run_a_share_current_promotion(
            artifacts_root=tmp_path,
            start_date="20240102",
            end_date="20260529",
            current_health_report=destination,
            apply=apply,
            clean_only_linked=mode == "clean_only_linked",
            clean_linked=mode == "clean_linked",
        )
    assert destination.is_dir()
    assert [(path.stat().st_ino, path.read_bytes()) for path in (raw, universe)] == before
