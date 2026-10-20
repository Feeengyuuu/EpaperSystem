import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw
import pytest

import plugins.live_radar.live_radar as live_radar_module
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import PresentationMode
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    read_source_provenance,
)
from plugins.live_radar.live_radar import (
    DEFAULT_ROOMS_TEXT,
    HEADER_ART_FILE,
    HEADER_ART_SIZE,
    LIVE_STATUS_DOT,
    SECTION_TITLE_WORDMARK_FILES,
    SECTION_TITLE_WORDMARK_SIZE,
    SECTION_TITLE_WORDMARK_SIZES,
    TITLE_WORDMARK_FILE,
    TITLE_WORDMARK_OFFSET_X,
    TITLE_WORDMARK_SIZE,
    STATUS_TOTAL_DARK_OFFLINE_FILL,
    STATUS_TOTAL_FILLS,
    TITLE_LOGO_SCALE,
    LiveRadar,
)
from runtime.refresh_contracts import freeze_payload


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), orientation="horizontal", mode="night"):
        self.resolution = resolution
        self.orientation = orientation
        self.mode = mode

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"orientation": self.orientation, "theme": self.mode}
        if key is None:
            return values
        return values.get(key, default)


def _plugin():
    plugin = LiveRadar({"id": "live_radar"})
    plugin._write_context = lambda *args, **kwargs: None
    plugin._write_source_health = lambda *args, **kwargs: None
    return plugin


def _memory_cache(plugin):
    cache = {}
    plugin._read_cache = lambda key: cache.get(key, {})
    plugin._write_cache = lambda key, data: cache.__setitem__(key, data)
    return cache


def _canonical_theme(mode, *, background, panel, ink, muted, rule, accent):
    palette = {
        "background": background,
        "panel": panel,
        "ink": ink,
        "muted": muted,
        "rule": rule,
        "accent": accent,
    }
    return {"mode": mode, "palette": palette, "css": {}}


def _status_result(*, ok=True, is_live=True):
    return {
        "ok": ok,
        "platform": "twitch",
        "id": "xqc",
        "error": "provider unavailable" if not ok else "",
        "status": {
            "isLive": is_live,
            "owner": "xQc",
            "title": "Live now" if is_live else "Offline",
            "heatValue": 100 if is_live else 0,
        },
    }


def _tree_snapshot(root):
    root = Path(root)
    if not root.exists():
        return None
    return {
        path.relative_to(root).as_posix(): (None if path.is_dir() else path.read_bytes())
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
    }


def test_live_radar_declares_explicit_no_change_presentation_mode():
    plugin = _plugin()

    assert "presentation_mode" in LiveRadar.__dict__
    assert LiveRadar.__dict__["presentation_mode"] is not BasePlugin.presentation_mode
    assert plugin.presentation_mode({"forceRefresh": True}) is PresentationMode.NO_CHANGE


def test_live_radar_requests_live_refresh_using_shortest_internal_ttl(
    monkeypatch,
    tmp_path,
):
    cache_root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(cache_root))
    plugin = _plugin()
    settings = {
        "roomsText": "twitch|xqc|xQc",
        "apiUrl": "https://provider.invalid/status",
        "fetchAvatars": False,
        "cacheSeconds": 90,
        "snapshotCacheSeconds": 60,
    }
    rooms = plugin._parse_rooms(settings)
    cache_key = plugin._cache_key(rooms, settings["apiUrl"], False)
    cache_path = plugin.cache_dir(leaf="cache") / f"{cache_key}.json"
    cache_path.write_text(
        json.dumps({"fetched_at": 1, "results": [_status_result()]}),
        encoding="utf-8",
    )

    assert LiveRadar.get_live_refresh_state is not BasePlugin.get_live_refresh_state
    assert plugin.get_live_refresh_state(settings, datetime.now(timezone.utc)) == {
        "active": True,
        "interval_seconds": 60,
    }


def test_live_refresh_state_stays_active_and_side_effect_free_at_status_cache_boundary(
    monkeypatch,
    tmp_path,
):
    cache_root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(cache_root))
    plugin = _plugin()
    settings = {
        "roomsText": "twitch|xqc|xQc",
        "apiUrl": "https://provider.invalid/status",
        "fetchAvatars": False,
    }
    current_dt = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    rooms = plugin._parse_rooms(settings)
    cache_key = plugin._cache_key(rooms, settings["apiUrl"], False)
    cache_path = plugin.cache_dir(leaf="cache") / f"{cache_key}.json"
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": current_dt.timestamp() - 59,
                "results": [_status_result()],
            }
        ),
        encoding="utf-8",
    )
    plugin._fetch_statuses = lambda *_args, **_kwargs: pytest.fail("live hook called provider")
    plugin._write_cache = lambda *_args, **_kwargs: pytest.fail("live hook wrote cache")

    assert plugin.get_live_refresh_state(settings, current_dt) == {
        "active": True,
        "interval_seconds": 60,
    }

    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": current_dt.timestamp() - 60,
                "results": [_status_result()],
            }
        ),
        encoding="utf-8",
    )
    before = _tree_snapshot(cache_root)

    assert plugin.get_live_refresh_state(settings, current_dt) == {
        "active": True,
        "interval_seconds": 60,
    }
    assert _tree_snapshot(cache_root) == before


def test_live_refresh_state_is_inactive_and_side_effect_free_without_rooms_or_cache(
    monkeypatch,
    tmp_path,
):
    cache_root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(cache_root))
    plugin = _plugin()
    plugin._fetch_statuses = lambda *_args, **_kwargs: pytest.fail("live hook called provider")
    plugin._write_cache = lambda *_args, **_kwargs: pytest.fail("live hook wrote cache")
    current_dt = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)

    assert plugin.get_live_refresh_state({}, current_dt) is None
    assert not cache_root.exists()
    assert (
        plugin.get_live_refresh_state(
            {"roomsText": "twitch|xqc|xQc", "fetchAvatars": False},
            current_dt,
        )
        is None
    )
    assert not cache_root.exists()


@pytest.mark.parametrize(
    "settings",
    [
        {"roomsJson": "[]"},
        {"roomsJson": "not-json"},
        {"roomsJson": "null"},
        {"roomsJson": '{"rooms": {}}'},
        {"roomsJson": "1"},
        {"roomsJson": []},
        {"roomsText": "   "},
    ],
    ids=[
        "empty-array",
        "invalid-json",
        "json-null",
        "wrong-rooms-type",
        "wrong-root-type",
        "native-empty-array",
        "empty-rooms-text",
    ],
)
def test_live_refresh_state_rejects_explicit_invalid_rooms_without_cache_access(
    monkeypatch,
    tmp_path,
    settings,
):
    cache_root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(cache_root))
    plugin = _plugin()
    plugin._read_cache_read_only = lambda *_args, **_kwargs: pytest.fail(
        "invalid explicit rooms reached cache lookup"
    )

    assert (
        plugin.get_live_refresh_state(
            settings,
            datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        )
        is None
    )
    assert not cache_root.exists()


@pytest.mark.parametrize(
    "settings",
    [
        {
            "roomsJson": json.dumps(
                [{"platform": "twitch", "id": "xqc", "label": "xQc"}]
            )
        },
        {"roomsJson": "[]", "roomsText": "twitch|xqc|xQc"},
        {"roomsJson": "not-json", "roomsText": "twitch|xqc|xQc"},
    ],
    ids=["valid-json", "empty-json-valid-text", "invalid-json-valid-text"],
)
def test_live_refresh_state_reads_explicit_valid_json_and_text_sources(settings):
    plugin = _plugin()
    current_dt = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    reads = []

    def read_cache(cache_key):
        reads.append(cache_key)
        return {
            "fetched_at": current_dt.timestamp() - 60,
            "results": [_status_result()],
        }

    plugin._read_cache_read_only = read_cache

    assert plugin.get_live_refresh_state(settings, current_dt) == {
        "active": True,
        "interval_seconds": 60,
    }
    assert len(reads) == 1


@pytest.mark.parametrize(
    "result",
    [
        _status_result(is_live=False),
        _status_result(ok=False, is_live=True),
    ],
    ids=["offline", "error"],
)
def test_live_refresh_state_is_inactive_without_a_successful_live_stream(
    monkeypatch,
    tmp_path,
    result,
):
    cache_root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(cache_root))
    plugin = _plugin()
    settings = {
        "roomsText": "twitch|xqc|xQc",
        "apiUrl": "https://provider.invalid/status",
        "fetchAvatars": False,
    }
    rooms = plugin._parse_rooms(settings)
    cache_key = plugin._cache_key(rooms, settings["apiUrl"], False)
    cache_path = plugin.cache_dir(leaf="cache") / f"{cache_key}.json"
    cache_path.write_text(
        json.dumps({"fetched_at": 1, "results": [result]}),
        encoding="utf-8",
    )

    assert plugin.get_live_refresh_state(settings, datetime.now(timezone.utc)) is None


@pytest.mark.parametrize(
    ("cache_age", "provider_result", "provider_error", "expected"),
    [
        (None, _status_result(), None, SourceProvenance.LIVE),
        (10, None, None, SourceProvenance.FRESH_CACHE),
        (120, None, RuntimeError("offline"), SourceProvenance.STALE_CACHE),
        (None, [_status_result(ok=False)], None, SourceProvenance.LOCAL_FALLBACK),
    ],
    ids=["live", "fresh-cache", "stale-cache", "error-only-provider"],
)
def test_generate_image_attests_source_health(
    monkeypatch,
    cache_age,
    provider_result,
    provider_error,
    expected,
):
    plugin = _plugin()
    cache = _memory_cache(plugin)
    settings = {
        "roomsText": "twitch|xqc|xQc",
        "cacheSeconds": 60,
        "fetchAvatars": False,
        "showSnapshots": False,
    }
    rooms = plugin._parse_rooms(settings)
    key = plugin._cache_key(rooms, live_radar_module.DEFAULT_API_URL, False)
    if cache_age is not None:
        cache[key] = {
            "fetched_at": time.time() - cache_age,
            "results": [_status_result()],
        }
    if provider_error is not None:
        plugin._fetch_statuses = lambda *_args, **_kwargs: (_ for _ in ()).throw(provider_error)
    elif provider_result is not None:
        results = provider_result if isinstance(provider_result, list) else [provider_result]
        plugin._fetch_statuses = lambda *_args, **_kwargs: results
    else:
        plugin._fetch_statuses = lambda *_args, **_kwargs: pytest.fail("fresh cache called provider")
    monkeypatch.setattr(
        plugin,
        "_render_dashboard",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )

    image = plugin.generate_image(settings, FakeDeviceConfig())

    assert read_source_provenance(image) is expected
    if expected is SourceProvenance.LOCAL_FALLBACK:
        assert cache == {}


def test_error_only_refresh_preserves_last_good_cache(monkeypatch):
    plugin = _plugin()
    cache = _memory_cache(plugin)
    settings = {
        "roomsText": "twitch|xqc|xQc",
        "cacheSeconds": 20,
        "fetchAvatars": False,
        "showSnapshots": False,
    }
    rooms = plugin._parse_rooms(settings)
    key = plugin._cache_key(rooms, live_radar_module.DEFAULT_API_URL, False)
    last_good = {
        "fetched_at": time.time() - 120,
        "results": [_status_result()],
    }
    cache[key] = last_good
    plugin._fetch_statuses = lambda *_args, **_kwargs: [_status_result(ok=False)]
    monkeypatch.setattr(
        plugin,
        "_render_dashboard",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )

    image = plugin.generate_image(settings, FakeDeviceConfig())

    assert cache[key] == last_good
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE


def test_status_cache_refetches_at_exact_internal_expiry(monkeypatch):
    plugin = _plugin()
    _memory_cache(plugin)
    now = [1000.0]
    fetch_times = []
    monkeypatch.setattr(live_radar_module.time, "time", lambda: now[0])
    plugin._fetch_statuses = lambda *_args: fetch_times.append(now[0]) or [_status_result()]
    monkeypatch.setattr(
        plugin,
        "_render_dashboard",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )
    settings = {
        "roomsText": "twitch|xqc|xQc",
        "cacheSeconds": "60",
        "showSnapshots": False,
    }

    plugin.generate_image(settings, FakeDeviceConfig())
    now[0] += 59
    plugin.generate_image(settings, FakeDeviceConfig())
    now[0] += 1
    plugin.generate_image(settings, FakeDeviceConfig())

    assert fetch_times == [1000.0, 1060.0]


def test_status_refresh_budget_stops_individual_fallback_before_next_interval():
    plugin = _plugin()
    now = [0.0]
    plugin._status_refresh_clock = lambda: now[0]
    plugin._status_refresh_deadline = 15.0
    calls = []

    class FakeSession:
        def post(self, url, json, timeout, headers):
            calls.append(([room["id"] for room in json["rooms"]], timeout))
            now[0] = 16.0
            raise RuntimeError("batch timeout")

    rooms = [
        {"platform": "douyu", "id": "1", "label": "One", "isFav": False},
        {"platform": "douyu", "id": "2", "label": "Two", "isFav": False},
    ]

    results = plugin._fetch_aggregator_platform(
        FakeSession(),
        rooms,
        "https://example.test/batch",
        20,
        False,
        "douyu",
    )

    assert calls == [(["1", "2"], 15.0)]
    assert [result["id"] for result in results] == ["1", "2"]
    assert all(result["ok"] is False for result in results)


def test_parse_rooms_text_accepts_card_lines():
    plugin = _plugin()

    rooms = plugin._parse_rooms(
        {
            "roomsText": """
            twitch|xqc|xQc|fav
            bilibili,545318,Mr.Quin
            kick:adinross
            douyu|60937|fav
            unknown|bad
            """
        }
    )

    assert rooms == [
        {"platform": "twitch", "id": "xqc", "label": "xQc", "isFav": True},
        {"platform": "bilibili", "id": "545318", "label": "Mr.Quin", "isFav": True},
        {"platform": "kick", "id": "adinross", "label": "", "isFav": False},
        {"platform": "douyu", "id": "60937", "label": "", "isFav": True},
    ]


def test_parse_rooms_json_prefers_liveradar_export_shape():
    plugin = _plugin()

    rooms = plugin._parse_rooms(
        {
            "roomsText": "twitch|ignored",
            "roomsJson": '{"rooms":[{"platform":"picarto","id":"artist","isFav":true}]}',
        }
    )

    assert rooms == [
        {"platform": "picarto", "id": "artist", "label": "", "isFav": True},
    ]


def test_yellow_border_favorites_are_pinned_with_mr_quin_first():
    plugin = _plugin()

    rooms = plugin._parse_rooms(
        {
            "roomsText": "\n".join(
                [
                    "douyu|3507497",
                    "douyu|60937",
                    "bilibili|545318",
                    "twitch|xqc",
                    "douyu|12306",
                ]
            )
        }
    )

    assert [room["isFav"] for room in rooms] == [True, True, True, False, True]

    cards = [
        {"platform": room["platform"], "id": room["id"], "status": "offline", "is_fav": room["isFav"], "favorite_rank": plugin._favorite_priority(room["platform"], room["id"]), "heat": 9999, "owner": room["id"]}
        for room in rooms
    ]
    sorted_cards = plugin._sort_cards(cards)

    assert sorted_cards[0]["id"] == "545318"
    assert [card["id"] for card in sorted_cards[:4]] == ["545318", "60937", "12306", "3507497"]


def test_default_rooms_match_latest_backup_and_favorite_order():
    plugin = _plugin()

    rooms = plugin._parse_rooms({"roomsText": DEFAULT_ROOMS_TEXT})

    assert len(rooms) == 66
    assert {
        platform: sum(room["platform"] == platform for room in rooms)
        for platform in ("bilibili", "douyu", "twitch")
    } == {"bilibili": 31, "douyu": 22, "twitch": 13}
    assert (rooms[0]["platform"], rooms[0]["id"]) == ("bilibili", "545318")
    assert (rooms[-1]["platform"], rooms[-1]["id"]) == ("douyu", "4067868")
    assert ("bilibili", "173551") in [(room["platform"], room["id"]) for room in rooms]
    assert ("twitch", "ludwig") in [(room["platform"], room["id"]) for room in rooms]
    cake = next(room for room in rooms if (room["platform"], room["id"]) == ("douyu", "4067868"))
    assert cake["label"] == ""
    assert cake["isFav"] is False
    assert ("bilibili", "30931147") not in [(room["platform"], room["id"]) for room in rooms]

    favorite_keys = [(room["platform"], room["id"]) for room in rooms if room["isFav"]]
    assert favorite_keys == [
        ("bilibili", "545318"),
        ("douyu", "6979222"),
        ("douyu", "60937"),
        ("douyu", "12306"),
        ("douyu", "57321"),
        ("bilibili", "5229"),
        ("twitch", "jinnytty"),
        ("douyu", "10639765"),
        ("douyu", "3507497"),
        ("bilibili", "733"),
    ]
    assert ("bilibili", "7586498") not in favorite_keys
    assert ("bilibili", "173551") not in favorite_keys

    favorite_cards = [
        {
            "platform": room["platform"],
            "id": room["id"],
            "status": "offline",
            "is_fav": room["isFav"],
            "favorite_rank": plugin._favorite_priority(room["platform"], room["id"]),
            "heat": 0,
            "owner": room["id"],
        }
        for room in rooms
        if room["isFav"]
    ]

    sorted_favorite_cards = plugin._sort_cards(favorite_cards)
    assert sorted_favorite_cards[0]["id"] == "545318"
    assert [card["id"] for card in sorted_favorite_cards[:3]] == ["545318", "60937", "6979222"]


def test_clean_text_keeps_separators_when_dropping_emoji():
    plugin = _plugin()

    assert plugin._clean_text("LIVE🔪DRAMA🔪NEWS") == "LIVE DRAMA NEWS"


def test_format_uptime_hides_implausibly_old_start_time():
    old_start_ms = (time.time() - 90 * 3600) * 1000

    assert LiveRadar._format_uptime(old_start_ms) == ""


def test_live_overflow_text_prefers_remaining_live_names_and_fits_width():
    plugin = _plugin()
    image = Image.new("RGB", (420, 80), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    font = plugin._font(13, "bold")
    cards = [
        {"id": "60937", "owner": "Zard1991"},
        {"id": "545318", "owner": "Mr. Quin"},
        {"id": "3507497", "label": "Ams"},
    ]

    text = plugin._live_overflow_text(cards, draw, font, 420)
    assert text == "...Zard1991, Mr. Quin, Ams are live too"
    assert "60937" not in text
    assert "545318" not in text

    tight = plugin._live_overflow_text(cards, draw, font, 130)
    assert "Zard1991" in tight
    assert draw.textlength(tight, font=font) <= 130


def test_twitch_helix_batches_all_logins_and_reuses_cached_app_token():
    plugin = _plugin()
    rooms = [
        {"platform": "twitch", "id": f"streamer{i}", "label": f"Streamer {i}", "isFav": i == 0}
        for i in range(13)
    ]
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def post(self, url, params, timeout, headers):
            calls.append(("post", url, params, timeout, headers))
            return FakeResponse({"access_token": "short-lived-token", "expires_in": 3600})

        def get(self, url, params, timeout, headers):
            calls.append(("get", url, params, timeout, headers))
            return FakeResponse(
                {
                    "data": [
                        {
                            "user_login": "streamer0",
                            "user_name": "Streamer Zero",
                            "title": "Still live",
                            "viewer_count": 321,
                            "started_at": "2026-07-19T12:00:00Z",
                            "thumbnail_url": "https://static-cdn.jtvnw.net/previews-ttv/live_user_streamer0-{width}x{height}.jpg",
                        }
                    ]
                }
            )

    session = FakeSession()

    first = plugin._fetch_twitch_statuses_direct(
        session,
        rooms,
        timeout=9,
        fetch_avatars=False,
        client_id="test-client-id",
        client_secret="test-client-secret",
    )
    second = plugin._fetch_twitch_statuses_direct(
        session,
        rooms,
        timeout=9,
        fetch_avatars=False,
        client_id="test-client-id",
        client_secret="test-client-secret",
    )

    token_calls = [call for call in calls if call[0] == "post"]
    stream_calls = [call for call in calls if call[0] == "get"]
    assert len(token_calls) == 1
    assert len(stream_calls) == 2
    assert stream_calls[0][2] == [("user_login", f"streamer{i}") for i in range(13)]
    assert [result["id"] for result in first] == [room["id"] for room in rooms]
    assert first[0]["status"]["isLive"] is True
    assert first[0]["status"]["owner"] == "Streamer Zero"
    assert first[0]["status"]["cover"].endswith("-640x360.jpg")
    assert first[0]["status"]["startTime"] == 1784462400000
    assert all(result["status"]["isLive"] is False for result in first[1:])
    assert second == first
    serialized = json.dumps(first, sort_keys=True)
    assert "test-client-id" not in serialized
    assert "test-client-secret" not in serialized
    assert "short-lived-token" not in serialized


def test_twitch_helix_fetches_profiles_and_merges_display_names_and_avatars():
    plugin = _plugin()
    rooms = [
        {"platform": "twitch", "id": "liveuser", "label": "Live label", "isFav": True},
        {"platform": "twitch", "id": "offlineuser", "label": "Offline label", "isFav": False},
    ]
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def post(self, url, params, timeout, headers):
            del url, params, timeout, headers
            return FakeResponse({"access_token": "token", "expires_in": 3600})

        def get(self, url, params, timeout, headers):
            del timeout, headers
            calls.append((url, params))
            if url == live_radar_module.TWITCH_STREAMS_URL:
                return FakeResponse(
                    {
                        "data": [
                            {
                                "user_login": "liveuser",
                                "user_name": "Live User",
                                "title": "Live now",
                                "viewer_count": 321,
                                "started_at": "2026-07-19T12:00:00Z",
                                "thumbnail_url": "https://static-cdn.jtvnw.net/previews/live-{width}x{height}.jpg",
                            }
                        ]
                    }
                )
            assert url == live_radar_module.TWITCH_USERS_URL
            return FakeResponse(
                {
                    "data": [
                        {
                            "login": "liveuser",
                            "display_name": "Live Profile",
                            "profile_image_url": "https://static-cdn.jtvnw.net/jtv_user_pictures/live.png",
                        },
                        {
                            "login": "offlineuser",
                            "display_name": "Offline Profile",
                            "profile_image_url": "https://static-cdn.jtvnw.net/jtv_user_pictures/offline.png",
                        },
                    ]
                }
            )

    results = plugin._fetch_twitch_statuses_direct(
        FakeSession(),
        rooms,
        timeout=9,
        fetch_avatars=True,
        client_id="client-id",
        client_secret="client-secret",
    )

    assert calls == [
        (
            live_radar_module.TWITCH_STREAMS_URL,
            [("user_login", "liveuser"), ("user_login", "offlineuser")],
        ),
        (
            live_radar_module.TWITCH_USERS_URL,
            [("login", "liveuser"), ("login", "offlineuser")],
        ),
    ]
    assert results[0]["status"]["owner"] == "Live User"
    assert results[0]["status"]["avatar"].endswith("/live.png")
    assert results[1]["status"]["owner"] == "Offline Profile"
    assert results[1]["status"]["avatar"].endswith("/offline.png")
    assert results[1]["status"]["isLive"] is False


def test_twitch_profile_failure_keeps_successful_stream_data(caplog):
    plugin = _plugin()
    room = {"platform": "twitch", "id": "liveuser", "label": "Live label", "isFav": False}

    class FakeResponse:
        def __init__(self, payload, *, error=None):
            self.payload = payload
            self.error = error

        def raise_for_status(self):
            if self.error:
                raise self.error

        def json(self):
            return self.payload

    class FakeSession:
        def post(self, url, params, timeout, headers):
            del url, params, timeout, headers
            return FakeResponse({"access_token": "token", "expires_in": 3600})

        def get(self, url, params, timeout, headers):
            del params, timeout, headers
            if url == live_radar_module.TWITCH_USERS_URL:
                return FakeResponse({}, error=RuntimeError("profile endpoint unavailable"))
            return FakeResponse(
                {
                    "data": [
                        {
                            "user_login": "liveuser",
                            "user_name": "Live User",
                            "title": "Stream survives",
                            "viewer_count": 42,
                            "thumbnail_url": "https://static-cdn.jtvnw.net/previews/live-{width}x{height}.jpg",
                        }
                    ]
                }
            )

    results = plugin._fetch_twitch_statuses_direct(
        FakeSession(),
        [room],
        timeout=9,
        fetch_avatars=True,
        client_id="client-id",
        client_secret="client-secret",
    )

    assert results[0]["status"]["isLive"] is True
    assert results[0]["status"]["title"] == "Stream survives"
    assert results[0]["status"]["heatValue"] == 42
    assert results[0]["status"]["avatar"] == ""
    assert "Twitch profile avatars unavailable: RuntimeError" in caplog.text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://static-cdn.jtvnw.net/avatar.png", "https://static-cdn.jtvnw.net/avatar.png"),
        ("http://example.test/avatar.png", "http://example.test/avatar.png"),
        ("data:image/png;base64,AAAA", ""),
        ("javascript:alert(1)", ""),
        ("//static-cdn.jtvnw.net/avatar.png", ""),
    ],
)
def test_twitch_avatar_media_url_accepts_only_http_and_https(value, expected):
    assert LiveRadar._http_media_url(value) == expected


def test_jtvnw_media_uses_twitch_referer():
    assert LiveRadar._cover_headers(
        "https://static-cdn.jtvnw.net/jtv_user_pictures/avatar.png"
    )["Referer"] == "https://www.twitch.tv/"


def test_twitch_credentials_are_loaded_from_device_config_without_logging_values(monkeypatch):
    plugin = _plugin()
    requested = []

    class SecretDevice:
        def load_env_key(self, key):
            requested.append(key)
            return {"TWITCH_CLIENT_ID": "device-id", "TWITCH_CLIENT_SECRET": "device-secret"}[key]

    monkeypatch.setenv("TWITCH_CLIENT_ID", "process-id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "process-secret")

    assert plugin._load_twitch_credentials(SecretDevice()) == ("device-id", "device-secret")
    assert requested == ["TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"]


def test_fetch_statuses_routes_platforms_separately_and_preserves_room_order(monkeypatch):
    plugin = _plugin()
    plugin._twitch_credentials = ("configured-id", "configured-secret")
    aggregator_calls = []

    def source_results(source_name, rooms):
        return [
            {
                "ok": True,
                "platform": room["platform"],
                "id": room["id"],
                "status": {"isLive": False, "owner": room["label"]},
                "cache": source_name,
                "error": None,
            }
            for room in rooms
        ]

    plugin._fetch_twitch_statuses_direct = (
        lambda session, rooms, timeout, fetch_avatars, client_id, client_secret: source_results(
            "TWITCH_HELIX", rooms
        )
    )
    plugin._fetch_bilibili_statuses_direct = (
        lambda session, rooms, timeout, fetch_avatars: source_results("BILIBILI_DIRECT", rooms)
    )

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def post(self, url, json, timeout, headers):
            aggregator_calls.append(json["rooms"])
            return FakeResponse(
                {
                    "results": [
                        {
                            "ok": True,
                            "platform": room["platform"],
                            "id": room["id"],
                            "status": {"isLive": False, "owner": room["id"]},
                        }
                        for room in json["rooms"]
                    ]
                    + [
                        {
                            "ok": False,
                            "platform": "bilibili",
                            "id": "545318",
                            "status": None,
                            "error": "unexpected_cross_platform_result",
                        }
                    ]
                }
            )

    monkeypatch.setattr(live_radar_module, "get_http_session", lambda: FakeSession())
    rooms = [
        {"platform": "twitch", "id": "xqc", "label": "xQc", "isFav": False},
        {"platform": "douyu", "id": "60937", "label": "Zard", "isFav": True},
        {"platform": "bilibili", "id": "545318", "label": "Mr.Quin", "isFav": True},
        {"platform": "douyu", "id": "52", "label": "YYF", "isFav": False},
    ]

    results = plugin._fetch_statuses(rooms, "https://example.test/batch", 9, True)

    assert [[room["platform"] for room in call] for call in aggregator_calls] == [["douyu", "douyu"]]
    assert [result["id"] for result in results] == [room["id"] for room in rooms]
    assert [result.get("cache") for result in results] == [
        "TWITCH_HELIX",
        None,
        "BILIBILI_DIRECT",
        None,
    ]


def test_bilibili_room_uid_mapping_is_reused_for_warm_bulk_refresh(tmp_path):
    plugin = _plugin()
    plugin._cache_dir = lambda: tmp_path
    calls = []
    rooms = [
        {"platform": "bilibili", "id": "101", "label": "One", "isFav": False},
        {"platform": "bilibili", "id": "202", "label": "Two", "isFav": False},
    ]

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def get(self, url, params, timeout, headers):
            calls.append((url, params))
            if "get_info" in url:
                room_id = params["room_id"]
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "uid": int(room_id) + 1000,
                            "room_id": int(room_id),
                            "live_status": 0,
                            "title": f"Room {room_id}",
                        },
                    }
                )
            uids = [uid for key, uid in params if key == "uids[]"]
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        uid: {
                            "uid": int(uid),
                            "live_status": 1 if uid == "1101" else 0,
                            "uname": f"UID {uid}",
                            "title": "Bulk status",
                        }
                        for uid in uids
                    },
                }
            )

    session = FakeSession()
    cold = plugin._fetch_bilibili_statuses_direct(session, rooms, 9, True)
    warm = plugin._fetch_bilibili_statuses_direct(session, rooms, 9, True)

    room_info_calls = [call for call in calls if "get_info" in call[0]]
    uid_status_calls = [call for call in calls if "get_status_info_by_uids" in call[0]]
    assert len(room_info_calls) == 2
    assert len(uid_status_calls) == 2
    assert [result["id"] for result in cold] == ["101", "202"]
    assert [result["id"] for result in warm] == ["101", "202"]
    assert cold[0]["status"]["isLive"] is True
    assert warm == cold
    mapping = json.loads((tmp_path / "bilibili_room_map.json").read_text(encoding="utf-8"))
    assert set(mapping["rooms"]) == {"101", "202"}


def _bilibili_profile_session(calls, fail_uid_status):
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def get(self, url, params, timeout, headers):
            calls.append(url)
            if "get_info" in url:
                room_id = params["room_id"]
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "uid": int(room_id) + 1000,
                            "room_id": int(room_id),
                            "live_status": 1,
                            "title": f"Room {room_id}",
                        },
                    }
                )
            if fail_uid_status[0]:
                raise ConnectionError("HTTPSConnectionPool(host='api.live.bilibili.com'): Max retries exceeded")
            uids = [uid for key, uid in params if key == "uids[]"]
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        uid: {
                            "uid": int(uid),
                            "live_status": 1,
                            "uname": f"Streamer {uid}",
                            "face": f"https://i0.hdslb.com/face/{uid}.jpg",
                            "title": "Bulk status",
                        }
                        for uid in uids
                    },
                }
            )

    return FakeSession()


def test_bilibili_failed_uid_status_keeps_last_known_name_and_avatar(tmp_path):
    plugin = _plugin()
    plugin._cache_dir = lambda: tmp_path
    calls = []
    fail_uid_status = [False]
    session = _bilibili_profile_session(calls, fail_uid_status)
    rooms = [
        {"platform": "bilibili", "id": "101", "label": "One", "isFav": False},
        {"platform": "bilibili", "id": "202", "isFav": False},
    ]

    healthy = plugin._fetch_bilibili_statuses_direct(session, rooms, 9, True)
    fail_uid_status[0] = True
    degraded = plugin._fetch_bilibili_statuses_direct(session, rooms, 9, True)

    assert [result["status"]["owner"] for result in healthy] == ["Streamer 1101", "Streamer 1202"]
    assert [result["ok"] for result in degraded] == [True, True]
    assert [result["status"]["owner"] for result in degraded] == ["Streamer 1101", "Streamer 1202"]
    assert [result["status"]["avatar"] for result in degraded] == [
        "https://i0.hdslb.com/face/1101.jpg",
        "https://i0.hdslb.com/face/1202.jpg",
    ]
    assert [result["status"]["isLive"] for result in degraded] == [True, True]
    assert [result["status"]["title"] for result in degraded] == ["Room 101", "Room 202"]
    assert len([url for url in calls if "get_info" in url]) == 2

    without_avatars = plugin._fetch_bilibili_statuses_direct(session, rooms, 9, False)
    assert [result["status"]["owner"] for result in without_avatars] == ["Streamer 1101", "Streamer 1202"]
    assert [result["status"]["avatar"] for result in without_avatars] == ["", ""]


def test_bilibili_first_failed_uid_status_falls_back_to_label_then_room_id(tmp_path):
    plugin = _plugin()
    plugin._cache_dir = lambda: tmp_path
    calls = []
    session = _bilibili_profile_session(calls, [True])
    rooms = [
        {"platform": "bilibili", "id": "101", "label": "One", "isFav": False},
        {"platform": "bilibili", "id": "202", "isFav": False},
    ]

    results = plugin._fetch_bilibili_statuses_direct(session, rooms, 9, True)

    assert [result["ok"] for result in results] == [True, True]
    assert [result["status"]["owner"] for result in results] == ["One", "202"]
    assert [result["status"]["avatar"] for result in results] == ["", ""]
    assert [result["status"]["isLive"] for result in results] == [True, True]
    mapping = json.loads((tmp_path / "bilibili_room_map.json").read_text(encoding="utf-8"))
    assert all("uname" not in entry and "face" not in entry for entry in mapping["rooms"].values())


def test_bilibili_room_info_refresh_keeps_last_known_profile(tmp_path):
    plugin = _plugin()
    plugin._cache_dir = lambda: tmp_path
    now = [1000.0]
    plugin._bilibili_room_map_clock = lambda: now[0]
    calls = []
    fail_uid_status = [False]
    session = _bilibili_profile_session(calls, fail_uid_status)
    room = {"platform": "bilibili", "id": "101", "label": "One", "isFav": False}

    plugin._fetch_bilibili_statuses_direct(session, [room], 9, True)
    now[0] += live_radar_module.BILIBILI_ROOM_MAP_TTL_SECONDS
    fail_uid_status[0] = True
    refreshed = plugin._fetch_bilibili_statuses_direct(session, [room], 9, True)

    assert len([url for url in calls if "get_info" in url]) == 2
    assert refreshed[0]["status"]["owner"] == "Streamer 1101"
    assert refreshed[0]["status"]["avatar"] == "https://i0.hdslb.com/face/1101.jpg"
    mapping = json.loads((tmp_path / "bilibili_room_map.json").read_text(encoding="utf-8"))
    assert mapping["rooms"]["101"]["updated_at"] == now[0]


def test_bilibili_missing_room_is_negative_cached_for_six_hours(tmp_path):
    plugin = _plugin()
    plugin._cache_dir = lambda: tmp_path
    now = [1000.0]
    plugin._bilibili_room_map_clock = lambda: now[0]
    room_info_calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def get(self, url, params, timeout, headers):
            if "get_info" in url:
                room_info_calls.append(params["room_id"])
                return FakeResponse({"code": 0, "data": None})
            return FakeResponse({"code": 0, "data": {}})

    room = {"platform": "bilibili", "id": "404", "label": "Missing", "isFav": False}
    session = FakeSession()

    first = plugin._fetch_bilibili_statuses_direct(session, [room], 9, False)
    now[0] += 6 * 3600 - 1
    second = plugin._fetch_bilibili_statuses_direct(session, [room], 9, False)

    assert room_info_calls == ["404"]
    assert first[0]["error"] == "bilibili_room_not_found"
    assert second[0]["error"] == "bilibili_room_not_found"

    now[0] += 1
    plugin._fetch_bilibili_statuses_direct(session, [room], 9, False)
    assert room_info_calls == ["404", "404"]


def test_known_missing_bilibili_room_does_not_hit_aggregator(monkeypatch):
    plugin = _plugin()
    aggregator_calls = []
    room = {"platform": "bilibili", "id": "404", "label": "Missing", "isFav": False}
    plugin._fetch_bilibili_statuses_direct = lambda *_args: [
        {
            "ok": False,
            "platform": "bilibili",
            "id": "404",
            "error": "bilibili_room_not_found",
            "status": plugin._default_status(room, is_error=True),
        }
    ]

    class FakeSession:
        def post(self, url, json, timeout, headers):
            aggregator_calls.append(json)
            raise AssertionError("known missing room reached aggregator")

    monkeypatch.setattr(live_radar_module, "get_http_session", lambda: FakeSession())

    results = plugin._fetch_statuses([room], "https://example.test/batch", 9, False)

    assert aggregator_calls == []
    assert results[0]["error"] == "bilibili_room_not_found"


def test_source_health_records_platform_counts_and_sanitized_fallback(tmp_path, monkeypatch):
    plugin = _plugin()
    plugin._cache_dir = lambda: tmp_path
    plugin._write_source_health = LiveRadar._write_source_health.__get__(plugin, LiveRadar)
    plugin._twitch_credentials = ("private-client-id", "private-client-secret")
    elapsed = iter([1.0, 1.2, 2.0, 2.3, 3.0, 3.4])
    plugin._source_health_elapsed_clock = lambda: next(elapsed)
    plugin._source_health_wall_clock = lambda: 1784476800.0
    rooms = [
        {"platform": "twitch", "id": "xqc", "label": "xQc", "isFav": False},
        {"platform": "bilibili", "id": "545318", "label": "Mr.Quin", "isFav": True},
        {"platform": "douyu", "id": "60937", "label": "Zard", "isFav": True},
    ]

    def result_for(room, source):
        return {
            "ok": True,
            "platform": room["platform"],
            "id": room["id"],
            "status": {"isLive": False, "owner": room["label"]},
            "cache": source,
            "error": None,
        }

    plugin._fetch_twitch_statuses_direct = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("private-client-id private-client-secret")
    )
    plugin._fetch_bilibili_statuses_direct = lambda session, source_rooms, timeout, fetch_avatars: [
        result_for(room, "BILIBILI_DIRECT") for room in source_rooms
    ]
    plugin._fetch_aggregator_platform = (
        lambda session, source_rooms, api_url, timeout, fetch_avatars, platform: [
            result_for(room, "AGGREGATOR") for room in source_rooms
        ]
    )
    monkeypatch.setattr(live_radar_module, "get_http_session", lambda: object())

    plugin._fetch_statuses(rooms, "https://example.test/batch", 9, True)

    health = json.loads((tmp_path / "source_health.json").read_text(encoding="utf-8"))
    assert health["updated_at"] == 1784476800.0
    assert set(health["platforms"]) == {"twitch", "bilibili", "douyu"}
    assert health["platforms"]["twitch"] == {
        "source": "twitch_helix+aggregator_fallback",
        "duration_ms": 200,
        "requested": 1,
        "success": 1,
        "error": 0,
        "last_error": "RuntimeError",
    }
    assert health["platforms"]["bilibili"]["source"] == "bilibili_direct"
    assert health["platforms"]["douyu"]["source"] == "aggregator"
    serialized = json.dumps(health, sort_keys=True)
    assert "private-client-id" not in serialized
    assert "private-client-secret" not in serialized


def test_source_health_write_failure_does_not_fail_status_refresh(monkeypatch):
    plugin = _plugin()
    plugin._write_source_health = LiveRadar._write_source_health.__get__(plugin, LiveRadar)
    plugin._cache_dir = lambda: (_ for _ in ()).throw(OSError("read-only cache"))
    plugin._twitch_credentials = ("", "")
    room = {"platform": "douyu", "id": "1", "label": "One", "isFav": False}
    plugin._fetch_aggregator_platform = lambda *_args: [
        {
            "ok": True,
            "platform": "douyu",
            "id": "1",
            "status": {"isLive": False, "owner": "One"},
        }
    ]
    monkeypatch.setattr(live_radar_module, "get_http_session", lambda: object())

    results = plugin._fetch_statuses([room], "https://example.test/batch", 9, False)

    assert results[0]["ok"] is True


def test_fetch_statuses_posts_in_batches(monkeypatch):
    plugin = _plugin()
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def post(self, url, json, timeout, headers):
            calls.append((url, json, timeout, headers))
            return FakeResponse(
                {
                    "ok": True,
                    "results": [
                        {
                            "ok": True,
                            "platform": room["platform"],
                            "id": room["id"],
                            "status": {"isLive": False, "owner": room["id"]},
                        }
                        for room in json["rooms"]
                    ],
                }
            )

    monkeypatch.setattr("plugins.live_radar.live_radar.get_http_session", lambda: FakeSession())
    rooms = [{"platform": "twitch", "id": str(i), "label": "", "isFav": False} for i in range(12)]

    results = plugin._fetch_statuses(rooms, "https://example.test/batch", 9, False)

    assert len(results) == 12
    assert len(calls) == 2
    assert calls[0][1]["rooms"][0] == {"platform": "twitch", "id": "0", "fetchAvatar": False}
    assert calls[1][1]["rooms"][0]["id"] == "10"


def test_fetch_statuses_falls_back_to_single_room_after_batch_failure(monkeypatch):
    plugin = _plugin()
    calls = []

    class FakeResponse:
        def __init__(self, payload=None, error=None):
            self.payload = payload or {}
            self.error = error

        def raise_for_status(self):
            if self.error:
                raise RuntimeError(self.error)

        def json(self):
            return self.payload

    class FakeSession:
        def post(self, url, json, timeout, headers):
            calls.append(json)
            if len(json["rooms"]) > 1:
                return FakeResponse(error="batch failed")
            room = json["rooms"][0]
            return FakeResponse(
                {
                    "ok": True,
                    "results": [
                        {
                            "ok": True,
                            "platform": room["platform"],
                            "id": room["id"],
                            "status": {"isLive": False, "owner": room["id"]},
                        }
                    ],
                }
            )

    monkeypatch.setattr("plugins.live_radar.live_radar.get_http_session", lambda: FakeSession())
    rooms = [{"platform": "douyu", "id": str(i), "label": "", "isFav": False} for i in range(2)]

    results = plugin._fetch_statuses(rooms, "https://example.test/batch", 9, True)

    assert [call["rooms"][0]["id"] for call in calls] == ["0", "0", "1"]
    assert [result["id"] for result in results] == ["0", "1"]


def test_fetch_statuses_cools_down_failed_multi_room_batches(monkeypatch):
    plugin = _plugin()
    now = [100.0]
    plugin._batch_clock = lambda: now[0]
    calls = []

    class FakeResponse:
        def __init__(self, payload=None, error=None):
            self.payload = payload or {}
            self.error = error

        def raise_for_status(self):
            if self.error:
                raise RuntimeError(self.error)

        def json(self):
            return self.payload

    class FakeSession:
        def post(self, url, json, timeout, headers):
            room_ids = [room["id"] for room in json["rooms"]]
            calls.append(room_ids)
            if len(room_ids) > 1 and now[0] < 500.0:
                return FakeResponse(error="batch failed")
            return FakeResponse(
                {
                    "ok": True,
                    "results": [
                        {
                            "ok": True,
                            "platform": room["platform"],
                            "id": room["id"],
                            "status": {"isLive": False, "owner": room["id"]},
                        }
                        for room in json["rooms"]
                    ],
                }
            )

    monkeypatch.setattr("plugins.live_radar.live_radar.get_http_session", lambda: FakeSession())
    rooms = [{"platform": "douyu", "id": str(i), "label": "", "isFav": False} for i in range(2)]

    plugin._fetch_statuses(rooms, "https://example.test/batch", 9, True)
    plugin._fetch_statuses(rooms, "https://example.test/batch", 9, True)

    assert calls == [["0", "1"], ["0"], ["1"], ["0"], ["1"]]

    now[0] = 500.0
    plugin._fetch_statuses(rooms, "https://example.test/batch", 9, True)

    assert calls[-1] == ["0", "1"]


def test_fetch_statuses_uses_direct_bilibili_before_douyu_aggregator(monkeypatch, tmp_path):
    plugin = _plugin()
    plugin._cache_dir = lambda: tmp_path
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def post(self, url, json, timeout, headers):
            return FakeResponse(
                {
                    "ok": True,
                    "results": [
                        {
                            "ok": True,
                            "platform": "douyu",
                            "id": "6979222",
                            "status": {"isLive": False, "isReplay": True, "owner": "玩机器"},
                        },
                    ],
                }
            )

        def get(self, url, params, timeout, headers):
            calls.append((url, params, timeout, headers))
            if "get_info" in url:
                assert params == {"room_id": "545318"}
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "uid": 15810,
                            "room_id": 545318,
                            "live_status": 1,
                            "title": "线路测试",
                            "online": 38000,
                            "user_cover": "https://example.test/room.jpg",
                        },
                    }
                )
            assert "get_status_info_by_uids" in url
            assert params == [("uids[]", "15810")]
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "15810": {
                            "uid": 15810,
                            "room_id": 545318,
                            "live_status": 1,
                            "title": "线路测试",
                            "uname": "Mr.Quin",
                            "face": "https://example.test/face.jpg",
                            "keyframe": "https://example.test/keyframe.jpg",
                            "online": 38600,
                            "live_time": 1780859458,
                        }
                    },
                }
            )

    monkeypatch.setattr("plugins.live_radar.live_radar.get_http_session", lambda: FakeSession())
    rooms = [
        {"platform": "bilibili", "id": "545318", "label": "", "isFav": True},
        {"platform": "douyu", "id": "6979222", "label": "", "isFav": False},
    ]

    results = plugin._fetch_statuses(rooms, "https://example.test/batch", 9, True)

    assert results[0]["ok"] is True
    assert results[0]["cache"] == "BILIBILI_DIRECT"
    assert results[0]["status"]["isLive"] is True
    assert results[0]["status"]["owner"] == "Mr.Quin"
    assert results[0]["status"]["avatar"] == "https://example.test/face.jpg"
    assert results[0]["status"]["cover"] == "https://example.test/keyframe.jpg"
    assert results[0]["status"]["heatValue"] == 38600
    assert results[0]["status"]["startTime"] == 1780859458000
    assert results[1]["platform"] == "douyu"
    assert results[1]["status"]["isReplay"] is True
    assert len(calls) == 2


def test_load_cover_source_uses_shared_session_headers_and_cache(monkeypatch, tmp_path):
    plugin = _plugin()
    plugin._cache_dir = lambda: tmp_path
    source = Image.new("RGB", (32, 18), (24, 180, 240))
    buffer = BytesIO()
    source.save(buffer, "JPEG")
    calls = []

    class FakeResponse:
        headers = {}

        def __init__(self):
            self.closed = False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size > 0
            yield buffer.getvalue()

        def close(self):
            self.closed = True

    response = FakeResponse()

    class FakeSession:
        trust_env = False

        def get(self, url, timeout, headers, stream=False):
            calls.append({"url": url, "timeout": timeout, "headers": headers, "stream": stream})
            return response

    session = FakeSession()
    monkeypatch.setattr(live_radar_module, "get_http_session", lambda: session)

    cover = plugin._load_cover_source("https://i0.hdslb.com/bfs/live/test.jpg", 30)

    assert cover is not None
    assert cover.size == (32, 18)
    assert calls == [
        {
            "url": "https://i0.hdslb.com/bfs/live/test.jpg",
            "timeout": 12,
            "headers": plugin._cover_headers("https://i0.hdslb.com/bfs/live/test.jpg"),
            "stream": True,
        }
    ]
    assert response.closed is True
    assert calls[0]["headers"]["Referer"] == "https://live.bilibili.com/"
    assert list(tmp_path.glob("cover_*.png"))


def test_live_cover_is_refetched_at_exact_snapshot_ttl(monkeypatch, tmp_path):
    plugin = _plugin()
    plugin._cache_dir = lambda: tmp_path
    source = Image.new("RGB", (32, 18), (24, 180, 240))
    buffer = BytesIO()
    source.save(buffer, "JPEG")
    now = [1000.0]
    calls = []

    class FakeResponse:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield buffer.getvalue()

        def close(self):
            return None

    class FakeSession:
        trust_env = False

        def get(self, url, timeout, headers, stream=False):
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr(live_radar_module, "get_http_session", lambda: FakeSession())
    monkeypatch.setattr(live_radar_module.time, "time", lambda: now[0])
    url = "https://static-cdn.jtvnw.net/previews-ttv/live_user_xqc-640x360.jpg"

    assert plugin._load_cover_source(url, 60) is not None
    cache_path = plugin._cover_cache_path(url)
    os.utime(cache_path, (now[0], now[0]))
    now[0] += 59
    assert plugin._load_cover_source(url, 60) is not None
    now[0] += 1
    assert plugin._load_cover_source(url, 60) is not None

    assert calls == [url, url]


def test_snapshot_default_is_sixty_seconds_independent_of_status_cache(monkeypatch):
    plugin = _plugin()
    _memory_cache(plugin)
    captured = {}
    plugin._fetch_statuses = lambda *_args: [_status_result()]

    def capture_layout(cards, dimensions, theme, generated_at, from_cache, warning, layout):
        captured.update(layout)
        return Image.new("RGB", dimensions, "white")

    monkeypatch.setattr(plugin, "_render_dashboard", capture_layout)

    plugin.generate_image(
        {
            "roomsText": "twitch|xqc|xQc",
            "cacheSeconds": "300",
            "showSnapshots": True,
        },
        FakeDeviceConfig(),
    )

    assert captured["snapshot_cache_seconds"] == 60


def test_generate_image_renders_card_wall_without_network():
    plugin = _plugin()
    _memory_cache(plugin)
    plugin._fetch_statuses = lambda rooms, api_url, timeout, fetch_avatars: [
        {
            "ok": True,
            "platform": "twitch",
            "id": "xqc",
            "status": {
                "isLive": True,
                "title": "Drama news content",
                "owner": "xQc",
                "heatValue": 12345,
                "startTime": (time.time() - 3660) * 1000,
            },
        },
        {
            "ok": True,
            "platform": "bilibili",
            "id": "545318",
            "status": {"isLive": False, "title": "007", "owner": "Mr.Quin", "heatValue": 0},
        },
    ]

    image = plugin.generate_image(
        {
            "roomsText": "twitch|xqc|xQc|fav\nbilibili|545318|Mr.Quin",
            "themeMode": "dark",
            "cacheSeconds": "20",
        },
        FakeDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert any(pixel != (0, 0, 0) for pixel in image.crop((10, 10, 790, 470)).getdata())


def test_theme_uses_original_day_palette_over_conflicting_legacy_alias():
    plugin = _plugin()
    context = _canonical_theme(
        "day",
        background=(242, 237, 226),
        panel=(224, 216, 198),
        ink=(20, 22, 24),
        muted=(72, 74, 78),
        rule=(130, 126, 118),
        accent=(176, 42, 54),
    )

    theme = plugin._theme({"themeMode": "dark", "_inkypi_theme": context}, FakeDeviceConfig(mode="night"))

    assert theme["mode"] == "light"
    assert theme["bg"] == (255, 255, 255)
    assert theme["panel"] == (255, 255, 255)
    assert theme["ink"] == (0, 0, 0)
    assert theme["muted"] == (0, 0, 0)
    assert theme["line"] == (0, 0, 0)


def test_render_themed_image_accepts_frozen_scheduler_theme_context():
    plugin = _plugin()
    _memory_cache(plugin)
    plugin._fetch_statuses = lambda *_args, **_kwargs: [_status_result()]
    context = _canonical_theme(
        "day",
        background=(242, 237, 226),
        panel=(224, 216, 198),
        ink=(20, 22, 24),
        muted=(72, 74, 78),
        rule=(130, 126, 118),
        accent=(176, 42, 54),
    )

    image = plugin.render_themed_image(
        {
            "roomsText": "twitch|xqc|xQc",
            "fetchAvatars": "false",
            "showSnapshots": "false",
            "cacheSeconds": "20",
        },
        FakeDeviceConfig(mode="night"),
        resolved_theme_context=freeze_payload(context),
    )

    assert image.getpixel((0, 0)) == (255, 255, 255)


def test_theme_only_stale_status_cache_rerenders_without_provider_calls():
    plugin = _plugin()
    cache = _memory_cache(plugin)
    calls = {"statuses": 0}

    def warm_statuses(*_args):
        calls["statuses"] += 1
        return [
            {
                "ok": True,
                "platform": "twitch",
                "id": "xqc",
                "status": {"isLive": False, "title": "Offline", "owner": "xQc", "heatValue": 0},
            }
        ]

    plugin._fetch_statuses = warm_statuses
    settings = {
        "roomsText": "twitch|xqc|xQc",
        "themeMode": "dark",
        "fetchAvatars": "false",
        "showSnapshots": "false",
        "cacheSeconds": "20",
    }
    plugin.generate_image(settings, FakeDeviceConfig(mode="night"))
    assert calls == {"statuses": 1}
    for entry in cache.values():
        entry["fetched_at"] = 0

    def fail_provider(*_args, **_kwargs):
        calls["statuses"] += 1
        raise AssertionError("theme-only redraw must not call a provider")

    plugin._fetch_statuses = fail_provider
    day = _canonical_theme(
        "day",
        background=(242, 237, 226),
        panel=(224, 216, 198),
        ink=(20, 22, 24),
        muted=(72, 74, 78),
        rule=(130, 126, 118),
        accent=(176, 42, 54),
    )
    night = _canonical_theme(
        "night",
        background=(7, 9, 12),
        panel=(22, 26, 32),
        ink=(245, 246, 248),
        muted=(178, 182, 190),
        rule=(58, 64, 72),
        accent=(76, 190, 238),
    )

    day_image = plugin.generate_image({**settings, "_theme_render_only": True, "_inkypi_theme": day}, FakeDeviceConfig(mode="night"))
    night_image = plugin.generate_image({**settings, "_theme_render_only": True, "_inkypi_theme": night}, FakeDeviceConfig(mode="day"))

    assert calls == {"statuses": 1}
    assert day_image.getpixel((0, 0)) == (255, 255, 255)
    assert night_image.getpixel((0, 0)) == night["palette"]["background"]
    assert hashlib.sha256(day_image.tobytes()).digest() != hashlib.sha256(night_image.tobytes()).digest()


def test_theme_only_render_reads_stale_cover_and_avatar_disk_cache_without_http(monkeypatch, tmp_path):
    plugin = _plugin()
    plugin._cache_dir = lambda: tmp_path
    cover_url = "https://covers.test/theme-only-live.jpg"
    avatar_url = "https://avatars.test/theme-only-live.png"
    settings = {
        "roomsText": "twitch|xqc|xQc|fav",
        "cacheSeconds": "20",
        "snapshotCacheSeconds": "30",
        "avatarCacheSeconds": "300",
        "_theme_render_only": True,
        "_inkypi_theme": _canonical_theme(
            "day",
            background=(242, 237, 226),
            panel=(224, 216, 198),
            ink=(20, 22, 24),
            muted=(72, 74, 78),
            rule=(130, 126, 118),
            accent=(176, 42, 54),
        ),
    }
    rooms = plugin._parse_rooms(settings)
    plugin._write_cache(
        plugin._cache_key(rooms, live_radar_module.DEFAULT_API_URL, True),
        {
            "fetched_at": 0,
            "results": [
                {
                    "ok": True,
                    "platform": "twitch",
                    "id": "xqc",
                    "status": {
                        "isLive": True,
                        "title": "Cached media stays visible",
                        "owner": "xQc",
                        "heatValue": 12345,
                        "cover": cover_url,
                        "avatar": avatar_url,
                    },
                }
            ],
        },
    )
    cover_color = (31, 101, 181)
    avatar_color = (211, 61, 101)
    Image.new("RGB", (160, 90), cover_color).save(plugin._cover_cache_path(cover_url), "PNG")
    Image.new("RGB", (64, 64), avatar_color).save(plugin._avatar_cache_path(avatar_url), "PNG")
    stale_time = time.time() - 7 * 24 * 60 * 60
    os.utime(plugin._cover_cache_path(cover_url), (stale_time, stale_time))
    os.utime(plugin._avatar_cache_path(avatar_url), (stale_time, stale_time))
    http_calls = []

    def fail_http():
        http_calls.append("session")
        raise AssertionError("theme-only media must not acquire an HTTP session")

    monkeypatch.setattr(live_radar_module, "get_http_session", fail_http)

    image = plugin.generate_image(settings, FakeDeviceConfig(mode="night"))

    assert http_calls == []
    pixels = set(image.getdata())
    assert cover_color in pixels
    assert avatar_color in pixels


def test_theme_only_render_uses_media_placeholders_for_missing_or_corrupt_cache_without_http(monkeypatch, tmp_path):
    plugin = _plugin()
    plugin._cache_dir = lambda: tmp_path
    cover_url = "https://covers.test/corrupt-theme-only.jpg"
    avatar_url = "https://avatars.test/missing-theme-only.png"
    settings = {
        "roomsText": "twitch|xqc|xQc|fav",
        "_theme_render_only": True,
        "_inkypi_theme": _canonical_theme(
            "night",
            background=(7, 9, 12),
            panel=(22, 26, 32),
            ink=(245, 246, 248),
            muted=(178, 182, 190),
            rule=(58, 64, 72),
            accent=(76, 190, 238),
        ),
    }
    rooms = plugin._parse_rooms(settings)
    plugin._write_cache(
        plugin._cache_key(rooms, live_radar_module.DEFAULT_API_URL, True),
        {
            "fetched_at": 0,
            "results": [
                {
                    "ok": True,
                    "platform": "twitch",
                    "id": "xqc",
                    "status": {
                        "isLive": True,
                        "title": "Placeholder path",
                        "owner": "xQc",
                        "cover": cover_url,
                        "avatar": avatar_url,
                    },
                }
            ],
        },
    )
    plugin._cover_cache_path(cover_url).write_bytes(b"not an image")
    http_calls = []

    def fail_http():
        http_calls.append("session")
        raise AssertionError("theme-only media must not acquire an HTTP session")

    monkeypatch.setattr(live_radar_module, "get_http_session", fail_http)

    image = plugin.generate_image(settings, FakeDeviceConfig(mode="night"))

    assert image.size == (800, 480)
    assert http_calls == []


def test_theme_only_status_cache_miss_fails_without_provider_calls():
    plugin = _plugin()
    _memory_cache(plugin)
    calls = {"statuses": 0}

    def fake_statuses(*_args):
        calls["statuses"] += 1
        return []

    plugin._fetch_statuses = fake_statuses

    with pytest.raises(RuntimeError, match="warm .*cache"):
        plugin.generate_image(
            {
                "roomsText": "twitch|xqc|xQc",
                "fetchAvatars": "false",
                "showSnapshots": "false",
                "_theme_render_only": True,
                "_inkypi_theme": _canonical_theme(
                    "day",
                    background=(255, 255, 255),
                    panel=(255, 255, 255),
                    ink=(0, 0, 0),
                    muted=(74, 78, 84),
                    rule=(185, 188, 194),
                    accent=(24, 92, 150),
                ),
            },
            FakeDeviceConfig(mode="day"),
        )

    assert calls == {"statuses": 0}


def test_title_logo_renders_without_outer_box_in_both_themes():
    plugin = _plugin()
    source = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    for px in range(14, 26):
        for py in range(14, 26):
            source.putpixel((px, py), (255, 255, 255, 255))
    plugin._load_title_logo = lambda: source

    for theme_mode, bg in (("dark", (0, 0, 0)), ("light", (255, 255, 255))):
        theme = plugin._theme({"themeMode": theme_mode}, FakeDeviceConfig(mode="day"))
        image = Image.new("RGB", (80, 80), bg)

        assert plugin._paste_title_logo(image, 12, 14, 40, theme)
        assert image.getpixel((12, 14)) == bg
        assert image.getpixel((52, 54)) == bg


def test_title_logo_layout_scales_logo_body_by_40_percent():
    base_size = max(34, int(480 * 0.09))
    logo_size, logo_y = LiveRadar._title_logo_layout(480)

    assert logo_size == round(base_size * TITLE_LOGO_SCALE)
    assert TITLE_LOGO_SCALE == 1.4
    assert logo_y < 16


def test_header_art_asset_is_transparent_measured_strip():
    path = Path(live_radar_module.PLUGIN_DIR) / HEADER_ART_FILE

    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert image.size == HEADER_ART_SIZE
        assert image.getchannel("A").getextrema()[0] == 0


def test_title_wordmark_asset_is_transparent_measured_strip():
    path = Path(live_radar_module.PLUGIN_DIR) / TITLE_WORDMARK_FILE

    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert image.size == TITLE_WORDMARK_SIZE
        alpha = image.getchannel("A")
        assert alpha.getextrema()[0] == 0
        assert alpha.getbbox() is not None
        assert alpha.getpixel((0, 0)) == 0
        assert alpha.getpixel((image.width - 1, 0)) == 0


def test_section_title_wordmark_assets_are_transparent_measured_strips():
    for title, filename in SECTION_TITLE_WORDMARK_FILES.items():
        path = Path(live_radar_module.PLUGIN_DIR) / filename

        with Image.open(path) as image:
            assert image.mode == "RGBA"
            assert image.size == SECTION_TITLE_WORDMARK_SIZES[title]
            alpha = image.getchannel("A")
            assert alpha.getextrema()[0] == 0
            assert alpha.getbbox() is not None
            visible_pixels = sum(1 for value in alpha.getdata() if value > 0)
            assert visible_pixels < image.width * image.height * 0.45


def test_section_title_wordmark_offsets_count_pill(monkeypatch):
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig(mode="day"))
    image = Image.new("RGB", (180, 60), theme["bg"])
    draw = ImageDraw.Draw(image)
    source = Image.new("RGBA", SECTION_TITLE_WORDMARK_SIZE, (0, 0, 0, 255))
    seen = {}

    monkeypatch.setattr(LiveRadar, "_load_section_title_wordmark", staticmethod(lambda title: source))
    plugin._draw_pill = lambda _draw, box, text, _font, **_kwargs: seen.update(box=box, text=text)

    plugin._draw_section_title(image, draw, 10, 20, "LIVE TOO", 5, theme, plugin._font(13, "bold"))

    assert image.getpixel((14, 20)) != theme["bg"]
    assert seen["text"] == "5"
    assert seen["box"][0] == 10 + SECTION_TITLE_WORDMARK_SIZE[0] + 8

def test_dashboard_uses_generated_wordmark_instead_of_plain_header_text(monkeypatch):
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig(mode="day"))
    seen = {}
    text_calls = []

    def fake_paste_title_wordmark(image, x, y, size, theme):
        seen["wordmark"] = (int(x), int(y), tuple(int(value) for value in size))
        return (int(x), int(y), int(x) + 190, int(y) + int(size[1]))

    original_text = ImageDraw.ImageDraw.text

    def capture_text(self, xy, text, *args, **kwargs):
        text_calls.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    plugin._paste_title_wordmark = fake_paste_title_wordmark
    plugin._draw_header_art = lambda image, box: True
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)

    plugin._render_dashboard([], (800, 480), theme, datetime.now(timezone.utc), False, None)

    logo_size, _logo_y = LiveRadar._title_logo_layout(480)
    expected_x = max(14, int(800 * 0.02)) + logo_size + 10 + TITLE_WORDMARK_OFFSET_X
    assert seen["wordmark"][0] == expected_x
    assert seen["wordmark"][2] == TITLE_WORDMARK_SIZE
    assert "LiveRadar" not in text_calls
    assert "STREAM CARD WALL" not in text_calls


def test_dashboard_falls_back_to_plain_header_text_when_wordmark_missing(monkeypatch):
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig(mode="day"))
    text_calls = []
    original_text = ImageDraw.ImageDraw.text

    def capture_text(self, xy, text, *args, **kwargs):
        text_calls.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    plugin._paste_title_wordmark = lambda *args, **kwargs: None
    plugin._draw_header_art = lambda image, box: True
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)

    plugin._render_dashboard([], (800, 480), theme, datetime.now(timezone.utc), False, None)

    assert "LiveRadar" in text_calls
    assert "STREAM CARD WALL" in text_calls


def test_dashboard_positions_header_art_between_title_and_status():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig(mode="day"))
    seen = {}

    def fake_draw_header_art(image, box):
        seen["box"] = tuple(int(value) for value in box)
        return True

    def fake_paste_title_wordmark(image, x, y, size, theme):
        seen["wordmark_size"] = tuple(int(value) for value in size)
        return (int(x), int(y), int(x) + 200, int(y) + int(size[1]))

    plugin._draw_header_art = fake_draw_header_art
    plugin._paste_title_wordmark = fake_paste_title_wordmark
    plugin._render_dashboard([], (800, 480), theme, datetime.now(timezone.utc), False, None)

    left, top, right, bottom = seen["box"]
    assert seen["wordmark_size"] == TITLE_WORDMARK_SIZE
    assert TITLE_WORDMARK_OFFSET_X == -35
    assert bottom - top == HEADER_ART_SIZE[1]
    assert 8 <= top <= 13
    assert 74 <= bottom <= 76
    assert 250 <= left <= 280
    assert right <= 556
    assert right - left >= 240


def test_status_total_badges_use_semantic_backgrounds_in_both_themes():
    plugin = _plugin()

    light_theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig(mode="day"))
    dark_theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig(mode="night"))

    assert plugin._status_total_palette("live", light_theme) == (
        LIVE_STATUS_DOT,
        (0, 0, 0),
        light_theme["ink"],
    )
    assert plugin._status_total_palette("replay", light_theme) == (
        STATUS_TOTAL_FILLS["replay"],
        (0, 0, 0),
        light_theme["ink"],
    )
    assert plugin._status_total_palette("offline", light_theme) == (
        STATUS_TOTAL_FILLS["offline"],
        (0, 0, 0),
        light_theme["ink"],
    )

    assert plugin._status_total_palette("live", dark_theme) == (
        LIVE_STATUS_DOT,
        (0, 0, 0),
        dark_theme["ink"],
    )
    assert plugin._status_total_palette("replay", dark_theme) == (
        STATUS_TOTAL_FILLS["replay"],
        (0, 0, 0),
        dark_theme["ink"],
    )
    assert plugin._status_total_palette("offline", dark_theme) == (
        STATUS_TOTAL_DARK_OFFLINE_FILL,
        (255, 255, 255),
        dark_theme["ink"],
    )


def test_generate_image_returns_error_panel_when_render_crashes():
    plugin = _plugin()
    _memory_cache(plugin)
    plugin._fetch_statuses = lambda rooms, api_url, timeout, fetch_avatars: [
        {
            "ok": True,
            "platform": "twitch",
            "id": "xqc",
            "status": {"isLive": True, "owner": "xQc", "title": "Stream", "platform": "twitch", "id": "xqc"},
        }
    ]
    plugin._render_dashboard = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("layout exploded"))

    image = plugin.generate_image(
        {"roomsText": "twitch|xqc|xQc", "forceRefresh": "true", "themeMode": "dark"},
        FakeDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert len(set(image.crop((20, 20, 780, 180)).getdata())) > 2


def test_generate_image_requests_avatars_by_default():

    plugin = _plugin()
    _memory_cache(plugin)
    seen = {}

    def fake_fetch(rooms, api_url, timeout, fetch_avatars):
        seen["fetch_avatars"] = fetch_avatars
        return [
            {
                "ok": True,
                "platform": "twitch",
                "id": "xqc",
                "status": {"isLive": False, "title": "Offline", "owner": "xQc"},
            }
        ]

    plugin._fetch_statuses = fake_fetch

    plugin.generate_image(
        {
            "roomsText": "twitch|xqc|xQc",
            "themeMode": "dark",
            "cacheSeconds": "20",
        },
        FakeDeviceConfig(),
    )

    assert seen["fetch_avatars"] is True


def test_generate_image_draws_cover_snapshot_band():
    plugin = _plugin()
    _memory_cache(plugin)
    cover = Image.new("RGB", (120, 60), (0, 0, 0))
    for x in range(120):
        shade = int(255 * (x / 119))
        for y in range(60):
            cover.putpixel((x, y), (shade, 48, 255 - shade))

    plugin._load_cover_source = lambda url, cache_seconds: cover if url == "https://covers.test/live.jpg" else None
    plugin._fetch_statuses = lambda rooms, api_url, timeout, fetch_avatars: [
        {
            "ok": True,
            "platform": "twitch",
            "id": "xqc",
            "status": {
                "isLive": True,
                "title": "Live with a real cover",
                "owner": "xQc",
                "cover": "https://covers.test/live.jpg",
                "heatValue": 12345,
                "startTime": (time.time() - 120) * 1000,
            },
        }
    ]

    image = plugin.generate_image(
        {
            "roomsText": "twitch|xqc|xQc",
            "themeMode": "dark",
            "cacheSeconds": "20",
            "showSnapshots": "true",
        },
        FakeDeviceConfig(),
    )

    snapshot_band = image.crop((30, 112, 770, 160))
    assert len(set(snapshot_band.getdata())) > 8
    assert any(r != g or g != b for r, g, b in snapshot_band.getdata())


def test_snapshot_header_fills_available_area_without_letterboxing():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (280, 180), theme["bg"])
    draw = ImageDraw.Draw(image)
    cover = Image.new("RGB", (60, 120), (24, 180, 240))
    plugin._load_cover_source = lambda url, cache_seconds: cover if url == "https://covers.test/tall.jpg" else None
    card = {
        "platform": "twitch",
        "id": "xqc",
        "status": "live",
        "cover": "https://covers.test/tall.jpg",
    }

    header_h = plugin._draw_snapshot_header(image, draw, (20, 20, 220, 150), card, theme, True, 90)
    mid_y = 20 + header_h // 2

    assert image.getpixel((21, mid_y)) == (24, 180, 240)
    assert image.getpixel((238, mid_y)) == (24, 180, 240)


def test_large_live_card_draws_avatar_in_lower_left():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig(mode="day"))
    image = Image.new("RGB", (300, 220), theme["bg"])
    draw = ImageDraw.Draw(image)
    cover = Image.new("RGB", (160, 90), (30, 120, 220))
    avatar = Image.new("RGB", (80, 80), (220, 40, 90))
    plugin._load_cover_source = lambda url, cache_seconds: cover if url == "https://covers.test/live.jpg" else None
    plugin._load_avatar_source = lambda url, cache_seconds: avatar if url == "https://avatars.test/xqc.png" else None

    card = {
        "platform": "twitch",
        "id": "xqc",
        "label": "xQc",
        "is_fav": True,
        "owner": "xQc",
        "title": "Checking the layout with a real avatar",
        "heat": 1234,
        "start_time": None,
        "cover": "https://covers.test/live.jpg",
        "avatar": "https://avatars.test/xqc.png",
        "is_error": False,
        "favorite_rank": 1,
        "status": "live",
    }

    plugin._draw_card(image, draw, (20, 20, 250, 170), card, theme, large=True, show_snapshot=True)

    avatar_area = image.crop((38, 145, 60, 167))
    assert (220, 40, 90) in set(avatar_area.getdata())
    assert any(r != g or g != b for r, g, b in avatar_area.getdata())


def test_card_detail_text_size_nudge_is_uniform(monkeypatch):
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig(mode="day"))
    image = Image.new("RGB", (760, 320), theme["bg"])
    draw = ImageDraw.Draw(image)
    base_card = {
        "platform": "twitch",
        "id": "xqc",
        "label": "xQc",
        "is_fav": False,
        "owner": "xQc",
        "title": "Uniform title sizing check",
        "heat": 57000,
        "start_time": None,
        "cover": "",
        "avatar": "",
        "is_error": False,
        "favorite_rank": None,
        "status": "live",
    }

    wrapped_start_sizes = []
    fit_font_requests = []
    font_requests = []
    original_fit_wrapped_text = plugin._fit_wrapped_text
    original_fit_font = plugin._fit_font
    original_font = plugin._font

    def capture_fit_wrapped_text(draw_obj, text, max_width, max_height, max_lines, start_size, min_size, weight="normal"):
        wrapped_start_sizes.append(start_size)
        return original_fit_wrapped_text(draw_obj, text, max_width, max_height, max_lines, start_size, min_size, weight)

    def capture_fit_font(draw_obj, text, max_width, start_size, min_size, weight="normal"):
        fit_font_requests.append((str(text), start_size, min_size, weight))
        return original_fit_font(draw_obj, text, max_width, start_size, min_size, weight)

    def capture_font(size, weight="normal"):
        font_requests.append((size, weight))
        return original_font(size, weight)

    monkeypatch.setattr(plugin, "_fit_wrapped_text", capture_fit_wrapped_text)
    monkeypatch.setattr(plugin, "_fit_font", capture_fit_font)
    monkeypatch.setattr(plugin, "_font", capture_font)

    plugin._draw_card(image, draw, (10, 10, 240, 118), base_card, theme, large=True, show_snapshot=False)
    monkeypatch.setattr(plugin, "_draw_snapshot_header", lambda *_args, **_kwargs: 104)
    plugin._draw_card(image, draw, (270, 10, 240, 170), base_card, theme, large=True, show_snapshot=True)
    plugin._draw_card(image, draw, (10, 210, 220, 60), base_card, theme, large=False, show_snapshot=False)
    plugin._draw_compact_card(image, draw, (270, 210, 250, 52), base_card, theme)

    assert live_radar_module.CARD_DETAIL_FONT_SIZE_NUDGE == 2
    assert live_radar_module.CARD_DETAIL_Y_NUDGE == 2
    assert live_radar_module.LIVE_CARD_TITLE_MAX_SIZE in wrapped_start_sizes
    assert live_radar_module.LIVE_CARD_SNAPSHOT_TITLE_MAX_SIZE in wrapped_start_sizes
    assert (10 + live_radar_module.CARD_DETAIL_FONT_SIZE_NUDGE, "bold") in font_requests
    assert (
        "Uniform title sizing check",
        live_radar_module.COMPACT_CARD_DETAIL_MAX_SIZE,
        7,
        "bold",
    ) in fit_font_requests


def _has_vivid_pixels(image, *, minimum=3):
    vivid = 0
    rgb = image.convert("RGB")
    for y in range(rgb.height):
        for x in range(rgb.width):
            red, green, blue = rgb.getpixel((x, y))
            if max(red, green, blue) - min(red, green, blue) > 35:
                vivid += 1
                if vivid >= minimum:
                    return True
    return False


def test_live_radar_uses_local_transparent_color_icon_assets():
    plugin_dir = Path(live_radar_module.PLUGIN_DIR)
    expected = {
        "platform_icons": ("bilibili.png", "douyu.png", "twitch.png"),
        "status_icons": ("fav.png", "live.png"),
    }

    for directory, names in expected.items():
        assert (plugin_dir / directory / "SOURCES.md").is_file()
        for name in names:
            path = plugin_dir / directory / name
            assert path.is_file(), f"missing local LiveRadar icon asset: {path}"
            with Image.open(path) as source:
                icon = source.convert("RGBA")
            alpha = icon.getchannel("A")
            assert icon.size == (64, 64)
            assert alpha.getextrema() == (0, 255)
            assert alpha.getpixel((0, 0)) == 0
            assert _has_vivid_pixels(icon)


@pytest.mark.parametrize("platform", ("bilibili", "douyu", "twitch"))
def test_platform_badges_paste_alpha_assets_without_generated_shell(monkeypatch, platform):
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    background = (21, 22, 24)
    image = Image.new("RGB", (52, 42), background)
    draw = ImageDraw.Draw(image)

    def fail_generated_mark(*_args, **_kwargs):
        raise AssertionError("local alpha icon must bypass generated badge/mark drawing")

    monkeypatch.setattr(plugin, "_rounded_rectangle", fail_generated_mark)
    monkeypatch.setattr(plugin, "_draw_bilibili_mark", fail_generated_mark)
    monkeypatch.setattr(plugin, "_draw_douyu_mark", fail_generated_mark)
    monkeypatch.setattr(plugin, "_draw_twitch_mark", fail_generated_mark)

    plugin._draw_platform_badge(
        image,
        draw,
        (9, 7, 43, 35),
        platform,
        fill=theme["ink"],
        ink=theme["bg"],
        outline=theme["ink"],
    )

    assert image.getpixel((9, 7)) == background
    assert _has_vivid_pixels(image.crop((9, 7, 43, 35)))


@pytest.mark.parametrize("kind", ("fav", "live"))
def test_status_badges_paste_alpha_assets_without_generated_shell(monkeypatch, kind):
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    background = (21, 22, 24)
    image = Image.new("RGB", (44, 44), background)
    draw = ImageDraw.Draw(image)

    monkeypatch.setattr(
        plugin,
        "_rounded_rectangle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("local alpha icon must bypass generated status badge drawing")
        ),
    )

    plugin._draw_icon_badge(
        image,
        draw,
        (8, 8, 36, 36),
        kind,
        fill=theme["panel"],
        ink=theme["ink"],
        outline=theme["line"],
    )

    assert image.getpixel((8, 8)) == background
    assert _has_vivid_pixels(image.crop((8, 8, 36, 36)))


def test_real_dashboard_contains_colored_platform_and_status_icon_pixels(monkeypatch):
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    platform_boxes = {}
    status_boxes = {}
    draw_platform = plugin._draw_platform_badge
    draw_status = plugin._draw_icon_badge

    def capture_platform(image, draw, box, platform_key, *args, **kwargs):
        result = draw_platform(image, draw, box, platform_key, *args, **kwargs)
        platform_boxes.setdefault(platform_key, []).append(tuple(int(value) for value in box))
        return result

    def capture_status(image, draw, box, kind, *args, **kwargs):
        result = draw_status(image, draw, box, kind, *args, **kwargs)
        status_boxes.setdefault(kind, []).append(tuple(int(value) for value in box))
        return result

    monkeypatch.setattr(plugin, "_draw_platform_badge", capture_platform)
    monkeypatch.setattr(plugin, "_draw_icon_badge", capture_status)
    cards = [
        {
            "platform": platform,
            "id": f"{platform}-live",
            "owner": f"{platform} streamer",
            "label": "",
            "title": "Live stream",
            "status": "live",
            "is_fav": True,
            "heat": 100 - index,
            "start_time": None,
            "cover": "",
            "avatar": "",
        }
        for index, platform in enumerate(("bilibili", "douyu", "twitch"))
    ]

    dashboard = plugin._render_dashboard(
        cards,
        (800, 480),
        theme,
        datetime.now(timezone.utc),
        False,
        None,
        {
            "max_live_cards": 3,
            "max_offline_cards": 3,
            "show_snapshots": False,
            "snapshot_cache_seconds": 60,
            "avatar_cache_seconds": 60,
        },
    )

    for platform in ("bilibili", "douyu", "twitch"):
        assert platform_boxes.get(platform)
        assert any(_has_vivid_pixels(dashboard.crop(box)) for box in platform_boxes[platform])
    for kind in ("fav", "live"):
        assert status_boxes.get(kind)
        assert any(_has_vivid_pixels(dashboard.crop(box)) for box in status_boxes[kind])


def test_light_theme_live_cards_use_white_shell():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig(mode="day"))

    assert plugin._card_palette("live", theme) == (
        theme["panel"],
        theme["ink"],
        theme["ink"],
        theme["ink"],
    )


def test_snapshot_header_is_only_for_large_live_cards():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (260, 160), (0, 0, 0))
    draw = ImageDraw.Draw(image)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("snapshot header should not be drawn")

    plugin._draw_snapshot_header = fail_if_called
    base_card = {
        "platform": "twitch",
        "id": "xqc",
        "label": "xQc",
        "is_fav": False,
        "owner": "xQc",
        "title": "Live title",
        "heat": 0,
        "start_time": None,
        "cover": "https://covers.test/live.jpg",
        "avatar": "",
        "is_error": False,
        "favorite_rank": None,
    }

    plugin._draw_card(image, draw, (10, 10, 220, 60), {**base_card, "status": "live"}, theme, large=False, show_snapshot=True)
    plugin._draw_card(image, draw, (10, 80, 220, 60), {**base_card, "status": "offline"}, theme, large=True, show_snapshot=True)


def test_compact_card_draws_avatar_image():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (280, 80), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    avatar = Image.new("RGB", (60, 60), (0, 0, 0))
    for x in range(60):
        color = (255, 48, 16) if x > 30 else (32, 180, 240)
        for y in range(60):
            avatar.putpixel((x, y), color)
    plugin._load_avatar_source = lambda url, cache_seconds: avatar if url == "https://avatars.test/xqc.png" else None

    card = {
        "platform": "twitch",
        "id": "xqc",
        "label": "xQc",
        "is_fav": True,
        "owner": "xQc",
        "title": "Offline",
        "heat": 0,
        "start_time": None,
        "cover": "",
        "avatar": "https://avatars.test/xqc.png",
        "is_error": False,
        "favorite_rank": None,
        "status": "offline",
    }

    plugin._draw_compact_card(image, draw, (10, 10, 250, 52), card, theme)

    avatar_area = image.crop((24, 18, 58, 52))
    assert len(set(avatar_area.getdata())) > 2
    assert any(r != g or g != b for r, g, b in avatar_area.getdata())


def test_compact_card_draws_offline_doodle_at_native_size():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (430, 80), theme["bg"])
    draw = ImageDraw.Draw(image)
    seen = {}
    plugin._draw_offline_doodle = lambda _image, _draw, box, _theme, card: seen.update(box=box, card=card["id"])
    card = {
        "platform": "twitch",
        "id": "zard1991",
        "label": "",
        "is_fav": False,
        "owner": "zard1991",
        "title": "",
        "heat": 0,
        "start_time": None,
        "cover": "",
        "avatar": "",
        "is_error": False,
        "favorite_rank": None,
        "status": "offline",
    }

    plugin._draw_compact_card(image, draw, (10, 10, 377, 48), card, theme)

    assert seen["box"][2:] == live_radar_module.COMPACT_PLACEHOLDER_SIZE
    assert seen["box"][0] > 10 + 10 + 32 + 10
    assert seen["box"][0] + seen["box"][2] < 10 + 377 - 30
    assert seen["card"] == "zard1991"


SPECTRA_SOLID = {(0, 0, 0), (255, 255, 255), (255, 255, 0), (255, 0, 0), (0, 0, 255), (0, 255, 0)}


def _doodle_card(index):
    return {"platform": "douyu" if index % 2 else "bilibili", "id": f"room-{index}"}


def test_offline_doodles_are_stable_per_streamer_and_vary_between_streamers():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())

    def render(card):
        image = Image.new("RGB", (170, 40), theme["bg"])
        plugin._draw_offline_doodle(image, ImageDraw.Draw(image), (10, 8, 150, 24), theme, card)
        return image.tobytes()

    assert render(_doodle_card(3)) == render(_doodle_card(3))
    assert len({render(_doodle_card(index)) for index in range(12)}) >= 4


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_offline_doodles_use_only_solid_panel_colours_and_stay_in_their_box(mode):
    plugin = _plugin()
    theme = plugin._theme({"themeMode": mode}, FakeDeviceConfig())
    sentinel = (12, 34, 56)
    allowed = SPECTRA_SOLID | {theme["panel"], theme["ink"], sentinel}
    for index in range(12):
        image = Image.new("RGB", (170, 40), sentinel)
        image.paste(theme["panel"], (10, 8, 160, 32))
        plugin._draw_offline_doodle(image, ImageDraw.Draw(image), (10, 8, 150, 24), theme, _doodle_card(index))

        assert set(image.getdata()) <= allowed
        assert image.crop((0, 0, 170, 8)).getcolors() == [(170 * 8, sentinel)]
        assert image.crop((160, 0, 170, 40)).getcolors() == [(10 * 40, sentinel)]


def test_no_signal_placeholder_draws_solid_colour_bars_around_the_platform_logo():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = Image.new("RGB", (120, 70), (12, 34, 56))
    badges = []
    plugin._draw_platform_badge = lambda _image, _draw, box, platform_key, **_kwargs: badges.append((box, platform_key))

    plugin._draw_snapshot_placeholder(image, ImageDraw.Draw(image), (10, 10, 105, 47), {"platform": "douyu"}, theme, False)

    bar_row = [image.getpixel((x, 12)) for x in range(10, 106)]
    assert set(bar_row) <= SPECTRA_SOLID
    assert {(255, 255, 0), (0, 255, 0), (255, 0, 0), (0, 0, 255)} <= set(bar_row)
    (left, top, right, bottom), platform = badges[0]
    assert platform == "douyu"
    assert abs((left + right) / 2 - 57.5) <= 2 and 10 < top < bottom < 47
    assert image.getpixel((9, 30)) == image.getpixel((106, 30)) == (12, 34, 56)


def test_large_no_signal_placeholder_says_no_signal():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = Image.new("RGB", (270, 150), theme["bg"])
    texts = []

    class DrawSpy:
        def __init__(self, draw):
            self._draw = draw

        def __getattr__(self, name):
            return getattr(self._draw, name)

        def text(self, xy, text, *args, **kwargs):
            texts.append(text)
            return self._draw.text(xy, text, *args, **kwargs)

    plugin._draw_snapshot_placeholder(
        image, DrawSpy(ImageDraw.Draw(image)), (10, 10, 257, 139), {"platform": "bilibili"}, theme, True,
    )

    assert "NO SIGNAL" in texts


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_status_tile_says_how_many_are_live_and_stays_in_its_box(mode):
    plugin = _plugin()
    theme = plugin._theme({"themeMode": mode}, FakeDeviceConfig())
    sentinel = (12, 34, 56)
    image = Image.new("RGB", (220, 80), sentinel)
    texts = []

    class DrawSpy:
        def __init__(self, draw):
            self._draw = draw

        def __getattr__(self, name):
            return getattr(self._draw, name)

        def text(self, xy, text, *args, **kwargs):
            texts.append(text)
            return self._draw.text(xy, text, *args, **kwargs)

    plugin._draw_live_status_tile(image, DrawSpy(ImageDraw.Draw(image)), (10, 10, 188, 53), theme, 10)

    assert texts == ["10 位在线", "全部已显示"]
    inside = set(image.crop((10, 10, 198, 63)).getdata())
    assert (0, 255, 0) in inside and sentinel not in inside
    assert image.getpixel((9, 30)) == image.getpixel((198, 30)) == image.getpixel((50, 63)) == sentinel

def test_live_queue_section_fits_two_columns_of_remaining_live_rows():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (420, 170), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    cards = [
        {
            "platform": "twitch",
            "id": f"streamer{i}",
            "label": "",
            "is_fav": i == 0,
            "owner": f"Streamer {i}",
            "title": "Live",
            "heat": 100 + i,
            "start_time": None,
            "cover": "",
            "avatar": "",
            "is_error": False,
            "favorite_rank": None,
            "status": "live",
        }
        for i in range(9)
    ]

    visible = plugin._draw_live_queue_section(image, draw, (10, 10, 380, 128), "LIVE TOO", cards, theme, 8)

    assert visible == 8
    assert len(set(image.crop((16, 38, 386, 134)).getdata())) > 2
    assert len(set(image.crop((210, 38, 386, 134)).getdata())) > 2


def test_snapshot_mini_candidates_prefer_cover_cards_and_skip_visible_live():
    plugin = _plugin()

    visible_live = {
        "platform": "twitch",
        "id": "xqc",
        "owner": "xQc",
        "label": "",
        "status": "live",
        "is_fav": True,
        "heat": 999,
        "cover": "https://covers.test/xqc.jpg",
    }
    candidates = [
        {
            "platform": "twitch",
            "id": "offline-no-cover",
            "owner": "No Cover",
            "label": "",
            "status": "offline",
            "is_fav": True,
            "heat": 1000,
            "cover": "",
        },
        {
            "platform": "douyu",
            "id": "replay-cover",
            "owner": "Replay Cover",
            "label": "",
            "status": "replay",
            "is_fav": False,
            "heat": 1,
            "cover": "https://covers.test/replay.jpg",
        },
        {
            "platform": "bilibili",
            "id": "offline-cover",
            "owner": "Offline Cover",
            "label": "",
            "status": "offline",
            "is_fav": False,
            "heat": 2,
            "cover": "https://covers.test/offline.jpg",
        },
        visible_live,
    ]

    picked = plugin._snapshot_mini_candidates(candidates, [visible_live], max_items=2)

    assert [card["id"] for card in picked] == ["replay-cover", "offline-cover"]


def test_snapshot_mini_section_labels_replay_when_replay_card_is_visible(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (240, 120), "white")
    draw = ImageDraw.Draw(image)
    theme = plugin._theme({"themeMode": "day"}, FakeDeviceConfig())
    labels = []
    monkeypatch.setattr(
        plugin,
        "_draw_section_title",
        lambda _image, _draw, _x, _y, title, _count, _theme, _font: labels.append(title),
    )
    monkeypatch.setattr(plugin, "_draw_snapshot_mini_card", lambda *_args, **_kwargs: None)

    plugin._draw_snapshot_mini_section(
        image,
        draw,
        (10, 10, 220, 100),
        "SNAPSHOT MINI",
        [{"platform": "bilibili", "id": "1", "status": "replay", "title": "昨夜回放"}],
        theme,
    )

    assert labels == ["REPLAY"]


def test_replay_snapshot_is_grayscale_while_live_snapshot_stays_colored():
    plugin = _plugin()
    source = Image.new("RGB", (32, 18), (210, 40, 10))

    replay = plugin._prepare_snapshot_image(source, (16, 9), "replay")
    live = plugin._prepare_snapshot_image(source, (16, 9), "live")

    replay_pixel = replay.getpixel((4, 4))
    assert replay_pixel[0] == replay_pixel[1] == replay_pixel[2]
    assert live.getpixel((4, 4)) == (210, 40, 10)


def test_replay_snapshot_mini_card_draws_room_title(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (260, 110), "white")
    draw = ImageDraw.Draw(image)
    theme = plugin._theme({"themeMode": "day"}, FakeDeviceConfig())
    fitted_text = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, font, max_width):
        fitted_text.append(str(text))
        return original_fit_text(draw_obj, text, font, max_width)

    monkeypatch.setattr(plugin, "_fit_text", capture_fit_text)
    monkeypatch.setattr(plugin, "_load_cover_source", lambda *_args: Image.new("RGB", (160, 90), "red"))
    monkeypatch.setattr(plugin, "_draw_avatar", lambda *_args, **_kwargs: None)
    card = {
        "platform": "bilibili",
        "id": "545318",
        "owner": "主播名",
        "label": "主播名",
        "title": "这是房间标题",
        "status": "replay",
        "start_time": None,
    }

    plugin._draw_snapshot_mini_card(image, draw, (10, 20, 240, 76), card, theme)

    assert "这是房间标题" in fitted_text


def test_snapshot_mini_section_draws_cover_thumbnails():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (260, 130), theme["bg"])
    draw = ImageDraw.Draw(image)
    cover = Image.new("RGB", (80, 50), (24, 180, 240))
    avatar = Image.new("RGB", (40, 40), (240, 80, 32))
    plugin._load_cover_source = lambda url, cache_seconds: cover if url == "https://covers.test/offline.jpg" else None
    plugin._load_avatar_source = lambda url, cache_seconds: avatar if url == "https://avatars.test/offline.png" else None
    cards = [
        {
            "platform": "twitch",
            "id": "offline-cover",
            "owner": "Offline Cover",
            "label": "",
            "title": "Recent stream",
            "status": "offline",
            "is_fav": False,
            "heat": 0,
            "cover": "https://covers.test/offline.jpg",
            "avatar": "https://avatars.test/offline.png",
        }
    ]

    visible = plugin._draw_snapshot_mini_section(image, draw, (10, 10, 220, 100), "SNAPSHOT MINI", cards, theme)

    assert visible == 1
    assert (24, 180, 240) in set(image.crop((18, 42, 52, 62)).getdata())
    assert (24, 180, 240) in set(image.crop((112, 58, 126, 72)).getdata())
    assert (240, 80, 32) not in set(image.crop((18, 82, 40, 104)).getdata())
    assert (240, 80, 32) in set(image.crop((140, 58, 158, 78)).getdata())


def test_snapshot_mini_card_keeps_single_tall_thumbnail_landscape():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (280, 130), theme["bg"])
    draw = ImageDraw.Draw(image)
    cover = Image.new("RGB", (160, 90), (24, 180, 240))
    plugin._load_cover_source = lambda url, cache_seconds: cover if url == "https://covers.test/live.jpg" else None
    plugin._load_avatar_source = lambda url, cache_seconds: None
    card = {
        "platform": "twitch",
        "id": "live-cover",
        "owner": "Live Cover",
        "label": "",
        "title": "Live stream",
        "status": "live",
        "is_fav": False,
        "heat": 0,
        "start_time": None,
        "cover": "https://covers.test/live.jpg",
        "avatar": "",
    }

    plugin._draw_snapshot_mini_card(image, draw, (10, 20, 220, 76), card, theme)

    assert image.getpixel((120, 52)) == (24, 180, 240)
    assert image.getpixel((136, 52)) != (24, 180, 240)


def test_snapshot_mini_section_keeps_two_thumbnails_landscape():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (420, 170), theme["bg"])
    draw = ImageDraw.Draw(image)
    covers = {
        "https://covers.test/0.jpg": Image.new("RGB", (160, 90), (24, 180, 240)),
        "https://covers.test/1.jpg": Image.new("RGB", (160, 90), (220, 90, 50)),
    }
    plugin._load_cover_source = lambda url, cache_seconds: covers.get(url)
    plugin._load_avatar_source = lambda url, cache_seconds: None
    cards = [
        {
            "platform": "twitch",
            "id": f"live-cover-{index}",
            "owner": f"Live Cover {index}",
            "label": "",
            "title": "Live stream",
            "status": "live",
            "is_fav": False,
            "heat": 0,
            "start_time": None,
            "cover": f"https://covers.test/{index}.jpg",
            "avatar": "",
        }
        for index in range(2)
    ]

    visible = plugin._draw_snapshot_mini_section(image, draw, (10, 10, 376, 129), "LIVE TOO", cards, theme)

    assert visible == 2
    assert image.getpixel((108, 80)) == (24, 180, 240)
    assert image.getpixel((300, 80)) == (220, 90, 50)
    assert image.getpixel((18, 126)) == theme["bg"]


def test_snapshot_mini_section_keeps_three_thumbnails_landscape():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (420, 170), theme["bg"])
    draw = ImageDraw.Draw(image)
    covers = {
        "https://covers.test/0.jpg": Image.new("RGB", (160, 90), (24, 180, 240)),
        "https://covers.test/1.jpg": Image.new("RGB", (160, 90), (220, 90, 50)),
        "https://covers.test/2.jpg": Image.new("RGB", (160, 90), (90, 160, 120)),
    }
    plugin._load_cover_source = lambda url, cache_seconds: covers.get(url)
    plugin._load_avatar_source = lambda url, cache_seconds: None
    cards = [
        {
            "platform": "twitch",
            "id": f"live-cover-{index}",
            "owner": f"Live Cover {index}",
            "label": "",
            "title": "Live stream",
            "status": "live",
            "is_fav": False,
            "heat": 0,
            "start_time": None,
            "cover": f"https://covers.test/{index}.jpg",
            "avatar": "",
        }
        for index in range(3)
    ]

    visible = plugin._draw_snapshot_mini_section(image, draw, (10, 10, 376, 129), "LIVE TOO", cards, theme)

    assert visible == 3
    assert image.getpixel((82, 50)) == (24, 180, 240)
    assert image.getpixel((274, 50)) == (220, 90, 50)
    assert image.getpixel((82, 106)) == (90, 160, 120)


def test_snapshot_mini_section_draws_exact_size_placeholder_for_empty_fourth_slot():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (420, 170), theme["bg"])
    draw = ImageDraw.Draw(image)
    seen = {}
    plugin._load_cover_source = lambda url, cache_seconds: Image.new("RGB", (160, 90), (24, 180, 240))
    plugin._load_avatar_source = lambda url, cache_seconds: None
    plugin._draw_snapshot_mini_placeholder = lambda _image, _draw, box, _theme, **_kwargs: seen.__setitem__("box", box)
    cards = [
        {
            "platform": "twitch",
            "id": f"live-cover-{index}",
            "owner": f"Live Cover {index}",
            "label": "",
            "title": "Live stream",
            "status": "live",
            "is_fav": False,
            "heat": 0,
            "start_time": None,
            "cover": f"https://covers.test/{index}.jpg",
            "avatar": "",
        }
        for index in range(3)
    ]

    visible = plugin._draw_snapshot_mini_section(image, draw, (10, 10, 376, 129), "LIVE TOO", cards, theme)

    assert visible == 3
    assert seen["box"] == (202, 89, 184, 49)


def test_live_queue_section_merges_the_last_rows_empty_cells_into_one_status_tile():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (420, 170), theme["bg"])
    draw = ImageDraw.Draw(image)
    tiles, status = [], []
    plugin._draw_live_queue_tile = lambda _image, _draw, box, card, *_args, **_kwargs: tiles.append(box)
    plugin._draw_live_status_tile = lambda _image, _draw, box, _theme, live_count, **_kwargs: status.append((box, live_count))
    cards = [{"platform": "twitch", "id": f"live-{index}", "status": "live"} for index in range(5)]

    visible = plugin._draw_live_queue_section(
        image, draw, (10, 10, 376, 129), "LIVE TOO", cards, theme, max_items=12, live_total=8,
    )

    # Five streams fill three columns by two rows; the one free cell says who is live.
    assert visible == 5
    assert [box[2] for box in tiles] == [125, 126, 125, 125, 126]
    assert status == [((261, 86, 125, 53), 8)]


def test_live_queue_status_tile_spans_every_free_cell_of_the_last_row():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = Image.new("RGB", (620, 170), theme["bg"])
    status = []
    plugin._draw_live_queue_tile = lambda *_args, **_kwargs: None
    plugin._draw_live_status_tile = lambda _image, _draw, box, _theme, live_count, **_kwargs: status.append(box)
    cards = [{"platform": "twitch", "id": f"live-{index}", "status": "live"} for index in range(7)]

    plugin._draw_live_queue_section(
        image, ImageDraw.Draw(image), (10, 10, 564, 129), "LIVE TOO", cards, theme, max_items=12, live_total=10,
    )

    # Seven streams take four columns; the last row's single free cell is the status tile.
    assert status == [(433, 86, 141, 53)]


def test_live_queue_section_draws_one_merged_cover_wall_in_reading_order():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = Image.new("RGB", (420, 170), theme["bg"])
    draw = ImageDraw.Draw(image)
    colours = [(200, 30 + 25 * index, 40) for index in range(8)]
    covers = {
        f"https://covers.test/{index}.jpg": Image.new("RGB", (160, 90), colour)
        for index, colour in enumerate(colours)
    }
    plugin._load_cover_source = lambda url, cache_seconds: covers.get(url)
    cards = [
        _queue_row_card(id=f"live-{index}", platform="bilibili", cover=f"https://covers.test/{index}.jpg")
        for index in range(9)
    ]

    visible = plugin._draw_live_queue_section(image, draw, (10, 10, 376, 129), "LIVE TOO", cards, theme, max_items=8)

    assert visible == 8
    for index, colour in enumerate(colours):
        column, row = index % 4, index // 4
        assert image.getpixel((10 + 94 * column + 56, 34 + 52 * row + 22)) == colour
    # One rounded frame around the whole wall and single 1 px dividers, no per-tile frames.
    assert image.getpixel((10, 34)) == theme["bg"]
    assert image.getpixel((10, 60)) == image.getpixel((140, 34)) == theme["line"]
    assert image.getpixel((104, 56)) == theme["line"]
    assert image.getpixel((103, 56)) == colours[0]
    assert image.getpixel((105, 56)) == colours[1]


def test_live_queue_layout_uses_two_columns_in_narrow_boxes():
    wide = LiveRadar._live_queue_layout((16, 317, 377, 128), 11, 8)
    narrow = LiveRadar._live_queue_layout((14, 520, 220, 250), 11, 8)

    assert (wide["columns"], wide["rows"], wide["visible_count"]) == (4, 2, 8)
    assert (narrow["columns"], narrow["rows"], narrow["visible_count"]) == (2, 4, 8)


def _queue_row_card(**overrides):
    card = {
        "platform": "twitch",
        "id": "hasanabi",
        "owner": "HasanAbi",
        "label": "",
        "title": "Live",
        "status": "live",
        "is_fav": False,
        "heat": 0,
        "start_time": None,
        "cover": "https://covers.test/queue.jpg",
        "avatar": "",
    }
    card.update(overrides)
    return card


class _TextSpy:
    def __init__(self, draw, seen):
        self._draw = draw
        self._seen = seen

    def __getattr__(self, name):
        return getattr(self._draw, name)

    def text(self, xy, text, fill=None, font=None, *args, **kwargs):
        self._seen.append((xy, text, fill, font))
        return self._draw.text(xy, text, fill=fill, font=font, *args, **kwargs)


def _tile_image(theme):
    return Image.new("RGB", (140, 90), theme["bg"])


TILE_BOX = (10, 10, 94, 52)


def _strip_top():
    return TILE_BOX[1] + TILE_BOX[3] - live_radar_module.LIVE_QUEUE_NAME_STRIP_H


def test_live_queue_tile_lays_platform_logo_on_cover_above_brand_lined_name():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = _tile_image(theme)
    texts, badges, cache_seconds = [], [], []
    cover_colour = (24, 180, 240)

    def load_cover(url, seconds):
        cache_seconds.append(seconds)
        return Image.new("RGB", (160, 90), cover_colour)

    plugin._load_cover_source = load_cover
    plugin._draw_platform_badge = lambda _image, _draw, box, platform_key, **_kwargs: badges.append((box, platform_key))
    card = _queue_row_card(platform="douyu", owner="异灵术老师")

    plugin._draw_live_queue_tile(
        image, _TextSpy(ImageDraw.Draw(image), texts), TILE_BOX, card, theme,
        show_snapshot=True, snapshot_cache_seconds=75,
    )

    assert cache_seconds == [75]
    strip_top = _strip_top()
    # The cover is full-bleed across the tile above the name strip.
    assert image.getpixel((10, strip_top - 2)) == image.getpixel((103, strip_top - 2)) == cover_colour
    assert image.getpixel((70, 12)) == cover_colour
    # The platform logo sits on the cover's top-left corner.
    (left, top, right, bottom), platform = badges[0]
    assert platform == "douyu"
    assert 10 < left < right < 10 + 22 and 10 < top < bottom < 10 + 22
    # A 3 px platform-colour line separates cover and name; the name is plain ink.
    brand = live_radar_module.PLATFORM_BRAND_COLORS["douyu"]
    assert [image.getpixel((50, strip_top + offset)) for offset in range(3)] == [brand] * 3
    assert image.getpixel((100, strip_top + 3)) == theme["panel"]
    (name_x, name_y), _name, fill, font = next(item for item in texts if item[1] == "异灵术老师")
    assert fill == theme["ink"]
    assert name_x >= 10 and strip_top + 3 <= name_y + 2
    assert name_y + plugin._line_height(font) <= 10 + 52 + 1


@pytest.mark.parametrize("platform", ["bilibili", "douyu", "twitch", "kick", "picarto"])
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_live_queue_name_line_uses_each_platform_brand_colour(platform, mode):
    plugin = _plugin()
    theme = plugin._theme({"themeMode": mode}, FakeDeviceConfig())
    image = _tile_image(theme)
    plugin._load_cover_source = lambda url, seconds: Image.new("RGB", (160, 90), (24, 180, 240))

    plugin._draw_live_queue_tile(image, ImageDraw.Draw(image), TILE_BOX, _queue_row_card(platform=platform), theme)

    expected = live_radar_module.PLATFORM_BRAND_COLORS.get(platform, theme["line"])
    assert image.getpixel((50, _strip_top() + 1)) == expected


def test_live_queue_tile_shows_uptime_on_the_cover():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = _tile_image(theme)
    texts = []
    plugin._load_cover_source = lambda url, seconds: Image.new("RGB", (160, 90), (24, 180, 240))
    card = _queue_row_card(start_time=(time.time() - 2 * 3600 - 5 * 60 - 20) * 1000)

    plugin._draw_live_queue_tile(image, _TextSpy(ImageDraw.Draw(image), texts), TILE_BOX, card, theme)

    (chip_x, chip_y), _text, fill, _font = next(item for item in texts if item[1] == "2h05m")
    assert fill == (255, 255, 255)
    assert chip_x > 10 + 94 // 2 and chip_y < _strip_top()


def test_live_queue_tile_keeps_favourite_star_in_the_name_strip():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = _tile_image(theme)
    texts, stars = [], []
    plugin._load_cover_source = lambda url, seconds: Image.new("RGB", (160, 90), (24, 180, 240))
    plugin._draw_icon_badge = lambda _image, _draw, box, kind, **_kwargs: stars.append(box) if kind == "fav" else None

    plugin._draw_live_queue_tile(
        image, _TextSpy(ImageDraw.Draw(image), texts), TILE_BOX, _queue_row_card(is_fav=True), theme,
    )

    (name_x, _name_y), name, _fill, font = next(item for item in texts if item[1] == "HasanAbi")
    left, top, right, bottom = stars[0]
    assert top >= _strip_top() + 3 and bottom <= 10 + 52
    assert left >= name_x + ImageDraw.Draw(image).textlength(name, font=font)
    assert right <= 10 + 94


def test_live_queue_tile_skips_covers_when_snapshots_are_disabled():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = _tile_image(theme)
    seen = {}

    def fail_cover(*_args, **_kwargs):
        raise AssertionError("snapshots disabled: queue tiles must not load covers")

    plugin._load_cover_source = fail_cover
    plugin._draw_snapshot_placeholder = lambda _image, _draw, box, card, _theme, large: seen.update(box=box, large=large)

    plugin._draw_live_queue_tile(image, ImageDraw.Draw(image), TILE_BOX, _queue_row_card(), theme, show_snapshot=False)

    assert seen == {"box": (10, 10, 103, _strip_top() - 1), "large": False}


def test_live_queue_tile_without_cover_draws_snapshot_placeholder():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = _tile_image(theme)
    seen = {}
    plugin._load_cover_source = lambda url, seconds: None
    plugin._draw_snapshot_placeholder = lambda _image, _draw, box, card, _theme, large: seen.update(box=box, large=large)

    plugin._draw_live_queue_tile(image, ImageDraw.Draw(image), TILE_BOX, _queue_row_card(cover=""), theme)

    assert seen == {"box": (10, 10, 103, _strip_top() - 1), "large": False}


@pytest.mark.parametrize("platform", ["bilibili", "douyu", "twitch", "picarto"])
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_card_borders_use_the_platform_brand_colour(platform, mode):
    plugin = _plugin()
    theme = plugin._theme({"themeMode": mode}, FakeDeviceConfig())
    plugin._load_cover_source = lambda url, cache_seconds: Image.new("RGB", (160, 90), (24, 180, 240))
    plugin._load_avatar_source = lambda url, cache_seconds: None
    live = _queue_row_card(platform=platform)
    offline = _queue_row_card(platform=platform, status="offline", cover="")
    expected = live_radar_module.PLATFORM_BRAND_COLORS.get(platform)

    def border(draw_card, card, box, palette_status, width):
        image = Image.new("RGB", (box[0] * 2 + box[2], box[1] * 2 + box[3]), theme["bg"])
        draw_card(image, ImageDraw.Draw(image), box, card)
        x, y, w, h = box
        fill, _ink, _muted, line = plugin._card_palette(palette_status, theme)
        colour = expected or line
        # Only the colour changes: the border keeps its original thickness.
        for offset in range(width):
            assert image.getpixel((x + offset, y + h // 2)) == colour
            assert image.getpixel((x + w // 2, y + offset)) == colour
        # (The left edge is followed by the card's ink accent stripe, so check the top.)
        assert image.getpixel((x + w // 2, y + width)) != colour

    border(lambda image, draw, box, card: plugin._draw_card(image, draw, box, card, theme, large=True),
           live, (10, 10, 248, 196), "live", 2)
    border(lambda image, draw, box, card: plugin._draw_compact_card(image, draw, box, card, theme),
           offline, (10, 10, 377, 48), "offline", 1)
    border(lambda image, draw, box, card: plugin._draw_snapshot_mini_card(image, draw, box, card, theme),
           live, (10, 10, 184, 49), "live", 1)


def test_live_queue_section_passes_snapshot_settings_to_each_tile():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = Image.new("RGB", (420, 170), theme["bg"])
    draw = ImageDraw.Draw(image)
    seen = []
    plugin._draw_live_queue_tile = lambda _image, _draw, _box, card, _theme, **kwargs: seen.append((card["id"], kwargs))
    cards = [_queue_row_card(id=f"live-{index}") for index in range(6)]

    plugin._draw_live_queue_section(
        image, draw, (10, 10, 376, 129), "LIVE TOO", cards, theme, max_items=8,
        avatar_cache_seconds=900, show_snapshot=True, snapshot_cache_seconds=75,
    )

    assert [card_id for card_id, _kwargs in seen] == [f"live-{index}" for index in range(6)]
    assert all(
        kwargs == {"show_snapshot": True, "snapshot_cache_seconds": 75, "avatar_cache_seconds": 900}
        for _card_id, kwargs in seen
    )


def test_live_queue_tile_puts_the_streamer_avatar_before_the_name():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = _tile_image(theme)
    texts, avatars = [], []
    plugin._load_cover_source = lambda url, seconds: Image.new("RGB", (160, 90), (24, 180, 240))

    def draw_avatar(_image, _draw, avatar_box, card, _platform, _fill, _ink, _line, cache_seconds, show_fav_badge=True):
        avatars.append((avatar_box, card["id"], cache_seconds, show_fav_badge))

    plugin._draw_avatar = draw_avatar

    plugin._draw_live_queue_tile(
        image, _TextSpy(ImageDraw.Draw(image), texts), TILE_BOX, _queue_row_card(), theme, avatar_cache_seconds=600,
    )

    ((avatar_x, avatar_y, size), card_id, cache_seconds, badge), = avatars
    assert (card_id, cache_seconds, badge) == ("hasanabi", 600, False)
    assert size >= 16 and 10 < avatar_x <= 14
    assert avatar_y + size <= 10 + 52 and avatar_y + size > _strip_top() + 3
    (name_x, _name_y), _name, _fill, _font = next(item for item in texts if item[1] == "HasanAbi")
    assert name_x >= avatar_x + size + 2


def test_snapshot_mini_section_keeps_four_thumbnails_landscape():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (420, 170), theme["bg"])
    draw = ImageDraw.Draw(image)
    covers = {
        "https://covers.test/0.jpg": Image.new("RGB", (160, 90), (24, 180, 240)),
        "https://covers.test/1.jpg": Image.new("RGB", (160, 90), (220, 90, 50)),
        "https://covers.test/2.jpg": Image.new("RGB", (160, 90), (90, 160, 120)),
        "https://covers.test/3.jpg": Image.new("RGB", (160, 90), (180, 120, 220)),
    }
    plugin._load_cover_source = lambda url, cache_seconds: covers.get(url)
    plugin._load_avatar_source = lambda url, cache_seconds: None
    cards = [
        {
            "platform": "twitch",
            "id": f"live-cover-{index}",
            "owner": f"Live Cover {index}",
            "label": "",
            "title": "Live stream",
            "status": "live",
            "is_fav": False,
            "heat": 0,
            "start_time": None,
            "cover": f"https://covers.test/{index}.jpg",
            "avatar": "",
        }
        for index in range(4)
    ]

    visible = plugin._draw_snapshot_mini_section(image, draw, (10, 10, 376, 129), "LIVE TOO", cards, theme)

    assert visible == 4
    assert image.getpixel((82, 50)) == (24, 180, 240)
    assert image.getpixel((274, 50)) == (220, 90, 50)
    assert image.getpixel((82, 106)) == (90, 160, 120)
    assert image.getpixel((274, 106)) == (180, 120, 220)


def test_snapshot_mini_card_uses_platform_text_and_uptime_instead_of_live_dot():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (260, 120), theme["bg"])
    real_draw = ImageDraw.Draw(image)
    seen = {"texts": [], "ellipses": []}

    class DrawSpy:
        def __getattr__(self, name):
            return getattr(real_draw, name)

        def text(self, xy, text, fill=None, font=None, *args, **kwargs):
            seen["texts"].append((xy, text, fill))
            return real_draw.text(xy, text, fill=fill, font=font, *args, **kwargs)

        def ellipse(self, xy, fill=None, outline=None, *args, **kwargs):
            seen["ellipses"].append((xy, fill, outline))
            return real_draw.ellipse(xy, fill=fill, outline=outline, *args, **kwargs)

    draw = DrawSpy()
    cover = Image.new("RGB", (80, 50), (24, 180, 240))
    avatar = Image.new("RGB", (40, 40), (240, 80, 32))

    plugin._load_cover_source = lambda url, cache_seconds: cover if url == "https://covers.test/live.jpg" else None
    plugin._load_avatar_source = lambda url, cache_seconds: avatar if url == "https://avatars.test/live.png" else None

    def fail_platform_badge(*args, **kwargs):
        raise AssertionError("mini cards should use platform text, not platform badge icons")

    plugin._draw_platform_badge = fail_platform_badge
    now = 1_700_000_000.0
    card = {
        "platform": "twitch",
        "id": "live-cover",
        "owner": "Live Cover",
        "label": "",
        "title": "Live stream",
        "status": "live",
        "is_fav": False,
        "heat": 0,
        "start_time": (now - 3720) * 1000,
        "cover": "https://covers.test/live.jpg",
        "avatar": "https://avatars.test/live.png",
    }

    original_time = live_radar_module.time.time
    try:
        live_radar_module.time.time = lambda: now
        plugin._draw_snapshot_mini_card(image, draw, (10, 20, 220, 76), card, theme)
    finally:
        live_radar_module.time.time = original_time

    meta_pixels = set(image.crop((160, 58, 220, 80)).getdata())
    assert theme["live_muted"] in meta_pixels
    assert any(text == "TW" and fill == theme["live_muted"] for _xy, text, fill in seen["texts"])
    assert any(text == "1h 02m" and fill == LIVE_STATUS_DOT for _xy, text, fill in seen["texts"])
    assert not any(fill == LIVE_STATUS_DOT or outline == LIVE_STATUS_DOT for _xy, fill, outline in seen["ellipses"])


def test_dashboard_uses_snapshot_mini_when_no_extra_live():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    seen = {}

    def draw_snapshot_mini(_image, _draw, _box, _title, cards, _theme, max_items=4, snapshot_cache_seconds=90, avatar_cache_seconds=0, caption=None, live_count=None):
        seen["live_count"] = live_count
        seen["ids"] = [card["id"] for card in cards]
        seen["caption"] = caption
        return len(cards)

    def fail_live_queue(*_args, **_kwargs):
        raise AssertionError("live queue should not draw when there are no extra live cards")

    plugin._draw_snapshot_mini_section = draw_snapshot_mini
    plugin._draw_live_queue_section = fail_live_queue
    cards = [
        {
            "platform": "twitch",
            "id": "xqc",
            "owner": "xQc",
            "label": "",
            "title": "Live",
            "status": "live",
            "is_fav": False,
            "heat": 10,
            "start_time": None,
            "cover": "",
            "avatar": "",
        },
        {
            "platform": "douyu",
            "id": "recent-cover",
            "owner": "Recent Cover",
            "label": "",
            "title": "Recent",
            "status": "offline",
            "is_fav": False,
            "heat": 0,
            "start_time": None,
            "cover": "https://covers.test/recent.jpg",
            "avatar": "",
        },
    ]

    plugin._render_dashboard(cards, (800, 480), theme, datetime.now(timezone.utc), False, None)

    assert seen["ids"] == ["recent-cover"]
    assert seen["caption"] == "quiet slots"
    assert seen["live_count"] == sum(card["status"] == "live" for card in cards)


def test_dashboard_uses_the_cover_wall_for_any_extra_live_cards():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    seen = {}

    def fail_snapshot_mini(*_args, **_kwargs):
        raise AssertionError("snapshot mini only fills the quiet slots when nobody else is live")

    def draw_live_queue(_image, _draw, _box, title, cards, _theme, max_items, **_kwargs):
        seen["title"] = title
        seen["ids"] = [card["id"] for card in cards]
        return len(cards)

    plugin._draw_snapshot_mini_section = fail_snapshot_mini
    plugin._draw_live_queue_section = draw_live_queue

    plugin._render_dashboard(_live_cards(7), (800, 480), theme, datetime.now(timezone.utc), False, None)

    assert seen == {"title": "LIVE TOO", "ids": ["streamer3", "streamer4", "streamer5", "streamer6"]}


def test_dashboard_draws_the_cover_wall_for_many_live_cards():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    seen = {}

    def fail_snapshot_mini(*_args, **_kwargs):
        raise AssertionError("snapshot mini only fills the quiet slots when nobody else is live")

    def draw_live_queue(_image, _draw, _box, title, cards, _theme, max_items, avatar_cache_seconds=0, show_snapshot=False, snapshot_cache_seconds=0, live_total=None):
        seen["title"] = title
        seen["count"] = len(cards)
        seen["show_snapshot"] = show_snapshot
        seen["snapshot_cache_seconds"] = snapshot_cache_seconds
        return len(cards)

    plugin._draw_snapshot_mini_section = fail_snapshot_mini
    plugin._draw_live_queue_section = draw_live_queue
    cards = [
        {
            "platform": "twitch",
            "id": f"streamer{i}",
            "owner": f"Streamer {i}",
            "label": "",
            "title": "Live",
            "status": "live",
            "is_fav": False,
            "heat": 100 - i,
            "start_time": None,
            "cover": f"https://covers.test/{i}.jpg",
            "avatar": "",
        }
        for i in range(8)
    ]

    plugin._render_dashboard(
        cards, (800, 480), theme, datetime.now(timezone.utc), False, None,
        {"show_snapshots": True, "snapshot_cache_seconds": 75},
    )

    assert seen == {"title": "LIVE TOO", "count": 5, "show_snapshot": True, "snapshot_cache_seconds": 75}


def _live_cards(count, *, platform="twitch"):
    return [
        {
            "platform": platform,
            "id": f"streamer{index}",
            "owner": f"Streamer {index}",
            "label": "",
            "title": "Live",
            "status": "live",
            "is_fav": False,
            "heat": 100 - index,
            "start_time": None,
            "cover": "",
            "avatar": "",
        }
        for index in range(count)
    ]


def _offline_cards(count):
    platforms = ("bilibili", "douyu", "picarto", "twitch", "kick")
    return [
        {
            "platform": platforms[index % len(platforms)],
            "id": f"offline{index}",
            "owner": f"Offline {index}",
            "label": "",
            "title": f"Sign {index}",
            "status": "offline",
            "is_fav": index == 0,
            "heat": 0,
            "start_time": None,
            "cover": "",
            "avatar": "",
        }
        for index in range(count)
    ]


def _bottom_boxes(plugin, cards, dimensions):
    seen = {}

    def live_queue(_image, _draw, box, _title, _cards, _theme, max_items, **_kwargs):
        seen["live"] = (box, max_items)
        return 0

    def snapshot_mini(_image, _draw, box, *_args, **_kwargs):
        seen["live"] = (box, None)
        return 0

    plugin._draw_live_queue_section = live_queue
    plugin._draw_snapshot_mini_section = snapshot_mini
    plugin._draw_compact_section = lambda _image, _draw, box, *_args, **_kwargs: seen.__setitem__("offline", ("cards", box))
    plugin._draw_offline_list_section = lambda _image, _draw, box, *_args, **_kwargs: seen.__setitem__("offline", ("list", box))
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    plugin._render_dashboard(cards, dimensions, theme, datetime.now(timezone.utc), False, None, {"show_snapshots": True})
    return seen


def test_dense_live_dashboard_widens_live_too_and_slims_offline():
    plugin = _plugin()

    seen = _bottom_boxes(plugin, _live_cards(15) + _offline_cards(5), (800, 480))

    (live_x, _live_y, live_w, _live_h), max_items = seen["live"]
    kind, (off_x, _off_y, off_w, _off_h) = seen["offline"]
    assert kind == "list"
    assert max_items == 12
    assert LiveRadar._live_queue_layout(seen["live"][0], 12, max_items)["columns"] == 6
    assert off_w <= 200 and live_w >= 3 * off_w - 40
    assert live_x + live_w < off_x and off_x + off_w <= 800 - 14


def test_three_extra_live_get_one_row_of_large_tiles():
    plugin = _plugin()

    seen = _bottom_boxes(plugin, _live_cards(6) + _offline_cards(5), (800, 480))

    (live_x, _y, live_w, _h), _max_items = seen["live"]
    kind, (off_x, _off_y, off_w, _off_h) = seen["offline"]
    layout = LiveRadar._live_queue_layout(seen["live"][0], 3, 12)
    assert (layout["columns"], layout["rows"]) == (3, 1)
    assert live_w == 3 * live_radar_module.LIVE_QUEUE_LARGE_TILE_W
    assert kind == "list" and off_x == live_x + live_w + 14 and off_x + off_w == 800 - 16


def test_few_extra_live_spread_in_one_row_and_squeeze_offline():
    row_w = 800 - 2 * 16 - 14
    for extra in (4, 5, 6):
        plugin = _plugin()

        seen = _bottom_boxes(plugin, _live_cards(3 + extra) + _offline_cards(5), (800, 480))

        (live_x, _y, live_w, _h), max_items = seen["live"]
        kind, (off_x, _off_y, off_w, _off_h) = seen["offline"]
        layout = LiveRadar._live_queue_layout(seen["live"][0], extra, max_items)
        assert (layout["columns"], layout["rows"], layout["visible_count"]) == (extra, 1, extra)
        assert live_w == row_w - live_radar_module.LIVE_WALL_OFFLINE_MIN_W
        assert live_w / extra >= live_radar_module.LIVE_QUEUE_ROW_TILE_MIN_W
        assert kind == "list" and off_w == live_radar_module.LIVE_WALL_OFFLINE_MIN_W
        assert off_x == live_x + live_w + 14


def test_four_extra_live_show_larger_snapshots_than_a_two_by_two_wall():
    plugin = _plugin()

    seen = _bottom_boxes(plugin, _live_cards(7) + _offline_cards(5), (800, 480))

    box = seen["live"][0]
    layout = LiveRadar._live_queue_layout(box, 4, 12)
    cover_w = box[2] / layout["columns"]
    cover_h = layout["tile_h"] - live_radar_module.LIVE_QUEUE_NAME_STRIP_H
    two_by_two = LiveRadar._live_queue_layout((16, box[1], 2 * live_radar_module.LIVE_QUEUE_TILE_W, box[3]), 4, 12)
    small_h = two_by_two["tile_h"] - live_radar_module.LIVE_QUEUE_NAME_STRIP_H
    assert cover_w >= 140 and cover_h >= 78
    assert cover_w * cover_h > 3 * live_radar_module.LIVE_QUEUE_TILE_W * small_h


def test_seven_extra_live_fall_back_to_two_rows():
    plugin = _plugin()

    seen = _bottom_boxes(plugin, _live_cards(10) + _offline_cards(5), (800, 480))

    (_x, _y, live_w, _h), max_items = seen["live"]
    layout = LiveRadar._live_queue_layout(seen["live"][0], 7, max_items)
    assert (layout["columns"], layout["rows"], layout["visible_count"]) == (4, 2, 7)
    assert live_w == 4 * live_radar_module.LIVE_QUEUE_TILE_W


def test_two_extra_live_leave_room_for_full_offline_cards():
    plugin = _plugin()

    seen = _bottom_boxes(plugin, _live_cards(5) + _offline_cards(5), (800, 480))

    (_x, _y, live_w, _h), _max_items = seen["live"]
    kind, (_off_x, _off_y, off_w, _off_h) = seen["offline"]
    assert live_w == 2 * live_radar_module.LIVE_QUEUE_LARGE_TILE_W
    assert kind == "cards" and off_w >= live_radar_module.OFFLINE_CARDS_MIN_W


def test_live_queue_layout_uses_one_row_of_large_tiles_for_up_to_three():
    for count in (1, 2, 3):
        width = count * live_radar_module.LIVE_QUEUE_LARGE_TILE_W
        layout = LiveRadar._live_queue_layout((16, 317, width, 128), count, 12)
        assert (layout["columns"], layout["rows"], layout["visible_count"]) == (count, 1, count)


def test_vertical_dense_dashboard_keeps_two_equal_halves():
    plugin = _plugin()

    seen = _bottom_boxes(plugin, _live_cards(15) + _offline_cards(5), (480, 800))

    assert seen["offline"][0] == "cards"
    assert seen["live"][0][2] == seen["offline"][1][2]


def test_live_queue_layout_fits_six_columns_in_the_wide_wall():
    layout = LiveRadar._live_queue_layout((16, 317, 562, 128), 14, 12)

    assert (layout["columns"], layout["rows"], layout["visible_count"]) == (6, 2, 12)


def test_offline_list_section_is_one_frame_with_platform_edged_rows():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = Image.new("RGB", (220, 150), theme["bg"])
    texts, badges, avatars = [], [], []

    class DrawSpy:
        def __init__(self, draw):
            self._draw = draw

        def __getattr__(self, name):
            return getattr(self._draw, name)

        def text(self, xy, text, *args, **kwargs):
            texts.append(text)
            return self._draw.text(xy, text, *args, **kwargs)

    plugin._draw_platform_badge = lambda _image, _draw, box, platform_key, **_kwargs: badges.append(platform_key)
    plugin._draw_avatar = lambda _image, _draw, avatar_box, card, *_args, **_kwargs: avatars.append(card["id"])
    cards = _offline_cards(5)

    visible = plugin._draw_offline_list_section(
        image, DrawSpy(ImageDraw.Draw(image)), (10, 10, 192, 128), "OFFLINE", cards, theme, max_items=3,
    )

    assert visible == 3
    assert avatars == ["offline0", "offline1", "offline2"]
    assert badges == ["bilibili", "douyu", "picarto"]
    assert {"Offline 0", "Offline 1", "Offline 2", "+2"} <= set(texts)
    top, slot = 34, 104 / 3
    for index, platform in enumerate(("bilibili", "douyu", "picarto")):
        middle = round(top + slot * index + slot / 2)
        expected = live_radar_module.PLATFORM_BRAND_COLORS.get(platform, theme["line"])
        assert image.getpixel((12, middle)) == expected
    assert image.getpixel((10, 34)) == theme["bg"]
    assert image.getpixel((10, 52)) == image.getpixel((100, 34)) == theme["line"]
    assert image.getpixel((100, round(top + slot))) == theme["line"]


def test_top_overflow_excludes_live_queue_rows():
    cards = [{"id": str(index)} for index in range(12)]

    overflow = LiveRadar._top_live_overflow_cards(cards, top_count=3, queue_count=8)

    assert [card["id"] for card in overflow] == ["11"]


def test_light_title_logo_treats_black_source_background_as_transparent(monkeypatch):
    plugin = _plugin()
    source = Image.new("RGBA", (28, 28), (0, 0, 0, 255))
    source_draw = ImageDraw.Draw(source)
    source_draw.ellipse((9, 9, 19, 19), fill=(255, 255, 255, 255))
    monkeypatch.setattr(LiveRadar, "_load_title_logo", staticmethod(lambda: source))

    image = Image.new("RGB", (64, 64), (255, 255, 255))
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig(mode="day"))

    assert plugin._paste_title_logo(image, 10, 10, 32, theme)
    assert image.getpixel((16, 16)) == (255, 255, 255)
    assert image.getpixel((26, 26)) != (255, 255, 255)


def test_generate_image_uses_stale_cache_on_fetch_failure():
    plugin = _plugin()
    _memory_cache(plugin)
    settings = {"roomsText": "twitch|xqc|xQc", "cacheSeconds": "20", "themeMode": "light"}

    plugin._fetch_statuses = lambda rooms, api_url, timeout, fetch_avatars: [
        {
            "ok": True,
            "platform": "twitch",
            "id": "xqc",
            "status": {"isLive": True, "owner": "xQc", "title": "Live once", "heatValue": 100},
        }
    ]
    first = plugin.generate_image(settings, FakeDeviceConfig())
    assert first.size == (800, 480)

    plugin._fetch_statuses = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
    stale = plugin.generate_image({**settings, "forceRefresh": "true"}, FakeDeviceConfig())

    assert stale.size == (800, 480)


def test_live_radar_base_font_uses_shared_resolver(monkeypatch):
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        live_radar_module,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or sentinel,
        raising=False,
    )

    assert LiveRadar._font(17, "bold") is sentinel
    assert calls == [(17, True)]


def test_live_radar_preserves_shared_bold_fallback_raster(monkeypatch):
    shared = live_radar_module.get_base_ui_font(48, bold=True)
    expected = bytes(shared.getmask("Readable UI"))
    monkeypatch.setattr(
        live_radar_module,
        "get_base_ui_font",
        lambda size, bold=False: shared,
    )

    font = LiveRadar._font(48, "bold")

    assert font is shared
    assert bytes(font.getmask("Readable UI")) == expected


def test_kick_uses_its_official_favicon_asset():
    icon = LiveRadar._load_platform_icon_asset("kick")

    assert icon is not None and icon.size == (64, 64)
    colours = {colour for _count, colour in icon.convert("RGB").getcolors(4096)}
    assert live_radar_module.PLATFORM_BRAND_COLORS["kick"] in colours


def test_kick_quin_sorts_right_behind_mr_quin_among_favourites():
    cards = [
        {"platform": platform, "id": room_id, "status": "live", "is_fav": True, "heat": heat, "owner": room_id,
         "label": "", "favorite_rank": LiveRadar._favorite_priority(platform, room_id)}
        for platform, room_id, heat in (
            ("douyu", "60937", 900),
            ("kick", "quin3614", 10),
            ("bilibili", "545318", 5),
        )
    ]

    ordered = LiveRadar._sort_cards(cards)

    assert [(card["platform"], card["id"]) for card in ordered] == [
        ("bilibili", "545318"),
        ("kick", "quin3614"),
        ("douyu", "60937"),
    ]


KICK_SRCSET = (
    "https://images.kick.com/video_thumbnails/a/b/1080.webp?v=1 1920w, "
    "https://images.kick.com/video_thumbnails/a/b/720.webp?v=2 1280w, "
    "https://images.kick.com/video_thumbnails/a/b/360.webp?v=3 480w, "
    "https://images.kick.com/video_thumbnails/a/b/480.webp?v=4 640w"
)
STALE_KICK_COVER = "https://stream.kick.com/thumbnails/livestream/1/thumb0/video_thumbnail/thumb0.jpg"


class _KickApiSession:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def get(self, url, timeout, headers):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        payload = self.payload

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return payload

        return Response()


def _kick_results():
    return [
        {"ok": True, "platform": "kick", "id": "quin3614", "status": {"isLive": True, "cover": STALE_KICK_COVER}},
        {"ok": True, "platform": "kick", "id": "xqc", "status": {"isLive": False, "cover": "https://files.kick.com/u.webp"}},
    ]


def test_live_kick_rooms_take_their_snapshot_from_kicks_livestream_api():
    plugin = _plugin()
    session = _KickApiSession({"data": {"thumbnail": {"src": "https://images.kick.com/x/720.webp", "srcset": KICK_SRCSET}}})
    results = _kick_results()

    updated = plugin._attach_kick_thumbnails(session, results, timeout=9)

    assert session.calls == ["https://kick.com/api/v2/channels/quin3614/livestream"]
    assert updated[0]["status"]["cover"] == "https://images.kick.com/video_thumbnails/a/b/480.webp?v=4"
    assert updated[1] == results[1]
    assert results[0]["status"]["cover"] == STALE_KICK_COVER


def test_kick_thumbnail_lookup_failure_keeps_the_aggregator_cover():
    plugin = _plugin()
    results = _kick_results()

    for session in (_KickApiSession(error=RuntimeError("blocked")), _KickApiSession({"data": None})):
        assert plugin._attach_kick_thumbnails(session, results, timeout=9) == results


def test_kick_rooms_use_the_livestream_thumbnail_during_a_full_fetch(monkeypatch):
    plugin = _plugin()
    session = _KickApiSession({"data": {"thumbnail": {"src": "https://images.kick.com/x/720.webp", "srcset": ""}}})
    monkeypatch.setattr(live_radar_module, "get_http_session", lambda: session)
    plugin._fetch_aggregator_platform = lambda _session, rooms, *_args: [dict(result) for result in _kick_results()[:1]]

    results = plugin._fetch_statuses([{"platform": "kick", "id": "quin3614", "isFav": True}], "https://aggregator.test", 9, True)

    assert results[0]["status"]["cover"] == "https://images.kick.com/x/720.webp"
    assert LiveRadar._cover_headers("https://images.kick.com/x/720.webp")["Referer"] == "https://kick.com/"


def test_status_tile_without_anyone_live_has_no_live_dot():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = Image.new("RGB", (220, 80), theme["bg"])
    texts = []

    class DrawSpy:
        def __init__(self, draw):
            self._draw = draw

        def __getattr__(self, name):
            return getattr(self._draw, name)

        def text(self, xy, text, *args, **kwargs):
            texts.append(text)
            return self._draw.text(xy, text, *args, **kwargs)

    plugin._draw_live_status_tile(image, DrawSpy(ImageDraw.Draw(image)), (10, 10, 188, 53), theme, 0)

    assert texts == ["\u6682\u65e0\u76f4\u64ad"]
    assert (0, 255, 0) not in set(image.getdata())


def _cover_aspects(plugin, live_count, dimensions, orientation="horizontal"):
    seen = []
    plugin._load_cover_source = lambda url, seconds: Image.new("RGB", (160, 90), (24, 180, 240))
    plugin._load_avatar_source = lambda url, seconds=0: None
    plugin._draw_live_queue_tile = lambda _image, _draw, box, card, *_args, **_kwargs: seen.append(box)
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig(resolution=dimensions, orientation=orientation))
    cards = [dict(card, cover=f"https://covers.test/{card['id']}.jpg") for card in _live_cards(live_count)]
    plugin._render_dashboard(cards, dimensions, theme, datetime.now(timezone.utc), False, None, {"show_snapshots": True})
    strip = live_radar_module.LIVE_QUEUE_NAME_STRIP_H
    return [w / (h - strip) for _x, _y, w, h in seen]


@pytest.mark.parametrize("live_count", [4, 5, 6, 7, 9, 15])
@pytest.mark.parametrize("dimensions", [(480, 800), (1600, 1200), (800, 480)])
def test_wall_tiles_never_stretch_taller_than_a_wide_cover(live_count, dimensions):
    aspects = _cover_aspects(_plugin(), live_count, dimensions)

    assert aspects and min(aspects) >= 1.6


def test_one_row_landscape_wall_shows_as_many_streams_as_fit():
    seen = {}
    plugin = _plugin()

    def live_queue(_image, _draw, box, _title, cards, _theme, max_items, **_kwargs):
        seen["layout"] = LiveRadar._live_queue_layout(box, len(cards), max_items)
        return seen["layout"]["visible_count"]

    plugin._draw_live_queue_section = live_queue
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())

    for extra in (3, 4, 5):
        plugin._render_dashboard(_live_cards(3 + extra), (600, 448), theme, datetime.now(timezone.utc), False, None,
                                 {"show_snapshots": True})
        assert seen["layout"]["rows"] == 1
        assert seen["layout"]["visible_count"] >= min(extra, 3)


def test_status_tile_spanning_two_cells_has_no_divider_through_it():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "light"}, FakeDeviceConfig())
    image = Image.new("RGB", (330, 220), theme["bg"])
    status = []
    plugin._draw_live_queue_tile = lambda *_args, **_kwargs: None
    original = plugin._draw_live_status_tile

    def record(_image, _draw, box, _theme, live_count, **kwargs):
        status.append(box)
        return original(_image, _draw, box, _theme, live_count, **kwargs)

    plugin._draw_live_status_tile = record
    cards = [{"platform": "twitch", "id": f"live-{index}", "status": "live"} for index in range(7)]

    plugin._draw_live_queue_section(
        image, ImageDraw.Draw(image), (10, 10, 300, 190), "LIVE TOO", cards, theme, max_items=12, live_total=10,
    )

    # Seven streams in three columns leave two free cells on the last row: one tile, no divider inside it.
    (left, top, width, height), = status
    assert left == 110 and left + width == 310
    middle_divider = 210
    # The centred caption may cross the old divider column, so sample the tile's free top and bottom edges.
    edges = [*range(top + 1, top + 4), *range(top + height - 4, top + height - 1)]
    assert all(image.getpixel((middle_divider, y)) == theme["panel"] for y in edges)
