# Retention Link Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove per-link comparisons against unrelated candidate paths.

**Architecture:** Index normalized candidate component tuples and their prefixes. Resolve a link normally, retrieve candidate ancestors and descendants, then apply the unchanged self-containment exclusion. Use exact rooted-set membership for the existing metadata exclusions. Keep reference traversal and all error handling unchanged.

**Tech Stack:** Python 3.11+, pathlib, pytest, uv.

**Spec:** docs/superpowers/specs/2026-11-06-retention-link-index.md

## Global Constraints

- Preserve current decisions and first-reference evidence.
- Do not cache filesystem resolutions or follow symlink directories.
- Do not change production data, aliases, schedulers or quota state.

## Review Focus

- A target ancestor must protect all nested candidates.
- A link inside a parent candidate may still protect a nested candidate.
- Sibling prefixes such as v1 and v10 must remain disjoint for path overlap.
- Windows case-insensitive components and different anchors must stay distinct as appropriate.
- An incomplete reference scan must still fail closed.

### Task 1: Index candidate link overlap

**Files:** Modify `src/quant_market_data_platform/retention_references.py`, `tests/test_retention_external_references.py`, and the exact recorded line allowance in `config/code-quality-baseline.json`.

**Interfaces:** Public planner and retained-reference API remain unchanged.

- [ ] Add a real-link operation bound and observe failure on the current implementation.
- [ ] Implement lexical candidate indexing without caching link resolution.
- [ ] Verify nested, self, disjoint and Windows component behavior; bound relative ancestry work while preserving only the exact two metadata exclusions.
- [ ] Run focused suites and the full quality gate; benchmark complete synthetic PlanItems equivalence and a bounded real-root scan.
- [ ] Commit and independently review the complete branch, then integrate only after current target checks pass.
- [ ] Retain results, verify postmerge checks, and remove only this task's resources.
