# Data operations overview

Inspect asset freshness, historical quality gaps, recorded index recovery, release paths and service
incidents through one read-only JSON command. A failed service invocation remains distinct from an
asset that was subsequently recovered and published.

```bash
uv run --extra dev python scripts/operations/data_status.py \
  --root "$DATA_PLATFORM_ROOT" --expected-date 20261023 \
  --production-root "$CODE_ROOT/production" --with-systemd \
  --evidence fundamentals="$TASK_REPORT/asset.json" \
  --evidence coverage="$TASK_REPORT/coverage-summary.json"
```

Supply the expected business trading date explicitly. The command does not infer calendar targets,
request providers, send messages, repair services or write receipts. Output goes to stdout. systemd
queries are opt-in and limited to five known services, with a five-second timeout each.

Minute `current` means the validated operational receipt covers the requested date, not that every
row was audited again. Preserve `quality_status` and inspect `contract_date_matches` for a stale
contract declaration. Materialized aliases must match their version receipt exactly. Asset paths
outside the supplied data root are rejected rather than implicitly trusted.

Historical evidence defaults to a six-hour freshness window, configurable with `--max-age-hours`.
`reported_ready` requires a completion state, full-market readiness, zero remaining/deferred dates,
and no quarantine. Missing, malformed, future or timezone-naive evidence cannot establish readiness.

The index window reads at most 1000 summaries and selects the latest 20 by completion timestamp.
Every JSON file is capped at 8 MiB. `no_samples` is unknown, not a perfect success rate. These are
recorded-run statistics, not uptime estimates; a scan-limit result needs input retention review.

Supplemental `--evidence NAME=PATH` entries expose only selected scalar declarations and a file hash,
always as `unverified`. They do not replace the corresponding independent receipt or partial-asset
consumer. Those consumers still enforce source bindings, explicit partial opt-in and conflict rules.

Exit code zero means the overview was produced. Read `needs_attention` and component statuses; do
not use this command as a publication gate. Schema `data-operations-overview.v1` explicitly declares
`receipt_inspection_not_data_acceptance`. Missing release paths and service errors stay visible.
