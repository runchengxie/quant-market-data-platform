# current_assets 迁移状态

更新时间：2026 年 9 月 8 日

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

## 仍需推进

- 继续排查部署仓库中直接读取 `latest` 的内部输入流程
- 在独立 fixture 上补充发布失败和回滚演练
- 逐个确认兼容链接的代码、调度、发布和恢复引用
- 确认无引用后，再逐批删除兼容链接

## 当前判断

本轮没有删除兼容链接。当前链接仍有发布、恢复、分钟数据物化或上下文构建用途，直接删除会增加生产风险。

下次删除前，需要先完成对应读取程序的切换，并通过 no-send、回滚和恢复检查。
