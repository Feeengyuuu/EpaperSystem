import sys
from pathlib import Path

from PIL import Image, ImageDraw

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import plugins.dota_profile_dashboard.dota_profile_dashboard as dota_module  # noqa: E402
from plugins.dota_profile_dashboard.dota_profile_dashboard import (  # noqa: E402
    DEFAULT_ACCOUNT_ID,
    DEFAULT_STEAM_ID64,
    DotaProfileDashboard,
)


def test_dashboard_background_layer_stays_flat_for_color_epaper():
    plugin = DotaProfileDashboard({"id": "dota_profile_dashboard"})
    background = (5, 9, 16)
    image = Image.new("RGB", (96, 64), background)

    plugin._draw_background_pattern(ImageDraw.Draw(image), image.width, image.height)

    assert image.getcolors(maxcolors=image.width * image.height) == [
        (image.width * image.height, background)
    ]


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), orientation="horizontal", env=None):
        self.resolution = resolution
        self.orientation = orientation
        self.env = env or {}

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"orientation": self.orientation, "theme_mode": "night"}
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, key):
        return self.env.get(key)


def make_plugin(tmp_path):
    plugin = DotaProfileDashboard({"id": "dota_profile_dashboard"})
    plugin._cache_dir = lambda: tmp_path
    return plugin


def test_account_id_defaults_to_known_steam_profile(tmp_path):
    plugin = make_plugin(tmp_path)

    assert plugin._account_id({}) == DEFAULT_ACCOUNT_ID
    assert plugin._account_id({"steamId64": DEFAULT_STEAM_ID64}) == DEFAULT_ACCOUNT_ID
    assert plugin._account_id({"accountId": "12345"}) == "12345"


def test_mock_generate_image_renders_dashboard(tmp_path):
    plugin = make_plugin(tmp_path)

    image = plugin.generate_image({"useMockData": "true"}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    generated = list(tmp_path.glob("*.png"))
    assert generated
    assert not any(path.name.startswith("image_") for path in generated)


def test_fetch_uses_no_key_endpoints_and_hero_cache(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    seen_paths = []

    def fake_get_json(path, params=None):
        seen_paths.append((path, params or {}))
        if path == "/heroStats":
            return [{"id": 1, "localized_name": "敌法师"}]
        if path.endswith("/wl"):
            return {"win": 1, "lose": 1}
        if path.endswith("/recentMatches"):
            return []
        if path.endswith("/heroes"):
            return []
        if path.endswith("/totals"):
            return []
        if path.endswith("/counts"):
            return {}
        if path.endswith("/rankings"):
            return []
        if path.endswith("/wordcloud"):
            return {}
        if "/records/" in path:
            return []
        return {"profile": {"personaname": "Tester"}, "rank_tier": 53}

    monkeypatch.setattr(plugin, "_get_json", fake_get_json)

    data = plugin._fetch_dashboard_data("123", {"includeWordcloud": "true", "includeRecords": "true"}, FakeDeviceConfig())

    assert data["profile"]["rank_tier"] == 53
    assert data["wl"]["win"] == 1
    assert any(path == "/players/123/recentMatches" for path, _ in seen_paths)
    assert any(path == "/heroStats" for path, _ in seen_paths)
    assert all("api_key" not in params for _, params in seen_paths)


def test_record_and_wordcloud_helpers_are_stable(tmp_path):
    plugin = make_plugin(tmp_path)
    data = plugin._sample_payload()

    assert plugin._record_lines(data)
    assert plugin._wordcloud_terms(data["wordcloud"])[:2] == ["push", "roshan"]
    assert "game_mode" in plugin._counts_line(data["counts"])


def test_hero_image_urls_prefer_working_steam_cdn(tmp_path):
    plugin = make_plugin(tmp_path)

    urls = plugin._hero_image_urls({"icon": "/apps/dota2/images/dota_react/heroes/icons/antimage.png?"})

    assert urls[0] == "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/icons/antimage.png?"
    assert "https://api.opendota.com/apps/dota2/images/dota_react/heroes/icons/antimage.png?" in urls


def test_hero_stats_apply_simplified_chinese_names(tmp_path):
    plugin = make_plugin(tmp_path)

    heroes = plugin._apply_hero_names(
        [{"id": 1, "localized_name": "Anti-Mage"}, {"id": 3, "localized_name": "Bane"}],
        {1: "敌法师", 3: "祸乱之源"},
    )

    assert heroes[1]["localized_name"] == "敌法师"
    assert heroes[1]["localized_name_en"] == "Anti-Mage"
    assert heroes[3]["localized_name"] == "祸乱之源"


def test_hero_square_icon_uses_black_background(tmp_path):
    plugin = make_plugin(tmp_path)
    raw = Image.new("RGBA", (16, 8), (255, 255, 255, 255))
    raw.putpixel((8, 4), (255, 0, 0, 255))

    icon = plugin._square_icon(raw, 24)

    assert icon.getpixel((1, 1))[:3] == (0, 0, 0)
    assert icon.getpixel((12, 12))[0] > 0
    assert icon.getpixel((12, 10))[:3] == (0, 0, 0)


def test_fetch_reads_the_peers_of_90_days_and_stands_without_them(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    seen = []
    monkeypatch.setattr(plugin, "_current_patch", lambda: {"name": "7.41f", "ts": 1789455600})
    monkeypatch.setattr(plugin, "_hero_stats", lambda params=None: {})

    def fake_get_json(path, params=None):
        seen.append((path, dict(params or {})))
        if path.endswith("/peers"):
            if fake_get_json.peers_fail:
                raise RuntimeError("HTTP 500")
            return [{"personaname": "Gunnar", "with_games": 40, "with_win": 25},
                    {"personaname": "  Oh   Wonder ", "with_games": 31, "with_win": 19},
                    {"personaname": "Once", "with_games": 1, "with_win": 1}, "junk",
                    {"personaname": "", "with_games": 9, "with_win": 3}]
        return {} if not path.endswith(("/recentMatches", "/heroes", "/totals", "/rankings")) else []

    fake_get_json.peers_fail = False
    monkeypatch.setattr(plugin, "_get_json", fake_get_json)
    data = plugin._fetch_dashboard_data("123", {"includeWordcloud": "false", "includeRecords": "false"}, FakeDeviceConfig())

    assert ("/players/123/peers", {"date": 90}) in seen
    assert data["peers"] == [{"name": "Gunnar", "games": 40, "wins": 25}, {"name": "Oh Wonder", "games": 31, "wins": 19}]
    assert data["patch"]["name"] == "7.41f"
    fake_get_json.peers_fail = True
    assert plugin._fetch_dashboard_data("123", {"includeWordcloud": "false", "includeRecords": "false"},
                                        FakeDeviceConfig())["peers"] == []


def test_the_current_patch_is_the_newest_in_valves_list_and_kept_a_day(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"patches": [{"patch_number": "7.41e", "patch_timestamp": 1785394800},
                                {"patch_number": "7.41f", "patch_timestamp": 1789455600},
                                {"patch_number": "7.41d", "patch_timestamp": 1780556400}], "success": True}

    class Session:
        def get(self, url, timeout=None):
            calls.append(url)
            return Response()

    monkeypatch.setattr(dota_module, "get_http_session", lambda: Session())

    assert plugin._current_patch() == {"name": "7.41f", "ts": 1789455600.0}
    assert plugin._current_patch()["name"] == "7.41f" and len(calls) == 1
    assert calls[0] == dota_module.DOTA_PATCH_LIST_URL


def test_recent_game_details_read_as_the_page_prints_them(tmp_path):
    plugin = make_plugin(tmp_path)
    match = {"start_time": 1_000_000, "duration": 1954}

    assert plugin._ago_text(match, now=1_000_000 + 1954 + 2 * 3600) == "2小时前"
    assert plugin._ago_text(match, now=1_000_000 + 1954 + 25 * 60) == "25分钟前"
    assert plugin._ago_text(match, now=1_000_000 + 1954 + 3 * 86400) == "3天前"
    assert plugin._ago_text({}) == ""
    assert (plugin._duration_text(1954), plugin._duration_text(None)) == ("32分", "")
    assert [plugin._party_text(size) for size in (1, 3, None, 9)] == ["单排", "3人组", "", ""]
    assert [plugin._rank_chip_text(rank) for rank in (75, 80, 52, None, 0, 95)] == ["超凡5", "冠绝", "传奇2", "", "", ""]


def test_the_pro_meta_ranks_heroes_by_recent_picks_and_bans(tmp_path):
    plugin = make_plugin(tmp_path)
    heroes = {1: {"id": 1, "localized_name": "赏金猎人", "pro_pick": 3, "pro_ban": 52, "1_pick": 0},
              2: {"id": 2, "localized_name": "德鲁伊", "pro_pick": 8, "pro_ban": 42},
              3: {"id": 3, "localized_name": "冷门", "pro_pick": 0, "pro_ban": 0},
              4: {"id": 4, "localized_name": "坏数据", "pro_pick": "x"}}

    assert [(hero["id"], picks, bans) for hero, picks, bans in plugin._pro_meta(heroes)] == [(1, 3, 52), (2, 8, 42)]
    assert plugin._pro_meta({}) == []


def test_mode_counts_and_hero_rankings_read_opendotas_current_shapes(tmp_path):
    plugin = make_plugin(tmp_path)
    counts = {"game_mode": {"1": {"games": 2862, "win": 1739}, "22": {"games": 4930, "win": 2888}}}
    data = {"hero_stats": {91: {"localized_name": "艾欧"}},
            "rankings": [{"hero_id": 91, "score": 8034.7, "percent_rank": 0.999668983780205}]}

    # Each mode is now {"games", "win"}; reading it no longer breaks the page.
    assert plugin._counts_line(counts) == "game_mode: 22 · 4,930 场"
    assert plugin._first_ranking(data) == "艾欧 前 0.03%"
    assert plugin._first_ranking({"hero_stats": {}, "rankings": [{"hero_id": 14, "rank": 391}]}) == "英雄 14 #391"


def test_the_page_draws_the_new_details_and_stands_without_them(tmp_path):
    plugin = make_plugin(tmp_path)
    full = plugin._sample_payload()
    bare = {**full, "recent_matches": [{key: value for key, value in match.items()
                                        if key not in ("start_time", "duration", "last_hits", "average_rank",
                                                       "party_size")} for match in full["recent_matches"]],
            "peers": [], "patch": {}, "records": {},
            "hero_stats": {key: {k: v for k, v in hero.items() if not k.startswith("pro_")}
                           for key, hero in full["hero_stats"].items()}}

    images = [plugin._render_dashboard(data, (800, 480)) for data in (full, bare)]

    assert all(image.size == (800, 480) for image in images)
    # The lobby's rank chips and the pro meta's bars add colour the bare page lacks.
    colours = [set(image.get_flattened_data()) for image in images]
    assert dota_module.RANK_COLOURS[6] in colours[0] and dota_module.RANK_COLOURS[6] not in colours[1]
    assert (214, 62, 72) in colours[0] and (214, 62, 72) not in colours[1]


def test_dota_dashboard_base_font_uses_shared_resolver(monkeypatch, tmp_path):
    plugin = make_plugin(tmp_path)
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        dota_module,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or sentinel,
        raising=False,
    )

    assert plugin._font(21, bold=True) is sentinel
    assert calls == [(21, True)]
