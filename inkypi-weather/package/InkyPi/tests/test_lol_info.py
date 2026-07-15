import json
import sys
import time
import types
from pathlib import Path

from PIL import Image, ImageDraw

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


def install_import_stubs():
    base_pkg = types.ModuleType("plugins.base_plugin")
    sys.modules.setdefault("plugins.base_plugin", base_pkg)
    base = types.ModuleType("plugins.base_plugin.base_plugin")

    class BasePlugin:
        def __init__(self, config, **_dependencies):
            self.config = config

        def get_plugin_id(self):
            return self.config.get("id")

        def get_plugin_dir(self, path=None):
            plugin_dir = SRC / "plugins" / self.get_plugin_id()
            return str(plugin_dir / path) if path else str(plugin_dir)

        def get_dimensions(self, device_config):
            return device_config.get_resolution()

        def generate_settings_template(self):
            return {"settings_template": "base_plugin/settings.html"}

    base.BasePlugin = BasePlugin
    sys.modules.setdefault("plugins.base_plugin.base_plugin", base)

    context = types.ModuleType("plugins.context_cache")
    context.write_context = lambda *args, **kwargs: None
    sys.modules.setdefault("plugins.context_cache", context)

    http = types.ModuleType("utils.http_client")
    http.get_http_client = lambda: None
    http.get_http_session = lambda: None
    sys.modules.setdefault("utils.http_client", http)

    theme = types.ModuleType("utils.theme_utils")
    theme.get_theme_context = lambda *args, **kwargs: {}
    sys.modules.setdefault("utils.theme_utils", theme)


install_import_stubs()

import plugins.lol_info.lol_info as lol_info_module  # noqa: E402
from plugins.lol_info.lol_info import LoLInfo, STYLE_VERSION  # noqa: E402


class FakeDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"orientation": "horizontal", "theme_mode": "night"}
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, key):
        return ""


def make_plugin(tmp_path):
    plugin = LoLInfo({"id": "lol_info"})
    plugin._cache_dir = lambda: tmp_path
    return plugin


def _theme_context(mode, requested_mode="auto"):
    palettes = {
        "day": {
            "background": (237, 243, 244),
            "panel": (255, 255, 255),
            "ink": (10, 12, 15),
            "muted": (74, 78, 84),
            "rule": (185, 188, 194),
            "accent": (26, 112, 128),
        },
        "night": {
            "background": (8, 20, 22),
            "panel": (0, 0, 0),
            "ink": (255, 255, 255),
            "muted": (194, 196, 202),
            "rule": (46, 48, 56),
            "accent": (101, 189, 201),
        },
    }
    return {
        "requested_mode": requested_mode,
        "mode": mode,
        "source": "weather",
        "reason": "sunrise/sunset",
        "sunrise": "2026-07-13T05:57:00-07:00",
        "sunset": "2026-07-13T20:30:00-07:00",
        "palette": palettes[mode],
    }


def _theme_payload(plugin):
    payload = plugin._sample_payload()
    payload["ranked"] = {}
    payload["matches"] = []
    payload["mastery"] = []
    payload["skin_art_pool"] = []
    payload["summary"] = {}
    return payload


def test_lol_renderer_uses_original_day_and_injected_night_palette(
    tmp_path,
):
    plugin = make_plugin(tmp_path)
    payload = _theme_payload(plugin)
    day_theme = _theme_context("day", requested_mode="day")
    night_theme = _theme_context("night", requested_mode="night")
    auto_theme = _theme_context("night")

    day = plugin._render_dashboard(
        payload,
        (800, 480),
        {"_inkypi_theme": day_theme},
        day_theme,
    )
    night = plugin._render_dashboard(
        payload,
        (800, 480),
        {"_inkypi_theme": night_theme},
        night_theme,
    )
    auto = plugin._render_dashboard(
        payload,
        (800, 480),
        {"_inkypi_theme": auto_theme},
        auto_theme,
    )

    assert day.size == night.size == auto.size == (800, 480)
    assert day.getpixel((420, 10)) == (5, 7, 12)
    assert night.getpixel((420, 10)) == night_theme["palette"]["background"]
    assert day.getpixel((22, 22)) == (236, 232, 206)
    assert night.getpixel((22, 22)) == night_theme["palette"]["rule"]
    assert auto.getpixel((420, 10)) == auto_theme["palette"]["background"]
    assert auto.getpixel((22, 22)) == auto_theme["palette"]["rule"]
    assert day.tobytes() != night.tobytes()


def test_lol_theme_only_render_uses_expired_cache_and_injected_context(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = {
        "_theme_render_only": True,
        "_inkypi_theme": _theme_context("night"),
    }
    dimensions = (800, 480)
    cache_key = plugin._cache_key(settings, dimensions, plugin._identity(settings))
    old_image = plugin._cache_image_path(cache_key)
    Image.new("RGB", dimensions, (1, 2, 3)).save(old_image)
    provider_cache_path = plugin._cache_path(cache_key)
    plugin._write_json(
        provider_cache_path,
        {
            "schema": STYLE_VERSION,
            "updated_ts": 0,
            "identity": plugin._identity(settings),
            "image_path": str(old_image),
            "data": _theme_payload(plugin),
        },
    )
    provider_cache_bytes = provider_cache_path.read_bytes()
    canonical_png_bytes = old_image.read_bytes()
    calls = {"fetch": 0, "context": 0, "render": 0, "write_context": 0}

    def fail_fetch(*_args, **_kwargs):
        calls["fetch"] += 1
        raise AssertionError("theme-only render fetched Riot provider data")

    def fail_context(*_args, **_kwargs):
        calls["context"] += 1
        raise AssertionError("theme-only render ignored injected theme context")

    def fake_render(data, dimensions, settings=None, theme_context=None):
        calls["render"] += 1
        assert data["source"] == "Mock Riot API"
        assert theme_context == settings["_inkypi_theme"]
        return Image.new("RGB", dimensions, theme_context["palette"]["background"])

    def fail_write_context(*_args, **_kwargs):
        calls["write_context"] += 1
        raise AssertionError("theme-only render rewrote provider context")

    plugin._fetch_dashboard_data = fail_fetch
    plugin._render_dashboard = fake_render
    plugin._write_context = fail_write_context
    monkeypatch.setattr(lol_info_module, "get_theme_context", fail_context)

    image = plugin.generate_image(settings, FakeDeviceConfig())

    assert calls == {"fetch": 0, "context": 0, "render": 1, "write_context": 0}
    assert image.getpixel((0, 0)) == settings["_inkypi_theme"]["palette"]["background"]
    assert provider_cache_path.read_bytes() == provider_cache_bytes
    assert old_image.read_bytes() == canonical_png_bytes


def test_mock_generate_image_renders_branded_dashboard(tmp_path):
    plugin = make_plugin(tmp_path)

    image = plugin.generate_image({"useMockData": "true"}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    generated = list(tmp_path.glob("*.png"))
    assert generated
    cache_files = list(tmp_path.glob("*.json"))
    assert cache_files
    cached_image_paths = []
    for path in cache_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("image_path"):
            cached_image_paths.append(Path(payload["image_path"]))
    assert any(path in generated for path in cached_image_paths)


def test_riot_logo_light_background_is_removed(tmp_path):
    plugin = make_plugin(tmp_path)
    raw = Image.new("RGBA", (4, 2), (255, 255, 255, 255))
    raw.putpixel((1, 1), (20, 20, 20, 255))

    cleaned = plugin._remove_light_background(raw)

    assert cleaned.getpixel((0, 0))[3] == 0
    assert cleaned.getpixel((1, 1))[3] == 255


def test_asset_logos_are_available(tmp_path):
    plugin = make_plugin(tmp_path)

    lol_logo = plugin._asset_logo("league-of-legends-logo.png", (100, 42))
    riot_logo = plugin._asset_logo("riot-games-logo.png", (90, 28), tint=(236, 82, 78), remove_light=True)

    assert lol_logo is not None
    assert riot_logo is not None
    assert lol_logo.width <= 100 and lol_logo.height <= 42
    assert riot_logo.width <= 90 and riot_logo.height <= 28


def test_hangul_riot_id_uses_font_with_korean_glyphs(tmp_path):
    plugin = make_plugin(tmp_path)
    base_font = plugin._font(15)
    hangul_text = "BLG " + chr(0xC628)

    hangul_font = plugin._font_for_text(base_font, hangul_text)

    assert plugin._contains_hangul(hangul_text) is True
    assert Path(getattr(hangul_font, "path", "")).name == "LXGWWenKai-Regular.ttf"


def test_lol_normal_font_uses_shared_resolver(monkeypatch, tmp_path):
    plugin = make_plugin(tmp_path)
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        lol_info_module,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or sentinel,
        raising=False,
    )

    assert plugin._font(19, bold=True) is sentinel
    assert calls == [(19, True)]


def test_lol_hangul_font_falls_back_when_shared_font_lacks_glyphs(
    monkeypatch, tmp_path
):
    plugin = make_plugin(tmp_path)
    shared_font = object()
    hangul_font = object()
    calls = []
    monkeypatch.setattr(
        lol_info_module,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or shared_font,
        raising=False,
    )
    monkeypatch.setattr(
        plugin,
        "_font_supports_text",
        lambda font, text: font is hangul_font,
        raising=False,
    )
    monkeypatch.setattr(
        lol_info_module.Path,
        "exists",
        lambda path: "NotoSansCJK" in str(path),
    )
    monkeypatch.setattr(
        lol_info_module.ImageFont,
        "truetype",
        lambda path, size: hangul_font,
    )

    assert plugin._font(16, prefer_hangul=True) is hangul_font
    assert calls == [(16, False)]


def test_pro_accounts_parse_default_pool_with_korean_names(tmp_path):
    plugin = make_plugin(tmp_path)

    accounts = plugin._pro_accounts({"proAccounts": lol_info_module.DEFAULT_PRO_ACCOUNTS_TEXT})

    assert [account["label"] for account in accounts] == ["Faker", "Bin", "ShowMaker", "Chovy"]
    assert accounts[1]["gameName"] == "BLG " + chr(0xC628)
    assert accounts[3]["gameName"] == chr(0xD5C8) + chr(0xAC70) + chr(0xB369)
    assert all(account["platformRoute"] == "kr" for account in accounts)
    assert all(account["regionalRoute"] == "asia" for account in accounts)


def test_pro_account_rotation_random_queue_removes_displayed_accounts(tmp_path):
    plugin = make_plugin(tmp_path)
    settings = {
        "proAccounts": "\n".join([
            "A|Alpha|KR1|kr|asia",
            "B|Beta|KR1|kr|asia",
            "C|Gamma|KR1|kr|asia",
        ])
    }

    selected = [plugin._select_pro_account(settings)["label"] for _ in range(3)]

    assert set(selected) == {"A", "B", "C"}
    assert len(selected) == len(set(selected))
    state_path = next(tmp_path.glob("pro_account_rotation_*.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["queue"] == []
    next_selected = plugin._select_pro_account(settings)
    assert next_selected["label"] in {"A", "B", "C"}
    refreshed_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(refreshed_state["queue"]) == 2


def test_selected_pro_account_settings_override_single_riot_id(tmp_path):
    plugin = make_plugin(tmp_path)
    settings = {
        "gameName": "Old",
        "tagLine": "NA1",
        "platformRoute": "na1",
        "regionalRoute": "americas",
        "proAccounts": "Chovy|" + chr(0xD5C8) + chr(0xAC70) + chr(0xB369) + "|0303|kr|asia",
    }

    effective = plugin._settings_for_selected_pro_account(settings)

    assert effective["gameName"] == chr(0xD5C8) + chr(0xAC70) + chr(0xB369)
    assert effective["tagLine"] == "0303"
    assert effective["platformRoute"] == "kr"
    assert effective["regionalRoute"] == "asia"
    assert effective["_proAccountLabel"] == "Chovy"


def test_recent_summary_calculates_metrics(tmp_path):
    plugin = make_plugin(tmp_path)

    summary = plugin._recent_summary([
        {"kills": 6, "deaths": 2, "assists": 4, "win": True, "duration": 1800, "cs": 210, "kp": 60},
        {"kills": 2, "deaths": 4, "assists": 8, "win": False, "duration": 1200, "cs": 90, "kp": 50},
    ])

    assert summary["games"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert round(summary["kda"], 2) == 3.33
    assert summary["cs_per_min"] == 6


def test_champion_full_name_combines_title_and_name(tmp_path):
    plugin = make_plugin(tmp_path)

    label = plugin._champion_full_name_from_match(
        {"champion_id": 201, "champion_name": "布隆"},
        {
            "by_key": {
                "201": {
                    "id": "Braum",
                    "name": "弗雷尔卓德之心",
                    "title": "布隆",
                    "icon_url": "",
                }
            },
            "by_id": {},
        },
    )

    assert label == "弗雷尔卓德之心 布隆"


def test_match_history_status_marks_stale_match_v5_data(tmp_path):
    plugin = make_plugin(tmp_path)
    latest = 1_000_000
    revision = latest + 15 * 24 * 60 * 60 * 1000

    status = plugin._match_history_status(
        [{"timestamp": latest}],
        {"revisionDate": revision},
    )

    assert status["stale"] is True
    assert status["latest_match_ts"] == latest
    assert status["summoner_revision_ts"] == revision


def test_match_history_status_keeps_recent_match_v5_data(tmp_path):
    plugin = make_plugin(tmp_path)
    latest = 1_000_000
    revision = latest + 2 * 24 * 60 * 60 * 1000

    status = plugin._match_history_status(
        [{"timestamp": latest}],
        {"revisionDate": revision},
    )

    assert status["stale"] is False


def test_local_match_history_merges_ahead_of_match_v5_matches(tmp_path):
    plugin = make_plugin(tmp_path)
    history_path = tmp_path / "league_client_matches.json"
    history_path.write_text(json.dumps({
        "matches": [
            {
                "match_id": "LCU_20260610",
                "champion_id": 32,
                "champion_name": "Amumu",
                "kills": 4,
                "deaths": 13,
                "assists": 21,
                "win": "Fail",
                "lane": "ARAM",
                "queueId": 450,
                "timestamp": 1781132529188,
                "duration": 902,
                "cs": 28,
                "gold": 11914,
                "damage": 18000,
                "kp": 56,
            }
        ]
    }), encoding="utf-8")
    stale = [
        {
            "match_id": "NA1_5161178880",
            "champion_id": 115,
            "champion_name": "Ziggs",
            "kills": 7,
            "deaths": 9,
            "assists": 25,
            "win": True,
            "lane": "ARAM",
            "queue": "ARAM",
            "timestamp": 1732338189188,
            "duration": 1435,
            "cs": 64,
            "gold": 15667,
            "damage": 24000,
            "kp": 60,
        }
    ]

    local = plugin._local_match_summaries(
        {"localMatchHistoryPath": str(history_path)},
        {"puuid": "test-puuid"},
        {"by_key": {"32": {"id": "Amumu", "name": "Amumu", "icon_url": "amumu.png"}}},
    )
    merged = plugin._merge_match_summaries(local, stale, 5)

    assert [row["match_id"] for row in merged] == ["LCU_20260610", "NA1_5161178880"]
    assert merged[0]["source"] == "local_lcu"
    assert merged[0]["win"] is False
    assert merged[0]["champion_key"] == "Amumu"


def test_lcu_raw_match_history_payload_normalizes_player_stats(tmp_path):
    plugin = make_plugin(tmp_path)
    history_path = tmp_path / "league_client_matches.json"
    history_path.write_text(json.dumps({
        "puuid": "player-puuid",
        "games": {
            "games": {
                "games": [
                    {
                        "gameId": 123456,
                        "queueId": 2400,
                        "gameCreation": 1781131619000,
                        "gameDuration": 902,
                        "participants": [
                            {
                                "participantId": 1,
                                "teamId": 100,
                                "championId": 32,
                                "stats": {
                                    "kills": 4,
                                    "deaths": 13,
                                    "assists": 21,
                                    "win": "Fail",
                                    "totalMinionsKilled": 26,
                                    "neutralMinionsKilled": 2,
                                    "goldEarned": 11914,
                                    "totalDamageDealtToChampions": 18000,
                                },
                                "timeline": {"lane": "MIDDLE"},
                            },
                            {
                                "participantId": 2,
                                "teamId": 100,
                                "championId": 81,
                                "stats": {"kills": 6, "assists": 9},
                            },
                        ],
                        "participantIdentities": [
                            {"participantId": 1, "player": {"puuid": "player-puuid"}},
                            {"participantId": 2, "player": {"puuid": "other-puuid"}},
                        ],
                    }
                ]
            }
        },
    }), encoding="utf-8")

    matches = plugin._local_match_summaries(
        {"localMatchHistoryPath": str(history_path)},
        {"puuid": "player-puuid"},
        {"by_key": {"32": {"id": "Amumu", "name": "Amumu", "icon_url": "amumu.png"}}},
    )

    assert len(matches) == 1
    assert matches[0]["match_id"] == "123456"
    assert matches[0]["kills"] == 4
    assert matches[0]["deaths"] == 13
    assert matches[0]["assists"] == 21
    assert matches[0]["win"] is False
    assert matches[0]["cs"] == 28
    assert matches[0]["gold"] == 11914
    assert matches[0]["queue"] == "大混战"
    assert round(matches[0]["kp"], 1) == 100.0
    assert matches[0]["timestamp"] == 1781132521000


def test_local_match_history_ignores_payload_for_different_account(tmp_path):
    plugin = make_plugin(tmp_path)
    history_path = tmp_path / "league_client_matches.json"
    history_path.write_text(json.dumps({
        "puuid": "old-puuid",
        "summoner": {"gameName": "Old Player", "tagLine": "NA1", "puuid": "old-puuid"},
        "games": {
            "games": {
                "games": [
                    {
                        "gameId": 222,
                        "queueId": 420,
                        "gameCreation": 1781131619000,
                        "gameDuration": 1800,
                        "participants": [
                            {
                                "participantId": 1,
                                "teamId": 100,
                                "championId": 32,
                                "stats": {"kills": 9, "deaths": 1, "assists": 3, "win": "Win"},
                            }
                        ],
                        "participantIdentities": [
                            {"participantId": 1, "player": {"puuid": "old-puuid"}},
                        ],
                    }
                ]
            }
        },
    }), encoding="utf-8")

    matches = plugin._local_match_summaries(
        {"localMatchHistoryPath": str(history_path)},
        {"puuid": "current-puuid", "gameName": "Current Player", "tagLine": "KR1"},
        {"by_key": {"32": {"id": "Amumu", "name": "Amumu", "icon_url": "amumu.png"}}},
    )

    assert matches == []


def test_local_match_history_filters_flat_rows_for_current_account(tmp_path):
    plugin = make_plugin(tmp_path)
    history_path = tmp_path / "league_client_matches.json"
    history_path.write_text(json.dumps({
        "matches": [
            {
                "match_id": "OLD_1",
                "puuid": "old-puuid",
                "champion_id": 32,
                "kills": 9,
                "deaths": 1,
                "assists": 3,
                "win": True,
                "timestamp": 1781131619000,
                "duration": 1800,
            },
            {
                "match_id": "KR_1",
                "puuid": "current-puuid",
                "champion_id": 81,
                "kills": 2,
                "deaths": 4,
                "assists": 8,
                "win": False,
                "timestamp": 1781132529000,
                "duration": 1200,
            },
        ]
    }), encoding="utf-8")

    matches = plugin._local_match_summaries(
        {"localMatchHistoryPath": str(history_path)},
        {"puuid": "current-puuid", "gameName": "Current Player", "tagLine": "KR1"},
        {"by_key": {
            "32": {"id": "Amumu", "name": "Amumu", "icon_url": "amumu.png"},
            "81": {"id": "Ezreal", "name": "Ezreal", "icon_url": "ezreal.png"},
        }},
    )

    assert [row["match_id"] for row in matches] == ["KR_1"]
    assert matches[0]["champion_key"] == "Ezreal"

def test_write_context_includes_local_match_metrics(tmp_path):
    plugin = make_plugin(tmp_path)
    captured = {}
    original = lol_info_module.write_context

    def fake_write_context(plugin_id, payload, **kwargs):
        captured["plugin_id"] = plugin_id
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        return True

    lol_info_module.write_context = fake_write_context
    try:
        plugin._write_context(
            {
                "account": {"gameName": "Test", "tagLine": "NA1"},
                "ranked": {},
                "summary": {"games": 5, "wins": 2, "losses": 3, "kda": 2.89, "winrate": 40.0},
                "matches": [
                    {
                        "champion_name": "Amumu",
                        "kills": 4,
                        "deaths": 13,
                        "assists": 21,
                        "source": "local_lcu",
                    }
                ],
                "match_source_counts": {"local_lcu": 1, "match_v5": 4, "total": 5},
                "source": "Riot API + 本机记录",
                "active_game": None,
            },
            time.time(),
            120,
        )
    finally:
        lol_info_module.write_context = original

    payload = captured["payload"]
    assert payload["source"] == "Riot API + 本机记录"
    assert payload["recent_games"] == 5
    assert payload["recent_kda"] == 2.89
    assert payload["recent_winrate"] == 40.0
    assert payload["local_match_count"] == 1
    assert payload["match_v5_count"] == 4
    assert "最近一局 Amumu: 4/13/21" in payload["summary"]


def test_featured_champions_combines_mastery_and_recent_usage(tmp_path):
    plugin = make_plugin(tmp_path)

    featured = plugin._featured_champions(
        [
            {"champion_key": "Ahri", "champion_name": "阿狸", "champion_icon": "", "points": 500000},
            {"champion_key": "Yasuo", "champion_name": "亚索", "champion_icon": "", "points": 200000},
        ],
        [
            {"champion_key": "Riven", "champion_name": "锐雯", "champion_icon": ""},
            {"champion_key": "Riven", "champion_name": "锐雯", "champion_icon": ""},
            {"champion_key": "Ahri", "champion_name": "阿狸", "champion_icon": ""},
        ],
    )

    by_key = {item["champion_key"]: item for item in featured}
    assert set(by_key) == {"Ahri", "Riven", "Yasuo"}
    assert by_key["Ahri"]["mastery_points"] == 500000
    assert by_key["Riven"]["recent_games"] == 2


def test_skin_art_pool_uses_non_chroma_skin_splash_urls(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin._dragon_champion_detail = lambda champion_key, version: {
        "skins": [
            {"name": "default", "num": 0},
            {"name": "Star Guardian Ahri", "num": 1},
            {"name": "Star Guardian Ahri Chroma", "num": 2, "parentSkin": 1},
        ]
    }

    pool = plugin._skin_art_pool(
        [{"champion_key": "Ahri", "champion_name": "阿狸", "mastery_points": 1000, "recent_games": 2}],
        {"version": "16.11.1"},
    )

    assert [item["id"] for item in pool] == ["Ahri:1"]
    assert pool[0]["splash_url"].endswith("/cdn/img/champion/splash/Ahri_1.jpg")
    assert pool[0]["loading_url"].endswith("/cdn/img/champion/loading/Ahri_1.jpg")


def test_skin_art_pool_can_use_configured_owned_skin_ids(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin._communitydragon_skins = lambda **_kwargs: [
        {
            "id": 103001,
            "championId": 103,
            "name": "Owned Ahri",
            "releaseDate": "2011-12-14",
            "uncenteredSplashPath": "/lol-game-data/assets/ASSETS/Characters/Ahri/Skins/Skin01/Images/Ahri_Splash_Uncentered_1.jpg",
            "loadScreenPath": "/lol-game-data/assets/ASSETS/Characters/Ahri/Skins/Skin01/AhriLoadscreen_1.jpg",
        },
        {
            "id": 92002,
            "championId": 92,
            "name": "Owned Riven",
            "releaseDate": "2012-03-01",
            "uncenteredSplashPath": "/lol-game-data/assets/ASSETS/Characters/Riven/Skins/Skin02/Images/Riven_Splash_Uncentered_2.jpg",
        },
        {
            "id": 157001,
            "championId": 157,
            "name": "Not Owned Yasuo",
            "releaseDate": "2013-12-13",
        },
    ]

    pool = plugin._skin_art_pool(
        [],
        {
            "version": "16.11.1",
            "by_key": {
                "103": {"id": "Ahri", "name": "Ahri"},
                "92": {"id": "Riven", "name": "Riven"},
                "157": {"id": "Yasuo", "name": "Yasuo"},
            },
        },
        {"ownedSkinIds": "103001, Riven:2", "includeLatestSkins": "false"},
    )

    assert [item["id"] for item in pool] == ["Ahri:1", "Riven:2", "Yasuo:1"]
    assert [item["pool_source"] for item in pool] == ["owned", "owned", "catalog"]
    assert pool[0]["splash_url"].endswith("/assets/characters/ahri/skins/skin01/images/ahri_splash_uncentered_1.jpg")


def test_owned_skin_manual_fallback_keeps_canonical_champion_key(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin._communitydragon_skins = lambda **_kwargs: []

    pool = plugin._skin_art_pool(
        [],
        {
            "version": "16.11.1",
            "by_id": {
                "riven": {"id": "Riven", "name": "Riven"},
            },
        },
        {"ownedSkinIds": "riven:2", "includeLatestSkins": "false"},
    )

    assert [item["id"] for item in pool] == ["Riven:2"]
    assert pool[0]["splash_url"].endswith("/cdn/img/champion/splash/Riven_2.jpg")


def test_skin_art_pool_adds_latest_skins_by_release_date(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin._communitydragon_skins = lambda **_kwargs: [
        {"id": 103001, "championId": 103, "name": "Old Ahri", "releaseDate": "2011-12-14"},
        {"id": 92002, "championId": 92, "name": "New Riven", "releaseDate": "2026-06-01"},
        {"id": 157001, "name": "Newest Yasuo", "releaseDate": "2026-06-03"},
    ]

    pool = plugin._skin_art_pool(
        [],
        {
            "version": "16.11.1",
            "by_key": {
                "103": {"id": "Ahri", "name": "Ahri"},
                "92": {"id": "Riven", "name": "Riven"},
                "157": {"id": "Yasuo", "name": "Yasuo"},
            },
        },
        {"includeLatestSkins": "true", "latestSkinCount": "2"},
    )

    assert [item["id"] for item in pool] == ["Yasuo:1", "Riven:2", "Ahri:1"]
    assert [item["pool_source"] for item in pool] == ["latest", "latest", "catalog"]


def test_skin_art_pool_includes_full_catalog_even_without_latest_priority(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin._communitydragon_skins = lambda **_kwargs: [
        {"id": 103001, "championId": 103, "name": "Old Ahri", "releaseDate": "2011-12-14"},
        {"id": 92002, "championId": 92, "name": "New Riven", "releaseDate": "2026-06-01"},
        {"id": 157001, "championId": 157, "name": "Newest Yasuo", "releaseDate": "2026-06-03"},
    ]

    pool = plugin._skin_art_pool(
        [],
        {
            "version": "16.11.1",
            "by_key": {
                "103": {"id": "Ahri", "name": "Ahri"},
                "92": {"id": "Riven", "name": "Riven"},
                "157": {"id": "Yasuo", "name": "Yasuo"},
            },
        },
        {"includeLatestSkins": "false"},
    )

    assert [item["id"] for item in pool] == ["Yasuo:1", "Riven:2", "Ahri:1"]
    assert all(item["pool_source"] == "catalog" for item in pool)

def test_skin_art_pool_dedupes_owned_and_latest_skins(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin._communitydragon_skins = lambda **_kwargs: [
        {"id": 103001, "championId": 103, "name": "Ahri Skin", "releaseDate": "2026-06-03"},
        {"id": 92002, "championId": 92, "name": "Riven Skin", "releaseDate": "2026-06-01"},
    ]

    pool = plugin._skin_art_pool(
        [],
        {
            "version": "16.11.1",
            "by_key": {
                "103": {"id": "Ahri", "name": "Ahri"},
                "92": {"id": "Riven", "name": "Riven"},
            },
        },
        {"ownedSkinIds": "Ahri:1", "includeLatestSkins": "true", "latestSkinCount": "2"},
    )

    assert [item["id"] for item in pool] == ["Ahri:1", "Riven:2"]
    assert pool[0]["pool_source"] == "owned"


def test_overview_layout_places_art_large_on_right_and_logo_before_it(tmp_path):
    plugin = make_plugin(tmp_path)

    content_x1, logo_box, art_box = plugin._overview_layout((22, 280, 778, 456))

    assert content_x1 < logo_box[0]
    assert logo_box[2] < art_box[0]
    assert 340 <= logo_box[1] <= 360
    assert art_box[0] >= 490
    assert art_box[2] == 766
    assert art_box[3] == 444


def test_overview_draws_selected_skin_name_text_below_riot_logo(tmp_path):
    plugin = make_plugin(tmp_path)
    image = Image.new("RGB", (800, 480), (5, 7, 12))
    draw = ImageDraw.Draw(image)
    fonts = {
        "title": plugin._font(25, bold=True),
        "section": plugin._font(20, bold=True),
        "body": plugin._font(15),
        "small": plugin._font(13),
        "skin_label": plugin._font(14),
        "tiny": plugin._font(10),
        "micro": plugin._font(9),
    }
    selected = {
        "id": "Ahri:1",
        "skin_name": "Arcade Ahri",
        "champion_name": "Ahri",
        "splash_url": "https://example.test/ahri.jpg",
    }
    plugin._choose_skin_art = lambda _data: selected
    plugin._image_from_url = lambda _url, _label="": Image.new("RGB", (160, 90), (24, 180, 240))
    seen = {}
    original_single = plugin._single

    def record_single(draw_obj, position, text, font, fill, max_width, min_size=8):
        if text == "Arcade Ahri":
            seen["skin_label"] = {"position": position, "font_size": getattr(font, "size", None), "min_size": min_size}
        return original_single(draw_obj, position, text, font, fill, max_width, min_size)

    plugin._single = record_single
    box = (22, 280, 778, 456)
    _content_x1, logo_box, _art_box = plugin._overview_layout(box)

    plugin._draw_overview(
        image,
        draw,
        {"summary": {"games": 5}, "skin_art_pool": [selected]},
        box,
        fonts,
        (255, 250, 222),
        (202, 190, 150),
        (255, 205, 54),
        (82, 202, 128),
        (107, 204, 255),
        (255, 82, 74),
    )

    assert seen["skin_label"]["font_size"] == 14
    assert seen["skin_label"]["min_size"] == 8
    assert seen["skin_label"]["position"][0] > logo_box[0]
    assert seen["skin_label"]["position"][1] > logo_box[3]
    assert image.getpixel((logo_box[0] + 7, logo_box[3] + 22)) != (255, 82, 74)


def test_skin_art_choice_rotates_without_immediate_repeat(tmp_path):
    plugin = make_plugin(tmp_path)
    data = {
        "account": {"puuid": "rotation-test"},
        "skin_art_pool": [
            {"id": "Ahri:1", "splash_url": "https://example.test/Ahri_1.jpg"},
            {"id": "Riven:2", "splash_url": "https://example.test/Riven_2.jpg"},
        ],
    }

    first = plugin._choose_skin_art(data)
    second = plugin._choose_skin_art(data)

    assert first["id"] != second["id"]


def test_skin_art_choice_rotates_through_existing_pool_in_order(tmp_path):
    plugin = make_plugin(tmp_path)
    data = {
        "account": {"puuid": "ordered-rotation-test"},
        "skin_art_pool": [
            {"id": "Ahri:1", "splash_url": "https://example.test/Ahri_1.jpg"},
            {"id": "Riven:2", "splash_url": "https://example.test/Riven_2.jpg"},
            {"id": "Yasuo:3", "splash_url": "https://example.test/Yasuo_3.jpg"},
        ],
    }

    selected = [plugin._choose_skin_art(data)["id"] for _ in range(4)]

    assert selected == ["Ahri:1", "Riven:2", "Yasuo:3", "Ahri:1"]


def test_skin_art_choice_restarts_when_catalog_pool_changes(tmp_path):
    plugin = make_plugin(tmp_path)
    data = {
        "account": {"puuid": "catalog-change-test"},
        "skin_art_pool": [
            {"id": "Ahri:1", "splash_url": "https://example.test/Ahri_1.jpg"},
            {"id": "Riven:2", "splash_url": "https://example.test/Riven_2.jpg"},
        ],
    }

    first = plugin._choose_skin_art(data)
    data["skin_art_pool"] = [
        {"id": "Mel:1", "splash_url": "https://example.test/Mel_1.jpg"},
        {"id": "Ahri:1", "splash_url": "https://example.test/Ahri_1.jpg"},
        {"id": "Riven:2", "splash_url": "https://example.test/Riven_2.jpg"},
    ]
    second = plugin._choose_skin_art(data)

    assert first["id"] == "Ahri:1"
    assert second["id"] == "Mel:1"


def test_rank_emblem_url_uses_communitydragon_shared_image(tmp_path):
    plugin = make_plugin(tmp_path)

    url = plugin._rank_emblem_url({"tier": "GRANDMASTER"})

    assert url == "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-shared-components/global/default/images/grandmaster.png"
    assert plugin._rank_emblem_url({"tier": "UNRANKED"}) == ""


def test_rank_mastery_draws_rank_emblem_with_exact_rank_label_below_icon(tmp_path):
    plugin = make_plugin(tmp_path)
    image = Image.new("RGB", (800, 480), (5, 7, 12))
    draw = ImageDraw.Draw(image)
    fonts = {
        "title": plugin._font(25, bold=True),
        "section": plugin._font(20, bold=True),
        "body": plugin._font(15),
        "small": plugin._font(13),
        "skin_label": plugin._font(14),
        "tiny": plugin._font(10),
        "micro": plugin._font(9),
    }
    ranked = {"tier": "GRANDMASTER", "rank": "I", "leaguePoints": 1684, "wins": 274, "losses": 227}
    rank_label = plugin._rank_text(ranked)
    seen = []
    original_single = plugin._single

    def record_single(draw_obj, position, text, font, fill, max_width, min_size=8):
        seen.append({"text": str(text), "position": position, "font_size": getattr(font, "size", None), "max_width": max_width, "min_size": min_size})
        return original_single(draw_obj, position, text, font, fill, max_width, min_size)

    plugin._single = record_single
    plugin._rank_emblem_image = lambda _ranked, size: Image.new("RGBA", (size, size), (14, 188, 214, 255))

    plugin._draw_rank_mastery(
        image,
        draw,
        {"ranked": ranked, "mastery": []},
        (602, 22, 778, 264),
        fonts,
        (255, 250, 222),
        (202, 190, 150),
        (255, 205, 54),
        (82, 202, 128),
        (107, 204, 255),
    )

    rank_record = next(item for item in seen if item["text"] == rank_label)
    assert rank_record["position"] == (614, 140)
    assert rank_record["font_size"] == 10
    assert rank_record["max_width"] == 74
    assert rank_record["min_size"] == 7
    assert any(item["text"] == "1684 LP" for item in seen)
    assert image.getpixel((618, 68)) == (14, 188, 214)
def test_valid_data_cache_still_rerenders_image_without_refetch(tmp_path):
    plugin = make_plugin(tmp_path)
    settings = {}
    dimensions = (800, 480)
    cache_key = plugin._cache_key(settings, dimensions, plugin._identity(settings))
    old_image = tmp_path / "old.png"
    Image.new("RGB", dimensions, (1, 2, 3)).save(old_image)
    plugin._write_json(plugin._cache_path(cache_key), {
        "schema": STYLE_VERSION,
        "updated_ts": time.time(),
        "identity": plugin._identity(settings),
        "image_path": str(old_image),
        "data": plugin._sample_payload(),
    })

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("cached data should avoid Riot API calls")

    render_calls = []

    def fake_render(data, dimensions, settings=None, theme_context=None):
        render_calls.append(data)
        return Image.new("RGB", dimensions, (9, 8, 7))

    plugin._fetch_dashboard_data = fail_fetch
    plugin._render_dashboard = fake_render

    image = plugin.generate_image(settings, FakeDeviceConfig())

    assert len(render_calls) == 1
    assert image.getpixel((0, 0)) == (9, 8, 7)
