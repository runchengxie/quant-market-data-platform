# Asian index fallback implementation plan

> For agentic workers: use superpowers:executing-plans task by task. Plan approved; implementation and validation are tracked below.

Goal: recover missing Asian index closes through a verified owner receipt, with
bounded acquisition and truthful receipt times.

Architecture: the data owner captures raw rows and finalizes a versioned receipt.
The report owner validates it and recovers only missing symbols. Deployment
orchestrates installed commands and owns deadlines and cooldown persistence.

Tech stack: existing Python, pytest, CSV/JSON/SHA256, exchange-calendars in the
report consumer, subprocess and the existing provider configuration launcher.

Spec: [Asian index operational fallback](../specs/2026-10-22-asian-index-backup.md).

## Global constraints

- Protocol `overseas-index-operational-v1`, separate from the research archive.
- Three supported codes: HSI, N225, KS11. Preserve the report's Yahoo identifiers.
- One logical API call per missing symbol, 12-second transport timeout,
  45-second parent process-group deadline including route failover.
- No production alias, credentials, shared configuration or research timer edits.
- Keep timestamps aware UTC and distinguish source session from receipt time.
- Every repository uses its own task worktree, checks and PR.

## Review focus

- A later receipt must fail an earlier observation cutoff.
- A timeout must reap all child processes and reject unfinished receipts.
- A malicious CSV path, symlink, bad hash or duplicate session must fail closed.
- Recovery of one index must preserve all unrelated errors and valid quotes.
- Persistent429 state must survive restarts without indefinite suppression.

## Task 1: Data owner operational capture

Files in `quant-market-data-platform`:

- Create `src/quant_market_data_platform/overseas_index_capture.py`.
- Create `tests/test_overseas_index_capture.py`.
- Update `docs/contracts.md`, `docs/contracts.en.md`, `docs/operations.md` and
  `docs/operations.en.md` for the separate operational protocol and CLI.

Interfaces:

- `capture(root: Path, trade_date: str, symbols: list[str], *, client=None) -> Path`.
- Module CLI described in the spec; stdout returns a JSON receipt path.
- Calls the existing configured owner client, preserving the chosen credential
  namespace and route association. Raw exception messages never reach outputs.

- [ ] Add a fake client test returning HSI rows; assert one API call, date bounds,
  a finalized operational receipt, accurate row count and matching CSV SHA256.
- [ ] Add tests for partial/empty/permission/transport failure, invalid date,
  unsupported/duplicate symbol, exclusive run creation and secret redaction.
- [ ] Run these tests and record the expected missing-module failure.
- [ ] Implement capture and CLI with started-first, receipt-last exclusive writes.
- [ ] Run the focused tests and all owner-required checks from AGENTS.md. Check
  docs/public boundary and shared code-quality ratchet if applicable. Any budget
  delta must match the feature exactly with a recorded reason.
- [ ] Commit, push and open the provider PR. Merge only after required checks.

## Task 2: Report receipt validation and recovery

Files in `quant-intel-platform`:

- Create `src/a_share_daily/cross_market_index_receipt.py`.
- Create `tests/test_cross_market_index_receipt.py`.
- Modify `src/a_share_daily/fallback_fetch.py` and, where needed,
  `src/a_share_daily/cross_market.py` to expose recovery without a second live run.
- Add recovery integration tests beside `tests/test_cross_market_partial_cache.py`.

Interfaces:

- `recover_indices(data: dict, receipt_path: Path, *, root: Path,
  observed_at: datetime) -> dict` returns a copy with verified replacements.
- A `fallback_fetch` receipt-consumption CLI mode accepts an exact finalized
  receipt and isolated snapshot destination. It never fetches network data.
- Use existing `_equity_sessions` for exact market target and preceding sessions.

- [ ] Add a valid recovery test asserting exact source dates, same-source return,
  preserved valid quotes, preserved unrelated errors and retained attempt history.
- [ ] Add one rejection case each for wrong protocol/date/code, future receipt,
  future session, missing session, duplicated date, invalid/NaN/zero close,
  inconsistent source time, bad hash/row count, oversized file and path escape.
- [ ] Assert the real reader never opens a symlink target outside the approved
  root and rejects interrupted runs with only `started.json`.
- [ ] Run tests to observe the missing adapter failure.
- [ ] Implement the reader and non-fetching recovery mode; recompute affected
  mappings and freshness after successful replacements.
- [ ] Run focused regressions and `python project_tools/check_all.py --scope all`.
- [ ] Commit, push and open the report PR after provider protocol is merged.

## Task 3: Confirmed Yahoo rate-limit behavior

Files in `quant-intel-platform`:

- Modify `src/a_share_daily/cross_market.py`.
- Extend `tests/test_cross_market_retry.py`.
- Create `src/a_share_daily/cross_market_rate_limit.py` and
  `tests/test_cross_market_rate_limit.py` for pure cooldown calculations.

Interfaces:

- Provider diagnostics distinguish confirmed429 from ordinary insufficient bars.
- `next_retry_at(observed_at: datetime, failures: int,
  retry_after: str | None) -> datetime` implements the spec's bounded policy.
- Persisted-state storage belongs to deployment; pure policy stays report-side.

- [ ] Test typed rate-limit exceptions suppress further immediate serial retries;
  missing-session data without429 retains the existing bounded retry behavior.
- [ ] Test Retry-After seconds/date, invalid values, 15-minute initial delay,
  repeated-failure60-minute cap, clock skew and success reset.
- [ ] Observe failing tests, implement typed diagnostics and the pure policy,
  then rerun retry/session/cache regressions and the report full gate.
- [ ] Include in the report PR without changing existing unrelated fetch budgets.

## Task 4: Deployment orchestration and persistent state

Files in `quant-intel-deploy`:

- Create `scripts/cross_market_index_recovery.py` and
  `tests/smoke/test_cross_market_index_recovery.py`.
- Modify `scripts/local_fetch_cross_market.sh` and extend
  `tests/smoke/test_cross_market_installed.py`.
- Update the existing dependency pin manifest and operational runbook only after
  the exact merged provider/report revisions are known.

Interfaces:

- Opt-in orchestration invokes explicitly installed commands through the existing
  owner launcher. Credential settings remain private and owner-managed.
- Inputs: target date, installed owner/report roots, owner artifact directory,
  task namespace and explicit cooldown-state path.
- Capture only symbols still missing after one normal report fetch. Invoke the
  non-fetching consumer exactly once with the resulting finalized receipt.

- [ ] Add subprocess-fake tests for healthy-first-choice zero extra owner calls,
  partial recovery, mixed success, owner permission failure and absent config.
- [ ] Add a process-tree timeout test proving45-second bounded termination via
  an injected shorter test deadline, no orphan descendants and no receipt use.
- [ ] Add state-lock/atomic-write tests for two concurrent runs, malformed state,
  cold start, bounded clock skew and explicit success clearing.
- [ ] Prove no second Yahoo/FRED/AAII run, no shell interpolation, no sibling
  source imports, no network access in tests and no message-sending subprocess.
- [ ] Observe failures; implement orchestration preserving default disabled
  behavior and the existing snapshot path/exit-code contract.
- [ ] Run every deploy-required local gate in AGENTS.md, including smoke coverage,
  static/type checks, shell checks, boundary and dependency audit. No Actions run
  solely to replace missing local validation.
- [ ] Commit, push and merge after provider/report revisions pass their gates.

## Task 5: Isolated verification and release decision

- [ ] Against the exact merged pins, use existing cached rows in a new task output
  directory. Compare expected dates, provenance and partial status with the live
  production snapshot, without overwriting that snapshot.
- [ ] Record source data and acquisition-time limits. The research08:44 receipt
  cannot establish06:45 availability. Do not report such a replay as live recovery.
- [ ] If real acquisition verification is needed, use at most one bounded owner
  run with explicit output path; never print credentials or send reports.
- [ ] Record the current production pins, active writer status and rollback target.
  A minute-writer conflict defers owner activation, not offline verification.
- [ ] Report actual PRs, merged commits, gates, isolated verification and activation
  status separately. Clean only this task's merged, saved-equivalent worktrees.

## Plan self-review

The probe establishes candidate records only. This plan adds the separate owner
contract before production consumption, incorporates transport failover into the
parent deadline, and keeps research and live receipt cutoffs distinct. It covers
the five review-focus failures and uses the provider-before-consumer merge order.
Implementation is present across three task branches. Owner local gates passed (1442 tests, one skip); report and deploy final validation and release verification are tracked in the PRs. Production activation remains disabled.
