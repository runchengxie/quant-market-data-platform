# 板块资金流 Parquet 归档 v1

[English](board-flow-archive.md)

数据平台拥有这份无损归档，回放应用只读消费文件，不连接采集器，也不导入供应商源码。
归档验证证明文件完整且可以恢复，不证明供应商统计口径正确、交易日完整或采集成功。

## 格式与数据含义

manifest.json 使用 UTF-8 JSON，format 固定为 board-flow-parquet-v1，
未知版本和任意层级的重复 JSON 键均拒绝读取。
以下六张表均须出现在文件清单中：

| 表 | 文件布局 | 含义 |
|---|---|---|
| board_catalog | board_catalog/all.parquet | 最近观测的名称、分类与首次和末次时间 |
| board_catalog_daily | board_catalog_daily/YYYY-MM-DD.parquet | 当天实际观测到的名单 |
| flow_snapshot | flow_snapshot/YYYY-MM-DD.parquet | 当时实际采到的累计值 |
| flow_minute | flow_minute/YYYY-MM-DD.parquet | 后来取回的供应商分钟累计曲线 |
| collection_run | collection_run/all.parquet | 已保留的采集结果 |
| schema_meta | schema_meta/all.parquet | SQLite 结构元数据 |

非日分区表的 all.parquet 可以是零行。v1 导出要求三张日分区表各至少有一个日期，
不同表可以覆盖不同日期。缺日期、板块或分钟不视为零流入。后来更新的名单不能证明
历史覆盖率或当时名称。分钟曲线不能重建当时实际采到的快照。板块成分重叠，不能把
板块净流入相加当作全市场资金流。采集状态与 detail 原文保留，新回执可能为 partial，
预期范围、实际尝试与已有归档分别计数。旧回执缺字段时保留未知，不能根据旧的成功标签
默认判定全覆盖，也不回写历史状态。

每个 Parquet 必须在清单中恰好出现一次。不接受未列出的 Parquet、重复表与日期、
目录内符号链接或非法日期。文件路径由固定表名和真实 ISO 日期生成，不执行清单指令。
允许附带说明文件。现有旧 v1 无需补 producer 元数据或 path 字段，也无需改写清单。

## 清单与数值

清单保存 snapshot_started_utc、源文件名 source_file、tables_sql、indexes_sql、
SQLite 的 user_version 和 application_id，以及 files。每个文件项保存 table、date、
有序 columns、SQLite types、有序主键 key、rows、bytes 和小写十六进制 sha256。
非日分区 date 为 null，SQLite 两个整数元数据范围为 0 至 2147483647。
新导出增加可选 producer 信息，包括 owner 名称、可取得时的安装包版本、实际归档与
契约模块 SHA 及固定结构身份，不猜测 Git revision。旧归档缺少这些信息仍兼容。
记录的文件身份不能作为真实性证明，也不能据此宣称旧归档来自当前代码。
不可变 release 与验证命令由外部复现记录绑定。

结构固定为 [board_flow_store.py](https://github.com/runchengxie/quant-market-data-platform/blob/b2a40031a42d3b25b5b1ca26e2a3d4ca4a336fff/src/quant_market_data_platform/board_flow_store.py)
中的六表与两个显式索引。未知列、类型、主键、索引、表、view 或 trigger 均拒绝。
清单 SQL 仅与可信结构比较，恢复时绝不执行它。原结构文本仍保留供检查。v1 固定结构身份，后续在线库或索引变更不能悄悄改写同一格式的含义。归档保留声明列、结构与列出的 SQLite 元数据，不保留隐式 rowid、物理页布局和空闲空间，不声称 SQLite 文件字节相同。

SQLite 实际值须符合声明类型：TEXT 为字符串，INTEGER 为整数，REAL 为浮点数
或可精确表示的整数，空值遵循结构约束。拒绝 BLOB 转文本、非整数转整数等静默转换，
并逐值核对转换前后记录。

TEXT 对应 Arrow string，INTEGER 对应 int64，REAL 对应 float64，保留列顺序与数值，
快照中原有空值仍为空。每个文件按完整主键排序，主键不得为空或重复，日分区内每行
trade_date 必须匹配文件日期。验证大小、SHA、实际 Arrow 结构、行数、主键与日期绑定
后才能使用数据。Parquet 使用 Zstandard 压缩。

日期与时间戳仍为字符串。trade_time 是上海本地墙钟分钟 YYYY-MM-DD HH:MM:SS，
异地显示时明确使用 Asia/Shanghai。采集和归档时间保留原文与已有时区偏移。
_yuan 字段单位为元，_pct 字段为百分比。分钟资金流是当日累计值，5、15、30 分钟变化
应由有效端点相减并说明午休规则，不能把累计值逐分钟相加。

## 发布、恢复与边界

导出使用 mode=ro 与 query_only=ON，在一个 SQLite 只读事务内分批读取。先写独立
staging，核对 Parquet，恢复到临时 SQLite，逐值比较并运行 integrity_check 后，才以
原子且不覆盖目标的方式发布。入口清单的字节、SHA 和文件身份始终绑定，成功或发布前重验同一文件清单及全部
文件大小与 SHA，结果包含 manifest_sha256。输入仍须保持不可变，这些检查
未对多个源文件建立文件系统事务锁。已有路径及发布时并发创建的路径都保留。该操作需要
Linux renameat2(RENAME_NOREPLACE) 或 Windows 原生不覆盖 rename，不支持时拒绝。

恢复先检查完整输入，只使用本地可信结构，并只发布到归档目录外的全新 SQLite。
错误、损坏或中断不会覆盖在线库。普通异常只清理本次 staging，进程被强杀可能留下
临时目录，需核对归属后另行处理。重试创建新 staging，不采纳或删除未知残留。
多文件在断电时的事务持久性仍需独立保障。

导出的是整库一致性快照，并按日分文件。自动日增量发布仍需独立设计。它不消除多次快照间的重复，
不接定时器、不清理在线库、不改索引、不设保留期限，也不更新共享别名。
这些生产决策不能由一次归档验证代替。
