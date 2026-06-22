import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import http_client



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