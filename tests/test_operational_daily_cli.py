from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.operations import tushare_minute_operational_daily as command


@pytest.mark.parametrize(
    ("arguments", "expected_fallback"),
    [
        ([], "TUSHARE_TOKEN"),
        (["--fallback-token-env", "CUSTOM_MINUTE_TOKEN"], "CUSTOM_MINUTE_TOKEN"),
        (["--no-fallback"], None),
    ],
)
def test_daily_cli_passes_explicit_fallback_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    expected_fallback: str | None,
) -> None:
    observed = []

    def run(options):
        observed.append(options)
        return {"status": "noop", "date_max": "20261023"}

    monkeypatch.setattr(command, "run_operational_daily", run)

    assert command.main(["--artifacts-root", str(tmp_path), *arguments]) == 0

    assert len(observed) == 1
    assert observed[0].artifacts_root == tmp_path
    assert observed[0].token_env == "TUSHARE_TOKEN_2"
    assert observed[0].fallback_token_env == expected_fallback
    assert json.loads(capsys.readouterr().out)["status"] == "noop"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--no-fallback", "--fallback-token-env", "CUSTOM_MINUTE_TOKEN"],
        ["--fallback-token-env", "CUSTOM_MINUTE_TOKEN", "--no-fallback"],
    ],
)
def test_daily_cli_rejects_conflicting_fallback_policy(tmp_path: Path, arguments) -> None:
    with pytest.raises(SystemExit) as error:
        command.build_parser().parse_args(["--artifacts-root", str(tmp_path), *arguments])

    assert error.value.code == 2


@pytest.mark.parametrize("selector", ["", "CUSTOM_MINUTE_TOKEN"])
def test_project_environment_can_disable_or_select_fallback(tmp_path, monkeypatch, selector):
    monkeypatch.setenv("TUSHARE_OPERATIONAL_FALLBACK_TOKEN_ENV", selector)
    parsed = command.build_parser().parse_args(["--artifacts-root", str(tmp_path)])
    assert parsed.fallback_token_env == (selector or None)


def test_status_only_never_enters_mutating_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(command, "run_operational_daily", lambda *a: pytest.fail("run called"))
    monkeypatch.setattr(command, "inspect_operational_daily", lambda *a: {"status": "current"})
    assert command.main(["--artifacts-root", str(tmp_path), "--status-only"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "current"


def test_status_only_reports_behind_with_temporary_failure_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(command, "inspect_operational_daily", lambda *a: {"status": "behind"})
    assert command.main(["--artifacts-root", str(tmp_path), "--status-only"]) == 75
