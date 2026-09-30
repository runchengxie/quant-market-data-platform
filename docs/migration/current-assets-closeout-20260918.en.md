# Current-Assets Compatibility Closeout

[中文页面](current-assets-closeout-20260918.md)

Updated: 2026-09-18

## Conclusion

This audit covered code, scheduling, publication, and recovery reads of the current-assets compatibility layer. It found no `latest` entry that could be safely removed without changing production semantics. These aliases remain operational contracts with active consumers, not leftover migration artifacts.

## Verified consumers

| Consumer | Current use | Decision |
| --- | --- | --- |
| Current contract | Primary read path for research, DailyWatch20, and DC candidate pools | Keep as the preferred entry point |
| Publish and recovery workflows | Maintain compatibility aliases after publication and read published versions during recovery | Keep `latest` |
| Minute-data materialization | Read mutable input directories and seal date-versioned outputs | Keep input `latest`; switch to a version directory after materialization |
| Context construction | Use an internal date-built `latest` alias | Keep until context reads are migrated |
| Legacy research reads | No longer a primary path and do not fall back when the current contract is missing | Keep the stricter failure behavior |

Evidence includes `tests/test_current_path_audit.py`, `tests/test_context_snapshots_publish.py`, `tests/test_materialize_current_versions.py`, `tests/test_publish_probe_assets.py`, and `tests/test_cli_governance.py`.

## `ths_member`

`ths_member` is not eligible for publication in the current contract. Its current directory lacks `manifest.yml`, and the TuShare endpoint has rate-limit risk. This audit does not synthesize a manifest, publish the incomplete directory, or make morning/evening reports depend on it. The blocker is recovery of the external data source, not an incomplete repository migration.

Resume only after a real provider pull yields non-empty data and a complete manifest, followed by no-send reads, quality checks, and rollback validation.

## Migration status

- Canonical copies and manifests exist for legacy repositories, notes, and experiment materials.
- Production paths have moved to five canonical repositories.
- `latest` compatibility entries have been audited and retained where consumers remain.
- Publication failure and rollback have dedicated tests and a no-send execution path.
- The only remaining external item is to restore `ths_member` from its source and republish it.

## Removal criteria

Remove an individual `latest` entry only after all of the following are true:

1. Production code, schedules, publication, and recovery scripts no longer read it.
2. The current contract works in both no-send and real rehearsal reads.
3. Rollback and recovery have been validated.
4. Version directories, manifests, and hashes independently reproduce the asset.
5. The change passes quality gates and is observed through a full production cycle after promotion.
