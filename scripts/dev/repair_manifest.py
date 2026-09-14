"""Repair manifest.yml for trade-date-partitioned assets to reflect actual disk totals."""

import argparse
from pathlib import Path

import pandas as pd
import yaml


def repair_manifest(asset_dir: Path) -> dict:
    data_dir = asset_dir / "data"
    if not data_dir.is_dir():
        raise FileNotFoundError(f"No data dir: {data_dir}")

    partitions = sorted(d for d in data_dir.iterdir() if (d / "part.parquet").exists())
    if not partitions:
        raise FileNotFoundError(f"No partitions in: {data_dir}")

    frames = [pd.read_parquet(d / "part.parquet") for d in partitions]
    df = pd.concat(frames, ignore_index=True)

    manifest_path = asset_dir / "manifest.yml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest: {manifest_path}")

    manifest = yaml.safe_load(manifest_path.read_text()) or {}

    # Recompute totals from disk
    rows = int(len(df))
    symbols = (
        int(df["symbol"].nunique())
        if "symbol" in df.columns
        else (int(df["ts_code"].nunique()) if "ts_code" in df.columns else 0)
    )
    actual_fields = sorted(df.columns.tolist())

    totals = manifest.get("totals", {})
    totals["rows"] = rows
    totals["symbols"] = symbols
    totals["files"] = len(partitions)
    totals["trade_dates_on_disk"] = len(partitions)
    manifest["totals"] = totals

    query = manifest.get("query", {})
    query["actual_fields_on_disk"] = actual_fields
    manifest["query"] = query

    manifest["_repaired_at"] = pd.Timestamp.now().isoformat()

    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    return {
        "asset": asset_dir.name,
        "rows": rows,
        "symbols": symbols,
        "files": len(partitions),
        "fields": actual_fields,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("asset_dir", nargs="+")
    args = p.parse_args()
    for d in args.asset_dir:
        result = repair_manifest(Path(d))
        print(
            f"{result['asset']}: {result['rows']} rows, "
            f"{result['symbols']} symbols, {result['files']} files"
        )
        print(f"  fields: {result['fields']}")
