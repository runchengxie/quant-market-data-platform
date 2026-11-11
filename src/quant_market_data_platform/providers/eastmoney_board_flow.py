"""Eastmoney intraday sector fund-flow provider.

Two public quote endpoints are used:

* ``/api/qt/clist/get`` with ``fs=m:90+t:N`` lists every board together with its
  accumulated main-force net inflow at the moment of the request.
* ``/api/qt/stock/fflow/kline/get`` with ``klt=1`` returns one board's minute
  series for the current trading day, starting at 09:31.

The older ``/api/qt/stock/fflow/KLChart`` route used by some community projects
answers ``404``; it is intentionally not implemented here.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BOARD_TYPE_QUERY = {
    "industry": "m:90+t:2",
    "concept": "m:90+t:3",
    "region": "m:90+t:1",
}
BOARD_TYPES = tuple(BOARD_TYPE_QUERY)

BOARD_LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
BOARD_FLOW_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"

# Public quote token used by quote.eastmoney.com; it is a page token, not a secret.
EASTMONEY_UT = "bd1d9ddb04089700cf9c27f6f7426281"
EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

SNAPSHOT_FIELDS = "f12,f14,f62,f184,f3,f6"
FLOW_FIELDS1 = "f1,f2,f3,f7"
FLOW_FIELDS2 = "f51,f52,f53,f54,f55,f56"
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 30
MAIN_NET_TOLERANCE = 1.0
# 102 and 103 are the quote service's transient throttle codes, not request errors.
TRANSIENT_RC = frozenset({102, 103})


class BoardFlowError(RuntimeError):
    """Raised when the Eastmoney board-flow response cannot be trusted."""


class BoardFlowDataError(BoardFlowError):
    """Raised when a response is reachable but carries no usable payload."""


@dataclass(frozen=True)
class BoardRow:
    """One board's accumulated flow at a single observation time."""

    board_code: str
    board_name: str
    board_type: str
    main_net_yuan: float | None
    main_net_ratio_pct: float | None
    change_pct: float | None
    amount_yuan: float | None


@dataclass(frozen=True)
class MinuteFlowRow:
    """One minute of one board's cumulative flow, in yuan."""

    board_code: str
    board_type: str
    trade_time: datetime
    main_net_yuan: float
    small_net_yuan: float
    mid_net_yuan: float
    large_net_yuan: float
    xlarge_net_yuan: float


Fetcher = Callable[[str, dict[str, str]], dict[str, Any]]


def default_fetch_json(
    url: str,
    params: dict[str, str],
    *,
    timeout: float = 15.0,
    attempts: int = 3,
    retry_delay: float = 1.0,
) -> dict[str, Any]:
    """Fetch a JSON document with bounded retries for transient failures."""
    query = urlencode(params)
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        request = Request(f"{url}?{query}", headers=EASTMONEY_HEADERS)
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            last_error = error
            if error.code not in (429, 500, 502, 503, 504):
                raise BoardFlowError(f"Eastmoney request failed with HTTP {error.code}.") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
        else:
            if not isinstance(payload, dict):
                raise BoardFlowDataError("Eastmoney response is not a JSON object.")
            if payload.get("rc") not in TRANSIENT_RC:
                return payload
            last_error = BoardFlowError(
                f"Eastmoney throttled the request with rc={payload.get('rc')}."
            )
        if attempt < attempts:
            time.sleep(retry_delay * attempt)
    raise BoardFlowError(f"Eastmoney request failed after {attempts} attempts: {last_error}")


def _payload_data(payload: dict[str, Any], *, context: str) -> dict[str, Any]:
    rc = payload.get("rc")
    if rc not in (0, None):
        raise BoardFlowError(f"{context} returned rc={rc!r}.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise BoardFlowDataError(f"{context} returned no data payload.")
    return data


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text in {"", "-", "--", "None", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_board_rows(data: dict[str, Any], *, board_type: str) -> list[BoardRow]:
    raw_rows = data.get("diff") or []
    if isinstance(raw_rows, dict):
        raw_rows = list(raw_rows.values())
    rows: list[BoardRow] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        code = _as_text(item.get("f12"))
        name = _as_text(item.get("f14"))
        if not code or not name:
            continue
        rows.append(
            BoardRow(
                board_code=code,
                board_name=name,
                board_type=board_type,
                main_net_yuan=_as_float(item.get("f62")),
                main_net_ratio_pct=_as_float(item.get("f184")),
                change_pct=_as_float(item.get("f3")),
                amount_yuan=_as_float(item.get("f6")),
            )
        )
    return rows


def _dedupe(rows: list[BoardRow]) -> list[BoardRow]:
    seen: dict[str, BoardRow] = {}
    for row in rows:
        seen.setdefault(row.board_code, row)
    return list(seen.values())


def parse_minute_klines(klines: Any, *, board_code: str, board_type: str) -> list[MinuteFlowRow]:
    """Parse ``data.klines`` strings into validated minute rows."""
    if not isinstance(klines, list):
        raise BoardFlowDataError(f"Board {board_code} returned no minute klines.")
    rows: list[MinuteFlowRow] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 6:
            raise BoardFlowDataError(f"Board {board_code} returned a malformed kline: {line!r}.")
        try:
            trade_time = datetime.fromisoformat(parts[0].strip())
        except ValueError as error:
            raise BoardFlowDataError(
                f"Board {board_code} returned an invalid trade time: {parts[0]!r}."
            ) from error
        values = [_as_float(part) for part in parts[1:6]]
        if any(value is None for value in values):
            raise BoardFlowDataError(f"Board {board_code} returned a non-numeric kline: {line!r}.")
        main, small, mid, large, xlarge = (float(value) for value in values if value is not None)
        rows.append(
            MinuteFlowRow(
                board_code=board_code,
                board_type=board_type,
                trade_time=trade_time,
                main_net_yuan=main,
                small_net_yuan=small,
                mid_net_yuan=mid,
                large_net_yuan=large,
                xlarge_net_yuan=xlarge,
            )
        )
    if not rows:
        raise BoardFlowDataError(f"Board {board_code} returned an empty minute series.")
    _validate_curve(rows, board_code=board_code)
    return rows


def _validate_curve(rows: list[MinuteFlowRow], *, board_code: str) -> None:
    for previous, current in zip(rows, rows[1:], strict=False):
        if current.trade_time <= previous.trade_time:
            raise BoardFlowDataError(
                f"Board {board_code} minute series is not strictly increasing at "
                f"{current.trade_time.isoformat()}."
            )
    for row in rows:
        combined = row.large_net_yuan + row.xlarge_net_yuan
        tolerance = max(MAIN_NET_TOLERANCE, abs(row.main_net_yuan) * 1e-6)
        if abs(row.main_net_yuan - combined) > tolerance:
            raise BoardFlowDataError(
                f"Board {board_code} main net inflow disagrees with large+xlarge at "
                f"{row.trade_time.isoformat()}: {row.main_net_yuan} vs {combined}."
            )


@dataclass(frozen=True)
class BoardFlowClient:
    """Thin client for the two Eastmoney board-flow routes."""

    fetcher: Fetcher = default_fetch_json
    board_market: int = 90
    page_size: int = DEFAULT_PAGE_SIZE
    max_pages: int = DEFAULT_MAX_PAGES

    def fetch_boards(self, board_type: str) -> list[BoardRow]:
        """Return every board of one type with its current accumulated flow."""
        if board_type not in BOARD_TYPE_QUERY:
            raise ValueError(f"Unsupported board type '{board_type}'.")
        collected: list[BoardRow] = []
        total: int | None = None
        for page in range(1, self.max_pages + 1):
            payload = self.fetcher(
                BOARD_LIST_URL,
                {
                    "ut": EASTMONEY_UT,
                    "pn": str(page),
                    "pz": str(self.page_size),
                    "po": "0",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f12",
                    "fs": BOARD_TYPE_QUERY[board_type],
                    "fields": SNAPSHOT_FIELDS,
                },
            )
            data = _payload_data(payload, context=f"Board list page {page}")
            rows = _parse_board_rows(data, board_type=board_type)
            if total is None:
                total = int(_as_float(data.get("total")) or 0)
            if not rows:
                break
            collected.extend(rows)
            if total and len(_dedupe(collected)) >= total:
                break
            if len(rows) < self.page_size:
                break
        result = _dedupe(collected)
        if not result:
            raise BoardFlowDataError(f"No {board_type} boards returned.")
        return result

    def fetch_minute_curve(self, board_code: str, *, board_type: str) -> list[MinuteFlowRow]:
        """Return today's minute flow curve for one board."""
        payload = self.fetcher(
            BOARD_FLOW_URL,
            {
                "ut": EASTMONEY_UT,
                "secid": f"{self.board_market}.{board_code}",
                "klt": "1",
                "lmt": "0",
                "fields1": FLOW_FIELDS1,
                "fields2": FLOW_FIELDS2,
            },
        )
        data = _payload_data(payload, context=f"Board {board_code} minute flow")
        return parse_minute_klines(data.get("klines"), board_code=board_code, board_type=board_type)
