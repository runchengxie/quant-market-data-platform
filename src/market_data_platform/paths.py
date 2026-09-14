from __future__ import annotations

from pathlib import Path

from .artifacts import resolve_artifacts_root as _resolve_platform_artifacts_root

DATA_PLATFORM_ROOT_ENV = "DATA_PLATFORM_ROOT"

SUPPORTED_MARKETS = {"a_share", "cn_context"}
SUPPORTED_PROVIDERS_BY_MARKET = {
    "a_share": {"tushare"},
    "cn_context": {"composite"},
}
DEFAULT_PROVIDER_BY_MARKET = {
    "a_share": "tushare",
    "cn_context": "composite",
}


def normalize_market(market: str | None = None) -> str:
    value = str(market or "a_share").strip().lower()
    if value not in SUPPORTED_MARKETS:
        supported = ", ".join(sorted(SUPPORTED_MARKETS))
        raise ValueError(f"Unsupported market '{value}'. Supported markets: {supported}.")
    return value


def normalize_provider(provider: str | None = None, *, market: str | None = None) -> str:
    market = normalize_market(market)
    value = str(provider or DEFAULT_PROVIDER_BY_MARKET[market]).strip().lower()
    supported = SUPPORTED_PROVIDERS_BY_MARKET[market]
    if value not in supported:
        available = ", ".join(sorted(supported))
        raise ValueError(
            f"Unsupported provider '{value}' for market '{market}'. Supported providers: "
            f"{available}."
        )
    return value


def current_contract_relative_path(market: str | None = None) -> Path:
    if str(market or "").strip().lower() == "hk":
        return Path("metadata") / "current_assets" / "hk_current.json"
    market = normalize_market(market)
    return Path("metadata") / "current_assets" / f"{market}_current.json"


CURRENT_CONTRACT_RELATIVE_PATH = current_contract_relative_path("a_share")
DATASET_REGISTRY_RELATIVE_PATH = Path("metadata") / "dataset_registry.csv"


TUSHARE_A_SHARE_ASSET_PATH_SPECS: dict[str, tuple[str, ...]] = {
    "instruments": (
        "assets",
        "tushare",
        "a_share",
        "instruments",
        "a_share_all_instruments_latest.parquet",
    ),
    "trade_cal": ("assets", "tushare", "a_share", "trade_cal", "a_share_trade_cal_latest.parquet"),
    "daily": ("assets", "tushare", "a_share", "daily", "a_share_all_daily_latest"),
    "adj_factor": (
        "assets",
        "tushare",
        "a_share",
        "adj_factor",
        "a_share_all_adj_factor_latest",
    ),
    "daily_basic": (
        "assets",
        "tushare",
        "a_share",
        "daily_basic",
        "a_share_all_daily_basic_latest",
    ),
    "limit_status": (
        "assets",
        "tushare",
        "a_share",
        "limit_status",
        "a_share_limit_status_latest",
    ),
    "moneyflow": (
        "assets",
        "tushare",
        "a_share",
        "moneyflow",
        "a_share_all_moneyflow_latest",
    ),
    "moneyflow_dc": (
        "assets",
        "tushare",
        "a_share",
        "moneyflow_dc",
        "a_share_all_moneyflow_dc_latest",
    ),
    "moneyflow_hsgt": (
        "assets",
        "tushare",
        "a_share",
        "moneyflow_hsgt",
        "a_share_all_moneyflow_hsgt_latest",
    ),
    "top_inst": (
        "assets",
        "tushare",
        "a_share",
        "top_inst",
        "a_share_all_top_inst_latest",
    ),
    "ths_hot": (
        "assets",
        "tushare",
        "a_share",
        "ths_hot",
        "a_share_all_ths_hot_latest",
    ),
    "dc_concept": (
        "assets",
        "tushare",
        "a_share",
        "dc_concept",
        "a_share_all_dc_concept_latest",
    ),
    "dc_concept_cons": (
        "assets",
        "tushare",
        "a_share",
        "dc_concept_cons",
        "a_share_all_dc_concept_cons_latest",
    ),
    "kpl_list": (
        "assets",
        "tushare",
        "a_share",
        "kpl_list",
        "a_share_all_kpl_list_latest",
    ),
    "kpl_concept_cons": (
        "assets",
        "tushare",
        "a_share",
        "kpl_concept_cons",
        "a_share_all_kpl_concept_cons_latest",
    ),
    "limit_step": (
        "assets",
        "tushare",
        "a_share",
        "limit_step",
        "a_share_all_limit_step_latest",
    ),
    "limit_cpt_list": (
        "assets",
        "tushare",
        "a_share",
        "limit_cpt_list",
        "a_share_all_limit_cpt_list_latest",
    ),
    "report_rc": (
        "assets",
        "tushare",
        "a_share",
        "report_rc",
        "a_share_all_report_rc_latest",
    ),
    "stk_surv": (
        "assets",
        "tushare",
        "a_share",
        "stk_surv",
        "a_share_all_stk_surv_latest",
    ),
    "broker_recommend": (
        "assets",
        "tushare",
        "a_share",
        "broker_recommend",
        "a_share_all_broker_recommend_latest",
    ),
    "fund_portfolio": (
        "assets",
        "tushare",
        "a_share",
        "fund_portfolio",
        "a_share_all_fund_portfolio_latest",
    ),
    "top10_holders": (
        "assets",
        "tushare",
        "a_share",
        "top10_holders",
        "a_share_all_top10_holders_latest",
    ),
    "top10_floatholders": (
        "assets",
        "tushare",
        "a_share",
        "top10_floatholders",
        "a_share_all_top10_floatholders_latest",
    ),
    "stk_holdertrade": (
        "assets",
        "tushare",
        "a_share",
        "stk_holdertrade",
        "a_share_all_stk_holdertrade_latest",
    ),
    "ths_member": (
        "assets",
        "tushare",
        "a_share",
        "ths_member",
        "a_share_all_ths_member_latest",
    ),
    "ths_index": (
        "assets",
        "tushare",
        "a_share",
        "ths_index",
        "a_share_all_ths_index_latest",
    ),
    "moneyflow_ths": (
        "assets",
        "tushare",
        "a_share",
        "moneyflow_ths",
        "a_share_all_moneyflow_ths_latest",
    ),
    "limit_list_ths": (
        "assets",
        "tushare",
        "a_share",
        "limit_list_ths",
        "a_share_all_limit_list_ths_latest",
    ),
    "margin_detail": (
        "assets",
        "tushare",
        "a_share",
        "margin_detail",
        "a_share_all_margin_detail_latest",
    ),
    "margin": (
        "assets",
        "tushare",
        "a_share",
        "margin",
        "a_share_all_margin_latest",
    ),
    "hsgt_top10": (
        "assets",
        "tushare",
        "a_share",
        "hsgt_top10",
        "a_share_all_hsgt_top10_latest",
    ),
    "daily_clean": ("assets", "tushare", "a_share", "daily", "a_share_all_daily_clean_latest"),
    "minute_1m_tushare": (
        "assets",
        "derived",
        "a_share",
        "minute_1m_tushare",
    ),
    "flow_ownership_features": (
        "assets",
        "tushare",
        "a_share",
        "flow_ownership_features",
        "a_share_all_flow_ownership_features_latest",
    ),
    "hotspot_features": (
        "assets",
        "tushare",
        "a_share",
        "hotspot_features",
        "a_share_all_hotspot_features_latest",
    ),
    "fund_portfolio_features": (
        "assets",
        "tushare",
        "a_share",
        "fund_portfolio_features",
        "a_share_all_fund_portfolio_features_latest",
    ),
    "holder_structure_features": (
        "assets",
        "tushare",
        "a_share",
        "holder_structure_features",
        "a_share_all_holder_structure_features_latest",
    ),
    "top_inst_events": (
        "assets",
        "tushare",
        "a_share",
        "top_inst_events",
        "a_share_all_top_inst_events_latest",
    ),
    "holdertrade_events": (
        "assets",
        "tushare",
        "a_share",
        "holdertrade_events",
        "a_share_all_holdertrade_events_latest",
    ),
    "hsgt_market_features": (
        "assets",
        "tushare",
        "a_share",
        "hsgt_market_features",
        "a_share_all_hsgt_market_features_latest",
    ),
    "pit_fundamentals": (
        "assets",
        "tushare",
        "a_share",
        "pit_fundamentals",
        "a_share_all_pit_fundamentals_latest",
    ),
    "normalized_fundamentals": (
        "assets",
        "tushare",
        "a_share",
        "normalized_fundamentals",
        "a_share_all_normalized_fundamentals_latest",
    ),
    "industry_changes": (
        "assets",
        "tushare",
        "a_share",
        "industry_changes",
        "a_share_all_industry_changes_latest",
    ),
    "universe_by_date": ("assets", "universe", "a_share_all_full_by_date.csv"),
    "universe_symbols": ("assets", "universe", "a_share_all_full_symbols.txt"),
    "universe_meta": ("assets", "universe", "a_share_all_full_by_date.meta.yml"),
    # Reference datasets consolidated from the provider jobs (2026-07-29 assessment).
    "stock_st": (
        "assets",
        "tushare",
        "a_share",
        "stock_st",
        "a_share_all_stock_st_latest.parquet",
    ),
    "namechange": (
        "assets",
        "tushare",
        "a_share",
        "namechange",
        "a_share_all_namechange_latest.parquet",
    ),
    "margin_secs": (
        "assets",
        "tushare",
        "a_share",
        "margin_secs",
        "a_share_all_margin_secs_latest.parquet",
    ),
    "st_history_reconstructed": (
        "assets",
        "tushare",
        "a_share",
        "st_history_reconstructed",
        "a_share_all_st_history_reconstructed_latest.parquet",
    ),
    "st_intervals_reconstructed": (
        "assets",
        "tushare",
        "a_share",
        "st_intervals_reconstructed",
        "a_share_all_st_intervals_reconstructed_latest.parquet",
    ),
    "index_weight": (
        "assets",
        "tushare",
        "a_share",
        "index_weight",
        "a_share_all_index_weight_latest.parquet",
    ),
    "index_weight_daily": (
        "assets",
        "tushare",
        "a_share",
        "index_weight_daily",
        "a_share_all_index_weight_daily_latest.parquet",
    ),
    "stock_company": (
        "assets",
        "tushare",
        "a_share",
        "stock_company",
        "a_share_all_stock_company_latest.parquet",
    ),
    "stk_managers": (
        "assets",
        "tushare",
        "a_share",
        "stk_managers",
        "a_share_all_stk_managers_latest.parquet",
    ),
    "share_float": (
        "assets",
        "tushare",
        "a_share",
        "share_float",
        "a_share_all_share_float_latest.parquet",
    ),
}

CN_CONTEXT_ASSET_PATH_SPECS: dict[str, tuple[str, ...]] = {
    "context_catalog": (
        "assets",
        "context",
        "cn",
        "catalog",
        "cn_context_catalog_latest.parquet",
    ),
    "context_observations": (
        "assets",
        "context",
        "cn",
        "normalized",
        "cn_context_observations_latest",
    ),
    "context_pit": (
        "assets",
        "context",
        "cn",
        "pit",
        "cn_context_pit_latest",
    ),
    "context_release_calendar": (
        "assets",
        "context",
        "cn",
        "release_calendar",
        "cn_context_release_calendar_latest.parquet",
    ),
}

ASSET_PATH_SPECS_BY_MARKET_PROVIDER: dict[str, dict[str, dict[str, tuple[str, ...]]]] = {
    "a_share": {
        "tushare": TUSHARE_A_SHARE_ASSET_PATH_SPECS,
    },
    "cn_context": {
        "composite": CN_CONTEXT_ASSET_PATH_SPECS,
    },
}

# Backward-compatible alias for the current default market/provider.
ASSET_PATH_SPECS = TUSHARE_A_SHARE_ASSET_PATH_SPECS


def resolve_artifacts_root(value: str | Path | None = None) -> Path:
    return _resolve_platform_artifacts_root(value)


def current_contract_path(
    artifacts_root: str | Path | None = None,
    *,
    market: str | None = None,
) -> Path:
    return resolve_artifacts_root(artifacts_root) / current_contract_relative_path(market)


def dataset_registry_path(artifacts_root: str | Path | None = None) -> Path:
    return resolve_artifacts_root(artifacts_root) / DATASET_REGISTRY_RELATIVE_PATH


def candidate_asset_paths(
    artifacts_root: str | Path | None = None,
    *,
    market: str | None = None,
    provider: str | None = None,
) -> dict[str, Path]:
    root = resolve_artifacts_root(artifacts_root)
    market = normalize_market(market)
    provider = normalize_provider(provider, market=market)
    specs = ASSET_PATH_SPECS_BY_MARKET_PROVIDER[market][provider]
    return {key: root.joinpath(*parts) for key, parts in specs.items()}
