# Special-event semantics in L2 data

[中文页面](l2-special-event-semantics.md)

This note records the initial classification decision for `Price <= 0` rows in the 105 GiB pilot. The classification is provisional; additional vendor evidence is needed to determine a unique interpretation of the source codes.

## Observations

In `deal_20260424.parquet` (194,296,495 rows), 36,317,914 rows (18.692%) have `Price == 0`. Their `Side` values are only `-11` and `-1`, and the corresponding buyer or seller identifier is zero. This differs from sampled ordinary positive-price trades, whose `Side` is `0` or `1`.

The same pattern appears in `deal_20260416.parquet`. These rows are more consistent with special auction, unmatched, or placeholder events than ordinary trades, but the available fields do not establish one vendor-defined meaning.

## Handling decision

- Preserve raw rows unchanged.
- Add sparse sidecar labels with `decision=tag` and `reason=special_price_or_event`.
- Exclude rows only when a downstream dataset contract explicitly requires executable positive-price trades, and record the exclusion in the manifest.
- Do not reorder or rewrite the raw stream to repair the timestamp reset observed in `deal_20260416`; preserve source order and publish the reset diagnostic.

For snapshots, interpret non-positive prices as empty book levels and label them `empty_snapshot_price`. Do not automatically delete these rows because a zero price can be a valid sentinel for an absent level.

The policy may be revised after comparing model and backtest results with and without tagged rows. The raw layer remains immutable under either decision.
