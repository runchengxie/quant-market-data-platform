from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from market_data_platform.research_views.daily_watch20_data import (
    next_open_trade_date,
    resolve_daily_watch20_assets,
)


def test_resolve_assets_uses_published_contract_and_minute_alias(tmp_path: Path) -> None:
    daily = tmp_path / "daily"
    instruments = tmp_path / "instruments.parquet"
    calendar = tmp_path / "calendar.parquet"
    minute = tmp_path / "assets" / "derived" / "a_share" / "minute_1m_v1"
    for directory in (daily, minute):
        directory.mkdir(parents=True)
    instruments.write_bytes(b"placeholder")
    calendar.write_bytes(b"placeholder")
    alias = tmp_path / "assets" / "derived" / "a_share" / "minute_1m"
    alias.symlink_to(minute.name, target_is_directory=True)
    contract = tmp_path / "metadata" / "current_assets" / "a_share_current.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(
        json.dumps(
            {
                "assets": {
                    "daily_clean": {"resolved_path": str(daily), "as_of": "2026-07-17"},
                    "instruments": {"resolved_path": str(instruments)},
                    "trade_cal": {"resolved_path": str(calendar)},
                }
            }
        ),
        encoding="utf-8",
    )
    assets = resolve_daily_watch20_assets(tmp_path, minute_dataset="legacy")
    assert assets.daily_clean == daily.resolve()
    assert assets.daily_as_of == "20260717"
    assert assets.minute_current == minute.resolve()
    assert assets.minute_dataset == "legacy"
    assert assets.minute_provider == "guan"


def test_resolve_assets_selects_validated_tushare_operational_alias(tmp_path: Path) -> None:
    daily = tmp_path / "daily"
    instruments = tmp_path / "instruments.parquet"
    calendar = tmp_path / "calendar.parquet"
    operational = tmp_path / "assets" / "derived" / "a_share" / "minute_1m_tushare_v1_20260727"
    for directory in (daily, operational):
        directory.mkdir(parents=True)
    instruments.write_bytes(b"placeholder")
    calendar.write_bytes(b"placeholder")
    alias = tmp_path / "assets" / "derived" / "a_share" / "minute_1m_tushare"
    alias.symlink_to(operational.name, target_is_directory=True)
    receipt = operational / "_operational_receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "a_share.minute_tushare_operational_version.v1",
                "status": "published_operational_version",
                "provider": "tushare",
                "output_dir": str(operational),
                "legacy_canonical_mutated": False,
                "summary": {
                    "date_min": "20220715",
                    "date_max": "20260727",
                    "market_scope": "SH_SZ_BJ",
                },
            }
        ),
        encoding="utf-8",
    )
    contract = tmp_path / "metadata" / "current_assets" / "a_share_current.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(
        json.dumps(
            {
                "assets": {
                    "daily_clean": {"resolved_path": str(daily), "as_of": "2026-07-27"},
                    "instruments": {"resolved_path": str(instruments)},
                    "trade_cal": {"resolved_path": str(calendar)},
                }
            }
        ),
        encoding="utf-8",
    )

    assets = resolve_daily_watch20_assets(tmp_path, minute_dataset="tushare")

    assert assets.minute_current == operational.resolve()
    assert assets.minute_coverage == receipt
    assert assets.minute_dataset == "tushare"
    assert assets.minute_provider == "tushare"
    assert assets.minute_date_min == "20220715"
    assert assets.minute_date_max == "20260727"


def test_resolve_assets_accepts_materialized_tushare_alias(tmp_path: Path) -> None:
    daily = tmp_path / "daily"
    instruments = tmp_path / "instruments.parquet"
    calendar = tmp_path / "calendar.parquet"
    version = tmp_path / "assets" / "derived" / "a_share" / "minute_1m_tushare_v1_20260728"
    alias = tmp_path / "assets" / "derived" / "a_share" / "minute_1m_tushare"
    for directory in (daily, version, alias):
        directory.mkdir(parents=True)
    instruments.write_bytes(b"placeholder")
    calendar.write_bytes(b"placeholder")
    receipt = alias / "_operational_receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "a_share.minute_tushare_operational_version.v1",
                "status": "published_operational_version",
                "provider": "tushare",
                "output_dir": str(version),
                "current_alias_mutated": False,
                "legacy_canonical_mutated": False,
                "summary": {"date_min": "20220715", "date_max": "20260728"},
            }
        ),
        encoding="utf-8",
    )
    contract = tmp_path / "metadata" / "current_assets" / "a_share_current.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(
        json.dumps(
            {
                "assets": {
                    "daily_clean": {"resolved_path": str(daily), "as_of": "20260728"},
                    "instruments": {"resolved_path": str(instruments)},
                    "trade_cal": {"resolved_path": str(calendar)},
                }
            }
        ),
        encoding="utf-8",
    )

    assets = resolve_daily_watch20_assets(tmp_path, minute_dataset="tushare")

    assert not alias.is_symlink()
    assert assets.minute_current == version.resolve()
    assert assets.minute_coverage == receipt
    assert assets.minute_date_min == "20220715"
    assert assets.minute_date_max == "20260728"


def test_resolve_assets_defaults_to_tushare_after_formal_launch(tmp_path: Path) -> None:
    daily = tmp_path / "daily"
    instruments = tmp_path / "instruments.parquet"
    calendar = tmp_path / "calendar.parquet"
    legacy = tmp_path / "assets" / "derived" / "a_share" / "minute_1m_v3"
    tushare = tmp_path / "assets" / "derived" / "a_share" / "minute_1m_tushare_v1_20260903"
    for directory in (daily, legacy, tushare):
        directory.mkdir(parents=True)
    instruments.write_bytes(b"placeholder")
    calendar.write_bytes(b"placeholder")
    (tmp_path / "assets" / "derived" / "a_share" / "minute_1m").symlink_to(
        legacy.name, target_is_directory=True
    )
    (tmp_path / "assets" / "derived" / "a_share" / "minute_1m_tushare").symlink_to(
        tushare.name, target_is_directory=True
    )
    (tushare / "_operational_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "a_share.minute_tushare_operational_version.v1",
                "status": "published_operational_version",
                "provider": "tushare",
                "output_dir": str(tushare),
                "legacy_canonical_mutated": False,
                "summary": {"date_min": "20220715", "date_max": "20260903"},
            }
        ),
        encoding="utf-8",
    )
    contract = tmp_path / "metadata" / "current_assets" / "a_share_current.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(
        json.dumps(
            {
                "assets": {
                    "daily_clean": {"resolved_path": str(daily), "as_of": "20260903"},
                    "instruments": {"resolved_path": str(instruments)},
                    "trade_cal": {"resolved_path": str(calendar)},
                }
            }
        ),
        encoding="utf-8",
    )

    assets = resolve_daily_watch20_assets(tmp_path)

    assert assets.minute_current == tushare.resolve()
    assert assets.minute_dataset == "tushare"
    assert assets.minute_provider == "tushare"
    assert assets.minute_date_min == "20220715"
    assert assets.minute_date_max == "20260903"


def test_next_open_trade_date_is_strictly_later() -> None:
    dates = pd.DatetimeIndex(["2026-07-17", "2026-07-20"])
    assert next_open_trade_date(dates, "2026-07-17") == pd.Timestamp("2026-07-20")
