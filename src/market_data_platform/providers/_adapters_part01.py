"""Business adapters (mirror/export functions) for the TuShare A-share provider.

This module also hosts the A-group request-policy helpers and ``_tushare_runtime``
so that the F-group mirrors can reference them without creating import cycles.
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import yaml

from market_data_platform.providers._client import (
    _call_trade_date_endpoint_frame,
    _call_tushare_api,
    _configure_tushare_client_api_url,
    _pandas,
    get_tushare_client,
)
from market_data_platform.providers._env import (
    resolve_tushare_api_url,
)
from market_data_platform.providers._frame import (
    _prepare_frame,
)
from market_data_platform.providers._io import (
    _fields_text,
    _single_file_manifest_path,
    _write_frame,
    _write_frame_atomically,
    _write_manifest,
)
from market_data_platform.providers.tushare_a_share_dates import (
    _validate_date,
)
from market_data_platform.providers.tushare_a_share_dc_concept_cons import (
    fetch_dc_concept_cons_pages,
)
from market_data_platform.providers.tushare_a_share_options import (
    DEFAULT_DISABLE_PROXY,
    DEFAULT_LIST_STATUSES,
    DEFAULT_QUOTA_COOLDOWN_SECONDS,
    DEFAULT_REQUEST_ATTEMPTS,
    DEFAULT_RETRY_MAX_SLEEP_SECONDS,
    DEFAULT_RETRY_SLEEP_SECONDS,
    DEFAULT_STOCK_BASIC_FIELDS,
    DEFAULT_TRADE_DATE_FIELDS,
    TRADE_DATE_REQUIRED_FIELDS,
    TushareRequestPolicy,
)

OptionsT = TypeVar("OptionsT")


@dataclass(frozen=True)
class _FundPortfolioPeriodRequest:
    client: Any
    policy: TushareRequestPolicy
    period: str
    page_size: int
    max_pages_per_period: int
    fields_text: str | None


def _coerce_options(
    options: OptionsT | None,
    option_type: type[OptionsT],
    kwargs: dict[str, Any],
) -> OptionsT:
    if options is not None:
        if kwargs:
            raise TypeError("Pass either an options object or keyword arguments, not both.")
        if not isinstance(options, option_type):
            raise TypeError(f"options must be {option_type.__name__}.")
        return options

    option_fields = set(vars(option_type)["__dataclass_fields__"])
    request_options = dict(kwargs)
    option_kwargs = {
        name: request_options.pop(name)
        for name in tuple(request_options)
        if name in option_fields and name != "request_options"
    }
    option_kwargs["request_options"] = request_options
    return option_type(**option_kwargs)


def _request_policy(
    *,
    request_attempts: int = DEFAULT_REQUEST_ATTEMPTS,
    retry_sleep_seconds: float = DEFAULT_RETRY_SLEEP_SECONDS,
    retry_max_sleep_seconds: float = DEFAULT_RETRY_MAX_SLEEP_SECONDS,
    quota_cooldown_seconds: float = DEFAULT_QUOTA_COOLDOWN_SECONDS,
    disable_proxy: bool = DEFAULT_DISABLE_PROXY,
) -> TushareRequestPolicy:
    attempts = max(1, int(request_attempts))
    retry_sleep = max(0.0, float(retry_sleep_seconds))
    retry_max_sleep = max(retry_sleep, float(retry_max_sleep_seconds))
    quota_cooldown = max(0.0, float(quota_cooldown_seconds))
    return TushareRequestPolicy(
        attempts=attempts,
        retry_sleep_seconds=retry_sleep,
        retry_max_sleep_seconds=retry_max_sleep,
        quota_cooldown_seconds=quota_cooldown,
        disable_proxy=bool(disable_proxy),
    )


def request_policy(
    *,
    request_attempts: int = DEFAULT_REQUEST_ATTEMPTS,
    retry_sleep_seconds: float = DEFAULT_RETRY_SLEEP_SECONDS,
    retry_max_sleep_seconds: float = DEFAULT_RETRY_MAX_SLEEP_SECONDS,
    quota_cooldown_seconds: float = DEFAULT_QUOTA_COOLDOWN_SECONDS,
    disable_proxy: bool = DEFAULT_DISABLE_PROXY,
) -> TushareRequestPolicy:
    return _request_policy(
        request_attempts=request_attempts,
        retry_sleep_seconds=retry_sleep_seconds,
        retry_max_sleep_seconds=retry_max_sleep_seconds,
        quota_cooldown_seconds=quota_cooldown_seconds,
        disable_proxy=disable_proxy,
    )


def _request_policy_from_options(
    request_policy: TushareRequestPolicy | None,
    options: dict[str, Any],
) -> TushareRequestPolicy:
    request_options = dict(options)
    if request_policy is not None:
        if request_options:
            raise TypeError("Pass either request_policy or individual TuShare request options.")
        if not isinstance(request_policy, TushareRequestPolicy):
            raise TypeError("request_policy must be a TushareRequestPolicy.")
        return request_policy
    return _request_policy(**request_options)


def request_policy_from_options(
    request_policy: TushareRequestPolicy | None,
    options: dict[str, Any],
) -> TushareRequestPolicy:
    return _request_policy_from_options(request_policy, options)


def _request_policy_payload(policy: TushareRequestPolicy) -> dict[str, Any]:
    payload = {
        "attempts": policy.attempts,
        "retry_sleep_seconds": policy.retry_sleep_seconds,
        "retry_max_sleep_seconds": policy.retry_max_sleep_seconds,
        "quota_cooldown_seconds": policy.quota_cooldown_seconds,
        "disable_proxy": policy.disable_proxy,
    }
    if policy.request_timeout_seconds is not None:
        payload["request_timeout_seconds"] = policy.request_timeout_seconds
    return payload


def request_policy_payload(policy: TushareRequestPolicy) -> dict[str, Any]:
    return _request_policy_payload(policy)


def _tushare_runtime(
    *,
    token_env: str,
    api_url: str | None,
    request_policy: TushareRequestPolicy | None,
    request_options: dict[str, Any],
) -> tuple[Any, TushareRequestPolicy, str | None]:
    runtime_options = dict(request_options)
    client = runtime_options.pop("client", None)
    policy = _request_policy_from_options(request_policy, runtime_options)
    resolved_api_url = resolve_tushare_api_url(api_url, token_env=token_env)
    pro = client or get_tushare_client(
        token_env=token_env,
        api_url=resolved_api_url,
        disable_proxy=policy.disable_proxy,
        request_timeout_seconds=policy.request_timeout_seconds,
    )
    _configure_tushare_client_api_url(pro, resolved_api_url)
    return pro, policy, resolved_api_url


def export_a_share_instruments(  # noqa: PLR0913
    *,
    out: str | Path,
    list_statuses: Iterable[str] | None = None,
    fields: Iterable[str] | None = None,
    symbols_out: str | Path | None = None,
    token_env: str = "TUSHARE_TOKEN",
    api_url: str | None = None,
    client: Any | None = None,
    request_policy: TushareRequestPolicy | None = None,
    **request_options: Any,
) -> dict[str, Any]:
    pd = _pandas()
    policy = _request_policy_from_options(request_policy, request_options)
    resolved_api_url = resolve_tushare_api_url(api_url, token_env=token_env)
    pro = client or get_tushare_client(
        token_env=token_env,
        api_url=resolved_api_url,
        disable_proxy=policy.disable_proxy,
    )
    _configure_tushare_client_api_url(pro, resolved_api_url)
    statuses = tuple(
        str(value).strip().upper() for value in (list_statuses or DEFAULT_LIST_STATUSES)
    )
    requested_fields = tuple(fields or DEFAULT_STOCK_BASIC_FIELDS)
    fields_text = _fields_text(requested_fields, required=("ts_code", "list_status"))
    frames = [
        _prepare_frame(
            _call_tushare_api(
                lambda status=status: pro.stock_basic(
                    exchange="",
                    list_status=status,
                    fields=fields_text,
                ),
                policy=policy,
            )
        )
        for status in statuses
    ]
    non_empty = [frame for frame in frames if not frame.empty]
    df = pd.concat(non_empty, ignore_index=True) if non_empty else pd.DataFrame()
    if not df.empty and "ts_code" in df.columns:
        df = df.drop_duplicates(subset=["ts_code", "list_status"]).sort_values("ts_code")

    output = Path(out).expanduser().resolve()
    _write_frame(df, output)
    symbols = sorted(df["symbol"].dropna().astype(str).unique().tolist()) if "symbol" in df else []
    if symbols_out is not None:
        symbols_path = Path(symbols_out).expanduser().resolve()
        symbols_path.parent.mkdir(parents=True, exist_ok=True)
        symbols_path.write_text("\n".join(symbols) + ("\n" if symbols else ""), encoding="utf-8")

    manifest = {
        "schema_version": "tushare.stock_basic.v1",
        "dataset": "instruments",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(output),
        "query": {"list_statuses": list(statuses), "fields": list(requested_fields)},
        "api_url": resolved_api_url,
        "request_policy": _request_policy_payload(policy),
        "totals": {"rows": int(len(df)), "symbols": len(symbols), "files": 1},
    }
    _write_manifest(_single_file_manifest_path(output), manifest)
    return manifest


def mirror_a_share_trade_cal(  # noqa: PLR0913
    *,
    out: str | Path,
    start_date: str,
    end_date: str,
    exchange: str = "",
    token_env: str = "TUSHARE_TOKEN",
    api_url: str | None = None,
    client: Any | None = None,
    request_policy: TushareRequestPolicy | None = None,
    **request_options: Any,
) -> dict[str, Any]:
    start = _validate_date(start_date)
    end = _validate_date(end_date)
    policy = _request_policy_from_options(request_policy, request_options)
    resolved_api_url = resolve_tushare_api_url(api_url, token_env=token_env)
    pro = client or get_tushare_client(
        token_env=token_env,
        api_url=resolved_api_url,
        disable_proxy=policy.disable_proxy,
    )
    _configure_tushare_client_api_url(pro, resolved_api_url)
    df = _prepare_frame(
        _call_tushare_api(
            lambda: pro.trade_cal(exchange=exchange, start_date=start, end_date=end),
            policy=policy,
        )
    )
    output = Path(out).expanduser().resolve()
    _write_frame(df, output)
    open_dates = int((df["is_open"].astype(str) == "1").sum()) if "is_open" in df else 0
    manifest = {
        "schema_version": "tushare.trade_cal.v1",
        "dataset": "trade_cal",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(output),
        "query": {"exchange": exchange, "start_date": start, "end_date": end},
        "api_url": resolved_api_url,
        "request_policy": _request_policy_payload(policy),
        "totals": {"rows": int(len(df)), "open_dates": open_dates, "files": 1},
    }
    _write_manifest(_single_file_manifest_path(output), manifest)
    return manifest


def _open_trade_dates(
    client: Any,
    *,
    start_date: str,
    end_date: str,
    policy: TushareRequestPolicy,
) -> list[str]:
    frame = _prepare_frame(
        _call_tushare_api(
            lambda: client.trade_cal(
                exchange="",
                start_date=start_date,
                end_date=end_date,
                is_open="1",
            ),
            policy=policy,
        )
    )
    if frame.empty:
        return []
    if "cal_date" not in frame.columns or "is_open" not in frame.columns:
        raise ValueError("TuShare trade_cal response is missing cal_date or is_open.")
    values = frame.loc[frame["is_open"].astype(str) == "1", "cal_date"].astype(str)
    return sorted({_validate_date(value) for value in values})


def _trade_date_field_context(
    dataset: str,
    fields: Iterable[str] | None,
) -> tuple[tuple[str, ...], str | None]:
    requested_fields = tuple(
        DEFAULT_TRADE_DATE_FIELDS.get(dataset, ()) if fields is None else fields
    )
    required_fields = TRADE_DATE_REQUIRED_FIELDS.get(dataset, ("ts_code", "trade_date"))
    return requested_fields, _fields_text(requested_fields, required=required_fields)


def _fetch_trade_date_dataset_frame(
    client: Any,
    api_name: str,
    dataset: str,
    api_kwargs: dict[str, Any],
    *,
    policy: TushareRequestPolicy,
) -> Any:
    tag_values = api_kwargs.get("tag")
    if dataset != "kpl_list" or not isinstance(tag_values, (list, tuple)):
        return _call_trade_date_endpoint_frame(client, api_name, api_kwargs, policy=policy)

    tag_frames: list[Any] = []
    for tag in tag_values:
        tag_kwargs = {**api_kwargs, "tag": str(tag)}
        tag_frame = _call_trade_date_endpoint_frame(
            client,
            api_name,
            tag_kwargs,
            policy=policy,
        )
        if not tag_frame.empty:
            tag_frames.append(tag_frame)
    if not tag_frames:
        return _pandas().DataFrame()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="The behavior of DataFrame concatenation with empty or all-NA",
            category=FutureWarning,
        )
        return _pandas().concat(tag_frames, ignore_index=True)


def _fetch_trade_date_partition(
    client: Any,
    context: dict[str, Any],
    api_kwargs: dict[str, Any],
) -> tuple[Any, dict[str, Any] | None]:
    if context["dataset"] != "dc_concept_cons":
        return (
            _fetch_trade_date_dataset_frame(
                client,
                context["api_name"],
                context["dataset"],
                api_kwargs,
                policy=context["policy"],
            ),
            None,
        )

    trade_date = str(api_kwargs["trade_date"])
    result = fetch_dc_concept_cons_pages(
        trade_date=trade_date,
        api_kwargs=api_kwargs,
        fetch_page=lambda page_kwargs: _call_trade_date_endpoint_frame(
            client,
            context["api_name"],
            page_kwargs,
            policy=context["policy"],
        ),
        pandas=_pandas(),
    )
    return result.frame, result.completeness


def _existing_dc_completeness_by_date(manifest_path: Path) -> dict[str, dict[str, Any]]:
    if not manifest_path.is_file():
        return {}
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(payload, dict):
        return {}
    completeness = payload.get("completeness")
    if not isinstance(completeness, dict):
        return {}
    trade_dates = completeness.get("trade_dates")
    if not isinstance(trade_dates, dict):
        return {}
    return {
        str(trade_date): dict(receipt)
        for trade_date, receipt in trade_dates.items()
        if isinstance(receipt, dict)
    }


def _skipped_dc_completeness(
    trade_date: str,
    existing_by_date: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    existing = existing_by_date.get(trade_date)
    if existing is not None:
        return {**existing, "trade_date": trade_date, "source": "existing_manifest"}
    return {
        "trade_date": trade_date,
        "complete": False,
        "row_count": None,
        "page_count": None,
        "request_count": None,
        "page_size": None,
        "terminal_page_reached": False,
        "last_page_row_count": None,
        "distinct_theme_count": None,
        "coverage": None,
        "source": "skipped_existing_unverified",
    }


def _mirror_trade_date_partitions(client: Any, context: dict[str, Any]) -> dict[str, Any]:
    rows = 0
    symbols: set[str] = set()
    written_dates: list[str] = []
    skipped_dates: list[str] = []
    empty_dates: list[str] = []
    themes: set[str] = set()
    date_completeness: dict[str, dict[str, Any]] = {}

    for trade_date in context["trade_dates"]:
        output_path = context["data_dir"] / f"trade_date={trade_date}" / "part.parquet"
        if context["skip_existing"] and output_path.exists():
            skipped_dates.append(trade_date)
            if context["dataset"] == "dc_concept_cons":
                date_completeness[trade_date] = _skipped_dc_completeness(
                    trade_date,
                    context["existing_dc_completeness_by_date"],
                )
            continue
        api_kwargs: dict[str, Any] = {**context["query_options"], "trade_date": trade_date}
        if context["fields_text"] is not None:
            api_kwargs["fields"] = context["fields_text"]
        df, completeness = _fetch_trade_date_partition(client, context, api_kwargs)
        if completeness is not None:
            date_completeness[trade_date] = completeness
        if df.empty:
            empty_dates.append(trade_date)
            _sleep_after_partition(context["interval_seconds"])
            continue
        if context["dataset"] == "dc_concept_cons":
            _write_frame_atomically(df, output_path)
        else:
            _write_frame(df, output_path)
        written_dates.append(trade_date)
        rows += int(len(df))
        if "symbol" in df.columns:
            symbols.update(df["symbol"].dropna().astype(str).tolist())
        if "theme_code" in df.columns:
            themes.update(
                value for value in df["theme_code"].dropna().astype(str).str.strip() if value
            )
        _sleep_after_partition(context["interval_seconds"])

    return {
        "rows": rows,
        "symbols": symbols,
        "written_dates": written_dates,
        "skipped_dates": skipped_dates,
        "empty_dates": empty_dates,
        "themes": themes,
        "date_completeness": date_completeness,
    }


def _sleep_after_partition(interval_seconds: float) -> None:
    if interval_seconds > 0.0:
        time.sleep(interval_seconds)
