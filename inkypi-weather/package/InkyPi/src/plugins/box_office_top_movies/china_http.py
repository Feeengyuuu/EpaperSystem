"""Killable HTTP requests under the existing single-heavy-child governor."""
import logging
import time

import requests

from runtime.long_task_executor import (
    InstanceIdentity, LongTaskExecutor, current_instance_identity,
    current_instance_identity_validator,
)
from runtime.refresh_contracts import TaskContext, TaskCancelled, TaskDeadlineExceeded
from utils.http_client import create_single_attempt_http_client, HttpClientError, HttpResult, HttpStatusError


REAP_RESERVE_SECONDS = 2.0
logger = logging.getLogger(__name__)


def fetch_in_child(payload, cancel_event):
    """No rendering or writes: return bounded bytes and safe error categories."""
    context = TaskContext(cancel_event, payload["deadline"])
    try:
        with create_single_attempt_http_client() as client:
            result = client.request_bytes(
                "GET", payload["url"], context=context, max_bytes=payload["max_bytes"],
                timeout=payload["timeout"], **payload["kwargs"],
            )
            return {"status": result.status, "body": result.data, "headers": dict(result.headers), "url": result.url}
    except HttpStatusError as error:
        return {"http_error": error.status}
    except HttpClientError as error:
        return {"transport_error": isinstance(error.__cause__, (requests.ConnectionError, requests.Timeout))}
    except (requests.ConnectionError, requests.Timeout):
        return {"transport_error": True}


class IsolatedChinaHttpClient:
    def __init__(self, *, executor=None):
        self.executor = executor
        self.deadline = None
        self._closed = False

    def request_bytes(self, method, url, *, context, timeout, max_bytes, **kwargs):
        started = time.monotonic()
        status = "error"
        try:
            result = self._request_bytes(method, url, context=context, timeout=timeout, max_bytes=max_bytes, **kwargs)
            status = "succeeded"
            return result
        except TaskDeadlineExceeded:
            status = "deadline"
            raise
        except TaskCancelled:
            status = "canceled"
            raise
        except HttpStatusError as error:
            status = "http_" + str(error.status)
            raise
        finally:
            logger.info(
                "China HTTP completed. | status=%s elapsed_seconds=%.3f budget_remaining_seconds=%.3f",
                status, time.monotonic() - started, context.remaining_seconds(),
            )

    def _request_bytes(self, method, url, *, context, timeout, max_bytes, **kwargs):
        if method != "GET":
            raise ValueError("China source transport permits only GET")
        if self._closed:
            raise HttpClientError("China source transport is closed")
        context.raise_if_cancelled()
        self.deadline = context.deadline_monotonic
        child_context = TaskContext(context.cancel_event, self.deadline - REAP_RESERVE_SECONDS, context.clock)
        child_context.raise_if_cancelled()
        if self.executor is None:
            # Every executor uses the same HEAVY_CHILD and provider-exclusive
            # governor. This does not add another global worker slot.
            self.executor = LongTaskExecutor(
                {"china_http": fetch_in_child}, max_queue=0, register_global=True,
                terminate_grace_seconds=0.1,
            )
        handle = self.executor.submit(
            "china_http", {"url": url, "timeout": timeout, "max_bytes": max_bytes,
                           "kwargs": kwargs, "deadline": child_context.deadline_monotonic},
            context=child_context,
            instance_identity=current_instance_identity() or InstanceIdentity(None, None, None),
            identity_validator=current_instance_identity_validator(),
        )
        try:
            # Leave a second for forced cancellation/reaping if the coordinator
            # itself is delayed. The entire refresh still owns one deadline.
            result = handle.result(timeout=max(0.001, context.remaining_seconds() - 1))
        except TimeoutError as error:
            handle.cancel()
            self.close()
            raise TaskDeadlineExceeded("China HTTP worker exceeded its deadline") from error
        if result.status != "succeeded":
            context.raise_if_cancelled()
            if result.status in {"abandoned", "canceled"}:
                raise TaskDeadlineExceeded("China HTTP worker exhausted its budget")
            if result.status == "stale":
                raise TaskCancelled("China HTTP instance changed")
            raise HttpClientError("China HTTP worker failed: " + str(result.error_code))
        value = result.value
        if "http_error" in value:
            raise HttpStatusError("GET", url, value["http_error"])
        if "transport_error" in value:
            error = HttpClientError("China HTTP transport failed")
            if value["transport_error"]:
                raise error from requests.ConnectionError("temporary transport failure")
            raise error
        context.raise_if_cancelled()
        return HttpResult(value["status"], value["body"], value["headers"], value["url"])

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.executor is not None:
            # shutdown may force TERM/KILL after its coordinator join deadline.
            # Reserve 0.75s for both contenders' 0.1s termination phases and
            # reaping. Do this once, including when __exit__ follows a timeout.
            deadline = time.monotonic() if self.deadline is None else self.deadline - 0.75
            self.executor.shutdown(deadline_monotonic=deadline)
