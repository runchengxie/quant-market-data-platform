from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from quant_market_data_platform.published_assets import (
    PublishedAssetContract,
    PublishedAssetContractError,
)


def fixture(root: Path):
    assets = {}
    for key in ("daily", "calendar"):
        target = root / f"{key}-v1"
        target.mkdir(parents=True)
        (target / "manifest.yml").write_text(yaml.safe_dump({"dataset": key, "version": 1}))
        (target / "data.bin").write_bytes(b"synthetic fixture")
        alias = root / f"{key}-current"
        alias.symlink_to(target, target_is_directory=True)
        assets[key] = {"alias_path": str(alias), "manifest_path": str(alias / "manifest.yml")}
    path = root / "metadata" / "contract.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "contract": {
                    "name": "fixture",
                    "market": "a_share",
                    "provider": "tushare",
                    "version": 1,
                    "artifacts_root": str(root),
                },
                "assets": assets,
            }
        )
    )
    return PublishedAssetContract.load(path, artifacts_root=root)


def test_snapshot_is_pinned_and_explicitly_metadata_only(tmp_path):
    contract = fixture(tmp_path)
    snapshot = contract.capture(["daily", "calendar"])
    receipt = snapshot.provenance_dict()
    assert receipt["schema_version"] == "marketdata.asset-snapshot.v1"
    assert receipt["integrity_scope"] == "manifest_identity"
    assert receipt["data_content_verified"] is False
    assert [ref.key for ref in snapshot.assets] == ["daily", "calendar"]
    alias = tmp_path / "daily-current"
    alias.unlink()
    replacement = tmp_path / "daily-v2"
    replacement.mkdir()
    alias.symlink_to(replacement, target_is_directory=True)
    snapshot.validate_metadata()
    assert snapshot.assets[0].resolved_path == tmp_path / "daily-v1"
    # A manifest receipt must not pretend to verify data bytes.
    (tmp_path / "daily-v1" / "data.bin").write_bytes(b"changed data")
    snapshot.validate_metadata()
    (tmp_path / "daily-v1" / "manifest.yml").write_text("version: 2\n")
    with pytest.raises(PublishedAssetContractError, match="changed"):
        snapshot.validate_metadata()


@pytest.mark.parametrize("mutation", ["alias", "manifest", "contract"])
def test_capture_rejects_drift_between_individual_resolutions(tmp_path, monkeypatch, mutation):
    contract = fixture(tmp_path)
    original = PublishedAssetContract.asset
    calls = []

    def change(self, key):
        ref = original(self, key)
        calls.append(key)
        if len(calls) == 1:
            if mutation == "alias":
                alias = tmp_path / "daily-current"
                replacement = tmp_path / "daily-v2"
                replacement.mkdir()
                (replacement / "manifest.yml").write_text("version: 2\n")
                alias.unlink()
                alias.symlink_to(replacement, target_is_directory=True)
            elif mutation == "manifest":
                ref.manifest_path.write_text("version: 2\n")
            else:
                contract.path.write_text(contract.path.read_text() + " ")
        return ref

    monkeypatch.setattr(PublishedAssetContract, "asset", change)
    with pytest.raises(PublishedAssetContractError, match="changed"):
        contract.capture(["daily", "calendar"])


def test_require_assets_preserves_order_and_rejects_drift(tmp_path):
    contract = fixture(tmp_path)
    assert [ref.key for ref in contract.require_assets(["calendar", "daily", "calendar"])] == [
        "calendar",
        "daily",
        "calendar",
    ]
    assert contract.require_assets([]) == ()


def test_contract_capture_cli_emits_only_provenance(tmp_path, capsys):
    from quant_market_data_platform.cli import main

    contract = fixture(tmp_path)
    assert (
        main(
            [
                "contract",
                "capture",
                "--artifacts-root",
                str(tmp_path),
                "--current-contract",
                str(contract.path),
                "--asset",
                "daily",
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["data_content_verified"] is False
    assert receipt["assets"][0]["resolved_path"] == str(tmp_path / "daily-v1")


@pytest.mark.parametrize("kind", ["contract", "manifest"])
def test_parsed_metadata_and_digest_are_from_same_read(tmp_path, monkeypatch, kind):
    import hashlib

    contract = fixture(tmp_path)
    path = contract.path if kind == "contract" else tmp_path / "daily-v1" / "manifest.yml"
    before = path.read_bytes()
    original = Path.read_text

    def replace_after_read(self, *args, **kwargs):
        content = original(self, *args, **kwargs)
        if self == path:
            self.write_bytes(before + b"\n")
        return content

    monkeypatch.setattr(Path, "read_text", replace_after_read)
    ref = PublishedAssetContract.load(contract.path, artifacts_root=tmp_path)
    digest = ref.contract_sha256 if kind == "contract" else ref.asset("daily").manifest_sha256
    assert digest == hashlib.sha256(before).hexdigest()
