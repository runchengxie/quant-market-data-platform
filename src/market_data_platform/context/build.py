from __future__ import annotations

import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from market_data_platform.context.models import (
    normalize_context_catalog,
    validate_context_observations,
)
from market_data_platform.context.source_payload import SourcePayload
from market_data_platform.providers.nbs_context import nbs_context_catalog, parse_nbs_context
from market_data_platform.providers.nea_context import (
    nea_context_catalog,
    parse_nea_electricity_release,
)
from market_data_platform.providers.tushare_context import (
    normalize_tushare_context,
    tushare_context_catalog,
)

RELEASE_CALENDAR_COLUMNS = (
    "data_api",
    "issuing_org",
    "month",
    "publish_date",
    "title",
    "provider",
    "source_locator",
    "source_retrieved_at",
)


@dataclass(frozen=True)
class ContextBuildFrames:
    catalog: pd.DataFrame
    observations: pd.DataFrame
    pit: pd.DataFrame
    release_calendar: pd.DataFrame
    lineage: tuple[Mapping[str, Any], ...]


def raw_context_root(artifacts_root: Path) -> Path:
    return artifacts_root / "assets" / "context" / "cn" / "raw"


def snapshot_dirs(artifacts_root: Path) -> list[Path]:
    root = raw_context_root(artifacts_root)
    if not root.exists():
        return []
    return sorted(
        path.parent
        for path in root.glob("*/*/vintage=*/receipt.json")
        if (path.parent / "raw.bin").is_file()
    )


def read_snapshot(snapshot: Path) -> tuple[dict[str, Any], bytes]:
    receipt = json.loads((snapshot / "receipt.json").read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError(f"context snapshot receipt must be an object: {snapshot}")
    return receipt, (snapshot / "raw.bin").read_bytes()


def _receipt_time(receipt: Mapping[str, Any]) -> pd.Timestamp:
    value = pd.Timestamp(receipt.get("retrieved_at"))  # ty: ignore[invalid-argument-type]
    if value.tzinfo is None:
        return value.tz_localize("UTC")  # ty: ignore[invalid-return-type]
    return value.tz_convert("UTC")


def _release_calendar(records: list[tuple[dict[str, Any], bytes]]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for receipt, body in records:
        if receipt.get("provider") != "tushare" or receipt.get("dataset") != "cn_schedule":
            continue
        raw = pd.read_csv(io.BytesIO(body))
        _, releases = normalize_tushare_context(
            "cn_schedule",
            raw,
            retrieved_at=_receipt_time(receipt).to_pydatetime(),
            source_hash=str(receipt["sha256"]),
        )
        if not releases.empty:
            frames.append(releases)
    if not frames:
        return pd.DataFrame(columns=list(RELEASE_CALENDAR_COLUMNS))  # ty: ignore[invalid-argument-type]
    combined = pd.concat(frames, ignore_index=True)
    for column in RELEASE_CALENDAR_COLUMNS:
        if column not in combined:
            combined[column] = pd.NA
    return combined.loc[:, list(RELEASE_CALENDAR_COLUMNS)].drop_duplicates().reset_index(drop=True)


def _source_payload(receipt: Mapping[str, Any], body: bytes) -> SourcePayload:
    request_metadata = receipt.get("request_metadata")
    metadata = dict(request_metadata) if isinstance(request_metadata, Mapping) else {}
    return SourcePayload(
        provider=str(receipt.get("provider") or ""),
        dataset=str(receipt.get("dataset") or ""),
        source_locator=str(receipt.get("source_locator") or ""),
        retrieved_at=_receipt_time(receipt).to_pydatetime(),
        content_type=str(receipt.get("content_type") or "application/octet-stream"),
        body=body,
        metadata=metadata,
    )


def _normalize_record(
    receipt: Mapping[str, Any],
    body: bytes,
    release_calendar: pd.DataFrame,
) -> pd.DataFrame:
    provider = str(receipt.get("provider") or "")
    dataset = str(receipt.get("dataset") or "")
    if provider == "tushare":
        if dataset == "cn_schedule":
            return pd.DataFrame()
        raw = pd.read_csv(io.BytesIO(body))
        observations, _ = normalize_tushare_context(
            dataset,
            raw,
            retrieved_at=_receipt_time(receipt).to_pydatetime(),
            source_hash=str(receipt["sha256"]),
            release_calendar=release_calendar,
        )
        return observations
    payload = _source_payload(receipt, body)
    if provider == "nbs":
        return parse_nbs_context(payload)
    if provider == "nea":
        return parse_nea_electricity_release(payload)
    raise ValueError(f"unsupported context snapshot provider: {provider}")


def _assign_revision_numbers(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    ordered = frame.sort_values(
        ["series_id", "period_end", "source_retrieved_at", "vintage_id"], kind="stable"
    ).copy()
    ordered["revision_number"] = (
        ordered.groupby(["series_id", "period_end"], sort=False).cumcount().astype(int)
    )
    return ordered.reset_index(drop=True)


def _lineage(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider": receipt.get("provider"),
        "dataset": receipt.get("dataset"),
        "source_locator": receipt.get("source_locator"),
        "retrieved_at": receipt.get("retrieved_at"),
        "sha256": receipt.get("sha256"),
        "parser_version": receipt.get("parser_version"),
    }


def _catalog() -> pd.DataFrame:
    combined = pd.concat(
        [tushare_context_catalog(), nbs_context_catalog(), nea_context_catalog()],
        ignore_index=True,
    )
    return normalize_context_catalog(combined)


def build_context_frames(
    artifacts_root: str | Path,
    *,
    as_of: str | pd.Timestamp,
) -> ContextBuildFrames:
    root = Path(artifacts_root).expanduser().resolve()
    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")

    snapshots = snapshot_dirs(root)
    if not snapshots:
        raise FileNotFoundError(f"no sealed context snapshots found under {raw_context_root(root)}")
    all_records = [read_snapshot(snapshot) for snapshot in snapshots]
    records = [item for item in all_records if _receipt_time(item[0]) <= cutoff]
    if not records:
        raise ValueError(f"no context raw snapshots had been retrieved by {cutoff.isoformat()}")  # ty: ignore[unresolved-attribute]
    release_calendar = _release_calendar(records)

    observation_frames: list[pd.DataFrame] = []
    lineages: list[dict[str, Any]] = []
    for receipt, body in records:
        lineages.append(_lineage(receipt))
        observations = _normalize_record(receipt, body, release_calendar)
        if not observations.empty:
            observation_frames.append(observations)
    if not observation_frames:
        raise ValueError("sealed context snapshots produced no normalized observations")

    observations = validate_context_observations(
        _assign_revision_numbers(pd.concat(observation_frames, ignore_index=True))
    )
    # Preserve every vintage that was physically retrieved and visible by this build's
    # as-of. Later as-of readers choose the then-latest revision for each period.
    pit = observations.loc[
        (observations["source_retrieved_at"] <= cutoff) & (observations["available_at"] <= cutoff)
    ].reset_index(drop=True)
    if pit.empty:
        raise ValueError(f"no context observations are safely visible as of {cutoff.isoformat()}")  # ty: ignore[unresolved-attribute]

    release_calendar = release_calendar.copy()
    if not release_calendar.empty:
        release_calendar["source_retrieved_at"] = pd.to_datetime(
            release_calendar["source_retrieved_at"], utc=True, errors="coerce"
        )
        release_calendar = release_calendar.loc[
            release_calendar["source_retrieved_at"].notna()
            & (release_calendar["source_retrieved_at"] <= cutoff)
        ].reset_index(drop=True)

    return ContextBuildFrames(
        catalog=_catalog(),
        observations=observations,
        pit=pit,
        release_calendar=release_calendar,
        lineage=tuple(lineages),
    )
