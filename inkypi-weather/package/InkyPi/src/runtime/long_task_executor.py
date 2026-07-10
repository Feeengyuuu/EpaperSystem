"""Capacity-bounded process isolation for slow or untrusted plugin work."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import importlib
import logging
import math
import multiprocessing
import queue
import threading
import time
from typing import Any, Callable, Mapping
from uuid import uuid4
import weakref

from runtime.refresh_contracts import (
    TaskCancelled,
    TaskContext,
    TaskDeadlineExceeded,
)


logger = logging.getLogger(__name__)

DEFAULT_LONG_TASK_TIMEOUT_SECONDS = 180.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.05
DEFAULT_TERMINATE_GRACE_SECONDS = 0.25
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 32 * 1024 * 1024
MAX_PRIMITIVE_ITEMS = 20_000
MAX_PRIMITIVE_DEPTH = 32

_CURRENT_TASK_CONTEXT: ContextVar[TaskContext | None] = ContextVar(
    "inkypi_long_task_context",
    default=None,
)
_CURRENT_INSTANCE_IDENTITY: ContextVar[InstanceIdentity | None] = ContextVar(
    "inkypi_long_task_identity",
    default=None,
)
_GLOBAL_EXECUTORS: weakref.WeakSet[LongTaskExecutor] = weakref.WeakSet()
_GLOBAL_EXECUTORS_LOCK = threading.Lock()
_STOP = object()


class LongTaskQueueFull(RuntimeError):
    """Raised when the fixed active-plus-queued capacity is exhausted."""


class LongTaskExecutorClosed(RuntimeError):
    """Raised when work is submitted after shutdown begins."""


class LongTaskFailure(RuntimeError):
    """A child-process failure with a safe code and user-facing message."""

    def __init__(self, code: str, public_message: str):
        self.code = str(code or "long_task_failed")[:64]
        self.public_message = _sanitize_message(public_message)
        super().__init__(self.public_message)


@dataclass(frozen=True)
class InstanceIdentity:
    """Immutable identity used to reject ABA/stale plugin results."""

    instance_uuid: str | None
    structural_generation: int | None
    settings_revision: int | None

    def __post_init__(self):
        if self.instance_uuid is not None and not isinstance(self.instance_uuid, str):
            raise TypeError("instance_uuid must be a string or None")
        for name in ("structural_generation", "settings_revision"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise TypeError(f"{name} must be an integer or None")


@dataclass(frozen=True)
class LongTaskResult:
    status: str
    value: Any = None
    error_code: str | None = None
    error: str | None = None


class LongTaskHandle:
    """Thread-safe handle for one queued or running isolated task."""

    def __init__(self, executor: LongTaskExecutor, task_id: str):
        self.id = task_id
        self._executor = executor
        self._done = threading.Event()
        self._cancel_requested = threading.Event()
        self._result: LongTaskResult | None = None
        self._result_lock = threading.Lock()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def cancel(self) -> bool:
        return self._executor.cancel(self)

    def result(self, timeout: float | None = None) -> LongTaskResult:
        if not self._done.wait(timeout):
            raise TimeoutError("isolated task did not finish before the wait timeout")
        with self._result_lock:
            if self._result is None:
                raise RuntimeError("isolated task completed without a result")
            return self._result

    def _request_cancel(self) -> bool:
        if self._done.is_set():
            return False
        self._cancel_requested.set()
        return True

    def _set_result(self, result: LongTaskResult) -> bool:
        with self._result_lock:
            if self._result is not None:
                return False
            self._result = result
            self._done.set()
            return True


@dataclass
class _Job:
    task_name: str
    task: Callable[[Any, Any], Any]
    payload: Any
    context: TaskContext
    identity: InstanceIdentity
    identity_validator: Callable[[InstanceIdentity], bool] | None
    handle: LongTaskHandle
    termination_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class _PrimitiveBudget:
    bytes_remaining: int
    items_remaining: int = MAX_PRIMITIVE_ITEMS

    def consume(self, size: int = 0) -> None:
        self.items_remaining -= 1
        self.bytes_remaining -= max(0, int(size))
        if self.items_remaining < 0 or self.bytes_remaining < 0:
            raise TypeError("primitive payload exceeds the process boundary limit")


def _copy_primitive(value: Any, *, max_bytes: int, _depth: int = 0, _budget=None):
    if _depth > MAX_PRIMITIVE_DEPTH:
        raise TypeError("primitive payload nesting is too deep")
    budget = _budget or _PrimitiveBudget(max_bytes)
    value_type = type(value)
    if value is None or value_type in {bool, int}:
        budget.consume()
        return value
    if value_type is float:
        if not math.isfinite(value):
            raise TypeError("primitive payload floats must be finite")
        budget.consume()
        return value
    if value_type is str:
        budget.consume(len(value.encode("utf-8")))
        return value
    if value_type is bytes:
        budget.consume(len(value))
        return bytes(value)
    if value_type in {list, tuple}:
        budget.consume()
        copied = [
            _copy_primitive(
                item,
                max_bytes=max_bytes,
                _depth=_depth + 1,
                _budget=budget,
            )
            for item in value
        ]
        return copied if value_type is list else tuple(copied)
    if value_type is dict:
        budget.consume()
        copied = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("primitive payload mappings require string keys")
            budget.consume(len(key.encode("utf-8")))
            copied[key] = _copy_primitive(
                item,
                max_bytes=max_bytes,
                _depth=_depth + 1,
                _budget=budget,
            )
        return copied
    raise TypeError(
        f"primitive process payload cannot contain {type(value).__name__}"
    )


def _sanitize_message(value: Any) -> str:
    text = " ".join(str(value or "isolated task failed").split())
    return text[:512] or "isolated task failed"


def _resolve_task(value: Any) -> Callable[[Any, Any], Any]:
    if callable(value):
        return value
    if isinstance(value, str) and ":" in value:
        module_name, attribute_name = value.rsplit(":", 1)
        candidate = getattr(importlib.import_module(module_name), attribute_name)
        if callable(candidate):
            return candidate
    raise TypeError("isolated tasks must be callables or 'module:callable' strings")


def _child_main(task, payload, cancel_event, sender) -> None:
    try:
        value = task(payload, cancel_event)
        value = _copy_primitive(value, max_bytes=MAX_RESULT_BYTES)
        sender.send(("succeeded", value, None, None))
    except LongTaskFailure as error:
        sender.send(("failed", None, error.code, error.public_message))
    except TaskDeadlineExceeded as error:
        sender.send(("abandoned", None, "deadline_expired", _sanitize_message(error)))
    except TaskCancelled as error:
        sender.send(("canceled", None, "task_canceled", _sanitize_message(error)))
    except BaseException as error:
        # Exception text may contain prompts, credentials, or remote response data.
        sender.send(
            (
                "failed",
                None,
                type(error).__name__[:64],
                "isolated task failed",
            )
        )
    finally:
        try:
            sender.close()
        except Exception:
            pass


@contextmanager
def bind_long_task_runtime(
    context: TaskContext,
    instance_identity: InstanceIdentity,
):
    """Expose a refresh context to nested plugin code for this call only."""

    if not isinstance(instance_identity, InstanceIdentity):
        raise TypeError("instance_identity must be an InstanceIdentity")
    context_token = _CURRENT_TASK_CONTEXT.set(context)
    identity_token = _CURRENT_INSTANCE_IDENTITY.set(instance_identity)
    try:
        yield
    finally:
        _CURRENT_INSTANCE_IDENTITY.reset(identity_token)
        _CURRENT_TASK_CONTEXT.reset(context_token)


def current_task_context() -> TaskContext | None:
    return _CURRENT_TASK_CONTEXT.get()


def current_instance_identity() -> InstanceIdentity | None:
    return _CURRENT_INSTANCE_IDENTITY.get()


def task_context_or_default(
    timeout_seconds: float = DEFAULT_LONG_TASK_TIMEOUT_SECONDS,
) -> TaskContext:
    context = current_task_context()
    if context is not None:
        return context
    timeout = max(0.01, min(DEFAULT_LONG_TASK_TIMEOUT_SECONDS, float(timeout_seconds)))
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + timeout,
    )


class LongTaskExecutor:
    """Run at most one child process with a strictly bounded waiting queue."""

    def __init__(
        self,
        tasks: Mapping[str, Any],
        *,
        max_workers: int = 1,
        max_queue: int = 1,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        terminate_grace_seconds: float = DEFAULT_TERMINATE_GRACE_SECONDS,
        start_method: str = "spawn",
        register_global: bool = False,
    ):
        if int(max_workers) != 1:
            raise ValueError("LongTaskExecutor currently requires exactly one worker")
        if isinstance(max_queue, bool) or int(max_queue) < 0:
            raise ValueError("max_queue must be a non-negative integer")
        self._tasks = {
            str(name): _resolve_task(task)
            for name, task in dict(tasks).items()
        }
        if not self._tasks:
            raise ValueError("at least one isolated task must be registered")
        self._max_queue = int(max_queue)
        self._poll_interval = max(0.005, float(poll_interval_seconds))
        self._terminate_grace = max(0.01, float(terminate_grace_seconds))
        self._mp = multiprocessing.get_context(start_method)
        self._slots = threading.BoundedSemaphore(1 + self._max_queue)
        self._queue: queue.Queue[_Job | object] = queue.Queue()
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._active: dict[str, Any] = {}
        self._closed = False
        self._coordinator = threading.Thread(
            target=self._coordinate,
            name="inkypi-long-task-executor",
            daemon=True,
        )
        self._coordinator.start()
        if register_global:
            with _GLOBAL_EXECUTORS_LOCK:
                _GLOBAL_EXECUTORS.add(self)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def active_processes(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(
                sorted(
                    process.pid
                    for process in self._active.values()
                    if process.pid is not None
                )
            )

    def submit(
        self,
        task_name: str,
        payload: Any,
        *,
        context: TaskContext,
        instance_identity: InstanceIdentity,
        identity_validator: Callable[[InstanceIdentity], bool] | None = None,
    ) -> LongTaskHandle:
        task_name = str(task_name)
        task = self._tasks.get(task_name)
        if task is None:
            raise KeyError(f"unknown isolated task: {task_name}")
        if not isinstance(instance_identity, InstanceIdentity):
            raise TypeError("instance_identity must be an InstanceIdentity")
        if identity_validator is not None and not callable(identity_validator):
            raise TypeError("identity_validator must be callable")
        payload = _copy_primitive(payload, max_bytes=MAX_PAYLOAD_BYTES)
        with self._lock:
            if self._closed:
                raise LongTaskExecutorClosed("isolated task executor is closed")
            if not self._slots.acquire(blocking=False):
                raise LongTaskQueueFull("isolated task queue is full")
            task_id = uuid4().hex
            handle = LongTaskHandle(self, task_id)
            job = _Job(
                task_name,
                task,
                payload,
                context,
                instance_identity,
                identity_validator,
                handle,
            )
            self._jobs[task_id] = job
        self._queue.put(job)
        return handle

    def cancel(self, handle_or_id: LongTaskHandle | str) -> bool:
        task_id = (
            handle_or_id.id
            if isinstance(handle_or_id, LongTaskHandle)
            else str(handle_or_id)
        )
        with self._lock:
            job = self._jobs.get(task_id)
        return job.handle._request_cancel() if job is not None else False

    def shutdown(self, *, deadline_monotonic: float) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                jobs = tuple(self._jobs.values())
                self._queue.put(_STOP)
            else:
                jobs = tuple(self._jobs.values())
        for job in jobs:
            job.handle._request_cancel()

        remaining = max(0.0, float(deadline_monotonic) - time.monotonic())
        self._coordinator.join(remaining)
        if self._coordinator.is_alive():
            with self._lock:
                active = [
                    (self._jobs.get(task_id), process)
                    for task_id, process in self._active.items()
                ]
            for job, process in active:
                if job is not None:
                    self._terminate_process(job, process)
            remaining = max(0.0, float(deadline_monotonic) - time.monotonic())
            self._coordinator.join(remaining)
        if self._coordinator.is_alive():
            logger.error("Long-task executor did not stop before its shutdown deadline")

    def _coordinate(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            job = item
            try:
                if job.handle.cancel_requested:
                    result = LongTaskResult(
                        "canceled",
                        error_code="task_canceled",
                        error="isolated task was canceled before it started",
                    )
                else:
                    result = self._run_job(job)
                job.handle._set_result(result)
            except Exception:
                logger.exception("Long-task coordinator failed safely")
                job.handle._set_result(
                    LongTaskResult(
                        "failed",
                        error_code="executor_failed",
                        error="isolated task executor failed",
                    )
                )
            finally:
                with self._lock:
                    self._jobs.pop(job.handle.id, None)
                self._slots.release()

    def _run_job(self, job: _Job) -> LongTaskResult:
        aborted = self._abort_result(job)
        if aborted is not None:
            return aborted

        receiver, sender = self._mp.Pipe(duplex=False)
        cancel_event = self._mp.Event()
        process = self._mp.Process(
            target=_child_main,
            args=(job.task, job.payload, cancel_event, sender),
            name=f"inkypi-long-task-{job.task_name}",
        )
        try:
            process.start()
        except Exception:
            receiver.close()
            sender.close()
            return LongTaskResult(
                "failed",
                error_code="process_start_failed",
                error="isolated task process could not start",
            )
        sender.close()
        with self._lock:
            self._active[job.handle.id] = process

        try:
            while True:
                aborted = self._abort_result(job)
                if aborted is not None:
                    cancel_event.set()
                    self._terminate_process(job, process)
                    return aborted

                if receiver.poll(self._poll_interval):
                    try:
                        message = receiver.recv()
                    except EOFError:
                        message = None
                    result = self._decode_message(message)
                    self._reap_completed_process(job, process, cancel_event)
                    return self._validate_identity(job, result)

                if not process.is_alive():
                    process.join(timeout=0)
                    if receiver.poll(self._poll_interval):
                        try:
                            message = receiver.recv()
                        except EOFError:
                            message = None
                        return self._validate_identity(
                            job,
                            self._decode_message(message),
                        )
                    return LongTaskResult(
                        "failed",
                        error_code="child_exited",
                        error="isolated task process exited without a result",
                    )
        finally:
            receiver.close()
            if process.is_alive():
                self._terminate_process(job, process)
            else:
                process.join(timeout=0)
            with self._lock:
                self._active.pop(job.handle.id, None)
            try:
                process.close()
            except (OSError, ValueError):
                pass

    @staticmethod
    def _decode_message(message) -> LongTaskResult:
        if not isinstance(message, tuple) or len(message) != 4:
            return LongTaskResult(
                "failed",
                error_code="invalid_child_result",
                error="isolated task returned an invalid result",
            )
        status, value, error_code, error = message
        if status not in {"succeeded", "failed", "canceled", "abandoned"}:
            return LongTaskResult(
                "failed",
                error_code="invalid_child_status",
                error="isolated task returned an invalid status",
            )
        return LongTaskResult(status, value, error_code, error)

    @staticmethod
    def _validate_identity(job: _Job, result: LongTaskResult) -> LongTaskResult:
        if result.status != "succeeded" or job.identity_validator is None:
            return result
        try:
            current = bool(job.identity_validator(job.identity))
        except Exception:
            logger.exception("Long-task identity validation failed safely")
            return LongTaskResult(
                "failed",
                error_code="identity_validation_failed",
                error="plugin identity could not be revalidated",
            )
        if current:
            return result
        return LongTaskResult(
            "stale",
            error_code="stale_instance",
            error="plugin instance changed while isolated work was running",
        )

    @staticmethod
    def _abort_result(job: _Job) -> LongTaskResult | None:
        if job.handle.cancel_requested:
            return LongTaskResult(
                "canceled",
                error_code="task_canceled",
                error="isolated task was canceled",
            )
        try:
            job.context.raise_if_cancelled()
        except TaskDeadlineExceeded as error:
            return LongTaskResult(
                "abandoned",
                error_code="deadline_expired",
                error=_sanitize_message(error),
            )
        except TaskCancelled as error:
            return LongTaskResult(
                "canceled",
                error_code="task_canceled",
                error=_sanitize_message(error),
            )
        return None

    def _reap_completed_process(self, job, process, cancel_event) -> None:
        process.join(timeout=self._terminate_grace)
        if process.is_alive():
            cancel_event.set()
            self._terminate_process(job, process)

    def _terminate_process(self, job: _Job, process) -> None:
        with job.termination_lock:
            try:
                alive = process.is_alive()
            except (AssertionError, ValueError):
                return
            if not alive:
                process.join(timeout=0)
                return
            try:
                process.terminate()
            except (OSError, AttributeError):
                pass
            process.join(timeout=self._terminate_grace)
            if process.is_alive():
                try:
                    process.kill()
                except (OSError, AttributeError):
                    pass
                process.join(timeout=self._terminate_grace)


def shutdown_long_task_executors(*, deadline_monotonic: float) -> None:
    with _GLOBAL_EXECUTORS_LOCK:
        executors = tuple(_GLOBAL_EXECUTORS)
    for executor in executors:
        executor.shutdown(deadline_monotonic=deadline_monotonic)
