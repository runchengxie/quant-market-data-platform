# Board-flow Parquet archive v1

[简体中文](board-flow-archive.zh-CN.md)

The data platform owns this lossless, read-only-consumer artifact. A replay app
reads the archive without connecting to the collector or importing provider code.
Archive validation proves file integrity and recoverability. It does not certify
vendor semantics, complete trading-day coverage or a successful collection run.

## Identity and layout

`manifest.json` is UTF-8 JSON with `format: "board-flow-parquet-v1"`. Unknown
versions and duplicate JSON keys at any nesting level fail closed.
The six fixed tables are:

| Table | File layout | Meaning |
|---|---|---|
| board_catalog | board_catalog/all.parquet | Latest observed names, types and first/last seen times |
| board_catalog_daily | board_catalog_daily/YYYY-MM-DD.parquet | Names and universe actually observed that day |
| flow_snapshot | flow_snapshot/YYYY-MM-DD.parquet | Cumulative flow actually observed at capture timestamps |
| flow_minute | flow_minute/YYYY-MM-DD.parquet | Vendor minute cumulative curves retrieved later |
| collection_run | collection_run/all.parquet | Retained collector outcomes |
| schema_meta | schema_meta/all.parquet | SQLite schema metadata |

Each table needs at least one inventory entry. A non-daily table may have zero
rows in its `all.parquet`. V1 export requires at least one observed partition in
each daily table. Different daily tables can cover different dates. Missing
dates, boards or minutes are missing observations, never implicit zero flow.
The daily catalog is the evidence for that day's observed universe. A later
catalog cannot establish historical coverage or historical names. Collector status
and detail are preserved verbatim. New receipts may report partial collection,
with expected scope, attempted fetches and already-archived rows counted separately.
Legacy receipts lacking those fields remain unknown; an old success label alone
does not prove complete coverage.

All Parquet data files must be listed once. Unlisted Parquet files, duplicate
table/date identities and symlinks inside the archive fail validation. Paths are
derived from the fixed table name and a real ISO date, not executable manifest
instructions. Companion documentation is permitted. Existing legacy v1 archives
remain readable without producer metadata or an added path field.

## Manifest and typed values

The manifest contains `snapshot_started_utc`, the source basename `source_file`,
`tables_sql`, `indexes_sql`, SQLite `user_version` and `application_id`, and
`files`. Each file entry contains `table`, `date` (null for non-daily tables),
ordered `columns`, declared SQLite `types`, ordered primary `key`, `rows`,
`bytes`, and lowercase hexadecimal `sha256`. SQLite metadata values are integers
from zero through 2147483647. New exports add optional `producer` metadata: the owner name, installed package
version when available, actual archive/contract module SHA-256 values, and the
pinned schema identity. No Git revision is guessed. Legacy absence remains
compatible. These are recorded file identities, not an authenticity proof or a
claim that an old archive came from the current code. External reproduction
records bind the immutable release and validation commands.

The schema is exactly the six-table, two-explicit-index schema in
[board_flow_store.py](https://github.com/runchengxie/quant-market-data-platform/blob/b2a40031a42d3b25b5b1ca26e2a3d4ca4a336fff/src/quant_market_data_platform/board_flow_store.py).
Unknown columns, types, primary keys, indexes, tables, views and triggers are
rejected. Manifest SQL is compared to that trusted schema and never executed.
The original SQLite schema text is preserved for inspection. A pinned v1 schema
identity prevents later store or index changes from silently redefining this format.
The contract preserves declared column values, schema and the listed SQLite
metadata; hidden rowids, physical page layout and free space are not archive data.

Actual SQLite values must match their declared types: TEXT is string, INTEGER
is an integer, and REAL is a float or exactly representable integer, with null
allowed where the schema permits it. BLOB-to-text, fractional-to-integer and other
silent Arrow coercions are refused, with a row-value roundtrip check.

SQLite TEXT maps to Arrow string, INTEGER to int64, and REAL to float64. Column
order and values are retained. Existing nullable snapshot values remain null.
Rows are ordered by the complete primary key, which must be non-null and unique.
Every row in a dated file has the matching `trade_date`. Files use Parquet with
Zstandard compression. Consumers validate size, SHA-256, actual Arrow schema,
row count, keys and partition binding before interpreting values.

Dates and timestamps remain strings. `trade_time` is a Shanghai-local wall-clock
minute (`YYYY-MM-DD HH:MM:SS`); use the explicit `Asia/Shanghai` time zone when
displaying it elsewhere. Capture/archive timestamps retain their original text,
including an offset when recorded. Monetary flow fields ending `_yuan` are yuan,
ratio fields ending `_pct` are percentage values. Flow-minute values are
cumulative within the day. A 5/15/30-minute change is a difference between valid
endpoints with a documented session policy, not a sum of cumulative values.

Snapshots and minute curves are distinct observations. A later curve does not
reconstruct what was actually captured intraday. Overlapping concept, industry
and regional boards must not be added up as total-market money flow.

## Publication and restoration

Export reads one SQLite transaction with `mode=ro` and `query_only=ON`, fetching
bounded batches. It writes a new staging directory, checks all Parquet values,
restores into a separate temporary SQLite, compares every typed value and runs
`integrity_check` before atomic no-replace publication. The initial manifest bytes, SHA-256 and file identity stay bound throughout an
operation. Inventory and every listed file size/hash are rechecked before success
or publication. Inputs must remain immutable; this is not a filesystem transaction
locking multiple source files. Verification results include `manifest_sha256`.
Existing output paths are refused, including concurrent creations. Publication requires Linux
`renameat2(RENAME_NOREPLACE)` or Windows native no-replace rename; other hosts
fail closed when the primitive is unavailable.

Restore verifies the complete input and uses trusted local schema definitions.
It only publishes a new database outside the immutable input archive. Existing
destinations, partial failures and corruption never replace a live database.
An ordinary exception cleans only its own staging directory. A killed process
may leave a task-named staging directory for explicit ownership review; retry
creates new staging and does not adopt or delete unknown leftovers. Atomic
namespace publication is not a multi-file power-loss durability guarantee.

This command exports the entire current store, partitioned by date. It does not
deduplicate successive snapshots, schedule incremental publication, prune the
online SQLite database, change indexes, set retention or update a shared alias.
Keep those production decisions separate from validating this artifact.
