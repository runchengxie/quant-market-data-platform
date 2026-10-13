"""Evidence-stage and duplicate-event guards for unpublished execution inputs."""

import importlib.util
from pathlib import Path

import pandas as pd

spec = importlib.util.spec_from_file_location(
    "execution_audit", Path(__file__).parents[1] / "scripts/operations/audit_execution_inputs.py"
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_implementation_stage_and_missing_payment_are_audited():
    frame = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "end_date": "20241231",
                "ann_date": "20250301",
                "div_proc": "预案",
                "ex_date": None,
                "imp_ann_date": None,
                "record_date": None,
                "pay_date": None,
                "div_listdate": None,
                "stk_div": 0.0,
                "cash_div_tax": 1.0,
            },
            {
                "ts_code": "000001.SZ",
                "end_date": "20241231",
                "ann_date": "20250301",
                "div_proc": "实施 ",
                "ex_date": "20250601",
                "imp_ann_date": "20250520",
                "record_date": "20250531",
                "pay_date": "",
                "div_listdate": None,
                "stk_div": 0.0,
                "cash_div_tax": 1.0,
            },
        ]
    )
    audit = module.dividend_audit(frame)
    assert audit["implemented_rows"] == 1
    assert audit["implemented_positive_cash_missing_pay_date"] == 1
    assert audit["implemented_positive_stock_missing_listing_date"] == 0
    assert audit["pit_classification"] == "reconstructed_pit_not_revision_safe"


def test_conflicting_amounts_are_distinct_from_exact_duplicates():
    frame = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "end_date": "20241231",
                "ann_date": "20250301",
                "div_proc": "实施",
                "ex_date": "20250601",
                "imp_ann_date": "20250520",
                "record_date": "20250531",
                "pay_date": "20250601",
                "div_listdate": None,
                "stk_div": 0.0,
                "cash_div_tax": amount,
            }
            for amount in [1.0, 1.0, 2.0]
        ]
    )
    audit = module.dividend_audit(frame)
    assert audit["exact_duplicate_rows"] == 1
    assert audit["duplicate_event_keys"] == 3


def test_export_preserves_conflicts_and_removes_only_exact_duplicates(tmp_path):
    import argparse

    frame = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "end_date": "20241231",
                "ann_date": "20250301",
                "div_proc": "实施",
                "ex_date": "20250601",
                "imp_ann_date": "20250520",
                "record_date": "20250531",
                "pay_date": "20250601",
                "div_listdate": None,
                "stk_div": 0.0,
                "cash_div_tax": amount,
            }
            for amount in [1.0, 1.0, 2.0]
        ]
    )
    dividend = tmp_path / "div.parquet"
    frame.to_parquet(dividend, index=False)
    suspension = tmp_path / "susp.parquet"
    pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20250601"]}).to_parquet(
        suspension, index=False
    )
    output = tmp_path / "output"
    result = module.prepare(
        argparse.Namespace(
            out_dir=output,
            suspension=[suspension],
            dividend=dividend,
            symbols=None,
            window=["y2025:20250101:20251231"],
        )
    )
    path = result["windows"]["y2025"]["assets"]["dividend_implemented"]["path"]
    exported = pd.read_parquet(path)
    assert len(exported) == 2
    assert exported.event_key_conflict.all()
    assert len(pd.read_parquet(output / "y2025/dividend_all_stages.parquet")) == 3
    assert result["published"] is False


def test_equivalent_announcements_collapse_but_missing_settlement_blocks():
    frame = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "end_date": "20241231",
                "ann_date": ann,
                "div_proc": "实施",
                "ex_date": "20250601",
                "imp_ann_date": "20250520",
                "record_date": "20250531",
                "pay_date": "20250601",
                "div_listdate": None,
                "stk_div": 0.0,
                "cash_div_tax": 1.0,
            }
            for ann in ["20250301", "20250401"]
        ]
    )
    ready, audit = module.resolve_dividend_events(frame)
    assert len(ready) == 1
    assert ready.source_versions.iloc[0] == 2
    assert ready.available_from.iloc[0] == "20250520"
    assert audit["equivalent_versions_collapsed"] == 1
    frame["pay_date"] = None
    ready, audit = module.resolve_dividend_events(frame)
    assert ready.empty
    assert audit["blocked_events"][0]["missing_required_fields"] == ["pay_date"]


def test_material_cash_conflict_is_excluded_from_ready_events():
    frame = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "end_date": "20241231",
                "ann_date": "20250301",
                "div_proc": "实施",
                "ex_date": "20250601",
                "imp_ann_date": "20250520",
                "record_date": "20250531",
                "pay_date": "20250601",
                "div_listdate": None,
                "stk_div": 0.0,
                "cash_div_tax": amount,
            }
            for amount in [1.0, 2.0]
        ]
    )
    ready, audit = module.resolve_dividend_events(frame)
    assert ready.empty
    assert audit["blocked_events"][0]["conflict_fields"] == ["cash_div_tax"]
