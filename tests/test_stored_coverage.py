from pathlib import Path

import pandas as pd
import pytest
import yaml

from quant_market_data_platform.contract_health_part01 import _manifest_date
from quant_market_data_platform.manifest import load_manifest_summary
from quant_market_data_platform.providers.tushare_a_share_manifests import trade_date_manifest


def _partition(output: Path, day: str) -> Path:
    path = output / "data" / f"trade_date={day}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"trade_date": [day], "ts_code": ["000001.SZ"]}).to_parquet(path)
    return path


def _manifest(output: Path, *, skipped: bool = False) -> dict:
    context = {
        "dataset": "moneyflow",
        "api_name": "moneyflow",
        "start": "20261023",
        "end": "20261023",
        "requested_fields": None,
        "output_dir": output,
        "data_dir": output / "data",
        "interval_seconds": 0,
        "query_options": {},
        "api_url": None,
        "trade_dates": ["20261023"],
        "skip_existing": True,
    }
    partitions = {
        "rows": 0 if skipped else 1,
        "symbols": set() if skipped else {"000001.SZ"},
        "written_dates": [] if skipped else ["20261023"],
        "skipped_dates": ["20261023"] if skipped else [],
        "empty_dates": [],
    }
    return trade_date_manifest(context, partitions, {})


def _summary(output: Path, manifest: dict) -> dict:
    path = output / "manifest.yml"
    path.write_text(yaml.safe_dump(manifest))
    return load_manifest_summary(path)


@pytest.mark.parametrize("skipped", [False, True])
def test_incremental_manifest_proves_stored_extent_without_rewriting_query(tmp_path, skipped):
    _partition(tmp_path, "20140102")
    _partition(tmp_path, "20261023")
    manifest = _manifest(tmp_path, skipped=skipped)
    summary = _summary(tmp_path, manifest)
    assert manifest["query"]["start_date"] == "20261023"
    assert summary["query_start_date"] == "20261023"
    assert summary["stored_coverage"]["status"] == "verified"
    assert _manifest_date(summary, "start") == "20140102"
    assert manifest["run_totals"]["files"] == (0 if skipped else 1)


@pytest.mark.parametrize("damage", ["missing", "changed", "malformed"])
def test_stale_or_malformed_coverage_never_proves_history(tmp_path, damage):
    old = _partition(tmp_path, "20140102")
    _partition(tmp_path, "20261023")
    manifest = _manifest(tmp_path)
    if damage == "missing":
        old.unlink()
    elif damage == "changed":
        old.touch()
    else:
        manifest["stored_coverage"] = {"start_date": "19000101"}
    summary = _summary(tmp_path, manifest)
    assert summary["stored_coverage"]["status"] == "invalid"
    assert _manifest_date(summary, "start") == "20261023"


def test_legacy_manifest_remains_unproven(tmp_path):
    _partition(tmp_path, "20140102")
    summary = _summary(tmp_path, {"query": {"start_date": "20261023"}})
    assert summary["stored_coverage"]["status"] == "unknown"
    assert _manifest_date(summary, "start") == "20261023"


def test_new_writer_does_not_invent_missing_history(tmp_path):
    _partition(tmp_path, "20261023")
    summary = _summary(tmp_path, _manifest(tmp_path))
    assert _manifest_date(summary, "start") == "20261023"


def test_health_revalidates_persisted_contract_evidence(tmp_path):
    from quant_market_data_platform.contract_health_part01 import _asset_record

    old = _partition(tmp_path, "20140102")
    _partition(tmp_path, "20261023")
    summary = _summary(tmp_path, _manifest(tmp_path))
    old.unlink()
    record = _asset_record("moneyflow", {"manifest": summary})
    assert record["stored_coverage_status"] == "invalid"
    assert record["manifest_start_date"] == "20261023"


def test_health_reports_invalid_inventory_even_with_historical_request(tmp_path):
    from quant_market_data_platform.contract_health_part01 import (
        _asset_record,
        _build_quality_checks,
    )

    old = _partition(tmp_path, "20140102")
    manifest = _manifest(tmp_path)
    manifest["query"]["start_date"] = "20140101"
    summary = _summary(tmp_path, manifest)
    old.unlink()
    record = _asset_record("daily", {"manifest": summary, "exists": True})
    checks = _build_quality_checks(
        contract_exists=True,
        current_contract=tmp_path / "current.json",
        target_date=None,
        required_start_date="20140101",
        asset_records=[record],
    )
    assert any(check["check"] == "asset_stored_coverage_invalid" for check in checks)


def test_health_uses_proven_stored_end_for_freshness(tmp_path):
    from quant_market_data_platform.contract_health_part01 import _asset_record

    _partition(tmp_path, "20261022")
    summary = _summary(tmp_path, _manifest(tmp_path))
    record = _asset_record("daily", {"manifest": summary, "as_of": "20261023"})
    assert record["as_of"] == "20261022"


@pytest.mark.parametrize("damage", ["append", "remove", "change"])
def test_inventory_refuses_observed_concurrent_mutations(tmp_path, monkeypatch, damage):
    from quant_market_data_platform import stored_coverage

    partition = _partition(tmp_path, "20140102")
    original_stat = Path.stat
    changed = False

    def changing_stat(path, *args, **kwargs):
        nonlocal changed
        result = original_stat(path, *args, **kwargs)
        if path == partition and not changed and kwargs.get("follow_symlinks", True):
            changed = True
            if damage == "append":
                _partition(tmp_path, "20261023")
            elif damage == "remove":
                partition.unlink()
            else:
                partition.touch()
        return result

    monkeypatch.setattr(Path, "stat", changing_stat)
    with pytest.raises(ValueError, match="changed"):
        stored_coverage.trade_date_inventory(tmp_path / "data")


def test_inventory_rejects_symlink_subdirectories(tmp_path):
    from quant_market_data_platform.stored_coverage import trade_date_inventory

    _partition(tmp_path, "20261023")
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "data" / "trade_date=20140102").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        trade_date_inventory(tmp_path / "data")
