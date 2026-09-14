# 中国宏观与产业情境数据

`cn_context` 是独立于 A 股行情契约的组合数据域，用于保存宏观、利率、信用、价格、产业与能源等研究情境数据。

它不会改变 `a_share_current.json` 的 `market=a_share`、`provider=tushare` 语义。当前情境数据契约位于：

```text
$DATA_PLATFORM_ROOT/metadata/current_assets/cn_context_current.json
```

第一版 current contract 使用：

```text
market=cn_context
provider=composite
```

稳定资产键为：

- `context_catalog`
- `context_observations`
- `context_pit`
- `context_release_calendar`

## 时间点语义

标准化 observation 同时保留 `published_at`、`observed_at`、`ingested_at`、`source_retrieved_at` 与 `available_at`。

研究可见性同时要求：

```text
available_at <= as_of
source_retrieved_at <= as_of
```

第二个条件用于阻止晚抓的历史网页、晚获取的修订值或后来补齐的发布时间信息穿越回历史研究。

同一个 `series_id + period_end` 存在多次修订时，PIT 读取器选择 as-of 当时已经可见的最新 vintage。

历史值如果缺少可靠发布时间或当时抓取证据，会标记：

```text
reconstructed=true
revision_covered=false
```

这类数据可以用于探索，不应作为晋级证据冒充 revision-safe 历史。

## TuShare 第一批数据

第一批适配器固定覆盖以下接口：

```text
shibor
shibor_lpr
cn_m
sf_month
cn_pmi
cn_cpi
cn_ppi
cn_gdp
cn_schedule
```

供应商接口只负责原始数据获取。稳定 `series_id`、PIT 语义和 current contract 由本平台维护。

## 国家统计局

国家统计局适配器面向当前发布库：

```text
https://data.stats.gov.cn/dg/website
```

第一批系列包括：

```text
规模以上工业增加值同比
发电量
火力发电量
水力发电量
核能发电量
风力发电量
太阳能发电量
原煤产量
原油产量
天然气产量
```

规模以上工业增加值使用已核实的新发布库 `directoryId:indicatorUUID`。能源系列在抓取时使用官方搜索接口解析指标，并要求 `expected_name` 唯一精确匹配。零匹配或多匹配都失败关闭，不自动选择相似名称。

NBS 数据接口本身不提供本平台所需的可靠历史发布时间证据。因此观测默认从实际抓取时间开始可见。离报告期很久以后才做的历史回填会标记为 reconstructed。长期 revision-safe 历史需要持续积累 observed vintage。

示例：

```bash
marketdata context fetch \
  --provider nbs \
  --dataset industrial_value_added_yoy \
  --period 202607
```

## 国家能源局

国家能源局适配器直接保存并解析官方 `nea.gov.cn` 发布页。第一批月度全国用电系列包括：

```text
全社会用电量
第一产业用电量
第二产业用电量
工业用电量
高技术及装备制造业用电量
第三产业用电量
城乡居民生活用电量
```

每个可用系列同时产生 level 与同比。工业和高技术及装备制造业细分在旧发布中并非始终存在，因此属于可选细分。总量、第一产业、第二产业、第三产业和居民生活是当前 parser 的必需字段。

页面解析器要求标题、发布时间、月度段落和 `亿千瓦时` 单位符合冻结结构。结构变化会报错，不返回部分伪正常结果。

示例：

```bash
marketdata context fetch \
  --provider nea \
  --dataset electricity \
  --source-url https://www.nea.gov.cn/<official-release>/c.html
```

只有在官方发布日期附近完成的抓取才标记 observed vintage。旧页面今天重新抓取仍保留官方 `published_at`，但 `available_at` 和 PIT 可见性受实际 `source_retrieved_at` 约束，因此不会进入更早的回测时点。

## 抓取

TuShare 示例：

```bash
marketdata context fetch \
  --provider tushare \
  --dataset shibor \
  --start-date 20260801 \
  --end-date 20260828
```

抓取结果不会直接覆盖标准化资产。每次响应先密封到不可变 raw snapshot：

```text
assets/context/cn/raw/<provider>/<dataset>/vintage=<UTC timestamp>/
```

目录包含：

```text
raw.bin
receipt.json
manifest.seal.json
```

`receipt.json` 记录来源、抓取时间、content type、请求参数、parser version、字节数与 SHA-256。

月度 TuShare 数据需要发布日证据时，应同时定期抓取：

```bash
marketdata context fetch \
  --provider tushare \
  --dataset cn_schedule \
  --end-date 20260831
```

## 构建

从已密封快照构建标准化 observation 和 PIT 输入：

```bash
marketdata context build \
  --as-of 20260828
```

构建产物位于：

```text
assets/context/cn/builds/as_of=20260828/build=<UTC timestamp>/
```

historical build 只读取 `source_retrieved_at <= as_of` 的 raw snapshot。PIT 资产保留截至该时点已经存在的全部 vintage，之后由 as-of loader 按查询日期选择当时最新修订。

`latest` 只指向最近一次完成的 build。`build` 不重新访问数据源。

## 发布

发布最近一次完成 build：

```bash
marketdata context publish \
  --as-of 20260828
```

发布后可使用通用 `PublishedAssetContract`：

```python
from market_data_platform import PublishedAssetContract

contract = PublishedAssetContract.load_current(
    "/data/market-data-platform",
    market="cn_context",
)
pit = contract.asset("context_pit")
print(pit.provenance_dict())
```

组合 manifest 的 `lineage.sources` 保存所有进入该 build 的 raw snapshot 的 provider、dataset、抓取时间、hash 与 parser version。

## 检查

查看指定日期当时可见的 PIT 状态：

```bash
marketdata context inspect \
  --as-of 20260828
```

输出包含 current contract hash、PIT asset provenance、可见行数和 audit。audit 至少报告：

```text
revision_covered
freshness_verified
series_missing
series_stale
selected_vintages
max_observation_age
reconstructed_series
```

`freshness_verified` 只根据每个 series 最新可见 observation 判断。历史 observation 不会因为年龄较大而被删除，因为滚动变换、同比和修订研究仍需要完整历史。

## 依赖与凭证

TuShare 抓取使用现有凭证机制：

```bash
uv sync --extra tushare
```

NBS 和 NEA 适配器只使用 Python 标准库网络能力，不新增 provider SDK 依赖。

凭证继续放在仓库规定的未跟踪 secret 文件或环境变量中。raw snapshot、Parquet、build 和 current 实际数据均位于 `$DATA_PLATFORM_ROOT`，不会提交 Git。
