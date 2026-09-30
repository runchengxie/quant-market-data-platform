from __future__ import annotations

import builtins
import importlib
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from types import ModuleType

import pytest

from quant_market_data_platform import cli

OPTIONAL_IMPORT_NAMES = {
    "duckdb",
    "pandas",
    "pyarrow",
    "tushare",
}
OPTIONAL_PLATFORM_MODULES = {
    "quant_market_data_platform.backup_data",
    "quant_market_data_platform.data_warehouse",
    "quant_market_data_platform.providers.tushare_a_share_clean",
    "quant_market_data_platform.providers.tushare_a_share_fundamentals",
    "quant_market_data_platform.providers.tushare_a_share_research",
    "quant_market_data_platform.providers.tushare_a_share_universe",
}
RELOAD_FOR_CLI_IMPORT = {
    "quant_market_data_platform.cli",
    "quant_market_data_platform.tushare_cli",
    *OPTIONAL_PLATFORM_MODULES,
}


def test_distribution_and_import_namespace_have_no_legacy_alias() -> None:
    assert version("quant-market-data-platform") == "0.2.0"
    assert find_spec("quant_market_data_platform") is not None
    assert find_spec("market_data_platform") is None
    with pytest.raises(PackageNotFoundError):
        version("market-data-platform")


def _guard_optional_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    imported: list[str] = []
    real_import: Callable = builtins.__import__

    def guarded_import(
        name: str,
        globals=None,
        locals=None,
        fromlist=(),
        level: int = 0,
    ) -> ModuleType:
        root_name = name.partition(".")[0]
        if root_name in OPTIONAL_IMPORT_NAMES or name in OPTIONAL_PLATFORM_MODULES:
            imported.append(name)
            raise AssertionError(f"unexpected optional import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    for module_name in RELOAD_FOR_CLI_IMPORT:
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    return imported


def test_build_parser_does_not_import_optional_provider_stacks(monkeypatch):
    imported = _guard_optional_imports(monkeypatch)

    cli = importlib.import_module("quant_market_data_platform.cli")
    cli.build_parser()

    assert imported == []


def test_paths_command_does_not_import_optional_provider_stacks(monkeypatch, capsys):
    imported = _guard_optional_imports(monkeypatch)

    cli = importlib.import_module("quant_market_data_platform.cli")
    assert cli.main(["paths", "--json"]) == 0

    output = capsys.readouterr().out
    assert '"current_contract"' in output
    assert imported == []


def test_missing_optional_provider_dependency_reports_actionable_extra(
    monkeypatch,
    tmp_path,
    capsys,
):
    from quant_market_data_platform import cli

    real_import: Callable = builtins.__import__

    def missing_research_import(
        name: str,
        globals=None,
        locals=None,
        fromlist=(),
        level: int = 0,
    ) -> ModuleType:
        if name == "quant_market_data_platform.providers.tushare_a_share_research":
            raise ModuleNotFoundError("No module named 'pyarrow'", name="pyarrow")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing_research_import)

    assert (
        cli.main(
            [
                "tushare",
                "download-a-share-industry-membership",
                "--out-dir",
                str(tmp_path),
            ]
        )
        == 1
    )

    error = capsys.readouterr().err
    assert "pyarrow is required for this command" in error
    assert "uv sync --extra tushare" in error


def test_marketdata_help_is_stable_and_complete(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "usage: marketdata" in output
    for command in (
        "paths",
        "contract",
        "registry",
        "data",
        "governance",
        "backup-data",
        "tushare",
    ):
        assert command in output


def test_tushare_help_is_stable_for_core_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["tushare", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    for command in (
        "mirror-a-share-daily",
        "backfill-a-share-history",
        "download-a-share-fundamentals",
        "build-a-share-universe",
    ):
        assert command in output
