# Unified JSON configuration and QuantZone pilot

Status: accepted by the user on 2026-10-02; implementation and production
migration have not started.

## Purpose and accepted scope

Provide one active private JSON configuration for the market-data owner,
including existing TuShare settings and QuantZone credentials. Add a bounded
QuantZone factor download pilot owned by `quant-market-data-platform`.
Research consumes frozen local files; reviewed publication remains a separate
decision.

The user approved the JSON draft and inclusion of existing TuShare production
tasks in the migration. This document makes the shared configuration contract,
deployment changes, and acceptance criteria explicit for review.

Success means that existing jobs receive the same TuShare variables from JSON,
no active credential-bearing env file is maintained alongside JSON, and a small
QuantZone query produces a validated, immutable research artifact with a receipt.
If QuantZone credentials remain unconfigured, offline verification can complete,
but authenticated acquisition remains explicitly pending.

## Ownership and dependencies

| Owner | Responsibility |
| --- | --- |
| `quant-market-data-platform` | Configuration loading, process launch, optional SDK integration, bounded factor acquisition, validation, and download receipts. |
| `quant-market-data-deploy` | Pin a merged immutable platform release and update deployment templates and checks. |
| `quant-intel-deploy` | Launch existing data-dependent jobs through the owner CLI and update scheduler and recovery wiring. |
| `quant-intel-platform` | Update credential-file health checks to understand the selected JSON configuration. |
| `quant-research` | Read frozen local panels and evaluate factors; do not add online supplier clients. |
| `quant-platform` | Continue consuming generic data contracts; no supplier-specific dependency is required. |
| `quant-factor-observatory` | Consume only separately reviewed public projections with adequate supplier permission. |

Merge and validate the provider before consumers. Production launches must use
an immutable release path, never a task worktree. Machine-specific paths, actual
credentials, migration inventories, and runtime outputs stay outside Git.

## JSON contract

Public `config/config.example.json` contains null credential placeholders and
portable paths. The populated `config.json` is private and has mode `0600`.
Credential-only directories have mode `0700`. Select its location explicitly
with `DATA_PLATFORM_CONFIG`; a portable fallback uses
`${XDG_CONFIG_HOME:-$HOME/.config}/quant-market-data-platform/config.json`.
The host selects its owner-specific grouping through deployment configuration.

The top-level object has exactly three versioned sections:

```json
{
  "schema_version": 1,
  "environment": {
    "DATA_PLATFORM_ROOT": "${HOME}/data/quant/quant-market-data-platform",
    "TUSHARE_TOKEN": null,
    "TUSHARE_TOKEN_2": null,
    "TUSHARE_API_URL_2": null,
    "TUSHARE_MINUTE_PROVIDER_NO_DATA_EXCEPTIONS": null,
    "QUANTZONE_ACCESS_KEY": null,
    "QUANTZONE_SIGN_SECRET": null,
    "QUANTZONE_BASE_URL": "https://api.quantzone.tech"
  },
  "downloads": {
    "quantzone": {
      "sdk_version": "0.10.0",
      "timeout_seconds": 60,
      "query": {
        "ukeys": ["000001.XSHE", "600519.XSHG"],
        "factor": ["trend_dominance_factor"],
        "start_date": "2024-01-02",
        "end_date": "2024-01-05"
      },
      "batch": {"calendar_days": 7, "max_symbols": 100, "max_factors": 20},
      "retry": {"max_attempts": 1},
      "output": {
        "relative_directory": "research/quantzone-pilot",
        "format": "parquet",
        "immutable_runs": true
      },
      "evidence": {
        "pit_availability": "unknown",
        "revision_safety": "unknown",
        "public_redistribution": "not_authorized"
      }
    }
  }
}
```

`environment` accepts valid environment names and string or null values. Null
means unconfigured. Preserve existing provider variable names and every existing
setting during migration. Do not convert credential strings into numbers or
interpret their contents as paths or shell expressions.

Already-present process variables take precedence, including intentionally empty
values. JSON fills absent variables. Only declared path fields support `${HOME}`
and `${DATA_PLATFORM_ROOT}` substitution; secrets remain opaque. Reject duplicate
JSON keys, unsupported schema versions, invalid types, and malformed required
sections. Error messages identify keys and conditions without printing values.

An explicitly selected JSON file must exist and pass ownership, regular-file,
non-symlink, and private-permission checks. Failure must stop the operation.
Legacy env loading remains a compatibility option only when JSON is not
selected or present. A selected JSON configuration never merges credentials
from an env file, repository `.env`, or another private configuration.

## Public configuration interface

Add two owner CLI operations:

```text
marketdata config check --config PATH
marketdata config run --config PATH -- COMMAND ARGUMENTS...
```

`check` validates configuration without network calls or credential-value output.
It reports whether requested credentials are configured, not their contents.
`run` validates, resolves the child environment, and executes argv directly
without shell evaluation, shell command construction, or an exported env file.
Child signals and exit status retain the command's normal behavior.

The TuShare provider uses the same configuration loader so direct owner commands
and wrapped deployment jobs resolve consistent credentials and API URLs. The
implementation must work with the core dependencies; configuration parsing and
process launch do not require QuantZone, pandas, PyArrow, or TuShare installed.

## QuantZone acquisition interface

Add an optional `quantzone` extra pinned initially to SDK `0.10.0`, plus the
dataframe and Parquet requirements. Loading ordinary CLI help or unrelated
commands must not import or initialize the SDK.

```text
marketdata quantzone check --config PATH
marketdata quantzone download-factors --config PATH --dry-run
marketdata quantzone download-factors --config PATH
```

`check` performs authenticated quota and factor-catalog checks. The downloader
validates the configuration and catalog coverage before factor acquisition.
Dry-run produces a credential-free query plan without calling authenticated
services. Required credentials are passed explicitly to `QuantZone` and remain
out of argv, logs, serialized plans, receipts, and public data.

The first pilot requires an explicit stock list, fixed start and end dates, and
explicit factors. Each request contains at most 100 stocks and 20 factors, and
spans at most 365 days. The configured calendar batch is seven days by default.
The initial example selects two stocks, one documented factor, and four calendar
dates. Catalog presence and coverage are checked on the actual account; the
example is not proof that the factor is currently queryable.

SDK requests return data in memory. The data-platform downloader owns batching
and persistence. Timeout is in `(0, 60]` seconds. No automatic retry occurs in the
first pilot because repeating a query may consume additional quota. Authentication,
quota, revoked-key, IP, version, validation, and network failures stop acquisition
with a sanitized error and a resumable record of completed batches.

## Data and receipts

Create a unique run directory under `DATA_PLATFORM_ROOT` and the configured
relative research directory. Reject absolute output overrides, traversal, and
symlinks that escape the selected root. Never change `current` aliases or the
active A-share contract during this pilot.

Save each successful batch atomically as Parquet. Store the native
`date / ukey / value / factor` data and a research projection in the same run.
Build the projection using an explicit, verified stock-identifier mapping to
`symbol` and `trade_date`. Preserve six-digit identifiers and leading zeros;
never infer an exchange solely from a bare code when mapping is ambiguous.

Validate the response schema, requested dates, stock set, factor set, and unique
`(date, ukey, factor)` keys. Reject nonnumeric non-null values and infinities.
Preserve missing observations and null factor values. Pivot only after checking
key uniqueness; do not average duplicates or fill missing values.

The receipt records query identity, SDK version, retrieval times, batch status,
observed row counts, coverage, missingness, file hashes, and the artifact schema.
It records `pit_availability=unknown`, `revision_safety=unknown`, and
`public_redistribution=not_authorized` unless later evidence supports a reviewed
change. Retrieval time must not be presented as historical information
availability. A zero-row response is recorded as empty, not as complete market
coverage or a successful predictive result.

Resume verifies the existing query identity and completed-batch hashes before
skipping work. Completed runs are immutable. Failures leave a recoverable partial
run rather than replacing a completed artifact or silently promoting partial
data. Research references an explicit completed run and its receipt.

## Production migration

1. Validate and merge the owner implementation; validate consumer changes
   against the merged owner CLI. Pin an immutable owner release.
2. Inventory existing env consumers, effective systemd units and drop-ins,
   cron/recovery paths, running jobs, timers, and the stable production release.
   Keep the existing data roots and schedules.
3. Prepare the populated JSON through a local migration that preserves all env
   settings without printing values. Reject unexpected existing JSON files or
   conflicting active credential sources. Create files privately before writing.
4. Update deployment entry points to launch through `marketdata config run`.
   Replace the credential `EnvironmentFile` with the non-secret JSON location.
   Shell scripts must not `source` JSON or require a generated secret env file.
   Update credential-file health checks and deployment documentation.
5. Verify environment equivalence in a no-network child probe, render and check
   units, and exercise deployed owner command startup without data production or
   messaging. Record previous unit and timer state for restoration.
6. Switch the local active configuration after consumers are ready. Preserve one
   restricted rollback backup outside active lookup paths; remove the credential
   env file from active lookup. Reload systemd and verify effective configuration
   and timer state. Do not restart an unrelated or currently running job.
7. Run a bounded QuantZone query only after its credentials are configured.
   Report missing credentials as pending work; do not manufacture successful
   acquisition evidence from the offline fixture.

On migration failure, restore the previous release and scheduler configuration
and the matching single active credential source. Do not retain JSON and env as
two continuously maintained sources. Historical archives are not read as active
configuration and are not deleted by this migration.

## Validation and release criteria

- Configuration tests cover permissions, ownership, symlink rejection, duplicate
  keys, null placeholders, opaque secrets, precedence, invalid schema, sanitized
  failures, direct TuShare resolution, and child exit/signal behavior.
- Downloader tests use a fake SDK and cover batching limits, catalog coverage,
  identifier ambiguity, schema errors, duplicate observations, nulls, empty
  results, quota/auth/network errors, atomic writes, output containment, hash
  corruption, resume, and immutable completion.
- The standard test environment does not require QuantZone. A separate installed
  SDK probe checks actual signatures and import compatibility with supported
  Python 3.11–3.13 environments; no Python 3.14 wheel is assumed.
- Run the data platform's documented Python, Ruff, formatting, type, quality-debt,
  maintainability, compatibility, and architecture gates. Run affected consumer
  tests and scheduler/shell checks in their own task worktrees. Respect required
  GitHub checks and merge the provider before consumers.
- Local migration evidence records environment-equivalence booleans, effective
  unit references, release identities, file permissions, and remaining work;
  never record credential values or per-token hashes.
- A real-data success claim requires an actual authenticated query, persisted
  batch hashes, and a completed receipt. Offline compatibility checks establish
  interface feasibility only; they do not establish factor efficacy or PIT safety.

## Explicit limits

This change does not replace the current TuShare market-data assets, add a new
production factor alias, claim point-in-time availability, select strategies,
or publish QuantZone values or derived results to a public site. QuantZone's
local factor-expression engine and backtester can be evaluated independently
after the acquisition pilot establishes useful data and acceptable conditions.

## Sources

- [QuantZone SDK integration documentation](https://www.quantzone.tech/docs),
  checked on 2026-10-02 against the installed SDK's public signatures.
- [QuantZone SDK 0.10.0 metadata](https://pypi.org/project/quantzone/0.10.0/).
- [Existing data contracts](../../contracts.en.md) and
  [research data interface](../../research-data-interface.en.md).
- [Existing credential configuration](../../operations/credentials.en.md).

The public SDK documentation confirms bounded factor queries and in-memory
results. It does not establish historical information availability or permission
to redistribute supplier data through the Observatory.
