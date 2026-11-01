# Local and CI Quality Gate Implementation Plan

> **For agentic workers:** Use the existing isolated task worktree. Execute and verify each step before delivery.

**Goal:** Run the same existing quality checks locally and in public CI, with isolated fixture resources.
**Architecture:** A Bash orchestration entrypoint calls the existing Python tools and batch runner. No business API or dependency pin changes. Keep the strict complexity and structural budgets fixed; record the exact source-line cost of extracted validation stages.
**Tech Stack:** Bash, locked uv dev environment, pytest, Ruff, ty and pip-audit.
**Spec:** The workspace owner's maintenance follow-up: align local/CI gates and remove host-dependent test resource failures.

## Constraints and review focus

Keep every existing CI check and add the documented local checks. Fail on command/export/audit errors. Temporary output must be outside source trees, require the existing 20 GiB integration prerequisite, and use a unique child directory. Preserve failed evidence and clean only owned successful output. Do not relax complexity, structural, coverage or assertion limits. Any source-line inventory allowance must match the measured extraction cost and carry a retirement condition.

## Steps

- [x] Inspect actual CI and documented local commands, branch state and existing worktrees.
- [x] Add shell regressions for low/invalid disk evidence, foreign Git trees, missing roots and independent workspace allocation; observe failure before the entrypoint exists.
- [x] Implement the shared entrypoint and point CI plus contributor documentation to it.
- [x] Run environment regressions, Bash syntax/ShellCheck and the complete locked gate. The documented isolated C901 budget fails on unchanged Python source (26 versus 9), so this proposal must remain unmerged.
- [x] Extract ordered validation/recovery stages from 12 existing modules. The existing strict complexity budget now passes with nine C901 findings; the exact helper-boundary cost is 74 Python lines, recorded with an exit condition.
- [x] Obtain independent read-only review of all extracted stages.
- [ ] Validate the complete gate against latest main, pass remote checks, merge and clean task resources.

Independent review caught missing strict flags in the first orchestration draft. They are preserved. The complexity budget remains nine. The first complete closeout attempt passed source checks and reached fixture batch 16, where a forbidden punctuation mark in the new audit note failed the existing prose test; the note was corrected. Complete final-base validation and remote checks remain required.
