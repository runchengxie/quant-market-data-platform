# Research Parquet 文件读取

`market_data_platform.standardize.parquet` 提供研究数据文件的通用读取能力：

- 检查 Parquet 和 CSV 的列名
- 识别 Hive 分区目录中的字段和值
- 按请求列投影读取文件
- 在 Parquet 数据集读取失败时逐文件读取，并补回分区字段

这些函数只处理文件格式和数据集布局，不包含策略、特征、选股或回测逻辑。上层业务模块可以通过 `read_parquet_dataset_compat` 和 `select_available_columns` 复用这套能力。
