#!/usr/bin/env python3
"""Compare an archived board flow day with TuShare's daily board flow.

Run it through the platform configuration so the TuShare token and custom API
URL are injected without touching the shell history:

    marketdata config run --config <config.json> -- \\
        python scripts/operations/board_flow_tushare_check.py \\
        --date 20261111 \\
        --database "$DATA_PLATFORM_ROOT/intraday/board_flow.sqlite3"

Exit code 0 means every archived board matched within tolerance.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any

from quant_market_data_platform.board_flow_validation import (
    CONTENT_TYPES,
    DEFAULT_TOLERANCE_YUAN,
    compare,
    latest_values,
)


def _reference_rows(frame: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        rows.append(
            {
                "code": str(record["ts_code"]).split(".")[0],
                "name": str(record["name"]),
                "net_amount": float(record["net_amount"]),
            }
        )
    return rows


def _provider(content_type: str, token_env: str) -> Any:
    import tushare as ts  # ty: ignore[import-not-found]

    token = os.environ.get(token_env) or os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError(f"Missing TuShare token in {token_env} or TUSHARE_TOKEN.")
    pro = ts.pro_api(token, timeout=30)
    url = os.environ.get("TUSHARE_API_URL_2") or os.environ.get("TUSHARE_API_URL")
    if url:
        vars(pro)["_DataApi__http_url"] = url
    return pro


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Trade date YYYYMMDD.")
    parser.add_argument("--database", required=True, help="board_flow.sqlite3 path.")
    parser.add_argument("--content-type", choices=sorted(CONTENT_TYPES), default="概念")
    parser.add_argument("--token-env", default="TUSHARE_TOKEN_2")
    parser.add_argument(
        "--tolerance-yi",
        type=float,
        default=DEFAULT_TOLERANCE_YUAN / 1e8,
        help="Absolute tolerance in 100 million yuan per board.",
    )
    args = parser.parse_args(argv)

    board_type = CONTENT_TYPES[args.content_type]
    trade_date = f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}"
    pro = _provider(args.content_type, args.token_env)
    frame = pro.moneyflow_ind_dc(trade_date=args.date, content_type=args.content_type)

    connection = sqlite3.connect(args.database)
    ours = latest_values(connection, trade_date=trade_date, board_type=board_type)
    result = compare(_reference_rows(frame), ours, tolerance_yuan=args.tolerance_yi * 1e8)
    print(json.dumps({"trade_date": trade_date, **result}, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
