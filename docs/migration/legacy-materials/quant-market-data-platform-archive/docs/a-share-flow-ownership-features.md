# A 股资金流和持仓类特征

本文记录中国大陆市场 A 股资金流、机构持仓和股东结构数据的接入边界。平台侧负责下载、
清洗、PIT 化和发布资产。研究仓库只读消费发布后的 feature parquet。

## 权限判断

截至 2026-06-16，TuShare 官方积分表显示：

- 5000 分以上：常规数据每分钟 500 次，常规数据无总量上限。
- 10000 分以上：常规数据频次仍为每分钟 500 次，增加特色数据权限，特色数据每分钟 300 次。
- 15000 分以上：常规数据频次仍为每分钟 500 次，特色数据无总量限制。
- 分钟、新闻、公告、港美股等独立权限不随积分自动包含。

`TUSHARE_TOKEN_2` 当前按 15000 分账户使用。若该 token 需要代理域名，配置
`TUSHARE_API_URL_2` 或在命令中传 `--api-url`。这会改写 TuShare SDK client 的 API URL，
作用范围不涉及本机 HTTP 代理。

## 可用数据族

适合作为候选特征的 TuShare 数据族：

| 类型 | 接口示例 | 特征用途 | 注意事项 |
| --- | --- | --- | --- |
| 个股资金流 | `moneyflow`、`moneyflow_dc`、`moneyflow_ths` | 主力、大单、超大单净流入的滚动标准化特征 | 数据商口径，不等同于监管穿透机构账户 |
| 市场互联资金 | `moneyflow_hsgt` | 北向、南向资金状态和市场环境特征 | 更适合做市场级或行业级状态变量 |
| 公募基金持仓 | `fund_portfolio` | 公募持仓比例、持仓市值、环比变化 | 必须按披露日做 PIT，可用日取披露日后一交易日 |
| 龙虎榜机构明细 | `top_inst` | 稀疏事件特征 | 只覆盖龙虎榜上榜股票，不能当全市场机构交易 |
| 股东结构 | `top10_holders`、`top10_floatholders` | 持股集中度、机构股东比例、变动方向 | 更新慢，适合低频截面 |
| 股东增减持 | `stk_holdertrade` | 重要股东增减持事件特征 | 事件稀疏，缺失值不能直接过滤样本 |

## 推荐资产形态

先不要把这些字段直接塞进 `daily_clean`。推荐新增独立资产：

```text
tushare.a_share.moneyflow.v1
tushare.a_share.moneyflow_dc.v1
tushare.fund_portfolio.v1
tushare.a_share.fund_portfolio_features.v1
tushare.a_share.holder_pit.v1
tushare.a_share.top_inst_events.v1
tushare.a_share.flow_ownership_features.v1
```

MVP 可以先发布一个 feature parquet：

```text
assets/tushare/a_share/flow_ownership_features/a_share_all_flow_ownership_features_latest/data/trade_date=YYYYMMDD/part.parquet
assets/tushare/a_share/fund_portfolio_features/a_share_all_fund_portfolio_features_latest/data/trade_date=YYYYMMDD/part.parquet
```

最小字段约定：

```text
trade_date
symbol
available_date
mf_net_amount_20d_to_amount
mf_elg_net_amount_20d_to_amount
fund_hold_mv_to_float_mv
fund_hold_mv_qoq_change
top10_inst_hold_ratio
top_inst_net_buy_20d_to_amount
```

第一阶段已接入资金流链路：

```bash
marketdata tushare mirror-a-share-moneyflow \
  --start-date 20240101 --end-date 20260529 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/moneyflow/a_share_all_20240101_20260529_moneyflow" \
  --token-env TUSHARE_TOKEN_2

marketdata tushare build-a-share-flow-ownership-features \
  --moneyflow-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/moneyflow/a_share_all_20240101_20260529_moneyflow" \
  --daily-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily" \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_20240101_20260529_daily_basic" \
  --industry-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/industry_changes/a_share_all_industry_changes_latest" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/flow_ownership_features/a_share_all_flow_ownership_features_latest" \
  --min-rows 100000 --min-symbols 5000

marketdata tushare validate-a-share-flow-ownership-features \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/flow_ownership_features/a_share_all_flow_ownership_features_latest" \
  --min-rows 100000 --min-symbols 5000

marketdata tushare mirror-a-share-moneyflow-dc \
  --start-date 20240101 --end-date 20260529 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/moneyflow_dc/a_share_all_moneyflow_dc_latest" \
  --token-env TUSHARE_TOKEN_2 \
  --skip-existing

marketdata tushare mirror-a-share-moneyflow-hsgt \
  --start-date 20240101 --end-date 20260529 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/moneyflow_hsgt/a_share_all_moneyflow_hsgt_latest" \
  --token-env TUSHARE_TOKEN_2 \
  --skip-existing

marketdata tushare build-a-share-hsgt-market-features \
  --moneyflow-hsgt-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/moneyflow_hsgt/a_share_all_moneyflow_hsgt_latest" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/hsgt_market_features/a_share_all_hsgt_market_features_latest" \
  --window 20 --window 60 \
  --min-rows 100

marketdata tushare validate-a-share-hsgt-market-features \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/hsgt_market_features/a_share_all_hsgt_market_features_latest" \
  --min-rows 100
```

第一阶段实际生成 `mf_net_amount_*d_to_amount`、`mf_elg_net_amount_*d_to_amount`、
`mf_lg_net_amount_*d_to_amount`、`mf_net_amount_20d_to_float_mv` 和
`mf_buy_sell_imbalance_20d`，并额外生成 20 日截面 rank/zscore 特征。
传入 `--industry-dir` 时，还会生成 `mf_net_amount_20d_industry_zscore`。
`daily.amount` 是千元口径，构建时会除以 10 转成与 TuShare `moneyflow` 和
`moneyflow_dc` 金额字段一致的万元口径。

当前公募基金持仓链路按报告期分页下载 raw `fund_portfolio`，再按披露日后一交易日发布
PIT 特征：

```bash
marketdata tushare mirror-a-share-fund-portfolio \
  --start-date 20141231 --end-date 20260529 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_portfolio/a_share_all_20141231_20260529_fund_portfolio" \
  --token-env TUSHARE_TOKEN_2 \
  --page-size 8000 --max-pages-per-period 300 \
  --skip-existing

marketdata tushare build-a-share-fund-portfolio-features \
  --fund-portfolio-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_portfolio/a_share_all_20141231_20260529_fund_portfolio" \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_20150101_20260608_daily_basic" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_portfolio_features/a_share_all_fund_portfolio_features_latest" \
  --available-delay-days 1 \
  --min-rows 100000 --min-symbols 3000

marketdata tushare validate-a-share-fund-portfolio-features \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_portfolio_features/a_share_all_fund_portfolio_features_latest" \
  --min-rows 100000 --min-symbols 3000
```

派生特征包括 `fund_count_holding_stock`、`fund_hold_mv`、`fund_hold_amount`、
`fund_stk_mkv_ratio_sum`、`fund_stk_float_ratio_sum`、
`fund_hold_mv_to_total_mv`、`fund_hold_mv_to_float_mv`、
`fund_hold_amount_to_float_share` 以及相关环比变化。构建器按基金的最新已披露持仓维护
PIT 状态。基金下一次披露中不再持有的股票会写出 0 值行，避免下游前向填充留下陈旧持仓。

当前股东结构和龙虎榜机构事件链路使用独立 raw 资产与 derived 资产：

```bash
marketdata tushare mirror-a-share-top10-holders \
  --start-date 20150101 --end-date 20260529 \
  --symbols-file "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_symbols.txt" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/top10_holders/a_share_all_top10_holders_latest" \
  --token-env TUSHARE_TOKEN_2 \
  --skip-existing

marketdata tushare mirror-a-share-top10-floatholders \
  --start-date 20150101 --end-date 20260529 \
  --symbols-file "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_symbols.txt" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/top10_floatholders/a_share_all_top10_floatholders_latest" \
  --token-env TUSHARE_TOKEN_2 \
  --skip-existing

marketdata tushare build-a-share-holder-structure-features \
  --top10-holders-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/top10_holders/a_share_all_top10_holders_latest" \
  --top10-floatholders-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/top10_floatholders/a_share_all_top10_floatholders_latest" \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_daily_basic_latest" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/holder_structure_features/a_share_all_holder_structure_features_latest" \
  --available-delay-days 1 \
  --min-rows 100000 --min-symbols 3000

marketdata tushare validate-a-share-holder-structure-features \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/holder_structure_features/a_share_all_holder_structure_features_latest" \
  --min-rows 100000 --min-symbols 3000

marketdata tushare mirror-a-share-top-inst \
  --start-date 20240101 --end-date 20260529 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/top_inst/a_share_all_top_inst_latest" \
  --token-env TUSHARE_TOKEN_2 \
  --skip-existing

marketdata tushare build-a-share-top-inst-events \
  --top-inst-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/top_inst/a_share_all_top_inst_latest" \
  --daily-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_daily_latest" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/top_inst_events/a_share_all_top_inst_events_latest" \
  --window 20 \
  --min-rows 1000 --min-symbols 100

marketdata tushare validate-a-share-top-inst-events \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/top_inst_events/a_share_all_top_inst_events_latest" \
  --min-rows 1000 --min-symbols 100

marketdata tushare mirror-a-share-stk-holdertrade \
  --start-date 20150101 --end-date 20260529 \
  --symbols-file "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_symbols.txt" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/stk_holdertrade/a_share_all_stk_holdertrade_latest" \
  --token-env TUSHARE_TOKEN_2 \
  --skip-existing

marketdata tushare build-a-share-holdertrade-events \
  --stk-holdertrade-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/stk_holdertrade/a_share_all_stk_holdertrade_latest" \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_daily_basic_latest" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/holdertrade_events/a_share_all_holdertrade_events_latest" \
  --amount-window 20 --count-window 60 \
  --available-delay-days 1 \
  --min-rows 1000 --min-symbols 100

marketdata tushare validate-a-share-holdertrade-events \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/holdertrade_events/a_share_all_holdertrade_events_latest" \
  --min-rows 1000 --min-symbols 100
```

## PIT 和特征规则

- 资金流日频数据若用于收盘后生成信号、下一交易日交易，可以使用当日盘后可见值。若假设盘中交易，应至少滞后一日。
- 基金持仓、股东结构、增减持事件必须使用 `ann_date` / `disclosure_date` 生成
  `available_date`，不能用 `report_period` 直接对齐历史交易日。
- 金额类资金流必须除以成交额、自由流通市值或总市值，再做滚动窗口、截面 rank 或 zscore。
- 龙虎榜和增减持是稀疏事件，应保留缺失标记，不能用 complete-case 过滤训练样本。
- 新特征进入生产候选前，至少要跑覆盖率、缺失率、PIT 泄漏检查、单特征 IC、与既有技术特征相关性、
  benchmark ladder、CPCV、turnover 和 cost drag。

## 推荐顺序

1. 先做 `fund_portfolio` 公募基金持仓 PIT 特征。
1. 再做 `moneyflow` 或 `moneyflow_dc` 的滚动标准化资金流特征。
1. 再做 `top10_holders` / `top10_floatholders` 股东结构特征。
1. 最后做 `top_inst` 和 `stk_holdertrade` 稀疏事件特征。

研究仓库短期可以通过 `fundamentals.source=file` 加载外部 feature parquet 验证增量。长期应新增
独立的 `panel_features` / `alternative_features` 配置入口，避免把资金流和持仓特征误当基本面使用。
