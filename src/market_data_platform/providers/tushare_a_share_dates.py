"""Date helpers for TuShare A-share provider mirrors."""

from __future__ import annotations

import re
from datetime import datetime, timedelta


def _validate_date(value: str) -> str:
    text = str(value).strip()
    if not re.fullmatch(r"\d{8}", text):
        raise ValueError(f"Expected YYYYMMDD date, got: {value}")
    return text


def _calendar_dates(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(_validate_date(start_date), "%Y%m%d").date()
    end = datetime.strptime(_validate_date(end_date), "%Y%m%d").date()
    if start > end:
        raise ValueError(f"start_date must be <= end_date: {start_date} > {end_date}")
    dates: list[str] = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


def _calendar_months(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(_validate_date(start_date), "%Y%m%d").date()
    end = datetime.strptime(_validate_date(end_date), "%Y%m%d").date()
    if start > end:
        raise ValueError(f"start_date must be <= end_date: {start_date} > {end_date}")
    months: list[str] = []
    year = start.year
    month = start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year:04d}{month:02d}")
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return months


def _quarter_periods(start_date: str, end_date: str) -> list[str]:
    start = _validate_date(start_date)
    end = _validate_date(end_date)
    if start > end:
        raise ValueError(f"start_date must be <= end_date: {start} > {end}")
    periods: list[str] = []
    start_year = int(start[:4])
    end_year = int(end[:4])
    for year in range(start_year, end_year + 1):
        for month_day in ("0331", "0630", "0930", "1231"):
            period = f"{year}{month_day}"
            if start <= period <= end:
                periods.append(period)
    return periods
