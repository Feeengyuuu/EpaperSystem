from __future__ import annotations

from contextlib import contextmanager
import threading

from .refresh_contracts import TaskContext


class ReentrantPluginLeaseError(RuntimeError):
    """Raised when one thread recursively leases the same plugin singleton."""


class RenderArbiter:
    """Serialize access to shared plugin singletons by canonical plugin ID."""

    POLL_SECONDS = 0.05

    def __init__(self):
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._owners: dict[str, int] = {}

    @contextmanager
    def lease(self, plugin_id: str, context: TaskContext):
        key = self._canonical_plugin_id(plugin_id)
        context.raise_if_cancelled()
        thread_id = threading.get_ident()

        with self._guard:
            if self._owners.get(key) == thread_id:
                raise ReentrantPluginLeaseError(f"plugin lease is not reentrant: {key}")
            plugin_lock = self._locks.setdefault(key, threading.Lock())

        acquired = False
        try:
            while not acquired:
                timeout = min(self.POLL_SECONDS, context.remaining_seconds())
                acquired = plugin_lock.acquire(timeout=max(0.0, timeout))
                if not acquired:
                    context.raise_if_cancelled()

            try:
                context.raise_if_cancelled()
            except BaseException:
                plugin_lock.release()
                acquired = False
                raise

            with self._guard:
                self._owners[key] = thread_id

            try:
                yield
            finally:
                with self._guard:
                    self._owners.pop(key, None)
                plugin_lock.release()
                acquired = False
        finally:
            if acquired:
                plugin_lock.release()

    @staticmethod
    def _canonical_plugin_id(plugin_id: str) -> str:
        if not isinstance(plugin_id, str):
            raise TypeError("plugin_id must be a string")
        key = plugin_id.strip()
        if not key:
            raise ValueError("plugin_id must not be empty")
        return key
