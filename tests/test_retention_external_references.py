from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from quant_market_data_platform.data_governance import RetainNewestRule, plan_data_governance


def test_external_donor_and_link_protect_candidates(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    donor = root / "versions" / "v20260101"
    donor.mkdir(parents=True)
    external = tmp_path / "research"
    external.mkdir()
    (external / "receipt.json").write_text('{"reuse_from": "old/root/v20260101"}')
    items = plan_data_governance(
        root, [RetainNewestRule("versions", "versions", "v*", 0)], reference_roots=[external]
    )
    assert items[0].action == "keep"
    assert str(external) in items[0].reason
    (external / "receipt.json").unlink()
    (external / "latest").symlink_to(donor, target_is_directory=True)
    assert (
        plan_data_governance(
            root, [RetainNewestRule("versions", "versions", "v*", 0)], reference_roots=[external]
        )[0].action
        == "keep"
    )


@pytest.mark.parametrize("evidence", ["missing", "malformed", "unreadable"])
def test_unproven_external_evidence_fails_closed(tmp_path: Path, evidence: str) -> None:
    root = tmp_path / "assets"
    (root / "versions" / "v20260101").mkdir(parents=True)
    external = tmp_path / "research"
    if evidence != "missing":
        external.mkdir()
        (external / "receipt.json").write_bytes(b"{" if evidence == "malformed" else b"\xff")
    items = plan_data_governance(
        root, [RetainNewestRule("versions", "versions", "v*", 0)], reference_roots=[external]
    )
    assert items[0].action == "review"
    assert "reference scan incomplete" in items[0].reason


def test_hardlink_donor_keeps_shared_blocks_out_of_reclaimable(tmp_path: Path) -> None:
    import os

    root = tmp_path / "assets"
    donor = root / "versions" / "v20260101"
    donor.mkdir(parents=True)
    payload = donor / "payload"
    payload.write_bytes(b"payload" * 1000)
    consumer = root / "consumer"
    consumer.mkdir()
    os.link(payload, consumer / "reused")
    (consumer / "snapshot_receipt.json").write_text('{"reuse_from": "v20260101"}')
    items = plan_data_governance(root, [RetainNewestRule("versions", "versions", "v*", 0)])
    assert items[0].action == "keep"
    assert items[0].usage.reclaimable_bytes == 0
    assert items[0].usage.external_hardlink_inodes == 1


def test_unrelated_reports_do_not_multiply_path_containment_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unrelated retained text must not incur one path walk per candidate."""
    root = tmp_path / "assets"
    versions = root / "versions"
    for number in range(32):
        (versions / f"v{number:04}").mkdir(parents=True)
    reports = root / "reports"
    reports.mkdir()
    for number in range(64):
        (reports / f"report-{number}.md").write_text("Unrelated research evidence.")
    (reports / "donor.md").write_text("Retain former/root/v0001/data.parquet")
    (versions / "v0002" / "own.md").write_text("v0002 is this directory's own evidence")
    original = Path.is_relative_to
    containment_checks = 0
    name_reads = 0
    name_getter = Path.name.fget
    assert name_getter is not None

    def count_name(path: Path) -> str:
        nonlocal name_reads
        name_reads += 1
        return name_getter(path)

    def count_containment(path: Path, other: str | Path) -> bool:
        nonlocal containment_checks
        containment_checks += 1
        return original(path, other)

    monkeypatch.setattr(Path, "is_relative_to", count_containment)
    monkeypatch.setattr(Path, "name", property(count_name))
    items = plan_data_governance(root, [RetainNewestRule("versions", "versions", "v*", 0)])
    decisions = {item.path.name: item.action for item in items}
    assert decisions["v0001"] == "keep"
    assert decisions["v0002"] == "retire_candidate"
    assert list(decisions.values()).count("retire_candidate") == 31
    # A deterministic work bound avoids timing thresholds and machine-load noise.
    assert containment_checks <= 8 * (32 + 64)
    assert name_reads <= 8 * (32 + 64)


def test_reference_prefilter_preserves_literal_and_overlapping_names(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    versions = root / "versions"
    for name in ("v1", "v10", "v[0]+", "other"):
        (versions / name).mkdir(parents=True)
    (root / "report.md").write_text("former/root/v10/data and v[0]+/payload")
    items = plan_data_governance(root, [RetainNewestRule("versions", "versions", "*", 0)])
    assert {item.path.name: item.action for item in items} == {
        "v1": "keep",
        "v10": "keep",
        "v[0]+": "keep",
        "other": "retire_candidate",
    }


def test_unrelated_links_do_not_multiply_candidate_path_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "assets"
    for number in range(32):
        (root / "versions" / f"v{number:04}").mkdir(parents=True)
    links = root / "inputs"
    links.mkdir()
    for number in range(64):
        (links / f"link-{number}").symlink_to(root / "unrelated" / str(number))
    (links / "donor").symlink_to(root / "versions" / "v0001" / "missing")
    (root / "versions" / "v0002" / "own").symlink_to(root / "versions" / "v0002" / "missing")
    original = Path.is_relative_to
    checks = 0

    def count_checks(path: Path, other: str | Path) -> bool:
        nonlocal checks
        checks += 1
        return original(path, other)

    monkeypatch.setattr(Path, "is_relative_to", count_checks)
    items = plan_data_governance(root, [RetainNewestRule("versions", "versions", "v*", 0)])
    decisions = {item.path.name: item.action for item in items}
    assert decisions["v0001"] == "keep"
    assert decisions["v0002"] == "retire_candidate"
    assert list(decisions.values()).count("retire_candidate") == 31
    assert checks <= 8 * (32 + 64)


@pytest.mark.parametrize(
    ("location", "parent_action", "child_action"),
    [
        ("external", "keep", "keep"),
        ("parent", "retire_candidate", "keep"),
        ("child", "retire_candidate", "retire_candidate"),
    ],
)
@pytest.mark.parametrize("target_scope", ["parent", "descendant"])
def test_link_index_preserves_nested_candidate_self_exclusion(
    tmp_path: Path, location: str, parent_action: str, child_action: str, target_scope: str
) -> None:
    from quant_market_data_platform.data_governance import ExplicitPathRule

    parent = tmp_path / "v1"
    child = parent / "nested"
    child.mkdir(parents=True)
    sibling = tmp_path / "v10"
    sibling.mkdir()
    source = {"external": tmp_path, "parent": parent, "child": child}[location]
    (source / "link").symlink_to(parent if target_scope == "parent" else child / "missing")
    items = plan_data_governance(
        tmp_path,
        [
            ExplicitPathRule("parent", parent, "retire_candidate"),
            ExplicitPathRule("child", child, "retire_candidate"),
            ExplicitPathRule("sibling", sibling, "retire_candidate"),
        ],
    )
    assert {item.rule: item.action for item in items} == {
        "parent": parent_action,
        "child": child_action,
        "sibling": "retire_candidate",
    }


def test_link_index_path_components_preserve_flavor_and_anchor() -> None:
    from quant_market_data_platform.retention_references import _path_components

    assert _path_components(PureWindowsPath("C:/Data/V1")) == _path_components(
        PureWindowsPath("c:/data/v1")
    )
    assert _path_components(PureWindowsPath("C:/Data")) != _path_components(
        PureWindowsPath("D:/Data")
    )
    assert _path_components(PurePosixPath("/Data")) != _path_components(PurePosixPath("/data"))
    assert _path_components(PurePosixPath(".")) != _path_components(PurePosixPath("/"))


def test_reference_scan_excludes_only_the_exact_metadata_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "assets"
    donor = root / "versions" / "v0001"
    donor.mkdir(parents=True)
    for relative in ("metadata/retention", "metadata/lifecycle"):
        directory = root / relative
        directory.mkdir(parents=True)
        (directory / "report.md").write_text("v0001")
    unrelated = root / "unrelated"
    unrelated.mkdir()
    for number in range(1024):
        (unrelated / str(number)).mkdir()
    original = Path.relative_to
    calls = 0

    def count_relative(path: Path, other: str | Path) -> Path:
        nonlocal calls
        calls += 1
        return original(path, other)

    monkeypatch.setattr(Path, "relative_to", count_relative)
    rules = [RetainNewestRule("versions", "versions", "v*", 0)]
    assert plan_data_governance(root, rules)[0].action == "retire_candidate"
    nested = root / "nested/metadata/retention"
    nested.mkdir(parents=True)
    (nested / "report.md").write_text("v0001")
    [item] = plan_data_governance(root, rules)
    assert item.action == "keep"
    assert str(nested / "report.md") in item.reason
    assert calls < 32
