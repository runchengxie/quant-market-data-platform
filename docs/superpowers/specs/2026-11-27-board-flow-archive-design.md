# Board-flow archive owner

Preserve the existing `board-flow-parquet-v1` artifact as a data-platform-owned
contract. The replay application consumes artifacts independently of collector
code, credentials and the live SQLite database. Collection and deployment stay
with their existing owners.

## Approach

Retaining an unversioned operational script would leave validation and recovery
without a maintained owner. Moving collection into the replay application would
duplicate authoritative data. Add maintained archive export, verification and
new-target restoration to the existing data owner instead.

The archive keeps all six current tables, both explicit indexes, SQLite metadata,
exact typed values and all observed date partitions. No source values are
modified and missing dates remain absent. Snapshot observations and archived
cumulative minute curves remain distinct datasets.

An export uses a read-only SQLite transaction and bounded batches. It verifies
Parquet, restores a temporary SQLite database and compares every value before
atomic no-replace publication. Restoration validates the fixed schema and all
files, uses trusted schema definitions instead of manifest SQL, and publishes
only a new database outside the immutable input archive. Unknown artifacts,
symlinks, partitions, keys, schemas or digests fail closed.

Existing v1 archives without producer metadata remain readable. No manifest is
rewritten for adoption. Exports are whole-store snapshots, not incremental daily
publication; no scheduler, cleanup, index change, retention rule or current alias
is added. Those remain separate production decisions.

The root agent approved this contract and the three CLI names before development.
