from pathlib import Path

import pytest

from market_data_platform.artifacts import (
    configured_data_platform_root,
    resolve_artifacts_root,
    resolve_configured_artifacts_root,
    resolve_data_input_path,
    resolve_metadata_db_path,
    resolve_repo_path,
    resolve_warehouse_db_path,
)
from market_data_platform.paths import resolve_artifacts_root as resolve_paths_artifacts_root


def test_resolve_repo_path_handles_relative_and_absolute_inputs(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)

    relative = resolve_repo_path("artifacts/runs")
    assert relative == (repo_root / "artifacts" / "runs").resolve()

    absolute_input = repo_root / "artifacts" / "cache"
    absolute = resolve_repo_path(absolute_input)
    assert absolute == absolute_input.resolve()


def test_resolve_artifacts_root_uses_data_platform_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_PLATFORM_ROOT", "platform-artifacts")

    assert resolve_artifacts_root() == (tmp_path / "platform-artifacts").resolve()


def test_configured_data_platform_root_returns_explicit_environment_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PLATFORM_ROOT", str(tmp_path / "platform-artifacts"))

    assert configured_data_platform_root() == (tmp_path / "platform-artifacts").resolve()


def test_configured_data_platform_root_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("DATA_PLATFORM_ROOT", raising=False)

    assert configured_data_platform_root() is None


def test_resolve_configured_artifacts_root_prefers_data_platform_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_PLATFORM_ROOT", "preferred-artifacts")

    resolved = resolve_configured_artifacts_root({"paths": {"artifacts_root": "config-artifacts"}})

    assert resolved == (tmp_path / "preferred-artifacts").resolve()


def test_resolve_data_input_path_rebases_shared_data_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_PLATFORM_ROOT", "platform-artifacts")

    assert (
        resolve_data_input_path("artifacts/assets/universe/demo.csv")
        == (tmp_path / "platform-artifacts" / "assets" / "universe" / "demo.csv").resolve()
    )
    assert (
        resolve_data_input_path("artifacts/metadata/current_assets/a_share_current.json")
        == (
            tmp_path / "platform-artifacts" / "metadata" / "current_assets" / "a_share_current.json"
        ).resolve()
    )
    assert (
        resolve_data_input_path("artifacts/runs/demo")
        == (tmp_path / "artifacts" / "runs" / "demo").resolve()
    )


def test_data_platform_root_does_not_override_run_artifacts_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_PLATFORM_ROOT", "platform-artifacts")

    assert resolve_artifacts_root() == (tmp_path / "platform-artifacts").resolve()
    assert resolve_paths_artifacts_root() == (tmp_path / "platform-artifacts").resolve()


@pytest.mark.parametrize(
    ("resolver", "env_name", "expected"),
    [
        (
            resolve_artifacts_root,
            "DATA_PLATFORM_ROOT",
            Path("preferred-artifacts"),
        ),
        (
            resolve_metadata_db_path,
            "DATA_PLATFORM_METADATA_DB_PATH",
            Path("preferred") / "catalog.sqlite",
        ),
        (
            resolve_warehouse_db_path,
            "DATA_PLATFORM_WAREHOUSE_DB_PATH",
            Path("preferred") / "warehouse.duckdb",
        ),
    ],
)
def test_platform_env_resolvers_use_platform_env(
    resolver,
    env_name,
    expected,
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(env_name, expected.as_posix())

    assert resolver() == (tmp_path / expected).resolve()
