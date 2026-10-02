from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from quant_market_data_platform.configuration import load_config


def fixture_config(tmp_path: Path, section: str | None = None, **updates: object) -> Any:
    payload = json.loads(
        (Path(__file__).parents[1] / "tests/fixtures/platform-config-v1.json").read_text()
    )
    payload["environment"]["DATA_PLATFORM_ROOT"] = str(tmp_path / "data")
    if section:
        payload["downloads"]["quantzone"][section].update(updates)
    else:
        payload["downloads"]["quantzone"].update(updates)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    return load_config(path)


class FakeClient:
    def __init__(self) -> None:
        self.closed = False
        self.factor_queries: list[dict[str, object]] = []

    def get_quota(self) -> dict[str, object]:
        return {"available_bytes": 1000000}

    def list_factors(self) -> list[dict[str, str]]:
        return [
            {
                "factorname": "trend_dominance_factor",
                "startDate": "2020-01-01",
                "endDate": "2026-01-01",
            }
        ]

    def list_stocks(self) -> pd.DataFrame:
        return pd.DataFrame({"ukey": ["000001.SZ", "600519.SH", "430001.BJ"]})

    def close(self) -> None:
        self.closed = True

    def get_factors(self, **kwargs: object) -> pd.DataFrame:
        self.factor_queries.append(kwargs)
        return pd.DataFrame()


def test_plan_is_deterministic_bounded_and_secret_free(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_plan")
    config = fixture_config(tmp_path, "query", end_date="2024-02-01")
    plan = api.build_factor_plan(config, {})
    assert len(plan.batches) == 5
    assert all(len(b.ukeys) <= 100 and len(b.factors) <= 20 for b in plan.batches)
    assert all((b.end_date - b.start_date).days <= 6 for b in plan.batches)
    assert plan.query_identity == api.build_factor_plan(config, {}).query_identity
    assert plan.root == tmp_path / "data/research/quantzone-pilot"
    assert "QUANTZONE_SIGN_SECRET" not in repr(plan)


@pytest.mark.parametrize(
    ("section", "updates"),
    [
        (None, {"sdk_version": "0.9.0"}),
        (None, {"timeout_seconds": 61}),
        (None, {"timeout_seconds": True}),
        ("query", {"ukeys": []}),
        ("query", {"ukeys": ["invalid"]}),
        ("query", {"factor": ["duplicate", "duplicate"]}),
        ("query", {"start_date": "2025-01-01", "end_date": "2024-01-01"}),
        ("query", {"end_date": None}),
        ("batch", {"max_symbols": 101}),
        ("batch", {"max_factors": 21}),
        ("batch", {"calendar_days": 366}),
        ("retry", {"max_attempts": 2}),
        ("output", {"relative_directory": "../escape"}),
        ("output", {"relative_directory": "/tmp/escape"}),
        ("output", {"immutable_runs": False}),
        ("evidence", {"pit_availability": "proven"}),
    ],
)
def test_rejects_unsafe_query_settings(
    tmp_path: Path, section: str | None, updates: dict[str, object]
) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_plan")
    with pytest.raises(ValueError):
        api.build_factor_plan(fixture_config(tmp_path, section, **updates), {})


def test_output_symlink_cannot_escape_data_root(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_plan")
    config = fixture_config(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/research").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        api.build_factor_plan(config, {})


def test_catalog_check_verifies_identifiers_without_factor_requests(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.providers.quantzone")
    plan_api = importlib.import_module("quant_market_data_platform.quantzone_plan")
    client = FakeClient()
    result = api.check_quantzone(client, plan_api.build_factor_plan(fixture_config(tmp_path), {}))
    assert result["symbol_map"] == {"000001": "000001.SZ", "600519": "600519.SH"}
    assert result["factor_coverage"]["trend_dominance_factor"]["start_date"] == "2020-01-01"
    assert client.factor_queries == []


def test_sdk_requires_credentials_before_import(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.providers.quantzone")
    with pytest.raises(ValueError, match="credentials"):
        api.create_client(fixture_config(tmp_path), {})


def test_ambiguous_catalog_identifiers_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = importlib.import_module("quant_market_data_platform.providers.quantzone")
    plan_api = importlib.import_module("quant_market_data_platform.quantzone_plan")
    client = FakeClient()
    monkeypatch.setattr(
        client,
        "list_stocks",
        lambda: pd.DataFrame({"ukey": ["000001.XSHE", "000001.XSHG", "600519.XSHG"]}),
    )
    with pytest.raises(ValueError, match="ambiguous"):
        api.check_quantzone(client, plan_api.build_factor_plan(fixture_config(tmp_path), {}))


def test_dry_run_never_constructs_sdk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from quant_market_data_platform.cli import main

    config = fixture_config(tmp_path)
    monkeypatch.setenv("DATA_PLATFORM_CONFIG", str(config.path))
    monkeypatch.setattr(
        "quant_market_data_platform.cli_quantzone.create_client",
        lambda *args: pytest.fail("dry-run must remain offline"),
    )
    assert main(["quantzone", "download-factors", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["batch_count"] == 1


@pytest.mark.parametrize(
    "catalog",
    [
        [],
        [
            {
                "factorname": "trend_dominance_factor",
                "startDate": "2025-01-01",
                "endDate": "2026-01-01",
            }
        ],
    ],
)
def test_catalog_coverage_fails_before_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalog: list[dict[str, str]]
) -> None:
    api = importlib.import_module("quant_market_data_platform.providers.quantzone")
    plan_api = importlib.import_module("quant_market_data_platform.quantzone_plan")
    client = FakeClient()
    monkeypatch.setattr(client, "list_factors", lambda: catalog)
    with pytest.raises(ValueError):
        api.check_quantzone(client, plan_api.build_factor_plan(fixture_config(tmp_path), {}))
    assert client.factor_queries == []


@pytest.mark.parametrize("command", ["check", "download-factors"])
@pytest.mark.parametrize("no_proxy", [False, True])
def test_explicit_direct_mode_scopes_proxy_environment_to_client_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_proxy: bool, command: str
) -> None:
    import os

    from quant_market_data_platform.cli import main

    config = fixture_config(tmp_path)
    monkeypatch.setenv("DATA_PLATFORM_CONFIG", str(config.path))
    monkeypatch.setenv("ALL_PROXY", "socks5://fixture.invalid:1080")
    monkeypatch.setenv("https_proxy", "")
    seen = []

    def factory(*args: object, **kwargs: object) -> FakeClient:
        seen.append({key: os.environ.get(key) for key in ("ALL_PROXY", "https_proxy")})
        return FakeClient()

    monkeypatch.setattr("quant_market_data_platform.cli_quantzone.create_client", factory)
    monkeypatch.setattr(
        "quant_market_data_platform.quantzone_download.run_factor_download",
        lambda *args, **kwargs: tmp_path,
    )
    assert main(["quantzone", command, *(["--no-proxy"] if no_proxy else [])]) == 0
    assert seen == [
        {"ALL_PROXY": None, "https_proxy": None}
        if no_proxy
        else {"ALL_PROXY": "socks5://fixture.invalid:1080", "https_proxy": ""}
    ]
    assert os.environ["ALL_PROXY"] == "socks5://fixture.invalid:1080"
    assert os.environ["https_proxy"] == ""


def test_direct_mode_restores_proxy_environment_when_client_construction_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from quant_market_data_platform.cli import main
    from quant_market_data_platform.configuration import ConfigurationError

    config = fixture_config(tmp_path)
    monkeypatch.setenv("DATA_PLATFORM_CONFIG", str(config.path))
    monkeypatch.setenv("ALL_PROXY", "socks5://fixture.invalid:1080")

    def factory(*args: object, **kwargs: object) -> FakeClient:
        assert "ALL_PROXY" not in os.environ
        raise ConfigurationError("fixture client unavailable")

    monkeypatch.setattr("quant_market_data_platform.cli_quantzone.create_client", factory)
    assert main(["quantzone", "check", "--no-proxy"]) == 2
    assert os.environ["ALL_PROXY"] == "socks5://fixture.invalid:1080"
