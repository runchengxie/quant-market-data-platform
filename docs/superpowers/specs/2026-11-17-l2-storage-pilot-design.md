# Complete-day L2 storage pilot

The approved exploration direction prioritizes a few securities over intraday windows while retaining complete-day scans. This pilot converts provider-native compressed CSV archives into isolated full-field Parquet assets; it does not silently replace existing daily/monthly legacy readers.

## Approaches

- Retaining CSV is simple but costly to scan and expands the archives substantially.
- One file per security/day/type makes lookup straightforward but multiplies small files.
- Chosen pilot: date/type/security-bucket files, per-security time ordering and row groups, full original fields, and original CSV row ordinals. Compare bucket counts and row-group configurations on complete days before choosing a global default.

## Contract

All source members must be accounted for. Reject traversal, links, encryption and unexpected archive members before extraction. Freeze archive SHA-256 and extracted CSV manifests. Profile every file: identical headers per modality, exact symbol/date identities, supported source clock, row counts, raw file hash and logical all-field hash. Empty strings remain strings. Integer conversion is permitted only when all values in the entire modality round-trip exactly to their original text; security identifiers remain text. Unknown columns are retained. Price and clock units are not rescaled.

Use ZSTD6 with delta encodings and 65,536-row groups initially. Route securities using a versioned CRC32/UTF-8 mapping and a configurable bucket count (initial complete-day candidate: 64). Keep `_source_row` and `_time` as provenance/query fields. Verify each member by restoring original row order and string schema, comparing all-column logical hashes and row counts. Store output file hashes and a query catalog. Both security/time queries and complete-day scans must be supported, with an explicit raw-provider schema boundary.

Atomic publication is a new day directory with a verified manifest; partial work stays isolated and recoverable. Refuse unknown existing outputs, changed source/configuration and corrupted checkpoints. Require explicit input/output/scratch paths outside source trees, per-task locks, free-space and per-file memory guards. Existing repair dependencies and legacy originals are untouched. Runtime uses a stable immutable code release, not a temporary worktree.

## Acceptance and retirement

Implement and test the tool, then run complete representative dates including all modalities/instruments. Measure complete-day size and routing/query/full-scan behavior. No source archive deletion is authorized by a mere successful copy or file hash: original members and values, query compatibility, dependencies and whole-day acceptance must pass first. The user has authorized corresponding incoming archive retirement after those conditions, while preserving audit evidence and the separate mobile copy. The pilot itself does not automatically delete source archives or switch current/latest aliases. Existing research event packs are not full-field archival replacements.
