"""Research-facing adapter for published market-data assets.

This module owns the small adapter used by research applications.  It accepts
published platform assets or a fixed local artifact and delegates provider
access to the market-data-platform public API.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .artifacts import resolve_data_input_path
from .data_provider_contracts import (
    normalize_market,
    require_supported_market,
    resolve_provider,
)
from .data_providers_public_api import fetch_daily, fetch_fundamentals, load_basic

PLATFORM_ASSET_SOURCE_MODE = "platform_assets"
FIXED_SCORED_ARTIFACT_SOURCE_MODE = "fixed_scored_artifact"
LOCAL_ARTIFACT_PROVIDER = "local_artifact"
AUTO_SOURCE_MODE = "auto"
ARCHIVED_ONLINE_SOURCE_MODES = {
    "provider_online_legacy",
    "provider_online",
    "online_provider",
    "provider",
}
SUPPORTED_SOURCE_MODES = {
    AUTO_SOURCE_MODE,
    FIXED_SCORED_ARTIFACT_SOURCE_MODE,
    PLATFORM_ASSET_SOURCE_MODE,
}
LOCAL_ARTIFACT_PATH_KEYS = ("scored_file", "panel_file", "daily_file", "file", "path")


def _resolve_source_mode(data_cfg: Mapping) -> str:
    value = str(data_cfg.get("source_mode") or AUTO_SOURCE_MODE).strip().lower()
    if value in ARCHIVED_ONLINE_SOURCE_MODES:
        raise SystemExit(
            "data.source_mode online provider reads are archived. Use platform_assets with "
            "published market-data-platform assets or fixed_scored_artifact with "
            "data.provider=local_artifact."
        )
    if value not in SUPPORTED_SOURCE_MODES:
        supported = ", ".join(sorted(SUPPORTED_SOURCE_MODES))
        raise SystemExit(f"data.source_mode must be one of: {supported}.")
    return value


@dataclass
class ResearchDataInterface:
    """Load daily, basic, and fundamentals data through owner contracts."""

    market: str
    data_cfg: Mapping
    cache_dir: Path
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("market_data_platform")
    )
    provider: str = field(init=False)
    client: object = field(init=False, default=None)
    _local_artifact_frame: pd.DataFrame | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.market = normalize_market(self.market) or "a_share"
        self.data_cfg = self.data_cfg if isinstance(self.data_cfg, Mapping) else {}
        provider_raw = str(self.data_cfg.get("provider") or "").strip().lower()
        if provider_raw in {LOCAL_ARTIFACT_PROVIDER, FIXED_SCORED_ARTIFACT_SOURCE_MODE}:
            self.provider = LOCAL_ARTIFACT_PROVIDER
        else:
            self.provider = resolve_provider(self.data_cfg) or ""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._init_client()

    def _init_client(self) -> None:
        """Validate source mode. Provider clients remain owned by the platform."""
        source_mode = _resolve_source_mode(self.data_cfg)
        if self.provider == LOCAL_ARTIFACT_PROVIDER:
            if source_mode not in {AUTO_SOURCE_MODE, FIXED_SCORED_ARTIFACT_SOURCE_MODE}:
                raise SystemExit(
                    "data.provider=local_artifact requires data.source_mode="
                    "fixed_scored_artifact or source_mode=auto."
                )
            return
        if source_mode == FIXED_SCORED_ARTIFACT_SOURCE_MODE:
            raise SystemExit(
                "data.source_mode=fixed_scored_artifact requires data.provider=local_artifact."
            )

    def _owner_market(self) -> str:
        try:
            return require_supported_market(self.market)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        if self.provider == LOCAL_ARTIFACT_PROVIDER:
            return self._fetch_daily_from_local_artifact(symbol, start_date, end_date)
        return fetch_daily(
            self._owner_market(), symbol, start_date, end_date, self.cache_dir, None, self.data_cfg
        )

    def load_basic(self, symbols: list[str] | None = None) -> pd.DataFrame | None:
        if self.provider == LOCAL_ARTIFACT_PROVIDER:
            return self._load_basic_from_local_artifact(symbols)
        return load_basic(
            self._owner_market(), self.cache_dir, None, self.data_cfg, symbols=symbols
        )

    def fetch_fundamentals(  # noqa: PLR0913
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        fundamentals_cfg: Mapping,
        *,
        cache_dir: Path | None = None,
        log_retry_failures: bool = True,
        log_retry_traceback: bool = True,
    ) -> pd.DataFrame | None:
        del log_retry_failures, log_retry_traceback
        if self.provider == LOCAL_ARTIFACT_PROVIDER:
            return pd.DataFrame()
        return fetch_fundamentals(
            self._owner_market(),
            symbol,
            start_date,
            end_date,
            cache_dir or self.cache_dir,
            None,
            self.data_cfg,
            fundamentals_cfg,
        )

    def _local_artifact_path(self) -> Path:
        nested = self.data_cfg.get(LOCAL_ARTIFACT_PROVIDER)
        nested = nested if isinstance(nested, Mapping) else {}
        for key in LOCAL_ARTIFACT_PATH_KEYS:
            value = self.data_cfg.get(key, nested.get(key))
            if value:
                return resolve_data_input_path(str(value))
        keys = ", ".join(f"data.{key}" for key in LOCAL_ARTIFACT_PATH_KEYS)
        raise SystemExit(
            "data.provider=local_artifact requires one local artifact path key: " + keys
        )

    def _load_local_artifact_frame(self) -> pd.DataFrame:
        if self._local_artifact_frame is not None:
            return self._local_artifact_frame
        path = self._local_artifact_path()
        if not path.exists():
            raise SystemExit(f"Local artifact data file not found: {path}")
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            frame = pd.read_parquet(path)
        elif suffix in {".csv", ".txt"}:
            frame = pd.read_csv(path)
        elif suffix in {".json", ".jsonl"}:
            frame = pd.read_json(path, lines=suffix == ".jsonl")
        else:
            raise SystemExit(
                f"Local artifact data file must be .parquet, .csv, .json, or .jsonl: {path}"
            )
        self._local_artifact_frame = self._normalize_local_artifact_frame(frame)
        return self._local_artifact_frame

    def _normalize_local_artifact_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        if "symbol" not in out.columns:
            for column in ("ts_code", "order_book_id", "stock_ticker"):
                if column in out.columns:
                    out["symbol"] = out[column]
                    break
        if "trade_date" not in out.columns and "date" in out.columns:
            out["trade_date"] = out["date"]
        missing = [column for column in ("trade_date", "symbol") if column not in out.columns]
        if missing:
            raise SystemExit(
                "Local artifact data file is missing required column(s): " + ", ".join(missing)
            )
        out["symbol"] = out["symbol"].astype(str).str.strip()
        out["trade_date"] = self._parse_local_artifact_dates(out["trade_date"])
        out = out.dropna(subset=["trade_date", "symbol"]).sort_values(["symbol", "trade_date"])
        return out.reset_index(drop=True)

    @staticmethod
    def _parse_local_artifact_dates(values: pd.Series) -> pd.Series:
        text = values.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        compact_mask = text.str.fullmatch(r"\d{8}")
        parsed = pd.to_datetime(text, errors="coerce")
        if compact_mask.any():
            parsed.loc[compact_mask] = pd.to_datetime(
                text.loc[compact_mask], format="%Y%m%d", errors="coerce"
            )
        return parsed.dt.normalize()

    def _fetch_daily_from_local_artifact(
        self, symbol: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        frame = self._load_local_artifact_frame()
        start_ts = pd.to_datetime(start_date, format="%Y%m%d", errors="coerce")
        end_ts = pd.to_datetime(end_date, format="%Y%m%d", errors="coerce")
        mask = frame["symbol"].astype(str).eq(str(symbol).strip())
        if pd.notna(start_ts):
            mask &= frame["trade_date"] >= start_ts.normalize()
        if pd.notna(end_ts):
            mask &= frame["trade_date"] <= end_ts.normalize()
        result = frame.loc[mask].copy()
        result.attrs["tr_close_meta"] = {
            "symbol": str(symbol).strip(),
            "source": LOCAL_ARTIFACT_PROVIDER,
            "configured_local_ex_factors": False,
            "local_ex_factors_available": None,
            "adjust_type": None,
        }
        return result

    def _load_basic_from_local_artifact(self, symbols: list[str] | None = None) -> pd.DataFrame:
        nested = self.data_cfg.get(LOCAL_ARTIFACT_PROVIDER)
        nested = nested if isinstance(nested, Mapping) else {}
        basic_file = self.data_cfg.get("basic_file", nested.get("basic_file"))
        if not basic_file:
            return pd.DataFrame()
        path = resolve_data_input_path(str(basic_file))
        if not path.exists():
            raise SystemExit(f"Local artifact basic file not found: {path}")
        basic = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)
        if symbols and "symbol" in basic.columns:
            symbol_set = {str(symbol).strip() for symbol in symbols}
            basic = basic.loc[basic["symbol"].astype(str).str.strip().isin(symbol_set)].copy()
        return basic
