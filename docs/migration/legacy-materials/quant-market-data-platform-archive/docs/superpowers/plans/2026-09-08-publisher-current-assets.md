# Current Asset Publisher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 current 发布链把新数据写入不可变版本文件或目录，并通过稳定入口兼容现有读取程序。

**Architecture:** 刷新阶段先写入带日期的候选输出，质量检查通过后复制到不可变发布路径，再将 current 稳定入口原子切换为相对软链接。current contract 继续记录稳定入口和最终解析路径，后续可以基于版本路径执行回滚。

**Tech Stack:** Python、PyYAML、pytest、Ruff、current contract、TuShare refresh pipeline。

**Spec:** `docs/data-governance.md`

## Global Constraints

- 所有测试优先在本地运行，不依赖 GitHub Actions。
- 发布前拒绝缺少清单、状态未完成或空数据资产。
- 数据发布使用实体版本路径，稳定入口只保留兼容作用。
- 所有改动在独立 worktree 中完成，通过 PR 合并到 `main`。

---

### Task 1: 将股票池文件发布到日期版本路径

**Files:**
- Modify: `src/market_data_platform/tushare_refresh_part01.py`
- Modify: `src/market_data_platform/tushare_refresh_part02.py`
- Test: `tests/test_tushare_a_share.py`

**Interfaces:**
- Consumes: 当前刷新阶段生成的股票池候选文件。
- Produces: 日期版本文件、稳定入口软链接和 current contract 可解析的 manifest。

- [x] 为发布测试增加日期版本文件和稳定入口断言。
- [x] 本地运行测试，确认旧实现无法通过。
- [x] 将发布目标改为日期版本文件，并把稳定入口切换为软链接。
- [x] 运行针对性测试和 Ruff 检查。

### Task 2: 验证发布链路

**Files:**
- Test: `tests/test_tushare_a_share.py`
- Check: current contract、路径审计和本地 pre-push 检查。

**Interfaces:**
- Consumes: Task 1 的日期版本发布结果。
- Produces: 可审计的发布结果，包含版本路径、稳定入口和 manifest。

- [x] 使用临时数据根目录运行发布测试。
- [x] 检查日期版本文件和稳定入口解析结果。
- [x] 检查 current contract 和路径审计输出。
- [x] 创建 PR，合并后删除分支和 worktree。
