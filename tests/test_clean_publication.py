from pathlib import Path

import pytest
import yaml

from quant_market_data_platform import clean_publication as publication


def version(root: Path, name: str) -> Path:
    target = root / name
    (target / "data").mkdir(parents=True)
    (target / "data" / "part.parquet").write_bytes(name.encode())
    (target / "manifest.yml").write_text(
        yaml.safe_dump({"status": "completed", "dataset": "daily_clean", "output_dir": str(target)})
    )
    return target


def test_publish_relative_link_and_metadata(tmp_path):
    target = version(tmp_path, "version1")
    alias = tmp_path / "latest"
    metadata = tmp_path / "current.json"
    metadata.write_text("old")
    result = publication.publish_clean(
        root=tmp_path,
        alias=alias,
        target=target,
        metadata=[metadata],
        commit=lambda: metadata.write_text("new"),
    )
    assert alias.is_symlink()
    assert alias.readlink() == Path("version1")
    assert metadata.read_text() == "new"
    assert result["resolved_path"] == str(target)
    assert result["materialized"] == "false"


def test_callback_failure_restores_alias_and_metadata(tmp_path):
    old = version(tmp_path, "old")
    new = version(tmp_path, "new")
    alias = tmp_path / "latest"
    alias.symlink_to(old.name, target_is_directory=True)
    metadata = tmp_path / "current.json"
    metadata.write_text("old")

    def fail():
        metadata.write_text("partial")
        raise RuntimeError("health failed")

    with pytest.raises(RuntimeError, match="health failed"):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=new, metadata=[metadata], commit=fail
        )
    assert alias.resolve() == old
    assert metadata.read_text() == "old"


def test_physical_latest_requires_explicit_migration_plan(tmp_path):
    alias = version(tmp_path, "latest")
    target = version(tmp_path, "new")
    plan = publication.plan_clean_migration(root=tmp_path, alias=alias, target=target)
    assert plan["requires_explicit_migration"] is True
    assert plan["rollback_source"] == str(alias)
    with pytest.raises(ValueError, match="migration"):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=target, metadata=[], commit=lambda: None
        )
    assert (alias / "data" / "part.parquet").read_bytes() == b"latest"


def test_interrupted_callback_requires_explicit_recovery(tmp_path):
    old = version(tmp_path, "old")
    target = version(tmp_path, "new")
    alias = tmp_path / "latest"
    alias.symlink_to(old.name, target_is_directory=True)
    metadata = tmp_path / "current.json"
    metadata.write_text("old")

    def interrupt():
        metadata.write_text("partial")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=target, metadata=[metadata], commit=interrupt
        )
    assert alias.resolve() == target
    assert publication.recover_clean(root=tmp_path, alias=alias)["status"] == "rolled_back"
    assert alias.resolve() == old
    assert metadata.read_text() == "old"
    assert publication.recover_clean(root=tmp_path, alias=alias)["status"] == "rolled_back"
    archive = publication.acknowledge_clean(root=tmp_path, alias=alias)
    assert archive.is_dir()
    publication.publish_clean(
        root=tmp_path, alias=alias, target=target, metadata=[], commit=lambda: None
    )
    assert alias.resolve() == target


@pytest.mark.parametrize("fault", ["running", "wrong_output", "missing_manifest", "linked_data"])
def test_invalid_target_preserves_old_alias(tmp_path, fault):
    old = version(tmp_path, "old")
    target = version(tmp_path, "new")
    alias = tmp_path / "latest"
    alias.symlink_to(old.name, target_is_directory=True)
    if fault == "running":
        (target / "manifest.yml").write_text("status: running\n")
    elif fault == "wrong_output":
        (target / "manifest.yml").write_text("status: completed\noutput_dir: /invalid\n")
    elif fault == "missing_manifest":
        (target / "manifest.yml").unlink()
    else:
        (target / "data" / "escape").symlink_to("/etc")
    with pytest.raises(ValueError):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=target, metadata=[], commit=lambda: None
        )
    assert alias.resolve() == old


def test_active_journal_prevents_second_publisher_and_acknowledgement(tmp_path):
    target = version(tmp_path, "new")
    alias = tmp_path / "latest"

    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=target, metadata=[], commit=interrupt
        )
    with pytest.raises(FileExistsError):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=target, metadata=[], commit=lambda: None
        )
    with pytest.raises(ValueError, match="recover"):
        publication.acknowledge_clean(root=tmp_path, alias=alias)


def test_outside_root_rejected_without_mutation(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = version(tmp_path, "outside")
    alias = root / "latest"
    with pytest.raises(ValueError, match="inside root"):
        publication.publish_clean(
            root=root, alias=alias, target=target, metadata=[], commit=lambda: None
        )
    assert not alias.exists()


def test_failed_link_creation_leaves_previous_publication(tmp_path, monkeypatch):
    target = version(tmp_path, "new")
    old = version(tmp_path, "old")
    alias = tmp_path / "latest"
    alias.symlink_to(old.name, target_is_directory=True)
    original = Path.symlink_to

    def denied(path, destination, target_is_directory=False):
        if str(destination) == "new":
            raise PermissionError("symlink denied")
        return original(path, destination, target_is_directory=target_is_directory)

    monkeypatch.setattr(Path, "symlink_to", denied)
    with pytest.raises(PermissionError):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=target, metadata=[], commit=lambda: None
        )
    assert alias.resolve() == old


def test_recovery_removes_new_metadata_and_alias(tmp_path):
    target = version(tmp_path, "new")
    alias = tmp_path / "latest"
    metadata = tmp_path / "new.json"

    def interrupt():
        metadata.write_text("partial")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=target, metadata=[metadata], commit=interrupt
        )
    publication.recover_clean(root=tmp_path, alias=alias)
    assert not metadata.exists()
    assert not alias.is_symlink()


def test_dry_run_is_read_only_and_cli_exposes_plan(tmp_path, capsys):
    target = version(tmp_path, "new")
    alias = version(tmp_path, "latest")
    assert (
        publication.main(["--root", str(tmp_path), "--alias", str(alias), "--target", str(target)])
        == 0
    )
    assert '"requires_explicit_migration": true' in capsys.readouterr().out
    assert not (tmp_path / ".latest.clean-publication-lock").exists()


@pytest.mark.parametrize(
    "fault", ["raw_manifest", "linked_manifest", "file_target", "alias_parent", "dangling_alias"]
)
def test_invalid_publication_paths_fail_closed(tmp_path, fault):
    target = version(tmp_path, "new")
    alias = tmp_path / "latest"
    if fault == "raw_manifest":
        (target / "manifest.yml").write_text(
            yaml.safe_dump({"status": "completed", "dataset": "daily", "output_dir": str(target)})
        )
    elif fault == "linked_manifest":
        manifest = target / "manifest.yml"
        manifest.rename(target / "original.yml")
        manifest.symlink_to("original.yml")
    elif fault == "file_target":
        target = target / "data" / "part.parquet"
    elif fault == "alias_parent":
        alias = tmp_path
    else:
        alias.symlink_to("missing", target_is_directory=True)
    with pytest.raises((ValueError, FileNotFoundError)):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=target, metadata=[], commit=lambda: None
        )
    assert not (tmp_path / ".latest.clean-publication-lock").exists()


def test_relative_link_survives_root_move(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = version(root, "new")
    publication.publish_clean(
        root=root, alias=root / "latest", target=target, metadata=[], commit=lambda: None
    )
    moved = tmp_path / "moved"
    root.rename(moved)
    assert (moved / "latest" / "data" / "part.parquet").read_bytes() == b"new"


def test_metadata_link_refused_before_alias_update(tmp_path):
    target = version(tmp_path, "new")
    alias = tmp_path / "latest"
    metadata = tmp_path / "current.json"
    metadata.symlink_to("missing")
    with pytest.raises(ValueError, match="physical files"):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=target, metadata=[metadata], commit=lambda: None
        )
    assert not alias.is_symlink()


def test_metadata_under_alias_rejected_without_corrupting_versions(tmp_path):
    old = version(tmp_path, "old")
    target = version(tmp_path, "new")
    alias = tmp_path / "latest"
    alias.symlink_to(old.name, target_is_directory=True)
    before = (target / "manifest.yml").read_bytes()
    with pytest.raises(ValueError, match="overlap|parent"):
        publication.publish_clean(
            root=tmp_path,
            alias=alias,
            target=target,
            metadata=[alias / "manifest.yml"],
            commit=lambda: None,
        )
    assert (target / "manifest.yml").read_bytes() == before
    assert alias.resolve() == old


def test_alias_parent_symlink_cannot_make_relative_link_escape_root(tmp_path):
    target = version(tmp_path, "new")
    actual = tmp_path / "actual"
    actual.mkdir()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "parent").symlink_to(actual, target_is_directory=True)
    alias = nested / "parent" / "latest"
    with pytest.raises(ValueError, match="parent"):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=target, metadata=[], commit=lambda: None
        )
    assert not (actual / "latest").is_symlink()


def test_existing_temporary_link_is_preserved(tmp_path, monkeypatch):
    from types import SimpleNamespace

    target = version(tmp_path, "new")
    alias = tmp_path / "latest"
    temporary = tmp_path / ".latest.collision.link"
    temporary.write_text("other writer")
    monkeypatch.setattr(publication, "uuid4", lambda: SimpleNamespace(hex="collision"))
    with pytest.raises(FileExistsError):
        publication.publish_clean(
            root=tmp_path, alias=alias, target=target, metadata=[], commit=lambda: None
        )
    assert temporary.read_text() == "other writer"
    assert not alias.is_symlink()


def test_existing_temporary_metadata_is_preserved(tmp_path, monkeypatch):
    from types import SimpleNamespace

    path = tmp_path / "metadata.json"
    temporary = tmp_path / ".metadata.json.collision.tmp"
    temporary.write_text("other writer")
    monkeypatch.setattr(publication, "uuid4", lambda: SimpleNamespace(hex="collision"))
    with pytest.raises(FileExistsError):
        publication._write(path, b"new")
    assert temporary.read_text() == "other writer"
