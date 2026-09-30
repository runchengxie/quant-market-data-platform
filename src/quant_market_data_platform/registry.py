from __future__ import annotations

import csv
import io
import os
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

DATASET_REGISTRY_COLUMNS = (
    "dataset_name",
    "version",
    "market",
    "type",
    "date_range",
    "source",
    "records",
    "symbols",
    "description",
    "path",
)

DATASET_REGISTRY_DESCRIPTIONS = {
    "current_contract": (
        "current {market_label} asset contract with resolved aliases and manifest summaries"
    ),
    "daily": "current {market_label} raw daily OHLCV asset",
    "trade_cal": "current {market_label} trading calendar",
    "adj_factor": "current {market_label} adjustment-factor history",
    "daily_basic": "current {market_label} daily valuation and capitalization metrics",
    "moneyflow": "current {market_label} raw moneyflow asset",
    "moneyflow_dc": "current {market_label} raw Eastmoney-style moneyflow asset",
    "moneyflow_hsgt": "current {market_label} raw Connect moneyflow asset",
    "top_inst": "current {market_label} raw top institutional trading event asset",
    "ths_hot": "current {market_label} raw THS hot-list asset",
    "dc_concept": "current {market_label} raw Eastmoney concept market asset",
    "dc_concept_cons": "current {market_label} raw Eastmoney concept constituents asset",
    "kpl_list": "current {market_label} raw KPL limit-up and failed-board list asset",
    "kpl_concept_cons": "current {market_label} raw KPL concept constituents asset",
    "limit_step": "current {market_label} raw limit-up streak ladder asset",
    "limit_cpt_list": "current {market_label} raw strongest limit-up concept board asset",
    "report_rc": "current {market_label} raw broker earnings-forecast report asset",
    "stk_surv": "current {market_label} raw institutional survey event asset",
    "broker_recommend": "current {market_label} raw broker monthly golden-stock asset",
    "fund_portfolio": "current {market_label} raw public fund stock holdings asset",
    "top10_holders": "current {market_label} raw top-10 shareholder asset",
    "top10_floatholders": "current {market_label} raw top-10 floating shareholder asset",
    "stk_holdertrade": ("current {market_label} raw shareholder increase/decrease event asset"),
    "ths_member": ("current {market_label} raw THS concept member asset"),
    "ths_index": ("current {market_label} raw THS concept index directory asset"),
    "moneyflow_ths": ("current {market_label} raw THS-style moneyflow asset"),
    "limit_list_ths": ("current {market_label} raw THS limit-up/down detail asset"),
    "margin_detail": ("current {market_label} raw margin-trading detail asset"),
    "margin": ("current {market_label} raw margin-trading summary asset"),
    "hsgt_top10": ("current {market_label} raw Connect top-10 trading list asset"),
    "daily_clean": "current {market_label} daily clean layer",
    "flow_ownership_features": (
        "current {market_label} derived moneyflow and ownership feature asset"
    ),
    "hotspot_features": (
        "current {market_label} derived hotspot theme and confirmation feature asset"
    ),
    "fund_portfolio_features": (
        "current {market_label} derived public fund ownership feature asset"
    ),
    "holder_structure_features": (
        "current {market_label} derived shareholder-structure feature asset"
    ),
    "top_inst_events": ("current {market_label} derived institutional trading event feature asset"),
    "holdertrade_events": (
        "current {market_label} derived shareholder increase/decrease event feature asset"
    ),
    "hsgt_market_features": (
        "current {market_label} derived Connect moneyflow market regime feature asset"
    ),
    "pit_fundamentals": "current {market_label} PIT fundamentals asset",
    "normalized_fundamentals": "current {market_label} normalized fundamentals asset",
    "intraday": "current {market_label} intraday 5m asset",
    "tick_depth_raw": "current {market_label} raw 10-level tick depth snapshot asset",
    "tick_depth_daily": (
        "current {market_label} daily aggregate derived from 10-level tick depth snapshots"
    ),
    "execution_cost_model": (
        "current {market_label} execution cost model calibrated from market microstructure assets"
    ),
    "etf_daily": "current {market_label} ETF raw daily asset",
    "etf_daily_clean": "current {market_label} ETF daily clean layer",
    "etf_instruments": "current {market_label} ETF instrument master",
    "valuation": "current {market_label} valuation factors",
    "instruments": "current {market_label} instrument master",
    "pit": "current {market_label} PIT fundamentals asset",
    "ex_factors": "current {market_label} ex-factor events",
    "dividends": "current {market_label} dividend events",
    "shares": "current {market_label} share capital events",
    "exchange_rate": "current {market_label} exchange-rate reference asset",
    "southbound": "current {market_label} Connect southbound eligibility asset",
    "financial_details": "current {market_label} financial details asset",
    "industry_changes": "current {market_label} industry membership changes",
    "industry": "current {market_label} industry labels",
    "industry_citic": "current {market_label} CITIC industry labels",
    "industry_sw": "current {market_label} Shenwan industry labels",
    "st_flags": "current {market_label} ST flag history",
    "stock_st": "current {market_label} ST stock list from TuShare stock_st",
    "namechange": "current {market_label} historical security-name intervals from TuShare",
    "margin_secs": (
        "current {market_label} margin eligibility upper bound, not borrow availability"
    ),
    "st_history_reconstructed": (
        "current {market_label} reconstructed daily ST history from namechange intervals"
    ),
    "st_intervals_reconstructed": (
        "current {market_label} reconstructed ST effective intervals from namechange"
    ),
    "index_weight": "current {market_label} index constituent weights from TuShare index_weight",
    "index_weight_daily": (
        "current {market_label} daily forward-expanded index weights with drift normalization"
    ),
    "stock_company": "current {market_label} listed company profiles from TuShare stock_company",
    "stk_managers": "current {market_label} shareholder manager history from TuShare stk_managers",
    "share_float": "current {market_label} share float history from TuShare share_float",
    "suspend": "current {market_label} suspension history",
    "limit_status": "current {market_label} limit-up/limit-down status history",
    "index_components": "current {market_label} index component history",
    "northbound": "current {market_label} northbound reference asset",
    "universe_by_date": "current {market_label} full-market universe by date",
    "universe_symbols": "current {market_label} latest full-market universe symbols",
    "universe_meta": "current {market_label} universe build metadata",
}

DATASET_REGISTRY_SOURCES = {
    "daily_clean": "derived",
    "flow_ownership_features": "derived",
    "hotspot_features": "derived",
    "fund_portfolio_features": "derived",
    "holder_structure_features": "derived",
    "top_inst_events": "derived",
    "holdertrade_events": "derived",
    "hsgt_market_features": "derived",
    "etf_daily_clean": "derived",
    "tick_depth_daily": "derived",
    "execution_cost_model": "derived",
    "universe_by_date": "derived",
    "universe_symbols": "derived",
    "universe_meta": "derived",
    "st_history_reconstructed": "derived",
    "st_intervals_reconstructed": "derived",
}


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _registry_date(value: object | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(\d{8})", text)
    if match:
        token = match.group(1)
        return f"{token[:4]}-{token[4:6]}-{token[6:8]}"
    return text


def _registry_path(path_text: object, *, artifacts_root: Path) -> str:
    path = Path(os.path.abspath(Path(str(path_text or "")).expanduser()))
    registry_root = Path(os.path.abspath(artifacts_root.parent))
    try:
        return path.relative_to(registry_root).as_posix()
    except ValueError:
        return str(path)


def _registry_records_text(entry: Mapping[str, Any]) -> str:
    manifest = _mapping(entry.get("manifest"))
    totals = _mapping(manifest.get("totals"))
    rows = totals.get("rows")
    files = totals.get("files")
    if rows is not None:
        return str(rows)
    if files is not None:
        return f"{files} files"
    return ""


def _registry_symbols_text(entry: Mapping[str, Any]) -> str:
    manifest = _mapping(entry.get("manifest"))
    totals = _mapping(manifest.get("totals"))
    for key in ("symbols_written", "symbols", "files"):
        if totals.get(key) is not None:
            return str(totals[key])
    return ""


def _registry_date_range(entry: Mapping[str, Any]) -> str:
    manifest = _mapping(entry.get("manifest"))
    start = _registry_date(manifest.get("query_start_date"))
    end = _registry_date(manifest.get("query_end_date") or entry.get("as_of"))
    if start and end:
        return f"{start} to {end}"
    if end:
        return f"as of {end}"
    return ""


def _market_label(market: str) -> str:
    if market == "a_share":
        return "A-share"
    return market.upper()


def _registry_description(asset_key: str, market: str) -> str:
    template = DATASET_REGISTRY_DESCRIPTIONS.get(
        asset_key,
        "current {market_label} {asset_key} asset",
    )
    return template.format(market_label=_market_label(market), asset_key=asset_key)


def _registry_current_path(raw_entry: Mapping[str, Any]) -> str:
    entry = _mapping(raw_entry)
    if entry.get("exists") is False:
        return ""
    return str(entry.get("alias_path") or entry.get("resolved_path") or "").strip()


def build_dataset_registry_rows(contract: Mapping[str, Any]) -> list[dict[str, str]]:
    contract_meta = _mapping(contract.get("contract"))
    target_date = str(contract_meta.get("target_date") or "").strip()
    assets = _mapping(contract.get("assets"))
    artifacts_root = Path(str(contract_meta.get("artifacts_root") or "artifacts")).resolve()
    contract_path = str(contract_meta.get("contract_path") or "").strip()
    market = str(contract_meta.get("market") or "a_share").strip().lower() or "a_share"
    provider = str(contract_meta.get("provider") or "tushare").strip().lower() or "tushare"
    contract_name = str(contract_meta.get("name") or f"{market}_current").strip()
    available_assets = sum(
        bool(_registry_current_path(raw_entry))
        for raw_entry in assets.values()
        if isinstance(raw_entry, Mapping)
    )
    rows: list[dict[str, str]] = []
    if contract_path:
        rows.append(
            {
                "dataset_name": f"{contract_name}_contract",
                "version": target_date,
                "market": market,
                "type": "metadata",
                "date_range": f"as of {_registry_date(target_date)}" if target_date else "",
                "source": "local",
                "records": f"{available_assets} available / {len(assets)} declared assets",
                "symbols": str(available_assets),
                "description": _registry_description("current_contract", market),
                "path": _registry_path(contract_path, artifacts_root=artifacts_root),
            }
        )
    for asset_key, raw_entry in assets.items():
        if not isinstance(raw_entry, Mapping):
            continue
        entry = _mapping(raw_entry)
        current_path = _registry_current_path(raw_entry)
        if not current_path:
            continue
        manifest = _mapping(entry.get("manifest"))
        as_of = str(entry.get("as_of") or manifest.get("query_end_date") or target_date).strip()
        version = re.sub(r"\D", "", as_of) or target_date
        rows.append(
            {
                "dataset_name": f"{market}_{asset_key}",
                "version": version,
                "market": market,
                "type": str(asset_key),
                "date_range": _registry_date_range(entry),
                "source": DATASET_REGISTRY_SOURCES.get(
                    str(asset_key),
                    str(manifest.get("provider") or provider),
                ),
                "records": _registry_records_text(raw_entry),
                "symbols": _registry_symbols_text(raw_entry),
                "description": _registry_description(str(asset_key), market),
                "path": _registry_path(current_path, artifacts_root=artifacts_root),
            }
        )
    return rows


def build_combined_dataset_registry_rows(
    contracts: Iterable[Mapping[str, Any]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for contract in contracts:
        rows.extend(build_dataset_registry_rows(contract))
    return rows


def _registry_market_scope(contracts: list[Mapping[str, Any]]) -> str:
    markets: list[str] = []
    for contract in contracts:
        contract_meta = _mapping(contract.get("contract"))
        market = str(contract_meta.get("market") or "a_share").strip().lower() or "a_share"
        if market not in markets:
            markets.append(market)
    if not markets:
        return "market"
    if len(markets) == 1:
        return _market_label(markets[0])
    return "/".join(_market_label(market) for market in markets)


def render_combined_dataset_registry_csv(
    contracts: Iterable[Mapping[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> str:
    contract_list = list(contracts)
    generated = generated_at or datetime.now().astimezone()
    market_scope = _registry_market_scope(contract_list)
    contract_names = []
    for contract in contract_list:
        contract_meta = _mapping(contract.get("contract"))
        market = str(contract_meta.get("market") or "a_share").strip().lower() or "a_share"
        contract_names.append(str(contract_meta.get("name") or f"{market}_current").strip())
    source_text = ", ".join(contract_names) if contract_names else "current contracts"
    buffer = io.StringIO()
    buffer.write(f"# Dataset Registry for current {market_scope} research data assets.\n")
    buffer.write(
        f"# Auto-generated from {source_text}; prefer each current contract plus each asset "
        "manifest for source-of-truth freshness.\n"
    )
    buffer.write(f"# Last updated: {generated.date().isoformat()}\n")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(DATASET_REGISTRY_COLUMNS)
    for row in build_combined_dataset_registry_rows(contract_list):
        writer.writerow([row.get(column, "") for column in DATASET_REGISTRY_COLUMNS])
    return buffer.getvalue()


def render_dataset_registry_csv(
    contract: Mapping[str, Any],
    *,
    generated_at: datetime | None = None,
) -> str:
    return render_combined_dataset_registry_csv([contract], generated_at=generated_at)


def write_dataset_registry(path: str | Path, contract: Mapping[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_dataset_registry_csv(contract), encoding="utf-8")


def write_combined_dataset_registry(
    path: str | Path,
    contracts: Iterable[Mapping[str, Any]],
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_combined_dataset_registry_csv(contracts), encoding="utf-8")
