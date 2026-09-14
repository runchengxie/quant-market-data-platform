"""Immutable part IO and pagination for TuShare constraint references."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_common import pandas

CONSTRAINT_RECEIPT_SCHEMA = "market-data-platform.tushare-constraint-source.v1"


@dataclass(frozen=True)
class ConstraintDownloadOptions:
    dataset: str
    out_dir: str | Path
    start_date: str
    end_date: str
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    page_size: int = 5000
    max_pages: int = 100
    request_interval_seconds: float = 0.2
    retries: int = 5


@dataclass(frozen=True)
class PartRequest:
    key: str
    api_name: str
    params: dict[str, Any]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def atomic_parquet(frame: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".parquet", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "connection reset",
            "connection aborted",
            "connection refused",
            "connection broken",
            "rate limit",
            "remote end closed connection without response",
            "ssleoferror",
            "too many requests",
            "temporarily unavailable",
            "timeout",
            "timed out",
            "unexpected_eof_while_reading",
            "频率",
            "频次",
            "限频",
            "每分钟",
            "502",
            "503",
            "504",
        )
    )


def _query_frame(endpoint: Any, params: dict[str, Any], options: ConstraintDownloadOptions) -> Any:
    pd = pandas()
    for attempt in range(options.retries + 1):
        try:
            frame = pd.DataFrame(endpoint(**params))
            if options.request_interval_seconds > 0:
                time.sleep(options.request_interval_seconds)
            return frame
        except Exception as error:
            if attempt >= options.retries or not _is_retryable(error):
                raise
            time.sleep(min(30.0, 2.0 ** (attempt + 1)))
    raise RuntimeError("unreachable TuShare retry state")


def fetch_pages(
    client: Any,
    api_name: str,
    params: dict[str, Any],
    options: ConstraintDownloadOptions,
) -> tuple[Any, int]:
    pd = pandas()
    endpoint = getattr(client, api_name)
    frames: list[Any] = []
    for page in range(options.max_pages):
        frame = _query_frame(
            endpoint,
            {
                **params,
                "offset": page * options.page_size,
                "limit": options.page_size,
            },
            options,
        )
        if frame.empty:
            break
        frames.append(frame)
        if len(frame) < options.page_size:
            break
    else:
        raise RuntimeError(
            f"{api_name} reached max_pages={options.max_pages}; narrow the request window"
        )
    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    return combined, len(frames)


def year_windows(start_date: str, end_date: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (max(start_date, f"{year}0101"), min(end_date, f"{year}1231"))
        for year in range(int(start_date[:4]), int(end_date[:4]) + 1)
    )


def month_windows(start_date: str, end_date: str) -> tuple[tuple[str, str], ...]:
    windows = []
    start_year = int(start_date[:4])
    start_month = int(start_date[4:6])
    end_year = int(end_date[:4])
    end_month = int(end_date[4:6])
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        month_start = f"{year:04d}{month:02d}01"
        month_end = f"{year:04d}{month:02d}{monthrange(year, month)[1]:02d}"
        windows.append((max(start_date, month_start), min(end_date, month_end)))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return tuple(windows)


def part_receipt_path(path: Path) -> Path:
    return path.with_suffix(".receipt.json")


def _part_path(options: ConstraintDownloadOptions, request: PartRequest) -> Path:
    root = Path(options.out_dir).expanduser().resolve()
    return root / "_parts" / options.dataset / f"{options.dataset}_{request.key}.parquet"


def _verified_existing_part(
    path: Path,
    options: ConstraintDownloadOptions,
    request: PartRequest,
    api_url: str | None,
) -> Any:
    receipt_path = part_receipt_path(path)
    if not receipt_path.is_file():
        raise ValueError(f"existing constraint part lacks receipt: {path}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": CONSTRAINT_RECEIPT_SCHEMA,
        "dataset": options.dataset,
        "api_name": request.api_name,
        "params": request.params,
        "api_url": api_url,
        "page_size": options.page_size,
    }
    mismatched = [key for key, value in expected.items() if receipt.get(key) != value]
    if mismatched:
        raise ValueError(
            f"constraint part receipt context mismatch ({', '.join(mismatched)}): {path}"
        )
    if receipt.get("sha256") != sha256(path):
        raise ValueError(f"constraint part receipt hash mismatch: {path}")
    return pandas().read_parquet(path)


def _write_part(
    frame: Any,
    path: Path,
    *,
    context: tuple[ConstraintDownloadOptions, PartRequest, str | None],
    pages: int,
) -> None:
    options, request, api_url = context
    atomic_parquet(frame, path)
    atomic_json(
        part_receipt_path(path),
        {
            "schema_version": CONSTRAINT_RECEIPT_SCHEMA,
            "dataset": options.dataset,
            "api_name": request.api_name,
            "params": request.params,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "api_url": api_url,
            "page_size": options.page_size,
            "pages_with_rows": pages,
            "rows": int(len(frame)),
            "columns": list(frame.columns),
            "sha256": sha256(path),
        },
    )


def load_or_fetch_part(
    client: Any,
    options: ConstraintDownloadOptions,
    request: PartRequest,
    *,
    api_url: str | None,
) -> Any:
    path = _part_path(options, request)
    if path.is_file():
        return _verified_existing_part(path, options, request, api_url)
    frame, pages = fetch_pages(client, request.api_name, request.params, options)
    _write_part(frame, path, context=(options, request, api_url), pages=pages)
    return frame


__all__ = [
    "CONSTRAINT_RECEIPT_SCHEMA",
    "ConstraintDownloadOptions",
    "PartRequest",
    "atomic_json",
    "atomic_parquet",
    "fetch_pages",
    "load_or_fetch_part",
    "month_windows",
    "part_receipt_path",
    "sha256",
    "year_windows",
]
