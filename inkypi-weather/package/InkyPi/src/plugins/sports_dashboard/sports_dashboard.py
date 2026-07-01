import sys as _sys
import types as _types

from .common import *
from .common import SportsDashboardCommonMixin, _ACTIVE_COLORS, _safe_exception_text, _normalize_country_alias
from . import common as _common_module
from . import worldcup as _worldcup_module
from . import worldcup_render as _worldcup_render_module
from . import nba as _nba_module
from . import esports as _esports_module
from . import esports_render as _esports_render_module
from . import f1 as _f1_module
from . import offseason_hub as _offseason_hub_module
from . import offseason_render as _offseason_render_module
from .worldcup import WorldCupMixin
from .worldcup_render import WorldCupRenderMixin
from .nba import NBAMixin
from .esports import EsportsMixin
from .esports_render import EsportsRenderMixin
from .f1 import F1Mixin
from .offseason_hub import OffseasonHubMixin
from .offseason_render import OffseasonRenderMixin


class SportsDashboard(
    SportsDashboardCommonMixin,
    WorldCupMixin,
    WorldCupRenderMixin,
    NBAMixin,
    EsportsMixin,
    EsportsRenderMixin,
    F1Mixin,
    OffseasonHubMixin,
    OffseasonRenderMixin,
    BasePlugin,
):
    def get_live_refresh_state(self, settings, current_dt):
        settings = settings or {}
        active_intervals = []
        for source in self._active_live_refresh_sources(settings, current_dt):
            active_intervals.append(self._live_image_refresh_interval(settings, source))
        if not active_intervals:
            return None
        return {"active": True, "interval_seconds": min(active_intervals)}

    def _active_live_refresh_sources(self, settings, current_dt):
        sources = []
        if self._live_state_active(self._worldcup_live_state_path(), WORLD_CUP_LIVE_STATE_VERSION, current_dt):
            sources.append("worldcup")
        if self._live_state_active(self._lpl_live_state_path(), LPL_LIVE_STATE_VERSION, current_dt):
            sources.append("lpl")
        if self._live_state_active(self._msi_live_state_path(), MSI_LIVE_STATE_VERSION, current_dt):
            sources.append("msi")
        if self._live_state_active(self._nba_live_state_path(), NBA_LIVE_STATE_VERSION, current_dt):
            sources.append("nba")
        if self._live_state_active(
            self._offseason_hub_live_state_path(),
            OFFSEASON_HUB_STATE_VERSION,
            current_dt,
            live_status_fallback=True,
        ):
            sources.append("offseason_hub")
        return [source for source in sources if self._live_image_refresh_enabled(settings, source)]

    def _live_image_refresh_enabled(self, settings, source):
        if source == "nba":
            return self._bool_setting(settings, "nbaLiveRefreshEnabled", True)
        if source == "worldcup":
            return self._bool_setting(settings, "worldCupLiveRefreshEnabled", True)
        if source == "offseason_hub":
            return self._bool_setting(settings, "offseasonHubLiveRefreshEnabled", True)
        if source in {"lpl", "msi"}:
            return self._bool_setting(settings, "lplLiveRefreshEnabled", True)
        return False

    def _live_image_refresh_interval(self, settings, source):
        if source == "nba":
            return self._int_setting(settings, "nbaLiveRefreshIntervalSeconds", 60, 60, 900)
        if source == "worldcup":
            return self._int_setting(settings, "worldCupLiveRefreshIntervalSeconds", 60, 60, 900)
        if source == "offseason_hub":
            return self._int_setting(settings, "offseasonHubLiveRefreshIntervalSeconds", 60, 60, 900)
        if source in {"lpl", "msi"}:
            return self._int_setting(settings, "lplLiveRefreshIntervalSeconds", 60, 60, 900)
        return 60

    def _live_state_active(self, path, version, current_dt, live_status_fallback=False):
        state = self._read_json_file(path)
        if state.get("version") != version:
            return False
        has_live = state.get("has_live")
        if has_live is None and live_status_fallback:
            has_live = str(state.get("status") or "").strip().upper() == "LIVE"
        if not has_live:
            return False
        live_until = self._parse_live_state_datetime(state.get("live_until"))
        if not live_until:
            return True
        current = current_dt if isinstance(current_dt, datetime) else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc) <= live_until

    @staticmethod
    def _parse_live_state_datetime(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)








































    @staticmethod
    def _html_text(value):
        text = str(value or "")
        text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_lib.unescape(text)
        return re.sub(r"\s+", " ", text).strip()

















    @staticmethod
    def _right_sidebar_has_active_competition(candidates):
        for item in candidates or []:
            phase = item.get("phase")
            kind = str(item.get("kind") or "").strip().lower()
            if kind == "lol" and phase in (0, 1):
                return True
            if kind == "ewc" and phase == 0:
                return True
            if kind == "valve":
                return True
        return False































































































    @staticmethod
    def _ordinal_text(value):
        value = int(value)
        if 10 <= value % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
        return f"{value}{suffix}"



























































































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
    def _espn_competitor_advance(competitor):
        value = (competitor or {}).get("advance")
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
        return None



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
    def _team_info(teams, index):
        if index >= len(teams):
            return "TBD", None, ""
        team = teams[index] or {}
        result = team.get("result") or {}
        name = str(team.get("code") or team.get("name") or "TBD").strip() or "TBD"
        logo = str(team.get("image") or "").strip()
        return name, SportsDashboard._lpl_int_value(result.get("gameWins")), logo












    @staticmethod
    def _countdown_days(now, start):
        if not isinstance(now, datetime) or not isinstance(start, datetime):
            return 0
        return max(0, (start.date() - now.date()).days)







































































































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
    def _normalize_odds_team_name(value):
        text = str(value or "").strip()
        if not text:
            return ""
        text = text.replace("&", " ")
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        parts = [part for part in text.lower().replace("-", " ").split() if part != "and"]
        return "".join(ch for part in parts for ch in part if ch.isalnum())







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
    def _trim_transparent_flag(flag):
        if flag.mode != "RGBA":
            flag = flag.convert("RGBA")
        bbox = flag.getchannel("A").getbbox()
        if bbox:
            return flag.crop(bbox)
        return flag














































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
    def _score_label(event):
        if event.get("wins_a") is None or event.get("wins_b") is None:
            return "vs"
        return f"{event['wins_a']}-{event['wins_b']}"































_SPLIT_MODULES = (
    _common_module,
    _worldcup_module,
    _worldcup_render_module,
    _nba_module,
    _esports_module,
    _esports_render_module,
    _f1_module,
    _offseason_hub_module,
    _offseason_render_module,
)
for _module in _SPLIT_MODULES:
    _module.SportsDashboard = SportsDashboard


class _SportsDashboardModule(_types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for module in _SPLIT_MODULES:
            if hasattr(module, name):
                setattr(module, name, value)


_sys.modules[__name__].__class__ = _SportsDashboardModule
