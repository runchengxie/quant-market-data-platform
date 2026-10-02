from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest


def private_config(tmp_path: Path, **changes: object) -> Path:
    payload: dict[str, object] = {
        "schema_version": 1,
        "environment": {"TUSHARE_TOKEN": "fixture-secret"},
        "downloads": {},
    }
    payload.update(changes)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    return path


def test_json_environment_preserves_explicit_empty_and_opaque_secrets(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.configuration")
    secret = " dollar${HOME}\nquoted'value "
    path = private_config(
        tmp_path,
        environment={
            "TUSHARE_TOKEN": "json-token",
            "QUANTZONE_SIGN_SECRET": secret,
            "QUANTZONE_ACCESS_KEY": None,
            "DATA_PLATFORM_ROOT": "${HOME}/data/quant/quant-market-data-platform",
        },
    )
    config = api.load_config(path)
    env = api.resolve_environment(config, {"HOME": "/test-home", "TUSHARE_TOKEN": ""})
    assert env["TUSHARE_TOKEN"] == ""
    assert env["QUANTZONE_SIGN_SECRET"] == secret
    assert "QUANTZONE_ACCESS_KEY" not in env
    assert env["DATA_PLATFORM_ROOT"] == "/test-home/data/quant/quant-market-data-platform"
    assert env["DATA_PLATFORM_CONFIG"] == str(path)
    assert secret not in repr(config)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"environment": []},
        {"environment": {"TUSHARE_TOKEN": 123}},
        {"environment": {"bad-name": "secret-never-print"}},
        {"environment": {"TUSHARE_TOKEN": "secret\x00never-print"}},
        {"downloads": []},
        {"unknown": "secret-never-print"},
    ],
)
def test_rejects_invalid_json_contract_without_printing_values(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    api = importlib.import_module("quant_market_data_platform.configuration")
    with pytest.raises(api.ConfigurationError) as exc:
        api.load_config(private_config(tmp_path, **changes))
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("body", ["{bad", '{"schema_version":1,"schema_version":1}', "[]"])
def test_rejects_malformed_or_duplicate_json(tmp_path: Path, body: str) -> None:
    api = importlib.import_module("quant_market_data_platform.configuration")
    path = private_config(tmp_path)
    path.write_text(body)
    with pytest.raises(api.ConfigurationError):
        api.load_config(path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o400, 0o660])
def test_rejects_wrong_private_file_permissions(tmp_path: Path, mode: int) -> None:
    api = importlib.import_module("quant_market_data_platform.configuration")
    path = private_config(tmp_path)
    path.chmod(mode)
    with pytest.raises(api.ConfigurationError, match="0600"):
        api.load_config(path)


def test_rejects_symlink_without_resolving_it(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.configuration")
    target = private_config(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(api.ConfigurationError):
        api.load_config(link)


def test_rejects_wrong_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = importlib.import_module("quant_market_data_platform.configuration")
    if not hasattr(os, "geteuid"):
        pytest.skip("POSIX ownership contract")
    path = private_config(tmp_path)
    monkeypatch.setattr(os, "geteuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(api.ConfigurationError, match="owner"):
        api.load_config(path)


def test_explicit_missing_file_does_not_fall_back(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.configuration")
    missing = tmp_path / "absent.json"
    assert api.resolve_config_path({"DATA_PLATFORM_CONFIG": str(missing)}) == missing
    with pytest.raises(api.ConfigurationError):
        api.load_config(missing)


def test_portable_default_respects_xdg_and_is_optional(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.configuration")
    assert api.resolve_config_path({"XDG_CONFIG_HOME": str(tmp_path)}) is None
    directory = tmp_path / "quant-market-data-platform"
    directory.mkdir()
    path = private_config(directory)
    assert api.resolve_config_path({"XDG_CONFIG_HOME": str(tmp_path)}) == path


def test_rejects_unresolved_path_substitution(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.configuration")
    config = api.load_config(
        private_config(tmp_path, environment={"DATA_PLATFORM_ROOT": "${UNKNOWN}/data"})
    )
    with pytest.raises(api.ConfigurationError):
        api.resolve_environment(config, {})


def test_selected_config_ignores_legacy_env_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = importlib.import_module("quant_market_data_platform.configuration")
    config = private_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_PLATFORM_CONFIG", str(config))
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    (tmp_path / ".env").write_text("TUSHARE_TOKEN=legacy-token\n")
    assert api.apply_config_environment() == config
    assert os.environ["TUSHARE_TOKEN"] == "fixture-secret"
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("DATA_PLATFORM_CONFIG_LOADED", raising=False)
