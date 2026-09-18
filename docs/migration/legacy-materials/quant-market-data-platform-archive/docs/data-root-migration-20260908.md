# 数据根迁移记录

日期：2026 年 9 月 8 日

## 迁移结果

主数据目录统一为：

```text
$DATA_PLATFORM_ROOT
```

本次整理完成了两项工作：

1. 修复主数据目录中的 25,522 条软链接，把旧的
   `$DATA_PLATFORM_ROOT/` 目标改为新的数据根。
2. 将两个 0 字节临时文件移入可恢复的归档目录：
   `~/data/quant/archive/market-data-platform/cleanup-20260908/empty-files/`。

软链接的逐条变更记录位于数据根的：

```text
metadata/lifecycle/migrations/data-root-symlink-rewrite-20260908.json
```

## 目录约定

- `assets/` 保存原始和加工后的数据资产。
- `published/` 保存通过检查、供其他项目读取的数据版本。
- `metadata/` 保存注册表、manifest、receipt 和生命周期记录。
- `reports/` 保存质量检查和发布报告。
- `strategy_inputs/`、`strategy_outputs/` 保存策略输入和正式输出。
- `staging/` 保存补数、候选版本和验证中的中间结果。
- `experiments/`、`runs/` 保存研究实验和运行记录。
- `archive/` 保存已退出日常使用范围、但仍需保留的历史材料。

## 后续整理规则

处理 `staging/` 或历史实验数据前，依次检查：

1. manifest 或 receipt 是否已经进入终态。
2. 是否仍有运行进程或锁文件。
3. 是否已有通过校验的正式版本。
4. 是否仍被 `current`、`latest`、`rollback` 或报告引用。
5. 是否已经记录原路径、目标路径、文件数量和校验摘要。

满足条件后，优先移动到 `archive/`，观察一个完整运行周期，再考虑清理。

## 复查命令

预览旧路径软链接：

```bash
uv run python scripts/repair_data_root_links.py \
  --root $DATA_PLATFORM_ROOT \
  --old-root $DATA_PLATFORM_ROOT \
  --new-root $DATA_PLATFORM_ROOT \
  --manifest /tmp/data-root-migration.json
```

预览命令默认不会修改文件。只有添加 `--apply` 才会替换软链接。
