"""Bounded read-only summaries; incident history is separate from asset freshness."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quant_market_data_platform.tushare_minute_operational import OPERATIONAL_VERSION_SCHEMA

SERVICES = (
    "tushare-minute-operational-daily",
    "tushare-minute-history-watch",
    "market-intel-scheduled-recovery",
    "tushare-fundamentals-vintage-archive",
    "local-fetch-cross-market",
)
DECLARED_FIELDS = (
    "schema_version",
    "status",
    "event_count",
    "conflict_event_count",
    "consensus_event_count",
    "candidate_row_count",
    "partial",
    "reconstructed",
    "historical_pit_certified",
    "complete_current_eligible",
    "full_market_promotion_allowed",
    "acceptance_receipt",
    "dates",
    "passed_dates",
    "unresolved_dates",
    "cross_source_acceptance",
    "source_revision",
)


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite JSON number")
    return result


def _read(path: Path) -> tuple[dict[str, Any], str]:
    with path.open("rb") as source:
        raw = source.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("evidence exceeds size limit")
    value = json.loads(
        raw,
        parse_float=_finite_float,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )
    if not isinstance(value, dict):
        raise ValueError("evidence must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def _age(stamp: Any, now: datetime) -> float:
    observed = datetime.fromisoformat(stamp)
    if observed.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    seconds = (now - observed).total_seconds()
    if seconds < 0:
        raise ValueError("future evidence")
    return seconds


def _error(exc: Exception) -> dict[str, str]:
    return {
        "status": "missing" if isinstance(exc, FileNotFoundError) else "invalid",
        "error_type": type(exc).__name__,
    }


def _minute(root: Path, target: str, now: datetime) -> dict[str, Any]:
    try:
        alias = root / "assets/derived/a_share/minute_1m_tushare"
        contract_path = root / "metadata/current_assets/a_share_current.json"
        contract_as_of = None
        if contract_path.exists():
            contract, _ = _read(contract_path)
            entry = contract["assets"]["minute_1m_tushare"]
            alias = Path(entry["alias_path"])
            if not alias.is_absolute() or alias.resolve() != Path(entry["resolved_path"]).resolve():
                raise ValueError("invalid contract path")
            if entry.get("availability") != "available":
                raise ValueError("asset unavailable in contract")
            contract_as_of = entry.get("as_of")
            if contract_as_of is not None and (
                not isinstance(contract_as_of, str)
                or datetime.strptime(contract_as_of, "%Y%m%d").strftime("%Y%m%d") != contract_as_of
            ):
                raise ValueError("invalid contract date")
        output = alias.resolve(strict=True)
        output.relative_to(root)
        path = output / "_operational_receipt.json"
        receipt, digest = _read(path)
        version = Path(receipt["output_dir"]).resolve(strict=True)
        if version != output:
            if (
                output.name != "minute_1m_tushare"
                or version.parent != output.parent
                or not version.name.startswith("minute_1m_tushare_v")
            ):
                raise ValueError("invalid receipt location")
            _, embedded_digest = _read(version / "_operational_receipt.json")
            if embedded_digest != digest:
                raise ValueError("copied receipt differs from version")
        if (
            receipt["schema_version"] != OPERATIONAL_VERSION_SCHEMA
            or receipt["status"] != "published_operational_version"
        ):
            raise ValueError("invalid operational receipt")
        summary = receipt["summary"]
        day = summary["date_max"]
        if any(type(summary.get(key)) is not int or summary[key] < 0 for key in ("rows", "dates")):
            raise ValueError("invalid receipt counts")
        if receipt.get("quality_status") is not None and not isinstance(
            receipt["quality_status"], str
        ):
            raise ValueError("invalid quality status")
        parsed = datetime.strptime(day, "%Y%m%d")
        if (
            parsed.strftime("%Y%m%d") != day
            or parsed.date() > now.astimezone(ZoneInfo("Asia/Shanghai")).date()
        ):
            raise ValueError("invalid or future receipt date")
        return {
            "status": "current" if day >= target else "lagging",
            "date_max": day,
            "rows": summary.get("rows"),
            "dates": summary.get("dates"),
            "quality_status": receipt.get("quality_status"),
            "contract_as_of": contract_as_of,
            "contract_date_matches": contract_as_of in (None, day),
            "receipt": str(path),
            "sha256": digest,
            "verification": "receipt_only",
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _error(exc)


def _history(root: Path, now: datetime, max_age: float) -> dict[str, Any]:
    path = root / "metadata/minute_backfill/reverse_scheduler/monitor/status.json"
    try:
        data, digest = _read(path)
        age = _age(data["generated_at"], now)
        progress = data["progress"]
        if not isinstance(progress, dict) or not isinstance(data["state"], str):
            raise ValueError("invalid monitor shape")
        status = "stale" if age > max_age else "attention"
        if (
            age <= max_age
            and progress.get("full_market_ready") is True
            and data["state"] in {"download_complete", "download_complete_with_gaps", "idle"}
            and type(progress.get("remaining_open_dates")) is int
            and progress["remaining_open_dates"] == 0
            and type(progress.get("deferred_dates")) is int
            and progress["deferred_dates"] == 0
            and progress.get("semantic_quarantine_dates") == []
        ):
            status = "reported_ready"
        keys = (
            "full_market_ready",
            "remaining_open_dates",
            "deferred_dates",
            "settled_dates",
            "independently_audited_dates",
            "audit_scope_dates",
            "semantic_quarantine_dates",
        )
        return {
            "status": status,
            "state": data["state"],
            "age_seconds": age,
            "generated_at": data["generated_at"],
            "sha256": digest,
            "progress": {key: progress.get(key) for key in keys},
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _error(exc)


def _index_row(path: Path, now: datetime) -> tuple[float, dict[str, Any]]:
    row, _ = _read(path)
    age = _age(row["ended_utc"], now)
    if type(row.get("schema_version")) is not int or type(row.get("exit_code")) is not int:
        raise ValueError("invalid run integer fields")
    for key in ("first_source_status", "cooldown_until", "failure_class"):
        if row.get(key) is not None and not isinstance(row[key], str):
            raise ValueError("invalid run scalar")
    if "confirmed_429" in row and type(row["confirmed_429"]) is not bool:
        raise ValueError("invalid rate limit flag")
    if "duration_seconds" in row and (
        type(row["duration_seconds"]) not in (int, float) or row["duration_seconds"] < 0
    ):
        raise ValueError("invalid run duration")
    recovered = row.get("recovered_indices", [])
    if not isinstance(recovered, list) or any(not isinstance(item, str) for item in recovered):
        raise ValueError("invalid recovered symbols")
    if datetime.strptime(row["date"], "%Y%m%d").strftime("%Y%m%d") != row["date"]:
        raise ValueError("invalid run date")
    if row["schema_version"] != 1 or row["outcome"] not in {
        "complete",
        "partial",
        "failed",
    }:
        raise ValueError("invalid run schema")
    missing = row["final_missing_symbols"]
    if missing is not None and (
        not isinstance(missing, list) or any(not isinstance(item, str) for item in missing)
    ):
        raise ValueError("invalid missing symbols")
    if row["outcome"] == "complete" and (row["exit_code"] != 0 or missing != []):
        raise ValueError("inconsistent complete result")
    return age, row


def _indices(root: Path, target: str, now: datetime, max_age: float) -> dict[str, Any]:
    try:
        paths = list(islice(root.glob("*/summary.json"), 1001))
    except OSError as exc:
        return _error(exc)
    if len(paths) > 1000:
        return {"status": "scan_limit_exceeded", "runs_considered": None}
    rows, invalid = [], 0
    for path in paths:
        try:
            rows.append(_index_row(path, now))
        except (OSError, ValueError, KeyError, TypeError):
            invalid += 1
    rows.sort(key=lambda value: value[0])
    rows = rows[:20]
    status = "invalid" if invalid else "no_samples"
    latest = None
    if rows:
        age, last = rows[0]
        status = "observed" if last["date"] == target else "different_target_date"
        if age > max_age:
            status = "stale"
        if invalid or last["outcome"] != "complete":
            status = "attention"
        latest = {
            key: last.get(key)
            for key in (
                "date",
                "ended_utc",
                "outcome",
                "exit_code",
                "first_source_status",
                "failure_class",
                "final_missing_symbols",
                "recovered_indices",
                "confirmed_429",
                "cooldown_until",
                "duration_seconds",
            )
        }
    return {
        "status": status,
        "runs_considered": len(rows),
        "invalid_records": invalid,
        "complete_runs": sum(row["outcome"] == "complete" for _, row in rows),
        "latest": latest,
        "scope": "latest_20_recorded_runs_not_uptime",
    }


def service_states() -> dict[str, Any]:
    result = {}
    for name in SERVICES:
        try:
            reply = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    f"{name}.service",
                    "-p",
                    "ActiveState",
                    "-p",
                    "Result",
                    "-p",
                    "ExecMainStatus",
                    "-p",
                    "ExecMainExitTimestamp",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            result[name] = dict(
                line.split("=", 1) for line in reply.stdout.splitlines() if "=" in line
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result[name] = {"status": "unavailable", "error_type": type(exc).__name__}
    return result


def _evidence(paths: dict[str, Path]) -> dict[str, Any]:
    result = {}
    for name, path in paths.items():
        try:
            data, digest = _read(path)
            result[name] = {
                "status": "unverified",
                "path": str(path),
                "sha256": digest,
                "declared": {
                    key: data[key]
                    for key in DECLARED_FIELDS
                    if key in data and type(data[key]) in (str, int, float, bool, type(None))
                },
            }
        except (OSError, ValueError, TypeError) as exc:
            result[name] = _error(exc)
    return result


@dataclass(frozen=True)
class OverviewOptions:
    max_age_hours: float = 6
    with_systemd: bool = False
    evidence: dict[str, Path] = field(default_factory=dict)
    production_root: Path | None = None


def build_overview(
    root: Path,
    target_date: str,
    *,
    now: datetime | None = None,
    options: OverviewOptions | None = None,
) -> dict[str, Any]:
    """Inspect bounded local receipts without providers, messages or persistent writes."""
    if datetime.strptime(target_date, "%Y%m%d").strftime("%Y%m%d") != target_date:
        raise ValueError("target date must be YYYYMMDD")
    options = options or OverviewOptions()
    max_age_hours = options.max_age_hours
    now = now or datetime.now(UTC)
    if now.tzinfo is None or not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise ValueError("require aware time and positive finite freshness window")
    root = root.expanduser().resolve()
    minute = _minute(root, target_date, now)
    history = _history(root, now, max_age_hours * 3600)
    indices = _indices(
        root / "reports/market-intel/index-recovery", target_date, now, max_age_hours * 3600
    )
    services = service_states() if options.with_systemd else {}
    versions = {}
    if options.production_root is not None:
        for name in ("quant-market-data-platform", "quant-intel-platform", "quant-intel-deploy"):
            path = options.production_root / name / "current"
            versions[name] = {"path": str(path.resolve()), "exists": path.is_dir()}
    supplemental = _evidence(options.evidence)
    attention = (
        any(row["status"] != "unverified" for row in supplemental.values())
        or any(not row["exists"] for row in versions.values())
        or minute["status"] != "current"
        or minute.get("contract_date_matches") is False
        or history["status"] != "reported_ready"
        or indices["status"] != "observed"
        or any(
            row.get("Result") not in (None, "success") or row.get("status")
            for row in services.values()
        )
    )
    return {
        "schema_version": "data-operations-overview.v1",
        "generated_at": now.isoformat(),
        "expected_date": target_date,
        "needs_attention": attention,
        "minute": minute,
        "history": history,
        "index_recovery": indices,
        "services": services,
        "production_releases": versions,
        "evidence": supplemental,
        "writes_production": False,
        "provider_requests": 0,
        "scope": "receipt_inspection_not_data_acceptance",
    }
