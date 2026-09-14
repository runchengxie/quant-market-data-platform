from market_data_platform.l2_quality_gate import evaluate_l2_quality


def _scan_report(**updates):
    report = {
        "path": "/data/order_2026-08-28.parquet",
        "rows": 10,
        "missing_columns": [],
        "trading_day_mismatch_rows": 0,
        "duplicate_id_rows": 0,
        "id_tracking_truncated": False,
        "timestamp_backwards": 0,
        "nulls": {},
        "nonpositive": {},
        "ordering": {
            "exchange_sequence_available": True,
            "ordering_mode": "channel_sequence",
            "sequence_quality": {
                "rows_observed": 10,
                "non_numeric_rows": 0,
                "duplicate_rows": 0,
                "backwards_rows": 0,
                "gap_events": 0,
                "gap_span": 0,
                "tracking_truncated": False,
            },
        },
    }
    report.update(updates)
    return {"status": "complete", "root": "/data", "files_scanned": 1, "reports": [report]}


def test_clean_l2_scan_is_production_eligible() -> None:
    receipt = evaluate_l2_quality(
        _scan_report(),
        dataset_id="cn_a_share_l2",
        provider="vendor_x",
        run_id="gate-1",
    )

    assert receipt["result"] == {"status": "passed", "eligibility": "production"}


def test_sequence_violation_quarantines_partition() -> None:
    scan = _scan_report()
    scan["reports"][0]["ordering"]["sequence_quality"]["duplicate_rows"] = 1

    receipt = evaluate_l2_quality(
        scan,
        dataset_id="cn_a_share_l2",
        provider="vendor_x",
        run_id="gate-2",
    )

    assert receipt["result"] == {"status": "failed", "eligibility": "quarantine"}
    assert "exchange_sequence_duplicate" in {
        check["id"] for check in receipt["quality"]["checks"] if not check["passed"]
    }


def test_tags_make_partition_research_only_without_failing_it() -> None:
    receipt = evaluate_l2_quality(
        _scan_report(nonpositive={"Price": 2}),
        pilot_manifest={"summary": {"keep": 8, "tag": 2, "exclude": 0}},
        dataset_id="cn_a_share_l2",
        provider="vendor_x",
        run_id="gate-3",
    )

    assert receipt["result"] == {"status": "passed", "eligibility": "research_only"}


def test_contract_quality_rule_can_allow_expected_nulls() -> None:
    receipt = evaluate_l2_quality(
        _scan_report(nulls={"AskPrice10": 4}),
        dataset_contract={
            "schema_version": "market_data_platform.dataset_contract.v1",
            "dataset": {"id": "cn_a_share_l2", "schema_version": "v1"},
            "quality_rules": {"null_values": "ignore"},
        },
        dataset_id="cn_a_share_l2",
        provider="vendor_x",
        run_id="gate-4",
    )

    assert receipt["result"] == {"status": "passed", "eligibility": "production"}
    null_check = next(
        check for check in receipt["quality"]["checks"] if check["id"] == "null_values"
    )
    assert null_check["effective_severity"] == "ignore"
