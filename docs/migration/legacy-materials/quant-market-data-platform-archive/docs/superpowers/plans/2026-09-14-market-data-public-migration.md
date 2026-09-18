# Market Data Public Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Produce a verified sanitized baseline that can seed a public platform repository, plus an explicit private deploy repository boundary and migration runbook.

**Architecture:** Keep one active business-code source in the public platform repository. Move private deployment state into a separate thin repository, while the original repository becomes an immutable archive after cutover. The current worktree implements and verifies the sanitized baseline; GitHub repository creation and visibility changes are performed only after the baseline PR is reviewed.

**Tech Stack:** Python 3.13, uv, pytest, Ruff, ty, Bash/systemd templates, GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-09-14-market-data-public-three-repository-design.md`

## Global Constraints

- Do not commit Parquet, credentials, caches, reports, logs, or production data.
- Do not read, print, or commit token values.
- Public code must not contain personal host paths, personal proxy domains, or private sibling project names.
- TuShare endpoints remain configurable through environment variables or CLI arguments.
- The private deploy repository must not contain a business-code copy.
- Preserve the known baseline failure in `tests/test_quality_governance.py`; do not claim it as fixed by this task.
- Use a new worktree and branch for each repository change; never force-push or reset shared branches.

### Task 1: Define endpoint and path sanitization behavior

**Files:**
- Modify: `tests/test_tushare_a_share.py`
- Modify: `tests/test_tushare_a_share_constraints.py`
- Modify: `tests/test_archive_tushare_fundamentals_vintage.py`
- Modify: `src/market_data_platform/providers/_env.py`
- Modify: `scripts/operations/archive_tushare_fundamentals_vintage.py`
- Modify: `scripts/operations/market_data_platform_retention.sh`

**Interfaces:**
- `resolve_tushare_api_urls()` returns only configured URLs and no personal defaults.
- Fundamentals archive accepts any explicitly configured valid URL and does not enforce a named private host.
- Retention script resolves its root from `MARKET_DATA_ROOT`, `DATA_PLATFORM_ROOT`, or `${XDG_DATA_HOME:-$HOME/.local/share}/market-data-platform`.

- [ ] Write a failing test proving `TUSHARE_TOKEN_2` with no URL does not return a private proxy URL.
- [ ] Run the focused tests and verify failure identifies the hard-coded default.
- [ ] Write a failing test proving an explicit non-private fundamentals endpoint is accepted.
- [ ] Run that test and verify failure identifies the host allow-list.
- [ ] Write a failing shell test or controlled invocation proving retention has a platform-neutral fallback.
- [ ] Implement the smallest changes that satisfy those behaviors.
- [ ] Run the focused tests and shell syntax checks.
- [ ] Commit with `fix: remove private runtime defaults`.

### Task 2: Sanitize examples, active docs, and service templates

**Files:**
- Modify: `.env.example`
- Modify: `docs/operations/credentials.md`
- Modify: `docs/operations/a-share-tushare.md`
- Modify: `docs/operations/a-share-minutes.md`
- Modify: `docs/a-share-fundamentals.md`
- Modify: `scripts/systemd/README.md`
- Modify: `scripts/systemd/tushare-fundamentals-vintage-archive.service`

**Interfaces:**
- Examples use `https://your-proxy.example.com` or empty values.
- Instructions refer to `$DATA_PLATFORM_ROOT` and generic `@APP_DIR@`/`@ENV_FILE@` placeholders.
- No active public documentation names a private endpoint, personal path, or sibling private repository.

- [ ] Replace personal endpoint/path examples with placeholders and generic environment variables.
- [ ] Remove operational prose that reveals private topology while retaining reproducible public commands.
- [ ] Run the existing documentation/governance tests.
- [ ] Commit with `docs: sanitize public configuration examples`.

### Task 3: Exclude internal archive and planning material from the public snapshot

**Files:**
- Create: `scripts/dev/public_snapshot_excludes.txt`
- Modify: `scripts/dev/audit_public_snapshot.py`

**Interfaces:**
- Public snapshot contains no session handoffs, historical rollout logs, private plans, or migration specs.
- The source private archive keeps these materials for internal audit; snapshot generation excludes them without deleting them from the archive source.

- [ ] Add `docs/archive/` and `docs/superpowers/` to the snapshot exclusion manifest.
- [ ] Make the snapshot auditor reject those directories when run against an export.
- [ ] Update the exported public docs index so it does not link to excluded internal material.
- [ ] Run link/governance tests against the exported tree and confirm only intentional references changed.
- [ ] Commit with `chore: exclude private operational history from public snapshot`.

### Task 4: Add a clean-room snapshot and secret-scan gate

**Files:**
- Create: `scripts/dev/audit_public_snapshot.py`
- Create: `tests/test_public_snapshot_audit.py`
- Modify: `.github/workflows/quality.yml`
- Modify: `README.md`

**Interfaces:**
- `python scripts/dev/audit_public_snapshot.py <root>` exits nonzero and reports forbidden personal paths/domains, credential files, tracked data artifacts, and internal documentation directories.
- The public quality workflow runs this audit without secrets.

- [ ] Write failing tests for forbidden path/domain detection and a clean fixture tree.
- [ ] Run the tests and verify the auditor is missing.
- [ ] Implement the auditor with literal forbidden patterns and a nonzero exit code.
- [ ] Run tests, then add the audit to the public workflow.
- [ ] Document clean-room snapshot generation and the no-secrets CI boundary.
- [ ] Commit with `ci: add public snapshot audit`.

### Task 5: Verify the sanitized baseline

**Files:**
- No source changes unless verification exposes a scoped defect.

- [ ] Run `uv run --locked --extra dev python -m pytest <focused tests>`.
- [ ] Run `uv run --locked --extra dev python -m ruff check .`.
- [ ] Run `uv run --locked --extra dev python -m ruff format --check .`.
- [ ] Run `uv run --locked --extra dev ty check --error-on-warning`.
- [ ] Run the public snapshot audit against the worktree.
- [ ] Run the full isolated pytest suite, recording the pre-existing governance failure separately if unchanged.
- [ ] Commit any scoped fixes and tag the resulting commit as the clean-room source candidate.

### Task 6: Execute the three-repository cutover

**Files / external state:**
- GitHub repository settings for the existing repository.
- New GitHub public repository `quant-market-data-platform`.
- New GitHub private repository `quant-market-data-deploy`.
- Create outside the public snapshot: deploy repository files for production configuration and automation.

- [ ] Record current repository URL, default branch SHA, tags, issues/PR policy, workflows, and production consumers.
- [ ] Rename the current private repository to `quant-market-data-platform-archive` and disable automated production triggers.
- [ ] Export the verified worktree without `.git`, internal docs, credentials, data, or artifacts.
- [ ] Create the public repository and push a new root commit from the export; do not copy old tags, refs, Issues, PRs, or release assets.
- [ ] Create the private deploy repository with only deployment/configuration files and an immutable public release pin.
- [ ] Validate public CI, private dry-run/health/rollback, and production consumer remotes.
- [ ] Archive the old repository only after all validations pass.
- [ ] Record repository URLs, commit SHAs, visibility, archive state, and residual risks in the private migration record.
