"""Probe TuShare historical A-share minute availability without writing assets."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pandas as pd
import requests

from market_data_platform.providers._env import _resolve_token, resolve_tushare_api_url

DEFAULT_SYMBOLS = ("000001.SZ", "600000.SH", "830799.BJ")


def probe_symbol_frame(frame: Any, *, symbol: str, trade_date: str) -> dict[str, Any]:
    rows = int(len(frame)) if frame is not None else 0
    result: dict[str, Any] = {
        "symbol": symbol,
        "trade_date": trade_date,
        "rows": rows,
        "complete_241": False,
    }
    if rows == 0:
        result["status"] = "empty"
        return result
    if not isinstance(frame, pd.DataFrame) or "trade_time" not in frame:
        result["status"] = "invalid_schema"
        return result
    times = pd.to_datetime(frame["trade_time"], errors="coerce")
    valid = times.notna() & (times.dt.strftime("%Y%m%d") == trade_date)
    complete = rows == 241 and bool(valid.all()) and int(times.nunique()) == 241
    result["complete_241"] = complete
    result["status"] = "complete" if complete else "partial_or_invalid"
    return result


def summarize_probe(probes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for probe in probes:
        by_date.setdefault(str(probe["trade_date"]), []).append(probe)
    confirmed = sorted(
        date
        for date, rows in by_date.items()
        if rows and all(bool(row.get("complete_241")) for row in rows)
    )
    return {
        "confirmed_dates": confirmed,
        "earliest_confirmed_date": confirmed[0] if confirmed else None,
        "probe_count": len(probes),
        "status": "confirmed" if confirmed else "no_confirmed_date",
    }


class _HttpClient:
    def __init__(self, *, token: str, url: str) -> None:
        self._token = token
        self._url = url

    def stk_mins(self, **params: str) -> pd.DataFrame:
        response = requests.post(
            self._url,
            json={"api_name": "stk_mins", "token": self._token, "params": params},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("code", -1)) != 0:
            raise RuntimeError(f"TuShare stk_mins failed: {payload.get('msg', '')}")
        data = payload.get("data") or {}
        return pd.DataFrame(data.get("items") or [], columns=data.get("fields") or None)


def _client(token_env: str, api_url: str | None) -> Any:
    token = _resolve_token(None, token_env)
    resolved = resolve_tushare_api_url(api_url, token_env=token_env)
    if not resolved:
        raise RuntimeError("No TuShare API URL configured")
    return _HttpClient(token=token, url=resolved)


def run_probe(
    client: Any,
    *,
    dates: Sequence[str],
    symbols: Sequence[str],
) -> dict[str, Any]:
    probes: list[dict[str, Any]] = []
    for trade_date in dates:
        for symbol in symbols:
            frame = client.stk_mins(
                ts_code=symbol,
                freq="1min",
                start_date=f"{trade_date} 09:30:00",
                end_date=f"{trade_date} 15:00:00",
            )
            probes.append(probe_symbol_frame(frame, symbol=symbol, trade_date=trade_date))
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "provider": "tushare",
        "api": "stk_mins",
        "frequency": "1min",
        "symbols": list(symbols),
        "dates": list(dates),
        "probes": probes,
        **summarize_probe(probes),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--token-env", default="TUSHARE_TOKEN_2")
    parser.add_argument("--api-url")
    args = parser.parse_args(argv)
    result = run_probe(
        _client(args.token_env, args.api_url), dates=args.dates, symbols=args.symbols
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "confirmed" else 75


if __name__ == "__main__":
    raise SystemExit(main())
