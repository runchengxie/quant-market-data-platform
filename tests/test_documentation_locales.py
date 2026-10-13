from __future__ import annotations

import re
from pathlib import Path

import yaml

PAGE_PAIRS = (
    ("architecture/quant-repo-boundaries.en.md", "architecture/quant-repo-boundaries.md"),
    ("data-lifecycle-architecture.md", "data-lifecycle-architecture.zh-CN.md"),
    ("ownership-migration.en.md", "ownership-migration.md"),
    ("research-data-interface.en.md", "research-data-interface.md"),
    ("research-integrity.en.md", "research-integrity.md"),
    ("integrations.en.md", "integrations.md"),
    ("operations.en.md", "operations.md"),
    ("compatibility.en.md", "compatibility.md"),
    ("data-governance.en.md", "data-governance.md"),
    ("quality-governance.en.md", "quality-governance.md"),
    ("operations/credentials.en.md", "operations/credentials.md"),
    ("operations/quantzone.en.md", "operations/quantzone.md"),
    ("operations/backup-and-dev.en.md", "operations/backup-and-dev.md"),
    ("operations/package-publishing.en.md", "operations/package-publishing.md"),
    ("operations/testing.en.md", "operations/testing.md"),
    ("data-warehouse.en.md", "data-warehouse.md"),
    ("documentation-style.en.md", "documentation-style.md"),
    ("research-parquet-io.en.md", "research-parquet-io.md"),
    ("quota-rendering.en.md", "quota-rendering.md"),
    ("l2-ingestion-gate.en.md", "l2-ingestion-gate.md"),
    ("l2-special-event-semantics.en.md", "l2-special-event-semantics.md"),
    ("concepts/historical-industry-labels.en.md", "concepts/historical-industry-labels.md"),
    (
        "migration/current-assets-consumer-audit.en.md",
        "migration/current-assets-consumer-audit.md",
    ),
    (
        "migration/current-assets-migration-status.en.md",
        "migration/current-assets-migration-status.md",
    ),
    (
        "migration/current-assets-publish-rollback-checklist.en.md",
        "migration/current-assets-publish-rollback-checklist.md",
    ),
    ("operations/hk-archive-restore.en.md", "operations/hk-archive-restore.md"),
    ("operations/etf-minutes.en.md", "operations/etf-minutes.md"),
    ("afml-research-features.en.md", "afml-research-features.md"),
    ("a-share-research-profile.en.md", "a-share-research-profile.md"),
    ("operations/context-data.en.md", "operations/context-data.md"),
    (
        "migration/current-assets-closeout-20260918.en.md",
        "migration/current-assets-closeout-20260918.md",
    ),
    (
        "a-share-fund-top10-ownership-features.en.md",
        "a-share-fund-top10-ownership-features.md",
    ),
    (
        "a-share-flow-ownership-features.en.md",
        "a-share-flow-ownership-features.md",
    ),
    ("a-share-fundamentals.en.md", "a-share-fundamentals.md"),
    ("contracts.en.md", "contracts.md"),
    ("operations/a-share-minutes.en.md", "operations/a-share-minutes.md"),
    ("operations/a-share-tushare.en.md", "operations/a-share-tushare.md"),
    ("maintenance.en.md", "maintenance-audit.md"),
)


def test_mkdocs_uses_english_and_translated_pages_link_both_ways() -> None:
    root = Path(__file__).resolve().parents[1]
    english_readme = (root / "README.md").read_text(encoding="utf-8")
    chinese_readme = (root / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "[中文 README](README.zh-CN.md)" in english_readme
    assert "[English README](README.md)" in chinese_readme
    config = yaml.safe_load((root / "mkdocs.yml").read_text(encoding="utf-8"))
    assert config["theme"]["language"] == "en" and "简体中文" in config["nav"][-1]
    assert any("Migration and topics" in group for group in config["nav"])
    assert "Switch to dark mode" in (root / "mkdocs.yml").read_text(encoding="utf-8")
    assert "Switch to light mode" in (root / "mkdocs.yml").read_text(encoding="utf-8")

    for english, chinese in PAGE_PAIRS:
        english_page = (root / "docs" / english).read_text(encoding="utf-8")
        chinese_page = (root / "docs" / chinese).read_text(encoding="utf-8")
        assert f"[中文页面]({Path(chinese).name})" in english_page
        assert f"[English page]({Path(english).name})" in chinese_page


def test_currently_translated_navigation_entries_use_english_pages() -> None:
    root = Path(__file__).resolve().parents[1]
    nav = yaml.safe_load((root / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
    rendered = str(nav[:-1])

    def page_paths(node: object) -> list[str]:
        if isinstance(node, dict):
            return [path for value in node.values() for path in page_paths(value)]
        if isinstance(node, list):
            return [path for value in node for path in page_paths(value)]
        return [node] if isinstance(node, str) and node.endswith(".md") else []

    canonical_page = "data-lifecycle-architecture.md"
    pages = page_paths(nav[:-1])
    assert all(path in {canonical_page, "index.md"} or path.endswith(".en.md") for path in pages)
    for path, _ in PAGE_PAIRS:
        assert path in rendered


def test_maintenance_audit_verification_date_matches_localization() -> None:
    root = Path(__file__).resolve().parents[1]
    english = (root / "docs/maintenance.en.md").read_text(encoding="utf-8")
    chinese = (root / "docs/maintenance-audit.md").read_text(encoding="utf-8")
    english_date = re.search(r"^> - last_verified: (\S+)$", english, re.MULTILINE)
    chinese_date = re.search(r"^> last_verified: (\S+)$", chinese, re.MULTILINE)
    assert english_date is not None
    assert chinese_date is not None
    assert chinese_date.group(1) == english_date.group(1)
