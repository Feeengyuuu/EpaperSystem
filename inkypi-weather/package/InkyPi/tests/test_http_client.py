import os
import sys
from pathlib import Path
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import http_client
from runtime.refresh_contracts import TaskContext, TaskDeadlineExceeded


class FakeResponse:
    def __init__(self, status, payload=b"{}", *, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = dict(headers or {})
        self.url = "https://example.test/resource?secret=value"
        self.closed = False

    def iter_content(self, chunk_size=8192):
        for offset in range(0, len(self._payload), chunk_size):
            yield self._payload[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def _context(seconds=5):
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + seconds,
    )



def test_sanitize_dead_local_proxy_environment_removes_only_dead_proxy(monkeypatch):
    http_client.close_http_session()
    for name in http_client._PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8080")

    removed = http_client.sanitize_dead_local_proxy_environment()

    assert removed == {"HTTPS_PROXY": "http://127.0.0.1:9"}
    assert "HTTPS_PROXY" not in os.environ
    assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:8080"


def test_http_session_ignores_dead_local_proxy(monkeypatch):
    http_client.close_http_session()
    for name in http_client._PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    session = http_client.get_http_session()

    assert session.trust_env is False
    assert "HTTPS_PROXY" not in os.environ
    http_client.close_http_session()


def test_http_session_disables_dead_local_proxy_after_creation(monkeypatch):
    http_client.close_http_session()
    for name in http_client._PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    session = http_client.get_http_session()
    assert session.trust_env is True

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    assert http_client.get_http_session() is session
    assert session.trust_env is False
    assert "HTTPS_PROXY" not in os.environ
    http_client.close_http_session()

def test_http_session_keeps_real_proxy_support(monkeypatch):
    http_client.close_http_session()
    for name in http_client._PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")

    session = http_client.get_http_session()

    assert session.trust_env is True
    http_client.close_http_session()


def test_http_session_applies_default_timeout(monkeypatch):
    http_client.close_http_session()
    for name in http_client._PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    captured = {}

    def fake_request(self, method, url, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr("requests.Session.request", fake_request)

    session = http_client.get_http_session()
    session.get("https://example.com")

    assert captured["timeout"] == http_client.DEFAULT_TIMEOUT_SECONDS
    http_client.close_http_session()


def test_http_session_keeps_explicit_timeout(monkeypatch):
    http_client.close_http_session()
    for name in http_client._PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    captured = {}

    def fake_request(self, method, url, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr("requests.Session.request", fake_request)

    session = http_client.get_http_session()
    session.get("https://example.com", timeout=5)

    assert captured["timeout"] == 5
    http_client.close_http_session()

def test_concurrent_first_calls_share_single_session(monkeypatch):
    import threading
    import time

    http_client.close_http_session()
    constructed = []

    class SlowTimeoutSession(http_client.TimeoutSession):
        def __init__(self):
            constructed.append(1)
            time.sleep(0.05)
            super().__init__()

    monkeypatch.setattr(http_client, "TimeoutSession", SlowTimeoutSession)

    barrier = threading.Barrier(4)
    sessions = []

    def worker():
        barrier.wait()
        sessions.append(http_client.get_http_session())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert len(constructed) == 1
        assert all(session is sessions[0] for session in sessions)
        # the published session must be fully configured
        assert "InkyPi" in sessions[0].headers.get("User-Agent", "")
    finally:
        http_client.close_http_session()


def test_post_is_not_retried_and_get_retries_once_on_503():
    first = FakeResponse(503)
    second = FakeResponse(200, b'{"ok": true}')
    session = FakeSession([first, second])
    client = http_client.HttpClient(session=session)

    result = client.request_json(
        "GET",
        "https://example.test/resource",
        context=_context(),
    )

    assert result.status == 200
    assert result.data == {"ok": True}
    assert len(session.calls) == 2
    assert first.closed and second.closed

    failed = FakeResponse(503)
    unused = FakeResponse(200)
    session = FakeSession([failed, unused])
    client = http_client.HttpClient(session=session)
    with pytest.raises(http_client.HttpStatusError) as raised:
        client.request_json(
            "POST",
            "https://example.test/resource",
            context=_context(),
            json={},
        )
    assert raised.value.status == 503
    assert len(session.calls) == 1
    assert failed.closed


def test_request_bytes_closes_response_when_limit_exceeded():
    response = FakeResponse(200, b"12345")
    client = http_client.HttpClient(session=FakeSession([response]))

    with pytest.raises(http_client.ResponseTooLarge):
        client.request_bytes(
            "GET",
            "https://example.test/image",
            max_bytes=4,
            context=_context(),
        )

    assert response.closed


def test_expired_context_prevents_network_call():
    session = FakeSession([FakeResponse(200)])
    client = http_client.HttpClient(session=session)
    context = TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() - 1,
    )

    with pytest.raises(TaskDeadlineExceeded):
        client.request_bytes(
            "GET",
            "https://example.test/image",
            context=context,
        )

    assert session.calls == []


def test_request_timeout_is_capped_by_context_deadline():
    session = FakeSession([FakeResponse(200, b"ok")])
    client = http_client.HttpClient(session=session)

    client.request_bytes(
        "GET",
        "https://example.test/image",
        timeout=(10, 20),
        context=_context(0.5),
    )

    timeout = session.calls[0][2]["timeout"]
    assert 0 < timeout[0] <= 0.5
    assert 0 < timeout[1] <= 0.5


def test_stream_to_file_is_atomic_and_closes_response(tmp_path):
    target = tmp_path / "download.bin"
    target.write_bytes(b"old")
    response = FakeResponse(200, b"new-content")
    client = http_client.HttpClient(session=FakeSession([response]))

    result = client.stream_to_file(
        "GET",
        "https://example.test/file",
        target,
        max_bytes=32,
        context=_context(),
    )

    assert result.data == target
    assert target.read_bytes() == b"new-content"
    assert response.closed
    assert list(tmp_path.glob("*.tmp")) == []


def test_shared_session_retry_policy_is_bounded_and_safe_methods_only(monkeypatch):
    http_client.close_http_session()
    for name in http_client._PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    session = http_client.get_http_session()
    retry = session.get_adapter("https://").max_retries

    assert retry.total == 1
    assert retry.connect == 1
    assert retry.read == 0
    assert retry.status == 1
    assert retry.allowed_methods == frozenset({"GET", "HEAD"})
    assert retry.status_forcelist == {429, 502, 503, 504}
    assert retry.respect_retry_after_header is True
    http_client.close_http_session()
