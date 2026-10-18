"""Fresh provider requests with immutable receipt directories; never a backfill."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from quant_market_data_platform.providers._client import get_tushare_client

PROTOCOL = "overseas-receipt-v1"
SOURCES = {
    "spx": ("index_global", {"ts_code": "SPX"}),
    "hsi": ("index_global", {"ts_code": "HSI"}),
    "n225": ("index_global", {"ts_code": "N225"}),
    "ks11": ("index_global", {"ts_code": "KS11"}),
    "usdcnh": ("fx_daily", {"ts_code": "USDCNH.FXCM"}),
    "usdjpy": ("fx_daily", {"ts_code": "USDJPY.FXCM"}),
    "eurusd": ("fx_daily", {"ts_code": "EURUSD.FXCM"}),
}


def now() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime) -> str:
    return value.isoformat()


def seal(path: Path, payload: dict) -> None:
    """Exclusive creation: retries and concurrent processes cannot replace evidence."""
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, allow_nan=False)
        handle.write("\n")


def source_close(day: str, market: str) -> datetime:
    date = datetime.strptime(day, "%Y%m%d")
    if market in {"usdcnh", "usdjpy", "eurusd"}:
        return (date + timedelta(days=1)).replace(tzinfo=UTC)
    hour, minute, zone = {
        "spx": (16, 0, "America/New_York"),
        "hsi": (16, 10, "Asia/Hong_Kong"),
        "n225": (15, 30, "Asia/Tokyo"),
        "ks11": (15, 30, "Asia/Seoul"),
    }[market]
    return date.replace(hour=hour, minute=minute, tzinfo=ZoneInfo(zone))


def capture(root: Path, mode: str, client=None) -> Path:
    started = now()
    local = started.astimezone(ZoneInfo("Asia/Shanghai"))
    day = local.strftime("%Y%m%d")
    cutoff = local.replace(hour=8, minute=45, second=0, microsecond=0)
    run = root / day / (started.strftime("%H%M%S%fZ-") + uuid4().hex)
    run.mkdir(parents=True, exist_ok=False)
    seal(
        run / "started.json",
        {
            "protocol": PROTOCOL,
            "mode": mode,
            "started_utc": stamp(started),
            "target_date": day,
            "cutoff_utc": stamp(cutoff.astimezone(UTC)),
            "source_revision": "v1",
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "token_env": "TUSHARE_TOKEN_2",
        },
    )
    records = {}
    client_error = None
    try:
        client = client or get_tushare_client(
            token_env="TUSHARE_TOKEN_2",
            request_timeout_seconds=12,
        )
    except Exception as exc:
        client_error = type(exc).__name__
    requests = {
        **SOURCES,
        "calendar": (
            "trade_cal",
            {
                "exchange": "SSE",
                "start_date": day,
                "end_date": day,
            },
        ),
    }
    for name, (api, parameters) in requests.items():
        request = dict(parameters)
        if name != "calendar":
            request.update(start_date=(local - timedelta(days=21)).strftime("%Y%m%d"), end_date=day)
        began = now()
        record = {"api": api, "parameters": request, "request_started_utc": stamp(began)}
        try:
            if client_error:
                raise RuntimeError(client_error)
            frame = getattr(client, api)(**request)
            content = frame.to_csv(index=False).encode("utf-8")
            path = run / (name + ".csv")
            with path.open("xb") as handle:
                handle.write(content)
            record.update(
                status="ok" if len(frame) else "missing",
                rows=len(frame),
                sha256=hashlib.sha256(content).hexdigest(),
                file=path.name,
            )
            if name != "calendar" and len(frame):
                dates = sorted(set(frame["trade_date"].astype(str)))
                record["source_dates"] = dates
                record["assumed_regular_close_utc"] = {
                    date: stamp(source_close(date, name).astimezone(UTC)) for date in dates
                }
                record["close_semantics"] = (
                    "regular schedule assumption; not provider publication time"
                )
        except Exception as exc:
            record.update(status="failed", error_class=type(exc).__name__)
        record["route_host"] = urlsplit(getattr(client, "_DataApi__http_url", "")).hostname
        record["request_completed_utc"] = stamp(now())
        records[name] = record
    completed = now()
    on_time = (
        mode == "scheduled"
        and local.weekday() < 5
        and cutoff - timedelta(minutes=2) <= local
        and completed <= cutoff.astimezone(UTC)
    )
    seal(
        run / "receipt.json",
        {
            "protocol": PROTOCOL,
            "mode": mode,
            "target_date": day,
            "started_utc": stamp(started),
            "completed_utc": stamp(completed),
            "cutoff_utc": stamp(cutoff.astimezone(UTC)),
            "timely_capture": on_time,
            "late": completed > cutoff.astimezone(UTC),
            "sources": records,
        },
    )
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=["capture_probe", "scheduled"], required=True)
    args = parser.parse_args()
    print(capture(args.root, args.mode))


if __name__ == "__main__":
    main()
