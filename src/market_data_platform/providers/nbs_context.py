from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from market_data_platform.context.models import ContextSeriesSpec, validate_context_observations
from market_data_platform.context.source_payload import SourcePayload

NBS_BASE_URL = "https://data.stats.gov.cn/dg/website"
NBS_SEARCH_PATH = "/publicrelease/web/external/query"
# The legacy JSON endpoint now returns a WAF 404. The current web client uses
# the streaming endpoint for the same request payload.
NBS_DATA_PATH = "/publicrelease/web/external/stream/esData"
NBS_MONTHLY_TYPE_CODE = "1"


class NBSContextError(RuntimeError):
    """Base error for the official NBS context adapter."""


class NBSContextSchemaError(NBSContextError):
    """Raised when the NBS release database no longer matches the frozen contract."""


@dataclass(frozen=True)
class NBSIndicatorSpec:
    dataset: str
    series_id: str
    query: str
    expected_name: str
    unit: str
    family: str
    value_semantics: str
    exact_identifier: str | None = None
    max_staleness: int = 70

    def catalog_spec(self) -> ContextSeriesSpec:
        source_key = self.exact_identifier or self.query
        return ContextSeriesSpec(
            series_id=self.series_id,
            source_id=f"nbs.{self.dataset}",
            provider="nbs",
            source_series_key=source_key,
            name=self.expected_name,
            family=self.family,
            frequency="monthly",
            unit=self.unit,
            seasonal_adjustment="official",
            value_semantics=self.value_semantics,
            revision_policy="observed_vintage",
            availability_policy="retrieval_observed",
            expected_release_lag="monthly_release",
            max_staleness=self.max_staleness,
        )


NBS_CONTEXT_SERIES: dict[str, NBSIndicatorSpec] = {
    "industrial_value_added_yoy": NBSIndicatorSpec(
        dataset="industrial_value_added_yoy",
        series_id="activity.industrial_value_added_yoy",
        query="规模以上工业增加值同比增长速度",
        expected_name="规模以上工业增加值同比增长速度",
        unit="percent",
        family="activity",
        value_semantics="yoy",
        exact_identifier=("3f2e14f0542348ed9fe02476eca3450b:ef1b1765960d45a29b4d7c4ca91be916"),
        max_staleness=70,
    ),
    "electricity_generation": NBSIndicatorSpec(
        dataset="electricity_generation",
        series_id="energy.electricity_generation",
        query="发电量",
        expected_name="发电量",
        unit="100m_kwh",
        family="energy",
        value_semantics="level",
    ),
    "thermal_generation": NBSIndicatorSpec(
        dataset="thermal_generation",
        series_id="energy.thermal_generation",
        query="火力发电量",
        expected_name="火力发电量",
        unit="100m_kwh",
        family="energy",
        value_semantics="level",
    ),
    "hydro_generation": NBSIndicatorSpec(
        dataset="hydro_generation",
        series_id="energy.hydro_generation",
        query="水力发电量",
        expected_name="水力发电量",
        unit="100m_kwh",
        family="energy",
        value_semantics="level",
    ),
    "nuclear_generation": NBSIndicatorSpec(
        dataset="nuclear_generation",
        series_id="energy.nuclear_generation",
        query="核能发电量",
        expected_name="核能发电量",
        unit="100m_kwh",
        family="energy",
        value_semantics="level",
    ),
    "wind_generation": NBSIndicatorSpec(
        dataset="wind_generation",
        series_id="energy.wind_generation",
        query="风力发电量",
        expected_name="风力发电量",
        unit="100m_kwh",
        family="energy",
        value_semantics="level",
    ),
    "solar_generation": NBSIndicatorSpec(
        dataset="solar_generation",
        series_id="energy.solar_generation",
        query="太阳能发电量",
        expected_name="太阳能发电量",
        unit="100m_kwh",
        family="energy",
        value_semantics="level",
    ),
    "coal_output": NBSIndicatorSpec(
        dataset="coal_output",
        series_id="energy.coal_output",
        query="原煤产量",
        expected_name="原煤产量",
        unit="10k_tonnes",
        family="energy",
        value_semantics="level",
    ),
    "crude_oil_output": NBSIndicatorSpec(
        dataset="crude_oil_output",
        series_id="energy.crude_oil_output",
        query="原油产量",
        expected_name="原油产量",
        unit="10k_tonnes",
        family="energy",
        value_semantics="level",
    ),
    "natural_gas_output": NBSIndicatorSpec(
        dataset="natural_gas_output",
        series_id="energy.natural_gas_output",
        query="天然气产量",
        expected_name="天然气产量",
        unit="100m_m3",
        family="energy",
        value_semantics="level",
    ),
}


def nbs_context_catalog() -> pd.DataFrame:
    return pd.DataFrame([asdict(spec.catalog_spec()) for spec in NBS_CONTEXT_SERIES.values()])


def _json_request(
    method: str,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    body: Mapping[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{NBS_BASE_URL}{path}"
    if params:
        url = (
            f"{url}?{urlencode({key: value for key, value in params.items() if value is not None})}"
        )
    data = None
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{NBS_BASE_URL}/page.html#/pc/national/home",
        "User-Agent": "Mozilla/5.0",
    }
    if body is not None:
        data = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=UTF-8"
    request = Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except OSError as exc:
        raise NBSContextError(f"NBS request failed: {method} {url}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NBSContextSchemaError(f"NBS returned non-JSON content for {url}") from exc
    if not isinstance(payload, dict):
        raise NBSContextSchemaError("NBS JSON response must be an object")
    return payload


def _exact_row(spec: NBSIndicatorSpec) -> dict[str, str] | None:
    if spec.exact_identifier is None:
        return None
    parts = spec.exact_identifier.split(":", 1)
    if len(parts) != 2 or any(len(part) != 32 for part in parts):
        raise NBSContextSchemaError(f"invalid pinned NBS identifier for {spec.dataset}")
    return {
        "cid": parts[0],
        "indic_id": parts[1],
        "show_name": spec.expected_name,
        "treeinfo_globalid": "",
    }


def resolve_nbs_indicator(
    search_response: Mapping[str, Any],
    spec: NBSIndicatorSpec,
) -> dict[str, Any]:
    data = search_response.get("data")
    if not isinstance(data, Mapping):
        raise NBSContextSchemaError("NBS search response lacks data object")
    rows = data.get("data")
    if not isinstance(rows, list):
        raise NBSContextSchemaError("NBS search response lacks data.data rows")
    matches = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("show_name") or row.get("i_name") or "").strip() == spec.expected_name
        and str(row.get("cid") or "").strip()
        and str(row.get("indic_id") or "").strip()
    ]
    if len(matches) != 1:
        raise NBSContextSchemaError(
            "NBS expected indicator "
            f"'{spec.expected_name}' must resolve uniquely; got {len(matches)}"
        )
    return matches[0]


def _root_id(row: Mapping[str, Any]) -> str:
    parts = str(row.get("treeinfo_globalid") or "").split(".")
    return parts[1] if len(parts) > 1 else ""


def fetch_nbs_payload(
    dataset: str,
    *,
    period: str,
    retrieved_at: datetime | None = None,
    request_json: Callable[..., dict[str, Any]] | None = None,
) -> SourcePayload:
    try:
        spec = NBS_CONTEXT_SERIES[dataset]
    except KeyError as exc:
        raise ValueError(f"unsupported NBS context dataset: {dataset}") from exc
    requester = request_json or _json_request
    direct = _exact_row(spec)
    search_response: dict[str, Any] | None = None
    if direct is None:
        search_response = requester(
            "GET",
            NBS_SEARCH_PATH,
            params={
                "search": spec.query,
                "code": NBS_MONTHLY_TYPE_CODE,
                "pagenum": 1,
                "pageSize": 100,
            },
        )
        resolved = resolve_nbs_indicator(search_response, spec)
    else:
        resolved = direct

    data_response = requester(
        "POST",
        NBS_DATA_PATH,
        body={
            "cid": str(resolved["cid"]),
            "indicatorIds": [str(resolved["indic_id"])],
            "daCatalogId": "",
            "das": [{"text": "national", "value": "000000000000"}],
            "showType": 1,
            "dts": [period if str(period).endswith("MM") else f"{period}MM"],
            "rootId": _root_id(resolved),
        },
    )
    envelope = {
        "resolved": resolved,
        "search_response": search_response,
        "data_response": data_response,
    }
    when = retrieved_at or datetime.now(UTC)
    return SourcePayload(
        provider="nbs",
        dataset=dataset,
        source_locator=f"{NBS_BASE_URL}{NBS_DATA_PATH}",
        retrieved_at=when,
        content_type="application/json",
        body=json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        metadata={
            "period": str(period).removesuffix("MM"),
            "indicator_cid": str(resolved["cid"]),
            "indicator_id": str(resolved["indic_id"]),
            "indicator_name": str(resolved.get("show_name") or spec.expected_name),
        },
    )


def _period_bounds(value: object) -> tuple[pd.Timestamp, pd.Timestamp]:
    digits = "".join(char for char in str(value) if char.isdigit())
    if len(digits) < 6:
        raise NBSContextSchemaError(f"invalid NBS monthly period code: {value}")
    start = pd.to_datetime(f"{digits[:6]}01", format="%Y%m%d", utc=True)
    return start, start + pd.offsets.MonthEnd(1)


def _is_current_vintage(retrieved_at: pd.Timestamp, period_end: pd.Timestamp) -> bool:
    lag = retrieved_at - period_end
    return pd.Timedelta(0) <= lag <= pd.Timedelta(days=45)


def parse_nbs_context(payload: SourcePayload) -> pd.DataFrame:  # noqa: PLR0912
    if payload.provider != "nbs":
        raise ValueError(f"expected nbs payload, got {payload.provider}")
    try:
        spec = NBS_CONTEXT_SERIES[payload.dataset]
    except KeyError as exc:
        raise ValueError(f"unsupported NBS context dataset: {payload.dataset}") from exc
    try:
        envelope = json.loads(payload.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NBSContextSchemaError("NBS payload body is not valid JSON") from exc
    if not isinstance(envelope, Mapping):
        raise NBSContextSchemaError("NBS payload envelope must be an object")
    resolved = envelope.get("resolved")
    response = envelope.get("data_response")
    if not isinstance(resolved, Mapping) or not isinstance(response, Mapping):
        raise NBSContextSchemaError("NBS payload lacks resolved/data_response objects")
    resolved_name = str(resolved.get("show_name") or "").strip()
    if resolved_name != spec.expected_name:
        raise NBSContextSchemaError(
            f"NBS resolved indicator name drifted: {resolved_name!r} != {spec.expected_name!r}"
        )
    periods = response.get("data")
    if not isinstance(periods, list):
        raise NBSContextSchemaError("NBS data response lacks data rows")

    retrieved = pd.Timestamp(payload.retrieved_at).tz_convert("UTC")
    resolved_indicator_id = str(resolved.get("indic_id") or "").strip()
    rows: list[dict[str, Any]] = []
    for period in periods:
        if not isinstance(period, Mapping):
            raise NBSContextSchemaError("NBS period row must be an object")
        values = period.get("values")
        if not isinstance(values, list):
            raise NBSContextSchemaError("NBS period row lacks values")
        period_start, period_end = _period_bounds(period.get("code"))
        matching = [
            value
            for value in values
            if isinstance(value, Mapping)
            and (
                (
                    resolved_indicator_id
                    and str(value.get("_id") or "").strip() == resolved_indicator_id
                )
                or str(value.get("i_showname") or value.get("_name") or "").strip()
                == spec.expected_name
            )
        ]
        if len(matching) != 1:
            raise NBSContextSchemaError(
                f"NBS period {period.get('code')} must contain one value for {spec.expected_name}"
            )
        numeric = pd.to_numeric(pd.Series([matching[0].get("value")]), errors="coerce").iloc[0]
        if pd.isna(numeric):
            raise NBSContextSchemaError(
                f"NBS value for {spec.expected_name} is not numeric in {period.get('code')}"
            )
        observed = _is_current_vintage(retrieved, period_end)  # ty: ignore[invalid-argument-type]
        rows.append(
            {
                "series_id": spec.series_id,
                "period_start": period_start,
                "period_end": period_end,
                "value": float(numeric),
                "unit": spec.unit,
                "published_at": pd.NaT,
                "observed_at": retrieved,
                "ingested_at": retrieved,
                "source_retrieved_at": retrieved,
                "available_at": retrieved,
                "vintage_id": retrieved.strftime("%Y%m%dT%H%M%SZ"),  # ty: ignore[unresolved-attribute]
                "revision_number": 0,
                "source_hash": payload.sha256,
                "revision_covered": observed,
                "reconstructed": not observed,
            }
        )
    if not rows:
        raise NBSContextSchemaError("NBS response produced no observations")
    return validate_context_observations(pd.DataFrame(rows))
