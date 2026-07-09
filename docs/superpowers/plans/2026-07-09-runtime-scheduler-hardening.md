# Runtime Scheduler Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make refresh scheduling bounded, stoppable, race-safe, and immune to zero-wait loops while preserving current plugin display behavior.

**Architecture:** Add focused runtime contracts, a bounded command queue, lifecycle/backoff state, and a per-plugin render arbiter beside the existing `RefreshTask`. Migrate `RefreshTask` through narrow adapters so the current DailyWiki/SportsDashboard/TechPulse changes remain intact; Playlist commands resolve immutable instance snapshots at execution and revalidate revisions before commit.

**Tech Stack:** Python 3.11, dataclasses, threading, deque/heapq, Flask, pytest, Pillow.

## Global Constraints

- Python 3.11 is the release floor.
- Existing `device.json`, playlist, and plugin settings remain readable; new identity fields have defaults.
- Queue capacity defaults to 32, hard-caps at 128, and reserves 4 manual-display slots.
- Automatic retry delays are 30, 60, 120, then 300 seconds with at most 10% jitter.
- Application shutdown budget is 210 seconds inside systemd's 240-second window; tests inject millisecond budgets.
- No network, render, filesystem, plugin, or hardware work may run while the queue Condition is held.
- Existing uncommitted edits in `src/refresh_task.py` and `tests/test_refresh_task.py` must be retained.
- Every production change starts with a failing test and uses `tools/run_inkypi_tests.ps1`.

---

### Task 1: Define immutable command and job contracts

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/runtime/__init__.py`
- Create: `inkypi-weather/package/InkyPi/src/runtime/refresh_contracts.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_refresh_contracts.py`

**Interfaces:**
- Produces: `LifecycleState`, `CommandKind`, `CommandSource`, `JobStatus`, `RefreshCommand`, `JobRecord`, `QueueSnapshot`, `TaskContext`, `freeze_payload()`.
- Consumes: only Python standard library.

- [ ] **Step 1: Write failing contract tests**

```python
from dataclasses import FrozenInstanceError

import pytest

from src.runtime.refresh_contracts import (
    CommandKind,
    CommandSource,
    JobRecord,
    JobStatus,
    RefreshCommand,
)


def test_refresh_command_is_immutable_and_freezes_nested_payload():
    source = {"settings": {"refreshOnDisplay": "false"}}
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="sports_dashboard",
        payload=source,
        now_monotonic=10.0,
        deadline_monotonic=20.0,
    )
    source["settings"]["refreshOnDisplay"] = "true"
    assert command.payload["settings"]["refreshOnDisplay"] == "false"
    with pytest.raises(FrozenInstanceError):
        command.priority = 0


def test_cancel_requested_is_metadata_not_a_job_status():
    job = JobRecord.from_command(
        RefreshCommand.create(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            plugin_id="weather",
            payload={},
            now_monotonic=1.0,
            deadline_monotonic=2.0,
        ),
        submitted_at=100.0,
    )
    job.mark_running(101.0)
    job.request_cancel(102.0)
    assert job.status is JobStatus.RUNNING
    assert job.cancel_requested_at == 102.0
    with pytest.raises(ValueError):
        job.mark_succeeded(103.0)
```

- [ ] **Step 2: Run the contract tests and verify import failure**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_refresh_contracts.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.runtime'`.

- [ ] **Step 3: Implement the contracts**

```python
# src/runtime/refresh_contracts.py
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4
import threading
import time


class LifecycleState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    QUIESCING = "quiescing"
    DRAINING = "draining"
    STOPPED = "stopped"
    FORCED_EXIT = "forced_exit"


class CommandKind(str, Enum):
    DISPLAY = "display"
    CACHE_REFRESH = "cache_refresh"


class CommandSource(str, Enum):
    MANUAL = "manual"
    SCHEDULER = "scheduler"
    LIVE = "live"
    BACKGROUND = "background"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


def freeze_payload(value: Any) -> Any:
    value = deepcopy(value)
    if isinstance(value, dict):
        return MappingProxyType({key: freeze_payload(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_payload(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze_payload(item) for item in value)
    return value


@dataclass(frozen=True)
class RefreshCommand:
    id: str
    kind: CommandKind
    source: CommandSource
    plugin_id: str
    instance_uuid: str | None
    structural_generation: int | None
    settings_revision: int | None
    force: bool
    priority: int
    enqueued_monotonic: float
    deadline_monotonic: float
    idempotency_key: str | None
    payload: Mapping[str, Any] = field(compare=False, repr=False)

    @classmethod
    def create(cls, *, kind, source, plugin_id, payload, now_monotonic,
               deadline_monotonic, instance_uuid=None, structural_generation=None,
               settings_revision=None, force=False, priority=0,
               idempotency_key=None):
        return cls(
            id=uuid4().hex,
            kind=kind,
            source=source,
            plugin_id=str(plugin_id),
            instance_uuid=instance_uuid,
            structural_generation=structural_generation,
            settings_revision=settings_revision,
            force=bool(force),
            priority=int(priority),
            enqueued_monotonic=float(now_monotonic),
            deadline_monotonic=float(deadline_monotonic),
            idempotency_key=idempotency_key,
            payload=freeze_payload(payload or {}),
        )


@dataclass
class JobRecord:
    id: str
    command_id: str
    status: JobStatus
    submitted_at: float
    started_at: float | None = None
    completed_at: float | None = None
    cancel_requested_at: float | None = None
    superseded_by: str | None = None
    error_code: str | None = None
    error: str | None = None

    @classmethod
    def from_command(cls, command: RefreshCommand, submitted_at: float):
        return cls(command.id, command.id, JobStatus.QUEUED, submitted_at)

    def mark_running(self, when: float) -> None:
        if self.status is not JobStatus.QUEUED:
            raise ValueError("Only queued jobs can start")
        self.status = JobStatus.RUNNING
        self.started_at = when

    def request_cancel(self, when: float) -> None:
        if self.status is JobStatus.RUNNING:
            self.cancel_requested_at = when

    def mark_succeeded(self, when: float) -> None:
        if self.status is not JobStatus.RUNNING or self.cancel_requested_at is not None:
            raise ValueError("Canceled or non-running jobs cannot succeed")
        self.status = JobStatus.SUCCEEDED
        self.completed_at = when


@dataclass(frozen=True)
class QueueSnapshot:
    depth: int
    capacity: int
    rejected_total: int
    superseded_total: int
    accepting: bool


class TaskCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskContext:
    cancel_event: threading.Event
    deadline_monotonic: float
    clock: Any = field(default=time.monotonic, compare=False, repr=False)

    @classmethod
    def never_cancelled(cls, *, deadline_monotonic, clock=time.monotonic):
        return cls(threading.Event(), float(deadline_monotonic), clock)

    def remaining_seconds(self):
        return max(0.0, self.deadline_monotonic - self.clock())

    def raise_if_cancelled(self):
        if self.cancel_event.is_set():
            raise TaskCancelled("task was canceled")
        if self.remaining_seconds() <= 0:
            raise TaskCancelled("task deadline expired")
```

- [ ] **Step 4: Run the contract tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_refresh_contracts.py`

Expected: PASS.

- [ ] **Step 5: Commit the contract module**

```powershell
git add -- inkypi-weather/package/InkyPi/src/runtime/__init__.py inkypi-weather/package/InkyPi/src/runtime/refresh_contracts.py inkypi-weather/package/InkyPi/tests/test_refresh_contracts.py
git commit -m "feat: define refresh runtime contracts"
```

### Task 2: Add stable playlist instance identity and atomic snapshots

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/model.py:66-180`
- Modify: `inkypi-weather/package/InkyPi/src/model.py:393-512`
- Modify: `inkypi-weather/package/InkyPi/tests/test_model.py`

**Interfaces:**
- Consumes: `RefreshCommand.instance_uuid`, `structural_generation`, `settings_revision` from Task 1.
- Produces: `PluginInstanceSnapshot`, `PlaylistManager.snapshot_instance()`, `update_plugin_instance()`, `delete_plugin_instance()`, and UUID-compatible serialization.

- [ ] **Step 1: Add failing UUID, ABA, and snapshot tests**

```python
def test_legacy_plugin_instance_receives_stable_uuid_and_revisions():
    instance = PluginInstance.from_dict({
        "plugin_id": "weather",
        "name": "Home",
        "plugin_settings": {},
        "refresh": {"interval": 300},
    })
    restored = PluginInstance.from_dict(instance.to_dict())
    assert restored.instance_uuid == instance.instance_uuid
    assert restored.structural_generation == 1
    assert restored.settings_revision == 1


def test_delete_and_same_name_recreate_cannot_match_old_snapshot():
    manager = PlaylistManager.from_dict({"playlists": [{
        "name": "Default", "start_time": "00:00", "end_time": "24:00",
        "plugins": [{"plugin_id": "weather", "name": "Home",
                     "plugin_settings": {}, "refresh": {"interval": 300}}],
    }]})
    old = manager.find_plugin("weather", "Home")
    old_snapshot = manager.snapshot_instance(old.instance_uuid)
    removed = manager.delete_plugin_instance(old.instance_uuid)
    manager.add_plugin_to_playlist("Default", {
        "plugin_id": "weather", "name": "Home",
        "plugin_settings": {}, "refresh": {"interval": 300},
    })
    new = manager.find_plugin("weather", "Home")
    assert removed.instance_uuid == old_snapshot.instance_uuid
    assert new.instance_uuid != old_snapshot.instance_uuid
```

- [ ] **Step 2: Run the focused model tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_model.py -k "uuid or snapshot or recreate"`

Expected: FAIL because the identity fields and APIs do not exist.

- [ ] **Step 3: Implement identity and immutable snapshots**

```python
# model.py additions
from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class PluginInstanceSnapshot:
    instance_uuid: str
    plugin_id: str
    name: str
    settings: dict
    refresh: dict
    latest_refresh_time: str | None
    structural_generation: int
    settings_revision: int


class PluginInstance:
    def __init__(self, plugin_id, name, settings=None, refresh=None,
                 latest_refresh_time=None, instance_uuid=None,
                 structural_generation=1, settings_revision=1):
        self.plugin_id = plugin_id
        self.name = name
        self.settings = settings or {}
        self.refresh = refresh or {}
        self.latest_refresh_time = latest_refresh_time
        self.instance_uuid = instance_uuid or uuid4().hex
        self.structural_generation = max(1, int(structural_generation or 1))
        self.settings_revision = max(1, int(settings_revision or 1))

    def snapshot(self) -> PluginInstanceSnapshot:
        return PluginInstanceSnapshot(
            self.instance_uuid,
            self.plugin_id,
            self.name,
            deepcopy(self.settings),
            deepcopy(self.refresh),
            self.latest_refresh_time,
            self.structural_generation,
            self.settings_revision,
        )
```

Implement manager methods under `self._lock`:

```python
def snapshot_instance(self, instance_uuid):
    for playlist in self.playlists:
        for instance in playlist.plugins:
            if instance.instance_uuid == instance_uuid:
                return playlist.name, instance.snapshot()
    return None

def update_plugin_instance(self, instance_uuid, *, settings=None, refresh=None, name=None):
    for playlist in self.playlists:
        for instance in playlist.plugins:
            if instance.instance_uuid != instance_uuid:
                continue
            if settings is not None:
                instance.settings = deepcopy(settings)
            if refresh is not None:
                instance.refresh = deepcopy(refresh)
            if name is not None:
                instance.name = str(name)
            instance.settings_revision += 1
            return instance.snapshot()
    return None

def delete_plugin_instance(self, instance_uuid):
    for playlist in self.playlists:
        for index, instance in enumerate(playlist.plugins):
            if instance.instance_uuid == instance_uuid:
                return playlist.plugins.pop(index).snapshot()
    return None
```

Add `instance_uuid`, `structural_generation`, and `settings_revision` to `to_dict()` and optional reads in `from_dict()`.

- [ ] **Step 4: Run all model tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_model.py`

Expected: PASS, including legacy fixture round-trips.

- [ ] **Step 5: Commit identity and snapshot support**

```powershell
git add -- inkypi-weather/package/InkyPi/src/model.py inkypi-weather/package/InkyPi/tests/test_model.py
git commit -m "feat: add stable playlist instance identity"
```

### Task 3: Implement the bounded coalescing refresh queue

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/runtime/refresh_queue.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_refresh_queue.py`

**Interfaces:**
- Consumes: Task 1 contracts.
- Produces: `RefreshQueue.submit()`, `take()`, `finish()`, `cancel_instance()`, `begin_quiesce()`, `snapshot()`, `QueueFullError`, `QueueStoppingError`.

- [ ] **Step 1: Write failing queue capacity, merge, fairness, and history tests**

```python
def test_display_supersedes_cache_but_cache_never_supersedes_display():
    queue = RefreshQueue(capacity=8, manual_reserved=2, clock=lambda: 10.0)
    cache = command(kind=CommandKind.CACHE_REFRESH, source=CommandSource.BACKGROUND,
                    instance_uuid="one", settings_revision=1)
    display = command(kind=CommandKind.DISPLAY, source=CommandSource.MANUAL,
                      instance_uuid="one", settings_revision=2, force=True)
    cache_job = queue.submit(cache)
    display_job = queue.submit(display)
    assert queue.get_job(cache_job.id).status is JobStatus.SUPERSEDED
    assert queue.take(timeout=0).command.id == display_job.command_id


def test_background_cannot_consume_reserved_manual_slots():
    queue = RefreshQueue(capacity=4, manual_reserved=1, clock=lambda: 1.0)
    for index in range(3):
        queue.submit(command(source=CommandSource.BACKGROUND, instance_uuid=str(index)))
    with pytest.raises(QueueFullError):
        queue.submit(command(source=CommandSource.BACKGROUND, instance_uuid="four"))
    queue.submit(command(source=CommandSource.MANUAL, instance_uuid="manual"))
    assert queue.snapshot().depth == 4
```

- [ ] **Step 2: Run the queue tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_refresh_queue.py`

Expected: FAIL because `RefreshQueue` is absent.

- [ ] **Step 3: Implement queue ownership under one Condition**

```python
class RefreshQueue:
    def __init__(self, capacity=32, manual_reserved=4, terminal_limit=256,
                 terminal_ttl_seconds=1800, clock=time.monotonic,
                 wall_clock=time.time):
        self.capacity = max(1, min(128, int(capacity)))
        self.manual_reserved = max(0, min(self.capacity, int(manual_reserved)))
        self._condition = threading.Condition()
        self._pending = []
        self._jobs = {}
        self._accepting = True
        self._sequence = 0
        self._rejected_total = 0
        self._superseded_total = 0

    def submit(self, command):
        with self._condition:
            if not self._accepting:
                raise QueueStoppingError("refresh service is stopping")
            merged = self._coalesce_locked(command)
            if merged is not None:
                self._condition.notify()
                return merged
            background_limit = self.capacity - self.manual_reserved
            if len(self._pending) >= self.capacity or (
                command.source is not CommandSource.MANUAL
                and self._background_depth_locked() >= background_limit
            ):
                self._rejected_total += 1
                raise QueueFullError("refresh queue is full")
            job = JobRecord.from_command(command, self._wall_clock())
            self._jobs[job.id] = job
            self._sequence += 1
            heapq.heappush(self._pending, (-command.priority, self._sequence, command))
            self._condition.notify()
            return job
```

Complete `_coalesce_locked` with the approved matrix, maintain a `job_id → command` map, implement a three-high/one-low fairness counter in `take()`, and trim only terminal jobs older than TTL or beyond 256 records. Return copies from `get_job()` and `snapshot()`.

- [ ] **Step 4: Run deterministic queue tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_refresh_queue.py`

Expected: PASS with no real sleeps.

- [ ] **Step 5: Commit the queue**

```powershell
git add -- inkypi-weather/package/InkyPi/src/runtime/refresh_queue.py inkypi-weather/package/InkyPi/tests/test_refresh_queue.py
git commit -m "feat: bound and coalesce refresh commands"
```

### Task 4: Add plugin arbitration, lifecycle state, and retry deadlines

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/runtime/render_arbiter.py`
- Create: `inkypi-weather/package/InkyPi/src/runtime/scheduler_state.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_runtime_state.py`

**Interfaces:**
- Produces: `RenderArbiter.lease(plugin_id, context)`, `LifecycleController`, `RetryRegistry.next_delay()`, `RetryRegistry.mark_success()`, `SchedulerSnapshot`.
- Consumes: Task 1 lifecycle enums.

- [ ] **Step 1: Write failing concurrency and fake-clock retry tests**

```python
def test_same_plugin_id_is_serialized_across_business_instances():
    arbiter = RenderArbiter()
    entered = 0
    maximum = 0
    barrier = threading.Barrier(2)

    def render():
        nonlocal entered, maximum
        barrier.wait()
        with arbiter.lease("sports_dashboard", TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 1)):
            entered += 1
            maximum = max(maximum, entered)
            time.sleep(0.02)
            entered -= 1

    threads = [threading.Thread(target=render) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert maximum == 1


def test_retry_registry_uses_bounded_sequence_and_resets():
    retry = RetryRegistry(jitter=lambda delay: delay)
    assert [retry.mark_failure("one", now) for now in (0, 30, 90, 210, 510)] == [30, 60, 120, 300, 300]
    retry.mark_success("one")
    assert retry.mark_failure("one", 1000) == 30
```

- [ ] **Step 2: Run the runtime-state tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_runtime_state.py`

Expected: FAIL because the modules are absent.

- [ ] **Step 3: Implement the focused helpers**

```python
class RenderArbiter:
    def __init__(self):
        self._guard = threading.Lock()
        self._locks = {}

    @contextmanager
    def lease(self, plugin_id, context):
        key = str(plugin_id)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        while not lock.acquire(timeout=min(0.1, context.remaining_seconds())):
            context.raise_if_cancelled()
        try:
            context.raise_if_cancelled()
            yield
        finally:
            lock.release()


class RetryRegistry:
    DELAYS = (30.0, 60.0, 120.0, 300.0)

    def __init__(self, jitter=None):
        self._failures = {}
        self._deadlines = {}
        self._jitter = jitter or self._default_jitter

    def mark_failure(self, key, now):
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        delay = self.DELAYS[min(count - 1, len(self.DELAYS) - 1)]
        delay = self._jitter(delay)
        self._deadlines[key] = now + delay
        return delay

    def mark_success(self, key):
        self._failures.pop(key, None)
        self._deadlines.pop(key, None)
```

`LifecycleController.begin_quiesce()` must be idempotent, set the shared stop event, call `RefreshQueue.begin_quiesce()`, and expose immutable snapshots without blocking on workers.

- [ ] **Step 4: Run the tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_runtime_state.py`

Expected: PASS.

- [ ] **Step 5: Commit runtime helpers**

```powershell
git add -- inkypi-weather/package/InkyPi/src/runtime/render_arbiter.py inkypi-weather/package/InkyPi/src/runtime/scheduler_state.py inkypi-weather/package/InkyPi/tests/test_runtime_state.py
git commit -m "feat: add refresh lifecycle and render arbitration"
```

### Task 5: Refactor RefreshTask to short-lock command execution

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/refresh_task.py:190-605`
- Modify: `inkypi-weather/package/InkyPi/tests/test_refresh_task.py`

**Interfaces:**
- Consumes: Tasks 1-4 contracts, queue, lifecycle, arbiter, retry registry, and Playlist snapshots.
- Produces: existing `start()`, `stop()`, `manual_update()`, `submit_manual_update()`, `get_manual_update_job()`, and `signal_config_change()` APIs with bounded semantics.

- [ ] **Step 1: Add failing zero-loop, lost-wakeup, stale-result, and shared-singleton tests**

```python
def test_overdue_empty_playlist_advances_monotonic_attempt_deadline(monkeypatch):
    clock = FakeClock()
    task = make_task(clock=clock, playlists=[])
    task._run_one_iteration_for_test()
    first = task.scheduler_snapshot().next_attempt_monotonic
    task._run_one_iteration_for_test()
    assert first >= 30.0
    assert task.attempt_count == 1


def test_stop_wakes_waiting_refresh_thread_without_cycle_delay():
    task = make_task(cycle_seconds=300)
    task.start()
    assert task.wait_until_waiting(timeout=1)
    task.stop(join_timeout=1)
    assert not task.thread.is_alive()


def test_deleted_instance_result_is_discarded_after_render(monkeypatch):
    task, manager, render_started, allow_render = make_blocked_playlist_task()
    job = task.submit_playlist_display(manager.first_instance_uuid())
    assert render_started.wait(1)
    manager.delete_plugin_instance(job["instance_uuid"])
    allow_render.set()
    assert task.wait_for_job(job["id"])["status"] == "canceled"
    assert not task.display_manager.calls
```

- [ ] **Step 2: Run the four focused tests and verify old behavior fails**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_refresh_task.py -k "overdue_empty or stop_wakes or deleted_instance or singleton"`

Expected: FAIL on zero-wait, blocking stop, stale commit, or concurrent entry.

- [ ] **Step 3: Wire the runtime collaborators into `RefreshTask.__init__`**

```python
def __init__(self, device_config, display_manager, *, clock=time.monotonic,
             wall_clock=time.time, refresh_queue=None, render_arbiter=None,
             lifecycle=None, retry_registry=None):
    self.device_config = device_config
    self.display_manager = display_manager
    self._clock = clock
    self._wall_clock = wall_clock
    self.stop_event = threading.Event()
    self.refresh_queue = refresh_queue or RefreshQueue(
        capacity=self._config_int("manual_update_queue_capacity", 32, 1, 128),
        manual_reserved=4,
        clock=clock,
        wall_clock=wall_clock,
    )
    self.render_arbiter = render_arbiter or RenderArbiter()
    self.retry_registry = retry_registry or RetryRegistry()
    self.lifecycle = lifecycle or LifecycleController(self.stop_event, self.refresh_queue)
    self._wake_event = threading.Event()
```

Keep compatibility properties for `manual_update_requests`, `manual_update_jobs`, and `running` only while existing tests/callers migrate; all mutations route through `RefreshQueue`.

- [ ] **Step 4: Split waiting, selection, execution, and commit**

Create exact private boundaries:

```python
def _wait_for_work(self) -> RefreshCommand | None: ...
def _select_scheduled_command(self, current_dt) -> RefreshCommand | None: ...
def _execute_command(self, command: RefreshCommand): ...
def _resolve_playlist_command(self, command: RefreshCommand): ...
def _commit_command_result(self, command, resolved_snapshot, image, current_dt): ...
def _record_command_failure(self, command, error): ...
```

`_wait_for_work()` waits on `_wake_event`/queue without holding a lock during execution. `_select_scheduled_command()` always sets a monotonic next-attempt deadline on no playlist, empty playlist, missing plugin, render error, or display error. `_execute_command()` acquires only the `plugin_id` render lease. `_commit_command_result()` re-resolves UUID/generation/revision before cache/display/config side effects.

- [ ] **Step 5: Implement bounded stop semantics**

```python
def stop(self, join_timeout=None):
    if self.lifecycle.state is LifecycleState.STOPPED:
        return True
    self.lifecycle.begin_quiesce(self._wall_clock())
    self.stop_event.set()
    self._wake_event.set()
    if self.thread:
        timeout = 210.0 if join_timeout is None else max(0.0, float(join_timeout))
        self.thread.join(timeout=timeout)
    if self.thread and self.thread.is_alive():
        self.lifecycle.mark_forced_exit(self._wall_clock())
        return False
    self.lifecycle.mark_stopped(self._wall_clock())
    return True
```

Production `inkypi.py` handles a `False` result by finishing registered child-process cleanup and exiting non-zero; unit tests never call `os._exit()`.

- [ ] **Step 6: Run all refresh tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_refresh_task.py`

Expected: PASS, including the existing live-refresh/resource-pressure/theme tests.

- [ ] **Step 7: Commit the scheduler integration without staging unrelated hunks**

Use an explicit patch or interactive staging and verify the staged diff contains only this task:

```powershell
git diff -- inkypi-weather/package/InkyPi/src/refresh_task.py inkypi-weather/package/InkyPi/tests/test_refresh_task.py
git add -p -- inkypi-weather/package/InkyPi/src/refresh_task.py inkypi-weather/package/InkyPi/tests/test_refresh_task.py
git diff --cached --check
git commit -m "fix: make refresh scheduling bounded and stoppable"
```

### Task 6: Route Playlist mutations and Web queue responses through the new contracts

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/blueprints/plugin.py:19-321`
- Modify: `inkypi-weather/package/InkyPi/src/blueprints/playlist.py:20-174`
- Create: `inkypi-weather/package/InkyPi/src/utils/refresh_validation.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_plugin_blueprint.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_playlist_blueprint.py`

**Interfaces:**
- Consumes: `PlaylistManager` atomic APIs and `RefreshQueue` error codes.
- Produces: shared `parse_refresh_config()`, HTTP 429/503 payloads, deletion cancellation, and no direct Playlist mutation.

- [ ] **Step 1: Write failing request validation and backpressure tests**

```python
@pytest.mark.parametrize("interval", ["0", "-1", "abc"])
def test_add_plugin_rejects_invalid_interval(client, interval):
    response = client.post("/add_plugin", data=plugin_form(interval=interval))
    assert response.status_code == 400
    assert response.get_json()["error_code"] == "invalid_refresh_interval"


def test_display_queue_full_returns_retry_after(client, fake_refresh_task):
    fake_refresh_task.submit_manual_update.side_effect = QueueFullError("full")
    response = client.post("/update_now", data={"plugin_id": "weather"})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "5"
    assert response.get_json()["error_code"] == "refresh_queue_full"
```

- [ ] **Step 2: Run blueprint tests and verify failure**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_plugin_blueprint.py tests\test_playlist_blueprint.py`

Expected: FAIL because negative intervals and queue exceptions are not handled.

- [ ] **Step 3: Implement shared refresh validation**

```python
VALID_INTERVAL_UNITS = {"minute", "hour", "day"}

def parse_refresh_config(refresh_settings):
    refresh_type = refresh_settings.get("refreshType")
    if refresh_type == "interval":
        unit = refresh_settings.get("unit")
        if unit not in VALID_INTERVAL_UNITS:
            raise RefreshValidationError("invalid_refresh_unit")
        try:
            value = int(refresh_settings.get("interval"))
        except (TypeError, ValueError) as exc:
            raise RefreshValidationError("invalid_refresh_interval") from exc
        if value <= 0:
            raise RefreshValidationError("invalid_refresh_interval")
        return {"interval": calculate_seconds(value, unit)}
    if refresh_type == "scheduled":
        value = str(refresh_settings.get("refreshTime") or "")
        datetime.strptime(value, "%H:%M")
        return {"scheduled": value}
    raise RefreshValidationError("invalid_refresh_type")
```

On legacy load, `PluginInstance.from_dict()` normalizes non-positive intervals to 60 seconds and logs once; Web writes always reject them.

- [ ] **Step 4: Replace direct mutation and queue response branches**

Resolve the target to `instance_uuid`, call `PlaylistManager.update_plugin_instance()` or `delete_plugin_instance()`, cancel queued work by UUID before cleanup, and route cleanup through `RenderArbiter.lease(plugin_id)`. Map `QueueFullError` to 429 and `QueueStoppingError` to 503 using stable JSON and `Retry-After`.

- [ ] **Step 5: Run blueprint and model tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_plugin_blueprint.py tests\test_playlist_blueprint.py tests\test_model.py`

Expected: PASS.

- [ ] **Step 6: Commit Web and model integration**

```powershell
git add -- inkypi-weather/package/InkyPi/src/blueprints/plugin.py inkypi-weather/package/InkyPi/src/blueprints/playlist.py inkypi-weather/package/InkyPi/src/utils/refresh_validation.py inkypi-weather/package/InkyPi/tests/test_plugin_blueprint.py inkypi-weather/package/InkyPi/tests/test_playlist_blueprint.py
git commit -m "fix: make playlist mutations and refresh admission atomic"
```

### Task 7: Runtime regression gate

**Files:**
- Modify only if a regression test exposes a defect in Tasks 1-6.

**Interfaces:**
- Consumes: all runtime tasks.
- Produces: evidence that current runtime behavior remains green.

- [ ] **Step 1: Run the complete runtime-focused suite**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_refresh_contracts.py tests\test_refresh_queue.py tests\test_runtime_state.py tests\test_model.py tests\test_refresh_task.py tests\test_plugin_blueprint.py tests\test_playlist_blueprint.py tests\test_plugin_registry.py`

Expected: PASS.

- [ ] **Step 2: Run fatal Ruff rules on touched files**

Run: `inkypi-weather\package\InkyPi\.venv\Scripts\python.exe -m ruff check --no-cache --select E9,F63,F7,F82 inkypi-weather\package\InkyPi\src\runtime inkypi-weather\package\InkyPi\src\model.py inkypi-weather\package\InkyPi\src\refresh_task.py inkypi-weather\package\InkyPi\src\blueprints inkypi-weather\package\InkyPi\tests`

Expected: exit 0.

- [ ] **Step 3: Verify no zero-wait probe regression**

Run the deterministic fake-clock test 100 times:

```powershell
1..100 | ForEach-Object { .\tools\run_inkypi_tests.ps1 -q tests\test_refresh_task.py -k overdue_empty_playlist | Out-Null; if ($LASTEXITCODE -ne 0) { throw "iteration $_ failed" } }
```

Expected: all iterations exit 0 without creating uncontrolled threads.

- [ ] **Step 4: Record the runtime gate commit if fixes were required**

```powershell
git status --short
git diff --check
```

If Task 7 required a code correction, stage only that correction and commit it as `test: close refresh runtime regressions`; otherwise create no empty commit.
