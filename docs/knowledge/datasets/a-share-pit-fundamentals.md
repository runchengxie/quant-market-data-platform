---
schema_version: knowledge/v2
id: quant-market-data-platform.dataset.a_share.pit_fundamentals
type: dataset
owner: quant-market-data-platform
status: active
last_verified: 2026-09-30
source_of_truth: false
authority_ref: asset:quant-market-data-platform:a_share:pit_fundamentals
relations: []
asset_key: pit_fundamentals
---
# A-share point-in-time fundamentals

`pit_fundamentals` is the published A-share point-in-time fundamentals asset. Its stable asset key and default path are listed in the [data contract](../../contracts.md); the current publication state must be checked in the deployment's current manifest.

The builder accepts normalized fundamentals inputs and uses disclosure dates plus a configured calendar-day delay to determine `available_date`. Rows without usable report-period or disclosure-date information are quarantined. The output preserves revisions by retrieval vintage; same-event conflicting values fail closed. `trade_date` mirrors `available_date` and is not guaranteed to be an exchange session.

See the [fundamentals operations guide](../../a-share-fundamentals.md) for the raw-to-PIT workflow. The implementation is maintained in `src/market_data_platform/providers/tushare_a_share_fundamentals_part04.py`.
