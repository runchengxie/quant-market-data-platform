"""Export archived board fund flow as CSV and a self-contained HTML report.

Three views are supported:

* ``cumulative`` – cumulative main-force net inflow per board (the classic race).
* ``delta`` – net inflow change inside fixed intraday buckets, as a heatmap. This
  is the view that shows money rotating between boards.
* ``rank`` – each board's cross-sectional rank over time, useful for spotting
  leadership changes.

All values are in 100 million yuan. The HTML is self-contained apart from the
ECharts script loaded from a CDN.
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
METRICS = ("cumulative", "delta", "rank")
STYLES = ("heatmap", "line")
SESSION_OPEN_MINUTE = 9 * 60 + 30
LUNCH_MINUTES = 90
SESSION_MINUTES = 240


class ReportError(RuntimeError):
    """Raised when there is nothing to render."""


@dataclass(frozen=True)
class RaceReportOptions:
    """Selection and layout of one flow report."""

    trade_date: date
    board_type: str = "concept"
    top_n: int = 20
    names: tuple[str, ...] = ()
    title: str = ""
    metric: str = "cumulative"
    window_minutes: int = 30
    style: str = "heatmap"

    def __post_init__(self) -> None:
        if self.metric not in METRICS:
            raise ValueError(f"Unsupported metric '{self.metric}'; choose from {METRICS}.")
        if self.style not in STYLES:
            raise ValueError(f"Unsupported style '{self.style}'; choose from {STYLES}.")
        if self.window_minutes <= 0 or SESSION_MINUTES % self.window_minutes != 0:
            raise ValueError("window_minutes must be a positive divisor of 240.")


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


def _session_minute(stamp: str) -> int:
    """Minutes since 09:30 on the continuous session clock (lunch removed)."""
    moment = _parse_point_time(stamp)
    minutes = moment.hour * 60 + moment.minute
    if minutes >= 13 * 60:
        minutes -= LUNCH_MINUTES
    return minutes - SESSION_OPEN_MINUTE


def _bucket_index(stamp: str, *, window_minutes: int) -> int:
    return max(0, _session_minute(stamp)) // window_minutes


def _bucket_labels(*, window_minutes: int) -> list[str]:
    labels: list[str] = []
    for index in range(SESSION_MINUTES // window_minutes):
        minute = SESSION_OPEN_MINUTE + (index + 1) * window_minutes
        if minute > 11 * 60 + 30:
            minute += LUNCH_MINUTES
        labels.append(f"{minute // 60:02d}:{minute % 60:02d}")
    return labels


def _bucket_deltas(points: list[tuple[str, float]], *, window_minutes: int) -> dict[int, float]:
    """Net inflow added inside each bucket, in yuan."""
    by_bucket: dict[int, float] = {}
    for stamp, value in points:
        by_bucket[_bucket_index(stamp, window_minutes=window_minutes)] = value
    deltas: dict[int, float] = {}
    previous = 0.0
    for index in range(SESSION_MINUTES // window_minutes):
        if index not in by_bucket:
            continue
        current = by_bucket[index]
        deltas[index] = current - previous
        previous = current
    return deltas


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


def _cumulative_option(selected: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
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


def _delta_option(
    selected: list[tuple[str, dict[str, Any]]], *, window_minutes: int
) -> dict[str, Any]:
    labels = _bucket_labels(window_minutes=window_minutes)
    names = [entry["name"] for _, entry in selected]
    data: list[list[Any]] = []
    for y, (_, entry) in enumerate(selected):
        deltas = _bucket_deltas(entry["points"], window_minutes=window_minutes)
        for x, value in deltas.items():
            if x < len(labels):
                data.append([x, y, round(value / 1e8, 3)])
    values = [item[2] for item in data] or [0.0]
    bound = max(abs(min(values)), abs(max(values)), 0.01)
    return {
        "tooltip": {"position": "top"},
        "grid": {"left": 160, "right": 40, "top": 30, "bottom": 90},
        "xAxis": {"type": "category", "data": labels, "splitArea": {"show": True}},
        "yAxis": {"type": "category", "data": names, "inverse": True, "splitArea": {"show": True}},
        "visualMap": {
            "min": -bound,
            "max": bound,
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "bottom": 10,
            "text": ["净流入", "净流出"],
            "inRange": {"color": ["#1a9850", "#f7f7f7", "#d73027"]},
        },
        "series": [
            {
                "name": "区间净流入",
                "type": "heatmap",
                "data": data,
                "label": {"show": False},
                "emphasis": {"itemStyle": {"shadowBlur": 10}},
            }
        ],
    }


def _delta_line_option(
    selected: list[tuple[str, dict[str, Any]]], *, window_minutes: int
) -> dict[str, Any]:
    labels = _bucket_labels(window_minutes=window_minutes)
    series = []
    for code, entry in selected:
        deltas = _bucket_deltas(entry["points"], window_minutes=window_minutes)
        data = [
            None if index not in deltas else round(deltas[index] / 1e8, 3)
            for index in range(len(labels))
        ]
        series.append(
            {
                "name": entry["name"],
                "type": "line",
                "showSymbol": True,
                "symbolSize": 5,
                "emphasis": {"focus": "series"},
                "data": data,
                "code": code,
                "markLine": {
                    "silent": True,
                    "symbol": "none",
                    "lineStyle": {"type": "dashed", "color": "#999"},
                    "data": [{"yAxis": 0}],
                }
                if not series
                else None,
            }
        )
    return {
        "tooltip": {"trigger": "axis"},
        "legend": {"type": "scroll", "top": 8},
        "grid": {"left": 70, "right": 40, "top": 70, "bottom": 60},
        "xAxis": {"type": "category", "boundaryGap": False, "data": labels},
        "yAxis": {
            "type": "value",
            "name": f"{window_minutes} 分钟区间净流入（亿元）",
            "nameLocation": "end",
            "scale": True,
        },
        "series": series,
    }


def _rank_option(selected: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    times = sorted({_hhmm(stamp) for _, entry in selected for stamp, _ in entry["points"]})
    values = {
        code: {_hhmm(stamp): value for stamp, value in entry["points"]} for code, entry in selected
    }
    rank: dict[str, dict[str, int]] = {code: {} for code in values}
    for stamp in times:
        present = [(code, values[code][stamp]) for code in values if stamp in values[code]]
        present.sort(key=lambda item: item[1], reverse=True)
        for position, (code, _) in enumerate(present, start=1):
            rank[code][stamp] = position
    series = [
        {
            "name": entry["name"],
            "type": "line",
            "showSymbol": False,
            "emphasis": {"focus": "series"},
            "data": [rank[code].get(stamp) for stamp in times],
            "code": code,
        }
        for code, entry in selected
    ]
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
            "name": "净流入排名（越上越强）",
            "inverse": True,
            "min": 1,
            "max": len(selected),
            "interval": 1 if len(selected) <= 10 else 2,
        },
        "dataZoom": [{"type": "inside"}, {"type": "slider", "height": 22}],
        "series": series,
    }


def _html(title: str, option: dict[str, Any], table: list[dict[str, Any]], note: str) -> str:
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
<p class="note">{note}</p>
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


NOTE_BASE = (
    "单位：亿元。数据来源 eastmoney.board_flow.kline_1m，累计主力净流入 = 大单净流入 + "
    "超大单净流入。板块成分股高度重叠，各板块净流入不可相加。"
)
NOTE_CUMULATIVE = NOTE_BASE + " 曲线按收盘累计值排序选取，天然偏向赢家，不代表盘中轮动。"
NOTE_DELTA = NOTE_BASE + " 每个格子是该区间内新增的主力净流入；红色为净流入，绿色为净流出。"
NOTE_DELTA_LINE = NOTE_BASE + " 每条线是一个区间内新增的主力净流入；线在 0 轴下方即为该区间净流出。"
NOTE_RANK = NOTE_BASE + " 排名按每分钟累计净流入在所选板块内计算，线条交叉即代表领先者切换。"


def render_race_report(connection: Any, options: RaceReportOptions) -> str:
    """Render one archived trading day as HTML."""
    wanted = options.trade_date.isoformat()
    rows = _series_rows(connection, trade_date=wanted, board_type=options.board_type)
    if not rows:
        raise ReportError(f"No archived minute rows for {wanted} ({options.board_type}).")
    selected = _pick(_group(rows), options)
    suffix = {
        "cumulative": "累计资金流",
        "delta": f"增量{'曲线' if options.style == 'line' else '热力图'}",
        "rank": "排名变化",
    }[options.metric]
    title = options.title or (
        f"{wanted} {options.board_type} 板块{suffix}（前 {len(selected)} 名）"
    )
    if options.metric == "delta":
        if options.style == "line":
            option = _delta_line_option(selected, window_minutes=options.window_minutes)
            note = NOTE_DELTA_LINE
        else:
            option = _delta_option(selected, window_minutes=options.window_minutes)
            note = NOTE_DELTA
    elif options.metric == "rank":
        option = _rank_option(selected)
        note = NOTE_RANK
    else:
        option = _cumulative_option(selected)
        note = NOTE_CUMULATIVE
    return _html(title, option, _table_rows(selected), note)


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
