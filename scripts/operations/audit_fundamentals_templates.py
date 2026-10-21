"""Diagnose template repair using sealed raw inputs; never publish a normalized asset."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from quant_market_data_platform.providers.tushare_a_share_fundamentals_part01 import DATASET_SPECS
from quant_market_data_platform.providers.tushare_a_share_fundamentals_part02 import (
    _assert_unambiguous_normalized_rows,
    _revision_safe_raw_inputs,
    _select_latest_provider_update,
)
from quant_market_data_platform.providers.tushare_a_share_fundamentals_part03 import (
    _filter_normalized_report_rows,
)
from quant_market_data_platform.providers.tushare_a_share_fundamentals_support import (
    FieldValidationError,
    asset_manifest_payload,
    date_token,
    file_sha256,
)
from quant_market_data_platform.providers.tushare_common import normalize_ts_code, pandas

STATEMENT_DATASETS = ("income", "balancesheet", "cashflow")


def run(vintage: Path, output: Path, *, datasets: Sequence[str] = STATEMENT_DATASETS) -> dict:
    """Quarantine every ambiguous observation key after the ordinary revision filters."""
    selected = tuple(dict.fromkeys(datasets))
    if not selected or any(name not in STATEMENT_DATASETS for name in selected):
        raise ValueError("datasets must select income, balancesheet, or cashflow")
    pd = pandas()
    if output.exists():
        raise ValueError("output must be a new directory")
    output.mkdir(parents=True)
    audit = {
        "schema_version": "financial_template_diagnostic.v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "historical_pit_certified": False,
        "published_asset": False,
        "new_api_requests": 0,
        "quarantine_policy": "exclude all ambiguous keys after provider revision selection",
        "datasets": {},
    }
    for name in selected:
        source = vintage / "raw" / name
        manifest = asset_manifest_payload(source)
        _, observation, frames = _revision_safe_raw_inputs(source, manifest)
        frame = pd.concat(frames, ignore_index=True, sort=False)
        spec = DATASET_SPECS[name]
        frame["symbol"] = frame.ts_code.map(normalize_ts_code)
        frame = frame.loc[frame.symbol.ne("")].copy()
        for column in spec.date_fields:
            if column in frame:
                frame[column] = frame[column].map(date_token)
        dropped = {}
        frame = _filter_normalized_report_rows(name, spec, frame, dropped)
        keys = ["symbol", *[k for k in spec.dedupe_keys if k != "ts_code" and k in frame]]
        keys += ["_source_retrieved_at"]
        frame, update_drops, announcement_drops = _select_latest_provider_update(frame, keys)
        conflicts = []
        rejected = []
        for _, group in frame.loc[frame.duplicated(keys, keep=False)].groupby(keys, dropna=False):
            try:
                _assert_unambiguous_normalized_rows(group, keys)
            except FieldValidationError:
                rejected.extend(group.index.tolist())
                compare = [
                    c for c in frame if c not in keys and c != "ann_date" and not c.startswith("_")
                ]
                different = [
                    c for c in compare if group[c].astype("string").fillna("<NA>").nunique() > 1
                ]
                conflicts.append(
                    {
                        "key": {k: str(group[k].iloc[0]) for k in keys},
                        "rows": len(group),
                        "templates": sorted(group.comp_type.unique().tolist()),
                        "different_columns": different,
                    }
                )
        conflicts_path = output / f"{name}_quarantined_rows.parquet"
        frame.loc[rejected].to_parquet(conflicts_path, index=False)
        clean = frame.drop(index=rejected).drop_duplicates(keys, keep="last")
        _assert_unambiguous_normalized_rows(clean, keys)
        path = output / f"{name}_diagnostic.parquet"
        clean.to_parquet(path, index=False)
        old = vintage / "normalized" / name / "manifest.yml"
        audit["datasets"][name] = {
            "raw_manifest_sha256": file_sha256(source / "manifest.yml"),
            "old_normalized_manifest_sha256": file_sha256(old) if old.is_file() else None,
            "source_observation": observation,
            "source_rows": sum(len(f) for f in frames),
            "diagnostic_rows": len(clean),
            "filter_drops": dropped,
            "superseded_update_flag": update_drops,
            "superseded_ann_date": announcement_drops,
            "quarantined_keys": conflicts,
            "quarantined_rows": len(rejected),
            "diagnostic_sha256": file_sha256(path),
            "quarantined_sha256": file_sha256(conflicts_path),
        }
        print(name, "diagnostic rows", len(clean), "quarantined keys", len(conflicts), flush=True)
    (output / "diagnostic_receipt.json").write_text(json.dumps(audit, indent=2) + "\n")
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vintage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", dest="datasets", action="append", choices=STATEMENT_DATASETS)
    args = parser.parse_args()
    run(args.vintage.resolve(), args.output.resolve(), datasets=args.datasets or STATEMENT_DATASETS)
