from collections.abc import Mapping, MutableMapping
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from io import BytesIO
import hashlib
import json
import re
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import logging
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import resolve_path

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
VALVE_ESPORTS_STATE_VERSION = "sports-dashboard-valve-esports-v1"
VALVE_ESPORTS_LIVE_STATE_VERSION = "sports-dashboard-valve-esports-live-v1"
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
LOL_ESPORTS_ROTATION_MINUTES = 30
LPL_LIVE_STATES = {"inprogress", "in_progress", "in-progress", "live"}
LPL_INFERRED_LIVE_WINDOW = timedelta(hours=6)
LPL_LIVE_PREGAME_WINDOW = timedelta(minutes=30)
LPL_LIVE_STATS_MAX_FRAME_AGE = timedelta(minutes=10)
FLAG_IMAGE_URL_TEMPLATE = "https://flagcdn.com/w80/{country_code_lower}.png"
DEFAULT_LPL_LEAGUE_ID = "98767991314006698"
DEFAULT_LCK_LEAGUE_ID = "98767991310872058"
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
LOCAL_NBA_COURT_STRIP_PATH = os.path.join(LOCAL_DECOR_DIR, "nba_court_strip.png")
LOCAL_MLB_HEADER_CUTOUT_PATH = os.path.join(LOCAL_DECOR_DIR, "mlb_header_cutout.png")
LOCAL_WNBA_HEADER_CUTOUT_PATH = os.path.join(LOCAL_DECOR_DIR, "wnba_header_cutout.png")
LOCAL_PGA_HEADER_CUTOUT_PATH = os.path.join(LOCAL_DECOR_DIR, "pga_header_cutout.png")
LOCAL_NFL_HEADER_CUTOUT_PATH = os.path.join(LOCAL_DECOR_DIR, "nfl_header_cutout.png")
LOCAL_NCAA_HEADER_CUTOUT_PATH = os.path.join(LOCAL_DECOR_DIR, "ncaa_header_cutout.png")
SPORT_HEADER_CUTOUT_SCALE = 1.24
SPORT_HEADER_CUTOUT_LEFT_BIAS = 0.45
SPORT_HEADER_CUTOUT_TITLE_GAP = 104
PGA_HEADER_CUTOUT_X_OFFSET = 16
LOCAL_NBA_EMPTY_SLOT_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "nba_empty_slot_filler.png")
LOCAL_NBA_OFFSEASON_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "nba_offseason_filler.png")
LOCAL_NBA_OFFSEASON_ACCENT_PATH = os.path.join(LOCAL_DECOR_DIR, "nba_offseason_accent.png")
LOCAL_PGA_FAIRWAY_STRIP_PATH = os.path.join(LOCAL_DECOR_DIR, "pga_fairway_strip.png")
LOCAL_LPL_MARBLE_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "lpl_marble_filler.png")
LOCAL_LPL_MSI_NEXT_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "lpl_msi_next_filler.png")
LOCAL_LPL_MSI_OFFSEASON_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "lpl_msi_offseason_filler.png")
LOCAL_LPL_MSI_CARD_ACCENT_PATH = os.path.join(LOCAL_DECOR_DIR, "lpl_msi_card_accent.png")
LOCAL_LPL_MSI_CARD_ACCENT_DIR = os.path.join(LOCAL_DECOR_DIR, "lpl_msi_card_accents")
LOLESPORTS_API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
LOLESPORTS_SCHEDULE_URL = (
    "https://esports-api.lolesports.com/persisted/gw/getSchedule"
    "?hl=en-US&leagueId={league_id}"
)
LOLESPORTS_LIVE_URL = "https://esports-api.lolesports.com/persisted/gw/getLive?hl=en-US"
LOLESPORTS_EVENT_DETAILS_URL = "https://esports-api.lolesports.com/persisted/gw/getEventDetails?hl=en-US&id={event_id}"
LOLESPORTS_LIVE_STATS_WINDOW_URL = "https://feed.lolesports.com/livestats/v1/window/{game_id}"
BO3_API_BASE_URL = "https://api.bo3.gg/api/v1"
TEAM_LOGO_CACHE = {}
FLAG_IMAGE_CACHE = {}

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


class SportsDashboard(BasePlugin):
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
            valve_selected = None
            valve_source_state = ""
            if self._bool_setting(settings, "valveEsportsEnabled", True):
                try:
                    valve_selected, valve_source_state = self._load_valve_esports(settings, timezone_info, now)
                except Exception as exc:
                    logger.warning("Valve esports sidebar failed, falling back to LPL: %s", _safe_exception_text(exc))
            esports_choice = self._select_right_esports_sidebar(lol_cards, valve_selected, valve_source_state, now)

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
    def _timezone(settings, device_config):
        timezone_name = str(
            settings.get("localTimezone")
            or settings.get("timezone")
            or device_config.get_config("timezone", DEFAULT_TIMEZONE)
            or DEFAULT_TIMEZONE
        ).strip()
        if timezone_name.upper() == "UTC":
            return timezone.utc
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            logger.warning("Unknown timezone %s, falling back to %s.", timezone_name, DEFAULT_TIMEZONE)
            try:
                return ZoneInfo(DEFAULT_TIMEZONE)
            except ZoneInfoNotFoundError:
                logger.warning("Default timezone %s unavailable, falling back to UTC.", DEFAULT_TIMEZONE)
                return timezone.utc

    @staticmethod
    def _timezone_key(timezone_info):
        return getattr(timezone_info, "key", None) or getattr(timezone_info, "zone", None) or "UTC"

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
    def _visible_worldcup_matches(settings):
        raw_value = settings.get("worldCupVisibleMatches", DEFAULT_WORLD_CUP_VISIBLE_MATCHES)
        try:
            visible_matches = int(raw_value)
        except (TypeError, ValueError):
            visible_matches = DEFAULT_WORLD_CUP_VISIBLE_MATCHES
        return max(1, min(WORLD_CUP_VISIBLE_MATCH_LIMIT, visible_matches))

    @staticmethod
    def _worldcup_top_height(settings, dimensions, visible_matches):
        width, height = dimensions
        default_height = min(DEFAULT_WORLD_CUP_TOP_HEIGHT, max(160, height - 220))
        raw_value = settings.get("worldCupTopHeight", default_height)
        try:
            top_height = int(raw_value)
        except (TypeError, ValueError):
            top_height = default_height
        minimum = 150 if visible_matches <= 3 else 188
        maximum = max(minimum, height - 180)
        if width < 600:
            minimum = max(140, minimum - 18)
        return max(minimum, min(maximum, top_height))

    @staticmethod
    def _worldcup_crop_height(visible_matches):
        return min(480, 43 + visible_matches * 61 + 6)

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

    def _take_worldcup_screenshot(self, settings, dimensions, timezone_name, visible_matches):
        url = str(settings.get("worldCupUrl") or DEFAULT_WORLD_CUP_URL).strip()
        capture_width = self._worldcup_capture_width(settings, dimensions[0], visible_matches)
        capture_height = self._worldcup_crop_height(visible_matches)
        try:
            logger.info("Taking World Cup widget screenshot: %s", url)
            return take_screenshot(
                url,
                (capture_width, capture_height),
                timeout_ms=40000,
                timezone_name=timezone_name,
            )
        except Exception as exc:
            logger.warning("World Cup screenshot failed: %s", exc)
            return None

    @staticmethod
    def _worldcup_capture_width(settings, target_width, visible_matches):
        if visible_matches > 3:
            return target_width
        raw_value = settings.get("worldCupCaptureWidth", DEFAULT_WORLD_CUP_ZOOM_WIDTH)
        try:
            capture_width = int(raw_value)
        except (TypeError, ValueError):
            capture_width = DEFAULT_WORLD_CUP_ZOOM_WIDTH
        return max(320, min(target_width, capture_width))

    def _prepare_worldcup_panel(self, image, target_dimensions, visible_matches):
        crop_height = min(image.height, self._worldcup_crop_height(visible_matches))
        image = image.crop((0, 0, image.width, crop_height))
        panel = Image.new("RGB", target_dimensions, COLORS["paper"])
        draw = ImageDraw.Draw(panel)
        self._draw_halftone(draw, (0, 0, target_dimensions[0], target_dimensions[1]), COLORS["amber"], COLORS["paper"], 18, 1)
        fitted = ImageOps.contain(image, target_dimensions, Image.LANCZOS)
        paste_x = (target_dimensions[0] - fitted.width) // 2
        paste_y = 0
        draw.rectangle(
            (paste_x, paste_y, paste_x + fitted.width - 1, paste_y + fitted.height - 1),
            fill=COLORS["border"],
        )
        panel.paste(fitted, (paste_x, paste_y))
        draw.rectangle(
            (paste_x, paste_y, paste_x + fitted.width - 1, paste_y + fitted.height - 1),
            outline=COLORS["border"],
            width=2,
        )
        return panel, (paste_x, paste_y, paste_x + fitted.width, paste_y + fitted.height)

    def _load_lpl_events(self, settings, timezone_info):
        try:
            events = self._fetch_lpl_events(settings, timezone_info)
            if events:
                return events, "LIVE DATA"
        except Exception as exc:
            logger.warning("LPL schedule fetch failed: %s", exc)
        return self._fallback_lpl_events(timezone_info), "CACHE DATA"

    def _load_lck_events(self, settings, timezone_info):
        try:
            events = self._fetch_lck_events(settings, timezone_info)
            if events:
                return events, "LCK LIVE DATA"
        except Exception as exc:
            logger.warning("LCK schedule fetch failed: %s", exc)
        return [], "LCK NO DATA"

    def _load_lol_esports_sidebar_cards(self, settings, device_config, timezone_info, now):
        lpl_events, lpl_source_state = self._load_lpl_events(settings, timezone_info)
        lpl_events = self._attach_lpl_odds(lpl_events, settings, device_config, timezone_info)
        lpl_selected = self._select_lpl_events(lpl_events, now)
        cards = [
            {
                "league_key": "LPL",
                "selected": lpl_selected,
                "source_state": lpl_source_state,
                "priority": 0,
            }
        ]
        if self._bool_setting(settings, "lckEnabled", True):
            lck_events, lck_source_state = self._load_lck_events(settings, timezone_info)
            lck_selected = self._select_lck_events(lck_events, now)
            cards.append(
                {
                    "league_key": "LCK",
                    "selected": lck_selected,
                    "source_state": lck_source_state,
                    "priority": 1,
                }
            )
        return cards

    def _load_lol_esports_sidebar(self, settings, device_config, timezone_info, now):
        league_override = self._lol_esports_sidebar_override(settings)
        cards = self._load_lol_esports_sidebar_cards(settings, device_config, timezone_info, now)
        return self._select_lol_esports_sidebar(cards, now, league_override=league_override)

    @staticmethod
    def _lol_esports_sidebar_override(settings=None):
        settings = settings or {}
        configured = str(
            settings.get("lolEsportsSidebarOverride")
            or settings.get("lolSidebarLeagueOverride")
            or ""
        ).strip().upper()
        if configured in {"LPL", "LCK"}:
            return configured
        if SportsDashboard._bool_setting(settings, "lckPreviewEnabled", False):
            return "LCK"
        if (Path(__file__).resolve().parent / "lck_preview.flag").exists():
            return "LCK"
        return ""

    @staticmethod
    def _select_lol_esports_sidebar(cards, now, league_override=None):
        cards = [dict(card) for card in (cards or []) if card and card.get("selected") is not None]
        if not cards:
            return SportsDashboard._right_sidebar_default_lpl_choice(cards, now)

        override = str(league_override or "").strip().upper()
        if override:
            for card in cards:
                if str(card.get("league_key") or "").strip().upper() == override:
                    return card

        displayable = [card for card in cards if SportsDashboard._lol_selected_has_displayable_event(card.get("selected"))]
        if not displayable:
            return SportsDashboard._right_sidebar_default_lpl_choice(cards, now)

        live_cards = [card for card in displayable if SportsDashboard._lol_sidebar_candidate_phase(card) == 0]
        if live_cards:
            return sorted(live_cards, key=SportsDashboard._right_sidebar_lol_sort_key)[0]

        upcoming_cards = [card for card in displayable if SportsDashboard._lol_sidebar_candidate_phase(card) == 1]
        if upcoming_cards:
            return sorted(upcoming_cards, key=SportsDashboard._right_sidebar_lol_sort_key)[0]

        return SportsDashboard._right_sidebar_default_lpl_choice(displayable, now)

    @staticmethod
    def _select_right_esports_sidebar(lol_cards, valve_selected, valve_source_state, now):
        lol_cards = [dict(card) for card in (lol_cards or []) if card and card.get("selected") is not None]
        candidates = []
        for card in lol_cards:
            phase = SportsDashboard._lol_sidebar_candidate_phase(card)
            if phase is None:
                continue
            candidates.append(
                {
                    "kind": "lol",
                    "choice": card,
                    "phase": phase,
                    "priority": SportsDashboard._right_sidebar_lol_priority(card),
                    "tie": SportsDashboard._right_sidebar_lol_tie_value(card),
                }
            )

        for card in SportsDashboard._valve_esports_active_cards(valve_selected):
            candidates.append(
                {
                    "kind": "valve",
                    "selected": SportsDashboard._valve_esports_selected_for_card(valve_selected, card),
                    "source_state": card.get("source_state") or valve_source_state or "VALVE DATA",
                    "phase": 0,
                    "priority": SportsDashboard._right_sidebar_valve_priority(card),
                    "tie": str(card.get("event_name") or ""),
                }
            )

        if candidates:
            return sorted(candidates, key=lambda item: (item["phase"], item["priority"], item.get("tie") or ""))[0]
        return {"kind": "lol", "choice": SportsDashboard._right_sidebar_default_lpl_choice(lol_cards, now)}

    @staticmethod
    def _lol_sidebar_candidate_phase(card):
        selected = (card or {}).get("selected") or {}
        if selected.get("live"):
            return 0
        if selected.get("upcoming"):
            return 1
        if str((card or {}).get("league_key") or "").strip().upper() == "LPL":
            return 2
        return None

    @staticmethod
    def _right_sidebar_lol_sort_key(card):
        return (SportsDashboard._right_sidebar_lol_priority(card), SportsDashboard._right_sidebar_lol_tie_value(card))

    @staticmethod
    def _right_sidebar_lol_priority(card):
        league_key = str((card or {}).get("league_key") or "").strip().upper()
        if league_key == "LPL":
            return 0
        if league_key == "LCK":
            return 1
        try:
            return int((card or {}).get("priority") or 99)
        except (TypeError, ValueError):
            return 99

    @staticmethod
    def _right_sidebar_lol_tie_value(card):
        value = SportsDashboard._lol_sidebar_main_timestamp(card, float("inf"))
        return value if value is not None else float("inf")

    @staticmethod
    def _right_sidebar_valve_priority(card):
        series = str((card or {}).get("series") or "").strip().upper()
        if series == "CS":
            return 2
        if series == "TI":
            return 3
        try:
            return 4 + int((card or {}).get("order") or 0)
        except (TypeError, ValueError):
            return 99

    @staticmethod
    def _right_sidebar_default_lpl_choice(cards, now):
        for card in cards or []:
            if str((card or {}).get("league_key") or "").strip().upper() == "LPL":
                return card
        return {"league_key": "LPL", "selected": SportsDashboard._select_lpl_events([], now), "source_state": "CACHE DATA", "priority": 0}

    @staticmethod
    def _valve_esports_active_cards(selected):
        cards = (selected or {}).get("cards") or []
        return sorted(
            [card for card in cards if card and card.get("main") and card.get("window_active")],
            key=lambda card: (
                SportsDashboard._valve_esports_status_rank(card.get("status")),
                SportsDashboard._right_sidebar_valve_priority(card),
                str(card.get("event_name") or ""),
            ),
        )

    @staticmethod
    def _valve_esports_selected_for_card(selected, card):
        selected_copy = dict(selected or {})
        selected_copy["primary"] = card
        selected_copy["rotation_pool"] = [item.get("series") for item in SportsDashboard._valve_esports_active_cards(selected_copy)]
        return selected_copy

    @staticmethod
    def _lol_selected_has_displayable_event(selected):
        selected = selected or {}
        return bool(
            selected.get("live")
            or selected.get("upcoming")
            or selected.get("recent")
            or selected.get("main")
            or selected.get("featured_event")
        )

    @staticmethod
    def _rotate_lol_sidebar_cards(cards, now):
        cards = sorted(cards or [], key=lambda card: int(card.get("priority") or 0))
        if not cards:
            return None
        if len(cards) == 1:
            return cards[0]
        try:
            timestamp = int(now.timestamp()) if isinstance(now, datetime) else 0
        except (OverflowError, OSError, ValueError):
            timestamp = 0
        bucket = timestamp // max(60, LOL_ESPORTS_ROTATION_MINUTES * 60)
        return cards[bucket % len(cards)]

    @staticmethod
    def _lol_sidebar_main_timestamp(card, default):
        selected = (card or {}).get("selected") or {}
        event = selected.get("main")
        if not event and selected.get("upcoming"):
            event = selected["upcoming"][0]
        start = (event or {}).get("start")
        if isinstance(start, datetime):
            return start.timestamp()
        return default

    def _load_nba_events(self, settings, timezone_info):
        try:
            payload, source_state, _fetched_at = self._load_nba_scoreboard(settings, timezone_info)
            events = self._parse_nba_espn_events(payload, timezone_info)
            if events:
                return events, source_state
        except Exception as exc:
            logger.warning("NBA scoreboard fetch failed: %s", exc)
        return self._fallback_nba_events(timezone_info), "NBA FALLBACK"

    def _load_valve_esports(self, settings, timezone_info, now):
        preview_card = self._valve_dota2_preview_card(settings, now)
        if preview_card:
            return {
                "primary": preview_card,
                "cards": [preview_card],
                "rotation_pool": ["TI"],
                "updated_at": now.isoformat() if hasattr(now, "isoformat") else "",
                "source_states": ["DOTA2 PREVIEW"],
            }, "DOTA2 PREVIEW"
        cards = []
        source_states = []
        if self._bool_setting(settings, "valveEsportsCsapiEnabled", True):
            try:
                matches, source_state, _fetched_at = self._load_valve_csapi_matches(settings, timezone_info)
                cards.extend(self._parse_valve_cs_major_cards(matches, timezone_info, now, settings))
                source_states.append(source_state)
            except Exception as exc:
                logger.warning("CSAPI Major fetch failed: %s", _safe_exception_text(exc))
        if self._bool_setting(settings, "valveEsportsOpenDotaEnabled", True):
            try:
                matches, source_state, _fetched_at = self._load_valve_opendota_matches(settings, timezone_info)
                team_profiles = self._load_valve_opendota_team_profiles(settings, self._valve_ti_team_ids(matches))
                cards.extend(self._parse_valve_ti_cards(matches, timezone_info, now, settings, team_profiles))
                source_states.append(source_state)
            except Exception as exc:
                logger.warning("OpenDota TI fetch failed: %s", _safe_exception_text(exc))
        selected = self._select_valve_esports(cards, now)
        primary = (selected or {}).get("primary") or {}
        source_state = primary.get("source_state") or ", ".join(source_states) or "VALVE DATA"
        selected["source_states"] = source_states
        return selected, source_state

    def _valve_dota2_preview_card(self, settings, now):
        if not self._valve_dota2_preview_enabled():
            return None
        now_value = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        events = [
            {
                "series": "TI",
                "event_name": "The International 2026",
                "match_id": "dota2-preview-1",
                "start": now_value - timedelta(hours=1),
                "state": "completed",
                "team_a": "Team Liquid",
                "team_b": "Gaimin Gladiators",
                "team_a_tag": "TL",
                "team_b_tag": "GG",
                "team_a_id": 2163,
                "team_b_id": 8599101,
                "wins_a": 42,
                "wins_b": 37,
                "best_of": 3,
                "duration": 3180,
                "source": "OpenDota",
                "score_kind": "KILLS",
            },
            {
                "series": "TI",
                "event_name": "The International 2026",
                "match_id": "dota2-preview-2",
                "start": now_value - timedelta(hours=5),
                "state": "completed",
                "team_a": "Tundra Esports",
                "team_b": "LGD Gaming",
                "team_a_tag": "Tundra",
                "team_b_tag": "LGD",
                "team_a_id": 8291895,
                "team_b_id": 10150538,
                "wins_a": 28,
                "wins_b": 34,
                "best_of": 5,
                "duration": 2860,
                "source": "OpenDota",
                "score_kind": "KILLS",
            },
            {
                "series": "TI",
                "event_name": "The International 2026",
                "match_id": "dota2-preview-3",
                "start": now_value - timedelta(days=1),
                "state": "completed",
                "team_a": "BetBoom Team",
                "team_b": "Team Spirit",
                "team_a_tag": "BB",
                "team_b_tag": "Spirit",
                "team_a_id": 9131584,
                "team_b_id": 7119388,
                "wins_a": 19,
                "wins_b": 31,
                "best_of": 3,
                "duration": 2510,
                "source": "OpenDota",
                "score_kind": "KILLS",
            },
            {
                "series": "TI",
                "event_name": "The International 2026",
                "match_id": "dota2-preview-4",
                "start": now_value - timedelta(days=2),
                "state": "completed",
                "team_a": "Xtreme Gaming",
                "team_b": "Team Falcons",
                "team_a_tag": "XG",
                "team_b_tag": "Falcons",
                "team_a_id": 8261500,
                "team_b_id": 9247354,
                "wins_a": 36,
                "wins_b": 29,
                "best_of": 3,
                "duration": 3030,
                "source": "OpenDota",
                "score_kind": "KILLS",
            },
        ]
        card = self._valve_esports_card_from_events("TI", "The International 2026", events, now_value, LOCAL_TI_LOGO_PATH, 1, settings, "OpenDota")
        if card:
            card["source_state"] = "DOTA2 PREVIEW"
        return card

    @staticmethod
    def _valve_dota2_preview_enabled():
        try:
            return (Path(__file__).resolve().parent / "dota2_preview.flag").exists()
        except OSError:
            return False
    def _load_valve_csapi_matches(self, settings, timezone_info):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._valve_csapi_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._valve_csapi_cache_key(settings, timezone_info)
        force_refresh = self._force_refresh_requested(settings)
        cache_hours = self._int_setting(settings, "valveEsportsCacheHours", DEFAULT_VALVE_ESPORTS_CACHE_HOURS, 1, 48)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("matches"), list)
        if has_compatible_cache and not force_refresh and self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return cache["matches"], "CSAPI CACHE", cache.get("fetched_at")
        if self._valve_esports_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache["matches"], "CSAPI STALE", cache.get("fetched_at")
            return [], "CSAPI LIMIT", None
        try:
            payload = self._fetch_valve_csapi_payload(settings, cache_key, now_utc)
        except Exception:
            if has_compatible_cache:
                return cache["matches"], "CSAPI STALE", cache.get("fetched_at")
            raise
        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write CSAPI cache: %s", exc)
        return payload["matches"], "CSAPI LIVE", payload.get("fetched_at")

    def _fetch_valve_csapi_payload(self, settings, cache_key, now_utc):
        base_url = str(settings.get("valveEsportsCsapiBaseUrl") or CSAPI_BASE_URL).strip().rstrip("/") or CSAPI_BASE_URL
        limit = self._int_setting(settings, "valveEsportsCsLimit", DEFAULT_VALVE_ESPORTS_CS_LIMIT, 10, 500)
        session = get_http_session()
        try:
            response = session.get(
                f"{base_url}/matches/latest",
                params={"limit": str(limit)},
                headers={"Accept": "application/json", "User-Agent": "EpaperSystem/ValveEsports"},
                timeout=25,
            )
        finally:
            self._record_valve_esports_call(settings, now_utc)
        response.raise_for_status()
        matches = response.json()
        if not isinstance(matches, list):
            matches = []
        return {
            "version": VALVE_ESPORTS_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "provider": "csapi",
            "matches": matches,
        }

    def _load_valve_opendota_matches(self, settings, timezone_info):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._valve_opendota_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._valve_opendota_cache_key(settings, timezone_info)
        force_refresh = self._force_refresh_requested(settings)
        cache_hours = self._int_setting(settings, "valveEsportsCacheHours", DEFAULT_VALVE_ESPORTS_CACHE_HOURS, 1, 48)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("matches"), list)
        if has_compatible_cache and not force_refresh and self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return cache["matches"], "OPENDOTA CACHE", cache.get("fetched_at")
        if self._valve_esports_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache["matches"], "OPENDOTA STALE", cache.get("fetched_at")
            return [], "OPENDOTA LIMIT", None
        try:
            payload = self._fetch_valve_opendota_payload(settings, cache_key, now_utc)
        except Exception:
            if has_compatible_cache:
                return cache["matches"], "OPENDOTA STALE", cache.get("fetched_at")
            raise
        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write OpenDota cache: %s", exc)
        return payload["matches"], "OPENDOTA LIVE", payload.get("fetched_at")

    def _fetch_valve_opendota_payload(self, settings, cache_key, now_utc):
        base_url = str(settings.get("valveEsportsOpenDotaBaseUrl") or OPENDOTA_BASE_URL).strip().rstrip("/") or OPENDOTA_BASE_URL
        limit = self._int_setting(settings, "valveEsportsOpenDotaLimit", DEFAULT_VALVE_ESPORTS_OPENDOTA_LIMIT, 10, 500)
        session = get_http_session()
        try:
            response = session.get(
                f"{base_url}/proMatches",
                headers={"Accept": "application/json", "User-Agent": "EpaperSystem/ValveEsports"},
                timeout=25,
            )
        finally:
            self._record_valve_esports_call(settings, now_utc)
        response.raise_for_status()
        matches = response.json()
        if not isinstance(matches, list):
            matches = []
        return {
            "version": VALVE_ESPORTS_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "provider": "opendota",
            "matches": matches[:limit],
        }

    @staticmethod
    def _valve_ti_team_ids(matches):
        team_ids = set()
        for item in matches or []:
            if not isinstance(item, Mapping):
                continue
            if not SportsDashboard._is_valve_ti_main_event_name(item.get("league_name")):
                continue
            for key in ("radiant_team_id", "dire_team_id"):
                value = SportsDashboard._lpl_int_value(item.get(key))
                if value:
                    team_ids.add(value)
        return team_ids

    def _load_valve_opendota_team_profiles(self, settings, team_ids):
        team_ids = sorted({self._lpl_int_value(value) for value in team_ids or [] if self._lpl_int_value(value)})
        if not team_ids:
            return {}
        now_utc = datetime.now(timezone.utc)
        cache_path = self._valve_opendota_team_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._valve_opendota_team_cache_key(settings)
        cached_profiles = cache.get("profiles") if isinstance(cache.get("profiles"), Mapping) else {}
        profiles = dict(cached_profiles or {})
        cache_fresh = cache.get("cache_key") == cache_key and self._worldcup_cache_is_fresh(cache, DEFAULT_VALVE_ESPORTS_CACHE_HOURS, now_utc)
        missing = [team_id for team_id in team_ids if str(team_id) not in profiles]
        if cache_fresh and not missing:
            return profiles
        calls_left = self._valve_esports_calls_left(settings, now_utc)
        if calls_left <= 0:
            return profiles
        for team_id in missing[:calls_left]:
            try:
                profiles[str(team_id)] = self._fetch_valve_opendota_team_profile(settings, team_id, now_utc)
            except Exception as exc:
                logger.warning("OpenDota team profile fetch failed for %s: %s", team_id, _safe_exception_text(exc))
                profiles[str(team_id)] = {}
        try:
            self._write_json_file(
                cache_path,
                {
                    "version": VALVE_ESPORTS_STATE_VERSION,
                    "cache_key": cache_key,
                    "fetched_at": now_utc.isoformat(),
                    "provider": "opendota-teams",
                    "profiles": profiles,
                },
            )
        except OSError as exc:
            logger.warning("Failed to write OpenDota team profile cache: %s", exc)
        return profiles

    def _fetch_valve_opendota_team_profile(self, settings, team_id, now_utc):
        base_url = str((settings or {}).get("valveEsportsOpenDotaBaseUrl") or OPENDOTA_BASE_URL).strip().rstrip("/") or OPENDOTA_BASE_URL
        session = get_http_session()
        try:
            response = session.get(
                f"{base_url}/teams/{int(team_id)}",
                headers={"Accept": "application/json", "User-Agent": "EpaperSystem/ValveEsports"},
                timeout=15,
            )
        finally:
            self._record_valve_esports_call(settings, now_utc)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, Mapping) else {}

    @staticmethod
    def _parse_valve_cs_major_cards(matches, timezone_info, now, settings=None):
        grouped = {}
        for item in matches or []:
            if not isinstance(item, Mapping):
                continue
            event_name = str(item.get("event") or "").strip()
            if not SportsDashboard._is_valve_cs_major_name(event_name):
                continue
            start = SportsDashboard._parse_csapi_match_date(item.get("date"), timezone_info)
            if not start:
                continue
            team1 = item.get("team1") or {}
            team2 = item.get("team2") or {}
            match = {
                "series": "CS",
                "event_name": event_name,
                "match_id": str(item.get("id") or ""),
                "start": start,
                "state": "completed",
                "team_a": str(team1.get("name") or "TBD").strip() or "TBD",
                "team_b": str(team2.get("name") or "TBD").strip() or "TBD",
                "team_a_id": SportsDashboard._lpl_int_value(team1.get("id")),
                "team_b_id": SportsDashboard._lpl_int_value(team2.get("id")),
                "team_a_logo": str(team1.get("logo_url") or team1.get("logo") or "").strip(),
                "team_b_logo": str(team2.get("logo_url") or team2.get("logo") or "").strip(),
                "wins_a": SportsDashboard._lpl_int_value(team1.get("score")),
                "wins_b": SportsDashboard._lpl_int_value(team2.get("score")),
                "rank_a": SportsDashboard._lpl_int_value(team1.get("rank")),
                "rank_b": SportsDashboard._lpl_int_value(team2.get("rank")),
                "best_of": SportsDashboard._lpl_int_value(item.get("best_of")),
                "maps": SportsDashboard._parse_csapi_maps(item.get("maps")),
                "source": "CSAPI",
                "score_kind": "MAPS",
            }
            grouped.setdefault(event_name, []).append(match)
        cards = []
        for event_name, events in grouped.items():
            cards.append(
                SportsDashboard._valve_esports_card_from_events(
                    "CS",
                    event_name,
                    events,
                    now,
                    LOCAL_CS_MAJOR_LOGO_PATH,
                    0,
                    settings,
                    "CSAPI",
                )
            )
        return [card for card in cards if card]

    @staticmethod
    def _parse_valve_ti_cards(matches, timezone_info, now, settings=None, team_profiles=None):
        grouped = {}
        for item in matches or []:
            if not isinstance(item, Mapping):
                continue
            league_name = str(item.get("league_name") or "").strip()
            if not SportsDashboard._is_valve_ti_main_event_name(league_name):
                continue
            start = SportsDashboard._parse_opendota_start_time(item.get("start_time"), timezone_info)
            if not start:
                continue
            radiant_id = SportsDashboard._lpl_int_value(item.get("radiant_team_id"))
            dire_id = SportsDashboard._lpl_int_value(item.get("dire_team_id"))
            radiant_profile = (team_profiles or {}).get(str(radiant_id)) if radiant_id else {}
            dire_profile = (team_profiles or {}).get(str(dire_id)) if dire_id else {}
            match = {
                "series": "TI",
                "event_name": league_name,
                "match_id": str(item.get("match_id") or ""),
                "start": start,
                "state": "completed",
                "team_a": str(item.get("radiant_name") or (radiant_profile or {}).get("name") or "Radiant").strip() or "Radiant",
                "team_b": str(item.get("dire_name") or (dire_profile or {}).get("name") or "Dire").strip() or "Dire",
                "team_a_id": radiant_id,
                "team_b_id": dire_id,
                "team_a_logo": str((radiant_profile or {}).get("logo_url") or "").strip(),
                "team_b_logo": str((dire_profile or {}).get("logo_url") or "").strip(),
                "team_a_tag": str((radiant_profile or {}).get("tag") or "").strip(),
                "team_b_tag": str((dire_profile or {}).get("tag") or "").strip(),
                "wins_a": SportsDashboard._lpl_int_value(item.get("radiant_score")),
                "wins_b": SportsDashboard._lpl_int_value(item.get("dire_score")),
                "best_of": SportsDashboard._opendota_best_of(item.get("series_type")),
                "duration": SportsDashboard._lpl_int_value(item.get("duration")),
                "source": "OpenDota",
                "score_kind": "KILLS",
            }
            grouped.setdefault(league_name, []).append(match)
        cards = []
        for event_name, events in grouped.items():
            cards.append(
                SportsDashboard._valve_esports_card_from_events(
                    "TI",
                    event_name,
                    events,
                    now,
                    LOCAL_TI_LOGO_PATH,
                    1,
                    settings,
                    "OpenDota",
                )
            )
        return [card for card in cards if card]

    @staticmethod
    def _valve_esports_card_from_events(series, event_name, events, now, logo_path, order, settings=None, source=""):
        events = sorted([event for event in events or [] if isinstance(event.get("start"), datetime)], key=lambda item: item["start"], reverse=True)
        if not events:
            return None
        first_start = min(event["start"] for event in events)
        latest_start = max(event["start"] for event in events)
        window_after = SportsDashboard._int_setting(settings or {}, "valveEsportsWindowAfterDays", DEFAULT_VALVE_ESPORTS_WINDOW_AFTER_DAYS, 0, 14)
        active_until = latest_start + timedelta(days=window_after, hours=23, minutes=59)
        now_value = now if isinstance(now, datetime) else datetime.now(first_start.tzinfo or timezone.utc)
        final_reported = any(
            SportsDashboard._valve_esports_grand_final_reported(series, event)
            for event in events
            if event["start"].date() == latest_start.date()
        )
        window_active = first_start.date() <= now_value.date() <= active_until.date() and not final_reported
        status = "ACTIVE" if window_active else "BREAK"
        return {
            "series": series,
            "sport": "CS2 Major" if series == "CS" else "The International",
            "event_name": event_name,
            "status": status,
            "window_active": window_active,
            "main": events[0],
            "live": [],
            "upcoming": [],
            "recent": events,
            "events": events,
            "start": first_start,
            "end": active_until,
            "latest": latest_start,
            "logo_path": logo_path,
            "source": source,
            "source_state": f"{source.upper()} DATA" if source else "VALVE DATA",
            "order": order,
        }

    @staticmethod
    def _valve_esports_grand_final_reported(series, event):
        if str(series or "").strip().upper() != "CS":
            return False
        best_of = SportsDashboard._lpl_int_value((event or {}).get("best_of"))
        if not best_of or best_of < 5:
            return False
        wins_a = SportsDashboard._lpl_int_value((event or {}).get("wins_a"))
        wins_b = SportsDashboard._lpl_int_value((event or {}).get("wins_b"))
        if wins_a is None or wins_b is None:
            return False
        return max(wins_a, wins_b) >= (best_of // 2 + 1)

    @staticmethod
    def _select_valve_esports(cards, now):
        cards = [card for card in cards or [] if card and card.get("main")]
        pool = sorted(
            [card for card in cards if card.get("window_active")],
            key=lambda card: (SportsDashboard._valve_esports_status_rank(card.get("status")), card.get("order", 99), str(card.get("event_name") or "")),
        )
        primary = None
        if pool:
            minute_key = int(now.timestamp() // max(1, OFFSEASON_HUB_ROTATION_MINUTES * 60)) if isinstance(now, datetime) else 0
            primary = pool[minute_key % len(pool)]
        return {
            "primary": primary,
            "cards": cards,
            "rotation_pool": [card.get("series") for card in pool],
            "updated_at": now.isoformat() if hasattr(now, "isoformat") else "",
        }

    @staticmethod
    def _valve_esports_has_displayable_event(selected):
        primary = (selected or {}).get("primary") or None
        return bool(primary and primary.get("main") and primary.get("window_active"))

    @staticmethod
    def _valve_esports_status_rank(status):
        return {"LIVE": 0, "ACTIVE": 1, "NEXT": 2, "RECENT": 3, "BREAK": 4}.get(str(status or "").upper(), 5)

    @staticmethod
    def _is_valve_cs_major_name(name):
        text = str(name or "").strip().lower()
        if "major" not in text:
            return False
        excluded = ("qualifier", "closed", "open", "rmr", "road to", "regional", "last chance")
        return not any(value in text for value in excluded)

    @staticmethod
    def _is_valve_ti_main_event_name(name):
        text = " ".join(str(name or "").strip().lower().split())
        if not text.startswith("the international"):
            return False
        excluded = ("qualifier", "regional", "road to", "last chance", "closed", "open")
        return not any(value in text for value in excluded)

    @staticmethod
    def _parse_csapi_match_date(value, timezone_info):
        try:
            parsed = datetime.fromisoformat(str(value or "")).date()
        except ValueError:
            return None
        return datetime(parsed.year, parsed.month, parsed.day, 12, 0, tzinfo=timezone_info)

    @staticmethod
    def _parse_opendota_start_time(value, timezone_info):
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return None
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(timezone_info)

    @staticmethod
    def _parse_csapi_maps(maps):
        result = []
        for item in maps or []:
            if not isinstance(item, Mapping):
                continue
            result.append(
                {
                    "name": str(item.get("name") or "Map").strip() or "Map",
                    "team_a_score": SportsDashboard._lpl_int_value(item.get("team1_score")),
                    "team_b_score": SportsDashboard._lpl_int_value(item.get("team2_score")),
                }
            )
        return result

    @staticmethod
    def _opendota_best_of(series_type):
        value = SportsDashboard._lpl_int_value(series_type)
        return {0: 1, 1: 3, 2: 5}.get(value)

    @staticmethod
    def _valve_score_label(event):
        wins_a = SportsDashboard._lpl_int_value((event or {}).get("wins_a"))
        wins_b = SportsDashboard._lpl_int_value((event or {}).get("wins_b"))
        if wins_a is None or wins_b is None:
            return "VS"
        return f"{wins_a}:{wins_b}"

    def _valve_csapi_cache_path(self):
        return self._sports_dashboard_cache_dir() / "valve_csapi_matches.json"

    def _valve_opendota_cache_path(self):
        return self._sports_dashboard_cache_dir() / "valve_opendota_matches.json"

    def _valve_opendota_team_cache_path(self):
        return self._sports_dashboard_cache_dir() / "valve_opendota_teams.json"

    def _valve_esports_state_path(self):
        return self._sports_dashboard_cache_dir() / "valve_esports_state.json"

    def _valve_esports_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "valve_esports_live_state.json"

    def _valve_csapi_cache_key(self, settings, timezone_info):
        base_url = str((settings or {}).get("valveEsportsCsapiBaseUrl") or CSAPI_BASE_URL).strip().rstrip("/") or CSAPI_BASE_URL
        limit = self._int_setting(settings or {}, "valveEsportsCsLimit", DEFAULT_VALVE_ESPORTS_CS_LIMIT, 10, 500)
        return "|".join([VALVE_ESPORTS_STATE_VERSION, "csapi", base_url, str(limit), self._timezone_key(timezone_info)])

    def _valve_opendota_cache_key(self, settings, timezone_info):
        base_url = str((settings or {}).get("valveEsportsOpenDotaBaseUrl") or OPENDOTA_BASE_URL).strip().rstrip("/") or OPENDOTA_BASE_URL
        limit = self._int_setting(settings or {}, "valveEsportsOpenDotaLimit", DEFAULT_VALVE_ESPORTS_OPENDOTA_LIMIT, 10, 500)
        return "|".join([VALVE_ESPORTS_STATE_VERSION, "opendota", base_url, str(limit), self._timezone_key(timezone_info)])

    def _valve_opendota_team_cache_key(self, settings):
        base_url = str((settings or {}).get("valveEsportsOpenDotaBaseUrl") or OPENDOTA_BASE_URL).strip().rstrip("/") or OPENDOTA_BASE_URL
        return "|".join([VALVE_ESPORTS_STATE_VERSION, "opendota-teams", base_url])

    def _valve_esports_calls_left(self, settings, now_utc):
        limit = self._int_setting(settings or {}, "valveEsportsDailyLimit", DEFAULT_VALVE_ESPORTS_DAILY_LIMIT, 1, 240)
        state = self._read_json_file(self._valve_esports_state_path())
        today = now_utc.date().isoformat()
        if state.get("date") != today:
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_valve_esports_call(self, settings, now_utc):
        path = self._valve_esports_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        count = 0
        if state.get("date") == today:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        try:
            self._write_json_file(
                path,
                {
                    "version": VALVE_ESPORTS_STATE_VERSION,
                    "date": today,
                    "count": count + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as exc:
            logger.warning("Failed to write Valve esports request counter: %s", exc)

    def _write_valve_esports_live_state(self, selected, now, source_state):
        primary = (selected or {}).get("primary") or {}
        main = primary.get("main") or {}
        payload = {
            "version": VALVE_ESPORTS_LIVE_STATE_VERSION,
            "updated_at": now.astimezone(timezone.utc).isoformat() if isinstance(now, datetime) else "",
            "source_state": source_state,
            "has_live": str(primary.get("status") or "").upper() == "LIVE",
            "displaying_valve_event": bool(primary),
            "rotation_pool": (selected or {}).get("rotation_pool") or [],
        }
        if primary:
            start = main.get("start")
            end = primary.get("end")
            payload.update(
                {
                    "series": primary.get("series") or "",
                    "event_name": primary.get("event_name") or "",
                    "status": primary.get("status") or "",
                    "team_a": main.get("team_a") or "",
                    "team_b": main.get("team_b") or "",
                    "score": self._valve_score_label(main),
                    "match_id": main.get("match_id") or "",
                    "started_at": start.astimezone(timezone.utc).isoformat() if isinstance(start, datetime) else None,
                    "active_until": end.astimezone(timezone.utc).isoformat() if isinstance(end, datetime) else None,
                    "source": primary.get("source") or "",
                }
            )
        try:
            self._write_json_file(self._valve_esports_live_state_path(), payload)
        except OSError as exc:
            logger.warning("Failed to write Valve esports live state: %s", exc)

    @staticmethod
    def _should_show_offseason_hub_panel(settings, nba_selected):
        mode = str((settings or {}).get("nbaOffseasonPanelMode") or "auto").strip().lower()
        if mode in {"off", "false", "disabled", "disable", "none", "nba"}:
            return False
        if mode in {"always", "on", "true", "hub", "live-hub", "offseason"}:
            return True
        return bool((nba_selected or {}).get("offseason"))

    def _load_offseason_hub(self, settings, timezone_info, now):
        try:
            payload, source_state, _fetched_at = self._load_offseason_hub_payload(settings, timezone_info, now)
            parsed = self._parse_offseason_hub_payload(payload, timezone_info, now)
            selected = self._select_offseason_hub(parsed, now)
            if self._offseason_hub_has_displayable_card(selected):
                return selected, source_state
        except Exception as exc:
            logger.warning("Offseason hub fetch failed: %s", exc)
        parsed = self._fallback_offseason_hub_data(timezone_info, now)
        return self._select_offseason_hub(parsed, now), "HUB FALLBACK"

    @staticmethod
    def _offseason_hub_has_displayable_card(selected):
        for card in (selected or {}).get("cards") or []:
            if (card or {}).get("main") or str((card or {}).get("status") or "").upper() != "BREAK":
                return True
        return False

    def _load_offseason_hub_payload(self, settings, timezone_info, now):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._offseason_hub_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._offseason_hub_cache_key(settings, timezone_info, now_utc)
        force_refresh = self._force_refresh_requested(settings)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("payloads"), dict)
        if (
            has_compatible_cache
            and not force_refresh
            and self._offseason_hub_cache_is_fresh(cache, settings, timezone_info, now, now_utc)
        ):
            return cache, "HUB CACHE", cache.get("fetched_at")

        if self._offseason_hub_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache, "HUB STALE", cache.get("fetched_at")
            return {}, "HUB LIMIT", None

        try:
            payload = self._fetch_offseason_hub_payload(settings, timezone_info, cache_key, now_utc)
        except Exception:
            if has_compatible_cache:
                return cache, "HUB STALE", cache.get("fetched_at")
            raise

        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write offseason hub cache: %s", exc)
        return payload, "HUB LIVE", payload.get("fetched_at")

    def _fetch_offseason_hub_payload(self, settings, timezone_info, cache_key, now_utc):
        local_date = now_utc.astimezone(timezone_info).date()
        lookback = self._int_setting(settings, "offseasonHubLookbackDays", DEFAULT_OFFSEASON_HUB_LOOKBACK_DAYS, 0, 7)
        lookahead = self._int_setting(settings, "offseasonHubLookaheadDays", DEFAULT_OFFSEASON_HUB_LOOKAHEAD_DAYS, 1, 14)
        start_date = local_date - timedelta(days=lookback)
        end_date = local_date + timedelta(days=lookahead)
        session = get_http_session()
        payloads = {}
        errors = {}
        endpoints = (
            (
                "mlb",
                self._mlb_scoreboard_url(settings),
                {
                    "sportId": "1",
                    "startDate": start_date.isoformat(),
                    "endDate": end_date.isoformat(),
                    "hydrate": "team,linescore,probablePitcher",
                },
            ),
            (
                "wnba",
                self._wnba_scoreboard_url(settings),
                {
                    "dates": f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}",
                    "limit": "100",
                },
            ),
            ("pga", self._pga_scoreboard_url(settings), {}),
            (
                "nfl",
                self._nfl_scoreboard_url(settings),
                {
                    "dates": f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}",
                    "limit": "100",
                },
            ),
            (
                "ncaa",
                self._ncaa_scoreboard_url(settings),
                {
                    "dates": f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}",
                    "limit": "100",
                },
            ),
        )
        try:
            for key, url, params in endpoints:
                try:
                    response = session.get(
                        url,
                        params=params,
                        headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"},
                        timeout=20,
                    )
                    response.raise_for_status()
                    payloads[key] = response.json()
                except Exception as exc:
                    errors[key] = _safe_exception_text(exc)
                    payloads[key] = {}
        finally:
            self._record_offseason_hub_call(settings, now_utc)
        return {
            "version": OFFSEASON_HUB_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "range_start": start_date.isoformat(),
            "range_end": end_date.isoformat(),
            "payloads": payloads,
            "errors": errors,
        }

    def _offseason_hub_cache_is_fresh(self, cache, settings, timezone_info, now, now_utc):
        cache_hours = self._int_setting(settings, "offseasonHubCacheHours", DEFAULT_OFFSEASON_HUB_CACHE_HOURS, 1, 12)
        if not self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return False
        try:
            parsed = self._parse_offseason_hub_payload(cache, timezone_info, now)
            selected = self._select_offseason_hub(parsed, now)
        except Exception as exc:
            logger.debug("Offseason hub cache live candidate parse failed: %s", exc)
            return True
        if str(((selected.get("primary") or {}).get("status") or "")).upper() == "LIVE":
            return self._cache_is_fresh_seconds(cache, self._offseason_hub_live_refresh_seconds(settings), now_utc)
        return True

    def _offseason_hub_cache_key(self, settings, timezone_info, now_utc):
        local_date = now_utc.astimezone(timezone_info).date()
        lookback = self._int_setting(settings, "offseasonHubLookbackDays", DEFAULT_OFFSEASON_HUB_LOOKBACK_DAYS, 0, 7)
        lookahead = self._int_setting(settings, "offseasonHubLookaheadDays", DEFAULT_OFFSEASON_HUB_LOOKAHEAD_DAYS, 1, 14)
        return "|".join(
            [
                OFFSEASON_HUB_STATE_VERSION,
                self._mlb_scoreboard_url(settings),
                self._wnba_scoreboard_url(settings),
                self._pga_scoreboard_url(settings),
                self._nfl_scoreboard_url(settings),
                self._ncaa_scoreboard_url(settings),
                (local_date - timedelta(days=lookback)).isoformat(),
                (local_date + timedelta(days=lookahead)).isoformat(),
                getattr(timezone_info, "key", DEFAULT_TIMEZONE),
            ]
        )

    @staticmethod
    def _mlb_scoreboard_url(settings):
        value = str((settings or {}).get("mlbScoreboardUrl") or DEFAULT_MLB_SCOREBOARD_URL).strip()
        return value or DEFAULT_MLB_SCOREBOARD_URL

    @staticmethod
    def _wnba_scoreboard_url(settings):
        value = str((settings or {}).get("wnbaScoreboardUrl") or DEFAULT_WNBA_SCOREBOARD_URL).strip()
        return value or DEFAULT_WNBA_SCOREBOARD_URL

    @staticmethod
    def _pga_scoreboard_url(settings):
        value = str((settings or {}).get("pgaScoreboardUrl") or DEFAULT_PGA_SCOREBOARD_URL).strip()
        return value or DEFAULT_PGA_SCOREBOARD_URL

    @staticmethod
    def _nfl_scoreboard_url(settings):
        value = str((settings or {}).get("nflScoreboardUrl") or DEFAULT_NFL_SCOREBOARD_URL).strip()
        return value or DEFAULT_NFL_SCOREBOARD_URL

    @staticmethod
    def _ncaa_scoreboard_url(settings):
        value = str((settings or {}).get("ncaaScoreboardUrl") or DEFAULT_NCAA_SCOREBOARD_URL).strip()
        return value or DEFAULT_NCAA_SCOREBOARD_URL

    def _offseason_hub_cache_path(self):
        return self._sports_dashboard_cache_dir() / "offseason_hub.json"

    def _offseason_hub_state_path(self):
        return self._sports_dashboard_cache_dir() / "offseason_hub_state.json"

    def _offseason_hub_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "offseason_hub_live.json"

    def _offseason_hub_calls_left(self, settings, now_utc):
        limit = self._int_setting(settings, "offseasonHubDailyLimit", DEFAULT_OFFSEASON_HUB_DAILY_LIMIT, 1, 240)
        state = self._read_json_file(self._offseason_hub_state_path())
        today = now_utc.date().isoformat()
        if state.get("date") != today:
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_offseason_hub_call(self, settings, now_utc):
        path = self._offseason_hub_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        count = 0
        if state.get("date") == today:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        try:
            self._write_json_file(
                path,
                {
                    "version": OFFSEASON_HUB_STATE_VERSION,
                    "date": today,
                    "count": count + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as exc:
            logger.warning("Failed to update offseason hub request counter: %s", exc)

    @staticmethod
    def _offseason_hub_live_refresh_seconds(settings):
        return SportsDashboard._int_setting(
            settings,
            "offseasonHubLiveRefreshSeconds",
            DEFAULT_OFFSEASON_HUB_LIVE_REFRESH_SECONDS,
            30,
            900,
        )

    @staticmethod
    def _parse_offseason_hub_payload(payload, timezone_info, now):
        payloads = (payload or {}).get("payloads") or {}
        return {
            "mlb": SportsDashboard._parse_mlb_scoreboard(payloads.get("mlb"), timezone_info),
            "wnba": SportsDashboard._parse_wnba_scoreboard(payloads.get("wnba"), timezone_info),
            "pga": SportsDashboard._parse_pga_scoreboard(payloads.get("pga"), timezone_info, now),
            "nfl": SportsDashboard._parse_football_scoreboard(payloads.get("nfl"), timezone_info, "NFL"),
            "ncaa": SportsDashboard._parse_football_scoreboard(payloads.get("ncaa"), timezone_info, "NCAA"),
        }

    @staticmethod
    def _parse_mlb_scoreboard(payload, timezone_info):
        events = []
        for date_bucket in (payload or {}).get("dates") or []:
            for game in date_bucket.get("games") or []:
                event = SportsDashboard._parse_mlb_game(game, timezone_info)
                if event:
                    events.append(event)
        events.sort(key=lambda item: item["start"])
        return {"events": events}

    @staticmethod
    def _parse_mlb_game(game, timezone_info):
        if not isinstance(game, Mapping):
            return None
        start = SportsDashboard._parse_start_time(game.get("gameDate"), timezone_info)
        if not start:
            return None
        teams = game.get("teams") or {}
        away = teams.get("away") or {}
        home = teams.get("home") or {}
        away_team = away.get("team") or {}
        home_team = home.get("team") or {}
        away_name = str(away_team.get("name") or "Away").strip() or "Away"
        home_name = str(home_team.get("name") or "Home").strip() or "Home"
        away_code = SportsDashboard._mlb_team_code(away_name)
        home_code = SportsDashboard._mlb_team_code(home_name)
        status = game.get("status") or {}
        state = SportsDashboard._mlb_state(status)
        show_score = state in {"live", "final"}
        linescore = game.get("linescore") or {}
        return {
            "sport": "MLB",
            "event_id": str(game.get("gamePk") or game.get("id") or "").strip(),
            "start": start,
            "state": state,
            "status_text": str(status.get("detailedState") or status.get("abstractGameState") or "").strip(),
            "team_a": SportsDashboard._mlb_display_team_name(away_code, away_name),
            "team_b": SportsDashboard._mlb_display_team_name(home_code, home_name),
            "team_a_name": away_name,
            "team_b_name": home_name,
            "team_a_code": away_code,
            "team_b_code": home_code,
            "team_a_logo": SportsDashboard._mlb_team_logo_url(away_team, away_code),
            "team_b_logo": SportsDashboard._mlb_team_logo_url(home_team, home_code),
            "wins_a": SportsDashboard._lpl_int_value(away.get("score")) if show_score else None,
            "wins_b": SportsDashboard._lpl_int_value(home.get("score")) if show_score else None,
            "record_a": SportsDashboard._mlb_record_label(away.get("leagueRecord")),
            "record_b": SportsDashboard._mlb_record_label(home.get("leagueRecord")),
            "probable_a": SportsDashboard._mlb_pitcher_name(away.get("probablePitcher")),
            "probable_b": SportsDashboard._mlb_pitcher_name(home.get("probablePitcher")),
            "venue": str((game.get("venue") or {}).get("name") or "").strip(),
            "inning": SportsDashboard._lpl_int_value(linescore.get("currentInning")),
            "inning_label": str(linescore.get("currentInningOrdinal") or "").strip(),
            "inning_state": str(linescore.get("inningState") or "").strip(),
            "outs": SportsDashboard._lpl_int_value(linescore.get("outs")),
            "balls": SportsDashboard._lpl_int_value(linescore.get("balls")),
            "strikes": SportsDashboard._lpl_int_value(linescore.get("strikes")),
            "bases": SportsDashboard._mlb_base_state(linescore.get("offense") or {}),
            "current_batter": SportsDashboard._mlb_pitcher_name((linescore.get("offense") or {}).get("batter")),
            "current_pitcher": SportsDashboard._mlb_pitcher_name((linescore.get("defense") or {}).get("pitcher")),
            "away_line": SportsDashboard._mlb_line_score((linescore.get("teams") or {}).get("away") or {}),
            "home_line": SportsDashboard._mlb_line_score((linescore.get("teams") or {}).get("home") or {}),
        }

    @staticmethod
    def _parse_wnba_scoreboard(payload, timezone_info):
        events = SportsDashboard._parse_nba_espn_events(payload, timezone_info)
        for event in events:
            event["sport"] = "WNBA"
            event["block"] = "WNBA"
            SportsDashboard._localize_wnba_event_team(event, "a")
            SportsDashboard._localize_wnba_event_team(event, "b")
        return {"events": events}

    @staticmethod
    def _localize_wnba_event_team(event, side):
        if not isinstance(event, MutableMapping):
            return
        prefix = f"team_{side}"
        code = SportsDashboard._wnba_normalized_team_code(
            event.get(f"{prefix}_code"),
            event.get(f"{prefix}_name"),
            event.get(f"{prefix}_source_aliases"),
        )
        if code and code != "TBD":
            event[f"{prefix}_code"] = code
        localized = SportsDashboard._wnba_display_team_name(
            code,
            event.get(f"{prefix}_name"),
            event.get(f"{prefix}_source_aliases"),
        )
        if localized:
            event[prefix] = localized
        if not str(event.get(f"{prefix}_logo") or "").strip():
            event[f"{prefix}_logo"] = SportsDashboard._espn_cdn_team_logo_url("wnba", code)

    @staticmethod
    def _wnba_normalized_team_code(code, fallback="", aliases=None):
        normalized_code = str(code or "").strip().upper()
        if normalized_code in WNBA_TEAM_ZH_NAMES:
            return normalized_code
        values = [fallback, *(aliases or [])]
        for value in values:
            alias_key = _normalize_country_alias(value)
            alias_code = WNBA_TEAM_ALIAS_TO_CODE.get(alias_key)
            if alias_code:
                return alias_code
        return normalized_code or "TBD"

    @staticmethod
    def _wnba_display_team_name(code, fallback="", aliases=None, full=False):
        normalized_code = SportsDashboard._wnba_normalized_team_code(code, fallback, aliases)
        if full and normalized_code in WNBA_TEAM_ZH_FULL_NAMES:
            return WNBA_TEAM_ZH_FULL_NAMES[normalized_code]
        if normalized_code in WNBA_TEAM_ZH_NAMES:
            return WNBA_TEAM_ZH_NAMES[normalized_code]
        return str(fallback or normalized_code or "TBD").strip() or "TBD"

    @staticmethod
    def _parse_pga_scoreboard(payload, timezone_info, now):
        events = []
        for event in (payload or {}).get("events") or []:
            parsed = SportsDashboard._parse_pga_event(event, timezone_info, now)
            if parsed:
                events.append(parsed)
        if not events:
            events.extend(SportsDashboard._parse_pga_calendar((payload or {}).get("leagues") or [], timezone_info, now))
        events.sort(key=lambda item: item.get("start") or datetime.max.replace(tzinfo=timezone.utc))
        return {"events": events}

    @staticmethod
    def _parse_football_scoreboard(payload, timezone_info, sport):
        sport = str(sport or "NFL").upper()
        events = []
        for event in (payload or {}).get("events") or []:
            parsed = SportsDashboard._parse_football_event(event, timezone_info, sport)
            if parsed:
                events.append(parsed)
        events.sort(key=lambda item: item["start"])
        return {
            "events": events,
            "season_label": SportsDashboard._football_season_label(payload),
            "week_label": SportsDashboard._football_week_label(payload),
        }

    @staticmethod
    def _parse_football_event(event, timezone_info, sport):
        if not isinstance(event, Mapping):
            return None
        competitions = event.get("competitions") or []
        competition = competitions[0] if competitions else {}
        start = SportsDashboard._parse_start_time(competition.get("date") or event.get("date"), timezone_info)
        if not start:
            return None
        away, home = SportsDashboard._nba_competitors_by_side(competition.get("competitors") or [])
        if not away or not home:
            return None
        status = competition.get("status") or event.get("status") or {}
        situation = competition.get("situation") or event.get("situation") or {}
        show_score = SportsDashboard._football_state(status) in {"live", "final"}
        away_info = SportsDashboard._football_team_info(away, sport, show_score)
        home_info = SportsDashboard._football_team_info(home, sport, show_score)
        odds = SportsDashboard._football_odds_info(competition.get("odds") or event.get("odds") or [])
        broadcasts = SportsDashboard._football_broadcast_label(competition.get("broadcasts") or event.get("broadcasts") or [])
        notes = SportsDashboard._football_event_note(event, competition)
        week_info = event.get("week") if isinstance(event.get("week"), Mapping) else {}
        week = SportsDashboard._lpl_int_value((week_info or {}).get("number"))
        week_label = SportsDashboard._football_week_label({"week": week_info}) if week_info else (f"WEEK {week}" if week else "")
        return {
            "sport": sport,
            "event_id": str(event.get("id") or competition.get("id") or "").strip(),
            "start": start,
            "state": SportsDashboard._football_state(status),
            "status_text": SportsDashboard._football_status_text(status, start),
            "team_a": away_info["display"],
            "team_b": home_info["display"],
            "team_a_code": away_info["code"],
            "team_b_code": home_info["code"],
            "team_a_zh": away_info["zh"],
            "team_b_zh": home_info["zh"],
            "team_a_name": away_info["name"],
            "team_b_name": home_info["name"],
            "team_a_logo": away_info["logo"],
            "team_b_logo": home_info["logo"],
            "team_a_rank": away_info["rank"],
            "team_b_rank": home_info["rank"],
            "record_a": away_info["record"],
            "record_b": home_info["record"],
            "wins_a": away_info["score"],
            "wins_b": home_info["score"],
            "winner_a": away_info["winner"],
            "winner_b": home_info["winner"],
            "period": SportsDashboard._lpl_int_value(status.get("period")),
            "clock": str(status.get("displayClock") or "").strip(),
            "possession": SportsDashboard._football_possession_code(situation, away_info["id"], home_info["id"], away_info["code"], home_info["code"]),
            "down_distance": SportsDashboard._football_down_distance(situation),
            "yard_line": SportsDashboard._football_yard_line(situation),
            "last_play": SportsDashboard._football_last_play(situation),
            "venue": str(((competition.get("venue") or {}).get("fullName") or (competition.get("venue") or {}).get("displayName") or "")).strip(),
            "city": SportsDashboard._football_venue_city(competition.get("venue") or {}),
            "neutral_site": bool(competition.get("neutralSite") or event.get("neutralSite")),
            "broadcast": broadcasts,
            "spread": odds.get("spread", ""),
            "over_under": odds.get("over_under", ""),
            "note": notes,
            "week": week,
            "week_label": week_label,
            "season_label": str(((event.get("season") or {}).get("slug") if isinstance(event.get("season"), Mapping) else "") or "").strip(),
        }

    @staticmethod
    def _football_team_info(competitor, sport, show_score):
        team = (competitor or {}).get("team") or {}
        raw_code = str(team.get("abbreviation") or team.get("shortDisplayName") or team.get("name") or "TBD").strip().upper() or "TBD"
        name = str(team.get("shortDisplayName") or team.get("displayName") or team.get("name") or raw_code).strip() or raw_code
        aliases = [
            raw_code,
            name,
            team.get("displayName"),
            team.get("shortDisplayName"),
            team.get("name"),
            team.get("location"),
            team.get("nickname"),
        ]
        code = SportsDashboard._football_normalized_team_code(raw_code, aliases, sport)
        score = SportsDashboard._lpl_int_value((competitor or {}).get("score")) if show_score else None
        rank = SportsDashboard._football_rank(competitor)
        display = SportsDashboard._football_display_team_name(code, name, sport, aliases)
        zh = ""
        if sport == "NCAA":
            zh = SportsDashboard._ncaa_display_school_name(code, name, aliases)
            display = zh
        return {
            "id": str(team.get("id") or (competitor or {}).get("id") or "").strip(),
            "code": code,
            "display": display,
            "zh": zh,
            "name": name,
            "logo": SportsDashboard._football_team_logo_url(team, code, sport),
            "score": score,
            "winner": SportsDashboard._espn_competitor_winner(competitor),
            "rank": rank,
            "record": SportsDashboard._football_record_label((competitor or {}).get("records") or []),
        }

    @staticmethod
    def _football_team_logo_url(team, code, sport):
        logo = SportsDashboard._espn_team_logo(team)
        if logo:
            return logo
        league = "ncaa" if str(sport or "").upper() == "NCAA" else "nfl"
        team_id = ""
        if isinstance(team, Mapping):
            team_id = str(team.get("id") or "").strip()
        return SportsDashboard._espn_cdn_team_logo_url(league, code, team_id)

    @staticmethod
    def _football_normalized_team_code(raw_code, aliases, sport):
        code = str(raw_code or "").strip().upper()
        if sport == "NFL":
            if code in NFL_TEAM_ZH_NAMES:
                return code
            for value in aliases or []:
                alias_code = NFL_TEAM_ALIAS_TO_CODE.get(_normalize_country_alias(value))
                if alias_code:
                    return alias_code
            return code[:4] if code else "TBD"
        if sport == "NCAA":
            if code in NCAA_TEAM_ZH_NAMES:
                return code
            for value in aliases or []:
                alias_code = NCAA_TEAM_ALIAS_TO_CODE.get(_normalize_country_alias(value))
                if alias_code:
                    return alias_code
            return code[:5] if code else "TBD"
        return code[:5] if sport == "NCAA" else code[:4]

    @staticmethod
    def _football_display_team_name(code, fallback="", sport="NFL", aliases=None, full=False):
        sport = str(sport or "").strip().upper()
        normalized = str(code or "").strip().upper()
        if sport == "NFL":
            if full and normalized in NFL_TEAM_ZH_FULL_NAMES:
                return NFL_TEAM_ZH_FULL_NAMES[normalized]
            if normalized in NFL_TEAM_ZH_NAMES:
                return NFL_TEAM_ZH_NAMES[normalized]
            for value in aliases or []:
                alias_code = NFL_TEAM_ALIAS_TO_CODE.get(_normalize_country_alias(value))
                if full and alias_code and alias_code in NFL_TEAM_ZH_FULL_NAMES:
                    return NFL_TEAM_ZH_FULL_NAMES[alias_code]
                if alias_code and alias_code in NFL_TEAM_ZH_NAMES:
                    return NFL_TEAM_ZH_NAMES[alias_code]
            return str(fallback or normalized or "TBD").strip() or "TBD"
        if sport == "NCAA":
            return SportsDashboard._ncaa_display_school_name(
                normalized,
                fallback=fallback,
                aliases=aliases,
                full=full,
            )
        if normalized:
            return normalized
        return str(fallback or normalized or "TBD").strip() or "TBD"

    @staticmethod
    def _ncaa_display_school_name(code, fallback="", aliases=None, full=False):
        normalized = str(code or "").strip().upper()
        if full and normalized in NCAA_TEAM_ZH_FULL_NAMES:
            return NCAA_TEAM_ZH_FULL_NAMES[normalized]
        if normalized in NCAA_TEAM_ZH_NAMES:
            return NCAA_TEAM_ZH_NAMES[normalized]
        for value in aliases or []:
            alias_code = NCAA_TEAM_ALIAS_TO_CODE.get(_normalize_country_alias(value))
            if full and alias_code and alias_code in NCAA_TEAM_ZH_FULL_NAMES:
                return NCAA_TEAM_ZH_FULL_NAMES[alias_code]
            if alias_code and alias_code in NCAA_TEAM_ZH_NAMES:
                return NCAA_TEAM_ZH_NAMES[alias_code]
        return str(fallback or normalized or "TBD").strip() or "TBD"

    @staticmethod
    def _football_rank(competitor):
        rank = SportsDashboard._lpl_int_value((competitor or {}).get("curatedRank", {}).get("current") if isinstance((competitor or {}).get("curatedRank"), Mapping) else None)
        if rank and rank < 100:
            return rank
        team = (competitor or {}).get("team") or {}
        rank = SportsDashboard._lpl_int_value(team.get("rank"))
        return rank if rank and rank < 100 else None

    @staticmethod
    def _football_record_label(records):
        for record in records or []:
            summary = str((record or {}).get("summary") or "").strip()
            if summary:
                return summary
        return ""

    @staticmethod
    def _football_state(status):
        status_type = (status or {}).get("type") or {}
        state = str(status_type.get("state") or "").strip().lower()
        name = str(status_type.get("name") or "").strip().lower()
        description = str(status_type.get("description") or "").strip().lower()
        detail = str(status_type.get("detail") or status_type.get("shortDetail") or "").strip().lower()
        if status_type.get("completed") is True or state == "post" or "final" in name or "final" in description:
            return "final"
        if state == "in" or "progress" in name or "halftime" in detail or "end of" in detail:
            return "live"
        return "scheduled"

    @staticmethod
    def _football_status_text(status, start):
        state = SportsDashboard._football_state(status)
        status_type = (status or {}).get("type") or {}
        if state == "live":
            period = SportsDashboard._lpl_int_value((status or {}).get("period"))
            clock = str((status or {}).get("displayClock") or "").strip()
            if period and clock:
                return f"Q{period} {clock}"
            return str(status_type.get("shortDetail") or status_type.get("detail") or "LIVE").strip() or "LIVE"
        if state == "final":
            return str(status_type.get("shortDetail") or status_type.get("detail") or status_type.get("description") or "Final").strip() or "Final"
        return SportsDashboard._format_time(start)

    @staticmethod
    def _football_possession_code(situation, away_id, home_id, away_code, home_code):
        possession = str((situation or {}).get("possession") or "").strip()
        if not possession:
            return ""
        if possession == str(away_id):
            return away_code
        if possession == str(home_id):
            return home_code
        return possession

    @staticmethod
    def _football_down_distance(situation):
        text = str((situation or {}).get("downDistanceText") or "").strip()
        if text:
            return text.upper()
        down = SportsDashboard._lpl_int_value((situation or {}).get("down"))
        distance = SportsDashboard._lpl_int_value((situation or {}).get("distance"))
        if down and distance is not None:
            return f"{SportsDashboard._ordinal_text(down).upper()} & {distance}"
        return ""

    @staticmethod
    def _football_yard_line(situation):
        return str((situation or {}).get("yardLineText") or (situation or {}).get("possessionText") or "").strip()

    @staticmethod
    def _football_last_play(situation):
        last_play = (situation or {}).get("lastPlay") or {}
        text = str(last_play.get("text") or (situation or {}).get("lastPlayText") or "").strip()
        return text[:86]

    @staticmethod
    def _football_venue_city(venue):
        address = (venue or {}).get("address") or {}
        city = str(address.get("city") or "").strip()
        state = str(address.get("state") or "").strip()
        return ", ".join(part for part in (city, state) if part)

    @staticmethod
    def _football_broadcast_label(broadcasts):
        names = []
        for broadcast in broadcasts or []:
            market = str((broadcast or {}).get("market") or "").lower()
            if market and market not in {"national", "us"}:
                continue
            for name in (broadcast or {}).get("names") or []:
                text = str(name or "").strip()
                if text and text not in names:
                    names.append(text)
            media = (broadcast or {}).get("media") or {}
            short = str(media.get("shortName") or "").strip()
            if short and short not in names:
                names.append(short)
        return " / ".join(names[:2])

    @staticmethod
    def _football_odds_info(odds):
        if not odds:
            return {"spread": "", "over_under": ""}
        item = odds[0] or {}
        spread = str(item.get("details") or "").strip()
        over_under = item.get("overUnder")
        if over_under is None:
            over_under = item.get("over_under")
        return {
            "spread": spread,
            "over_under": f"O/U {over_under}" if over_under not in {None, ""} else "",
        }

    @staticmethod
    def _football_event_note(event, competition):
        notes = (event or {}).get("notes") or (competition or {}).get("notes") or []
        for note in notes:
            headline = str((note or {}).get("headline") or (note or {}).get("type") or "").strip()
            if headline:
                return headline[:40]
        name = str((event or {}).get("name") or (event or {}).get("shortName") or "").strip()
        return name[:40]

    @staticmethod
    def _football_season_label(payload):
        season = (payload or {}).get("season") or {}
        return str(season.get("displayName") or season.get("year") or "").strip()

    @staticmethod
    def _football_week_label(payload):
        week = (payload or {}).get("week") or {}
        text = str(week.get("text") or "").strip()
        number = SportsDashboard._lpl_int_value(week.get("number"))
        if text:
            text_upper = text.upper()
            compact = re.sub(r"\s+", "", text_upper)
            if not number or compact not in {f"WEEK{number}", f"WK{number}"}:
                return text_upper
        if number:
            return f"WEEK {number}"
        return text.upper()

    @staticmethod
    def _football_header_week_label(card, event):
        label = str((card or {}).get("week_label") or (event or {}).get("week_label") or (event or {}).get("week") or "").strip()
        if not label:
            return ""
        number = SportsDashboard._lpl_int_value(label)
        if number is not None and re.fullmatch(r"\d+", label):
            return f"WEEK {number}"
        return label.upper()

    @staticmethod
    def _ordinal_text(value):
        value = int(value)
        if 10 <= value % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
        return f"{value}{suffix}"

    @staticmethod
    def _parse_pga_event(event, timezone_info, now):
        if not isinstance(event, Mapping):
            return None
        start = SportsDashboard._parse_start_time(event.get("date"), timezone_info)
        end = SportsDashboard._parse_start_time(event.get("endDate"), timezone_info)
        competitions = event.get("competitions") or []
        competition = competitions[0] if competitions else {}
        if competition:
            start = SportsDashboard._parse_start_time(competition.get("date"), timezone_info) or start
            end = SportsDashboard._parse_start_time(competition.get("endDate"), timezone_info) or end
        if not start:
            return None
        leaderboard = SportsDashboard._parse_pga_leaderboard(competition.get("competitors") or event.get("competitors") or [])
        if end and start <= now <= end + timedelta(hours=18):
            state = "live"
        elif end and now > end + timedelta(hours=18):
            state = "final"
        else:
            state = "scheduled"
        event_name = str(event.get("shortName") or event.get("name") or "PGA TOUR").strip() or "PGA TOUR"
        return {
            "sport": "PGA",
            "event_id": str(event.get("id") or competition.get("id") or "").strip(),
            "start": start,
            "end": end,
            "state": state,
            "status_text": SportsDashboard._pga_status_label(start, end, now),
            "name": SportsDashboard._pga_display_event_name(event_name),
            "name_en": event_name,
            "venue": SportsDashboard._pga_venue_label(competition, event),
            "leaderboard": leaderboard,
            "leader": SportsDashboard._pga_leader_summary(leaderboard),
        }

    @staticmethod
    def _pga_venue_label(competition, event=None):
        for source in (competition, event):
            venue = (source or {}).get("venue") if isinstance(source, Mapping) else {}
            if not isinstance(venue, Mapping):
                continue
            for key in ("fullName", "displayName", "name", "shortName"):
                value = str(venue.get(key) or "").strip()
                if value:
                    return value
        return ""

    @staticmethod
    def _parse_pga_calendar(leagues, timezone_info, now):
        events = []
        for league in leagues:
            for item in (league or {}).get("calendar") or []:
                start = SportsDashboard._parse_start_time(item.get("startDate"), timezone_info)
                end = SportsDashboard._parse_start_time(item.get("endDate"), timezone_info)
                if not start:
                    continue
                if end and now > end + timedelta(hours=18):
                    state = "final"
                elif start <= now <= (end or start + timedelta(days=4)) + timedelta(hours=18):
                    state = "live"
                else:
                    state = "scheduled"
                event_name = str(item.get("label") or "PGA TOUR").strip() or "PGA TOUR"
                events.append(
                    {
                        "sport": "PGA",
                        "event_id": str(item.get("id") or "").strip(),
                        "start": start,
                        "end": end,
                        "state": state,
                        "status_text": SportsDashboard._pga_status_label(start, end, now),
                        "name": SportsDashboard._pga_display_event_name(event_name),
                        "name_en": event_name,
                        "venue": "",
                        "leaderboard": [],
                        "leader": {},
                    }
                )
        return events

    @staticmethod
    def _pga_leader_summary(leaderboard):
        rows = list(leaderboard or [])
        if not rows:
            return {}
        leader = rows[0]
        summary = {
            "name": str(leader.get("name") or "Leader").strip(),
            "score": str(leader.get("score") or "E").strip() or "E",
            "round": leader.get("round"),
            "today": str(leader.get("today") or "").strip(),
            "strokes": str(leader.get("strokes") or "").strip(),
        }
        country = str(leader.get("country") or "").strip()
        if country:
            summary["country"] = country
        return summary

    @staticmethod
    def _parse_pga_leaderboard(competitors):
        rows = []
        for index, competitor in enumerate(competitors or []):
            athlete = (competitor or {}).get("athlete") or {}
            linescores = competitor.get("linescores") or []
            latest_round = SportsDashboard._pga_latest_round(linescores)
            country = SportsDashboard._pga_athlete_country_code(athlete, competitor)
            position = SportsDashboard._pga_position_value(competitor, index)
            rows.append(
                {
                    "position": position,
                    "position_label": SportsDashboard._pga_position_display_label(competitor, position),
                    "name": str(athlete.get("shortName") or athlete.get("displayName") or athlete.get("fullName") or "Player").strip(),
                    "country": country,
                    "score": str(competitor.get("score") or "E").strip() or "E",
                    "round": latest_round.get("round"),
                    "today": latest_round.get("today") or "",
                    "strokes": latest_round.get("strokes") or "",
                }
            )
        rows.sort(key=lambda item: item["position"])
        return rows[:8]

    @staticmethod
    def _pga_position_value(competitor, index):
        competitor = competitor or {}
        curated = competitor.get("curatedRank") if isinstance(competitor.get("curatedRank"), Mapping) else {}
        candidates = [
            curated.get("displayValue"),
            curated.get("current"),
            curated.get("rank"),
            curated.get("value"),
            competitor.get("displayRank"),
            competitor.get("rankDisplay"),
            competitor.get("currentRank"),
            competitor.get("rank"),
            competitor.get("order"),
        ]
        for value in candidates:
            parsed = SportsDashboard._lpl_int_value(value)
            if parsed is not None:
                return parsed
            match = re.search(r"\d+", str(value or ""))
            if match:
                return int(match.group(0))
        return index + 1

    @staticmethod
    def _pga_position_display_label(competitor, position):
        competitor = competitor or {}
        curated = competitor.get("curatedRank") if isinstance(competitor.get("curatedRank"), Mapping) else {}
        candidates = [
            curated.get("displayValue"),
            curated.get("abbreviation"),
            competitor.get("displayRank"),
            competitor.get("rankDisplay"),
            competitor.get("currentRankDisplay"),
            competitor.get("rank"),
        ]
        for value in candidates:
            label = SportsDashboard._pga_clean_position_label(value)
            if label:
                return label
        return f"P{position}"

    @staticmethod
    def _pga_clean_position_label(value):
        text = str(value or "").strip().upper()
        if not text:
            return ""
        text = text.replace(" ", "").replace("-", "")
        tied = re.fullmatch(r"T(\d+)", text)
        if tied:
            return f"T{int(tied.group(1))}"
        plain = re.fullmatch(r"(?:P|#)?(\d+)", text)
        if plain:
            return f"P{int(plain.group(1))}"
        return ""

    @staticmethod
    def _pga_athlete_country_code(athlete, competitor=None):
        for value in SportsDashboard._pga_country_candidates(athlete):
            code = SportsDashboard._pga_country_code(value)
            if code:
                return code
        for value in SportsDashboard._pga_country_candidates(competitor):
            code = SportsDashboard._pga_country_code(value)
            if code:
                return code
        return ""

    @staticmethod
    def _pga_country_candidates(source):
        if not isinstance(source, Mapping):
            return []
        values = []
        for key in ("country", "countryCode", "country_code", "nationality", "flag"):
            value = source.get(key)
            if isinstance(value, Mapping):
                values.extend(
                    value.get(field)
                    for field in ("abbreviation", "code", "countryCode", "displayName", "name", "alt")
                )
            else:
                values.append(value)
        return [str(value).strip() for value in values if str(value or "").strip()]

    @staticmethod
    def _pga_country_code(value):
        text = str(value or "").strip()
        if not text:
            return ""
        upper = text.upper().replace(".", "")
        common = {
            "UNITED STATES": "USA",
            "UNITED STATES OF AMERICA": "USA",
            "US": "USA",
            "U S": "USA",
            "NORTHERN IRELAND": "NIR",
            "ENGLAND": "ENG",
            "SCOTLAND": "SCO",
            "WALES": "WAL",
            "SOUTH AFRICA": "RSA",
            "REPUBLIC OF KOREA": "KOR",
            "SOUTH KOREA": "KOR",
        }
        if upper in common:
            return common[upper]
        if re.fullmatch(r"[A-Z]{2,3}", upper):
            return common.get(upper, upper)
        alias = FIFA_COUNTRY_ALIAS_TO_TLA.get(_normalize_country_alias(text))
        if alias:
            return alias
        return ""

    @staticmethod
    def _pga_country_display_label(value):
        code = SportsDashboard._pga_country_code(value) or str(value or "").strip().upper()
        if not code:
            return ""
        mapped = PGA_COUNTRY_ZH_NAME_OVERRIDES.get(code)
        if mapped:
            return mapped
        mapped = FIFA_TLA_TO_ZH_NAME.get(SportsDashboard._canonical_country_tla(code))
        return mapped or code

    @staticmethod
    def _pga_display_event_name(name):
        text = str(name or "").strip()
        key = _normalize_country_alias(text)
        if key in PGA_EVENT_ZH_NAMES:
            return PGA_EVENT_ZH_NAMES[key]
        return text or "PGA TOUR"

    @staticmethod
    def _pga_latest_round(linescores):
        best = {}
        for item in linescores or []:
            if item.get("displayValue") is None:
                continue
            period = SportsDashboard._lpl_int_value(item.get("period")) or 0
            if period >= (best.get("round") or 0):
                best = {
                    "round": period,
                    "today": str(item.get("displayValue") or "").strip(),
                    "strokes": str(item.get("value") or "").strip(),
                }
        return best

    @staticmethod
    def _select_offseason_hub(parsed, now):
        cards = [
            SportsDashboard._offseason_hub_card("MLB", (parsed or {}).get("mlb"), now),
            SportsDashboard._offseason_hub_card("WNBA", (parsed or {}).get("wnba"), now),
            SportsDashboard._offseason_hub_card("PGA", (parsed or {}).get("pga"), now),
            SportsDashboard._offseason_hub_card("NFL", (parsed or {}).get("nfl"), now),
            SportsDashboard._offseason_hub_card("NCAA", (parsed or {}).get("ncaa"), now),
        ]
        cards = [card for card in cards if card]
        live_cards = [card for card in cards if card.get("status") == "LIVE"]
        active_cards = [card for card in cards if card.get("status") != "BREAK"]
        pinned_urgent_next = False
        urgent_next_cards = [] if live_cards else [
            card for card in active_cards
            if SportsDashboard._offseason_hub_is_urgent_next_card(card, now)
        ]
        if urgent_next_cards:
            pinned_urgent_next = True
        pool = live_cards or urgent_next_cards or active_cards or cards
        pool = sorted(pool, key=lambda card: SportsDashboard._offseason_hub_priority_key(card, now))
        if not pool:
            primary = None
        elif pinned_urgent_next:
            primary = pool[0]
        else:
            minute_key = int(now.timestamp() // max(1, OFFSEASON_HUB_ROTATION_MINUTES * 60))
            primary = pool[minute_key % len(pool)]
        return {
            "primary": primary,
            "cards": cards,
            "rotation_pool": [card.get("sport") for card in pool],
            "updated_at": now.isoformat() if hasattr(now, "isoformat") else "",
        }

    @staticmethod
    def _offseason_hub_card(sport, parsed, now):
        events = list(((parsed or {}).get("events") or []))
        if sport in {"MLB", "WNBA", "NFL", "NCAA"}:
            live = sorted(
                [event for event in events if SportsDashboard._hub_event_state(event) == "live"],
                key=lambda item: item.get("start") or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            upcoming = sorted(
                [
                    event for event in events
                    if SportsDashboard._hub_event_state(event) == "scheduled"
                    and event.get("start")
                    and event["start"] >= now
                ],
                key=lambda item: item.get("start") or datetime.max.replace(tzinfo=timezone.utc),
            )
            recent = sorted(
                [
                    event for event in events
                    if SportsDashboard._hub_event_state(event) == "final"
                    or (
                        SportsDashboard._hub_event_state(event) != "live"
                        and event.get("start")
                        and event["start"] < now
                    )
                ],
                key=lambda item: item.get("start") or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            main = live[0] if live else (upcoming[0] if upcoming else (recent[0] if recent else None))
        else:
            live = sorted(
                [event for event in events if SportsDashboard._hub_event_state(event) == "live"],
                key=lambda item: item.get("start") or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            upcoming = sorted(
                [
                    event for event in events
                    if SportsDashboard._hub_event_state(event) == "scheduled"
                    and event.get("start")
                    and event["start"] >= now
                ],
                key=lambda item: item.get("start") or datetime.max.replace(tzinfo=timezone.utc),
            )
            recent = sorted(
                [event for event in events if SportsDashboard._hub_event_state(event) == "final" or (event.get("end") and event["end"] < now)],
                key=lambda item: item.get("start") or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            main = live[0] if live else (upcoming[0] if upcoming else (recent[0] if recent else None))
        status = "BREAK"
        if live:
            status = "LIVE"
        elif upcoming:
            status = "NEXT"
        elif recent:
            status = "RECENT"
        return {
            "sport": sport,
            "status": status,
            "main": main,
            "live": live,
            "upcoming": upcoming,
            "recent": recent,
            "events": events,
            "season_label": str(((parsed or {}).get("season_label") or "")).strip(),
            "week_label": str(((parsed or {}).get("week_label") or "")).strip(),
            "order": {"MLB": 0, "WNBA": 1, "PGA": 2, "NFL": 3, "NCAA": 4}.get(sport, 99),
        }

    @staticmethod
    def _offseason_hub_status_rank(status):
        return {"LIVE": 0, "NEXT": 1, "RECENT": 2, "BREAK": 3}.get(str(status or "").upper(), 4)

    @staticmethod
    def _offseason_hub_is_urgent_next_card(card, now):
        if str((card or {}).get("status") or "").upper() != "NEXT":
            return False
        seconds = SportsDashboard._offseason_hub_seconds_until_main(card, now)
        return 0 <= seconds <= int(OFFSEASON_HUB_URGENT_NEXT_WINDOW.total_seconds())

    @staticmethod
    def _offseason_hub_priority_key(card, now):
        status = str((card or {}).get("status") or "").upper()
        order = (card or {}).get("order", 99)
        if status == "LIVE":
            return (
                SportsDashboard._offseason_hub_status_rank(status),
                SportsDashboard._offseason_hub_seconds_since_main(card, now),
                order,
            )
        if status == "NEXT":
            return (
                SportsDashboard._offseason_hub_status_rank(status),
                SportsDashboard._offseason_hub_seconds_until_main(card, now),
                order,
            )
        if status == "RECENT":
            return (
                SportsDashboard._offseason_hub_status_rank(status),
                SportsDashboard._offseason_hub_seconds_since_main(card, now),
                order,
            )
        return (SportsDashboard._offseason_hub_status_rank(status), order, 0)

    @staticmethod
    def _offseason_hub_seconds_until_main(card, now):
        start = ((card or {}).get("main") or {}).get("start")
        if isinstance(start, datetime) and isinstance(now, datetime):
            return max(0, int((start - now).total_seconds()))
        return 10**12

    @staticmethod
    def _offseason_hub_seconds_since_main(card, now):
        start = ((card or {}).get("main") or {}).get("start")
        if isinstance(start, datetime) and isinstance(now, datetime):
            return max(0, int((now - start).total_seconds()))
        return 10**12

    @staticmethod
    def _hub_event_state(event):
        state = str((event or {}).get("state") or "").strip().lower()
        if state in NBA_LIVE_STATES or state in {"inprogress", "in_progress", "in-progress"}:
            return "live"
        if state in NBA_FINISHED_STATES or state in {"completed", "post", "finished"}:
            return "final"
        if state in {"pre", "preview"}:
            return "scheduled"
        return state or "scheduled"

    @staticmethod
    def _mlb_state(status):
        abstract = str((status or {}).get("abstractGameState") or "").strip().lower()
        coded = str((status or {}).get("codedGameState") or "").strip().lower()
        detailed = str((status or {}).get("detailedState") or "").strip().lower()
        if abstract == "live" or coded in {"i", "m"}:
            return "live"
        if abstract == "final" or "final" in detailed or coded == "f":
            return "final"
        return "scheduled"

    @staticmethod
    def _mlb_team_code(name):
        text = str(name or "").strip()
        if text in MLB_TEAM_CODES:
            return MLB_TEAM_CODES[text]
        alias_code = MLB_TEAM_ALIAS_TO_CODE.get(_normalize_country_alias(text))
        if alias_code:
            return alias_code
        words = [part for part in re.split(r"[^A-Za-z0-9]+", text.upper()) if part]
        if not words:
            return "TBD"
        if len(words) == 1:
            return words[0][:3]
        return "".join(word[0] for word in words[-2:])[:3]

    @staticmethod
    def _mlb_display_team_name(code, fallback="", full=False):
        normalized = str(code or "").strip().upper()
        if full and normalized in MLB_TEAM_ZH_FULL_NAMES:
            return MLB_TEAM_ZH_FULL_NAMES[normalized]
        if normalized in MLB_TEAM_ZH_NAMES:
            return MLB_TEAM_ZH_NAMES[normalized]
        return str(fallback or normalized or "MLB").strip() or "MLB"

    @staticmethod
    def _mlb_display_team_from_event(event, side, full=False):
        prefix = "team_a" if side == "a" else "team_b"
        raw_event = event or {}
        raw_team = str(raw_event.get(prefix) or "").strip()
        raw_code = str(raw_event.get(f"{prefix}_code") or "").strip()
        raw_name = str(raw_event.get(f"{prefix}_name") or "").strip()
        code = raw_code
        if not code or code.upper() == "TBD":
            for value in (raw_team, raw_name):
                candidate = SportsDashboard._mlb_team_code(value)
                if candidate and candidate != "TBD":
                    code = candidate
                    break
        fallback = raw_team or raw_name or raw_code or "TBD"
        return SportsDashboard._mlb_display_team_name(code or raw_team, fallback, full=full)

    @staticmethod
    def _mlb_team_logo_url(team, code):
        logo = SportsDashboard._espn_team_logo(team)
        if logo:
            return logo
        normalized = str(code or "").strip().upper()
        if not normalized or normalized == "TBD":
            return ""
        espn_code = MLB_ESPN_LOGO_CODES.get(normalized, normalized).lower()
        return f"https://a.espncdn.com/i/teamlogos/mlb/500/{espn_code}.png"

    @staticmethod
    def _mlb_record_label(record):
        if not isinstance(record, Mapping):
            return ""
        wins = SportsDashboard._lpl_int_value(record.get("wins"))
        losses = SportsDashboard._lpl_int_value(record.get("losses"))
        if wins is None or losses is None:
            return ""
        return f"{wins}-{losses}"

    @staticmethod
    def _mlb_pitcher_name(pitcher):
        if not isinstance(pitcher, Mapping):
            return ""
        full = str(pitcher.get("fullName") or "").strip()
        if not full:
            return ""
        parts = full.split()
        if len(parts) <= 1:
            return parts[0][:12]
        return f"{parts[0][0]}. {parts[-1]}"[:16]

    @staticmethod
    def _mlb_base_state(offense):
        bases = []
        if isinstance(offense.get("first"), Mapping):
            bases.append("1")
        if isinstance(offense.get("second"), Mapping):
            bases.append("2")
        if isinstance(offense.get("third"), Mapping):
            bases.append("3")
        return "".join(bases)

    @staticmethod
    def _mlb_line_score(line):
        if not isinstance(line, Mapping):
            return {}
        return {
            "runs": SportsDashboard._lpl_int_value(line.get("runs")),
            "hits": SportsDashboard._lpl_int_value(line.get("hits")),
            "errors": SportsDashboard._lpl_int_value(line.get("errors")),
        }

    @staticmethod
    def _pga_status_label(start, end, now):
        if start and now < start:
            return f"NEXT {start.strftime('%m/%d')}"
        if end and now > end + timedelta(hours=18):
            return "FINAL"
        if end:
            return f"THRU {end.strftime('%m/%d')}"
        return "LIVE"

    @staticmethod
    def _fallback_offseason_hub_data(timezone_info, now):
        start = now + timedelta(hours=2)
        return {
            "mlb": {
                "events": [
                    {
                        "sport": "MLB",
                        "event_id": "fallback-mlb",
                        "start": start,
                        "state": "scheduled",
                        "status_text": "Preview",
                        "team_a": SportsDashboard._mlb_display_team_name("LAD", "LAD"),
                        "team_b": SportsDashboard._mlb_display_team_name("SF", "SF"),
                        "team_a_code": "LAD",
                        "team_b_code": "SF",
                        "team_a_name": "Los Angeles Dodgers",
                        "team_b_name": "San Francisco Giants",
                        "team_a_logo": "https://a.espncdn.com/i/teamlogos/mlb/500/lad.png",
                        "team_b_logo": "https://a.espncdn.com/i/teamlogos/mlb/500/sf.png",
                        "wins_a": None,
                        "wins_b": None,
                        "record_a": "",
                        "record_b": "",
                        "probable_a": "",
                        "probable_b": "",
                        "venue": "MLB",
                        "inning_label": "",
                        "inning_state": "",
                        "outs": None,
                        "balls": None,
                        "strikes": None,
                        "bases": "",
                        "away_line": {},
                        "home_line": {},
                    }
                ]
            },
            "wnba": {
                "events": [
                    {
                        "sport": "WNBA",
                        "event_id": "fallback-wnba",
                        "start": start + timedelta(hours=1),
                        "state": "scheduled",
                        "status_text": "Preview",
                        "team_a": SportsDashboard._wnba_display_team_name("NY", "NY"),
                        "team_b": SportsDashboard._wnba_display_team_name("LV", "LV"),
                        "team_a_code": "NY",
                        "team_b_code": "LV",
                        "team_a_name": "New York Liberty",
                        "team_b_name": "Las Vegas Aces",
                        "team_a_logo": "https://a.espncdn.com/i/teamlogos/wnba/500/ny.png",
                        "team_b_logo": "https://a.espncdn.com/i/teamlogos/wnba/500/lv.png",
                        "wins_a": None,
                        "wins_b": None,
                        "record_a": "0-0",
                        "record_b": "0-0",
                        "venue": "WNBA",
                        "period": None,
                        "clock": "",
                        "period_scores_a": [],
                        "period_scores_b": [],
                    }
                ]
            },
            "pga": {
                "events": [
                    {
                        "sport": "PGA",
                        "event_id": "fallback-pga",
                        "start": start + timedelta(days=2),
                        "end": start + timedelta(days=5),
                        "state": "scheduled",
                        "status_text": "NEXT",
                        "name": "PGA TOUR",
                        "venue": "",
                        "leaderboard": [],
                    }
                ]
            },
            "nfl": {
                "events": [
                    {
                        "sport": "NFL",
                        "event_id": "fallback-nfl",
                        "start": start + timedelta(days=1),
                        "state": "scheduled",
                        "status_text": "Preview",
                        "team_a": SportsDashboard._football_display_team_name("KC", "KC", "NFL"),
                        "team_b": SportsDashboard._football_display_team_name("BUF", "BUF", "NFL"),
                        "team_a_code": "KC",
                        "team_b_code": "BUF",
                        "team_a_name": "Kansas City Chiefs",
                        "team_b_name": "Buffalo Bills",
                        "team_a_logo": "https://a.espncdn.com/i/teamlogos/nfl/500/kc.png",
                        "team_b_logo": "https://a.espncdn.com/i/teamlogos/nfl/500/buf.png",
                        "wins_a": None,
                        "wins_b": None,
                        "record_a": "0-0",
                        "record_b": "0-0",
                        "season_label": "2026 NFL",
                        "week_label": "WEEK 1",
                        "period": None,
                        "clock": "",
                        "possession": "",
                        "down_distance": "SEASON WATCH",
                        "yard_line": "KICKOFF TBA",
                        "last_play": "",
                        "venue": "NFL",
                        "city": "",
                        "neutral_site": False,
                        "broadcast": "",
                        "spread": "",
                        "over_under": "",
                        "note": "",
                    }
                ],
                "season_label": "2026 NFL",
                "week_label": "WEEK 1",
            },
            "ncaa": {
                "events": [
                    {
                        "sport": "NCAA",
                        "event_id": "fallback-ncaa",
                        "start": start + timedelta(days=2),
                        "state": "scheduled",
                        "status_text": "Preview",
                        "team_a": SportsDashboard._ncaa_display_school_name("TEX", "TEX"),
                        "team_b": SportsDashboard._ncaa_display_school_name("MICH", "MICH"),
                        "team_a_code": "TEX",
                        "team_b_code": "MICH",
                        "team_a_zh": SportsDashboard._ncaa_display_school_name("TEX", "TEX"),
                        "team_b_zh": SportsDashboard._ncaa_display_school_name("MICH", "MICH"),
                        "team_a_name": "Texas Longhorns",
                        "team_b_name": "Michigan Wolverines",
                        "team_a_logo": "https://a.espncdn.com/i/teamlogos/ncaa/500/251.png",
                        "team_b_logo": "https://a.espncdn.com/i/teamlogos/ncaa/500/130.png",
                        "team_a_rank": 12,
                        "team_b_rank": 7,
                        "wins_a": None,
                        "wins_b": None,
                        "record_a": "0-0",
                        "record_b": "0-0",
                        "season_label": "2026 College Football",
                        "week_label": "WEEK 1",
                        "period": None,
                        "clock": "",
                        "possession": "",
                        "down_distance": "RANKED WATCH",
                        "yard_line": "NEUTRAL SITE",
                        "last_play": "",
                        "venue": "College Football",
                        "city": "",
                        "neutral_site": True,
                        "broadcast": "",
                        "spread": "",
                        "over_under": "",
                        "note": "Kickoff Watch",
                    }
                ],
                "season_label": "2026 College Football",
                "week_label": "WEEK 1",
            },
        }

    def _write_offseason_hub_state(self, selected, now, source_state):
        primary = (selected or {}).get("primary") or {}
        main = primary.get("main") or {}
        has_live = str(primary.get("status") or "").upper() == "LIVE"
        live_until = self._offseason_hub_live_until(primary, now) if has_live else None
        payload = {
            "version": OFFSEASON_HUB_STATE_VERSION,
            "source_state": source_state,
            "status": primary.get("status"),
            "sport": primary.get("sport"),
            "has_live": has_live,
            "live_until": live_until.astimezone(timezone.utc).isoformat() if isinstance(live_until, datetime) else None,
            "event_id": main.get("event_id") or "",
            "rotation_pool": (selected or {}).get("rotation_pool") or [],
            "updated_at": now.isoformat() if hasattr(now, "isoformat") else datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._write_json_file(self._offseason_hub_live_state_path(), payload)
        except OSError as exc:
            logger.warning("Failed to write offseason hub live state: %s", exc)

    @staticmethod
    def _offseason_hub_live_until(primary, now):
        main = (primary or {}).get("main") or {}
        sport = str((primary or {}).get("sport") or main.get("sport") or "").strip().upper()
        start = main.get("start")
        end = main.get("end")
        if sport == "PGA":
            if isinstance(end, datetime):
                return end + OFFSEASON_HUB_PGA_POST_EVENT_WINDOW
            if isinstance(start, datetime):
                return start + timedelta(days=5)
            return now + timedelta(days=1)
        if isinstance(start, datetime):
            return start + OFFSEASON_HUB_DEFAULT_LIVE_WINDOW
        return now + OFFSEASON_HUB_DEFAULT_LIVE_WINDOW

    @staticmethod
    def _should_show_f1_panel(settings, nba_selected):
        mode = str((settings or {}).get("f1PanelMode") or "auto").strip().lower()
        if mode in {"off", "false", "disabled", "disable", "none", "nba"}:
            return False
        if mode in {"always", "on", "true", "f1"}:
            return True
        return bool((nba_selected or {}).get("offseason"))

    def _load_f1_events(self, settings, timezone_info):
        try:
            payload, source_state, _fetched_at = self._load_f1_jolpica_bundle(settings, timezone_info)
            parsed = self._parse_f1_jolpica_bundle(payload, timezone_info)
            return parsed, source_state
        except Exception as exc:
            logger.warning("F1 Jolpica fetch failed: %s", exc)
        return self._fallback_f1_data(timezone_info), "F1 FALLBACK"

    def _load_f1_jolpica_bundle(self, settings, timezone_info):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._f1_jolpica_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._f1_jolpica_cache_key(settings, timezone_info)
        force_refresh = self._force_refresh_requested(settings)
        has_compatible_cache = (
            cache.get("cache_key") == cache_key
            and isinstance(cache.get("schedule"), dict)
        )
        if (
            has_compatible_cache
            and not force_refresh
            and self._f1_jolpica_cache_is_fresh(cache, settings, timezone_info, now_utc)
        ):
            return cache, "JOLPICA CACHE", cache.get("fetched_at")

        if self._f1_jolpica_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache, "JOLPICA STALE", cache.get("fetched_at")
            return {}, "JOLPICA LIMIT", None

        try:
            payload = self._fetch_f1_jolpica_bundle(settings, cache_key, now_utc)
        except Exception:
            if has_compatible_cache:
                return cache, "JOLPICA STALE", cache.get("fetched_at")
            raise

        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write F1 Jolpica cache: %s", exc)
        return payload, "JOLPICA LIVE", payload.get("fetched_at")

    def _fetch_f1_jolpica_bundle(self, settings, cache_key, now_utc):
        base_url = self._f1_jolpica_base_url(settings)
        session = get_http_session()
        payloads = {}
        endpoints = {
            "schedule": "current.json",
            "results": "current/last/results.json",
            "driver_standings": "current/driverstandings.json",
            "constructor_standings": "current/constructorstandings.json",
        }
        try:
            for key, suffix in endpoints.items():
                response = session.get(
                    f"{base_url}/{suffix}",
                    headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"},
                    timeout=20,
                )
                response.raise_for_status()
                payloads[key] = response.json()
        finally:
            self._record_f1_jolpica_call(settings, now_utc)
        return {
            "version": F1_JOLPICA_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "base_url": base_url,
            **payloads,
        }

    def _f1_jolpica_cache_is_fresh(self, cache, settings, timezone_info, now_utc):
        cache_hours = self._int_setting(settings, "f1CacheHours", DEFAULT_F1_CACHE_HOURS, 1, 24)
        if not self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return False
        if self._f1_jolpica_cache_has_live_poll_candidate(cache, timezone_info, now_utc):
            return self._cache_is_fresh_seconds(cache, self._f1_live_refresh_seconds(settings), now_utc)
        return True

    def _f1_jolpica_cache_has_live_poll_candidate(self, cache, timezone_info, now_utc):
        try:
            parsed = self._parse_f1_jolpica_bundle(cache, timezone_info)
        except Exception as exc:
            logger.debug("F1 live cache candidate parse failed: %s", exc)
            return False
        return self._should_poll_f1_data(parsed, now_utc.astimezone(timezone_info))

    @staticmethod
    def _should_poll_f1_data(parsed, now):
        for race in (parsed or {}).get("races") or []:
            for session in race.get("sessions") or []:
                if SportsDashboard._is_f1_live_session(session, now):
                    return True
                start = session.get("start")
                if isinstance(start, datetime) and start - F1_SESSION_PREGAME_WINDOW <= now < start:
                    return True
        return False

    def _f1_jolpica_cache_key(self, settings, timezone_info):
        return "|".join(
            [
                F1_JOLPICA_STATE_VERSION,
                self._f1_jolpica_base_url(settings),
                getattr(timezone_info, "key", DEFAULT_TIMEZONE),
            ]
        )

    @staticmethod
    def _f1_jolpica_base_url(settings):
        value = str((settings or {}).get("f1JolpicaBaseUrl") or DEFAULT_F1_JOLPICA_BASE_URL).strip().rstrip("/")
        return value or DEFAULT_F1_JOLPICA_BASE_URL

    def _f1_jolpica_cache_path(self):
        return self._sports_dashboard_cache_dir() / "f1_jolpica.json"

    def _f1_jolpica_state_path(self):
        return self._sports_dashboard_cache_dir() / "f1_jolpica_state.json"

    def _f1_jolpica_calls_left(self, settings, now_utc):
        limit = self._int_setting(settings, "f1DailyLimit", DEFAULT_F1_DAILY_LIMIT, 1, 120)
        state = self._read_json_file(self._f1_jolpica_state_path())
        today = now_utc.date().isoformat()
        if state.get("date") != today:
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_f1_jolpica_call(self, settings, now_utc):
        path = self._f1_jolpica_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        count = 0
        if state.get("date") == today:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        try:
            self._write_json_file(
                path,
                {
                    "version": F1_JOLPICA_STATE_VERSION,
                    "date": today,
                    "count": count + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as exc:
            logger.warning("Failed to update F1 Jolpica request counter: %s", exc)

    @staticmethod
    def _parse_f1_jolpica_bundle(payload, timezone_info):
        races = [
            SportsDashboard._parse_f1_jolpica_race(race, timezone_info)
            for race in SportsDashboard._f1_races_from_payload((payload or {}).get("schedule") or payload)
        ]
        races = [race for race in races if race]
        races.sort(key=lambda item: item.get("race_start") or datetime.max.replace(tzinfo=timezone.utc))
        return {
            "races": races,
            "last_result": SportsDashboard._parse_f1_last_result((payload or {}).get("results"), timezone_info),
            "driver_standings": SportsDashboard._parse_f1_driver_standings((payload or {}).get("driver_standings")),
            "constructor_standings": SportsDashboard._parse_f1_constructor_standings((payload or {}).get("constructor_standings")),
        }

    @staticmethod
    def _f1_races_from_payload(payload):
        mr_data = (payload or {}).get("MRData") or {}
        race_table = mr_data.get("RaceTable") or {}
        races = race_table.get("Races") or []
        return races if isinstance(races, list) else []

    @staticmethod
    def _parse_f1_jolpica_race(race, timezone_info):
        if not isinstance(race, Mapping):
            return None
        circuit = race.get("Circuit") or {}
        location = circuit.get("Location") or {}
        race_start = SportsDashboard._parse_f1_date_time(race.get("date"), race.get("time"), timezone_info)
        sessions = []
        for source_key, label, title, duration in SportsDashboard._f1_session_specs():
            if source_key == "Race":
                start = race_start
            else:
                source = race.get(source_key) or {}
                start = SportsDashboard._parse_f1_date_time(source.get("date"), source.get("time"), timezone_info)
            if not start:
                continue
            sessions.append(
                {
                    "key": source_key,
                    "label": label,
                    "title": title,
                    "start": start,
                    "duration": duration,
                }
            )
        sessions.sort(key=lambda item: item["start"])
        return {
            "season": str(race.get("season") or ""),
            "round": str(race.get("round") or ""),
            "race_name": str(race.get("raceName") or "Formula 1").strip() or "Formula 1",
            "circuit_name": str(circuit.get("circuitName") or "").strip(),
            "locality": str(location.get("locality") or "").strip(),
            "country": str(location.get("country") or "").strip(),
            "race_start": race_start,
            "sessions": sessions,
        }

    @staticmethod
    def _f1_session_specs():
        return (
            ("FirstPractice", "FP1", "FP1", timedelta(hours=2)),
            ("SecondPractice", "FP2", "FP2", timedelta(hours=2)),
            ("ThirdPractice", "FP3", "FP3", timedelta(hours=2)),
            ("SprintQualifying", "SQ", "SPRINT Q", timedelta(hours=2)),
            ("SprintShootout", "SQ", "SPRINT Q", timedelta(hours=2)),
            ("Sprint", "SPRINT", "SPRINT", timedelta(hours=2)),
            ("Qualifying", "Q", "QUALIFYING", timedelta(hours=2)),
            ("Race", "RACE", "RACE", timedelta(hours=4)),
        )

    @staticmethod
    def _parse_f1_date_time(date_value, time_value, timezone_info):
        date_text = str(date_value or "").strip()
        if not date_text:
            return None
        time_text = str(time_value or "00:00:00Z").strip() or "00:00:00Z"
        if time_text.endswith("Z"):
            time_text = f"{time_text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(f"{date_text}T{time_text}")
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone_info)

    @staticmethod
    def _parse_f1_last_result(payload, timezone_info):
        races = SportsDashboard._f1_races_from_payload(payload)
        if not races:
            return None
        race = races[0]
        top = []
        for item in (race.get("Results") or [])[:5]:
            driver = item.get("Driver") or {}
            constructor = item.get("Constructor") or {}
            top.append(
                {
                    "position": SportsDashboard._lpl_int_value(item.get("position")) or len(top) + 1,
                    "driver_code": SportsDashboard._f1_driver_code(driver),
                    "driver_name": SportsDashboard._f1_driver_name(driver),
                    "constructor": str(constructor.get("name") or "").strip(),
                    "gap": SportsDashboard._f1_result_gap(item),
                    "status": str(item.get("status") or "").strip(),
                }
            )
        return {
            "round": str(race.get("round") or ""),
            "race_name": str(race.get("raceName") or "").strip(),
            "start": SportsDashboard._parse_f1_date_time(race.get("date"), race.get("time"), timezone_info),
            "top": top,
        }

    @staticmethod
    def _parse_f1_driver_standings(payload):
        standings = SportsDashboard._f1_standings_list(payload, "DriverStandings")
        result = []
        for item in standings[:5]:
            driver = item.get("Driver") or {}
            result.append(
                {
                    "position": SportsDashboard._lpl_int_value(item.get("position")) or len(result) + 1,
                    "driver_code": SportsDashboard._f1_driver_code(driver),
                    "points": str(item.get("points") or "0"),
                    "wins": str(item.get("wins") or "0"),
                }
            )
        return result

    @staticmethod
    def _parse_f1_constructor_standings(payload):
        standings = SportsDashboard._f1_standings_list(payload, "ConstructorStandings")
        result = []
        for item in standings[:5]:
            constructors = item.get("Constructor") or item.get("Constructors") or {}
            result.append(
                {
                    "position": SportsDashboard._lpl_int_value(item.get("position")) or len(result) + 1,
                    "constructor": str(constructors.get("name") or "").strip(),
                    "points": str(item.get("points") or "0"),
                    "wins": str(item.get("wins") or "0"),
                }
            )
        return result

    @staticmethod
    def _f1_standings_list(payload, key):
        mr_data = (payload or {}).get("MRData") or {}
        table = mr_data.get("StandingsTable") or {}
        lists = table.get("StandingsLists") or []
        if not lists:
            return []
        standings = (lists[0] or {}).get(key) or []
        return standings if isinstance(standings, list) else []

    @staticmethod
    def _f1_driver_code(driver):
        code = str((driver or {}).get("code") or (driver or {}).get("name_acronym") or "").strip().upper()
        if code:
            return code[:3]
        family_name = str((driver or {}).get("familyName") or (driver or {}).get("last_name") or "").strip().upper()
        if len(family_name) >= 3:
            return family_name[:3]
        given_name = str((driver or {}).get("givenName") or (driver or {}).get("first_name") or "").strip().upper()
        return (family_name or given_name or "DRV")[:3]

    @staticmethod
    def _f1_driver_name(driver):
        given = str((driver or {}).get("givenName") or (driver or {}).get("first_name") or "").strip()
        family = str((driver or {}).get("familyName") or (driver or {}).get("last_name") or "").strip()
        full = str((driver or {}).get("full_name") or "").strip()
        return full or " ".join(part for part in (given, family) if part) or SportsDashboard._f1_driver_code(driver)

    @staticmethod
    def _f1_result_gap(item):
        time_info = (item or {}).get("Time") or {}
        if time_info.get("time"):
            return str(time_info.get("time")).strip()
        status = str((item or {}).get("status") or "").strip()
        return status or "-"

    @staticmethod
    def _select_f1_events(data, now):
        races = (data or {}).get("races") or []
        sessions = []
        for race in races:
            for session in race.get("sessions") or []:
                entry = dict(session)
                entry["race"] = race
                sessions.append(entry)
        sessions.sort(key=lambda item: item["start"])
        live_sessions = [session for session in sessions if SportsDashboard._is_f1_live_session(session, now)]
        upcoming_sessions = [session for session in sessions if session.get("start") and session["start"] >= now]
        recent_sessions = sorted(
            [session for session in sessions if session.get("start") and session["start"] < now],
            key=lambda item: item["start"],
            reverse=True,
        )
        next_session = upcoming_sessions[0] if upcoming_sessions else None
        live_session = live_sessions[0] if live_sessions else None
        weekend_race = SportsDashboard._f1_weekend_race(races, now)
        next_race = SportsDashboard._f1_next_race(races, now)
        recent_race = SportsDashboard._f1_recent_race(races, now)
        main_race = (live_session or {}).get("race") or weekend_race or next_race or recent_race
        if live_session:
            status = "LIVE"
        elif next_session or next_race:
            status = "NEXT"
        elif (data or {}).get("last_result"):
            status = "RECENT"
        else:
            status = "BREAK"
        weekend_sessions = list((main_race or {}).get("sessions") or [])
        return {
            "status": status,
            "live_session": live_session,
            "next_session": next_session,
            "recent_session": recent_sessions[0] if recent_sessions else None,
            "main_race": main_race,
            "next_race": next_race,
            "recent_race": recent_race,
            "weekend_sessions": weekend_sessions,
            "last_result": (data or {}).get("last_result"),
            "driver_standings": (data or {}).get("driver_standings") or [],
            "constructor_standings": (data or {}).get("constructor_standings") or [],
            "leaderboard": [],
            "weather": None,
        }

    @staticmethod
    def _is_f1_live_session(session, now):
        start = (session or {}).get("start")
        duration = (session or {}).get("duration") or timedelta(hours=2)
        if not isinstance(start, datetime) or now is None:
            return False
        return start - F1_SESSION_PREGAME_WINDOW <= now < start + duration

    @staticmethod
    def _f1_weekend_race(races, now):
        for race in races or []:
            sessions = race.get("sessions") or []
            starts = [session.get("start") for session in sessions if isinstance(session.get("start"), datetime)]
            if not starts:
                continue
            start = min(starts) - timedelta(hours=12)
            end = max(starts) + F1_SESSION_RESULT_WINDOW
            if start <= now <= end:
                return race
        return None

    @staticmethod
    def _f1_next_race(races, now):
        for race in races or []:
            sessions = race.get("sessions") or []
            starts = [session.get("start") for session in sessions if isinstance(session.get("start"), datetime)]
            candidate = min(starts) if starts else race.get("race_start")
            if isinstance(candidate, datetime) and candidate >= now:
                return race
        return None

    @staticmethod
    def _f1_recent_race(races, now):
        recent = []
        for race in races or []:
            race_start = race.get("race_start")
            if isinstance(race_start, datetime) and race_start < now:
                recent.append(race)
        return sorted(recent, key=lambda item: item.get("race_start"), reverse=True)[0] if recent else None

    def _attach_f1_openf1_snapshot(self, selected, settings):
        if not selected or not self._bool_setting(settings, "f1OpenF1Enabled", True):
            return selected
        try:
            snapshot, source_state, _fetched_at = self._load_f1_openf1_snapshot(settings)
            parsed = self._parse_f1_openf1_snapshot(snapshot)
        except Exception as exc:
            logger.debug("OpenF1 enhancement failed: %s", exc)
            return selected
        if parsed.get("leaderboard"):
            selected["leaderboard"] = parsed["leaderboard"][:5]
            selected["openf1_source_state"] = source_state
        if parsed.get("weather"):
            selected["weather"] = parsed["weather"]
        return selected

    def _load_f1_openf1_snapshot(self, settings):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._f1_openf1_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._f1_openf1_cache_key(settings)
        force_refresh = self._force_refresh_requested(settings)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("snapshot"), dict)
        if (
            has_compatible_cache
            and not force_refresh
            and self._cache_is_fresh_seconds(cache, self._f1_live_refresh_seconds(settings), now_utc)
        ):
            return cache["snapshot"], "OPENF1 CACHE", cache.get("fetched_at")

        if self._f1_openf1_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache["snapshot"], "OPENF1 STALE", cache.get("fetched_at")
            return {}, "OPENF1 LIMIT", None

        try:
            payload = self._fetch_f1_openf1_snapshot(settings, cache_key, now_utc)
        except Exception:
            if has_compatible_cache:
                return cache["snapshot"], "OPENF1 STALE", cache.get("fetched_at")
            raise

        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write OpenF1 cache: %s", exc)
        return payload["snapshot"], "OPENF1 LIVE", payload.get("fetched_at")

    def _fetch_f1_openf1_snapshot(self, settings, cache_key, now_utc):
        base_url = self._f1_openf1_base_url(settings)
        session = get_http_session()
        endpoints = {
            "sessions": ("sessions", {"meeting_key": "latest"}),
            "drivers": ("drivers", {"session_key": "latest"}),
            "position": ("position", {"session_key": "latest"}),
            "intervals": ("intervals", {"session_key": "latest"}),
            "session_result": ("session_result", {"session_key": "latest"}),
            "weather": ("weather", {"session_key": "latest"}),
        }
        snapshot = {}
        try:
            for key, (path, params) in endpoints.items():
                response = session.get(
                    f"{base_url}/{path}",
                    params=params,
                    headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"},
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
                snapshot[key] = data if isinstance(data, list) else []
        finally:
            self._record_f1_openf1_call(settings, now_utc)
        return {
            "version": F1_OPENF1_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "base_url": base_url,
            "snapshot": snapshot,
        }

    @staticmethod
    def _parse_f1_openf1_snapshot(snapshot):
        drivers = {
            str((driver or {}).get("driver_number") or ""): driver
            for driver in (snapshot or {}).get("drivers") or []
            if str((driver or {}).get("driver_number") or "").strip()
        }
        positions = SportsDashboard._f1_latest_by_driver((snapshot or {}).get("position") or [], "position")
        intervals = SportsDashboard._f1_latest_by_driver((snapshot or {}).get("intervals") or [], "date")
        leaderboard = []
        for driver_number, position_item in positions.items():
            position = SportsDashboard._lpl_int_value(position_item.get("position"))
            if position is None:
                continue
            driver = drivers.get(str(driver_number), {})
            interval_item = intervals.get(str(driver_number), {})
            leaderboard.append(
                {
                    "position": position,
                    "driver_code": SportsDashboard._f1_driver_code(driver),
                    "driver_name": SportsDashboard._f1_driver_name(driver),
                    "team": str(driver.get("team_name") or "").strip(),
                    "team_color": SportsDashboard._f1_team_color(driver.get("team_colour")),
                    "gap": SportsDashboard._f1_openf1_gap(interval_item),
                    "interval": SportsDashboard._f1_openf1_interval(interval_item),
                }
            )
        if not leaderboard:
            leaderboard = SportsDashboard._f1_leaderboard_from_session_result(
                (snapshot or {}).get("session_result") or [],
                drivers,
            )
        leaderboard.sort(key=lambda item: item.get("position") or 99)
        return {
            "leaderboard": leaderboard,
            "weather": SportsDashboard._f1_latest_weather((snapshot or {}).get("weather") or []),
        }

    @staticmethod
    def _f1_latest_by_driver(items, fallback_sort_key):
        latest = {}
        for item in items or []:
            driver_number = str((item or {}).get("driver_number") or "").strip()
            if not driver_number:
                continue
            current = latest.get(driver_number)
            if current is None or SportsDashboard._f1_item_date(item, fallback_sort_key) >= SportsDashboard._f1_item_date(current, fallback_sort_key):
                latest[driver_number] = item
        return latest

    @staticmethod
    def _f1_item_date(item, fallback_key):
        parsed = SportsDashboard._parse_cached_utc((item or {}).get("date") or (item or {}).get("date_start"))
        if parsed:
            return parsed
        value = SportsDashboard._lpl_int_value((item or {}).get(fallback_key))
        return datetime.fromtimestamp(value or 0, tz=timezone.utc)

    @staticmethod
    def _f1_leaderboard_from_session_result(results, drivers):
        rows = []
        for item in results or []:
            position = SportsDashboard._lpl_int_value((item or {}).get("position"))
            if position is None:
                continue
            driver_number = str((item or {}).get("driver_number") or "").strip()
            driver = drivers.get(driver_number, {})
            rows.append(
                {
                    "position": position,
                    "driver_code": SportsDashboard._f1_driver_code(driver),
                    "driver_name": SportsDashboard._f1_driver_name(driver),
                    "team": str(driver.get("team_name") or "").strip(),
                    "team_color": SportsDashboard._f1_team_color(driver.get("team_colour")),
                    "gap": SportsDashboard._f1_session_result_gap(item, position),
                    "interval": "-",
                }
            )
        return rows

    @staticmethod
    def _f1_session_result_gap(item, position):
        gap = (item or {}).get("gap_to_leader")
        if gap not in (None, ""):
            return str(gap)
        if position == 1:
            return "LEADER"
        duration = (item or {}).get("duration")
        return str(duration) if duration not in (None, "") else "-"

    @staticmethod
    def _f1_openf1_gap(item):
        value = (item or {}).get("gap_to_leader")
        if value is None or value == "":
            return "LEADER"
        return str(value)

    @staticmethod
    def _f1_openf1_interval(item):
        value = (item or {}).get("interval")
        return "-" if value is None or value == "" else str(value)

    @staticmethod
    def _f1_latest_weather(items):
        if not items:
            return None
        latest = sorted(items, key=lambda item: SportsDashboard._f1_item_date(item, "date"), reverse=True)[0]
        air = latest.get("air_temperature")
        track = latest.get("track_temperature")
        rainfall = latest.get("rainfall")
        return {
            "air": air,
            "track": track,
            "rainfall": rainfall,
        }

    @staticmethod
    def _f1_team_color(value):
        text = str(value or "").strip().lstrip("#")
        if len(text) != 6:
            return COLORS["f1_accent"]
        try:
            return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))
        except ValueError:
            return COLORS["f1_accent"]

    def _f1_openf1_cache_key(self, settings):
        return "|".join([F1_OPENF1_STATE_VERSION, self._f1_openf1_base_url(settings)])

    @staticmethod
    def _f1_openf1_base_url(settings):
        value = str((settings or {}).get("f1OpenF1BaseUrl") or DEFAULT_F1_OPENF1_BASE_URL).strip().rstrip("/")
        return value or DEFAULT_F1_OPENF1_BASE_URL

    def _f1_openf1_cache_path(self):
        return self._sports_dashboard_cache_dir() / "f1_openf1_latest.json"

    def _f1_openf1_state_path(self):
        return self._sports_dashboard_cache_dir() / "f1_openf1_state.json"

    def _f1_openf1_calls_left(self, settings, now_utc):
        limit = self._int_setting(settings, "f1OpenF1DailyLimit", DEFAULT_F1_OPENF1_DAILY_LIMIT, 1, 240)
        state = self._read_json_file(self._f1_openf1_state_path())
        today = now_utc.date().isoformat()
        if state.get("date") != today:
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_f1_openf1_call(self, settings, now_utc):
        path = self._f1_openf1_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        count = 0
        if state.get("date") == today:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        try:
            self._write_json_file(
                path,
                {
                    "version": F1_OPENF1_STATE_VERSION,
                    "date": today,
                    "count": count + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as exc:
            logger.warning("Failed to update OpenF1 request counter: %s", exc)

    @staticmethod
    def _f1_live_refresh_seconds(settings):
        return SportsDashboard._int_setting(settings, "f1LiveRefreshSeconds", DEFAULT_F1_LIVE_REFRESH_SECONDS, 30, 900)

    @staticmethod
    def _fallback_f1_data(timezone_info):
        return {
            "races": [],
            "last_result": None,
            "driver_standings": [],
            "constructor_standings": [],
        }

    def _load_nba_scoreboard(self, settings, timezone_info):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._nba_scoreboard_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._nba_scoreboard_cache_key(settings, timezone_info, now_utc)
        force_refresh = self._force_refresh_requested(settings)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("scoreboard"), dict)
        if (
            has_compatible_cache
            and not force_refresh
            and self._nba_scoreboard_cache_is_fresh(cache, settings, timezone_info, now_utc)
        ):
            return cache["scoreboard"], "ESPN CACHE", cache.get("fetched_at")

        if self._nba_scoreboard_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache["scoreboard"], "ESPN STALE", cache.get("fetched_at")
            return {}, "ESPN LIMIT", None

        try:
            payload = self._fetch_nba_scoreboard_payload(settings, timezone_info, cache_key, now_utc)
        except Exception:
            if has_compatible_cache:
                return cache["scoreboard"], "ESPN STALE", cache.get("fetched_at")
            raise

        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write NBA scoreboard cache: %s", exc)
        return payload["scoreboard"], "ESPN LIVE", payload.get("fetched_at")

    def _fetch_nba_scoreboard_payload(self, settings, timezone_info, cache_key, now_utc):
        start_date, end_date = self._nba_scoreboard_date_range(settings, timezone_info, now_utc)
        url = self._nba_scoreboard_url(settings)
        session = get_http_session()
        try:
            response = session.get(
                url,
                params={
                    "dates": f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}",
                    "limit": "100",
                },
                headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"},
                timeout=20,
            )
        finally:
            self._record_nba_scoreboard_call(settings, now_utc)
        response.raise_for_status()
        return {
            "version": NBA_SCOREBOARD_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "range_start": start_date.isoformat(),
            "range_end": end_date.isoformat(),
            "scoreboard": response.json(),
        }

    @staticmethod
    def _parse_nba_espn_events(payload, timezone_info):
        parsed = []
        for event in (payload or {}).get("events") or []:
            competitions = event.get("competitions") or []
            competition = competitions[0] if competitions else {}
            start_time = SportsDashboard._parse_start_time(
                competition.get("date") or event.get("date"),
                timezone_info,
            )
            if not start_time:
                continue
            away, home = SportsDashboard._nba_competitors_by_side(competition.get("competitors") or [])
            if not away or not home:
                continue
            state = SportsDashboard._nba_event_state(event, competition)
            show_score = SportsDashboard._nba_state_has_score(state)
            team_a, team_a_name, team_a_code, team_a_logo, wins_a, periods_a, record_a, winner_a = SportsDashboard._nba_team_info(away, show_score)
            team_b, team_b_name, team_b_code, team_b_logo, wins_b, periods_b, record_b, winner_b = SportsDashboard._nba_team_info(home, show_score)
            series_wins_a, series_wins_b = SportsDashboard._nba_series_wins_by_side(event, competition, away, home)
            venue = competition.get("venue") or event.get("venue") or {}
            broadcasts = competition.get("broadcasts") or event.get("broadcasts") or []
            odds = SportsDashboard._football_odds_info(competition.get("odds") or event.get("odds") or [])
            parsed.append(
                {
                    "event_id": str(event.get("id") or competition.get("id") or "").strip(),
                    "start": start_time,
                    "state": state,
                    "team_a": team_a,
                    "team_b": team_b,
                    "team_a_name": team_a_name,
                    "team_b_name": team_b_name,
                    "team_a_code": team_a_code,
                    "team_b_code": team_b_code,
                    "team_a_source_aliases": SportsDashboard._nba_team_source_aliases(away, team_a, team_a_code),
                    "team_b_source_aliases": SportsDashboard._nba_team_source_aliases(home, team_b, team_b_code),
                    "team_a_logo": team_a_logo,
                    "team_b_logo": team_b_logo,
                    "wins_a": wins_a,
                    "wins_b": wins_b,
                    "winner_a": winner_a,
                    "winner_b": winner_b,
                    "record_a": record_a,
                    "record_b": record_b,
                    "series_wins_a": series_wins_a,
                    "series_wins_b": series_wins_b,
                    "period_scores_a": periods_a,
                    "period_scores_b": periods_b,
                    "status_text": SportsDashboard._nba_status_text(event, competition, start_time),
                    "period": SportsDashboard._lpl_int_value((competition.get("status") or {}).get("period")),
                    "block": SportsDashboard._nba_event_block(event, competition),
                    "venue": SportsDashboard._espn_venue_name(venue),
                    "city": SportsDashboard._football_venue_city(venue),
                    "broadcast": SportsDashboard._football_broadcast_label(broadcasts),
                    "spread": odds.get("spread", ""),
                    "over_under": odds.get("over_under", ""),
                }
            )
        parsed = sorted(parsed, key=lambda item: item["start"])
        unique = []
        seen = set()
        for event in parsed:
            key = event.get("event_id") or f"{event['start'].isoformat()}|{event['team_a']}|{event['team_b']}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(event)
        return SportsDashboard._normalize_nba_series_wins(unique)

    @staticmethod
    def _nba_competitors_by_side(competitors):
        away = None
        home = None
        for competitor in competitors or []:
            side = str((competitor or {}).get("homeAway") or "").strip().lower()
            if side == "away":
                away = competitor
            elif side == "home":
                home = competitor
        if away is None and len(competitors or []) > 0:
            away = competitors[0]
        if home is None and len(competitors or []) > 1:
            home = competitors[1]
        return away, home

    @staticmethod
    def _nba_team_info(competitor, show_score):
        team = (competitor or {}).get("team") or {}
        raw_code = str(
            team.get("abbreviation")
            or team.get("shortDisplayName")
            or team.get("name")
            or "TBD"
        ).strip().upper() or "TBD"
        name = str(team.get("shortDisplayName") or team.get("displayName") or raw_code).strip() or raw_code
        aliases = [
            raw_code,
            name,
            team.get("abbreviation"),
            team.get("shortDisplayName"),
            team.get("displayName"),
            team.get("name"),
            team.get("location"),
        ]
        code = SportsDashboard._nba_normalized_team_code(raw_code, aliases)
        display_name = SportsDashboard._nba_display_team_name(code, name, aliases)
        logo = SportsDashboard._espn_team_logo(team)
        score = SportsDashboard._lpl_int_value((competitor or {}).get("score")) if show_score else None
        periods = SportsDashboard._nba_period_scores(competitor) if show_score else []
        record = SportsDashboard._nba_record_label((competitor or {}).get("records") or [])
        winner = SportsDashboard._espn_competitor_winner(competitor)
        return display_name, name, code, logo, score, periods, record, winner

    @staticmethod
    def _espn_competitor_winner(competitor):
        value = (competitor or {}).get("winner")
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
        return None

    @staticmethod
    def _nba_normalized_team_code(raw_code, aliases=None):
        code = str(raw_code or "").strip().upper()
        if code in NBA_TEAM_ZH_NAMES:
            return code
        for value in aliases or []:
            alias_code = NBA_TEAM_ALIAS_TO_CODE.get(_normalize_country_alias(value))
            if alias_code:
                return alias_code
        return code[:4] if code else "TBD"

    @staticmethod
    def _nba_record_label(records):
        for record in records or []:
            summary = str((record or {}).get("summary") or "").strip()
            if summary:
                return summary
        return ""

    @staticmethod
    def _espn_team_logo(team):
        if not isinstance(team, Mapping):
            return ""
        logo = str(team.get("logo") or "").strip()
        if logo:
            return logo
        for item in team.get("logos") or []:
            href = str((item or {}).get("href") or "").strip()
            if href:
                return href
        return ""

    @staticmethod
    def _espn_cdn_team_logo_url(league, code="", team_id=""):
        normalized_league = str(league or "").strip().lower()
        normalized_code = str(code or "").strip().upper()
        alias_key = _normalize_country_alias(code)
        if normalized_league == "ncaa":
            normalized_code = NCAA_TEAM_ALIAS_TO_CODE.get(alias_key, normalized_code)
            identifier = str(team_id or "").strip() or NCAA_ESPN_LOGO_IDS.get(normalized_code) or normalized_code.lower()
        elif normalized_league == "wnba":
            normalized_code = WNBA_TEAM_ALIAS_TO_CODE.get(alias_key, normalized_code)
            identifier = WNBA_ESPN_LOGO_CODES.get(normalized_code, normalized_code.lower())
        elif normalized_league == "nfl":
            normalized_code = NFL_TEAM_ALIAS_TO_CODE.get(alias_key, normalized_code)
            identifier = NFL_ESPN_LOGO_CODES.get(normalized_code, normalized_code.lower())
        elif normalized_league == "mlb":
            normalized_code = MLB_TEAM_ALIAS_TO_CODE.get(alias_key, normalized_code)
            identifier = MLB_ESPN_LOGO_CODES.get(normalized_code, normalized_code).lower()
        else:
            identifier = normalized_code.lower()
        if not normalized_league or not identifier or identifier == "tbd":
            return ""
        return f"https://a.espncdn.com/i/teamlogos/{normalized_league}/500/{identifier}.png"

    @staticmethod
    def _espn_venue_name(venue):
        if not isinstance(venue, Mapping):
            return ""
        return str(venue.get("fullName") or venue.get("displayName") or venue.get("name") or "").strip()

    @staticmethod
    def _nba_team_source_aliases(competitor, display_name, code):
        team = (competitor or {}).get("team") or {}
        values = [
            display_name,
            code,
            team.get("abbreviation"),
            team.get("shortDisplayName"),
            team.get("displayName"),
            team.get("name"),
            team.get("location"),
        ]
        normalized_code = str(code or "").strip().upper()
        if normalized_code in NBA_ODDS_TEAM_ALIASES:
            values.extend(NBA_ODDS_TEAM_ALIASES[normalized_code])
        return [str(value).strip() for value in values if str(value or "").strip()]

    @staticmethod
    def _nba_series_wins_by_side(event, competition, away, home):
        series = (competition or {}).get("series") or (event or {}).get("series") or {}
        series_scores = {}
        for competitor in (series.get("competitors") if isinstance(series, dict) else []) or []:
            wins = SportsDashboard._lpl_int_value((competitor or {}).get("wins"))
            if wins is None:
                continue
            for key in SportsDashboard._nba_competitor_keys(competitor):
                series_scores[key] = wins

        def find_series_wins(competitor):
            for key in SportsDashboard._nba_competitor_keys(competitor):
                if key in series_scores:
                    return series_scores[key]
            return None

        series_a = find_series_wins(away)
        series_b = find_series_wins(home)
        if series_a is not None and series_b is not None:
            return series_a, series_b

        away_record = SportsDashboard._nba_record_pair((away or {}).get("record"))
        if away_record:
            series_a = away_record[0] if series_a is None else series_a
            series_b = away_record[1] if series_b is None else series_b

        home_record = SportsDashboard._nba_record_pair((home or {}).get("record"))
        if home_record:
            series_b = home_record[0] if series_b is None else series_b
            series_a = home_record[1] if series_a is None else series_a
        return series_a, series_b

    @staticmethod
    def _normalize_nba_series_wins(events):
        groups = {}
        for event in events or []:
            team_a_key = SportsDashboard._nba_event_series_team_key(event, "a")
            team_b_key = SportsDashboard._nba_event_series_team_key(event, "b")
            if not team_a_key or not team_b_key:
                continue
            group_key = tuple(sorted((team_a_key, team_b_key)))
            group = groups.setdefault(group_key, {"events": [], "scores": {}, "completed_wins": {}})
            group["events"].append(event)

            for team_key, value in (
                (team_a_key, event.get("series_wins_a")),
                (team_b_key, event.get("series_wins_b")),
            ):
                wins = SportsDashboard._lpl_int_value(value)
                if wins is None:
                    continue
                current = group["scores"].get(team_key)
                if current is None or wins > current:
                    group["scores"][team_key] = wins

            if not SportsDashboard._is_nba_finished_event(event):
                continue
            points_a = SportsDashboard._lpl_int_value(event.get("wins_a"))
            points_b = SportsDashboard._lpl_int_value(event.get("wins_b"))
            if points_a is None or points_b is None or points_a == points_b:
                continue
            winner_key = team_a_key if points_a > points_b else team_b_key
            group["completed_wins"][winner_key] = group["completed_wins"].get(winner_key, 0) + 1

        for group in groups.values():
            scores = dict(group["scores"])
            if not scores:
                continue
            for team_key, wins in group["completed_wins"].items():
                scores[team_key] = max(scores.get(team_key, 0), wins)
            for event in group["events"]:
                team_a_key = SportsDashboard._nba_event_series_team_key(event, "a")
                team_b_key = SportsDashboard._nba_event_series_team_key(event, "b")
                if team_a_key in scores and team_b_key in scores:
                    event["series_wins_a"] = scores[team_a_key]
                    event["series_wins_b"] = scores[team_b_key]
        return events

    @staticmethod
    def _nba_event_series_team_key(event, side):
        for key in (f"team_{side}_code", f"team_{side}_name", f"team_{side}"):
            value = str((event or {}).get(key) or "").strip().upper()
            if value and value != "TBD":
                return value
        return ""

    @staticmethod
    def _nba_competitor_keys(competitor):
        team = (competitor or {}).get("team") or {}
        values = [
            (competitor or {}).get("id"),
            (competitor or {}).get("uid"),
            team.get("id"),
            team.get("uid"),
            team.get("abbreviation"),
            team.get("shortDisplayName"),
            team.get("displayName"),
            team.get("name"),
        ]
        return {
            str(value).strip().upper()
            for value in values
            if str(value or "").strip()
        }

    @staticmethod
    def _nba_record_pair(value):
        text = str(value or "").strip()
        parts = text.split("-")
        if len(parts) != 2:
            return None
        first = SportsDashboard._lpl_int_value(parts[0])
        second = SportsDashboard._lpl_int_value(parts[1])
        if first is None or second is None:
            return None
        return first, second

    @staticmethod
    def _nba_display_team_name(code, fallback, aliases=None, full=False):
        normalized = str(code or "").strip().upper()
        if full and normalized in NBA_TEAM_ZH_FULL_NAMES:
            return NBA_TEAM_ZH_FULL_NAMES[normalized]
        if normalized in NBA_TEAM_ZH_NAMES:
            return NBA_TEAM_ZH_NAMES[normalized]
        for value in aliases or []:
            alias_code = NBA_TEAM_ALIAS_TO_CODE.get(_normalize_country_alias(value))
            if full and alias_code and alias_code in NBA_TEAM_ZH_FULL_NAMES:
                return NBA_TEAM_ZH_FULL_NAMES[alias_code]
            if alias_code and alias_code in NBA_TEAM_ZH_NAMES:
                return NBA_TEAM_ZH_NAMES[alias_code]
        return str(fallback or normalized or "TBD").strip() or "TBD"

    @staticmethod
    def _nba_display_team_from_event(event, side, full=False):
        prefix = "team_a" if side == "a" else "team_b"
        raw_event = event or {}
        raw_team = str(raw_event.get(prefix) or "").strip()
        raw_code = str(raw_event.get(f"{prefix}_code") or "").strip()
        raw_team_code = raw_team.upper() if re.fullmatch(r"[A-Za-z]{2,4}", raw_team) else ""
        code = raw_team_code or raw_code or raw_team
        fallback = raw_team or raw_code or "TBD"
        aliases = [
            raw_event.get(f"{prefix}_name"),
            raw_team,
            raw_code,
        ]
        return SportsDashboard._nba_display_team_name(code, fallback, aliases, full=full)

    @staticmethod
    def _nba_period_scores(competitor):
        scores = []
        for item in (competitor or {}).get("linescores") or (competitor or {}).get("lineScores") or []:
            value = item.get("value")
            if value is None:
                value = item.get("displayValue")
            parsed = SportsDashboard._lpl_int_value(value)
            if parsed is not None:
                scores.append(parsed)
        return scores

    @staticmethod
    def _nba_event_state(event, competition):
        status = (competition or {}).get("status") or (event or {}).get("status") or {}
        status_type = status.get("type") or {}
        state = str(status_type.get("state") or "").strip().lower()
        name = str(status_type.get("name") or "").strip().lower()
        description = str(status_type.get("description") or "").strip().lower()
        detail = str(status_type.get("detail") or status_type.get("shortDetail") or "").strip().lower()
        if status_type.get("completed") is True or state == "post" or "final" in name or "final" in description:
            return "completed"
        if state == "in" or "progress" in name or "halftime" in detail or "end of" in detail:
            return "inprogress"
        return "unstarted"

    @staticmethod
    def _nba_status_text(event, competition, start_time):
        status = (competition or {}).get("status") or (event or {}).get("status") or {}
        status_type = status.get("type") or {}
        state = SportsDashboard._nba_event_state(event, competition)
        if state == "inprogress":
            period = SportsDashboard._lpl_int_value(status.get("period"))
            clock = str(status.get("displayClock") or "").strip()
            if period and clock:
                return f"Q{period} {clock}"
            return str(status_type.get("shortDetail") or status_type.get("detail") or "LIVE").strip() or "LIVE"
        if state == "completed":
            return str(status_type.get("shortDetail") or status_type.get("detail") or status_type.get("description") or "Final").strip() or "Final"
        return SportsDashboard._format_time(start_time)

    @staticmethod
    def _nba_event_block(event, competition):
        season = (event or {}).get("season") or {}
        season_slug = str(season.get("slug") or season.get("type") or "").strip().replace("-", " ")
        if season_slug:
            return season_slug.upper()
        competition_type = (competition or {}).get("type") or {}
        value = str(competition_type.get("abbreviation") or competition_type.get("text") or "").strip()
        return value.upper() if value else "NBA"

    @staticmethod
    def _nba_state_has_score(state):
        return str(state or "").strip().lower() in NBA_LIVE_STATES.union(NBA_FINISHED_STATES)

    @staticmethod
    def _fallback_nba_events(timezone_info):
        rows = [
            (
                "2026-06-14T00:30:00+00:00",
                "completed",
                "NY",
                "SA",
                94,
                90,
                4,
                1,
                [],
                [],
                "Final",
                "POSTSEASON",
            ),
            (
                "2026-06-11T00:30:00+00:00",
                "completed",
                "NY",
                "SA",
                107,
                106,
                3,
                1,
                [],
                [],
                "Final",
                "POSTSEASON",
            ),
            (
                "2026-06-09T00:30:00+00:00",
                "completed",
                "SA",
                "NY",
                110,
                104,
                1,
                2,
                [],
                [],
                "Final",
                "POSTSEASON",
            ),
            (
                "2026-06-05T00:30:00+00:00",
                "completed",
                "SA",
                "NY",
                106,
                112,
                2,
                0,
                [25, 29, 24, 28],
                [28, 27, 31, 26],
                "Final",
                "POSTSEASON",
            ),
        ]
        events = []
        for (
            start,
            state,
            team_a,
            team_b,
            wins_a,
            wins_b,
            series_wins_a,
            series_wins_b,
            periods_a,
            periods_b,
            status_text,
            block,
        ) in rows:
            team_a_code = team_a
            team_b_code = team_b
            events.append(
                {
                    "event_id": "",
                    "start": datetime.fromisoformat(start).astimezone(timezone_info),
                    "state": state,
                    "team_a": SportsDashboard._nba_display_team_name(team_a_code, team_a_code),
                    "team_b": SportsDashboard._nba_display_team_name(team_b_code, team_b_code),
                    "team_a_name": team_a,
                    "team_b_name": team_b,
                    "team_a_code": team_a_code,
                    "team_b_code": team_b_code,
                    "team_a_source_aliases": [team_a_code, *NBA_ODDS_TEAM_ALIASES.get(team_a_code, ())],
                    "team_b_source_aliases": [team_b_code, *NBA_ODDS_TEAM_ALIASES.get(team_b_code, ())],
                    "team_a_logo": "",
                    "team_b_logo": "",
                    "wins_a": wins_a,
                    "wins_b": wins_b,
                    "series_wins_a": series_wins_a,
                    "series_wins_b": series_wins_b,
                    "period_scores_a": periods_a,
                    "period_scores_b": periods_b,
                    "status_text": status_text,
                    "period": None,
                    "block": block,
                }
            )
        return sorted(events, key=lambda item: item["start"])

    @staticmethod
    def _select_nba_events(events, now):
        live = [event for event in events if SportsDashboard._is_nba_live_event(event)]
        upcoming = [
            event for event in events
            if not SportsDashboard._is_nba_live_event(event)
            and not SportsDashboard._is_nba_finished_event(event)
            and not SportsDashboard._is_nba_series_decided_placeholder(event)
            and event["start"] >= now
        ]
        recent = sorted(
            [
                event for event in events
                if not SportsDashboard._is_nba_live_event(event)
                and (SportsDashboard._is_nba_finished_event(event) or event["start"] < now)
            ],
            key=lambda item: item["start"],
            reverse=True,
        )
        main = live[0] if live else (upcoming[0] if upcoming else (recent[0] if recent else None))
        offseason = SportsDashboard._is_nba_offseason_state(live, upcoming, recent, now)
        return {
            "live": live,
            "upcoming": upcoming,
            "recent": recent,
            "main": main,
            "next_season_event": upcoming[0] if upcoming else None,
            "offseason": offseason,
        }

    @staticmethod
    def _is_nba_live_event(event):
        return str((event or {}).get("state") or "").strip().lower() in NBA_LIVE_STATES

    @staticmethod
    def _is_nba_finished_event(event):
        return str((event or {}).get("state") or "").strip().lower() in NBA_FINISHED_STATES

    @staticmethod
    def _is_nba_series_decided_placeholder(event):
        if SportsDashboard._is_nba_live_event(event) or SportsDashboard._is_nba_finished_event(event):
            return False
        return max(
            SportsDashboard._lpl_int_value((event or {}).get("series_wins_a")) or 0,
            SportsDashboard._lpl_int_value((event or {}).get("series_wins_b")) or 0,
        ) >= 4

    @staticmethod
    def _is_nba_offseason_state(live, upcoming, recent, now):
        if live:
            return False
        next_event = upcoming[0] if upcoming else None
        latest_recent = recent[0] if recent else None
        if next_event:
            try:
                if next_event["start"] - now <= timedelta(days=NBA_OFFSEASON_NEXT_WINDOW_DAYS):
                    return False
            except (KeyError, TypeError):
                pass
        recent_block = str((latest_recent or {}).get("block") or "").upper()
        if "POST" in recent_block:
            return True
        month = getattr(now, "month", None)
        return month in {6, 7, 8, 9, 10}

    def _nba_scoreboard_cache_key(self, settings, timezone_info, now_utc):
        start_date, end_date = self._nba_scoreboard_date_range(settings, timezone_info, now_utc)
        return "|".join(
            [
                NBA_SCOREBOARD_STATE_VERSION,
                self._nba_scoreboard_url(settings),
                start_date.isoformat(),
                end_date.isoformat(),
                getattr(timezone_info, "key", DEFAULT_TIMEZONE),
            ]
        )

    @staticmethod
    def _nba_scoreboard_url(settings):
        value = str(settings.get("nbaScoreboardUrl") or DEFAULT_NBA_SCOREBOARD_URL).strip()
        return value or DEFAULT_NBA_SCOREBOARD_URL

    @staticmethod
    def _nba_scoreboard_date_range(settings, timezone_info, now_utc):
        local_date = now_utc.astimezone(timezone_info).date()
        lookback = SportsDashboard._int_setting(settings, "nbaLookbackDays", DEFAULT_NBA_LOOKBACK_DAYS, 0, 30)
        lookahead = SportsDashboard._int_setting(settings, "nbaLookaheadDays", DEFAULT_NBA_LOOKAHEAD_DAYS, 1, 240)
        return local_date - timedelta(days=lookback), local_date + timedelta(days=lookahead)

    def _nba_scoreboard_cache_path(self):
        return self._sports_dashboard_cache_dir() / "nba_scoreboard.json"

    def _nba_scoreboard_state_path(self):
        return self._sports_dashboard_cache_dir() / "nba_scoreboard_state.json"

    def _nba_scoreboard_cache_is_fresh(self, cache, settings, timezone_info, now_utc):
        cache_hours = self._int_setting(settings, "nbaCacheHours", DEFAULT_NBA_CACHE_HOURS, 1, 12)
        if not self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return False
        if self._nba_scoreboard_cache_has_live_poll_candidate(cache, timezone_info, now_utc):
            return self._cache_is_fresh_seconds(cache, self._nba_live_refresh_seconds(settings), now_utc)
        return True

    def _nba_scoreboard_cache_has_live_poll_candidate(self, cache, timezone_info, now_utc):
        try:
            events = self._parse_nba_espn_events(cache.get("scoreboard") or {}, timezone_info)
        except Exception as exc:
            logger.debug("NBA live cache candidate parse failed: %s", exc)
            return False
        return self._should_poll_nba_live_scoreboard(events, now_utc.astimezone(timezone_info))

    @staticmethod
    def _should_poll_nba_live_scoreboard(events, now):
        return any(SportsDashboard._is_nba_live_poll_candidate(event, now) for event in events or [])

    @staticmethod
    def _is_nba_live_poll_candidate(event, now):
        if SportsDashboard._is_nba_live_event(event):
            return True
        if SportsDashboard._is_nba_finished_event(event):
            return False
        start = (event or {}).get("start")
        if not isinstance(start, datetime) or now is None:
            return False
        return start - NBA_LIVE_PREGAME_WINDOW <= now < start + NBA_INFERRED_LIVE_WINDOW

    @staticmethod
    def _nba_live_refresh_seconds(settings):
        return SportsDashboard._int_setting(settings, "nbaLiveRefreshSeconds", DEFAULT_NBA_LIVE_REFRESH_SECONDS, 30, 900)

    def _nba_scoreboard_calls_left(self, settings, now_utc):
        limit = self._int_setting(settings, "nbaDailyLimit", DEFAULT_NBA_DAILY_LIMIT, 1, 120)
        state = self._read_json_file(self._nba_scoreboard_state_path())
        today = now_utc.date().isoformat()
        if state.get("date") != today:
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_nba_scoreboard_call(self, settings, now_utc):
        path = self._nba_scoreboard_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        count = 0
        if state.get("date") == today:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        payload = {"date": today, "count": count + 1}
        try:
            self._write_json_file(path, payload)
        except OSError as exc:
            logger.warning("Failed to update NBA scoreboard request counter: %s", exc)

    def _attach_nba_odds(self, events, settings, device_config, timezone_info):
        if not events or not self._bool_setting(settings, "nbaOddsEnabled", True):
            return events
        provider = self._nba_odds_provider(settings, device_config)
        api_key = self._nba_odds_api_key(settings, device_config, provider)
        if not api_key:
            return events
        try:
            odds_events, _source_state, _fetched_at = self._load_nba_odds(settings, api_key, provider)
            if not odds_events:
                return events
            return self._merge_nba_odds(events, odds_events, timezone_info, settings)
        except Exception as exc:
            logger.warning("NBA odds overlay failed: %s", _safe_exception_text(exc))
            return events

    @staticmethod
    def _nba_odds_api_key(settings, device_config=None, provider=None):
        provider = provider or SportsDashboard._nba_odds_provider(settings, device_config)
        if provider == "oddsapiio":
            key_names = ("nbaOddsApiIoKey", "oddsApiIoKey")
            env_names = (
                "NBA_ODDS_API_IO_KEY",
                "NBA_ODDSAPI_IO_KEY",
                "Odds_API_IO_KEY",
                "ODDS_API_IO_KEY",
                "ODDSAPI_IO_KEY",
                "nbaOddsApiIoKey",
                "oddsApiIoKey",
            )
        else:
            key_names = ("nbaTheOddsApiKey", "nbaOddsApiKey", "theOddsApiKey", "oddsApiKey")
            env_names = (
                "NBA_THE_ODDS_API_KEY",
                "NBA_ODDS_API_KEY",
                "THE_ODDS_API_KEY",
                "ODDS_API_KEY",
                "nbaTheOddsApiKey",
                "nbaOddsApiKey",
                "theOddsApiKey",
                "oddsApiKey",
            )
        for key_name in key_names:
            value = str(settings.get(key_name) or "").strip()
            if value:
                return value
        if device_config and hasattr(device_config, "get_config"):
            for key_name in key_names:
                value = str(device_config.get_config(key_name, "") or "").strip()
                if value:
                    return value
        if device_config and hasattr(device_config, "load_env_key"):
            for env_name in env_names:
                value = str(device_config.load_env_key(env_name) or "").strip()
                if value:
                    return value
        for env_name in env_names:
            value = str(os.environ.get(env_name) or "").strip()
            if value:
                return value
        return ""

    def _load_nba_odds(self, settings, api_key, provider=None):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._nba_odds_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._nba_odds_cache_key(settings, api_key, provider)
        force_refresh = self._force_refresh_requested(settings)
        cache_hours = self._int_setting(settings, "nbaOddsCacheHours", DEFAULT_NBA_ODDS_CACHE_HOURS, 1, 24)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("odds_events"), list)
        if has_compatible_cache and not force_refresh and self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return cache["odds_events"], "NBA ODDS CACHE", cache.get("fetched_at")

        if self._nba_odds_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache["odds_events"], "NBA ODDS STALE", cache.get("fetched_at")
            return [], "NBA ODDS LIMIT", None

        try:
            payload = self._fetch_nba_odds_payload(settings, api_key, cache_key, now_utc, provider)
        except Exception:
            if has_compatible_cache:
                return cache["odds_events"], "NBA ODDS STALE", cache.get("fetched_at")
            raise

        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write NBA odds cache: %s", exc)
        return payload["odds_events"], "NBA ODDS LIVE", payload.get("fetched_at")

    def _fetch_nba_odds_payload(self, settings, api_key, cache_key, now_utc, provider=None):
        if (provider or self._nba_odds_provider(settings)) == "oddsapiio":
            return self._fetch_nba_odds_api_io_payload(settings, api_key, cache_key, now_utc)
        return self._fetch_nba_the_odds_api_payload(settings, api_key, cache_key, now_utc)

    def _fetch_nba_the_odds_api_payload(self, settings, api_key, cache_key, now_utc):
        sport_key = self._nba_odds_sport_key(settings)
        session = get_http_session()
        try:
            response = session.get(
                f"{THE_ODDS_API_BASE_URL}/sports/{sport_key}/odds/",
                params={
                    "apiKey": api_key,
                    "regions": self._nba_odds_regions(settings),
                    "markets": self._nba_odds_markets(settings),
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                },
                headers={"Accept": "application/json"},
                timeout=25,
            )
        finally:
            self._record_nba_odds_call(settings, now_utc)
        response.raise_for_status()
        odds_events = response.json()
        if not isinstance(odds_events, list):
            odds_events = []
        return {
            "version": NBA_ODDS_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "provider": "theoddsapi",
            "sport_key": sport_key,
            "regions": self._nba_odds_regions(settings),
            "markets": self._nba_odds_markets(settings),
            "odds_events": odds_events,
        }

    def _fetch_nba_odds_api_io_payload(self, settings, api_key, cache_key, now_utc):
        events = self._nba_odds_api_io_get_json(
            "/events",
            {
                "apiKey": api_key,
                "sport": self._nba_odds_api_io_sport(settings),
                "league": self._nba_odds_api_io_league(settings),
                "status": self._nba_odds_api_io_status(settings),
                "limit": str(self._nba_odds_api_io_limit(settings)),
            },
            settings,
            now_utc,
        )
        if not isinstance(events, list):
            events = []
        event_ids = [str(item.get("id")) for item in events if item.get("id") is not None][:10]
        odds_events = []
        if event_ids:
            odds_events = self._nba_odds_api_io_get_json(
                "/odds/multi",
                {
                    "apiKey": api_key,
                    "eventIds": ",".join(event_ids),
                    "bookmakers": self._nba_odds_bookmakers(settings),
                },
                settings,
                now_utc,
            )
            if not isinstance(odds_events, list):
                odds_events = []
        return {
            "version": NBA_ODDS_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "provider": "oddsapiio",
            "sport": self._nba_odds_api_io_sport(settings),
            "league": self._nba_odds_api_io_league(settings),
            "status": self._nba_odds_api_io_status(settings),
            "bookmakers": self._nba_odds_bookmakers(settings),
            "events": events,
            "odds_events": odds_events,
        }

    def _nba_odds_api_io_get_json(self, path, params, settings, now_utc):
        if self._nba_odds_calls_left(settings, now_utc) <= 0:
            raise RuntimeError("NBA odds daily request limit reached")
        session = get_http_session()
        try:
            response = session.get(
                f"{ODDS_API_IO_BASE_URL}{path}",
                params=params,
                headers={"Accept": "application/json"},
                timeout=25,
            )
        finally:
            self._record_nba_odds_call(settings, now_utc)
        response.raise_for_status()
        return response.json()

    def _nba_odds_cache_key(self, settings, api_key, provider=None):
        token_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:10]
        return "|".join(
            [
                NBA_ODDS_STATE_VERSION,
                provider or self._nba_odds_provider(settings),
                self._nba_odds_sport_key(settings),
                self._nba_odds_api_io_sport(settings),
                self._nba_odds_api_io_league(settings),
                self._nba_odds_bookmakers(settings).lower(),
                self._nba_odds_regions(settings).lower(),
                self._nba_odds_markets(settings).lower(),
                token_hash,
            ]
        )

    @staticmethod
    def _nba_odds_provider(settings, device_config=None):
        provider = str(settings.get("nbaOddsProvider") or "").strip().lower()
        provider = provider.replace("-", "").replace("_", "")
        if provider in {"oddsapiio", "oddsio"}:
            return "oddsapiio"
        if provider:
            return "theoddsapi"
        if SportsDashboard._nba_odds_api_io_key_available(settings, device_config):
            return "oddsapiio"
        return "theoddsapi"

    @staticmethod
    def _nba_odds_api_io_key_available(settings, device_config=None):
        key_names = ("nbaOddsApiIoKey", "oddsApiIoKey")
        for key_name in key_names:
            if str(settings.get(key_name) or "").strip():
                return True
        if device_config and hasattr(device_config, "get_config"):
            for key_name in key_names:
                if str(device_config.get_config(key_name, "") or "").strip():
                    return True
        env_names = (
            "NBA_ODDS_API_IO_KEY",
            "NBA_ODDSAPI_IO_KEY",
            "Odds_API_IO_KEY",
            "ODDS_API_IO_KEY",
            "ODDSAPI_IO_KEY",
            "nbaOddsApiIoKey",
            "oddsApiIoKey",
        )
        if device_config and hasattr(device_config, "load_env_key"):
            for env_name in env_names:
                if str(device_config.load_env_key(env_name) or "").strip():
                    return True
        for env_name in env_names:
            if str(os.environ.get(env_name) or "").strip():
                return True
        return False

    @staticmethod
    def _nba_odds_sport_key(settings):
        sport_key = str(settings.get("nbaOddsSportKey") or DEFAULT_NBA_ODDS_SPORT_KEY).strip()
        return sport_key or DEFAULT_NBA_ODDS_SPORT_KEY

    @staticmethod
    def _nba_odds_api_io_sport(settings):
        sport = str(settings.get("nbaOddsApiIoSport") or DEFAULT_NBA_ODDS_API_IO_SPORT).strip()
        return sport or DEFAULT_NBA_ODDS_API_IO_SPORT

    @staticmethod
    def _nba_odds_api_io_league(settings):
        league = str(settings.get("nbaOddsApiIoLeague") or DEFAULT_NBA_ODDS_API_IO_LEAGUE).strip()
        league = league or DEFAULT_NBA_ODDS_API_IO_LEAGUE
        return ODDS_API_IO_LEAGUE_ALIASES.get(league, league)

    @staticmethod
    def _nba_odds_api_io_status(settings):
        status = str(settings.get("nbaOddsApiIoStatus") or DEFAULT_NBA_ODDS_API_IO_STATUS).strip()
        return status or DEFAULT_NBA_ODDS_API_IO_STATUS

    @staticmethod
    def _nba_odds_api_io_limit(settings):
        return SportsDashboard._int_setting(settings, "nbaOddsApiIoLimit", DEFAULT_NBA_ODDS_API_IO_LIMIT, 1, 10)

    @staticmethod
    def _nba_odds_regions(settings):
        regions = str(settings.get("nbaOddsRegions") or DEFAULT_NBA_ODDS_REGIONS).strip()
        return regions or DEFAULT_NBA_ODDS_REGIONS

    @staticmethod
    def _nba_odds_markets(settings):
        markets = str(settings.get("nbaOddsMarkets") or DEFAULT_NBA_ODDS_MARKETS).strip()
        return markets or DEFAULT_NBA_ODDS_MARKETS

    @staticmethod
    def _nba_odds_bookmakers(settings):
        bookmakers = str(settings.get("nbaOddsBookmakers") or settings.get("nbaOddsBookmaker") or DEFAULT_NBA_ODDS_BOOKMAKERS).strip()
        return bookmakers or DEFAULT_NBA_ODDS_BOOKMAKERS

    @staticmethod
    def _nba_odds_preferred_bookmakers(settings):
        raw = SportsDashboard._nba_odds_bookmakers(settings)
        return [
            SportsDashboard._normalize_odds_team_name(item)
            for item in raw.replace(";", ",").split(",")
            if item.strip()
        ]

    def _nba_odds_cache_path(self):
        return self._sports_dashboard_cache_dir() / "nba_odds.json"

    def _nba_odds_state_path(self):
        return self._sports_dashboard_cache_dir() / "nba_odds_state.json"

    def _nba_odds_calls_left(self, settings, now_utc):
        limit = self._int_setting(settings, "nbaOddsDailyLimit", DEFAULT_NBA_ODDS_DAILY_LIMIT, 1, 30)
        state = self._read_json_file(self._nba_odds_state_path())
        today = now_utc.date().isoformat()
        if state.get("date") != today:
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_nba_odds_call(self, settings, now_utc):
        path = self._nba_odds_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        count = 0
        if state.get("date") == today:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        try:
            self._write_json_file(
                path,
                {
                    "version": NBA_ODDS_STATE_VERSION,
                    "date": today,
                    "count": count + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as exc:
            logger.warning("Failed to write NBA odds request counter: %s", exc)

    def _merge_nba_odds(self, events, odds_events, timezone_info, settings):
        offers = self._nba_odds_offers(odds_events, timezone_info, settings)
        if not offers:
            return events
        enriched = []
        for event in events:
            next_event = dict(event)
            matched = self._match_nba_odds_offer(event, offers)
            if matched:
                offer, reversed_order = matched
                next_event["odds"] = self._nba_event_odds_from_offer(offer, reversed_order)
            enriched.append(next_event)
        return enriched

    def _nba_odds_offers(self, odds_events, timezone_info, settings):
        preferred_bookmakers = self._nba_odds_preferred_bookmakers(settings)
        offers = []
        for item in odds_events or []:
            home_team = str(item.get("home_team") or item.get("home") or "").strip()
            away_team = str(item.get("away_team") or item.get("away") or "").strip()
            if not home_team or not away_team:
                continue
            odds = self._pick_worldcup_h2h_odds(item, preferred_bookmakers)
            if not odds:
                continue
            start = self._parse_start_time(item.get("commence_time") or item.get("date"), timezone_info)
            offers.append(
                {
                    "start": start,
                    "home_team": home_team,
                    "away_team": away_team,
                    **odds,
                }
            )
        return offers

    @staticmethod
    def _match_nba_odds_offer(event, offers):
        team_a_aliases = SportsDashboard._nba_event_team_aliases(event, "a")
        team_b_aliases = SportsDashboard._nba_event_team_aliases(event, "b")
        for offer in offers:
            if not SportsDashboard._nba_odds_time_matches(event.get("start"), offer.get("start")):
                continue
            home_matches_a = SportsDashboard._nba_team_matches_aliases(offer.get("home_team"), team_a_aliases)
            away_matches_b = SportsDashboard._nba_team_matches_aliases(offer.get("away_team"), team_b_aliases)
            if home_matches_a and away_matches_b:
                return offer, False
            home_matches_b = SportsDashboard._nba_team_matches_aliases(offer.get("home_team"), team_b_aliases)
            away_matches_a = SportsDashboard._nba_team_matches_aliases(offer.get("away_team"), team_a_aliases)
            if home_matches_b and away_matches_a:
                return offer, True
        return None

    @staticmethod
    def _nba_event_odds_from_offer(offer, reversed_order):
        if reversed_order:
            team_a = offer.get("away_odds") or ""
            team_b = offer.get("home_odds") or ""
        else:
            team_a = offer.get("home_odds") or ""
            team_b = offer.get("away_odds") or ""
        return {
            "team_a": team_a,
            "team_b": team_b,
            "bookmaker": offer.get("bookmaker") or "",
        }

    @staticmethod
    def _nba_odds_time_matches(event_start, odds_start):
        if not event_start or not odds_start:
            return True
        try:
            event_utc = event_start.astimezone(timezone.utc)
            odds_utc = odds_start.astimezone(timezone.utc)
        except (AttributeError, ValueError):
            return True
        return abs((event_utc - odds_utc).total_seconds()) <= 12 * 60 * 60

    @staticmethod
    def _nba_event_team_aliases(event, side):
        aliases = []
        for key in (f"team_{side}", f"team_{side}_name", f"team_{side}_code"):
            value = event.get(key)
            if value:
                aliases.append(value)
        for value in event.get(f"team_{side}_source_aliases") or []:
            if value:
                aliases.append(value)
        code = str(event.get(f"team_{side}_code") or "").strip().upper()
        if code in NBA_ODDS_TEAM_ALIASES:
            aliases.extend((code, *NBA_ODDS_TEAM_ALIASES[code]))
        normalized_values = {
            SportsDashboard._normalize_odds_team_name(alias)
            for alias in aliases
            if SportsDashboard._normalize_odds_team_name(alias)
        }
        for candidate_code, candidate_aliases in NBA_ODDS_TEAM_ALIASES.items():
            normalized_aliases = {
                SportsDashboard._normalize_odds_team_name(alias)
                for alias in (candidate_code, *candidate_aliases)
                if SportsDashboard._normalize_odds_team_name(alias)
            }
            if normalized_values.intersection(normalized_aliases):
                return normalized_values.union(normalized_aliases)
        return normalized_values

    @staticmethod
    def _nba_team_matches_aliases(team_name, aliases):
        normalized = SportsDashboard._normalize_odds_team_name(team_name)
        if not normalized:
            return False
        if normalized in aliases:
            return True
        return any(len(alias) >= 4 and (alias in normalized or normalized in alias) for alias in aliases)

    def _fetch_lpl_events(self, settings, timezone_info):
        return self._fetch_lol_esports_events(
            settings,
            timezone_info,
            "lplLeagueId",
            DEFAULT_LPL_LEAGUE_ID,
            "LPL",
        )

    def _fetch_lck_events(self, settings, timezone_info):
        return self._fetch_lol_esports_events(
            settings,
            timezone_info,
            "lckLeagueId",
            DEFAULT_LCK_LEAGUE_ID,
            "LCK",
        )

    def _fetch_lol_esports_events(self, settings, timezone_info, league_setting_key, default_league_id, league_key):
        league_id = str(settings.get(league_setting_key) or default_league_id).strip()
        url = LOLESPORTS_SCHEDULE_URL.format(league_id=league_id)
        session = get_http_session()
        response = session.get(
            url,
            headers={"x-api-key": LOLESPORTS_API_KEY, "Accept": "application/json"},
            timeout=25,
        )
        response.raise_for_status()
        events = self._parse_lpl_events(response.json(), timezone_info)
        now = datetime.now(timezone_info)
        live_endpoint_key = "lplLiveEndpointEnabled" if str(league_key).upper() == "LPL" else "lckLiveEndpointEnabled"
        if self._bool_setting(settings, live_endpoint_key, True) and self._should_poll_lpl_live_endpoint(events, now):
            try:
                live_events = self._fetch_lpl_live_events(settings, timezone_info)
                events = self._merge_lpl_live_events(events, live_events, league_id)
            except Exception as exc:
                logger.warning("%s live endpoint fetch failed: %s", league_key, exc)
        return self._annotate_lol_league_key(events, league_key)

    @staticmethod
    def _annotate_lol_league_key(events, league_key):
        key = str(league_key or "").strip().upper()
        for event in events or []:
            event["league_key"] = key
        return events

    def _fetch_lpl_live_events(self, settings, timezone_info):
        session = get_http_session()
        response = session.get(
            LOLESPORTS_LIVE_URL,
            headers={"x-api-key": LOLESPORTS_API_KEY, "Accept": "application/json"},
            timeout=20,
        )
        response.raise_for_status()
        return self._parse_lpl_events(response.json(), timezone_info)

    @staticmethod
    def _parse_lpl_events(payload, timezone_info):
        parsed = []
        events = payload.get("data", {}).get("schedule", {}).get("events", [])
        for event in events:
            start_time = SportsDashboard._parse_start_time(event.get("startTime"), timezone_info)
            if not start_time:
                continue
            match = event.get("match") or {}
            league = event.get("league") or {}
            teams = match.get("teams") or []
            team_a, wins_a, team_a_logo = SportsDashboard._team_info(teams, 0)
            team_b, wins_b, team_b_logo = SportsDashboard._team_info(teams, 1)
            best_of = SportsDashboard._lpl_best_of(match)
            event_id = str(event.get("id") or match.get("id") or "").strip()
            block = str(event.get("blockName") or "").strip()
            parsed.append(
                {
                    "event_id": event_id,
                    "match_id": str(match.get("id") or event_id).strip(),
                    "league_id": str(league.get("id") or "").strip(),
                    "league_name": str(league.get("name") or "").strip(),
                    "league_slug": str(league.get("slug") or "").strip(),
                    "start": start_time,
                    "state": str(event.get("state") or "").lower(),
                    "team_a": team_a,
                    "team_b": team_b,
                    "team_a_logo": team_a_logo,
                    "team_b_logo": team_b_logo,
                    "wins_a": wins_a,
                    "wins_b": wins_b,
                    "best_of": best_of,
                    "block": block,
                    "stage_label": SportsDashboard._lpl_source_stage_label(event, match),
                }
            )
        return SportsDashboard._annotate_lpl_stage_labels(parsed)

    @staticmethod
    def _merge_lpl_live_events(schedule_events, live_events, league_id):
        merged = list(schedule_events or [])
        if not live_events:
            return merged
        league_id = str(league_id or "").strip()
        by_id = {
            event.get("event_id"): index
            for index, event in enumerate(merged)
            if event.get("event_id")
        }
        for live_event in live_events:
            live_league_id = str(live_event.get("league_id") or "").strip()
            if league_id and live_league_id and live_league_id != league_id:
                continue
            event_id = live_event.get("event_id")
            match_index = by_id.get(event_id)
            if match_index is None:
                match_index = SportsDashboard._find_lpl_event_match(merged, live_event)
            if match_index is None:
                if league_id and not live_league_id:
                    continue
                merged.append(live_event)
            else:
                merged[match_index] = {**merged[match_index], **live_event}
        return SportsDashboard._annotate_lpl_stage_labels(merged)

    @staticmethod
    def _lpl_source_stage_label(event, match=None):
        event = event or {}
        match = match or {}
        candidates = (
            event.get("stageName"),
            event.get("stage"),
            event.get("roundName"),
            event.get("round"),
            event.get("phaseName"),
            event.get("phase"),
            event.get("blockName"),
            match.get("stageName"),
            match.get("stage"),
            match.get("roundName"),
            match.get("round"),
            match.get("phaseName"),
            match.get("phase"),
        )
        for candidate in candidates:
            for value in SportsDashboard._lpl_stage_candidate_values(candidate):
                label = SportsDashboard._canonical_lpl_stage_label(value)
                if label:
                    return label
        return ""

    @staticmethod
    def _lpl_stage_candidate_values(value):
        if value is None:
            return []
        if isinstance(value, dict):
            values = []
            for key in ("name", "title", "label", "slug", "stage", "round", "phase"):
                values.extend(SportsDashboard._lpl_stage_candidate_values(value.get(key)))
            return values
        if isinstance(value, (list, tuple)):
            values = []
            for item in value:
                values.extend(SportsDashboard._lpl_stage_candidate_values(item))
            return values
        return [value]

    @staticmethod
    def _canonical_lpl_stage_label(value):
        text = str(value or "").strip()
        if not text:
            return ""
        normalized = " ".join(text.replace("_", " ").replace("-", " ").split())
        lower = normalized.lower()
        compact = lower.replace(" ", "")
        if compact in {"playoff", "playoffs", "lpl", "lplplayoff", "lplplayoffs"}:
            return ""
        if "semi" in lower:
            return "Semi-Final"
        if "quarter" in lower:
            return "Quarter-Final"
        if "grandfinal" in compact or "final" in lower:
            return "Final"
        if "group" in lower:
            if "stage" in lower:
                return "Group Stage"
            return SportsDashboard._format_lpl_stage_label(normalized)
        if lower.startswith("round"):
            return SportsDashboard._format_lpl_stage_label(normalized)
        return ""

    @staticmethod
    def _is_generic_lpl_playoff_stage(value):
        text = str(value or "").strip()
        if not text:
            return False
        compact = "".join(ch for ch in text.lower() if ch.isalnum())
        return compact in {"playoff", "playoffs", "lplplayoff", "lplplayoffs"}

    @staticmethod
    def _format_lpl_stage_label(value):
        text = " ".join(str(value or "").strip().split())
        if not text:
            return "LPL"
        if text.upper() == "LPL":
            return "LPL"
        words = []
        for word in text.replace("_", " ").split():
            if word.upper() in {"LPL", "MSI"}:
                words.append(word.upper())
            elif len(word) == 1:
                words.append(word.upper())
            else:
                words.append(word.capitalize())
        return " ".join(words)

    @staticmethod
    def _annotate_lpl_stage_labels(events):
        annotated = [dict(event) for event in sorted(events or [], key=lambda item: item.get("start") or datetime.max)]
        generic_indices = []
        for index, event in enumerate(annotated):
            explicit_label = SportsDashboard._canonical_lpl_stage_label(event.get("stage_label"))
            if explicit_label:
                event["stage_label"] = explicit_label
                continue
            block_label = SportsDashboard._canonical_lpl_stage_label(event.get("block"))
            if block_label:
                event["stage_label"] = block_label
                continue
            if SportsDashboard._is_generic_lpl_playoff_stage(event.get("stage_label")) or SportsDashboard._is_generic_lpl_playoff_stage(event.get("block")):
                generic_indices.append(index)
                continue
            event["stage_label"] = SportsDashboard._format_lpl_stage_label(event.get("stage_label") or event.get("block") or "LPL")

        if not generic_indices:
            return annotated
        if len(generic_indices) == 1:
            index = generic_indices[0]
            start = annotated[index].get("start")
            has_future_final = any(
                event.get("stage_label") == "Final"
                and isinstance(event.get("start"), datetime)
                and isinstance(start, datetime)
                and event["start"] > start
                for event in annotated
            )
            annotated[index]["stage_label"] = "Semi-Final" if has_future_final else "Playoffs"
            return annotated

        ranked = sorted(
            generic_indices,
            key=lambda index: (
                annotated[index]["start"].timestamp()
                if isinstance(annotated[index].get("start"), datetime)
                else float("-inf"),
                index,
            ),
            reverse=True,
        )
        explicit_final_starts = [
            event["start"]
            for index, event in enumerate(annotated)
            if index not in generic_indices
            and event.get("stage_label") == "Final"
            and isinstance(event.get("start"), datetime)
        ]
        for rank, index in enumerate(ranked):
            start = annotated[index].get("start")
            has_future_final = any(
                isinstance(start, datetime) and final_start > start
                for final_start in explicit_final_starts
            )
            if has_future_final:
                if rank <= 1:
                    label = "Semi-Final"
                elif rank <= 5:
                    label = "Quarter-Final"
                else:
                    label = "Playoffs"
            elif rank == 0:
                label = "Final"
            elif rank <= 2:
                label = "Semi-Final"
            elif rank <= 6:
                label = "Quarter-Final"
            else:
                label = "Playoffs"
            annotated[index]["stage_label"] = label
        return annotated

    @staticmethod
    def _find_lpl_event_match(events, candidate):
        candidate_start = candidate.get("start")
        for index, event in enumerate(events):
            if event.get("event_id") and candidate.get("event_id") and event.get("event_id") != candidate.get("event_id"):
                continue
            same_teams = (
                event.get("team_a") == candidate.get("team_a")
                and event.get("team_b") == candidate.get("team_b")
            )
            same_time = False
            event_start = event.get("start")
            if isinstance(event_start, datetime) and isinstance(candidate_start, datetime):
                same_time = abs((event_start - candidate_start).total_seconds()) <= 1800
            if same_teams and same_time:
                return index
        return None

    @staticmethod
    def _parse_start_time(value, timezone_info):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone_info)
        except ValueError:
            return None

    @staticmethod
    def _team_info(teams, index):
        if index >= len(teams):
            return "TBD", None, ""
        team = teams[index] or {}
        result = team.get("result") or {}
        name = str(team.get("code") or team.get("name") or "TBD").strip() or "TBD"
        logo = str(team.get("image") or "").strip()
        return name, SportsDashboard._lpl_int_value(result.get("gameWins")), logo

    @staticmethod
    def _lpl_best_of(match):
        match = match or {}
        strategy = match.get("strategy") or {}
        for value in (
            strategy.get("count"),
            strategy.get("bestOf"),
            strategy.get("best_of"),
            match.get("bestOf"),
            match.get("best_of"),
        ):
            parsed = SportsDashboard._lpl_int_value(value)
            if parsed and parsed > 0:
                return parsed
        return None

    @staticmethod
    def _lpl_int_value(value):
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fallback_lpl_events(timezone_info):
        rows = [
            ("2026-06-03T09:00:00+00:00", "unstarted", "BLG", "EDG", None, None, "Playoffs", 5),
            ("2026-06-05T09:00:00+00:00", "unstarted", "LGD", "AL", None, None, "Playoffs", 5),
            ("2026-06-06T09:00:00+00:00", "unstarted", "TBD", "JDG", None, None, "Playoffs", 5),
            ("2026-06-07T09:00:00+00:00", "unstarted", "WE", "TES", None, None, "Playoffs", 5),
            ("2026-06-02T09:00:00+00:00", "completed", "TT", "LGD", 2, 3, "Playoffs", 5),
            ("2026-06-01T09:00:00+00:00", "completed", "AL", "WE", 0, 3, "Playoffs", 5),
            ("2026-05-31T09:00:00+00:00", "completed", "TES", "JDG", 3, 1, "Playoffs", 5),
        ]
        events = []
        for start, state, team_a, team_b, wins_a, wins_b, block, best_of in rows:
            events.append(
                {
                    "event_id": "",
                    "league_id": DEFAULT_LPL_LEAGUE_ID,
                    "league_name": "LPL",
                    "league_slug": "lpl",
                    "start": datetime.fromisoformat(start).astimezone(timezone_info),
                    "state": state,
                    "team_a": team_a,
                    "team_b": team_b,
                    "team_a_logo": "",
                    "team_b_logo": "",
                    "wins_a": wins_a,
                    "wins_b": wins_b,
                    "best_of": best_of,
                    "match_id": "",
                    "block": block,
                }
            )
        return sorted(events, key=lambda item: item["start"])

    @staticmethod
    def _select_lpl_events(events, now):
        return SportsDashboard._select_lol_events(events, now, include_lpl_featured=True)

    @staticmethod
    def _select_lck_events(events, now):
        return SportsDashboard._select_lol_events(events, now, include_lpl_featured=False)

    @staticmethod
    def _select_lol_events(events, now, include_lpl_featured=True):
        live = [event for event in events if SportsDashboard._is_lpl_live_event(event, now)]
        upcoming = [
            event for event in events
            if (
                not SportsDashboard._is_lpl_live_event(event, now)
                and not SportsDashboard._is_lpl_finished_event(event, now)
                and event["start"] >= now
            )
        ]
        recent = sorted(
            [
                event for event in events
                if not SportsDashboard._is_lpl_live_event(event, now)
                and event["start"] < now
            ],
            key=lambda item: item["start"],
            reverse=True,
        )
        main = live[0] if live else (upcoming[0] if upcoming else (recent[0] if recent else None))
        featured_event = None
        if include_lpl_featured:
            featured_event = SportsDashboard._lpl_featured_event_for_selection(live, upcoming, now)
            if not featured_event and not live and not upcoming:
                featured_event = SportsDashboard._lpl_msi_featured_event(now)
        featured_event_page = bool(featured_event and not live and not upcoming)
        return {
            "live": live,
            "upcoming": upcoming,
            "recent": recent,
            "main": main,
            "featured_event": featured_event,
            "featured_event_page": featured_event_page,
            "offseason": bool(featured_event_page and featured_event.get("phase") == "countdown"),
        }

    @staticmethod
    def _lpl_featured_event_for_selection(live, upcoming, now):
        for event, phase in ((live[0] if live else None, "live"), (upcoming[0] if upcoming else None, "match_upcoming")):
            key = SportsDashboard._lpl_featured_event_key(event)
            if key == "MSI":
                return SportsDashboard._lpl_msi_featured_event(now, phase_override=phase, start=event.get("start"))
        return None

    @staticmethod
    def _lpl_featured_event_key(event):
        if not event:
            return ""
        candidates = []
        for key in (
            "featured_event",
            "league_name",
            "league_slug",
            "stage_label",
            "block",
            "stage",
            "round",
            "phase",
        ):
            value = event.get(key)
            if value:
                candidates.append(value)
        normalized = " ".join(str(value).strip().lower() for value in candidates if str(value).strip())
        compact = normalized.replace("-", "").replace("_", "").replace(" ", "")
        if "midseasoninvitational" in compact or compact == "msi" or " msi" in f" {normalized}":
            return "MSI"
        return ""

    @staticmethod
    def _lpl_msi_featured_event(now, phase_override=None, start=None):
        if not isinstance(now, datetime):
            now = datetime.now(timezone.utc)
        tzinfo = now.tzinfo or timezone.utc
        start_at = start if isinstance(start, datetime) else datetime(*MSI_2026_START, 0, 0, tzinfo=tzinfo)
        end_at = datetime(*MSI_2026_END, 23, 59, tzinfo=tzinfo)
        if phase_override:
            phase = phase_override
        elif now.date() < start_at.date():
            phase = "countdown"
        elif now.date() <= end_at.date():
            phase = "active"
        else:
            return None
        return {
            "key": "MSI",
            "name": "MSI",
            "title": "2026 MSI",
            "start": start_at,
            "end": end_at,
            "logo_path": LOCAL_MSI_LOGO_PATH,
            "phase": phase,
            "countdown_days": SportsDashboard._countdown_days(now, start_at),
        }

    @staticmethod
    def _countdown_days(now, start):
        if not isinstance(now, datetime) or not isinstance(start, datetime):
            return 0
        return max(0, (start.date() - now.date()).days)

    @staticmethod
    def _lpl_msi_next_filler_active(now):
        return SportsDashboard._lpl_msi_next_filler_event(now) is not None

    @staticmethod
    def _lpl_msi_next_filler_event(now, featured_event=None):
        if isinstance(featured_event, Mapping) and str(featured_event.get("key") or "").strip().upper() == "MSI":
            phase = str(featured_event.get("phase") or "").strip().lower()
            if phase in {"countdown", "match_upcoming"}:
                return featured_event
        featured = SportsDashboard._lpl_msi_featured_event(now)
        if not featured or featured.get("phase") != "countdown":
            return None
        days = SportsDashboard._lpl_int_value(featured.get("countdown_days"))
        if days is None or days < 0 or days > LPL_MSI_NEXT_WINDOW_DAYS:
            return None
        return featured

    @staticmethod
    def _should_poll_lpl_live_endpoint(events, now):
        return any(SportsDashboard._is_lpl_live_poll_candidate(event, now) for event in events or [])

    @staticmethod
    def _is_lpl_live_poll_candidate(event, now):
        if SportsDashboard._is_lpl_live_event(event, now):
            return True
        start = (event or {}).get("start")
        if not isinstance(start, datetime) or now is None:
            return False
        return start - LPL_LIVE_PREGAME_WINDOW <= now < start + LPL_INFERRED_LIVE_WINDOW

    @staticmethod
    def _is_lpl_live_event(event, now=None):
        event = event or {}
        if str(event.get("state") or "").strip().lower() in LPL_LIVE_STATES:
            return True
        if now is None:
            return False
        start = event.get("start")
        if not isinstance(start, datetime):
            return False
        if not start <= now < start + LPL_INFERRED_LIVE_WINDOW:
            return False
        return (
            SportsDashboard._lpl_score_is_unresolved(event)
            or SportsDashboard._lpl_series_is_unfinished(event)
        )

    @staticmethod
    def _is_lpl_finished_event(event, now=None):
        event = event or {}
        if SportsDashboard._is_lpl_live_event(event, now):
            return False
        if str(event.get("state") or "").strip().lower() != "completed":
            return False
        if now is None:
            return True
        start = event.get("start")
        if isinstance(start, datetime) and now < start + LPL_INFERRED_LIVE_WINDOW:
            return not (
                SportsDashboard._lpl_score_is_unresolved(event)
                or SportsDashboard._lpl_series_is_unfinished(event)
            )
        return True

    @staticmethod
    def _lpl_score_is_unresolved(event):
        wins_a = SportsDashboard._lpl_int_value(event.get("wins_a"))
        wins_b = SportsDashboard._lpl_int_value(event.get("wins_b"))
        return wins_a in (None, 0) and wins_b in (None, 0)

    @staticmethod
    def _lpl_series_is_unfinished(event):
        best_of = SportsDashboard._lpl_int_value((event or {}).get("best_of"))
        if not best_of or best_of <= 1:
            return False
        wins_a = SportsDashboard._lpl_int_value(event.get("wins_a"))
        wins_b = SportsDashboard._lpl_int_value(event.get("wins_b"))
        if wins_a is None and wins_b is None:
            return True
        wins_needed = best_of // 2 + 1
        return max(wins_a or 0, wins_b or 0) < wins_needed

    def _write_nba_live_state(self, selected, now, source_state):
        live_events = (selected or {}).get("live") or []
        event = live_events[0] if live_events else None
        payload = {
            "version": NBA_LIVE_STATE_VERSION,
            "updated_at": now.astimezone(timezone.utc).isoformat(),
            "source_state": source_state,
            "has_live": bool(event),
            "live_until": None,
        }
        if event:
            start = event.get("start")
            live_until = start + NBA_INFERRED_LIVE_WINDOW if isinstance(start, datetime) else now + NBA_INFERRED_LIVE_WINDOW
            payload.update(
                {
                    "live_until": live_until.astimezone(timezone.utc).isoformat(),
                    "event_id": event.get("event_id"),
                    "team_a": event.get("team_a"),
                    "team_b": event.get("team_b"),
                    "score_a": event.get("wins_a"),
                    "score_b": event.get("wins_b"),
                    "status_text": event.get("status_text"),
                }
            )
        try:
            self._write_json_file(self._nba_live_state_path(), payload)
        except OSError as exc:
            logger.warning("Failed to write NBA live refresh state: %s", exc)

    def _write_f1_live_state(self, selected, now, source_state):
        live_session = (selected or {}).get("live_session")
        race = (selected or {}).get("main_race") or {}
        payload = {
            "version": F1_LIVE_STATE_VERSION,
            "updated_at": now.astimezone(timezone.utc).isoformat(),
            "source_state": source_state,
            "has_live": bool(live_session),
            "live_until": None,
            "status": (selected or {}).get("status") or "BREAK",
            "race_name": race.get("race_name") or "",
        }
        if live_session:
            start = live_session.get("start")
            duration = live_session.get("duration") or timedelta(hours=2)
            live_until = start + duration if isinstance(start, datetime) else now + duration
            payload.update(
                {
                    "live_until": live_until.astimezone(timezone.utc).isoformat(),
                    "session": live_session.get("label") or "",
                    "started_at": start.astimezone(timezone.utc).isoformat() if isinstance(start, datetime) else None,
                }
            )
        try:
            self._write_json_file(self._f1_live_state_path(), payload)
        except OSError as exc:
            logger.warning("Failed to write F1 live refresh state: %s", exc)

    def _write_lpl_live_state(self, selected, now, source_state):
        self._write_lol_live_state(selected, now, source_state, league_key="LPL")

    def _write_lck_live_state(self, selected, now, source_state):
        self._write_lol_live_state(selected, now, source_state, league_key="LCK")

    def _write_lol_live_state(self, selected, now, source_state, league_key="LPL"):
        key = str(league_key or "LPL").strip().upper()
        live_events = (selected or {}).get("live") or []
        event = live_events[0] if live_events else None
        payload = {
            "version": LCK_LIVE_STATE_VERSION if key == "LCK" else LPL_LIVE_STATE_VERSION,
            "league_key": key,
            "updated_at": now.astimezone(timezone.utc).isoformat(),
            "source_state": source_state,
            "has_live": bool(event),
            "live_until": None,
        }
        if event:
            start = event.get("start")
            live_until = start + LPL_INFERRED_LIVE_WINDOW if isinstance(start, datetime) else None
            payload.update(
                {
                    "event_id": event.get("event_id") or "",
                    "team_a": event.get("team_a") or "",
                    "team_b": event.get("team_b") or "",
                    "score": self._score_label(event),
                    "best_of": event.get("best_of"),
                    "little_round": event.get("little_round") or None,
                    "state": event.get("state") or "",
                    "started_at": start.astimezone(timezone.utc).isoformat() if isinstance(start, datetime) else None,
                    "live_until": live_until.astimezone(timezone.utc).isoformat() if live_until else None,
                }
            )
        try:
            path = self._lck_live_state_path() if key == "LCK" else self._lpl_live_state_path()
            self._write_json_file(path, payload)
        except OSError as exc:
            logger.warning("Failed to write %s live refresh state: %s", key, exc)

    def _write_worldcup_live_state(self, selected, now, source_state):
        live_events = (selected or {}).get("live") or []
        event = live_events[0] if live_events else None
        payload = {
            "version": WORLD_CUP_LIVE_STATE_VERSION,
            "updated_at": now.astimezone(timezone.utc).isoformat(),
            "source_state": source_state,
            "has_live": bool(event),
            "live_until": None,
        }
        if event:
            start = event.get("start")
            live_until = start + WORLD_CUP_INFERRED_LIVE_WINDOW if isinstance(start, datetime) else now + WORLD_CUP_INFERRED_LIVE_WINDOW
            inferred_live = bool(event.get("inferred_live"))
            source_state_text = str(source_state or "").upper()
            provider = str(event.get("provider") or "").strip()
            score_source = str(event.get("score_source") or "").strip()
            if not provider and ("ESPN" in source_state_text or score_source.upper() == "ESPN"):
                provider = "ESPN"
            payload.update(
                {
                    "live_until": live_until.astimezone(timezone.utc).isoformat(),
                    "event_id": event.get("event_id") or "",
                    "team_a": event.get("team_a") or "",
                    "team_b": event.get("team_b") or "",
                    "score": self._worldcup_score_or_vs(event),
                    "state": "LIVE" if inferred_live else (event.get("state") or ""),
                    "status": "Inferred live" if inferred_live else (event.get("status") or ""),
                    "inferred_live": inferred_live,
                    "elapsed": event.get("elapsed"),
                    "started_at": start.astimezone(timezone.utc).isoformat() if isinstance(start, datetime) else None,
                    "provider": provider,
                    "score_source": score_source,
                    "source_url": str(event.get("source_url") or "").strip(),
                    "provider_status_confirmed": bool(event.get("provider_status_confirmed")) and not inferred_live,
                    "score_confirmed": bool(event.get("score_confirmed")) and not inferred_live,
                }
            )
        try:
            self._write_json_file(self._worldcup_live_state_path(), payload)
        except OSError as exc:
            logger.warning("Failed to write World Cup live refresh state: %s", exc)

    def _attach_lpl_realtime_info(self, selected, settings, league_key="LPL"):
        if not self._bool_setting(settings, "lplLiveStatsEnabled", True):
            return selected
        live_events = (selected or {}).get("live") or []
        event = live_events[0] if live_events else None
        if not event:
            return selected
        try:
            little_round = self._fetch_lpl_realtime_info(event, settings, league_key=league_key)
        except Exception as exc:
            logger.warning("LPL live stats fetch failed: %s", exc)
            return selected
        if little_round:
            event["little_round"] = little_round
        return selected

    def _fetch_lpl_realtime_info(self, event, settings=None, league_key="LPL"):
        settings = settings or {}
        try:
            little_round = self._fetch_lpl_riot_realtime_info(event)
        except Exception as exc:
            logger.debug("Riot LPL live stats failed before bo3.gg fallback: %s", exc)
            little_round = None
        if little_round:
            return little_round
        if str(league_key or "LPL").strip().upper() == "LPL" and self._bool_setting(settings, "lplBo3LiveApiEnabled", True):
            try:
                return self._fetch_lpl_bo3_little_round(event)
            except Exception as exc:
                logger.debug("bo3.gg LPL live stats fallback failed: %s", exc)
        return None

    def _fetch_lpl_riot_realtime_info(self, event):
        event_id = str((event or {}).get("event_id") or (event or {}).get("match_id") or "").strip()
        if not event_id:
            return None
        payload = self._fetch_lpl_event_details_payload(event_id)
        detail_event = (payload.get("data") or {}).get("event") or {}
        match = detail_event.get("match") or {}
        game = self._lpl_current_game(match)
        if not game:
            if self._lpl_details_show_intermission(match, event):
                return self._lpl_intermission_little_round()
            little_round = self._lpl_little_round_from_candidate_games(detail_event, match, event)
            if little_round:
                return little_round
            return None
        game_id = str(game.get("id") or "").strip()
        if not game_id:
            return None
        window = self._fetch_lpl_live_stats_window(game_id)
        little_round = self._lpl_little_round_from_window(window, detail_event, game, event)
        if little_round:
            return little_round
        return None

    def _lpl_little_round_from_candidate_games(self, detail_event, match, event):
        for game in (match or {}).get("games") or []:
            game_id = str((game or {}).get("id") or "").strip()
            if not game_id:
                continue
            state = str((game or {}).get("state") or "").strip().lower()
            if state == "completed":
                continue
            try:
                window = self._fetch_lpl_live_stats_window(game_id)
            except Exception as exc:
                logger.debug("LPL live stats candidate window failed for %s: %s", game_id, exc)
                continue
            little_round = self._lpl_little_round_from_window(window, detail_event, game, event)
            if little_round:
                return little_round
        return None

    def _fetch_lpl_bo3_little_round(self, event):
        payload = self._fetch_lpl_bo3_match_payload(event)
        if not payload:
            return None
        return self._lpl_little_round_from_bo3_payload(payload, event)

    def _fetch_lpl_bo3_match_payload(self, event):
        session = get_http_session()
        for slug in self._lpl_bo3_match_slug_candidates(event):
            response = session.get(
                f"{BO3_API_BASE_URL}/matches/{slug}",
                headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
                timeout=12,
            )
            if getattr(response, "status_code", None) == 404:
                continue
            response.raise_for_status()
            payload = response.json()
            if self._lpl_bo3_payload_matches_event(payload, event):
                return payload
        return None

    @staticmethod
    def _lpl_little_round_from_bo3_payload(payload, event):
        live_updates = (payload or {}).get("live_updates") or {}
        if live_updates.get("game_ended") is True:
            return SportsDashboard._lpl_intermission_little_round(
                game_id=f"bo3:{payload.get('id') or payload.get('slug') or ''}",
                game_number=SportsDashboard._lpl_bo3_next_game_number(payload),
                frame_time=payload.get("updated_at") or payload.get("start_date") or "",
                source="bo3.gg",
            )
        scores = SportsDashboard._lpl_bo3_game_scores(payload)
        if not scores:
            return None
        score1, score2, game_number = scores
        team1_is_a = SportsDashboard._lpl_bo3_team_matches_event_side(payload, "team1", event, "a")
        team2_is_b = SportsDashboard._lpl_bo3_team_matches_event_side(payload, "team2", event, "b")
        team1_is_b = SportsDashboard._lpl_bo3_team_matches_event_side(payload, "team1", event, "b")
        team2_is_a = SportsDashboard._lpl_bo3_team_matches_event_side(payload, "team2", event, "a")
        if team1_is_a and team2_is_b:
            kills_a, kills_b = score1, score2
        elif team1_is_b and team2_is_a:
            kills_a, kills_b = score2, score1
        else:
            return None
        return {
            "state": "in_game",
            "label": "Little Round",
            "score": f"{kills_a}-{kills_b}",
            "game_id": f"bo3:{payload.get('id') or payload.get('slug') or ''}",
            "game_number": game_number,
            "frame_time": payload.get("updated_at") or payload.get("start_date") or "",
            "source": "bo3.gg",
        }

    @staticmethod
    def _lpl_intermission_little_round(game_id="", game_number=None, frame_time="", source=""):
        result = {
            "state": "intermission",
            "label": "Little Round",
            "score": "0-0",
        }
        if game_id:
            result["game_id"] = str(game_id)
        if game_number is not None:
            result["game_number"] = game_number
        if frame_time:
            result["frame_time"] = frame_time
        if source:
            result["source"] = source
        return result

    @staticmethod
    def _lpl_bo3_game_scores(payload):
        live_updates = (payload or {}).get("live_updates") or {}
        team1_live = live_updates.get("team_1") or live_updates.get("team1") or {}
        team2_live = live_updates.get("team_2") or live_updates.get("team2") or {}
        score1 = SportsDashboard._lpl_int_value(team1_live.get("game_score"))
        score2 = SportsDashboard._lpl_int_value(team2_live.get("game_score"))
        if score1 is None or score2 is None:
            score1 = SportsDashboard._lpl_int_value((payload or {}).get("team1_last_game_score"))
            score2 = SportsDashboard._lpl_int_value((payload or {}).get("team2_last_game_score"))
        if score1 is None or score2 is None:
            return None
        game_number = SportsDashboard._lpl_int_value(live_updates.get("game_number"))
        if game_number is None:
            team1_match_score = SportsDashboard._lpl_int_value((payload or {}).get("team1_score")) or 0
            team2_match_score = SportsDashboard._lpl_int_value((payload or {}).get("team2_score")) or 0
            completed_games = team1_match_score + team2_match_score
            if live_updates and live_updates.get("game_ended") is False:
                completed_games += 1
            game_number = max(1, completed_games)
        return score1, score2, game_number

    @staticmethod
    def _lpl_bo3_next_game_number(payload):
        live_updates = (payload or {}).get("live_updates") or {}
        current_game = SportsDashboard._lpl_int_value(live_updates.get("game_number"))
        if current_game is not None:
            return current_game + 1
        team1_match_score = SportsDashboard._lpl_int_value((payload or {}).get("team1_score")) or 0
        team2_match_score = SportsDashboard._lpl_int_value((payload or {}).get("team2_score")) or 0
        return max(1, team1_match_score + team2_match_score + 1)

    @staticmethod
    def _lpl_bo3_payload_matches_event(payload, event):
        if not payload:
            return False
        same_order = (
            SportsDashboard._lpl_bo3_team_matches_event_side(payload, "team1", event, "a")
            and SportsDashboard._lpl_bo3_team_matches_event_side(payload, "team2", event, "b")
        )
        reversed_order = (
            SportsDashboard._lpl_bo3_team_matches_event_side(payload, "team1", event, "b")
            and SportsDashboard._lpl_bo3_team_matches_event_side(payload, "team2", event, "a")
        )
        if not (same_order or reversed_order):
            return False
        payload_start = SportsDashboard._parse_lpl_frame_time((payload or {}).get("start_date"))
        event_start = SportsDashboard._parse_lpl_frame_time((event or {}).get("start"))
        if payload_start and event_start:
            return abs((payload_start - event_start).total_seconds()) <= 12 * 60 * 60
        return True

    @staticmethod
    def _lpl_bo3_team_matches_event_side(payload, team_key, event, side):
        aliases = SportsDashboard._lpl_event_team_aliases(event or {}, side)
        if not aliases:
            return False
        for value in SportsDashboard._lpl_bo3_team_identity_values(payload, team_key):
            normalized = SportsDashboard._normalize_odds_team_name(value)
            if normalized in aliases:
                return True
            if normalized.endswith("lol") and normalized[:-3] in aliases:
                return True
        return False

    @staticmethod
    def _lpl_bo3_team_identity_values(payload, team_key):
        team = (payload or {}).get(team_key) or {}
        values = [
            team.get("name"),
            team.get("slug"),
            (payload or {}).get(f"{team_key}_name"),
            (payload or {}).get(f"{team_key}_slug"),
        ]
        return [value for value in values if value]

    @staticmethod
    def _lpl_bo3_match_slug_candidates(event):
        team_a_slug = SportsDashboard._lpl_bo3_team_slug(event or {}, "a")
        team_b_slug = SportsDashboard._lpl_bo3_team_slug(event or {}, "b")
        date_part = SportsDashboard._lpl_bo3_slug_date(event or {})
        if not (team_a_slug and team_b_slug and date_part):
            return []
        candidates = []
        for first, second in ((team_a_slug, team_b_slug), (team_b_slug, team_a_slug)):
            slug = f"{first}-vs-{second}-{date_part}"
            if slug not in candidates:
                candidates.append(slug)
        return candidates

    @staticmethod
    def _lpl_bo3_team_slug(event, side):
        normalized = SportsDashboard._normalize_odds_team_name((event or {}).get(f"team_{side}"))
        if not normalized:
            return None
        for code, slug in BO3_LPL_TEAM_SLUGS.items():
            aliases = {
                SportsDashboard._normalize_odds_team_name(alias)
                for alias in (code, *LPL_ODDS_TEAM_ALIASES.get(code, ()))
                if SportsDashboard._normalize_odds_team_name(alias)
            }
            if normalized in aliases:
                return slug
        return None

    @staticmethod
    def _lpl_bo3_slug_date(event):
        start = SportsDashboard._parse_lpl_frame_time((event or {}).get("start"))
        if not start:
            start = datetime.now(timezone.utc)
        return start.strftime("%d-%m-%Y")

    def _fetch_lpl_event_details_payload(self, event_id):
        session = get_http_session()
        response = session.get(
            LOLESPORTS_EVENT_DETAILS_URL.format(event_id=event_id),
            headers={"x-api-key": LOLESPORTS_API_KEY, "Accept": "application/json"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def _fetch_lpl_live_stats_window(self, game_id):
        session = get_http_session()
        response = session.get(
            LOLESPORTS_LIVE_STATS_WINDOW_URL.format(game_id=game_id),
            headers={"Accept": "application/json"},
            timeout=12,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _lpl_current_game(match):
        games = (match or {}).get("games") or []
        for game in games:
            state = str((game or {}).get("state") or "").strip().lower()
            if state in LPL_LIVE_STATES:
                return game
        return None

    @staticmethod
    def _lpl_details_show_intermission(match, event):
        games = (match or {}).get("games") or []
        has_completed_game = any(str((game or {}).get("state") or "").strip().lower() == "completed" for game in games)
        if not SportsDashboard._lpl_series_is_unfinished(event):
            return False
        wins_a = SportsDashboard._lpl_int_value((event or {}).get("wins_a"))
        wins_b = SportsDashboard._lpl_int_value((event or {}).get("wins_b"))
        return has_completed_game or bool(wins_a or wins_b)

    @staticmethod
    def _lpl_little_round_from_window(window, detail_event, game, event):
        frame = SportsDashboard._lpl_latest_stats_frame(window)
        if not frame:
            return None
        if SportsDashboard._lpl_stats_frame_is_stale(frame):
            return None
        side_scores = {
            "blue": SportsDashboard._lpl_side_total_kills(frame.get("blueTeam")),
            "red": SportsDashboard._lpl_side_total_kills(frame.get("redTeam")),
        }
        team_sides = SportsDashboard._lpl_team_sides(detail_event, game, event)
        side_a = team_sides.get("team_a") or "blue"
        side_b = team_sides.get("team_b") or "red"
        kills_a = side_scores.get(side_a)
        kills_b = side_scores.get(side_b)
        if kills_a is None or kills_b is None:
            return None
        return {
            "state": "in_game",
            "label": "Little Round",
            "score": f"{kills_a}-{kills_b}",
            "game_id": str((game or {}).get("id") or (window or {}).get("esportsGameId") or ""),
            "game_number": SportsDashboard._lpl_int_value((game or {}).get("number")),
            "frame_time": frame.get("rfc460Timestamp") or "",
        }

    @staticmethod
    def _lpl_stats_frame_is_stale(frame, now=None):
        frame_time = SportsDashboard._parse_lpl_frame_time((frame or {}).get("rfc460Timestamp"))
        if not frame_time:
            return False
        now = now or datetime.now(timezone.utc)
        return frame_time < now.astimezone(timezone.utc) - LPL_LIVE_STATS_MAX_FRAME_AGE

    @staticmethod
    def _parse_lpl_frame_time(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _lpl_latest_stats_frame(window):
        frames = (window or {}).get("frames") or []
        for frame in reversed(frames):
            if isinstance(frame.get("blueTeam"), dict) and isinstance(frame.get("redTeam"), dict):
                return frame
        return None

    @staticmethod
    def _lpl_side_total_kills(team_frame):
        return SportsDashboard._lpl_int_value((team_frame or {}).get("totalKills"))

    @staticmethod
    def _lpl_team_sides(detail_event, game, event):
        match = (detail_event or {}).get("match") or {}
        teams = match.get("teams") or []
        team_ids_by_code = {
            str((team or {}).get("code") or "").strip().upper(): str((team or {}).get("id") or "").strip()
            for team in teams
        }
        sides_by_team_id = {
            str((team or {}).get("id") or "").strip(): str((team or {}).get("side") or "").strip().lower()
            for team in ((game or {}).get("teams") or [])
        }
        team_a_code = str((event or {}).get("team_a") or "").strip().upper()
        team_b_code = str((event or {}).get("team_b") or "").strip().upper()
        return {
            "team_a": sides_by_team_id.get(team_ids_by_code.get(team_a_code, "")),
            "team_b": sides_by_team_id.get(team_ids_by_code.get(team_b_code, "")),
        }

    def _attach_lpl_odds(self, events, settings, device_config, timezone_info):
        if not events or not self._bool_setting(settings, "lplOddsEnabled", True):
            return events
        api_key = self._lpl_odds_api_key(settings, device_config)
        if not api_key:
            return events
        try:
            odds_events, _source_state, _fetched_at = self._load_lpl_odds(settings, api_key)
            if not odds_events:
                return events
            return self._merge_lpl_odds(events, odds_events, timezone_info, settings)
        except Exception as exc:
            logger.warning("LPL odds overlay failed: %s", _safe_exception_text(exc))
            return events

    @staticmethod
    def _lpl_odds_api_key(settings, device_config=None):
        for key_name in ("lplOddsApiKey", "lplOddsApiIoKey", "oddsApiIoKey"):
            value = str(settings.get(key_name) or "").strip()
            if value:
                return value
        if device_config and hasattr(device_config, "get_config"):
            for key_name in ("lplOddsApiKey", "lplOddsApiIoKey", "oddsApiIoKey"):
                value = str(device_config.get_config(key_name, "") or "").strip()
                if value:
                    return value
        return SportsDashboard._the_odds_api_key(settings, device_config)

    def _load_lpl_odds(self, settings, api_key):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._lpl_odds_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._lpl_odds_cache_key(settings, api_key)
        force_refresh = self._force_refresh_requested(settings)
        cache_hours = self._int_setting(settings, "lplOddsCacheHours", DEFAULT_LPL_ODDS_CACHE_HOURS, 1, 48)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("odds_events"), list)
        if has_compatible_cache and not force_refresh and self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return cache["odds_events"], "LPL ODDS CACHE", cache.get("fetched_at")

        if self._lpl_odds_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache["odds_events"], "LPL ODDS STALE", cache.get("fetched_at")
            return [], "LPL ODDS LIMIT", None

        try:
            payload = self._fetch_lpl_odds_payload(settings, api_key, cache_key, now_utc)
        except Exception:
            if has_compatible_cache:
                return cache["odds_events"], "LPL ODDS STALE", cache.get("fetched_at")
            raise

        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write LPL odds cache: %s", exc)
        return payload["odds_events"], "LPL ODDS LIVE", payload.get("fetched_at")

    def _fetch_lpl_odds_payload(self, settings, api_key, cache_key, now_utc):
        events = self._lpl_odds_api_io_get_json(
            "/events",
            {
                "apiKey": api_key,
                "sport": self._lpl_odds_api_io_sport(settings),
                "league": self._lpl_odds_api_io_league(settings),
                "status": self._lpl_odds_api_io_status(settings),
                "limit": str(self._lpl_odds_api_io_limit(settings)),
            },
            settings,
            now_utc,
        )
        if not isinstance(events, list):
            events = []
        event_ids = [str(item.get("id")) for item in events if item.get("id") is not None][:10]
        odds_events = []
        if event_ids:
            odds_events = self._lpl_odds_api_io_get_json(
                "/odds/multi",
                {
                    "apiKey": api_key,
                    "eventIds": ",".join(event_ids),
                    "bookmakers": self._lpl_odds_bookmakers(settings),
                },
                settings,
                now_utc,
            )
            if not isinstance(odds_events, list):
                odds_events = []
        return {
            "version": LPL_ODDS_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "provider": "oddsapiio",
            "sport": self._lpl_odds_api_io_sport(settings),
            "league": self._lpl_odds_api_io_league(settings),
            "status": self._lpl_odds_api_io_status(settings),
            "bookmakers": self._lpl_odds_bookmakers(settings),
            "events": events,
            "odds_events": odds_events,
        }

    def _lpl_odds_api_io_get_json(self, path, params, settings, now_utc):
        if self._lpl_odds_calls_left(settings, now_utc) <= 0:
            raise RuntimeError("LPL odds daily request limit reached")
        session = get_http_session()
        try:
            response = session.get(
                f"{ODDS_API_IO_BASE_URL}{path}",
                params=params,
                headers={"Accept": "application/json"},
                timeout=25,
            )
        finally:
            self._record_lpl_odds_call(settings, now_utc)
        response.raise_for_status()
        return response.json()

    def _lpl_odds_cache_key(self, settings, api_key):
        token_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:10]
        return "|".join(
            [
                LPL_ODDS_STATE_VERSION,
                self._lpl_odds_api_io_sport(settings),
                self._lpl_odds_api_io_league(settings),
                self._lpl_odds_bookmakers(settings).lower(),
                token_hash,
            ]
        )

    @staticmethod
    def _lpl_odds_api_io_sport(settings):
        sport = str(settings.get("lplOddsApiIoSport") or DEFAULT_LPL_ODDS_API_IO_SPORT).strip()
        return sport or DEFAULT_LPL_ODDS_API_IO_SPORT

    @staticmethod
    def _lpl_odds_api_io_league(settings):
        league = str(settings.get("lplOddsApiIoLeague") or DEFAULT_LPL_ODDS_API_IO_LEAGUE).strip()
        league = league or DEFAULT_LPL_ODDS_API_IO_LEAGUE
        return ODDS_API_IO_LEAGUE_ALIASES.get(league, league)

    @staticmethod
    def _lpl_odds_api_io_status(settings):
        status = str(settings.get("lplOddsApiIoStatus") or DEFAULT_LPL_ODDS_API_IO_STATUS).strip()
        return status or DEFAULT_LPL_ODDS_API_IO_STATUS

    @staticmethod
    def _lpl_odds_api_io_limit(settings):
        return SportsDashboard._int_setting(settings, "lplOddsApiIoLimit", DEFAULT_LPL_ODDS_API_IO_LIMIT, 1, 10)

    @staticmethod
    def _lpl_odds_bookmakers(settings):
        bookmakers = str(settings.get("lplOddsBookmakers") or settings.get("lplOddsBookmaker") or DEFAULT_LPL_ODDS_BOOKMAKERS).strip()
        return bookmakers or DEFAULT_LPL_ODDS_BOOKMAKERS

    @staticmethod
    def _lpl_odds_preferred_bookmakers(settings):
        raw = SportsDashboard._lpl_odds_bookmakers(settings)
        return [
            SportsDashboard._normalize_odds_team_name(item)
            for item in raw.replace(";", ",").split(",")
            if item.strip()
        ]

    def _lpl_odds_cache_path(self):
        return self._sports_dashboard_cache_dir() / "lpl_odds.json"

    def _lpl_odds_state_path(self):
        return self._sports_dashboard_cache_dir() / "lpl_odds_state.json"

    def _lpl_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "lpl_live_state.json"

    def _lck_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "lck_live_state.json"

    def _nba_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "nba_live_state.json"

    def _f1_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "f1_live_state.json"

    def _worldcup_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "worldcup_live_state.json"

    def _lpl_odds_calls_left(self, settings, now_utc):
        limit = self._int_setting(settings, "lplOddsDailyLimit", DEFAULT_LPL_ODDS_DAILY_LIMIT, 1, 12)
        state = self._read_json_file(self._lpl_odds_state_path())
        today = now_utc.date().isoformat()
        if state.get("date") != today:
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_lpl_odds_call(self, settings, now_utc):
        path = self._lpl_odds_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        count = 0
        if state.get("date") == today:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        try:
            self._write_json_file(
                path,
                {
                    "version": LPL_ODDS_STATE_VERSION,
                    "date": today,
                    "count": count + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as exc:
            logger.warning("Failed to write LPL odds request counter: %s", exc)

    def _merge_lpl_odds(self, events, odds_events, timezone_info, settings):
        offers = self._lpl_odds_offers(odds_events, timezone_info, settings)
        if not offers:
            return events
        enriched = []
        for event in events:
            next_event = dict(event)
            matched = self._match_lpl_odds_offer(event, offers)
            if matched:
                offer, reversed_order = matched
                next_event["odds"] = self._lpl_event_odds_from_offer(offer, reversed_order)
            enriched.append(next_event)
        return enriched

    def _lpl_odds_offers(self, odds_events, timezone_info, settings):
        preferred_bookmakers = self._lpl_odds_preferred_bookmakers(settings)
        offers = []
        for item in odds_events or []:
            home_team = str(item.get("home") or item.get("home_team") or "").strip()
            away_team = str(item.get("away") or item.get("away_team") or "").strip()
            if not home_team or not away_team:
                continue
            bookmakers = item.get("bookmakers") or {}
            if isinstance(bookmakers, dict):
                odds = self._pick_odds_api_io_ml_odds(bookmakers, preferred_bookmakers)
            else:
                odds = self._pick_worldcup_h2h_odds(item, preferred_bookmakers)
            if not odds:
                continue
            start = self._parse_start_time(item.get("date") or item.get("commence_time"), timezone_info)
            offers.append(
                {
                    "start": start,
                    "home_team": home_team,
                    "away_team": away_team,
                    **odds,
                }
            )
        return offers

    @staticmethod
    def _match_lpl_odds_offer(event, offers):
        team_a_aliases = SportsDashboard._lpl_event_team_aliases(event, "a")
        team_b_aliases = SportsDashboard._lpl_event_team_aliases(event, "b")
        for offer in offers:
            if not SportsDashboard._lpl_odds_time_matches(event.get("start"), offer.get("start")):
                continue
            home_matches_a = SportsDashboard._lpl_team_matches_aliases(offer.get("home_team"), team_a_aliases)
            away_matches_b = SportsDashboard._lpl_team_matches_aliases(offer.get("away_team"), team_b_aliases)
            if home_matches_a and away_matches_b:
                return offer, False
            home_matches_b = SportsDashboard._lpl_team_matches_aliases(offer.get("home_team"), team_b_aliases)
            away_matches_a = SportsDashboard._lpl_team_matches_aliases(offer.get("away_team"), team_a_aliases)
            if home_matches_b and away_matches_a:
                return offer, True
        return None

    @staticmethod
    def _lpl_event_odds_from_offer(offer, reversed_order):
        if reversed_order:
            team_a = offer.get("away_odds") or ""
            team_b = offer.get("home_odds") or ""
        else:
            team_a = offer.get("home_odds") or ""
            team_b = offer.get("away_odds") or ""
        return {
            "team_a": team_a,
            "team_b": team_b,
            "bookmaker": offer.get("bookmaker") or "",
        }

    @staticmethod
    def _lpl_odds_time_matches(event_start, odds_start):
        if not event_start or not odds_start:
            return True
        try:
            event_utc = event_start.astimezone(timezone.utc)
            odds_utc = odds_start.astimezone(timezone.utc)
        except (AttributeError, ValueError):
            return True
        return abs((event_utc - odds_utc).total_seconds()) <= 8 * 60 * 60

    @staticmethod
    def _lpl_event_team_aliases(event, side):
        value = event.get(f"team_{side}")
        normalized = SportsDashboard._normalize_odds_team_name(value)
        if not normalized:
            return set()
        for code, aliases in LPL_ODDS_TEAM_ALIASES.items():
            normalized_aliases = {
                SportsDashboard._normalize_odds_team_name(alias)
                for alias in (code, *aliases)
                if SportsDashboard._normalize_odds_team_name(alias)
            }
            if normalized in normalized_aliases:
                return normalized_aliases
        return {normalized}

    @staticmethod
    def _lpl_team_matches_aliases(team_name, aliases):
        normalized = SportsDashboard._normalize_odds_team_name(team_name)
        return bool(normalized and normalized in aliases)

    def _try_worldcup_football_data_panel(self, settings, device_config, dimensions, timezone_info, visible_matches, now):
        api_key = self._football_data_key(settings, device_config)
        if not api_key:
            return None
        try:
            matches, source_state, fetched_at = self._load_football_data_matches(settings, api_key, timezone_info)
            events = self._parse_football_data_events(matches, timezone_info)
            events, score_source_state, score_fetched_at = self._attach_worldcup_scoreboard_scores(events, settings, timezone_info)
            if score_source_state:
                source_state = self._worldcup_combined_score_source_state(source_state, score_source_state)
                fetched_at = score_fetched_at or fetched_at
            events = self._attach_worldcup_odds(events, settings, device_config, timezone_info)
            events = self._attach_worldcup_standings_points(events, settings)
            selected = self._select_worldcup_event_sections(events, now, visible_matches)
            if not selected:
                return None
            self._attach_worldcup_lineup_summary_from_api(selected, settings, device_config, timezone_info, now)
            self._write_worldcup_live_state(selected, now, source_state)
            return self._render_worldcup_api_panel(dimensions, selected, source_state, fetched_at, visible_matches, now)
        except Exception as exc:
            logger.warning("football-data.org World Cup panel failed: %s", exc)
            return None

    @staticmethod
    def _football_data_key(settings, device_config=None):
        for key_name in ("footballDataKey", "footballDataToken"):
            value = str(settings.get(key_name) or "").strip()
            if value:
                return value
        if device_config and hasattr(device_config, "get_config"):
            for key_name in ("footballDataKey", "footballDataToken"):
                value = str(device_config.get_config(key_name, "") or "").strip()
                if value:
                    return value
        env_names = (
            "FOOTBALL_DATA",
            "FOOTBALL_DATA_KEY",
            "FOOTBALL_DATA_API_KEY",
            "FOOTBALL_DATA_TOKEN",
            "FOOTBALLDATA_KEY",
            "FOOTBALLDATA_TOKEN",
            "footballDataKey",
        )
        if device_config and hasattr(device_config, "load_env_key"):
            for env_name in env_names:
                value = str(device_config.load_env_key(env_name) or "").strip()
                if value:
                    return value
        for env_name in env_names:
            value = str(os.environ.get(env_name) or "").strip()
            if value:
                return value
        return ""

    def _load_football_data_matches(self, settings, api_key, timezone_info):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._football_data_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._football_data_cache_key(settings, api_key, timezone_info)
        force_refresh = self._force_refresh_requested(settings)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("matches"), list)
        if has_compatible_cache and not force_refresh and self._football_data_cache_is_fresh(cache, settings, timezone_info, now_utc):
            return cache["matches"], "FOOTBALL CACHE", cache.get("fetched_at")

        if self._football_data_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache["matches"], "FOOTBALL STALE", cache.get("fetched_at")
            return [], "FOOTBALL LIMIT", None

        try:
            payload = self._fetch_football_data_payload(settings, api_key, timezone_info, cache_key, now_utc)
        except Exception:
            if has_compatible_cache:
                return cache["matches"], "FOOTBALL STALE", cache.get("fetched_at")
            raise

        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write football-data.org World Cup cache: %s", exc)
        return payload["matches"], "FOOTBALL LIVE", payload.get("fetched_at")

    def _fetch_football_data_payload(self, settings, api_key, timezone_info, cache_key, now_utc):
        season = self._football_data_season(settings)
        competition = self._football_data_competition(settings)
        payload = self._football_data_get_json(
            f"/competitions/{competition}/matches",
            {"season": season},
            api_key,
            settings,
            now_utc,
        )
        matches = payload.get("matches") or []
        if not isinstance(matches, list):
            matches = []
        return {
            "version": FOOTBALL_DATA_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "season": season,
            "competition": competition,
            "timezone": getattr(timezone_info, "key", DEFAULT_TIMEZONE),
            "matches": matches,
        }

    def _football_data_cache_is_fresh(self, cache, settings, timezone_info, now_utc):
        cache_hours = self._int_setting(
            settings,
            "footballDataCacheHours",
            DEFAULT_FOOTBALL_DATA_CACHE_HOURS,
            1,
            24,
        )
        if not self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return False
        if self._football_data_cache_has_live_poll_candidate(cache, timezone_info, now_utc):
            return self._cache_is_fresh_seconds(cache, self._worldcup_live_refresh_seconds(settings), now_utc)
        return True

    def _football_data_cache_has_live_poll_candidate(self, cache, timezone_info, now_utc):
        try:
            events = self._parse_football_data_events(cache.get("matches") or [], timezone_info)
        except Exception as exc:
            logger.debug("football-data.org live cache candidate parse failed: %s", exc)
            return False
        return self._should_poll_worldcup_live_data(events, now_utc.astimezone(timezone_info))

    def _football_data_get_json(self, path, params, api_key, settings, now_utc):
        if self._football_data_calls_left(settings, now_utc) <= 0:
            raise RuntimeError("football-data.org daily request limit reached")
        session = get_http_session()
        try:
            response = session.get(
                f"{FOOTBALL_DATA_BASE_URL}{path}",
                params=params,
                headers={"X-Auth-Token": api_key, "Accept": "application/json"},
                timeout=25,
            )
        finally:
            self._record_football_data_call(settings, now_utc)
        response.raise_for_status()
        return response.json()

    def _football_data_cache_key(self, settings, api_key, timezone_info):
        token_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:10]
        return "|".join(
            [
                FOOTBALL_DATA_STATE_VERSION,
                self._football_data_competition(settings).lower(),
                self._football_data_season(settings),
                getattr(timezone_info, "key", DEFAULT_TIMEZONE),
                token_hash,
            ]
        )

    @staticmethod
    def _football_data_competition(settings):
        competition = str(settings.get("footballDataCompetition") or DEFAULT_FOOTBALL_DATA_COMPETITION).strip().upper()
        return competition if competition else DEFAULT_FOOTBALL_DATA_COMPETITION

    @staticmethod
    def _football_data_season(settings):
        season = str(
            settings.get("footballDataSeason")
            or settings.get("worldCupApiSeason")
            or DEFAULT_WORLD_CUP_SEASON
        ).strip()
        return season if season.isdigit() and len(season) == 4 else DEFAULT_WORLD_CUP_SEASON

    def _football_data_cache_path(self):
        return self._sports_dashboard_cache_dir() / "football_data_worldcup.json"

    def _football_data_state_path(self):
        return self._sports_dashboard_cache_dir() / "football_data_state.json"

    def _football_data_calls_left(self, settings, now_utc):
        limit = self._int_setting(settings, "footballDataDailyLimit", DEFAULT_FOOTBALL_DATA_DAILY_LIMIT, 1, 60)
        state = self._read_json_file(self._football_data_state_path())
        today = now_utc.date().isoformat()
        if state.get("date") != today:
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_football_data_call(self, settings, now_utc):
        path = self._football_data_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        count = 0
        if state.get("date") == today:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        try:
            self._write_json_file(
                path,
                {
                    "version": FOOTBALL_DATA_STATE_VERSION,
                    "date": today,
                    "count": count + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as exc:
            logger.warning("Failed to write football-data.org request counter: %s", exc)

    def _try_worldcup_scoreboard_panel(self, settings, device_config, dimensions, timezone_info, visible_matches, now):
        if not self._bool_setting(settings, "worldCupScoreboardEnabled", True):
            return None
        try:
            payload, source_state, fetched_at = self._load_worldcup_scoreboard(settings, timezone_info)
            events = self._parse_worldcup_espn_events(payload, timezone_info)
            if not events:
                return None
            events = self._attach_worldcup_group_blocks_from_cached_football_data(events, timezone_info)
            events = self._attach_worldcup_odds(events, settings, device_config, timezone_info)
            events = self._attach_worldcup_standings_points(events, settings)
            selected = self._select_worldcup_event_sections(events, now, visible_matches)
            if not selected:
                return None
            self._attach_worldcup_lineup_summary_from_api(selected, settings, device_config, timezone_info, now)
            self._write_worldcup_live_state(selected, now, source_state)
            return self._render_worldcup_api_panel(dimensions, selected, source_state, fetched_at, visible_matches, now)
        except Exception as exc:
            logger.warning("ESPN World Cup scoreboard panel failed: %s", exc)
            return None

    def _attach_worldcup_group_blocks_from_cached_football_data(self, events, timezone_info):
        if not events:
            return events
        cache = self._read_json_file(self._football_data_cache_path())
        matches = cache.get("matches")
        if not isinstance(matches, list):
            return events
        try:
            group_events = [
                event
                for event in self._parse_football_data_events(matches, timezone_info)
                if self._worldcup_explicit_group_key(event)
            ]
        except Exception as exc:
            logger.debug("Failed to parse cached football-data World Cup groups: %s", exc)
            return events
        if not group_events:
            return events
        enriched = []
        for event in events:
            next_event = dict(event)
            if not self._worldcup_explicit_group_key(next_event):
                matched = self._match_worldcup_scoreboard_event(next_event, group_events)
                if matched:
                    group_event, _reversed_order = matched
                    block = group_event.get("block")
                    if self._worldcup_explicit_group_key({"block": block}):
                        next_event["block"] = block
            enriched.append(next_event)
        return enriched

    def _attach_worldcup_scoreboard_scores(self, events, settings, timezone_info):
        if not events or not self._bool_setting(settings, "worldCupScoreboardEnabled", True):
            return events, "", None
        try:
            payload, source_state, fetched_at = self._load_worldcup_scoreboard(settings, timezone_info)
            scoreboard_events = self._parse_worldcup_espn_events(payload, timezone_info)
            if not scoreboard_events:
                return events, "", fetched_at
            merged, attached_count = self._merge_worldcup_scoreboard_events(events, scoreboard_events)
            if attached_count <= 0:
                return merged, "", fetched_at
            return merged, source_state, fetched_at
        except Exception as exc:
            logger.warning("ESPN World Cup score overlay failed: %s", _safe_exception_text(exc))
            return events, "", None

    def _load_worldcup_scoreboard(self, settings, timezone_info):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._worldcup_scoreboard_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._worldcup_scoreboard_cache_key(settings, timezone_info, now_utc)
        force_refresh = self._force_refresh_requested(settings)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("scoreboard"), dict)
        if (
            has_compatible_cache
            and not force_refresh
            and self._worldcup_scoreboard_cache_is_fresh(cache, settings, timezone_info, now_utc)
        ):
            return cache["scoreboard"], "ESPN CACHE", cache.get("fetched_at")

        if self._worldcup_scoreboard_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache["scoreboard"], "ESPN STALE", cache.get("fetched_at")
            return {}, "ESPN LIMIT", None

        try:
            payload = self._fetch_worldcup_scoreboard_payload(settings, timezone_info, cache_key, now_utc)
        except Exception:
            if has_compatible_cache:
                return cache["scoreboard"], "ESPN STALE", cache.get("fetched_at")
            raise

        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write ESPN World Cup scoreboard cache: %s", exc)
        return payload["scoreboard"], "ESPN LIVE", payload.get("fetched_at")

    def _fetch_worldcup_scoreboard_payload(self, settings, timezone_info, cache_key, now_utc):
        start_date, end_date = self._worldcup_scoreboard_date_range(settings, timezone_info, now_utc)
        url = self._worldcup_scoreboard_url(settings)
        session = get_http_session()
        try:
            response = session.get(
                url,
                params={
                    "dates": f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}",
                    "limit": "100",
                },
                headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"},
                timeout=20,
            )
        finally:
            self._record_worldcup_scoreboard_call(settings, now_utc)
        response.raise_for_status()
        return {
            "version": WORLD_CUP_SCOREBOARD_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "range_start": start_date.isoformat(),
            "range_end": end_date.isoformat(),
            "scoreboard": response.json(),
        }

    def _worldcup_scoreboard_cache_key(self, settings, timezone_info, now_utc):
        start_date, end_date = self._worldcup_scoreboard_date_range(settings, timezone_info, now_utc)
        return "|".join(
            [
                WORLD_CUP_SCOREBOARD_STATE_VERSION,
                self._worldcup_scoreboard_url(settings),
                start_date.isoformat(),
                end_date.isoformat(),
                getattr(timezone_info, "key", DEFAULT_TIMEZONE),
            ]
        )

    @staticmethod
    def _worldcup_scoreboard_url(settings):
        value = str(settings.get("worldCupScoreboardUrl") or DEFAULT_WORLD_CUP_SCOREBOARD_URL).strip()
        return value or DEFAULT_WORLD_CUP_SCOREBOARD_URL

    @staticmethod
    def _worldcup_scoreboard_date_range(settings, timezone_info, now_utc):
        local_date = now_utc.astimezone(timezone_info).date()
        lookback = SportsDashboard._int_setting(
            settings,
            "worldCupScoreboardLookbackDays",
            DEFAULT_WORLD_CUP_SCOREBOARD_LOOKBACK_DAYS,
            0,
            30,
        )
        lookahead = SportsDashboard._int_setting(
            settings,
            "worldCupScoreboardLookaheadDays",
            DEFAULT_WORLD_CUP_SCOREBOARD_LOOKAHEAD_DAYS,
            1,
            60,
        )
        return local_date - timedelta(days=lookback), local_date + timedelta(days=lookahead)

    def _worldcup_scoreboard_cache_path(self):
        return self._sports_dashboard_cache_dir() / "worldcup_scoreboard.json"

    def _worldcup_scoreboard_state_path(self):
        return self._sports_dashboard_cache_dir() / "worldcup_scoreboard_state.json"

    def _worldcup_scoreboard_cache_is_fresh(self, cache, settings, timezone_info, now_utc):
        cache_hours = self._int_setting(
            settings,
            "worldCupScoreboardCacheHours",
            DEFAULT_WORLD_CUP_SCOREBOARD_CACHE_HOURS,
            1,
            12,
        )
        if not self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return False
        if self._worldcup_scoreboard_cache_has_live_poll_candidate(cache, timezone_info, now_utc):
            return self._cache_is_fresh_seconds(cache, self._worldcup_live_refresh_seconds(settings), now_utc)
        return True

    def _worldcup_scoreboard_cache_has_live_poll_candidate(self, cache, timezone_info, now_utc):
        try:
            events = self._parse_worldcup_espn_events(cache.get("scoreboard") or {}, timezone_info)
        except Exception as exc:
            logger.debug("ESPN World Cup live cache candidate parse failed: %s", exc)
            return False
        return self._should_poll_worldcup_live_data(events, now_utc.astimezone(timezone_info))

    def _worldcup_scoreboard_calls_left(self, settings, now_utc):
        limit = self._int_setting(
            settings,
            "worldCupScoreboardDailyLimit",
            DEFAULT_WORLD_CUP_SCOREBOARD_DAILY_LIMIT,
            1,
            1440,
        )
        state = self._read_json_file(self._worldcup_scoreboard_state_path())
        today = now_utc.date().isoformat()
        if state.get("date") != today:
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_worldcup_scoreboard_call(self, settings, now_utc):
        path = self._worldcup_scoreboard_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        count = 0
        if state.get("date") == today:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        try:
            self._write_json_file(
                path,
                {
                    "version": WORLD_CUP_SCOREBOARD_STATE_VERSION,
                    "date": today,
                    "count": count + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as exc:
            logger.warning("Failed to update ESPN World Cup scoreboard request counter: %s", exc)

    def _try_worldcup_api_panel(self, settings, device_config, dimensions, timezone_info, visible_matches, now):
        api_key = self._api_sports_key(settings, device_config)
        if not api_key:
            return None
        try:
            fixtures, source_state, fetched_at = self._load_worldcup_api_fixtures(settings, api_key, timezone_info)
            events = self._parse_worldcup_api_events(fixtures, timezone_info)
            events, score_source_state, score_fetched_at = self._attach_worldcup_scoreboard_scores(events, settings, timezone_info)
            if score_source_state:
                source_state = self._worldcup_combined_score_source_state(source_state, score_source_state)
                fetched_at = score_fetched_at or fetched_at
            events = self._attach_worldcup_odds(events, settings, device_config, timezone_info)
            events = self._attach_worldcup_standings_points(events, settings)
            selected = self._select_worldcup_event_sections(events, now, visible_matches)
            if not selected:
                return None
            self._attach_worldcup_lineup_summary_from_api(
                selected,
                settings,
                device_config,
                timezone_info,
                now,
                api_key=api_key,
                api_events=events,
            )
            self._write_worldcup_live_state(selected, now, source_state)
            return self._render_worldcup_api_panel(dimensions, selected, source_state, fetched_at, visible_matches, now)
        except Exception as exc:
            logger.warning("World Cup API panel failed: %s", exc)
            return None

    def _attach_worldcup_lineup_summary_from_api(
        self,
        selected,
        settings,
        device_config,
        timezone_info,
        now,
        api_key="",
        api_events=None,
    ):
        if not selected or not self._bool_setting(settings, "worldCupLineupsEnabled", True):
            return selected
        event = selected.get("main")
        if not event or not self._should_fetch_worldcup_lineups(event, now):
            return selected
        api_key = api_key or self._api_sports_key(settings, device_config)
        if not api_key:
            return selected
        if not str(event.get("fixture_id") or "").strip():
            try:
                if api_events is None:
                    fixtures, _source_state, _fetched_at = self._load_worldcup_api_fixtures(settings, api_key, timezone_info)
                    api_events = self._parse_worldcup_api_events(fixtures, timezone_info)
                matched = self._match_worldcup_api_lineup_event(event, api_events)
                if matched:
                    event["fixture_id"] = matched.get("fixture_id")
            except Exception as exc:
                logger.warning("World Cup lineup fixture match failed: %s", exc)
                return selected
        return self._attach_worldcup_lineup_summary(selected, settings, api_key, timezone_info, now)

    @staticmethod
    def _match_worldcup_api_lineup_event(event, api_events):
        team_a_aliases = SportsDashboard._worldcup_event_team_aliases(event, "a")
        team_b_aliases = SportsDashboard._worldcup_event_team_aliases(event, "b")
        for candidate in api_events or []:
            if not str((candidate or {}).get("fixture_id") or "").strip():
                continue
            if not SportsDashboard._worldcup_odds_time_matches(event.get("start"), candidate.get("start")):
                continue
            candidate_a_matches_a = SportsDashboard._worldcup_team_matches_aliases(
                candidate.get("team_a_source_name") or candidate.get("team_a"),
                team_a_aliases,
            )
            candidate_b_matches_b = SportsDashboard._worldcup_team_matches_aliases(
                candidate.get("team_b_source_name") or candidate.get("team_b"),
                team_b_aliases,
            )
            if candidate_a_matches_a and candidate_b_matches_b:
                return candidate
            candidate_a_matches_b = SportsDashboard._worldcup_team_matches_aliases(
                candidate.get("team_a_source_name") or candidate.get("team_a"),
                team_b_aliases,
            )
            candidate_b_matches_a = SportsDashboard._worldcup_team_matches_aliases(
                candidate.get("team_b_source_name") or candidate.get("team_b"),
                team_a_aliases,
            )
            if candidate_a_matches_b and candidate_b_matches_a:
                return candidate
        return None

    @staticmethod
    def _api_sports_key(settings, device_config=None):
        for key_name in ("apiSportsKey", "apiFootballKey"):
            value = str(settings.get(key_name) or "").strip()
            if value:
                return value
        if device_config and hasattr(device_config, "get_config"):
            for key_name in ("apiSportsKey", "apiFootballKey"):
                value = str(device_config.get_config(key_name, "") or "").strip()
                if value:
                    return value
        env_names = (
            "apiSportsKey",
            "apiFootballKey",
            "API_SPORTS_KEY",
            "APISPORTS_KEY",
            "API_FOOTBALL_KEY",
            "API_FPPTBALL_KEY",
            "X_APISPORTS_KEY",
            "World_CUP",
            "WORLD_CUP",
            "WORLD_CUP_API_KEY",
        )
        if device_config and hasattr(device_config, "load_env_key"):
            for env_name in env_names:
                value = str(device_config.load_env_key(env_name) or "").strip()
                if value:
                    return value
        for env_name in env_names:
            value = str(os.environ.get(env_name) or "").strip()
            if value:
                return value
        return ""

    def _load_worldcup_api_fixtures(self, settings, api_key, timezone_info):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._worldcup_api_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._worldcup_api_cache_key(settings, api_key, timezone_info)
        force_refresh = self._force_refresh_requested(settings)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("fixtures"), list)
        if has_compatible_cache and not force_refresh and self._worldcup_api_block_is_fresh(cache, now_utc):
            return [], str(cache.get("source_state") or "API BLOCKED"), cache.get("fetched_at")
        if has_compatible_cache and not force_refresh and self._worldcup_api_cache_is_fresh(cache, settings, timezone_info, now_utc):
            return cache["fixtures"], "API CACHE", cache.get("fetched_at")

        if self._worldcup_api_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache["fixtures"], "API STALE", cache.get("fetched_at")
            return [], "API LIMIT", None

        try:
            payload = self._fetch_worldcup_api_payload(settings, api_key, timezone_info, cache, cache_key, now_utc)
        except Exception as exc:
            if self._is_worldcup_free_plan_error(exc):
                cache_hours = self._int_setting(
                    settings,
                    "worldCupApiCacheHours",
                    DEFAULT_WORLD_CUP_API_CACHE_HOURS,
                    1,
                    24,
                )
                blocked_payload = self._worldcup_api_block_payload(cache_key, now_utc, cache_hours, exc)
                try:
                    self._write_json_file(cache_path, blocked_payload)
                except OSError as write_exc:
                    logger.warning("Failed to write World Cup API block cache: %s", write_exc)
                return [], "API BLOCKED", blocked_payload.get("fetched_at")
            if has_compatible_cache:
                return cache["fixtures"], "API STALE", cache.get("fetched_at")
            raise

        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write World Cup API cache: %s", exc)
        return payload["fixtures"], "API LIVE", payload.get("fetched_at")

    def _worldcup_api_cache_is_fresh(self, cache, settings, timezone_info, now_utc):
        cache_hours = self._int_setting(
            settings,
            "worldCupApiCacheHours",
            DEFAULT_WORLD_CUP_API_CACHE_HOURS,
            1,
            24,
        )
        if not self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return False
        if self._worldcup_api_cache_has_live_poll_candidate(cache, timezone_info, now_utc):
            return self._cache_is_fresh_seconds(cache, self._worldcup_live_refresh_seconds(settings), now_utc)
        return True

    def _worldcup_api_cache_has_live_poll_candidate(self, cache, timezone_info, now_utc):
        try:
            events = self._parse_worldcup_api_events(cache.get("fixtures") or [], timezone_info)
        except Exception as exc:
            logger.debug("World Cup API live cache candidate parse failed: %s", exc)
            return False
        return self._should_poll_worldcup_live_data(events, now_utc.astimezone(timezone_info))

    def _fetch_worldcup_api_payload(self, settings, api_key, timezone_info, cache, cache_key, now_utc):
        season = self._worldcup_api_season(settings)
        league_id = self._resolve_worldcup_api_league_id(settings, api_key, cache, now_utc)
        payload = self._api_football_get_json(
            "/fixtures",
            {
                "league": league_id,
                "season": season,
                "timezone": getattr(timezone_info, "key", DEFAULT_TIMEZONE),
            },
            api_key,
            settings,
            now_utc,
        )
        errors = payload.get("errors")
        if errors:
            raise RuntimeError(f"API-Sports returned errors: {errors}")
        fixtures = payload.get("response") or []
        if not isinstance(fixtures, list):
            fixtures = []
        return {
            "version": SPORTS_DASHBOARD_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "season": season,
            "league_id": league_id,
            "timezone": getattr(timezone_info, "key", DEFAULT_TIMEZONE),
            "fixtures": fixtures,
        }

    def _resolve_worldcup_api_league_id(self, settings, api_key, cache, now_utc):
        configured = str(settings.get("worldCupApiLeagueId") or DEFAULT_WORLD_CUP_API_LEAGUE_ID).strip()
        if configured and configured.lower() != "auto":
            return configured
        cached = str(cache.get("league_id") or "").strip()
        if cached:
            return cached

        payload = self._api_football_get_json(
            "/leagues",
            {"search": "World Cup", "season": self._worldcup_api_season(settings)},
            api_key,
            settings,
            now_utc,
        )
        leagues = payload.get("response") or []
        best = None
        for item in leagues:
            league = item.get("league") or {}
            name = str(league.get("name") or "").lower()
            if "world cup" in name:
                best = item
                if name == "world cup":
                    break
        if best is None and leagues:
            best = leagues[0]
        league_id = ((best or {}).get("league") or {}).get("id")
        if league_id is None:
            raise RuntimeError("API-Sports league discovery returned no World Cup league id")
        return str(league_id)

    def _api_football_get_json(self, path, params, api_key, settings, now_utc):
        if self._worldcup_api_calls_left(settings, now_utc) <= 0:
            raise RuntimeError("World Cup API daily request limit reached")
        session = get_http_session()
        try:
            response = session.get(
                f"{API_FOOTBALL_BASE_URL}{path}",
                params=params,
                headers={"x-apisports-key": api_key, "Accept": "application/json"},
                timeout=25,
            )
        finally:
            self._record_worldcup_api_call(settings, now_utc)
        response.raise_for_status()
        return response.json()

    def _attach_worldcup_lineup_summary(self, selected, settings, api_key, timezone_info, now):
        if not selected or not self._bool_setting(settings, "worldCupLineupsEnabled", True):
            return selected
        event = selected.get("main")
        fixture_id = str((event or {}).get("fixture_id") or "").strip()
        if not event or not fixture_id or not self._should_fetch_worldcup_lineups(event, now):
            return selected
        try:
            lineups, _source_state, _fetched_at = self._load_worldcup_lineups(settings, api_key, fixture_id, now)
            formation_a, formation_b = self._worldcup_formations_from_lineups(lineups, event)
        except Exception as exc:
            logger.warning("World Cup lineups failed for fixture %s: %s", fixture_id, exc)
            return selected
        if formation_a and formation_b:
            event["formation_a"] = formation_a
            event["formation_b"] = formation_b
            event["lineups_ready"] = True
        return selected

    @staticmethod
    def _should_fetch_worldcup_lineups(event, now):
        start = (event or {}).get("start")
        if not isinstance(start, datetime) or now is None:
            return False
        return start - WORLD_CUP_LINEUP_LOOKAHEAD <= now <= start + WORLD_CUP_LINEUP_POSTMATCH_WINDOW

    def _load_worldcup_lineups(self, settings, api_key, fixture_id, now):
        now_utc = now.astimezone(timezone.utc) if isinstance(now, datetime) else datetime.now(timezone.utc)
        fixture_id = str(fixture_id or "").strip()
        cache_path = self._worldcup_lineups_cache_path()
        cache = self._read_json_file(cache_path)
        fixture_cache = cache.get("fixtures") if isinstance(cache.get("fixtures"), Mapping) else {}
        entry = fixture_cache.get(fixture_id) if isinstance(fixture_cache, Mapping) else None
        if isinstance(entry, Mapping):
            cache_seconds = self._worldcup_lineup_cache_seconds(settings)
            if not entry.get("lineups"):
                cache_seconds = min(cache_seconds, self._worldcup_live_refresh_seconds(settings))
            if self._cache_is_fresh_seconds(entry, cache_seconds, now_utc):
                return entry.get("lineups") or [], "LINEUP CACHE", entry.get("fetched_at")
            if self._worldcup_api_calls_left(settings, now_utc) <= 0:
                return entry.get("lineups") or [], "LINEUP STALE", entry.get("fetched_at")

        payload = self._api_football_get_json("/fixtures/lineups", {"fixture": fixture_id}, api_key, settings, now_utc)
        errors = payload.get("errors")
        if errors:
            raise RuntimeError(f"API-Sports lineups returned errors: {errors}")
        lineups = payload.get("response") or []
        if not isinstance(lineups, list):
            lineups = []
        updated_fixtures = dict(fixture_cache) if isinstance(fixture_cache, Mapping) else {}
        updated_fixtures[fixture_id] = {
            "fixture_id": fixture_id,
            "fetched_at": now_utc.isoformat(),
            "lineups": lineups,
        }
        try:
            self._write_json_file(
                cache_path,
                {
                    "version": WORLD_CUP_LINEUP_STATE_VERSION,
                    "updated_at": now_utc.isoformat(),
                    "fixtures": updated_fixtures,
                },
            )
        except OSError as exc:
            logger.warning("Failed to write World Cup lineup cache: %s", exc)
        return lineups, "LINEUP LIVE", now_utc.isoformat()

    @staticmethod
    def _worldcup_formations_from_lineups(lineups, event):
        formation_a = ""
        formation_b = ""
        fallback = []
        aliases_a = SportsDashboard._normalized_alias_set(
            [event.get("team_a_source_name"), event.get("team_a_tla"), event.get("team_a"), *(event.get("team_a_source_aliases") or [])]
        )
        aliases_b = SportsDashboard._normalized_alias_set(
            [event.get("team_b_source_name"), event.get("team_b_tla"), event.get("team_b"), *(event.get("team_b_source_aliases") or [])]
        )
        for item in lineups or []:
            if not isinstance(item, Mapping):
                continue
            formation = str(item.get("formation") or "").strip()
            if not formation:
                continue
            team = item.get("team") or {}
            team_aliases = SportsDashboard._normalized_alias_set(
                [team.get("name"), team.get("code"), team.get("id"), team.get("country")]
            )
            if team_aliases & aliases_a and not formation_a:
                formation_a = formation
            elif team_aliases & aliases_b and not formation_b:
                formation_b = formation
            else:
                fallback.append(formation)
        if not formation_a and fallback:
            formation_a = fallback.pop(0)
        if not formation_b and fallback:
            formation_b = fallback.pop(0)
        return formation_a, formation_b

    @staticmethod
    def _normalized_alias_set(values):
        aliases = set()
        for value in values or []:
            text = str(value or "").strip()
            if not text:
                continue
            normalized = SportsDashboard._normalize_odds_team_name(text)
            if normalized:
                aliases.add(normalized)
        return aliases

    @staticmethod
    def _worldcup_lineup_cache_seconds(settings):
        return SportsDashboard._int_setting(
            settings,
            "worldCupLineupCacheSeconds",
            DEFAULT_WORLD_CUP_LINEUP_CACHE_SECONDS,
            60,
            3600,
        )

    def _worldcup_api_cache_key(self, settings, api_key, timezone_info):
        token_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:10]
        league_id = str(settings.get("worldCupApiLeagueId") or DEFAULT_WORLD_CUP_API_LEAGUE_ID).strip() or "auto"
        return "|".join(
            [
                SPORTS_DASHBOARD_STATE_VERSION,
                self._worldcup_api_season(settings),
                league_id.lower(),
                getattr(timezone_info, "key", DEFAULT_TIMEZONE),
                token_hash,
            ]
        )

    @staticmethod
    def _worldcup_api_season(settings):
        season = str(
            settings.get("worldCupApiSeason")
            or settings.get("footballDataSeason")
            or DEFAULT_WORLD_CUP_SEASON
        ).strip()
        return season if season.isdigit() and len(season) == 4 else DEFAULT_WORLD_CUP_SEASON

    @staticmethod
    def _worldcup_configured_season(settings):
        season = str(
            (settings or {}).get("worldCupApiSeason")
            or (settings or {}).get("footballDataSeason")
            or DEFAULT_WORLD_CUP_SEASON
        ).strip()
        return season if season.isdigit() and len(season) == 4 else DEFAULT_WORLD_CUP_SEASON

    def _worldcup_api_cache_path(self):
        return self._sports_dashboard_cache_dir() / "worldcup_api.json"

    def _worldcup_api_state_path(self):
        return self._sports_dashboard_cache_dir() / "api_state.json"

    def _worldcup_lineups_cache_path(self):
        return self._sports_dashboard_cache_dir() / "worldcup_lineups.json"

    def _sports_dashboard_cache_dir(self):
        cache_dir = Path(self.get_plugin_dir("cache"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    @staticmethod
    def _worldcup_cache_is_fresh(cache, cache_hours, now_utc):
        fetched_at = SportsDashboard._parse_cached_utc(cache.get("fetched_at"))
        if fetched_at is None:
            return False
        return now_utc - fetched_at <= timedelta(hours=cache_hours)

    @staticmethod
    def _cache_is_fresh_seconds(cache, cache_seconds, now_utc):
        fetched_at = SportsDashboard._parse_cached_utc(cache.get("fetched_at"))
        if fetched_at is None:
            return False
        return now_utc - fetched_at <= timedelta(seconds=cache_seconds)

    @staticmethod
    def _worldcup_api_block_is_fresh(cache, now_utc):
        blocked_until = SportsDashboard._parse_cached_utc(cache.get("blocked_until"))
        return blocked_until is not None and now_utc < blocked_until

    @staticmethod
    def _worldcup_api_block_payload(cache_key, now_utc, cache_hours, exc):
        blocked_hours = max(6, min(24, cache_hours))
        return {
            "version": SPORTS_DASHBOARD_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "blocked_until": (now_utc + timedelta(hours=blocked_hours)).isoformat(),
            "source_state": "API BLOCKED",
            "error": SportsDashboard._safe_api_error_message(exc),
            "fixtures": [],
        }

    @staticmethod
    def _is_worldcup_free_plan_error(exc):
        message = str(exc).lower()
        return "free plans" in message and "season" in message

    @staticmethod
    def _safe_api_error_message(exc):
        text = str(exc)
        return text[:240]

    @staticmethod
    def _parse_cached_utc(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _worldcup_api_calls_left(self, settings, now_utc):
        limit = self._int_setting(settings, "worldCupApiDailyLimit", DEFAULT_WORLD_CUP_API_DAILY_LIMIT, 1, 90)
        state = self._read_json_file(self._worldcup_api_state_path())
        today = now_utc.date().isoformat()
        if state.get("date") != today:
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_worldcup_api_call(self, settings, now_utc):
        path = self._worldcup_api_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        count = 0
        if state.get("date") == today:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        try:
            self._write_json_file(
                path,
                {
                    "version": SPORTS_DASHBOARD_STATE_VERSION,
                    "date": today,
                    "count": count + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as exc:
            logger.warning("Failed to write World Cup API request counter: %s", exc)

    def _attach_worldcup_odds(self, events, settings, device_config, timezone_info):
        if not events or not self._bool_setting(settings, "worldCupOddsEnabled", True):
            return events
        provider = self._worldcup_odds_provider(settings, device_config)
        api_key = self._worldcup_odds_api_key(settings, device_config, provider)
        if not api_key:
            return events
        try:
            odds_events, _source_state, _fetched_at = self._load_worldcup_odds(settings, api_key, provider)
            if not odds_events:
                return events
            return self._merge_worldcup_odds(events, odds_events, timezone_info, settings)
        except Exception as exc:
            logger.warning("World Cup odds overlay failed: %s", _safe_exception_text(exc))
            return events

    @staticmethod
    def _worldcup_odds_api_key(settings, device_config=None, provider=None):
        settings = settings or {}
        provider = provider or SportsDashboard._worldcup_odds_provider(settings, device_config)
        if provider == "oddsapiio":
            key_names = (
                "worldCupOddsApiIoKey",
                "oddsApiIoKey",
                "worldCupOddsApiKey",
                "oddsApiKey",
                "theOddsApiKey",
            )
            env_names = (
                "WORLD_CUP_ODDS_API_IO_KEY",
                "Odds_API_IO_KEY",
                "ODDS_API_IO_KEY",
                "ODDSAPI_IO_KEY",
                "worldCupOddsApiIoKey",
                "oddsApiIoKey",
            )
        else:
            key_names = ("theOddsApiKey", "oddsApiKey", "worldCupOddsApiKey")
            env_names = (
                "THE_ODDS_API_KEY",
                "ODDS_API_KEY",
                "WORLD_CUP_ODDS_API_KEY",
                "theOddsApiKey",
                "oddsApiKey",
                "worldCupOddsApiKey",
            )
        for key_name in key_names:
            value = str(settings.get(key_name) or "").strip()
            if value:
                return value
        if device_config and hasattr(device_config, "get_config"):
            for key_name in key_names:
                value = str(device_config.get_config(key_name, "") or "").strip()
                if value:
                    return value
        if device_config and hasattr(device_config, "load_env_key"):
            for env_name in env_names:
                value = str(device_config.load_env_key(env_name) or "").strip()
                if value:
                    return value
        for env_name in env_names:
            value = str(os.environ.get(env_name) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _worldcup_odds_api_io_key_available(settings, device_config=None):
        settings = settings or {}
        key_names = ("worldCupOddsApiIoKey", "oddsApiIoKey")
        for key_name in key_names:
            if str(settings.get(key_name) or "").strip():
                return True
        if device_config and hasattr(device_config, "get_config"):
            for key_name in key_names:
                if str(device_config.get_config(key_name, "") or "").strip():
                    return True
        env_names = (
            "WORLD_CUP_ODDS_API_IO_KEY",
            "Odds_API_IO_KEY",
            "ODDS_API_IO_KEY",
            "ODDSAPI_IO_KEY",
            "worldCupOddsApiIoKey",
            "oddsApiIoKey",
        )
        if device_config and hasattr(device_config, "load_env_key"):
            for env_name in env_names:
                if str(device_config.load_env_key(env_name) or "").strip():
                    return True
        for env_name in env_names:
            if str(os.environ.get(env_name) or "").strip():
                return True
        return False

    @staticmethod
    def _the_odds_api_key(settings, device_config=None):
        for key_name in ("theOddsApiKey", "oddsApiKey", "worldCupOddsApiKey", "oddsApiIoKey", "worldCupOddsApiIoKey"):
            value = str(settings.get(key_name) or "").strip()
            if value:
                return value
        if device_config and hasattr(device_config, "get_config"):
            for key_name in ("theOddsApiKey", "oddsApiKey", "worldCupOddsApiKey", "oddsApiIoKey", "worldCupOddsApiIoKey"):
                value = str(device_config.get_config(key_name, "") or "").strip()
                if value:
                    return value
        env_names = (
            "THE_ODDS_API_KEY",
            "ODDS_API_KEY",
            "WORLD_CUP_ODDS_API_KEY",
            "Odds_API_IO_KEY",
            "ODDS_API_IO_KEY",
            "ODDSAPI_IO_KEY",
            "WORLD_CUP_ODDS_API_IO_KEY",
            "theOddsApiKey",
            "oddsApiKey",
            "worldCupOddsApiKey",
            "oddsApiIoKey",
            "worldCupOddsApiIoKey",
        )
        if device_config and hasattr(device_config, "load_env_key"):
            for env_name in env_names:
                value = str(device_config.load_env_key(env_name) or "").strip()
                if value:
                    return value
        for env_name in env_names:
            value = str(os.environ.get(env_name) or "").strip()
            if value:
                return value
        return ""

    def _load_worldcup_odds(self, settings, api_key, provider=None):
        provider = provider or self._worldcup_odds_provider(settings)
        now_utc = datetime.now(timezone.utc)
        cache_path = self._worldcup_odds_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._worldcup_odds_cache_key(settings, api_key, provider)
        force_refresh = self._force_refresh_requested(settings)
        cache_hours = self._int_setting(
            settings,
            "worldCupOddsCacheHours",
            DEFAULT_WORLD_CUP_ODDS_CACHE_HOURS,
            1,
            24,
        )
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("odds_events"), list)
        if has_compatible_cache and not force_refresh and self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return cache["odds_events"], "ODDS CACHE", cache.get("fetched_at")

        if self._worldcup_odds_calls_left(settings, now_utc) <= 0:
            if has_compatible_cache:
                return cache["odds_events"], "ODDS STALE", cache.get("fetched_at")
            return [], "ODDS LIMIT", None

        try:
            payload = self._fetch_worldcup_odds_payload(settings, api_key, cache_key, now_utc, provider)
        except Exception:
            if has_compatible_cache:
                return cache["odds_events"], "ODDS STALE", cache.get("fetched_at")
            raise

        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write World Cup odds cache: %s", exc)
        return payload["odds_events"], "ODDS LIVE", payload.get("fetched_at")

    def _fetch_worldcup_odds_payload(self, settings, api_key, cache_key, now_utc, provider=None):
        if (provider or self._worldcup_odds_provider(settings)) == "oddsapiio":
            return self._fetch_odds_api_io_payload(settings, api_key, cache_key, now_utc)
        return self._fetch_the_odds_api_payload(settings, api_key, cache_key, now_utc)

    def _fetch_the_odds_api_payload(self, settings, api_key, cache_key, now_utc):
        sport_key = self._worldcup_odds_sport_key(settings)
        session = get_http_session()
        try:
            response = session.get(
                f"{THE_ODDS_API_BASE_URL}/sports/{sport_key}/odds/",
                params={
                    "apiKey": api_key,
                    "regions": self._worldcup_odds_regions(settings),
                    "markets": self._worldcup_odds_markets(settings),
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                },
                headers={"Accept": "application/json"},
                timeout=25,
            )
        finally:
            self._record_worldcup_odds_call(settings, now_utc)
        response.raise_for_status()
        odds_events = response.json()
        if not isinstance(odds_events, list):
            odds_events = []
        return {
            "version": WORLD_CUP_ODDS_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "sport_key": sport_key,
            "regions": self._worldcup_odds_regions(settings),
            "markets": self._worldcup_odds_markets(settings),
            "odds_events": odds_events,
        }

    def _fetch_odds_api_io_payload(self, settings, api_key, cache_key, now_utc):
        events = self._odds_api_io_get_json(
            "/events",
            {
                "apiKey": api_key,
                "sport": self._worldcup_odds_api_io_sport(settings),
                "league": self._worldcup_odds_api_io_league(settings),
                "status": self._worldcup_odds_api_io_status(settings),
                "limit": str(self._worldcup_odds_api_io_limit(settings)),
            },
            settings,
            now_utc,
        )
        if not isinstance(events, list):
            events = []
        event_ids = [str(item.get("id")) for item in events if item.get("id") is not None][:10]
        odds_events = []
        if event_ids:
            odds_events = self._odds_api_io_get_json(
                "/odds/multi",
                {
                    "apiKey": api_key,
                    "eventIds": ",".join(event_ids),
                    "bookmakers": self._worldcup_odds_bookmakers(settings),
                },
                settings,
                now_utc,
            )
            if not isinstance(odds_events, list):
                odds_events = []
        return {
            "version": WORLD_CUP_ODDS_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "provider": "oddsapiio",
            "sport": self._worldcup_odds_api_io_sport(settings),
            "league": self._worldcup_odds_api_io_league(settings),
            "status": self._worldcup_odds_api_io_status(settings),
            "bookmakers": self._worldcup_odds_bookmakers(settings),
            "events": events,
            "odds_events": odds_events,
        }

    def _odds_api_io_get_json(self, path, params, settings, now_utc):
        if self._worldcup_odds_calls_left(settings, now_utc) <= 0:
            raise RuntimeError("World Cup odds daily request limit reached")
        session = get_http_session()
        try:
            response = session.get(
                f"{ODDS_API_IO_BASE_URL}{path}",
                params=params,
                headers={"Accept": "application/json"},
                timeout=25,
            )
        finally:
            self._record_worldcup_odds_call(settings, now_utc)
        response.raise_for_status()
        return response.json()

    def _worldcup_odds_cache_key(self, settings, api_key, provider=None):
        token_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:10]
        provider = provider or self._worldcup_odds_provider(settings)
        return "|".join(
            [
                WORLD_CUP_ODDS_STATE_VERSION,
                provider,
                self._worldcup_odds_sport_key(settings),
                self._worldcup_odds_api_io_sport(settings),
                self._worldcup_odds_api_io_league(settings),
                self._worldcup_odds_bookmakers(settings).lower(),
                self._worldcup_odds_regions(settings).lower(),
                self._worldcup_odds_markets(settings).lower(),
                token_hash,
            ]
        )

    @staticmethod
    def _worldcup_odds_provider(settings, device_config=None):
        settings = settings or {}
        provider = str(settings.get("worldCupOddsProvider") or "").strip().lower()
        provider = provider.replace("-", "").replace("_", "")
        if provider in {"oddsapiio", "oddsio"}:
            return "oddsapiio"
        if provider:
            return "theoddsapi"
        if SportsDashboard._worldcup_odds_api_io_key_available(settings, device_config):
            return "oddsapiio"
        provider = str(DEFAULT_WORLD_CUP_ODDS_PROVIDER).strip().lower()
        provider = provider.replace("-", "").replace("_", "")
        if provider in {"oddsapiio", "oddsio"}:
            return "oddsapiio"
        return "theoddsapi"

    @staticmethod
    def _worldcup_odds_sport_key(settings):
        sport_key = str(settings.get("worldCupOddsSportKey") or DEFAULT_WORLD_CUP_ODDS_SPORT_KEY).strip()
        return sport_key or DEFAULT_WORLD_CUP_ODDS_SPORT_KEY

    @staticmethod
    def _worldcup_odds_api_io_sport(settings):
        sport = str(settings.get("worldCupOddsApiIoSport") or DEFAULT_WORLD_CUP_ODDS_API_IO_SPORT).strip()
        return sport or DEFAULT_WORLD_CUP_ODDS_API_IO_SPORT

    @staticmethod
    def _worldcup_odds_api_io_league(settings):
        league = str(settings.get("worldCupOddsApiIoLeague") or DEFAULT_WORLD_CUP_ODDS_API_IO_LEAGUE).strip()
        league = league or DEFAULT_WORLD_CUP_ODDS_API_IO_LEAGUE
        return ODDS_API_IO_LEAGUE_ALIASES.get(league, league)

    @staticmethod
    def _worldcup_odds_api_io_status(settings):
        status = str(settings.get("worldCupOddsApiIoStatus") or DEFAULT_WORLD_CUP_ODDS_API_IO_STATUS).strip()
        return status or DEFAULT_WORLD_CUP_ODDS_API_IO_STATUS

    @staticmethod
    def _worldcup_odds_api_io_limit(settings):
        return SportsDashboard._int_setting(
            settings,
            "worldCupOddsApiIoLimit",
            DEFAULT_WORLD_CUP_ODDS_API_IO_LIMIT,
            1,
            10,
        )

    @staticmethod
    def _worldcup_odds_regions(settings):
        regions = str(settings.get("worldCupOddsRegions") or DEFAULT_WORLD_CUP_ODDS_REGIONS).strip()
        return regions or DEFAULT_WORLD_CUP_ODDS_REGIONS

    @staticmethod
    def _worldcup_odds_markets(settings):
        markets = str(settings.get("worldCupOddsMarkets") or DEFAULT_WORLD_CUP_ODDS_MARKETS).strip()
        return markets or DEFAULT_WORLD_CUP_ODDS_MARKETS

    @staticmethod
    def _worldcup_odds_bookmakers(settings):
        bookmakers = str(settings.get("worldCupOddsBookmakers") or settings.get("worldCupOddsBookmaker") or DEFAULT_WORLD_CUP_ODDS_BOOKMAKERS).strip()
        return bookmakers or DEFAULT_WORLD_CUP_ODDS_BOOKMAKERS

    @staticmethod
    def _worldcup_odds_preferred_bookmakers(settings):
        raw = SportsDashboard._worldcup_odds_bookmakers(settings)
        return [
            SportsDashboard._normalize_odds_team_name(item)
            for item in raw.replace(";", ",").split(",")
            if item.strip()
        ]

    def _worldcup_odds_cache_path(self):
        return self._sports_dashboard_cache_dir() / "worldcup_odds.json"

    def _worldcup_odds_state_path(self):
        return self._sports_dashboard_cache_dir() / "odds_state.json"

    def _worldcup_odds_calls_left(self, settings, now_utc):
        limit = self._int_setting(settings, "worldCupOddsDailyLimit", DEFAULT_WORLD_CUP_ODDS_DAILY_LIMIT, 1, 30)
        state = self._read_json_file(self._worldcup_odds_state_path())
        today = now_utc.date().isoformat()
        if state.get("date") != today:
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_worldcup_odds_call(self, settings, now_utc):
        path = self._worldcup_odds_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        count = 0
        if state.get("date") == today:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        try:
            self._write_json_file(
                path,
                {
                    "version": WORLD_CUP_ODDS_STATE_VERSION,
                    "date": today,
                    "count": count + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as exc:
            logger.warning("Failed to write World Cup odds request counter: %s", exc)

    def _merge_worldcup_odds(self, events, odds_events, timezone_info, settings):
        offers = self._worldcup_odds_offers(odds_events, timezone_info, settings)
        if not offers:
            return events
        enriched = []
        for event in events:
            next_event = dict(event)
            matched = self._match_worldcup_odds_offer(event, offers)
            if matched:
                offer, reversed_order = matched
                next_event["odds"] = self._worldcup_event_odds_from_offer(offer, reversed_order)
            enriched.append(next_event)
        return enriched

    def _worldcup_odds_offers(self, odds_events, timezone_info, settings):
        preferred_bookmakers = self._worldcup_odds_preferred_bookmakers(settings)
        offers = []
        for item in odds_events or []:
            home_team = str(item.get("home_team") or item.get("home") or "").strip()
            away_team = str(item.get("away_team") or item.get("away") or "").strip()
            if not home_team or not away_team:
                continue
            odds = self._pick_worldcup_h2h_odds(item, preferred_bookmakers)
            if not odds:
                continue
            start = self._parse_start_time(item.get("commence_time") or item.get("date"), timezone_info)
            offers.append(
                {
                    "start": start,
                    "home_team": home_team,
                    "away_team": away_team,
                    **odds,
                }
            )
        return offers

    @staticmethod
    def _pick_worldcup_h2h_odds(odds_event, preferred_bookmakers):
        bookmakers = odds_event.get("bookmakers") or []
        if isinstance(bookmakers, dict):
            return SportsDashboard._pick_odds_api_io_ml_odds(bookmakers, preferred_bookmakers)
        if not isinstance(bookmakers, list):
            return None
        if preferred_bookmakers:
            preferred = []
            remaining = []
            for bookmaker in bookmakers:
                bookmaker_names = {
                    SportsDashboard._normalize_odds_team_name(bookmaker.get("key")),
                    SportsDashboard._normalize_odds_team_name(bookmaker.get("title")),
                }
                if bookmaker_names.intersection(preferred_bookmakers):
                    preferred.append(bookmaker)
                else:
                    remaining.append(bookmaker)
            bookmakers = preferred + remaining
        for bookmaker in bookmakers:
            for market in bookmaker.get("markets") or []:
                if str(market.get("key") or "").lower() != "h2h":
                    continue
                outcomes = market.get("outcomes") or []
                home_price = SportsDashboard._outcome_price_for_team(outcomes, odds_event.get("home_team"))
                away_price = SportsDashboard._outcome_price_for_team(outcomes, odds_event.get("away_team"))
                if home_price is None or away_price is None:
                    continue
                draw_price = SportsDashboard._outcome_price_for_names(outcomes, {"draw", "tie", "x"})
                return {
                    "home_odds": SportsDashboard._format_decimal_odds(home_price),
                    "draw_odds": SportsDashboard._format_decimal_odds(draw_price) if draw_price is not None else "",
                    "away_odds": SportsDashboard._format_decimal_odds(away_price),
                    "bookmaker": str(bookmaker.get("title") or bookmaker.get("key") or "").strip(),
                }
        return None

    @staticmethod
    def _pick_odds_api_io_ml_odds(bookmakers, preferred_bookmakers):
        bookmaker_items = list(bookmakers.items())
        if preferred_bookmakers:
            preferred = []
            remaining = []
            for bookmaker_name, markets in bookmaker_items:
                normalized = SportsDashboard._normalize_odds_team_name(bookmaker_name)
                if normalized in preferred_bookmakers:
                    preferred.append((bookmaker_name, markets))
                else:
                    remaining.append((bookmaker_name, markets))
            bookmaker_items = preferred + remaining
        for bookmaker_name, markets in bookmaker_items:
            if not isinstance(markets, list):
                continue
            for market in markets:
                if str(market.get("name") or "").strip().lower() not in {"ml", "moneyline", "match winner"}:
                    continue
                for odds in market.get("odds") or []:
                    home = SportsDashboard._format_decimal_odds(odds.get("home"))
                    away = SportsDashboard._format_decimal_odds(odds.get("away"))
                    if not home or not away:
                        continue
                    return {
                        "home_odds": home,
                        "draw_odds": SportsDashboard._format_decimal_odds(odds.get("draw")),
                        "away_odds": away,
                        "bookmaker": str(bookmaker_name or "").strip(),
                    }
        return None

    @staticmethod
    def _outcome_price_for_team(outcomes, team_name):
        target = SportsDashboard._normalize_odds_team_name(team_name)
        for outcome in outcomes or []:
            if SportsDashboard._normalize_odds_team_name(outcome.get("name")) == target:
                return outcome.get("price")
        return None

    @staticmethod
    def _outcome_price_for_names(outcomes, names):
        targets = {SportsDashboard._normalize_odds_team_name(name) for name in names}
        for outcome in outcomes or []:
            if SportsDashboard._normalize_odds_team_name(outcome.get("name")) in targets:
                return outcome.get("price")
        return None

    @staticmethod
    def _format_decimal_odds(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        if number <= 0:
            return ""
        return f"{number:.2f}"

    @staticmethod
    def _match_worldcup_odds_offer(event, offers):
        team_a_aliases = SportsDashboard._worldcup_event_team_aliases(event, "a")
        team_b_aliases = SportsDashboard._worldcup_event_team_aliases(event, "b")
        for offer in offers:
            if not SportsDashboard._worldcup_odds_time_matches(event.get("start"), offer.get("start")):
                continue
            home_matches_a = SportsDashboard._worldcup_team_matches_aliases(offer.get("home_team"), team_a_aliases)
            away_matches_b = SportsDashboard._worldcup_team_matches_aliases(offer.get("away_team"), team_b_aliases)
            if home_matches_a and away_matches_b:
                return offer, False
            home_matches_b = SportsDashboard._worldcup_team_matches_aliases(offer.get("home_team"), team_b_aliases)
            away_matches_a = SportsDashboard._worldcup_team_matches_aliases(offer.get("away_team"), team_a_aliases)
            if home_matches_b and away_matches_a:
                return offer, True
        return None

    @staticmethod
    def _worldcup_event_odds_from_offer(offer, reversed_order):
        if reversed_order:
            team_a = offer.get("away_odds") or ""
            team_b = offer.get("home_odds") or ""
        else:
            team_a = offer.get("home_odds") or ""
            team_b = offer.get("away_odds") or ""
        return {
            "team_a": team_a,
            "draw": offer.get("draw_odds") or "",
            "team_b": team_b,
            "bookmaker": offer.get("bookmaker") or "",
        }

    @staticmethod
    def _worldcup_odds_time_matches(event_start, odds_start):
        if not event_start or not odds_start:
            return True
        try:
            event_utc = event_start.astimezone(timezone.utc)
            odds_utc = odds_start.astimezone(timezone.utc)
        except (AttributeError, ValueError):
            return True
        return abs((event_utc - odds_utc).total_seconds()) <= 36 * 60 * 60

    @staticmethod
    def _worldcup_event_team_aliases(event, side):
        aliases = []
        for key in (f"team_{side}", f"team_{side}_tla", f"team_{side}_source_name"):
            value = event.get(key)
            if value:
                aliases.append(value)
        for value in event.get(f"team_{side}_source_aliases") or []:
            if value:
                aliases.append(value)
        for value in list(aliases):
            aliases.extend(SportsDashboard._country_aliases_for_value(value))
        return {
            SportsDashboard._normalize_odds_team_name(alias)
            for alias in aliases
            if SportsDashboard._normalize_odds_team_name(alias)
        }

    @staticmethod
    def _worldcup_team_matches_aliases(team_name, aliases):
        normalized = SportsDashboard._normalize_odds_team_name(team_name)
        return bool(normalized and normalized in aliases)

    @staticmethod
    def _normalize_odds_team_name(value):
        text = str(value or "").strip()
        if not text:
            return ""
        text = text.replace("&", " ")
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        parts = [part for part in text.lower().replace("-", " ").split() if part != "and"]
        return "".join(ch for part in parts for ch in part if ch.isalnum())

    @staticmethod
    def _read_json_file(path):
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, OSError, ValueError):
            return {}

    @staticmethod
    def _write_json_file(path, payload):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.with_name(f"{target.name}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
        try:
            os.replace(tmp_path, target)
        except OSError:
            with target.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            try:
                tmp_path.unlink()
            except OSError:
                pass

    @staticmethod
    def _int_setting(settings, key, default, minimum, maximum):
        try:
            value = int(settings.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _parse_football_data_events(matches, timezone_info):
        parsed = []
        for item in matches or []:
            start_time = SportsDashboard._parse_start_time(item.get("utcDate"), timezone_info)
            if not start_time:
                continue
            home = item.get("homeTeam") or {}
            away = item.get("awayTeam") or {}
            home_tla = SportsDashboard._football_data_team_tla(home)
            away_tla = SportsDashboard._football_data_team_tla(away)
            home_aliases = SportsDashboard._football_data_team_aliases(home, home_tla)
            away_aliases = SportsDashboard._football_data_team_aliases(away, away_tla)
            score = item.get("score") or {}
            fulltime = score.get("fullTime") or score.get("fulltime") or {}
            parsed.append(
                {
                    "start": start_time,
                    "state": str(item.get("status") or "").upper(),
                    "status": str(item.get("status") or "").strip(),
                    "team_a": SportsDashboard._localized_country_name(home, home_tla),
                    "team_b": SportsDashboard._localized_country_name(away, away_tla),
                    "team_a_tla": home_tla,
                    "team_b_tla": away_tla,
                    "team_a_source_name": home_aliases[0] if home_aliases else home_tla,
                    "team_b_source_name": away_aliases[0] if away_aliases else away_tla,
                    "team_a_source_aliases": home_aliases,
                    "team_b_source_aliases": away_aliases,
                    "team_a_flag": SportsDashboard._flag_url_for_tla(home_tla),
                    "team_b_flag": SportsDashboard._flag_url_for_tla(away_tla),
                    "wins_a": SportsDashboard._first_number(fulltime.get("home")),
                    "wins_b": SportsDashboard._first_number(fulltime.get("away")),
                    "block": SportsDashboard._clean_football_data_stage(item.get("group") or item.get("stage")),
                }
            )
        return sorted(parsed, key=lambda item: item["start"])

    @staticmethod
    def _football_data_team_tla(team):
        tla = SportsDashboard._canonical_country_tla(team.get("tla") or team.get("code"))
        if tla:
            return tla
        short_name = str(team.get("shortName") or "").strip().upper()
        if len(short_name) <= 3:
            return SportsDashboard._canonical_country_tla(short_name) or short_name
        return SportsDashboard._country_tla_for_value(team.get("shortName") or team.get("name"))

    @staticmethod
    def _football_data_team_aliases(team, tla):
        aliases = []
        for value in (team.get("name"), team.get("shortName"), team.get("tla"), team.get("code"), tla):
            text = str(value or "").strip()
            if text and text not in aliases:
                aliases.append(text)
        return aliases

    @staticmethod
    def _localized_country_name(team, tla):
        mapped = FIFA_TLA_TO_ZH_NAME.get(SportsDashboard._canonical_country_tla(tla))
        if mapped:
            return mapped
        values = []
        if isinstance(team, Mapping):
            values.extend(
                team.get(key)
                for key in (
                    "shortName",
                    "shortDisplayName",
                    "displayName",
                    "name",
                    "country",
                    "code",
                    "tla",
                    "abbreviation",
                )
            )
        else:
            values.append(team)
        for value in values:
            alias_tla = SportsDashboard._country_tla_for_value(value)
            mapped = FIFA_TLA_TO_ZH_NAME.get(alias_tla)
            if mapped:
                return mapped
        fallback = str(
            (team.get("shortName") or team.get("name")) if isinstance(team, Mapping) else team
            or tla
            or "TBD"
        ).strip()
        return fallback or "TBD"

    @staticmethod
    def _flag_url_for_tla(tla):
        canonical_tla = SportsDashboard._canonical_country_tla(tla)
        if canonical_tla in LOCAL_WORLDCUP_FLAG_TLAS:
            return f"{LOCAL_WORLDCUP_FLAG_URL_PREFIX}{canonical_tla.lower()}"
        country_code = FIFA_TLA_TO_FLAGS_API_CODE.get(canonical_tla)
        if not country_code:
            return ""
        return FLAG_IMAGE_URL_TEMPLATE.format(country_code=country_code, country_code_lower=country_code.lower())

    @staticmethod
    def _canonical_country_tla(value):
        text = str(value or "").strip().upper()
        if not text:
            return ""
        if text in FIFA_TLA_EQUIVALENTS:
            return FIFA_TLA_EQUIVALENTS[text]
        if text in FIFA_TLA_TO_ZH_NAME:
            return text
        return FIFA_COUNTRY_ALIAS_TO_TLA.get(_normalize_country_alias(text), "")

    @staticmethod
    def _country_tla_for_value(value):
        text = str(value or "").strip()
        if not text:
            return ""
        if text in FIFA_ZH_NAME_TO_TLA:
            return FIFA_ZH_NAME_TO_TLA[text]
        return SportsDashboard._canonical_country_tla(text) or FIFA_COUNTRY_ALIAS_TO_TLA.get(
            _normalize_country_alias(text),
            "",
        )

    @staticmethod
    def _country_aliases_for_value(value):
        tla = SportsDashboard._country_tla_for_value(value)
        if not tla:
            return []
        aliases = [tla]
        aliases.extend(FIFA_TLA_TO_NAME_ALIASES.get(tla, ()))
        for alias_tla, canonical_tla in FIFA_TLA_EQUIVALENTS.items():
            if canonical_tla == tla:
                aliases.append(alias_tla)
        return aliases

    @staticmethod
    def _clean_football_data_stage(value):
        text = str(value or "World Cup").strip().replace("_", " ")
        if not text:
            return "World Cup"
        return " ".join(part.capitalize() for part in text.split())

    @staticmethod
    def _parse_worldcup_api_events(fixtures, timezone_info):
        parsed = []
        for item in fixtures or []:
            fixture = item.get("fixture") or {}
            start_time = SportsDashboard._parse_start_time(fixture.get("date"), timezone_info)
            if not start_time:
                continue
            teams = item.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            home_tla = SportsDashboard._api_team_tla(home)
            away_tla = SportsDashboard._api_team_tla(away)
            status = fixture.get("status") or {}
            league = item.get("league") or {}
            goals = item.get("goals") or {}
            score = item.get("score") or {}
            fulltime = score.get("fulltime") or {}
            parsed.append(
                {
                    "fixture_id": str(fixture.get("id") or "").strip(),
                    "start": start_time,
                    "state": str(status.get("short") or "").upper(),
                    "status": str(status.get("long") or "").strip(),
                    "elapsed": status.get("elapsed"),
                    "team_a": SportsDashboard._api_team_name(home),
                    "team_b": SportsDashboard._api_team_name(away),
                    "team_a_tla": home_tla,
                    "team_b_tla": away_tla,
                    "team_a_source_name": str(home.get("name") or home_tla or "").strip(),
                    "team_b_source_name": str(away.get("name") or away_tla or "").strip(),
                    "team_a_source_aliases": SportsDashboard._api_team_aliases(home),
                    "team_b_source_aliases": SportsDashboard._api_team_aliases(away),
                    "team_a_flag": SportsDashboard._flag_url_for_tla(home_tla),
                    "team_b_flag": SportsDashboard._flag_url_for_tla(away_tla),
                    "wins_a": SportsDashboard._first_number(goals.get("home"), fulltime.get("home")),
                    "wins_b": SportsDashboard._first_number(goals.get("away"), fulltime.get("away")),
                    "block": str(league.get("round") or "World Cup").strip(),
                }
            )
        return sorted(parsed, key=lambda item: item["start"])

    @staticmethod
    def _parse_worldcup_espn_events(payload, timezone_info):
        parsed = []
        for event in (payload or {}).get("events") or []:
            competitions = event.get("competitions") or []
            competition = competitions[0] if competitions else {}
            start_time = SportsDashboard._parse_start_time(
                competition.get("date") or event.get("date"),
                timezone_info,
            )
            if not start_time:
                continue
            away, home = SportsDashboard._nba_competitors_by_side(competition.get("competitors") or [])
            if not away or not home:
                continue
            state = SportsDashboard._worldcup_espn_event_state(event, competition)
            show_score = SportsDashboard._worldcup_state_has_score(state)
            team_a, team_a_name, team_a_tla, team_a_flag, wins_a, team_a_aliases = SportsDashboard._worldcup_espn_team_info(
                home,
                show_score,
            )
            team_b, team_b_name, team_b_tla, team_b_flag, wins_b, team_b_aliases = SportsDashboard._worldcup_espn_team_info(
                away,
                show_score,
            )
            source_url = SportsDashboard._worldcup_espn_event_url(event, competition)
            parsed.append(
                {
                    "event_id": str(event.get("id") or competition.get("id") or "").strip(),
                    "start": start_time,
                    "state": state,
                    "status": SportsDashboard._worldcup_espn_status_text(event, competition, start_time),
                    "elapsed": SportsDashboard._worldcup_espn_elapsed(event, competition),
                    "team_a": team_a,
                    "team_b": team_b,
                    "team_a_tla": team_a_tla,
                    "team_b_tla": team_b_tla,
                    "team_a_source_name": team_a_name,
                    "team_b_source_name": team_b_name,
                    "team_a_source_aliases": team_a_aliases,
                    "team_b_source_aliases": team_b_aliases,
                    "team_a_flag": team_a_flag,
                    "team_b_flag": team_b_flag,
                    "wins_a": wins_a,
                    "wins_b": wins_b,
                    "block": SportsDashboard._worldcup_espn_event_block(event, competition),
                    "score_source": "ESPN",
                    "provider": "ESPN",
                    "source_url": source_url,
                    "provider_status_confirmed": state in WORLD_CUP_LIVE_STATES.union(WORLD_CUP_FINISHED_STATES),
                    "score_confirmed": show_score and wins_a is not None and wins_b is not None,
                }
            )
        parsed = sorted(parsed, key=lambda item: item["start"])
        unique = []
        seen = set()
        for event in parsed:
            key = event.get("event_id") or f"{event['start'].isoformat()}|{event['team_a']}|{event['team_b']}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(event)
        return unique

    @staticmethod
    def _worldcup_espn_event_url(event, competition):
        choices = []

        def add_links(container):
            for link in ((container or {}).get("links") or []):
                href = str((link or {}).get("href") or "").strip()
                if not href:
                    continue
                rel = (link or {}).get("rel") or []
                if isinstance(rel, str):
                    rel = [rel]
                rel_text = " ".join(str(item or "").lower() for item in rel)
                label = " ".join(
                    str((link or {}).get(key) or "").lower()
                    for key in ("text", "shortText", "title")
                )
                searchable = f"{rel_text} {label} {href.lower()}"
                priority = 100
                for index, token in enumerate(("summary", "gamecast", "match", "recap", "boxscore")):
                    if token in searchable:
                        priority = index
                        break
                if "espn.com" not in href.lower():
                    priority += 25
                if "api." in href.lower():
                    priority += 25
                choices.append((priority, href))

        add_links(event)
        add_links(competition)
        if not choices:
            return ""
        choices.sort(key=lambda item: item[0])
        return choices[0][1]

    @staticmethod
    def _worldcup_espn_team_info(competitor, show_score):
        team = (competitor or {}).get("team") or {}
        source_name = str(team.get("displayName") or team.get("name") or team.get("shortDisplayName") or "").strip()
        tla = SportsDashboard._espn_country_tla(team, source_name)
        localized = SportsDashboard._localized_country_name(
            {"shortName": team.get("shortDisplayName"), "name": source_name},
            tla,
        )
        score = SportsDashboard._lpl_int_value((competitor or {}).get("score")) if show_score else None
        return (
            localized,
            source_name or tla,
            tla,
            SportsDashboard._flag_url_for_tla(tla) or str(team.get("logo") or "").strip(),
            score,
            SportsDashboard._worldcup_espn_team_aliases(team, tla),
        )

    @staticmethod
    def _worldcup_espn_team_aliases(team, tla):
        aliases = []
        for value in (
            team.get("displayName"),
            team.get("name"),
            team.get("shortDisplayName"),
            team.get("abbreviation"),
            tla,
        ):
            text = str(value or "").strip()
            if text and text not in aliases:
                aliases.append(text)
        return aliases

    @staticmethod
    def _worldcup_espn_event_state(event, competition):
        status = (competition or {}).get("status") or (event or {}).get("status") or {}
        status_type = status.get("type") or {}
        state = str(status_type.get("state") or "").strip().lower()
        name = str(status_type.get("name") or "").strip().upper()
        description = str(status_type.get("description") or "").strip().upper()
        detail = str(status_type.get("detail") or status_type.get("shortDetail") or "").strip().upper()
        combined = " ".join(part for part in (name, description, detail) if part)
        if status_type.get("completed") is True or state == "post" or "FULL_TIME" in combined or combined in {"FT", "FINAL"}:
            if "PEN" in combined:
                return "PEN"
            if "AET" in combined or "EXTRA" in combined:
                return "AET"
            return "FT"
        if "POSTPONED" in combined:
            return "POSTPONED"
        # Scheduled games expose their kickoff as a human-readable date in ``detail`` (e.g.
        # "Sun, June 21st at 12:00 PM EDT"). The ordinal day suffixes ("1st"/"21st" -> "1ST",
        # "2nd"/"22nd" -> "2ND") would otherwise be misread as first/second half below, marking
        # upcoming fixtures as live. Only derive in-play sub-states once ESPN actually reports
        # the match as in progress; anything else stays scheduled ("TIMED").
        in_progress = (
            state == "in"
            or "IN_PROGRESS" in name
            or "IN PROGRESS" in combined
            or "STATUS_LIVE" in name
            or "FIRST_HALF" in name
            or "SECOND_HALF" in name
            or "HALFTIME" in name
            or "HALF_TIME" in name
            or "EXTRA_TIME" in name
            or "SHOOTOUT" in name
            or "PENALT" in name
        )
        if not in_progress:
            return "TIMED"
        if "HALF_TIME" in combined or "HALFTIME" in combined or combined == "HT":
            return "HT"
        if "SECOND_HALF" in combined or "2ND" in combined:
            return "2H"
        if "FIRST_HALF" in combined or "1ST" in combined:
            return "1H"
        if "PEN" in combined:
            return "P"
        if "EXTRA" in combined:
            return "ET"
        return "LIVE"

    @staticmethod
    def _worldcup_espn_status_text(event, competition, start_time):
        status = (competition or {}).get("status") or (event or {}).get("status") or {}
        status_type = status.get("type") or {}
        state = SportsDashboard._worldcup_espn_event_state(event, competition)
        if state in WORLD_CUP_LIVE_STATES.union(WORLD_CUP_FINISHED_STATES):
            text = str(
                status_type.get("shortDetail")
                or status_type.get("detail")
                or status_type.get("description")
                or state
            ).strip()
            return text or state
        return SportsDashboard._format_time_24h(start_time)

    @staticmethod
    def _worldcup_espn_elapsed(event, competition):
        status = (competition or {}).get("status") or (event or {}).get("status") or {}
        value = SportsDashboard._lpl_int_value(status.get("period"))
        detail = str((status.get("type") or {}).get("shortDetail") or (status.get("type") or {}).get("detail") or "").strip()
        match = re.search(r"\b(\d{1,3})(?:\+(\d{1,2}))?['’]?", detail)
        if match:
            value = SportsDashboard._lpl_int_value(match.group(1))
        return value

    @staticmethod
    def _worldcup_espn_event_block(event, competition):
        # ESPN only carries the group/stage label in competition.altGameNote
        # (e.g. "FIFA World Cup, Group A"); season.slug is just "group-stage",
        # so the note is the only source of the group letter used for standings.
        note = str((competition or {}).get("altGameNote") or "").strip()
        if note:
            cleaned = re.sub(r"^FIFA\s+World\s+Cup[\s,\-]+", "", note, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r"^World\s+Cup[\s,\-]+", "", cleaned, flags=re.IGNORECASE).strip()
            if cleaned:
                return cleaned
        competition_type = (competition or {}).get("type") or {}
        value = str(competition_type.get("abbreviation") or competition_type.get("text") or "").strip()
        if value:
            return value.upper()
        season = (event or {}).get("season") or {}
        season_slug = str(season.get("slug") or season.get("type") or "").strip().replace("-", " ")
        return season_slug.upper() if season_slug else "World Cup"

    @staticmethod
    def _worldcup_state_has_score(state):
        return str(state or "").strip().upper() in WORLD_CUP_LIVE_STATES.union(WORLD_CUP_FINISHED_STATES)

    @staticmethod
    def _merge_worldcup_scoreboard_events(events, scoreboard_events):
        merged = []
        attached_count = 0
        for event in events or []:
            next_event = dict(event)
            matched = SportsDashboard._match_worldcup_scoreboard_event(event, scoreboard_events)
            if matched:
                scoreboard_event, reversed_order = matched
                SportsDashboard._apply_worldcup_scoreboard_event(next_event, scoreboard_event, reversed_order)
                attached_count += 1
            merged.append(next_event)
        return merged, attached_count

    @staticmethod
    def _match_worldcup_scoreboard_event(event, scoreboard_events):
        team_a_aliases = SportsDashboard._worldcup_event_team_aliases(event, "a")
        team_b_aliases = SportsDashboard._worldcup_event_team_aliases(event, "b")
        for candidate in scoreboard_events or []:
            if not SportsDashboard._worldcup_score_time_matches(event.get("start"), candidate.get("start")):
                continue
            candidate_a_matches_a = SportsDashboard._worldcup_candidate_team_matches_aliases(candidate, "a", team_a_aliases)
            candidate_b_matches_b = SportsDashboard._worldcup_candidate_team_matches_aliases(candidate, "b", team_b_aliases)
            if candidate_a_matches_a and candidate_b_matches_b:
                return candidate, False
            candidate_a_matches_b = SportsDashboard._worldcup_candidate_team_matches_aliases(candidate, "a", team_b_aliases)
            candidate_b_matches_a = SportsDashboard._worldcup_candidate_team_matches_aliases(candidate, "b", team_a_aliases)
            if candidate_a_matches_b and candidate_b_matches_a:
                return candidate, True
        return None

    @staticmethod
    def _worldcup_candidate_team_matches_aliases(event, side, aliases):
        candidate_aliases = SportsDashboard._worldcup_event_team_aliases(event, side)
        return bool(candidate_aliases.intersection(aliases or set()))

    @staticmethod
    def _apply_worldcup_scoreboard_event(event, scoreboard_event, reversed_order):
        if reversed_order:
            event["wins_a"] = scoreboard_event.get("wins_b")
            event["wins_b"] = scoreboard_event.get("wins_a")
            event["team_a_flag"] = event.get("team_a_flag") or scoreboard_event.get("team_b_flag", "")
            event["team_b_flag"] = event.get("team_b_flag") or scoreboard_event.get("team_a_flag", "")
        else:
            event["wins_a"] = scoreboard_event.get("wins_a")
            event["wins_b"] = scoreboard_event.get("wins_b")
            event["team_a_flag"] = event.get("team_a_flag") or scoreboard_event.get("team_a_flag", "")
            event["team_b_flag"] = event.get("team_b_flag") or scoreboard_event.get("team_b_flag", "")
        for key in ("state", "status", "elapsed", "score_source", "provider", "source_url"):
            value = scoreboard_event.get(key)
            if value is not None and str(value).strip():
                event[key] = value
        for key in ("provider_status_confirmed", "score_confirmed"):
            if key in scoreboard_event:
                event[key] = bool(scoreboard_event.get(key))
        if scoreboard_event.get("event_id"):
            event["scoreboard_event_id"] = scoreboard_event["event_id"]

    @staticmethod
    def _worldcup_score_time_matches(event_start, score_start):
        if not event_start or not score_start:
            return True
        try:
            event_utc = event_start.astimezone(timezone.utc)
            score_utc = score_start.astimezone(timezone.utc)
        except (AttributeError, ValueError):
            return True
        return abs((event_utc - score_utc).total_seconds()) <= 6 * 60 * 60

    @staticmethod
    def _worldcup_combined_score_source_state(base_state, score_state):
        base = str(base_state or "API").strip().upper()
        score = str(score_state or "").strip().upper()
        if not score:
            return base
        if "ESPN" in base:
            return base
        return f"{base} + {score}"

    @staticmethod
    def _api_team_name(team):
        return SportsDashboard._localized_country_name(team, SportsDashboard._api_team_tla(team))

    @staticmethod
    def _api_team_aliases(team):
        aliases = []
        for value in (team.get("name"), team.get("shortName"), team.get("country"), team.get("code"), team.get("tla")):
            text = str(value or "").strip()
            if text and text not in aliases:
                aliases.append(text)
        for value in list(aliases):
            for alias in SportsDashboard._country_aliases_for_value(value):
                if alias and alias not in aliases:
                    aliases.append(alias)
        return aliases

    @staticmethod
    def _api_team_tla(team):
        for key in ("code", "tla", "abbreviation"):
            tla = SportsDashboard._canonical_country_tla((team or {}).get(key))
            if tla:
                return tla
        return SportsDashboard._country_tla_for_value(
            (team or {}).get("name")
            or (team or {}).get("shortName")
            or (team or {}).get("country")
        )

    @staticmethod
    def _espn_country_tla(team, source_name):
        for key in ("abbreviation", "code", "tla"):
            tla = SportsDashboard._canonical_country_tla((team or {}).get(key))
            if tla:
                return tla
        return SportsDashboard._country_tla_for_value(
            source_name
            or (team or {}).get("shortDisplayName")
            or (team or {}).get("displayName")
            or (team or {}).get("name")
        )

    @staticmethod
    def _first_number(*values):
        for value in values:
            if value is not None:
                return value
        return None

    @staticmethod
    def _select_worldcup_events(events, now, visible_matches):
        selected = SportsDashboard._select_worldcup_event_sections(events, now, visible_matches)
        if not selected:
            return []
        main = selected.get("main")
        rows = []
        if main:
            rows.append(main)
        for event in selected.get("upcoming") or []:
            if event is not main and event not in rows:
                rows.append(event)
        for event in selected.get("recent") or []:
            if event is not main and event not in rows:
                rows.append(event)
        return rows[: selected.get("visible_matches", visible_matches)]

    @staticmethod
    def _select_worldcup_event_sections(events, now, visible_matches):
        events = list(events or [])
        if not events:
            return None
        visible_matches = max(1, min(WORLD_CUP_VISIBLE_MATCH_LIMIT, int(visible_matches or DEFAULT_WORLD_CUP_VISIBLE_MATCHES)))
        for event in events:
            if isinstance(event, MutableMapping):
                event.pop("inferred_live", None)
        live = []
        for event in events:
            if SportsDashboard._is_worldcup_live_event(event, now):
                live.append(event)
                continue
            if SportsDashboard._is_worldcup_inferred_live_event(event, now):
                if isinstance(event, MutableMapping):
                    event["inferred_live"] = True
                live.append(event)
        if live:
            live = sorted(
                live,
                key=lambda item: item["start"] if isinstance(item.get("start"), datetime) else datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
        SportsDashboard._annotate_worldcup_group_points(events)
        upcoming = [
            event for event in events
            if event not in live
            and not SportsDashboard._is_worldcup_finished_event(event)
            and event["start"] >= now
        ]
        recent = sorted(
            [event for event in events if event not in live and (SportsDashboard._is_worldcup_finished_event(event) or event["start"] < now)],
            key=lambda item: item["start"],
            reverse=True,
        )
        main = live[0] if live else (upcoming[0] if upcoming else (recent[0] if recent else None))
        return {
            "live": live,
            "upcoming": upcoming,
            "recent": recent,
            "main": main,
            "visible_matches": visible_matches,
        }

    @staticmethod
    def _should_poll_worldcup_live_data(events, now):
        return any(SportsDashboard._is_worldcup_live_poll_candidate(event, now) for event in events or [])

    @staticmethod
    def _is_worldcup_live_poll_candidate(event, now):
        if SportsDashboard._is_worldcup_live_event(event):
            return True
        if SportsDashboard._is_worldcup_finished_event(event):
            return False
        start = (event or {}).get("start")
        if not isinstance(start, datetime) or now is None:
            return False
        return start - WORLD_CUP_LIVE_PREGAME_WINDOW <= now < start + WORLD_CUP_INFERRED_LIVE_WINDOW

    @staticmethod
    def _is_worldcup_inferred_live_event(event, now):
        if SportsDashboard._is_worldcup_live_event(event, now) or SportsDashboard._is_worldcup_finished_event(event):
            return False
        start = (event or {}).get("start")
        if not isinstance(start, datetime) or now is None:
            return False
        if not (start <= now < start + WORLD_CUP_INFERRED_LIVE_WINDOW):
            return False
        return True

    @staticmethod
    def _worldcup_is_display_live(event, now=None):
        return SportsDashboard._is_worldcup_live_event(event, now) or bool((event or {}).get("inferred_live"))

    @staticmethod
    def _is_worldcup_live_event(event, now=None):
        if str((event or {}).get("state") or "").strip().upper() not in WORLD_CUP_LIVE_STATES:
            return False
        if now is None:
            return True
        start = (event or {}).get("start")
        if not isinstance(start, datetime):
            return True
        return now < start + WORLD_CUP_INFERRED_LIVE_WINDOW

    @staticmethod
    def _is_worldcup_finished_event(event):
        return str((event or {}).get("state") or "").strip().upper() in WORLD_CUP_FINISHED_STATES

    @staticmethod
    def _worldcup_live_refresh_seconds(settings):
        return SportsDashboard._int_setting(settings, "worldCupLiveRefreshSeconds", DEFAULT_WORLD_CUP_LIVE_REFRESH_SECONDS, 30, 900)

    def _render_worldcup_api_panel(self, dimensions, events, source_state, fetched_at, visible_matches, now):
        image = Image.new("RGB", dimensions, COLORS["paper"])
        draw = ImageDraw.Draw(image)
        width, height = dimensions
        visible_matches = max(1, min(WORLD_CUP_VISIBLE_MATCH_LIMIT, int(visible_matches or DEFAULT_WORLD_CUP_VISIBLE_MATCHES)))
        selected = events if isinstance(events, Mapping) else self._select_worldcup_event_sections(events, now, visible_matches)
        if not selected:
            selected = {"live": [], "upcoming": [], "recent": [], "main": None, "visible_matches": visible_matches}
        self._draw_worldcup_compact_panel(
            image,
            draw,
            (0, 0, width - 1, height - 1),
            selected,
            source_state,
            fetched_at,
            now,
        )
        return image

    def _draw_worldcup_compact_panel(self, image, draw, bounds, selected, source_state, fetched_at, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        panel_w = x2 - x1 + 1
        panel_h = y2 - y1 + 1
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["paper"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["worldcup_accent"], COLORS["paper"], 20, 1)

        header_y = y1 + 8
        logo_size = 30
        self._draw_worldcup_logo(image, draw, x1 + 14, header_y - 1, logo_size)
        title_year = self._worldcup_title_year(selected)
        title, title_font = self._fit_text(draw, f"{title_year} World Cup", 178, 20, bold=True, min_size=15)
        draw.text((x1 + 52, header_y + 1), title, font=title_font, fill=COLORS["text"])
        source = self._worldcup_api_source_label(source_state, fetched_at)
        source_text, source_font = self._fit_text(draw, source, 140, 9, bold=True, min_size=7)
        draw.text((x1 + 52, header_y + 24), source_text, font=source_font, fill=COLORS["muted"])
        self._draw_worldcup_header_banner(image, x1 + 225, y1, x2 - 90, y1 + 47)

        live = selected.get("live") or []
        upcoming = selected.get("upcoming") or []
        recent = selected.get("recent") or []
        main_event = selected.get("main")
        main_mode = self._worldcup_main_mode(selected, main_event)
        is_live = main_mode == "live"
        pill_label = "LIVE" if is_live else ("RECENT" if main_mode == "recent" else "NEXT")
        self._draw_status_pill(draw, x2 - 84, header_y + 4, pill_label, is_live)
        draw.line((x1 + 12, y1 + 48, x2 - 12, y1 + 48), fill=COLORS["border"], width=1)

        content_y = y1 + 57
        content_bottom = y2 - 8
        split_x = x1 + max(250, min(296, int(panel_w * 0.50)))
        left_x1 = x1 + 12
        left_x2 = split_x - 10
        right_x1 = split_x + 4
        right_x2 = x2 - 12
        if panel_h < 190:
            content_y = y1 + 53
            content_bottom = y2 - 6
        draw.line((split_x - 3, content_y - 5, split_x - 3, content_bottom), fill=COLORS["border"], width=1)
        draw.line((split_x - 1, content_y - 5, split_x - 1, content_bottom), fill=COLORS["line"], width=1)

        self._draw_worldcup_main_card(image, draw, left_x1, content_y, left_x2, content_bottom, main_event, now, main_mode)

        visible_matches = max(1, int(selected.get("visible_matches") or DEFAULT_WORLD_CUP_VISIBLE_MATCHES))
        upcoming_rows = [event for event in upcoming if event is not main_event]
        recent_rows = [event for event in recent if event is not main_event]
        if recent_rows:
            upcoming_rows = upcoming_rows[:2]
            recent_rows = recent_rows[:1]
        else:
            upcoming_rows = upcoming_rows[: max(0, visible_matches - 1)]

        upcoming_y = content_y
        upcoming_used_bottom = self._draw_worldcup_mini_rows(
            image,
            draw,
            right_x1,
            right_x2,
            upcoming_y,
            content_bottom,
            "UPCOMING",
            upcoming_rows,
            show_time=True,
        )
        if recent_rows:
            recent_y = max(upcoming_used_bottom + 1, content_bottom - 53)
            self._draw_worldcup_recent_rows(
                image,
                draw,
                right_x1,
                right_x2,
                recent_y,
                content_bottom,
                recent_rows,
            )
        else:
            self._draw_worldcup_tactics_strip(image, draw, right_x1, right_x2, upcoming_used_bottom + 2, content_bottom, main_event)

    def _attach_worldcup_standings_points(self, events, settings):
        """Overlay authoritative cumulative group PTS from ESPN's standings feed.

        Writes the ``team_{side}_standing_points`` slot, which the points label
        prefers over the locally-tallied points. Fails soft: on any error the
        local 3/1/0 tally in ``_annotate_worldcup_group_points`` still applies.
        """
        if not events:
            return events
        try:
            payload = self._load_worldcup_standings(settings)
            lookup = self._parse_worldcup_standings(payload)
            record_lookup = self._parse_worldcup_standings_records(payload)
            if lookup or record_lookup:
                self._apply_worldcup_standings(events, lookup, record_lookup)
        except Exception as exc:
            logger.warning("World Cup standings overlay failed: %s", _safe_exception_text(exc))
        return events

    def _load_worldcup_standings(self, settings):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._sports_dashboard_cache_dir() / "worldcup_standings.json"
        cache = self._read_json_file(cache_path)
        url = str(settings.get("worldCupStandingsUrl") or DEFAULT_WORLD_CUP_STANDINGS_URL).strip() or DEFAULT_WORLD_CUP_STANDINGS_URL
        cache_key = "|".join([WORLD_CUP_STANDINGS_STATE_VERSION, url])
        has_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("standings"), Mapping)
        cache_hours = self._int_setting(settings, "worldCupStandingsCacheHours", DEFAULT_WORLD_CUP_STANDINGS_CACHE_HOURS, 1, 24)
        if has_cache and not self._force_refresh_requested(settings) and self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return cache["standings"]
        try:
            response = get_http_session().get(
                url,
                params={"season": DEFAULT_WORLD_CUP_SEASON},
                headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"},
                timeout=20,
            )
            response.raise_for_status()
            payload = {
                "version": WORLD_CUP_STANDINGS_STATE_VERSION,
                "cache_key": cache_key,
                "fetched_at": now_utc.isoformat(),
                "standings": response.json(),
            }
        except Exception as exc:
            logger.warning("ESPN World Cup standings fetch failed: %s", _safe_exception_text(exc))
            return cache["standings"] if has_cache else {}
        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write ESPN World Cup standings cache: %s", exc)
        return payload["standings"]

    @staticmethod
    def _parse_worldcup_standings(payload):
        """Build {(group_key, team_alias_upper): points} from the ESPN standings feed."""
        lookup = {}
        if not isinstance(payload, Mapping):
            return lookup
        for group in payload.get("children") or []:
            group_key = SportsDashboard._worldcup_explicit_group_key({"block": str((group or {}).get("name") or "")})
            if not group_key:
                continue
            entries = ((group or {}).get("standings") or {}).get("entries") or []
            for entry in entries:
                team = (entry or {}).get("team") or {}
                points = None
                for stat in (entry or {}).get("stats") or []:
                    if str((stat or {}).get("name") or "") == "points":
                        points = stat.get("value")
                        if points is None:
                            points = stat.get("displayValue")
                        break
                if points is None:
                    continue
                try:
                    points_int = int(round(float(points)))
                except (TypeError, ValueError):
                    continue
                for alias in (team.get("abbreviation"), team.get("displayName"), team.get("shortDisplayName"), team.get("name")):
                    alias = str(alias or "").strip().upper()
                    if alias:
                        lookup[(group_key, alias)] = points_int
        return lookup


    @staticmethod
    def _worldcup_standings_stat_int(stats, names):
        wanted = {str(name).strip().lower() for name in names}
        for stat in stats or []:
            stat = stat or {}
            candidates = {
                str(stat.get("name") or "").strip().lower(),
                str(stat.get("abbreviation") or "").strip().lower(),
                str(stat.get("displayName") or "").strip().lower(),
                str(stat.get("shortDisplayName") or "").strip().lower(),
            }
            if not candidates.intersection(wanted):
                continue
            for key in ("value", "displayValue"):
                value = stat.get(key)
                if value is None:
                    continue
                try:
                    return int(round(float(str(value).strip())))
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _parse_worldcup_standings_records(payload):
        """Build {(group_key, team_alias_upper): "W-D-L"} from ESPN standings."""
        lookup = {}
        if not isinstance(payload, Mapping):
            return lookup
        for group in payload.get("children") or []:
            group_key = SportsDashboard._worldcup_explicit_group_key({"block": str((group or {}).get("name") or "")})
            if not group_key:
                continue
            entries = ((group or {}).get("standings") or {}).get("entries") or []
            for entry in entries:
                team = (entry or {}).get("team") or {}
                stats = (entry or {}).get("stats") or []
                wins = SportsDashboard._worldcup_standings_stat_int(stats, ("wins", "win", "w"))
                draws = SportsDashboard._worldcup_standings_stat_int(stats, ("draws", "draw", "ties", "tie", "d", "t"))
                losses = SportsDashboard._worldcup_standings_stat_int(stats, ("losses", "loss", "l"))
                if wins is None or draws is None or losses is None:
                    continue
                record = f"{wins}-{draws}-{losses}"
                for alias in (team.get("abbreviation"), team.get("displayName"), team.get("shortDisplayName"), team.get("name")):
                    alias = str(alias or "").strip().upper()
                    if alias:
                        lookup[(group_key, alias)] = record
        return lookup

    @staticmethod
    def _apply_worldcup_standings(events, lookup, record_lookup=None):
        """Write authoritative PTS and W-D-L onto team standing slots."""
        lookup = lookup or {}
        record_lookup = record_lookup or {}
        if not lookup and not record_lookup:
            return
        for event in events or []:
            if not isinstance(event, MutableMapping):
                continue
            group_key = SportsDashboard._worldcup_explicit_group_key(event)
            if not group_key:
                continue
            for side in ("a", "b"):
                team_key = SportsDashboard._worldcup_group_team_key(event, side)
                if not team_key:
                    continue
                points = lookup.get((group_key, team_key))
                if points is not None:
                    event[f"team_{side}_standing_points"] = points
                record = record_lookup.get((group_key, team_key))
                if record:
                    event[f"team_{side}_standing_record"] = record

    @staticmethod
    def _annotate_worldcup_group_points(events):
        group_points = {}
        group_records = {}
        for event in events or []:
            group_key = SportsDashboard._worldcup_explicit_group_key(event)
            if not group_key:
                continue
            for side in ("a", "b"):
                team_key = SportsDashboard._worldcup_group_team_key(event, side)
                if team_key:
                    group_points.setdefault((group_key, team_key), 0)
                    group_records.setdefault((group_key, team_key), [0, 0, 0])

        for event in events or []:
            group_key = SportsDashboard._worldcup_explicit_group_key(event)
            if not group_key:
                continue
            if not SportsDashboard._is_worldcup_finished_event(event):
                continue
            wins_a = event.get("wins_a")
            wins_b = event.get("wins_b")
            if wins_a is None or wins_b is None:
                continue
            try:
                wins_a = int(wins_a)
                wins_b = int(wins_b)
            except (TypeError, ValueError):
                continue
            team_a_key = SportsDashboard._worldcup_group_team_key(event, "a")
            team_b_key = SportsDashboard._worldcup_group_team_key(event, "b")
            if not team_a_key or not team_b_key:
                continue
            record_a = group_records.setdefault((group_key, team_a_key), [0, 0, 0])
            record_b = group_records.setdefault((group_key, team_b_key), [0, 0, 0])
            if wins_a > wins_b:
                group_points[(group_key, team_a_key)] = group_points.get((group_key, team_a_key), 0) + 3
                record_a[0] += 1
                record_b[2] += 1
            elif wins_b > wins_a:
                group_points[(group_key, team_b_key)] = group_points.get((group_key, team_b_key), 0) + 3
                record_b[0] += 1
                record_a[2] += 1
            else:
                group_points[(group_key, team_a_key)] = group_points.get((group_key, team_a_key), 0) + 1
                group_points[(group_key, team_b_key)] = group_points.get((group_key, team_b_key), 0) + 1
                record_a[1] += 1
                record_b[1] += 1

        for event in events or []:
            group_key = SportsDashboard._worldcup_explicit_group_key(event)
            if not group_key:
                continue
            for side in ("a", "b"):
                team_key = SportsDashboard._worldcup_group_team_key(event, side)
                if not team_key:
                    continue
                if SportsDashboard._worldcup_group_points_value(event, side) is None:
                    points = group_points.get((group_key, team_key))
                    if points is not None:
                        event[f"team_{side}_group_points"] = points
                if not SportsDashboard._worldcup_group_record_value(event, side):
                    record = group_records.get((group_key, team_key))
                    if record is not None:
                        event[f"team_{side}_group_record"] = f"{record[0]}-{record[1]}-{record[2]}"

    @staticmethod
    def _worldcup_explicit_group_key(event):
        stage = SportsDashboard._clean_worldcup_stage((event or {}).get("block"))
        match = re.search(r"\bGroup\s+([A-L])\b", stage, re.IGNORECASE)
        if not match:
            return ""
        return f"Group {match.group(1).upper()}"

    @staticmethod
    def _worldcup_group_team_key(event, side):
        event = event or {}
        side = "a" if side == "a" else "b"
        for key in (f"team_{side}_tla", f"team_{side}_source_name", f"team_{side}"):
            value = str(event.get(key) or "").strip().upper()
            if value and value != "TBD":
                return value
        return ""

    @staticmethod
    def _worldcup_main_mode(selected, main_event):
        selected = selected or {}
        live = selected.get("live") or []
        upcoming = selected.get("upcoming") or []
        recent = selected.get("recent") or []
        if main_event and any(event is main_event for event in live):
            return "live"
        if main_event and any(event is main_event for event in recent) and not upcoming:
            return "recent"
        return "next"

    @staticmethod
    def _worldcup_title_year(selected, fallback=DEFAULT_WORLD_CUP_SEASON):
        if isinstance(selected, Mapping):
            configured = str(selected.get("season") or "").strip()
            if configured.isdigit() and len(configured) == 4:
                return configured
            candidates = [selected.get("main")]
            for key in ("live", "upcoming", "recent"):
                candidates.extend(selected.get(key) or [])
        else:
            candidates = list(selected or [])
        for event in candidates:
            start = (event or {}).get("start") if isinstance(event, Mapping) else None
            if isinstance(start, datetime):
                return str(start.year)
        return fallback

    def _draw_worldcup_main_card(self, image, draw, x1, y1, x2, y2, event, now, main_mode):
        is_live = main_mode == "live"
        is_recent = main_mode == "recent"
        if is_live:
            accent = COLORS["worldcup_live"]
        elif is_recent and event:
            accent = self._worldcup_status_color(event)
        else:
            accent = COLORS["worldcup_accent"]
        draw.rounded_rectangle((x1 + 3, y1 + 3, x2 + 3, y2 + 3), radius=5, fill=COLORS["worldcup_shadow"])
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, y2 - 1), fill=accent)
        if not event:
            message, message_font = self._fit_text(draw, "No World Cup schedule", x2 - x1 - 36, 15, bold=True, min_size=10)
            self._draw_centered(draw, ((x1 + x2) / 2, (y1 + y2) / 2), message, message_font, COLORS["text"])
            return

        tag = "NOW PLAYING" if is_live else ("RECENT RESULT" if is_recent else "NEXT MATCH")
        tag_w = 112 if is_recent else (104 if is_live else 88)
        tag_text, tag_font = self._fit_text(draw, tag, tag_w - 8, 11, bold=True, min_size=7)
        tag_fill = COLORS["worldcup_live"] if is_live else (COLORS["green"] if is_recent else COLORS["worldcup_tag"])
        draw.rectangle((x1 + 14, y1 + 9, x1 + 14 + tag_w, y1 + 27), fill=tag_fill, outline=COLORS["border"], width=1)
        draw.text((x1 + 18, y1 + 10), tag_text, font=tag_font, fill=COLORS["text"])
        date_text = event["start"].strftime("%m/%d")
        date_text, date_font = self._fit_text(draw, date_text, 52, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 11), date_text, date_font, COLORS["muted"])

        stage = self._clean_worldcup_stage(event.get("block"))
        stage_text, stage_font = self._fit_text(draw, stage, x2 - x1 - 34, 8, bold=True, min_size=6)
        draw.text((x1 + 18, y1 + 30), stage_text, font=stage_font, fill=COLORS["worldcup_accent"])

        status_text = self._worldcup_main_status_label(event, now)
        status_text, status_font = self._fit_text(draw, status_text, x2 - x1 - 42, 16, bold=True, min_size=10)
        self._draw_centered(draw, ((x1 + x2) / 2, y1 + 41), status_text, status_font, COLORS["text"])

        center_x = (x1 + x2) / 2
        flag_h, flag_cap = 27, 54
        left_area = (x1 + 18, center_x - 21)
        right_area = (center_x + 21, x2 - 18)
        flag_y = y1 + 58
        left_flag_x = int((left_area[0] + left_area[1] - flag_cap) / 2)
        right_flag_x = int((right_area[0] + right_area[1] - flag_cap) / 2)
        self._draw_worldcup_flag(image, draw, event.get("team_a_flag"), left_flag_x, flag_y, flag_cap, flag_h, event.get("team_a_tla"), align="center")
        self._draw_worldcup_flag(image, draw, event.get("team_b_flag"), right_flag_x, flag_y, flag_cap, flag_h, event.get("team_b_tla"), align="center")
        center_label = self._worldcup_score_or_vs(event)
        center_label, center_font = self._fit_text(draw, center_label, 64, 17, bold=True, min_size=11)
        self._draw_centered(draw, (center_x, flag_y + flag_h / 2 + 1), center_label, center_font, COLORS["text"])

        team_y = flag_y + flag_h + 16
        team_a, team_a_font = self._fit_text(draw, event.get("team_a"), left_area[1] - left_area[0], 16, bold=True, min_size=9)
        team_b, team_b_font = self._fit_text(draw, event.get("team_b"), right_area[1] - right_area[0], 16, bold=True, min_size=9)
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, team_a_font, COLORS["text"])
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, team_b_font, COLORS["text"])

        points_y = team_y + 13
        left_meta = self._worldcup_team_points_meta(event, "a")
        right_meta = self._worldcup_team_points_meta(event, "b")
        left_meta_left, left_meta_right = int(left_area[0]), int(left_area[1])
        right_meta_left, right_meta_right = int(right_area[0]), int(right_area[1])
        meta_width = max(1, min(left_meta_right - left_meta_left, right_meta_right - right_meta_left))
        self._draw_worldcup_odds_text(draw, (left_meta_left, points_y, left_meta_left + meta_width, points_y + 11), left_meta, max_size=8)
        self._draw_worldcup_odds_text(draw, (right_meta_right - meta_width, points_y, right_meta_right, points_y + 11), right_meta, max_size=8)

        if self._worldcup_event_has_odds(event):
            odds_y = points_y + 11
            odds = event.get("odds") or {}
            self._draw_worldcup_odds_text(draw, (left_area[0], odds_y, left_area[1], odds_y + 12), (event.get("odds") or {}).get("team_a"), max_size=9)
            if odds.get("draw"):
                self._draw_worldcup_odds_text(draw, (center_x - 26, odds_y, center_x + 26, odds_y + 12), f"X {odds.get('draw')}", max_size=9)
            self._draw_worldcup_odds_text(draw, (right_area[0], odds_y, right_area[1], odds_y + 12), (event.get("odds") or {}).get("team_b"), max_size=9)

    def _draw_worldcup_mini_rows(self, image, draw, x1, x2, y, bottom, title, events, show_time):
        self._draw_worldcup_mini_section_header(draw, x1, x2, y, title)
        if not events:
            message = "No more World Cup schedule" if title == "UPCOMING" else "No recent results"
            message, message_font = self._fit_text(draw, message, x2 - x1 - 16, 10, bold=True, min_size=7)
            draw.text((x1 + 10, y + 23), message, font=message_font, fill=COLORS["muted"])
            return y + 38
        row_y = y + 21
        row_h = 33
        max_rows = max(1, (bottom - row_y + 1) // (row_h + 2))
        rows = events[:max_rows]
        for index, event in enumerate(rows):
            center_text = "VS" if show_time else self._worldcup_score_or_vs(event)
            self._draw_worldcup_mini_match_row(
                image,
                draw,
                x1,
                x2,
                row_y + index * (row_h + 2),
                row_h,
                event,
                center_text,
                show_time=show_time,
            )
        return row_y + len(rows) * (row_h + 2) - 2

    def _draw_worldcup_mini_section_header(self, draw, x1, x2, y, title):
        draw.rectangle((x1, y + 2, x1 + 8, y + 17), fill=COLORS["worldcup_accent"], outline=COLORS["border"], width=1)
        draw.text((x1 + 13, y - 2), title, font=self._font(13, True), fill=COLORS["text"])
        draw.line((x1, y + 19, x2, y + 19), fill=COLORS["border"], width=1)

    def _draw_worldcup_recent_rows(self, image, draw, x1, x2, y, bottom, events):
        if bottom - y < 45:
            return y
        self._draw_worldcup_mini_section_header(draw, x1, x2, y, "RECENT")
        row_y = y + 20
        available = bottom - row_y + 1
        row_h = min(32, available)
        if row_h < 30:
            return row_y
        row_gap = 3
        max_rows = 1 + max(0, (available - row_h) // (row_h + row_gap))
        visible_events = list(events or [])[: min(2, max_rows)]
        if not visible_events:
            message, message_font = self._fit_text(draw, "No recent results", x2 - x1 - 16, 10, bold=True, min_size=7)
            draw.text((x1 + 10, y + 23), message, font=message_font, fill=COLORS["muted"])
            return row_y
        for index, event in enumerate(visible_events):
            self._draw_worldcup_recent_match_row(
                image,
                draw,
                x1,
                x2,
                row_y + index * (row_h + row_gap),
                row_h,
                event,
            )
        return row_y + len(visible_events) * (row_h + row_gap) - row_gap

    def _draw_worldcup_recent_match_row(self, image, draw, x1, x2, y, row_h, event):
        draw.rounded_rectangle((x1, y, x2, y + row_h), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + row_h - 1), fill=self._worldcup_status_color(event))

        center_x = (x1 + x2) / 2
        score_w = 48
        team_y1 = y + 8
        team_y2 = min(y + row_h - 10, y + 20)
        left_area = (x1 + 8, center_x - score_w / 2 - 4)
        right_area = (center_x + score_w / 2 + 4, x2 - 8)
        self._draw_worldcup_recent_team_identity(image, draw, event, "a", left_area, team_y1, team_y2)
        score = self._worldcup_score_or_vs(event)
        score, score_font = self._fit_text(draw, score, score_w, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (center_x - score_w / 2, team_y1, center_x + score_w / 2, team_y2), score, score_font, COLORS["text"])
        self._draw_worldcup_recent_team_identity(image, draw, event, "b", right_area, team_y1, team_y2)
        points_y = y + row_h - 10
        date_text, date_font = self._fit_text(draw, event["start"].strftime("%m/%d"), score_w - 4, 7, bold=True, min_size=6)
        self._draw_centered_in_box(draw, (center_x - score_w / 2, points_y, center_x + score_w / 2, y + row_h - 1), date_text, date_font, COLORS["muted"])
        left_meta = self._worldcup_team_points_meta(event, "a")
        right_meta = self._worldcup_team_points_meta(event, "b")
        self._draw_worldcup_odds_text(draw, (left_area[0], points_y, left_area[1], y + row_h - 1), left_meta, max_size=7)
        self._draw_worldcup_odds_text(draw, (right_area[0], points_y, right_area[1], y + row_h - 1), right_meta, max_size=7)

    def _draw_worldcup_recent_team_identity(self, image, draw, event, side, area, y1, y2):
        left, right = [int(value) for value in area]
        area_w = max(1, right - left)
        flag_h, flag_cap = 13, 26
        gap = 5
        side_key = "a" if side == "a" else "b"
        label = event.get(f"team_{side_key}")
        fallback = event.get(f"team_{side_key}_tla")
        flag_url = event.get(f"team_{side_key}_flag")
        flag_w = self._worldcup_flag_display_size(flag_url, fallback, flag_cap, flag_h)[0]
        max_text_w = max(24, area_w - flag_w - gap)
        label, font = self._fit_text(draw, label, max_text_w, 11, bold=True, min_size=7)
        text_w = min(max_text_w, self._text_width(draw, label, font))
        flag_y = int(y1 + max(0, ((y2 - y1) - flag_h) / 2))
        if side_key == "b":
            flag_x = int(right - flag_w)
            text_right = int(flag_x - gap)
            text_left = max(left, int(text_right - text_w))
            self._draw_text_in_box(
                draw,
                (text_left, y1, text_right, y2),
                label,
                font,
                COLORS["text"],
                align="right",
            )
        else:
            flag_x = int(left)
            text_left = int(flag_x + flag_w + gap)
            text_right = min(right, int(text_left + text_w))
            self._draw_text_in_box(
                draw,
                (text_left, y1, text_right, y2),
                label,
                font,
                COLORS["text"],
            )
        self._draw_worldcup_flag(image, draw, flag_url, flag_x, flag_y, flag_w, flag_h, fallback)

    def _draw_worldcup_mini_match_row(self, image, draw, x1, x2, y, row_h, event, center_text, show_time=False):
        draw.rounded_rectangle((x1, y, x2, y + row_h), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + row_h - 1), fill=self._worldcup_status_color(event))
        date_text, date_font = self._fit_text(draw, event["start"].strftime("%m/%d"), 36, 9, bold=True, min_size=7)
        draw.text((x1 + 9, y + 1), date_text, font=date_font, fill=COLORS["muted"])
        stage = self._clean_worldcup_stage(event.get("block"))
        stage_text, stage_font = self._fit_text(draw, stage, 78, 9, bold=True, min_size=7)
        draw.text((x1 + 47, y + 1), stage_text, font=stage_font, fill=COLORS["muted"])
        if show_time:
            time_text, time_font = self._fit_text(draw, self._worldcup_event_time_label(event), 48, 9, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 8, y + 1), time_text, time_font, COLORS["text"])
        self._draw_worldcup_row_lineup(image, draw, x1 + 9, x2 - 8, y + 13, event, center_text)

    def _draw_worldcup_row_lineup(self, image, draw, x1, x2, y, event, center_text):
        center_x = (x1 + x2) / 2
        flag_h, flag_cap = 14, 28
        left_flag_x = x1 + 1
        has_odds = center_text == "VS" and self._worldcup_event_has_odds(event)
        team_bottom = y + 11
        flag_y = y - 1
        left_w = self._draw_worldcup_flag(image, draw, event.get("team_a_flag"), left_flag_x, flag_y, flag_cap, flag_h, event.get("team_a_tla"), align="left")
        left_text_x = left_flag_x + left_w + 4
        team_a, font_a = self._fit_text(draw, event.get("team_a"), max(20, center_x - left_text_x - 4), 10, bold=True, min_size=7)
        self._draw_text_in_box(draw, (left_text_x, y - 1, center_x - 28, team_bottom), team_a, font_a, COLORS["text"])
        center_text, center_font = self._fit_text(draw, center_text, 52, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (center_x - 26, y - 1, center_x + 26, team_bottom), center_text, center_font, COLORS["text"])
        right_w = self._draw_worldcup_flag(image, draw, event.get("team_b_flag"), x2 - flag_cap - 1, flag_y, flag_cap, flag_h, event.get("team_b_tla"), align="right")
        right_text_x = x2 - 1 - right_w - 4
        team_b, font_b = self._fit_text(draw, event.get("team_b"), max(20, right_text_x - center_x - 28), 10, bold=True, min_size=7)
        self._draw_text_in_box(draw, (center_x + 28, y - 1, right_text_x, team_bottom), team_b, font_b, COLORS["text"], align="right")
        left_meta = self._worldcup_team_points_meta(event, "a", include_odds=has_odds)
        right_meta = self._worldcup_team_points_meta(event, "b", include_odds=has_odds)
        meta_margin = flag_cap + 5
        self._draw_worldcup_odds_text(draw, (x1 + meta_margin, y + 11, center_x - 28, y + 20), left_meta, max_size=7)
        self._draw_worldcup_odds_text(draw, (center_x + 28, y + 11, x2 - meta_margin, y + 20), right_meta, max_size=7)
        if has_odds:
            odds = event.get("odds") or {}
            if odds.get("draw"):
                self._draw_worldcup_odds_text(draw, (center_x - 21, y + 11, center_x + 21, y + 20), f"X / {odds.get('draw')}", max_size=7)

    def _draw_worldcup_tactics_strip(self, image, draw, x1, x2, y1, y2, event):
        x1 = int(x1)
        x2 = int(x2)
        y1 = int(y1)
        y2 = int(y2)
        if y2 - y1 < 12 or x2 - x1 < 80:
            return
        formation_pair = self._worldcup_formation_pair(event)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 5, y2 - 1), fill=COLORS["worldcup_accent"])
        if formation_pair:
            left_text, right_text = formation_pair
            center_x = (x1 + x2) / 2
            text_y = y1 + max(1, (y2 - y1 - 10) // 2)
            label, label_font = self._fit_text(draw, "\u9996\u53d1", 28, 9, bold=True, min_size=7)
            draw.text((x1 + 10, text_y), label, font=label_font, fill=COLORS["amber"])
            vs_text, vs_font = self._fit_text(draw, "VS", 18, 9, bold=True, min_size=7)
            self._draw_centered(draw, (center_x, text_y + 5), vs_text, vs_font, COLORS["text"])
            left_width = max(24, int(center_x - (x1 + 43) - 10))
            right_width = max(24, int((x2 - 8) - (center_x + 14)))
            left_text, left_font = self._fit_text(draw, left_text, left_width, 9, bold=True, min_size=6)
            draw.text((x1 + 43, text_y), left_text, font=left_font, fill=COLORS["text"])
            right_text, right_font = self._fit_text(draw, right_text, right_width, 9, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 8, text_y), right_text, right_font, COLORS["text"])
            return
        self._draw_worldcup_pitch_strip(image, draw, x1 + 6, y1 + 2, x2 - 1, y2)

    @staticmethod
    def _worldcup_formation_summary(event):
        formation_a = str((event or {}).get("formation_a") or "").strip()
        formation_b = str((event or {}).get("formation_b") or "").strip()
        if not formation_a or not formation_b:
            return ""
        return f"{formation_a} VS {formation_b}"

    @staticmethod
    def _worldcup_formation_pair(event):
        formation_a = str((event or {}).get("formation_a") or "").strip()
        formation_b = str((event or {}).get("formation_b") or "").strip()
        if not formation_a or not formation_b:
            return None
        team_a = SportsDashboard._worldcup_tactics_team_label(event, "a")
        team_b = SportsDashboard._worldcup_tactics_team_label(event, "b")
        return f"{team_a} {formation_a}", f"{formation_b} {team_b}"

    @staticmethod
    def _worldcup_tactics_team_label(event, side):
        team = str((event or {}).get(f"team_{side}") or "").strip()
        tla = str((event or {}).get(f"team_{side}_tla") or "").strip().upper()
        if team and len(team) <= 5:
            return team
        if tla:
            return tla
        return team[:5] if team else "TBD"

    def _draw_worldcup_pitch_strip(self, image, draw, x1, y1, x2, y2):
        x1 = int(x1)
        y1 = int(y1)
        x2 = int(x2)
        y2 = int(y2)
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        if width < 16 or height < 5:
            return
        pitch_strip = self._load_worldcup_pitch_strip((width, height))
        if pitch_strip:
            image.paste(pitch_strip, (x1, y1))
            return

        white = (255, 255, 255)
        black = (0, 0, 0)
        field = Image.new("RGB", (width, height), black)
        field_draw = ImageDraw.Draw(field)
        right = width - 1
        bottom = height - 1

        field_draw.rectangle((0, 0, right, bottom), outline=white, width=1)
        mid_x = width // 2
        field_draw.line((mid_x, 1, mid_x, bottom - 1), fill=white, width=1)
        field_draw.point((mid_x, height // 2), fill=white)
        if width >= 52 and height >= 9:
            circle_r = max(2, min(height // 3, width // 18))
            field_draw.ellipse(
                (mid_x - circle_r, height // 2 - circle_r, mid_x + circle_r, height // 2 + circle_r),
                outline=white,
                width=1,
            )

        box_w = max(5, min(width // 7, 24))
        box_h = max(3, height - 4)
        box_y1 = max(1, (height - box_h) // 2)
        box_y2 = min(bottom - 1, box_y1 + box_h)
        field_draw.rectangle((1, box_y1, box_w, box_y2), outline=white, width=1)
        field_draw.rectangle((right - box_w, box_y1, right - 1, box_y2), outline=white, width=1)
        if width >= 84 and height >= 10:
            six_w = max(3, box_w // 2)
            six_h = max(2, box_h // 2)
            six_y1 = max(1, (height - six_h) // 2)
            field_draw.rectangle((1, six_y1, six_w, six_y1 + six_h), outline=white, width=1)
            field_draw.rectangle((right - six_w, six_y1, right - 1, six_y1 + six_h), outline=white, width=1)

        player_size = 1 if height <= 12 else 2
        players = (
            (0.18, 0.38),
            (0.26, 0.68),
            (0.36, 0.28),
            (0.45, 0.58),
            (0.57, 0.35),
            (0.66, 0.72),
            (0.76, 0.45),
            (0.86, 0.64),
        )
        for px_frac, py_frac in players:
            px = max(2, min(right - 2, int(width * px_frac)))
            py = max(2, min(bottom - 2, int(height * py_frac)))
            if player_size == 1:
                field_draw.point((px, py), fill=white)
                field_draw.point((px, max(1, py - 1)), fill=white)
            else:
                field_draw.rectangle((px - 1, py - 1, px + 1, py + 1), fill=white)
                field_draw.point((px, max(1, py - 2)), fill=white)
        ball_x = max(3, min(right - 3, int(width * 0.31)))
        ball_y = max(2, min(bottom - 2, int(height * 0.48)))
        field_draw.point((ball_x, ball_y), fill=white)

        image.paste(field, (x1, y1))

    @staticmethod
    def _load_worldcup_pitch_strip(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_WORLDCUP_PITCH_STRIP_PATH
        cache_key = (path, (width, height), "worldcup-pitch-strip-v3")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                strip = source.convert("RGB")
            if strip.size != (width, height):
                strip = strip.resize((width, height), Image.NEAREST)
            TEAM_LOGO_CACHE[cache_key] = strip
            return strip
        except Exception as exc:
            logger.warning("Failed to load World Cup pitch strip %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _load_worldcup_header_banner(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_WORLDCUP_HEADER_BANNER_PATH
        cache_key = (path, (width, height), "worldcup-header-banner-v1")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                banner = source.convert("RGBA")
            if banner.size != (width, height):
                banner = banner.resize((width, height), Image.LANCZOS)
            TEAM_LOGO_CACHE[cache_key] = banner
            return banner
        except Exception as exc:
            logger.warning("Failed to load World Cup header banner %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    def _draw_worldcup_header_banner(self, image, x1, y1, x2, y2):
        x1 = int(x1)
        y1 = int(y1)
        x2 = int(x2)
        y2 = int(y2)
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        if width < 80 or height < 18:
            return
        banner = self._load_worldcup_header_banner((width, height))
        if banner:
            image.paste(banner, (x1, y1), banner)

    @staticmethod
    def _load_nba_court_strip(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_NBA_COURT_STRIP_PATH
        cache_key = (path, (width, height), "nba-court-strip-v2")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                strip = source.convert("RGBA")
            if strip.size != (width, height):
                strip = strip.resize((width, height), Image.NEAREST)
            TEAM_LOGO_CACHE[cache_key] = strip
            return strip
        except Exception as exc:
            logger.warning("Failed to load NBA court strip %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _sport_header_cutout_path(sport):
        paths = {
            "MLB": LOCAL_MLB_HEADER_CUTOUT_PATH,
            "WNBA": LOCAL_WNBA_HEADER_CUTOUT_PATH,
            "PGA": LOCAL_PGA_HEADER_CUTOUT_PATH,
            "NFL": LOCAL_NFL_HEADER_CUTOUT_PATH,
            "NCAA": LOCAL_NCAA_HEADER_CUTOUT_PATH,
        }
        return paths.get(str(sport or "").upper())

    @staticmethod
    def _load_sport_header_cutout(sport, size=None):
        target_size = None
        if size is not None:
            width, height = int(size[0]), int(size[1])
            if width <= 0 or height <= 0:
                return None
            target_size = (width, height)
        path = SportsDashboard._sport_header_cutout_path(sport)
        if not path:
            return None
        cache_key = (path, target_size, "sport-header-cutout-v2")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                cutout = source.convert("RGBA")
            if target_size is not None and cutout.size != target_size:
                cutout.thumbnail(target_size, Image.LANCZOS)
            TEAM_LOGO_CACHE[cache_key] = cutout
            return cutout
        except Exception as exc:
            logger.warning("Failed to load %s header cutout %s: %s", sport, path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _load_nba_empty_slot_filler(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_NBA_EMPTY_SLOT_FILLER_PATH
        cache_key = (path, (width, height), "nba-empty-slot-filler-v1")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                filler = source.convert("RGBA")
            if filler.size != (width, height):
                filler = ImageOps.fit(
                    filler,
                    (width, height),
                    method=Image.LANCZOS,
                    centering=(0.5, 0.5),
                )
            TEAM_LOGO_CACHE[cache_key] = filler
            return filler
        except Exception as exc:
            logger.warning("Failed to load NBA empty slot filler %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _load_nba_offseason_filler(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_NBA_OFFSEASON_FILLER_PATH
        cache_key = (path, (width, height), "nba-offseason-filler-v1")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                filler = source.convert("RGB")
            if filler.size != (width, height):
                filler = ImageOps.fit(
                    filler,
                    (width, height),
                    method=Image.LANCZOS,
                    centering=(0.5, 0.5),
                )
            TEAM_LOGO_CACHE[cache_key] = filler
            return filler
        except Exception as exc:
            logger.warning("Failed to load NBA offseason filler %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _load_nba_offseason_accent(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_NBA_OFFSEASON_ACCENT_PATH
        cache_key = (path, (width, height), "nba-offseason-accent-v1")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                accent = source.convert("RGBA")
            if accent.size != (width, height):
                accent = accent.resize((width, height), Image.LANCZOS)
            TEAM_LOGO_CACHE[cache_key] = accent
            return accent
        except Exception as exc:
            logger.warning("Failed to load NBA offseason accent %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _load_pga_fairway_strip(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_PGA_FAIRWAY_STRIP_PATH
        cache_key = (path, (width, height), "pga-fairway-strip-v3")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                strip = source.convert("RGBA")
            if strip.size != (width, height):
                strip = strip.resize((width, height), Image.LANCZOS)
            TEAM_LOGO_CACHE[cache_key] = strip
            return strip
        except Exception as exc:
            logger.warning("Failed to load PGA fairway strip %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _load_lpl_sidebar_filler(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_LPL_MARBLE_FILLER_PATH
        cache_key = (path, (width, height), "lpl-marble-filler-v1")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                filler = source.convert("RGBA")
            if filler.size != (width, height):
                filler = filler.resize((width, height), Image.LANCZOS)
            TEAM_LOGO_CACHE[cache_key] = filler
            return filler
        except Exception as exc:
            logger.warning("Failed to load LPL sidebar filler %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _load_lpl_msi_next_filler(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_LPL_MSI_NEXT_FILLER_PATH
        cache_key = (path, (width, height), "lpl-msi-next-filler-v1")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                filler = source.convert("RGB")
            if filler.size != (width, height):
                filler = ImageOps.fit(
                    filler,
                    (width, height),
                    method=Image.LANCZOS,
                    centering=(0.5, 0.5),
                )
            TEAM_LOGO_CACHE[cache_key] = filler
            return filler
        except Exception as exc:
            logger.warning("Failed to load LPL MSI next filler %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _load_lpl_msi_offseason_filler(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_LPL_MSI_OFFSEASON_FILLER_PATH
        cache_key = (path, (width, height), "lpl-msi-offseason-filler-v1")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                filler = source.convert("RGB")
            if filler.size != (width, height):
                filler = ImageOps.fit(
                    filler,
                    (width, height),
                    method=Image.LANCZOS,
                    centering=(0.5, 0.5),
                )
            TEAM_LOGO_CACHE[cache_key] = filler
            return filler
        except Exception as exc:
            logger.warning("Failed to load LPL MSI offseason filler %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _lpl_msi_card_accent_paths():
        paths = []
        if os.path.isdir(LOCAL_LPL_MSI_CARD_ACCENT_DIR):
            try:
                paths = sorted(
                    str(path)
                    for path in Path(LOCAL_LPL_MSI_CARD_ACCENT_DIR).glob("*.png")
                    if path.is_file()
                )
            except OSError as exc:
                logger.warning("Failed to list LPL MSI card accent pool %s: %s", LOCAL_LPL_MSI_CARD_ACCENT_DIR, exc)
        if paths:
            return tuple(paths)
        if os.path.exists(LOCAL_LPL_MSI_CARD_ACCENT_PATH):
            return (LOCAL_LPL_MSI_CARD_ACCENT_PATH,)
        return ()

    @staticmethod
    def _lpl_msi_card_accent_index(rotation_seed, count):
        if count <= 1:
            return 0
        try:
            if isinstance(rotation_seed, datetime):
                return int(rotation_seed.timestamp()) % count
            if rotation_seed is not None:
                return int(rotation_seed) % count
        except (TypeError, ValueError, OSError, OverflowError):
            pass
        return int(datetime.now(timezone.utc).timestamp()) % count

    @staticmethod
    def _load_lpl_msi_card_accent(size, rotation_seed=None):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        paths = SportsDashboard._lpl_msi_card_accent_paths()
        if not paths:
            return None
        path = paths[SportsDashboard._lpl_msi_card_accent_index(rotation_seed, len(paths))]
        cache_key = (path, (width, height), "lpl-msi-card-accent-v2")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        try:
            with Image.open(path) as source:
                accent = source.convert("RGBA")
            bbox = accent.getbbox()
            if bbox:
                accent = accent.crop(bbox)
            accent.thumbnail((width, height), Image.LANCZOS)
            fitted = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            fitted.alpha_composite(accent, ((width - accent.width) // 2, height - accent.height))
            accent = fitted
            TEAM_LOGO_CACHE[cache_key] = accent
            return accent
        except Exception as exc:
            logger.warning("Failed to load LPL MSI card accent %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _worldcup_event_has_odds(event):
        odds = (event or {}).get("odds") or {}
        return bool(odds.get("team_a") and odds.get("team_b"))

    @staticmethod
    def _worldcup_score_or_vs(event):
        if (event or {}).get("wins_a") is None or (event or {}).get("wins_b") is None:
            return "VS"
        return f"{event['wins_a']}-{event['wins_b']}"

    @staticmethod
    def _worldcup_group_points_value(event, side):
        if not isinstance(event, Mapping):
            return None
        side = "a" if side == "a" else "b"
        team_key = f"team_{side}"
        keys = (
            f"{team_key}_group_points",
            f"{team_key}_standing_points",
            f"group_points_{side}",
            f"standing_points_{side}",
        )
        for key in keys:
            value = event.get(key)
            if value is None:
                continue
            value = str(value).strip()
            if value:
                return value
        return None

    @staticmethod
    def _worldcup_group_points_label(event, side):
        value = SportsDashboard._worldcup_group_points_value(event, side)
        return f"PTS {value}" if value is not None else "PTS -"

    @staticmethod
    def _worldcup_group_record_value(event, side):
        if not isinstance(event, Mapping):
            return ""
        side = "a" if side == "a" else "b"
        team_key = f"team_{side}"
        keys = (
            f"{team_key}_group_record",
            f"{team_key}_standing_record",
            f"group_record_{side}",
            f"standing_record_{side}",
            f"record_{side}",
        )
        for key in keys:
            value = str(event.get(key) or "").strip()
            if not value:
                continue
            match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\s*", value)
            if match:
                return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        return ""

    @staticmethod
    def _worldcup_group_record_label(event, side):
        value = SportsDashboard._worldcup_group_record_value(event, side)
        if value:
            return value
        if SportsDashboard._worldcup_explicit_group_key(event) and SportsDashboard._worldcup_group_team_key(event, side):
            return "0-0-0"
        return ""

    @staticmethod
    def _worldcup_team_points_meta(event, side, include_odds=False):
        points = SportsDashboard._worldcup_group_points_label(event, side)
        record = SportsDashboard._worldcup_group_record_label(event, side)
        odds_key = "team_a" if side == "a" else "team_b"
        event_data = event if isinstance(event, Mapping) else {}
        odds_value = (event_data.get("odds") or {}).get(odds_key) if include_odds else None
        if side == "a":
            parts = [points]
            if record:
                parts.append(record)
            if odds_value:
                parts.append(str(odds_value))
        else:
            parts = []
            if odds_value:
                parts.append(str(odds_value))
            if record:
                parts.append(record)
            parts.append(points)
        return " / ".join(part for part in parts if part)

    def _draw_worldcup_logo(self, image, draw, x, y, size):
        x = int(x)
        y = int(y)
        size = int(size)
        logo = self._load_local_logo(LOCAL_WORLDCUP_LOGO_PATH, (size, size), alpha_threshold=16)
        if logo:
            image.paste(logo, (x + (size - logo.width) // 2, y + (size - logo.height) // 2), logo)
            return
        draw.ellipse((x, y, x + size, y + size), fill=COLORS["panel"], outline=COLORS["border"], width=2)
        seam = max(3, size // 6)
        draw.arc((x + seam, y + 3, x + size - seam, y + size - 3), 90, 270, fill=COLORS["blue"], width=2)
        draw.arc((x + seam, y + 3, x + size - seam, y + size - 3), 270, 90, fill=COLORS["green"], width=2)
        draw.line((x + 4, y + size / 2, x + size - 4, y + size / 2), fill=COLORS["red"], width=2)
        text, font = self._fit_text(draw, "WC", max(10, size - 8), max(10, int(size * 0.42)), bold=True, min_size=8)
        self._draw_centered(draw, (x + size / 2, y + size / 2), text, font, COLORS["text"])

    def _draw_worldcup_header_brand(self, image, draw, width, compact=False):
        logo_size = 28 if compact else 36
        gap = 10 if compact else 14
        title = "2026 World Cup"
        title_text, title_font = self._fit_text(draw, title, max(150, width - 150), 18 if compact else 22, bold=True, min_size=14)
        text_box = draw.textbbox((0, 0), title_text, font=title_font)
        title_w = text_box[2] - text_box[0]
        title_h = text_box[3] - text_box[1]
        total_w = logo_size + gap + title_w
        group_x = int((width - total_w) / 2)
        center_y = 29 if compact else 40
        logo_y = int(center_y - logo_size / 2)
        title_x = group_x + logo_size + gap - text_box[0]
        title_y = center_y - title_h / 2 - text_box[1]
        self._draw_worldcup_logo(image, draw, group_x, logo_y, logo_size)
        draw.text((title_x, title_y), title_text, font=title_font, fill=COLORS["text"])

    @staticmethod
    def _worldcup_row_regions(width):
        row_left = 16
        row_right = width - 22
        group_width = 96 if width >= 500 else 84
        group_x1 = row_left + 10
        group_x2 = min(group_x1 + group_width, row_right - 210)
        time_width = 56 if width >= 500 else 50
        date_width = 52 if width >= 500 else 48
        right_gap = 4
        date_x2 = row_right - 8
        date_x1 = date_x2 - date_width
        time_x2 = date_x1 - right_gap
        time_x1 = time_x2 - time_width
        match_x1 = group_x2 + 14
        match_x2 = max(match_x1 + 1, time_x1 - 10)
        return {
            "group": (int(group_x1), int(group_x2)),
            "match": (int(match_x1), int(match_x2)),
            "date": (int(date_x1), int(date_x2)),
            "time": (int(time_x1), int(time_x2)),
        }

    @staticmethod
    def _worldcup_right_info_x_ranges(width):
        regions = SportsDashboard._worldcup_row_regions(width)
        return regions["date"], regions["time"]

    @staticmethod
    def _worldcup_matchup_row_offset(row_height):
        return max(8, int((row_height - 21) / 2))

    def _draw_worldcup_matchup(self, image, draw, event, x, y, max_width, row_height=None):
        max_width = max(1, int(max_width))
        row_height = int(row_height) if row_height is not None else None
        odds = event.get("odds") or {}
        has_team_odds = bool(odds.get("team_a") and odds.get("team_b"))
        compact_odds = has_team_odds and row_height is not None and row_height <= 38
        line_y = int(y)
        if row_height is not None:
            line_y += 3 if compact_odds else self._worldcup_matchup_row_offset(row_height)
        if has_team_odds:
            center_width = 34
            side_gap = 8
            center_x = x + max_width / 2
            side_width = max(44, int((max_width - center_width - side_gap * 2) / 2))
            left_country_x = int(center_x - center_width / 2 - side_gap - side_width)
            right_country_x = int(center_x + center_width / 2 + side_gap)
            if compact_odds:
                odds_bottom = int(y + row_height - 2)
                odds_top = max(line_y + 16, odds_bottom - 11)
                side_odds_size = 9
                draw_odds_size = 8
                vs_y = line_y + 7
            else:
                odds_top = line_y + 20
                odds_bottom = line_y + 35
                side_odds_size = 11
                draw_odds_size = 10
                vs_y = line_y + 8
            self._draw_worldcup_country(
                image,
                draw,
                event.get("team_a_flag"),
                event.get("team_a"),
                event.get("team_a_tla"),
                left_country_x,
                line_y,
                side_width,
                "left",
                compact=compact_odds,
            )
            self._draw_worldcup_odds_text(
                draw,
                (left_country_x, odds_top, left_country_x + side_width, odds_bottom),
                odds.get("team_a"),
                max_size=side_odds_size,
            )
            self._draw_centered(draw, (center_x, vs_y), "VS", self._font(10 if compact_odds else 11, True), COLORS["text"])
            if odds.get("draw"):
                self._draw_worldcup_odds_text(
                    draw,
                    (center_x - 24, odds_top, center_x + 24, odds_bottom),
                    f"X {odds.get('draw')}",
                    max_size=draw_odds_size,
                )
            self._draw_worldcup_country(
                image,
                draw,
                event.get("team_b_flag"),
                event.get("team_b"),
                event.get("team_b_tla"),
                right_country_x,
                line_y,
                side_width,
                "right",
                compact=compact_odds,
            )
            self._draw_worldcup_odds_text(
                draw,
                (right_country_x, odds_top, right_country_x + side_width, odds_bottom),
                odds.get("team_b"),
                max_size=side_odds_size,
            )
            return

        center_x = x + max_width / 2
        side_width = max(30, int((max_width - 42) / 2))
        self._draw_worldcup_country(
            image,
            draw,
            event.get("team_a_flag"),
            event.get("team_a"),
            event.get("team_a_tla"),
            x,
            line_y,
            side_width,
            "left",
        )
        self._draw_centered(draw, (center_x, line_y + 11), "VS", self._font(12, True), COLORS["text"])
        self._draw_worldcup_country(
            image,
            draw,
            event.get("team_b_flag"),
            event.get("team_b"),
            event.get("team_b_tla"),
            int(center_x + 21),
            line_y,
            side_width,
            "right",
        )

    def _draw_worldcup_odds_box(self, draw, box, text, max_size=10):
        text = str(text or "").strip()
        if not text:
            return
        left, top, right, bottom = [int(value) for value in box]
        draw.rectangle((left, top, right, bottom), fill=COLORS["panel_gold"], outline=COLORS["border"], width=1)
        fitted, font = self._fit_text(draw, text, max(1, right - left - 4), max_size, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (left + 1, top, right - 1, bottom), fitted, font, COLORS["text"])

    def _draw_worldcup_odds_text(self, draw, box, text, max_size=11):
        text = str(text or "").strip()
        if not text:
            return
        left, top, right, bottom = [int(value) for value in box]
        fitted, font = self._fit_text(draw, text, max(1, right - left), max_size, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (left, top, right, bottom), fitted, font, COLORS["text"])

    def _draw_worldcup_points_text(self, draw, box, event, side, max_size=8):
        text = self._worldcup_group_points_label(event, side)
        left, top, right, bottom = [int(value) for value in box]
        fitted, font = self._fit_text(draw, text, max(1, right - left), max_size, bold=True, min_size=6)
        fill = COLORS["green"] if self._worldcup_group_points_value(event, side) is not None else COLORS["muted"]
        self._draw_centered_in_box(draw, (left, top, right, bottom), fitted, font, fill)

    def _draw_worldcup_country(self, image, draw, flag_url, label, fallback_text, x, y, width, align, compact=False):
        flag_h, flag_cap = (14, 28) if compact else (18, 36)
        flag_w = self._worldcup_flag_display_size(flag_url, fallback_text, flag_cap, flag_h)[0]
        text_gap = 4 if compact else 5
        label_max_size = 14 if compact else 17
        label_min_size = 7 if compact else 8
        label = str(label or fallback_text or "TBD")
        row_h = max(flag_h, 17 if compact else 20)
        flag_y = int(y + (row_h - flag_h) / 2)
        if align == "right":
            text_max = max(16, width - flag_w - text_gap)
            label_text, label_font = self._fit_text(draw, label, text_max, label_max_size, bold=True, min_size=label_min_size)
            text_w = self._text_width(draw, label_text, label_font)
            total_w = min(width, flag_w + text_gap + text_w)
            start_x = int(x + max(0, width - total_w))
            text_x = start_x
            flag_x = int(text_x + text_w + text_gap)
            self._draw_text_in_box(draw, (text_x, y, text_x + text_w, y + row_h), label_text, label_font, COLORS["text"], align="right")
        else:
            text_max = max(16, width - flag_w - text_gap)
            label_text, label_font = self._fit_text(draw, label, text_max, label_max_size, bold=True, min_size=label_min_size)
            text_w = self._text_width(draw, label_text, label_font)
            total_w = min(width, flag_w + text_gap + text_w)
            start_x = int(x)
            flag_x = start_x
            text_x = int(flag_x + flag_w + text_gap)
            self._draw_text_in_box(draw, (text_x, y, text_x + text_w, y + row_h), label_text, label_font, COLORS["text"])
        self._draw_worldcup_flag(image, draw, flag_url, flag_x, flag_y, flag_w, flag_h, fallback_text)

    def _draw_worldcup_flag(self, image, draw, flag_url, x, y, max_width, height, fallback_text, align="left"):
        display_w, display_h = self._worldcup_flag_display_size(flag_url, fallback_text, max_width, height)
        flag = self._load_flag_image(flag_url, (display_w, display_h))
        if align == "right":
            slot_x = int(x + max_width - display_w)
        elif align == "center":
            slot_x = int(x + (max_width - display_w) / 2)
        else:
            slot_x = int(x)
        paste_y = int(y + (height - display_h) / 2)
        if flag:
            image.paste(flag, (slot_x + (display_w - flag.width) // 2, paste_y + (display_h - flag.height) // 2), flag)
            return display_w
        draw.rectangle((slot_x, paste_y, slot_x + display_w, paste_y + display_h), fill=COLORS["panel"], outline=COLORS["border"], width=1)
        fallback = str(fallback_text or "?").strip().upper()[:2] or "?"
        fallback_text, fallback_font = self._fit_text(draw, fallback, max(4, display_w - 3), 9, bold=True, min_size=7)
        self._draw_centered(draw, (slot_x + display_w / 2, paste_y + display_h / 2), fallback_text, fallback_font, COLORS["muted"])
        return display_w

    @staticmethod
    def _worldcup_flag_display_size(flag_url, fallback_text, width, height):
        width = max(1, int(width))
        height = max(1, int(height))
        aspect_ratio = SportsDashboard._worldcup_flag_aspect_ratio(flag_url, fallback_text)
        slot_ratio = width / height
        if aspect_ratio >= slot_ratio:
            display_w = width
            display_h = max(1, min(height, int(round(width / aspect_ratio))))
        else:
            display_h = height
            display_w = max(1, min(width, int(round(height * aspect_ratio))))
        return display_w, display_h

    @staticmethod
    def _worldcup_flag_aspect_ratio(flag_url, fallback_text):
        country_code = SportsDashboard._worldcup_flag_country_code(flag_url, fallback_text)
        return ISO_FLAG_ASPECT_RATIOS.get(country_code, DEFAULT_FLAG_ASPECT_RATIO)

    @staticmethod
    def _worldcup_flag_country_code(flag_url, fallback_text):
        for value in (fallback_text,):
            text = str(value or "").strip().upper()
            if not text:
                continue
            tla = SportsDashboard._canonical_country_tla(text)
            if tla in LOCAL_WORLDCUP_FLAG_TLAS:
                return tla
            country_code = FIFA_TLA_TO_FLAGS_API_CODE.get(tla)
            if country_code:
                return country_code
            if re.fullmatch(r"[A-Z]{2}", text):
                return text
        url = str(flag_url or "")
        local_match = re.fullmatch(rf"{re.escape(LOCAL_WORLDCUP_FLAG_URL_PREFIX)}([a-z]{{3}})", url, re.IGNORECASE)
        if local_match:
            local_tla = local_match.group(1).upper()
            if local_tla in LOCAL_WORLDCUP_FLAG_TLAS:
                return local_tla
        match = re.search(r"flagcdn\.com/(?:[^/]+/)?([A-Z]{2})\.(?:png|svg|webp)", url, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        match = re.search(r"/([A-Z]{2})/flat/", url, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        return ""

    @staticmethod
    def _load_flag_image(flag_url, size):
        if not flag_url:
            return None
        size = (max(1, int(size[0])), max(1, int(size[1])))
        cache_key = (flag_url, size, "worldcup-flag-contain-v3")
        if cache_key in FLAG_IMAGE_CACHE:
            return FLAG_IMAGE_CACHE[cache_key]
        local_flag = SportsDashboard._render_local_worldcup_flag(flag_url, size)
        if local_flag is not None:
            FLAG_IMAGE_CACHE[cache_key] = local_flag
            return local_flag
        try:
            data = SportsDashboard._fetch_remote_image_bytes(flag_url, 4)
            with Image.open(BytesIO(data)) as source:
                flag = SportsDashboard._trim_transparent_flag(source.convert("RGBA"))
                flag = ImageOps.contain(flag, size, Image.LANCZOS)
            FLAG_IMAGE_CACHE[cache_key] = flag
            return flag
        except Exception as exc:
            logger.warning("Failed to load World Cup flag %s: %s", flag_url, exc)
            FLAG_IMAGE_CACHE[cache_key] = None
            return None

    @staticmethod
    def _render_local_worldcup_flag(flag_url, size):
        url = str(flag_url or "").strip().lower()
        if url != f"{LOCAL_WORLDCUP_FLAG_URL_PREFIX}sco":
            return None
        width, height = size
        flag = Image.new("RGBA", (width, height), (0, 94, 184, 255))
        local_draw = ImageDraw.Draw(flag)
        band_width = max(2, int(round(min(width, height) * 0.22)))
        local_draw.line((0, 0, width - 1, height - 1), fill=(255, 255, 255, 255), width=band_width)
        local_draw.line((0, height - 1, width - 1, 0), fill=(255, 255, 255, 255), width=band_width)
        return flag

    @staticmethod
    def _trim_transparent_flag(flag):
        if flag.mode != "RGBA":
            flag = flag.convert("RGBA")
        bbox = flag.getchannel("A").getbbox()
        if bbox:
            return flag.crop(bbox)
        return flag

    @staticmethod
    def _worldcup_api_source_label(source_state, fetched_at):
        fetched = SportsDashboard._parse_cached_utc(fetched_at)
        time_text = fetched.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%I:%M %p").lstrip("0") if fetched else ""
        state = str(source_state or "API").upper()
        if "ESPN" in state and "FOOTBALL" in state:
            prefix = "FD+ESPN"
        elif "ESPN" in state and state.startswith("API"):
            prefix = "API+ESPN"
        elif state == "ESPN LIVE":
            prefix = "ESPN LIVE"
        elif state == "ESPN CACHE":
            prefix = "ESPN CACHE"
        elif state == "ESPN STALE":
            prefix = "ESPN STALE"
        elif state == "ESPN LIMIT":
            prefix = "ESPN LIMIT"
        elif state == "FOOTBALL LIVE":
            prefix = "FOOTBALL DATA"
        elif state == "FOOTBALL CACHE":
            prefix = "FD CACHE"
        elif state == "FOOTBALL STALE":
            prefix = "FD STALE"
        elif state == "FOOTBALL LIMIT":
            prefix = "FD LIMIT"
        elif state == "API LIVE":
            prefix = "API LIVE"
        elif state == "API STALE":
            prefix = "STALE CACHE"
        elif state == "API LIMIT":
            prefix = "API LIMIT"
        elif state in {"NO DATA", "NO HISTORICAL DATA"}:
            prefix = "NO DATA"
        elif state in {"BACKUP SCHEDULE", "FALLBACK"}:
            prefix = "BACKUP VIEW"
        else:
            prefix = "API CACHE"
        return f"{prefix} {time_text}".strip()

    @staticmethod
    def _clean_worldcup_stage(value):
        text = str(value or "World Cup").strip()
        for prefix in ("World Cup - ", "FIFA World Cup - "):
            if text.startswith(prefix):
                text = text[len(prefix):]
        return text or "World Cup"

    @staticmethod
    def _worldcup_status_color(event, now=None):
        state = str(event.get("state") or "").upper()
        if SportsDashboard._worldcup_is_display_live(event, now):
            return COLORS["worldcup_live"]
        if state in {"FT", "AET", "PEN", "FINISHED", "AWARDED"}:
            return COLORS["green"]
        return COLORS["worldcup_accent"]

    @staticmethod
    def _worldcup_event_status_label(event, now):
        state = str(event.get("state") or "").upper()
        if state in {"FT", "AET", "PEN", "FINISHED", "AWARDED"} and event.get("wins_a") is not None and event.get("wins_b") is not None:
            return f"{event['wins_a']}-{event['wins_b']}"
        if SportsDashboard._worldcup_is_display_live(event, now):
            score = SportsDashboard._score_label(event)
            status = str(event.get("status") or "").strip()
            status_upper = status.upper()
            if event.get("inferred_live") and status_upper in {"", "NS", "NOT STARTED", "SCHEDULED", "TIMED", "INFERRED LIVE"}:
                status = ""
                status_upper = ""
            if score != "vs":
                if status and status_upper not in {"LIVE", "IN PLAY", "IN_PROGRESS", "FIRST HALF", "SECOND HALF"}:
                    return f"{status} {score}"
                return f"LIVE {score}"
            return "LIVE"
        return SportsDashboard._format_time_24h(event["start"])

    @staticmethod
    def _worldcup_main_status_label(event, now):
        label = SportsDashboard._worldcup_event_status_label(event, now)
        if not event or event.get("inferred_live"):
            return label
        is_live_or_final = SportsDashboard._worldcup_is_display_live(event, now) or SportsDashboard._is_worldcup_finished_event(event)
        if not is_live_or_final:
            return label
        if not (event.get("score_confirmed") or event.get("provider_status_confirmed")):
            return label
        source = SportsDashboard._worldcup_status_source_label(event)
        if not source:
            return label
        return f"{source} {label}"

    @staticmethod
    def _worldcup_status_source_label(event):
        source = str((event or {}).get("score_source") or (event or {}).get("provider") or "").strip()
        normalized = source.upper().replace("_", " ").replace("-", " ")
        if "ESPN" in normalized:
            return "ESPN"
        if "FOOTBALL" in normalized or normalized in {"FD", "FOOTBALLDATA"}:
            return "FD"
        if source:
            return source.upper()[:6]
        return ""

    @staticmethod
    def _worldcup_event_time_label(event):
        return SportsDashboard._format_time_24h(event["start"])

    def _render_worldcup_fallback(self, dimensions, visible_matches=DEFAULT_WORLD_CUP_VISIBLE_MATCHES, season=DEFAULT_WORLD_CUP_SEASON):
        visible_matches = max(1, min(WORLD_CUP_VISIBLE_MATCH_LIMIT, int(visible_matches or DEFAULT_WORLD_CUP_VISIBLE_MATCHES)))
        timezone_info = ZoneInfo(DEFAULT_TIMEZONE)
        season = str(season or DEFAULT_WORLD_CUP_SEASON).strip()
        if not season.isdigit() or len(season) != 4:
            season = DEFAULT_WORLD_CUP_SEASON
        if season != DEFAULT_WORLD_CUP_SEASON:
            selected = {
                "live": [],
                "upcoming": [],
                "recent": [],
                "main": None,
                "visible_matches": visible_matches,
                "season": season,
            }
            return self._render_worldcup_api_panel(
                dimensions,
                selected,
                "NO HISTORICAL DATA",
                None,
                visible_matches,
                datetime.now(timezone_info),
            )
        labels = self._worldcup_local_time_labels()
        fallback_events = []
        for index in range(visible_matches):
            hour, minute = [int(part) for part in labels[index].split(":")]
            fallback_events.append(
                {
                    "start": datetime(2026, 6, 11 + index, hour, minute, tzinfo=timezone_info),
                    "state": "SCHEDULED",
                    "status": "Scheduled",
                    "team_a": "Teams",
                    "team_b": "TBD",
                    "team_a_tla": "TBD",
                    "team_b_tla": "TBD",
                    "team_a_flag": "",
                    "team_b_flag": "",
                    "wins_a": None,
                    "wins_b": None,
                    "block": "Opening Match" if index == 0 else "Group Stage",
                }
            )
        selected = self._select_worldcup_event_sections(
            fallback_events,
            datetime.now(timezone_info),
            visible_matches,
        )
        return self._render_worldcup_api_panel(
            dimensions,
            selected,
            "BACKUP SCHEDULE",
            None,
            visible_matches,
            datetime.now(timezone_info),
        )

    def _overlay_worldcup_local_times(self, image, left_width, timezone_info, visible_matches, content_box):
        if self._timezone_key(timezone_info) != DEFAULT_TIMEZONE:
            return
        draw = ImageDraw.Draw(image)
        crop_height = self._worldcup_crop_height(visible_matches)
        content_left, content_top, content_right, content_bottom = content_box
        content_height = max(1, content_bottom - content_top)
        scale_y = content_height / crop_height
        row_times = self._worldcup_local_time_labels()
        row_y = [61, 122, 183, 244, 305, 366, 427]
        font_size = max(14, min(22, int(14 * min(scale_y, 1.6))))
        rect_height = max(23, min(34, int(23 * min(scale_y, 1.6))))
        rect_width = 112 if visible_matches <= 3 else 96
        for y, text in zip(row_y[:visible_matches], row_times[:visible_matches]):
            y1 = content_top + int(y * scale_y)
            y2 = y1 + rect_height
            x1 = max(content_left + 2, min(left_width - rect_width, content_right - rect_width))
            x2 = min(left_width - 2, content_right - 2)
            draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"], outline=COLORS["border"], width=1)
            draw.rectangle((x1, y1, x1 + 5, y2), fill=COLORS["worldcup_accent"])
            value, value_font = self._fit_text(draw, text, x2 - x1 - 8, font_size, bold=True, min_size=11)
            self._draw_right_aligned(draw, (x2 - 6, y1 + 4), value, value_font, COLORS["text"])

    @staticmethod
    def _worldcup_local_time_labels():
        return ["12:00", "19:00", "12:00", "18:00", "12:00", "15:00", "18:00"]

    def _draw_f1_compact_panel(self, image, draw, bounds, selected, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        panel_w = x2 - x1 + 1
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["f1_accent"], COLORS["panel"], 22, 1)
        header_y = y1 + 8
        self._draw_f1_logo(image, draw, x1 + 15, header_y - 2, 74, 34)
        title, title_font = self._fit_text(draw, "FORMULA 1", 142, 17, bold=True, min_size=12)
        draw.text((x1 + 96, header_y + 1), title, font=title_font, fill=COLORS["text"])
        source_label = self._f1_source_label(selected, source_state)
        source_label, source_font = self._fit_text(draw, source_label, 124, 10, bold=True, min_size=7)
        draw.text((x1 + 97, header_y + 22), source_label, font=source_font, fill=COLORS["muted"])
        self._draw_f1_header_track_strip(draw, x1 + 214, header_y + 2, x2 - 92, y1 + 47)

        status = str((selected or {}).get("status") or "BREAK").upper()
        pill_text = "LIVE" if status == "LIVE" else ("NEXT" if status == "NEXT" else ("RESULT" if status == "RECENT" else "BREAK"))
        self._draw_status_pill(draw, x2 - 84, header_y + 4, pill_text, status == "LIVE")
        draw.line((x1 + 12, y1 + 48, x2 - 12, y1 + 48), fill=COLORS["border"], width=1)

        content_y = y1 + 58
        content_bottom = y2 - 8
        split_x = x1 + max(260, min(310, int(panel_w * 0.54)))
        left_x1 = x1 + 12
        left_x2 = split_x - 10
        right_x1 = split_x + 4
        right_x2 = x2 - 12
        draw.line((split_x - 3, content_y - 5, split_x - 3, content_bottom), fill=COLORS["border"], width=1)
        draw.line((split_x - 1, content_y - 5, split_x - 1, content_bottom), fill=COLORS["line"], width=1)

        self._draw_f1_main_card(image, draw, left_x1, content_y, left_x2, content_bottom, selected, now)
        self._draw_f1_side_column(draw, right_x1, right_x2, content_y, content_bottom, selected, now)

    def _draw_f1_logo(self, image, draw, x, y, width, height):
        logo = self._load_local_logo(LOCAL_F1_LOGO_PATH, (int(width), int(height)), alpha_threshold=12)
        if logo:
            image.paste(logo, (int(x + (width - logo.width) / 2), int(y + (height - logo.height) / 2)), logo)
            return
        text, text_font = self._fit_text(draw, "F1", width, 25, bold=True, min_size=18)
        draw.text((x, y + 3), text, font=text_font, fill=COLORS["f1_accent"])

    def _draw_f1_header_track_strip(self, draw, x1, y1, x2, y2):
        x1 = int(x1)
        y1 = int(y1)
        x2 = int(x2)
        y2 = int(y2)
        if x2 - x1 < 40 or y2 - y1 < 12:
            return
        mid_y = (y1 + y2) // 2
        draw.line((x1, mid_y + 6, x2, mid_y - 3), fill=COLORS["f1_track"], width=3)
        draw.line((x1, mid_y + 11, x2, mid_y + 2), fill=COLORS["border"], width=1)
        draw.line((x1, mid_y + 1, x2, mid_y - 8), fill=COLORS["border"], width=1)
        square = 5
        start_x = x2 - min(82, x2 - x1)
        for index in range(12):
            col = index % 6
            row = index // 6
            fill = COLORS["text"] if (col + row) % 2 == 0 else COLORS["panel"]
            sx = start_x + col * square
            sy = y1 + 2 + row * square
            draw.rectangle((sx, sy, sx + square - 1, sy + square - 1), fill=fill)

    @staticmethod
    def _f1_source_label(selected, source_state):
        openf1_state = str((selected or {}).get("openf1_source_state") or "").strip()
        if openf1_state:
            return f"{source_state} + {openf1_state}"
        return str(source_state or "F1 DATA").strip() or "F1 DATA"

    def _draw_f1_main_card(self, image, draw, x1, y1, x2, y2, selected, now):
        status = str((selected or {}).get("status") or "BREAK").upper()
        race = (selected or {}).get("main_race") or {}
        live_session = (selected or {}).get("live_session")
        next_session = (selected or {}).get("next_session")
        focus_session = live_session or next_session
        accent = COLORS["f1_live"] if status == "LIVE" else COLORS["f1_accent"]
        draw.rounded_rectangle((x1 + 3, y1 + 3, x2 + 3, y2 + 3), radius=5, fill=COLORS["f1_shadow"])
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, y2 - 1), fill=accent)

        if status == "LIVE":
            tag = "\u6bd4\u8d5b\u4e2d"
        elif status == "RECENT":
            tag = "\u6700\u8fd1\u8d5b\u679c"
        elif status == "BREAK":
            tag = "\u7b49\u5f85\u8d5b\u5386"
        else:
            tag = "\u4e0b\u4e00\u7ad9"
        tag_text, tag_font = self._fit_text(draw, tag, 92, 11, bold=True, min_size=7)
        draw.rectangle((x1 + 14, y1 + 10, x1 + 112, y1 + 28), fill=COLORS["f1_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 19, y1 + 11), tag_text, font=tag_font, fill=COLORS["text"])

        date_source = (focus_session or {}).get("start") or race.get("race_start")
        date_text = date_source.strftime("%m/%d") if isinstance(date_source, datetime) else "--"
        date_text, date_font = self._fit_text(draw, date_text, 56, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 12), date_text, date_font, COLORS["muted"])

        if not race:
            title, title_font = self._fit_text(draw, "\u7b49\u5f85 F1 \u8d5b\u5386", x2 - x1 - 42, 22, bold=True, min_size=14)
            self._draw_centered_in_box(draw, (x1 + 18, y1 + 52, x2 - 18, y1 + 84), title, title_font, COLORS["text"])
            return

        race_name, race_font = self._fit_text(draw, race.get("race_name") or "Formula 1", x2 - x1 - 36, 22, bold=True, min_size=14)
        self._draw_centered_in_box(draw, (x1 + 18, y1 + 39, x2 - 18, y1 + 66), race_name, race_font, COLORS["text"])
        place = self._f1_place_label(race)
        place, place_font = self._fit_text(draw, place, x2 - x1 - 42, 11, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (x1 + 20, y1 + 67, x2 - 20, y1 + 83), place, place_font, COLORS["muted"])

        session_box_y1 = max(y1 + 103, y2 - 70)
        session_box_y2 = min(y2 - 18, session_box_y1 + 46)
        draw.rounded_rectangle((x1 + 16, session_box_y1, x2 - 16, session_box_y2), radius=5, fill=COLORS["panel_blue"], outline=COLORS["border"], width=1)
        label, label_font = self._fit_text(draw, "\u4e0b\u4e00\u8282" if status != "LIVE" else "\u5f53\u524d\u8d5b\u6bb5", 76, 10, bold=True, min_size=7)
        draw.text((x1 + 25, session_box_y1 + 5), label, font=label_font, fill=COLORS["muted"])
        if focus_session:
            session_text = self._f1_session_status_label(focus_session, now, status == "LIVE")
            secondary = self._f1_countdown_label(focus_session.get("start"), now) if status != "LIVE" else "LIVE TIMING"
        else:
            last_result = (selected or {}).get("last_result") or {}
            session_text = (last_result.get("top") or [{}])[0].get("driver_code") or "\u7b49\u5f85\u6570\u636e"
            secondary = "\u5168\u573a\u7ed3\u679c" if status == "RECENT" else "\u8d5b\u7a0b\u66f4\u65b0\u4e2d"
        session_text, session_font = self._fit_text(draw, session_text, x2 - x1 - 138, 16, bold=True, min_size=10)
        self._draw_right_aligned(draw, (x2 - 24, session_box_y1 + 4), session_text, session_font, COLORS["text"])
        secondary, secondary_font = self._fit_text(draw, secondary, x2 - x1 - 52, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 24, session_box_y1 + 27, x2 - 24, session_box_y2 - 3), secondary, secondary_font, COLORS["muted"])

        if (selected or {}).get("weather"):
            weather = self._f1_weather_label(selected.get("weather"))
            weather, weather_font = self._fit_text(draw, weather, x2 - x1 - 34, 9, bold=True, min_size=7)
            draw.text((x1 + 16, y2 - 15), weather, font=weather_font, fill=COLORS["f1_accent"])

    @staticmethod
    def _f1_place_label(race):
        parts = [
            str((race or {}).get("circuit_name") or "").strip(),
            str((race or {}).get("country") or "").strip(),
        ]
        return " / ".join(part for part in parts if part) or "Formula 1"

    def _f1_session_status_label(self, session, now, is_live):
        label = str((session or {}).get("label") or "F1").strip()
        start = (session or {}).get("start")
        if is_live:
            return f"{label} LIVE"
        if isinstance(start, datetime):
            return f"{label} {self._format_time(start)}"
        return label

    @staticmethod
    def _f1_countdown_label(start, now):
        if not isinstance(start, datetime) or not isinstance(now, datetime):
            return "\u8d5b\u7a0b\u66f4\u65b0\u4e2d"
        days = max(0, (start.date() - now.date()).days)
        if days > 0:
            return f"D-{days}"
        return "\u4eca\u65e5"

    @staticmethod
    def _f1_weather_label(weather):
        if not weather:
            return ""
        air = (weather or {}).get("air")
        track = (weather or {}).get("track")
        if air is None and track is None:
            return ""
        return f"\u5929\u6c14 AIR {air if air is not None else '-'} / TRACK {track if track is not None else '-'}"

    def _draw_f1_side_column(self, draw, x1, x2, y1, y2, selected, now):
        self._draw_f1_mini_section_header(draw, x1, x2, y1, "F1 WEEKEND")
        rows = self._f1_visible_weekend_sessions(selected, now)
        leaderboard = (selected or {}).get("leaderboard") or ((selected or {}).get("last_result") or {}).get("top") or []
        row_y = y1 + 26
        row_h = 28
        max_session_rows = 2 if leaderboard else 4
        visible_rows = rows[:max_session_rows]
        for index, session in enumerate(visible_rows):
            self._draw_f1_session_row(draw, x1, x2, row_y + index * row_h, session, now)
        used_rows = max(1, len(visible_rows))
        lower_y = row_y + used_rows * row_h + 15
        if leaderboard:
            title = "LIVE TIMING" if str((selected or {}).get("status") or "").upper() == "LIVE" else "LAST RACE"
            self._draw_f1_mini_section_header(draw, x1, x2, lower_y, title)
            available = max(0, y2 - (lower_y + 25))
            max_rows = max(1, min(3, available // 23))
            for index, row in enumerate(leaderboard[:max_rows]):
                top = lower_y + 25 + index * 23
                if top + 18 > y2:
                    break
                self._draw_f1_leaderboard_row(draw, x1, x2, top, row)
        else:
            self._draw_f1_mini_section_header(draw, x1, x2, lower_y, "STANDINGS")
            standings = (selected or {}).get("driver_standings") or []
            if not standings:
                text, text_font = self._fit_text(draw, "\u7b49\u5f85\u6570\u636e", x2 - x1 - 20, 11, bold=True, min_size=8)
                draw.text((x1 + 10, lower_y + 24), text, font=text_font, fill=COLORS["muted"])
                return
            for index, row in enumerate(standings[:4]):
                top = lower_y + 23 + index * 23
                if top + 18 > y2:
                    break
                self._draw_f1_standing_row(draw, x1, x2, top, row)

    @staticmethod
    def _f1_visible_weekend_sessions(selected, now):
        sessions = list((selected or {}).get("weekend_sessions") or [])
        if not sessions:
            candidate = (selected or {}).get("next_session") or (selected or {}).get("live_session")
            return [candidate] if candidate else []
        future_or_live = [
            session for session in sessions
            if SportsDashboard._is_f1_live_session(session, now)
            or (isinstance(session.get("start"), datetime) and session["start"] >= now)
        ]
        recent = [
            session for session in sessions
            if isinstance(session.get("start"), datetime) and session["start"] < now
        ][-2:]
        return (recent + future_or_live)[:4] if future_or_live else sessions[-4:]

    def _draw_f1_mini_section_header(self, draw, x1, x2, y, title):
        draw.rectangle((x1, y + 2, x1 + 8, y + 17), fill=COLORS["f1_accent"], outline=COLORS["border"], width=1)
        draw.text((x1 + 13, y - 2), title, font=self._font(13, True), fill=COLORS["text"])
        draw.line((x1, y + 19, x2, y + 19), fill=COLORS["border"], width=1)

    def _draw_f1_session_row(self, draw, x1, x2, y, session, now):
        if not session:
            return
        is_live = self._is_f1_live_session(session, now)
        start = session.get("start")
        row_h = 23
        draw.rounded_rectangle((x1, y, x2, y + row_h), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + row_h - 1), fill=COLORS["f1_live"] if is_live else COLORS["f1_accent"])
        label, label_font = self._fit_text(draw, str(session.get("label") or "F1"), 50, 10, bold=True, min_size=7)
        draw.text((x1 + 10, y + 3), label, font=label_font, fill=COLORS["muted"])
        time_text = "LIVE" if is_live else (self._format_time(start) if isinstance(start, datetime) else "--")
        time_text, time_font = self._fit_text(draw, time_text, 58, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 9, y + 3), time_text, time_font, COLORS["text"] if is_live else COLORS["muted"])

    def _draw_f1_leaderboard_row(self, draw, x1, x2, y, row):
        position = str((row or {}).get("position") or "-")
        driver = str((row or {}).get("driver_code") or "DRV")
        gap = str((row or {}).get("gap") or "-")
        team_color = (row or {}).get("team_color") or COLORS["f1_accent"]
        draw.rectangle((x1 + 1, y + 2, x1 + 5, y + 16), fill=team_color)
        draw.text((x1 + 10, y), f"P{position}", font=self._font(10, True), fill=COLORS["muted"])
        driver, driver_font = self._fit_text(draw, driver, 44, 12, bold=True, min_size=8)
        draw.text((x1 + 46, y - 1), driver, font=driver_font, fill=COLORS["text"])
        gap, gap_font = self._fit_text(draw, gap, x2 - x1 - 100, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 8, y), gap, gap_font, COLORS["muted"])

    def _draw_f1_standing_row(self, draw, x1, x2, y, row):
        position = str((row or {}).get("position") or "-")
        driver = str((row or {}).get("driver_code") or "DRV")
        points = str((row or {}).get("points") or "0")
        draw.text((x1 + 8, y), f"P{position}", font=self._font(10, True), fill=COLORS["muted"])
        driver, driver_font = self._fit_text(draw, driver, 58, 12, bold=True, min_size=8)
        draw.text((x1 + 44, y - 1), driver, font=driver_font, fill=COLORS["text"])
        points, points_font = self._fit_text(draw, f"{points} PTS", 70, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 8, y), points, points_font, COLORS["muted"])

    def _draw_offseason_hub_compact_panel(self, image, draw, bounds, selected, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        primary = (selected or {}).get("primary") or {}
        sport = str(primary.get("sport") or "MLB").upper()
        if sport == "WNBA":
            self._draw_wnba_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        elif sport == "PGA":
            self._draw_pga_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        elif sport == "NFL":
            self._draw_nfl_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        elif sport == "NCAA":
            self._draw_ncaa_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        else:
            self._draw_mlb_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)

    def _draw_mlb_standalone_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["mlb_accent"], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "MLB", card, source_state)
        content_y = y1 + 58
        bottom = y2 - 8
        split_x = x1 + max(300, min(330, int((x2 - x1 + 1) * 0.59)))
        left = (x1 + 12, content_y, split_x - 10, bottom)
        right = (split_x + 6, content_y, x2 - 12, bottom)
        self._draw_vertical_split(draw, split_x, content_y - 6, bottom)
        self._draw_mlb_main_card(image, draw, left, card, now)
        self._draw_mlb_side_column(image, draw, right, card, now)

    def _draw_wnba_standalone_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["wnba_accent"], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "WNBA", card, source_state)
        content_y = y1 + 58
        bottom = y2 - 8
        split_x = x1 + max(300, min(330, int((x2 - x1 + 1) * 0.59)))
        left = (x1 + 12, content_y, split_x - 10, bottom)
        right = (split_x + 6, content_y, x2 - 12, bottom)
        self._draw_vertical_split(draw, split_x, content_y - 6, bottom)
        self._draw_wnba_main_card(image, draw, left, card, now)
        self._draw_wnba_side_column(image, draw, right, card, now)

    def _draw_pga_standalone_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["pga_accent"], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "PGA", card, source_state)
        content_y = y1 + 58
        bottom = y2 - 8
        split_x = x1 + max(252, min(286, int((x2 - x1 + 1) * 0.50)))
        left = (x1 + 12, content_y, split_x - 10, bottom)
        right = (split_x + 6, content_y, x2 - 12, bottom)
        self._draw_vertical_split(draw, split_x, content_y - 6, bottom)
        self._draw_pga_event_card(image, draw, left, card, now)
        self._draw_pga_leaderboard_column(draw, right, card, now)

    def _draw_nfl_standalone_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["nfl_accent"], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "NFL", card, source_state)
        content_y = y1 + 58
        bottom = y2 - 8
        split_x = x1 + max(304, min(334, int((x2 - x1 + 1) * 0.60)))
        left = (x1 + 12, content_y, split_x - 10, bottom)
        right = (split_x + 6, content_y, x2 - 12, bottom)
        self._draw_vertical_split(draw, split_x, content_y - 6, bottom)
        self._draw_nfl_main_card(image, draw, left, card, now)
        self._draw_football_side_column(image, draw, right, card, now, "NFL")

    def _draw_ncaa_standalone_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["ncaa_accent"], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "NCAA", card, source_state)
        content_y = y1 + 58
        bottom = y2 - 8
        split_x = x1 + max(304, min(334, int((x2 - x1 + 1) * 0.60)))
        left = (x1 + 12, content_y, split_x - 10, bottom)
        right = (split_x + 6, content_y, x2 - 12, bottom)
        self._draw_vertical_split(draw, split_x, content_y - 6, bottom)
        self._draw_ncaa_main_card(image, draw, left, card, now)
        self._draw_ncaa_side_column(image, draw, right, card, now)

    def _draw_ncaa_main_card(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        status = str((card or {}).get("status") or "NEXT").upper()
        accent = COLORS["ncaa_live"] if status == "LIVE" else COLORS["ncaa_accent"]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        tag, tag_font = self._fit_text(draw, f"NCAA {status}", 98, 12, bold=True, min_size=8)
        draw.rectangle((x1 + 18, y1 + 11, x1 + 120, y1 + 29), fill=COLORS["ncaa_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 23, y1 + 12), tag, font=tag_font, fill=COLORS["text"])
        week = self._football_header_week_label(card, event)
        week, week_font = self._fit_text(draw, week or self._hub_event_time_label(event, now), 82, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 13), week, week_font, COLORS["muted"])
        header_badge = SportsDashboard._ncaa_header_badge_label(event)
        if header_badge:
            badge, badge_font = self._fit_text(draw, header_badge, 62, 9, bold=True, min_size=7)
            draw.text((x1 + 127, y1 + 16), badge, font=badge_font, fill=COLORS["amber"])

        board_y1 = y1 + 39
        board_y2 = y2 - 58
        board_fill = self._blend(COLORS["ncaa_accent"], COLORS["panel"], 0.18)
        draw.rounded_rectangle((x1 + 16, board_y1, x2 - 16, board_y2), radius=5, fill=board_fill, outline=COLORS["border"], width=1)
        center_x = (x1 + x2) / 2
        self._draw_ncaa_field_backdrop(draw, x1 + 16, board_y1, x2 - 16, board_y2)

        away_fill = COLORS[SportsDashboard._football_team_side_fill_key(event, "a")]
        home_fill = COLORS[SportsDashboard._football_team_side_fill_key(event, "b")]
        self._draw_ncaa_team_block(
            image,
            draw,
            x1 + 28,
            board_y1 + 12,
            center_x - 28,
            event,
            "a",
            team_fill=away_fill,
        )
        self._draw_ncaa_team_block(
            image,
            draw,
            center_x + 28,
            board_y1 + 12,
            x2 - 28,
            event,
            "b",
            align="right",
            team_fill=home_fill,
        )
        score = self._hub_score_label(event)
        score, score_font = self._fit_text(draw, score, 86, 29 if score != "VS" else 23, bold=True, min_size=16)
        self._draw_centered(draw, (center_x, board_y1 + 44), score, score_font, COLORS["text"])
        if self._hub_event_state(event) != "scheduled":
            live_label = str(event.get("status_text") or self._hub_event_time_label(event, now)).upper()
            live_label, live_font = self._fit_text(draw, live_label, 86, 12, bold=True, min_size=8)
            self._draw_centered(draw, (center_x, board_y1 + 18), live_label, live_font, COLORS["amber"] if status == "LIVE" else COLORS["muted"])

        if self._hub_event_state(event) == "final":
            self._draw_ncaa_final_context(draw, x1, y2, x2, board_y2, event)
            return
        if self._hub_event_state(event) == "scheduled":
            self._draw_ncaa_pregame_context(draw, x1, y2, x2, board_y2, event, now)
            return
        self._draw_ncaa_live_context(draw, x1, y2, x2, board_y2, event)

    def _draw_ncaa_field_backdrop(self, draw, x1, y1, x2, y2):
        center_x = (x1 + x2) / 2
        accent = self._blend(COLORS["ncaa_accent"], COLORS["panel"], 0.42)
        gold = self._blend(COLORS["amber"], COLORS["panel"], 0.38)
        draw.line((center_x, y1 + 7, center_x, y2 - 7), fill=COLORS["line"], width=1)
        for y in range(y1 + 14, y2 - 8, 18):
            draw.line((x1 + 8, y, x1 + 16, y), fill=COLORS["line"], width=1)
            draw.line((x2 - 16, y, x2 - 8, y), fill=COLORS["line"], width=1)
        for index, top in enumerate(range(y1 + 7, y2 - 16, 15)):
            color = gold if index % 2 == 0 else accent
            draw.line((x1 + 7, top, x1 + 18, top + 7), fill=color, width=1)
            draw.line((x2 - 18, top + 7, x2 - 7, top), fill=color, width=1)
        badge_y = max(y1 + 56, y2 - 25)
        draw.rounded_rectangle((center_x - 24, badge_y, center_x + 24, badge_y + 13), radius=3, outline=accent, width=1)
        label, label_font = self._fit_text(draw, "CFB", 36, 8, bold=True, min_size=6)
        self._draw_centered(draw, (center_x, badge_y + 2), label, label_font, COLORS["ncaa_accent"])

    def _draw_ncaa_team_block(self, image, draw, x1, y, x2, event, side, align="left", team_fill=None):
        prefix = "team_a" if side == "a" else "team_b"
        code = str((event or {}).get(f"{prefix}_code") or (event or {}).get(prefix) or "TBD").strip().upper()
        rank = (event or {}).get(f"{prefix}_rank")
        school = self._ncaa_school_label(event, side, full=True)
        record = str((event or {}).get("record_a" if side == "a" else "record_b") or "").strip()
        logo_size = 20
        text_fill = team_fill or COLORS["text"]
        rank_fill = team_fill or COLORS["ncaa_accent"]
        if align == "right":
            logo_x = int(x2 - logo_size)
            self._draw_team_logo(image, draw, (event or {}).get(f"{prefix}_logo"), logo_x, y, logo_size, code)
            text_x2 = logo_x - 4
            rank_text = f"#{rank}" if rank else code
            rank_text, rank_font = self._fit_text(draw, rank_text, 36, 10, bold=True, min_size=7)
            self._draw_right_aligned(draw, (text_x2, y), rank_text, rank_font, rank_fill)
            school_text, school_font = self._fit_text(draw, school, max(36, text_x2 - x1), 17, bold=True, min_size=10)
            self._draw_right_aligned(draw, (text_x2, y + 15), school_text, school_font, text_fill)
            code_text = " / ".join(part for part in (code, record) if part)
            code_text, code_font = self._fit_text(draw, code_text, max(36, text_x2 - x1), 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (text_x2, y + 36), code_text, code_font, COLORS["muted"])
            return
        self._draw_team_logo(image, draw, (event or {}).get(f"{prefix}_logo"), x1, y, logo_size, code)
        text_x1 = x1 + logo_size + 5
        rank_text = f"#{rank}" if rank else code
        rank_text, rank_font = self._fit_text(draw, rank_text, 36, 10, bold=True, min_size=7)
        draw.text((text_x1, y), rank_text, font=rank_font, fill=rank_fill)
        school_text, school_font = self._fit_text(draw, school, max(36, x2 - text_x1), 17, bold=True, min_size=10)
        draw.text((text_x1, y + 15), school_text, font=school_font, fill=text_fill)
        code_text = " / ".join(part for part in (code, record) if part)
        code_text, code_font = self._fit_text(draw, code_text, max(36, x2 - text_x1), 8, bold=True, min_size=6)
        draw.text((text_x1, y + 36), code_text, font=code_font, fill=COLORS["muted"])

    def _draw_ncaa_live_context(self, draw, x1, y2, x2, board_y2, event):
        last_play = str((event or {}).get("last_play") or "").strip()
        has_play = bool(last_play)
        context_y = max(board_y2 + (4 if has_play else 7), y2 - (58 if has_play else 51))
        box_bottom = context_y + (43 if has_play else 31)
        draw.rounded_rectangle((x1 + 16, context_y, x2 - 16, box_bottom), radius=4, fill=COLORS["ncaa_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "DOWN", x1 + 25, context_y + 6, COLORS["ncaa_accent"])
        label, label_font = self._fit_text(draw, "COLLEGE DRIVE", 104, 9, bold=True, min_size=7)
        draw.text((x1 + 40, context_y + 4), label, font=label_font, fill=COLORS["ncaa_accent"])
        possession = SportsDashboard._football_possession_display_label(event, "NCAA")
        if possession:
            pos, pos_font = self._fit_text(draw, f"POS {possession}", 70, 9, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 25, context_y + 4), pos, pos_font, COLORS["amber"])
        self._draw_ncaa_live_drive_chips(draw, x1 + 24, context_y + 16, x2 - 24, event)
        if has_play:
            self._draw_ncaa_last_play_strip(draw, x1 + 24, context_y + 32, x2 - 24, last_play)
        meta = self._ncaa_main_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        meta_box = (x1 + 20, y2 - 12, x2 - 20, y2 - 2) if has_play else (x1 + 20, y2 - 18, x2 - 20, y2 - 4)
        self._draw_centered_in_box(draw, meta_box, meta, meta_font, COLORS["muted"])

    def _draw_ncaa_live_drive_chips(self, draw, x1, y, x2, event):
        down = str((event or {}).get("down_distance") or "").strip().upper()
        field = str((event or {}).get("yard_line") or (event or {}).get("note") or "").strip()
        last_play = str((event or {}).get("last_play") or "").strip()
        items = []
        if down:
            items.append(("DOWN", down, COLORS["amber"]))
        if field:
            items.append(("FIELD", field, COLORS["ncaa_accent"]))
        if not items:
            if last_play:
                items.append(("PLAY", "LAST PLAY", COLORS["amber"]))
            else:
                items.append(("DOWN", "LIVE DRIVE", COLORS["amber"]))
        if len(items) == 1:
            self._draw_ncaa_live_drive_chip(draw, (x1, y, x2, y + 14), items[0][0], items[0][1], items[0][2])
            return
        gap = 6
        mid = int((x1 + x2) / 2)
        self._draw_ncaa_live_drive_chip(draw, (x1, y, mid - gap // 2, y + 14), items[0][0], items[0][1], items[0][2])
        self._draw_ncaa_live_drive_chip(draw, (mid + gap // 2, y, x2, y + 14), items[1][0], items[1][1], items[1][2])

    def _draw_ncaa_live_drive_chip(self, draw, box, label, value, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, label, x1 + 5, y1 + 3, accent)
        label_text, label_font = self._fit_text(draw, label, 34, 6, bold=True, min_size=5)
        draw.text((x1 + 18, y1 + 3), label_text, font=label_font, fill=accent)
        value_text, value_font = self._fit_text(draw, str(value or "TBD"), max(24, x2 - x1 - 58), 8, bold=True, min_size=6)
        self._draw_right_aligned(draw, (x2 - 5, y1 + 3), value_text, value_font, COLORS["text"])

    def _draw_ncaa_last_play_strip(self, draw, x1, y, x2, play):
        play = str(play or "").strip()
        if not play:
            return
        y = int(y)
        draw.rounded_rectangle((x1, y, x2, y + 10), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "PLAY", x1 + 5, y + 1, COLORS["ncaa_accent"])
        label, label_font = self._fit_text(draw, "PLAY", 28, 6, bold=True, min_size=5)
        draw.text((x1 + 18, y + 1), label, font=label_font, fill=COLORS["ncaa_accent"])
        play_text, play_font = self._fit_text(draw, play, max(44, x2 - x1 - 58), 7, bold=True, min_size=5)
        self._draw_right_aligned(draw, (x2 - 5, y + 1), play_text, play_font, COLORS["text"])

    def _draw_ncaa_pregame_context(self, draw, x1, y2, x2, board_y2, event, now):
        context_y = max(board_y2 + 7, y2 - 51)
        draw.rounded_rectangle((x1 + 16, context_y, x2 - 16, context_y + 29), radius=4, fill=COLORS["ncaa_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "KICK", x1 + 25, context_y + 6, COLORS["ncaa_accent"])
        label, label_font = self._fit_text(draw, "KICKOFF", 72, 10, bold=True, min_size=7)
        draw.text((x1 + 40, context_y + 5), label, font=label_font, fill=COLORS["ncaa_accent"])
        kick = SportsDashboard._football_kick_label(event, now)
        kick, kick_font = self._fit_text(draw, kick, 92, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, context_y + 5), kick, kick_font, COLORS["text"])
        site = SportsDashboard._football_ncaa_site_label(event)
        if site:
            site, site_font = self._fit_text(draw, site, x2 - x1 - 56, 8, bold=True, min_size=6)
            self._draw_centered(draw, ((x1 + x2) / 2, context_y + 21), site, site_font, COLORS["muted"])
        meta = self._ncaa_main_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_ncaa_final_context(self, draw, x1, y2, x2, board_y2, event):
        context_y = max(board_y2 + 7, y2 - 51)
        draw.rounded_rectangle((x1 + 16, context_y, x2 - 16, context_y + 29), radius=4, fill=COLORS["ncaa_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "SCORE", x1 + 25, context_y + 6, COLORS["ncaa_accent"])
        label, label_font = self._fit_text(draw, "FINAL", 64, 11, bold=True, min_size=8)
        draw.text((x1 + 40, context_y + 5), label, font=label_font, fill=COLORS["ncaa_accent"])
        date_label = ((event or {}).get("start").strftime("%m/%d") if (event or {}).get("start") else "RESULT")
        date_label, date_font = self._fit_text(draw, date_label, 76, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, context_y + 5), date_label, date_font, COLORS["text"])
        site = SportsDashboard._football_ncaa_site_label(event)
        if site:
            site, site_font = self._fit_text(draw, site, x2 - x1 - 56, 8, bold=True, min_size=6)
            self._draw_centered(draw, ((x1 + x2) / 2, context_y + 21), site, site_font, COLORS["muted"])
        meta = self._ncaa_main_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_ncaa_side_column(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        main = (card or {}).get("main") or {}
        upcoming = list((card or {}).get("upcoming") or [])
        recent = list((card or {}).get("recent") or [])
        main_state = self._hub_event_state(main)
        ranked_upcoming = sorted(upcoming, key=SportsDashboard._ncaa_ranked_watch_sort_key)
        if main_state == "live" and (upcoming or recent):
            self._draw_football_info_section(
                draw,
                x1,
                x2,
                y1,
                "COLLEGE DRIVE",
                self._football_live_drive_rows(main, include_context=True, sport="NCAA"),
                COLORS["ncaa_accent"],
            )
            second_y = min(y2 - 76, y1 + 104)
            if upcoming:
                self._draw_hub_section_header(draw, x1, x2, second_y, "RANKED WATCH", COLORS["ncaa_accent"])
                row_y = second_y + 24
                for index, event in enumerate(ranked_upcoming[:2]):
                    self._draw_ncaa_small_row(image, draw, x1, x2, row_y + index * 31, event, True)
            else:
                self._draw_hub_section_header(draw, x1, x2, second_y, "RECENT", COLORS["ncaa_accent"])
                row_y = second_y + 24
                for index, event in enumerate(recent[:2]):
                    self._draw_ncaa_small_row(image, draw, x1, x2, row_y + index * 28, event, False)
            return
        if not upcoming and not recent and main_state == "live":
            self._draw_ncaa_live_side_fallback(draw, x1, y1, x2, y2, main, now)
            return
        if not upcoming and not recent and main_state == "final":
            self._draw_football_info_section(draw, x1, x2, y1, "FINAL SNAP", self._football_final_snap_rows(main, "NCAA"), COLORS["ncaa_accent"])
            return
        self._draw_hub_section_header(draw, x1, x2, y1, "RANKED WATCH", COLORS["ncaa_accent"])
        row_y = y1 + 24
        if upcoming:
            for index, event in enumerate(ranked_upcoming[:3]):
                self._draw_ncaa_small_row(image, draw, x1, x2, row_y + index * 31, event, True)
        else:
            draw.text((x1 + 10, row_y + 6), "No NCAA schedule", font=self._font(10, True), fill=COLORS["muted"])
        recent_y = min(y2 - 76, row_y + max(1, min(3, len(upcoming))) * 31 + 10)
        if recent:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", COLORS["ncaa_accent"])
            recent_row_y = recent_y + 24
            for index, event in enumerate(recent[:2]):
                self._draw_ncaa_small_row(image, draw, x1, x2, recent_row_y + index * 28, event, False)
        elif main_state == "live":
            self._draw_football_info_section(draw, x1, x2, recent_y, "COLLEGE DRIVE", self._football_live_drive_rows(main, include_context=True, sport="NCAA"), COLORS["ncaa_accent"])
        elif main_state == "scheduled":
            self._draw_football_info_section(draw, x1, x2, recent_y, "GAME INFO", self._ncaa_game_info_rows(main, now), COLORS["ncaa_accent"])
        elif main_state == "final":
            self._draw_football_info_section(draw, x1, x2, recent_y, "FINAL SNAP", self._football_final_snap_rows(main, "NCAA"), COLORS["ncaa_accent"])
        else:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", COLORS["ncaa_accent"])
            draw.text((x1 + 10, recent_y + 30), "No recent results", font=self._font(10, True), fill=COLORS["muted"])

    def _draw_ncaa_live_side_fallback(self, draw, x1, y1, x2, y2, event, now):
        self._draw_football_info_section(
            draw,
            x1,
            x2,
            y1,
            "COLLEGE DRIVE",
            self._football_live_drive_rows(event, sport="NCAA"),
            COLORS["ncaa_accent"],
        )
        self._draw_football_info_section(
            draw,
            x1,
            x2,
            min(y2 - 92, y1 + 104),
            "GAME INFO",
            self._football_live_game_info_rows(event, "NCAA"),
            COLORS["ncaa_accent"],
        )

    def _draw_ncaa_small_row(self, image, draw, x1, x2, y, event, show_time):
        draw.rounded_rectangle((x1, y, x2, y + 25), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + 24), fill=COLORS["ncaa_accent"])
        left = self._format_time(event["start"]) if show_time and event.get("start") else (event["start"].strftime("%m/%d") if event.get("start") else "TBD")
        left, left_font = self._fit_text(draw, left, 42, 9, bold=True, min_size=7)
        draw.text((x1 + 10, y + 3), left, font=left_font, fill=COLORS["muted"])
        logo_size = 11
        matchup = self._ncaa_score_matchup_label(event)
        matchup, matchup_font = self._fit_text(draw, matchup, x2 - x1 - 96, 10, bold=True, min_size=7)
        self._draw_small_row_team_logos(image, draw, x1, x2, y + 3, event, matchup, matchup_font, x2 - 25, logo_size)
        self._draw_right_aligned(draw, (x2 - 25, y + 3), matchup, matchup_font, COLORS["text"])
        note = self._football_small_note_label(event)
        if note:
            note, note_font = self._fit_text(draw, note, x2 - x1 - 84, 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 25, y + 15), note, note_font, COLORS["muted"])

    def _draw_standalone_sport_header(self, image, draw, x1, y1, x2, sport, card, source_state):
        sport = str(sport or "MLB").upper()
        accent = self._hub_sport_accent(sport)
        header_y = y1 + 8
        logo_w = 74 if sport in {"MLB", "WNBA"} else 42
        self._draw_sport_logo(image, draw, sport, x1 + 14, header_y - 1, logo_w, 34)
        title_x = x1 + 98 if sport in {"MLB", "WNBA"} else x1 + 66
        title_text = "PGA TOUR" if sport == "PGA" else ("NCAA FB" if sport == "NCAA" else sport)
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

    @staticmethod
    def _standalone_sport_source_label(sport, card, source_state):
        source_label = SportsDashboard._source_label(source_state)
        if not source_label.upper().startswith("HUB "):
            return source_label
        sport = str(sport or (card or {}).get("sport") or "MLB").upper()
        status = str((card or {}).get("status") or "NEXT").upper()
        labels = {
            "MLB": {"LIVE": "LIVE BOX", "NEXT": "FIRST PITCH", "RECENT": "BOX SCORE", "BREAK": "SEASON WATCH"},
            "WNBA": {"LIVE": "LIVE GAME", "NEXT": "TIPOFF", "RECENT": "FINAL SCORE", "BREAK": "SEASON WATCH"},
            "PGA": {"LIVE": "LEADERBOARD", "NEXT": "TEE TIMES", "RECENT": "RESULTS", "BREAK": "TOUR WATCH"},
            "NFL": {"LIVE": "DRIVE CAST", "NEXT": "KICKOFF", "RECENT": "FINAL SNAP", "BREAK": "SEASON WATCH"},
            "NCAA": {"LIVE": "COLLEGE LIVE", "NEXT": "RANKED WATCH", "RECENT": "FINAL BOARD", "BREAK": "RANKED WATCH"},
        }
        return labels.get(sport, {}).get(status, source_label)

    def _draw_sport_logo(self, image, draw, sport, x, y, width, height):
        path = self._sport_logo_path(sport)
        logo = self._load_local_logo(path, (int(width), int(height)), alpha_threshold=8) if path else None
        if logo:
            image.paste(logo, (int(x) + (int(width) - logo.width) // 2, int(y) + (int(height) - logo.height) // 2), logo)
            return
        text, font = self._fit_text(draw, str(sport or "SPORT"), int(width), 11, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x, y, x + width, y + height), text, font, COLORS["muted"])

    @staticmethod
    def _sport_logo_path(sport):
        return {
            "NBA": LOCAL_NBA_LOGO_PATH,
            "MLB": LOCAL_MLB_LOGO_PATH,
            "WNBA": LOCAL_WNBA_LOGO_PATH,
            "PGA": LOCAL_PGA_LOGO_PATH,
            "NFL": LOCAL_NFL_LOGO_PATH,
            "NCAA": LOCAL_NCAA_LOGO_PATH,
        }.get(str(sport or "").upper())

    @staticmethod
    def _hub_sport_accent(sport):
        sport = str(sport or "").upper()
        if sport == "WNBA":
            return COLORS["wnba_accent"]
        if sport == "PGA":
            return COLORS["pga_accent"]
        if sport == "NFL":
            return COLORS["nfl_accent"]
        if sport == "NCAA":
            return COLORS["ncaa_accent"]
        return COLORS["mlb_accent"]

    @staticmethod
    def _football_context_fill_key(sport):
        sport = str(sport or "").upper()
        if sport == "NFL":
            return "nfl_field_tint"
        if sport == "NCAA":
            return "ncaa_field_tint"
        return "panel_blue"

    def _draw_vertical_split(self, draw, x, y1, y2):
        draw.line((x - 3, y1, x - 3, y2), fill=COLORS["border"], width=1)
        draw.line((x - 1, y1, x - 1, y2), fill=COLORS["line"], width=1)

    def _draw_hub_card_shell(self, draw, x1, y1, x2, y2, accent):
        draw.rounded_rectangle((x1 + 3, y1 + 3, x2 + 3, y2 + 3), radius=5, fill=self._blend(accent, COLORS["border"], 0.28))
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, y2 - 1), fill=accent)

    def _draw_mlb_main_card(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        accent = COLORS["mlb_live"] if card.get("status") == "LIVE" else COLORS["mlb_accent"]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        tag, tag_font = self._fit_text(draw, f"MLB {card.get('status') or 'NEXT'}", 92, 12, bold=True, min_size=8)
        draw.rectangle((x1 + 18, y1 + 11, x1 + 112, y1 + 29), fill=COLORS["mlb_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 23, y1 + 12), tag, font=tag_font, fill=COLORS["text"])
        right_label = self._hub_event_time_label(event, now)
        right_label, right_font = self._fit_text(draw, right_label, 94, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 13), right_label, right_font, COLORS["muted"])
        center_x = (x1 + x2) / 2
        top_y = y1 + 45
        away_label = self._mlb_display_team_from_event(event, "a", full=True)
        home_label = self._mlb_display_team_from_event(event, "b", full=True)
        away_fill = COLORS[SportsDashboard._mlb_team_side_fill_key(event, "a")]
        home_fill = COLORS[SportsDashboard._mlb_team_side_fill_key(event, "b")]
        self._draw_hub_team_score(
            draw,
            x1 + 18,
            top_y,
            center_x - 24,
            away_label,
            event.get("wins_a"),
            event.get("record_a"),
            image=image,
            logo_url=event.get("team_a_logo"),
            logo_size=20,
            logo_fallback=SportsDashboard._event_team_logo_fallback(event, "a", "MLB"),
            team_fill=away_fill,
            score_fill=away_fill,
        )
        self._draw_hub_team_score(
            draw,
            center_x + 24,
            top_y,
            x2 - 18,
            home_label,
            event.get("wins_b"),
            event.get("record_b"),
            align="right",
            image=image,
            logo_url=event.get("team_b_logo"),
            logo_size=20,
            logo_fallback=SportsDashboard._event_team_logo_fallback(event, "b", "MLB"),
            team_fill=home_fill,
            score_fill=home_fill,
        )
        score = self._hub_score_label(event)
        score, score_font = self._fit_text(draw, score, 80, 29 if score != "VS" else 23, bold=True, min_size=16)
        self._draw_centered(draw, (center_x, top_y + 29), score, score_font, COLORS["text"])
        if self._hub_event_state(event) == "scheduled":
            self._draw_mlb_pregame_context(draw, x1, y1, x2, y2, event, now)
            return
        if self._hub_event_state(event) == "final":
            self._draw_mlb_final_context(draw, x1, y1, x2, y2, event)
            return
        info_y = y1 + 103
        draw.rounded_rectangle((x1 + 16, info_y, x2 - 16, info_y + 31), radius=4, fill=COLORS["mlb_field_tint"], outline=COLORS["border"], width=1)
        inning = self._mlb_inning_label(event)
        inning, inning_font = self._fit_text(draw, inning, 92, 12, bold=True, min_size=8)
        draw.text((x1 + 25, info_y + 5), inning, font=inning_font, fill=COLORS["mlb_accent"])
        self._draw_mlb_base_diamond(draw, int(center_x - 16), info_y + 2, event.get("bases") or "")
        if not self._draw_mlb_bso_board(draw, x2 - 116, info_y + 6, x2 - 25, info_y + 25, event):
            count = self._mlb_count_label(event)
            count, count_font = self._fit_text(draw, count, 96, 11, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 25, info_y + 6), count, count_font, COLORS["text"])
        self._draw_mlb_rhe_line(draw, x1 + 16, y2 - 58, x2 - 16, event)
        self._draw_mlb_live_matchup_strip(draw, x1 + 20, y2 - 25, x2 - 20, event)

    def _draw_mlb_live_matchup_strip(self, draw, x1, y, x2, event):
        batter = str((event or {}).get("current_batter") or "").strip()
        pitcher = str((event or {}).get("current_pitcher") or "").strip()
        if not (batter or pitcher):
            pitch_text = self._mlb_live_main_meta_label(event)
            pitch_text, pitch_font = self._fit_text(draw, pitch_text, x2 - x1, 10, bold=True, min_size=7)
            self._draw_centered_in_box(draw, (x1, y, x2, y + 16), pitch_text, pitch_font, COLORS["muted"])
            return
        if not batter:
            self._draw_mlb_live_matchup_cell(draw, (x1, y, x2, y + 16), "P", pitcher, COLORS["mlb_accent"])
            return
        if not pitcher:
            self._draw_mlb_live_matchup_cell(draw, (x1, y, x2, y + 16), "BAT", batter, COLORS["amber"])
            return

        gap = 6
        mid = int((x1 + x2) / 2)
        self._draw_mlb_live_matchup_cell(draw, (x1, y, mid - gap // 2, y + 16), "BAT", batter, COLORS["amber"])
        self._draw_mlb_live_matchup_cell(draw, (mid + gap // 2, y, x2, y + 16), "P", pitcher, COLORS["mlb_accent"])

    def _draw_mlb_live_matchup_cell(self, draw, box, label, value, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, label, x1 + 5, y1 + 4, accent)
        label_text, label_font = self._fit_text(draw, label, 22, 7, bold=True, min_size=5)
        draw.text((x1 + 18, y1 + 3), label_text, font=label_font, fill=accent)
        value_text, value_font = self._fit_text(draw, str(value or "TBD"), max(28, x2 - x1 - 47), 8, bold=True, min_size=6)
        self._draw_right_aligned(draw, (x2 - 5, y1 + 4), value_text, value_font, COLORS["text"])

    def _draw_mlb_bso_board(self, draw, x1, y1, x2, y2, event):
        event = event or {}
        if SportsDashboard._hub_event_state(event) != "live":
            return False
        raw_values = (event.get("balls"), event.get("strikes"), event.get("outs"))
        if all(value is None for value in raw_values):
            return False
        values = (
            ("B", raw_values[0], COLORS["mlb_accent"]),
            ("S", raw_values[1], COLORS["amber"]),
            ("O", raw_values[2], COLORS["red"]),
        )
        x1, y1, x2, y2 = [int(value) for value in (x1, y1, x2, y2)]
        gap = 3
        cell_width = max(24, int((x2 - x1 - gap * 2) / 3))
        for index, (label, raw_value, accent) in enumerate(values):
            parsed = SportsDashboard._lpl_int_value(raw_value)
            value = "-" if parsed is None else str(parsed)
            cell_x1 = x1 + index * (cell_width + gap)
            cell_x2 = x2 if index == 2 else cell_x1 + cell_width
            outs = raw_value if label == "O" else None
            self._draw_mlb_bso_cell(draw, (cell_x1, y1, cell_x2, y2), label, value, accent, outs=outs)
        return True

    def _draw_mlb_bso_cell(self, draw, box, label, value, accent, outs=None):
        x1, y1, x2, y2 = [int(value) for value in box]
        fill = self._blend(accent, COLORS["panel"], 0.2)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=fill, outline=accent, width=1)
        label_text, label_font = self._fit_text(draw, str(label), 8, 7, bold=True, min_size=5)
        draw.text((x1 + 3, y1 + 2), label_text, font=label_font, fill=accent)
        value_text, value_font = self._fit_text(draw, str(value), 10, 9, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 4, y1 + 3), value_text, value_font, COLORS["text"])
        if label != "O":
            return
        out_count = SportsDashboard._lpl_int_value(outs)
        out_count = 0 if out_count is None else max(0, min(3, out_count))
        for index in range(3):
            cx = x1 + 6 + index * 5
            dot_fill = COLORS["red"] if index < out_count else COLORS["panel"]
            dot_outline = COLORS["amber"] if index < out_count else COLORS["border"]
            draw.ellipse((cx, y2 - 5, cx + 2, y2 - 3), fill=dot_fill, outline=dot_outline, width=1)

    def _draw_mlb_pregame_context(self, draw, x1, y1, x2, y2, event, now):
        info_y = y1 + 103
        draw.rounded_rectangle((x1 + 16, info_y, x2 - 16, info_y + 31), radius=4, fill=COLORS["mlb_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "FIRST", x1 + 25, info_y + 10, COLORS["mlb_accent"])
        label, label_font = self._fit_text(draw, "FIRST PITCH", 92, 10, bold=True, min_size=7)
        draw.text((x1 + 40, info_y + 8), label, font=label_font, fill=COLORS["mlb_accent"])
        first = self._mlb_first_pitch_label(event, now)
        first, first_font = self._fit_text(draw, first, 94, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, info_y + 8), first, first_font, COLORS["text"])

        details = SportsDashboard._mlb_pregame_detail_rows(event)
        row_y = y2 - 63
        for label, value in details:
            self._draw_mlb_pregame_detail_row(draw, x1 + 16, row_y, x2 - 16, label, value)
            row_y += 19

    def _draw_mlb_pregame_detail_row(self, draw, x1, y, x2, label, value):
        draw.rounded_rectangle((x1, y, x2, y + 16), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, label, x1 + 8, y + 4, COLORS["mlb_accent"])
        label_text, label_font = self._fit_text(draw, str(label), 34, 7, bold=True, min_size=5)
        draw.text((x1 + 23, y + 4), label_text, font=label_font, fill=COLORS["mlb_accent"])
        value_text, value_font = self._fit_text(draw, str(value), x2 - x1 - 64, 9, bold=True, min_size=6)
        self._draw_right_aligned(draw, (x2 - 6, y + 4), value_text, value_font, COLORS["text"])

    def _draw_mlb_final_context(self, draw, x1, y1, x2, y2, event):
        info_y = y1 + 103
        draw.rounded_rectangle((x1 + 16, info_y, x2 - 16, info_y + 31), radius=4, fill=COLORS["mlb_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "SCORE", x1 + 25, info_y + 10, COLORS["mlb_accent"])
        label, label_font = self._fit_text(draw, "FINAL RESULT", 102, 10, bold=True, min_size=7)
        draw.text((x1 + 40, info_y + 8), label, font=label_font, fill=COLORS["mlb_accent"])
        date_label = self._mlb_final_date_label(event)
        date_label, date_font = self._fit_text(draw, date_label, 72, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, info_y + 8), date_label, date_font, COLORS["text"])

        self._draw_mlb_rhe_line(draw, x1 + 16, y2 - 58, x2 - 16, event)
        meta = self._mlb_final_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 25, x2 - 20, y2 - 9), meta, meta_font, COLORS["muted"])

    def _draw_mlb_side_column(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        main = (card or {}).get("main") or {}
        upcoming = list((card or {}).get("upcoming") or [])
        recent = list((card or {}).get("recent") or [])
        main_state = self._hub_event_state(main)
        if main_state == "live":
            if not upcoming and not recent:
                self._draw_mlb_live_state_section(draw, x1, x2, y1, y2, main)
                return
            secondary_y = min(y2 - 54, y1 + 136)
            self._draw_mlb_live_state_section(draw, x1, x2, y1, max(y1 + 88, secondary_y - 2), main)
            if upcoming:
                self._draw_hub_section_header(draw, x1, x2, secondary_y, "UPCOMING", COLORS["mlb_accent"])
                row_y = secondary_y + 24
                for index, event in enumerate(upcoming[:1]):
                    self._draw_mlb_small_row(image, draw, x1, x2, row_y + index * 31, event, now, show_time=True)
            else:
                self._draw_hub_section_header(draw, x1, x2, secondary_y, "RECENT", COLORS["mlb_accent"])
                row_y = secondary_y + 24
                for index, event in enumerate(recent[:1]):
                    self._draw_mlb_small_row(image, draw, x1, x2, row_y + index * 28, event, now, show_time=False)
            return
        if not upcoming and not recent and self._hub_event_state(main) == "final":
            self._draw_mlb_final_snap_section(draw, x1, x2, y1, y2, main)
            return
        self._draw_hub_section_header(draw, x1, x2, y1, "UPCOMING", COLORS["mlb_accent"])
        row_y = y1 + 24
        if upcoming:
            for index, event in enumerate(upcoming[:3]):
                self._draw_mlb_small_row(image, draw, x1, x2, row_y + index * 31, event, now, show_time=True)
        else:
            draw.text((x1 + 10, row_y + 6), "No MLB schedule", font=self._font(10, True), fill=COLORS["muted"])
        recent_y = min(y2 - 76, row_y + max(1, min(3, len(upcoming))) * 31 + 10)
        if recent:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", COLORS["mlb_accent"])
            recent_row_y = recent_y + 24
            for index, event in enumerate(recent[:2]):
                self._draw_mlb_small_row(image, draw, x1, x2, recent_row_y + index * 28, event, now, show_time=False)
        elif main_state == "live":
            self._draw_mlb_live_state_section(draw, x1, x2, recent_y, y2, main)
        elif main_state == "scheduled":
            self._draw_mlb_game_info_section(draw, x1, x2, recent_y, y2, main, now)
        elif main_state == "final":
            self._draw_mlb_final_snap_section(draw, x1, x2, recent_y, y2, main)
        else:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", COLORS["mlb_accent"])
            recent_row_y = recent_y + 24
            draw.text((x1 + 10, recent_row_y + 6), "No recent results", font=self._font(10, True), fill=COLORS["muted"])

    def _draw_mlb_live_state_section(self, draw, x1, x2, y, y2, event):
        self._draw_hub_section_header(draw, x1, x2, y, "LIVE STATE", COLORS["mlb_accent"])
        row_y = y + 24
        for index, (label, value) in enumerate(self._mlb_live_state_rows(event)):
            top = row_y + index * 18
            if top + 14 > y2 - 4:
                break
            draw.line((x1, top - 2, x2, top - 2), fill=COLORS["line"], width=1)
            self._draw_sport_info_icon(draw, label, x1 + 3, top + 1, COLORS["mlb_accent"])
            label, label_font = self._fit_text(draw, label, 40, 8, bold=True, min_size=6)
            draw.text((x1 + 17, top), label, font=label_font, fill=COLORS["muted"])
            value, value_font = self._fit_text(draw, value, x2 - x1 - 70, 10, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 3, top), value, value_font, COLORS["text"])

    def _draw_mlb_final_snap_section(self, draw, x1, x2, y, y2, event):
        self._draw_hub_section_header(draw, x1, x2, y, "FINAL SNAP", COLORS["mlb_accent"])
        row_y = y + 24
        rows = self._mlb_final_snap_rows(event)
        if not rows:
            draw.text((x1 + 10, row_y + 6), "No recent results", font=self._font(10, True), fill=COLORS["muted"])
            return
        for index, (label, value) in enumerate(rows):
            top = row_y + index * 18
            if top + 14 > y2 - 4:
                break
            draw.line((x1, top - 2, x2, top - 2), fill=COLORS["line"], width=1)
            self._draw_sport_info_icon(draw, label, x1 + 3, top + 1, COLORS["mlb_accent"])
            label, label_font = self._fit_text(draw, label, 40, 8, bold=True, min_size=6)
            draw.text((x1 + 17, top), label, font=label_font, fill=COLORS["muted"])
            value, value_font = self._fit_text(draw, value, x2 - x1 - 70, 10, bold=True, min_size=7)
            value_fill = COLORS["mlb_accent"] if str(label).upper() == "WIN" else COLORS["text"]
            self._draw_right_aligned(draw, (x2 - 3, top), value, value_font, value_fill)

    def _draw_mlb_game_info_section(self, draw, x1, x2, y, y2, event, now):
        self._draw_hub_section_header(draw, x1, x2, y, "GAME INFO", COLORS["mlb_accent"])
        row_y = y + 24
        for index, (label, value) in enumerate(self._mlb_game_info_rows(event, now)):
            top = row_y + index * 18
            if top + 14 > y2 - 4:
                break
            draw.line((x1, top - 2, x2, top - 2), fill=COLORS["line"], width=1)
            self._draw_sport_info_icon(draw, label, x1 + 3, top + 1, COLORS["mlb_accent"])
            label, label_font = self._fit_text(draw, label, 40, 8, bold=True, min_size=6)
            draw.text((x1 + 17, top), label, font=label_font, fill=COLORS["muted"])
            value, value_font = self._fit_text(draw, value, x2 - x1 - 70, 10, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 3, top), value, value_font, COLORS["text"])

    def _draw_mlb_small_row(self, image, draw, x1, x2, y, event, now, show_time):
        draw.rounded_rectangle((x1, y, x2, y + 25), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + 24), fill=COLORS["mlb_accent"])
        left = self._format_time(event["start"]) if show_time and event.get("start") else (event["start"].strftime("%m/%d") if event.get("start") else "TBD")
        left, left_font = self._fit_text(draw, left, 42, 9, bold=True, min_size=7)
        draw.text((x1 + 10, y + 2), left, font=left_font, fill=COLORS["muted"])
        logo_size = 11
        label = f"{self._mlb_display_team_from_event(event, 'a')} {self._hub_score_label(event)} {self._mlb_display_team_from_event(event, 'b')}"
        label, label_font = self._fit_text(draw, label, x2 - x1 - 96, 10, bold=True, min_size=7)
        self._draw_small_row_team_logos(image, draw, x1, x2, y + 3, event, label, label_font, x2 - 25, logo_size)
        self._draw_right_aligned(draw, (x2 - 25, y + 2), label, label_font, COLORS["text"])
        note = self._mlb_small_note_label(event)
        if note:
            is_live = SportsDashboard._hub_event_state(event) == "live"
            has_base_icon = is_live and bool(str(event.get("bases") or "").strip())
            count_chip = SportsDashboard._mlb_count_chip_label(event) if is_live else ""
            has_count_chip = bool(count_chip)
            if has_base_icon and has_count_chip:
                note_width = x2 - x1 - 148
            elif has_count_chip:
                note_width = x2 - x1 - 120
            else:
                note_width = x2 - x1 - (112 if has_base_icon else 84)
            if has_base_icon:
                self._draw_mlb_mini_base_diamond(draw, x1 + 58, y + 9, event.get("bases"))
            if has_count_chip:
                self._draw_mlb_count_chip(draw, x1 + (84 if has_base_icon else 58), y + 16, count_chip, event.get("outs"))
            note, note_font = self._fit_text(draw, note, note_width, 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 25, y + 15), note, note_font, COLORS["muted"])

    def _draw_wnba_main_card(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        accent = COLORS["wnba_live"] if card.get("status") == "LIVE" else COLORS["wnba_accent"]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        tag, tag_font = self._fit_text(draw, f"WNBA {card.get('status') or 'NEXT'}", 100, 12, bold=True, min_size=8)
        draw.rectangle((x1 + 18, y1 + 11, x1 + 120, y1 + 29), fill=COLORS["wnba_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 23, y1 + 12), tag, font=tag_font, fill=COLORS["text"])
        right_label = self._hub_event_time_label(event, now)
        right_label, right_font = self._fit_text(draw, right_label, 86, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 13), right_label, right_font, COLORS["muted"])
        court_y1 = y1 + 43
        court_y2 = y2 - 42
        draw.rounded_rectangle((x1 + 16, court_y1, x2 - 16, court_y2), radius=5, fill=COLORS["wnba_court"], outline=COLORS["border"], width=1)
        center_x = (x1 + x2) / 2
        draw.line((center_x, court_y1 + 3, center_x, court_y2 - 3), fill=COLORS["line"], width=1)
        draw.ellipse((center_x - 24, court_y1 + 20, center_x + 24, court_y1 + 68), outline=COLORS["line"], width=2)
        away_label = self._wnba_display_team_from_event(event, "a", full=True)
        home_label = self._wnba_display_team_from_event(event, "b", full=True)
        away_fill = COLORS[SportsDashboard._wnba_score_side_fill_key(event, "a")]
        home_fill = COLORS[SportsDashboard._wnba_score_side_fill_key(event, "b")]
        self._draw_hub_team_score(draw, x1 + 27, court_y1 + 11, center_x - 24, away_label, event.get("wins_a"), event.get("record_a"), image=image, logo_url=event.get("team_a_logo"), logo_size=20, logo_fallback=SportsDashboard._wnba_logo_fallback(event, "a"), team_fill=away_fill, score_fill=away_fill)
        self._draw_hub_team_score(draw, center_x + 24, court_y1 + 11, x2 - 27, home_label, event.get("wins_b"), event.get("record_b"), align="right", image=image, logo_url=event.get("team_b_logo"), logo_size=20, logo_fallback=SportsDashboard._wnba_logo_fallback(event, "b"), team_fill=home_fill, score_fill=home_fill)
        score = self._hub_score_label(event)
        score, score_font = self._fit_text(draw, score, 86, 28 if score != "VS" else 23, bold=True, min_size=16)
        self._draw_centered(draw, (center_x, court_y1 + 43), score, score_font, COLORS["text"])
        self._draw_wnba_court_quarter_strip(draw, x1 + 36, court_y2 - 39, x2 - 36, event)
        if self._hub_event_state(event) == "scheduled":
            self._draw_wnba_pregame_context(draw, x1, x2, court_y2, y2, event, now)
            return
        if self._hub_event_state(event) == "final":
            self._draw_wnba_final_context(draw, x1, x2, court_y2, y2, event, now)
            return
        self._draw_wnba_live_context(draw, x1, x2, court_y2, y2, event, now)

    def _draw_wnba_court_quarter_strip(self, draw, x1, y, x2, event):
        rows = SportsDashboard._wnba_quarter_rows(event)
        if not rows:
            return
        period = SportsDashboard._lpl_int_value((event or {}).get("period"))
        y = int(y)
        gap = 3
        count = min(4, len(rows))
        cell_w = max(34, int((x2 - x1 - gap * (count - 1)) / count))
        for index, (quarter, score) in enumerate(rows[:count]):
            left = int(x1 + index * (cell_w + gap))
            right = int(x2 if index == count - 1 else left + cell_w)
            active = period == index + 1
            self._draw_wnba_quarter_score_cell(draw, (left, y, right, y + 13), quarter, score, active)

    def _draw_wnba_quarter_score_cell(self, draw, box, quarter, score, active=False):
        x1, y1, x2, y2 = [int(value) for value in box]
        accent = COLORS["wnba_accent"] if active else COLORS["line"]
        fill = self._blend(COLORS["wnba_accent"], COLORS["panel"], 0.22) if active else COLORS["panel"]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=fill, outline=accent, width=1)
        quarter_text, quarter_font = self._fit_text(draw, str(quarter or "Q"), 17, 6, bold=True, min_size=5)
        draw.text((x1 + 4, y1 + 3), quarter_text, font=quarter_font, fill=COLORS["wnba_accent"] if active else COLORS["muted"])
        score_text, score_font = self._fit_text(draw, str(score or "-"), max(14, x2 - x1 - 25), 7, bold=True, min_size=5)
        self._draw_right_aligned(draw, (x2 - 4, y1 + 3), score_text, score_font, COLORS["text"])

    def _draw_wnba_pregame_context(self, draw, x1, x2, court_y2, y2, event, now):
        tip_y = court_y2 - 25
        draw.rounded_rectangle((x1 + 26, tip_y, x2 - 26, tip_y + 20), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "TIP", x1 + 34, tip_y + 6, COLORS["wnba_accent"])
        label, label_font = self._fit_text(draw, "TIP OFF", 72, 9, bold=True, min_size=7)
        draw.text((x1 + 49, tip_y + 5), label, font=label_font, fill=COLORS["wnba_accent"])
        tip = self._wnba_tip_label(event, now)
        tip, tip_font = self._fit_text(draw, tip, 92, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 35, tip_y + 5), tip, tip_font, COLORS["text"])
        meta = SportsDashboard._wnba_pregame_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 44, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 18, y2 - 30, x2 - 18, y2 - 10), meta, meta_font, COLORS["muted"])

    def _draw_wnba_live_context(self, draw, x1, x2, court_y2, y2, event, now):
        pulse_y = court_y2 - 25
        draw.rounded_rectangle((x1 + 26, pulse_y, x2 - 26, pulse_y + 20), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "CLOCK", x1 + 34, pulse_y + 6, COLORS["wnba_accent"])
        label, label_font = self._fit_text(draw, "LIVE PULSE", 78, 9, bold=True, min_size=7)
        draw.text((x1 + 49, pulse_y + 5), label, font=label_font, fill=COLORS["wnba_accent"])
        status = str((event or {}).get("status_text") or "").strip() or self._hub_event_time_label(event, now)
        status, status_font = self._fit_text(draw, status, 92, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 35, pulse_y + 5), status, status_font, COLORS["text"])
        self._draw_wnba_live_summary_strip(draw, x1 + 18, y2 - 30, x2 - 18, event)

    def _draw_wnba_live_summary_strip(self, draw, x1, y, x2, event):
        lead = SportsDashboard._wnba_lead_label(event)
        if lead == "TBD":
            lead = ""
        quarter = SportsDashboard._wnba_current_quarter_label(event)
        items = []
        if lead:
            items.append(("LEAD", lead, COLORS["wnba_accent"]))
        if quarter:
            items.append(("QTR", quarter, COLORS["amber"]))
        broadcast = str((event or {}).get("broadcast") or "").strip()
        spread = str((event or {}).get("spread") or "").strip()
        total = str((event or {}).get("over_under") or "").strip()
        if broadcast:
            items.append(("TV", " / ".join(part for part in (broadcast, spread, total) if part), COLORS["wnba_accent"]))
        elif spread:
            items.append(("SPREAD", " / ".join(part for part in (spread, total) if part), COLORS["wnba_accent"]))
        elif total:
            items.append(("TOTAL", total, COLORS["wnba_accent"]))
        if not items:
            meta = SportsDashboard._wnba_live_main_meta_label(event)
            meta, meta_font = self._fit_text(draw, meta, x2 - x1, 10, bold=True, min_size=7)
            self._draw_centered_in_box(draw, (x1, y, x2, y + 20), meta, meta_font, COLORS["muted"])
            return
        if len(items) == 1:
            self._draw_wnba_live_summary_cell(draw, (x1, y, x2, y + 20), items[0][0], items[0][1], items[0][2])
            return
        items = items[:3]
        gap = 4 if len(items) > 2 else 6
        cell_w = max(58, int((x2 - x1 - gap * (len(items) - 1)) / len(items)))
        for index, (label, value, accent) in enumerate(items):
            left = int(x1 + index * (cell_w + gap))
            right = int(x2 if index == len(items) - 1 else left + cell_w)
            self._draw_wnba_live_summary_cell(draw, (left, y, right, y + 20), label, value, accent)

    def _draw_wnba_live_summary_cell(self, draw, box, label, value, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, label, x1 + 5, y1 + 6, accent)
        compact = (x2 - x1) < 94
        label_budget = 23 if compact else 32
        value_budget = max(24, x2 - x1 - (45 if compact else 55))
        label_text, label_font = self._fit_text(draw, label, label_budget, 7, bold=True, min_size=5)
        draw.text((x1 + 18, y1 + 5), label_text, font=label_font, fill=accent)
        value_text, value_font = self._fit_text(draw, str(value or "TBD"), value_budget, 9, bold=True, min_size=6)
        self._draw_right_aligned(draw, (x2 - 5, y1 + 5), value_text, value_font, COLORS["text"])

    def _draw_wnba_final_context(self, draw, x1, x2, court_y2, y2, event, now):
        snap_y = court_y2 - 25
        draw.rounded_rectangle((x1 + 26, snap_y, x2 - 26, snap_y + 20), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "SCORE", x1 + 34, snap_y + 6, COLORS["wnba_accent"])
        label, label_font = self._fit_text(draw, "RESULT SNAP", 88, 9, bold=True, min_size=7)
        draw.text((x1 + 49, snap_y + 5), label, font=label_font, fill=COLORS["wnba_accent"])
        status = str((event or {}).get("status_text") or "").strip() or self._hub_event_time_label(event, now)
        status, status_font = self._fit_text(draw, status, 74, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 35, snap_y + 5), status, status_font, COLORS["text"])
        meta = SportsDashboard._wnba_final_main_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 44, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 18, y2 - 30, x2 - 18, y2 - 10), meta, meta_font, COLORS["muted"])

    @staticmethod
    def _wnba_live_main_meta_label(event):
        parts = []
        lead = SportsDashboard._wnba_lead_label(event)
        if lead and lead != "TBD":
            parts.append(lead)
        quarter = SportsDashboard._wnba_current_quarter_label(event)
        if quarter:
            parts.append(quarter)
        line = SportsDashboard._wnba_live_line_label(event)
        if line:
            parts.append(line)
        return " / ".join(parts[:3]) or SportsDashboard._nba_period_label(event, max_parts=2) or "LIVE GAME"

    @staticmethod
    def _wnba_live_line_label(event):
        event = event or {}
        parts = []
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast:
            parts.append(f"TV {broadcast}")
        spread = str(event.get("spread") or "").strip()
        if spread:
            parts.append(f"SPREAD {spread}")
        total = str(event.get("over_under") or "").strip()
        if total:
            parts.append(total)
        return "  |  ".join(parts)

    @staticmethod
    def _wnba_final_main_meta_label(event):
        result = SportsDashboard._wnba_result_meta_label(event)
        period = SportsDashboard._nba_period_label(event, max_parts=2)
        if result and period and " / " not in result:
            return f"{result} / {period}"
        return result or period or "FINAL RESULT"

    @staticmethod
    def _wnba_pregame_meta_label(event):
        parts = []
        broadcast = str((event or {}).get("broadcast") or "").strip()
        spread = str((event or {}).get("spread") or "").strip()
        total = str((event or {}).get("over_under") or "").strip()
        line_parts = [part for part in (spread, total) if part]
        if broadcast:
            tv_parts = [f"TV {broadcast}"] + line_parts
            parts.append(" | ".join(tv_parts))
        elif line_parts:
            parts.append(" / ".join(line_parts))
        venue = str((event or {}).get("venue") or (event or {}).get("city") or "").strip()
        if venue:
            parts.append(venue)
        status = str((event or {}).get("status_text") or "").strip()
        block = str((event or {}).get("block") or "").strip()
        if parts:
            if status and len(parts) < 3:
                parts.append(status)
            if block and len(parts) < 3:
                parts.append(block)
            return " / ".join(parts[:3]) or "WNBA GAME INFO"
        record = SportsDashboard._wnba_record_matchup_label(event)
        if record:
            parts.append(record)
        if status and len(parts) < 2:
            parts.append(status)
        if block and len(parts) < 2:
            parts.append(block)
        return " / ".join(parts[:2]) or "WNBA GAME INFO"

    @staticmethod
    def _wnba_result_meta_label(event):
        event = event or {}
        parts = []
        lead = SportsDashboard._wnba_lead_label(event)
        if lead and lead != "TBD":
            parts.append(lead)
        status = str(event.get("status_text") or "").strip()
        if status:
            parts.append(status)
        record = SportsDashboard._wnba_record_matchup_label(event)
        if record and len(parts) < 2:
            parts.append(record)
        block = str(event.get("block") or "").strip()
        if block and len(parts) < 2:
            parts.append(block)
        return " / ".join(parts[:2]) or "WNBA RESULT"

    def _draw_wnba_side_column(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        main = (card or {}).get("main") or {}
        upcoming = list((card or {}).get("upcoming") or [])
        recent = list((card or {}).get("recent") or [])
        main_state = self._hub_event_state(main)
        recent_only_main = len(recent) == 1 and (
            recent[0] is main
            or (
                str(recent[0].get("event_id") or "")
                and str(recent[0].get("event_id") or "") == str(main.get("event_id") or "")
            )
        )
        if main_state == "live":
            if not upcoming and not recent:
                self._draw_wnba_live_side_fallback(image, draw, x1, y1, x2, y2, main)
                return
            secondary_y = min(y2 - 54, y1 + 136)
            self._draw_wnba_live_pulse_section(image, draw, x1, x2, y1, max(y1 + 88, secondary_y - 2), main)
            if upcoming:
                self._draw_hub_section_header(draw, x1, x2, secondary_y, "UPCOMING", COLORS["wnba_accent"])
                row_y = secondary_y + 24
                for index, event in enumerate(upcoming[:1]):
                    self._draw_wnba_small_row(image, draw, x1, x2, row_y + index * 31, event, True)
            else:
                self._draw_hub_section_header(draw, x1, x2, secondary_y, "RECENT", COLORS["wnba_accent"])
                row_y = secondary_y + 24
                for index, event in enumerate(recent[:1]):
                    self._draw_wnba_small_row(image, draw, x1, x2, row_y + index * 28, event, False)
            return
        if not upcoming and main_state == "final" and (not recent or recent_only_main):
            self._draw_wnba_result_side_fallback(draw, x1, y1, x2, y2, main)
            return
        self._draw_hub_section_header(draw, x1, x2, y1, "UPCOMING", COLORS["wnba_accent"])
        row_y = y1 + 24
        if upcoming:
            for index, event in enumerate(upcoming[:3]):
                self._draw_wnba_small_row(image, draw, x1, x2, row_y + index * 31, event, True)
        else:
            draw.text((x1 + 10, row_y + 6), "No WNBA schedule", font=self._font(10, True), fill=COLORS["muted"])
        recent_y = min(y2 - 76, row_y + max(1, min(3, len(upcoming))) * 31 + 10)
        if recent:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", COLORS["wnba_accent"])
            recent_row_y = recent_y + 24
            for index, event in enumerate(recent[:2]):
                self._draw_wnba_small_row(image, draw, x1, x2, recent_row_y + index * 28, event, False)
        elif main_state == "live":
            self._draw_wnba_live_pulse_section(image, draw, x1, x2, recent_y, y2, main)
        elif main_state == "scheduled":
            self._draw_wnba_game_info_section(draw, x1, x2, recent_y, y2, main, now)
        else:
            self._draw_wnba_result_snap_section(draw, x1, x2, recent_y, y2, main)

    def _draw_wnba_live_side_fallback(self, image, draw, x1, y1, x2, y2, event):
        self._draw_hub_section_header(draw, x1, x2, y1, "LIVE GAME", COLORS["wnba_accent"])
        row_y = y1 + 24
        status = str(event.get("status_text") or "").strip() or self._hub_event_time_label(event, None)
        self._draw_wnba_live_info_row(draw, x1, x2, row_y, "CLOCK", status)
        self._draw_wnba_live_team_row(image, draw, x1, x2, row_y + 18, event, "a")
        self._draw_wnba_live_team_row(image, draw, x1, x2, row_y + 36, event, "b")
        info_y = row_y + 54
        lead = self._wnba_lead_label(event)
        if lead and lead != "TBD":
            self._draw_wnba_live_info_row(draw, x1, x2, info_y, "LEAD", lead, accent=True)
            info_y += 18
        broadcast = str(event.get("broadcast") or "").strip()
        line = " / ".join(
            part
            for part in (
                str(event.get("spread") or "").strip(),
                str(event.get("over_under") or "").strip(),
            )
            if part
        )
        if broadcast:
            self._draw_wnba_live_info_row(draw, x1, x2, info_y, "TV", " / ".join(part for part in (broadcast, line) if part))
            info_y += 18
            line = ""
        if line:
            self._draw_wnba_live_info_row(draw, x1, x2, info_y, "SPREAD", line)
            info_y += 18
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue:
            self._draw_wnba_live_info_row(draw, x1, x2, info_y, "VENUE", venue)
            info_y += 18

        quarter_y = min(y2 - 76, max(y1 + 104, info_y + 6))
        self._draw_hub_section_header(draw, x1, x2, quarter_y, "QUARTER LOG", COLORS["wnba_accent"])
        period = SportsDashboard._lpl_int_value(event.get("period"))
        for index, label in enumerate(self._wnba_quarter_rows(event)[:4]):
            top = quarter_y + 22 + index * 14
            if top + 10 > y2 - 2:
                break
            score_color = COLORS["wnba_accent"] if period == index + 1 else COLORS["text"]
            draw.line((x1, top - 2, x2, top - 2), fill=COLORS["line"], width=1)
            quarter, score = label
            self._draw_sport_info_icon(draw, "PERIOD", x1 + 3, top + 1, COLORS["wnba_accent"])
            quarter, quarter_font = self._fit_text(draw, quarter, 32, 8, bold=True, min_size=6)
            draw.text((x1 + 17, top), quarter, font=quarter_font, fill=score_color)
            score, score_font = self._fit_text(draw, score, 58, 9, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 3, top), score, score_font, score_color)

    def _draw_wnba_live_pulse_section(self, image, draw, x1, x2, y, y2, event):
        self._draw_hub_section_header(draw, x1, x2, y, "LIVE PULSE", COLORS["wnba_accent"])
        row_y = y + 24
        for index, row in enumerate(self._wnba_live_pulse_rows(event)):
            top = row_y + index * 18
            if top + 14 > y2 - 4:
                break
            label, value, accent = row
            if label == "SCORE":
                self._draw_wnba_live_score_row(image, draw, x1, x2, top, event)
            else:
                self._draw_wnba_live_info_row(draw, x1, x2, top, label, value, accent=accent)

    def _draw_wnba_result_side_fallback(self, draw, x1, y1, x2, y2, event):
        quarter_rows = self._wnba_quarter_rows(event)
        self._draw_wnba_result_snap_section(
            draw,
            x1,
            x2,
            y1,
            min(y2, y1 + 104),
            event,
            max_rows=4,
            include_quarter=not quarter_rows,
        )
        if not quarter_rows:
            return
        quarter_y = min(y2 - 96, y1 + 104)
        self._draw_hub_section_header(draw, x1, x2, quarter_y, "QUARTER LOG", COLORS["wnba_accent"])
        for index, row in enumerate(quarter_rows[:4]):
            top = quarter_y + 24 + index * 18
            if top + 14 > y2 - 4:
                break
            quarter, score = row
            draw.line((x1, top - 2, x2, top - 2), fill=COLORS["line"], width=1)
            self._draw_sport_info_icon(draw, "PERIOD", x1 + 3, top + 1, COLORS["wnba_accent"])
            quarter, quarter_font = self._fit_text(draw, quarter, 32, 9, bold=True, min_size=7)
            draw.text((x1 + 17, top), quarter, font=quarter_font, fill=COLORS["muted"])
            score, score_font = self._fit_text(draw, score, 58, 10, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 3, top), score, score_font, COLORS["text"])

    def _draw_wnba_result_snap_section(self, draw, x1, x2, y, y2, event, max_rows=5, include_quarter=True):
        self._draw_hub_section_header(draw, x1, x2, y, "RESULT SNAP", COLORS["wnba_accent"])
        row_y = y + 24
        rows = self._wnba_result_snap_rows(event, include_quarter=include_quarter)
        if not rows:
            draw.text((x1 + 10, row_y + 6), "No recent results", font=self._font(10, True), fill=COLORS["muted"])
            return
        for index, row in enumerate(rows[:max_rows]):
            top = row_y + index * 18
            if top + 14 > y2 - 4:
                break
            label, value, accent = row
            self._draw_wnba_live_info_row(draw, x1, x2, top, label, value, accent=accent)

    def _draw_wnba_game_info_section(self, draw, x1, x2, y, y2, event, now):
        self._draw_hub_section_header(draw, x1, x2, y, "GAME INFO", COLORS["wnba_accent"])
        row_y = y + 24
        for index, row in enumerate(self._wnba_game_info_rows(event, now)):
            top = row_y + index * 18
            if top + 14 > y2 - 4:
                break
            label, value, accent = row
            self._draw_wnba_live_info_row(draw, x1, x2, top, label, value, accent=accent)

    @staticmethod
    def _wnba_game_info_rows(event, now):
        event = event or {}
        rows = []
        tip = SportsDashboard._wnba_tip_label(event, now)
        status = str(event.get("status_text") or "").strip()
        if tip:
            tip_parts = [tip]
            if status and status != tip:
                tip_parts.append(status)
            rows.append(("TIP", " / ".join(tip_parts[:2]), False))
        team_a = SportsDashboard._wnba_display_team_from_event(event, "a")
        team_b = SportsDashboard._wnba_display_team_from_event(event, "b")
        rows.append(("MATCH", f"{team_a} @ {team_b}", True))
        rows.extend(SportsDashboard._wnba_media_context_rows(event))
        record = SportsDashboard._wnba_record_matchup_label(event)
        if record:
            rows.append(("RECORD", record, False))
        return rows

    @staticmethod
    def _wnba_media_context_rows(event):
        event = event or {}
        rows = []
        broadcast = str(event.get("broadcast") or "").strip()
        spread = str(event.get("spread") or "").strip()
        total = str(event.get("over_under") or "").strip()
        if broadcast:
            tv_parts = [broadcast]
            if spread:
                tv_parts.append(spread)
            if total:
                tv_parts.append(total)
            rows.append(("TV", " / ".join(tv_parts), False))
        elif spread:
            line_parts = [spread]
            if total:
                line_parts.append(total)
            rows.append(("SPREAD", " / ".join(line_parts), False))
        elif total:
            rows.append(("TOTAL", total, False))
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue:
            rows.append(("VENUE", venue, False))
        return rows

    @staticmethod
    def _wnba_result_snap_rows(event, include_quarter=True):
        event = event or {}
        rows = []
        lead = SportsDashboard._wnba_lead_label(event)
        if lead and lead != "TBD":
            label = "WIN" if SportsDashboard._hub_event_state(event) == "final" else "LEAD"
            rows.append((label, lead, True))
        score = SportsDashboard._wnba_score_line_label(event)
        if score:
            rows.append(("SCORE", score, False))
        quarter = SportsDashboard._nba_period_label(event, max_parts=2)
        if include_quarter and quarter:
            rows.append(("QTR", quarter, False))
        rows.extend(SportsDashboard._wnba_media_context_rows(event))
        record = SportsDashboard._wnba_record_matchup_label(event)
        if record:
            rows.append(("RECORD", record, False))
        status = str(event.get("status_text") or "").strip()
        if status:
            rows.append(("STATUS", status, False))
        block = str(event.get("block") or "").strip()
        if block and not status:
            rows.append(("STATUS", block, False))
        return rows

    @staticmethod
    def _wnba_score_line_label(event):
        event = event or {}
        score_a = event.get("wins_a")
        score_b = event.get("wins_b")
        if score_a is None or score_b is None:
            return ""
        team_a = SportsDashboard._wnba_display_team_from_event(event, "a")
        team_b = SportsDashboard._wnba_display_team_from_event(event, "b")
        return f"{team_a} {score_a} / {team_b} {score_b}"

    @staticmethod
    def _wnba_tip_label(event, now):
        start = (event or {}).get("start")
        if start:
            return f"{start.strftime('%m/%d')} {SportsDashboard._format_time(start)}"
        return SportsDashboard._hub_event_time_label(event, now)

    @staticmethod
    def _wnba_record_matchup_label(event):
        team_a = SportsDashboard._wnba_display_team_from_event(event, "a")
        team_b = SportsDashboard._wnba_display_team_from_event(event, "b")
        record_a = str((event or {}).get("record_a") or "").strip()
        record_b = str((event or {}).get("record_b") or "").strip()
        if record_a and record_b:
            return f"{team_a} {record_a} / {team_b} {record_b}"
        if record_a:
            return f"{team_a} {record_a}"
        if record_b:
            return f"{team_b} {record_b}"
        return ""

    @staticmethod
    def _wnba_live_pulse_rows(event):
        event = event or {}
        rows = []
        status = str(event.get("status_text") or "").strip()
        if status:
            rows.append(("CLOCK", status, False))
        lead = SportsDashboard._wnba_lead_label(event)
        if lead and lead != "TBD":
            rows.append(("LEAD", lead, True))
        score_a = event.get("wins_a")
        score_b = event.get("wins_b")
        if score_a is not None and score_b is not None:
            team_a = SportsDashboard._wnba_display_team_from_event(event, "a")
            team_b = SportsDashboard._wnba_display_team_from_event(event, "b")
            rows.append(("SCORE", f"{team_a} {score_a} / {team_b} {score_b}", False))
        quarter = SportsDashboard._wnba_current_quarter_label(event)
        if quarter:
            rows.append(("QTR", quarter, False))
        broadcast = str(event.get("broadcast") or "").strip()
        spread = str(event.get("spread") or "").strip()
        total = str(event.get("over_under") or "").strip()
        if broadcast:
            rows.append(("TV", " / ".join(part for part in (broadcast, spread, total) if part), False))
        elif spread:
            rows.append(("SPREAD", " / ".join(part for part in (spread, total) if part), False))
        elif total:
            rows.append(("TOTAL", total, False))
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue:
            rows.append(("VENUE", venue, False))
        return rows

    def _draw_wnba_live_info_row(self, draw, x1, x2, y, label, value, accent=False):
        draw.line((x1, y - 2, x2, y - 2), fill=COLORS["line"], width=1)
        self._draw_sport_info_icon(draw, label, x1 + 3, y + 1, COLORS["wnba_accent"])
        label, label_font = self._fit_text(draw, label, 40, 8, bold=True, min_size=6)
        draw.text((x1 + 17, y), label, font=label_font, fill=COLORS["muted"])
        value, value_font = self._fit_text(draw, value or "TBD", x2 - x1 - 70, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 3, y), value, value_font, COLORS["wnba_accent"] if accent else COLORS["text"])

    def _draw_wnba_live_score_row(self, image, draw, x1, x2, y, event):
        draw.line((x1, y - 2, x2, y - 2), fill=COLORS["line"], width=1)
        self._draw_sport_info_icon(draw, "SCORE", x1 + 3, y + 1, COLORS["wnba_accent"])
        label, label_font = self._fit_text(draw, "SCORE", 38, 8, bold=True, min_size=6)
        draw.text((x1 + 17, y), label, font=label_font, fill=COLORS["muted"])
        content_x1 = x1 + 58
        content_w = max(92, x2 - content_x1 - 2)
        mid_x = content_x1 + content_w // 2
        score_a = "-" if (event or {}).get("wins_a") is None else str((event or {}).get("wins_a"))
        score_b = "-" if (event or {}).get("wins_b") is None else str((event or {}).get("wins_b"))
        self._draw_wnba_live_score_team(image, draw, content_x1, mid_x - 3, y, event, "a", score_a)
        self._draw_wnba_live_score_team(image, draw, mid_x + 4, x2 - 2, y, event, "b", score_b)
        draw.line((mid_x, y + 1, mid_x, y + 12), fill=COLORS["line"], width=1)

    def _draw_wnba_live_score_team(self, image, draw, x1, x2, y, event, side, score):
        prefix = "team_a" if side == "a" else "team_b"
        logo_size = 11
        logo_url = (event or {}).get(f"{prefix}_logo")
        fallback = SportsDashboard._wnba_logo_fallback(event, side)
        team = SportsDashboard._wnba_display_team_from_event(event, side)
        fill = COLORS[SportsDashboard._wnba_score_side_fill_key(event, side)]
        self._draw_team_logo(image, draw, logo_url, x1, y, logo_size, fallback)
        team_width = max(16, x2 - x1 - 28)
        team, team_font = self._fit_text(draw, team, team_width, 9, bold=True, min_size=6)
        draw.text((x1 + logo_size + 3, y), team, font=team_font, fill=fill)
        score, score_font = self._fit_text(draw, score, 20, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2, y), score, score_font, fill)

    def _draw_wnba_live_team_row(self, image, draw, x1, x2, y, event, side):
        draw.line((x1, y - 2, x2, y - 2), fill=COLORS["line"], width=1)
        event = event or {}
        prefix = "team_a" if side == "a" else "team_b"
        team = SportsDashboard._wnba_display_team_from_event(event, side)
        score = event.get(f"wins_{side}")
        logo_url = event.get(f"{prefix}_logo")
        logo_size = 11
        self._draw_team_logo(image, draw, logo_url, x1 + 3, y, logo_size, SportsDashboard._wnba_logo_fallback(event, side))
        fill = COLORS[SportsDashboard._wnba_score_side_fill_key(event, side)]
        team, team_font = self._fit_text(draw, team, x2 - x1 - 82, 10, bold=True, min_size=7)
        draw.text((x1 + 18, y), team, font=team_font, fill=fill)
        score = "-" if score is None else str(score)
        score, score_font = self._fit_text(draw, score, 34, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 3, y), score, score_font, fill)

    @staticmethod
    def _wnba_score_side_fill_key(event, side):
        winner_side = SportsDashboard._wnba_winner_side(event)
        if winner_side:
            return "wnba_accent" if winner_side == side else "text"
        score_a = SportsDashboard._lpl_int_value((event or {}).get("wins_a"))
        score_b = SportsDashboard._lpl_int_value((event or {}).get("wins_b"))
        if score_a is None or score_b is None or score_a == score_b:
            return "text"
        if side == "a" and score_a > score_b:
            return "wnba_accent"
        if side == "b" and score_b > score_a:
            return "wnba_accent"
        return "text"

    @staticmethod
    def _wnba_lead_label(event):
        score_a = SportsDashboard._lpl_int_value((event or {}).get("wins_a"))
        score_b = SportsDashboard._lpl_int_value((event or {}).get("wins_b"))
        team_a = SportsDashboard._wnba_display_team_from_event(event, "a")
        team_b = SportsDashboard._wnba_display_team_from_event(event, "b")
        winner_side = SportsDashboard._wnba_winner_side(event)
        if winner_side:
            winner_team = SportsDashboard._wnba_display_team_from_event(event, winner_side, full=True)
            if score_a is not None and score_b is not None and score_a != score_b:
                return f"{winner_team} \u80dc{abs(score_a - score_b)}\u5206"
            return f"{winner_team} \u80dc"
        if score_a is None or score_b is None:
            return "TBD"
        if score_a == score_b:
            return "TIE"
        if score_a > score_b:
            return f"{team_a} +{score_a - score_b}"
        return f"{team_b} +{score_b - score_a}"

    @staticmethod
    def _wnba_winner_side(event):
        if SportsDashboard._hub_event_state(event) != "final":
            return ""
        event = event or {}
        if event.get("winner_a") is True and event.get("winner_b") is not True:
            return "a"
        if event.get("winner_b") is True and event.get("winner_a") is not True:
            return "b"
        score_a = SportsDashboard._lpl_int_value(event.get("wins_a"))
        score_b = SportsDashboard._lpl_int_value(event.get("wins_b"))
        if score_a is None or score_b is None or score_a == score_b:
            return ""
        return "a" if score_a > score_b else "b"

    @staticmethod
    def _wnba_display_team_from_event(event, side, full=False):
        prefix = "team_a" if side == "a" else "team_b"
        raw_event = event or {}
        raw_team = str(raw_event.get(prefix) or "").strip()
        raw_code = str(raw_event.get(f"{prefix}_code") or "").strip()
        raw_team_code = raw_team.upper() if re.fullmatch(r"[A-Za-z]{2,4}", raw_team) else ""
        code = raw_team_code or raw_code or raw_team
        fallback = raw_team or raw_code or "TBD"
        aliases = [
            raw_event.get(f"{prefix}_name"),
            raw_team,
            raw_code,
        ]
        return SportsDashboard._wnba_display_team_name(code, fallback, aliases, full=full)

    @staticmethod
    def _wnba_logo_fallback(event, side):
        prefix = "team_a" if side == "a" else "team_b"
        raw_event = event or {}
        raw_team = str(raw_event.get(prefix) or "").strip()
        raw_code = str(raw_event.get(f"{prefix}_code") or "").strip()
        raw_name = str(raw_event.get(f"{prefix}_name") or "").strip()
        for value in (raw_code, raw_team):
            value = str(value or "").strip().upper()
            if re.fullmatch(r"[A-Z]{2,4}", value):
                return value
        code = SportsDashboard._wnba_normalized_team_code(raw_code or raw_team, raw_name or raw_team, [raw_name, raw_team])
        if code and code != "TBD":
            return code
        return raw_code or raw_team or raw_name or "TBD"

    @staticmethod
    def _wnba_quarter_rows(event):
        scores_a = list((event or {}).get("period_scores_a") or [])
        scores_b = list((event or {}).get("period_scores_b") or [])
        rows = []
        for index in range(min(len(scores_a), len(scores_b), 4)):
            rows.append((f"Q{index + 1}", f"{scores_a[index]}-{scores_b[index]}"))
        return rows

    @staticmethod
    def _wnba_current_quarter_label(event):
        period = SportsDashboard._lpl_int_value((event or {}).get("period"))
        if period is None or period <= 0:
            return ""
        rows = SportsDashboard._wnba_quarter_rows(event)
        if period <= len(rows):
            quarter, score = rows[period - 1]
            return f"{quarter} {score}"
        return f"Q{period}"

    @staticmethod
    def _wnba_small_note_label(event, show_time=True):
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            status = str((event or {}).get("status_text") or "").strip() or SportsDashboard._nba_period_label(event, max_parts=2)
            lead = SportsDashboard._wnba_lead_label(event)
            parts = []
            if status:
                parts.append(status)
            if lead and lead != "TBD":
                parts.append(lead)
            return " / ".join(parts[:2])
        if state == "final":
            status = str((event or {}).get("status_text") or "Final").strip() or "Final"
            venue = str((event or {}).get("venue") or (event or {}).get("city") or "").strip()
            return " / ".join(part for part in (status, venue) if part)
        if not show_time:
            return SportsDashboard._nba_period_label(event, max_parts=2) or str((event or {}).get("status_text") or "").strip()
        parts = []
        broadcast = str((event or {}).get("broadcast") or "").strip()
        if broadcast:
            parts.append(broadcast)
        spread = str((event or {}).get("spread") or "").strip()
        total = str((event or {}).get("over_under") or "").strip()
        if spread:
            parts.append(spread)
        elif total:
            parts.append(total)
        venue = str((event or {}).get("venue") or (event or {}).get("city") or "").strip()
        if venue and len(parts) < 2:
            parts.append(venue)
        if parts:
            return " / ".join(parts[:2])
        return str((event or {}).get("status_text") or "").strip()

    def _draw_wnba_small_row(self, image, draw, x1, x2, y, event, show_time):
        draw.rounded_rectangle((x1, y, x2, y + 25), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + 24), fill=COLORS["wnba_accent"])
        left = self._format_time(event["start"]) if show_time and event.get("start") else (event["start"].strftime("%m/%d") if event.get("start") else "TBD")
        left, left_font = self._fit_text(draw, left, 42, 9, bold=True, min_size=7)
        draw.text((x1 + 10, y + 3), left, font=left_font, fill=COLORS["muted"])
        logo_size = 11
        label = f"{self._wnba_display_team_from_event(event, 'a')} {self._hub_score_label(event)} {self._wnba_display_team_from_event(event, 'b')}"
        label, label_font = self._fit_text(draw, label, x2 - x1 - 96, 10, bold=True, min_size=7)
        self._draw_small_row_team_logos(image, draw, x1, x2, y + 3, event, label, label_font, x2 - 25, logo_size)
        self._draw_right_aligned(draw, (x2 - 25, y + 3), label, label_font, COLORS["text"])
        note = self._wnba_small_note_label(event, show_time=show_time)
        if note:
            period = SportsDashboard._lpl_int_value((event or {}).get("period"))
            has_period_chips = SportsDashboard._hub_event_state(event) == "live" and bool(period and period > 0)
            lead_chip = SportsDashboard._wnba_lead_chip_label(event) if SportsDashboard._hub_event_state(event) == "live" else ""
            note_width = x2 - x1 - (140 if lead_chip else (112 if has_period_chips else 84))
            if has_period_chips:
                self._draw_wnba_period_chips(draw, x1 + 58, y + 16, period)
            if lead_chip:
                self._draw_wnba_lead_chip(draw, x1 + 86, y + 16, lead_chip)
            note, note_font = self._fit_text(draw, note, note_width, 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 25, y + 15), note, note_font, COLORS["muted"])

    def _draw_wnba_period_chips(self, draw, x, y, period):
        current = max(1, min(4, SportsDashboard._lpl_int_value(period) or 1))
        for index in range(4):
            left = int(x) + index * 6
            top = int(y)
            fill = COLORS["wnba_accent"] if index + 1 <= current else COLORS["panel"]
            outline = COLORS["amber"] if index + 1 == current else COLORS["border"]
            draw.rectangle((left, top, left + 4, top + 5), fill=fill, outline=outline, width=1)

    def _draw_wnba_lead_chip(self, draw, x, y, label):
        label = str(label or "").strip()
        if not label:
            return
        x = int(x)
        y = int(y)
        box = (x, y, x + 25, y + 9)
        fill = self._blend(COLORS["wnba_accent"], COLORS["panel"], 0.28)
        draw.rounded_rectangle(box, radius=2, fill=fill, outline=COLORS["wnba_accent"], width=1)
        draw.ellipse((x + 2, y + 2, x + 7, y + 7), outline=COLORS["amber"], width=1)
        draw.line((x + 4, y + 2, x + 4, y + 7), fill=COLORS["amber"], width=1)
        draw.arc((x + 1, y + 1, x + 6, y + 8), 300, 60, fill=COLORS["amber"], width=1)
        label, label_font = self._fit_text(draw, label, 13, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (x + 10, y + 1, x + 24, y + 8), label, label_font, COLORS["text"])

    @staticmethod
    def _wnba_lead_chip_label(event):
        score_a = SportsDashboard._lpl_int_value((event or {}).get("wins_a"))
        score_b = SportsDashboard._lpl_int_value((event or {}).get("wins_b"))
        if score_a is None or score_b is None or score_a == score_b:
            return ""
        return f"+{abs(score_a - score_b)}"

    def _draw_football_main_card(self, image, draw, bounds, card, now, sport):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        accent = COLORS["red"] if card.get("status") == "LIVE" else self._hub_sport_accent(sport)
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        tag = f"{sport} {card.get('status') or 'NEXT'}" if sport == "NFL" else f"NCAA {card.get('status') or 'NEXT'}"
        tag, tag_font = self._fit_text(draw, tag, 96, 12, bold=True, min_size=8)
        tag_fill = COLORS["nfl_tag"] if sport == "NFL" else COLORS["ncaa_tag"]
        draw.rectangle((x1 + 18, y1 + 11, x1 + 116, y1 + 29), fill=tag_fill, outline=COLORS["border"], width=1)
        draw.text((x1 + 23, y1 + 12), tag, font=tag_font, fill=COLORS["text"])
        week = self._football_header_week_label(card, event)
        week, week_font = self._fit_text(draw, week or self._hub_event_time_label(event, now), 84, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 13), week, week_font, COLORS["muted"])
        if sport == "NCAA":
            header_badge = SportsDashboard._ncaa_header_badge_label(event)
            if header_badge:
                badge, badge_font = self._fit_text(draw, header_badge, 82, 9, bold=True, min_size=7)
                draw.text((x1 + 123, y1 + 16), badge, font=badge_font, fill=COLORS["amber"])

        field_y1 = y1 + 39
        field_y2 = y2 - 58
        self._draw_football_field(draw, x1 + 17, field_y1, x2 - 17, field_y2, sport, event)
        center_x = (x1 + x2) / 2
        away_label = self._football_display_team(event, "a", sport, full=True)
        home_label = self._football_display_team(event, "b", sport, full=True)
        away_fill = COLORS[SportsDashboard._football_score_side_fill_key(event, "a", sport)]
        home_fill = COLORS[SportsDashboard._football_score_side_fill_key(event, "b", sport)]
        self._draw_hub_team_score(draw, x1 + 28, field_y1 + 13, center_x - 28, away_label, event.get("wins_a"), event.get("record_a"), image=image, logo_url=event.get("team_a_logo"), logo_size=20, logo_fallback=SportsDashboard._event_team_logo_fallback(event, "a", sport), team_fill=away_fill, score_fill=away_fill)
        self._draw_hub_team_score(draw, center_x + 28, field_y1 + 13, x2 - 28, home_label, event.get("wins_b"), event.get("record_b"), align="right", image=image, logo_url=event.get("team_b_logo"), logo_size=20, logo_fallback=SportsDashboard._event_team_logo_fallback(event, "b", sport), team_fill=home_fill, score_fill=home_fill)
        score = self._hub_score_label(event)
        score, score_font = self._fit_text(draw, score, 84, 29 if score != "VS" else 23, bold=True, min_size=16)
        self._draw_centered(draw, (center_x, field_y1 + 44), score, score_font, COLORS["text"])
        if self._hub_event_state(event) != "scheduled":
            status = str(event.get("status_text") or self._hub_event_time_label(event, now)).upper()
            status, status_font = self._fit_text(draw, status, 86, 12, bold=True, min_size=8)
            self._draw_centered(draw, (center_x, field_y1 + 19), status, status_font, COLORS["amber"] if card.get("status") == "LIVE" else COLORS["muted"])
        if self._hub_event_state(event) == "final":
            self._draw_football_final_context(draw, x1, y2, x2, field_y2, event, sport)
            return
        if self._hub_event_state(event) == "scheduled":
            self._draw_football_pregame_context(draw, x1, y2, x2, field_y2, event, sport, now)
            return

        situation_y = max(field_y2 + 7, y2 - 51)
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, situation_y + 28), radius=4, fill=COLORS[self._football_context_fill_key(sport)], outline=COLORS["border"], width=1)
        down = str(event.get("down_distance") or ("NEUTRAL SITE" if event.get("neutral_site") else event.get("note") or "SCHEDULED")).upper()
        down, down_font = self._fit_text(draw, down, 98, 11, bold=True, min_size=7)
        draw.text((x1 + 25, situation_y + 5), down, font=down_font, fill=self._hub_sport_accent(sport))
        yard = str(event.get("yard_line") or event.get("note") or event.get("city") or "").strip()
        yard, yard_font = self._fit_text(draw, yard, x2 - x1 - 150, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 24, situation_y + 6), yard, yard_font, COLORS["text"])
        possession = SportsDashboard._football_possession_display_label(event, sport)
        if possession:
            pos, pos_font = self._fit_text(draw, f"POS {possession}", 70, 9, bold=True, min_size=7)
            self._draw_centered(draw, (center_x, situation_y + 20), pos, pos_font, COLORS["amber"])

        meta = self._football_meta_label(event, sport)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_nfl_main_card(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        status = str((card or {}).get("status") or "NEXT").upper()
        accent = COLORS["nfl_live"] if status == "LIVE" else COLORS["nfl_accent"]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        tag, tag_font = self._fit_text(draw, f"NFL {status}", 94, 12, bold=True, min_size=8)
        draw.rectangle((x1 + 18, y1 + 11, x1 + 116, y1 + 29), fill=COLORS["nfl_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 23, y1 + 12), tag, font=tag_font, fill=COLORS["text"])
        week = self._football_header_week_label(card, event)
        week, week_font = self._fit_text(draw, week or self._hub_event_time_label(event, now), 84, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 13), week, week_font, COLORS["muted"])

        field_y1 = y1 + 39
        field_y2 = y2 - 62
        self._draw_nfl_field(draw, x1 + 17, field_y1, x2 - 17, field_y2, event)
        center_x = (x1 + x2) / 2
        away_label = self._football_display_team(event, "a", "NFL", full=True)
        home_label = self._football_display_team(event, "b", "NFL", full=True)
        away_fill = COLORS[SportsDashboard._football_score_side_fill_key(event, "a", "NFL")]
        home_fill = COLORS[SportsDashboard._football_score_side_fill_key(event, "b", "NFL")]
        self._draw_hub_team_score(
            draw,
            x1 + 28,
            field_y1 + 13,
            center_x - 28,
            away_label,
            event.get("wins_a"),
            event.get("record_a"),
            image=image,
            logo_url=event.get("team_a_logo"),
            logo_size=20,
            logo_fallback=event.get("team_a_code") or event.get("team_a"),
            team_fill=away_fill,
            score_fill=away_fill,
        )
        self._draw_hub_team_score(
            draw,
            center_x + 28,
            field_y1 + 13,
            x2 - 28,
            home_label,
            event.get("wins_b"),
            event.get("record_b"),
            align="right",
            image=image,
            logo_url=event.get("team_b_logo"),
            logo_size=20,
            logo_fallback=event.get("team_b_code") or event.get("team_b"),
            team_fill=home_fill,
            score_fill=home_fill,
        )
        score = self._hub_score_label(event)
        score, score_font = self._fit_text(draw, score, 84, 29 if score != "VS" else 23, bold=True, min_size=16)
        self._draw_centered(draw, (center_x, field_y1 + 44), score, score_font, COLORS["text"])
        if self._hub_event_state(event) != "scheduled":
            status_text = str(event.get("status_text") or self._hub_event_time_label(event, now)).upper()
            status_text, status_font = self._fit_text(draw, status_text, 86, 12, bold=True, min_size=8)
            self._draw_centered(draw, (center_x, field_y1 + 18), status_text, status_font, COLORS["amber"] if status == "LIVE" else COLORS["muted"])

        if self._hub_event_state(event) == "final":
            self._draw_nfl_final_context(draw, x1, y2, x2, field_y2, event)
            return
        if self._hub_event_state(event) == "scheduled":
            self._draw_nfl_pregame_context(draw, x1, y2, x2, field_y2, event, now)
            return
        self._draw_nfl_live_drive_context(draw, x1, y2, x2, field_y2, event)

    def _draw_nfl_pregame_context(self, draw, x1, y2, x2, field_y2, event, now):
        situation_y = max(field_y2 + 7, y2 - 55)
        accent = COLORS["nfl_accent"]
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, situation_y + 34), radius=4, fill=COLORS["nfl_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "KICK", x1 + 25, situation_y + 6, accent)
        label, label_font = self._fit_text(draw, "NFL KICK", 78, 9, bold=True, min_size=7)
        draw.text((x1 + 40, situation_y + 4), label, font=label_font, fill=accent)
        kick = SportsDashboard._football_kick_label(event, now)
        kick, kick_font = self._fit_text(draw, kick, 92, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, situation_y + 4), kick, kick_font, COLORS["text"])
        line = SportsDashboard._nfl_broadcast_line_label(event)
        line, line_font = self._fit_text(draw, line, x2 - x1 - 58, 10, bold=True, min_size=7)
        draw.text((x1 + 25, situation_y + 19), line, font=line_font, fill=COLORS["amber"] if line != "NFL PREGAME" else COLORS["muted"])
        meta = SportsDashboard._nfl_venue_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_nfl_final_context(self, draw, x1, y2, x2, field_y2, event):
        situation_y = max(field_y2 + 7, y2 - 55)
        accent = COLORS["nfl_accent"]
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, situation_y + 34), radius=4, fill=COLORS["nfl_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "SCORE", x1 + 25, situation_y + 6, accent)
        label, label_font = self._fit_text(draw, "FINAL SCORE", 92, 9, bold=True, min_size=7)
        draw.text((x1 + 40, situation_y + 4), label, font=label_font, fill=accent)
        date_label = ""
        start = (event or {}).get("start")
        if start:
            date_label = start.strftime("%m/%d")
        date_label, date_font = self._fit_text(draw, date_label or "RESULT", 76, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, situation_y + 4), date_label, date_font, COLORS["text"])
        line = SportsDashboard._nfl_broadcast_line_label(event)
        line, line_font = self._fit_text(draw, line, x2 - x1 - 58, 10, bold=True, min_size=7)
        draw.text((x1 + 25, situation_y + 19), line, font=line_font, fill=COLORS["amber"] if line != "NFL FINAL" else COLORS["muted"])
        meta = SportsDashboard._nfl_venue_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_nfl_live_drive_context(self, draw, x1, y2, x2, field_y2, event):
        last_play = str((event or {}).get("last_play") or "").strip()
        has_play = bool(last_play)
        situation_y = max(field_y2 + 4, y2 - (58 if has_play else 55))
        accent = COLORS["nfl_accent"]
        box_bottom = situation_y + (43 if has_play else 34)
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, box_bottom), radius=4, fill=COLORS["nfl_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "DOWN", x1 + 25, situation_y + 6, accent)
        label, label_font = self._fit_text(draw, "NFL DRIVE", 78, 9, bold=True, min_size=7)
        draw.text((x1 + 40, situation_y + 4), label, font=label_font, fill=accent)
        possession = SportsDashboard._football_possession_display_label(event, "NFL")
        if possession:
            pos, pos_font = self._fit_text(draw, f"POS {possession}", 68, 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 25, situation_y + 4), pos, pos_font, COLORS["amber"])
        self._draw_nfl_live_drive_chips(draw, x1 + 24, situation_y + 17, x2 - 24, event)
        if has_play:
            self._draw_nfl_last_play_strip(draw, x1 + 24, situation_y + 32, x2 - 24, last_play)
        meta = self._football_meta_label(event, "NFL")
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        meta_box = (x1 + 20, y2 - 12, x2 - 20, y2 - 2) if has_play else (x1 + 20, y2 - 18, x2 - 20, y2 - 4)
        self._draw_centered_in_box(draw, meta_box, meta, meta_font, COLORS["muted"])

    def _draw_nfl_last_play_strip(self, draw, x1, y, x2, play):
        play = str(play or "").strip()
        if not play:
            return
        y = int(y)
        draw.rounded_rectangle((x1, y, x2, y + 10), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "PLAY", x1 + 5, y + 1, COLORS["nfl_accent"])
        label, label_font = self._fit_text(draw, "PLAY", 28, 6, bold=True, min_size=5)
        draw.text((x1 + 18, y + 1), label, font=label_font, fill=COLORS["nfl_accent"])
        play_text, play_font = self._fit_text(draw, play, max(44, x2 - x1 - 58), 7, bold=True, min_size=5)
        self._draw_right_aligned(draw, (x2 - 5, y + 1), play_text, play_font, COLORS["text"])

    def _draw_nfl_live_drive_chips(self, draw, x1, y, x2, event):
        down = str((event or {}).get("down_distance") or "").strip().upper()
        field = str((event or {}).get("yard_line") or (event or {}).get("note") or "").strip()
        last_play = str((event or {}).get("last_play") or "").strip()
        items = []
        if down:
            items.append(("DOWN", down, COLORS["amber"]))
        if field:
            items.append(("FIELD", field, COLORS["nfl_accent"]))
        if not items:
            if last_play:
                items.append(("PLAY", "LAST PLAY", COLORS["amber"]))
            else:
                items.append(("DOWN", "LIVE DRIVE", COLORS["amber"]))
        if len(items) == 1:
            self._draw_nfl_live_drive_chip(draw, (x1, y, x2, y + 14), items[0][0], items[0][1], items[0][2])
            return
        gap = 6
        mid = int((x1 + x2) / 2)
        self._draw_nfl_live_drive_chip(draw, (x1, y, mid - gap // 2, y + 14), items[0][0], items[0][1], items[0][2])
        self._draw_nfl_live_drive_chip(draw, (mid + gap // 2, y, x2, y + 14), items[1][0], items[1][1], items[1][2])

    def _draw_nfl_live_drive_chip(self, draw, box, label, value, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, label, x1 + 5, y1 + 3, accent)
        label_text, label_font = self._fit_text(draw, label, 34, 6, bold=True, min_size=5)
        draw.text((x1 + 18, y1 + 3), label_text, font=label_font, fill=accent)
        value_text, value_font = self._fit_text(draw, str(value or "TBD"), max(24, x2 - x1 - 58), 8, bold=True, min_size=6)
        self._draw_right_aligned(draw, (x2 - 5, y1 + 3), value_text, value_font, COLORS["text"])

    def _draw_football_pregame_context(self, draw, x1, y2, x2, field_y2, event, sport, now):
        situation_y = max(field_y2 + 7, y2 - 51)
        accent = self._hub_sport_accent(sport)
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, situation_y + 29), radius=4, fill=COLORS[self._football_context_fill_key(sport)], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "KICK", x1 + 25, situation_y + 6, accent)
        label, label_font = self._fit_text(draw, "KICKOFF", 72, 10, bold=True, min_size=7)
        draw.text((x1 + 40, situation_y + 5), label, font=label_font, fill=accent)
        kick = SportsDashboard._football_kick_label(event, now)
        kick, kick_font = self._fit_text(draw, kick, 92, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, situation_y + 5), kick, kick_font, COLORS["text"])
        headline = SportsDashboard._football_pregame_headline(event, sport)
        if headline:
            headline, headline_font = self._fit_text(draw, headline, x2 - x1 - 56, 8, bold=True, min_size=6)
            self._draw_centered(draw, ((x1 + x2) / 2, situation_y + 21), headline, headline_font, COLORS["muted"])
        meta = SportsDashboard._football_pregame_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_football_final_context(self, draw, x1, y2, x2, field_y2, event, sport):
        situation_y = max(field_y2 + 7, y2 - 51)
        accent = self._hub_sport_accent(sport)
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, situation_y + 29), radius=4, fill=COLORS[self._football_context_fill_key(sport)], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "SCORE", x1 + 25, situation_y + 6, accent)
        label, label_font = self._fit_text(draw, "FINAL", 64, 11, bold=True, min_size=8)
        draw.text((x1 + 40, situation_y + 5), label, font=label_font, fill=accent)
        date_label = ""
        start = (event or {}).get("start")
        if start:
            date_label = start.strftime("%m/%d")
        date_label, date_font = self._fit_text(draw, date_label or "RESULT", 76, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, situation_y + 5), date_label, date_font, COLORS["text"])
        headline = SportsDashboard._football_final_headline(event, sport)
        if headline:
            headline, headline_font = self._fit_text(draw, headline, x2 - x1 - 56, 8, bold=True, min_size=6)
            self._draw_centered(draw, ((x1 + x2) / 2, situation_y + 21), headline, headline_font, COLORS["muted"])
        meta = SportsDashboard._football_final_meta_label(event, sport)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_football_side_column(self, image, draw, bounds, card, now, sport):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        main = (card or {}).get("main") or {}
        upcoming = list((card or {}).get("upcoming") or [])
        recent = list((card or {}).get("recent") or [])
        if sport == "NCAA":
            upcoming = sorted(upcoming, key=SportsDashboard._ncaa_ranked_watch_sort_key)
        if self._hub_event_state(main) == "live" and (upcoming or recent):
            accent = self._hub_sport_accent(sport)
            self._draw_football_info_section(
                draw,
                x1,
                x2,
                y1,
                self._football_live_drive_title(sport),
                self._football_live_drive_rows(main, include_context=True, sport=sport),
                accent,
            )
            second_y = min(y2 - 76, y1 + 104)
            if upcoming:
                title = "RANKED WATCH" if sport == "NCAA" else "UPCOMING"
                self._draw_hub_section_header(draw, x1, x2, second_y, title, accent)
                row_y = second_y + 24
                for index, event in enumerate(upcoming[:2]):
                    self._draw_football_small_row(image, draw, x1, x2, row_y + index * 31, event, True, sport)
            else:
                self._draw_hub_section_header(draw, x1, x2, second_y, "RECENT", accent)
                row_y = second_y + 24
                for index, event in enumerate(recent[:2]):
                    self._draw_football_small_row(image, draw, x1, x2, row_y + index * 28, event, False, sport)
            return
        if not upcoming and not recent and self._hub_event_state(main) == "live":
            self._draw_football_live_side_fallback(draw, x1, y1, x2, y2, main, sport)
            return
        if not upcoming and not recent and self._hub_event_state(main) == "final":
            self._draw_football_info_section(draw, x1, x2, y1, "FINAL SNAP", self._football_final_snap_rows(main, sport), self._hub_sport_accent(sport))
            return
        title = "RANKED WATCH" if sport == "NCAA" else "UPCOMING"
        self._draw_hub_section_header(draw, x1, x2, y1, title, self._hub_sport_accent(sport))
        row_y = y1 + 24
        if upcoming:
            for index, event in enumerate(upcoming[:3]):
                self._draw_football_small_row(image, draw, x1, x2, row_y + index * 31, event, True, sport)
        else:
            empty = "No NCAA schedule" if sport == "NCAA" else "No NFL schedule"
            draw.text((x1 + 10, row_y + 6), empty, font=self._font(10, True), fill=COLORS["muted"])
        recent_y = min(y2 - 76, row_y + max(1, min(3, len(upcoming))) * 31 + 10)
        if recent:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", self._hub_sport_accent(sport))
            recent_row_y = recent_y + 24
            for index, event in enumerate(recent[:2]):
                self._draw_football_small_row(image, draw, x1, x2, recent_row_y + index * 28, event, False, sport)
        elif self._hub_event_state(main) == "live":
            self._draw_football_info_section(draw, x1, x2, recent_y, self._football_live_drive_title(sport), self._football_live_drive_rows(main, include_context=True, sport=sport), self._hub_sport_accent(sport))
        elif self._hub_event_state(main) == "scheduled":
            self._draw_football_info_section(draw, x1, x2, recent_y, "GAME INFO", self._football_game_info_rows(main, sport, now), self._hub_sport_accent(sport))
        elif self._hub_event_state(main) == "final":
            self._draw_football_info_section(draw, x1, x2, recent_y, "FINAL SNAP", self._football_final_snap_rows(main, sport), self._hub_sport_accent(sport))
        else:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", self._hub_sport_accent(sport))
            recent_row_y = recent_y + 24
            draw.text((x1 + 10, recent_row_y + 6), "No recent results", font=self._font(10, True), fill=COLORS["muted"])

    def _draw_football_live_side_fallback(self, draw, x1, y1, x2, y2, event, sport):
        accent = self._hub_sport_accent(sport)
        self._draw_football_info_section(draw, x1, x2, y1, self._football_live_drive_title(sport), self._football_live_drive_rows(event, sport=sport), accent)
        self._draw_football_info_section(draw, x1, x2, min(y2 - 92, y1 + 104), "GAME INFO", self._football_live_game_info_rows(event, sport), accent)

    @staticmethod
    def _football_live_drive_title(sport):
        return "COLLEGE DRIVE" if str(sport or "").upper() == "NCAA" else "LIVE DRIVE"

    @staticmethod
    def _football_live_drive_rows(event, include_context=False, sport=None):
        line = str((event or {}).get("yard_line") or "").strip()
        possession = SportsDashboard._football_possession_display_label(event, sport or (event or {}).get("sport") or "")
        field = line
        if possession:
            field = f"{line} / POS {possession}" if line else f"POS {possession}"
        rows = []
        status = str((event or {}).get("status_text") or "").strip().upper()
        down = str((event or {}).get("down_distance") or "").strip().upper()
        if status:
            rows.append(("QTR", status))
        if down:
            rows.append(("DOWN", down))
        if field:
            rows.append(("FIELD", field))
        last_play = str((event or {}).get("last_play") or "").strip()
        if last_play:
            rows.append(("PLAY", last_play))
        if include_context:
            rows.extend(SportsDashboard._football_compact_context_rows(event, combine_line_with_tv=True))
        return [(label, value) for label, value in rows if str(value or "").strip()][:5]

    @staticmethod
    def _football_live_context_row(event):
        rows = SportsDashboard._football_compact_context_rows(event)
        return rows[0] if rows else None

    @staticmethod
    def _football_compact_context_rows(event, combine_line_with_tv=False):
        event = event or {}
        rows = []
        broadcast = str(event.get("broadcast") or "").strip()
        line = SportsDashboard._football_line_label(event)
        if broadcast:
            if combine_line_with_tv:
                rows.append(("TV", " / ".join(part for part in (broadcast, line) if part)))
                line = ""
            else:
                rows.append(("TV", broadcast))
        if line:
            rows.append(("SPREAD", line))
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue:
            rows.append(("VENUE", venue))
        return rows

    @staticmethod
    def _football_live_game_info_rows(event, sport):
        event = event or {}
        sport = str(sport or event.get("sport") or "").upper()
        rows = []
        context_rows = SportsDashboard._football_compact_context_rows(event, combine_line_with_tv=True)
        venue_rows = [row for row in context_rows if row[0] == "VENUE"]
        rows.extend(row for row in context_rows if row[0] != "VENUE")
        if sport == "NCAA":
            site = SportsDashboard._football_ncaa_site_label(event)
            venue = str(event.get("venue") or event.get("city") or "").strip()
            if site and venue and venue not in site:
                site = f"{site} / {venue}"
            if site:
                rows.append(("SITE", site))
            elif venue_rows:
                rows.append(venue_rows[0])
        elif venue_rows:
            rows.append(venue_rows[0])
        record = SportsDashboard._football_record_matchup_label(event, sport)
        if record:
            rows.append(("RECORD", record))
        return rows[:5]

    @staticmethod
    def _football_line_label(event):
        event = event or {}
        spread = str(event.get("spread") or "").strip()
        total = str(event.get("over_under") or "").strip()
        return " / ".join(part for part in (spread, total) if part)

    @staticmethod
    def _football_game_info_rows(event, sport, now):
        event = event or {}
        rows = []
        kick = SportsDashboard._football_kick_label(event, now)
        if kick:
            rows.append(("KICK", kick))
        matchup = SportsDashboard._football_matchup_label(event, sport)
        if matchup:
            rows.append(("MATCH", matchup))
        context_rows = SportsDashboard._football_compact_context_rows(event, combine_line_with_tv=True)
        venue_rows = [row for row in context_rows if row[0] == "VENUE"]
        rows.extend(row for row in context_rows if row[0] != "VENUE")
        if sport == "NCAA":
            site = SportsDashboard._football_ncaa_site_label(event)
            venue = str(event.get("venue") or event.get("city") or "").strip()
            if site and venue and venue not in site:
                site = f"{site} / {venue}"
            if site:
                rows.append(("SITE", site))
            elif venue_rows:
                rows.append(venue_rows[0])
        elif venue_rows:
            rows.append(venue_rows[0])
        record = SportsDashboard._football_record_matchup_label(event, sport)
        if record:
            rows.append(("RECORD", record))
        return rows[:5]

    @staticmethod
    def _football_final_snap_rows(event, sport):
        event = event or {}
        sport = str(sport or event.get("sport") or "").upper()
        rows = []
        winner = SportsDashboard._football_final_winner_label(event, sport)
        if winner:
            rows.append(("WIN", winner))
        score = SportsDashboard._football_score_line_label(event, sport)
        if score:
            rows.append(("SCORE", score))
        record = SportsDashboard._football_record_matchup_label(event, sport)
        if record:
            rows.append(("RECORD", record))
        if sport == "NCAA":
            site = SportsDashboard._football_ncaa_site_label(event)
            venue = str(event.get("venue") or event.get("city") or "").strip()
            if site and venue and venue not in site:
                site = f"{site} / {venue}"
            if site:
                rows.append(("SITE", site))
        rows.extend(SportsDashboard._football_compact_context_rows(event, combine_line_with_tv=True))
        return rows[:5]

    @staticmethod
    def _football_pregame_headline(event, sport):
        event = event or {}
        if sport == "NCAA" and event.get("neutral_site"):
            site = SportsDashboard._football_ncaa_site_label(event)
            if site:
                return site
        parts = []
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast:
            parts.append(f"TV {broadcast}")
        spread = str(event.get("spread") or "").strip()
        if spread:
            parts.append(spread)
        total = str(event.get("over_under") or "").strip()
        if total and len(parts) < 2:
            parts.append(total)
        note = str(event.get("note") or "").strip()
        if note and len(parts) < 2:
            parts.append(note)
        return " / ".join(parts[:2])

    @staticmethod
    def _football_pregame_meta_label(event):
        event = event or {}
        parts = []
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue:
            parts.append(venue)
        total = str(event.get("over_under") or "").strip()
        if total:
            parts.append(total)
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast and len(parts) < 2:
            parts.append(f"TV {broadcast}")
        spread = str(event.get("spread") or "").strip()
        if spread and len(parts) < 2:
            parts.append(f"SPREAD {spread}")
        return "  |  ".join(parts[:2]) or "PREGAME INFO"

    @staticmethod
    def _football_final_headline(event, sport):
        event = event or {}
        if sport == "NCAA" and event.get("neutral_site"):
            site = SportsDashboard._football_ncaa_site_label(event)
            if site:
                return site
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue:
            return venue
        note = str(event.get("note") or "").strip()
        if note:
            return note
        return "RESULT FINAL"

    @staticmethod
    def _football_final_meta_label(event, sport):
        event = event or {}
        if sport == "NCAA":
            parts = []
            if event.get("neutral_site"):
                parts.append("NEUTRAL SITE")
            venue = str(event.get("venue") or event.get("city") or "").strip()
            if venue:
                parts.append(venue)
            broadcast = str(event.get("broadcast") or "").strip()
            spread = str(event.get("spread") or "").strip()
            total = str(event.get("over_under") or "").strip()
            line_parts = []
            if broadcast:
                line_parts.append(f"TV {broadcast}")
            if spread:
                line_parts.append(f"SPREAD {spread}")
            if total:
                line_parts.append(total)
            if line_parts:
                parts.append(" / ".join(line_parts))
            return "  |  ".join(parts[:3]) or "FINAL RESULT"

        parts = []
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast:
            parts.append(f"TV {broadcast}")
        spread = str(event.get("spread") or "").strip()
        if spread:
            parts.append(f"SPREAD {spread}")
        total = str(event.get("over_under") or "").strip()
        if total:
            parts.append(total)
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue and len(parts) < 2:
            parts.append(venue)
        return "  |  ".join(parts[:3]) or "FINAL RESULT"

    @staticmethod
    def _nfl_broadcast_line_label(event):
        event = event or {}
        parts = []
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast:
            parts.append(f"TV {broadcast}")
        line = SportsDashboard._football_line_label(event)
        if line:
            parts.append(line)
        fallback = "NFL FINAL" if SportsDashboard._hub_event_state(event) == "final" else "NFL PREGAME"
        return " / ".join(parts[:2]) or fallback

    @staticmethod
    def _nfl_venue_label(event):
        venue = str((event or {}).get("venue") or (event or {}).get("city") or "").strip()
        return venue or "NFL GAME INFO"

    @staticmethod
    def _football_kick_label(event, now):
        start = (event or {}).get("start")
        if start:
            return f"{start.strftime('%m/%d')} {SportsDashboard._format_time(start)}"
        return SportsDashboard._hub_event_time_label(event, now)

    @staticmethod
    def _football_matchup_label(event, sport):
        away = SportsDashboard._football_display_team(event, "a", sport)
        home = SportsDashboard._football_display_team(event, "b", sport)
        if away and home:
            connector = "VS" if sport == "NCAA" and (event or {}).get("neutral_site") else "@"
            return f"{away} {connector} {home}"
        return ""

    @staticmethod
    def _football_record_matchup_label(event, sport):
        away = SportsDashboard._football_display_team(event, "a", sport)
        home = SportsDashboard._football_display_team(event, "b", sport)
        record_a = str((event or {}).get("record_a") or "").strip()
        record_b = str((event or {}).get("record_b") or "").strip()
        if record_a and record_b:
            return f"{away} {record_a} / {home} {record_b}"
        if record_a:
            return f"{away} {record_a}"
        if record_b:
            return f"{home} {record_b}"
        return ""

    @staticmethod
    def _football_score_line_label(event, sport):
        score_a = (event or {}).get("wins_a")
        score_b = (event or {}).get("wins_b")
        if score_a is None or score_b is None:
            return ""
        away = SportsDashboard._football_display_team(event, "a", sport)
        home = SportsDashboard._football_display_team(event, "b", sport)
        return f"{away} {score_a} / {home} {score_b}"

    @staticmethod
    def _football_final_winner_label(event, sport):
        winner_side = SportsDashboard._football_winner_side(event)
        if not winner_side:
            return ""
        winner = SportsDashboard._football_display_team(event, winner_side, sport, full=True)
        score_a = SportsDashboard._coerce_int((event or {}).get("wins_a"))
        score_b = SportsDashboard._coerce_int((event or {}).get("wins_b"))
        if score_a is not None and score_b is not None and score_a != score_b:
            return f"{winner} \u80dc{abs(score_a - score_b)}\u5206"
        return f"{winner} \u80dc"

    @staticmethod
    def _football_winner_side(event):
        if SportsDashboard._hub_event_state(event) != "final":
            return ""
        event = event or {}
        if event.get("winner_a") is True and event.get("winner_b") is not True:
            return "a"
        if event.get("winner_b") is True and event.get("winner_a") is not True:
            return "b"
        score_a = SportsDashboard._coerce_int(event.get("wins_a"))
        score_b = SportsDashboard._coerce_int(event.get("wins_b"))
        if score_a is None or score_b is None or score_a == score_b:
            return ""
        return "a" if score_a > score_b else "b"

    @staticmethod
    def _ncaa_school_label(event, side, include_rank=False, full=False):
        prefix = "team_a" if side == "a" else "team_b"
        raw_event = event or {}
        school = str(raw_event.get(f"{prefix}_zh") or "").strip()
        code = str(raw_event.get(f"{prefix}_code") or raw_event.get(prefix) or "TBD").strip()
        fallback = str(raw_event.get(prefix) or raw_event.get(f"{prefix}_name") or code or "TBD").strip()
        aliases = [
            raw_event.get(f"{prefix}_name"),
            raw_event.get(prefix),
            code,
        ]
        if full:
            full_school = SportsDashboard._ncaa_display_school_name(code, fallback=fallback, aliases=aliases, full=True)
            if full_school and full_school != "TBD":
                school = full_school
        if not school:
            school = SportsDashboard._ncaa_display_school_name(code, fallback=fallback, aliases=aliases)
        if include_rank:
            rank = (event or {}).get(f"{prefix}_rank")
            if rank:
                return f"#{rank} {school}"
        return school

    @staticmethod
    def _ncaa_matchup_label(event):
        away = SportsDashboard._ncaa_school_label(event, "a", include_rank=True)
        home = SportsDashboard._ncaa_school_label(event, "b", include_rank=True)
        if away and home:
            connector = "VS" if (event or {}).get("neutral_site") else "@"
            return f"{away} {connector} {home}"
        return ""

    @staticmethod
    def _ncaa_record_matchup_label(event):
        away = SportsDashboard._ncaa_school_label(event, "a", include_rank=True)
        home = SportsDashboard._ncaa_school_label(event, "b", include_rank=True)
        record_a = str((event or {}).get("record_a") or "").strip()
        record_b = str((event or {}).get("record_b") or "").strip()
        if record_a and record_b:
            return f"{away} {record_a} / {home} {record_b}"
        if record_a:
            return f"{away} {record_a}"
        if record_b:
            return f"{home} {record_b}"
        return ""

    @staticmethod
    def _ncaa_score_matchup_label(event):
        away = SportsDashboard._ncaa_school_label(event, "a", include_rank=True)
        home = SportsDashboard._ncaa_school_label(event, "b", include_rank=True)
        return f"{away} {SportsDashboard._hub_score_label(event)} {home}"

    @staticmethod
    def _ncaa_ranked_watch_sort_key(event):
        ranks = []
        for key in ("team_a_rank", "team_b_rank"):
            rank = SportsDashboard._lpl_int_value((event or {}).get(key))
            if rank and rank < 100:
                ranks.append(rank)
        start = (event or {}).get("start")
        if isinstance(start, datetime):
            sort_start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc)
        else:
            sort_start = datetime.max.replace(tzinfo=timezone.utc)
        return (
            0 if ranks else 1,
            0 if len(ranks) >= 2 else 1,
            min(ranks) if ranks else 999,
            0 if (event or {}).get("neutral_site") else 1,
            sort_start,
        )

    @staticmethod
    def _ncaa_header_badge_label(event):
        event = event or {}
        ranks = [
            rank
            for rank in (
                SportsDashboard._lpl_int_value(event.get("team_a_rank")),
                SportsDashboard._lpl_int_value(event.get("team_b_rank")),
            )
            if rank and rank < 100
        ]
        if len(ranks) >= 2 and max(ranks) <= 25:
            return "TOP 25"
        if ranks:
            return "RANKED"
        if event.get("neutral_site"):
            return "NEUTRAL"
        return ""

    @staticmethod
    def _ncaa_game_info_rows(event, now):
        return SportsDashboard._football_game_info_rows(event, "NCAA", now)

    @staticmethod
    def _ncaa_meta_label(event):
        event = event or {}
        parts = []
        if event.get("neutral_site"):
            parts.append("NEUTRAL SITE")
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue:
            parts.append(venue)
        broadcast = str(event.get("broadcast") or "").strip()
        spread = str(event.get("spread") or "").strip()
        total = str(event.get("over_under") or "").strip()
        line_parts = []
        if broadcast:
            line_parts.append(f"TV {broadcast}")
        if spread:
            line_parts.append(f"SPREAD {spread}")
        if total:
            line_parts.append(total)
        if line_parts:
            parts.append(" / ".join(line_parts))
        return "  |  ".join(parts[:3]) or "COLLEGE FOOTBALL"

    @staticmethod
    def _ncaa_main_meta_label(event):
        event = event or {}
        site = SportsDashboard._football_ncaa_site_label(event)
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if site and venue and venue not in site:
            return f"{site} / {venue}"
        if site:
            return site
        if venue:
            return venue
        return SportsDashboard._football_record_matchup_label(event, "NCAA") or "COLLEGE FOOTBALL"

    @staticmethod
    def _football_ncaa_site_label(event):
        if not (event or {}).get("neutral_site"):
            return ""
        note = str((event or {}).get("note") or "").strip()
        venue = str((event or {}).get("venue") or (event or {}).get("city") or "").strip()
        if note and venue:
            return f"NEUTRAL / {note}"
        if note:
            return f"NEUTRAL / {note}"
        if venue:
            return f"NEUTRAL / {venue}"
        return "NEUTRAL SITE"

    def _draw_football_info_section(self, draw, x1, x2, y, title, rows, accent):
        self._draw_hub_section_header(draw, x1, x2, y, title, accent)
        row_y = y + 24
        compact_rows = [(label, value) for label, value in rows if str(value or "").strip()]
        if not compact_rows:
            draw.text((x1 + 10, row_y + 6), "No live details", font=self._font(10, True), fill=COLORS["muted"])
            return
        for index, (label, value) in enumerate(compact_rows[:5]):
            top = row_y + index * 18
            draw.line((x1, top - 2, x2, top - 2), fill=COLORS["line"], width=1)
            self._draw_sport_info_icon(draw, label, x1 + 3, top + 1, accent)
            label, label_font = self._fit_text(draw, label, 40, 8, bold=True, min_size=6)
            draw.text((x1 + 17, top), label, font=label_font, fill=COLORS["muted"])
            value, value_font = self._fit_text(draw, value, x2 - x1 - 70, 10, bold=True, min_size=7)
            value_fill = accent if str(label).upper() == "WIN" else COLORS["text"]
            self._draw_right_aligned(draw, (x2 - 3, top), value, value_font, value_fill)

    def _draw_football_small_row(self, image, draw, x1, x2, y, event, show_time, sport):
        draw.rounded_rectangle((x1, y, x2, y + 25), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + 24), fill=self._hub_sport_accent(sport))
        left = self._format_time(event["start"]) if show_time and event.get("start") else (event["start"].strftime("%m/%d") if event.get("start") else "TBD")
        left, left_font = self._fit_text(draw, left, 42, 9, bold=True, min_size=7)
        draw.text((x1 + 10, y + 3), left, font=left_font, fill=COLORS["muted"])
        logo_size = 11
        matchup = f"{self._football_display_team(event, 'a', sport)} {self._hub_score_label(event)} {self._football_display_team(event, 'b', sport)}"
        matchup, matchup_font = self._fit_text(draw, matchup, x2 - x1 - 96, 10, bold=True, min_size=7)
        self._draw_small_row_team_logos(image, draw, x1, x2, y + 3, event, matchup, matchup_font, x2 - 25, logo_size)
        self._draw_right_aligned(draw, (x2 - 25, y + 3), matchup, matchup_font, COLORS["text"])
        note = self._football_small_note_label(event)
        if note:
            is_live = SportsDashboard._hub_event_state(event) == "live"
            has_field_marker = (
                is_live
                and SportsDashboard._football_field_marker_fraction(event) is not None
            )
            down_chip = SportsDashboard._football_down_chip_label(event) if is_live else ""
            has_down_chip = bool(down_chip)
            if has_field_marker and has_down_chip:
                note_width = x2 - x1 - 148
            elif has_down_chip:
                note_width = x2 - x1 - 120
            else:
                note_width = x2 - x1 - (112 if has_field_marker else 84)
            if has_field_marker:
                self._draw_football_mini_field_marker(draw, x1 + 58, y + 16, 24, sport, event)
            if has_down_chip:
                self._draw_football_down_chip(draw, x1 + (86 if has_field_marker else 58), y + 16, down_chip, event, sport)
            note, note_font = self._fit_text(draw, note, note_width, 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 25, y + 15), note, note_font, COLORS["muted"])

    def _draw_football_down_chip(self, draw, x, y, label, event, sport):
        label = str(label or "").strip()
        if not label:
            return
        x = int(x)
        y = int(y)
        accent = self._hub_sport_accent(sport)
        fill = self._blend(accent, COLORS["panel"], 0.24)
        draw.rounded_rectangle((x, y, x + 35, y + 9), radius=2, fill=fill, outline=accent, width=1)
        label, label_font = self._fit_text(draw, label, 17, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (x + 2, y + 1, x + 19, y + 8), label, label_font, COLORS["text"])
        current_down = SportsDashboard._football_down_number(event)
        for index in range(4):
            left = x + 22 + index * 3
            fill_color = COLORS["amber"] if current_down and index + 1 <= current_down else COLORS["panel"]
            outline = accent if current_down and index + 1 == current_down else COLORS["border"]
            draw.rectangle((left, y + 3, left + 1, y + 6), fill=fill_color, outline=outline, width=1)

    def _draw_football_mini_field_marker(self, draw, x, y, width, sport, event):
        fraction = SportsDashboard._football_field_marker_fraction(event)
        if fraction is None:
            return
        x = int(x)
        y = int(y)
        width = max(18, int(width))
        accent = self._hub_sport_accent(sport)
        field_fill = self._blend(accent, COLORS["panel"], 0.18)
        draw.rounded_rectangle((x, y, x + width, y + 6), radius=2, fill=field_fill, outline=COLORS["border"], width=1)
        draw.line((x + width // 2, y + 1, x + width // 2, y + 5), fill=COLORS["line"], width=1)
        marker_x = max(x + 2, min(x + width - 2, int(round(x + width * fraction))))
        draw.line((marker_x, y + 1, marker_x, y + 5), fill=accent, width=1)
        draw.ellipse((marker_x - 2, y + 2, marker_x + 2, y + 6), fill=COLORS["amber"], outline=COLORS["text"], width=1)

    def _draw_football_field(self, draw, x1, y1, x2, y2, sport, event=None):
        field_fill = COLORS["panel_blue"] if sport == "NFL" else self._blend(self._hub_sport_accent(sport), COLORS["panel"], 0.18)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=field_fill, outline=COLORS["border"], width=1)
        for index in range(1, 5):
            x = x1 + int((x2 - x1) * index / 5)
            draw.line((x, y1 + 4, x, y2 - 4), fill=COLORS["line"], width=1)
        for x in range(x1 + 14, x2 - 12, 18):
            draw.line((x, y1 + 8, x + 6, y1 + 8), fill=COLORS["line"], width=1)
            draw.line((x, y2 - 8, x + 6, y2 - 8), fill=COLORS["line"], width=1)
        marker_fraction = self._football_field_marker_fraction(event)
        if marker_fraction is None:
            return
        marker_x = int(round(x1 + (x2 - x1) * marker_fraction))
        accent = self._hub_sport_accent(sport)
        draw.line((marker_x, y1 + 5, marker_x, y2 - 5), fill=self._blend(accent, field_fill, 0.48), width=1)
        ball_y = y2 - 15
        draw.ellipse((marker_x - 6, ball_y - 4, marker_x + 6, ball_y + 4), fill=COLORS["amber"], outline=COLORS["text"], width=1)
        draw.line((marker_x - 3, ball_y - 2, marker_x + 3, ball_y + 2), fill=COLORS["text"], width=1)
        draw.line((marker_x - 3, ball_y + 2, marker_x + 3, ball_y - 2), fill=COLORS["text"], width=1)

    def _draw_nfl_field(self, draw, x1, y1, x2, y2, event=None):
        accent = COLORS["nfl_accent"]
        field_fill = self._blend(accent, COLORS["panel"], 0.22)
        red_zone = self._blend(COLORS["red"], field_fill, 0.42)
        line_color = self._blend(accent, COLORS["line"], 0.28)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=field_fill, outline=COLORS["border"], width=1)
        zone_w = max(16, int((x2 - x1) * 0.12))
        draw.rectangle((x1 + 1, y1 + 2, x1 + zone_w, y2 - 2), fill=red_zone)
        draw.rectangle((x2 - zone_w, y1 + 2, x2 - 1, y2 - 2), fill=red_zone)
        for index in range(1, 5):
            x = x1 + int((x2 - x1) * index / 5)
            draw.line((x, y1 + 4, x, y2 - 4), fill=line_color, width=1)
        for x in range(x1 + 14, x2 - 12, 18):
            draw.line((x, y1 + 8, x + 6, y1 + 8), fill=COLORS["line"], width=1)
            draw.line((x, y2 - 8, x + 6, y2 - 8), fill=COLORS["line"], width=1)
        center_x = (x1 + x2) / 2
        badge_y = max(y1 + 56, y2 - 25)
        draw.rounded_rectangle((center_x - 24, badge_y, center_x + 24, badge_y + 13), radius=3, outline=accent, width=1)
        label, label_font = self._fit_text(draw, "NFL", 36, 8, bold=True, min_size=6)
        self._draw_centered(draw, (center_x, badge_y + 2), label, label_font, accent)
        marker_fraction = self._football_field_marker_fraction(event)
        if marker_fraction is None:
            return
        marker_x = int(round(x1 + (x2 - x1) * marker_fraction))
        draw.line((marker_x, y1 + 5, marker_x, y2 - 5), fill=self._blend(accent, field_fill, 0.52), width=1)
        ball_y = y2 - 15
        draw.ellipse((marker_x - 6, ball_y - 4, marker_x + 6, ball_y + 4), fill=COLORS["amber"], outline=COLORS["text"], width=1)
        draw.line((marker_x - 3, ball_y - 2, marker_x + 3, ball_y + 2), fill=COLORS["text"], width=1)
        draw.line((marker_x - 3, ball_y + 2, marker_x + 3, ball_y - 2), fill=COLORS["text"], width=1)

    @staticmethod
    def _football_field_marker_fraction(event):
        event = event or {}
        line = str(event.get("yard_line") or "").strip().upper()
        if not line:
            return None
        away_code = str(event.get("team_a_code") or event.get("team_a") or "").strip().upper()
        home_code = str(event.get("team_b_code") or event.get("team_b") or "").strip().upper()
        possession = str(event.get("possession") or "").strip().upper()
        if line in {"50", "MIDFIELD"}:
            return 0.5
        match = re.search(r"([A-Z0-9]+)?\s*(\d{1,2})", line)
        if not match:
            return None
        code = str(match.group(1) or "").strip().upper()
        yard = SportsDashboard._coerce_int(match.group(2))
        if yard is None:
            return None
        yard = max(0, min(50, int(yard)))
        if code == home_code:
            return max(0.0, min(1.0, (100 - yard) / 100))
        if code == away_code:
            return max(0.0, min(1.0, yard / 100))
        if code == possession and possession == home_code:
            return max(0.0, min(1.0, (100 - yard) / 100))
        if code == possession and possession == away_code:
            return max(0.0, min(1.0, yard / 100))
        return 0.5 if yard == 50 else max(0.0, min(1.0, yard / 100))

    @staticmethod
    def _football_down_number(event):
        text = str((event or {}).get("down_distance") or "").strip().upper()
        match = re.search(r"\b([1-4])(?:ST|ND|RD|TH)?\b", text)
        if match:
            return SportsDashboard._coerce_int(match.group(1))
        return None

    @staticmethod
    def _football_down_chip_label(event):
        if not event or SportsDashboard._hub_event_state(event) != "live":
            return ""
        text = str((event or {}).get("down_distance") or "").strip().upper()
        if not text:
            return ""
        match = re.search(r"\b([1-4])(?:ST|ND|RD|TH)?\s*&\s*(GOAL|[0-9]{1,2})\b", text)
        if match:
            distance = "G" if match.group(2) == "GOAL" else match.group(2)
            return f"{match.group(1)}&{distance}"
        down = SportsDashboard._football_down_number(event)
        return f"{down}D" if down else ""

    @staticmethod
    def _football_possession_side_fill_key(event, side):
        event = event or {}
        if SportsDashboard._hub_event_state(event) != "live":
            return "text"
        prefix = "team_a" if side == "a" else "team_b"
        possession = str(event.get("possession") or "").strip().upper()
        if not possession:
            return "text"
        values = [
            event.get(f"{prefix}_code"),
            event.get(prefix),
            event.get(f"{prefix}_name"),
        ]
        for value in values:
            if possession == str(value or "").strip().upper():
                return "amber"
        return "text"

    @staticmethod
    def _football_team_side_fill_key(event, side):
        event = event or {}
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            return SportsDashboard._football_possession_side_fill_key(event, side)
        if state != "final":
            return "text"
        winner_side = SportsDashboard._football_winner_side(event)
        return "amber" if winner_side == side else "text"

    @staticmethod
    def _football_score_side_fill_key(event, side, sport):
        event = event or {}
        if str(sport or event.get("sport") or "").upper() == "NFL" and SportsDashboard._hub_event_state(event) == "live":
            return "text"
        return SportsDashboard._football_team_side_fill_key(event, side)

    @staticmethod
    def _football_possession_display_label(event, sport):
        event = event or {}
        possession = str(event.get("possession") or "").strip()
        if not possession:
            return ""
        sport = str(sport or event.get("sport") or "").upper()
        normalized = possession.upper()
        for side in ("a", "b"):
            prefix = "team_a" if side == "a" else "team_b"
            values = [
                event.get(f"{prefix}_code"),
                event.get(prefix),
                event.get(f"{prefix}_name"),
            ]
            for value in values:
                if normalized == str(value or "").strip().upper():
                    if sport == "NCAA":
                        return SportsDashboard._ncaa_school_label(event, side)
                    return SportsDashboard._football_display_team(event, side, sport)
        if sport == "NCAA":
            return SportsDashboard._ncaa_display_school_name(possession, fallback=possession, aliases=[possession])
        if sport == "NFL":
            return SportsDashboard._football_display_team_name(possession, fallback=possession, sport=sport, aliases=[possession])
        return possession

    @staticmethod
    def _football_display_team(event, side, sport, full=False):
        prefix = "team_a" if side == "a" else "team_b"
        raw_event = event or {}
        code = str(raw_event.get(f"{prefix}_code") or raw_event.get(prefix) or "TBD").strip()
        fallback = str(raw_event.get(prefix) or code or "TBD").strip() or "TBD"
        aliases = [
            raw_event.get(f"{prefix}_name"),
            raw_event.get(prefix),
            code,
        ]
        rank = (event or {}).get(f"{prefix}_rank")
        if sport == "NCAA":
            if full:
                team = SportsDashboard._ncaa_display_school_name(code, fallback=fallback, aliases=aliases, full=True)
            else:
                team = str(raw_event.get(f"{prefix}_zh") or "").strip()
            if not team or team == "TBD":
                team = SportsDashboard._ncaa_display_school_name(code, fallback=fallback, aliases=aliases)
            if rank:
                return f"#{rank} {team}"
            return team
        if sport == "NFL":
            team = str(raw_event.get(f"{prefix}_zh") or "").strip()
            if full:
                team = SportsDashboard._football_display_team_name(code, fallback=fallback, sport=sport, aliases=aliases, full=True)
            elif not team:
                team = SportsDashboard._football_display_team_name(code, fallback=fallback, sport=sport, aliases=aliases)
            return team
        return fallback

    @staticmethod
    def _mlb_small_note_label(event):
        if SportsDashboard._hub_event_state(event) == "live":
            parts = []
            inning = SportsDashboard._mlb_inning_label(event)
            if inning and inning != "SCHEDULED":
                parts.append(inning)
            bases = SportsDashboard._mlb_bases_label((event or {}).get("bases") or "")
            if bases and bases != "EMPTY":
                parts.append(bases)
            outs = SportsDashboard._mlb_outs_label(event)
            if outs:
                parts.append(outs)
            count = SportsDashboard._mlb_balls_strikes_label(event)
            if count and len(parts) < 3:
                parts.append(count)
            if parts:
                return " / ".join(parts[:3])
        if SportsDashboard._hub_event_state(event) == "final":
            venue = str((event or {}).get("venue") or "").strip()
            return " / ".join(part for part in ("FINAL", venue) if part)
        return SportsDashboard._mlb_pitching_compact_label(event) or str((event or {}).get("venue") or "").strip()

    @staticmethod
    def _mlb_live_state_rows(event):
        rows = [
            ("INNING", SportsDashboard._mlb_inning_label(event)),
            ("COUNT", SportsDashboard._mlb_count_label(event)),
        ]
        bases = SportsDashboard._mlb_bases_label((event or {}).get("bases") or "")
        if bases and bases != "EMPTY":
            rows.append(("BASES", bases))
        matchup = SportsDashboard._mlb_current_matchup_label(event)
        if matchup:
            rows.append(("B/P", matchup))
        rhe = SportsDashboard._mlb_compact_rhe_label(event)
        if rhe:
            rows.append(("RHE", rhe))
        venue = str((event or {}).get("venue") or "").strip()
        if venue:
            rows.append(("VENUE", venue))
        return rows

    @staticmethod
    def _mlb_game_info_rows(event, now):
        event = event or {}
        rows = []
        first = SportsDashboard._mlb_first_pitch_label(event, now)
        if first:
            rows.append(("FIRST", first))
        matchup = SportsDashboard._mlb_matchup_label(event)
        if matchup:
            rows.append(("MATCH", matchup))
        probable = SportsDashboard._mlb_probable_pitching_label(event)
        if probable:
            rows.append(("SP", probable))
        venue = str(event.get("venue") or "").strip()
        if venue:
            rows.append(("VENUE", venue))
        record = SportsDashboard._mlb_record_matchup_label(event)
        if record:
            rows.append(("RECORD", record))
        return rows[:5]

    @staticmethod
    def _mlb_pregame_detail_rows(event):
        event = event or {}
        rows = []
        probable = SportsDashboard._mlb_probable_pitching_label(event)
        if probable:
            rows.append(("SP", probable))
        venue = str(event.get("venue") or "").strip()
        if venue:
            rows.append(("VENUE", venue))
        record = SportsDashboard._mlb_record_matchup_label(event)
        if record:
            rows.append(("RECORD", record))
        return rows[:3]

    @staticmethod
    def _mlb_final_snap_rows(event):
        event = event or {}
        rows = []
        winner = SportsDashboard._mlb_final_winner_label(event)
        if winner:
            rows.append(("WIN", winner))
        score = SportsDashboard._mlb_score_line_label(event)
        if score:
            rows.append(("SCORE", score))
        rhe = SportsDashboard._mlb_compact_rhe_label(event)
        if rhe:
            rows.append(("RHE", rhe))
        record = SportsDashboard._mlb_record_matchup_label(event)
        if record:
            rows.append(("RECORD", record))
        venue = str(event.get("venue") or "").strip()
        if venue:
            rows.append(("VENUE", venue))
        return rows[:5]

    @staticmethod
    def _mlb_final_date_label(event):
        start = (event or {}).get("start")
        if start:
            return start.strftime("%m/%d")
        return "FINAL"

    @staticmethod
    def _mlb_final_meta_label(event):
        event = event or {}
        parts = []
        winner = SportsDashboard._mlb_final_winner_label(event)
        if winner:
            parts.append(winner)
        venue = str(event.get("venue") or "").strip()
        if venue:
            parts.append(venue)
        return " / ".join(parts[:2]) or "FINAL RESULT"

    @staticmethod
    def _coerce_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _mlb_first_pitch_label(event, now):
        start = (event or {}).get("start")
        if start:
            return f"{start.strftime('%m/%d')} {SportsDashboard._format_time(start)}"
        return SportsDashboard._hub_event_time_label(event, now)

    @staticmethod
    def _mlb_matchup_label(event):
        away = SportsDashboard._mlb_display_team_from_event(event, "a")
        home = SportsDashboard._mlb_display_team_from_event(event, "b")
        return f"{away} @ {home}"

    @staticmethod
    def _mlb_record_matchup_label(event):
        away = SportsDashboard._mlb_display_team_from_event(event, "a")
        home = SportsDashboard._mlb_display_team_from_event(event, "b")
        record_a = str((event or {}).get("record_a") or "").strip()
        record_b = str((event or {}).get("record_b") or "").strip()
        if record_a and record_b:
            return f"{away} {record_a} / {home} {record_b}"
        if record_a:
            return f"{away} {record_a}"
        if record_b:
            return f"{home} {record_b}"
        return ""

    @staticmethod
    def _mlb_score_line_label(event):
        score_a = (event or {}).get("wins_a")
        score_b = (event or {}).get("wins_b")
        if score_a is None or score_b is None:
            return ""
        away = SportsDashboard._mlb_display_team_from_event(event, "a")
        home = SportsDashboard._mlb_display_team_from_event(event, "b")
        return f"{away} {score_a} / {home} {score_b}"

    @staticmethod
    def _mlb_final_winner_label(event):
        winner_side = SportsDashboard._mlb_winner_side(event)
        if not winner_side:
            return "TIE" if SportsDashboard._hub_event_state(event) == "final" else ""
        winner = SportsDashboard._mlb_display_team_from_event(event, winner_side)
        score_a = SportsDashboard._coerce_int((event or {}).get("wins_a"))
        score_b = SportsDashboard._coerce_int((event or {}).get("wins_b"))
        if score_a is not None and score_b is not None and score_a != score_b:
            return f"{winner} \u80dc{abs(score_a - score_b)}\u5206"
        return f"{winner} \u80dc"

    @staticmethod
    def _mlb_winner_side(event):
        if SportsDashboard._hub_event_state(event) != "final":
            return ""
        event = event or {}
        score_a = SportsDashboard._coerce_int(event.get("wins_a"))
        score_b = SportsDashboard._coerce_int(event.get("wins_b"))
        if score_a is None or score_b is None or score_a == score_b:
            return ""
        return "a" if score_a > score_b else "b"

    @staticmethod
    def _mlb_probable_pitching_label(event):
        away = SportsDashboard._mlb_short_pitcher_name((event or {}).get("probable_a"))
        home = SportsDashboard._mlb_short_pitcher_name((event or {}).get("probable_b"))
        if away and home:
            return f"{away} / {home}"
        if away:
            return f"{SportsDashboard._mlb_display_team_from_event(event, 'a')} {away}"
        if home:
            return f"{SportsDashboard._mlb_display_team_from_event(event, 'b')} {home}"
        return ""

    @staticmethod
    def _mlb_current_matchup_label(event):
        batter = str((event or {}).get("current_batter") or "").strip()
        pitcher = str((event or {}).get("current_pitcher") or "").strip()
        if batter and pitcher:
            return f"{batter} / {pitcher}"
        if batter:
            return f"BAT {batter}"
        if pitcher:
            return f"P {pitcher}"
        return ""

    @staticmethod
    def _mlb_live_main_meta_label(event):
        batter = str((event or {}).get("current_batter") or "").strip()
        pitcher = str((event or {}).get("current_pitcher") or "").strip()
        if batter and pitcher:
            return f"B/P {batter} / {pitcher}"
        if batter:
            return f"BAT {batter}"
        if pitcher:
            return f"P {pitcher}"
        return SportsDashboard._mlb_pitching_label(event)

    @staticmethod
    def _mlb_batting_side_fill_key(event, side):
        if SportsDashboard._hub_event_state(event) != "live":
            return "text"
        inning_state = str((event or {}).get("inning_state") or "").strip().lower()
        if side == "a" and inning_state.startswith("top"):
            return "amber"
        if side == "b" and inning_state.startswith("bottom"):
            return "amber"
        return "text"

    @staticmethod
    def _mlb_team_side_fill_key(event, side):
        event = event or {}
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            return SportsDashboard._mlb_batting_side_fill_key(event, side)
        if state != "final":
            return "text"
        winner_side = SportsDashboard._mlb_winner_side(event)
        return "amber" if winner_side == side else "text"

    @staticmethod
    def _mlb_bases_label(bases):
        labels = []
        text = str(bases or "").upper()
        if "1B" in text or "1" in text:
            labels.append("1B")
        if "2B" in text or "2" in text:
            labels.append("2B")
        if "3B" in text or "3" in text:
            labels.append("3B")
        return " ".join(labels) if labels else "EMPTY"

    @staticmethod
    def _mlb_compact_rhe_label(event):
        away = ((event or {}).get("away_line") or {})
        home = ((event or {}).get("home_line") or {})
        if not (
            SportsDashboard._mlb_line_has_rhe(away)
            or SportsDashboard._mlb_line_has_rhe(home)
        ):
            return ""
        team_a = SportsDashboard._mlb_display_team_from_event(event, "a")
        team_b = SportsDashboard._mlb_display_team_from_event(event, "b")

        def compact(line):
            values = []
            for key in ("runs", "hits", "errors"):
                value = line.get(key)
                values.append("-" if value is None else str(value))
            return "/".join(values)

        return f"{team_a} {compact(away)}  {team_b} {compact(home)}"

    @staticmethod
    def _mlb_line_has_rhe(line):
        if not isinstance(line, Mapping):
            return False
        return any(line.get(key) is not None for key in ("runs", "hits", "errors"))

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

    @staticmethod
    def _event_team_logo_fallback(event, side, sport=None):
        event = event or {}
        sport = str(sport or event.get("sport") or "").upper()
        prefix = "team_a" if side == "a" else "team_b"
        raw_code = str(event.get(f"{prefix}_code") or "").strip()
        raw_team = str(event.get(prefix) or "").strip()
        raw_name = str(event.get(f"{prefix}_name") or "").strip()
        aliases = [raw_name, raw_team, raw_code]
        if sport == "WNBA":
            return SportsDashboard._wnba_logo_fallback(event, side)
        if sport in {"NFL", "NCAA"}:
            code = SportsDashboard._football_normalized_team_code(raw_code or raw_team, aliases, sport)
            if code and code != "TBD":
                return code
        if sport == "MLB":
            for value in (raw_code, raw_team, raw_name):
                code = SportsDashboard._mlb_team_code(value)
                if code and code != "TBD":
                    return code
        return raw_code or raw_team or raw_name or "TBD"

    @staticmethod
    def _small_row_logo_fallback(event, side):
        return SportsDashboard._event_team_logo_fallback(event, side)

    @staticmethod
    def _football_small_note_label(event):
        event = event or {}
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            parts = []
            status = str(event.get("status_text") or "").strip().upper()
            if status:
                parts.append(status)
            down = str(event.get("down_distance") or "").strip().upper()
            if down:
                parts.append(down)
            yard = str(event.get("yard_line") or "").strip()
            possession = SportsDashboard._football_possession_display_label(event, event.get("sport") or "")
            if yard and possession:
                parts.append(f"{yard} / POS {possession}")
            elif yard:
                parts.append(yard)
            elif possession:
                parts.append(f"POS {possession}")
            if parts:
                return " / ".join(parts[:3])

        if state == "final":
            status = str(event.get("status_text") or "FINAL").strip().upper() or "FINAL"
            note = str(event.get("note") or "").strip()
            venue = str(event.get("venue") or event.get("city") or "").strip()
            return " / ".join(part for part in (status, note, venue) if part)

        parts = []
        for key in ("broadcast", "spread", "over_under"):
            value = str(event.get(key) or "").strip()
            if value:
                parts.append(value)
        note = str(event.get("note") or "").strip()
        if note and len(parts) < 2:
            parts.append(note)
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue and len(parts) < 3:
            parts.append(venue)
        return " / ".join(parts[:3])

    @staticmethod
    def _football_meta_label(event, sport):
        event = event or {}
        parts = []
        if sport == "NCAA" and (event or {}).get("neutral_site"):
            parts.append("NEUTRAL SITE")
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast:
            parts.append(f"TV {broadcast}")
        spread = str(event.get("spread") or "").strip()
        if spread:
            parts.append(f"SPREAD {spread}")
        total = str(event.get("over_under") or "").strip()
        if total:
            parts.append(total)
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue and len(parts) < 2:
            parts.append(venue)
        return "  |  ".join(parts[:3]) or "FOOTBALL DATA"

    def _draw_pga_event_card(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        accent = COLORS["pga_live"] if card.get("status") == "LIVE" else COLORS["pga_accent"]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        self._draw_sport_logo(image, draw, "PGA", x1 + 18, y1 + 11, 36, 48)
        tag, tag_font = self._fit_text(draw, f"PGA {card.get('status') or 'NEXT'}", 90, 12, bold=True, min_size=8)
        draw.rectangle((x1 + 64, y1 + 13, x1 + 156, y1 + 31), fill=COLORS["pga_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 69, y1 + 14), tag, font=tag_font, fill=COLORS["text"])
        name = str(event.get("name") or "PGA TOUR").strip() or "PGA TOUR"
        name, name_font = self._fit_text(draw, name, x2 - x1 - 42, 18, bold=True, min_size=10)
        draw.text((x1 + 20, y1 + 60), name, font=name_font, fill=COLORS["text"])
        status = str(event.get("status_text") or "").strip() or self._hub_event_time_label(event, now)
        status, status_font = self._fit_text(draw, status, x2 - x1 - 42, 12, bold=True, min_size=8)
        draw.text((x1 + 20, y1 + 84), status, font=status_font, fill=COLORS["pga_accent"])
        if not (event.get("leader") or {}):
            self._draw_pga_schedule_summary(draw, x1 + 20, y1 + 106, x2 - 20, y1 + 148, event, now)
        else:
            self._draw_pga_leader_summary(draw, x1 + 20, y1 + 106, x2 - 20, y1 + 148, event.get("leader") or {})
        venue = str(event.get("venue") or "").strip()
        if venue:
            venue, venue_font = self._fit_text(draw, venue, x2 - x1 - 42, 10, bold=True, min_size=7)
            draw.text((x1 + 20, y1 + 154), venue, font=venue_font, fill=COLORS["muted"])
        self._draw_pga_fairway(image, draw, x1 + 20, y2 - 49, x2 - 20, y2 - 13)

    def _draw_pga_schedule_summary(self, draw, x1, y1, x2, y2, event, now):
        event_state = SportsDashboard._hub_event_state(event)
        is_scheduled = event_state == "scheduled"
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["pga_course_tint"], outline=COLORS["border"], width=1)
        icon = "TEE" if is_scheduled else "CLOCK"
        label_text = "TEE WINDOW" if is_scheduled else ("FINAL STATUS" if event_state == "final" else "EVENT STATUS")
        self._draw_sport_info_icon(draw, icon, x1 + 8, y1 + 5, COLORS["pga_accent"])
        label, label_font = self._fit_text(draw, label_text, 78, 8, bold=True, min_size=6)
        draw.text((x1 + 23, y1 + 5), label, font=label_font, fill=COLORS["muted"])
        if is_scheduled:
            tee = SportsDashboard._pga_tee_label(event, now)
        else:
            tee = str((event or {}).get("status_text") or "").strip() or SportsDashboard._pga_event_window_label(event) or SportsDashboard._pga_tee_label(event, now)
        tee, tee_font = self._fit_text(draw, tee, 84, 16, bold=True, min_size=10)
        self._draw_right_aligned(draw, (x2 - 8, y1 + 13), tee, tee_font, COLORS["pga_accent"])
        window = SportsDashboard._pga_event_window_label(event)
        venue = str((event or {}).get("venue") or "").strip()
        detail = " / ".join(part for part in (window, venue) if part)
        if not detail:
            return
        self._draw_sport_info_icon(draw, "VENUE", x1 + 8, y2 - 13, COLORS["pga_accent"])
        detail, detail_font = self._fit_text(draw, detail, x2 - x1 - 33, 8, bold=True, min_size=6)
        draw.text((x1 + 23, y2 - 12), detail, font=detail_font, fill=COLORS["muted"])

    def _draw_pga_leader_summary(self, draw, x1, y1, x2, y2, leader):
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["pga_course_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "GOLF", x1 + 8, y1 + 5, COLORS["pga_accent"])
        label, label_font = self._fit_text(draw, "LEADER", 54, 8, bold=True, min_size=6)
        draw.text((x1 + 23, y1 + 5), label, font=label_font, fill=COLORS["muted"])
        if not leader:
            fallback, fallback_font = self._fit_text(draw, "EVENT INFO", x2 - x1 - 18, 11, bold=True, min_size=8)
            self._draw_centered_in_box(draw, (x1 + 8, y1 + 17, x2 - 8, y2 - 4), fallback, fallback_font, COLORS["text"])
            return
        name_x = x1 + 8
        name_budget = x2 - x1 - 74
        country_code = SportsDashboard._pga_country_badge_code(leader)
        if country_code:
            self._draw_pga_country_badge(draw, name_x, y1 + 18, country_code, COLORS["pga_accent"])
            name_x += 26
            name_budget = max(42, x2 - name_x - 52)
        name, name_font = self._fit_text(draw, leader.get("name") or "Leader", name_budget, 13, bold=True, min_size=8)
        draw.text((name_x, y1 + 17), name, font=name_font, fill=COLORS["text"])
        score, score_font = self._fit_text(draw, str(leader.get("score") or "E"), 44, 17, bold=True, min_size=11)
        self._draw_right_aligned(draw, (x2 - 8, y1 + 15), score, score_font, COLORS["pga_accent"])

    def _draw_pga_leader_scorecard_strip(self, draw, x1, y, x2, leader):
        items = SportsDashboard._pga_leader_scorecard_items(leader)
        if not items:
            self._draw_sport_info_icon(draw, "SCORE", x1, y + 2, COLORS["pga_accent"])
            label, label_font = self._fit_text(draw, "SCORECARD", x2 - x1 - 18, 8, bold=True, min_size=6)
            draw.text((x1 + 15, y + 2), label, font=label_font, fill=COLORS["muted"])
            return
        gap = 3
        count = len(items)
        cell_w = max(34, int((x2 - x1 - gap * (count - 1)) / count))
        for index, (label, value, icon, accent) in enumerate(items):
            left = int(x1 + index * (cell_w + gap))
            right = int(x2 if index == count - 1 else left + cell_w)
            self._draw_pga_leader_scorecard_cell(draw, (left, y, right, y + 12), label, value, icon, accent)

    def _draw_pga_leader_scorecard_cell(self, draw, box, label, value, icon, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, icon, x1 + 3, y1 + 2, accent)
        label_text, label_font = self._fit_text(draw, label, 20, 6, bold=True, min_size=5)
        draw.text((x1 + 15, y1 + 2), label_text, font=label_font, fill=accent)
        value_text, value_font = self._fit_text(draw, str(value or "TBD"), max(14, x2 - x1 - 39), 7, bold=True, min_size=5)
        self._draw_right_aligned(draw, (x2 - 3, y1 + 2), value_text, value_font, COLORS["text"])

    @staticmethod
    def _pga_leader_scorecard_items(leader):
        leader = leader or {}
        items = []
        country = str(leader.get("country") or "").strip()
        country_label = SportsDashboard._pga_country_display_label(country)
        if country_label:
            items.append(("NAT", country_label, "GOLF", COLORS["pga_accent"]))
        round_no = SportsDashboard._lpl_int_value(leader.get("round"))
        if round_no:
            items.append(("RND", f"R{round_no}", "PERIOD", COLORS["pga_accent"]))
        today = str(leader.get("today") or "").strip()
        if today:
            items.append(("DAY", today, "SCORE", COLORS["amber"] if today.startswith("-") else COLORS["pga_accent"]))
        strokes = str(leader.get("strokes") or "").strip()
        if strokes:
            items.append(("CARD", strokes, "SCORE", COLORS["pga_accent"]))
        return items[:4]

    def _draw_pga_fairway(self, image, draw, x1, y1, x2, y2):
        base_width = max(1, int(x2 - x1 + 1))
        base_height = max(1, int(y2 - y1))
        strip_size = (max(1, int(round(base_width * 1.34))), max(1, int(round(base_height * 1.34))))
        strip = self._load_pga_fairway_strip(strip_size)
        if strip is not None:
            paste_x = int(round(x1 - (strip_size[0] - base_width) / 2))
            paste_y = int(round(y1 + 10))
            paste_x = min(max(0, paste_x), max(0, image.width - strip_size[0]))
            paste_y = min(max(0, paste_y), max(0, image.height - strip_size[1]))
            image.paste(strip, (paste_x, paste_y), strip)
            return
        fallback_y1 = y1 + 10
        fallback_y2 = y2 + 10
        draw.arc((x1 + 4, fallback_y1 - 6, x2 - 10, fallback_y2 + 18), 200, 342, fill=COLORS["pga_accent"], width=2)
        draw.ellipse((x2 - 36, fallback_y1 + 7, x2 - 10, fallback_y1 + 28), outline=COLORS["pga_accent"], width=2)
        draw.line((x2 - 22, fallback_y1 + 7, x2 - 22, fallback_y1 - 8), fill=COLORS["red"], width=2)

    def _draw_pga_leaderboard_column(self, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        rows = list(event.get("leaderboard") or [])
        if not rows:
            self._draw_pga_event_info_section(draw, x1, x2, y1, y2 - 28, event, now)
        else:
            leaderboard_y = y1
            self._draw_hub_section_header(draw, x1, x2, leaderboard_y, "LEADERBOARD", COLORS["pga_accent"])
            row_y = leaderboard_y + 27
            snap_y = y2 - 38
            drawn_rows = 0
            leader_score = rows[0].get("score") if rows else ""
            for index, row in enumerate(rows[:7]):
                top = row_y + index * 21
                if top + 18 > snap_y - 4:
                    break
                self._draw_pga_leaderboard_row(draw, x1, x2, top, row, index, leader_score=leader_score)
                drawn_rows += 1
            self._draw_pga_event_snap_row(draw, x1, x2, snap_y, event, now)
        next_label = self._pga_next_label(card, now)
        next_label, next_font = self._fit_text(draw, next_label, x2 - x1 - 8, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1, y2 - 19, x2, y2 - 4), next_label, next_font, COLORS["muted"])

    def _draw_pga_compact_event_info_section(self, draw, x1, x2, y1, y2, event, now):
        self._draw_hub_section_header(draw, x1, x2, y1, "EVENT INFO", COLORS["pga_accent"])
        row_y = y1 + 22
        for index, (icon, label, value) in enumerate(self._pga_compact_event_info_rows(event, now)):
            top = row_y + index * 17
            if top + 14 > y2:
                break
            self._draw_pga_info_row(draw, x1, x2, top, icon, label, value)

    def _draw_pga_event_info_section(self, draw, x1, x2, y1, y2, event, now):
        self._draw_hub_section_header(draw, x1, x2, y1, "EVENT INFO", COLORS["pga_accent"])
        rows = self._pga_event_info_rows(event, now)
        row_y = y1 + 27
        for index, (icon, label, value) in enumerate(rows):
            top = row_y + index * 21
            if top + 16 > y2:
                break
            self._draw_pga_info_row(draw, x1, x2, top, icon, label, value)

    @staticmethod
    def _pga_compact_event_info_rows(event, now):
        event = event or {}
        rows = []
        name = str(event.get("name") or "").strip()
        if name:
            rows.append(("GOLF", "EVENT", name))
        status = str(event.get("status_text") or "").strip()
        if not status:
            status = SportsDashboard._hub_event_time_label(event, now)
        if status == "TBD":
            status = ""
        venue = str(event.get("venue") or "").strip()
        detail = " / ".join(part for part in (status, venue) if part)
        if not detail:
            detail = SportsDashboard._pga_event_window_label(event)
        if detail:
            rows.append(("CLOCK", "STATUS", detail))
        return rows[:2]

    def _draw_pga_info_row(self, draw, x1, x2, y, icon, label, value):
        draw.line((x1, y - 2, x2, y - 2), fill=COLORS["line"], width=1)
        self._draw_sport_info_icon(draw, icon, x1 + 2, y + 1, COLORS["pga_accent"])
        label, label_font = self._fit_text(draw, label, 48, 8, bold=True, min_size=6)
        draw.text((x1 + 15, y), label, font=label_font, fill=COLORS["muted"])
        value, value_font = self._fit_text(draw, value or "TBD", x2 - x1 - 72, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 2, y), value, value_font, COLORS["text"])

    def _draw_pga_event_snap_row(self, draw, x1, x2, y, event, now):
        draw.line((x1, y - 2, x2, y - 2), fill=COLORS["line"], width=1)
        self._draw_sport_info_icon(draw, "PGA", x1 + 2, y + 1, COLORS["pga_accent"])
        label, label_font = self._fit_text(draw, "SNAP", 42, 8, bold=True, min_size=6)
        draw.text((x1 + 15, y), label, font=label_font, fill=COLORS["muted"])
        value = SportsDashboard._pga_event_snap_label(event, now)
        value, value_font = self._fit_text(draw, value, x2 - x1 - 64, 9, bold=True, min_size=6)
        self._draw_right_aligned(draw, (x2 - 2, y), value, value_font, COLORS["text"])

    @staticmethod
    def _pga_event_info_rows(event, now):
        event = event or {}
        rows = []
        name = str(event.get("name") or "").strip()
        if name:
            rows.append(("GOLF", "EVENT", name))
        status = str(event.get("status_text") or "").strip()
        if not status:
            status = SportsDashboard._hub_event_time_label(event, now)
        if status == "TBD":
            status = ""
        if status:
            rows.append(("CLOCK", "STATUS", status))
        venue = str(event.get("venue") or "").strip()
        if venue:
            rows.append(("VENUE", "COURSE", venue))
        window = SportsDashboard._pga_event_window_label(event)
        if window:
            rows.append(("PERIOD", "WINDOW", window))
        if not rows:
            rows.append(("PGA", "BOARD", "PGA TOUR"))
        return rows[:5]

    @staticmethod
    def _pga_event_snap_label(event, now):
        event = event or {}
        parts = []
        leader = event.get("leader") or {}
        if leader:
            leader_name = str(leader.get("name") or "").strip()
            leader_score = str(leader.get("score") or "").strip()
            leader_label = " ".join(part for part in ("LEADER", leader_name, leader_score) if part)
            if leader_label != "LEADER":
                parts.append(leader_label)
        status = str(event.get("status_text") or "").strip()
        if not status:
            status = SportsDashboard._hub_event_time_label(event, now)
        if status == "TBD":
            status = ""
        if status:
            parts.append(status.upper())
        venue = str(event.get("venue") or "").strip()
        if venue:
            parts.append(venue)
        window = SportsDashboard._pga_event_window_label(event)
        if window:
            parts.append(window)
        return " / ".join(parts[:3]) or "PGA TOUR"

    def _draw_pga_leaderboard_row(self, draw, x1, x2, y, row, index, leader_score=None):
        rank_color_key = self._pga_leaderboard_rank_color_key(row, index)
        rank_accent = COLORS[rank_color_key]
        position = SportsDashboard._lpl_int_value((row or {}).get("position")) or index + 1
        podium = position in {1, 2, 3}
        draw.line((x1, y - 2, x2, y - 2), fill=COLORS["line"], width=1)
        self._draw_sport_info_icon(draw, "GOLF", x1 + 2, y + 1, rank_accent)
        pos = SportsDashboard._pga_position_label(row, index)
        pos, pos_font = self._fit_text(draw, pos, 24, 10, bold=True, min_size=7)
        draw.text((x1 + 15, y), pos, font=pos_font, fill=rank_accent if podium else COLORS["muted"])
        name_x = x1 + 43
        detail_x = name_x
        name_budget = x2 - x1 - 94
        country_code = SportsDashboard._pga_country_badge_code(row)
        if country_code:
            self._draw_pga_country_badge(draw, name_x, y + 1, country_code, rank_accent if podium else COLORS["pga_accent"])
            name_x += 26
            detail_x = name_x
            name_budget = max(42, x2 - name_x - 50)
        name, name_font = self._fit_text(draw, row.get("name") or "Player", name_budget, 10, bold=True, min_size=7)
        draw.text((name_x, y), name, font=name_font, fill=COLORS["text"])
        score = str(row.get("score") or "E")
        score, score_font = self._fit_text(draw, score, 48, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 2, y), score, score_font, rank_accent if podium else COLORS["text"])
        gap_chip = SportsDashboard._pga_gap_chip_label(row, leader_score)
        round_chip = SportsDashboard._pga_round_chip_label(row)
        detail = self._pga_row_detail_label(row, leader_score=leader_score)
        if detail:
            if gap_chip and round_chip:
                detail_reserve = 112
            elif gap_chip or round_chip:
                detail_reserve = 78
            else:
                detail_reserve = 48
            detail, detail_font = self._fit_text(
                draw,
                detail,
                max(42, x2 - detail_x - detail_reserve),
                8,
                bold=True,
                min_size=6,
            )
            draw.text((detail_x, y + 11), detail, font=detail_font, fill=COLORS["muted"])
        if round_chip:
            round_x = x2 - (66 if gap_chip else 31)
            self._draw_pga_round_chip(draw, round_x, y + 11, round_chip, row, rank_accent)
        if gap_chip:
            self._draw_pga_gap_chip(draw, x2 - 31, y + 11, gap_chip, rank_accent)

    def _draw_pga_country_badge(self, draw, x, y, code, accent):
        code = str(code or "").strip().upper()[:3]
        if not code:
            return
        box = (int(x), int(y), int(x) + 21, int(y) + 9)
        fill = self._blend(accent, COLORS["panel"], 0.22)
        draw.rounded_rectangle(box, radius=2, fill=fill, outline=accent, width=1)
        text, text_font = self._fit_text(draw, code, 17, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1), text, text_font, COLORS["text"])

    def _draw_pga_gap_chip(self, draw, x, y, label, accent):
        label = str(label or "").strip()
        if not label:
            return
        x = int(x)
        y = int(y)
        box = (x, y, x + 29, y + 9)
        fill = self._blend(accent, COLORS["panel"], 0.26)
        draw.rounded_rectangle(box, radius=2, fill=fill, outline=accent, width=1)
        draw.line((x + 5, y + 2, x + 5, y + 7), fill=COLORS["border"], width=1)
        draw.polygon((x + 6, y + 2, x + 11, y + 4, x + 6, y + 5), fill=accent)
        label, label_font = self._fit_text(draw, label, 15, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (x + 12, y + 1, x + 28, y + 8), label, label_font, COLORS["text"])

    def _draw_pga_round_chip(self, draw, x, y, label, row, accent):
        label = str(label or "").strip()
        if not label:
            return
        x = int(x)
        y = int(y)
        current = max(1, min(4, SportsDashboard._lpl_int_value((row or {}).get("round")) or 1))
        fill = self._blend(accent, COLORS["panel"], 0.22)
        draw.rounded_rectangle((x, y, x + 31, y + 9), radius=2, fill=fill, outline=accent, width=1)
        label, label_font = self._fit_text(draw, label, 12, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (x + 2, y + 1, x + 14, y + 8), label, label_font, COLORS["text"])
        for index in range(4):
            left = x + 18 + index * 3
            fill_color = accent if index + 1 <= current else COLORS["panel"]
            outline = accent if index + 1 == current else COLORS["border"]
            draw.rectangle((left, y + 3, left + 1, y + 6), fill=fill_color, outline=outline, width=1)

    @staticmethod
    def _pga_leaderboard_rank_color_key(row, index):
        position = SportsDashboard._lpl_int_value((row or {}).get("position")) or index + 1
        if position == 1:
            return "pga_leader"
        if position == 2:
            return "pga_accent"
        if position == 3:
            return "orange"
        return "pga_accent"

    @staticmethod
    def _pga_position_label(row, index):
        label = str((row or {}).get("position_label") or "").strip().upper()
        if label:
            return label
        position = SportsDashboard._lpl_int_value((row or {}).get("position")) or index + 1
        return f"P{position}"

    @staticmethod
    def _pga_country_badge_code(row):
        return SportsDashboard._pga_country_code((row or {}).get("country"))[:3]

    @staticmethod
    def _pga_row_detail_label(row, leader_score=None):
        parts = []
        country = str((row or {}).get("country") or "").strip()
        country_label = SportsDashboard._pga_country_display_label(country)
        round_no = (row or {}).get("round")
        strokes = str((row or {}).get("strokes") or "").strip()
        today = str((row or {}).get("today") or "").strip()
        if country_label:
            parts.append(country_label)
        if round_no:
            round_label = f"R{round_no}"
            if strokes:
                round_label = f"{round_label} {strokes}"
            parts.append(round_label)
        elif strokes:
            parts.append(strokes)
        if today:
            parts.append(today)
        gap = SportsDashboard._pga_gap_to_leader_label((row or {}).get("score"), leader_score)
        if gap:
            parts.append(gap)
        return " / ".join(parts)

    @staticmethod
    def _pga_gap_chip_label(row, leader_score):
        gap = SportsDashboard._pga_gap_to_leader_label((row or {}).get("score"), leader_score)
        match = re.search(r"\+(\d+)", gap)
        if not match:
            return ""
        return f"+{match.group(1)}"

    @staticmethod
    def _pga_round_chip_label(row):
        round_no = SportsDashboard._lpl_int_value((row or {}).get("round"))
        if not round_no or round_no <= 0:
            return ""
        return f"R{round_no}"

    @staticmethod
    def _pga_gap_to_leader_label(score, leader_score):
        score_value = SportsDashboard._pga_score_to_par(score)
        leader_value = SportsDashboard._pga_score_to_par(leader_score)
        if score_value is None or leader_value is None:
            return ""
        gap = score_value - leader_value
        if gap <= 0:
            return ""
        return f"GAP +{gap}"

    @staticmethod
    def _pga_score_to_par(value):
        text = str(value or "").strip().upper()
        if not text:
            return None
        if text in {"E", "EVEN"}:
            return 0
        match = re.search(r"^[+-]?\d+$", text)
        if not match:
            return None
        return int(text)

    @staticmethod
    def _pga_tee_label(event, now):
        start = (event or {}).get("start")
        if start:
            if now and start.date() == now.date():
                return SportsDashboard._format_time(start)
            return f"{start.strftime('%m/%d')} {SportsDashboard._format_time(start)}"
        return str((event or {}).get("status_text") or "").strip()

    @staticmethod
    def _pga_event_window_label(event):
        start = (event or {}).get("start")
        end = (event or {}).get("end")
        if start and end:
            if start.date() == end.date():
                return f"{start.strftime('%m/%d')} {SportsDashboard._format_time(start)}"
            if start.month == end.month:
                return f"{start.strftime('%m/%d')}-{end.strftime('%d')}"
            return f"{start.strftime('%m/%d')}-{end.strftime('%m/%d')}"
        if start:
            return f"{start.strftime('%m/%d')} {SportsDashboard._format_time(start)}"
        if end:
            return f"THRU {end.strftime('%m/%d')}"
        return ""

    def _draw_hub_section_header(self, draw, x1, x2, y, title, accent):
        draw.rectangle((x1, y + 2, x1 + 8, y + 17), fill=accent, outline=COLORS["border"], width=1)
        title, title_font = self._fit_text(draw, title, x2 - x1 - 18, 13, bold=True, min_size=8)
        draw.text((x1 + 13, y - 2), title, font=title_font, fill=COLORS["text"])
        draw.line((x1, y + 19, x2, y + 19), fill=COLORS["border"], width=1)

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

    def _draw_hub_team_score(self, draw, x1, y, x2, team, score, record="", align="left", image=None, logo_url="", logo_size=0, logo_fallback=None, team_fill=None, score_fill=None):
        logo_size = int(logo_size or 0)
        team_fill = team_fill or COLORS["text"]
        score_fill = score_fill or COLORS["text"]
        text_x1 = x1
        text_x2 = x2
        has_logo = image is not None and logo_size > 0
        if has_logo and align != "right":
            fallback = logo_fallback if logo_fallback is not None else team
            logo_x = int(x1)
            self._draw_team_logo(image, draw, logo_url, logo_x, y, logo_size, fallback)
            text_x1 = logo_x + logo_size + 5
        team_width_budget = max(32, text_x2 - text_x1)
        if has_logo and align == "right":
            team_width_budget = max(32, team_width_budget - logo_size - 5)
        team_text, team_font = self._fit_text(draw, str(team or "TBD"), team_width_budget, 19, bold=True, min_size=11)
        if has_logo and align == "right":
            fallback = logo_fallback if logo_fallback is not None else team
            team_width = self._text_width(draw, team_text, team_font)
            logo_x = max(int(x1), int(text_x2 - team_width - logo_size - 5))
            self._draw_team_logo(image, draw, logo_url, logo_x, y, logo_size, fallback)
        score_text = "-" if score is None else str(score)
        score_text, score_font = self._fit_text(draw, score_text, 38, 19, bold=True, min_size=11)
        record_text = str(record or "").strip()
        record_text, record_font = self._fit_text(draw, record_text, max(32, text_x2 - text_x1), 9, bold=True, min_size=7)
        if align == "right":
            self._draw_right_aligned(draw, (text_x2, y), team_text, team_font, team_fill)
            self._draw_right_aligned(draw, (text_x2, y + 22), score_text, score_font, score_fill)
            if record_text:
                self._draw_right_aligned(draw, (text_x2, y + 43), record_text, record_font, COLORS["muted"])
        else:
            draw.text((text_x1, y), team_text, font=team_font, fill=team_fill)
            draw.text((text_x1, y + 22), score_text, font=score_font, fill=score_fill)
            if record_text:
                draw.text((text_x1, y + 43), record_text, font=record_font, fill=COLORS["muted"])

    def _draw_mlb_base_diamond(self, draw, x, y, bases):
        occupied = set(str(bases or ""))
        points = {"2": (x + 16, y + 3), "1": (x + 29, y + 16), "3": (x + 3, y + 16)}
        for base, (cx, cy) in points.items():
            fill = COLORS["amber"] if base in occupied else COLORS["panel"]
            draw.polygon([(cx, cy - 5), (cx + 5, cy), (cx, cy + 5), (cx - 5, cy)], fill=fill, outline=COLORS["border"])
        label, label_font = self._fit_text(draw, "1B 2B 3B", 42, 7, bold=True, min_size=6)
        self._draw_centered(draw, (x + 16, y + 30), label, label_font, COLORS["muted"])

    def _draw_mlb_mini_base_diamond(self, draw, x, y, bases):
        occupied = set(str(bases or ""))
        points = {"2": (x + 7, y), "1": (x + 14, y + 7), "3": (x, y + 7)}
        for base, (cx, cy) in points.items():
            fill = COLORS["amber"] if base in occupied else COLORS["panel"]
            draw.polygon([(cx, cy - 3), (cx + 3, cy), (cx, cy + 3), (cx - 3, cy)], fill=fill, outline=COLORS["border"])

    def _draw_mlb_count_chip(self, draw, x, y, label, outs=None):
        label = str(label or "").strip()
        if not label:
            return
        x = int(x)
        y = int(y)
        fill = self._blend(COLORS["mlb_accent"], COLORS["panel"], 0.26)
        draw.rounded_rectangle((x, y, x + 35, y + 9), radius=2, fill=fill, outline=COLORS["mlb_accent"], width=1)
        label, label_font = self._fit_text(draw, label, 18, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (x + 2, y + 1, x + 21, y + 8), label, label_font, COLORS["text"])
        out_count = SportsDashboard._lpl_int_value(outs)
        out_count = 0 if out_count is None else max(0, min(3, out_count))
        for index in range(3):
            cx = x + 24 + index * 4
            dot_fill = COLORS["red"] if index < out_count else COLORS["panel"]
            dot_outline = COLORS["amber"] if index < out_count else COLORS["border"]
            draw.ellipse((cx, y + 3, cx + 2, y + 5), fill=dot_fill, outline=dot_outline, width=1)

    def _draw_mlb_rhe_line(self, draw, x1, y, x2, event):
        away = event.get("away_line") or {}
        home = event.get("home_line") or {}
        if not (SportsDashboard._mlb_line_has_rhe(away) or SportsDashboard._mlb_line_has_rhe(home)):
            return

        draw.rounded_rectangle((x1, y, x2, y + 31), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.line((x1 + 6, y + 16, x2 - 6, y + 16), fill=COLORS["line"], width=1)
        value_x1 = x2 - 82
        rows = (
            (self._mlb_display_team_from_event(event, "a"), away, y + 2, y + 15),
            (self._mlb_display_team_from_event(event, "b"), home, y + 17, y + 30),
        )
        for team, line, row_y1, row_y2 in rows:
            team_text, team_font = self._fit_text(draw, team, max(42, value_x1 - x1 - 18), 10, bold=True, min_size=7)
            self._draw_text_in_box(draw, (x1 + 9, row_y1, value_x1 - 6, row_y2), team_text, team_font, COLORS["text"])
            values = []
            for key in ("runs", "hits", "errors"):
                value = line.get(key)
                values.append("-" if value is None else str(value))
            value_text, value_font = self._fit_text(draw, f"R/H/E {'/'.join(values)}", 76, 10, bold=True, min_size=7)
            self._draw_text_in_box(draw, (value_x1, row_y1, x2 - 8, row_y2), value_text, value_font, COLORS["text"], align="right")

    @staticmethod
    def _hub_score_label(event):
        if (event or {}).get("wins_a") is None or (event or {}).get("wins_b") is None:
            return "VS"
        return f"{event.get('wins_a')}-{event.get('wins_b')}"

    @staticmethod
    def _hub_event_time_label(event, now):
        if not event or not event.get("start"):
            return "TBD"
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            return str(event.get("status_text") or "LIVE").upper()[:14]
        if state == "final":
            return "FINAL"
        start = event["start"]
        if start.date() == now.date():
            return SportsDashboard._format_time(start)
        return start.strftime("%m/%d")

    @staticmethod
    def _mlb_inning_label(event):
        if not event:
            return "SCHEDULED"
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            inning = str(event.get("inning_label") or event.get("inning") or "").strip()
            half = str(event.get("inning_state") or "").strip().upper()
            return f"{half} {inning}".strip() or "LIVE"
        if state == "final":
            return "FINAL"
        return str(event.get("status_text") or "SCHEDULED").upper()[:18]

    @staticmethod
    def _mlb_count_label(event):
        if not event:
            return "MLB"
        if SportsDashboard._hub_event_state(event) != "live":
            return str(event.get("venue") or "MLB").strip()[:18]
        count = SportsDashboard._mlb_balls_strikes_label(event) or "B-S ---"
        outs = SportsDashboard._lpl_int_value(event.get("outs"))
        outs = "-" if outs is None else str(outs)
        return f"{count} OUT {outs}"

    @staticmethod
    def _mlb_balls_strikes_label(event):
        if not event or SportsDashboard._hub_event_state(event) != "live":
            return ""
        balls = event.get("balls")
        strikes = event.get("strikes")
        if balls is None and strikes is None:
            return ""
        balls = "-" if balls is None else str(balls)
        strikes = "-" if strikes is None else str(strikes)
        return f"B-S {balls}-{strikes}"

    @staticmethod
    def _mlb_count_chip_label(event):
        if not event or SportsDashboard._hub_event_state(event) != "live":
            return ""
        balls = event.get("balls")
        strikes = event.get("strikes")
        if balls is None and strikes is None:
            return ""
        balls = "-" if balls is None else str(balls)
        strikes = "-" if strikes is None else str(strikes)
        return f"{balls}-{strikes}"

    @staticmethod
    def _mlb_outs_label(event):
        if not event or SportsDashboard._hub_event_state(event) != "live":
            return ""
        outs = SportsDashboard._lpl_int_value(event.get("outs"))
        if outs is None:
            return ""
        return f"{outs} OUT"

    @staticmethod
    def _mlb_pitching_label(event):
        if not event:
            return "MLB GAME INFO"
        away = str(event.get("probable_a") or "").strip()
        home = str(event.get("probable_b") or "").strip()
        if away and home:
            return f"SP {away} / {home}"
        if away:
            return f"SP {SportsDashboard._mlb_display_team_from_event(event, 'a')} {away}"
        if home:
            return f"SP {SportsDashboard._mlb_display_team_from_event(event, 'b')} {home}"
        return str(event.get("venue") or "MLB GAME INFO").strip()

    @staticmethod
    def _mlb_pitching_compact_label(event):
        if not event:
            return ""
        away = SportsDashboard._mlb_short_pitcher_name((event or {}).get("probable_a"))
        home = SportsDashboard._mlb_short_pitcher_name((event or {}).get("probable_b"))
        if away or home:
            return f"SP {away or 'TBD'} / {home or 'TBD'}"
        return ""

    @staticmethod
    def _mlb_short_pitcher_name(name):
        value = str(name or "").strip()
        if not value:
            return ""
        parts = [part for part in value.replace(".", " ").split() if part]
        if len(parts) < 2:
            return value[:14]
        return f"{parts[0][0]}. {' '.join(parts[1:])}"[:18]

    @staticmethod
    def _pga_next_label(card, now):
        upcoming = list((card or {}).get("upcoming") or [])
        if upcoming:
            event = upcoming[0]
            start = event.get("start")
            date_text = start.strftime("%m/%d") if start else "TBD"
            name = str(event.get("name") or event.get("name_en") or "").strip()
            return f"NEXT TEE {date_text} / {name}" if name else f"NEXT TEE {date_text}"
        recent = list((card or {}).get("recent") or [])
        if recent:
            name = str(recent[0].get("name") or recent[0].get("name_en") or "").strip()
            return f"RECENT / {name}" if name else "RECENT TOURNAMENT"
        return "TOUR CALENDAR"

    def _draw_nba_compact_panel(self, image, draw, bounds, selected, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        panel_w = x2 - x1 + 1
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["nba_accent"], COLORS["panel"], 22, 1)
        header_y = y1 + 8
        self._draw_nba_logo(image, draw, x1 + 14, header_y - 2, 30, 34)
        title, title_font = self._fit_text(draw, "NBA", 86, 22, bold=True, min_size=17)
        draw.text((x1 + 52, header_y), title, font=title_font, fill=COLORS["text"])
        source_label = self._source_label(source_state)
        source_label, source_font = self._fit_text(draw, source_label, 96, 10, bold=True, min_size=7)
        draw.text((x1 + 52, header_y + 22), source_label, font=source_font, fill=COLORS["muted"])
        self._draw_nba_header_court_strip(image, x1 + 150, header_y + 2, x2 - 92, y1 + 47)

        live = selected.get("live") or []
        upcoming = selected.get("upcoming") or []
        recent = selected.get("recent") or []
        main_event = live[0] if live else (upcoming[0] if upcoming else selected.get("main"))
        remaining_upcoming = [event for event in upcoming if event is not main_event][:4]
        pill_text = "LIVE" if live else ("OFF" if selected.get("offseason") else "NEXT")
        self._draw_status_pill(draw, x2 - 84, header_y + 4, pill_text, bool(live))
        draw.line((x1 + 12, y1 + 48, x2 - 12, y1 + 48), fill=COLORS["border"], width=1)

        content_y = y1 + 58
        content_bottom = y2 - 8
        if selected.get("offseason"):
            self._draw_nba_compact_offseason_panel(
                image,
                draw,
                x1 + 12,
                content_y,
                x2 - 12,
                content_bottom,
                selected,
                now,
            )
            return

        split_x = x1 + max(254, min(292, int(panel_w * 0.51)))
        left_x1 = x1 + 12
        left_x2 = split_x - 10
        right_x1 = split_x + 4
        right_x2 = x2 - 12
        draw.line((split_x - 3, content_y - 5, split_x - 3, content_bottom), fill=COLORS["border"], width=1)
        draw.line((split_x - 1, content_y - 5, split_x - 1, content_bottom), fill=COLORS["line"], width=1)

        self._draw_nba_compact_main_card(image, draw, left_x1, content_y, left_x2, content_bottom, main_event, now, bool(live))
        upcoming_rows = remaining_upcoming[:4]
        recent_rows = recent[:1] if len(upcoming_rows) >= 4 else recent[:2]
        upcoming_y = content_y
        recent_y = upcoming_y + 21 + max(1, len(upcoming_rows)) * 31 + 8
        if recent_rows and recent_y + 48 > content_bottom:
            recent_y = max(upcoming_y + 54, content_bottom - 48)
        self._draw_nba_compact_upcoming_rows(image, draw, right_x1, right_x2, upcoming_y, upcoming_rows)
        self._draw_nba_compact_recent_rows(image, draw, right_x1, right_x2, recent_y, content_bottom, recent_rows)

    def _draw_nba_compact_offseason_panel(self, image, draw, x1, y1, x2, y2, selected, now):
        width = x2 - x1 + 1
        split_x = x1 + max(282, min(318, int(width * 0.58)))
        left_x2 = split_x - 10
        right_x1 = split_x + 8
        next_event = selected.get("next_season_event")
        recent = selected.get("recent") or []
        last_event = recent[0] if recent else None

        draw.rounded_rectangle((x1 + 3, y1 + 3, left_x2 + 3, y2 + 3), radius=5, fill=COLORS["nba_shadow"])
        draw.rounded_rectangle((x1, y1, left_x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, y2 - 1), fill=COLORS["nba_accent"])
        self._draw_nba_offseason_court_backdrop(image, x1 + 10, y1 + 9, left_x2 - 8, y2 - 9)
        accent_w, accent_h = NBA_OFFSEASON_ACCENT_SIZE
        accent_x = max(x1 + 118, left_x2 - accent_w - 10)
        accent_y = y1 + 24
        self._draw_nba_offseason_accent(image, accent_x, accent_y, accent_w, accent_h)

        tag, tag_font = self._fit_text(draw, "OFFSEASON", 88, 11, bold=True, min_size=7)
        draw.rectangle((x1 + 16, y1 + 10, x1 + 106, y1 + 28), fill=COLORS["nba_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 20, y1 + 11), tag, font=tag_font, fill=COLORS["text"])
        season = self._nba_next_season_label(now, next_event)
        season_text, season_font = self._fit_text(draw, season, 74, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (left_x2 - 12, y1 + 12), season_text, season_font, COLORS["muted"])

        title, title_font = self._fit_text(draw, "\u4f11\u8d5b\u671f", 132, 30, bold=True, min_size=22)
        draw.text((x1 + 18, y1 + 41), title, font=title_font, fill=COLORS["text"])
        subtitle, subtitle_font = self._fit_text(draw, "NBA SEASON BREAK", left_x2 - x1 - 42, 11, bold=True, min_size=8)
        draw.text((x1 + 20, y1 + 76), subtitle, font=subtitle_font, fill=COLORS["nba_accent"])

        next_y1 = y1 + 98
        next_y2 = min(y2 - 41, next_y1 + 48)
        draw.rounded_rectangle((x1 + 16, next_y1, left_x2 - 14, next_y2), radius=5, fill=COLORS["panel_blue"], outline=COLORS["border"], width=1)
        label, label_font = self._fit_text(draw, "\u4e0b\u5b63\u9996\u6218", 78, 10, bold=True, min_size=7)
        draw.text((x1 + 25, next_y1 + 5), label, font=label_font, fill=COLORS["muted"])
        if next_event:
            primary = f"{next_event['start'].strftime('%m/%d')} {self._format_time(next_event['start'])}"
            secondary = f"{next_event.get('team_a', 'TBD')} vs {next_event.get('team_b', 'TBD')}"
        else:
            primary = "\u8d5b\u7a0b\u5f85\u516c\u5e03"
            secondary = self._nba_expected_opening_month_text(now)
        primary, primary_font = self._fit_text(draw, primary, left_x2 - x1 - 142, 17, bold=True, min_size=10)
        self._draw_right_aligned(draw, (left_x2 - 22, next_y1 + 3), primary, primary_font, COLORS["text"])
        secondary, secondary_font = self._fit_text(draw, secondary, left_x2 - x1 - 52, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 24, next_y1 + 28, left_x2 - 22, next_y2 - 4), secondary, secondary_font, COLORS["muted"])

        if last_event:
            self._draw_nba_offseason_last_result(draw, x1 + 16, y2 - 34, left_x2 - 14, y2 - 9, last_event)

        draw.line((split_x - 2, y1 - 5, split_x - 2, y2), fill=COLORS["border"], width=1)
        draw.line((split_x, y1 - 5, split_x, y2), fill=COLORS["line"], width=1)
        filler_bleed_bounds = (
            right_x1 - NBA_OFFSEASON_FILLER_LEFT_BLEED,
            x2 + NBA_OFFSEASON_FILLER_RIGHT_BLEED,
            y2 + NBA_OFFSEASON_FILLER_BOTTOM_BLEED,
        )
        self._draw_nba_offseason_watch(image, draw, right_x1, y1, x2, y2, next_event, now, filler_bleed_bounds)

    def _draw_nba_offseason_accent(self, image, x, y, width, height):
        accent = self._load_nba_offseason_accent((int(width), int(height)))
        if accent:
            image.paste(accent, (int(x), int(y)), accent)

    def _draw_nba_offseason_court_backdrop(self, image, x1, y1, x2, y2):
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        if width < 60 or height < 24:
            return
        strip = self._load_nba_court_strip((width, min(44, height)))
        if not strip:
            return
        strip = self._tint_alpha_art(strip, COLORS["nba_accent"])
        strip.putalpha(strip.getchannel("A").point(lambda value: min(54, value)))
        image.paste(strip, (x1, y1 + max(0, height - strip.height) // 2), strip)

    def _draw_nba_offseason_last_result(self, draw, x1, y1, x2, y2, event):
        draw.rounded_rectangle((x1, y1, x2, y2), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 6, y2 - 1), fill=COLORS["red"])
        label, label_font = self._fit_text(draw, "LAST RESULT", 76, 9, bold=True, min_size=7)
        draw.text((x1 + 12, y1 + 5), label, font=label_font, fill=COLORS["muted"])
        score = self._nba_score_label(event)
        result = f"{event.get('team_a', 'TBD')} {score} {event.get('team_b', 'TBD')}"
        result, result_font = self._fit_text(draw, result, x2 - x1 - 104, 12, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 9, y1 + 4), result, result_font, COLORS["text"])

    def _draw_nba_offseason_watch(self, image, draw, x1, y1, x2, y2, next_event, now, filler_bleed_bounds=None):
        self._draw_nba_mini_section_header(draw, x1, x2, y1, "OFFSEASON WATCH")
        items = self._nba_offseason_watch_items(next_event, now)
        row_y = y1 + 27
        row_h = 32
        visible_count = 0
        for index, item in enumerate(items[:4]):
            top = row_y + index * row_h
            if top + 25 > y2:
                break
            visible_count += 1
            date_text, title_text = item
            draw.rounded_rectangle((x1, top, x2, top + 25), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
            draw.rectangle((x1 + 1, top + 1, x1 + 5, top + 24), fill=COLORS["nba_accent"])
            date_text, date_font = self._fit_text(draw, date_text, 52, 10, bold=True, min_size=7)
            draw.text((x1 + 10, top + 3), date_text, font=date_font, fill=COLORS["muted"])
            title_text, title_font = self._fit_text(draw, title_text, x2 - x1 - 72, 11, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 9, top + 3), title_text, title_font, COLORS["text"])
        filler_top = row_y + visible_count * row_h
        if filler_bleed_bounds:
            bleed_x1, bleed_x2, bleed_y2 = [int(value) for value in filler_bleed_bounds]
            bleed_x1 = max(0, bleed_x1)
            bleed_x2 = min(image.size[0] - 1, bleed_x2)
            bleed_y2 = min(image.size[1] - 1, bleed_y2)
            self._draw_nba_offseason_filler(
                image,
                bleed_x1,
                bleed_x2,
                max(row_y, filler_top - NBA_OFFSEASON_FILLER_TOP_BLEED),
                bleed_y2,
            )
        else:
            self._draw_nba_offseason_filler(image, x1, x2, filler_top, y2)

    def _draw_nba_offseason_filler(self, image, x1, x2, y1, y2):
        x1 = int(x1)
        x2 = int(x2)
        y1 = int(y1)
        y2 = int(y2)
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        if width < 80 or height < 24:
            return
        source_width = max(width, int(width * NBA_OFFSEASON_FILLER_ZOOM + 0.999))
        source_height = max(height, int(height * NBA_OFFSEASON_FILLER_ZOOM + 0.999))
        filler = self._load_nba_offseason_filler((source_width, source_height))
        if filler:
            if filler.size[0] >= width and filler.size[1] >= height:
                crop_x = (filler.size[0] - width) // 2
                crop_y = filler.size[1] - height
                filler = filler.crop((crop_x, crop_y, crop_x + width, crop_y + height))
            elif filler.size != (width, height):
                filler = ImageOps.fit(filler, (width, height), method=Image.LANCZOS, centering=(0.5, 1.0))
            image.paste(filler, (x1, y1))

    def _nba_offseason_watch_items(self, next_event, now):
        if next_event:
            return [
                (next_event["start"].strftime("%m/%d"), "\u5e38\u89c4\u8d5b\u9996\u6218"),
                ("AUG", "\u5b8c\u6574\u8d5b\u7a0b\u516c\u5e03"),
                ("06/30", "\u81ea\u7531\u5e02\u573a\u5f00\u542f"),
                ("06/23", "NBA DRAFT"),
            ]
        return [
            ("TBD", "\u5e38\u89c4\u8d5b\u9996\u6218"),
            ("AUG", "\u7b49\u5f85\u8d5b\u7a0b\u53d1\u5e03"),
            ("06/30", "\u81ea\u7531\u5e02\u573a\u5f00\u542f"),
            ("06/23", "NBA DRAFT"),
        ]

    @staticmethod
    def _nba_next_season_label(now, next_event=None):
        start = (next_event or {}).get("start") if isinstance(next_event, Mapping) else None
        year = start.year if isinstance(start, datetime) else getattr(now, "year", datetime.now().year)
        if getattr(now, "month", 1) < 6 and not isinstance(start, datetime):
            year -= 1
        return f"{year}-{str(year + 1)[-2:]}"

    @staticmethod
    def _nba_expected_opening_month_text(now):
        year = getattr(now, "year", datetime.now().year)
        if getattr(now, "month", 1) < 6:
            year -= 1
        return f"\u9884\u8ba1 {year}\u5e7410\u6708"

    def _draw_nba_header_court_strip(self, image, x1, y1, x2, y2):
        x1 = int(x1)
        y1 = int(y1)
        x2 = int(x2)
        y2 = int(y2)
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        if width < 40 or height < 10:
            return
        strip = self._load_nba_court_strip((width, height))
        if strip:
            if strip.mode != "RGBA":
                strip = strip.convert("RGBA")
            shadow = self._tint_alpha_art(strip, COLORS["nba_accent"])
            shadow.putalpha(shadow.getchannel("A").point(lambda value: min(120, value)))
            image.paste(shadow, (x1, y1 + 1), shadow)
            strip = self._tint_alpha_art(strip, COLORS["text"])
            image.paste(strip, (x1, y1), strip)

    @staticmethod
    def _tint_alpha_art(source, color):
        alpha = source.getchannel("A")
        tinted = Image.new("RGBA", source.size, tuple(color) + (255,))
        tinted.putalpha(alpha)
        return tinted

    def _draw_nba_compact_main_card(self, image, draw, x1, y1, x2, y2, event, now, is_live):
        accent = COLORS["nba_live"] if is_live else COLORS["nba_accent"]
        draw.rounded_rectangle((x1 + 3, y1 + 3, x2 + 3, y2 + 3), radius=5, fill=COLORS["nba_shadow"])
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, y2 - 1), fill=accent)
        if not event:
            draw.text((x1 + 18, y1 + 62), "No NBA schedule", font=self._font(16, True), fill=COLORS["text"])
            return

        tag = "NOW PLAYING" if is_live else "NEXT MATCH"
        tag_w = 104 if is_live else 88
        tag_text, tag_font = self._fit_text(draw, tag, tag_w - 8, 11, bold=True, min_size=7)
        tag_fill = COLORS["nba_live"] if is_live else COLORS["nba_tag"]
        draw.rectangle((x1 + 14, y1 + 10, x1 + 14 + tag_w, y1 + 28), fill=tag_fill, outline=COLORS["border"], width=1)
        draw.text((x1 + 18, y1 + 11), tag_text, font=tag_font, fill=COLORS["text"])
        date_text = event["start"].strftime("%m/%d")
        date_text, date_font = self._fit_text(draw, date_text, 52, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 11), date_text, date_font, COLORS["muted"])

        time_text = str(event.get("status_text") or ("LIVE" if is_live else self._format_time(event["start"])))
        time_text = "IN PROGRESS" if is_live and not time_text else time_text
        time_text, time_font = self._fit_text(draw, time_text, x2 - x1 - 42, 17, bold=True, min_size=10)
        self._draw_centered(draw, ((x1 + x2) / 2, y1 + 45), time_text, time_font, COLORS["text"])

        center_x = (x1 + x2) / 2
        logo_size = 34
        left_area = (x1 + 20, center_x - 17)
        right_area = (center_x + 17, x2 - 20)
        logo_y = y1 + 66
        left_logo_x = int((left_area[0] + left_area[1] - logo_size) / 2)
        right_logo_x = int((right_area[0] + right_area[1] - logo_size) / 2)
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, logo_y, logo_size, event["team_a"])
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, logo_y, logo_size, event["team_b"])
        has_series_score = event.get("series_wins_a") is not None and event.get("series_wins_b") is not None
        score_text = self._nba_score_label(event)
        score_text, score_font = self._fit_text(draw, score_text, 50, 13, bold=True, min_size=9)
        self._draw_centered(draw, (center_x, logo_y + 19), score_text, score_font, COLORS["text"])

        team_y = logo_y + 47
        team_a_label = self._nba_display_team_from_event(event, "a", full=True)
        team_b_label = self._nba_display_team_from_event(event, "b", full=True)
        team_a, font_a = self._fit_text(draw, team_a_label, left_area[1] - left_area[0], 18, bold=True, min_size=10)
        team_b, font_b = self._fit_text(draw, team_b_label, right_area[1] - right_area[0], 18, bold=True, min_size=10)
        team_a_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "a")]
        team_b_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "b")]
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, font_a, team_a_fill)
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, font_b, team_b_fill)
        has_odds = self._nba_event_has_moneyline_odds(event)
        if has_odds:
            self._draw_nba_odds_pair(draw, left_area, right_area, team_y + 14, event, max_size=10)
        if has_series_score:
            self._draw_nba_main_series_score(draw, left_area, right_area, center_x, team_y + (32 if has_odds else 21), event)

        bottom_label = SportsDashboard._nba_main_footer_label(event, max_period_parts=2)
        block = str(event.get("block") or "NBA").upper()
        block_width = x2 - x1 - (152 if bottom_label else 88)
        block_text, block_font = self._fit_text(draw, block, max(64, block_width), 9, bold=True, min_size=7)
        draw.text((x1 + 15, y2 - 17), block_text, font=block_font, fill=COLORS["nba_accent"])
        if bottom_label:
            bottom_label, bottom_font = self._fit_text(draw, bottom_label, 128, 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 14, y2 - 17), bottom_label, bottom_font, COLORS["muted"])

    def _draw_nba_main_series_score(self, draw, left_area, right_area, center_x, y, event):
        left_score = str(event.get("series_wins_a"))
        right_score = str(event.get("series_wins_b"))
        score_font = self._font(18, True)
        score_y1 = y - 3
        score_y2 = y + 22
        left_score, left_font = self._fit_text(draw, left_score, left_area[1] - left_area[0], 19, bold=True, min_size=12)
        right_score, right_font = self._fit_text(draw, right_score, right_area[1] - right_area[0], 19, bold=True, min_size=12)
        self._draw_centered_in_box(draw, (left_area[0], score_y1, left_area[1], score_y2), left_score, left_font, COLORS["red"])
        self._draw_centered_in_box(draw, (center_x - 16, score_y1, center_x + 16, score_y2), "-", score_font, COLORS["muted"])
        self._draw_centered_in_box(draw, (right_area[0], score_y1, right_area[1], score_y2), right_score, right_font, COLORS["red"])

    def _draw_nba_compact_detail_card(self, image, draw, x1, y1, x2, y2, main_event, detail_event):
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 6, y2 - 1), fill=COLORS["nba_accent"])
        if not main_event:
            draw.text((x1 + 14, y1 + 24), "No NBA detail", font=self._font(12, True), fill=COLORS["muted"])
            return
        date_text = main_event["start"].strftime("%m/%d")
        status_text = str(main_event.get("status_text") or self._format_time(main_event["start"]))
        date_text, date_font = self._fit_text(draw, date_text, 42, 10, bold=True, min_size=7)
        status_text, status_font = self._fit_text(draw, status_text, 78, 10, bold=True, min_size=7)
        draw.text((x1 + 12, y1 + 3), date_text, font=date_font, fill=COLORS["muted"])
        self._draw_right_aligned(draw, (x2 - 10, y1 + 3), status_text, status_font, COLORS["muted"])
        self._draw_nba_lineup_inline(image, draw, x1 + 10, x2 - 10, y1 + 17, main_event, self._nba_score_label(main_event), logo_size=13, team_size=10, score_w=42)

        if detail_event:
            self._draw_nba_detail_scoreline(draw, x1 + 12, x2 - 10, y1 + 33, detail_event, detail_event is not main_event)
            period_label = self._nba_period_label(detail_event, max_parts=4)
            if period_label:
                period_label = f"\u5c0f\u8282 {period_label}"
                period_label, period_font = self._fit_text(draw, period_label, x2 - x1 - 28, 7, bold=True, min_size=6)
                self._draw_centered_in_box(draw, (x1 + 12, y2 - 15, x2 - 10, y2 - 3), period_label, period_font, COLORS["muted"])

    def _draw_nba_detail_scoreline(self, draw, x1, x2, y, event, show_date=False):
        has_score = event.get("wins_a") is not None and event.get("wins_b") is not None
        left_team = str(event.get("team_a") or "TBD")
        right_team = str(event.get("team_b") or "TBD")
        if has_score:
            left_text = f"{left_team} {event['wins_a']}"
            right_text = f"{right_team} {event['wins_b']}"
        else:
            left_text = f"{left_team} -"
            right_text = f"{right_team} -"
        if show_date:
            left_text = f"{event['start'].strftime('%m/%d')} {left_text}"
        center_x = (x1 + x2) / 2
        color = COLORS["red"] if has_score else COLORS["muted"]
        left_text, left_font = self._fit_text(draw, left_text, max(44, center_x - x1 - 4), 11, bold=True, min_size=7)
        right_text, right_font = self._fit_text(draw, right_text, max(44, x2 - center_x - 4), 11, bold=True, min_size=7)
        self._draw_text_in_box(draw, (x1, y - 1, center_x - 4, y + 14), left_text, left_font, color)
        self._draw_text_in_box(draw, (center_x + 4, y - 1, x2, y + 14), right_text, right_font, color, align="right")

    def _draw_nba_compact_upcoming_rows(self, image, draw, x1, x2, y, events):
        self._draw_nba_mini_section_header(draw, x1, x2, y, "UPCOMING")
        if not events:
            draw.text((x1 + 10, y + 23), "No more NBA schedule", font=self._font(10, True), fill=COLORS["muted"])
            return
        row_y = y + 21
        for index, event in enumerate(events[:4]):
            self._draw_nba_mini_match_row(image, draw, x1, x2, row_y + index * 31, event, "VS", show_time=True)

    def _draw_nba_compact_recent_rows(self, image, draw, x1, x2, y, bottom, events):
        self._draw_nba_mini_section_header(draw, x1, x2, y, "RECENT")
        row_y = y + 21
        row_gap = 27
        if not events:
            self._draw_nba_empty_recent_filler(image, x1, x2, y + 42, bottom)
            draw.text((x1 + 10, y + 23), "No recent NBA results", font=self._font(10, True), fill=COLORS["muted"])
            return
        visible_events = events[:2]
        self._draw_nba_empty_recent_filler(image, x1, x2, row_y + len(visible_events) * row_gap, bottom)
        for index, event in enumerate(visible_events):
            self._draw_nba_mini_match_row(image, draw, x1, x2, row_y + index * row_gap, event, self._nba_score_label(event), show_date=True)

    def _draw_nba_empty_recent_filler(self, image, x1, x2, y1, y2):
        x1 = int(x1)
        x2 = int(x2)
        y1 = int(y1)
        y2 = int(y2)
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        if width < 80 or height < 24:
            return
        filler = self._load_nba_empty_slot_filler((width, height))
        if filler:
            image.paste(filler, (x1, y1), filler)

    def _draw_nba_mini_section_header(self, draw, x1, x2, y, title):
        draw.rectangle((x1, y + 2, x1 + 8, y + 17), fill=COLORS["nba_accent"], outline=COLORS["border"], width=1)
        draw.text((x1 + 13, y - 2), title, font=self._font(13, True), fill=COLORS["text"])
        draw.line((x1, y + 19, x2, y + 19), fill=COLORS["border"], width=1)

    def _draw_nba_mini_match_row(self, image, draw, x1, x2, y, event, center_text, show_time=False, show_date=False):
        has_odds = center_text == "VS" and self._nba_event_has_moneyline_odds(event)
        row_h = 31 if has_odds else 27
        draw.rounded_rectangle((x1, y, x2, y + row_h), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + row_h - 1), fill=COLORS["nba_accent"])
        left_label = event["start"].strftime("%m/%d") if show_date or show_time else ""
        matchup_x1 = x1 + 8
        if left_label:
            left_label, label_font = self._fit_text(draw, left_label, 36, 9, bold=True, min_size=7)
            draw.text((x1 + 9, y + 1), left_label, font=label_font, fill=COLORS["muted"])
            matchup_x1 = x1 + 45
        matchup_x2 = x2 - 7
        if show_time:
            time_text, time_font = self._fit_text(draw, self._format_time(event["start"]), 58, 9, bold=True, min_size=7)
            self._draw_centered(draw, ((matchup_x1 + matchup_x2) / 2, y + 6), time_text, time_font, COLORS["text"])
        row_y = y + 11 if show_time else y + 8
        self._draw_nba_lineup_inline(
            image,
            draw,
            matchup_x1,
            matchup_x2,
            row_y,
            event,
            center_text,
            logo_size=NBA_MINI_LINEUP_LOGO_SIZE,
            team_size=NBA_MINI_LINEUP_TEAM_FONT_SIZE,
            score_w=46,
            odds_team_size=NBA_MINI_LINEUP_ODDS_TEAM_FONT_SIZE,
        )

    def _draw_nba_lineup_inline(self, image, draw, x1, x2, y, event, center_text, logo_size=14, team_size=11, score_w=42, odds_team_size=9):
        center_x = (x1 + x2) / 2
        left_logo_x = x1 + 2
        right_logo_x = x2 - logo_size - 2
        has_odds = center_text == "VS" and self._nba_event_has_moneyline_odds(event)
        team_bottom = y + (10 if has_odds else 15)
        team_font_size = min(team_size, odds_team_size) if has_odds else team_size
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, y, logo_size, event["team_a"])
        team_a_box = (left_logo_x + logo_size + 4, y - 1, center_x - score_w / 2 - 3, team_bottom)
        team_b_box = (center_x + score_w / 2 + 3, y - 1, right_logo_x - 4, team_bottom)
        team_a, font_a = self._fit_text(draw, event["team_a"], max(24, team_a_box[2] - team_a_box[0]), team_font_size, bold=True, min_size=7)
        team_a_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "a")]
        team_b_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "b")]
        self._draw_text_in_box(draw, team_a_box, team_a, font_a, team_a_fill)
        center_text, center_font = self._fit_text(draw, center_text, score_w, team_font_size, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (center_x - score_w / 2, y - 1, center_x + score_w / 2, team_bottom), center_text, center_font, COLORS["text"])
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, y, logo_size, event["team_b"])
        team_b, font_b = self._fit_text(draw, event["team_b"], max(24, team_b_box[2] - team_b_box[0]), team_font_size, bold=True, min_size=7)
        self._draw_text_in_box(draw, team_b_box, team_b, font_b, team_b_fill, align="right")
        if has_odds:
            odds = event.get("odds") or {}
            self._draw_nba_odds_text(draw, (team_a_box[0], y + 11, team_a_box[2], y + 21), odds.get("team_a"), max_size=8, align="left")
            self._draw_nba_odds_text(draw, (team_b_box[0], y + 11, team_b_box[2], y + 21), odds.get("team_b"), max_size=8, align="right")

    @staticmethod
    def _nba_event_has_moneyline_odds(event):
        odds = (event or {}).get("odds") or {}
        return bool(odds.get("team_a") and odds.get("team_b"))

    def _draw_nba_odds_pair(self, draw, left_area, right_area, y, event, max_size=9):
        odds = event.get("odds") or {}
        if not (odds.get("team_a") and odds.get("team_b")):
            return
        self._draw_nba_odds_text(draw, (left_area[0], y, left_area[1], y + 12), odds.get("team_a"), max_size=max_size)
        self._draw_nba_odds_text(draw, (right_area[0], y, right_area[1], y + 12), odds.get("team_b"), max_size=max_size)

    def _draw_nba_odds_text(self, draw, box, text, max_size=9, align="center"):
        text = str(text or "").strip()
        if not text:
            return
        left, top, right, bottom = [int(value) for value in box]
        fitted, font = self._fit_text(draw, text, max(1, right - left), max_size, bold=True, min_size=6)
        self._draw_text_in_box(draw, (left, top, right, bottom), fitted, font, COLORS["nba_accent"], align=align)

    def _draw_nba_compact_focus_card(self, image, draw, x1, y1, x2, y2, event, now, is_live):
        accent = COLORS["nba_live"] if is_live else COLORS["nba_accent"]
        compact_card = (y2 - y1) <= 84
        draw.rounded_rectangle((x1 + 3, y1 + 3, x2 + 3, y2 + 3), radius=5, fill=COLORS["nba_shadow"])
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 7, y2 - 1), fill=accent)
        if not event:
            draw.text((x1 + 18, y1 + 36), "No NBA schedule", font=self._font(16, True), fill=COLORS["text"])
            return
        tag = "NOW PLAYING" if is_live else "NEXT MATCH"
        tag_w = 104 if is_live else 92
        tag_text, tag_font = self._fit_text(draw, tag, tag_w - 8, 11, bold=True, min_size=7)
        tag_fill = COLORS["nba_live"] if is_live else COLORS["nba_tag"]
        draw.rectangle((x1 + 14, y1 + 9, x1 + 14 + tag_w, y1 + 27), fill=tag_fill, outline=COLORS["border"], width=1)
        draw.text((x1 + 18, y1 + 10), tag_text, font=tag_font, fill=COLORS["text"])
        date_time = f"{event['start'].strftime('%m/%d')} {event.get('status_text') or self._format_time(event['start'])}"
        date_time, date_font = self._fit_text(draw, date_time, 112, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 11), date_time, date_font, COLORS["muted"])
        score = self._nba_score_label(event)
        core_y = y1 + 29 if compact_card else y1 + 34
        logo_size = 22 if compact_card else 28
        team_size = 12 if compact_card else 15
        self._draw_compact_match_core(image, draw, x1 + 14, x2 - 14, core_y, event, score, logo_size=logo_size, team_size=team_size)
        small_score = self._nba_period_label(event, max_parts=4)
        if small_score and not compact_card:
            small_score, small_font = self._fit_text(draw, small_score, x2 - x1 - 42, 8, bold=True, min_size=7)
            self._draw_centered_in_box(draw, (x1 + 20, y2 - 31, x2 - 20, y2 - 19), small_score, small_font, COLORS["muted"])
        if not compact_card:
            block = str(event.get("block") or "NBA").upper()
            block_text, block_font = self._fit_text(draw, block, x2 - x1 - 36, 9, bold=True, min_size=7)
            draw.text((x1 + 16, y2 - 18), block_text, font=block_font, fill=COLORS["nba_accent"])

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

    def _draw_nba_recent_score_block(self, image, draw, x1, x2, y1, y2, event):
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 6, y2 - 1), fill=COLORS["red"])
        date_text = event["start"].strftime("%m/%d")
        date_text, date_font = self._fit_text(draw, date_text, 42, 11, bold=True, min_size=7)
        draw.text((x1 + 12, y1 + 5), date_text, font=date_font, fill=COLORS["muted"])

        team_text = f"{event['team_a']} vs {event['team_b']}"
        score_label = f"\u603b\u5206 {self._nba_score_label(event)}"
        score_label, score_font = self._fit_text(draw, score_label, 105, 15, bold=True, min_size=10)
        score_box = draw.textbbox((0, 0), score_label, font=score_font)
        score_w = score_box[2] - score_box[0]
        score_left = x2 - score_w - 11
        team_text, team_font = self._fit_text(draw, team_text, max(72, score_left - x1 - 67), 12, bold=True, min_size=8)
        draw.text((x1 + 58, y1 + 4), team_text, font=team_font, fill=COLORS["text"])
        draw.text((score_left, y1 + 2), score_label, font=score_font, fill=COLORS["red"])

        period_label = self._nba_period_label(event, max_parts=4)
        if period_label:
            period_label = f"\u5c0f\u8282 {period_label}"
        else:
            period_label = "\u5c0f\u8282 -"
        period_label, period_font = self._fit_text(draw, period_label, x2 - x1 - 24, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 12, y2 - 18, x2 - 12, y2 - 4), period_label, period_font, COLORS["muted"])

    def _draw_nba_panel(self, image, top_y, panel_height, selected, source_state, now):
        draw = ImageDraw.Draw(image)
        width, _height = image.size
        bottom_y = top_y + panel_height - 1
        draw.rectangle((0, top_y, width - 1, bottom_y), fill=COLORS["panel"])
        self._draw_halftone(draw, (0, top_y, width - 1, bottom_y), COLORS["nba_accent"], COLORS["panel"], 22, 1)
        draw.line((0, top_y, width, top_y), fill=COLORS["border"], width=2)

        live = selected.get("live") or []
        upcoming = selected.get("upcoming") or []
        recent = selected.get("recent") or []
        main_event = live[0] if live else (upcoming[0] if upcoming else selected.get("main"))
        remaining_upcoming = [event for event in upcoming if event is not main_event][:2]

        header_y = top_y + 8
        self._draw_nba_logo(image, draw, 16, header_y - 2, 34, 38)
        title_text, title_font = self._fit_text(draw, "NBA", 82, 25, bold=True, min_size=18)
        draw.text((58, header_y + 2), title_text, font=title_font, fill=COLORS["text"])
        source_label = self._source_label(source_state)
        source_label, source_font = self._fit_text(draw, source_label, 116, 11, bold=True, min_size=8)
        draw.text((58, header_y + 29), source_label, font=source_font, fill=COLORS["muted"])
        self._draw_status_pill(draw, width - 92, header_y + 4, "LIVE" if live else "NEXT", bool(live))
        draw.line((14, top_y + 48, width - 14, top_y + 48), fill=COLORS["border"], width=1)

        content_y = top_y + 58
        focus_x1 = 14
        focus_x2 = min(width - 14, 366)
        focus_h = max(164, panel_height - 72)
        self._draw_nba_focus_card(
            image,
            draw,
            focus_x1,
            content_y,
            focus_x2,
            min(bottom_y - 10, content_y + focus_h),
            main_event,
            now,
            bool(live),
        )

        right_x = focus_x2 + 16
        right_w = width - right_x - 14
        if right_w < 240:
            return
        self._draw_nba_upcoming_rows(image, draw, right_x, right_w, content_y, remaining_upcoming, now)
        recent_y = min(bottom_y - 94, content_y + 128)
        self._draw_nba_recent_rows(image, draw, right_x, right_w, recent_y, recent[:2])

    def _draw_nba_logo(self, image, draw, x, y, width, height):
        x = int(x)
        y = int(y)
        width = int(width)
        height = int(height)
        logo = self._load_local_logo(LOCAL_NBA_LOGO_PATH, (width, height), alpha_threshold=8)
        if logo:
            image.paste(logo, (x + (width - logo.width) // 2, y + (height - logo.height) // 2), logo)
            return
        draw.rounded_rectangle((x, y, x + width, y + height), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x + 3, y + 3, x + width // 2, y + height - 3), fill=COLORS["blue"])
        draw.rectangle((x + width // 2, y + 3, x + width - 3, y + height - 3), fill=COLORS["red"])
        text, font = self._fit_text(draw, "NBA", width - 8, 12, bold=True, min_size=8)
        self._draw_centered(draw, (x + width / 2, y + height / 2), text, font, COLORS["paper_text"])

    def _draw_nba_focus_card(self, image, draw, x1, y1, x2, y2, event, now, is_live):
        accent = COLORS["nba_live"] if is_live else COLORS["nba_accent"]
        draw.rounded_rectangle((x1 + 4, y1 + 4, x2 + 4, y2 + 4), radius=6, fill=COLORS["nba_shadow"])
        draw.rounded_rectangle((x1, y1, x2, y2), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, y2 - 1), fill=accent)
        if not event:
            draw.text((x1 + 22, y1 + 72), "No NBA schedule", font=self._font(20, True), fill=COLORS["text"])
            return

        tag = "NOW PLAYING" if is_live else "NEXT MATCH"
        tag_w = 114 if is_live else 94
        tag_text, tag_font = self._fit_text(draw, tag, tag_w - 10, 12, bold=True, min_size=8)
        tag_fill = COLORS["nba_live"] if is_live else COLORS["nba_tag"]
        draw.rectangle((x1 + 18, y1 + 12, x1 + 18 + tag_w, y1 + 31), fill=tag_fill, outline=COLORS["border"], width=1)
        draw.text((x1 + 23, y1 + 13), tag_text, font=tag_font, fill=COLORS["text"])
        date_text = event["start"].strftime("%m/%d")
        status_text = str(event.get("status_text") or self._format_time(event["start"]))
        right_label, right_font = self._fit_text(draw, f"{date_text} {status_text}", 120, 12, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 14, y1 + 13), right_label, right_font, COLORS["muted"])

        center_x = (x1 + x2) / 2
        logo_size = 42
        left_area = (x1 + 22, center_x - 54)
        right_area = (center_x + 54, x2 - 22)
        logo_y = y1 + 52
        left_logo_x = int((left_area[0] + left_area[1] - logo_size) / 2)
        right_logo_x = int((right_area[0] + right_area[1] - logo_size) / 2)
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, logo_y, logo_size, event["team_a"])
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, logo_y, logo_size, event["team_b"])

        score = self._nba_score_label(event)
        if score != "VS":
            label, label_font = self._fit_text(draw, "TOTAL", 50, 9, bold=True, min_size=7)
            self._draw_centered(draw, (center_x, y1 + 64), label, label_font, COLORS["muted"])
        score_text, score_font = self._fit_text(draw, score, 82, 31 if score != "VS" else 25, bold=True, min_size=17)
        self._draw_centered(draw, (center_x, y1 + 87), score_text, score_font, COLORS["text"])

        team_y = y1 + 111
        team_a_label = self._nba_display_team_from_event(event, "a", full=True)
        team_b_label = self._nba_display_team_from_event(event, "b", full=True)
        team_a, font_a = self._fit_text(draw, team_a_label, left_area[1] - left_area[0], 20, bold=True, min_size=12)
        team_b, font_b = self._fit_text(draw, team_b_label, right_area[1] - right_area[0], 20, bold=True, min_size=12)
        team_a_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "a")]
        team_b_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "b")]
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, font_a, team_a_fill)
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, font_b, team_b_fill)

        small_score = self._nba_period_label(event, max_parts=4)
        if small_score:
            box_y1 = y1 + 130
            box_y2 = min(y2 - 34, box_y1 + 24)
            draw.rounded_rectangle((x1 + 18, box_y1, x2 - 18, box_y2), radius=5, fill=COLORS["panel_blue"], outline=COLORS["border"], width=1)
            label, label_font = self._fit_text(draw, "QTR", 28, 9, bold=True, min_size=7)
            draw.text((x1 + 25, box_y1 + 6), label, font=label_font, fill=COLORS["nba_accent"])
            small_text, small_font = self._fit_text(draw, small_score, x2 - x1 - 88, 12, bold=True, min_size=8)
            self._draw_text_in_box(draw, (x1 + 58, box_y1, x2 - 24, box_y2), small_text, small_font, COLORS["text"])

        line_label = "" if small_score else SportsDashboard._nba_main_footer_label(event)
        block = str(event.get("block") or "NBA").upper()
        block_width = x2 - x1 - (190 if line_label else 42)
        block_text, block_font = self._fit_text(draw, block, max(80, block_width), 11, bold=True, min_size=8)
        draw.text((x1 + 18, y2 - 23), block_text, font=block_font, fill=COLORS["nba_accent"])
        if line_label:
            line_label, line_font = self._fit_text(draw, line_label, 164, 9, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 14, y2 - 23), line_label, line_font, COLORS["muted"])

    def _draw_nba_upcoming_rows(self, image, draw, right_x, right_w, y, events, now):
        self._draw_section_header(draw, right_x, right_w, y, "UPCOMING", COLORS["nba_accent"])
        if not events:
            draw.text((right_x + 18, y + 36), "No more NBA schedule", font=self._font(14, True), fill=COLORS["muted"])
            return
        row_y = y + 27
        for index, event in enumerate(events[:2]):
            self._draw_nba_match_row(image, draw, right_x, right_w, row_y + index * 46, event, now)

    def _draw_nba_match_row(self, image, draw, right_x, right_w, y, event, now):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        draw.rounded_rectangle((row_x1, y, row_x2, y + 40), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((row_x1 + 1, y + 1, row_x1 + 5, y + 39), fill=COLORS["nba_accent"])
        date_text, date_font = self._fit_text(draw, event["start"].strftime("%m/%d"), 42, 11, bold=True, min_size=8)
        draw.text((row_x1 + 12, y + 2), date_text, font=date_font, fill=COLORS["muted"])
        time_text, time_font = self._fit_text(draw, self._format_time(event["start"]), 78, 12, bold=True, min_size=9)
        self._draw_centered(draw, (right_x + right_w / 2, y + 8), time_text, time_font, COLORS["text"])
        self._draw_nba_teams_inline(image, draw, row_x1, row_x2, y + 17, event, "VS")

    def _draw_nba_recent_rows(self, image, draw, right_x, right_w, y, events):
        self._draw_section_header(draw, right_x, right_w, y, "RECENT", COLORS["nba_accent"])
        if not events:
            draw.text((right_x + 18, y + 36), "No recent NBA results", font=self._font(14, True), fill=COLORS["muted"])
            return
        row_y = y + 25
        for index, event in enumerate(events[:2]):
            self._draw_nba_recent_row(image, draw, right_x, right_w, row_y + index * 38, event)

    def _draw_nba_recent_row(self, image, draw, right_x, right_w, y, event):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        draw.line((row_x1, y - 5, row_x2, y - 5), fill=COLORS["line"], width=1)
        draw.text((row_x1 + 2, y + 6), event["start"].strftime("%m/%d"), font=self._font(11, True), fill=COLORS["text"])
        score = self._nba_score_label(event)
        self._draw_nba_teams_inline(image, draw, row_x1 + 44, row_x2, y + 2, event, score)
        small_score = self._nba_period_label(event, max_parts=2)
        if small_score:
            small_score, small_font = self._fit_text(draw, small_score, row_x2 - row_x1 - 92, 9, bold=True, min_size=7)
            self._draw_centered_in_box(draw, (row_x1 + 68, y + 20, row_x2 - 68, y + 34), small_score, small_font, COLORS["muted"])

    def _draw_nba_teams_inline(self, image, draw, x1, x2, y, event, center_text):
        center_x = (x1 + x2) / 2
        logo_size = NBA_INLINE_LOGO_SIZE
        left_logo_x = x1 + 8
        right_logo_x = x2 - 8 - logo_size
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, y, logo_size, event["team_a"])
        team_a, font_a = self._fit_text(
            draw,
            event["team_a"],
            max(28, center_x - left_logo_x - logo_size - 20),
            NBA_INLINE_TEAM_FONT_SIZE,
            bold=True,
            min_size=NBA_INLINE_TEAM_MIN_FONT_SIZE,
        )
        center_text, center_font = self._fit_text(draw, center_text, 54, 13, bold=True, min_size=9)
        self._draw_centered_in_box(draw, (center_x - 27, y - 1, center_x + 27, y + 18), center_text, center_font, COLORS["text"])
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, y, logo_size, event["team_b"])
        team_b, font_b = self._fit_text(
            draw,
            event["team_b"],
            max(28, right_logo_x - center_x - 32),
            NBA_INLINE_TEAM_FONT_SIZE,
            bold=True,
            min_size=NBA_INLINE_TEAM_MIN_FONT_SIZE,
        )
        team_a_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "a")]
        team_b_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "b")]
        self._draw_text_in_box(draw, (left_logo_x + logo_size + 5, y - 1, center_x - 25, y + 18), team_a, font_a, team_a_fill)
        self._draw_text_in_box(draw, (center_x + 28, y - 1, right_logo_x - 5, y + 18), team_b, font_b, team_b_fill, align="right")

    @staticmethod
    def _nba_winner_side(event):
        if SportsDashboard._hub_event_state(event) != "final":
            return ""
        event = event or {}
        if event.get("winner_a") is True and event.get("winner_b") is not True:
            return "a"
        if event.get("winner_b") is True and event.get("winner_a") is not True:
            return "b"
        score_a = SportsDashboard._lpl_int_value(event.get("wins_a"))
        score_b = SportsDashboard._lpl_int_value(event.get("wins_b"))
        if score_a is None or score_b is None or score_a == score_b:
            return ""
        return "a" if score_a > score_b else "b"

    @staticmethod
    def _nba_team_side_fill_key(event, side):
        return "nba_accent" if SportsDashboard._nba_winner_side(event) == side else "text"

    @staticmethod
    def _nba_score_label(event):
        score = SportsDashboard._score_label(event or {})
        return "VS" if score == "vs" else score

    @staticmethod
    def _nba_line_total_label(event):
        event = event or {}
        parts = []
        spread = str(event.get("spread") or "").strip()
        if spread:
            parts.append(f"SPREAD {spread}")
        total = str(event.get("over_under") or "").strip()
        if total:
            parts.append(total)
        return "  |  ".join(parts)

    @staticmethod
    def _nba_main_footer_label(event, max_period_parts=4):
        period_label = SportsDashboard._nba_period_label(event, max_parts=max_period_parts)
        if period_label:
            return period_label
        return SportsDashboard._nba_pregame_meta_label(event)

    @staticmethod
    def _nba_pregame_meta_label(event):
        event = event or {}
        parts = []
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast:
            parts.append(f"TV {broadcast}")
        spread = str(event.get("spread") or "").strip()
        if spread and len(parts) < 2:
            parts.append(f"SPREAD {spread}")
        total = str(event.get("over_under") or "").strip()
        if total and len(parts) < 2:
            parts.append(total)
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue and len(parts) < 2:
            parts.append(venue)
        return "  |  ".join(parts)

    @staticmethod
    def _nba_period_label(event, max_parts=4):
        scores_a = list((event or {}).get("period_scores_a") or [])
        scores_b = list((event or {}).get("period_scores_b") or [])
        pair_count = min(len(scores_a), len(scores_b), max_parts)
        if pair_count <= 0:
            return ""
        parts = []
        for index in range(pair_count):
            label = f"Q{index + 1}" if index < 4 else f"OT{index - 3}"
            parts.append(f"{label} {scores_a[index]}-{scores_b[index]}")
        return "  ".join(parts)

    def _draw_valve_esports_sidebar(self, image, left_width, selected, source_state, now):
        draw = ImageDraw.Draw(image)
        width, height = image.size
        right_x = left_width + LPL_SEPARATOR_WIDTH
        right_w = width - right_x
        primary = (selected or {}).get("primary") or {}
        main_event = primary.get("main") or {}
        recent = primary.get("recent") or []
        status = str(primary.get("status") or "ACTIVE").upper()
        accent = self._valve_series_accent(primary, status)

        draw.rectangle((left_width, 0, right_x - 1, height), fill=COLORS["paper"])
        draw.line((left_width, 0, left_width, height), fill=COLORS["border"], width=1)
        if LPL_SEPARATOR_WIDTH > 2:
            draw.line((left_width + 2, 0, left_width + 2, height), fill=COLORS["line"], width=1)
        draw.rectangle((right_x, 0, width - 1, height - 1), fill=COLORS["panel"])
        self._draw_halftone(draw, (right_x, 0, width - 1, height - 1), self._valve_series_shadow(primary), COLORS["panel"], 20, 1)
        draw.line((right_x, 0, right_x, height), fill=COLORS["border"], width=1)

        header_y = 10
        panel_left = right_x + 12
        panel_right = right_x + right_w - 12
        series = str(primary.get("series") or "").upper()
        header_title = {"CS": "Counter-Strike 2", "TI": "Dota 2"}.get(series, "")
        status_text = self._valve_status_pill_text(primary)
        if header_title:
            logo_size = 40
            logo_x = panel_left + 2
            logo_y = header_y + 3
            title_left = logo_x + logo_size + 9
            badge_width = 58
            badge_x = panel_right - badge_width
            self._draw_valve_esports_logo(image, draw, logo_x, logo_y, logo_size, logo_size, primary)
            title_text, title_font = self._fit_text_ellipsis(
                draw,
                header_title,
                max(1, panel_right - title_left),
                15,
                bold=True,
                min_size=10,
            )
            self._draw_text_in_box(
                draw,
                (title_left, header_y + 4, panel_right, header_y + 25),
                title_text,
                title_font,
                COLORS["text"],
                align="left",
            )
            source_label = self._source_label(source_state)
            source_label, source_font = self._fit_text_ellipsis(
                draw,
                source_label,
                max(1, badge_x - title_left - 6),
                8,
                bold=True,
                min_size=6,
            )
            self._draw_text_in_box(
                draw,
                (title_left, header_y + 30, badge_x - 6, header_y + 46),
                source_label,
                source_font,
                COLORS["muted"],
                align="left",
            )
            self._draw_valve_status_badge(draw, badge_x, header_y + 29, badge_width, 18, status_text, status == "LIVE")
        else:
            self._draw_valve_esports_logo(image, draw, panel_left + 1, header_y + 4, 70, 40, primary)
            source_label = self._source_label(source_state)
            source_label, source_font = self._fit_text_ellipsis(draw, source_label, 68, 9, bold=True, min_size=7)
            self._draw_text_in_box(
                draw,
                (right_x + 88, header_y + 9, panel_right - 68, header_y + 31),
                source_label,
                source_font,
                COLORS["muted"],
                align="center",
            )
            self._draw_valve_status_badge(draw, panel_right - 58, header_y + 14, 58, 18, status_text, status == "LIVE")
        draw.line((panel_left + 2, 66, panel_right - 2, 66), fill=COLORS["border"], width=1)

        self._draw_valve_esports_focus_card(image, draw, right_x, right_w, 78, primary, main_event, now, accent)
        rows = [event for event in recent if event is not main_event][:3]
        self._draw_valve_esports_recent_rows(image, draw, right_x, right_w, 282, rows, primary, accent)

    @staticmethod
    def _valve_series_key(primary):
        series = str((primary or {}).get("series") or "").strip().upper()
        return "ti" if series == "TI" else "cs"

    @staticmethod
    def _valve_series_accent(primary, status=None):
        if str(status or (primary or {}).get("status") or "").strip().upper() == "LIVE":
            return COLORS["red"]
        return COLORS["valve_ti_accent"] if SportsDashboard._valve_series_key(primary) == "ti" else COLORS["valve_cs_accent"]

    @staticmethod
    def _valve_series_tag_fill(primary):
        return COLORS["valve_ti_tag"] if SportsDashboard._valve_series_key(primary) == "ti" else COLORS["valve_cs_tag"]

    @staticmethod
    def _valve_series_shadow(primary):
        if SportsDashboard._valve_series_key(primary) == "ti":
            return COLORS["valve_shadow"]
        return COLORS["valve_cs_accent"]

    @staticmethod
    def _valve_focus_header_layout(card_x1, card_x2, y):
        return {
            "tag_box": (card_x1 + 16, y + 12, card_x2 - 16, y + 30),
            "date_box": (card_x2 - 92, y + 32, card_x2 - 16, y + 42),
            "title_box": (card_x1 + 18, y + 46, card_x2 - 20, y + 64),
            "subtitle_box": (card_x1 + 19, y + 70, card_x2 - 20, y + 81),
        }

    def _draw_valve_esports_logo(self, image, draw, x, y, width, height, primary):
        logo_path = (primary or {}).get("logo_path") or ""
        logo = self._load_local_logo(logo_path, (int(width), int(height)), alpha_threshold=8)
        if logo:
            image.paste(logo, (int(x) + (int(width) - logo.width) // 2, int(y) + (int(height) - logo.height) // 2), logo)
            return
        fallback_text = "CS" if str((primary or {}).get("series") or "").upper() == "CS" else "D2"
        draw.rounded_rectangle((x, y, x + width, y + height), radius=5, fill=self._valve_series_tag_fill(primary), outline=COLORS["border"], width=2)
        draw.rectangle((x + 5, y + 5, x + 13, y + height - 5), fill=self._valve_series_accent(primary), outline=COLORS["border"], width=1)
        text, font = self._fit_text_ellipsis(draw, fallback_text, width - 28, max(16, int(height * 0.62)), bold=True, min_size=13)
        self._draw_centered(draw, (x + width / 2 + 4, y + height / 2), text, font, COLORS["text"])

    def _draw_valve_status_badge(self, draw, x, y, width, height, text, is_live):
        color = COLORS["red"] if is_live else COLORS["green"]
        draw.rounded_rectangle((x, y, x + width, y + height), radius=4, outline=COLORS["border"], fill=COLORS["panel"], width=1)
        dot_size = max(6, min(9, int(height * 0.46)))
        dot_y = y + (height - dot_size) // 2
        draw.rectangle((x + 5, dot_y, x + 5 + dot_size, dot_y + dot_size), fill=color, outline=COLORS["border"], width=1)
        value, value_font = self._fit_text_ellipsis(draw, text, width - dot_size - 14, 9, bold=True, min_size=7)
        self._draw_text_in_box(draw, (x + dot_size + 10, y + 1, x + width - 4, y + height - 1), value, value_font, COLORS["text"])
    @staticmethod
    def _valve_status_pill_text(primary):
        status = str((primary or {}).get("status") or "ACTIVE").strip().upper()
        if status == "LIVE":
            return "LIVE"
        if status == "NEXT":
            return "NEXT"
        if status == "RECENT":
            return "RECENT"
        return "ACTIVE"

    def _draw_valve_esports_focus_card(self, image, draw, right_x, right_w, y, primary, event, now, accent):
        card_x1 = right_x + 12
        card_x2 = right_x + right_w - 12
        card_y2 = y + 188
        draw.rounded_rectangle((card_x1 + 4, y + 4, card_x2 + 4, card_y2 + 4), radius=6, fill=COLORS["valve_shadow"])
        draw.rounded_rectangle((card_x1, y, card_x2, card_y2), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((card_x1 + 1, y + 1, card_x1 + 8, card_y2 - 1), fill=accent)

        if not event:
            draw.text((card_x1 + 20, y + 58), "No Valve event", font=self._font(19, True), fill=COLORS["text"])
            return

        header = self._valve_focus_header_layout(card_x1, card_x2, y)
        tag_box = header["tag_box"]
        tag = str((primary or {}).get("sport") or "VALVE").upper()
        tag_text, tag_font = self._fit_text_ellipsis(draw, tag, tag_box[2] - tag_box[0] - 12, 11, bold=True, min_size=7)
        draw.rectangle(tag_box, fill=self._valve_series_tag_fill(primary), outline=COLORS["border"], width=1)
        self._draw_text_in_box(draw, (tag_box[0] + 6, tag_box[1], tag_box[2] - 6, tag_box[3]), tag_text, tag_font, COLORS["text"])

        date_label = self._valve_event_date_label(primary, event)
        date_box = header["date_box"]
        date_label, date_font = self._fit_text_ellipsis(draw, date_label, date_box[2] - date_box[0], 9, bold=True, min_size=7)
        self._draw_text_in_box(draw, date_box, date_label, date_font, COLORS["muted"], align="right")

        title = str((primary or {}).get("event_name") or "Valve Event").strip() or "Valve Event"
        title_box = header["title_box"]
        title, title_font = self._fit_text_ellipsis(draw, title, title_box[2] - title_box[0], 18, bold=True, min_size=11)
        self._draw_text_in_box(draw, title_box, title, title_font, COLORS["text"])
        subtitle = f"{event.get('source') or primary.get('source') or 'Valve'} TRACK"
        subtitle_box = header["subtitle_box"]
        subtitle, subtitle_font = self._fit_text_ellipsis(draw, subtitle, subtitle_box[2] - subtitle_box[0], 9, bold=True, min_size=7)
        self._draw_text_in_box(draw, subtitle_box, subtitle, subtitle_font, accent)

        center_x = (card_x1 + card_x2) / 2
        board_y1 = y + 88
        board_y2 = y + 153
        draw.rounded_rectangle((card_x1 + 16, board_y1, card_x2 - 16, board_y2), radius=5, fill=COLORS["panel_blue"], outline=COLORS["border"], width=1)
        logo_size = 30
        left_area = (card_x1 + 24, center_x - 35)
        right_area = (center_x + 35, card_x2 - 24)
        left_logo_x = int((left_area[0] + left_area[1] - logo_size) / 2)
        right_logo_x = int((right_area[0] + right_area[1] - logo_size) / 2)
        logo_y = int(board_y1 + 7)
        self._draw_valve_team_icon(image, draw, event, "a", left_logo_x, logo_y, logo_size)
        self._draw_valve_team_icon(image, draw, event, "b", right_logo_x, logo_y, logo_size)

        score = self._valve_score_label(event)
        score, score_font = self._fit_text_ellipsis(draw, score, 68, 25, bold=True, min_size=16)
        self._draw_centered_in_box(draw, (center_x - 34, board_y1 + 6, center_x + 34, board_y1 + 35), score, score_font, COLORS["text"])
        score_kind = str(event.get("score_kind") or "").strip().upper()
        if score_kind:
            kind_text, kind_font = self._fit_text_ellipsis(draw, score_kind, 62, 8, bold=True, min_size=6)
            self._draw_centered_in_box(draw, (center_x - 31, board_y1 + 36, center_x + 31, board_y1 + 49), kind_text, kind_font, COLORS["muted"])

        team_a_label = self._valve_team_display_name(event, "a")
        team_b_label = self._valve_team_display_name(event, "b")
        team_a, font_a = self._fit_text_ellipsis(draw, team_a_label, left_area[1] - left_area[0], 13, bold=True, min_size=8)
        team_b, font_b = self._fit_text_ellipsis(draw, team_b_label, right_area[1] - right_area[0], 13, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (left_area[0], board_y2 - 17, left_area[1], board_y2 - 3), team_a, font_a, COLORS["text"])
        self._draw_centered_in_box(draw, (right_area[0], board_y2 - 17, right_area[1], board_y2 - 3), team_b, font_b, COLORS["text"])

        detail = self._valve_match_detail_label(event)
        detail, detail_font = self._fit_text_ellipsis(draw, detail, card_x2 - card_x1 - 44, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (card_x1 + 20, y + 160, card_x2 - 20, y + 176), detail, detail_font, COLORS["muted"])
    @staticmethod
    def _valve_team_display_name(event, side):
        event = event or {}
        if side == "a":
            return str(event.get("team_a_tag") or event.get("team_a") or "TBD").strip() or "TBD"
        return str(event.get("team_b_tag") or event.get("team_b") or "TBD").strip() or "TBD"

    def _draw_valve_team_icon(self, image, draw, event, side, x, y, size):
        logo_url = str((event or {}).get("team_a_logo" if side == "a" else "team_b_logo") or "").strip()
        name = str((event or {}).get("team_a" if side == "a" else "team_b") or "TBD").strip() or "TBD"
        team_id = (event or {}).get("team_a_id" if side == "a" else "team_b_id")
        series = str((event or {}).get("series") or "").strip().upper()
        logo = self._load_team_logo(logo_url, int(size)) if logo_url else None
        if not logo:
            logo = self._load_valve_local_team_logo(name, team_id, int(size), series)
        if logo:
            image.paste(logo, (int(x) + (int(size) - logo.width) // 2, int(y) + (int(size) - logo.height) // 2), logo)
            return
        fill, stripe = self._valve_team_icon_colors(name, team_id)
        draw.rounded_rectangle((x, y, x + size, y + size), radius=5, fill=fill, outline=COLORS["border"], width=1)
        draw.rectangle((x + 3, y + 3, x + 7, y + size - 3), fill=stripe)
        initials = self._valve_team_initials(name)
        initials, font = self._fit_text(draw, initials, max(12, size - 13), max(12, int(size * 0.42)), bold=True, min_size=8)
        self._draw_centered(draw, (x + size / 2 + 3, y + size / 2), initials, font, COLORS["text"])

    @staticmethod
    def _load_valve_local_team_logo(name, team_id, size, series=None):
        for path in SportsDashboard._valve_local_team_logo_candidates(name, team_id, series):
            logo = SportsDashboard._load_local_logo(path, (size, size))
            if logo:
                return logo
        return None

    @staticmethod
    def _valve_local_team_logo_dirs(series):
        series = str(series or "").strip().upper()
        if series == "TI":
            return [LOCAL_DOTA2_TEAM_LOGO_DIR]
        if series == "CS":
            return [LOCAL_CS2_TEAM_LOGO_DIR]
        return [LOCAL_CS2_TEAM_LOGO_DIR, LOCAL_DOTA2_TEAM_LOGO_DIR]

    @staticmethod
    def _valve_local_team_logo_candidates(name, team_id, series=None):
        candidates = []
        logo_dirs = SportsDashboard._valve_local_team_logo_dirs(series)
        team_id_value = SportsDashboard._lpl_int_value(team_id)
        if team_id_value:
            for logo_dir in logo_dirs:
                candidates.extend(
                    os.path.join(logo_dir, f"{team_id_value}{extension}")
                    for extension in (".png", ".webp", ".jpg", ".jpeg")
                )
        slug = SportsDashboard._valve_team_logo_slug(name)
        if slug:
            for logo_dir in logo_dirs:
                candidates.extend(
                    os.path.join(logo_dir, f"{slug}{extension}")
                    for extension in (".png", ".webp", ".jpg", ".jpeg")
                )
        return candidates

    @staticmethod
    def _valve_team_logo_slug(name):
        normalized = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
        return "".join(ch for ch in normalized.lower() if ch.isalnum())

    @staticmethod
    def _valve_team_initials(name):
        words = [part for part in str(name or "").replace("_", " ").replace("-", " ").split() if part]
        if not words:
            return "?"
        if len(words) >= 2:
            return "".join(part[0] for part in words[:3]).upper()
        letters = "".join(ch for ch in words[0].upper() if ch.isalnum())
        return (letters[:3] or "?")

    @staticmethod
    def _valve_team_icon_colors(name, team_id=None):
        palette = [
            ((34, 73, 128), COLORS["amber"]),
            ((92, 38, 116), COLORS["cyan"]),
            ((34, 104, 89), COLORS["orange"]),
            ((126, 48, 54), COLORS["amber"]),
            ((72, 79, 96), COLORS["green"]),
            ((44, 92, 147), COLORS["red"]),
        ]
        seed_text = f"{name or ''}:{team_id or ''}"
        seed = sum((index + 1) * ord(ch) for index, ch in enumerate(seed_text))
        return palette[seed % len(palette)]

    def _draw_valve_esports_recent_rows(self, image, draw, right_x, right_w, y, events, primary, accent):
        self._draw_section_header(draw, right_x, right_w, y, "RECENT", accent)
        if not events:
            draw.text((right_x + 18, y + 36), "No more Valve results", font=self._font(14, True), fill=COLORS["muted"])
            return
        row_y = y + 29
        for index, event in enumerate(events[:3]):
            top = row_y + index * 55
            self._draw_valve_esports_recent_row(image, draw, right_x, right_w, top, event, accent)

    def _draw_valve_esports_recent_row(self, image, draw, right_x, right_w, y, event, accent):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        row_h = 50
        draw.rounded_rectangle((row_x1, y, row_x2, y + row_h), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((row_x1 + 1, y + 1, row_x1 + 5, y + row_h - 1), fill=accent)
        date_label = event["start"].strftime("%m/%d") if isinstance(event.get("start"), datetime) else "--/--"
        date_label, date_font = self._fit_text_ellipsis(draw, date_label, 40, 8, bold=True, min_size=6)
        draw.text((row_x1 + 10, y + 4), date_label, font=date_font, fill=COLORS["muted"])
        score = self._valve_score_label(event)
        score, score_font = self._fit_text_ellipsis(draw, score, 40, 13, bold=True, min_size=9)
        self._draw_centered_in_box(draw, (row_x1 + 91, y + 4, row_x2 - 91, y + 20), score, score_font, COLORS["text"])
        icon_size = 17
        team_y1 = y + 21
        icon_y = y + 21
        center_x = (row_x1 + row_x2) / 2
        self._draw_valve_team_icon(image, draw, event, "a", row_x1 + 12, icon_y, icon_size)
        self._draw_valve_team_icon(image, draw, event, "b", row_x2 - 29, icon_y, icon_size)
        left_name_box = (row_x1 + 33, team_y1 - 1, center_x - 24, team_y1 + 18)
        right_name_box = (center_x + 24, team_y1 - 1, row_x2 - 33, team_y1 + 18)
        team_a, team_a_font = self._fit_text_ellipsis(draw, self._valve_team_display_name(event, "a"), left_name_box[2] - left_name_box[0], 10, bold=True, min_size=7)
        team_b, team_b_font = self._fit_text_ellipsis(draw, self._valve_team_display_name(event, "b"), right_name_box[2] - right_name_box[0], 10, bold=True, min_size=7)
        self._draw_text_in_box(draw, left_name_box, team_a, team_a_font, COLORS["text"])
        self._draw_text_in_box(draw, right_name_box, team_b, team_b_font, COLORS["text"], align="right")
        detail = self._valve_match_detail_label(event, compact=True)
        detail, detail_font = self._fit_text_ellipsis(draw, detail, row_x2 - row_x1 - 22, 7, bold=True, min_size=6)
        self._draw_centered_in_box(draw, (row_x1 + 10, y + 38, row_x2 - 10, y + row_h - 1), detail, detail_font, COLORS["muted"])
    @staticmethod
    def _valve_event_date_label(primary, event):
        start = (primary or {}).get("start") or (event or {}).get("start")
        end = (primary or {}).get("latest")
        if isinstance(start, datetime) and isinstance(end, datetime) and start.date() != end.date():
            return f"{start.strftime('%m/%d')}-{end.strftime('%m/%d')}"
        if isinstance(start, datetime):
            return start.strftime("%m/%d")
        return "--/--"

    @staticmethod
    def _valve_match_detail_label(event, compact=False):
        event = event or {}
        maps = event.get("maps") or []
        if maps:
            parts = []
            for item in maps[:3 if compact else 4]:
                left = SportsDashboard._lpl_int_value(item.get("team_a_score"))
                right = SportsDashboard._lpl_int_value(item.get("team_b_score"))
                score = f" {left}-{right}" if left is not None and right is not None else ""
                parts.append(f"{item.get('name') or 'Map'}{score}")
            return "  |  ".join(parts)
        duration = SportsDashboard._lpl_int_value(event.get("duration"))
        best_of = SportsDashboard._lpl_int_value(event.get("best_of"))
        bits = []
        if best_of:
            bits.append(f"BO{best_of}")
        if duration:
            bits.append(f"{max(1, duration // 60)}m")
        return "  |  ".join(bits) or str(event.get("source") or "Valve")

    @staticmethod
    def _lol_sidebar_config(league_key):
        key = str(league_key or "LPL").strip().upper()
        if key == "LCK":
            return {
                "key": "LCK",
                "name": "LCK",
                "logo_path": LOCAL_LCK_LOGO_PATH,
                "accent": "lck_accent",
                "live": "lck_live",
                "tag": "lck_tag",
                "shadow": "lck_shadow",
                "empty_schedule": "No LCK schedule",
                "empty_upcoming": "No more LCK schedule",
            }
        return {
            "key": "LPL",
            "name": "LPL",
            "logo_path": LOCAL_LPL_LOGO_PATH,
            "accent": "lpl_accent",
            "live": "lpl_live",
            "tag": "lpl_tag",
            "shadow": "lpl_shadow",
            "empty_schedule": "No LPL schedule",
            "empty_upcoming": "No more LPL schedule",
        }

    @staticmethod
    def _lol_sidebar_color(league_key, role):
        config = SportsDashboard._lol_sidebar_config(league_key)
        return COLORS[config.get(role, "lpl_accent")]

    def _draw_lpl_sidebar(self, image, left_width, selected, source_state, now, league_key="LPL"):
        config = self._lol_sidebar_config(league_key)
        draw = ImageDraw.Draw(image)
        width, height = image.size
        right_x = left_width + LPL_SEPARATOR_WIDTH
        right_w = width - right_x
        draw.rectangle((left_width, 0, right_x - 1, height), fill=COLORS["paper"])
        draw.line((left_width, 0, left_width, height), fill=COLORS["border"], width=1)
        if LPL_SEPARATOR_WIDTH > 2:
            draw.line((left_width + 2, 0, left_width + 2, height), fill=COLORS["line"], width=1)
        draw.rectangle((right_x, 0, width - 1, height - 1), fill=COLORS["panel"])
        self._draw_halftone(draw, (right_x, 0, width - 1, height - 1), COLORS[config["shadow"]], COLORS["panel"], 20, 1)
        draw.line((right_x, 0, right_x, height), fill=COLORS["border"], width=1)

        live = selected.get("live") or []
        upcoming = selected.get("upcoming") or []
        recent = selected.get("recent") or []
        featured_event = selected.get("featured_event") or None
        featured_event_page = bool(selected.get("featured_event_page"))
        main_event = live[0] if live else (upcoming[0] if upcoming else selected.get("main"))
        remaining_upcoming = [event for event in upcoming if event is not main_event][:2]
        logo_path = featured_event.get("logo_path") if (featured_event and (featured_event_page or live or upcoming)) else (None if config["key"] == "LPL" else config["logo_path"])

        header_y = 12

        self._draw_lpl_logo(image, draw, right_x + 13, header_y + 5, 74, 38, logo_path=logo_path, fallback_text=config["key"])
        source_label = "MSI WATCH" if featured_event_page and config["key"] == "LPL" else self._source_label(source_state)
        source_label, source_font = self._fit_text(draw, source_label, 62, 10, bold=True, min_size=8)
        self._draw_text_in_box(
            draw,
            (right_x + 90, header_y + 9, right_x + right_w - 92, header_y + 32),
            source_label,
            source_font,
            COLORS["muted"],
            align="center",
        )
        if live:
            pill_text = "LIVE"
        elif featured_event_page:
            pill_text = self._lpl_featured_event_pill_text(featured_event)
        else:
            pill_text = "NEXT"
        self._draw_status_pill(draw, right_x + right_w - 88, header_y + 8, pill_text, bool(live))
        draw.line((right_x + 14, 66, right_x + right_w - 14, 66), fill=COLORS["border"], width=1)

        if featured_event_page:
            self._draw_lpl_featured_event_panel(image, draw, right_x, right_w, 78, height - 1, selected, now)
            return

        msi_next_filler_event = self._lpl_msi_next_filler_event(now, featured_event)
        self._draw_lpl_focus_card(image, draw, right_x, right_w, 78, main_event, now, bool(live), league_key=league_key)
        self._draw_lpl_next_rows(
            image,
            draw,
            right_x,
            right_w,
            244,
            remaining_upcoming,
            now,
            bool(live),
            msi_next_filler=bool(msi_next_filler_event),
            msi_next_start=(msi_next_filler_event or {}).get("start"),
            league_key=league_key,
        )
        self._draw_lpl_recent_rows(image, draw, right_x, right_w, 374, recent[:2], league_key=league_key)

    def _draw_lpl_logo(self, image, draw, x, y, width, height, logo_path=None, fallback_text=None):
        x = int(x)
        y = int(y)
        width = int(width)
        height = int(height)
        logo_path = logo_path or LOCAL_LPL_LOGO_PATH
        logo = self._load_local_logo(logo_path, (width, height), alpha_threshold=8)
        if logo:
            image.paste(logo, (x + (width - logo.width) // 2, y + (height - logo.height) // 2), logo)
            return
        fallback_text = fallback_text or ("MSI" if logo_path == LOCAL_MSI_LOGO_PATH else "LPL")
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=5,
            fill=COLORS["panel_gold"],
            outline=COLORS["border"],
            width=2,
        )
        stripe_w = max(7, int(width * 0.16))
        draw.rectangle((x + 5, y + 5, x + 5 + stripe_w, y + height - 5), fill=COLORS["red"], outline=COLORS["border"], width=1)
        draw.polygon(
            [
                (x + width - 12, y + 5),
                (x + width - 5, y + 5),
                (x + width - 5, y + height - 5),
                (x + width - 18, y + height - 5),
            ],
            fill=COLORS["blue"],
            outline=COLORS["border"],
        )
        text, font = self._fit_text(draw, fallback_text, width - stripe_w - 22, max(16, int(height * 0.62)), bold=True, min_size=13)
        self._draw_centered(draw, (x + width / 2 + 3, y + height / 2), text, font, COLORS["text"])

    @staticmethod
    def _lpl_featured_event_pill_text(featured_event):
        featured_event = featured_event or {}
        if featured_event.get("phase") == "countdown":
            days = SportsDashboard._lpl_int_value(featured_event.get("countdown_days"))
            if days and days > 0:
                return f"D-{days}"
            return "TODAY"
        return str(featured_event.get("name") or "MSI" or "NEXT").strip()[:6]

    def _draw_lpl_featured_event_panel(self, image, draw, right_x, right_w, y1, y2, selected, now):
        featured = (selected or {}).get("featured_event") or {}
        phase = str(featured.get("phase") or "").strip().lower()
        is_countdown = phase == "countdown"
        card_x1 = right_x + 12
        card_x2 = right_x + right_w - 12
        card_y2 = min(y2 - 96, y1 + 196)
        if card_y2 < y1 + 158:
            card_y2 = min(y2, y1 + 158)
        accent = COLORS["lpl_accent"]
        draw.rounded_rectangle((card_x1 + 4, y1 + 4, card_x2 + 4, card_y2 + 4), radius=6, fill=COLORS["lpl_shadow"])
        draw.rounded_rectangle((card_x1, y1, card_x2, card_y2), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((card_x1 + 1, y1 + 1, card_x1 + 8, card_y2 - 1), fill=accent)

        tag = "OFFSEASON" if is_countdown else "FEATURED"
        tag_w = 92 if is_countdown else 82
        tag_text, tag_font = self._fit_text(draw, tag, tag_w - 10, 11, bold=True, min_size=7)
        draw.rectangle((card_x1 + 16, y1 + 12, card_x1 + 16 + tag_w, y1 + 30), fill=COLORS["lpl_tag"], outline=COLORS["border"], width=1)
        draw.text((card_x1 + 21, y1 + 13), tag_text, font=tag_font, fill=COLORS["text"])

        start = featured.get("start")
        end = featured.get("end")
        date_text = start.strftime("%m/%d") if isinstance(start, datetime) else "06/28"
        date_text, date_font = self._fit_text(draw, date_text, 54, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (card_x2 - 13, y1 + 13), date_text, date_font, COLORS["muted"])

        title = "\u4f11\u8d5b\u671f" if is_countdown else "MSI\u8fdb\u884c\u4e2d"
        title, title_font = self._fit_text(draw, title, card_x2 - card_x1 - 94, 29, bold=True, min_size=20)
        title_y = y1 + 41 if is_countdown else y1 + 44
        draw.text((card_x1 + 18, title_y), title, font=title_font, fill=COLORS["text"])
        subtitle = "LPL SEASON BREAK" if is_countdown else "MID-SEASON INVITATIONAL"
        subtitle, subtitle_font = self._fit_text(draw, subtitle, card_x2 - card_x1 - 102, 10, bold=True, min_size=7)
        draw.text((card_x1 + 19, y1 + 77), subtitle, font=subtitle_font, fill=COLORS["lpl_accent"])
        card_accent = self._load_lpl_msi_card_accent((94, 68), now)
        if card_accent:
            image.paste(card_accent, (card_x2 - 112, y1 + 28), card_accent)

        next_y1 = y1 + 103
        next_y2 = min(card_y2 - 14, next_y1 + 58)
        draw.rounded_rectangle((card_x1 + 16, next_y1, card_x2 - 16, next_y2), radius=5, fill=COLORS["panel_blue"], outline=COLORS["border"], width=1)
        label = "\u4e0b\u4e00\u7ad9 MSI" if is_countdown else "MSI STATUS"
        label, label_font = self._fit_text(draw, label, 92, 10, bold=True, min_size=7)
        draw.text((card_x1 + 25, next_y1 + 5), label, font=label_font, fill=COLORS["muted"])
        primary = self._lpl_featured_event_pill_text(featured) if is_countdown else "LIVE"
        primary, primary_font = self._fit_text(draw, primary, 78, 24, bold=True, min_size=16)
        self._draw_right_aligned(draw, (card_x2 - 26, next_y1 + 1), primary, primary_font, COLORS["text"])
        if is_countdown:
            secondary = f"{date_text} \u5f00\u8d5b"
        elif isinstance(start, datetime) and isinstance(end, datetime):
            secondary = f"{start.strftime('%m/%d')}-{end.strftime('%m/%d')}"
        else:
            secondary = "\u8d5b\u7a0b\u8fdb\u884c\u4e2d"
        secondary, secondary_font = self._fit_text(draw, secondary, card_x2 - card_x1 - 50, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (card_x1 + 24, next_y1 + 34, card_x2 - 24, next_y2 - 3), secondary, secondary_font, COLORS["muted"])

        watch_y = card_y2 + 16
        if watch_y + 30 < y2:
            self._draw_section_header(draw, right_x, right_w, watch_y, "MSI WATCH", COLORS["lpl_accent"])
            row_y = watch_y + 30
            visible_count = 0
            for index, (date_label, title_label) in enumerate(self._lpl_featured_watch_items(featured, is_countdown)):
                top = row_y + index * 32
                if top + 25 > y2:
                    break
                visible_count += 1
                row_x1 = right_x + 14
                row_x2 = right_x + right_w - 14
                draw.rounded_rectangle((row_x1, top, row_x2, top + 25), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
                draw.rectangle((row_x1 + 1, top + 1, row_x1 + 5, top + 24), fill=COLORS["lpl_accent"])
                date_label, date_font = self._fit_text(draw, date_label, 46, 10, bold=True, min_size=7)
                draw.text((row_x1 + 10, top + 3), date_label, font=date_font, fill=COLORS["muted"])
                title_label, title_font = self._fit_text(draw, title_label, row_x2 - row_x1 - 66, 11, bold=True, min_size=7)
                self._draw_right_aligned(draw, (row_x2 - 9, top + 3), title_label, title_font, COLORS["text"])
            filler_top = row_y + visible_count * 32 + 4
            self._draw_lpl_featured_event_filler(image, right_x, right_x + right_w - 1, filler_top, y2)

    @staticmethod
    def _lpl_featured_watch_items(featured, is_countdown):
        start = (featured or {}).get("start")
        end = (featured or {}).get("end")
        start_label = start.strftime("%m/%d") if isinstance(start, datetime) else "06/28"
        end_label = end.strftime("%m/%d") if isinstance(end, datetime) else "07/12"
        if is_countdown:
            return [
                (start_label, "MSI \u5f00\u8d5b"),
                (end_label, "MSI FINAL"),
                ("TBD", "LPL \u540e\u7eed\u8d5b\u7a0b"),
            ]
        return [
            ("NOW", "MSI \u8fdb\u884c\u4e2d"),
            (end_label, "MSI FINAL"),
            ("TBD", "LPL \u540e\u7eed\u8d5b\u7a0b"),
        ]

    def _draw_lpl_featured_event_filler(self, image, x1, x2, y1, y2):
        x1 = int(x1)
        x2 = int(x2)
        y1 = int(y1)
        y2 = int(y2)
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        if width < 80 or height < 24:
            return
        source_width = max(width, int(width * LPL_MSI_OFFSEASON_FILLER_ZOOM + 0.999))
        source_height = max(height, int(height * LPL_MSI_OFFSEASON_FILLER_ZOOM + 0.999))
        filler = self._load_lpl_msi_offseason_filler((source_width, source_height))
        if filler:
            if filler.size[0] >= width and filler.size[1] >= height:
                crop_x = (filler.size[0] - width) // 2
                crop_y = filler.size[1] - height
                filler = filler.crop((crop_x, crop_y, crop_x + width, crop_y + height))
            elif filler.size != (width, height):
                filler = ImageOps.fit(filler, (width, height), method=Image.LANCZOS, centering=(0.5, 1.0))
            image.paste(filler, (x1, y1))

    def _draw_status_pill(self, draw, x, y, text, is_live):
        color = COLORS["red"] if is_live else COLORS["green"]
        draw.rounded_rectangle((x, y, x + 74, y + 24), radius=5, outline=COLORS["border"], fill=COLORS["panel"], width=2)
        draw.rectangle((x + 5, y + 5, x + 13, y + 19), fill=color, outline=COLORS["border"], width=1)
        value, value_font = self._fit_text(draw, text, 46, 13, bold=True, min_size=10)
        self._draw_centered_in_box(draw, (x, y + 2, x + 74, y + 22), value, value_font, COLORS["text"])

    def _draw_lpl_odds_text(self, draw, box, text, max_size=11, align="center"):
        text = str(text or "").strip()
        if not text:
            return
        left, top, right, bottom = [int(value) for value in box]
        fitted, font = self._fit_text(draw, text, max(1, right - left), max_size, bold=True, min_size=7)
        if align == "center":
            self._draw_centered_in_box(draw, (left, top, right, bottom), fitted, font, COLORS["text"])
        else:
            self._draw_text_in_box(draw, (left, top, right, bottom), fitted, font, COLORS["text"], align=align)

    def _draw_lpl_focus_card(self, image, draw, right_x, right_w, y, event, now, is_live, league_key="LPL"):
        card_x1 = right_x + 12
        card_x2 = right_x + right_w - 12
        card_y2 = y + 154
        config = self._lol_sidebar_config(league_key)
        accent = COLORS[config["live"]] if is_live else COLORS[config["accent"]]
        draw.rounded_rectangle((card_x1 + 4, y + 4, card_x2 + 4, card_y2 + 4), radius=6, fill=COLORS[config["shadow"]])
        draw.rounded_rectangle((card_x1, y, card_x2, card_y2), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((card_x1 + 1, y + 1, card_x1 + 8, card_y2 - 1), fill=accent)

        if not event:
            draw.text((card_x1 + 20, y + 58), config["empty_schedule"], font=self._font(19, True), fill=COLORS["text"])
            return

        tag = self._lpl_focus_tag(is_live)
        tag_w = 112 if is_live else 86
        tag_text, tag_font = self._fit_text(draw, tag, tag_w - 10, 12, bold=True, min_size=8)
        tag_fill = COLORS[config["live"]] if is_live else COLORS[config["tag"]]
        draw.rectangle((card_x1 + 16, y + 12, card_x1 + 16 + tag_w, y + 31), fill=tag_fill, outline=COLORS["border"], width=1)
        draw.text((card_x1 + 21, y + 13), tag_text, font=tag_font, fill=COLORS["text"])
        date_text = event["start"].strftime("%m/%d")
        date_text, date_font = self._fit_text(draw, date_text, 54, 12, bold=True, min_size=9)
        self._draw_right_aligned(draw, (card_x2 - 12, y + 13), date_text, date_font, COLORS["muted"])

        center_x = right_x + right_w / 2
        time_text = "IN PROGRESS" if is_live else self._format_time(event["start"])
        time_text, time_font = self._fit_text(draw, time_text, card_x2 - card_x1 - 58, 19, bold=True, min_size=13)
        self._draw_centered(draw, (center_x, y + 44), time_text, time_font, COLORS["text"])

        logo_size = 42
        left_area = (card_x1 + 22, center_x - 18)
        right_area = (center_x + 18, card_x2 - 22)
        left_logo_x = int((left_area[0] + left_area[1] - logo_size) / 2)
        right_logo_x = int((right_area[0] + right_area[1] - logo_size) / 2)
        logo_y = y + 61
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, logo_y, logo_size, event["team_a"])
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, logo_y, logo_size, event["team_b"])
        score_text = self._score_label(event).upper()
        center_score = score_text if is_live and score_text != "VS" else "VS"
        team_a_label = self._lpl_display_team_from_event(event, "a", league_key=league_key)
        team_b_label = self._lpl_display_team_from_event(event, "b", league_key=league_key)

        stage = self._lpl_stage_label(event, league_key=league_key)
        if not is_live:
            stage_text, stage_font = self._fit_text(draw, stage, 88, 12, bold=True, min_size=7)
            self._draw_centered_in_box(
                draw,
                (center_x - 44, y + 76, center_x + 44, y + 88),
                stage_text,
                stage_font,
                COLORS[config["accent"]],
            )
        self._draw_centered(draw, (center_x, y + (98 if not is_live else 86)), center_score, self._font(13, True), COLORS["text"])
        if is_live:
            self._draw_lpl_little_round(draw, center_x, y, event)

        team_y = y + 116
        team_a, font_a = self._fit_text(draw, team_a_label, left_area[1] - left_area[0], 22, bold=True, min_size=13)
        team_b, font_b = self._fit_text(draw, team_b_label, right_area[1] - right_area[0], 22, bold=True, min_size=13)
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, font_a, COLORS["text"])
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, font_b, COLORS["text"])

        odds = event.get("odds") or {}
        has_odds = bool(odds.get("team_a") and odds.get("team_b"))
        if has_odds:
            self._draw_lpl_odds_text(draw, (left_area[0], y + 132, left_area[1], y + 144), odds.get("team_a"), max_size=11)
            self._draw_lpl_odds_text(draw, (right_area[0], y + 132, right_area[1], y + 144), odds.get("team_b"), max_size=11)
        elif is_live:
            block_text, block_font = self._fit_text(draw, stage, card_x2 - card_x1 - 34, 12, bold=True, min_size=8)
            draw.text((card_x1 + 17, y + 136), block_text, font=block_font, fill=COLORS[config["accent"]])

    def _draw_lpl_little_round(self, draw, center_x, y, event):
        little_round = (event or {}).get("little_round") or {}
        if not little_round:
            return
        score = str(little_round.get("score") or "").strip()
        if not score:
            return
        label, label_font = self._fit_text(draw, "Little Round", 78, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (center_x - 40, y + 96, center_x + 40, y + 106), label, label_font, COLORS["muted"])
        score_text, score_font = self._fit_text(draw, score, 48, 12, bold=True, min_size=9)
        self._draw_centered_in_box(draw, (center_x - 24, y + 106, center_x + 24, y + 119), score_text, score_font, COLORS["amber"])

    @staticmethod
    def _lpl_focus_tag(is_live):
        return "NOW PLAYING" if is_live else "NEXT MATCH"

    def _draw_lpl_next_rows(self, image, draw, right_x, right_w, y, events, now, is_live, msi_next_filler=False, msi_next_start=None, league_key="LPL"):
        config = self._lol_sidebar_config(league_key)
        self._draw_section_header(draw, right_x, right_w, y, "UPCOMING", COLORS[config["accent"]])
        if not events:
            draw.text((right_x + 18, y + 38), config["empty_upcoming"], font=self._font(14, True), fill=COLORS["muted"])
            self._draw_lpl_empty_upcoming_filler(
                image,
                right_x,
                right_w,
                y,
                0,
                msi_next_filler=msi_next_filler,
                msi_next_start=msi_next_start,
            )
            return
        row_y = y + 30
        visible_events = events[:2]
        for index, event in enumerate(visible_events):
            self._draw_lpl_next_row(image, draw, right_x, right_w, row_y + index * 48, event, now, league_key=league_key)
        self._draw_lpl_empty_upcoming_filler(
            image,
            right_x,
            right_w,
            y,
            len(visible_events),
            msi_next_filler=msi_next_filler,
            msi_next_start=msi_next_start,
        )

    def _draw_lpl_empty_upcoming_filler(self, image, right_x, right_w, section_y, visible_count, msi_next_filler=False, msi_next_start=None):
        if visible_count >= 2:
            return
        x1 = int(right_x + 14)
        x2 = int(right_x + right_w - 14)
        y1 = int(section_y + (76 if visible_count <= 0 else 30 + visible_count * 48))
        y2 = int(section_y + 124)
        width = x2 - x1
        height = y2 - y1
        if width < 80 or height < 24:
            return
        if msi_next_filler:
            filler = self._load_lpl_msi_next_filler((width, height))
            if filler:
                image.paste(filler, (x1, y1))
                self._draw_lpl_msi_next_label(image, x1, y1, width, height, msi_next_start)
                return
        filler = self._load_lpl_sidebar_filler((width, height))
        if filler:
            image.paste(filler, (x1, y1), filler)

    def _draw_lpl_msi_next_label(self, image, x, y, width, height, start):
        date_label = start.strftime("%m/%d") if isinstance(start, datetime) else "TBD"
        label = f"MSI NEXT {date_label}"
        draw = ImageDraw.Draw(image)
        inset_x = max(18, int(width * 0.10))
        left = int(x + inset_x)
        right = int(x + width - inset_x - 1)
        top = int(y + max(13, height * 0.32))
        bottom = int(min(y + height - 4, top + max(22, int(height * 0.50))))
        if right - left < 80 or bottom - top < 14:
            return
        draw.rounded_rectangle((left, top, right, bottom), radius=3, fill=(5, 13, 26), outline=(221, 173, 82), width=1)
        draw.line((left + 5, top + 1, right - 5, top + 1), fill=(255, 227, 128), width=1)
        fitted, font = self._fit_text(draw, label, right - left - 8, 11, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (left + 2, top, right - 2, bottom), fitted, font, (255, 239, 181))

    def _draw_lpl_next_row(self, image, draw, right_x, right_w, y, event, now, league_key="LPL"):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        draw.rounded_rectangle(
            (row_x1, y, row_x2, y + 44),
            radius=6,
            fill=COLORS["panel"],
            outline=COLORS["border"],
            width=1,
        )
        draw.rectangle((row_x1 + 1, y + 1, row_x1 + 5, y + 43), fill=self._lol_sidebar_color(league_key, "accent"))
        date_text, date_font = self._fit_text(draw, event["start"].strftime("%m/%d"), 44, 11, bold=True, min_size=8)
        draw.text((row_x1 + 12, y + 1), date_text, font=date_font, fill=COLORS["muted"])
        time_text, time_font = self._fit_text(draw, self._format_time(event["start"]), 76, 12, bold=True, min_size=9)
        self._draw_centered(draw, (right_x + right_w / 2, y + 7), time_text, time_font, COLORS["text"])

        logo_size = 19
        center_x = right_x + right_w / 2
        team_top = y + 16
        team_bottom = y + 32
        logo_y = int(team_top + (team_bottom - team_top - logo_size) / 2)
        left_logo_x = row_x1 + 12
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, logo_y, logo_size, event["team_a"])
        team_a_label = self._lpl_display_team_from_event(event, "a", league_key=league_key)
        team_a, font_a = self._fit_text(draw, team_a_label, 45, 13, bold=True, min_size=8)
        self._draw_text_in_box(draw, (row_x1 + 36, team_top, center_x - 16, team_bottom), team_a, font_a, COLORS["text"])
        self._draw_centered_in_box(draw, (center_x - 13, team_top, center_x + 13, team_bottom), "VS", self._font(10, True), COLORS["muted"])
        logo_x = row_x2 - 12 - logo_size
        self._draw_team_logo(image, draw, event.get("team_b_logo"), logo_x, logo_y, logo_size, event["team_b"])
        team_b_label = self._lpl_display_team_from_event(event, "b", league_key=league_key)
        team_b, font_b = self._fit_text(draw, team_b_label, 45, 13, bold=True, min_size=8)
        self._draw_text_in_box(draw, (center_x + 16, team_top, logo_x - 5, team_bottom), team_b, font_b, COLORS["text"], align="right")
        odds = event.get("odds") or {}
        if odds.get("team_a") and odds.get("team_b"):
            self._draw_lpl_odds_text(draw, (row_x1 + 36, y + 31, center_x - 16, y + 43), odds.get("team_a"), max_size=9, align="left")
            self._draw_lpl_odds_text(draw, (center_x + 16, y + 31, logo_x - 5, y + 43), odds.get("team_b"), max_size=9, align="right")

    def _draw_lpl_recent_rows(self, image, draw, right_x, right_w, y, events, league_key="LPL"):
        self._draw_section_header(draw, right_x, right_w, y, "RECENT", self._lol_sidebar_color(league_key, "accent"))
        if not events:
            draw.text((right_x + 18, y + 42), "No recent results", font=self._font(16, True), fill=COLORS["text"])
            return
        row_y = y + 28
        for index, event in enumerate(events[:2]):
            self._draw_lpl_recent_result_row(image, draw, right_x, right_w, row_y + index * 40, event, league_key=league_key)

    def _draw_lpl_recent_result_row(self, image, draw, right_x, right_w, y, event, league_key="LPL"):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        draw.line((row_x1, y - 6, row_x2, y - 6), fill=COLORS["line"], width=1)
        row_h = 30
        draw.text((row_x1 + 2, y + 8), event["start"].strftime("%m/%d"), font=self._font(11, True), fill=COLORS["text"])
        logo_size = 16
        score_w = 34
        match_x1 = row_x1 + 50
        score_x = int((match_x1 + row_x2) / 2 - score_w / 2)
        left_logo_x = match_x1
        left_text_x = left_logo_x + logo_size + 5
        left_text_w = max(22, score_x - left_text_x - 6)
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, y + 7, logo_size, event["team_a"])
        team_a_label = self._lpl_display_team_from_event(event, "a", league_key=league_key)
        team_a, font_a = self._fit_text(draw, team_a_label, left_text_w, 12, bold=True, min_size=8)
        self._draw_text_in_box(draw, (left_text_x, y, score_x - 6, y + row_h), team_a, font_a, COLORS["text"])
        score = self._score_label(event)
        score_text, score_font = self._fit_text(draw, score, score_w, 12, bold=True, min_size=9)
        self._draw_centered_in_box(draw, (score_x, y, score_x + score_w, y + row_h), score_text, score_font, COLORS["text"])
        right_logo_x = row_x2 - logo_size
        right_text_x2 = right_logo_x - 5
        right_text_x1 = score_x + score_w + 6
        right_text_w = max(22, right_text_x2 - right_text_x1)
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, y + 7, logo_size, event["team_b"])
        team_b_label = self._lpl_display_team_from_event(event, "b", league_key=league_key)
        team_b, font_b = self._fit_text(draw, team_b_label, right_text_w, 12, bold=True, min_size=8)
        self._draw_text_in_box(draw, (right_text_x1, y, right_text_x2, y + row_h), team_b, font_b, COLORS["text"], align="right")

    @staticmethod
    def _lpl_display_team_name(value):
        text = str(value or "").strip()
        if not text:
            return "TBD"
        code = text.upper()
        if code in LPL_TEAM_ZH_NAMES:
            return LPL_TEAM_ZH_NAMES[code]
        normalized = SportsDashboard._normalize_odds_team_name(text)
        for team_code, aliases in LPL_ODDS_TEAM_ALIASES.items():
            normalized_aliases = {
                SportsDashboard._normalize_odds_team_name(alias)
                for alias in (team_code, *aliases)
                if SportsDashboard._normalize_odds_team_name(alias)
            }
            if normalized in normalized_aliases:
                return LPL_TEAM_ZH_NAMES.get(team_code, team_code)
        return text

    @staticmethod
    def _lpl_display_team_from_event(event, side, league_key="LPL"):
        key = "team_a" if side == "a" else "team_b"
        value = (event or {}).get(key)
        if str(league_key or "LPL").strip().upper() == "LPL":
            return SportsDashboard._lpl_display_team_name(value)
        text = str(value or "").strip()
        return text or "TBD"

    @staticmethod
    def _lpl_stage_label(event, league_key="LPL"):
        event = event or {}
        for key in ("stage_label", "round_label", "stage", "round", "phase", "block"):
            value = event.get(key)
            label = SportsDashboard._canonical_lpl_stage_label(value)
            if label:
                return label
        for key in ("stage_label", "round_label", "stage", "round", "phase", "block"):
            value = event.get(key)
            if value:
                return SportsDashboard._format_lpl_stage_label(value)
        return SportsDashboard._lol_sidebar_config(league_key)["key"]

    @staticmethod
    def _score_label(event):
        if event.get("wins_a") is None or event.get("wins_b") is None:
            return "vs"
        return f"{event['wins_a']}-{event['wins_b']}"

    def _draw_lpl_main_card(self, draw, right_x, right_w, y, event, now, is_live, league_key="LPL"):
        draw.rounded_rectangle(
            (right_x + 12, y, right_x + right_w - 12, y + 130),
            radius=6,
            fill=COLORS["panel2"],
            outline=COLORS["border"],
            width=1,
        )
        if not event:
            draw.text((right_x + 24, y + 42), "No LPL data", font=self._font(20, True), fill=COLORS["text"])
            return

        day_text = self._day_text(event["start"], now)
        day_text, day_font = self._fit_text(draw, day_text, right_w - 126, 17, bold=True, min_size=12)
        draw.text((right_x + 24, y + 14), day_text, font=day_font, fill=COLORS["amber"])
        self._draw_right_aligned(
            draw,
            (right_x + right_w - 25, y + 14),
            self._format_time(event["start"]),
            self._font(17, True),
            COLORS["text"],
        )

        if is_live and event.get("wins_a") is not None and event.get("wins_b") is not None:
            center = f"{event['wins_a']}-{event['wins_b']}"
        else:
            center = "vs"
        team_col_w = max(64, int((right_w - 78) / 2))
        team_a_label = self._lpl_display_team_from_event(event, "a", league_key=league_key)
        team_b_label = self._lpl_display_team_from_event(event, "b", league_key=league_key)
        team_a, font_a = self._fit_text(draw, team_a_label, team_col_w, 31, bold=True, min_size=18)
        team_b, font_b = self._fit_text(draw, team_b_label, team_col_w, 31, bold=True, min_size=18)
        center_x = right_x + right_w / 2
        draw.text((right_x + 25, y + 49), team_a, font=font_a, fill=COLORS["text"])
        self._draw_centered(draw, (center_x, y + 66), center, self._font(15, True), COLORS["muted"])
        self._draw_right_aligned(draw, (right_x + right_w - 25, y + 49), team_b, font_b, COLORS["text"])

        block = self._lpl_stage_label(event)[:18]
        draw.text((right_x + 25, y + 100), block, font=self._font(14), fill=COLORS["lpl_accent"])

    def _draw_lpl_upcoming(self, draw, right_x, right_w, y, events):
        self._draw_section_header(draw, right_x, right_w, y, "UPCOMING", COLORS["lpl_accent"])
        for index, event in enumerate(events):
            row_y = y + 34 + index * 42
            self._draw_schedule_row(draw, right_x, right_w, row_y, event)

    def _draw_lpl_recent(self, draw, right_x, right_w, y, events, league_key="LPL"):
        self._draw_section_header(draw, right_x, right_w, y, "RECENT", self._lol_sidebar_color(league_key, "accent"))
        for index, event in enumerate(events):
            row_y = y + 32 + index * 32
            draw.line((right_x + 14, row_y - 7, right_x + right_w - 14, row_y - 7), fill=COLORS["line"], width=1)
            draw.text((right_x + 16, row_y), event["start"].strftime("%m/%d"), font=self._font(14), fill=COLORS["muted"])
            label, label_font = self._fit_text(draw, self._result_label(event), right_w - 104, 17, bold=True, min_size=12)
            draw.text((right_x + 82, row_y - 1), label, font=label_font, fill=COLORS["text"])

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
        logo = self._load_local_team_logo(fallback_text, draw_size) or self._load_team_logo(logo_url, draw_size)
        if logo:
            image.paste(logo, (draw_x + (draw_size - logo.width) // 2, draw_y + (draw_size - logo.height) // 2), logo)
            return
        draw.rounded_rectangle((draw_x, draw_y, draw_x + draw_size, draw_y + draw_size), radius=4, fill=COLORS["panel_gold"], outline=COLORS["border"], width=1)
        fallback = str(fallback_text or "?")[:1].upper()
        fallback_font = self._font(max(10, int(draw_size * 0.55)), True)
        self._draw_centered(draw, (draw_x + draw_size / 2, draw_y + draw_size / 2), fallback, fallback_font, COLORS["muted"])

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
        )
        response.raise_for_status()
        return response.content

    @staticmethod
    def _load_team_logo(logo_url, size):
        if not logo_url:
            return None
        cache_key = (logo_url, size)
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        try:
            data = SportsDashboard._fetch_remote_image_bytes(logo_url, TEAM_LOGO_FETCH_TIMEOUT_SECONDS)
            with Image.open(BytesIO(data)) as source:
                logo = SportsDashboard._logo_with_transparent_background(source)
                bbox = logo.getbbox()
                if bbox:
                    logo = logo.crop(bbox)
                logo = ImageOps.contain(logo, (size, size), Image.LANCZOS)
            TEAM_LOGO_CACHE[cache_key] = logo
            return logo
        except Exception as exc:
            logger.warning("Failed to load team logo %s: %s", logo_url, _safe_exception_text(exc))
            TEAM_LOGO_CACHE[cache_key] = None
            return None

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
    def _format_time(match_time):
        return match_time.strftime("%I:%M %p").lstrip("0")

    @staticmethod
    def _format_time_24h(match_time):
        return match_time.strftime("%H:%M")

    @staticmethod
    def _source_label(source_state):
        return {
            "LIVE DATA": "LIVE DATA",
            "CACHE DATA": "CACHE DATA",
        }.get(str(source_state or "").upper(), str(source_state or "DATA"))

    @staticmethod
    def _font(size, bold=False):
        candidates = []
        yahei_regular_fonts = [
            resolve_path(os.path.join("plugins", "sports_dashboard", "fonts", "msyh.ttc")),
            resolve_path(os.path.join("plugins", "sports_dashboard", "fonts", "msyhl.ttc")),
            resolve_path(os.path.join("plugins", "sports_dashboard", "fonts", "msyhbd.ttc")),
        ]
        yahei_bold_fonts = [
            resolve_path(os.path.join("plugins", "sports_dashboard", "fonts", "msyhbd.ttc")),
            resolve_path(os.path.join("plugins", "sports_dashboard", "fonts", "msyh.ttc")),
            resolve_path(os.path.join("plugins", "sports_dashboard", "fonts", "msyhl.ttc")),
        ]
        bundled_fallback_fonts = [
            resolve_path(os.path.join("static", "fonts", "LXGWWenKai-Regular.ttf")),
            resolve_path(os.path.join("plugins", "chinese_literature_clock", "fonts", "FandolKai-Regular.otf")),
            resolve_path(os.path.join("plugins", "chinese_literature_clock", "fonts", "I.Ming-8.10.ttf")),
        ]
        if bold:
            candidates.extend(
                [
                    *yahei_bold_fonts,
                    r"C:\Windows\Fonts\msyhbd.ttc",
                    *bundled_fallback_fonts,
                    r"C:\Windows\Fonts\arialbd.ttf",
                    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                ]
            )
        candidates.extend(
            [
                *yahei_regular_fonts,
                r"C:\Windows\Fonts\msyh.ttc",
                *bundled_fallback_fonts,
                r"C:\Windows\Fonts\arial.ttf",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ]
        )
        for path in candidates:
            if os.path.exists(path):
                return ImageFont.truetype(path, size=size)
        return ImageFont.load_default()

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
