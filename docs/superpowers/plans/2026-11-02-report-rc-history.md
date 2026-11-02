# Broker forecast history implementation plan

**Goal**: Retrieve 2011-01-01 through 2026-11-01 broker forecasts into an isolated, resumable snapshot without replacing published assets.

**Architecture**: A standalone owner script uses the existing credential loader and TuShare request policy. Calendar-month partitions have bounded pagination, hash-bound receipts, local retrieval timestamps and atomic writes. Existing published datasets remain unchanged.

**Authorization**: The owner approved the proposed historical backfill and factor-research division of responsibilities in this conversation. Implementation choices are delegated; execute inline.

**Constraints**: No credentials or raw records in Git. Keep provider update time separate from first availability. Zero-row partitions are recorded. Completion requires every requested month. No current/latest pointer changes or production scheduling.

**Review focus**: ignored pagination offsets; incomplete resume receipts; partial failures; out-of-range dates; duplicate pages or rows.

## Tasks

- [ ] Add `scripts/research/backfill_report_rc.py` with `fetch_month(client, policy, start, end, interval)` and hash-verified monthly checkpoints. Write failing tests for pagination, duplicate pages, invalid dates and resume integrity before implementation.
- [ ] Run the 2011–2026 backfill using the existing configuration wrapper. Retain source revision, query, fields, timestamps and checksums externally.
- [ ] Generate annual coverage and revision-delay quality summaries. Run affected tests and repository quality gates, independent review, PR and merge.

The private consumer owns factor construction and return research. Its public projection must contain aggregates only and identify reconstructed historical availability explicitly.
