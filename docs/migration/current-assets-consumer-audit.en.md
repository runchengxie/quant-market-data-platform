# `current_assets` consumer audit

[中文页面](current-assets-consumer-audit.md)

Updated 2026-09-18.

## Findings

Main research consumers now resolve physical version directories through `metadata/current_assets/a_share_current.json`.

The DC-concept candidate universe was tightened in this review. If the current contract is missing or the asset is unavailable, the reader fails instead of falling back to `latest`.

## Consumer entry points

| Entry point | Current behavior | Result |
| --- | --- | --- |
| General A-share research data | `PublishedAssetContract.load_current` | Migrated |
| DailyWatch20 data | Reads the A-share current contract | Migrated |
| DailyWatch20 THS hot-stock universe | Reads the A-share current contract | Migrated |
| DailyWatch20 DC-concept universe | Reads the A-share current contract | Tightened in this review; fails when missing |
| Publisher | Updates the current contract and maintains compatibility entries | Retained |
| Minute-data materialization | Uses an explicitly selected minute-data input directory | Internal compatibility path temporarily retained |
| Context builder | Uses a date-built `latest` alias | Temporarily retained |

## Deferred asset

Publication of `ths_member` is deferred because its current data lacks `manifest.yml` and the TuShare endpoint is rate-limited. It is not a required input for morning or evening reports.

## Audit rules

- Prefer the current contract's `resolved_path` when reading data.
- Fail when the current contract is missing or an asset is absent or unavailable.
- Use `latest` only for registered internal inputs such as publication, recovery, minute materialization, and context building.
- Before removing a compatibility entry, inspect code, schedules, publication, and recovery flows together.
