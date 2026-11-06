# Retention Scan Cost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Avoid unnecessary path containment work without changing retirement decisions.

**Architecture:** Reject nonmatching reference basenames before checking candidate containment. For symlinks, check target overlap before testing whether the link is inside the candidate. Preserve all evidence and error handling.

**Tech Stack:** Python 3.11+, pathlib, pytest, uv.

**Spec:** docs/superpowers/specs/2026-11-06-retention-scan-cost.md

## Global Constraints

- Preserve all existing protection and incomplete-scan behavior.
- Do not change production data, current/latest pointers, quotas, or schedulers.
- Use predicate ordering and a compiled literal-name prefilter without changing matches.

## Review Focus

- Candidate names that are substrings of other version names retain conservative matching.
- Evidence inside a candidate must not protect itself.
- Relative and absolute references remain equivalent under basename matching.
- External malformed evidence still prevents retirement.
- Links into descendants and links inside candidates retain their current decisions.

### Task 1: Reduce reference matching path work

**Files:** Modify `src/quant_market_data_platform/retention_references.py` and `tests/test_retention_external_references.py`.

**Interfaces:** Keep `protect_retained_references` and planner output unchanged.

- [ ] Add a real filesystem regression with many unrelated reports and candidates; verify decisions and bound containment operations independently of candidate count for nonmatching text.
- [ ] Run the regression against unchanged code and observe the operation-count failure.
- [ ] Reorder predicates and prefilter unrelated text with escaped candidate names; preserve validation before filtering and the original matching loop.
- [ ] Run focused retention tests and the full repository quality command.
- [ ] Commit, obtain an independent whole-branch review, and address material findings.
- [ ] Validate current target checks, integrate through the user's repository, and clean only this task's resources.
