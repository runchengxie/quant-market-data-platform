# Index candidate overlap for retained links

The full reference scan still exceeds ten minutes after text predicate
optimization. It repeatedly compares each symlink target against every retirement
candidate. Build an in-memory index of lexical candidate path components once per
reference root, and use it to find candidate ancestors and descendants of each
resolved target. This must preserve every keep/review/retire decision and first
reference reason.

Continue resolving each filesystem link afresh and never traverse linked
directories. Do not cache filesystem resolution, skip evidence, weaken scan-error
handling, or change production aliases, quotas, data or schedulers. Preserve
self-reference exclusion and native Windows path case semantics. Validate with
real links, nested candidates, disjoint sibling prefixes and deterministic work
bounds, then run the full quality gate and independent review.
