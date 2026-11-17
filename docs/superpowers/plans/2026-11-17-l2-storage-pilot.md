# Complete-day L2 Storage Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and run a complete-day, all-field verified security-selective L2 storage pilot.
**Architecture:** Strict archive intake, global lossless field profiling, atomic bucketed Parquet build, and a manifest-backed selective/full scanner. Run representative days in isolated data directories; retain source archives until acceptance.
**Tech Stack:** Python 3.11+, existing PyArrow/filelock extras, explicitly supplied 7-Zip extractor.
**Spec:** docs/superpowers/specs/2026-11-17-l2-storage-pilot-design.md

## Global Constraints

No source deletion or canonical alias switching by the converter. Preserve every original field, value, duplicate, source row ordinal and instrument; no price/time rescaling. Freeze source/config and verify every member. Bound memory, disk use and writable paths. Real inputs, reports and runtime outputs stay outside Git. Runtime services use immutable releases.

## Review Focus

- Unsafe archive paths, links, unexpected members: reject before extraction.
- Missing/empty/rare values, leading zeros and ID overflow: preserve strings rather than coerce.
- Corrupted output, stale or changed source/configuration: never resume or publish as verified.
- Many securities sharing a bucket, equal timestamps and duplicate rows: route exactly and retain order provenance.
- Interrupted runs and low disk/memory: preserve recoverable work and never delete inputs.

### Task 1: Intake, profiling and proof primitives

Files: `src/quant_market_data_platform/l2_storage/{archive,profile,proof}.py`, `tests/test_l2_storage.py`.
Interfaces: archive inventory/extraction records; profile modalities and per-member logical proofs; safe paths, atomic JSON and SHA helpers.
- [x] Write fixtures covering GB18030, blanks, leading zeros, rare nonnumeric IDs, schema differences, empty files, invalid clocks and unsafe archive members; watch failures before implementation.
- [x] Implement strict intake and bounded all-string CSV parsing; global exact-roundtrip integer decisions and canonical logical fingerprints.
- [x] Run the focused tests and commit.

### Task 2: Versioned builder and query catalog

Files: `src/quant_market_data_platform/l2_storage/{build,reader,__init__,__main__}.py`, tests.
Interfaces: `build(archive, output, scratch, extractor, expected_sha256, ...)`; `scan(root, modality, symbols=None, start=None, stop=None)`; independent `verify(root)`.
- [x] Write failing build/query/verification tests using real fixture files and actual Parquet, including repeated timestamps, shared buckets, unknown destination and corruption.
- [x] Implement per-security row groups, stable routing, exact all-field readback proofs, file hashes, atomic complete-day publication, locks and guarded reruns.
- [x] Add CLI behavior tests and English documentation plus aligned Chinese operations notes.
- [ ] Run focused tests, full repository quality gate and a fresh whole-change review. Fix important findings, then commit/push/PR and merge against current main with required checks.

### Task 3: Real complete-day pilot

Data-only artifacts under the owning project's task-specific data directories.
- [ ] Freeze a stable immutable tool release and archive manifest; run all members of representative complete dates with bounded resources.
- [ ] Independently verify all output proofs and benchmark security/time versus full-day reads, compare size/capacity and record schema/coverage limitations.
- [ ] Record acceptance per day. Retire a corresponding source archive only after source integrity, full-field equality, routing, reader compatibility and dependency checks pass; otherwise retain it and report the remaining condition.
