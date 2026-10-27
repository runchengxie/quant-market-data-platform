# quant-market-data-platform

[English page](index.md)

`quant-market-data-platform` 从 TuShare 等来源取得市场数据，统一格式，检查缺失、覆盖范围和版本，再发布给研究与报告程序读取。当前主要维护 A 股数据，Guan 分钟数据作为补充来源。RQData 和历史港股生产流程已退役，归档数据单独保留。

## 快速开始

需要 Git、`uv` 和 Python 3.11 或更新版本。在本仓库根目录执行以下命令。安装依赖需要联网，查看帮助无需供应商凭证，也不会下载行情。

```bash
uv sync --locked --extra dev
uv run --locked marketdata --help
```

成功后会显示 `marketdata` 的用法、子命令和 `-h, --help` 选项，并以退出码 0 结束。下一步按[操作总览](operations.md)选择任务，再按[凭证与配置](operations/credentials.md)设置仓库外的数据目录和所需凭证。

凭证和供应商地址只放在本地配置中，不提交到 Git。完整配置说明见[凭证与配置](operations/credentials.md)。

## 项目边界

- 数据平台维护 provider、数据契约、质量回执、清单和发布入口。
- `quant-platform` 消费稳定的数据资产并提供通用回测、组合和风险能力。
- `quant-research` 维护策略、特征、模型和研究证据。
- 生产主机、真实数据、凭证和部署拓扑由 private deploy 层管理。

从[架构与边界](architecture/quant-repo-boundaries.md)开始，或直接查看[操作总览](operations.md)。
