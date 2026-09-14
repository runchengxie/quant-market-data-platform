from market_data_platform.dq_receipts import build_dq_receipt, worst_eligibility


def test_dq_receipt_has_stable_envelope() -> None:
    receipt = build_dq_receipt(
        dataset_id="cn_a_share_l2_order",
        provider="vendor_x",
        data_version="20260830",
        asset_schema_version="vendor_x.order.v3",
        run_id="run-1",
        input_summary={"files": 4},
        quality={"checks": [{"id": "schema", "passed": True}]},
        lineage={"raw_root": "/data/raw"},
        status="passed",
        eligibility="production",
        started_at="2026-08-30T01:00:00+00:00",
        finished_at="2026-08-30T01:00:03+00:00",
    )

    assert receipt["schema_version"] == "market_data_platform.dq_receipt.v1"
    assert receipt["dataset"]["id"] == "cn_a_share_l2_order"
    assert receipt["result"] == {"status": "passed", "eligibility": "production"}
    assert receipt["timing"]["duration_ms"] == 3000


def test_worst_eligibility_is_monotone() -> None:
    assert worst_eligibility("production", "research_only") == "research_only"
    assert worst_eligibility("research_only", "quarantine") == "quarantine"
