from __future__ import annotations

import json
from pathlib import Path

import pytest

from quant_market_data_platform.configuration import (
    ConfigurationError,
    load_config,
    resolve_environment,
)
from quant_market_data_platform.quantzone_plan import build_factor_plan

ROOT = Path(__file__).parents[1]


def private_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    return path


def split_config(tmp_path: Path) -> Path:
    private_json(
        tmp_path / "keys.json",
        {"fred": "untouched", "tushare": " dollar${HOME}\nquoted'value ", "quantzone": None},
    )
    legacy = json.loads((ROOT / "config/config.example.json").read_text())
    # Fixtures remain independent of the new example contract.
    if legacy["schema_version"] == 1:
        job = dict(legacy["downloads"]["quantzone"])
        connection = {key: job.pop(key) for key in ("sdk_version", "timeout_seconds")}
    else:
        job = json.loads((ROOT / "config/jobs/quantzone-pilot.example.json").read_text())[
            "quantzone"
        ]
        connection = legacy["providers"]["quantzone"]
    private_json(tmp_path / "job.json", {"schema_version": 1, "quantzone": job})
    return private_json(
        tmp_path / "config.json",
        {
            "schema_version": 2,
            "environment": {
                "DATA_PLATFORM_ROOT": str(tmp_path / "data"),
                "QUANTZONE_BASE_URL": "https://api.quantzone.tech",
            },
            "credentials": {
                "path": "keys.json",
                "keys": {"TUSHARE_TOKEN": "tushare", "QUANTZONE_ACCESS_KEY": "quantzone"},
            },
            "providers": {"quantzone": connection},
            "jobs": {"quantzone": "job.json"},
        },
    )


def test_shared_registry_resolves_only_referenced_keys_and_preserves_values(tmp_path: Path) -> None:
    config = load_config(split_config(tmp_path))
    env = resolve_environment(config, {})
    assert env["TUSHARE_TOKEN"] == " dollar${HOME}\nquoted'value "
    assert "fred" not in env and "QUANTZONE_ACCESS_KEY" not in env
    assert "dollar" not in repr(config)
    assert resolve_environment(config, {"TUSHARE_TOKEN": ""})["TUSHARE_TOKEN"] == ""
    assert config.downloads == {}


@pytest.mark.parametrize(
    "problem", ["missing", "symlink", "permissions", "duplicate", "type", "conflict"]
)
def test_selected_shared_registry_fails_closed_without_secret_output(
    tmp_path: Path, problem: str
) -> None:
    path = split_config(tmp_path)
    keys = tmp_path / "keys.json"
    if problem == "missing":
        keys.unlink()
    elif problem == "symlink":
        keys.rename(tmp_path / "real.json")
        keys.symlink_to(tmp_path / "real.json")
    elif problem == "permissions":
        keys.chmod(0o644)
    elif problem == "duplicate":
        keys.write_text('{"tushare":"secret-first","tushare":"secret-second"}')
    elif problem == "type":
        private_json(keys, {"tushare": {"secret": "never-print"}})
    else:
        payload = json.loads(path.read_text())
        payload["environment"]["TUSHARE_TOKEN"] = "secret-inline"
        private_json(path, payload)
    with pytest.raises(ConfigurationError) as error:
        load_config(path)
    assert "secret" not in str(error.value) and "never-print" not in str(error.value)


def test_job_is_separate_and_explicit_override_changes_query(tmp_path: Path) -> None:
    config = load_config(split_config(tmp_path))
    plan = build_factor_plan(config, {})
    assert plan.batches[0].ukeys == ("000001.XSHE", "600519.XSHG")
    assert plan.snapshot["query"]["start_date"] == "2024-01-02"
    assert plan.snapshot["timeout_seconds"] == 60
    assert "tushare" not in json.dumps(plan.snapshot)
    job = json.loads((tmp_path / "job.json").read_text())
    job["quantzone"]["query"]["ukeys"] = ["000001.XSHE"]
    private_json(tmp_path / "override.json", job)
    override = build_factor_plan(config, {}, job=tmp_path / "override.json")
    assert len(override.batches[0].ukeys) == 1
    assert override.query_identity != plan.query_identity


def test_job_cannot_override_connection_or_introduce_secrets(tmp_path: Path) -> None:
    config = load_config(split_config(tmp_path))
    job = json.loads((tmp_path / "job.json").read_text())
    job["quantzone"]["timeout_seconds"] = 1
    private_json(tmp_path / "job.json", job)
    with pytest.raises(ConfigurationError):
        build_factor_plan(config, {})


def test_config_check_does_not_open_or_require_job(tmp_path: Path) -> None:
    path = split_config(tmp_path)
    (tmp_path / "job.json").unlink()
    assert load_config(path).environment["TUSHARE_TOKEN"]
    with pytest.raises(ConfigurationError):
        build_factor_plan(load_config(path), {})
