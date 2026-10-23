import importlib.util
from pathlib import Path

import pandas as pd
import pytest
import yaml

_PATH = Path(__file__).parents[1] / "scripts/operations/reconcile_trade_date_manifest.py"
_SPEC = importlib.util.spec_from_file_location("reconcile_trade_date_manifest", _PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
reconcile_manifest = _MODULE.reconcile_manifest


def test_reconcile_manifest_uses_partitions_as_source_of_truth(tmp_path: Path) -> None:
    asset = tmp_path / "asset"
    for date, value in (("20260907", 1), ("20260908", 2)):
        data = asset / "data" / f"trade_date={date}"
        data.mkdir(parents=True)
        pd.DataFrame({"trade_date": [date], "ts_code": [f"{value:06d}.SZ"]}).to_parquet(
            data / "part.parquet", index=False
        )
    (asset / "manifest.yml").write_text(
        yaml.safe_dump({"status": "completed", "totals": {"rows": 0, "files": 0}}),
        encoding="utf-8",
    )

    result = reconcile_manifest(asset, apply=True)

    assert result["status"] == "reconciled"
    manifest = yaml.safe_load((asset / "manifest.yml").read_text(encoding="utf-8"))
    assert manifest["totals"] == {"rows": 2, "files": 2, "symbols": 2}
    assert manifest["stored_coverage"]["partition_count"] == 2
    assert "written_trade_dates" not in manifest


def test_reconcile_preserves_request_and_run_receipts(tmp_path: Path) -> None:
    asset = tmp_path / "asset"
    data = asset / "data" / "trade_date=20140102"
    data.mkdir(parents=True)
    pd.DataFrame({"trade_date": ["20140102"], "ts_code": ["000001.SZ"]}).to_parquet(
        data / "part.parquet"
    )
    original = {
        "status": "completed",
        "query": {"start_date": "20261023"},
        "written_trade_dates": [],
        "skipped_trade_dates": ["20261023"],
        "empty_trade_dates": [],
        "run_totals": {"files": 0},
    }
    (asset / "manifest.yml").write_text(yaml.safe_dump(original))
    reconcile_manifest(asset, apply=True)
    actual = yaml.safe_load((asset / "manifest.yml").read_text())
    for key, value in original.items():
        assert actual[key] == value
    assert actual["stored_coverage"]["start_date"] == "20140102"


def test_reconcile_refuses_changed_inventory(tmp_path: Path, monkeypatch) -> None:
    import pytest

    asset = tmp_path / "asset"
    data = asset / "data" / "trade_date=20140102"
    data.mkdir(parents=True)
    partition = data / "part.parquet"
    pd.DataFrame({"trade_date": ["20140102"]}).to_parquet(partition)
    manifest_path = asset / "manifest.yml"
    original_text = yaml.safe_dump({"status": "completed"})
    manifest_path.write_text(original_text)
    original_inventory = _MODULE.trade_date_inventory
    calls = 0

    def changing_inventory(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            partition.touch()
        return original_inventory(path)

    monkeypatch.setattr(_MODULE, "trade_date_inventory", changing_inventory)
    with pytest.raises(_MODULE.ManifestReconciliationError, match="changed during"):
        reconcile_manifest(asset, apply=True)
    assert manifest_path.read_text() == original_text


class _FailingWriter:
    def __init__(self, stream):
        self.stream = stream

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, value):
        self.stream.write(value[:3])
        raise OSError("injected write failure")


def _reconciliation_asset(tmp_path):
    data = tmp_path / "data" / "trade_date=20140102"
    data.mkdir(parents=True)
    pd.DataFrame({"trade_date": ["20140102"]}).to_parquet(data / "part.parquet")
    manifest = tmp_path / "manifest.yml"
    manifest.write_bytes(b'status: completed\nquery: {start_date: "20261023"}\n')
    manifest.chmod(0o640)
    return manifest


@pytest.mark.parametrize("failure", ["write", "fsync", "replace"])
def test_atomic_reconcile_preserves_old_manifest_on_precommit_failure(
    tmp_path, monkeypatch, failure
):
    import io
    import os

    manifest = _reconciliation_asset(tmp_path)
    original = manifest.read_bytes()
    if failure == "write":
        original_open = io.open

        def failing_open(file, mode="r", *args, **kwargs):
            stream = original_open(file, mode, *args, **kwargs)
            return _FailingWriter(stream) if "w" in mode else stream

        monkeypatch.setattr(io, "open", failing_open)
    else:

        def fail(*args):
            raise OSError(f"injected {failure} failure")

        monkeypatch.setattr(os, failure, fail)
    with pytest.raises(OSError, match="injected"):
        reconcile_manifest(tmp_path, apply=True)
    assert manifest.read_bytes() == original
    assert manifest.stat().st_mode & 0o777 == 0o640
    assert sorted(path.name for path in tmp_path.iterdir()) == ["data", "manifest.yml"]


def test_atomic_reconcile_preserves_manifest_mode(tmp_path):
    manifest = _reconciliation_asset(tmp_path)
    reconcile_manifest(tmp_path, apply=True)
    assert manifest.stat().st_mode & 0o777 == 0o640
    assert yaml.safe_load(manifest.read_text())["stored_coverage"]["start_date"] == "20140102"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["data", "manifest.yml"]


def test_directory_fsync_failure_reports_committed_manifest(tmp_path, monkeypatch):
    import os

    manifest = _reconciliation_asset(tmp_path)
    original_fsync = os.fsync
    calls = 0

    def fail_directory_sync(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected directory sync failure")
        return original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_directory_sync)
    with pytest.raises(_MODULE.ManifestReconciliationError, match="manifest replaced.*unconfirmed"):
        reconcile_manifest(tmp_path, apply=True)
    assert yaml.safe_load(manifest.read_text())["stored_coverage"]["start_date"] == "20140102"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["data", "manifest.yml"]


@pytest.mark.parametrize("mutation", ["manifest", "partition"])
def test_atomic_publication_rechecks_inputs_after_staging(tmp_path, monkeypatch, mutation):
    import os

    manifest = _reconciliation_asset(tmp_path)
    original = manifest.read_bytes()
    original_fsync = os.fsync

    def mutate_after_staging(descriptor):
        original_fsync(descriptor)
        if mutation == "manifest":
            manifest.write_bytes(b"status: externally-updated\n")
        else:
            next((tmp_path / "data").rglob("*.parquet")).touch()

    monkeypatch.setattr(os, "fsync", mutate_after_staging)
    with pytest.raises(_MODULE.ManifestReconciliationError, match="changed before atomic"):
        reconcile_manifest(tmp_path, apply=True)
    expected = b"status: externally-updated\n" if mutation == "manifest" else original
    assert manifest.read_bytes() == expected
    assert sorted(path.name for path in tmp_path.iterdir()) == ["data", "manifest.yml"]
