# Daily broker forecast observation plan

**Goal**: Accumulate immutable observation-date forecast snapshots for future research using the existing configured provider.

**Authorization**: The user accepted the proposed next work and delegated implementation details. Execute inline; use isolated worktrees and merge the public provider before deployment.

**Architecture**: A small observation command wraps the tested monthly history reader. It captures a rolling 270-calendar-day query into a unique timestamped observation, seals hashes and actual retrieval times, and never publishes or modifies historical aliases. The private deployment repository owns its narrow immutable release pin and daily systemd units.

**Decisions**: Run daily at 10:00 Asia/Shanghai, after the documented morning provider refresh. Each invocation creates a new observation including retries on the same day. Missed observation days remain missing. A 270-day window covers the current 180-day freshness and earlier 63-day comparison without repeating the entire 15-year history. Keep the existing global production release and services unchanged.

**Review focus**: backdating, incomplete or mixed attempts, inner symlinks, concurrent writers, reuse of source acquisition provenance, and ambiguous provider-versus-local timestamps.

- [ ] Add failing tests for truthful UTC/local dates, unique attempts, partial runs, dry-run and hash verification.
- [ ] Implement `providers/report_rc_observation.py` using existing `report_rc_history.backfill` and request configuration.
- [ ] Run full locked public quality checks and independent review. Merge the public provider.
- [ ] Deploy from an isolated stable noneditable release via the private deploy owner, capture one authorized observation, verify integrity, and enable the new timer after dry-run and rollback checks.

Raw vendor records remain private and owned by the data platform. Observation completion establishes local observation availability, never original historical first availability.
