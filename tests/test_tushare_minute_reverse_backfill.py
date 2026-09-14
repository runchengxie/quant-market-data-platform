from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
from market_data_platform.tushare_minute_backfill_part01 import _policy_payload
from market_data_platform.tushare_minute_reverse_backfill import (
    _resume_paths,
    _scheduler_plan_stem,
    _select_reverse_dates,
)


def test_reverse_selector_walks_newest_to_oldest_and_skips_complete_dates() -> None:
    assert _select_reverse_dates(
        open_dates=["20220707", "20220708", "20220711", "20220712", "20220713", "20220714"],
        current_min_date="20220715",
        complete_dates={"20220714", "20220713", "20220711"},
        max_dates=2,
    ) == ["20220712", "20220708"]


def test_scheduler_plan_stem_is_stable_for_resume() -> None:
    assert _scheduler_plan_stem(["20220712", "20220708"]) == "reverse_20220708_20220712"


def test_selector_returns_empty_when_history_is_exhausted() -> None:
    assert (
        _select_reverse_dates(
            open_dates=["20220715"],
            current_min_date="20220715",
            complete_dates=set(),
            max_dates=1,
        )
        == []
    )


def test_resume_ignores_plan_larger_than_scheduler_window(tmp_path) -> None:
    plan_path = tmp_path / "plans/reverse_20220701_20220703.plan.json"
    receipt_path = tmp_path / "receipts/reverse_20220701_20220703.receipt.json"
    plan_path.parent.mkdir(parents=True)
    receipt_path.parent.mkdir(parents=True)
    plan_path.write_text(
        '{"identity": {"segments": [{"dates": ["20220703", "20220702", "20220701"]}]} }',
        encoding="utf-8",
    )
    receipt_path.write_text('{"status": "interrupted"}', encoding="utf-8")

    assert _resume_paths(tmp_path, max_dates=1) is None


def test_resume_keeps_incomplete_plan_within_scheduler_window(tmp_path) -> None:
    plan_path = tmp_path / "plans/reverse_20220703_20220703.plan.json"
    receipt_path = tmp_path / "receipts/reverse_20220703_20220703.receipt.json"
    plan_path.parent.mkdir(parents=True)
    receipt_path.parent.mkdir(parents=True)
    plan_path.write_text(
        '{"identity": {"segments": [{"dates": ["20220703"]}]} }',
        encoding="utf-8",
    )
    receipt_path.write_text('{"status": "interrupted"}', encoding="utf-8")

    assert _resume_paths(tmp_path, max_dates=1) == (plan_path, receipt_path)


def test_legacy_request_policy_payload_keeps_original_shape() -> None:
    assert "request_timeout_seconds" not in _policy_payload(TushareRequestPolicy())
