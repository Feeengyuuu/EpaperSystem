import os
import threading
import time
from datetime import datetime, timezone

import pytest

from plugins.telegram_digest import account_worker
from plugins.telegram_digest.telegram_digest import TelegramDigest
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded


FAKE_TELETHON = '''
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

types = SimpleNamespace(InputMessagesFilterPhotoVideo=object)


class TelegramClient:
    def __init__(self, session, api_id, api_hash):
        Path(os.environ["FAKE_TELETHON_PID_FILE"]).write_text(str(os.getpid()), encoding="utf-8")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def is_user_authorized(self):
        mode = os.environ.get("FAKE_TELETHON_MODE", "")
        if mode == "sleep":
            await asyncio.sleep(60)
        return mode != "unauthorized"

    async def _no_items(self):
        return
        yield

    def iter_dialogs(self, limit=None):
        return self._no_items()
'''


@pytest.fixture
def fake_telethon(tmp_path, monkeypatch):
    package = tmp_path / "fake-site" / "telethon"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(FAKE_TELETHON, encoding="utf-8")
    pid_file = tmp_path / "telethon.pid"
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(package.parent), existing])))
    monkeypatch.setenv("FAKE_TELETHON_PID_FILE", str(pid_file))
    monkeypatch.delenv("FAKE_TELETHON_MODE", raising=False)
    return pid_file


def _request(tmp_path):
    return {
        "settings": {"unreadOnly": False},
        "cache": {},
        "now": datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc).isoformat(),
        "max_messages": 8,
        "config": {"session_path": str(tmp_path / "telegram_account"), "api_id": 12345, "api_hash": "hash-value"},
        "cache_dir": str(tmp_path / "plugin-cache"),
    }


def test_child_fetch_returns_the_payload_from_another_process(tmp_path, fake_telethon):
    payload = account_worker.run_account_fetch_in_child(_request(tmp_path), timeout=60)

    assert payload["status"]["source_state"] == "live"
    assert int(fake_telethon.read_text(encoding="utf-8")) != os.getpid()


def test_child_fetch_reports_the_worker_error(tmp_path, fake_telethon, monkeypatch):
    monkeypatch.setenv("FAKE_TELETHON_MODE", "unauthorized")

    with pytest.raises(RuntimeError, match="^Telegram account session is not authorized yet$"):
        account_worker.run_account_fetch_in_child(_request(tmp_path), timeout=60)


def test_child_fetch_is_killed_when_the_task_is_cancelled(tmp_path, fake_telethon, monkeypatch):
    monkeypatch.setenv("FAKE_TELETHON_MODE", "sleep")
    context = TaskContext(threading.Event(), time.monotonic() + 120)
    threading.Timer(1.0, context.cancel_event.set).start()
    started = time.monotonic()

    with pytest.raises(TaskCancelled):
        account_worker.run_account_fetch_in_child(_request(tmp_path), context=context, timeout=120)

    assert time.monotonic() - started < 15


def test_child_fetch_stops_at_the_task_deadline(tmp_path, fake_telethon, monkeypatch):
    monkeypatch.setenv("FAKE_TELETHON_MODE", "sleep")
    context = TaskContext(threading.Event(), time.monotonic() + 3)

    with pytest.raises(TaskDeadlineExceeded):
        account_worker.run_account_fetch_in_child(_request(tmp_path), context=context, timeout=120)


class DummyDeviceConfig:
    def __init__(self, env):
        self.env = env

    def load_env_key(self, key):
        return self.env.get(key, "")

    def get_config(self, key=None, default=None):
        return default


def _session_plugin(tmp_path, monkeypatch):
    runtime_data = tmp_path / "runtime-data"
    runtime_data.mkdir()
    (runtime_data / "telegram_account.session").write_text("authorized", encoding="utf-8")
    monkeypatch.setenv("INKYPI_DATA_DIR", str(runtime_data))
    plugin = TelegramDigest({"id": "telegram_digest"})
    cache_dir = tmp_path / "plugin-cache"
    plugin._cache_dir = lambda: cache_dir
    return plugin, cache_dir, runtime_data


def test_account_fetch_runs_in_a_child_process_by_default(tmp_path, monkeypatch):
    plugin, cache_dir, runtime_data = _session_plugin(tmp_path, monkeypatch)
    requests = []
    monkeypatch.setattr(
        account_worker,
        "run_account_fetch_in_child",
        lambda request, context=None: requests.append(request) or {"status": {"source_state": "live"}},
    )

    payload = plugin._fetch_account_payload(
        {"unreadOnly": False},
        DummyDeviceConfig({"TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "hash-value"}),
        {"messages": []},
        datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc),
        8,
    )

    assert payload == {"status": {"source_state": "live"}}
    assert requests[0]["cache_dir"] == str(cache_dir)
    assert requests[0]["now"] == "2026-10-06T20:00:00+00:00"
    assert requests[0]["max_messages"] == 8
    assert requests[0]["config"]["session_path"] == str(runtime_data / "telegram_account")


def test_account_fetch_lets_task_cancellation_through(tmp_path, monkeypatch):
    plugin, _cache_dir, _runtime_data = _session_plugin(tmp_path, monkeypatch)

    def cancelled(request, context=None):
        raise TaskCancelled("task was canceled")

    monkeypatch.setattr(account_worker, "run_account_fetch_in_child", cancelled)

    with pytest.raises(TaskCancelled):
        plugin._fetch_account_payload(
            {},
            DummyDeviceConfig({"TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "hash-value"}),
            {},
            datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc),
            8,
        )
