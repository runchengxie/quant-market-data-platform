"""Private manifest helpers for A-share minute coverage."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from market_data_platform.providers._coverage_common import (
    _ANNUAL_FULL_TIME_MAX,
    _DEAL_FULL_TIME_MIN,
    ExpectedPartitionStats,
    GuanDealCoverageResult,
    _validate_date,
)
from market_data_platform.providers._coverage_manifest_part01 import (
    _clock_text,
    _first_record_value,
    _guan_deal_manifest_inventory,
    _structured_payload,
    _timestamp_text,
)


def _guan_deal_output_sha256(
    output_signature: Any,
    aggregation: Mapping[str, Any],
    trade_date: str,
) -> str | None:
    if output_signature is None:
        return None
    if not isinstance(output_signature, Mapping):
        raise ValueError(f"Guan deal manifest has malformed output signature: {trade_date}")
    if int(output_signature.get("rows", -1)) != int(aggregation["output_rows"]):
        raise ValueError(f"Guan deal output signature row count changed: {trade_date}")
    raw_output_sha256 = output_signature.get("sha256")
    if raw_output_sha256 is None:
        raise ValueError(f"Guan deal output signature lacks SHA-256: {trade_date}")
    return str(raw_output_sha256)


def _guan_deal_action_is_override(
    record: Mapping[str, Any],
    session_validation: Any,
    trade_date: str,
) -> bool:
    is_override = record.get("replacement_policy") == "explicit_whole_day_deal_only"
    if not is_override:
        return False
    if record.get("replaced_source") != "guan_annual_minbar":
        raise ValueError(f"Guan deal override has wrong replaced source for {trade_date}")
    if not isinstance(session_validation, Mapping):
        raise ValueError(f"Guan deal override lacks session validation for {trade_date}")
    issues = session_validation.get("issues")
    if not (
        session_validation.get("profile") == "guan_deal_full_session"
        and _clock_text(session_validation.get("time_min")) == _DEAL_FULL_TIME_MIN
        and _clock_text(session_validation.get("time_max")) == _ANNUAL_FULL_TIME_MAX
        and int(session_validation.get("opening_bar_rows", 0)) > 0
        and int(session_validation.get("opening_bar_symbols", 0)) > 0
        and int(session_validation.get("closing_bar_rows", 0)) > 0
        and int(session_validation.get("closing_bar_symbols", 0)) > 0
        and isinstance(issues, Mapping)
        and int(issues.get("unexpected_market_time_min", -1)) == 0
        and int(issues.get("unexpected_market_time_max", -1)) == 0
        and int(issues.get("missing_opening_execution_bar", -1)) == 0
        and int(issues.get("missing_closing_execution_bar", -1)) == 0
        and session_validation.get("valid") is True
    ):
        raise ValueError(f"Guan deal override has an invalid full-session receipt for {trade_date}")
    return True


def _guan_deal_record_stats(
    record: Any,
) -> tuple[str, ExpectedPartitionStats, bool] | None:
    if not isinstance(record, Mapping) or record.get("status") != "written":
        return None
    trade_date = _validate_date(str(record.get("date", "")))
    aggregation = record.get("aggregation")
    if not isinstance(aggregation, Mapping):
        raise ValueError(f"Guan deal manifest lacks aggregation stats for {trade_date}")
    required = (
        "output_rows",
        "output_symbols",
        "output_traded_symbols",
        "output_vol_sum",
        "output_amount_sum",
        "unit_profile",
    )
    missing = [name for name in required if aggregation.get(name) is None]
    if missing:
        raise ValueError(f"Guan deal manifest lacks strict stats for {trade_date}: {missing}")
    session_validation = record.get("session_validation")
    output_sha256 = _guan_deal_output_sha256(
        record.get("output_signature"),
        aggregation,
        trade_date,
    )
    is_override = _guan_deal_action_is_override(record, session_validation, trade_date)
    stats = ExpectedPartitionStats(
        rows=int(aggregation["output_rows"]),
        symbols=int(aggregation["output_symbols"]),
        traded_symbols=int(aggregation["output_traded_symbols"]),
        vol_sum=float(aggregation["output_vol_sum"]),
        amount_sum=float(aggregation["output_amount_sum"]),
        unit_profile=str(aggregation["unit_profile"]),
        time_min=_timestamp_text(
            (
                session_validation.get("time_min")
                if isinstance(session_validation, Mapping)
                else None
            )
            or _first_record_value(record, ("time_min",))
            or _first_record_value(aggregation, ("time_min",))
        ),
        time_max=_timestamp_text(
            (
                session_validation.get("time_max")
                if isinstance(session_validation, Mapping)
                else None
            )
            or _first_record_value(record, ("time_max",))
            or _first_record_value(aggregation, ("time_max",))
        ),
        output_sha256=output_sha256,
    )
    return trade_date, stats, is_override


def load_guan_deal_manifest(
    manifest_path: str | Path,
) -> GuanDealCoverageResult:
    """Load independently recorded output stats from a Guan deal build."""
    path = Path(manifest_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Guan deal manifest not found: {path}")
    payload = _structured_payload(path)
    records, override_dates = _guan_deal_manifest_inventory(payload, path)

    dates: set[str] = set()
    stats: dict[str, ExpectedPartitionStats] = {}
    action_override_dates: set[str] = set()
    for record in records:
        parsed = _guan_deal_record_stats(record)
        if parsed is None:
            continue
        trade_date, partition_stats, is_override = parsed
        if is_override:
            action_override_dates.add(trade_date)
        dates.add(trade_date)
        stats[trade_date] = partition_stats
    if not dates:
        raise ValueError(f"Guan deal manifest contains no written deal dates: {path}")
    if action_override_dates != override_dates:
        raise ValueError(
            "Guan deal override actions do not match options.annual_override_dates: "
            f"missing={sorted(override_dates - action_override_dates)}, "
            f"unexpected={sorted(action_override_dates - override_dates)}"
        )
    return GuanDealCoverageResult(dates=dates, stats=stats, override_dates=override_dates)
