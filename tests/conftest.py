from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_platform_path_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DATA_PLATFORM_ROOT",
        "DATA_PLATFORM_METADATA_DB_PATH",
        "DATA_PLATFORM_WAREHOUSE_DB_PATH",
        "DATA_PLATFORM_CONFIG",
        "DATA_PLATFORM_CONFIG_LOADED",
    ):
        monkeypatch.delenv(name, raising=False)
