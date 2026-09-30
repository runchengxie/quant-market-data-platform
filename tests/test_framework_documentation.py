from pathlib import Path


def test_qlib_docs_describe_conditional_dataloader_support() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    readme_zh = Path("README.zh-CN.md").read_text(encoding="utf-8")
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    docs_index = (Path("docs") / "README.md").read_text(encoding="utf-8")
    integrations = (Path("docs") / "integrations.en.md").read_text(encoding="utf-8")
    integrations_zh = (Path("docs") / "integrations.md").read_text(encoding="utf-8")
    testing = (Path("docs") / "operations" / "testing.en.md").read_text(encoding="utf-8")
    testing_zh = (Path("docs") / "operations" / "testing.md").read_text(encoding="utf-8")
    ownership = (Path("docs") / "ownership-migration.md").read_text(encoding="utf-8")

    assert "The current Qlib integration provides a read-only DataLoader adapter" in readme
    assert "Standard development checks install only the `dev` extra" in readme
    assert "当前 Qlib 接入只提供已发布数据资产的只读 DataLoader 适配器" in readme_zh
    assert "常规开发门禁只安装 `dev` extra" in readme_zh
    assert (
        "The optional adapter maps published Parquet assets into a Qlib DataLoader" in integrations
    )
    assert "当前实现只把已发布的 Parquet 资产映射为 Qlib DataLoader" in integrations_zh
    assert "The standard `dev` gate does not install `pyqlib`" in testing
    assert "标准 `dev` 门禁不安装 `pyqlib`" in testing_zh
    assert "--extra dev --extra qlib" in agents
    assert "ownership-migration.md" in docs_index
    assert "etf-minutes.md" in docs_index
    assert "# DailyWatch20 数据归属" in ownership
    assert "Ownership migration" not in ownership
