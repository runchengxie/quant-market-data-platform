# DailyWatch20 数据归属

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

`market_data_platform.research_views` 是 DailyWatch20 数据视图的权威实现，负责数据事实和来源审计：

- 按研究日期当时可见的信息生成候选股票池
- 校验指定日期的分区和快照完整性
- 记录来源文件、内容哈希和数据血缘
- 汇总分钟数据来源目录和输入可用性

读取器遇到不完整、过期或日期不一致的同花顺热门股票快照时会直接失败。模型可用性判断由
`alpha-research` 负责，组合选择由 `portfolio-backtester` 负责，策略级实验组合由
`research-apps` 负责。

数据平台提供可复现的数据视图，不决定策略是否选择某只股票，也不保存策略模型和实验结论。

工作区 2.0 已完成归属迁移。新增数据读取、PIT 股票池或来源血缘逻辑应直接加入
`market_data_platform.research_views`。`strategy-pipeline` 只调用公开入口并记录运行回执。

`resolve_daily_watch20_assets(..., minute_dataset="legacy" | "tushare")` 默认使用 TuShare，
解析独立的 `minute_1m_tushare` alias 和 operational receipt。复现既有 Guan 研究时必须显式传入
`minute_dataset="legacy"`。该入口解析 `minute_1m`，继续作为回滚和对照来源。
