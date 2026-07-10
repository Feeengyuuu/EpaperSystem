"""Shared, deadline-aware HTTP transport with bounded response ownership."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import json as json_module
import logging
import operator
import os
from pathlib import Path
import tempfile
import threading
import time
from types import MappingProxyType
from typing import Generic, Mapping, Optional, TypeVar
from urllib.parse import urlparse, urlsplit, urlunsplit

import requests
from urllib3.util.retry import Retry

from runtime.refresh_contracts import TaskContext, TaskDeadlineExceeded
from utils.atomic_file import fsync_directory


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_JSON_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_RESPONSE_MAX_BYTES = 25 * 1024 * 1024
RETRYABLE_METHODS = frozenset({"GET", "HEAD"})
RETRYABLE_STATUSES = {429, 502, 503, 504}
_CHUNK_BYTES = 64 * 1024


class HttpClientError(RuntimeError):
    """Base class for bounded transport failures."""


class HttpStatusError(HttpClientError):
    def __init__(self, method, url, status):
        self.method = str(method).upper()
        self.url = _safe_url(url)
        self.status = int(status)
        super().__init__(f"{self.method} {self.url} returned HTTP {self.status}")


class ResponseTooLarge(HttpClientError):
    def __init__(self, max_bytes):
        self.max_bytes = int(max_bytes)
        super().__init__(f"HTTP response exceeded {self.max_bytes} bytes")


class HttpDecodeError(HttpClientError):
    pass


T = TypeVar("T")


@dataclass(frozen=True)
class HttpResult(Generic[T]):
    status: int
    data: T
    headers: Mapping[str, str]
    url: str


class TimeoutSession(requests.Session):
    """Compatibility Session that always applies a finite timeout."""

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT_SECONDS)
        return super().request(method, url, **kwargs)


class DeadlineRetry(Retry):
    """urllib3 retry policy whose waits cannot exceed the active TaskContext."""

    _state = threading.local()

    @classmethod
    @contextmanager
    def for_context(cls, context):
        previous = getattr(cls._state, "context", None)
        cls._state.context = context
        try:
            yield
        finally:
            cls._state.context = previous

    def sleep(self, response=None):
        delay = self.get_retry_after(response) if response is not None else None
        if delay is None:
            delay = self.get_backoff_time()
        if not delay or delay <= 0:
            return
        context = getattr(self._state, "context", None)
        if context is None:
            time.sleep(delay)
            return
        remaining = context.remaining_seconds()
        if delay >= remaining:
            raise TaskDeadlineExceeded("HTTP retry delay exceeds task deadline")
        if context.cancel_event.wait(delay):
            context.raise_if_cancelled()


def _retry_policy():
    return DeadlineRetry(
        total=1,
        connect=1,
        read=0,
        status=1,
        allowed_methods=RETRYABLE_METHODS,
        status_forcelist=RETRYABLE_STATUSES,
        respect_retry_after_header=True,
        backoff_factor=0.5,
        raise_on_status=False,
    )


_HTTP_SESSION: Optional[requests.Session] = None
_HTTP_CLIENT = None
_SESSION_LOCK = threading.RLock()
_PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_DEAD_LOCAL_PROXY_HOSTS = {"127.0.0.1", "localhost", "::1"}
_DEAD_LOCAL_PROXY_PORTS = {0, 9}


def _safe_url(value):
    parsed = urlsplit(str(value or ""))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _is_dead_local_proxy(value: str) -> bool:
    proxy = str(value or "").strip()
    if not proxy:
        return False
    candidate = proxy if "://" in proxy else f"http://{proxy}"
    try:
        parsed = urlparse(candidate)
        port = parsed.port
    except ValueError:
        return False
    host = str(parsed.hostname or "").strip().lower()
    return host in _DEAD_LOCAL_PROXY_HOSTS and port in _DEAD_LOCAL_PROXY_PORTS


def sanitize_dead_local_proxy_environment() -> dict[str, str]:
    """Remove known-dead local proxy variables from the process environment."""

    removed = {}
    for name in _PROXY_ENV_NAMES:
        value = os.environ.get(name)
        if _is_dead_local_proxy(value):
            removed[name] = value
            os.environ.pop(name, None)
    if removed:
        logger.warning(
            "Removed dead local proxy environment variables: %s",
            ", ".join(sorted(removed)),
        )
    return removed


def _disable_dead_local_proxy(session: requests.Session) -> None:
    removed = sanitize_dead_local_proxy_environment()
    if removed and session.trust_env:
        logger.warning("Ignoring dead local proxy environment for shared HTTP session")
        session.trust_env = False


def get_http_session() -> requests.Session:
    """Return the fully configured shared compatibility Session."""

    global _HTTP_SESSION

    session = _HTTP_SESSION
    if session is None:
        with _SESSION_LOCK:
            if _HTTP_SESSION is None:
                session = TimeoutSession()
                _disable_dead_local_proxy(session)
                session.headers.update(
                    {"User-Agent": "InkyPi/1.0 (https://github.com/fatihak/InkyPi/)"}
                )
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=10,
                    pool_maxsize=10,
                    max_retries=_retry_policy(),
                    pool_block=False,
                )
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                session._inkypi_adapter_retries = True
                _HTTP_SESSION = session
            else:
                session = _HTTP_SESSION
    _disable_dead_local_proxy(session)
    return session


class HttpClient:
    """Consume, size-check, and close every response inside one adapter."""

    def __init__(self, *, session=None, owns_session=False):
        self.session = session if session is not None else get_http_session()
        self.owns_session = bool(owns_session)
        self._closed = False

    def request_json(
        self,
        method,
        url,
        *,
        context=None,
        max_bytes=DEFAULT_JSON_MAX_BYTES,
        timeout=None,
        **kwargs,
    ):
        result = self.request_bytes(
            method,
            url,
            context=context,
            max_bytes=max_bytes,
            timeout=timeout,
            **kwargs,
        )
        try:
            data = json_module.loads(result.data)
        except (UnicodeDecodeError, json_module.JSONDecodeError, TypeError) as error:
            raise HttpDecodeError(
                f"invalid JSON response from {_safe_url(url)}"
            ) from error
        return HttpResult(result.status, data, result.headers, result.url)

    def request_text(
        self,
        method,
        url,
        *,
        context=None,
        max_bytes=DEFAULT_JSON_MAX_BYTES,
        timeout=None,
        encoding="utf-8",
        errors="strict",
        **kwargs,
    ):
        result = self.request_bytes(
            method,
            url,
            context=context,
            max_bytes=max_bytes,
            timeout=timeout,
            **kwargs,
        )
        try:
            data = result.data.decode(encoding, errors=errors)
        except UnicodeError as error:
            raise HttpDecodeError(
                f"invalid text response from {_safe_url(url)}"
            ) from error
        return HttpResult(result.status, data, result.headers, result.url)

    def request_bytes(
        self,
        method,
        url,
        *,
        context=None,
        max_bytes=DEFAULT_RESPONSE_MAX_BYTES,
        timeout=None,
        **kwargs,
    ):
        return self._request_owned(
            method,
            url,
            context=context,
            max_bytes=max_bytes,
            timeout=timeout,
            consumer=_read_limited_response,
            **kwargs,
        )

    def stream_to_file(
        self,
        method,
        url,
        path,
        *,
        context=None,
        max_bytes=DEFAULT_RESPONSE_MAX_BYTES,
        timeout=None,
        mode=0o600,
        **kwargs,
    ):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        return self._request_owned(
            method,
            url,
            context=context,
            max_bytes=max_bytes,
            timeout=timeout,
            consumer=lambda response, limit, active_context: _stream_response_atomic(
                response,
                target,
                limit,
                active_context,
                mode,
            ),
            **kwargs,
        )

    def _request_owned(
        self,
        method,
        url,
        *,
        context,
        max_bytes,
        timeout,
        consumer,
        **kwargs,
    ):
        if self._closed:
            raise RuntimeError("HTTP client is closed")
        limit = _positive_bytes(max_bytes)
        context = _request_context(context, timeout)
        method = str(method).upper()
        managed_retries = bool(
            getattr(self.session, "_inkypi_adapter_retries", False)
        )
        attempts = 1 if managed_retries or method not in RETRYABLE_METHODS else 2

        for attempt in range(attempts):
            context.raise_if_cancelled()
            request_kwargs = dict(kwargs)
            request_kwargs["stream"] = True
            request_kwargs["timeout"] = _bounded_timeout(timeout, context)
            retry_scope = (
                DeadlineRetry.for_context(context)
                if managed_retries
                else nullcontext()
            )
            try:
                with retry_scope:
                    response = self.session.request(method, url, **request_kwargs)
            except requests.RequestException as error:
                raise HttpClientError(
                    f"{method} {_safe_url(url)} transport failed"
                ) from error
            try:
                status = int(response.status_code)
                if status in RETRYABLE_STATUSES and attempt + 1 < attempts:
                    delay = _manual_retry_delay(response, attempt)
                    _close_response(response)
                    _wait_for_retry(context, delay)
                    continue
                if not 200 <= status < 300:
                    raise HttpStatusError(method, url, status)
                data = consumer(response, limit, context)
                headers = MappingProxyType(
                    {str(key): str(value) for key, value in response.headers.items()}
                )
                final_url = _safe_url(getattr(response, "url", url))
                context.raise_if_cancelled()
                return HttpResult(status, data, headers, final_url)
            finally:
                _close_response(response)
        raise HttpClientError("HTTP request exhausted retry policy")

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.owns_session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _positive_bytes(value):
    try:
        number = operator.index(value)
    except TypeError:
        raise ValueError("max_bytes must be a positive integer") from None
    if number <= 0:
        raise ValueError("max_bytes must be a positive integer")
    return number


def _request_context(context, timeout):
    if context is not None:
        return context
    if timeout is None:
        seconds = DEFAULT_TIMEOUT_SECONDS
    elif isinstance(timeout, (tuple, list)):
        seconds = max(float(item) for item in timeout)
    else:
        seconds = float(timeout)
    seconds = max(0.001, min(900.0, seconds))
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + seconds,
    )


def _bounded_timeout(timeout, context):
    context.raise_if_cancelled()
    remaining = max(0.001, context.remaining_seconds())
    requested = DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout
    if isinstance(requested, (tuple, list)):
        if len(requested) != 2:
            raise ValueError("timeout tuple must contain connect and read values")
        return tuple(
            max(0.001, min(float(item), remaining))
            for item in requested
        )
    return max(0.001, min(float(requested), remaining))


def _read_limited_response(response, limit, context):
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > limit:
                raise ResponseTooLarge(limit)
        except ValueError:
            pass
    payload = bytearray()
    for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
        context.raise_if_cancelled()
        if not chunk:
            continue
        if len(payload) + len(chunk) > limit:
            raise ResponseTooLarge(limit)
        payload.extend(chunk)
    return bytes(payload)


def _stream_response_atomic(response, target, limit, context, mode):
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > limit:
                raise ResponseTooLarge(limit)
        except ValueError:
            pass

    raw_fd = None
    stream = None
    temp_path = None
    try:
        raw_fd, temp_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        if os.name != "nt":
            os.fchmod(raw_fd, operator.index(mode))
        stream = os.fdopen(raw_fd, "wb")
        raw_fd = None
        written = 0
        for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
            context.raise_if_cancelled()
            if not chunk:
                continue
            written += len(chunk)
            if written > limit:
                raise ResponseTooLarge(limit)
            stream.write(chunk)
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        stream = None
        os.replace(temp_path, target)
        temp_path = None
        fsync_directory(target.parent)
        return target
    finally:
        if stream is not None:
            try:
                stream.close()
            except OSError:
                logger.warning("Could not close HTTP download temporary file")
        elif raw_fd is not None:
            try:
                os.close(raw_fd)
            except OSError:
                logger.warning("Could not close HTTP download descriptor")
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("Could not remove HTTP download temporary file")


def _close_response(response):
    try:
        response.close()
    except Exception:
        logger.warning("Could not close owned HTTP response", exc_info=True)


def _manual_retry_delay(response, attempt):
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                target = parsedate_to_datetime(retry_after).timestamp()
                return max(0.0, target - time.time())
            except (TypeError, ValueError, OverflowError):
                pass
    return 0.0 if attempt == 0 else 0.5 * (2 ** (attempt - 1))


def _wait_for_retry(context, delay):
    remaining = context.remaining_seconds()
    if delay >= remaining:
        raise TaskDeadlineExceeded("HTTP retry delay exceeds task deadline")
    if delay > 0 and context.cancel_event.wait(delay):
        context.raise_if_cancelled()
    context.raise_if_cancelled()


def get_http_client() -> HttpClient:
    global _HTTP_CLIENT

    client = _HTTP_CLIENT
    if client is None:
        with _SESSION_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = HttpClient(session=get_http_session())
            client = _HTTP_CLIENT
    return client


def close_http_session():
    """Close and forget the shared client/session during lifecycle shutdown."""

    global _HTTP_CLIENT, _HTTP_SESSION

    with _SESSION_LOCK:
        if _HTTP_CLIENT is not None:
            _HTTP_CLIENT._closed = True
            _HTTP_CLIENT = None
        if _HTTP_SESSION is not None:
            _HTTP_SESSION.close()
            _HTTP_SESSION = None
