import hashlib
import sys
import types
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw
import pytest

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.Session = object
    requests_stub.get = lambda *args, **kwargs: None
    requests_stub.adapters = types.SimpleNamespace(HTTPAdapter=object)
    sys.modules["requests"] = requests_stub

try:
    import psutil  # noqa: F401
except ModuleNotFoundError:
    psutil_stub = types.ModuleType("psutil")
    psutil_stub.virtual_memory = lambda: types.SimpleNamespace(total=8 * 1024 ** 3)
    sys.modules["psutil"] = psutil_stub

try:
    import jinja2  # noqa: F401
except ModuleNotFoundError:
    jinja2_stub = types.ModuleType("jinja2")
    jinja2_stub.Environment = object
    jinja2_stub.FileSystemLoader = object
    jinja2_stub.select_autoescape = lambda *args, **kwargs: None
    sys.modules["jinja2"] = jinja2_stub

import plugins.sports_dashboard.sports_dashboard as sports_dashboard_module
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
    read_source_provenance,
)
from plugins.base_plugin.presentation import PresentationMode
from plugins.sports_dashboard.sports_dashboard import (
    COLORS,
    DAY_COLORS,
    DEEP_NIGHT_COLORS,
    DEFAULT_EWC_COMPETITIONS_URL,
    DEFAULT_MSI_LEAGUE_ID,
    DEFAULT_WORLD_CUP_STANDINGS_CACHE_HOURS,
    DEFAULT_WORLD_CUP_STANDINGS_URL,
    LOCAL_F1_LOGO_PATH,
    LOCAL_CS_MAJOR_LOGO_PATH,
    LOCAL_EWC_LOGO_PATH,
    LOCAL_EWC_GAME_LOGO_DIR,
    LOCAL_TI_LOGO_PATH,
    LOCAL_LPL_LOGO_PATH,
    LOCAL_LCK_LOGO_PATH,
    LOCAL_LCK_TEAM_LOGO_DIR,
    LOCAL_LPL_MARBLE_FILLER_PATH,
    LOCAL_LPL_MSI_CARD_ACCENT_DIR,
    LOCAL_LPL_MSI_CARD_ACCENT_PATH,
    LOCAL_LPL_MSI_NEXT_FILLER_PATH,
    LOCAL_LPL_MSI_OFFSEASON_FILLER_PATH,
    LOCAL_LPL_MSI_OFFSEASON_FILLER_PATHS,
    LPL_MSI_OFFSEASON_FILLER_BOTTOM_OVERFILL,
    LPL_MSI_OFFSEASON_FILLER_VERTICAL_CROP_OFFSET,
    LPL_MSI_OFFSEASON_FILLER_ZOOM,
    LOCAL_MLB_HEADER_CUTOUT_PATH,
    LOCAL_MLB_LOGO_PATH,
    LOCAL_MLB_TITLE_WORDMARK_PATH,
    LOCAL_MSI_LOGO_PATH,
    LOCAL_NCAA_HEADER_CUTOUT_PATH,
    LOCAL_NCAA_LOGO_PATH,
    LOCAL_NBA_COURT_STRIP_PATH,
    LOCAL_NBA_EMPTY_SLOT_FILLER_PATH,
    LOCAL_NBA_LOGO_PATH,
    LOCAL_NBA_OFFSEASON_ACCENT_PATH,
    LOCAL_NBA_OFFSEASON_FILLER_PATH,
    LOCAL_NFL_HEADER_CUTOUT_PATH,
    LOCAL_NFL_LOGO_PATH,
    LOCAL_PGA_HEADER_CUTOUT_PATH,
    LOCAL_PGA_FAIRWAY_STRIP_PATH,
    LOCAL_PGA_LOGO_PATH,
    LOCAL_PGA_TITLE_WORDMARK_PATH,
    LOCAL_WNBA_HEADER_CUTOUT_PATH,
    LOCAL_WNBA_LOGO_PATH,
    LOCAL_WNBA_TITLE_WORDMARK_PATH,
    SPORT_HEADER_CUTOUT_SCALE,
    NBA_OFFSEASON_ACCENT_SIZE,
    NBA_OFFSEASON_FILLER_ZOOM,
    LOCAL_WORLDCUP_HEADER_BANNER_PATH,
    LOCAL_WORLDCUP_TITLE_WORDMARK_PATH,
    LOCAL_WORLDCUP_PITCH_STRIP_PATH,
    LOCAL_WORLDCUP_LOGO_PATH,
    MLB_TEAM_ZH_FULL_NAMES,
    MLB_TEAM_ZH_NAMES,
    NCAA_ESPN_LOGO_IDS,
    NCAA_TEAM_ZH_FULL_NAMES,
    NCAA_TEAM_ZH_NAMES,
    NFL_TEAM_ZH_FULL_NAMES,
    NFL_TEAM_ZH_NAMES,
    NBA_INLINE_LOGO_SIZE,
    NBA_INLINE_TEAM_FONT_SIZE,
    NBA_INLINE_TEAM_MIN_FONT_SIZE,
    NBA_MINI_LINEUP_LOGO_SIZE,
    NBA_MINI_LINEUP_ODDS_TEAM_FONT_SIZE,
    OFFSEASON_HUB_ROTATION_MINUTES,
    SportsDashboard,
    FLAG_IMAGE_CACHE,
    TEAM_LOGO_CACHE,
    TEAM_LOGO_FETCH_TIMEOUT_SECONDS,
    WORLD_CUP_STANDINGS_STATE_VERSION,
    WNBA_TEAM_ZH_FULL_NAMES,
    WNBA_TEAM_ZH_NAMES,
    _ACTIVE_COLORS,
    _safe_exception_text,
)


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), orientation="horizontal", timezone="America/Los_Angeles"):
        self.resolution = resolution
        self.orientation = orientation
        self.timezone = timezone
        self.env = {}

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"orientation": self.orientation, "timezone": self.timezone}
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, key):
        return self.env.get(key)


def _plugin():
    return SportsDashboard({"id": "sports_dashboard"})


def test_sports_dashboard_manifest_restores_internal_panel_refresh_on_display():
    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "sports_dashboard"
        / "plugin-info.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["capabilities"]["supports_presentation_refresh"] is True
    assert manifest["refresh_on_display"] is True


def test_sports_dashboard_presentation_rerenders_from_cache_without_forcing_providers(monkeypatch):
    plugin = _plugin()
    captured = {}

    def render(settings, device_config, *, resolved_theme_context):
        captured["settings"] = dict(settings)
        captured["device_config"] = device_config
        captured["theme"] = resolved_theme_context
        return attach_source_provenance(
            Image.new("RGB", (800, 480), "white"),
            SourceProvenance.FRESH_CACHE,
        )

    monkeypatch.setattr(plugin, "render_themed_image", render)
    request = types.SimpleNamespace(request_id="a" * 32)
    device_config = object()
    theme = {"mode": "day"}

    assert plugin.presentation_mode({}) is PresentationMode.PREPARED_BANK
    prepared = plugin.prepare_presentation(
        {"worldCupTopHeight": "208"},
        device_config,
        request=request,
        resolved_theme_context=theme,
    )

    assert prepared.changed is True
    assert prepared.request_id == "a" * 32
    assert captured["settings"]["_inkypiPresentationRefresh"] is True
    assert "forceRefresh" not in captured["settings"]
    assert captured["device_config"] is device_config
    assert captured["theme"] is theme


def _canonical_theme(mode, *, background, panel, ink, muted, rule, accent):
    palette = {
        "background": background,
        "panel": panel,
        "ink": ink,
        "muted": muted,
        "rule": rule,
        "accent": accent,
    }
    return {"mode": mode, "palette": palette, "css": {}}


def test_league_accent_palettes_are_distinct():
    for palette in (DAY_COLORS, DEEP_NIGHT_COLORS):
        assert palette["worldcup_accent"] != palette["nba_accent"]
        assert palette["worldcup_accent"] != palette["lpl_accent"]
        assert palette["nba_accent"] != palette["lpl_accent"]
        assert palette["lck_accent"] != palette["lpl_accent"]
        assert palette["lck_accent"] != palette["nba_accent"]
        assert palette["worldcup_tag"] != palette["nba_tag"]
        assert palette["nba_tag"] != palette["lpl_tag"]
        assert palette["lck_tag"] != palette["lpl_tag"]
        assert palette["ewc_accent"] != palette["lpl_accent"]
        assert palette["ewc_accent"] != palette["worldcup_accent"]
        assert palette["ewc_tag"] != palette["lpl_tag"]
        assert palette["msi_accent"] != palette["lpl_shadow"]
        assert palette["msi_accent"] != palette["ewc_accent"]
        assert palette["msi_tag"] != palette["lpl_tag"]
        assert palette["msi_tag"] != palette["ewc_tag"]


def test_original_day_roles_preserve_structure_and_league_accents():
    plugin = _plugin()
    theme = _canonical_theme(
        "day",
        background=(241, 236, 225),
        panel=(221, 213, 196),
        ink=(19, 21, 23),
        muted=(73, 75, 79),
        rule=(128, 124, 116),
        accent=(180, 44, 58),
    )

    colors = plugin._sports_dashboard_colors(theme)

    assert colors == DAY_COLORS
    assert len({colors[key] for key in ("worldcup_accent", "nba_accent", "lpl_accent", "lck_accent")}) == 4


def test_explicit_mode_resolves_manifest_palette_for_direct_generate_fallback():
    day_palette = {
        "background": (239, 234, 222),
        "panel": (218, 210, 192),
        "ink": (18, 20, 22),
        "muted": (70, 72, 76),
        "rule": (124, 120, 112),
        "accent": (174, 40, 52),
    }
    night_palette = {
        "background": (8, 10, 13),
        "panel": (23, 27, 33),
        "ink": (245, 246, 248),
        "muted": (178, 182, 190),
        "rule": (58, 64, 72),
        "accent": (74, 188, 236),
    }
    manifest = types.SimpleNamespace(theme=types.SimpleNamespace(day=day_palette, night=night_palette))
    plugin = SportsDashboard({"id": "sports_dashboard", "_manifest": manifest})

    context = plugin._sports_dashboard_theme_context(
        {"sportsDashboardTheme": "day"},
        FakeDeviceConfig(timezone="UTC"),
        datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc),
    )

    assert context["mode"] == "day"
    assert context["palette"] == day_palette


def test_offseason_hub_sport_accents_are_distinct_comic_palette_colors():
    sports = ["MLB", "WNBA", "PGA", "NFL", "NCAA"]
    for palette in (DAY_COLORS, DEEP_NIGHT_COLORS):
        token = _ACTIVE_COLORS.set(palette)
        try:
            allowed = {palette[key] for key in ("blue", "orange", "green", "amber", "cyan")}
            accents = {sport: SportsDashboard._hub_sport_accent(sport) for sport in sports}

            assert len(set(accents.values())) == len(sports)
            assert set(accents.values()).issubset(allowed)
            assert accents["NFL"] == palette["nfl_accent"]
            assert accents["NCAA"] == palette["ncaa_accent"]
            assert accents["NFL"] != accents["MLB"]
            assert accents["NCAA"] != accents["NFL"]
            assert palette["nfl_tag"] != palette["mlb_tag"]
            assert palette["ncaa_tag"] != palette["mlb_tag"]
            assert palette["pga_leader"] != palette["amber"]
            assert palette["pga_leader"] != palette["pga_accent"]
            assert palette["nfl_field_tint"] != palette["panel_blue"]
            assert palette["ncaa_field_tint"] != palette["panel_blue"]
            assert SportsDashboard._football_context_fill_key("NFL") == "nfl_field_tint"
            assert SportsDashboard._football_context_fill_key("NCAA") == "ncaa_field_tint"
        finally:
            _ACTIVE_COLORS.reset(token)


def test_section_header_uses_supplied_league_accent():
    plugin = _plugin()
    image = Image.new("RGB", (120, 52), COLORS["paper"])
    draw = ImageDraw.Draw(image)

    plugin._draw_section_header(draw, 0, 120, 10, "UPCOMING", COLORS["lpl_accent"])

    assert image.getpixel((18, 20)) == COLORS["lpl_accent"]

def test_worldcup_flag_display_size_uses_country_specific_aspect_ratios():
    assert SportsDashboard._worldcup_flag_display_size("https://flagcdn.com/w80/ch.png", "SUI", 44, 30) == (30, 30)
    assert SportsDashboard._worldcup_flag_display_size("https://flagcdn.com/w80/qa.png", "QAT", 44, 30) == (44, 17)
    assert SportsDashboard._worldcup_flag_display_size("https://flagcdn.com/w80/be.png", "BEL", 44, 30) == (35, 30)
    assert SportsDashboard._worldcup_flag_display_size("https://flagcdn.com/w80/us.png", "USA", 44, 30) == (44, 23)
    assert SportsDashboard._worldcup_flag_display_size("https://flagcdn.com/w80/zz.png", "ZZZ", 44, 30) == (44, 29)

def test_worldcup_scotland_uses_local_saltire_flag():
    FLAG_IMAGE_CACHE.clear()

    flag_url = SportsDashboard._flag_url_for_tla("SCO")
    assert flag_url == "local:worldcup:sco"
    assert SportsDashboard._worldcup_flag_country_code(flag_url, "SCO") == "SCO"
    assert SportsDashboard._worldcup_flag_display_size(flag_url, "SCO", 44, 30) == (44, 26)

    flag = SportsDashboard._load_flag_image(flag_url, (50, 30))

    assert flag.size == (50, 30)
    assert flag.getpixel((25, 15))[:3] == (255, 255, 255)
    assert flag.getpixel((25, 2))[:3] == (0, 94, 184)


def test_worldcup_flag_draw_loads_country_specific_display_size(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    image = Image.new("RGBA", (80, 50), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    requested_sizes = []

    def fake_load_flag_image(flag_url, size):
        requested_sizes.append(size)
        return Image.new("RGBA", size, (255, 0, 0, 255))

    monkeypatch.setattr(plugin, "_load_flag_image", fake_load_flag_image)

    plugin._draw_worldcup_flag(image, draw, "https://flagcdn.com/w80/ch.png", 10, 10, 44, 30, "SUI")
    plugin._draw_worldcup_flag(image, draw, "https://flagcdn.com/w80/qa.png", 10, 10, 44, 30, "QAT")
    plugin._draw_worldcup_flag(image, draw, "https://flagcdn.com/w80/be.png", 10, 10, 44, 30, "BEL")

    assert requested_sizes == [(30, 30), (44, 17), (35, 30)]

def test_worldcup_flag_loader_preserves_source_ratio(monkeypatch):
    FLAG_IMAGE_CACHE.clear()
    source = Image.new("RGBA", (40, 20), (0, 92, 185, 255))
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    data = buffer.getvalue()
    calls = []

    class FakeResponse:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size > 0
            yield data

        def close(self):
            return None

    class FakeSession:
        def get(self, url, headers=None, timeout=None, stream=False):
            assert stream is True
            calls.append((url, headers, timeout))
            return FakeResponse()

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    flag = SportsDashboard._load_flag_image("https://example.test/ratio-2x1.png", (20, 20))

    assert flag.size == (20, 10)
    assert calls == [("https://example.test/ratio-2x1.png", {"User-Agent": "InkyPi/1.0"}, 4)]

def test_worldcup_scheduled_rows_use_worldcup_accent():
    assert SportsDashboard._worldcup_status_color({"state": "SCHEDULED"}) == COLORS["worldcup_accent"]
    assert SportsDashboard._worldcup_status_color({"state": "LIVE"}) == COLORS["worldcup_live"]


def test_safe_exception_text_redacts_query_secrets():
    text = _safe_exception_text(
        RuntimeError(
            "401 Client Error for url: "
            "https://api.example.test/odds?apiKey=secret-key-123&token=secret-token&regions=us"
        )
    )

    assert "secret-key-123" not in text
    assert "secret-token" not in text
    assert "apiKey=<redacted>" in text
    assert "token=<redacted>" in text
    assert "regions=us" in text


def _sample_payload():
    return {
        "data": {
            "schedule": {
                "events": [
                    {
                        "startTime": "2026-06-03T09:00:00Z",
                        "state": "unstarted",
                        "blockName": "Playoffs",
                        "match": {
                            "id": "match-blg-edg",
                            "strategy": {"type": "bestOf", "count": 5},
                            "teams": [
                                {"code": "BLG", "image": "https://example.com/blg.png", "result": {}},
                                {"code": "EDG", "image": "https://example.com/edg.png", "result": {}},
                            ]
                        },
                    },
                    {
                        "startTime": "2026-06-02T09:00:00Z",
                        "state": "completed",
                        "blockName": "Playoffs",
                        "match": {
                            "id": "match-tt-lgd",
                            "strategy": {"type": "bestOf", "count": 5},
                            "teams": [
                                {"code": "TT", "image": "https://example.com/tt.png", "result": {"gameWins": 2}},
                                {"code": "LGD", "image": "https://example.com/lgd.png", "result": {"gameWins": 3}},
                            ]
                        },
                    },
                ]
            }
        }
    }

def _sample_ewc_competitions_html():
    return """
    <section>
      <div class="card">upcoming <img alt="Competition Logo" src="/_next/image?url=https%3A%2F%2Fd3h9qea4qy4169.cloudfront.net%2FALGS_Split_1_Playoff_Logo_Black_b705b1f3d8.png&amp;w=1920&amp;q=50">
        Main Event Jul 07 - 11, 2026
        Confirmed until 2026
        Prize Pool$2,000,000
        Participating clubs 40
        <a href="/en/competitions/2026/apex-legends">Visit Game Page</a>
      </div>
      <div class="card">upcoming <img alt="Competition Logo" src="https://d3h9qea4qy4169.cloudfront.net/Game_dota2_Variant_Dark_82f230e51b.svg">
        Main Event Jul 07 - 19, 2026
        Prize Pool$2,000,000
        Participating clubs 24
        <a href="/en/competitions/2026/dota2">Visit Game Page</a>
      </div>
      <div class="card">upcoming <img alt="Competition Logo" src="/_next/image?url=https%3A%2F%2Fd3h9qea4qy4169.cloudfront.net%2Ffatalfury_cotw_Logo_Black_f768ff3bae.png&amp;w=1920&amp;q=50">
        Main Event Jul 08 - 11, 2026
        Prize Pool$1,000,000
        Participating players 32
        <a href="/en/competitions/2026/fatal-fury">Visit Game Page</a>
      </div>
      <div class="card duplicate">upcoming
        Main Event Jul 07 - 11, 2026 Prize Pool$2,000,000 Participating clubs 40
        <a href="/en/competitions/2026/apex-legends">Visit Game Page</a>
      </div>
    </section>
    """

def _sample_ewc_detail_schedule_html():
    return """
    <section aria-label="Schedule">
      <article class="match-row">
        <span>Thu, Jul 2 - 11:00</span>
        <span>upcoming</span>
        <span>Group A - Opening Match #1</span>
        <img alt="Team RRQ" src="/_next/image?url=https%3A%2F%2Ftds-cdn.ewc.efg.gg%2Fassets%2Fclubs%2F2068035497296400384%2FLOGO_LIGHT.png&amp;w=256&amp;q=50">
        <h5>Team RRQ</h5>
        <strong>-</strong>
      </article>
      <article class="match-row">
        <span>Thu, Jul 2 - 11:00</span>
        <span>upcoming</span>
        <span>Group A - Opening Match #1</span>
        <img alt="100 Thieves" srcset="/_next/image?url=https%3A%2F%2Ftds-cdn.ewc.efg.gg%2Fassets%2Fclubs%2F2068035456414519296%2FLOGO_LIGHT.png&amp;w=128&amp;q=50 1x">
        <h5>100 Thieves</h5>
        <strong>-</strong>
      </article>
      <article class="match-row">
        <span>Thu, Jul 2 - 11:00</span>
        <span>live</span>
        <span>Group B - Opening Match #1</span>
        <img alt="BBL Esports" src="https://example.com/bbl.png">
        <h5>BBL Esports</h5>
        <strong>1</strong>
      </article>
      <article class="match-row">
        <span>Thu, Jul 2 - 11:00</span>
        <span>live</span>
        <span>Group B - Opening Match #1</span>
        <img alt="EDward Gaming" src="https://example.com/edg.png">
        <h5>EDward Gaming</h5>
        <strong>0</strong>
      </article>
      <article class="match-row">
        <span>Wed, Jul 1 - 09:00</span>
        <span>completed</span>
        <span>Round 1 1</span>
        <img alt="The MongolZ" src="https://example.com/mongolz.png">
        <h5>The MongolZ</h5>
        <strong>0</strong>
      </article>
      <article class="match-row">
        <span>Wed, Jul 1 - 09:00</span>
        <span>completed</span>
        <span>Round 1 1</span>
        <img alt="FUT Esports" src="https://example.com/fut.png">
        <h5>FUT Esports</h5>
        <strong>1</strong>
      </article>
    </section>
    """
def _sample_worldcup_fixture():
    return {
        "fixture": {
            "id": 10101,
            "date": "2026-06-12T00:00:00+00:00",
            "status": {"short": "NS", "long": "Not Started", "elapsed": None},
        },
        "league": {"round": "Group Stage - 1"},
        "teams": {
            "home": {"name": "United States", "code": "USA"},
            "away": {"name": "Mexico", "code": "MEX"},
        },
        "goals": {"home": None, "away": None},
        "score": {"fulltime": {"home": None, "away": None}},
    }


def _sample_nba_scoreboard_payload():
    return {
        "events": [
            {
                "id": "401000001",
                "date": "2026-06-05T00:30Z",
                "season": {"slug": "post-season"},
                "competitions": [
                    {
                        "id": "401000001",
                        "date": "2026-06-05T00:30Z",
                        "status": {
                            "period": 4,
                            "displayClock": "0.0",
                            "type": {
                                "state": "post",
                                "completed": True,
                                "description": "Final",
                                "shortDetail": "Final",
                            },
                        },
                        "series": {
                            "competitors": [
                                {"team": {"id": "18", "abbreviation": "NY"}, "wins": 2},
                                {"team": {"id": "24", "abbreviation": "SA"}, "wins": 0},
                            ]
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "112",
                                "team": {
                                    "abbreviation": "NY",
                                    "shortDisplayName": "Knicks",
                                    "displayName": "New York Knicks",
                                    "logo": "https://example.com/ny.png",
                                },
                                "linescores": [{"value": 28}, {"value": 27}, {"value": 31}, {"value": 26}],
                            },
                            {
                                "homeAway": "away",
                                "score": "106",
                                "team": {
                                    "abbreviation": "SA",
                                    "shortDisplayName": "Spurs",
                                    "displayName": "San Antonio Spurs",
                                    "logo": "https://example.com/sa.png",
                                },
                                "linescores": [{"value": 25}, {"value": 29}, {"value": 24}, {"value": 28}],
                            },
                        ],
                    }
                ],
            },
            {
                "id": "401000002",
                "date": "2026-06-09T00:30Z",
                "season": {"slug": "post-season"},
                "competitions": [
                    {
                        "id": "401000002",
                        "date": "2026-06-09T00:30Z",
                        "status": {
                            "period": 0,
                            "displayClock": "",
                            "type": {
                                "state": "pre",
                                "completed": False,
                                "description": "Scheduled",
                                "shortDetail": "Tue, Jun 9",
                            },
                        },
                        "series": {
                            "competitors": [
                                {"team": {"id": "18", "abbreviation": "NY"}, "wins": 2},
                                {"team": {"id": "24", "abbreviation": "SA"}, "wins": 0},
                            ]
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "0",
                                "team": {
                                    "abbreviation": "NY",
                                    "shortDisplayName": "Knicks",
                                    "displayName": "New York Knicks",
                                    "logo": "https://example.com/ny.png",
                                },
                                "linescores": [],
                            },
                            {
                                "homeAway": "away",
                                "score": "0",
                                "team": {
                                    "abbreviation": "SA",
                                    "shortDisplayName": "Spurs",
                                    "displayName": "San Antonio Spurs",
                                    "logo": "https://example.com/sa.png",
                                },
                                "linescores": [],
                            },
                        ],
                    }
                ],
            },
        ]
    }


def _sample_nba_odds_event():
    return {
        "id": "nba-ny-sa",
        "sport_key": "basketball_nba",
        "commence_time": "2026-06-09T00:30:00Z",
        "home_team": "New York Knicks",
        "away_team": "San Antonio Spurs",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "New York Knicks", "price": 1.75},
                            {"name": "San Antonio Spurs", "price": 2.05},
                        ],
                    }
                ],
            }
        ],
    }


def _sample_nba_odds_api_io_event():
    return {
        "id": 88112233,
        "home": "New York Knicks",
        "away": "San Antonio Spurs",
        "date": "2026-06-09T00:30:00Z",
        "status": "pending",
        "league": {"name": "USA - NBA, Playoffs", "slug": "usa-nba-playoffs"},
        "bookmakers": {
            "Bet365": [
                {
                    "name": "ML",
                    "odds": [
                        {"home": "1.650", "away": "2.100"},
                    ],
                }
            ]
        },
    }


def _sample_mlb_scoreboard_payload():
    return {
        "dates": [
            {
                "date": "2026-06-14",
                "games": [
                    {
                        "gamePk": 777001,
                        "gameDate": "2026-06-14T20:10:00Z",
                        "status": {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"},
                        "venue": {"name": "Dodger Stadium"},
                        "teams": {
                            "away": {
                                "score": 3,
                                "leagueRecord": {"wins": 41, "losses": 28},
                                "probablePitcher": {"fullName": "Logan Webb"},
                                "team": {"name": "San Francisco Giants"},
                            },
                            "home": {
                                "score": 5,
                                "leagueRecord": {"wins": 45, "losses": 24},
                                "probablePitcher": {"fullName": "Yoshinobu Yamamoto"},
                                "team": {"name": "Los Angeles Dodgers"},
                            },
                        },
                        "linescore": {
                            "currentInning": 7,
                            "currentInningOrdinal": "7th",
                            "inningState": "Top",
                            "balls": 2,
                            "strikes": 1,
                            "outs": 1,
                            "offense": {
                                "batter": {"fullName": "Matt Chapman"},
                                "first": {"id": 1},
                                "third": {"id": 3},
                            },
                            "defense": {"pitcher": {"fullName": "Yoshinobu Yamamoto"}},
                            "teams": {
                                "away": {"runs": 3, "hits": 7, "errors": 1},
                                "home": {"runs": 5, "hits": 8, "errors": 0},
                            },
                        },
                    },
                    {
                        "gamePk": 777002,
                        "gameDate": "2026-06-15T02:10:00Z",
                        "status": {"abstractGameState": "Preview", "detailedState": "Scheduled", "codedGameState": "S"},
                        "venue": {"name": "T-Mobile Park"},
                        "teams": {
                            "away": {
                                "team": {"name": "Texas Rangers"},
                                "leagueRecord": {"wins": 35, "losses": 33},
                                "probablePitcher": {"fullName": "Jacob deGrom"},
                            },
                            "home": {
                                "team": {"name": "Seattle Mariners"},
                                "leagueRecord": {"wins": 39, "losses": 29},
                                "probablePitcher": {"fullName": "Luis Castillo"},
                            },
                        },
                        "linescore": {},
                    },
                ],
            }
        ]
    }


def _sample_wnba_scoreboard_payload():
    payload = _sample_nba_scoreboard_payload()
    payload["events"] = [payload["events"][0]]
    live = payload["events"][0]
    live["id"] = "wnba-live"
    live["date"] = "2026-06-14T23:00Z"
    competition = live["competitions"][0]
    competition["id"] = "wnba-live"
    competition["date"] = "2026-06-14T23:00Z"
    competition["venue"] = {
        "fullName": "Michelob ULTRA Arena",
        "address": {"city": "Las Vegas", "state": "NV"},
    }
    competition["broadcasts"] = [{"market": "national", "names": ["ION"]}]
    competition["status"] = {
        "period": 3,
        "displayClock": "4:22",
        "type": {"state": "in", "completed": False, "description": "In Progress", "shortDetail": "Q3 4:22"},
    }
    competition["competitors"][0]["team"]["abbreviation"] = "LV"
    competition["competitors"][0]["team"]["shortDisplayName"] = "Aces"
    competition["competitors"][0]["team"]["displayName"] = "Las Vegas Aces"
    competition["competitors"][0]["team"]["logo"] = "https://example.com/wnba-lv.png"
    competition["competitors"][0]["team"]["logos"] = [{"href": "https://example.com/wnba-lv.png"}]
    competition["competitors"][0]["score"] = "78"
    competition["competitors"][0]["records"] = [{"summary": "8-3"}]
    competition["competitors"][1]["team"]["abbreviation"] = "SEA"
    competition["competitors"][1]["team"]["shortDisplayName"] = "Storm"
    competition["competitors"][1]["team"]["displayName"] = "Seattle Storm"
    competition["competitors"][1]["team"]["logo"] = "https://example.com/wnba-sea.png"
    competition["competitors"][1]["team"]["logos"] = [{"href": "https://example.com/wnba-sea.png"}]
    competition["competitors"][1]["score"] = "72"
    competition["competitors"][1]["records"] = [{"summary": "7-4"}]
    return payload


def _sample_pga_scoreboard_payload():
    return {
        "events": [
            {
                "id": "pga-1",
                "name": "U.S. Open",
                "shortName": "U.S. Open",
                "date": "2026-06-12T14:00Z",
                "endDate": "2026-06-15T23:00Z",
                "competitions": [
                    {
                        "id": "pga-1",
                        "date": "2026-06-12T14:00Z",
                        "endDate": "2026-06-15T23:00Z",
                        "venue": {"fullName": "Shinnecock Hills"},
                        "competitors": [
                            {
                                "order": 1,
                                "score": "-9",
                                "athlete": {"shortName": "S. Scheffler"},
                                "linescores": [{"period": 3, "displayValue": "-2", "value": 68}],
                            },
                            {
                                "order": 2,
                                "score": "-7",
                                "athlete": {"shortName": "R. McIlroy"},
                                "linescores": [{"period": 3, "displayValue": "E", "value": 70}],
                            },
                        ],
                    }
                ],
            }
        ]
    }


def _sample_nfl_scoreboard_payload():
    return {
        "season": {"year": 2026, "displayName": "2026 NFL Season"},
        "week": {"number": 1, "text": "Week 1"},
        "events": [
            {
                "id": "nfl-live",
                "date": "2026-09-11T00:20Z",
                "week": {"number": 1},
                "season": {"slug": "regular-season"},
                "shortName": "SEA @ NE",
                "competitions": [
                    {
                        "id": "nfl-live",
                        "date": "2026-09-11T00:20Z",
                        "neutralSite": False,
                        "venue": {
                            "fullName": "Gillette Stadium",
                            "address": {"city": "Foxborough", "state": "MA"},
                        },
                        "status": {
                            "period": 2,
                            "displayClock": "8:42",
                            "type": {"state": "in", "completed": False, "description": "In Progress", "shortDetail": "Q2 8:42"},
                        },
                        "situation": {
                            "possession": "26",
                            "downDistanceText": "3rd & 4",
                            "yardLineText": "SEA 42",
                            "lastPlay": {"text": "Kenneth Walker run for 6 yards"},
                        },
                        "broadcasts": [{"market": "national", "names": ["NBC"]}],
                        "odds": [{"details": "NE -2.5", "overUnder": 44.5}],
                        "competitors": [
                            {
                                "homeAway": "away",
                                "id": "26",
                                "score": "17",
                                "team": {"id": "26", "abbreviation": "SEA", "shortDisplayName": "Seahawks", "displayName": "Seattle Seahawks", "logos": [{"href": "https://example.com/nfl-sea.png"}]},
                                "records": [{"summary": "0-0"}],
                            },
                            {
                                "homeAway": "home",
                                "id": "17",
                                "score": "14",
                                "team": {"id": "17", "abbreviation": "NE", "shortDisplayName": "Patriots", "displayName": "New England Patriots", "logos": [{"href": "https://example.com/nfl-ne.png"}]},
                                "records": [{"summary": "0-0"}],
                            },
                        ],
                    }
                ],
            },
            {
                "id": "nfl-next",
                "date": "2026-09-14T20:25Z",
                "week": {"number": 1},
                "competitions": [
                    {
                        "id": "nfl-next",
                        "date": "2026-09-14T20:25Z",
                        "venue": {
                            "fullName": "Soldier Field",
                            "address": {"city": "Chicago", "state": "IL"},
                        },
                        "status": {"period": 0, "displayClock": "", "type": {"state": "pre", "completed": False, "description": "Scheduled"}},
                        "broadcasts": [{"market": "national", "names": ["FOX"]}],
                        "odds": [{"details": "CHI -1.5", "overUnder": 42.5}],
                        "competitors": [
                            {"homeAway": "away", "team": {"id": "13", "abbreviation": "GB", "shortDisplayName": "Packers", "logos": [{"href": "https://example.com/nfl-gb.png"}]}, "records": [{"summary": "0-0"}]},
                            {"homeAway": "home", "team": {"id": "3", "abbreviation": "CHI", "shortDisplayName": "Bears", "logos": [{"href": "https://example.com/nfl-chi.png"}]}, "records": [{"summary": "0-0"}]},
                        ],
                    }
                ],
            },
        ],
    }


def _sample_ncaa_scoreboard_payload():
    return {
        "season": {"year": 2026, "displayName": "2026 College Football"},
        "week": {"number": 1, "text": "Week 1"},
        "events": [
            {
                "id": "ncaa-live",
                "date": "2026-08-29T23:30Z",
                "week": {"number": 1},
                "shortName": "TEX vs MICH",
                "notes": [{"headline": "Kickoff Classic"}],
                "competitions": [
                    {
                        "id": "ncaa-live",
                        "date": "2026-08-29T23:30Z",
                        "neutralSite": True,
                        "venue": {
                            "fullName": "AT&T Stadium",
                            "address": {"city": "Arlington", "state": "TX"},
                        },
                        "status": {
                            "period": 4,
                            "displayClock": "1:18",
                            "type": {"state": "in", "completed": False, "description": "In Progress", "shortDetail": "Q4 1:18"},
                        },
                        "situation": {
                            "possession": "251",
                            "downDistanceText": "2nd & 8",
                            "yardLineText": "MICH 36",
                        },
                        "broadcasts": [{"market": "national", "names": ["ESPN"]}],
                        "odds": [{"details": "TEX -6.5", "overUnder": 52.5}],
                        "competitors": [
                            {
                                "homeAway": "away",
                                "id": "251",
                                "score": "31",
                                "curatedRank": {"current": 12},
                                "team": {"id": "251", "abbreviation": "TEX", "shortDisplayName": "Texas", "displayName": "Texas Longhorns", "logos": [{"href": "https://example.com/ncaa-tex.png"}]},
                                "records": [{"summary": "0-0"}],
                            },
                            {
                                "homeAway": "home",
                                "id": "130",
                                "score": "28",
                                "curatedRank": {"current": 7},
                                "team": {"id": "130", "abbreviation": "MICH", "shortDisplayName": "Michigan", "displayName": "Michigan Wolverines", "logos": [{"href": "https://example.com/ncaa-mich.png"}]},
                                "records": [{"summary": "0-0"}],
                            },
                        ],
                    }
                ],
            }
        ],
    }


def _sample_f1_jolpica_bundle():
    race = {
        "season": "2026",
        "round": "7",
        "raceName": "Barcelona-Catalunya Grand Prix",
        "Circuit": {
            "circuitName": "Circuit de Barcelona-Catalunya",
            "Location": {"locality": "Montmelo", "country": "Spain"},
        },
        "date": "2026-06-14",
        "time": "13:00:00Z",
        "FirstPractice": {"date": "2026-06-12", "time": "11:30:00Z"},
        "SecondPractice": {"date": "2026-06-12", "time": "15:00:00Z"},
        "ThirdPractice": {"date": "2026-06-13", "time": "10:30:00Z"},
        "Qualifying": {"date": "2026-06-13", "time": "14:00:00Z"},
    }
    next_race = {
        "season": "2026",
        "round": "8",
        "raceName": "Austrian Grand Prix",
        "Circuit": {
            "circuitName": "Red Bull Ring",
            "Location": {"locality": "Spielberg", "country": "Austria"},
        },
        "date": "2026-06-28",
        "time": "13:00:00Z",
        "FirstPractice": {"date": "2026-06-26", "time": "11:30:00Z"},
        "Qualifying": {"date": "2026-06-27", "time": "14:00:00Z"},
    }
    schedule = {
        "MRData": {
            "RaceTable": {
                "season": "2026",
                "Races": [race, next_race],
            }
        }
    }
    results_race = json.loads(json.dumps(race))
    results_race["Results"] = [
        {
            "position": "1",
            "Driver": {"code": "RUS", "givenName": "George", "familyName": "Russell"},
            "Constructor": {"name": "Mercedes"},
            "Time": {"time": "1:32:18.441"},
            "status": "Finished",
        },
        {
            "position": "2",
            "Driver": {"code": "HAM", "givenName": "Lewis", "familyName": "Hamilton"},
            "Constructor": {"name": "Ferrari"},
            "Time": {"time": "+4.122"},
            "status": "Finished",
        },
    ]
    results = {"MRData": {"RaceTable": {"Races": [results_race]}}}
    driver_standings = {
        "MRData": {
            "StandingsTable": {
                "StandingsLists": [
                    {
                        "DriverStandings": [
                            {"position": "1", "points": "142", "wins": "3", "Driver": {"code": "ANT"}},
                            {"position": "2", "points": "130", "wins": "2", "Driver": {"code": "RUS"}},
                        ]
                    }
                ]
            }
        }
    }
    constructor_standings = {
        "MRData": {
            "StandingsTable": {
                "StandingsLists": [
                    {
                        "ConstructorStandings": [
                            {"position": "1", "points": "272", "wins": "5", "Constructor": {"name": "Mercedes"}}
                        ]
                    }
                ]
            }
        }
    }
    return {
        "version": "sports-dashboard-f1-jolpica-v1",
        "cache_key": "sample",
        "fetched_at": "2026-06-14T12:00:00+00:00",
        "schedule": schedule,
        "results": results,
        "driver_standings": driver_standings,
        "constructor_standings": constructor_standings,
    }


def _sample_openf1_snapshot():
    return {
        "drivers": [
            {"driver_number": 63, "name_acronym": "RUS", "team_name": "Mercedes", "team_colour": "27F4D2"},
            {"driver_number": 44, "name_acronym": "HAM", "team_name": "Ferrari", "team_colour": "E80020"},
        ],
        "position": [
            {"driver_number": 44, "position": 2, "date": "2026-06-14T13:02:00+00:00"},
            {"driver_number": 63, "position": 1, "date": "2026-06-14T13:02:01+00:00"},
        ],
        "intervals": [
            {"driver_number": 63, "gap_to_leader": None, "interval": None, "date": "2026-06-14T13:02:02+00:00"},
            {"driver_number": 44, "gap_to_leader": 1.204, "interval": 1.204, "date": "2026-06-14T13:02:02+00:00"},
        ],
        "session_result": [],
        "weather": [
            {"date": "2026-06-14T13:02:02+00:00", "air_temperature": 26.5, "track_temperature": 39.2, "rainfall": 0}
        ],
    }


def _sample_football_data_match():
    return {
        "utcDate": "2026-06-11T19:00:00Z",
        "status": "TIMED",
        "stage": "GROUP_STAGE",
        "group": "GROUP_A",
        "homeTeam": {"id": 758, "name": "Mexico", "shortName": "Mexico", "tla": "MEX"},
        "awayTeam": {"id": 1577, "name": "South Africa", "shortName": "South Africa", "tla": "RSA"},
        "score": {"fullTime": {"home": None, "away": None}},
    }


def _sample_worldcup_espn_scoreboard_payload():
    return {
        "events": [
            {
                "id": "760415",
                "date": "2026-06-11T19:00Z",
                "season": {"slug": "fifa-world-cup"},
                "links": [
                    {
                        "rel": ["summary"],
                        "href": "https://www.espn.com/soccer/match/_/gameId/760415/mex-rsa",
                        "text": "Summary",
                    }
                ],
                "competitions": [
                    {
                        "id": "760415",
                        "date": "2026-06-11T19:00Z",
                        "status": {
                            "period": 2,
                            "type": {
                                "state": "post",
                                "completed": True,
                                "name": "STATUS_FULL_TIME",
                                "description": "Full Time",
                                "shortDetail": "FT",
                                "detail": "FT",
                            },
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "2",
                                "advance": True,
                                "team": {
                                    "abbreviation": "MEX",
                                    "shortDisplayName": "Mexico",
                                    "displayName": "Mexico",
                                    "logo": "https://example.com/mex.png",
                                },
                            },
                            {
                                "homeAway": "away",
                                "score": "0",
                                "advance": False,
                                "team": {
                                    "abbreviation": "RSA",
                                    "shortDisplayName": "South Africa",
                                    "displayName": "South Africa",
                                    "logo": "https://example.com/rsa.png",
                                },
                            },
                        ],
                    }
                ],
            },
            {
                "id": "760414",
                "date": "2026-06-12T02:00Z",
                "competitions": [
                    {
                        "id": "760414",
                        "date": "2026-06-12T02:00Z",
                        "links": [
                            {
                                "rel": ["gamecast"],
                                "href": "https://www.espn.com/soccer/gamecast/_/gameId/760414/kor-cze",
                                "text": "Gamecast",
                            }
                        ],
                        "status": {
                            "period": 1,
                            "type": {
                                "state": "in",
                                "completed": False,
                                "name": "STATUS_FIRST_HALF",
                                "description": "First Half",
                                "shortDetail": "9'",
                                "detail": "9'",
                            },
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "0",
                                "team": {
                                    "abbreviation": "KOR",
                                    "shortDisplayName": "South Korea",
                                    "displayName": "South Korea",
                                },
                            },
                            {
                                "homeAway": "away",
                                "score": "0",
                                "team": {
                                    "abbreviation": "CZE",
                                    "shortDisplayName": "Czechia",
                                    "displayName": "Czechia",
                                },
                            },
                        ],
                    }
                ],
            },
        ]
    }


def _sample_worldcup_odds_event():
    return {
        "id": "mex-rsa",
        "sport_key": "soccer_fifa_world_cup",
        "commence_time": "2026-06-11T19:00:00Z",
        "home_team": "Mexico",
        "away_team": "South Africa",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Mexico", "price": 1.8},
                            {"name": "Draw", "price": 3.25},
                            {"name": "South Africa", "price": 4.5},
                        ],
                    }
                ],
            }
        ],
    }


def _sample_worldcup_odds_api_io_event():
    return {
        "id": 66456904,
        "home": "Mexico",
        "away": "South Africa",
        "date": "2026-06-11T19:00:00Z",
        "status": "pending",
        "league": {"name": "International - FIFA World Cup", "slug": "international-fifa-world-cup"},
        "bookmakers": {
            "Bet365": [
                {
                    "name": "ML",
                    "odds": [
                        {"home": "1.400", "draw": "4.500", "away": "8.000"},
                    ],
                }
            ]
        },
    }


def _sample_lpl_odds_api_io_event(
    home="Bilibili Gaming",
    away="Edward Gaming",
    date="2026-06-03T09:00:00Z",
    home_odds="1.650",
    away_odds="2.100",
):
    return {
        "id": 71827048,
        "home": home,
        "away": away,
        "date": date,
        "status": "pending",
        "league": {"name": "League of Legends - Split 2", "slug": "league-of-legends-split-2"},
        "bookmakers": {
            "Bet365": [
                {
                    "name": "ML",
                    "odds": [
                        {"home": home_odds, "away": away_odds},
                    ],
                }
            ]
        },
    }


def _sports_dashboard_tmp(name):
    path = Path(__file__).resolve().parents[1] / "tmp" / "sports_dashboard_tests" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_live_refresh_state(path, version, **overrides):
    payload = {
        "version": version,
        "has_live": True,
        "live_until": "2026-05-26T08:00:00+00:00",
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_live_refresh_state_reads_active_source_files():
    cases = [
        (
            "worldcup_live_state.json",
            "sports-dashboard-worldcup-live-v1",
            "worldCupLiveRefreshIntervalSeconds",
            120,
        ),
        (
            "lpl_live_state.json",
            "sports-dashboard-lpl-live-v1",
            "lplLiveRefreshIntervalSeconds",
            180,
        ),
        (
            "msi_live_state.json",
            "sports-dashboard-msi-live-v1",
            "lplLiveRefreshIntervalSeconds",
            240,
        ),
        (
            "lck_live_state.json",
            "sports-dashboard-lck-live-v1",
            "lplLiveRefreshIntervalSeconds",
            210,
        ),
        (
            "ewc_live_state.json",
            "sports-dashboard-ewc-live-v1",
            "ewcLiveRefreshIntervalSeconds",
            150,
        ),
        (
            "valve_esports_live_state.json",
            "sports-dashboard-valve-esports-live-v1",
            "valveEsportsLiveRefreshIntervalSeconds",
            270,
        ),
        (
            "nba_live_state.json",
            "sports-dashboard-nba-live-v1",
            "nbaLiveRefreshIntervalSeconds",
            300,
        ),
        (
            "offseason_hub_live.json",
            "sports-dashboard-offseason-hub-v1",
            "offseasonHubLiveRefreshIntervalSeconds",
            360,
        ),
        (
            "f1_live_state.json",
            "sports-dashboard-f1-live-v1",
            "f1LiveRefreshIntervalSeconds",
            420,
        ),
    ]
    current_dt = datetime(2026, 5, 26, 7, 0, tzinfo=timezone.utc)

    for file_name, version, setting_key, interval_seconds in cases:
        plugin = _plugin()
        cache_dir = _sports_dashboard_tmp(f"live_refresh_hook_{file_name}")
        plugin._sports_dashboard_cache_dir = lambda cache_dir=cache_dir: cache_dir
        _write_live_refresh_state(cache_dir / file_name, version)

        state = plugin.get_live_refresh_state(
            {"id": "sports", setting_key: str(interval_seconds)},
            current_dt,
        )

        assert state == {"active": True, "interval_seconds": interval_seconds}


def test_live_refresh_state_defaults_to_one_minute_interval():
    plugin = _plugin()
    cache_dir = _sports_dashboard_tmp("live_refresh_hook_default")
    plugin._sports_dashboard_cache_dir = lambda: cache_dir
    _write_live_refresh_state(cache_dir / "lpl_live_state.json", "sports-dashboard-lpl-live-v1")

    state = plugin.get_live_refresh_state(
        {"id": "sports"},
        datetime(2026, 5, 26, 7, 0, tzinfo=timezone.utc),
    )

    assert state == {"active": True, "interval_seconds": 60}


def test_live_refresh_state_respects_disabled_source():
    plugin = _plugin()
    cache_dir = _sports_dashboard_tmp("live_refresh_hook_disabled")
    plugin._sports_dashboard_cache_dir = lambda: cache_dir
    _write_live_refresh_state(
        cache_dir / "offseason_hub_live.json",
        "sports-dashboard-offseason-hub-v1",
        status="LIVE",
        sport="WNBA",
    )

    state = plugin.get_live_refresh_state(
        {"id": "sports", "offseasonHubLiveRefreshEnabled": "false"},
        datetime(2026, 5, 26, 7, 0, tzinfo=timezone.utc),
    )

    assert state is None


def test_live_refresh_state_ignores_missing_or_expired_state():
    plugin = _plugin()
    cache_dir = _sports_dashboard_tmp("live_refresh_hook_expired")
    plugin._sports_dashboard_cache_dir = lambda: cache_dir
    current_dt = datetime(2026, 5, 26, 7, 0, tzinfo=timezone.utc)

    assert plugin.get_live_refresh_state({"id": "sports"}, current_dt) is None

    _write_live_refresh_state(
        cache_dir / "lpl_live_state.json",
        "sports-dashboard-lpl-live-v1",
        live_until="2026-05-26T06:59:00+00:00",
    )

    assert plugin.get_live_refresh_state({"id": "sports"}, current_dt) is None

def _fresh_lpl_frame_time(minutes_ago=0):
    frame_time = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return frame_time.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_lpl_parser_converts_utc_starts_to_california_time():
    la = ZoneInfo("America/Los_Angeles")

    events = SportsDashboard._parse_lpl_events(_sample_payload(), la)

    assert events[0]["team_a"] == "TT"
    assert events[0]["start"].strftime("%Y-%m-%d %H:%M") == "2026-06-02 02:00"
    assert events[1]["team_a"] == "BLG"
    assert events[1]["start"].strftime("%Y-%m-%d %H:%M") == "2026-06-03 02:00"
    assert events[1]["team_a_logo"] == "https://example.com/blg.png"
    assert events[1]["team_b_logo"] == "https://example.com/edg.png"
    assert events[1]["best_of"] == 5
    assert events[1]["event_id"] == "match-blg-edg"
    assert events[1]["match_id"] == "match-blg-edg"


def test_select_lpl_events_returns_next_match_and_recent_result():
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_lpl_events(_sample_payload(), la)
    now = datetime(2026, 6, 2, 12, 0, tzinfo=la)

    selected = SportsDashboard._select_lpl_events(events, now)

    assert selected["main"]["team_a"] == "BLG"
    assert selected["upcoming"][0]["team_b"] == "EDG"
    assert selected["recent"][0]["team_a"] == "TT"
    assert SportsDashboard._result_label(selected["recent"][0]) == "TT 2-3 LGD"


def test_select_lck_events_does_not_inject_lpl_msi_featured_page():
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)

    selected = SportsDashboard._select_lck_events([], now)

    assert selected["main"] is None
    assert selected["featured_event"] is None
    assert selected["featured_event_page"] is False


def test_lol_sidebar_prefers_lck_upcoming_over_lpl_offseason_feature():
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    lpl_selected = SportsDashboard._select_lpl_events([], now)
    lck_event = {
        "start": now + timedelta(hours=2),
        "state": "unstarted",
        "team_a": "GEN",
        "team_b": "T1",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": None,
        "wins_b": None,
        "best_of": 3,
        "block": "Regular Season",
    }
    lck_selected = SportsDashboard._select_lck_events([lck_event], now)

    choice = SportsDashboard._select_lol_esports_sidebar(
        [
            {"league_key": "LPL", "selected": lpl_selected, "source_state": "CACHE DATA", "priority": 0},
            {"league_key": "LCK", "selected": lck_selected, "source_state": "LCK LIVE DATA", "priority": 1},
        ],
        now,
    )

    assert choice["league_key"] == "LCK"


def test_lol_sidebar_override_forces_lck(monkeypatch):
    plugin = _plugin()
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    lpl_event = {
        "start": now + timedelta(hours=3),
        "state": "unstarted",
        "team_a": "BLG",
        "team_b": "TES",
        "wins_a": None,
        "wins_b": None,
        "best_of": 3,
        "block": "Split 2",
    }
    lck_event = {
        "start": now - timedelta(days=1),
        "state": "completed",
        "team_a": "GEN",
        "team_b": "T1",
        "wins_a": 2,
        "wins_b": 1,
        "best_of": 3,
        "block": "Week 2",
    }
    plugin._load_lpl_events = lambda _settings, _timezone_info: ([lpl_event], "LIVE DATA")
    plugin._load_lck_events = lambda _settings, _timezone_info: ([lck_event], "LCK LIVE DATA")
    plugin._load_msi_events = lambda _settings, _timezone_info, _now: ([], "MSI NO DATA", None)
    plugin._attach_lpl_odds = lambda events, *_args, **_kwargs: events

    choice = plugin._load_lol_esports_sidebar(
        {"lolEsportsSidebarOverride": "LCK"},
        FakeDeviceConfig(timezone="UTC"),
        timezone.utc,
        now,
    )

    assert choice["league_key"] == "LCK"
    assert choice["selected"]["main"]["team_a"] == "GEN"

def test_lol_sidebar_defaults_to_lpl_when_lck_has_no_schedule():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 2, 12, 0, tzinfo=la)
    lpl_selected = SportsDashboard._select_lpl_events(SportsDashboard._parse_lpl_events(_sample_payload(), la), now)
    lck_selected = SportsDashboard._select_lck_events([], now)

    choice = SportsDashboard._select_lol_esports_sidebar(
        [
            {"league_key": "LPL", "selected": lpl_selected, "source_state": "LIVE DATA", "priority": 0},
            {"league_key": "LCK", "selected": lck_selected, "source_state": "LCK NO DATA", "priority": 1},
        ],
        now,
    )

    assert choice["league_key"] == "LPL"


def test_ewc_competitions_parser_extracts_official_cards():
    la = ZoneInfo("America/Los_Angeles")

    events = SportsDashboard._parse_ewc_competitions_html(
        _sample_ewc_competitions_html(),
        la,
        DEFAULT_EWC_COMPETITIONS_URL,
    )

    assert [event["game"] for event in events] == ["Apex Legends", "Dota 2", "Fatal Fury"]
    assert events[0]["start"].strftime("%Y-%m-%d %H:%M") == "2026-07-07 00:00"
    assert events[0]["end"].strftime("%Y-%m-%d %H:%M") == "2026-07-11 23:59"
    assert events[0]["prize_pool"] == "$2,000,000"
    assert events[0]["participant_count"] == 40
    assert events[0]["participant_label"] == "clubs"
    assert events[0]["logo_url"] == "https://d3h9qea4qy4169.cloudfront.net/ALGS_Split_1_Playoff_Logo_Black_b705b1f3d8.png"
    assert events[1]["logo_url"].endswith("/Game_dota2_Variant_Dark_82f230e51b.svg")
    assert events[1]["source_url"].endswith("/en/competitions/2026/dota2")


def test_ewc_detail_schedule_parser_pairs_official_match_rows():
    la = ZoneInfo("America/Los_Angeles")

    matches = SportsDashboard._parse_ewc_detail_schedule_html(
        _sample_ewc_detail_schedule_html(),
        la,
        "valorant",
        "VALORANT",
        "https://esportsworldcup.com/en/competitions/2026/valorant",
    )

    assert len(matches) == 3
    upcoming = next(match for match in matches if match["stage"] == "Group A - Opening Match #1")
    assert upcoming["kind"] == "match"
    assert upcoming["event_id"] == "ewc-2026-valorant-20260702-1100-group-a-opening-match-1"
    assert upcoming["game"] == "VALORANT"
    assert upcoming["slug"] == "valorant"
    assert upcoming["source_url"].endswith("/en/competitions/2026/valorant")
    assert upcoming["start"].strftime("%Y-%m-%d %H:%M") == "2026-07-02 11:00"
    assert upcoming["end"].strftime("%Y-%m-%d %H:%M") == "2026-07-02 14:00"
    assert upcoming["status"] == "UPCOMING"
    assert upcoming["team_a"] == "Team RRQ"
    assert upcoming["team_b"] == "100 Thieves"
    assert upcoming["score_a"] is None
    assert upcoming["score_b"] is None
    assert upcoming["team_a_logo"] == "https://tds-cdn.ewc.efg.gg/assets/clubs/2068035497296400384/LOGO_LIGHT.png"
    assert upcoming["team_b_logo"] == "https://tds-cdn.ewc.efg.gg/assets/clubs/2068035456414519296/LOGO_LIGHT.png"

    live = next(match for match in matches if match["stage"] == "Group B - Opening Match #1")
    assert live["status"] == "LIVE"
    assert live["score_a"] == 1
    assert live["score_b"] == 0

    completed = next(match for match in matches if match["stage"] == "Round 1 1")
    assert completed["status"] == "COMPLETED"
    assert completed["team_a"] == "The MongolZ"
    assert completed["team_b"] == "FUT Esports"
    assert completed["score_a"] == 0
    assert completed["score_b"] == 1


def test_select_ewc_events_prioritizes_and_rotates_live_matches():
    la = ZoneInfo("America/Los_Angeles")
    matches = SportsDashboard._parse_ewc_detail_schedule_html(
        _sample_ewc_detail_schedule_html(),
        la,
        "valorant",
        "VALORANT",
        "https://esportsworldcup.com/en/competitions/2026/valorant",
    )
    live_match = next(match for match in matches if match["status"] == "LIVE")
    second_live = dict(live_match)
    second_live.update(
        {
            "event_id": "ewc-2026-valorant-20260702-1100-group-c-opening-match-1",
            "stage": "Group C - Opening Match #1",
            "team_a": "FNATIC",
            "team_b": "Rex Regum Qeon",
            "score_a": 0,
            "score_b": 0,
        }
    )
    now = datetime(2026, 7, 2, 11, 20, tzinfo=la)

    selected_first = SportsDashboard._select_ewc_events([live_match, second_live], now, 21, rotation_seed=0)
    selected_second = SportsDashboard._select_ewc_events([live_match, second_live], now, 21, rotation_seed=1)

    assert selected_first["display_window_active"] is True
    assert [match["team_a"] for match in selected_first["live_matches"]] == ["BBL Esports", "FNATIC"]
    assert selected_first["main_match"]["team_a"] == "BBL Esports"
    assert selected_first["main"] == selected_first["main_match"]
    assert selected_second["main_match"]["team_a"] == "FNATIC"


def test_select_ewc_events_uses_next_match_and_rotates_same_start_time():
    la = ZoneInfo("America/Los_Angeles")
    matches = SportsDashboard._parse_ewc_detail_schedule_html(
        _sample_ewc_detail_schedule_html(),
        la,
        "valorant",
        "VALORANT",
        "https://esportsworldcup.com/en/competitions/2026/valorant",
    )
    next_match = next(match for match in matches if match["status"] == "UPCOMING")
    same_time_match = dict(next_match)
    same_time_match.update(
        {
            "event_id": "ewc-2026-valorant-20260702-1100-group-a-opening-match-2",
            "stage": "Group A - Opening Match #2",
            "team_a": "Sentinels",
            "team_b": "Bilibili Gaming",
        }
    )
    later_match = dict(next_match)
    later_match.update(
        {
            "event_id": "ewc-2026-valorant-20260702-1400-group-d-opening-match-1",
            "start": datetime(2026, 7, 2, 14, 0, tzinfo=la),
            "end": datetime(2026, 7, 2, 17, 0, tzinfo=la),
            "stage": "Group D - Opening Match #1",
            "team_a": "Paper Rex",
            "team_b": "Gen.G",
        }
    )
    now = datetime(2026, 7, 2, 9, 0, tzinfo=la)

    selected_first = SportsDashboard._select_ewc_events([later_match, same_time_match, next_match], now, 21, rotation_seed=0)
    selected_second = SportsDashboard._select_ewc_events([later_match, same_time_match, next_match], now, 21, rotation_seed=1)

    assert selected_first["display_window_active"] is True
    assert [match["team_a"] for match in selected_first["upcoming_matches"][:2]] == ["Team RRQ", "Sentinels"]
    assert selected_first["main_match"]["team_a"] == "Team RRQ"
    assert selected_second["main_match"]["team_a"] == "Sentinels"


def test_select_ewc_events_rotates_overlapping_match_lists_by_game():
    la = ZoneInfo("America/Los_Angeles")
    valorant = {
        "kind": "match",
        "event_id": "ewc-2026-valorant-bbl-edg",
        "match_id": "ewc-2026-valorant-bbl-edg",
        "game": "VALORANT",
        "slug": "valorant",
        "start": datetime(2026, 7, 8, 11, 0, tzinfo=la),
        "end": datetime(2026, 7, 8, 14, 0, tzinfo=la),
        "status": "LIVE",
        "stage": "Group B",
        "team_a": "BBL Esports",
        "team_b": "EDward Gaming",
        "score_a": 1,
        "score_b": 0,
    }
    valorant_next = dict(valorant)
    valorant_next.update(
        {
            "event_id": "ewc-2026-valorant-nrg-prx",
            "match_id": "ewc-2026-valorant-nrg-prx",
            "start": datetime(2026, 7, 8, 15, 0, tzinfo=la),
            "end": datetime(2026, 7, 8, 18, 0, tzinfo=la),
            "status": "UPCOMING",
            "team_a": "NRG",
            "team_b": "Paper Rex",
            "score_a": None,
            "score_b": None,
        }
    )
    apex = {
        "kind": "match",
        "event_id": "ewc-2026-apex-legends-final-a",
        "match_id": "ewc-2026-apex-legends-final-a",
        "game": "Apex Legends",
        "slug": "apex-legends",
        "start": datetime(2026, 7, 8, 11, 0, tzinfo=la),
        "end": datetime(2026, 7, 8, 14, 0, tzinfo=la),
        "status": "LIVE",
        "stage": "Finals Match 1",
        "team_a": "Alliance",
        "team_b": "Team Falcons",
        "score_a": 2,
        "score_b": 1,
    }
    apex_recent = dict(apex)
    apex_recent.update(
        {
            "event_id": "ewc-2026-apex-legends-group-a",
            "match_id": "ewc-2026-apex-legends-group-a",
            "start": datetime(2026, 7, 8, 9, 0, tzinfo=la),
            "end": datetime(2026, 7, 8, 10, 0, tzinfo=la),
            "status": "COMPLETED",
            "stage": "Group A",
            "team_a": "NRG",
            "team_b": "Fnatic",
            "score_a": 1,
            "score_b": 0,
        }
    )
    now = datetime(2026, 7, 8, 11, 15, tzinfo=la)

    selected_apex = SportsDashboard._select_ewc_events(
        [valorant, valorant_next, apex, apex_recent],
        now,
        21,
        rotation_seed=0,
    )
    selected_valorant = SportsDashboard._select_ewc_events(
        [valorant, valorant_next, apex, apex_recent],
        now,
        21,
        rotation_seed=1,
    )

    assert selected_apex["selected_match_group"]["slug"] == "apex-legends"
    assert selected_apex["main_match"]["team_a"] == "Alliance"
    assert [match["game"] for match in selected_apex["live_matches"]] == ["Apex Legends"]
    assert [match["game"] for match in selected_apex["recent_matches"]] == ["Apex Legends"]
    assert selected_apex["upcoming_matches"] == []

    assert selected_valorant["selected_match_group"]["slug"] == "valorant"
    assert selected_valorant["main_match"]["team_a"] == "BBL Esports"
    assert [match["game"] for match in selected_valorant["live_matches"]] == ["VALORANT"]
    assert [match["game"] for match in selected_valorant["upcoming_matches"]] == ["VALORANT"]
    assert selected_valorant["recent_matches"] == []

def test_ewc_live_state_file_marks_live_match_group():
    plugin = _plugin()
    cache_dir = _sports_dashboard_tmp("ewc_live_state")
    plugin._sports_dashboard_cache_dir = lambda: cache_dir
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 7, 8, 11, 15, tzinfo=la)
    live_match = {
        "kind": "match",
        "event_id": "ewc-2026-apex-legends-final-a",
        "match_id": "ewc-2026-apex-legends-final-a",
        "game": "Apex Legends",
        "slug": "apex-legends",
        "start": datetime(2026, 7, 8, 11, 0, tzinfo=la),
        "end": datetime(2026, 7, 8, 14, 0, tzinfo=la),
        "status": "LIVE",
        "stage": "Finals Match 1",
        "team_a": "Alliance",
        "team_b": "Team Falcons",
        "score_a": 2,
        "score_b": 1,
    }
    selected = SportsDashboard._select_ewc_events([live_match], now, 21, rotation_seed=0)

    plugin._write_ewc_live_state(selected, now, "EWC DETAIL")

    state = json.loads((cache_dir / "ewc_live_state.json").read_text(encoding="utf-8"))
    assert state["version"] == "sports-dashboard-ewc-live-v1"
    assert state["has_live"] is True
    assert state["event_id"] == "ewc-2026-apex-legends-final-a"
    assert state["game"] == "Apex Legends"
    assert state["team_a"] == "Alliance"
    assert state["team_b"] == "Team Falcons"
    assert state["live_until"] == "2026-07-08T21:00:00+00:00"


def test_ewc_competition_window_without_matches_is_not_live_state():
    plugin = _plugin()
    cache_dir = _sports_dashboard_tmp("ewc_event_window_state")
    plugin._sports_dashboard_cache_dir = lambda: cache_dir
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 7, 10, 18, 0, tzinfo=la)
    competition = {
        "event_id": "ewc-2026-valorant",
        "game": "VALORANT",
        "slug": "valorant",
        "start": datetime(2026, 7, 2, 0, 0, tzinfo=la),
        "end": datetime(2026, 7, 12, 23, 59, tzinfo=la),
        "status": "ONGOING",
        "participant_count": 16,
        "participant_label": "clubs",
    }
    selected = SportsDashboard._select_ewc_events(
        [competition], now, 21, rotation_seed=0
    )

    plugin._write_ewc_live_state(selected, now, "EWC LIVE")

    state = json.loads((cache_dir / "ewc_live_state.json").read_text(encoding="utf-8"))
    assert selected["main_match"] is None
    assert state["has_live"] is False
    assert state["live_until"] is None


def test_ewc_competition_window_renders_as_active_calendar_not_live_match():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 7, 10, 18, 0, tzinfo=la)
    selected = SportsDashboard._select_ewc_events(
        [
            {
                "event_id": "ewc-2026-valorant",
                "game": "VALORANT",
                "slug": "valorant",
                "start": datetime(2026, 7, 2, 0, 0, tzinfo=la),
                "end": datetime(2026, 7, 12, 23, 59, tzinfo=la),
                "status": "ONGOING",
                "participant_count": 16,
                "participant_label": "clubs",
                "prize_pool": "$2,000,000",
            }
        ],
        now,
        21,
        rotation_seed=0,
    )
    image = Image.new("RGB", (800, 480), COLORS["paper"])
    seen_texts = []
    status_pills = []
    original_fit_text_ellipsis = plugin._fit_text_ellipsis

    def capture_fit_text_ellipsis(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text_ellipsis(draw_obj, text, *args, **kwargs)

    def capture_status_pill(_draw, _x, _y, text, is_live):
        status_pills.append((str(text), bool(is_live)))

    plugin._fit_text_ellipsis = capture_fit_text_ellipsis
    plugin._draw_status_pill = capture_status_pill

    plugin._draw_ewc_sidebar(image, 552, selected, "EWC LIVE", now)

    assert status_pills == [("ACTIVE", False)]
    assert "CURRENT EVENT" in seen_texts
    assert "EVENT IN PROGRESS" in seen_texts
    assert "LIVE EVENT" not in seen_texts
    assert "EWC LIVE" not in seen_texts
    assert "OFFICIAL DATA" in seen_texts


def test_ewc_sidebar_render_draws_detail_match_names_and_official_logos(monkeypatch, tmp_path):
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    matches = SportsDashboard._parse_ewc_detail_schedule_html(
        _sample_ewc_detail_schedule_html(),
        la,
        "valorant",
        "VALORANT",
        "https://esportsworldcup.com/en/competitions/2026/valorant",
    )
    selected = SportsDashboard._select_ewc_events(matches, datetime(2026, 7, 2, 9, 0, tzinfo=la), 21, rotation_seed=0)
    image = Image.new("RGB", (800, 480), COLORS["paper"])
    seen_texts = []
    original_fit_text_ellipsis = plugin._fit_text_ellipsis

    def record_fit_text_ellipsis(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text_ellipsis(draw_obj, text, *args, **kwargs)

    remote_logo_urls = []
    source = Image.new("RGBA", (12, 12), (12, 34, 56, 255))
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    logo_bytes = buffer.getvalue()

    def record_remote_logo(logo_url, timeout):
        remote_logo_urls.append(logo_url)
        return logo_bytes

    monkeypatch.setattr(plugin, "_fit_text_ellipsis", record_fit_text_ellipsis)
    monkeypatch.setattr(plugin, "_load_local_team_logo", lambda *_args: None)
    monkeypatch.setattr(plugin, "_team_logo_disk_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(SportsDashboard, "_fetch_remote_image_bytes", record_remote_logo)
    TEAM_LOGO_CACHE.clear()

    plugin._draw_ewc_sidebar(image, 552, selected, "EWC DETAIL", datetime(2026, 7, 2, 9, 0, tzinfo=la))

    assert remote_logo_urls == [
        "https://www.esportsworldcup.com/_next/image?url=https%3A%2F%2Ftds-cdn.ewc.efg.gg%2Fassets%2Fclubs%2F2068035497296400384%2FLOGO_LIGHT.png&w=128&q=50",
        "https://www.esportsworldcup.com/_next/image?url=https%3A%2F%2Ftds-cdn.ewc.efg.gg%2Fassets%2Fclubs%2F2068035456414519296%2FLOGO_LIGHT.png&w=128&q=50",
    ]
    assert len(list(tmp_path.iterdir())) == 2
    assert "Team RRQ" in seen_texts
    assert "100 Thieves" in seen_texts
    assert image.getpixel((560, 80)) != COLORS["paper"]


def test_ewc_match_focus_card_draws_game_logo_and_name_without_box_chrome(monkeypatch):
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    matches = SportsDashboard._parse_ewc_detail_schedule_html(
        _sample_ewc_detail_schedule_html(),
        la,
        "valorant",
        "VALORANT",
        "https://esportsworldcup.com/en/competitions/2026/valorant",
    )
    match = next(item for item in matches if item["status"] == "UPCOMING")
    image = Image.new("RGB", (800, 480), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    logo_calls = []
    text_box_calls = []
    original_fit_text_ellipsis = plugin._fit_text_ellipsis
    original_draw_text_in_box = plugin._draw_text_in_box

    def record_fit_text_ellipsis(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text_ellipsis(draw_obj, text, *args, **kwargs)

    def load_game_logo(event, size):
        logo_calls.append((event.get("slug"), size))
        return Image.new("RGBA", (24, 14), (0, 163, 173, 255))

    def record_draw_text_in_box(draw_obj, box, text, font, color, align="left"):
        text_box_calls.append((str(text), box, align))
        return original_draw_text_in_box(draw_obj, box, text, font, color, align=align)

    monkeypatch.setattr(plugin, "_fit_text_ellipsis", record_fit_text_ellipsis)
    monkeypatch.setattr(plugin, "_load_ewc_game_logo", load_game_logo)
    monkeypatch.setattr(plugin, "_draw_text_in_box", record_draw_text_in_box)
    monkeypatch.setattr(plugin, "_load_local_team_logo", lambda *_args: None)
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda *_args: None)

    plugin._draw_ewc_match_focus_card(image, draw, 556, 244, 78, match, datetime(2026, 7, 2, 9, 0, tzinfo=la), False)

    assert logo_calls == [("valorant", (46, 16))]
    assert "VALORANT" in seen_texts
    game_text_call = next(call for call in text_box_calls if call[0] == "VALORANT")
    assert game_text_call[1] == (651, 114, 765, 135)
    assert game_text_call[2] == "right"
    assert image.getpixel((613, 124)) == (0, 163, 173)
    assert image.getpixel((586, 114)) == COLORS["panel"]
    assert image.getpixel((646, 114)) == COLORS["panel"]


def test_ewc_team_logo_url_filters_non_official_sources():
    assert SportsDashboard._ewc_team_logo_url(
        {"team_a_logo": "https://tds-cdn.ewc.efg.gg/assets/clubs/2068035497296400384/LOGO_LIGHT.png"},
        "a",
    ) == "https://www.esportsworldcup.com/_next/image?url=https%3A%2F%2Ftds-cdn.ewc.efg.gg%2Fassets%2Fclubs%2F2068035497296400384%2FLOGO_LIGHT.png&w=128&q=50"
    assert SportsDashboard._ewc_team_logo_url({"team_a_logo": "https://example.com/rrq.png"}, "a") == ""


def test_ewc_game_logo_slug_and_path_use_official_assets():
    assert SportsDashboard._ewc_game_logo_slug({"slug": "cs2", "game": "Counter-Strike 2"}) == "cs2"
    assert SportsDashboard._ewc_game_logo_slug({"game": "EA Sports FC 26"}) == "eafc"
    assert SportsDashboard._ewc_game_logo_slug("street-fighter-6") == "street-fighter6"
    path = Path(SportsDashboard._ewc_game_logo_path({"slug": "street-fighter6"}))
    assert path.parent == Path(LOCAL_EWC_GAME_LOGO_DIR)
    assert path.name == "street-fighter6.png"


def test_ewc_current_short_slugs_use_clean_titles():
    assert SportsDashboard._ewc_game_name("eafc") == "EA Sports FC 26"
    assert SportsDashboard._ewc_game_name("mlbb") == "Mobile Legends: Bang Bang"
    assert SportsDashboard._ewc_game_name("mlbb-women") == "MLBB Women"
    assert SportsDashboard._ewc_game_name("cs2") == "Counter-Strike 2"
    assert SportsDashboard._ewc_game_name("pmwc") == "PUBG Mobile World Cup"
    assert SportsDashboard._ewc_game_name("street-fighter6") == "Street Fighter 6"
    assert SportsDashboard._ewc_game_name("cod-blackops") == "Call of Duty: Black Ops 7"

def test_select_ewc_events_activates_upcoming_window_and_live_range():
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_ewc_competitions_html(_sample_ewc_competitions_html(), la, DEFAULT_EWC_COMPETITIONS_URL)

    upcoming_selected = SportsDashboard._select_ewc_events(events, datetime(2026, 6, 24, 12, 0, tzinfo=la), upcoming_window_days=21)
    assert upcoming_selected["display_window_active"] is True
    assert upcoming_selected["main"]["game"] == "Apex Legends"
    assert upcoming_selected["upcoming"][0]["game"] == "Apex Legends"

    live_selected = SportsDashboard._select_ewc_events(events, datetime(2026, 7, 8, 12, 0, tzinfo=la), upcoming_window_days=21)
    assert [event["game"] for event in live_selected["live"][:3]] == ["Apex Legends", "Dota 2", "Fatal Fury"]
    assert live_selected["main"]["game"] == "Apex Legends"


def test_right_sidebar_prefers_ewc_over_valve_but_not_lpl_upcoming():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    ewc_selected = SportsDashboard._select_ewc_events(
        [
            {
                "event_id": "ewc-apex",
                "game": "Apex Legends",
                "start": now + timedelta(days=2),
                "end": now + timedelta(days=6),
                "status": "UPCOMING",
                "participant_count": 40,
                "participant_label": "clubs",
            }
        ],
        now,
        upcoming_window_days=21,
    )
    valve_selected = {
        "cards": [
            {
                "series": "CS",
                "event_name": "CS Major",
                "main": {"match_id": "cs-1", "start": now, "team_a": "A", "team_b": "B"},
                "window_active": True,
                "status": "ACTIVE",
                "source_state": "CSAPI LIVE",
            }
        ]
    }
    lpl_offseason = SportsDashboard._select_lpl_events([], now)

    choice = SportsDashboard._select_right_esports_sidebar(
        [{"league_key": "LPL", "selected": lpl_offseason, "source_state": "CACHE DATA", "priority": 0}],
        valve_selected,
        "CSAPI LIVE",
        now,
        ewc_card={"selected": ewc_selected, "source_state": "EWC CACHE", "priority": 2},
    )
    assert choice["kind"] == "ewc"

    lpl_event = {
        "start": now + timedelta(hours=2),
        "state": "unstarted",
        "team_a": "BLG",
        "team_b": "TES",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": None,
        "wins_b": None,
        "best_of": 3,
        "block": "Split 2",
    }
    lpl_selected = SportsDashboard._select_lpl_events([lpl_event], now)
    choice = SportsDashboard._select_right_esports_sidebar(
        [{"league_key": "LPL", "selected": lpl_selected, "source_state": "LIVE DATA", "priority": 0}],
        valve_selected,
        "CSAPI LIVE",
        now,
        ewc_card={"selected": ewc_selected, "source_state": "EWC CACHE", "priority": 2},
    )
    assert choice["kind"] == "lol"
    assert choice["choice"]["league_key"] == "LPL"


def test_right_sidebar_uses_ewc_next_match_when_no_live_right_competition():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    lpl_offseason = {
        "league_key": "LPL",
        "selected": SportsDashboard._select_lpl_events([], now),
        "source_state": "CACHE DATA",
        "priority": 0,
    }
    lck_offseason = {
        "league_key": "LCK",
        "selected": SportsDashboard._select_lck_events([], now),
        "source_state": "LCK NO DATA",
        "priority": 1,
    }
    ewc_selected = SportsDashboard._select_ewc_events(
        [
            {
                "event_id": "ewc-apex",
                "game": "Apex Legends",
                "start": now + timedelta(days=2),
                "end": now + timedelta(days=6),
                "status": "UPCOMING",
            }
        ],
        now,
        upcoming_window_days=21,
    )
    break_card = {
        "series": "CS",
        "event_name": "Finished Major",
        "status": "BREAK",
        "window_active": False,
        "main": {"start": now - timedelta(days=3)},
    }

    choice = SportsDashboard._select_right_esports_sidebar(
        [lpl_offseason, lck_offseason],
        {"primary": break_card, "cards": [break_card], "rotation_pool": []},
        "VALVE CACHE",
        now,
        ewc_card={"selected": ewc_selected, "source_state": "EWC CACHE", "priority": 2},
    )

    assert SportsDashboard._ewc_sidebar_candidate_phase({"selected": ewc_selected}) == 1
    assert choice["kind"] == "ewc"
    assert choice["selected"]["main"]["game"] == "Apex Legends"

def test_ewc_logo_asset_and_sidebar_render_are_available():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_ewc_competitions_html(_sample_ewc_competitions_html(), la, DEFAULT_EWC_COMPETITIONS_URL)
    selected = SportsDashboard._select_ewc_events(events, datetime(2026, 6, 24, 12, 0, tzinfo=la), upcoming_window_days=21)
    logo = SportsDashboard._load_local_logo(LOCAL_EWC_LOGO_PATH, (92, 35), alpha_threshold=8)
    game_logo = SportsDashboard._load_ewc_game_logo(events[0], (112, 34))
    image = Image.new("RGB", (800, 480), COLORS["paper"])

    plugin._draw_ewc_sidebar(image, 552, selected, "EWC CACHE", datetime(2026, 6, 24, 12, 0, tzinfo=la))

    assert logo is not None
    assert game_logo is not None
    assert logo.getchannel("A").getextrema()[0] == 0
    assert game_logo.getchannel("A").getextrema()[0] == 0
    assert image.getpixel((560, 80)) != COLORS["paper"]

def test_lck_sidebar_uses_lck_logo_and_plain_team_names(monkeypatch):
    plugin = _plugin()
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    event = {
        "start": now + timedelta(hours=1),
        "state": "unstarted",
        "team_a": "Gen.G",
        "team_b": "T1",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": None,
        "wins_b": None,
        "best_of": 3,
        "block": "Regular Season",
    }
    selected = SportsDashboard._select_lck_events([event], now)
    image = Image.new("RGB", (800, 480), COLORS["paper"])
    seen_texts = []
    logo_calls = []
    original_fit_text = plugin._fit_text

    def record_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def record_lol_logo(_image, _draw, _x, _y, _width, _height, logo_path=None, fallback_text=None):
        logo_calls.append((logo_path, fallback_text))

    monkeypatch.setattr(plugin, "_fit_text", record_fit_text)
    monkeypatch.setattr(plugin, "_draw_lpl_logo", record_lol_logo)

    plugin._draw_lpl_sidebar(image, 552, selected, "LCK LIVE DATA", now, league_key="LCK")

    assert logo_calls[0] == (LOCAL_LCK_LOGO_PATH, "LCK")
    assert "Gen.G" in seen_texts
    assert "T1" in seen_texts


def test_msi_sidebar_header_logo_is_forty_percent_larger(monkeypatch):
    plugin = _plugin()
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    selected = {
        "live": [
            {
                "start": now,
                "state": "inprogress",
                "team_a": "T1",
                "team_b": "TLAW",
            }
        ],
        "upcoming": [],
        "recent": [],
    }
    image = Image.new("RGB", (800, 480), COLORS["paper"])
    logo_calls = []

    def record_lol_logo(_image, _draw, x, y, width, height, logo_path=None, fallback_text=None):
        logo_calls.append((x, y, width, height, logo_path, fallback_text))

    monkeypatch.setattr(plugin, "_draw_lpl_logo", record_lol_logo)
    monkeypatch.setattr(plugin, "_draw_lpl_focus_card", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_draw_lpl_next_rows", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_draw_lpl_recent_rows", lambda *_args, **_kwargs: None)

    plugin._draw_lpl_sidebar(image, 552, selected, "MSI LIVE DATA", now, league_key="MSI")

    assert logo_calls[0] == (554, 9, 104, 53, LOCAL_MSI_LOGO_PATH, "MSI")


def test_fetch_lck_events_uses_official_lck_league_id(monkeypatch):
    plugin = _plugin()
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"schedule": {"events": []}}}

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            calls.append((url, headers, timeout))
            return FakeResponse()

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    assert plugin._fetch_lck_events({}, timezone.utc) == []
    assert "leagueId=98767991310872058" in calls[0][0]

def test_select_msi_tournament_uses_next_riot_msi_window():
    la = ZoneInfo("America/Los_Angeles")
    tournaments = [
        {"id": "old", "slug": "msi_2024", "startDate": "2024-04-30", "endDate": "2024-05-19"},
        {"id": "115570934354631452", "slug": "msi_2026", "startDate": "2026-06-27", "endDate": "2026-07-12"},
    ]

    selected = SportsDashboard._select_msi_tournament(
        tournaments,
        la,
        datetime(2026, 6, 24, 12, 0, tzinfo=la),
    )

    assert selected["id"] == "115570934354631452"
    assert selected["slug"] == "msi_2026"
    assert selected["start"].strftime("%Y-%m-%d") == "2026-06-27"
    assert selected["end"].strftime("%Y-%m-%d %H:%M") == "2026-07-12 23:59"


def test_fetch_msi_tournament_accepts_riot_leagues_list_payload(monkeypatch):
    plugin = _plugin()
    calls = []
    payload = {
        "data": {
            "leagues": [
                {
                    "id": DEFAULT_MSI_LEAGUE_ID,
                    "tournaments": [
                        {
                            "id": "115570934354631452",
                            "slug": "msi_2026",
                            "startDate": "2026-06-27",
                            "endDate": "2026-07-12",
                        }
                    ],
                }
            ]
        }
    }

    class FakeResponse:
        def json(self):
            return payload

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    tournament = plugin._fetch_msi_tournament({}, timezone.utc, datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc))

    assert tournament["id"] == "115570934354631452"
    assert tournament["slug"] == "msi_2026"
    assert f"leagueId={DEFAULT_MSI_LEAGUE_ID}" in calls[0]

def test_fetch_msi_events_uses_official_msi_league_id_and_filters_old_schedule(monkeypatch):
    plugin = _plugin()
    calls = []
    old_schedule = {
        "data": {
            "schedule": {
                "events": [
                    {
                        "id": "old-msi-match",
                        "startTime": "2024-05-01T08:00:00Z",
                        "state": "completed",
                        "blockName": "Play-Ins",
                        "match": {
                            "id": "old-msi-match",
                            "strategy": {"type": "bestOf", "count": 3},
                            "teams": [
                                {"code": "FLY", "result": {"gameWins": 2}},
                                {"code": "PSG", "result": {"gameWins": 1}},
                            ],
                        },
                    }
                ]
            }
        }
    }

    class FakeResponse:
        def json(self):
            return old_schedule

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            calls.append((url, headers, timeout))
            return FakeResponse()

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())
    tournament = {
        "id": "115570934354631452",
        "slug": "msi_2026",
        "start": datetime(2026, 6, 27, tzinfo=timezone.utc),
        "end": datetime(2026, 7, 12, 23, 59, tzinfo=timezone.utc),
    }

    events = plugin._fetch_msi_events({}, timezone.utc, datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc), tournament)

    assert events == []
    assert f"leagueId={DEFAULT_MSI_LEAGUE_ID}" in calls[0][0]
    assert len(calls) == 1


def test_fetch_msi_events_polls_live_endpoint_during_msi_window(monkeypatch):
    plugin = _plugin()
    calls = []
    old_schedule = {"data": {"schedule": {"events": []}}}
    live_payload = {
        "data": {
            "schedule": {
                "events": [
                    {
                        "id": "msi-live-event",
                        "startTime": "2026-06-27T09:00:00Z",
                        "state": "inProgress",
                        "blockName": "Bracket Stage",
                        "league": {"id": DEFAULT_MSI_LEAGUE_ID, "name": "MSI", "slug": "msi"},
                        "match": {
                            "id": "msi-live-match",
                            "strategy": {"type": "bestOf", "count": 5},
                            "teams": [
                                {"code": "T1", "result": {"gameWins": 1}},
                                {"code": "BLG", "result": {"gameWins": 1}},
                            ],
                        },
                    }
                ]
            }
        }
    }

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            calls.append(url)
            if "getLive" in url:
                return FakeResponse(live_payload)
            return FakeResponse(old_schedule)

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())
    tournament = {
        "id": "115570934354631452",
        "slug": "msi_2026",
        "start": datetime(2026, 6, 27, tzinfo=timezone.utc),
        "end": datetime(2026, 7, 12, 23, 59, tzinfo=timezone.utc),
    }

    events = plugin._fetch_msi_events({}, timezone.utc, datetime(2026, 6, 27, 9, 15, tzinfo=timezone.utc), tournament)

    assert len(events) == 1
    assert events[0]["league_key"] == "MSI"
    assert events[0]["team_a"] == "T1"
    assert events[0]["team_b"] == "BLG"
    assert any("getSchedule" in url and f"leagueId={DEFAULT_MSI_LEAGUE_ID}" in url for url in calls)
    assert any("getLive" in url for url in calls)


def test_msi_live_placeholder_stage_fallback_uses_msi_label():
    payload = {
        "data": {
            "schedule": {
                "events": [
                    {
                        "id": "116357327527674222",
                        "startTime": "2026-06-29T02:01:00.071Z",
                        "state": "inProgress",
                        "type": "show",
                        "blockName": "",
                        "league": {"id": DEFAULT_MSI_LEAGUE_ID, "name": "MSI", "slug": "msi"},
                    }
                ]
            }
        }
    }

    events = SportsDashboard._parse_lpl_events(payload, timezone.utc)

    assert len(events) == 1
    assert events[0]["team_a"] == "TBD"
    assert events[0]["team_b"] == "TBD"
    assert events[0]["stage_label"] == "MSI"
    assert SportsDashboard._lpl_stage_label(events[0], league_key="MSI") == "MSI"

def test_select_msi_events_ignores_live_show_placeholder_for_next_match():
    now = datetime(2026, 6, 28, 19, 30, tzinfo=timezone.utc)
    placeholder = {
        "event_id": "116357327527674222",
        "match_id": "116357327527674222",
        "source_match_id": "",
        "event_type": "show",
        "league_id": DEFAULT_MSI_LEAGUE_ID,
        "league_name": "MSI",
        "league_slug": "msi",
        "start": now - timedelta(minutes=29),
        "state": "inprogress",
        "team_a": "TBD",
        "team_b": "TBD",
        "wins_a": None,
        "wins_b": None,
        "best_of": None,
        "block": "",
        "stage_label": "MSI",
    }
    next_match = {
        "event_id": "real-msi-match",
        "match_id": "115570934355614509",
        "source_match_id": "115570934355614509",
        "event_type": "match",
        "league_id": DEFAULT_MSI_LEAGUE_ID,
        "league_name": "MSI",
        "league_slug": "msi",
        "start": now + timedelta(minutes=30),
        "state": "unstarted",
        "team_a": "T1",
        "team_b": "KC",
        "wins_a": None,
        "wins_b": None,
        "best_of": 5,
        "block": "Play In Knockouts",
        "stage_label": "Play In Knockouts",
    }

    selected = SportsDashboard._select_msi_events([placeholder, next_match], now)

    assert selected["live"] == []
    assert selected["main"]["team_a"] == "T1"
    assert selected["main"]["team_b"] == "KC"
    assert selected["upcoming"] == [next_match]

def test_select_msi_events_uses_featured_page_only_without_matches():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    featured = SportsDashboard._lpl_msi_featured_event(now)

    empty_selected = SportsDashboard._select_msi_events([], now, featured_event=featured)

    assert empty_selected["featured_event_page"] is True
    assert empty_selected["featured_event"]["key"] == "MSI"

    recent_event = {
        "start": now - timedelta(hours=2),
        "state": "completed",
        "team_a": "T1",
        "team_b": "BLG",
        "wins_a": 3,
        "wins_b": 2,
        "best_of": 5,
        "block": "Bracket Stage",
    }
    selected_with_recent = SportsDashboard._select_msi_events([recent_event], now, featured_event=featured)

    assert selected_with_recent["featured_event_page"] is False
    assert selected_with_recent["main"]["team_a"] == "T1"


def test_lol_sidebar_override_forces_msi(monkeypatch):
    plugin = _plugin()
    now = datetime(2026, 6, 27, 9, 15, tzinfo=timezone.utc)
    msi_event = {
        "start": now - timedelta(minutes=15),
        "state": "inprogress",
        "team_a": "T1",
        "team_b": "BLG",
        "wins_a": 1,
        "wins_b": 1,
        "best_of": 5,
        "block": "Bracket Stage",
    }
    plugin._load_lpl_events = lambda _settings, _timezone_info: ([], "CACHE DATA")
    plugin._load_lck_events = lambda _settings, _timezone_info: ([], "LCK NO DATA")
    plugin._load_msi_events = lambda _settings, _timezone_info, _now: ([msi_event], "MSI LIVE DATA", None)
    plugin._attach_lpl_odds = lambda events, *_args, **_kwargs: events

    choice = plugin._load_lol_esports_sidebar(
        {"lolEsportsSidebarOverride": "MSI"},
        FakeDeviceConfig(timezone="UTC"),
        timezone.utc,
        now,
    )

    assert choice["league_key"] == "MSI"
    assert choice["selected"]["live"][0]["team_b"] == "BLG"


def test_write_lol_live_state_uses_msi_file(monkeypatch, tmp_path):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "get_plugin_dir", lambda name: str(tmp_path / name))
    now = datetime(2026, 6, 27, 9, 15, tzinfo=timezone.utc)
    selected = {
        "live": [
            {
                "event_id": "msi-live-event",
                "start": now - timedelta(minutes=15),
                "state": "inprogress",
                "team_a": "T1",
                "team_b": "BLG",
                "wins_a": 1,
                "wins_b": 1,
                "best_of": 5,
            }
        ]
    }

    plugin._write_lol_live_state(selected, now, "MSI LIVE DATA", league_key="MSI")

    payload = json.loads((tmp_path / "cache" / "msi_live_state.json").read_text(encoding="utf-8"))
    assert payload["version"] == "sports-dashboard-msi-live-v1"
    assert payload["league_key"] == "MSI"
    assert payload["team_a"] == "T1"
    assert not (tmp_path / "cache" / "lpl_live_state.json").exists()

def test_lpl_generic_playoff_stages_are_inferred_from_schedule_order():
    tz = timezone.utc
    events = [
        {
            "start": datetime(2026, 6, day, 9, 0, tzinfo=tz),
            "state": "unstarted",
            "team_a": team_a,
            "team_b": team_b,
            "block": "Playoffs",
        }
        for day, team_a, team_b in (
            (1, "QF1A", "QF1B"),
            (2, "QF2A", "QF2B"),
            (3, "SF1A", "SF1B"),
            (4, "SF2A", "SF2B"),
            (5, "FNL", "OPP"),
        )
    ]

    annotated = SportsDashboard._annotate_lpl_stage_labels(events)

    labels = {event["team_a"]: event["stage_label"] for event in annotated}
    assert labels["FNL"] == "Final"
    assert labels["SF2A"] == "Semi-Final"
    assert labels["SF1A"] == "Semi-Final"
    assert labels["QF2A"] == "Quarter-Final"


def test_lpl_generic_stage_respects_explicit_future_final():
    tz = timezone.utc
    events = [
        {
            "start": datetime(2026, 6, 12, 9, 0, tzinfo=tz),
            "state": "unstarted",
            "team_a": "EARLY",
            "team_b": "OPP",
            "block": "Playoffs",
        },
        {
            "start": datetime(2026, 6, 13, 9, 0, tzinfo=tz),
            "state": "unstarted",
            "team_a": "BLG",
            "team_b": "WE",
            "block": "Playoffs",
        },
        {
            "start": datetime(2026, 6, 14, 9, 0, tzinfo=tz),
            "state": "unstarted",
            "team_a": "TBD",
            "team_b": "TES",
            "block": "Finals",
        },
    ]

    annotated = SportsDashboard._annotate_lpl_stage_labels(events)

    labels = {event["team_a"]: event["stage_label"] for event in annotated}
    assert labels["TBD"] == "Final"
    assert labels["BLG"] == "Semi-Final"
    assert labels["EARLY"] == "Semi-Final"


def test_lpl_focus_stage_label_uses_stage_without_series_score():
    plugin = _plugin()
    image = Image.new("RGB", (320, 220), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    event = {
        "start": datetime(2026, 6, 13, 9, 0, tzinfo=timezone.utc),
        "state": "unstarted",
        "team_a": "BLG",
        "team_b": "WE",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 0,
        "wins_b": 0,
        "block": "Playoffs",
        "stage_label": "Semi-Final",
    }

    plugin._draw_lpl_focus_card(image, draw, 0, 220, 0, event, event["start"], False)

    assert "Semi-Final" in seen_texts
    assert "0-0" not in seen_texts


def test_lpl_focus_stage_label_draws_above_vs():
    plugin = _plugin()
    image = Image.new("RGB", (320, 220), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    centered_labels = []
    centered_points = []
    fit_text_calls = []
    original_centered_in_box = plugin._draw_centered_in_box
    original_centered = plugin._draw_centered
    original_fit_text = plugin._fit_text

    def capture_centered_in_box(draw_obj, box, text, *args, **kwargs):
        centered_labels.append((box, str(text)))
        return original_centered_in_box(draw_obj, box, text, *args, **kwargs)

    def capture_centered(draw_obj, center, text, *args, **kwargs):
        centered_points.append((center, str(text)))
        return original_centered(draw_obj, center, text, *args, **kwargs)

    def capture_fit_text(draw_obj, text, max_width, size, bold=False, min_size=11):
        fit_text_calls.append((str(text), max_width, size, bold, min_size))
        return original_fit_text(draw_obj, text, max_width, size, bold=bold, min_size=min_size)

    plugin._draw_centered_in_box = capture_centered_in_box
    plugin._draw_centered = capture_centered
    plugin._fit_text = capture_fit_text
    event = {
        "start": datetime(2026, 6, 13, 9, 0, tzinfo=timezone.utc),
        "state": "unstarted",
        "team_a": "BLG",
        "team_b": "TES",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": None,
        "wins_b": None,
        "block": "Final",
        "stage_label": "Final",
        "odds": {"team_a": "1.40", "team_b": "2.75"},
    }

    plugin._draw_lpl_focus_card(image, draw, 0, 220, 0, event, event["start"], False)

    final_box = next(box for box, text in centered_labels if text == "Final")
    final_fit = next(call for call in fit_text_calls if call[0] == "Final")
    vs_center = next(center for center, text in centered_points if text == "VS")
    assert final_fit[2] == 12
    assert final_box[0] > 50
    assert final_box[2] < 170
    assert final_box[1] >= 76
    assert final_box[3] <= 88
    assert final_box[3] < vs_center[1]



def test_msi_focus_stage_label_draws_below_team_names():
    plugin = _plugin()
    image = Image.new("RGB", (320, 220), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    centered_labels = []
    centered_points = []
    fit_text_calls = []
    original_centered_in_box = plugin._draw_centered_in_box
    original_centered = plugin._draw_centered
    original_fit_text = plugin._fit_text

    def capture_centered_in_box(draw_obj, box, text, *args, **kwargs):
        centered_labels.append((box, str(text)))
        return original_centered_in_box(draw_obj, box, text, *args, **kwargs)

    def capture_centered(draw_obj, center, text, *args, **kwargs):
        centered_points.append((center, str(text)))
        return original_centered(draw_obj, center, text, *args, **kwargs)

    def capture_fit_text(draw_obj, text, max_width, size, bold=False, min_size=11):
        fit_text_calls.append((str(text), max_width, size, bold, min_size))
        return original_fit_text(draw_obj, text, max_width, size, bold=bold, min_size=min_size)

    plugin._draw_centered_in_box = capture_centered_in_box
    plugin._draw_centered = capture_centered
    plugin._fit_text = capture_fit_text
    event = {
        "start": datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc),
        "state": "unstarted",
        "team_a": "T1",
        "team_b": "TLAW",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": None,
        "wins_b": None,
        "block": "Play In Knockouts",
        "stage_label": "Play In Knockouts",
    }

    plugin._draw_lpl_focus_card(image, draw, 0, 220, 0, event, event["start"], False, league_key="MSI")

    stage_box = next(box for box, text in centered_labels if text == "Play In Knockouts")
    stage_fit = next(call for call in fit_text_calls if call[0] == "Play In Knockouts")
    team_y = max(center[1] for center, text in centered_points if text in {"T1", "TLAW"})
    vs_center = next(center for center, text in centered_points if text == "VS")
    assert stage_fit[2] == 11
    assert stage_box[1] >= team_y + 22
    assert stage_box[3] <= 152
    assert stage_box[3] > vs_center[1]

def test_lpl_display_team_names_prefer_chinese_short_names():
    assert SportsDashboard._lpl_display_team_name("BLG") == "\u54d4\u54e9\u54d4\u54e9"
    assert SportsDashboard._lpl_display_team_name("Bilibili Gaming") == "\u54d4\u54e9\u54d4\u54e9"
    assert SportsDashboard._lpl_display_team_name("Top Esports") == "\u6ed4\u640f"
    assert SportsDashboard._lpl_display_team_name("JD Gaming") == "\u4eac\u4e1c"
    assert SportsDashboard._lpl_display_team_name("LNG Esports") == "\u674e\u5b81"
    assert SportsDashboard._lpl_display_team_name("Weibo Gaming") == "\u5fae\u535a"
    assert SportsDashboard._lpl_display_team_name("EDG") == "EDG"


def test_lpl_cards_render_chinese_names_without_changing_logo_codes():
    plugin = _plugin()
    image = Image.new("RGB", (340, 420), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    logo_fallbacks = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def capture_logo(_image, _draw, _logo_url, _x, _y, _size, fallback_text):
        logo_fallbacks.append(str(fallback_text))

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = capture_logo
    event = {
        "start": datetime(2026, 6, 13, 9, 0, tzinfo=timezone.utc),
        "state": "unstarted",
        "team_a": "BLG",
        "team_b": "TES",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 3,
        "wins_b": 2,
        "block": "Final",
        "stage_label": "Final",
        "odds": {"team_a": "1.40", "team_b": "2.75"},
    }

    plugin._draw_lpl_focus_card(image, draw, 0, 220, 0, event, event["start"], False)
    plugin._draw_lpl_next_row(image, draw, 0, 220, 166, event, event["start"])
    plugin._draw_lpl_recent_result_row(image, draw, 0, 220, 224, event)
    plugin._draw_lpl_main_card(draw, 0, 220, 280, event, event["start"], False)

    assert "\u54d4\u54e9\u54d4\u54e9" in seen_texts
    assert "\u6ed4\u640f" in seen_texts
    assert "BLG" in logo_fallbacks
    assert "TES" in logo_fallbacks


def test_lpl_marble_filler_asset_is_exact_transparent_strip():
    with Image.open(LOCAL_LPL_MARBLE_FILLER_PATH) as source:
        filler = source.convert("RGBA")

    assert filler.size == (196, 46)
    assert filler.getchannel("A").getextrema()[0] == 0


def test_lpl_msi_next_filler_asset_is_exact_strip():
    with Image.open(LOCAL_LPL_MSI_NEXT_FILLER_PATH) as source:
        filler = source.convert("RGB")

    assert filler.size == (196, 48)
    assert len(filler.getcolors(maxcolors=filler.width * filler.height + 1)) > 200


def test_lpl_empty_upcoming_slot_draws_marble_filler(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (224, 420), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    filler = Image.new("RGBA", (196, 46), (240, 10, 20, 255))
    event = {"start": datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc), "team_a": "TBD", "team_b": "TES"}

    monkeypatch.setattr(plugin, "_load_lpl_sidebar_filler", lambda size: filler.resize(size))
    monkeypatch.setattr(plugin, "_draw_lpl_next_row", lambda *_args, **_kwargs: None)

    plugin._draw_lpl_next_rows(image, draw, 0, 224, 244, [event], event["start"], False)

    assert image.getpixel((112, 345)) == (240, 10, 20)


def test_lpl_empty_upcoming_slot_draws_msi_next_filler_when_active(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (224, 420), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    marble = Image.new("RGBA", (196, 46), (240, 10, 20, 255))
    msi_next = Image.new("RGB", (196, 48), (20, 120, 240))
    event_start = datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc)
    msi_start = datetime(2026, 6, 28, 0, 0, tzinfo=timezone.utc)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def record_fit_text(draw_arg, text, max_width, size, bold=False, min_size=11):
        seen_texts.append(str(text))
        return original_fit_text(draw_arg, text, max_width, size, bold=bold, min_size=min_size)

    monkeypatch.setattr(plugin, "_load_lpl_sidebar_filler", lambda size: marble.resize(size))
    monkeypatch.setattr(plugin, "_load_lpl_msi_next_filler", lambda size: msi_next.resize(size))
    plugin._fit_text = record_fit_text

    plugin._draw_lpl_next_rows(
        image,
        draw,
        0,
        224,
        244,
        [],
        event_start,
        False,
        msi_next_filler=True,
        msi_next_start=msi_start,
    )

    assert image.getpixel((20, 345)) == (20, 120, 240)
    assert "MSI NEXT 06/28" in seen_texts


def test_lpl_empty_upcoming_slot_stays_clear_with_two_rows(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (224, 420), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    filler = Image.new("RGBA", (196, 46), (240, 10, 20, 255))
    event = {"start": datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc), "team_a": "TBD", "team_b": "TES"}

    monkeypatch.setattr(plugin, "_load_lpl_sidebar_filler", lambda size: filler.resize(size))
    monkeypatch.setattr(plugin, "_draw_lpl_next_row", lambda *_args, **_kwargs: None)

    plugin._draw_lpl_next_rows(image, draw, 0, 224, 244, [event, event], event["start"], False)

    assert image.getpixel((112, 345)) != (240, 10, 20)


def test_select_lpl_events_marks_msi_countdown_when_lpl_schedule_ends():
    la = ZoneInfo("America/Los_Angeles")
    final = {
        "start": datetime(2026, 6, 13, 2, 0, tzinfo=la),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "TES",
        "wins_a": 3,
        "wins_b": 2,
        "best_of": 5,
        "block": "Final",
        "league_name": "LPL",
    }

    selected = SportsDashboard._select_lpl_events(
        [final],
        datetime(2026, 6, 14, 9, 0, tzinfo=la),
    )

    assert selected["offseason"] is True
    assert selected["featured_event_page"] is True
    assert selected["featured_event"]["key"] == "MSI"
    assert selected["featured_event"]["countdown_days"] == 14
    assert selected["featured_event"]["logo_path"] == LOCAL_MSI_LOGO_PATH


def test_select_lpl_events_uses_msi_logo_for_msi_schedule_event_without_offseason():
    la = ZoneInfo("America/Los_Angeles")
    event = {
        "start": datetime(2026, 6, 28, 2, 0, tzinfo=la),
        "state": "unstarted",
        "team_a": "TBD",
        "team_b": "TBD",
        "wins_a": None,
        "wins_b": None,
        "best_of": 5,
        "block": "Bracket Stage",
        "league_name": "Mid-Season Invitational",
        "league_slug": "msi",
    }

    selected = SportsDashboard._select_lpl_events(
        [event],
        datetime(2026, 6, 27, 9, 0, tzinfo=la),
    )

    assert selected["offseason"] is False
    assert selected["featured_event_page"] is False
    assert selected["featured_event"]["key"] == "MSI"
    assert selected["featured_event"]["phase"] == "match_upcoming"


def test_lpl_featured_event_countdown_days_are_date_based():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 13, 21, 30, tzinfo=la)
    featured = SportsDashboard._lpl_msi_featured_event(now)

    assert featured["phase"] == "countdown"
    assert featured["countdown_days"] == 15
    assert SportsDashboard._lpl_featured_event_pill_text(featured) == "D-15"


def test_lpl_msi_next_filler_only_appears_near_msi_countdown():
    la = ZoneInfo("America/Los_Angeles")

    assert SportsDashboard._lpl_msi_next_filler_active(datetime(2026, 6, 14, 9, 0, tzinfo=la)) is True
    assert SportsDashboard._lpl_msi_next_filler_active(datetime(2026, 6, 1, 9, 0, tzinfo=la)) is False
    assert SportsDashboard._lpl_msi_next_filler_active(datetime(2026, 7, 1, 9, 0, tzinfo=la)) is False


def test_lpl_msi_next_filler_prefers_fetched_msi_start_date():
    la = ZoneInfo("America/Los_Angeles")
    fetched = {
        "key": "MSI",
        "phase": "match_upcoming",
        "start": datetime(2026, 6, 29, 2, 0, tzinfo=la),
    }

    event = SportsDashboard._lpl_msi_next_filler_event(datetime(2026, 6, 14, 9, 0, tzinfo=la), fetched)

    assert event["start"].strftime("%m/%d") == "06/29"


def test_lpl_msi_logo_and_offseason_filler_assets_are_available():
    msi_logo = SportsDashboard._load_local_logo(LOCAL_MSI_LOGO_PATH, (74, 38), alpha_threshold=8)

    assert msi_logo is not None
    assert msi_logo.size[0] <= 74
    assert msi_logo.size[1] <= 38
    assert msi_logo.getchannel("A").getextrema()[0] == 0
    with Image.open(LOCAL_LPL_MSI_OFFSEASON_FILLER_PATH) as source:
        filler = source.convert("RGB")
    assert filler.size == (212, 80)
    accent_paths = sorted(Path(LOCAL_LPL_MSI_CARD_ACCENT_DIR).glob("*.png"))
    assert len(accent_paths) >= 8
    for accent_path in accent_paths:
        with Image.open(accent_path) as source:
            accent = source.convert("RGBA")
        assert accent.size == (128, 92)
        assert accent.getchannel("A").getextrema()[0] == 0
        assert accent.getbbox() is not None
    with Image.open(LOCAL_LPL_MSI_CARD_ACCENT_PATH) as source:
        fallback_accent = source.convert("RGBA")
    assert fallback_accent.size == (128, 92)

def test_lpl_msi_offseason_filler_pool_assets_are_transparent():
    paths = SportsDashboard._lpl_msi_offseason_filler_paths()

    assert paths == LOCAL_LPL_MSI_OFFSEASON_FILLER_PATHS
    assert {SportsDashboard._lpl_msi_offseason_filler_index(seed, len(paths)) for seed in range(64)} == {0, 1}
    for path in paths:
        with Image.open(path) as source:
            filler = source.convert("RGBA")
        assert filler.size == (212, 80)
        assert filler.getchannel("A").getextrema()[0] == 0
        assert filler.getpixel((0, 0))[3] == 0


def test_lpl_msi_card_accent_pool_rotates_by_render_time():
    paths = SportsDashboard._lpl_msi_card_accent_paths()

    assert len(paths) >= 2
    assert SportsDashboard._lpl_msi_card_accent_index(datetime(2026, 6, 15, 9, 0, 0, tzinfo=timezone.utc), len(paths)) != (
        SportsDashboard._lpl_msi_card_accent_index(datetime(2026, 6, 15, 9, 0, 1, tzinfo=timezone.utc), len(paths))
    )
    first = SportsDashboard._load_lpl_msi_card_accent((94, 68), datetime(2026, 6, 15, 9, 0, 0, tzinfo=timezone.utc))
    second = SportsDashboard._load_lpl_msi_card_accent((94, 68), datetime(2026, 6, 15, 9, 0, 1, tzinfo=timezone.utc))
    assert first is not None
    assert second is not None
    assert first.size == (94, 68)
    assert second.size == (94, 68)


def test_lpl_sidebar_uses_featured_logo_only_for_featured_event(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (800, 480), COLORS["paper"])
    la = ZoneInfo("America/Los_Angeles")
    logo_paths = []

    def capture_logo(_image, _draw, _x, _y, _width, _height, logo_path=None, fallback_text=None):
        logo_paths.append(logo_path)

    monkeypatch.setattr(plugin, "_draw_lpl_logo", capture_logo)
    monkeypatch.setattr(plugin, "_draw_lpl_featured_event_panel", lambda *_args, **_kwargs: None)
    selected = SportsDashboard._select_lpl_events(
        [
            {
                "start": datetime(2026, 6, 13, 2, 0, tzinfo=la),
                "state": "completed",
                "team_a": "BLG",
                "team_b": "TES",
                "wins_a": 3,
                "wins_b": 2,
                "best_of": 5,
                "block": "Final",
                "league_name": "LPL",
            }
        ],
        datetime(2026, 6, 14, 9, 0, tzinfo=la),
    )

    plugin._draw_lpl_sidebar(image, 556, selected, "LIVE DATA", datetime(2026, 6, 14, 9, 0, tzinfo=la))

    normal_selected = {
        "live": [],
        "upcoming": [{"start": datetime(2026, 8, 1, 2, 0, tzinfo=la), "team_a": "BLG", "team_b": "TES"}],
        "recent": [],
        "main": None,
    }
    monkeypatch.setattr(plugin, "_draw_lpl_focus_card", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_draw_lpl_next_rows", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_draw_lpl_recent_rows", lambda *_args, **_kwargs: None)
    plugin._draw_lpl_sidebar(image, 556, normal_selected, "LIVE DATA", datetime(2026, 7, 30, 9, 0, tzinfo=la))

    assert logo_paths[0] == LOCAL_MSI_LOGO_PATH
    assert logo_paths[1] is None


def test_lpl_featured_event_panel_draws_core_status_labels():
    plugin = _plugin()
    image = Image.new("RGB", (240, 420), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    seen_texts = []
    original_fit_text = plugin._fit_text

    def record_fit_text(draw_arg, text, max_width, size, bold=False, min_size=11):
        seen_texts.append(str(text))
        return original_fit_text(draw_arg, text, max_width, size, bold=bold, min_size=min_size)

    plugin._fit_text = record_fit_text
    selected = {
        "featured_event": SportsDashboard._lpl_msi_featured_event(datetime(2026, 6, 14, 9, 0, tzinfo=la)),
        "featured_event_page": True,
        "offseason": True,
        "recent": [],
    }

    plugin._draw_lpl_featured_event_panel(
        image,
        draw,
        0,
        240,
        78,
        408,
        selected,
        datetime(2026, 6, 14, 9, 0, tzinfo=la),
    )

    assert "\u4f11\u8d5b\u671f" in seen_texts
    assert "\u4e0b\u4e00\u7ad9 MSI" in seen_texts
    assert "D-14" in seen_texts
    assert "MSI \u5f00\u8d5b" in seen_texts


def test_lpl_featured_event_panel_draws_card_accent(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (240, 420), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    selected = {
        "featured_event": SportsDashboard._lpl_msi_featured_event(datetime(2026, 6, 14, 9, 0, tzinfo=la)),
        "featured_event_page": True,
        "offseason": True,
        "recent": [],
    }

    monkeypatch.setattr(
        plugin,
        "_load_lpl_msi_card_accent",
        lambda size, rotation_seed=None: Image.new("RGBA", size, (20, 180, 220, 255)),
    )

    plugin._draw_lpl_featured_event_panel(
        image,
        draw,
        0,
        240,
        78,
        408,
        selected,
        datetime(2026, 6, 14, 9, 0, tzinfo=la),
    )

    assert image.getpixel((180, 136)) == (20, 180, 220)


def test_lpl_featured_event_panel_omits_duplicate_card_logo(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (240, 420), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    logo_paths = []
    selected = {
        "featured_event": SportsDashboard._lpl_msi_featured_event(datetime(2026, 6, 14, 9, 0, tzinfo=la)),
        "featured_event_page": True,
        "offseason": True,
        "recent": [],
    }

    def record_logo(path, size, alpha_threshold=8):
        logo_paths.append(path)
        return Image.new("RGBA", size, (255, 0, 0, 255))

    monkeypatch.setattr(plugin, "_load_local_logo", record_logo)
    monkeypatch.setattr(plugin, "_load_lpl_msi_offseason_filler", lambda size, *_args: None)

    plugin._draw_lpl_featured_event_panel(
        image,
        draw,
        0,
        240,
        78,
        408,
        selected,
        datetime(2026, 6, 14, 9, 0, tzinfo=la),
    )

    assert logo_paths == []


def test_lpl_featured_event_panel_bleeds_bottom_filler_to_sidebar_edges(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (240, 420), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    calls = []
    selected = {
        "featured_event": SportsDashboard._lpl_msi_featured_event(datetime(2026, 6, 14, 9, 0, tzinfo=la)),
        "featured_event_page": True,
        "offseason": True,
        "recent": [],
    }

    monkeypatch.setattr(plugin, "_draw_lpl_featured_event_filler", lambda *_args: calls.append(_args))

    plugin._draw_lpl_featured_event_panel(
        image,
        draw,
        0,
        240,
        78,
        408,
        selected,
        datetime(2026, 6, 14, 9, 0, tzinfo=la),
    )

    assert calls
    assert calls[0][1:5] == (0, 239, 388, 408)
    assert calls[0][5] == datetime(2026, 6, 14, 9, 0, tzinfo=la)


def test_lpl_featured_event_sidebar_allows_filler_to_reach_canvas_bottom(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (800, 480), COLORS["paper"])
    la = ZoneInfo("America/Los_Angeles")
    calls = []
    selected = {
        "featured_event": SportsDashboard._lpl_msi_featured_event(datetime(2026, 6, 14, 9, 0, tzinfo=la)),
        "featured_event_page": True,
        "offseason": True,
        "recent": [],
    }

    monkeypatch.setattr(plugin, "_draw_lpl_featured_event_panel", lambda *_args: calls.append(_args))

    plugin._draw_lpl_sidebar(image, 552, selected, "fallback", datetime(2026, 6, 14, 9, 0, tzinfo=la))

    assert calls
    assert calls[0][5] == image.height - 1


def test_lpl_featured_event_filler_uses_zoomed_bottom_crop(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (220, 100), COLORS["paper"])
    requested_sizes = []

    def load_filler(size, *_args):
        requested_sizes.append(size)
        filler = Image.new("RGB", size, (200, 10, 10))
        filler_draw = ImageDraw.Draw(filler)
        filler_draw.rectangle((0, size[1] - 80, size[0] - 1, size[1] - 1), fill=(12, 200, 40))
        return filler

    monkeypatch.setattr(plugin, "_load_lpl_msi_offseason_filler", load_filler)

    plugin._draw_lpl_featured_event_filler(image, 10, 209, 20, 99)

    assert requested_sizes == [
        (
            int(200 * LPL_MSI_OFFSEASON_FILLER_ZOOM + 0.999),
            int((80 + LPL_MSI_OFFSEASON_FILLER_BOTTOM_OVERFILL) * LPL_MSI_OFFSEASON_FILLER_ZOOM + 0.999),
        )
    ]
    assert image.getpixel((10, 20)) == (200, 10, 10)
    assert image.getpixel((209, 99)) == (12, 200, 40)

def test_lpl_featured_event_filler_overfills_transparent_bottom_gap(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (220, 100), COLORS["paper"])
    requested_sizes = []

    def load_filler(size, *_args):
        requested_sizes.append(size)
        return Image.new("RGBA", size, (12, 200, 40, 255))

    monkeypatch.setattr(plugin, "_load_lpl_msi_offseason_filler", load_filler)

    plugin._draw_lpl_featured_event_filler(image, 10, 209, 20, 99)

    assert requested_sizes == [
        (
            int(200 * LPL_MSI_OFFSEASON_FILLER_ZOOM + 0.999),
            int((80 + LPL_MSI_OFFSEASON_FILLER_BOTTOM_OVERFILL) * LPL_MSI_OFFSEASON_FILLER_ZOOM + 0.999),
        )
    ]
    assert LPL_MSI_OFFSEASON_FILLER_VERTICAL_CROP_OFFSET < LPL_MSI_OFFSEASON_FILLER_BOTTOM_OVERFILL
    assert image.getpixel((10, 99)) == (12, 200, 40)

def test_f1_jolpica_parser_builds_race_sessions_and_standings():
    la = ZoneInfo("America/Los_Angeles")
    data = SportsDashboard._parse_f1_jolpica_bundle(_sample_f1_jolpica_bundle(), la)

    assert data["races"][0]["race_name"] == "Barcelona-Catalunya Grand Prix"
    assert data["races"][0]["sessions"][-1]["label"] == "RACE"
    assert data["races"][0]["race_start"].strftime("%Y-%m-%d %H:%M") == "2026-06-14 06:00"
    assert data["last_result"]["top"][0]["driver_code"] == "RUS"
    assert data["driver_standings"][0]["driver_code"] == "ANT"


def test_mlb_scoreboard_parser_extracts_live_base_and_rhe_state():
    la = ZoneInfo("America/Los_Angeles")
    data = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)

    live = data["events"][0]
    assert live["sport"] == "MLB"
    assert live["state"] == "live"
    assert live["team_a"] == "\u5de8\u4eba"
    assert live["team_b"] == "\u9053\u5947"
    assert live["team_a_code"] == "SF"
    assert live["team_b_code"] == "LAD"
    assert live["wins_a"] == 3
    assert live["wins_b"] == 5
    assert live["inning_label"] == "7th"
    assert live["bases"] == "13"
    assert live["away_line"] == {"runs": 3, "hits": 7, "errors": 1}
    assert live["home_line"] == {"runs": 5, "hits": 8, "errors": 0}
    assert live["probable_b"] == "Y. Yamamoto"
    assert live["current_batter"] == "M. Chapman"
    assert live["current_pitcher"] == "Y. Yamamoto"
    assert live["team_a_logo"].endswith("/sf.png")
    assert live["team_b_logo"].endswith("/lad.png")


def test_mlb_warmup_state_is_scheduled_not_live():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_mlb_scoreboard_payload()))
    game = payload["dates"][0]["games"][0]
    game["status"] = {
        "abstractGameState": "Live",
        "codedGameState": "P",
        "detailedState": "Warmup",
        "statusCode": "PW",
    }

    event = SportsDashboard._parse_mlb_scoreboard(payload, la)["events"][0]

    assert event["state"] == "scheduled"
    assert event["status_text"] == "Warmup"
    assert event["wins_a"] is None
    assert event["wins_b"] is None


def test_mlb_short_team_aliases_stay_chinese_with_correct_logo_codes():
    la = ZoneInfo("America/Los_Angeles")
    payload = {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 777003,
                        "gameDate": "2026-06-16T00:10:00Z",
                        "status": {
                            "abstractGameState": "Preview",
                            "detailedState": "Scheduled",
                            "codedGameState": "S",
                        },
                        "teams": {
                            "away": {"team": {"name": "White Sox"}},
                            "home": {"team": {"name": "D-backs"}},
                        },
                        "linescore": {},
                    }
                ]
            }
        ]
    }

    event = SportsDashboard._parse_mlb_scoreboard(payload, la)["events"][0]

    assert event["team_a_code"] == "CWS"
    assert event["team_a"] == "\u767d\u889c"
    assert event["team_a_logo"].endswith("/chw.png")
    assert event["team_b_code"] == "ARI"
    assert event["team_b"] == "\u54cd\u5c3e\u86c7"
    assert event["team_b_logo"].endswith("/ari.png")
    assert SportsDashboard._mlb_team_code("LA Dodgers") == "LAD"
    alias_cases = [
        ("WAS", "WSH", "\u56fd\u6c11", "\u534e\u76db\u987f\u56fd\u6c11", "/wsh.png"),
        ("AZ", "ARI", "\u54cd\u5c3e\u86c7", "\u4e9a\u5229\u6851\u90a3\u54cd\u5c3e\u86c7", "/ari.png"),
        ("CHW", "CWS", "\u767d\u889c", "\u829d\u52a0\u54e5\u767d\u889c", "/chw.png"),
        ("SDP", "SD", "\u6559\u58eb", "\u5723\u8fed\u6208\u6559\u58eb", "/sd.png"),
        ("SFG", "SF", "\u5de8\u4eba", "\u65e7\u91d1\u5c71\u5de8\u4eba", "/sf.png"),
        ("KCR", "KC", "\u7687\u5bb6", "\u582a\u8428\u65af\u57ce\u7687\u5bb6", "/kc.png"),
    ]
    for alias_code, canonical_code, short_name, full_name, logo_suffix in alias_cases:
        assert SportsDashboard._mlb_display_team_name(alias_code) == short_name
        assert SportsDashboard._mlb_display_team_name(alias_code, full=True) == full_name
        assert SportsDashboard._mlb_team_code(alias_code) == canonical_code
        assert SportsDashboard._mlb_team_logo_url({}, alias_code).endswith(logo_suffix)


def test_mlb_info_rows_map_short_codes_to_chinese_team_names():
    now = datetime(2026, 6, 14, 13, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    event = {
        "sport": "MLB",
        "state": "final",
        "start": now - timedelta(hours=2),
        "team_a": "LAD",
        "team_b": "SF",
        "team_a_code": "LAD",
        "team_b_code": "SF",
        "wins_a": 5,
        "wins_b": 3,
        "record_a": "42-28",
        "record_b": "34-35",
        "away_line": {"runs": 5, "hits": 8, "errors": 0},
        "home_line": {"runs": 3, "hits": 7, "errors": 1},
        "venue": "Oracle Park",
    }

    assert SportsDashboard._mlb_display_team_from_event(event, "a") == "\u9053\u5947"
    assert SportsDashboard._mlb_display_team_from_event(event, "b") == "\u5de8\u4eba"
    assert SportsDashboard._mlb_matchup_label(event) == "\u9053\u5947 @ \u5de8\u4eba"
    assert (
        SportsDashboard._mlb_record_matchup_label(event)
        == "\u9053\u5947 42-28 / \u5de8\u4eba 34-35"
    )
    assert SportsDashboard._mlb_final_meta_label(event) == "\u9053\u5947 \u80dc2\u5206 / Oracle Park"
    assert (
        SportsDashboard._mlb_compact_rhe_label(event)
        == "\u9053\u5947 5/8/0  \u5de8\u4eba 3/7/1"
    )
    assert SportsDashboard._mlb_display_team_from_event(event, "a", full=True) == "\u6d1b\u6749\u77f6\u9053\u5947"
    assert SportsDashboard._mlb_display_team_from_event(event, "b", full=True) == "\u65e7\u91d1\u5c71\u5de8\u4eba"


def test_mlb_phillies_full_name_uses_compact_chinese_label():
    event = {
        "team_a": "Philadelphia Phillies",
        "team_a_name": "Philadelphia Phillies",
        "team_a_code": "PHI",
    }

    full_name = SportsDashboard._mlb_display_team_from_event(event, "a", full=True)

    assert full_name == "\u8d39\u57ce\u4eba"
    assert full_name != "\u8d39\u57ce\u8d39\u57ce\u4eba"


def test_pga_event_name_aliases_use_chinese_tournament_names():
    cases = {
        "THE PLAYERS Championship": "\u7403\u5458\u9526\u6807\u8d5b",
        "The Open Championship": "\u82f1\u56fd\u516c\u5f00\u8d5b",
        "U.S. Open Championship": "\u7f8e\u56fd\u516c\u5f00\u8d5b",
        "TOUR Championship": "\u5de1\u56de\u9526\u6807\u8d5b",
        "WM Phoenix Open": "WM\u51e4\u51f0\u57ce\u516c\u5f00\u8d5b",
        "AT&T Pebble Beach Pro-Am": "AT&T\u5706\u77f3\u6ee9\u804c\u4e1a\u4e1a\u4f59\u914d\u5bf9\u8d5b",
        "RBC Canadian Open": "RBC\u52a0\u62ff\u5927\u516c\u5f00\u8d5b",
        "John Deere Classic": "\u7ea6\u7ff0\u8fea\u5c14\u7cbe\u82f1\u8d5b",
        "Genesis Scottish Open": "\u82cf\u683c\u5170\u516c\u5f00\u8d5b",
        "Rocket Classic": "\u706b\u7bad\u7cbe\u82f1\u8d5b",
        "The Sentry": "\u54e8\u5175\u51a0\u519b\u8d5b",
        "Texas Children's Houston Open": "\u5fb7\u5dde\u513f\u7ae5\u4f11\u65af\u6566\u516c\u5f00\u8d5b",
    }

    for raw_name, expected_name in cases.items():
        assert SportsDashboard._pga_display_event_name(raw_name) == expected_name


def test_pga_leaderboard_parses_country_codes_into_detail_lines():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    payload = json.loads(json.dumps(_sample_pga_scoreboard_payload()))
    competitors = payload["events"][0]["competitions"][0]["competitors"]
    competitors[0]["athlete"]["country"] = {"abbreviation": "USA", "displayName": "United States"}
    competitors[1]["athlete"]["country"] = "Northern Ireland"

    event = SportsDashboard._parse_pga_scoreboard(payload, la, now)["events"][0]

    assert event["leaderboard"][0]["country"] == "USA"
    assert event["leaderboard"][1]["country"] == "NIR"
    assert event["leader"]["country"] == "USA"
    assert SportsDashboard._pga_country_code("US") == "USA"
    assert SportsDashboard._pga_country_code("South Korea") == "KOR"
    assert SportsDashboard._pga_row_detail_label(event["leaderboard"][0], leader_score="-9") == "\u7f8e\u56fd / R3 68 / -2"
    assert (
        SportsDashboard._pga_row_detail_label(event["leaderboard"][1], leader_score="-9")
        == "\u5317\u7231\u5c14\u5170 / R3 70 / E / GAP +2"
    )

    image = Image.new("RGB", (320, 190), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_pga_leader_summary(draw, 10, 10, 250, 52, event["leader"])
    plugin._draw_pga_leaderboard_row(draw, 10, 250, 70, event["leaderboard"][1], 1, leader_score="-9")

    assert "USA" in seen_texts
    assert "S. Scheffler" in seen_texts
    assert "-9" in seen_texts
    assert "NAT" not in seen_texts
    assert "\u7f8e\u56fd / R3 68 / -2" not in seen_texts
    assert "NIR" in seen_texts
    assert "\u5317\u7231\u5c14\u5170 / R3 70 / E / GAP +2" in seen_texts


def test_pga_event_parser_preserves_available_course_names():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    payload = json.loads(json.dumps(_sample_pga_scoreboard_payload()))
    event = payload["events"][0]
    competition = event["competitions"][0]
    event["venue"] = {"name": "Event Course"}
    competition["venue"] = {"displayName": "Display Course"}

    parsed = SportsDashboard._parse_pga_scoreboard(payload, la, now)

    assert parsed["events"][0]["venue"] == "Display Course"

    competition["venue"] = {}
    parsed = SportsDashboard._parse_pga_scoreboard(payload, la, now)

    assert parsed["events"][0]["venue"] == "Event Course"


def test_pga_provider_play_complete_is_not_rendered_as_live():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 7, 10, 18, 0, tzinfo=la)
    payload = json.loads(json.dumps(_sample_pga_scoreboard_payload()))
    event = payload["events"][0]
    event["date"] = "2026-07-09T04:00Z"
    event["endDate"] = "2026-07-12T22:00Z"
    competition = event["competitions"][0]
    competition["date"] = event["date"]
    competition["endDate"] = event["endDate"]
    competition["status"] = {
        "type": {
            "state": "post",
            "completed": True,
            "detail": "Round 2 - Play Complete",
        }
    }

    parsed = SportsDashboard._parse_pga_scoreboard(payload, la, now)
    current = parsed["events"][0]

    assert current["state"] == "final"
    assert current["status_text"] == "ROUND 2 COMPLETE"


def test_pga_equal_scores_without_provider_rank_render_as_tied():
    competitors = [
        {
            "order": index,
            "score": "-9",
            "athlete": {"shortName": name},
            "linescores": [],
        }
        for index, name in enumerate(
            ("J. Smith", "T. Kim", "R. McIlroy"), start=1
        )
    ]

    rows = SportsDashboard._parse_pga_leaderboard(competitors)

    assert [row["position_label"] for row in rows] == ["T1", "T1", "T1"]


def test_pga_equal_scores_preserve_provider_rank_labels():
    provider_rank_shapes = (
        ({"rank": 1}, {"rank": 2}),
        (
            {"curatedRank": {"abbreviation": "1"}},
            {"curatedRank": {"abbreviation": "2"}},
        ),
    )

    for first_rank, second_rank in provider_rank_shapes:
        rows = SportsDashboard._parse_pga_leaderboard(
            [
                {
                    **rank,
                    "score": "-9",
                    "athlete": {"shortName": name},
                    "linescores": [],
                }
                for rank, name in (
                    (first_rank, "J. Smith"),
                    (second_rank, "T. Kim"),
                )
            ]
        )

        assert [row["position_label"] for row in rows] == ["P1", "P2"]


def test_offseason_hub_parses_wnba_and_pga_sources():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_offseason_hub_payload(
        {
            "payloads": {
                "mlb": _sample_mlb_scoreboard_payload(),
                "wnba": _sample_wnba_scoreboard_payload(),
                "pga": _sample_pga_scoreboard_payload(),
                "nfl": _sample_nfl_scoreboard_payload(),
                "ncaa": _sample_ncaa_scoreboard_payload(),
            }
        },
        la,
        now,
    )

    wnba_live = next(event for event in parsed["wnba"]["events"] if SportsDashboard._hub_event_state(event) == "live")
    assert wnba_live["sport"] == "WNBA"
    assert wnba_live["status_text"] == "Q3 4:22"
    assert wnba_live["team_a"] == "\u98ce\u66b4"
    assert wnba_live["team_b"] == "\u738b\u724c"
    assert wnba_live["team_a_name"] == "Storm"
    assert wnba_live["team_b_name"] == "Aces"
    assert wnba_live["team_a_logo"] == "https://example.com/wnba-sea.png"
    assert wnba_live["team_b_logo"] == "https://example.com/wnba-lv.png"
    assert wnba_live["record_a"] == "7-4"
    assert wnba_live["record_b"] == "8-3"
    assert wnba_live["winner_a"] is None
    assert wnba_live["winner_b"] is None
    assert wnba_live["broadcast"] == "ION"
    assert wnba_live["venue"] == "Michelob ULTRA Arena"
    assert wnba_live["city"] == "Las Vegas, NV"
    assert parsed["pga"]["events"][0]["sport"] == "PGA"
    assert parsed["pga"]["events"][0]["state"] == "live"
    assert parsed["pga"]["events"][0]["name"] == "\u7f8e\u56fd\u516c\u5f00\u8d5b"
    assert parsed["pga"]["events"][0]["name_en"] == "U.S. Open"
    assert parsed["pga"]["events"][0]["leaderboard"][0]["name"] == "S. Scheffler"
    assert parsed["pga"]["events"][0]["leader"] == {
        "name": "S. Scheffler",
        "score": "-9",
        "round": 3,
        "today": "-2",
        "strokes": "68",
    }
    assert parsed["nfl"]["events"][0]["sport"] == "NFL"
    assert parsed["nfl"]["events"][0]["team_a"] == "\u6d77\u9e70"
    assert parsed["nfl"]["events"][0]["team_b"] == "\u7231\u56fd\u8005"
    assert parsed["nfl"]["events"][0]["team_a_code"] == "SEA"
    assert parsed["nfl"]["events"][0]["team_b_code"] == "NE"
    assert parsed["nfl"]["events"][0]["down_distance"] == "3RD & 4"
    assert parsed["nfl"]["events"][0]["possession"] == "SEA"
    assert parsed["nfl"]["events"][0]["last_play"] == "Kenneth Walker run for 6 yards"
    assert parsed["nfl"]["events"][0]["broadcast"] == "NBC"
    assert parsed["nfl"]["events"][0]["team_a_logo"] == "https://example.com/nfl-sea.png"
    assert parsed["nfl"]["events"][0]["team_b_logo"] == "https://example.com/nfl-ne.png"
    assert parsed["ncaa"]["events"][0]["sport"] == "NCAA"
    assert parsed["ncaa"]["events"][0]["team_a"] == "\u5fb7\u5dde"
    assert parsed["ncaa"]["events"][0]["team_b"] == "\u5bc6\u6b47\u6839"
    assert parsed["ncaa"]["events"][0]["team_a_rank"] == 12
    assert parsed["ncaa"]["events"][0]["team_a_zh"] == "\u5fb7\u5dde"
    assert parsed["ncaa"]["events"][0]["team_b_zh"] == "\u5bc6\u6b47\u6839"
    assert parsed["ncaa"]["events"][0]["neutral_site"] is True
    assert parsed["ncaa"]["events"][0]["note"] == "Kickoff Classic"
    assert parsed["ncaa"]["events"][0]["team_a_logo"] == "https://example.com/ncaa-tex.png"
    assert parsed["ncaa"]["events"][0]["team_b_logo"] == "https://example.com/ncaa-mich.png"


def test_scoreboard_parsers_build_logo_fallback_urls_when_payload_logo_is_missing():
    la = ZoneInfo("America/Los_Angeles")
    wnba_payload = json.loads(json.dumps(_sample_wnba_scoreboard_payload()))
    for competitor in wnba_payload["events"][0]["competitions"][0]["competitors"]:
        competitor["team"].pop("logo", None)
        competitor["team"].pop("logos", None)
    wnba_event = SportsDashboard._parse_wnba_scoreboard(wnba_payload, la)["events"][0]

    nfl_payload = json.loads(json.dumps(_sample_nfl_scoreboard_payload()))
    for event in nfl_payload["events"]:
        for competitor in event["competitions"][0]["competitors"]:
            competitor["team"].pop("logo", None)
            competitor["team"].pop("logos", None)
    nfl_event = SportsDashboard._parse_football_scoreboard(nfl_payload, la, "NFL")["events"][0]

    ncaa_payload = json.loads(json.dumps(_sample_ncaa_scoreboard_payload()))
    for competitor in ncaa_payload["events"][0]["competitions"][0]["competitors"]:
        competitor["team"].pop("logo", None)
        competitor["team"].pop("logos", None)
        competitor["team"].pop("id", None)
    ncaa_event = SportsDashboard._parse_football_scoreboard(ncaa_payload, la, "NCAA")["events"][0]

    assert wnba_event["team_a_logo"].endswith("/wnba/500/sea.png")
    assert wnba_event["team_b_logo"].endswith("/wnba/500/lv.png")
    assert nfl_event["team_a_logo"].endswith("/nfl/500/sea.png")
    assert nfl_event["team_b_logo"].endswith("/nfl/500/ne.png")
    assert ncaa_event["team_a_logo"].endswith("/ncaa/500/251.png")
    assert ncaa_event["team_b_logo"].endswith("/ncaa/500/130.png")


def test_wnba_parser_handles_2026_expansion_teams_without_payload_logos():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_wnba_scoreboard_payload()))
    competition = payload["events"][0]["competitions"][0]
    home_competitor, away_competitor = competition["competitors"]
    away_competitor["team"] = {
        "abbreviation": "POR",
        "shortDisplayName": "Fire",
        "displayName": "Portland Fire",
    }
    home_competitor["team"] = {
        "abbreviation": "TOR",
        "shortDisplayName": "Tempo",
        "displayName": "Toronto Tempo",
    }

    event = SportsDashboard._parse_wnba_scoreboard(payload, la)["events"][0]

    assert event["team_a"] == "\u6ce2\u7279\u5170\u706b\u7130"
    assert event["team_b"] == "\u591a\u4f26\u591a\u8282\u594f"
    assert event["team_a_code"] == "POR"
    assert event["team_b_code"] == "TOR"
    assert event["team_a_logo"].endswith("/wnba/500/por.png")
    assert event["team_b_logo"].endswith("/wnba/500/tor.png")


def test_wnba_phoenix_alt_code_uses_mercury_chinese_name_and_logo():
    assert SportsDashboard._wnba_display_team_name("PHO", "Mercury") == "\u6c34\u661f"
    assert (
        SportsDashboard._wnba_display_team_name("PHO", "Mercury", full=True)
        == "\u83f2\u5c3c\u514b\u65af\u6c34\u661f"
    )
    assert SportsDashboard._espn_cdn_team_logo_url("wnba", "PHO").endswith("/wnba/500/phx.png")


def test_wnba_parser_captures_espn_winner_flags_by_display_side():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_wnba_scoreboard_payload()))
    competition = payload["events"][0]["competitions"][0]
    competition["competitors"][0]["winner"] = True
    competition["competitors"][1]["winner"] = False

    event = SportsDashboard._parse_wnba_scoreboard(payload, la)["events"][0]

    assert event["winner_a"] is False
    assert event["winner_b"] is True


def test_wnba_parser_captures_espn_odds_lines():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_wnba_scoreboard_payload()))
    competition = payload["events"][0]["competitions"][0]
    competition["odds"] = [{"details": "NY -4.5", "overUnder": 166.5}]

    event = SportsDashboard._parse_wnba_scoreboard(payload, la)["events"][0]

    assert event["spread"] == "NY -4.5"
    assert event["over_under"] == "O/U 166.5"


def test_nba_parser_captures_espn_spread_and_total_lines():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_nba_scoreboard_payload()))
    competition = payload["events"][1]["competitions"][0]
    competition["odds"] = [{"details": "NY -4.5", "overUnder": 221.5}]

    event = SportsDashboard._parse_nba_espn_events(payload, la)[1]

    assert event["spread"] == "NY -4.5"
    assert event["over_under"] == "O/U 221.5"
    assert SportsDashboard._nba_line_total_label(event) == "SPREAD NY -4.5  |  O/U 221.5"


def test_wnba_connecticut_sun_uses_official_chinese_short_name():
    assert SportsDashboard._wnba_display_team_name("CON", "Sun") == "\u592a\u9633"
    assert SportsDashboard._wnba_display_team_name("CONN", "Sun") == "\u592a\u9633"
    assert (
        SportsDashboard._wnba_display_team_name("CON", "Connecticut Sun", full=True)
        == "\u5eb7\u6d85\u72c4\u683c\u592a\u9633"
    )


def test_wnba_golden_state_uses_official_chinese_short_name():
    assert SportsDashboard._wnba_display_team_name("GS", "Valkyries") == "\u5973\u6b66\u795e"
    assert SportsDashboard._wnba_display_team_name("GS", "Valkyries", full=True) == "\u91d1\u5dde\u5973\u6b66\u795e"
    assert (
        SportsDashboard._wnba_display_team_name(
            "",
            "Golden State Valkyries",
            ["Golden State Valkyries", "Valks"],
        )
        == "\u5973\u6b66\u795e"
    )


def test_wnba_2026_expansion_teams_use_chinese_names_and_logo_fallbacks():
    assert SportsDashboard._wnba_display_team_name("POR", "Fire") == "\u6ce2\u7279\u5170\u706b\u7130"
    assert SportsDashboard._wnba_display_team_name("TOR", "Tempo") == "\u591a\u4f26\u591a\u8282\u594f"
    assert SportsDashboard._wnba_display_team_name("TOR", "Tempo", full=True) == "\u591a\u4f26\u591a\u8282\u594f"
    assert SportsDashboard._wnba_display_team_name("", "Portland Fire", ["Fire"]) == "\u6ce2\u7279\u5170\u706b\u7130"
    assert SportsDashboard._wnba_display_team_name("", "Toronto Tempo", ["Tempo"]) == "\u591a\u4f26\u591a\u8282\u594f"
    assert SportsDashboard._espn_cdn_team_logo_url("wnba", "POR").endswith("/wnba/500/por.png")
    assert SportsDashboard._espn_cdn_team_logo_url("wnba", "TOR").endswith("/wnba/500/tor.png")


def test_offseason_hub_team_chinese_name_maps_cover_known_logo_codes():
    league_maps = [
        (MLB_TEAM_ZH_NAMES, MLB_TEAM_ZH_FULL_NAMES),
        (WNBA_TEAM_ZH_NAMES, WNBA_TEAM_ZH_FULL_NAMES),
        (NFL_TEAM_ZH_NAMES, NFL_TEAM_ZH_FULL_NAMES),
        (NCAA_TEAM_ZH_NAMES, NCAA_TEAM_ZH_FULL_NAMES),
    ]

    for short_names, full_names in league_maps:
        assert set(short_names) <= set(full_names)
        for code, short_name in short_names.items():
            assert short_name
            assert short_name.upper() != code
            full_name = full_names[code]
            assert full_name
            assert full_name.upper() != code

    assert set(NCAA_ESPN_LOGO_IDS) <= set(NCAA_TEAM_ZH_NAMES)
    assert set(NCAA_ESPN_LOGO_IDS) <= set(NCAA_TEAM_ZH_FULL_NAMES)


def test_ncaa_logo_fallback_uses_espn_numeric_ids_for_code_only_events():
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "TULN").endswith("/ncaa/500/2655.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "UNLV").endswith("/ncaa/500/2439.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "IU").endswith("/ncaa/500/84.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "NU").endswith("/ncaa/500/77.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "TEM").endswith("/ncaa/500/218.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "TLSA").endswith("/ncaa/500/202.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "VAN").endswith("/ncaa/500/238.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "BUFF").endswith("/ncaa/500/2084.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "BUF").endswith("/ncaa/500/2084.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "CHAR").endswith("/ncaa/500/2429.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "CLT").endswith("/ncaa/500/2429.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "NCSU").endswith("/ncaa/500/152.png")
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "JXST").endswith("/ncaa/500/55.png")
    assert SportsDashboard._ncaa_display_school_name("NCSU", "NCSU") == "\u5317\u5361\u5dde\u7acb"
    assert SportsDashboard._ncaa_display_school_name("NCSU", "NCSU", full=True) == "\u5317\u5361\u5dde\u7acb\u72fc\u7fa4"
    assert SportsDashboard._ncaa_display_school_name("CLT", "CLT") == "\u590f\u6d1b\u7279"
    assert SportsDashboard._ncaa_display_school_name("CLT", "CLT", full=True) == "\u590f\u6d1b\u727949\u4eba"
    assert SportsDashboard._ncaa_display_school_name("JXST", "JXST") == "\u6770\u514b\u900a\u7ef4\u5c14\u5dde\u7acb"
    assert SportsDashboard._ncaa_display_school_name("JXST", "JXST", full=True) == "\u6770\u514b\u900a\u7ef4\u5c14\u5dde\u7acb\u6597\u9e21"
    assert SportsDashboard._ncaa_display_school_name("BUF", "BUF") == "\u5e03\u6cd5\u7f57"
    assert SportsDashboard._ncaa_display_school_name("BUF", "BUF", full=True) == "\u5e03\u6cd5\u7f57\u516c\u725b"
    assert SportsDashboard._ncaa_display_school_name("IU", "IU") == "\u5370\u7b2c\u5b89\u7eb3"
    assert SportsDashboard._ncaa_display_school_name("IU", "IU", full=True) == "\u5370\u7b2c\u5b89\u7eb3\u80e1\u5e0c\u5c14\u4eba"
    assert SportsDashboard._ncaa_display_school_name("NU", "NU") == "\u897f\u5317"
    assert SportsDashboard._ncaa_display_school_name("NU", "NU", full=True) == "\u897f\u5317\u91ce\u732b"
    assert SportsDashboard._ncaa_display_school_name("TEM", "TEM") == "\u5929\u666e"
    assert SportsDashboard._ncaa_display_school_name("TEM", "TEM", full=True) == "\u5929\u666e\u732b\u5934\u9e70"
    assert SportsDashboard._ncaa_display_school_name("TLSA", "TLSA") == "\u5854\u5c14\u8428"
    assert SportsDashboard._ncaa_display_school_name("TLSA", "TLSA", full=True) == "\u5854\u5c14\u8428\u91d1\u8272\u98d3\u98ce"
    assert SportsDashboard._ncaa_display_school_name("VAN", "VAN") == "\u8303\u5fb7\u5821"
    assert SportsDashboard._ncaa_display_school_name("VAN", "VAN", full=True) == "\u8303\u5fb7\u5821\u51c6\u5c06"


def test_espn_cdn_team_logo_url_accepts_team_name_aliases():
    assert SportsDashboard._espn_cdn_team_logo_url("ncaa", "Notre Dame Fighting Irish").endswith("/ncaa/500/87.png")
    assert SportsDashboard._espn_cdn_team_logo_url("nfl", "New England Patriots").endswith("/nfl/500/ne.png")
    assert SportsDashboard._espn_cdn_team_logo_url("wnba", "Las Vegas Aces").endswith("/wnba/500/lv.png")
    assert SportsDashboard._espn_cdn_team_logo_url("mlb", "Los Angeles Dodgers").endswith("/mlb/500/lad.png")


def test_small_row_logo_fallback_normalizes_team_name_aliases():
    assert (
        SportsDashboard._small_row_logo_fallback(
            {"sport": "WNBA", "team_a": "\u98ce\u66b4", "team_a_name": "Seattle Storm"},
            "a",
        )
        == "SEA"
    )
    assert (
        SportsDashboard._small_row_logo_fallback(
            {"sport": "NFL", "team_a": "Patriots", "team_a_name": "New England Patriots"},
            "a",
        )
        == "NE"
    )
    assert (
        SportsDashboard._small_row_logo_fallback(
            {"sport": "NCAA", "team_b": "Notre Dame Fighting Irish", "team_b_name": "Notre Dame"},
            "b",
        )
        == "ND"
    )
    assert (
        SportsDashboard._small_row_logo_fallback(
            {"sport": "MLB", "team_b": "Los Angeles Dodgers"},
            "b",
        )
        == "LAD"
    )


def test_main_card_logo_fallbacks_normalize_team_name_aliases():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    logo_fallbacks = []

    def capture_team_score(*_args, **kwargs):
        logo_fallbacks.append(str(kwargs.get("logo_fallback") or ""))

    plugin._draw_hub_team_score = capture_team_score

    mlb_event = {
        "sport": "MLB",
        "state": "scheduled",
        "start": now + timedelta(hours=3),
        "team_a": "Los Angeles Dodgers",
        "team_b": "San Francisco Giants",
        "wins_a": None,
        "wins_b": None,
    }
    plugin._draw_mlb_main_card(
        image,
        draw,
        (0, 0, 300, 190),
        {"sport": "MLB", "status": "NEXT", "main": mlb_event},
        now,
    )
    assert logo_fallbacks[-2:] == ["LAD", "SF"]

    nfl_event = {
        "sport": "NFL",
        "state": "scheduled",
        "start": now + timedelta(days=2),
        "team_a": "Patriots",
        "team_a_name": "New England Patriots",
        "team_b": "Seattle Seahawks",
        "wins_a": None,
        "wins_b": None,
    }
    plugin._draw_football_main_card(
        image,
        draw,
        (0, 0, 320, 190),
        {"sport": "NFL", "status": "NEXT", "main": nfl_event},
        now,
        "NFL",
    )
    assert logo_fallbacks[-2:] == ["NE", "SEA"]

    ncaa_event = {
        "sport": "NCAA",
        "state": "scheduled",
        "start": now + timedelta(days=4),
        "team_a": "Notre Dame Fighting Irish",
        "team_a_name": "Notre Dame",
        "team_b": "Texas Longhorns",
        "team_b_name": "Texas",
        "wins_a": None,
        "wins_b": None,
    }
    plugin._draw_football_main_card(
        image,
        draw,
        (0, 0, 320, 190),
        {"sport": "NCAA", "status": "NEXT", "main": ncaa_event},
        now,
        "NCAA",
    )
    assert logo_fallbacks[-2:] == ["ND", "TEX"]


def test_ncaa_chinese_name_maps_cover_all_known_espn_codes():
    missing_short_names = [code for code in NCAA_ESPN_LOGO_IDS if code not in NCAA_TEAM_ZH_NAMES]
    missing_full_names = [code for code in NCAA_TEAM_ZH_NAMES if code not in NCAA_TEAM_ZH_FULL_NAMES]

    assert missing_short_names == []
    assert missing_full_names == []


def test_nfl_alt_codes_use_chinese_names_and_canonical_logo_codes():
    cases = [
        ("JAC", "\u7f8e\u6d32\u864e", "\u6770\u514b\u900a\u7ef4\u5c14\u7f8e\u6d32\u864e", "/nfl/500/jax.png"),
        ("ARZ", "\u7ea2\u96c0", "\u4e9a\u5229\u6851\u90a3\u7ea2\u96c0", "/nfl/500/ari.png"),
    ]
    for code, short_name, full_name, logo_suffix in cases:
        assert SportsDashboard._football_display_team_name(code, code, "NFL") == short_name
        assert SportsDashboard._football_display_team_name(code, code, "NFL", full=True) == full_name
        assert SportsDashboard._espn_cdn_team_logo_url("nfl", code).endswith(logo_suffix)


def test_football_week_label_prefers_named_stage_over_generic_number():
    assert SportsDashboard._football_week_label({"week": {"number": 1, "text": "Week 1"}}) == "WEEK 1"
    assert SportsDashboard._football_week_label({"week": {"number": 23, "text": "Super Bowl"}}) == "SUPER BOWL"
    assert SportsDashboard._football_week_label({"week": {"text": "Bowl Season"}}) == "BOWL SEASON"
    assert SportsDashboard._football_header_week_label({}, {"week": "01"}) == "WEEK 1"
    assert SportsDashboard._football_header_week_label({"week_label": "SUPER BOWL"}, {"week": 23}) == "SUPER BOWL"


def test_football_parser_preserves_event_level_named_stage_label():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_nfl_scoreboard_payload()))
    payload["events"][0]["week"] = {"number": 23, "text": "Super Bowl"}

    event = SportsDashboard._parse_football_scoreboard(payload, la, "NFL")["events"][0]

    assert event["week"] == 23
    assert event["week_label"] == "SUPER BOWL"
    assert SportsDashboard._football_header_week_label({}, event) == "SUPER BOWL"


def test_football_parser_captures_espn_winner_flags_by_display_side():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_nfl_scoreboard_payload()))
    competition = payload["events"][0]["competitions"][0]
    competition["competitors"][0]["winner"] = True
    competition["competitors"][1]["winner"] = False

    event = SportsDashboard._parse_football_scoreboard(payload, la, "NFL")["events"][0]

    assert event["winner_a"] is True
    assert event["winner_b"] is False


def test_football_live_drive_rows_do_not_let_empty_situation_hide_context():
    event = {
        "sport": "NFL",
        "state": "in",
        "status_text": "Q2 8:42",
        "down_distance": "",
        "yard_line": "",
        "possession": "",
        "last_play": "Timeout New England",
        "broadcast": "NBC",
        "spread": "NE -2.5",
        "over_under": "O/U 44.5",
        "venue": "Gillette Stadium",
    }

    rows = SportsDashboard._football_live_drive_rows(event, include_context=True, sport="NFL")

    assert rows == [
        ("QTR", "Q2 8:42"),
        ("PLAY", "Timeout New England"),
        ("TV", "NBC / NE -2.5 / O/U 44.5"),
        ("VENUE", "Gillette Stadium"),
    ]


def test_nfl_info_rows_combine_line_with_tv_to_keep_venue_visible():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 10, 17, 30, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")
    next_event = next(event for event in parsed["events"] if event["event_id"] == "nfl-next")

    game_rows = SportsDashboard._football_game_info_rows(next_event, "NFL", now)

    assert game_rows[-3:] == [
        ("TV", "FOX / CHI -1.5 / O/U 42.5"),
        ("VENUE", "Soldier Field"),
        ("RECORD", "\u5305\u88c5\u5de5 0-0 / \u718a 0-0"),
    ]

    final_event = dict(next_event)
    final_event.update({"state": "post", "wins_a": 24, "wins_b": 21, "winner_a": True, "winner_b": False})

    final_rows = SportsDashboard._football_final_snap_rows(final_event, "NFL")

    assert final_rows[-2:] == [
        ("TV", "FOX / CHI -1.5 / O/U 42.5"),
        ("VENUE", "Soldier Field"),
    ]


def test_football_live_game_info_rows_prioritize_context_location_and_records():
    la = ZoneInfo("America/Los_Angeles")
    nfl_live = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")["events"][0]
    ncaa_live = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")["events"][0]

    assert SportsDashboard._football_live_game_info_rows(nfl_live, "NFL") == [
        ("TV", "NBC / NE -2.5 / O/U 44.5"),
        ("VENUE", "Gillette Stadium"),
        ("RECORD", "\u6d77\u9e70 0-0 / \u7231\u56fd\u8005 0-0"),
    ]
    assert SportsDashboard._football_live_game_info_rows(ncaa_live, "NCAA") == [
        ("TV", "ESPN / TEX -6.5 / O/U 52.5"),
        ("SITE", "NEUTRAL / Kickoff Classic / AT&T Stadium"),
        ("RECORD", "#12 \u5fb7\u5dde 0-0 / #7 \u5bc6\u6b47\u6839 0-0"),
    ]
    assert SportsDashboard._ncaa_main_meta_label(ncaa_live) == "NEUTRAL / Kickoff Classic / AT&T Stadium"
    assert "SPREAD" not in SportsDashboard._ncaa_main_meta_label(ncaa_live)


def test_wnba_info_rows_map_short_codes_to_chinese_team_names():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    event = {
        "state": "in",
        "start": now - timedelta(minutes=30),
        "status_text": "Q3 4:22",
        "team_a": "PHX",
        "team_b": "NY",
        "wins_a": 72,
        "wins_b": 78,
        "record_a": "3-1",
        "record_b": "2-2",
    }

    assert SportsDashboard._wnba_record_matchup_label(event) == "\u6c34\u661f 3-1 / \u81ea\u7531\u4eba 2-2"
    assert SportsDashboard._wnba_lead_label(event) == "\u81ea\u7531\u4eba +6"
    rows = SportsDashboard._wnba_live_pulse_rows(event)
    assert ("SCORE", "\u6c34\u661f 72 / \u81ea\u7531\u4eba 78", False) in rows


def test_wnba_result_snap_rows_split_media_line_and_venue():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_wnba_scoreboard_payload()))
    payload["events"][0]["competitions"][0]["odds"] = [{"details": "LV -4.5", "overUnder": 166.5}]
    event = SportsDashboard._parse_wnba_scoreboard(payload, la)["events"][0]
    event = dict(event)
    event.update({"state": "post", "status_text": "Final"})

    rows = SportsDashboard._wnba_result_snap_rows(event)

    assert ("TV", "ION / LV -4.5 / O/U 166.5", False) in rows
    assert ("VENUE", "Michelob ULTRA Arena", False) in rows
    assert not any(label == "INFO" for label, _value, _accent in rows)
    compact_rows = SportsDashboard._wnba_result_snap_rows(event, include_quarter=False)
    assert not any(label == "QTR" for label, _value, _accent in compact_rows)


def test_offseason_hub_selects_one_primary_sport_card():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = {
        "mlb": SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la),
        "wnba": {"events": []},
        "pga": {"events": []},
    }

    selected = SportsDashboard._select_offseason_hub(parsed, now)

    assert selected["primary"]["sport"] == "MLB"
    assert selected["primary"]["status"] == "LIVE"
    assert selected["primary"]["main"]["team_b"] == "\u9053\u5947"
    assert selected["primary"]["main"]["team_b_code"] == "LAD"
    assert selected["rotation_pool"] == ["MLB"]


def test_offseason_hub_rotates_between_multiple_live_sports():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = {
        "mlb": {
            "events": [
                {
                    "sport": "MLB",
                    "state": "in",
                    "start": now - timedelta(hours=1),
                    "team_a": "\u5de8\u4eba",
                    "team_b": "\u9053\u5947",
                    "wins_a": 2,
                    "wins_b": 3,
                }
            ]
        },
        "wnba": {
            "events": [
                {
                    "sport": "WNBA",
                    "state": "in",
                    "start": now - timedelta(minutes=40),
                    "team_a": "\u98ce\u66b4",
                    "team_b": "\u738b\u724c",
                    "wins_a": 64,
                    "wins_b": 61,
                }
            ]
        },
        "pga": {"events": []},
        "nfl": {
            "events": [
                {
                    "sport": "NFL",
                    "state": "pre",
                    "start": now + timedelta(days=1),
                    "team_a": "\u914b\u957f",
                    "team_b": "\u6bd4\u5c14",
                }
            ]
        },
    }

    first = SportsDashboard._select_offseason_hub(parsed, now)
    second = SportsDashboard._select_offseason_hub(parsed, now + timedelta(minutes=OFFSEASON_HUB_ROTATION_MINUTES))

    assert first["rotation_pool"] == ["WNBA", "MLB"]
    assert second["rotation_pool"] == ["WNBA", "MLB"]
    assert {first["primary"]["sport"], second["primary"]["sport"]} == {"MLB", "WNBA"}
    assert first["primary"]["sport"] != second["primary"]["sport"]


def test_offseason_hub_rotates_standalone_sports_when_no_live_priority():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = {
        "mlb": {
            "events": [
                {
                    "sport": "MLB",
                    "state": "pre",
                    "start": now + timedelta(hours=3),
                    "team_a": "SF",
                    "team_b": "LAD",
                }
            ]
        },
        "wnba": {
            "events": [
                {
                    "sport": "WNBA",
                    "state": "pre",
                    "start": now + timedelta(hours=4),
                    "team_a": "SEA",
                    "team_b": "LV",
                }
            ]
        },
        "pga": {"events": []},
    }

    first = SportsDashboard._select_offseason_hub(parsed, now)
    second = SportsDashboard._select_offseason_hub(parsed, now + timedelta(minutes=OFFSEASON_HUB_ROTATION_MINUTES))

    assert first["rotation_pool"] == ["MLB", "WNBA"]
    assert second["rotation_pool"] == ["MLB", "WNBA"]
    assert {first["primary"]["sport"], second["primary"]["sport"]} == {"MLB", "WNBA"}
    assert first["primary"]["sport"] != second["primary"]["sport"]


def test_offseason_hub_rotates_every_active_standalone_sport_independently():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = {
        "mlb": {
            "events": [
                {"sport": "MLB", "state": "pre", "start": now + timedelta(hours=8), "team_a": "SF", "team_b": "LAD"}
            ]
        },
        "wnba": {
            "events": [
                {"sport": "WNBA", "state": "pre", "start": now + timedelta(hours=9), "team_a": "SEA", "team_b": "LV"}
            ]
        },
        "pga": {
            "events": [
                {
                    "sport": "PGA",
                    "state": "scheduled",
                    "start": now + timedelta(hours=10),
                    "end": now + timedelta(days=3),
                    "name": "PGA TOUR",
                }
            ]
        },
        "nfl": {
            "events": [
                {"sport": "NFL", "state": "pre", "start": now + timedelta(hours=11), "team_a": "KC", "team_b": "BUF"}
            ]
        },
        "ncaa": {
            "events": [
                {"sport": "NCAA", "state": "pre", "start": now + timedelta(hours=12), "team_a": "TEX", "team_b": "MICH"}
            ]
        },
    }

    selections = [
        SportsDashboard._select_offseason_hub(
            parsed,
            now + timedelta(minutes=OFFSEASON_HUB_ROTATION_MINUTES * index),
        )
        for index in range(5)
    ]

    assert selections[0]["rotation_pool"] == ["MLB", "WNBA", "PGA", "NFL", "NCAA"]
    assert {selection["primary"]["sport"] for selection in selections} == {"MLB", "WNBA", "PGA", "NFL", "NCAA"}
    for selection in selections:
        assert len(selection["cards"]) == 5
        assert [card["sport"] for card in selection["cards"]] == ["MLB", "WNBA", "PGA", "NFL", "NCAA"]
        assert selection["primary"] in selection["cards"]


def test_offseason_hub_prioritizes_urgent_next_game_before_full_rotation():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = {
        "mlb": {
            "events": [
                {
                    "sport": "MLB",
                    "state": "pre",
                    "start": now + timedelta(hours=4),
                    "team_a": "SF",
                    "team_b": "LAD",
                }
            ]
        },
        "wnba": {
            "events": [
                {
                    "sport": "WNBA",
                    "state": "pre",
                    "start": now + timedelta(minutes=45),
                    "team_a": "SEA",
                    "team_b": "LV",
                }
            ]
        },
        "pga": {
            "events": [
                {
                    "sport": "PGA",
                    "state": "scheduled",
                    "start": now + timedelta(hours=2),
                    "end": now + timedelta(days=3),
                    "name": "PGA TOUR",
                }
            ]
        },
    }

    selected = SportsDashboard._select_offseason_hub(parsed, now)

    assert selected["rotation_pool"] == ["WNBA"]
    assert selected["primary"]["sport"] == "WNBA"


def test_offseason_hub_pins_soonest_urgent_next_game():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = {
        "mlb": {
            "events": [
                {
                    "sport": "MLB",
                    "state": "pre",
                    "start": now + timedelta(minutes=85),
                    "team_a": "SF",
                    "team_b": "LAD",
                }
            ]
        },
        "wnba": {
            "events": [
                {
                    "sport": "WNBA",
                    "state": "pre",
                    "start": now + timedelta(minutes=65),
                    "team_a": "SEA",
                    "team_b": "LV",
                }
            ]
        },
        "pga": {
            "events": [
                {
                    "sport": "PGA",
                    "state": "scheduled",
                    "start": now + timedelta(hours=4),
                    "end": now + timedelta(days=3),
                    "name": "PGA TOUR",
                }
            ]
        },
    }

    first = SportsDashboard._select_offseason_hub(parsed, now)
    second = SportsDashboard._select_offseason_hub(
        parsed,
        now + timedelta(minutes=OFFSEASON_HUB_ROTATION_MINUTES),
    )

    assert first["rotation_pool"] == ["WNBA", "MLB"]
    assert second["rotation_pool"] == ["WNBA", "MLB"]
    assert first["primary"]["sport"] == "WNBA"
    assert second["primary"]["sport"] == "WNBA"


def test_offseason_hub_card_prioritizes_soonest_upcoming_event():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    later = {"sport": "WNBA", "event_id": "later", "state": "pre", "start": now + timedelta(hours=5)}
    sooner = {"sport": "WNBA", "event_id": "sooner", "state": "pre", "start": now + timedelta(hours=1)}

    card = SportsDashboard._offseason_hub_card("WNBA", {"events": [later, sooner]}, now)

    assert card["main"]["event_id"] == "sooner"
    assert [event["event_id"] for event in card["upcoming"]] == ["sooner", "later"]


def test_offseason_hub_card_prioritizes_most_recent_live_event():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    older = {"sport": "MLB", "event_id": "older-live", "state": "in", "start": now - timedelta(hours=3)}
    newer = {"sport": "MLB", "event_id": "newer-live", "state": "in", "start": now - timedelta(minutes=40)}

    card = SportsDashboard._offseason_hub_card("MLB", {"events": [older, newer]}, now)

    assert card["main"]["event_id"] == "newer-live"
    assert [event["event_id"] for event in card["live"]] == ["newer-live", "older-live"]


def test_offseason_hub_state_marks_live_and_expiry(monkeypatch):
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    start = now - timedelta(minutes=40)
    captured = {}

    def capture_write(path, payload):
        captured["path"] = path
        captured["payload"] = payload

    monkeypatch.setattr(plugin, "_write_json_file", capture_write)

    plugin._write_offseason_hub_state(
        {
            "primary": {
                "sport": "MLB",
                "status": "LIVE",
                "main": {"event_id": "mlb-live", "start": start},
            },
            "rotation_pool": ["MLB", "WNBA"],
        },
        now,
        "HUB LIVE",
    )

    payload = captured["payload"]
    assert payload["version"] == "sports-dashboard-offseason-hub-v1"
    assert payload["has_live"] is True
    assert payload["status"] == "LIVE"
    assert payload["sport"] == "MLB"
    assert payload["event_id"] == "mlb-live"
    assert payload["live_until"] == (start + timedelta(hours=5)).astimezone(timezone.utc).isoformat()


def test_offseason_hub_fallback_preserves_all_standalone_sports():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    selected = SportsDashboard._select_offseason_hub(SportsDashboard._fallback_offseason_hub_data(la, now), now)

    assert selected["rotation_pool"] == ["MLB", "WNBA", "NFL", "PGA", "NCAA"]
    assert {card["sport"] for card in selected["cards"]} == {"MLB", "WNBA", "PGA", "NFL", "NCAA"}
    cards = {card["sport"]: card for card in selected["cards"]}
    assert cards["MLB"]["main"]["team_a"] == "\u9053\u5947"
    assert cards["MLB"]["main"]["team_a_code"] == "LAD"
    assert cards["WNBA"]["main"]["team_a"] == "\u81ea\u7531\u4eba"
    assert cards["WNBA"]["main"]["team_a_code"] == "NY"
    assert cards["NFL"]["main"]["team_a"] == "\u914b\u957f"
    assert cards["NFL"]["main"]["team_a_code"] == "KC"
    assert cards["NCAA"]["main"]["team_a"] == "\u5fb7\u5dde"
    assert cards["NCAA"]["main"]["team_a_code"] == "TEX"
    for card in selected["cards"]:
        assert card["status"] == "NEXT"
        assert card["main"]["start"] > now


def test_offseason_hub_uses_fallback_when_live_payload_has_only_break_cards(monkeypatch):
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    empty_payload = {
        "version": "sports-dashboard-offseason-hub-v1",
        "payloads": {
            "mlb": {"dates": []},
            "wnba": {"events": []},
            "pga": {"events": []},
            "nfl": {"events": []},
            "ncaa": {"events": []},
        },
    }

    monkeypatch.setattr(plugin, "_load_offseason_hub_payload", lambda *_args, **_kwargs: (empty_payload, "HUB LIVE", now.isoformat()))

    selected, source_state = plugin._load_offseason_hub({}, la, now)

    assert source_state == "HUB FALLBACK"
    assert selected["primary"]["status"] == "NEXT"
    assert selected["rotation_pool"] == ["MLB", "WNBA", "NFL", "PGA", "NCAA"]
    assert all(card["main"] for card in selected["cards"])


def test_offseason_hub_draw_dispatches_only_primary_sport(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (552, 268), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    calls = []

    monkeypatch.setattr(plugin, "_draw_mlb_standalone_panel", lambda *_args: calls.append("MLB"))
    monkeypatch.setattr(plugin, "_draw_wnba_standalone_panel", lambda *_args: calls.append("WNBA"))
    monkeypatch.setattr(plugin, "_draw_pga_standalone_panel", lambda *_args: calls.append("PGA"))
    monkeypatch.setattr(plugin, "_draw_nfl_standalone_panel", lambda *_args: calls.append("NFL"))
    monkeypatch.setattr(plugin, "_draw_ncaa_standalone_panel", lambda *_args: calls.append("NCAA"))

    plugin._draw_offseason_hub_compact_panel(
        image,
        draw,
        (0, 0, 551, 267),
        {
            "primary": {"sport": "PGA", "status": "LIVE"},
            "cards": [
                {"sport": "MLB", "status": "LIVE"},
                {"sport": "WNBA", "status": "LIVE"},
                {"sport": "PGA", "status": "LIVE"},
                {"sport": "NFL", "status": "LIVE"},
                {"sport": "NCAA", "status": "LIVE"},
            ],
        },
        "HUB LIVE",
        datetime(2026, 6, 14, 13, 30, tzinfo=ZoneInfo("America/Los_Angeles")),
    )

    assert calls == ["PGA"]


def test_standalone_sport_panels_draw_their_own_information():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    selected = SportsDashboard._select_offseason_hub(
        {
            "mlb": SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la),
            "wnba": SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la),
            "pga": SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), la, now),
            "nfl": SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL"),
            "ncaa": SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA"),
        },
        now,
    )
    seen_texts = []
    logo_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def capture_team_logo(_image, _draw, logo_url, _x, _y, size, fallback_text):
        logo_calls.append((str(logo_url or ""), int(size), str(fallback_text or "")))

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = capture_team_logo
    for sport in ("MLB", "WNBA", "PGA", "NFL", "NCAA"):
        card = next(card for card in selected["cards"] if card["sport"] == sport)
        image = Image.new("RGB", (552, 268), COLORS["panel"])
        draw = ImageDraw.Draw(image)
        plugin._draw_offseason_hub_compact_panel(image, draw, (0, 0, 551, 267), {"primary": card}, "HUB LIVE", now)

    assert "MLB LIVE" in seen_texts
    assert "R/H/E 3/7/1" in seen_texts
    assert "R/H/E 5/8/0" in seen_texts
    assert "BAT" in seen_texts
    assert "M. Chapman" in seen_texts
    assert "P" in seen_texts
    assert "Y. Yamamoto" in seen_texts
    assert "B/P M. Chapman / Y. Yamamoto" not in seen_texts
    assert "SP J. deGrom / L. Castillo" in seen_texts
    assert "LIVE STATE" in seen_texts
    assert "1B 3B" in seen_texts
    assert "WNBA LIVE" in seen_texts
    assert "QTR DATA PENDING" not in seen_texts
    assert "LIVE GAME" in seen_texts
    assert "QUARTER LOG" in seen_texts
    assert "PGA LIVE" in seen_texts
    assert "LEADER" in seen_texts
    assert "S. Scheffler" in seen_texts
    assert "RND" not in seen_texts
    assert "DAY" not in seen_texts
    assert "CARD" not in seen_texts
    assert "R3 68 / -2" in seen_texts
    assert "LEADERBOARD" in seen_texts
    assert "NFL LIVE" in seen_texts
    assert "\u897f\u96c5\u56fe\u6d77\u9e70" in seen_texts
    assert "\u65b0\u82f1\u683c\u5170\u7231\u56fd\u8005" in seen_texts
    assert "3RD & 4" in seen_texts
    assert any("Kenneth Walker run for 6 yards" in text for text in seen_texts)
    assert "TV NBC  |  SPREAD NE -2.5  |  O/U 44.5" in seen_texts
    assert "NCAA LIVE" in seen_texts
    assert "CFB" in seen_texts
    assert "\u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b" in seen_texts
    assert "\u5bc6\u6b47\u6839\u72fc\u737e" in seen_texts
    assert "NEUTRAL / Kickoff Classic / AT&T Stadium" in seen_texts
    assert "NEUTRAL SITE  |  AT&T Stadium  |  TV ESPN / SPREAD TEX -6.5 / O/U 52.5" not in seen_texts
    assert "LIVE DRIVE" in seen_texts
    assert "COLLEGE DRIVE" in seen_texts
    assert ("https://a.espncdn.com/i/teamlogos/mlb/500/sf.png", 20, "SF") in logo_calls
    assert any(call[0] == "https://example.com/wnba-sea.png" and call[1] == 20 for call in logo_calls)
    assert ("https://example.com/nfl-sea.png", 20, "SEA") in logo_calls
    assert ("https://example.com/ncaa-tex.png", 20, "TEX") in logo_calls
    assert any(call == ("https://a.espncdn.com/i/teamlogos/mlb/500/tex.png", 11, "TEX") for call in logo_calls)




def test_pga_title_wordmark_asset_is_transparent():
    wordmark = Image.open(LOCAL_PGA_TITLE_WORDMARK_PATH).convert("RGBA")
    assert wordmark.size == (154, 24)
    alpha = wordmark.getchannel("A")
    assert alpha.getbbox() is not None
    assert alpha.getextrema() == (0, 255)
    assert wordmark.getpixel((0, 0))[3] == 0
    assert wordmark.getpixel((wordmark.width - 1, wordmark.height - 1))[3] == 0


def test_mlb_title_wordmark_asset_is_transparent():
    wordmark = Image.open(LOCAL_MLB_TITLE_WORDMARK_PATH).convert("RGBA")
    assert wordmark.size == (154, 24)
    alpha = wordmark.getchannel("A")
    assert alpha.getbbox() is not None
    assert alpha.getextrema() == (0, 255)
    assert wordmark.getpixel((0, 0))[3] == 0
    assert wordmark.getpixel((wordmark.width - 1, wordmark.height - 1))[3] == 0

def test_wnba_title_wordmark_asset_is_transparent():
    wordmark = Image.open(LOCAL_WNBA_TITLE_WORDMARK_PATH).convert("RGBA")
    assert wordmark.size == (154, 24)
    alpha = wordmark.getchannel("A")
    assert alpha.getbbox() is not None
    assert alpha.getextrema() == (0, 255)
    assert wordmark.getpixel((0, 0))[3] == 0
    assert wordmark.getpixel((wordmark.width - 1, wordmark.height - 1))[3] == 0


def test_standalone_mlb_header_uses_img2_title_wordmark(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (552, 80), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    wordmark_calls = []
    fit_texts = []
    original_fit_text = plugin._fit_text

    def capture_wordmark(_image, x, y, max_width, max_height):
        wordmark_calls.append((x, y, max_width, max_height))
        return True

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        fit_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_mlb_title_wordmark", capture_wordmark)
    plugin._fit_text = capture_fit_text
    plugin._draw_sport_logo = lambda *_args, **_kwargs: None
    plugin._draw_status_pill = lambda *_args, **_kwargs: None
    plugin._draw_standalone_sport_header_cutout = lambda *_args, **_kwargs: True

    plugin._draw_standalone_sport_header(
        image,
        draw,
        0,
        0,
        551,
        "MLB",
        {"sport": "MLB", "status": "LIVE"},
        "HUB LIVE",
    )

    assert wordmark_calls == [(98, 7, 154, 24)]
    assert "MLB" not in fit_texts
    assert "LIVE BOX" in fit_texts


    plugin = _plugin()
    image = Image.new("RGB", (552, 80), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    wordmark_calls = []
    fit_texts = []
    original_fit_text = plugin._fit_text

    def capture_wordmark(_image, x, y, max_width, max_height):
        wordmark_calls.append((x, y, max_width, max_height))
        return True

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        fit_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_pga_title_wordmark", capture_wordmark)
    plugin._fit_text = capture_fit_text
    plugin._draw_sport_logo = lambda *_args, **_kwargs: None
    plugin._draw_status_pill = lambda *_args, **_kwargs: None
    plugin._draw_standalone_sport_header_cutout = lambda *_args, **_kwargs: True

    plugin._draw_standalone_sport_header(
        image,
        draw,
        0,
        0,
        551,
        "PGA",
        {"sport": "PGA", "status": "LIVE"},
        "HUB LIVE",
    )

    assert wordmark_calls == [(66, 7, 154, 24)]
    assert "PGA TOUR" not in fit_texts
    assert "LEADERBOARD" in fit_texts

def test_standalone_wnba_header_uses_img2_title_wordmark(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (552, 80), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    wordmark_calls = []
    fit_texts = []
    original_fit_text = plugin._fit_text

    def capture_wordmark(_image, x, y, max_width, max_height):
        wordmark_calls.append((x, y, max_width, max_height))
        return True

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        fit_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_wnba_title_wordmark", capture_wordmark)
    plugin._fit_text = capture_fit_text
    plugin._draw_sport_logo = lambda *_args, **_kwargs: None
    plugin._draw_status_pill = lambda *_args, **_kwargs: None
    plugin._draw_standalone_sport_header_cutout = lambda *_args, **_kwargs: True

    plugin._draw_standalone_sport_header(
        image,
        draw,
        0,
        0,
        551,
        "WNBA",
        {"sport": "WNBA", "status": "LIVE"},
        "HUB LIVE",
    )

    assert wordmark_calls == [(98, 7, 154, 24)]
    assert "WNBA" not in fit_texts
    assert "LIVE GAME" in fit_texts


def test_standalone_sport_header_uses_sport_local_status_label():
    plugin = _plugin()
    image = Image.new("RGB", (552, 80), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_logo = lambda *_args, **_kwargs: None
    plugin._draw_status_pill = lambda *_args, **_kwargs: None

    plugin._draw_standalone_sport_header(image, draw, 0, 0, 551, "MLB", {"sport": "MLB", "status": "NEXT"}, "HUB LIVE")
    plugin._draw_standalone_sport_header(image, draw, 0, 0, 551, "WNBA", {"sport": "WNBA", "status": "NEXT"}, "HUB LIVE")
    plugin._draw_standalone_sport_header(image, draw, 0, 0, 551, "NFL", {"sport": "NFL", "status": "LIVE"}, "HUB LIVE")
    plugin._draw_standalone_sport_header(image, draw, 0, 0, 551, "NFL", {"sport": "NFL", "status": "RECENT"}, "HUB LIVE")
    plugin._draw_standalone_sport_header(image, draw, 0, 0, 551, "NCAA", {"sport": "NCAA", "status": "NEXT"}, "HUB LIVE")
    plugin._draw_standalone_sport_header(image, draw, 0, 0, 551, "PGA", {"sport": "PGA", "status": "LIVE"}, "HUB LIVE")

    assert "FIRST PITCH" in seen_texts
    assert "TIPOFF" in seen_texts
    assert "DRIVE CAST" in seen_texts
    assert "FINAL SNAP" in seen_texts
    assert "RANKED WATCH" in seen_texts
    assert "LEADERBOARD" in seen_texts
    assert "SCHEDULE" not in seen_texts
    assert "HUB LIVE" not in seen_texts
    assert SportsDashboard._standalone_sport_source_label("MLB", {"status": "NEXT"}, "ESPN LIVE") == "ESPN LIVE"


def test_standalone_sport_header_draws_each_sport_cutout():
    plugin = _plugin()
    image = Image.new("RGB", (552, 80), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    cutout_calls = []

    def capture_cutout(_image, sport, x1, y1, x2, y2, accent):
        cutout_calls.append((sport, x1, y1, x2, y2, accent))
        return True

    plugin._draw_sport_logo = lambda *_args, **_kwargs: None
    plugin._draw_status_pill = lambda *_args, **_kwargs: None
    plugin._draw_standalone_sport_header_cutout = capture_cutout

    for sport in ("MLB", "WNBA", "PGA", "NFL", "NCAA"):
        plugin._draw_standalone_sport_header(image, draw, 0, 0, 551, sport, {"sport": sport, "status": "NEXT"}, "HUB LIVE")

    assert [call[0] for call in cutout_calls] == ["MLB", "WNBA", "PGA", "NFL", "NCAA"]
    assert [call[1] for call in cutout_calls] == [202, 202, 170, 170, 170]
    assert all(call[3] - call[1] + 1 >= 258 for call in cutout_calls)
    assert all(call[4] - call[2] + 1 == 47 for call in cutout_calls)


def test_standalone_sport_header_cutout_scales_up_and_left_biases(monkeypatch):
    plugin = _plugin()
    source = Image.new("RGBA", (100, 20), (255, 255, 255, 255))
    monkeypatch.setattr(plugin, "_load_sport_header_cutout", lambda _sport: source)
    image = Image.new("RGB", (260, 80), (0, 0, 0))

    drawn = plugin._draw_standalone_sport_header_cutout(
        image,
        "WNBA",
        20,
        10,
        219,
        49,
        COLORS["wnba_accent"],
    )

    bbox = image.getbbox()
    assert drawn is True
    assert bbox is not None
    drawn_width = bbox[2] - bbox[0]
    drawn_height = bbox[3] - bbox[1]
    assert drawn_width >= int(100 * SPORT_HEADER_CUTOUT_SCALE) - 1
    assert drawn_height >= int(20 * SPORT_HEADER_CUTOUT_SCALE) - 1
    centered_x = 20 + (200 - drawn_width) // 2
    assert bbox[0] < centered_x
    assert centered_x - bbox[0] <= 6


def test_pga_header_cutout_moves_right_twenty_two_pixels(monkeypatch):
    plugin = _plugin()
    source = Image.new("RGBA", (20, 10), (255, 255, 255, 255))
    monkeypatch.setattr(plugin, "_load_sport_header_cutout", lambda _sport: source)
    base_image = Image.new("RGB", (180, 70), (0, 0, 0))
    pga_image = Image.new("RGB", (180, 70), (0, 0, 0))

    assert plugin._draw_standalone_sport_header_cutout(
        base_image,
        "NFL",
        20,
        10,
        119,
        49,
        COLORS["nfl_accent"],
    ) is True
    assert plugin._draw_standalone_sport_header_cutout(
        pga_image,
        "PGA",
        20,
        10,
        119,
        49,
        COLORS["pga_accent"],
    ) is True

    base_bbox = base_image.getbbox()
    pga_bbox = pga_image.getbbox()
    assert base_bbox is not None
    assert pga_bbox is not None
    assert pga_bbox[0] == base_bbox[0] + 22


def test_mlb_side_column_prioritizes_live_state_before_schedule():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "MLB",
        SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la),
        now,
    )
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert "UPCOMING" in seen_texts
    assert "LIVE STATE" in seen_texts
    assert seen_texts.index("LIVE STATE") < seen_texts.index("UPCOMING")
    assert "TOP 7th" in seen_texts
    assert "B-S 2-1 OUT 1" in seen_texts
    assert "1B 3B" in seen_texts
    assert "M. Chapman / Y. Yamamoto" in seen_texts
    assert "\u5de8\u4eba 3/7/1  \u9053\u5947 5/8/0" in seen_texts
    assert "Dodger Stadium" in seen_texts
    assert "RECENT" not in seen_texts
    assert icon_calls[-6:] == ["INNING", "COUNT", "BASES", "B/P", "RHE", "VENUE"]


def test_mlb_live_state_rows_keep_live_context_before_venue():
    event = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), ZoneInfo("America/Los_Angeles"))["events"][0]

    assert SportsDashboard._mlb_live_state_rows(event) == [
        ("INNING", "TOP 7th"),
        ("COUNT", "B-S 2-1 OUT 1"),
        ("BASES", "1B 3B"),
        ("B/P", "M. Chapman / Y. Yamamoto"),
        ("RHE", "\u5de8\u4eba 3/7/1  \u9053\u5947 5/8/0"),
        ("VENUE", "Dodger Stadium"),
    ]


def test_mlb_live_state_falls_back_to_rhe_without_current_matchup():
    event = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), ZoneInfo("America/Los_Angeles"))["events"][0]
    event = dict(event)
    event["current_batter"] = ""
    event["current_pitcher"] = ""

    rows = SportsDashboard._mlb_live_state_rows(event)

    assert rows[-2] == ("RHE", "\u5de8\u4eba 3/7/1  \u9053\u5947 5/8/0")
    assert rows[-1] == ("VENUE", "Dodger Stadium")


def test_mlb_live_state_omits_empty_bases_to_preserve_denser_context():
    event = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), ZoneInfo("America/Los_Angeles"))["events"][0]
    event = dict(event)
    event["bases"] = ""

    rows = SportsDashboard._mlb_live_state_rows(event)

    assert ("BASES", "EMPTY") not in rows
    assert rows == [
        ("INNING", "TOP 7th"),
        ("COUNT", "B-S 2-1 OUT 1"),
        ("B/P", "M. Chapman / Y. Yamamoto"),
        ("RHE", "\u5de8\u4eba 3/7/1  \u9053\u5947 5/8/0"),
        ("VENUE", "Dodger Stadium"),
    ]


def test_mlb_rhe_rows_are_omitted_when_line_score_is_missing():
    event = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), ZoneInfo("America/Los_Angeles"))["events"][0]
    event = dict(event)
    event["away_line"] = {}
    event["home_line"] = {}

    live_rows = SportsDashboard._mlb_live_state_rows(event)
    final_rows = SportsDashboard._mlb_final_snap_rows(event)

    assert SportsDashboard._mlb_compact_rhe_label(event) == ""
    assert not any(label == "RHE" for label, _value in live_rows)
    assert ("VENUE", "Dodger Stadium") in live_rows
    assert not any(label == "RHE" for label, _value in final_rows)


def test_mlb_rhe_line_skips_placeholder_when_line_score_is_missing():
    plugin = _plugin()
    event = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), ZoneInfo("America/Los_Angeles"))["events"][0]
    event = dict(event)
    event["away_line"] = {}
    event["home_line"] = {}
    image = Image.new("RGB", (250, 50), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    centered_texts = []

    plugin._draw_centered = lambda _draw, _xy, text, _font, _fill: centered_texts.append(str(text))

    plugin._draw_mlb_rhe_line(draw, 10, 10, 230, event)

    assert centered_texts == []


def test_mlb_rhe_line_uses_two_readable_rows(monkeypatch):
    plugin = _plugin()
    event = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), ZoneInfo("America/Los_Angeles"))["events"][0]
    image = Image.new("RGB", (250, 80), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    text_boxes = []

    def capture_text_in_box(_draw, box, text, _font, _fill, align="left"):
        text_boxes.append((tuple(int(value) for value in box), str(text), align))

    monkeypatch.setattr(plugin, "_draw_text_in_box", capture_text_in_box)

    plugin._draw_mlb_rhe_line(draw, 10, 10, 230, event)

    value_rows = [(box, text, align) for box, text, align in text_boxes if text.startswith("R/H/E")]
    assert [(text, align) for _box, text, align in value_rows] == [
        ("R/H/E 3/7/1", "right"),
        ("R/H/E 5/8/0", "right"),
    ]
    assert value_rows[0][0][1:] == (12, 222, 25)
    assert value_rows[1][0][1:] == (27, 222, 40)


def test_mlb_live_matchup_strip_uses_single_cell_when_one_side_is_missing():
    plugin = _plugin()
    event = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), ZoneInfo("America/Los_Angeles"))["events"][0]
    event = dict(event)
    event["current_pitcher"] = ""
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    cells = []

    def capture_cell(_draw, box, label, value, accent):
        cells.append((tuple(int(item) for item in box), str(label), str(value), accent))

    plugin._draw_mlb_live_matchup_cell = capture_cell

    plugin._draw_mlb_live_matchup_strip(draw, 10, 12, 230, event)

    assert cells == [((10, 12, 230, 28), "BAT", "M. Chapman", COLORS["amber"])]
    assert all("TBD" not in cell[2] for cell in cells)

    cells.clear()
    event["current_batter"] = ""
    event["current_pitcher"] = "Y. Yamamoto"

    plugin._draw_mlb_live_matchup_strip(draw, 10, 12, 230, event)

    assert cells == [((10, 12, 230, 28), "P", "Y. Yamamoto", COLORS["mlb_accent"])]
    assert all("TBD" not in cell[2] for cell in cells)


def test_mlb_probable_pitching_label_omits_tbd_for_partial_starter_data():
    event = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), ZoneInfo("America/Los_Angeles"))["events"][1]
    event = dict(event)
    event["probable_b"] = ""

    label = SportsDashboard._mlb_probable_pitching_label(event)

    assert label.endswith("J. deGrom")
    assert "TBD" not in label


def test_mlb_pitching_label_omits_tbd_for_partial_starter_data():
    event = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), ZoneInfo("America/Los_Angeles"))["events"][1]
    event = dict(event)
    event["probable_b"] = ""

    label = SportsDashboard._mlb_pitching_label(event)

    assert label == "SP \u6e38\u9a91\u5175 J. deGrom"
    assert "TBD" not in label
    assert "pending" not in label.lower()


def test_mlb_pitching_label_uses_neutral_fallback_when_event_is_empty():
    assert SportsDashboard._mlb_pitching_label({}) == "MLB GAME INFO"
    assert SportsDashboard._mlb_pitching_label(None) == "MLB GAME INFO"


def test_mlb_batting_side_fill_key_marks_current_offense():
    event = {"state": "live", "inning_state": "Top"}

    assert SportsDashboard._mlb_batting_side_fill_key(event, "a") == "amber"
    assert SportsDashboard._mlb_batting_side_fill_key(event, "b") == "text"

    event["inning_state"] = "Bottom"
    assert SportsDashboard._mlb_batting_side_fill_key(event, "a") == "text"
    assert SportsDashboard._mlb_batting_side_fill_key(event, "b") == "amber"

    event["state"] = "final"
    assert SportsDashboard._mlb_batting_side_fill_key(event, "b") == "text"


def test_mlb_team_side_fill_key_marks_live_offense_or_final_winner():
    event = {"state": "live", "inning_state": "Bottom", "wins_a": 5, "wins_b": 3}

    assert SportsDashboard._mlb_team_side_fill_key(event, "a") == "text"
    assert SportsDashboard._mlb_team_side_fill_key(event, "b") == "amber"

    event.update({"state": "final", "inning_state": "", "wins_a": 5, "wins_b": 3})
    assert SportsDashboard._mlb_team_side_fill_key(event, "a") == "amber"
    assert SportsDashboard._mlb_team_side_fill_key(event, "b") == "text"

    event.update({"wins_a": 3, "wins_b": 3})
    assert SportsDashboard._mlb_team_side_fill_key(event, "a") == "text"
    assert SportsDashboard._mlb_team_side_fill_key(event, "b") == "text"


def test_mlb_main_card_highlights_current_batting_side():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)
    card = SportsDashboard._offseason_hub_card("MLB", parsed, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    team_score_calls = []

    def capture_team_score(*args, **kwargs):
        team_score_calls.append(
            {
                "team": args[4],
                "score": args[5],
                "team_fill": kwargs.get("team_fill"),
                "score_fill": kwargs.get("score_fill"),
            }
        )

    plugin._draw_hub_team_score = capture_team_score
    plugin._draw_mlb_base_diamond = lambda *_args, **_kwargs: None
    plugin._draw_mlb_rhe_line = lambda *_args, **_kwargs: None
    plugin._draw_mlb_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert team_score_calls[0]["team"] == "\u65e7\u91d1\u5c71\u5de8\u4eba"
    assert team_score_calls[0]["score"] == 3
    assert team_score_calls[0]["team_fill"] == COLORS["amber"]
    assert team_score_calls[0]["score_fill"] == COLORS["amber"]
    assert team_score_calls[1]["team"] == "\u6d1b\u6749\u77f6\u9053\u5947"
    assert team_score_calls[1]["team_fill"] == COLORS["text"]


def test_mlb_main_card_uses_field_tint_for_live_state_box():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)
    card = SportsDashboard._offseason_hub_card("MLB", parsed, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_base_diamond = lambda *_args, **_kwargs: None
    plugin._draw_mlb_rhe_line = lambda *_args, **_kwargs: None
    plugin._draw_mlb_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert COLORS["mlb_field_tint"] != COLORS["panel_blue"]
    assert image.getpixel((22, 129)) == COLORS["mlb_field_tint"]


def test_mlb_main_card_draws_bso_count_board_for_live_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)
    card = SportsDashboard._offseason_hub_card("MLB", parsed, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    bso_cells = []

    def capture_bso_cell(_draw, box, label, value, accent, outs=None):
        bso_cells.append((tuple(int(item) for item in box), str(label), str(value), accent, outs))

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_base_diamond = lambda *_args, **_kwargs: None
    plugin._draw_mlb_rhe_line = lambda *_args, **_kwargs: None
    plugin._draw_mlb_bso_cell = capture_bso_cell
    plugin._draw_mlb_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert [(label, value) for _box, label, value, _accent, _outs in bso_cells] == [
        ("B", "2"),
        ("S", "1"),
        ("O", "1"),
    ]
    assert [accent for _box, _label, _value, accent, _outs in bso_cells] == [
        COLORS["mlb_accent"],
        COLORS["amber"],
        COLORS["red"],
    ]
    assert bso_cells[-1][-1] == 1


def test_mlb_main_card_highlights_final_winner():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 15, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)
    final_event = dict(parsed["events"][0])
    final_event.update({"state": "final", "status_text": "Final", "inning_state": "", "bases": "", "current_batter": "", "current_pitcher": ""})
    card = SportsDashboard._offseason_hub_card("MLB", {"events": [final_event]}, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    team_score_calls = []

    def capture_team_score(*args, **kwargs):
        team_score_calls.append(
            {
                "team": args[4],
                "score": args[5],
                "team_fill": kwargs.get("team_fill"),
                "score_fill": kwargs.get("score_fill"),
            }
        )

    plugin._draw_hub_team_score = capture_team_score
    plugin._draw_mlb_rhe_line = lambda *_args, **_kwargs: None
    plugin._draw_mlb_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert team_score_calls[0]["team"] == "\u65e7\u91d1\u5c71\u5de8\u4eba"
    assert team_score_calls[0]["team_fill"] == COLORS["text"]
    assert team_score_calls[1]["team"] == "\u6d1b\u6749\u77f6\u9053\u5947"
    assert team_score_calls[1]["score"] == 5
    assert team_score_calls[1]["team_fill"] == COLORS["amber"]
    assert team_score_calls[1]["score_fill"] == COLORS["amber"]


def test_mlb_main_card_promotes_current_batter_pitcher_matchup():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)
    card = SportsDashboard._offseason_hub_card("MLB", parsed, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_base_diamond = lambda *_args, **_kwargs: None
    plugin._draw_mlb_rhe_line = lambda *_args, **_kwargs: None
    plugin._draw_mlb_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert "BAT" in seen_texts
    assert "M. Chapman" in seen_texts
    assert "P" in seen_texts
    assert "Y. Yamamoto" in seen_texts
    assert icon_calls[-2:] == ["BAT", "P"]
    assert "B/P M. Chapman / Y. Yamamoto" not in seen_texts
    assert "SP L. Webb / Y. Yamamoto" not in seen_texts


def test_mlb_main_card_falls_back_to_probable_pitchers_without_current_matchup():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)
    event = dict(parsed["events"][0])
    event["current_batter"] = ""
    event["current_pitcher"] = ""
    card = SportsDashboard._offseason_hub_card("MLB", {"events": [event]}, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_base_diamond = lambda *_args, **_kwargs: None
    plugin._draw_mlb_rhe_line = lambda *_args, **_kwargs: None
    plugin._draw_mlb_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert "SP L. Webb / Y. Yamamoto" in seen_texts
    assert "BAT" not in icon_calls
    assert "P" not in icon_calls


def test_mlb_main_card_uses_pregame_context_for_scheduled_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)
    next_event = next(event for event in parsed["events"] if event["event_id"] == "777002")
    card = SportsDashboard._offseason_hub_card("MLB", {"events": [next_event]}, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    live_only_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_base_diamond = lambda *_args, **_kwargs: live_only_calls.append("bases")
    plugin._draw_mlb_rhe_line = lambda *_args, **_kwargs: live_only_calls.append("rhe")
    plugin._draw_mlb_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert "FIRST PITCH" in seen_texts
    assert "06/14 7:10 PM" in seen_texts
    assert "SP" in seen_texts
    assert "J. deGrom / L. Castillo" in seen_texts
    assert "VENUE" in seen_texts
    assert "T-Mobile Park" in seen_texts
    assert "RECORD" in seen_texts
    assert any("35-33" in text and "39-29" in text for text in seen_texts)
    assert "R" not in seen_texts
    assert "H" not in seen_texts
    assert "E" not in seen_texts
    assert icon_calls[-4:] == ["FIRST", "SP", "VENUE", "RECORD"]
    assert live_only_calls == []


def test_mlb_main_card_omits_pregame_placeholders_when_details_are_missing():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)
    next_event = dict(next(event for event in parsed["events"] if event["event_id"] == "777002"))
    next_event.update(
        {
            "probable_a": "",
            "probable_b": "",
            "venue": "",
            "record_a": "",
            "record_b": "",
        }
    )
    card = SportsDashboard._offseason_hub_card("MLB", {"events": [next_event]}, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert "FIRST PITCH" in seen_texts
    assert "TBD / TBD" not in seen_texts
    assert "Ballpark pending" not in seen_texts
    assert "SP" not in icon_calls
    assert "VENUE" not in icon_calls
    assert "RECORD" not in icon_calls


def test_mlb_main_card_uses_final_context_for_completed_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 15, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)
    final_event = dict(parsed["events"][0])
    final_event.update({"state": "final", "status_text": "Final", "bases": "", "current_batter": "", "current_pitcher": ""})
    card = SportsDashboard._offseason_hub_card("MLB", {"events": [final_event]}, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    live_only_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_base_diamond = lambda *_args, **_kwargs: live_only_calls.append("bases")
    plugin._draw_mlb_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert "MLB RECENT" in seen_texts
    assert "FINAL RESULT" in seen_texts
    assert "06/14" in seen_texts
    assert "R/H/E 3/7/1" in seen_texts
    assert "R/H/E 5/8/0" in seen_texts
    assert "\u5de8\u4eba" in seen_texts
    assert "\u9053\u5947" in seen_texts
    assert "\u9053\u5947 \u80dc2\u5206 / Dodger Stadium" in seen_texts
    assert "SP L. Webb / Y. Yamamoto" not in seen_texts
    assert "1B 2B 3B" not in seen_texts
    assert icon_calls[-1:] == ["SCORE"]
    assert live_only_calls == []


def test_mlb_side_column_replaces_empty_recent_with_game_info_for_next_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)
    next_event = next(event for event in parsed["events"] if event["event_id"] == "777002")
    card = SportsDashboard._offseason_hub_card("MLB", {"events": [next_event]}, now)
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert "UPCOMING" in seen_texts
    assert "GAME INFO" in seen_texts
    assert "06/14 7:10 PM" in seen_texts
    assert "\u6e38\u9a91\u5175 @ \u6c34\u624b" in seen_texts
    assert "\u6e38\u9a91\u5175 35-33 / \u6c34\u624b 39-29" in seen_texts
    assert "J. deGrom / L. Castillo" in seen_texts
    assert "T-Mobile Park" in seen_texts
    assert "RECENT" not in seen_texts
    assert icon_calls[-5:] == ["FIRST", "MATCH", "SP", "VENUE", "RECORD"]


def test_mlb_side_column_uses_final_snap_when_no_recent_or_upcoming():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 15, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)
    final_event = dict(parsed["events"][0])
    final_event.update(
        {
            "state": "final",
            "status_text": "Final",
            "bases": "",
            "current_batter": "",
            "current_pitcher": "",
        }
    )
    card = {"sport": "MLB", "status": "RECENT", "main": final_event, "upcoming": [], "recent": []}
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    right_aligned = []
    original_fit_text = plugin._fit_text
    original_right_aligned = plugin._draw_right_aligned

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def capture_right_aligned(draw_obj, pos, text, font, fill):
        right_aligned.append((str(text), fill))
        return original_right_aligned(draw_obj, pos, text, font, fill)

    plugin._fit_text = capture_fit_text
    plugin._draw_right_aligned = capture_right_aligned
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert "FINAL SNAP" in seen_texts
    assert "\u9053\u5947 \u80dc2\u5206" in seen_texts
    assert "\u5de8\u4eba 3 / \u9053\u5947 5" in seen_texts
    assert "\u5de8\u4eba 3/7/1  \u9053\u5947 5/8/0" in seen_texts
    assert "\u5de8\u4eba 41-28 / \u9053\u5947 45-24" in seen_texts
    assert "Dodger Stadium" in seen_texts
    assert "UPCOMING" not in seen_texts
    assert "RECENT" not in seen_texts
    assert "No MLB schedule" not in seen_texts
    assert "No recent results" not in seen_texts
    assert icon_calls[:5] == ["WIN", "SCORE", "RHE", "RECORD", "VENUE"]
    assert ("\u9053\u5947 \u80dc2\u5206", COLORS["mlb_accent"]) in right_aligned


def test_pga_leaderboard_row_draws_round_strokes_and_today():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    row = SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), la, now)["events"][0]["leaderboard"][0]
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    right_aligned = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, accent: icon_calls.append((str(kind), accent))
    plugin._draw_right_aligned = lambda _draw, _pos, text, _font, fill: right_aligned.append((str(text), fill))
    plugin._draw_pga_leaderboard_row(draw, 0, 240, 8, row, 0)

    assert "P1" in seen_texts
    assert "S. Scheffler" in seen_texts
    assert "-9" in seen_texts
    assert "R3 68 / -2" in seen_texts
    assert icon_calls == [("GOLF", COLORS["pga_leader"])]
    assert ("-9", COLORS["pga_leader"]) in right_aligned


def test_pga_leaderboard_row_draws_gap_to_leader_for_chasing_player():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    rows = SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), la, now)["events"][0]["leaderboard"]
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda *_args, **_kwargs: None
    plugin._draw_pga_leaderboard_row(draw, 0, 240, 8, rows[1], 1, leader_score=rows[0]["score"])

    assert "P2" in seen_texts
    assert "R. McIlroy" in seen_texts
    assert "-7" in seen_texts
    assert "R3 70 / E / GAP +2" in seen_texts


def test_pga_leaderboard_row_draws_gap_chip_for_chasing_player():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    rows = SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), la, now)["events"][0]["leaderboard"]
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    chip_calls = []

    def capture_gap_chip(_draw, x, y, label, accent):
        chip_calls.append((int(x), int(y), str(label), accent))

    plugin._draw_sport_info_icon = lambda *_args, **_kwargs: None
    plugin._draw_pga_gap_chip = capture_gap_chip
    plugin._draw_pga_leaderboard_row(draw, 0, 240, 8, rows[0], 0, leader_score=rows[0]["score"])
    plugin._draw_pga_leaderboard_row(draw, 0, 240, 8, rows[1], 1, leader_score=rows[0]["score"])

    assert SportsDashboard._pga_gap_chip_label(rows[0], rows[0]["score"]) == ""
    assert SportsDashboard._pga_gap_chip_label(rows[1], rows[0]["score"]) == "+2"
    assert chip_calls == [(209, 19, "+2", COLORS["pga_accent"])]


def test_pga_leaderboard_row_draws_round_chip_for_current_round():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    rows = SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), la, now)["events"][0]["leaderboard"]
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    chip_calls = []

    def capture_round_chip(_draw, x, y, label, row, accent):
        chip_calls.append((int(x), int(y), str(label), row.get("round"), accent))

    plugin._draw_sport_info_icon = lambda *_args, **_kwargs: None
    plugin._draw_pga_round_chip = capture_round_chip
    plugin._draw_pga_gap_chip = lambda *_args, **_kwargs: None
    plugin._draw_pga_leaderboard_row(draw, 0, 240, 8, rows[0], 0, leader_score=rows[0]["score"])
    plugin._draw_pga_leaderboard_row(draw, 0, 240, 8, rows[1], 1, leader_score=rows[0]["score"])

    row_without_round = dict(rows[0])
    row_without_round.pop("round", None)
    assert SportsDashboard._pga_round_chip_label(rows[0]) == "R3"
    assert SportsDashboard._pga_round_chip_label(row_without_round) == ""
    assert chip_calls == [
        (209, 19, "R3", 3, COLORS["pga_leader"]),
        (174, 19, "R3", 3, COLORS["pga_accent"]),
    ]


def test_pga_country_display_label_uses_simplified_chinese_names():
    assert SportsDashboard._pga_country_display_label("USA") == "\u7f8e\u56fd"
    assert SportsDashboard._pga_country_display_label("Northern Ireland") == "\u5317\u7231\u5c14\u5170"
    assert SportsDashboard._pga_country_display_label("South Africa") == "\u5357\u975e"
    assert SportsDashboard._pga_country_display_label("XYZ") == "XYZ"


def test_pga_leaderboard_row_draws_chinese_country_label_when_available():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    payload = json.loads(json.dumps(_sample_pga_scoreboard_payload()))
    payload["events"][0]["competitions"][0]["competitors"][0]["athlete"]["country"] = {
        "abbreviation": "USA",
        "displayName": "United States",
    }
    row = SportsDashboard._parse_pga_scoreboard(payload, la, now)["events"][0]["leaderboard"][0]
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda *_args, **_kwargs: None
    plugin._draw_pga_leaderboard_row(draw, 0, 240, 8, row, 0)

    assert row["country"] == "USA"
    assert "\u7f8e\u56fd / R3 68 / -2" in seen_texts
    assert "USA / R3 68 / -2" not in seen_texts


def test_pga_leaderboard_row_draws_country_badge_next_to_player_name():
    plugin = _plugin()
    row = {
        "position": 2,
        "position_label": "T2",
        "name": "R. McIlroy",
        "country": "NIR",
        "score": "-7",
        "round": 3,
        "strokes": "70",
        "today": "E",
    }
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    badge_calls = []

    def capture_badge(_draw, x, y, code, accent):
        badge_calls.append((int(x), int(y), str(code), accent))

    plugin._draw_pga_country_badge = capture_badge
    plugin._draw_pga_leaderboard_row(draw, 0, 240, 8, row, 1, leader_score="-9")

    assert badge_calls == [(43, 9, "NIR", COLORS["pga_accent"])]


def test_pga_leaderboard_row_preserves_tied_position_label():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    payload = json.loads(json.dumps(_sample_pga_scoreboard_payload()))
    competitor = payload["events"][0]["competitions"][0]["competitors"][1]
    competitor["displayRank"] = "T2"
    competitor["order"] = 4

    rows = SportsDashboard._parse_pga_scoreboard(payload, la, now)["events"][0]["leaderboard"]
    tied_row = next(row for row in rows if row["name"] == "R. McIlroy")
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda *_args, **_kwargs: None
    plugin._draw_pga_leaderboard_row(draw, 0, 240, 8, tied_row, 1, leader_score=rows[0]["score"])

    assert tied_row["position"] == 2
    assert tied_row["position_label"] == "T2"
    assert "T2" in seen_texts
    assert "P2" not in seen_texts


def test_pga_leaderboard_rank_color_keys_mark_top_three():
    assert SportsDashboard._pga_leaderboard_rank_color_key({"position": 1}, 0) == "pga_leader"
    assert SportsDashboard._pga_leaderboard_rank_color_key({"position": 2}, 1) == "pga_accent"
    assert SportsDashboard._pga_leaderboard_rank_color_key({"position": 3}, 2) == "orange"
    assert SportsDashboard._pga_leaderboard_rank_color_key({"position": 4}, 3) == "pga_accent"


def test_pga_leaderboard_column_prioritizes_board_before_event_snap():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    payload = json.loads(json.dumps(_sample_pga_scoreboard_payload()))
    payload["events"][0]["competitions"][0]["competitors"].extend(
        [
            {
                "order": 3,
                "score": "-6",
                "athlete": {"shortName": "X. Schauffele"},
                "linescores": [{"period": 3, "displayValue": "-1", "value": 69}],
            },
            {
                "order": 4,
                "score": "-5",
                "athlete": {"shortName": "C. Morikawa"},
                "linescores": [{"period": 3, "displayValue": "+1", "value": 72}],
            },
        ]
    )
    parsed = SportsDashboard._parse_pga_scoreboard(payload, la, now)
    card = SportsDashboard._offseason_hub_card("PGA", parsed, now)
    image = Image.new("RGB", (250, 190), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_pga_leaderboard_column(draw, (0, 0, 240, 180), card, now)

    assert "LEADERBOARD" in seen_texts
    assert "EVENT INFO" not in seen_texts
    assert "S. Scheffler" in seen_texts
    assert "R. McIlroy" in seen_texts
    assert "X. Schauffele" in seen_texts
    assert "C. Morikawa" in seen_texts
    assert "R3 70 / E / GAP +2" in seen_texts
    assert "SNAP" in seen_texts
    assert seen_texts.index("LEADERBOARD") < seen_texts.index("SNAP")
    assert "LEADER S. Scheffler -9 / THRU 06/15 / Shinnecock Hills" in seen_texts
    assert icon_calls[-1] == "PGA"


def test_pga_leaderboard_column_uses_event_info_when_leaderboard_missing():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    payload = json.loads(json.dumps(_sample_pga_scoreboard_payload()))
    payload["events"][0]["competitions"][0]["competitors"] = []
    parsed = SportsDashboard._parse_pga_scoreboard(payload, la, now)
    card = SportsDashboard._offseason_hub_card("PGA", parsed, now)
    image = Image.new("RGB", (250, 190), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_pga_leaderboard_column(draw, (0, 0, 240, 180), card, now)

    assert "EVENT INFO" in seen_texts
    assert "LEADERBOARD" not in seen_texts
    assert "\u7f8e\u56fd\u516c\u5f00\u8d5b" in seen_texts
    assert "THRU 06/15" in seen_texts
    assert "Shinnecock Hills" in seen_texts
    assert "06/12-15" in seen_texts
    assert "Leaderboard pending" not in seen_texts
    assert "PGA TOUR" not in seen_texts
    assert icon_calls == ["GOLF", "CLOCK", "VENUE", "PERIOD"]


def test_pga_event_info_rows_omit_generic_event_name_when_missing():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    event = {
        "sport": "PGA",
        "state": "pre",
        "start": now + timedelta(days=1),
        "name": "",
        "status_text": "Scheduled",
        "venue": "Shinnecock Hills",
        "end": now + timedelta(days=4),
    }

    rows = SportsDashboard._pga_event_info_rows(event, now)
    compact_rows = SportsDashboard._pga_compact_event_info_rows(event, now)

    assert ("GOLF", "EVENT", "PGA TOUR") not in rows
    assert ("GOLF", "EVENT", "PGA TOUR") not in compact_rows
    assert rows[0] == ("CLOCK", "STATUS", "Scheduled")
    assert ("VENUE", "COURSE", "Shinnecock Hills") in rows
    assert not any(row[1] == "BOARD" for row in rows)


def test_pga_event_info_rows_use_neutral_fallback_only_when_event_is_empty():
    rows = SportsDashboard._pga_event_info_rows({}, datetime(2026, 6, 14, 13, 30, tzinfo=ZoneInfo("America/Los_Angeles")))

    assert rows == [("PGA", "BOARD", "PGA TOUR")]
    assert "Leaderboard pending" not in [value for _icon, _label, value in rows]


def test_pga_event_snap_label_prioritizes_leader_when_available():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    event = SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), la, now)["events"][0]
    fallback = dict(event)
    fallback["leader"] = {}

    assert SportsDashboard._pga_event_snap_label(event, now) == "LEADER S. Scheffler -9 / THRU 06/15 / Shinnecock Hills"
    assert SportsDashboard._pga_event_snap_label(fallback, now) == "THRU 06/15 / Shinnecock Hills / 06/12-15"


def test_pga_next_label_includes_tournament_name_when_available():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)

    assert (
        SportsDashboard._pga_next_label(
            {
                "upcoming": [
                    {
                        "start": datetime(2026, 6, 19, 7, 0, tzinfo=la),
                        "name": "\u65c5\u884c\u8005\u9526\u6807\u8d5b",
                    }
                ]
            },
            now,
        )
        == "NEXT TEE 06/19 / \u65c5\u884c\u8005\u9526\u6807\u8d5b"
    )
    assert (
        SportsDashboard._pga_next_label(
            {"recent": [{"name": "\u7f8e\u56fd\u516c\u5f00\u8d5b"}]},
            now,
        )
        == "RECENT / \u7f8e\u56fd\u516c\u5f00\u8d5b"
    )
    assert SportsDashboard._pga_next_label({}, now) == "TOUR CALENDAR"


def test_pga_main_card_uses_tee_window_for_scheduled_event_without_leaderboard():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 10, 9, 0, tzinfo=la)
    parsed = SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), la, now)
    event = dict(parsed["events"][0])
    event["leaderboard"] = []
    event["leader"] = {}
    card = SportsDashboard._offseason_hub_card("PGA", {"events": [event]}, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_pga_event_card(image, draw, (0, 0, 300, 190), card, now)

    assert "PGA NEXT" in seen_texts
    assert "\u7f8e\u56fd\u516c\u5f00\u8d5b" in seen_texts
    assert "TEE WINDOW" in seen_texts
    assert "06/12 7:00 AM" in seen_texts
    assert "06/12-15 / Shinnecock Hills" in seen_texts
    assert "Leaderboard pending" not in seen_texts
    assert icon_calls == ["TEE", "VENUE"]


def test_pga_main_card_uses_event_status_when_live_leaderboard_is_missing():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), la, now)
    event = dict(parsed["events"][0])
    event["leaderboard"] = []
    event["leader"] = {}
    card = SportsDashboard._offseason_hub_card("PGA", {"events": [event]}, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_pga_event_card(image, draw, (0, 0, 300, 190), card, now)

    assert "PGA LIVE" in seen_texts
    assert "EVENT STATUS" in seen_texts
    assert "THRU 06/15" in seen_texts
    assert "06/12-15 / Shinnecock Hills" in seen_texts
    assert "Leaderboard pending" not in seen_texts
    assert icon_calls == ["CLOCK", "VENUE"]


def test_pga_main_card_omits_course_placeholder_when_venue_is_missing():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 10, 9, 0, tzinfo=la)
    parsed = SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), la, now)
    event = dict(parsed["events"][0])
    event["leaderboard"] = []
    event["leader"] = {}
    event["venue"] = ""
    card = SportsDashboard._offseason_hub_card("PGA", {"events": [event]}, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_pga_event_card(image, draw, (0, 0, 300, 190), card, now)

    assert "Course pending" not in seen_texts
    assert "06/12-15" in seen_texts


def test_pga_schedule_summary_omits_detail_placeholder_when_window_and_course_are_missing():
    plugin = _plugin()
    image = Image.new("RGB", (240, 70), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    now = datetime(2026, 6, 10, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    event = {"sport": "PGA", "state": "pre", "status_text": "Scheduled", "venue": ""}
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_pga_schedule_summary(draw, 0, 0, 220, 48, event, now)

    assert "Scheduled" in seen_texts
    assert "Schedule pending" not in seen_texts
    assert icon_calls == ["TEE"]


def test_pga_schedule_summary_uses_course_tint_not_generic_blue_panel():
    plugin = _plugin()
    image = Image.new("RGB", (240, 70), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 10, 9, 0, tzinfo=la)
    event = SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), la, now)["events"][0]

    plugin._draw_pga_schedule_summary(draw, 0, 0, 220, 48, event, now)

    assert COLORS["pga_course_tint"] != COLORS["panel_blue"]
    assert image.getpixel((5, 25)) == COLORS["pga_course_tint"]


def test_pga_leader_summary_omits_bottom_scorecard_strip():
    plugin = _plugin()
    image = Image.new("RGB", (240, 70), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    leader = {
        "name": "S. Scheffler",
        "score": "-9",
        "round": 3,
        "today": "-2",
        "strokes": "68",
    }
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))

    plugin._draw_pga_leader_summary(draw, 0, 0, 220, 48, leader)

    assert "LEADER" in seen_texts
    assert "S. Scheffler" in seen_texts
    assert "-9" in seen_texts
    assert "RND" not in seen_texts
    assert "DAY" not in seen_texts
    assert "CARD" not in seen_texts
    assert "68" not in seen_texts
    assert icon_calls == ["GOLF"]


def test_pga_leader_summary_does_not_draw_scorecard_placeholder_when_round_details_missing():
    plugin = _plugin()
    image = Image.new("RGB", (240, 70), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    leader = {
        "name": "S. Scheffler",
        "score": "-9",
    }
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))

    plugin._draw_pga_leader_summary(draw, 0, 0, 220, 48, leader)

    assert "SCORECARD" not in seen_texts
    assert "ROUND DATA" not in seen_texts
    assert icon_calls == ["GOLF"]


def test_pga_leader_summary_uses_neutral_fallback_when_leader_missing():
    plugin = _plugin()
    image = Image.new("RGB", (240, 70), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_pga_leader_summary(draw, 0, 0, 220, 48, {})

    assert "EVENT INFO" in seen_texts
    assert "Leaderboard pending" not in seen_texts


def test_pga_leader_summary_keeps_country_badge_without_detail_strip():
    plugin = _plugin()
    image = Image.new("RGB", (240, 70), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    leader = {
        "name": "R. McIlroy",
        "country": "NIR",
        "score": "-7",
        "round": 3,
        "today": "E",
        "strokes": "70",
    }
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda *_args, **_kwargs: None
    plugin._draw_pga_leader_summary(draw, 0, 0, 220, 48, leader)

    assert "R. McIlroy" in seen_texts
    assert "NIR" in seen_texts
    assert "-7" in seen_texts
    assert "NAT" not in seen_texts
    assert "\u5317\u7231\u5c14\u5170" not in seen_texts
    assert "RND" not in seen_texts
    assert "DAY" not in seen_texts
    assert "CARD" not in seen_texts
    assert "NIR / R3 / E / 70" not in seen_texts


def test_pga_leader_summary_draws_country_badge_next_to_leader_name():
    plugin = _plugin()
    image = Image.new("RGB", (240, 70), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    leader = {
        "name": "R. McIlroy",
        "country": "NIR",
        "score": "-7",
        "round": 3,
        "today": "E",
        "strokes": "70",
    }
    badge_calls = []

    def capture_badge(_draw, x, y, code, accent):
        badge_calls.append((int(x), int(y), str(code), accent))

    plugin._draw_sport_info_icon = lambda *_args, **_kwargs: None
    plugin._draw_pga_country_badge = capture_badge
    plugin._draw_pga_leader_summary(draw, 0, 0, 220, 48, leader)

    assert badge_calls == [(8, 18, "NIR", COLORS["pga_accent"])]


def test_pga_leader_summary_uses_course_tint_not_generic_blue_panel():
    plugin = _plugin()
    image = Image.new("RGB", (240, 70), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    leader = {
        "name": "S. Scheffler",
        "score": "-9",
        "round": 3,
        "today": "-2",
        "strokes": "68",
    }

    plugin._draw_pga_leader_summary(draw, 0, 0, 220, 48, leader)

    assert COLORS["pga_course_tint"] != COLORS["panel_blue"]
    assert image.getpixel((5, 25)) == COLORS["pga_course_tint"]


def test_pga_fairway_strip_asset_is_exact_transparent_size():
    assert Path(LOCAL_PGA_FAIRWAY_STRIP_PATH).exists()
    with Image.open(LOCAL_PGA_FAIRWAY_STRIP_PATH) as source:
        strip = source.convert("RGBA")

    assert strip.size == (215, 36)
    assert strip.getchannel("A").getextrema()[0] == 0
    assert strip.getbbox() is not None
    assert [strip.getpixel(point)[3] for point in ((0, 0), (214, 0), (0, 35), (214, 35))] == [0, 0, 0, 0]


def test_pga_fairway_uses_uploaded_strip(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (260, 80), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    requested_sizes = []
    strip_color = (12, 200, 40, 255)

    def load_strip(size):
        requested_sizes.append(size)
        strip = Image.new("RGBA", size, (0, 0, 0, 0))
        strip_draw = ImageDraw.Draw(strip)
        strip_draw.rectangle((10, 8, size[0] - 12, size[1] - 8), fill=strip_color)
        return strip

    monkeypatch.setattr(plugin, "_load_pga_fairway_strip", load_strip)

    plugin._draw_pga_fairway(image, draw, 10, 20, 224, 56)

    assert requested_sizes == [(288, 48)]
    assert image.getpixel((10, 20)) == COLORS["panel"]
    assert image.getpixel((40, 38)) == strip_color[:3]


def test_pga_fairway_falls_back_to_code_drawing(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (260, 80), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    monkeypatch.setattr(plugin, "_load_pga_fairway_strip", lambda _size: None)

    plugin._draw_pga_fairway(image, draw, 10, 20, 224, 56)

    assert image.getpixel((10, 20)) == COLORS["panel"]
    assert image.getpixel((202, 29)) == COLORS["red"]


def test_wnba_live_side_column_uses_score_and_quarter_log_when_schedule_empty():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "WNBA",
        SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la),
        now,
    )
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    logo_calls = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def capture_team_logo(_image, _draw, logo_url, _x, _y, size, fallback_text):
        logo_calls.append((str(logo_url or ""), int(size), str(fallback_text or "")))

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = capture_team_logo
    plugin._draw_wnba_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert "LIVE GAME" in seen_texts
    assert "QUARTER LOG" in seen_texts
    assert "Q3 4:22" in seen_texts
    assert "ION" in seen_texts
    assert "Michelob ULTRA Arena" in seen_texts
    assert "\u98ce\u66b4" in seen_texts
    assert "\u738b\u724c" in seen_texts
    assert "72" in seen_texts
    assert "78" in seen_texts
    assert "\u738b\u724c +6" in seen_texts
    assert "Q1" in seen_texts
    assert "25-28" in seen_texts
    assert "Q3" in seen_texts
    assert "24-31" in seen_texts
    assert ("https://example.com/wnba-sea.png", 11, "SEA") in logo_calls
    assert ("https://example.com/wnba-lv.png", 11, "LV") in logo_calls
    assert icon_calls[:4] == ["CLOCK", "LEAD", "TV", "VENUE"]
    assert icon_calls.count("PERIOD") == 4


def test_wnba_live_team_row_localizes_english_payload_names():
    plugin = _plugin()
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    event = {
        "team_a": "Seattle Storm",
        "team_a_name": "Seattle Storm",
        "team_a_code": "SEA",
        "team_a_logo": "https://example.com/wnba-sea.png",
        "wins_a": 72,
    }
    seen_texts = []
    logo_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def capture_team_logo(_image, _draw, logo_url, _x, _y, size, fallback_text):
        logo_calls.append((str(logo_url or ""), int(size), str(fallback_text or "")))

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = capture_team_logo

    plugin._draw_wnba_live_team_row(image, draw, 10, 230, 8, event, "a")

    assert "\u98ce\u66b4" in seen_texts
    assert "SEATTLE STORM" not in seen_texts
    assert ("https://example.com/wnba-sea.png", 11, "SEA") in logo_calls


def test_wnba_logo_fallback_prefers_stable_team_code():
    assert (
        SportsDashboard._wnba_logo_fallback(
            {"team_a": "\u98ce\u66b4", "team_a_name": "Seattle Storm"},
            "a",
        )
        == "SEA"
    )
    assert (
        SportsDashboard._wnba_logo_fallback(
            {"team_b": "\u738b\u724c", "team_b_code": "LV", "team_b_name": "Las Vegas Aces"},
            "b",
        )
        == "LV"
    )


def test_wnba_score_side_fill_key_marks_only_current_leader():
    event = {"wins_a": 72, "wins_b": 78}

    assert SportsDashboard._wnba_score_side_fill_key(event, "a") == "text"
    assert SportsDashboard._wnba_score_side_fill_key(event, "b") == "wnba_accent"
    assert SportsDashboard._wnba_score_side_fill_key({"wins_a": 78, "wins_b": 78}, "a") == "text"
    assert SportsDashboard._wnba_score_side_fill_key({"wins_a": None, "wins_b": 78}, "b") == "text"


def test_wnba_final_winner_uses_win_label_and_winner_flag():
    event = {
        "state": "post",
        "team_a": "SEA",
        "team_b": "LV",
        "team_a_code": "SEA",
        "team_b_code": "LV",
        "wins_a": 72,
        "wins_b": 78,
        "winner_a": False,
        "winner_b": True,
    }

    assert SportsDashboard._wnba_score_side_fill_key(event, "a") == "text"
    assert SportsDashboard._wnba_score_side_fill_key(event, "b") == "wnba_accent"
    assert SportsDashboard._wnba_lead_label(event) == "\u62c9\u65af\u7ef4\u52a0\u65af\u738b\u724c \u80dc6\u5206"


def test_wnba_lead_label_uses_full_name_only_for_final_winner():
    event = {
        "state": "in",
        "team_a": "SEA",
        "team_b": "LV",
        "team_a_code": "SEA",
        "team_b_code": "LV",
        "wins_a": 72,
        "wins_b": 78,
        "winner_a": False,
        "winner_b": True,
    }

    assert SportsDashboard._wnba_lead_label(event) == "\u738b\u724c +6"
    event["state"] = "post"
    assert SportsDashboard._wnba_lead_label(event) == "\u62c9\u65af\u7ef4\u52a0\u65af\u738b\u724c \u80dc6\u5206"


def test_wnba_live_score_team_highlights_leading_side_score():
    plugin = _plugin()
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    event = {
        "team_a": "Seattle Storm",
        "team_b": "Las Vegas Aces",
        "team_a_code": "SEA",
        "team_b_code": "LV",
        "team_b_logo": "https://example.com/wnba-lv.png",
        "wins_a": 72,
        "wins_b": 78,
    }
    right_aligned = []

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_right_aligned = lambda _draw, _pos, text, _font, fill: right_aligned.append((str(text), fill))

    plugin._draw_wnba_live_score_team(image, draw, 10, 118, 8, event, "b", "78")

    assert ("78", COLORS["wnba_accent"]) in right_aligned


def test_wnba_side_column_prioritizes_live_pulse_before_schedule():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)
    parsed["events"][0]["spread"] = "LV -4.5"
    parsed["events"][0]["over_under"] = "O/U 166.5"
    upcoming = dict(parsed["events"][0])
    upcoming.update(
        {
            "event_id": "wnba-upcoming",
            "state": "pre",
            "status_text": "Preview",
            "start": now + timedelta(hours=3),
            "team_a": "PHX",
            "team_b": "NY",
            "team_a_logo": "https://example.com/wnba-phx.png",
            "team_b_logo": "https://example.com/wnba-ny.png",
            "wins_a": None,
            "wins_b": None,
            "period_scores_a": [],
            "period_scores_b": [],
        }
    )
    parsed["events"].append(upcoming)
    card = SportsDashboard._offseason_hub_card("WNBA", parsed, now)
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    logo_calls = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def capture_team_logo(_image, _draw, logo_url, _x, _y, size, fallback_text):
        logo_calls.append((str(logo_url or ""), int(size), str(fallback_text or "")))

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = capture_team_logo
    plugin._draw_wnba_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert "UPCOMING" in seen_texts
    assert "LIVE PULSE" in seen_texts
    assert seen_texts.index("LIVE PULSE") < seen_texts.index("UPCOMING")
    assert "Q3 4:22" in seen_texts
    assert "\u738b\u724c +6" in seen_texts
    assert "\u98ce\u66b4" in seen_texts
    assert "72" in seen_texts
    assert "\u738b\u724c" in seen_texts
    assert "78" in seen_texts
    assert "Q3 24-31" in seen_texts
    assert "ION / LV -4.5 / O/U 166.5" in seen_texts
    assert "Michelob ULTRA Arena" in seen_texts
    assert "RECENT" not in seen_texts
    assert ("https://example.com/wnba-sea.png", 11, "SEA") in logo_calls
    assert ("https://example.com/wnba-lv.png", 11, "LV") in logo_calls
    assert icon_calls[-6:] == ["CLOCK", "LEAD", "SCORE", "QTR", "TV", "VENUE"]
    assert "LINE" not in icon_calls


def test_wnba_side_column_replaces_empty_recent_with_game_info_for_next_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)
    upcoming = dict(parsed["events"][0])
    upcoming.update(
        {
            "event_id": "wnba-next-only",
            "state": "pre",
            "status_text": "Preview",
            "start": now + timedelta(hours=3),
            "team_a": "PHX",
            "team_b": "NY",
            "team_a_logo": "https://example.com/wnba-phx.png",
            "team_b_logo": "https://example.com/wnba-ny.png",
            "wins_a": None,
            "wins_b": None,
            "record_a": "3-1",
            "record_b": "2-2",
            "period_scores_a": [],
            "period_scores_b": [],
        }
    )
    card = SportsDashboard._offseason_hub_card("WNBA", {"events": [upcoming]}, now)
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert "UPCOMING" in seen_texts
    assert "GAME INFO" in seen_texts
    assert "06/14 4:30 PM / Preview" in seen_texts
    assert "\u6c34\u661f @ \u81ea\u7531\u4eba" in seen_texts
    assert "\u6c34\u661f 3-1 / \u81ea\u7531\u4eba 2-2" in seen_texts
    assert "ION" in seen_texts
    assert "Michelob ULTRA Arena" in seen_texts
    assert "RECENT" not in seen_texts
    assert icon_calls[-5:] == ["TIP", "MATCH", "TV", "VENUE", "RECORD"]


def test_wnba_game_info_shows_odds_without_hiding_venue():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)
    upcoming = dict(parsed["events"][0])
    upcoming.update(
        {
            "event_id": "wnba-next-odds",
            "state": "pre",
            "status_text": "Preview",
            "start": now + timedelta(hours=3),
            "team_a": "PHX",
            "team_b": "NY",
            "team_a_logo": "https://example.com/wnba-phx.png",
            "team_b_logo": "https://example.com/wnba-ny.png",
            "wins_a": None,
            "wins_b": None,
            "record_a": "3-1",
            "record_b": "2-2",
            "spread": "NY -4.5",
            "over_under": "O/U 166.5",
            "period_scores_a": [],
            "period_scores_b": [],
        }
    )
    card = SportsDashboard._offseason_hub_card("WNBA", {"events": [upcoming]}, now)
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert "GAME INFO" in seen_texts
    assert "06/14 4:30 PM / Preview" in seen_texts
    assert "ION / NY -4.5 / O/U 166.5" in seen_texts
    assert "Michelob ULTRA Arena" in seen_texts
    assert "SPREAD" not in icon_calls
    assert "STATUS" not in icon_calls
    assert icon_calls[-5:] == ["TIP", "MATCH", "TV", "VENUE", "RECORD"]


def test_wnba_main_card_uses_pregame_context_for_scheduled_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)
    upcoming = dict(parsed["events"][0])
    upcoming.update(
        {
            "event_id": "wnba-main-next",
            "state": "pre",
            "status_text": "Preview",
            "start": now + timedelta(hours=3),
            "team_a": "PHX",
            "team_b": "NY",
            "team_a_logo": "https://example.com/wnba-phx.png",
            "team_b_logo": "https://example.com/wnba-ny.png",
            "wins_a": None,
            "wins_b": None,
            "record_a": "3-1",
            "record_b": "2-2",
            "period_scores_a": [],
            "period_scores_b": [],
        }
    )
    card = SportsDashboard._offseason_hub_card("WNBA", {"events": [upcoming]}, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert "TIP OFF" in seen_texts
    assert "06/14 4:30 PM" in seen_texts
    assert "TV ION / Michelob ULTRA Arena / Preview" in seen_texts
    assert "\u6c34\u661f 3-1 / \u81ea\u7531\u4eba 2-2 / Preview" not in seen_texts
    assert "QTR DATA PENDING" not in seen_texts
    assert icon_calls[-1] == "TIP"


def test_wnba_pregame_meta_prioritizes_tv_and_odds_before_venue():
    event = {
        "team_a": "PHX",
        "team_b": "NY",
        "record_a": "3-1",
        "record_b": "2-2",
        "status_text": "Preview",
        "broadcast": "ION",
        "spread": "NY -4.5",
        "over_under": "O/U 166.5",
        "venue": "Michelob ULTRA Arena",
        "city": "Las Vegas, NV",
    }

    assert (
        SportsDashboard._wnba_pregame_meta_label(event)
        == "TV ION | NY -4.5 | O/U 166.5 / Michelob ULTRA Arena / Preview"
    )

    event["broadcast"] = ""
    assert SportsDashboard._wnba_pregame_meta_label(event) == "NY -4.5 / O/U 166.5 / Michelob ULTRA Arena / Preview"


def test_wnba_pregame_meta_falls_back_to_records_when_broadcast_and_venue_missing():
    event = {
        "team_a": "PHX",
        "team_b": "NY",
        "record_a": "3-1",
        "record_b": "2-2",
        "status_text": "Preview",
        "broadcast": "",
        "venue": "",
        "city": "",
    }

    assert SportsDashboard._wnba_pregame_meta_label(event) == "\u6c34\u661f 3-1 / \u81ea\u7531\u4eba 2-2 / Preview"


def test_wnba_meta_uses_neutral_module_fallback_when_details_are_empty():
    assert SportsDashboard._wnba_pregame_meta_label({}) == "WNBA GAME INFO"
    assert SportsDashboard._wnba_result_meta_label({}) == "WNBA RESULT"


def test_wnba_live_meta_keeps_score_context_and_adds_tv_line_info():
    la = ZoneInfo("America/Los_Angeles")
    event = SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)["events"][0]
    event["spread"] = "LV -3.5"
    event["over_under"] = "O/U 166.5"

    assert (
        SportsDashboard._wnba_live_main_meta_label(event)
        == "\u738b\u724c +6 / Q3 24-31 / TV ION  |  SPREAD LV -3.5  |  O/U 166.5"
    )
    pulse_rows = SportsDashboard._wnba_live_pulse_rows(event)
    assert ("TV", "ION / LV -3.5 / O/U 166.5", False) in pulse_rows
    assert not any(row[0] == "LINE" for row in pulse_rows)


def test_wnba_live_pulse_rows_use_spread_label_without_broadcast():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_wnba_scoreboard_payload()))
    payload["events"][0]["competitions"][0]["odds"] = [{"details": "LV -4.5", "overUnder": 166.5}]
    event = SportsDashboard._parse_wnba_scoreboard(payload, la)["events"][0]
    event = dict(event)
    event["broadcast"] = ""

    pulse_rows = SportsDashboard._wnba_live_pulse_rows(event)

    assert ("SPREAD", "LV -4.5 / O/U 166.5", False) in pulse_rows
    assert not any(row[0] == "LINE" for row in pulse_rows)


def test_wnba_main_card_uses_live_pulse_context_for_live_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "WNBA",
        SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la),
        now,
    )
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert "WNBA LIVE" in seen_texts
    assert "LIVE PULSE" in seen_texts
    assert "Q3 4:22" in seen_texts
    assert "LEAD" in seen_texts
    assert "\u738b\u724c +6" in seen_texts
    assert "QTR" in seen_texts
    assert "TV" in seen_texts
    assert "ION" in seen_texts
    assert "Q1" in seen_texts
    assert "25-28" in seen_texts
    assert "Q2" in seen_texts
    assert "Q3 24-31" in seen_texts
    assert "\u738b\u724c +6 / Q3 24-31 / TV ION" not in seen_texts
    assert "RESULT SNAP" not in seen_texts
    assert icon_calls[-4:] == ["CLOCK", "LEAD", "QTR", "TV"]


def test_wnba_main_card_live_summary_combines_tv_and_odds():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    payload = json.loads(json.dumps(_sample_wnba_scoreboard_payload()))
    payload["events"][0]["competitions"][0]["odds"] = [{"details": "LV -4.5", "overUnder": 166.5}]
    card = SportsDashboard._offseason_hub_card(
        "WNBA",
        SportsDashboard._parse_wnba_scoreboard(payload, la),
        now,
    )
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert "TV" in seen_texts
    assert "ION / LV -4.5 / O/U 166.5" in seen_texts
    assert "LINE" not in icon_calls
    assert icon_calls[-4:] == ["CLOCK", "LEAD", "QTR", "TV"]


def test_wnba_main_card_draws_current_quarter_score_strip():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "WNBA",
        SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la),
        now,
    )
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    quarter_cells = []

    def capture_quarter_cell(_draw, _box, quarter, score, active=False):
        quarter_cells.append((str(quarter), str(score), bool(active)))

    plugin._draw_wnba_quarter_score_cell = capture_quarter_cell
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert quarter_cells == [
        ("Q1", "25-28", False),
        ("Q2", "29-27", False),
        ("Q3", "24-31", True),
        ("Q4", "28-26", False),
    ]


def test_wnba_main_card_live_summary_falls_back_when_score_context_missing():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)
    event = dict(parsed["events"][0])
    event.update(
        {
            "wins_a": None,
            "wins_b": None,
            "period": None,
            "period_scores_a": [],
            "period_scores_b": [],
        }
    )
    card = SportsDashboard._offseason_hub_card("WNBA", {"events": [event]}, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert "TV" in seen_texts
    assert "ION" in seen_texts
    assert "LEAD" not in icon_calls
    assert "QTR" not in icon_calls
    assert "TV" in icon_calls


def test_wnba_main_card_uses_own_court_tint_not_generic_gold_panel():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "WNBA",
        SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la),
        now,
    )
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert COLORS["wnba_court"] != COLORS["panel_gold"]
    assert image.getpixel((30, 54)) == COLORS["wnba_court"]


def test_wnba_main_card_highlights_score_leader():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "WNBA",
        SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la),
        now,
    )
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    team_score_calls = []

    def capture_team_score(*args, **kwargs):
        team_score_calls.append(
            {
                "team": args[4],
                "score": args[5],
                "logo_fallback": kwargs.get("logo_fallback"),
                "team_fill": kwargs.get("team_fill"),
                "score_fill": kwargs.get("score_fill"),
            }
        )

    plugin._draw_hub_team_score = capture_team_score
    plugin._draw_wnba_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert team_score_calls[0]["team"] == "\u897f\u96c5\u56fe\u98ce\u66b4"
    assert team_score_calls[0]["score"] == 72
    assert team_score_calls[0]["team_fill"] == COLORS["text"]
    assert team_score_calls[0]["score_fill"] == COLORS["text"]
    assert team_score_calls[0]["logo_fallback"] == "SEA"
    assert team_score_calls[1]["team"] == "\u62c9\u65af\u7ef4\u52a0\u65af\u738b\u724c"
    assert team_score_calls[1]["score"] == 78
    assert team_score_calls[1]["team_fill"] == COLORS["wnba_accent"]
    assert team_score_calls[1]["score_fill"] == COLORS["wnba_accent"]
    assert team_score_calls[1]["logo_fallback"] == "LV"


def test_wnba_main_card_uses_result_meta_when_final_quarter_scores_missing():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 15, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)
    final_event = dict(parsed["events"][0])
    final_event.update(
        {
            "event_id": "wnba-final-no-periods",
            "state": "post",
            "status_text": "Final",
            "start": datetime(2026, 6, 14, 16, 0, tzinfo=la),
            "wins_a": 72,
            "wins_b": 78,
            "record_a": "7-4",
            "record_b": "8-3",
            "period_scores_a": [],
            "period_scores_b": [],
        }
    )
    card = SportsDashboard._offseason_hub_card("WNBA", {"events": [final_event]}, now)
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert "WNBA RECENT" in seen_texts
    assert "RESULT SNAP" in seen_texts
    assert "Final" in seen_texts
    assert "\u62c9\u65af\u7ef4\u52a0\u65af\u738b\u724c \u80dc6\u5206 / Final" in seen_texts
    assert "QTR DATA PENDING" not in seen_texts
    assert icon_calls[-1] == "SCORE"


def test_wnba_side_column_uses_result_snap_when_only_recent_is_main_final():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 15, 13, 30, tzinfo=la)
    parsed = SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)
    final_event = dict(parsed["events"][0])
    final_event.update(
        {
            "event_id": "wnba-final-snap",
            "state": "post",
            "status_text": "Final",
            "start": datetime(2026, 6, 14, 16, 0, tzinfo=la),
        }
    )
    card = SportsDashboard._offseason_hub_card("WNBA", {"events": [final_event]}, now)
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert "RESULT SNAP" in seen_texts
    assert "QUARTER LOG" in seen_texts
    assert "\u62c9\u65af\u7ef4\u52a0\u65af\u738b\u724c \u80dc6\u5206" in seen_texts
    assert "\u98ce\u66b4 72 / \u738b\u724c 78" in seen_texts
    assert "ION" in seen_texts
    assert "VENUE" in seen_texts
    assert "Michelob ULTRA Arena" in seen_texts
    assert "Q1" in seen_texts
    assert "25-28" in seen_texts
    assert "UPCOMING" not in seen_texts
    assert "RECENT" not in seen_texts
    assert "No WNBA schedule" not in seen_texts
    assert icon_calls[:4] == ["WIN", "SCORE", "TV", "VENUE"]
    assert icon_calls[-4:] == ["PERIOD", "PERIOD", "PERIOD", "PERIOD"]


def test_football_live_side_column_uses_drive_and_game_info_when_schedule_empty():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 17, 0, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "NCAA",
        SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA"),
        now,
    )
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_football_side_column(image, draw, (0, 0, 220, 200), card, now, "NCAA")

    assert "COLLEGE DRIVE" in seen_texts
    assert "LIVE DRIVE" not in seen_texts
    assert "GAME INFO" in seen_texts
    assert "Q4 1:18" in seen_texts
    assert "2ND & 8" in seen_texts
    assert "MICH 36 / POS \u5fb7\u5dde" in seen_texts
    assert "ESPN / TEX -6.5 / O/U 52.5" in seen_texts
    assert "NEUTRAL / Kickoff Classic / AT&T Stadium" in seen_texts
    assert "#12 \u5fb7\u5dde 0-0 / #7 \u5bc6\u6b47\u6839 0-0" in seen_texts
    assert icon_calls[:3] == ["QTR", "DOWN", "FIELD"]
    assert icon_calls[3:] == ["TV", "SITE", "RECORD"]


def test_football_side_column_replaces_empty_recent_with_live_drive_after_upcoming():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 10, 17, 30, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "NFL",
        SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL"),
        now,
    )
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_side_column(image, draw, (0, 0, 220, 200), card, now, "NFL")

    assert "UPCOMING" in seen_texts
    assert "LIVE DRIVE" in seen_texts
    assert seen_texts.index("LIVE DRIVE") < seen_texts.index("UPCOMING")
    assert "Q2 8:42" in seen_texts
    assert "3RD & 4" in seen_texts
    assert "SEA 42 / POS \u6d77\u9e70" in seen_texts
    assert "Kenneth Walker run for 6 yards" in seen_texts
    assert "NBC / NE -2.5 / O/U 44.5" in seen_texts
    assert "RECENT" not in seen_texts
    assert icon_calls[-5:] == ["QTR", "DOWN", "FIELD", "PLAY", "TV"]


def test_football_side_column_replaces_empty_recent_with_game_info_for_next_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 10, 17, 30, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")
    next_event = next(event for event in parsed["events"] if event["event_id"] == "nfl-next")
    card = SportsDashboard._offseason_hub_card("NFL", {"events": [next_event]}, now)
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_side_column(image, draw, (0, 0, 220, 200), card, now, "NFL")

    assert "UPCOMING" in seen_texts
    assert "GAME INFO" in seen_texts
    assert "09/14 1:25 PM" in seen_texts
    assert "\u5305\u88c5\u5de5 @ \u718a" in seen_texts
    assert "\u5305\u88c5\u5de5 0-0 / \u718a 0-0" in seen_texts
    assert "FOX / CHI -1.5 / O/U 42.5" in seen_texts
    assert "Soldier Field" in seen_texts
    assert "RECENT" not in seen_texts
    assert icon_calls[-5:] == ["KICK", "MATCH", "TV", "VENUE", "RECORD"]


def test_football_side_column_uses_final_snap_when_no_recent_or_upcoming():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 15, 17, 30, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")
    final_event = dict(next(event for event in parsed["events"] if event["event_id"] == "nfl-next"))
    final_event.update(
        {
            "state": "post",
            "status_text": "Final",
            "start": datetime(2026, 9, 14, 13, 25, tzinfo=la),
            "wins_a": 24,
            "wins_b": 21,
            "winner_a": True,
            "winner_b": False,
            "down_distance": "",
            "yard_line": "",
            "possession": "",
        }
    )
    card = {"sport": "NFL", "status": "RECENT", "main": final_event, "upcoming": [], "recent": []}
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    right_aligned = []
    original_fit_text = plugin._fit_text
    original_right_aligned = plugin._draw_right_aligned

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def capture_right_aligned(draw_obj, pos, text, font, fill):
        right_aligned.append((str(text), fill))
        return original_right_aligned(draw_obj, pos, text, font, fill)

    plugin._fit_text = capture_fit_text
    plugin._draw_right_aligned = capture_right_aligned
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_side_column(image, draw, (0, 0, 220, 200), card, now, "NFL")

    assert "FINAL SNAP" in seen_texts
    assert "\u7eff\u6e7e\u5305\u88c5\u5de5 \u80dc3\u5206" in seen_texts
    assert "\u5305\u88c5\u5de5 24 / \u718a 21" in seen_texts
    assert "\u5305\u88c5\u5de5 0-0 / \u718a 0-0" in seen_texts
    assert "FOX / CHI -1.5 / O/U 42.5" in seen_texts
    assert "Soldier Field" in seen_texts
    assert "UPCOMING" not in seen_texts
    assert "RECENT" not in seen_texts
    assert "No recent results" not in seen_texts
    assert icon_calls[:5] == ["WIN", "SCORE", "RECORD", "TV", "VENUE"]
    assert ("\u7eff\u6e7e\u5305\u88c5\u5de5 \u80dc3\u5206", COLORS["nfl_accent"]) in right_aligned


def test_ncaa_side_column_uses_final_snap_for_neutral_site_result_without_lists():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 30, 12, 0, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    final_event = dict(parsed["events"][0])
    final_event.update(
        {
            "state": "post",
            "status_text": "Final",
            "start": datetime(2026, 8, 29, 16, 30, tzinfo=la),
            "wins_a": 31,
            "wins_b": 28,
            "winner_a": True,
            "winner_b": False,
            "period": None,
            "clock": "",
            "down_distance": "",
            "yard_line": "",
            "possession": "",
        }
    )
    card = {"sport": "NCAA", "status": "RECENT", "main": final_event, "upcoming": [], "recent": []}
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_side_column(image, draw, (0, 0, 220, 200), card, now, "NCAA")

    assert "FINAL SNAP" in seen_texts
    assert "#12 \u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b \u80dc3\u5206" in seen_texts
    assert "#12 \u5fb7\u5dde 31 / #7 \u5bc6\u6b47\u6839 28" in seen_texts
    assert "#12 \u5fb7\u5dde 0-0 / #7 \u5bc6\u6b47\u6839 0-0" in seen_texts
    assert "NEUTRAL / Kickoff Classic / AT&T Stadium" in seen_texts
    assert "ESPN / TEX -6.5 / O/U 52.5" in seen_texts
    assert "RANKED WATCH" not in seen_texts
    assert "RECENT" not in seen_texts
    assert "No recent results" not in seen_texts
    assert icon_calls[:5] == ["WIN", "SCORE", "RECORD", "SITE", "TV"]


def test_football_main_card_uses_pregame_context_for_scheduled_nfl_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 10, 17, 30, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")
    next_event = next(event for event in parsed["events"] if event["event_id"] == "nfl-next")
    card = SportsDashboard._offseason_hub_card("NFL", {"events": [next_event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_main_card(image, draw, (0, 0, 320, 190), card, now, "NFL")

    assert "KICKOFF" in seen_texts
    assert "09/14 1:25 PM" in seen_texts
    assert "TV FOX / CHI -1.5" in seen_texts
    assert "Soldier Field  |  O/U 42.5" in seen_texts
    assert "SCHEDULED" not in seen_texts
    assert icon_calls[-1] == "KICK"


def test_football_main_card_uses_final_context_for_completed_nfl_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 15, 17, 30, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")
    final_event = dict(next(event for event in parsed["events"] if event["event_id"] == "nfl-next"))
    final_event.update(
        {
            "state": "post",
            "status_text": "Final",
            "start": datetime(2026, 9, 14, 13, 25, tzinfo=la),
            "wins_a": 24,
            "wins_b": 21,
            "down_distance": "",
            "yard_line": "",
            "possession": "",
        }
    )
    card = SportsDashboard._offseason_hub_card("NFL", {"events": [final_event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_main_card(image, draw, (0, 0, 320, 190), card, now, "NFL")

    assert "NFL RECENT" in seen_texts
    assert "FINAL" in seen_texts
    assert "09/14" in seen_texts
    assert "Soldier Field" in seen_texts
    assert "TV FOX  |  SPREAD CHI -1.5  |  O/U 42.5" in seen_texts
    assert "SCHEDULED" not in seen_texts
    assert icon_calls[-1] == "SCORE"


def test_nfl_main_card_uses_nfl_specific_pregame_context():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 10, 17, 30, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")
    next_event = next(event for event in parsed["events"] if event["event_id"] == "nfl-next")
    card = SportsDashboard._offseason_hub_card("NFL", {"events": [next_event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_nfl_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert "NFL NEXT" in seen_texts
    assert "NFL KICK" in seen_texts
    assert "09/14 1:25 PM" in seen_texts
    assert "TV FOX / CHI -1.5 / O/U 42.5" in seen_texts
    assert "Soldier Field" in seen_texts
    assert "KICKOFF" not in seen_texts
    assert icon_calls[-1] == "KICK"


def test_scheduled_football_main_cards_do_not_repeat_preview_center_label():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    fallback = SportsDashboard._fallback_offseason_hub_data(la, now)
    nfl_card = SportsDashboard._offseason_hub_card("NFL", fallback["nfl"], now)
    ncaa_card = SportsDashboard._offseason_hub_card("NCAA", fallback["ncaa"], now)
    image = Image.new("RGB", (340, 420), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_nfl_main_card(image, draw, (0, 0, 320, 190), nfl_card, now)
    plugin._draw_ncaa_main_card(image, draw, (0, 210, 320, 400), ncaa_card, now)

    assert "Preview" not in seen_texts
    assert "PREVIEW" not in seen_texts
    assert "NFL KICK" in seen_texts
    assert "KICKOFF" in seen_texts
    assert "NEUTRAL / Kickoff Watch" in seen_texts


def test_nfl_main_card_uses_nfl_specific_final_context():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 15, 17, 30, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")
    final_event = dict(next(event for event in parsed["events"] if event["event_id"] == "nfl-next"))
    final_event.update(
        {
            "state": "post",
            "status_text": "Final",
            "start": datetime(2026, 9, 14, 13, 25, tzinfo=la),
            "wins_a": 24,
            "wins_b": 21,
            "down_distance": "",
            "yard_line": "",
            "possession": "",
        }
    )
    card = SportsDashboard._offseason_hub_card("NFL", {"events": [final_event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_nfl_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert "NFL RECENT" in seen_texts
    assert "FINAL SCORE" in seen_texts
    assert "09/14" in seen_texts
    assert "TV FOX / CHI -1.5 / O/U 42.5" in seen_texts
    assert "Soldier Field" in seen_texts
    assert "KICKOFF" not in seen_texts
    assert icon_calls[-1] == "SCORE"


def test_football_field_marker_fraction_maps_yard_line_to_screen_position():
    event = {
        "team_a_code": "SEA",
        "team_b_code": "NE",
        "possession": "SEA",
        "yard_line": "SEA 42",
    }

    assert SportsDashboard._football_field_marker_fraction(event) == 0.42

    event["yard_line"] = "NE 36"
    assert SportsDashboard._football_field_marker_fraction(event) == 0.64

    event.update({"possession": "NE", "yard_line": "NE 25"})
    assert SportsDashboard._football_field_marker_fraction(event) == 0.75


def test_football_possession_side_fill_key_marks_only_live_possession():
    event = {
        "state": "in",
        "team_a_code": "SEA",
        "team_b_code": "NE",
        "possession": "SEA",
    }

    assert SportsDashboard._football_possession_side_fill_key(event, "a") == "amber"
    assert SportsDashboard._football_possession_side_fill_key(event, "b") == "text"

    event["state"] = "post"
    assert SportsDashboard._football_possession_side_fill_key(event, "a") == "text"


def test_football_team_side_fill_key_marks_live_possession_or_final_winner():
    event = {
        "state": "in",
        "team_a_code": "SEA",
        "team_b_code": "NE",
        "possession": "NE",
        "wins_a": 17,
        "wins_b": 14,
    }

    assert SportsDashboard._football_team_side_fill_key(event, "a") == "text"
    assert SportsDashboard._football_team_side_fill_key(event, "b") == "amber"

    event.update({"state": "post", "possession": "", "wins_a": 17, "wins_b": 24})
    assert SportsDashboard._football_team_side_fill_key(event, "a") == "text"
    assert SportsDashboard._football_team_side_fill_key(event, "b") == "amber"

    event.update({"wins_a": 24, "wins_b": 24})
    assert SportsDashboard._football_team_side_fill_key(event, "a") == "text"
    assert SportsDashboard._football_team_side_fill_key(event, "b") == "text"


def test_football_possession_display_label_uses_chinese_team_names():
    la = ZoneInfo("America/Los_Angeles")
    nfl_event = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")["events"][0]
    ncaa_event = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")["events"][0]

    assert nfl_event["possession"] == "SEA"
    assert ncaa_event["possession"] == "TEX"
    assert SportsDashboard._football_possession_display_label(nfl_event, "NFL") == "\u6d77\u9e70"
    assert SportsDashboard._football_possession_display_label(ncaa_event, "NCAA") == "\u5fb7\u5dde"
    assert SportsDashboard._football_display_team(nfl_event, "a", "NFL", full=True) == "\u897f\u96c5\u56fe\u6d77\u9e70"
    assert SportsDashboard._football_display_team(nfl_event, "b", "NFL", full=True) == "\u65b0\u82f1\u683c\u5170\u7231\u56fd\u8005"


def test_football_display_team_honors_full_ncaa_program_names_when_zh_short_exists():
    event = {
        "team_a": "Texas",
        "team_a_name": "Texas Longhorns",
        "team_a_code": "TEX",
        "team_a_zh": "\u5fb7\u5dde",
        "team_a_rank": 12,
    }

    assert SportsDashboard._football_display_team(event, "a", "NCAA") == "#12 \u5fb7\u5dde"
    assert (
        SportsDashboard._football_display_team(event, "a", "NCAA", full=True)
        == "#12 \u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b"
    )


def test_football_final_winner_label_uses_full_chinese_team_names():
    nfl_event = {
        "sport": "NFL",
        "state": "post",
        "team_a": "Green Bay Packers",
        "team_a_code": "GB",
        "team_a_zh": "\u5305\u88c5\u5de5",
        "team_b": "Chicago Bears",
        "team_b_code": "CHI",
        "team_b_zh": "\u718a",
        "wins_a": 24,
        "wins_b": 21,
        "winner_a": True,
        "winner_b": False,
    }
    ncaa_event = {
        "sport": "NCAA",
        "state": "post",
        "team_a": "Texas",
        "team_a_name": "Texas Longhorns",
        "team_a_code": "TEX",
        "team_a_zh": "\u5fb7\u5dde",
        "team_a_rank": 12,
        "team_b": "Michigan",
        "team_b_name": "Michigan Wolverines",
        "team_b_code": "MICH",
        "team_b_zh": "\u5bc6\u6b47\u6839",
        "team_b_rank": 7,
        "wins_a": 31,
        "wins_b": 28,
        "winner_a": True,
        "winner_b": False,
    }

    assert SportsDashboard._football_final_winner_label(nfl_event, "NFL") == "\u7eff\u6e7e\u5305\u88c5\u5de5 \u80dc3\u5206"
    assert (
        SportsDashboard._football_final_winner_label(ncaa_event, "NCAA")
        == "#12 \u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b \u80dc3\u5206"
    )


def test_football_display_team_name_supports_ncaa_chinese_fallbacks():
    assert SportsDashboard._football_display_team_name("TEX", "Texas", "NCAA") == "\u5fb7\u5dde"
    assert (
        SportsDashboard._football_display_team_name("TEX", "Texas", "NCAA", full=True)
        == "\u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b"
    )
    assert (
        SportsDashboard._football_display_team_name(
            "TBD",
            "Texas Longhorns",
            "NCAA",
            aliases=["Texas Longhorns", "Texas"],
            full=True,
        )
        == "\u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b"
    )


def test_football_small_note_label_uses_venue_fallback():
    assert (
        SportsDashboard._football_small_note_label(
            {
                "sport": "NFL",
                "state": "pre",
                "venue": "Soldier Field",
                "broadcast": "",
                "spread": "",
                "over_under": "",
            }
        )
        == "Soldier Field"
    )
    assert (
        SportsDashboard._football_small_note_label(
            {
                "sport": "NCAA",
                "state": "pre",
                "note": "Kickoff Classic",
                "venue": "AT&T Stadium",
            }
        )
        == "Kickoff Classic / AT&T Stadium"
    )


def test_ncaa_meta_label_preserves_neutral_site_venue_and_full_line_info():
    assert (
        SportsDashboard._ncaa_meta_label(
            {
                "neutral_site": True,
                "venue": "AT&T Stadium",
                "broadcast": "ESPN",
                "spread": "TEX -6.5",
                "over_under": "O/U 52.5",
            }
        )
        == "NEUTRAL SITE  |  AT&T Stadium  |  TV ESPN / SPREAD TEX -6.5 / O/U 52.5"
    )


def test_ncaa_header_badge_prioritizes_ranked_games_before_neutral_site():
    assert (
        SportsDashboard._ncaa_header_badge_label(
            {"team_a_rank": 12, "team_b_rank": 7, "neutral_site": True}
        )
        == "TOP 25"
    )
    assert (
        SportsDashboard._ncaa_header_badge_label(
            {"team_a_rank": 3, "team_b_rank": "", "neutral_site": True}
        )
        == "RANKED"
    )
    assert SportsDashboard._ncaa_header_badge_label({"neutral_site": True}) == "NEUTRAL"
    assert SportsDashboard._ncaa_header_badge_label({}) == ""


def test_football_small_note_label_prioritizes_final_status():
    assert (
        SportsDashboard._football_small_note_label(
            {
                "sport": "NFL",
                "state": "post",
                "status_text": "Final",
                "venue": "Soldier Field",
                "broadcast": "FOX",
                "spread": "CHI -1.5",
                "over_under": "O/U 42.5",
            }
        )
        == "FINAL / Soldier Field"
    )
    assert (
        SportsDashboard._football_small_note_label(
            {
                "sport": "NCAA",
                "state": "post",
                "status_text": "Final",
                "note": "Kickoff Classic",
                "venue": "AT&T Stadium",
            }
        )
        == "FINAL / Kickoff Classic / AT&T Stadium"
    )


def test_nfl_main_card_keeps_live_team_scores_high_contrast():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 10, 17, 30, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "NFL",
        SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL"),
        now,
    )
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    team_score_calls = []

    def capture_team_score(*args, **kwargs):
        team_score_calls.append(
            {
                "team": args[4],
                "score": args[5],
                "team_fill": kwargs.get("team_fill"),
                "score_fill": kwargs.get("score_fill"),
            }
        )

    plugin._draw_hub_team_score = capture_team_score
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_main_card(image, draw, (0, 0, 320, 190), card, now, "NFL")

    assert team_score_calls[0]["team"] == "\u897f\u96c5\u56fe\u6d77\u9e70"
    assert team_score_calls[0]["score"] == 17
    assert team_score_calls[0]["team_fill"] == COLORS["text"]
    assert team_score_calls[0]["score_fill"] == COLORS["text"]
    assert team_score_calls[1]["team"] == "\u65b0\u82f1\u683c\u5170\u7231\u56fd\u8005"
    assert team_score_calls[1]["team_fill"] == COLORS["text"]
    assert team_score_calls[1]["score_fill"] == COLORS["text"]


def test_nfl_main_card_highlights_final_winner():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 15, 17, 30, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")
    final_event = dict(next(event for event in parsed["events"] if event["event_id"] == "nfl-next"))
    final_event.update(
        {
            "state": "post",
            "status_text": "Final",
            "start": datetime(2026, 9, 14, 13, 25, tzinfo=la),
            "wins_a": 21,
            "wins_b": 24,
            "down_distance": "",
            "yard_line": "",
            "possession": "",
        }
    )
    card = SportsDashboard._offseason_hub_card("NFL", {"events": [final_event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    team_score_calls = []

    def capture_team_score(*args, **kwargs):
        team_score_calls.append(
            {
                "team": args[4],
                "score": args[5],
                "team_fill": kwargs.get("team_fill"),
                "score_fill": kwargs.get("score_fill"),
            }
        )

    plugin._draw_hub_team_score = capture_team_score
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_nfl_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert team_score_calls[0]["team_fill"] == COLORS["text"]
    assert team_score_calls[0]["score_fill"] == COLORS["text"]
    assert team_score_calls[1]["team"] == "芝加哥熊"
    assert team_score_calls[1]["score"] == 24
    assert team_score_calls[1]["team_fill"] == COLORS["amber"]
    assert team_score_calls[1]["score_fill"] == COLORS["amber"]


def test_nfl_main_card_uses_own_field_tint_for_live_context_box():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 10, 17, 30, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "NFL",
        SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL"),
        now,
    )
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_nfl_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert COLORS["nfl_field_tint"] != COLORS["panel_blue"]
    assert image.getpixel((20, 140)) == COLORS["nfl_field_tint"]


def test_nfl_main_card_live_meta_prioritizes_tv_and_line_before_last_play():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 10, 17, 30, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "NFL",
        SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL"),
        now,
    )
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_nfl_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert "NFL DRIVE" in seen_texts
    assert "DOWN" in seen_texts
    assert "3RD & 4" in seen_texts
    assert "FIELD" in seen_texts
    assert "SEA 42" in seen_texts
    assert "POS \u6d77\u9e70" in seen_texts
    assert "PLAY" in seen_texts
    assert "Kenneth Walker run for 6 yards" in seen_texts
    assert "TV NBC  |  SPREAD NE -2.5  |  O/U 44.5" in seen_texts
    assert "LAST Kenneth Walker run for 6 yards" not in seen_texts
    assert icon_calls[-3:] == ["DOWN", "FIELD", "PLAY"]


def test_nfl_main_card_live_drive_chips_fall_back_without_drive_position():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 10, 17, 30, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")
    event = dict(next(event for event in parsed["events"] if event["event_id"] == "nfl-live"))
    event.update({"down_distance": "", "yard_line": "", "note": "", "last_play": ""})
    card = SportsDashboard._offseason_hub_card("NFL", {"events": [event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_nfl_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert "LIVE DRIVE" in seen_texts
    assert "FIELD" not in icon_calls
    assert icon_calls.count("DOWN") == 2


def test_nfl_live_drive_chips_use_last_play_when_position_missing():
    plugin = _plugin()
    image = Image.new("RGB", (180, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    chips = []

    def capture_chip(_draw, _box, label, value, accent):
        chips.append((label, value, accent))

    plugin._draw_nfl_live_drive_chip = capture_chip
    plugin._draw_nfl_live_drive_chips(
        draw,
        0,
        0,
        160,
        {"down_distance": "", "yard_line": "", "note": "", "last_play": "Timeout New England"},
    )

    assert chips == [("PLAY", "LAST PLAY", COLORS["amber"])]


def test_ncaa_main_card_highlights_final_winner_team_block():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 30, 12, 0, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    final_event = dict(parsed["events"][0])
    final_event.update(
        {
            "state": "post",
            "status_text": "Final",
            "start": datetime(2026, 8, 29, 16, 30, tzinfo=la),
            "wins_a": 31,
            "wins_b": 28,
            "period": None,
            "clock": "",
            "down_distance": "",
            "yard_line": "",
            "possession": "",
        }
    )
    card = SportsDashboard._offseason_hub_card("NCAA", {"events": [final_event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    team_blocks = []

    def capture_team_block(_image, _draw, _x1, _y, _x2, _event, side, align="left", team_fill=None):
        team_blocks.append({"side": side, "align": align, "team_fill": team_fill})

    plugin._draw_ncaa_team_block = capture_team_block
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert team_blocks == [
        {"side": "a", "align": "left", "team_fill": COLORS["amber"]},
        {"side": "b", "align": "right", "team_fill": COLORS["text"]},
    ]


def test_ncaa_side_column_game_info_prioritizes_ranked_neutral_site_context():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 12, 0, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    next_event = dict(parsed["events"][0])
    next_event.update(
        {
            "state": "pre",
            "status_text": "Preview",
            "score_a": "",
            "score_b": "",
            "period": None,
            "clock": "",
            "down_distance": "",
            "yard_line": "",
            "possession": "",
        }
    )
    card = SportsDashboard._offseason_hub_card("NCAA", {"events": [next_event]}, now)
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_side_column(image, draw, (0, 0, 220, 200), card, now, "NCAA")

    assert "RANKED WATCH" in seen_texts
    assert "GAME INFO" in seen_texts
    assert "08/29 4:30 PM" in seen_texts
    assert "#12 \u5fb7\u5dde VS #7 \u5bc6\u6b47\u6839" in seen_texts
    assert "#12 \u5fb7\u5dde 0-0 / #7 \u5bc6\u6b47\u6839 0-0" in seen_texts
    assert "NEUTRAL / Kickoff Classic / AT&T Stadium" in seen_texts
    assert "ESPN / TEX -6.5 / O/U 52.5" in seen_texts
    assert "RECENT" not in seen_texts
    assert icon_calls[-5:] == ["KICK", "MATCH", "TV", "SITE", "RECORD"]


def test_ncaa_matchup_label_maps_english_aliases_to_chinese_school_names():
    event = {
        "team_a": "Texas",
        "team_b": "Michigan",
        "team_a_rank": 12,
        "team_b_rank": 7,
        "record_a": "0-0",
        "record_b": "0-0",
        "neutral_site": True,
    }

    assert SportsDashboard._football_matchup_label(event, "NCAA") == "#12 \u5fb7\u5dde VS #7 \u5bc6\u6b47\u6839"
    assert (
        SportsDashboard._football_record_matchup_label(event, "NCAA")
        == "#12 \u5fb7\u5dde 0-0 / #7 \u5bc6\u6b47\u6839 0-0"
    )


def test_ncaa_school_label_maps_full_english_alias_without_zh_field():
    event = {
        "team_a": "Texas Longhorns",
        "team_b": "Michigan Wolverines",
        "team_a_rank": 12,
        "team_b_rank": 7,
        "neutral_site": True,
    }

    assert SportsDashboard._ncaa_school_label(event, "a") == "\u5fb7\u5dde"
    assert SportsDashboard._ncaa_school_label(event, "b") == "\u5bc6\u6b47\u6839"
    assert SportsDashboard._ncaa_matchup_label(event) == "#12 \u5fb7\u5dde VS #7 \u5bc6\u6b47\u6839"


def test_ncaa_school_label_supports_full_program_names_for_main_cards():
    event = {
        "team_a": "Texas Longhorns",
        "team_b": "Michigan Wolverines",
        "team_a_code": "TEX",
        "team_b_code": "MICH",
        "team_a_zh": "\u5fb7\u5dde",
        "team_b_zh": "\u5bc6\u6b47\u6839",
        "team_a_rank": 12,
        "team_b_rank": 7,
        "neutral_site": True,
    }

    assert SportsDashboard._ncaa_school_label(event, "a", full=True) == "\u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b"
    assert SportsDashboard._ncaa_school_label(event, "b", full=True) == "\u5bc6\u6b47\u6839\u72fc\u737e"
    assert (
        SportsDashboard._ncaa_school_label(event, "a", include_rank=True, full=True)
        == "#12 \u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b"
    )
    assert SportsDashboard._ncaa_school_label({"team_a": "Georgia Bulldogs"}, "a", full=True) == "\u4f50\u6cbb\u4e9a\u6597\u725b\u72ac"
    assert SportsDashboard._ncaa_school_label({"team_a": "Boise State Broncos"}, "a", full=True) == "\u535a\u4f0a\u897f\u5dde\u7acb\u91ce\u9a6c"


def test_ncaa_school_label_supports_common_power_program_full_names():
    cases = [
        ("AUB", "\u5965\u672c\u8001\u864e"),
        ("BAY", "\u8d1d\u52d2\u718a"),
        ("DUKE", "\u675c\u514b\u84dd\u9b54"),
        ("GT", "\u4f50\u6cbb\u4e9a\u7406\u5de5\u9ec4\u5939\u514b"),
        ("ILL", "\u4f0a\u5229\u8bfa\u4f0a\u6218\u6597\u4f0a\u5229\u5c3c"),
        ("IOWA", "\u7231\u8377\u534e\u9e70\u773c"),
        ("ISU", "\u7231\u8377\u534e\u5dde\u7acb\u65cb\u98ce"),
        ("KSU", "\u582a\u8428\u65af\u5dde\u7acb\u91ce\u732b"),
        ("KU", "\u582a\u8428\u65af\u677e\u9e26\u9e70"),
        ("UK", "\u80af\u5854\u57fa\u91ce\u732b"),
        ("LOU", "\u8def\u6613\u7ef4\u5c14\u7ea2\u96c0"),
        ("MSU", "\u5bc6\u6b47\u6839\u5dde\u7acb\u65af\u5df4\u8fbe\u4eba"),
        ("OU", "\u4fc4\u514b\u62c9\u8377\u9a6c\u6377\u8db3\u8005"),
        ("PITT", "\u5339\u5179\u5821\u9ed1\u8c79"),
        ("PUR", "\u666e\u6e21\u9505\u7089\u5de5"),
        ("RUTG", "\u7f57\u683c\u65af\u7ea2\u8863\u9a91\u58eb"),
        ("SC", "\u5357\u5361\u6597\u9e21"),
        ("TCU", "TCU\u89d2\u86d9"),
        ("TTU", "\u5fb7\u5dde\u7406\u5de5\u7ea2\u8272\u7a81\u88ad\u8005"),
        ("UCLA", "UCLA\u68d5\u718a"),
        ("UNC", "\u5317\u5361\u7126\u6cb9\u8e35"),
        ("VT", "\u5f17\u5409\u5c3c\u4e9a\u7406\u5de5\u970d\u57fa"),
        ("WIS", "\u5a01\u65af\u5eb7\u661f\u737e"),
        ("WSU", "\u534e\u76db\u987f\u5dde\u7acb\u7f8e\u6d32\u72ee"),
        ("WVU", "\u897f\u5f17\u5409\u5c3c\u4e9a\u767b\u5c71\u8005"),
    ]
    for code, expected in cases:
        assert SportsDashboard._ncaa_display_school_name(code, code, full=True) == expected


def test_ncaa_school_label_supports_second_wave_program_full_names():
    cases = [
        ("AF", "\u7a7a\u519b\u730e\u9e70"),
        ("APP", "\u963f\u5df4\u62c9\u5951\u4e9a\u5dde\u7acb\u767b\u5c71\u8005"),
        ("ARK", "\u963f\u80af\u8272\u91ce\u732a"),
        ("ARMY", "\u9646\u519b\u9ed1\u9a91\u58eb"),
        ("BYU", "\u6768\u767e\u7ff0\u7f8e\u6d32\u72ee"),
        ("CAL", "\u52a0\u5dde\u91d1\u718a"),
        ("CIN", "\u8f9b\u8f9b\u90a3\u63d0\u718a\u72f8"),
        ("COLO", "\u79d1\u7f57\u62c9\u591a\u6c34\u725b"),
        ("ECU", "\u4e1c\u5361\u7f57\u6765\u7eb3\u6d77\u76d7"),
        ("HAW", "\u590f\u5a01\u5937\u5f69\u8679\u52c7\u58eb"),
        ("HOU", "\u4f11\u65af\u987f\u7f8e\u6d32\u72ee"),
        ("NAVY", "\u6d77\u519b\u519b\u5b98\u751f"),
        ("UCF", "\u4e2d\u4f5b\u7f57\u91cc\u8fbe\u9a91\u58eb"),
        ("UCONN", "\u5eb7\u6d85\u72c4\u683c\u54c8\u58eb\u5947"),
        ("UVA", "\u5f17\u5409\u5c3c\u4e9a\u9a91\u58eb"),
        ("WAKE", "\u7ef4\u514b\u68ee\u6797\u9b54\u9b3c\u6267\u4e8b"),
        ("WYO", "\u6000\u4fc4\u660e\u725b\u4ed4"),
    ]
    for code, expected in cases:
        assert SportsDashboard._ncaa_display_school_name(code, code, full=True) == expected


def test_ncaa_school_label_supports_remaining_program_full_names():
    cases = [
        ("AKR", "\u963f\u514b\u4f26\u9f50\u666e\u65af"),
        ("CCU", "\u5361\u7f57\u6765\u7eb3\u6d77\u5cb8\u96c4\u9e21"),
        ("CMU", "\u4e2d\u5bc6\u6b47\u6839\u5947\u73c0\u74e6\u4eba"),
        ("CONN", "\u5eb7\u6d85\u72c4\u683c\u54c8\u58eb\u5947"),
        ("ODU", "\u8001\u9053\u660e\u541b\u4e3b"),
        ("OHIO", "\u4fc4\u4ea5\u4fc4\u5c71\u732b"),
        ("SHSU", "\u8428\u59c6\u4f11\u65af\u987f\u718a\u72f8"),
        ("UL", "\u8def\u6613\u65af\u5b89\u90a3\u72c2\u6012\u5361\u6d25\u4eba"),
        ("ULL", "\u8def\u6613\u65af\u5b89\u90a3\u72c2\u6012\u5361\u6d25\u4eba"),
    ]
    for code, expected in cases:
        assert SportsDashboard._ncaa_display_school_name(code, code, full=True) == expected


def test_ncaa_school_label_maps_expanded_common_program_aliases():
    cases = [
        ("Georgia Bulldogs", "\u4f50\u6cbb\u4e9a"),
        ("Boise State Broncos", "\u535a\u4f0a\u897f\u5dde\u7acb"),
        ("Iowa State Cyclones", "\u7231\u8377\u534e\u5dde\u7acb"),
        ("Texas Tech Red Raiders", "\u5fb7\u5dde\u7406\u5de5"),
        ("UCF Knights", "\u4e2d\u4f5b\u7f57\u91cc\u8fbe"),
        ("UConn Huskies", "\u5eb7\u6d85\u72c4\u683c"),
        ("West Virginia Mountaineers", "\u897f\u5f17\u5409\u5c3c\u4e9a"),
    ]
    for raw_name, expected in cases:
        assert SportsDashboard._ncaa_school_label({"team_a": raw_name}, "a") == expected


def test_ncaa_school_label_maps_g5_and_service_academy_aliases():
    cases = [
        ("Army Black Knights", "\u9646\u519b"),
        ("Navy Midshipmen", "\u6d77\u519b"),
        ("Air Force Falcons", "\u7a7a\u519b"),
        ("Tulane Green Wave", "\u675c\u5170"),
        ("UNLV Rebels", "\u5185\u534e\u8fbe\u62c9\u65af\u7ef4\u52a0\u65af"),
        ("James Madison Dukes", "\u8a79\u59c6\u65af\u9ea6\u8fea\u900a"),
        ("Liberty Flames", "\u81ea\u7531"),
        ("Louisiana Ragin' Cajuns", "\u8def\u6613\u65af\u5b89\u90a3"),
        ("San Jose State Spartans", "\u5723\u4f55\u585e\u5dde\u7acb"),
        ("Miami (OH) RedHawks", "\u8fc8\u963f\u5bc6\u4fc4\u4ea5\u4fc4"),
        ("Hawai'i Rainbow Warriors", "\u590f\u5a01\u5937"),
    ]

    for raw_name, expected in cases:
        assert SportsDashboard._ncaa_school_label({"team_a": raw_name}, "a") == expected


def test_ncaa_matchup_label_uses_expanded_common_program_codes():
    event = {
        "team_a_code": "UGA",
        "team_b_code": "BSU",
        "team_a": "Georgia",
        "team_b": "Boise State",
        "team_a_rank": 3,
        "team_b_rank": 18,
        "neutral_site": True,
    }

    assert SportsDashboard._ncaa_matchup_label(event) == "#3 \u4f50\u6cbb\u4e9a VS #18 \u535a\u4f0a\u897f\u5dde\u7acb"


def test_ncaa_matchup_label_uses_expanded_g5_program_codes():
    event = {
        "team_a_code": "TULN",
        "team_b_code": "UNLV",
        "team_a": "Tulane",
        "team_b": "UNLV",
        "team_a_rank": 24,
        "team_b_rank": 21,
        "neutral_site": True,
    }

    assert SportsDashboard._ncaa_matchup_label(event) == "#24 \u675c\u5170 VS #21 \u5185\u534e\u8fbe\u62c9\u65af\u7ef4\u52a0\u65af"


def test_nfl_matchup_label_maps_english_aliases_to_chinese_team_names():
    event = {
        "team_a": "Seattle Seahawks",
        "team_b": "New England Patriots",
        "record_a": "2-0",
        "record_b": "1-1",
    }

    assert SportsDashboard._football_matchup_label(event, "NFL") == "\u6d77\u9e70 @ \u7231\u56fd\u8005"
    assert (
        SportsDashboard._football_record_matchup_label(event, "NFL")
        == "\u6d77\u9e70 2-0 / \u7231\u56fd\u8005 1-1"
    )


def test_nfl_standalone_panel_uses_own_comic_accent_not_mlb_blue():
    plugin = _plugin()
    image = Image.new("RGB", (552, 268), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    now = datetime(2026, 9, 10, 17, 50, tzinfo=ZoneInfo("America/Los_Angeles"))
    card = {"sport": "NFL", "status": "NEXT", "main": {"sport": "NFL", "state": "pre"}}
    halftone_colors = []
    shell_accents = []

    def capture_halftone(_draw, _bounds, foreground, _background, _spacing, _radius):
        halftone_colors.append(foreground)

    def capture_shell(_draw, _x1, _y1, _x2, _y2, accent):
        shell_accents.append(accent)

    plugin._draw_halftone = capture_halftone
    plugin._draw_hub_card_shell = capture_shell
    plugin._draw_nfl_field = lambda *_args, **_kwargs: None
    plugin._draw_hub_team_score = lambda *_args, **_kwargs: None
    plugin._draw_nfl_pregame_context = lambda *_args, **_kwargs: None
    plugin._draw_football_side_column = lambda *_args, **_kwargs: None

    plugin._draw_nfl_standalone_panel(image, draw, (0, 0, 551, 267), card, "HUB LIVE", now)

    assert halftone_colors == [COLORS["nfl_accent"]]
    assert shell_accents == [COLORS["nfl_accent"]]
    assert COLORS["nfl_accent"] != COLORS["mlb_accent"]


def test_ncaa_standalone_panel_uses_own_comic_accent_token():
    plugin = _plugin()
    image = Image.new("RGB", (552, 268), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    now = datetime(2026, 8, 29, 16, 45, tzinfo=ZoneInfo("America/Los_Angeles"))
    card = {"sport": "NCAA", "status": "NEXT", "main": {"sport": "NCAA", "state": "pre"}}
    halftone_colors = []
    shell_accents = []

    def capture_halftone(_draw, _bounds, foreground, _background, _spacing, _radius):
        halftone_colors.append(foreground)

    def capture_shell(_draw, _x1, _y1, _x2, _y2, accent):
        shell_accents.append(accent)

    plugin._draw_halftone = capture_halftone
    plugin._draw_hub_card_shell = capture_shell
    plugin._draw_ncaa_field_backdrop = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_team_block = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_pregame_context = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_side_column = lambda *_args, **_kwargs: None

    plugin._draw_ncaa_standalone_panel(image, draw, (0, 0, 551, 267), card, "HUB LIVE", now)

    assert halftone_colors == [COLORS["ncaa_accent"]]
    assert shell_accents == [COLORS["ncaa_accent"]]
    assert COLORS["ncaa_accent"] != COLORS["nfl_accent"]


def test_nfl_standalone_panel_uses_drive_first_layout():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 10, 17, 50, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")
    card = SportsDashboard._offseason_hub_card("NFL", parsed, now)
    image = Image.new("RGB", (552, 268), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    logo_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def capture_team_logo(_image, _draw, logo_url, _x, _y, size, fallback_text):
        logo_calls.append((str(logo_url or ""), int(size), str(fallback_text or "")))

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = capture_team_logo
    plugin._draw_nfl_standalone_panel(image, draw, (0, 0, 551, 267), card, "HUB LIVE", now)

    assert "NFL LIVE" in seen_texts
    assert "\u897f\u96c5\u56fe\u6d77\u9e70" in seen_texts
    assert "\u65b0\u82f1\u683c\u5170\u7231\u56fd\u8005" in seen_texts
    assert "NFL DRIVE" in seen_texts
    assert "3RD & 4" in seen_texts
    assert "SEA 42" in seen_texts
    assert "POS \u6d77\u9e70" in seen_texts
    assert any("Kenneth Walker run for 6 yards" in text for text in seen_texts)
    assert "TV NBC  |  SPREAD NE -2.5  |  O/U 44.5" in seen_texts
    assert ("https://example.com/nfl-sea.png", 20, "SEA") in logo_calls
    assert ("https://example.com/nfl-ne.png", 20, "NE") in logo_calls


def test_nfl_main_card_uses_named_stage_header_without_week_prefix():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 9, 10, 17, 50, tzinfo=la)
    payload = json.loads(json.dumps(_sample_nfl_scoreboard_payload()))
    payload["week"] = {"number": 23, "text": "Super Bowl"}
    parsed = SportsDashboard._parse_football_scoreboard(payload, la, "NFL")
    card = SportsDashboard._offseason_hub_card("NFL", parsed, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_nfl_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert card["week_label"] == "SUPER BOWL"
    assert "SUPER BOWL" in seen_texts
    assert "WEEK SUPER BOWL" not in seen_texts


def test_ncaa_standalone_panel_uses_college_specific_layout():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 16, 45, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    card = SportsDashboard._offseason_hub_card("NCAA", parsed, now)
    image = Image.new("RGB", (552, 268), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    logo_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def capture_team_logo(_image, _draw, logo_url, _x, _y, size, fallback_text):
        logo_calls.append((str(logo_url or ""), int(size), str(fallback_text or "")))

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = capture_team_logo
    plugin._draw_ncaa_standalone_panel(image, draw, (0, 0, 551, 267), card, "HUB LIVE", now)

    assert "NCAA LIVE" in seen_texts
    assert "\u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b" in seen_texts
    assert "\u5bc6\u6b47\u6839\u72fc\u737e" in seen_texts
    assert "COLLEGE DRIVE" in seen_texts
    assert "GAME INFO" in seen_texts
    assert "RANKED WATCH" not in seen_texts
    assert "No NCAA schedule" not in seen_texts
    assert "NEUTRAL / Kickoff Classic / AT&T Stadium" in seen_texts
    assert "NEUTRAL SITE  |  AT&T Stadium  |  TV ESPN / SPREAD TEX -6.5 / O/U 52.5" not in seen_texts
    assert ("https://example.com/ncaa-tex.png", 20, "TEX") in logo_calls
    assert ("https://example.com/ncaa-mich.png", 20, "MICH") in logo_calls


def test_ncaa_main_card_renders_full_program_names():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 16, 45, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    card = SportsDashboard._offseason_hub_card("NCAA", parsed, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert "\u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b" in seen_texts
    assert "\u5bc6\u6b47\u6839\u72fc\u737e" in seen_texts
    assert "POS \u5fb7\u5dde" in seen_texts


def test_ncaa_main_card_live_context_uses_college_drive_label():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 16, 45, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    card = SportsDashboard._offseason_hub_card("NCAA", parsed, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert "COLLEGE DRIVE" in seen_texts
    assert "2ND & 8" in seen_texts
    assert "MICH 36" in seen_texts
    assert "POS \u5fb7\u5dde" in seen_texts
    assert "NEUTRAL / Kickoff Classic / AT&T Stadium" in seen_texts
    assert "NEUTRAL SITE  |  AT&T Stadium  |  TV ESPN / SPREAD TEX -6.5 / O/U 52.5" not in seen_texts
    assert icon_calls[-3:] == ["DOWN", "DOWN", "FIELD"]


def test_ncaa_side_column_uses_chinese_ranked_matchup_for_scheduled_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 12, 0, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    next_event = dict(parsed["events"][0])
    next_event.update(
        {
            "state": "pre",
            "status_text": "Preview",
            "wins_a": None,
            "wins_b": None,
            "period": None,
            "clock": "",
            "down_distance": "",
            "yard_line": "",
            "possession": "",
        }
    )
    card = SportsDashboard._offseason_hub_card("NCAA", {"events": [next_event]}, now)
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert "RANKED WATCH" in seen_texts
    assert "#12 \u5fb7\u5dde VS #7 \u5bc6\u6b47\u6839" in seen_texts
    assert "#12 \u5fb7\u5dde 0-0 / #7 \u5bc6\u6b47\u6839 0-0" in seen_texts
    assert "NEUTRAL / Kickoff Classic / AT&T Stadium" in seen_texts


def test_ncaa_side_column_prioritizes_ranked_watch_over_soonest_unranked_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 12, 0, tzinfo=la)
    unranked_soon = {
        "event_id": "unranked-soon",
        "sport": "NCAA",
        "state": "pre",
        "start": now + timedelta(hours=1),
        "team_a": "Tulane",
        "team_b": "Rice",
        "team_a_code": "TULN",
        "team_b_code": "RICE",
    }
    ranked_late = {
        "event_id": "ranked-late",
        "sport": "NCAA",
        "state": "pre",
        "start": now + timedelta(hours=4),
        "team_a": "Georgia",
        "team_b": "Boise State",
        "team_a_code": "UGA",
        "team_b_code": "BSU",
        "team_a_rank": 3,
        "team_b_rank": 18,
        "neutral_site": True,
    }
    card = {
        "sport": "NCAA",
        "status": "NEXT",
        "main": unranked_soon,
        "upcoming": [unranked_soon, ranked_late],
        "recent": [],
    }
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    row_order = []

    def capture_small_row(_image, _draw, _x1, _x2, _y, event, _show_time):
        row_order.append(event.get("event_id"))

    plugin._draw_ncaa_small_row = capture_small_row
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert row_order[:2] == ["ranked-late", "unranked-soon"]


def test_ncaa_live_side_column_prioritizes_drive_before_ranked_watch():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 16, 45, tzinfo=la)
    live_event = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")["events"][0]
    ranked_late = {
        "event_id": "ranked-late",
        "sport": "NCAA",
        "state": "pre",
        "start": now + timedelta(hours=4),
        "team_a": "Georgia",
        "team_b": "Boise State",
        "team_a_code": "UGA",
        "team_b_code": "BSU",
        "team_a_rank": 3,
        "team_b_rank": 18,
        "neutral_site": True,
    }
    card = {"sport": "NCAA", "status": "LIVE", "main": live_event, "upcoming": [ranked_late], "recent": []}
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    headers = []
    row_order = []
    original_header = plugin._draw_hub_section_header

    def capture_header(draw_arg, x1, x2, y, title, accent):
        headers.append((str(title), int(y)))
        return original_header(draw_arg, x1, x2, y, title, accent)

    def capture_small_row(_image, _draw, _x1, _x2, _y, event, _show_time):
        row_order.append(event.get("event_id"))

    plugin._draw_hub_section_header = capture_header
    plugin._draw_ncaa_small_row = capture_small_row
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_side_column(image, draw, (0, 0, 220, 200), card, now)

    drive_y = next(y for title, y in headers if title == "COLLEGE DRIVE")
    ranked_y = next(y for title, y in headers if title == "RANKED WATCH")
    assert drive_y < ranked_y
    assert row_order == ["ranked-late"]


def test_ncaa_live_side_fallback_keeps_drive_and_game_info_separate():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 17, 0, tzinfo=la)
    card = SportsDashboard._offseason_hub_card(
        "NCAA",
        SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA"),
        now,
    )
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_ncaa_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert "COLLEGE DRIVE" in seen_texts
    assert "GAME INFO" in seen_texts
    assert "Q4 1:18" in seen_texts
    assert "2ND & 8" in seen_texts
    assert "MICH 36 / POS \u5fb7\u5dde" in seen_texts
    assert "ESPN / TEX -6.5 / O/U 52.5" in seen_texts
    assert "NEUTRAL / Kickoff Classic / AT&T Stadium" in seen_texts
    assert "#12 \u5fb7\u5dde 0-0 / #7 \u5bc6\u6b47\u6839 0-0" in seen_texts
    assert "KICK" not in seen_texts
    assert "MATCH" not in seen_texts
    assert icon_calls == ["QTR", "DOWN", "FIELD", "TV", "SITE", "RECORD"]


def test_ncaa_side_column_uses_final_snap_when_final_has_no_lists():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 30, 12, 0, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    final_event = dict(parsed["events"][0])
    final_event.update(
        {
            "state": "post",
            "status_text": "Final",
            "start": datetime(2026, 8, 29, 16, 30, tzinfo=la),
            "wins_a": 31,
            "wins_b": 28,
            "winner_a": True,
            "winner_b": False,
            "period": None,
            "clock": "",
            "down_distance": "",
            "yard_line": "",
            "possession": "",
        }
    )
    card = {"sport": "NCAA", "status": "RECENT", "main": final_event, "upcoming": [], "recent": []}
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_side_column(image, draw, (0, 0, 220, 200), card, now)

    assert "FINAL SNAP" in seen_texts
    assert "#12 \u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b \u80dc3\u5206" in seen_texts
    assert "#12 \u5fb7\u5dde 31 / #7 \u5bc6\u6b47\u6839 28" in seen_texts
    assert "NEUTRAL / Kickoff Classic / AT&T Stadium" in seen_texts
    assert "RANKED WATCH" not in seen_texts
    assert "No NCAA schedule" not in seen_texts
    assert "No recent results" not in seen_texts


def test_ncaa_side_column_keeps_final_snap_below_ranked_watch_when_upcoming_exists():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 30, 12, 0, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    final_event = dict(parsed["events"][0])
    final_event.update(
        {
            "state": "post",
            "status_text": "Final",
            "start": datetime(2026, 8, 29, 16, 30, tzinfo=la),
            "wins_a": 31,
            "wins_b": 28,
            "winner_a": True,
            "winner_b": False,
            "down_distance": "",
            "yard_line": "",
            "possession": "",
        }
    )
    ranked_late = {
        "event_id": "ranked-late",
        "sport": "NCAA",
        "state": "pre",
        "start": now + timedelta(hours=4),
        "team_a": "Georgia",
        "team_b": "Boise State",
        "team_a_code": "UGA",
        "team_b_code": "BSU",
        "team_a_rank": 3,
        "team_b_rank": 18,
        "neutral_site": True,
    }
    card = {"sport": "NCAA", "status": "RECENT", "main": final_event, "upcoming": [ranked_late], "recent": []}
    image = Image.new("RGB", (240, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    headers = []
    original_header = plugin._draw_hub_section_header

    def capture_header(draw_arg, x1, x2, y, title, accent):
        headers.append((str(title), int(y)))
        return original_header(draw_arg, x1, x2, y, title, accent)

    plugin._draw_hub_section_header = capture_header
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_side_column(image, draw, (0, 0, 220, 200), card, now)

    ranked_y = next(y for title, y in headers if title == "RANKED WATCH")
    final_y = next(y for title, y in headers if title == "FINAL SNAP")
    assert final_y > ranked_y


def test_ncaa_main_card_uses_neutral_site_pregame_context():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 12, 0, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    next_event = dict(parsed["events"][0])
    next_event.update(
        {
            "state": "pre",
            "status_text": "Preview",
            "score_a": "",
            "score_b": "",
            "period": None,
            "clock": "",
            "down_distance": "",
            "yard_line": "",
            "possession": "",
            "wins_a": None,
            "wins_b": None,
        }
    )
    card = SportsDashboard._offseason_hub_card("NCAA", {"events": [next_event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_main_card(image, draw, (0, 0, 320, 190), card, now, "NCAA")

    assert "TOP 25" in seen_texts
    assert "KICKOFF" in seen_texts
    assert "08/29 4:30 PM" in seen_texts
    assert "NEUTRAL / Kickoff Classic" in seen_texts
    assert "AT&T Stadium  |  O/U 52.5" in seen_texts
    assert "NEUTRAL SITE" not in seen_texts
    assert icon_calls[-1] == "KICK"


def test_ncaa_main_card_live_drive_uses_down_and_field_chips():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 16, 45, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    card = SportsDashboard._offseason_hub_card("NCAA", parsed, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert "NCAA LIVE" in seen_texts
    assert "TOP 25" in seen_texts
    assert "COLLEGE DRIVE" in seen_texts
    assert "DOWN" in seen_texts
    assert "2ND & 8" in seen_texts
    assert "FIELD" in seen_texts
    assert "MICH 36" in seen_texts
    assert "POS \u5fb7\u5dde" in seen_texts
    assert "NEUTRAL / Kickoff Classic / AT&T Stadium" in seen_texts
    assert "NEUTRAL SITE  |  AT&T Stadium  |  TV ESPN / SPREAD TEX -6.5 / O/U 52.5" not in seen_texts
    assert icon_calls[-3:] == ["DOWN", "DOWN", "FIELD"]


def test_ncaa_main_card_live_drive_draws_last_play_strip_when_available():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 16, 45, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    event = dict(parsed["events"][0])
    event["last_play"] = "Arch Manning pass complete to Ryan Wingo for 12 yards"
    card = SportsDashboard._offseason_hub_card("NCAA", {"events": [event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert "COLLEGE DRIVE" in seen_texts
    assert "PLAY" in seen_texts
    assert "Arch Manning pass complete to Ryan Wingo for 12 yards" in seen_texts
    assert icon_calls[-4:] == ["DOWN", "DOWN", "FIELD", "PLAY"]


def test_ncaa_main_card_live_drive_falls_back_without_field_position():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 16, 45, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    event = dict(parsed["events"][0])
    event.update({"down_distance": "", "yard_line": "", "note": ""})
    card = SportsDashboard._offseason_hub_card("NCAA", {"events": [event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert "COLLEGE DRIVE" in seen_texts
    assert "LIVE DRIVE" in seen_texts
    assert "FIELD" not in seen_texts
    assert icon_calls[-2:] == ["DOWN", "DOWN"]


def test_ncaa_live_drive_chips_use_last_play_when_position_missing():
    plugin = _plugin()
    image = Image.new("RGB", (180, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    chips = []

    def capture_chip(_draw, _box, label, value, accent):
        chips.append((label, value, accent))

    plugin._draw_ncaa_live_drive_chip = capture_chip
    plugin._draw_ncaa_live_drive_chips(
        draw,
        0,
        0,
        160,
        {"down_distance": "", "yard_line": "", "note": "", "last_play": "Arch Manning pass complete"},
    )

    assert chips == [("PLAY", "LAST PLAY", COLORS["amber"])]


def test_football_main_card_uses_ncaa_tag_token_for_ncaa_fallback():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 12, 0, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    next_event = dict(parsed["events"][0])
    next_event.update({"state": "pre", "score_a": "", "score_b": ""})
    card = SportsDashboard._offseason_hub_card("NCAA", {"events": [next_event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_main_card(image, draw, (0, 0, 320, 190), card, now, "NCAA")

    assert image.getpixel((20, 20)) == COLORS["ncaa_tag"]


def test_ncaa_main_card_uses_own_field_tint_for_pregame_context_box():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 29, 12, 0, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    next_event = dict(parsed["events"][0])
    next_event.update(
        {
            "state": "pre",
            "status_text": "Preview",
            "score_a": "",
            "score_b": "",
            "period": None,
            "clock": "",
            "down_distance": "",
            "yard_line": "",
            "possession": "",
            "wins_a": None,
            "wins_b": None,
        }
    )
    card = SportsDashboard._offseason_hub_card("NCAA", {"events": [next_event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_ncaa_main_card(image, draw, (0, 0, 320, 190), card, now)

    assert COLORS["ncaa_field_tint"] != COLORS["panel_blue"]
    assert image.getpixel((20, 145)) == COLORS["ncaa_field_tint"]


def test_ncaa_main_card_uses_neutral_site_final_context():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 30, 12, 0, tzinfo=la)
    parsed = SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), la, "NCAA")
    final_event = dict(parsed["events"][0])
    final_event.update(
        {
            "state": "post",
            "status_text": "Final",
            "start": datetime(2026, 8, 29, 16, 30, tzinfo=la),
            "wins_a": 31,
            "wins_b": 28,
            "period": None,
            "clock": "",
            "down_distance": "",
            "yard_line": "",
            "possession": "",
        }
    )
    card = SportsDashboard._offseason_hub_card("NCAA", {"events": [final_event]}, now)
    image = Image.new("RGB", (340, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_main_card(image, draw, (0, 0, 320, 190), card, now, "NCAA")

    assert "NCAA RECENT" in seen_texts
    assert "FINAL" in seen_texts
    assert "NEUTRAL / Kickoff Classic" in seen_texts
    assert "NEUTRAL SITE  |  AT&T Stadium  |  TV ESPN / SPREAD TEX -6.5 / O/U 52.5" in seen_texts
    assert "SCHEDULED" not in seen_texts
    assert icon_calls[-1] == "SCORE"


def test_wnba_side_row_draws_live_status_context():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)["events"][0]
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_wnba_small_row(image, draw, 10, 230, 8, event, True)

    assert "Q3 4:22 / \u738b\u724c +6" in seen_texts


def test_wnba_side_row_draws_period_chips_for_live_quarter():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = dict(SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)["events"][0])
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    chip_calls = []

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_period_chips = lambda _draw, x, y, period: chip_calls.append((x, y, period))

    plugin._draw_wnba_small_row(image, draw, 10, 230, 8, event, True)

    assert chip_calls == [(68, 24, 3)]

    chip_calls.clear()
    event.update({"state": "pre", "period": 0, "wins_a": None, "wins_b": None})
    plugin._draw_wnba_small_row(image, draw, 10, 230, 8, event, True)

    assert chip_calls == []


def test_wnba_side_row_draws_lead_chip_for_live_margin():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = dict(SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)["events"][0])
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    lead_chips = []

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_wnba_lead_chip = lambda _draw, x, y, label: lead_chips.append((x, y, label))

    plugin._draw_wnba_small_row(image, draw, 10, 230, 8, event, True)

    assert SportsDashboard._wnba_lead_chip_label(event) == "+6"
    assert lead_chips == [(96, 24, "+6")]

    lead_chips.clear()
    event.update({"wins_a": 78, "wins_b": 78})
    plugin._draw_wnba_small_row(image, draw, 10, 230, 8, event, True)

    assert SportsDashboard._wnba_lead_chip_label(event) == ""
    assert lead_chips == []


def test_wnba_side_row_draws_scheduled_tv_and_venue_context():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)["events"][0]
    event = dict(event)
    event.update(
        {
            "state": "pre",
            "status_text": "Preview",
            "wins_a": None,
            "wins_b": None,
            "period_scores_a": [],
            "period_scores_b": [],
            "spread": "",
            "over_under": "",
        }
    )
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_wnba_small_row(image, draw, 10, 230, 8, event, True)

    assert "ION / Michelob ULTRA Arena" in seen_texts
    assert "Preview" not in seen_texts


def test_wnba_live_side_fallback_preserves_tv_line_and_venue_context():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_wnba_scoreboard_payload()))
    payload["events"][0]["competitions"][0]["odds"] = [{"details": "LV -4.5", "overUnder": 166.5}]
    event = SportsDashboard._parse_wnba_scoreboard(payload, la)["events"][0]
    image = Image.new("RGB", (250, 190), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_wnba_live_side_fallback(image, draw, 10, 8, 230, 188, event)

    assert "TV" in seen_texts
    assert "ION / LV -4.5 / O/U 166.5" in seen_texts
    assert "LINE" not in seen_texts
    assert "VENUE" in seen_texts
    assert "Michelob ULTRA Arena" in seen_texts
    assert "QUARTER LOG" in seen_texts
    assert icon_calls[:4] == ["CLOCK", "LEAD", "TV", "VENUE"]


def test_wnba_live_side_fallback_uses_spread_icon_without_broadcast():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_wnba_scoreboard_payload()))
    payload["events"][0]["competitions"][0]["odds"] = [{"details": "LV -4.5", "overUnder": 166.5}]
    event = SportsDashboard._parse_wnba_scoreboard(payload, la)["events"][0]
    event = dict(event)
    event["broadcast"] = ""
    image = Image.new("RGB", (250, 190), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_wnba_live_side_fallback(image, draw, 10, 8, 230, 188, event)

    assert "TV" not in seen_texts
    assert "SPREAD" in seen_texts
    assert "LV -4.5 / O/U 166.5" in seen_texts
    assert "LINE" not in seen_texts
    assert "VENUE" in seen_texts
    assert "Michelob ULTRA Arena" in seen_texts
    assert icon_calls[:4] == ["CLOCK", "LEAD", "SPREAD", "VENUE"]


def test_wnba_live_side_fallback_omits_placeholder_lead_without_scores():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_wnba_scoreboard_payload()))
    payload["events"][0]["competitions"][0]["odds"] = [{"details": "LV -4.5", "overUnder": 166.5}]
    event = SportsDashboard._parse_wnba_scoreboard(payload, la)["events"][0]
    event = dict(event)
    event.update({"wins_a": None, "wins_b": None})
    image = Image.new("RGB", (250, 190), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_wnba_live_side_fallback(image, draw, 10, 8, 230, 188, event)

    assert "LEAD" not in icon_calls
    assert "TBD" not in seen_texts
    assert "TV" in seen_texts
    assert "ION / LV -4.5 / O/U 166.5" in seen_texts
    assert "VENUE" in seen_texts
    assert "Michelob ULTRA Arena" in seen_texts
    assert icon_calls[:3] == ["CLOCK", "TV", "VENUE"]


def test_wnba_result_side_fallback_preserves_tv_line_and_venue_context():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_wnba_scoreboard_payload()))
    payload["events"][0]["competitions"][0]["odds"] = [{"details": "LV -4.5", "overUnder": 166.5}]
    event = SportsDashboard._parse_wnba_scoreboard(payload, la)["events"][0]
    event = dict(event)
    event.update({"state": "post", "status_text": "Final"})
    image = Image.new("RGB", (250, 190), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    icon_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_sport_info_icon = lambda _draw, kind, _x, _y, _accent: icon_calls.append(str(kind))
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_wnba_result_side_fallback(draw, 10, 8, 230, 188, event)

    assert "RESULT SNAP" in seen_texts
    assert "INFO" not in seen_texts
    assert "TV" in seen_texts
    assert "ION / LV -4.5 / O/U 166.5" in seen_texts
    assert "VENUE" in seen_texts
    assert "Michelob ULTRA Arena" in seen_texts
    assert "QUARTER LOG" in seen_texts
    assert icon_calls[:4] == ["WIN", "SCORE", "TV", "VENUE"]


def test_wnba_small_note_label_falls_back_to_status_without_score():
    event = {
        "state": "in",
        "status_text": "Q2 8:10",
        "team_a": "SEA",
        "team_b": "LV",
        "wins_a": None,
        "wins_b": None,
    }

    assert SportsDashboard._wnba_small_note_label(event) == "Q2 8:10"


def test_wnba_small_note_label_prioritizes_final_status_over_pregame_lines():
    event = {
        "state": "post",
        "status_text": "Final",
        "venue": "Barclays Center",
        "broadcast": "ESPN",
        "spread": "NY -4.5",
        "over_under": "O/U 166.5",
    }

    assert SportsDashboard._wnba_small_note_label(event) == "Final / Barclays Center"


def test_wnba_side_row_uses_team_code_logo_fallbacks_next_to_names():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), la)["events"][0]
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    logo_calls = []

    def capture_team_logo(_image, _draw, logo_url, x, y, size, fallback_text):
        logo_calls.append(
            {
                "url": str(logo_url or ""),
                "x": int(x),
                "size": int(size),
                "fallback": str(fallback_text or ""),
            }
        )

    plugin._draw_team_logo = capture_team_logo

    plugin._draw_wnba_small_row(image, draw, 10, 230, 8, event, True)

    assert [call["fallback"] for call in logo_calls] == ["SEA", "LV"]
    assert logo_calls[0]["x"] >= 66
    assert logo_calls[0]["x"] < logo_calls[1]["x"]


def test_mlb_side_row_draws_live_inning_and_count_context():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)["events"][0]
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_mlb_small_row(
        image,
        draw,
        10,
        230,
        8,
        event,
        datetime(2026, 6, 14, 13, 30, tzinfo=la),
        True,
    )

    assert "TOP 7th / 1B 3B / 1 OUT" in seen_texts


def test_mlb_side_row_draws_mini_base_diamond_for_live_runners():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = dict(SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)["events"][0])
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    base_icons = []

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_mini_base_diamond = lambda _draw, x, y, bases: base_icons.append((x, y, bases))

    plugin._draw_mlb_small_row(
        image,
        draw,
        10,
        230,
        8,
        event,
        datetime(2026, 6, 14, 13, 30, tzinfo=la),
        True,
    )

    assert base_icons == [(68, 17, "13")]

    base_icons.clear()
    event["bases"] = ""
    plugin._draw_mlb_small_row(
        image,
        draw,
        10,
        230,
        8,
        event,
        datetime(2026, 6, 14, 13, 30, tzinfo=la),
        True,
    )

    assert base_icons == []


def test_mlb_side_row_draws_count_chip_for_live_count_and_outs():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = dict(SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)["events"][0])
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    count_chips = []

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_mlb_mini_base_diamond = lambda *_args, **_kwargs: None
    plugin._draw_mlb_count_chip = lambda _draw, x, y, label, outs: count_chips.append((x, y, label, outs))

    plugin._draw_mlb_small_row(
        image,
        draw,
        10,
        230,
        8,
        event,
        datetime(2026, 6, 14, 13, 30, tzinfo=la),
        True,
    )

    assert SportsDashboard._mlb_count_chip_label(event) == "2-1"
    assert count_chips == [(94, 24, "2-1", 1)]

    count_chips.clear()
    event.update({"state": "pre", "balls": 2, "strikes": 1, "outs": 1})
    plugin._draw_mlb_small_row(
        image,
        draw,
        10,
        230,
        8,
        event,
        datetime(2026, 6, 14, 13, 30, tzinfo=la),
        True,
    )

    assert SportsDashboard._mlb_count_chip_label(event) == ""
    assert count_chips == []


def test_mlb_small_note_falls_back_to_count_when_no_runners_are_on_base():
    la = ZoneInfo("America/Los_Angeles")
    event = dict(SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la)["events"][0])
    event["bases"] = ""

    assert SportsDashboard._mlb_small_note_label(event) == "TOP 7th / 1 OUT / B-S 2-1"
    assert SportsDashboard._mlb_count_label(event) == "B-S 2-1 OUT 1"
    assert SportsDashboard._mlb_count_chip_label(event) == "2-1"


def test_mlb_side_row_uses_chinese_names_with_code_logo_fallback():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    event = {
        "sport": "MLB",
        "state": "pre",
        "start": now + timedelta(hours=2),
        "team_a": "LAD",
        "team_b": "SF",
        "team_a_code": "LAD",
        "team_b_code": "SF",
        "team_a_logo": "https://example.com/mlb-lad.png",
        "team_b_logo": "https://example.com/mlb-sf.png",
    }
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    logo_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def capture_team_logo(_image, _draw, logo_url, _x, _y, size, fallback_text):
        logo_calls.append((str(logo_url or ""), int(size), str(fallback_text or "")))

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = capture_team_logo

    plugin._draw_mlb_small_row(image, draw, 10, 230, 8, event, now, show_time=True)

    assert "\u9053\u5947 VS \u5de8\u4eba" in seen_texts
    assert ("https://example.com/mlb-lad.png", 11, "LAD") in logo_calls
    assert ("https://example.com/mlb-sf.png", 11, "SF") in logo_calls


def test_mlb_main_card_uses_chinese_names_with_code_logo_fallback():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 13, 30, tzinfo=la)
    event = {
        "sport": "MLB",
        "state": "pre",
        "start": now + timedelta(hours=2),
        "status_text": "Scheduled",
        "team_a": "LAD",
        "team_b": "SF",
        "team_a_code": "LAD",
        "team_b_code": "SF",
        "team_a_logo": "https://example.com/mlb-lad.png",
        "team_b_logo": "https://example.com/mlb-sf.png",
        "record_a": "42-28",
        "record_b": "34-35",
        "probable_a": "T. Glasnow",
        "probable_b": "L. Webb",
        "venue": "Oracle Park",
    }
    card = {"sport": "MLB", "status": "NEXT", "main": event, "upcoming": [event], "recent": []}
    image = Image.new("RGB", (320, 210), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    logo_calls = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    def capture_team_logo(_image, _draw, logo_url, _x, _y, size, fallback_text):
        logo_calls.append((str(logo_url or ""), int(size), str(fallback_text or "")))

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = capture_team_logo
    plugin._draw_mlb_main_card(image, draw, (0, 0, 300, 190), card, now)

    assert "\u6d1b\u6749\u77f6\u9053\u5947" in seen_texts
    assert "\u65e7\u91d1\u5c71\u5de8\u4eba" in seen_texts
    assert ("https://example.com/mlb-lad.png", 20, "LAD") in logo_calls
    assert ("https://example.com/mlb-sf.png", 20, "SF") in logo_calls


def test_hub_team_score_right_logo_stays_before_team_name():
    plugin = _plugin()
    image = Image.new("RGB", (240, 90), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    logo_calls = []
    right_aligned_calls = []
    original_right_aligned = plugin._draw_right_aligned

    def capture_team_logo(_image, _draw, logo_url, x, y, size, fallback_text):
        logo_calls.append(
            {
                "url": str(logo_url or ""),
                "x": int(x),
                "y": int(y),
                "size": int(size),
                "fallback": str(fallback_text or ""),
            }
        )

    def capture_right_aligned(draw_obj, xy, text, font, color):
        right_aligned_calls.append((xy, str(text), font))
        return original_right_aligned(draw_obj, xy, text, font, color)

    plugin._draw_team_logo = capture_team_logo
    plugin._draw_right_aligned = capture_right_aligned

    plugin._draw_hub_team_score(
        draw,
        20,
        12,
        210,
        "Liberty",
        82,
        "12-3",
        align="right",
        image=image,
        logo_url="https://example.com/wnba-ny.png",
        logo_size=20,
        logo_fallback="NY",
    )

    team_xy, team_text, team_font = next(call for call in right_aligned_calls if call[1] == "Liberty")
    team_left = int(team_xy[0] - SportsDashboard._text_width(draw, team_text, team_font))
    logo = logo_calls[0]
    logo_gap = team_left - (logo["x"] + logo["size"])

    assert logo["fallback"] == "NY"
    assert logo["x"] < team_left
    assert 0 <= logo_gap <= 5


def test_football_side_row_draws_live_drive_context_with_time():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")["events"][0]
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_football_small_row(image, draw, 10, 230, 8, event, True, "NFL")

    assert "Q2 8:42 / 3RD & 4 / SEA 42 / POS \u6d77\u9e70" in seen_texts


def test_football_side_row_draws_mini_field_marker_for_live_drive():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = dict(SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")["events"][0])
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    field_markers = []

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_mini_field_marker = lambda _draw, x, y, width, sport, row_event: field_markers.append(
        (x, y, width, sport, row_event.get("yard_line"))
    )

    plugin._draw_football_small_row(image, draw, 10, 230, 8, event, True, "NFL")

    assert field_markers == [(68, 24, 24, "NFL", "SEA 42")]

    field_markers.clear()
    event.update({"state": "pre", "yard_line": "", "possession": "", "down_distance": ""})
    plugin._draw_football_small_row(image, draw, 10, 230, 8, event, True, "NFL")

    assert field_markers == []


def test_football_side_row_draws_down_chip_for_live_drive():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = dict(SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")["events"][0])
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    down_chips = []

    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    plugin._draw_football_mini_field_marker = lambda *_args, **_kwargs: None
    plugin._draw_football_down_chip = lambda _draw, x, y, label, row_event, sport: down_chips.append(
        (x, y, label, row_event.get("down_distance"), sport)
    )

    plugin._draw_football_small_row(image, draw, 10, 230, 8, event, True, "NFL")

    assert SportsDashboard._football_down_number(event) == 3
    assert SportsDashboard._football_down_chip_label(event) == "3&4"
    assert down_chips == [(96, 24, "3&4", "3RD & 4", "NFL")]

    down_chips.clear()
    event.update({"state": "pre", "yard_line": "SEA 42", "possession": "SEA", "down_distance": "3RD & 4"})
    plugin._draw_football_small_row(image, draw, 10, 230, 8, event, True, "NFL")

    assert SportsDashboard._football_down_chip_label(event) == ""
    assert down_chips == []


def test_football_side_row_keeps_broadcast_context_for_scheduled_game():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    parsed = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")
    event = next(item for item in parsed["events"] if item["event_id"] == "nfl-next")
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_football_small_row(image, draw, 10, 230, 8, event, True, "NFL")

    assert "FOX / CHI -1.5 / O/U 42.5" in seen_texts


def test_football_side_row_left_logo_stays_next_to_left_team_name():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = SportsDashboard._parse_football_scoreboard(_sample_nfl_scoreboard_payload(), la, "NFL")["events"][0]
    image = Image.new("RGB", (250, 40), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    logo_calls = []
    right_aligned_calls = []
    original_right_aligned = plugin._draw_right_aligned

    def capture_team_logo(_image, _draw, logo_url, x, y, size, fallback_text):
        logo_calls.append(
            {
                "url": str(logo_url or ""),
                "x": int(x),
                "y": int(y),
                "size": int(size),
                "fallback": str(fallback_text or ""),
            }
        )

    def capture_right_aligned(draw_obj, xy, text, font, color):
        right_aligned_calls.append((xy, str(text), font))
        return original_right_aligned(draw_obj, xy, text, font, color)

    plugin._draw_team_logo = capture_team_logo
    plugin._draw_right_aligned = capture_right_aligned

    plugin._draw_football_small_row(image, draw, 10, 230, 8, event, True, "NFL")

    matchup_xy, matchup_text, matchup_font = next(
        call for call in right_aligned_calls if call[1] == "\u6d77\u9e70 17-14 \u7231\u56fd\u8005"
    )
    matchup_left = int(matchup_xy[0] - SportsDashboard._text_width(draw, matchup_text, matchup_font))
    left_logo = next(call for call in logo_calls if call["fallback"] == "SEA")
    logo_gap = matchup_left - (left_logo["x"] + left_logo["size"])

    assert left_logo["x"] >= 66
    assert 0 <= logo_gap <= 5


def test_select_f1_events_tracks_live_race_weekend():
    la = ZoneInfo("America/Los_Angeles")
    data = SportsDashboard._parse_f1_jolpica_bundle(_sample_f1_jolpica_bundle(), la)

    selected = SportsDashboard._select_f1_events(data, datetime(2026, 6, 14, 6, 45, tzinfo=la))

    assert selected["status"] == "LIVE"
    assert selected["live_session"]["label"] == "RACE"
    assert selected["main_race"]["race_name"] == "Barcelona-Catalunya Grand Prix"
    assert selected["next_race"]["race_name"] == "Austrian Grand Prix"


def test_f1_openf1_snapshot_adds_live_leaderboard_and_weather():
    parsed = SportsDashboard._parse_f1_openf1_snapshot(_sample_openf1_snapshot())

    assert parsed["leaderboard"][0]["driver_code"] == "RUS"
    assert parsed["leaderboard"][1]["gap"] == "1.204"
    assert parsed["weather"]["track"] == 39.2


def test_sports_dashboard_uses_offseason_hub_during_nba_offseason(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (800, 480), COLORS["paper"])
    la = ZoneInfo("America/Los_Angeles")
    calls = []
    now = datetime(2026, 6, 14, 9, 0, tzinfo=la)
    hub_selected = SportsDashboard._select_offseason_hub(
        {
            "mlb": SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), la),
            "wnba": {"events": []},
            "pga": {"events": []},
        },
        now,
    )

    monkeypatch.setattr(plugin, "_try_worldcup_football_data_panel", lambda *args, **kwargs: Image.new("RGB", args[2], COLORS["panel"]))
    monkeypatch.setattr(plugin, "_try_worldcup_api_panel", lambda *args, **kwargs: None)
    monkeypatch.setattr(plugin, "_try_worldcup_scoreboard_panel", lambda *args, **kwargs: None)
    monkeypatch.setattr(plugin, "_prepare_worldcup_panel", lambda panel, dimensions, visible: (panel, (0, 0, dimensions[0], dimensions[1])))
    monkeypatch.setattr(plugin, "_load_nba_events", lambda *_args, **_kwargs: (SportsDashboard._fallback_nba_events(la), "NBA FALLBACK"))
    monkeypatch.setattr(plugin, "_attach_nba_odds", lambda events, *_args, **_kwargs: events)
    monkeypatch.setattr(plugin, "_write_nba_live_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_load_offseason_hub", lambda *_args, **_kwargs: (hub_selected, "HUB LIVE"))
    monkeypatch.setattr(plugin, "_write_offseason_hub_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_draw_offseason_hub_compact_panel", lambda *_args, **_kwargs: calls.append("hub"))
    monkeypatch.setattr(plugin, "_draw_nba_compact_panel", lambda *_args, **_kwargs: calls.append("nba"))
    monkeypatch.setattr(plugin, "_load_lpl_events", lambda *_args, **_kwargs: ([], "CACHE DATA"))
    monkeypatch.setattr(plugin, "_attach_lpl_odds", lambda events, *_args, **_kwargs: events)
    monkeypatch.setattr(plugin, "_attach_lpl_realtime_info", lambda selected, settings, **_kwargs: selected)
    monkeypatch.setattr(plugin, "_write_lpl_live_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_draw_lpl_sidebar", lambda *_args, **_kwargs: None)

    result = plugin._generate_image_with_active_colors(
        {},
        FakeDeviceConfig(),
        image.size,
        la,
        now,
    )

    assert result.size == image.size
    assert calls == ["hub"]


def test_f1_compact_panel_draws_core_labels():
    plugin = _plugin()
    image = Image.new("RGB", (560, 220), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    data = SportsDashboard._parse_f1_jolpica_bundle(_sample_f1_jolpica_bundle(), la)
    selected = SportsDashboard._select_f1_events(data, datetime(2026, 6, 14, 6, 45, tzinfo=la))
    selected["leaderboard"] = SportsDashboard._parse_f1_openf1_snapshot(_sample_openf1_snapshot())["leaderboard"]
    seen_texts = []
    original_fit_text = plugin._fit_text

    def record_fit_text(draw_arg, text, max_width, size, bold=False, min_size=11):
        seen_texts.append(str(text))
        return original_fit_text(draw_arg, text, max_width, size, bold=bold, min_size=min_size)

    plugin._fit_text = record_fit_text

    plugin._draw_f1_compact_panel(
        image,
        draw,
        (0, 0, 559, 219),
        selected,
        "JOLPICA LIVE",
        datetime(2026, 6, 14, 6, 45, tzinfo=la),
    )

    assert "FORMULA 1" in seen_texts
    assert "Barcelona-Catalunya Grand Prix" in seen_texts
    assert "\u6bd4\u8d5b\u4e2d" in seen_texts
    assert "RACE LIVE" in seen_texts


def test_f1_logo_draws_uploaded_asset_without_border():
    plugin = _plugin()
    background = (31, 47, 63)
    image = Image.new("RGB", (120, 70), background)
    draw = ImageDraw.Draw(image)

    plugin._draw_f1_logo(image, draw, 10, 10, 74, 34)

    assert image.getpixel((10, 10)) == background
    assert image.getpixel((83, 43)) == background


def test_f1_main_card_has_no_generated_track_art_method():
    assert not hasattr(SportsDashboard, "_draw_f1_card_track_art")


def test_f1_side_column_keeps_live_timing_below_session_rows(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (560, 220), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    data = SportsDashboard._parse_f1_jolpica_bundle(_sample_f1_jolpica_bundle(), la)
    selected = SportsDashboard._select_f1_events(data, datetime(2026, 6, 14, 6, 45, tzinfo=la))
    selected["leaderboard"] = SportsDashboard._parse_f1_openf1_snapshot(_sample_openf1_snapshot())["leaderboard"]
    headers = []
    session_rows = []
    leaderboard_rows = []

    monkeypatch.setattr(plugin, "_draw_f1_mini_section_header", lambda _draw, _x1, _x2, y, title: headers.append((title, y)))
    monkeypatch.setattr(plugin, "_draw_f1_session_row", lambda _draw, _x1, _x2, y, session, _now: session_rows.append((session["label"], y)))
    monkeypatch.setattr(plugin, "_draw_f1_leaderboard_row", lambda _draw, _x1, _x2, y, row: leaderboard_rows.append((row["driver_code"], y)))

    plugin._draw_f1_side_column(draw, 306, 548, 58, 211, selected, datetime(2026, 6, 14, 6, 45, tzinfo=la))

    assert len(session_rows) == 2
    live_header_y = next(y for title, y in headers if title == "LIVE TIMING")
    assert live_header_y >= max(y for _label, y in session_rows) + 38
    assert leaderboard_rows
    assert min(y for _code, y in leaderboard_rows) >= live_header_y + 25


def test_nba_espn_parser_uses_chinese_team_names_and_period_scores():
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)

    assert events[0]["team_a"] == "\u9a6c\u523a"
    assert events[0]["team_b"] == "\u5c3c\u514b\u65af"
    assert events[0]["team_a_code"] == "SA"
    assert events[0]["wins_a"] == 106
    assert events[0]["wins_b"] == 112
    assert events[0]["series_wins_a"] == 0
    assert events[0]["series_wins_b"] == 2
    assert events[0]["period_scores_a"] == [25, 29, 24, 28]
    assert SportsDashboard._nba_period_label(events[0]) == "Q1 25-28  Q2 29-27  Q3 24-31  Q4 28-26"


def test_nba_parser_captures_espn_winner_flags_by_display_side():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_nba_scoreboard_payload()))
    competition = payload["events"][0]["competitions"][0]
    competition["competitors"][0]["winner"] = True
    competition["competitors"][1]["winner"] = False

    event = SportsDashboard._parse_nba_espn_events(payload, la)[0]

    assert event["team_a_code"] == "SA"
    assert event["team_b_code"] == "NY"
    assert event["winner_a"] is False
    assert event["winner_b"] is True


def test_nba_team_aliases_normalize_short_names_to_chinese_codes():
    la = ZoneInfo("America/Los_Angeles")
    payload = {
        "events": [
            {
                "id": "401000777",
                "date": "2026-10-21T02:00Z",
                "competitions": [
                    {
                        "id": "401000777",
                        "date": "2026-10-21T02:00Z",
                        "status": {
                            "period": 0,
                            "displayClock": "",
                            "type": {"state": "pre", "completed": False, "description": "Scheduled"},
                        },
                        "competitors": [
                            {
                                "homeAway": "away",
                                "team": {
                                    "shortDisplayName": "Lakers",
                                    "displayName": "Los Angeles Lakers",
                                    "logo": "https://example.com/lal.png",
                                },
                            },
                            {
                                "homeAway": "home",
                                "team": {
                                    "shortDisplayName": "Knicks",
                                    "displayName": "New York Knicks",
                                    "logo": "https://example.com/nyk.png",
                                },
                            },
                        ],
                    }
                ],
            }
        ]
    }

    event = SportsDashboard._parse_nba_espn_events(payload, la)[0]

    assert event["team_a_code"] == "LAL"
    assert event["team_a"] == "\u6e56\u4eba"
    assert event["team_a_logo"] == "https://example.com/lal.png"
    assert event["team_b_code"] == "NYK"
    assert event["team_b"] == "\u5c3c\u514b\u65af"
    assert "Los Angeles Lakers" in event["team_a_source_aliases"]


def test_nba_full_team_names_for_main_cards():
    assert SportsDashboard._nba_display_team_name("LAL", "Lakers", full=True) == "\u6d1b\u6749\u77f6\u6e56\u4eba"
    assert (
        SportsDashboard._nba_display_team_name(
            "",
            "Los Angeles Lakers",
            ["Los Angeles Lakers", "Lakers"],
            full=True,
        )
        == "\u6d1b\u6749\u77f6\u6e56\u4eba"
    )
    assert (
        SportsDashboard._nba_display_team_from_event(
            {
                "team_a": "\u6e56\u4eba",
                "team_a_code": "LAL",
                "team_a_name": "Lakers",
            },
            "a",
            full=True,
        )
        == "\u6d1b\u6749\u77f6\u6e56\u4eba"
    )
    assert (
        SportsDashboard._nba_display_team_from_event(
            {
                "team_b": "PHX",
                "team_b_code": "NYK",
                "team_b_name": "Suns",
            },
            "b",
            full=True,
        )
        == "\u83f2\u5c3c\u514b\u65af\u592a\u9633"
    )


def test_nba_winner_side_prefers_espn_flag_then_score():
    flag_event = {
        "state": "post",
        "winner_a": False,
        "winner_b": True,
        "wins_a": 120,
        "wins_b": 118,
    }
    score_event = {
        "state": "completed",
        "winner_a": None,
        "winner_b": None,
        "wins_a": 101,
        "wins_b": 109,
    }
    live_event = {
        "state": "in",
        "winner_a": True,
        "winner_b": False,
        "wins_a": 88,
        "wins_b": 80,
    }

    assert SportsDashboard._nba_winner_side(flag_event) == "b"
    assert SportsDashboard._nba_team_side_fill_key(flag_event, "a") == "text"
    assert SportsDashboard._nba_team_side_fill_key(flag_event, "b") == "nba_accent"
    assert SportsDashboard._nba_winner_side(score_event) == "b"
    assert SportsDashboard._nba_winner_side(live_event) == ""


def test_select_nba_events_returns_next_upcoming_and_recent_result():
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)
    now = datetime(2026, 6, 6, 12, 0, tzinfo=la)

    selected = SportsDashboard._select_nba_events(events, now)

    assert selected["main"]["state"] == "unstarted"
    assert selected["upcoming"][0]["team_a"] == "\u9a6c\u523a"
    assert selected["recent"][0]["team_b"] == "\u5c3c\u514b\u65af"
    assert SportsDashboard._nba_score_label(selected["recent"][0]) == "106-112"


def test_select_nba_events_filters_decided_finals_placeholders_and_marks_offseason():
    la = ZoneInfo("America/Los_Angeles")
    final = {
        "start": datetime(2026, 6, 13, 17, 30, tzinfo=la),
        "state": "completed",
        "team_a": "\u5c3c\u514b\u65af",
        "team_b": "\u9a6c\u523a",
        "wins_a": 94,
        "wins_b": 90,
        "series_wins_a": 4,
        "series_wins_b": 1,
        "block": "POSTSEASON",
    }
    if_necessary = {
        "start": datetime(2026, 6, 16, 17, 30, tzinfo=la),
        "state": "unstarted",
        "team_a": "\u9a6c\u523a",
        "team_b": "\u5c3c\u514b\u65af",
        "series_wins_a": 1,
        "series_wins_b": 4,
        "block": "POSTSEASON",
    }

    selected = SportsDashboard._select_nba_events(
        [final, if_necessary],
        datetime(2026, 6, 13, 21, 0, tzinfo=la),
    )

    assert selected["upcoming"] == []
    assert selected["main"] is final
    assert selected["offseason"] is True
    assert selected["next_season_event"] is None


def test_select_nba_events_keeps_distant_next_season_opener_as_offseason_target():
    la = ZoneInfo("America/Los_Angeles")
    final = {
        "start": datetime(2026, 6, 13, 17, 30, tzinfo=la),
        "state": "completed",
        "team_a": "\u5c3c\u514b\u65af",
        "team_b": "\u9a6c\u523a",
        "wins_a": 94,
        "wins_b": 90,
        "series_wins_a": 4,
        "series_wins_b": 1,
        "block": "POSTSEASON",
    }
    opener = {
        "start": datetime(2026, 10, 20, 17, 0, tzinfo=la),
        "state": "unstarted",
        "team_a": "TBD",
        "team_b": "TBD",
        "series_wins_a": None,
        "series_wins_b": None,
        "block": "REGULAR SEASON",
    }

    selected = SportsDashboard._select_nba_events(
        [final, opener],
        datetime(2026, 6, 14, 9, 0, tzinfo=la),
    )

    assert selected["upcoming"] == [opener]
    assert selected["next_season_event"] is opener
    assert selected["offseason"] is True


def test_select_nba_events_marks_late_summer_distant_opener_as_offseason_without_recent():
    la = ZoneInfo("America/Los_Angeles")
    opener = {
        "start": datetime(2026, 10, 20, 17, 0, tzinfo=la),
        "state": "unstarted",
        "team_a": "TBD",
        "team_b": "TBD",
        "series_wins_a": None,
        "series_wins_b": None,
        "block": "REGULAR SEASON",
    }

    selected = SportsDashboard._select_nba_events(
        [opener],
        datetime(2026, 8, 15, 9, 0, tzinfo=la),
    )

    assert selected["upcoming"] == [opener]
    assert selected["recent"] == []
    assert selected["offseason"] is True


def test_nba_parser_propagates_latest_series_score_to_upcoming_game():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_nba_scoreboard_payload()))
    completed_series = payload["events"][0]["competitions"][0]["series"]["competitors"]
    upcoming_series = payload["events"][1]["competitions"][0]["series"]["competitors"]
    completed_series[0]["wins"] = 3
    completed_series[1]["wins"] = 1
    upcoming_series[0]["wins"] = 2
    upcoming_series[1]["wins"] = 1

    events = SportsDashboard._parse_nba_espn_events(payload, la)
    selected = SportsDashboard._select_nba_events(events, datetime(2026, 6, 6, 12, 0, tzinfo=la))

    assert selected["main"]["state"] == "unstarted"
    assert selected["main"]["team_a_code"] == "SA"
    assert selected["main"]["team_b_code"] == "NY"
    assert selected["main"]["series_wins_a"] == 1
    assert selected["main"]["series_wins_b"] == 3


def test_nba_scoreboard_date_range_can_see_next_season_opener():
    la = ZoneInfo("America/Los_Angeles")
    start_date, end_date = SportsDashboard._nba_scoreboard_date_range(
        {},
        la,
        datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )

    assert start_date.isoformat() == "2026-06-04"
    assert end_date >= datetime(2026, 10, 20, tzinfo=la).date()


def test_nba_scoreboard_live_cache_uses_short_refresh_window():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("nba_live_score_refresh")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    la = ZoneInfo("America/Los_Angeles")
    settings = {"nbaCacheHours": "1", "nbaLiveRefreshSeconds": "180"}
    now_utc = datetime.now(timezone.utc)
    cache_key = plugin._nba_scoreboard_cache_key(settings, la, now_utc)

    cached_scoreboard = json.loads(json.dumps(_sample_nba_scoreboard_payload()))
    cached_status = cached_scoreboard["events"][0]["competitions"][0]["status"]["type"]
    cached_status["state"] = "in"
    cached_status["completed"] = False
    cached_status["description"] = "In Progress"
    cached_status["shortDetail"] = "4th Quarter"

    fresh_scoreboard = json.loads(json.dumps(cached_scoreboard))
    fresh_competitors = fresh_scoreboard["events"][0]["competitions"][0]["competitors"]
    fresh_competitors[0]["score"] = "118"
    fresh_competitors[1]["score"] = "111"
    fresh_payload = {
        "version": "sports-dashboard-nba-scoreboard-v1",
        "cache_key": cache_key,
        "fetched_at": now_utc.isoformat(),
        "range_start": "2026-06-01",
        "range_end": "2026-06-30",
        "scoreboard": fresh_scoreboard,
    }
    calls = []
    plugin._fetch_nba_scoreboard_payload = lambda *args, **kwargs: calls.append(args) or fresh_payload
    SportsDashboard._write_json_file(
        tmp_path / "nba_scoreboard.json",
        {
            "version": "sports-dashboard-nba-scoreboard-v1",
            "cache_key": cache_key,
            "fetched_at": (now_utc - timedelta(seconds=240)).isoformat(),
            "scoreboard": cached_scoreboard,
        },
    )

    payload, source_state, _fetched_at = plugin._load_nba_scoreboard(settings, la)

    assert calls
    assert source_state == "ESPN LIVE"
    assert payload["events"][0]["competitions"][0]["competitors"][0]["score"] == "118"


def test_nba_live_state_tracks_active_scoreboard_event():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("nba_live_state")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    la = ZoneInfo("America/Los_Angeles")
    scoreboard = json.loads(json.dumps(_sample_nba_scoreboard_payload()))
    status = scoreboard["events"][0]["competitions"][0]["status"]["type"]
    status["state"] = "in"
    status["completed"] = False
    status["description"] = "In Progress"
    events = SportsDashboard._parse_nba_espn_events(scoreboard, la)
    selected = SportsDashboard._select_nba_events(events, events[0]["start"] + timedelta(hours=1))

    plugin._write_nba_live_state(selected, events[0]["start"] + timedelta(hours=1), "ESPN LIVE")
    state = json.loads((tmp_path / "nba_live_state.json").read_text(encoding="utf-8"))

    assert state["version"] == "sports-dashboard-nba-live-v1"
    assert state["has_live"] is True
    assert state["team_a"] == "\u9a6c\u523a"
    assert state["team_b"] == "\u5c3c\u514b\u65af"
    assert state["live_until"] == (events[0]["start"] + timedelta(hours=4)).astimezone(timezone.utc).isoformat()


def test_nba_odds_match_espn_event_with_chinese_team_names():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)

    enriched = plugin._merge_nba_odds(events, [_sample_nba_odds_event()], la, {"nbaOddsBookmakers": "DraftKings"})
    upcoming = next(event for event in enriched if event["state"] == "unstarted")

    assert upcoming["team_a"] == "\u9a6c\u523a"
    assert upcoming["team_b"] == "\u5c3c\u514b\u65af"
    assert upcoming["odds"]["team_a"] == "2.05"
    assert upcoming["odds"]["team_b"] == "1.75"
    assert upcoming["odds"]["bookmaker"] == "DraftKings"


def test_nba_odds_api_io_match_espn_event():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)

    enriched = plugin._merge_nba_odds(
        events,
        [_sample_nba_odds_api_io_event()],
        la,
        {"nbaOddsProvider": "oddsapiio", "nbaOddsBookmakers": "Bet365"},
    )
    upcoming = next(event for event in enriched if event["state"] == "unstarted")

    assert upcoming["odds"]["team_a"] == "2.10"
    assert upcoming["odds"]["team_b"] == "1.65"
    assert upcoming["odds"]["bookmaker"] == "Bet365"


def test_nba_odds_uses_fresh_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("nba_odds_fresh_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_nba_odds_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"nbaOddsCacheHours": "6"}
    cache_key = plugin._nba_odds_cache_key(settings, "secret")
    odds_event = _sample_nba_odds_event()
    SportsDashboard._write_json_file(
        tmp_path / "nba_odds.json",
        {
            "version": "sports-dashboard-nba-odds-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "odds_events": [odds_event],
        },
    )

    odds_events, source_state, _fetched_at = plugin._load_nba_odds(settings, "secret")

    assert odds_events == [odds_event]
    assert source_state == "NBA ODDS CACHE"


def test_nba_odds_api_io_payload_fetches_event_ids_then_multi_odds():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("nba_odds_api_io_fetch")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    calls = []

    def fake_get_json(path, params, settings, now_utc):
        calls.append((path, params))
        if path == "/events":
            return [{"id": 88112233}, {"id": 88112234}]
        return [_sample_nba_odds_api_io_event()]

    plugin._nba_odds_api_io_get_json = fake_get_json
    settings = {"nbaOddsProvider": "oddsapiio", "nbaOddsBookmakers": "Bet365"}

    payload = plugin._fetch_nba_odds_payload(settings, "secret", "cache", datetime.now(timezone.utc))

    assert payload["provider"] == "oddsapiio"
    assert payload["odds_events"] == [_sample_nba_odds_api_io_event()]
    assert calls[0][0] == "/events"
    assert calls[0][1]["sport"] == "basketball"
    assert calls[0][1]["league"] == "usa-nba-playoffs"
    assert calls[1][0] == "/odds/multi"
    assert calls[1][1]["eventIds"] == "88112233,88112234"


def test_nba_mini_match_row_renders_moneyline_odds():
    plugin = _plugin()
    image = Image.new("RGB", (240, 60), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)
    event = plugin._merge_nba_odds(events, [_sample_nba_odds_event()], la, {"nbaOddsBookmakers": "DraftKings"})[1]
    odds_text = []
    odds_sizes = []
    logo_sizes = []
    team_sizes = []
    original_draw_odds_text = plugin._draw_nba_odds_text
    original_fit_text = plugin._fit_text

    def record_odds_text(draw, box, text, max_size=9, align="center"):
        if text:
            odds_text.append(text)
            odds_sizes.append(max_size)
        return original_draw_odds_text(draw, box, text, max_size=max_size, align=align)

    def record_fit_text(draw_arg, text, max_width, size, bold=False, min_size=11):
        if text in {event["team_a"], event["team_b"]}:
            team_sizes.append(size)
        return original_fit_text(draw_arg, text, max_width, size, bold=bold, min_size=min_size)

    def record_team_logo(_image, _draw, _logo_url, _x, _y, size, fallback_text):
        if fallback_text in {event["team_a"], event["team_b"]}:
            logo_sizes.append(size)

    plugin._draw_nba_odds_text = record_odds_text
    plugin._fit_text = record_fit_text
    plugin._draw_team_logo = record_team_logo

    plugin._draw_nba_mini_match_row(image, draw, 4, 236, 4, event, "VS", show_time=True)

    assert odds_text == ["2.05", "1.75"]
    assert odds_sizes == [8, 8]
    assert logo_sizes == [NBA_MINI_LINEUP_LOGO_SIZE, NBA_MINI_LINEUP_LOGO_SIZE]
    assert team_sizes == [NBA_MINI_LINEUP_ODDS_TEAM_FONT_SIZE, NBA_MINI_LINEUP_ODDS_TEAM_FONT_SIZE]


def test_nba_focus_card_renders_larger_moneyline_odds():
    plugin = _plugin()
    image = Image.new("RGB", (300, 190), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)
    event = dict(plugin._merge_nba_odds(events, [_sample_nba_odds_event()], la, {"nbaOddsBookmakers": "DraftKings"})[1])
    event["team_a_logo"] = ""
    event["team_b_logo"] = ""
    odds_sizes = []
    original_draw_odds_text = plugin._draw_nba_odds_text

    def record_odds_text(draw, box, text, max_size=9, align="center"):
        if text:
            odds_sizes.append(max_size)
        return original_draw_odds_text(draw, box, text, max_size=max_size, align=align)

    plugin._draw_nba_odds_text = record_odds_text

    plugin._draw_nba_compact_main_card(image, draw, 4, 4, 276, 172, event, datetime.now(la), False)

    assert odds_sizes == [10, 10]


def test_nba_main_cards_render_spread_total_footer_for_pregame():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_nba_scoreboard_payload()))
    competition = payload["events"][1]["competitions"][0]
    competition["odds"] = [{"details": "NY -4.5", "overUnder": 221.5}]
    event = dict(SportsDashboard._parse_nba_espn_events(payload, la)[1])
    event["team_a_logo"] = ""
    event["team_b_logo"] = ""
    compact_image = Image.new("RGB", (300, 190), COLORS["paper"])
    compact_draw = ImageDraw.Draw(compact_image)
    focus_image = Image.new("RGB", (380, 210), COLORS["paper"])
    focus_draw = ImageDraw.Draw(focus_image)
    seen_text = []
    original_fit_text = plugin._fit_text

    def record_fit_text(draw_arg, text, max_width, size, bold=False, min_size=11):
        seen_text.append(str(text))
        return original_fit_text(draw_arg, text, max_width, size, bold=bold, min_size=min_size)

    plugin._fit_text = record_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_nba_compact_main_card(compact_image, compact_draw, 4, 4, 276, 172, event, datetime.now(la), False)
    plugin._draw_nba_focus_card(focus_image, focus_draw, 4, 4, 360, 194, event, datetime.now(la), False)

    assert seen_text.count("SPREAD NY -4.5  |  O/U 221.5") == 2


def test_nba_main_cards_prioritize_broadcast_before_venue_for_pregame():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_nba_scoreboard_payload()))
    competition = payload["events"][1]["competitions"][0]
    competition["broadcasts"] = [{"market": "national", "names": ["ESPN"]}]
    competition["venue"] = {
        "fullName": "Madison Square Garden",
        "address": {"city": "New York", "state": "NY"},
    }
    competition["odds"] = [{"details": "NY -4.5", "overUnder": 221.5}]
    event = dict(SportsDashboard._parse_nba_espn_events(payload, la)[1])
    event["team_a_logo"] = ""
    event["team_b_logo"] = ""
    compact_image = Image.new("RGB", (300, 190), COLORS["paper"])
    compact_draw = ImageDraw.Draw(compact_image)
    focus_image = Image.new("RGB", (380, 210), COLORS["paper"])
    focus_draw = ImageDraw.Draw(focus_image)
    seen_text = []
    original_fit_text = plugin._fit_text

    def record_fit_text(draw_arg, text, max_width, size, bold=False, min_size=11):
        seen_text.append(str(text))
        return original_fit_text(draw_arg, text, max_width, size, bold=bold, min_size=min_size)

    plugin._fit_text = record_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_nba_compact_main_card(compact_image, compact_draw, 4, 4, 276, 172, event, datetime.now(la), False)
    plugin._draw_nba_focus_card(focus_image, focus_draw, 4, 4, 360, 194, event, datetime.now(la), False)

    assert event["broadcast"] == "ESPN"
    assert event["venue"] == "Madison Square Garden"
    assert seen_text.count("TV ESPN  |  SPREAD NY -4.5") == 2
    assert "Madison Square Garden" not in seen_text


def test_nba_main_footer_falls_back_to_venue_when_tv_and_odds_missing():
    assert SportsDashboard._nba_main_footer_label({"venue": "Madison Square Garden"}) == "Madison Square Garden"


def test_nba_main_cards_render_full_chinese_team_names():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = dict(SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)[0])
    event["team_a_logo"] = ""
    event["team_b_logo"] = ""
    compact_image = Image.new("RGB", (300, 190), COLORS["paper"])
    compact_draw = ImageDraw.Draw(compact_image)
    focus_image = Image.new("RGB", (380, 210), COLORS["paper"])
    focus_draw = ImageDraw.Draw(focus_image)
    seen_text = []
    original_fit_text = plugin._fit_text

    def record_fit_text(draw_arg, text, max_width, size, bold=False, min_size=11):
        seen_text.append(str(text))
        return original_fit_text(draw_arg, text, max_width, size, bold=bold, min_size=min_size)

    plugin._fit_text = record_fit_text
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_nba_compact_main_card(compact_image, compact_draw, 4, 4, 276, 172, event, datetime.now(la), False)
    plugin._draw_nba_focus_card(focus_image, focus_draw, 4, 4, 360, 194, event, datetime.now(la), False)

    assert "\u5723\u5b89\u4e1c\u5c3c\u5965\u9a6c\u523a" in seen_text
    assert "\u7ebd\u7ea6\u5c3c\u514b\u65af" in seen_text


def test_nba_main_cards_highlight_final_winner_with_nba_accent():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = dict(SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)[0])
    event["team_a_logo"] = ""
    event["team_b_logo"] = ""
    event["winner_a"] = False
    event["winner_b"] = True
    compact_image = Image.new("RGB", (300, 190), COLORS["paper"])
    compact_draw = ImageDraw.Draw(compact_image)
    focus_image = Image.new("RGB", (380, 210), COLORS["paper"])
    focus_draw = ImageDraw.Draw(focus_image)
    centered_calls = []
    original_draw_centered = plugin._draw_centered

    def record_draw_centered(draw_arg, center, text, font, fill):
        centered_calls.append((str(text), fill))
        return original_draw_centered(draw_arg, center, text, font, fill)

    plugin._draw_centered = record_draw_centered
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_nba_compact_main_card(compact_image, compact_draw, 4, 4, 276, 172, event, datetime.now(la), False)
    plugin._draw_nba_focus_card(focus_image, focus_draw, 4, 4, 360, 194, event, datetime.now(la), False)

    assert [fill for text, fill in centered_calls if text == "\u7ebd\u7ea6\u5c3c\u514b\u65af"] == [
        COLORS["nba_accent"],
        COLORS["nba_accent"],
    ]
    assert [fill for text, fill in centered_calls if text == "\u5723\u5b89\u4e1c\u5c3c\u5965\u9a6c\u523a"] == [
        COLORS["text"],
        COLORS["text"],
    ]


def test_nba_offseason_panel_draws_core_status_labels():
    plugin = _plugin()
    image = Image.new("RGB", (552, 268), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    seen_texts = []
    draw_text_calls = []
    original_fit_text = plugin._fit_text
    original_draw_text = draw.text

    def record_fit_text(draw_arg, text, max_width, size, bold=False, min_size=11):
        seen_texts.append(str(text))
        return original_fit_text(draw_arg, text, max_width, size, bold=bold, min_size=min_size)

    def record_draw_text(xy, text, *args, **kwargs):
        draw_text_calls.append((xy, str(text)))
        return original_draw_text(xy, text, *args, **kwargs)

    plugin._fit_text = record_fit_text
    draw.text = record_draw_text
    selected = {
        "live": [],
        "upcoming": [],
        "recent": [
            {
                "start": datetime(2026, 6, 13, 17, 30, tzinfo=la),
                "state": "completed",
                "team_a": "\u5c3c\u514b\u65af",
                "team_b": "\u9a6c\u523a",
                "wins_a": 94,
                "wins_b": 90,
                "block": "POSTSEASON",
            }
        ],
        "main": None,
        "next_season_event": None,
        "offseason": True,
    }

    plugin._draw_nba_compact_offseason_panel(
        image,
        draw,
        12,
        58,
        539,
        260,
        selected,
        datetime(2026, 6, 14, 9, 0, tzinfo=la),
    )

    assert "\u4f11\u8d5b\u671f" in seen_texts
    title_xy = next(xy for xy, text in draw_text_calls if text == "\u4f11\u8d5b\u671f")
    assert title_xy == (30, 99)
    assert "\u4e0b\u5b63\u9996\u6218" in seen_texts
    assert "\u8d5b\u7a0b\u5f85\u516c\u5e03" in seen_texts
    assert "\u9884\u8ba1 2026\u5e7410\u6708" in seen_texts


def test_nba_offseason_accent_asset_is_transparent():
    assert Path(LOCAL_NBA_OFFSEASON_ACCENT_PATH).exists()
    with Image.open(LOCAL_NBA_OFFSEASON_ACCENT_PATH) as source:
        accent = source.convert("RGBA")

    assert accent.size == NBA_OFFSEASON_ACCENT_SIZE
    alpha = accent.getchannel("A")
    assert alpha.getextrema()[0] == 0
    assert alpha.getbbox() is not None
    assert accent.getpixel((0, 0))[3] == 0
    assert accent.getpixel((accent.width - 1, accent.height - 1))[3] == 0


def test_nba_offseason_panel_draws_accent_in_left_blank_area(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (552, 268), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    accent_color = (12, 200, 40, 255)
    requested_sizes = []
    selected = {
        "live": [],
        "upcoming": [],
        "recent": [],
        "main": None,
        "next_season_event": None,
        "offseason": True,
    }

    def load_accent(size):
        requested_sizes.append(size)
        return Image.new("RGBA", size, accent_color)

    monkeypatch.setattr(plugin, "_load_nba_offseason_accent", load_accent)

    plugin._draw_nba_compact_offseason_panel(
        image,
        draw,
        12,
        58,
        539,
        260,
        selected,
        datetime(2026, 6, 14, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles")),
    )

    assert requested_sizes == [NBA_OFFSEASON_ACCENT_SIZE]
    assert image.getpixel((200, 100)) == accent_color[:3]


def test_nba_offseason_panel_bleeds_filler_past_inner_slot(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (552, 268), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    filler_color = (12, 200, 40)
    requested_sizes = []
    selected = {
        "live": [],
        "upcoming": [],
        "recent": [],
        "main": None,
        "next_season_event": None,
        "offseason": True,
    }

    def load_filler(size, *_args):
        requested_sizes.append(size)
        return Image.new("RGB", size, filler_color)

    monkeypatch.setattr(plugin, "_load_nba_offseason_filler", load_filler)

    plugin._draw_nba_compact_offseason_panel(
        image,
        draw,
        12,
        58,
        539,
        260,
        selected,
        datetime(2026, 6, 14, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles")),
    )

    assert requested_sizes
    assert image.getpixel((551, 267)) == filler_color


def test_nba_inline_list_team_names_use_larger_font():
    plugin = _plugin()
    image = Image.new("RGB", (240, 44), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    event = {
        "team_a": "\u9a6c\u523a",
        "team_b": "\u5c3c\u514b\u65af",
        "team_a_logo": "",
        "team_b_logo": "",
    }
    fit_calls = []
    logo_sizes = []
    original_fit_text = plugin._fit_text
    original_draw_team_logo = plugin._draw_team_logo

    def record_fit_text(draw_arg, text, max_width, size, bold=False, min_size=11):
        fit_calls.append((text, size, min_size))
        return original_fit_text(draw_arg, text, max_width, size, bold=bold, min_size=min_size)

    def record_team_logo(image_arg, draw_arg, logo_url, x, y, size, fallback_text):
        logo_sizes.append(size)
        return original_draw_team_logo(image_arg, draw_arg, logo_url, x, y, size, fallback_text)

    plugin._fit_text = record_fit_text
    plugin._draw_team_logo = record_team_logo

    plugin._draw_nba_teams_inline(image, draw, 4, 236, 12, event, "VS")

    team_calls = [call for call in fit_calls if call[0] in {event["team_a"], event["team_b"]}]
    assert team_calls == [
        (event["team_a"], NBA_INLINE_TEAM_FONT_SIZE, NBA_INLINE_TEAM_MIN_FONT_SIZE),
        (event["team_b"], NBA_INLINE_TEAM_FONT_SIZE, NBA_INLINE_TEAM_MIN_FONT_SIZE),
    ]
    assert logo_sizes == [NBA_INLINE_LOGO_SIZE, NBA_INLINE_LOGO_SIZE]


def test_nba_inline_rows_highlight_final_winner_with_nba_accent():
    plugin = _plugin()
    image = Image.new("RGB", (260, 48), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    event = {
        "state": "post",
        "team_a": "\u9a6c\u523a",
        "team_b": "\u5c3c\u514b\u65af",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 106,
        "wins_b": 112,
        "winner_a": False,
        "winner_b": True,
    }
    text_calls = []

    def record_text_in_box(_draw, _box, text, _font, fill, align="left"):
        text_calls.append((str(text), fill, align))

    plugin._draw_text_in_box = record_text_in_box
    plugin._draw_team_logo = lambda *_args, **_kwargs: None

    plugin._draw_nba_lineup_inline(image, draw, 4, 256, 8, event, "106-112")
    plugin._draw_nba_teams_inline(image, draw, 4, 256, 28, event, "106-112")

    assert ("\u9a6c\u523a", COLORS["text"], "left") in text_calls
    assert ("\u5c3c\u514b\u65af", COLORS["nba_accent"], "right") in text_calls


def test_nba_header_court_strip_asset_renders_in_empty_header_space():
    plugin = _plugin()
    assert Path(LOCAL_NBA_COURT_STRIP_PATH).exists()
    with Image.open(LOCAL_NBA_COURT_STRIP_PATH) as strip:
        assert strip.size == (310, 38)
        assert "A" in strip.getbands()
        assert strip.getchannel("A").getextrema() == (0, 255)
        bottom_alpha = strip.getchannel("A").crop((0, strip.height - 1, strip.width, strip.height))
        assert sum(bottom_alpha.histogram()[1:]) > 250

    def render_header(colors):
        token = _ACTIVE_COLORS.set(colors)
        try:
            image = Image.new("RGB", (552, 268), COLORS["paper"])
            draw = ImageDraw.Draw(image)
            la = ZoneInfo("America/Los_Angeles")
            events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)
            now = datetime(2026, 6, 6, 12, 0, tzinfo=la)
            selected = SportsDashboard._select_nba_events(events, now)
            plugin._load_team_logo = lambda _logo_url, _size: None

            plugin._draw_nba_compact_panel(image, draw, (0, 0, 551, 267), selected, "ESPN LIVE", now)
            return image
        finally:
            _ACTIVE_COLORS.reset(token)

    image = render_header(DAY_COLORS)
    pixels = image.load()
    dark_pixels = 0
    background_pixels = 0
    for y in range(10, 48):
        for x in range(150, 460):
            if pixels[x, y] == DAY_COLORS["text"]:
                dark_pixels += 1
            if pixels[x, y] not in (DAY_COLORS["text"], DAY_COLORS["panel"]):
                background_pixels += 1
    assert dark_pixels > 20
    assert background_pixels > 200

    image = render_header(DEEP_NIGHT_COLORS)
    pixels = image.load()
    light_pixels = 0
    background_pixels = 0
    for y in range(10, 48):
        for x in range(150, 460):
            if pixels[x, y] == DEEP_NIGHT_COLORS["text"]:
                light_pixels += 1
            if pixels[x, y] not in (DEEP_NIGHT_COLORS["text"], DEEP_NIGHT_COLORS["panel"]):
                background_pixels += 1
    assert light_pixels > 20
    assert background_pixels > 200


def test_nba_empty_slot_filler_asset_is_exact_slot_size():
    assert Path(LOCAL_NBA_EMPTY_SLOT_FILLER_PATH).exists()
    with Image.open(LOCAL_NBA_EMPTY_SLOT_FILLER_PATH) as source:
        filler = source.convert("RGB")

    assert filler.size == (257, 67)
    assert filler.getbbox() is not None
    assert len(filler.getcolors(maxcolors=257 * 67)) > 20


def test_nba_empty_slot_filler_preserves_aspect_ratio_when_short():
    with Image.open(LOCAL_NBA_EMPTY_SLOT_FILLER_PATH) as source:
        source = source.convert("RGBA")
        distorted = source.resize((257, 34), Image.LANCZOS)

    fitted = SportsDashboard._load_nba_empty_slot_filler((257, 34))

    assert fitted.size == (257, 34)
    assert fitted.tobytes() != distorted.tobytes()


def test_nba_offseason_filler_asset_is_exact_blank_slot_size():
    assert Path(LOCAL_NBA_OFFSEASON_FILLER_PATH).exists()
    with Image.open(LOCAL_NBA_OFFSEASON_FILLER_PATH) as source:
        filler = source.convert("RGB")

    assert filler.size == (214, 48)
    assert filler.getbbox() is not None
    assert len(filler.getcolors(maxcolors=214 * 48)) > 20


def test_nba_offseason_watch_draws_filler_in_bottom_blank_slot(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (250, 230), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    requested_sizes = []

    def load_filler(size, *_args):
        requested_sizes.append(size)
        filler = Image.new("RGB", size, (200, 10, 10))
        filler_draw = ImageDraw.Draw(filler)
        filler_draw.rectangle((0, size[1] - 48, size[0] - 1, size[1] - 1), fill=(12, 200, 40))
        return filler

    monkeypatch.setattr(plugin, "_load_nba_offseason_filler", load_filler)

    plugin._draw_nba_offseason_watch(
        image,
        draw,
        10,
        10,
        223,
        212,
        None,
        datetime(2026, 6, 14, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles")),
    )

    assert requested_sizes == [
        (
            int(214 * NBA_OFFSEASON_FILLER_ZOOM + 0.999),
            int(48 * NBA_OFFSEASON_FILLER_ZOOM + 0.999),
        )
    ]
    assert image.getpixel((20, 170)) == (12, 200, 40)


def test_nba_recent_empty_slot_draws_filler(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (300, 150), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    filler = Image.new("RGBA", (257, 67), (10, 220, 30, 255))
    event = {
        "start": datetime(2026, 6, 8, 17, 0, tzinfo=timezone.utc),
        "team_a": "NY",
        "team_b": "SA",
        "wins_a": 106,
        "wins_b": 107,
    }

    monkeypatch.setattr(plugin, "_load_nba_empty_slot_filler", lambda size: filler.resize(size))
    monkeypatch.setattr(plugin, "_draw_nba_mini_match_row", lambda *_args, **_kwargs: None)

    plugin._draw_nba_compact_recent_rows(image, draw, 10, 266, 10, 130, [event, event])

    assert image.getpixel((20, 100)) == (10, 220, 30)


def test_worldcup_header_banner_asset_renders_in_empty_header_space():
    plugin = _plugin()
    assert Path(LOCAL_WORLDCUP_HEADER_BANNER_PATH).exists()
    with Image.open(LOCAL_WORLDCUP_HEADER_BANNER_PATH) as banner:
        assert banner.size == (233, 40)
        assert "A" in banner.getbands()
        assert banner.getchannel("A").getextrema() == (0, 255)

    image = Image.new("RGB", (556, 208), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    plugin._draw_worldcup_header_banner(image, 225, 0, 465, 47)

    pixels = image.load()
    changed_pixels = 0
    paper_pixels = 0
    for y in range(0, 48):
        for x in range(225, 466):
            if pixels[x, y] != COLORS["paper"]:
                changed_pixels += 1
            else:
                paper_pixels += 1
    assert changed_pixels > 900
    assert paper_pixels > 100


def test_worldcup_compact_panel_places_header_banner_flush_to_top(monkeypatch):
    plugin = _plugin()
    now = datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc)
    image = Image.new("RGB", (556, 208), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    captured = []
    selected = {"live": [], "upcoming": [], "recent": [], "main": None, "visible_matches": 4}

    monkeypatch.setattr(plugin, "_draw_worldcup_header_banner", lambda _image, x1, y1, x2, y2: captured.append((x1, y1, x2, y2)))

    plugin._draw_worldcup_compact_panel(image, draw, (0, 0, 555, 207), selected, "ESPN LIVE", None, now)

    assert captured == [(225, 0, 465, 47)]


def test_live_lpl_event_becomes_now_playing_without_duplicate_rows():
    tz = timezone.utc
    live_event = {
        "start": datetime(2026, 6, 3, 9, 0, tzinfo=tz),
        "state": "inprogress",
        "team_a": "BLG",
        "team_b": "EDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 1,
        "wins_b": 0,
        "block": "Playoffs",
    }
    next_event = {
        "start": datetime(2026, 6, 5, 9, 0, tzinfo=tz),
        "state": "unstarted",
        "team_a": "LGD",
        "team_b": "AL",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": None,
        "wins_b": None,
        "block": "Playoffs",
    }
    recent_event = {
        "start": datetime(2026, 6, 2, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "TT",
        "team_b": "LGD",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 2,
        "wins_b": 3,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events(
        [recent_event, live_event, next_event],
        datetime(2026, 6, 3, 10, 0, tzinfo=tz),
    )

    assert selected["main"] is live_event
    assert selected["live"] == [live_event]
    assert selected["upcoming"] == [next_event]
    assert selected["recent"] == [recent_event]
    assert SportsDashboard._lpl_focus_tag(True) == "NOW PLAYING"
    assert SportsDashboard._score_label(live_event) == "1-0"


def test_recent_zero_zero_lpl_match_is_inferred_live_during_match_window():
    tz = timezone.utc
    event = {
        "start": datetime(2026, 6, 3, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "EDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 0,
        "wins_b": 0,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events([event], datetime(2026, 6, 3, 9, 15, tzinfo=tz))

    assert selected["live"] == [event]
    assert selected["main"] is event
    assert selected["recent"] == []

    stale = SportsDashboard._select_lpl_events([event], datetime(2026, 6, 3, 16, 1, tzinfo=tz))
    assert stale["live"] == []
    assert stale["recent"] == [event]


def test_future_completed_zero_zero_lpl_match_stays_next_until_start():
    tz = timezone.utc
    event = {
        "start": datetime(2026, 6, 6, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "JDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
        "block": "Playoffs",
    }
    next_event = {
        "start": datetime(2026, 6, 7, 9, 0, tzinfo=tz),
        "state": "unstarted",
        "team_a": "WE",
        "team_b": "TES",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events(
        [event, next_event],
        datetime(2026, 6, 6, 8, 50, tzinfo=tz),
    )

    assert selected["live"] == []
    assert selected["main"] is event
    assert selected["upcoming"] == [event, next_event]
    assert selected["recent"] == []

    live = SportsDashboard._select_lpl_events([event, next_event], datetime(2026, 6, 6, 9, 5, tzinfo=tz))
    assert live["live"] == [event]
    assert live["main"] is event
    assert live["upcoming"] == [next_event]
    assert live["recent"] == []


def test_lpl_live_endpoint_polling_starts_in_pregame_window():
    tz = timezone.utc
    event = {
        "start": datetime(2026, 6, 6, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "JDG",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
    }

    assert SportsDashboard._should_poll_lpl_live_endpoint([event], datetime(2026, 6, 6, 8, 45, tzinfo=tz))
    assert not SportsDashboard._should_poll_lpl_live_endpoint([event], datetime(2026, 6, 6, 8, 0, tzinfo=tz))


def test_partial_best_of_lpl_series_is_inferred_live_between_games():
    tz = timezone.utc
    event = {
        "start": datetime(2026, 6, 3, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "EDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 1,
        "wins_b": 0,
        "best_of": 5,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events([event], datetime(2026, 6, 3, 10, 45, tzinfo=tz))

    assert selected["live"] == [event]
    assert selected["main"] is event
    assert selected["recent"] == []
    assert SportsDashboard._score_label(event) == "1-0"


def test_completed_best_of_lpl_series_moves_to_recent_after_deciding_win():
    tz = timezone.utc
    event = {
        "start": datetime(2026, 6, 3, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "EDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 2,
        "wins_b": 0,
        "best_of": 3,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events([event], datetime(2026, 6, 3, 10, 45, tzinfo=tz))

    assert selected["live"] == []
    assert selected["main"] is event
    assert selected["recent"] == [event]


def test_lpl_realtime_info_reads_little_round_in_event_team_order():
    plugin = _plugin()
    event = {
        "event_id": "match-blg-edg",
        "team_a": "BLG",
        "team_b": "EDG",
        "wins_a": 1,
        "wins_b": 0,
        "best_of": 5,
    }
    plugin._fetch_lpl_event_details_payload = lambda event_id: {
        "data": {
            "event": {
                "match": {
                    "teams": [
                        {"id": "team-blg", "code": "BLG", "result": {"gameWins": 1}},
                        {"id": "team-edg", "code": "EDG", "result": {"gameWins": 0}},
                    ],
                    "games": [
                        {"number": 1, "id": "game-1", "state": "completed", "teams": []},
                        {
                            "number": 2,
                            "id": "game-2",
                            "state": "inProgress",
                            "teams": [
                                {"id": "team-edg", "side": "blue"},
                                {"id": "team-blg", "side": "red"},
                            ],
                        },
                    ],
                }
            }
        }
    }
    plugin._fetch_lpl_live_stats_window = lambda game_id: {
        "esportsGameId": game_id,
        "frames": [
            {
                "rfc460Timestamp": _fresh_lpl_frame_time(),
                "blueTeam": {"totalKills": 5},
                "redTeam": {"totalKills": 7},
            }
        ],
    }

    info = plugin._fetch_lpl_realtime_info(event)

    assert info["label"] == "Little Round"
    assert info["score"] == "7-5"
    assert info["game_id"] == "game-2"
    assert info["game_number"] == 2


def test_lpl_realtime_info_falls_back_to_stats_window_when_detail_games_lag():
    plugin = _plugin()
    event = {
        "event_id": "match-blg-jdg",
        "team_a": "BLG",
        "team_b": "JDG",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
    }
    plugin._fetch_lpl_event_details_payload = lambda event_id: {
        "data": {
            "event": {
                "match": {
                    "teams": [
                        {"id": "team-blg", "code": "BLG", "result": {"gameWins": 0}},
                        {"id": "team-jdg", "code": "JDG", "result": {"gameWins": 0}},
                    ],
                    "games": [
                        {
                            "number": 1,
                            "id": "game-1",
                            "state": "unstarted",
                            "teams": [
                                {"id": "team-blg", "side": "blue"},
                                {"id": "team-jdg", "side": "red"},
                            ],
                        },
                        {"number": 2, "id": "game-2", "state": "unstarted", "teams": []},
                    ],
                }
            }
        }
    }
    plugin._fetch_lpl_live_stats_window = lambda game_id: {
        "esportsGameId": game_id,
        "frames": [
            {
                "rfc460Timestamp": _fresh_lpl_frame_time(),
                "blueTeam": {"totalKills": 2},
                "redTeam": {"totalKills": 1},
            }
        ],
    }

    info = plugin._fetch_lpl_realtime_info(event)

    assert info["label"] == "Little Round"
    assert info["score"] == "2-1"
    assert info["game_id"] == "game-1"
    assert info["game_number"] == 1


def test_lpl_realtime_info_falls_back_to_bo3_when_riot_frame_is_stale():
    plugin = _plugin()
    event = {
        "event_id": "match-blg-jdg",
        "start": datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
        "team_a": "BLG",
        "team_b": "JDG",
        "wins_a": 0,
        "wins_b": 1,
        "best_of": 5,
    }
    plugin._fetch_lpl_event_details_payload = lambda event_id: {
        "data": {
            "event": {
                "match": {
                    "teams": [
                        {"id": "team-blg", "code": "BLG", "result": {"gameWins": 0}},
                        {"id": "team-jdg", "code": "JDG", "result": {"gameWins": 1}},
                    ],
                    "games": [
                        {
                            "number": 2,
                            "id": "game-2",
                            "state": "inProgress",
                            "teams": [
                                {"id": "team-blg", "side": "blue"},
                                {"id": "team-jdg", "side": "red"},
                            ],
                        },
                    ],
                }
            }
        }
    }
    plugin._fetch_lpl_live_stats_window = lambda game_id: {
        "esportsGameId": game_id,
        "frames": [
            {
                "rfc460Timestamp": _fresh_lpl_frame_time(minutes_ago=20),
                "blueTeam": {"totalKills": 0},
                "redTeam": {"totalKills": 0},
            }
        ],
    }
    plugin._fetch_lpl_bo3_match_payload = lambda event: {
        "id": 116291,
        "slug": "jd-gaming-lol-vs-bilibili-gaming-lol-06-06-2026",
        "status": "current",
        "start_date": "2026-06-06T09:00:00.000+00:00",
        "team1_score": 0,
        "team2_score": 1,
        "team1": {"name": "JD Gaming", "slug": "jd-gaming-lol"},
        "team2": {"name": "Bilibili Gaming", "slug": "bilibili-gaming-lol"},
        "live_updates": {
            "team_1": {"game_score": 10, "match_score": 1},
            "team_2": {"game_score": 3, "match_score": 0},
            "game_number": 2,
        },
    }

    info = plugin._fetch_lpl_realtime_info(event)

    assert info["label"] == "Little Round"
    assert info["score"] == "3-10"
    assert info["game_id"] == "bo3:116291"
    assert info["game_number"] == 2
    assert info["source"] == "bo3.gg"


def test_lpl_realtime_info_hides_stale_riot_frame_when_bo3_is_disabled():
    plugin = _plugin()
    event = {
        "event_id": "match-blg-jdg",
        "team_a": "BLG",
        "team_b": "JDG",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
    }
    plugin._fetch_lpl_event_details_payload = lambda event_id: {
        "data": {
            "event": {
                "match": {
                    "teams": [
                        {"id": "team-blg", "code": "BLG", "result": {"gameWins": 0}},
                        {"id": "team-jdg", "code": "JDG", "result": {"gameWins": 0}},
                    ],
                    "games": [
                        {
                            "number": 1,
                            "id": "game-1",
                            "state": "inProgress",
                            "teams": [
                                {"id": "team-blg", "side": "blue"},
                                {"id": "team-jdg", "side": "red"},
                            ],
                        }
                    ],
                }
            }
        }
    }
    plugin._fetch_lpl_live_stats_window = lambda game_id: {
        "esportsGameId": game_id,
        "frames": [
            {
                "rfc460Timestamp": _fresh_lpl_frame_time(minutes_ago=20),
                "blueTeam": {"totalKills": 0},
                "redTeam": {"totalKills": 0},
            }
        ],
    }
    plugin._fetch_lpl_bo3_match_payload = lambda event: (_ for _ in ()).throw(AssertionError("bo3 called"))

    info = plugin._fetch_lpl_realtime_info(event, {"lplBo3LiveApiEnabled": False})

    assert info is None


def test_lpl_realtime_info_shows_intermission_between_series_games():
    plugin = _plugin()
    event = {
        "event_id": "match-blg-edg",
        "team_a": "BLG",
        "team_b": "EDG",
        "wins_a": 1,
        "wins_b": 0,
        "best_of": 5,
    }
    plugin._fetch_lpl_event_details_payload = lambda event_id: {
        "data": {
            "event": {
                "match": {
                    "teams": [
                        {"id": "team-blg", "code": "BLG", "result": {"gameWins": 1}},
                        {"id": "team-edg", "code": "EDG", "result": {"gameWins": 0}},
                    ],
                    "games": [
                        {"number": 1, "id": "game-1", "state": "completed", "teams": []},
                        {"number": 2, "id": "game-2", "state": "unstarted", "teams": []},
                    ],
                }
            }
        }
    }
    plugin._fetch_lpl_live_stats_window = lambda game_id: (_ for _ in ()).throw(AssertionError("window called"))

    info = plugin._fetch_lpl_realtime_info(event)

    assert info == {"state": "intermission", "label": "Little Round", "score": "0-0"}


def test_lpl_bo3_completed_game_resets_little_round_for_intermission():
    event = {
        "start": datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
        "team_a": "BLG",
        "team_b": "JDG",
        "wins_a": 1,
        "wins_b": 0,
        "best_of": 5,
    }
    payload = {
        "id": 116291,
        "slug": "jd-gaming-lol-vs-bilibili-gaming-lol-06-06-2026",
        "start_date": "2026-06-06T09:00:00.000+00:00",
        "team1_score": 0,
        "team2_score": 1,
        "team1": {"name": "JD Gaming", "slug": "jd-gaming-lol"},
        "team2": {"name": "Bilibili Gaming", "slug": "bilibili-gaming-lol"},
        "live_updates": {
            "team_1": {"game_score": 23, "match_score": 0},
            "team_2": {"game_score": 6, "match_score": 1},
            "game_number": 1,
            "game_ended": True,
        },
    }

    info = SportsDashboard._lpl_little_round_from_bo3_payload(payload, event)

    assert info["state"] == "intermission"
    assert info["label"] == "Little Round"
    assert info["score"] == "0-0"
    assert info["game_id"] == "bo3:116291"
    assert info["game_number"] == 2
    assert info["source"] == "bo3.gg"


def test_lpl_live_state_file_marks_inferred_live_window():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("lpl_live_state")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    now = datetime(2026, 6, 3, 9, 15, tzinfo=timezone.utc)
    event = {
        "event_id": "1122",
        "start": datetime(2026, 6, 3, 9, 0, tzinfo=timezone.utc),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "EDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events([event], now)
    plugin._write_lpl_live_state(selected, now, "LIVE DATA")

    state = json.loads((tmp_path / "lpl_live_state.json").read_text(encoding="utf-8"))
    assert state["version"] == "sports-dashboard-lpl-live-v1"
    assert state["has_live"] is True
    assert state["event_id"] == "1122"
    assert state["team_a"] == "BLG"
    assert state["team_b"] == "EDG"
    assert state["score"] == "0-0"
    assert state["best_of"] == 5
    assert state["live_until"] == "2026-06-03T15:00:00+00:00"


def test_lpl_live_endpoint_merge_replaces_matching_schedule_event():
    start = datetime(2026, 6, 3, 9, 0, tzinfo=timezone.utc)
    schedule_event = {
        "event_id": "100",
        "league_id": "98767991314006698",
        "start": start,
        "state": "completed",
        "team_a": "BLG",
        "team_b": "EDG",
        "wins_a": 0,
        "wins_b": 0,
    }
    live_event = {
        "event_id": "100",
        "league_id": "98767991314006698",
        "start": start,
        "state": "inProgress",
        "team_a": "BLG",
        "team_b": "EDG",
        "wins_a": 1,
        "wins_b": 0,
    }

    merged = SportsDashboard._merge_lpl_live_events([schedule_event], [live_event], "98767991314006698")

    assert len(merged) == 1
    assert merged[0]["state"] == "inProgress"
    assert merged[0]["wins_a"] == 1


def test_lpl_odds_match_lolesports_team_codes():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_lpl_events(_sample_payload(), la)

    enriched = plugin._merge_lpl_odds(
        events,
        [_sample_lpl_odds_api_io_event()],
        la,
        {"lplOddsBookmakers": "Bet365"},
    )

    assert enriched[1]["odds"]["team_a"] == "1.65"
    assert enriched[1]["odds"]["team_b"] == "2.10"
    assert enriched[1]["odds"]["bookmaker"] == "Bet365"
    assert "odds" not in enriched[0]


def test_lpl_odds_handles_reversed_lolesports_order():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = next(
        item
        for item in SportsDashboard._fallback_lpl_events(la)
        if item["team_a"] == "LGD" and item["team_b"] == "AL"
    )

    enriched = plugin._merge_lpl_odds(
        [event],
        [
            _sample_lpl_odds_api_io_event(
                home="Anyones Legend",
                away="LGD Gaming",
                date="2026-06-05T09:00:00Z",
                home_odds="1.350",
                away_odds="3.200",
            )
        ],
        la,
        {"lplOddsBookmakers": "Bet365"},
    )

    assert enriched[0]["odds"]["team_a"] == "3.20"
    assert enriched[0]["odds"]["team_b"] == "1.35"


def test_lpl_odds_uses_fresh_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("lpl_odds_fresh_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_lpl_odds_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"lplOddsCacheHours": "12"}
    cache_key = plugin._lpl_odds_cache_key(settings, "secret")
    odds_event = _sample_lpl_odds_api_io_event()
    SportsDashboard._write_json_file(
        tmp_path / "lpl_odds.json",
        {
            "version": "sports-dashboard-lpl-odds-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "odds_events": [odds_event],
        },
    )

    odds_events, source_state, _fetched_at = plugin._load_lpl_odds(settings, "secret")

    assert odds_events == [odds_event]
    assert source_state == "LPL ODDS CACHE"


def test_left_width_keeps_lpl_sidebar_usable():
    width = SportsDashboard._left_width({"worldCupLeftWidth": "680"}, (800, 480))

    assert width == 556


def test_worldcup_defaults_to_four_visible_matches():
    assert SportsDashboard._visible_worldcup_matches({}) == 4
    assert SportsDashboard._visible_worldcup_matches({"worldCupVisibleMatches": "7"}) == 4
    assert SportsDashboard._worldcup_capture_width({}, 800, 4) == 800
    assert SportsDashboard._worldcup_local_time_labels()[0] == "12:00"


def test_worldcup_group_points_labels_reserve_future_slots():
    assert SportsDashboard._worldcup_group_points_label({}, "a") == "PTS -"
    assert SportsDashboard._worldcup_group_points_label({"team_a_group_points": 3}, "a") == "PTS 3"
    assert SportsDashboard._worldcup_group_points_label({"group_points_b": "0"}, "b") == "PTS 0"
    assert SportsDashboard._worldcup_team_points_meta({"team_b_standing_points": 4}, "b") == "PTS 4"
    assert SportsDashboard._worldcup_group_record_label({"block": "Group A", "team_a_group_record": "1-0-0"}, "a") == "1-0-0"
    assert SportsDashboard._worldcup_group_record_label({"block": "Group A", "team_a": "Mexico"}, "a") == "0-0-0"
    assert SportsDashboard._worldcup_group_record_label({"block": "Group A"}, "a") == ""
    assert SportsDashboard._worldcup_team_points_meta(
        {"block": "Group A", "team_a_group_record": "1-0-0", "team_a_group_points": 3, "odds": {"team_a": "6.00"}},
        "a",
        include_odds=True,
    ) == "PTS 3 / 1-0-0 / 6.00"


def test_worldcup_espn_event_block_reads_group_from_alt_game_note():
    # ESPN's scoreboard only carries the group letter in competition.altGameNote;
    # season.slug is just "group-stage", so the block must come from the note.
    competition = {"altGameNote": "FIFA World Cup, Group A"}
    block = SportsDashboard._worldcup_espn_event_block({"season": {"slug": "group-stage"}}, competition)
    assert SportsDashboard._worldcup_explicit_group_key({"block": block}) == "Group A"


def test_worldcup_explicit_group_key_supports_groups_through_l():
    # The 2026 World Cup has 12 groups (A-L), not 8.
    assert SportsDashboard._worldcup_explicit_group_key({"block": "Group L"}) == "Group L"
    assert SportsDashboard._worldcup_explicit_group_key({"block": "Group I"}) == "Group I"
    assert SportsDashboard._worldcup_explicit_group_key({"block": "GROUP STAGE"}) == ""


def _wc_group_event(eid, date, note, home_tla, home_score, away_tla, away_score, finished=True):
    if finished:
        status = {"type": {"state": "post", "completed": True, "name": "STATUS_FULL_TIME", "detail": "FT"}}
    else:
        status = {"type": {"state": "pre", "completed": False, "name": "STATUS_SCHEDULED",
                           "detail": "Sun, June 21st at 12:00 PM EDT"}}
    return {
        "id": eid,
        "date": date,
        "season": {"slug": "group-stage"},
        "competitions": [{
            "id": eid,
            "date": date,
            "altGameNote": note,
            "status": status,
            "competitors": [
                {"homeAway": "home", "score": None if home_score is None else str(home_score),
                 "team": {"abbreviation": home_tla, "displayName": home_tla}},
                {"homeAway": "away", "score": None if away_score is None else str(away_score),
                 "team": {"abbreviation": away_tla, "displayName": away_tla}},
            ],
        }],
    }


def test_worldcup_group_points_computed_end_to_end_from_espn_feed():
    la = ZoneInfo("America/Los_Angeles")
    payload = {"events": [
        _wc_group_event("1", "2026-06-11T19:00Z", "FIFA World Cup, Group A", "MEX", 2, "RSA", 0),
        _wc_group_event("2", "2026-06-12T19:00Z", "FIFA World Cup, Group A", "KOR", 2, "CZE", 1),
        _wc_group_event("3", "2026-06-17T19:00Z", "FIFA World Cup, Group A", "MEX", None, "KOR", None, finished=False),
    ]}
    events = SportsDashboard._parse_worldcup_espn_events(payload, la)
    SportsDashboard._annotate_worldcup_group_points(events)
    by_id = {e["event_id"]: e for e in events}

    # Finished MEX 2-0 RSA -> winners get 3, losers 0.
    assert SportsDashboard._worldcup_group_points_label(by_id["1"], "a") == "PTS 3"  # MEX
    assert SportsDashboard._worldcup_group_points_label(by_id["1"], "b") == "PTS 0"  # RSA
    # Upcoming MEX vs KOR shows each side's accumulated group points and W-D-L (both won once).
    assert SportsDashboard._worldcup_group_points_label(by_id["3"], "a") == "PTS 3"  # MEX
    assert SportsDashboard._worldcup_group_points_label(by_id["3"], "b") == "PTS 3"  # KOR
    assert SportsDashboard._worldcup_group_record_label(by_id["3"], "a") == "1-0-0"  # MEX
    assert SportsDashboard._worldcup_group_record_label(by_id["3"], "b") == "1-0-0"  # KOR


def _sample_worldcup_standings_payload():
    def entry(abbr, name, points, wins=2, draws=0, losses=0):
        return {
            "team": {"abbreviation": abbr, "displayName": name},
            "stats": [
                {"name": "wins", "value": float(wins), "displayValue": str(wins)},
                {"name": "draws", "value": float(draws), "displayValue": str(draws)},
                {"name": "losses", "value": float(losses), "displayValue": str(losses)},
                {"name": "points", "value": float(points), "displayValue": str(points)},
            ],
        }
    return {
        "children": [
            {"name": "Group A", "standings": {"entries": [
                entry("MEX", "Mexico", 6, wins=2, draws=0, losses=0),
                entry("KOR", "Korea Republic", 3, wins=1, draws=0, losses=1),
                entry("RSA", "South Africa", 1, wins=0, draws=1, losses=1),
                entry("CZE", "Czechia", 0, wins=0, draws=0, losses=2),
            ]}},
            {"name": "Group L", "standings": {"entries": [
                entry("BRA", "Brazil", 4),
            ]}},
        ]
    }


def test_parse_worldcup_standings_indexes_points_by_group_and_alias():
    lookup = SportsDashboard._parse_worldcup_standings(_sample_worldcup_standings_payload())
    assert lookup[("Group A", "MEX")] == 6
    assert lookup[("Group A", "MEXICO")] == 6  # also keyed by display name
    assert lookup[("Group A", "CZE")] == 0
    assert lookup[("Group L", "BRA")] == 4  # 12-group (A-L) format


def test_parse_worldcup_standings_indexes_records_by_group_and_alias():
    lookup = SportsDashboard._parse_worldcup_standings_records(_sample_worldcup_standings_payload())
    assert lookup[("Group A", "MEX")] == "2-0-0"
    assert lookup[("Group A", "MEXICO")] == "2-0-0"
    assert lookup[("Group A", "RSA")] == "0-1-1"
def test_worldcup_standings_default_cache_refreshes_after_one_hour(tmp_path, monkeypatch):
    plugin = _plugin()
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    assert DEFAULT_WORLD_CUP_STANDINGS_CACHE_HOURS == 1
    cache_key = "|".join([WORLD_CUP_STANDINGS_STATE_VERSION, DEFAULT_WORLD_CUP_STANDINGS_URL])
    SportsDashboard._write_json_file(
        tmp_path / "worldcup_standings.json",
        {
            "version": WORLD_CUP_STANDINGS_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": (datetime.now(timezone.utc) - timedelta(minutes=70)).isoformat(),
            "standings": {"children": []},
        },
    )
    fresh_payload = _sample_worldcup_standings_payload()
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return fresh_payload

    class FakeSession:
        def get(self, *args, **kwargs):
            calls.append((args, kwargs))
            return FakeResponse()

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    assert plugin._load_worldcup_standings({}) == fresh_payload
    assert len(calls) == 1

def test_apply_worldcup_standings_populates_authoritative_pts():
    events = [{
        "block": "Group A", "state": "TIMED",
        "team_a": "Mexico", "team_a_tla": "MEX",
        "team_b": "Korea", "team_b_tla": "KOR",
        "wins_a": None, "wins_b": None,
    }]
    lookup = SportsDashboard._parse_worldcup_standings(_sample_worldcup_standings_payload())
    record_lookup = SportsDashboard._parse_worldcup_standings_records(_sample_worldcup_standings_payload())
    SportsDashboard._apply_worldcup_standings(events, lookup, record_lookup)
    assert SportsDashboard._worldcup_group_points_label(events[0], "a") == "PTS 6"
    assert SportsDashboard._worldcup_group_points_label(events[0], "b") == "PTS 3"
    assert SportsDashboard._worldcup_group_record_label(events[0], "a") == "2-0-0"
    assert SportsDashboard._worldcup_group_record_label(events[0], "b") == "1-0-1"


def test_worldcup_standings_give_correct_pts_even_with_no_finished_matches_in_window():
    # The whole point: an upcoming match whose group's earlier results have aged
    # out of the scoreboard window still shows the true cumulative PTS.
    events = [{
        "block": "Group A", "state": "TIMED",
        "team_a": "Mexico", "team_a_tla": "MEX",
        "team_b": "South Africa", "team_b_tla": "RSA",
        "wins_a": None, "wins_b": None,
    }]
    lookup = SportsDashboard._parse_worldcup_standings(_sample_worldcup_standings_payload())
    SportsDashboard._apply_worldcup_standings(events, lookup)
    SportsDashboard._annotate_worldcup_group_points(events)  # local tally finds nothing finished
    assert SportsDashboard._worldcup_group_points_label(events[0], "a") == "PTS 6"  # from standings
    assert SportsDashboard._worldcup_group_points_label(events[0], "b") == "PTS 1"


def test_worldcup_standings_override_incomplete_local_tally():
    # Window shows only one finished MEX match (local tally would say 3), but the
    # authoritative standings say MEX already has 6 across the full group stage.
    events = [{
        "block": "Group A", "state": "FT",
        "team_a": "Mexico", "team_a_tla": "MEX",
        "team_b": "South Africa", "team_b_tla": "RSA",
        "wins_a": 2, "wins_b": 0,
    }]
    lookup = SportsDashboard._parse_worldcup_standings(_sample_worldcup_standings_payload())
    SportsDashboard._apply_worldcup_standings(events, lookup)
    SportsDashboard._annotate_worldcup_group_points(events)
    assert SportsDashboard._worldcup_group_points_label(events[0], "a") == "PTS 6"  # authoritative, not local 3


def test_worldcup_api_season_can_follow_football_data_season_for_history():
    assert SportsDashboard._worldcup_api_season({"footballDataSeason": "2022"}) == "2022"
    assert (
        SportsDashboard._worldcup_api_season(
            {"footballDataSeason": "2022", "worldCupApiSeason": "2024"}
        )
        == "2024"
    )


def test_worldcup_football_data_and_api_parsers_read_extra_time_and_penalties():
    la = ZoneInfo("America/Los_Angeles")
    match = _sample_football_data_match()
    match["status"] = "FINISHED"
    match["score"] = {
        "fullTime": {"home": 1, "away": 1},
        "extraTime": {"home": 0, "away": 0},
        "penalties": {"home": 4, "away": 3},
    }

    football_data_event = SportsDashboard._parse_football_data_events([match], la)[0]

    assert football_data_event["wins_a"] == 1
    assert football_data_event["wins_b"] == 1
    assert football_data_event["extra_time_score_a"] == 0
    assert football_data_event["extra_time_score_b"] == 0
    assert football_data_event["penalty_score_a"] == 4
    assert football_data_event["penalty_score_b"] == 3
    assert SportsDashboard._worldcup_side_period_score_label(football_data_event, "a") == "ET 0/P4"
    assert SportsDashboard._worldcup_side_period_score_label(football_data_event, "b") == "P3/ET 0"

    fixture = _sample_worldcup_fixture()
    fixture["fixture"]["status"] = {"short": "PEN", "long": "Match Finished", "elapsed": 120}
    fixture["goals"] = {"home": 2, "away": 2}
    fixture["score"] = {
        "fulltime": {"home": 2, "away": 2},
        "extratime": {"home": 1, "away": 1},
        "penalty": {"home": 5, "away": 4},
    }

    api_event = SportsDashboard._parse_worldcup_api_events([fixture], la)[0]

    assert api_event["wins_a"] == 2
    assert api_event["wins_b"] == 2
    assert api_event["extra_time_score_a"] == 1
    assert api_event["extra_time_score_b"] == 1
    assert api_event["penalty_score_a"] == 5
    assert api_event["penalty_score_b"] == 4

def test_worldcup_espn_parser_reads_finished_and_live_scores():
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_worldcup_espn_events(_sample_worldcup_espn_scoreboard_payload(), la)

    assert len(events) == 2
    assert events[0]["event_id"] == "760415"
    assert events[0]["state"] == "FT"
    assert events[0]["team_a"] == "\u58a8\u897f\u54e5"
    assert events[0]["team_b"] == "南非"
    assert events[0]["team_a_tla"] == "MEX"
    assert events[0]["team_b_tla"] == "RSA"
    assert events[0]["wins_a"] == 2
    assert events[0]["wins_b"] == 0
    assert events[0]["score_source"] == "ESPN"
    assert events[0]["provider"] == "ESPN"
    assert events[0]["source_url"] == "https://www.espn.com/soccer/match/_/gameId/760415/mex-rsa"
    assert events[0]["provider_status_confirmed"] is True
    assert events[0]["score_confirmed"] is True
    assert events[0]["team_a_advance"] is True
    assert events[0]["team_b_advance"] is False
    assert SportsDashboard._worldcup_team_eliminated(events[0], "b") is False
    assert events[1]["state"] == "1H"
    assert events[1]["source_url"] == "https://www.espn.com/soccer/gamecast/_/gameId/760414/kor-cze"
    assert events[1]["team_a"] == "韩国"
    assert events[1]["team_b"] == "捷克"
    assert events[1]["elapsed"] == 9
    assert SportsDashboard._worldcup_event_status_label(events[1], datetime(2026, 6, 11, 19, 10, tzinfo=la)) == "9' 0-0"


def test_worldcup_espn_parser_reads_extra_time_and_penalty_scores():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_worldcup_espn_scoreboard_payload()))
    event = payload["events"][0]
    competition = event["competitions"][0]
    competition["altGameNote"] = "FIFA World Cup, Round of 32"
    competition["status"]["period"] = 5
    competition["status"]["type"].update(
        {
            "name": "STATUS_FINAL_PEN",
            "description": "Final Penalties",
            "shortDetail": "PEN",
            "detail": "PEN",
        }
    )
    home = competition["competitors"][0]
    away = competition["competitors"][1]
    home["score"] = "2"
    away["score"] = "2"
    home["penaltyKickScore"] = "4"
    away["penaltyKickScore"] = "3"
    home["team"]["id"] = "203"
    away["team"]["id"] = "799"
    competition["details"] = [
        {"clock": {"displayValue": "105'"}, "team": {"id": "203"}, "scoringPlay": True, "scoreValue": 1, "shootout": False},
        {"clock": {"displayValue": "118'"}, "team": {"id": "799"}, "scoringPlay": True, "scoreValue": 1, "shootout": False},
    ]

    parsed = SportsDashboard._parse_worldcup_espn_events(payload, la)[0]

    assert parsed["state"] == "PEN"
    assert parsed["wins_a"] == 2
    assert parsed["wins_b"] == 2
    assert parsed["extra_time_score_a"] == 1
    assert parsed["extra_time_score_b"] == 1
    assert parsed["penalty_score_a"] == 4
    assert parsed["penalty_score_b"] == 3
    assert SportsDashboard._worldcup_side_period_score_label(parsed, "a") == "ET 1/P4"
    assert SportsDashboard._worldcup_side_period_score_label(parsed, "b") == "P3/ET 1"

def test_worldcup_espn_parser_marks_eliminated_knockout_loser():
    la = ZoneInfo("America/Los_Angeles")
    payload = _sample_worldcup_espn_scoreboard_payload()
    event = payload["events"][0]
    event["season"] = {"slug": "round-of-32"}
    event["competitions"][0]["altGameNote"] = "FIFA World Cup, Round of 32"

    events = SportsDashboard._parse_worldcup_espn_events(payload, la)

    assert events[0]["block"] == "Round of 32"
    assert events[0]["team_a_advance"] is True
    assert events[0]["team_b_advance"] is False
    assert SportsDashboard._worldcup_team_eliminated(events[0], "a") is False
    assert SportsDashboard._worldcup_team_eliminated(events[0], "b") is True


def test_worldcup_real_espn_knockout_stage_labels_are_detected():
    for stage in ("Quarterfinals", "Semifinals", "3rd-Place Match"):
        event = {
            "block": stage,
            "state": "FT",
            "team_a_advance": True,
            "team_b_advance": False,
        }

        assert SportsDashboard._worldcup_is_knockout_stage_event(event) is True, stage
        assert SportsDashboard._worldcup_team_eliminated(event, "a") is False, stage
        assert SportsDashboard._worldcup_team_eliminated(event, "b") is True, stage


def _worldcup_espn_scheduled_competition(detail):
    return {
        "status": {
            "period": 0,
            "type": {
                "state": "pre",
                "completed": False,
                "name": "STATUS_SCHEDULED",
                "description": "Scheduled",
                "detail": detail,
                "shortDetail": detail,
            },
        }
    }


def test_worldcup_espn_state_does_not_read_date_ordinals_as_live_halves():
    # ESPN reports a scheduled match's kickoff as a human date in ``detail``. Its ordinal day
    # suffixes ("21st" -> "1ST", "22nd" -> "2ND") must never be interpreted as first/second
    # half, otherwise upcoming fixtures masquerade as live games.
    state = SportsDashboard._worldcup_espn_event_state
    assert state({}, _worldcup_espn_scheduled_competition("Sun, June 21st at 12:00 PM EDT")) == "TIMED"
    assert state({}, _worldcup_espn_scheduled_competition("Mon, June 22nd at 1:00 PM EDT")) == "TIMED"
    assert state({}, _worldcup_espn_scheduled_competition("Wed, June 1st at 3:00 PM EDT")) == "TIMED"
    assert state({}, _worldcup_espn_scheduled_competition("Thu, June 18th at 12:00 PM EDT")) == "TIMED"
    # Genuinely in-progress matches must still resolve to their live sub-state.
    live_2h = {"status": {"type": {"state": "in", "name": "STATUS_SECOND_HALF", "description": "Second Half", "detail": "62'"}}}
    live_1h = {"status": {"type": {"state": "in", "name": "STATUS_FIRST_HALF", "description": "First Half", "detail": "9'"}}}
    assert state({}, live_2h) == "2H"
    assert state({}, live_1h) == "1H"


def test_worldcup_future_scheduled_matches_do_not_hijack_the_live_main_card():
    la = ZoneInfo("America/Los_Angeles")
    payload = {
        "events": [
            {
                "id": "live-arg-alg",
                "date": "2026-06-17T01:00Z",
                "competitions": [
                    {
                        "id": "live-arg-alg",
                        "date": "2026-06-17T01:00Z",
                        "status": {"period": 2, "type": {"state": "in", "completed": False, "name": "STATUS_HALFTIME", "description": "Halftime", "detail": "HT", "shortDetail": "HT"}},
                        "competitors": [
                            {"homeAway": "home", "score": "1", "team": {"abbreviation": "ARG", "displayName": "Argentina"}},
                            {"homeAway": "away", "score": "0", "team": {"abbreviation": "ALG", "displayName": "Algeria"}},
                        ],
                    }
                ],
            },
            {
                "id": "sched-ksa-esp",
                "date": "2026-06-21T16:00Z",
                "competitions": [
                    {
                        "id": "sched-ksa-esp",
                        "date": "2026-06-21T16:00Z",
                        "status": {"period": 0, "type": {"state": "pre", "completed": False, "name": "STATUS_SCHEDULED", "description": "Scheduled", "detail": "Sun, June 21st at 12:00 PM EDT", "shortDetail": "6/21 - 12:00 PM EDT"}},
                        "competitors": [
                            {"homeAway": "home", "score": "0", "team": {"abbreviation": "ESP", "displayName": "Spain"}},
                            {"homeAway": "away", "score": "0", "team": {"abbreviation": "KSA", "displayName": "Saudi Arabia"}},
                        ],
                    }
                ],
            },
            {
                "id": "sched-aut-arg",
                "date": "2026-06-22T17:00Z",
                "competitions": [
                    {
                        "id": "sched-aut-arg",
                        "date": "2026-06-22T17:00Z",
                        "status": {"period": 0, "type": {"state": "pre", "completed": False, "name": "STATUS_SCHEDULED", "description": "Scheduled", "detail": "Mon, June 22nd at 1:00 PM EDT", "shortDetail": "6/22 - 1:00 PM EDT"}},
                        "competitors": [
                            {"homeAway": "home", "score": "0", "team": {"abbreviation": "ARG", "displayName": "Argentina"}},
                            {"homeAway": "away", "score": "0", "team": {"abbreviation": "AUT", "displayName": "Austria"}},
                        ],
                    }
                ],
            },
        ]
    }
    events = SportsDashboard._parse_worldcup_espn_events(payload, la)
    now = datetime(2026, 6, 16, 18, 30, tzinfo=la)  # during the ARG vs ALG halftime

    selected = SportsDashboard._select_worldcup_event_sections(events, now, 3)

    assert [event["state"] for event in events if event["event_id"].startswith("sched")] == ["TIMED", "TIMED"]
    assert [event["event_id"] for event in selected["live"]] == ["live-arg-alg"]
    assert selected["main"]["event_id"] == "live-arg-alg"
    assert all(not event["event_id"].startswith("sched") for event in selected["live"])


def test_worldcup_scoreboard_overlay_updates_football_data_scores():
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_football_data_events([_sample_football_data_match()], la)
    scoreboard_events = SportsDashboard._parse_worldcup_espn_events(_sample_worldcup_espn_scoreboard_payload(), la)

    merged, attached_count = SportsDashboard._merge_worldcup_scoreboard_events(events, scoreboard_events)

    assert attached_count == 1
    assert merged[0]["state"] == "FT"
    assert merged[0]["wins_a"] == 2
    assert merged[0]["wins_b"] == 0
    assert merged[0]["score_source"] == "ESPN"
    assert merged[0]["source_url"] == "https://www.espn.com/soccer/match/_/gameId/760415/mex-rsa"
    assert merged[0]["provider_status_confirmed"] is True
    assert merged[0]["score_confirmed"] is True
    assert SportsDashboard._worldcup_event_status_label(merged[0], datetime(2026, 6, 11, 14, 0, tzinfo=la)) == "2-0"


def test_worldcup_scoreboard_overlay_carries_reversed_extra_time_and_penalties():
    la = ZoneInfo("America/Los_Angeles")
    match = _sample_football_data_match()
    match["homeTeam"], match["awayTeam"] = match["awayTeam"], match["homeTeam"]
    events = SportsDashboard._parse_football_data_events([match], la)
    scoreboard_events = SportsDashboard._parse_worldcup_espn_events(_sample_worldcup_espn_scoreboard_payload(), la)
    scoreboard_events[0]["extra_time_score_a"] = 1
    scoreboard_events[0]["extra_time_score_b"] = 0
    scoreboard_events[0]["penalty_score_a"] = 4
    scoreboard_events[0]["penalty_score_b"] = 3

    merged, attached_count = SportsDashboard._merge_worldcup_scoreboard_events(events, scoreboard_events)

    assert attached_count == 1
    assert merged[0]["team_a_tla"] == "RSA"
    assert merged[0]["team_b_tla"] == "MEX"
    assert merged[0]["wins_a"] == 0
    assert merged[0]["wins_b"] == 2
    assert merged[0]["extra_time_score_a"] == 0
    assert merged[0]["extra_time_score_b"] == 1
    assert merged[0]["penalty_score_a"] == 3
    assert merged[0]["penalty_score_b"] == 4

def test_worldcup_main_status_label_marks_verified_score_source_only_when_confirmed():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 11, 14, 0, tzinfo=la)
    final = {
        "start": datetime(2026, 6, 11, 12, 0, tzinfo=la),
        "state": "FT",
        "status": "Final",
        "wins_a": 2,
        "wins_b": 0,
        "score_source": "ESPN",
        "provider_status_confirmed": True,
        "score_confirmed": True,
    }
    scheduled = dict(final, state="TIMED", wins_a=None, wins_b=None, score_confirmed=False)
    inferred_live = dict(
        scheduled,
        state="TIMED",
        inferred_live=True,
        provider_status_confirmed=False,
        score_confirmed=False,
    )

    assert SportsDashboard._worldcup_main_status_label(final, now) == "ESPN 2-0"
    assert SportsDashboard._worldcup_main_status_label(scheduled, now) == "12:00"
    assert SportsDashboard._worldcup_main_status_label(inferred_live, now) == "LIVE"


def test_worldcup_main_card_uses_uniform_height_flag_slots():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = {
        "start": datetime(2026, 6, 18, 14, 0, tzinfo=la),
        "state": "LIVE",
        "status": "45'+6'",
        "team_a": "\u52a0\u62ff\u5927",
        "team_b": "\u5361\u5854\u5c14",
        "team_a_tla": "CAN",
        "team_b_tla": "QAT",
        "team_a_flag": "https://flagcdn.com/w80/ca.png",
        "team_b_flag": "https://flagcdn.com/w80/qa.png",
        "wins_a": 3,
        "wins_b": 0,
        "block": "Group B",
    }
    image = Image.new("RGB", (360, 170), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    flag_slots = []

    def record_flag(_image, _draw, _flag_url, _x, _y, width, height, _fallback, align="left"):
        flag_slots.append((width, height))
        return width

    plugin._draw_worldcup_flag = record_flag

    plugin._draw_worldcup_main_card(image, draw, 0, 0, 359, 169, event, datetime(2026, 6, 18, 14, 45, tzinfo=la), "live")

    assert flag_slots == [(54, 27), (54, 27)]


def test_worldcup_main_card_meta_uses_mirrored_points_and_record():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = {
        "start": datetime(2026, 6, 18, 14, 0, tzinfo=la),
        "state": "LIVE",
        "status": "45+6",
        "team_a": "USA",
        "team_b": "Australia",
        "team_a_tla": "USA",
        "team_b_tla": "AUS",
        "team_a_flag": "https://flagcdn.com/w80/us.png",
        "team_b_flag": "https://flagcdn.com/w80/au.png",
        "team_a_group_record": "1-0-0",
        "team_b_group_record": "0-1-0",
        "team_a_group_points": 3,
        "team_b_group_points": 1,
        "wins_a": 2,
        "wins_b": 0,
        "block": "Group D",
    }
    image = Image.new("RGB", (360, 170), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    calls = []

    plugin._draw_worldcup_flag = lambda *_args, **_kwargs: 54

    def record_meta(_draw, box, text, max_size=11):
        calls.append((tuple(int(value) for value in box), text, max_size))

    plugin._draw_worldcup_odds_text = record_meta

    plugin._draw_worldcup_main_card(image, draw, 0, 0, 359, 169, event, datetime(2026, 6, 18, 14, 45, tzinfo=la), "live")

    left_box, left_text, left_max_size = calls[0]
    right_box, right_text, right_max_size = calls[1]
    assert left_box[2] - left_box[0] == right_box[2] - right_box[0]
    assert left_box[0] + right_box[2] == left_box[2] + right_box[0]
    assert left_text == "PTS 3 / 1-0-0"
    assert right_text == "0-1-0 / PTS 1"
    assert left_max_size == 8
    assert right_max_size == 8


def test_worldcup_team_points_meta_hides_points_for_knockout_stage():
    event = {
        "block": "Round of 32",
        "team_a_group_record": "2-0-1",
        "team_b_group_record": "1-1-1",
        "team_a_group_points": 6,
        "team_b_group_points": 4,
        "odds": {"team_a": "2.20", "draw": "3.10", "team_b": "3.40"},
    }

    assert SportsDashboard._worldcup_is_group_stage_event(event) is False
    assert SportsDashboard._worldcup_team_points_meta(event, "a") == ""
    assert SportsDashboard._worldcup_team_points_meta(event, "b") == ""
    assert SportsDashboard._worldcup_team_points_meta(event, "a", include_odds=True) == "2.20"
    assert SportsDashboard._worldcup_team_points_meta(event, "b", include_odds=True) == "3.40"


def test_worldcup_team_points_meta_shows_points_for_group_stage_alias():
    event = {
        "block": "GROUP_STAGE",
        "group": "GROUP_A",
        "team_a_group_record": "1-1-0",
        "team_b_group_record": "0-1-1",
        "team_a_group_points": 4,
        "team_b_group_points": 1,
    }

    assert SportsDashboard._worldcup_is_group_stage_event(event) is True
    assert SportsDashboard._worldcup_team_points_meta(event, "a") == "PTS 4 / 1-1-0"
    assert SportsDashboard._worldcup_team_points_meta(event, "b") == "0-1-1 / PTS 1"

def test_worldcup_main_card_draws_verified_source_in_status_line():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = {
        "start": datetime(2026, 6, 11, 12, 0, tzinfo=la),
        "state": "FT",
        "status": "Final",
        "team_a": "\u58a8\u897f\u54e5",
        "team_b": "\u5357\u975e",
        "team_a_tla": "MEX",
        "team_b_tla": "RSA",
        "team_a_flag": "",
        "team_b_flag": "",
        "wins_a": 2,
        "wins_b": 0,
        "block": "Group A",
        "score_source": "ESPN",
        "provider_status_confirmed": True,
        "score_confirmed": True,
    }
    image = Image.new("RGB", (320, 180), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    plugin._draw_worldcup_flag = lambda *_args, **_kwargs: None

    plugin._draw_worldcup_main_card(image, draw, 0, 0, 300, 150, event, datetime(2026, 6, 11, 14, 0, tzinfo=la), "recent")

    assert "ESPN 2-0" in seen_texts


def test_worldcup_scoreboard_source_label_identifies_overlay():
    label = SportsDashboard._worldcup_api_source_label(
        "FOOTBALL LIVE + ESPN LIVE",
        "2026-06-11T19:12:00+00:00",
    )

    assert label.startswith("FD+ESPN")


def test_worldcup_fresh_source_label_does_not_claim_match_is_live():
    label = SportsDashboard._worldcup_api_source_label(
        "ESPN LIVE",
        "2026-07-11T01:51:12+00:00",
    )

    assert label.startswith("ESPN DATA")
    assert "LIVE" not in label


def test_worldcup_completed_only_selection_uses_recent_mode_and_year():
    recent = {
        "start": datetime(2022, 12, 18, 18, 0, tzinfo=timezone.utc),
        "state": "FT",
        "status": "Match Finished",
        "team_a": "Argentina",
        "team_b": "France",
        "team_a_tla": "ARG",
        "team_b_tla": "FRA",
        "wins_a": 3,
        "wins_b": 3,
        "block": "Final",
    }

    selected = SportsDashboard._select_worldcup_event_sections(
        [recent],
        datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc),
        4,
    )

    assert selected["main"] is recent
    assert SportsDashboard._worldcup_main_mode(selected, recent) == "recent"
    assert SportsDashboard._worldcup_title_year(selected) == "2022"


def test_worldcup_started_timed_match_is_inferred_live_not_recent():
    la = ZoneInfo("America/Los_Angeles")
    event = {
        "event_id": "ned-jpn",
        "start": datetime(2026, 6, 14, 12, 0, tzinfo=la),
        "state": "TIMED",
        "status": "Timed",
        "team_a": "荷兰",
        "team_b": "日本",
        "team_a_tla": "NED",
        "team_b_tla": "JPN",
        "wins_a": None,
        "wins_b": None,
        "block": "Group F",
    }

    selected = SportsDashboard._select_worldcup_event_sections(
        [event],
        datetime(2026, 6, 14, 13, 28, tzinfo=la),
        4,
    )

    assert selected["live"] == [event]
    assert selected["recent"] == []
    assert selected["main"] is event
    assert event["inferred_live"] is True
    assert SportsDashboard._worldcup_main_mode(selected, event) == "live"
    assert SportsDashboard._worldcup_event_status_label(event, datetime(2026, 6, 14, 13, 28, tzinfo=la)) == "LIVE"


def test_worldcup_started_timed_match_moves_to_recent_after_live_window():
    la = ZoneInfo("America/Los_Angeles")
    event = {
        "event_id": "ned-jpn",
        "start": datetime(2026, 6, 14, 12, 0, tzinfo=la),
        "state": "TIMED",
        "status": "Timed",
        "team_a": "荷兰",
        "team_b": "日本",
        "team_a_tla": "NED",
        "team_b_tla": "JPN",
        "wins_a": None,
        "wins_b": None,
        "block": "Group F",
    }

    selected = SportsDashboard._select_worldcup_event_sections(
        [event],
        datetime(2026, 6, 14, 15, 1, tzinfo=la),
        4,
    )

    assert selected["live"] == []
    assert selected["recent"] == [event]
    assert event.get("inferred_live") is None


def test_worldcup_expired_explicit_live_state_does_not_block_new_live_match():
    la = ZoneInfo("America/Los_Angeles")
    stale_live = {
        "event_id": "ger-cuw",
        "start": datetime(2026, 6, 14, 10, 0, tzinfo=la),
        "state": "2H",
        "status": "80'",
        "team_a": "Germany",
        "team_b": "Curacao",
        "team_a_tla": "GER",
        "team_b_tla": "CUW",
        "wins_a": 6,
        "wins_b": 1,
        "block": "Group E",
    }
    current = {
        "event_id": "ned-jpn",
        "start": datetime(2026, 6, 14, 13, 0, tzinfo=la),
        "state": "TIMED",
        "status": "13:00",
        "team_a": "Netherlands",
        "team_b": "Japan",
        "team_a_tla": "NED",
        "team_b_tla": "JPN",
        "wins_a": None,
        "wins_b": None,
        "block": "Group F",
    }

    selected = SportsDashboard._select_worldcup_event_sections(
        [stale_live, current],
        datetime(2026, 6, 14, 13, 50, tzinfo=la),
        4,
    )

    assert selected["live"] == [current]
    assert selected["recent"] == [stale_live]
    assert selected["main"] is current
    assert current["inferred_live"] is True
    assert SportsDashboard._worldcup_main_mode(selected, current) == "live"
    assert SportsDashboard._worldcup_event_status_label(current, datetime(2026, 6, 14, 13, 50, tzinfo=la)) == "LIVE"
    assert SportsDashboard._worldcup_event_status_label(stale_live, datetime(2026, 6, 14, 13, 50, tzinfo=la)) == "10:00"


def test_worldcup_group_points_are_inferred_from_completed_group_matches():
    now = datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc)
    completed = {
        "start": datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
        "state": "FT",
        "team_a": "Mexico",
        "team_b": "South Africa",
        "team_a_tla": "MEX",
        "team_b_tla": "RSA",
        "wins_a": 2,
        "wins_b": 1,
        "block": "Group A",
    }
    upcoming = {
        "start": datetime(2026, 6, 13, 19, 0, tzinfo=timezone.utc),
        "state": "TIMED",
        "team_a": "Mexico",
        "team_b": "Qatar",
        "team_a_tla": "MEX",
        "team_b_tla": "QAT",
        "wins_a": None,
        "wins_b": None,
        "block": "Group A",
    }

    SportsDashboard._select_worldcup_event_sections([completed, upcoming], now, 4)

    assert completed["team_a_group_points"] == 3
    assert completed["team_b_group_points"] == 0
    assert upcoming["team_a_group_points"] == 3
    assert upcoming["team_b_group_points"] == 0


def test_worldcup_scoreboard_uses_cached_football_data_groups_for_points(tmp_path):
    plugin = _plugin()
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    la = ZoneInfo("America/Los_Angeles")
    plugin._write_json_file(
        plugin._football_data_cache_path(),
        {
            "version": "sports-dashboard-football-data-v1",
            "matches": [
                {
                    "utcDate": "2026-06-14T20:00:00Z",
                    "status": "FINISHED",
                    "stage": "GROUP_STAGE",
                    "group": "GROUP_F",
                    "homeTeam": {"name": "Netherlands", "shortName": "Netherlands", "tla": "NED"},
                    "awayTeam": {"name": "Japan", "shortName": "Japan", "tla": "JPN"},
                    "score": {"fullTime": {"home": 2, "away": 2}},
                }
            ],
        },
    )
    events = [
        {
            "event_id": "ned-jpn",
            "start": datetime(2026, 6, 14, 13, 0, tzinfo=la),
            "state": "FT",
            "status": "FT",
            "team_a": "荷兰",
            "team_b": "日本",
            "team_a_tla": "NED",
            "team_b_tla": "JPN",
            "team_a_source_name": "Netherlands",
            "team_b_source_name": "Japan",
            "team_a_source_aliases": ["Netherlands", "NED"],
            "team_b_source_aliases": ["Japan", "JPN"],
            "wins_a": 2,
            "wins_b": 2,
            "block": "GROUP STAGE",
        }
    ]

    enriched = plugin._attach_worldcup_group_blocks_from_cached_football_data(events, la)
    selected = SportsDashboard._select_worldcup_event_sections(
        enriched,
        datetime(2026, 6, 14, 14, 0, tzinfo=la),
        4,
    )

    assert enriched[0]["block"] == "Group F"
    assert selected["recent"][0]["team_a_group_points"] == 1
    assert selected["recent"][0]["team_b_group_points"] == 1


def test_worldcup_live_state_file_tracks_active_match():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("worldcup_live_state")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    now = datetime(2026, 6, 11, 12, 15, tzinfo=timezone.utc)
    event = {
        "event_id": "wc-mex-rsa",
        "start": datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc),
        "state": "IN_PLAY",
        "status": "In Play",
        "elapsed": 15,
        "team_a": "\u58a8\u897f\u54e5",
        "team_b": "\u5357\u975e",
        "wins_a": 1,
        "wins_b": 0,
        "block": "Group A",
        "provider": "ESPN",
        "score_source": "ESPN",
        "source_url": "https://www.espn.com/soccer/match/_/gameId/wc-mex-rsa",
        "provider_status_confirmed": True,
        "score_confirmed": True,
    }
    selected = SportsDashboard._select_worldcup_event_sections([event], now, 4)

    plugin._write_worldcup_live_state(selected, now, "FOOTBALL LIVE")
    state = json.loads((tmp_path / "worldcup_live_state.json").read_text(encoding="utf-8"))

    assert state["version"] == "sports-dashboard-worldcup-live-v1"
    assert state["has_live"] is True
    assert state["team_a"] == "\u58a8\u897f\u54e5"
    assert state["team_b"] == "\u5357\u975e"
    assert state["score"] == "1-0"
    assert state["live_until"] == "2026-06-11T15:00:00+00:00"
    assert state["provider"] == "ESPN"
    assert state["score_source"] == "ESPN"
    assert state["source_url"] == "https://www.espn.com/soccer/match/_/gameId/wc-mex-rsa"
    assert state["provider_status_confirmed"] is True
    assert state["score_confirmed"] is True


def test_worldcup_live_state_bridges_back_to_back_match_refresh_window():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("worldcup_live_state_back_to_back")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    now = datetime(2026, 6, 29, 21, 49, tzinfo=timezone.utc)
    current = {
        "event_id": "ger-par",
        "start": datetime(2026, 6, 29, 20, 30, tzinfo=timezone.utc),
        "state": "2H",
        "status": "54'",
        "elapsed": 54,
        "team_a": "Germany",
        "team_b": "Paraguay",
        "wins_a": 0,
        "wins_b": 1,
        "block": "Round of 32",
        "provider": "ESPN",
        "score_source": "ESPN",
    }
    next_match = {
        "event_id": "ned-mar",
        "start": datetime(2026, 6, 29, 23, 30, tzinfo=timezone.utc),
        "state": "TIMED",
        "status": "23:30",
        "team_a": "Netherlands",
        "team_b": "Morocco",
        "wins_a": None,
        "wins_b": None,
        "block": "Round of 32",
    }
    selected = SportsDashboard._select_worldcup_event_sections([current, next_match], now, 4)

    plugin._write_worldcup_live_state(selected, now, "ESPN LIVE")
    state = json.loads((tmp_path / "worldcup_live_state.json").read_text(encoding="utf-8"))

    assert selected["live"] == [current]
    assert selected["upcoming"] == [next_match]
    assert state["has_live"] is True
    assert state["event_id"] == "ger-par"
    assert state["live_until"] == "2026-06-30T02:30:00+00:00"

def test_worldcup_fallback_renders_compact_four_match_list():
    plugin = _plugin()

    image = plugin._render_worldcup_fallback((800, 208), 4)

    assert image.size == (800, 208)
    assert image.getpixel((18, 64)) != COLORS["paper"]
    assert image.getpixel((30, 190)) != COLORS["paper"]


def test_worldcup_compact_panel_draws_recent_section_in_bottom_gap(monkeypatch):
    plugin = _plugin()
    now = datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc)

    def event(day, state, team_a, team_b, wins_a=None, wins_b=None):
        return {
            "start": datetime(2026, 6, day, 19, 0, tzinfo=timezone.utc),
            "state": state,
            "status": state,
            "team_a": team_a,
            "team_b": team_b,
            "team_a_tla": team_a[:3].upper(),
            "team_b_tla": team_b[:3].upper(),
            "team_a_flag": "",
            "team_b_flag": "",
            "wins_a": wins_a,
            "wins_b": wins_b,
            "block": "Group Stage",
        }

    main = event(13, "TIMED", "USA", "Mexico")
    second = event(14, "TIMED", "Brazil", "Morocco")
    third = event(15, "TIMED", "Canada", "Qatar")
    recent = event(11, "FT", "Mexico", "South Africa", 2, 1)
    selected = {
        "live": [],
        "upcoming": [main, second, third],
        "recent": [recent],
        "main": main,
        "visible_matches": 4,
    }
    calls = []
    original = plugin._draw_worldcup_recent_rows

    def record_recent_rows(image, draw, x1, x2, y, bottom, events):
        calls.append({"x1": x1, "y": y, "bottom": bottom, "events": list(events)})
        return original(image, draw, x1, x2, y, bottom, events)

    monkeypatch.setattr(plugin, "_draw_worldcup_recent_rows", record_recent_rows)

    image = plugin._render_worldcup_api_panel((556, 208), selected, "FOOTBALL LIVE", now, 4, now)

    assert calls
    assert calls[0]["events"] == [recent]
    assert calls[0]["y"] <= calls[0]["bottom"] - 38
    assert image.getpixel((calls[0]["x1"] + 4, calls[0]["y"] + 5)) == COLORS["worldcup_accent"]


def test_worldcup_api_parser_converts_fixture_to_local_match_row():
    la = ZoneInfo("America/Los_Angeles")

    events = SportsDashboard._parse_worldcup_api_events([_sample_worldcup_fixture()], la)

    assert events[0]["start"].strftime("%Y-%m-%d %H:%M") == "2026-06-11 17:00"
    assert events[0]["team_a"] == "美国"
    assert events[0]["team_b"] == "\u58a8\u897f\u54e5"
    assert events[0]["state"] == "NS"
    assert events[0]["block"] == "Group Stage - 1"
    assert events[0]["fixture_id"] == "10101"


def test_worldcup_country_name_aliases_stay_simplified_chinese():
    la = ZoneInfo("America/Los_Angeles")
    fixture = json.loads(json.dumps(_sample_worldcup_fixture()))
    fixture["teams"]["home"] = {"name": "Germania"}
    fixture["teams"]["away"] = {"name": "Cura\u00e7ao"}

    events = SportsDashboard._parse_worldcup_api_events([fixture], la)

    assert SportsDashboard._localized_country_name({"name": "Deutschland"}, "") == "德国"
    assert "Germania" in SportsDashboard._country_aliases_for_value("德国")
    assert SportsDashboard._localized_country_name({"name": "Curacao"}, "") == "\u5e93\u62c9\u7d22"
    assert "Curacao" in SportsDashboard._country_aliases_for_value("\u5e93\u62c9\u7d22")
    assert events[0]["team_a"] == "德国"
    assert events[0]["team_b"] == "\u5e93\u62c9\u7d22"
    assert events[0]["team_a_tla"] == "GER"
    assert events[0]["team_b_tla"] == "CUW"
    assert "Germany" in events[0]["team_a_source_aliases"]
    assert "Curacao" in events[0]["team_b_source_aliases"]
    assert events[0]["team_a_flag"] == "https://flagcdn.com/w80/de.png"
    assert events[0]["team_b_flag"] == "https://flagcdn.com/w80/cw.png"
    assert SportsDashboard._localized_country_name({"name": "Cape Verde"}, "") == "\u4f5b\u5f97\u89d2"
    assert SportsDashboard._localized_country_name({"name": "Cabo Verde"}, "") == "\u4f5b\u5f97\u89d2"
    assert SportsDashboard._localized_country_name({"name": "Cabo-Verde"}, "") == "\u4f5b\u5f97\u89d2"
    assert SportsDashboard._localized_country_name({"name": "Cape Verde"}, "CVE") == "\u4f5b\u5f97\u89d2"
    assert SportsDashboard._canonical_country_tla("CVE") == "CPV"
    assert "Cabo Verde" in SportsDashboard._country_aliases_for_value("\u4f5b\u5f97\u89d2")
    assert SportsDashboard._flag_url_for_tla("CVE") == "https://flagcdn.com/w80/cv.png"


def test_worldcup_espn_parser_localizes_cape_verde():
    la = ZoneInfo("America/Los_Angeles")
    payload = {
        "events": [
            {
                "id": "esp-cpv",
                "date": "2026-06-15T16:00Z",
                "competitions": [
                    {
                        "id": "esp-cpv",
                        "date": "2026-06-15T16:00Z",
                        "status": {"type": {"state": "pre", "completed": False, "shortDetail": "12:00"}},
                        "competitors": [
                            {
                                "homeAway": "home",
                                "team": {
                                    "abbreviation": "ESP",
                                    "shortDisplayName": "Spain",
                                    "displayName": "Spain",
                                },
                            },
                            {
                                "homeAway": "away",
                                "team": {
                                    "abbreviation": "CPV",
                                    "shortDisplayName": "Cape Verde",
                                    "displayName": "Cape Verde",
                                },
                            },
                        ],
                    }
                ],
            }
        ]
    }

    events = SportsDashboard._parse_worldcup_espn_events(payload, la)

    assert events[0]["team_a"] == "\u897f\u73ed\u7259"
    assert events[0]["team_b"] == "\u4f5b\u5f97\u89d2"
    assert events[0]["team_b_tla"] == "CPV"
    assert events[0]["team_b_flag"] == "https://flagcdn.com/w80/cv.png"


def test_worldcup_espn_parser_preserves_embedded_moneyline_odds():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_worldcup_espn_scoreboard_payload()))
    payload["events"] = [payload["events"][0]]
    event = payload["events"][0]
    event["date"] = "2026-07-14T19:00Z"
    competition = event["competitions"][0]
    competition["date"] = event["date"]
    competition["altGameNote"] = "FIFA World Cup, Semifinals"
    home, away = competition["competitors"]
    home["team"].update(
        {"abbreviation": "FRA", "shortDisplayName": "France", "displayName": "France"}
    )
    away["team"].update(
        {"abbreviation": "ESP", "shortDisplayName": "Spain", "displayName": "Spain"}
    )
    competition["odds"] = [
        {
            "provider": {"name": "DraftKings", "displayName": "DraftKings"},
            "moneyline": {
                "home": {"close": {"odds": "+135"}},
                "draw": {"close": {"odds": "+225"}},
                "away": {"close": {"odds": "+215"}},
            },
        }
    ]

    parsed = SportsDashboard._parse_worldcup_espn_events(payload, la)[0]

    assert parsed["team_a"] == "\u6cd5\u56fd"
    assert parsed["team_b"] == "\u897f\u73ed\u7259"
    assert parsed["odds"] == {
        "team_a": "2.35",
        "draw": "3.25",
        "team_b": "3.15",
        "bookmaker": "DraftKings",
    }


def test_worldcup_lineups_attach_formation_summary_from_api_cache():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("worldcup_lineups")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    la = ZoneInfo("America/Los_Angeles")
    event = SportsDashboard._parse_worldcup_api_events([_sample_worldcup_fixture()], la)[0]
    selected = {"main": event}

    def fake_api(path, params, api_key, settings, now_utc):
        assert path == "/fixtures/lineups"
        assert params == {"fixture": "10101"}
        return {
            "response": [
                {"team": {"name": "United States"}, "formation": "4-3-3"},
                {"team": {"name": "Mexico"}, "formation": "4-2-3-1"},
            ]
        }

    plugin._api_football_get_json = fake_api
    plugin._attach_worldcup_lineup_summary(
        selected,
        {"worldCupLineupCacheSeconds": "600"},
        "secret",
        la,
        datetime(2026, 6, 11, 16, 30, tzinfo=la),
    )

    assert event["formation_a"] == "4-3-3"
    assert event["formation_b"] == "4-2-3-1"
    assert event["lineups_ready"] is True


def test_worldcup_formation_pair_includes_team_labels():
    pair = SportsDashboard._worldcup_formation_pair(
        {
            "team_a": "\u58a8\u897f\u54e5",
            "team_b": "\u5357\u975e",
            "formation_a": "4-3-3",
            "formation_b": "4-2-3-1",
        }
    )

    assert pair == ("\u58a8\u897f\u54e5 4-3-3", "4-2-3-1 \u5357\u975e")

    pair = SportsDashboard._worldcup_formation_pair(
        {
            "team_a": "United States",
            "team_a_tla": "USA",
            "team_b": "Netherlands",
            "team_b_tla": "NED",
            "formation_a": "4-3-3",
            "formation_b": "3-4-2-1",
        }
    )

    assert pair == ("USA 4-3-3", "3-4-2-1 NED")


def test_worldcup_tactics_strip_draws_pitch_when_lineups_missing():
    plugin = _plugin()
    assert Path(LOCAL_WORLDCUP_PITCH_STRIP_PATH).exists()
    with Image.open(LOCAL_WORLDCUP_PITCH_STRIP_PATH) as pitch_strip:
        assert pitch_strip.size == (248, 13)
        pitch_strip = pitch_strip.convert("RGB")
        left_goal_pixels = 0
        right_goal_pixels = 0
        for y in range(pitch_strip.height):
            for x in range(0, 32):
                if pitch_strip.getpixel((x, y)) == (255, 255, 255):
                    left_goal_pixels += 1
            for x in range(pitch_strip.width - 32, pitch_strip.width):
                if pitch_strip.getpixel((x, y)) == (255, 255, 255):
                    right_goal_pixels += 1
        assert left_goal_pixels > 0
        assert right_goal_pixels > 0

    image = Image.new("RGB", (340, 18), COLORS["paper"])
    draw = ImageDraw.Draw(image)

    plugin._draw_worldcup_tactics_strip(image, draw, 0, 339, 0, 17, {})

    pixels = image.load()
    white_pixels = 0
    dark_pixels = 0
    left_edge_pixels = 0
    right_edge_pixels = 0
    for y in range(2, 16):
        for x in range(8, 332):
            if pixels[x, y] == (255, 255, 255):
                white_pixels += 1
            if pixels[x, y] == (0, 0, 0):
                dark_pixels += 1
    for y in range(2, 16):
        for x in range(8, 42):
            if pixels[x, y] == (255, 255, 255):
                left_edge_pixels += 1
        for x in range(298, 332):
            if pixels[x, y] == (255, 255, 255):
                right_edge_pixels += 1
    assert white_pixels > 10
    assert dark_pixels > 10
    assert left_edge_pixels > 0
    assert right_edge_pixels > 0
    assert pixels[337, 8] in {(0, 0, 0), (255, 255, 255)}
    bottom_line_pixels = 0
    for x in range(8, 338):
        if pixels[x, 17] == (255, 255, 255):
            bottom_line_pixels += 1
    assert bottom_line_pixels > 120


def test_worldcup_api_key_can_come_from_device_env_alias():
    device_config = FakeDeviceConfig()
    device_config.env["World_CUP"] = "secret"

    assert SportsDashboard._api_sports_key({}, device_config) == "secret"


def test_football_data_key_can_come_from_device_env_alias():
    device_config = FakeDeviceConfig()
    device_config.env["FOOTBALL_DATA"] = "secret"

    assert SportsDashboard._football_data_key({}, device_config) == "secret"


def test_worldcup_odds_key_can_come_from_device_env_alias():
    device_config = FakeDeviceConfig()
    device_config.env["THE_ODDS_API_KEY"] = "secret"

    assert SportsDashboard._the_odds_api_key({}, device_config) == "secret"
    assert SportsDashboard._worldcup_odds_api_key({}, device_config, "theoddsapi") == "secret"


def test_worldcup_odds_provider_auto_detects_odds_api_io_env_key():
    device_config = FakeDeviceConfig()
    device_config.env["ODDS_API_IO_KEY"] = "secret"

    assert SportsDashboard._worldcup_odds_provider({}, device_config) == "oddsapiio"
    assert SportsDashboard._worldcup_odds_api_key({}, device_config, "oddsapiio") == "secret"


def test_worldcup_the_odds_api_provider_does_not_reuse_odds_api_io_key():
    device_config = FakeDeviceConfig()
    device_config.env["ODDS_API_IO_KEY"] = "secret"

    assert SportsDashboard._worldcup_odds_api_key({}, device_config, "theoddsapi") == ""


def test_odds_api_io_live_alias_takes_priority_over_old_uppercase_key():
    device_config = FakeDeviceConfig()
    device_config.env["ODDS_API_IO_KEY"] = "old-secret"
    device_config.env["Odds_API_IO_KEY"] = "new-secret"

    assert SportsDashboard._worldcup_odds_provider({}, device_config) == "oddsapiio"
    assert SportsDashboard._worldcup_odds_api_key({}, device_config, "oddsapiio") == "new-secret"
    assert SportsDashboard._nba_odds_api_key({}, device_config, "oddsapiio") == "new-secret"
    assert SportsDashboard._lpl_odds_api_key({}, device_config) == "new-secret"


def test_odds_api_io_legacy_league_slugs_map_to_current_feed_slugs():
    assert (
        SportsDashboard._worldcup_odds_api_io_league({"worldCupOddsApiIoLeague": "international-world-cup"})
        == "international-fifa-world-cup"
    )
    assert SportsDashboard._nba_odds_api_io_league({"nbaOddsApiIoLeague": "usa-nba"}) == "usa-nba-playoffs"
    assert (
        SportsDashboard._lpl_odds_api_io_league({"lplOddsApiIoLeague": "league-of-legends-lpl"})
        == "league-of-legends-split-2"
    )


def test_nba_odds_provider_auto_detects_odds_api_io_env_key():
    device_config = FakeDeviceConfig()
    device_config.env["ODDS_API_IO_KEY"] = "secret"

    assert SportsDashboard._nba_odds_provider({}, device_config) == "oddsapiio"
    assert SportsDashboard._nba_odds_api_key({}, device_config, "oddsapiio") == "secret"


def test_nba_the_odds_api_provider_does_not_reuse_odds_api_io_key():
    device_config = FakeDeviceConfig()
    device_config.env["ODDS_API_IO_KEY"] = "secret"

    assert SportsDashboard._nba_odds_api_key({}, device_config, "theoddsapi") == ""


def test_football_data_parser_uses_chinese_country_names_and_flat_flags():
    la = ZoneInfo("America/Los_Angeles")

    events = SportsDashboard._parse_football_data_events([_sample_football_data_match()], la)

    assert events[0]["start"].strftime("%Y-%m-%d %H:%M") == "2026-06-11 12:00"
    assert events[0]["team_a"] == "\u58a8\u897f\u54e5"
    assert events[0]["team_b"] == "南非"
    assert events[0]["team_a_flag"] == "https://flagcdn.com/w80/mx.png"
    assert events[0]["team_b_flag"] == "https://flagcdn.com/w80/za.png"
    assert events[0]["block"] == "Group A"
    assert "Mexico" in events[0]["team_a_source_aliases"]


def test_football_data_uses_fresh_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("football_data_fresh_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_football_data_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"footballDataKey": "secret", "footballDataCacheHours": "6"}
    la = ZoneInfo("America/Los_Angeles")
    cache_key = plugin._football_data_cache_key(settings, "secret", la)
    match = _sample_football_data_match()
    SportsDashboard._write_json_file(
        tmp_path / "football_data_worldcup.json",
        {
            "version": "sports-dashboard-football-data-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "matches": [match],
        },
    )

    matches, source_state, _fetched_at = plugin._load_football_data_matches(settings, "secret", la)

    assert matches == [match]
    assert source_state == "FOOTBALL CACHE"


def test_football_data_force_refresh_bypasses_fresh_cache():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("football_data_force_refresh")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    settings = {"footballDataKey": "secret", "footballDataCacheHours": "6", "forceRefresh": "true"}
    la = ZoneInfo("America/Los_Angeles")
    cache_key = plugin._football_data_cache_key(settings, "secret", la)
    cached_match = _sample_football_data_match()
    live_match = {
        **_sample_football_data_match(),
        "utcDate": "2026-06-12T22:00:00Z",
    }
    SportsDashboard._write_json_file(
        tmp_path / "football_data_worldcup.json",
        {
            "version": "sports-dashboard-football-data-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "matches": [cached_match],
        },
    )
    plugin._fetch_football_data_payload = lambda *args, **kwargs: {
        "version": "sports-dashboard-football-data-v1",
        "cache_key": cache_key,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "matches": [live_match],
    }

    matches, source_state, _fetched_at = plugin._load_football_data_matches(settings, "secret", la)

    assert matches == [live_match]
    assert source_state == "FOOTBALL LIVE"


def test_football_data_live_cache_uses_short_refresh_window():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("football_data_live_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    settings = {"footballDataKey": "secret", "footballDataCacheHours": "6", "worldCupLiveRefreshSeconds": "180"}
    la = ZoneInfo("America/Los_Angeles")
    now_utc = datetime.now(timezone.utc)
    cache_key = plugin._football_data_cache_key(settings, "secret", la)
    cached_match = {**_sample_football_data_match(), "status": "IN_PLAY"}
    fresh_match = {
        **cached_match,
        "score": {"fullTime": {"home": 1, "away": 0}},
    }
    calls = []
    plugin._fetch_football_data_payload = lambda *args, **kwargs: calls.append(args) or {
        "version": "sports-dashboard-football-data-v1",
        "cache_key": cache_key,
        "fetched_at": now_utc.isoformat(),
        "matches": [fresh_match],
    }
    SportsDashboard._write_json_file(
        tmp_path / "football_data_worldcup.json",
        {
            "version": "sports-dashboard-football-data-v1",
            "cache_key": cache_key,
            "fetched_at": (now_utc - timedelta(seconds=240)).isoformat(),
            "matches": [cached_match],
        },
    )

    matches, source_state, _fetched_at = plugin._load_football_data_matches(settings, "secret", la)

    assert calls
    assert matches == [fresh_match]
    assert source_state == "FOOTBALL LIVE"


def test_worldcup_api_uses_fresh_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("fresh_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"apiSportsKey": "secret", "worldCupApiCacheHours": "6"}
    la = ZoneInfo("America/Los_Angeles")
    cache_key = plugin._worldcup_api_cache_key(settings, "secret", la)
    fixture = _sample_worldcup_fixture()
    SportsDashboard._write_json_file(
        tmp_path / "worldcup_api.json",
        {
            "version": "sports-dashboard-api-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "fixtures": [fixture],
        },
    )

    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert fixtures == [fixture]
    assert source_state == "API CACHE"


def test_worldcup_api_live_cache_uses_short_refresh_window():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("worldcup_api_live_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    settings = {"apiSportsKey": "secret", "worldCupApiCacheHours": "6", "worldCupLiveRefreshSeconds": "180"}
    la = ZoneInfo("America/Los_Angeles")
    now_utc = datetime.now(timezone.utc)
    cache_key = plugin._worldcup_api_cache_key(settings, "secret", la)
    cached_fixture = {
        **_sample_worldcup_fixture(),
        "fixture": {
            **_sample_worldcup_fixture()["fixture"],
            "status": {"short": "1H", "long": "First Half", "elapsed": 22},
        },
        "goals": {"home": 0, "away": 0},
    }
    fresh_fixture = {
        **cached_fixture,
        "goals": {"home": 1, "away": 0},
    }
    calls = []
    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: calls.append(args) or {
        "version": "sports-dashboard-api-v1",
        "cache_key": cache_key,
        "fetched_at": now_utc.isoformat(),
        "fixtures": [fresh_fixture],
    }
    SportsDashboard._write_json_file(
        tmp_path / "worldcup_api.json",
        {
            "version": "sports-dashboard-api-v1",
            "cache_key": cache_key,
            "fetched_at": (now_utc - timedelta(seconds=240)).isoformat(),
            "fixtures": [cached_fixture],
        },
    )

    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert calls
    assert fixtures == [fresh_fixture]
    assert source_state == "API LIVE"


def test_worldcup_scoreboard_default_daily_budget_supports_minute_refresh(tmp_path):
    plugin = _plugin()
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    state_path = plugin._worldcup_scoreboard_state_path()

    assert plugin._worldcup_scoreboard_calls_left({}, now) == 720
    assert plugin._worldcup_scoreboard_calls_left({"worldCupScoreboardDailyLimit": "1440"}, now) == 1440

    plugin._write_json_file(
        state_path,
        {
            "version": "sports-dashboard-worldcup-scoreboard-v1",
            "date": now.date().isoformat(),
            "count": 96,
        },
    )

    assert plugin._worldcup_scoreboard_calls_left({}, now) == 624


def test_worldcup_scoreboard_default_range_covers_group_history():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 25, 16, 0, tzinfo=timezone.utc)

    start_date, end_date = SportsDashboard._worldcup_scoreboard_date_range({}, la, now)

    assert start_date <= datetime(2026, 6, 11, tzinfo=la).date()
    assert end_date >= now.astimezone(la).date()


def test_worldcup_scoreboard_fetch_keeps_semifinals_beyond_first_hundred_events(monkeypatch):
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now_utc = datetime(2026, 7, 11, 6, 0, tzinfo=timezone.utc)
    captured = {}
    plugin._record_worldcup_scoreboard_call = lambda *_args, **_kwargs: None

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            events = [{"id": str(index)} for index in range(100)]
            if int(captured["limit"]) >= 102:
                events.extend(
                    [
                        {"id": "760514", "shortName": "ESP @ FRA"},
                        {"id": "760515", "shortName": "QW4 @ QFW3"},
                    ]
                )
            return {"events": events}

    class FakeSession:
        def get(self, _url, params=None, headers=None, timeout=None):
            captured.update(params or {})
            return FakeResponse()

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    cache_key = plugin._worldcup_scoreboard_cache_key({}, la, now_utc)
    payload = plugin._fetch_worldcup_scoreboard_payload({}, la, cache_key, now_utc)

    assert int(captured["limit"]) >= 102
    assert str(captured["limit"]) in cache_key.split("|")
    assert any(
        event.get("id") == "760514"
        for event in payload["scoreboard"]["events"]
    )


def test_worldcup_api_daily_limit_uses_stale_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("daily_limit")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"apiSportsKey": "secret", "worldCupApiCacheHours": "1", "worldCupApiDailyLimit": "1"}
    la = ZoneInfo("America/Los_Angeles")
    cache_key = plugin._worldcup_api_cache_key(settings, "secret", la)
    fixture = _sample_worldcup_fixture()
    SportsDashboard._write_json_file(
        tmp_path / "worldcup_api.json",
        {
            "version": "sports-dashboard-api-v1",
            "cache_key": cache_key,
            "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "fixtures": [fixture],
        },
    )
    SportsDashboard._write_json_file(
        tmp_path / "api_state.json",
        {"date": datetime.now(timezone.utc).date().isoformat(), "count": 1},
    )

    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert fixtures == [fixture]
    assert source_state == "API STALE"


def test_worldcup_api_free_plan_error_is_negative_cached():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("free_plan_block")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    settings = {"apiSportsKey": "secret", "worldCupApiCacheHours": "6", "worldCupApiDailyLimit": "12"}
    la = ZoneInfo("America/Los_Angeles")

    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("API-Sports returned errors: {'plan': 'Free plans do not have access to this season'}")
    )
    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert fixtures == []
    assert source_state == "API BLOCKED"

    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert fixtures == []
    assert source_state == "API BLOCKED"


def test_worldcup_api_force_refresh_bypasses_negative_cache():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("force_refresh_api_block")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    settings = {"apiSportsKey": "secret", "worldCupApiCacheHours": "6", "worldCupApiDailyLimit": "12", "forceRefresh": "true"}
    la = ZoneInfo("America/Los_Angeles")
    cache_key = plugin._worldcup_api_cache_key(settings, "secret", la)
    fixture = _sample_worldcup_fixture()
    SportsDashboard._write_json_file(
        tmp_path / "worldcup_api.json",
        {
            "version": "sports-dashboard-api-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "blocked_until": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
            "source_state": "API BLOCKED",
            "error": "Free plans do not have access to this season",
            "fixtures": [],
        },
    )
    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: {
        "version": "sports-dashboard-api-v1",
        "cache_key": cache_key,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fixtures": [fixture],
    }

    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert fixtures == [fixture]
    assert source_state == "API LIVE"


def test_worldcup_odds_uses_fresh_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("odds_fresh_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_worldcup_odds_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"theOddsApiKey": "secret", "worldCupOddsCacheHours": "6"}
    cache_key = plugin._worldcup_odds_cache_key(settings, "secret")
    odds_event = _sample_worldcup_odds_event()
    SportsDashboard._write_json_file(
        tmp_path / "worldcup_odds.json",
        {
            "version": "sports-dashboard-worldcup-odds-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "odds_events": [odds_event],
        },
    )

    odds_events, source_state, _fetched_at = plugin._load_worldcup_odds(settings, "secret")

    assert odds_events == [odds_event]
    assert source_state == "ODDS CACHE"


def test_worldcup_odds_match_football_data_event_with_localized_country_names():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_football_data_events([_sample_football_data_match()], la)

    enriched = plugin._merge_worldcup_odds(events, [_sample_worldcup_odds_event()], la, {})

    assert enriched[0]["odds"]["team_a"] == "1.80"
    assert enriched[0]["odds"]["draw"] == "3.25"
    assert enriched[0]["odds"]["team_b"] == "4.50"


def test_worldcup_odds_api_io_match_football_data_event():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_football_data_events([_sample_football_data_match()], la)

    enriched = plugin._merge_worldcup_odds(
        events,
        [_sample_worldcup_odds_api_io_event()],
        la,
        {"worldCupOddsProvider": "oddsapiio", "worldCupOddsBookmakers": "Bet365"},
    )

    assert enriched[0]["odds"]["team_a"] == "1.40"
    assert enriched[0]["odds"]["draw"] == "4.50"
    assert enriched[0]["odds"]["team_b"] == "8.00"


def test_worldcup_odds_normalizes_ampersand_country_aliases():
    assert (
        SportsDashboard._normalize_odds_team_name("Bosnia & Herzegovina")
        == SportsDashboard._normalize_odds_team_name("Bosnia and Herzegovina")
    )
    assert (
        SportsDashboard._normalize_odds_team_name("Bosnia-Herzegovina")
        == SportsDashboard._normalize_odds_team_name("Bosnia and Herzegovina")
    )


def test_worldcup_odds_api_io_payload_fetches_event_ids_then_multi_odds():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("odds_api_io_fetch")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    calls = []

    def fake_get_json(path, params, settings, now_utc):
        calls.append((path, params))
        if path == "/events":
            return [{"id": 66456904}, {"id": 66456906}]
        return [_sample_worldcup_odds_api_io_event()]

    plugin._odds_api_io_get_json = fake_get_json
    settings = {"worldCupOddsProvider": "oddsapiio", "worldCupOddsBookmakers": "Bet365"}

    payload = plugin._fetch_worldcup_odds_payload(settings, "secret", "cache", datetime.now(timezone.utc))

    assert payload["provider"] == "oddsapiio"
    assert payload["odds_events"] == [_sample_worldcup_odds_api_io_event()]
    assert calls[0][0] == "/events"
    assert calls[0][1]["league"] == "international-fifa-world-cup"
    assert calls[1][0] == "/odds/multi"
    assert calls[1][1]["eventIds"] == "66456904,66456906"


def test_generate_image_builds_top_worldcup_panel_with_lpl_and_nba_below():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    plugin._try_worldcup_football_data_panel = lambda *args, **kwargs: None
    plugin._try_worldcup_api_panel = lambda *args, **kwargs: None
    plugin._try_worldcup_scoreboard_panel = lambda *args, **kwargs: None
    plugin._take_worldcup_screenshot = lambda settings, dimensions, timezone_name, visible_matches: Image.new("RGB", dimensions, (1, 2, 3))
    plugin._load_lpl_events = lambda settings, timezone_info: (
        SportsDashboard._parse_lpl_events(_sample_payload(), la),
        "LIVE DATA",
    )
    plugin._attach_lpl_odds = lambda events, *_args: events
    plugin._load_lck_events = lambda settings, timezone_info: ([], "LCK NO DATA")
    plugin._load_nba_events = lambda settings, timezone_info: (
        SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la),
        "ESPN LIVE",
    )
    plugin._attach_lpl_realtime_info = lambda selected, settings, **_kwargs: selected
    plugin._write_nba_live_state = lambda selected, now, source_state: None
    plugin._write_lpl_live_state = lambda selected, now, source_state: None
    plugin._load_team_logo = lambda logo_url, size: None

    image = plugin.generate_image(
        {"worldCupTopHeight": "208", "overlayWorldCupLocalTimes": "false", "valveEsportsEnabled": "false"},
        FakeDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert image.getpixel((10, 10)) != (1, 2, 3)
    assert image.getpixel((10, 190)) != (1, 2, 3)
    assert image.getpixel((10, 230)) != (1, 2, 3)
    assert image.getpixel((360, 230)) != (1, 2, 3)
    assert image.getpixel((560, 230)) != (1, 2, 3)


def _render_dashboard_with_source_states(
    plugin,
    monkeypatch,
    *,
    left_provenance,
    nba_source_state,
    lol_source_state,
    force_refresh=False,
):
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 7, 13, 9, 0, tzinfo=la)
    if left_provenance is None:
        left_panel = None
    else:
        left_panel = attach_source_provenance(
            Image.new("RGB", (552, 208), (9, 9, 9)),
            left_provenance,
        )
    monkeypatch.setattr(
        plugin,
        "_try_worldcup_scoreboard_panel",
        lambda *args, **kwargs: left_panel,
    )
    monkeypatch.setattr(
        plugin,
        "_try_worldcup_football_data_panel",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        plugin,
        "_try_worldcup_api_panel",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(plugin, "_take_worldcup_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        plugin,
        "_load_nba_events",
        lambda *_args, **_kwargs: (
            SportsDashboard._fallback_nba_events(la),
            nba_source_state,
        ),
    )
    monkeypatch.setattr(plugin, "_attach_nba_odds", lambda events, *_args: events)
    monkeypatch.setattr(plugin, "_write_nba_live_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_draw_nba_compact_panel", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        plugin,
        "_load_lol_esports_sidebar_cards",
        lambda *_args, **_kwargs: [
            {
                "league_key": "LPL",
                "selected": SportsDashboard._select_lpl_events([], now),
                "source_state": lol_source_state,
                "priority": 0,
            }
        ],
    )
    monkeypatch.setattr(plugin, "_attach_lpl_realtime_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_write_lol_live_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_draw_lpl_sidebar", lambda *_args, **_kwargs: None)
    settings = {
        "worldCupTopHeight": "208",
        "overlayWorldCupLocalTimes": "false",
        "nbaOffseasonPanelMode": "off",
        "ewcSidebarEnabled": "false",
        "valveEsportsEnabled": "false",
    }
    if force_refresh:
        settings["force_refresh"] = True
    return plugin._generate_image_with_active_colors(
        settings,
        FakeDeviceConfig(),
        (800, 480),
        la,
        now,
    )


def test_generate_image_attests_all_remote_panel_fallback_as_local(monkeypatch):
    image = _render_dashboard_with_source_states(
        _plugin(),
        monkeypatch,
        left_provenance=None,
        nba_source_state="NBA FALLBACK",
        lol_source_state="LPL FALLBACK",
        force_refresh=True,
    )

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True


def test_generate_image_attests_complete_live_remote_panels(monkeypatch):
    image = _render_dashboard_with_source_states(
        _plugin(),
        monkeypatch,
        left_provenance=SourceProvenance.LIVE,
        nba_source_state="ESPN LIVE",
        lol_source_state="LIVE DATA",
        force_refresh=True,
    )

    assert read_source_provenance(image) is SourceProvenance.LIVE


def test_generate_image_attests_complete_fresh_remote_cache(monkeypatch):
    image = _render_dashboard_with_source_states(
        _plugin(),
        monkeypatch,
        left_provenance=SourceProvenance.FRESH_CACHE,
        nba_source_state="ESPN CACHE",
        lol_source_state="LPL CACHE",
    )

    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE


def test_generate_image_attests_stale_remote_panels_and_skips_promotion(monkeypatch):
    image = _render_dashboard_with_source_states(
        _plugin(),
        monkeypatch,
        left_provenance=SourceProvenance.STALE_CACHE,
        nba_source_state="ESPN STALE",
        lol_source_state="LPL STALE",
        force_refresh=True,
    )

    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True


@pytest.mark.parametrize(
    ("mixed_source_state", "expected"),
    [
        ("NBA FALLBACK", SourceProvenance.LOCAL_FALLBACK),
        ("ESPN STALE", SourceProvenance.STALE_CACHE),
        ("ESPN CACHE", SourceProvenance.STALE_CACHE),
    ],
)
def test_forced_composite_refresh_fails_closed_when_any_visible_panel_is_not_live(
    monkeypatch,
    mixed_source_state,
    expected,
):
    image = _render_dashboard_with_source_states(
        _plugin(),
        monkeypatch,
        left_provenance=SourceProvenance.LIVE,
        nba_source_state=mixed_source_state,
        lol_source_state="LIVE DATA",
        force_refresh=True,
    )

    assert read_source_provenance(image) is expected
    assert image.info["inkypi_skip_cache"] is True


def test_generate_image_prefers_espn_scoreboard_for_worldcup_panel():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    calls = []

    def scoreboard_panel(_settings, _device_config, dimensions, *_args):
        calls.append("scoreboard")
        return Image.new("RGB", dimensions, (9, 9, 9))

    plugin._try_worldcup_scoreboard_panel = scoreboard_panel
    plugin._try_worldcup_football_data_panel = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("football-data should not be called when ESPN scoreboard renders")
    )
    plugin._try_worldcup_api_panel = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("api-sports should not be called when ESPN scoreboard renders")
    )
    plugin._take_worldcup_screenshot = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("screenshot should not be called when ESPN scoreboard renders")
    )
    plugin._load_lpl_events = lambda settings, timezone_info: (
        SportsDashboard._parse_lpl_events(_sample_payload(), la),
        "LIVE DATA",
    )
    plugin._attach_lpl_odds = lambda events, *_args: events
    plugin._load_lck_events = lambda settings, timezone_info: ([], "LCK NO DATA")
    plugin._load_nba_events = lambda settings, timezone_info: (
        SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la),
        "ESPN LIVE",
    )
    plugin._attach_nba_odds = lambda events, *_args: events
    plugin._attach_lpl_realtime_info = lambda selected, settings, **_kwargs: selected
    plugin._write_nba_live_state = lambda selected, now, source_state: None
    plugin._write_lpl_live_state = lambda selected, now, source_state: None
    plugin._load_team_logo = lambda logo_url, size: None

    image = plugin.generate_image(
        {"worldCupTopHeight": "208", "overlayWorldCupLocalTimes": "false", "valveEsportsEnabled": "false"},
        FakeDeviceConfig(),
    )

    assert calls == ["scoreboard"]
    assert image.size == (800, 480)


def test_worldcup_panel_preserves_screenshot_aspect_ratio():
    plugin = _plugin()

    panel, content_box = plugin._prepare_worldcup_panel(
        Image.new("RGB", (400, 200), (1, 2, 3)),
        (540, 480),
        3,
    )

    assert panel.size == (540, 480)
    assert content_box == (0, 0, 540, 270)
    assert panel.getpixel((20, 20)) == (1, 2, 3)
    assert panel.getpixel((20, 300)) != (1, 2, 3)


def test_worldcup_api_panel_renders_flat_flag_matchup():
    plugin = _plugin()
    plugin._load_flag_image = lambda _url, size: Image.new("RGBA", size, (0, 92, 185, 255))
    la = ZoneInfo("America/Los_Angeles")
    events = plugin._merge_worldcup_odds(
        SportsDashboard._parse_football_data_events([_sample_football_data_match()], la),
        [_sample_worldcup_odds_event()],
        la,
        {},
    )

    image = plugin._render_worldcup_api_panel(
        (552, 480),
        events,
        "FOOTBALL LIVE",
        datetime.now(timezone.utc).isoformat(),
        1,
        datetime(2026, 6, 10, 12, 0, tzinfo=la),
    )

    assert image.size == (552, 480)
    assert SportsDashboard._worldcup_event_status_label(events[0], datetime(2026, 6, 10, 12, 0, tzinfo=la)) == "12:00"
    assert SportsDashboard._worldcup_event_time_label(events[0]) == "12:00"
    regions = SportsDashboard._worldcup_row_regions(552)
    date_range, time_range = SportsDashboard._worldcup_right_info_x_ranges(552)
    assert regions["group"][1] < regions["match"][0] < regions["match"][1] < time_range[0]
    assert time_range[0] < date_range[0]
    assert SportsDashboard._worldcup_matchup_row_offset(54) == 16
    assert image.getpixel((28, 92)) != COLORS["paper"]
    assert image.getpixel((65, 123)) == (0, 92, 185)
    assert image.getpixel((214, 124)) != COLORS["paper"]


def test_worldcup_compact_api_odds_stay_inside_each_match_row():
    plugin = _plugin()
    plugin._load_flag_image = lambda _url, size: Image.new("RGBA", size, (0, 92, 185, 255))
    la = ZoneInfo("America/Los_Angeles")
    base_event = plugin._merge_worldcup_odds(
        SportsDashboard._parse_football_data_events([_sample_football_data_match()], la),
        [_sample_worldcup_odds_event()],
        la,
        {},
    )[0]
    events = []
    for index, (team_a, team_b) in enumerate(
        [
            ("\u58a8\u897f\u54e5", "\u5357\u975e"),
            ("\u97e9\u56fd", "\u6377\u514b"),
            ("\u52a0\u62ff\u5927", "\u6ce2\u9ed1"),
            ("\u7f8e\u56fd", "\u5df4\u62c9\u572d"),
        ]
    ):
        event = dict(base_event)
        event["team_a"] = team_a
        event["team_b"] = team_b
        event["team_a_tla"] = team_a[:2]
        event["team_b_tla"] = team_b[:2]
        event["start"] = base_event["start"] + timedelta(days=index)
        event["odds"] = dict(base_event["odds"])
        events.append(event)

    odds_boxes = []
    original_draw_odds_text = plugin._draw_worldcup_odds_text

    def record_odds_text(draw, box, text, max_size=11):
        if text:
            odds_boxes.append(tuple(int(value) for value in box))
        return original_draw_odds_text(draw, box, text, max_size=max_size)

    plugin._draw_worldcup_odds_text = record_odds_text
    image = plugin._render_worldcup_api_panel(
        (552, 208),
        events,
        "FOOTBALL LIVE",
        datetime.now(timezone.utc).isoformat(),
        4,
        datetime(2026, 6, 10, 12, 0, tzinfo=la),
    )

    assert image.size == (552, 208)
    assert len(odds_boxes) == 14
    for box in odds_boxes:
        assert 0 <= box[0] < box[2] <= 552
        assert 57 <= box[1] < box[3] <= 208


def test_worldcup_compact_row_meta_uses_symmetric_slots_and_slashes():
    plugin = _plugin()
    event = {
        "team_a": "Canada",
        "team_b": "Qatar",
        "team_a_tla": "CAN",
        "team_b_tla": "QAT",
        "team_a_flag": "https://flagcdn.com/w80/ca.png",
        "team_b_flag": "https://flagcdn.com/w80/qa.png",
        "block": "Group A",
        "team_a_group_record": "1-0-0",
        "team_b_group_record": "0-1-0",
        "team_a_group_points": 3,
        "team_b_group_points": 1,
        "odds": {"team_a": "2.20", "draw": "3.10", "team_b": "3.40"},
    }
    image = Image.new("RGB", (260, 40), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    calls = []

    def record_flag(_image, _draw, _flag_url, _x, _y, width, height, _fallback, align="left"):
        return 24 if align == "left" else 14

    def record_meta(_draw, box, text, max_size=11):
        calls.append((tuple(int(value) for value in box), text))

    plugin._draw_worldcup_flag = record_flag
    plugin._draw_worldcup_odds_text = record_meta

    plugin._draw_worldcup_row_lineup(image, draw, 4, 256, 14, event, "VS")

    left_box, left_text = calls[0]
    right_box, right_text = calls[1]
    draw_box, draw_text = calls[2]
    assert left_box[2] - left_box[0] == right_box[2] - right_box[0]
    assert left_box[0] + right_box[2] == left_box[2] + right_box[0]
    assert left_text == "PTS 3 / 1-0-0 / 2.20"
    assert right_text == "3.40 / 0-1-0 / PTS 1"
    assert draw_text == "X / 3.10"
    assert draw_box[0] < draw_box[2]


def test_worldcup_recent_row_meta_uses_mirrored_points_and_record():
    plugin = _plugin()
    event = {
        "start": datetime(2026, 6, 18, 20, 0, tzinfo=timezone.utc),
        "team_a": "Mexico",
        "team_b": "Korea",
        "team_a_tla": "MEX",
        "team_b_tla": "KOR",
        "team_a_flag": "https://flagcdn.com/w80/mx.png",
        "team_b_flag": "https://flagcdn.com/w80/kr.png",
        "team_a_group_record": "2-0-0",
        "team_b_group_record": "0-1-0",
        "team_a_group_points": 6,
        "team_b_group_points": 3,
        "block": "Group A",
        "wins_a": 1,
        "wins_b": 0,
    }
    image = Image.new("RGB", (260, 50), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    calls = []

    plugin._draw_worldcup_recent_team_identity = lambda *_args, **_kwargs: None

    def record_meta(_draw, box, text, max_size=11):
        calls.append((tuple(int(value) for value in box), text, max_size))

    plugin._draw_worldcup_odds_text = record_meta

    plugin._draw_worldcup_recent_match_row(image, draw, 4, 256, 10, 32, event)

    left_box, left_text, left_max_size = calls[0]
    right_box, right_text, right_max_size = calls[1]
    assert left_box[2] - left_box[0] == right_box[2] - right_box[0]
    assert left_box[0] + right_box[2] == left_box[2] + right_box[0]
    assert left_text == "PTS 6 / 2-0-0"
    assert right_text == "0-1-0 / PTS 3"
    assert left_max_size == 7
    assert right_max_size == 7

def test_worldcup_recent_row_draws_team_side_extra_time_and_penalty_score_chips():
    plugin = _plugin()
    event = {
        "start": datetime(2026, 6, 29, 17, 0, tzinfo=timezone.utc),
        "state": "PEN",
        "team_a": "Brazil",
        "team_b": "Japan",
        "team_a_tla": "BRA",
        "team_b_tla": "JPN",
        "team_a_flag": "https://flagcdn.com/w80/br.png",
        "team_b_flag": "https://flagcdn.com/w80/jp.png",
        "block": "Round of 32",
        "wins_a": 2,
        "wins_b": 2,
        "extra_time_score_a": 1,
        "extra_time_score_b": 1,
        "penalty_score_a": 4,
        "penalty_score_b": 3,
    }
    image = Image.new("RGB", (260, 50), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    chip_calls = []

    plugin._draw_worldcup_recent_team_identity = lambda *_args, **_kwargs: None

    def record_chip(_draw, box, text, align="left"):
        chip_calls.append((tuple(int(value) for value in box), text, align))

    plugin._draw_worldcup_score_detail_chip = record_chip

    plugin._draw_worldcup_recent_match_row(image, draw, 4, 256, 10, 32, event)

    assert [call[1] for call in chip_calls] == ["ET 1/P4", "P3/ET 1"]
    assert chip_calls[0][0][2] <= 108
    assert chip_calls[0][2] == "right"
    assert chip_calls[1][0][0] >= 152
    assert chip_calls[1][2] == "left"


def test_worldcup_score_detail_text_has_no_background_or_border():
    plugin = _plugin()
    image = Image.new("RGB", (120, 40), COLORS["paper"])
    base_draw = ImageDraw.Draw(image)

    class RecordingDraw:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.rounded_calls = []
            self.text_calls = []

        def textbbox(self, *args, **kwargs):
            return self.wrapped.textbbox(*args, **kwargs)

        def text(self, *args, **kwargs):
            self.text_calls.append((args, kwargs))
            return self.wrapped.text(*args, **kwargs)

        def rounded_rectangle(self, *args, **kwargs):
            self.rounded_calls.append((args, kwargs))

    draw = RecordingDraw(base_draw)

    plugin._draw_worldcup_score_detail_chip(draw, (10, 10, 68, 23), "ET 1/P4", align="right")

    assert draw.rounded_calls == []
    assert len(draw.text_calls) == 1
    font = draw.text_calls[0][1]["font"]
    assert getattr(font, "size", None) == 9
    assert draw.text_calls[0][1]["fill"] == COLORS["text"]


def test_worldcup_recent_row_strikes_eliminated_knockout_team():
    plugin = _plugin()
    event = {
        "start": datetime(2026, 6, 29, 17, 0, tzinfo=timezone.utc),
        "state": "FT",
        "team_a": "Brazil",
        "team_b": "Japan",
        "team_a_tla": "BRA",
        "team_b_tla": "JPN",
        "team_a_flag": "https://flagcdn.com/w80/br.png",
        "team_b_flag": "https://flagcdn.com/w80/jp.png",
        "team_a_advance": True,
        "team_b_advance": False,
        "block": "Round of 32",
        "wins_a": 2,
        "wins_b": 1,
    }
    image = Image.new("RGB", (260, 50), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    calls = []
    strikes = []

    plugin._draw_worldcup_flag = lambda *_args, **_kwargs: calls.append("flag")
    plugin._draw_worldcup_elimination_strike = lambda _draw, x1, x2, y1, y2: (calls.append("strike"), strikes.append((x1, x2, y1, y2)))

    plugin._draw_worldcup_recent_match_row(image, draw, 4, 256, 10, 32, event)

    assert calls[-2:] == ["flag", "strike"]
    assert len(strikes) == 1
    strike_left, strike_right, _y1, _y2 = strikes[0]
    assert strike_left > 130
    assert strike_right == 248



def test_worldcup_elimination_strike_overflows_identity_bounds():
    plugin = _plugin()
    image = Image.new("RGB", (40, 20), COLORS["paper"])
    draw = ImageDraw.Draw(image)

    plugin._draw_worldcup_elimination_strike(draw, 10, 20, 4, 12)

    strike_y = 9
    assert image.getpixel((7, strike_y)) == COLORS["red"]
    assert image.getpixel((23, strike_y)) == COLORS["red"]
    assert image.getpixel((6, strike_y)) != COLORS["red"]
    assert image.getpixel((24, strike_y)) != COLORS["red"]

def test_worldcup_compact_row_flag_slot_budget():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = plugin._merge_worldcup_odds(
        SportsDashboard._parse_football_data_events([_sample_football_data_match()], la),
        [_sample_worldcup_odds_event()],
        la,
        {},
    )[0]
    image = Image.new("RGB", (260, 40), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    flag_sizes = []

    def record_flag(_image, _draw, _flag_url, _x, _y, width, height, _fallback, align="left"):
        flag_sizes.append((width, height))
        return width

    plugin._draw_worldcup_flag = record_flag

    plugin._draw_worldcup_row_lineup(image, draw, 4, 256, 14, event, "VS")

    assert flag_sizes == [(28, 14), (28, 14)]


def test_worldcup_compact_row_preserves_country_flag_ratios(monkeypatch):
    plugin = _plugin()
    event = {
        "team_a": "Canada",
        "team_b": "Qatar",
        "team_a_tla": "CAN",
        "team_b_tla": "QAT",
        "team_a_flag": "https://flagcdn.com/w80/ca.png",
        "team_b_flag": "https://flagcdn.com/w80/qa.png",
        "wins_a": 2,
        "wins_b": 0,
    }
    image = Image.new("RGBA", (260, 40), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    requested_sizes = []

    def fake_load_flag_image(_flag_url, size):
        requested_sizes.append(size)
        return Image.new("RGBA", size, (255, 0, 0, 255))

    monkeypatch.setattr(plugin, "_load_flag_image", fake_load_flag_image)

    plugin._draw_worldcup_row_lineup(image, draw, 4, 256, 14, event, "VS")

    assert requested_sizes == [(28, 14), (28, 11)]


def test_worldcup_flag_returns_rendered_width(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGBA", (120, 40), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    monkeypatch.setattr(
        plugin, "_load_flag_image", lambda _url, size: Image.new("RGBA", size, (255, 0, 0, 255))
    )

    korea = plugin._draw_worldcup_flag(image, draw, "https://flagcdn.com/w80/kr.png", 0, 0, 36, 18, "KOR")
    qatar = plugin._draw_worldcup_flag(image, draw, "https://flagcdn.com/w80/qa.png", 0, 0, 36, 18, "QAT")

    # 3:2 flag fills the full 18px height and stays narrower than the 36px budget.
    assert korea == 27
    # 2.55:1 flag is capped at the 36px budget width (and ends up slightly shorter).
    assert qatar == 36


def test_worldcup_flag_right_align_flushes_to_budget_right_edge(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGBA", (60, 30), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    monkeypatch.setattr(
        plugin, "_load_flag_image", lambda _url, size: Image.new("RGBA", size, (0, 92, 185, 255))
    )

    width = plugin._draw_worldcup_flag(
        image, draw, "https://flagcdn.com/w80/kr.png", 0, 5, 36, 18, "KOR", align="right"
    )

    assert width == 27
    # Flag occupies x in [9, 36): flush to the right edge, transparent gap on the left.
    assert image.getpixel((35, 13))[:3] == (0, 92, 185)
    assert image.getpixel((2, 13))[3] == 0


def test_worldcup_row_text_starts_after_actual_flag_width(monkeypatch):
    plugin = _plugin()
    event = {
        "team_a": "Korea",
        "team_b": "Qatar",
        "team_a_tla": "KOR",
        "team_b_tla": "QAT",
        "team_a_flag": "https://flagcdn.com/w80/kr.png",
        "team_b_flag": "https://flagcdn.com/w80/qa.png",
    }
    image = Image.new("RGBA", (260, 40), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    monkeypatch.setattr(
        plugin, "_load_flag_image", lambda _url, size: Image.new("RGBA", size, (255, 0, 0, 255))
    )
    boxes = []
    original_draw_text_in_box = plugin._draw_text_in_box

    def capture_text_box(draw_obj, box, *args, **kwargs):
        boxes.append(box)
        return original_draw_text_in_box(draw_obj, box, *args, **kwargs)

    plugin._draw_text_in_box = capture_text_box

    plugin._draw_worldcup_row_lineup(image, draw, 4, 256, 14, event, "VS")

    # Korea (3:2) renders 21px wide in the 28px budget; the team name must start
    # right after the actual flag (x1 + 1 + 21 + 4), not after the full budget.
    assert boxes[0][0] == 4 + 1 + 21 + 4


def test_forced_night_theme_uses_deep_night_palette_without_leaking():
    plugin = _plugin()
    plugin._try_worldcup_football_data_panel = lambda _settings, _device_config, dimensions, *_args: Image.new(
        "RGB", dimensions, COLORS["paper"]
    )
    plugin._try_worldcup_api_panel = lambda *args, **kwargs: None
    plugin._take_worldcup_screenshot = lambda *args, **kwargs: None
    plugin._load_lpl_events = lambda _settings, timezone_info: (
        SportsDashboard._fallback_lpl_events(timezone_info),
        "LIVE DATA",
    )
    plugin._attach_lpl_odds = lambda events, *_args: events
    plugin._load_lck_events = lambda settings, timezone_info: ([], "LCK NO DATA")
    plugin._attach_lpl_realtime_info = lambda selected, settings, **_kwargs: selected
    plugin._write_nba_live_state = lambda selected, now, source_state: None
    plugin._write_lpl_live_state = lambda selected, now, source_state: None
    plugin._load_nba_events = lambda _settings, timezone_info: (
        SportsDashboard._fallback_nba_events(timezone_info),
        "ESPN CACHE",
    )
    plugin._load_team_logo = lambda _logo_url, _size: None

    image = plugin.generate_image(
        {"sportsDashboardTheme": "night", "localTimezone": "UTC", "worldCupTopHeight": "208", "valveEsportsEnabled": "false"},
        FakeDeviceConfig(timezone="UTC"),
    )

    assert max(image.getpixel((620, 120))) < 90
    assert image.getpixel((620, 120)) != DAY_COLORS["paper"]
    assert DEEP_NIGHT_COLORS["paper"] != DAY_COLORS["paper"]
    assert COLORS["paper"] == DAY_COLORS["paper"]


def test_theme_only_injected_palettes_use_cached_stubs_without_provider_and_reset_colors(monkeypatch):
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    provider_calls = {"count": 0}

    def fail_http_session():
        provider_calls["count"] += 1
        raise AssertionError("theme-only redraw must not open a provider session")

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", fail_http_session)
    plugin._try_worldcup_scoreboard_panel = lambda *args, **kwargs: None
    plugin._try_worldcup_football_data_panel = lambda _settings, _device_config, dimensions, *_args: Image.new(
        "RGB", dimensions, COLORS["paper"]
    )
    plugin._try_worldcup_api_panel = lambda *args, **kwargs: None
    plugin._load_lpl_events = lambda _settings, _timezone_info: (
        SportsDashboard._parse_lpl_events(_sample_payload(), la),
        "CACHE",
    )
    plugin._attach_lpl_odds = lambda events, *_args: events
    plugin._load_lck_events = lambda _settings, _timezone_info: ([], "CACHE")
    plugin._load_nba_events = lambda _settings, _timezone_info: (
        SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la),
        "CACHE",
    )
    plugin._attach_nba_odds = lambda events, *_args: events
    plugin._attach_lpl_realtime_info = lambda selected, settings, **_kwargs: selected
    plugin._write_nba_live_state = lambda *_args, **_kwargs: None
    plugin._write_lpl_live_state = lambda *_args, **_kwargs: None
    plugin._load_team_logo = lambda *_args, **_kwargs: None
    plugin._should_show_offseason_hub_panel = lambda *_args, **_kwargs: False
    settings = {
        "sportsDashboardTheme": "night",
        "localTimezone": "America/Los_Angeles",
        "worldCupTopHeight": "208",
        "valveEsportsEnabled": "false",
        "ewcSidebarEnabled": "false",
        "lckEnabled": "false",
        "msiEnabled": "false",
        "_theme_render_only": True,
    }
    day = _canonical_theme(
        "day",
        background=(241, 236, 225),
        panel=(221, 213, 196),
        ink=(19, 21, 23),
        muted=(73, 75, 79),
        rule=(128, 124, 116),
        accent=(180, 44, 58),
    )
    night = _canonical_theme(
        "night",
        background=(9, 11, 14),
        panel=(25, 29, 35),
        ink=(244, 246, 248),
        muted=(179, 183, 191),
        rule=(61, 67, 75),
        accent=(72, 186, 234),
    )

    day_image = plugin.generate_image({**settings, "_inkypi_theme": day}, FakeDeviceConfig())
    assert _ACTIVE_COLORS.get() is DAY_COLORS
    night_image = plugin.generate_image({**settings, "_inkypi_theme": night}, FakeDeviceConfig())

    assert provider_calls == {"count": 0}
    assert _ACTIVE_COLORS.get() is DAY_COLORS
    assert hashlib.sha256(day_image.tobytes()).digest() != hashlib.sha256(night_image.tobytes()).digest()


def test_uploaded_brand_logos_are_loaded_from_local_assets():
    lpl_logo = SportsDashboard._load_local_logo(LOCAL_LPL_LOGO_PATH, (74, 38), alpha_threshold=8)
    lck_logo = SportsDashboard._load_local_logo(LOCAL_LCK_LOGO_PATH, (74, 38), alpha_threshold=8)
    nba_logo = SportsDashboard._load_local_logo(LOCAL_NBA_LOGO_PATH, (34, 38), alpha_threshold=8)
    worldcup_logo = SportsDashboard._load_local_logo(LOCAL_WORLDCUP_LOGO_PATH, (36, 36), alpha_threshold=16)
    f1_logo = SportsDashboard._load_local_logo(LOCAL_F1_LOGO_PATH, (62, 24), alpha_threshold=12)
    mlb_logo = SportsDashboard._load_local_logo(LOCAL_MLB_LOGO_PATH, (74, 34), alpha_threshold=8)
    wnba_logo = SportsDashboard._load_local_logo(LOCAL_WNBA_LOGO_PATH, (78, 34), alpha_threshold=8)
    pga_logo = SportsDashboard._load_local_logo(LOCAL_PGA_LOGO_PATH, (36, 48), alpha_threshold=8)
    nfl_logo = SportsDashboard._load_local_logo(LOCAL_NFL_LOGO_PATH, (36, 36), alpha_threshold=8)
    ncaa_logo = SportsDashboard._load_local_logo(LOCAL_NCAA_LOGO_PATH, (36, 36), alpha_threshold=8)

    assert lpl_logo is not None
    assert lpl_logo.size[0] <= 74
    assert lpl_logo.size[1] <= 38
    assert lpl_logo.getchannel("A").getextrema()[0] == 0
    assert lck_logo is not None
    assert lck_logo.size[0] <= 74
    assert lck_logo.size[1] <= 38
    assert lck_logo.getchannel("A").getextrema()[0] == 0
    assert nba_logo is not None
    assert nba_logo.size[0] <= 34
    assert nba_logo.size[1] <= 38
    assert nba_logo.getchannel("A").getextrema()[0] == 0
    assert worldcup_logo is not None
    assert worldcup_logo.size[0] <= 36
    assert worldcup_logo.size[1] <= 36
    assert worldcup_logo.getchannel("A").getextrema()[0] == 0
    assert f1_logo is not None
    assert f1_logo.size[0] <= 62
    assert f1_logo.size[1] <= 24
    assert f1_logo.getchannel("A").getextrema()[0] == 0
    for logo in (mlb_logo, wnba_logo, pga_logo, nfl_logo, ncaa_logo):
        assert logo is not None
        assert logo.getchannel("A").getextrema()[0] == 0


def test_local_brand_logo_retries_after_transient_decode_failure(monkeypatch):
    cache_key = (LOCAL_LCK_LOGO_PATH, (74, 38), 8)
    original_open = sports_dashboard_module.Image.open
    attempts = []

    def fail_first_open(path, *args, **kwargs):
        if path == LOCAL_LCK_LOGO_PATH:
            attempts.append(path)
            if len(attempts) == 1:
                raise MemoryError("transient image decode pressure")
        return original_open(path, *args, **kwargs)

    TEAM_LOGO_CACHE.pop(cache_key, None)
    monkeypatch.setattr(sports_dashboard_module.Image, "open", fail_first_open)
    try:
        assert SportsDashboard._load_local_logo(LOCAL_LCK_LOGO_PATH, (74, 38), alpha_threshold=8) is None

        recovered = SportsDashboard._load_local_logo(LOCAL_LCK_LOGO_PATH, (74, 38), alpha_threshold=8)

        assert recovered is not None
        assert recovered.size[0] <= 74
        assert recovered.size[1] <= 38
        assert attempts == [LOCAL_LCK_LOGO_PATH, LOCAL_LCK_LOGO_PATH]
    finally:
        TEAM_LOGO_CACHE.pop(cache_key, None)


def test_worldcup_title_wordmark_asset_is_transparent_and_wide():
    wordmark = Image.open(LOCAL_WORLDCUP_TITLE_WORDMARK_PATH).convert("RGBA")

    assert wordmark.size == (640, 123)
    assert wordmark.width > wordmark.height * 4
    assert wordmark.getbbox() is not None
    assert wordmark.getchannel("A").getextrema()[0] == 0


def test_worldcup_title_wordmark_draws_inside_header_slot():
    plugin = _plugin()
    image = Image.new("RGBA", (240, 70), (0, 0, 0, 0))

    assert plugin._draw_worldcup_title_wordmark(image, 52, 6, 178, 27) is True
    bbox = image.getbbox()

    assert bbox is not None
    assert bbox[0] >= 52
    assert bbox[2] <= 52 + 178
    assert bbox[1] >= 6
    assert bbox[3] <= 6 + 27

def test_lck_team_logos_are_synced_and_loadable():
    expected_codes = {"BFX", "BRO", "DK", "DNS", "GEN", "HLE", "KRX", "KT", "NS", "T1"}
    logo_dir = Path(LOCAL_LCK_TEAM_LOGO_DIR)
    manifest_path = logo_dir / "manifest.json"

    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert {team["code"] for team in manifest["teams"]} == expected_codes

    for code in expected_codes:
        path = logo_dir / f"{code.lower()}.png"
        assert path.exists(), code
        logo = SportsDashboard._load_local_team_logo(code, 34)
        assert logo is not None, code
        assert logo.size[0] <= 34
        assert logo.size[1] <= 34
        assert logo.getchannel("A").getextrema()[1] > 0


def test_sports_dashboard_local_asset_constants_exist():
    asset_paths = [
        LOCAL_LPL_LOGO_PATH,
        LOCAL_LCK_LOGO_PATH,
        LOCAL_MSI_LOGO_PATH,
        LOCAL_WORLDCUP_LOGO_PATH,
        LOCAL_NBA_LOGO_PATH,
        LOCAL_F1_LOGO_PATH,
        LOCAL_CS_MAJOR_LOGO_PATH,
        LOCAL_EWC_LOGO_PATH,
        LOCAL_TI_LOGO_PATH,
        LOCAL_MLB_LOGO_PATH,
        LOCAL_MLB_TITLE_WORDMARK_PATH,
        LOCAL_WNBA_LOGO_PATH,
        LOCAL_WNBA_TITLE_WORDMARK_PATH,
        LOCAL_PGA_LOGO_PATH,
        LOCAL_PGA_TITLE_WORDMARK_PATH,
        LOCAL_NFL_LOGO_PATH,
        LOCAL_NCAA_LOGO_PATH,
        LOCAL_WORLDCUP_PITCH_STRIP_PATH,
        LOCAL_WORLDCUP_HEADER_BANNER_PATH,
        LOCAL_WORLDCUP_TITLE_WORDMARK_PATH,
        LOCAL_NBA_COURT_STRIP_PATH,
        LOCAL_MLB_HEADER_CUTOUT_PATH,
        LOCAL_WNBA_HEADER_CUTOUT_PATH,
        LOCAL_PGA_HEADER_CUTOUT_PATH,
        LOCAL_NFL_HEADER_CUTOUT_PATH,
        LOCAL_NCAA_HEADER_CUTOUT_PATH,
        LOCAL_NBA_EMPTY_SLOT_FILLER_PATH,
        LOCAL_NBA_OFFSEASON_FILLER_PATH,
        LOCAL_NBA_OFFSEASON_ACCENT_PATH,
        LOCAL_PGA_FAIRWAY_STRIP_PATH,
        LOCAL_LPL_MARBLE_FILLER_PATH,
        LOCAL_LPL_MSI_NEXT_FILLER_PATH,
        LOCAL_LPL_MSI_OFFSEASON_FILLER_PATH,
    ]

    for path in asset_paths:
        asset = Path(path)
        assert asset.is_file(), path
        assert asset.stat().st_size > 0, path


def test_standalone_sport_header_cutouts_load_as_transparent_strips():
    TEAM_LOGO_CACHE.clear()
    for sport in ("MLB", "WNBA", "PGA", "NFL", "NCAA"):
        cutout = SportsDashboard._load_sport_header_cutout(sport, (220, 34))

        assert cutout is not None
        assert cutout.size[0] <= 220
        assert cutout.size[1] <= 34
        alpha_min, alpha_max = cutout.getchannel("A").getextrema()
        assert alpha_min == 0
        assert alpha_max > 0


def test_remote_team_logo_loader_uses_short_timeout_and_failure_cache(monkeypatch):
    logo_url = "https://example.com/offseason-hub-logo-timeout.png"
    cache_key = (logo_url, 24)
    TEAM_LOGO_CACHE.pop(cache_key, None)
    calls = []

    class FakeSession:
        def get(self, url, headers=None, timeout=None, stream=False):
            assert stream is True
            calls.append((url, timeout))
            raise OSError("offline")

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    assert SportsDashboard._load_team_logo(logo_url, 24) is None
    assert SportsDashboard._load_team_logo(logo_url, 24) is None

    assert calls == [(logo_url, TEAM_LOGO_FETCH_TIMEOUT_SECONDS)]
    assert TEAM_LOGO_CACHE[cache_key] is None
    TEAM_LOGO_CACHE.pop(cache_key, None)

def test_remote_team_logo_uses_shared_http_session(monkeypatch):
    logo_url = "https://a.espncdn.com/i/teamlogos/mlb/500/lad.png"
    cache_key = (logo_url, 24)
    TEAM_LOGO_CACHE.pop(cache_key, None)
    source = Image.new("RGBA", (12, 12), (0, 92, 185, 255))
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    data = buffer.getvalue()
    calls = []

    class FakeResponse:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size > 0
            yield data

        def close(self):
            return None

    class FakeSession:
        def get(self, url, headers=None, timeout=None, stream=False):
            assert stream is True
            calls.append((url, headers, timeout))
            return FakeResponse()

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    logo = SportsDashboard._load_team_logo(logo_url, 24)

    assert logo is not None
    assert logo.size == (24, 24)
    assert calls == [(logo_url, {"User-Agent": "InkyPi/1.0"}, TEAM_LOGO_FETCH_TIMEOUT_SECONDS)]
    TEAM_LOGO_CACHE.pop(cache_key, None)


def test_remote_team_logo_loader_uses_disk_cache(monkeypatch, tmp_path):
    logo_url = "https://tds-cdn.ewc.efg.gg/assets/clubs/2068035497296400384/LOGO_LIGHT.png"
    TEAM_LOGO_CACHE.clear()
    source = Image.new("RGBA", (12, 12), (0, 92, 185, 255))
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    data = buffer.getvalue()
    calls = []

    class FakeResponse:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size > 0
            yield data

        def close(self):
            return None

    class FakeSession:
        def get(self, url, headers=None, timeout=None, stream=False):
            assert stream is True
            calls.append((url, timeout))
            return FakeResponse()

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    first = SportsDashboard._load_team_logo(logo_url, 24, cache_dir=tmp_path)
    TEAM_LOGO_CACHE.clear()
    second = SportsDashboard._load_team_logo(logo_url, 24, cache_dir=tmp_path)

    assert first is not None
    assert second is not None
    assert calls == [(logo_url, TEAM_LOGO_FETCH_TIMEOUT_SECONDS)]
    assert len(list(tmp_path.iterdir())) == 1


def test_sports_image_caches_are_lru_bounded_after_one_thousand_misses():
    from utils.cache_manager import ImageLRUCache

    assert isinstance(TEAM_LOGO_CACHE, ImageLRUCache)
    assert isinstance(FLAG_IMAGE_CACHE, ImageLRUCache)
    TEAM_LOGO_CACHE.clear()

    for index in range(1000):
        TEAM_LOGO_CACHE[(f"missing-{index}", 24)] = None

    assert len(TEAM_LOGO_CACHE) <= 128


def test_remote_team_logo_loader_replaces_oversized_disk_cache(monkeypatch, tmp_path):
    logo_url = "https://tds-cdn.ewc.efg.gg/assets/clubs/2068035497296400384/LOGO_LIGHT.png"
    TEAM_LOGO_CACHE.clear()
    disk_path = SportsDashboard._team_logo_disk_cache_path(tmp_path, logo_url)
    Image.new("RGBA", (2050, 8), (0, 92, 185, 255)).save(disk_path, format="PNG")
    small = Image.new("RGBA", (12, 12), (12, 34, 56, 255))
    buffer = BytesIO()
    small.save(buffer, format="PNG")
    data = buffer.getvalue()
    calls = []

    class FakeResponse:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size > 0
            yield data

        def close(self):
            return None

    class FakeSession:
        def get(self, url, headers=None, timeout=None, stream=False):
            assert stream is True
            calls.append((url, timeout))
            return FakeResponse()

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    logo = SportsDashboard._load_team_logo(logo_url, 24, cache_dir=tmp_path)

    assert logo is not None
    assert logo.size == (24, 24)
    assert calls == [(logo_url, TEAM_LOGO_FETCH_TIMEOUT_SECONDS)]
    with Image.open(disk_path) as cached:
        assert cached.size == (12, 12)


def _sample_valve_csapi_major_payload():
    return [
        {
            "id": 1001,
            "event": "IEM Cologne Major 2026",
            "date": "2026-06-20",
            "best_of": 3,
            "team1": {"id": 7020, "name": "Spirit", "rank": 1, "score": 1},
            "team2": {"id": 11283, "name": "Falcons", "rank": 2, "score": 2},
            "maps": [
                {"name": "Mirage", "team1_score": 13, "team2_score": 8},
                {"name": "Anubis", "team1_score": 14, "team2_score": 16},
                {"name": "Dust2", "team1_score": 12, "team2_score": 16},
            ],
        },
        {
            "id": 1002,
            "event": "Austin Major Closed Qualifier 2026",
            "date": "2026-06-20",
            "team1": {"name": "Qualifier A", "score": 2},
            "team2": {"name": "Qualifier B", "score": 0},
        },
        {
            "id": 1003,
            "event": "IEM Dallas 2026",
            "date": "2026-06-20",
            "team1": {"name": "Team A", "score": 2},
            "team2": {"name": "Team B", "score": 1},
        },
    ]


def _sample_valve_ti_payload():
    return [
        {
            "match_id": 2001,
            "league_name": "The International 2026 - Regional Qualifier Europe",
            "start_time": 1781701200,
            "radiant_team_id": 101,
            "dire_team_id": 102,
            "radiant_name": "Qualifier Team A",
            "dire_name": "Qualifier Team B",
            "radiant_score": 31,
            "dire_score": 20,
            "series_type": 1,
            "duration": 2400,
        },
        {
            "match_id": 2002,
            "league_name": "The International 2026",
            "start_time": 1782133200,
            "radiant_team_id": 2163,
            "dire_team_id": 8599101,
            "radiant_name": "Team Liquid",
            "dire_name": "Gaimin Gladiators",
            "radiant_score": 42,
            "dire_score": 37,
            "series_type": 2,
            "duration": 3180,
        },
    ]


def test_valve_csapi_parser_selects_active_major_and_excludes_qualifiers():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 21, 12, 0, tzinfo=la)

    cards = SportsDashboard._parse_valve_cs_major_cards(_sample_valve_csapi_major_payload(), la, now, {})

    assert len(cards) == 1
    card = cards[0]
    assert card["series"] == "CS"
    assert card["sport"] == "CS2 Major"
    assert card["event_name"] == "IEM Cologne Major 2026"
    assert card["window_active"] is True
    assert card["logo_path"] == LOCAL_CS_MAJOR_LOGO_PATH
    assert card["main"]["team_a"] == "Spirit"
    assert card["main"]["team_b"] == "Falcons"
    assert card["main"]["team_a_id"] == 7020
    assert card["main"]["team_b_id"] == 11283
    assert SportsDashboard._valve_score_label(card["main"]) == "1:2"
    assert SportsDashboard._valve_match_detail_label(card["main"], compact=True) == "Mirage 13-8  |  Anubis 14-16  |  Dust2 12-16"


def test_valve_csapi_parser_marks_major_break_after_bo5_final_result():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 21, 20, 0, tzinfo=la)
    payload = [
        {
            "id": 2395002,
            "event": "IEM Cologne Major 2026",
            "date": "2026-06-21",
            "best_of": 5,
            "team1": {"id": 11283, "name": "Falcons", "rank": 2, "score": 3},
            "team2": {"id": 8297, "name": "FURIA", "rank": 5, "score": 0},
            "maps": [
                {"name": "Inferno", "team1_score": 13, "team2_score": 8},
                {"name": "Anubis", "team1_score": 13, "team2_score": 8},
                {"name": "Mirage", "team1_score": 13, "team2_score": 8},
            ],
        }
    ]

    cards = SportsDashboard._parse_valve_cs_major_cards(payload, la, now, {})

    assert len(cards) == 1
    assert cards[0]["status"] == "BREAK"
    assert cards[0]["window_active"] is False
    selected = SportsDashboard._select_valve_esports(cards, now)
    assert selected["primary"] is None
    assert SportsDashboard._valve_esports_has_displayable_event(selected) is False

def test_valve_ti_parser_excludes_regional_qualifiers():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 22, 12, 0, tzinfo=la)

    cards = SportsDashboard._parse_valve_ti_cards(_sample_valve_ti_payload(), la, now, {})

    assert len(cards) == 1
    card = cards[0]
    assert card["series"] == "TI"
    assert card["event_name"] == "The International 2026"
    assert card["window_active"] is True
    assert card["logo_path"] == LOCAL_TI_LOGO_PATH
    assert card["main"]["team_a"] == "Team Liquid"
    assert card["main"]["team_b"] == "Gaimin Gladiators"
    assert card["main"]["team_a_id"] == 2163
    assert card["main"]["team_b_id"] == 8599101
    assert SportsDashboard._valve_score_label(card["main"]) == "42:37"



def test_valve_dota2_preview_flag_short_circuits_live_sources(monkeypatch):
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 22, 12, 0, tzinfo=la)
    monkeypatch.setattr(SportsDashboard, "_valve_dota2_preview_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(plugin, "_load_valve_csapi_matches", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("CSAPI should not load")))
    monkeypatch.setattr(plugin, "_load_valve_opendota_matches", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("OpenDota should not load")))

    selected, source_state = plugin._load_valve_esports({}, la, now)

    assert source_state == "DOTA2 PREVIEW"
    assert selected["primary"]["series"] == "TI"
    assert selected["rotation_pool"] == ["TI"]
    assert selected["primary"]["main"]["team_a"] == "Team Liquid"

def test_valve_ti_sidebar_header_uses_dota2_label(monkeypatch):
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 22, 12, 0, tzinfo=la)
    cards = SportsDashboard._parse_valve_ti_cards(_sample_valve_ti_payload(), la, now, {})
    selected = SportsDashboard._select_valve_esports(cards, now)
    image = Image.new("RGB", (800, 480), COLORS["paper"])
    seen_texts = []
    original_draw_text_in_box = plugin._draw_text_in_box

    def record_text(draw, box, text, font, color, align="left"):
        seen_texts.append(str(text))
        return original_draw_text_in_box(draw, box, text, font, color, align=align)

    monkeypatch.setattr(plugin, "_draw_text_in_box", record_text)

    plugin._draw_valve_esports_sidebar(image, 552, selected, "OPENDOTA CACHE", now)

    assert "Dota 2" in seen_texts
    assert "Counter-Strike 2" not in seen_texts

def test_valve_ti_uses_red_theme_and_split_focus_header():
    primary = {"series": "TI", "status": "ACTIVE"}
    assert SportsDashboard._valve_series_accent(primary) == COLORS["valve_ti_accent"]
    assert SportsDashboard._valve_series_accent(primary) != COLORS["amber"]

    layout = SportsDashboard._valve_focus_header_layout(564, 788, 78)
    assert layout["tag_box"][3] <= layout["date_box"][1]
    assert layout["date_box"][3] <= layout["title_box"][1]

def test_valve_ti_parser_attaches_opendota_team_logos_and_tags():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 22, 12, 0, tzinfo=la)
    profiles = {
        "2163": {"logo_url": "https://example.com/liquid.png", "tag": "TL"},
        "8599101": {"logo_url": "https://example.com/gg.png", "tag": "GG"},
    }

    cards = SportsDashboard._parse_valve_ti_cards(_sample_valve_ti_payload(), la, now, {}, profiles)

    assert SportsDashboard._valve_ti_team_ids(_sample_valve_ti_payload()) == {2163, 8599101}
    event = cards[0]["main"]
    assert event["team_a_logo"] == "https://example.com/liquid.png"
    assert event["team_b_logo"] == "https://example.com/gg.png"
    assert SportsDashboard._valve_team_display_name(event, "a") == "TL"
    assert SportsDashboard._valve_team_display_name(event, "b") == "GG"


def test_valve_generated_team_icon_is_stable_and_distinct(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (90, 48), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    event = {"team_a": "Spirit", "team_a_id": 7020, "team_b": "Falcons", "team_b_id": 11283}
    monkeypatch.setattr(plugin, "_load_team_logo", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_load_valve_local_team_logo", lambda *_args, **_kwargs: None)

    plugin._draw_valve_team_icon(image, draw, event, "a", 8, 7, 34)
    plugin._draw_valve_team_icon(image, draw, event, "b", 48, 7, 34)

    assert image.getpixel((11, 10)) != COLORS["paper"]
    assert image.getpixel((51, 10)) != COLORS["paper"]
    assert SportsDashboard._valve_team_icon_colors("Spirit", 7020) != SportsDashboard._valve_team_icon_colors("Falcons", 11283)


def test_valve_team_icon_prefers_local_cs2_logo(tmp_path, monkeypatch):
    plugin = _plugin()
    logo_dir = tmp_path / "cs2"
    logo_dir.mkdir()
    Image.new("RGBA", (12, 12), (220, 20, 20, 255)).save(logo_dir / "7020.png")
    monkeypatch.setattr(sports_dashboard_module, "LOCAL_CS2_TEAM_LOGO_DIR", str(logo_dir))
    monkeypatch.setattr(plugin, "_load_team_logo", lambda *_args, **_kwargs: None)
    image = Image.new("RGB", (52, 52), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    event = {"series": "CS", "team_a": "Spirit", "team_a_id": 7020}

    plugin._draw_valve_team_icon(image, draw, event, "a", 8, 7, 34)

    assert image.getpixel((25, 24)) != COLORS["paper"]
    assert Path(SportsDashboard._valve_local_team_logo_candidates("Spirit", 7020, "CS")[0]).parent.name == "cs2"


def test_valve_team_icon_prefers_local_dota2_logo(tmp_path, monkeypatch):
    plugin = _plugin()
    cs2_dir = tmp_path / "cs2"
    dota2_dir = tmp_path / "dota2"
    cs2_dir.mkdir()
    dota2_dir.mkdir()
    Image.new("RGBA", (12, 12), (220, 20, 20, 255)).save(cs2_dir / "2163.png")
    Image.new("RGBA", (12, 12), (20, 220, 20, 255)).save(dota2_dir / "2163.png")
    monkeypatch.setattr(sports_dashboard_module, "LOCAL_CS2_TEAM_LOGO_DIR", str(cs2_dir))
    monkeypatch.setattr(sports_dashboard_module, "LOCAL_DOTA2_TEAM_LOGO_DIR", str(dota2_dir))
    monkeypatch.setattr(plugin, "_load_team_logo", lambda *_args, **_kwargs: None)
    image = Image.new("RGB", (52, 52), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    event = {"series": "TI", "team_a": "Team Liquid", "team_a_id": 2163}

    plugin._draw_valve_team_icon(image, draw, event, "a", 8, 7, 34)

    pixel = image.getpixel((25, 24))
    assert pixel[1] > pixel[0]
    assert Path(SportsDashboard._valve_local_team_logo_candidates("Team Liquid", 2163, "TI")[0]).parent.name == "dota2"

def test_valve_fit_text_ellipsis_never_exceeds_box_width():
    image = Image.new("RGB", (160, 60), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    text, font = SportsDashboard._fit_text_ellipsis(
        draw,
        "Natus Vincere International Counter-Strike 2 Roster",
        54,
        12,
        bold=True,
        min_size=7,
    )

    assert text.endswith("...")
    assert SportsDashboard._text_width(draw, text, font) <= 54

def test_valve_selector_rotates_active_cards_and_ignores_break_cards():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 21, 12, 0, tzinfo=la)
    active_cs = {"series": "CS", "event_name": "Major", "status": "ACTIVE", "window_active": True, "order": 0, "main": {"start": now}}
    active_ti = {"series": "TI", "event_name": "TI", "status": "ACTIVE", "window_active": True, "order": 1, "main": {"start": now}}
    break_card = {"series": "CS", "event_name": "Old Major", "status": "BREAK", "window_active": False, "order": 0, "main": {"start": now}}

    selected = SportsDashboard._select_valve_esports([active_cs, active_ti, break_card], now)

    assert selected["primary"] in (active_cs, active_ti)
    assert selected["rotation_pool"] == ["CS", "TI"]
    assert SportsDashboard._valve_esports_has_displayable_event(selected) is True


def test_right_esports_sidebar_priority_order_lpl_lck_cs_ti():
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    lpl_live = {
        "league_key": "LPL",
        "selected": {"live": [{"start": now}], "upcoming": [], "recent": [], "main": {"start": now}},
        "source_state": "LIVE DATA",
        "priority": 0,
    }
    lpl_default = {
        "league_key": "LPL",
        "selected": SportsDashboard._select_lpl_events([], now),
        "source_state": "CACHE DATA",
        "priority": 0,
    }
    lck_live = {
        "league_key": "LCK",
        "selected": {"live": [{"start": now}], "upcoming": [], "recent": [], "main": {"start": now}},
        "source_state": "LCK LIVE DATA",
        "priority": 1,
    }
    cs_card = {"series": "CS", "event_name": "CS Major", "status": "ACTIVE", "window_active": True, "order": 0, "main": {"start": now}}
    ti_card = {"series": "TI", "event_name": "The International", "status": "ACTIVE", "window_active": True, "order": 1, "main": {"start": now}}
    valve_selected = {"primary": ti_card, "cards": [ti_card, cs_card], "rotation_pool": ["TI", "CS"]}

    choice = SportsDashboard._select_right_esports_sidebar([lpl_live, lck_live], valve_selected, "VALVE DATA", now)
    assert choice["kind"] == "lol"
    assert choice["choice"]["league_key"] == "LPL"

    choice = SportsDashboard._select_right_esports_sidebar([lpl_default, lck_live], valve_selected, "VALVE DATA", now)
    assert choice["kind"] == "lol"
    assert choice["choice"]["league_key"] == "LCK"

    choice = SportsDashboard._select_right_esports_sidebar([lpl_default], valve_selected, "VALVE DATA", now)
    assert choice["kind"] == "valve"
    assert choice["selected"]["primary"]["series"] == "CS"


def test_generate_image_uses_lpl_before_active_valve_when_lpl_live():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 21, 12, 0, tzinfo=la)
    calls = []
    lpl_event = {
        "start": now - timedelta(minutes=5),
        "state": "live",
        "team_a": "BLG",
        "team_b": "TES",
        "wins_a": 1,
        "wins_b": 0,
        "best_of": 3,
        "block": "Split 2",
    }
    active_card = {
        "series": "CS",
        "sport": "CS2 Major",
        "event_name": "IEM Cologne Major 2026",
        "status": "ACTIVE",
        "window_active": True,
        "logo_path": LOCAL_CS_MAJOR_LOGO_PATH,
        "start": now,
        "latest": now,
        "main": {"start": now, "team_a": "Spirit", "team_b": "Falcons", "wins_a": 1, "wins_b": 2, "source": "CSAPI"},
        "recent": [],
    }

    plugin._try_worldcup_scoreboard_panel = lambda *args, **kwargs: Image.new("RGB", (552, 208), (9, 9, 9))
    plugin._try_worldcup_football_data_panel = lambda *args, **kwargs: None
    plugin._try_worldcup_api_panel = lambda *args, **kwargs: None
    plugin._take_worldcup_screenshot = lambda *args, **kwargs: None
    plugin._load_nba_events = lambda settings, timezone_info: (SportsDashboard._fallback_nba_events(timezone_info), "NBA FALLBACK")
    plugin._attach_nba_odds = lambda events, *_args: events
    plugin._write_nba_live_state = lambda selected, now_arg, source_state: None
    plugin._lol_esports_sidebar_override = lambda settings=None: ""
    plugin._load_lpl_events = lambda settings, timezone_info: ([lpl_event], "LIVE DATA")
    plugin._load_lck_events = lambda settings, timezone_info: ([], "LCK NO DATA")
    plugin._load_msi_events = lambda settings, timezone_info, now_arg: ([], "MSI NO DATA", None)
    plugin._attach_lpl_odds = lambda events, *_args, **_kwargs: events
    plugin._attach_lpl_realtime_info = lambda *args, **kwargs: None
    plugin._load_valve_esports = lambda settings, timezone_info, now_arg: ({"primary": active_card, "cards": [active_card], "rotation_pool": ["CS"]}, "CSAPI CACHE")
    plugin._write_valve_esports_live_state = lambda selected, now_arg, source_state: calls.append("valve_state")
    plugin._draw_valve_esports_sidebar = lambda *args, **kwargs: calls.append("valve")
    plugin._write_lol_live_state = lambda selected, now_arg, source_state, league_key="LPL": calls.append(f"lol_state:{league_key}")
    plugin._draw_lpl_sidebar = lambda *args, **kwargs: calls.append(f"lol:{kwargs.get('league_key', 'LPL')}")

    image = plugin._generate_image_with_active_colors(
        {"worldCupTopHeight": "208", "overlayWorldCupLocalTimes": "false"},
        FakeDeviceConfig(),
        (800, 480),
        la,
        now,
    )

    assert image.size == (800, 480)
    assert calls == ["lol_state:LPL", "lol:LPL"]


def test_generate_image_uses_active_valve_when_lol_has_no_active_or_upcoming():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 21, 12, 0, tzinfo=la)
    calls = []
    active_card = {
        "series": "CS",
        "sport": "CS2 Major",
        "event_name": "IEM Cologne Major 2026",
        "status": "ACTIVE",
        "window_active": True,
        "logo_path": LOCAL_CS_MAJOR_LOGO_PATH,
        "start": now,
        "latest": now,
        "main": {"start": now, "team_a": "Spirit", "team_b": "Falcons", "wins_a": 1, "wins_b": 2, "source": "CSAPI"},
        "recent": [],
    }

    plugin._try_worldcup_scoreboard_panel = lambda *args, **kwargs: Image.new("RGB", (552, 208), (9, 9, 9))
    plugin._try_worldcup_football_data_panel = lambda *args, **kwargs: None
    plugin._try_worldcup_api_panel = lambda *args, **kwargs: None
    plugin._take_worldcup_screenshot = lambda *args, **kwargs: None
    plugin._load_nba_events = lambda settings, timezone_info: (SportsDashboard._fallback_nba_events(timezone_info), "NBA FALLBACK")
    plugin._attach_nba_odds = lambda events, *_args: events
    plugin._write_nba_live_state = lambda selected, now_arg, source_state: None
    plugin._lol_esports_sidebar_override = lambda settings=None: ""
    plugin._load_lpl_events = lambda settings, timezone_info: ([], "CACHE DATA")
    plugin._load_lck_events = lambda settings, timezone_info: ([], "LCK NO DATA")
    plugin._load_msi_events = lambda settings, timezone_info, now_arg: ([], "MSI NO DATA", None)
    plugin._attach_lpl_odds = lambda events, *_args, **_kwargs: events
    plugin._attach_lpl_realtime_info = lambda *args, **kwargs: None
    plugin._load_valve_esports = lambda settings, timezone_info, now_arg: ({"primary": active_card, "cards": [active_card], "rotation_pool": ["CS"]}, "CSAPI CACHE")
    plugin._write_valve_esports_live_state = lambda selected, now_arg, source_state: calls.append("state")
    plugin._draw_valve_esports_sidebar = lambda *args, **kwargs: calls.append("valve")
    plugin._draw_lpl_sidebar = lambda *args, **kwargs: calls.append("lpl")

    image = plugin._generate_image_with_active_colors(
        {"worldCupTopHeight": "208", "overlayWorldCupLocalTimes": "false", "ewcSidebarEnabled": "false"},
        FakeDeviceConfig(),
        (800, 480),
        la,
        now,
    )

    assert image.size == (800, 480)
    assert calls == ["state", "valve"]

def test_valve_esports_sidebar_render_smoke():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 21, 12, 0, tzinfo=la)
    cards = SportsDashboard._parse_valve_cs_major_cards(_sample_valve_csapi_major_payload(), la, now, {})
    selected = SportsDashboard._select_valve_esports(cards, now)
    image = Image.new("RGB", (800, 480), COLORS["paper"])

    plugin._draw_valve_esports_sidebar(image, 552, selected, "CSAPI CACHE", now)

    assert image.getpixel((580, 24)) != COLORS["paper"]
    assert image.getpixel((620, 100)) != COLORS["paper"]

def test_settings_exposes_offseason_hub_controls():
    settings_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "sports_dashboard"
        / "settings.html"
    )
    html = settings_path.read_text(encoding="utf-8")
    fields = [
        "offseasonHubCacheHours",
        "offseasonHubLiveRefreshSeconds",
        "offseasonHubDailyLimit",
        "offseasonHubLookbackDays",
        "offseasonHubLookaheadDays",
        "mlbScoreboardUrl",
        "wnbaScoreboardUrl",
        "pgaScoreboardUrl",
        "nflScoreboardUrl",
        "ncaaScoreboardUrl",
    ]

    for field in fields:
        assert f'id="{field}"' in html
        assert f'name="{field}"' in html
        assert f"pluginSettings.{field}" in html

    assert 'id="worldCupScoreboardDailyLimit" name="worldCupScoreboardDailyLimit" min="1" max="1440" placeholder="720"' in html
    assert 'id="worldCupScoreboardLookbackDays" name="worldCupScoreboardLookbackDays" min="0" max="30" placeholder="30"' in html
    assert 'id="worldCupLiveRefreshSeconds" name="worldCupLiveRefreshSeconds" min="30" max="900" placeholder="60"' in html

    f1_fields = [
        "f1PanelMode",
        "f1JolpicaBaseUrl",
        "f1OpenF1BaseUrl",
        "f1OpenF1Enabled",
        "f1DailyLimit",
        "f1OpenF1DailyLimit",
    ]
    for field in f1_fields:
        assert f'id="{field}"' not in html
        assert f'name="{field}"' not in html
        assert f"pluginSettings.{field}" not in html



def test_settings_exposes_ewc_sidebar_controls():
    settings_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "sports_dashboard"
        / "settings.html"
    )
    html = settings_path.read_text(encoding="utf-8")
    fields = [
        "ewcSidebarEnabled",
        "ewcCompetitionsUrl",
        "ewcCacheHours",
        "ewcUpcomingWindowDays",
    ]

    for field in fields:
        assert f'id="{field}"' in html
        assert f"pluginSettings.{field}" in html


def test_settings_exposes_valve_esports_controls():
    settings_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "sports_dashboard"
        / "settings.html"
    )
    html = settings_path.read_text(encoding="utf-8")
    fields = [
        "valveEsportsEnabled",
        "valveEsportsCsapiEnabled",
        "valveEsportsCsapiBaseUrl",
        "valveEsportsOpenDotaEnabled",
        "valveEsportsCacheHours",
        "valveEsportsDailyLimit",
        "valveEsportsWindowAfterDays",
    ]

    for field in fields:
        assert f'id="{field}"' in html
        assert f'name="{field}"' in html
        assert f"pluginSettings.{field}" in html

def test_settings_exposes_lck_sidebar_controls():
    settings_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "sports_dashboard"
        / "settings.html"
    )
    html = settings_path.read_text(encoding="utf-8")

    for field in ("lckEnabled", "lckLeagueId"):
        assert f'id="{field}"' in html
        assert f'name="{field}"' in html
        assert f"pluginSettings.{field}" in html

def test_al_logo_draw_size_is_the_only_lpl_size_override():
    assert SportsDashboard._team_logo_draw_size("AL", 19) == 25
    assert SportsDashboard._team_logo_draw_size("al", 16) == 21
    assert SportsDashboard._team_logo_draw_size("BLG", 19) == 19


def test_worldcup_flag_draws_loaded_flag_without_background():
    plugin = _plugin()
    flag = Image.new("RGBA", (30, 22), (0, 0, 0, 0))
    for x in range(8, 22):
        for y in range(6, 16):
            flag.putpixel((x, y), (0, 92, 185, 255))
    plugin._load_flag_image = lambda _url, _size: flag
    image = Image.new("RGB", (80, 40), COLORS["paper"])
    draw = ImageDraw.Draw(image)

    plugin._draw_worldcup_flag(image, draw, "https://flagcdn.com/w80/mx.png", 10, 10, 30, 22, "MEX")

    assert image.getpixel((10, 10)) == COLORS["paper"]
    assert image.getpixel((20, 16)) == (0, 92, 185)


def test_logo_with_flat_background_becomes_transparent():
    source = Image.new("RGB", (8, 8), (255, 255, 255))
    for x in range(2, 6):
        for y in range(2, 6):
            source.putpixel((x, y), (0, 92, 185))

    logo = SportsDashboard._logo_with_transparent_background(source)

    assert logo.mode == "RGBA"
    assert logo.getpixel((0, 0))[3] == 0
    assert logo.getpixel((3, 3)) == (0, 92, 185, 255)


def test_sports_dashboard_base_font_uses_shared_resolver(monkeypatch):
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        "plugins.sports_dashboard.common.get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or sentinel,
        raising=False,
    )

    assert SportsDashboard._font(18, True) is sentinel
    assert calls == [(18, True)]
