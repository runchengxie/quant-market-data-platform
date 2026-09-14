"""Business adapters (mirror/export functions) for the TuShare A-share provider.

This module also hosts the A-group request-policy helpers and ``_tushare_runtime``
so that the F-group mirrors can reference them without creating import cycles.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers._adapters_part01 import (
    _coerce_options,
    _existing_dc_completeness_by_date,
    _FundPortfolioPeriodRequest,
    _mirror_trade_date_partitions,
    _open_trade_dates,
    _request_policy_payload,
    _trade_date_field_context,
    _tushare_runtime,
)
from market_data_platform.providers._client import (
    _call_tushare_api,
    _pandas,
)
from market_data_platform.providers._frame import (
    _normalize_ts_code,
    _prepare_fund_portfolio_frame,
    _prepare_stk_holdertrade_frame,
    _prepare_top10_holder_frame,
)
from market_data_platform.providers._io import (
    _fields_text,
    _prepare_output_dir,
    _write_frame,
    _write_manifest,
    _write_manifest_atomically,
)
from market_data_platform.providers.tushare_a_share_dates import (
    _quarter_periods,
    _validate_date,
)
from market_data_platform.providers.tushare_a_share_manifests import (
    fund_portfolio_manifest,
    trade_date_manifest,
)
from market_data_platform.providers.tushare_a_share_options import (
    DEFAULT_FUND_PORTFOLIO_FIELDS,
    DEFAULT_REPORT_RC_FIELDS,
    DEFAULT_STK_HOLDERTRADE_FIELDS,
    DEFAULT_STK_SURV_FIELDS,
    DEFAULT_TOP10_HOLDER_FIELDS,
    TRADE_DATE_APIS,
    TRADE_DATE_DEFAULT_REQUEST_OPTIONS,
    FundPortfolioMirrorOptions,
    StkHoldertradeMirrorOptions,
    Top10HolderMirrorOptions,
    TradeDateMirrorOptions,
)


def _fetch_fund_portfolio_period_frame(request: _FundPortfolioPeriodRequest) -> tuple[Any, int]:
    period_frames: list[Any] = []
    for page in range(request.max_pages_per_period):
        api_kwargs: dict[str, Any] = {
            "period": request.period,
            "limit": request.page_size,
            "offset": page * request.page_size,
        }
        if request.fields_text is not None:
            api_kwargs["fields"] = request.fields_text
        page_df = _prepare_fund_portfolio_frame(
            _call_tushare_api(
                lambda call_kwargs=dict(api_kwargs): request.client.fund_portfolio(**call_kwargs),
                policy=request.policy,
            )
        )
        if page_df.empty:
            break
        period_frames.append(page_df)
        if len(page_df) < request.page_size:
            break
    if not period_frames:
        return _pandas().DataFrame(), 0
    if (
        len(period_frames) >= request.max_pages_per_period
        and len(period_frames[-1]) == request.page_size
    ):
        raise RuntimeError(
            f"fund_portfolio period {request.period} reached max_pages_per_period="
            f"{request.max_pages_per_period}; increase the limit to avoid truncation."
        )
    return _pandas().concat(period_frames, ignore_index=True), len(period_frames)


def mirror_a_share_trade_date_dataset(
    options: TradeDateMirrorOptions | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    options = _coerce_options(options, TradeDateMirrorOptions, kwargs)
    dataset = options.dataset
    if dataset not in TRADE_DATE_APIS:
        raise ValueError(f"Unsupported TuShare A-share trade-date dataset: {dataset}")
    start = _validate_date(options.start_date)
    end = _validate_date(options.end_date)
    pro, policy, resolved_api_url = _tushare_runtime(
        token_env=options.token_env,
        api_url=options.api_url,
        request_policy=options.request_policy,
        request_options=options.request_options,
    )
    output_dir = _prepare_output_dir(
        Path(options.out_dir),
        allow_existing=options.skip_existing or dataset == "dc_concept_cons",
    )
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    requested_fields, fields_text = _trade_date_field_context(dataset, options.fields)
    default_query_options = TRADE_DATE_DEFAULT_REQUEST_OPTIONS.get(dataset, {})
    manifest_path = output_dir / "manifest.yml"
    existing_dc_completeness_by_date = (
        _existing_dc_completeness_by_date(manifest_path) if dataset == "dc_concept_cons" else {}
    )
    context = {
        "api_name": TRADE_DATE_APIS[dataset],
        "api_url": resolved_api_url,
        "data_dir": data_dir,
        "dataset": dataset,
        "end": end,
        "fields_text": fields_text,
        "interval_seconds": max(0.0, float(options.request_interval_seconds)),
        "market": str(options.market),
        "output_dir": output_dir,
        "policy": policy,
        "query_options": {**default_query_options, **options.query_options},
        "requested_fields": requested_fields,
        "skip_existing": options.skip_existing,
        "start": start,
        "trade_dates": _open_trade_dates(pro, start_date=start, end_date=end, policy=policy),
        "existing_dc_completeness_by_date": existing_dc_completeness_by_date,
    }
    manifest = trade_date_manifest(
        context,
        _mirror_trade_date_partitions(pro, context),
        _request_policy_payload(policy),
    )
    if dataset == "dc_concept_cons":
        _write_manifest_atomically(manifest_path, manifest)
    else:
        _write_manifest(manifest_path, manifest)
    return manifest


def mirror_a_share_fund_portfolio(
    options: FundPortfolioMirrorOptions | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    options = _coerce_options(options, FundPortfolioMirrorOptions, kwargs)
    start = _validate_date(options.start_date)
    end = _validate_date(options.end_date)
    pro, policy, resolved_api_url = _tushare_runtime(
        token_env=options.token_env,
        api_url=options.api_url,
        request_policy=options.request_policy,
        request_options=options.request_options,
    )
    output_dir = _prepare_output_dir(Path(options.out_dir), allow_existing=options.skip_existing)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    requested_fields = tuple(
        DEFAULT_FUND_PORTFOLIO_FIELDS if options.fields is None else options.fields
    )
    fields_text = _fields_text(
        requested_fields,
        required=("ts_code", "ann_date", "end_date", "symbol"),
    )
    periods = _quarter_periods(start, end)
    page_size = max(1, int(options.page_size))
    max_pages_per_period = max(1, int(options.max_pages_per_period))
    retrieved_at = datetime.now(UTC).isoformat()
    vintage_id = retrieved_at.replace("+00:00", "Z").replace(":", "")

    rows = 0
    symbols: set[str] = set()
    funds: set[str] = set()
    written_periods: list[str] = []
    skipped_periods: list[str] = []
    empty_periods: list[str] = []
    pages_by_period: dict[str, int] = {}
    for period in periods:
        output_path = data_dir / f"end_date={period}" / "part.parquet"
        if options.skip_existing and output_path.exists():
            skipped_periods.append(period)
            continue
        df, pages = _fetch_fund_portfolio_period_frame(
            _FundPortfolioPeriodRequest(
                client=pro,
                policy=policy,
                period=period,
                page_size=page_size,
                max_pages_per_period=max_pages_per_period,
                fields_text=fields_text,
            )
        )
        pages_by_period[period] = pages
        if df.empty:
            empty_periods.append(period)
            continue
        _write_frame(df, output_path)
        written_periods.append(period)
        rows += int(len(df))
        if "symbol" in df.columns:
            symbols.update(df["symbol"].dropna().astype(str).tolist())
        if "ts_code" in df.columns:
            funds.update(df["ts_code"].dropna().astype(str).tolist())

    manifest = fund_portfolio_manifest(
        {
            "api_url": resolved_api_url,
            "end": end,
            "output_dir": output_dir,
            "retrieved_at": retrieved_at,
            "vintage_id": vintage_id,
            "page_size": page_size,
            "periods": periods,
            "requested_fields": requested_fields,
            "start": start,
        },
        {
            "empty_periods": empty_periods,
            "funds": funds,
            "pages_by_period": pages_by_period,
            "rows": rows,
            "skipped_periods": skipped_periods,
            "symbols": symbols,
            "written_periods": written_periods,
        },
        _request_policy_payload(policy),
    )
    _write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def mirror_a_share_top10_holder_dataset(
    options: Top10HolderMirrorOptions | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Mirror TuShare top-10 shareholder APIs by stock symbol."""
    options = _coerce_options(options, Top10HolderMirrorOptions, kwargs)
    start = _validate_date(options.start_date)
    end = _validate_date(options.end_date)
    requested_symbols = [_normalize_ts_code(symbol) for symbol in options.symbols]
    requested_symbols = [symbol for symbol in requested_symbols if symbol]
    if not requested_symbols:
        raise ValueError("At least one A 股 symbol is required for top10 holder mirrors.")
    pro, policy, resolved_api_url = _tushare_runtime(
        token_env=options.token_env,
        api_url=options.api_url,
        request_policy=options.request_policy,
        request_options=options.request_options,
    )
    output_dir = _prepare_output_dir(Path(options.out_dir), allow_existing=options.skip_existing)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    requested_fields = tuple(
        DEFAULT_TOP10_HOLDER_FIELDS if options.fields is None else options.fields
    )
    fields_text = _fields_text(
        requested_fields,
        required=("ts_code", "ann_date", "end_date", "holder_name"),
    )

    rows = 0
    interval_seconds = max(0.0, float(options.request_interval_seconds))
    symbols_written: set[str] = set()
    written_symbols: list[str] = []
    skipped_symbols: list[str] = []
    empty_symbols: list[str] = []
    for symbol in requested_symbols:
        output_path = data_dir / f"symbol={symbol}" / "part.parquet"
        if options.skip_existing and output_path.exists():
            skipped_symbols.append(symbol)
            continue
        api_kwargs: dict[str, Any] = {
            "ts_code": symbol,
            "start_date": start,
            "end_date": end,
        }
        if fields_text is not None:
            api_kwargs["fields"] = fields_text
        df = _prepare_top10_holder_frame(
            _call_tushare_api(
                lambda call_kwargs=dict(api_kwargs): getattr(pro, options.api_name)(**call_kwargs),
                policy=policy,
            )
        )
        if df.empty:
            empty_symbols.append(symbol)
            if interval_seconds > 0.0:
                time.sleep(interval_seconds)
            continue
        _write_frame(df, output_path)
        written_symbols.append(symbol)
        rows += int(len(df))
        if "symbol" in df.columns:
            symbols_written.update(df["symbol"].dropna().astype(str).tolist())
        if interval_seconds > 0.0:
            time.sleep(interval_seconds)

    manifest = {
        "schema_version": f"tushare.{options.api_name}.v1",
        "dataset": options.dataset,
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(output_dir),
        "query": {
            "api": options.api_name,
            "start_date": start,
            "end_date": end,
            "fields": list(requested_fields) if requested_fields else None,
            "partition_by": "symbol",
            "request_interval_seconds": interval_seconds,
        },
        "api_url": resolved_api_url,
        "request_policy": _request_policy_payload(policy),
        "totals": {
            "rows": rows,
            "symbols": len(symbols_written),
            "symbols_requested": len(requested_symbols),
            "symbols_written": len(written_symbols),
            "symbols_skipped": len(skipped_symbols),
            "symbols_empty": len(empty_symbols),
            "files": len(written_symbols),
        },
        "written_symbols": written_symbols,
        "skipped_symbols": skipped_symbols,
        "empty_symbols": empty_symbols,
    }
    _write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def mirror_a_share_top10_holders(**kwargs: Any) -> dict[str, Any]:
    return mirror_a_share_top10_holder_dataset(
        dataset="top10_holders",
        api_name="top10_holders",
        **kwargs,
    )


def mirror_a_share_top10_floatholders(**kwargs: Any) -> dict[str, Any]:
    return mirror_a_share_top10_holder_dataset(
        dataset="top10_floatholders",
        api_name="top10_floatholders",
        **kwargs,
    )


def mirror_a_share_stk_holdertrade(
    options: StkHoldertradeMirrorOptions | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Mirror TuShare shareholder increase/decrease events by stock symbol."""
    options = _coerce_options(options, StkHoldertradeMirrorOptions, kwargs)
    start = _validate_date(options.start_date)
    end = _validate_date(options.end_date)
    requested_symbols = [_normalize_ts_code(symbol) for symbol in options.symbols]
    requested_symbols = [symbol for symbol in requested_symbols if symbol]
    if not requested_symbols:
        raise ValueError("At least one A 股 symbol is required for stk_holdertrade mirror.")
    pro, policy, resolved_api_url = _tushare_runtime(
        token_env=options.token_env,
        api_url=options.api_url,
        request_policy=options.request_policy,
        request_options=options.request_options,
    )
    output_dir = _prepare_output_dir(Path(options.out_dir), allow_existing=options.skip_existing)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    requested_fields = tuple(
        DEFAULT_STK_HOLDERTRADE_FIELDS if options.fields is None else options.fields
    )
    fields_text = _fields_text(
        requested_fields,
        required=("ts_code", "ann_date", "holder_name", "in_de"),
    )

    rows = 0
    interval_seconds = max(0.0, float(options.request_interval_seconds))
    symbols_written: set[str] = set()
    written_symbols: list[str] = []
    skipped_symbols: list[str] = []
    empty_symbols: list[str] = []
    for symbol in requested_symbols:
        output_path = data_dir / f"symbol={symbol}" / "part.parquet"
        if options.skip_existing and output_path.exists():
            skipped_symbols.append(symbol)
            continue
        api_kwargs: dict[str, Any] = {
            "ts_code": symbol,
            "start_date": start,
            "end_date": end,
        }
        if fields_text is not None:
            api_kwargs["fields"] = fields_text
        df = _prepare_stk_holdertrade_frame(
            _call_tushare_api(
                lambda call_kwargs=dict(api_kwargs): pro.stk_holdertrade(**call_kwargs),
                policy=policy,
            )
        )
        if df.empty:
            empty_symbols.append(symbol)
            if interval_seconds > 0.0:
                time.sleep(interval_seconds)
            continue
        _write_frame(df, output_path)
        written_symbols.append(symbol)
        rows += int(len(df))
        if "symbol" in df.columns:
            symbols_written.update(df["symbol"].dropna().astype(str).tolist())
        if interval_seconds > 0.0:
            time.sleep(interval_seconds)

    manifest = {
        "schema_version": "tushare.stk_holdertrade.v1",
        "dataset": "stk_holdertrade",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(output_dir),
        "query": {
            "api": "stk_holdertrade",
            "start_date": start,
            "end_date": end,
            "fields": list(requested_fields) if requested_fields else None,
            "partition_by": "symbol",
            "request_interval_seconds": interval_seconds,
        },
        "api_url": resolved_api_url,
        "request_policy": _request_policy_payload(policy),
        "totals": {
            "rows": rows,
            "symbols": len(symbols_written),
            "symbols_requested": len(requested_symbols),
            "symbols_written": len(written_symbols),
            "symbols_skipped": len(skipped_symbols),
            "symbols_empty": len(empty_symbols),
            "files": len(written_symbols),
        },
        "written_symbols": written_symbols,
        "skipped_symbols": skipped_symbols,
        "empty_symbols": empty_symbols,
    }
    _write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


EVENT_DATE_DEFAULT_FIELDS = {
    "report_rc": DEFAULT_REPORT_RC_FIELDS,
    "stk_surv": DEFAULT_STK_SURV_FIELDS,
}

EVENT_DATE_REQUIRED_FIELDS = {
    "report_rc": ("ts_code", "report_date"),
    "stk_surv": ("ts_code", "surv_date"),
}
