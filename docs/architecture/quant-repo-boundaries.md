# 量化仓库职责边界

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

## 总体关系

`market-data-platform` 是独立的数据平台项目。它负责生产、治理和发布市场数据资产，向下游提供稳定的数据契约、清单和质量回执。

```text
market-data-platform
数据接入、生产、PIT、质量治理、版本和发布
             |
             | published asset、manifest、receipt、schema
             v
quant-platform
通用回测、组合、风险、成本、容量和执行模拟
             |
             | 公开 API 和版本化研究产物
             v
quant-research
策略、特征、机器学习、实验和研究证据
             |
             | versioned artifact
             v
market-data-platform
报告、看板、消息交付和运营入口
```

`research-workspace` 进入 sunset 过渡期，继续维护历史工作区、版本组合、跨仓库检查和迁移导航。它不再作为新的业务实现位置。

## market-data-platform 的职责

- provider 接入、请求可靠性和配额管理。
- raw、standardized 和 canonical 数据资产的构建。
- PIT 语义、数据可用时间和来源血缘。
- 数据质量检查、quality receipt、manifest、版本和当前指针。
- DuckDB 查询、本地快照和已发布数据资产读取。
- 面向下游的稳定 schema、路径和只读适配器。

数据平台不负责策略选股、模型训练、组合回测或券商执行。

## quant-platform 的职责

- 通用回测、组合构造、风险、成本、容量和执行模拟。
- 与策略无关的研究接口、artifact envelope 和公共 contracts。
- 消费 `market-data-platform` 发布的数据资产。

平台代码不得依赖 provider SDK、真实策略数据、策略专属参数或私有研究模块。

## quant-research 的职责

- 策略身份、投资假设和策略生命周期。
- PIT 特征、标签、机器学习模型和模型选择。
- 实验协议、研究结果、晋升证据和私有配置。
- 现金流策略等具体策略的选股、评分和研究组合。

研究层通过已发布数据资产和平台公开 API 工作，不直接导入数据平台内部实现。

## 依赖规则

跨仓库连接使用以下稳定边界：

- published asset
- 数据集 manifest
- quality receipt
- 版本化 schema
- 公开 Python API
- 版本化研究 artifact

下游项目不应依赖上游仓库的工作目录、私有缓存、provider 内部模块或本地绝对路径。

## 迁移期规则

旧 `research-workspace` submodule 仍可用于历史复现和兼容迁移。新能力进入对应的目标仓库。迁移 adapter 需要保留直接测试、owner 和删除条件。
