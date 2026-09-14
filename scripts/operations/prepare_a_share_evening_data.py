"""Prepare the TuShare inputs consumed by the market-data-platform evening report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from market_data_platform.contract import load_current_contract
from market_data_platform.providers._env import _load_tushare_env_files

DATASETS = (
    ("daily", "mirror-a-share-daily", "daily/a_share_all_daily_latest", True),
    ("adj_factor", "mirror-a-share-adj-factor", "adj_factor/a_share_all_adj_factor_latest", True),
    (
        "daily_basic",
        "mirror-a-share-daily-basic",
        "daily_basic/a_share_all_daily_basic_latest",
        True,
    ),
    (
        "limit_status",
        "mirror-a-share-limit-status",
        "limit_status/a_share_limit_status_latest",
        True,
    ),
    ("margin", "mirror-a-share-margin", "margin/a_share_all_margin_latest", False),
    (
        "margin_detail",
        "mirror-a-share-margin-detail",
        "margin_detail/a_share_all_margin_detail_latest",
        False,
    ),
    ("hsgt_top10", "mirror-a-share-hsgt-top10", "hsgt_top10/a_share_all_hsgt_top10_latest", False),
    ("ths_hot", "mirror-a-share-ths-hot", "ths_hot/a_share_all_ths_hot_latest", False),
    ("dc_concept", "mirror-a-share-dc-concept", "dc_concept/a_share_all_dc_concept_latest", False),
    (
        "dc_concept_cons",
        "mirror-a-share-dc-concept-cons",
        "dc_concept_cons/a_share_all_dc_concept_cons_latest",
        False,
    ),
    (
        "kpl_concept_cons",
        "mirror-a-share-kpl-concept-cons",
        "kpl_concept_cons/a_share_all_kpl_concept_cons_latest",
        False,
    ),
    ("kpl_list", "mirror-a-share-kpl-list", "kpl_list/a_share_all_kpl_list_latest", False),
    (
        "moneyflow_ths",
        "mirror-a-share-moneyflow-ths",
        "moneyflow_ths/a_share_all_moneyflow_ths_latest",
        False,
    ),
    (
        "limit_list_ths",
        "mirror-a-share-limit-list-ths",
        "limit_list_ths/a_share_all_limit_list_ths_latest",
        False,
    ),
    ("limit_step", "mirror-a-share-limit-step", "limit_step/a_share_all_limit_step_latest", False),
    (
        "limit_cpt_list",
        "mirror-a-share-limit-cpt-list",
        "limit_cpt_list/a_share_all_limit_cpt_list_latest",
        False,
    ),
)
INDEX_CODES = (
    "000001.SH",
    "399001.SZ",
    "399006.SZ",
    "000688.SH",
    "000300.SH",
    "000905.SH",
    "000852.SH",
    "000016.SH",
)


def _latest_completed_daily_date(data_root: Path) -> str | None:
    """Return the newest daily partition already present in the data lake.

    The current contract may intentionally lag while a new daily publication
    is being built. Evening-data preparation must still be able to consume the
    canonical latest mirror in that window; otherwise the 19:00 report can
    prepare yesterday's receipt and then fail looking for today's receipt.
    """

    contract_path = data_root / "metadata/current_assets/a_share_current.json"
    payload = load_current_contract(contract_path)
    daily_roots = [data_root / "assets/tushare/a_share/daily/a_share_all_daily_latest"]
    if payload is not None:
        entry = payload.get("assets", {}).get("daily")
        if isinstance(entry, dict):
            value = entry.get("resolved_path") or entry.get("alias_path")
            if value:
                daily_roots.append(Path(str(value)).expanduser().resolve())

    dates: list[str] = []
    for daily_root in daily_roots:
        data_dir = daily_root / "data"
        dates.extend(
            path.name.split("=", 1)[1]
            for path in data_dir.glob("trade_date=*")
            if path.is_dir() and "=" in path.name and path.name.split("=", 1)[1].isdigit()
        )
    return max(dates) if dates else None


def _partition(path: Path, trade_date: str) -> Path:
    return path / "data" / f"trade_date={trade_date}" / "part.parquet"


def _run(
    command: str, out_dir: Path, date: str, token_env: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "market_data_platform.cli",
            "tushare",
            command,
            "--start-date",
            date,
            "--end-date",
            date,
            "--out-dir",
            str(out_dir),
            "--token-env",
            token_env,
            "--skip-existing",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _run_index(out_dir: Path, date: str, token_env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "market_data_platform.cli",
            "tushare",
            "mirror-a-share-index-daily",
            "--start-date",
            date,
            "--end-date",
            date,
            "--out-dir",
            str(out_dir),
            "--token-env",
            token_env,
            "--skip-existing",
            *sum((["--index-code", code] for code in INDEX_CODES), []),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def prepare(date: str, data_root: Path, token_env: str, premium: bool) -> tuple[Path, int]:
    asset_root = data_root / "assets" / "tushare" / "a_share"
    rows: list[dict[str, object]] = []
    for name, command, relative, required in DATASETS:
        if not required and not premium:
            continue
        out_dir = asset_root / relative
        result = _run(command, out_dir, date, token_env)
        partition = _partition(out_dir, date)
        ok = result.returncode == 0 and partition.is_file() and partition.stat().st_size > 0
        rows.append(
            {
                "dataset": name,
                "required": required,
                "status": "ready" if ok else "failed",
                "path": str(partition),
                "command_status": result.returncode,
                "output_tail": (result.stdout + result.stderr)[-1200:],
            }
        )

    index_dir = asset_root / "index_daily" / "a_share_all_index_daily_latest"
    result = _run_index(index_dir, date, token_env)
    index_file = index_dir / "data" / "part.parquet"
    rows.append(
        {
            "dataset": "index_daily",
            "required": True,
            "status": "ready"
            if result.returncode == 0 and index_file.is_file() and index_file.stat().st_size > 0
            else "failed",
            "path": str(index_file),
            "command_status": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-1200:],
        }
    )

    report_dir = data_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "market_data_platform.a_share_evening_data.v1",
        "trade_date": date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "producer": "market-data-platform",
        "premium_enabled": premium,
        "datasets": rows,
    }
    destination = report_dir / f"a_share_evening_data_{date}.json"
    temporary = destination.with_suffix(f".json.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    required_failed = any(row["required"] and row["status"] != "ready" for row in rows)
    return destination, int(required_failed)


def main() -> int:
    _load_tushare_env_files()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=None,
        help="Trade date in YYYYMMDD form (default: newest completed daily partition).",
    )
    parser.add_argument("--artifacts-root", default=os.environ.get("DATA_PLATFORM_ROOT"))
    default_token_env = os.environ.get("A_SHARE_TUSHARE_CORE_TOKEN_ENV")
    if not default_token_env:
        default_token_env = (
            "TUSHARE_TOKEN_2"
            if os.environ.get("TUSHARE_TOKEN_2") and os.environ.get("TUSHARE_API_URL_2")
            else "TUSHARE_TOKEN"
        )
    parser.add_argument("--token-env", default=default_token_env)
    parser.add_argument(
        "--premium", action="store_true", help="Prepare report enhancement datasets too."
    )
    args = parser.parse_args()
    if not args.artifacts_root:
        parser.error("--artifacts-root or DATA_PLATFORM_ROOT is required")
    data_root = Path(args.artifacts_root).expanduser()
    target_date = args.date or _latest_completed_daily_date(data_root)
    if target_date is None:
        target_date = datetime.now().astimezone().strftime("%Y%m%d")
    if len(target_date) != 8 or not target_date.isdigit():
        parser.error("--date must be YYYYMMDD")
    receipt, status = prepare(target_date, data_root, args.token_env, args.premium)
    print(
        json.dumps(
            {"receipt": str(receipt), "status": "ready" if status == 0 else "failed"},
            ensure_ascii=False,
        )
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
