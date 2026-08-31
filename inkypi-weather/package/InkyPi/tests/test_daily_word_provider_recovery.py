"""Provider failover through real DailyWord rendering, provenance and caching."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from plugins.daily_word_poem import daily_word_poem as module
from plugins.daily_word_poem.daily_word_poem import DailyWordPoem
from tests.test_daily_word_poem import FakeDeviceConfig
from runtime.refresh_contracts import TaskContext, TaskCancelled


WIKTIONARY = {"parse": {"title": "tranquil", "revid": 92094244, "text": """
<div class="mw-parser-output"><div class="mw-heading mw-heading2"><h2 id="English">English</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Pronunciation">Pronunciation</h3></div>
<ul><li><span class="IPA">/\u02c8t\u0279\u00e6\u014bkw\u026al/</span></li></ul>
<div class="mw-heading mw-heading3"><h3 id="Adjective">Adjective</h3></div>
<p class="headword-line">tranquil</p><ol><li>Free from emotional or mental disturbance.
<dl><dd>Example text must not pollute the definition.</dd></dl></li></ol>
<div class="mw-heading mw-heading2"><h2 id="French">French</h2></div><ol><li>Wrong language.</li></ol></div>
"""}}


class Session:
    _inkypi_adapter_retries = True

    def __init__(self):
        self.calls = []
        self.primary = requests.ReadTimeout("primary provider unavailable")
        self.alternative = WIKTIONARY
        self.closed = []

    def request(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.primary if "dictionaryapi.dev" in url else self.alternative
        if isinstance(value, Exception):
            raise value
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = json.dumps(value).encode()
        response._content_consumed = True
        response.headers["Content-Type"] = "application/json"
        response.close = lambda: self.closed.append(url)
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


@pytest.fixture
def setup_plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "context"))
    clock = [datetime(2026, 8, 30, 12, tzinfo=timezone.utc)]
    plugin = DailyWordPoem({"id": "daily_word_poem"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path / "provider")
    monkeypatch.setattr(plugin, "_localized_now", lambda _device: clock[0])
    session = Session()
    monkeypatch.setattr(module, "get_http_session", lambda: session)
    settings = {"word_list": "tranquil", "quote_list": "Stay curious - Fixture Author",
                "fetch_dictionary": True, "fetch_wikiquote": False}
    return plugin, session, clock, settings


def test_primary_outage_recovers_real_definition_and_persists_attributed_daily_cache(setup_plugin):
    plugin, session, _clock, settings = setup_plugin
    with plugin.generate_image(settings, FakeDeviceConfig()) as image:
        assert read_source_provenance(image) is SourceProvenance.LIVE, "working alternate source must restore DATA health"
    cached = json.loads((plugin._cache_dir() / "daily.json").read_text(encoding="utf-8"))
    assert cached["word"]["definition"] == "Free from emotional or mental disturbance."
    assert cached["word"]["source"] == "Wiktionary"
    assert cached["word"]["source_revision"] == 92094244
    assert cached["word"]["source_license"] == "CC BY-SA 4.0"
    assert "en.wiktionary.org" in cached["word"]["source_url"]
    assert "Wiktionary" in cached["sources"][0]
    assert not cached["warnings"]
    assert len(session.calls) == 2
    assert len(session.closed) == 1
    assert session.calls[1][1]["params"]["maxlag"] == "5"
    assert session.calls[1][1]["stream"] is True
    assert 0 < session.calls[1][1]["timeout"] <= 8


def test_same_word_next_day_uses_fresh_source_cache_without_network(setup_plugin):
    plugin, session, clock, settings = setup_plugin
    plugin.generate_image(settings, FakeDeviceConfig()).close()
    clock[0] += timedelta(days=1)
    session.primary = session.alternative = requests.ReadTimeout("both offline")
    session.calls.clear()
    with plugin.generate_image(settings, FakeDeviceConfig()) as image:
        assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    cached = json.loads((plugin._cache_dir() / "daily.json").read_text(encoding="utf-8"))
    assert cached["date"] == "2026-08-31"
    assert cached["word"]["definition"] == "Free from emotional or mental disturbance."
    assert session.calls == [], "a validated per-word cache should avoid repeat upstream work"


def test_expired_word_cache_keeps_real_definition_visible_without_claiming_live_success(setup_plugin, monkeypatch):
    plugin, session, clock, settings = setup_plugin
    plugin.generate_image(settings, FakeDeviceConfig()).close()
    clock[0] += timedelta(days=31)
    session.primary = session.alternative = requests.ReadTimeout("both offline")
    drawn = []
    real_text = module.ImageDraw.ImageDraw.text

    def capture_text(draw, xy, text, *args, **kwargs):
        drawn.append(str(text))
        return real_text(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(module.ImageDraw.ImageDraw, "text", capture_text)
    with plugin.generate_image(settings, FakeDeviceConfig()) as image:
        assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
        assert not image.info.get("inkypi_skip_cache")
    assert "Free from emotional or mental disturbance." in " ".join(drawn)
    assert "definition cached" in " ".join(drawn)
    cached = json.loads((plugin._cache_dir() / "daily.json").read_text(encoding="utf-8"))
    assert cached["date"] == "2026-08-30", "expired data must not refresh last-good daily metadata"


@pytest.mark.parametrize("bad_response", [
    {"error": {"code": "maxlag"}},
    {"parse": {"title": "different word", "revid": 1, "text": "<h2>English</h2>"}},
    {"parse": {"title": "tranquil", "revid": 1, "text": "<h2>French</h2><h3>Noun</h3><ol><li>Wrong</li></ol>"}},
    {"parse": {"title": "tranquil", "revid": 1, "text": "<h2>English</h2><h3>Noun</h3><ol><li>Truncated"}},
    {"parse": {"title": "tranquil", "revid": 1, "text": "x" * 262145}},
])
def test_invalid_backup_cannot_become_healthy_or_poison_source_cache(setup_plugin, bad_response):
    plugin, session, _clock, settings = setup_plugin
    session.alternative = bad_response
    with plugin.generate_image(settings, FakeDeviceConfig()) as image:
        assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert not (plugin._cache_dir() / "daily.json").exists()
    assert not (plugin._cache_dir() / "dictionary.json").exists()
    assert len(session.closed) == 1


def test_primary_success_does_not_call_backup_and_force_refresh_really_rechecks(setup_plugin):
    plugin, session, _clock, settings = setup_plugin
    session.primary = [{"word": "tranquil", "meanings": [{"partOfSpeech": "adjective", "definitions": [{"definition": "A primary-source definition."}]}]}]
    plugin.generate_image(settings, FakeDeviceConfig()).close()
    assert len(session.calls) == 1
    session.primary = session.alternative = requests.ReadTimeout("both unavailable")
    with plugin.generate_image({**settings, "force_refresh": True}, FakeDeviceConfig()) as image:
        assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert len(session.calls) == 3


def test_malformed_cached_source_url_is_ignored_and_refetched(setup_plugin):
    plugin, session, clock, settings = setup_plugin
    plugin.generate_image(settings, FakeDeviceConfig()).close()
    cache_path = plugin._cache_dir() / "dictionary.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    cache["entries"]["tranquil"]["entry"]["source_url"] = "https://[invalid"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    clock[0] += timedelta(days=1)
    with plugin.generate_image(settings, FakeDeviceConfig()) as image:
        assert read_source_provenance(image) is SourceProvenance.LIVE
    assert len(session.calls) == 4


def test_dictionary_cache_eviction_bounds_entries_and_preserves_new_success(setup_plugin):
    plugin, session, clock, settings = setup_plugin
    plugin.generate_image(settings, FakeDeviceConfig()).close()
    path = plugin._cache_dir() / "dictionary.json"
    cache = json.loads(path.read_text(encoding="utf-8"))
    template = cache["entries"]["tranquil"]
    for number in range(140):
        row = deepcopy(template)
        key = f"fixture{number}"
        row["entry"]["word"] = key
        row["fetched_at"] = clock[0].timestamp() - number - 1
        cache["entries"][key] = row
    path.write_text(json.dumps(cache), encoding="utf-8")
    plugin.generate_image({**settings, "force_refresh": True}, FakeDeviceConfig()).close()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved["entries"]) == 128
    assert "tranquil" in saved["entries"]
    assert "fixture139" not in saved["entries"]


def test_cancellation_after_primary_failure_does_not_start_backup_or_write_cache(setup_plugin, monkeypatch):
    plugin, session, _clock, settings = setup_plugin
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)
    monkeypatch.setattr(module, "current_task_context", lambda: context)
    original_request = session.request

    def cancel_on_primary(method, url, **kwargs):
        context.cancel_event.set()
        return original_request(method, url, **kwargs)

    session.request = cancel_on_primary
    with pytest.raises(TaskCancelled):
        plugin.generate_image(settings, FakeDeviceConfig())
    assert len(session.calls) == 1
    assert not (plugin._cache_dir() / "dictionary.json").exists()
    assert not (plugin._cache_dir() / "daily.json").exists()


@pytest.mark.parametrize("bad_definition", [{"status": "temporarily unavailable"}, 123, "<html>Service unavailable</html>"])
def test_malformed_primary_definition_uses_valid_backup(setup_plugin, bad_definition):
    plugin, session, _clock, settings = setup_plugin
    session.primary = [{"word": "tranquil", "meanings": [{"partOfSpeech": "adjective", "definitions": [{"definition": bad_definition}]}]}]
    plugin.generate_image(settings, FakeDeviceConfig()).close()
    cached = json.loads((plugin._cache_dir() / "daily.json").read_text(encoding="utf-8"))
    assert cached["word"]["source"] == "Wiktionary"
    assert cached["word"]["definition"] == "Free from emotional or mental disturbance."


def test_cancellation_during_quote_response_does_not_publish_daily_cache(setup_plugin, monkeypatch):
    plugin, session, _clock, _settings = setup_plugin
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)
    monkeypatch.setattr(module, "current_task_context", lambda: context)

    def quote_response(url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = b"A valid fixture sentence. ~ [[Fixture Author]] ~"
        context.cancel_event.set()
        return response

    session.get = quote_response
    with pytest.raises(TaskCancelled):
        plugin.generate_image({"word_list": "tranquil", "fetch_dictionary": False, "fetch_wikiquote": True}, FakeDeviceConfig())
    assert not (plugin._cache_dir() / "daily.json").exists()


def test_runtime_backup_success_advances_data_success_clears_retry_and_displays_new_pixels(setup_plugin, tmp_path, monkeypatch):
    from PIL import Image
    from runtime.refresh_contracts import CommandKind, CommandSource, RefreshIntent
    from runtime.runtime_state import RefreshLane
    from tests.test_refresh_task import _make_runtime_task, _runtime_playlist, _runtime_plugin_data, _write_runtime_cache, refresh_task_module

    plugin, _session, clock, settings = setup_plugin
    old_success = "2026-08-26T06:45:46+00:00"
    config = _runtime_plugin_data("daily_word_poem", "DailyWord", latest_refresh_time=old_success, interval=300)
    config["plugin_settings"].update(settings)
    playlist = _runtime_playlist(config)
    (tmp_path / "runtime").mkdir()
    task, device, _ = _make_runtime_task(tmp_path / "runtime", playlists=[playlist])
    device.config.update({"theme_mode": "day", "active_theme": "day"})
    monkeypatch.setattr(device, "get_resolution", lambda: (800, 480))
    monkeypatch.setattr(task, "_get_current_datetime", lambda: clock[0])
    monkeypatch.setattr(refresh_task_module, "get_plugin_instance", lambda _config: plugin)
    instance = playlist.plugins[0].snapshot()
    task.runtime_state.record_success(instance.instance_uuid, old_success, lane=RefreshLane.DATA)
    cache_path = Path(_write_runtime_cache(task, instance, Image.new("RGB", (800, 480), "blue")))
    refresh = task._playlist_command(playlist.name, instance, source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH, display_cached_only=False, kind=CommandKind.CACHE_REFRESH, current_dt=clock[0])
    task._execute_command(refresh)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.data.last_success_at == clock[0].isoformat()
    assert state.data.next_retry_at is None
    with Image.open(cache_path) as cached:
        pixels = cached.tobytes()
    display = task._playlist_command(playlist.name, instance, source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE, display_cached_only=True, kind=CommandKind.DISPLAY, current_dt=clock[0])
    task._execute_command(display)
    assert task.display_manager.calls[-1][0].tobytes() == pixels


@pytest.mark.parametrize("document", [[], None, "broken root"])
def test_corrupt_daily_cache_root_is_refetched_without_breaking_render(setup_plugin, document):
    plugin, _session, _clock, settings = setup_plugin
    plugin._cache_dir().mkdir(parents=True)
    (plugin._cache_dir() / "daily.json").write_text(json.dumps(document), encoding="utf-8")
    with plugin.generate_image(settings, FakeDeviceConfig()) as image:
        assert read_source_provenance(image) is SourceProvenance.LIVE


def test_cache_write_is_byte_bounded_even_when_entry_count_is_below_limit(setup_plugin):
    plugin, session, clock, settings = setup_plugin
    plugin.generate_image(settings, FakeDeviceConfig()).close()
    path = plugin._cache_dir() / "dictionary.json"
    cache = json.loads(path.read_text(encoding="utf-8"))
    template = cache["entries"]["tranquil"]
    # Seed just below the byte limit. A new valid large entry crosses it.
    definition = "A valid definition " + "\u00e9" * 1990
    row = deepcopy(template)
    row["entry"]["definition"] = definition
    cache["entries"] = {}
    for number in range(127):
        key = f"fixture{number}"
        candidate = deepcopy(row)
        candidate["entry"]["word"] = key
        candidate["fetched_at"] = clock[0].timestamp() - number - 1
        cache["entries"][key] = candidate
        encoded = (json.dumps(cache, ensure_ascii=False) + "\n").encode()
        if len(encoded) > 523000:
            del cache["entries"][key]
            break
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    session.alternative = deepcopy(WIKTIONARY)
    session.alternative["parse"]["text"] = WIKTIONARY["parse"]["text"].replace("Free from emotional or mental disturbance.", definition)
    plugin.generate_image({**settings, "force_refresh": True}, FakeDeviceConfig()).close()
    assert path.stat().st_size <= 524288
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "tranquil" in saved["entries"]
