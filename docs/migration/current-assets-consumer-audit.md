# current_assets 读取审计

更新时间：2026 年 9 月 18 日

## 结论

主要研究读取入口已经通过 `metadata/current_assets/a_share_current.json` 获取实体版本目录。

本轮又收紧了 DC 概念候选池。缺少 current contract 或数据资产不可用时，程序会直接报错，不再回退到 `latest`。

## 读取入口

| 入口 | 当前方式 | 处理结果 |
| --- | --- | --- |
| 通用 A 股研究数据 | `PublishedAssetContract.load_current` | 已接入 |
| DailyWatch20 数据入口 | 读取 A 股 current contract | 已接入 |
| DailyWatch20 THS 热股候选池 | 读取 A 股 current contract | 已接入 |
| DailyWatch20 DC 概念候选池 | 读取 A 股 current contract | 本轮已收紧，缺失时直接失败 |
| 发布程序 | 更新 current contract，并维护兼容入口 | 保留 |
| 分钟数据物化 | 使用指定的分钟数据输入目录 | 暂保留内部兼容路径 |
| 上下文构建 | 使用按日期构建的 `latest` 构建别名 | 暂保留 |

## 暂缓项目

`ths_member` 暂缓发布。当前数据缺少 `manifest.yml`，TuShare 接口也存在限流问题。它不属于晨报和晚报的必需输入。

## 审计原则

- 读取数据时优先使用 current contract 中的 `resolved_path`
- current contract 不存在、资产缺失或资产不可用时直接失败
- `latest` 仅用于发布、恢复、分钟物化和上下文构建等已登记的内部输入
- 删除兼容入口前，必须同时检查代码、调度、发布和恢复流程
