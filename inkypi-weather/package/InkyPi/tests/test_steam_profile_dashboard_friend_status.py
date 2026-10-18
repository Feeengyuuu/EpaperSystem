import sys
import traceback
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
import pytest

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


def test_generate_image_redacts_api_key_from_logs_error_and_traceback(
    caplog,
    monkeypatch,
):
    marker = "steam-credential-marker-123"
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    monkeypatch.setattr(plugin, "_read_cache", lambda _cache_key: None)

    class Response:
        def raise_for_status(self):
            raise RuntimeError(
                "503 Server Error for url: "
                "https://api.steampowered.com/ISteamUser/"
                f"GetPlayerSummaries/v2/?key={marker}&steamids=7"
            )

    class Session:
        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(steam_profile_module, "get_http_session", lambda: Session())

    with caplog.at_level("WARNING", logger=steam_profile_module.__name__):
        with pytest.raises(RuntimeError) as caught:
            plugin.generate_image({}, _SteamDeviceConfig())

    rendered_traceback = "".join(
        traceback.format_exception(caught.type, caught.value, caught.tb)
    )
    for public_text in (caplog.text, str(caught.value), rendered_traceback):
        assert marker not in public_text
        assert "GetPlayerSummaries" in public_text
    assert "<redacted>" in caplog.text
    assert "<redacted>" in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


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


def test_dashboard_renders_an_empty_profile():
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    plugin._avatar_image = lambda _url, size: Image.new("RGBA", (size, size), (80, 90, 100, 255))
    plugin._game_square_icon = lambda _data, _appid, size: Image.new("RGBA", (size, size), (0, 0, 0, 255))
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
