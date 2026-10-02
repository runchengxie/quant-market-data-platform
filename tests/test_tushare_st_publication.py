"""ST availability survives owner publication and daily-clean consumption."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from quant_market_data_platform.providers.tushare_a_share_clean import build_a_share_daily_clean


@pytest.mark.parametrize("legacy,published", [(False, False), (True, False), (False, True)])
def test_daily_clean_uses_validated_st_history_across_dates(tmp_path, legacy, published) -> None:
    raw = tmp_path / "raw"
    out = tmp_path / "clean"
    instruments = tmp_path / "instruments.parquet"
    history = tmp_path / "st_history_reconstructed.parquet"
    for date in ("20240102", "20240103"):
        _write_part(
            pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": date,
                        "close": 10.0,
                        "pre_close": 10.0,
                        "open": 10.0,
                        "high": 10.0,
                        "low": 10.0,
                        "vol": 100.0,
                        "amount": 1000.0,
                    }
                ]
            ),
            raw,
            date,
        )
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "name": "ST金龙鱼",
                "list_date": "20200101",
            }
        ]
    ).to_parquet(instruments, index=False)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20240103",
                **({} if legacy else {"available_from": "20240104"}),
            }
        ]
    ).to_parquet(history, index=False)
    history.with_name("st_history_reconstructed.receipt.json").write_text(
        json.dumps(
            {
                "quality_status": "complete",
                "schema_version": (
                    "market-data-platform.tushare-reference.v1"
                    if legacy
                    else "market-data-platform.reconstructed-st-history.v2"
                ),
                "start_date": "20240102",
                "end_date": "20240103",
                "history_sha256": hashlib.sha256(history.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    if published:
        from quant_market_data_platform.providers.tushare_constraint_publish import (
            publish_constraint_assets,
        )

        item = publish_constraint_assets(
            tmp_path / "lake", tmp_path, "20240103", datasets=["st_history_reconstructed"]
        )["published"][0]
        history = Path(item["path"])

    manifest = build_a_share_daily_clean(
        daily_dir=raw,
        instruments_file=instruments,
        st_history_file=history,
        out_dir=out,
        batch_trade_dates=1,
        memory_soft_limit_mb=0,
        memory_hard_limit_mb=0,
    )
    rows = pd.read_parquet(out / "data" / "000001.SZ.parquet")
    assert rows["is_st"].tolist() == [False, True]
    assert rows["st_available_from"].isna().tolist() == [True, legacy]
    if not legacy:
        assert rows.loc[1, "st_available_from"] == "20240104"
    assert manifest["inputs"]["st_history_file"] == str(history)
    assert manifest["inputs"]["st_history_receipt_schema"] == (
        "market-data-platform.tushare-reference.v1"
        if legacy or published
        else "market-data-platform.reconstructed-st-history.v2"
    )
    from quant_market_data_platform.providers.tushare_a_share_quality_part02 import _load_st_keys

    keys, error = _load_st_keys(manifest)
    if legacy:
        assert error is not None
    else:
        assert error is None
        assert keys is not None and keys[("000001.SZ", "20240103")] == "20240104"
    assert "st_available_from" in manifest["columns"]


def _write_part(frame, root, trade_date):
    path = root / "data" / f"trade_date={trade_date}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
