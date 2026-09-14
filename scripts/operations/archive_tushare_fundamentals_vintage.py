#!/usr/bin/env python3
"""Archive an immutable TuShare fundamentals observation vintage without publishing latest."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from market_data_platform.providers.tushare_a_share_fundamentals import (
    PitBuildOptions,
    RawFundamentalsDownloadOptions,
    build_normalized_fundamentals,
    build_pit_fundamentals,
    download_raw_fundamentals,
    validate_normalized_fundamentals,
    validate_pit_fundamentals,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    asset_manifest_payload,
    file_sha256,
    require_asset_integrity,
)
from market_data_platform.providers.tushare_common import write_manifest

DEFAULT_DATASETS = ("income", "balancesheet", "cashflow", "fina_indicator")
DEFAULT_FIELD_MAPPINGS = (
    "revenue=revenue",
    "total_revenue=total_revenue",
    "operate_profit=operate_profit",
    "total_profit=total_profit",
    "n_income=n_income",
    "n_income_attr_p=n_income_attr_p",
    "total_assets=total_assets",
    "total_liab=total_liab",
    "total_hldr_eqy_exc_min_int=total_hldr_eqy_exc_min_int",
    "n_cashflow_act=n_cashflow_act",
    "n_cashflow_inv_act=n_cashflow_inv_act",
    "n_cash_flows_fnc_act=n_cash_flows_fnc_act",
    "roe=roe",
    "roa=roa",
    "grossprofit_margin=grossprofit_margin",
    "netprofit_margin=netprofit_margin",
    "debt_to_assets=debt_to_assets",
    "assets_turn=assets_turn",
    "netprofit_yoy=netprofit_yoy",
    "or_yoy=or_yoy",
    "q_sales_yoy=q_sales_yoy",
)
OBSERVATION_FREQUENCIES = ("daily", "weekly", "ad_hoc")


def _date_token(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) != 8:
        raise ValueError(f"Expected YYYYMMDD date, got: {value}")
    datetime.strptime(digits, "%Y%m%d")
    return digits


def _today_shanghai() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")


def snapshot_root(artifacts_root: Path, snapshot_date: str) -> Path:
    return (
        artifacts_root.expanduser().resolve()
        / "assets"
        / "tushare"
        / "a_share"
        / "fundamentals_vintages"
        / f"vintage={_date_token(snapshot_date)}"
    )


def _validate_api_url(api_url: str | None) -> None:
    value = str(api_url or "").strip().rstrip("/")
    if not value or not re.fullmatch(r"https?://[^/\s]+(?:/[^?\s#]*)?", value):
        raise ValueError(
            "An explicit TuShare API URL is required (use --api-url or configuration)."
        )


def _require_passed(validation: Mapping[str, object], *, label: str) -> None:
    if validation.get("status") == "passed":
        return
    raw_checks = validation.get("checks")
    checks = (
        raw_checks
        if isinstance(raw_checks, Sequence) and not isinstance(raw_checks, (str, bytes))
        else []
    )
    failed = [
        str(row.get("id")) for row in checks if isinstance(row, Mapping) and not row.get("passed")
    ]
    raise RuntimeError(f"{label} validation failed: {', '.join(failed) or 'unknown check'}")


def _reuse_derived_snapshot(path: Path, *, expected_source_hashes: set[str]) -> dict | None:
    manifest = asset_manifest_payload(path)
    if manifest.get("status") != "completed" or manifest.get("immutable_snapshot") is not True:
        return None
    require_asset_integrity(path, manifest)
    source_integrity = manifest.get("source_integrity")
    values = (
        [source_integrity]
        if isinstance(source_integrity, Mapping) and "manifest_sha256" in source_integrity
        else list(source_integrity.values())
        if isinstance(source_integrity, Mapping)
        else []
    )
    actual_source_hashes = {
        str(value.get("manifest_sha256"))
        for value in values
        if isinstance(value, Mapping) and value.get("manifest_sha256")
    }
    if actual_source_hashes != expected_source_hashes:
        raise RuntimeError(f"Immutable derived snapshot source lineage changed: {path}")
    return manifest


def _archive_dataset(args: argparse.Namespace, root: Path, dataset: str) -> tuple[Path, Path]:
    raw = root / "raw" / dataset
    normalized = root / "normalized" / dataset
    raw_manifest = download_raw_fundamentals(
        RawFundamentalsDownloadOptions(
            dataset=dataset,
            out_dir=raw,
            start_date=args.start_date,
            end_date=args.end_date,
            entitlement_mode="vip_batch",
            token_env=args.token_env,
            api_url=args.api_url,
            run_id=f"{dataset}-{args.snapshot_date}",
            retry_attempts=args.retry_attempts,
            retry_backoff_seconds=args.retry_backoff_seconds,
            request_interval_seconds=args.request_interval_seconds,
            page_size=args.page_size,
            max_pages=args.max_pages,
        )
    )
    if raw_manifest.get("status") != "completed":
        raise RuntimeError(f"Raw {dataset} snapshot is incomplete: {raw}")
    raw_manifest_hash = file_sha256(raw / "manifest.yml")
    normalized_manifest = _reuse_derived_snapshot(
        normalized,
        expected_source_hashes={raw_manifest_hash},
    )
    if normalized_manifest is None:
        normalized_manifest = build_normalized_fundamentals(
            dataset=dataset,
            raw_dir=raw,
            out_dir=normalized,
        )
    _require_passed(
        validate_normalized_fundamentals(
            asset_dir=normalized,
            target_date=args.snapshot_date,
        ),
        label=f"Normalized {dataset}",
    )
    return raw, normalized


def _archive_pit(args: argparse.Namespace, root: Path, normalized: Sequence[Path]) -> Path:
    pit = root / "pit"
    source_hashes = {file_sha256(path / "manifest.yml") for path in normalized}
    manifest = _reuse_derived_snapshot(pit, expected_source_hashes=source_hashes)
    if manifest is None:
        build_pit_fundamentals(
            PitBuildOptions(
                normalized_dirs=normalized,
                out_dir=pit,
                field_mappings=args.field_mappings,
                available_delay_days=args.available_delay_days,
                max_observation_age_days=args.max_observation_age_days,
                bucket_count=args.bucket_count,
                batch_rows=args.batch_rows,
            )
        )
    _require_passed(
        validate_pit_fundamentals(
            asset_dir=pit,
            target_date=args.snapshot_date,
            batch_rows=args.batch_rows,
        ),
        label="PIT fundamentals",
    )
    return pit


def _child_receipt(root: Path, path: Path) -> dict[str, object]:
    manifest = asset_manifest_payload(path)
    require_asset_integrity(path, manifest)
    return {
        "path": path.relative_to(root).as_posix(),
        "manifest_sha256": file_sha256(path / "manifest.yml"),
        "content_aggregate_sha256": manifest["integrity"]["aggregate_sha256"],
        "observed_vintage_dates": manifest.get("observed_vintage_dates", []),
    }


def _seal_snapshot(
    args: argparse.Namespace,
    root: Path,
    raw_dirs: Sequence[Path],
    normalized_dirs: Sequence[Path],
    pit_dir: Path,
) -> dict[str, object]:
    children = {
        "raw": [_child_receipt(root, path) for path in raw_dirs],
        "normalized": [_child_receipt(root, path) for path in normalized_dirs],
        "pit": _child_receipt(root, pit_dir),
    }
    manifest = {
        "schema_version": "tushare.a_share.fundamentals.vintage_archive.v1",
        "status": "completed",
        "immutable_snapshot": True,
        "snapshot_date": args.snapshot_date,
        "query": {"start_date": args.start_date, "end_date": args.end_date},
        "datasets": list(args.datasets),
        "provider": "tushare",
        "api_url": args.api_url,
        "token_env": args.token_env,
        "children": children,
        "revision_safety": {
            "observation_class": "observed_vintage_pit_v2",
            "revision_safe_from": args.snapshot_date,
            "historical_periods_before_first_observation": "reconstructed_pit",
            "observation_frequency": args.observation_frequency,
        },
        "publication": {"latest_switched": False},
    }
    manifest_path = root / "manifest.yml"
    write_manifest(manifest_path, manifest)
    seal = {
        "schema_version": "tushare.a_share.fundamentals.vintage_seal.v1",
        "manifest": "manifest.yml",
        "manifest_sha256": file_sha256(manifest_path),
    }
    (root / "SEALED.json").write_text(
        json.dumps(seal, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_sealed_snapshot(root: Path) -> dict[str, object]:
    seal_path = root / "SEALED.json"
    manifest_path = root / "manifest.yml"
    if not seal_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError(f"Snapshot is not sealed: {root}")
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    if seal.get("manifest_sha256") != file_sha256(manifest_path):
        raise RuntimeError(f"Snapshot manifest seal does not match: {root}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping) or manifest.get("status") != "completed":
        raise RuntimeError(f"Snapshot manifest is incomplete: {root}")
    children = manifest.get("children")
    if not isinstance(children, Mapping):
        raise RuntimeError(f"Snapshot child receipts are missing: {root}")
    receipts = [*children.get("raw", []), *children.get("normalized", []), children.get("pit")]
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            raise RuntimeError(f"Invalid child receipt in {root}")
        child = (root / str(receipt.get("path") or "")).resolve()
        if not child.is_relative_to(root.resolve()):
            raise RuntimeError(f"Unsafe child receipt path in {root}")
        child_manifest = asset_manifest_payload(child)
        require_asset_integrity(child, child_manifest)
        if receipt.get("manifest_sha256") != file_sha256(child / "manifest.yml"):
            raise RuntimeError(f"Child manifest hash does not match: {child}")
    return dict(manifest)


def archive_vintage(args: argparse.Namespace) -> dict[str, object]:
    _validate_api_url(args.api_url)
    root = snapshot_root(args.artifacts_root, args.snapshot_date)
    if (root / "SEALED.json").is_file():
        return verify_sealed_snapshot(root)
    root.mkdir(parents=True, exist_ok=True)
    raw_dirs = []
    normalized_dirs = []
    for dataset in args.datasets:
        raw, normalized = _archive_dataset(args, root, dataset)
        raw_dirs.append(raw)
        normalized_dirs.append(normalized)
    pit = _archive_pit(args, root, normalized_dirs)
    manifest = _seal_snapshot(args, root, raw_dirs, normalized_dirs, pit)
    verify_sealed_snapshot(root)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--snapshot-date", default=_today_shanghai())
    parser.add_argument("--start-date", default="20150101")
    parser.add_argument("--end-date")
    parser.add_argument("--dataset", dest="datasets", action="append")
    parser.add_argument("--field-map", dest="field_mappings", action="append")
    parser.add_argument("--token-env", default="TUSHARE_TOKEN_2")
    parser.add_argument(
        "--api-url",
        default=None,
        help="Explicit TuShare API URL; configure it locally or pass it on the command line.",
    )
    parser.add_argument("--retry-attempts", type=int, default=5)
    parser.add_argument("--retry-backoff-seconds", type=float, default=2.0)
    parser.add_argument("--request-interval-seconds", type=float, default=0.15)
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--available-delay-days", type=int, default=1)
    parser.add_argument("--max-observation-age-days", type=int, default=3)
    parser.add_argument("--bucket-count", type=int, default=128)
    parser.add_argument("--batch-rows", type=int, default=65536)
    parser.add_argument(
        "--observation-frequency",
        choices=OBSERVATION_FREQUENCIES,
        default="daily",
        help="Declared cadence for this immutable observation lane.",
    )
    parser.add_argument("--verify-only", action="store_true")
    return parser


def _prepare_args(args: argparse.Namespace) -> argparse.Namespace:
    args.snapshot_date = _date_token(args.snapshot_date)
    args.start_date = _date_token(args.start_date)
    args.end_date = _date_token(args.end_date or args.snapshot_date)
    args.datasets = tuple(dict.fromkeys(args.datasets or DEFAULT_DATASETS))
    args.field_mappings = tuple(args.field_mappings or DEFAULT_FIELD_MAPPINGS)
    if args.start_date > args.end_date:
        raise ValueError("start_date must not be after end_date")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _prepare_args(build_parser().parse_args(argv))
    root = snapshot_root(args.artifacts_root, args.snapshot_date)
    result = verify_sealed_snapshot(root) if args.verify_only else archive_vintage(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
