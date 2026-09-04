"""Explicit, parent-owned execution capabilities for plugin work.

Legacy providers still read ContextVars through the long-task adapter. New code
can receive this value directly; neither path can choose a different executor.
Resource admission and durable commits remain responsibilities of the runtime.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, TypeVar

from runtime.long_task_executor import InstanceIdentity, bind_long_task_runtime
from runtime.refresh_contracts import TaskCancelled, TaskContext


Result = TypeVar("Result")


@dataclass(frozen=True)
class PluginExecutionContext:
    task: TaskContext
    identity: InstanceIdentity
    identity_validator: Callable[[InstanceIdentity], bool] | None = None
    parallel_image_runner: Any | None = None

    def checkpoint(self) -> None:
        self.task.raise_if_cancelled()
        if self.identity_validator is not None and not self.identity_validator(self.identity):
            raise TaskCancelled("plugin instance changed during execution")

    @contextmanager
    def activate(self) -> Iterator[None]:
        """Bind the compatibility bridge and always restore the previous context."""
        with bind_long_task_runtime(
            self.task, self.identity, self.identity_validator,
            parallel_image_runner=self.parallel_image_runner,
        ):
            yield

    def run(self, operation: Callable[[], Result]) -> Result:
        """Run one operation, rejecting cancellation or a superseded instance."""
        self.checkpoint()
        with self.activate():
            result = operation()
        self.checkpoint()
        return result
