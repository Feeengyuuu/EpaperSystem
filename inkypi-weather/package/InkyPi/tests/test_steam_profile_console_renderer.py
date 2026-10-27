"""Acceptance checks for the data-driven console, without network access."""

import colorsys
from collections import Counter
from copy import deepcopy

from PIL import Image, ImageChops, ImageDraw
import pytest

from plugins.steam_profile_dashboard import avatar_frame, console_renderer
from plugins.steam_profile_dashboard.avatar_frame import (
    AVATAR_BOX,
    AVATAR_SIZE,
    FRAME_ORIGIN,
    OVERLAY_SIZE,
    load_avatar_frame,
)
from plugins.steam_profile_dashboard.console_renderer import (
    _Console,
    CANVAS,
    CYAN,
    FRIEND_LIST,
    FRIEND_TILE,
    GREEN,
    PANEL,
    render_console,
)
from plugins.steam_profile_dashboard.steam_profile_dashboard import SteamProfileDashboard


@pytest.fixture
def data():
    return {
        "profile": {"personaname": "AshenOne", "personastate": 1,
                    "gameid": "108600", "gameextrainfo": "Project Zomboid"},
        "level": 103,
        "friend_count": 57,
        "online_friend_count": 6,
        "friends": [{"steamid": str(index), "personaname": f"Friend {index}",
                     "personastate": 1} for index in range(6)],
        "recent_games": [
            {"appid": 108600, "name": "Project Zomboid", "playtime_2weeks": 60,
             "playtime_forever": 180},
            {"appid": 3751260, "name": "The Blood of Dawnwalker 黎明行者",
             "playtime_2weeks": 1260, "playtime_forever": 2040},
            {"appid": 1867240, "name": "WARDOGS", "playtime_2weeks": 900,
             "playtime_forever": 2760},
            {"appid": 292030, "name": "巫师3：狂猎 — 重制版", "playtime_2weeks": 600,
             "playtime_forever": 8220},
        ],
        "owned_games": [
            {"appid": 1454400, "name": "Cookie Clicker", "playtime_forever": 39000},
            {"appid": 730, "name": "Counter-Strike 2", "playtime_forever": 31380},
            {"appid": 431960, "name": "Wallpaper Engine：壁纸引擎", "playtime_forever": 18960},
            {"appid": 108600, "name": "Project Zomboid", "playtime_forever": 180},
        ],
        "badges": {"badges": [{}] * 54, "player_xp": 59107},
    }


@pytest.fixture
def plugin(monkeypatch):
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    plugin.asset_calls = []

    def background(_data, appid, size):
        plugin.asset_calls.append(("background", str(appid), size))
        return Image.new("RGB", size, (130, 110, 70))

    def icon(_data, appid, size):
        plugin.asset_calls.append(("icon", str(appid), size))
        return Image.new("RGB", (size, size), (200, 80, 40))

    def avatar(_url, size, **_kwargs):
        plugin.asset_calls.append(("avatar", "", size))
        return Image.new("RGB", (size, size), (105, 120, 90))

    def framed():
        # A fixed frame whose opening clears the left margin keeps layout checks exact.
        return load_avatar_frame("12")

    monkeypatch.setattr(plugin, "_game_background", background, raising=False)
    monkeypatch.setattr(plugin, "_next_avatar_frame", framed)
    monkeypatch.setattr(plugin, "_game_square_icon", icon)
    monkeypatch.setattr(plugin, "_avatar_image", avatar)
    monkeypatch.setattr(plugin, "_profile_avatar_image", avatar, raising=False)
    return plugin


def _first_avatar_call(plugin):
    return next(call for call in plugin.asset_calls if call[0] == "avatar")


@pytest.fixture
def displayed(monkeypatch):
    records = []
    original = _Console.text

    def record(self, box, value, *args, **kwargs):
        records.append((box, str(value)))
        return original(self, box, value, *args, **kwargs)

    monkeypatch.setattr(_Console, "text", record)
    return records


def test_game_art_and_icons_are_preserved_over_a_solid_page_canvas(plugin, data, monkeypatch):
    # The navy console's flat canvas; a game's hue washes the gaps instead (tested below).
    monkeypatch.setattr(console_renderer, "game_palette", lambda _art: None)
    data["owned_games"].append({"appid": 99999, "name": "Not on screen", "playtime_forever": 1})
    image = render_console(plugin, data, (800, 480))
    assert image.mode == "RGB"
    assert image.size == (800, 480)
    backgrounds = Counter(appid for kind, appid, _ in plugin.asset_calls if kind == "background")
    icons = Counter(appid for kind, appid, _ in plugin.asset_calls if kind == "icon")
    expected = Counter({"108600": 2, "3751260": 1, "1867240": 1, "292030": 1,
                        "1454400": 1, "730": 1, "431960": 1})
    # The game being played lends its art once more, small, for the page's colours.
    assert backgrounds == expected + Counter({"108600": 1})
    assert icons == expected
    # The live friend's total belongs in the heading; the profile photo and the six online
    # friends' tiles are the only avatars fetched.
    assert sum(kind == "avatar" for kind, _, _ in plugin.asset_calls) == 7
    avatar_positions = [index for index, (kind, _, _) in enumerate(plugin.asset_calls)
                        if kind == "avatar"]
    game_positions = [index for index, (kind, _, _) in enumerate(plugin.asset_calls)
                      if kind in {"background", "icon"}]
    # Slow friend avatars cannot consume the shared game-icon network deadline.
    assert max(game_positions) < avatar_positions[1]
    # The requested flat colour belongs to the page underneath the game cards.
    # The game artwork itself remains present, with the existing navy shading.
    # The rank border starts at x=6; the canvas around it stays black.
    # Frame 12, kept from before the whole-frame canvas, crosses the rail divider
    # above the persona name and stops four pixels short of the hero panel.
    for box in ((183, 174, 193, 480), (190, 0, 193, 480), (791, 0, 800, 480), (0, 178, 6, 480),
                (0, 178, 12, 234), (0, 334, 12, 480)):
        crop = image.crop(box)
        assert ImageChops.difference(crop, Image.new("RGB", crop.size, CANVAS)).getbbox() is None
    # Above it, the left margin and the divider gap hold the avatar frame and nothing else.
    frame = load_avatar_frame("12").image
    expected = Image.new("RGB", (800, 480), CANVAS)
    expected.paste(frame, FRAME_ORIGIN, frame)
    for box in ((0, 0, 12, 178), (183, 0, 190, 174)):
        assert ImageChops.difference(image.crop(box), expected.crop(box)).getbbox() is None
    assert image.getpixel((200, 50)) != PANEL
    assert image.getpixel((235, 250)) != PANEL
    assert image.getpixel((259, 450)) != PANEL


def test_game_rows_use_per_game_hours_and_global_stats_remain_account_totals(plugin, data, displayed):
    render_console(plugin, data, (800, 480))
    first_row = [text for box, text in displayed if 244 <= box[1] < 273]
    assert "1h" in first_row
    assert "3h" in first_row
    rail = [text for box, text in displayed if box[0] < 181]
    assert "47h" in rail
    assert "1492h" in rail
    assert any(text == "6/57" for _, text in displayed)


def test_playing_row_badge_trails_off_between_the_name_and_hours(plugin, data, displayed):
    data["profile"].update(gameid="108600", gameextrainfo="Project Zomboid")
    render_console(plugin, data, (800, 480))
    badge = [box for box, text in displayed if text == "正在游玩..." and 244 <= box[1] < 273]
    assert badge
    assert all(box[0] > 509 and box[2] < 599 for box in badge)


def test_persona_name_sits_on_a_painted_stroke_in_the_game_hue(plugin, data, displayed, monkeypatch):
    # The navy console: the default cyan accent sets the paint's hue.
    monkeypatch.setattr(console_renderer, "game_palette", lambda _art: None)
    image = render_console(plugin, data, (800, 480))
    x, y, right, bottom = console_renderer.NAME_STROKE_BOX
    name = [box for box, text in displayed if text == "AshenOne"]
    assert name and all(x < box[0] and box[2] < right and y < box[1] and box[3] <= bottom for box in name)
    # The paint is the strip's dominant vivid colour; the dark name sits on it.
    vivid = [colorsys.rgb_to_hsv(*(channel / 255 for channel in image.getpixel((px, py))))
             for px in range(x, right, 2) for py in range(y, bottom, 2)]
    vivid = [hsv for hsv in vivid if hsv[1] >= 0.5 and hsv[2] >= 0.6]
    assert len(vivid) > 200
    hue = Counter(round(h, 2) for h, _s, _v in vivid).most_common(1)[0][0]
    assert abs(hue - _hue(CYAN)) < 0.05


def test_the_game_picks_the_stroke_shape(plugin, data, monkeypatch):
    monkeypatch.setattr(console_renderer, "game_palette", lambda _art: None)
    box = console_renderer.NAME_STROKE_BOX
    first = render_console(plugin, dict(data, profile=dict(data["profile"], gameid="108600")), (800, 480))
    other = render_console(plugin, dict(data, profile=dict(data["profile"], gameid="108601")), (800, 480))
    assert ImageChops.difference(first.crop(box), other.crop(box)).getbbox() is not None


def test_unknown_current_game_hours_are_not_filled_with_account_totals(plugin, data, displayed):
    data["profile"].update(gameid="777777", gameextrainfo="New Release")
    render_console(plugin, data, (800, 480))
    first_row = [text for box, text in displayed if 244 <= box[1] < 273 and box[0] > 590]
    assert first_row == ["—", "—"]
    assert ("background", "777777", (388, 162)) in plugin.asset_calls
    assert any(kind == "icon" and appid == "777777" for kind, appid, _ in plugin.asset_calls)


def test_same_game_has_consistent_rounded_hours_in_recent_and_top_cards(plugin, data, displayed):
    data["profile"].update(gameid="108600", gameextrainfo="Project Zomboid")
    data["recent_games"][0]["playtime_forever"] = 39035
    data["owned_games"][-1]["playtime_forever"] = 39035
    render_console(plugin, data, (800, 480))
    assert any(text == "651h" and box[0] == 701 and 244 <= box[1] < 273
               for box, text in displayed)
    assert any(text == "651h" and box[1] >= 400 for box, text in displayed)


@pytest.mark.parametrize("name", ["Project Zomboid", "未来新游戏中文标题与世界探索之旅全新完整版"])
def test_recent_visible_glyphs_and_hours_share_icon_vertical_center(plugin, data, name):
    data["recent_games"][0]["name"] = name
    image = render_console(plugin, data, (800, 480))
    row_top, row_bottom = 244, 272
    for left, right in ((243, 537), (599, 678), (701, 780)):
        crop = image.crop((left, row_top, right, row_bottom))
        bounds = _text_bounds(crop)
        assert bounds is not None
        ink_center = row_top + (bounds[1] + bounds[3] - 1) / 2
        icon_center = 246 + (24 - 1) / 2
        assert abs(ink_center - icon_center) <= 1


@pytest.mark.parametrize("name", ["Project Zomboid", "The Blood of Dawnwalker 黎明行者之血"])
def test_hero_icon_centers_on_the_visible_title_and_status_group(plugin, data, name, displayed):
    data["profile"]["gameextrainfo"] = name
    data["recent_games"][0]["name"] = name
    image = render_console(plugin, data, (800, 480))
    title_box = next(box for box, text in displayed if text == name and box[0] == 281)
    status_box = next(box for box, text in displayed if text == "游戏中 · AppID 108600")
    assert title_box[3] < status_box[1]
    assert title_box[0] > 209 + 56
    crop = image.crop((281, 43, 571, 205))
    bounds = _text_bounds(crop)
    assert bounds is not None
    glyph_group_center = 43 + (bounds[1] + bounds[3] - 1) / 2
    icon_center = 96 + (56 - 1) / 2
    assert abs(glyph_group_center - icon_center) <= 1


def test_top_game_title_and_hours_group_centers_on_each_independent_icon(plugin, data):
    image = render_console(plugin, data, (800, 480))
    for x in (199, 396, 593):
        crop = image.crop((x + 63, 400, x + 187, 459))
        bounds = _text_bounds(crop)
        assert bounds is not None
        text_group_center = 400 + (bounds[1] + bounds[3] - 1) / 2
        icon_center = 412 + (33 - 1) / 2
        assert abs(text_group_center - icon_center) <= 1


def _text_bounds(crop):
    # The fixture's artwork stays below this level after shading.  This mask
    # measures the real white/cyan glyph pixels independently of restored art.
    channels = crop.split()
    bright = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    return bright.point(lambda value: 255 if value > 190 else 0).getbbox()


def test_long_names_are_contained_and_do_not_replace_hour_columns(plugin, data, displayed, monkeypatch):
    data["profile"]["personaname"] = "VeryLongSteamAccountName" * 8
    data["profile"]["gameextrainfo"] = "这是一个很长的未来新游戏名称 " * 9
    data["recent_games"][0]["name"] = data["profile"]["gameextrainfo"]
    data["friends"][0]["personaname"] = "好友备注名字非常非常非常长" * 6
    glyphs = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        result = original(self, xy, text, *args, **kwargs)
        if kwargs.get("anchor") == "lt":
            bounds = self.textbbox(xy, text, font=kwargs.get("font"),
                                   anchor=kwargs.get("anchor"), stroke_width=0)
            glyphs.append((bounds, self._image.size, text))
        return result

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    image = render_console(plugin, data, (800, 480))
    assert image.size == (800, 480)
    assert glyphs
    # Every actual glyph is constrained to its text tile, including two-line CJK
    # game titles.  A small negative bearing is permitted and clipped by the tile.
    overflows = [(bounds, size, text) for bounds, size, text in glyphs
                 if bounds[2] > size[0] + 1 or bounds[3] > size[1] + 1]
    assert not overflows
    assert all(0 <= left < right <= 800 and 0 <= top < bottom <= 480
               for (left, top, right, bottom), _ in displayed)
    assert any(text == "1h" for _, text in displayed)
    assert any(text == "3h" for _, text in displayed)


def test_empty_offline_profile_and_unavailable_art_render_honestly(plugin, displayed, monkeypatch):
    monkeypatch.setattr(plugin, "_game_background", lambda *_args: None)
    monkeypatch.setattr(plugin, "_game_square_icon", lambda *_args: None)
    image = render_console(plugin, {"profile": {"personastate": 0}, "friend_count": 0,
                                    "online_friend_count": 0}, (800, 480))
    values = [text for _, text in displayed]
    assert "最近游玩" in values
    assert "离线" in values
    assert "目前没有在线好友" in values
    assert "没有公开的近期游戏数据" in values
    assert "暂无公开的累计游玩记录" in values
    assert "正在玩" not in values
    assert image.mode == "RGB"


def test_offline_profile_with_recent_game_does_not_claim_playing(plugin, data, displayed):
    data["profile"] = {"personaname": "AshenOne", "personastate": 0}
    render_console(plugin, data, (800, 480))
    assert "离线 · AppID 108600" in [text for _, text in displayed]
    assert "正在玩" not in [text for _, text in displayed]


def test_owned_fallback_is_not_mislabeled_as_recent_activity(plugin, data, displayed):
    data["profile"] = {"personaname": "AshenOne", "personastate": 0}
    data["recent_games"] = []
    render_console(plugin, data, (800, 480))
    values = [text for _, text in displayed]
    assert "游戏精选" in values
    assert "最近 / 常玩" in values
    assert "最近游玩" not in values


def test_friend_remark_and_game_activity_use_provider_display_names(plugin, data, displayed, monkeypatch):
    data["friends"][0].update(remark="城", gameid="108600", gameextrainfo="Project Zomboid")
    monkeypatch.setattr(plugin, "_friend_display_id", lambda friend: friend.get("remark") or friend["personaname"])
    render_console(plugin, data, (800, 480))
    friend_names = [text for box, text in displayed if box[0] == FRIEND_LIST[0] + 43]
    assert "城" in friend_names
    assert "Friend 0" not in friend_names
    assert any(text == "Project Zomboid" and box[0] == FRIEND_LIST[0] + 61 for box, text in displayed)


def test_selected_navy_design_is_stable_across_themes_and_scales_to_device(plugin, data):
    original = deepcopy(data)
    day = render_console(plugin, data, (800, 480), {"mode": "day"})
    night = render_console(plugin, data, (800, 480), {"mode": "night"})
    assert day.tobytes() == night.tobytes()
    assert render_console(plugin, data, (1600, 960)).size == (1600, 960)
    assert data == original


def test_friend_game_activity_shows_the_game_icon_before_its_name(plugin, data, displayed):
    data["friends"][0].update(gameid="1172470", gameextrainfo="Company of Heroes 3")
    image = render_console(plugin, data, (800, 480))
    x, y = FRIEND_LIST[:2]

    assert ("icon", "1172470", 14) in plugin.asset_calls
    # A friend in a game leads the list as a row: the fixture's solid icon before the game's
    # name, and the avatar ringed in the in-game green.
    assert image.getpixel((x + 50, y + 28)) == (200, 80, 40)
    assert image.getpixel((x + 20, y + 1)) == GREEN
    assert any(text == "Company of Heroes 3" and box[0] == x + 61 for box, text in displayed)


def test_game_logos_sit_on_the_page_without_a_border(plugin, data):
    image = render_console(plugin, data, (800, 480))
    # Hero, four recent rows and the top three, as (x, y, size). A friend's
    # game keeps its green in-game frame (see the friend activity test).
    logos = ([(209, 96, 56)] + [(206, 246 + row * 29, 24) for row in range(4)]
             + [(222 + rank * 197, 412, 33) for rank in range(3)])

    for x, y, size in logos:
        middle_x, middle_y = x + size // 2, y + size // 2
        assert image.getpixel((middle_x, middle_y)) == (200, 80, 40)
        for xy in ((x - 1, middle_y), (x + size, middle_y), (middle_x, y - 1), (middle_x, y + size)):
            assert image.getpixel(xy) not in (CYAN, GREEN)


def test_hero_game_title_is_outlined_over_its_artwork(plugin, data, monkeypatch):
    # A bright, busy backdrop is where an unoutlined white title gets lost.
    monkeypatch.setattr(plugin, "_game_background",
                        lambda _data, appid, size: Image.new("RGB", size, (235, 235, 225)))
    image = render_console(plugin, data, (800, 480))
    title = image.crop((281, 50, 571, 150))
    pixels = {color: count for count, color in title.getcolors(1 << 16)}

    assert pixels[(242, 248, 253)] > 100
    # The outline is the panel's own black ink, hugging the white glyphs.
    assert pixels[CANVAS] > 100


def test_friend_game_without_icon_keeps_the_status_dot(plugin, data, displayed, monkeypatch):
    original = plugin._game_square_icon
    monkeypatch.setattr(
        plugin, "_game_square_icon",
        lambda game_data, appid, size: None if str(appid) == "1172470" else original(game_data, appid, size),
    )
    data["friends"][0].update(gameid="1172470", gameextrainfo="Company of Heroes 3")
    image = render_console(plugin, data, (800, 480))
    x, y = FRIEND_LIST[:2]

    # No placeholder icon: the game's name takes its place, the ring still says in a game.
    assert image.getpixel((x + 20, y + 1)) == GREEN
    assert any(text == "Company of Heroes 3" and box[0] == x + 43 for box, text in displayed)


def test_friend_game_icons_are_fetched_before_any_friend_avatar(plugin, data):
    for index, appid in enumerate(("1172470", "2868840", "570")):
        data["friends"][index].update(gameid=appid, gameextrainfo=f"Game {appid}")
    render_console(plugin, data, (800, 480))

    friend_icons = [index for index, (kind, appid, size) in enumerate(plugin.asset_calls)
                    if kind == "icon" and size == 14]
    avatars = [index for index, (kind, _, _) in enumerate(plugin.asset_calls) if kind == "avatar"]
    assert len(friend_icons) == 3
    # Avatars after the profile photo are friend avatars; game media keeps priority.
    assert max(friend_icons) < avatars[1]


@pytest.mark.parametrize("count", [2, 3, 9, 14])
def test_overflow_mosaic_uses_only_remaining_avatar_records_inside_one_avatar_slot(plugin, monkeypatch, count):
    calls = []

    def avatar(url, size, **kwargs):
        calls.append((url, size, kwargs))
        return Image.new("RGB", (size, size), (170, 100, 60))

    monkeypatch.setattr(plugin, "_avatar_image", avatar)
    console = _Console(plugin, {})
    before = console.image.copy()
    remaining = [{"personaname": f"Hidden {index}", "avatarfull": f"cached-avatar-{index}"} for index in range(count)]
    shown = console._friend_mosaic(remaining, 720, 140)

    assert shown == min(count, 9)
    assert [url for url, _size, _kwargs in calls] == [row["avatarfull"] for row in remaining[:9]]
    assert all(kwargs == {"decorative_outline": False} for _url, _size, kwargs in calls)
    bounds = ImageChops.difference(before, console.image).getbbox()
    assert bounds and 720 <= bounds[0] < bounds[2] <= 758 and 140 <= bounds[1] < bounds[3] <= 178


@pytest.mark.parametrize("presence,colour", [
    ({"personastate": 1}, console_renderer.STEAM_ONLINE),
    ({"personastate": 5}, console_renderer.STEAM_ONLINE),
    ({"personastate": "6"}, console_renderer.STEAM_ONLINE),
    ({"personastate": 2}, console_renderer.AWAY),
    ({"personastate": 3}, console_renderer.AWAY),
    ({"personastate": 4}, console_renderer.AWAY),
    ({"personastate": 3, "gameid": "730"}, GREEN),
    ({"personastate": 1, "gameextrainfo": "Game"}, GREEN),
])
@pytest.mark.parametrize("count,ring_x,centre_y", [(3, 728, 148), (9, 725, 145)])
def test_mini_rings_match_large_avatar_presence_without_covering_neighbours(
        plugin, presence, colour, count, ring_x, centre_y):
    friend = {"avatarfull": "cached-avatar", **presence}
    console = _Console(plugin, {})
    console._friend_ring(friend, 300, 100, size=18)
    assert console.image.getpixel((308, 98)) == colour
    before = console.image.copy()
    console._friend_mosaic([friend] * count, 720, 140)

    assert console.image.getpixel((ring_x, 140)) == colour
    assert console.image.getpixel((ring_x, centre_y)) == (105, 120, 90)
    # Both the 18px triangle and 12px dense grid leave their original column gap clear.
    gap_x = 738 if count == 3 else 732
    assert console.image.getpixel((gap_x, centre_y)) == before.getpixel((gap_x, centre_y))
    bounds = ImageChops.difference(before, console.image).getbbox()
    assert bounds and 720 <= bounds[0] < bounds[2] <= 758 and 140 <= bounds[1] < bounds[3] <= 178


@pytest.mark.parametrize("count", [8, 15])
def test_overflow_mosaic_keeps_the_first_five_names_and_hides_extra_names(plugin, data, displayed, count):
    data["friends"] = [{"steamid": str(index), "personaname": f"Friend {index:02}", "personastate": 1,
                        "avatarfull": f"cached-avatar-{index}"} for index in range(count)]
    data["online_friend_count"] = count
    render_console(plugin, data, (800, 480))
    labels = [text for _box, text in displayed]
    assert all(f"Friend {index:02}" in labels for index in range(5))
    assert all(f"Friend {index:02}" not in labels for index in range(5, count))
    assert "+3" not in labels and "更多在线" not in labels
    assert ("+1" in labels) == (count == 15)


def test_overflow_without_avatar_urls_keeps_a_count_instead_of_inventing_portraits(plugin, displayed):
    console = _Console(plugin, {})
    console._friend_tile(None, 710, 140, {}, [{"personaname": "Hidden"}] * 3)
    assert not plugin.asset_calls
    labels = [text for _box, text in displayed]
    assert "+3" in labels and "Hidden" not in labels and "更多在线" not in labels


def test_official_asset_budget_covers_main_panels_and_four_friend_games(tmp_path):
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    plugin._cache_dir = lambda: tmp_path
    # Hero 1 + recent rows 4 + top three 3 + visible online friends 4.
    assert plugin._official_game_assets().max_games >= 12


def test_rail_avatar_keeps_its_size_inside_the_breakout_frame(plugin, data, monkeypatch):
    # The navy console, so the rail behind the frame is its plain canvas.
    monkeypatch.setattr(console_renderer, "game_palette", lambda _art: None)
    image = render_console(plugin, data, (800, 480))
    frame = load_avatar_frame("12")
    avatar_left, avatar_top, avatar_right, avatar_bottom = AVATAR_BOX

    assert _first_avatar_call(plugin) == ("avatar", "", AVATAR_SIZE)
    for x in range(0, OVERLAY_SIZE[0], 3):
        for y in range(0, OVERLAY_SIZE[1], 3):
            opacity = frame.image.getpixel((x, y))[3]
            shown = image.getpixel((FRAME_ORIGIN[0] + x, FRAME_ORIGIN[1] + y))
            inside = avatar_left <= x < avatar_right and avatar_top <= y < avatar_bottom
            mask = frame.mask.getpixel((x - avatar_left, y - avatar_top)) if inside else 0
            if opacity == 255:
                assert shown == frame.image.getpixel((x, y))[:3]
            elif opacity == 0 and mask == 255:
                assert shown == (105, 120, 90)
            elif opacity == 0 and mask == 0 and not 179 <= x <= 183 and x < 190 and y < 175:
                # Outside the opening nothing shows above the name, apart from the rail divider.
                assert shown == CANVAS
    assert image.getpixel((89, 89)) == (105, 120, 90)


def test_the_whole_avatar_frame_lies_over_the_persona_name_and_hero_panel(plugin, data, monkeypatch):
    monkeypatch.setattr(console_renderer, "game_palette", lambda _art: None)
    frame = load_avatar_frame("90")
    monkeypatch.setattr(plugin, "_next_avatar_frame", lambda: frame)
    image = render_console(plugin, data, (800, 480))

    # Every solid pixel of the frame shows as drawn, down over the name and across the panel's edge.
    solid = [(x, y) for x in range(OVERLAY_SIZE[0]) for y in range(OVERLAY_SIZE[1])
             if frame.image.getpixel((x, y))[3] == 255]
    assert any(y >= 175 for _x, y in solid) and any(x >= 194 for x, _y in solid)
    for x, y in solid:
        assert image.getpixel((FRAME_ORIGIN[0] + x, FRAME_ORIGIN[1] + y)) == frame.image.getpixel((x, y))[:3]


def test_consecutive_renders_change_the_avatar_frame(plugin, data, monkeypatch):
    monkeypatch.setattr(avatar_frame, "_memory_states", {})
    dealt = []

    def rotate():
        frame = SteamProfileDashboard._next_avatar_frame(plugin)
        dealt.append(frame.frame_id)
        return frame

    monkeypatch.setattr(plugin, "_next_avatar_frame", rotate)
    rails = [render_console(plugin, data, (800, 480)).crop((0, 0, 182, 178)) for _ in range(31)]

    assert len(set(dealt[:30])) == 30  # one round deals each frame once
    assert all(previous != current for previous, current in zip(dealt, dealt[1:]))
    assert all(ImageChops.difference(a, b).getbbox() is not None for a, b in zip(rails, rails[1:]))


def test_rail_keeps_a_plain_avatar_when_no_frame_loads(plugin, data, monkeypatch):
    monkeypatch.setattr(plugin, "_next_avatar_frame", lambda: None)
    monkeypatch.setattr(console_renderer, "game_palette", lambda _art: None)

    image = render_console(plugin, data, (800, 480))

    assert _first_avatar_call(plugin) == ("avatar", "", 146)
    for xy in ((16, 16), (89, 89), (161, 161)):
        assert image.getpixel(xy) == (105, 120, 90)
    assert image.getpixel((10, 60)) == CANVAS


def test_style_version_moves_on_while_previous_cache_stays_compatible():
    from plugins.steam_profile_dashboard.steam_profile_dashboard import (
        STEAM_CACHED_DISPLAY_COMPATIBLE_STYLES,
        STEAM_DASHBOARD_STYLE_VERSION,
    )

    assert STEAM_DASHBOARD_STYLE_VERSION == "midnight-console-whole-avatar-frame-v47"
    assert STEAM_CACHED_DISPLAY_COMPATIBLE_STYLES[:6] == (
        "midnight-console-profile-backdrop-v46",
        "midnight-console-game-hue-v45",
        "midnight-console-outlined-hero-title-v44",
        "midnight-console-presence-ordered-friends-v43",
        "midnight-console-breakout-avatar-frames-v42",
        "midnight-console-rotating-avatar-frames-v41",
    )


def _hue(colour):
    return colorsys.rgb_to_hsv(*(channel / 255 for channel in colour))[0]


def test_game_palette_takes_the_hue_of_the_art_and_skips_grey_art():
    red = console_renderer.game_palette(Image.new("RGB", (96, 54), (200, 40, 30)))
    teal = console_renderer.game_palette(Image.new("RGB", (96, 54), (40, 150, 140)))

    assert console_renderer.game_palette(Image.new("RGB", (96, 54), (120, 120, 120))) is None
    assert console_renderer.game_palette(None) is None
    for palette, hue in ((red, _hue((200, 40, 30))), (teal, _hue((40, 150, 140)))):
        for role in ("canvas", "panel", "header", "border", "accent", "glow"):
            assert abs(_hue(palette[role]) - hue) < 0.03, role
        # Dark panels under light accents, so white text still reads.
        assert max(palette["panel"]) < 60
        assert sum(palette["accent"]) > 3 * 150


def test_the_page_takes_the_colours_of_the_game_being_played(plugin, data, monkeypatch):
    def background(_data, appid, size):
        plugin.asset_calls.append(("background", str(appid), size))
        colour = (200, 40, 30) if str(appid) == "108600" else (40, 150, 140)
        return Image.new("RGB", size, colour)

    monkeypatch.setattr(plugin, "_game_background", background, raising=False)
    playing = render_console(plugin, data, (800, 480))
    data["profile"].update(gameid="730", gameextrainfo="Counter-Strike 2")
    other = render_console(plugin, data, (800, 480))

    # The panel colour between the friends' tiles follows each game's own hue.
    gap = (FRIEND_LIST[0] + FRIEND_TILE[0] + 1, 120)
    assert abs(_hue(playing.getpixel(gap)) - _hue((200, 40, 30))) < 0.05
    assert abs(_hue(other.getpixel(gap)) - _hue((40, 150, 140))) < 0.05


def test_equipped_profile_background_becomes_a_still_image_record():
    record = SteamProfileDashboard._profile_background_record({"response": {"profile_background": {
        "image_large": "items/1091500/2f2692d068cb37afb353e8577b2c039da9fe7944.jpg",
        "movie_webm": "items/1091500/994eb96bd85c1204e49f38da6e8e90180bdb93c4.webm",
        "item_title": "Consumerism"}}})

    assert record == {"url": "https://shared.akamai.steamstatic.com/community_assets/images/items/1091500/"
                             "2f2692d068cb37afb353e8577b2c039da9fe7944.jpg", "name": "Consumerism",
                      "slot": "profile_background"}
    for equipped in ({}, {"response": {"profile_background": {}}},
                     {"response": {"profile_background": {"image_large": "https://evil.example/x.jpg"}}}):
        assert SteamProfileDashboard._profile_background_record(equipped) is None


def test_equipped_mini_profile_background_is_preferred_for_the_rail():
    record = SteamProfileDashboard._profile_background_record({"response": {
        "profile_background": {"image_large": "items/1091500/2f2692d068cb37afb353e8577b2c039da9fe7944.jpg"},
        "mini_profile_background": {"image_large": "items/1999520/b48f1a22a3752a3fd66c056b0728bcf3b67e740e.jpg",
                                    "item_title": "Multicato"},
    }})

    assert record["slot"] == "mini_profile_background"
    assert record["name"] == "Multicato"
    assert record["url"].endswith("items/1999520/b48f1a22a3752a3fd66c056b0728bcf3b67e740e.jpg")
    # An invalid mini background falls back to the profile background.
    fallback = SteamProfileDashboard._profile_background_record({"response": {
        "profile_background": {"image_large": "items/1091500/2f2692d068cb37afb353e8577b2c039da9fe7944.jpg"},
        "mini_profile_background": {"image_large": "https://evil.example/x.jpg"},
    }})
    assert fallback["slot"] == "profile_background"


def test_rail_lays_the_profile_background_blurred_and_dimmed_behind_the_rail(plugin, data, monkeypatch):
    monkeypatch.setattr(console_renderer, "game_palette", lambda _art: None)
    plain = render_console(plugin, deepcopy(data), (800, 480))
    loaded = []

    def background(url):
        loaded.append(url)
        return Image.new("RGB", (853, 480), (240, 200, 0))

    monkeypatch.setattr(plugin, "_profile_background_image", background, raising=False)
    data["profile_background"] = {"url": "https://example.test/bg.jpg", "name": "Bg"}
    image = render_console(plugin, data, (800, 480))

    assert loaded == ["https://example.test/bg.jpg"]
    top, bottom = image.getpixel((10, 230)), image.getpixel((10, 470))
    # A deep shade with the picture's yellow cast, darker toward the stats; the rest of the page is untouched.
    assert top[0] > top[2] + 20 and sum(bottom) < sum(top)
    assert top != plain.getpixel((10, 230))
    assert ImageChops.difference(image.crop((183, 0, 800, 480)), plain.crop((183, 0, 800, 480))).getbbox() is None
