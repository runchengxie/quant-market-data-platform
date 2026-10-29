# Capture related publication metadata

[中文说明](published-asset-snapshot.md)

Use this when a research task needs several current assets with explicit pinned identities:

```bash
marketdata contract capture --artifacts-root /external/data-owner \
  --asset daily_clean --asset trade_cal > /external/task/input-snapshot.json
```

`--current-contract /external/data-owner/metadata/current_assets/a_share_current.json` selects an
explicit contract instead of the default. Asset keys must exist in that contract; the example keys
are illustrative. The command prints JSON only. It does not publish, change aliases, scan data
rows, choose a production data root, or create an implicit result file. Keep redirected output in
the caller's task data directory.

The receipt schema is `marketdata.asset-snapshot.v1`. Each reference reuses existing contract and
manifest digests, canonical manifest fingerprint, lineage, alias and resolved target. Parsing and
exact-byte hashing now use one read, avoiding a metadata/hash mismatch if a file changes between
two reads. Capture checks the loaded contract before/after acquisition, rechecks pinned manifests
and re-resolves required assets to reject observed alias or manifest drift.

Python consumers may call `PublishedAssetContract.capture(keys)`. `require_assets(keys)` retains
its tuple/order interface, including duplicates and empty input, and now uses the same checks.
`PublishedAssetSnapshot.assets` contains the pinned references. `validate_metadata()` rechecks
the captured physical manifests, without following a later current alias. A subsequent legitimate
publication can therefore advance `current` without redirecting a captured research read.

The receipt deliberately states `integrity_scope: manifest_identity`, `data_content_verified: false`
and `atomic_transaction: false`. The existing `content_fingerprint` remains the canonical manifest
hash, not a hash of Parquet contents. This optimistic reader does not lock writers, prevent in-place
mutation, or exclude an adversarial change-and-restore race. It is not a filesystem snapshot or a
six-hour immutability guarantee. A research consumer must separately pin and verify the actual bytes
it reads, or consume a genuinely immutable owner publication. A file-body change that leaves a
manifest unchanged cannot be detected by this metadata receipt.

The regression suite mutates aliases, contract bytes and manifest bytes during capture, tests
same-read parsing/hash identity, and checks pinned references after current aliases advance.
Public fixtures are synthetic; no production data or current pointers are modified.
