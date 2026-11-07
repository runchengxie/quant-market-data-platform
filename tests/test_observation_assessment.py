"""A raw capture inspection must never certify historical availability."""

import json
from pathlib import Path

import pytest
import yaml

from quant_market_data_platform.cli import main
from quant_market_data_platform.providers.tushare_a_share_fundamentals_support import (
    build_asset_integrity,
    file_sha256,
    seal_manifest,
)
from quant_market_data_platform.providers.tushare_fundamentals_observation import seal_observation


def observation(root: Path) -> str:
    # Canonical identity order differs from captured child order.
    for dataset in ["income", "balancesheet"]:
        child = root / "raw" / dataset
        child.mkdir(parents=True)
        data = child / "part.parquet"
        data.write_bytes(b"synthetic fixture bytes")
        payload = {
            "status": "completed",
            "immutable_snapshot": True,
            "retrieved_at": "2026-11-06T19:36:54+00:00",
            "observed_vintage_dates": ["20261106"],
            "integrity": build_asset_integrity(child, [data]),
        }
        (child / "manifest.yml").write_text(yaml.safe_dump(payload))
        seal_manifest(child / "manifest.yml")
    seal_observation(
        root,
        {"datasets": ["balancesheet", "income"], "snapshot_date": "20261107"},
        ["income", "balancesheet"],
    )
    return file_sha256(root / "manifest.yml")


def invoke(root, digest, *extra):
    return main(
        [
            "contract",
            "verify-observation",
            "--root",
            str(root),
            "--expected-manifest-sha256",
            digest,
            *extra,
        ]
    )


def test_verified_capture_is_read_only_and_preserves_unknown_pit(tmp_path, capsys):
    digest = observation(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert invoke(tmp_path, digest) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "quant.raw_observation_assessment.v1"
    assert result["manifest_sha256"] == digest
    assert result["data_content_integrity"] == "passed"
    assert result["historical_pit"] == "unknown"
    assert result["derived_materialization"] == "absent"
    assert result["research_ready"] is False
    assert result["time_basis"] == "UTC"
    assert result["children"][0]["retrieved_at"] == "2026-11-06T19:36:54+00:00"
    assert result["children"][0]["observed_vintage_dates"] == ["20261106"]
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_expected_hash",
        "changed_bytes",
        "missing_seal",
        "over_budget",
        "symlink_child",
        "unsafe_dataset",
    ],
)
def test_invalid_observation_never_prints_verified_result(tmp_path, capsys, mutation):
    digest = observation(tmp_path)
    extra = []
    if mutation == "wrong_expected_hash":
        digest = "0" * 64
    elif mutation == "changed_bytes":
        (tmp_path / "raw/income/part.parquet").write_bytes(b"tampered")
    elif mutation == "missing_seal":
        (tmp_path / "OBSERVATION_SEALED.json").unlink()
    elif mutation == "over_budget":
        extra = ["--max-content-bytes", "1"]
    elif mutation == "symlink_child":
        (tmp_path / "raw/income").rename(tmp_path / "redirected")
        (tmp_path / "raw/income").symlink_to(tmp_path / "redirected", target_is_directory=True)
    else:
        manifest = tmp_path / "manifest.yml"
        payload = yaml.safe_load(manifest.read_text())
        payload["children"][0]["path"] = "../../outside"
        manifest.write_text(yaml.safe_dump(payload))
        digest = file_sha256(manifest)
    assert invoke(tmp_path, digest, *extra) != 0
    assert not capsys.readouterr().out


def test_budget_rejects_before_canonical_content_scan(tmp_path, monkeypatch, capsys):
    from quant_market_data_platform import observation_assessment

    digest = observation(tmp_path)
    monkeypatch.setattr(
        observation_assessment,
        "verify_observation",
        lambda *args: pytest.fail("over-budget input reached canonical scanner"),
    )
    assert invoke(tmp_path, digest, "--max-files", "1") == 1
    assert not capsys.readouterr().out


def test_root_changed_during_scan_is_not_verified(tmp_path, monkeypatch, capsys):
    from quant_market_data_platform import observation_assessment

    digest = observation(tmp_path)
    original = observation_assessment.verify_observation

    def changing_scan(*args):
        result = original(*args)
        with (tmp_path / "manifest.yml").open("a") as stream:
            stream.write("\n# concurrent root change\n")
        return result

    monkeypatch.setattr(observation_assessment, "verify_observation", changing_scan)
    assert invoke(tmp_path, digest) == 1
    assert not capsys.readouterr().out
