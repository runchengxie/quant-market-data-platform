from __future__ import annotations

from types import SimpleNamespace

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


@pytest.fixture
def history_disk_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """State-machine fixtures must not inherit the host's free-disk budget."""
    from quant_market_data_platform import minute_history_status

    monkeypatch.setattr(
        minute_history_status.shutil, "disk_usage", lambda _: SimpleNamespace(free=10**15)
    )
