from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_script() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "operations"
        / "tushare_minute_replacement_campaign.py"
    )
    spec = importlib.util.spec_from_file_location("tushare_minute_campaign", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


campaign = _load_script()


def test_build_campaign_days_balances_and_canaries() -> None:
    dates = [f"202601{index:02d}" for index in range(1, 56)]

    days = campaign.build_campaign_days(
        dates,
        dates_per_day=50,
        canary_per_lane=3,
    )

    assert len(days) == 2
    first = days[0]
    assert len(first["dates"]) == 50
    assert [phase["name"] for phase in first["phases"]] == ["canary", "main"]
    canary, main = first["phases"]
    assert len(canary["lanes"]["a"]) == len(canary["lanes"]["b"]) == 3
    assert len(main["lanes"]["a"]) == len(main["lanes"]["b"]) == 22
    selected = {
        *canary["lanes"]["a"],
        *canary["lanes"]["b"],
        *main["lanes"]["a"],
        *main["lanes"]["b"],
    }
    assert selected == set(first["dates"])
    assert len(days[1]["phases"][0]["lanes"]["a"]) == 3
    assert len(days[1]["phases"][0]["lanes"]["b"]) == 2


@pytest.mark.parametrize(
    ("dates_per_day", "canary_per_lane"),
    [(1, 1), (3, 1), (50, 0), (50, 26)],
)
def test_build_campaign_days_rejects_invalid_limits(
    dates_per_day: int, canary_per_lane: int
) -> None:
    with pytest.raises(ValueError):
        campaign.build_campaign_days(
            ["20260101"],
            dates_per_day=dates_per_day,
            canary_per_lane=canary_per_lane,
        )


def _write_sidecar(
    root: Path,
    trade_date: str,
    *,
    status: str,
    completed: int,
    generated_at: str,
) -> None:
    path = root / f"trade_date={trade_date}" / campaign.COMPLETENESS_FILENAME
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "trade_date": trade_date,
                "status": status,
                "completed_symbols": [f"S{index}" for index in range(completed)],
                "expected_symbols": [f"S{index}" for index in range(10)],
                "partition": {"rows": completed * 241},
                "generated_at": generated_at,
            }
        ),
        encoding="utf-8",
    )


def test_sidecar_inventory_prefers_complete_and_best_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _write_sidecar(
        root_a,
        "20260101",
        status="partial",
        completed=4,
        generated_at="2026-01-01T00:00:00Z",
    )
    _write_sidecar(
        root_b,
        "20260101",
        status="complete",
        completed=10,
        generated_at="2026-01-01T01:00:00Z",
    )
    _write_sidecar(
        root_a,
        "20260102",
        status="partial",
        completed=5,
        generated_at="2026-01-01T00:00:00Z",
    )
    _write_sidecar(
        root_b,
        "20260102",
        status="partial",
        completed=7,
        generated_at="2026-01-01T01:00:00Z",
    )

    def fake_validate(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": 2410,
            "partition_sha256": "partition",
            "sidecar_sha256": "sidecar",
        }

    monkeypatch.setattr(campaign, "validate_complete_minute_partition", fake_validate)

    complete, partial = campaign._sidecar_inventory([root_a, root_b], {"20260101", "20260102"})

    assert set(complete) == {"20260101"}
    assert complete["20260101"]["data_root"] == str(root_b)
    assert set(partial) == {"20260102"}
    assert partial["20260102"]["completed_symbols"] == 7
    assert partial["20260102"]["data_root"] == str(root_b)


def test_load_dates_sorts_and_rejects_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "dates.json"
    path.write_text('{"dates":["20260102","20260101"]}', encoding="utf-8")
    assert campaign._load_dates(path) == ["20260101", "20260102"]

    path.write_text('{"dates":["20260101","20260101"]}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        campaign._load_dates(path)


def test_run_budgeted_cli_requires_limits_and_uses_safe_monitor_defaults(
    tmp_path: Path,
) -> None:
    parsed = campaign.build_parser().parse_args(
        [
            "run-budgeted",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--max-new-rows",
            "67000000",
            "--max-runtime-seconds",
            "11700",
        ]
    )

    assert parsed.max_new_rows == 67_000_000
    assert parsed.max_runtime_seconds == 11_700
    assert parsed.poll_seconds == 10
    assert parsed.interrupt_grace_seconds == 300
    assert parsed.quota_timezone == "Asia/Shanghai"
    assert parsed.quota_reset_guard_seconds == 1200
    assert parsed.run_window_start is None
    assert parsed.run_window_drain is None
    assert parsed.run_window_stop is None
    assert parsed.heartbeat_seconds == 300
    assert parsed.no_progress_limit == 2
    assert parsed.single_lane_threshold_rows == 0
    assert parsed.minute_quota_mode is None
    assert parsed.dry_run is False
    assert parsed.func is campaign.run_budgeted


@pytest.mark.parametrize(
    "missing_limit",
    ["--max-new-rows", "--max-runtime-seconds"],
)
def test_run_budgeted_cli_requires_both_limits(
    tmp_path: Path,
    missing_limit: str,
) -> None:
    values = {
        "--max-new-rows": "67000000",
        "--max-runtime-seconds": "11700",
    }
    argv = ["run-budgeted", "--manifest", str(tmp_path / "manifest.json")]
    for option, value in values.items():
        if option != missing_limit:
            argv.extend([option, value])

    with pytest.raises(SystemExit):
        campaign.build_parser().parse_args(argv)


def test_run_next_cli_remains_backward_compatible(tmp_path: Path) -> None:
    parsed = campaign.build_parser().parse_args(
        ["run-next", "--manifest", str(tmp_path / "manifest.json")]
    )

    assert parsed.dry_run is False
    assert parsed.func is campaign.run_next
    assert not hasattr(parsed, "max_new_rows")


def test_budgeted_cli_accepts_safe_window_and_shared_quota_controls(tmp_path: Path) -> None:
    parsed = campaign.build_parser().parse_args(
        [
            "run-budgeted",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--max-new-rows",
            "67000000",
            "--max-runtime-seconds",
            "14400",
            "--run-window-start",
            "00:45",
            "--run-window-drain",
            "04:15",
            "--run-window-stop",
            "04:20",
            "--single-lane-threshold-rows",
            "5500000",
            "--minute-quota-mode",
            "enforce",
            "--minute-quota-db",
            str(tmp_path / "quota.sqlite3"),
            "--minute-quota-consumer",
            "replacement_campaign",
            "--minute-quota-limit-rows",
            "80000000",
            "--minute-quota-safety-rows",
            "5000000",
            "--minute-quota-gate",
            "requests",
            "--minute-quota-limit-requests",
            "10000",
            "--minute-quota-burst-limit-requests",
            "20000",
            "--minute-quota-safety-requests",
            "500",
            "--minute-quota-allow-burst",
            "--ignore-blocker-service",
            "a-share-factor-observation-refresh.service",
            "--ignore-blocker-service",
            "another.service",
        ]
    )

    assert parsed.run_window_start == "00:45"
    assert parsed.run_window_drain == "04:15"
    assert parsed.run_window_stop == "04:20"
    assert parsed.single_lane_threshold_rows == 5_500_000
    assert parsed.minute_quota_mode == "enforce"
    assert parsed.minute_quota_consumer == "replacement_campaign"
    assert parsed.minute_quota_gate == "requests"
    assert parsed.minute_quota_limit_requests == 10_000
    assert parsed.minute_quota_burst_limit_requests == 20_000
    assert parsed.minute_quota_safety_requests == 500
    assert parsed.minute_quota_allow_burst is True
    assert parsed.ignore_blocker_service == [
        "a-share-factor-observation-refresh.service",
        "another.service",
    ]


def test_status_cli_supports_text_and_json(tmp_path: Path) -> None:
    parsed = campaign.build_parser().parse_args(
        ["status", "--manifest", str(tmp_path / "manifest.json"), "--json"]
    )

    assert parsed.json is True
    assert parsed.max_new_rows == 67_000_000
    assert parsed.run_window_start == "00:45"
    assert parsed.func is campaign.campaign_status
