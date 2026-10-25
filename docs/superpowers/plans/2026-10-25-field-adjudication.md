# Field adjudication implementation plan

> **For agentic workers:** Use superpowers:executing-plans; parent performs independent whole-branch review.

**Goal:** Make explicitly selected, manually verified conflict fields consumable in an independent partial reconstructed research asset.

**Architecture:** A small separate module wraps a hash-bound existing partial asset and evidence ledger. It validates every selected event/field against original conflicting candidates, verifies PDF hashes, preserves original reports and ledger entries, and exposes only an explicit field reader. Old APIs retain their schema and rejection behavior.

**Tech Stack:** Python, JSON, existing statement research schema and pytest fixtures.

**Spec:** Parent task brief, reflected by the constraints below.

## Global constraints

- Never publish to production, current aliases, selectors or PIT seals; no market API calls.
- Evidence is a human assertion about source semantics. A matching file hash proves byte identity, not official semantic certification.
- Only exact selected conflict fields may be adjudicated; retain original candidates and consensus without modification.
- Bind serialized partial, ledger and PDF hashes; reject unknown, duplicate, out-of-scope, missing and mismatched evidence.
- Availability is at least the maximum original usable time, verification/acquisition time and actual build creation time. No historical possession or full-market certification.
- Use new schema and explicit reader opt-in; old reader must reject the new schema.

## Review focus

- A ledger's extra common field must not authorize another field or whole row.
- Null differs from zero, and integer precision must survive.
- Duplicate or forged event keys/fields must fail closed.
- Changed/missing PDFs or source artifacts must fail both build and read.
- Before usable time, no candidate or evidence values may leak.

### Task 1: Bounded builder and reader

Files: create `src/quant_market_data_platform/statement_field_adjudication.py`, `tests/test_statement_field_adjudication.py`; update `docs/contracts.md`.

Interfaces: `build_field_adjudicated_asset(*, source: tuple[Path, str], ledger: tuple[Path, str], selections: tuple[tuple[str, str, str, str, str], ...], code_revision: str) -> dict`; `read_adjudicated_field(path: Path, *, expected_sha256: str, accept_manual_field_adjudication: bool, field_key: tuple[str, str, str, str, str], as_of: str) -> dict`.

- [x] Write tests for one selected field while another conflict remains blocked; watch missing module/API fail.
- [x] Implement validation and field reader with retained originals and conservative availability.
- [x] Exercise unknown/duplicate keys, evidence mismatch, malformed timestamps, missing/changed PDFs, no opt-in and legacy-reader rejection.
- [x] Run focused tests, then all required repository checks; record results.
- [ ] Commit, push and open PR; wait exact-head CI without merge.

### Task 2: Independent research artifact

- [ ] Select the 10 actual conflict fields in the existing ledger, bind actual input hashes and build to a new task report directory.
- [ ] Consume all 10 fields, prove historical as-of blocked, and prove unchanged original 140 consensus / 10 conflict events and selector 7/143 coverage boundary.
- [ ] Record source revision, artifact hashes and reproducible API usage outside source control; parent reviews before merge.
