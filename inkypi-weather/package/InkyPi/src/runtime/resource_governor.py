"""Adaptive, fail-safe admission for bounded runtime work.

The governor deliberately grants no more than one parallel image batch for the
whole process.  Resource uncertainty and contention degrade callers to a
single-worker lease instead of rejecting useful serial work.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import threading
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .refresh_contracts import TaskContext


PARALLEL_IMAGE_BATCH = "parallel_image_batch"
PROVIDER_IO = "provider_io"
# Parent-only gate held for the lifetime of a LongTask child. Spawned children
# have their own provider registry, so excluding parent provider traffic keeps
# machine-wide I/O at the child's bounded 4-total/1-per-host limit.
PROVIDER_IO_EXCLUSIVE = "provider_io_exclusive"
CHROMIUM = "chromium"
AI_GENERATION = "ai_generation"
HEAVY_CHILD = "heavy_child"
DISPLAY_WRITE = "display_write"
_SINGLETON_RESOURCE_KINDS = frozenset(
    {
        CHROMIUM,
        AI_GENERATION,
        HEAVY_CHILD,
        DISPLAY_WRITE,
        PROVIDER_IO_EXCLUSIVE,
    }
)
_SUPPORTED_RESOURCE_KINDS = frozenset(
    {PARALLEL_IMAGE_BATCH, PROVIDER_IO, *_SINGLETON_RESOURCE_KINDS}
)
_GLOBAL_PARALLEL_BATCH_SLOT = threading.BoundedSemaphore(1)


def _available_affinity_count() -> int | None:
    getter = getattr(os, "sched_getaffinity", None)
    if not callable(getter):
        return None
    try:
        count = len(getter(0))
    except (OSError, TypeError, ValueError):
        return None
    return count if count > 0 else None


class _GlobalCapacityRegistry:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active: dict[str, int] = {}
        self._provider_hosts: dict[str, int] = {}

    def acquire(self, kind: str, resource_key: str | None, context: TaskContext) -> None:
        with self._condition:
            while not self._available(kind, resource_key):
                context.raise_if_cancelled()
                remaining = context.remaining_seconds()
                if remaining <= 0:
                    context.raise_if_cancelled()
                self._condition.wait(timeout=min(0.05, remaining))
            context.raise_if_cancelled()
            self._active[kind] = self._active.get(kind, 0) + 1
            if kind == PROVIDER_IO:
                self._provider_hosts[resource_key] = (
                    self._provider_hosts.get(resource_key, 0) + 1
                )

    def release(self, kind: str, resource_key: str | None) -> None:
        with self._condition:
            count = self._active.get(kind, 0)
            if count <= 0:
                raise RuntimeError("resource capacity released without acquisition")
            if count == 1:
                self._active.pop(kind, None)
            else:
                self._active[kind] = count - 1
            if kind == PROVIDER_IO:
                host_count = self._provider_hosts.get(resource_key, 0)
                if host_count <= 0:
                    raise RuntimeError("provider host capacity released without acquisition")
                if host_count == 1:
                    self._provider_hosts.pop(resource_key, None)
                else:
                    self._provider_hosts[resource_key] = host_count - 1
            self._condition.notify_all()

    def _available(self, kind: str, resource_key: str | None) -> bool:
        if kind == PROVIDER_IO:
            return (
                self._active.get(PROVIDER_IO_EXCLUSIVE, 0) == 0
                and
                self._active.get(kind, 0) < 4
                and self._provider_hosts.get(resource_key, 0) < 1
            )
        if kind == PROVIDER_IO_EXCLUSIVE:
            return (
                self._active.get(PROVIDER_IO_EXCLUSIVE, 0) < 1
                and self._active.get(PROVIDER_IO, 0) == 0
            )
        return self._active.get(kind, 0) < 1


_GLOBAL_CAPACITY_REGISTRY = _GlobalCapacityRegistry()


def _coerce_task_context(context: Any) -> TaskContext:
    """Normalize the supported ``src.runtime`` import alias without duck typing."""

    if isinstance(context, TaskContext):
        return context
    context_type = type(context)
    if (
        context_type.__name__ != "TaskContext"
        or not context_type.__module__.endswith("runtime.refresh_contracts")
    ):
        raise TypeError("context must be a TaskContext")
    try:
        cancel_event = context.cancel_event
        deadline_monotonic = float(context.deadline_monotonic)
        clock = context.clock
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise TypeError("context must be a TaskContext") from None
    if not callable(getattr(cancel_event, "is_set", None)) or not callable(clock):
        raise TypeError("context must be a TaskContext")
    return TaskContext(cancel_event, deadline_monotonic, clock)


@dataclass(frozen=True)
class _ResourceSnapshot:
    available_mb: float | None
    swap_percent: float | None
    cpu_quota_cores: float | None


class ResourceLease:
    """One idempotently releasable resource decision."""

    def __init__(
        self,
        *,
        kind: str,
        worker_count: int,
        reason: str | None,
        available_mb: float | None,
        swap_percent: float | None,
        cpu_quota_cores: float | None,
        acquired_parallel_slot: bool,
        acquired_capacity_slot: bool = False,
        resource_key: str | None = None,
        release_callback: Callable[[], None] | None = None,
    ):
        self.kind = str(kind)
        self.worker_count = int(worker_count)
        self.reason = None if reason is None else str(reason)
        self.available_mb = available_mb
        self.swap_percent = swap_percent
        self.cpu_quota_cores = cpu_quota_cores
        self.acquired_parallel_slot = bool(acquired_parallel_slot)
        self.acquired_capacity_slot = bool(acquired_capacity_slot)
        self.resource_key = resource_key
        self._release_callback = release_callback
        self._release_lock = threading.Lock()
        self._released = False

    @property
    def parallel(self) -> bool:
        return self.worker_count > 1 and self.acquired_parallel_slot

    @property
    def released(self) -> bool:
        with self._release_lock:
            return self._released

    def release(self) -> None:
        callback = None
        with self._release_lock:
            if self._released:
                return
            self._released = True
            callback = self._release_callback
            self._release_callback = None
        if callback is not None:
            callback()

    def __enter__(self) -> ResourceLease:
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.release()


class RuntimeResourceGovernor:
    """Select a 1/2/3-worker tier from cgroup CPU and Linux memory state."""

    def __init__(
        self,
        *,
        proc_root: str | os.PathLike[str] = "/proc",
        cgroup_root: str | os.PathLike[str] = "/sys/fs/cgroup",
        cpu_count_provider: Callable[[], int | None] = os.cpu_count,
        affinity_count_provider: Callable[[], int | None] = _available_affinity_count,
        snapshot_provider: Callable[[], Mapping[str, Any]] | None = None,
    ):
        self._proc_root = Path(proc_root)
        self._cgroup_root = Path(cgroup_root)
        self._cpu_count_provider = cpu_count_provider
        self._affinity_count_provider = affinity_count_provider
        self._snapshot_provider = snapshot_provider
        self._last_snapshot = _ResourceSnapshot(None, None, None)
        self._snapshot_lock = threading.Lock()

    def acquire(
        self,
        kind: str,
        claim: Mapping[str, Any] | None,
        context: TaskContext,
    ) -> ResourceLease:
        """Return a parallel lease when safe, otherwise a serial fallback."""

        context = _coerce_task_context(context)
        context.raise_if_cancelled()
        kind = str(kind or "").strip()
        if not kind:
            raise ValueError("kind must be non-empty")
        if kind not in _SUPPORTED_RESOURCE_KINDS:
            raise ValueError(f"unsupported resource kind: {kind}")
        if claim is None:
            claim = {}
        if not isinstance(claim, Mapping):
            raise TypeError("claim must be a mapping")
        if kind != PARALLEL_IMAGE_BATCH:
            resource_key = (
                _normalize_provider_host(claim.get("host"))
                if kind == PROVIDER_IO
                else None
            )
            _GLOBAL_CAPACITY_REGISTRY.acquire(kind, resource_key, context)
            return ResourceLease(
                kind=kind,
                worker_count=1,
                reason=None,
                available_mb=None,
                swap_percent=None,
                cpu_quota_cores=None,
                acquired_parallel_slot=False,
                acquired_capacity_slot=True,
                resource_key=resource_key,
                release_callback=lambda: _GLOBAL_CAPACITY_REGISTRY.release(
                    kind,
                    resource_key,
                ),
            )

        requested = claim.get("max_workers", 3)
        if isinstance(requested, bool) or not isinstance(requested, int):
            raise TypeError("claim max_workers must be an integer")
        if requested < 1 or requested > 3:
            raise ValueError("claim max_workers must be between 1 and 3")

        snapshot = self.sample()
        workers, reason = self._select_worker_count(snapshot, requested)
        acquired = False
        release_callback = None
        if workers > 1:
            acquired = _GLOBAL_PARALLEL_BATCH_SLOT.acquire(blocking=False)
            if acquired:
                release_callback = _GLOBAL_PARALLEL_BATCH_SLOT.release
            else:
                workers = 1
                reason = "parallel_batch_busy"

        return ResourceLease(
            kind=kind,
            worker_count=workers,
            reason=reason,
            available_mb=snapshot.available_mb,
            swap_percent=snapshot.swap_percent,
            cpu_quota_cores=snapshot.cpu_quota_cores,
            acquired_parallel_slot=acquired,
            acquired_capacity_slot=False,
            resource_key=None,
            release_callback=release_callback,
        )

    def sample(self) -> _ResourceSnapshot:
        """Read one coherent admission snapshot from injected or Linux inputs."""

        try:
            if self._snapshot_provider is not None:
                value = self._snapshot_provider()
                snapshot = _ResourceSnapshot(
                    _optional_finite_number(value.get("available_mb")),
                    _optional_percent(value.get("swap_percent")),
                    _optional_positive_number(value.get("cpu_quota_cores")),
                )
            else:
                available_mb, swap_percent = self._read_memory_state()
                snapshot = _ResourceSnapshot(
                    available_mb,
                    swap_percent,
                    self._read_cpu_quota_cores(),
                )
        except Exception:
            snapshot = _ResourceSnapshot(None, None, None)
        with self._snapshot_lock:
            self._last_snapshot = snapshot
        return snapshot

    @property
    def last_snapshot(self) -> Mapping[str, float | None]:
        with self._snapshot_lock:
            snapshot = self._last_snapshot
        return {
            "available_mb": snapshot.available_mb,
            "swap_percent": snapshot.swap_percent,
            "cpu_quota_cores": snapshot.cpu_quota_cores,
        }

    def cpu_throttling_snapshot(self) -> Mapping[str, int | None]:
        """Return normalized leaf-cgroup CPU throttling counters.

        Cgroup v2 exposes microseconds directly.  Cgroup v1 exposes
        ``throttled_time`` in nanoseconds, which is normalized to truncated
        microseconds so health consumers have one stable shape.
        """

        unavailable = {
            "nr_periods": None,
            "nr_throttled": None,
            "throttled_usec": None,
        }
        try:
            relative_v2, relative_v1 = self._read_cpu_cgroup_memberships()
            candidates: list[tuple[Path, bool]] = []
            if relative_v2 is not None:
                candidates.append(
                    (self._cgroup_root / relative_v2 / "cpu.stat", True)
                )
            candidates.append((self._cgroup_root / "cpu.stat", True))
            for root in (
                self._cgroup_root / "cpu",
                self._cgroup_root / "cpu,cpuacct",
            ):
                base = root / relative_v1 if relative_v1 else root
                candidates.append((base / "cpu.stat", False))

            for path, is_v2 in candidates:
                if not path.is_file():
                    continue
                values = _read_nonnegative_integer_fields(path)
                nr_periods = values.get("nr_periods")
                nr_throttled = values.get("nr_throttled")
                throttled = values.get("throttled_usec") if is_v2 else None
                if not is_v2:
                    throttled_time = values.get("throttled_time")
                    throttled = (
                        None
                        if throttled_time is None
                        else throttled_time // 1000
                    )
                return {
                    "nr_periods": nr_periods,
                    "nr_throttled": nr_throttled,
                    "throttled_usec": throttled,
                }
        except (OSError, TypeError, ValueError):
            pass
        return unavailable

    @staticmethod
    def _select_worker_count(
        snapshot: _ResourceSnapshot,
        requested: int,
    ) -> tuple[int, str | None]:
        available = snapshot.available_mb
        swap = snapshot.swap_percent
        quota = snapshot.cpu_quota_cores
        if available is None or swap is None or quota is None:
            return 1, "resource_snapshot_unavailable"
        if requested >= 3 and available >= 170 and swap < 60 and quota >= 3:
            return 3, None
        if requested >= 2 and available >= 150 and swap < 65 and quota >= 2:
            return 2, None
        if requested == 1:
            return 1, "serial_requested"
        if quota < 2:
            return 1, "cpu_quota_below_parallel_threshold"
        if available < 150:
            return 1, "memory_below_parallel_threshold"
        if swap >= 65:
            return 1, "swap_above_parallel_threshold"
        return 1, "parallel_threshold_not_met"

    def _read_memory_state(self) -> tuple[float, float]:
        values: dict[str, float] = {}
        for line in (self._proc_root / "meminfo").read_text(
            encoding="utf-8",
            errors="strict",
        ).splitlines():
            name, separator, raw_value = line.partition(":")
            if not separator:
                continue
            pieces = raw_value.strip().split()
            if pieces:
                values[name] = float(pieces[0])
        available_kb = values["MemAvailable"]
        swap_total_kb = values.get("SwapTotal", 0.0)
        swap_free_kb = values.get("SwapFree", 0.0)
        if available_kb < 0 or swap_total_kb < 0 or swap_free_kb < 0:
            raise ValueError("negative memory metric")
        if swap_total_kb == 0:
            swap_percent = 0.0
        else:
            used_kb = max(0.0, min(swap_total_kb, swap_total_kb - swap_free_kb))
            swap_percent = used_kb * 100.0 / swap_total_kb
        return available_kb / 1024.0, swap_percent

    def _read_cpu_quota_cores(self) -> float:
        fallback = self._cpu_count_provider()
        fallback_cores = float(fallback) if fallback and fallback > 0 else 1.0
        affinity = self._affinity_count_provider()
        if (
            not isinstance(affinity, bool)
            and isinstance(affinity, int)
            and affinity > 0
        ):
            fallback_cores = min(fallback_cores, float(affinity))
        relative_v2, relative_v1 = self._read_cpu_cgroup_memberships()

        v2_candidates = []
        if relative_v2 is not None:
            v2_candidates.append(self._cgroup_root / relative_v2 / "cpu.max")
        v2_candidates.append(self._cgroup_root / "cpu.max")
        for candidate in v2_candidates:
            if not candidate.is_file():
                continue
            quota_text, period_text = candidate.read_text(encoding="utf-8").split()[:2]
            if quota_text == "max":
                return fallback_cores
            quota = float(quota_text)
            period = float(period_text)
            if quota > 0 and period > 0:
                return min(fallback_cores, quota / period)

        v1_roots = (self._cgroup_root / "cpu", self._cgroup_root / "cpu,cpuacct")
        for root in v1_roots:
            base = root / relative_v1 if relative_v1 else root
            quota_path = base / "cpu.cfs_quota_us"
            period_path = base / "cpu.cfs_period_us"
            if not quota_path.is_file() or not period_path.is_file():
                continue
            quota = float(quota_path.read_text(encoding="utf-8").strip())
            period = float(period_path.read_text(encoding="utf-8").strip())
            if quota < 0:
                return fallback_cores
            if quota > 0 and period > 0:
                return min(fallback_cores, quota / period)
        return fallback_cores

    def _read_cpu_cgroup_memberships(self) -> tuple[str | None, str | None]:
        relative_v2 = None
        relative_v1 = None
        cgroup_file = self._proc_root / "self" / "cgroup"
        if cgroup_file.exists():
            for line in cgroup_file.read_text(encoding="utf-8").splitlines():
                hierarchy, controllers, relative = line.split(":", 2)
                if hierarchy == "0" and not controllers:
                    relative_v2 = relative.lstrip("/")
                elif "cpu" in controllers.split(","):
                    relative_v1 = relative.lstrip("/")
        return relative_v2, relative_v1


def _optional_finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _optional_positive_number(value: Any) -> float | None:
    converted = _optional_finite_number(value)
    return converted if converted is not None and converted > 0 else None


def _optional_percent(value: Any) -> float | None:
    converted = _optional_finite_number(value)
    if converted is None or converted < 0 or converted > 100:
        return None
    return converted


def _normalize_provider_host(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("provider_io claim requires a non-empty host")
    raw = value.strip()
    try:
        parsed = urlsplit(raw if "://" in raw else f"//{raw}")
        host = parsed.hostname
    except ValueError as error:
        raise ValueError("provider_io claim host is invalid") from error
    if not host:
        raise ValueError("provider_io claim host is invalid")
    host = host.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("provider_io claim host is invalid") from error
    if not host or any(character.isspace() for character in host):
        raise ValueError("provider_io claim host is invalid")
    return host


def _read_nonnegative_integer_fields(path: Path) -> dict[str, int]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        pieces = line.split()
        if len(pieces) != 2:
            continue
        name, raw_value = pieces
        try:
            value = int(raw_value)
        except ValueError:
            continue
        if value >= 0:
            values[name] = value
    return values
