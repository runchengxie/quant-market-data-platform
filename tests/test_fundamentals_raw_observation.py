from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_archive_tushare_fundamentals_vintage import _child, archive


def _args(root: Path):
    return archive._prepare_args(
        archive.build_parser().parse_args(
            [
                "--artifacts-root",
                str(root),
                "--snapshot-date",
                "20261024",
                "--api-url",
                "https://api.tushare.pro",
                "--archive-mode",
                "raw",
                "--observation-frequency",
                "weekly",
            ]
        )
    )


def test_raw_capture_and_reentry_verify_all_children(tmp_path, monkeypatch):
    args = _args(tmp_path)
    calls = []

    def capture(args, root, dataset):
        calls.append(dataset)
        return _child(root, f"raw/{dataset}")

    monkeypatch.setattr(archive, "_archive_raw_dataset", capture)
    monkeypatch.setattr(archive, "_archive_pit", lambda *a: pytest.fail("PIT called"))
    monkeypatch.setattr(
        archive, "_archive_normalized_dataset", lambda *a: pytest.fail("normalize called")
    )
    result = archive.archive_vintage(args)
    root = archive.observation_root(tmp_path, args.snapshot_date)
    assert not archive.snapshot_root(tmp_path, args.snapshot_date).exists()
    assert result["archive_mode"] == "raw"
    assert "revision_safety" not in result
    assert result["publication"]["latest_switched"] is False
    assert len(calls) == 4
    assert archive.archive_vintage(args) == result
    assert len(calls) == 4
    args.observation_frequency = "daily"
    with pytest.raises(RuntimeError, match="identity"):
        archive.archive_vintage(args)
    args.observation_frequency = "weekly"
    (root / "raw/income/data/part.bin").write_bytes(b"tampered")
    with pytest.raises(RuntimeError):
        archive.archive_vintage(args)


def test_raw_partial_failure_never_seals(tmp_path, monkeypatch):
    args = _args(tmp_path)

    def capture(args, root, dataset):
        if dataset == "cashflow":
            raise RuntimeError("interrupted")
        return _child(root, f"raw/{dataset}")

    monkeypatch.setattr(archive, "_archive_raw_dataset", capture)
    with pytest.raises(RuntimeError, match="interrupted"):
        archive.archive_vintage(args)
    root = archive.observation_root(tmp_path, args.snapshot_date)
    assert not (root / "OBSERVATION_SEALED.json").exists()
    assert json.loads((root / "archive_state.json").read_text())["status"] == "failed"


def test_raw_requires_four_datasets_before_writes(tmp_path):
    args = _args(tmp_path)
    args.datasets = ("income",)
    with pytest.raises(ValueError, match="four"):
        archive.archive_vintage(args)
    assert not archive.observation_root(tmp_path, args.snapshot_date).exists()


@pytest.mark.parametrize(
    "change", ["query", "endpoint", "mode", "missing_child", "receipt", "symlink"]
)
def test_raw_rejects_changed_scope_and_receipts(tmp_path, monkeypatch, change):
    args = _args(tmp_path)
    monkeypatch.setattr(
        archive, "_archive_raw_dataset", lambda args, root, dataset: _child(root, f"raw/{dataset}")
    )
    archive.archive_vintage(args)
    root = archive.observation_root(tmp_path, args.snapshot_date)
    monkeypatch.setattr(archive, "_archive_raw_dataset", lambda *a: pytest.fail("provider called"))
    if change == "query":
        args.start_date = "20200101"
    elif change == "endpoint":
        args.api_url = "https://different.example.com"
    elif change == "mode":
        # A raw receipt is not a full vintage seal, even if copied to its namespace.
        import shutil

        full = archive.snapshot_root(tmp_path, args.snapshot_date)
        shutil.copytree(root, full)
        (full / "OBSERVATION_SEALED.json").rename(full / "SEALED.json")
        with pytest.raises(RuntimeError):
            archive.verify_sealed_snapshot(full)
        return
    elif change == "missing_child":
        (root / "raw/cashflow/manifest.yml").unlink()
    elif change == "receipt":
        import yaml

        from quant_market_data_platform.providers.tushare_a_share_fundamentals_support import (
            file_sha256,
        )

        manifest = yaml.safe_load((root / "manifest.yml").read_text())
        manifest["children"].pop()
        (root / "manifest.yml").write_text(yaml.safe_dump(manifest))
        seal = json.loads((root / "OBSERVATION_SEALED.json").read_text())
        seal["manifest_sha256"] = file_sha256(root / "manifest.yml")
        (root / "OBSERVATION_SEALED.json").write_text(json.dumps(seal))
    else:
        child = root / "raw/income"
        child.rename(tmp_path / "outside")
        child.symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises((RuntimeError, FileNotFoundError, ValueError)):
        archive.archive_vintage(args)


def test_documented_offline_build_arguments_parse():
    from quant_market_data_platform.cli import build_parser

    parser = build_parser()
    for dataset in archive.DEFAULT_DATASETS:
        args = parser.parse_args(
            [
                "tushare",
                "normalize-a-share-fundamentals",
                "--dataset",
                dataset,
                "--raw-dir",
                f"/data/observation/raw/{dataset}",
                "--out-dir",
                f"/data/derived/normalized/{dataset}",
            ]
        )
        assert args.dataset == dataset
    command = ["tushare", "build-a-share-fundamentals-pit", "--out-dir", "/data/derived/pit"]
    for dataset in archive.DEFAULT_DATASETS:
        command.extend(["--normalized-dir", f"/data/derived/normalized/{dataset}"])
    for mapping in [
        "revenue=revenue",
        "total_assets=total_assets",
        "n_cashflow_act=n_cashflow_act",
        "roe=roe",
    ]:
        command.extend(["--field-map", mapping])
    assert len(parser.parse_args(command).normalized_dirs) == 4


def test_raw_incomplete_or_tampered_download_cannot_complete(tmp_path, monkeypatch):
    args = _args(tmp_path)

    def capture(args, root, dataset):
        path = _child(root, f"raw/{dataset}")
        if dataset == "fina_indicator":
            (path / "data/part.bin").write_bytes(b"changed after download")
        return path

    monkeypatch.setattr(archive, "_archive_raw_dataset", capture)
    with pytest.raises(RuntimeError):
        archive.archive_vintage(args)
    root = archive.observation_root(tmp_path, args.snapshot_date)
    assert not (root / "OBSERVATION_SEALED.json").exists()
    args.end_date = "20261023"
    monkeypatch.setattr(archive, "_archive_raw_dataset", lambda *a: pytest.fail("provider called"))
    with pytest.raises(RuntimeError, match="identity"):
        archive.archive_vintage(args)


@pytest.mark.parametrize("component", ["root", "raw", "child"])
def test_raw_rejects_output_symlink_before_provider_or_attempt(tmp_path, monkeypatch, component):
    args = _args(tmp_path)
    root = archive.observation_root(tmp_path, args.snapshot_date)
    target = tmp_path / "unrelated"
    target.mkdir()
    link = (
        root if component == "root" else root / "raw" if component == "raw" else root / "raw/income"
    )
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(archive, "_archive_raw_dataset", lambda *a: pytest.fail("provider called"))
    with pytest.raises(RuntimeError, match="Unsafe"):
        archive.archive_vintage(args)
    assert list(target.iterdir()) == []
    assert not (root / "attempts").exists()
