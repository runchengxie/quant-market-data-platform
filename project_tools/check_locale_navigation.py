"""Check that built English and Chinese pages have matching sidebars."""

from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"


def primary_navigation(path: str) -> str:
    html = (SITE / path).read_text(encoding="utf-8")
    return html.split("md-sidebar--primary", 1)[1].split("md-sidebar--secondary", 1)[0]


def main() -> None:
    english_html = (SITE / "index.html").read_text(encoding="utf-8")
    chinese_html = (SITE / "index.zh-CN/index.html").read_text(encoding="utf-8")
    english_detail_html = (SITE / "operations/credentials.en/index.html").read_text(
        encoding="utf-8"
    )
    chinese_detail_html = (SITE / "operations/credentials/index.html").read_text(encoding="utf-8")
    archive_english = (SITE / "contracts/board-flow-archive/index.html").read_text(encoding="utf-8")
    archive_chinese = (SITE / "contracts/board-flow-archive.zh-CN/index.html").read_text(
        encoding="utf-8"
    )
    assert '<html lang="en"' in english_html
    assert '<html lang="zh-CN"' in chinese_html
    assert '<html lang="en"' in english_detail_html
    assert '<html lang="zh-CN"' in chinese_detail_html
    assert '<html lang="en"' in archive_english
    assert '<html lang="zh-CN"' in archive_chinese
    english = primary_navigation("index.html")
    chinese = primary_navigation("index.zh-CN/index.html")
    english_page = primary_navigation("operations/credentials.en/index.html")
    chinese_page = primary_navigation("operations/credentials/index.html")
    dataset_page = primary_navigation("knowledge/datasets/a-share-daily-clean/index.html")
    archive_english_page = primary_navigation("contracts/board-flow-archive/index.html")
    archive_chinese_page = primary_navigation("contracts/board-flow-archive.zh-CN/index.html")

    for navigation in (english, english_page, dataset_page, archive_english_page):
        assert "Data contracts" in navigation
        assert "简体中文" not in navigation
        assert "共享数据契约" not in navigation
    for navigation in (chinese, chinese_page, archive_chinese_page):
        assert "数据契约" in navigation
        assert "Data contracts" not in navigation
        assert "Research data interface" not in navigation


if __name__ == "__main__":
    main()
