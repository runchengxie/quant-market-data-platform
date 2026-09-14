"""Full-range semantic audit for a frozen TuShare minute candidate."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from market_data_platform._tushare_minute_campaign_readiness import file_sha256
from market_data_platform.minute_candidate import (
    FEATURE_NAMES,
    SEMANTIC_AUDIT_SCHEMA_VERSION,
    MinuteCandidateError,
    _atomic_write_json,
    _distribution,
    _finite_float,
    _json_sha256,
    _now,
    _read_json,
    _valid_inventory,
)
from market_data_platform.minute_candidate_audit_sql import SYMBOL_AUDIT_SQL


def _safe_divide(numerator: Any, denominator: Any) -> Any:
    import pandas as pd

    top = pd.to_numeric(numerator, errors="coerce")
    bottom = pd.to_numeric(denominator, errors="coerce")
    return top.div(bottom.where(bottom != 0))


def _feature_columns(frame: Any, *, annual: bool) -> dict[str, tuple[Any, Any]]:
    t_first_open = frame["t_first_open_no_0930"] if annual else frame["t_first_open"]
    t_high = frame["t_high_no_0930"] if annual else frame["t_high"]
    t_low = frame["t_low_no_0930"] if annual else frame["t_low"]
    t_vol = frame["t_vol_no_0930"] if annual else frame["t_vol"]
    t_amount = frame["t_amount_no_0930"] if annual else frame["t_amount"]
    t_open_vol = frame["t_open_vol_no_0930"] if annual else frame["t_open_vol"]
    t_active = frame["t_active_minutes_no_0930"] if annual else frame["t_active_minutes"]
    return {
        "intraday_return": (
            _safe_divide(frame["g_last_close"], frame["g_first_open"]) - 1.0,
            _safe_divide(frame["t_last_close"], t_first_open) - 1.0,
        ),
        "range": (
            _safe_divide(frame["g_high"], frame["g_low"]) - 1.0,
            _safe_divide(t_high, t_low) - 1.0,
        ),
        "volume": (frame["g_vol"], t_vol),
        "amount": (frame["g_amount"], t_amount),
        "opening_volume_share": (
            _safe_divide(frame["g_open_vol"], frame["g_vol"]),
            _safe_divide(t_open_vol, t_vol),
        ),
        "closing_volume_share": (
            _safe_divide(frame["g_close_vol"], frame["g_vol"]),
            _safe_divide(frame["t_close_vol"], t_vol),
        ),
        "vwap": (
            _safe_divide(frame["g_amount"], frame["g_vol"]),
            _safe_divide(t_amount, t_vol),
        ),
        "active_minutes": (frame["g_active_minutes"], t_active),
    }


def _feature_regression(frame: Any, *, annual: bool) -> dict[str, dict[str, Any]]:
    import numpy as np
    import pandas as pd

    result: dict[str, dict[str, Any]] = {}
    for name, (guan_raw, candidate_raw) in _feature_columns(frame, annual=annual).items():
        guan = pd.to_numeric(guan_raw, errors="coerce").astype("float64")
        candidate = pd.to_numeric(candidate_raw, errors="coerce").astype("float64")
        valid = np.isfinite(guan) & np.isfinite(candidate)
        guan = guan.loc[valid]
        candidate = candidate.loc[valid]
        count = int(valid.sum())
        comparable_ranks = guan.nunique(dropna=True) > 1 and candidate.nunique(dropna=True) > 1
        rank_correlation = (
            _finite_float(guan.rank().corr(candidate.rank(), method="pearson"))
            if count >= 3 and comparable_ranks
            else None
        )
        relative = (candidate - guan).div(guan.abs().where(guan != 0))
        result[name] = {
            "count": count,
            "rank_correlation": rank_correlation,
            "candidate_minus_guan_relative": _distribution(relative.tolist()),
            "guan": _distribution(guan.tolist()),
            "candidate": _distribution(candidate.tolist()),
        }
    return result


def _sum_frame(frame: Any, column: str) -> float:
    return float(frame[column].fillna(0).sum())


def _daily_universe(
    inventory_record: dict[str, Any],
    frame: Any,
    common: Any,
    has_g: Any,
    has_t: Any,
) -> dict[str, Any]:
    return {
        "canonical_sh_sz_symbols": int(has_g.sum()),
        "candidate_sh_sz_symbols": int(has_t.sum()),
        "common_sh_sz_symbols": len(common),
        "canonical_only_symbols": int((has_g & ~has_t).sum()),
        "candidate_only_symbols": int((has_t & ~has_g).sum()),
        "candidate_bj_symbols": int(inventory_record["candidate"]["market_symbol_counts"]["BJ"]),
    }


def _daily_time_grid(common: Any, *, annual: bool) -> dict[str, Any]:
    return {
        "canonical_time_min": str(common["g_time_min"].min()),
        "canonical_time_max": str(common["g_time_max"].max()),
        "candidate_time_min": str(common["t_time_min"].min()),
        "candidate_time_max": str(common["t_time_max"].max()),
        "canonical_0930_symbol_rows": int(_sum_frame(common, "g_0930_rows")),
        "candidate_0930_symbol_rows": int(_sum_frame(common, "t_0930_rows")),
        "comparison_excludes_candidate_0930": annual,
    }


def _daily_price(common: Any, *, common_keys: int, relative_count: int) -> dict[str, Any]:
    exact_count = int(_sum_frame(common, "close_exact_count"))
    return {
        "close_count": common_keys,
        "close_exact_count": exact_count,
        "close_exact_rate": exact_count / common_keys if common_keys else None,
        "close_mean_abs_error": (
            _sum_frame(common, "close_abs_error_sum") / common_keys if common_keys else None
        ),
        "close_max_abs_error": _finite_float(common["close_abs_error_max"].max()),
        "close_mean_abs_relative_error": (
            _sum_frame(common, "close_abs_relative_error_sum") / relative_count
            if relative_count
            else None
        ),
        "open_mean_abs_error": (
            _sum_frame(common, "open_abs_error_sum") / common_keys if common_keys else None
        ),
        "high_mean_abs_error": (
            _sum_frame(common, "high_abs_error_sum") / common_keys if common_keys else None
        ),
        "low_mean_abs_error": (
            _sum_frame(common, "low_abs_error_sum") / common_keys if common_keys else None
        ),
    }


def _daily_semantic_record(
    inventory_record: dict[str, Any],
    frame: Any,
) -> dict[str, Any]:
    import pandas as pd

    trade_date = inventory_record["trade_date"]
    annual = inventory_record["canonical"]["canonical_source"] == "guan_annual_minbar"
    has_g = frame["g_rows"].notna()
    has_t = frame["t_rows"].notna()
    common = frame.loc[has_g & has_t].copy()
    if common.empty:
        raise MinuteCandidateError(f"No common SH/SZ symbols for {trade_date}")
    relative_count = int(_sum_frame(common, "close_relative_count"))
    common_keys = int(_sum_frame(common, "common_keys"))
    canonical_rows = int(_sum_frame(frame.loc[has_g], "g_rows"))
    candidate_rows = int(_sum_frame(frame.loc[has_t], "t_rows"))
    t_vol_column = "t_vol_no_0930" if annual else "t_vol"
    t_amount_column = "t_amount_no_0930" if annual else "t_amount"
    g_vol = pd.to_numeric(common["g_vol"], errors="coerce")
    g_amount = pd.to_numeric(common["g_amount"], errors="coerce")
    t_vol = pd.to_numeric(common[t_vol_column], errors="coerce")
    t_amount = pd.to_numeric(common[t_amount_column], errors="coerce")
    return {
        "trade_date": trade_date,
        "year": trade_date[:4],
        "canonical_source": inventory_record["canonical"]["canonical_source"],
        "tier": inventory_record["canonical"]["tier"],
        "inputs": {
            "canonical_partition_sha256": inventory_record["canonical"]["partition_sha256"],
            "candidate_partition_sha256": inventory_record["candidate"]["partition_sha256"],
        },
        "universe": _daily_universe(inventory_record, frame, common, has_g, has_t),
        "time_grid": _daily_time_grid(common, annual=annual),
        "keys": {
            "canonical_rows": canonical_rows,
            "candidate_rows": candidate_rows,
            "common_keys": common_keys,
            "common_key_rate_vs_canonical": (
                common_keys / canonical_rows if canonical_rows else None
            ),
        },
        "price": _daily_price(
            common,
            common_keys=common_keys,
            relative_count=relative_count,
        ),
        "flow": {
            "comparison_basis": "09:31-15:00" if annual else "09:30-15:00",
            "guan_over_candidate_volume": _distribution(
                g_vol.div(t_vol.where(t_vol != 0)).tolist()
            ),
            "guan_over_candidate_amount": _distribution(
                g_amount.div(t_amount.where(t_amount != 0)).tolist()
            ),
        },
        "feature_regression": _feature_regression(common, annual=annual),
    }


def _audit_one_inventory_record(
    connection: Any,
    inventory_record: dict[str, Any],
) -> dict[str, Any]:
    frame = connection.execute(
        SYMBOL_AUDIT_SQL,
        [
            inventory_record["canonical"]["partition_path"],
            inventory_record["candidate"]["partition_path"],
        ],
    ).fetchdf()
    return _daily_semantic_record(inventory_record, frame)


def _weighted_error_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    count = sum(int(record["price"]["close_count"]) for record in records)
    exact = sum(int(record["price"]["close_exact_count"]) for record in records)
    absolute_sum = sum(
        float(record["price"]["close_mean_abs_error"]) * int(record["price"]["close_count"])
        for record in records
    )
    relative_sum = sum(
        float(record["price"]["close_mean_abs_relative_error"])
        * int(record["price"]["close_count"])
        for record in records
    )
    return {
        "count": count,
        "exact_count": exact,
        "exact_rate": exact / count if count else None,
        "mean_abs_error": absolute_sum / count if count else None,
        "mean_abs_relative_error": relative_sum / count if count else None,
        "max_abs_error": max(
            (
                float(record["price"]["close_max_abs_error"])
                for record in records
                if record["price"]["close_max_abs_error"] is not None
            ),
            default=None,
        ),
    }


def _period_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    feature_summary = {
        name: {
            "daily_rank_correlation": _distribution(
                record["feature_regression"][name]["rank_correlation"] for record in records
            ),
            "daily_median_relative_delta": _distribution(
                record["feature_regression"][name]["candidate_minus_guan_relative"]["median"]
                for record in records
            ),
        }
        for name in FEATURE_NAMES
    }
    return {
        "dates": len(records),
        "date_min": records[0]["trade_date"],
        "date_max": records[-1]["trade_date"],
        "canonical_sources": dict(
            sorted(Counter(record["canonical_source"] for record in records).items())
        ),
        "universe": {
            "canonical_only_symbol_days": sum(
                int(record["universe"]["canonical_only_symbols"]) for record in records
            ),
            "candidate_only_symbol_days": sum(
                int(record["universe"]["candidate_only_symbols"]) for record in records
            ),
            "common_symbol_days": sum(
                int(record["universe"]["common_sh_sz_symbols"]) for record in records
            ),
            "candidate_bj_symbol_days": sum(
                int(record["universe"]["candidate_bj_symbols"]) for record in records
            ),
        },
        "keys": {
            "canonical_rows": sum(int(record["keys"]["canonical_rows"]) for record in records),
            "candidate_rows": sum(int(record["keys"]["candidate_rows"]) for record in records),
            "common_keys": sum(int(record["keys"]["common_keys"]) for record in records),
        },
        "price": _weighted_error_summary(records),
        "flow": {
            "daily_median_guan_over_candidate_volume": _distribution(
                record["flow"]["guan_over_candidate_volume"]["median"] for record in records
            ),
            "daily_median_guan_over_candidate_amount": _distribution(
                record["flow"]["guan_over_candidate_amount"]["median"] for record in records
            ),
        },
        "features": feature_summary,
    }


def _cutover_gates(summary: dict[str, Any]) -> dict[str, Any]:
    checks = [
        {
            "name": "close_mean_abs_relative_error",
            "actual": summary["price"]["mean_abs_relative_error"],
            "operator": "<=",
            "threshold": 0.001,
        },
        {
            "name": "intraday_return_daily_rank_median",
            "actual": summary["features"]["intraday_return"]["daily_rank_correlation"]["median"],
            "operator": ">=",
            "threshold": 0.99,
        },
        {
            "name": "range_daily_rank_median",
            "actual": summary["features"]["range"]["daily_rank_correlation"]["median"],
            "operator": ">=",
            "threshold": 0.99,
        },
        {
            "name": "volume_daily_rank_median",
            "actual": summary["features"]["volume"]["daily_rank_correlation"]["median"],
            "operator": ">=",
            "threshold": 0.98,
        },
        {
            "name": "amount_daily_rank_median",
            "actual": summary["features"]["amount"]["daily_rank_correlation"]["median"],
            "operator": ">=",
            "threshold": 0.98,
        },
    ]
    for check in checks:
        actual = cast("float | int", check["actual"])
        if actual is None:
            check["passed"] = False
            continue
        threshold = cast("float | int", check["threshold"])
        check["passed"] = bool(
            actual <= threshold if check["operator"] == "<=" else actual >= threshold
        )
    return {
        "policy": "candidate_cutover_diagnostic_v1",
        "checks": checks,
        "automated_checks_passed": all(check["passed"] for check in checks),
        "manual_source_regime_review_required": True,
        "canonical_cutover_approved": False,
    }


def audit_candidate_semantics(
    inventory_json: str | Path,
    output_json: str | Path,
    work_dir: str | Path,
    *,
    threads: int = 4,
) -> dict[str, Any]:
    """Compare every Guan/candidate day and checkpoint compact daily evidence."""

    import duckdb

    if threads < 1:
        raise ValueError("threads must be positive")
    inventory_path = Path(inventory_json).expanduser().resolve()
    output_path = Path(output_json).expanduser().resolve()
    checkpoint_root = Path(work_dir).expanduser().resolve() / "daily"
    inventory = _read_json(inventory_path)
    if not _valid_inventory(inventory):
        raise MinuteCandidateError(f"Candidate inventory is invalid: {inventory_path}")
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    connection.execute(f"SET threads={int(threads)}")
    daily: list[dict[str, Any]] = []
    try:
        for inventory_record in inventory["partitions"]:
            trade_date = inventory_record["trade_date"]
            checkpoint_path = checkpoint_root / f"{trade_date}.json"
            input_hashes = {
                "canonical_partition_sha256": inventory_record["canonical"]["partition_sha256"],
                "candidate_partition_sha256": inventory_record["candidate"]["partition_sha256"],
            }
            if checkpoint_path.is_file():
                checkpoint = _read_json(checkpoint_path)
                if checkpoint.get("inputs") == input_hashes:
                    daily.append(checkpoint)
                    continue
            checkpoint = _audit_one_inventory_record(connection, inventory_record)
            _atomic_write_json(checkpoint_path, checkpoint)
            daily.append(checkpoint)
    finally:
        connection.close()
    daily.sort(key=lambda record: record["trade_date"])
    by_year = {
        year: _period_summary([record for record in daily if record["year"] == year])
        for year in sorted({record["year"] for record in daily})
    }
    summary = _period_summary(daily)
    payload = {
        "schema_version": SEMANTIC_AUDIT_SCHEMA_VERSION,
        "status": "complete",
        "quality_status": "diagnostic",
        "generated_at": _now(),
        "diagnostic_only": True,
        "mutation_performed": False,
        "inputs": {
            "inventory": str(inventory_path),
            "inventory_sha256": file_sha256(inventory_path),
            "partitions_sha256": inventory["partitions_sha256"],
        },
        "policy": {
            "comparison_market": "SH_SZ",
            "candidate_bj_disposition": "structural_only_no_guan_comparator",
            "annual_time_basis": "compare_TuShare_09:31_15:00_to_Guan_annual",
            "deal_time_basis": "compare_full_09:30_15:00",
            "source_values": "preserved_no_shift_no_fill_no_clipping",
        },
        "summary": summary,
        "by_year": by_year,
        "cutover_gates": _cutover_gates(summary),
        "daily_checkpoint_dir": str(checkpoint_root),
        "daily_records_sha256": _json_sha256(daily),
        "daily": daily,
    }
    _atomic_write_json(output_path, payload)
    return payload


__all__ = ["audit_candidate_semantics"]
