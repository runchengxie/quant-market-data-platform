# Unified JSON Configuration and QuantZone Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use one active private JSON configuration for existing TuShare jobs and acquire bounded, validated QuantZone factor artifacts through the data owner.

**Architecture:** A standard-library configuration loader feeds direct provider commands and an argv-based process launcher. The optional QuantZone adapter writes immutable research runs. Consumer deployments adopt the merged owner CLI before the local configuration source changes.

**Tech Stack:** Python 3.11+, argparse, JSON, pytest, optional QuantZone 0.10.0, pandas, PyArrow, Bash, systemd, and immutable Git release pins.

**Spec:** [Accepted design](../specs/2026-10-02-quantzone-json-config-design.md).

## Global Constraints

- One active private `config.json`, mode `0600`; credential-only directories mode `0700`.
- `DATA_PLATFORM_CONFIG` explicitly selects JSON; portable fallback uses `${XDG_CONFIG_HOME:-$HOME/.config}/quant-market-data-platform/config.json`.
- Preserve all existing environment setting names and values. Existing process variables, including empty values, take precedence. Null JSON entries are unconfigured.
- Selected JSON fails closed and never merges credentials from env files. Secrets are opaque and never serialized in diagnostics or receipts.
- Public examples contain null credentials and portable paths. Machine details, actual credentials, data, receipts, and logs stay outside Git.
- QuantZone SDK `0.10.0` is optional. Supported installed-runtime probes use Python 3.11–3.13; standard gates do not install the SDK.
- Factor requests contain at most 100 stocks and 20 factors, span at most 365 days, and use seven-calendar-day batches by default. Timeout is in `(0, 60]`. Initial retries: one attempt.
- Keep `pit_availability=unknown`, `revision_safety=unknown`, and `public_redistribution=not_authorized`. Preserve nulls; reject duplicate observations and ambiguous symbol mappings.
- Resume verifies query identity and file hashes. Completed runs are immutable; pilot runs never update production aliases or current contracts.
- Merge the provider before consumers. Production uses an immutable release, never a task worktree. Keep a restricted inactive rollback backup and the previous release.
- Run each repository's required checks in its task worktree; preserve other tasks and do not bypass hooks or protection rules.

## Review Focus

1. A selected JSON file is malformed, absent, symlinked, or changed during opening: stop without reading a legacy credential source; Task 1 pins this.
2. A secret contains dollar signs, quotes, whitespace, or newlines: preserve its exact value and omit it from representations and error output; Tasks 1–2 pin this.
3. A scheduler invokes a shell script that also loads credentials: JSON injection happens once and no env file or JSON is sourced by the script; Task 5 pins this.
4. An interrupted batch left partial files, or completed data was tampered with: preserve recovery state, verify hashes, and never silently repeat completed queries; Task 4 pins this.
5. A consumer release or running job still uses old credentials: do not switch the active source until release/startup checks pass; Task 6 pins this with a no-network rollback exercise.

---

## File structure and release units

The current isolated branch and Draft PR carry the data-owner changes. Each
consumer receives its own branch, worktree, and PR after the owner contract is
implemented. Tasks 1–4 form the owner release; Task 5 forms consumer releases;
Task 6 applies the already-authorized local migration and bounded query.

### Task 1: Private JSON loader and example

**Files:** Create `src/quant_market_data_platform/configuration.py`, `config/config.example.json`, and `tests/test_configuration.py`. Update `tests/conftest.py` to isolate `DATA_PLATFORM_CONFIG` during tests.

**Interfaces:**
- `PlatformConfig`: frozen dataclass with `path: Path`, `environment: Mapping[str, str | None]`, and `downloads: Mapping[str, object]`; omit secret-bearing fields from repr.
- `ConfigurationError(ValueError)`: sanitized validation failures.
- `resolve_config_path(environment: Mapping[str, str]) -> Path | None` selects the explicit file or an existing portable default without silently ignoring an explicit missing file.
- `load_config(path: Path) -> PlatformConfig` validates file metadata, duplicate keys, schema version 1, and field types.
- `resolve_environment(config: PlatformConfig, inherited: Mapping[str, str]) -> dict[str, str]` resolves declared path fields and fills only absent variables; records the selected `DATA_PLATFORM_CONFIG` for children.

- [ ] **Step 1: Add failing configuration tests.** Include this contract plus cases for invalid versions/types/keys, duplicate JSON keys, POSIX permissions and ownership, symlinks, opaque secrets, invalid path substitutions, and null credentials:

  ```python
  result = resolve_environment(config, {"TUSHARE_TOKEN": ""})
  assert result["TUSHARE_TOKEN"] == ""
  assert result["QUANTZONE_SIGN_SECRET"] == "dollar${HOME}\nquoted'value"
  assert "dollar" not in repr(config)
  ```

- [ ] **Step 2: Run** `uv run --locked --extra dev python -m pytest tests/test_configuration.py -q`; confirm failures identify the missing loader or behavior.
- [ ] **Step 3: Implement the interfaces.** Open selected files without following symlinks and verify file metadata on the opened descriptor. Use standard-library JSON parsing with duplicate-key rejection. Validate names without emitting values. Add the exact accepted JSON example.
- [ ] **Step 4: Repeat the focused tests** and run Ruff/format checks on touched files. Success requires all configuration cases passing.
- [ ] **Step 5: Commit** `feat: add private JSON platform configuration`.

### Task 2: Process launcher and TuShare compatibility

**Files:** Create `src/quant_market_data_platform/cli_config.py` and `tests/test_config_cli.py`. Modify `src/quant_market_data_platform/cli.py`, `src/quant_market_data_platform/providers/_env.py`, and `tests/test_tushare_a_share.py`. Update `docs/operations.md`, `docs/operations.en.md`, and both `docs/operations/credentials.md` and `credentials.en.md`.

Update owner configuration-dependent templates in `scripts/systemd/`:
`tushare-minute-replacement-campaign.service`,
`tushare-minute-replacement-campaign-accelerate.service`,
`tushare-minute-replacement-campaign-tail.service`,
`tushare-minute-operational-daily.service`,
`tushare-minute-reverse-backfill.service`,
`tushare-fundamentals-vintage-archive.service`, and
`market-data-platform-retention.service` when its environment file belongs to
the same credential source. Update `tests/test_tushare_minute_campaign_systemd.py`,
`tests/test_fundamentals_systemd.py`, `tests/test_retention_systemd.py`, and
`scripts/systemd/README.md`; preserve unrelated retention configuration.

**Interfaces:**
- Consume Task 1's loader and resolver.
- `add_config_parser(subparsers: argparse._SubParsersAction) -> None` registers `config check` and `config run` with `--config PATH`.
- `apply_config_environment(path: Path | None = None) -> Path | None` applies selected JSON to the current process; return `None` only when no JSON configuration is selected or present.
- `config run` accepts argv after `--` and uses direct process replacement. The launcher supplies a non-secret loaded-configuration marker so downstream script bridges do not recurse.
- TuShare's `_load_tushare_env_files()` first uses this loader and returns without env-file reads when JSON was selected.

- [ ] **Step 1: Add failing CLI and provider tests.** Exercise direct provider token/API resolution, unavailable selected JSON, legacy-only fallback, invalid config, missing command, opaque child environment, and subprocess signal/exit behavior:

  ```python
  assert run_child_with_json_config(exit_code=7).returncode == 7
  assert resolve_tushare_api_url(token_env="TUSHARE_TOKEN_2") == "https://proxy.example.com"
  assert secret_fixture not in config_check_result.stdout + config_check_result.stderr
  ```

- [ ] **Step 2: Run** `uv run --locked --extra dev python -m pytest tests/test_config_cli.py tests/test_tushare_a_share.py -q`; confirm the new cases fail before implementation.
- [ ] **Step 3: Implement the CLI and provider integration.** Avoid shell evaluation and exported credential files. Keep SDK/dataframe dependencies out of configuration imports. Document exact command lines, precedence, secret-free diagnostics, and legacy fallback limits.
- [ ] **Step 4: Repeat focused tests**, CLI-help/governance checks, and touched-file lint/format checks. Test that core configuration commands work without optional supplier dependencies.
- [ ] **Step 5: Commit** `feat: launch provider jobs from JSON configuration`.

### Task 3: QuantZone query planning and authenticated checks

**Files:** Create `src/quant_market_data_platform/quantzone_plan.py`, `src/quant_market_data_platform/providers/quantzone.py`, `src/quant_market_data_platform/cli_quantzone.py`, and `tests/test_quantzone_plan.py`. Modify `src/quant_market_data_platform/cli.py`, `pyproject.toml`, and `uv.lock`. Create `docs/operations/quantzone.en.md` and its Chinese companion; update operations indexes.

**Interfaces:**
- Consume `PlatformConfig` and `resolve_environment`.
- `FactorBatch` holds a stable batch key, explicit stock/factor tuples, and inclusive start/end dates.
- `FactorDownloadPlan` holds query identity, ordered batches, resolved research output root, timeout, and SDK version; no credentials.
- `build_factor_plan(config: PlatformConfig, inherited: Mapping[str, str]) -> FactorDownloadPlan` validates and deterministically batches the configured query.
- `FactorClient` protocol exposes `get_quota`, `list_factors`, `list_stocks`, `get_factors`, and `close`.
- `create_client(config: PlatformConfig, inherited: Mapping[str, str]) -> FactorClient` lazily imports the pinned SDK and explicitly supplies credentials and timeout.
- `check_quantzone(client: FactorClient, plan: FactorDownloadPlan) -> dict[str, object]` validates quota, factor identity/coverage, and authoritative stock mapping; reports only non-secret status.
- Its result includes `symbol_map: dict[str, str]` from bare stock identifiers to verified platform symbols and `factor_coverage: dict[str, dict[str, str]]` for the selected factors. Ambiguous bare identifiers fail validation before acquisition.
- Register `quantzone check` and `quantzone download-factors --dry-run`.

- [ ] **Step 1: Add failing planning and fake-client tests** for fixed dates, explicit lists, size limits, supported SDK version, timeout, retry count, invalid directories, escaping symlinks, coverage, missing credentials, and sanitized errors:

  ```python
  assert all(len(batch.ukeys) <= 100 and len(batch.factors) <= 20 for batch in plan.batches)
  assert all((batch.end_date - batch.start_date).days <= 365 for batch in plan.batches)
  assert fake_client.factor_queries == []  # dry-run never acquires data
  ```

- [ ] **Step 2: Run** `uv run --locked --extra dev python -m pytest tests/test_quantzone_plan.py -q`; verify new behavior fails before implementation.
- [ ] **Step 3: Implement planning, lazy SDK construction, and checks.** Normalize dates without an open-ended server default. Validate paths beneath the selected data root. Add optional extra `quantzone` with SDK `==0.10.0`, pandas, and PyArrow; regenerate the lock without changing unrelated pins. Preserve credential opacity and classify supplier failures by safe exception type/code.
- [ ] **Step 4: Repeat tests** and CLI-help/governance checks without installing the extra. In a separate disposable environment, verify actual SDK signatures on Python 3.11–3.13; record results outside Git.
- [ ] **Step 5: Commit** `feat: plan bounded QuantZone factor downloads`.

### Task 4: Validated immutable downloads and resume

**Files:** Create `src/quant_market_data_platform/quantzone_download.py`, `src/quant_market_data_platform/quantzone_artifacts.py`, and `tests/test_quantzone_download.py`. Update the QuantZone CLI and topic docs.

**Interfaces:**
- Consume `FactorDownloadPlan`, `FactorClient`, and the catalog/identifier checks from Task 3.
- `validate_factor_batch(frame: pandas.DataFrame, batch: FactorBatch, symbol_map: Mapping[str, str]) -> pandas.DataFrame` preserves nulls and checks schema, numeric values, dates, membership, and uniqueness.
- `project_factor_panel(validated: pandas.DataFrame, symbol_map: Mapping[str, str]) -> pandas.DataFrame` creates the `symbol`/`trade_date` wide factor projection after uniqueness checks.
- `run_factor_download(plan: FactorDownloadPlan, client: FactorClient, *, resume: Path | None = None) -> Path` returns the completed run directory or leaves a recoverable partial run on failure.
- CLI `--resume PATH` selects a partial run and verifies its identity and hashes before making a new supplier request. A completed run can be verified but never mutated.
- Receipt schema: `market-data-platform.quantzone-factor-download.v1`; record query identity, SDK version, UTC request boundaries, batch/file hashes, observed coverage/missingness, run status, and the three accepted evidence flags.
- Store native long Parquet batches and a `symbol`/`trade_date` research projection in the same run, with receipt-listed paths and hashes. Build projections without loading the entire native dataset at once.

- [ ] **Step 1: Add failing fake-SDK and filesystem tests** for duplicate keys, invalid dates/stocks/factors, ambiguous mappings, numeric failures/infinities, nulls, empty responses, partial writes, output escapes, deterministic IDs, quota/auth/network failure without retries, resume mismatch, corruption, completed immutability, and cleanup of temporary files:

  ```python
  assert projection.loc[0, "symbol"] == "000001.SZ"
  assert projection["demo_factor"].isna().sum() == 1
  assert receipt["evidence"]["pit_availability"] == "unknown"
  assert resume_client.factor_queries == missing_batch_queries
  ```

- [ ] **Step 2: Run** `uv run --locked --extra dev python -m pytest tests/test_quantzone_download.py -q`; verify tests fail on absent behavior.
- [ ] **Step 3: Implement atomic batch and receipt writes, validation, projection, and resume.** Reject collisions and conflicting duplicate observations across batches. Do not silently count empty batches as complete coverage, average duplicates, fill nulls, reuse an invalid projection, or repeat completed queries. Always close the SDK client.
- [ ] **Step 4: Repeat focused tests** and a synthetic `ResearchDataInterface` compatibility probe. Run the owner gates listed below; resolve failures before updating the Draft PR and merging through required checks.
- [ ] **Step 5: Commit** `feat: persist and resume QuantZone research artifacts`; record the merged owner SHA for consumer pins.

### Task 5: Consumer deployment compatibility

**Files and repository boundaries:**
- `quant-market-data-deploy`: update `systemd/quant-market-data-platform.service.template`, `config/production.env.example`, `versions/public-platform.env`, `bin/verify_public_pin.sh` if the existing verifier needs CLI checks, `README.md`, and `tests/test_deployment_naming.py`.
- `quant-intel-deploy`: create `scripts/lib/data_platform_config.sh`; update `scripts/setup_cron.sh`, `scripts/refresh_tushare_daily.sh`, `scripts/refresh_tushare_report_datasets.sh`, `scripts/publish_a_share_current.sh`, `scripts/refresh_daily_watch20.sh`, `scripts/refresh_daily_watch20_quant_research.sh`, `scripts/recover_business_stage.sh`, `scripts/weekly_client_basket_provider.sh`, `scripts/morning_pipeline.sh`, and `scripts/evening_pipeline.sh`. Update affected `deploy/systemd/*.service` provider references, `tests/smoke/test_scheduler_templates.py`, add `tests/smoke/test_data_platform_config.py`, and update `AGENTS.md` and `docs/production-migration.md` to the new active filename.
- `quant-intel-platform`: update `src/a_share_daily/tushare_credentials.py`, `src/a_share_daily/deploy_check/env_helpers.py`, and `tests/test_a_share_deploy_check.py`; update credential guidance in `docs/boundary-contract.md`.

**Interfaces:**
- Every consumer calls the merged `marketdata config run --config PATH -- COMMAND ARGS...` interface; no consumer imports owner configuration code or maintains a second JSON parser for supplier credentials.
- Shell bridge `data_platform_config_exec SCRIPT ARGUMENTS...` loads non-secret deployment paths, checks the owner marker, and delegates to the stable owner CLI once. It preserves exit status and original script arguments.
- Service templates replace the owner credential `EnvironmentFile` with `Environment=DATA_PLATFORM_CONFIG=...` and invoke the owner launcher. Other credential owners retain their own configuration.
- Intel health checks inspect the explicit selected file's metadata and inherited provider status; they do not load supplier credentials from legacy env files when JSON is selected.

- [ ] **Step 1: Create each consumer's isolated worktree** after read-only branch/remote/worktree inspection and fetch. Use the merged provider SHA, not its development branch.
- [ ] **Step 2: Add failing tests** for rendered unit argv, one-time script delegation, arguments containing whitespace/metacharacters, unavailable owner CLI/config, child exit status, explicit JSON health checks, and removal of active provider-env references. Assert generated units contain `config run` and do not contain the owner credential `EnvironmentFile`.
- [ ] **Step 3: Run each repository's focused tests** to establish the failing new expectations.
- [ ] **Step 4: Implement consumer changes and update immutable pins.** Preserve schedule, working directory, existing data roots, unrelated credentials, and job arguments. Remove provider-secret loading from shell and doctor helpers. Keep diagnostics free of configuration values.
- [ ] **Step 5: Run each repository's required gates**, then commit and merge separate PRs in dependency order. Keep private Actions disabled. Record tested SHAs and merged owner/consumer pins.

### Task 6: Local migration, rollout, and bounded real-data probe

**Files:** Local owner configuration, private non-secret deployment-path configuration, affected user service files/drop-ins, and inactive rollback files only. Inventory and receipts belong outside Git under the corresponding project's data/configuration roots.

**Interfaces:** Consume the merged owner and consumer releases plus `marketdata config check`, `marketdata config run`, and QuantZone CLI. Use the existing deployment promotion command with explicit immutable SHAs, after checking its current help and local release inventory.

- [ ] **Step 1: Record the local inventory.** Include current releases, aliases, manifests, active config references, all affected effective units/drop-ins, timers, running jobs, cron/recovery entry points, and previous state. Record no values or per-token hashes.
- [ ] **Step 2: Exercise the migration using synthetic credentials** in a temporary private directory. Assert environment-equivalence booleans, permissions `0600`, rejection of unexpected/conflicting JSON, no secret in logs/receipts, and restoration of the prior source on a simulated failed preflight.
- [ ] **Step 3: Prepare the real populated JSON privately.** Preserve every existing env setting, add unconfigured QuantZone placeholders if needed, and compare values in memory without logging them. Reject conflicts and create the restricted inactive backup before replacing the active source.
- [ ] **Step 4: Stage immutable production releases and updated units.** Run owner startup/help, config checks, child environment-equivalence checks, unit rendering/verification, and deployment preflights without market-data production or message delivery.
- [ ] **Step 5: Apply the authorized switch** only after preflights pass and affected running jobs can finish safely. Remove the credential env file from active lookup, reload systemd, and verify effective unit references and previous timer state. On failure, restore the corresponding old release, units, and single active source.
- [ ] **Step 6: Run authenticated QuantZone checks and the configured small download** if credentials are present. Record actual query count, status, row counts, Parquet hashes, receipt path, and existing-interface consumption. If credentials are absent, finish the migration and explicitly leave real acquisition pending; do not mark that portion successful.
- [ ] **Step 7: Verify PR merges and local state**, clean up only this task's completed resources, fast-forward clean primary checkouts, and report PRs, merge SHAs, validation, actual migration/query outcome, and remaining work.

## Owner release gates

Run from the owner task worktree after runtime changes:

```bash
uv sync --locked --extra dev
uv run --locked --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run --locked --extra dev python -m ruff check .
uv run --locked --extra dev python -m ruff format --check .
uv run --locked --extra dev ty check --error-on-warning
uv run --locked --extra dev python scripts/dev/quality_debt.py
uv run --locked --extra dev python scripts/dev/maintainability_metrics.py
uv run --locked --extra dev python scripts/dev/compatibility_governance.py --check
uv run --locked --extra dev python scripts/dev/architecture_governance.py --check
git diff --check origin/main...HEAD
```

Run strict documentation and required GitHub checks as configured in the owner
repository. Do not weaken quality baselines or ignore failures to complete this
plan. Consumer release gates come from each consumer's current `AGENTS.md`;
record the actual commands, results, skips, and commit SHAs in its PR.

## Execution handoff

The design is accepted. This plan is ready for the user's review and execution
method selection. Recommend native execution in the current session because
configuration, launcher, downloader, and rollout interfaces are sequentially
dependent. If that method is selected, implement through `executing-plans` and
use one fresh reviewer before final release. If subagent-driven execution is
selected, use `subagent-driven-development` with separate task worktrees.
