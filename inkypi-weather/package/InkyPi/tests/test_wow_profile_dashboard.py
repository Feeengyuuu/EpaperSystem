import sys
import types
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
TEST_CACHE_ROOT = SRC.parents[3] / ".tmp" / "wow_profile_dashboard_tests"


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


install_import_stubs()

from config import Config  # noqa: E402
from plugins.wow_profile_dashboard.wow_profile_dashboard import WowProfileDashboard  # noqa: E402


def test_dashboard_background_interior_stays_flat_for_color_epaper():
    plugin = WowProfileDashboard({"id": "wow_profile_dashboard"})
    palette = {"bg": (8, 9, 12), "pattern": (24, 26, 31)}
    image = Image.new("RGB", (96, 64), palette["bg"])

    plugin._draw_background(ImageDraw.Draw(image), image.width, image.height, palette)

    interior = image.crop((4, 4, image.width - 4, image.height - 4))
    assert interior.getcolors(maxcolors=interior.width * interior.height) == [
        (interior.width * interior.height, palette["bg"])
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


class FakeResponse:
    def __init__(self, payload=None, content=b"", status_code=200):
        self.payload = payload or {}
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.posts = []
        self.gets = []

    def post(self, url, data=None, auth=None, timeout=None):
        self.posts.append({"url": url, "data": data or {}, "auth": auth, "timeout": timeout})
        return FakeResponse({"access_token": "public-token", "expires_in": 3600})

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append({"url": url, "params": params or {}, "headers": headers or {}, "timeout": timeout})
        if url.endswith("/profile/wow/character/area-52/testmage"):
            return FakeResponse({
                "name": "Testmage",
                "level": 80,
                "realm": {"name": "Area 52", "slug": "area-52"},
                "race": {"name": "Human"},
                "character_class": {"name": "Mage"},
                "active_spec": {"name": "Frost"},
                "faction": {"name": "Alliance"},
                "achievement_points": 12345,
                "average_item_level": 640,
                "equipped_item_level": 638,
                "last_login_timestamp": 1760000000000,
            })
        if url.endswith("/character-media"):
            return FakeResponse({"assets": []})
        if url.endswith("/equipment"):
            return FakeResponse({
                "equipped_items": [
                    {"slot": {"name": "Head"}, "name": "Arcane Crown", "level": {"value": 645}, "quality": {"type": "EPIC"}},
                    {"slot": {"name": "Weapon"}, "name": "Practice Staff", "level": {"value": 650}, "quality": {"type": "RARE"}},
                ]
            })
        if url.endswith("/mythic-keystone-profile"):
            return FakeResponse({
                "current_mythic_rating": {"rating": 2450.5},
                "current_period": {
                    "best_runs": [
                        {"dungeon": {"name": "Ara-Kara"}, "keystone_level": 10, "score": {"rating": 333.2}}
                    ]
                },
            })
        if url.endswith("/pvp-summary"):
            return FakeResponse({"brackets": [{"bracket": {"type": "ARENA_3v3"}, "rating": 1600}]})
        if url.endswith("/profile/user/wow"):
            return FakeResponse({
                "wow_accounts": [{
                    "characters": [
                        {
                            "name": "Lowbie",
                            "level": 12,
                            "realm": {"name": "Stormrage", "slug": "stormrage"},
                            "playable_class": {"name": "Priest"},
                            "playable_race": {"name": "Dwarf"},
                            "faction": {"name": "Alliance"},
                        },
                        {
                            "name": "Testmage",
                            "level": 80,
                            "realm": {"name": "Area 52", "slug": "area-52"},
                            "playable_class": {"name": "Mage"},
                            "playable_race": {"name": "Human"},
                            "faction": {"name": "Alliance"},
                        },
                    ]
                }]
            })
        return FakeResponse({})


def cache_dir_for(name):
    path = TEST_CACHE_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_plugin(cache_name):
    plugin = WowProfileDashboard({"id": "wow_profile_dashboard"})
    plugin._cache_dir = lambda: cache_dir_for(cache_name)
    return plugin


def test_config_accepts_common_blizzard_key_aliases(monkeypatch):
    env_path = cache_dir_for("config") / "blizzard.env"
    env_path.write_text("WoW_Key=id-value\nWOW_CLIENT_SECRET=secret-value\nWOW_PROFILE_ACCESS_TOKEN=user-token\n", encoding="utf-8")
    for key in [
        "BLIZZARD_CLIENT_ID",
        "BNET_CLIENT_ID",
        "BLIZZARD_CLIENT_SECRET",
        "WOW_CLIENT_SECRET",
        "BLIZZARD_USER_ACCESS_TOKEN",
        "WOW_PROFILE_ACCESS_TOKEN",
    ]:
        monkeypatch.delenv(key, raising=False)

    config = Config.__new__(Config)
    monkeypatch.setattr(config, "_env_file_candidates", lambda: [str(env_path)])

    assert config.load_env_key("BLIZZARD_CLIENT_ID") == "id-value"
    assert config.load_env_key("BLIZZARD_CLIENT_SECRET") == "secret-value"
    assert config.load_env_key("BLIZZARD_USER_ACCESS_TOKEN") == "user-token"


def test_client_credentials_token_uses_us_battle_net_oauth(monkeypatch):
    plugin = make_plugin("oauth")
    session = FakeSession()
    monkeypatch.setattr("plugins.wow_profile_dashboard.wow_profile_dashboard.get_http_session", lambda: session)

    token = plugin._client_credentials_token({}, FakeDeviceConfig(env={
        "BLIZZARD_CLIENT_ID": "client-id",
        "BLIZZARD_CLIENT_SECRET": "client-secret",
    }), "us")

    assert token == "public-token"
    assert session.posts[0]["url"] == "https://us.battle.net/oauth/token"
    assert session.posts[0]["data"] == {"grant_type": "client_credentials"}
    assert session.posts[0]["auth"] == ("client-id", "client-secret")


def test_fetch_character_dashboard_shapes_public_profile(monkeypatch):
    plugin = make_plugin("character")
    session = FakeSession()
    monkeypatch.setattr("plugins.wow_profile_dashboard.wow_profile_dashboard.get_http_session", lambda: session)

    data = plugin._fetch_dashboard_data(
        {"region": "us", "realmSlug": "Area 52", "characterName": "TestMage"},
        FakeDeviceConfig(env={"BLIZZARD_CLIENT_ID": "client-id", "BLIZZARD_CLIENT_SECRET": "client-secret"}),
    )

    assert data["title"] == "Testmage"
    assert data["region"] == "US"
    assert data["equipped_item_level"] == 638
    assert data["equipment"][0]["name"] == "Practice Staff"
    assert data["mythic_rating"] == 2450.5
    assert all(call["params"]["namespace"] == "profile-us" for call in session.gets if "api.blizzard.com" in call["url"])
    assert all(call["headers"]["Authorization"] == "Bearer public-token" for call in session.gets if "api.blizzard.com" in call["url"])


def test_missing_character_or_user_token_renders_setup_image():
    plugin = make_plugin("setup")

    image = plugin.generate_image({}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    assert ImageChops.difference(image, Image.new("RGB", image.size, image.getpixel((0, 0)))).getbbox() is not None


def test_account_mode_uses_user_oauth_token_when_no_character_is_set(monkeypatch):
    plugin = make_plugin("account")
    session = FakeSession()
    monkeypatch.setattr("plugins.wow_profile_dashboard.wow_profile_dashboard.get_http_session", lambda: session)

    data = plugin._fetch_dashboard_data({}, FakeDeviceConfig(env={"BLIZZARD_USER_ACCESS_TOKEN": "user-token"}))

    assert data["mode"] == "account"
    assert data["title"] == "Testmage"
    assert data["account_characters"][0]["name"] == "Testmage"
    account_call = next(call for call in session.gets if call["url"].endswith("/profile/user/wow"))
    assert account_call["headers"]["Authorization"] == "Bearer user-token"


def test_mock_generate_image_renders_dashboard():
    cache_dir = cache_dir_for("mock")
    plugin = make_plugin("mock")

    image = plugin.generate_image({"useMockData": "true"}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    generated = list(cache_dir.glob("*.png"))
    assert any(path.name.startswith("image_") for path in generated)
