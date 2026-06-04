import sys
import time
import types
from pathlib import Path

from PIL import Image

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

        def generate_settings_template(self):
            return {"settings_template": "base_plugin/settings.html"}

    base.BasePlugin = BasePlugin
    sys.modules.setdefault("plugins.base_plugin.base_plugin", base)

    context = types.ModuleType("plugins.context_cache")
    context.write_context = lambda *args, **kwargs: None
    sys.modules.setdefault("plugins.context_cache", context)

    http = types.ModuleType("utils.http_client")
    http.get_http_session = lambda: None
    sys.modules.setdefault("utils.http_client", http)

    theme = types.ModuleType("utils.theme_utils")
    theme.get_theme_context = lambda *args, **kwargs: {}
    sys.modules.setdefault("utils.theme_utils", theme)


install_import_stubs()

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


def test_mock_generate_image_renders_branded_dashboard(tmp_path):
    plugin = make_plugin(tmp_path)

    image = plugin.generate_image({"useMockData": "true"}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    generated = list(tmp_path.glob("*.png"))
    assert generated
    assert any(path.name.startswith("image_") for path in generated)


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


def test_overview_layout_places_art_large_on_right_and_logo_before_it(tmp_path):
    plugin = make_plugin(tmp_path)

    content_x1, logo_box, art_box = plugin._overview_layout((22, 280, 778, 456))

    assert content_x1 < logo_box[0]
    assert logo_box[2] < art_box[0]
    assert 340 <= logo_box[1] <= 360
    assert art_box[0] >= 490
    assert art_box[2] == 766
    assert art_box[3] == 444


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
