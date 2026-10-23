# 历史分钟独立覆盖凭证

这个离线工具对明确列出的 1–10 个交易日生成可重放的独立覆盖凭证。通过表示原生
241 行分钟数据与独立历史日线、每日指标和回溯上市范围一致。股票主表是后续获取的
当前快照，不能证明历史当时已知的信息，因此 `historical_pit_certified` 始终为 false。

凭证不构成全市场发布验收。`full_market_promotion_allowed` 始终为 false，
`cross_source_acceptance` 为 `not_evaluated_requires_0.99_gate`。原有跨源 0.99
门槛、正式发布入口和隔离记录继续独立生效。原分区生产版本无法确认时固定保留 unknown。
本次审计版本单独记在 auditor 中，不能代替生产版本。

## 输入与输出

使用[试点清单](historical-minute-coverage-pilot.md)的 dates、inputs 结构，并增加：

- `quarantine`：inputs 中隔离登记文件的键。文件必须含 dates。登记日期无条件阻止通过。
- `resolutions`：按交易日索引的显式候选裁决。没有裁决时，多候选日期仍为 unresolved。
- 每日的 `candidate_sidecars` 与 partitions 按顺序一一对应。第一项为明确选择的候选。
- 每日的 `prior_audit` 保留原始审计输入键。

每个输入必须包含 path、SHA256、bytes、device、inode、ctime_ns 和 mtime_ns。
工具在扫描前后逐项验证，支持文件替换后恢复的检测。输入总量限制为 2 GiB，单个分钟分区最多 200 万行。逐日扫描，
候选裁决最多两个分区。清单、原分区、sidecar、策略、日线、主表、隔离登记和修复记录
均只读。新输出要求不存在，不能覆盖已有凭证。所有清单路径必须来自可信操作者。

候选裁决支持两个明确的类型：

- `identical_content`：selected 指向第一候选，audit 指向 prior_audit 中的一份记录。
  必须验证两份 sidecar 的分区绑定、两文件 SHA256 相等，以及所选候选的原审计绑定。
- `audited_additive_patch`：额外提供 patch、acquisition、build 的输入键。采集记录的
  source_sidecar 和 patch 哈希、构建记录的源与目标 sidecar 哈希、安装后审计的分区
  哈希必须相符。重新比较所选分区是否精确等于旧分区加一个互不重叠的 241 行补采代码。
  原有行的任何变化都会拒绝裁决。通过后仍须独立宇宙和原生语义复核。

裁决只选择本次凭证的输入，不删除另一个候选，也不修改任何全局登记。错误的裁决、
输入损坏或文件变化使整次签发失败。完整输入中未满足覆盖、语义或隔离条件的日期
保留 unresolved 及原因。输出 records 中的通过状态为
`passed_retrospective_independent_coverage`，同时保留逐项差异和原生检查结果。

## 签发与消费

在已提交的仓库版本中运行。源代码有未提交修改时拒绝签发。

```bash
uv run --extra dev python -m scripts.operations.historical_minute_coverage_receipts issue \
  --manifest "$DATA_PLATFORM_ROOT/reports/my-task/manifest.json" \
  --output "$DATA_PLATFORM_ROOT/reports/my-task/receipts.json"

uv run --extra dev python -m scripts.operations.historical_minute_coverage_receipts validate \
  --receipt "$DATA_PLATFORM_ROOT/reports/my-task/receipts.json" \
  --sha256 "$RECEIPT_SHA256" --date 20200102 --partition "$SELECTED_PARTITION"
```

消费者必须从可信交接记录取得凭证 SHA256，并显式指定日期和实际分区路径。验证会
重新检查清单、全部输入、候选裁决、隔离与覆盖，比较完整结果，并确认调用者指定的分区
正是已绑定的通过候选。仅检查 JSON 中的 passed 字段无效。完整源码哈希集合也必须一致。auditor 只接受明确字段和完整 commit SHA，并从该提交的
Git 源码树重新计算哈希，拒绝伪造版本或额外声明。
相同内容的后续合并提交可重放，代码改变后须重新签发。SHA256 是完整性绑定，不提供数字签名。

旧清单引用的文件如更换 inode，即使内容不变也无法重放旧身份。操作者须创建新的清单，
保留旧绑定、记录同内容新身份与变更原因，然后重新执行完整扫描，不能修改旧凭证。
