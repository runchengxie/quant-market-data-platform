# A 股公募基金前十大重仓 PIT 特征

本页记录从 TuShare `fund_portfolio` 构建一致披露口径公募基金持仓特征的规则。

## 为什么单独建 top-10 资产

`fund_portfolio` 在不同定期报告中的股票明细范围并不稳定。季度报告通常只公开前十大股票投资明细，半年报和年度报告可能公开更完整的股票投资明细。如果直接把每次披露都当作完整组合替换，报告类型切换时会把未进入本期披露范围误判为基金已经卖出。

`fund_top10_portfolio_features` 不修改现有 `fund_portfolio_features`。它在进入 PIT 状态机之前，统一把每只基金、每个报告期、每次可用披露截取为按 `mkv` 排名最大的前 10 个股票持仓。因此季度、中期和年度披露使用同一可观察范围。

这个资产统计把该股票列入前十大重仓的公募基金数量，以及这些前十大重仓合计占多少流通股。该口径不能解释成完整公募股东人数或完整公募持股比例。

## 构建

先使用现有 raw 下载命令获取 `fund_portfolio`，再构建一致口径特征：

```bash
marketdata tushare build-a-share-fund-top10-portfolio-features \
  --fund-portfolio-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_portfolio/a_share_all_fund_portfolio_latest" \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_daily_basic_latest" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_top10_portfolio_features/a_share_all_fund_top10_portfolio_features_latest" \
  --available-delay-days 1 \
  --top-n 10 \
  --min-rows 100000 \
  --min-symbols 3000
```

校验：

```bash
marketdata tushare validate-a-share-fund-top10-portfolio-features \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_top10_portfolio_features/a_share_all_fund_top10_portfolio_features_latest" \
  --min-rows 100000 \
  --min-symbols 3000
```

`--top-n` 默认是 10。研究标准口径固定使用 10，该参数主要用于定点诊断，不应在同一历史序列中混用不同值。

## PIT 规则

- 原始 `ann_date` 作为披露日。
- 默认在披露日后第一个可用交易日发布状态，即 `available_delay_days=1`。
- 每只基金的新一期前十大重仓状态替换上一期前十大重仓状态。
- 从前十大退出的股票写 0 状态，避免下游前向填充留下陈旧重仓。
- 平台资产只发布披露事件状态，不直接生成所谓季度变化字段。研究层应在统一形成日，例如月末，先做 backward as-of，再计算形成日之间的变化。

## 主要字段

| 字段 | 含义 |
| --- | --- |
| `fund_top10_count_holding_stock` | 当前已知状态下，把该股列入前十大重仓的基金数量 |
| `fund_top10_hold_mv` | 上述前十大重仓合计持股市值 |
| `fund_top10_hold_amount` | 上述前十大重仓合计持股数量 |
| `fund_top10_stk_mkv_ratio_sum` | 各基金该股票占基金股票市值比例的合计 |
| `fund_top10_stk_float_ratio_sum` | 各基金该股票占流通股本比例的合计 |
| `fund_top10_hold_mv_to_total_mv` | 前十大重仓合计持股市值 / 股票总市值 |
| `fund_top10_hold_mv_to_float_mv` | 前十大重仓合计持股市值 / 股票流通市值 |
| `fund_top10_hold_amount_to_float_share` | 前十大重仓合计股数 / 流通股本 |

## 研究边界

该资产仍有几个不能靠改变量名消失的限制：

- 基金 A/C 等份额可能对应同一实际投资组合，基金数量不能视为严格的独立基金经理数量。
- ETF、被动指数基金和主动权益基金尚未拆分。
- `fund_top10_count_holding_stock` 天然暴露市值、流动性和指数成分，需要下游做行业、市值和流动性控制。
- 持仓是低频披露信息，不能拿它冒充日频资金流。

因此它适合作为研究候选的公募重仓广度和拥挤度信号，不能直接解释成完整股东名册。
