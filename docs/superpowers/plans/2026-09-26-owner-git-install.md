# Owner Git Install Bridge Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task.

## Goal

Let downstream repositories install the data owner from an immutable merged commit while the package registry is unavailable.

## Architecture

Keep the existing registry release process. Add a documented Git source path pinned to a full owner commit SHA, then transition downstream repositories to the registry after its first verified release.

## Tech Stack

Python packaging, `uv`, GitHub Git source dependencies, Markdown.

## Spec

`docs/operations/package-publishing.md`

## Global Constraints

- Depend only on merged owner commits identified by full SHA.
- Do not introduce a floating branch, local checkout, package release, or publish credential requirement.
- Select package extras according to the downstream imports and runtime dependencies.

## Review Focus

- Git dependency resolution: verify the example uses the full merged commit SHA and compatible package version.
- Optional dependencies: verify the documented extras exist in `pyproject.toml`.
- Cutover: preserve the existing registry path as the later migration target.

## Task 1: Document immutable Git installation

**Files:**
- Modify: `docs/operations/package-publishing.md`
- Test: documentation link and command checks, plus package build

- [x] Add a Git source example using `market-data-platform[research-features,duckdb]>=0.2.0` and a full merged SHA placeholder.
- [x] Check `research-features` and `duckdb` against `pyproject.toml`; run the documentation governance test and `git diff --check`.
- [x] Run `uv build --clear`; wheel and source distribution built successfully. Import the candidate pool API from the built wheel. No package was published.
- [ ] Open a Draft PR that records the validation actually completed.
