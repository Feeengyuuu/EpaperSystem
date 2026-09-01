import sys
import threading
import time
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.refresh_contracts import TaskContext, TaskDeadlineExceeded
from runtime.resource_governor import (
    PROVIDER_IO,
    PROVIDER_IO_EXCLUSIVE,
    RuntimeResourceGovernor,
)


def _context(seconds=5):
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + seconds,
    )


def _write_resource_files(root, *, cpu_max, available_mb, swap_total_mb, swap_free_mb):
    proc_root = root / "proc"
    cgroup_root = root / "sys" / "fs" / "cgroup"
    service_group = cgroup_root / "system.slice" / "inkypi.service"
    proc_root.mkdir(parents=True)
    service_group.mkdir(parents=True)
    (proc_root / "self").mkdir()
    (proc_root / "self" / "cgroup").write_text(
        "0::/system.slice/inkypi.service\n",
        encoding="utf-8",
    )
    (proc_root / "meminfo").write_text(
        "\n".join(
            (
                f"MemAvailable: {available_mb * 1024} kB",
                f"SwapTotal: {swap_total_mb * 1024} kB",
                f"SwapFree: {swap_free_mb * 1024} kB",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (service_group / "cpu.max").write_text(cpu_max + "\n", encoding="utf-8")
    return proc_root, cgroup_root


def test_acquire_selects_three_workers_from_cgroup_quota_and_memory_headroom(tmp_path):
    proc_root, cgroup_root = _write_resource_files(
        tmp_path,
        cpu_max="300000 100000",
        available_mb=180,
        swap_total_mb=100,
        swap_free_mb=50,
    )
    governor = RuntimeResourceGovernor(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        cpu_count_provider=lambda: 4,
    )

    with governor.acquire(
        "parallel_image_batch",
        {"max_workers": 3},
        _context(),
    ) as lease:
        assert lease.worker_count == 3
        assert lease.parallel
        assert lease.reason is None
        assert lease.cpu_quota_cores == 3.0
        assert lease.available_mb == 180.0
        assert lease.swap_percent == 50.0


def test_cpu_affinity_caps_effective_worker_tier_below_cgroup_quota(tmp_path):
    proc_root, cgroup_root = _write_resource_files(
        tmp_path,
        cpu_max="300000 100000",
        available_mb=180,
        swap_total_mb=100,
        swap_free_mb=50,
    )
    governor = RuntimeResourceGovernor(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        cpu_count_provider=lambda: 4,
        affinity_count_provider=lambda: 2,
    )

    with governor.acquire(
        "parallel_image_batch",
        {"max_workers": 3},
        _context(),
    ) as lease:
        assert lease.worker_count == 2
        assert lease.cpu_quota_cores == 2.0


def test_unavailable_cpu_affinity_preserves_cpu_count_and_quota_tier(tmp_path):
    proc_root, cgroup_root = _write_resource_files(
        tmp_path,
        cpu_max="300000 100000",
        available_mb=180,
        swap_total_mb=100,
        swap_free_mb=50,
    )
    governor = RuntimeResourceGovernor(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        cpu_count_provider=lambda: 4,
        affinity_count_provider=lambda: None,
    )

    with governor.acquire(
        "parallel_image_batch",
        {"max_workers": 3},
        _context(),
    ) as lease:
        assert lease.worker_count == 3
        assert lease.cpu_quota_cores == 3.0


def test_acquire_selects_two_workers_and_low_resources_fall_back_to_serial():
    two_worker_governor = RuntimeResourceGovernor(
        snapshot_provider=lambda: {
            "available_mb": 160,
            "swap_percent": 64,
            "cpu_quota_cores": 2,
        }
    )
    low_resource_governor = RuntimeResourceGovernor(
        snapshot_provider=lambda: {
            "available_mb": 159,
            "swap_percent": 10,
            "cpu_quota_cores": 4,
        }
    )

    with two_worker_governor.acquire(
        "parallel_image_batch", {"max_workers": 3}, _context()
    ) as lease:
        assert lease.worker_count == 2
        assert lease.parallel
    with low_resource_governor.acquire(
        "parallel_image_batch", {"max_workers": 3}, _context()
    ) as lease:
        assert lease.worker_count == 1
        assert not lease.parallel
        assert lease.reason == "memory_below_parallel_threshold"


def test_only_one_parallel_batch_is_granted_globally_and_release_is_idempotent():
    def high_resource_governor():
        return RuntimeResourceGovernor(
            snapshot_provider=lambda: {
                "available_mb": 200,
                "swap_percent": 0,
                "cpu_quota_cores": 4,
            }
        )

    first = high_resource_governor().acquire(
        "parallel_image_batch", {"max_workers": 3}, _context()
    )
    try:
        second = high_resource_governor().acquire(
            "parallel_image_batch", {"max_workers": 3}, _context()
        )
        assert second.worker_count == 1
        assert second.reason == "parallel_batch_busy"
        second.release()
    finally:
        first.release()
        first.release()

    with high_resource_governor().acquire(
        "parallel_image_batch", {"max_workers": 2}, _context()
    ) as third:
        assert third.worker_count == 2
        assert third.parallel


def test_cgroup_v1_cpu_quota_limits_the_worker_tier(tmp_path):
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "sys" / "fs" / "cgroup"
    service_group = cgroup_root / "cpu" / "inkypi"
    (proc_root / "self").mkdir(parents=True)
    service_group.mkdir(parents=True)
    (proc_root / "self" / "cgroup").write_text(
        "2:cpu,cpuacct:/inkypi\n",
        encoding="utf-8",
    )
    (proc_root / "meminfo").write_text(
        "MemAvailable: 204800 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n",
        encoding="utf-8",
    )
    (service_group / "cpu.cfs_quota_us").write_text("200000\n", encoding="utf-8")
    (service_group / "cpu.cfs_period_us").write_text("100000\n", encoding="utf-8")
    governor = RuntimeResourceGovernor(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        cpu_count_provider=lambda: 4,
    )

    with governor.acquire(
        "parallel_image_batch", {"max_workers": 3}, _context()
    ) as lease:
        assert lease.worker_count == 2
        assert lease.cpu_quota_cores == 2.0


def test_global_singleton_kinds_wait_for_capacity_and_release_it():
    governor = RuntimeResourceGovernor(
        snapshot_provider=lambda: {
            "available_mb": 200,
            "swap_percent": 0,
            "cpu_quota_cores": 4,
        }
    )
    first = governor.acquire("display_write", {}, _context())
    cancel = threading.Event()
    waiting_context = TaskContext(cancel, time.monotonic() + 2)
    outcome = []

    def wait_for_slot():
        outcome.append(governor.acquire("display_write", {}, waiting_context))

    thread = threading.Thread(target=wait_for_slot)
    thread.start()
    time.sleep(0.05)
    assert thread.is_alive()
    first.release()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert outcome[0].kind == "display_write"
    assert outcome[0].acquired_capacity_slot
    outcome[0].release()


def test_provider_io_enforces_total_four_and_one_per_normalized_host():
    governor = RuntimeResourceGovernor(
        snapshot_provider=lambda: {
            "available_mb": 200,
            "swap_percent": 0,
            "cpu_quota_cores": 4,
        }
    )
    leases = [
        governor.acquire("provider_io", {"host": f"https://api{i}.example/path"}, _context())
        for i in range(4)
    ]
    cancel = threading.Event()
    blocked_context = TaskContext(cancel, time.monotonic() + 2)
    outcome = []

    def wait_for_capacity():
        outcome.append(
            governor.acquire(
                "provider_io",
                {"host": "API0.EXAMPLE."},
                blocked_context,
            )
        )

    thread = threading.Thread(target=wait_for_capacity)
    thread.start()
    time.sleep(0.05)
    assert thread.is_alive()
    leases[0].release()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert outcome[0].resource_key == "api0.example"
    outcome[0].release()
    for lease in leases[1:]:
        lease.release()


def test_provider_exclusive_and_parent_provider_io_are_mutually_exclusive():
    governor = RuntimeResourceGovernor()
    provider = governor.acquire(
        PROVIDER_IO,
        {"host": "api.example"},
        _context(),
    )
    exclusive_outcome = []

    exclusive_thread = threading.Thread(
        target=lambda: exclusive_outcome.append(
            governor.acquire(PROVIDER_IO_EXCLUSIVE, {}, _context(2))
        )
    )
    exclusive_thread.start()
    time.sleep(0.05)
    assert exclusive_thread.is_alive()

    provider.release()
    exclusive_thread.join(timeout=1)
    assert not exclusive_thread.is_alive()
    exclusive = exclusive_outcome[0]

    provider_outcome = []
    provider_thread = threading.Thread(
        target=lambda: provider_outcome.append(
            governor.acquire(
                PROVIDER_IO,
                {"host": "other.example"},
                _context(2),
            )
        )
    )
    provider_thread.start()
    time.sleep(0.05)
    assert provider_thread.is_alive()

    exclusive.release()
    provider_thread.join(timeout=1)
    assert not provider_thread.is_alive()
    provider_outcome[0].release()


def test_unknown_resource_kind_and_provider_without_host_are_rejected():
    governor = RuntimeResourceGovernor()
    with pytest.raises(ValueError, match="unsupported"):
        governor.acquire("mystery", {}, _context())
    with pytest.raises(ValueError, match="host"):
        governor.acquire("provider_io", {}, _context())


def test_capacity_wait_honors_deadline_without_leaking_the_slot():
    governor = RuntimeResourceGovernor()
    first = governor.acquire("heavy_child", {}, _context())
    try:
        with pytest.raises(TaskDeadlineExceeded):
            governor.acquire("heavy_child", {}, _context(0.05))
    finally:
        first.release()

    with governor.acquire("heavy_child", {}, _context()) as recovered:
        assert recovered.acquired_capacity_slot


def test_cpu_throttling_snapshot_reads_cgroup_v2_counters(tmp_path):
    proc_root, cgroup_root = _write_resource_files(
        tmp_path,
        cpu_max="200000 100000",
        available_mb=180,
        swap_total_mb=0,
        swap_free_mb=0,
    )
    service_group = cgroup_root / "system.slice" / "inkypi.service"
    (service_group / "cpu.stat").write_text(
        "usage_usec 999\n"
        "nr_periods 120\n"
        "nr_throttled 7\n"
        "throttled_usec 34567\n",
        encoding="utf-8",
    )
    governor = RuntimeResourceGovernor(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
    )

    assert governor.cpu_throttling_snapshot() == {
        "nr_periods": 120,
        "nr_throttled": 7,
        "throttled_usec": 34567,
    }


def test_cpu_throttling_snapshot_normalizes_cgroup_v1_nanoseconds(tmp_path):
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "sys" / "fs" / "cgroup"
    service_group = cgroup_root / "cpu" / "inkypi"
    (proc_root / "self").mkdir(parents=True)
    service_group.mkdir(parents=True)
    (proc_root / "self" / "cgroup").write_text(
        "2:cpu,cpuacct:/inkypi\n",
        encoding="utf-8",
    )
    (service_group / "cpu.stat").write_text(
        "nr_periods 88\n"
        "nr_throttled 4\n"
        "throttled_time 7654321\n",
        encoding="utf-8",
    )
    governor = RuntimeResourceGovernor(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
    )

    assert governor.cpu_throttling_snapshot() == {
        "nr_periods": 88,
        "nr_throttled": 4,
        "throttled_usec": 7654,
    }


def test_cpu_throttling_snapshot_fails_closed_when_cgroup_stat_is_unavailable(tmp_path):
    proc_root = tmp_path / "proc"
    (proc_root / "self").mkdir(parents=True)
    (proc_root / "self" / "cgroup").write_text("0::/missing\n", encoding="utf-8")
    governor = RuntimeResourceGovernor(
        proc_root=proc_root,
        cgroup_root=tmp_path / "sys" / "fs" / "cgroup",
    )

    assert governor.cpu_throttling_snapshot() == {
        "nr_periods": None,
        "nr_throttled": None,
        "throttled_usec": None,
    }
