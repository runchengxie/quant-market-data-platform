# Current Assets Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成生产读取入口向 `current_assets` 的切换，验证发布与回滚流程，保留仍有用途的兼容链接，并形成清晰的迁移状态记录。

**Architecture:** 读取方通过公开平台已有的 current contract 接口获取实体目录。发布程序继续负责更新 contract，回滚通过指定实体目录重新生成 contract。只有没有生产引用、没有发布用途、也没有恢复用途的链接才允许删除。

**Tech Stack:** Python、Bash、JSON contract、pytest、uv。

**Spec:** `docs/contracts.md`、`docs/operations/testing.md`、`docs/maintenance-audit.md`。

## Global Constraints

- `ths_member` 本轮状态为暂缓，不参与读取切换和兼容链接清理。
- 不删除用户已有的本地修改，不重置现有分支。
- 代码改动使用独立 worktree、独立分支和本地测试。
- GitHub Actions 不作为本轮主要验证手段。
- 不用软链接替代实体目录，现有兼容链接只在有明确引用时保留。

---

### Task 1: Audit current contract consumers

**Files:**
- Create: `docs/migration/current-assets-consumer-audit.md`
- Test: `tests/test_current_assets_migration.py`

**Interfaces:**
- Consumes: `metadata/current_assets/a_share_current.json` and repository source references.
- Produces: a repeatable audit that distinguishes contract-backed readers from compatibility-only paths.

- [ ] Enumerate production readers and classify each `latest` reference as contract-backed, publication-only, internal input, or unresolved.
- [ ] Add a test that verifies the main research readers resolve through the current contract.
- [ ] Record `ths_member` as deferred because its manifest is unavailable and the provider is rate-limited.

### Task 2: Verify publish and rollback behavior locally

**Files:**
- Modify: `docs/operations/testing.md`
- Create: `docs/migration/current-assets-publish-rollback-checklist.md`

**Interfaces:**
- Consumes: current contract inspection and publish scripts.
- Produces: local commands and acceptance criteria for publish, rollback, and recovery.

- [ ] Run contract inspection against the current data root.
- [ ] Run publish and recovery checks against a temporary fixture or no-send mode.
- [ ] Confirm failed publication does not replace the previous current contract.
- [ ] Confirm rollback points to an existing immutable version.

### Task 3: Retire only proven-safe aliases

**Files:**
- Create: `docs/migration/current-assets-alias-retirement-20260908.tsv`

**Interfaces:**
- Consumes: Task 1 consumer audit and filesystem symlink inventory.
- Produces: a per-alias decision of `retain`, `retire`, or `defer`.

- [ ] Retain aliases used by publishers, recovery scripts, minute materialization, or context builds.
- [ ] Retain aliases for datasets whose readers have not yet moved to the contract.
- [ ] Remove only aliases with no code, release, scheduler, or recovery references.
- [ ] Verify no broken current contract paths after each removal.

### Task 4: Publish migration status

**Files:**
- Create: `docs/migration/current-assets-migration-status.md`

**Interfaces:**
- Consumes: migration TSV records, consumer audit, and local verification results.
- Produces: one newcomer-friendly summary of completed, deferred, and remaining work.

- [ ] Summarize completed data materialization and manifest reconciliation.
- [ ] Mark `ths_member` as deferred.
- [ ] List remaining compatibility aliases and their owners.
- [ ] Record the local verification commands and results.

