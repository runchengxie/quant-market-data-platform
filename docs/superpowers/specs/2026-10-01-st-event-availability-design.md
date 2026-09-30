# ST Event Availability for Historical Decisions

## Purpose

Make reconstructed A-share ST history safe to consume at historical decision times when the source records an announcement date but no intraday publication time. Preserve the effective status history while making its conservative information availability explicit. Keep the 600530.SH date conflict visible as source evidence, rather than treating a later name correction as the ST event's effective date.

## Current behavior

`build-a-share-st-history` reconstructs positive ST rows from historical `namechange` intervals. Each row carries `trade_date`, `ts_code`, `name`, interval dates, optional `ann_date`, `source`, and `pit_class`. The daily-clean builder derives `is_st` from this dated history. The ST timing audit compares same-day reconstructed rows with TuShare `st` events and correctly marks its receipt `revision_safe=false` because event dates do not carry intraday publication time.

Research consumers currently receive `is_st` from `daily_clean` and use it directly for formation or eligibility. The event's effective status and the time the source can prove it was available are therefore not represented as separate fields.

## Selected design

Add `available_from` to reconstructed ST history as a date-level point-in-time field. Keep `interval_start`, `interval_end`, `ann_date`, and `is_st` semantics unchanged. Propagate this as `st_available_from` in `daily_clean`, where it accompanies the existing `is_st` value.

- For a row whose `ann_date` is strictly before its effective interval start, set `available_from` to the first trading session on or after `interval_start`.
- For a row whose `ann_date` is equal to or later than `interval_start`, set `available_from` to the first trading session strictly after `ann_date`. This is the conservative rule for announcements without a publication time.
- For a row without a usable `ann_date`, set availability to unknown. Consumers must treat ST eligibility as unknown for that row; they must not infer an earlier availability from the effective name interval.
- Use the supplied trading calendar for session arithmetic. Do not approximate the next session with a calendar-day increment.
- Preserve the historical `is_st` value on every effective-date row. Consumers determine decision-time availability using `available_from <= trade_date` and fail closed when it is missing.

The published daily-clean artifact must expose `st_available_from` alongside `is_st`, with its manifest and receipt recording the source lineage and schema version. For rows with `is_st=true`, preserve the source event's `available_from`; rows with no active ST interval keep `st_available_from` null. Existing data that lacks the new field cannot claim the new PIT guarantee. Its current `revision_safe=false` status remains in force until a rebuilt candidate passes validation.

## 600530.SH evidence handling

The reconstructed history and TuShare `st` event dates disagree around the 2020 event. The company notice supports disclosure on 2020-04-29 and ST effectiveness on 2020-04-30; the later 2020-05-06 event is a short-name correction. Preserve these as distinct source observations in the audit. Do not move the 2020-04-30 effective date to 2020-05-06 or claim exact intraday availability without timestamp evidence. If the source provides only the 2020-04-29 announcement date, the conservative rule yields the next exchange session as `available_from`.

The 2020 interval was during a trading halt, so the absence of daily-clean rows during the halt must not be represented as proof that the historical announcement-time issue is resolved.

## Consumer contract

Consumers may use a positive `is_st` value for a decision date only when the matching row's `st_available_from` is present and no later than that decision date. If availability is unknown or later than the decision date, the ST state is unknown for that decision. Eligibility policies must fail closed on unknown ST state. No consumer may substitute the current name, `interval_start`, or `ann_date` alone for the availability check.

The implementation must identify each active consumer of the published `daily_clean` ST field and update its input validation and eligibility logic. The interface change is provider-first: publish and validate the field in `quant-market-data-platform`, then update consumers in their own repositories. Do not pin a consumer to an unmerged provider development commit.

## Validation and publication boundary

- Unit tests cover prior-dated announcements, same-day announcements, later announcements, missing announcement dates, weekends/holidays, and effective intervals spanning sessions.
- Provider contract tests verify the new field's schema, null behavior, calendar arithmetic, daily-clean propagation, lineage, and receipt compatibility.
- Consumer tests prove that a same-day event with unknown intraday time does not affect decisions before `available_from`, does affect decisions on/after it, and missing availability fails closed.
- Rebuild a staged candidate from existing source artifacts when inputs are adequate. Compare row counts, hashes, date coverage, `is_st` values, and timing-audit statuses against the current candidate. Do not write to the production alias or change a production pointer.
- Retain `revision_safe=false` unless all checks establish that the entire supported history meets the new availability contract. A data build alone does not prove source timestamps.

## Out of scope

- Changing the effective ST interval or rewriting historical `is_st` values to mimic announcement availability.
- Adding TuShare endpoints that require unavailable permissions or consume unapproved quota.
- Changing production data aliases, deployment pins, or production release pointers.
- Resolving historical source conflicts by deleting audit rows or weakening quality gates.

## Acceptance criteria

1. The reconstructed ST asset records effective status and decision-time availability separately.
2. Same-day or later announcements with unknown intraday time become available only from the next exchange session after `ann_date`.
3. Missing announcement dates remain unknown and are rejected for point-in-time eligibility.
4. The 600530.SH source observations remain auditable with the verified distinction between 2020-04-30 effectiveness and the 2020-05-06 name correction.
5. Every identified consumer uses the availability field or explicitly refuses PIT eligibility when it is absent.
6. A candidate can be built and compared without changing production pointers; no production deployment is performed.
