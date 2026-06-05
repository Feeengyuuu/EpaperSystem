import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

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
