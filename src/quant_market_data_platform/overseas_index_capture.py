"""Bounded operational index acquisition; independent of research capture windows."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from requests.exceptions import RequestException

from quant_market_data_platform.providers._client import get_tushare_client

PROTOCOL = "overseas-index-operational-v1"
SYMBOLS = frozenset({"HSI", "N225", "KS11"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _seal(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, allow_nan=False)
        handle.write("\n")


def _failure(exc: Exception) -> dict:
    # Inspect only known markers; never persist provider messages or credentials.
    text = str(exc).lower()
    if isinstance(exc, PermissionError) or any(
        marker in text for marker in ("没有访问该接口的权限", "permission denied", "access denied")
    ):
        category = "permission"
    elif isinstance(exc, (TimeoutError, ConnectionError, RequestException)):
        category = "transport"
    else:
        category = "provider"
    return {"status": "failed", "error_class": type(exc).__name__, "failure_category": category}


def _source(client: Any, symbol: str, start: str, end: str, run: Path) -> dict:
    record: dict[str, Any] = {
        "api": "index_global",
        "request_started_utc": _now(),
        "provider": "tushare-compatible",
    }
    try:
        frame = client.index_global(ts_code=symbol, start_date=start, end_date=end)
        raw = frame.to_csv(index=False).encode("utf-8")
        path = run / f"{symbol}.csv"
        with path.open("xb") as handle:
            handle.write(raw)
        record.update(
            status="ok" if len(frame) else "empty",
            rows=len(frame),
            file=path.name,
            sha256=hashlib.sha256(raw).hexdigest(),
            source_dates=sorted(set(frame["trade_date"].astype(str))),
            route_host=urlsplit(getattr(client, "_DataApi__http_url", "")).hostname,
        )
    except Exception as exc:
        record.update(_failure(exc))
    record["request_completed_utc"] = _now()
    return record


def capture(root: Path, trade_date: str, symbols: list[str], *, client: Any = None) -> Path:
    """Write an exclusive finalized receipt. Caller enforces the total deadline."""
    target = datetime.strptime(trade_date, "%Y%m%d")
    if target.strftime("%Y%m%d") != trade_date:
        raise ValueError("date must be YYYYMMDD")
    if not symbols or len(set(symbols)) != len(symbols) or not set(symbols) <= SYMBOLS:
        raise ValueError("symbols must be a nonempty unique subset of HSI,N225,KS11")
    run = root / trade_date / uuid4().hex
    run.mkdir(parents=True, exist_ok=False)
    receipt: dict[str, Any] = {
        "protocol": PROTOCOL,
        "requested_date": trade_date,
        "started_utc": _now(),
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    _seal(run / "started.json", receipt)
    client_error = None
    if client is None:
        try:
            client = get_tushare_client(token_env="TUSHARE_TOKEN_2", request_timeout_seconds=12)
        except Exception as exc:
            client_error = _failure(exc)
    sources = {}
    for symbol in symbols:
        if client_error:
            sources[symbol] = {
                "status": "failed",
                "api": "index_global",
                **client_error,
                "request_started_utc": _now(),
                "request_completed_utc": _now(),
            }
        else:
            sources[symbol] = _source(
                client, symbol, (target - timedelta(days=21)).strftime("%Y%m%d"), trade_date, run
            )
    successful = sum(value["status"] == "ok" for value in sources.values())
    receipt.update(
        sources=sources,
        completed_utc=_now(),
        status="complete" if successful == len(symbols) else "partial" if successful else "failed",
    )
    path = run / "receipt.json"
    _seal(path, receipt)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--symbols", default="HSI,N225,KS11")
    args = parser.parse_args()
    path = capture(args.root, args.date, args.symbols.split(","))
    print(json.dumps({"receipt": str(path.resolve())}))
    return 0 if json.loads(path.read_text())["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
