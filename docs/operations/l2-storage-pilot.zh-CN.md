# 供应商原始 L2 存储试点

[English](l2-storage-pilot.md)

将按日期保存的 Guan 风格 CSV 压缩包转换成独立的全字段 Parquet 版本，优先支持少数证券的盘中区间查询，同时保留流式全日读取。试点不会替换既有日／月文件契约，不调整价格单位、不删除重复事件、不删除输入，也不切换 current/latest。

安装现有 `offline-equities` extra（`uv sync --locked --extra offline-equities`），另外提供可信的 7-Zip 可执行文件，须支持 `l -slt` 和 `x`。输入、输出和暂存目录均应位于源码仓库之外。每个日期和配置使用各自的任务目录。

```bash
python -m quant_market_data_platform.l2_storage build \
  --archive "$ARCHIVE" --expected-sha256 "$ARCHIVE_SHA256" \
  --output "$OUTPUT" --scratch "$SCRATCH" --extractor "$EXTRACTOR" \
  --buckets 64 --row-group-rows 65536 --zstd-level 6
python -m quant_market_data_platform.l2_storage verify --root "$OUTPUT"
python -m quant_market_data_platform.l2_storage query --root "$OUTPUT" \
  --modality trades --symbol 000001.SZ --start 100000000 --stop 100500000 --count
```

`orders`、`trades`、`snapshots` 保留全部原始字段，包括未知字段和证券标识。输入为 GB18030 CSV，路径格式是 `YYYYMMDD/<security>/<type>.csv`。默认要求记录中的证券和自然日与路径一致。时间保留原始 `HHMMSSmmm` 数值，查询包含起点、不包含终点，不做时区转换或价格单位推断。空字符串保持为空字符串。只有整个数据类型中每个值都能精确转换回原文本时，该列才使用整数存储。

按证券标识的 UTF-8 CRC32 对桶数取模进行文件路由。行组不跨原 CSV 文件。同一文件内按原时钟和行号排序，按 `_source_symbol` 分组、按 `_source_row` 排序可还原各个 CSV 的记录顺序。保留全部源 ID、重复记录和原字段。`_time` 只是额外查询键，不代替原时钟字段。

v2 存储格式增加 `_source_symbol`，保留原始目录标识。经过审计确认来源目录与记录使用不同交易所后缀时，可以显式指定 `--identity-policy preserve-source-labels`。每个文件仍只允许一个合法记录标识，六位数字代码必须与目录相同。混合标识、不同数字代码、日期和时钟错误仍会停止任务。两个标识都保留原文，数字相同也不会被认定为同一经济标的或完成交易所映射。

证券查询使用原始 `万得代码`，通过字段清单找到所有匹配的来源桶，保留重复来源。使用数据或下线原件前，应查看 `identity_summary` 及每个文件的 `record_symbol`、`identity_status`。同一数字代码在不同数据类型中可能存在不同后缀。已有 v1 数据继续使用其固定 v1 发布版本读取，v2 不会自动重写这些文件。

只有每个输入文件的记录数、全部字段逻辑内容都验证通过后，才发布 manifest。它绑定压缩包哈希、全局字段决策、输出哈希和路由配置。独立复验会读取每个输出文件、复核每个来源文件的逻辑证明。这证明字段值一致，不意味着 CSV 引号、原编码和换行字节一致，也不证明供应商数据完整或经济单位正确。

构建使用文件锁，拒绝覆盖未知目录或沿用已变化的来源／配置。恢复时重新检查全部解压文件的字段清单和冻结哈希，不把缓存清单当作实际输入。辅助输出使用独占临时文件，不跟随符号链接。中断后可重新验证已完成的桶文件并继续。未完成解压会保留现场，需要使用新的任务暂存目录。磁盘须保留 20 GiB 余量，按包内清单估算解压空间，并为输出保守预留空间。单个 CSV 原始大小超过 512 MiB 或解码后超过 1 GiB 时停止。更大的单文件需要扩展流式实现。不要为了适配磁盘或测试环境降低这些门槛。

Python 接口为流式 scanner：

```python
from pathlib import Path
from quant_market_data_platform.l2_storage.reader import scan

for batch in scan(Path(output), "trades", ["000001.SZ"], 100000000, 100500000).to_batches():
    consume(batch)
# 不指定证券和时间范围，即可流式读取某类数据的全部记录。
```

研究用事件流可能省略源 ID，不能替代这份原始存储。旧读取方需要显式适配后才能切换路径。完整日数据、查询兼容、来源依赖和容量通过验收前，保留原压缩包。下线原件是另行执行、绑定验收证据的操作，本 CLI 没有删除命令。已有快照损坏说明和修复依赖不进入自动迁移。
