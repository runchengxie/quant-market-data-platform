# quant-market-data-platform

`quant-market-data-platform` 是量化研究和报告系统共享的市场数据控制面，负责数据接入、标准化、质量治理、版本管理和 published assets。

## 快速开始

```bash
uv sync --extra dev
export DATA_PLATFORM_ROOT=/data/market-data-platform
marketdata --help
```

凭证和供应商地址只放在本地配置中，不提交到 Git。完整配置说明见[凭证与配置](operations/credentials.md)。

## 项目边界

- 数据平台维护 provider、数据契约、质量回执、清单和发布入口。
- `quant-platform` 消费稳定的数据资产并提供通用回测、组合和风险能力。
- `quant-research` 维护策略、特征、模型和研究证据。
- 生产主机、真实数据、凭证和部署拓扑由 private deploy 层管理。

从[架构与边界](architecture/quant-repo-boundaries.md)开始，或直接查看[操作总览](operations.md)。
