# ST Event Availability Implementation Plan

**Goal:** Publish effective ST status and conservative decision-time availability as separate fields, then make active consumers fail closed when ST availability is unknown.

**Architecture:** The provider computes availability from `ann_date` and its supplied exchange calendar, stores `available_from` in reconstructed ST history, and propagates it as `st_available_from` through `daily_clean`. Consumer repositories adopt the field only after the provider contract is merged. A staged candidate is compared with current artifacts; no production alias or pointer changes.

**Tech Stack:** Python, pandas, Parquet, YAML manifests and receipts, pytest, Ruff, ty.

**Spec:** `docs/superpowers/specs/2026-10-01-st-event-availability-design.md`

## Global Constraints

- Preserve `interval_start`, `interval_end`, `ann_date`, and `is_st` effective-state semantics.
- For same-day or later announcements with no intraday timestamp, set availability to the first exchange session strictly after `ann_date`.
- Use the supplied exchange calendar; do not add calendar days to find the next session.
- Unknown `ann_date` yields unknown availability and consumers fail closed for ST eligibility.
- Keep `revision_safe=false` unless the full supported history is evidenced as point-in-time safe.
- Do not use TuShare endpoints requiring additional permissions or unapproved quota.
- Do not write a production alias, change a deployment pin, or move a production pointer.
- Complete and merge provider PR before creating consumer PRs.

## Review Focus

- An `ann_date` on a weekend or exchange holiday must advance to the next open session.
- A missing or malformed `ann_date` must yield null availability rather than an inferred date.
- An announcement later than the effective interval start must not backdate availability.
- Existing `is_st` rows and effective intervals must remain byte-for-byte equivalent in logical values after candidate rebuild.
- A halted interval with no `daily_clean` rows must remain represented in the reconstructed history and timing audit.

---

### Task 1: Add availability to reconstructed ST history

**Files:**
- Modify: `src/quant_market_data_platform/providers/tushare_a_share_constraints.py`
- Test: `tests/test_tushare_a_share_constraints.py`
- Test: `tests/test_tushare_a_share_constraint_st.py`
- Modify: `docs/contracts.en.md`
- Modify: `docs/contracts.md`

**Interfaces:**
- Consumes: `ReconstructedSTOptions`, existing calendar frame, and reconstructed interval rows.
- Produces: reconstructed ST-history rows containing `available_from` (`YYYYMMDD` or null) and receipt schema `market-data-platform.reconstructed-st-history.v2`.

- [ ] Add a failing test for an announcement dated on the effective start; assert `available_from` is the following session.
- [ ] Run the focused test and confirm it fails because `available_from` is absent.
- [ ] Add a failing test covering earlier announcement, later announcement, missing date, and a holiday/weekend using the supplied calendar.
- [ ] Implement availability calculation from `ann_date`, `interval_start`, and calendar sessions; keep effective interval fields unchanged.
- [ ] Bump the reconstructed-history receipt schema to `market-data-platform.reconstructed-st-history.v2` and assert the field/schema in receipt tests.
- [ ] Run both focused ST provider test modules and confirm the new cases pass.
- [ ] Update English and Chinese contract docs with the new field semantics and examples.
- [ ] Run `uv run --extra dev python -m ruff check src/quant_market_data_platform/providers/tushare_a_share_constraints.py tests/test_tushare_a_share_constraints.py tests/test_tushare_a_share_constraint_st.py` and `uv run --extra dev ty check --error-on-warning`.
- [ ] Commit the provider implementation on its isolated worktree branch.

### Task 2: Propagate availability through daily_clean and quality receipts

**Files:**
- Modify: `src/quant_market_data_platform/standardize/tushare/a_share_daily_part02.py`
- Modify: `src/quant_market_data_platform/standardize/tushare/a_share_daily_part01.py`
- Modify: `src/quant_market_data_platform/providers/tushare_a_share_quality_part02.py`
- Test: `tests/test_tushare_a_share_clean.py`
- Test: `tests/test_tushare_a_share_clean.py` (manifest schema and field validation)
- Modify: `docs/operations/a-share-tushare.en.md`
- Modify: `docs/operations/a-share-tushare.md`

**Interfaces:**
- Consumes: Task 1 `available_from` values keyed by `(trade_date, ts_code)`.
- Produces: `daily_clean.st_available_from`, null outside active positive ST rows, with manifest schema `tushare.a_share.daily_clean.v2` and receipt lineage.

- [ ] Add a failing daily-clean test asserting `is_st` is preserved and the matching `st_available_from` is propagated.
- [ ] Add a failing quality test for absent/malformed availability on a positive ST row and null availability on a non-ST row.
- [ ] Run both focused daily-clean tests and confirm the new contract assertions fail.
- [ ] Propagate `available_from` into `st_available_from` without changing `is_st` values.
- [ ] Bump `_build_daily_clean_manifest` to `tushare.a_share.daily_clean.v2`; include field lineage in its manifest/receipt and validate it in quality checks.
- [ ] Run the focused provider, clean, and quality tests; run Ruff and ty for changed modules.
- [ ] Build a candidate in a non-production staging directory from existing local inputs and compare hashes, date coverage, `is_st`, availability, and audit statuses.
- [ ] Commit and open the provider PR; do not start consumer PRs until it is merged.

### Task 3: Update quant-market-research consumers

**Files:**
- Modify: `src/market_research/markets/a_share.py`
- Modify: `src/market_research/microcap_execution.py`
- Modify: `src/market_research/style_portfolios/cross_section.py`
- Test: `tests/markets/test_a_share.py`
- Test: `tests/test_microcap_execution.py`
- Test: `tests/test_style_portfolios.py`
- Modify: `docs/data-storage-and-publication.md`

**Interfaces:**
- Consumes: merged provider field `daily_clean.st_available_from`.
- Produces: PIT eligibility that rejects positive ST rows when availability is missing or later than the decision date.

- [ ] Add failing consumer contract tests for same-day ST unknown-before-availability, known-on/after-availability, and missing availability.
- [ ] Run the focused tests and confirm they fail against the existing consumer contract.
- [ ] Require and validate `st_available_from` for PIT-eligible inputs; fail closed for unknown ST state.
- [ ] Preserve non-PIT exploratory paths only where their metadata cannot claim PIT eligibility.
- [ ] Run the consumer package's required pytest, Ruff, and type-check gates from its `AGENTS.md`.
- [ ] Open and merge a separate consumer PR after the provider PR is merged.

### Task 4: Update quant-platform style-replica universe consumer

**Files:**
- Modify: `packages/alpha/src/alpha_research/style_replica/universe.py`
- Test: `tests/alpha/test_style_replica_universe.py`
- Modify: `docs/alpha/concepts/style-replica.md`

**Interfaces:**
- Consumes: merged provider `st_available_from` on dated instrument rows.
- Produces: style-replica eligibility that excludes unknown or not-yet-available ST state.

- [ ] Add failing tests for missing availability, future availability, and availability on the decision date.
- [ ] Run the focused universe tests and confirm the contract currently accepts rows that should fail closed.
- [ ] Require `st_available_from` for PIT status checks and enforce the decision-date comparison.
- [ ] Run the platform's required pytest, Ruff, and type-check gates from its `AGENTS.md`.
- [ ] Open and merge a separate consumer PR after the provider PR is merged.

### Task 5: Audit private quant-research call paths and adopt only active PIT consumers

**Files:**
- Inspect: all active call sites that form eligibility from `daily_clean.is_st` in `src/`.
- Modify and test only active production/research entry points that consume the published provider artifact directly.

**Interfaces:**
- Consumes: merged provider `daily_clean.st_available_from`.
- Produces: no PIT-eligible result from a positive ST row when availability is unknown or later than the decision date.

- [ ] Trace each `is_st` use to its input artifact and decision clock; record paths that consume `daily_clean` versus derived fixtures or independent inputs.
- [ ] Add a failing regression test for each direct active `daily_clean` PIT consumer identified by the trace.
- [ ] Update only those consumers, preserving unrelated daily-report worktree changes.
- [ ] Run the repository's local-first test, Ruff, and type-check gates from its `AGENTS.md`; do not enable GitHub Actions.
- [ ] Open and merge a separate private consumer PR after the provider PR is merged.

### Task 6: Close the 600530.SH evidence loop without changing production

**Files:**
- Modify: provider timing-audit code/tests only if a focused test proves the two source observations are collapsed or mislabeled.
- Update: issue #41 evidence summary after the candidate validation.

**Interfaces:**
- Consumes: company notice evidence, TuShare `st` rows, reconstructed name history, and the validated exchange calendar.
- Produces: an auditable distinction between 2020-04-30 effectiveness and 2020-05-06 short-name correction; no inferred exact intraday publication time.

- [ ] Add or update a regression test preserving both event observations and expected effective/availability dates.
- [ ] Rebuild and inspect the 600530.SH candidate and timing-audit rows, including the halt interval.
- [ ] Update issue #41 only with verified source facts, the conservative availability rule, and explicit residual limitations.
- [ ] Leave production data and pointers unchanged.

## Final Verification

- Run all provider quality gates from `quant-market-data-platform/AGENTS.md` after focused tests pass.
- Run each consumer repository's mandatory local checks after its provider dependency has merged.
- Compare staged candidate output against existing input hashes and row-level `is_st` values.
- Confirm no production path, release pointer, or deployment lock changed.
- Record PR URLs, merge SHAs, actual check results, candidate receipt paths, and unresolved source limitations.
