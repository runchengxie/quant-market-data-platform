# 基本面投影共识证据

`statement-projection-consensus.v1` 为同一证券、报告期和披露日的多类别候选生成只读证明。
指定字段全部相等时返回共同值。任一字段不同则保留整个事件及所有候选，
`common_values=null`、`blocks_fallback=true`。不得使用旧报告的值填补该冲突。
这份证据仅供审计，不能作为完整研究资产、类别选择凭证或生产 PIT 发布许可。

## 使用

```python
import json
from pathlib import Path
from quant_market_data_platform.statement_consensus import audit_projection_consensus

report = audit_projection_consensus(
    dataset="income",
    source=Path("/data/normalized/income/fundamentals.parquet"),
    expected_sha256="<预先核验的源文件 SHA256>",
    fields=("revenue", "total_revenue", "rd_exp"),
    available_delay_days=1,
)
# 在调用方指定的独立证据目录保存。函数本身不写任何文件。
print(json.dumps(report, ensure_ascii=False, allow_nan=False))
```

每个数据集分别声明非空、不重复的数字字段集合。字段缺列直接报错，不视作 NULL。
NULL 仅与 NULL 相等，不与零相等。值使用严格 JSON 类型及精确表示比较，不做容差、
跨类别补全或非空优先。日期、类别、更新标记及血缘字段不属于可投影的财务指标。
现有归档 16 个 statement 字段需按所属数据集拆分传入。指标表不在此版本范围内。

证据保存输入路径及 SHA256、投影字段、空值规则、所有候选类别、完整候选行和行散列。
`proof_sha256` 绑定除自身及 `audit_created_at` 外的完整报告，使用排序键、紧凑分隔符、
UTF-8、禁止非有限浮点数的 JSON 序列化。输入、投影、候选或元数据变化必须重新运行，
不能复用旧证明。扫描复用现有只读验证器的散列和文件身份检查，schema 与行读取绑定同一已核验的文件描述符。
候选通过 Arrow 原始值读取，保留大整数精度和有效位。NaN、Infinity 等非有限值直接拒绝，
不能转成 NULL。
按批读取，先扫描全部身份列，再只保留多类别事件完整行。内存随身份行数及冲突候选数增长。

`disclosure_available_date` 是披露日加指定日历延迟的假设。`usable_at` 至少晚于所有候选的
源观察时间，同时不早于该日期 UTC 零点。该时间采用日历规则，也不证明历史当时可见。
`time_basis` 明确标记 reconstructed，`historical_pit_certified=false`。
缺失或无效身份、观察时间、重复类别身份、非合并报表均拒绝审计。

## 消费边界

只返回多类别事件，单类别事件计入输入行数但不输出。因此空事件列表无法证明完整覆盖。
输出始终 `published_asset=false`、`complete_current_eligible=false`，不能替代完整 current。
共同值只在本次限定投影上成立，无法证明完整候选重复或财务口径已经业务确认。
投影冲突事件不输出任何共同值，即使部分字段相同。调用方必须保留缺口和阻断标记，
不得丢弃事件后对旧报告前向填充。本模块没有时序填充、资产写入、类别选择或 current 切换路径。

若以后实现 partial 研究资产，需要另建明确的资产契约、覆盖与缺口清单、冲突持续区间和
消费者显式接受机制。本次证据不能充当该资产。现有 statement-selector.v1 和 legacy
retry_identity 均保持原有语义，不由本证明补造或替代。
