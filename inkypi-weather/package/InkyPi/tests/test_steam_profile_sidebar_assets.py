"""The bundled sidebar artwork is local, contained, and presentation-only."""

import hashlib

from PIL import Image, ImageChops, ImageDraw
import pytest

from plugins.steam_profile_dashboard import console_renderer, sidebar_assets
from plugins.steam_profile_dashboard.avatar_frame import load_avatar_frame
from plugins.steam_profile_dashboard.steam_profile_dashboard import SteamProfileDashboard


@pytest.fixture
def assets(tmp_path, monkeypatch):
    monkeypatch.setattr(sidebar_assets, "ASSET_ROOT", tmp_path)
    sidebar_assets._source.cache_clear()
    yield tmp_path
    sidebar_assets._source.cache_clear()


def test_alpha_bounds_are_contained_without_transparent_export_padding(assets):
    source = Image.new("RGBA", (200, 100))
    ImageDraw.Draw(source).rectangle((110, 20, 149, 79), fill=(130, 215, 245, 255))
    source.save(assets / "games.png")
    icon = sidebar_assets.sidebar_asset("games", 20)
    assert icon.mode == "RGBA" and icon.size == (20, 20)
    assert icon.getchannel("A").getbbox() == (3, 0, 16, 20)
    # Returning a new tile prevents one render from mutating the process cache.
    icon.paste((0, 0, 0, 0), (0, 0, 20, 20))
    assert sidebar_assets.sidebar_asset("games", 20).getchannel("A").getbbox() is not None


def test_level_frame_keeps_transparent_center_and_assets_are_readonly(assets, monkeypatch):
    source = Image.new("RGBA", (100, 100))
    ImageDraw.Draw(source).rectangle((10, 10, 89, 89), outline=(129, 213, 246, 255), width=5)
    path = assets / "level_frame.png"
    source.save(path)
    before = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Sidebar loading must not acquire or write anything")

    monkeypatch.setattr(Image.Image, "save", forbidden)
    monkeypatch.setattr("utils.http_client.get_http_session", forbidden)
    frame = sidebar_assets.sidebar_asset("level_frame", 54)
    assert frame.getpixel((27, 27))[3] == 0
    assert frame.getchannel("A").getbbox() == (0, 0, 54, 54)
    assert (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == before
    assert [item.name for item in assets.iterdir()] == ["level_frame.png"]


def test_missing_corrupt_or_empty_artwork_safely_keeps_renderer_fallback(assets):
    (assets / "games.png").write_bytes(b"not a PNG")
    Image.new("RGBA", (20, 20)).save(assets / "friends.png")
    assert sidebar_assets.sidebar_asset("games", 20) is None
    assert sidebar_assets.sidebar_asset("friends", 20) is None
    assert sidebar_assets.sidebar_asset("recent", 18) is None
    assert sidebar_assets.sidebar_asset("../games", 20) is None
    assert sidebar_assets.sidebar_asset("games", 9999) is None


def test_generated_assets_change_only_sidebar_and_level_stays_dynamic(monkeypatch):
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    monkeypatch.setattr(plugin, "_game_background", lambda *_args: None)
    monkeypatch.setattr(plugin, "_game_square_icon", lambda *_args: None)
    monkeypatch.setattr(plugin, "_avatar_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_profile_avatar_image", lambda *_args: None)
    # Every render deals a new avatar frame; hold one so only the icons differ.
    monkeypatch.setattr(plugin, "_next_avatar_frame", lambda: load_avatar_frame("12"))
    data = {"profile": {"personaname": "Player"}, "level": 103,
            "friends": [], "recent_games": [], "owned_games": [], "badges": {}}
    monkeypatch.setattr(console_renderer, "sidebar_asset", lambda *_args: None)
    baseline = console_renderer.render_console(plugin, data, (800, 480))
    requested = []

    def generated(name, size):
        requested.append((name, size))
        tile = Image.new("RGBA", (size, size))
        ImageDraw.Draw(tile).rectangle((0, 0, size - 1, size - 1), outline=(245, 125, 25), width=2)
        return tile

    monkeypatch.setattr(console_renderer, "sidebar_asset", generated)
    actual = console_renderer.render_console(plugin, data, (800, 480))
    assert set(requested) == {("level_frame", 54), ("games", 20), ("friends", 20),
                              ("recent", 18), ("total", 18), ("badges", 20), ("xp", 20)}
    bounds = ImageChops.difference(actual, baseline).getbbox()
    assert bounds and bounds[2] <= 64 and bounds[1] >= 237
    assert ImageChops.difference(actual.crop((182, 0, 800, 480)), baseline.crop((182, 0, 800, 480))).getbbox() is None
    other_level = console_renderer.render_console(plugin, dict(data, level=204), (800, 480))
    assert ImageChops.difference(actual.crop((17, 254, 56, 277)), other_level.crop((17, 254, 56, 277))).getbbox() is not None


def test_complete_bundled_asset_set_loads_and_level_frame_leaves_digits_clear():
    for name in sidebar_assets.ASSET_NAMES:
        icon = sidebar_assets.sidebar_asset(name, 54 if name == "level_frame" else 20)
        assert icon is not None, f"Missing or invalid bundled sidebar artwork: {name}"
        assert icon.getchannel("A").getbbox() is not None
    frame = sidebar_assets.sidebar_asset("level_frame", 54)
    assert frame.getchannel("A").crop((16, 16, 38, 38)).getextrema() == (0, 0)
