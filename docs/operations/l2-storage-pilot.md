# Provider-native L2 storage pilot

[简体中文](l2-storage-pilot.zh-CN.md)

Build an isolated full-field Parquet version of a dated Guan-style CSV archive. This pilot prioritizes a few securities over an intraday interval while retaining streaming full-day reads. It does not replace existing daily/monthly legacy schemas, rescale prices, remove duplicate events, delete inputs, or change current/latest aliases.

Install the existing `offline-equities` extra (`uv sync --locked --extra offline-equities`). Supply a trusted 7-Zip executable separately; its listing and extraction commands must support `l -slt` and `x`. Keep input archives, scratch space and output outside source repositories. Use a distinct owned scratch/output directory for each date and configuration.

```bash
python -m quant_market_data_platform.l2_storage build \
  --archive "$ARCHIVE" --expected-sha256 "$ARCHIVE_SHA256" \
  --output "$OUTPUT" --scratch "$SCRATCH" --extractor "$EXTRACTOR" \
  --buckets 64 --row-group-rows 65536 --zstd-level 6
python -m quant_market_data_platform.l2_storage verify --root "$OUTPUT"
python -m quant_market_data_platform.l2_storage query --root "$OUTPUT" \
  --modality trades --symbol 000001.SZ --start 100000000 --stop 100500000 --count
```

`orders`, `trades` and `snapshots` retain every original column, including unknown fields and security identifiers. Source files are GB18030 CSVs under `YYYYMMDD/<security>/<type>.csv`. By default, each record must agree with its directory security and date. Clocks are the original `HHMMSSmmm` values; intervals include start and exclude stop. No time zone conversion or price-unit inference is performed. Empty strings remain empty strings. Integer storage is selected only after every value in the entire modality round-trips exactly to its original text.

Files are routed by CRC32 of the UTF-8 security identifier modulo the configured bucket count. Row groups do not cross CSV member boundaries. Within a member, records are ordered by source clock and original row ordinal; Group by `_source_symbol` and order by `_source_row` to restore each original CSV member. All source IDs, duplicate records and original fields remain present. `_time` is an additional query key, not a replacement for the original clock field.

The v2 physical schema adds `_source_symbol`, the untouched directory identifier. If an audited source uses conflicting exchange suffixes in directories and records, opt in with `--identity-policy preserve-source-labels`. This permits a set of valid record identifiers only when every six-digit prefix matches the directory identifier. Different suffixes may coexist within one member. Mixed numeric prefixes, invalid identifiers and date/clock errors still stop the build. Neither identifier is rewritten, and matching numeric prefixes do not establish economic equivalence or a canonical exchange.

Symbol queries use the original record identifier from `万得代码`. The profile routes such a query through every matching source bucket; duplicate members remain distinct. Inspect `identity_summary`, per-member `record_symbols`, `record_symbol_counts` and `identity_status` before downstream use or source retirement. Conflicting labels can differ across modalities. Existing v1 data remains readable using its fixed v1 release; v2 does not silently rewrite that data.

The manifest is published only after every source member passes all-column logical equality and row-count checks. It binds the archive hash, global modality profiles, storage configuration, output hashes and routing. Full re-verification reads every output file and rechecks every member. These proofs establish lexical field equality, not byte-identical CSV quoting, encoding or line endings. They do not prove provider completeness or correct economic units.

Builds use advisory locks and reject unknown outputs or changed source/configuration. Resume recomputes the complete extracted-source profile and validates every frozen CSV hash; cached member lists cannot replace source inventory. Auxiliary outputs use exclusive temporary files and do not follow symlinks. Verified bucket files can be recovered after interruption by rechecking their member proofs. An incomplete extraction remains preserved and requires a new owned scratch directory. Guards require a 20 GiB disk reserve, extraction size from the archive inventory, and conservative output space. A CSV exceeding 512 MiB raw or 1 GiB decoded is rejected; this bounded pilot requires an extended streaming implementation for larger individual members. Do not lower these guards to fit an unsuitable disk or validation environment.

The Python scanner is streaming:

```python
from pathlib import Path
from quant_market_data_platform.l2_storage.reader import scan

for batch in scan(Path(output), "trades", ["000001.SZ"], 100000000, 100500000).to_batches():
    consume(batch)
# Omit securities and interval bounds to stream all records of a modality.
```

Existing research/event-stream packs may omit original IDs and are not replacements for this archive. Legacy readers need an explicit adapter before any production path switches. Retain original archives until complete-day data, query compatibility, source dependencies and storage capacity are accepted. Any source retirement is a separate, evidence-backed operation; this CLI has no delete command. Existing snapshot corruption notices and repair dependencies remain outside automatic migration.
