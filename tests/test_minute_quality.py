from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from test_published_assets import _write_asset, _write_contract

from quant_market_data_platform.published_assets import PublishedAssetContract
from quant_market_data_platform.published_frames import (
    ParquetFrameMapping,
    PublishedFramePlan,
    PublishedParquetFrameReader,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def guarded(tmp_path):
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2026-06-08 09:41", "2026-06-08 09:42", "2026-06-08 09:41", "2026-06-10 09:41"]
            ),
            "symbol": ["600608.SH", "600608.SH", "000001.SZ", "600608.SH"],
            "amount": [1066008.0, 12.0, 34.0, 56.0],
        }
    )
    asset = _write_asset(tmp_path, "minute", frame)
    path = _write_contract(tmp_path, {"minute": asset})
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}")
    entry = {
        "resolved_path": asset["resolved_path"],
        "manifest_sha256": _hash(Path(asset["manifest_path"])),
        "market_scope": ["SH", "SZ"],
        "partition_sha256": {
            "data/part.parquet": _hash(Path(asset["resolved_path"]) / "data" / "part.parquet")
        },
        "expected_dates": ["20260608", "20260609", "20260610"],
        "available_dates": ["20260608", "20260610"],
        "blocked_stock_days": [
            {
                "trade_date": "20260608",
                "symbol": "600608.SH",
                "issue_id": "hard-1",
                "evidence_path": str(evidence),
                "evidence_sha256": _hash(evidence),
            }
        ],
        "diagnostic_stock_days": [{"trade_date": "20260608", "symbol": "000001.SZ"}],
    }
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"schema_version": "minute.quality_registry.v1", "assets": {"minute": entry}})
    )
    payload = json.loads(path.read_text())
    payload["contract"]["minute_quality"] = {
        "registry_path": str(registry),
        "registry_sha256": _hash(registry),
    }
    path.write_text(json.dumps(payload))
    contract = PublishedAssetContract.load(path, artifacts_root=tmp_path)
    plan = PublishedFramePlan(
        (
            ParquetFrameMapping(
                "minute", "time", "symbol", {"amount": "amount"}, relative_path="data"
            ),
        )
    )
    return contract, plan, registry, evidence


def test_default_blocks_only_requested_stock_day(guarded):
    contract, plan, _, _ = guarded
    reader = PublishedParquetFrameReader(contract, plan)
    with pytest.raises(ValueError, match="hard-1"):
        reader.load(start_time="2026-06-08", end_time="2026-06-08 23:59")
    clean = reader.load(["000001.SZ"], "2026-06-08", "2026-06-08 23:59")
    assert clean.iloc[0, 0] == 34.0
    assert clean.attrs["minute_quality"]["excluded_rows"] == 0
    assert len(reader.load(["600608.SH"], "2026-06-10", "2026-06-10 23:59")) == 1


def test_exclusion_removes_whole_stock_day_and_preserves_values(guarded):
    contract, plan, _, _ = guarded
    reader = PublishedParquetFrameReader(
        contract, plan, quality_mode="exclude_stock_days", acknowledged_missing_dates=["20260609"]
    )
    result = reader.load()
    assert result.iloc[:, 0].tolist() == [34.0, 56.0]
    quality = result.attrs["minute_quality"]
    assert quality["excluded_rows"] == 2
    assert quality["missing_dates"] == ["20260609"]
    assert quality["excluded_stock_days"][0]["issue_id"] == "hard-1"
    assert reader.metadata["minute_quality"]["mode"] == "exclude_stock_days"


def test_missing_session_detected_even_with_empty_rows(guarded):
    contract, plan, _, _ = guarded
    reader = PublishedParquetFrameReader(contract, plan)
    with pytest.raises(ValueError, match="20260609"):
        reader.load(["NONEXISTENT"], "2026-06-09", "2026-06-09 23:59")
    reader = PublishedParquetFrameReader(contract, plan, acknowledged_missing_dates=["20260609"])
    result = reader.load(["NONEXISTENT"], "2026-06-09", "2026-06-09 23:59")
    assert result.empty
    assert result.attrs["minute_quality"]["missing_dates"] == ["20260609"]


@pytest.mark.parametrize("changed", ["registry", "evidence", "manifest", "contract"])
def test_identity_changes_rejected(guarded, changed):
    contract, plan, registry, evidence = guarded
    reader = PublishedParquetFrameReader(contract, plan)
    paths = {
        "registry": registry,
        "evidence": evidence,
        "manifest": contract.require_assets(["minute"])[0].manifest_path,
        "contract": contract.path,
    }
    with paths[changed].open("a") as handle:
        handle.write(" ")
    with pytest.raises(ValueError, match="changed|hash|identity"):
        reader.load(["000001.SZ"], "2026-06-08", "2026-06-08 23:59")


def test_mutation_during_read_rejected(guarded, monkeypatch):
    contract, plan, _, evidence = guarded
    reader = PublishedParquetFrameReader(contract, plan)
    original = reader._read_feature_frame

    def altered(mapping):
        result = original(mapping)
        evidence.write_text("changed")
        return result

    monkeypatch.setattr(reader, "_read_feature_frame", altered)
    with pytest.raises(ValueError, match="changed|hash|identity"):
        reader.load(["000001.SZ"], "2026-06-08", "2026-06-08 23:59")


def test_policy_changes_content_fingerprint(guarded):
    contract, plan, _, _ = guarded
    default = PublishedParquetFrameReader(contract, plan)
    excluded = PublishedParquetFrameReader(contract, plan, quality_mode="exclude_stock_days")
    assert default.metadata["content_fingerprint"] != excluded.metadata["content_fingerprint"]


def test_aware_request_uses_shanghai_trade_day(guarded):
    contract, plan, _, _ = guarded
    with pytest.raises(ValueError, match="hard-1"):
        PublishedParquetFrameReader(contract, plan).load(
            ["600608.SH"], "2026-06-08T01:00:00Z", "2026-06-08T02:00:00Z"
        )


def _rewrite_registry(guarded, update):
    contract, plan, registry, evidence = guarded
    payload = json.loads(registry.read_text())
    update(payload["assets"]["minute"])
    registry.write_text(json.dumps(payload))
    header = json.loads(contract.path.read_text())
    header["contract"]["minute_quality"]["registry_sha256"] = _hash(registry)
    contract.path.write_text(json.dumps(header))
    return PublishedAssetContract.load(contract.path, artifacts_root=contract.artifacts_root), plan


@pytest.mark.parametrize(
    "field,value",
    [
        ("manifest_sha256", "0" * 64),
        ("resolved_path", "/tmp"),
        ("expected_dates", ["20260230"]),
        ("available_dates", ["20260611"]),
    ],
)
def test_invalid_registry_binding_rejected(guarded, field, value):
    contract, plan = _rewrite_registry(guarded, lambda entry: entry.update({field: value}))
    with pytest.raises(ValueError):
        PublishedParquetFrameReader(contract, plan)


def test_evidence_cannot_escape_root(guarded, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text("{}")

    def update(entry):
        entry["blocked_stock_days"][0]["evidence_path"] = str(outside)

    contract, plan = _rewrite_registry(guarded, update)
    try:
        with pytest.raises(ValueError, match="root"):
            PublishedParquetFrameReader(contract, plan)
    finally:
        outside.unlink()


def test_aware_clean_selection_preserves_original_naive_index(guarded):
    contract, plan, _, _ = guarded
    result = PublishedParquetFrameReader(contract, plan).load(
        ["000001.SZ"], "2026-06-08T01:40Z", "2026-06-08T01:42Z"
    )
    assert result.index[0] == (pd.Timestamp("2026-06-08 09:41"), "000001.SZ")
    assert result.iloc[0, 0] == 34.0


def test_mapping_spans_and_generator_selection(guarded):
    contract, plan, _, _ = guarded
    reader = PublishedParquetFrameReader(contract, plan, acknowledged_missing_dates=["20260609"])
    result = reader.load({"600608.SH": [("2026-06-10", "2026-06-10 23:59")]})
    assert len(result) == 1
    result = reader.load(iter(["000001.SZ"]), "2026-06-08", "2026-06-08 23:59")
    assert len(result) == 1


def test_unguarded_contract_retains_metadata_and_ignores_guard_policy(guarded):
    contract, plan, _, _ = guarded
    payload = json.loads(contract.path.read_text())
    del payload["contract"]["minute_quality"]
    contract.path.write_text(json.dumps(payload))
    contract = PublishedAssetContract.load(contract.path, artifacts_root=contract.artifacts_root)
    plain = PublishedParquetFrameReader(contract, plan)
    ignored = PublishedParquetFrameReader(contract, plan, quality_mode="unused")
    assert plain.metadata == ignored.metadata
    assert "minute_quality" not in plain.metadata
    assert len(plain.load()) == 4


def test_diagnostic_marker_in_block_list_never_excludes(guarded):
    def update(entry):
        entry["blocked_stock_days"][0]["severity"] = "diagnostic"

    contract, plan = _rewrite_registry(guarded, update)
    result = PublishedParquetFrameReader(contract, plan).load(
        ["600608.SH"], "2026-06-08", "2026-06-08 23:59"
    )
    assert len(result) == 2
    assert not result.attrs["minute_quality"]["excluded_stock_days"]


def test_result_receipt_cannot_mutate_reader_policy(guarded):
    contract, plan, _, _ = guarded
    reader = PublishedParquetFrameReader(
        contract, plan, quality_mode="exclude_stock_days", acknowledged_missing_dates=["20260609"]
    )
    before = reader.metadata["content_fingerprint"]
    reader.load().attrs["minute_quality"]["acknowledged_missing_dates"].clear()
    assert reader.metadata["content_fingerprint"] == before


def test_explicit_bj_request_rejected_for_sh_sz_asset(guarded):
    contract, plan, _, _ = guarded
    with pytest.raises(ValueError, match="scope"):
        PublishedParquetFrameReader(contract, plan).load(
            ["430047.BJ"], "2026-06-10", "2026-06-10 23:59"
        )


@pytest.mark.parametrize("scope", ["SH_SZ", "SH_SZ_BJ", ["SH", "SZ"], ["SH", "SZ", "BJ"]])
def test_supported_market_scope_shapes(guarded, scope):
    contract, plan = _rewrite_registry(guarded, lambda entry: entry.update(market_scope=scope))
    result = PublishedParquetFrameReader(contract, plan).load(
        ["000001.SZ"], "2026-06-08", "2026-06-08 23:59"
    )
    assert result.attrs["minute_quality"]["market_scopes"]["minute"][:2] == ["SH", "SZ"]


@pytest.mark.parametrize("scope", ["", "SH", [], ["XX"], ["SH", "SH"], {"SH": True}, [None]])
def test_invalid_market_scope_rejected(guarded, scope):
    contract, plan = _rewrite_registry(guarded, lambda entry: entry.update(market_scope=scope))
    with pytest.raises(ValueError, match="market_scope"):
        PublishedParquetFrameReader(contract, plan)


@pytest.mark.parametrize("field", ["expected_dates", "available_dates"])
def test_unhashable_date_list_rejected_with_quality_error(guarded, field):
    from quant_market_data_platform.minute_quality import MinuteQualityError

    contract, plan = _rewrite_registry(guarded, lambda entry: entry.update({field: [{}]}))
    with pytest.raises(MinuteQualityError, match="dates"):
        PublishedParquetFrameReader(contract, plan)


def test_partition_replacement_before_load_rejected(guarded):
    contract, plan, _, _ = guarded
    reader = PublishedParquetFrameReader(contract, plan)
    path = contract.require_assets(["minute"])[0].resolved_path / "data/part.parquet"
    frame = pd.read_parquet(path)
    frame["amount"] *= 2
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="hash|identity"):
        reader.load(["000001.SZ"], "2026-06-08", "2026-06-08 23:59")


def test_partition_drift_during_read_rejected(guarded, monkeypatch):
    contract, plan, _, _ = guarded
    reader = PublishedParquetFrameReader(contract, plan)
    original = reader._read_feature_frame

    def changed(mapping):
        frame = original(mapping)
        path = contract.require_assets(["minute"])[0].resolved_path / "data/part.parquet"
        path.write_bytes(b"changed")
        return frame

    monkeypatch.setattr(reader, "_read_feature_frame", changed)
    with pytest.raises(ValueError, match="hash|identity"):
        reader.load(["000001.SZ"], "2026-06-08", "2026-06-08 23:59")


def test_missing_partition_hash_rejected(guarded):
    contract, plan = _rewrite_registry(guarded, lambda entry: entry.update(partition_sha256={}))
    with pytest.raises(ValueError, match="partition"):
        PublishedParquetFrameReader(contract, plan)


def test_missing_dates_intersect_instrument_validity_spans(guarded):
    contract, plan, _, _ = guarded
    reader = PublishedParquetFrameReader(contract, plan)
    assert len(reader.load({"600608.SH": [("2026-06-10", "2026-06-10 23:59")]})) == 1
    with pytest.raises(ValueError, match="20260609"):
        reader.load({"NONEXISTENT": [("2026-06-09", "2026-06-09 23:59")]})
    with pytest.raises(ValueError, match="20260609"):
        reader.load(
            {
                "600608.SH": [("2026-06-10", "2026-06-10 23:59")],
                "NONEXISTENT": [("2026-06-09", "2026-06-09 23:59")],
            }
        )


def test_actual_out_of_scope_frame_rejected_without_explicit_instruments(guarded):
    contract, plan, _, _ = guarded
    path = contract.require_assets(["minute"])[0].resolved_path / "data/part.parquet"
    frame = pd.read_parquet(path)
    frame.loc[2, "symbol"] = "430047.BJ"
    frame.to_parquet(path, index=False)
    contract, plan = _rewrite_registry(
        guarded, lambda entry: entry.update(partition_sha256={"data/part.parquet": _hash(path)})
    )
    with pytest.raises(ValueError, match="scope"):
        PublishedParquetFrameReader(contract, plan, quality_mode="exclude_stock_days").load(
            start_time="2026-06-08", end_time="2026-06-08 23:59"
        )


def test_unmapped_history_is_not_hashed_or_required(guarded):
    contract, plan, _, _ = guarded
    root = contract.require_assets(["minute"])[0].resolved_path
    unrelated = root / "unmapped-history.parquet"
    unrelated.write_bytes(b"not parquet and not bound")
    file_plan = PublishedFramePlan(
        (
            ParquetFrameMapping(
                "minute", "time", "symbol", {"amount": "amount"}, relative_path="data/part.parquet"
            ),
        )
    )
    reader = PublishedParquetFrameReader(contract, file_plan)
    assert len(reader.load(["000001.SZ"], "2026-06-08", "2026-06-08 23:59")) == 1


def test_added_mapped_partition_rejected(guarded):
    contract, plan, _, _ = guarded
    reader = PublishedParquetFrameReader(contract, plan)
    root = contract.require_assets(["minute"])[0].resolved_path
    (root / "data/new.parquet").write_bytes(b"new file")
    with pytest.raises(ValueError, match="inventory"):
        reader.load(["000001.SZ"], "2026-06-08", "2026-06-08 23:59")


def test_nested_partition_requires_binding(guarded):
    contract, plan, _, _ = guarded
    root = contract.require_assets(["minute"])[0].resolved_path
    nested = root / "data/trade_date=20260610/part.parquet"
    nested.parent.mkdir()
    nested.write_bytes(b"unbound")
    with pytest.raises(ValueError, match="partition_sha256"):
        PublishedParquetFrameReader(contract, plan)


def test_covered_global_window_does_not_include_outside_instrument_spans(guarded):
    contract, plan, _, _ = guarded
    reader = PublishedParquetFrameReader(contract, plan)
    assert reader.load(
        {"NONEXISTENT": [("2026-06-10", "2026-06-10 23:59")]}, "2026-06-09", "2026-06-10 23:59"
    ).empty


def test_mapped_directory_symlink_cannot_hide_unbound_partitions(guarded):
    contract, plan, _, _ = guarded
    root = contract.require_assets(["minute"])[0].resolved_path
    external = root / "other"
    external.mkdir()
    (external / "part.parquet").write_bytes(b"unbound")
    (root / "data/hidden").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="partition"):
        PublishedParquetFrameReader(contract, plan)


def test_mapping_must_cover_requested_registered_available_dates(guarded):
    contract, plan, _, _ = guarded
    root = contract.require_assets(["minute"])[0].resolved_path
    day_path = root / "data/day08.parquet"
    frame = pd.read_parquet(root / "data/part.parquet")
    frame.loc[frame.time.dt.day == 8].to_parquet(day_path, index=False)

    def update(entry):
        entry["partition_sha256"]["data/day08.parquet"] = _hash(day_path)

    contract, _ = _rewrite_registry(guarded, update)
    day_plan = PublishedFramePlan(
        (
            ParquetFrameMapping(
                "minute", "time", "symbol", {"amount": "amount"}, relative_path="data/day08.parquet"
            ),
        )
    )
    reader = PublishedParquetFrameReader(
        contract,
        day_plan,
        quality_mode="exclude_stock_days",
        acknowledged_missing_dates=["20260609"],
    )
    with pytest.raises(ValueError, match="mapping.*20260610"):
        reader.load(["NONEXISTENT"], "2026-06-08", "2026-06-10 23:59")
    assert reader.load(["NONEXISTENT"], "2026-06-08", "2026-06-08 23:59").empty
    assert reader.load({"NONEXISTENT": [("2026-06-08", "2026-06-08 23:59")]}).empty
