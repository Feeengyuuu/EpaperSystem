"""Acceptance checks for the data-driven console, without network access."""

from collections import Counter
from copy import deepcopy

from PIL import Image, ImageChops, ImageDraw
import pytest

from plugins.steam_profile_dashboard.console_renderer import (
    _Console,
    CANVAS,
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

    monkeypatch.setattr(plugin, "_game_background", background, raising=False)
    monkeypatch.setattr(plugin, "_game_square_icon", icon)
    monkeypatch.setattr(plugin, "_avatar_image", avatar)
    monkeypatch.setattr(plugin, "_profile_avatar_image", avatar, raising=False)
    return plugin


@pytest.fixture
def displayed(monkeypatch):
    records = []
    original = _Console.text

    def record(self, box, value, *args, **kwargs):
        records.append((box, str(value)))
        return original(self, box, value, *args, **kwargs)

    monkeypatch.setattr(_Console, "text", record)
    return records


def test_game_art_and_icons_are_preserved_over_a_solid_page_canvas(plugin, data):
    data["owned_games"].append({"appid": 99999, "name": "Not on screen", "playtime_forever": 1})
    image = render_console(plugin, data, (800, 480))
    assert image.mode == "RGB"
    assert image.size == (800, 480)
    backgrounds = Counter(appid for kind, appid, _ in plugin.asset_calls if kind == "background")
    icons = Counter(appid for kind, appid, _ in plugin.asset_calls if kind == "icon")
    expected = Counter({"108600": 2, "3751260": 1, "1867240": 1, "292030": 1,
                        "1454400": 1, "730": 1, "431960": 1})
    assert backgrounds == expected
    assert icons == expected
    # The live friend's total belongs in the heading; only four visible avatars
    # should cause asset work even when more friends are online.
    assert sum(kind == "avatar" for kind, _, _ in plugin.asset_calls) == 5
    avatar_positions = [index for index, (kind, _, _) in enumerate(plugin.asset_calls)
                        if kind == "avatar"]
    game_positions = [index for index, (kind, _, _) in enumerate(plugin.asset_calls)
                      if kind in {"background", "icon"}]
    # Slow friend avatars cannot consume the shared game-icon network deadline.
    assert max(game_positions) < avatar_positions[1]
    # The requested flat colour belongs to the page underneath the game cards.
    # The game artwork itself remains present, with the existing navy shading.
    # The generated 54 px level frame reaches x=9; the canvas around it stays black.
    for box in ((183, 0, 193, 480), (791, 0, 800, 480), (0, 178, 9, 480),
                (0, 178, 12, 237), (0, 292, 12, 480)):
        crop = image.crop(box)
        assert ImageChops.difference(crop, Image.new("RGB", crop.size, CANVAS)).getbbox() is None
    # Above it, the left margin holds the gilded avatar frame and nothing else.
    from plugins.steam_profile_dashboard.avatar_frame import gilded_avatar_frame

    frame_margin = gilded_avatar_frame().crop((0, 0, 12, 178))
    expected_margin = Image.new("RGB", frame_margin.size, CANVAS)
    expected_margin.paste(frame_margin, (0, 0), frame_margin)
    assert ImageChops.difference(image.crop((0, 0, 12, 178)), expected_margin).getbbox() is None
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
    friend_names = [text for box, text in displayed if box[0] == 646]
    assert "城" in friend_names
    assert "Friend 0" not in friend_names
    assert any(text == "Project Zomboid" and box[0] == 665 for box, text in displayed)


def test_selected_navy_design_is_stable_across_themes_and_scales_to_device(plugin, data):
    original = deepcopy(data)
    day = render_console(plugin, data, (800, 480), {"mode": "day"})
    night = render_console(plugin, data, (800, 480), {"mode": "night"})
    assert day.tobytes() == night.tobytes()
    assert render_console(plugin, data, (1600, 960)).size == (1600, 960)
    assert data == original


def _friend_row_y(index):
    return 48 + index * 39


def test_friend_game_activity_shows_the_game_icon_before_its_name(plugin, data, displayed):
    data["friends"][0].update(gameid="1172470", gameextrainfo="Company of Heroes 3")
    image = render_console(plugin, data, (800, 480))
    y = _friend_row_y(0)

    assert ("icon", "1172470", 14) in plugin.asset_calls
    # The fixture icon is a solid colour; the status dot is replaced by the icon.
    assert image.getpixel((652, y + 27)) == (200, 80, 40)
    assert image.getpixel((645, y + 27)) == GREEN
    assert any(text == "Company of Heroes 3" and box[0] == 665 for box, text in displayed)


def test_friend_game_without_icon_keeps_the_status_dot(plugin, data, displayed, monkeypatch):
    original = plugin._game_square_icon
    monkeypatch.setattr(
        plugin, "_game_square_icon",
        lambda game_data, appid, size: None if str(appid) == "1172470" else original(game_data, appid, size),
    )
    data["friends"][0].update(gameid="1172470", gameextrainfo="Company of Heroes 3")
    image = render_console(plugin, data, (800, 480))
    y = _friend_row_y(0)

    assert image.getpixel((649, y + 26)) == GREEN
    assert any(text == "Company of Heroes 3" and box[0] == 658 for box, text in displayed)


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


def test_official_asset_budget_covers_main_panels_and_four_friend_games(tmp_path):
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    plugin._cache_dir = lambda: tmp_path
    # Hero 1 + recent rows 4 + top three 3 + visible online friends 4.
    assert plugin._official_game_assets().max_games >= 12


def test_rail_avatar_sits_inside_the_gilded_frame(plugin, data):
    from plugins.steam_profile_dashboard.avatar_frame import gilded_avatar_frame

    image = render_console(plugin, data, (800, 480))
    frame = gilded_avatar_frame()

    for xy in ((16, 16), (89, 89), (161, 161)):
        assert image.getpixel(xy) == (105, 120, 90)
    for xy in ((10, 60), (168, 120), (10, 168)):
        assert image.getpixel(xy) == frame.getpixel(xy)[:3]
    assert image.getpixel((176, 120)) == CANVAS


def test_style_version_moves_on_while_previous_cache_stays_compatible():
    from plugins.steam_profile_dashboard.steam_profile_dashboard import (
        STEAM_CACHED_DISPLAY_COMPATIBLE_STYLES,
        STEAM_DASHBOARD_STYLE_VERSION,
    )

    assert STEAM_DASHBOARD_STYLE_VERSION == "midnight-console-gilded-avatar-v40"
    assert STEAM_CACHED_DISPLAY_COMPATIBLE_STYLES[0] == "midnight-console-borderless-friends-v39"
