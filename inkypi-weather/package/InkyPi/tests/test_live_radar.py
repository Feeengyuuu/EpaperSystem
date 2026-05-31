import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw

from plugins.live_radar.live_radar import DEFAULT_ROOMS_TEXT, LiveRadar


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


def test_yellow_border_favorites_are_pinned_with_zard_first():
    plugin = _plugin()

    rooms = plugin._parse_rooms(
        {
            "roomsText": "\n".join(
                [
                    "douyu|3507497",
                    "douyu|60937",
                    "twitch|xqc",
                    "douyu|12306",
                ]
            )
        }
    )

    assert [room["isFav"] for room in rooms] == [True, True, False, True]

    cards = [
        {"platform": room["platform"], "id": room["id"], "status": "offline", "is_fav": room["isFav"], "favorite_rank": plugin._favorite_priority(room["platform"], room["id"]), "heat": 9999, "owner": room["id"]}
        for room in rooms
    ]
    sorted_cards = plugin._sort_cards(cards)

    assert sorted_cards[0]["id"] == "60937"
    assert [card["id"] for card in sorted_cards[:3]] == ["60937", "12306", "3507497"]


def test_default_rooms_match_latest_backup_and_favorite_order():
    plugin = _plugin()

    rooms = plugin._parse_rooms({"roomsText": DEFAULT_ROOMS_TEXT})

    assert len(rooms) == 64
    assert (rooms[0]["platform"], rooms[0]["id"]) == ("douyu", "6979222")
    assert (rooms[-1]["platform"], rooms[-1]["id"]) == ("twitch", "jie_220")

    favorite_keys = [(room["platform"], room["id"]) for room in rooms if room["isFav"]]
    assert favorite_keys == [
        ("douyu", "6979222"),
        ("bilibili", "545318"),
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

    assert plugin._sort_cards(favorite_cards)[0]["id"] == "60937"


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
        (255, 255, 255),
        (0, 0, 0),
        (0, 0, 0),
        (0, 0, 0),
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
