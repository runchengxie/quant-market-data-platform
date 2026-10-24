# Lean fundamentals archive implementation

Design: ../specs/2026-10-24-lean-fundamentals-archive-design.md

1. Add failing tests for explicit raw-only mode: four captured children, no derived calls, separate observation root/marker, complete integrity-bound receipt, reentry without provider, partial download failure and child/receipt tampering rejection. Preserve full-mode tests and legacy full retry identity.
2. Implement the smallest raw-only orchestration using existing download, child integrity and attempt helpers. Do not promote a raw capture into a full/PIT vintage. Bind mode, requested scope and cadence before writes or sealed reentry. Add default-full CLI flag and mode-aware verify-only.
3. Change public scheduled service to explicit weekly raw-only capture and timer to a weekly Saturday 02:30 Shanghai calendar. Update cadence tests and EN/ZH operation/contract documentation, with derivation examples verified against CLI parsers. Describe future cadence and growth estimates, not reclaimed disk space.
4. Run focused tests red/green and required full gates against the final source. Record source tree and public exact-head CI, then merge the task PR. Independently review mode separation, retry identity, partial completion and path safety.
5. In a separate private deployment task, pin the merged provider revision, prepare immutable production runtime, run private tests, dry-run and rollback preview. Deploy code and update only the archive service/timer, keeping private endpoints and credentials unchanged. Verify weekly next trigger and raw-only arguments without starting acquisition. Preserve installed-unit backups and rollback evidence outside Git.
6. Record operational outcome, unchanged data metadata, and coordination boundaries with the independent storage audit. Clean only this task's worktrees and branches after confirmed merges.

Review focus: raw receipt cannot certify PIT; mismatched replay parameters fail before provider access; complete raw objects are verified before success; original full seals remain supported; timer update does not inadvertently trigger immediate collection.
