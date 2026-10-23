# 历史分钟覆盖独立复核试点

[English page](historical-minute-coverage-pilot.en.md)

`historical_minute_coverage_pilot.py` 在离线模式下复核最多 10 个交易日，生成新的诊断报告。
它不会修改原始 sidecar、生成验收凭证或切换发布资产。`eligible_for_receipt_review`
只表示可继续审核独立凭证。所有输出的 `full_market_promotion_allowed` 都是 `false`。

```bash
uv run --extra dev python scripts/operations/historical_minute_coverage_pilot.py \
  --manifest "$TASK_REPORT/manifest.json" --output "$TASK_REPORT/pilot.json"
```

输入清单包含 `source_revision`、`inputs` 和 `dates`。`inputs` 将文件键映射到
`path`（绝对路径）、`sha256`（完整哈希）、`bytes`、`device`、`inode`、`ctime_ns` 和 `mtime_ns`。扫描前后都核对完整文件哈希，
内容或文件身份变化即终止。输出使用独占创建，不能覆盖旧报告。清单必须存放在仓库外的任务报告目录。

每个日期对象包含：

- `date`：YYYYMMDD。`quarantined`：显式声明隔离状态，必须依据已绑定的隔离记录。
- `sidecar`：原始 sidecar 文件键。`partitions`：所有候选 Parquet 文件键，重复日期不具备资格。
- `daily`、`daily_basic`：对应日期的全部独立分区文件键，不得使用分钟 sidecar 反推股票池。
- `stocks`：以 L、D、P、G 为键的四个独立证券快照文件键映射，包括空快照。
- `policy`：原始 provider-no-data policy 文件键。

原始审计、证券转换规则源码、公告引用文档及隔离记录也应列入 `inputs`，保留完整来源绑定。
生产者版本若未写入原始 sidecar，应如实标为未知，不能用当前审计版本代替。
清单中的来源和隔离状态仍需人工核对。该工具仅提供诊断，不能承担防伪或发布授权职责。

复核保留原始 daily 代码差异，使用现有生效日期转换规则选择源生历史代码，并列出实际应用的
转换。转换间隙不推断交易状态。上市区间用于单独列出上市前 BJ 代码。缺少币种、上市时间、
独立 daily 一致性或证券范围证据时保持 `unresolved`，不以当前证券列表代替历史证据。
代码转换的日期与公告见 [分钟操作说明](a-share-minutes.md)。

每次重新扫描原生分钟结构、241 根 bar、价格和成交额约束及 sidecar 文件绑定，不复用旧语义
结论。停牌差异没有新增豁免路径：daily 与 daily_basic 不一致时保留未解决状态，必须补充
独立且带日期的停牌证据。适用的 provider 例外、BJ 缺口或已隔离日期均不能获得复核资格。
原始 policy 标记不被删除，也不能单凭其日期不适用就升级全市场覆盖。

报告记录输入字节数、总耗时、逐日耗时、进程最大 RSS 和零供应商请求。完整哈希至少读取
两遍输入文件，语义检查逐日进行。试点成本只能用于规划同类本地复核，不能外推为发布验收成本。
