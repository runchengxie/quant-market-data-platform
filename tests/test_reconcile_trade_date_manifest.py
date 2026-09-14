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
    assert manifest["written_trade_dates"] == ["20260907", "20260908"]
