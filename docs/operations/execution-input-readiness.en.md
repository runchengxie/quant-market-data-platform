# Execution research input audit

[中文](execution-input-readiness.md)

Audit suspensions and corporate actions before interpreting fills or portfolio returns. The script reuses local assets and writes independent research slices plus `readiness.json`. It does not download or publish current assets, and partition existence does not certify symbol-level completeness.

```bash
uv run --extra tushare python scripts/operations/audit_execution_inputs.py \
  --suspension "$SUSPEND_HISTORY" "$SUSPEND_EXTENSION" \
  --dividend "$DIVIDEND_COMPACT" \
  --symbols "$RESEARCH_SYMBOLS" \
  --current-assets "$CURRENT_ASSETS" \
  --window y2025:20250101:20251231 \
  --window q1_2025:20250101:20250331 \
  --out-dir "$TASK_DATA_ROOT/audit-v1"
```

Use external Parquet inputs, a text file with one TuShare symbol per line, and an optional current asset manifest. Omit `--symbols` to retain all input symbols. Repeat `--window NAME:YYYYMMDD:YYYYMMDD` for each interval. Output must be empty; use a new directory for reruns.

Each window contains suspension, all-stage dividend, implemented dividend, and resolved ready-event files. Any announcement, implementation announcement, record, ex-dividend, payment, or listing date within the window selects an event, including events from older fiscal periods. The implemented export trims stage whitespace and removes exact duplicates only. Conflicting event versions remain with `event_key_conflict`; review them before applying cash or share flows. The all-stage export preserves original rows.

The receipt records hashes, counts, duplicate keys, missing implementation dates, requested symbols without records, and units. Missing records do not prove no corporate actions occurred; inspect download state and provider coverage. Retrieved history is reconstructed PIT, not revision-safe vintage evidence. Implementation availability, cash settlement, and new-share tradability need separate dates.

The [provider dividend contract](https://tushare.pro/document/2?doc_id=103) defines cash amounts per share and share distributions per old share. After-tax and before-tax provider fields do not replace account-specific tax rules. The [suspension contract](https://tushare.pro/document/2?doc_id=214) distinguishes suspended and resumed states and intraday timing. It supplies effective dates without historical availability timestamps. Dividend data does not cover every corporate action; minute bars do not supply L2 queues, book depth, or actual spreads.

Use the existing constraint or dividend download CLI through project `config run` for missing inputs. Start with a canary, retain task-specific outputs, and leave publication to an explicit coordinated step.

`dividend_events_ready` selects implemented events with ex-dividend dates in the window and groups by symbol and ex-dividend date. Equivalent economic versions differing only in announcement date collapse with `source_ann_dates` and `source_versions` retained. Conflicting fiscal periods, amounts, share ratios, or economic dates remain in the raw export and enter `event_resolution.blocked_events`. Missing implementation/record dates, unknown cash/share amounts, positive cash without payment dates, and positive share distributions without listing dates also block readiness. Missing values remain unknown. `available_from` uses the supplied implementation announcement date. Historical PIT remains reconstructed.
