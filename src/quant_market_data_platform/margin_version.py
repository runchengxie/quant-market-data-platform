"""Validation and integrity for opt-in financing publications (never legacy mirrors)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

DATASETS = ("margin", "margin_detail")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Inventory root must be a physical directory")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Inventory cannot contain symlinks")
        if path.is_file() and str(path.relative_to(root)) not in {
            "inventory.json",
            "publication-receipt.json",
        }:
            result[str(path.relative_to(root))] = file_hash(path)
    return result


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def validate_lineage(path: Path) -> dict[str, Any]:
    lineage = read_json(path)
    if (
        lineage.get("schema_version") != "tushare.margin.sources.v1"
        or lineage.get("historical_pit_certified") is not False
    ):
        raise ValueError("Explicit non-PIT source lineage is required")
    inputs = lineage.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("Source lineage inputs are required")
    for item in inputs:
        if not isinstance(item, dict) or "observed_at" not in item:
            raise ValueError("Source observation time must be explicit, including unknown/null")
        source = Path(item["path"])
        if source.is_symlink() or not source.is_file() or file_hash(source) != item.get("sha256"):
            raise ValueError("Source input hash mismatch")
        if item["observed_at"] is not None:
            observed = datetime.fromisoformat(item["observed_at"])
            if observed.tzinfo is None:
                raise ValueError("Source observation time requires timezone")
            if observed > datetime.now(UTC) + timedelta(minutes=5):
                raise ValueError("Source observation time is in the future")
    return lineage


def validate_candidate(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = yaml.safe_load((root / "manifest.yml").read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("dataset") not in DATASETS:
        raise ValueError("Unsupported financing dataset")
    claims = (
        "historical_pit_certified",
        "full_market_certified",
        "full_market_ready",
        "full_ready",
    )
    if any(raw.get(key) not in (None, False) for key in claims):
        raise ValueError("Unverified upstream certification claim")
    dataset = raw["dataset"]
    if (
        raw.get("schema_version") != f"tushare.{dataset}.v1"
        or raw.get("provider") != "tushare"
        or raw.get("market") != "a_share"
        or raw.get("status") != "completed"
    ):
        raise ValueError("Invalid native candidate manifest")
    query = raw.get("query") or {}
    start, end = str(query.get("start_date", "")), str(query.get("end_date", ""))
    if not re.fullmatch(r"[0-9]{8}", start) or not re.fullmatch(r"[0-9]{8}", end):
        raise ValueError("Invalid query date format")
    datetime.strptime(start, "%Y%m%d")
    datetime.strptime(end, "%Y%m%d")
    if start > end:
        raise ValueError("Invalid query bounds")
    files = sorted((root / "data").glob("trade_date=*/part.parquet"))
    if set((root / "data").rglob("*parquet")) != set(files):
        raise ValueError("Unexpected candidate data layout")
    if not files:
        raise ValueError("No candidate partitions")
    rows = 0
    nulls: dict[str, int] = {}
    schemas = []
    for path in files:
        import pyarrow.parquet as pq

        schema = pq.read_schema(path)
        if schemas and not schema.equals(schemas[0]):
            raise ValueError("Inconsistent Arrow partition schema")
        schemas.append(schema)
        rows += _partition_quality(path, dataset, (start, end), nulls)
    totals = raw.get("totals") or {}
    if totals.get("rows") != rows or totals.get("files") != len(files):
        raise ValueError("Native candidate totals mismatch")
    return raw, {
        "rows": rows,
        "files": len(files),
        "null_counts": nulls,
        "full_market_certified": False,
        "historical_pit_certified": False,
    }


def verify_version_identity(target: Path, expected_sha256: str) -> dict[str, Any]:
    receipt = target / "publication-receipt.json"
    if receipt.is_symlink():
        raise ValueError("Prepared receipt identity must be physical")
    if file_hash(receipt) != expected_sha256:
        raise ValueError("Version receipt hash mismatch")
    payload = read_json(receipt)
    if (
        payload.get("schema_version") != "tushare.margin.publication.v1"
        or payload.get("status") != "prepared"
    ):
        raise ValueError("Invalid prepared version receipt")
    if (
        payload.get("version") != str(target.absolute())
        or payload.get("dataset") != target.parent.name
        or payload.get("candidate_digest") != target.name
    ):
        raise ValueError("Version identity/path mismatch")
    return payload


def _payload_quality(frame: Any, nulls: dict[str, int]) -> None:
    import pandas as pd

    for column in frame.columns:
        nulls[column] = nulls.get(column, 0) + int(frame[column].isna().sum())
    for column in ("rzye", "rqye", "rzmre", "rzche", "rqyl", "rqmcl", "rqchl", "rzrqye"):
        if column not in frame:
            continue
        if not pd.api.types.is_numeric_dtype(frame[column].dtype):
            raise ValueError("Invalid numeric dtype")
        values = pd.to_numeric(frame[column].dropna(), errors="raise")
        if not all(math.isfinite(value) and value >= 0 for value in values):
            raise ValueError("Invalid numeric payload")


def require_external(path: Path) -> None:
    for parent in (path.resolve(), *path.resolve().parents):
        if (
            (parent / ".git").is_file()
            or (parent / ".git" / "HEAD").is_file()
            or (parent / "pyproject.toml").is_file()
            or (parent / "pyvenv.cfg").is_file()
        ):
            raise ValueError(
                "Artifacts cannot live inside a source checkout or installed environment"
            )


def _partition_quality(
    path: Path, dataset: str, bounds: tuple[str, str], nulls: dict[str, int]
) -> int:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    key = "exchange_id" if dataset == "margin" else "ts_code"
    frame = pd.read_parquet(path)
    required = {"trade_date", key, "rzye", "rzmre", "rzche", "rqye", "rqmcl", "rzrqye", "rqyl"}
    if dataset == "margin_detail":
        required.add("rqchl")
    if not required <= set(frame.columns):
        raise ValueError("Missing required financing schema columns")
    schema = pq.read_schema(path)
    for column in ("trade_date", key, *(["name"] if "name" in frame.columns else [])):
        if not (
            pa.types.is_string(schema.field(column).type)
            or pa.types.is_large_string(schema.field(column).type)
        ):
            raise ValueError("Invalid string key/name schema")
    day = path.parent.name.split("=", 1)[1]
    if not re.fullmatch(r"[0-9]{8}", day):
        raise ValueError("Invalid partition day")
    datetime.strptime(day, "%Y%m%d")
    if not bounds[0] <= day <= bounds[1] or set(frame["trade_date"].astype(str)) != {day}:
        raise ValueError("Partition date outside declared bounds")
    if (
        frame.empty
        or frame[["trade_date", key]].isna().any().any()
        or frame.duplicated(["trade_date", key]).any()
    ):
        raise ValueError("Empty, missing or duplicate partition keys")
    if dataset == "margin" and not set(frame[key]) <= {"SSE", "SZSE", "BSE"}:
        raise ValueError("Unknown margin exchange")
    _payload_quality(frame, nulls)
    return len(frame)


def verify_version(target: Path, expected_sha256: str) -> dict[str, Any]:
    payload = verify_version_identity(target, expected_sha256)
    if file_hash(target / "inventory.json") != payload.get("inventory_sha256") or inventory(
        target
    ) != read_json(target / "inventory.json"):
        raise ValueError("Version inventory mismatch")
    return payload
