from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_ROOT = REPO_ROOT / "scripts" / "systemd"


def _unit(name: str) -> str:
    return (SYSTEMD_ROOT / name).read_text(encoding="utf-8")


def test_fundamentals_archive_runs_daily_without_publishing() -> None:
    service = _unit("tushare-fundamentals-vintage-archive.service")
    timer = _unit("tushare-fundamentals-vintage-archive.timer")

    assert "--observation-frequency daily" in service
    assert "archive_tushare_fundamentals_vintage.py" in service
    assert "publish" not in service.lower()
    assert "OnCalendar=*-*-* 02:30:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer
