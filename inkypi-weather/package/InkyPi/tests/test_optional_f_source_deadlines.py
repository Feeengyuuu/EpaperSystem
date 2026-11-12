"""A provider's HTTP retry deadline must not cancel an otherwise healthy page refresh."""

from datetime import datetime, timedelta, timezone
import random
import threading

import pytest

from plugins.daily_art import daily_art
from plugins.tech_pulse import extras
from plugins.ticketmaster_events import local_sources
from runtime.long_task_executor import InstanceIdentity, bind_long_task_runtime
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded
from tests.test_daily_art import make_plugin
from tests.test_daily_art_new_sources import MIA
from tests.test_http_client import FakeResponse, FakeSession
from tests.test_tech_pulse_extras import _answers
from tests.test_ticketmaster_events import DummyDeviceConfig, _canonical_theme
from tests.test_ticketmaster_local_sources import _refreshing_plugin, _ticketmaster
from utils.http_client import HttpClient

NOW = datetime(2026, 11, 12, 4, tzinfo=timezone.utc)
IDENTITY = InstanceIdentity("optional-f-test", 1, 1)


def _parent(seconds=120):
    clock = [100.0]
    return TaskContext(threading.Event(), clock[0] + seconds, lambda: clock[0]), clock


def _provider_retry(monkeypatch, *, during_request=None):
    replies = [FakeResponse(503, headers={"Retry-After": "30"}) for _ in range(4)]
    session = FakeSession(replies)
    original = session.request

    def request(*args, **kwargs):
        if during_request:
            during_request()
        return original(*args, **kwargs)

    session.request = request
    client = HttpClient(session=session)
    for module in (extras, local_sources, daily_art):
        monkeypatch.setattr(module, "get_http_client", lambda: client)
    return session, replies


def _kept_and_loader(kind, tmp_path, monkeypatch):
    if kind == "tech":
        kept = extras.refresh({}, NOW - timedelta(days=2), _answers)
        return kept, lambda: extras.refresh(kept, NOW)
    if kind == "local":
        kept = {name: {"fetched_at": (NOW - timedelta(hours=4)).isoformat(),
                       "events": [{"id": name + "-kept", "title": "Kept event"}]}
                for name in local_sources.SOURCES}
        return kept, lambda: local_sources.load_local_sources(kept, NOW)
    plugin = make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_get_json", lambda *_args, **_kwargs: MIA)
    kept = plugin._cached_collection_candidates("artsmia", "painting", 12, random.Random(1),
                                                NOW - timedelta(days=2))
    monkeypatch.delattr(plugin, "_get_json")
    return kept, lambda: plugin._cached_collection_candidates("artsmia", "painting", 12, random.Random(1), NOW)


@pytest.mark.parametrize("kind", ["tech", "local", "art"])
def test_http_retry_after_exceeding_its_slice_keeps_optional_data(kind, tmp_path, monkeypatch):
    kept, load = _kept_and_loader(kind, tmp_path, monkeypatch)
    session, replies = _provider_retry(monkeypatch)
    parent, _clock = _parent()
    with bind_long_task_runtime(parent, IDENTITY):
        assert load() == kept
    assert session.calls and parent.remaining_seconds() == 120
    assert all(reply.closed for reply in replies[:len(session.calls)])


@pytest.mark.parametrize("kind", ["tech", "local", "art"])
def test_optional_sources_leave_a_small_remaining_budget_to_the_main_page(kind, tmp_path, monkeypatch):
    kept, load = _kept_and_loader(kind, tmp_path, monkeypatch)
    session, _replies = _provider_retry(monkeypatch)
    parent, _clock = _parent(seconds=2)
    with bind_long_task_runtime(parent, IDENTITY):
        assert load() == kept
    assert session.calls == []


@pytest.mark.parametrize("kind", ["tech", "local", "art"])
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_optional_http_failure_still_propagates_a_real_parent_stop(kind, stop, tmp_path, monkeypatch):
    _kept, load = _kept_and_loader(kind, tmp_path, monkeypatch)
    parent, clock = _parent()

    def stop_parent():
        if stop == "cancel":
            parent.cancel_event.set()
        else:
            clock[0] += 121

    session, _replies = _provider_retry(monkeypatch, during_request=stop_parent)
    with bind_long_task_runtime(parent, IDENTITY), pytest.raises(
            TaskCancelled if stop == "cancel" else TaskDeadlineExceeded):
        load()
    assert len(session.calls) == 1


def test_optional_http_timeout_preserves_ticketmasters_newly_loaded_main_events(tmp_path, monkeypatch):
    plugin, rendered = _refreshing_plugin(monkeypatch, tmp_path, local_sources.load_local_sources)
    session, _replies = _provider_retry(monkeypatch)
    parent, _clock = _parent()
    with bind_long_task_runtime(parent, IDENTITY):
        image = plugin.generate_image({"apiKey": "test-key", "_inkypi_theme": _canonical_theme("day")},
                                      DummyDeviceConfig())
    assert session.calls and len(rendered) == 1
    assert [event.title for event in rendered[0]] == [event.title for event in _ticketmaster()]
    assert image.info.get("inkypi_skip_cache") is None


@pytest.mark.parametrize("error", [TaskCancelled, TaskDeadlineExceeded])
def test_ticketmaster_page_does_not_swallow_a_parent_stop_from_optional_sources(tmp_path, monkeypatch, error):
    def stopped(_previous, _now):
        raise error("parent stopped")

    plugin, _rendered = _refreshing_plugin(monkeypatch, tmp_path, stopped)
    with pytest.raises(error):
        plugin.generate_image({"apiKey": "test-key", "_inkypi_theme": _canonical_theme("day")}, DummyDeviceConfig())
