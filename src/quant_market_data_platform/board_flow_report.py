"""Export archived board fund flow as CSV and a self-contained HTML race chart.

The HTML report mirrors the common "sector fund-flow race" view: one line per
board, x-axis is the trading minute, y-axis is the cumulative main-force net
inflow in 100 million yuan.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"
DELTA_WINDOWS = (5, 15, 30)


class ReportError(RuntimeError):
    """Raised when there is nothing to render."""


@dataclass(frozen=True)
class RaceReportOptions:
    """Selection and layout of one race chart."""

    trade_date: date
    board_type: str = "concept"
    top_n: int = 20
    names: tuple[str, ...] = ()
    title: str = ""


def _series_rows(connection: Any, *, trade_date: str, board_type: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT f.board_code, c.board_name, f.trade_time, f.main_net_yuan
        FROM flow_minute AS f
        LEFT JOIN board_catalog AS c ON c.board_code = f.board_code
        WHERE f.trade_date = ? AND f.board_type = ?
        ORDER BY f.board_code, f.trade_time
        """,
        (trade_date, board_type),
    )
    return [dict(row) for row in rows]


def _group(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row["board_code"])
        entry = grouped.setdefault(
            code,
            {"name": str(row["board_name"] or code), "points": []},
        )
        entry["points"].append((str(row["trade_time"]), float(row["main_net_yuan"])))
    return grouped


def _pick(
    grouped: dict[str, dict[str, Any]], options: RaceReportOptions
) -> list[tuple[str, dict[str, Any]]]:
    items = list(grouped.items())
    if options.names:
        wanted = set(options.names)
        items = [
            (code, entry) for code, entry in items if code in wanted or entry["name"] in wanted
        ]
        if not items:
            raise ReportError("None of the requested board names were archived.")
    items.sort(key=lambda item: item[1]["points"][-1][1], reverse=True)
    return items[: max(1, options.top_n)]


def _delta(points: list[tuple[str, float]], *, minutes: int) -> float | None:
    if len(points) < 2:
        return None
    target = _parse_point_time(points[-1][0]).timestamp() - minutes * 60
    for stamp, value in reversed(points):
        if _parse_point_time(stamp).timestamp() <= target:
            return points[-1][1] - value
    return None


def _parse_point_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _hhmm(stamp: str) -> str:
    """Return HH:MM from a stored ``YYYY-MM-DD HH:MM:SS`` timestamp."""
    return stamp[11:16]


def _table_rows(selected: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code, entry in selected:
        points = entry["points"]
        row: dict[str, Any] = {
            "code": code,
            "name": entry["name"],
            "latest_yi": round(points[-1][1] / 1e8, 2),
            "latest_time": _hhmm(points[-1][0]),
        }
        for window in DELTA_WINDOWS:
            value = _delta(points, minutes=window)
            row[f"d{window}_yi"] = None if value is None else round(value / 1e8, 2)
        rows.append(row)
    return rows


def _build_option(selected: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    times = sorted({_hhmm(stamp) for _, entry in selected for stamp, _ in entry["points"]})
    series = []
    for code, entry in selected:
        mapping = {_hhmm(stamp): round(value / 1e8, 4) for stamp, value in entry["points"]}
        series.append(
            {
                "name": entry["name"],
                "type": "line",
                "showSymbol": False,
                "smooth": False,
                "emphasis": {"focus": "series"},
                "data": [mapping.get(stamp) for stamp in times],
                "code": code,
            }
        )
    return {
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "legend": {"type": "scroll", "top": 8},
        "grid": {"left": 70, "right": 40, "top": 70, "bottom": 60},
        "xAxis": {
            "type": "category",
            "boundaryGap": False,
            "data": times,
            "axisLabel": {"interval": 29},
        },
        "yAxis": {
            "type": "value",
            "name": "累计主力净流入（亿元）",
            "nameLocation": "end",
            "scale": True,
        },
        "dataZoom": [{"type": "inside"}, {"type": "slider", "height": 22}],
        "series": series,
    }


def _html(title: str, option: dict[str, Any], table: list[dict[str, Any]]) -> str:
    payload = json.dumps(option, ensure_ascii=False)
    head = "".join(
        f"<th>{label}</th>"
        for label in (
            "板块",
            "代码",
            f"最新（{table[0]['latest_time'] if table else ''}）",
            *[f"{window}分钟增量" for window in DELTA_WINDOWS],
        )
    )
    body_rows = "".join(
        "<tr>"
        f"<td>{row['name']}</td>"
        f"<td>{row['code']}</td>"
        f"<td>{row['latest_yi']}</td>"
        + "".join(
            f"<td>{'' if row[f'd{window}_yi'] is None else row[f'd{window}_yi']}</td>"
            for window in DELTA_WINDOWS
        )
        + "</tr>"
        for row in table
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif; margin: 24px; }}
  h1 {{ font-size: 20px; }}
  #chart {{ width: 100%; height: 640px; }}
  table {{ border-collapse: collapse; margin-top: 24px; font-size: 13px; }}
  th, td {{ border: 1px solid #ddd; padding: 4px 10px; text-align: right; }}
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
  .note {{ color: #666; font-size: 12px; margin-top: 12px; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div id="chart"></div>
<table>
<thead><tr>{head}</tr></thead>
<tbody>{body_rows}</tbody>
</table>
<p class="note">单位：亿元。数据来源 eastmoney.board_flow.kline_1m，累计主力净流入 =
大单净流入 + 超大单净流入。板块成分股高度重叠，各板块净流入不可相加。</p>
<script src="{ECHARTS_CDN}"></script>
<script>
const option = {payload};
const chart = echarts.init(document.getElementById('chart'));
chart.setOption(option);
window.addEventListener('resize', () => chart.resize());
</script>
</body>
</html>
"""


def render_race_report(connection: Any, options: RaceReportOptions) -> str:
    """Render the race chart HTML for one archived trading day."""
    wanted = options.trade_date.isoformat()
    rows = _series_rows(connection, trade_date=wanted, board_type=options.board_type)
    if not rows:
        raise ReportError(f"No archived minute rows for {wanted} ({options.board_type}).")
    selected = _pick(_group(rows), options)
    title = options.title or (f"{wanted} {options.board_type} 板块资金流（前 {len(selected)} 名）")
    return _html(title, _build_option(selected), _table_rows(selected))


def write_csv(
    connection: Any,
    options: RaceReportOptions,
    destination: str | Path,
) -> dict[str, Any]:
    """Write the selected boards' minute series as CSV."""
    wanted = options.trade_date.isoformat()
    rows = _series_rows(connection, trade_date=wanted, board_type=options.board_type)
    if not rows:
        raise ReportError(f"No archived minute rows for {wanted} ({options.board_type}).")
    selected = _pick(_group(rows), options)
    target = Path(destination).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["trade_date", "board_code", "board_name", "board_type", "trade_time", "main_net_yuan"]
        )
        for code, entry in selected:
            for stamp, value in entry["points"]:
                writer.writerow([wanted, code, entry["name"], options.board_type, stamp, value])
                written += 1
    return {"path": str(target), "rows": written, "boards": len(selected)}
