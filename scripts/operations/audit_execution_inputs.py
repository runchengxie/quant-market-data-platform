"""Prepare explicit, unpublished suspension/dividend research inputs from local assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

DATE_COLUMNS = ("ann_date", "imp_ann_date", "record_date", "ex_date", "pay_date", "div_listdate")
EVENT_KEYS = ("ts_code", "end_date", "ann_date", "div_proc", "ex_date")


def date_strings(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .replace({"": pd.NA, "None": pd.NA, "nan": pd.NA})
        .str.replace("-", "", regex=False)
        .str[:8]
    )


def date_range(series: pd.Series) -> dict:
    dates = date_strings(series).dropna()
    return {
        "min": str(dates.min()) if len(dates) else None,
        "max": str(dates.max()) if len(dates) else None,
    }


def dividend_audit(frame: pd.DataFrame) -> dict:
    """Audit provider stages without merging different announcements or amendments."""
    implemented = frame[frame.div_proc.astype("string").str.strip().eq("实施")]
    return {
        "rows": len(frame),
        "symbols": int(frame.ts_code.nunique()),
        "stages": {str(k): int(v) for k, v in frame.div_proc.value_counts().items()},
        "exact_duplicate_rows": int(frame.duplicated().sum()),
        "duplicate_event_keys": int(frame.duplicated(list(EVENT_KEYS), keep=False).sum()),
        "implemented_rows": len(implemented),
        "implemented_missing_dates": {
            column: int(date_strings(implemented[column]).isna().sum()) for column in DATE_COLUMNS
        },
        "implemented_positive_stock_missing_listing_date": int(
            (implemented.stk_div.gt(0) & date_strings(implemented.div_listdate).isna()).sum()
        ),
        "implemented_positive_cash_missing_pay_date": int(
            (implemented.cash_div_tax.gt(0) & date_strings(implemented.pay_date).isna()).sum()
        ),
        "date_ranges": {column: date_range(frame[column]) for column in DATE_COLUMNS},
        "pit_classification": "reconstructed_pit_not_revision_safe",
        "consumer_rule": (
            "Use implementation announcement for availability; apply only implemented events. "
            "Preserve conflicting event versions for review."
        ),
    }


def resolve_dividend_events(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Collapse equivalent implemented payouts and block missing or conflicting cash flows."""
    implemented = frame[frame.div_proc.astype("string").str.strip().eq("实施")].drop_duplicates()
    implemented = implemented.copy()
    material = [
        "end_date",
        "cash_div",
        "cash_div_tax",
        "stk_div",
        "stk_bo_rate",
        "stk_co_rate",
        "record_date",
        "pay_date",
        "div_listdate",
        "imp_ann_date",
    ]
    ready = []
    blocked = []
    collapsed = 0
    missing_ex = int(date_strings(implemented.ex_date).isna().sum())
    dated = implemented[date_strings(implemented.ex_date).notna()]
    for (symbol, ex_date), group in dated.groupby(["ts_code", "ex_date"], sort=True):
        conflict_fields = [
            column
            for column in material
            if column in group and group[column].astype("string").fillna("<missing>").nunique() > 1
        ]
        row = group.iloc[0].to_dict()
        missing = []
        for column in ("imp_ann_date", "record_date"):
            if pd.isna(date_strings(pd.Series([row[column]])).iloc[0]):
                missing.append(column)
        for amount, settlement in (("cash_div_tax", "pay_date"), ("stk_div", "div_listdate")):
            if pd.isna(row[amount]):
                missing.append(amount)
            elif row[amount] > 0 and pd.isna(date_strings(pd.Series([row[settlement]])).iloc[0]):
                missing.append(settlement)
        if conflict_fields or missing:
            blocked.append(
                {
                    "ts_code": symbol,
                    "ex_date": ex_date,
                    "conflict_fields": conflict_fields,
                    "missing_required_fields": missing,
                }
            )
            continue
        row["source_ann_dates"] = json.dumps(sorted(group.ann_date.dropna().astype(str).unique()))
        row["source_versions"] = len(group)
        row["available_from"] = row["imp_ann_date"]
        ready.append(row)
        collapsed += len(group) - 1
    columns = [*implemented.columns, "source_ann_dates", "source_versions", "available_from"]
    return pd.DataFrame(ready, columns=pd.Index(columns)), {
        "implemented_rows": len(implemented),
        "ready_events": len(ready),
        "equivalent_versions_collapsed": collapsed,
        "missing_ex_date_rows": missing_ex,
        "blocked_events": blocked,
        "scope": "cash and share distribution events only; no missing dates are inferred",
    }


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1048576), b""):
            result.update(block)
    return result.hexdigest()


def current_inventory(manifest: Path, windows: dict) -> dict:
    """Count date partitions; this is not a symbol-level completeness certificate."""
    contract = json.loads(manifest.read_text())
    inventory = {}
    for key in ("daily", "daily_basic", "adj_factor", "limit_status", "minute_1m_tushare"):
        asset = contract["assets"].get(key, {})
        path = Path(asset.get("resolved_path", "/nonexistent"))
        base = path / "data" if (path / "data").is_dir() else path
        dates = sorted(part.name.removeprefix("trade_date=") for part in base.glob("trade_date=*"))
        inventory[key] = {
            "path": str(path),
            "available": path.exists(),
            "partition_dates": len(dates),
            "first_date": dates[0] if dates else None,
            "last_date": dates[-1] if dates else None,
            "windows": {
                name: sum(window["start_date"] <= date <= window["end_date"] for date in dates)
                for name, window in windows.items()
            },
        }
    return {
        "contract_path": str(manifest),
        "contract_sha256": digest(manifest),
        "target_date": contract["contract"]["target_date"],
        "assets": inventory,
    }


def prepare(args: argparse.Namespace) -> dict:
    root = args.out_dir.resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("output directory must be empty; choose a new task-specific audit run")
    root.mkdir(parents=True, exist_ok=True)
    suspension = pd.concat([pd.read_parquet(path) for path in args.suspension], ignore_index=True)
    suspension = suspension.drop_duplicates().reset_index(drop=True)
    dividend = pd.read_parquet(args.dividend)
    requested = set(args.symbols.read_text().split()) if args.symbols else set()
    if requested:
        suspension = suspension[suspension.ts_code.isin(requested)]
        dividend = dividend[dividend.ts_code.isin(requested)]
    audit = dividend_audit(dividend)
    windows = {}
    for window in args.window:
        name, start, end = window.split(":")
        if not name.replace("_", "").isalnum() or len(start) != 8 or len(end) != 8:
            raise ValueError("window must be NAME:YYYYMMDD:YYYYMMDD")
        pd.to_datetime([start, end], format="%Y%m%d", errors="raise")
        if start > end:
            raise ValueError("window start exceeds end")
        local = root / name
        local.mkdir()
        susp = suspension[date_strings(suspension.trade_date).between(start, end)]
        # Keep all stages with any dated event in the window, including old fiscal periods.
        event_mask = pd.Series(False, index=dividend.index)
        for column in DATE_COLUMNS:
            event_mask |= date_strings(dividend[column]).between(start, end).fillna(False)
        div = dividend[event_mask]
        implemented = div[div.div_proc.astype("string").str.strip().eq("实施")].drop_duplicates()
        implemented = implemented.copy()
        implemented["event_key_conflict"] = implemented.duplicated(list(EVENT_KEYS), keep=False)
        events, event_verdict = resolve_dividend_events(
            div[date_strings(div.ex_date).between(start, end).fillna(False)]
        )
        paths = {}
        for key, frame in (
            ("suspend_d", susp),
            ("dividend_all_stages", div),
            ("dividend_implemented", implemented),
            ("dividend_events_ready", events),
        ):
            path = local / f"{key}.parquet"
            frame.to_parquet(path, index=False)
            paths[key] = {"path": str(path), "sha256": digest(path), "rows": len(frame)}
        windows[name] = {
            "start_date": start,
            "end_date": end,
            "assets": paths,
            "dividend_audit": dividend_audit(div),
            "event_resolution": event_verdict,
        }
    payload = {
        "schema_version": "execution-input-readiness.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "published": False,
        "source_assets": [
            {"path": str(path.resolve()), "sha256": digest(path)}
            for path in [*args.suspension, args.dividend]
        ],
        "requested_symbols": sorted(requested),
        "requested_symbols_without_dividend_rows": sorted(requested - set(dividend.ts_code)),
        "dividend_audit": audit,
        "windows": windows,
        "limitations": [
            (
                "Absent dividend rows do not prove absence of corporate actions; "
                "audit download state and selected symbols."
            ),
            "Event history retrieved today is reconstructed PIT, not revision-safe vintage data.",
            (
                "Suspension trade_date is effective date; "
                "no historical availability timestamp is supplied."
            ),
            "Minute bars contain no L2 queue, spread or order-book depth information.",
            "Rights issues and other corporate actions are outside the dividend endpoint boundary.",
        ],
        "field_units": {
            "cash_div": "currency per share, provider after-tax field",
            "cash_div_tax": "currency per share, provider before-tax field",
            "stk_div": "new shares per old share",
            "stk_bo_rate": "bonus shares per old share",
            "stk_co_rate": "capitalization shares per old share",
            "suspend_timing": "intraday time interval; null means no intraday interval supplied",
            "suspend_type": "S=suspended, R=resumed",
        },
        "sources": [
            "https://tushare.pro/document/2?doc_id=103",
            "https://tushare.pro/document/2?doc_id=214",
        ],
    }
    if getattr(args, "current_assets", None):
        payload["current_inventory"] = current_inventory(args.current_assets, windows)
    (root / "readiness.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suspension", type=Path, nargs="+", required=True)
    parser.add_argument("--dividend", type=Path, required=True)
    parser.add_argument("--symbols", type=Path)
    parser.add_argument(
        "--current-assets", type=Path, help="Optional current manifest for partition audit"
    )
    parser.add_argument("--window", action="append", required=True, help="NAME:YYYYMMDD:YYYYMMDD")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    payload = prepare(args)
    print(
        json.dumps(
            {"readiness": str(args.out_dir / "readiness.json"), "windows": list(payload["windows"])}
        )
    )


if __name__ == "__main__":
    main()
