from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, time
from typing import Any

import pandas as pd

from market_data_platform.context.models import ContextSeriesSpec, validate_context_observations
from market_data_platform.providers._client import _call_trade_date_endpoint_frame
from market_data_platform.providers.tushare_a_share_options import (
    DEFAULT_DISABLE_PROXY,
    DEFAULT_QUOTA_COOLDOWN_SECONDS,
    DEFAULT_REQUEST_ATTEMPTS,
    DEFAULT_RETRY_MAX_SLEEP_SECONDS,
    DEFAULT_RETRY_SLEEP_SECONDS,
    TushareRequestPolicy,
)

TUSHARE_CONTEXT_ENDPOINTS = (
    "shibor",
    "shibor_lpr",
    "cn_m",
    "sf_month",
    "cn_pmi",
    "cn_cpi",
    "cn_ppi",
    "cn_gdp",
    "cn_schedule",
)


def _spec(  # noqa: PLR0913
    series_id: str,
    endpoint: str,
    field: str,
    name: str,
    family: str,
    frequency: str,
    unit: str,
    value_semantics: str,
    max_staleness: int,
) -> ContextSeriesSpec:
    return ContextSeriesSpec(
        series_id=series_id,
        source_id=f"tushare.{endpoint}.{field}",
        provider="tushare",
        source_series_key=field,
        name=name,
        family=family,
        frequency=frequency,
        unit=unit,
        seasonal_adjustment="provider",
        value_semantics=value_semantics,
        revision_policy="observed_vintage",
        availability_policy="source_release_or_observed",
        expected_release_lag="provider_schedule",
        max_staleness=max_staleness,
    )


_SERIES_BY_ENDPOINT: dict[str, tuple[ContextSeriesSpec, ...]] = {
    "shibor": (
        _spec(
            "rates.shibor_on",
            "shibor",
            "on",
            "Shibor O/N",
            "rates",
            "daily",
            "percent",
            "level",
            10,
        ),
        _spec(
            "rates.shibor_1w", "shibor", "1w", "Shibor 1W", "rates", "daily", "percent", "level", 10
        ),
        _spec(
            "rates.shibor_1m", "shibor", "1m", "Shibor 1M", "rates", "daily", "percent", "level", 10
        ),
        _spec(
            "rates.shibor_3m", "shibor", "3m", "Shibor 3M", "rates", "daily", "percent", "level", 10
        ),
        _spec(
            "rates.shibor_6m", "shibor", "6m", "Shibor 6M", "rates", "daily", "percent", "level", 10
        ),
        _spec(
            "rates.shibor_1y", "shibor", "1y", "Shibor 1Y", "rates", "daily", "percent", "level", 10
        ),
    ),
    "shibor_lpr": (
        _spec(
            "rates.lpr_1y", "shibor_lpr", "1y", "LPR 1Y", "rates", "daily", "percent", "level", 40
        ),
        _spec(
            "rates.lpr_5y", "shibor_lpr", "5y", "LPR 5Y", "rates", "daily", "percent", "level", 40
        ),
    ),
    "cn_m": (
        _spec("credit.m1", "cn_m", "m1", "M1", "credit", "monthly", "100m_cny", "level", 70),
        _spec(
            "credit.m1_yoy", "cn_m", "m1_yoy", "M1 YoY", "credit", "monthly", "percent", "yoy", 70
        ),
        _spec("credit.m2", "cn_m", "m2", "M2", "credit", "monthly", "100m_cny", "level", 70),
        _spec(
            "credit.m2_yoy", "cn_m", "m2_yoy", "M2 YoY", "credit", "monthly", "percent", "yoy", 70
        ),
    ),
    "sf_month": (
        _spec(
            "credit.social_financing_flow",
            "sf_month",
            "inc_month",
            "Social financing monthly flow",
            "credit",
            "monthly",
            "100m_cny",
            "flow",
            70,
        ),
        _spec(
            "credit.social_financing_stock",
            "sf_month",
            "stk_endval",
            "Social financing stock",
            "credit",
            "monthly",
            "trillion_cny",
            "level",
            70,
        ),
    ),
    "cn_pmi": (
        _spec(
            "activity.pmi_manufacturing",
            "cn_pmi",
            "pmi010000",
            "Manufacturing PMI",
            "activity",
            "monthly",
            "index",
            "level",
            45,
        ),
        _spec(
            "activity.pmi_production",
            "cn_pmi",
            "pmi010400",
            "Manufacturing PMI production",
            "activity",
            "monthly",
            "index",
            "level",
            45,
        ),
    ),
    "cn_cpi": (
        _spec(
            "prices.cpi_yoy",
            "cn_cpi",
            "nt_yoy",
            "CPI YoY",
            "prices",
            "monthly",
            "percent",
            "yoy",
            70,
        ),
        _spec(
            "prices.cpi_mom",
            "cn_cpi",
            "nt_mom",
            "CPI MoM",
            "prices",
            "monthly",
            "percent",
            "mom",
            70,
        ),
    ),
    "cn_ppi": (
        _spec(
            "prices.ppi_yoy",
            "cn_ppi",
            "ppi_yoy",
            "PPI YoY",
            "prices",
            "monthly",
            "percent",
            "yoy",
            70,
        ),
        _spec(
            "prices.ppi_mom",
            "cn_ppi",
            "ppi_mom",
            "PPI MoM",
            "prices",
            "monthly",
            "percent",
            "mom",
            70,
        ),
        _spec(
            "prices.ppi_accu",
            "cn_ppi",
            "ppi_accu",
            "PPI accumulated YoY",
            "prices",
            "monthly",
            "percent",
            "yoy",
            70,
        ),
    ),
    "cn_gdp": (
        _spec(
            "activity.gdp_yoy",
            "cn_gdp",
            "gdp_yoy",
            "GDP YoY",
            "activity",
            "quarterly",
            "percent",
            "yoy",
            140,
        ),
        _spec(
            "activity.gdp_secondary_yoy",
            "cn_gdp",
            "si_yoy",
            "Secondary industry GDP YoY",
            "activity",
            "quarterly",
            "percent",
            "yoy",
            140,
        ),
        _spec(
            "activity.gdp_tertiary_yoy",
            "cn_gdp",
            "ti_yoy",
            "Tertiary industry GDP YoY",
            "activity",
            "quarterly",
            "percent",
            "yoy",
            140,
        ),
    ),
}


def tushare_context_catalog() -> pd.DataFrame:
    rows = [asdict(spec) for specs in _SERIES_BY_ENDPOINT.values() for spec in specs]
    return pd.DataFrame(rows)


def _policy() -> TushareRequestPolicy:
    return TushareRequestPolicy(
        attempts=DEFAULT_REQUEST_ATTEMPTS,
        retry_sleep_seconds=DEFAULT_RETRY_SLEEP_SECONDS,
        retry_max_sleep_seconds=DEFAULT_RETRY_MAX_SLEEP_SECONDS,
        quota_cooldown_seconds=DEFAULT_QUOTA_COOLDOWN_SECONDS,
        disable_proxy=DEFAULT_DISABLE_PROXY,
    )


def _month(value: str | None) -> str | None:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits[:6] if len(digits) >= 6 else None


def _quarter(value: str | None) -> str | None:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) < 6:
        return None
    month = int(digits[4:6])
    quarter = (month - 1) // 3 + 1
    return f"{digits[:4]}Q{quarter}"


def _endpoint_kwargs(endpoint: str, start_date: str | None, end_date: str | None) -> dict[str, Any]:
    if endpoint in {"shibor", "shibor_lpr"}:
        return {
            key: value
            for key, value in {"start_date": start_date, "end_date": end_date}.items()
            if value
        }
    if endpoint == "cn_schedule":
        month = _month(end_date or start_date)
        return {"m": month} if month else {}
    if endpoint == "cn_gdp":
        values = {"start_q": _quarter(start_date), "end_q": _quarter(end_date)}
        return {key: value for key, value in values.items() if value}
    values = {"start_m": _month(start_date), "end_m": _month(end_date)}
    return {key: value for key, value in values.items() if value}


def fetch_tushare_context_endpoint(
    client: Any,
    endpoint: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    request_policy: TushareRequestPolicy | None = None,
) -> pd.DataFrame:
    if endpoint not in TUSHARE_CONTEXT_ENDPOINTS:
        raise ValueError(f"unsupported TuShare context endpoint: {endpoint}")
    return _call_trade_date_endpoint_frame(
        client,
        endpoint,
        _endpoint_kwargs(endpoint, start_date, end_date),
        policy=request_policy or _policy(),
    )


def _period_bounds(value: object, frequency: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    text = str(value).strip()
    if frequency == "daily":
        day = pd.to_datetime(text, format="%Y%m%d", utc=True)
        return day, day
    if frequency == "monthly":
        start = pd.to_datetime(f"{text}01", format="%Y%m%d", utc=True)
        end = start + pd.offsets.MonthEnd(1)
        return start, end
    if frequency == "quarterly":
        year = int(text[:4])
        quarter = int(text[-1])
        month = 3 * (quarter - 1) + 1
        start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
        end = start + pd.offsets.QuarterEnd(startingMonth=3)
        return start, end  # ty: ignore[invalid-return-type]
    raise ValueError(f"unsupported context frequency: {frequency}")


def _known_daily_release(period_end: pd.Timestamp, endpoint: str) -> pd.Timestamp | None:
    if endpoint not in {"shibor", "shibor_lpr"}:
        return None
    # TuShare documents both feeds as published by noon China time. Noon is conservative for LPR.
    local_noon_utc = datetime.combine(period_end.date(), time(hour=4), tzinfo=UTC)
    return pd.Timestamp(local_noon_utc)  # ty: ignore[invalid-return-type]


def _schedule_release(
    endpoint: str,
    period_end: pd.Timestamp,
    release_calendar: pd.DataFrame | None,
) -> pd.Timestamp | None:
    if release_calendar is None or release_calendar.empty or "data_api" not in release_calendar:
        return None
    matches = release_calendar.loc[release_calendar["data_api"].astype(str).eq(endpoint)].copy()
    if matches.empty or "publish_date" not in matches:
        return None
    published = pd.to_datetime(
        matches["publish_date"].astype(str), format="%Y%m%d", errors="coerce"
    )
    matches = matches.loc[published.notna()].copy()
    if matches.empty:
        return None
    published = published.loc[published.notna()]
    # cn_schedule provides dates without clock time. Conservatively make the value visible
    # at 00:00 China time on the next calendar day.
    available = published.dt.tz_localize("Asia/Shanghai") + pd.Timedelta(days=1)
    available = available.dt.tz_convert("UTC")
    lower = period_end
    upper = period_end + pd.Timedelta(days=120)
    candidates = available.loc[(available > lower) & (available <= upper)]
    if candidates.empty:
        return None
    return pd.Timestamp(candidates.min())  # ty: ignore[invalid-return-type]


def _is_observed_vintage(retrieved_at: pd.Timestamp, release_at: pd.Timestamp | None) -> bool:
    if release_at is None:
        return False
    lag = retrieved_at - release_at
    return pd.Timedelta(0) <= lag <= pd.Timedelta(days=3)


def normalize_tushare_context(
    endpoint: str,
    raw: pd.DataFrame,
    *,
    retrieved_at: datetime,
    source_hash: str,
    release_calendar: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if endpoint not in TUSHARE_CONTEXT_ENDPOINTS:
        raise ValueError(f"unsupported TuShare context endpoint: {endpoint}")
    retrieved = pd.Timestamp(retrieved_at)
    if retrieved.tzinfo is None:
        retrieved = retrieved.tz_localize("UTC")
    else:
        retrieved = retrieved.tz_convert("UTC")

    if endpoint == "cn_schedule":
        required = {"month", "publish_date", "title", "issuing_org", "data_api"}
        missing = required.difference(raw.columns)
        if missing:
            raise ValueError(f"cn_schedule response missing fields: {', '.join(sorted(missing))}")
        releases = raw.loc[:, sorted(required)].copy()
        releases["provider"] = "tushare"
        releases["source_locator"] = "tushare://cn_schedule"
        releases["source_retrieved_at"] = retrieved
        return pd.DataFrame(columns=()), releases.reset_index(drop=True)  # ty: ignore[invalid-argument-type]

    specs = _SERIES_BY_ENDPOINT[endpoint]
    expected_period_column = (
        "date"
        if specs[0].frequency == "daily"
        else "quarter"
        if specs[0].frequency == "quarterly"
        else "month"
    )
    period_column = next(
        (column for column in raw.columns if str(column).strip().lower() == expected_period_column),
        None,
    )
    if period_column is None:
        raise ValueError(f"{endpoint} response missing fields: {expected_period_column}")
    source_columns = {str(column).strip().lower(): column for column in raw.columns}
    available_specs = [spec for spec in specs if spec.source_series_key.lower() in source_columns]
    if not available_specs:
        expected = ", ".join(spec.source_series_key for spec in specs)
        raise ValueError(f"{endpoint} response missing known series fields: {expected}")

    rows: list[dict[str, Any]] = []
    vintage_id = retrieved.strftime("%Y%m%dT%H%M%SZ")  # ty: ignore[unresolved-attribute]
    # TuShare uses columns such as `3m`, `1y`, and `5y`; record dictionaries preserve them.
    for source_row in raw.to_dict(orient="records"):
        for spec in available_specs:
            value = pd.to_numeric(
                pd.Series([source_row[source_columns[spec.source_series_key.lower()]]]),
                errors="coerce",
            ).iloc[0]
            if pd.isna(value):
                continue
            period_start, period_end = _period_bounds(source_row[period_column], spec.frequency)
            release_at = _known_daily_release(period_end, endpoint) or _schedule_release(
                endpoint, period_end, release_calendar
            )
            observed = _is_observed_vintage(retrieved, release_at)  # ty: ignore[invalid-argument-type]
            available_at = release_at if observed and release_at is not None else retrieved
            rows.append(
                {
                    "series_id": spec.series_id,
                    "period_start": period_start,
                    "period_end": period_end,
                    "value": float(value),
                    "unit": spec.unit,
                    # A scheduled release in the future is not yet observable at retrieval time;
                    # do not write a future publication timestamp into a reconstructed row.
                    "published_at": release_at if observed else pd.NaT,
                    "observed_at": retrieved,
                    "ingested_at": retrieved,
                    "source_retrieved_at": retrieved,
                    "available_at": available_at,
                    "vintage_id": vintage_id,
                    "revision_number": 0,
                    "source_hash": source_hash,
                    "revision_covered": observed,
                    "reconstructed": not observed,
                }
            )
    observations = pd.DataFrame(rows)
    if observations.empty:
        observations = pd.DataFrame(
            columns=pd.Index(
                [
                    "series_id",
                    "period_start",
                    "period_end",
                    "value",
                    "unit",
                    "published_at",
                    "observed_at",
                    "ingested_at",
                    "source_retrieved_at",
                    "available_at",
                    "vintage_id",
                    "revision_number",
                    "source_hash",
                    "revision_covered",
                    "reconstructed",
                ]
            )
        )
        return observations, pd.DataFrame()
    return validate_context_observations(observations), pd.DataFrame()
