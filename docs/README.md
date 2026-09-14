# 市场数据平台文档

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

本目录记录 `market-data-platform` 的数据契约、操作方式和治理规则。

RQData 已完全退役，HK 行情支持已移除。当前活跃主线是中国大陆市场数据，A 股以 TuShare 平台资产为主，Guan 分钟数据为辅。

本项目保持独立，负责数据生产、PIT、质量治理、版本和 published asset。`quant-platform` 消费本项目发布的数据资产，`quant-research` 负责策略和机器学习研究。跨仓库职责见[量化仓库职责边界](architecture/quant-repo-boundaries.md)。

## 推荐阅读顺序

1. [根目录 README](../README.md)
2. [量化仓库职责边界](architecture/quant-repo-boundaries.md)
3. [路径和数据契约](contracts.md)
4. [操作与公开 CLI](operations.md)
5. [下游系统接入](integrations.md)
6. [测试](operations/testing.md)
7. [质量治理](quality-governance.md)
8. [文档写作与生命周期规则](documentation-style.md)

## 按主题查找

| 主题 | 文档 |
| --- | --- |
| 跨仓库职责和依赖方向 | [architecture/quant-repo-boundaries.md](architecture/quant-repo-boundaries.md) |
| 路径、资产键、清单和当前数据契约 | [contracts.md](contracts.md) |
| 数据代码生命周期分层 | [data-lifecycle-architecture.md](data-lifecycle-architecture.md) |
| A 股研究资产口径 | [a-share-research-profile.md](a-share-research-profile.md) |
| 基本面原始数据到 PIT | [a-share-fundamentals.md](a-share-fundamentals.md) |
| 历史行业标签展开 | [concepts/historical-industry-labels.md](concepts/historical-industry-labels.md) |
| 资金流、持仓和股东结构特征 | [a-share-flow-ownership-features.md](a-share-flow-ownership-features.md) |
| 数据服务商凭证和日常操作 | [operations.md](operations.md) |
| A 股 TuShare 运维 | [operations/a-share-tushare.md](operations/a-share-tushare.md) |
| A 股分钟数据、来源口径和切换 | [operations/a-share-minutes.md](operations/a-share-minutes.md) |
| 公共源 ETF 分钟数据 | [operations/etf-minutes.md](operations/etf-minutes.md) |
| 数据仓库和 DuckDB 查询 | [data-warehouse.md](data-warehouse.md) |
| 数据生命周期 | [data-governance.md](data-governance.md) |
| 下游接入 | [integrations.md](integrations.md) |
| DailyWatch20 数据归属 | [ownership-migration.md](ownership-migration.md) |
| 研究数据完整性 | [research-integrity.md](research-integrity.md) |
| 研究数据读取适配器 | [research-data-interface.md](research-data-interface.md) |
| 服务商配额展示 | [quota-rendering.md](quota-rendering.md) |
| AFML 研究数据特征 | [afml-research-features.md](afml-research-features.md) |
| 兼容层和迁移入口 | [compatibility.md](compatibility.md) |
| 测试脚本 | [operations/testing.md](operations/testing.md) |
| 质量和维护性治理 | [quality-governance.md](quality-governance.md) |
| 当前维护性审计 | [maintenance-audit.md](maintenance-audit.md) |

港股历史资产恢复入口已随 RQData 一起退役，历史复现见 `hk-freeze-20260613` 标签或私有归档仓库，详见 `operations/hk-archive-restore.md`（已退役）。

内部运行记录和迁移计划由 private `quant-market-data-deploy` 仓库保存，public 文档只保留通用架构、契约和操作说明。

入口文档只说明项目定位、边界和导航。数据契约、操作命令和质量门禁分别放在对应专题文档中。
