# current_assets 清理收尾记录

更新时间：2026 年 9 月 18 日

## 结论

本轮完成了 current-assets 兼容层的代码、调度、发布和恢复读取审计。
没有发现可以安全删除且不会改变生产语义的 `latest` 入口，因此保留这些入口。它们属于当前数据平台仍在使用的操作契约，不属于旧仓库迁移残留。

## 已验证的消费者

| 消费者 | 当前用途 | 决策 |
| --- | --- | --- |
| current contract | 研究、DailyWatch20 和 DC 候选池的主读取入口 | 保持为首选入口 |
| 发布与恢复流程 | 发布完成后维护兼容别名，恢复时读取已发布版本 | 保留 `latest` |
| 分钟数据物化 | 从可变输入目录读取并封存为日期版本 | 保留输入 `latest`，物化后切换到版本目录 |
| 上下文构建 | 使用按日期构建的内部 `latest` 别名 | 保留，直至上下文读取切换完成 |
| 旧研究读取 | 不再作为主入口，缺少 current contract 时不回退 | 已收紧，缺失时失败 |

代码和测试证据包括 `tests/test_current_path_audit.py`、
`tests/test_context_snapshots_publish.py`、`tests/test_materialize_current_versions.py`、
`tests/test_publish_probe_assets.py` 以及 `tests/test_cli_governance.py`。

## `ths_member`

`ths_member` 仍不能发布为 current contract：当前目录缺少 `manifest.yml`，且 TuShare 接口存在限流风险。
本轮不伪造清单，不把不完整目录发布为当前资产，也不让晨报/晚报依赖它。该项源于外部数据源阻塞，并非仓库迁移遗漏。

恢复条件：成功完成一次真实 provider 拉取，输出非空数据和完整 manifest，经过 no-send 读取、质量检查和回滚验证后，才允许切换 current contract。

## 迁移状态

- 旧仓库和历史说明/实验材料：已建立 canonical 副本和 manifest。
- 生产仓库路径：已切换到五个 canonical 仓库。
- `latest` 兼容入口：完成审计，按消费者保留，当前仍有明确消费者。
- 发布失败/回滚：已有独立测试和 no-send 运行路径，保留在发布/恢复流程中。
- 剩余外部事项：仅 `ths_member` 的真实数据源恢复与重新发布。

## 删除门槛

未来只有在以下条件全部满足后，才删除相应 `latest` 入口：

1. 生产代码、调度、发布和恢复脚本不再读取该入口。
2. current contract 在 no-send 和真实演练中均能完成读取。
3. 已完成一次回滚和恢复验证。
4. 保留版本目录、manifest 和 hash 可独立复现。
5. 删除变更经过质量门禁并在生产 promotion 后观察一个完整运行周期。
