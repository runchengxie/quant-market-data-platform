# A 股分钟数据 Ingest / Standardize 边界设计

## 背景

当前分钟链路仍集中在 `market_data_platform.providers`。TuShare 下载、Guan 原始数据读取、单位规范化、来源融合、分区物化、质量检查、checkpoint 和发布 manifest 由相邻的 `partNN` 模块共同承担。日频 `daily_clean` 已经证明，保留旧 provider 入口并把真实实现迁移到生命周期包，可以在不改变调用方的前提下收紧依赖方向。

本次只处理 A 股 1 分钟数据，不迁移 fundamentals、flow、hotspot、ownership 或其他研究派生资产。

## 目标

建立以下稳定依赖方向：

```text
provider API
  -> ingest.tushare.minute
  -> immutable raw partitions
  -> standardize.fusion.a_share_minute
  -> standardize.materialize.a_share_minute
  -> quality receipt
  -> publish manifest / alias
```

目标包位于 `src/market_data_platform/` 下：

```text
market_data_platform/
├── ingest/tushare/minute.py
└── standardize/
    ├── fusion/a_share_minute/
    └── materialize/a_share_minute/
```

旧入口继续存在：

```text
providers.tushare_a_share_mins
providers.a_share_minute_fusion
providers.a_share_minute_build
```

旧入口只负责兼容导出和必要的 legacy kwargs / monkeypatch seam，不再新增真实的下载、融合或物化实现。

## 边界职责

### `ingest.tushare.minute`

负责 TuShare 分钟数据的 provider-facing 行为：client、凭证和 URL 解析，请求重试、配额和请求策略，交易日与股票池解析，分页、chunk 和 no-data exception，raw immutable 分区、sidecar、下载 receipt，以及可恢复下载和安全跳过已有分区。

该层可以依赖 provider runtime 和 raw landing 工具，但不得依赖 `standardize` 的融合或物化实现。

### `standardize.fusion.a_share_minute`

负责已经落盘的输入转换为 canonical minute frame：TuShare 字段规范化、legacy Guan 单位转换、Guan annual / Guan deal / TuShare 来源融合、BJ overlay、symbol 和时间规范化、来源内去重、唯一键、确定性排序以及 canonical Parquet 分区写入。

该层只消费明确的输入路径或 DataFrame / Arrow table，不发起 provider 请求，不管理下载配额，不写 quality receipt 或发布 alias。

### `standardize.materialize.a_share_minute`

负责构建流程编排：build options 和输入路径解析，交易日遍历，staging、checkpoint、fingerprint，lock、dry-run、resumable execution，以及调用 fusion 产出标准化分区。

checkpoint 是物化执行状态，继续归该层；manifest 最终持久化和 alias 切换不归该层。

### `quality` 与 `publish`

现有质量检查继续负责 coverage、241 根分钟 bar、唯一键、schema 和 receipt。现有 publish 代码继续负责 manifest、provenance、版本和 alias。此次迁移只调整调用关系，不改变 receipt schema、manifest schema 或发布规则。

## 模块迁移策略

不把旧 `part01/02/03` 原样复制为新的 `partNN` 文件。迁移后的代码按职责组织，目标结构为：

```text
standardize/fusion/a_share_minute/
├── __init__.py
├── schema.py
├── normalize.py
├── guan_deals.py
├── fusion.py
└── writer.py

standardize/materialize/a_share_minute/
├── __init__.py
├── options.py
├── checkpoint.py
├── build.py
└── validation_adapter.py
```

实际文件可以少于示意结构，但每个新模块只承担一个职责。第一阶段允许内部 helper 组合，以降低迁移风险；第二阶段再根据质量债务报告拆过长函数。

## 兼容约束

迁移不得改变以下行为：公开函数名、参数默认值和返回 payload 关键字段；TuShare 优先于 Guan 的来源优先级；Guan legacy 单位换算；BJ 覆盖规则；canonical schema、唯一键和排序；checkpoint fingerprint、resume、lock、dry-run；manifest、quality receipt、alias 的路径和字段契约；以及旧入口的 monkeypatch 注入点。

新入口与旧入口应在兼容层测试中验证函数 identity。若历史 seam 不能直接保持 identity，必须保留等价薄 wrapper 并记录原因。

## 测试与验收

每个迁移单元遵循先写失败测试、再实现、再回归的顺序。最低验收集合包括：

1. lifecycle AST 检查确认 `standardize` 不 import `providers` 或 `ingest` 实现。
2. 旧 fusion、build、minute ingest 入口与新入口的 public API 兼容测试。
3. Guan、TuShare、BJ 小型 fixture 的 canonical frame 和输出 hash 一致。
4. checkpoint fingerprint、dry-run、lock 和中断恢复行为保持一致。
5. quality receipt、manifest payload 和发布路径 contract 保持一致。
6. 全量 pytest、Ruff、format、ty、quality debt、maintainability、compatibility 和 architecture gate 通过。

不使用真实 provider 凭证作为单元测试依赖，不提交下载产物、Parquet、token 或运行报告。

## 非目标

- 不拆分独立仓库。
- 不改变数据内容、来源优先级或生产 alias。
- 不迁移 fundamentals、flow、hotspot、ownership 或 context。
- 不删除旧 `providers.*` 入口。
- 不把质量校验或发布 manifest 重新塞回 standardize。
