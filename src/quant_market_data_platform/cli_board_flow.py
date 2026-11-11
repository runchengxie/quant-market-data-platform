"""CLI for intraday A-share board fund-flow capture, archive and reporting."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from quant_market_data_platform import board_flow_monitor, board_flow_store
from quant_market_data_platform.artifacts import resolve_artifacts_root
from quant_market_data_platform.board_flow_report import (
    METRICS,
    RaceReportOptions,
    ReportError,
    render_race_report,
    write_csv,
)
from quant_market_data_platform.providers.eastmoney_board_flow import (
    BOARD_TYPES,
    BoardFlowClient,
    BoardFlowError,
)

DEFAULT_DB_NAME = "board_flow.sqlite3"
DEFAULT_REPORT_DIR = "reports/board-flow"


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _default_db_path() -> Path:
    return resolve_artifacts_root() / "intraday" / DEFAULT_DB_NAME


def _db_path(args: argparse.Namespace) -> Path:
    configured = getattr(args, "db", None)
    return Path(configured).expanduser() if configured else _default_db_path()


def _board_types(args: argparse.Namespace) -> tuple[str, ...]:
    raw = getattr(args, "board_types", None)
    if not raw:
        return BOARD_TYPES
    values = tuple(item.strip() for item in str(raw).split(",") if item.strip())
    unknown = [value for value in values if value not in BOARD_TYPES]
    if unknown:
        raise ValueError(f"Unsupported board types: {', '.join(unknown)}.")
    if not values:
        raise ValueError("At least one board type is required.")
    return values


def _open_store(args: argparse.Namespace):
    path = _db_path(args)
    return path, board_flow_store.connect(path)


def _report_options(args: argparse.Namespace) -> RaceReportOptions:
    names_raw = getattr(args, "name", None) or []
    names: list[str] = []
    for item in names_raw:
        names.extend(part.strip() for part in str(item).split(",") if part.strip())
    trade_date = date.fromisoformat(str(args.date))
    return RaceReportOptions(
        trade_date=trade_date,
        board_type=str(args.board_type),
        top_n=int(args.top),
        names=tuple(names),
        metric=str(getattr(args, "metric", "cumulative")),
        window_minutes=int(getattr(args, "window", 30)),
    )


def _handle_catalog(args: argparse.Namespace) -> int:
    path, connection = _open_store(args)
    client = BoardFlowClient()
    moment = board_flow_monitor.shanghai_now()
    captured_at = moment.isoformat(timespec="seconds")
    totals: dict[str, int] = {}
    trade_date = moment.astimezone(board_flow_monitor.SHANGHAI).date().isoformat()
    for board_type in _board_types(args):
        rows = client.fetch_boards(board_type)
        board_flow_store.upsert_catalog(connection, rows, seen_at=captured_at)
        board_flow_store.snapshot_catalog(
            connection, rows, trade_date=trade_date, captured_at=captured_at
        )
        totals[board_type] = len(rows)
    connection.commit()
    _print_json({"status": "ok", "db": str(path), "boards_by_type": totals})
    return 0


def _handle_snapshot(args: argparse.Namespace) -> int:
    path, connection = _open_store(args)
    options = board_flow_monitor.SnapshotOptions(board_types=_board_types(args))
    result = board_flow_monitor.take_snapshot(BoardFlowClient(), connection, options)
    _print_json({"status": "ok", "db": str(path), **result})
    return 0


def _handle_monitor(args: argparse.Namespace) -> int:
    path, connection = _open_store(args)
    options = board_flow_monitor.MonitorOptions(
        board_types=_board_types(args),
        interval_seconds=float(args.interval),
        wait_for_open=not bool(args.no_wait),
        max_minutes=None if args.max_minutes is None else float(args.max_minutes),
    )

    def progress(index: int, total: int) -> None:
        print(f"[monitor] snapshot {index}/{total}", file=sys.stderr)

    result = board_flow_monitor.run_monitor(
        BoardFlowClient(),
        connection,
        options,
        runtime=board_flow_monitor.Runtime(progress=progress),
    )
    _print_json({"status": "ok", "db": str(path), **result})
    return 0


def _handle_archive(args: argparse.Namespace) -> int:
    path, connection = _open_store(args)
    trade_date = date.fromisoformat(str(args.trade_date)) if args.trade_date else None
    options = board_flow_monitor.ArchiveOptions(
        board_types=_board_types(args),
        trade_date=trade_date,
        delay_seconds=float(args.delay),
        skip_archived=not bool(args.no_skip),
        limit=None if args.limit is None else int(args.limit),
    )

    def progress(index: int, total: int) -> None:
        print(f"[archive] fetched {index}/{total} boards", file=sys.stderr)

    result = board_flow_monitor.archive_minutes(
        BoardFlowClient(),
        connection,
        options,
        runtime=board_flow_monitor.Runtime(progress=progress),
    )
    _print_json({"status": "ok", "db": str(path), **result})
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    path, connection = _open_store(args)
    _print_json({"status": "ok", "db": str(path), **board_flow_store.summary(connection)})
    return 0


def _handle_report(args: argparse.Namespace) -> int:
    path, connection = _open_store(args)
    options = _report_options(args)
    html = render_race_report(connection, options)
    destination = (
        Path(args.out).expanduser()
        if args.out
        else path.parent
        / DEFAULT_REPORT_DIR
        / f"{options.trade_date}-{options.board_type}-race.html"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    _print_json({"status": "ok", "db": str(path), "report": str(destination)})
    return 0


def _handle_export(args: argparse.Namespace) -> int:
    path, connection = _open_store(args)
    options = _report_options(args)
    destination = (
        Path(args.out).expanduser()
        if args.out
        else path.parent / DEFAULT_REPORT_DIR / f"{options.trade_date}-{options.board_type}.csv"
    )
    _print_json({"status": "ok", "db": str(path), **write_csv(connection, options, destination)})
    return 0


def handle_board_flow(args: argparse.Namespace) -> int:
    handlers = {
        "catalog": _handle_catalog,
        "snapshot": _handle_snapshot,
        "monitor": _handle_monitor,
        "archive-minutes": _handle_archive,
        "status": _handle_status,
        "report": _handle_report,
        "export": _handle_export,
    }
    handler = handlers[args.board_flow_command]
    try:
        return handler(args)
    except (BoardFlowError, ReportError, ValueError, OSError) as error:
        _print_json({"status": "failed", "error": str(error)})
        return 1


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        help="SQLite path; defaults to <DATA_PLATFORM_ROOT>/intraday/board_flow.sqlite3.",
    )


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--board-types",
        default=",".join(BOARD_TYPES),
        help=f"Comma-separated subset of {', '.join(BOARD_TYPES)}.",
    )


def add_board_flow_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "board-flow",
        help="Capture and archive A-share sector fund flow from Eastmoney.",
    )
    group = parser.add_subparsers(dest="board_flow_command", required=True)

    catalog = group.add_parser("catalog", help="Refresh the board code/name catalog.")
    _add_common(catalog)
    _add_selection(catalog)
    catalog.set_defaults(handler=handle_board_flow)

    snapshot = group.add_parser("snapshot", help="Capture one flow snapshot for all boards.")
    _add_common(snapshot)
    _add_selection(snapshot)
    snapshot.set_defaults(handler=handle_board_flow)

    monitor = group.add_parser("monitor", help="Capture snapshots until the session closes.")
    _add_common(monitor)
    _add_selection(monitor)
    monitor.add_argument("--interval", type=float, default=30.0, help="Seconds between snapshots.")
    monitor.add_argument("--max-minutes", type=float, default=None, help="Optional stop guard.")
    monitor.add_argument("--no-wait", action="store_true", help="Exit outside trading hours.")
    monitor.set_defaults(handler=handle_board_flow)

    archive = group.add_parser(
        "archive-minutes", help="Archive each board's minute curve for one trading day."
    )
    _add_common(archive)
    _add_selection(archive)
    archive.add_argument("--trade-date", help="YYYY-MM-DD; defaults to today in Shanghai.")
    archive.add_argument("--delay", type=float, default=0.2, help="Seconds between requests.")
    archive.add_argument("--no-skip", action="store_true", help="Refetch already archived boards.")
    archive.add_argument("--limit", type=int, default=None, help="Archive at most N boards.")
    archive.set_defaults(handler=handle_board_flow)

    status = group.add_parser("status", help="Show stored row counts and recent days.")
    _add_common(status)
    status.set_defaults(handler=handle_board_flow)

    report = group.add_parser("report", help="Render an HTML fund-flow race chart.")
    _add_common(report)
    report.add_argument("--date", required=True, help="Trading day YYYY-MM-DD.")
    report.add_argument("--board-type", choices=BOARD_TYPES, default="concept")
    report.add_argument("--top", type=int, default=20)
    report.add_argument("--name", action="append", help="Board name or code; repeatable.")
    report.add_argument("--metric", choices=METRICS, default="cumulative")
    report.add_argument("--window", type=int, default=30, help="Delta bucket size in minutes.")
    report.add_argument("--out", help="Output HTML path.")
    report.set_defaults(handler=handle_board_flow)

    export = group.add_parser("export", help="Export selected boards' minutes as CSV.")
    _add_common(export)
    export.add_argument("--date", required=True, help="Trading day YYYY-MM-DD.")
    export.add_argument("--board-type", choices=BOARD_TYPES, default="concept")
    export.add_argument("--top", type=int, default=20)
    export.add_argument("--name", action="append", help="Board name or code; repeatable.")
    export.add_argument("--out", help="Output CSV path.")
    export.set_defaults(handler=handle_board_flow)
