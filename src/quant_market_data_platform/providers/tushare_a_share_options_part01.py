"""Configuration constants and option objects for TuShare A-share mirrors."""

from __future__ import annotations

DEFAULT_TOKEN_ENV_KEYS = ("TUSHARE_TOKEN", "TUSHARE_TOKEN_2")

DEFAULT_API_URL_ENV = "TUSHARE_API_URL"

DEFAULT_DISABLE_PROXY = True

DEFAULT_REQUEST_ATTEMPTS = 3

DEFAULT_RETRY_SLEEP_SECONDS = 2.0

DEFAULT_RETRY_MAX_SLEEP_SECONDS = 30.0

DEFAULT_QUOTA_COOLDOWN_SECONDS = 65.0

PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

NO_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy")

DEFAULT_LIST_STATUSES = ("L", "D", "P", "G")

DEFAULT_STOCK_BASIC_FIELDS = (
    "ts_code",
    "symbol",
    "name",
    "area",
    "industry",
    "fullname",
    "market",
    "exchange",
    "curr_type",
    "list_status",
    "list_date",
    "delist_date",
    "is_hs",
)

DEFAULT_DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)

DEFAULT_ADJ_FACTOR_FIELDS = (
    "ts_code",
    "trade_date",
    "adj_factor",
)

DEFAULT_FUND_DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)

DEFAULT_FUND_ADJ_FIELDS = (
    "ts_code",
    "trade_date",
    "adj_factor",
)

DEFAULT_DAILY_BASIC_FIELDS = (
    "ts_code",
    "trade_date",
    "close",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)

DEFAULT_LIMIT_STATUS_FIELDS = (
    "ts_code",
    "trade_date",
    "pre_close",
    "up_limit",
    "down_limit",
)

DEFAULT_MONEYFLOW_FIELDS = (
    "ts_code",
    "trade_date",
    "buy_sm_vol",
    "buy_sm_amount",
    "sell_sm_vol",
    "sell_sm_amount",
    "buy_md_vol",
    "buy_md_amount",
    "sell_md_vol",
    "sell_md_amount",
    "buy_lg_vol",
    "buy_lg_amount",
    "sell_lg_vol",
    "sell_lg_amount",
    "buy_elg_vol",
    "buy_elg_amount",
    "sell_elg_vol",
    "sell_elg_amount",
    "net_mf_vol",
    "net_mf_amount",
)

DEFAULT_MONEYFLOW_DC_FIELDS = (
    "ts_code",
    "trade_date",
    "name",
    "pct_change",
    "close",
    "net_amount",
    "net_amount_rate",
    "buy_elg_amount",
    "buy_elg_amount_rate",
    "buy_lg_amount",
    "buy_lg_amount_rate",
    "buy_md_amount",
    "buy_md_amount_rate",
    "buy_sm_amount",
    "buy_sm_amount_rate",
)

DEFAULT_MONEYFLOW_HSGT_FIELDS = (
    "trade_date",
    "ggt_ss",
    "ggt_sz",
    "hgt",
    "sgt",
    "north_money",
    "south_money",
)

DEFAULT_TOP_INST_FIELDS = (
    "trade_date",
    "ts_code",
    "exalter",
    "buy",
    "buy_rate",
    "sell",
    "sell_rate",
    "net_buy",
)

DEFAULT_THS_HOT_FIELDS = (
    "trade_date",
    "data_type",
    "ts_code",
    "ts_name",
    "rank",
    "pct_change",
    "current_price",
    "hot",
    "concept",
    "rank_time",
    "rank_reason",
)

DEFAULT_DC_CONCEPT_FIELDS = (
    "theme_code",
    "trade_date",
    "name",
    "pct_change",
    "hot",
    "sort",
    "strength",
    "z_t_num",
    "main_change",
    "lead_stock",
    "lead_stock_code",
    "lead_stock_pct_change",
)

DEFAULT_DC_CONCEPT_CONS_FIELDS = (
    "ts_code",
    "trade_date",
    "name",
    "theme_code",
    "industry_code",
    "industry",
    "reason",
    "hot_num",
)

DEFAULT_KPL_LIST_FIELDS = (
    "ts_code",
    "name",
    "trade_date",
    "lu_time",
    "ld_time",
    "open_time",
    "last_time",
    "lu_desc",
    "tag",
    "theme",
    "net_change",
    "bid_amount",
    "status",
    "bid_turnover",
    "lu_bid_vol",
    "pct_chg",
    "limit_order",
    "amount",
    "turnover_rate",
    "free_float",
    "lu_limit_order",
)

DEFAULT_KPL_CONCEPT_CONS_FIELDS = (
    "ts_code",
    "name",
    "con_name",
    "con_code",
    "trade_date",
    "desc",
    "hot_num",
)

DEFAULT_LIMIT_STEP_FIELDS = (
    "ts_code",
    "name",
    "trade_date",
    "nums",
)

DEFAULT_LIMIT_CPT_LIST_FIELDS = (
    "ts_code",
    "name",
    "trade_date",
    "days",
    "up_stat",
    "cons_nums",
    "up_nums",
    "pct_chg",
    "rank",
)

DEFAULT_REPORT_RC_FIELDS = (
    "ts_code",
    "name",
    "report_date",
    "report_title",
    "report_type",
    "classify",
    "org_name",
    "author_name",
    "quarter",
    "op_rt",
    "op_pr",
    "tp",
    "np",
    "eps",
    "pe",
    "rd",
    "roe",
    "ev_ebitda",
    "rating",
    "max_price",
    "min_price",
)

DEFAULT_STK_SURV_FIELDS = (
    "ts_code",
    "name",
    "surv_date",
    "fund_visitors",
    "rece_place",
    "rece_mode",
    "rece_org",
    "org_type",
    "comp_rece",
)

DEFAULT_BROKER_RECOMMEND_FIELDS = (
    "month",
    "broker",
    "ts_code",
    "name",
)

DEFAULT_FUND_PORTFOLIO_FIELDS = (
    "ts_code",
    "ann_date",
    "end_date",
    "symbol",
    "mkv",
    "amount",
    "stk_mkv_ratio",
    "stk_float_ratio",
)

DEFAULT_TOP10_HOLDER_FIELDS = (
    "ts_code",
    "ann_date",
    "end_date",
    "holder_name",
    "hold_amount",
    "hold_ratio",
    "hold_float_ratio",
    "hold_change",
    "holder_type",
)

DEFAULT_STK_HOLDERTRADE_FIELDS = (
    "ts_code",
    "ann_date",
    "holder_name",
    "holder_type",
    "in_de",
    "change_vol",
    "change_ratio",
    "after_share",
    "after_ratio",
    "avg_price",
    "total_share",
    "begin_date",
    "close_date",
)

DEFAULT_STK_AUCTION_FIELDS = (
    "ts_code",
    "trade_date",
)

DEFAULT_MONEYFLOW_THS_FIELDS = (
    "trade_date",
    "ts_code",
    "name",
    "net_amount",
    "buy_lg_amount",
    "buy_lg_amount_rate",
    "buy_md_amount",
    "buy_md_amount_rate",
    "buy_sm_amount",
    "buy_sm_amount_rate",
)

DEFAULT_LIMIT_LIST_THS_FIELDS = (
    "ts_code",
    "name",
    "trade_date",
    "limit_type",
    "pct_chg",
    "turnover_rate",
    "free_float",
    "lu_desc",
    "tag",
    "status",
)

DEFAULT_MARGIN_DETAIL_FIELDS = (
    "trade_date",
    "ts_code",
    "name",
    "rzye",
    "rqye",
    "rzmre",
    "rqyl",
    "rzche",
    "rqchl",
    "rqmcl",
    "rzrqye",
)

DEFAULT_MARGIN_FIELDS = (
    "trade_date",
    "exchange_id",
    "rzye",
    "rzmre",
    "rzche",
    "rqye",
    "rqyl",
    "rqchl",
    "rqmcl",
    "rzrqye",
)

DEFAULT_HSGT_TOP10_FIELDS = (
    "trade_date",
    "ts_code",
    "name",
    "close",
    "change",
    "rank",
    "market_type",
)

DEFAULT_THS_INDEX_FIELDS = (
    "ts_code",
    "name",
    "count",
    "exchange",
    "list_date",
    "type",
)

DEFAULT_THS_MEMBER_FIELDS = (
    "ts_code",
    "con_code",
    "name",
    "con_name",
    "in_date",
    "out_date",
    "is_new",
)

TRADE_DATE_APIS = {
    "daily": "daily",
    "adj_factor": "adj_factor",
    "daily_basic": "daily_basic",
    "limit_status": "stk_limit",
    "moneyflow": "moneyflow",
    "moneyflow_dc": "moneyflow_dc",
    "moneyflow_hsgt": "moneyflow_hsgt",
    "top_inst": "top_inst",
    "ths_hot": "ths_hot",
    "dc_concept": "dc_concept",
    "dc_concept_cons": "dc_concept_cons",
    "kpl_list": "kpl_list",
    "kpl_concept_cons": "kpl_concept_cons",
    "limit_step": "limit_step",
    "limit_cpt_list": "limit_cpt_list",
    "stk_auction_o": "stk_auction_o",
    "stk_auction_c": "stk_auction_c",
    "fund_daily": "fund_daily",
    "fund_adj": "fund_adj",
    "moneyflow_ths": "moneyflow_ths",
    "limit_list_ths": "limit_list_ths",
    "margin_detail": "margin_detail",
    "margin": "margin",
    "hsgt_top10": "hsgt_top10",
}
