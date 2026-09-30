# A-Share Flow and Ownership Features

[中文页面](a-share-flow-ownership-features.md)

This page defines the integration boundary for A-share money flow, institutional holdings, and shareholder-structure data. The platform retrieves, cleans, applies point-in-time (PIT) availability rules, and publishes assets. Research repositories consume the published feature Parquet files read-only.

## Provider permissions

As of 2026-06-16, the TuShare points table states that accounts above 5,000 points receive 500 requests per minute for standard data with no standard-data total limit. Above 10,000 points, the standard-data rate is unchanged and access to specialty data is added at 300 requests per minute. Above 15,000 points, specialty data has no total limit. Minute, news, announcements, and HK/US market access are separate permissions and are not included automatically.

`TUSHARE_TOKEN_2` is currently used as a 15,000-point account. If that token requires a proxy domain, configure `TUSHARE_API_URL_2` or pass `--api-url`. This changes the TuShare SDK client's API URL; it does not configure the machine's HTTP proxy.

## Candidate data families

| Family | TuShare interfaces | Candidate use | Limitation |
| --- | --- | --- | --- |
| Equity money flow | `moneyflow`, `moneyflow_dc`, `moneyflow_ths` | Rolling standardized net inflow for large and extra-large orders | Vendor-defined measures, not regulator-verified institutional accounts |
| Cross-market flows | `moneyflow_hsgt` | Northbound/southbound state and market context | Better suited to market- or industry-level features |
| Public-fund holdings | `fund_portfolio` | Holdings, market value, and changes | Must be PIT by disclosure date; default availability is the next trading day |
| Dragon-Tiger institutional details | `top_inst` | Sparse event features | Covers only listed stocks, not all institutional trading |
| Shareholder structure | `top10_holders`, `top10_floatholders` | Concentration and institutional-holder measures | Slow updates; more suitable for low-frequency cross-sections |
| Shareholder transactions | `stk_holdertrade` | Major-holder increase/decrease events | Sparse events; missing values must not filter samples directly |

## Asset boundaries and available pipelines

Keep these features outside `daily_clean`. The repository uses separate raw and derived assets, including `flow_ownership_features`, `fund_portfolio_features`, `holder_structure_features`, `top_inst_events`, `holdertrade_events`, and `hsgt_market_features`. Exact asset paths and CLI invocations are maintained in the Chinese companion; machine-readable options and output keys are unchanged between locales.

The money-flow pipeline mirrors raw `moneyflow`, then builds and validates derived features. Current generated measures include `mf_net_amount_*d_to_amount`, `mf_elg_net_amount_*d_to_amount`, `mf_lg_net_amount_*d_to_amount`, `mf_net_amount_20d_to_float_mv`, `mf_buy_sell_imbalance_20d`, and 20-day cross-sectional ranks/z-scores. When `--industry-dir` is provided, it also builds `mf_net_amount_20d_industry_zscore`. `daily.amount` is in thousands of yuan; the builder divides it by 10 to match the ten-thousand-yuan unit used by TuShare money-flow amount fields.

The public-fund pipeline mirrors raw `fund_portfolio` by report period, then publishes PIT features using the first eligible trading day after disclosure. It includes holding-fund counts, holding market value and amount, ratios to total/float market value and share count, and related quarter-over-quarter changes. The builder maintains each fund's latest disclosed state and emits zero rows for stocks no longer held in the next disclosure, preventing downstream forward fills from retaining stale positions.

Separate raw assets also feed shareholder-structure, top-institution, and shareholder-transaction event builders. Use each builder's matching `validate-a-share-*` command and the thresholds recorded in the companion before treating an output as a candidate asset.

## PIT and feature rules

- Same-day money-flow data may be used for a signal formed after the close and traded on the next session. Intraday-trading assumptions require at least a one-day lag.
- Fund holdings, shareholder structure, and holder transactions derive `available_date` from `ann_date` or `disclosure_date`. Do not align historical observations using `report_period` alone.
- Normalize money-flow amounts by turnover, float market value, or total market value before calculating rolling windows, cross-sectional ranks, or z-scores.
- Dragon-Tiger and shareholder-transaction data are sparse events. Preserve missingness instead of applying complete-case filtering.
- Before promotion to a production research candidate, evaluate coverage, missingness, PIT leakage, single-feature IC, correlation with existing technical features, benchmark ladder, CPCV, turnover, and cost drag.

## Suggested research sequence

1. Build PIT features from `fund_portfolio`.
2. Add rolling standardized money-flow features from `moneyflow` or `moneyflow_dc`.
3. Add shareholder-structure features from `top10_holders` and `top10_floatholders`.
4. Add sparse event features from `top_inst` and `stk_holdertrade`.

For short-term incremental validation, a research repository can load external feature Parquet through `fundamentals.source=file`. Longer term, these data should use a separate `panel_features` or `alternative_features` configuration entry so money-flow and ownership features are not represented as fundamentals.
