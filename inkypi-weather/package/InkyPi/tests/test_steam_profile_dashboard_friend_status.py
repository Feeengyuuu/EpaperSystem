import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.steam_profile_dashboard.steam_profile_dashboard import SteamProfileDashboard


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


def test_recent_items_show_six_recent_games_in_left_column():
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

    assert [item["appid"] for item in items[:6]] == [1, 2, 3, 4, 5, 6]
    assert 7 not in [item.get("appid") for item in items]
    assert "6" in visible_appids
    assert "7" not in visible_appids


def test_extract_badge_icon_records_from_community_badge_rows():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    page_html = """
        <div class="badge_row is_link" onclick="location.href='https://steamcommunity.com/profiles/1/gamecards/730/'">
            <div class="badge_icon">
                <img src="//community.fastly.steamstatic.com/economy/image/abc123/96fx96f">
            </div>
        </div>
        <div class="badge_row is_link" onclick="location.href='https://steamcommunity.com/profiles/1/gamecards/570/'">
            <div class="badge_icon">
                <img src="https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/badges/foo.png">
            </div>
        </div>
        <div class="badge_row is_link" onclick="location.href='https://steamcommunity.com/profiles/1/gamecards/999/'">
            <div class="badge_icon">
                <img src="https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/badges/skip.png">
            </div>
        </div>
    """

    records = plugin._extract_badge_icon_records(
        page_html,
        {"badges": [{"appid": 730}, {"appid": "570"}]},
    )

    assert records == [
        {
            "appid": "730",
            "icon_url": "https://community.fastly.steamstatic.com/economy/image/abc123/96fx96f",
        },
        {
            "appid": "570",
            "icon_url": "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/badges/foo.png",
        },
    ]


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
