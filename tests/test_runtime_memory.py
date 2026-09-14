from __future__ import annotations

from market_data_platform import runtime_memory
from market_data_platform.runtime_memory import MemorySnapshot, choose_memory_budget_mb


def test_auto_budget_for_32gb_machine_respects_available_memory() -> None:
    snapshot = MemorySnapshot(total_mb=32 * 1024, available_mb=24 * 1024, rss_mb=512)

    budget = choose_memory_budget_mb("auto", snapshot=snapshot)

    assert budget == 15_872
    assert snapshot.total_mb is not None
    assert snapshot.available_mb is not None
    assert budget <= snapshot.total_mb * 0.5
    assert budget <= snapshot.available_mb * 0.65
    assert budget % 256 == 0


def test_auto_budget_for_8gb_machine_keeps_a_conservative_reserve() -> None:
    snapshot = MemorySnapshot(total_mb=8 * 1024, available_mb=6 * 1024, rss_mb=256)

    budget = choose_memory_budget_mb("auto", snapshot=snapshot)

    assert budget == 3_840
    assert snapshot.available_mb is not None
    assert snapshot.available_mb - budget >= 2 * 1024


def test_auto_budget_falls_back_to_2gb_without_telemetry() -> None:
    snapshot = MemorySnapshot(total_mb=None, available_mb=None, rss_mb=None)

    assert choose_memory_budget_mb("auto", snapshot=snapshot) == 2 * 1024


def test_auto_budget_uses_minimum_when_no_memory_is_available() -> None:
    snapshot = MemorySnapshot(total_mb=8 * 1024, available_mb=0, rss_mb=256)

    assert choose_memory_budget_mb("auto", snapshot=snapshot) == 1


def test_explicit_budget_has_priority_over_low_available_memory() -> None:
    snapshot = MemorySnapshot(total_mb=32 * 1024, available_mb=1024, rss_mb=512)

    assert choose_memory_budget_mb("8GB", snapshot=snapshot) == 8 * 1024
    assert choose_memory_budget_mb("1536MiB", snapshot=snapshot) == 1536
    assert choose_memory_budget_mb("8192", snapshot=snapshot) == 8192


def test_memory_snapshot_total_is_backward_compatible() -> None:
    snapshot = MemorySnapshot(available_mb=2048, rss_mb=100)

    assert snapshot.total_mb is None
    assert snapshot.to_dict() == {
        "available_mb": 2048,
        "rss_mb": 100,
        "total_mb": None,
    }


def test_meminfo_reader_collects_total_and_available_together(tmp_path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       32768000 kB\nMemFree: 100 kB\nMemAvailable: 24576000 kB\n",
        encoding="utf-8",
    )

    total, available = runtime_memory._read_meminfo_bytes(meminfo)

    assert total == 32_768_000 * 1024
    assert available == 24_576_000 * 1024


def test_memory_snapshot_uses_cgroup_v2_limit_and_headroom(tmp_path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal: 33554432 kB\nMemAvailable: 25165824 kB\n",
        encoding="utf-8",
    )
    proc_cgroup = tmp_path / "cgroup"
    proc_cgroup.write_text("0::/workload/child\n", encoding="utf-8")
    cgroup_root = tmp_path / "cgroup-v2"
    child = cgroup_root / "workload" / "child"
    child.mkdir(parents=True)
    (child / "memory.max").write_text("max\n", encoding="utf-8")
    (child / "memory.current").write_text(str(1024**3), encoding="utf-8")
    parent = cgroup_root / "workload"
    (parent / "memory.max").write_text(str(8 * 1024**3), encoding="utf-8")
    (parent / "memory.current").write_text(str(2 * 1024**3), encoding="utf-8")

    snapshot = runtime_memory.read_memory_snapshot(
        meminfo_path=meminfo,
        process_status_path=tmp_path / "missing-status",
        proc_cgroup_path=proc_cgroup,
        cgroup_v2_root=cgroup_root,
        cgroup_v1_memory_root=tmp_path / "missing-v1",
    )

    assert snapshot.total_mb == 8 * 1024
    assert snapshot.available_mb == 6 * 1024
    assert snapshot.system_total_mb == 32 * 1024
    assert snapshot.system_available_mb == 24 * 1024
    assert snapshot.cgroup_limit_mb == 8 * 1024
    assert snapshot.cgroup_available_mb == 6 * 1024
    assert choose_memory_budget_mb("auto", snapshot=snapshot) == 3840


def test_cgroup_v1_memory_limit_is_supported(tmp_path) -> None:
    proc_cgroup = tmp_path / "cgroup"
    proc_cgroup.write_text("7:cpu:/job\n5:memory:/job\n", encoding="utf-8")
    cgroup_root = tmp_path / "cgroup-v1-memory"
    job = cgroup_root / "job"
    job.mkdir(parents=True)
    (job / "memory.limit_in_bytes").write_text(str(4 * 1024**3), encoding="utf-8")
    (job / "memory.usage_in_bytes").write_text(str(1024**3), encoding="utf-8")

    limit, available = runtime_memory._read_cgroup_memory_bytes(
        proc_cgroup_path=proc_cgroup,
        cgroup_v2_root=tmp_path / "missing-v2",
        cgroup_v1_memory_root=cgroup_root,
    )

    assert limit == 4 * 1024**3
    assert available == 3 * 1024**3
