from __future__ import annotations

import pytest

from market_data_platform.cli import build_parser
from market_data_platform.providers.tushare_a_share_ownership_features import (
    build_a_share_fund_top10_portfolio_features,
    validate_a_share_fund_top10_portfolio_features,
)


def _write_period_part(root, period, frame):
    path = root / "data" / f"end_date={period}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def _fund_row(*, symbol: str, ann_date: str, end_date: str, rank: int) -> dict[str, object]:
    score = float(100 - rank)
    return {
        "ts_code": "000001.OF",
        "ann_date": ann_date,
        "end_date": end_date,
        "symbol": symbol,
        "mkv": score * 1_000_000.0,
        "amount": score * 100_000.0,
        "stk_mkv_ratio": score / 100.0,
        "stk_float_ratio": score / 200.0,
    }


def test_fund_top10_features_normalize_disclosure_scope_and_track_exits(tmp_path):
    pd = pytest.importorskip("pandas")
    fund_dir = tmp_path / "fund_portfolio"

    first_symbols = [f"{index:06d}.SZ" for index in range(1, 13)]
    first = pd.DataFrame(
        [
            _fund_row(
                symbol=symbol,
                ann_date="20250820",
                end_date="20250630",
                rank=rank,
            )
            for rank, symbol in enumerate(first_symbols, start=1)
        ]
    )
    _write_period_part(fund_dir, "20250630", first)

    # Quarterly disclosure: the previous no.10 holding disappears and a new
    # stock enters the top-10. The state asset should emit a zero exit row for
    # the old no.10 position rather than carrying it forward indefinitely.
    next_symbols = first_symbols[:9] + ["000013.SZ"]
    second = pd.DataFrame(
        [
            _fund_row(
                symbol=symbol,
                ann_date="20251020",
                end_date="20250930",
                rank=rank,
            )
            for rank, symbol in enumerate(next_symbols, start=1)
        ]
    )
    _write_period_part(fund_dir, "20250930", second)

    out_dir = tmp_path / "fund_top10_features"
    manifest = build_a_share_fund_top10_portfolio_features(
        fund_portfolio_dir=fund_dir,
        out_dir=out_dir,
        available_delay_days=1,
        min_rows=20,
        min_symbols=11,
    )

    assert manifest["schema_version"] == "tushare.a_share.fund_top10_portfolio_features.v1"
    assert manifest["semantics"]["holding_scope"] == "top_10_by_mkv_per_fund_disclosure"
    assert manifest["semantics"]["event_level_change_fields"] is False

    data = pd.read_parquet(out_dir / "data")
    data["trade_date"] = data["trade_date"].astype(str)
    first_event = data[data["trade_date"] == "20250821"]
    second_event = data[data["trade_date"] == "20251021"]

    assert set(first_event["symbol"]) == set(first_symbols[:10])
    assert "000011.SZ" not in set(first_event["symbol"])
    assert "000012.SZ" not in set(first_event["symbol"])
    assert "fund_count_holding_stock" not in data.columns
    assert "fund_top10_count_holding_stock" in data.columns
    assert not any(column.endswith("_qoq_change") for column in data.columns)

    exited = second_event[second_event["symbol"] == "000010.SZ"].iloc[0]
    entered = second_event[second_event["symbol"] == "000013.SZ"].iloc[0]
    assert exited["fund_top10_count_holding_stock"] == 0.0
    assert entered["fund_top10_count_holding_stock"] == 1.0

    result = validate_a_share_fund_top10_portfolio_features(
        asset_dir=out_dir,
        min_rows=20,
        min_symbols=11,
    )
    assert result["status"] == "passed"


def test_fund_top10_feature_commands_are_exposed() -> None:
    build = build_parser().parse_args(
        [
            "tushare",
            "build-a-share-fund-top10-portfolio-features",
            "--fund-portfolio-dir",
            "fund-portfolio",
            "--out-dir",
            "out",
            "--top-n",
            "10",
        ]
    )
    assert build.tushare_command == "build-a-share-fund-top10-portfolio-features"
    assert build.fund_portfolio_dir == "fund-portfolio"
    assert build.top_n == 10

    validate = build_parser().parse_args(
        [
            "tushare",
            "validate-a-share-fund-top10-portfolio-features",
            "--asset-dir",
            "out",
        ]
    )
    assert validate.tushare_command == "validate-a-share-fund-top10-portfolio-features"
