# 固定多项已发布资产的元数据

[English](published-asset-snapshot.en.md)

研究任务需要同时引用几项 current 资产时，可以先保存一份明确的输入身份记录：

```bash
marketdata contract capture --artifacts-root /external/data-owner \
  --asset daily_clean --asset trade_cal > /external/task/input-snapshot.json
```

资产键必须存在于指定契约，示例中的键仅作说明。用 `--current-contract` 可选择明确的契约文件。
命令只把 JSON 打印到标准输出，不发布数据、不切换别名、不扫描数据行，也不默认写入结果文件。
重定向的记录应保存在调用方的任务数据目录。

记录格式为 `marketdata.asset-snapshot.v1`，复用既有契约和 manifest 摘要、manifest 规范化指纹、
血缘及实际解析路径。元数据解析与字节哈希来自同一次读取，避免两次读取之间内容变化导致口径
与摘要不对应。采集前后核对已加载契约，重新检查已固定的 manifest，再次解析所需资产，以拒绝
检测到的别名或元数据变动。

Python 入口为 `PublishedAssetContract.capture(keys)`。原来的 `require_assets(keys)` 仍按调用
顺序返回 tuple，保留重复键及空输入的行为，但使用同样的一致性检查。返回对象的 `assets` 保存
固定引用。`validate_metadata()` 重新检查其物理 manifest，不追随后来推进的 current 别名。

采集范围限于元数据一致性。记录显式标明 `integrity_scope: manifest_identity`、
`data_content_verified: false`、`atomic_transaction: false`。既有 `content_fingerprint`
仍是 manifest 的规范化哈希，不能解释为完整 Parquet 数据树校验。乐观检查不锁定写入者，不能
阻止后续原地修改，也不能排除先改后恢复的竞态。研究消费者仍需固定并校验实际读取的字节，或
使用真正不可变的 owner 发布版本。数据文件被改而 manifest 不变时，本记录无法检出。

回归测试会在采集期间移动别名、改写契约或 manifest，并核对解析内容与哈希来自同次读取。
测试仅使用合成数据，不修改生产资产和共享 current 指针。
