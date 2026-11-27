# Board archive outcomes and bounded failure traversal

The archive currently prints a successful CLI result even when every board fails.
One stored board also makes an incomplete collection receipt look successful.
The remote-disconnect retry fix exposes this path instead of aborting immediately.

Keep the selected board scope and existing per-request retry policy. Count
catalogued, already archived, pending, selected, attempted, stored, failed,
unattempted and deliberately deferred boards independently. Return the same
outcome in the persisted receipt and CLI. Failed and partial work exits nonzero.
An empty scope is explicit; already archived boards are not missing work.

Stop after three consecutive unsuccessful board fetches by default, including
wrong-date curves. A successful store resets that count. The positive integer
`ArchiveOptions.max_consecutive_failures` is an API option; no host configuration
or extra retry layer is added. HTTP 4xx, including exhausted 429, found in the
provider exception cause chain stops traversal immediately. Generic provider
errors are not classified as retryable and are not retried by the archive loop.

The existing fetcher may attempt a request three times. Three failed board
fetches therefore mean at most nine such attempts for persistent failure, not a
three-request global budget. A deliberate `--limit` narrows selected scope;
successful selected work does not certify the entire catalogue or minute grid.

Preserve successful rows and closing-row skip behavior. Keep source dates and
failed evidence visible. Do not publish, start services, change a production
database, add an external notification, or weaken quality gates for this fix.

Validation covers all-failed and mixed results, consecutive failure cutoff and
reset, permanent/throttle causes, wrong dates, already archived boards and
limited scope, CLI exits and receipt/output agreement. Complete the repository
quality gate and public checks before merge.
