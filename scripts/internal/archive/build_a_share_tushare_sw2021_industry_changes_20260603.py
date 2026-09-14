#!/usr/bin/env python
from __future__ import annotations

# Archived one-off script. Do not use in production.
# Replaced by:
#   marketdata tushare download-a-share-industry-membership
# Kept here for historical audit reproducibility only.
import argparse
import json
import os
import time
from datetime import date, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

ts = import_module("tushare")

DEFAULT_DATA_ROOT = Path(
    os.environ.get("DATA_PLATFORM_ROOT", Path.cwd() / "data" / "market-data-platform")
)
DEFAULT_AS_OF_DATE = date.today().strftime("%Y%m%d")
OUT_DIR_SUFFIX = "assets/tushare/a_share/industry_changes"
SNAPSHOT_NAME_PREFIX = "a_share_all_industry_changes_sw2021_l3"
ALIAS_DIR_NAME = "a_share_all_industry_changes_latest"


def _default_data_root() -> Path:
    return DEFAULT_DATA_ROOT


def _default_snapshot_name(as_of_date: str) -> str:
    return f"{SNAPSHOT_NAME_PREFIX}_{as_of_date}"


def _validate_as_of_date(raw: str) -> str:
    try:
        datetime.strptime(raw, "%Y%m%d")
    except ValueError as error:
        raise SystemExit(f"Invalid --as-of-date {raw!r}; expected YYYYMMDD") from error
    return raw


def _resolve_paths(
    *,
    data_root: Path,
    as_of_date: str,
    snapshot_name: str | None,
) -> dict[str, Any]:
    normalized_root = data_root.expanduser().resolve()
    snapshot = snapshot_name or _default_snapshot_name(as_of_date)
    out_dir = normalized_root / OUT_DIR_SUFFIX / snapshot
    alias_dir = normalized_root / OUT_DIR_SUFFIX / ALIAS_DIR_NAME
    registry = normalized_root / "metadata" / "dataset_registry.csv"
    return {
        "out_dir": out_dir,
        "alias_dir": alias_dir,
        "registry": registry,
        "snapshot": snapshot,
    }


def _load_token() -> str:
    load_dotenv(Path.cwd() / ".env")
    token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TUSHARE_TOKEN_2")
    if not token:
        raise SystemExit("TUSHARE_TOKEN/TUSHARE_TOKEN_2 not found in .env or environment")
    return token


def _fetch_paginated(
    pro: Any,
    *,
    is_new: str,
    limit: int = 1000,
    sleep_seconds: float = 0.15,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    offset = 0
    while True:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                df = pro.index_member_all(is_new=is_new, limit=limit, offset=offset)
                break
            except Exception as exc:  # pragma: no cover - network/API retry guard
                last_error = exc
                time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(
                f"index_member_all(is_new={is_new}, offset={offset}) failed: {last_error}"
            )
        if df.empty:
            break
        frames.append(df)
        print(f"fetched is_new={is_new} offset={offset} rows={len(df)}")
        if len(df) < limit:
            break
        offset += limit
        time.sleep(sleep_seconds)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _to_datetime_yyyymmdd(series: pd.Series) -> pd.Series:
    return pd.to_datetime(
        series.astype("string").str.strip().replace({"": pd.NA, "None": pd.NA}),
        format="%Y%m%d",
        errors="coerce",
    )


def _column(df: pd.DataFrame, name: str) -> pd.Series:
    values = df[name]
    if isinstance(values, pd.DataFrame):
        return values.iloc[:, 0]
    return values


def _normalize_sw_members(raw: pd.DataFrame) -> pd.DataFrame:
    required = {
        "l1_code",
        "l1_name",
        "l2_code",
        "l2_name",
        "l3_code",
        "l3_name",
        "ts_code",
        "name",
        "in_date",
        "out_date",
        "is_new",
    }
    missing = sorted(required - set(raw.columns))
    if missing:
        raise SystemExit(f"Tushare index_member_all missing columns: {missing}")
    df = raw.copy()
    for col in [
        "l1_code",
        "l1_name",
        "l2_code",
        "l2_name",
        "l3_code",
        "l3_name",
        "ts_code",
        "name",
        "is_new",
    ]:
        df[col] = df[col].astype("string").str.strip()
    df["effective_date"] = _to_datetime_yyyymmdd(_column(df, "in_date"))
    raw_out = _to_datetime_yyyymmdd(_column(df, "out_date"))
    # Tushare out_date denotes the first date outside the industry membership.
    # cstree's effective-date expander keeps rows while trade_date <= end_date,
    # so store end_date as the previous calendar day for as-of semantics.
    df["end_date"] = raw_out - pd.Timedelta(days=1)
    df.loc[raw_out.isna(), "end_date"] = pd.NaT
    normalized = pd.DataFrame(
        {
            "symbol": df["ts_code"],
            "name": df["name"],
            "industry_system": "申万行业分类2021",
            "industry_level": "L3",
            "industry_code": df["l3_code"],
            "industry_name": df["l3_name"],
            "first_industry_code": df["l1_code"],
            "first_industry_name": df["l1_name"],
            "second_industry_code": df["l2_code"],
            "second_industry_name": df["l2_name"],
            "third_industry_code": df["l3_code"],
            "third_industry_name": df["l3_name"],
            "effective_date": df["effective_date"],
            "end_date": df["end_date"],
            "raw_in_date": df["in_date"],
            "raw_out_date": df["out_date"],
            "is_current": df["is_new"].eq("Y"),
            "provider": "tushare",
        }
    )
    normalized = normalized.dropna(subset=["symbol", "effective_date", "industry_code"]).copy()
    normalized = normalized.sort_values(["symbol", "effective_date", "end_date", "industry_code"])
    normalized = normalized.drop_duplicates(
        subset=["symbol", "effective_date", "end_date", "industry_code"],
        keep="last",
    )
    return normalized.reset_index(drop=True)


def _write_manifest(
    df: pd.DataFrame,
    raw_rows: int,
    *,
    out_dir: Path,
    snapshot_name: str,
    as_of_date: str,
) -> None:
    manifest = {
        "dataset": "industry_changes",
        "provider": "tushare",
        "market": "a_share",
        "schema_version": "tushare.index_member_all.sw2021_l3.v1",
        "status": "completed",
        "output_dir": str(out_dir),
        "snapshot_name": snapshot_name,
        "source_api": "index_member_all",
        "industry_system": "SW2021",
        "industry_level": "L3",
        "query_start_date": None,
        "query_end_date": None,
        "as_of_date": as_of_date,
        "totals": {
            "raw_rows": int(raw_rows),
            "rows": int(len(df)),
            "symbols": int(df["symbol"].nunique()),
            "current_rows": int(df["is_current"].sum()),
            "historical_rows": int((~df["is_current"]).sum()),
            "first_industries": int(df["first_industry_name"].nunique()),
            "third_industries": int(df["industry_name"].nunique()),
            "files": 1,
        },
        "columns": df.columns.tolist(),
    }
    (out_dir / "manifest.yml").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _update_alias(*, out_dir: Path, alias_dir: Path) -> None:
    alias_dir.parent.mkdir(parents=True, exist_ok=True)
    if alias_dir.exists() or alias_dir.is_symlink():
        if alias_dir.is_symlink() or alias_dir.is_file():
            alias_dir.unlink()
        else:
            # Keep an existing directory alias safe by replacing only if it was generated here.
            import shutil

            shutil.rmtree(alias_dir)
    rel_target = os.path.relpath(out_dir, alias_dir.parent)
    alias_dir.symlink_to(rel_target, target_is_directory=True)


def _update_registry(
    df: pd.DataFrame,
    *,
    as_of_date: str,
    versioned_snapshot_name: str,
    registry_path: Path,
) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "dataset_name": "a_share_industry_changes",
        "version": as_of_date,
        "market": "a_share",
        "type": "industry_changes",
        "date_range": (
            f"{df['effective_date'].min().date()} to "
            f"{pd.to_datetime(df['end_date']).dropna().max().date()}"
        ),
        "source": "tushare",
        "records": int(len(df)),
        "symbols": int(df["symbol"].nunique()),
        "description": (
            "current A-share SW2021 L3 industry membership changes from Tushare index_member_all"
        ),
        "path": (
            f"market-data-platform/assets/tushare/a_share/industry_changes/{versioned_snapshot_name}"
        ),
    }
    if registry_path.exists():
        comment_lines: list[str] = []
        data_lines: list[str] = []
        for line in registry_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                comment_lines.append(line)
            elif line.strip():
                data_lines.append(line)
        if data_lines:
            from io import StringIO

            reg = pd.read_csv(StringIO("\n".join(data_lines)))
        else:
            reg = pd.DataFrame()
        for col in row:
            if col not in reg.columns:
                reg[col] = pd.NA
        if not reg.empty:
            mask = reg["dataset_name"].astype(str).eq(row["dataset_name"])
            reg = reg.loc[~mask].copy()
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
        body = reg.to_csv(index=False).strip()
        prefix = "\n".join(comment_lines)
        registry_path.write_text(f"{prefix}\n{body}\n" if prefix else f"{body}\n", encoding="utf-8")
    else:
        pd.DataFrame([row]).to_csv(registry_path, index=False)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download SW2021 L3 industry-membership changes from Tushare "
        "and write a snapshot."
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=_default_data_root(),
        help=(
            "Dataset root directory. Defaults to DATA_PLATFORM_ROOT environment variable, "
            f"then {DEFAULT_DATA_ROOT.as_posix()}"
        ),
    )
    parser.add_argument(
        "--as-of-date",
        default=DEFAULT_AS_OF_DATE,
        help="As-of date for snapshot naming and registry version, format YYYYMMDD.",
    )
    parser.add_argument(
        "--snapshot-name",
        default=None,
        help=(
            "Override output snapshot directory name. If omitted, defaults to "
            f"{SNAPSHOT_NAME_PREFIX}_YYYYMMDD."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Page size for index_member_all pagination.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.15,
        help="Sleep between pages for rate-limit friendliness.",
    )
    return parser.parse_args()


def main() -> None:
    token = _load_token()
    args = _parse_args()
    as_of_date = _validate_as_of_date(args.as_of_date)
    paths = _resolve_paths(
        data_root=args.data_root,
        as_of_date=as_of_date,
        snapshot_name=args.snapshot_name,
    )
    out_dir = paths["out_dir"]
    alias_dir = paths["alias_dir"]
    registry_path = paths["registry"]
    snapshot_name = paths["snapshot"]
    ts.set_token(token)
    pro = ts.pro_api()
    raw_new = _fetch_paginated(
        pro,
        is_new="Y",
        limit=args.limit,
        sleep_seconds=args.sleep_seconds,
    )
    raw_old = _fetch_paginated(
        pro,
        is_new="N",
        limit=args.limit,
        sleep_seconds=args.sleep_seconds,
    )
    raw = pd.concat([raw_new, raw_old], ignore_index=True)
    df = _normalize_sw_members(raw)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(data_dir / "part.parquet", index=False)
    _write_manifest(
        df,
        len(raw),
        out_dir=out_dir,
        snapshot_name=snapshot_name,
        as_of_date=as_of_date,
    )
    _update_alias(out_dir=out_dir, alias_dir=alias_dir)
    _update_registry(
        df,
        as_of_date=as_of_date,
        versioned_snapshot_name=snapshot_name,
        registry_path=registry_path,
    )
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "alias_dir": str(alias_dir),
                "rows": int(len(df)),
                "symbols": int(df["symbol"].nunique()),
                "current_rows": int(df["is_current"].sum()),
                "historical_rows": int((~df["is_current"]).sum()),
                "first_industries": int(df["first_industry_name"].nunique()),
                "third_industries": int(df["industry_name"].nunique()),
                "date_min": str(df["effective_date"].min().date()),
                "date_max": str(df["effective_date"].max().date()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
