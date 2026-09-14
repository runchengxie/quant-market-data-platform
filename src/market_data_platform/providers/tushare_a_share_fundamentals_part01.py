"""Stateful TuShare A-share fundamentals ingestion and PIT publication."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitAsOfPanel as PitAsOfPanel,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitAsOfView as PitAsOfView,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitFundamentalsEvents as PitFundamentalsEvents,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of as load_pit_fundamentals_as_of,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of_panel as load_pit_fundamentals_as_of_panel,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of_view as load_pit_fundamentals_as_of_view,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_events as load_pit_fundamentals_events,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    DuplicatePageError,
    FieldValidationError,
    FundamentalsDownloadError,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    date_token as _date_token,
)
from market_data_platform.providers.tushare_common import (
    normalize_ts_code,
    pandas,
)

STATEMENT_DATASETS = {"income", "balancesheet", "cashflow"}

SUPPORTED_STATEMENT_COMP_TYPES = {"1", "2", "3", "4"}

DEFAULT_PIT_BUCKET_COUNT = 128

DEFAULT_PIT_BATCH_ROWS = 65536

DEFAULT_FUNDAMENTALS_MAX_OBSERVATION_AGE_DAYS = 3

FUNDAMENTALS_DATASETS = (
    "income",
    "balancesheet",
    "cashflow",
    "forecast",
    "express",
    "dividend",
    "fina_indicator",
    "fina_audit",
    "fina_mainbz",
    "disclosure_date",
)

ENTITLEMENT_MODES = ("vip_batch", "non_vip_fallback")


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    api_name: str
    vip_api_name: str | None
    fallback_api_name: str | None
    fetch_granularity: str
    date_fields: tuple[str, ...]
    report_period_fields: tuple[str, ...]
    disclosure_fields: tuple[str, ...]
    primary_keys: tuple[str, ...]
    dedupe_keys: tuple[str, ...]
    required_columns: tuple[str, ...]
    refresh_mode: str
    entitlement_policy: str
    standard_report_type: str | None = None


DATASET_SPECS: dict[str, DatasetSpec] = {
    "income": DatasetSpec(
        "income",
        "income",
        "income_vip",
        "income",
        "period",
        ("ann_date", "f_ann_date", "end_date"),
        ("end_date",),
        ("f_ann_date", "ann_date"),
        ("ts_code", "end_date", "report_type"),
        ("ts_code", "end_date", "report_type", "f_ann_date"),
        ("ts_code", "end_date"),
        "quarterly",
        "vip_batch_or_per_symbol_fallback",
        "1",
    ),
    "balancesheet": DatasetSpec(
        "balancesheet",
        "balancesheet",
        "balancesheet_vip",
        "balancesheet",
        "period",
        ("ann_date", "f_ann_date", "end_date"),
        ("end_date",),
        ("f_ann_date", "ann_date"),
        ("ts_code", "end_date", "report_type"),
        ("ts_code", "end_date", "report_type", "f_ann_date"),
        ("ts_code", "end_date"),
        "quarterly",
        "vip_batch_or_per_symbol_fallback",
        "1",
    ),
    "cashflow": DatasetSpec(
        "cashflow",
        "cashflow",
        "cashflow_vip",
        "cashflow",
        "period",
        ("ann_date", "f_ann_date", "end_date"),
        ("end_date",),
        ("f_ann_date", "ann_date"),
        ("ts_code", "end_date", "report_type"),
        ("ts_code", "end_date", "report_type", "f_ann_date"),
        ("ts_code", "end_date"),
        "quarterly",
        "vip_batch_or_per_symbol_fallback",
        "1",
    ),
    "forecast": DatasetSpec(
        "forecast",
        "forecast",
        "forecast_vip",
        "forecast",
        "period",
        ("ann_date", "end_date"),
        ("end_date",),
        ("ann_date",),
        ("ts_code", "end_date", "ann_date"),
        ("ts_code", "end_date", "ann_date", "type"),
        ("ts_code", "end_date", "ann_date"),
        "quarterly",
        "vip_batch_or_per_symbol_fallback",
    ),
    "express": DatasetSpec(
        "express",
        "express",
        "express_vip",
        "express",
        "period",
        ("ann_date", "end_date"),
        ("end_date",),
        ("ann_date",),
        ("ts_code", "end_date", "ann_date"),
        ("ts_code", "end_date", "ann_date"),
        ("ts_code", "end_date", "ann_date"),
        "quarterly",
        "vip_batch_or_per_symbol_fallback",
    ),
    "dividend": DatasetSpec(
        "dividend",
        "dividend",
        None,
        "dividend",
        "symbol",
        ("ann_date", "end_date", "ex_date", "pay_date"),
        ("end_date",),
        ("ann_date",),
        ("ts_code", "end_date", "ann_date"),
        ("ts_code", "end_date", "ann_date", "div_proc"),
        ("ts_code", "end_date", "ann_date"),
        "event",
        "per_symbol_only",
    ),
    "fina_indicator": DatasetSpec(
        "fina_indicator",
        "fina_indicator",
        "fina_indicator_vip",
        "fina_indicator",
        "period",
        ("ann_date", "end_date"),
        ("end_date",),
        ("ann_date",),
        ("ts_code", "end_date", "ann_date"),
        ("ts_code", "end_date", "ann_date"),
        ("ts_code", "end_date", "ann_date"),
        "quarterly",
        "vip_batch_or_per_symbol_fallback",
    ),
    "fina_audit": DatasetSpec(
        "fina_audit",
        "fina_audit",
        None,
        "fina_audit",
        "symbol",
        ("ann_date", "end_date"),
        ("end_date",),
        ("ann_date",),
        ("ts_code", "end_date", "ann_date"),
        ("ts_code", "end_date", "ann_date"),
        ("ts_code", "end_date", "ann_date"),
        "quarterly",
        "per_symbol_only",
    ),
    "fina_mainbz": DatasetSpec(
        "fina_mainbz",
        "fina_mainbz",
        "fina_mainbz_vip",
        "fina_mainbz",
        "period",
        ("end_date",),
        ("end_date",),
        (),
        ("ts_code", "end_date"),
        ("ts_code", "end_date", "bz_item", "curr_type"),
        ("ts_code", "end_date"),
        "quarterly",
        "vip_batch_or_per_symbol_fallback",
    ),
    "disclosure_date": DatasetSpec(
        "disclosure_date",
        "disclosure_date",
        None,
        "disclosure_date",
        "symbol",
        ("ann_date", "end_date", "pre_date", "actual_date"),
        ("end_date",),
        ("actual_date", "ann_date"),
        ("ts_code", "end_date"),
        ("ts_code", "end_date", "actual_date"),
        ("ts_code", "end_date"),
        "quarterly",
        "per_symbol_only",
    ),
}


@dataclass(frozen=True)
class QueryUnit:
    dataset: str
    endpoint: str
    entitlement_mode: str
    granularity: str
    params: dict[str, str]

    @property
    def unit_id(self) -> str:
        encoded = json.dumps(self.params, sort_keys=True).encode("utf-8")
        suffix = hashlib.sha256(encoded).hexdigest()[:12]
        return f"{self.dataset}:{self.granularity}:{suffix}"


@dataclass(frozen=True)
class RawFundamentalsDownloadOptions:
    dataset: str
    out_dir: str | Path
    start_date: str
    end_date: str
    entitlement_mode: str
    symbols: Iterable[str] | None = None
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    client: Any | None = None
    run_id: str | None = None
    retry_attempts: int = 3
    retry_backoff_seconds: float = 0.0
    request_interval_seconds: float = 0.0
    page_size: int = 5000
    max_pages: int = 100
    stale_after_days: int | None = None


@dataclass
class RawFundamentalsDownloadContext:
    options: RawFundamentalsDownloadOptions
    output: Path
    source_run_id: str
    units: list[QueryUnit]
    state_path: Path
    failure_path: Path
    state: dict[str, Any]
    failures: dict[str, Any]
    client: Any
    parts: list[dict[str, Any]]
    last_request_at: float | None = None
    request_started_at: str | None = None


def dataset_specs_payload() -> dict[str, dict[str, Any]]:
    return {name: asdict(spec) for name, spec in DATASET_SPECS.items()}


def _parse_date(value: str) -> date:
    token = _date_token(value)
    if len(token) != 8:
        raise ValueError(f"Expected YYYYMMDD date, got: {value}")
    return datetime.strptime(token, "%Y%m%d").date()


def _quarter_ends(start_date: str, end_date: str) -> list[str]:
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start > end:
        raise ValueError("start_date must not be after end_date")
    result = []
    year = start.year
    while year <= end.year:
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            value = date(year, month, day)
            if start <= value <= end:
                result.append(value.strftime("%Y%m%d"))
        year += 1
    return result


def _days(start_date: str, end_date: str) -> list[str]:
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start > end:
        raise ValueError("start_date must not be after end_date")
    return [
        (start + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range((end - start).days + 1)
    ]


def _endpoint_for(spec: DatasetSpec, entitlement_mode: str) -> tuple[str, str]:
    if entitlement_mode not in ENTITLEMENT_MODES:
        raise ValueError(f"Unsupported entitlement mode: {entitlement_mode}")
    if entitlement_mode == "vip_batch" and spec.vip_api_name:
        return spec.vip_api_name, spec.fetch_granularity
    if spec.fallback_api_name:
        return spec.fallback_api_name, "symbol"
    raise ValueError(
        f"{spec.name} has no safe {entitlement_mode} endpoint; "
        "record it as skipped or use a supported entitlement mode."
    )


def plan_query_units(
    *,
    dataset: str,
    start_date: str,
    end_date: str,
    entitlement_mode: str,
    symbols: Iterable[str] | None = None,
) -> list[QueryUnit]:
    spec = DATASET_SPECS[dataset]
    endpoint, granularity = _endpoint_for(spec, entitlement_mode)
    if granularity == "symbol":
        values = sorted({normalize_ts_code(value) for value in symbols or () if value})
        if not values:
            raise ValueError(f"{dataset} {entitlement_mode} planning requires symbols.")
        return [
            QueryUnit(dataset, endpoint, entitlement_mode, granularity, {"ts_code": value})
            for value in values
        ]
    if granularity == "date":
        return [
            QueryUnit(dataset, endpoint, entitlement_mode, granularity, {"ann_date": value})
            for value in _days(start_date, end_date)
        ]
    return [
        QueryUnit(dataset, endpoint, entitlement_mode, granularity, {"period": value})
        for value in _quarter_ends(start_date, end_date)
    ]


def build_download_plan(
    *,
    datasets: Iterable[str] | None,
    start_date: str,
    end_date: str,
    entitlement_mode: str,
    symbols: Iterable[str] | None = None,
) -> dict[str, Any]:
    selected = list(dict.fromkeys(datasets or FUNDAMENTALS_DATASETS))
    planned = []
    skipped = []
    for dataset in selected:
        if dataset not in DATASET_SPECS:
            raise ValueError(f"Unknown fundamentals dataset: {dataset}")
        try:
            units = plan_query_units(
                dataset=dataset,
                start_date=start_date,
                end_date=end_date,
                entitlement_mode=entitlement_mode,
                symbols=symbols,
            )
        except ValueError as exc:
            skipped.append({"dataset": dataset, "reason": str(exc)})
            continue
        planned.append(
            {
                "dataset": dataset,
                "spec": asdict(DATASET_SPECS[dataset]),
                "units": [asdict(unit) | {"unit_id": unit.unit_id} for unit in units],
            }
        )
    return {
        "schema_version": "tushare.a_share.fundamentals.plan.v1",
        "status": "planned",
        "start_date": _date_token(start_date),
        "end_date": _date_token(end_date),
        "entitlement_mode": entitlement_mode,
        "datasets": planned,
        "skipped_datasets": skipped,
        "totals": {
            "datasets": len(planned),
            "query_units": sum(len(row["units"]) for row in planned),
            "skipped_datasets": len(skipped),
        },
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _frame_signature(frame: Any) -> str:
    payload = frame.to_json(orient="split", date_format="iso", default_handler=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _schema_hash(frame: Any) -> str:
    columns = [(str(column), str(frame[column].dtype)) for column in frame.columns]
    return hashlib.sha256(json.dumps(columns).encode("utf-8")).hexdigest()


def _validate_fields(frame: Any, spec: DatasetSpec) -> None:
    missing = sorted(set(spec.required_columns) - set(frame.columns))
    if missing:
        raise FieldValidationError(f"{spec.name} response is missing required columns: {missing}")


def _respect_request_interval(context: RawFundamentalsDownloadContext) -> None:
    interval = context.options.request_interval_seconds
    if interval <= 0:
        return
    if context.last_request_at is not None:
        elapsed = time.monotonic() - context.last_request_at
        remaining = interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
    context.last_request_at = time.monotonic()


def _fetch_query_unit(
    context: RawFundamentalsDownloadContext,
    unit: QueryUnit,
    *,
    page_size: int,
    max_pages: int,
) -> Any:
    pd = pandas()
    endpoint = getattr(context.client, unit.endpoint)
    frames = []
    signatures: set[str] = set()
    for page in range(max_pages):
        _respect_request_interval(context)
        frame = pd.DataFrame(
            endpoint(
                **unit.params,
                offset=page * page_size,
                limit=page_size,
            )
        )
        if frame.empty:
            break
        _validate_fields(frame, DATASET_SPECS[unit.dataset])
        signature = _frame_signature(frame)
        if signature in signatures:
            raise DuplicatePageError(f"{unit.unit_id} repeated provider page {page}.")
        signatures.add(signature)
        frames.append(frame)
        if len(frame) < page_size:
            break
    else:
        raise FundamentalsDownloadError(f"{unit.unit_id} exceeded max_pages={max_pages}.")
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _safe_part_name(unit: QueryUnit) -> str:
    return unit.unit_id.replace(":", "_")


def _state_default(dataset: str, run_id: str, units: list[QueryUnit]) -> dict[str, Any]:
    return {
        "schema_version": "tushare.a_share.fundamentals.state.v1",
        "dataset": dataset,
        "source_run_id": run_id,
        "updated_at": _now(),
        "plan": [unit.unit_id for unit in units],
        "units": {},
        "contiguous_watermark": None,
    }
