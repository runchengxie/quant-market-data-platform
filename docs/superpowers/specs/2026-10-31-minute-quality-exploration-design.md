# Minute quality and earlier-history exploration

The authorized exploration has four deliverables: a scoped research-reader
guard, usable SH/SZ research contracts, a bounded earlier-history acquisition
campaign, and separate Beijing exchange and predecessor-history evidence.

## Approach

Reuse published-asset contracts, the existing minute executor and the shared
quota ledger. Add a guarded reader contract and a configurable reverse-history
lower bound. This retains existing callers and avoids a second ingestion engine.
Keep diagnostics, retrospective coverage, native semantics and publication as
separate decisions. A 240-bar aggregation remains a research experiment.

## Reader boundary

An explicit contract may declare a hash-bound `minute_quality` registry.
The reader fails on intersecting known stock-day issues or unacknowledged
missing sessions. Explicit stock-day exclusion removes complete stock-days and
records the decision. Other dates and securities remain usable. Unguarded
contracts retain their existing behavior; adoption by other builders is separate.

## Acquisition boundary

`history_start_date` is optional. Without configuration the lower bound remains
`20160101`. An explicit valid date is persisted under scheduler metadata and
used by monitoring. Dry runs do not change the persisted bound. Resumed plans
must respect the lower bound. Existing writer locks, semantic blocking, quota
accounting, staging and publication boundaries remain active.

The host first runs one pre-2016 session, then uses small bounded night batches
only after scoped acceptance. Daily operational quota is retained. Production
uses a merged immutable release, with activation and rollback evidence.

## Historical scope

SH/SZ candidates require bounded consumer verification and explicit market
scope. Beijing prelaunch observations remain predecessor-history evidence;
they are not silently discarded. Historical request identifiers are checked
against official mappings before availability conclusions.
