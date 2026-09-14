from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

import market_data_platform.cli as cli
import market_data_platform.providers.public_etf_minute as provider


def test_public_etf_minute_cli_parser_exposes_platform_command() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(
        [
            "data",
            "mirror-public-etf-minute",
            "--symbols",
            "512880.SH,159915.SZ",
            "--start-date",
            "20260824",
            "--end-date",
            "20260825",
        ]
    )

    assert args.data_command == "mirror-public-etf-minute"
    assert args.period == "1"
    assert args.source == "auto"
    assert args.network_mode == "system"
    assert args.no_skip is False
    assert args.dry_run is False


def test_public_etf_minute_cli_dispatches_options(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_mirror(options):
        captured["options"] = options
        return {"status": "planned", "output_dir": str(tmp_path)}

    monkeypatch.setattr(provider, "mirror_public_etf_minute", fake_mirror)

    exit_code = cli.main(
        [
            "data",
            "mirror-public-etf-minute",
            "--symbols",
            "512880.SH,159915.SZ",
            "--start-date",
            "20260824",
            "--end-date",
            "20260825",
            "--period",
            "15",
            "--source",
            "sina",
            "--network-mode",
            "direct",
            "--out-dir",
            str(tmp_path / "output"),
            "--no-skip",
            "--dry-run",
        ]
    )

    options = cast(provider.EtfMinuteMirrorOptions, captured["options"])
    assert exit_code == 0
    assert options.symbols == ("512880.SH", "159915.SZ")
    assert options.period == "15"
    assert options.source == "sina"
    assert options.network_mode == "direct"
    assert options.output_dir == tmp_path / "output"
    assert options.skip_existing is False
    assert options.dry_run is True


def test_public_etf_minute_missing_dependency_message_names_extra() -> None:
    error = ModuleNotFoundError("No module named 'akshare'")
    error.name = "akshare"
    args = argparse.Namespace(
        command="data",
        data_command="mirror-public-etf-minute",
    )

    message = cli._format_missing_dependency_error(args, error)

    assert message == (
        "akshare is required for this command. "
        "Install optional dependencies with `uv sync --extra etf-minute-public`."
    )
