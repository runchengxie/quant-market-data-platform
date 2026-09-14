"""Private classification helpers for A-share minute coverage."""

from __future__ import annotations

from collections.abc import Iterable

from market_data_platform.providers._coverage_common import (
    ANNUAL_FULL_SH_SZ,
    DEAL_FULL_SH_SZ,
    GUAN_PARTIAL_SESSION,
    MISSING,
    TUSHARE_FULL_A_SHARE,
    TUSHARE_PARTIAL_TOP200,
    CoverageTier,
    DailyCoverageExpectation,
    _CoverageClassificationRequest,
    _CoverageSourceDates,
    _date_set,
)


def _audit_only_sources(
    trade_date: str,
    sources: _CoverageSourceDates,
    canonical_source: str | None,
) -> tuple[str, ...]:
    available_sources = (
        (
            "guan_annual_minbar",
            trade_date in sources.annual_full or trade_date in sources.annual_partial,
        ),
        ("guan_deal", trade_date in sources.deal),
        ("tushare_top200", trade_date in sources.tushare),
        ("tushare_full_day", trade_date in sources.tushare_full),
    )
    return tuple(
        source
        for source, available in available_sources
        if available and source != canonical_source
    )


def _classify_minute_coverage_date(
    trade_date: str,
    sources: _CoverageSourceDates,
    *,
    partial_symbols: int,
    partial_bars_per_symbol: int,
) -> DailyCoverageExpectation:
    expected_rows: int | None = None
    expected_symbols: int | None = None
    if trade_date in sources.deal_overrides:
        tier: CoverageTier = DEAL_FULL_SH_SZ
        canonical_source = "guan_deal"
        market_scope = "SH_SZ"
    elif trade_date in sources.annual_full:
        tier = ANNUAL_FULL_SH_SZ
        canonical_source = "guan_annual_minbar"
        market_scope = "SH_SZ"
    elif trade_date in sources.annual_partial and trade_date in sources.tushare_full:
        tier = TUSHARE_FULL_A_SHARE
        canonical_source = "tushare_full_day"
        market_scope = "SH_SZ_BJ"
    elif trade_date in sources.annual_partial:
        tier = GUAN_PARTIAL_SESSION
        canonical_source = "guan_annual_minbar"
        market_scope = "SH_SZ_PARTIAL_SESSION"
    elif trade_date in sources.deal:
        tier = DEAL_FULL_SH_SZ
        canonical_source = "guan_deal"
        market_scope = "SH_SZ"
    elif trade_date in sources.tushare_full:
        tier = TUSHARE_FULL_A_SHARE
        canonical_source = "tushare_full_day"
        market_scope = "SH_SZ_BJ"
    elif trade_date in sources.tushare:
        tier = TUSHARE_PARTIAL_TOP200
        canonical_source = "tushare_top200"
        market_scope = "SH_SZ_TOP200"
        expected_rows = partial_symbols * partial_bars_per_symbol
        expected_symbols = partial_symbols
    else:
        tier = MISSING
        canonical_source = None
        market_scope = "NONE"
    return DailyCoverageExpectation(
        trade_date=trade_date,
        tier=tier,
        canonical_source=canonical_source,
        market_scope=market_scope,
        audit_only_sources=_audit_only_sources(trade_date, sources, canonical_source),
        expected_rows=expected_rows,
        expected_symbols=expected_symbols,
    )


def _classify_minute_coverage(
    request: _CoverageClassificationRequest,
) -> list[DailyCoverageExpectation]:
    if request.partial_symbols < 1 or request.partial_bars_per_symbol < 1:
        raise ValueError("Partial coverage dimensions must be positive")
    calendar = _date_set(request.trade_dates)
    annual = _date_set(request.annual_dates)
    annual_partial = _date_set(request.partial_session_annual_dates)
    annual_all = annual | annual_partial
    annual_full = annual_all - annual_partial
    deal = _date_set(request.deal_dates)
    deal_overrides = _date_set(request.deal_override_dates)
    tushare = _date_set(request.tushare_dates)
    tushare_full = _date_set(request.tushare_full_dates)
    if not deal_overrides.issubset(deal):
        raise ValueError(
            "Deal override dates must have a written Guan deal partition: "
            f"{sorted(deal_overrides - deal)}"
        )
    if not deal_overrides.issubset(annual_all):
        raise ValueError(
            "Deal override dates must replace a Guan annual partition: "
            f"{sorted(deal_overrides - annual_all)}"
        )

    sources = _CoverageSourceDates(
        annual_full=frozenset(annual_full),
        annual_partial=frozenset(annual_partial),
        deal=frozenset(deal),
        deal_overrides=frozenset(deal_overrides),
        tushare=frozenset(tushare),
        tushare_full=frozenset(tushare_full),
    )
    return [
        _classify_minute_coverage_date(
            trade_date,
            sources,
            partial_symbols=request.partial_symbols,
            partial_bars_per_symbol=request.partial_bars_per_symbol,
        )
        for trade_date in sorted(calendar)
    ]


def classify_minute_coverage(  # noqa: PLR0913
    trade_dates: Iterable[str],
    *,
    annual_dates: Iterable[str],
    deal_dates: Iterable[str],
    tushare_dates: Iterable[str],
    partial_session_annual_dates: Iterable[str] = (),
    deal_override_dates: Iterable[str] = (),
    tushare_full_dates: Iterable[str] = (),
    partial_symbols: int = 200,
    partial_bars_per_symbol: int = 241,
) -> list[DailyCoverageExpectation]:
    """Classify each trading date without mixing sources inside Guan dates."""
    return _classify_minute_coverage(
        _CoverageClassificationRequest(
            trade_dates=trade_dates,
            annual_dates=annual_dates,
            deal_dates=deal_dates,
            tushare_dates=tushare_dates,
            partial_session_annual_dates=partial_session_annual_dates,
            deal_override_dates=deal_override_dates,
            tushare_full_dates=tushare_full_dates,
            partial_symbols=partial_symbols,
            partial_bars_per_symbol=partial_bars_per_symbol,
        )
    )
