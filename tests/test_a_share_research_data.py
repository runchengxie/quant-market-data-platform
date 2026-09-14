from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from market_data_platform.research_views.a_share_research_data import (
    AShareResearchAssets,
    load_a_share_research_daily,
    load_a_share_research_instruments,
    resolve_a_share_research_assets,
)


def test_load_a_share_research_frames_from_published_paths(tmp_path: Path) -> None:
    daily_clean = tmp_path / "daily_clean"
    data_dir = daily_clean / "data"
    data_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "trade_date": ["2024-01-02", "2024-01-03"],
            "symbol": ["000001.SZ", "000001.SZ"],
            "close": [10.0, 10.5],
            "turnover_rate": [1.2, 1.3],
            "total_mv": [100.0, 101.0],
        }
    ).to_parquet(data_dir / "part.parquet", index=False)
    instruments = tmp_path / "instruments.parquet"
    pd.DataFrame(
        {
            "symbol": ["000001.SZ"],
            "name": ["示例"],
            "industry": ["银行"],
        }
    ).to_parquet(instruments, index=False)
    assets = AShareResearchAssets(
        data_root=tmp_path,
        current_contract=tmp_path / "a_share_current.json",
        daily_clean=daily_clean,
        instruments=instruments,
        daily_as_of="20240103",
    )

    daily = load_a_share_research_daily(assets, end_date="20240102")
    instrument_frame = load_a_share_research_instruments(assets)

    assert daily["trade_date"].tolist() == [pd.Timestamp("2024-01-02")]
    assert daily["symbol"].tolist() == ["000001.SZ"]
    assert instrument_frame.loc[0, "industry_name"] == "银行"


def test_resolve_a_share_research_assets_uses_current_contract(monkeypatch, tmp_path: Path) -> None:
    daily = SimpleNamespace(resolved_path=tmp_path / "daily", as_of="2024-01-31")
    instruments = SimpleNamespace(resolved_path=tmp_path / "instruments.parquet")
    contract = SimpleNamespace(
        artifacts_root=tmp_path,
        path=tmp_path / "metadata/current_assets/a_share_current.json",
        require_assets=lambda keys: (daily, instruments),
    )

    def fake_load_current(data_root, *, market):
        assert data_root == tmp_path
        assert market == "a_share"
        return contract

    monkeypatch.setattr(
        "market_data_platform.research_views.a_share_research_data.PublishedAssetContract.load_current",
        fake_load_current,
    )

    resolved = resolve_a_share_research_assets(tmp_path)

    assert resolved.daily_clean == daily.resolved_path
    assert resolved.instruments == instruments.resolved_path
    assert resolved.daily_as_of == "20240131"
