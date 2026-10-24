# Lean fundamentals observations

## Intent

Reduce ongoing storage and provider work while preserving timestamped raw observations. The owner requested implementation of the simplification discussed in the session. Existing vintages and published assets stay intact.

## Approach

Use the existing archive entry point with an explicit raw-only mode. Scheduled collection runs weekly, captures all four raw datasets and seals their manifests and content receipts, then exits successfully without normalization or PIT. The existing full pipeline remains available explicitly and keeps its semantics. Observation cadence is recorded as weekly; gaps between observations remain visible and existing freshness limits are not widened.

A raw observation has a distinct schema and completion marker, and is never accepted as a complete PIT vintage. Put new raw observations in a separate fundamentals_observations namespace so existing vintage verification, seals and retries remain unchanged. All raw children must be complete and pass their content integrity checks before observation completion. Reentry verifies all bindings before skipping provider calls. Derivations can use captured raw inputs through the existing explicit build commands and write separate output directories.

## Alternatives

Continuing daily full builds preserves current cadence but repeats downloads and derivations. A content object store would additionally share identical payloads but requires new write, retry, recovery and reference lifecycle contracts. Weekly raw observation is the smallest operational change with substantial growth reduction; content sharing is deferred until needed. Daily recent-window observations may be added later if research requires finer cadence, with explicit coverage boundaries.

## Boundaries and validation

Do not delete or relink old data, rewrite seals, change current/latest pointers, bypass template conflict checks, claim historical PIT certification or send supplier messages. No full real provider acquisition is required for validation. Tests exercise raw completion, partial capture, tampering, reentry, mode isolation and the unchanged full path. Required repository gates and exact-head public CI precede deployment. Deployment pins the merged public revision and updates the installed weekly raw-only service/timer without starting collection.

## Coordination

A separate workspace audit has already inventoried raw duplicates and broad storage retirement. This change owns future fundamentals scheduling and capture behavior only, and reuses that audit instead of repeating its scan. Previously reviewed byte duplicates are potential savings, not storage already reclaimed.
