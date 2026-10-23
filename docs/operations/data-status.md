# 数据运行状态汇总

使用只读命令一次查看分钟资产截至日期、历史验收缺口、指数恢复样本、生产版本和服务最近退出状态。
数据已经更新与某次服务失败分别显示，避免用旧失败记录否定后续发布，也避免用进程成功代替数据验收。

```bash
uv run --extra dev python scripts/operations/data_status.py \
  --root "$DATA_PLATFORM_ROOT" --expected-date 20261023 \
  --production-root "$CODE_ROOT/production" --with-systemd \
  --evidence fundamentals="$TASK_REPORT/asset.json" \
  --evidence coverage="$TASK_REPORT/coverage-summary.json"
```

`--expected-date` 必须显式指定业务目标交易日。工具不推测交易日历，不请求供应商，不发送消息，
也不修改生产或回执。JSON 写入标准输出，可由调用方重定向到独立任务目录。
默认不调用 systemd，启用后只读取五个既有服务的状态和退出码，每个调用最多五秒。
`--production-root` 可省略，提供时仅报告三个生产 current 路径及是否存在。

分钟状态 `current` 仅表示有效位置上的运营回执日期覆盖目标日，检查范围不含逐条数据重审。
原始 `quality_status` 保留，契约记录的日期与实际回执不同会标注 `contract_date_matches=false`。
实体 current 副本必须与其版本回执哈希一致。目录须位于所提供的数据根目录下，外部存储映射
需要通过独立契约工具检查，本入口不会自动放行越界路径。

历史监控默认超过六小时显示 `stale`，可用 `--max-age-hours` 指定窗口。只有完成状态、完整
就绪标志、零剩余与延后日期、无隔离日期同时成立，才显示 `reported_ready`。
该名称仅表示监控报告的状态，正式验收仍需独立凭证。未来或无时区时间戳、坏日期、坏计数均不能
用于就绪判断。缺失、畸形、过期状态会保留，不能按零缺口处理。

指数恢复最多读取1000个汇总文件，每个JSON最多8MiB，按完成时间选最近20条。
超出上限会要求缩小或归档输入窗口，不会选择遍历顺序中的任意20条。样本为零时显示
`no_samples`，不能算成100%成功。统计范围限定为已记录的运行，可用率需要另外计算。

`--evidence NAME=PATH` 只展示白名单标量和输入哈希，始终标为 `unverified`。
它不调用覆盖凭证或基本面资产的独立消费者，也不认证历史PIT。正式消费仍需对应的哈希验证、
部分资产显式接受和冲突处理接口。嵌套载荷不会被当作摘要输出。

命令退出0表示完成了状态汇总，判断结果看 `needs_attention` 及各组件字段，不能把退出0
用作发布门禁。输出schema为 `data-operations-overview.v1`，并明确标记 `receipt_inspection_not_data_acceptance`。
