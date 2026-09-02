"""Real spawn/reap checks: blocked DNS/read has the same cancellation boundary."""
import time
import threading

import pytest

from plugins.box_office_top_movies.china_http import IsolatedChinaHttpClient
from plugins.box_office_top_movies.china_source import ChinaFetchBudget
from runtime.long_task_executor import LongTaskExecutor
from runtime.refresh_contracts import TaskContext, TaskCancelled


def blocked_get(_payload, _cancel_event):
    time.sleep(60)


def completed_get(payload, _cancel_event):
    return {"status": 200, "body": b"chart data", "headers": {}, "url": payload["url"]}


def test_killable_http_reaps_blocked_child_within_shared_deadline():
    executor = LongTaskExecutor({"china_http": blocked_get}, max_queue=0)
    client = IsolatedChinaHttpClient(executor=executor)
    started = time.monotonic()
    parent = TaskContext.never_cancelled(deadline_monotonic=started + 4)
    with pytest.raises(RuntimeError, match="source budget expired"):
        with ChinaFetchBudget(client=client, parent=parent) as budget:
            budget.get("https://example.test/blocked")
    elapsed = time.monotonic() - started
    assert elapsed < 4
    assert executor.closed
    assert executor.active_processes == ()


def test_killable_http_returns_data_and_closes_executor():
    executor = LongTaskExecutor({"china_http": completed_get}, max_queue=0)
    client = IsolatedChinaHttpClient(executor=executor)
    parent = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 8)
    with ChinaFetchBudget(client=client, parent=parent) as budget:
        assert budget.get("https://example.test/chart").text == "chart data"
    assert executor.closed
    assert executor.active_processes == ()


def test_parent_cancellation_reaps_http_child_without_becoming_cache_success():
    executor = LongTaskExecutor({"china_http": blocked_get}, max_queue=0)
    client = IsolatedChinaHttpClient(executor=executor)
    parent = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 8)
    timer = threading.Timer(0.4, parent.cancel_event.set)
    timer.start()
    try:
        with pytest.raises(TaskCancelled):
            with ChinaFetchBudget(client=client, parent=parent) as budget:
                budget.get("https://example.test/blocked")
    finally:
        timer.cancel()
        executor.shutdown(deadline_monotonic=time.monotonic() + 1)
    assert executor.active_processes == ()


def test_delayed_coordinator_fallback_cleans_up_once_before_budget(monkeypatch):
    # Model a coordinator that fails to notice cancellation/deadline. The
    # caller must force reaping, including on context-manager exit.
    executor = LongTaskExecutor(
        {"china_http": blocked_get}, max_queue=0, terminate_grace_seconds=0.1,
    )
    monkeypatch.setattr(executor, "_abort_result", lambda _job: None)
    real_shutdown = executor.shutdown
    shutdown_calls = []

    def counted_shutdown(**kwargs):
        shutdown_calls.append(kwargs["deadline_monotonic"])
        real_shutdown(**kwargs)

    monkeypatch.setattr(executor, "shutdown", counted_shutdown)
    client = IsolatedChinaHttpClient(executor=executor)
    started = time.monotonic()
    parent = TaskContext.never_cancelled(deadline_monotonic=started + 4)
    with pytest.raises(RuntimeError, match="source budget expired"):
        with ChinaFetchBudget(client=client, parent=parent) as budget:
            budget.get("https://example.test/blocked")
    assert time.monotonic() - started < 4
    assert len(shutdown_calls) == 1
    # A dead child can still be in coordinator bookkeeping for one poll.
    while executor.active_processes and time.monotonic() < started + 4:
        time.sleep(0.01)
    assert executor.active_processes == ()
