# Minute Recovery Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make minute campaign retries bounded and make quarantine restores auditable and recoverable.

**Architecture:** Expose the existing request policy timeout through the campaign CLI and manifest policy, then add a standalone restore operation that validates a quarantined partition before changing active files, writes a backup, rebuilds its sidecar, and appends a ledger event. Both features remain compatible with existing campaign layouts.

**Tech Stack:** Python, pytest, Parquet metadata via existing project helpers, JSON ledger/sidecar files.

## Global Constraints

- Restore operations default to dry-run and never overwrite an existing active partition without an explicit flag.
- Do not read or commit credentials or production data.
- Follow the repository's existing manifest, sidecar, ledger, and receipt schemas.

### Task 1: Expose bounded request timeout

**Files:**
- Modify: `scripts/operations/tushare_minute_replacement_campaign.py`
- Modify: the existing planner/runner module that constructs `TushareRequestPolicy`
- Test: existing minute campaign CLI/runner tests

- [ ] Add a CLI option `--request-timeout-seconds` and pass it into the request policy.
- [ ] Accept the value from the manifest policy when the CLI option is omitted.
- [ ] Reject non-positive values and preserve current behavior when unset.
- [ ] Add parser and propagation tests.
- [ ] Run focused campaign tests.

### Task 2: Add quarantine partition restore operation

**Files:**
- Create: `scripts/operations/restore_minute_partition_from_quarantine.py`
- Create: focused tests under `tests/`
- Modify: `docs/operations.md`

- [ ] Implement dry-run inspection of active, quarantine, sidecar, manifest, and ledger paths.
- [ ] Validate Parquet row count, expected symbol set, and 241 bars per symbol using existing helpers.
- [ ] Require `--apply` for changes, create a timestamped backup, atomically promote the validated file, rebuild sidecar, and append an auditable ledger event containing SHA-256.
- [ ] Refuse regression when active data is complete unless `--allow-regression` is explicitly supplied.
- [ ] Add tests for dry-run, successful apply, invalid symbol set, and regression refusal.
- [ ] Document the command and recovery guarantees.

### Task 3: Verify and hand off

- [ ] Run focused tests, ruff, format check, and type check for changed files.
- [ ] Commit, push branch, create PR, and merge after checks pass.
- [ ] Deploy only after production release verification, then re-run campaign status checks.

