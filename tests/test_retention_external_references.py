from pathlib import Path

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
