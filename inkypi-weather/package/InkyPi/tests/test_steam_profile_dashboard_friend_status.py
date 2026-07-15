import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.steam_profile_dashboard import steam_profile_dashboard as steam_profile_module
from plugins.steam_profile_dashboard.steam_profile_dashboard import (
    STEAM_SECTION_WORDMARK_IMAGES,
    STEAM_SECTION_WORDMARK_SIZES,
    STEAM_SECTION_WORDMARK_Y_OFFSET,
    SteamProfileDashboard,
)
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
)


class _SteamDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, _key, default=None):
        return default

    def load_env_key(self, key):
        assert key == "STEAM_API_KEY"
        return "test-key"


class _CommunityPresenceResponse:
    def __init__(self, text, *, error=None):
        self.text = text
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error


def test_offline_web_api_state_is_corrected_by_live_community_presence(monkeypatch):
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    requests = []

    class Session:
        def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return _CommunityPresenceResponse(
                "<profile><steamID>Player</steamID><onlineState>online</onlineState></profile>"
            )

    monkeypatch.setattr(steam_profile_module, "get_http_session", lambda: Session())

    profile = plugin._reconcile_community_presence(
        {"steamid": "76561198176386838", "personastate": 0},
        "76561198176386838",
    )

    assert profile["personastate"] == 1
    assert profile["_inkypi_presence_source"] == "steam_community_xml"
    assert requests == [(
        "https://steamcommunity.com/profiles/76561198176386838/",
        {"params": {"xml": 1}, "timeout": 12},
    )]


def test_non_offline_web_api_state_does_not_request_community_presence(monkeypatch):
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    monkeypatch.setattr(
        steam_profile_module,
        "get_http_session",
        lambda: (_ for _ in ()).throw(AssertionError("community lookup should not run")),
    )

    profile = plugin._reconcile_community_presence(
        {"steamid": "76561198176386838", "personastate": 1},
        "76561198176386838",
    )

    assert profile["personastate"] == 1
    assert "_inkypi_presence_source" not in profile


def test_failed_community_presence_check_preserves_offline_web_api_state(monkeypatch):
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})

    class Session:
        def get(self, *_args, **_kwargs):
            return _CommunityPresenceResponse("", error=RuntimeError("rate limited"))

    monkeypatch.setattr(steam_profile_module, "get_http_session", lambda: Session())

    profile = plugin._reconcile_community_presence(
        {"steamid": "76561198176386838", "personastate": 0},
        "76561198176386838",
    )

    assert profile["personastate"] == 0
    assert "_inkypi_presence_source" not in profile


def test_friend_game_status_keeps_long_title_inside_row_bounds():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (240, 80), "white")
    baseline = image.copy()
    draw = ImageDraw.Draw(image)
    font = plugin._font(13)

    plugin._display_game_name = (
        lambda data, appid=None, fallback=None:
        "Super Extremely Long Game Name That Should Wrap Inside The Friend Panel"
    )
    plugin._game_square_icon = lambda data, appid, size: Image.new("RGBA", (size, size), (0, 0, 0, 255))

    x, y = 24, 12
    max_width = 130
    row_height = 34
    next_y, fits = plugin._draw_friend_game_status(
        image,
        draw,
        {"gameid": "123", "gameextrainfo": "Fallback"},
        (x, y),
        font,
        (0, 0, 0),
        max_width,
        {},
        row_height=row_height,
    )

    diff = ImageChops.difference(image, baseline)
    assert fits is True
    assert next_y <= y + row_height
    assert diff.crop((x, y, x + max_width, y + row_height)).getbbox() is not None
    assert diff.crop((x + max_width, 0, image.width, image.height)).getbbox() is None


def test_force_refresh_aliases_bypass_fresh_steam_dashboard_cache(
    monkeypatch,
    tmp_path,
):
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    now = steam_profile_module.time.time()
    cached_image = tmp_path / "cached.png"
    Image.new("RGB", (800, 480), "black").save(cached_image)
    cache_entry = {
        "status_updated_at": now,
        "full_updated_at": now,
        "image_path": str(cached_image),
        "data": {"profile": {"personaname": "Cached"}},
    }
    fetch_calls = []

    def fetch_dashboard(*_args, **_kwargs):
        fetch_calls.append(True)
        return {"profile": {"personaname": "Live"}, "api_calls": 1}

    monkeypatch.setattr(steam_profile_module, "get_theme_context", lambda _config: {"mode": "day"})
    monkeypatch.setattr(plugin, "_read_cache", lambda _key: cache_entry)
    monkeypatch.setattr(plugin, "_fetch_dashboard_data", fetch_dashboard)
    monkeypatch.setattr(
        plugin,
        "_render_dashboard",
        lambda _data, dimensions, _theme: Image.new("RGB", dimensions, "white"),
    )
    monkeypatch.setattr(plugin, "_cache_image_path", lambda _key: str(tmp_path / "live.png"))
    monkeypatch.setattr(plugin, "_write_cache", lambda *_a, **_k: None)
    context_calls = []
    monkeypatch.setattr(
        plugin,
        "_write_steam_profile_context",
        lambda *_a, **_k: context_calls.append(True),
    )

    for force_key in ("forceRefresh", "force_refresh"):
        image = plugin.generate_image(
            {force_key: "true"},
            _SteamDeviceConfig(),
        )
        assert read_source_provenance(image) is SourceProvenance.LIVE

    assert len(fetch_calls) == 2
    assert len(context_calls) == 2

    monkeypatch.setattr(
        plugin,
        "_fetch_dashboard_data",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    stale = plugin.generate_image(
        {"forceRefresh": True},
        _SteamDeviceConfig(),
    )
    assert read_source_provenance(stale) is SourceProvenance.STALE_CACHE
    assert stale.info["inkypi_skip_cache"] is True
    assert len(context_calls) == 2


def test_online_friend_activity_preserves_friend_name_above_game_title():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (260, 90), "white")
    baseline = image.copy()
    draw = ImageDraw.Draw(image)
    font = plugin._font(13)
    line_height = plugin._line_height(draw, font)

    plugin._avatar_image = lambda url, size: Image.new("RGBA", (size, size), (255, 255, 255, 0))
    plugin._display_game_name = (
        lambda data, appid=None, fallback=None:
        "Super Extremely Long Game Name That Should Not Replace Friend Name"
    )
    plugin._game_square_icon = lambda data, appid, size: Image.new("RGBA", (size, size), (0, 0, 0, 255))

    x, y = 12, 12
    width = 210
    row_height = 34
    size = 32
    text_x = x + size + 10
    text_group_h = line_height * 2 + 1
    text_y = y + max(0, (row_height - text_group_h) // 2)

    plugin._draw_online_friend_activity(
        image,
        draw,
        [{
            "steamid": "1",
            "personaname": "Friend Visible",
            "personastate": 1,
            "gameid": "123",
            "gameextrainfo": "Fallback",
        }],
        x,
        y,
        width,
        row_height,
        size,
        6,
        {"tiny": font},
        {},
        (0, 0, 0),
    )

    diff = ImageChops.difference(image, baseline)
    assert diff.crop((text_x, text_y, x + width, text_y + line_height)).getbbox() is not None
    assert diff.crop((x + width, 0, image.width, image.height)).getbbox() is None


def test_friend_game_status_includes_playing_prefix():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (220, 60), "white")
    draw = ImageDraw.Draw(image)
    font = plugin._font(13)
    captured_text = []

    plugin._display_game_name = lambda data, appid=None, fallback=None: "Farthest Frontier"
    plugin._game_square_icon = lambda data, appid, size: Image.new("RGBA", (size, size), (255, 0, 0, 255))

    original_text = plugin._text

    def capture_text(draw, position, text, font, fill):
        captured_text.append((text, position))
        original_text(draw, position, text, font, fill)

    plugin._text = capture_text
    x, y = 20, 12
    plugin._draw_friend_game_status(
        image,
        draw,
        {"gameid": "123", "gameextrainfo": "Fallback"},
        (x, y),
        font,
        (0, 0, 0),
        150,
        {},
        row_height=20,
    )

    prefix_position = next(position for text, position in captured_text if text == "\u6b63\u5728\u6e38\u73a9\uff1a")
    game_position = next(position for text, position in captured_text if text == "Farthest Frontier")
    icon_pixels = [
        (px, py)
        for px in range(image.width)
        for py in range(image.height)
        if image.getpixel((px, py)) == (255, 0, 0)
    ]
    icon_left = min(px for px, _ in icon_pixels)
    icon_right = max(px for px, _ in icon_pixels)

    assert prefix_position[0] == x
    assert prefix_position[0] < icon_left
    assert icon_right < game_position[0]
    assert game_position[0] - icon_right <= 4

def test_current_game_line_highlights_playing_prefix():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (320, 60), "white")
    draw = ImageDraw.Draw(image)
    font = plugin._font(14, bold=True)
    normal = (0, 0, 0)
    accent = (0, 180, 90)
    captured = []
    original_text = plugin._text

    def capture_text(draw, position, text, font, fill):
        captured.append((text, fill))
        original_text(draw, position, text, font, fill)

    plugin._text = capture_text
    plugin._game_square_icon = lambda _data, _appid, size: Image.new("RGBA", (size, size), (255, 0, 0, 255))

    next_y, fits = plugin._draw_current_game_line(
        image,
        draw,
        (12, 12),
        "Farthest Frontier",
        "123",
        font,
        normal,
        280,
        {},
        label_fill=accent,
    )

    assert fits is True
    assert next_y > 12
    assert ("\u6b63\u5728\u73a9\uff1a", accent) in captured
    assert ("Farthest Frontier", normal) in captured


def test_game_strip_asset_is_exact_dashboard_slot_size():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    strip = plugin._game_strip_image((740, 38))

    assert strip.size == (740, 38)
    assert strip.getbbox() is not None
    assert len(strip.getcolors(maxcolors=740 * 38)) > 20


def test_game_strip_preserves_aspect_ratio_when_short():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    source = plugin._game_strip_image((740, 38))
    distorted = source.resize((740, 19), Image.Resampling.LANCZOS)

    fitted = plugin._game_strip_image((740, 19))

    assert fitted.size == (740, 19)
    assert fitted.tobytes() != distorted.tobytes()


def test_game_strip_draws_into_gap_area():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (800, 480), "black")
    baseline = image.copy()

    plugin._draw_game_strip(image, 34, 210, 740, 38)

    diff = ImageChops.difference(image, baseline)
    assert diff.crop((34, 210, 774, 248)).getbbox() is not None
    assert diff.crop((0, 0, 800, 200)).getbbox() is None


def test_section_wordmark_assets_are_transparent_and_sized():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})

    for key, image_name in STEAM_SECTION_WORDMARK_IMAGES.items():
        path = Path(plugin.get_plugin_dir(image_name))
        assert path.exists()
        with Image.open(path) as source:
            asset = source.convert("RGBA")
        assert asset.size == STEAM_SECTION_WORDMARK_SIZES[key]
        assert asset.getchannel("A").getbbox() is not None
        corners = [
            asset.getpixel((0, 0)),
            asset.getpixel((asset.width - 1, 0)),
            asset.getpixel((0, asset.height - 1)),
            asset.getpixel((asset.width - 1, asset.height - 1)),
        ]
        assert all(pixel[3] == 0 for pixel in corners)


def test_section_wordmark_images_are_recolored_for_dark_panel_readability():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})

    for key in STEAM_SECTION_WORDMARK_IMAGES:
        wordmark = plugin._section_wordmark_image(key)
        assert wordmark is not None
        assert wordmark.size == STEAM_SECTION_WORDMARK_SIZES[key]

        alpha = wordmark.getchannel("A")
        dark_panel = Image.new("RGBA", wordmark.size, (0, 0, 0, 255))
        dark_panel.alpha_composite(wordmark)
        luminance_values = []
        accent_pixels = 0
        for py in range(wordmark.height):
            for px in range(wordmark.width):
                if alpha.getpixel((px, py)) < 128:
                    continue
                r, g, b, _a = dark_panel.getpixel((px, py))
                luminance_values.append((r * 299 + g * 587 + b * 114) // 1000)
                if g >= 120 and b >= 100 and g > r:
                    accent_pixels += 1

        assert luminance_values
        assert sum(luminance_values) / len(luminance_values) >= 135
        assert accent_pixels >= 400


def test_section_wordmark_draws_with_configured_offset():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGBA", (260, 80), (255, 255, 255, 0))
    baseline = image.copy()

    box = plugin._draw_section_wordmark(image, "recent_live", 20, 24)

    expected_w, expected_h = STEAM_SECTION_WORDMARK_SIZES["recent_live"]
    expected_y = 24 + STEAM_SECTION_WORDMARK_Y_OFFSET
    assert box == (20, expected_y, 20 + expected_w, expected_y + expected_h)
    diff = ImageChops.difference(image, baseline)
    assert diff.crop(box).getbbox() is not None
    assert diff.crop((0, 0, 19, image.height)).getbbox() is None


def test_dashboard_render_uses_section_wordmarks_for_lower_titles():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    plugin._avatar_image = lambda _url, size: Image.new("RGBA", (size, size), (80, 90, 100, 255))
    plugin._game_square_icon = lambda _data, _appid, size: Image.new("RGBA", (size, size), (0, 0, 0, 255))
    calls = []
    original_wordmark = plugin._draw_section_wordmark

    def capture_wordmark(image, key, x, y):
        calls.append((key, int(x), int(y)))
        return original_wordmark(image, key, x, y)

    plugin._draw_section_wordmark = capture_wordmark
    data = {
        "profile": {"personaname": "Player", "personastate": 1, "avatarfull": ""},
        "level": 1,
        "friend_count": 0,
        "online_friend_count": 0,
        "recent_games": [],
        "owned_games": [],
        "friends": [],
        "app_details": {},
        "updated_at": "2026-06-26 14:35",
        "api_calls": 0,
        "refresh_mode": "cache",
        "warnings": [],
    }

    image = plugin._render_dashboard(data, (800, 480), {"mode": "day"})

    assert image.size == (800, 480)
    assert [key for key, _x, _y in calls] == ["recent_live", "library_friends"]

def test_game_backdrop_asset_is_exact_dashboard_slot_size():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    backdrop = plugin._game_backdrop_image((800, 232))

    assert backdrop.size == (800, 232)
    assert backdrop.getbbox() is not None
    assert len(backdrop.getcolors(maxcolors=800 * 232)) > 20


def test_game_backdrop_draws_behind_top_dashboard_area():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (800, 480), "black")
    baseline = image.copy()

    plugin._draw_game_backdrop(image, 0, 16, 800, 232)

    diff = ImageChops.difference(image, baseline)
    assert diff.crop((0, 16, 800, 248)).getbbox() is not None
    assert diff.crop((0, 0, 800, 12)).getbbox() is None


def test_dashboard_background_uses_theme_specific_assets():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    fallback = (1, 2, 3)

    day = plugin._dashboard_background((160, 96), fallback, theme_mode="day")
    night = plugin._dashboard_background((160, 96), fallback, theme_mode="night")

    assert day.size == (160, 96)
    assert night.size == (160, 96)
    assert ImageChops.difference(day, Image.new("RGB", day.size, fallback)).getbbox() is not None
    assert ImageChops.difference(day, night).getbbox() is not None
    assert sum(ImageStat.Stat(day.resize((1, 1))).mean) > sum(ImageStat.Stat(night.resize((1, 1))).mean)

def test_dashboard_render_does_not_draw_dark_backdrop_behind_avatar():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    plugin._avatar_image = lambda _url, size: Image.new("RGBA", (size, size), (80, 90, 100, 255))
    plugin._game_square_icon = lambda _data, _appid, size: Image.new("RGBA", (size, size), (0, 0, 0, 255))

    def fail_if_backdrop_drawn(*_args, **_kwargs):
        raise AssertionError("top game backdrop should not be drawn behind the avatar")

    plugin._draw_game_backdrop = fail_if_backdrop_drawn
    data = {
        "profile": {"personaname": "Player", "personastate": 1, "avatarfull": ""},
        "level": 1,
        "friend_count": 0,
        "online_friend_count": 0,
        "recent_games": [],
        "owned_games": [],
        "friends": [],
        "app_details": {},
        "updated_at": "2026-06-18 20:30",
        "api_calls": 0,
        "refresh_mode": "cache",
        "warnings": [],
    }

    image = plugin._render_dashboard(data, (800, 480), {"mode": "day"})

    assert image.size == (800, 480)


def test_dashboard_render_draws_simple_white_avatar_decoration_frame():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    plugin._avatar_image = lambda _url, size: Image.new("RGBA", (size, size), (80, 90, 100, 255))
    plugin._game_square_icon = lambda _data, _appid, size: Image.new("RGBA", (size, size), (0, 0, 0, 255))
    calls = []

    def capture_frame(_draw, avatar_box, outline, muted, fonts):
        calls.append((avatar_box, outline, muted, sorted(fonts.keys())))

    plugin._draw_avatar_gamepad_frame = capture_frame
    data = {
        "profile": {"personaname": "Player", "personastate": 1, "avatarfull": ""},
        "level": 1,
        "friend_count": 0,
        "online_friend_count": 0,
        "recent_games": [],
        "owned_games": [],
        "friends": [],
        "app_details": {},
        "updated_at": "2026-06-18 20:35",
        "api_calls": 0,
        "refresh_mode": "cache",
        "warnings": [],
    }

    image = plugin._render_dashboard(data, (800, 480), {"mode": "day"})

    assert image.size == (800, 480)
    assert len(calls) == 1
    avatar_box, outline, muted, font_keys = calls[0]
    assert avatar_box == (36, 36, 199, 199)
    assert outline == (255, 255, 255)
    assert all(channel >= 232 for channel in muted)
    assert "title" in font_keys


def test_dashboard_render_does_not_draw_horizontal_game_strip():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    plugin._avatar_image = lambda _url, size: Image.new("RGBA", (size, size), (80, 90, 100, 255))
    plugin._game_square_icon = lambda _data, _appid, size: Image.new("RGBA", (size, size), (0, 0, 0, 255))

    def fail_if_strip_drawn(*_args, **_kwargs):
        raise AssertionError("horizontal game strip should not be drawn in the dashboard render")

    plugin._draw_game_strip = fail_if_strip_drawn
    data = {
        "profile": {"personaname": "Player", "personastate": 1, "avatarfull": ""},
        "level": 1,
        "friend_count": 0,
        "online_friend_count": 0,
        "recent_games": [],
        "owned_games": [],
        "friends": [],
        "app_details": {},
        "updated_at": "2026-06-18 20:45",
        "api_calls": 0,
        "refresh_mode": "cache",
        "warnings": [],
    }

    image = plugin._render_dashboard(data, (800, 480), {"mode": "day"})

    assert image.size == (800, 480)


def test_recent_items_show_four_recent_games_in_left_column():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    data = {
        "profile": {},
        "recent_games": [
            {
                "appid": index,
                "name": f"Game {index}",
                "playtime_2weeks": index * 60,
                "playtime_forever": index * 600,
            }
            for index in range(1, 8)
        ],
    }

    items = plugin._recent_items(data)
    visible_appids = plugin._visible_game_appids(data)

    assert [item["appid"] for item in items[:4]] == [1, 2, 3, 4]
    assert all(item.get("compact_suffix", "").startswith(" - ") for item in items[:4])
    assert all(item.get("detail", "").startswith("\u8fd12\u5468") for item in items[:4])
    assert 5 not in [item.get("appid") for item in items]
    assert "6" in visible_appids
    assert "7" not in visible_appids


def test_recent_items_fill_fourth_slot_from_owned_games_when_recent_short():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    data = {
        "profile": {},
        "recent_games": [
            {
                "appid": index,
                "name": f"Recent {index}",
                "playtime_2weeks": index * 10,
                "playtime_forever": index * 100,
            }
            for index in range(1, 4)
        ],
        "owned_games": [
            {"appid": 99, "name": "Library Filler", "playtime_forever": 99999},
            {"appid": 1, "name": "Recent 1", "playtime_forever": 100},
        ],
    }

    items = plugin._recent_items(data)

    assert [item.get("appid") for item in items[:4]] == [1, 2, 3, 99]
    assert items[3]["prefix"] == "\u5e38\u73a9\uff1a"
    assert items[3]["compact_suffix"].startswith(" - ")
    assert items[3]["detail"].startswith("\u603b\u8ba1")


def test_recent_item_draws_full_detail_as_two_line_dense_row():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (280, 76), "white")
    baseline = image.copy()
    draw = ImageDraw.Draw(image)
    font = plugin._font(14, bold=True)
    plugin._game_square_icon = lambda _data, _appid, size: Image.new("RGBA", (size, size), (20, 40, 80, 255))

    next_y, fits = plugin._draw_recent_item(
        image,
        draw,
        {
            "appid": 123,
            "name": "A Very Long Steam Game Name That Used To Wrap Into Two Lines",
            "detail": "\u8fd12\u5468 22h | \u603b\u8ba1 7824h",
        },
        10,
        10,
        font,
        (0, 0, 0),
        (0, 180, 90),
        240,
        54,
        {},
        (110, 120, 135),
    )

    diff = ImageChops.difference(image, baseline)
    assert fits is True
    assert next_y <= 44
    assert diff.crop((10, 10, 250, 28)).getbbox() is not None
    assert diff.crop((30, 28, 250, 48)).getbbox() is not None
    assert diff.crop((0, 54, image.width, image.height)).getbbox() is None


def test_recent_panel_tracked_noto_metrics_fit_four_rows_without_internal_gap(
    monkeypatch,
):
    tracked_noto = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "static"
        / "fonts"
        / "NotoSansSC-VF.ttf"
    )

    def load_tracked_noto(size, bold=False):
        font = ImageFont.truetype(tracked_noto, int(size))
        font.set_variation_by_axes([700 if bold else 400])
        return font

    monkeypatch.setattr(
        steam_profile_module,
        "get_base_ui_font",
        load_tracked_noto,
    )
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (360, 150), "white")
    draw = ImageDraw.Draw(image)
    font = plugin._font(14, bold=True)
    plugin._game_square_icon = lambda _data, _appid, size: Image.new("RGBA", (size, size), (20, 40, 80, 255))
    y = 8
    for index in range(4):
        y, fits = plugin._draw_recent_item(
            image,
            draw,
            {
                "appid": index + 1,
                "name": f"Recent Game {index + 1} With A Long Name",
                "detail": f"\u8fd12\u5468 {index + 1}h | \u603b\u8ba1 {(index + 1) * 100}h",
            },
            10,
            y,
            font,
            (0, 0, 0),
            (0, 180, 90),
            315,
            151,
            {},
            (110, 120, 135),
        )
        assert fits is True
        if index < 3:
            y += 3
    # Four 33px rows plus the existing 3px inter-row spacing end at 149px.
    # A 1px title/detail gap would push the fourth row to 153px and clip it.
    assert y == 149

def test_recent_grid_uses_unboxed_single_line_playtime_rows():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (360, 160), "white")
    baseline = image.copy()
    draw = ImageDraw.Draw(image)
    font = plugin._font(14, bold=True)
    rule = (255, 0, 255)
    captured_lines = []
    icon_sizes = []
    original_line = draw.line
    original_clip = plugin._draw_single_line_clipped_text

    def capture_line(*args, **kwargs):
        captured_lines.append((args, kwargs))
        return original_line(*args, **kwargs)

    def capture_clip(draw, position, text, font, fill, max_width, max_bottom=None, min_size=10):
        captured_lines.append((("clip", position, text, getattr(font, "size", None), min_size, max_width), {}))
        return original_clip(draw, position, text, font, fill, max_width, max_bottom=max_bottom, min_size=min_size)

    draw.line = capture_line
    plugin._draw_single_line_clipped_text = capture_clip
    def recent_icon(_data, _appid, size):
        icon_sizes.append(size)
        return Image.new("RGBA", (size, size), (20, 40, 80, 255))

    plugin._game_square_icon = recent_icon
    items = [
        {
            "appid": index + 1,
            "name": f"Recent Game {index + 1} With A Long Name",
            "detail": f"\u8fd12\u5468 {index + 1}h | \u603b\u8ba1 {(index + 1) * 100}h",
        }
        for index in range(4)
    ]

    next_y, fits = plugin._draw_recent_grid(
        image,
        draw,
        items,
        10,
        8,
        315,
        148,
        font,
        (0, 0, 0),
        (0, 180, 90),
        rule,
        {},
        (110, 120, 135),
    )

    diff = ImageChops.difference(image, baseline)
    stat_calls = [args for args, _kwargs in captured_lines if args and args[0] == "clip" and "\u8fd12\u5468" in args[2]]
    assert fits is True
    assert next_y == 148
    assert diff.crop((10, 8, 325, 148)).getbbox() is not None
    assert diff.crop((0, 149, image.width, image.height)).getbbox() is None
    assert all(call[3] >= 12 for call in stat_calls)
    assert all(" | " not in call[2] for call in stat_calls)
    assert bytes(rule) not in image.tobytes()
    assert not [args for args, kwargs in captured_lines if args and args[0] != "clip"]
    assert icon_sizes == [29, 29, 29, 29]

def test_recent_grid_highlights_playing_prefix():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (360, 80), "white")
    draw = ImageDraw.Draw(image)
    font = plugin._font(14, bold=True)
    normal = (0, 0, 0)
    accent = (0, 180, 90)
    rule = (180, 190, 200)
    captured = []
    original_text = plugin._text

    def capture_text(draw, position, text, font, fill):
        captured.append((text, fill))
        original_text(draw, position, text, font, fill)

    plugin._text = capture_text
    plugin._game_square_icon = lambda _data, _appid, size: Image.new("RGBA", (size, size), (20, 40, 80, 255))

    next_y, fits = plugin._draw_recent_grid(
        image,
        draw,
        [{
            "appid": 123,
            "prefix": "\u6b63\u5728\u73a9\uff1a",
            "name": "Farthest Frontier",
            "detail": "\u8fd12\u5468 2h | \u603b\u8ba1 12h",
        }],
        10,
        8,
        315,
        48,
        font,
        normal,
        accent,
        rule,
        {},
        (110, 120, 135),
    )

    assert fits is True
    assert next_y == 44
    assert ("\u6b63\u5728\u73a9\uff1a", accent) in captured
    assert ("Farthest Frontier", normal) in captured

def test_extract_badge_icon_records_from_community_badge_rows():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    page_html = """
        <div class="badge_row is_link">
            <a class="badge_row_overlay" href="https://steamcommunity.com/profiles/1/gamecards/730/"></a>
            <div class="badge_info_image">
                <img src="https://community.fastly.steamstatic.com/public/shared/images/trans.gif" data-delayed-image="//community.fastly.steamstatic.com/economy/image/abc123/96fx96f" class="badge_icon">
            </div>
        </div>
        <div class="badge_row is_link">
            <a class="badge_row_overlay" href="https://steamcommunity.com/profiles/1/gamecards/457140/"></a>
            <div class="badge_info_image">
                <img src="https://community.fastly.steamstatic.com/public/shared/images/trans.gif" data-delayed-image="https://shared.fastly.steamstatic.com/community_assets/images/items/457140/562a65366fa2be4609d9cade098a39eb79abd089.png" class="badge_icon">
            </div>
        </div>
        <div class="badge_row is_link">
            <a class="badge_row_overlay" href="https://steamcommunity.com/profiles/1/gamecards/999/"></a>
            <div class="badge_info_image">
                <img data-delayed-image="https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/badges/skip.png" class="badge_icon">
            </div>
        </div>
    """

    records = plugin._extract_badge_icon_records(
        page_html,
        {"badges": [{"appid": 730}, {"appid": "457140"}]},
    )

    assert records == [
        {
            "appid": "730",
            "icon_url": "https://community.fastly.steamstatic.com/economy/image/abc123/96fx96f",
        },
        {
            "appid": "457140",
            "icon_url": "https://shared.fastly.steamstatic.com/community_assets/images/items/457140/562a65366fa2be4609d9cade098a39eb79abd089.png",
        },
    ]


def test_html_image_sources_include_delayed_badge_images():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    fragment = '<img src="/trans.gif" data-delayed-image="https://shared.fastly.steamstatic.com/community_assets/images/items/1/icon.png" srcset="https://example.test/one.png 1x, https://example.test/two.png 2x">'

    assert plugin._html_image_sources(fragment) == [
        "/trans.gif",
        "https://shared.fastly.steamstatic.com/community_assets/images/items/1/icon.png",
        "https://example.test/one.png",
        "https://example.test/two.png",
    ]


def test_badge_icon_scatter_can_focus_near_avatar_without_covering_avatar():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (260, 180), (10, 12, 16))
    baseline = image.copy()
    plugin._badge_icon_image = lambda _url, size: Image.new("RGBA", (size, size), (240, 220, 80, 255))
    avatar_box = (36, 36, 136, 136)

    plugin._draw_badge_icon_scatter(
        image,
        {"profile": {"steamid": "1"}, "badge_icons": [{"icon_url": "https://example.com/icon.png"}]},
        anchor_box=avatar_box,
        avoid_boxes=[avatar_box],
    )

    diff = ImageChops.difference(image, baseline)
    assert diff.getbbox() is not None
    assert diff.crop(avatar_box).getbbox() is None
    assert diff.crop((0, 0, 180, 170)).getbbox() is not None

def test_badge_icon_scatter_draws_background_icons():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    image = Image.new("RGB", (220, 140), (10, 12, 16))
    baseline = image.copy()
    plugin._badge_icon_image = lambda _url, size: Image.new("RGBA", (size, size), (240, 220, 80, 255))

    plugin._draw_badge_icon_scatter(
        image,
        {"profile": {"steamid": "1"}, "badge_icons": [{"icon_url": "https://example.com/icon.png"}]},
    )

    assert ImageChops.difference(image, baseline).getbbox() is not None
