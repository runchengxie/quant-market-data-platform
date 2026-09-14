# ruff: noqa: PLR0913
"""Restartable ETF raw backfill, reconciliation, and forward-adjusted daily bars."""

from __future__ import annotations

import calendar
import json
import re
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from market_data_platform.providers.tushare_a_share import (
    mirror_etf_adj_factor,
    mirror_etf_daily,
)
from market_data_platform.providers.tushare_common import TushareRequestPolicy

_BANG_ONE = re.compile(r"^(\d{6})!1\.(SH|SZ)$")


def etf_join_code(value: object) -> str:
    """Return a restricted reconciliation key without altering the raw code."""
    text = str(value or "").strip().upper()
    match = _BANG_ONE.fullmatch(text)
    return f"{match.group(1)}1.{match.group(2)}" if match else text


def _parts(root: str | Path) -> dict[str, Path]:
    path = Path(root).expanduser().resolve() / "data"
    return {
        item.parent.name.split("=", 1)[1]: item
        for item in path.glob("trade_date=*/part.parquet")
        if item.parent.name.startswith("trade_date=")
    }


def _read(path: Path | None) -> pd.DataFrame:
    return pd.read_parquet(path) if path is not None else pd.DataFrame()


def validate_etf_daily_pair(*, daily_dir: str | Path, adj_factor_dir: str | Path) -> dict[str, Any]:
    """Validate daily/adjustment keys by date and expose only auditable repairs."""
    daily_parts, adj_parts = _parts(daily_dir), _parts(adj_factor_dir)
    report: dict[str, Any] = {
        "schema_version": "tushare.etf_daily_pair_validation.v1",
        "daily_dir": str(Path(daily_dir).resolve()),
        "adj_factor_dir": str(Path(adj_factor_dir).resolve()),
        "daily_dates": len(daily_parts),
        "adj_dates": len(adj_parts),
        "missing_adj_partitions": sorted(set(daily_parts) - set(adj_parts)),
        "missing_daily_partitions": sorted(set(adj_parts) - set(daily_parts)),
        "daily_rows": 0,
        "adj_rows": 0,
        "daily_duplicate_keys": 0,
        "adj_duplicate_keys": 0,
        "daily_without_adj": 0,
        "adj_without_daily": 0,
        "reconciled_bang_one_rows": 0,
        "samples": [],
    }
    for trade_date, daily_path in sorted(daily_parts.items()):
        daily, adj = _read(daily_path), _read(adj_parts.get(trade_date))
        if "ts_code" not in daily or "ts_code" not in adj:
            raise ValueError(f"ETF partition {trade_date} is missing ts_code.")
        daily["_join_code"] = daily.ts_code.map(etf_join_code)
        adj["_join_code"] = adj.ts_code.map(etf_join_code)
        report["daily_rows"] += len(daily)
        report["adj_rows"] += len(adj)
        report["daily_duplicate_keys"] += int(daily.duplicated(["_join_code"]).sum())
        report["adj_duplicate_keys"] += int(adj.duplicated(["_join_code"]).sum())
        daily_codes, adj_codes = set(daily._join_code), set(adj._join_code)
        report["daily_without_adj"] += len(daily_codes - adj_codes)
        report["adj_without_daily"] += len(adj_codes - daily_codes)
        repaired = adj.ts_code.astype(str).str.contains("!1", regex=False).sum()
        report["reconciled_bang_one_rows"] += int(repaired)
        if len(report["samples"]) < 20 and (daily_codes - adj_codes or adj_codes - daily_codes):
            report["samples"].append(
                {
                    "trade_date": trade_date,
                    "daily_without_adj": sorted(daily_codes - adj_codes)[:5],
                    "adj_without_daily": sorted(adj_codes - daily_codes)[:5],
                }
            )
    report["status"] = (
        "passed"
        if not report["missing_adj_partitions"]
        and not report["daily_duplicate_keys"]
        and not report["adj_duplicate_keys"]
        else "failed"
    )
    return report


def build_etf_daily_forward_adjusted(
    *, daily_dir: str | Path, adj_factor_dir: str | Path, out_dir: str | Path
) -> dict[str, Any]:
    """Write ETF daily bars with prices forward-adjusted to each symbol's latest factor."""
    validation = validate_etf_daily_pair(daily_dir=daily_dir, adj_factor_dir=adj_factor_dir)
    if validation["status"] != "passed":
        raise ValueError(
            "ETF daily/adj-factor validation failed; inspect missing partitions or keys."
        )
    daily_parts, adj_parts = _parts(daily_dir), _parts(adj_factor_dir)
    latest: dict[str, float] = {}
    for trade_date in sorted(adj_parts):
        frame = _read(adj_parts[trade_date])
        frame["_join_code"] = frame.ts_code.map(etf_join_code)
        for join_code, factor in (
            frame[["_join_code", "adj_factor"]].dropna().itertuples(index=False, name=None)
        ):
            latest[str(join_code)] = float(factor)
    root = Path(out_dir).expanduser().resolve()
    data_root = root / "data"
    rows = 0
    symbols: set[str] = set()
    missing_factor = 0
    for trade_date, daily_path in sorted(daily_parts.items()):
        daily, adj = _read(daily_path), _read(adj_parts[trade_date])
        daily["_join_code"] = daily.ts_code.map(etf_join_code)
        adj["_join_code"] = adj.ts_code.map(etf_join_code)
        adj = adj[["_join_code", "adj_factor"]].drop_duplicates("_join_code")
        out = daily.merge(adj, on="_join_code", how="left")
        denominator = out._join_code.map(latest)
        ratio = pd.to_numeric(out.adj_factor, errors="coerce") / pd.to_numeric(
            denominator, errors="coerce"
        )
        missing_factor += int(ratio.isna().sum())
        for column in ("open", "high", "low", "close", "pre_close"):
            if column in out:
                out[f"adj_{column}"] = pd.to_numeric(out[column], errors="coerce") * ratio
        out["adjustment_method"] = "forward_to_latest_factor"
        out["adjustment_join_repaired"] = out.ts_code != out._join_code
        out = out.drop(columns=["_join_code"])
        path = data_root / f"trade_date={trade_date}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(path, index=False)
        rows += len(out)
        symbols.update(out.ts_code.astype(str))
    manifest = {
        "schema_version": "tushare.etf_daily_forward_adjusted.v1",
        "dataset": "etf_daily_forward_adjusted",
        "provider": "tushare",
        "market": "etf",
        "status": "completed",
        "output_dir": str(root),
        "inputs": {
            "daily_dir": str(Path(daily_dir).resolve()),
            "adj_factor_dir": str(Path(adj_factor_dir).resolve()),
        },
        "adjustment_method": "price * adj_factor / latest_adj_factor_by_reconciled_code",
        "validation": validation,
        "totals": {
            "rows": rows,
            "symbols": len(symbols),
            "trade_dates": len(daily_parts),
            "rows_missing_factor": missing_factor,
        },
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _periods(start_date: str, end_date: str) -> Iterable[tuple[str, str]]:
    current = date.fromisoformat(f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}")
    end = date.fromisoformat(f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}")
    while current <= end:
        last = calendar.monthrange(current.year, current.month)[1]
        finish = min(end, date(current.year, current.month, last))
        yield current.strftime("%Y%m%d"), finish.strftime("%Y%m%d")
        current = finish + timedelta(days=1)


def backfill_etf_history(
    *,
    artifacts_root: str | Path,
    start_date: str,
    end_date: str,
    token_env: str,
    api_url: str | None,
    request_policy: TushareRequestPolicy,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Backfill monthly into new immutable ETF snapshot directories and resume by partition."""
    root = Path(artifacts_root).expanduser().resolve()
    tag = f"{start_date}_{end_date}"
    targets = {
        "daily": root / "assets/tushare/etf/daily" / f"etf_all_{tag}_fund_daily",
        "adj_factor": root / "assets/tushare/etf/adj_factor" / f"etf_all_{tag}_fund_adj",
    }
    plan = {
        "schema_version": "tushare.etf_history_backfill.v1",
        "status": "planned" if dry_run else "completed",
        "start_date": start_date,
        "end_date": end_date,
        "segment": "month",
        "targets": {key: str(value) for key, value in targets.items()},
        "segments": list(_periods(start_date, end_date)),
    }
    if dry_run:
        return plan
    for dataset, target in targets.items():
        mirror = mirror_etf_daily if dataset == "daily" else mirror_etf_adj_factor
        for start, end in plan["segments"]:
            mirror(
                out_dir=target,
                start_date=start,
                end_date=end,
                skip_existing=True,
                token_env=token_env,
                api_url=api_url,
                request_policy=request_policy,
                request_interval_seconds=0.15,
            )
    plan["validation"] = validate_etf_daily_pair(
        daily_dir=targets["daily"], adj_factor_dir=targets["adj_factor"]
    )
    (root / "metadata" / f"etf_history_backfill_{tag}.json").parent.mkdir(
        parents=True, exist_ok=True
    )
    (root / "metadata" / f"etf_history_backfill_{tag}.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return plan
