"""Cross-check archived board fund flow against TuShare's daily board flow.

The archived minute curve at 15:00 should agree with TuShare
``moneyflow_ind_dc.net_amount`` for the same board and trade date. This module
keeps the comparison pure so it can be tested without a network call; the
operations script adds the provider fetch.
"""

from __future__ import annotations

import statistics
from typing import Any

DEFAULT_TOLERANCE_YUAN = 1_000_000.0
CONTENT_TYPES = {"概念": "concept", "行业": "industry", "地域": "region"}


def latest_values(
    connection: Any, *, trade_date: str, board_type: str
) -> dict[str, tuple[str, float]]:
    """Last archived cumulative value per board for one trading day."""
    rows = connection.execute(
        """
        SELECT f.board_code, c.board_name, f.main_net_yuan
        FROM flow_minute AS f
        LEFT JOIN board_catalog AS c ON c.board_code = f.board_code
        WHERE f.trade_date = ?
          AND f.board_type = ?
          AND f.trade_time = (
              SELECT MAX(trade_time) FROM flow_minute AS g
              WHERE g.trade_date = f.trade_date AND g.board_code = f.board_code
          )
        """,
        (trade_date, board_type),
    )
    return {str(row[0]): (str(row[1] or row[0]), float(row[2])) for row in rows}


def compare(
    reference_rows: list[dict[str, Any]],
    ours: dict[str, tuple[str, float]],
    *,
    tolerance_yuan: float = DEFAULT_TOLERANCE_YUAN,
) -> dict[str, Any]:
    """Compare provider rows with locally archived values by board code."""
    pairs: list[tuple[str, str, float, float]] = []
    missing: list[str] = []
    for row in reference_rows:
        code = str(row.get("code") or "")
        if not code:
            continue
        if code not in ours:
            missing.append(code)
            continue
        _, local = ours[code]
        pairs.append((code, str(row.get("name") or code), float(row["net_amount"]), local))
    if not pairs:
        return {
            "ok": False,
            "matched": 0,
            "missing_in_archive": missing,
            "error": "no matching boards between the provider and the archive",
        }
    diffs = [abs(reference - local) for _, _, reference, local in pairs]
    within = [diff for diff in diffs if diff <= tolerance_yuan]
    worst = sorted(pairs, key=lambda item: -abs(item[2] - item[3]))[:5]
    return {
        "ok": len(within) == len(pairs),
        "matched": len(pairs),
        "missing_in_archive": missing,
        "mean_abs_diff_yi": round(statistics.mean(diffs) / 1e8, 6),
        "max_abs_diff_yi": round(max(diffs) / 1e8, 6),
        "within_tolerance": len(within),
        "tolerance_yi": round(tolerance_yuan / 1e8, 6),
        "worst": [
            {
                "code": code,
                "name": name,
                "reference_yi": round(reference / 1e8, 4),
                "archived_yi": round(local / 1e8, 4),
            }
            for code, name, reference, local in worst
        ],
    }
