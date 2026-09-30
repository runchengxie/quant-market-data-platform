# A 股时点基本面资产

`pit_fundamentals` 是已发布的 A 股时点基本面资产。稳定资产键和默认路径见[数据契约](../../contracts.md)，当前发布状态应以部署环境中的 current manifest 为准。

构建器接收 normalized 基本面输入，并按披露日加配置的日历日延迟计算 `available_date`。缺少有效报告期或披露日的数据行会进入隔离区。输出按 retrieval vintage 保留修订，同一事件存在冲突值时会拒绝构建。`trade_date` 与 `available_date` 相同，但不保证对应交易所开市日。

raw-to-PIT 流程见[基本面运维说明](../../a-share-fundamentals.md)，实现维护在 `src/market_data_platform/providers/tushare_a_share_fundamentals_part04.py`。
