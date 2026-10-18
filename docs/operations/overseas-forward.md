# 海外日线前向回执 v1

`python -m quant_market_data_platform.overseas_forward --root <owner-data>/forward/overseas-v1 --mode capture_probe`

每次执行重新请求 TuShare，强制使用 `TUSHARE_TOKEN_2` 及其配套自定义 route，通过生产安装的 `marketdata config run --config <private-config> -- ...` 注入凭证。复用 owner 的客户端与传输失败切换，不复制历史缓存作为新接收证据。每项请求使用 12 秒超时。失败只保存异常类别，避免凭证进入日志。

原始 CSV 只写 owner 数据目录。每次运行生成独立 UTC 时间戳与随机 ID 目录，`started.json` 在联网前独占创建，`receipt.json` 在采集完成后独占创建。CSV 同样独占创建。没有回执的 started 目录表示中断，禁止删除或补写假时间。回执包含实际请求开始/结束、来源日期、内容 SHA256、空结果和失败状态。闭市时间来自常规时段假设，不能证明供应商发布时间。FX 日期按下一 UTC 午夜结束解释，额外一小时缓冲由消费者执行。

计划执行应在上海工作日 08:44 启动，08:45 为严格截止。仅 `scheduled` 模式、08:43–08:45 实际开始并在 08:45 前完成才标记 `timely_capture`。周末、补跑和 `capture_probe` 均不能成为合格信号。SSE 是否交易由同次请求的 `trade_cal` 判断，QMR 在截止前另外封存预测。source 缺失由消费者按固定信号依赖判断。该回执标记本身不保证研究样本合格。

该目录是研究专用追加档案，不发布任何 `current`/`latest`，不改变已有生产资产、港股生产契约或定时任务。它不承诺文件系统级 WORM。独占写入防止本工具覆盖，外部修改由 hash 检查发现。生产必须使用已合并的不可变发布目录，不能引用开发 worktree。systemd 建议 `Persistent=false`，防止开机补跑冒充准点记录。仍由回执时间判定迟到。
