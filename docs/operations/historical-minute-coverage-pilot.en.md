# Independent historical minute coverage pilot

[中文页面](historical-minute-coverage-pilot.md)

The offline pilot scans at most ten dates and writes a new diagnostic report. It does not
change original sidecars, issue acceptance receipts, or publish assets. Even
`eligible_for_receipt_review` always has `full_market_promotion_allowed=false`.

```bash
uv run --extra dev python scripts/operations/historical_minute_coverage_pilot.py \
  --manifest "$TASK_REPORT/manifest.json" --output "$TASK_REPORT/pilot.json"
```

Keep the manifest and outputs outside the repository. The manifest has `source_revision`,
`inputs`, and `dates`. Each input maps a file key to `path` (absolute), `sha256` (full digest),
`bytes`, `device`, `inode`, `ctime_ns`, and `mtime_ns`. Every file is hashed before and after scanning; changed content or file identity aborts the run.
The output uses exclusive creation and cannot overwrite a previous report.

Each date declares `date` (YYYYMMDD), `quarantined`, `sidecar`, `partitions` (all candidates),
`daily`, `daily_basic`, `stocks` (mapping explicit L/D/P/G roles to distinct snapshot files, including empty snapshots), and `policy`.
File references use keys from `inputs`. Include original audits, quarantine records,
transition source code and announcement references as additional bound inputs. Preserve
unknown original producer revisions as unknown; the auditor revision is not a substitute.
Manifest provenance and quarantine declarations require review: this diagnostic tool is
not an authentication or publication boundary.

The report keeps raw symbol differences and applies existing effective-date transitions.
It lists prelisting BJ symbols separately using dated listing evidence. Missing currency
or listing evidence, inconsistent daily references, missing symbols, applicable provider
exclusions, duplicate candidates and quarantine all prevent eligibility. Suspension
differences remain unresolved until independently dated evidence is available; no new
suspension exemption is introduced. Transition intervals and announcements are documented
in [minute operations](a-share-minutes.en.md).

Native structure, the 241-bar grid, price/flow guards and sidecar binding are rescanned one
date at a time. Previous semantic conclusions are not reused. Legacy policy markers remain
unchanged. The report records input bytes, elapsed time, peak RSS and zero provider requests.
Hash verification reads input files at least twice; pilot cost is not publication acceptance cost.
