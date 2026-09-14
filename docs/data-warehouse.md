# 数据目录、标准层与 DuckDB 查询

`marketdata data ...` 负责依据数据清单的目录刷新、标准层物化和 DuckDB 查询，相关能力由本平台统一维护。

默认产物根目录解析顺序为：

```text
--artifacts-root 参数
DATA_PLATFORM_ROOT
artifacts/
```

catalog SQLite 路径可用 `DATA_PLATFORM_METADATA_DB_PATH` 覆盖，DuckDB warehouse 路径可用
`DATA_PLATFORM_WAREHOUSE_DB_PATH` 覆盖。未配置时，两者都写入
`<artifacts_root>/metadata/`。


## 刷新 catalog

```bash
marketdata data catalog \
  --artifacts-root "$DATA_PLATFORM_ROOT"
```

默认写入：

```text
<artifacts_root>/metadata/catalog.sqlite
<artifacts_root>/metadata/catalog_summary.csv
```

## 物化标准层

从资产目录物化：

```bash
marketdata data materialize \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --name a_share_daily_panel \
  --market a_share \
  --preset generic \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_daily_latest" \
  --frequency D
```

输出默认位于：

```text
<artifacts_root>/standardized/<market>/<dataset>/<name>/
```

## 查询标准层

查询功能需要安装 DuckDB：

```bash
uv sync --extra dev --extra duckdb
```

```bash
marketdata data query \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --sql "select 1 as value"
```

查询时会扫描标准层的数据清单并在 DuckDB 中注册视图。需要把结果写出时使用 `--format` 和 `--out`。
