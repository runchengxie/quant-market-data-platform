from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from market_data_platform import cli


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_governance_audit_current_paths_writes_json_report(tmp_path: Path) -> None:
    version = tmp_path / "assets" / "daily_20260707"
    version.mkdir(parents=True)
    alias = version.parent / "daily_latest"
    alias.symlink_to(version.name, target_is_directory=True)
    contract = tmp_path / "metadata" / "current_assets" / "a_share_current.json"
    _write_json(
        contract,
        {
            "contract": {"market": "a_share", "target_date": "20260713"},
            "assets": {
                "daily": {
                    "alias_path": str(alias),
                    "resolved_path": str(version),
                    "manifest": {"query_end_date": "20260713"},
                }
            },
        },
    )
    report = tmp_path / "reports" / "current-paths.json"

    assert (
        cli.main(
            [
                "governance",
                "audit-current-paths",
                "--artifacts-root",
                str(tmp_path),
                "--out",
                str(report),
            ]
        )
        == 0
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["summary"]["assets"] == 1
    assert payload["summary"]["date_drift_assets"] == 1
    assert alias.is_symlink()


def test_governance_plan_retention_is_non_destructive(tmp_path: Path) -> None:
    candidate = tmp_path / "assets" / "pilot"
    candidate.mkdir(parents=True)
    (candidate / "data.parquet").write_bytes(b"pilot")
    inventory = tmp_path / "metadata" / "lifecycle" / "inventory.json"
    _write_json(
        inventory,
        {
            "schema_version": "market_data_platform.lifecycle_inventory.v1",
            "retention_rules": [
                {
                    "name": "pilot",
                    "kind": "explicit_path",
                    "path": "assets/pilot",
                    "disposition": "retire_candidate",
                }
            ],
        },
    )
    report = tmp_path / "metadata" / "retention" / "dry-run.tsv"
    latest = report.parent / "governance-latest.tsv"

    assert (
        cli.main(
            [
                "governance",
                "plan-retention",
                "--artifacts-root",
                str(tmp_path),
                "--out",
                str(report),
                "--latest-link",
                str(latest),
            ]
        )
        == 0
    )

    rows = list(csv.DictReader(io.StringIO(report.read_text(encoding="utf-8")), delimiter="\t"))
    assert rows[0]["action"] == "retire_candidate"
    assert rows[0]["path"] == str(candidate)
    assert candidate.joinpath("data.parquet").read_bytes() == b"pilot"
    assert latest.is_symlink()
    assert latest.readlink() == Path(report.name)


def test_governance_plan_retention_protects_current_contract_target(
    tmp_path: Path,
    capsys,
) -> None:
    current = tmp_path / "assets" / "version"
    current.mkdir(parents=True)
    _write_json(
        tmp_path / "metadata" / "current_assets" / "a_share_current.json",
        {
            "assets": {
                "daily": {
                    "exists": True,
                    "alias_path": str(current),
                    "resolved_path": str(current),
                }
            }
        },
    )
    _write_json(
        tmp_path / "metadata" / "lifecycle" / "inventory.json",
        {
            "schema_version": "market_data_platform.lifecycle_inventory.v1",
            "retention_rules": [
                {
                    "name": "stale-declaration",
                    "kind": "explicit_path",
                    "path": "assets/version",
                    "disposition": "retire_candidate",
                }
            ],
        },
    )

    assert (
        cli.main(
            [
                "governance",
                "plan-retention",
                "--artifacts-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out), delimiter="\t"))
    assert rows[0]["action"] == "keep"
    assert "declared current" in rows[0]["reason"]


def test_governance_plan_retention_protects_inventory_keep_entries(
    tmp_path: Path,
    capsys,
) -> None:
    current = tmp_path / "assets" / "version"
    current.mkdir(parents=True)
    _write_json(
        tmp_path / "metadata" / "lifecycle" / "inventory.json",
        {
            "schema_version": "market_data_platform.lifecycle_inventory.v1",
            "entries": [
                {
                    "path": "assets/version",
                    "disposition": "keep",
                }
            ],
            "retention_rules": [
                {
                    "name": "stale-declaration",
                    "kind": "explicit_path",
                    "path": "assets/version",
                    "disposition": "retire_candidate",
                }
            ],
        },
    )

    assert (
        cli.main(
            [
                "governance",
                "plan-retention",
                "--artifacts-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out), delimiter="\t"))
    assert rows[0]["action"] == "keep"
    assert "declared current or kept path" in rows[0]["reason"]


def test_governance_plan_refuses_to_overwrite_reports_or_regular_latest_paths(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "metadata" / "lifecycle" / "inventory.json",
        {
            "schema_version": "market_data_platform.lifecycle_inventory.v1",
            "retention_rules": [],
        },
    )
    report = tmp_path / "metadata" / "retention" / "existing.tsv"
    report.parent.mkdir(parents=True)
    report.write_text("historical\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="existing retention report"):
        cli.main(
            [
                "governance",
                "plan-retention",
                "--artifacts-root",
                str(tmp_path),
                "--out",
                str(report),
            ]
        )
    assert report.read_text(encoding="utf-8") == "historical\n"

    latest = report.parent / "governance-latest.tsv"
    latest.write_text("reserved\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="non-symlink latest path"):
        cli.main(
            [
                "governance",
                "plan-retention",
                "--artifacts-root",
                str(tmp_path),
                "--out",
                str(report.parent / "new.tsv"),
                "--latest-link",
                str(latest),
            ]
        )
