import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
TEST_CACHE_ROOT = SRC.parents[3] / ".tmp" / "wow_profile_dashboard_tests"

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


from io import BytesIO  # noqa: E402

from plugins.wow_profile_dashboard import keyless  # noqa: E402

RAIDERIO_PROFILE = {
    "name": "Elunelol", "race": "Dwarf", "class": "Paladin", "active_spec_name": "Holy", "faction": "alliance",
    "achievement_points": 15245, "realm": "Elune", "last_crawled_at": "2026-11-02T18:54:09.000Z",
    "thumbnail_url": "https://render.worldofwarcraft.com/us/character/elune/238/268221678-avatar.jpg?alt=/x.jpg",
    "mythic_plus_scores_by_season": [{"season": "season-mn-2", "scores": {"all": 4274.4}}],
    "mythic_plus_best_runs": [{"dungeon": "Murder Row", "mythic_level": 23, "score": 535.3},
                              {"dungeon": "Den of Nalorakk", "mythic_level": 24, "score": 546.5}],
    "gear": {"item_level_equipped": 335, "items": {
        "head": {"name": "Warhelm of the Consecrated Flame", "item_level": 334, "item_quality": 4},
        "finger1": {"name": "Band One", "item_level": 331, "item_quality": 3},
        "finger2": {"name": "Band Two", "item_level": 334, "item_quality": 4},
        "mainhand": {"name": "Malevolent Spiritcudgel", "item_level": 341, "item_quality": 4}}},
    "raid_progression": {
        "the-unbinding-of-kithix": {"summary": "1/1 H", "total_bosses": 1, "heroic_bosses_killed": 1},
        "the-venomous-abyss": {"summary": "5/8 M", "total_bosses": 8, "heroic_bosses_killed": 8,
                               "mythic_bosses_killed": 5},
        "tier-mn-1": {"summary": "", "total_bosses": 9},
    },
}
AFFIXES = {"affix_details": [
    {"id": 160, "name": "Xal'atath's Bargain: Devour", "icon_url": "https://cdn.raiderio.net/images/a.jpg"},
    {"id": 10, "name": "Fortified", "icon_url": "https://cdn.raiderio.net/images/b.jpg"},
    {"id": 9, "name": "Tyrannical", "icon_url": "https://evil.example/c.jpg"}]}
TOKEN = {"us": ["2026-11-02T23:33:30+00:00", 294637], "eu": ["2026-11-02T23:33:30+00:00", 416226]}


def _icon_bytes():
    buffer = BytesIO()
    Image.new("RGB", (56, 56), (40, 90, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


class KeylessSession(FakeSession):
    """Raider.IO, data.wowtoken.app and Raider.IO's icon CDN, plus Battle.net as FakeSession answers it."""

    def get(self, url, params=None, headers=None, timeout=None, stream=False):
        if url.endswith("/characters/profile"):
            answer = RAIDERIO_PROFILE
        elif url.endswith("/mythic-plus/affixes"):
            answer = AFFIXES
        elif url == keyless.WOWTOKEN_URL:
            answer = TOKEN
        elif url.startswith("https://cdn.raiderio.net/"):
            self.gets.append({"url": url, "params": params or {}, "headers": headers or {}, "timeout": timeout})
            return FakeResponse(content=_icon_bytes())
        else:
            return super().get(url, params=params, headers=headers, timeout=timeout)
        self.gets.append({"url": url, "params": params or {}, "headers": headers or {}, "timeout": timeout})
        return FakeResponse(answer)


def _fake_icon(response):
    return Image.open(BytesIO(response.content))


def test_a_raiderio_profile_becomes_the_character_page():
    data = keyless.character_payload(RAIDERIO_PROFILE, "US", "en_US")

    assert (data["title"], data["spec"], data["class"], data["faction"], data["source"]) == (
        "Elunelol", "Holy", "Paladin", "Alliance", "Raider.IO")
    assert data["equipped_item_level"] == 335 and data["mythic_rating"] == 4274.4
    assert [item["slot"] for item in data["equipment"]] == ["Head", "Finger", "Finger", "Main Hand"]
    assert data["equipment"][1]["quality"] == "RARE"
    assert [run["dungeon"] for run in data["mythic_best"]] == ["Den of Nalorakk", "Murder Row"]
    # The furthest raid first; one with no kills is left out.
    assert data["raids"] == [{"name": "The Venomous Abyss", "summary": "5/8 M"},
                             {"name": "The Unbinding Of Kithix", "summary": "1/1 H"}]
    assert data["media_url"] == "https://render.worldofwarcraft.com/us/character/elune/238/268221678-main-raw.png"
    assert keyless.short_affix("Xal'atath's Bargain: Devour") == "Bargain: Devour"
    assert keyless.gold_text(294637) == "294,637"


def test_without_battle_net_keys_raiderio_stands_in_and_the_week_is_added(monkeypatch):
    plugin = make_plugin("keyless")
    for path in cache_dir_for("keyless").glob("*"):
        path.unlink()
    session = KeylessSession()
    monkeypatch.setattr("plugins.wow_profile_dashboard.wow_profile_dashboard.get_http_session", lambda: session)
    monkeypatch.setattr("plugins.wow_profile_dashboard.wow_profile_dashboard.safe_open_image_response", _fake_icon)

    data = plugin._fetch_dashboard_data({"region": "us", "realmSlug": "elune", "characterName": "Elunelol"},
                                        FakeDeviceConfig())

    assert data["mode"] == "raiderio" and data["status"] == "ok"
    profile_call = next(call for call in session.gets if call["url"].endswith("/characters/profile"))
    assert profile_call["params"] == {"region": "us", "realm": "elune", "name": "elunelol",
                                      "fields": keyless.PROFILE_FIELDS}
    assert [affix["name"] for affix in data["affixes"]] == ["Xal'atath's Bargain: Devour", "Fortified", "Tyrannical"]
    assert data["affixes"][2]["icon_url"] == ""  # not Raider.IO's CDN
    assert data["token"]["gold"] == 294637
    assert (cache_dir_for("keyless") / "affix-160.png").exists()
    assert not any("api.blizzard.com" in call["url"] for call in session.gets)

    # Within their lifetimes the affixes, the token and the icons come from the cache.
    session.gets.clear()
    plugin._add_weekly({}, "us")
    assert session.gets == []


def test_battle_net_pages_gain_raider_io_raid_progress(monkeypatch):
    plugin = make_plugin("battlenet-raids")
    session = KeylessSession()
    monkeypatch.setattr("plugins.wow_profile_dashboard.wow_profile_dashboard.get_http_session", lambda: session)
    monkeypatch.setattr("plugins.wow_profile_dashboard.wow_profile_dashboard.safe_open_image_response", _fake_icon)

    data = plugin._fetch_dashboard_data(
        {"region": "us", "realmSlug": "Area 52", "characterName": "TestMage"},
        FakeDeviceConfig(env={"BLIZZARD_CLIENT_ID": "client-id", "BLIZZARD_CLIENT_SECRET": "client-secret"}))

    assert data["mode"] == "public-character" and data["mythic_rating"] == 2450.5
    assert data["raids"][0] == {"name": "The Venomous Abyss", "summary": "5/8 M"}
    summary_call = next(call for call in session.gets if call["url"].endswith("/characters/profile"))
    assert summary_call["params"]["fields"] == keyless.SUMMARY_FIELDS
    assert [item["name"] for item in data["equipment"]] == ["Practice Staff", "Arcane Crown"]


def test_the_week_and_the_token_are_drawn_on_the_character_page():
    plugin = make_plugin("render-week")
    data = {**plugin._sample_payload(), "updated_at": "2026-11-02 16:40"}
    drawn = []
    original = plugin._single

    def capture(draw, position, text, font, fill, max_width, min_size=8):
        drawn.append(str(text))
        return original(draw, position, text, font, fill, max_width, min_size)

    plugin._single = capture
    image = plugin._render_dashboard(data, (800, 480))

    assert image.size == (800, 480)
    assert {"Bargain: Devour", "Fortified", "Guile", "Ara-Kara"} <= set(drawn)
    assert any(text.startswith("Mock data · 11-02") for text in drawn)
    # The token sits in the snapshot's header, by its gold coin.
    coin = image.crop((600, 30, 780, 60)).convert("RGB").get_flattened_data()
    assert sum(1 for pixel in coin if pixel == (199, 151, 54)) > 20


def test_a_line_too_long_even_at_the_smallest_size_is_cut_short():
    plugin = make_plugin("single")
    image = Image.new("RGB", (400, 60))
    draw = ImageDraw.Draw(image)

    plugin._single(draw, (0, 0), "Masterwork Band of the Endless Reaches", plugin._font(9), (255, 255, 255), 60, 7)

    assert image.getbbox() is not None and image.getbbox()[2] <= 62
