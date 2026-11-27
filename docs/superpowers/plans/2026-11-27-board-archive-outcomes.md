# Board Archive Outcomes Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for this bounded fix.

**Goal:** Stop false successful archive outcomes and boundedly traverse failures.

**Architecture:** Retain existing provider retries and SQLite writes. Extend
archive outcome/accounting and let the CLI propagate the persisted outcome.

**Tech Stack:** Python, pytest, SQLite, existing public CLI.

**Spec:** ../specs/2026-11-27-board-archive-outcomes-design.md

## Constraints and review focus

Only archive behavior and the CLI archive handler change. Existing successful
requests, date rejection and skip behavior remain. Unknown provider errors are
not evidence of permission to retry. Explicit limits are selected scope, not a
full catalogue coverage guarantee. No production writes or scheduler changes.

## Task 1: Outcome and request traversal

- [ ] Add regressions in `tests/test_board_flow_monitor.py` for false success,
  exact counters, three consecutive failures, reset after success, HTTP causes,
  wrong dates and already archived/limited work. Run to observe intended failure.
- [ ] Extend `ArchiveOptions` and `archive_minutes` in
  `src/quant_market_data_platform/board_flow_monitor.py` with validated failure
  cutoff, explicit scope counts and matching stored/returned status. No retries.
- [ ] Run the new regressions and existing monitor suite to green.

## Task 2: CLI and delivery

- [ ] Add CLI regressions exercising real archive execution with injected
  providers for failed, partial and successful/empty outcomes. Observe red.
- [ ] Change only `_handle_archive` in `cli_board_flow.py` to preserve status
  and return nonzero for failed/partial. Run all board CLI/monitor tests.
- [ ] Update the bilingual board flow failure semantics. Record only exact
  physical Python growth in the existing baseline with reason/retirement.
- [ ] Run the locked full quality entry point, inspect diff and create a PR.
- [ ] Obtain root review, verify the current base and public checks, then merge.
  Remove only this merged task's owned branch/worktree/cache after evidence is
  retained and root no longer needs the development environment.
