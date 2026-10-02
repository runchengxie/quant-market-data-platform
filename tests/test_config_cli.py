from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from quant_market_data_platform.cli import main
from quant_market_data_platform.configuration import ConfigurationError
from quant_market_data_platform.providers._env import _resolve_token, resolve_tushare_api_url


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "private.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment": {
                    "TUSHARE_TOKEN": " opaque${HOME}\n'fixture' ",
                    "TUSHARE_TOKEN_2": "secondary-fixture",
                    "TUSHARE_API_URL_2": "https://proxy.example.com",
                },
                "downloads": {},
            }
        )
    )
    path.chmod(0o600)
    return path


def launch(path: Path, code: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("TUSHARE_")}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "quant_market_data_platform.cli",
            "config",
            "run",
            "--config",
            str(path),
            "--",
            sys.executable,
            "-c",
            code,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_check_never_prints_setting_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["config", "check", "--config", str(config_file(tmp_path))]) == 0
    result = capsys.readouterr()
    assert "opaque" not in result.out + result.err
    assert json.loads(result.out)["configured"]["TUSHARE_TOKEN"] is True


def test_selected_missing_config_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["config", "check", "--config", str(tmp_path / "missing")]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_run_preserves_argv_and_opaque_environment(tmp_path: Path) -> None:
    result = launch(
        config_file(tmp_path),
        "import os; assert os.environ['TUSHARE_TOKEN'] == \" opaque${HOME}\\n'fixture' \"; "
        "assert os.environ['DATA_PLATFORM_CONFIG_LOADED'] == os.environ['DATA_PLATFORM_CONFIG']",
    )
    assert result.returncode == 0, result.stderr


def test_run_preserves_child_exit_status(tmp_path: Path) -> None:
    assert launch(config_file(tmp_path), "raise SystemExit(7)").returncode == 7


@pytest.mark.skipif(os.name == "nt", reason="POSIX process signal")
def test_run_preserves_child_signal(tmp_path: Path) -> None:
    assert (
        launch(
            config_file(tmp_path), "import os,signal; os.kill(os.getpid(), signal.SIGTERM)"
        ).returncode
        == -signal.SIGTERM
    )


def test_run_requires_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "run", "--config", str(config_file(tmp_path)), "--"]) == 2
    assert "command" in capsys.readouterr().err


def test_direct_tushare_uses_selected_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_PLATFORM_CONFIG", str(config_file(tmp_path)))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("TUSHARE_TOKEN_2=legacy\n")
    assert _resolve_token(None, "TUSHARE_TOKEN_2") == "secondary-fixture"
    assert resolve_tushare_api_url(token_env="TUSHARE_TOKEN_2") == "https://proxy.example.com"
    for key in (
        "TUSHARE_TOKEN",
        "TUSHARE_TOKEN_2",
        "TUSHARE_API_URL_2",
        "DATA_PLATFORM_CONFIG_LOADED",
    ):
        monkeypatch.delenv(key, raising=False)


def test_direct_tushare_selected_missing_config_never_reads_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_PLATFORM_CONFIG", str(tmp_path / "missing"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("TUSHARE_TOKEN=legacy\n")
    with pytest.raises(ConfigurationError):
        _resolve_token(None, "TUSHARE_TOKEN")
