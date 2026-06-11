from collections.abc import Mapping
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
import urllib.request

from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import resolve_path
from utils.http_client import get_http_session
from utils.image_utils import take_screenshot

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
WORLD_CUP_ODDS_STATE_VERSION = "sports-dashboard-worldcup-odds-v1"
WORLD_CUP_LIVE_STATE_VERSION = "sports-dashboard-worldcup-live-v1"
WORLD_CUP_LINEUP_STATE_VERSION = "sports-dashboard-worldcup-lineups-v1"
LPL_ODDS_STATE_VERSION = "sports-dashboard-lpl-odds-v1"
LPL_LIVE_STATE_VERSION = "sports-dashboard-lpl-live-v1"
NBA_SCOREBOARD_STATE_VERSION = "sports-dashboard-nba-scoreboard-v1"
NBA_LIVE_STATE_VERSION = "sports-dashboard-nba-live-v1"
NBA_ODDS_STATE_VERSION = "sports-dashboard-nba-odds-v1"
THE_ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
ODDS_API_IO_BASE_URL = "https://api.odds-api.io/v3"
DEFAULT_NBA_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
DEFAULT_NBA_CACHE_HOURS = 1
DEFAULT_NBA_DAILY_LIMIT = 96
DEFAULT_NBA_LOOKBACK_DAYS = 10
DEFAULT_NBA_LOOKAHEAD_DAYS = 21
DEFAULT_NBA_LIVE_REFRESH_SECONDS = 180
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
DEFAULT_WORLD_CUP_LIVE_REFRESH_SECONDS = 180
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
LPL_LIVE_STATES = {"inprogress", "in_progress", "in-progress", "live"}
LPL_INFERRED_LIVE_WINDOW = timedelta(hours=6)
LPL_LIVE_PREGAME_WINDOW = timedelta(minutes=30)
LPL_LIVE_STATS_MAX_FRAME_AGE = timedelta(minutes=10)
FLAGS_API_URL_TEMPLATE = "https://flagsapi.com/{country_code}/flat/64.png"
DEFAULT_LPL_LEAGUE_ID = "98767991314006698"
DEFAULT_TIMEZONE = "America/Los_Angeles"
ODDS_API_IO_LEAGUE_ALIASES = {
    "international-world-cup": DEFAULT_WORLD_CUP_ODDS_API_IO_LEAGUE,
    "usa-nba": DEFAULT_NBA_ODDS_API_IO_LEAGUE,
    "league-of-legends-lpl": DEFAULT_LPL_ODDS_API_IO_LEAGUE,
}
LPL_SEPARATOR_WIDTH = 4
MIN_LPL_SIDEBAR_WIDTH = 240
LOCAL_TEAM_LOGO_DIR = resolve_path(os.path.join("plugins", "sports_dashboard", "assets", "logos"))
LOCAL_DECOR_DIR = resolve_path(os.path.join("plugins", "sports_dashboard", "assets", "decor"))
LOCAL_LPL_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "lpl.png")
LOCAL_WORLDCUP_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "worldcup.png")
LOCAL_NBA_LOGO_PATH = os.path.join(LOCAL_TEAM_LOGO_DIR, "nba.png")
LOCAL_WORLDCUP_PITCH_STRIP_PATH = os.path.join(LOCAL_DECOR_DIR, "worldcup_pitch_strip.png")
LOCAL_WORLDCUP_HEADER_BANNER_PATH = os.path.join(LOCAL_DECOR_DIR, "worldcup_header_banner.png")
LOCAL_NBA_COURT_STRIP_PATH = os.path.join(LOCAL_DECOR_DIR, "nba_court_strip.png")
LOCAL_NBA_EMPTY_SLOT_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "nba_empty_slot_filler.png")
LOCAL_LPL_MARBLE_FILLER_PATH = os.path.join(LOCAL_DECOR_DIR, "lpl_marble_filler.png")
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
    "worldcup_accent": (0, 152, 82),
    "worldcup_live": (222, 45, 38),
    "worldcup_tag": (218, 244, 215),
    "worldcup_shadow": (0, 163, 173),
    "nba_accent": (0, 92, 185),
    "nba_live": (222, 45, 38),
    "nba_tag": (222, 238, 255),
    "nba_shadow": (222, 45, 38),
    "lpl_accent": (222, 45, 38),
    "lpl_live": (222, 45, 38),
    "lpl_tag": (255, 226, 220),
    "lpl_shadow": (255, 196, 30),
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
    "worldcup_accent": (82, 202, 128),
    "worldcup_live": (255, 82, 74),
    "worldcup_tag": (28, 70, 48),
    "worldcup_shadow": (36, 124, 102),
    "nba_accent": (93, 169, 232),
    "nba_live": (255, 82, 74),
    "nba_tag": (21, 47, 82),
    "nba_shadow": (126, 54, 76),
    "lpl_accent": (255, 82, 74),
    "lpl_live": (255, 82, 74),
    "lpl_tag": (82, 34, 29),
    "lpl_shadow": (130, 76, 26),
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
    "COL": "哥伦比亚",
    "CRC": "哥斯达黎加",
    "CRO": "克罗地亚",
    "CZE": "捷克",
    "DEN": "丹麦",
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
    "NOR": "挪威",
    "NZL": "新西兰",
    "PAN": "巴拿马",
    "PAR": "巴拉圭",
    "PER": "秘鲁",
    "POL": "波兰",
    "POR": "葡萄牙",
    "QAT": "卡塔尔",
    "ROU": "罗马尼亚",
    "RSA": "南非",
    "SCO": "苏格兰",
    "SEN": "塞内加尔",
    "SRB": "塞尔维亚",
    "SUI": "瑞士",
    "SVK": "斯洛伐克",
    "SVN": "斯洛文尼亚",
    "SWE": "瑞典",
    "TUN": "突尼斯",
    "TUR": "土耳其",
    "UAE": "阿联酋",
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
    "COL": "CO",
    "CRC": "CR",
    "CRO": "HR",
    "CZE": "CZ",
    "DEN": "DK",
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
    "NOR": "NO",
    "NZL": "NZ",
    "PAN": "PA",
    "PAR": "PY",
    "PER": "PE",
    "POL": "PL",
    "POR": "PT",
    "QAT": "QA",
    "ROU": "RO",
    "RSA": "ZA",
    "SCO": "GB",
    "SEN": "SN",
    "SRB": "RS",
    "SUI": "CH",
    "SVK": "SK",
    "SVN": "SI",
    "SWE": "SE",
    "TUN": "TN",
    "TUR": "TR",
    "UAE": "AE",
    "UKR": "UA",
    "URU": "UY",
    "URY": "UY",
    "USA": "US",
    "UZB": "UZ",
    "VEN": "VE",
    "WAL": "GB",
    "ZAM": "ZM",
}


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
            left = self._render_worldcup_fallback((left_width, worldcup_height), visible_worldcup_matches)
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
        self._draw_nba_compact_panel(
            image,
            draw,
            (0, nba_top, left_width - 1, nba_top + nba_height - 1),
            nba_selected,
            nba_source_state,
            now,
        )

        lpl_events, lpl_source_state = self._load_lpl_events(settings, timezone_info)
        lpl_events = self._attach_lpl_odds(lpl_events, settings, device_config, timezone_info)
        lpl_selected = self._select_lpl_events(lpl_events, now)
        self._attach_lpl_realtime_info(lpl_selected, settings)
        self._write_lpl_live_state(lpl_selected, now, lpl_source_state)
        self._draw_lpl_sidebar(image, left_width, lpl_selected, lpl_source_state, now)
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
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
        return tuple(dimensions)

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

    def _load_nba_events(self, settings, timezone_info):
        try:
            payload, source_state, _fetched_at = self._load_nba_scoreboard(settings, timezone_info)
            events = self._parse_nba_espn_events(payload, timezone_info)
            if events:
                return events, source_state
        except Exception as exc:
            logger.warning("NBA scoreboard fetch failed: %s", exc)
        return self._fallback_nba_events(timezone_info), "NBA FALLBACK"

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
            team_a, team_a_name, team_a_code, team_a_logo, wins_a, periods_a = SportsDashboard._nba_team_info(away, show_score)
            team_b, team_b_name, team_b_code, team_b_logo, wins_b, periods_b = SportsDashboard._nba_team_info(home, show_score)
            series_wins_a, series_wins_b = SportsDashboard._nba_series_wins_by_side(event, competition, away, home)
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
                    "series_wins_a": series_wins_a,
                    "series_wins_b": series_wins_b,
                    "period_scores_a": periods_a,
                    "period_scores_b": periods_b,
                    "status_text": SportsDashboard._nba_status_text(event, competition, start_time),
                    "period": SportsDashboard._lpl_int_value((competition.get("status") or {}).get("period")),
                    "block": SportsDashboard._nba_event_block(event, competition),
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
        code = str(
            team.get("abbreviation")
            or team.get("shortDisplayName")
            or team.get("name")
            or "TBD"
        ).strip().upper() or "TBD"
        name = str(team.get("shortDisplayName") or team.get("displayName") or code).strip() or code
        display_name = SportsDashboard._nba_display_team_name(code, name)
        logo = str(team.get("logo") or "").strip()
        if not logo:
            logos = team.get("logos") or []
            if logos:
                logo = str((logos[0] or {}).get("href") or "").strip()
        score = SportsDashboard._lpl_int_value((competitor or {}).get("score")) if show_score else None
        periods = SportsDashboard._nba_period_scores(competitor) if show_score else []
        return display_name, name, code, logo, score, periods

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
    def _nba_display_team_name(code, fallback):
        normalized = str(code or "").strip().upper()
        return NBA_TEAM_ZH_NAMES.get(normalized, str(fallback or normalized or "TBD").strip() or "TBD")

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
                "2026-06-05T00:30:00+00:00",
                "completed",
                "NY",
                "SA",
                112,
                106,
                2,
                0,
                [28, 27, 31, 26],
                [25, 29, 24, 28],
                "Final",
                "POSTSEASON",
            ),
            (
                "2026-06-09T00:30:00+00:00",
                "unstarted",
                "SA",
                "NY",
                None,
                None,
                0,
                2,
                [],
                [],
                "5:30 PM",
                "POSTSEASON",
            ),
            (
                "2026-06-10T00:30:00+00:00",
                "unstarted",
                "NY",
                "SA",
                None,
                None,
                2,
                0,
                [],
                [],
                "5:30 PM",
                "POSTSEASON",
            ),
            (
                "2026-06-11T00:30:00+00:00",
                "unstarted",
                "SA",
                "NY",
                None,
                None,
                0,
                2,
                [],
                [],
                "5:30 PM",
                "POSTSEASON",
            ),
            (
                "2026-06-12T00:30:00+00:00",
                "unstarted",
                "NY",
                "SA",
                None,
                None,
                2,
                0,
                [],
                [],
                "5:30 PM",
                "POSTSEASON",
            ),
            (
                "2026-06-13T00:30:00+00:00",
                "unstarted",
                "SA",
                "NY",
                None,
                None,
                0,
                2,
                [],
                [],
                "5:30 PM",
                "POSTSEASON",
            ),
            (
                "2026-06-03T00:30:00+00:00",
                "completed",
                "SA",
                "NY",
                101,
                109,
                0,
                1,
                [22, 25, 27, 27],
                [26, 24, 30, 29],
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
        return {
            "live": live,
            "upcoming": upcoming,
            "recent": recent,
            "main": main,
        }

    @staticmethod
    def _is_nba_live_event(event):
        return str((event or {}).get("state") or "").strip().lower() in NBA_LIVE_STATES

    @staticmethod
    def _is_nba_finished_event(event):
        return str((event or {}).get("state") or "").strip().lower() in NBA_FINISHED_STATES

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
        lookahead = SportsDashboard._int_setting(settings, "nbaLookaheadDays", DEFAULT_NBA_LOOKAHEAD_DAYS, 1, 60)
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
        league_id = str(settings.get("lplLeagueId") or DEFAULT_LPL_LEAGUE_ID).strip()
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
        if self._bool_setting(settings, "lplLiveEndpointEnabled", True) and self._should_poll_lpl_live_endpoint(events, now):
            try:
                live_events = self._fetch_lpl_live_events(settings, timezone_info)
                events = self._merge_lpl_live_events(events, live_events, league_id)
            except Exception as exc:
                logger.warning("LPL live endpoint fetch failed: %s", exc)
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
                    "league_id": str((event.get("league") or {}).get("id") or "").strip(),
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
        return {
            "live": live,
            "upcoming": upcoming,
            "recent": recent,
            "main": main,
        }

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

    def _write_lpl_live_state(self, selected, now, source_state):
        live_events = (selected or {}).get("live") or []
        event = live_events[0] if live_events else None
        payload = {
            "version": LPL_LIVE_STATE_VERSION,
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
            self._write_json_file(self._lpl_live_state_path(), payload)
        except OSError as exc:
            logger.warning("Failed to write LPL live refresh state: %s", exc)

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
            payload.update(
                {
                    "live_until": live_until.astimezone(timezone.utc).isoformat(),
                    "event_id": event.get("event_id") or "",
                    "team_a": event.get("team_a") or "",
                    "team_b": event.get("team_b") or "",
                    "score": self._worldcup_score_or_vs(event),
                    "state": event.get("state") or "",
                    "status": event.get("status") or "",
                    "elapsed": event.get("elapsed"),
                    "started_at": start.astimezone(timezone.utc).isoformat() if isinstance(start, datetime) else None,
                }
            )
        try:
            self._write_json_file(self._worldcup_live_state_path(), payload)
        except OSError as exc:
            logger.warning("Failed to write World Cup live refresh state: %s", exc)

    def _attach_lpl_realtime_info(self, selected, settings):
        if not self._bool_setting(settings, "lplLiveStatsEnabled", True):
            return selected
        live_events = (selected or {}).get("live") or []
        event = live_events[0] if live_events else None
        if not event:
            return selected
        try:
            little_round = self._fetch_lpl_realtime_info(event, settings)
        except Exception as exc:
            logger.warning("LPL live stats fetch failed: %s", exc)
            return selected
        if little_round:
            event["little_round"] = little_round
        return selected

    def _fetch_lpl_realtime_info(self, event, settings=None):
        settings = settings or {}
        try:
            little_round = self._fetch_lpl_riot_realtime_info(event)
        except Exception as exc:
            logger.debug("Riot LPL live stats failed before bo3.gg fallback: %s", exc)
            little_round = None
        if little_round:
            return little_round
        if self._bool_setting(settings, "lplBo3LiveApiEnabled", True):
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

    def _nba_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "nba_live_state.json"

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
            events = self._attach_worldcup_odds(events, settings, device_config, timezone_info)
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

    def _try_worldcup_api_panel(self, settings, device_config, dimensions, timezone_info, visible_matches, now):
        api_key = self._api_sports_key(settings, device_config)
        if not api_key:
            return None
        try:
            fixtures, source_state, fetched_at = self._load_worldcup_api_fixtures(settings, api_key, timezone_info)
            events = self._parse_worldcup_api_events(fixtures, timezone_info)
            events = self._attach_worldcup_odds(events, settings, device_config, timezone_info)
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
        season = str(settings.get("worldCupApiSeason") or DEFAULT_WORLD_CUP_SEASON).strip()
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
        tla = str(team.get("tla") or team.get("code") or "").strip().upper()
        if tla:
            return tla
        short_name = str(team.get("shortName") or "").strip().upper()
        return short_name if len(short_name) <= 3 else ""

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
        mapped = FIFA_TLA_TO_ZH_NAME.get(str(tla or "").upper())
        if mapped:
            return mapped
        fallback = str(team.get("shortName") or team.get("name") or tla or "TBD").strip()
        return fallback or "TBD"

    @staticmethod
    def _flag_url_for_tla(tla):
        country_code = FIFA_TLA_TO_FLAGS_API_CODE.get(str(tla or "").upper())
        if not country_code:
            return ""
        return FLAGS_API_URL_TEMPLATE.format(country_code=country_code)

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
            home_tla = str(home.get("code") or "").strip().upper()
            away_tla = str(away.get("code") or "").strip().upper()
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
    def _api_team_name(team):
        return str(team.get("code") or team.get("name") or "TBD").strip() or "TBD"

    @staticmethod
    def _api_team_aliases(team):
        aliases = []
        for value in (team.get("name"), team.get("code")):
            text = str(value or "").strip()
            if text and text not in aliases:
                aliases.append(text)
        return aliases

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
        live = [event for event in events if SportsDashboard._is_worldcup_live_event(event)]
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
    def _is_worldcup_live_event(event):
        return str((event or {}).get("state") or "").strip().upper() in WORLD_CUP_LIVE_STATES

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
        title, title_font = self._fit_text(draw, "2026 World Cup", 178, 20, bold=True, min_size=15)
        draw.text((x1 + 52, header_y + 1), title, font=title_font, fill=COLORS["text"])
        source = self._worldcup_api_source_label(source_state, fetched_at)
        source_text, source_font = self._fit_text(draw, source, 140, 9, bold=True, min_size=7)
        draw.text((x1 + 52, header_y + 24), source_text, font=source_font, fill=COLORS["muted"])
        self._draw_worldcup_header_banner(image, x1 + 229, header_y, x2 - 94, y1 + 47)

        live = selected.get("live") or []
        upcoming = selected.get("upcoming") or []
        recent = selected.get("recent") or []
        main_event = selected.get("main")
        is_live = bool(live and main_event is live[0])
        self._draw_status_pill(draw, x2 - 84, header_y + 4, "LIVE" if is_live else "NEXT", is_live)
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

        self._draw_worldcup_main_card(image, draw, left_x1, content_y, left_x2, content_bottom, main_event, now, is_live)

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
            recent_y = upcoming_y + 20 + len(upcoming_rows) * 35 + 6
            if recent_y + 54 <= content_bottom:
                self._draw_worldcup_mini_rows(
                    image,
                    draw,
                    right_x1,
                    right_x2,
                    recent_y,
                    content_bottom,
                    "RECENT",
                    recent_rows,
                    show_time=False,
                )
        else:
            self._draw_worldcup_tactics_strip(image, draw, right_x1, right_x2, upcoming_used_bottom + 2, content_bottom, main_event)

    def _draw_worldcup_main_card(self, image, draw, x1, y1, x2, y2, event, now, is_live):
        accent = COLORS["worldcup_live"] if is_live else COLORS["worldcup_accent"]
        draw.rounded_rectangle((x1 + 3, y1 + 3, x2 + 3, y2 + 3), radius=5, fill=COLORS["worldcup_shadow"])
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, y2 - 1), fill=accent)
        if not event:
            message, message_font = self._fit_text(draw, "No World Cup schedule", x2 - x1 - 36, 15, bold=True, min_size=10)
            self._draw_centered(draw, ((x1 + x2) / 2, (y1 + y2) / 2), message, message_font, COLORS["text"])
            return

        tag = "NOW PLAYING" if is_live else "NEXT MATCH"
        tag_w = 104 if is_live else 88
        tag_text, tag_font = self._fit_text(draw, tag, tag_w - 8, 11, bold=True, min_size=7)
        tag_fill = COLORS["worldcup_live"] if is_live else COLORS["worldcup_tag"]
        draw.rectangle((x1 + 14, y1 + 9, x1 + 14 + tag_w, y1 + 27), fill=tag_fill, outline=COLORS["border"], width=1)
        draw.text((x1 + 18, y1 + 10), tag_text, font=tag_font, fill=COLORS["text"])
        date_text = event["start"].strftime("%m/%d")
        date_text, date_font = self._fit_text(draw, date_text, 52, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 11), date_text, date_font, COLORS["muted"])

        stage = self._clean_worldcup_stage(event.get("block"))
        stage_text, stage_font = self._fit_text(draw, stage, x2 - x1 - 34, 8, bold=True, min_size=6)
        draw.text((x1 + 18, y1 + 30), stage_text, font=stage_font, fill=COLORS["worldcup_accent"])

        status_text = self._worldcup_event_status_label(event, now)
        status_text, status_font = self._fit_text(draw, status_text, x2 - x1 - 42, 16, bold=True, min_size=10)
        self._draw_centered(draw, ((x1 + x2) / 2, y1 + 45), status_text, status_font, COLORS["text"])

        center_x = (x1 + x2) / 2
        flag_w, flag_h = 44, 30
        left_area = (x1 + 18, center_x - 21)
        right_area = (center_x + 21, x2 - 18)
        flag_y = y1 + 58
        left_flag_x = int((left_area[0] + left_area[1] - flag_w) / 2)
        right_flag_x = int((right_area[0] + right_area[1] - flag_w) / 2)
        self._draw_worldcup_flag(image, draw, event.get("team_a_flag"), left_flag_x, flag_y, flag_w, flag_h, event.get("team_a_tla"))
        self._draw_worldcup_flag(image, draw, event.get("team_b_flag"), right_flag_x, flag_y, flag_w, flag_h, event.get("team_b_tla"))
        center_label = self._worldcup_score_or_vs(event)
        center_label, center_font = self._fit_text(draw, center_label, 64, 17, bold=True, min_size=11)
        self._draw_centered(draw, (center_x, flag_y + flag_h / 2 + 1), center_label, center_font, COLORS["text"])

        team_y = flag_y + flag_h + 16
        team_a, team_a_font = self._fit_text(draw, event.get("team_a"), left_area[1] - left_area[0], 16, bold=True, min_size=9)
        team_b, team_b_font = self._fit_text(draw, event.get("team_b"), right_area[1] - right_area[0], 16, bold=True, min_size=9)
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, team_a_font, COLORS["text"])
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, team_b_font, COLORS["text"])

        points_y = team_y + 13
        self._draw_worldcup_points_text(draw, (left_area[0], points_y, left_area[1], points_y + 11), event, "a", max_size=8)
        self._draw_worldcup_points_text(draw, (right_area[0], points_y, right_area[1], points_y + 11), event, "b", max_size=8)

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
        flag_w, flag_h = 18, 12
        left_flag_x = x1 + 1
        right_flag_x = x2 - flag_w - 1
        has_odds = center_text == "VS" and self._worldcup_event_has_odds(event)
        team_bottom = y + 11
        flag_y = y - 1
        self._draw_worldcup_flag(image, draw, event.get("team_a_flag"), left_flag_x, flag_y, flag_w, flag_h, event.get("team_a_tla"))
        team_a, font_a = self._fit_text(draw, event.get("team_a"), max(24, center_x - left_flag_x - flag_w - 24), 10, bold=True, min_size=7)
        self._draw_text_in_box(draw, (left_flag_x + flag_w + 4, y - 1, center_x - 28, team_bottom), team_a, font_a, COLORS["text"])
        center_text, center_font = self._fit_text(draw, center_text, 52, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (center_x - 26, y - 1, center_x + 26, team_bottom), center_text, center_font, COLORS["text"])
        self._draw_worldcup_flag(image, draw, event.get("team_b_flag"), right_flag_x, flag_y, flag_w, flag_h, event.get("team_b_tla"))
        team_b, font_b = self._fit_text(draw, event.get("team_b"), max(24, right_flag_x - center_x - 31), 10, bold=True, min_size=7)
        self._draw_text_in_box(draw, (center_x + 28, y - 1, right_flag_x - 4, team_bottom), team_b, font_b, COLORS["text"], align="right")
        left_meta = self._worldcup_team_points_meta(event, "a", include_odds=has_odds)
        right_meta = self._worldcup_team_points_meta(event, "b", include_odds=has_odds)
        self._draw_worldcup_odds_text(draw, (left_flag_x + flag_w + 4, y + 11, center_x - 28, y + 20), left_meta, max_size=7)
        self._draw_worldcup_odds_text(draw, (center_x + 28, y + 11, right_flag_x - 4, y + 20), right_meta, max_size=7)
        if has_odds:
            odds = event.get("odds") or {}
            if odds.get("draw"):
                self._draw_worldcup_odds_text(draw, (center_x - 21, y + 11, center_x + 21, y + 20), f"X {odds.get('draw')}", max_size=6)

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
    def _worldcup_team_points_meta(event, side, include_odds=False):
        points = SportsDashboard._worldcup_group_points_label(event, side)
        if not include_odds:
            return points
        odds_key = "team_a" if side == "a" else "team_b"
        event_data = event if isinstance(event, Mapping) else {}
        odds_value = (event_data.get("odds") or {}).get(odds_key)
        if not odds_value:
            return points
        return f"{points}  {odds_value}"

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
        fitted, font = self._fit_text(draw, text, max(1, right - left), max_size, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (left, top, right, bottom), fitted, font, COLORS["text"])

    def _draw_worldcup_points_text(self, draw, box, event, side, max_size=8):
        text = self._worldcup_group_points_label(event, side)
        left, top, right, bottom = [int(value) for value in box]
        fitted, font = self._fit_text(draw, text, max(1, right - left), max_size, bold=True, min_size=6)
        fill = COLORS["green"] if self._worldcup_group_points_value(event, side) is not None else COLORS["muted"]
        self._draw_centered_in_box(draw, (left, top, right, bottom), fitted, font, fill)

    def _draw_worldcup_country(self, image, draw, flag_url, label, fallback_text, x, y, width, align, compact=False):
        flag_w, flag_h = (22, 15) if compact else (26, 19)
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

    def _draw_worldcup_flag(self, image, draw, flag_url, x, y, width, height, fallback_text):
        flag = self._load_flag_image(flag_url, (width, height))
        if flag:
            image.paste(flag, (x + (width - flag.width) // 2, y + (height - flag.height) // 2), flag)
            return
        draw.rectangle((x, y, x + width, y + height), fill=COLORS["panel"], outline=COLORS["border"], width=1)
        fallback = str(fallback_text or "?").strip().upper()[:2] or "?"
        fallback_text, fallback_font = self._fit_text(draw, fallback, width - 3, 9, bold=True, min_size=7)
        self._draw_centered(draw, (x + width / 2, y + height / 2), fallback_text, fallback_font, COLORS["muted"])

    @staticmethod
    def _load_flag_image(flag_url, size):
        if not flag_url:
            return None
        cache_key = (flag_url, size)
        if cache_key in FLAG_IMAGE_CACHE:
            return FLAG_IMAGE_CACHE[cache_key]
        try:
            request = urllib.request.Request(
                flag_url,
                headers={"User-Agent": "InkyPi/1.0"},
            )
            with urllib.request.urlopen(request, timeout=4) as response:
                data = response.read()
            with Image.open(BytesIO(data)) as source:
                flag = ImageOps.contain(source.convert("RGBA"), size, Image.LANCZOS)
            FLAG_IMAGE_CACHE[cache_key] = flag
            return flag
        except Exception as exc:
            logger.warning("Failed to load World Cup flag %s: %s", flag_url, exc)
            FLAG_IMAGE_CACHE[cache_key] = None
            return None

    @staticmethod
    def _worldcup_api_source_label(source_state, fetched_at):
        fetched = SportsDashboard._parse_cached_utc(fetched_at)
        time_text = fetched.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%I:%M %p").lstrip("0") if fetched else ""
        state = str(source_state or "API").upper()
        if state == "FOOTBALL LIVE":
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
    def _worldcup_status_color(event):
        state = str(event.get("state") or "").upper()
        if state in {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT", "IN_PLAY", "PAUSED"}:
            return COLORS["worldcup_live"]
        if state in {"FT", "AET", "PEN", "FINISHED", "AWARDED"}:
            return COLORS["green"]
        return COLORS["worldcup_accent"]

    @staticmethod
    def _worldcup_event_status_label(event, now):
        state = str(event.get("state") or "").upper()
        if state in {"FT", "AET", "PEN", "FINISHED", "AWARDED"} and event.get("wins_a") is not None and event.get("wins_b") is not None:
            return f"{event['wins_a']}-{event['wins_b']}"
        if state in {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT", "IN_PLAY", "PAUSED"}:
            score = SportsDashboard._score_label(event)
            if score != "vs":
                return f"LIVE {score}"
            return "LIVE"
        return SportsDashboard._format_time_24h(event["start"])

    @staticmethod
    def _worldcup_event_time_label(event):
        return SportsDashboard._format_time_24h(event["start"])

    def _render_worldcup_fallback(self, dimensions, visible_matches=DEFAULT_WORLD_CUP_VISIBLE_MATCHES):
        visible_matches = max(1, min(WORLD_CUP_VISIBLE_MATCH_LIMIT, int(visible_matches or DEFAULT_WORLD_CUP_VISIBLE_MATCHES)))
        timezone_info = ZoneInfo(DEFAULT_TIMEZONE)
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
        self._draw_status_pill(draw, x2 - 84, header_y + 4, "LIVE" if live else "NEXT", bool(live))
        draw.line((x1 + 12, y1 + 48, x2 - 12, y1 + 48), fill=COLORS["border"], width=1)

        content_y = y1 + 58
        content_bottom = y2 - 8
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
        team_a, font_a = self._fit_text(draw, event["team_a"], left_area[1] - left_area[0], 18, bold=True, min_size=10)
        team_b, font_b = self._fit_text(draw, event["team_b"], right_area[1] - right_area[0], 18, bold=True, min_size=10)
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, font_a, COLORS["text"])
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, font_b, COLORS["text"])
        has_odds = self._nba_event_has_moneyline_odds(event)
        if has_odds:
            self._draw_nba_odds_pair(draw, left_area, right_area, team_y + 14, event, max_size=10)
        if has_series_score:
            self._draw_nba_main_series_score(draw, left_area, right_area, center_x, team_y + (32 if has_odds else 21), event)

        block = str(event.get("block") or "NBA").upper()
        block_text, block_font = self._fit_text(draw, block, x2 - x1 - 88, 9, bold=True, min_size=7)
        draw.text((x1 + 15, y2 - 17), block_text, font=block_font, fill=COLORS["nba_accent"])
        period_label = self._nba_period_label(event, max_parts=2)
        if period_label:
            period_label, period_font = self._fit_text(draw, period_label, 102, 8, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 14, y2 - 17), period_label, period_font, COLORS["muted"])

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
        self._draw_text_in_box(draw, team_a_box, team_a, font_a, COLORS["text"])
        center_text, center_font = self._fit_text(draw, center_text, score_w, team_font_size, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (center_x - score_w / 2, y - 1, center_x + score_w / 2, team_bottom), center_text, center_font, COLORS["text"])
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, y, logo_size, event["team_b"])
        team_b, font_b = self._fit_text(draw, event["team_b"], max(24, team_b_box[2] - team_b_box[0]), team_font_size, bold=True, min_size=7)
        self._draw_text_in_box(draw, team_b_box, team_b, font_b, COLORS["text"], align="right")
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
        team_a, font_a = self._fit_text(draw, event["team_a"], left_area[1] - left_area[0], team_size, bold=True, min_size=9)
        team_b, font_b = self._fit_text(draw, event["team_b"], right_area[1] - right_area[0], team_size, bold=True, min_size=9)
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, font_a, COLORS["text"])
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, font_b, COLORS["text"])

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
        self._draw_text_in_box(draw, (left_logo_x + logo_size + 4, y - 1, center_x - 28, y + 16), team_a, font_a, COLORS["text"])
        center_text, center_font = self._fit_text(draw, center_text, 54, 11, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (center_x - 27, y - 1, center_x + 27, y + 16), center_text, center_font, COLORS["text"])
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, y, logo_size, event["team_b"])
        team_b, font_b = self._fit_text(draw, event["team_b"], max(24, right_logo_x - center_x - 31), 11, bold=True, min_size=7)
        self._draw_text_in_box(draw, (center_x + 28, y - 1, right_logo_x - 4, y + 16), team_b, font_b, COLORS["text"], align="right")

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
        team_a, font_a = self._fit_text(draw, event["team_a"], left_area[1] - left_area[0], 20, bold=True, min_size=12)
        team_b, font_b = self._fit_text(draw, event["team_b"], right_area[1] - right_area[0], 20, bold=True, min_size=12)
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, font_a, COLORS["text"])
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, font_b, COLORS["text"])

        small_score = self._nba_period_label(event, max_parts=4)
        if small_score:
            box_y1 = y1 + 130
            box_y2 = min(y2 - 34, box_y1 + 24)
            draw.rounded_rectangle((x1 + 18, box_y1, x2 - 18, box_y2), radius=5, fill=COLORS["panel_blue"], outline=COLORS["border"], width=1)
            label, label_font = self._fit_text(draw, "QTR", 28, 9, bold=True, min_size=7)
            draw.text((x1 + 25, box_y1 + 6), label, font=label_font, fill=COLORS["nba_accent"])
            small_text, small_font = self._fit_text(draw, small_score, x2 - x1 - 88, 12, bold=True, min_size=8)
            self._draw_text_in_box(draw, (x1 + 58, box_y1, x2 - 24, box_y2), small_text, small_font, COLORS["text"])

        block = str(event.get("block") or "NBA").upper()
        block_text, block_font = self._fit_text(draw, block, x2 - x1 - 42, 11, bold=True, min_size=8)
        draw.text((x1 + 18, y2 - 23), block_text, font=block_font, fill=COLORS["nba_accent"])

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
        self._draw_text_in_box(draw, (left_logo_x + logo_size + 5, y - 1, center_x - 25, y + 18), team_a, font_a, COLORS["text"])
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
        self._draw_text_in_box(draw, (center_x + 28, y - 1, right_logo_x - 5, y + 18), team_b, font_b, COLORS["text"], align="right")

    @staticmethod
    def _nba_score_label(event):
        score = SportsDashboard._score_label(event or {})
        return "VS" if score == "vs" else score

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

    def _draw_lpl_sidebar(self, image, left_width, selected, source_state, now):
        draw = ImageDraw.Draw(image)
        width, height = image.size
        right_x = left_width + LPL_SEPARATOR_WIDTH
        right_w = width - right_x
        draw.rectangle((left_width, 0, right_x - 1, height), fill=COLORS["paper"])
        draw.line((left_width, 0, left_width, height), fill=COLORS["border"], width=1)
        if LPL_SEPARATOR_WIDTH > 2:
            draw.line((left_width + 2, 0, left_width + 2, height), fill=COLORS["line"], width=1)
        draw.rectangle((right_x, 0, width - 1, height - 1), fill=COLORS["panel"])
        self._draw_halftone(draw, (right_x, 0, width - 1, height - 1), COLORS["lpl_shadow"], COLORS["panel"], 20, 1)
        draw.line((right_x, 0, right_x, height), fill=COLORS["border"], width=1)

        live = selected.get("live") or []
        upcoming = selected.get("upcoming") or []
        recent = selected.get("recent") or []
        main_event = live[0] if live else (upcoming[0] if upcoming else selected.get("main"))
        remaining_upcoming = [event for event in upcoming if event is not main_event][:2]

        header_y = 12

        self._draw_lpl_logo(image, draw, right_x + 13, header_y + 5, 74, 38)
        source_label = self._source_label(source_state)
        source_label, source_font = self._fit_text(draw, source_label, 62, 10, bold=True, min_size=8)
        self._draw_text_in_box(
            draw,
            (right_x + 90, header_y + 9, right_x + right_w - 92, header_y + 32),
            source_label,
            source_font,
            COLORS["muted"],
            align="center",
        )
        self._draw_status_pill(draw, right_x + right_w - 88, header_y + 8, "LIVE" if live else "NEXT", bool(live))
        draw.line((right_x + 14, 66, right_x + right_w - 14, 66), fill=COLORS["border"], width=1)

        self._draw_lpl_focus_card(image, draw, right_x, right_w, 78, main_event, now, bool(live))
        self._draw_lpl_next_rows(image, draw, right_x, right_w, 244, remaining_upcoming, now, bool(live))
        self._draw_lpl_recent_rows(image, draw, right_x, right_w, 374, recent[:2])

    def _draw_lpl_logo(self, image, draw, x, y, width, height):
        x = int(x)
        y = int(y)
        width = int(width)
        height = int(height)
        logo = self._load_local_logo(LOCAL_LPL_LOGO_PATH, (width, height), alpha_threshold=8)
        if logo:
            image.paste(logo, (x + (width - logo.width) // 2, y + (height - logo.height) // 2), logo)
            return
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
        text, font = self._fit_text(draw, "LPL", width - stripe_w - 22, max(16, int(height * 0.62)), bold=True, min_size=13)
        self._draw_centered(draw, (x + width / 2 + 3, y + height / 2), text, font, COLORS["text"])

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

    def _draw_lpl_focus_card(self, image, draw, right_x, right_w, y, event, now, is_live):
        card_x1 = right_x + 12
        card_x2 = right_x + right_w - 12
        card_y2 = y + 154
        accent = COLORS["lpl_live"] if is_live else COLORS["lpl_accent"]
        draw.rounded_rectangle((card_x1 + 4, y + 4, card_x2 + 4, card_y2 + 4), radius=6, fill=COLORS["lpl_shadow"])
        draw.rounded_rectangle((card_x1, y, card_x2, card_y2), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((card_x1 + 1, y + 1, card_x1 + 8, card_y2 - 1), fill=accent)

        if not event:
            draw.text((card_x1 + 20, y + 58), "No LPL schedule", font=self._font(19, True), fill=COLORS["text"])
            return

        tag = self._lpl_focus_tag(is_live)
        tag_w = 112 if is_live else 86
        tag_text, tag_font = self._fit_text(draw, tag, tag_w - 10, 12, bold=True, min_size=8)
        tag_fill = COLORS["lpl_live"] if is_live else COLORS["lpl_tag"]
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
        self._draw_centered(draw, (center_x, y + 88), center_score, self._font(13, True), COLORS["text"])
        if is_live:
            self._draw_lpl_little_round(draw, center_x, y, event)

        team_y = y + 109
        team_a, font_a = self._fit_text(draw, event["team_a"], left_area[1] - left_area[0], 22, bold=True, min_size=13)
        team_b, font_b = self._fit_text(draw, event["team_b"], right_area[1] - right_area[0], 22, bold=True, min_size=13)
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, font_a, COLORS["text"])
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, font_b, COLORS["text"])

        odds = event.get("odds") or {}
        has_odds = bool(odds.get("team_a") and odds.get("team_b"))
        if has_odds:
            self._draw_lpl_odds_text(draw, (left_area[0], y + 127, left_area[1], y + 139), odds.get("team_a"), max_size=11)
            self._draw_lpl_odds_text(draw, (right_area[0], y + 127, right_area[1], y + 139), odds.get("team_b"), max_size=11)

        stage = self._lpl_stage_label(event)
        block_text, block_font = self._fit_text(draw, stage, card_x2 - card_x1 - 34, 12, bold=True, min_size=8)
        block_y = y + 141 if has_odds else y + 136
        draw.text((card_x1 + 17, block_y), block_text, font=block_font, fill=COLORS["lpl_accent"])

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

    def _draw_lpl_next_rows(self, image, draw, right_x, right_w, y, events, now, is_live):
        self._draw_section_header(draw, right_x, right_w, y, "UPCOMING", COLORS["lpl_accent"])
        if not events:
            draw.text((right_x + 18, y + 38), "No more LPL schedule", font=self._font(14, True), fill=COLORS["muted"])
            self._draw_lpl_empty_upcoming_filler(image, right_x, right_w, y, 0)
            return
        row_y = y + 30
        visible_events = events[:2]
        for index, event in enumerate(visible_events):
            self._draw_lpl_next_row(image, draw, right_x, right_w, row_y + index * 48, event, now)
        self._draw_lpl_empty_upcoming_filler(image, right_x, right_w, y, len(visible_events))

    def _draw_lpl_empty_upcoming_filler(self, image, right_x, right_w, section_y, visible_count):
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
        filler = self._load_lpl_sidebar_filler((width, height))
        if filler:
            image.paste(filler, (x1, y1), filler)

    def _draw_lpl_next_row(self, image, draw, right_x, right_w, y, event, now):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        draw.rounded_rectangle(
            (row_x1, y, row_x2, y + 44),
            radius=6,
            fill=COLORS["panel"],
            outline=COLORS["border"],
            width=1,
        )
        draw.rectangle((row_x1 + 1, y + 1, row_x1 + 5, y + 43), fill=COLORS["lpl_accent"])
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
        team_a, font_a = self._fit_text(draw, event["team_a"], 45, 13, bold=True, min_size=8)
        self._draw_text_in_box(draw, (row_x1 + 36, team_top, center_x - 16, team_bottom), team_a, font_a, COLORS["text"])
        self._draw_centered_in_box(draw, (center_x - 13, team_top, center_x + 13, team_bottom), "VS", self._font(10, True), COLORS["muted"])
        logo_x = row_x2 - 12 - logo_size
        self._draw_team_logo(image, draw, event.get("team_b_logo"), logo_x, logo_y, logo_size, event["team_b"])
        team_b, font_b = self._fit_text(draw, event["team_b"], 45, 13, bold=True, min_size=8)
        self._draw_text_in_box(draw, (center_x + 16, team_top, logo_x - 5, team_bottom), team_b, font_b, COLORS["text"], align="right")
        odds = event.get("odds") or {}
        if odds.get("team_a") and odds.get("team_b"):
            self._draw_lpl_odds_text(draw, (row_x1 + 36, y + 31, center_x - 16, y + 43), odds.get("team_a"), max_size=9, align="left")
            self._draw_lpl_odds_text(draw, (center_x + 16, y + 31, logo_x - 5, y + 43), odds.get("team_b"), max_size=9, align="right")

    def _draw_lpl_recent_rows(self, image, draw, right_x, right_w, y, events):
        self._draw_section_header(draw, right_x, right_w, y, "RECENT", COLORS["lpl_accent"])
        if not events:
            draw.text((right_x + 18, y + 42), "No recent results", font=self._font(16, True), fill=COLORS["text"])
            return
        row_y = y + 28
        for index, event in enumerate(events[:2]):
            self._draw_lpl_recent_result_row(image, draw, right_x, right_w, row_y + index * 40, event)

    def _draw_lpl_recent_result_row(self, image, draw, right_x, right_w, y, event):
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
        team_a, font_a = self._fit_text(draw, event["team_a"], left_text_w, 12, bold=True, min_size=8)
        self._draw_text_in_box(draw, (left_text_x, y, score_x - 6, y + row_h), team_a, font_a, COLORS["text"])
        score = self._score_label(event)
        score_text, score_font = self._fit_text(draw, score, score_w, 12, bold=True, min_size=9)
        self._draw_centered_in_box(draw, (score_x, y, score_x + score_w, y + row_h), score_text, score_font, COLORS["text"])
        right_logo_x = row_x2 - logo_size
        right_text_x2 = right_logo_x - 5
        right_text_x1 = score_x + score_w + 6
        right_text_w = max(22, right_text_x2 - right_text_x1)
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, y + 7, logo_size, event["team_b"])
        team_b, font_b = self._fit_text(draw, event["team_b"], right_text_w, 12, bold=True, min_size=8)
        self._draw_text_in_box(draw, (right_text_x1, y, right_text_x2, y + row_h), team_b, font_b, COLORS["text"], align="right")

    @staticmethod
    def _lpl_stage_label(event):
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
        return "LPL"

    @staticmethod
    def _score_label(event):
        if event.get("wins_a") is None or event.get("wins_b") is None:
            return "vs"
        return f"{event['wins_a']}-{event['wins_b']}"

    def _draw_lpl_main_card(self, draw, right_x, right_w, y, event, now, is_live):
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
        team_a, font_a = self._fit_text(draw, event["team_a"], team_col_w, 31, bold=True, min_size=18)
        team_b, font_b = self._fit_text(draw, event["team_b"], team_col_w, 31, bold=True, min_size=18)
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

    def _draw_lpl_recent(self, draw, right_x, right_w, y, events):
        self._draw_section_header(draw, right_x, right_w, y, "RECENT", COLORS["lpl_accent"])
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
        return [
            os.path.join(LOCAL_TEAM_LOGO_DIR, f"{code}{extension}")
            for extension in (".png", ".webp", ".jpg", ".jpeg")
        ]

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
    def _load_team_logo(logo_url, size):
        if not logo_url:
            return None
        cache_key = (logo_url, size)
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        try:
            request = urllib.request.Request(
                logo_url,
                headers={"User-Agent": "InkyPi/1.0"},
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                data = response.read()
            with Image.open(BytesIO(data)) as source:
                logo = SportsDashboard._logo_with_transparent_background(source)
                bbox = logo.getbbox()
                if bbox:
                    logo = logo.crop(bbox)
                logo = ImageOps.contain(logo, (size, size), Image.LANCZOS)
            TEAM_LOGO_CACHE[cache_key] = logo
            return logo
        except Exception as exc:
            logger.warning("Failed to load LPL team logo %s: %s", logo_url, exc)
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
        return f"{event['team_a']} vs {event['team_b']}"

    @staticmethod
    def _result_label(event):
        if event.get("wins_a") is None or event.get("wins_b") is None:
            return SportsDashboard._match_label(event)
        return f"{event['team_a']} {event['wins_a']}-{event['wins_b']} {event['team_b']}"

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
            resolve_path(os.path.join("plugins", "chinese_literature_clock", "fonts", "LXGWWenKai-Regular.ttf")),
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
        box = draw.textbbox((0, 0), str(text), font=font)
        return box[2] - box[0]

    @staticmethod
    def _fit_text(draw, text, max_width, size, bold=False, min_size=11):
        text = str(text or "")
        for font_size in range(size, min_size - 1, -1):
            font = SportsDashboard._font(font_size, bold)
            if SportsDashboard._text_width(draw, text, font) <= max_width:
                return text, font
        return text, SportsDashboard._font(min_size, bold)

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
