"""Read-only structural quality checks for raw market-data files."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq

from market_data_platform.l2_ordering import SequenceQualityState, detect_ordering_columns

_DAY_RE = re.compile(r"(?:order|trades|deal|snapshot)_(\d{4})-?(\d{2})-?(\d{2})\.parquet$")
_ID_COLUMNS = ("OrderID", "DealID")
_NUMERIC_COLUMNS = (
    "time_ms",
    "OrderTime",
    "DealTime",
    "TickTime",
    "Price",
    "Volume",
    "LastPrice",
)
_BASE_REQUIRED_COLUMNS = ("ticker", "TradingDay", "time_ms", "Price", "Volume")


def _scalar(value: Any) -> Any:
    return value.as_py() if hasattr(value, "as_py") else value


def _compute(name: str, *args: Any) -> Any:
    return getattr(pc, name)(*args)


def _expected_day(path: Path) -> int | None:
    match = _DAY_RE.search(path.name)
    return int("".join(match.groups())) if match else None


def _update_range(array: Any, name: str, minimum: dict[str, Any], maximum: dict[str, Any]) -> None:
    if array.null_count == len(array):
        return
    low = _scalar(_compute("min", array))
    high = _scalar(_compute("max", array))
    if low is not None:
        minimum[name] = low if name not in minimum else min(minimum[name], low)
    if high is not None:
        maximum[name] = high if name not in maximum else max(maximum[name], high)


def _bounded_values(array: Any, target: set[Any], limit: int) -> None:
    if len(target) >= limit or array.null_count == len(array):
        return
    for value in _compute("unique", array).drop_null().to_pylist():
        if len(target) >= limit:
            break
        target.add(value)


def _count_nonpositive(array: Any) -> int:
    if array.null_count == len(array):
        return 0
    result = _compute("sum", _compute("cast", _compute("less_equal", array, 0), "int64"))
    return int(_scalar(result) or 0)


def _timestamp_order(
    batch: Any,
    last_timestamp: dict[Any, Any],
    *,
    ticker_column: str,
    timestamp_column: str,
) -> int:
    names = batch.schema.names
    if ticker_column not in names or timestamp_column not in names:
        return 0
    tickers = batch.column(ticker_column).to_numpy(zero_copy_only=False)
    timestamps = batch.column(timestamp_column).to_numpy(zero_copy_only=False)
    valid = np.array(
        [
            ticker is not None and timestamp is not None
            for ticker, timestamp in zip(tickers, timestamps, strict=True)
        ],
        dtype=bool,
    )
    if not valid.any():
        return 0
    backwards = 0
    for ticker in np.unique(tickers[valid]):
        positions = np.flatnonzero(valid & (tickers == ticker))
        values = timestamps[positions]
        backwards += int(np.count_nonzero(values[1:] < values[:-1]))
        previous = last_timestamp.get(ticker)
        if previous is not None and values[0] < previous:
            backwards += 1
        last_timestamp[ticker] = values[-1]
    return backwards


def _track_ids(
    array: Any,
    tracked_ids: set[Any],
    max_tracked_ids: int,
    *,
    scope_array: Any | None = None,
) -> tuple[int, bool]:
    if array.null_count == len(array):
        return 0, False
    identifiers = array.to_pylist()
    scopes = scope_array.to_pylist() if scope_array is not None else [None] * len(identifiers)
    keys = [
        (_id_key(scope), _id_key(identifier)) if scope_array is not None else _id_key(identifier)
        for identifier, scope in zip(identifiers, scopes, strict=True)
        if identifier is not None and (scope_array is None or scope is not None)
    ]
    counts = Counter(keys)
    unique_values = set(counts)
    duplicate_rows = sum(count - 1 for count in counts.values())
    duplicate_rows += sum(int(value in tracked_ids) for value in unique_values)
    if len(tracked_ids) >= max_tracked_ids:
        return duplicate_rows, True
    available = max_tracked_ids - len(tracked_ids)
    if len(unique_values) <= available:
        tracked_ids.update(unique_values)
        return duplicate_rows, False
    tracked_ids.update(list(unique_values)[:available])
    return duplicate_rows, True


def _id_key(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _collect_ids(
    path: Path,
    column: str,
    *,
    batch_size: int,
    max_ids: int,
) -> tuple[set[str], int, bool]:
    parquet = pq.ParquetFile(path)
    if column not in parquet.schema.names:
        return set(), 0, False
    identifiers: set[str] = set()
    rows = 0
    truncated = False
    for batch in parquet.iter_batches(columns=[column], batch_size=batch_size):
        rows += batch.num_rows
        for value in batch.column(column).drop_null().to_pylist():
            identifier = _id_key(value)
            if identifier is None:
                continue
            if identifier in identifiers:
                continue
            if len(identifiers) >= max_ids:
                truncated = True
                continue
            identifiers.add(identifier)
    return identifiers, rows, truncated


def profile_parquet(  # noqa: C901, PLR0912, PLR0915
    path: str | Path,
    *,
    batch_size: int = 262_144,
    max_tracked_ids: int = 1_000_000,
) -> dict[str, Any]:
    """Profile one Parquet file without modifying or rewriting the input."""
    parquet_path = Path(path).expanduser().resolve()
    parquet = pq.ParquetFile(parquet_path)
    columns = parquet.schema.names
    id_column = next((name for name in _ID_COLUMNS if name in columns), None)
    is_trade_file = parquet_path.name.startswith(("trades_", "deal_"))
    is_snapshot_file = parquet_path.name.startswith("snapshot_")
    expected_id_column = None if is_snapshot_file else "DealID" if is_trade_file else "OrderID"
    resolved_columns = {
        "ticker": "ticker" if "ticker" in columns else "SecuCode",
        "time_ms": (
            "time_ms"
            if "time_ms" in columns
            else "OrderTime"
            if "OrderTime" in columns
            else "DealTime"
            if "DealTime" in columns
            else "TickTime"
        ),
    }
    required_columns = list(_BASE_REQUIRED_COLUMNS)
    if expected_id_column is not None:
        required_columns.append(expected_id_column)
    missing_columns = []
    for name in required_columns:
        resolved_name = resolved_columns.get(name, name)
        if resolved_name not in columns:
            missing_columns.append(name)
    ordering = detect_ordering_columns(columns)
    sequence_state = SequenceQualityState(max_tracked_sequences=max_tracked_ids)
    tracked_ids: set[Any] = set()
    tracked_tickers: set[Any] = set()
    tracked_days: set[Any] = set()
    observed_types: set[Any] = set()
    last_timestamp: dict[Any, Any] = {}
    nulls = defaultdict(int)
    nonpositive = defaultdict(int)
    minimum: dict[str, Any] = {}
    maximum: dict[str, Any] = {}
    rows = 0
    duplicate_id_rows = 0
    timestamp_backwards = 0
    id_tracking_truncated = False
    expected_day = _expected_day(parquet_path)

    for batch in parquet.iter_batches(batch_size=batch_size):
        rows += batch.num_rows
        for name in columns:
            array = batch.column(name)
            nulls[name] += array.null_count
            if name in _NUMERIC_COLUMNS:
                _update_range(array, name, minimum, maximum)
        if resolved_columns["ticker"] in columns:
            _bounded_values(batch.column(resolved_columns["ticker"]), tracked_tickers, 100_000)
        if "TradingDay" in columns:
            _bounded_values(batch.column("TradingDay"), tracked_days, 100_000)
        if "OrderType" in columns:
            _bounded_values(batch.column("OrderType"), observed_types, 100)
        for name in ("Price", "Volume"):
            if name in columns:
                nonpositive[name] += _count_nonpositive(batch.column(name))
        if expected_day is not None and "TradingDay" in columns:
            mismatch = _compute("not_equal", batch.column("TradingDay"), expected_day)
            nulls["TradingDay_mismatch"] += int(
                _scalar(
                    _compute(
                        "sum", _compute("cast", _compute("fill_null", mismatch, False), "int64")
                    )
                )
                or 0
            )
        timestamp_backwards += _timestamp_order(
            batch,
            last_timestamp,
            ticker_column=resolved_columns["ticker"],
            timestamp_column=resolved_columns["time_ms"],
        )
        sequence_column = ordering.get("sequence_column")
        if isinstance(sequence_column, str) and sequence_column in columns:
            channel_column = ordering.get("channel_column")
            channels = (
                batch.column(channel_column).to_pylist()
                if isinstance(channel_column, str) and channel_column in columns
                else [None] * batch.num_rows
            )
            sequence_state.update(channels, batch.column(sequence_column).to_pylist())
        if id_column is not None:
            duplicate, truncated = _track_ids(
                batch.column(id_column),
                tracked_ids,
                max_tracked_ids,
                scope_array=(
                    batch.column(resolved_columns["ticker"])
                    if resolved_columns["ticker"] in columns
                    else None
                ),
            )
            duplicate_id_rows += duplicate
            id_tracking_truncated = id_tracking_truncated or truncated

    ordering_payload = dict(ordering)
    ordering_payload["sequence_quality"] = (
        sequence_state.to_payload() if ordering_payload["exchange_sequence_available"] else None
    )
    return {
        "path": str(parquet_path),
        "rows": rows,
        "columns": columns,
        "missing_columns": list(dict.fromkeys(missing_columns)),
        "resolved_columns": {
            key: value for key, value in resolved_columns.items() if value in columns
        },
        "id_column": id_column,
        "id_scope_columns": (
            [resolved_columns["ticker"], id_column]
            if id_column is not None and resolved_columns["ticker"] in columns
            else ([id_column] if id_column is not None else [])
        ),
        "distinct_ids_observed": len(tracked_ids),
        "duplicate_id_rows": duplicate_id_rows,
        "id_tracking_truncated": id_tracking_truncated,
        "distinct_tickers_observed": len(tracked_tickers),
        "trading_days": sorted(tracked_days),
        "expected_trading_day": expected_day,
        "trading_day_mismatch_rows": nulls.pop("TradingDay_mismatch", 0),
        "order_type_values_observed": sorted(observed_types),
        "nulls": dict(nulls),
        "nonpositive": dict(nonpositive),
        "minimum": minimum,
        "maximum": maximum,
        "timestamp_backwards": timestamp_backwards,
        "ordering": ordering_payload,
    }


def profile_l2_integrity(
    order_path: str | Path,
    trades_path: str | Path,
    *,
    batch_size: int = 262_144,
    max_order_ids: int = 2_000_000,
) -> dict[str, Any]:
    """Check whether trade order references are present in an order file."""
    order_file = Path(order_path).expanduser().resolve()
    trades_file = Path(trades_path).expanduser().resolve()
    order_ids, order_rows, order_truncated = _collect_ids(
        order_file,
        "OrderID",
        batch_size=batch_size,
        max_ids=max_order_ids,
    )
    trades = pq.ParquetFile(trades_file)
    missing_columns = [name for name in ("BuyID", "SellID") if name not in trades.schema.names]
    trade_rows = 0
    unknown = {"BuyID": 0, "SellID": 0}
    if not missing_columns:
        for batch in trades.iter_batches(columns=["BuyID", "SellID"], batch_size=batch_size):
            trade_rows += batch.num_rows
            for name in ("BuyID", "SellID"):
                unknown[name] += sum(
                    _id_key(value) not in order_ids
                    for value in batch.column(name).drop_null().to_pylist()
                )
    return {
        "order_path": str(order_file),
        "trades_path": str(trades_file),
        "order_rows": order_rows,
        "trade_rows": trade_rows,
        "order_id_count_observed": len(order_ids),
        "order_id_tracking_truncated": order_truncated,
        "missing_trade_columns": missing_columns,
        "unknown_buy_id_rows": unknown["BuyID"],
        "unknown_sell_id_rows": unknown["SellID"],
        "unknown_reference_counts_are_lower_bounds": order_truncated,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", action="append", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--batch-size", type=int, default=262_144)
    parser.add_argument("--max-tracked-ids", type=int, default=1_000_000)
    arguments = parser.parse_args(argv)
    payload = {
        "status": "complete",
        "reports": [
            profile_parquet(
                path,
                batch_size=arguments.batch_size,
                max_tracked_ids=arguments.max_tracked_ids,
            )
            for path in arguments.file
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    if arguments.output is None:
        print(text)
    else:
        arguments.output.expanduser().resolve().write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
