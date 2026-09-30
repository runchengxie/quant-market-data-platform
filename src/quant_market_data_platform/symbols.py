from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

SYMBOL_COL = "symbol"
LEGACY_SYMBOL_COLUMNS = ("ts_code", "stock_ticker")
SYMBOL_INPUT_COLUMNS = ("symbol", "ts_code", "stock_ticker", "order_book_id")
DEFAULT_SYMBOL_PRIORITY = ("symbol", "ts_code", "stock_ticker", "order_book_id")
PROVIDER_SYMBOL_PRIORITY = ("ts_code", "stock_ticker", "order_book_id", "symbol")
A_SHARE_SUFFIX_EXCHANGES = (
    (".XSHG", ".SH"),
    (".XSHE", ".SZ"),
    (".SH", ".SH"),
    (".SZ", ".SZ"),
)
A_SHARE_SH_PREFIXES = ("5", "6", "9")
A_SHARE_SZ_PREFIXES = ("0", "2", "3")


def _strip_known_suffix(text: str, suffixes: Sequence[str]) -> str:
    for suffix in suffixes:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _a_share_exchange_for_code(code: str) -> str | None:
    if code.startswith(A_SHARE_SH_PREFIXES):
        return ".SH"
    if code.startswith(A_SHARE_SZ_PREFIXES):
        return ".SZ"
    return None


def _normalize_a_share_symbol(upper: str) -> str:
    for suffix, exchange in A_SHARE_SUFFIX_EXCHANGES:
        if upper.endswith(suffix):
            return f"{upper[: -len(suffix)].zfill(6)}{exchange}"

    if upper.isdigit():
        code = upper.zfill(6)
        exchange = _a_share_exchange_for_code(code)
        if exchange is not None:
            return f"{code}{exchange}"
    return upper


def normalize_symbol_for_market(value: object, *, market: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    market_text = str(market or "").strip().lower()
    upper = text.upper()
    if market_text == "a_share":
        return _normalize_a_share_symbol(upper)
    return text


def normalize_historical_hk_symbol(value: object) -> str:
    """Normalize archived HK symbols to the five-digit ``.HK`` form.

    This helper is for historical holdings and migration data only.  It does
    not enable HK as a current market-data provider.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    upper = text.upper()
    if upper.endswith(".XHKG"):
        upper = upper[:-5]
    elif upper.endswith(".HK"):
        upper = upper[:-3]
    return f"{upper.zfill(5)}.HK" if upper.isdigit() else text


def _column_series(df: pd.DataFrame, column: str) -> pd.Series:
    values = df.loc[:, column]
    if isinstance(values, pd.DataFrame):
        values = values.iloc[:, 0]
    return values


def _clean_symbol_series(values: pd.Series) -> pd.Series:
    series = values if isinstance(values, pd.Series) else pd.Series([values])
    return series.where(series.notna(), "").astype(str).str.strip()


def normalize_symbol_standard_name(name: object) -> str:
    text = str(name or "").strip()
    if text in {SYMBOL_COL, *LEGACY_SYMBOL_COLUMNS, "order_book_id"}:
        return SYMBOL_COL
    return text


def resolve_symbol_series(
    df: pd.DataFrame,
    *,
    context: str,
    priority: Sequence[str] = DEFAULT_SYMBOL_PRIORITY,
) -> pd.Series:
    present_columns = [column for column in priority if column in df.columns]
    if not present_columns:
        raise SystemExit(f"{context} is missing symbol/stock_ticker/ts_code/order_book_id.")

    merged = _clean_symbol_series(_column_series(df, present_columns[0]))
    for column in present_columns[1:]:
        series = _clean_symbol_series(_column_series(df, column))
        merged = merged.where(merged != "", series)
    return merged


def ensure_symbol_columns(
    df: pd.DataFrame,
    *,
    context: str,
    priority: Sequence[str] = DEFAULT_SYMBOL_PRIORITY,
) -> pd.DataFrame:
    normalized = df.copy()
    merged = resolve_symbol_series(normalized, context=context, priority=priority)

    normalized[SYMBOL_COL] = merged
    return normalized


def drop_legacy_symbol_columns(
    df: pd.DataFrame,
    *,
    drop_order_book_id: bool = False,
) -> pd.DataFrame:
    drop_columns = [*LEGACY_SYMBOL_COLUMNS]
    if drop_order_book_id:
        drop_columns.append("order_book_id")
    out = df.drop(columns=drop_columns, errors="ignore")
    out.attrs = dict(getattr(df, "attrs", {}))
    return out


def canonicalize_symbol_columns(
    df: pd.DataFrame,
    *,
    context: str,
    priority: Sequence[str] = DEFAULT_SYMBOL_PRIORITY,
    drop_order_book_id: bool = False,
) -> pd.DataFrame:
    normalized = ensure_symbol_columns(df, context=context, priority=priority)
    return drop_legacy_symbol_columns(
        normalized,
        drop_order_book_id=drop_order_book_id,
    )


def normalize_saved_holdings_symbols(
    df: pd.DataFrame,
    *,
    context: str,
    market: str | None = None,
) -> pd.DataFrame:
    """Canonicalize symbols loaded from a saved holdings artifact.

    Saved holdings can use provider-specific symbol columns and may contain
    historical Hong Kong symbols even though current provider support is
    limited to other markets.  This helper owns that input normalization so
    consumers do not each reimplement the compatibility rules.
    """

    normalized = canonicalize_symbol_columns(
        df,
        context=context,
        drop_order_book_id=True,
    )
    selected_market = str(market or "").strip().lower()
    if not selected_market:
        symbols = normalized[SYMBOL_COL].astype(str).str.strip().str.upper()
        if symbols.str.endswith((".HK", ".XHKG")).all():
            selected_market = "hk"

    if selected_market == "hk":
        normalized[SYMBOL_COL] = normalized[SYMBOL_COL].map(normalize_historical_hk_symbol)
    else:
        normalized[SYMBOL_COL] = normalized[SYMBOL_COL].map(
            lambda value: normalize_symbol_for_market(value, market=selected_market or None)
        )
    return normalized
