# Acquisition exception source binding plan

> **For agentic workers:** Use superpowers:executing-plans for implementation and verification.

**Goal:** Stop TuShare stk_mins acquisition from applying another source's no-data exclusions, retaining the generic loader's existing API behavior and every promotion restriction.

**Architecture:** Add one optional expected (provider, dataset) pair to the shared policy loader. The TuShare acquisition consumer always supplies its own identity. Check the pair before reducing a universe, including dates with no active entries. Generic callers that omit the pair keep existing behavior.

**Spec:** Read-only C901 review found a wrong-source policy reducing the real acquisition universe; runtime fixtures declare tushare/stk_mins; readiness-only legacy policy validation is separate and must remain unchanged.

- [x] Reproduce wrong or missing source identity through the acquisition workflow before changing code.
- [x] Add a narrow shared loader check and bind the actual TuShare caller, with generic and valid-current compatibility regressions.
- [x] Document the runtime/header requirement and no production-policy migration in this task.
- [x] Record precise source/test growth without changing structure, complexity or other thresholds.
- [ ] Validate own source, then latest combined source, independent review and owner PR gates before merge.
