from collections.abc import Mapping, MutableMapping
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import html as html_lib
import hashlib
import json
import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import logging
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.sports_dashboard.cache_io import read_json_file, write_json_file
from utils.app_utils import get_base_ui_font, resolve_path
from utils.cache_manager import (
    CacheBudget,
    CacheError,
    ImageLRUCache,
    cache_namespace_for_directory,
)
from utils.safe_image import ImageLimits, read_limited_response_bytes, safe_open_image

try:
    from utils.app_utils import resolve_dimensions as _resolve_dimensions
except Exception:  # pragma: no cover - compatibility with older app_utils layout
    _resolve_dimensions = None
from utils.http_client import get_http_session
from utils.image_utils import take_screenshot

try:
    from utils.image_utils import text_width
except Exception:  # pragma: no cover - compatibility with older image_utils layout
    def text_width(draw, text, font):
        left, top, right, bottom = draw.textbbox((0, 0), str(text), font=font)
        return right - left

try:
    from utils.theme_utils import get_theme_context
except Exception:  # pragma: no cover - theme_utils can be unavailable in lightweight local previews.
    get_theme_context = None

logger = logging.getLogger(__name__)

SECRET_QUERY_PARAM_RE = re.compile(r"([?&](?:apiKey|api_key|apikey|key|token)=)[^&\s]+", re.IGNORECASE)


def _safe_exception_text(exc):
    return SECRET_QUERY_PARAM_RE.sub(r"\1<redacted>", str(exc))

DEFAULT_WORLD_CUP_URL = "https://www.sportbusy.com/embed/world-cup"
DEFAULT_WORLD_CUP_VISIBLE_MATCHES = 4
WORLD_CUP_VISIBLE_MATCH_LIMIT = 4
DEFAULT_WORLD_CUP_TOP_HEIGHT = 208
DEFAULT_WORLD_CUP_ZOOM_WIDTH = 420
DEFAULT_WORLD_CUP_SEASON = "2026"
DEFAULT_WORLD_CUP_API_LEAGUE_ID = "1"
DEFAULT_WORLD_CUP_API_CACHE_HOURS = 6
DEFAULT_WORLD_CUP_API_DAILY_LIMIT = 12
DEFAULT_FOOTBALL_DATA_COMPETITION = "WC"
DEFAULT_FOOTBALL_DATA_CACHE_HOURS = 6
DEFAULT_FOOTBALL_DATA_DAILY_LIMIT = 8
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"
FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
SPORTS_DASHBOARD_STATE_VERSION = "sports-dashboard-api-v1"
FOOTBALL_DATA_STATE_VERSION = "sports-dashboard-football-data-v1"
WORLD_CUP_SCOREBOARD_STATE_VERSION = "sports-dashboard-worldcup-scoreboard-v1"
WORLD_CUP_STANDINGS_STATE_VERSION = "sports-dashboard-worldcup-standings-v1"
WORLD_CUP_ODDS_STATE_VERSION = "sports-dashboard-worldcup-odds-v1"
WORLD_CUP_LIVE_STATE_VERSION = "sports-dashboard-worldcup-live-v1"
WORLD_CUP_LINEUP_STATE_VERSION = "sports-dashboard-worldcup-lineups-v1"
LPL_ODDS_STATE_VERSION = "sports-dashboard-lpl-odds-v1"
LPL_LIVE_STATE_VERSION = "sports-dashboard-lpl-live-v1"
LCK_LIVE_STATE_VERSION = "sports-dashboard-lck-live-v1"
MSI_TOURNAMENT_STATE_VERSION = "sports-dashboard-msi-tournament-v1"
MSI_LIVE_STATE_VERSION = "sports-dashboard-msi-live-v1"
VALVE_ESPORTS_STATE_VERSION = "sports-dashboard-valve-esports-v1"
VALVE_ESPORTS_LIVE_STATE_VERSION = "sports-dashboard-valve-esports-live-v1"
EWC_STATE_VERSION = "sports-dashboard-ewc-v1"
EWC_DETAIL_STATE_VERSION = "sports-dashboard-ewc-detail-v1"
EWC_LIVE_STATE_VERSION = "sports-dashboard-ewc-live-v1"
NBA_SCOREBOARD_STATE_VERSION = "sports-dashboard-nba-scoreboard-v1"
NBA_LIVE_STATE_VERSION = "sports-dashboard-nba-live-v1"
NBA_ODDS_STATE_VERSION = "sports-dashboard-nba-odds-v1"
F1_JOLPICA_STATE_VERSION = "sports-dashboard-f1-jolpica-v1"
F1_OPENF1_STATE_VERSION = "sports-dashboard-f1-openf1-v1"
F1_LIVE_STATE_VERSION = "sports-dashboard-f1-live-v1"
OFFSEASON_HUB_STATE_VERSION = "sports-dashboard-offseason-hub-v1"
THE_ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
ODDS_API_IO_BASE_URL = "https://api.odds-api.io/v3"
DEFAULT_NBA_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
DEFAULT_NBA_CACHE_HOURS = 1
DEFAULT_NBA_DAILY_LIMIT = 96
DEFAULT_NBA_LOOKBACK_DAYS = 10
DEFAULT_NBA_LOOKAHEAD_DAYS = 180
DEFAULT_NBA_LIVE_REFRESH_SECONDS = 180
NBA_OFFSEASON_NEXT_WINDOW_DAYS = 14
NBA_OFFSEASON_FILLER_ZOOM = 1.24
NBA_OFFSEASON_FILLER_LEFT_BLEED = 4
NBA_OFFSEASON_FILLER_TOP_BLEED = 3
NBA_OFFSEASON_FILLER_RIGHT_BLEED = 12
NBA_OFFSEASON_FILLER_BOTTOM_BLEED = 8
NBA_OFFSEASON_ACCENT_SIZE = (132, 72)
LPL_MSI_OFFSEASON_FILLER_ZOOM = 1.24
LPL_MSI_OFFSEASON_FILLER_BOTTOM_OVERFILL = 12
LPL_MSI_OFFSEASON_FILLER_VERTICAL_CROP_OFFSET = 8
DEFAULT_F1_JOLPICA_BASE_URL = "https://api.jolpi.ca/ergast/f1"
DEFAULT_F1_OPENF1_BASE_URL = "https://api.openf1.org/v1"
DEFAULT_F1_CACHE_HOURS = 6
DEFAULT_F1_DAILY_LIMIT = 24
DEFAULT_F1_OPENF1_DAILY_LIMIT = 96
DEFAULT_F1_LIVE_REFRESH_SECONDS = 180
F1_SESSION_PREGAME_WINDOW = timedelta(minutes=15)
F1_SESSION_RESULT_WINDOW = timedelta(hours=6)
DEFAULT_MLB_SCOREBOARD_URL = "https://statsapi.mlb.com/api/v1/schedule"
DEFAULT_WNBA_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard"
DEFAULT_PGA_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"
DEFAULT_NFL_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
DEFAULT_NCAA_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
DEFAULT_OFFSEASON_HUB_CACHE_HOURS = 1
DEFAULT_OFFSEASON_HUB_DAILY_LIMIT = 96
DEFAULT_OFFSEASON_HUB_LIVE_REFRESH_SECONDS = 180
DEFAULT_OFFSEASON_HUB_LOOKBACK_DAYS = 1
DEFAULT_OFFSEASON_HUB_LOOKAHEAD_DAYS = 5
OFFSEASON_HUB_ROTATION_MINUTES = 30
OFFSEASON_HUB_URGENT_NEXT_WINDOW = timedelta(minutes=90)
OFFSEASON_HUB_DEFAULT_LIVE_WINDOW = timedelta(hours=5)
OFFSEASON_HUB_PGA_POST_EVENT_WINDOW = timedelta(hours=18)
TEAM_LOGO_FETCH_TIMEOUT_SECONDS = 2
TEAM_LOGO_DISK_CACHE_MAX_BYTES = 2 * 1024 * 1024
TEAM_LOGO_DISK_CACHE_MAX_SIDE = 2048
TEAM_LOGO_DISK_CACHE_MAX_PIXELS = 4 * 1024 * 1024
TEAM_LOGO_IMAGE_LIMITS = ImageLimits(
    max_bytes=TEAM_LOGO_DISK_CACHE_MAX_BYTES,
    max_width=TEAM_LOGO_DISK_CACHE_MAX_SIDE,
    max_height=TEAM_LOGO_DISK_CACHE_MAX_SIDE,
    max_pixels=TEAM_LOGO_DISK_CACHE_MAX_PIXELS,
)
TEAM_LOGO_DISK_CACHE_BUDGET = CacheBudget(
    30 * 24 * 60 * 60,
    256,
    50 * 1024 * 1024,
)
DEFAULT_WORLD_CUP_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
# Authoritative ESPN group table (key-free): cumulative PTS/W-D-L for all 12 groups,
# correct on every matchday regardless of the scoreboard date window. Note: apis/v2
# (the "apis/site/v2" variant returns an empty body for this standings resource).
DEFAULT_WORLD_CUP_STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings"
DEFAULT_WORLD_CUP_STANDINGS_CACHE_HOURS = 1
DEFAULT_WORLD_CUP_SCOREBOARD_CACHE_HOURS = 1
DEFAULT_WORLD_CUP_SCOREBOARD_DAILY_LIMIT = 720
DEFAULT_WORLD_CUP_SCOREBOARD_LOOKBACK_DAYS = 30
DEFAULT_WORLD_CUP_SCOREBOARD_LOOKAHEAD_DAYS = 5
WORLD_CUP_SCOREBOARD_EVENT_LIMIT = 200
NBA_MINI_LINEUP_LOGO_SIZE = 15
NBA_MINI_LINEUP_TEAM_FONT_SIZE = 12
NBA_MINI_LINEUP_ODDS_TEAM_FONT_SIZE = 11
NBA_INLINE_LOGO_SIZE = 23
NBA_INLINE_TEAM_FONT_SIZE = 19
NBA_INLINE_TEAM_MIN_FONT_SIZE = 12
NBA_LIVE_STATES = {"inprogress", "in_progress", "in-progress", "live", "in"}
NBA_FINISHED_STATES = {"completed", "post", "final", "finished"}
NBA_INFERRED_LIVE_WINDOW = timedelta(hours=4)
NBA_LIVE_PREGAME_WINDOW = timedelta(minutes=30)
MSI_2026_START = (2026, 6, 28)
MSI_2026_END = (2026, 7, 12)
LPL_MSI_NEXT_WINDOW_DAYS = 21
DEFAULT_NBA_ODDS_PROVIDER = "theoddsapi"
DEFAULT_NBA_ODDS_SPORT_KEY = "basketball_nba"
DEFAULT_NBA_ODDS_REGIONS = "us"
DEFAULT_NBA_ODDS_MARKETS = "h2h"
DEFAULT_NBA_ODDS_CACHE_HOURS = 6
DEFAULT_NBA_ODDS_DAILY_LIMIT = 8
DEFAULT_NBA_ODDS_BOOKMAKERS = "Bet365"
DEFAULT_NBA_ODDS_API_IO_SPORT = "basketball"
DEFAULT_NBA_ODDS_API_IO_LEAGUE = "usa-nba-playoffs"
DEFAULT_NBA_ODDS_API_IO_STATUS = "pending"
DEFAULT_NBA_ODDS_API_IO_LIMIT = 10
DEFAULT_WORLD_CUP_ODDS_PROVIDER = "theoddsapi"
DEFAULT_WORLD_CUP_ODDS_SPORT_KEY = "soccer_fifa_world_cup"
DEFAULT_WORLD_CUP_ODDS_API_IO_SPORT = "football"
DEFAULT_WORLD_CUP_ODDS_API_IO_LEAGUE = "international-fifa-world-cup"
DEFAULT_WORLD_CUP_ODDS_API_IO_STATUS = "pending"
DEFAULT_WORLD_CUP_ODDS_API_IO_LIMIT = 10
DEFAULT_WORLD_CUP_ODDS_REGIONS = "us"
DEFAULT_WORLD_CUP_ODDS_MARKETS = "h2h"
DEFAULT_WORLD_CUP_ODDS_CACHE_HOURS = 6
DEFAULT_WORLD_CUP_ODDS_DAILY_LIMIT = 8
DEFAULT_WORLD_CUP_ODDS_BOOKMAKERS = "Bet365"
DEFAULT_WORLD_CUP_LIVE_REFRESH_SECONDS = 60
DEFAULT_WORLD_CUP_LINEUP_CACHE_SECONDS = 600
WORLD_CUP_LIVE_STATES = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT", "IN_PLAY", "PAUSED"}
WORLD_CUP_FINISHED_STATES = {"FT", "AET", "PEN", "FINISHED", "AWARDED"}
WORLD_CUP_INFERRED_LIVE_WINDOW = timedelta(hours=3)
WORLD_CUP_LIVE_PREGAME_WINDOW = timedelta(minutes=30)
WORLD_CUP_LINEUP_LOOKAHEAD = timedelta(hours=3)
WORLD_CUP_LINEUP_POSTMATCH_WINDOW = timedelta(hours=8)
DEFAULT_LPL_ODDS_API_IO_SPORT = "esports"
DEFAULT_LPL_ODDS_API_IO_LEAGUE = "league-of-legends-split-2"
DEFAULT_LPL_ODDS_API_IO_STATUS = "pending"
DEFAULT_LPL_ODDS_API_IO_LIMIT = 5
DEFAULT_LPL_ODDS_CACHE_HOURS = 12
DEFAULT_LPL_ODDS_DAILY_LIMIT = 8
DEFAULT_LPL_ODDS_BOOKMAKERS = "Bet365"
DEFAULT_LPL_LIVE_REFRESH_SECONDS = 180
CSAPI_BASE_URL = "https://api.csapi.de"
OPENDOTA_BASE_URL = "https://api.opendota.com/api"
DEFAULT_VALVE_ESPORTS_CACHE_HOURS = 6
DEFAULT_VALVE_ESPORTS_DAILY_LIMIT = 48
DEFAULT_VALVE_ESPORTS_CS_LIMIT = 80
DEFAULT_VALVE_ESPORTS_OPENDOTA_LIMIT = 120
DEFAULT_VALVE_ESPORTS_WINDOW_AFTER_DAYS = 2
DEFAULT_VALVE_ESPORTS_LIVE_REFRESH_SECONDS = 180
DEFAULT_EWC_COMPETITIONS_URL = "https://esportsworldcup.com/en/competitions/2026"
DEFAULT_EWC_CACHE_HOURS = 12
DEFAULT_EWC_DETAIL_CACHE_SECONDS = 600
DEFAULT_EWC_DETAIL_LOOKAHEAD_DAYS = 7
DEFAULT_EWC_DETAIL_MAX_PAGES = 5
DEFAULT_EWC_UPCOMING_WINDOW_DAYS = 21
DEFAULT_EWC_EVENT_ACTIVE_AFTER_DAYS = 1
DEFAULT_EWC_LIVE_REFRESH_SECONDS = 60
EWC_LIVE_PREGAME_WINDOW = timedelta(minutes=30)
EWC_MATCH_DEFAULT_DURATION = timedelta(hours=3)
LOL_ESPORTS_ROTATION_MINUTES = 30
LPL_LIVE_STATES = {"inprogress", "in_progress", "in-progress", "live"}
LPL_INFERRED_LIVE_WINDOW = timedelta(hours=6)
LPL_LIVE_PREGAME_WINDOW = timedelta(minutes=30)
LPL_LIVE_STATS_MAX_FRAME_AGE = timedelta(minutes=10)
FLAG_IMAGE_URL_TEMPLATE = "https://flagcdn.com/w80/{country_code_lower}.png"
DEFAULT_LPL_LEAGUE_ID = "98767991314006698"
DEFAULT_LCK_LEAGUE_ID = "98767991310872058"
DEFAULT_MSI_LEAGUE_ID = "98767991325878492"
DEFAULT_MSI_TOURNAMENT_CACHE_HOURS = 12
DEFAULT_TIMEZONE = "America/Los_Angeles"
ODDS_API_IO_LEAGUE_ALIASES = {
    "international-world-cup": DEFAULT_WORLD_CUP_ODDS_API_IO_LEAGUE,
    "usa-nba": DEFAULT_NBA_ODDS_API_IO_LEAGUE,
    "league-of-legends-lpl": DEFAULT_LPL_ODDS_API_IO_LEAGUE,
}
LPL_SEPARATOR_WIDTH = 4
MIN_LPL_SIDEBAR_WIDTH = 240
LOCAL_TEAM_LOGO_DIR = resolve_path(os.path.join("plugins", "sports_dashboard", "assets", "logos"))
LOCAL_CS2_TEAM_LOGO_DIR = os.path.join(LOCAL_TEAM_LOGO_DIR, "cs2")
LOCAL_DOTA2_TEAM_LOGO_DIR = os.path.join(LOCAL_TEAM_LOGO_DIR, "dota2")
LOCAL_LCK_TEAM_LOGO_DIR = os.path.join(LOCAL_TEAM_LOGO_DIR, "lck")
LOCAL_DECOR_DIR = resolve_path(os.path.join("plugins", "sports_dashboard", "assets", "decor"))
LOCAL_LPL_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "lpl.png")
LOCAL_LCK_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "lck.png")
LOCAL_EWC_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "ewc.png")
LOCAL_EWC_GAME_LOGO_DIR = os.path.join(LOCAL_TEAM_LOGO_DIR, "ewc_games")
LOCAL_MSI_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "msi.png")
LOCAL_WORLDCUP_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "worldcup.png")
LOCAL_NBA_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "nba.png")
LOCAL_F1_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "f1.png")
LOCAL_CS_MAJOR_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "cs_major.png")
LOCAL_DOTA2_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "dota2.png")
LOCAL_TI_LOGO_PATH = LOCAL_DOTA2_LOGO_PATH
LOCAL_MLB_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "mlb.png")
LOCAL_WNBA_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "wnba.png")
LOCAL_PGA_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "pga.png")
LOCAL_NFL_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "nfl.png")
LOCAL_NCAA_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "ncaa.png")
LOCAL_WORLDCUP_PITCH_STRIP_PATH = os.path.join(LOCAL_DECOR_DIR, "worldcup_pitch_strip.png")
LOCAL_WORLDCUP_HEADER_BANNER_PATH = os.path.join(LOCAL_DECOR_DIR, "worldcup_header_banner.png")
LOCAL_WORLDCUP_TITLE_WORDMARK_PATH = os.path.join(LOCAL_DECOR_DIR, "worldcup_title_wordmark.png")
LOCAL_PGA_TITLE_WORDMARK_PATH = os.path.join(LOCAL_DECOR_DIR, "pga_tour_title_wordmark.png")
LOCAL_MLB_TITLE_WORDMARK_PATH = os.path.join(LOCAL_DECOR_DIR, "mlb_title_wordmark.png")
LOCAL_WNBA_TITLE_WORDMARK_PATH = os.path.join(LOCAL_DECOR_DIR, "wnba_title_wordmark.png")
LOCAL_NBA_COURT_STRIP_PATH = os.path.join(LOCAL_DECOR_DIR, "nba_court_strip.png")
LOCAL_MLB_HEADER_CUTOUT_PATH = os.path.join(LOCAL_DECOR_DIR, "mlb_header_cutout.png")
LOCAL_WNBA_HEADER_CUTOUT_PATH = os.path.join(LOCAL_DECOR_DIR, "wnba_header_cutout.png")
LOCAL_PGA_HEADER_CUTOUT_PATH = os.path.join(LOCAL_DECOR_DIR, "pga_header_cutout.png")
LOCAL_NFL_HEADER_CUTOUT_PATH = os.path.join(LOCAL_DECOR_DIR, "nfl_header_cutout.png")
LOCAL_NCAA_HEADER_CUTOUT_PATH = os.path.join(LOCAL_DECOR_DIR, "ncaa_header_cutout.png")
SPORT_HEADER_CUTOUT_SCALE = 1.24
SPORT_HEADER_CUTOUT_LEFT_BIAS = 0.45
SPORT_HEADER_CUTOUT_TITLE_GAP = 104
PGA_HEADER_CUTOUT_X_OFFSET = 22
LOCAL_NBA_EMPTY_SLOT_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "nba_empty_slot_filler.png")
LOCAL_NBA_OFFSEASON_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "nba_offseason_filler.png")
LOCAL_NBA_OFFSEASON_ACCENT_PATH = os.path.join(LOCAL_DECOR_DIR, "nba_offseason_accent.png")
LOCAL_PGA_FAIRWAY_STRIP_PATH = os.path.join(LOCAL_DECOR_DIR, "pga_fairway_strip.png")
LOCAL_LPL_MARBLE_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "lpl_marble_filler.png")
LOCAL_LPL_MSI_NEXT_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "lpl_msi_next_filler.png")
LOCAL_LPL_MSI_OFFSEASON_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "lpl_msi_offseason_filler.png")
LOCAL_LPL_MSI_OFFSEASON_FILLER_PATHS = (
    os.path.join(LOCAL_DECOR_DIR, "lpl_msi_offseason_filler_01.png"),
    os.path.join(LOCAL_DECOR_DIR, "lpl_msi_offseason_filler_02.png"),
)
LOCAL_LPL_MSI_CARD_ACCENT_PATH = os.path.join(LOCAL_DECOR_DIR, "lpl_msi_card_accent.png")
LOCAL_LPL_MSI_CARD_ACCENT_DIR = os.path.join(LOCAL_DECOR_DIR, "lpl_msi_card_accents")
LOL_HEADER_LOGO_SIZE = (74, 38)
MSI_HEADER_LOGO_SCALE = 1.4
MSI_HEADER_LOGO_SIZE = (
    int(round(LOL_HEADER_LOGO_SIZE[0] * MSI_HEADER_LOGO_SCALE)),
    int(round(LOL_HEADER_LOGO_SIZE[1] * MSI_HEADER_LOGO_SCALE)),
)
LOLESPORTS_API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
LOLESPORTS_SCHEDULE_URL = (
    "https://esports-api.lolesports.com/persisted/gw/getSchedule"
    "?hl=en-US&leagueId={league_id}"
)
LOLESPORTS_TOURNAMENTS_URL = (
    "https://esports-api.lolesports.com/persisted/gw/getTournamentsForLeague"
    "?hl=en-US&leagueId={league_id}"
)
LOLESPORTS_LIVE_URL = "https://esports-api.lolesports.com/persisted/gw/getLive?hl=en-US"
LOLESPORTS_EVENT_DETAILS_URL = "https://esports-api.lolesports.com/persisted/gw/getEventDetails?hl=en-US&id={event_id}"
LOLESPORTS_LIVE_STATS_WINDOW_URL = "https://feed.lolesports.com/livestats/v1/window/{game_id}"
BO3_API_BASE_URL = "https://api.bo3.gg/api/v1"
TEAM_LOGO_CACHE = ImageLRUCache(max_entries=128, max_bytes=20 * 1024 * 1024)
FLAG_IMAGE_CACHE = ImageLRUCache(max_entries=128, max_bytes=20 * 1024 * 1024)

EWC_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
EWC_GAME_NAME_OVERRIDES = {
    "apex-legends": "Apex Legends",
    "call-of-duty-black-ops-7": "Call of Duty: Black Ops 7",
    "call-of-duty-warzone": "Call of Duty: Warzone",
    "chess": "Chess",
    "cod-blackops": "Call of Duty: Black Ops 7",
    "cod-warzone": "Call of Duty: Warzone",
    "counter-strike-2": "Counter-Strike 2",
    "crossfire": "Crossfire",
    "cs2": "Counter-Strike 2",
    "dota2": "Dota 2",
    "ea-sports-fc-26": "EA Sports FC 26",
    "eafc": "EA Sports FC 26",
    "fatal-fury": "Fatal Fury",
    "fortnite": "Fortnite",
    "free-fire": "Free Fire",
    "honor-of-kings": "Honor of Kings",
    "league-of-legends": "League of Legends",
    "mlbb": "Mobile Legends: Bang Bang",
    "mlbb-women": "MLBB Women",
    "mobile-legends-bang-bang": "Mobile Legends: Bang Bang",
    "mobile-legends-bang-bang-women": "MLBB Women",
    "overwatch": "Overwatch 2",
    "overwatch-2": "Overwatch 2",
    "pmwc": "PUBG Mobile World Cup",
    "pubg-battlegrounds": "PUBG",
    "pubg-mobile": "PUBG Mobile",
    "rainbow-six-siege": "Rainbow Six Siege",
    "rainbow-six-siege-x": "Rainbow Six Siege X",
    "rocket-league": "Rocket League",
    "street-fighter-6": "Street Fighter 6",
    "street-fighter6": "Street Fighter 6",
    "teamfight-tactics": "Teamfight Tactics",
    "tekken-8": "TEKKEN 8",
    "tekken8": "TEKKEN 8",
    "trackmania": "Trackmania",
    "valorant": "VALORANT",
}

EWC_OFFICIAL_GAME_LOGO_SLUGS = (
    "apex-legends",
    "dota2",
    "fatal-fury",
    "valorant",
    "mlbb-women",
    "league-of-legends",
    "free-fire",
    "teamfight-tactics",
    "pubg-battlegrounds",
    "eafc",
    "mlbb",
    "overwatch",
    "street-fighter6",
    "honor-of-kings",
    "cod-warzone",
    "rainbow-six-siege",
    "tekken8",
    "cod-blackops",
    "pmwc",
    "chess",
    "rocket-league",
    "crossfire",
    "cs2",
    "fortnite",
    "trackmania",
)
EWC_GAME_LOGO_FILES = {slug: f"{slug}.png" for slug in EWC_OFFICIAL_GAME_LOGO_SLUGS}
EWC_GAME_LOGO_ALIASES = {
    "call-of-duty-black-ops-7": "cod-blackops",
    "call-of-duty-warzone": "cod-warzone",
    "counter-strike-2": "cs2",
    "dota-2": "dota2",
    "ea-sports-fc-26": "eafc",
    "mobile-legends-bang-bang": "mlbb",
    "mobile-legends-bang-bang-women": "mlbb-women",
    "overwatch-2": "overwatch",
    "pubg-mobile": "pmwc",
    "pubg-mobile-world-cup": "pmwc",
    "rainbow-six-siege-x": "rainbow-six-siege",
    "street-fighter-6": "street-fighter6",
    "tekken-8": "tekken8",
}

LPL_ODDS_TEAM_ALIASES = {
    "AL": ("Anyones Legend", "Anyone's Legend", "Anyone Legend", "AL"),
    "BLG": ("Bilibili Gaming", "Bilibili", "BLG"),
    "EDG": ("Edward Gaming", "EDG"),
    "FPX": ("FunPlus Phoenix", "Funplus Phoenix", "FPX"),
    "IG": ("Invictus Gaming", "iG", "IG"),
    "JDG": ("JD Gaming", "Jingdong Gaming", "JDG"),
    "LGD": ("LGD Gaming", "LGD"),
    "LNG": ("LNG Esports", "LNG"),
    "NIP": ("Ninjas in Pyjamas", "NIP"),
    "OMG": ("Oh My God", "OMG"),
    "RA": ("Rare Atom", "RA"),
    "RNG": ("Royal Never Give Up", "RNG"),
    "TES": ("TOP Esports", "Top Esports", "TES"),
    "TT": ("ThunderTalk Gaming", "ThunderTalk", "TT Gaming", "TT"),
    "UP": ("Ultra Prime", "UP"),
    "WBG": ("Weibo Gaming", "WBG"),
    "WE": ("Team WE", "WE"),
}

LPL_TEAM_ZH_NAMES = {
    "AL": "AL",
    "BLG": "\u54d4\u54e9\u54d4\u54e9",
    "EDG": "EDG",
    "FPX": "FPX",
    "IG": "iG",
    "JDG": "\u4eac\u4e1c",
    "LGD": "LGD",
    "LNG": "\u674e\u5b81",
    "NIP": "NIP",
    "OMG": "OMG",
    "RA": "RA",
    "RNG": "RNG",
    "TES": "\u6ed4\u640f",
    "TT": "TT",
    "UP": "UP",
    "WBG": "\u5fae\u535a",
    "WE": "\u897f\u5b89WE",
}

BO3_LPL_TEAM_SLUGS = {
    "AL": "anyones-legend-lol",
    "BLG": "bilibili-gaming-lol",
    "EDG": "edward-gaming-lol",
    "FPX": "funplus-phoenix-lol",
    "IG": "invictus-gaming-lol",
    "JDG": "jd-gaming-lol",
    "LGD": "lgd-gaming-lol",
    "LNG": "lng-esports-lol",
    "NIP": "ninjas-in-pyjamas-lol",
    "OMG": "oh-my-god-lol",
    "RA": "rare-atom-lol",
    "RNG": "royal-never-give-up-lol",
    "TES": "top-esports-lol",
    "TT": "thundertalk-gaming-lol",
    "UP": "ultra-prime-lol",
    "WBG": "weibo-gaming-lol",
    "WE": "team-we-lol",
}

NBA_TEAM_ZH_NAMES = {
    "ATL": "\u8001\u9e70",
    "BKN": "\u7bee\u7f51",
    "BOS": "\u51ef\u5c14\u7279\u4eba",
    "CHA": "\u9ec4\u8702",
    "CHI": "\u516c\u725b",
    "CLE": "\u9a91\u58eb",
    "DAL": "\u72ec\u884c\u4fa0",
    "DEN": "\u6398\u91d1",
    "DET": "\u6d3b\u585e",
    "GS": "\u52c7\u58eb",
    "GSW": "\u52c7\u58eb",
    "HOU": "\u706b\u7bad",
    "IND": "\u6b65\u884c\u8005",
    "LAC": "\u5feb\u8239",
    "LAL": "\u6e56\u4eba",
    "MEM": "\u7070\u718a",
    "MIA": "\u70ed\u706b",
    "MIL": "\u96c4\u9e7f",
    "MIN": "\u68ee\u6797\u72fc",
    "NO": "\u9e48\u9e55",
    "NOP": "\u9e48\u9e55",
    "NY": "\u5c3c\u514b\u65af",
    "NYK": "\u5c3c\u514b\u65af",
    "OKC": "\u96f7\u9706",
    "ORL": "\u9b54\u672f",
    "PHI": "76\u4eba",
    "PHX": "\u592a\u9633",
    "POR": "\u5f00\u62d3\u8005",
    "SA": "\u9a6c\u523a",
    "SAS": "\u9a6c\u523a",
    "SAC": "\u56fd\u738b",
    "TOR": "\u731b\u9f99",
    "UTA": "\u7235\u58eb",
    "WSH": "\u5947\u624d",
}

NBA_TEAM_ZH_FULL_NAMES = {
    "ATL": "\u4e9a\u7279\u5170\u5927\u8001\u9e70",
    "BKN": "\u5e03\u9c81\u514b\u6797\u7bee\u7f51",
    "BOS": "\u6ce2\u58eb\u987f\u51ef\u5c14\u7279\u4eba",
    "CHA": "\u590f\u6d1b\u7279\u9ec4\u8702",
    "CHI": "\u829d\u52a0\u54e5\u516c\u725b",
    "CLE": "\u514b\u5229\u592b\u5170\u9a91\u58eb",
    "DAL": "\u8fbe\u62c9\u65af\u72ec\u884c\u4fa0",
    "DEN": "\u4e39\u4f5b\u6398\u91d1",
    "DET": "\u5e95\u7279\u5f8b\u6d3b\u585e",
    "GS": "\u91d1\u5dde\u52c7\u58eb",
    "GSW": "\u91d1\u5dde\u52c7\u58eb",
    "HOU": "\u4f11\u65af\u987f\u706b\u7bad",
    "IND": "\u5370\u7b2c\u5b89\u7eb3\u6b65\u884c\u8005",
    "LAC": "\u6d1b\u6749\u77f6\u5feb\u8239",
    "LAL": "\u6d1b\u6749\u77f6\u6e56\u4eba",
    "MEM": "\u5b5f\u83f2\u65af\u7070\u718a",
    "MIA": "\u8fc8\u963f\u5bc6\u70ed\u706b",
    "MIL": "\u5bc6\u5c14\u6c83\u57fa\u96c4\u9e7f",
    "MIN": "\u660e\u5c3c\u82cf\u8fbe\u68ee\u6797\u72fc",
    "NO": "\u65b0\u5965\u5c14\u826f\u9e48\u9e55",
    "NOP": "\u65b0\u5965\u5c14\u826f\u9e48\u9e55",
    "NY": "\u7ebd\u7ea6\u5c3c\u514b\u65af",
    "NYK": "\u7ebd\u7ea6\u5c3c\u514b\u65af",
    "OKC": "\u4fc4\u514b\u62c9\u8377\u9a6c\u57ce\u96f7\u9706",
    "ORL": "\u5965\u5170\u591a\u9b54\u672f",
    "PHI": "\u8d39\u57ce76\u4eba",
    "PHX": "\u83f2\u5c3c\u514b\u65af\u592a\u9633",
    "POR": "\u6ce2\u7279\u5170\u5f00\u62d3\u8005",
    "SA": "\u5723\u5b89\u4e1c\u5c3c\u5965\u9a6c\u523a",
    "SAS": "\u5723\u5b89\u4e1c\u5c3c\u5965\u9a6c\u523a",
    "SAC": "\u8428\u514b\u62c9\u95e8\u6258\u56fd\u738b",
    "TOR": "\u591a\u4f26\u591a\u731b\u9f99",
    "UTA": "\u72b9\u4ed6\u7235\u58eb",
    "WSH": "\u534e\u76db\u987f\u5947\u624d",
}

WNBA_TEAM_ZH_NAMES = {
    "ATL": "\u68a6\u60f3",
    "CHI": "\u5929\u7a7a",
    "CON": "\u592a\u9633",
    "CONN": "\u592a\u9633",
    "DAL": "\u98de\u7ffc",
    "GS": "\u5973\u6b66\u795e",
    "GSV": "\u5973\u6b66\u795e",
    "IND": "\u72c2\u70ed",
    "LA": "\u706b\u82b1",
    "LAS": "\u706b\u82b1",
    "LV": "\u738b\u724c",
    "LVA": "\u738b\u724c",
    "MIN": "\u5c71\u732b",
    "NY": "\u81ea\u7531\u4eba",
    "NYL": "\u81ea\u7531\u4eba",
    "PHO": "\u6c34\u661f",
    "PHX": "\u6c34\u661f",
    "POR": "\u6ce2\u7279\u5170\u706b\u7130",
    "SEA": "\u98ce\u66b4",
    "TOR": "\u591a\u4f26\u591a\u8282\u594f",
    "WAS": "\u795e\u79d8\u4eba",
    "WSH": "\u795e\u79d8\u4eba",
}

WNBA_TEAM_ZH_FULL_NAMES = {
    "ATL": "亚特兰大梦想",
    "CHI": "芝加哥天空",
    "CON": "康涅狄格太阳",
    "CONN": "康涅狄格太阳",
    "DAL": "达拉斯飞翼",
    "GS": "金州女武神",
    "GSV": "金州女武神",
    "IND": "印第安纳狂热",
    "LA": "洛杉矶火花",
    "LAS": "洛杉矶火花",
    "LV": "拉斯维加斯王牌",
    "LVA": "拉斯维加斯王牌",
    "MIN": "明尼苏达山猫",
    "NY": "纽约自由人",
    "NYL": "纽约自由人",
    "PHO": "\u83f2\u5c3c\u514b\u65af\u6c34\u661f",
    "PHX": "菲尼克斯水星",
    "POR": "波特兰火焰",
    "SEA": "西雅图风暴",
    "TOR": "多伦多节奏",
    "WAS": "华盛顿神秘人",
    "WSH": "华盛顿神秘人",
}

WNBA_TEAM_ALIAS_TO_CODE = {
    "aces": "LV",
    "lasvegasaces": "LV",
    "storm": "SEA",
    "seattlestorm": "SEA",
    "liberty": "NY",
    "newyorkliberty": "NY",
    "fever": "IND",
    "indianafever": "IND",
    "lynx": "MIN",
    "minnesotalynx": "MIN",
    "sun": "CON",
    "connecticutsun": "CON",
    "sky": "CHI",
    "chicagosky": "CHI",
    "wings": "DAL",
    "dallaswings": "DAL",
    "mercury": "PHX",
    "phoenixmercury": "PHX",
    "fire": "POR",
    "portlandfire": "POR",
    "portland": "POR",
    "tempo": "TOR",
    "torontotempo": "TOR",
    "toronto": "TOR",
    "mystics": "WAS",
    "washingtonmystics": "WAS",
    "sparks": "LA",
    "losangelessparks": "LA",
    "dream": "ATL",
    "atlantadream": "ATL",
    "valkyries": "GS",
    "goldenstatevalkyries": "GS",
    "valks": "GS",
    "goldenstatevalks": "GS",
}

WNBA_ESPN_LOGO_CODES = {
    "ATL": "atl",
    "CHI": "chi",
    "CON": "conn",
    "CONN": "conn",
    "DAL": "dal",
    "GS": "gs",
    "GSV": "gs",
    "IND": "ind",
    "LA": "la",
    "LAS": "la",
    "LV": "lv",
    "LVA": "lv",
    "MIN": "min",
    "NY": "ny",
    "NYL": "ny",
    "PHO": "phx",
    "PHX": "phx",
    "POR": "por",
    "SEA": "sea",
    "TOR": "tor",
    "WAS": "wsh",
    "WSH": "wsh",
}

PGA_EVENT_ZH_NAMES = {
    "americanexpress": "\u7f8e\u56fd\u8fd0\u901a\u8d5b",
    "arnoldpalmerinvitational": "\u963f\u8bfa\u5fb7\u5e15\u5c14\u9ed8\u9080\u8bf7\u8d5b",
    "atandtpebblebeachproam": "AT&T\u5706\u77f3\u6ee9\u804c\u4e1a\u4e1a\u4f59\u914d\u5bf9\u8d5b",
    "attpebblebeachproam": "AT&T\u5706\u77f3\u6ee9\u804c\u4e1a\u4e1a\u4f59\u914d\u5bf9\u8d5b",
    "bankofutahchampionship": "\u72b9\u4ed6\u94f6\u884c\u9526\u6807\u8d5b",
    "barbasolchampionship": "\u5df4\u5c14\u5df4\u7d22\u9526\u6807\u8d5b",
    "baycurrentclassic": "Baycurrent\u7cbe\u82f1\u8d5b",
    "biltmorechampionship": "\u6bd4\u5c14\u7279\u83ab\u5c14\u9526\u6807\u8d5b",
    "bmwchampionship": "BMW\u9526\u6807\u8d5b",
    "butterfieldbermudachampionship": "\u5df4\u7279\u83f2\u5c14\u5fb7\u767e\u6155\u5927\u9526\u6807\u8d5b",
    "byronnelson": "\u62dc\u4f26\u5c3c\u5c14\u68ee\u8d5b",
    "cadillacchampionship": "\u51ef\u8fea\u62c9\u514b\u9526\u6807\u8d5b",
    "canadianopen": "RBC\u52a0\u62ff\u5927\u516c\u5f00\u8d5b",
    "charlesschwabchallenge": "\u5609\u4fe1\u6311\u6218\u8d5b",
    "cjcupbyronnelson": "\u62dc\u4f26\u5c3c\u5c14\u68ee\u8d5b",
    "cognizantclassic": "\u79d1\u683c\u5c3c\u8d5e\u7279\u7cbe\u82f1\u8d5b",
    "coralespuntacanachampionship": "\u79d1\u62c9\u83b1\u65af\u84ec\u5854\u5361\u7eb3\u9526\u6807\u8d5b",
    "fedexstjudechampionship": "\u8054\u90a6\u5feb\u9012\u5723\u88d8\u5fb7\u9526\u6807\u8d5b",
    "fedexcupplayoffs": "\u8054\u90a6\u5feb\u9012\u676f\u5b63\u540e\u8d5b",
    "farmersinsuranceopen": "\u519c\u592b\u4fdd\u9669\u516c\u5f00\u8d5b",
    "genesisinvitational": "\u6377\u5c3c\u8d5b\u601d\u9080\u8bf7\u8d5b",
    "genesisscottishopen": "\u82cf\u683c\u5170\u516c\u5f00\u8d5b",
    "goodgoodchampionship": "Good Good\u9526\u6807\u8d5b",
    "grantthorntoninvitational": "\u683c\u5170\u7279\u6851\u987f\u9080\u8bf7\u8d5b",
    "heroworldchallenge": "\u82f1\u96c4\u4e16\u754c\u6311\u6218\u8d5b",
    "iscochampionship": "ISCO\u9526\u6807\u8d5b",
    "johndeereclassic": "\u7ea6\u7ff0\u8fea\u5c14\u7cbe\u82f1\u8d5b",
    "memorialtournament": "\u7eaa\u5ff5\u9ad8\u7403\u8d5b",
    "mexicoopen": "\u58a8\u897f\u54e5\u516c\u5f00\u8d5b",
    "mexicoopenatvidanta": "\u58a8\u897f\u54e5\u516c\u5f00\u8d5b",
    "myrtlebeachclassic": "ONEflight\u9ed8\u7279\u5c14\u6bd4\u5947\u7cbe\u82f1\u8d5b",
    "oneflightmyrtlebeachclassic": "ONEflight\u9ed8\u7279\u5c14\u6bd4\u5947\u7cbe\u82f1\u8d5b",
    "openchampionship": "\u82f1\u56fd\u516c\u5f00\u8d5b",
    "phoenixopen": "\u51e4\u51f0\u57ce\u516c\u5f00\u8d5b",
    "presidentscup": "\u603b\u7edf\u676f",
    "puertoricoopen": "\u6ce2\u591a\u9ece\u5404\u516c\u5f00\u8d5b",
    "masters": "\u7f8e\u56fd\u540d\u4eba\u8d5b",
    "masterstournament": "\u7f8e\u56fd\u540d\u4eba\u8d5b",
    "pgachampionship": "PGA\u9526\u6807\u8d5b",
    "playerschampionship": "\u7403\u5458\u9526\u6807\u8d5b",
    "rbcheritage": "RBC\u4f20\u7edf\u8d5b",
    "rbccanadianopen": "RBC\u52a0\u62ff\u5927\u516c\u5f00\u8d5b",
    "rocketclassic": "\u706b\u7bad\u7cbe\u82f1\u8d5b",
    "rocketmortgageclassic": "\u706b\u7bad\u7cbe\u82f1\u8d5b",
    "rsmclassic": "RSM\u7cbe\u82f1\u8d5b",
    "scottishopen": "\u82cf\u683c\u5170\u516c\u5f00\u8d5b",
    "sentrytournamentofchampions": "\u54e8\u5175\u51a0\u519b\u8d5b",
    "sonyopeninhawaii": "\u590f\u5a01\u5937\u7d22\u5c3c\u516c\u5f00\u8d5b",
    "texaschildrenshoustonopen": "\u5fb7\u5dde\u513f\u7ae5\u4f11\u65af\u6566\u516c\u5f00\u8d5b",
    "theamericanexpress": "\u7f8e\u56fd\u8fd0\u901a\u8d5b",
    "thecjcupbyronnelson": "\u62dc\u4f26\u5c3c\u5c14\u68ee\u8d5b",
    "thegenesisinvitational": "\u6377\u5c3c\u8d5b\u601d\u9080\u8bf7\u8d5b",
    "themasters": "\u7f8e\u56fd\u540d\u4eba\u8d5b",
    "thememorialtournament": "\u7eaa\u5ff5\u9ad8\u7403\u8d5b",
    "theopen": "\u82f1\u56fd\u516c\u5f00\u8d5b",
    "theopenchampionship": "\u82f1\u56fd\u516c\u5f00\u8d5b",
    "theplayerschampionship": "\u7403\u5458\u9526\u6807\u8d5b",
    "thescottishopen": "\u82cf\u683c\u5170\u516c\u5f00\u8d5b",
    "thesentry": "\u54e8\u5175\u51a0\u519b\u8d5b",
    "thetourchampionship": "\u5de1\u56de\u9526\u6807\u8d5b",
    "tourchampionship": "\u5de1\u56de\u9526\u6807\u8d5b",
    "travelerschampionship": "\u65c5\u884c\u8005\u9526\u6807\u8d5b",
    "truistchampionship": "Truist\u9526\u6807\u8d5b",
    "usopen": "\u7f8e\u56fd\u516c\u5f00\u8d5b",
    "usopenchampionship": "\u7f8e\u56fd\u516c\u5f00\u8d5b",
    "usopengolfchampionship": "\u7f8e\u56fd\u516c\u5f00\u8d5b",
    "valerotexasopen": "\u5f97\u514b\u8428\u65af\u516c\u5f00\u8d5b",
    "valsparchampionship": "\u74e6\u5c14\u65af\u5e15\u9526\u6807\u8d5b",
    "vidantaworldmexicoopen": "VidantaWorld\u58a8\u897f\u54e5\u516c\u5f00\u8d5b",
    "wellsfargochampionship": "\u5bcc\u56fd\u94f6\u884c\u9526\u6807\u8d5b",
    "wmphoenixopen": "WM\u51e4\u51f0\u57ce\u516c\u5f00\u8d5b",
    "worldwidetechnologychampionship": "World Wide Technology\u9526\u6807\u8d5b",
    "wyndhamchampionship": "\u6e29\u5fb7\u59c6\u9526\u6807\u8d5b",
    "zurichclassicofneworleans": "\u65b0\u5965\u5c14\u826f\u82cf\u9ece\u4e16\u7cbe\u82f1\u8d5b",
    "3mopen": "3M\u516c\u5f00\u8d5b",
}

PGA_COUNTRY_ZH_NAME_OVERRIDES = {
    "NIR": "\u5317\u7231\u5c14\u5170",
}

MLB_TEAM_ZH_NAMES = {
    "ARI": "\u54cd\u5c3e\u86c7",
    "AZ": "\u54cd\u5c3e\u86c7",
    "ATL": "\u52c7\u58eb",
    "BAL": "\u91d1\u83ba",
    "BOS": "\u7ea2\u889c",
    "CHC": "\u5c0f\u718a",
    "CHW": "\u767d\u889c",
    "CWS": "\u767d\u889c",
    "CIN": "\u7ea2\u4eba",
    "CLE": "\u5b88\u62a4\u8005",
    "COL": "\u843d\u57fa",
    "DET": "\u8001\u864e",
    "HOU": "\u592a\u7a7a\u4eba",
    "KC": "\u7687\u5bb6",
    "KCR": "\u7687\u5bb6",
    "LAA": "\u5929\u4f7f",
    "LAD": "\u9053\u5947",
    "MIA": "\u9a6c\u6797\u9c7c",
    "MIL": "\u917f\u9152\u4eba",
    "MIN": "\u53cc\u57ce",
    "NYM": "\u5927\u90fd\u4f1a",
    "NYY": "\u6d0b\u57fa",
    "OAK": "\u8fd0\u52a8\u5bb6",
    "ATH": "\u8fd0\u52a8\u5bb6",
    "PHI": "\u8d39\u57ce\u4eba",
    "PIT": "\u6d77\u76d7",
    "SD": "\u6559\u58eb",
    "SDP": "\u6559\u58eb",
    "SF": "\u5de8\u4eba",
    "SFG": "\u5de8\u4eba",
    "SEA": "\u6c34\u624b",
    "STL": "\u7ea2\u96c0",
    "TB": "\u5149\u8292",
    "TEX": "\u6e38\u9a91\u5175",
    "TOR": "\u84dd\u9e1f",
    "WAS": "\u56fd\u6c11",
    "WSH": "\u56fd\u6c11",
}

MLB_TEAM_ZH_FULL_NAMES = {
    "ARI": "亚利桑那响尾蛇",
    "AZ": "\u4e9a\u5229\u6851\u90a3\u54cd\u5c3e\u86c7",
    "ATL": "亚特兰大勇士",
    "BAL": "巴尔的摩金莺",
    "BOS": "波士顿红袜",
    "CHC": "芝加哥小熊",
    "CHW": "\u829d\u52a0\u54e5\u767d\u889c",
    "CWS": "芝加哥白袜",
    "CIN": "辛辛那提红人",
    "CLE": "克利夫兰守护者",
    "COL": "科罗拉多落基",
    "DET": "底特律老虎",
    "HOU": "休斯顿太空人",
    "KC": "堪萨斯城皇家",
    "KCR": "\u582a\u8428\u65af\u57ce\u7687\u5bb6",
    "LAA": "洛杉矶天使",
    "LAD": "洛杉矶道奇",
    "MIA": "迈阿密马林鱼",
    "MIL": "密尔沃基酿酒人",
    "MIN": "明尼苏达双城",
    "NYM": "纽约大都会",
    "NYY": "纽约洋基",
    "OAK": "运动家",
    "ATH": "运动家",
    "PHI": "费城人",
    "PIT": "匹兹堡海盗",
    "SD": "圣迭戈教士",
    "SDP": "\u5723\u8fed\u6208\u6559\u58eb",
    "SF": "旧金山巨人",
    "SFG": "\u65e7\u91d1\u5c71\u5de8\u4eba",
    "SEA": "西雅图水手",
    "STL": "圣路易斯红雀",
    "TB": "坦帕湾光芒",
    "TEX": "德克萨斯游骑兵",
    "TOR": "多伦多蓝鸟",
    "WAS": "\u534e\u76db\u987f\u56fd\u6c11",
    "WSH": "华盛顿国民",
}

MLB_TEAM_CODES = {
    "Arizona Diamondbacks": "ARI",
    "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",
    "Detroit Tigers": "DET",
    "Houston Astros": "HOU",
    "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",
    "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Oakland Athletics": "OAK",
    "Athletics": "ATH",
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD",
    "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}

MLB_TEAM_NAME_ALIASES = {
    "ARI": ("AZ", "Arizona Diamondbacks", "Diamondbacks", "D-backs", "Dbacks", "Arizona"),
    "ATL": ("Atlanta Braves", "Braves"),
    "BAL": ("Baltimore Orioles", "Orioles"),
    "BOS": ("Boston Red Sox", "Red Sox"),
    "CHC": ("Chicago Cubs", "Cubs"),
    "CWS": ("CHW", "Chicago White Sox", "White Sox", "Chi White Sox"),
    "CIN": ("Cincinnati Reds", "Reds"),
    "CLE": ("Cleveland Guardians", "Guardians"),
    "COL": ("Colorado Rockies", "Rockies"),
    "DET": ("Detroit Tigers", "Tigers"),
    "HOU": ("Houston Astros", "Astros"),
    "KC": ("KCR", "Kansas City Royals", "Royals"),
    "LAA": ("Los Angeles Angels", "LA Angels", "Angels", "Anaheim Angels"),
    "LAD": ("Los Angeles Dodgers", "LA Dodgers", "Dodgers"),
    "MIA": ("Miami Marlins", "Marlins"),
    "MIL": ("Milwaukee Brewers", "Brewers"),
    "MIN": ("Minnesota Twins", "Twins"),
    "NYM": ("New York Mets", "Mets", "NY Mets"),
    "NYY": ("New York Yankees", "Yankees", "NY Yankees"),
    "OAK": ("Oakland Athletics", "Oakland A's", "Oakland As"),
    "ATH": ("Athletics", "Sacramento Athletics", "A's", "As"),
    "PHI": ("Philadelphia Phillies", "Phillies"),
    "PIT": ("Pittsburgh Pirates", "Pirates"),
    "SD": ("SDP", "San Diego Padres", "Padres"),
    "SF": ("SFG", "San Francisco Giants", "SF Giants", "Giants"),
    "SEA": ("Seattle Mariners", "Mariners"),
    "STL": ("St. Louis Cardinals", "St Louis Cardinals", "Cardinals"),
    "TB": ("Tampa Bay Rays", "Rays"),
    "TEX": ("Texas Rangers", "Rangers"),
    "TOR": ("Toronto Blue Jays", "Blue Jays"),
    "WSH": ("WAS", "Washington Nationals", "Nationals"),
}

MLB_ESPN_LOGO_CODES = {
    "AZ": "ari",
    "CHW": "chw",
    "CWS": "chw",
    "ATH": "ath",
    "KCR": "kc",
    "OAK": "oak",
    "SDP": "sd",
    "SFG": "sf",
    "WAS": "wsh",
}

NFL_TEAM_ZH_NAMES = {
    "ARI": "\u7ea2\u96c0",
    "ARZ": "\u7ea2\u96c0",
    "ATL": "\u730e\u9e70",
    "BAL": "\u4e4c\u9e26",
    "BUF": "\u6bd4\u5c14",
    "CAR": "\u9ed1\u8c79",
    "CHI": "\u718a",
    "CIN": "\u731b\u864e",
    "CLE": "\u5e03\u6717",
    "DAL": "\u725b\u4ed4",
    "DEN": "\u91ce\u9a6c",
    "DET": "\u96c4\u72ee",
    "GB": "\u5305\u88c5\u5de5",
    "HOU": "\u5fb7\u5dde\u4eba",
    "IND": "\u5c0f\u9a6c",
    "JAC": "\u7f8e\u6d32\u864e",
    "JAX": "\u7f8e\u6d32\u864e",
    "KC": "\u914b\u957f",
    "LAC": "\u95ea\u7535",
    "LAR": "\u516c\u7f8a",
    "LV": "\u7a81\u88ad\u8005",
    "MIA": "\u6d77\u8c5a",
    "MIN": "\u7ef4\u4eac\u4eba",
    "NE": "\u7231\u56fd\u8005",
    "NO": "\u5723\u5f92",
    "NYG": "\u5de8\u4eba",
    "NYJ": "\u55b7\u6c14\u673a",
    "PHI": "\u8001\u9e70",
    "PIT": "\u94a2\u4eba",
    "SEA": "\u6d77\u9e70",
    "SF": "49\u4eba",
    "TB": "\u6d77\u76d7",
    "TEN": "\u6cf0\u5766",
    "WAS": "\u6307\u6325\u5b98",
    "WSH": "\u6307\u6325\u5b98",
}

NFL_TEAM_ZH_FULL_NAMES = {
    "ARI": "亚利桑那红雀",
    "ARZ": "\u4e9a\u5229\u6851\u90a3\u7ea2\u96c0",
    "ATL": "亚特兰大猎鹰",
    "BAL": "巴尔的摩乌鸦",
    "BUF": "布法罗比尔",
    "CAR": "卡罗莱纳黑豹",
    "CHI": "芝加哥熊",
    "CIN": "辛辛那提猛虎",
    "CLE": "克利夫兰布朗",
    "DAL": "达拉斯牛仔",
    "DEN": "丹佛野马",
    "DET": "底特律雄狮",
    "GB": "绿湾包装工",
    "HOU": "休斯顿德州人",
    "IND": "印第安纳波利斯小马",
    "JAC": "\u6770\u514b\u900a\u7ef4\u5c14\u7f8e\u6d32\u864e",
    "JAX": "杰克逊维尔美洲虎",
    "KC": "堪萨斯城酋长",
    "LAC": "洛杉矶闪电",
    "LAR": "洛杉矶公羊",
    "LV": "拉斯维加斯突袭者",
    "MIA": "迈阿密海豚",
    "MIN": "明尼苏达维京人",
    "NE": "新英格兰爱国者",
    "NO": "新奥尔良圣徒",
    "NYG": "纽约巨人",
    "NYJ": "纽约喷气机",
    "PHI": "费城老鹰",
    "PIT": "匹兹堡钢人",
    "SEA": "西雅图海鹰",
    "SF": "旧金山49人",
    "TB": "坦帕湾海盗",
    "TEN": "田纳西泰坦",
    "WAS": "华盛顿指挥官",
    "WSH": "华盛顿指挥官",
}

NFL_TEAM_NAME_ALIASES = {
    "ARI": ("ARZ", "Arizona Cardinals", "Cardinals", "Arizona"),
    "ATL": ("Atlanta Falcons", "Falcons", "Atlanta"),
    "BAL": ("Baltimore Ravens", "Ravens", "Baltimore"),
    "BUF": ("Buffalo Bills", "Bills", "Buffalo"),
    "CAR": ("Carolina Panthers", "Panthers", "Carolina"),
    "CHI": ("Chicago Bears", "Bears", "Chicago"),
    "CIN": ("Cincinnati Bengals", "Bengals", "Cincinnati"),
    "CLE": ("Cleveland Browns", "Browns", "Cleveland"),
    "DAL": ("Dallas Cowboys", "Cowboys", "Dallas"),
    "DEN": ("Denver Broncos", "Broncos", "Denver"),
    "DET": ("Detroit Lions", "Lions", "Detroit"),
    "GB": ("Green Bay Packers", "Packers", "Green Bay"),
    "HOU": ("Houston Texans", "Texans", "Houston"),
    "IND": ("Indianapolis Colts", "Colts", "Indianapolis"),
    "JAX": ("JAC", "Jacksonville Jaguars", "Jaguars", "Jacksonville"),
    "KC": ("Kansas City Chiefs", "Chiefs", "Kansas City"),
    "LAC": ("Los Angeles Chargers", "LA Chargers", "Chargers"),
    "LAR": ("Los Angeles Rams", "LA Rams", "Rams"),
    "LV": ("Las Vegas Raiders", "Raiders", "Las Vegas"),
    "MIA": ("Miami Dolphins", "Dolphins", "Miami"),
    "MIN": ("Minnesota Vikings", "Vikings", "Minnesota"),
    "NE": ("New England Patriots", "Patriots", "New England"),
    "NO": ("New Orleans Saints", "Saints", "New Orleans"),
    "NYG": ("New York Giants", "Giants", "NY Giants"),
    "NYJ": ("New York Jets", "Jets", "NY Jets"),
    "PHI": ("Philadelphia Eagles", "Eagles", "Philadelphia"),
    "PIT": ("Pittsburgh Steelers", "Steelers", "Pittsburgh"),
    "SEA": ("Seattle Seahawks", "Seahawks", "Seattle"),
    "SF": ("San Francisco 49ers", "49ers", "Niners", "San Francisco"),
    "TB": ("Tampa Bay Buccaneers", "Buccaneers", "Bucs", "Tampa Bay"),
    "TEN": ("Tennessee Titans", "Titans", "Tennessee"),
    "WSH": ("Washington Commanders", "Commanders", "Washington"),
    "WAS": ("Washington Commanders", "Commanders", "Washington"),
}

NFL_ESPN_LOGO_CODES = {
    "ARZ": "ari",
    "JAC": "jax",
    "WAS": "wsh",
    "WSH": "wsh",
}

NCAA_ESPN_LOGO_IDS = {
    "AF": "2005",
    "AKR": "2006",
    "ALA": "333",
    "APP": "2026",
    "ARK": "8",
    "ARIZ": "12",
    "ARMY": "349",
    "ASU": "9",
    "AUB": "2",
    "BALL": "2050",
    "BAY": "239",
    "BC": "103",
    "BGSU": "189",
    "BSU": "2085",
    "BUF": "2084",
    "BUFF": "2084",
    "BYU": "252",
    "CAL": "25",
    "CCU": "324",
    "CHAR": "2429",
    "CIN": "2132",
    "CLEM": "228",
    "CLT": "2429",
    "CMU": "2117",
    "CONN": "41",
    "COLO": "38",
    "CSU": "36",
    "DEL": "48",
    "DUKE": "150",
    "ECU": "151",
    "EMU": "2199",
    "FAU": "2226",
    "FIU": "2229",
    "FRES": "278",
    "FSU": "52",
    "GASO": "290",
    "GAST": "2247",
    "GT": "59",
    "HAW": "62",
    "HOU": "248",
    "ILL": "356",
    "IND": "84",
    "IOWA": "2294",
    "ISU": "66",
    "IU": "84",
    "JMU": "256",
    "JVST": "55",
    "JXST": "55",
    "KENN": "338",
    "KENT": "2309",
    "KSU": "2306",
    "KU": "2305",
    "LIB": "2335",
    "LOU": "97",
    "LSU": "99",
    "LT": "2348",
    "M-OH": "193",
    "MARY": "559",
    "MEM": "235",
    "MIA": "2390",
    "MICH": "130",
    "MINN": "135",
    "MISS": "145",
    "MIZ": "142",
    "MIZZ": "142",
    "MRSH": "276",
    "MSST": "344",
    "MSU": "127",
    "NAVY": "2426",
    "NCST": "152",
    "NCSU": "152",
    "ND": "87",
    "NEB": "158",
    "NEV": "2440",
    "NIU": "2459",
    "NMSU": "166",
    "NU": "77",
    "NW": "77",
    "ODU": "295",
    "OHIO": "195",
    "OKST": "197",
    "ORE": "2483",
    "ORST": "204",
    "OSU": "194",
    "OU": "201",
    "PITT": "221",
    "PSU": "213",
    "PUR": "2509",
    "RICE": "242",
    "RUTG": "164",
    "SC": "2579",
    "SDSU": "21",
    "SHSU": "2534",
    "SJSU": "23",
    "SMU": "2567",
    "STAN": "24",
    "SYR": "183",
    "TCU": "2628",
    "TEM": "218",
    "TEMP": "218",
    "TENN": "2633",
    "TEX": "251",
    "TLSA": "202",
    "TOL": "2649",
    "TROY": "2653",
    "TTU": "2641",
    "TULN": "2655",
    "TULSA": "202",
    "UCF": "2116",
    "UCONN": "41",
    "UCLA": "26",
    "UF": "2224",
    "UK": "96",
    "UL": "309",
    "ULL": "309",
    "UNC": "153",
    "UNLV": "2439",
    "USC": "30",
    "USF": "58",
    "USM": "2572",
    "USU": "328",
    "UTAH": "254",
    "UTEP": "2638",
    "UTSA": "2636",
    "UVA": "258",
    "VAN": "238",
    "VAND": "238",
    "VT": "259",
    "WAKE": "154",
    "WASH": "264",
    "WIS": "275",
    "WKU": "98",
    "WMU": "2711",
    "WSU": "265",
    "WVU": "277",
    "WYO": "2751",
    "UGA": "61",
}

NCAA_TEAM_ZH_NAMES = {
    "AF": "\u7a7a\u519b",
    "AKR": "\u963f\u514b\u4f26",
    "ALA": "\u963f\u62c9\u5df4\u9a6c",
    "APP": "\u963f\u5df4\u62c9\u5951\u4e9a\u5dde\u7acb",
    "ARK": "\u963f\u80af\u8272",
    "ARIZ": "\u4e9a\u5229\u6851\u90a3",
    "ARMY": "\u9646\u519b",
    "ASU": "\u4e9a\u5229\u6851\u90a3\u5dde\u7acb",
    "AUB": "\u5965\u672c",
    "BALL": "\u6ce2\u5c14\u5dde\u7acb",
    "BAY": "\u8d1d\u52d2",
    "BC": "\u6ce2\u58eb\u987f\u5b66\u9662",
    "BGSU": "\u9c8d\u7075\u683c\u6797",
    "BUF": "\u5e03\u6cd5\u7f57",
    "BSU": "\u535a\u4f0a\u897f\u5dde\u7acb",
    "BUFF": "\u5e03\u6cd5\u7f57",
    "BYU": "\u6768\u767e\u7ff0",
    "CAL": "\u52a0\u5dde",
    "CCU": "\u5361\u7f57\u6765\u7eb3\u6d77\u5cb8",
    "CLT": "\u590f\u6d1b\u7279",
    "CHAR": "\u590f\u6d1b\u7279",
    "CIN": "\u8f9b\u8f9b\u90a3\u63d0",
    "CLEM": "\u514b\u83b1\u59c6\u68ee",
    "CMU": "\u4e2d\u5bc6\u6b47\u6839",
    "CONN": "\u5eb7\u6d85\u72c4\u683c",
    "COLO": "\u79d1\u7f57\u62c9\u591a",
    "CSU": "\u79d1\u7f57\u62c9\u591a\u5dde\u7acb",
    "DEL": "\u7279\u62c9\u534e",
    "DUKE": "\u675c\u514b",
    "ECU": "\u4e1c\u5361\u7f57\u6765\u7eb3",
    "EMU": "\u4e1c\u5bc6\u6b47\u6839",
    "FAU": "\u4f5b\u7f57\u91cc\u8fbe\u5927\u897f\u6d0b",
    "FIU": "\u4f5b\u7f57\u91cc\u8fbe\u56fd\u9645",
    "FRES": "\u5f17\u96f7\u65af\u8bfa\u5dde\u7acb",
    "FSU": "\u4f5b\u5dde",
    "GASO": "\u4f50\u6cbb\u4e9a\u5357\u65b9",
    "GAST": "\u4f50\u6cbb\u4e9a\u5dde\u7acb",
    "GT": "\u4f50\u6cbb\u4e9a\u7406\u5de5",
    "UGA": "\u4f50\u6cbb\u4e9a",
    "HAW": "\u590f\u5a01\u5937",
    "HOU": "\u4f11\u65af\u987f",
    "ILL": "\u4f0a\u5229\u8bfa\u4f0a",
    "IND": "\u5370\u7b2c\u5b89\u7eb3",
    "IU": "\u5370\u7b2c\u5b89\u7eb3",
    "IOWA": "\u7231\u8377\u534e",
    "ISU": "\u7231\u8377\u534e\u5dde\u7acb",
    "JMU": "\u8a79\u59c6\u65af\u9ea6\u8fea\u900a",
    "JVST": "\u6770\u514b\u900a\u7ef4\u5c14\u5dde\u7acb",
    "JXST": "\u6770\u514b\u900a\u7ef4\u5c14\u5dde\u7acb",
    "KENN": "\u80af\u5c3c\u7d22\u5dde\u7acb",
    "KENT": "\u80af\u7279\u5dde\u7acb",
    "KSU": "\u582a\u8428\u65af\u5dde\u7acb",
    "KU": "\u582a\u8428\u65af",
    "UK": "\u80af\u5854\u57fa",
    "LIB": "\u81ea\u7531",
    "LOU": "\u8def\u6613\u7ef4\u5c14",
    "LSU": "\u8def\u6613\u65af\u5b89\u90a3\u5dde\u7acb",
    "LT": "\u8def\u6613\u65af\u5b89\u90a3\u7406\u5de5",
    "M-OH": "\u8fc8\u963f\u5bc6\u4fc4\u4ea5\u4fc4",
    "MARY": "\u9a6c\u91cc\u5170",
    "MEM": "\u5b5f\u83f2\u65af",
    "MIA": "\u8fc8\u963f\u5bc6",
    "MICH": "\u5bc6\u6b47\u6839",
    "MINN": "\u660e\u5c3c\u82cf\u8fbe",
    "MISS": "\u5bc6\u897f\u897f\u6bd4",
    "MIZ": "\u5bc6\u82cf\u91cc",
    "MIZZ": "\u5bc6\u82cf\u91cc",
    "MRSH": "\u9a6c\u6b47\u5c14",
    "MSST": "\u5bc6\u897f\u897f\u6bd4\u5dde\u7acb",
    "MSU": "\u5bc6\u6b47\u6839\u5dde\u7acb",
    "NAVY": "\u6d77\u519b",
    "NCST": "\u5317\u5361\u5dde\u7acb",
    "NCSU": "\u5317\u5361\u5dde\u7acb",
    "ND": "\u5723\u6bcd",
    "NEB": "\u5185\u5e03\u62c9\u65af\u52a0",
    "NEV": "\u5185\u534e\u8fbe",
    "NIU": "\u5317\u4f0a\u5229\u8bfa\u4f0a",
    "NMSU": "\u65b0\u58a8\u897f\u54e5\u5dde\u7acb",
    "NU": "\u897f\u5317",
    "NW": "\u897f\u5317",
    "ODU": "\u8001\u9053\u660e",
    "OHIO": "\u4fc4\u4ea5\u4fc4",
    "OKST": "\u4fc4\u514b\u62c9\u8377\u9a6c\u5dde\u7acb",
    "ORE": "\u4fc4\u52d2\u5188",
    "ORST": "\u4fc4\u52d2\u5188\u5dde\u7acb",
    "OSU": "\u4fc4\u4ea5\u4fc4\u5dde\u7acb",
    "OU": "\u4fc4\u514b\u62c9\u8377\u9a6c",
    "PSU": "\u5bbe\u5dde\u5dde\u7acb",
    "PITT": "\u5339\u5179\u5821",
    "PUR": "\u666e\u6e21",
    "RICE": "\u83b1\u65af",
    "RUTG": "\u7f57\u683c\u65af",
    "SC": "\u5357\u5361",
    "SDSU": "\u5723\u8fed\u6208\u5dde\u7acb",
    "SHSU": "\u8428\u59c6\u4f11\u65af\u987f",
    "SJSU": "\u5723\u4f55\u585e\u5dde\u7acb",
    "SMU": "\u5357\u65b9\u536b\u7406\u516c\u4f1a",
    "STAN": "\u65af\u5766\u798f",
    "SYR": "\u96ea\u57ce",
    "TCU": "\u5fb7\u514b\u8428\u65af\u57fa\u7763\u6559",
    "TEM": "\u5929\u666e",
    "TEMP": "\u5929\u666e",
    "TENN": "\u7530\u7eb3\u897f",
    "TEX": "\u5fb7\u5dde",
    "TOL": "\u6258\u83b1\u591a",
    "TROY": "\u7279\u6d1b\u4f0a",
    "TTU": "\u5fb7\u5dde\u7406\u5de5",
    "TULN": "\u675c\u5170",
    "TLSA": "\u5854\u5c14\u8428",
    "TULSA": "\u5854\u5c14\u8428",
    "UL": "\u8def\u6613\u65af\u5b89\u90a3",
    "ULL": "\u8def\u6613\u65af\u5b89\u90a3",
    "UNLV": "\u5185\u534e\u8fbe\u62c9\u65af\u7ef4\u52a0\u65af",
    "UCF": "\u4e2d\u4f5b\u7f57\u91cc\u8fbe",
    "UCONN": "\u5eb7\u6d85\u72c4\u683c",
    "UCLA": "\u52a0\u5dde\u6d1b\u6749\u77f6",
    "UF": "\u4f5b\u7f57\u91cc\u8fbe",
    "UNC": "\u5317\u5361",
    "USC": "\u5357\u52a0\u5dde",
    "USF": "\u5357\u4f5b\u7f57\u91cc\u8fbe",
    "USM": "\u5357\u5bc6\u897f\u897f\u6bd4",
    "USU": "\u72b9\u4ed6\u5dde\u7acb",
    "UTAH": "\u72b9\u4ed6",
    "UTEP": "\u5fb7\u5dde\u57c3\u5c14\u5e15\u7d22",
    "UTSA": "\u5fb7\u5dde\u5723\u5b89\u4e1c\u5c3c\u5965",
    "UVA": "\u5f17\u5409\u5c3c\u4e9a",
    "VAN": "\u8303\u5fb7\u5821",
    "VAND": "\u8303\u5fb7\u5821",
    "VT": "\u5f17\u5409\u5c3c\u4e9a\u7406\u5de5",
    "WAKE": "\u7ef4\u514b\u68ee\u6797",
    "WASH": "\u534e\u76db\u987f",
    "WIS": "\u5a01\u65af\u5eb7\u661f",
    "WKU": "\u897f\u80af\u5854\u57fa",
    "WMU": "\u897f\u5bc6\u6b47\u6839",
    "WSU": "\u534e\u76db\u987f\u5dde\u7acb",
    "WVU": "\u897f\u5f17\u5409\u5c3c\u4e9a",
    "WYO": "\u6000\u4fc4\u660e",
}

NCAA_TEAM_ZH_FULL_NAMES = {
    "AF": "\u7a7a\u519b\u730e\u9e70",
    "AKR": "\u963f\u514b\u4f26\u9f50\u666e\u65af",
    "ALA": "\u963f\u62c9\u5df4\u9a6c\u7ea2\u6f6e",
    "APP": "\u963f\u5df4\u62c9\u5951\u4e9a\u5dde\u7acb\u767b\u5c71\u8005",
    "ARK": "\u963f\u80af\u8272\u91ce\u732a",
    "ARIZ": "\u4e9a\u5229\u6851\u90a3\u91ce\u732b",
    "ARMY": "\u9646\u519b\u9ed1\u9a91\u58eb",
    "ASU": "\u4e9a\u5229\u6851\u90a3\u5dde\u7acb\u592a\u9633\u9b54\u9b3c",
    "AUB": "\u5965\u672c\u8001\u864e",
    "BALL": "\u6ce2\u5c14\u5dde\u7acb\u7ea2\u96c0",
    "BAY": "\u8d1d\u52d2\u718a",
    "BC": "\u6ce2\u58eb\u987f\u5b66\u9662\u8001\u9e70",
    "BGSU": "\u9c8d\u7075\u683c\u6797\u730e\u9e70",
    "BSU": "\u535a\u4f0a\u897f\u5dde\u7acb\u91ce\u9a6c",
    "BUF": "\u5e03\u6cd5\u7f57\u516c\u725b",
    "BUFF": "\u5e03\u6cd5\u7f57\u516c\u725b",
    "BYU": "\u6768\u767e\u7ff0\u7f8e\u6d32\u72ee",
    "CAL": "\u52a0\u5dde\u91d1\u718a",
    "CCU": "\u5361\u7f57\u6765\u7eb3\u6d77\u5cb8\u96c4\u9e21",
    "CHAR": "\u590f\u6d1b\u727949\u4eba",
    "CIN": "\u8f9b\u8f9b\u90a3\u63d0\u718a\u72f8",
    "CLT": "\u590f\u6d1b\u727949\u4eba",
    "CLEM": "\u514b\u83b1\u59c6\u68ee\u8001\u864e",
    "CMU": "\u4e2d\u5bc6\u6b47\u6839\u5947\u73c0\u74e6\u4eba",
    "CONN": "\u5eb7\u6d85\u72c4\u683c\u54c8\u58eb\u5947",
    "COLO": "\u79d1\u7f57\u62c9\u591a\u6c34\u725b",
    "CSU": "\u79d1\u7f57\u62c9\u591a\u5dde\u7acb\u516c\u7f8a",
    "DEL": "\u7279\u62c9\u534e\u84dd\u6bcd\u9e21",
    "DUKE": "\u675c\u514b\u84dd\u9b54",
    "ECU": "\u4e1c\u5361\u7f57\u6765\u7eb3\u6d77\u76d7",
    "EMU": "\u4e1c\u5bc6\u6b47\u6839\u8001\u9e70",
    "FAU": "\u4f5b\u7f57\u91cc\u8fbe\u5927\u897f\u6d0b\u732b\u5934\u9e70",
    "FIU": "\u4f5b\u7f57\u91cc\u8fbe\u56fd\u9645\u9ed1\u8c79",
    "FRES": "\u5f17\u96f7\u65af\u8bfa\u5dde\u7acb\u6597\u725b\u72ac",
    "FSU": "\u4f5b\u5dde\u585e\u7c73\u8bfa\u5c14\u4eba",
    "GASO": "\u4f50\u6cbb\u4e9a\u5357\u65b9\u8001\u9e70",
    "GAST": "\u4f50\u6cbb\u4e9a\u5dde\u7acb\u9ed1\u8c79",
    "GT": "\u4f50\u6cbb\u4e9a\u7406\u5de5\u9ec4\u5939\u514b",
    "HAW": "\u590f\u5a01\u5937\u5f69\u8679\u52c7\u58eb",
    "HOU": "\u4f11\u65af\u987f\u7f8e\u6d32\u72ee",
    "ILL": "\u4f0a\u5229\u8bfa\u4f0a\u6218\u6597\u4f0a\u5229\u5c3c",
    "IND": "\u5370\u7b2c\u5b89\u7eb3\u80e1\u5e0c\u5c14\u4eba",
    "IU": "\u5370\u7b2c\u5b89\u7eb3\u80e1\u5e0c\u5c14\u4eba",
    "IOWA": "\u7231\u8377\u534e\u9e70\u773c",
    "ISU": "\u7231\u8377\u534e\u5dde\u7acb\u65cb\u98ce",
    "JMU": "\u8a79\u59c6\u65af\u9ea6\u8fea\u900a\u516c\u7235",
    "JVST": "\u6770\u514b\u900a\u7ef4\u5c14\u5dde\u7acb\u6597\u9e21",
    "JXST": "\u6770\u514b\u900a\u7ef4\u5c14\u5dde\u7acb\u6597\u9e21",
    "KENN": "\u80af\u5c3c\u7d22\u5dde\u7acb\u732b\u5934\u9e70",
    "KENT": "\u80af\u7279\u5dde\u7acb\u91d1\u8272\u95ea\u7535",
    "KSU": "\u582a\u8428\u65af\u5dde\u7acb\u91ce\u732b",
    "KU": "\u582a\u8428\u65af\u677e\u9e26\u9e70",
    "UK": "\u80af\u5854\u57fa\u91ce\u732b",
    "LIB": "\u81ea\u7531\u706b\u7130",
    "LOU": "\u8def\u6613\u7ef4\u5c14\u7ea2\u96c0",
    "LSU": "\u8def\u6613\u65af\u5b89\u90a3\u5dde\u7acb\u8001\u864e",
    "LT": "\u8def\u6613\u65af\u5b89\u90a3\u7406\u5de5\u6597\u725b\u72ac",
    "M-OH": "\u8fc8\u963f\u5bc6\u4fc4\u4ea5\u4fc4\u7ea2\u9e70",
    "MARY": "\u9a6c\u91cc\u5170\u6de1\u6c34\u9f9f",
    "MEM": "\u5b5f\u83f2\u65af\u8001\u864e",
    "MIA": "\u8fc8\u963f\u5bc6\u98d3\u98ce",
    "MICH": "\u5bc6\u6b47\u6839\u72fc\u737e",
    "MISS": "\u5bc6\u897f\u897f\u6bd4\u53db\u519b",
    "MINN": "\u660e\u5c3c\u82cf\u8fbe\u91d1\u5730\u9f20",
    "MIZ": "\u5bc6\u82cf\u91cc\u8001\u864e",
    "MIZZ": "\u5bc6\u82cf\u91cc\u8001\u864e",
    "MRSH": "\u9a6c\u6b47\u5c14\u96f7\u9706\u7267\u7fa4",
    "MSST": "\u5bc6\u897f\u897f\u6bd4\u5dde\u7acb\u6597\u725b\u72ac",
    "MSU": "\u5bc6\u6b47\u6839\u5dde\u7acb\u65af\u5df4\u8fbe\u4eba",
    "NAVY": "\u6d77\u519b\u519b\u5b98\u751f",
    "NCST": "\u5317\u5361\u5dde\u7acb\u72fc\u7fa4",
    "NCSU": "\u5317\u5361\u5dde\u7acb\u72fc\u7fa4",
    "ND": "\u5723\u6bcd\u6218\u6597\u7231\u5c14\u5170\u4eba",
    "NEB": "\u5185\u5e03\u62c9\u65af\u52a0\u7389\u7c73\u5265\u76ae\u4eba",
    "NEV": "\u5185\u534e\u8fbe\u72fc\u7fa4",
    "NIU": "\u5317\u4f0a\u5229\u8bfa\u4f0a\u54c8\u58eb\u5947",
    "NMSU": "\u65b0\u58a8\u897f\u54e5\u5dde\u7acb\u519c\u5de5",
    "NU": "\u897f\u5317\u91ce\u732b",
    "NW": "\u897f\u5317\u91ce\u732b",
    "ODU": "\u8001\u9053\u660e\u541b\u4e3b",
    "OHIO": "\u4fc4\u4ea5\u4fc4\u5c71\u732b",
    "OKST": "\u4fc4\u514b\u62c9\u8377\u9a6c\u5dde\u7acb\u725b\u4ed4",
    "ORE": "\u4fc4\u52d2\u5188\u9e2d",
    "ORST": "\u4fc4\u52d2\u5188\u5dde\u7acb\u6d77\u72f8",
    "OSU": "\u4fc4\u4ea5\u4fc4\u5dde\u7acb\u4e03\u53f6\u6811",
    "OU": "\u4fc4\u514b\u62c9\u8377\u9a6c\u6377\u8db3\u8005",
    "PITT": "\u5339\u5179\u5821\u9ed1\u8c79",
    "PSU": "\u5bbe\u5dde\u5dde\u7acb\u5c3c\u5854\u5c3c\u72ee",
    "PUR": "\u666e\u6e21\u9505\u7089\u5de5",
    "RICE": "\u83b1\u65af\u732b\u5934\u9e70",
    "RUTG": "\u7f57\u683c\u65af\u7ea2\u8863\u9a91\u58eb",
    "SC": "\u5357\u5361\u6597\u9e21",
    "SDSU": "\u5723\u8fed\u6208\u5dde\u7acb\u963f\u5179\u7279\u514b",
    "SHSU": "\u8428\u59c6\u4f11\u65af\u987f\u718a\u72f8",
    "SJSU": "\u5723\u4f55\u585e\u5dde\u7acb\u65af\u5df4\u8fbe\u4eba",
    "SMU": "\u5357\u65b9\u536b\u7406\u516c\u4f1a\u91ce\u9a6c",
    "STAN": "\u65af\u5766\u798f\u7ea2\u8863\u4e3b\u6559",
    "SYR": "\u96ea\u57ce\u6a59",
    "TCU": "TCU\u89d2\u86d9",
    "TEM": "\u5929\u666e\u732b\u5934\u9e70",
    "TEMP": "\u5929\u666e\u732b\u5934\u9e70",
    "TENN": "\u7530\u7eb3\u897f\u5fd7\u613f\u8005",
    "TEX": "\u5fb7\u514b\u8428\u65af\u957f\u89d2\u725b",
    "TLSA": "\u5854\u5c14\u8428\u91d1\u8272\u98d3\u98ce",
    "TOL": "\u6258\u83b1\u591a\u706b\u7bad",
    "TROY": "\u7279\u6d1b\u4f0a\u7279\u6d1b\u4f0a\u4eba",
    "TTU": "\u5fb7\u5dde\u7406\u5de5\u7ea2\u8272\u7a81\u88ad\u8005",
    "TULN": "\u675c\u5170\u7eff\u6d6a",
    "TULSA": "\u5854\u5c14\u8428\u91d1\u8272\u98d3\u98ce",
    "UL": "\u8def\u6613\u65af\u5b89\u90a3\u72c2\u6012\u5361\u6d25\u4eba",
    "ULL": "\u8def\u6613\u65af\u5b89\u90a3\u72c2\u6012\u5361\u6d25\u4eba",
    "UCF": "\u4e2d\u4f5b\u7f57\u91cc\u8fbe\u9a91\u58eb",
    "UCONN": "\u5eb7\u6d85\u72c4\u683c\u54c8\u58eb\u5947",
    "UCLA": "UCLA\u68d5\u718a",
    "UF": "\u4f5b\u7f57\u91cc\u8fbe\u77ed\u543b\u9cc4",
    "UGA": "\u4f50\u6cbb\u4e9a\u6597\u725b\u72ac",
    "UNC": "\u5317\u5361\u7126\u6cb9\u8e35",
    "UNLV": "\u5185\u534e\u8fbe\u62c9\u65af\u7ef4\u52a0\u65af\u53db\u9006\u8005",
    "USC": "\u5357\u52a0\u5dde\u7279\u6d1b\u4f0a\u4eba",
    "USF": "\u5357\u4f5b\u7f57\u91cc\u8fbe\u516c\u725b",
    "USM": "\u5357\u5bc6\u897f\u897f\u6bd4\u91d1\u9e70",
    "USU": "\u72b9\u4ed6\u5dde\u7acb\u519c\u5de5",
    "UTAH": "\u72b9\u4ed6\u72b9\u7279\u4eba",
    "UTEP": "\u5fb7\u5dde\u57c3\u5c14\u5e15\u7d22\u77ff\u5de5",
    "UTSA": "\u5fb7\u5dde\u5723\u5b89\u4e1c\u5c3c\u5965\u8d70\u9e43",
    "UVA": "\u5f17\u5409\u5c3c\u4e9a\u9a91\u58eb",
    "VAN": "\u8303\u5fb7\u5821\u51c6\u5c06",
    "VAND": "\u8303\u5fb7\u5821\u51c6\u5c06",
    "VT": "\u5f17\u5409\u5c3c\u4e9a\u7406\u5de5\u970d\u57fa",
    "WAKE": "\u7ef4\u514b\u68ee\u6797\u9b54\u9b3c\u6267\u4e8b",
    "WASH": "\u534e\u76db\u987f\u54c8\u58eb\u5947",
    "WIS": "\u5a01\u65af\u5eb7\u661f\u737e",
    "WKU": "\u897f\u80af\u5854\u57fa\u5c71\u9876\u4eba",
    "WMU": "\u897f\u5bc6\u6b47\u6839\u91ce\u9a6c",
    "WSU": "\u534e\u76db\u987f\u5dde\u7acb\u7f8e\u6d32\u72ee",
    "WVU": "\u897f\u5f17\u5409\u5c3c\u4e9a\u767b\u5c71\u8005",
    "WYO": "\u6000\u4fc4\u660e\u725b\u4ed4",
}

NCAA_TEAM_NAME_ALIASES = {
    "AF": ("Air Force", "Air Force Falcons"),
    "AKR": ("Akron", "Akron Zips"),
    "ALA": ("Alabama", "Alabama Crimson Tide"),
    "APP": ("App State", "Appalachian State", "Appalachian State Mountaineers"),
    "ARK": ("Arkansas", "Arkansas Razorbacks"),
    "ARIZ": ("Arizona", "Arizona Wildcats"),
    "ARMY": ("Army", "Army Black Knights", "Army West Point"),
    "ASU": ("Arizona State", "Arizona State Sun Devils"),
    "AUB": ("Auburn", "Auburn Tigers"),
    "BALL": ("Ball State", "Ball State Cardinals"),
    "BAY": ("Baylor", "Baylor Bears"),
    "BC": ("Boston College", "Boston College Eagles"),
    "BGSU": ("Bowling Green", "Bowling Green Falcons"),
    "BSU": ("Boise State", "Boise State Broncos"),
    "BUFF": ("Buffalo", "Buffalo Bulls"),
    "BYU": ("BYU", "Brigham Young", "BYU Cougars"),
    "CAL": ("California", "California Golden Bears", "Cal"),
    "CCU": ("Coastal Carolina", "Coastal Carolina Chanticleers"),
    "CHAR": ("Charlotte", "Charlotte 49ers"),
    "CIN": ("Cincinnati", "Cincinnati Bearcats"),
    "CLEM": ("Clemson", "Clemson Tigers"),
    "CMU": ("Central Michigan", "Central Michigan Chippewas"),
    "CONN": ("Connecticut", "Connecticut Huskies"),
    "COLO": ("Colorado", "Colorado Buffaloes"),
    "CSU": ("Colorado State", "Colorado State Rams"),
    "DEL": ("Delaware", "Delaware Blue Hens"),
    "DUKE": ("Duke", "Duke Blue Devils"),
    "ECU": ("East Carolina", "East Carolina Pirates"),
    "EMU": ("Eastern Michigan", "Eastern Michigan Eagles"),
    "FAU": ("Florida Atlantic", "Florida Atlantic Owls", "FAU Owls"),
    "FIU": ("Florida International", "FIU", "FIU Panthers"),
    "FRES": ("Fresno State", "Fresno State Bulldogs"),
    "FSU": ("Florida State", "Florida State Seminoles"),
    "GASO": ("Georgia Southern", "Georgia Southern Eagles"),
    "GAST": ("Georgia State", "Georgia State Panthers"),
    "GT": ("Georgia Tech", "Georgia Tech Yellow Jackets"),
    "UGA": ("Georgia", "Georgia Bulldogs"),
    "HAW": ("Hawaii", "Hawai'i", "Hawaii Rainbow Warriors", "Hawai'i Rainbow Warriors"),
    "HOU": ("Houston", "Houston Cougars"),
    "ILL": ("Illinois", "Illinois Fighting Illini"),
    "IND": ("Indiana", "Indiana Hoosiers"),
    "IOWA": ("Iowa", "Iowa Hawkeyes"),
    "ISU": ("Iowa State", "Iowa State Cyclones"),
    "JMU": ("James Madison", "James Madison Dukes"),
    "JVST": ("Jacksonville State", "Jacksonville State Gamecocks"),
    "KENN": ("Kennesaw State", "Kennesaw State Owls"),
    "KENT": ("Kent State", "Kent State Golden Flashes"),
    "KSU": ("Kansas State", "Kansas State Wildcats"),
    "KU": ("Kansas", "Kansas Jayhawks"),
    "UK": ("Kentucky", "Kentucky Wildcats"),
    "LIB": ("Liberty", "Liberty Flames"),
    "LOU": ("Louisville", "Louisville Cardinals"),
    "LSU": ("LSU", "Louisiana State", "LSU Tigers"),
    "LT": ("Louisiana Tech", "Louisiana Tech Bulldogs"),
    "M-OH": ("Miami (OH)", "Miami Ohio", "Miami (OH) RedHawks", "Miami RedHawks"),
    "MARY": ("Maryland", "Maryland Terrapins"),
    "MEM": ("Memphis", "Memphis Tigers"),
    "MIA": ("Miami", "Miami Hurricanes"),
    "MICH": ("Michigan", "Michigan Wolverines"),
    "MINN": ("Minnesota", "Minnesota Golden Gophers"),
    "MISS": ("Ole Miss", "Mississippi Rebels"),
    "MIZ": ("Missouri", "Missouri Tigers"),
    "MIZZ": ("Missouri", "Missouri Tigers"),
    "MRSH": ("Marshall", "Marshall Thundering Herd"),
    "MSST": ("Mississippi State", "Mississippi State Bulldogs"),
    "MSU": ("Michigan State", "Michigan State Spartans"),
    "NAVY": ("Navy", "Navy Midshipmen"),
    "NCST": ("NC State", "North Carolina State", "NC State Wolfpack"),
    "ND": ("Notre Dame", "Notre Dame Fighting Irish"),
    "NEB": ("Nebraska", "Nebraska Cornhuskers"),
    "NEV": ("Nevada", "Nevada Wolf Pack"),
    "NIU": ("Northern Illinois", "Northern Illinois Huskies"),
    "NMSU": ("New Mexico State", "New Mexico State Aggies"),
    "NW": ("Northwestern", "Northwestern Wildcats"),
    "ODU": ("Old Dominion", "Old Dominion Monarchs"),
    "OHIO": ("Ohio", "Ohio Bobcats"),
    "OKST": ("Oklahoma State", "Oklahoma State Cowboys"),
    "ORE": ("Oregon", "Oregon Ducks"),
    "ORST": ("Oregon State", "Oregon State Beavers"),
    "OSU": ("Ohio State", "Ohio State Buckeyes"),
    "OU": ("Oklahoma", "Oklahoma Sooners"),
    "PSU": ("Penn State", "Penn State Nittany Lions"),
    "PITT": ("Pittsburgh", "Pittsburgh Panthers"),
    "PUR": ("Purdue", "Purdue Boilermakers"),
    "RICE": ("Rice", "Rice Owls"),
    "RUTG": ("Rutgers", "Rutgers Scarlet Knights"),
    "SC": ("South Carolina", "South Carolina Gamecocks"),
    "SDSU": ("San Diego State", "San Diego State Aztecs"),
    "SHSU": ("Sam Houston", "Sam Houston State", "Sam Houston Bearkats"),
    "SJSU": ("San Jose State", "San José State", "San Jose State Spartans", "San José State Spartans"),
    "SMU": ("SMU", "Southern Methodist", "SMU Mustangs"),
    "STAN": ("Stanford", "Stanford Cardinal"),
    "SYR": ("Syracuse", "Syracuse Orange"),
    "TCU": ("TCU", "Texas Christian", "TCU Horned Frogs"),
    "TEMP": ("Temple", "Temple Owls"),
    "TENN": ("Tennessee", "Tennessee Volunteers"),
    "TEX": ("Texas", "Texas Longhorns"),
    "TOL": ("Toledo", "Toledo Rockets"),
    "TROY": ("Troy", "Troy Trojans"),
    "TTU": ("Texas Tech", "Texas Tech Red Raiders"),
    "TULN": ("Tulane", "Tulane Green Wave"),
    "TULSA": ("Tulsa", "Tulsa Golden Hurricane"),
    "UL": ("Louisiana", "Louisiana Ragin' Cajuns", "Louisiana Ragin Cajuns"),
    "ULL": ("Louisiana", "Louisiana Ragin' Cajuns", "Louisiana Ragin Cajuns"),
    "UNLV": ("UNLV", "UNLV Rebels", "Nevada Las Vegas", "Nevada-Las Vegas"),
    "UCF": ("UCF", "Central Florida", "UCF Knights"),
    "UCONN": ("UConn", "Connecticut", "UConn Huskies"),
    "UCLA": ("UCLA", "UCLA Bruins"),
    "UF": ("Florida", "Florida Gators"),
    "UNC": ("North Carolina", "North Carolina Tar Heels"),
    "USC": ("USC", "Southern California", "USC Trojans"),
    "USF": ("USF", "South Florida", "USF Bulls"),
    "USM": ("Southern Miss", "Southern Mississippi", "Southern Miss Golden Eagles"),
    "USU": ("Utah State", "Utah State Aggies"),
    "UTAH": ("Utah", "Utah Utes"),
    "UTEP": ("UTEP", "UTEP Miners", "Texas El Paso", "Texas-El Paso"),
    "UTSA": ("UTSA", "UTSA Roadrunners", "Texas San Antonio", "Texas-San Antonio"),
    "UVA": ("Virginia", "Virginia Cavaliers"),
    "VAND": ("Vanderbilt", "Vanderbilt Commodores"),
    "VT": ("Virginia Tech", "Virginia Tech Hokies"),
    "WAKE": ("Wake Forest", "Wake Forest Demon Deacons"),
    "WASH": ("Washington", "Washington Huskies"),
    "WIS": ("Wisconsin", "Wisconsin Badgers"),
    "WKU": ("Western Kentucky", "Western Kentucky Hilltoppers"),
    "WMU": ("Western Michigan", "Western Michigan Broncos"),
    "WSU": ("Washington State", "Washington State Cougars"),
    "WVU": ("West Virginia", "West Virginia Mountaineers"),
    "WYO": ("Wyoming", "Wyoming Cowboys"),
}

NBA_ODDS_TEAM_ALIASES = {
    "ATL": ("Atlanta Hawks", "Hawks"),
    "BKN": ("Brooklyn Nets", "Nets"),
    "BOS": ("Boston Celtics", "Celtics"),
    "CHA": ("Charlotte Hornets", "Hornets"),
    "CHI": ("Chicago Bulls", "Bulls"),
    "CLE": ("Cleveland Cavaliers", "Cavaliers", "Cavs"),
    "DAL": ("Dallas Mavericks", "Mavericks", "Mavs"),
    "DEN": ("Denver Nuggets", "Nuggets"),
    "DET": ("Detroit Pistons", "Pistons"),
    "GS": ("Golden State Warriors", "Warriors", "GSW"),
    "GSW": ("Golden State Warriors", "Warriors", "GS"),
    "HOU": ("Houston Rockets", "Rockets"),
    "IND": ("Indiana Pacers", "Pacers"),
    "LAC": ("LA Clippers", "Los Angeles Clippers", "Clippers"),
    "LAL": ("Los Angeles Lakers", "Lakers"),
    "MEM": ("Memphis Grizzlies", "Grizzlies"),
    "MIA": ("Miami Heat", "Heat"),
    "MIL": ("Milwaukee Bucks", "Bucks"),
    "MIN": ("Minnesota Timberwolves", "Timberwolves", "Wolves"),
    "NO": ("New Orleans Pelicans", "Pelicans", "NOP"),
    "NOP": ("New Orleans Pelicans", "Pelicans", "NO"),
    "NY": ("New York Knicks", "Knicks", "NYK"),
    "NYK": ("New York Knicks", "Knicks", "NY"),
    "OKC": ("Oklahoma City Thunder", "Thunder"),
    "ORL": ("Orlando Magic", "Magic"),
    "PHI": ("Philadelphia 76ers", "76ers", "Sixers"),
    "PHX": ("Phoenix Suns", "Suns"),
    "POR": ("Portland Trail Blazers", "Trail Blazers", "Blazers"),
    "SA": ("San Antonio Spurs", "Spurs", "SAS"),
    "SAS": ("San Antonio Spurs", "Spurs", "SA"),
    "SAC": ("Sacramento Kings", "Kings"),
    "TOR": ("Toronto Raptors", "Raptors"),
    "UTA": ("Utah Jazz", "Jazz"),
    "WSH": ("Washington Wizards", "Wizards"),
}

# Color tokens follow docs/color-ui-guidelines.md: warm paper, process black
# linework, and limited vintage comic process-color accents.
DAY_COLORS = {
    "paper": (255, 248, 220),  # 25Y PANTONE 100, vintage comic paper ground
    "panel": (255, 253, 240),
    "panel2": (255, 253, 240),
    "panel_blue": (235, 246, 255),  # 25B PANTONE 304 family, paper-tinted
    "panel_gold": (255, 239, 176),  # 50Y PANTONE 101 family, paper-tinted
    "border": (8, 8, 8),  # PROCESS BLACK
    "line": (190, 177, 134),
    "text": (8, 8, 8),
    "paper_text": (255, 248, 220),
    "muted": (126, 112, 82),  # 50Y-25R-25B PANTONE 465 family
    "blue": (0, 92, 185),  # 100B-25R PANTONE 285 family
    "cyan": (0, 163, 173),  # 50Y-100B PANTONE 327 family
    "amber": (255, 196, 30),  # 100Y-25R PANTONE 123 family
    "orange": (245, 122, 38),  # 100Y-50R PANTONE ORANGE 021 family
    "green": (0, 152, 82),  # 100Y-100B PANTONE 354 family
    "red": (222, 45, 38),  # 100Y-100R PANTONE RED 032 family
    "valve_cs_accent": (0, 92, 185),
    "valve_cs_tag": (222, 238, 255),
    "valve_ti_accent": (222, 45, 38),
    "valve_ti_tag": (255, 226, 220),
    "valve_shadow": (222, 45, 38),
    "ewc_accent": (0, 92, 185),
    "ewc_live": (222, 45, 38),
    "ewc_tag": (222, 238, 255),
    "ewc_shadow": (0, 163, 173),
    "worldcup_accent": (0, 152, 82),
    "worldcup_live": (222, 45, 38),
    "worldcup_tag": (218, 244, 215),
    "worldcup_shadow": (0, 163, 173),
    "nba_accent": (0, 92, 185),
    "nba_live": (222, 45, 38),
    "nba_tag": (222, 238, 255),
    "nba_shadow": (222, 45, 38),
    "f1_accent": (222, 45, 38),
    "f1_live": (222, 45, 38),
    "f1_tag": (255, 226, 220),
    "f1_shadow": (8, 8, 8),
    "f1_track": (190, 177, 134),
    "mlb_accent": (0, 92, 185),
    "mlb_live": (222, 45, 38),
    "mlb_tag": (222, 238, 255),
    "mlb_field": (0, 152, 82),
    "mlb_field_tint": (224, 244, 226),
    "wnba_accent": (245, 122, 38),
    "wnba_live": (222, 45, 38),
    "wnba_tag": (255, 239, 176),
    "wnba_court": (255, 231, 190),
    "pga_accent": (0, 152, 82),
    "pga_leader": (0, 112, 70),
    "pga_live": (222, 45, 38),
    "pga_tag": (218, 244, 215),
    "pga_course_tint": (229, 247, 222),
    "nfl_accent": (255, 196, 30),
    "nfl_live": (222, 45, 38),
    "nfl_tag": (255, 239, 176),
    "nfl_field_tint": (255, 246, 202),
    "ncaa_accent": (0, 163, 173),
    "ncaa_live": (222, 45, 38),
    "ncaa_tag": (218, 244, 244),
    "ncaa_field_tint": (226, 248, 248),
    "lpl_accent": (222, 45, 38),
    "lpl_live": (222, 45, 38),
    "lpl_tag": (255, 226, 220),
    "lpl_shadow": (255, 196, 30),
    "lck_accent": (0, 163, 173),
    "lck_live": (222, 45, 38),
    "lck_tag": (218, 244, 244),
    "lck_shadow": (0, 92, 185),
    "msi_accent": (0, 84, 166),
    "msi_live": (222, 45, 38),
    "msi_tag": (221, 238, 255),
    "msi_shadow": (0, 136, 156),
}

DEEP_NIGHT_COLORS = {
    "paper": (5, 7, 12),  # deep-night ground, close to process black
    "panel": (18, 22, 35),
    "panel2": (24, 19, 35),
    "panel_blue": (12, 32, 54),
    "panel_gold": (68, 54, 12),
    "border": (236, 232, 206),
    "line": (92, 90, 74),
    "text": (255, 250, 222),
    "paper_text": (5, 7, 12),
    "muted": (202, 190, 150),
    "blue": (93, 169, 232),
    "cyan": (107, 204, 255),
    "amber": (255, 205, 54),
    "orange": (255, 136, 47),
    "green": (82, 202, 128),
    "red": (255, 82, 74),
    "valve_cs_accent": (93, 169, 232),
    "valve_cs_tag": (21, 47, 82),
    "valve_ti_accent": (255, 82, 74),
    "valve_ti_tag": (82, 34, 29),
    "valve_shadow": (126, 54, 76),
    "ewc_accent": (93, 169, 232),
    "ewc_live": (255, 82, 74),
    "ewc_tag": (21, 47, 82),
    "ewc_shadow": (36, 124, 102),
    "worldcup_accent": (82, 202, 128),
    "worldcup_live": (255, 82, 74),
    "worldcup_tag": (28, 70, 48),
    "worldcup_shadow": (36, 124, 102),
    "nba_accent": (93, 169, 232),
    "nba_live": (255, 82, 74),
    "nba_tag": (21, 47, 82),
    "nba_shadow": (126, 54, 76),
    "f1_accent": (255, 82, 74),
    "f1_live": (255, 82, 74),
    "f1_tag": (82, 34, 29),
    "f1_shadow": (72, 16, 16),
    "f1_track": (92, 90, 74),
    "mlb_accent": (93, 169, 232),
    "mlb_live": (255, 82, 74),
    "mlb_tag": (21, 47, 82),
    "mlb_field": (82, 202, 128),
    "mlb_field_tint": (18, 58, 38),
    "wnba_accent": (255, 136, 47),
    "wnba_live": (255, 82, 74),
    "wnba_tag": (68, 54, 12),
    "wnba_court": (76, 43, 25),
    "pga_accent": (82, 202, 128),
    "pga_leader": (132, 235, 164),
    "pga_live": (255, 82, 74),
    "pga_tag": (28, 70, 48),
    "pga_course_tint": (22, 64, 42),
    "nfl_accent": (255, 205, 54),
    "nfl_live": (255, 82, 74),
    "nfl_tag": (68, 54, 12),
    "nfl_field_tint": (72, 58, 16),
    "ncaa_accent": (107, 204, 255),
    "ncaa_live": (255, 82, 74),
    "ncaa_tag": (18, 64, 74),
    "ncaa_field_tint": (15, 54, 64),
    "lpl_accent": (255, 82, 74),
    "lpl_live": (255, 82, 74),
    "lpl_tag": (82, 34, 29),
    "lpl_shadow": (130, 76, 26),
    "lck_accent": (107, 204, 255),
    "lck_live": (255, 82, 74),
    "lck_tag": (18, 64, 74),
    "lck_shadow": (34, 84, 130),
    "msi_accent": (112, 210, 255),
    "msi_live": (255, 82, 74),
    "msi_tag": (20, 52, 86),
    "msi_shadow": (25, 104, 128),
}

_ACTIVE_COLORS = ContextVar("sports_dashboard_active_colors", default=DAY_COLORS)


class _ActiveColorProxy(Mapping):
    def __getitem__(self, key):
        return _ACTIVE_COLORS.get()[key]

    def __iter__(self):
        return iter(_ACTIVE_COLORS.get())

    def __len__(self):
        return len(_ACTIVE_COLORS.get())

    def copy(self):
        return dict(_ACTIVE_COLORS.get())


COLORS = _ActiveColorProxy()

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

FIFA_TLA_TO_ZH_NAME = {
    "ALB": "阿尔巴尼亚",
    "ALG": "阿尔及利亚",
    "ARG": "阿根廷",
    "AUS": "澳大利亚",
    "AUT": "奥地利",
    "BEL": "比利时",
    "BIH": "波黑",
    "BOL": "玻利维亚",
    "BRA": "巴西",
    "CAN": "加拿大",
    "CHI": "智利",
    "CHL": "智利",
    "CIV": "科特迪瓦",
    "CMR": "喀麦隆",
    "COD": "刚果（金）",
    "COL": "哥伦比亚",
    "CRC": "哥斯达黎加",
    "CRO": "克罗地亚",
    "CPV": "佛得角",
    "CZE": "捷克",
    "CUW": "库拉索",
    "DEN": "丹麦",
    "DEU": "德国",
    "DNK": "丹麦",
    "ECU": "厄瓜多尔",
    "EGY": "埃及",
    "ENG": "英格兰",
    "ESP": "西班牙",
    "FRA": "法国",
    "GAB": "加蓬",
    "GER": "德国",
    "GHA": "加纳",
    "GRE": "希腊",
    "HAI": "海地",
    "HON": "洪都拉斯",
    "HRV": "克罗地亚",
    "HUN": "匈牙利",
    "IRL": "爱尔兰",
    "IRN": "伊朗",
    "IRQ": "伊拉克",
    "ITA": "意大利",
    "JAM": "牙买加",
    "JOR": "约旦",
    "JPN": "日本",
    "KOR": "韩国",
    "KSA": "沙特阿拉伯",
    "MAR": "摩洛哥",
    "MEX": "墨西哥",
    "NED": "荷兰",
    "NGA": "尼日利亚",
    "NLD": "荷兰",
    "NOR": "挪威",
    "NZL": "新西兰",
    "PAN": "巴拿马",
    "PAR": "巴拉圭",
    "PER": "秘鲁",
    "POL": "波兰",
    "POR": "葡萄牙",
    "PRT": "葡萄牙",
    "QAT": "卡塔尔",
    "ROU": "罗马尼亚",
    "RSA": "南非",
    "SAU": "沙特阿拉伯",
    "SCO": "苏格兰",
    "SEN": "塞内加尔",
    "SRB": "塞尔维亚",
    "CHE": "瑞士",
    "SUI": "瑞士",
    "SVK": "斯洛伐克",
    "SVN": "斯洛文尼亚",
    "SWE": "瑞典",
    "TUN": "突尼斯",
    "TUR": "土耳其",
    "UAE": "阿联酋",
    "ARE": "阿联酋",
    "UKR": "乌克兰",
    "URU": "乌拉圭",
    "URY": "乌拉圭",
    "USA": "美国",
    "UZB": "乌兹别克斯坦",
    "VEN": "委内瑞拉",
    "WAL": "威尔士",
    "ZAM": "赞比亚",
}

FIFA_TLA_TO_FLAGS_API_CODE = {
    "ALB": "AL",
    "ALG": "DZ",
    "ARG": "AR",
    "AUS": "AU",
    "AUT": "AT",
    "BEL": "BE",
    "BIH": "BA",
    "BOL": "BO",
    "BRA": "BR",
    "CAN": "CA",
    "CHI": "CL",
    "CHL": "CL",
    "CIV": "CI",
    "CMR": "CM",
    "COD": "CD",
    "COL": "CO",
    "CRC": "CR",
    "CRO": "HR",
    "CPV": "CV",
    "CZE": "CZ",
    "CUW": "CW",
    "DEN": "DK",
    "DEU": "DE",
    "DNK": "DK",
    "ECU": "EC",
    "EGY": "EG",
    "ENG": "GB",
    "ESP": "ES",
    "FRA": "FR",
    "GAB": "GA",
    "GER": "DE",
    "GHA": "GH",
    "GRE": "GR",
    "HAI": "HT",
    "HON": "HN",
    "HRV": "HR",
    "HUN": "HU",
    "IRL": "IE",
    "IRN": "IR",
    "IRQ": "IQ",
    "ITA": "IT",
    "JAM": "JM",
    "JOR": "JO",
    "JPN": "JP",
    "KOR": "KR",
    "KSA": "SA",
    "MAR": "MA",
    "MEX": "MX",
    "NED": "NL",
    "NGA": "NG",
    "NLD": "NL",
    "NOR": "NO",
    "NZL": "NZ",
    "PAN": "PA",
    "PAR": "PY",
    "PER": "PE",
    "POL": "PL",
    "POR": "PT",
    "PRT": "PT",
    "QAT": "QA",
    "ROU": "RO",
    "RSA": "ZA",
    "SAU": "SA",
    "SEN": "SN",
    "SRB": "RS",
    "CHE": "CH",
    "SUI": "CH",
    "SVK": "SK",
    "SVN": "SI",
    "SWE": "SE",
    "TUN": "TN",
    "TUR": "TR",
    "UAE": "AE",
    "ARE": "AE",
    "UKR": "UA",
    "URU": "UY",
    "URY": "UY",
    "USA": "US",
    "UZB": "UZ",
    "VEN": "VE",
    "WAL": "GB",
    "ZAM": "ZM",
}

LOCAL_WORLDCUP_FLAG_URL_PREFIX = "local:worldcup:"
LOCAL_WORLDCUP_FLAG_TLAS = {"SCO"}

DEFAULT_FLAG_ASPECT_RATIO = 3 / 2
ISO_FLAG_ASPECT_RATIOS = {
    "AL": 7 / 5,
    "AR": 8 / 5,
    "AU": 2 / 1,
    "BE": 15 / 13,
    "BA": 2 / 1,
    "BO": 22 / 15,
    "BR": 10 / 7,
    "CA": 2 / 1,
    "CD": 4 / 3,
    "CR": 5 / 3,
    "HR": 2 / 1,
    "CV": 17 / 10,
    "DK": 37 / 28,
    "DE": 5 / 3,
    "GA": 4 / 3,
    "GB": 2 / 1,
    "HT": 5 / 3,
    "HN": 2 / 1,
    "HU": 2 / 1,
    "IE": 2 / 1,
    "IR": 7 / 4,
    "JM": 2 / 1,
    "JO": 2 / 1,
    "MX": 7 / 4,
    "NG": 2 / 1,
    "NO": 11 / 8,
    "NZ": 2 / 1,
    "PY": 5 / 3,
    "PL": 8 / 5,
    "QA": 28 / 11,
    "SI": 2 / 1,
    "SE": 8 / 5,
    "SCO": 5 / 3,
    "CH": 1 / 1,
    "AE": 2 / 1,
    "US": 19 / 10,
    "UZ": 2 / 1,
}


FIFA_TLA_TO_NAME_ALIASES = {
    "ALB": ("Albania",),
    "ALG": ("Algeria",),
    "ARG": ("Argentina",),
    "AUS": ("Australia",),
    "AUT": ("Austria",),
    "BEL": ("Belgium",),
    "BIH": ("Bosnia and Herzegovina", "Bosnia & Herzegovina", "Bosnia-Herzegovina"),
    "BOL": ("Bolivia",),
    "BRA": ("Brazil", "Brasil"),
    "CAN": ("Canada",),
    "CHI": ("Chile",),
    "CHL": ("Chile",),
    "CIV": ("Cote d'Ivoire", "Côte d'Ivoire", "Ivory Coast"),
    "CMR": ("Cameroon",),
    "COD": (
        "Congo DR",
        "DR Congo",
        "Democratic Republic of the Congo",
        "Congo (DR)",
        "Congo DR.",
        "DRC",
    ),
    "COL": ("Colombia",),
    "CRC": ("Costa Rica",),
    "CRO": ("Croatia", "Hrvatska"),
    "CPV": ("Cape Verde", "Cabo Verde", "Cabo-Verde"),
    "CZE": ("Czechia", "Czech Republic"),
    "CUW": ("Curacao", "Curaçao", "Curazao"),
    "DEN": ("Denmark", "Danmark"),
    "ECU": ("Ecuador",),
    "EGY": ("Egypt",),
    "ENG": ("England",),
    "ESP": ("Spain", "España", "Espana"),
    "FRA": ("France",),
    "GAB": ("Gabon",),
    "GER": ("Germany", "Deutschland", "Germania", "Alemania", "Allemagne", "Alemanha"),
    "GHA": ("Ghana",),
    "GRE": ("Greece", "Hellas"),
    "HAI": ("Haiti",),
    "HON": ("Honduras",),
    "HUN": ("Hungary",),
    "IRL": ("Ireland",),
    "IRN": ("Iran", "IR Iran", "Iran Islamic Republic"),
    "IRQ": ("Iraq",),
    "ITA": ("Italy", "Italia", "Italie", "Italien"),
    "JAM": ("Jamaica",),
    "JOR": ("Jordan",),
    "JPN": ("Japan", "Nippon"),
    "KOR": ("South Korea", "Korea Republic", "Republic of Korea", "Korea"),
    "KSA": ("Saudi Arabia", "Saudi"),
    "MAR": ("Morocco", "Maroc"),
    "MEX": ("Mexico", "México", "Mejico"),
    "NED": ("Netherlands", "Holland", "Nederland", "The Netherlands"),
    "NGA": ("Nigeria",),
    "NOR": ("Norway", "Norge"),
    "NZL": ("New Zealand",),
    "PAN": ("Panama", "Panamá"),
    "PAR": ("Paraguay",),
    "PER": ("Peru", "Perú"),
    "POL": ("Poland", "Polska"),
    "POR": ("Portugal",),
    "QAT": ("Qatar",),
    "ROU": ("Romania", "Rumania"),
    "RSA": ("South Africa",),
    "SCO": ("Scotland",),
    "SEN": ("Senegal",),
    "SRB": ("Serbia", "Srbija"),
    "SUI": ("Switzerland", "Suisse", "Schweiz", "Svizzera"),
    "SVK": ("Slovakia",),
    "SVN": ("Slovenia",),
    "SWE": ("Sweden", "Sverige"),
    "TUN": ("Tunisia",),
    "TUR": ("Turkey", "Türkiye", "Turkiye"),
    "UAE": ("United Arab Emirates", "U.A.E.", "UAE"),
    "UKR": ("Ukraine",),
    "URU": ("Uruguay",),
    "URY": ("Uruguay",),
    "USA": ("United States", "United States of America", "USA", "US", "U.S.", "America"),
    "UZB": ("Uzbekistan",),
    "VEN": ("Venezuela",),
    "WAL": ("Wales",),
    "ZAM": ("Zambia",),
}

FIFA_TLA_EQUIVALENTS = {
    "ARE": "UAE",
    "CHE": "SUI",
    "CVE": "CPV",
    "DEU": "GER",
    "DNK": "DEN",
    "HRV": "CRO",
    "NLD": "NED",
    "PRT": "POR",
    "SAU": "KSA",
    "ZAF": "RSA",
}

FIFA_ZH_NAME_TO_TLA = {}
for _tla, _zh_name in FIFA_TLA_TO_ZH_NAME.items():
    if _zh_name:
        FIFA_ZH_NAME_TO_TLA[_zh_name] = FIFA_TLA_EQUIVALENTS.get(_tla, _tla)


def _normalize_country_alias(value):
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("&", " and ")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in text.lower() if ch.isalnum())


FIFA_COUNTRY_ALIAS_TO_TLA = {}
for _tla, _aliases in FIFA_TLA_TO_NAME_ALIASES.items():
    for _alias in (_tla, *_aliases):
        _normalized_alias = _normalize_country_alias(_alias)
        if _normalized_alias:
            FIFA_COUNTRY_ALIAS_TO_TLA[_normalized_alias] = _tla
for _alias_tla, _canonical_tla in FIFA_TLA_EQUIVALENTS.items():
    _normalized_alias = _normalize_country_alias(_alias_tla)
    if _normalized_alias:
        FIFA_COUNTRY_ALIAS_TO_TLA[_normalized_alias] = _canonical_tla

NBA_TEAM_ALIAS_TO_CODE = {}
for _team_code, _aliases in NBA_ODDS_TEAM_ALIASES.items():
    for _alias in (_team_code, *_aliases):
        _normalized_alias = _normalize_country_alias(_alias)
        if _normalized_alias:
            NBA_TEAM_ALIAS_TO_CODE[_normalized_alias] = _team_code

MLB_TEAM_ALIAS_TO_CODE = {}
for _team_name, _team_code in MLB_TEAM_CODES.items():
    _normalized_alias = _normalize_country_alias(_team_name)
    if _normalized_alias:
        MLB_TEAM_ALIAS_TO_CODE[_normalized_alias] = _team_code
for _team_code, _aliases in MLB_TEAM_NAME_ALIASES.items():
    for _alias in (_team_code, *_aliases):
        _normalized_alias = _normalize_country_alias(_alias)
        if _normalized_alias:
            MLB_TEAM_ALIAS_TO_CODE[_normalized_alias] = _team_code

NFL_TEAM_ALIAS_TO_CODE = {}
for _team_code, _aliases in NFL_TEAM_NAME_ALIASES.items():
    for _alias in (_team_code, *_aliases):
        _normalized_alias = _normalize_country_alias(_alias)
        if _normalized_alias:
            NFL_TEAM_ALIAS_TO_CODE[_normalized_alias] = _team_code

NCAA_TEAM_ALIAS_TO_CODE = {}
for _team_code, _aliases in NCAA_TEAM_NAME_ALIASES.items():
    for _alias in (_team_code, *_aliases):
        _normalized_alias = _normalize_country_alias(_alias)
        if _normalized_alias:
            NCAA_TEAM_ALIAS_TO_CODE[_normalized_alias] = _team_code


SportsDashboard = None


class SportsDashboardCommonMixin:
    def generate_image(self, settings, device_config):
        dimensions = self._display_dimensions(device_config)
        timezone_info = self._timezone(settings, device_config)
        now = datetime.now(timezone_info)
        theme_context = self._sports_dashboard_theme_context(settings, device_config, now)
        theme_token = _ACTIVE_COLORS.set(self._sports_dashboard_colors(theme_context))
        try:
            return self._generate_image_with_active_colors(settings, device_config, dimensions, timezone_info, now)
        finally:
            _ACTIVE_COLORS.reset(theme_token)

    def _generate_image_with_active_colors(self, settings, device_config, dimensions, timezone_info, now):
        left_width = self._left_width(settings, dimensions)
        visible_worldcup_matches = self._visible_worldcup_matches(settings)
        separator_height = 4
        worldcup_height = self._worldcup_top_height(settings, (left_width, dimensions[1]), visible_worldcup_matches)
        nba_top = worldcup_height + separator_height
        nba_height = max(1, dimensions[1] - nba_top)

        image = Image.new("RGB", dimensions, COLORS["paper"])
        left_source = "api"
        left = self._try_worldcup_scoreboard_panel(
            settings,
            device_config,
            (left_width, worldcup_height),
            timezone_info,
            visible_worldcup_matches,
            now,
        )
        if left is None:
            left = self._try_worldcup_football_data_panel(
                settings,
                device_config,
                (left_width, worldcup_height),
                timezone_info,
                visible_worldcup_matches,
                now,
            )
        if left is None:
            left = self._try_worldcup_api_panel(
                settings,
                device_config,
                (left_width, worldcup_height),
                timezone_info,
                visible_worldcup_matches,
                now,
            )
        if left is None:
            if self._bool_setting(settings, "worldCupScreenshotFallback", False):
                left_source = "screenshot"
                left = self._take_worldcup_screenshot(
                    settings,
                    (left_width, worldcup_height),
                    self._timezone_key(timezone_info),
                    visible_worldcup_matches,
                )
        if left is None:
            left_source = "fallback"
            left = self._render_worldcup_fallback(
                (left_width, worldcup_height),
                visible_worldcup_matches,
                self._worldcup_configured_season(settings),
            )
        left, worldcup_content_box = self._prepare_worldcup_panel(
            left.convert("RGB"),
            (left_width, worldcup_height),
            visible_worldcup_matches,
        )
        image.paste(left, (0, 0))

        if left_source == "screenshot" and self._bool_setting(settings, "overlayWorldCupLocalTimes", True):
            self._overlay_worldcup_local_times(
                image,
                left_width,
                timezone_info,
                visible_worldcup_matches,
                worldcup_content_box,
            )

        draw = ImageDraw.Draw(image)
        separator_y = worldcup_height
        draw.rectangle((0, separator_y, left_width - 1, separator_y + separator_height - 1), fill=COLORS["border"])
        if separator_height > 2:
            draw.line((0, separator_y + 2, left_width - 1, separator_y + 2), fill=COLORS["line"], width=1)

        nba_events, nba_source_state = self._load_nba_events(settings, timezone_info)
        nba_events = self._attach_nba_odds(nba_events, settings, device_config, timezone_info)
        nba_selected = self._select_nba_events(nba_events, now)
        self._write_nba_live_state(nba_selected, now, nba_source_state)
        if self._should_show_offseason_hub_panel(settings, nba_selected):
            try:
                hub_selected, hub_source_state = self._load_offseason_hub(settings, timezone_info, now)
                self._write_offseason_hub_state(hub_selected, now, hub_source_state)
                self._draw_offseason_hub_compact_panel(
                    image,
                    draw,
                    (0, nba_top, left_width - 1, nba_top + nba_height - 1),
                    hub_selected,
                    hub_source_state,
                    now,
                )
            except Exception as exc:
                logger.warning("Offseason hub panel failed, falling back to NBA panel: %s", exc)
                self._draw_nba_compact_panel(
                    image,
                    draw,
                    (0, nba_top, left_width - 1, nba_top + nba_height - 1),
                    nba_selected,
                    nba_source_state,
                    now,
                )
        else:
            self._draw_nba_compact_panel(
                image,
                draw,
                (0, nba_top, left_width - 1, nba_top + nba_height - 1),
                nba_selected,
                nba_source_state,
                now,
            )

        lol_cards = self._load_lol_esports_sidebar_cards(settings, device_config, timezone_info, now)
        lol_sidebar_override = self._lol_esports_sidebar_override(settings)
        if lol_sidebar_override:
            esports_choice = {
                "kind": "lol",
                "choice": self._select_lol_esports_sidebar(lol_cards, now, league_override=lol_sidebar_override),
            }
        else:
            ewc_card = None
            if self._bool_setting(settings, "ewcSidebarEnabled", True):
                try:
                    ewc_card = self._load_ewc_sidebar_card(settings, timezone_info, now)
                    if ewc_card:
                        self._write_ewc_live_state(
                            ewc_card.get("selected"),
                            now,
                            ewc_card.get("source_state") or "EWC DATA",
                        )
                except Exception as exc:
                    logger.warning("EWC sidebar failed, falling back to other esports panels: %s", _safe_exception_text(exc))
            valve_selected = None
            valve_source_state = ""
            if self._bool_setting(settings, "valveEsportsEnabled", True):
                try:
                    valve_selected, valve_source_state = self._load_valve_esports(settings, timezone_info, now)
                except Exception as exc:
                    logger.warning("Valve esports sidebar failed, falling back to LPL: %s", _safe_exception_text(exc))
            esports_choice = self._select_right_esports_sidebar(lol_cards, valve_selected, valve_source_state, now, ewc_card=ewc_card)

        if esports_choice.get("kind") == "ewc":
            ewc_selected = esports_choice["selected"]
            ewc_source_state = esports_choice["source_state"]
            self._draw_ewc_sidebar(image, left_width, ewc_selected, ewc_source_state, now)
            return image

        if esports_choice.get("kind") == "valve":
            valve_selected = esports_choice["selected"]
            valve_source_state = esports_choice["source_state"]
            self._write_valve_esports_live_state(valve_selected, now, valve_source_state)
            self._draw_valve_esports_sidebar(image, left_width, valve_selected, valve_source_state, now)
            return image

        lol_choice = esports_choice["choice"]
        lol_selected = lol_choice["selected"]
        lol_source_state = lol_choice["source_state"]
        lol_league_key = lol_choice["league_key"]
        self._attach_lpl_realtime_info(lol_selected, settings, league_key=lol_league_key)
        self._write_lol_live_state(lol_selected, now, lol_source_state, league_key=lol_league_key)
        self._draw_lpl_sidebar(image, left_width, lol_selected, lol_source_state, now, league_key=lol_league_key)
        return image

    @staticmethod
    def _sports_dashboard_theme_context(settings, device_config, now):
        requested = str(settings.get("sportsDashboardTheme") or "").strip().lower()
        if requested in {"night", "dark", "midnight", "deep-night", "deep_night"}:
            return {"mode": "night", "source": "sports_dashboard", "reason": "forced night"}
        if requested in {"day", "light", "paper"}:
            return {"mode": "day", "source": "sports_dashboard", "reason": "forced day"}
        if get_theme_context:
            try:
                return get_theme_context(device_config, now)
            except Exception as exc:
                logger.warning("SportsDashboard theme context failed: %s", exc)
        local_time = now.timetz().replace(tzinfo=None)
        mode = "day" if 7 <= local_time.hour < 19 else "night"
        return {"mode": mode, "source": "sports_dashboard", "reason": "local fallback"}

    @staticmethod
    def _sports_dashboard_colors(theme_context):
        mode = str((theme_context or {}).get("mode") or "day").strip().lower()
        return DEEP_NIGHT_COLORS if mode == "night" else DAY_COLORS

    @staticmethod
    def _display_dimensions(device_config):
        if _resolve_dimensions is not None:
            try:
                return _resolve_dimensions(device_config)
            except Exception:
                logger.exception("resolve_dimensions from utils.app_utils is unavailable; using fallback path")
        width = None
        height = None
        orientation = "horizontal"

        if device_config is not None:
            try:
                if isinstance(device_config, Mapping):
                    resolution = device_config.get("resolution")
                else:
                    resolution = device_config.get_config("resolution", None) if hasattr(device_config, "get_config") else None
                if isinstance(resolution, Mapping):
                    width = resolution.get("width")
                    height = resolution.get("height")
                elif isinstance(resolution, (list, tuple)) and len(resolution) >= 2:
                    width, height = resolution[0], resolution[1]
            except Exception:
                width = None
                height = None

            if width is None and hasattr(device_config, "get_resolution"):
                try:
                    width, height = device_config.get_resolution()
                except Exception:
                    width = None
                    height = None

            if width is None and hasattr(device_config, "get_config"):
                try:
                    orientation = str(device_config.get_config("orientation", orientation)).strip().lower()
                except Exception:
                    orientation = orientation
                if width is None:
                    width = device_config.get_config("width", None)
                if height is None:
                    height = device_config.get_config("height", None)

        width = int(width) if width else 800
        height = int(height) if height else 480

        if orientation == "vertical":
            return (height, width)
        return (width, height)

    @staticmethod
    def _left_width(settings, dimensions):
        width, _height = dimensions
        default_width = width - MIN_LPL_SIDEBAR_WIDTH - LPL_SEPARATOR_WIDTH
        raw_value = settings.get("worldCupLeftWidth", default_width)
        try:
            left_width = int(raw_value)
        except (TypeError, ValueError):
            left_width = default_width
        max_left_width = width - MIN_LPL_SIDEBAR_WIDTH - LPL_SEPARATOR_WIDTH
        return max(360, min(max_left_width, left_width))

    @staticmethod
    def _bool_setting(settings, key, default):
        value = settings.get(key)
        if value is None:
            return default
        return str(value).strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _force_refresh_requested(settings):
        settings = settings or {}
        for key in ("forceRefresh", "force_refresh", "refreshNow", "retry"):
            if SportsDashboard._bool_setting(settings, key, False):
                return True
        return False

    def _sports_dashboard_cache_dir(self):
        if os.getenv("INKYPI_CACHE_DIR", "").strip():
            return self.cache_dir(leaf="cache")
        cache_dir = Path(self.get_plugin_dir("cache"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    @staticmethod
    def _cache_is_fresh_seconds(cache, cache_seconds, now_utc):
        fetched_at = SportsDashboard._parse_cached_utc(cache.get("fetched_at"))
        if fetched_at is None:
            return False
        return now_utc - fetched_at <= timedelta(seconds=cache_seconds)

    @staticmethod
    def _safe_api_error_message(exc):
        text = str(exc)
        return text[:240]

    @staticmethod
    def _read_json_file(path):
        return read_json_file(path)

    @staticmethod
    def _write_json_file(path, payload):
        write_json_file(path, payload)

    @staticmethod
    def _int_setting(settings, key, default, minimum, maximum):
        try:
            value = int(settings.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _draw_local_wordmark(self, image, path, x, y, max_width, max_height, alpha_threshold=8, align_bottom_y=None):
        wordmark = self._load_local_logo(path, (int(max_width), int(max_height)), alpha_threshold=alpha_threshold)
        if not wordmark:
            return False
        paste_x = int(x)
        if align_bottom_y is None:
            paste_y = int(y + (int(max_height) - wordmark.height) / 2)
        else:
            paste_y = int(align_bottom_y) - wordmark.height + 1
        image.paste(wordmark, (paste_x, paste_y), wordmark)
        return True

    def _draw_standalone_sport_header(self, image, draw, x1, y1, x2, sport, card, source_state):
        sport = str(sport or "MLB").upper()
        accent = self._hub_sport_accent(sport)
        header_y = y1 + 8
        logo_w = 74 if sport in {"MLB", "WNBA"} else 42
        self._draw_sport_logo(image, draw, sport, x1 + 14, header_y - 1, logo_w, 34)
        title_x = x1 + 98 if sport in {"MLB", "WNBA"} else x1 + 66
        title_text = "PGA TOUR" if sport == "PGA" else ("NCAA FB" if sport == "NCAA" else sport)
        title_drawn = False
        if sport == "MLB":
            title_drawn = self._draw_mlb_title_wordmark(image, title_x, header_y - 1, 154, 24)
        elif sport == "WNBA":
            title_drawn = self._draw_wnba_title_wordmark(image, title_x, header_y - 1, 154, 24)
        elif sport == "PGA":
            title_drawn = self._draw_pga_title_wordmark(image, title_x, header_y - 1, 154, 24)
        if not title_drawn:
            title, title_font = self._fit_text(draw, title_text, 134, 22, bold=True, min_size=15)
            draw.text((title_x, header_y), title, font=title_font, fill=COLORS["text"])
        source_label = self._standalone_sport_source_label(sport, card, source_state)
        source_label, source_font = self._fit_text(draw, source_label, 112, 10, bold=True, min_size=7)
        draw.text((title_x, header_y + 24), source_label, font=source_font, fill=COLORS["muted"])
        status = str((card or {}).get("status") or "NEXT").upper()
        strip_drawn = self._draw_standalone_sport_header_cutout(
            image,
            sport,
            title_x + SPORT_HEADER_CUTOUT_TITLE_GAP,
            header_y - 6,
            x2 - 92,
            y1 + 48,
            accent,
        )
        self._draw_status_pill(draw, x2 - 84, header_y + 3, status, status == "LIVE")
        if not strip_drawn:
            draw.rectangle((x2 - 120, header_y + 13, x2 - 112, header_y + 21), fill=accent, outline=COLORS["border"], width=1)
        draw.line((x1 + 12, y1 + 48, x2 - 12, y1 + 48), fill=COLORS["border"], width=1)

    def _draw_standalone_sport_header_cutout(self, image, sport, x1, y1, x2, y2, accent):
        x1, y1, x2, y2 = [int(value) for value in (x1, y1, x2, y2)]
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        if width < 72 or height < 16:
            return False
        cutout = self._load_sport_header_cutout(sport)
        if cutout is None:
            return False
        scale = min(SPORT_HEADER_CUTOUT_SCALE, width / cutout.width, height / cutout.height)
        if abs(scale - 1.0) > 0.01:
            cutout = cutout.resize(
                (max(1, int(round(cutout.width * scale))), max(1, int(round(cutout.height * scale)))),
                Image.LANCZOS,
            )
        tint = self._blend(accent, COLORS["text"], 0.58)
        tinted = self._tint_alpha_art(cutout, tint)
        alpha = tinted.getchannel("A").point(lambda value: min(210, value))
        tinted.putalpha(alpha)
        paste_x = x1 + max(0, int((width - tinted.width) * SPORT_HEADER_CUTOUT_LEFT_BIAS))
        if str(sport or "").upper() == "PGA":
            paste_x += PGA_HEADER_CUTOUT_X_OFFSET
        paste_y = y2 - tinted.height + 1
        image.paste(tinted, (paste_x, paste_y), tinted)
        return True

    def _draw_sport_logo(self, image, draw, sport, x, y, width, height):
        path = self._sport_logo_path(sport)
        logo = self._load_local_logo(path, (int(width), int(height)), alpha_threshold=8) if path else None
        if logo:
            image.paste(logo, (int(x) + (int(width) - logo.width) // 2, int(y) + (int(height) - logo.height) // 2), logo)
            return
        text, font = self._fit_text(draw, str(sport or "SPORT"), int(width), 11, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x, y, x + width, y + height), text, font, COLORS["muted"])

    def _draw_vertical_split(self, draw, x, y1, y2):
        draw.line((x - 3, y1, x - 3, y2), fill=COLORS["border"], width=1)
        draw.line((x - 1, y1, x - 1, y2), fill=COLORS["line"], width=1)

    @staticmethod
    def _coerce_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _draw_small_row_team_logos(self, image, draw, x1, x2, y, event, label, label_font, label_right, logo_size):
        right_logo_x = int(x2 - 21)
        text_width = self._text_width(draw, label, label_font)
        label_left = int(label_right - text_width)
        left_logo_x = label_left - logo_size - 4
        min_left_logo_x = int(x1 + 56)
        max_left_logo_x = int(right_logo_x - logo_size - 8)
        left_logo_x = max(min_left_logo_x, min(left_logo_x, max_left_logo_x))
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, y, logo_size, self._small_row_logo_fallback(event, "a"))
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, y, logo_size, self._small_row_logo_fallback(event, "b"))

    def _draw_sport_info_icon(self, draw, kind, x, y, accent):
        kind = str(kind or "").strip().upper()
        x = int(x)
        y = int(y)
        accent = accent or COLORS["muted"]
        border = COLORS["muted"]
        if kind in {"CLOCK", "QTR", "INNING", "TIP", "KICK", "FIRST", "TEE"}:
            draw.ellipse((x, y, x + 8, y + 8), outline=border, width=1)
            draw.line((x + 4, y + 4, x + 4, y + 1), fill=accent, width=1)
            draw.line((x + 4, y + 4, x + 7, y + 4), fill=accent, width=1)
        elif kind == "COUNT":
            for dot_x, dot_y in ((x + 1, y + 2), (x + 4, y + 2), (x + 7, y + 2), (x + 2, y + 6), (x + 6, y + 6)):
                draw.rectangle((dot_x, dot_y, dot_x + 1, dot_y + 1), fill=accent)
        elif kind == "BASES":
            points = [(x + 4, y), (x + 8, y + 4), (x + 4, y + 8), (x, y + 4)]
            draw.polygon(points, outline=border)
            draw.rectangle((x + 3, y + 3, x + 5, y + 5), fill=accent)
        elif kind in {"MATCHUP", "MATCH", "B/P", "BAT/P", "SP", "BAT", "P", "PIT", "PITCH"}:
            draw.line((x + 1, y + 8, x + 7, y), fill=accent, width=1)
            draw.line((x + 3, y + 8, x + 8, y + 3), fill=border, width=1)
            draw.ellipse((x, y + 1, x + 3, y + 4), outline=border, width=1)
        elif kind in {"RHE", "SCORE", "RECORD"}:
            draw.rectangle((x, y + 1, x + 8, y + 8), outline=border, width=1)
            for column_x in (x + 3, x + 6):
                draw.line((column_x, y + 1, column_x, y + 8), fill=border, width=1)
            draw.line((x, y + 4, x + 8, y + 4), fill=accent, width=1)
        elif kind == "DOWN":
            draw.polygon([(x + 4, y + 8), (x + 1, y + 4), (x + 3, y + 4), (x + 3, y), (x + 5, y), (x + 5, y + 4), (x + 7, y + 4)], fill=accent)
        elif kind == "FIELD":
            draw.rectangle((x, y + 1, x + 8, y + 8), outline=border, width=1)
            draw.line((x + 4, y + 1, x + 4, y + 8), fill=accent, width=1)
            draw.line((x + 2, y + 3, x + 2, y + 6), fill=border, width=1)
            draw.line((x + 6, y + 3, x + 6, y + 6), fill=border, width=1)
        elif kind == "PLAY":
            draw.rectangle((x, y + 1, x + 8, y + 8), outline=border, width=1)
            draw.polygon([(x + 3, y + 3), (x + 3, y + 7), (x + 7, y + 5)], fill=accent)
        elif kind == "TV":
            draw.rectangle((x, y + 1, x + 8, y + 6), outline=border, width=1)
            draw.line((x + 3, y + 8, x + 5, y + 8), fill=accent, width=1)
            draw.point((x + 4, y + 4), fill=accent)
        elif kind == "SPREAD":
            draw.line((x, y + 7, x + 8, y + 1), fill=accent, width=1)
            draw.line((x, y + 2, x + 3, y + 2), fill=border, width=1)
            draw.line((x + 5, y + 7, x + 8, y + 7), fill=border, width=1)
        elif kind == "TOTAL":
            draw.rectangle((x, y + 1, x + 8, y + 3), outline=border, width=1)
            draw.rectangle((x, y + 5, x + 8, y + 7), outline=accent, width=1)
        elif kind in {"VENUE", "SITE"}:
            draw.ellipse((x + 1, y, x + 7, y + 6), outline=border, width=1)
            draw.point((x + 4, y + 3), fill=accent)
            draw.line((x + 4, y + 6, x + 4, y + 8), fill=accent, width=1)
        elif kind in {"LEAD", "WIN"}:
            draw.line((x, y + 6, x + 4, y + 2), fill=accent, width=1)
            draw.line((x + 4, y + 2, x + 8, y + 6), fill=accent, width=1)
            draw.line((x + 4, y + 2, x + 4, y + 8), fill=border, width=1)
        elif kind == "PERIOD":
            draw.rectangle((x, y + 1, x + 8, y + 8), outline=border, width=1)
            draw.arc((x + 2, y + 2, x + 6, y + 6), 0, 360, fill=accent, width=1)
        elif kind in {"GOLF", "PGA"}:
            draw.line((x + 2, y, x + 2, y + 8), fill=border, width=1)
            draw.polygon([(x + 3, y), (x + 8, y + 2), (x + 3, y + 4)], fill=accent)
            draw.arc((x + 4, y + 5, x + 8, y + 9), 0, 360, fill=border, width=1)
        else:
            draw.rectangle((x, y + 1, x + 8, y + 8), outline=border, width=1)
            draw.rectangle((x + 3, y + 4, x + 5, y + 6), fill=accent)

    def _draw_compact_match_core(self, image, draw, x1, x2, y, event, center_text, logo_size=24, team_size=13):
        center_x = (x1 + x2) / 2
        left_area = (x1, center_x - 40)
        right_area = (center_x + 40, x2)
        left_logo_x = int((left_area[0] + left_area[1] - logo_size) / 2)
        right_logo_x = int((right_area[0] + right_area[1] - logo_size) / 2)
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, y, logo_size, event["team_a"])
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, y, logo_size, event["team_b"])
        center_text, center_font = self._fit_text(draw, center_text, 72, 19 if center_text != "VS" else 16, bold=True, min_size=11)
        self._draw_centered(draw, (center_x, y + logo_size / 2 + 1), center_text, center_font, COLORS["text"])
        team_y = y + logo_size + 14
        team_a_label = self._nba_display_team_from_event(event, "a", full=True)
        team_b_label = self._nba_display_team_from_event(event, "b", full=True)
        team_a, font_a = self._fit_text(draw, team_a_label, left_area[1] - left_area[0], team_size, bold=True, min_size=9)
        team_b, font_b = self._fit_text(draw, team_b_label, right_area[1] - right_area[0], team_size, bold=True, min_size=9)
        team_a_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "a")]
        team_b_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "b")]
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, font_a, team_a_fill)
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, font_b, team_b_fill)

    def _draw_compact_match_row(self, image, draw, x1, x2, y, event, center_text, show_time=False, show_date=False):
        row_h = 31
        draw.rounded_rectangle((x1, y, x2, y + row_h), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + row_h - 1), fill=COLORS["nba_accent"])
        left_label = event["start"].strftime("%m/%d") if (show_date or show_time) else ""
        if left_label:
            left_label, label_font = self._fit_text(draw, left_label, 38, 10, bold=True, min_size=7)
            draw.text((x1 + 11, y + 2), left_label, font=label_font, fill=COLORS["muted"])
        top_label = self._format_time(event["start"]) if show_time else ""
        if top_label:
            top_label, top_font = self._fit_text(draw, top_label, 62, 10, bold=True, min_size=7)
            self._draw_centered(draw, ((x1 + x2) / 2, y + 7), top_label, top_font, COLORS["text"])
        row_y = y + 13 if show_time else y + 7
        self._draw_compact_row_teams(image, draw, x1 + 8, x2 - 8, row_y, event, center_text)

    def _draw_compact_row_teams(self, image, draw, x1, x2, y, event, center_text):
        center_x = (x1 + x2) / 2
        logo_size = 14
        left_logo_x = x1 + 2
        right_logo_x = x2 - logo_size - 2
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, y, logo_size, event["team_a"])
        team_a, font_a = self._fit_text(draw, event["team_a"], max(24, center_x - left_logo_x - logo_size - 22), 11, bold=True, min_size=7)
        team_a_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "a")]
        team_b_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "b")]
        self._draw_text_in_box(draw, (left_logo_x + logo_size + 4, y - 1, center_x - 28, y + 16), team_a, font_a, team_a_fill)
        center_text, center_font = self._fit_text(draw, center_text, 54, 11, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (center_x - 27, y - 1, center_x + 27, y + 16), center_text, center_font, COLORS["text"])
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, y, logo_size, event["team_b"])
        team_b, font_b = self._fit_text(draw, event["team_b"], max(24, right_logo_x - center_x - 31), 11, bold=True, min_size=7)
        self._draw_text_in_box(draw, (center_x + 28, y - 1, right_logo_x - 4, y + 16), team_b, font_b, team_b_fill, align="right")

    def _draw_status_pill(self, draw, x, y, text, is_live):
        color = COLORS["red"] if is_live else COLORS["green"]
        draw.rounded_rectangle((x, y, x + 74, y + 24), radius=5, outline=COLORS["border"], fill=COLORS["panel"], width=2)
        draw.rectangle((x + 5, y + 5, x + 13, y + 19), fill=color, outline=COLORS["border"], width=1)
        value, value_font = self._fit_text(draw, text, 46, 13, bold=True, min_size=10)
        self._draw_centered_in_box(draw, (x, y + 2, x + 74, y + 22), value, value_font, COLORS["text"])

    def _draw_section_header(self, draw, right_x, right_w, y, title, accent=None):
        accent = accent or COLORS["blue"]
        draw.rectangle((right_x + 14, y + 3, right_x + 22, y + 21), fill=accent, outline=COLORS["border"], width=1)
        draw.text((right_x + 29, y), title, font=self._font(17, True), fill=COLORS["text"])
        draw.line((right_x + 14, y + 26, right_x + right_w - 14, y + 26), fill=COLORS["border"], width=1)

    def _draw_schedule_row(self, draw, right_x, right_w, y, event):
        draw.line((right_x + 14, y - 7, right_x + right_w - 14, y - 7), fill=COLORS["line"], width=1)
        draw.text((right_x + 16, y), event["start"].strftime("%m/%d"), font=self._font(14), fill=COLORS["muted"])
        time_text = self._format_time(event["start"]).replace(":00", "")
        self._draw_right_aligned(draw, (right_x + 110, y), time_text, self._font(14), COLORS["amber"])
        label, label_font = self._fit_text(draw, self._match_label(event), right_w - 142, 17, bold=True, min_size=12)
        draw.text((right_x + 128, y - 2), label, font=label_font, fill=COLORS["text"])

    def _draw_team_logo(self, image, draw, logo_url, x, y, size, fallback_text):
        draw_size = self._team_logo_draw_size(fallback_text, size)
        draw_x = int(x - (draw_size - size) / 2)
        draw_y = int(y - (draw_size - size) / 2)
        logo = self._load_local_team_logo(fallback_text, draw_size) or self._load_team_logo_for_render(logo_url, draw_size)
        if logo:
            image.paste(logo, (draw_x + (draw_size - logo.width) // 2, draw_y + (draw_size - logo.height) // 2), logo)
            return
        draw.rounded_rectangle((draw_x, draw_y, draw_x + draw_size, draw_y + draw_size), radius=4, fill=COLORS["panel_gold"], outline=COLORS["border"], width=1)
        fallback = str(fallback_text or "?")[:1].upper()
        fallback_font = self._font(max(10, int(draw_size * 0.55)), True)
        self._draw_centered(draw, (draw_x + draw_size / 2, draw_y + draw_size / 2), fallback, fallback_font, COLORS["muted"])


    def _load_team_logo_for_render(self, logo_url, size):
        try:
            return self._load_team_logo(logo_url, size, cache_dir=self._team_logo_disk_cache_dir())
        except TypeError:
            return self._load_team_logo(logo_url, size)

    def _team_logo_disk_cache_dir(self):
        try:
            return self.managed_cache_namespace(
                self._sports_dashboard_cache_dir() / "team_logos",
                TEAM_LOGO_DISK_CACHE_BUDGET,
            ).root
        except (OSError, CacheError) as exc:
            logger.warning("Failed to prepare team logo disk cache: %s", exc)
            return None

    @staticmethod
    def _team_logo_draw_size(team_code, size):
        code = str(team_code or "").strip().upper()
        if code == "AL":
            return max(size, int(round(size * 1.3)))
        return size

    @staticmethod
    def _local_team_logo_candidates(team_code):
        code = "".join(ch for ch in str(team_code or "").strip().lower() if ch.isalnum() or ch in {"_", "-"})
        if not code:
            return []
        candidates = []
        for directory in (LOCAL_TEAM_LOGO_DIR, LOCAL_LCK_TEAM_LOGO_DIR):
            candidates.extend(
                os.path.join(directory, f"{code}{extension}")
                for extension in (".png", ".webp", ".jpg", ".jpeg")
            )
        return candidates

    @staticmethod
    def _load_local_team_logo(team_code, size):
        for path in SportsDashboard._local_team_logo_candidates(team_code):
            cache_key = (path, size)
            if cache_key in TEAM_LOGO_CACHE:
                return TEAM_LOGO_CACHE[cache_key]
            if not os.path.exists(path):
                continue
            try:
                with Image.open(path) as source:
                    logo = SportsDashboard._logo_with_transparent_background(source)
                    bbox = logo.getbbox()
                    if bbox:
                        logo = logo.crop(bbox)
                    logo = ImageOps.contain(logo, (size, size), Image.LANCZOS)
                TEAM_LOGO_CACHE[cache_key] = logo
                return logo
            except Exception as exc:
                logger.warning("Failed to load local LPL team logo %s: %s", path, exc)
                TEAM_LOGO_CACHE[cache_key] = None
        return None

    @staticmethod
    def _load_local_logo(path, size, alpha_threshold=1):
        if not path or not os.path.exists(path):
            return None
        cache_key = (path, tuple(size), alpha_threshold)
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        try:
            with Image.open(path) as source:
                logo = SportsDashboard._logo_with_transparent_background(source)
            if alpha_threshold > 1:
                pixels = logo.load()
                width, height = logo.size
                for y in range(height):
                    for x in range(width):
                        red, green, blue, alpha = pixels[x, y]
                        if alpha < alpha_threshold:
                            pixels[x, y] = (red, green, blue, 0)
            bbox = logo.getbbox()
            if bbox:
                logo = logo.crop(bbox)
            logo = ImageOps.contain(logo, size, Image.LANCZOS)
            TEAM_LOGO_CACHE[cache_key] = logo
            return logo
        except Exception as exc:
            logger.warning("Failed to load local logo %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _fetch_remote_image_bytes(url, timeout):
        response = get_http_session().get(
            url,
            headers={"User-Agent": "InkyPi/1.0"},
            timeout=timeout,
            stream=True,
        )
        return read_limited_response_bytes(response, max_bytes=TEAM_LOGO_IMAGE_LIMITS.max_bytes)

    @staticmethod
    def _load_team_logo(logo_url, size, cache_dir=None):
        if not logo_url:
            return None
        cache_key = (logo_url, size) if cache_dir is None else (logo_url, size, str(cache_dir))
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        try:
            disk_path = SportsDashboard._team_logo_disk_cache_path(cache_dir, logo_url)
            data = SportsDashboard._read_team_logo_disk_cache(disk_path)
            if data is None:
                data = SportsDashboard._fetch_remote_image_bytes(logo_url, TEAM_LOGO_FETCH_TIMEOUT_SECONDS)
                if not SportsDashboard._team_logo_data_is_safe_to_decode(data):
                    logger.warning("Skipping oversized team logo %s", logo_url)
                    TEAM_LOGO_CACHE[cache_key] = None
                    return None
                SportsDashboard._write_team_logo_disk_cache(disk_path, data)
            logo = SportsDashboard._team_logo_from_bytes(data, size)
            TEAM_LOGO_CACHE[cache_key] = logo
            return logo
        except Exception as exc:
            logger.warning("Failed to load team logo %s: %s", logo_url, _safe_exception_text(exc))
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _team_logo_from_bytes(data, size):
        if not data:
            return None
        try:
            source = safe_open_image(data, limits=TEAM_LOGO_IMAGE_LIMITS)
        except Exception:
            return None
        logo = SportsDashboard._logo_with_transparent_background(source)
        bbox = logo.getbbox()
        if bbox:
            logo = logo.crop(bbox)
        return ImageOps.contain(logo, (size, size), Image.LANCZOS)

    @staticmethod
    def _team_logo_disk_cache_path(cache_dir, logo_url):
        if cache_dir is None:
            return None
        try:
            namespace = cache_namespace_for_directory(
                Path(cache_dir),
                TEAM_LOGO_DISK_CACHE_BUDGET,
            )
        except (OSError, CacheError) as exc:
            logger.warning("Failed to prepare team logo disk cache %s: %s", cache_dir, exc)
            return None
        parsed = urlparse(str(logo_url or ""))
        suffix = Path(parsed.path).suffix.lower()
        if suffix not in {".png", ".webp", ".jpg", ".jpeg", ".gif"}:
            suffix = ".img"
        digest = hashlib.sha1(str(logo_url).encode("utf-8")).hexdigest()
        return namespace.path(digest, suffix)

    @staticmethod
    def _read_team_logo_disk_cache(path):
        if path is None or not path.exists():
            return None
        try:
            namespace = cache_namespace_for_directory(
                path.parent,
                TEAM_LOGO_DISK_CACHE_BUDGET,
            )
            data = namespace.get_bytes(path.stem, suffix=path.suffix)
        except (OSError, CacheError) as exc:
            logger.warning("Failed to read team logo disk cache %s: %s", path, exc)
            return None
        if not SportsDashboard._team_logo_data_is_safe_to_decode(data):
            try:
                namespace.remove(path.stem, suffix=path.suffix)
            except (OSError, CacheError) as exc:
                logger.warning("Failed to remove oversized team logo disk cache %s: %s", path, exc)
            return None
        return data

    @staticmethod
    def _write_team_logo_disk_cache(path, data):
        if path is None or not SportsDashboard._team_logo_data_is_safe_to_decode(data):
            return
        try:
            namespace = cache_namespace_for_directory(
                path.parent,
                TEAM_LOGO_DISK_CACHE_BUDGET,
            )
            namespace.put_bytes(path.stem, data, suffix=path.suffix)
        except (OSError, CacheError) as exc:
            logger.warning("Failed to write team logo disk cache %s: %s", path, exc)

    @staticmethod
    def _team_logo_data_is_safe_to_decode(data):
        if not data or len(data) > TEAM_LOGO_DISK_CACHE_MAX_BYTES:
            return False
        try:
            source = safe_open_image(data, limits=TEAM_LOGO_IMAGE_LIMITS)
            width, height = source.size
        except Exception:
            return False
        if width <= 0 or height <= 0:
            return False
        if width > TEAM_LOGO_DISK_CACHE_MAX_SIDE or height > TEAM_LOGO_DISK_CACHE_MAX_SIDE:
            return False
        return width * height <= TEAM_LOGO_DISK_CACHE_MAX_PIXELS

    @staticmethod
    def _logo_with_transparent_background(source):
        logo = source.convert("RGBA")
        alpha = logo.getchannel("A")
        if alpha.getextrema()[0] < 255:
            return logo

        width, height = logo.size
        if width < 2 or height < 2:
            return logo

        pixels = logo.load()
        corners = [
            pixels[0, 0][:3],
            pixels[width - 1, 0][:3],
            pixels[0, height - 1][:3],
            pixels[width - 1, height - 1][:3],
        ]
        background = max(corners, key=corners.count)
        if max(background) - min(background) > 18:
            return logo

        threshold = 28
        for y in range(height):
            for x in range(width):
                red, green, blue, _alpha = pixels[x, y]
                if (
                    abs(red - background[0]) <= threshold
                    and abs(green - background[1]) <= threshold
                    and abs(blue - background[2]) <= threshold
                ):
                    pixels[x, y] = (red, green, blue, 0)
        return logo

    @staticmethod
    def _match_label(event):
        team_a = SportsDashboard._lpl_display_team_from_event(event, "a")
        team_b = SportsDashboard._lpl_display_team_from_event(event, "b")
        return f"{team_a} vs {team_b}"

    @staticmethod
    def _result_label(event):
        if event.get("wins_a") is None or event.get("wins_b") is None:
            return SportsDashboard._match_label(event)
        team_a = SportsDashboard._lpl_display_team_from_event(event, "a")
        team_b = SportsDashboard._lpl_display_team_from_event(event, "b")
        return f"{team_a} {event['wins_a']}-{event['wins_b']} {team_b}"

    @staticmethod
    def _day_text(match_time, now):
        day_delta = (match_time.date() - now.date()).days
        if day_delta == 0:
            return "TODAY"
        if day_delta == 1:
            return "TOMORROW"
        return f"{WEEKDAYS[match_time.weekday()]} {match_time.strftime('%m/%d')}"

    @staticmethod
    def _source_label(source_state):
        return {
            "LIVE DATA": "LIVE DATA",
            "CACHE DATA": "CACHE DATA",
        }.get(str(source_state or "").upper(), str(source_state or "DATA"))

    @staticmethod
    def _font(size, bold=False):
        return get_base_ui_font(int(size), bold=bool(bold))

    @staticmethod
    def _text_width(draw, text, font):
        return text_width(draw, str(text), font)

    @staticmethod
    def _fit_text(draw, text, max_width, size, bold=False, min_size=11):
        text = str(text or "")
        for font_size in range(size, min_size - 1, -1):
            font = SportsDashboard._font(font_size, bold)
            if SportsDashboard._text_width(draw, text, font) <= max_width:
                return text, font
        return text, SportsDashboard._font(min_size, bold)

    @staticmethod
    def _fit_text_ellipsis(draw, text, max_width, size, bold=False, min_size=11):
        text = str(text or "")
        max_width = max(1, int(max_width))
        fitted, font = SportsDashboard._fit_text(draw, text, max_width, size, bold=bold, min_size=min_size)
        if SportsDashboard._text_width(draw, fitted, font) <= max_width:
            return fitted, font
        ellipsis = "..."
        if SportsDashboard._text_width(draw, ellipsis, font) > max_width:
            return "", font
        low = 0
        high = len(text)
        best = ellipsis
        while low <= high:
            mid = (low + high) // 2
            candidate = text[:mid].rstrip() + ellipsis
            if SportsDashboard._text_width(draw, candidate, font) <= max_width:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        return best, font

    @staticmethod
    def _draw_right_aligned(draw, xy, text, font, color):
        x, y = xy
        draw.text((x - SportsDashboard._text_width(draw, text, font), y), text, font=font, fill=color)

    @staticmethod
    def _draw_centered(draw, xy, text, font, color):
        x, y = xy
        box = draw.textbbox((0, 0), text, font=font)
        draw.text((x - (box[2] - box[0]) / 2, y - (box[3] - box[1]) / 2), text, font=font, fill=color)

    @staticmethod
    def _draw_text_in_box(draw, box, text, font, color, align="left"):
        left, top, right, bottom = box
        text_box = draw.textbbox((0, 0), str(text), font=font)
        text_w = text_box[2] - text_box[0]
        text_h = text_box[3] - text_box[1]
        if align == "right":
            x = right - text_w - text_box[0]
        elif align == "center":
            x = left + ((right - left) - text_w) / 2 - text_box[0]
        else:
            x = left - text_box[0]
        y = top + ((bottom - top) - text_h) / 2 - text_box[1]
        draw.text((x, y), text, font=font, fill=color)

    @staticmethod
    def _draw_centered_in_box(draw, box, text, font, color):
        SportsDashboard._draw_text_in_box(draw, box, text, font, color, align="center")

    @staticmethod
    def _blend(foreground, background, amount):
        amount = max(0.0, min(1.0, float(amount)))
        return tuple(
            int(background[index] + (foreground[index] - background[index]) * amount)
            for index in range(3)
        )

    @classmethod
    def _draw_halftone(cls, draw, bounds, color, paper, spacing, radius):
        left, top, right, bottom = [int(value) for value in bounds]
        dot = cls._blend(color, paper, 0.18)
        step = max(4, int(spacing))
        radius = max(1, int(radius))
        for y in range(top, bottom, step):
            offset = 0 if ((y - top) // step) % 2 == 0 else step // 2
            for x in range(left + offset, right, step):
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=dot)
