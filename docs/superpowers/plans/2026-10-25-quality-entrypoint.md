# Local and CI Quality Gate Implementation Plan

> **For agentic workers:** Use the existing isolated task worktree. Execute and verify each step before delivery.

**Goal:** Run the same existing quality checks locally and in public CI, with isolated fixture resources.
**Architecture:** A Bash orchestration entrypoint calls the existing Python tools and batch runner. No business API, dependency pin or quality budget changes.
**Tech Stack:** Bash, locked uv dev environment, pytest, Ruff, ty and pip-audit.
**Spec:** The workspace owner's maintenance follow-up: align local/CI gates and remove host-dependent test resource failures.

## Constraints and review focus

Keep every existing CI check and add the documented local checks. Fail on command/export/audit errors. Temporary output must be outside source trees, require the existing 20 GiB integration prerequisite, and use a unique child directory. Preserve failed evidence and clean only owned successful output. Do not relax metrics or assertions.

## Steps

- [x] Inspect actual CI and documented local commands, branch state and existing worktrees.
- [x] Add shell regressions for low/invalid disk evidence, foreign Git trees, missing roots and independent workspace allocation; observe failure before the entrypoint exists.
- [x] Implement the shared entrypoint and point CI plus contributor documentation to it.
- [x] Run environment regressions, Bash syntax/ShellCheck and the complete locked gate. The documented isolated C901 budget fails on unchanged Python source (26 versus 9), so this proposal must remain unmerged.
- [ ] Obtain independent review, validate against latest main, pass remote checks, merge and clean task resources.

Independent review caught missing strict flags in the first orchestration draft. They are now preserved. The legacy budget is not refreshed, and the full Python fixture suite passes independently. Do not mark the complete gate as passing or remove checks to merge this proposal.
