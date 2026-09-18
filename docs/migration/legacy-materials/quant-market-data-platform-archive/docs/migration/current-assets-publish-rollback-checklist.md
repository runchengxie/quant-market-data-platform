# current_assets 发布与回滚检查清单

## 发布前

1. 确认目标数据目录包含 `manifest.yml`。
2. 确认 manifest 中的文件数和行数与实际文件一致。
3. 确认目标版本目录是实体目录，不依赖待删除的兼容链接。
4. 使用 no-send 模式运行生产读取流程。

## 发布后

1. 检查 `metadata/current_assets/a_share_current.json`。
2. 运行：

   ```bash
   uv run marketdata contract inspect \
     --artifacts-root "$DATA_PLATFORM_ROOT" \
     --market a_share \
     --format text
   ```

3. 检查研究入口是否读取目标版本目录。
4. 检查晨报、晚报和 DailyWatch20 的产物回执。

## 回滚

1. 选择一个已存在且通过 manifest 校验的实体版本目录。
2. 在 no-send 模式下重新生成 current contract。
3. 再次运行 contract inspect。
4. 运行研究读取入口，确认读取路径回到指定版本。
5. 确认失败的候选版本没有覆盖原 current contract。

## 本轮结果

- current contract 检查：已运行
- 晚报读取和发送：2026 年 9 月 8 日已成功
- `ths_member`：暂缓
- 兼容链接删除：本轮没有发现满足删除条件的链接
