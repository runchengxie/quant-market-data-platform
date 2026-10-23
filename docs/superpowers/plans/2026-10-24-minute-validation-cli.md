# Minute validation CLI implementation plan

Goal: preserve the existing exact-date, hash-bound SH/SZ validation while replacing the report consumer's internal provider import with an installed owner CLI.

Spec: the workspace audit follow-up design approved for execution in this conversation. Python argparse, existing minute-partition validator, JSON, subprocess, and synthetic Parquet fixtures; no live provider calls or production mutation.

1. Add `marketdata tushare validate-minute-partition --data-root PATH --trade-date YYYYMMDD --exchange SH|SZ`. Reuse owner validation, enforce the exchange universe rule and nonempty exclusive counts. Return the existing promotion receipt fields plus `validation_schema_version=marketdata.minute_exchange_overlay.v1` and `exchange`. Read-only, failed validation exits nonzero with no receipt on stdout.
2. Test a real synthetic partition through the CLI, reject missing sidecars, changed Parquet, wrong date/exchange and invalid options. Verify that input bytes remain unchanged. Run local owner gates, independent review, then provider PR and merge before consumers depend on it.
3. In quant-intel-platform accept an explicit owner CLI path (default MARKETDATA_CLI or installed marketdata), remove PYTHONPATH/PYTHONHOME for execution, enforce a timeout and validate response schema/date/exchange/required fields. Keep persisted marker receipt fields unchanged; no fallback to internal imports. Test malformed responses and process failures as well as matching old output structure.
4. In quant-intel-deploy pass MDP_DIR/.venv/bin/marketdata explicitly, pin the merged framework and record the minimum data-owner revision. Add a synthetic installed-package integration command with owner and consumer revisions in its result, including a tampered-partition failure. Do not traverse sibling repositories' test suites or publish synthetic artifacts.
5. Independently inventory old task delivery evidence and run remaining static-site/build/browser checks from isolated copies. Record actual gaps and preserve other tasks' branches/worktrees.

Review focus: never weaken partition/sidecar binding; no receipt on nonzero CLI exit; a correct-schema receipt for another date/exchange must fail; no sibling-source imports or PYTHONPATH fallback; tests must record the installed version combination rather than only source directory names.
