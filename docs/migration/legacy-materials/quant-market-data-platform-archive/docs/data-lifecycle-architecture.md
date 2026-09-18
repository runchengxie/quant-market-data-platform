# 数据代码生命周期分层

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

平台代码按共享市场数据的生命周期逐步收敛到以下依赖方向。

```text
provider API
  -> ingest
  -> raw immutable asset
  -> standardize
  -> standardized / canonical asset
  -> quality receipt
  -> publish
  -> published asset
```

## 层次职责

`market_data_platform.ingest` 负责 provider 接入、请求可靠性、配额和 raw landing。当前首批入口位于 `ingest.tushare.daily`，底层 provider runtime 会在后续迁移中分批移入该层。

`market_data_platform.standardize` 负责字段映射、类型转换、去重、排序、时间处理、来源融合和标准化数据集物化。它只消费已经落盘的 raw asset，不回依赖 `providers` 或 `ingest` 实现。

quality 负责可用性判断和 receipt。publish 负责版本、alias、manifest 与 provenance。warehouse 负责查询和物化。

模型 window、embedding、label、训练样本和研究特征由模型或研究项目拥有，不进入通用标准化层。

## 已迁移链路

TuShare A 股 `daily_clean` 的 build 实现和 daily schema 已移入 `standardize`。旧 `providers.tushare_a_share_clean` 继续提供兼容入口，现有调用路径可以逐步迁移。

A 股分钟融合和 fused dataset materialization 已按生命周期拆分：

```text
providers.a_share_minute_fusion
  -> standardize.fusion.a_share_minute

providers.a_share_minute_build
  -> standardize.materialize.a_share_minute

standardize.materialize.a_share_minute
  -> quality_a_share_minute
  -> publish.json_manifest
```

`standardize.fusion.a_share_minute` 拥有 canonical schema、来源标准化、去重、确定性排序、Guan/TuShare 融合、Guan deal 聚合和 canonical Parquet 写入。

`standardize.materialize.a_share_minute` 拥有 build options、source inventory、resumable checkpoint、partition workers、dataset lock 和 build orchestration。最终 dataset acceptance 调用独立的分钟 quality API，durable manifest JSON 通过 publish 层持久化。deal checkpoint 继续属于 materialization execution state。

仓库当前已有顶层 `market_data_platform.quality.py` 模块，因此分钟质量实现暂放在 `market_data_platform.quality_a_share_minute`，避免同时创建同名 `quality/` package。后续如整体迁移 quality package，再统一收敛该模块。

旧 `providers.a_share_minute_fusion` 和 `providers.a_share_minute_build` 保持兼容入口。历史 `a_share_minute_*_partNN` 文件只保留兼容转发，不再拥有分钟 transformation、validation、checkpoint 或 manifest 业务实现。

## 边界验证

`tests/test_data_lifecycle_architecture.py` 会阻止 `standardize` 直接 import `providers` 或 `ingest`。daily clean 继续锁定旧入口与新入口的函数身份一致。分钟链路对可直接转发的 schema、normalization、fusion、options 和 validation 使用 identity 兼容。

分钟 build 和 Guan deal aggregation 的旧入口保留一层薄 wrapper，用于兼容历史测试和调用者对 provider facade 注入/monkeypatch optional aggregation engine 的行为。这些 wrapper 只做依赖注入，不拥有 transformation 或 materialization 逻辑。

`tests/test_a_share_minute_lifecycle_compatibility.py` 额外验证旧/新入口的 canonical contract、fusion 内容 hash、Guan deal pandas 聚合结果和 build dry-run payload 等价。

## 后续迁移顺序

后续继续按完整链路迁移 provider runtime 和研究派生资产。优先处理 TuShare 分钟 raw ingress 的剩余 provider runtime，再处理 fundamentals、ownership、flow 和 hotspot。每次迁移保留旧入口，并使用现有 contract、quality receipt 与输出一致性测试验证行为。
