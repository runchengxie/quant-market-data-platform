# Minute operational evidence and bounded fallback implementation plan

> **For agentic workers:** Execute the coordinated tasks below with test-first changes and an independent final review. Each file has one owner; root serializes Git and deployment.

**Goal:** Preserve first-failure evidence across resumed minute downloads, distinguish current assets from failed jobs, and explicitly disable a known unsuitable fallback without changing credential permissions.

**Architecture:** Keep mutable backfill plans and receipts for resume. Record a separate unique per-invocation journal with safe, hash-bound attempt snapshots. Add a read-only status mode and an explicit opt-out for the existing fallback; no shared retry or quota-policy change.

**Tech stack:** Existing Python, JSON, ContextVar, pytest and installed operational script.

**Spec:** User-authorized follow-ups: verify actual post-deployment receipts; retain failures and prove recovery; assess fallback permissions and quota. Existing production/data boundaries in AGENTS.md apply.

## Constraints and review focus

- No credentials, raw provider response text, raw endpoint URLs or market data in Git/history.
- A successful asset publication is distinct from a successful systemd invocation or a real delivery.
- Preserve existing return payloads, CLI defaults, raw data, resume paths, quota limits and publication checks.
- No full-market downloads or supplier probes just to validate changes.
- Journal failures must not disappear when a mutable receipt is overwritten; concurrent contexts must not mix.
- Read-only status must not initialize history, repair catalogs or invoke providers.
- Reject conflicting fallback flags; keep default and explicit fallback consumers compatible.

## Tasks

- [x] Capture actual service invocation, timer and output receipt evidence; label future runs pending and manual/no-send runs accurately.
- [x] Test then implement `_minute_operational_history.py` and its tests: unique run records, safe attempt snapshots, failures and exceptions, corrupt evidence and context isolation.
- [x] Test then integrate journal into `run_operational_daily`; add read-only `inspect_operational_daily` status that separates current/behind/recovered data and last invocation.
- [x] Test then add `--no-fallback` and `--status-only` to the operational script; retain existing defaults.
- [x] Document history/status/fallback contracts and safe operations in existing owner documentation.
- [x] Run targeted and full owner gates; record exact additive source/test size only if the existing size baseline requires it, without loosening structural limits.
- [ ] Independent review; merge provider PR before updating deployment pin/consumers.
- [ ] Validate immutable production candidate and rollback; install the source owner; explicitly remove only the known unsuitable fallback argument from the existing operational unit after a safe preview. Preserve credentials and old unit for rollback.
- [ ] Verify installed read-only status, run one explicitly bounded no-download/no-send operational check only when the target is already present, and report remaining natural future triggers rather than fabricating completion.
