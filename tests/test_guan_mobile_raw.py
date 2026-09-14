from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from market_data_platform.guan_mobile_raw import (
    GuanMobilePromotionConflict,
    GuanMobilePromotionError,
    GuanMobilePromotionVerificationError,
    PromotionOptions,
    plan_guan_mobile_promotion,
    promote_guan_mobile,
    verify_guan_mobile_promotion,
)
from market_data_platform.guan_mobile_raw_part01 import PromotionStrategy
from market_data_platform.providers.a_share_minute_build_part01 import (
    _discover_deal_files,
)


def _write_archive_manifest(
    path: Path,
    *,
    source: Path,
    files: list[Path],
) -> None:
    receipts: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for file_path in files:
        relative = file_path.relative_to(source).as_posix()
        file_stat = file_path.stat()
        sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
        total_bytes += file_stat.st_size
        receipts[relative] = {
            "status": "complete",
            "verification_status": "verified",
            "sha256": sha256,
            "verified_sha256": sha256,
            "size": file_stat.st_size,
            "destination_mtime_ns": file_stat.st_mtime_ns,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "guan.mobile_archive.v2",
                "status": "verified",
                "copy_status": "complete",
                "source_dir": "/run/media/test/Nuevo vol/level2",
                "destination_dir": str(source),
                "file_count": len(files),
                "bytes": total_bytes,
                "files": receipts,
            }
        ),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict[str, Path]]:
    raw_root = tmp_path / "enclosure" / "raw" / "cn_a_share_level2"
    source = raw_root / "_incoming" / "guan-mobile-test" / "level2"
    source.mkdir(parents=True)
    contents = {
        "deal_20260318.parquet": b"deal-18",
        "deal_20260318(1).parquet": b"deal-18",
        "snapshot_20260318.parquet": b"snapshot-18",
        "order_20260318.parquet": b"order-18",
        "index_20260318.parquet": b"index-18",
        "level2-note.txt": b"native schema notes",
    }
    files: dict[str, Path] = {}
    for index, (name, content) in enumerate(contents.items()):
        file_path = source / name
        file_path.write_bytes(content)
        mtime = 1_700_000_000_000_000_000 + index
        os.utime(file_path, ns=(mtime, mtime))
        files[name] = file_path
    archive_manifest = tmp_path / "metadata" / "archive.json"
    _write_archive_manifest(
        archive_manifest,
        source=source,
        files=list(files.values()),
    )
    receipt = tmp_path / "metadata" / "promotion.json"
    return raw_root, source, archive_manifest, receipt, files


def _options(  # noqa: PLR0913
    raw_root: Path,
    source: Path,
    archive_manifest: Path,
    receipt: Path,
    *,
    strategy: PromotionStrategy = "hardlink",
    dry_run: bool = False,
) -> PromotionOptions:
    return PromotionOptions(
        source_dir=source,
        raw_root=raw_root,
        archive_manifest=archive_manifest,
        receipt=receipt,
        strategy=strategy,
        dry_run=dry_run,
    )


def test_dry_run_builds_semantic_union_without_writes_and_isolates_month_overlap(
    tmp_path: Path,
) -> None:
    raw_root, source, archive_manifest, receipt, _ = _fixture(tmp_path)
    standardized_month = raw_root / "snapshot" / "snapshot_202603.parquet"
    standardized_month.parent.mkdir()
    standardized_month.write_bytes(b"different standardized schema")

    result = plan_guan_mobile_promotion(_options(raw_root, source, archive_manifest, receipt))

    assert result["status"] == "planned"
    assert result["summary"] == {
        "archive_files": 6,
        "supported_daily_files": 5,
        "auxiliary_files": 1,
        "logical_artifacts": 5,
        "planned_promotions": 5,
        "existing_same_sha_skipped": 0,
        "exact_source_duplicates_skipped": 1,
        "ignored_archive_files": 0,
        "standardized_schema_overlaps": 1,
        "conflicts": 0,
        "logical_bytes": 53,
        "additional_data_bytes": 0,
    }
    duplicate = result["source_duplicates"][0]
    assert duplicate["relative_path"] == "deal_20260318(1).parquet"
    assert duplicate["duplicate_of"] == "deal_20260318.parquet"
    snapshot = next(entry for entry in result["entries"] if entry["dataset"] == "snapshot")
    assert snapshot["standardized_overlaps"] == [str(standardized_month)]
    assert "/source_native/guan_mobile/snapshot/202603/" in snapshot["organized_path"]
    auxiliary = next(entry for entry in result["entries"] if entry["dataset"] == "auxiliary")
    assert auxiliary["logical_key"] == "auxiliary:level2-note.txt"
    assert auxiliary["organized_path"].endswith(
        "/source_native/guan_mobile/auxiliary/level2-note.txt"
    )
    assert not receipt.exists()
    assert not (raw_root / "source_native").exists()


def test_default_hardlink_promotion_is_atomic_idempotent_and_records_lineage(
    tmp_path: Path,
) -> None:
    raw_root, source, archive_manifest, receipt, files = _fixture(tmp_path)
    source_mode = files["deal_20260318.parquet"].stat().st_mode
    source_mtime = files["deal_20260318.parquet"].stat().st_mtime_ns

    first = promote_guan_mobile(_options(raw_root, source, archive_manifest, receipt))

    assert first["status"] == "complete"
    assert first["summary"]["promoted"] == 5
    assert first["summary"]["complete_logical_artifacts"] == 5
    deal = next(entry for entry in first["entries"] if entry["dataset"] == "deal")
    destination = Path(deal["organized_path"])
    source_stat = files["deal_20260318.parquet"].stat()
    destination_stat = destination.stat()
    assert (source_stat.st_dev, source_stat.st_ino) == (
        destination_stat.st_dev,
        destination_stat.st_ino,
    )
    assert source_stat.st_mode == source_mode
    assert source_stat.st_mtime_ns == source_mtime
    assert deal["origin_mobile_path"].endswith("/deal_20260318.parquet")
    assert deal["incoming_path"] == str(files["deal_20260318.parquet"])
    assert deal["sha256"] == hashlib.sha256(b"deal-18").hexdigest()
    assert not destination.with_name("deal_20260318(1).parquet").exists()

    second = promote_guan_mobile(_options(raw_root, source, archive_manifest, receipt))

    assert second["status"] == "complete"
    assert second["summary"]["promoted"] == 0
    assert second["summary"]["existing_same_sha_skipped"] == 5
    assert all(entry["status"] == "existing_same_sha_skipped" for entry in second["entries"])
    repeated_deal = next(entry for entry in second["entries"] if entry["dataset"] == "deal")
    assert repeated_deal["first_promoted_at"] == deal["first_promoted_at"]


def test_different_source_sha_for_same_logical_key_fails_before_data_promotion(
    tmp_path: Path,
) -> None:
    raw_root, source, archive_manifest, receipt, files = _fixture(tmp_path)
    duplicate = files["deal_20260318(1).parquet"]
    duplicate.write_bytes(b"different")
    _write_archive_manifest(
        archive_manifest,
        source=source,
        files=list(files.values()),
    )

    with pytest.raises(GuanMobilePromotionConflict, match="preflight"):
        promote_guan_mobile(_options(raw_root, source, archive_manifest, receipt))

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "conflict"
    assert payload["conflicts"][0]["type"] == "source_logical_key_sha_mismatch"
    assert not list((raw_root / "source_native" / "guan_mobile").rglob("*.parquet"))


def test_existing_different_sha_target_is_a_preflight_conflict(tmp_path: Path) -> None:
    raw_root, source, archive_manifest, receipt, _ = _fixture(tmp_path)
    target = (
        raw_root / "source_native" / "guan_mobile" / "deal" / "202603" / "deal_20260318.parquet"
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(b"conflicting raw")

    with pytest.raises(GuanMobilePromotionConflict, match="preflight"):
        promote_guan_mobile(_options(raw_root, source, archive_manifest, receipt))

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    conflict_types = {item["type"] for item in payload["conflicts"]}
    assert "target_logical_key_sha_mismatch" in conflict_types
    assert target.read_bytes() == b"conflicting raw"
    assert not list((raw_root / "source_native" / "guan_mobile" / "order").rglob("*"))


def test_existing_same_sha_file_is_skipped_without_claiming_it_is_a_hardlink(
    tmp_path: Path,
) -> None:
    raw_root, source, archive_manifest, receipt, files = _fixture(tmp_path)
    target = (
        raw_root / "source_native" / "guan_mobile" / "deal" / "202603" / "deal_20260318.parquet"
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(files["deal_20260318.parquet"].read_bytes())

    result = promote_guan_mobile(_options(raw_root, source, archive_manifest, receipt))
    deal = next(entry for entry in result["entries"] if entry["dataset"] == "deal")

    assert deal["status"] == "existing_same_sha_skipped"
    assert deal["storage_strategy"] == "preexisting_same_sha"
    assert target.stat().st_ino != files["deal_20260318.parquet"].stat().st_ino
    assert verify_guan_mobile_promotion(receipt)["verification_status"] == "verified"


def test_auxiliary_uses_canonical_filename_for_same_sha_skip_and_conflict(
    tmp_path: Path,
) -> None:
    raw_root, source, archive_manifest, receipt, files = _fixture(tmp_path)
    nested = source / "nested"
    nested.mkdir()
    duplicate = nested / "level2-note.txt"
    duplicate.write_bytes(files["level2-note.txt"].read_bytes())
    all_files = [*files.values(), duplicate]
    _write_archive_manifest(archive_manifest, source=source, files=all_files)

    plan = plan_guan_mobile_promotion(_options(raw_root, source, archive_manifest, receipt))
    auxiliary_duplicate = next(
        item
        for item in plan["source_duplicates"]
        if item["logical_key"] == "auxiliary:level2-note.txt"
    )
    assert auxiliary_duplicate["status"] == "exact_source_duplicate_skipped"

    duplicate.write_bytes(b"different auxiliary content")
    _write_archive_manifest(archive_manifest, source=source, files=all_files)
    with pytest.raises(GuanMobilePromotionConflict, match="preflight"):
        promote_guan_mobile(_options(raw_root, source, archive_manifest, receipt))
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    conflict = next(
        item for item in payload["conflicts"] if item["logical_key"] == "auxiliary:level2-note.txt"
    )
    assert conflict["type"] == "source_logical_key_sha_mismatch"


def test_copy_strategy_is_explicit_and_full_verification_detects_corruption(
    tmp_path: Path,
) -> None:
    raw_root, source, archive_manifest, receipt, files = _fixture(tmp_path)
    result = promote_guan_mobile(
        _options(raw_root, source, archive_manifest, receipt, strategy="copy")
    )
    deal = next(entry for entry in result["entries"] if entry["dataset"] == "deal")
    destination = Path(deal["organized_path"])
    assert destination.stat().st_ino != files["deal_20260318.parquet"].stat().st_ino

    verified = verify_guan_mobile_promotion(receipt)
    assert verified["verification_status"] == "verified"
    assert verified["verification"]["status"] == "verified"
    assert verified["verification"]["content_validation"] == "verified"
    assert verified["verification"]["receipt_update"] == "atomic_replace_and_parent_fsync"
    assert verified["verification"]["verified_artifacts"] == 5

    destination.write_bytes(b"corrupt")
    with pytest.raises(GuanMobilePromotionVerificationError, match="failure"):
        verify_guan_mobile_promotion(receipt)
    failed = json.loads(receipt.read_text(encoding="utf-8"))
    assert failed["verification_status"] == "failed"


def test_verification_rejects_changed_archive_manifest_anchor(tmp_path: Path) -> None:
    raw_root, source, archive_manifest, receipt, _ = _fixture(tmp_path)
    promote_guan_mobile(_options(raw_root, source, archive_manifest, receipt))
    archive_manifest.write_text(
        archive_manifest.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(GuanMobilePromotionVerificationError, match="failure"):
        verify_guan_mobile_promotion(receipt)
    failed = json.loads(receipt.read_text(encoding="utf-8"))
    assert failed["verification"]["failures"][0]["type"] == ("archive_manifest_sha256_mismatch")


def test_hardlink_failure_does_not_fall_back_to_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root, source, archive_manifest, receipt, _ = _fixture(tmp_path)

    def reject_link(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "link", reject_link)
    with pytest.raises(GuanMobilePromotionError, match="--strategy copy"):
        promote_guan_mobile(_options(raw_root, source, archive_manifest, receipt))
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["entries"][0]["status"] == "promotion_failed"


def test_minute_builder_discovers_month_partitioned_deals_and_rejects_duplicates(
    tmp_path: Path,
) -> None:
    deal_root = tmp_path / "source_native" / "guan_mobile" / "deal"
    first = deal_root / "202603" / "deal_20260318.parquet"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"deal")

    assert _discover_deal_files(deal_root) == {"20260318": first}

    duplicate = deal_root / "duplicate" / "deal_20260318.parquet"
    duplicate.parent.mkdir()
    duplicate.write_bytes(b"deal")
    with pytest.raises(ValueError, match="Duplicate Guan deal date"):
        _discover_deal_files(deal_root)
