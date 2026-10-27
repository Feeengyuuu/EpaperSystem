import json
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.ticketmaster_events.ticketmaster_events as ticketmaster_module  # noqa: E402
from plugins.ticketmaster_events.ticketmaster_events import (  # noqa: E402
    TicketmasterEvent,
    TicketmasterEvents,
)
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
)
from utils import cache_manager  # noqa: E402


class DummyDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, _key=None, default=None):
        return default

    def load_env_key(self, _key):
        return None


def test_paper_background_stays_solid_for_color_epaper():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = {"mode": "paper", "paper": (246, 241, 227), "line": (190, 184, 170)}
    image = Image.new("RGB", (96, 64), colors["paper"])

    plugin._draw_background(ImageDraw.Draw(image), image.size, colors)

    assert image.getcolors(maxcolors=image.width * image.height) == [
        (image.width * image.height, colors["paper"])
    ]


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

    events, _week, _on_sale = plugin._load_overview({"postalCode": "94539"}, "test-key",
                                                     datetime(2026, 7, 4, 9, 0, tzinfo=timezone.utc))

    assert events[0].title == "Sample Artist Live"
    assert calls[0]["postalCode"] == "94539"
    assert "postalCode" not in calls[1]
    assert calls[1]["latlong"] == "37.5202,-121.9264"
    # The other days and the on-sale list keep the parameters that answered.
    assert all(call.get("latlong") == "37.5202,-121.9264" for call in calls[2:])


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
    assert night["chromatic_media"] is True
    assert night["wordmark_shadow"] == night["accent"]
    assert night["wordmark_highlight"] == night["ink"]


def test_ticketmaster_night_theme_keeps_each_event_poster_color(tmp_path):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    poster_colors = (
        (212, 42, 38),
        (28, 172, 82),
        (18, 122, 220),
        (218, 132, 30),
        (156, 52, 204),
    )
    events = []

    for index, poster_color in enumerate(poster_colors):
        poster = tmp_path / f"poster-{index}.png"
        Image.new("RGB", (80, 48), poster_color).save(poster)
        events.append(TicketmasterEvent(
            rank=index + 1,
            title=f"Color event {index + 1}",
            local_date=f"2026-08-{index + 1:02d}",
            local_time="19:30:00",
            venue_name=f"Venue {index + 1}",
            city="San Francisco",
            state_code="CA",
            segment="Music",
            genre="Live",
            status="onsale",
            poster_path=str(poster),
        ))

    target = plugin._render_events(
        (800, 480),
        events,
        {"_inkypi_theme": _canonical_theme("night")},
        "Ticketmaster Discovery",
        datetime.now(timezone.utc),
    )
    poster_samples = (
        (170, 140),
        (442, 116),
        (442, 163),
        (442, 210),
        (442, 257),
    )

    for sample, poster_color in zip(poster_samples, poster_colors):
        rendered = target.getpixel(sample)
        assert max(rendered) - min(rendered) > 45
        assert rendered.index(max(rendered)) == poster_color.index(max(poster_color))

    colors = plugin._palette({"_inkypi_theme": _canonical_theme("night")})
    placeholder = plugin._placeholder_event_image(
        TicketmasterEvent(rank=6, title="Missing poster", segment="Sports"),
        (80, 48),
        colors,
    )
    assert any(
        max(placeholder.getpixel((x, y))) - min(placeholder.getpixel((x, y))) > 25
        for y in range(placeholder.height)
        for x in range(placeholder.width)
    )


def test_ticketmaster_night_wordmark_uses_readable_gold_and_cream_tones():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({"_inkypi_theme": _canonical_theme("night")})
    target = Image.new("RGB", (360, 64), colors["paper"])

    bottom = plugin._draw_header_wordmark(
        target,
        ImageDraw.Draw(target),
        0,
        0,
        320,
        48,
        colors,
    )

    assert bottom is not None
    visible = [
        pixel
        for pixel in target.get_flattened_data()
        if pixel != colors["paper"]
    ]
    assert visible
    assert any(
        red > green + 20 and green > blue + 35
        for red, green, blue in visible
    )
    assert any(
        max(pixel) - min(pixel) < 18 and min(pixel) > 180
        for pixel in visible
    )
    assert sum(max(pixel) >= 90 for pixel in visible) / len(visible) > 0.8


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

    def fake_load(_settings, _api_key, _now):
        calls["load"] += 1
        return [TicketmasterEvent(rank=1, title="Theme-safe event")], {}, []

    monkeypatch.setattr(plugin, "_load_overview", fake_load)
    monkeypatch.setattr(
        plugin,
        "_download_event_images",
        lambda _events, _dimensions: None,
    )
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

    def fake_load(_settings, _api_key, _now):
        calls["load"] += 1
        return [TicketmasterEvent(rank=1, title=f"Refresh {calls['load']}")], {}, []

    monkeypatch.setattr(plugin, "_load_overview", fake_load)
    monkeypatch.setattr(
        plugin,
        "_download_event_images",
        lambda _events, _dimensions: None,
    )
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

    def fake_safe_open_image_response(received, *, draft_size=None):
        decoded.append((received, draft_size))
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

    plugin._download_event_images([event], (800, 480))

    assert decoded == [(response, (800, 480))]
    assert request_kwargs["stream"] is True
    assert Path(event.poster_path).is_file()


def test_cold_poster_is_drafted_and_cached_within_display_dimensions(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    response = object()

    class FakeSession:
        def get(self, _url, **_kwargs):
            return response

    decoded = {}

    def fake_safe_open_image_response(received, *, draft_size=None):
        decoded["response"] = received
        decoded["draft_size"] = draft_size
        return Image.new("RGB", (1600, 960), (18, 122, 220))

    monkeypatch.setattr(ticketmaster_module, "get_http_session", lambda: FakeSession())
    monkeypatch.setattr(
        ticketmaster_module,
        "safe_open_image_response",
        fake_safe_open_image_response,
    )
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    event = TicketmasterEvent(
        rank=1,
        title="Large poster",
        image_url="https://example.test/large-poster.jpg",
    )

    plugin._download_event_images([event], (800, 480))

    with Image.open(event.poster_path) as cached:
        assert cached.format == "JPEG"
        assert cached.width <= 800
        assert cached.height <= 480
    assert decoded == {
        "response": response,
        "draft_size": (800, 480),
    }


def test_poster_cache_hit_uses_managed_path_without_reading_bytes(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    event = TicketmasterEvent(
        rank=1,
        title="Cached poster",
        image_url="https://example.test/cached-poster.jpg",
    )
    namespace = plugin._poster_cache_namespace()
    key = plugin._event_image_cache_key(event)
    expected = namespace.put_bytes(key, b"cached jpeg", suffix=".jpg")

    def fail_read(_path):
        raise AssertionError("poster cache hits must not read the payload")

    def fail_http():
        raise AssertionError("poster cache hits must not use the network")

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    monkeypatch.setattr(ticketmaster_module, "get_http_session", fail_http)

    plugin._download_event_images([event], (800, 480))

    assert event.poster_path == str(expected)


def test_zero_byte_poster_cache_entry_is_replaced_from_network(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    event = TicketmasterEvent(
        rank=1,
        title="Empty cached poster",
        image_url="https://example.test/replacement-poster.jpg",
    )
    namespace = plugin._poster_cache_namespace()
    key = plugin._event_image_cache_key(event)
    target = namespace.put_bytes(key, b"", suffix=".jpg")
    response = object()
    requests = []

    class FakeSession:
        def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return response

    def fake_safe_open_image_response(received, *, draft_size=None):
        assert received is response
        assert draft_size == (800, 480)
        return Image.new("RGB", (80, 48), "green")

    monkeypatch.setattr(ticketmaster_module, "get_http_session", lambda: FakeSession())
    monkeypatch.setattr(
        ticketmaster_module,
        "safe_open_image_response",
        fake_safe_open_image_response,
    )

    plugin._download_event_images([event], (800, 480))

    assert len(requests) == 1
    assert event.poster_path == str(target)
    with Image.open(target) as cached:
        assert cached.size == (80, 48)


def test_one_bad_poster_does_not_block_later_posters(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    responses = [object(), object()]

    class FakeSession:
        def get(self, _url, **_kwargs):
            return responses.pop(0)

    decode_calls = {"count": 0}

    def fake_safe_open_image_response(_response, *, draft_size=None):
        decode_calls["count"] += 1
        if decode_calls["count"] == 1:
            raise ValueError("bad image")
        assert draft_size == (800, 480)
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

    plugin._download_event_images(events, (800, 480))

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


def test_runtime_cache_manager_accepts_ticketmaster_state_and_image_namespaces(
    monkeypatch,
    tmp_path,
):
    runtime_root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(runtime_root))
    monkeypatch.delenv("INKYPI_TICKETMASTER_EVENTS_CACHE", raising=False)
    monkeypatch.setattr(
        cache_manager,
        "_GLOBAL_MANAGER",
        cache_manager.CacheManager(SimpleNamespace(cache_dir=runtime_root)),
    )
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    rendered = {}

    monkeypatch.setattr(
        plugin,
        "_load_overview",
        lambda *_args: ([TicketmasterEvent(rank=1, title="Runtime event")], {}, []),
    )
    monkeypatch.setattr(plugin, "_write_ticketmaster_context", lambda *_args: None)

    def fake_render(_dimensions, events, *_args, **_kwargs):
        rendered["titles"] = [event.title for event in events]
        return Image.new("RGB", (800, 480), "white")

    monkeypatch.setattr(plugin, "_render_events", fake_render)

    image = plugin.generate_image(
        {"apiKey": "test-key", "forceRefresh": True},
        DummyDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert rendered == {"titles": ["Runtime event"]}
    assert len(plugin._read_cache()["events"]) == 1
    assert plugin._state_cache_namespace().status().files == 1
    assert plugin._poster_cache_namespace().root.is_dir()


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
    cache_key = plugin._cache_key(settings, (800, 480), ticketmaster_module.LIST_EVENTS)
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
        "_load_overview",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("provider down")),
    )
    monkeypatch.setattr(plugin, "_write_ticketmaster_context", lambda *_args: None)

    def fake_render(_dimensions, events, _settings, _source, _generated, stale, **_overview):
        rendered["titles"] = [event.title for event in events]
        rendered["stale"] = stale
        return Image.new("RGB", (800, 480), "white")

    monkeypatch.setattr(plugin, "_render_events", fake_render)

    image = plugin.generate_image(
        {**settings, "forceRefresh": True},
        DummyDeviceConfig(),
    )

    assert rendered == {"titles": ["Last good event"], "stale": True}
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True


def test_refresh_failure_never_logs_api_key(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    secret = "credential-must-not-reach-logs"

    monkeypatch.setattr(
        plugin,
        "_load_overview",
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
        "_load_overview",
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


def test_event_without_a_poster_shows_its_segments_generated_artwork():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({"_inkypi_theme": _canonical_theme("day")})

    def artwork(segment, event_id):
        event = TicketmasterEvent(rank=1, title="No poster", segment=segment, event_id=event_id)
        return plugin._placeholder_event_image(event, (80, 48), colors)

    sports = artwork("Sports", "a")
    assert sports.size == (768, 512)
    assert artwork("Sports", "b").tobytes() == sports.tobytes()
    music = {artwork("Music", f"event-{index}").tobytes() for index in range(12)}
    assert len(music) == 2
    assert artwork("Undefined", "x").tobytes() == artwork("Miscellaneous", "y").tobytes()


def _day_payload(total, segments, start="10:00:00", day="2026-10-25"):
    rows = []
    for index, segment in enumerate(segments):
        rows.append({"id": f"{day}-{index}", "name": f"{segment} show {index}", "distance": 5 + index,
                     "images": [{"url": f"https://s1.ticketm.net/dam/a/{day}-{index}.jpg", "ratio": "16_9",
                                 "width": 1024, "height": 576}],
                     "dates": {"start": {"localDate": day, "localTime": start}, "status": {"code": "onsale"}},
                     "classifications": [{"segment": {"name": segment}}],
                     "_embedded": {"venues": [{"name": "Venue", "city": {"name": "San Jose"}}],
                                   "attractions": [{"name": "Act", "upcomingEvents": {"_total": 3 + 10 * index}}]}})
    return {"_embedded": {"events": rows}, "page": {"totalElements": total}}


def test_the_week_loader_counts_each_day_and_lists_sales_opening_soon():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    now = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)
    calls = []

    def fake_fetch(params):
        calls.append(dict(params))
        if "onsaleStartDateTime" in params:
            return {"_embedded": {"events": [
                {"name": "Big Tour", "sales": {"public": {"startDateTime": "2026-10-28T17:00:00Z"}},
                 "dates": {"start": {"localDate": "2027-03-01"}}, "classifications": [{"segment": {"name": "Music"}}],
                 "_embedded": {"venues": [{"name": "Chase Center", "city": {"name": "San Francisco"}}]}},
                {"name": "Already Open", "sales": {"public": {"startDateTime": "2026-10-20T17:00:00Z"}}}]}}
        day = params["localStartDateTime"][:10]
        return _day_payload(40 if day == "2026-10-25" else 12, ["Music", "Sports", "Music"], day=day)

    plugin._fetch_ticketmaster_json = fake_fetch

    events, week, on_sale = plugin._load_overview({}, "key", now)

    days = [call["localStartDateTime"] for call in calls if "localStartDateTime" in call]
    # Today counts from now on; the next six days whole.
    assert days[0] == "2026-10-25T09:30:00,2026-10-25T23:59:59" and days[1] == "2026-10-26T00:00:00,2026-10-26T23:59:59"
    assert len(days) == 7 and all("startDateTime" not in call for call in calls)
    assert [day["count"] for day in week["days"]] == [40, 12, 12, 12, 12, 12, 12]
    assert week["days"][0]["segments"] == {"music": 2, "sports": 1}
    assert week["days"][0]["kind_counts"] == {"music|music": 2, "sports|sports": 1}
    assert week["days"][0]["sample_count"] == 3
    # The featured event is the biggest tour starting within two days; the rest follow by time.
    assert events[0].extra["tour_dates"] == 23 and len(events) == ticketmaster_module.LIST_EVENTS
    assert [event.rank for event in events] == list(range(1, len(events) + 1))
    assert on_sale == [{"title": "Big Tour", "venue": "Chase Center", "city": "San Francisco", "date": "2027-03-01",
                        "sale_start": "2026-10-28T17:00:00Z", "segment": "music", "kind": "music|music"}]
    assert calls[-1]["sort"] == "onSaleStartDate,asc" and "localStartDateTime" not in calls[-1]


def test_a_later_day_that_fails_is_left_out_but_today_failing_fails_the_refresh():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    now = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)

    def flaky(params):
        if params.get("localStartDateTime", "").startswith("2026-10-27"):
            raise RuntimeError("timeout")
        return _day_payload(5, ["Music"], day=params.get("localStartDateTime", "2026-10-25")[:10])

    plugin._fetch_ticketmaster_json = flaky
    _events, week, _on_sale = plugin._load_overview({}, "key", now)
    assert [day["count"] for day in week["days"]][:3] == [5, 5, None]

    plugin._fetch_ticketmaster_json = lambda params: (_ for _ in ()).throw(RuntimeError("down"))
    with pytest.raises(RuntimeError):
        plugin._load_overview({}, "key", now)


@pytest.mark.parametrize("successful_location", ["postalCode", "latlong"])
@pytest.mark.parametrize("empty_embedded", [False, True])
def test_an_empty_evening_retries_the_preferred_location_tomorrow(successful_location, empty_embedded):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    now = datetime(2026, 10, 26, 20, 42, 12, tzinfo=ZoneInfo("America/Los_Angeles"))
    calls = []
    empty = {"_embedded": {"events": []}, "page": {"totalElements": 0}} if empty_embedded else {"page": {"totalElements": 0}}

    def fetch(params):
        calls.append(dict(params))
        if "onsaleStartDateTime" in params:
            return empty
        day = params["localStartDateTime"][:10]
        if day == "2026-10-26" or successful_location not in params:
            return empty
        payload = _day_payload(1, ["Music"], day=day)
        payload["_embedded"]["events"][0]["classifications"][0]["genre"] = {"name": "Rock"}
        return payload

    plugin._fetch_ticketmaster_json = fetch
    events, week, on_sale = plugin._load_overview({"postalCode": "94539", "city": "Fremont"}, "test-key", now)

    day_calls = [call for call in calls if "localStartDateTime" in call]
    assert [next(key for key in ("postalCode", "latlong", "city") if key in call) for call in day_calls[:3]] == [
        "postalCode", "latlong", "city",
    ]
    assert all(call["localStartDateTime"] == "2026-10-26T20:42:00,2026-10-26T23:59:59" for call in day_calls[:3])
    assert "postalCode" in day_calls[3]
    assert day_calls[3]["localStartDateTime"] == "2026-10-27T00:00:00,2026-10-27T23:59:59"
    successful_index = 3 if successful_location == "postalCode" else 4
    assert all(successful_location in call and "city" not in call for call in day_calls[successful_index:])
    assert len(day_calls) == 9 + (successful_location == "latlong")
    assert len(calls) == len(day_calls) + 1 and successful_location in calls[-1]
    assert [day["count"] for day in week["days"]] == [0, 1, 1, 1, 1, 1, 1]
    assert week["days"][0]["kind_counts"] == {}
    assert week["days"][1]["kind_counts"] == {"music|rock": 1}
    assert len(events) == 6 and all(event.local_date > "2026-10-26" for event in events)
    assert on_sale == []


def test_a_whole_empty_week_keeps_the_existing_no_events_failure():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    calls = []

    def fetch(params):
        calls.append(dict(params))
        return {"page": {"totalElements": 0}}

    plugin._fetch_ticketmaster_json = fetch
    with pytest.raises(RuntimeError, match="returned no displayable events"):
        plugin._load_overview({}, "test-key", datetime(2026, 10, 26, 20, 42, tzinfo=timezone.utc))
    assert len(calls) == ticketmaster_module.WEEK_DAYS * 3
    assert all("onsaleStartDateTime" not in call for call in calls)


def test_the_page_renders_the_week_band_and_the_sales_opening_soon(monkeypatch, tmp_path):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    poster = tmp_path / "poster.jpg"
    Image.new("RGB", (640, 360), (200, 40, 40)).save(poster)
    now = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)
    events = [TicketmasterEvent(rank=index + 1, title=f"Show {index}", local_date="2026-10-25",
                                local_time="19:30:00", venue_name="Venue", city="San Jose", segment="Music",
                                status="onsale", poster_path=str(poster)) for index in range(8)]
    week = {"days": [{"date": f"2026-10-{25 + index}", "count": 10 + index, "segments": {"music": 3, "sports": 1}}
                     for index in range(7)]}
    sales = [{"title": "Big Tour", "sale_start": "2026-10-28T17:00:00Z", "segment": "music"}]
    colors = plugin._palette({})

    image = plugin._render_events((800, 480), events, {}, "Ticketmaster Discovery", now, week=week, on_sale=sales)
    plain = plugin._render_events((800, 480), events, {}, "Ticketmaster Discovery", now)

    assert image.size == (800, 480)
    # The page lies on the featured poster's colour; the plain background has none to lie on.
    assert image.getpixel((330, 300)) != plain.getpixel((330, 300)) or image.getpixel((5, 300)) != colors["paper"]
    # Today's cell in the week band is outlined in the accent colour.
    from plugins.ticketmaster_events.week_page import WEEK_BAND

    x0, y0, _x1, _y1 = WEEK_BAND
    assert colors["accent"] in {image.getpixel((x, y0 + 3)) for x in range(x0 + 4, x0 + 100)}


def test_the_featured_title_parts_the_headliner_from_the_rest_of_the_bill():
    from plugins.ticketmaster_events.week_page import title_parts

    assert title_parts("Lenny Pearce - Toddler Rave World Tour") == ("Lenny Pearce", "Toddler Rave World Tour")
    assert title_parts("Tyler, The Creator: CHROMAKOPIA") == ("Tyler, The Creator", "CHROMAKOPIA")
    assert title_parts("Inner Wave w/ Heathers") == ("Inner Wave", "w/ Heathers")
    assert title_parts("Disney On Ice presents Let's Dance!") == ("Disney On Ice", "Let's Dance!")
    # A title naming nothing more, or a break at its very edge, stays whole.
    assert title_parts("Golden State Warriors vs. Memphis Grizzlies") == (
        "Golden State Warriors vs. Memphis Grizzlies", "")
    assert title_parts("Doors 7:30 PM") == ("Doors 7:30 PM", "")
    assert title_parts("A - B") == ("A - B", "")


def test_a_venue_named_after_its_city_says_the_city_once():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    def place(venue, city):
        return plugin._place_line(TicketmasterEvent(rank=1, title="x", venue_name=venue, city=city))

    assert place("Fox Theater - Oakland", "Oakland") == "Fox Theater · Oakland"
    assert place("SAP Center at San Jose", "San Jose") == "SAP Center · San Jose"
    assert place("Oakland Arena", "Oakland") == "Oakland Arena · Oakland"
    assert place("Oakland", "Oakland") == "Oakland · Oakland"
    assert place("", "Berkeley") == "地点待定 · Berkeley"


def test_the_featured_poster_takes_the_room_its_title_leaves():
    from plugins.ticketmaster_events import week_page

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({})
    feet = []
    for title in ("Lenny Pearce - Toddler Rave World Tour", "Golden State Warriors vs. Memphis Grizzlies Tonight"):
        image = Image.new("RGB", (800, 480), colors["paper"])
        event = TicketmasterEvent(rank=1, title=title, local_date="2026-10-27", local_time="18:00:00",
                                  venue_name="Fox Theater", city="Oakland", segment="Music", distance="27.4 mi")
        feet.append(plugin._draw_featured(image, ImageDraw.Draw(image), event, datetime(2026, 10, 26).date(), colors))

    # The text ends where the section under it still has its full room, whatever the title's lines.
    bottom = week_page.HERO[3] - 8 - week_page.SECTION_H - 16
    assert all(abs(foot - bottom) <= 4 for foot in feet)


def test_an_age_limit_after_the_title_is_a_chip_not_the_subtitle():
    from plugins.ticketmaster_events.week_page import age_notice

    assert age_notice("Ages 14+ (No children under 5 allowed)") == "14+"
    assert age_notice("21+ Show") == "21+"
    assert age_notice("All Ages") == "全龄"
    assert age_notice("Toddler Rave World Tour") == age_notice("Tour 2026") == ""

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({})
    drawn = []

    class Pen:
        def __getattr__(self, name):
            draw = ImageDraw.Draw(image)
            if name != "text":
                return getattr(draw, name)
            return lambda xy, text, **kwargs: (drawn.append(text), draw.text(xy, text, **kwargs))

    image = Image.new("RGB", (800, 480), colors["paper"])
    event = TicketmasterEvent(rank=1, title="Oh, Mary! (Touring) - Ages 14+ (No children under 5 allowed)",
                              local_date="2026-10-27", local_time="19:00:00", venue_name="Curran Theatre",
                              city="San Francisco", segment="Arts & Theatre", genre="Theatre")
    plugin._draw_featured(image, Pen(), event, datetime(2026, 10, 26).date(), colors)

    assert "Oh, Mary! (Touring)" in drawn and "14+" in drawn
    assert not any("No children" in text for text in drawn)
