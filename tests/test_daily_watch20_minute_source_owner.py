from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from market_data_platform.research_views.daily_watch20_data import DailyWatch20Assets
from market_data_platform.research_views.daily_watch20_live_inputs import (
    DailyWatch20InputOptions,
    inspect_daily_watch20_input_availability,
)
from market_data_platform.research_views.daily_watch20_minute_source import (
    cached_source_partitions,
    minute_cache_delta,
    scan_daily_watch20_minute_sources,
)
from market_data_platform.research_views.daily_watch20_policy import DailyWatch20UniversePolicy


def _partition(root: Path, date: str, name: str = "part-000.parquet") -> Path:
    directory = root / f"trade_date={date}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(f"{root.name}:{date}:{name}".encode())
    return path


def _assets(tmp_path: Path, minute_root: Path, coverage: Path) -> DailyWatch20Assets:
    trade_cal = tmp_path / "trade_cal.parquet"
    pd.DataFrame(
        {"cal_date": ["2026-07-16", "2026-07-17", "2026-07-20"], "is_open": [1, 1, 1]}
    ).to_parquet(trade_cal, index=False)
    instruments = tmp_path / "instruments.parquet"
    pd.DataFrame(
        {
            "symbol": ["000001.SZ"],
            "name": ["x"],
            "industry": ["i"],
            "list_status": ["L"],
            "market": ["main"],
            "exchange": ["SZ"],
        }
    ).to_parquet(instruments, index=False)
    daily = tmp_path / "daily"
    daily.mkdir()
    contract = tmp_path / "current.json"
    contract.write_text("{}", encoding="utf-8")
    return DailyWatch20Assets(
        data_root=tmp_path,
        current_contract=contract,
        daily_clean=daily,
        instruments=instruments,
        trade_cal=trade_cal,
        minute_current=minute_root,
        minute_coverage=coverage,
        daily_as_of="20260716",
        minute_date_min="20260716",
        minute_date_max="20260717",
    )


def test_source_catalog_overlay_and_delta(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    _partition(canonical, "20260716")
    _partition(canonical, "20260717")
    sh = tmp_path / "sh"
    sz = tmp_path / "sz"
    _partition(sh, "20260717")
    _partition(sz, "20260717")
    coverage = tmp_path / "coverage.json"
    coverage.write_text("{}", encoding="utf-8")
    assets = _assets(tmp_path, canonical, coverage)

    catalog = scan_daily_watch20_minute_sources(
        assets,
        start_date="20260716",
        end_date="20260717",
        overlay_roots=(sh, sz),
    )
    assert catalog.dates == ("20260716", "20260717")
    assert len(catalog.partitions["20260717"].files) == 2
    assert {item["source_kind"] for item in catalog.partitions["20260717"].file_metadata} == {
        "overlay"
    }

    cached = catalog.partition_records()
    delta = minute_cache_delta(catalog, cached)
    assert not delta.rebuild_required
    assert delta.reused_dates == {"20260716", "20260717"}

    _partition(sh, "20260717", name="part-001.parquet")
    changed = scan_daily_watch20_minute_sources(
        assets,
        start_date="20260716",
        end_date="20260717",
        overlay_roots=(sh, sz),
    )
    assert minute_cache_delta(changed, cached).changed_dates == {"20260717"}
    assert cached_source_partitions({"source_partitions": cached}) == cached


def test_overlay_set_fails_closed(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    _partition(canonical, "20260717")
    sh = tmp_path / "sh"
    sz = tmp_path / "sz"
    _partition(sh, "20260717")
    sz.mkdir()
    coverage = tmp_path / "coverage.json"
    coverage.write_text("{}", encoding="utf-8")
    assets = _assets(tmp_path, canonical, coverage)
    with pytest.raises(RuntimeError, match="Incomplete minute overlay set"):
        scan_daily_watch20_minute_sources(
            assets,
            start_date="20260717",
            end_date="20260717",
            overlay_roots=(sh, sz),
        )


def test_availability_reports_currentness_without_release_policy(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    partition = _partition(canonical, "20260717")
    digest = hashlib.sha256(partition.read_bytes()).hexdigest()
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps(
            {
                "status": "passed",
                "quality_status": "passed",
                "coverage_status": "full_sh_sz",
                "daily": [
                    {
                        "date": "20260717",
                        "valid": True,
                        "market_scope": "SH_SZ",
                        "sh_sz_symbols": 1,
                        "content_sha256": digest,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assets = _assets(tmp_path, canonical, coverage)
    _, availability = inspect_daily_watch20_input_availability(
        assets,
        source_date="20260717",
        candidate_pool_mode="all_market",
    )
    assert availability.available
    assert availability.minute_source == "canonical"
    assert availability.daily_is_current is False
    assert availability.issues == ()

    _, structured = inspect_daily_watch20_input_availability(
        assets,
        DailyWatch20InputOptions(
            source_date="20260717",
            candidate_pool_mode="all_market",
        ),
    )
    assert structured == availability


def test_tushare_operational_availability_uses_embedded_daily_hash(
    tmp_path: Path,
) -> None:
    operational = tmp_path / "minute_1m_tushare_v1_20260717"
    partition = _partition(operational, "20260717")
    digest = hashlib.sha256(partition.read_bytes()).hexdigest()
    receipt = tmp_path / "operational.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "a_share.minute_tushare_operational_version.v1",
                "status": "published_operational_version",
                "provider": "tushare",
                "summary": {"market_scope": "SH_SZ_BJ"},
                "daily": [
                    {
                        "trade_date": "20260717",
                        "symbols": 1,
                        "content_sha256": digest,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    legacy_assets = _assets(tmp_path, operational, receipt)
    legacy_fields: dict[str, Any] = dict(legacy_assets.__dict__)
    legacy_fields["minute_dataset"] = "tushare"
    legacy_fields["minute_provider"] = "tushare"
    assets = DailyWatch20Assets(**legacy_fields)

    _, availability = inspect_daily_watch20_input_availability(
        assets,
        source_date="20260717",
        candidate_pool_mode="all_market",
    )

    assert availability.available
    assert availability.minute_source == "tushare_operational"


def test_universe_policy_identity_is_stable() -> None:
    left = DailyWatch20UniversePolicy(candidate_pool_mode="all_market", minute_lag_trade_days=1)
    right = DailyWatch20UniversePolicy(candidate_pool_mode="all_market", minute_lag_trade_days=1)
    assert left.policy_id == right.policy_id
    assert left.to_dict()["candidate_pool_policy_id"] == "daily_watch20.all_market.v1"


def test_minute_cache_delta_flags_out_of_window_cached_date_as_removed(tmp_path: Path) -> None:
    """Regression for the 1100-day window head-roll.

    When the rolling window advances one trading day, the oldest cached head
    (e.g. 20230717) falls *below* the new catalog.start_date (e.g. 20230718).
    It must be reported as `removed` so the derived cache can drop it
    incrementally. The previous `removed` filter excluded dates below the window,
    so the stale head was retained and the next build raised
    ValueError('minute cache/source catalog dates do not match').
    """
    canonical = tmp_path / "canonical"
    for date in ("20230718", "20230719"):
        _partition(canonical, date)
    coverage = tmp_path / "coverage.json"
    coverage.write_text("{}", encoding="utf-8")
    assets = _assets(tmp_path, canonical, coverage)
    catalog = scan_daily_watch20_minute_sources(assets, start_date="20230718", end_date="20230719")
    cached = catalog.partition_records()
    cached["20230717"] = {"fingerprint": "stale-head", "feature_rows": 1}
    delta = minute_cache_delta(catalog, cached)
    assert delta.removed_dates == {"20230717"}
    assert delta.changed_dates == set()
    assert delta.rebuild_required
