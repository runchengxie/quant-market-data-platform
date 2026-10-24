# Offline US equities layout implementation plan

> Execution: superpowers:executing-plans; implementation and review stay separate.

**Goal:** preserve the current full run and bring its lossless by-security converter into the data owner's source repository.

**Architecture:** an optional offline Python module accepts an already audited by-day dataset and explicit identity-evidence directory. Separate integrity/storage, identity/path encoding, staging, final publication and CLI modules. No changes to market registry, current asset aliases or the running frozen release. No real data or machine paths in Git.

**Tech stack:** Python 3.11+, optional PyArrow/DuckDB/psutil, synthetic pytest fixtures.

**Spec:** [offline operational contract](../../offline-equities.en.md).

Tasks:
- [x] Add regression tests for exact case-sensitive symbols on case-insensitive filesystems, original-field equality, identities, resume, interrupted publication, changed inputs and ordering failures. Observe failure before implementation.
- [x] Port the frozen algorithm into focused modules, require explicit paths, use reversible percent encoding for lowercase ASCII in symbol directories, and fingerprint all implementation modules for checkpoint compatibility.
- [x] Run targeted and repository checks; document the offline entry point, dependencies, existing-run separation and limitations.
- [ ] Review and integrate the PR using workspace standing authorization; retain the original running release for audit.
- [ ] Independently accept the completed real dataset: source/index/checkpoint/footer row counts and schemas, compression, symbol coverage, case collisions, catalog queries and selected file hashes. Write receipts and refresh local status docs. Preserve intermediate files.

Validation: synthetic fixture only in repository; full real-data receipts remain beside the dataset. Explicitly distinguish all-file metadata checks, prior pipeline full readback proofs and independently sampled payload checks. No claim of a new all-row EXCEPT check.

Review ledger: independent review identified stale completion after failed validation, decoded recovery labels, missing requested dates and incomplete source manifests. Added failing regressions before fixes, then re-ran the full targeted suite (15 passed). DuckDB version is included in the implementation fingerprint. The reviewer cleared those corrections; exact-symbol selection is also covered.

Local verification: repository-wide Ruff check/format, Linux-target ty, public snapshot audit and structural ratchet pass. Real-data acceptance is a separate external receipt; the source PR does not claim the real v2 run or completion of the still-running frozen v1.
