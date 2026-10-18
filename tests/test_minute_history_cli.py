import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

from scripts.operations import tushare_minute_history as command


def test_status_cli_reads_without_provider_or_output_writes(capsys):
    module = SimpleNamespace(__name__="quant_market_data_platform.minute_history_status")
    module.history_status = lambda *a, **k: {}
    module.watch_history = lambda *a, **k: {}
    module.history_status = lambda root, runtime, **kw: {
        "state": "quota_wait",
        "writes_production": False,
    }
    with patch.dict(sys.modules, {module.__name__: module}):
        assert (
            command.main(["status", "--artifacts-root", "/fixture", "--runtime-root", "/runtime"])
            == 0
        )
    assert json.loads(capsys.readouterr().out)["state"] == "quota_wait"


def test_watch_surfaces_critical_audit_block(capsys):
    module = SimpleNamespace(__name__="quant_market_data_platform.minute_history_status")
    module.history_status = lambda *a, **k: {}
    module.watch_history = lambda *a, **k: {}
    module.watch_history = lambda root, runtime, state, **kw: {"state": "audit_blocked"}
    with patch.dict(sys.modules, {module.__name__: module}):
        assert (
            command.main(
                [
                    "watch",
                    "--artifacts-root",
                    "/fixture",
                    "--runtime-root",
                    "/runtime",
                    "--state-root",
                    "/state",
                ]
            )
            == 75
        )
    assert json.loads(capsys.readouterr().out)["state"] == "audit_blocked"


def test_repair_cli_has_its_own_scope_and_budget(capsys):
    module = SimpleNamespace(__name__="quant_market_data_platform.minute_gap_repair")
    module.GapRepairOptions = lambda **kw: kw

    def run(options):
        assert options["daily_request_limit"] == 500
        assert options["dry_run"] is True
        return {"status": "empty_queue"}

    module.run_gap_repair = run
    with patch.dict(sys.modules, {module.__name__: module}):
        assert (
            command.main(
                [
                    "repair",
                    "--artifacts-root",
                    "/fixture",
                    "--runtime-root",
                    "/runtime",
                    "--dry-run",
                ]
            )
            == 0
        )
    assert json.loads(capsys.readouterr().out)["status"] == "empty_queue"


def test_systemd_lanes_share_audit_marker_and_artifact_root():
    from pathlib import Path

    units = Path(__file__).resolve().parents[1] / "scripts/systemd"
    main = (units / "tushare-minute-reverse-backfill.service").read_text()
    repair = (units / "tushare-minute-gap-repair.service").read_text()
    watch = (units / "tushare-minute-history-watch.service").read_text()
    assert "--audit-block-path @MINUTE_RUNTIME_ROOT@/semantic-audit-blocked.json" in main
    for text in (repair, watch):
        assert "--runtime-root @MINUTE_RUNTIME_ROOT@" in text
        assert "--artifacts-root @DATA_PLATFORM_ROOT@" in text
    assert "config run" not in watch
