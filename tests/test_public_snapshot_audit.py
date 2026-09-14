from __future__ import annotations

from pathlib import Path

from scripts.dev.audit_public_snapshot import audit_snapshot


def test_public_snapshot_audit_rejects_private_markers_and_internal_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("data at /" + "home/owner/data\n", encoding="utf-8")
    internal = tmp_path / "docs" / ("super" + "powers")
    internal.mkdir(parents=True)
    (internal / "plan.md").write_text("internal\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=do-not-commit\n", encoding="utf-8")

    findings = audit_snapshot(tmp_path)

    assert "personal path marker: README.md" in findings
    assert "excluded internal directory: docs/superpowers" in findings
    assert "credential file: .env" in findings


def test_public_snapshot_audit_accepts_clean_fixture(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "Configure DATA_PLATFORM_ROOT and TUSHARE_API_URL locally.\n", encoding="utf-8"
    )

    assert audit_snapshot(tmp_path) == []


def test_public_snapshot_audit_can_exclude_private_archive_paths(tmp_path: Path) -> None:
    (tmp_path / "docs" / "superpowers").mkdir(parents=True)
    (tmp_path / "docs" / "superpowers" / "plan.md").write_text("internal\n", encoding="utf-8")

    assert audit_snapshot(tmp_path, excluded_paths=("docs/superpowers/",)) == []
