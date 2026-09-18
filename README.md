# quant-market-data-platform

> 项目状态：独立数据平台。新的数据生产、质量治理和 published asset 能力继续进入本仓库。通用回测、组合、风险和执行模拟进入 `quant-platform`，策略专属数据派生进入 `quant-research`。历史名称 `market-data-platform` 仅保留在兼容命令、迁移记录和数据路径中。

`market-data-platform` 是量化研究和交易系统共享的市场数据资产平台。它统一管理数据采集、清洗、检查、发布和读取入口。

大体量行情数据、缓存、报告和凭证不进入 Git。

## 当前范围

当前主线覆盖中国大陆市场数据：

- TuShare 数据源入口
- A 股原始层、日频清洗和股票池
- Guan 与 TuShare A 股分钟数据融合、覆盖审计和原子切换
- 当前数据契约与数据集注册表
- 基本面时间点（PIT）、行业、资金流和热点特征
- 标准层、DuckDB 查询和本地快照
- 数据质量、兼容性和架构治理
- 面向 Qlib 的条件化只读 DataLoader 适配器

中国香港市场生产模块已经归档，只保留冷存储冻结和恢复入口。

## Qlib 接入状态

当前 Qlib 接入只提供已发布数据资产的只读 DataLoader 适配器。核心包、数据生产命令和
当前数据契约发布链路不依赖 Qlib。Dataset、DataHandler、模型训练和实验记录由研究层负责，
本仓库没有提供这些能力。

常规开发门禁只安装 `dev` extra，不安装真实 `pyqlib`。需要验证真实 Qlib 运行时时，使用
`dev` 与 `qlib` 两个 extra 运行定点测试。具体命令见[下游接入](docs/integrations.md)和
[测试](docs/operations/testing.md)。

## 快速开始

```bash
uv sync --extra dev
cp .envrc.example .envrc
cp .env.example .env.local
direnv allow
export DATA_PLATFORM_ROOT=/data/market-data-platform
```

真实数据源凭证写入未跟踪的 `.env.local`，也可以放在：

```text
~/.config/market-data-platform/config.env
```

最小检查：

```bash
marketdata --help
marketdata paths --market a_share
marketdata contract build --market a_share --provider tushare \
  --artifacts-root "$DATA_PLATFORM_ROOT"
marketdata registry build --artifacts-root "$DATA_PLATFORM_ROOT"
```

## 稳定数据入口

```text
$DATA_PLATFORM_ROOT/metadata/current_assets/<market>_current.json
```

A 股权威文件名为 `a_share_current.json`。数据集注册表用于查询和审计，程序读取优先使用当前数据契约。

A 股分钟数据通过两个显式分源的稳定 alias 发布：

```text
$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m
$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m_tushare
```

`minute_1m` 保留 Guan legacy canonical，当前指向 `minute_1m_v3_20260714`。
`minute_1m_tushare` 是采用独立特征、模型和阈值基线的 TuShare operational canonical。
分钟资产尚未写入 `a_share_current.json` 或数据集注册表。读取方必须保留所选 alias、最终解析版本
和对应 receipt，不得把两个来源当作可无缝替换的数据。来源口径与研究限制见
[A 股分钟数据](docs/operations/a-share-minutes.md)。

详细规则见 [docs/contracts.md](docs/contracts.md)。

## 常用入口

```bash
marketdata data catalog --artifacts-root "$DATA_PLATFORM_ROOT"
marketdata data query --artifacts-root "$DATA_PLATFORM_ROOT" --sql "select 1 as value"
marketdata backup-data --help
marketdata migration freeze-hk --help
marketdata migration hydrate-hk --help
```

TuShare 功能需要 `tushare` extra 和 token。完整操作清单见 [docs/operations.md](docs/operations.md)。

## 测试和质量检查

```bash
uv run --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run --extra dev python -m ruff check .
uv run --extra dev python -m ruff format --check .
uv run --extra dev ty check --error-on-warning
uv run --extra dev python scripts/dev/architecture_governance.py --check
```

`ty` 的配置范围合并了迁移前的日常检查与发布检查文件，并额外纳入 `symbols.py`。日常检查与发布检查使用同一配置。

详细说明见 [测试](docs/operations/testing.md) 和 [docs/quality-governance.md](docs/quality-governance.md)。编码代理默认读取根 README、[文档索引](docs/README.md) 和一个任务相关分类。

## 文档入口

- [文档首页](docs/README.md)
- [数据契约](docs/contracts.md)
- [操作与 CLI](docs/operations.md)
- [A 股分钟数据](docs/operations/a-share-minutes.md)
- [数据仓库和查询](docs/data-warehouse.md)
- [下游接入](docs/integrations.md)
- [研究视图归属](docs/ownership-migration.md)
- [研究数据完整性](docs/research-integrity.md)
- [兼容层](docs/compatibility.md)
- [测试](docs/operations/testing.md)
- [质量治理](docs/quality-governance.md)
