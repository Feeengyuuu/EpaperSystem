import json
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.ticketmaster_events.ticketmaster_events as ticketmaster_module  # noqa: E402
from plugins.ticketmaster_events.ticketmaster_events import (  # noqa: E402
    TicketmasterEvent,
    TicketmasterEvents,
)


class DummyDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, _key=None, default=None):
        return default

    def load_env_key(self, _key):
        return None


class DummyEnvDeviceConfig(DummyDeviceConfig):
    def load_env_key(self, key):
        return "alias-key" if key == "TICKETMASTER_CONSUMER_KEY" else None


def _canonical_theme(mode):
    palette = {
        "background": (229, 236, 232) if mode == "day" else (0, 0, 0),
        "panel": (207, 226, 220) if mode == "day" else (18, 20, 22),
        "ink": (28, 43, 48) if mode == "day" else (238, 238, 231),
        "muted": (52, 72, 76) if mode == "day" else (176, 176, 168),
        "rule": (175, 197, 194) if mode == "day" else (46, 48, 52),
        "accent": (177, 68, 53) if mode == "day" else (229, 188, 88),
    }
    return {
        "mode": mode,
        "requested_mode": "auto",
        "palette": palette,
        "css": {},
    }


def _sample_api_payload():
    return {
        "_embedded": {
            "events": [
                {
                    "id": "abc123",
                    "name": "Sample Artist Live",
                    "url": "https://example.com/event",
                    "distance": 4.23,
                    "images": [
                        {"url": "https://example.com/small.jpg", "ratio": "3_2", "width": 300, "height": 200},
                        {"url": "https://example.com/large.jpg", "ratio": "16_9", "width": 1200, "height": 675},
                    ],
                    "dates": {
                        "start": {"localDate": "2026-07-04", "localTime": "19:30:00", "timezone": "America/Los_Angeles"},
                        "status": {"code": "onsale"},
                    },
                    "classifications": [
                        {"segment": {"name": "Music"}, "genre": {"name": "Rock"}}
                    ],
                    "priceRanges": [
                        {"currency": "USD", "min": 45.0, "max": 120.0}
                    ],
                    "_embedded": {
                        "venues": [
                            {
                                "name": "The Fillmore",
                                "city": {"name": "San Francisco"},
                                "state": {"stateCode": "CA"},
                            }
                        ]
                    },
                }
            ]
        }
    }


def test_events_from_discovery_parses_ticketmaster_event():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    events = plugin._events_from_discovery(_sample_api_payload(), 5)

    assert len(events) == 1
    assert events[0].rank == 1
    assert events[0].event_id == "abc123"
    assert events[0].title == "Sample Artist Live"
    assert events[0].local_date == "2026-07-04"
    assert events[0].local_time == "19:30:00"
    assert events[0].venue_name == "The Fillmore"
    assert events[0].city == "San Francisco"
    assert events[0].state_code == "CA"
    assert events[0].segment == "Music"
    assert events[0].genre == "Rock"
    assert events[0].status == "onsale"
    assert events[0].price == "$45-120"
    assert events[0].distance == "4.2 mi"
    assert events[0].image_url.endswith("large.jpg")


def test_base_params_default_to_three_hour_plugin_location_window():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    params = plugin._base_params({}, "test-key", 5)

    assert params["apikey"] == "test-key"
    assert params["postalCode"] == "94539"
    assert params["radius"] == "50"
    assert params["includeTBA"] == "no"
    assert params["includeTBD"] == "no"
    assert params["sort"] == "date,asc"
    assert "startDateTime" in params
    assert "endDateTime" in params

def test_ticketmaster_api_key_accepts_registry_alias():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    assert plugin._ticketmaster_api_key({}, DummyEnvDeviceConfig()) == "alias-key"


def test_ticketmaster_key_matches_canonical_registry_entry():
    project_root = Path(__file__).resolve().parents[1]
    registry = json.loads(
        (project_root / "install" / "api_key_registry.json").read_text(
            encoding="utf-8",
        )
    )
    entry = next(
        item for item in registry["keys"]
        if item["key"] == "TICKETMASTER_API_KEY"
    )

    assert "TICKETMASTER_CONSUMER_KEY" in entry["aliases"]
    settings = (
        project_root / "src" / "plugins" / "ticketmaster_events" / "settings.html"
    ).read_text(encoding="utf-8")
    assert 'value="TICKETMASTER_API_KEY"' in settings

def test_load_events_falls_back_to_94539_latlong():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    calls = []

    def fake_fetch(params):
        calls.append(dict(params))
        return {} if len(calls) == 1 else _sample_api_payload()

    plugin._fetch_ticketmaster_json = fake_fetch

    events = plugin._load_events({"postalCode": "94539"}, 1, "test-key")

    assert events[0].title == "Sample Artist Live"
    assert calls[0]["postalCode"] == "94539"
    assert "postalCode" not in calls[1]
    assert calls[1]["latlong"] == "37.5202,-121.9264"


def test_blank_postal_code_uses_explicit_city_and_state():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    params = plugin._base_params(
        {
            "postalCode": "",
            "city": "Oakland",
            "stateCode": "CA",
            "countryCode": "US",
        },
        "test-key",
        5,
    )

    assert "postalCode" not in params
    assert params["city"] == "Oakland"
    assert params["stateCode"] == "CA"
    assert params["countryCode"] == "US"


def test_provider_json_uses_bounded_shared_client(monkeypatch):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    captured = {}

    class FakeClient:
        def request_json(self, method, url, **kwargs):
            captured.update(method=method, url=url, kwargs=kwargs)
            return SimpleNamespace(data=_sample_api_payload())

    monkeypatch.setattr(ticketmaster_module, "get_http_client", lambda: FakeClient())

    payload = plugin._fetch_ticketmaster_json({"apikey": "test-key"})

    assert payload == _sample_api_payload()
    assert captured["method"] == "GET"
    assert captured["url"] == ticketmaster_module.DISCOVERY_EVENTS_URL
    assert captured["kwargs"]["max_bytes"] <= 2 * 1024 * 1024
    assert captured["kwargs"]["timeout"] == 18


def test_settings_keep_blank_postal_and_never_offer_direct_key_persistence():
    settings = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "ticketmaster_events"
        / "settings.html"
    ).read_text(encoding="utf-8")

    assert 'name="apiKey"' not in settings
    assert "pluginSettings.apiKey ||" not in settings
    assert "pluginSettings.postalCode !== undefined" in settings

def test_default_palette_is_color_epaper():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    palette = plugin._palette({})

    assert palette["mode"] == "color"
    assert palette["paper"] != (0, 0, 0)
    assert palette["accent"] != palette["line"]


def test_ticketmaster_uses_injected_canonical_day_and_night_palettes():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    day = plugin._palette({"_inkypi_theme": _canonical_theme("day")})
    night = plugin._palette({"_inkypi_theme": _canonical_theme("night")})

    assert day["mode"] == "color"
    assert day["paper"] == (229, 236, 232)
    assert day["ink"] == (28, 43, 48)
    assert day["line"] == (175, 197, 194)
    assert day["accent"] == (177, 68, 53)
    assert night["mode"] == "dark"
    assert night["paper"] == (0, 0, 0)
    assert night["ink"] == (238, 238, 231)
    assert night["line"] == (46, 48, 52)
    assert night["accent"] == (229, 188, 88)


def test_ticketmaster_source_cache_key_ignores_dimensions_and_theme():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    day_settings = {
        "themeMode": "day",
        "_inkypi_theme": _canonical_theme("day"),
    }
    night_settings = {
        "themeMode": "night",
        "_inkypi_theme": _canonical_theme("night"),
    }

    assert plugin._cache_key(day_settings, (800, 480), 5) == plugin._cache_key(
        night_settings,
        (480, 800),
        5,
    )


def test_theme_only_render_reuses_even_expired_source_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    calls = {"load": 0}

    def fake_load(_settings, _items_count, _api_key):
        calls["load"] += 1
        return [TicketmasterEvent(rank=1, title="Theme-safe event")]

    monkeypatch.setattr(plugin, "_load_events", fake_load)
    monkeypatch.setattr(plugin, "_download_event_images", lambda _events: None)
    monkeypatch.setattr(plugin, "_write_ticketmaster_context", lambda *_args: None)
    monkeypatch.setattr(
        plugin,
        "_render_events",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )

    settings = {
        "apiKey": "test-key",
        "cacheHours": "1",
        "_inkypi_theme": _canonical_theme("day"),
    }
    plugin.generate_image(settings, DummyDeviceConfig())
    cache = plugin._read_cache()
    cache["generated_at"] = "2000-01-01T00:00:00+00:00"
    plugin._write_cache(cache)

    plugin.generate_image(
        {
            **settings,
            "_inkypi_theme": _canonical_theme("night"),
            "_theme_render_only": True,
        },
        DummyDeviceConfig(),
    )

    assert calls == {"load": 1}


@pytest.mark.parametrize("force_key", ["forceRefresh", "force_refresh"])
def test_force_refresh_bypasses_fresh_source_cache(monkeypatch, tmp_path, force_key):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    calls = {"load": 0}

    def fake_load(_settings, _items_count, _api_key):
        calls["load"] += 1
        return [TicketmasterEvent(rank=1, title=f"Refresh {calls['load']}")]

    monkeypatch.setattr(plugin, "_load_events", fake_load)
    monkeypatch.setattr(plugin, "_download_event_images", lambda _events: None)
    monkeypatch.setattr(plugin, "_write_ticketmaster_context", lambda *_args: None)
    monkeypatch.setattr(
        plugin,
        "_render_events",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )

    settings = {"apiKey": "test-key", "cacheHours": "3"}
    plugin.generate_image(settings, DummyDeviceConfig())
    plugin.generate_image(
        {**settings, force_key: True},
        DummyDeviceConfig(),
    )

    assert calls == {"load": 2}


def test_refresh_label_uses_configured_cache_hours():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    assert plugin._refresh_label({"cacheHours": "12"}) == "12H REFRESH"


def test_context_ttl_and_fact_use_configured_cache_hours(monkeypatch):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    captured = {}

    def fake_write_context(kind, payload, *, generated_at, ttl_seconds):
        captured.update(
            kind=kind,
            payload=payload,
            generated_at=generated_at,
            ttl_seconds=ttl_seconds,
        )

    monkeypatch.setattr(ticketmaster_module, "write_context", fake_write_context)
    generated_at = datetime(2026, 7, 13, tzinfo=timezone.utc)

    plugin._write_ticketmaster_context(
        [TicketmasterEvent(rank=1, title="Context event")],
        generated_at,
        False,
        {"cacheHours": "12"},
    )

    facts = {item["label"]: item["value"] for item in captured["payload"]["facts"]}
    assert facts["cache_hours"] == "12"
    assert captured["ttl_seconds"] == 12 * 60 * 60


def test_poster_download_uses_owned_bounded_image_decoder(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    payload = BytesIO()
    Image.new("RGB", (40, 24), "blue").save(payload, format="JPEG")

    class FakeResponse:
        content = payload.getvalue()

        def raise_for_status(self):
            return None

    response = FakeResponse()
    request_kwargs = {}

    class FakeSession:
        def get(self, _url, **kwargs):
            request_kwargs.update(kwargs)
            return response

    decoded = []

    def fake_safe_open_image_response(received):
        decoded.append(received)
        return Image.new("RGB", (40, 24), "green")

    monkeypatch.setattr(ticketmaster_module, "get_http_session", lambda: FakeSession())
    monkeypatch.setattr(
        ticketmaster_module,
        "safe_open_image_response",
        fake_safe_open_image_response,
        raising=False,
    )
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    event = TicketmasterEvent(
        rank=1,
        title="Bounded poster",
        image_url="https://example.test/poster.jpg",
    )

    plugin._download_event_images([event])

    assert decoded == [response]
    assert request_kwargs["stream"] is True
    assert Path(event.poster_path).is_file()


def test_one_bad_poster_does_not_block_later_posters(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    responses = [object(), object()]

    class FakeSession:
        def get(self, _url, **_kwargs):
            return responses.pop(0)

    decode_calls = {"count": 0}

    def fake_safe_open_image_response(_response):
        decode_calls["count"] += 1
        if decode_calls["count"] == 1:
            raise ValueError("bad image")
        return Image.new("RGB", (40, 24), "green")

    monkeypatch.setattr(ticketmaster_module, "get_http_session", lambda: FakeSession())
    monkeypatch.setattr(
        ticketmaster_module,
        "safe_open_image_response",
        fake_safe_open_image_response,
    )
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    events = [
        TicketmasterEvent(rank=1, title="Bad", image_url="https://example.test/bad"),
        TicketmasterEvent(rank=2, title="Good", image_url="https://example.test/good"),
    ]

    plugin._download_event_images(events)

    assert events[0].poster_path == ""
    assert Path(events[1].poster_path).is_file()


def test_ticketmaster_state_json_uses_single_file_managed_namespace(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    payload = {
        "version": "test",
        "cache_key": "key",
        "generated_at": "2026-07-13T00:00:00+00:00",
        "events": [],
    }

    plugin._write_cache(payload)
    namespace = plugin._state_cache_namespace()

    assert plugin._read_cache() == payload
    assert namespace.budget.max_files == 1
    assert namespace.budget.max_bytes <= 512 * 1024
    assert namespace.status().files == 1
    assert namespace.status().bytes <= namespace.budget.max_bytes


def test_ticketmaster_poster_namespace_evicts_by_count_and_bytes(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    namespace = plugin._poster_cache_namespace()
    for index in range(namespace.budget.max_files + 5):
        namespace.put_bytes(f"poster-{index:03d}", b"x", suffix=".jpg")

    status = namespace.status()
    assert status.files == namespace.budget.max_files
    assert status.bytes <= namespace.budget.max_bytes


def test_refresh_failure_uses_matching_stale_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    settings = {"apiKey": "test-key", "cacheHours": "3"}
    cache_key = plugin._cache_key(settings, (800, 480), 5)
    plugin._write_cache(
        {
            "version": ticketmaster_module.STATE_VERSION,
            "cache_key": cache_key,
            "generated_at": "2026-07-13T00:00:00+00:00",
            "events": [
                TicketmasterEvent(rank=1, title="Last good event").to_dict()
            ],
        }
    )
    rendered = {}

    monkeypatch.setattr(
        plugin,
        "_load_events",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("provider down")),
    )
    monkeypatch.setattr(plugin, "_write_ticketmaster_context", lambda *_args: None)

    def fake_render(_dimensions, events, _settings, _source, _generated, stale):
        rendered["titles"] = [event.title for event in events]
        rendered["stale"] = stale
        return Image.new("RGB", (800, 480), "white")

    monkeypatch.setattr(plugin, "_render_events", fake_render)

    plugin.generate_image(
        {**settings, "forceRefresh": True},
        DummyDeviceConfig(),
    )

    assert rendered == {"titles": ["Last good event"], "stale": True}


def test_refresh_failure_never_logs_api_key(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    secret = "credential-must-not-reach-logs"

    monkeypatch.setattr(
        plugin,
        "_load_events",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError(f"provider URL contained apikey={secret}")
        ),
    )

    plugin.generate_image(
        {"apiKey": secret, "forceRefresh": True},
        DummyDeviceConfig(),
    )

    assert secret not in caplog.text
    assert "error_type: RuntimeError" in caplog.text


def test_missing_key_without_cache_never_calls_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    prompt = {}

    monkeypatch.setattr(
        plugin,
        "_load_events",
        lambda *_args: (_ for _ in ()).throw(AssertionError("provider called")),
    )

    def fake_prompt(dimensions, title, subtitle):
        prompt.update(title=title, subtitle=subtitle)
        return Image.new("RGB", dimensions, "white")

    monkeypatch.setattr(plugin, "_config_image", fake_prompt)

    plugin.generate_image({}, DummyDeviceConfig())

    assert prompt["title"] == "Ticketmaster API key required"
    assert "TICKETMASTER_API_KEY" in prompt["subtitle"]


def test_render_events_smoke(monkeypatch, tmp_path):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    poster = tmp_path / "poster.jpg"
    Image.new("RGB", (640, 360), (180, 180, 170)).save(poster)
    events = [
        TicketmasterEvent(rank=1, title="Sample Artist Live", local_date="2026-07-04", local_time="19:30:00", venue_name="The Fillmore", city="San Francisco", state_code="CA", segment="Music", genre="Rock", status="onsale", price="$45-120", poster_path=str(poster)),
        TicketmasterEvent(rank=2, title="Night Market", local_date="2026-07-05", local_time="18:00:00", venue_name="Civic Center", city="San Francisco", state_code="CA", segment="Miscellaneous", genre="Festival"),
        TicketmasterEvent(rank=3, title="Bay FC Match", local_date="2026-07-06", local_time="15:00:00", venue_name="PayPal Park", city="San Jose", state_code="CA", segment="Sports", genre="Soccer"),
        TicketmasterEvent(rank=4, title="Comedy Showcase", local_date="2026-07-07", local_time="20:00:00", venue_name="Punch Line", city="San Francisco", state_code="CA", segment="Arts & Theatre", genre="Comedy"),
        TicketmasterEvent(rank=5, title="Symphony Night", local_date="2026-07-08", local_time="19:00:00", venue_name="Davies Symphony Hall", city="San Francisco", state_code="CA", segment="Music", genre="Classical"),
    ]

    image = plugin._render_events((800, 480), events, {}, "Ticketmaster Discovery", datetime.now(timezone.utc))

    assert image.size == (800, 480)
    assert image.getbbox() is not None


def test_generate_image_without_api_key_shows_configuration_prompt(monkeypatch, tmp_path):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))

    image = plugin.generate_image({}, DummyDeviceConfig())

    assert image.size == (800, 480)
    assert image.getbbox() is not None


def test_plugin_info_and_settings_defaults_are_declared():
    root = Path(__file__).resolve().parents[1] / "src" / "plugins" / "ticketmaster_events"
    info = json.loads((root / "plugin-info.json").read_text(encoding="utf-8"))
    settings = (root / "settings.html").read_text(encoding="utf-8")

    assert info["id"] == "ticketmaster_events"
    assert info["class"] == "TicketmasterEvents"
    assert "Ticketmaster Events" in info["display_name"]
    assert info["schema_version"] == 2
    assert info["refresh_on_display"] is False
    assert info["capabilities"] == {
        "supports_live_refresh": False,
        "supports_day_night_theme": True,
    }
    assert info["recommended_refresh"] == {"interval": 10_800}
    assert set(info["theme"]) == {"presentation", "day", "night"}
    assert (root / "header_wordmark.png").is_file()
    assert '<option value="3" selected>3 hours</option>' in settings
    assert 'name="themeMode"' not in settings
    assert "getElementById('themeMode')" not in settings
