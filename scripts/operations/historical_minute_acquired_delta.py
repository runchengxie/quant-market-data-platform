"""Corroborate retained multi-symbol deltas using explicitly new raw acquisition."""

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from quant_market_data_platform._minute_gap_repair_artifacts import _validate
from quant_market_data_platform.providers._a_share_mins_constants import DEFAULT_MINS_FIELDS
from scripts.operations import historical_minute_coverage_pilot as pilot
from scripts.operations import historical_minute_delta_quota as quota


def authenticate(row: dict[str, Any], resolution: dict[str, Any], paths: dict[str, Path]):
    try:
        return _authenticate(row, resolution, paths)
    except (KeyError, TypeError, AssertionError) as exc:
        raise ValueError("Malformed acquired delta evidence") from exc


def _authenticate(row: dict[str, Any], resolution: dict[str, Any], paths: dict[str, Path]):
    acquired = json.loads(paths[resolution["acquisition"]].read_text())
    binding = json.loads(paths[resolution["quota_binding"]].read_text())
    if binding["acquisition"] != pilot.bind(paths[resolution["acquisition"]]):
        raise ValueError("Quota snapshot does not bind final acquisition")
    selected, old = [pq.ParquetFile(paths[k]).read().to_pandas() for k in row["partitions"]]
    sources = [*row["partitions"], *row["candidate_sidecars"]]
    expected_sources = {str(paths[k]): pilot.bind(paths[k])["sha256"] for k in sources}
    symbols = sorted(set(selected.ts_code) - set(old.ts_code))
    if (
        acquired["status"] != "all_delta_corroborated"
        or acquired["date"] != row["date"]
        or acquired["writes_production"] is not False
        or acquired["formal_resolution"] is not False
        or acquired["original_bindings_unchanged"] is not True
        or acquired["original_bindings"] != expected_sources
        or acquired["symbols"] != symbols
        or not 0 < len(symbols) == acquired["max_unique_symbols"] <= 1000
        or type(acquired["attempted"]) is not int
        or not len(symbols) <= acquired["attempted"] <= acquired["max_attempts"] <= 2000
        or len(acquired["responses"]) != acquired["attempted"]
        or quota.instant(acquired["started_at"]) > quota.instant(acquired["finished_at"])
    ):
        raise ValueError("Incomplete acquired delta or original source binding")
    if binding["consumer"] != resolution["consumer"]:
        raise ValueError("Unexpected task quota consumer")
    if any(r["params"] != _params(r["code"], row["date"]) for r in acquired["responses"]):
        raise ValueError("Attempted request target or parameters mismatch")
    quota.authenticate(acquired, binding, paths, resolution["retrospective_prefix"])
    successes = [r for r in acquired["responses"] if r.get("exact_match_to_retained_delta") is True]
    if (
        sorted(r["code"] for r in successes) != symbols
        or any(r["code"] not in symbols for r in acquired["responses"])
        or len({r["raw"]["path"] for r in successes}) != len(symbols)
        or any("raw" in r for r in acquired["responses"] if r not in successes)
    ):
        raise ValueError("Missing, repeated or unexplained successful raw response")
    frames = [_response(r, row["date"], paths) for r in successes]
    return selected, old, pd.concat(frames, ignore_index=True)


def _response(response: dict[str, Any], date: str, paths: dict[str, Path]) -> pd.DataFrame:
    code = response["code"]
    params = _params(code, date)
    raw = paths[response["raw"]["path"]]
    actual = pilot.bind(raw)
    if (
        not {"path", "sha256"}.issubset(response["raw"])
        or any(actual[k] != v for k, v in response["raw"].items())
        or response["params"] != params
        or response["rows"] != 241
        or response["semantic_validation"] != "passed"
        or not quota.instant(response["started_at"])
        <= quota.instant(response["received_at"])
        <= quota.instant(response["finished_at"])
        or pq.ParquetFile(raw).metadata.num_rows != 241
    ):
        raise ValueError("Raw acquisition hash, request or timestamp mismatch")
    return _validate(pq.ParquetFile(raw).read().to_pandas(), date, [code])


def _params(code: str, date: str) -> dict[str, Any]:
    return {
        "ts_code": code,
        "freq": "1min",
        "start_date": f"{date} 09:00:00",
        "end_date": f"{date} 15:30:00",
        "fields": ",".join(DEFAULT_MINS_FIELDS),
        "limit": 8000,
    }
