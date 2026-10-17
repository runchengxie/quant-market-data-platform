"""Evidence for daily_basic rows that do not represent a traded stock-day.

Listing pauses use inclusive start and exclusive resumption dates. Each interval
is grounded in issuer disclosures, rather than inferred from missing prices.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

LISTING_PAUSES = {
    "000670.SZ": (
        "20200407",
        "20220822",
        "https://static.cninfo.com.cn/finalpage/2026-06-09/1225358835.PDF",
    ),
    "000792.SZ": (
        "20200522",
        "20210810",
        "https://static.cninfo.com.cn/finalpage/2020-05-21/1207838318.PDF",
        "https://static.cninfo.com.cn/finalpage/2021-08-03/1210641780.PDF",
    ),
    "000995.SZ": (
        "20190513",
        "20201216",
        "https://static.cninfo.com.cn/finalpage/2020-12-05/1208857867.PDF",
        "https://static.cninfo.com.cn/finalpage/2020-12-09/1208871266.PDF",
    ),
}


def _full_day_suspensions(pro: Any, trade_date: str, policy: Any) -> set[str]:
    from quant_market_data_platform.providers.tushare_a_share import _call_tushare_api

    api = getattr(pro, "suspend_d", None)
    if api is None:
        return set()  # No evidence; the caller must keep rejecting the mismatch.
    frames = []
    for page in range(10):
        response = _call_tushare_api(
            lambda page=page: api(
                trade_date=trade_date,
                fields="ts_code,trade_date,suspend_type,suspend_timing",
                limit=5000,
                offset=page * 5000,
            ),
            policy=policy,
        )
        if response is None or len(response) > 5000:
            raise RuntimeError(f"Invalid suspend_d response for {trade_date}")
        if response.empty:
            break
        required = {"ts_code", "trade_date", "suspend_type", "suspend_timing"}
        if not required.issubset(response.columns):
            raise ValueError("suspend_d lacks full-day suspension evidence fields")
        if not response["trade_date"].astype(str).eq(trade_date).all():
            raise ValueError("suspend_d returned evidence for a different date")
        frames.append(response)
        if len(response) < 5000:
            break
    else:
        raise RuntimeError("suspend_d pagination exceeded 10 pages")
    if not frames:
        return set()
    evidence = pd.concat(frames, ignore_index=True)
    timing = evidence["suspend_timing"].fillna("").astype(str).str.strip()
    full_day = evidence["suspend_type"].eq("S") & timing.eq("")
    resumes = set(evidence.loc[evidence["suspend_type"].eq("R"), "ts_code"])
    return set(evidence.loc[full_day, "ts_code"]) - resumes


def verified_nontrading_symbols(
    pro: Any,
    *,
    missing: set[str],
    trade_date: str,
    policy: Any,
) -> dict[str, str]:
    """Explain only missing daily rows; never remove actual traded symbols."""
    if not missing:
        return {}
    paused = {
        code
        for code, interval in LISTING_PAUSES.items()
        if code in missing and interval[0] <= trade_date < interval[1]
    }
    suspended = _full_day_suspensions(pro, trade_date, policy) if missing - paused else set()
    return {
        code: "issuer_listing_pause" if code in paused else "tushare_suspend_d_full_day"
        for code in sorted(missing & (paused | suspended))
    }
