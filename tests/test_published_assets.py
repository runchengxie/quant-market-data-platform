from __future__ import annotations

import json
import re
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytest
import yaml

from market_data_platform.integrations.qlib import QlibPublishedAssetAdapter
from market_data_platform.published_assets import (
    PublishedAssetContract,
    PublishedAssetPathError,
)
from market_data_platform.published_frames import (
    ParquetFrameMapping,
    PITUniverseMapping,
    PublishedFramePlan,
    PublishedFrameSchemaError,
    PublishedParquetFrameReader,
    TradingCalendarMapping,
)


def _write_asset(
    root: Path,
    key: str,
    frame: pd.DataFrame,
    *,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    asset = root / "assets" / key / f"{key}_20240104"
    data = asset / "data"
    data.mkdir(parents=True)
    frame.to_parquet(data / "part.parquet", index=False)
    manifest_path = asset / "manifest.yml"
    manifest_payload = manifest or {
        "schema_version": f"test.{key}.v1",
        "dataset": key,
        "provider": "synthetic",
        "status": "completed",
        "semantics": {"point_in_time": key == "universe"},
        "lineage": {"fixture": "synthetic", "source_revision": 1},
        "totals": {"rows": len(frame)},
    }
    manifest_path.write_text(
        yaml.safe_dump(manifest_payload, sort_keys=False),
        encoding="utf-8",
    )
    return {
        "alias_path": str(asset),
        "resolved_path": str(asset),
        "manifest_path": str(manifest_path),
        "exists": True,
        "as_of": "20240104",
        # This summary is deliberately incomplete. Readers must reload the full manifest.
        "manifest": {"dataset": key},
    }


def _write_contract(root: Path, assets: dict[str, dict[str, Any]]) -> Path:
    path = root / "metadata" / "current_assets" / "a_share_current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "contract": {
                    "name": "a_share_current",
                    "market": "a_share",
                    "provider": "tushare",
                    "version": 1,
                    "artifacts_root": str(root.resolve()),
                    "target_date": "20240104",
                },
                "assets": assets,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _synthetic_contract(root: Path) -> PublishedAssetContract:
    features = pd.DataFrame(
        {
            "trade_date": [
                "2024-01-02",
                "2024-01-02",
                "2024-01-03",
                "2024-01-03",
                "2024-01-04",
                "2024-01-04",
            ],
            "symbol": ["A", "B", "A", "B", "A", "B"],
            "factor_raw": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )
    calendar = pd.DataFrame(
        {
            "cal_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "is_open": [1, 0, 1],
        }
    )
    universe = pd.DataFrame(
        {
            "trade_date": [
                "2024-01-02",
                "2024-01-02",
                "2024-01-03",
                "2024-01-03",
                "2024-01-04",
                "2024-01-04",
            ],
            "symbol": ["A", "B", "A", "B", "A", "B"],
            "selected": [True, True, True, True, False, True],
        }
    )
    assets = {
        "features": _write_asset(root, "features", features),
        "trade_cal": _write_asset(root, "trade_cal", calendar),
        "universe": _write_asset(root, "universe", universe),
    }
    _write_contract(root, assets)
    return PublishedAssetContract.load_current(root, market="a_share")


def _frame_plan() -> PublishedFramePlan:
    return PublishedFramePlan(
        frames=(
            ParquetFrameMapping(
                asset_key="features",
                relative_path="data",
                datetime_column="trade_date",
                instrument_column="symbol",
                columns={"factor_raw": "factor"},
                column_group="feature",
            ),
        ),
        calendar=TradingCalendarMapping(
            asset_key="trade_cal",
            relative_path="data",
            datetime_column="cal_date",
            open_column="is_open",
            open_values=(1,),
        ),
        universe=PITUniverseMapping(
            asset_key="universe",
            relative_path="data",
            datetime_column="trade_date",
            instrument_column="symbol",
            membership_column="selected",
            included_values=(True,),
        ),
    )


def test_contract_loads_full_manifest_and_exposes_stable_provenance(tmp_path: Path) -> None:
    root = tmp_path / "market-data"
    entry = _write_asset(
        root,
        "pit_fundamentals",
        pd.DataFrame(
            {
                "trade_date": ["2024-01-02"],
                "symbol": ["A"],
                "value": [1.0],
            }
        ),
        manifest={
            "schema_version": "test.pit_fundamentals.v1",
            "dataset": "pit_fundamentals",
            "provider": "synthetic",
            "semantics": {
                "point_in_time": True,
                "available_date_column": "trade_date",
            },
            "lineage": {"normalized_source_sha256": "a" * 64},
        },
    )
    _write_contract(root, {"pit_fundamentals": entry})

    contract = PublishedAssetContract.load_current(root, market="a_share")
    asset = contract.asset("pit_fundamentals")

    assert asset.manifest["semantics"]["point_in_time"] is True
    assert asset.lineage["normalized_source_sha256"] == "a" * 64
    assert re.fullmatch(r"[0-9a-f]{64}", contract.contract_sha256)
    assert re.fullmatch(r"[0-9a-f]{64}", asset.manifest_sha256)
    assert re.fullmatch(r"[0-9a-f]{64}", asset.content_fingerprint)
    assert json.loads(json.dumps(asset.provenance_dict()))["asset_key"] == "pit_fundamentals"
    with pytest.raises(TypeError):
        cast(dict[str, Any], asset.manifest)["dataset"] = "changed"

    original_content_fingerprint = asset.content_fingerprint
    asset.manifest_path.write_text(
        yaml.safe_dump(
            {
                "lineage": {"normalized_source_sha256": "a" * 64},
                "semantics": {
                    "available_date_column": "trade_date",
                    "point_in_time": True,
                },
                "provider": "synthetic",
                "dataset": "pit_fundamentals",
                "schema_version": "test.pit_fundamentals.v1",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    reformatted = contract.asset("pit_fundamentals")
    assert reformatted.content_fingerprint == original_content_fingerprint
    assert reformatted.manifest_sha256 != asset.manifest_sha256


def test_asset_resolution_rejects_symlink_outside_artifacts_root(tmp_path: Path) -> None:
    root = tmp_path / "market-data"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.yml").write_text("dataset: escaped\n", encoding="utf-8")
    alias = root / "assets" / "escaped"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(outside, target_is_directory=True)
    contract_path = _write_contract(
        root,
        {
            "escaped": {
                "alias_path": str(alias),
                "resolved_path": str(outside),
                "manifest_path": str(outside / "manifest.yml"),
                "exists": True,
            }
        },
    )

    contract = PublishedAssetContract.load(contract_path, artifacts_root=root)
    with pytest.raises(PublishedAssetPathError, match="escapes artifacts root"):
        contract.asset("escaped")


def test_single_file_asset_cannot_resolve_sibling_paths(tmp_path: Path) -> None:
    root = tmp_path / "market-data"
    asset = root / "assets" / "single.parquet"
    asset.parent.mkdir(parents=True)
    pd.DataFrame({"value": [1]}).to_parquet(asset, index=False)
    sibling = asset.parent / "sibling.parquet"
    pd.DataFrame({"value": [2]}).to_parquet(sibling, index=False)
    manifest_path = asset.with_name("single.manifest.yml")
    manifest_path.write_text("dataset: single\nstatus: completed\n", encoding="utf-8")
    contract_path = _write_contract(
        root,
        {
            "single": {
                "alias_path": str(asset),
                "resolved_path": str(asset),
                "manifest_path": str(manifest_path),
                "exists": True,
            }
        },
    )

    published = PublishedAssetContract.load(contract_path, artifacts_root=root).asset("single")

    assert published.resolve_data_path() == asset.resolve()
    with pytest.raises(PublishedAssetPathError, match="does not contain relative data paths"):
        published.resolve_data_path("sibling.parquet")


def test_synthetic_pit_calendar_and_qlib_adapter_frames_are_identical(tmp_path: Path) -> None:
    contract = _synthetic_contract(tmp_path / "market-data")
    plan = _frame_plan()
    reader = PublishedParquetFrameReader(contract, plan)
    adapter = QlibPublishedAssetAdapter(contract, plan)

    native = reader.load()
    adapted = adapter.load()
    pd.testing.assert_frame_equal(adapted, native)

    expected_index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2024-01-02"), "A"),
            (pd.Timestamp("2024-01-02"), "B"),
            (pd.Timestamp("2024-01-04"), "B"),
        ],
        names=["datetime", "instrument"],
    )
    assert native.index.equals(expected_index)
    assert native.columns.tolist() == [("feature", "factor")]
    assert native[("feature", "factor")].tolist() == [1.0, 2.0, 6.0]
    pd.testing.assert_frame_equal(
        adapter.load(instruments=["B"], start_time="2024-01-04"),
        native.loc[(slice(pd.Timestamp("2024-01-04"), None), ["B"]), :],
    )
    pd.testing.assert_frame_equal(
        adapter.load(instruments={"B": [("2024-01-04", "2024-01-04")]}),
        native.loc[(slice(pd.Timestamp("2024-01-04"), None), ["B"]), :],
    )

    metadata = adapter.dataset_metadata
    assert metadata["backend"]["name"] == "qlib"
    assert metadata["source_backend"]["name"] == "market_data_platform.published_parquet"
    assert [source["asset_key"] for source in metadata["sources"]] == [
        "features",
        "trade_cal",
        "universe",
    ]
    assert metadata["mapping"]["universe"]["point_in_time"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", metadata["content_fingerprint"])
    json.dumps(metadata)


def test_frame_mapping_never_guesses_missing_columns(tmp_path: Path) -> None:
    contract = _synthetic_contract(tmp_path / "market-data")
    plan = PublishedFramePlan(
        frames=(
            ParquetFrameMapping(
                asset_key="features",
                relative_path="data",
                datetime_column="trade_date",
                instrument_column="symbol",
                columns={"not_a_real_column": "factor"},
            ),
        )
    )

    with pytest.raises(PublishedFrameSchemaError, match="explicit Parquet columns"):
        PublishedParquetFrameReader(contract, plan).load()


def test_lazy_runtime_wrapper_is_a_real_qlib_data_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDataLoader:
        pass

    qlib = types.ModuleType("qlib")
    qlib.__path__ = []  # type: ignore[attr-defined]
    qlib_data = types.ModuleType("qlib.data")
    qlib_data.__path__ = []  # type: ignore[attr-defined]
    qlib_dataset = types.ModuleType("qlib.data.dataset")
    qlib_dataset.__path__ = []  # type: ignore[attr-defined]
    qlib_loader: Any = types.ModuleType("qlib.data.dataset.loader")
    qlib_loader.DataLoader = FakeDataLoader
    for name, module in {
        "qlib": qlib,
        "qlib.data": qlib_data,
        "qlib.data.dataset": qlib_dataset,
        "qlib.data.dataset.loader": qlib_loader,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    runtime_name = "market_data_platform.integrations._qlib_runtime"
    monkeypatch.delitem(sys.modules, runtime_name, raising=False)

    contract = _synthetic_contract(tmp_path / "market-data")
    adapter = QlibPublishedAssetAdapter(contract, _frame_plan())
    loader = adapter.as_data_loader()

    assert isinstance(loader, FakeDataLoader)
    pd.testing.assert_frame_equal(loader.load(), adapter.load())
    assert loader.dataset_metadata["backend"]["name"] == "qlib"
    sys.modules.pop(runtime_name, None)


def test_installed_qlib_runtime_accepts_the_adapter(tmp_path: Path) -> None:
    qlib_loader_module = pytest.importorskip("qlib.data.dataset.loader")
    contract = _synthetic_contract(tmp_path / "market-data")
    adapter = QlibPublishedAssetAdapter(contract, _frame_plan())

    loader = adapter.as_data_loader()

    assert isinstance(loader, qlib_loader_module.DataLoader)
    pd.testing.assert_frame_equal(loader.load(), adapter.load())


def test_core_and_lazy_adapter_import_when_qlib_is_missing() -> None:
    script = """
import importlib.abc
import sys

class BlockQlib(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'qlib' or fullname.startswith('qlib.'):
            raise ModuleNotFoundError("blocked optional dependency", name=fullname)
        return None

sys.meta_path.insert(0, BlockQlib())
import market_data_platform
from market_data_platform.integrations.qlib import (
    QlibIntegrationUnavailableError,
    QlibPublishedAssetAdapter,
)
assert 'qlib' not in sys.modules
adapter = object.__new__(QlibPublishedAssetAdapter)
try:
    adapter.as_data_loader()
except QlibIntegrationUnavailableError:
    pass
else:
    raise AssertionError('missing Qlib did not produce the optional-dependency error')
"""
    subprocess.run([sys.executable, "-c", script], check=True, text=True, capture_output=True)
