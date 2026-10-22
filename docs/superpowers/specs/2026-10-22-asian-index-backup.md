# Asian index operational fallback

Status: proposed interface, pending implementation-plan review.

## Outcome

A cross-market report can recover missing Hang Seng, Nikkei 225 and Korea
Composite daily closes through an owner-managed TuShare-compatible route.
Recovery preserves the observation session and actual acquisition time. A
provider failure leaves partial data visible rather than manufacturing readiness.

The existing Yahoo path remains first choice. A successfully verified snapshot
continues to return immediately. The research-only `overseas-receipt-v1` archive
and its scheduled capture deadline retain their current contract.

## Evidence and limits

An offline probe checked six existing research receipts and their index CSV
hashes. The latest receipt contained both required sessions for all three
indices. It completed after the two early report fetch schedules, so those
records were unavailable at the earlier runs. Four probe cases accepted valid
CSV and rejected changed bytes, duplicate dates and a missing required session.

This establishes candidate-source feasibility, not continuous availability or
independence of underlying feeds. The configured TuShare-compatible route must
be identified accurately and must not be described as official direct access.
The source acquisition and CSV handling remain the data owner's responsibility.

## Ownership

- `quant-market-data-platform`: capture CLI, receipt schema and append-only raw CSV.
- `quant-intel-platform`: validate and consume the receipt, recover missing index
  quotes and recompute affected report mappings.
- `quant-intel-deploy`: installed CLI orchestration, deadlines, explicit paths,
  release pins and persistent Yahoo cooldown state.

Consumers use installed public commands and versioned files. They never import
another repository's implementation. This narrowly scoped report-index artifact
does not enable Hong Kong stock production or change published asset aliases.

## Operational receipt v1

Proposed module command:

```text
python -m quant_market_data_platform.overseas_index_capture
  --root <owner-report-index-root> --date YYYYMMDD --symbols HSI,N225,KS11
```

The deployment supplies an explicit root under the owner data directory. The
collector creates one exclusive run directory and writes `started.json` first.
Each requested index has at most one logical API call, using the existing owner
client and a 12-second per-transport request timeout. Route failover remains
subject to the outer 45-second process deadline. Only missing requested indices
are queried. Empty, permission and exhausted transport results stay distinct.
Never serialize credential-bearing exception text or request headers.

The collector requests the preceding 21 calendar days through the requested
calendar date. It does not choose market sessions. The consumer resolves exact
sessions using its existing exchange calendars.

Write each raw CSV exclusively. Write `receipt.json` last, with:

- `protocol`: `overseas-index-operational-v1`.
- `requested_date`, `started_utc`, `completed_utc`: explicit aware UTC timestamps.
- `sources`: requested code to `status`, `api`, request start/end, row count,
  source dates, CSV basename, SHA256 and a sanitized provider/route identifier.
- `status`: `complete`, `partial` or `failed`; a valid receipt can contain
  per-index failures. `started.json` alone is an interrupted run, never usable.

No global latest pointer, no research qualification flags and no alias update.
The CLI prints one structured receipt-path result after finalization. The parent
accepts an exact path only inside the selected root and current task run.

## Report recovery

The existing fetch entry point gains a deployed opt-in recovery mode. Its first
phase runs the existing snapshot/live path once. If a needed Asian index remains
missing, the deployment requests an operational receipt for those symbols only.
It then invokes a report-side receipt consumer without running Yahoo, FRED or
AAII again. Disabled recovery preserves current behavior.

The consumer requires the operational protocol and requested date to match. It
checks per-source status, simple CSV basenames, resolved containment, no symlink
escape, file size limits, row count, SHA256, unique dates, explicit code mapping,
finite positive closes and the exact target/preceding exchange sessions. Extra
future session rows, incompatible codes or inconsistent source timestamps are
rejected. Each consumed receipt must have completed no later than the recovery
observation time. Replaying an earlier run uses its explicit earlier cutoff.

For each accepted symbol, compute percentage change using its two same-source
closes. Preserve `^HSI`, `^N225`, `^KS11`, `close`, `pct_chg` and `as_of_date`.
Add source identity, receipt hash/path, actual received time and fallback reason.
Keep initial attempts in a separate diagnostics field. Remove only current
missing/error entries for successfully recovered symbols. Other errors remain.
Recompute report mappings after recovery, then run the existing freshness checks.
A rejected or absent source leaves that symbol partial. Receipt data never
replaces a valid first-choice quote.

## Rate limiting

Do not infer HTTP429 from generic empty data. Expose a typed, sanitized Yahoo
rate-limit signal where the library supplies one. Once observed, suppress
immediate Yahoo retries in that run and use the eligible alternate path.

The deployment owns one configured cooldown-state file, coordinated by a lock
and atomic replace. HTTP Retry-After delay or date wins when available. Without
it, use a 15-minute initial delay, exponential growth for consecutive confirmed
429s and a 60-minute cap. Persist actual observed UTC time. Only a successful
Yahoo fetch clears the consecutive-rate-limit count. Clock skew or malformed
state must not suppress Yahoo indefinitely. Independent raw Yahoo consumers
remain outside this task; do not claim an account-wide budget has been added.

## Resource and release boundaries

The deployment starts the owner child in its own process group and kills/reaps
that group at the 45-second deadline. A timeout leaves any incomplete run for
inspection and cannot turn partial output into a valid receipt. Do not invoke
shell command strings or mutate shared provider configuration.

No sending, existing task restart or production promotion is part of initial
verification. First verify offline contracts and a bounded isolated shadow.
Merge provider before consumers. Before an eventual production activation,
validate the exact combined release pins, installed entry points and rollback
version. Do not switch the minute owner release while its writer is active.
