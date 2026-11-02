# Historical broker forecast snapshots

`report_rc_history` retrieves broker earnings forecasts by calendar month. Each request is bounded to 3,000 rows and continues until a short terminal page. Repeated pages, exact duplicate records and dates outside the request fail the snapshot. At most four isolated acquisition processes run concurrently.

Use the existing configuration runner. Choose a new output directory under the owning data root, outside Git. The command never updates published assets or current/latest aliases.

```bash
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- \
  python -m quant_market_data_platform.providers.report_rc_history \
  --out-dir "$DATA_PLATFORM_ROOT/tasks/report-rc-history-<run-id>" \
  --start-date 20110101 --end-date 20261101 \
  --source-revision "$(git rev-parse HEAD)" --workers 4
```

The locked environment needs the `tushare` extra. Use the configured token environment, which defaults to `TUSHARE_TOKEN_2`; pass `--token-env` to select another configured account. Avoid displaying credentials or copying private configuration into the snapshot.

Repeat the same query and output path to resume. Completed monthly payloads are accepted only after receipt, query, pagination and SHA-256 verification. Empty months also have receipts. Different ranges or fields require a new snapshot; a corrupt checkpoint is rejected rather than overwritten. Keep a single coordinator per output directory. A partial run remains partial until all requested months have verified receipts.

`run_source_revision` identifies the current coordinator. Monthly receipts separately retain `acquisition_revision` and the imported acquisition module hash. Older receipts without acquisition provenance remain unknown on resume. A newer coordinator does not certify that an older partition was acquired with its code.

The data retains `report_date`, forecast period, institution, provider `create_time` and local retrieval time. Provider update time and report date do not establish complete historical first availability or every historical correction. A retrospective reconstruction must disclose this limit. New observation-date snapshots can accumulate forward version evidence by using a new immutable output directory for each run.

Consumers should pin the completed manifest and payload hashes. Preserve annual coverage, missing forecasts and update delays before evaluating factors. This module downloads vendor data for authorized local research; its raw output is not a public website asset.
