# Overseas daily forward receipts v1

[中文页面](overseas-forward.md)

Run `python -m quant_market_data_platform.overseas_forward --root <owner-data>/forward/overseas-v1 --mode capture_probe` through the installed `marketdata config run --config <private-config> -- ...` launcher. The collector makes fresh requests using `TUSHARE_TOKEN_2` and its configured custom route, reusing the owner client and transport failover. It never treats an old local file as a new provider receipt. Requests have a 12-second timeout and failures retain only the exception class.

Raw CSV files remain in owner storage. Every invocation exclusively creates a UTC timestamp/UUID directory, `started.json` before network access, and `receipt.json` after collection. A started directory without a final receipt records an interruption. CSV and JSON files cannot be overwritten by this tool. Receipts record actual request start/completion, source dates, SHA256 hashes, empty results and failures. Regular market close times are assumptions, not evidence of provider publication time. FX source dates are interpreted as ending at the next UTC midnight, with an additional consumer-side hour buffer.

Schedule weekday runs at 08:44 Asia/Shanghai with a strict 08:45 cutoff. `timely_capture` requires scheduled mode, a weekday start in 08:43–08:45, and completion by 08:45. Weekend runs, late catch-up and probes cannot qualify. QMR separately verifies that the same receipt confirms an SSE trading day and durably seals its prediction before the cutoff. Source completeness depends on the frozen signal, so a timely receipt alone does not qualify an observation.

This research archive does not publish current/latest aliases or extend the Hong Kong production contract. Exclusive creation prevents this tool from overwriting evidence, while hashes reveal external changes. It does not provide filesystem WORM guarantees. Production uses merged immutable releases. Configure systemd with `Persistent=false` to avoid startup catch-up, and retain timestamp-based late-run exclusion.
