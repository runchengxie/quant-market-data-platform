"""Shared price/flow diagnostics for canonical A-share minute bars."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

VWAP_OHLC_ABSOLUTE_TOLERANCE = 0.01
VWAP_OHLC_RELATIVE_TOLERANCE = 1e-6
VWAP_EXTREME_LOW_MULTIPLIER = 0.5
VWAP_EXTREME_HIGH_MULTIPLIER = 2.0
VWAP_SOURCE_DIAGNOSTIC_RELATIVE_TOLERANCE = 0.02
NOTIONAL_HARD_GUARD_RELATIVE_TOLERANCE = 0.10
NOTIONAL_HARD_GUARD_ABSOLUTE_TOLERANCE_CNY = 1.0

VWAP_OHLC_DIAGNOSTIC = "vwap_outside_ohlc_rows"
VWAP_EXTREME_UNIT_SCALE_ISSUE = "extreme_vwap_unit_scale_rows"
# The field name is retained in receipts for compatibility; its disposition is
# source-aware and accepted for TuShare inputs.
VWAP_SOURCE_GUARD_ISSUE = "vwap_beyond_source_guard_rows"
NOTIONAL_HARD_GUARD_ISSUE = "notional_beyond_hard_price_guard_rows"
ZERO_VOLUME_NONZERO_AMOUNT_ISSUE = "zero_volume_nonzero_amount_rows"
POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE = "positive_volume_zero_amount_rows"
VWAP_DIAGNOSTIC_POLICY = "preserve_source_values_no_clipping"
VWAP_DIAGNOSTIC_SOURCES = frozenset({"guan_annual_minbar", "tushare_full_day", "tushare_top200"})
TUSHARE_VWAP_DIAGNOSTIC_SOURCES = frozenset({"tushare_full_day", "tushare_top200"})
VWAP_PRICE_FLOW_INCOMPARABLE_UNIT_PROFILES = frozenset({"guan_lots_yuan"})


def price_flow_diagnostics(numeric: pd.DataFrame) -> dict[str, int]:
    """Return transparent VWAP diagnostics and the independent hard notional guard."""
    required = {"high", "low", "vol", "amount"}
    missing = sorted(required.difference(numeric.columns))
    if missing:
        raise ValueError(f"Price/flow diagnostics require columns: {missing}")

    high = numeric["high"]
    low = numeric["low"]
    volume = numeric["vol"]
    amount = numeric["amount"]
    positive_volume = volume.gt(0)
    positive_amount = amount.gt(0)
    positive_flow = positive_volume & positive_amount
    vwap = amount.div(volume.where(positive_volume))
    price_tolerance = (
        VWAP_OHLC_ABSOLUTE_TOLERANCE
        + pd.concat([high.abs(), low.abs()], axis=1).max(axis=1) * VWAP_OHLC_RELATIVE_TOLERANCE
    )
    hard_lower_notional = (
        low * volume * (1.0 - NOTIONAL_HARD_GUARD_RELATIVE_TOLERANCE)
        - NOTIONAL_HARD_GUARD_ABSOLUTE_TOLERANCE_CNY
    )
    hard_upper_notional = (
        high * volume * (1.0 + NOTIONAL_HARD_GUARD_RELATIVE_TOLERANCE)
        + NOTIONAL_HARD_GUARD_ABSOLUTE_TOLERANCE_CNY
    )
    return {
        VWAP_OHLC_DIAGNOSTIC: int(
            (
                positive_volume & (vwap.lt(low - price_tolerance) | vwap.gt(high + price_tolerance))
            ).sum()
        ),
        VWAP_SOURCE_GUARD_ISSUE: int(
            (
                positive_flow
                & (
                    vwap.lt(low * (1.0 - VWAP_SOURCE_DIAGNOSTIC_RELATIVE_TOLERANCE))
                    | vwap.gt(high * (1.0 + VWAP_SOURCE_DIAGNOSTIC_RELATIVE_TOLERANCE))
                )
            ).sum()
        ),
        NOTIONAL_HARD_GUARD_ISSUE: int(
            (
                positive_volume & (amount.lt(hard_lower_notional) | amount.gt(hard_upper_notional))
            ).sum()
        ),
        VWAP_EXTREME_UNIT_SCALE_ISSUE: int(
            (
                positive_flow
                & (
                    vwap.lt(low * VWAP_EXTREME_LOW_MULTIPLIER)
                    | vwap.gt(high * VWAP_EXTREME_HIGH_MULTIPLIER)
                )
            ).sum()
        ),
        ZERO_VOLUME_NONZERO_AMOUNT_ISSUE: int(((~positive_volume) & positive_amount).sum()),
        POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE: int((positive_volume & ~positive_amount).sum()),
    }


def tushare_price_flow_policy() -> Mapping[str, Any]:
    """Return the canonical receipt contract for TuShare price/flow validation."""
    return {
        "source_values": "preserve_no_clipping",
        "ohlc_vwap_diagnostic": {
            "issue": VWAP_OHLC_DIAGNOSTIC,
            "absolute_tolerance_cny": VWAP_OHLC_ABSOLUTE_TOLERANCE,
            "relative_to_max_abs_high_low": VWAP_OHLC_RELATIVE_TOLERANCE,
            "disposition": "accepted_diagnostic",
        },
        "two_percent_vwap_diagnostic": {
            "issue": VWAP_SOURCE_GUARD_ISSUE,
            "relative_tolerance": VWAP_SOURCE_DIAGNOSTIC_RELATIVE_TOLERANCE,
            "disposition": "accepted_diagnostic",
        },
        "hard_notional_guard": {
            "issue": NOTIONAL_HARD_GUARD_ISSUE,
            "relative_tolerance": NOTIONAL_HARD_GUARD_RELATIVE_TOLERANCE,
            "absolute_tolerance_cny": NOTIONAL_HARD_GUARD_ABSOLUTE_TOLERANCE_CNY,
            "lower_bound": "amount < low * vol * 0.90 - 1 CNY",
            "upper_bound": "amount > high * vol * 1.10 + 1 CNY",
            "disposition": "fatal",
        },
        "positive_volume_zero_amount": ("accepted_diagnostic_when_hard_notional_guard_passes"),
        "zero_volume_nonzero_amount": "fatal",
    }


def vwap_diagnostic_summary(daily: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate source-aware VWAP diagnostics for a coverage receipt."""
    by_tier: dict[str, dict[str, int]] = {}
    totals = {"rows": 0, "accepted_rows": 0, "fatal_rows": 0}
    affected_dates: list[str] = []
    accepted_dates: list[str] = []
    for record in daily:
        count = int(record.get("issues", {}).get(VWAP_OHLC_DIAGNOSTIC, 0))
        accepted = int(record.get("accepted_diagnostics", {}).get(VWAP_OHLC_DIAGNOSTIC, 0))
        fatal = count - accepted
        tier_record = by_tier.setdefault(
            str(record["tier"]),
            {"rows": 0, "accepted_rows": 0, "fatal_rows": 0, "dates_affected": 0},
        )
        for target in (tier_record, totals):
            target["rows"] += count
            target["accepted_rows"] += accepted
            target["fatal_rows"] += fatal
        tier_record["dates_affected"] += int(count > 0)
        if count:
            affected_dates.append(str(record["date"]))
        if accepted:
            accepted_dates.append(str(record["date"]))
    return {
        "policy": VWAP_DIAGNOSTIC_POLICY,
        "tolerance": {
            "absolute": VWAP_OHLC_ABSOLUTE_TOLERANCE,
            "relative_to_max_abs_high_low": VWAP_OHLC_RELATIVE_TOLERANCE,
        },
        "accepted_sources": sorted(VWAP_DIAGNOSTIC_SOURCES),
        "price_flow_incomparable_unit_profiles": sorted(VWAP_PRICE_FLOW_INCOMPARABLE_UNIT_PROFILES),
        "source_deviation_guard": {
            **dict(tushare_price_flow_policy()["two_percent_vwap_diagnostic"]),
            "accepted_sources": sorted(TUSHARE_VWAP_DIAGNOSTIC_SOURCES),
            "accepted_for_incomparable_unit_profiles": sorted(
                VWAP_PRICE_FLOW_INCOMPARABLE_UNIT_PROFILES
            ),
        },
        "hard_notional_guard": {
            **dict(tushare_price_flow_policy()["hard_notional_guard"]),
            "accepted_for_incomparable_unit_profiles": sorted(
                VWAP_PRICE_FLOW_INCOMPARABLE_UNIT_PROFILES
            ),
        },
        **totals,
        "dates_affected": affected_dates,
        "accepted_dates": accepted_dates,
        "by_tier": by_tier,
    }


def issue_disposition_summary(
    daily: Sequence[Mapping[str, Any]],
    issue: str,
) -> dict[str, Any]:
    """Aggregate one diagnostic's accepted and fatal dispositions."""
    by_tier: dict[str, dict[str, int]] = {}
    totals = {"rows": 0, "accepted_rows": 0, "fatal_rows": 0, "dates_affected": 0}
    for record in daily:
        count = int(record.get("issues", {}).get(issue, 0))
        accepted = int(record.get("accepted_diagnostics", {}).get(issue, 0))
        tier_record = by_tier.setdefault(
            str(record["tier"]),
            {"rows": 0, "accepted_rows": 0, "fatal_rows": 0, "dates_affected": 0},
        )
        for target in (tier_record, totals):
            target["rows"] += count
            target["accepted_rows"] += accepted
            target["fatal_rows"] += count - accepted
            target["dates_affected"] += int(count > 0)
    return {"issue": issue, **totals, "by_tier": by_tier}


def accepted_issue_rows_by_source(
    daily: Sequence[Mapping[str, Any]],
    issue: str,
) -> dict[str, int]:
    """Aggregate accepted diagnostic rows for Guan and TuShare independently."""
    totals = {"guan": 0, "tushare": 0}
    for record in daily:
        by_source = record.get("accepted_diagnostics_by_source")
        if not isinstance(by_source, Mapping):
            continue
        for source in totals:
            diagnostics = by_source.get(source)
            if isinstance(diagnostics, Mapping):
                totals[source] += int(diagnostics.get(issue, 0))
    return totals


__all__ = [
    "NOTIONAL_HARD_GUARD_ABSOLUTE_TOLERANCE_CNY",
    "NOTIONAL_HARD_GUARD_ISSUE",
    "NOTIONAL_HARD_GUARD_RELATIVE_TOLERANCE",
    "POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE",
    "TUSHARE_VWAP_DIAGNOSTIC_SOURCES",
    "VWAP_EXTREME_HIGH_MULTIPLIER",
    "VWAP_EXTREME_LOW_MULTIPLIER",
    "VWAP_EXTREME_UNIT_SCALE_ISSUE",
    "VWAP_DIAGNOSTIC_POLICY",
    "VWAP_DIAGNOSTIC_SOURCES",
    "VWAP_OHLC_ABSOLUTE_TOLERANCE",
    "VWAP_OHLC_DIAGNOSTIC",
    "VWAP_OHLC_RELATIVE_TOLERANCE",
    "VWAP_SOURCE_DIAGNOSTIC_RELATIVE_TOLERANCE",
    "VWAP_SOURCE_GUARD_ISSUE",
    "VWAP_PRICE_FLOW_INCOMPARABLE_UNIT_PROFILES",
    "ZERO_VOLUME_NONZERO_AMOUNT_ISSUE",
    "accepted_issue_rows_by_source",
    "issue_disposition_summary",
    "price_flow_diagnostics",
    "tushare_price_flow_policy",
    "vwap_diagnostic_summary",
]
