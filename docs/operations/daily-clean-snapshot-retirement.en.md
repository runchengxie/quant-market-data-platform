# Reviewed daily-clean input snapshot retirement

[中文说明](daily-clean-snapshot-retirement.md)

Use this Linux/POSIX operation only for reviewed input snapshots with independently
retained, byte-identical donors. Stop relevant writers and review external consumers
before execution. The command checks local publication references and prior retirement
donor dependencies. Its checks supplement the operator's external reference audit.

```bash
python scripts/operations/retire_daily_clean_snapshot.py \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --snapshot "$REVIEWED_SNAPSHOT" \
  --peer-map "$REVIEWED_PEER_MAP" \
  --archive-dir "$NEW_RETIREMENT_ARCHIVE"
```

The default performs a complete read-only preflight. Add `--apply --writers-stopped`
only after reviewing its result. The peer map is a JSON or gzip JSON list containing
`path`, `peer`, `bytes` and `sha256`, covering every receipt-listed shard exactly once.
Each donor must belong to another complete snapshot under the same input root and
must remain outside every target represented by the map. Symlinks, path escapes,
unexpected files, changed metadata, hashes and incomplete receipts cause refusal.

Apply holds the publication lock, archives the original five metadata files and full
donor mapping durably, and writes a target-side `prepared` retirement receipt before
unlinking data shards. It preserves manifests and `snapshot_receipt.json` in place.
Success changes the retirement receipt to `completed`. Failure records `failed` where
possible and preserves recovery evidence. The command refuses automatic retries.
Small progress receipts are batched, so an interrupted final batch requires checking
which mapped files still exist. Cooperative locks and stopped writers are required
throughout execution, including other cleanup tools.

Restore each mapped `peer` to `path`, verify its recorded size and SHA-256, retain
the original metadata, and remove the retirement marker only after complete recovery.
Prior retirement receipts protect their donors from subsequent retirement. Neither
age-based deletion, current alias changes nor directory-level deletion is supported.
