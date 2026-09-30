# `current_assets` publication and rollback checklist

[中文页面](current-assets-publish-rollback-checklist.md)

## Before publication

1. Confirm the target data directory contains `manifest.yml`.
2. Verify that the file and row counts in the manifest match the files on disk.
3. Confirm the target version is a physical directory and does not depend on a compatibility link scheduled for removal.
4. Run the production reader in no-send mode.

## After publication

1. Inspect `metadata/current_assets/a_share_current.json`.
2. Run:

   ```bash
   uv run marketdata contract inspect \
     --artifacts-root "$DATA_PLATFORM_ROOT" \
     --market a_share \
     --format text
   ```

3. Confirm the research entry point reads the target version directory.
4. Inspect artifact receipts for morning reports, evening reports, and DailyWatch20.

## Rollback

1. Select an existing physical version directory that passes manifest validation.
2. Regenerate the current contract in no-send mode.
3. Run `contract inspect` again.
4. Run the research reader and confirm it resolves to the selected version.
5. Confirm the failed candidate did not overwrite the previous current contract.

## Review record

- Current-contract check: run.
- Evening-report read and delivery: succeeded on 2026-09-08.
- `ths_member`: deferred.
- Compatibility-link removal: no links met the removal conditions in this review.
