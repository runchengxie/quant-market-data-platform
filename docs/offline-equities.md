# 美股分钟数据的离线无损布局转换

此工具将已经审计完成的按日 Parquet 数据集转换为按代码、身份分段和年份保存的
ZSTD6 镜像。它不下载行情，不启用新的平台市场，也不注册或切换 current asset。
原始数据、真实运行配置和生成文件保留在仓库外。

```bash
uv sync --locked --extra offline-equities
uv run --locked --extra offline-equities python -m quant_market_data_platform.offline_equities \
  --source /datasets/equities/by-day \
  --identity-root /datasets/equities/identity-evidence \
  --output /datasets/equities/by-security-v2
```

三个路径都必须显式提供。输入和输出目录不能重叠。预留至少 20 GiB 空间作为最低检查门槛。完整转换所需空间还包括中间数据和最终镜像。

输入包含 `summary.json`（`status=complete`）、`parquet_manifest.csv`、
`file_quality.parquet` 和清单内的 `parquet/` 文件。清单记录每个源文件的交易日、
lane、相对路径、行数、字节数及 SHA256。工具拒绝清单中越出源 Parquet 目录的路径。
身份证据目录包含 `wolf_identity_rule.json` 和
`reference_inputs/security_identity_intervals.csv`，其哈希写入运行配置。

身份规则沿用这一离线数据集的约定：保留已有候选键，否则标为 `unresolved:ticker`。
WOLF 按 2025-09-29 分成旧权益和重组后权益。身份证据用于固定血缘，本工具不会自动解析
文件并推断新身份规则。候选键对应的证券身份还需独立核验。不同代码和结算方式分别保存。
没有复权、填补分钟、过滤记录或恢复历史指数成分。

## 目录、校验和续跑

最终数据位于 `parquet/symbol=.../security=.../year=.../data.parquet`。
原始物理列及其类型、空值保持不变，只增加 `ResearchKey`。
`BCPC` 与 `BCpC` 等代码必须区分。小写 ASCII 字母及末尾的点使用可逆百分号编码，
避免 Windows 大小写和末尾点规则造成目录冲突。`BCpC` 对应 `symbol=BC%70C`。
DuckDB hive 分区会还原原始代码，不需要用户统一大小写。

第一阶段按月写入 128 个哈希分桶的中间文件，第二阶段写出按代码和年份的镜像。
两个阶段均逐行组读回所有列，核对类型、有效位、值及行序的逻辑 SHA256。
最终文件还验证严格递增的交易日与分钟顺序，拒绝重复分钟。
源文件压缩哈希和 Parquet 页校验也会核对。最终文件使用 ZSTD6，临时 stage 使用 ZSTD1。

重复运行相同命令会核对配置、实现文件组合哈希、输入属性、身份证据和已提交文件。
未完成的任务目录或部分发布结果移入 `recovery/` 后重做。源数据不删除。
同一输出目录只允许一个持有 PID 和进程创建时间锁的任务。
运行状态保存在 `progress.json`。只有 `status=complete` 才表示全量完成。
`file_index.csv`、`catalog.duckdb` 和 `summary.json` 在完成阶段生成。

查询只读取最终 `parquet/`，不要递归合并 `staging/`、`work/`、`recovery/` 或试点。
完成后的 DuckDB 目录视图为 `bars_by_security`，可按精确大小写的 `symbol` 和 `year` 筛选。
`--dates`、`--symbols` 可生成单独试点。变更选择、分桶数、行组参数或实现需要新的输出版本。

## 冻结版本与正式代码

仓库模块是 `security-year-lossless-v2`，实现拆分在
`src/quant_market_data_platform/offline_equities/`。
此前仓库外的单文件 v1 运行仍使用其冻结脚本与检查点。不能用本模块接管 v1 输出目录。
原单文件脚本及真实验收收据仍保留在仓库外用于溯源。
v2 已覆盖大小写目录冲突、尾点、字段一致性、身份分段、发布恢复和输入边界的合成回归测试。
v2 目前只在合成样本上验证。真实历史结果仍属于冻结 v1 及其迁移收据。

英文说明见 [offline-equities.en.md](offline-equities.en.md)。
