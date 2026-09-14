from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.request import Request, urlopen

import pandas as pd

from market_data_platform.context.models import ContextSeriesSpec, validate_context_observations
from market_data_platform.context.source_payload import SourcePayload


class NEAContextError(RuntimeError):
    """Base error for the official NEA context adapter."""


class NEAContextSchemaError(NEAContextError):
    """Raised when the official electricity release layout drifts."""


@dataclass(frozen=True)
class NEAElectricitySpec:
    key: str
    label: str
    required: bool = True

    @property
    def level_series_id(self) -> str:
        return f"energy.electricity_consumption_{self.key}"

    @property
    def yoy_series_id(self) -> str:
        return f"{self.level_series_id}_yoy"


NEA_ELECTRICITY_SPECS = (
    NEAElectricitySpec("total", "全社会用电量"),
    NEAElectricitySpec("primary", "第一产业用电量"),
    NEAElectricitySpec("secondary", "第二产业用电量"),
    NEAElectricitySpec("industrial", "工业用电量", required=False),
    NEAElectricitySpec("high_tech_equipment", "高技术及装备制造业用电量", required=False),
    NEAElectricitySpec("tertiary", "第三产业用电量"),
    NEAElectricitySpec("residential", "城乡居民生活用电量"),
)


def nea_context_catalog() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for item in NEA_ELECTRICITY_SPECS:
        rows.append(
            asdict(
                ContextSeriesSpec(
                    series_id=item.level_series_id,
                    source_id=f"nea.electricity.{item.key}",
                    provider="nea",
                    source_series_key=item.label,
                    name=item.label,
                    family="energy",
                    frequency="monthly",
                    unit="100m_kwh",
                    seasonal_adjustment="official",
                    value_semantics="level",
                    revision_policy="observed_vintage",
                    availability_policy="official_release_date_or_retrieval",
                    expected_release_lag="monthly_release",
                    max_staleness=70,
                )
            )
        )
        rows.append(
            asdict(
                ContextSeriesSpec(
                    series_id=item.yoy_series_id,
                    source_id=f"nea.electricity.{item.key}.yoy",
                    provider="nea",
                    source_series_key=f"{item.label}.yoy",
                    name=f"{item.label}同比",
                    family="energy",
                    frequency="monthly",
                    unit="percent",
                    seasonal_adjustment="official",
                    value_semantics="yoy",
                    revision_policy="observed_vintage",
                    availability_policy="official_release_date_or_retrieval",
                    expected_release_lag="monthly_release",
                    max_staleness=70,
                )
            )
        )
    return pd.DataFrame(rows)


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = html.unescape(data).strip()
        if value:
            self.parts.append(value)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def _html_text(body: bytes) -> tuple[str, str]:
    try:
        source = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NEAContextSchemaError("NEA release is not UTF-8 HTML") from exc
    title_match = re.search(r"<title[^>]*>(.*?)</title>", source, flags=re.I | re.S)
    title = ""
    if title_match:
        title = re.sub(r"<[^>]+>", "", html.unescape(title_match.group(1))).strip()
    collector = _TextCollector()
    collector.feed(source)
    return title, collector.text()


def _publication_date(text: str) -> pd.Timestamp:
    match = re.search(r"发布时间\s*[：:]\s*(\d{4})-(\d{2})-(\d{2})", text)
    if match is None:
        raise NEAContextSchemaError("NEA release lacks publication date")
    date_text = "-".join(match.groups())
    return pd.Timestamp(date_text, tz="Asia/Shanghai").tz_convert("UTC")  # ty: ignore[invalid-return-type]


def _monthly_segment(text: str) -> tuple[int, str]:
    match = re.search(
        r"(?<!\d)(\d{1,2})月份[，,](.*?)(?=\s*1\s*[～—\-]\s*\d+\s*月|$)",
        text,
    )
    if match is None:
        raise NEAContextSchemaError("NEA release lacks a single-month electricity paragraph")
    month = int(match.group(1))
    if not 1 <= month <= 12:
        raise NEAContextSchemaError(f"invalid NEA release month: {month}")
    return month, match.group(2)


def _target_period(published_at: pd.Timestamp, month: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    published_local = published_at.tz_convert("Asia/Shanghai")
    year = published_local.year - 1 if month > published_local.month else published_local.year
    start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
    return start, start + pd.offsets.MonthEnd(1)  # ty: ignore[invalid-return-type]


def _metric(segment: str, item: NEAElectricitySpec) -> tuple[float, float] | None:
    pattern = re.compile(
        rf"{re.escape(item.label)}\s*([0-9]+(?:\.[0-9]+)?)\s*亿千瓦时\s*[,，;；。]?\s*"
        rf"同比\s*(增长|下降)\s*([0-9]+(?:\.[0-9]+)?)\s*%"
    )
    matches = pattern.findall(segment)
    if not matches:
        if item.required:
            raise NEAContextSchemaError(
                f"NEA monthly release lacks required metric {item.label} with 亿千瓦时 unit"
            )
        return None
    if len(matches) != 1:
        raise NEAContextSchemaError(f"NEA monthly release has duplicate metric {item.label}")
    level_text, direction, yoy_text = matches[0]
    yoy = float(yoy_text) * (-1.0 if direction == "下降" else 1.0)
    return float(level_text), yoy


def _observed_release(
    retrieved_at: pd.Timestamp,
    conservative_available_at: pd.Timestamp,
) -> bool:
    lag = retrieved_at - conservative_available_at
    return pd.Timedelta(0) <= lag <= pd.Timedelta(days=7)


def parse_nea_electricity_release(payload: SourcePayload) -> pd.DataFrame:
    if payload.provider != "nea" or payload.dataset != "electricity":
        raise ValueError(
            f"expected nea/electricity payload, got {payload.provider}/{payload.dataset}"
        )
    title, text = _html_text(payload.body)
    if "全社会用电量" not in title:
        raise NEAContextSchemaError("NEA release title does not identify an electricity release")
    published_at = _publication_date(text)
    month, segment = _monthly_segment(text)
    period_start, period_end = _target_period(published_at, month)
    retrieved = pd.Timestamp(payload.retrieved_at).tz_convert("UTC")

    # Official pages expose only a date. Make the observation visible no earlier than
    # 00:00 China time on the following calendar day; old page backfills become visible
    # only when they were actually retrieved.
    conservative_available = (
        published_at.tz_convert("Asia/Shanghai").normalize() + pd.Timedelta(days=1)
    ).tz_convert("UTC")
    observed = _observed_release(retrieved, conservative_available)  # ty: ignore[invalid-argument-type]
    available_at = conservative_available if observed else retrieved

    rows: list[dict[str, object]] = []
    for item in NEA_ELECTRICITY_SPECS:
        metric = _metric(segment, item)
        if metric is None:
            continue
        level, yoy = metric
        common = {
            "period_start": period_start,
            "period_end": period_end,
            "published_at": published_at,
            "observed_at": retrieved,
            "ingested_at": retrieved,
            "source_retrieved_at": retrieved,
            "available_at": available_at,
            "vintage_id": retrieved.strftime("%Y%m%dT%H%M%SZ"),  # ty: ignore[unresolved-attribute]
            "revision_number": 0,
            "source_hash": payload.sha256,
            "revision_covered": observed,
            "reconstructed": not observed,
        }
        rows.append(
            {
                **common,
                "series_id": item.level_series_id,
                "value": level,
                "unit": "100m_kwh",
            }
        )
        rows.append(
            {
                **common,
                "series_id": item.yoy_series_id,
                "value": yoy,
                "unit": "percent",
            }
        )
    if not rows:
        raise NEAContextSchemaError("NEA release produced no electricity observations")
    return validate_context_observations(pd.DataFrame(rows))


def _download(url: str) -> tuple[bytes, str]:
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urlopen(request, timeout=30.0) as response:
            return response.read(), str(response.headers.get("Content-Type") or "text/html")
    except OSError as exc:
        raise NEAContextError(f"NEA request failed for {url}: {exc}") from exc


def fetch_nea_payload(
    source_locator: str,
    *,
    retrieved_at: datetime | None = None,
    downloader: Callable[[str], tuple[bytes, str]] | None = None,
) -> SourcePayload:
    url = str(source_locator).strip()
    if not url.startswith("https://") or ".nea.gov.cn/" not in url:
        raise ValueError("NEA source_locator must be an official nea.gov.cn HTTPS URL")
    body, content_type = (downloader or _download)(url)
    return SourcePayload(
        provider="nea",
        dataset="electricity",
        source_locator=url,
        retrieved_at=retrieved_at or datetime.now(UTC),
        content_type=content_type,
        body=body,
        metadata={},
    )
