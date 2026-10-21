# Daily-clean 输入快照退役

[English page](daily-clean-snapshot-retirement.en.md)

仅对已逐文件审阅、仍有完整保留副本的重复输入快照使用此操作。默认执行完整读取校验，
不修改数据。操作前需另行审阅外部消费者引用并停止相关写入任务。命令检查本地 current
契约和 daily-clean manifest，不宣称覆盖所有外部引用。

```bash
python scripts/operations/retire_daily_clean_snapshot.py \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --snapshot "$REVIEWED_SNAPSHOT" \
  --peer-map "$REVIEWED_PEER_MAP" \
  --archive-dir "$NEW_RETIREMENT_ARCHIVE"
```

审阅校验结果后，在同一命令追加 `--apply --writers-stopped`。每次只处理一个快照。
归档目录必须不存在且位于输入快照树之外。命令持有 `.locks/a_share_current_publish.lock`
的非阻塞独占锁。锁被占用时退出。停止写入的确认仍由操作人员负责。

peer map 支持 JSON 或 gzip JSON 列表，每行包含 `path`、`peer`、`sha256`、`bytes`。
必须完整覆盖目标 receipt 中的全部数据文件。供体必须位于同一输入根下的独立完整快照，
相对数据路径一致，且不能是这份 map 中任意待退役目标。仅允许 `daily`、`adj_factor`、
`daily_basic`、`limit_status` 四个数据集，目标完整文件清单必须与 owner receipt 一致。
路径别名、符号链接、额外文件、不完整 receipt 和散列不一致均拒绝。

删除前，归档目录保存原始五份元数据、完整供体映射、元数据散列、peer map 散列和恢复说明。
完整预检全部通过后，仅逐个删除重复数据分片。原目录保留四份 manifest 和原始
`snapshot_receipt.json`。完成后新增 `retirement_receipt.json`，其 schema 为
`a_share.daily_clean_input_retirement.v1`，`status` 为 `completed`。
归档 receipt 持续记录 `removed` 数量和状态。中断或失败后保留现场，不支持自动续跑、
覆盖归档目录或重复退役。文件删除后的故障可能留下部分退役状态，需人工核对映射和文件。

恢复时，按归档 receipt 的 `files` 列表将每个 `peer` 复制回 `path`，逐文件校验 `bytes`
和 SHA256，保留原始 manifest 与 snapshot receipt。所有文件验证通过后再移除退役标记。
恢复前应确认供体仍保留并停止相关写入。该命令不提供自动恢复或目录级删除功能。

完整映射仅在准备完成、最终完成或失败时写入归档 receipt，正常删除过程每 128 个文件
写入小型 `progress.json`。进程被强制终止时，最后一批的实际删除数量可能领先于进度记录。
恢复必须逐个核对完整映射中的目标文件是否存在。已记录的准备状态与元数据仍完整保留。
publication 锁与停止写入确认共同防止最终预检和 unlink 之间的并发改写。操作期间不能重新
启动绕过该锁的写入进程。

第一次删除之前，目标目录也会写入 `prepared` 退役标记。失败时尽力将其更新为 `failed`。
任何状态的退役标记都会阻止该目录再次作为完整输入或新退役目标。已有退役标记的供体映射
构成恢复依赖：若另一退役目录的 `files[].peer` 位于待处理目标下，命令拒绝继续，
避免破坏此前的恢复路径。当前不支持多级供体链恢复。

## 维护预算

本次新增独立退役实现、日线状态分类、命令入口和安全回归测试，更新共享文件数与行数预算。
长函数、大文件和复杂度豁免预算保持约束。原有维护基线的两个漂移值已与 `5da3796`
核对一致，并在基线文件记录原因和收紧条件。后续合并或退役这些流程时应同步收紧预算。
