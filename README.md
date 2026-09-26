# quant-market-data-platform

`quant-market-data-platform` 负责量化研究所需的市场数据：接入数据源、整理数据、检查质量、管理版本并发布可供下游使用的数据资产。

本项目属于 Quant Research 项目系列，与同系列的研究框架、数据消费方和交付项目各自独立维护、按接口协作。

当前活跃开发以中国大陆市场为主。研究和回测项目读取这里发布的数据，不直接依赖本仓库的内部实现。大体量数据、运行结果和凭证保存在仓库之外。

当前 Qlib 接入只提供已发布数据资产的只读 DataLoader 适配器。常规开发门禁只安装 `dev` extra。安装选项和接入限制见[下游接入说明](docs/integrations.md)。

## 开始使用

项目使用 `uv` 管理 Python 环境：

```bash
uv sync --extra dev
marketdata --help
```

若要读取或构建真实数据，还需要配置数据目录和数据源凭证。请先阅读[本地开发与操作说明](docs/operations/backup-and-dev.md)和[凭证配置](docs/operations/credentials.md)。

## 接下来读什么

- [文档首页](docs/index.md)：快速了解项目和主要入口
- [操作总览](docs/operations.md)：查找数据命令与日常操作
- [数据契约](docs/contracts.md)：了解已发布数据的格式和约定
- [下游接入](docs/integrations.md)：了解其他项目如何读取发布资产
- [文档目录](docs/README.md)：按主题查找技术说明

## 项目边界

本项目维护市场数据生产与发布。通用回测和组合能力属于 [`quant-platform`](https://github.com/runchengxie/quant-platform)，策略研究与实验管理属于私有 `quant-research`，持久化回测任务由 [`quant-backtest-runtime`](https://github.com/runchengxie/quant-backtest-runtime) 执行。

当前数据范围、各数据源能力、质量规则、命令参数和运维流程都收录在 `docs/` 中。

本地开发质量检查包含 `ty check` 等门禁，完整命令见[测试说明](docs/operations/testing.md)。
