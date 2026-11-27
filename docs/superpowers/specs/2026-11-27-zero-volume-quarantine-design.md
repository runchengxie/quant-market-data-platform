# Reproduced zero-volume minute evidence quarantine

The acquisition lane needs an explicit disposition for a complete source day whose
only fatal audit issue is zero volume with positive amount. Original source values,
failed acceptances and block markers remain unchanged. The entire date stays
ineligible for acceptance and publication.

## Approach

Correcting values or relaxing the normal audit would lose source fidelity. Reusing
the single-stock notional exemption would misstate both category and scope. Add an
explicit zero-volume registration command with exact multi-stock evidence instead.

The command checks the complete acquired universe and session grid, all structural
checks and the existing hard notional guard. The supplied symbol set must equal all
zero-volume/positive-amount failure symbols. A requery must contain exactly those
complete stock-days and reproduce every normalized field. No other nonzero audit
issue is eligible. There is no provider request or source rewrite.

New entries declare `anomaly_kind`, `scope=trade_date`, and the complete `symbols`
set. Their registry uses schema v2. New readers accept v1 and v2, including legacy
single-symbol entries within v2. Old readers reject v2 and remain blocked. The old
single-symbol registration command and semantics stay supported.

## Validation and rollout

Test reproduction, omitted/extra symbols, grid/value mismatch, other failures,
changed evidence, unknown markers, malformed v2 entries, lock contention, atomic
write interruption, idempotence, publication refusal, and old-reader rejection.
Run the complete existing quality gate. Upgrade acquisition, monitoring and
recovery verifiers before registering v2 evidence. Validate recovery in preview
mode before any explicit production reconcile. Registration and production
deployment are separate operations outside this source change.
