"""Private materialization helpers for A-share minute coverage."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from market_data_platform.providers._coverage_classify import (
    _classify_minute_coverage,
)
from market_data_platform.providers._coverage_common import (
    _TUSHARE_FULL_BARS_PER_SYMBOL,
    ANNUAL_FULL_SH_SZ,
    DEAL_FULL_SH_SZ,
    GUAN_PARTIAL_SESSION,
    MISSING,
    PRODUCTION_COVERAGE_REQUIREMENTS,
    TUSHARE_FULL_A_SHARE,
    TUSHARE_PARTIAL_TOP200,
    CoverageRequirements,
    DailyCoverageExpectation,
    ExpectedPartitionStats,
    MarketReferenceStats,
    _atomic_write_json,
    _CoverageClassificationRequest,
    _date_set,
    _sha256_file,
    _sum_issue_maps,
    _validate_date,
    apply_bj_overlay_coverage,
)
from market_data_platform.providers._coverage_materialize_part01 import (
    _accepted_diagnostics,
    _fatal_issues,
    _frame_issues,
    _partial_shape_issues,
)
from market_data_platform.providers._coverage_materialize_part02 import (
    _coerce_expected_stats,
    _coerce_market_reference,
    _discover_output_layout,
    _overlay_frame_disposition,
    _session_semantic_issues,
    _source_reconciliation_issues,
)
from market_data_platform.providers.a_share_minute_fusion import (
    CANONICAL_MINUTE_COLUMNS,
    CANONICAL_MINUTE_SCHEMA,
)
from market_data_platform.providers.a_share_minute_price_flow import (
    POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
    TUSHARE_VWAP_DIAGNOSTIC_SOURCES,
    ZERO_VOLUME_NONZERO_AMOUNT_ISSUE,
)


def _audit_partition(
    expectation: DailyCoverageExpectation,
    path: Path,
    *,
    expected_stats: ExpectedPartitionStats | None,
    market_reference: MarketReferenceStats | None,
    partial_bars_per_symbol: int,
) -> dict[str, Any]:
    """Audit one canonical minute partition and return a manifest-ready report.

    The function is an orchestrator: it delegates the real checks to dedicated
    issue functions (_frame_issues, _session_semantic_issues, _partial_shape_issues,
    _source_reconciliation_issues, _overlay_frame_disposition) and here only assembles
    the report in stages (base -> actual stats -> issues -> final payload).
    """
    # Stage 1: read file metadata and assemble the base report header.
    parquet_file = pq.ParquetFile(path)
    schema_match = parquet_file.schema_arrow.equals(CANONICAL_MINUTE_SCHEMA)
    file_stat = path.stat()
    base: dict[str, Any] = {
        "date": expectation.trade_date,
        "tier": expectation.tier,
        "canonical_source": expectation.canonical_source,
        "market_scope": expectation.market_scope,
        "audit_only_sources": list(expectation.audit_only_sources),
        "overlay_sources": list(expectation.overlay_sources),
        "path": str(path),
        "file_size": file_stat.st_size,
        "file_mtime_ns": file_stat.st_mtime_ns,
        "content_sha256": _sha256_file(path),
        "schema_match": schema_match,
    }
    if not schema_match:
        base.update(
            {
                "rows": int(parquet_file.metadata.num_rows),
                "symbols": None,
                "traded_symbols": None,
                "issues": {"schema_mismatch": 1},
                "accepted_diagnostics": {},
                "fatal_issues": {"schema_mismatch": 1},
                "valid": False,
            }
        )
        return base

    # Stage 2: schema mismatch is fatal; report it immediately without reading rows.
    # Stage 3: read the frame and compute actual coverage/symbol statistics.
    frame = parquet_file.read().to_pandas()
    numeric = frame.loc[:, list(CANONICAL_MINUTE_COLUMNS[2:])]
    codes = frame["ts_code"].astype("string")
    traded = frame["vol"].gt(0)
    actual_time_min = str(frame["trade_time"].min()) if not frame.empty else None
    actual_time_max = str(frame["trade_time"].max()) if not frame.empty else None
    actual = {
        "rows": len(frame),
        "symbols": int(frame["ts_code"].nunique()),
        "traded_symbols": int(frame.loc[traded, "ts_code"].nunique()),
        "sh_sz_symbols": int(frame.loc[codes.str.endswith((".SH", ".SZ")), "ts_code"].nunique()),
        "sh_sz_traded_symbols": int(
            frame.loc[traded & codes.str.endswith((".SH", ".SZ")), "ts_code"].nunique()
        ),
        "bj_symbols": int(frame.loc[codes.str.endswith(".BJ"), "ts_code"].nunique()),
        "bj_traded_symbols": int(
            frame.loc[traded & codes.str.endswith(".BJ"), "ts_code"].nunique()
        ),
        "vol_sum": float(numeric["vol"].sum()),
        "amount_sum": float(numeric["amount"].sum()),
        "time_min": actual_time_min,
        "time_max": actual_time_max,
    }
    # Stage 4: collect issues from the dedicated check functions and overlay disposition.
    frame_issues = _frame_issues(
        expectation.trade_date,
        frame,
        sh_sz_only=expectation.market_scope != "SH_SZ_BJ",
    )
    issues = dict(frame_issues)
    issues.update(
        _session_semantic_issues(
            expectation,
            actual_time_min=actual_time_min,
            actual_time_max=actual_time_max,
        )
    )
    if expectation.tier == TUSHARE_PARTIAL_TOP200:
        issues.update(
            _partial_shape_issues(
                frame,
                expected_symbols=int(expectation.expected_symbols or 0),
                expected_bars_per_symbol=partial_bars_per_symbol,
            )
        )
    if expectation.tier == TUSHARE_FULL_A_SHARE and expected_stats is not None:
        issues.update(
            _partial_shape_issues(
                frame,
                expected_symbols=expected_stats.symbols,
                expected_bars_per_symbol=_TUSHARE_FULL_BARS_PER_SYMBOL,
            )
        )
    issues.update(_source_reconciliation_issues(actual, expected_stats))
    if expectation.tier == MISSING:
        issues["unexpected_partition_for_missing_source"] = 1
    unit_profile = expected_stats.unit_profile if expected_stats is not None else None
    if expectation.overlay_sources:
        accepted_diagnostics, fatal_issues, accepted_diagnostics_by_source = (
            _overlay_frame_disposition(
                frame,
                expectation,
                unit_profile=unit_profile,
            )
        )
        non_frame_issues = {
            name: count for name, count in issues.items() if name not in frame_issues
        }
        fatal_issues = _sum_issue_maps(
            fatal_issues,
            {name: int(count) for name, count in non_frame_issues.items() if count},
        )
    else:
        accepted_diagnostics = _accepted_diagnostics(
            issues,
            canonical_source=expectation.canonical_source,
            unit_profile=unit_profile,
        )
        fatal_issues = _fatal_issues(
            issues,
            canonical_source=expectation.canonical_source,
            unit_profile=unit_profile,
        )
        accepted_diagnostics_by_source = {
            "guan": (
                accepted_diagnostics if expectation.canonical_source == "guan_annual_minbar" else {}
            ),
            "tushare": (
                accepted_diagnostics
                if expectation.canonical_source in TUSHARE_VWAP_DIAGNOSTIC_SOURCES
                else {}
            ),
        }

    # Stage 5: attach the market-reference delta (if any) and assemble the final report.
    reference_payload: dict[str, Any] | None = None
    if market_reference is not None:
        reference_payload = {
            **asdict(market_reference),
            "sh_sz_traded_symbol_delta": actual["sh_sz_traded_symbols"]
            - market_reference.sh_sz_symbols,
            "sh_sz_traded_symbol_ratio": (
                actual["sh_sz_traded_symbols"] / market_reference.sh_sz_symbols
                if market_reference.sh_sz_symbols
                else None
            ),
            "bj_traded_symbol_delta": actual["bj_traded_symbols"] - market_reference.bj_symbols,
        }
    base.update(
        {
            **actual,
            "expected_source_stats": asdict(expected_stats) if expected_stats is not None else None,
            "market_reference": reference_payload,
            "issues": issues,
            "accepted_diagnostics": accepted_diagnostics,
            "accepted_diagnostics_by_source": accepted_diagnostics_by_source,
            "fatal_issues": fatal_issues,
            "valid": not fatal_issues,
        }
    )
    return base


def _requirement_mismatches(
    tier_counts: Mapping[str, int],
    *,
    calendar_count: int,
    accepted_zero_volume_nonzero_amount_rows: int,
    accepted_positive_volume_zero_amount_rows: int,
    requirements: CoverageRequirements,
) -> list[dict[str, Any]]:
    checks = (
        ("trade_dates", calendar_count, requirements.expected_trade_dates),
        (
            ANNUAL_FULL_SH_SZ,
            tier_counts.get(ANNUAL_FULL_SH_SZ, 0),
            requirements.expected_annual_full_sh_sz_dates,
        ),
        (
            DEAL_FULL_SH_SZ,
            tier_counts.get(DEAL_FULL_SH_SZ, 0),
            requirements.expected_deal_full_sh_sz_dates,
        ),
        (
            TUSHARE_FULL_A_SHARE,
            tier_counts.get(TUSHARE_FULL_A_SHARE, 0),
            requirements.expected_tushare_full_a_share_dates,
        ),
        (
            GUAN_PARTIAL_SESSION,
            tier_counts.get(GUAN_PARTIAL_SESSION, 0),
            requirements.expected_guan_partial_session_dates,
        ),
        (
            TUSHARE_PARTIAL_TOP200,
            tier_counts.get(TUSHARE_PARTIAL_TOP200, 0),
            requirements.expected_partial_top200_dates,
        ),
        (
            ZERO_VOLUME_NONZERO_AMOUNT_ISSUE,
            accepted_zero_volume_nonzero_amount_rows,
            requirements.expected_accepted_zero_volume_nonzero_amount_rows,
        ),
        (
            POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
            accepted_positive_volume_zero_amount_rows,
            requirements.expected_accepted_positive_volume_zero_amount_rows,
        ),
    )
    return [
        {"check": name, "actual": actual, "expected": expected}
        for name, actual, expected in checks
        if expected is not None and actual != expected
    ]


def audit_minute_coverage(  # noqa: PLR0913
    output_dir: str | Path,
    *,
    trade_dates: Iterable[str],
    annual_dates: Iterable[str],
    deal_dates: Iterable[str],
    tushare_dates: Iterable[str],
    partial_session_annual_dates: Iterable[str] = (),
    deal_override_dates: Iterable[str] = (),
    tushare_full_dates: Iterable[str] = (),
    expected_partition_stats: Mapping[str, ExpectedPartitionStats | Mapping[str, Any]]
    | None = None,
    market_reference: Mapping[str, MarketReferenceStats | Mapping[str, Any]] | None = None,
    requirements: CoverageRequirements = PRODUCTION_COVERAGE_REQUIREMENTS,
    partial_symbols: int = 200,
    partial_bars_per_symbol: int = 241,
    audit_workers: int = 1,
    input_lineage: Mapping[str, Any] | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Audit canonical partitions against the trading calendar and source plan."""
    from market_data_platform.providers._a_share_minute_coverage_audit import (
        _audit_minute_coverage,
        _CoverageAuditBindings,
        _CoverageAuditRequest,
    )

    return _audit_minute_coverage(
        _CoverageAuditRequest(
            bindings=_CoverageAuditBindings(
                annual_full_sh_sz=ANNUAL_FULL_SH_SZ,
                deal_full_sh_sz=DEAL_FULL_SH_SZ,
                guan_partial_session=GUAN_PARTIAL_SESSION,
                missing=MISSING,
                tushare_full_a_share=TUSHARE_FULL_A_SHARE,
                tushare_partial_top200=TUSHARE_PARTIAL_TOP200,
                classification_request=_CoverageClassificationRequest,
                classify=_classify_minute_coverage,
                apply_bj_overlay=apply_bj_overlay_coverage,
                date_set=_date_set,
                discover_output_layout=_discover_output_layout,
                validate_date=_validate_date,
                coerce_expected_stats=_coerce_expected_stats,
                coerce_market_reference=_coerce_market_reference,
                audit_partition=_audit_partition,
                requirement_mismatches=_requirement_mismatches,
                atomic_write_json=_atomic_write_json,
            ),
            output_dir=output_dir,
            trade_dates=trade_dates,
            annual_dates=annual_dates,
            deal_dates=deal_dates,
            tushare_dates=tushare_dates,
            partial_session_annual_dates=partial_session_annual_dates,
            deal_override_dates=deal_override_dates,
            tushare_full_dates=tushare_full_dates,
            expected_partition_stats=expected_partition_stats,
            market_reference=market_reference,
            requirements=requirements,
            partial_symbols=partial_symbols,
            partial_bars_per_symbol=partial_bars_per_symbol,
            audit_workers=audit_workers,
            input_lineage=input_lineage,
            manifest_path=manifest_path,
        )
    )
