from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from market_data_platform.research_views.daily_watch20_candidate_pool import (
    THS_HOT_V2_MAX_MISSING_RANKS,
    THS_HOT_V3_MAX_MISSING_RANKS,
    DailyWatch20CandidatePool,
    candidate_pool_policy_id,
    load_daily_watch20_candidate_pool,
    restrict_daily_watch20_candidates,
)
from market_data_platform.research_views.daily_watch20_candidate_pool_dc_concept import (
    load_dc_concept_strict_v1,
)


def _write_ths_hot_partition(root: Path, ranks: list[int]) -> None:
    partition = root / "data" / "trade_date=20260717"
    partition.mkdir(parents=True)
    pd.DataFrame(
        {
            "trade_date": ["20260717"] * len(ranks),
            "data_type": ["热股"] * len(ranks),
            "symbol": [f"{rank:06d}.SZ" for rank in ranks],
            "rank": ranks,
            "pct_change": [1.0] * len(ranks),
            "rank_time": ["2026-07-17 15:01:00"] * len(ranks),
            "platform_market": ["a_share"] * len(ranks),
        }
    ).to_parquet(partition / "part.parquet", index=False)


def test_all_market_is_an_explicit_unrestricted_snapshot(tmp_path: Path) -> None:
    pool = load_daily_watch20_candidate_pool(
        tmp_path,
        source_date="2026-07-17",
        mode="all_market",
    )
    assert pool.source_date == "20260717"
    assert not pool.restricted
    assert pool.policy_id == candidate_pool_policy_id("all_market")


def test_restriction_fails_closed_when_intersection_is_too_small() -> None:
    pool = DailyWatch20CandidatePool(
        mode="ths_hot_strict",
        source_date="20260717",
        frame=pd.DataFrame(
            {
                "symbol": ["000001.SZ", "600000.SH"],
                "ths_hot_rank": [1, 2],
                "ths_hot_pct_change": [1.0, 2.0],
            }
        ),
    )
    candidates = pd.DataFrame({"symbol": ["000001.SZ"], "score": [0.5]})
    with pytest.raises(RuntimeError, match="full-market fill is disabled"):
        restrict_daily_watch20_candidates(candidates, pool, required_symbols=2)


def test_dc_concept_composite_is_an_exact_date_restricted_pool() -> None:
    pool = DailyWatch20CandidatePool(
        mode="dc_concept_composite_strict_v1",
        source_date="20260717",
        frame=pd.DataFrame({"symbol": ["000001.SZ"]}),
    )

    assert pool.restricted


def test_dc_concept_requires_current_contract(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="A-share current contract not found"):
        load_dc_concept_strict_v1(tmp_path, source_date="20260717")


@pytest.mark.parametrize(
    "mode",
    ["dc_concept_strict_v1", "dc_concept_composite_strict_v1"],
)
def test_dc_concept_policy_identity_is_available_from_public_dispatch(mode: str) -> None:
    policy_id = candidate_pool_policy_id(mode)  # ty: ignore[invalid-argument-type]

    assert policy_id.startswith("daily_watch20.")
    assert ":min_symbols=20:ranked_stock_strength=v1" in policy_id


@pytest.mark.parametrize(
    "mode",
    ["dc_concept_strict_v1", "dc_concept_composite_strict_v1"],
)
def test_dc_concept_receipt_records_dc_source_for_both_modes(mode: str) -> None:
    pool = DailyWatch20CandidatePool(
        mode=mode,  # ty: ignore[invalid-argument-type]
        source_date="20260717",
        frame=pd.DataFrame({"symbol": ["000001.SZ"]}),
    )

    assert pool.receipt_summary()["source"] == (
        "tushare.dc_concept+dc_concept_cons+limit_list_ths+moneyflow_ths"
    )


def test_v2_accepts_two_late_rank_gaps_without_renumbering(tmp_path: Path) -> None:
    root = tmp_path / "ths_hot"
    expected_ranks = [rank for rank in range(1, 101) if rank not in {53, 73}]
    _write_ths_hot_partition(root, expected_ranks)

    pool = load_daily_watch20_candidate_pool(
        tmp_path,
        source_date="20260717",
        mode="ths_hot_strict_v2",
        ths_hot_root=root,
    )

    assert THS_HOT_V2_MAX_MISSING_RANKS == 2
    assert pool.missing_ranks == (53, 73)
    assert list(pool.frame["ths_hot_rank"]) == expected_ranks
    assert ":max_missing_ranks=2:" in pool.policy_id
    receipt = pool.receipt_summary(eligible_intersection_symbols=40)
    assert receipt["missing_ranks"] == [53, 73]
    assert receipt["max_missing_ranks"] == 2
    assert receipt["rank_coverage_status"] == "degraded"


def test_v2_rejects_more_than_two_late_rank_gaps(tmp_path: Path) -> None:
    root = tmp_path / "ths_hot"
    _write_ths_hot_partition(
        root,
        [rank for rank in range(1, 101) if rank not in {53, 73, 83}],
    )

    with pytest.raises(RuntimeError, match="too many missing ranks"):
        load_daily_watch20_candidate_pool(
            tmp_path,
            source_date="20260717",
            mode="ths_hot_strict_v2",
            ths_hot_root=root,
        )


def test_v2_complete_rank_coverage_is_reported_as_complete(tmp_path: Path) -> None:
    root = tmp_path / "ths_hot"
    _write_ths_hot_partition(root, list(range(1, 101)))

    pool = load_daily_watch20_candidate_pool(
        tmp_path,
        source_date="20260717",
        mode="ths_hot_strict_v2",
        ths_hot_root=root,
    )

    receipt = pool.receipt_summary()
    assert receipt["missing_ranks"] == []
    assert receipt["rank_coverage_status"] == "complete"


def test_v3_accepts_a_bounded_top_twenty_gap_without_renumbering(tmp_path: Path) -> None:
    root = tmp_path / "ths_hot"
    expected_ranks = [rank for rank in range(1, 101) if rank != 19]
    _write_ths_hot_partition(root, expected_ranks)

    pool = load_daily_watch20_candidate_pool(
        tmp_path,
        source_date="20260717",
        mode="ths_hot_strict_v3",
        ths_hot_root=root,
    )

    assert THS_HOT_V3_MAX_MISSING_RANKS == 2
    assert pool.missing_ranks == (19,)
    assert list(pool.frame["ths_hot_rank"]) == expected_ranks
    receipt = pool.receipt_summary(eligible_intersection_symbols=40)
    assert receipt["required_top_ranks"] == 1
    assert receipt["require_rank_one"] is True
    assert receipt["missing_ranks"] == [19]
    assert receipt["rank_coverage_status"] == "degraded"
    assert receipt["policy_id"].startswith("daily_watch20.ths_hot_positive_close.v3:")


def test_v3_still_rejects_a_missing_rank_one(tmp_path: Path) -> None:
    root = tmp_path / "ths_hot"
    _write_ths_hot_partition(root, list(range(2, 101)))

    with pytest.raises(RuntimeError, match="missing required rank 1"):
        load_daily_watch20_candidate_pool(
            tmp_path,
            source_date="20260717",
            mode="ths_hot_strict_v3",
            ths_hot_root=root,
        )


def test_v2_retains_the_historical_top_twenty_requirement(tmp_path: Path) -> None:
    root = tmp_path / "ths_hot"
    _write_ths_hot_partition(root, [rank for rank in range(1, 101) if rank != 19])

    with pytest.raises(RuntimeError, match=r"missing required top ranks: \[19\]"):
        load_daily_watch20_candidate_pool(
            tmp_path,
            source_date="20260717",
            mode="ths_hot_strict_v2",
            ths_hot_root=root,
        )


def test_dc_concept_strict_v1_builds_a_research_pool_from_exact_date_assets(
    tmp_path: Path,
) -> None:
    base = tmp_path / "assets" / "tushare" / "a_share"
    rows = {
        "dc_concept": pd.DataFrame(
            {
                "theme_code": ["A.DC", "B.DC"],
                "trade_date": ["20260717"] * 2,
                "name": ["主题A", "主题B"],
                "pct_change": [3.0, 1.0],
                "main_change": [100.0, 50.0],
                "hot": [1000, 500],
                "z_t_num": [3, 1],
            }
        ),
        "dc_concept_cons": pd.DataFrame(
            {
                "theme_code": ["A.DC"] * 20,
                "ts_code": [f"{index:06d}.SZ" for index in range(1, 21)],
                "trade_date": ["20260717"] * 20,
            }
        ),
        "limit_list_ths": pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "limit_type": ["涨停池"],
                "trade_date": ["20260717"],
            }
        ),
        "moneyflow_ths": pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "net_amount": [100.0],
                "trade_date": ["20260717"],
            }
        ),
    }
    contract_assets = {}
    for dataset, frame in rows.items():
        version = base / dataset / f"a_share_all_{dataset}_20260717"
        partition = version / "data" / "trade_date=20260717"
        partition.mkdir(parents=True)
        frame.to_parquet(partition / "part.parquet", index=False)
        contract_assets[dataset] = {
            "alias_path": str(base / dataset / f"a_share_all_{dataset}_latest"),
            "resolved_path": str(version),
            "exists": True,
            "availability": "available",
        }
    contract = tmp_path / "metadata" / "current_assets" / "a_share_current.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(json.dumps({"assets": contract_assets}), encoding="utf-8")

    pool = load_daily_watch20_candidate_pool(
        tmp_path,
        source_date="20260717",
        mode="dc_concept_strict_v1",
    )

    assert pool.restricted
    assert pool.mode == "dc_concept_strict_v1"
    assert len(pool.frame) == 20
    assert pool.frame.iloc[0]["symbol"] == "000001.SZ"
    assert pool.receipt_summary(eligible_intersection_symbols=20)["source"].startswith(
        "tushare.dc_concept"
    )
