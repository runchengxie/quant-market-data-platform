"""Evidence eligibility must fail closed before any consumer can reuse it."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/operations/historical_minute_coverage_pilot.py"


def module():
    assert SCRIPT.exists(), "independent coverage pilot is not implemented"
    spec = importlib.util.spec_from_file_location("coverage_pilot", SCRIPT)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_mutated_input_is_rejected(tmp_path):
    pilot = module()
    path = tmp_path / "input.json"
    path.write_text("{}")
    binding = pilot.bind(path)
    path.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="changed"):
        pilot.verify(binding)


@pytest.mark.parametrize(
    "reason",
    [
        "quarantined",
        "applicable_provider_exclusions",
        "missing_currency_evidence",
        "unresolved_listing_bounds",
        "daily_reference_disagreement",
        "minute_universe_mismatch",
        "native_semantics_failed",
        "duplicate_partitions",
    ],
)
def test_any_unresolved_evidence_blocks_eligibility(reason):
    result = module().decision([reason])
    assert result["status"] == "unresolved"
    assert not result["eligible_for_receipt_review"]
    assert not result["full_market_promotion_allowed"]


def test_clean_evidence_only_qualifies_for_review():
    result = module().decision([])
    assert result["eligible_for_receipt_review"]
    assert not result["full_market_promotion_allowed"]


def test_code_transition_gap_is_unresolved():
    pilot = module()
    with pytest.raises(RuntimeError, match="unresolved code-transition gap"):
        pilot.normalize({"001872.SZ"}, "20181224")
    assert pilot.normalize({"001872.SZ"}, "20181220")[0] == {"000022.SZ"}
    assert pilot.normalize({"001872.SZ"}, "20181226")[0] == {"001872.SZ"}


def test_stock_snapshot_cannot_admit_non_cny_or_undated_delisting(tmp_path):
    import pandas as pd

    path = tmp_path / "stocks.parquet"
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "list_status": "L",
                "list_date": "19910101",
                "delist_date": "",
                "curr_type": "USD",
            },
            {
                "ts_code": "000002.SZ",
                "list_status": "D",
                "list_date": "19910101",
                "delist_date": "",
                "curr_type": "CNY",
            },
        ]
    ).to_parquet(path)
    active, reasons = module().listing_evidence(stock_snapshots(tmp_path, path), "20200102")
    assert "000001.SZ" not in active
    assert "missing_delisting_date" in reasons


def test_daily_reference_rejects_wrong_date(tmp_path):
    import pandas as pd

    path = tmp_path / "daily.parquet"
    pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20200103"]}).to_parquet(path)
    with pytest.raises(ValueError, match="date mismatch"):
        module().reference([path], "20200102")


@pytest.mark.parametrize(
    "quarantined,excluded,missing",
    [(False, False, False), (True, False, False), (False, True, False), (False, False, True)],
)
def test_real_partition_never_qualifies_when_evidence_blocks(
    tmp_path, quarantined, excluded, missing
):
    import json

    import pandas as pd

    pilot = module()
    date = "20200102"
    times = pd.date_range("2020-01-02 09:30", "2020-01-02 11:30", freq="min").append(
        pd.date_range("2020-01-02 13:01", "2020-01-02 15:00", freq="min")
    )
    frame = pd.DataFrame(
        {
            "ts_code": "000001.SZ",
            "trade_time": times,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "vol": 100.0,
            "amount": 1000.0,
        }
    )
    from quant_market_data_platform.providers._a_share_mins_partition import (
        _atomic_write_partition,
        _partition_state,
    )

    minute_dir = tmp_path / "minute"
    minute_dir.mkdir()
    part = _atomic_write_partition(frame, minute_dir, trade_date=date)
    sidecar = minute_dir / "_minute_mirror.json"
    sidecar.write_text(
        json.dumps({"trade_date": date, "partition": _partition_state(minute_dir, trade_date=date)})
    )
    reference = tmp_path / "reference.parquet"
    symbols = ["000001.SZ", "920001.BJ"] if missing else ["000001.SZ"]
    pd.DataFrame({"ts_code": symbols, "trade_date": date}).to_parquet(reference)
    stock = tmp_path / "stock.parquet"
    pd.DataFrame(
        {
            "ts_code": symbols,
            "list_date": "19910101",
            "delist_date": "",
            "curr_type": "CNY",
            "list_status": "L",
        }
    ).to_parquet(stock)
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {"exceptions": [{"ts_code": "000001.SZ", "observed_dates": [date] if excluded else []}]}
        )
    )
    paths = {
        "sidecar": sidecar,
        "partition": part,
        "reference": reference,
        "stock": stock,
        "policy": policy,
    }
    row = {
        "date": date,
        "quarantined": quarantined,
        "sidecar": "sidecar",
        "partitions": ["partition"],
        "daily": ["reference"],
        "daily_basic": ["reference"],
        "stocks": dict(zip("LDPG", "LDPG", strict=True)),
        "policy": "policy",
    }
    paths.update(stock_snapshots(tmp_path, stock))
    result = pilot.inspect_date(row, paths)
    expected = (
        "quarantined"
        if quarantined
        else "applicable_provider_exclusions"
        if excluded
        else "minute_universe_mismatch"
    )
    if quarantined or excluded or missing:
        assert expected in result["unresolved"]
        assert not result["eligible_for_receipt_review"]
    else:
        assert result["unresolved"] == []
        assert result["eligible_for_receipt_review"]
    assert not result["full_market_promotion_allowed"]
    if missing:
        assert result["independent_minus_actual"] == ["920001.BJ"]
    if not (quarantined or excluded or missing):
        content = json.loads(sidecar.read_text())
        content["partition"]["files"].append({"name": "part-00001.parquet"})
        sidecar.write_text(json.dumps(content))
        unscanned = pilot.inspect_date(row, paths)
        assert "unscanned_sidecar_partitions" in unscanned["unresolved"]
        assert not unscanned["eligible_for_receipt_review"]


def test_input_replace_and_restore_is_detected(tmp_path):
    pilot = module()
    path = tmp_path / "file"
    path.write_text("original")
    binding = pilot.bind(path)
    saved = tmp_path / "saved"
    path.rename(saved)
    path.write_text("replacement")
    path.unlink()
    saved.rename(path)
    with pytest.raises(ValueError, match="changed"):
        pilot.verify(binding)


def test_duplicate_stock_snapshot_paths_are_rejected(tmp_path):
    import pandas as pd

    path = tmp_path / "stocks.parquet"
    pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "list_status": ["L"],
            "list_date": ["19910101"],
            "delist_date": [""],
            "curr_type": ["CNY"],
        }
    ).to_parquet(path)
    with pytest.raises(ValueError, match="distinct"):
        module().listing_evidence(dict.fromkeys("LDPG", path), "20200102")


@pytest.mark.parametrize(
    "date,quarantined", [("20200230", False), ("2020-01-02", False), ("20200102", "false")]
)
def test_manifest_date_and_quarantine_types_reject_invalid_values(date, quarantined):
    with pytest.raises(ValueError):
        module().validate_dates([{"date": date, "quarantined": quarantined}])


def stock_snapshots(tmp_path, source):
    import pandas as pd

    frame = pd.read_parquet(source)
    paths = {}
    for status in "LDPG":
        path = tmp_path / f"status-{status}.parquet"
        frame.loc[frame.list_status.eq(status)].to_parquet(path)
        paths[status] = path
    return paths


def test_sidecar_cannot_authenticate_another_scanned_partition(tmp_path):
    import json

    sidecar = tmp_path / "_minute_mirror.json"
    sidecar.write_text(json.dumps({"trade_date": "20200102"}))
    row = {"date": "20200102", "quarantined": False, "sidecar": "sidecar", "partitions": ["other"]}
    with pytest.raises(ValueError, match="does not match sidecar"):
        module().inspect_date(row, {"sidecar": sidecar, "other": tmp_path / "other.parquet"})


@pytest.mark.parametrize("dates", [["10000000"], ["20200230"], ["19910101", "19920101"]])
def test_invalid_or_conflicting_listing_bounds_rejected(tmp_path, dates):
    import pandas as pd

    source = tmp_path / "stock.parquet"
    pd.DataFrame(
        {
            "ts_code": "000001.SZ",
            "list_status": "L",
            "curr_type": "CNY",
            "list_date": dates,
            "delist_date": "",
        }
    ).to_parquet(source)
    with pytest.raises(ValueError):
        module().listing_evidence(stock_snapshots(tmp_path, source), "20200102")
