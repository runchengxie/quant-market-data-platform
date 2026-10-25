# Offline staging resource ownership implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement each checked task.

**Goal:** Close real Parquet readers and all staging writers on success and failed validation without publishing a failed month.

**Architecture:** Keep the existing offline layout and checkpoint contracts. Reader scope uses PyArrow's context manager; a local staging sink context owns dynamically created writers and closes every sink even when one close fails. Primary failures remain primary; all cleanup errors are retained as exception notes. Sink.close closes its writer even if flushing fails.

**Tech Stack:** Python, PyArrow, pytest, Ruff, ty.

**Spec:** User-authorized audit continuation; existing offline layout input integrity and recovery contract.

- [x] Record unchanged-source targeted tests and existing isolated complexity failure.
- [x] Add real-file lifecycle regressions; observe reader/writer leaks before changes.
- [x] Scope readers in append_input and Sink.verify; scope monthly sinks and make close exception safe. Preserve artifacts, errors and checkpoint semantics.
- [x] Run regression, offline integration and configured full tests/static/security gates; separately report legacy C901 budget without relaxing it.
- [ ] Independent review, PR, current-base checks, merge and task-only cleanup.

Record necessary source/test growth using the repository's exact scope change-record convention, including source revision and retirement condition; retain all structural and diagnostic thresholds.

The 26 versus 9 legacy complexity budget is not a reason to mechanically split other validators. This PR addresses demonstrated I/O cleanup failures independently of the draft shared gate.
