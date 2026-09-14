from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from market_data_platform.providers import tushare_a_share_reference as reference


class FakeReferenceClient:
    def stock_company(self, *, exchange: str, offset: int, limit: int) -> pd.DataFrame:
        if offset:
            return pd.DataFrame()
        return pd.DataFrame([{"ts_code": f"{exchange}.SH", "exchange": exchange}])


class FakeShareFloatClient:
    def share_float(
        self,
        *,
        ann_date: str,
        offset: int,
        limit: int,
    ) -> pd.DataFrame:
        rows = [{"ts_code": f"{index:06d}.SZ", "ann_date": ann_date} for index in range(6)]
        return pd.DataFrame(rows[offset : offset + limit])


def test_stock_company_download_uses_all_exchanges(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(reference, "get_tushare_client", lambda **_kwargs: FakeReferenceClient())

    summary = reference.download_raw_reference(
        reference.RawReferenceDownloadOptions(
            dataset="stock_company",
            out_dir=tmp_path,
            start_date="20260101",
            end_date="20260730",
        )
    )

    frame = pd.read_parquet(summary["path"])
    assert summary["rows"] == 3
    assert set(frame["exchange"]) == {"SSE", "SZSE", "BSE"}


def test_index_weight_daily_expands_latest_snapshot_over_open_dates(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    pd.DataFrame(
        [
            {
                "index_code": "000300.SH",
                "con_code": "000001.SZ",
                "trade_date": "20260102",
                "weight": 60.0,
            },
            {
                "index_code": "000300.SH",
                "con_code": "600000.SH",
                "trade_date": "20260102",
                "weight": 40.0,
            },
            {
                "index_code": "000300.SH",
                "con_code": "000001.SZ",
                "trade_date": "20260106",
                "weight": 30.0,
            },
            {
                "index_code": "000300.SH",
                "con_code": "600000.SH",
                "trade_date": "20260106",
                "weight": 70.0,
            },
        ]
    ).to_parquet(raw / "index_weight.parquet", index=False)
    calendar_path = tmp_path / "trade_cal.parquet"
    pd.DataFrame(
        {
            "cal_date": ["20260102", "20260105", "20260106", "20260107"],
            "is_open": [1, 1, 1, 1],
        }
    ).to_parquet(calendar_path, index=False)

    summary = reference.build_normalized_index_weight_daily(
        raw,
        raw,
        trade_cal_path=calendar_path,
        end_date="20260107",
    )

    daily = pd.read_parquet(summary["path"])
    assert sorted(daily["trade_date"].unique()) == [
        "20260102",
        "20260105",
        "20260106",
        "20260107",
    ]
    assert set(daily[daily["trade_date"] == "20260105"]["snapshot_date"]) == {"20260102"}
    assert set(daily[daily["trade_date"] == "20260107"]["snapshot_date"]) == {"20260106"}
    assert daily.groupby("trade_date")["drift_weight"].sum().round(8).eq(1.0).all()


def test_share_float_downloads_by_announcement_day(tmp_path: Path) -> None:
    options = reference.RawReferenceDownloadOptions(
        dataset="share_float",
        out_dir=tmp_path,
        start_date="20260101",
        end_date="20260103",
        page_size=5,
        request_interval_seconds=0,
    )

    frame = reference._download_share_float(
        FakeShareFloatClient(),
        options,
    )

    assert frame.groupby("ann_date").size().to_dict() == {
        "20260101": 6,
        "20260102": 6,
        "20260103": 6,
    }
    assert not frame["_source_truncated"].any()


def test_publish_reference_assets_writes_version_and_sha_receipt(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    for dataset in reference.REFERENCE_DATASETS:
        pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260730"}]).to_parquet(
            raw / f"{dataset}.parquet",
            index=False,
        )

    summary = reference.publish_reference_assets(
        tmp_path / "lake",
        raw,
        "20260730",
    )

    assert len(summary["published"]) == len(reference.REFERENCE_DATASETS)
    for item in summary["published"]:
        path = Path(item["path"])
        receipt = json.loads(Path(item["receipt_path"]).read_text(encoding="utf-8"))
        assert path.is_file()
        assert Path(item["version_path"]).is_file()
        assert receipt["schema_version"] == reference.REFERENCE_RECEIPT_SCHEMA_VERSION
        assert receipt["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert receipt["version_sha256"] == receipt["sha256"]

    reference.publish_reference_assets(tmp_path / "lake", raw, "20260730")
    pd.DataFrame([{"ts_code": "000002.SZ"}]).to_parquet(
        raw / "stock_st.parquet",
        index=False,
    )
    with pytest.raises(FileExistsError, match="immutable reference version"):
        reference.publish_reference_assets(tmp_path / "lake", raw, "20260730")
