# Knowledge v2 数据集试点契约

`docs/knowledge/pilot.yml` 列出本仓库纳入检索的两份 A 股数据集说明。每条记录的 `asset_key` 对应[数据契约](contracts.md)中的稳定键名，`document` 是仓库相对路径。只有清单列出的页面进入索引。

页面使用单个 YAML frontmatter 对象，字段固定为 `schema_version`、`id`、`type`、`owner`、`status`、`last_verified`、`source_of_truth`、`authority_ref`、`relations` 和 `asset_key`。当前试点仅接受 `knowledge/v2`、`dataset`、`quant-market-data-platform`、`active`、`false`，以及 `quant-market-data-platform.dataset.a_share.<asset_key>` 形式的 ID。`authority_ref` 使用 `asset:quant-market-data-platform:a_share:<asset_key>`。`last_verified` 为 ISO 日期，`relations` 为字符串列表。

`status: active` 表示这份知识页面参与检索，实际数据资产发布状态需查询当前环境的契约。发布状态和路径仍以[当前数据契约](contracts.md)及其 current manifest 为准。索引只读取仓库文档，不读取 `$DATA_PLATFORM_ROOT` 或生产数据。

开发时运行：

```bash
python scripts/dev/knowledge_index.py validate --manifest docs/knowledge/pilot.yml
python scripts/dev/knowledge_index.py build --manifest docs/knowledge/pilot.yml --output /tmp/qmd-knowledge-v2.json
```

构建结果是按 `id` 排序的 JSON 数组，包含页面元数据和仓库相对 `path`。输出必须位于仓库外，不提交生成的 JSON。此脚本是开发校验器，不属于公开 `marketdata` CLI。
