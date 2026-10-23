"""Validate terminal acquisition evidence without claiming audit or publication readiness."""

from datetime import datetime
from pathlib import Path


def completed_pass(evidence: dict, progress: dict, root: Path, now: datetime) -> bool:
    marker = evidence["completion"]
    if not marker or evidence["active"]:
        return False
    try:
        statuses = {
            "first_pass_complete_with_gaps": "download_pass_complete_with_gaps",
            "download_history_exhausted": "noop",
        }
        result = marker["result"]
        expected = statuses[marker["status"]]
        if not isinstance(result, dict) or result.get("status") != expected:
            raise ValueError("completion status mismatch")
        deferred = progress["deferred_dates"]
        if (
            type(deferred) is not int
            or deferred < 0
            or progress["remaining_open_dates"] != deferred
            or type(result.get("deferred_dates")) is not int
            or result["deferred_dates"] != deferred
        ):
            raise ValueError("completion inventory mismatch")
        queue = root / "metadata/minute_backfill/reverse_scheduler/deferred_missing.json"
        if "deferred_queue" in result and (
            Path(result["deferred_queue"]).resolve(strict=True) != queue.resolve(strict=True)
        ):
            raise ValueError("completion queue mismatch")
        if result.get("semantic_quarantine_dates", []) != progress["semantic_quarantine_dates"]:
            raise ValueError("completion quarantine mismatch")
        activity, provenance = evidence["activity"], evidence["provenance"]
        quota_day = evidence["quota"]["quota_date"]
        if not isinstance(quota_day, str) or len(quota_day) != 8 or not quota_day.isdigit():
            raise ValueError("invalid completion quota date")
        day = datetime.strptime(quota_day, "%Y%m%d").date()
        ages = [provenance["age_seconds"], activity["age_seconds"]]
        if (
            day > datetime.strptime(evidence["day"], "%Y%m%d").date()
            or provenance["age_seconds"] is None
            or any(age is not None and age < -60 for age in ages)
            or activity.get("ledger_readable") is not True
            or activity["returned_rows_today"] < 0
            or evidence["completion_path"].stat().st_mtime > now.timestamp() + 60
        ):
            raise ValueError("completion ledger or clock evidence invalid")
    except (KeyError, ValueError, TypeError, OSError) as exc:
        evidence["warnings"].append(f"invalid download completion evidence: {exc}")
        return False
    return True
