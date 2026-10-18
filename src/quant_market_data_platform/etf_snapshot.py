"""Immutable, symbol-bounded ETF acquisitions with explicit incomplete receipts."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant_market_data_platform.providers._adapters_part01 import _tushare_runtime
from quant_market_data_platform.providers._client import (
    _call_tushare_api,
    _call_tushare_endpoint,
    _is_quota_error,
    _is_retryable_provider_error,
    _TushareEndpointFailover,
)
from quant_market_data_platform.providers.tushare_common import TushareRequestPolicy


class SnapshotUnavailable(ValueError):
    """Token-safe reason for an incomplete snapshot."""


def _instant(now: Callable[[], datetime]) -> datetime:
    value = now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must be timezone aware")
    return value


def acquire_etf_snapshot(  # noqa: PLR0913
    *,
    symbols: tuple[str, ...],
    start_date: str,
    end_date: str,
    baseline_manifest: Path | None,
    output: Path,
    token_env: str,
    api_url: str | None,
    max_requests: int = 96,
    deadline: datetime | None = None,
    now: Callable[[], datetime],
    client: object | None = None,
) -> dict[str, Any]:
    """Acquire a new snapshot; incomplete work is never admitted as a panel."""
    if (
        not symbols
        or len(set(symbols)) != len(symbols)
        or any(not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", symbol) for symbol in symbols)
    ):
        raise ValueError("invalid or duplicate ETF symbol")
    for day in (start_date, end_date):
        if not re.fullmatch(r"[0-9]{8}", day):
            raise ValueError("date must be YYYYMMDD")
        datetime.strptime(day, "%Y%m%d")
    if start_date > end_date or not 1 <= max_requests <= 96:
        raise ValueError("invalid date range or request budget")
    started = _instant(now)
    if deadline is not None and (deadline.tzinfo is None or deadline <= started):
        raise ValueError("deadline must be aware and in the future")
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("symlink output is forbidden")
    output.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {
        "schema_version": "marketdata.etf_snapshot.v1",
        "status": "incomplete",
        "symbols": list(symbols),
        "start_date": start_date,
        "end_date": end_date,
        "baseline_sha256": None,
        "capture_started_at": started.isoformat(),
        "capture_completed_at": None,
        "vendor_publication_at": None,
        "request_count": 0,
        "files": {},
        "reasons": [],
    }
    try:
        # Baseline merging is admitted only by a verified pair, never by adjusted levels alone.
        baseline = _read_baseline(baseline_manifest, symbols, start_date)
        if baseline_manifest is not None:
            result["baseline_sha256"] = _file_sha256(baseline_manifest)
        policy = TushareRequestPolicy(attempts=1, request_timeout_seconds=30)
        pro, policy, _ = _tushare_runtime(
            token_env=token_env,
            api_url=api_url,
            request_policy=policy,
            request_options={"client": client} if client is not None else {},
        )

        # One configured endpoint per attempt: wrapper failover would hide network calls.
        pro = _single_endpoint(pro)
        request = _requester(pro, policy, result, max_requests, deadline, now)

        calendar = request("trade_cal", exchange="SSE", start_date=start_date, end_date=end_date)
        _retain(output, result, "calendar", calendar)
        daily, factors = _download_pairs(request, symbols, start_date, end_date, output)
        _validate_coverage(calendar, daily, symbols, start_date, end_date)
        if baseline is not None:
            old_daily, old_factors, old_calendar, baseline_start = baseline
            daily = pd.concat([old_daily, daily], ignore_index=True)
            factors = pd.concat([old_factors, factors], ignore_index=True)
            calendar = pd.concat([old_calendar, calendar], ignore_index=True)
            result["start_date"] = baseline_start
            _retain(output, result, "calendar", calendar)
        _validate_coverage(calendar, daily, symbols, result["start_date"], end_date)
        panel = _adjusted_panel(daily, factors, result["start_date"], end_date)
        for key, frame in (("daily", daily), ("factors", factors), ("panel", panel)):
            _retain(output, result, key, frame)
        if deadline is not None and _instant(now) > deadline:
            raise ValueError("acquisition_deadline_exceeded")
        result["status"] = "unreviewed"
    except SnapshotUnavailable as error:
        result["reasons"].append(str(error))
    except Exception:
        # Provider messages may contain credentials. Only stable reason codes are persisted.
        result["reasons"].append("acquisition_incomplete")
    result["capture_completed_at"] = _instant(now).isoformat()
    (output / "manifest.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def _retain(output: Path, result: dict[str, Any], key: str, frame: Any) -> None:
    path = output / f"{key}.parquet"
    frame.to_parquet(path, index=False)
    result["files"][key] = {
        "path": path.name,
        "sha256": _file_sha256(path),
    }


def _download_pairs(
    request: Callable[..., Any],
    symbols: tuple[str, ...],
    start_date: str,
    end_date: str,
    output: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    pages = output / "raw-pages"
    pages.mkdir()
    frames: dict[str, list[pd.DataFrame]] = {"daily": [], "factors": []}
    for symbol in symbols:
        for key, api in (("daily", "fund_daily"), ("factors", "fund_adj")):
            chunks: list[pd.DataFrame] = []
            offset = 0
            while True:
                frame = request(
                    api,
                    ts_code=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    limit=5000,
                    offset=offset,
                )
                if not isinstance(frame, pd.DataFrame) or frame.empty:
                    break
                frame.to_parquet(pages / f"{api}-{symbol}-{offset}.parquet", index=False)
                if "ts_code" not in frame or set(frame.ts_code) != {symbol}:
                    raise ValueError("unexpected_symbol")
                if chunks and frame.equals(chunks[-1]):
                    raise SnapshotUnavailable("repeated_provider_page")
                chunks.append(frame)
                if len(frame) < 5000:
                    break
                offset += len(frame)
            if not chunks:
                raise ValueError("missing_symbol_rows")
            frames[key].append(pd.concat(chunks, ignore_index=True))
    return pd.concat(frames["daily"], ignore_index=True), pd.concat(
        frames["factors"], ignore_index=True
    )


def _adjusted_panel(
    daily: pd.DataFrame, factors: pd.DataFrame, start_date: str, end_date: str
) -> pd.DataFrame:
    keys = ["ts_code", "trade_date"]
    amounts = daily["amount"].to_numpy(dtype=float)
    if not (np.isfinite(amounts) & (amounts >= 0)).all():
        raise SnapshotUnavailable("invalid_amount")
    for frame in (daily, factors):
        if frame.duplicated(keys).any():
            raise ValueError("duplicate_keys")
        if not frame.trade_date.astype(str).between(start_date, end_date).all():
            raise ValueError("out_of_range")
    paired = daily.merge(
        factors[keys + ["adj_factor"]], on=keys, how="outer", validate="one_to_one", indicator=True
    )
    if not paired["_merge"].eq("both").all():
        raise ValueError("incomplete_factor_pair")
    for column in ("close", "adj_factor"):
        values = paired[column].to_numpy(dtype=float)
        if not (np.isfinite(values) & (values > 0)).all():
            raise ValueError("invalid_price_or_factor")
    paired = paired.sort_values(keys)
    latest = paired.groupby("ts_code")["adj_factor"].transform("last")
    paired["adjusted_close"] = paired.close * paired.adj_factor / latest
    panel = paired.pivot(index="trade_date", columns="ts_code", values="adjusted_close")
    return panel.reset_index()


def _requester(  # noqa: PLR0913
    pro: Any,
    policy: TushareRequestPolicy,
    result: dict[str, Any],
    max_requests: int,
    deadline: datetime | None,
    now: Callable[[], datetime],
) -> Callable[..., Any]:
    def request(api: str, **kwargs: Any) -> Any:
        for attempt in range(3):
            if result["request_count"] >= max_requests:
                raise SnapshotUnavailable("request_budget_exhausted")
            if deadline is not None and _instant(now) >= deadline:
                raise SnapshotUnavailable("acquisition_deadline_exceeded")
            timeout = (
                30.0 if deadline is None else min(30.0, (deadline - _instant(now)).total_seconds())
            )
            if timeout <= 0:
                raise SnapshotUnavailable("acquisition_deadline_exceeded")
            pro._DataApi__timeout = timeout
            result["request_count"] += 1
            try:
                return _call_tushare_api(
                    lambda: _call_tushare_endpoint(pro, api, kwargs),
                    policy=policy,
                )
            except Exception as error:
                if _is_quota_error(error) or not _is_retryable_provider_error(error):
                    raise SnapshotUnavailable("provider_request_rejected") from None
                if attempt == 2:
                    raise SnapshotUnavailable("provider_retries_exhausted") from None
                wait = float(2**attempt)
                if deadline is not None:
                    remaining = (deadline - _instant(now)).total_seconds()
                    if remaining <= wait:
                        raise SnapshotUnavailable("acquisition_deadline_exceeded") from None
                time.sleep(wait)
        raise SnapshotUnavailable("provider_retries_exhausted")

    return request


def _validate_coverage(
    calendar: pd.DataFrame, daily: pd.DataFrame, symbols: tuple[str, ...], start: str, end: str
) -> None:
    if not {"cal_date", "is_open"}.issubset(calendar.columns):
        raise SnapshotUnavailable("invalid_calendar")
    dates = calendar.cal_date.astype(str)
    expected = set(pd.date_range(start, end).strftime("%Y%m%d"))
    if dates.duplicated().any() or set(dates) != expected:
        raise SnapshotUnavailable("incomplete_calendar")
    if not calendar.is_open.isin([0, 1]).all():
        raise SnapshotUnavailable("invalid_calendar")
    sessions = set(dates[calendar.is_open.eq(1)])
    if not sessions:
        raise SnapshotUnavailable("no_open_sessions")
    for symbol in symbols:
        actual = set(daily.loc[daily.ts_code.eq(symbol), "trade_date"].astype(str))
        if actual != sessions:
            raise SnapshotUnavailable("missing_calendar_coverage")


def snapshot_file(root: Path, record: dict[str, Any]) -> Path:
    """Resolve only a hash-bound regular file directly below a snapshot."""
    relative = Path(record["path"])
    if relative.is_absolute() or len(relative.parts) != 1:
        raise SnapshotUnavailable("unsafe_snapshot_path")
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise SnapshotUnavailable("unsafe_snapshot_path")
    if _file_sha256(path) != record["sha256"]:
        raise SnapshotUnavailable("baseline_hash_mismatch")
    return path


def _read_baseline(
    manifest_path: Path | None, symbols: tuple[str, ...], start: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str] | None:
    if manifest_path is None:
        return None
    if manifest_path.is_symlink():
        raise SnapshotUnavailable("unsafe_snapshot_path")
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest.get("schema_version") != "marketdata.etf_snapshot.v1"
        or manifest.get("status") != "unreviewed"
        or not set(symbols).issubset(manifest["symbols"])
        or manifest["end_date"] >= start
    ):
        raise SnapshotUnavailable("baseline_query_mismatch")
    files = manifest["files"]
    # Validate every supplied identity before reading rows or creating a client.
    paths = {key: snapshot_file(manifest_path.parent, record) for key, record in files.items()}
    daily = pd.read_parquet(
        paths["daily"],
        columns=["ts_code", "trade_date", "close", "amount"],
        filters=[("ts_code", "in", list(symbols))],
    )
    factors = pd.read_parquet(
        paths["factors"],
        columns=["ts_code", "trade_date", "adj_factor"],
        filters=[("ts_code", "in", list(symbols))],
    )
    calendar = pd.read_parquet(paths["calendar"], columns=["cal_date", "is_open"])
    _validate_coverage(calendar, daily, symbols, manifest["start_date"], manifest["end_date"])
    _adjusted_panel(daily, factors, manifest["start_date"], manifest["end_date"])
    return daily, factors, calendar, manifest["start_date"]


def _single_endpoint(pro: Any) -> Any:
    return pro._client if isinstance(pro, _TushareEndpointFailover) else pro


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
