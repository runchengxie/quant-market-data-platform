# L2 source-label recovery

The complete-day pilot stopped on a directory/record security suffix conflict. A source inventory audit found recurring suffix conflicts with matching numeric prefixes. This is intake evidence only; full-file checks remain mandatory. Real audit counts and records stay outside Git.

## Contract and implementation

- Preserve strict identity validation by default. An explicit `preserve-source-labels` policy accepts valid record-identifier sets with the same six-digit prefix and potentially conflicting exchange suffixes. It does not assert that labels denote the same economic instrument.
- Retain original provider columns unchanged and store the directory identifier separately as `_source_symbol`. Keep member fingerprints and row ordinals. Reject mixed numeric prefixes and invalid or unrelated record identifiers, and retain date/clock checks.
- Version the physical schema as `l2.storage.pilot.v2`. Route queries by actual record identifiers through all matching source buckets, rather than changing either label. Verify each source member through `_source_symbol`, retaining distinct copies when labels collide.
- Bind the selected policy, per-member record-identifier sets/counts and conflict summary into the profile, job and manifest. Independent verification must reject suppressed or inconsistent identity evidence.
- Add failing regressions first, then run focused/full checks and a fresh review. Record exact code-count budget changes without relaxing structural/security thresholds. Merge through the user's PR workflow.
- Create an immutable merged release. Preserve the failed run plan/log/state as evidence, reuse the verified 45 GB first-day extraction, and resume the same three-date/four-layout trial in a separate v2 output namespace. Keep original archives and all legacy/current entry points intact.

## Acceptance

Both original identifier text and provenance must round-trip. A raw record-ID query must return all matching members even when their directory identifiers route to different buckets, without merging duplicates. Conflict evidence remains visible and canonical exchange mapping remains unresolved. Retirement still requires separate whole-day, layout and dependency acceptance.
