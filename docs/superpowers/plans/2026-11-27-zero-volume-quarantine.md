# Zero-volume quarantine implementation plan

> **For agentic workers:** Use superpowers:executing-plans for implementation.

**Goal:** Record reproduced zero-volume source failures for acquisition while
retaining whole-date publication rejection.

**Architecture:** Extend the hash-bound registry with a v2 entry variant and an
explicit registration function/command. Share immutable source, audit and atomic
registry helpers with the existing single-stock registration.

**Tech Stack:** Python, pandas, PyArrow, argparse, pytest.

**Spec:** ../specs/2026-11-27-zero-volume-quarantine-design.md

## Constraints and review focus

No raw-data or production-state changes. Preserve v1 reads and the old API.
Incomplete or extra requery symbols, changed source evidence, mixed fatal issues,
unknown audit markers and interrupted writes must remain blocked. Old readers
must reject a v2 registry.

## Task 1: Registry and registration

- [ ] Add failing fixture tests in `tests/test_minute_zero_volume_quarantine.py`
  for successful exact reproduction and the failure classes above.
- [ ] Run the new tests and retain their expected missing-interface failure.
- [ ] Add `register_zero_volume_quarantine(..., symbols, reason)` to
  `minute_semantic_quarantine.py`, v2 shape validation, and the independent
  zero-volume registration implementation. Reuse source/audit/store helpers from
  `_minute_semantic_registration.py` without changing old registration behavior.
- [ ] Run new and existing quarantine tests and inspect immutable fixture bytes.

## Task 2: CLI, contracts and delivery

- [ ] Add and run a failing real CLI registration test.
- [ ] Add `quarantine-zero-volume` with repeatable required `--symbol` arguments.
- [ ] Update paired operations/contracts docs with scope, evidence and consumer
  upgrade ordering. Record only measured source/test growth in the size baseline.
- [ ] Run full locked quality gate, inspect diff, commit, push to the verified
  owner remote and open a PR. Review/check exact head before merging.
