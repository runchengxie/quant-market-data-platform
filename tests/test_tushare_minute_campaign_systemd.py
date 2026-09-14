from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_ROOT = REPO_ROOT / "scripts" / "systemd"


def _unit(name: str) -> str:
    return (SYSTEMD_ROOT / name).read_text(encoding="utf-8")


def test_campaign_service_enforces_window_and_shared_quota() -> None:
    service = _unit("tushare-minute-replacement-campaign.service")

    assert "--max-new-rows 80000000" in service
    assert "--run-window-start 00:45" in service
    assert "--run-window-drain 04:15" in service
    assert "--run-window-stop 04:20" in service
    assert "--single-lane-threshold-rows 5500000" in service
    assert "--minute-quota-mode enforce" in service
    assert "--minute-quota-consumer replacement_campaign" in service
    assert "--minute-quota-gate requests" in service
    assert "--minute-quota-limit-requests 10000" in service
    assert "--minute-quota-burst-limit-requests 20000" in service
    assert "--minute-quota-safety-requests 500" in service
    assert "--no-minute-quota-allow-burst" in service
    assert "--minute-quota-limit-rows 160000000" in service
    assert "--minute-quota-safety-rows 4000000" in service
    assert "Requires=tushare-minute-quota-coordinator.service" in service
    assert "REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt" in service
    assert "EnvironmentFile=-@HOME@/.config/market-data-platform/config.env" in service
    assert "OnFailure=tushare-minute-replacement-campaign-status.service" in service
    assert "cutover" not in service.lower()


def test_reverse_backfill_is_staged_and_runs_one_newest_missing_day() -> None:
    service = _unit("tushare-minute-reverse-backfill.service")
    timer = _unit("tushare-minute-reverse-backfill.timer")

    assert "tushare_minute_reverse_backfill.py" in service
    assert "--token-env TUSHARE_TOKEN_2" in service
    assert "--max-dates 1" in service
    assert "Requires=tushare-minute-quota-coordinator.service" in service
    assert "ConditionPathExists=" in service
    assert "Persistent=false" in timer
    assert "OnCalendar=*-*-* 00:45:00 Asia/Shanghai" in timer
    assert "tushare-minute-reverse-backfill.service" in timer


def test_operational_daily_has_fallback_credential() -> None:
    service = _unit("tushare-minute-operational-daily.service")

    assert "--token-env TUSHARE_TOKEN_2" in service
    assert "--fallback-token-env TUSHARE_TOKEN" in service


def test_accelerator_reuses_campaign_budget_after_daily_raw_completion() -> None:
    service = _unit("tushare-minute-replacement-campaign-accelerate.service")

    assert "--max-new-rows 80000000" in service
    assert "--max-runtime-seconds 7200" in service
    assert "--run-window-start 08:00" in service
    assert "--run-window-drain 16:30" in service
    assert "--run-window-stop 16:40" in service
    assert "--single-lane-threshold-rows 5500000" in service
    assert "--minute-quota-mode enforce" in service
    assert "--minute-quota-consumer replacement_campaign" in service
    assert "--minute-quota-gate requests" in service
    assert "--no-minute-quota-allow-burst" in service
    assert "--minute-quota-limit-rows 160000000" in service
    assert "--minute-quota-safety-rows 4000000" in service
    assert "await-marker" in service
    assert "--consumer daily_watch20" in service
    assert "--not-before-local 05:15" in service
    assert "release-ready" in service
    assert "REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt" in service
    assert "EnvironmentFile=-@HOME@/.config/market-data-platform/config.env" in service
    assert "tushare_minute_replacement_campaign_accelerate.log" in service
    assert "cutover" not in service.lower()


def test_campaign_timer_is_nonpersistent_and_retries_inside_window() -> None:
    timer = _unit("tushare-minute-replacement-campaign.timer")

    assert "OnCalendar=*-*-* 00:45:00 Asia/Shanghai" in timer
    assert "OnCalendar=*-*-* 01..03:15,45:00 Asia/Shanghai" in timer
    assert "OnCalendar=*-*-* 04:00:00 Asia/Shanghai" in timer
    assert "Persistent=false" in timer
    assert "DeferReactivation=true" in timer


def test_campaign_has_independent_hard_stop_backstop() -> None:
    service = _unit("tushare-minute-replacement-campaign-hard-stop.service")
    timer = _unit("tushare-minute-replacement-campaign-hard-stop.timer")

    assert "--signal=SIGKILL tushare-minute-replacement-campaign.service" in service
    assert "OnCalendar=*-*-* 04:21:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer


def test_quota_coordinator_precedes_campaign_with_idempotent_request_holds() -> None:
    service = _unit("tushare-minute-quota-coordinator.service")
    timer = _unit("tushare-minute-quota-coordinator.timer")

    assert " coordinate " in service
    assert "--daily-consumer daily_watch20" in service
    assert "--daily-hold-requests 300" in service
    assert "--top200-consumer top200_factor_observation" in service
    assert "--top200-hold-requests 30" in service
    assert "--minute-quota-limit-requests 10000" in service
    assert "--minute-quota-burst-limit-requests 20000" in service
    assert "--minute-quota-safety-requests 500" in service
    assert "SuccessExitStatus=75" not in service
    assert "Restart=no" in service
    assert "Environment=PYTHONPATH=@MDP_DIR@/src" in service
    assert "OnCalendar=*-*-* 00:05:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer

    for dependent_name in (
        "tushare-minute-replacement-campaign.service",
        "tushare-minute-replacement-campaign-accelerate.service",
        "tushare-minute-replacement-campaign-tail.service",
    ):
        dependent = _unit(dependent_name)
        assert "EnvironmentFile=-@HOME@/.config/market-data-platform/config.env" in dependent
        assert "Requires=tushare-minute-quota-coordinator.service" in dependent
        assert "After=network-online.target tushare-minute-quota-coordinator.service" in dependent


def test_accelerator_timer_retries_only_inside_its_daytime_window() -> None:
    timer = _unit("tushare-minute-replacement-campaign-accelerate.timer")

    assert "OnCalendar=*-*-* 08:00:00 Asia/Shanghai" in timer
    assert "OnCalendar=*-*-* 09..16:00:00 Asia/Shanghai" in timer
    assert "Persistent=false" in timer
    assert "DeferReactivation=true" in timer


def test_tail_filler_uses_raw_marker_and_only_it_enters_burst() -> None:
    service = _unit("tushare-minute-replacement-campaign-tail.service")
    timer = _unit("tushare-minute-replacement-campaign-tail.timer")

    assert "ExecCondition=" in service
    assert "await-marker" in service
    assert "--consumer daily_watch20" in service
    assert "tushare_minute_quota_schedule.py status" in service
    assert "--allow-burst" in service
    assert "--not-before-local 05:15" in service
    assert "--deadline-local 23:25" in service
    assert "release-ready" in service
    assert "--minute-quota-allow-burst" in service
    assert "--max-new-rows 155000000" in service
    assert "--run-window-start 21:15" in service
    assert "--run-window-drain 23:30" in service
    assert "--run-window-stop 23:35" in service
    assert "--quota-reset-guard-seconds 1500" in service
    assert "--ignore-blocker-service a-share-factor-observation-refresh.service" in service
    assert "After=a-share-factor-observation-refresh.service" not in service
    assert "OnCalendar=*-*-* 21:15,45:00 Asia/Shanghai" in timer
    assert "OnCalendar=*-*-* 23:15:00 Asia/Shanghai" in timer


def test_tail_has_a_pre_midnight_manager_backstop() -> None:
    service = _unit("tushare-minute-replacement-campaign-tail-hard-stop.service")
    timer = _unit("tushare-minute-replacement-campaign-tail-hard-stop.timer")

    assert "--signal=SIGKILL tushare-minute-replacement-campaign-tail.service" in service
    assert "OnCalendar=*-*-* 23:36:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer
