"""Normalized A-share research assets derived from licensed local extracts."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_common import (
    configure_tushare_client_api_url,
    get_tushare_client,
    normalize_ts_code,
    pandas,
    resolve_tushare_api_url,
    validate_date,
    write_frame,
    write_manifest,
)

PIT_METADATA_COLUMNS = (
    "symbol",
    "trade_date",
    "report_period",
    "disclosure_date",
    "available_date",
)

INDUSTRY_COLUMNS = (
    "symbol",
    "effective_date",
    "end_date",
    "industry_system",
    "industry_code",
    "industry_name",
)


@dataclass(frozen=True)
class IndustryChangesColumnMap:
    effective_date: str = "start_date"
    end_date: str = "end_date"
    industry_code: str = "industry_code"
    industry_name: str = "industry_name"


@dataclass
class _IndustryMembershipDownloadStats:
    query_count: int = 0
    empty_queries: int = 0
    skipped_existing_parts: int = 0
    skipped_existing_empty: int = 0


@dataclass(frozen=True)
class _IndustryMembershipDownloadRequest:
    pro: Any
    classify: Any
    output_dir: Path
    src: str
    level: str
    flags: Sequence[str]
    request_interval_seconds: float


@dataclass(frozen=True)
class _IndustryMembershipManifestRequest:
    output_dir: Path
    data_path: Path
    src: str
    level: str
    flags: Sequence[str]
    api_url: str | None
    stats: _IndustryMembershipDownloadStats
    totals: Mapping[str, int]


def _safe_path_token(value: object) -> str:
    text = str(value or "").strip()
    return "".join(char if char.isalnum() else "_" for char in text).strip("_") or "unknown"


def _read_frame(path: str | Path) -> Any:
    pd = pandas()
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source file does not exist: {source}")
    suffix = source.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(source)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(source)
    raise ValueError(f"Unsupported source file type: {source}")


def _date_token(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return ""
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) >= 8:
        return validate_date(digits[:8])
    return ""


def _normalize_symbol_frame(frame: Any) -> Any:
    df = frame.copy()
    if "symbol" not in df.columns and "ts_code" in df.columns:
        df["symbol"] = df["ts_code"]
    if "symbol" not in df.columns:
        raise ValueError("Source file must contain symbol or ts_code.")
    df["symbol"] = df["symbol"].map(normalize_ts_code)
    return df[df["symbol"].astype(str).str.len() > 0].copy()


def _field_maps(rows: Iterable[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in rows or ():
        text = str(raw or "").strip()
        if not text:
            continue
        if "=" not in text:
            raise ValueError(f"Expected field mapping as source=target, got: {text}")
        source, target = (part.strip() for part in text.split("=", 1))
        if not source or not target:
            raise ValueError(f"Invalid field mapping: {text}")
        result[source] = target
    return result


def _load_asset_frames(asset_dir: str | Path) -> list[Any]:
    root = Path(asset_dir).expanduser().resolve()
    data_dir = root / "data"
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Asset data directory does not exist: {data_dir}")
    files = sorted([*data_dir.glob("*.parquet"), *data_dir.glob("*.csv")])
    if not files:
        raise FileNotFoundError(f"Asset data directory contains no CSV/Parquet files: {data_dir}")
    return [_read_frame(path) for path in files]


def _validation_result(
    checks: list[dict[str, Any]],
    *,
    totals: Mapping[str, object],
) -> dict[str, object]:
    failed = [check for check in checks if not bool(check.get("passed"))]
    return {
        "status": "passed" if not failed else "failed",
        "checks": checks,
        "totals": dict(totals),
    }


def _normalize_industry_level(level: str) -> str:
    normalized = str(level or "").strip().upper()
    if normalized not in {"L1", "L2", "L3"}:
        raise ValueError("level must be one of L1/L2/L3.")
    return normalized


def _normalize_industry_is_new_flags(flags: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(str(flag or "").strip().upper() for flag in flags))
    if not normalized or any(flag not in {"Y", "N"} for flag in normalized):
        raise ValueError("is_new_flags must contain Y and/or N.")
    return normalized


def _industry_membership_client(
    *,
    token_env: str,
    api_url: str | None,
    client: Any | None,
) -> tuple[Any, str | None]:
    resolved_api_url = resolve_tushare_api_url(api_url, token_env=token_env)
    pro = client or get_tushare_client(token_env=token_env, api_url=resolved_api_url)
    configure_tushare_client_api_url(pro, resolved_api_url)
    return pro, resolved_api_url


def _load_industry_classifications(pro: Any, *, src: str, level: str) -> Any:
    pd = pandas()
    classify = pd.DataFrame(pro.index_classify(level=level, src=src)).copy()
    if classify.empty or "index_code" not in classify.columns:
        raise ValueError(
            f"TuShare index_classify returned no {level} classifications for src={src}."
        )
    return classify.dropna(subset=["index_code"]).drop_duplicates(subset=["index_code"])


def _industry_member_part_path(part_dir: Path, *, level: str, code: str, flag: str) -> Path:
    return part_dir / f"{level.lower()}={_safe_path_token(code)}" / f"is_new={flag}.parquet"


def _download_industry_membership_parts(
    request: _IndustryMembershipDownloadRequest,
) -> tuple[list[Any], _IndustryMembershipDownloadStats]:
    pd = pandas()
    stats = _IndustryMembershipDownloadStats()
    frames: list[Any] = []
    code_column = f"{request.level.lower()}_code"
    part_dir = request.output_dir / "parts"
    for raw_code in request.classify["index_code"].astype(str).sort_values():
        code = raw_code.strip()
        if not code:
            continue
        for flag in request.flags:
            stats.query_count += 1
            part_path = _industry_member_part_path(
                part_dir,
                level=request.level,
                code=code,
                flag=flag,
            )
            empty_marker = part_path.with_suffix(".empty")
            if part_path.is_file():
                frames.append(_read_frame(part_path))
                stats.skipped_existing_parts += 1
                continue
            if empty_marker.is_file():
                stats.empty_queries += 1
                stats.skipped_existing_empty += 1
                continue
            frame = pd.DataFrame(
                request.pro.index_member_all(**{code_column: code, "is_new": flag})
            ).copy()
            if frame.empty:
                stats.empty_queries += 1
                empty_marker.parent.mkdir(parents=True, exist_ok=True)
                empty_marker.write_text("empty\n", encoding="utf-8")
            else:
                frame["_query_level"] = request.level
                frame["_query_src"] = str(request.src)
                frame["_query_code"] = code
                frame["_query_is_new"] = flag
                frames.append(frame)
                write_frame(frame, part_path)
            if request.request_interval_seconds:
                time.sleep(request.request_interval_seconds)
    return frames, stats


def _industry_membership_output(frames: list[Any]) -> Any:
    pd = pandas()
    if not frames:
        raise ValueError(
            "TuShare index_member_all returned no rows for all requested classifications."
        )
    output = pd.concat(frames, ignore_index=True)
    if "ts_code" in output.columns:
        output["symbol"] = output["ts_code"].map(normalize_ts_code)
    dedupe_columns = [
        column
        for column in (
            "l1_code",
            "l2_code",
            "l3_code",
            "ts_code",
            "in_date",
            "out_date",
            "is_new",
        )
        if column in output.columns
    ]
    if dedupe_columns:
        output = output.drop_duplicates(subset=dedupe_columns, keep="last")
    sort_columns = [
        column
        for column in ("l1_code", "l2_code", "l3_code", "ts_code", "in_date", "out_date")
        if column in output.columns
    ]
    return output.sort_values(sort_columns).reset_index(drop=True)


def _industry_membership_totals(
    output: Any,
    classify: Any,
    frames: Sequence[Any],
) -> dict[str, int]:
    return {
        "rows": int(len(output)),
        "symbols": int(output["symbol"].nunique()) if "symbol" in output else 0,
        "files": 1,
        "classification_rows": int(len(classify)),
        "part_files": int(len(frames)),
    }


def _industry_membership_manifest(request: _IndustryMembershipManifestRequest) -> dict[str, Any]:
    return {
        "schema_version": "tushare.a_share.industry_membership_source.v1",
        "dataset": "industry_membership_source",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(request.output_dir),
        "source_file": str(request.data_path),
        "generated_at": datetime.now(UTC).isoformat(),
        "api_url": request.api_url,
        "query": {
            "classification_api": "index_classify",
            "membership_api": "index_member_all",
            "src": str(request.src),
            "level": request.level,
            "is_new_flags": list(request.flags),
            "queries": request.stats.query_count,
            "empty_queries": request.stats.empty_queries,
            "skipped_existing_parts": request.stats.skipped_existing_parts,
            "skipped_existing_empty": request.stats.skipped_existing_empty,
        },
        "semantics": {
            "historical_membership_source": True,
            "effective_date_column": "in_date",
            "end_date_column": "out_date",
            "industry_system": f"{str(request.src).lower()}_{request.level.lower()}",
        },
        "totals": dict(request.totals),
    }


def download_a_share_industry_membership(  # noqa: PLR0913
    *,
    out_dir: str | Path,
    src: str = "SW2021",
    level: str = "L3",
    is_new_flags: Sequence[str] = ("Y", "N"),
    token_env: str = "TUSHARE_TOKEN",
    api_url: str | None = None,
    request_interval_seconds: float = 0.1,
    client: Any | None = None,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, Any]:
    """Download TuShare Shenwan industry membership into a local source asset."""
    output_dir = Path(out_dir).expanduser().resolve()
    normalized_level = _normalize_industry_level(level)
    flags = _normalize_industry_is_new_flags(is_new_flags)
    if request_interval_seconds < 0:
        raise ValueError("request_interval_seconds must be non-negative.")

    pro, resolved_api_url = _industry_membership_client(
        token_env=token_env,
        api_url=api_url,
        client=client,
    )
    classify = _load_industry_classifications(pro, src=src, level=normalized_level)
    query_frames, stats = _download_industry_membership_parts(
        _IndustryMembershipDownloadRequest(
            pro=pro,
            classify=classify,
            output_dir=output_dir,
            src=src,
            level=normalized_level,
            flags=flags,
            request_interval_seconds=request_interval_seconds,
        )
    )
    output = _industry_membership_output(query_frames)
    totals = _industry_membership_totals(output, classify, query_frames)
    if totals["rows"] < min_rows or totals["symbols"] < min_symbols:
        raise ValueError(f"Industry membership source asset is too small: {totals}")

    data_path = output_dir / "data" / "part.parquet"
    write_frame(classify, output_dir / "classifications.parquet")
    write_frame(output, data_path)
    manifest = _industry_membership_manifest(
        _IndustryMembershipManifestRequest(
            output_dir=output_dir,
            data_path=data_path,
            src=src,
            level=normalized_level,
            flags=flags,
            api_url=resolved_api_url,
            stats=stats,
            totals=totals,
        )
    )
    write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def build_a_share_pit_fundamentals(  # noqa: PLR0913
    *,
    source_file: str | Path,
    out_dir: str | Path,
    report_period_col: str = "end_date",
    disclosure_date_col: str = "ann_date",
    available_delay_days: int = 1,
    field_maps: Iterable[str] | None = None,
    provider: str = "tushare",
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, Any]:
    pd = pandas()
    source = Path(source_file).expanduser().resolve()
    output_dir = Path(out_dir).expanduser().resolve()
    df = _normalize_symbol_frame(_read_frame(source))
    if report_period_col not in df.columns:
        raise ValueError(f"Missing report period column: {report_period_col}")
    if disclosure_date_col not in df.columns:
        raise ValueError(f"Missing disclosure date column: {disclosure_date_col}")

    mappings = _field_maps(field_maps)
    df["report_period"] = df[report_period_col].map(_date_token)
    df["disclosure_date"] = df[disclosure_date_col].map(_date_token)
    df = df[(df["report_period"] != "") & (df["disclosure_date"] != "")].copy()
    if available_delay_days < 0:
        raise ValueError("available_delay_days must be non-negative.")
    disclosure_dates = pd.to_datetime(df["disclosure_date"], format="%Y%m%d")
    df["available_date"] = (
        disclosure_dates + timedelta(days=int(available_delay_days))
    ).dt.strftime("%Y%m%d")
    df["trade_date"] = df["available_date"]

    excluded = set(PIT_METADATA_COLUMNS) | {report_period_col, disclosure_date_col, "ts_code"}
    source_value_columns = [column for column in df.columns if column not in excluded]
    rename_map = {
        source_col: target for source_col, target in mappings.items() if source_col in df.columns
    }
    if rename_map:
        df = df.rename(columns=rename_map)
    value_columns = [rename_map.get(column, column) for column in source_value_columns]
    field_mapping = {column: rename_map.get(column, column) for column in source_value_columns}
    output_columns = [*PIT_METADATA_COLUMNS, *dict.fromkeys(value_columns)]
    output = df.loc[:, [column for column in output_columns if column in df.columns]].copy()
    output = output.drop_duplicates(
        subset=["symbol", "report_period", "available_date"],
        keep="last",
    ).sort_values(["available_date", "symbol", "report_period"])

    totals = {
        "rows": int(len(output)),
        "symbols": int(output["symbol"].nunique()) if "symbol" in output else 0,
        "files": 1,
    }
    if totals["rows"] < min_rows or totals["symbols"] < min_symbols:
        raise ValueError(f"PIT fundamentals asset is too small: {totals}")

    write_frame(output, output_dir / "data" / "part.parquet")
    manifest = {
        "schema_version": "tushare.a_share.pit_fundamentals.v1",
        "dataset": "pit_fundamentals",
        "market": "a_share",
        "provider": provider,
        "status": "completed",
        "output_dir": str(output_dir),
        "source_file": str(source),
        "query": {
            "start_date": str(output["available_date"].min()),
            "end_date": str(output["available_date"].max()),
        },
        "semantics": {
            "point_in_time": True,
            "report_period_column": "report_period",
            "disclosure_date_column": "disclosure_date",
            "available_date_column": "available_date",
            "availability_delay_days": int(available_delay_days),
            "field_mappings": field_mapping,
        },
        "totals": totals,
    }
    write_manifest(output_dir / "manifest.yml", manifest)
    return manifest
