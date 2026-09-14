"""Restartable downloads for TuShare constraint reference datasets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_common import (
    get_tushare_client,
    normalize_ts_code,
    pandas,
    resolve_tushare_api_url,
)
from market_data_platform.providers.tushare_constraint_io import (
    CONSTRAINT_RECEIPT_SCHEMA,
    ConstraintDownloadOptions,
    PartRequest,
    atomic_json,
    atomic_parquet,
    fetch_pages,
    load_or_fetch_part,
    month_windows,
    part_receipt_path,
    sha256,
    year_windows,
)

CONSTRAINT_REFERENCE_DATASETS = (
    "namechange",
    "margin_secs",
    "margin_detail",
    "suspend_d",
    "st",
    "slb_sec_detail",
)
TRADE_DATE_CONSTRAINT_DATASETS = ("margin_secs", "margin_detail")
CONSTRAINT_SEMANTICS = {
    "namechange": "historical_name_intervals_for_reconstructed_st",
    "margin_secs": "borrow_qualification_upper_bound_not_inventory",
    "margin_detail": "reported_margin_and_securities_lending_activity_not_inventory",
    "suspend_d": "explicit_exchange_suspension_events",
    "st": "provider_st_change_events_for_cross_validation_not_complete_daily_state",
    "slb_sec_detail": "reported_securities_lending_transactions_not_borrow_availability",
}


def _validate_date(value: str, field: str) -> None:
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError as error:
        raise ValueError(f"{field} must be a valid YYYYMMDD date") from error
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError(f"{field} must be a valid YYYYMMDD date")


def _download_namechange(
    client: Any,
    options: ConstraintDownloadOptions,
    api_url: str | None,
) -> Any:
    pd = pandas()
    frames = [
        frame
        for start_date, end_date in year_windows(options.start_date, options.end_date)
        if not (
            frame := load_or_fetch_part(
                client,
                options,
                PartRequest(
                    key=f"{start_date}_{end_date}",
                    api_name="namechange",
                    params={"start_date": start_date, "end_date": end_date},
                ),
                api_url=api_url,
            )
        ).empty
    ]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _trade_dates(client: Any, options: ConstraintDownloadOptions) -> tuple[str, ...]:
    frame, _ = fetch_pages(
        client,
        "trade_cal",
        {
            "exchange": "SSE",
            "start_date": options.start_date,
            "end_date": options.end_date,
            "is_open": "1",
        },
        options,
    )
    if frame.empty or "cal_date" not in frame:
        raise RuntimeError(f"trade_cal returned no open dates for {options.dataset} download")
    return tuple(sorted(frame["cal_date"].astype(str).unique()))


def _download_trade_date_dataset(
    client: Any,
    options: ConstraintDownloadOptions,
    api_url: str | None,
) -> Any:
    pd = pandas()
    frames = [
        frame
        for trade_date in _trade_dates(client, options)
        if not (
            frame := load_or_fetch_part(
                client,
                options,
                PartRequest(
                    key=trade_date,
                    api_name=options.dataset,
                    params={"trade_date": trade_date},
                ),
                api_url=api_url,
            )
        ).empty
    ]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _download_windowed_dataset(
    client: Any,
    options: ConstraintDownloadOptions,
    api_url: str | None,
    windows: tuple[tuple[str, str], ...],
) -> Any:
    pd = pandas()
    frames = [
        frame
        for start_date, end_date in windows
        if not (
            frame := load_or_fetch_part(
                client,
                options,
                PartRequest(
                    key=f"{start_date}_{end_date}",
                    api_name=options.dataset,
                    params={"start_date": start_date, "end_date": end_date},
                ),
                api_url=api_url,
            )
        ).empty
    ]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _download_suspend_d(
    client: Any, options: ConstraintDownloadOptions, api_url: str | None
) -> Any:
    return _download_windowed_dataset(
        client, options, api_url, year_windows(options.start_date, options.end_date)
    )


def _download_slb_sec_detail(
    client: Any, options: ConstraintDownloadOptions, api_url: str | None
) -> Any:
    return _download_windowed_dataset(
        client, options, api_url, month_windows(options.start_date, options.end_date)
    )


def _download_st(
    client: Any,
    options: ConstraintDownloadOptions,
    api_url: str | None,
) -> Any:
    # The endpoint caps pages at 1,000 rows. Matching that cap prevents
    # short-page detection from silently truncating the global event history.
    st_options = replace(options, page_size=min(options.page_size, 1000))
    frame = load_or_fetch_part(
        client,
        st_options,
        PartRequest(key="all", api_name="st", params={}),
        api_url=api_url,
    )
    if frame.empty or "imp_date" not in frame:
        return frame
    dates = frame["imp_date"].astype("string").str.replace("-", "", regex=False).str[:8]
    return frame[dates.between(options.start_date, options.end_date)].copy()


def _normalize_download(frame: Any, dataset: str) -> Any:
    if frame.empty:
        return frame
    frame = frame.copy()
    if "ts_code" in frame:
        frame["ts_code"] = frame["ts_code"].map(normalize_ts_code)
    for column in (
        "trade_date",
        "start_date",
        "end_date",
        "ann_date",
        "pub_date",
        "imp_date",
    ):
        if column in frame:
            frame[column] = frame[column].astype("string").str.replace("-", "", regex=False).str[:8]
    keys_by_dataset = {
        "namechange": ["ts_code", "name", "start_date", "end_date", "ann_date"],
        "margin_secs": ["trade_date", "ts_code", "exchange"],
        "margin_detail": ["trade_date", "ts_code"],
        "suspend_d": ["trade_date", "ts_code", "suspend_timing", "suspend_type"],
        "st": ["ts_code", "pub_date", "imp_date", "st_type"],
        "slb_sec_detail": ["trade_date", "ts_code", "tenor", "fee_rate"],
    }
    keys = keys_by_dataset[dataset]
    present = [key for key in keys if key in frame]
    if not present:
        raise ValueError(f"{dataset} response lacks normalization keys: {keys}")
    return frame.drop_duplicates(present, keep="last").sort_values(present).reset_index(drop=True)


def _aggregate_receipt(
    options: ConstraintDownloadOptions,
    path: Path,
    frame: Any,
    api_url: str | None,
) -> dict[str, Any]:
    candidate_receipts = sorted(
        part_receipt_path(part)
        for part in (path.parent / "_parts" / options.dataset).glob("*.parquet")
    )
    receipt_payloads = [
        (receipt, json.loads(receipt.read_text(encoding="utf-8"))) for receipt in candidate_receipts
    ]

    def belongs_to_query(payload: dict[str, Any]) -> bool:
        params = payload.get("params") or {}
        if payload.get("dataset") != options.dataset or not isinstance(params, dict):
            return False
        if options.dataset in TRADE_DATE_CONSTRAINT_DATASETS:
            trade_date = str(params.get("trade_date") or "")
            return options.start_date <= trade_date <= options.end_date
        if options.dataset == "st":
            return params == {}
        start_date = str(params.get("start_date") or "")
        end_date = str(params.get("end_date") or "")
        return options.start_date <= start_date <= end_date <= options.end_date

    receipts = [
        (receipt, payload) for receipt, payload in receipt_payloads if belongs_to_query(payload)
    ]
    part_hashes = [payload["sha256"] for _receipt, payload in receipts]
    digest = hashlib.sha256("\n".join(part_hashes).encode()).hexdigest()
    return {
        "schema_version": CONSTRAINT_RECEIPT_SCHEMA,
        "dataset": options.dataset,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "api_url": api_url,
        "query_start_date": options.start_date,
        "query_end_date": options.end_date,
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "sha256": sha256(path),
        "part_count": len(receipts),
        "part_hashes_sha256": digest,
        "quality_status": "complete",
        "semantics": CONSTRAINT_SEMANTICS[options.dataset],
    }


def download_constraint_reference(options: ConstraintDownloadOptions) -> dict[str, Any]:
    """Download one constraint reference with immutable, hash-checked resumable parts."""
    if options.dataset not in CONSTRAINT_REFERENCE_DATASETS:
        raise ValueError(f"unsupported constraint reference dataset: {options.dataset}")
    _validate_date(options.start_date, "start_date")
    _validate_date(options.end_date, "end_date")
    if options.start_date > options.end_date:
        raise ValueError("start_date must not exceed end_date")
    if options.page_size <= 0 or options.max_pages <= 0 or options.retries < 0:
        raise ValueError("page_size/max_pages must be positive and retries non-negative")
    if options.request_interval_seconds < 0:
        raise ValueError("request_interval_seconds cannot be negative")
    api_url = resolve_tushare_api_url(options.api_url, token_env=options.token_env)
    client = get_tushare_client(token_env=options.token_env, api_url=api_url)
    loaders = {
        "namechange": _download_namechange,
        "suspend_d": _download_suspend_d,
        "st": _download_st,
        "slb_sec_detail": _download_slb_sec_detail,
    }
    loader = loaders.get(options.dataset, _download_trade_date_dataset)
    frame = _normalize_download(loader(client, options, api_url), options.dataset)
    if frame.empty:
        raise ValueError(f"{options.dataset} is empty for the requested window")
    root = Path(options.out_dir).expanduser().resolve()
    path = root / f"{options.dataset}.parquet"
    atomic_parquet(frame, path)
    receipt = _aggregate_receipt(options, path, frame, api_url)
    receipt_path = path.with_suffix(".receipt.json")
    atomic_json(receipt_path, receipt)
    return {**receipt, "path": str(path), "receipt_path": str(receipt_path)}


__all__ = [
    "CONSTRAINT_REFERENCE_DATASETS",
    "ConstraintDownloadOptions",
    "download_constraint_reference",
]
