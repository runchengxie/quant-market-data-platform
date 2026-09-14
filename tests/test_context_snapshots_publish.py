from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from market_data_platform.context.publish import publish_context_assets
from market_data_platform.context.snapshots import seal_context_snapshot
from market_data_platform.published_assets import PublishedAssetContract


def test_seal_context_snapshot_is_immutable_and_hashed(tmp_path: Path):
    snapshot = seal_context_snapshot(
        tmp_path,
        provider="tushare",
        dataset="shibor",
        source_locator="tushare://shibor",
        retrieved_at=datetime(2026, 8, 28, 12, tzinfo=UTC),
        body=b"date,on,3m\n20260828,1.2,1.4\n",
        parser_version="tushare-context.v1",
        request_metadata={"start_date": "20260828", "end_date": "20260828"},
    )
    receipt = json.loads((snapshot / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["provider"] == "tushare"
    assert receipt["dataset"] == "shibor"
    assert receipt["byte_count"] > 0
    assert len(receipt["sha256"]) == 64
    assert (snapshot / "raw.bin").is_file()
    assert (snapshot / "manifest.seal.json").is_file()

    with pytest.raises(FileExistsError):
        seal_context_snapshot(
            tmp_path,
            provider="tushare",
            dataset="shibor",
            source_locator="tushare://shibor",
            retrieved_at=datetime(2026, 8, 28, 12, tzinfo=UTC),
            body=b"changed",
            parser_version="tushare-context.v1",
            request_metadata={},
        )


def test_publish_context_assets_creates_composite_current_contract(tmp_path: Path):
    catalog = pd.DataFrame(
        [
            {
                "series_id": "rates.shibor_3m",
                "source_id": "tushare.shibor.3m",
                "provider": "tushare",
                "source_series_key": "3m",
                "name": "Shibor 3M",
                "family": "rates",
                "frequency": "daily",
                "unit": "percent",
                "seasonal_adjustment": "none",
                "value_semantics": "level",
                "revision_policy": "observed_vintage",
                "availability_policy": "source_release",
                "expected_release_lag": "same_day",
                "max_staleness": 10,
                "status": "active",
            }
        ]
    )
    observations = pd.DataFrame(
        [
            {
                "series_id": "rates.shibor_3m",
                "period_start": pd.Timestamp("2026-08-28", tz="UTC"),
                "period_end": pd.Timestamp("2026-08-28", tz="UTC"),
                "value": 1.4,
                "unit": "percent",
                "published_at": pd.Timestamp("2026-08-28T03:00:00Z"),
                "observed_at": pd.Timestamp("2026-08-28T03:00:00Z"),
                "ingested_at": pd.Timestamp("2026-08-28T04:00:00Z"),
                "source_retrieved_at": pd.Timestamp("2026-08-28T04:00:00Z"),
                "available_at": pd.Timestamp("2026-08-28T03:00:00Z"),
                "vintage_id": "20260828T040000Z",
                "revision_number": 0,
                "source_hash": "a" * 64,
                "revision_covered": True,
                "reconstructed": False,
            }
        ]
    )
    release_calendar = pd.DataFrame(
        [
            {
                "series_id": "rates.shibor_3m",
                "period_end": pd.Timestamp("2026-08-28", tz="UTC"),
                "published_at": pd.Timestamp("2026-08-28T03:00:00Z"),
                "provider": "tushare",
                "source_locator": "tushare://shibor",
            }
        ]
    )

    contract_path = publish_context_assets(
        tmp_path,
        catalog=catalog,
        observations=observations,
        pit=observations,
        release_calendar=release_calendar,
        as_of="20260828",
        lineage=[{"provider": "tushare", "dataset": "shibor", "sha256": "b" * 64}],
    )

    assert contract_path == tmp_path / "metadata/current_assets/cn_context_current.json"
    contract = PublishedAssetContract.load_current(tmp_path, market="cn_context")
    assert contract.provider == "composite"
    assert set(contract.asset_keys) == {
        "context_catalog",
        "context_observations",
        "context_pit",
        "context_release_calendar",
    }
    for key in contract.asset_keys:
        ref = contract.asset(key)
        assert ref.manifest["provider"] == "composite"
        assert ref.manifest["lineage"]
    assert not (
        tmp_path / "assets/context/cn/catalog/cn_context_catalog_latest.parquet"
    ).is_symlink()
    assert not (
        tmp_path / "assets/context/cn/normalized/cn_context_observations_latest"
    ).is_symlink()
