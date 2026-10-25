# 备份和本地开发

[English page](backup-and-dev.en.md)

## 本地快照备份

`marketdata backup-data` 用于冻结本地缓存、股票池、配置文件。该命令写入快照目录和 `manifest.yml`，不会覆盖已有快照。

```bash
marketdata backup-data --preset a_share_current --name a_share_current_20260526
```

## 标准层和 DuckDB 查询

```bash
marketdata data catalog --artifacts-root "$DATA_PLATFORM_ROOT"
marketdata data materialize --help
marketdata data query --artifacts-root "$DATA_PLATFORM_ROOT" --sql "select 1 as value"
```

DuckDB 查询依赖：

```bash
uv sync --extra dev --extra duckdb
```

## 本地开发检查

本地与 CI 使用同一完整入口：

```bash
uv sync --locked --extra dev
uv run --locked --extra dev bash scripts/dev/check_quality.sh
```

`TMPDIR` 指向仓库外且至少有 20 GiB 可用空间的临时根目录。入口为本次运行创建
独立子目录，检查通过后清理，失败则保留取证。依赖漏洞检查需要公共网络。
以下命令用于单项诊断，完整入口包含下面的治理预算检查，不会刷新基线。

常规开发：

```bash
uv sync --extra dev
uv run --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run --extra dev python -m ruff check .
uv run --extra dev python -m ruff format --check .
uv run --extra dev ty check --error-on-warning
```

本地推送前治理检查：

```bash
uv run --extra dev python scripts/dev/quality_debt.py --skip-ruff --complexity --check-baseline --check-ratchet
uv run --extra dev python scripts/dev/maintainability_metrics.py --check-baseline
uv run --extra dev python scripts/dev/compatibility_governance.py --check
uv run --extra dev python scripts/dev/architecture_governance.py --check
```
