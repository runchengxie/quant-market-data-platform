"""Immutable statement observations; disclosure dates never imply prior observation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

_DATASETS = {
    "income": "income",
    "income_vip": "income",
    "balancesheet": "balancesheet",
    "balancesheet_vip": "balancesheet",
    "cashflow": "cashflow",
    "cashflow_vip": "cashflow",
}


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sources(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text())
    if payload.get("status") != "completed":
        raise ValueError(f"Incomplete source manifest: {path}")
    entries = payload.get("parts", payload.get("entries", []))
    return [entry for entry in entries if entry.get("endpoint") in _DATASETS]


def _observation(entry: dict[str, Any], manifest: Path, delay: int) -> Any:
    import pandas as pd

    if entry.get("status", "completed") != "completed":
        raise ValueError("Incomplete source query")
    source = Path(entry["path"]).expanduser()
    if not source.is_absolute():
        source = manifest.parent / source
    expected = entry.get("content_sha256", entry.get("sha256"))
    if not expected or _digest(source) != expected:
        raise ValueError(f"Source checksum mismatch: {source}")
    stamp = entry.get("retrieved_at", entry.get("request_completed_at"))
    if not stamp or datetime.fromisoformat(stamp).utcoffset() is None:
        raise ValueError("Observation timestamp must include a time zone")
    frame = pd.read_parquet(source)
    if "rows" in entry and len(frame) != entry["rows"]:
        raise ValueError(f"Source row count mismatch: {source}")
    if frame.empty:
        return frame
    required = {"ts_code", "end_date", "ann_date", "report_type"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Missing statement identity columns: {source}")
    announced = pd.to_datetime(frame["ann_date"], format="%Y%m%d", errors="coerce")
    if "f_ann_date" in frame:
        actual = pd.to_datetime(frame["f_ann_date"], format="%Y%m%d", errors="coerce")
        announced = pd.concat([announced, actual], axis=1).max(axis=1)
    if announced.isna().any():
        raise ValueError(f"Missing disclosure dates: {source}")
    observed_date = pd.Timestamp(
        datetime.fromisoformat(stamp).astimezone(ZoneInfo("Asia/Shanghai")).date()
    )
    visible = announced.clip(lower=observed_date) + pd.Timedelta(days=delay)
    frame["available_from"] = visible.dt.strftime("%Y%m%d")
    frame["observed_at"] = stamp
    frame["source_sha256"] = expected
    frame["source_manifest_sha256"] = _digest(manifest)
    frame["dataset"] = _DATASETS[entry["endpoint"]]
    return frame


def build_statement_version_ledger(
    *, source_manifests: list[str], out_dir: str, available_delay_days: int = 1
) -> dict[str, Any]:
    """Write one partition per verified source; refuse to replace any existing asset."""
    if available_delay_days < 1:
        raise ValueError("At least one calendar day of visibility delay is required")
    output = Path(out_dir).expanduser()
    output.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {
        "schema_version": "statement-observations.v1",
        "status": "building",
        "availability_policy": "max(disclosure_date, Shanghai_observation_date) + calendar_delay",
        "available_delay_days": available_delay_days,
        "historical_revision_completeness": False,
        "parts": [],
    }
    try:
        for source_manifest in source_manifests:
            manifest = Path(source_manifest).expanduser().resolve()
            for entry in _sources(manifest):
                frame = _observation(entry, manifest, available_delay_days)
                target = output / f"part-{len(summary['parts']):05d}.parquet"
                frame.to_parquet(target, index=False)
                summary["parts"].append(
                    {"path": target.name, "rows": len(frame), "sha256": _digest(target)}
                )
        if not summary["parts"]:
            raise ValueError("No supported statement observations in supplied manifests")
        summary["status"] = "completed"
        summary["rows"] = sum(part["rows"] for part in summary["parts"])
    except Exception:
        summary["status"] = "failed"
        raise
    finally:
        (output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def read_statement_observations(
    *,
    asset_dir: str,
    as_of: str,
    report_type: str = "1",
    dataset: str | None = None,
    columns: list[str] | None = None,
) -> Any:
    """Read verified observations visible on a date; retain all versions for caller audit."""
    import pandas as pd
    import pyarrow.parquet as pq

    if dataset is not None and dataset not in _DATASETS.values():
        raise ValueError("Unsupported statement dataset")
    if columns is not None and (not columns or len(columns) != len(set(columns))):
        raise ValueError("Projected columns must be non-empty and unique")
    cutoff = datetime.strptime(as_of, "%Y%m%d").strftime("%Y%m%d")
    root = Path(asset_dir).expanduser()
    manifest = json.loads((root / "manifest.json").read_text())
    if (
        manifest.get("schema_version") != "statement-observations.v1"
        or manifest.get("status") != "completed"
    ):
        raise ValueError("A completed statement observation ledger is required")
    frames = []
    for part in manifest["parts"]:
        path = root / part["path"]
        if path.resolve().parent != root.resolve() or _digest(path) != part["sha256"]:
            raise ValueError("Ledger partition path or checksum mismatch")
        if columns is None:
            frame = pd.read_parquet(path)
        else:
            schema = pq.ParquetFile(path)
            if schema.metadata.num_rows == 0:
                continue
            requested = list(dict.fromkeys([*columns, "dataset", "available_from", "report_type"]))
            frame = pd.read_parquet(
                path, columns=[c for c in requested if c in schema.schema_arrow.names]
            )
        if frame.empty:
            continue
        mask = (frame["available_from"] <= cutoff) & (
            frame["report_type"].astype(str) == report_type
        )
        if dataset is not None:
            mask &= frame["dataset"].eq(dataset)
        if mask.any():
            if columns is not None and not set(columns).issubset(frame):
                raise ValueError("Requested columns are absent from visible statement dataset")
            frames.append(frame.loc[mask, columns] if columns is not None else frame.loc[mask])
    return (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=pd.Index(columns or []))
    )
