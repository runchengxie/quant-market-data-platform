"""Normalized A-share research assets derived from licensed local extracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_research_part01 import (
    INDUSTRY_COLUMNS,
    PIT_METADATA_COLUMNS,
    IndustryChangesColumnMap,
    _date_token,
    _load_asset_frames,
    _normalize_symbol_frame,
    _read_frame,
    _validation_result,
)
from market_data_platform.providers.tushare_common import (
    pandas,
    write_frame,
    write_manifest,
)


def validate_a_share_pit_fundamentals(
    *,
    asset_dir: str | Path,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, object]:
    pd = pandas()
    frames = _load_asset_frames(asset_dir)
    frame = pd.concat(frames, ignore_index=True)
    required = set(PIT_METADATA_COLUMNS)
    missing = sorted(required - set(frame.columns))
    rows = int(len(frame))
    symbols = int(frame["symbol"].nunique()) if "symbol" in frame else 0
    checks = [
        {"id": "required_columns", "passed": not missing, "missing": missing},
        {"id": "min_rows", "passed": rows >= min_rows, "actual": rows, "expected": min_rows},
        {
            "id": "min_symbols",
            "passed": symbols >= min_symbols,
            "actual": symbols,
            "expected": min_symbols,
        },
    ]
    if not missing:
        disclosure = pd.to_datetime(
            frame["disclosure_date"].map(_date_token),
            format="%Y%m%d",
            errors="coerce",
        )
        available = pd.to_datetime(
            frame["available_date"].map(_date_token),
            format="%Y%m%d",
            errors="coerce",
        )
        report_period = pd.to_datetime(
            frame["report_period"].map(_date_token),
            format="%Y%m%d",
            errors="coerce",
        )
        duplicate_count = int(
            frame.duplicated(subset=["symbol", "report_period", "available_date"]).sum()
        )
        checks.extend(
            [
                {
                    "id": "available_not_before_disclosure",
                    "passed": bool((available >= disclosure).all()),
                },
                {
                    "id": "disclosure_not_before_report_period",
                    "passed": bool((disclosure >= report_period).all()),
                },
                {
                    "id": "unique_symbol_report_available",
                    "passed": duplicate_count == 0,
                    "duplicates": duplicate_count,
                },
            ]
        )
    return _validation_result(
        checks,
        totals={"rows": rows, "symbols": symbols, "files": len(frames)},
    )


def build_a_share_industry_changes(  # noqa: PLR0913
    *,
    source_file: str | Path,
    out_dir: str | Path,
    columns: IndustryChangesColumnMap | None = None,
    industry_system: str = "sw",
    provider: str = "licensed_extract",
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, Any]:
    columns = columns or IndustryChangesColumnMap()
    source = Path(source_file).expanduser().resolve()
    output_dir = Path(out_dir).expanduser().resolve()
    df = _normalize_symbol_frame(_read_frame(source))
    if columns.effective_date not in df.columns:
        raise ValueError(f"Missing effective date column: {columns.effective_date}")
    if columns.industry_code not in df.columns and columns.industry_name not in df.columns:
        raise ValueError("Source file must contain an industry code or industry name column.")
    df["effective_date"] = df[columns.effective_date].map(_date_token)
    df["end_date"] = df[columns.end_date].map(_date_token) if columns.end_date in df.columns else ""
    df["industry_code"] = (
        df[columns.industry_code].astype(str) if columns.industry_code in df.columns else ""
    )
    df["industry_name"] = (
        df[columns.industry_name].astype(str) if columns.industry_name in df.columns else ""
    )
    df["industry_system"] = str(industry_system)
    output = df.loc[:, list(INDUSTRY_COLUMNS)].copy()
    output = output[
        (output["effective_date"] != "")
        & ((output["industry_code"].str.len() > 0) | (output["industry_name"].str.len() > 0))
    ].copy()
    output = output.drop_duplicates(
        subset=["symbol", "effective_date", "industry_system", "industry_code", "industry_name"],
        keep="last",
    ).sort_values(["effective_date", "symbol", "industry_system"])
    totals = {
        "rows": int(len(output)),
        "symbols": int(output["symbol"].nunique()) if "symbol" in output else 0,
        "files": 1,
    }
    if totals["rows"] < min_rows or totals["symbols"] < min_symbols:
        raise ValueError(f"Industry changes asset is too small: {totals}")

    write_frame(output, output_dir / "data" / "part.parquet")
    effective_end = output["end_date"].replace("", None).dropna()
    manifest = {
        "schema_version": "licensed.a_share.industry_changes.v1",
        "dataset": "industry_changes",
        "market": "a_share",
        "provider": provider,
        "status": "completed",
        "output_dir": str(output_dir),
        "source_file": str(source),
        "query": {
            "start_date": str(output["effective_date"].min()),
            "end_date": str(
                effective_end.max() if not effective_end.empty else output["effective_date"].max()
            ),
        },
        "semantics": {
            "historical_membership": True,
            "effective_date_column": "effective_date",
            "end_date_column": "end_date",
            "industry_system": str(industry_system),
        },
        "totals": totals,
    }
    write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def validate_a_share_industry_changes(
    *,
    asset_dir: str | Path,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, object]:
    pd = pandas()
    frames = _load_asset_frames(asset_dir)
    frame = pd.concat(frames, ignore_index=True)
    required = set(INDUSTRY_COLUMNS)
    missing = sorted(required - set(frame.columns))
    rows = int(len(frame))
    symbols = int(frame["symbol"].nunique()) if "symbol" in frame else 0
    checks = [
        {"id": "required_columns", "passed": not missing, "missing": missing},
        {"id": "min_rows", "passed": rows >= min_rows, "actual": rows, "expected": min_rows},
        {
            "id": "min_symbols",
            "passed": symbols >= min_symbols,
            "actual": symbols,
            "expected": min_symbols,
        },
    ]
    if not missing:
        effective = pd.to_datetime(
            frame["effective_date"].map(_date_token),
            format="%Y%m%d",
            errors="coerce",
        )
        end_tokens = frame["end_date"].map(_date_token)
        ended = end_tokens != ""
        end = (
            pd.to_datetime(end_tokens[ended], format="%Y%m%d", errors="coerce")
            if ended.any()
            else None
        )
        duplicate_count = int(
            frame.duplicated(
                subset=[
                    "symbol",
                    "effective_date",
                    "industry_system",
                    "industry_code",
                    "industry_name",
                ]
            ).sum()
        )
        checks.append(
            {
                "id": "end_not_before_effective",
                "passed": True if end is None else bool((end >= effective[ended]).all()),
            }
        )
        checks.append(
            {
                "id": "unique_symbol_effective_industry",
                "passed": duplicate_count == 0,
                "duplicates": duplicate_count,
            }
        )
    return _validation_result(
        checks,
        totals={"rows": rows, "symbols": symbols, "files": len(frames)},
    )
