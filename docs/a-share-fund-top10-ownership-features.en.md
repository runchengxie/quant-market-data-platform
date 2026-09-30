# A-Share Fund Top-10 Ownership PIT Features

[中文页面](a-share-fund-top10-ownership-features.md)

This page documents the consistent-disclosure-scope fund ownership features built from TuShare `fund_portfolio`.

## Why this is a separate asset

The stock detail disclosed by `fund_portfolio` varies by report type. Quarterly reports generally expose the top ten stock investments, while half-year and annual reports may expose broader holdings. Treating every disclosure as a complete portfolio replacement would incorrectly mark holdings outside a quarterly report's disclosed scope as sold.

`fund_top10_portfolio_features` leaves `fund_portfolio_features` unchanged. Before entering the PIT state machine, it selects the ten largest stock holdings by `mkv` for each fund, report period, and available disclosure. The asset counts funds reporting a stock among their top ten and measures the aggregate value and share of those disclosed positions. It is not a count of all public funds holding the stock or a measure of their complete ownership.

## Build and validate

Download the raw `fund_portfolio` data with the existing mirror command, then build the feature asset:

```bash
marketdata tushare build-a-share-fund-top10-portfolio-features \
  --fund-portfolio-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_portfolio/a_share_all_fund_portfolio_latest" \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_daily_basic_latest" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_top10_portfolio_features/a_share_all_fund_top10_portfolio_features_latest" \
  --available-delay-days 1 \
  --top-n 10 \
  --min-rows 100000 \
  --min-symbols 3000

marketdata tushare validate-a-share-fund-top10-portfolio-features \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_top10_portfolio_features/a_share_all_fund_top10_portfolio_features_latest" \
  --min-rows 100000 \
  --min-symbols 3000
```

`--top-n` defaults to 10. The research-standard definition uses 10; the option is primarily for focused diagnostics and different values should not be mixed in one historical series.

## Point-in-time rules

- `ann_date` is treated as the disclosure date.
- By default, the state becomes available on the first eligible trading day after disclosure (`available_delay_days=1`).
- A fund's newly disclosed top-ten state replaces its previous top-ten state.
- Stocks that leave the top ten are emitted with a zero state to prevent stale holdings from surviving downstream forward fills.
- The platform publishes disclosure-event states; it does not calculate quarterly-change fields. Research consumers should perform a backward as-of join at a consistent formation date, such as month-end, before calculating changes between formation dates.

## Main fields

| Field | Meaning |
| --- | --- |
| `fund_top10_count_holding_stock` | Funds whose currently known top-ten holdings include the stock |
| `fund_top10_hold_mv` | Aggregate market value of those disclosed top-ten positions |
| `fund_top10_hold_amount` | Aggregate number of shares in those disclosed positions |
| `fund_top10_stk_mkv_ratio_sum` | Sum of each fund's position value as a share of its stock investment value |
| `fund_top10_stk_float_ratio_sum` | Sum of each fund's position as a share of the stock's float |
| `fund_top10_hold_mv_to_total_mv` | Aggregate top-ten position value divided by total market capitalization |
| `fund_top10_hold_mv_to_float_mv` | Aggregate top-ten position value divided by float market capitalization |
| `fund_top10_hold_amount_to_float_share` | Aggregate top-ten shares divided by floating shares |

## Research limitations

Fund share classes such as A and C may represent the same underlying portfolio, so the fund count is not a strict count of independent managers. The asset does not separate ETFs, passive index funds, and active equity funds. Its count is naturally exposed to capitalization, liquidity, and index membership, which downstream research should control for. These are low-frequency disclosure features, not daily flow measures. Treat them as candidate breadth or crowding signals, not as a complete shareholder register.
