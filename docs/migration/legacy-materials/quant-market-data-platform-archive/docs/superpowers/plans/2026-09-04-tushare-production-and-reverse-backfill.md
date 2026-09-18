# TuShare 生产数据与逆序历史回补实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**目标：**将现有 TuShare 默认数据源改动放入本地运行分支，并增加安全、可恢复的从新到旧历史分钟数据回补计划模式。

**架构：**保持已发布的 TuShare 运行资产和 Guan 旧别名不变。在不可变回补计划中增加明确的日期顺序。逆序模式按降序枚举开放日期和月份片段，执行过程仍然顺序运行并以回执作为检查点。历史下载在完成验证和晋级前只能写入暂存区。

**Tech Stack:** Python, argparse, pytest, Parquet/Arrow, git worktrees.

**Spec:** Existing TuShare minute operational/backfill contracts in `docs/operations/a-share-minutes.md`.

## 全局约束

- Reverse backfill never writes directly to the production alias.
- Default daily operational refresh remains ascending/forward.
- Never expose `TUSHARE_TOKEN_2` or its value in logs or receipts.
- Preserve unrelated dirty hotsector changes.

### 任务一：整合现有默认数据源提交

- Verify worktree state and merge bases.
- Keep MDP local `main` at `914a5a6` and merge `a62fc8e` into the strategy local `main` without touching unrelated dirty files.
- Run focused default-source tests.

### 任务二：增加降序日期顺序

- Add `date_order: Literal["ascending", "descending"]` with ascending compatibility default.
- Persist it in `identity.query.date_order`; reverse selected dates and segments newest-first.
- Add `--date-order` CLI support, tests, and operator documentation.

### 任务三：探测首个历史窗口

- Use the current TuShare minimum `20220715` and local calendar to identify the first earlier open date.
- Build a descending dry-run plan with `writes_production=false`.
- Inspect plan ordering and request bounds before any network download.
