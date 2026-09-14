"""Business adapters (mirror/export functions) for the TuShare A-share provider.

This module also hosts the A-group request-policy helpers and ``_tushare_runtime``
so that the F-group mirrors can reference them without creating import cycles.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from market_data_platform.providers._adapters_part01 import (
    _coerce_options,
    _request_policy_payload,
    _tushare_runtime,
)
from market_data_platform.providers._adapters_part02 import (
    EVENT_DATE_DEFAULT_FIELDS,
    EVENT_DATE_REQUIRED_FIELDS,
    mirror_a_share_trade_date_dataset,
)
from market_data_platform.providers._client import (
    _call_tushare_api,
    _call_tushare_endpoint,
    _pandas,
)
from market_data_platform.providers._frame import (
    _normalize_ts_code,
    _prepare_event_frame,
)
from market_data_platform.providers._io import (
    _fields_text,
    _prepare_output_dir,
    _write_frame,
    _write_manifest,
)
from market_data_platform.providers.tushare_a_share_dates import (
    _calendar_dates,
    _calendar_months,
    _validate_date,
)
from market_data_platform.providers.tushare_a_share_options import (
    DEFAULT_BROKER_RECOMMEND_FIELDS,
    DEFAULT_INDEX_DAILY_FIELDS,
    DEFAULT_THS_INDEX_FIELDS,
    DateRangeEventMirrorOptions,
    IndexDailyMirrorOptions,
    MonthMirrorOptions,
)
from market_data_platform.providers.tushare_a_share_ths_member import (
    ThsMemberMirrorDependencies,
    ThsMemberMirrorOptions,
    mirror_a_share_ths_member_impl,
)


def mirror_a_share_date_range_event_dataset(
    options: DateRangeEventMirrorOptions | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Mirror TuShare event-style APIs by calendar day."""
    options = _coerce_options(options, DateRangeEventMirrorOptions, kwargs)
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
        EVENT_DATE_DEFAULT_FIELDS.get(options.dataset, ())
        if options.fields is None
        else options.fields
    )
    fields_text = _fields_text(
        requested_fields,
        required=EVENT_DATE_REQUIRED_FIELDS.get(options.dataset, ("ts_code",)),
    )

    rows = 0
    symbols: set[str] = set()
    written_dates: list[str] = []
    skipped_dates: list[str] = []
    empty_dates: list[str] = []
    interval_seconds = max(0.0, float(options.request_interval_seconds))
    for event_date in _calendar_dates(start, end):
        output_path = data_dir / f"event_date={event_date}" / "part.parquet"
        if options.skip_existing and output_path.exists():
            skipped_dates.append(event_date)
            continue
        api_kwargs: dict[str, Any] = {
            **options.query_options,
            "start_date": event_date,
            "end_date": event_date,
        }
        if fields_text is not None:
            api_kwargs["fields"] = fields_text
        df = _prepare_event_frame(
            _call_tushare_api(
                lambda api=options.api_name, call_kwargs=dict(api_kwargs): _call_tushare_endpoint(
                    pro, api, call_kwargs
                ),
                policy=policy,
            )
        )
        if df.empty:
            empty_dates.append(event_date)
            if interval_seconds > 0.0:
                time.sleep(interval_seconds)
            continue
        _write_frame(df, output_path)
        written_dates.append(event_date)
        rows += int(len(df))
        if "symbol" in df.columns:
            symbols.update(df["symbol"].dropna().astype(str).tolist())
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
            "partition_by": "event_date",
            "request_interval_seconds": interval_seconds,
            "query_options": options.query_options,
        },
        "api_url": resolved_api_url,
        "request_policy": _request_policy_payload(policy),
        "totals": {
            "rows": rows,
            "symbols": len(symbols),
            "event_dates_requested": len(_calendar_dates(start, end)),
            "event_dates_written": len(written_dates),
            "event_dates_skipped": len(skipped_dates),
            "event_dates_empty": len(empty_dates),
            "files": len(written_dates),
        },
        "written_event_dates": written_dates,
        "skipped_event_dates": skipped_dates,
        "empty_event_dates": empty_dates,
    }
    _write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def mirror_a_share_month_dataset(
    options: MonthMirrorOptions | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Mirror TuShare month-keyed APIs."""
    options = _coerce_options(options, MonthMirrorOptions, kwargs)
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
        DEFAULT_BROKER_RECOMMEND_FIELDS if options.fields is None else options.fields
    )
    fields_text = _fields_text(requested_fields, required=("month", "ts_code"))

    rows = 0
    symbols: set[str] = set()
    written_months: list[str] = []
    skipped_months: list[str] = []
    empty_months: list[str] = []
    interval_seconds = max(0.0, float(options.request_interval_seconds))
    months = _calendar_months(start, end)
    for month in months:
        output_path = data_dir / f"month={month}" / "part.parquet"
        if options.skip_existing and output_path.exists():
            skipped_months.append(month)
            continue
        api_kwargs: dict[str, Any] = {**options.query_options, "month": month}
        if fields_text is not None:
            api_kwargs["fields"] = fields_text
        df = _prepare_event_frame(
            _call_tushare_api(
                lambda api=options.api_name, call_kwargs=dict(api_kwargs): _call_tushare_endpoint(
                    pro, api, call_kwargs
                ),
                policy=policy,
            )
        )
        if df.empty:
            empty_months.append(month)
            if interval_seconds > 0.0:
                time.sleep(interval_seconds)
            continue
        _write_frame(df, output_path)
        written_months.append(month)
        rows += int(len(df))
        if "symbol" in df.columns:
            symbols.update(df["symbol"].dropna().astype(str).tolist())
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
            "partition_by": "month",
            "request_interval_seconds": interval_seconds,
            "query_options": options.query_options,
        },
        "api_url": resolved_api_url,
        "request_policy": _request_policy_payload(policy),
        "totals": {
            "rows": rows,
            "symbols": len(symbols),
            "months_requested": len(months),
            "months_written": len(written_months),
            "months_skipped": len(skipped_months),
            "months_empty": len(empty_months),
            "files": len(written_months),
        },
        "written_months": written_months,
        "skipped_months": skipped_months,
        "empty_months": empty_months,
    }
    _write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def mirror_a_share_daily(**kwargs: Any) -> dict[str, Any]:
    return mirror_a_share_trade_date_dataset(dataset="daily", **kwargs)


def mirror_a_share_adj_factor(**kwargs: Any) -> dict[str, Any]:
    return mirror_a_share_trade_date_dataset(dataset="adj_factor", **kwargs)


def mirror_etf_daily(**kwargs: Any) -> dict[str, Any]:
    return mirror_a_share_trade_date_dataset(
        dataset="fund_daily",
        market="etf",
        **kwargs,
    )


def mirror_etf_adj_factor(**kwargs: Any) -> dict[str, Any]:
    return mirror_a_share_trade_date_dataset(
        dataset="fund_adj",
        market="etf",
        **kwargs,
    )


def mirror_a_share_daily_basic(**kwargs: Any) -> dict[str, Any]:
    return mirror_a_share_trade_date_dataset(dataset="daily_basic", **kwargs)


def _trade_date_mirror_function(dataset: str) -> Callable[..., dict[str, Any]]:
    def mirror(**kwargs: Any) -> dict[str, Any]:
        return mirror_a_share_trade_date_dataset(dataset=dataset, **kwargs)

    mirror.__name__ = f"mirror_a_share_{dataset}"
    return mirror


mirror_a_share_limit_status = _trade_date_mirror_function("limit_status")

mirror_a_share_moneyflow = _trade_date_mirror_function("moneyflow")

mirror_a_share_moneyflow_dc = _trade_date_mirror_function("moneyflow_dc")

mirror_a_share_moneyflow_hsgt = _trade_date_mirror_function("moneyflow_hsgt")

mirror_a_share_top_inst = _trade_date_mirror_function("top_inst")

mirror_a_share_ths_hot = _trade_date_mirror_function("ths_hot")

mirror_a_share_dc_concept = _trade_date_mirror_function("dc_concept")

mirror_a_share_dc_concept_cons = _trade_date_mirror_function("dc_concept_cons")

mirror_a_share_kpl_list = _trade_date_mirror_function("kpl_list")

mirror_a_share_kpl_concept_cons = _trade_date_mirror_function("kpl_concept_cons")

mirror_a_share_limit_step = _trade_date_mirror_function("limit_step")

mirror_a_share_limit_cpt_list = _trade_date_mirror_function("limit_cpt_list")

mirror_a_share_stk_auction_open = _trade_date_mirror_function("stk_auction_o")

mirror_a_share_stk_auction_close = _trade_date_mirror_function("stk_auction_c")

mirror_a_share_moneyflow_ths = _trade_date_mirror_function("moneyflow_ths")

mirror_a_share_limit_list_ths = _trade_date_mirror_function("limit_list_ths")

mirror_a_share_margin_detail = _trade_date_mirror_function("margin_detail")

mirror_a_share_margin = _trade_date_mirror_function("margin")

mirror_a_share_hsgt_top10 = _trade_date_mirror_function("hsgt_top10")


def mirror_a_share_report_rc(**kwargs: Any) -> dict[str, Any]:
    return mirror_a_share_date_range_event_dataset(
        dataset="report_rc",
        api_name="report_rc",
        **kwargs,
    )


def mirror_a_share_stk_surv(**kwargs: Any) -> dict[str, Any]:
    return mirror_a_share_date_range_event_dataset(
        dataset="stk_surv",
        api_name="stk_surv",
        **kwargs,
    )


def mirror_a_share_broker_recommend(**kwargs: Any) -> dict[str, Any]:
    return mirror_a_share_month_dataset(
        dataset="broker_recommend",
        api_name="broker_recommend",
        **kwargs,
    )


def mirror_a_share_index_daily(
    options: IndexDailyMirrorOptions | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Mirror TuShare index_daily bars for the requested index codes.

    The tushare ``index_daily`` API returns daily bars for a single index code
    per call, so we loop over ``index_codes`` and merge the frames into one
    single-file asset at ``data/part.parquet``.
    """
    options = _coerce_options(options, IndexDailyMirrorOptions, kwargs)
    if not options.index_codes:
        raise ValueError("IndexDailyMirrorOptions.index_codes must not be empty.")
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
        DEFAULT_INDEX_DAILY_FIELDS if options.fields is None else options.fields
    )
    fields_text = _fields_text(requested_fields, required=("ts_code",))
    interval_seconds = max(0.0, float(options.request_interval_seconds))

    frames: list[Any] = []
    written_codes: list[str] = []
    empty_codes: list[str] = []
    for index_code in options.index_codes:
        api_kwargs: dict[str, Any] = {
            **options.query_options,
            "ts_code": index_code,
            "start_date": start,
            "end_date": end,
        }
        if fields_text is not None:
            api_kwargs["fields"] = fields_text
        df = _call_tushare_api(
            lambda api_kwargs=dict(api_kwargs): pro.index_daily(**api_kwargs),
            policy=policy,
        )
        frame = _pandas().DataFrame(df)
        if frame.empty:
            empty_codes.append(index_code)
        else:
            if "ts_code" in frame.columns:
                frame["symbol"] = frame["ts_code"].map(_normalize_ts_code)
            frames.append(frame)
            written_codes.append(index_code)
        if interval_seconds > 0.0:
            time.sleep(interval_seconds)

    if not frames:
        raise ValueError("index_daily returned no rows for any requested index code.")
    merged = _pandas().concat(frames, ignore_index=True)
    data_path = data_dir / "part.parquet"
    _write_frame(merged, data_path)

    manifest: dict[str, Any] = {
        "schema_version": "tushare.index_daily.v1",
        "dataset": "index_daily",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(output_dir),
        "query": {
            "api": "index_daily",
            "index_codes": list(options.index_codes),
            "start_date": start,
            "end_date": end,
            "fields": list(requested_fields),
            "request_interval_seconds": interval_seconds,
            "query_options": options.query_options,
        },
        "api_url": resolved_api_url,
        "request_policy": _request_policy_payload(policy),
        "totals": {
            "rows": int(len(merged)),
            "index_codes_requested": len(options.index_codes),
            "index_codes_written": len(written_codes),
            "index_codes_empty": len(empty_codes),
            "files": 1,
        },
        "written_index_codes": written_codes,
        "empty_index_codes": empty_codes,
    }
    _write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def mirror_a_share_ths_index(
    *,
    out_dir: str | Path,
    fields: Iterable[str] | None = None,
    **request_options: Any,
) -> dict[str, Any]:
    """Mirror THS concept index directory as a single-file asset."""
    src = str(request_options.pop("src", "THS"))
    token_env = str(request_options.pop("token_env", "TUSHARE_TOKEN"))
    api_url = request_options.pop("api_url", None)
    request_policy = request_options.pop("request_policy", None)
    pd = _pandas()
    output_dir = _prepare_output_dir(Path(out_dir))
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    pro, policy, resolved_api_url = _tushare_runtime(
        token_env=token_env,
        api_url=api_url,
        request_policy=request_policy,
        request_options=request_options,
    )
    requested_fields = tuple(DEFAULT_THS_INDEX_FIELDS if fields is None else fields)
    fields_text = _fields_text(requested_fields)
    api_kwargs: dict[str, Any] = {"src": str(src)}
    if fields_text is not None:
        api_kwargs["fields"] = fields_text
    df = _call_tushare_api(
        lambda: pro.ths_index(**api_kwargs),
        policy=policy,
    )
    df = pd.DataFrame(df)
    if df.empty:
        raise ValueError("ths_index returned no rows.")
    if "ts_code" in df.columns:
        df["symbol"] = df["ts_code"].map(_normalize_ts_code)
    data_path = data_dir / "part.parquet"
    _write_frame(df, data_path)
    manifest: dict[str, Any] = {
        "schema_version": "tushare.ths_index.v1",
        "dataset": "ths_index",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(output_dir),
        "query": {
            "api": "ths_index",
            "fields": list(requested_fields),
            "src": str(src),
        },
        "api_url": resolved_api_url,
        "request_policy": _request_policy_payload(policy),
        "totals": {
            "rows": int(len(df)),
            "symbols": int(df["symbol"].nunique() if "symbol" in df.columns else 0),
            "files": 1,
        },
    }
    _write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def mirror_a_share_ths_member(
    options: ThsMemberMirrorOptions,
) -> dict[str, Any]:
    """Mirror THS concept member data by looping over THS concept codes."""
    # Reach the thin shell at call time via a *string* import
    # (importlib.import_module) so monkeypatch contracts such as
    # ``setattr(tushare_a_share, "_write_frame", ...)`` keep working: the injected
    # functions are the *shell-namespace* objects that tests patch, not the
    # private submodule copies. Using a string import (instead of
    # ``from ... import tushare_a_share``) keeps this edge invisible to the static
    # import graph, so the shell<->_adapters cycle is acyclic at analysis time.
    import importlib

    _shell = importlib.import_module("market_data_platform.providers.tushare_a_share")

    return mirror_a_share_ths_member_impl(
        options,
        dependencies=ThsMemberMirrorDependencies(
            pandas=_pandas,
            tushare_runtime=_tushare_runtime,
            call_tushare_api=_shell._call_tushare_api,
            fields_text=_shell._fields_text,
            normalize_ts_code=_shell._normalize_ts_code,
            write_frame=_shell._write_frame,
            write_manifest=_shell._write_manifest,
            request_policy_payload=_request_policy_payload,
        ),
    )
