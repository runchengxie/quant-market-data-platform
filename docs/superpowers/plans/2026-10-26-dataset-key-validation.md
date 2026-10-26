# Dataset primary-key boundary implementation plan

> **For agentic workers:** Use superpowers:executing-plans for implementation and verification.

**Goal:** Enforce the existing list-of-unique-nonempty-strings primary_key contract before accepting or writing an asset, preserving valid payloads and schema v1.

**Architecture:** Add a string type check before the existing emptiness check. Do not coerce names, change optional empty keys, or tighten the separate L2 evaluator's partial override mapping API. Test public validation and write paths, including preserving existing output on rejection.

**Spec:** User-authorized contract audit; DatasetContract annotations, current validator error contract and L2 dataset documentation.

**Tech Stack:** Python, JSON, pytest, Ruff, ty.

- [x] Record baseline and search direct/dynamic/cross-repository references.
- [x] Add public rejection, unchanged valid roundtrip and write side-effect regressions; observe malformed keys accepted before the fix.
- [x] Apply narrow validation and document key semantics in Chinese/English contract pages.
- [x] Record only exact necessary source/test growth under the existing change-record policy; do not change complexity or structural budgets.
- [ ] Verify targeted/full configured checks, independent review, owner PR, current-base validation and task-only cleanup.

Cross-repository search found source API/writer references and a separate l2_quality_gate partial mapping consumer, but no active external caller of validate_dataset_contract. Do not infer that every JSON consumer validates this envelope or change partial overrides without a separate contract assessment.
