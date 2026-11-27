# Board-flow Archive Implementation Plan

**Goal:** Own lossless v1 archive export, validation and recovery in the data platform.

**Architecture:** A fixed contract validator checks artifact identity and typed
batches. A separate archive module reads SQLite, restores temporary databases and
publishes new targets. Existing board-flow CLI delegates to those operations.

**Tech Stack:** Python, SQLite, PyArrow/Parquet Zstandard, existing CLI and pytest.

**Spec:** ../specs/2026-11-27-board-flow-archive-design.md

## Constraints

- Keep `board-flow-parquet-v1` and the existing six-table typed layout compatible.
- Never write the source database or input archive, execute manifest SQL, replace
  a destination, delete source observations or modify production scheduling.
- Verify missing/extra identities, schemas, hashes, rows, keys and partition dates.
- Use task-specific environment and external temporary storage for validation.

## Work

- [x] Record a failing test for lossless roundtrip and rejected corrupt manifests.
- [x] Implement contract checks and bounded-batch export/verify/new-target restore.
- [x] Add interruption, publication-race, symlink, duplicate-key and immutable-input tests.
- [x] Add archive-export, archive-verify and archive-restore CLI behavior tests.
- [x] Document the producer contract and consumer/production boundary in both languages.
- [x] Verify the retained legacy v1 artifact without modifying it.
- [x] Integrate the earlier collection-failure fix and review the combined diff.
- [ ] Run the full existing quality gate against the current target branch.
- [ ] Complete normal public PR checks and merge after root review.
- [ ] Retain source/lock/results, then clean only this merged task's disposable resources.
