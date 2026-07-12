import hashlib
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

    assert len(rooms) == 65
    assert (rooms[0]["platform"], rooms[0]["id"]) == ("bilibili", "545318")
    assert (rooms[-1]["platform"], rooms[-1]["id"]) == ("twitch", "ludwig")
    assert ("bilibili", "173551") in [(room["platform"], room["id"]) for room in rooms]
    assert ("twitch", "ludwig") in [(room["platform"], room["id"]) for room in rooms]
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


def test_fetch_statuses_repairs_bilibili_batch_failures_with_direct_api(monkeypatch):
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
            return FakeResponse(
                {
                    "ok": True,
                    "results": [
                        {
                            "ok": False,
                            "platform": "bilibili",
                            "id": "545318",
                            "status": None,
                            "error": "bilibili_batch_fetch_failed",
                        },
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


def test_theme_uses_injected_palette_over_conflicting_legacy_alias():
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
    assert theme["bg"] == context["palette"]["background"]
    assert theme["panel"] == context["palette"]["panel"]
    assert theme["ink"] == context["palette"]["ink"]
    assert theme["muted"] == context["palette"]["muted"]
    assert theme["line"] == context["palette"]["rule"]


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
    assert day_image.getpixel((0, 0)) == day["palette"]["background"]
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
    pixels = set(image.get_flattened_data())
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


def test_platform_badges_render_known_icons():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (150, 42), theme["bg"])
    draw = ImageDraw.Draw(image)

    for index, platform in enumerate(("bilibili", "douyu", "twitch")):
        x = 8 + index * 46
        plugin._draw_platform_badge(
            draw,
            (x, 8, x + 34, 31),
            platform,
            fill=theme["ink"],
            ink=theme["bg"],
            outline=theme["ink"],
        )

    pixels = set(image.crop((0, 0, 150, 42)).getdata())
    assert theme["bg"] in pixels
    assert theme["ink"] in pixels


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


def test_compact_internal_placeholder_asset_is_exact_size_and_transparent():
    path = Path(live_radar_module.PLUGIN_DIR) / live_radar_module.COMPACT_PLACEHOLDER_FILE
    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")

    assert image.size == live_radar_module.COMPACT_PLACEHOLDER_SIZE
    assert alpha.getextrema()[0] == 0
    assert alpha.getextrema()[1] > 0
    assert len(set(image.getdata())) > 8


def test_compact_card_draws_internal_placeholder_at_native_size():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (430, 80), theme["bg"])
    draw = ImageDraw.Draw(image)
    seen = {}
    plugin._draw_compact_placeholder_asset = lambda _image, _draw, box, _theme: seen.__setitem__("box", box)
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


def test_generated_slot_placeholder_asset_is_exact_size_and_nonblank():
    path = Path(live_radar_module.PLUGIN_DIR) / live_radar_module.SLOT_PLACEHOLDER_FILE
    image = Image.open(path).convert("RGB")

    assert image.size == (184, 49)
    assert len(set(image.getdata())) > 16


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
    plugin._draw_snapshot_mini_placeholder = lambda _image, _draw, box, _theme: seen.__setitem__("box", box)
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


def test_live_queue_section_draws_placeholder_for_empty_second_column_slot():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    image = Image.new("RGB", (420, 170), theme["bg"])
    draw = ImageDraw.Draw(image)
    seen = {}
    plugin._draw_live_mini_row = lambda *_args, **_kwargs: None
    plugin._draw_snapshot_mini_placeholder = lambda _image, _draw, box, _theme: seen.__setitem__("box", box)
    cards = [{"platform": "twitch", "id": f"live-{index}", "status": "live"} for index in range(5)]

    visible = plugin._draw_live_queue_section(image, draw, (10, 10, 376, 129), "LIVE TOO", cards, theme, max_items=8)

    assert visible == 5
    assert seen["box"] == (202, 106, 184, 32)


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

    def draw_snapshot_mini(_image, _draw, _box, _title, cards, _theme, max_items=4, snapshot_cache_seconds=90, avatar_cache_seconds=0, caption=None):
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


def test_dashboard_uses_snapshot_mini_for_seven_or_fewer_live_cards():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    seen = {}

    def draw_snapshot_mini(_image, _draw, _box, title, cards, _theme, max_items=4, snapshot_cache_seconds=90, avatar_cache_seconds=0, caption=None):
        seen["title"] = title
        seen["ids"] = [card["id"] for card in cards]
        seen["caption"] = caption
        return len(cards)

    def fail_live_queue(*_args, **_kwargs):
        raise AssertionError("dense live queue should not draw for 7 or fewer live cards")

    plugin._draw_snapshot_mini_section = draw_snapshot_mini
    plugin._draw_live_queue_section = fail_live_queue
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
        for i in range(7)
    ]

    plugin._render_dashboard(cards, (800, 480), theme, datetime.now(timezone.utc), False, None)

    assert seen["title"] == "LIVE TOO"
    assert seen["ids"] == ["streamer3", "streamer4", "streamer5", "streamer6"]
    assert seen["caption"] is None


def test_dashboard_keeps_dense_live_queue_for_more_than_seven_live_cards():
    plugin = _plugin()
    theme = plugin._theme({"themeMode": "dark"}, FakeDeviceConfig())
    seen = {}

    def fail_snapshot_mini(*_args, **_kwargs):
        raise AssertionError("snapshot mini should only replace live queue at 7 or fewer live cards")

    def draw_live_queue(_image, _draw, _box, title, cards, _theme, max_items, avatar_cache_seconds=0):
        seen["title"] = title
        seen["count"] = len(cards)
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

    plugin._render_dashboard(cards, (800, 480), theme, datetime.now(timezone.utc), False, None)

    assert seen == {"title": "LIVE TOO", "count": 5}


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
