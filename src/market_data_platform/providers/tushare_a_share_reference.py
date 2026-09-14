"""TuShare A-share reference datasets owned and published by market-data-platform."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_common import (
    get_tushare_client,
    normalize_ts_code,
    pandas,
)

DEFAULT_MAX_PAGES = 100
DEFAULT_INDEX_CODES = (
    "000300.SH",
    "000905.SH",
    "000852.SH",
    "399006.SZ",
    "000016.SH",
)
DEFAULT_EXCHANGES = ("SSE", "SZSE", "BSE")
REFERENCE_RECEIPT_SCHEMA_VERSION = "market-data-platform.tushare-reference.v1"


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    api_name: str
    fetch_granularity: str
    primary_keys: tuple[str, ...]
    date_fields: tuple[str, ...]
    refresh_mode: str = "latest_snapshot"


DATASET_SPECS: dict[str, DatasetSpec] = {
    "stock_st": DatasetSpec(
        "stock_st", "stock_st", "daily", ("ts_code", "trade_date"), ("trade_date",)
    ),
    "index_weight": DatasetSpec(
        "index_weight",
        "index_weight",
        "monthly",
        ("index_code", "con_code", "trade_date"),
        ("trade_date",),
    ),
    "index_weight_daily": DatasetSpec(
        "index_weight_daily",
        "index_weight",
        "daily",
        ("index_code", "con_code", "trade_date"),
        ("trade_date",),
    ),
    "stock_company": DatasetSpec(
        "stock_company", "stock_company", "latest_snapshot", ("ts_code",), ()
    ),
    "stk_managers": DatasetSpec(
        "stk_managers",
        "stk_managers",
        "annual_windows",
        ("ts_code", "ann_date", "name", "title", "begin_date", "end_date"),
        ("ann_date",),
    ),
    "share_float": DatasetSpec(
        "share_float",
        "share_float",
        "annual_windows",
        ("ts_code", "ann_date", "float_date", "holder_name", "share_type"),
        ("ann_date", "float_date"),
    ),
}

REFERENCE_DATASETS = tuple(DATASET_SPECS)


@dataclass(frozen=True)
class RawReferenceDownloadOptions:
    dataset: str
    out_dir: str | Path
    start_date: str
    end_date: str
    index_code: str | None = None
    exchange: str | None = None
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    run_id: str | None = None
    page_size: int = 5000
    max_pages: int = DEFAULT_MAX_PAGES
    request_interval_seconds: float = 0.2
    retries: int = 5


@dataclass(frozen=True)
class RequestRuntime:
    page_size: int
    max_pages: int
    request_interval_seconds: float
    retries: int


def _runtime(options: RawReferenceDownloadOptions) -> RequestRuntime:
    return RequestRuntime(
        page_size=options.page_size,
        max_pages=options.max_pages,
        request_interval_seconds=options.request_interval_seconds,
        retries=options.retries,
    )


def _output_path(base: str | Path, name: str) -> Path:
    root = Path(base).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / name


def _atomic_parquet(frame: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".parquet",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _daily_part_path(
    options: RawReferenceDownloadOptions,
    dataset: str,
    date_value: str,
) -> Path:
    return (
        Path(options.out_dir).expanduser().resolve()
        / "_parts"
        / dataset
        / f"{dataset}_{date_value}.parquet"
    )


def _fetch(
    client: Any,
    api_name: str,
    params: dict[str, Any],
    *,
    runtime: RequestRuntime,
) -> Any:
    pd = pandas()
    endpoint = getattr(client, api_name)
    frames: list[Any] = []
    for page in range(runtime.max_pages):
        frame = _query_frame(
            endpoint,
            {
                **params,
                "offset": page * runtime.page_size,
                "limit": runtime.page_size,
            },
            request_interval_seconds=runtime.request_interval_seconds,
            retries=runtime.retries,
        )
        if frame.empty:
            break
        frames.append(frame)
        if len(frame) < runtime.page_size:
            break
    else:
        raise RuntimeError(
            f"{api_name} reached max_pages={runtime.max_pages}; narrow the request window"
        )
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _query_frame(
    endpoint: Any,
    params: dict[str, Any],
    *,
    request_interval_seconds: float = 0.2,
    retries: int = 5,
) -> Any:
    pd = pandas()
    for attempt in range(retries + 1):
        try:
            frame = pd.DataFrame(endpoint(**params))
            if request_interval_seconds > 0:
                time.sleep(request_interval_seconds)
            return frame
        except Exception:
            if attempt >= retries:
                raise
            time.sleep(min(30.0, 2.0 ** (attempt + 1)))
    raise RuntimeError("unreachable TuShare retry state")


def _csv_values(raw: str | None, defaults: tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(item.strip() for item in str(raw or "").split(",") if item.strip())
    return values or defaults


def _year_windows(start_date: str, end_date: str) -> tuple[tuple[str, str], ...]:
    start_year = int(start_date[:4])
    end_year = int(end_date[:4])
    return tuple(
        (
            max(start_date, f"{year}0101"),
            min(end_date, f"{year}1231"),
        )
        for year in range(start_year, end_year + 1)
    )


def _calendar_dates(start_date: str, end_date: str) -> tuple[str, ...]:
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    dates: list[str] = []
    cursor = start
    while cursor <= end:
        dates.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)
    return tuple(dates)


def _trade_dates(client: Any, options: RawReferenceDownloadOptions) -> tuple[str, ...]:
    frame = _fetch(
        client,
        "trade_cal",
        {
            "exchange": "SSE",
            "start_date": options.start_date,
            "end_date": options.end_date,
            "is_open": "1",
        },
        runtime=_runtime(options),
    )
    if frame.empty or "cal_date" not in frame:
        raise RuntimeError("trade_cal returned no open dates for stock_st download")
    return tuple(sorted(frame["cal_date"].astype(str).unique()))


def _download_stock_st(client: Any, options: RawReferenceDownloadOptions) -> Any:
    pd = pandas()
    frames: list[Any] = []
    for trade_date in _trade_dates(client, options):
        path = _daily_part_path(options, "stock_st", trade_date)
        if path.is_file():
            frame = pd.read_parquet(path)
        else:
            frame = _fetch(
                client,
                "stock_st",
                {"trade_date": trade_date},
                runtime=_runtime(options),
            )
            _atomic_parquet(frame, path)
        if not frame.empty:
            frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _download_index_weight(client: Any, options: RawReferenceDownloadOptions) -> Any:
    pd = pandas()
    frames = [
        frame
        for index_code in _csv_values(options.index_code, DEFAULT_INDEX_CODES)
        if not (
            frame := _fetch(
                client,
                "index_weight",
                {
                    "index_code": index_code,
                    "start_date": options.start_date,
                    "end_date": options.end_date,
                },
                runtime=_runtime(options),
            )
        ).empty
    ]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _download_stock_company(client: Any, options: RawReferenceDownloadOptions) -> Any:
    pd = pandas()
    frames = [
        frame
        for exchange in _csv_values(options.exchange, DEFAULT_EXCHANGES)
        if not (
            frame := _fetch(
                client,
                "stock_company",
                {"exchange": exchange},
                runtime=_runtime(options),
            )
        ).empty
    ]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _download_annual_windows(client: Any, options: RawReferenceDownloadOptions) -> Any:
    pd = pandas()
    frames = [
        frame
        for start_date, end_date in _year_windows(options.start_date, options.end_date)
        if not (
            frame := _fetch(
                client,
                options.dataset,
                {"start_date": start_date, "end_date": end_date},
                runtime=_runtime(options),
            )
        ).empty
    ]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _fetch_share_float_announcement(
    client: Any,
    ann_date: str,
    runtime: RequestRuntime,
) -> list[Any]:
    frame = _fetch(
        client,
        "share_float",
        {"ann_date": ann_date},
        runtime=runtime,
    )
    return [frame.assign(_source_truncated=False)] if not frame.empty else []


def _download_share_float(client: Any, options: RawReferenceDownloadOptions) -> Any:
    pd = pandas()
    frames: list[Any] = []
    for ann_date in _calendar_dates(options.start_date, options.end_date):
        path = _daily_part_path(options, "share_float", ann_date)
        if path.is_file():
            frame = pd.read_parquet(path)
        else:
            daily_frames = _fetch_share_float_announcement(
                client,
                ann_date,
                _runtime(options),
            )
            daily_frames = [daily for daily in daily_frames if not daily.empty]
            frame = (
                pd.concat(daily_frames, ignore_index=True, sort=False)
                if daily_frames
                else pd.DataFrame()
            )
            _atomic_parquet(frame, path)
        if not frame.empty:
            frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _normalize_reference_frame(frame: Any, spec: DatasetSpec) -> Any:
    if frame.empty:
        return frame
    for column in ("ts_code", "con_code"):
        if column in frame.columns:
            frame[column] = frame[column].map(normalize_ts_code)
    keys = [column for column in spec.primary_keys if column in frame.columns]
    if keys:
        frame = frame.drop_duplicates(keys, keep="last").sort_values(keys)
    return frame.reset_index(drop=True)


def download_raw_reference(options: RawReferenceDownloadOptions) -> dict[str, Any]:
    if options.dataset not in DATASET_SPECS or options.dataset == "index_weight_daily":
        raise ValueError(f"unsupported raw reference dataset: {options.dataset}")
    if options.page_size <= 0 or options.max_pages <= 0 or options.retries < 0:
        raise ValueError("page_size/max_pages must be positive and retries non-negative")
    if options.request_interval_seconds < 0:
        raise ValueError("request_interval_seconds cannot be negative")
    client = get_tushare_client(token_env=options.token_env, api_url=options.api_url)
    loaders = {
        "stock_st": _download_stock_st,
        "index_weight": _download_index_weight,
        "stock_company": _download_stock_company,
        "stk_managers": _download_annual_windows,
        "share_float": _download_share_float,
    }
    frame = _normalize_reference_frame(
        loaders[options.dataset](client, options),
        DATASET_SPECS[options.dataset],
    )
    out_path = _output_path(options.out_dir, f"{options.dataset}.parquet")
    frame.to_parquet(out_path, index=False)
    truncated_rows = (
        int(frame["_source_truncated"].fillna(False).astype(bool).sum())
        if "_source_truncated" in frame
        else 0
    )
    return {
        "dataset": options.dataset,
        "path": str(out_path),
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "start_date": options.start_date,
        "end_date": options.end_date,
        "quality_status": "partial" if truncated_rows else "complete",
        "truncated_rows": truncated_rows,
    }


def _open_calendar(path: Path, *, start_date: str, end_date: str) -> Any:
    pd = pandas()
    calendar = pd.read_parquet(path)
    date_column = "cal_date" if "cal_date" in calendar.columns else "trade_date"
    if date_column not in calendar:
        raise ValueError(f"trade calendar lacks cal_date/trade_date: {path}")
    if "is_open" in calendar:
        calendar = calendar[calendar["is_open"].astype(str).isin(("1", "True", "true"))]
    dates = calendar[date_column].astype(str).str.replace("-", "", regex=False)
    dates = dates[(dates >= start_date) & (dates <= end_date)]
    return pd.DataFrame({"trade_date": sorted(dates.unique())})


def build_normalized_index_weight_daily(
    raw_dir: str | Path,
    out_dir: str | Path,
    *,
    trade_cal_path: str | Path | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    pd = pandas()
    raw_path = _output_path(raw_dir, "index_weight.parquet")
    weight = pd.read_parquet(raw_path)
    if weight.empty:
        raise ValueError("index_weight raw asset is empty")
    weight["trade_date"] = weight["trade_date"].astype(str).str.replace("-", "", regex=False)
    weight = weight.sort_values(["index_code", "trade_date", "con_code"])
    start_date = str(weight["trade_date"].min())
    last_snapshot_date = str(weight["trade_date"].max())
    resolved_end_date = str(end_date or last_snapshot_date)
    if resolved_end_date < last_snapshot_date:
        raise ValueError(
            f"end_date {resolved_end_date} precedes the latest snapshot {last_snapshot_date}"
        )
    if trade_cal_path:
        calendar = _open_calendar(
            Path(trade_cal_path).expanduser().resolve(),
            start_date=start_date,
            end_date=resolved_end_date,
        )
    else:
        calendar = pd.DataFrame(
            {
                "trade_date": pd.bdate_range(
                    pd.to_datetime(start_date),
                    pd.to_datetime(resolved_end_date),
                ).strftime("%Y%m%d")
            }
        )

    frames: list[Any] = []
    for index_code, index_frame in weight.groupby("index_code", sort=True):
        snapshots = sorted(index_frame["trade_date"].unique())
        calendar_keys = calendar.assign(
            trade_key=calendar["trade_date"].astype("int64")
        ).sort_values("trade_key")
        snapshot_keys = pd.DataFrame({"snapshot_date": snapshots}).assign(
            snapshot_key=lambda frame: frame["snapshot_date"].astype("int64")
        )
        mapping = pd.merge_asof(
            calendar_keys,
            snapshot_keys,
            left_on="trade_key",
            right_on="snapshot_key",
            direction="backward",
        ).dropna(subset=["snapshot_date"])
        mapping = mapping.drop(columns=["trade_key", "snapshot_key"])
        expanded = mapping.merge(
            index_frame.rename(columns={"trade_date": "snapshot_date"}),
            on="snapshot_date",
            how="inner",
        )
        expanded["index_code"] = index_code
        expanded["drift_weight"] = expanded.groupby("trade_date")["weight"].transform(
            lambda values: values / values.sum()
        )
        frames.append(expanded)
    daily = pd.concat(frames, ignore_index=True, sort=False)
    daily = _normalize_reference_frame(daily, DATASET_SPECS["index_weight_daily"])
    out_path = _output_path(out_dir, "index_weight_daily.parquet")
    daily.to_parquet(out_path, index=False)
    return {
        "dataset": "index_weight_daily",
        "path": str(out_path),
        "rows": int(len(daily)),
        "start_date": str(daily["trade_date"].min()),
        "end_date": str(daily["trade_date"].max()),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def publish_reference_asset(
    dataset: str,
    raw_path: Path,
    asset_path: Path,
    target_date: str,
) -> dict[str, Any]:
    pd = pandas()
    frame = pd.read_parquet(raw_path)
    if frame.empty:
        raise ValueError(f"refusing to publish empty reference dataset: {dataset}")
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    version_path = asset_path.with_name(asset_path.name.replace("_latest", f"_{target_date}"))
    if version_path.is_file():
        existing = pd.read_parquet(version_path)
        if not existing.reset_index(drop=True).equals(frame.reset_index(drop=True)):
            raise FileExistsError(
                f"refusing to overwrite immutable reference version: {version_path}"
            )
    else:
        with tempfile.NamedTemporaryFile(
            suffix=".parquet", dir=asset_path.parent, prefix=f".{dataset}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            frame.to_parquet(temporary, index=False)
            os.replace(temporary, version_path)
        finally:
            temporary.unlink(missing_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".parquet", dir=asset_path.parent, prefix=f".{dataset}.latest.", delete=False
    ) as handle:
        latest_temporary = Path(handle.name)
    try:
        shutil.copyfile(version_path, latest_temporary)
        os.replace(latest_temporary, asset_path)
    finally:
        latest_temporary.unlink(missing_ok=True)
    receipt = {
        "schema_version": REFERENCE_RECEIPT_SCHEMA_VERSION,
        "dataset": dataset,
        "target_date": target_date,
        "published_at": datetime.now(UTC).isoformat(),
        "path": str(asset_path),
        "version_path": str(version_path),
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "sha256": _file_sha256(asset_path),
        "version_sha256": _file_sha256(version_path),
        "source_sha256": _file_sha256(raw_path),
        "quality_status": (
            "partial"
            if "_source_truncated" in frame
            and frame["_source_truncated"].fillna(False).astype(bool).any()
            else "complete"
        ),
        "truncated_rows": (
            int(frame["_source_truncated"].fillna(False).astype(bool).sum())
            if "_source_truncated" in frame
            else 0
        ),
    }
    receipt_path = asset_path.with_suffix(".receipt.json")
    _atomic_json(receipt_path, receipt)
    return {**receipt, "receipt_path": str(receipt_path)}


def publish_reference_assets(
    artifacts_root: str | Path | None,
    raw_dir: str | Path,
    target_date: str,
    *,
    allow_partial: bool = False,
) -> dict[str, Any]:
    from market_data_platform.paths import candidate_asset_paths

    raw_root = Path(raw_dir).expanduser().resolve()
    paths = candidate_asset_paths(artifacts_root)
    missing = [
        dataset for dataset in REFERENCE_DATASETS if not (raw_root / f"{dataset}.parquet").is_file()
    ]
    if missing and not allow_partial:
        raise FileNotFoundError("missing raw reference assets: " + ", ".join(missing))
    published = [
        publish_reference_asset(
            dataset,
            raw_root / f"{dataset}.parquet",
            paths[dataset],
            target_date,
        )
        for dataset in REFERENCE_DATASETS
        if (raw_root / f"{dataset}.parquet").is_file()
    ]
    return {"target_date": target_date, "published": published, "missing": missing}


def dataset_specs_payload() -> dict[str, Any]:
    return {
        "datasets": {
            name: {
                "api_name": spec.api_name,
                "fetch_granularity": spec.fetch_granularity,
                "primary_keys": list(spec.primary_keys),
            }
            for name, spec in DATASET_SPECS.items()
        }
    }


__all__ = [
    "DATASET_SPECS",
    "REFERENCE_DATASETS",
    "REFERENCE_RECEIPT_SCHEMA_VERSION",
    "RawReferenceDownloadOptions",
    "build_normalized_index_weight_daily",
    "dataset_specs_payload",
    "download_raw_reference",
    "publish_reference_asset",
    "publish_reference_assets",
]
