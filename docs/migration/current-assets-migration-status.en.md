# `current_assets` migration status

[中文页面](current-assets-migration-status.md)

Updated 2026-09-18.

## Completed

- Confirmed staging candidates were archived under `archive/staging/`.
- Multiple data batches were published from dated physical directories into the current contract.
- Valid versions were added for `hsgt_top10`, `margin`, and `margin_detail`.
- The `moneyflow_hsgt` manifest was corrected.
- `moneyflow_ths` was revalidated and published as the 20260908 version.
- General research data, THS hot stocks, and the DC-concept candidate universe use the current contract.
- The DC-concept reader fails when the current contract is missing.
- An evening report was republished once using the new data directory.

## Deferred

`ths_member` remains deferred because `manifest.yml` is missing and the TuShare endpoint is rate-limited.

## Closeout findings

- Reads of `latest` were audited across deployment, research, publication, and recovery flows.
- Publication-failure and rollback paths have dedicated fixtures, no-send checks, and tests.
- Minute materialization, context building, publication, and recovery still use `latest`; keep it under the production contract instead of deleting it.
- `ths_member` remains blocked by the live TuShare source and rate limits. Do not publish it before the recovery conditions are met.

## Relationship to repository migration

The remaining work described here concerns cleanup of the current-assets compatibility layer inside the data platform. Migration of the old repository's capabilities is complete. Readable copies of the old repository and historical notes are under `quant-market-data-platform/docs/migration/legacy-materials/` or `quant-research/docs/migration/legacy-materials/`. The `latest`, rollback fixture, compatibility-link, and `ths_member` items here concern data-production contract governance. The audit has recorded decisions; do not remove or publish these items without no-send, rollback, and recovery validation.

## Current decision

No compatibility links were removed in this review. They still serve publication, recovery, minute materialization, or context-building flows, and removing them directly would increase production risk.

Before a future removal, first switch the corresponding reader and pass no-send, rollback, and recovery checks.
