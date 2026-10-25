# Minute candidate resolution implementation plan

**Goal:** Authenticate retained legacy canary evidence and three candidates with two matching complete partitions plus one exact strict subset.
**Architecture:** Add explicit resolution kinds without changing existing gates. All candidate paths/sidecars and prior audits remain bound; final independent references, native semantics, quarantine and consumer replay remain mandatory.
**Spec:** Owner-authorized offline resolution of the two documented historical evidence gaps; no source deletion, data edits or publication.

- [x] Add failing behavior tests for three-candidate exact subset, disagreement, duplicate keys, sidecars, reference failures and consumer tampering.
- [x] Implement bounded exact comparisons; require precisely one complete corroborating candidate and one strict native subset.
- [x] Add failing legacy canary tests for real schema, raw/source/target/date/hash/status/response/cached-evidence corruption.
- [x] Authenticate old canary acquisition and validation fields; normalize raw native 241-row single-symbol response and prove exact disjoint union.
- [x] Document explicit manifest inputs and scope. All repository gates pass (23 isolated batches, 1,926 passed, 1 skipped). Real preflight passes both dates.
- [ ] Commit, then issue and replay real offline receipts only if all formal conditions pass.
- [ ] Verify owner destination and remote state, push PR for root review; retain worktree until merged.

Review focus: no trusted passed flags alone; no hidden fourth candidate; dtype/value/key drift; no reference exemptions; immutable original evidence and fresh auditor identity.

The shared CI ratchet requires the exact new one-file/423-line allowance. Only file and line budgets change, with the retirement condition recorded in the baseline. All structural budgets remain fixed.
