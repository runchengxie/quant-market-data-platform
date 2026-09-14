"""Pagination and completeness evidence for ``dc_concept_cons``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DEFAULT_DC_CONCEPT_CONS_PAGE_SIZE = 3000
DEFAULT_DC_CONCEPT_CONS_MAX_PAGES = 100
DC_CONCEPT_CONS_BUSINESS_KEY = ("trade_date", "theme_code", "ts_code")


@dataclass(frozen=True)
class DcConceptConsFetchResult:
    frame: Any
    completeness: dict[str, Any]


@dataclass(frozen=True)
class _DcConceptConsPaginationStats:
    page_count: int
    request_count: int
    page_size: int
    last_page_row_count: int
    offset_page_count: int
    offset_request_count: int
    repair_page_count: int
    repair_request_count: int
    boundary_theme_codes: list[str]
    raw_row_count: int


@dataclass(frozen=True)
class _DcConceptConsRequest:
    trade_date: str
    api_kwargs: dict[str, Any]
    fetch_page: Callable[[dict[str, Any]], Any]
    pandas: Any
    page_size: int
    max_pages: int


def _nonblank_theme_codes(frame: Any) -> Any:
    if "theme_code" not in frame.columns:
        return frame.index[:0]
    values = frame["theme_code"].fillna("").astype(str).str.strip()
    return values[values.ne("")]


def _validated_business_keys(frame: Any, *, trade_date: str) -> set[tuple[str, str, str]]:
    missing = [column for column in DC_CONCEPT_CONS_BUSINESS_KEY if column not in frame.columns]
    if missing:
        raise ValueError(
            f"dc_concept_cons response is missing completeness key fields: {', '.join(missing)}."
        )
    normalized = frame.loc[:, list(DC_CONCEPT_CONS_BUSINESS_KEY)].copy()
    for column in DC_CONCEPT_CONS_BUSINESS_KEY:
        normalized[column] = normalized[column].fillna("").astype(str).str.strip()
        if normalized[column].eq("").any():
            raise ValueError(f"dc_concept_cons response contains blank {column} values.")
    returned_dates = set(normalized["trade_date"].tolist())
    if returned_dates != {trade_date}:
        raise ValueError(
            "dc_concept_cons response trade_date does not match the requested exact date "
            f"{trade_date}: {sorted(returned_dates)}."
        )
    keys = set(normalized.itertuples(index=False, name=None))
    if len(keys) != len(normalized):
        raise RuntimeError(
            "dc_concept_cons response contains duplicate business keys for trade_date="
            f"{trade_date}; refusing to publish an ambiguous partition."
        )
    return keys


def _validated_theme_bounds(frame: Any, *, trade_date: str) -> tuple[str, str]:
    themes = frame["theme_code"].fillna("").astype(str).str.strip().tolist()
    if any(left > right for left, right in zip(themes, themes[1:], strict=False)):
        raise RuntimeError(
            "dc_concept_cons response is not ordered by theme_code for trade_date="
            f"{trade_date}; boundary repair cannot prove completeness."
        )
    return themes[0], themes[-1]


def _fetch_boundary_theme(
    request: _DcConceptConsRequest,
    *,
    theme_code: str,
) -> tuple[Any, int, int]:
    frames: list[Any] = []
    seen_keys: set[tuple[str, str, str]] = set()
    request_count = 0
    terminal_page_reached = False
    for page_index in range(request.max_pages):
        page_frame = request.fetch_page(
            {
                **request.api_kwargs,
                "theme_code": theme_code,
                "limit": request.page_size,
                "offset": page_index * request.page_size,
            }
        )
        request_count += 1
        if page_frame.empty:
            terminal_page_reached = True
            break
        page_keys = _validated_business_keys(page_frame, trade_date=request.trade_date)
        if {key[1] for key in page_keys} != {theme_code}:
            raise ValueError(
                "dc_concept_cons boundary query returned a different theme_code for "
                f"requested theme {theme_code}."
            )
        if seen_keys.intersection(page_keys):
            raise RuntimeError(
                "dc_concept_cons boundary-theme pagination pages overlap on business keys for "
                f"trade_date={request.trade_date}, theme_code={theme_code}."
            )
        seen_keys.update(page_keys)
        frames.append(page_frame)
        if len(page_frame) >= request.page_size:
            raise RuntimeError(
                "dc_concept_cons boundary-theme query reached page_size="
                f"{request.page_size} for trade_date={request.trade_date}, "
                f"theme_code={theme_code}; the "
                "filtered result has no stable sub-theme ordering, so completeness cannot be "
                "proved."
            )
        if len(page_frame) < request.page_size:
            terminal_page_reached = True
            break
    if not terminal_page_reached:
        raise RuntimeError(
            "dc_concept_cons boundary-theme pagination reached max_pages="
            f"{request.max_pages} for trade_date={request.trade_date}, "
            f"theme_code={theme_code} without a "
            "short terminal page."
        )
    if not frames:
        raise RuntimeError(
            "dc_concept_cons boundary-theme query returned no rows for observed theme_code="
            f"{theme_code}, trade_date={request.trade_date}."
        )
    return request.pandas.concat(frames, ignore_index=True), len(frames), request_count


def _completeness_receipt(
    frame: Any,
    *,
    trade_date: str,
    stats: _DcConceptConsPaginationStats,
) -> dict[str, Any]:
    row_count = int(len(frame))
    theme_codes = _nonblank_theme_codes(frame)
    populated_theme_rows = int(len(theme_codes))
    coverage_ratio = populated_theme_rows / row_count if row_count else None
    distinct_theme_count = int(theme_codes.nunique()) if populated_theme_rows else 0
    complete = bool(
        row_count
        and distinct_theme_count
        and populated_theme_rows == row_count
        and stats.last_page_row_count < stats.page_size
    )
    return {
        "trade_date": trade_date,
        "complete": complete,
        "row_count": row_count,
        "page_count": stats.page_count,
        "request_count": stats.request_count,
        "page_size": stats.page_size,
        "terminal_page_reached": True,
        "last_page_row_count": stats.last_page_row_count,
        "pagination_strategy": "offset_with_boundary_theme_repair",
        "offset_page_count": stats.offset_page_count,
        "offset_request_count": stats.offset_request_count,
        "repair_page_count": stats.repair_page_count,
        "repair_request_count": stats.repair_request_count,
        "boundary_theme_count": len(stats.boundary_theme_codes),
        "boundary_theme_codes": stats.boundary_theme_codes,
        "raw_row_count": stats.raw_row_count,
        "distinct_theme_count": distinct_theme_count,
        "coverage": {
            "field": "theme_code",
            "populated_row_count": populated_theme_rows,
            "row_coverage_ratio": coverage_ratio,
        },
    }


def _fetch_offset_pages(
    request: _DcConceptConsRequest,
) -> tuple[list[Any], set[str], int, int]:
    page_frames: list[Any] = []
    seen_business_keys: set[tuple[str, str, str]] = set()
    boundary_theme_codes: set[str] = set()
    request_count = 0
    last_page_row_count = request.page_size
    previous_theme_end: str | None = None
    for page_index in range(request.max_pages):
        page_frame = request.fetch_page(
            {
                **request.api_kwargs,
                "limit": request.page_size,
                "offset": page_index * request.page_size,
            }
        )
        request_count += 1
        last_page_row_count = int(len(page_frame))
        if page_frame.empty:
            return page_frames, boundary_theme_codes, request_count, last_page_row_count
        page_business_keys = _validated_business_keys(page_frame, trade_date=request.trade_date)
        theme_start, theme_end = _validated_theme_bounds(page_frame, trade_date=request.trade_date)
        if page_frames and page_frame.equals(page_frames[-1]):
            raise RuntimeError(
                "dc_concept_cons pagination repeated an earlier page for trade_date="
                f"{request.trade_date}; refusing to publish a possibly unstable partition."
            )
        boundary_theme: str | None = None
        if previous_theme_end is not None:
            if previous_theme_end > theme_start:
                raise RuntimeError(
                    "dc_concept_cons pagination theme ranges moved backwards for trade_date="
                    f"{request.trade_date}; boundary repair cannot prove completeness."
                )
            if previous_theme_end == theme_start:
                boundary_theme = theme_start
                boundary_theme_codes.add(theme_start)
        overlap = seen_business_keys.intersection(page_business_keys)
        if overlap and (boundary_theme is None or {key[1] for key in overlap} != {boundary_theme}):
            raise RuntimeError(
                "dc_concept_cons pagination pages overlap on business keys for trade_date="
                f"{request.trade_date} outside a repairable theme boundary."
            )
        if not page_business_keys.difference(seen_business_keys):
            raise RuntimeError(
                "dc_concept_cons pagination made no forward progress for trade_date="
                f"{request.trade_date}; refusing to publish a possibly unstable partition."
            )
        seen_business_keys.update(page_business_keys)
        page_frames.append(page_frame)
        previous_theme_end = theme_end
        if last_page_row_count < request.page_size:
            return page_frames, boundary_theme_codes, request_count, last_page_row_count
    raise RuntimeError(
        "dc_concept_cons pagination reached max_pages="
        f"{request.max_pages} for trade_date={request.trade_date} without a short terminal page; "
        "refusing to publish a truncated partition."
    )


def fetch_dc_concept_cons_pages(
    *,
    trade_date: str,
    api_kwargs: dict[str, Any],
    fetch_page: Callable[[dict[str, Any]], Any],
    pandas: Any,
    **pagination_options: int,
) -> DcConceptConsFetchResult:
    """Fetch one trade date until a short terminal page proves pagination is complete."""
    unknown_options = set(pagination_options) - {"page_size", "max_pages"}
    if unknown_options:
        raise TypeError(f"Unexpected pagination options: {sorted(unknown_options)}")
    resolved_page_size = int(pagination_options.get("page_size", DEFAULT_DC_CONCEPT_CONS_PAGE_SIZE))
    resolved_max_pages = int(pagination_options.get("max_pages", DEFAULT_DC_CONCEPT_CONS_MAX_PAGES))
    if resolved_page_size <= 0:
        raise ValueError("dc_concept_cons page_size must be positive.")
    if resolved_max_pages <= 0:
        raise ValueError("dc_concept_cons max_pages must be positive.")

    request = _DcConceptConsRequest(
        trade_date=trade_date,
        api_kwargs=api_kwargs,
        fetch_page=fetch_page,
        pandas=pandas,
        page_size=resolved_page_size,
        max_pages=resolved_max_pages,
    )
    page_frames, boundary_theme_codes, offset_request_count, last_page_row_count = (
        _fetch_offset_pages(request)
    )

    raw_frame = pandas.concat(page_frames, ignore_index=True) if page_frames else pandas.DataFrame()
    repair_frames: list[Any] = []
    repair_page_count = 0
    repair_request_count = 0
    sorted_boundary_themes = sorted(boundary_theme_codes)
    for theme_code in sorted_boundary_themes:
        repair_frame, theme_pages, theme_requests = _fetch_boundary_theme(
            request,
            theme_code=theme_code,
        )
        repair_frames.append(repair_frame)
        repair_page_count += theme_pages
        repair_request_count += theme_requests
    if raw_frame.empty:
        frame = raw_frame
    elif repair_frames:
        stable_rows = raw_frame.loc[
            ~raw_frame["theme_code"].astype(str).isin(sorted_boundary_themes)
        ]
        frame = pandas.concat([stable_rows, *repair_frames], ignore_index=True)
    else:
        frame = raw_frame
    if not frame.empty:
        _validated_business_keys(frame, trade_date=trade_date)
        frame = frame.sort_values(list(DC_CONCEPT_CONS_BUSINESS_KEY)).reset_index(drop=True)
    page_count = len(page_frames) + repair_page_count
    request_count = offset_request_count + repair_request_count
    return DcConceptConsFetchResult(
        frame=frame,
        completeness=_completeness_receipt(
            frame,
            trade_date=trade_date,
            stats=_DcConceptConsPaginationStats(
                page_count=page_count,
                request_count=request_count,
                page_size=resolved_page_size,
                last_page_row_count=last_page_row_count,
                offset_page_count=len(page_frames),
                offset_request_count=offset_request_count,
                repair_page_count=repair_page_count,
                repair_request_count=repair_request_count,
                boundary_theme_codes=sorted_boundary_themes,
                raw_row_count=int(len(raw_frame)),
            ),
        ),
    )
