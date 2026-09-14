from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from market_data_platform.providers.a_share_minute_fusion import (
    CANONICAL_MINUTE_COLUMNS,
    CANONICAL_MINUTE_SCHEMA,
    LEGACY_GUAN_CANONICAL_UNITS,
    LEGACY_GUAN_HUNDRED_X_UNITS,
    MinuteAggregationEngine,
    aggregate_guan_deal_file,
    fuse_and_write_minute_partition,
    normalize_legacy_guan_partition,
    normalize_legacy_guan_partition_with_stats,
    normalize_tushare_partition,
)


def _minute_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ts_code": "000001.SZ",
        "trade_time": "2026-07-06 09:31:00",
        "open": 10.0,
        "close": 10.1,
        "high": 10.2,
        "low": 9.9,
        "vol": 100.0,
        "amount": 1_000.0,
    }
    row.update(overrides)
    return row


def test_legacy_guan_normalization_requires_audited_unit_profile(tmp_path: Path) -> None:
    source = tmp_path / "legacy.parquet"
    pd.DataFrame(
        [
            _minute_row(vol=12_300.0, amount=456_700.0),
        ]
    ).assign(legacy_extra="not canonical").to_parquet(source, index=False)

    scaled = normalize_legacy_guan_partition(
        source,
        unit_profile=LEGACY_GUAN_HUNDRED_X_UNITS,
    )
    unscaled = normalize_legacy_guan_partition(
        source,
        unit_profile=LEGACY_GUAN_CANONICAL_UNITS,
    )

    assert list(scaled.columns) == list(CANONICAL_MINUTE_COLUMNS)
    assert scaled.loc[0, "vol"] == pytest.approx(123.0)
    assert scaled.loc[0, "amount"] == pytest.approx(4_567.0)
    assert unscaled.loc[0, "vol"] == pytest.approx(12_300.0)
    assert unscaled.loc[0, "amount"] == pytest.approx(456_700.0)
    assert scaled["trade_time"].dtype == "datetime64[ns]"


def test_legacy_guan_normalization_drops_only_fully_empty_placeholders() -> None:
    empty = _minute_row()
    for column in CANONICAL_MINUTE_COLUMNS[2:]:
        empty[column] = None
    partial = _minute_row(open=None)

    normalized = normalize_legacy_guan_partition(
        pd.DataFrame([_minute_row(), empty]),
        unit_profile=LEGACY_GUAN_CANONICAL_UNITS,
    )

    assert len(normalized) == 1
    with pytest.raises(ValueError, match="contains null or invalid canonical values"):
        normalize_legacy_guan_partition(
            pd.DataFrame([partial]),
            unit_profile=LEGACY_GUAN_CANONICAL_UNITS,
        )


def test_legacy_guan_normalization_repairs_ohlc_bounds_and_reports_cleanup() -> None:
    empty = _minute_row()
    for column in CANONICAL_MINUTE_COLUMNS[2:]:
        empty[column] = None
    result = normalize_legacy_guan_partition_with_stats(
        pd.DataFrame([_minute_row(close=10.3, high=10.2), empty]),
        unit_profile=LEGACY_GUAN_CANONICAL_UNITS,
    )

    assert result.frame.loc[0, "high"] == pytest.approx(10.3)
    assert result.stats.input_rows == 2
    assert result.stats.dropped_empty_rows == 1
    assert result.stats.repaired_ohlc_rows == 1
    assert result.stats.output_rows == 1


def test_tushare_projection_priority_unique_key_and_atomic_schema(tmp_path: Path) -> None:
    guan = pd.DataFrame(
        [
            _minute_row(close=9.0),
            _minute_row(close=9.5),
            _minute_row(
                ts_code="600000.SH",
                trade_time="2026-07-06 09:31:00",
                close=20.0,
            ),
        ]
    )
    tushare_source = tmp_path / "tushare.parquet"
    pd.DataFrame(
        [
            _minute_row(close=11.0, vol=12, amount=120.0),
            _minute_row(close=11.5, vol=13, amount=130.0),
        ]
    ).assign(
        trade_date="20260706",
        pre_close=8.0,
        change=3.5,
        pct_chg=43.75,
    ).to_parquet(tushare_source, index=False)
    tushare = normalize_tushare_partition(tushare_source)
    output_path = tmp_path / "trade_date=20260706" / "part-00000.parquet"

    stats = fuse_and_write_minute_partition(guan, tushare, output_path)
    output = pd.read_parquet(output_path)

    assert list(output.columns) == list(CANONICAL_MINUTE_COLUMNS)
    assert not output.duplicated(["ts_code", "trade_time"]).any()
    selected = output.loc[output["ts_code"].eq("000001.SZ")].iloc[0]
    assert selected["close"] == pytest.approx(11.5)
    assert stats.guan_input_rows == 3
    assert stats.guan_unique_rows == 2
    assert stats.tushare_input_rows == 2
    assert stats.tushare_unique_rows == 1
    assert stats.overlap_rows == 1
    assert stats.guan_output_rows == 1
    assert stats.tushare_output_rows == 1
    assert stats.output_rows == 2
    assert stats.as_dict()["output_rows_by_source"] == {"guan": 1, "tushare": 1}
    assert pq.read_schema(output_path).equals(CANONICAL_MINUTE_SCHEMA)
    assert not list(output_path.parent.glob(f".{output_path.name}.*.tmp"))


def test_deal_file_row_group_aggregation_units_buckets_and_mapping(tmp_path: Path) -> None:
    source = tmp_path / "deal_20260706.parquet"
    frame = pd.DataFrame(
        {
            "BizIndex": list(range(1, 16)),
            "DealTime": [
                92_500_000,
                93_000_000,
                93_030_000,
                93_100_000,
                113_000_000,
                113_000_999,
                114_500_000,
                130_000_000,
                145_959_500,
                150_000_000,
                150_001_770,
                100_000_000,
                100_000_000,
                150_100_000,
                93_015_000,
            ],
            "Price": [
                900,
                1_000,
                1_010,
                1_005,
                1_100,
                1_200,
                999,
                2_000,
                2_100,
                2_200,
                2_300,
                500,
                700,
                999,
                9_999,
            ],
            "SecuCode": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 600_000, -1, 1, 1],
            "TradingDay": [20_260_706] * 15,
            "Volume": [1, 100, 200, 50, 1, 1, 1, 10, 10, 10, 10, 2, 2, 1, 0],
        }
    )
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), source, row_group_size=2)

    result = aggregate_guan_deal_file(
        source,
        symbol_to_ts_code={"000001": "000001.SZ"},
        engine="pandas",
    )
    one_group_at_a_time = aggregate_guan_deal_file(
        source,
        symbol_to_ts_code={"000001": "000001.SZ"},
        engine="pandas",
        batch_row_groups=1,
        compaction_row_groups=2,
    )
    output = result.frame

    pd.testing.assert_frame_equal(output, one_group_at_a_time.frame)
    assert one_group_at_a_time.stats.row_group_batches == 8

    assert set(output["trade_time"].dt.strftime("%H:%M")) == {
        "09:30",
        "09:31",
        "10:00",
        "11:30",
        "13:01",
        "15:00",
    }
    opening_auction = output.loc[
        output["ts_code"].eq("000001.SZ")
        & output["trade_time"].eq(pd.Timestamp("2026-07-06 09:30:00"))
    ].iloc[0]
    assert opening_auction["open"] == pytest.approx(9.0)
    assert opening_auction["vol"] == pytest.approx(1.0)
    assert opening_auction["amount"] == pytest.approx(9.0)
    opening = output.loc[
        output["ts_code"].eq("000001.SZ")
        & output["trade_time"].eq(pd.Timestamp("2026-07-06 09:31:00"))
    ].iloc[0]
    assert opening["open"] == pytest.approx(10.0)
    assert opening["close"] == pytest.approx(10.05)
    assert opening["high"] == pytest.approx(10.1)
    assert opening["low"] == pytest.approx(10.0)
    assert opening["vol"] == pytest.approx(350.0)
    assert opening["amount"] == pytest.approx(3_522.5)

    morning_close = output.loc[
        output["ts_code"].eq("000001.SZ")
        & output["trade_time"].eq(pd.Timestamp("2026-07-06 11:30:00"))
    ].iloc[0]
    assert morning_close["open"] == pytest.approx(11.0)
    assert morning_close["close"] == pytest.approx(12.0)

    close = output.loc[
        output["ts_code"].eq("000001.SZ")
        & output["trade_time"].eq(pd.Timestamp("2026-07-06 15:00:00"))
    ].iloc[0]
    assert close["open"] == pytest.approx(21.0)
    assert close["close"] == pytest.approx(23.0)
    assert close["high"] == pytest.approx(23.0)
    assert close["vol"] == pytest.approx(30.0)
    assert close["amount"] == pytest.approx(660.0)

    fallback = output.loc[output["ts_code"].eq("600000.SH")].iloc[0]
    assert fallback["vol"] == pytest.approx(2.0)
    assert fallback["amount"] == pytest.approx(10.0)
    assert result.stats.row_groups == 8
    assert result.stats.row_group_batches == 2
    assert result.stats.input_rows == 15
    assert result.stats.session_rows == 13
    assert result.stats.eligible_rows == 11
    assert result.stats.mapping_rows == 10
    assert result.stats.fallback_rows == 1
    assert result.stats.unresolved_rows == 1
    assert result.stats.invalid_value_rows == 1
    assert result.stats.output_rows == 6
    assert result.stats.output_symbols == 2
    assert result.stats.output_traded_symbols == 2
    assert result.stats.output_vol_sum == pytest.approx(output["vol"].sum())
    assert result.stats.output_amount_sum == pytest.approx(output["amount"].sum())
    assert result.stats.unit_profile == "guan_deal_price_cents_volume_shares"
    assert list(output.columns) == list(CANONICAL_MINUTE_COLUMNS)


@pytest.mark.parametrize("engine", ["pandas", "polars"])
def test_deal_fallback_maps_302_prefix_to_shenzhen(
    tmp_path: Path, engine: MinuteAggregationEngine
) -> None:
    source = tmp_path / "deal_20260706.parquet"
    frame = pd.DataFrame(
        {
            "BizIndex": [1],
            "DealTime": [93_100_000],
            "Price": [5_000],
            "SecuCode": [302_132],
            "TradingDay": [20_260_706],
            "Volume": [100.0],
        }
    )
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), source)

    result = aggregate_guan_deal_file(source, engine=engine)

    assert result.frame["ts_code"].tolist() == ["302132.SZ"]
    assert result.stats.fallback_rows == 1


def test_atomic_write_preserves_existing_partition_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_data_platform.providers import a_share_minute_fusion as fusion

    output_path = tmp_path / "part.parquet"
    old = pd.DataFrame([_minute_row(close=7.0)])
    fusion.write_canonical_minute_partition(old, output_path)
    real_write_table = fusion.pq.write_table

    def fail_after_partial_write(table: pa.Table, where: Path, **kwargs: object) -> None:
        real_write_table(table.slice(0, 0), where, **kwargs)
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(fusion.pq, "write_table", fail_after_partial_write)
    with pytest.raises(RuntimeError, match="simulated write failure"):
        fusion.write_canonical_minute_partition(
            pd.DataFrame([_minute_row(close=99.0)]), output_path
        )

    preserved = pd.read_parquet(output_path)
    assert preserved.loc[0, "close"] == pytest.approx(7.0)
    assert not list(tmp_path.glob(f".{output_path.name}.*.tmp"))


def test_deal_file_rejects_invalid_trading_day_values(tmp_path: Path) -> None:
    source = tmp_path / "deal_20260706.parquet"
    pd.DataFrame(
        {
            "BizIndex": [1],
            "DealTime": [93_000_000],
            "Price": [1_000],
            "SecuCode": [1],
            "TradingDay": [None],
            "Volume": [100],
        }
    ).to_parquet(source, index=False)

    with pytest.raises(ValueError, match="invalid TradingDay"):
        aggregate_guan_deal_file(source)


def test_deal_engine_auto_falls_back_and_explicit_polars_has_clear_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_data_platform.providers import a_share_minute_fusion as fusion

    source = tmp_path / "deal_20260706.parquet"
    pd.DataFrame(
        {
            "BizIndex": [1],
            "DealTime": [92_500_000],
            "Price": [1_000],
            "SecuCode": [1],
            "TradingDay": [20_260_706],
            "Volume": [100],
        }
    ).to_parquet(source, index=False)
    monkeypatch.setattr(fusion, "_try_import_polars", lambda: None)

    automatic = aggregate_guan_deal_file(source, engine="auto")
    pandas_result = aggregate_guan_deal_file(source, engine="pandas")

    pd.testing.assert_frame_equal(automatic.frame, pandas_result.frame)
    assert automatic.stats == pandas_result.stats
    with pytest.raises(RuntimeError, match="minute-fusion.*optional dependency"):
        aggregate_guan_deal_file(source, engine="polars")


def test_polars_deal_engine_matches_pandas_across_row_groups(tmp_path: Path) -> None:
    pytest.importorskip("polars")
    source = tmp_path / "deal_20260706.parquet"
    frame = pd.DataFrame(
        {
            "BizIndex": [1, 9, 3, 3, 3, 5, 6, 7, 8, 9, 10],
            "DealTime": [
                92_500_000,
                100_000_000,
                100_000_000,
                100_000_000,
                100_000_000,
                113_000_999,
                130_000_000,
                150_001_770,
                100_000_000,
                100_000_000,
                100_000_000,
            ],
            "Price": [1_000, 1_200, 1_100, 1_125, 1_150, 1_300, 1_400, 1_500, 2_000, 700, 999],
            "SecuCode": [1, 1, 1, 1, 1, 1, 1, 1, 600_000, 700_000, 1],
            "TradingDay": [20_260_706] * 11,
            "Volume": [100, 10, 20, 30, 40, 50, 60, 70, 80, 90, 0],
        }
    )
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), source, row_group_size=2)
    options: dict[str, Any] = {
        "symbol_to_ts_code": {"000001": "000001.SZ"},
        "batch_row_groups": 2,
        "compaction_row_groups": 2,
    }

    pandas_result = aggregate_guan_deal_file(source, engine="pandas", **options)
    polars_result = aggregate_guan_deal_file(source, engine="polars", **options)
    default_polars_result = aggregate_guan_deal_file(source, engine="polars")

    pd.testing.assert_frame_equal(
        pandas_result.frame,
        polars_result.frame,
        check_exact=False,
        rtol=1e-12,
        atol=1e-8,
    )
    pandas_stats = pandas_result.stats.as_dict()
    polars_stats = polars_result.stats.as_dict()
    assert pandas_stats.pop("engine") == "pandas"
    assert polars_stats.pop("engine") == "polars"
    assert pandas_stats == polars_stats
    assert default_polars_result.stats.row_group_batches == 1
    ten_oclock = polars_result.frame.loc[
        polars_result.frame["ts_code"].eq("000001.SZ")
        & polars_result.frame["trade_time"].eq(pd.Timestamp("2026-07-06 10:00:00"))
    ].iloc[0]
    assert ten_oclock["open"] == pytest.approx(11.0)
    assert ten_oclock["close"] == pytest.approx(12.0)
    assert ten_oclock["vol"] == pytest.approx(100.0)
    assert ten_oclock["amount"] == pytest.approx(1_137.5)
    assert "09:30" in set(polars_result.frame["trade_time"].dt.strftime("%H:%M"))
