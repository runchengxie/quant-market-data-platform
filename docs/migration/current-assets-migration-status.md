# current_assets 迁移状态

更新时间：2026 年 9 月 18 日

## 已完成

- `staging/` 中可确认的候选目录已归档到 `archive/staging/`
- 多批数据已从日期实体目录发布到 current contract
- `hsgt_top10`、`margin`、`margin_detail` 已补齐有效版本
- `moneyflow_hsgt` manifest 已修正
- `moneyflow_ths` 已重新验证并发布为 20260908 版本
- 通用研究数据、THS 热股和 DC 概念候选池已使用 current contract
- 缺少 current contract 时，DC 概念候选池会直接失败
- 晚报已使用新数据目录完成一次补发

## 暂缓

- `ths_member` 暂缓。原因是缺少 `manifest.yml`，同时 TuShare 接口存在限流问题。

## 收尾结论

- 部署、研究、发布和恢复流程对 `latest` 的读取已完成审计。
- 发布失败和回滚路径已有独立 fixture、no-send 检查和测试覆盖。
- `latest` 仍被分钟物化、上下文构建、发布和恢复使用，因此按生产契约保留，不删除。
- `ths_member` 仍受真实 TuShare 数据源和限流阻塞，满足恢复条件前不发布。

## 与仓库迁移的关系

本页的“仍需推进”是当前数据平台内部的 current-assets 兼容层清理，不是旧仓库能力迁移的未完成项。旧仓库和历史说明材料已经在 `quant-market-data-platform/docs/migration/legacy-materials/` 或 `quant-research/docs/migration/legacy-materials/` 建立可读副本；这里列出的 `latest`、rollback fixture、兼容链接和 `ths_member` 工作属于数据生产契约治理；当前已完成审计并记录明确决策，未经 no-send、回滚和恢复验证不得删除或发布。

## 当前判断

本轮没有删除兼容链接。当前链接仍有发布、恢复、分钟数据物化或上下文构建用途，直接删除会增加生产风险。

下次删除前，需要先完成对应读取程序的切换，并通过 no-send、回滚和恢复检查。
