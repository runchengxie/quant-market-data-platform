from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

import pytest

from market_data_platform.data_governance import (
    ExplicitPathRule,
    JsonStatusRule,
    RetainNewestRule,
    plan_data_governance,
    protected_paths_from_inventory,
    render_retention_tsv,
    rules_from_inventory,
)


def _allocated_bytes(path: Path) -> int:
    return path.stat().st_blocks * 512


def test_explicit_private_file_reports_inode_aware_usage(tmp_path: Path) -> None:
    candidate = tmp_path / "pilot.bin"
    payload = b"candidate-bytes" * 500
    candidate.write_bytes(payload)

    [item] = plan_data_governance(
        tmp_path,
        [ExplicitPathRule("minute-pilot", candidate, "retire_candidate")],
    )

    assert item.action == "retire_candidate"
    assert item.path == candidate
    assert item.usage.logical_bytes == len(payload)
    assert item.usage.allocated_bytes == _allocated_bytes(candidate)
    assert item.usage.reclaimable_bytes == _allocated_bytes(candidate)
    assert item.usage.files == 1
    assert item.usage.unique_inodes == 1
    assert item.usage.external_hardlink_inodes == 0


def test_explicit_keep_rule_is_supported(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()

    [item] = plan_data_governance(
        tmp_path,
        [ExplicitPathRule("protected", protected, "keep")],
    )

    assert item.action == "keep"


def test_external_hardlink_is_allocated_but_not_reclaimable(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    source = candidate / "shared.parquet"
    source.write_bytes(b"shared" * 1_000)
    os.link(source, tmp_path / "kept.parquet")

    [item] = plan_data_governance(
        tmp_path,
        [ExplicitPathRule("pilot", candidate, "retire_candidate")],
    )

    assert item.usage.logical_bytes == source.stat().st_size
    assert item.usage.allocated_bytes == _allocated_bytes(source)
    assert item.usage.reclaimable_bytes == 0
    assert item.usage.files == 1
    assert item.usage.unique_inodes == 1
    assert item.usage.external_hardlink_inodes == 1


def test_internal_hardlinks_count_logical_paths_and_one_reclaimable_inode(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    first = candidate / "first.parquet"
    first.write_bytes(b"internal" * 1_000)
    os.link(first, candidate / "second.parquet")

    [item] = plan_data_governance(
        tmp_path,
        [ExplicitPathRule("internal-links", candidate, "retire_candidate")],
    )

    assert item.usage.logical_bytes == first.stat().st_size * 2
    assert item.usage.allocated_bytes == _allocated_bytes(first)
    assert item.usage.reclaimable_bytes == _allocated_bytes(first)
    assert item.usage.files == 2
    assert item.usage.unique_inodes == 1
    assert item.usage.external_hardlink_inodes == 0


def test_inventory_does_not_follow_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "large.bin").write_bytes(b"outside" * 2_000)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "external").symlink_to(outside, target_is_directory=True)

    [item] = plan_data_governance(
        tmp_path,
        [ExplicitPathRule("symlink-boundary", candidate, "review")],
    )

    assert item.action == "review"
    assert item.usage.logical_bytes == 0
    assert item.usage.allocated_bytes == 0
    assert item.usage.reclaimable_bytes == 0
    assert item.usage.files == 0


def test_retain_newest_keeps_newest_and_any_sibling_symlink_target(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "daily"
    parent.mkdir()
    oldest = parent / "snapshot_20260701"
    middle = parent / "snapshot_20260702"
    newest = parent / "snapshot_20260703"
    for path in (oldest, middle, newest):
        path.mkdir()
        (path / "data.parquet").write_bytes(path.name.encode())
    (parent / "current").symlink_to(oldest.name, target_is_directory=True)

    items = plan_data_governance(
        tmp_path,
        [RetainNewestRule("daily-clean", "daily", "snapshot_*", 1)],
    )
    actions = {item.path.name: item.action for item in items}

    assert [item.path.name for item in items] == [newest.name, middle.name, oldest.name]
    assert actions == {
        newest.name: "keep",
        middle.name: "retire_candidate",
        oldest.name: "keep",
    }
    assert "sibling symlink" in next(item.reason for item in items if item.path == oldest)


def test_retain_newest_orders_by_rightmost_date_before_basename(tmp_path: Path) -> None:
    parent = tmp_path / "versions"
    parent.mkdir()
    older = parent / "z_snapshot_20260101_payload"
    newer = parent / "a_snapshot_20260713_payload"
    older.mkdir()
    newer.mkdir()

    items = plan_data_governance(
        tmp_path,
        [RetainNewestRule("versions", "versions", "*_payload", 1)],
    )

    assert [item.path for item in items] == [newer, older]
    assert [item.action for item in items] == ["keep", "retire_candidate"]


def test_explicit_candidate_is_kept_when_sibling_symlink_targets_it(tmp_path: Path) -> None:
    version = tmp_path / "minute_v3"
    version.mkdir()
    (tmp_path / "minute_current").symlink_to(version.name, target_is_directory=True)

    [item] = plan_data_governance(
        tmp_path,
        [ExplicitPathRule("minute", version, "retire_candidate")],
    )

    assert item.action == "keep"
    assert "sibling symlink" in item.reason


def test_json_status_glob_only_flags_explicit_eligible_statuses(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    payloads = {
        "released.lock.json": {"status": "released"},
        "complete.checkpoint.json": {"status": "complete"},
        "owned.lock.json": {"status": "owned"},
        "progress.checkpoint.json": {"status": "in_progress"},
    }
    for name, payload in payloads.items():
        (metadata / name).write_text(json.dumps(payload), encoding="utf-8")
    (metadata / "broken.lock.json").write_text("{broken", encoding="utf-8")

    items = plan_data_governance(
        tmp_path,
        [
            JsonStatusRule(
                "state-files",
                "metadata",
                "*.json",
                frozenset({"released", "complete"}),
            )
        ],
    )
    by_name = {item.path.name: item for item in items}

    assert by_name["released.lock.json"].action == "retire_candidate"
    assert by_name["released.lock.json"].status == "released"
    assert by_name["complete.checkpoint.json"].action == "retire_candidate"
    assert by_name["owned.lock.json"].action == "keep"
    assert by_name["progress.checkpoint.json"].action == "keep"
    assert by_name["broken.lock.json"].action == "review"
    assert by_name["broken.lock.json"].status is None


def test_missing_explicit_path_is_reported_without_inventory(tmp_path: Path) -> None:
    [item] = plan_data_governance(
        tmp_path,
        [ExplicitPathRule("missing-pilot", "missing", "retire_candidate")],
    )

    assert item.action == "missing"
    assert item.usage.logical_bytes == 0
    assert item.usage.unique_inodes == 0


def test_rules_cannot_escape_configured_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes the configured root"):
        plan_data_governance(
            tmp_path,
            [ExplicitPathRule("escape", tmp_path.parent, "review")],
        )


def test_rules_cannot_escape_through_symlink_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.json").write_text("{}", encoding="utf-8")
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="resolves outside the configured root"):
        plan_data_governance(
            root,
            [ExplicitPathRule("escape", "escape/data.json", "retire_candidate")],
        )


def test_explicit_rule_cannot_target_entire_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot target the configured root"):
        plan_data_governance(
            tmp_path,
            [ExplicitPathRule("root", ".", "retire_candidate")],
        )


def test_cross_parent_current_alias_protects_version_and_descendants(tmp_path: Path) -> None:
    version = tmp_path / "versions" / "minute_v3"
    version.mkdir(parents=True)
    data = version / "data.parquet"
    data.write_bytes(b"current")
    aliases = tmp_path / "aliases"
    aliases.mkdir()
    current = aliases / "minute_current"
    current.symlink_to(version, target_is_directory=True)

    items = plan_data_governance(
        tmp_path,
        [
            ExplicitPathRule("version", version, "retire_candidate"),
            ExplicitPathRule("version-file", data, "retire_candidate"),
        ],
        protected_paths=[current],
    )

    assert [item.action for item in items] == ["keep", "keep"]
    assert all("declared current" in item.reason for item in items)


def test_current_alias_also_protects_its_lexical_parent(tmp_path: Path) -> None:
    version = tmp_path / "versions" / "minute_v3"
    version.mkdir(parents=True)
    aliases = tmp_path / "aliases"
    aliases.mkdir()
    current = aliases / "minute_current"
    current.symlink_to(version, target_is_directory=True)

    [item] = plan_data_governance(
        tmp_path,
        [ExplicitPathRule("aliases", aliases, "retire_candidate")],
        protected_paths=[current, version],
    )

    assert item.action == "keep"
    assert "declared current" in item.reason


def test_render_retention_tsv_has_stable_audit_columns_and_escaping(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.tsv"
    candidate.write_text("content", encoding="utf-8")
    [item] = plan_data_governance(
        tmp_path,
        [ExplicitPathRule("explicit\trule", candidate, "review")],
    )

    rows = list(csv.DictReader(io.StringIO(render_retention_tsv([item])), delimiter="\t"))

    assert len(rows) == 1
    assert set(rows[0]) == {
        "action",
        "rule",
        "logical_bytes",
        "allocated_bytes",
        "reclaimable_bytes",
        "files",
        "unique_inodes",
        "external_hardlink_inodes",
        "path",
        "reason",
        "status",
    }
    assert rows[0]["action"] == "review"
    assert rows[0]["rule"] == "explicit\trule"
    assert rows[0]["reclaimable_bytes"] == str(_allocated_bytes(candidate))
    assert rows[0]["status"] == ""


def test_rules_from_inventory_validates_and_materializes_all_rule_kinds() -> None:
    rules = rules_from_inventory(
        {
            "schema_version": "market_data_platform.lifecycle_inventory.v1",
            "retention_rules": [
                {
                    "name": "keep-current",
                    "kind": "explicit_path",
                    "path": "assets/current",
                    "disposition": "keep",
                },
                {
                    "name": "keep-two",
                    "kind": "retain_newest",
                    "parent": "assets/versions",
                    "pattern": "snapshot_*",
                    "retain_newest": 2,
                },
                {
                    "name": "released-locks",
                    "kind": "json_status",
                    "parent": "metadata",
                    "pattern": "*.lock",
                    "candidate_statuses": ["released"],
                },
            ],
        }
    )

    assert [type(rule) for rule in rules] == [
        ExplicitPathRule,
        RetainNewestRule,
        JsonStatusRule,
    ]


def test_protected_paths_from_inventory_reads_keep_entries_only() -> None:
    protected = protected_paths_from_inventory(
        {
            "schema_version": "market_data_platform.lifecycle_inventory.v1",
            "entries": [
                {"path": "assets/current", "disposition": "keep"},
                {"path": "assets/old", "disposition": "retire_candidate"},
            ],
        }
    )

    assert protected == ["assets/current"]


def test_rules_from_inventory_rejects_unknown_schema_and_kind() -> None:
    with pytest.raises(ValueError, match="Unsupported lifecycle inventory schema"):
        rules_from_inventory({"schema_version": "unknown", "retention_rules": []})

    with pytest.raises(ValueError, match="kind is unsupported"):
        rules_from_inventory(
            {
                "schema_version": "market_data_platform.lifecycle_inventory.v1",
                "retention_rules": [{"name": "bad", "kind": "age"}],
            }
        )
