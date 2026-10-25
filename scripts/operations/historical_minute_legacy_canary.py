"""Authenticate retained canary acquisition without inventing modern repair records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from quant_market_data_platform._minute_gap_repair_artifacts import _validate
from scripts.operations import historical_minute_coverage_pilot as pilot


def authenticate(
    row: dict[str, Any], resolution: dict[str, Any], paths: dict[str, Path]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if len(row["partitions"]) != 2:
        raise ValueError("Legacy canary requires exactly two candidates")
    raw = paths[resolution["raw"]]
    if pq.ParquetFile(raw).metadata.num_rows != 241:
        raise ValueError("Legacy canary requires one native 241-row raw response")
    record = json.loads(paths[resolution["canary"]].read_text())
    selected, old = [paths[k] for k in row["partitions"]]
    selected_side, old_side = [paths[k] for k in row["candidate_sidecars"]]
    original = paths[resolution["canary_partition"]]
    original_side = paths[resolution["canary_sidecar"]]
    payload = json.loads(original_side.read_text())
    if (
        original != original_side.parent / "part-00000.parquet"
        or payload.get("trade_date") != row["date"]
        or not pilot._partition_binding_matches(payload, original_side.parent)
        or pilot.bind(original)["sha256"] != pilot.bind(selected)["sha256"]
        or pilot.bind(original_side)["sha256"] != pilot.bind(selected_side)["sha256"]
    ):
        raise ValueError("Legacy canary original target differs from retained candidate")
    expected = {
        "date": row["date"],
        "status": "isolated_candidate_ready",
        "source_sidecar": str(old_side),
        "source_hashes": {str(p): pilot.bind(p)["sha256"] for p in [old, old_side]},
        "candidate": str(original.parent),
    }
    if any(record.get(k) != v for k, v in expected.items()) or any(
        record.get(k) is not v
        for k, v in {
            "source_hashes_unchanged": True,
            "writes_production": False,
            "full_market_accepted": False,
        }.items()
    ):
        raise ValueError("Legacy canary source/date/status binding mismatch")
    responses = record["responses"]
    if len(responses) != 1:
        raise ValueError("Legacy canary requires exactly one acquired response")
    response = responses[0]
    if (
        response.get("rows") != 241
        or response.get("semantic_validation") != "passed"
        or response.get("raw_files") != {str(raw): pilot.bind(raw)["sha256"]}
    ):
        raise ValueError("Legacy canary raw response binding mismatch")
    before, after = record["repair_requests_used_before"], record["repair_requests_used_after"]
    if (
        type(before) is not int
        or before < 0
        or type(after) is not int
        or after != before + 1
        or type(record["requests_attempted"]) is not int
        or record["requests_attempted"] != 1
        or type(record["request_limit"]) is not int
        or record["request_limit"] < 1
    ):
        raise ValueError("Legacy canary acquisition counter mismatch")
    validation = record["validation"]
    target = {
        "schema_version": "tushare.a_share.minute_partition.promotion.v1",
        "trade_date": row["date"],
        "partition_path": str(original),
        "sidecar_path": str(original_side),
        "partition_sha256": pilot.bind(selected)["sha256"],
        "sidecar_sha256": pilot.bind(selected_side)["sha256"],
        "expected_bars_per_symbol": 241,
    }
    if any(validation.get(k) != v for k, v in target.items()):
        raise ValueError("Legacy canary target validation binding mismatch")
    patch = _validate(pq.ParquetFile(raw).read().to_pandas(), row["date"], [response["code"]])
    old_frame = pq.ParquetFile(old).read().to_pandas()
    selected_frame = pq.ParquetFile(selected).read().to_pandas()
    if (
        set(old_frame.ts_code) & set(patch.ts_code)
        or validation.get("rows") != len(selected_frame)
        or validation.get("symbols") != selected_frame.ts_code.nunique()
    ):
        raise ValueError("Legacy canary patch must be disjoint and target counts exact")
    cached = record["cached_native_evidence"]
    codes = [entry["code"] for entry in cached]
    if len(codes) != len(set(codes)):
        raise ValueError("Duplicate legacy cached evidence")
    for entry in cached:
        if (
            entry.get("rows") != 241
            or entry.get("semantic_validation") != "passed"
            or entry.get("source") != "existing_raw_partition"
            or type(entry.get("provider_requests")) is not int
            or entry["provider_requests"] != 0
        ):
            raise ValueError("Legacy cached evidence mismatch")
        frame = old_frame.loc[old_frame.ts_code.eq(entry["code"])]
        _validate(frame, row["date"], [entry["code"]])
    return selected_frame, old_frame, patch
