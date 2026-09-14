"""Report expected Beijing omissions without granting full-market completeness."""

from pathlib import Path
from typing import Any

from market_data_platform._tushare_minute_campaign_readiness import file_sha256
from market_data_platform.minute_candidate import _atomic_write_json, _read_json
from market_data_platform.providers._a_share_mins_partition import _partition_binding_matches


def bj_missing_report(sidecar: Path, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Accept only absent BJ responses with intact, complete remaining symbols."""
    if payload.get("schema_version") != "tushare.a_share.minute_partition.v3":
        return None
    if payload.get("status") != "partial" or payload.get("freq") != "1min":
        return None
    expected = set(payload.get("expected_symbols", []))
    completed = set(payload.get("completed_symbols", []))
    missing = set(payload.get("missing_request_symbols", []))
    partition = payload.get("partition") or {}
    error = payload.get("error") or {}
    if (
        not completed
        or not missing
        or not completed.issubset(expected)
        or missing != expected - completed
        or any(not code.endswith(".BJ") for code in missing)
        or error.get("type") != "IncompleteBatch"
        or error.get("issues") != dict.fromkeys(missing, "missing from response")
        or set(partition.get("symbols", [])) != completed
        or set(partition.get("complete_symbols", [])) != completed
        or partition.get("rows") != 241 * len(completed)
        or payload.get("expected_bars_per_symbol") != 241
        or sidecar.parent.name != f"trade_date={payload.get('trade_date')}"
        or not _partition_binding_matches(payload, sidecar.parent)
    ):
        return None
    return {
        "trade_date": payload["trade_date"],
        "missing_bj_symbols": sorted(missing),
        "completed_symbols": len(completed),
        "rows": partition["rows"],
        "sidecar": str(sidecar),
        "sidecar_sha256": file_sha256(sidecar),
    }


def scan_bj_missing(staging: Path) -> list[dict[str, Any]]:
    reports = []
    for sidecar in sorted(staging.glob("runs/*/data/trade_date=*/_minute_mirror.json")):
        try:
            report = bj_missing_report(sidecar, _read_json(sidecar))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if report is not None:
            reports.append(report)
    return reports


def persist_bj_report(path: Path, reports: list[dict[str, Any]]) -> None:
    _atomic_write_json(
        path,
        {
            "schema_version": "tushare.reverse_backfill.bj_missing_report.v1",
            "bj_missing_policy": "report",
            "status": "accepted_bj_missing" if reports else "no_accepted_bj_missing",
            "full_market_complete": False,
            "writes_production": False,
            "partitions": reports,
        },
    )
