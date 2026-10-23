import importlib.util
from pathlib import Path

import pandas as pd
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
