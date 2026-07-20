from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from .common import *
from .common import _normalize_country_alias, _safe_exception_text
from .club_football_localization import (
    CLUB_FOOTBALL_TEAM_LOCALIZATIONS,
    contains_chinese,
)


SportsDashboard = None

CLUB_FOOTBALL_LIVE_STATE_VERSION = "sports-dashboard-club-football-live-v1"
CLUB_FOOTBALL_WORLD_CUP_LEAD = timedelta(days=14)
CLUB_FOOTBALL_WORLD_CUP_TAIL = timedelta(hours=24)
CLUB_FOOTBALL_DEFAULT_FINAL_DURATION = timedelta(hours=3)
CLUB_FOOTBALL_PROVIDER_CACHE_VERSION = "sports-dashboard-club-football-provider-v1"
CLUB_FOOTBALL_STANDINGS_CACHE_VERSION = "sports-dashboard-club-football-standings-v1"
CLUB_FOOTBALL_ESPN_STATE_VERSION = "sports-dashboard-club-football-espn-state-v1"
CLUB_ESPN_SCOREBOARD_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
CLUB_FOOTBALL_NORMAL_CACHE_SECONDS = 6 * 60 * 60
CLUB_FOOTBALL_MATCHDAY_CACHE_SECONDS = 15 * 60
CLUB_FOOTBALL_LIVE_CACHE_SECONDS = 60
CLUB_FOOTBALL_PREGAME_WINDOW = timedelta(minutes=15)
CLUB_FOOTBALL_DEFAULT_MATCH_WINDOW = timedelta(hours=2)
CLUB_FOOTBALL_ROTATION_STATE_VERSION = "sports-dashboard-club-football-rotation-v1"
CLUB_FOOTBALL_API_ODDS_CACHE_VERSION = "sports-dashboard-club-api-football-odds-v1"
DEFAULT_CLUB_FOOTBALL_ESPN_DAILY_LIMIT = 720
DEFAULT_CLUB_FOOTBALL_API_ODDS_CACHE_HOURS = 3
DEFAULT_CLUB_FOOTBALL_API_ODDS_DAILY_LIMIT = 40
CLUB_FOOTBALL_API_ODDS_LOOKBACK = timedelta(days=7)
CLUB_FOOTBALL_API_ODDS_LOOKAHEAD = timedelta(days=14)
CLUB_FOOTBALL_LEAGUES = {
    "PL": {"name": "英超", "short_name": "英超", "espn_slug": "eng.1", "api_football_id": 39},
    "PD": {"name": "西甲", "short_name": "西甲", "espn_slug": "esp.1", "api_football_id": 140},
    "BL1": {"name": "德甲", "short_name": "德甲", "espn_slug": "ger.1", "api_football_id": 78},
    "SA": {"name": "意甲", "short_name": "意甲", "espn_slug": "ita.1", "api_football_id": 135},
    "FL1": {"name": "法甲", "short_name": "法甲", "espn_slug": "fra.1", "api_football_id": 61},
}


class _ClubFootballRotationSeed(int):
    def __new__(cls, value, previous_league=None):
        instance = int.__new__(cls, int(value))
        instance.previous_league = previous_league
        return instance


class ClubFootballMixin:
    @staticmethod
    def _club_football_enabled_leagues(settings):
        default_value = ",".join(CLUB_FOOTBALL_LEAGUES)
        raw = str((settings or {}).get("clubFootballEnabledLeagues") or default_value)
        requested = {item.strip().upper() for item in raw.split(",") if item.strip()}
        enabled = tuple(code for code in CLUB_FOOTBALL_LEAGUES if code in requested)
        return enabled or tuple(CLUB_FOOTBALL_LEAGUES)

    @staticmethod
    def _football_panel_mode(settings):
        mode = str((settings or {}).get("footballPanelMode") or "club").strip().lower()
        return mode if mode in {"auto", "worldcup", "club"} else "club"

    @staticmethod
    def _select_football_panel_kind(mode, now, worldcup_summary):
        if mode in {"worldcup", "club"}:
            return mode
        if not isinstance(now, datetime) or not isinstance(worldcup_summary, Mapping):
            return "club"

        first = worldcup_summary.get("first_start")
        final = worldcup_summary.get("final_start")
        if not isinstance(first, datetime) or not isinstance(final, datetime):
            return "club"
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        if final.tzinfo is None:
            final = final.replace(tzinfo=timezone.utc)

        final_end = worldcup_summary.get("final_end")
        if not isinstance(final_end, datetime):
            final_end = final + CLUB_FOOTBALL_DEFAULT_FINAL_DURATION
        if final_end.tzinfo is None:
            final_end = final_end.replace(tzinfo=timezone.utc)

        return (
            "worldcup"
            if first - CLUB_FOOTBALL_WORLD_CUP_LEAD
            <= now
            <= final_end + CLUB_FOOTBALL_WORLD_CUP_TAIL
            else "club"
        )


    @staticmethod
    def _club_parse_utc(value):
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _club_score_value(value):
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _club_first_logo(item):
        singular = str((item or {}).get("logo") or "").strip()
        if singular:
            return singular
        logos = (item or {}).get("logos") or []
        for logo in logos:
            href = str((logo or {}).get("href") or "").strip()
            if href:
                return href
        return ""

    @staticmethod
    def _club_team_match_key(name):
        key = _normalize_country_alias(name)
        for suffix in ("footballclub", "calcio", "fc", "cf"):
            if key.endswith(suffix) and len(key) > len(suffix) + 2:
                key = key[:-len(suffix)]
                break
        return key

    @staticmethod
    def _club_team_zh_name(league_code, name, team_id=""):
        display_name = str(name or "").strip()
        if contains_chinese(display_name):
            return display_name

        entries = CLUB_FOOTBALL_TEAM_LOCALIZATIONS.get(
            str(league_code or "").upper(), ()
        )
        requested_id = str(team_id or "").strip()
        if requested_id:
            for entry_id, _english_name, chinese_name in entries:
                if entry_id == requested_id:
                    return chinese_name

        requested_key = SportsDashboard._club_team_match_key(display_name)
        for _entry_id, english_name, chinese_name in entries:
            if SportsDashboard._club_team_match_key(english_name) == requested_key:
                return chinese_name
        return "待定球队"

    @staticmethod
    def _club_event_key(league_code, start_utc, home_name, away_name):
        if not isinstance(start_utc, datetime):
            return ""
        start_key = start_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M")
        home = SportsDashboard._club_team_match_key(home_name)
        away = SportsDashboard._club_team_match_key(away_name)
        return f"{league_code}:{start_key}:{home}:{away}"

    @staticmethod
    def _club_base_event(
        league_code,
        start_utc,
        status,
        home_name,
        away_name,
        *,
        provider,
        provider_status_confirmed=False,
    ):
        league = CLUB_FOOTBALL_LEAGUES[league_code]
        return {
            "league_code": league_code,
            "league_name": league["name"],
            "espn_slug": league["espn_slug"],
            "event_key": SportsDashboard._club_event_key(
                league_code, start_utc, home_name, away_name
            ),
            "start_utc": start_utc,
            "status": status,
            "home_name": home_name,
            "away_name": away_name,
            "home_name_zh": SportsDashboard._club_team_zh_name(league_code, home_name),
            "away_name_zh": SportsDashboard._club_team_zh_name(league_code, away_name),
            "home_normalized": SportsDashboard._club_team_match_key(home_name),
            "away_normalized": SportsDashboard._club_team_match_key(away_name),
            "home_aliases": [home_name],
            "away_aliases": [away_name],
            "home_score": None,
            "away_score": None,
            "display_clock": "",
            "venue": "",
            "matchday": None,
            "league_logo_url": "",
            "home_logo_url": "",
            "away_logo_url": "",
            "provider": provider,
            "source_state": provider,
            "fetched_at": None,
            "provider_status_confirmed": bool(provider_status_confirmed),
            "inferred_live_window": False,
        }

    @staticmethod
    def _club_american_odds_to_decimal(value):
        if value is None:
            return None
        text = str(value).strip().upper().replace(",", "")
        if text in {"EVEN", "EVENS"}:
            return 2.0
        try:
            american = float(text)
        except (TypeError, ValueError):
            return None
        if american == 0:
            return None
        decimal = 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))
        return round(decimal, 2)

    @staticmethod
    def _club_odds_provider_short(provider):
        name = str(provider or "").strip()
        compact = "".join(character for character in name if character.isalnum())
        normalized = compact.casefold()
        if normalized == "draftkings":
            return "DK"
        if normalized == "bet365":
            return "365"
        return compact.upper()[:6] or "BOOK"

    @staticmethod
    def _club_espn_moneyline_price(market, side):
        moneyline = market.get("moneyline") if isinstance(market, Mapping) else None
        moneyline = moneyline if isinstance(moneyline, Mapping) else {}
        side_block = moneyline.get(side)
        if isinstance(side_block, Mapping):
            for phase in ("close", "open"):
                phase_block = side_block.get(phase)
                if isinstance(phase_block, Mapping) and phase_block.get("odds") is not None:
                    return phase_block.get("odds")
            if side_block.get("odds") is not None:
                return side_block.get("odds")

        legacy_key = {
            "home": "homeTeamOdds",
            "draw": "drawOdds",
            "away": "awayTeamOdds",
        }[side]
        legacy = market.get(legacy_key) if isinstance(market, Mapping) else None
        if isinstance(legacy, Mapping):
            return legacy.get("moneyLine")
        return None

    @staticmethod
    def _club_espn_moneyline_odds(competition):
        markets = competition.get("odds") if isinstance(competition, Mapping) else None
        for market in markets or []:
            if not isinstance(market, Mapping):
                continue
            decimals = {
                side: SportsDashboard._club_american_odds_to_decimal(
                    SportsDashboard._club_espn_moneyline_price(market, side)
                )
                for side in ("home", "draw", "away")
            }
            if any(value is None for value in decimals.values()):
                continue
            provider_block = market.get("provider") or {}
            provider = str(
                (provider_block or {}).get("name")
                if isinstance(provider_block, Mapping)
                else provider_block
            ).strip()
            return {
                "odds_home_decimal": decimals["home"],
                "odds_draw_decimal": decimals["draw"],
                "odds_away_decimal": decimals["away"],
                "odds_provider": provider or "ESPN",
                "odds_provider_short": SportsDashboard._club_odds_provider_short(
                    provider or "ESPN"
                ),
                "odds_source": "ESPN",
            }
        return {}

    @staticmethod
    def _club_event_has_complete_odds(event):
        if not isinstance(event, Mapping):
            return False
        for key in (
            "odds_home_decimal",
            "odds_draw_decimal",
            "odds_away_decimal",
        ):
            try:
                value = float(event.get(key))
            except (TypeError, ValueError):
                return False
            if value <= 1.0 or value != value or value == float("inf"):
                return False
        return True

    @staticmethod
    def _club_event_team_keys(event, side):
        values = list((event or {}).get(f"{side}_aliases") or [])
        values.append((event or {}).get(f"{side}_name"))
        return {
            SportsDashboard._club_team_match_key(value)
            for value in values
            if str(value or "").strip()
        }

    @staticmethod
    def _club_api_football_fixture_id(event, payload, tolerance=timedelta(minutes=20)):
        if not isinstance(event, Mapping) or not isinstance(payload, Mapping):
            return ""
        event_start = SportsDashboard._club_parse_utc(event.get("start_utc"))
        if event_start is None:
            return ""
        league_code = str(event.get("league_code") or "").upper()
        expected_league_id = CLUB_FOOTBALL_LEAGUES.get(league_code, {}).get(
            "api_football_id"
        )
        home_keys = SportsDashboard._club_event_team_keys(event, "home")
        away_keys = SportsDashboard._club_event_team_keys(event, "away")
        for item in payload.get("response") or []:
            if not isinstance(item, Mapping):
                continue
            league = item.get("league") or {}
            if expected_league_id and str(league.get("id") or "") != str(
                expected_league_id
            ):
                continue
            fixture = item.get("fixture") or {}
            fixture_start = SportsDashboard._club_parse_utc(fixture.get("date"))
            if fixture_start is None or abs(fixture_start - event_start) > tolerance:
                continue
            teams = item.get("teams") or {}
            api_home = SportsDashboard._club_team_match_key(
                (teams.get("home") or {}).get("name")
            )
            api_away = SportsDashboard._club_team_match_key(
                (teams.get("away") or {}).get("name")
            )
            if api_home in home_keys and api_away in away_keys:
                return str(fixture.get("id") or "")
        return ""

    @staticmethod
    def _club_parse_api_football_odds(payload):
        if not isinstance(payload, Mapping):
            return {}
        candidates = []
        for item in payload.get("response") or []:
            if not isinstance(item, Mapping):
                continue
            for bookmaker in item.get("bookmakers") or []:
                if not isinstance(bookmaker, Mapping):
                    continue
                provider = str(bookmaker.get("name") or "").strip() or "API-Football"
                for bet in bookmaker.get("bets") or []:
                    if not isinstance(bet, Mapping):
                        continue
                    bet_name = str(bet.get("name") or "").strip().casefold()
                    if str(bet.get("id") or "") != "1" and bet_name != "match winner":
                        continue
                    values = {}
                    for raw_value in bet.get("values") or []:
                        if not isinstance(raw_value, Mapping):
                            continue
                        outcome = str(raw_value.get("value") or "").strip().casefold()
                        if outcome not in {"home", "draw", "away"}:
                            continue
                        try:
                            decimal = round(float(raw_value.get("odd")), 2)
                        except (TypeError, ValueError):
                            continue
                        if decimal > 1.0:
                            values[outcome] = decimal
                    if all(side in values for side in ("home", "draw", "away")):
                        normalized = "".join(
                            character for character in provider if character.isalnum()
                        ).casefold()
                        priority = 0 if normalized == "bet365" else 1
                        candidates.append((priority, provider, values))
        if not candidates:
            return {}
        _priority, provider, values = sorted(candidates, key=lambda item: item[:2])[0]
        return {
            "odds_home_decimal": values["home"],
            "odds_draw_decimal": values["draw"],
            "odds_away_decimal": values["away"],
            "odds_provider": provider,
            "odds_provider_short": SportsDashboard._club_odds_provider_short(provider),
            "odds_source": "API-Football",
        }

    @staticmethod
    def _club_apply_odds_fallback(event, fallback):
        merged = dict(event or {})
        if SportsDashboard._club_event_has_complete_odds(merged):
            return merged
        if SportsDashboard._club_event_has_complete_odds(fallback):
            merged.update(
                {
                    key: fallback.get(key)
                    for key in (
                        "odds_home_decimal",
                        "odds_draw_decimal",
                        "odds_away_decimal",
                        "odds_provider",
                        "odds_provider_short",
                        "odds_source",
                    )
                }
            )
        return merged

    @staticmethod
    def _parse_club_espn_events(league_code, payload, timezone_info):
        if league_code not in CLUB_FOOTBALL_LEAGUES or not isinstance(payload, Mapping):
            return []

        league_logo_url = ""
        for league in payload.get("leagues") or []:
            if str((league or {}).get("slug") or "") == CLUB_FOOTBALL_LEAGUES[league_code]["espn_slug"]:
                league_logo_url = SportsDashboard._club_first_logo(league)
                break
        if not league_logo_url and payload.get("leagues"):
            league_logo_url = SportsDashboard._club_first_logo(payload["leagues"][0])

        parsed_events = []
        for raw_event in payload.get("events") or []:
            competitions = (raw_event or {}).get("competitions") or []
            competition = competitions[0] if competitions else {}
            competitors = competition.get("competitors") or []
            home = next(
                (item for item in competitors if str((item or {}).get("homeAway") or "").lower() == "home"),
                None,
            )
            away = next(
                (item for item in competitors if str((item or {}).get("homeAway") or "").lower() == "away"),
                None,
            )
            if not home or not away:
                continue

            start_utc = SportsDashboard._club_parse_utc(raw_event.get("date"))
            if start_utc is None:
                continue
            home_team = home.get("team") or {}
            away_team = away.get("team") or {}
            home_name = str(
                home_team.get("displayName")
                or home_team.get("shortDisplayName")
                or home_team.get("name")
                or "TBD"
            ).strip()
            away_name = str(
                away_team.get("displayName")
                or away_team.get("shortDisplayName")
                or away_team.get("name")
                or "TBD"
            ).strip()

            status_block = competition.get("status") or raw_event.get("status") or {}
            type_block = status_block.get("type") or {}
            provider_state = str(type_block.get("state") or "").strip().lower()
            provider_name = str(type_block.get("name") or "").strip().upper()
            completed = bool(type_block.get("completed")) or provider_state == "post"
            confirmed_live = provider_state in {"in", "live"} or provider_name in {
                "STATUS_IN_PROGRESS",
                "STATUS_HALFTIME",
                "STATUS_DELAYED",
            }
            status = "FINAL" if completed else ("LIVE" if confirmed_live else "SCHEDULED")
            event = SportsDashboard._club_base_event(
                league_code,
                start_utc,
                status,
                home_name,
                away_name,
                provider="ESPN",
                provider_status_confirmed=confirmed_live,
            )
            event.update(
                {
                    "provider_event_id": str(raw_event.get("id") or ""),
                    "home_team_id": str(home_team.get("id") or ""),
                    "away_team_id": str(away_team.get("id") or ""),
                    "home_name_zh": SportsDashboard._club_team_zh_name(
                        league_code, home_name, team_id=home_team.get("id")
                    ),
                    "away_name_zh": SportsDashboard._club_team_zh_name(
                        league_code, away_name, team_id=away_team.get("id")
                    ),
                    "home_score": SportsDashboard._club_score_value(home.get("score")),
                    "away_score": SportsDashboard._club_score_value(away.get("score")),
                    "display_clock": str(
                        status_block.get("displayClock")
                        or type_block.get("shortDetail")
                        or type_block.get("detail")
                        or ""
                    ).strip(),
                    "venue": str((competition.get("venue") or {}).get("fullName") or "").strip(),
                    "league_logo_url": league_logo_url,
                    "home_logo_url": SportsDashboard._club_first_logo(home_team),
                    "away_logo_url": SportsDashboard._club_first_logo(away_team),
                    "source_state": "ESPN",
                    "fetched_at": payload.get("fetched_at"),
                    **SportsDashboard._club_espn_moneyline_odds(competition),
                }
            )
            parsed_events.append(event)
        return parsed_events

    @staticmethod
    def _parse_club_football_data_events(league_code, matches, timezone_info):
        if league_code not in CLUB_FOOTBALL_LEAGUES:
            return []

        parsed_events = []
        for raw_match in matches or []:
            start_utc = SportsDashboard._club_parse_utc((raw_match or {}).get("utcDate"))
            if start_utc is None:
                continue
            home_team = raw_match.get("homeTeam") or {}
            away_team = raw_match.get("awayTeam") or {}
            home_name = str(home_team.get("name") or home_team.get("shortName") or "TBD").strip()
            away_name = str(away_team.get("name") or away_team.get("shortName") or "TBD").strip()
            provider_status = str(raw_match.get("status") or "").strip().upper()
            completed = provider_status in {"FINISHED", "AWARDED"}
            confirmed_live = provider_status in {"IN_PLAY", "PAUSED"}
            status = "FINAL" if completed else ("LIVE" if confirmed_live else "SCHEDULED")
            event = SportsDashboard._club_base_event(
                league_code,
                start_utc,
                status,
                home_name,
                away_name,
                provider="football-data.org",
                provider_status_confirmed=confirmed_live,
            )
            full_time = (raw_match.get("score") or {}).get("fullTime") or {}
            event.update(
                {
                    "provider_event_id": str(raw_match.get("id") or ""),
                    "home_team_id": str(home_team.get("id") or ""),
                    "away_team_id": str(away_team.get("id") or ""),
                    "home_name_zh": SportsDashboard._club_team_zh_name(
                        league_code, home_name
                    ),
                    "away_name_zh": SportsDashboard._club_team_zh_name(
                        league_code, away_name
                    ),
                    "home_aliases": [
                        value
                        for value in (
                            home_name,
                            home_team.get("shortName"),
                            home_team.get("tla"),
                        )
                        if value
                    ],
                    "away_aliases": [
                        value
                        for value in (
                            away_name,
                            away_team.get("shortName"),
                            away_team.get("tla"),
                        )
                        if value
                    ],
                    "home_score": SportsDashboard._club_score_value(full_time.get("home")),
                    "away_score": SportsDashboard._club_score_value(full_time.get("away")),
                    "venue": str(raw_match.get("venue") or "").strip(),
                    "matchday": raw_match.get("matchday"),
                    "home_logo_url": str(home_team.get("crest") or "").strip(),
                    "away_logo_url": str(away_team.get("crest") or "").strip(),
                    "source_state": "football-data.org",
                    "fetched_at": raw_match.get("fetched_at"),
                }
            )
            parsed_events.append(event)
        return parsed_events

    @staticmethod
    def _merge_club_football_events(
        schedule_events,
        score_events,
        tolerance=timedelta(minutes=15),
    ):
        remaining_scores = list(score_events or [])
        merged_events = []

        for schedule in schedule_events or []:
            match_index = None
            reverse_order = False
            for index, score in enumerate(remaining_scores):
                if schedule.get("league_code") != score.get("league_code"):
                    continue
                schedule_start = schedule.get("start_utc")
                score_start = score.get("start_utc")
                if not isinstance(schedule_start, datetime) or not isinstance(score_start, datetime):
                    continue
                if abs(schedule_start - score_start) > tolerance:
                    continue
                same_order = (
                    SportsDashboard._club_team_match_key(schedule.get("home_name"))
                    == SportsDashboard._club_team_match_key(score.get("home_name"))
                    and SportsDashboard._club_team_match_key(schedule.get("away_name"))
                    == SportsDashboard._club_team_match_key(score.get("away_name"))
                )
                reversed_names = (
                    SportsDashboard._club_team_match_key(schedule.get("home_name"))
                    == SportsDashboard._club_team_match_key(score.get("away_name"))
                    and SportsDashboard._club_team_match_key(schedule.get("away_name"))
                    == SportsDashboard._club_team_match_key(score.get("home_name"))
                )
                if same_order or reversed_names:
                    match_index = index
                    reverse_order = reversed_names
                    break

            if match_index is None:
                merged_events.append(dict(schedule))
                continue

            score = remaining_scores.pop(match_index)
            merged = dict(schedule)
            home_score = score.get("away_score") if reverse_order else score.get("home_score")
            away_score = score.get("home_score") if reverse_order else score.get("away_score")
            home_logo = score.get("away_logo_url") if reverse_order else score.get("home_logo_url")
            away_logo = score.get("home_logo_url") if reverse_order else score.get("away_logo_url")
            home_odds = (
                score.get("odds_away_decimal")
                if reverse_order
                else score.get("odds_home_decimal")
            )
            away_odds = (
                score.get("odds_home_decimal")
                if reverse_order
                else score.get("odds_away_decimal")
            )
            merged.update(
                {
                    "status": score.get("status") or schedule.get("status"),
                    "home_score": home_score,
                    "away_score": away_score,
                    "display_clock": score.get("display_clock") or "",
                    "venue": score.get("venue") or schedule.get("venue") or "",
                    "league_logo_url": score.get("league_logo_url") or schedule.get("league_logo_url") or "",
                    "home_logo_url": home_logo or schedule.get("home_logo_url") or "",
                    "away_logo_url": away_logo or schedule.get("away_logo_url") or "",
                    "provider": "football-data.org+ESPN",
                    "source_state": "football-data.org+ESPN",
                    "fetched_at": score.get("fetched_at") or schedule.get("fetched_at"),
                    "provider_status_confirmed": bool(score.get("provider_status_confirmed")),
                    "inferred_live_window": False,
                    "odds_home_decimal": home_odds,
                    "odds_draw_decimal": score.get("odds_draw_decimal"),
                    "odds_away_decimal": away_odds,
                    "odds_provider": score.get("odds_provider"),
                    "odds_provider_short": score.get("odds_provider_short"),
                    "odds_source": score.get("odds_source"),
                }
            )
            merged_events.append(merged)

        merged_events.extend(dict(score) for score in remaining_scores)
        unique = {}
        for event in merged_events:
            event_key = event.get("event_key") or SportsDashboard._club_event_key(
                event.get("league_code"),
                event.get("start_utc"),
                event.get("home_name"),
                event.get("away_name"),
            )
            unique[event_key] = event
        return list(unique.values())


    @staticmethod
    def _club_espn_cache_seconds(events, now):
        if not isinstance(now, datetime):
            return CLUB_FOOTBALL_NORMAL_CACHE_SECONDS
        for event in events or []:
            start = (event or {}).get("start_utc")
            if (
                event.get("status") == "LIVE"
                and event.get("provider_status_confirmed")
            ):
                return CLUB_FOOTBALL_LIVE_CACHE_SECONDS
            if isinstance(start, datetime) and now <= start <= now + CLUB_FOOTBALL_PREGAME_WINDOW:
                return CLUB_FOOTBALL_LIVE_CACHE_SECONDS
        for event in events or []:
            start = (event or {}).get("start_utc")
            if isinstance(start, datetime) and start.astimezone(now.tzinfo).date() == now.date():
                return CLUB_FOOTBALL_MATCHDAY_CACHE_SECONDS
        return CLUB_FOOTBALL_NORMAL_CACHE_SECONDS

    @staticmethod
    def _club_espn_scoreboard_url(league_code):
        league = CLUB_FOOTBALL_LEAGUES[league_code]
        return f"{CLUB_ESPN_SCOREBOARD_BASE_URL}/{league['espn_slug']}/scoreboard"

    def _club_football_cache_path(self, provider, league_code):
        safe_provider = str(provider).replace("-", "_")
        return self._sports_dashboard_cache_dir() / (
            f"club_football_{safe_provider}_{league_code.lower()}.json"
        )

    def _club_football_last_good_cache_path(self, provider, league_code):
        safe_provider = str(provider).replace("-", "_")
        return self._sports_dashboard_cache_dir() / (
            f"club_football_{safe_provider}_{league_code.lower()}.last_good.json"
        )

    def _club_football_standings_cache_path(self, league_code):
        return self._club_football_cache_path("standings", league_code)

    def _club_football_standings_last_good_path(self, league_code):
        return self._club_football_last_good_cache_path("standings", league_code)

    def _club_espn_state_path(self):
        return self._sports_dashboard_cache_dir() / "club_football_espn_requests.json"

    @staticmethod
    def _club_read_cached_payload(current_path, last_good_path, version, league_code):
        for path in (current_path, last_good_path):
            cache = SportsDashboard._read_json_file(path)
            if (
                cache.get("version") == version
                and cache.get("league_code") == league_code
                and isinstance(cache.get("payload"), Mapping)
            ):
                return cache
        return {}

    def _club_write_cached_payload(
        self,
        current_path,
        last_good_path,
        version,
        league_code,
        payload,
        fetched_at,
    ):
        wrapper = {
            "version": version,
            "league_code": league_code,
            "fetched_at": fetched_at,
            "payload": payload,
        }
        self._write_json_file(current_path, wrapper)
        self._write_json_file(last_good_path, wrapper)

    @staticmethod
    def _club_cache_fresh(cache, cache_seconds, now_utc):
        fetched_at = SportsDashboard._parse_cached_utc(cache.get("fetched_at"))
        if fetched_at is None:
            return False
        return now_utc - fetched_at <= timedelta(seconds=cache_seconds)

    def _club_espn_calls_left(self, settings, now_utc):
        limit = self._int_setting(
            settings,
            "clubFootballEspnDailyLimit",
            DEFAULT_CLUB_FOOTBALL_ESPN_DAILY_LIMIT,
            1,
            1440,
        )
        state = self._read_json_file(self._club_espn_state_path())
        if (
            state.get("version") != CLUB_FOOTBALL_ESPN_STATE_VERSION
            or state.get("date") != now_utc.date().isoformat()
        ):
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_club_espn_call(self, now_utc):
        path = self._club_espn_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        if (
            state.get("version") != CLUB_FOOTBALL_ESPN_STATE_VERSION
            or state.get("date") != today
        ):
            count = 0
        else:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        self._write_json_file(
            path,
            {
                "version": CLUB_FOOTBALL_ESPN_STATE_VERSION,
                "date": today,
                "count": count + 1,
                "updated_at": now_utc.isoformat(),
            },
        )

    def _fetch_club_espn_payload(self, league_code, settings, now_utc):
        if self._club_espn_calls_left(settings, now_utc) <= 0:
            raise RuntimeError("club football ESPN daily request limit reached")
        response = get_http_session().get(
            self._club_espn_scoreboard_url(league_code),
            headers={"User-Agent": "InkyPi/1.0"},
            timeout=15,
        )
        try:
            response.raise_for_status()
            payload = response.json()
        finally:
            self._record_club_espn_call(now_utc)
        if not isinstance(payload, Mapping):
            raise ValueError("club football ESPN response is not an object")
        return payload

    def _load_club_espn_league_payload(
        self,
        league_code,
        settings,
        timezone_info,
        now,
    ):
        now_utc = now.astimezone(timezone.utc)
        current_path = self._club_football_cache_path("espn", league_code)
        last_good_path = self._club_football_last_good_cache_path("espn", league_code)
        cache = self._club_read_cached_payload(
            current_path,
            last_good_path,
            CLUB_FOOTBALL_PROVIDER_CACHE_VERSION,
            league_code,
        )
        cached_payload = cache.get("payload") or {}
        cached_events = self._parse_club_espn_events(
            league_code, cached_payload, timezone_info
        )
        cache_seconds = self._club_espn_cache_seconds(cached_events, now)
        force_refresh = self._force_refresh_requested(settings)
        if cache and not force_refresh and self._club_cache_fresh(
            cache, cache_seconds, now_utc
        ):
            return cached_payload, "ESPN CACHE", cache.get("fetched_at")

        try:
            payload = self._fetch_club_espn_payload(league_code, settings, now_utc)
            parsed = self._parse_club_espn_events(league_code, payload, timezone_info)
            if not isinstance(parsed, list):
                raise ValueError("club football ESPN payload could not be parsed")
            fetched_at = now_utc.isoformat()
            payload = dict(payload)
            payload["fetched_at"] = fetched_at
            self._club_write_cached_payload(
                current_path,
                last_good_path,
                CLUB_FOOTBALL_PROVIDER_CACHE_VERSION,
                league_code,
                payload,
                fetched_at,
            )
            return payload, "ESPN LIVE", fetched_at
        except Exception:
            if cache:
                return cached_payload, "ESPN STALE", cache.get("fetched_at")
            raise

    def _load_club_football_data_league_payload(
        self,
        league_code,
        settings,
        api_key,
        timezone_info,
        now,
    ):
        now_utc = now.astimezone(timezone.utc)
        current_path = self._club_football_cache_path("football_data", league_code)
        last_good_path = self._club_football_last_good_cache_path(
            "football_data", league_code
        )
        cache = self._club_read_cached_payload(
            current_path,
            last_good_path,
            CLUB_FOOTBALL_PROVIDER_CACHE_VERSION,
            league_code,
        )
        force_refresh = self._force_refresh_requested(settings)
        if cache and not force_refresh and self._club_cache_fresh(
            cache, CLUB_FOOTBALL_NORMAL_CACHE_SECONDS, now_utc
        ):
            return cache["payload"], "FOOTBALL CACHE", cache.get("fetched_at")

        try:
            payload = self._football_data_get_json(
                f"/competitions/{league_code}/matches",
                {},
                api_key,
                settings,
                now_utc,
            )
            if not isinstance(payload, Mapping) or not isinstance(
                payload.get("matches"), list
            ):
                raise ValueError("football-data.org matches response is invalid")
            fetched_at = now_utc.isoformat()
            payload = dict(payload)
            payload["fetched_at"] = fetched_at
            self._club_write_cached_payload(
                current_path,
                last_good_path,
                CLUB_FOOTBALL_PROVIDER_CACHE_VERSION,
                league_code,
                payload,
                fetched_at,
            )
            return payload, "FOOTBALL LIVE", fetched_at
        except Exception:
            if cache:
                return cache["payload"], "FOOTBALL STALE", cache.get("fetched_at")
            raise

    def _load_club_standings_league_payload(
        self,
        league_code,
        settings,
        api_key,
        timezone_info,
        now,
        league_events=None,
    ):
        now_utc = now.astimezone(timezone.utc)
        current_path = self._club_football_standings_cache_path(league_code)
        last_good_path = self._club_football_standings_last_good_path(league_code)
        cache = self._club_read_cached_payload(
            current_path,
            last_good_path,
            CLUB_FOOTBALL_STANDINGS_CACHE_VERSION,
            league_code,
        )
        force_refresh = self._force_refresh_requested(settings)
        cache_seconds = self._club_standings_cache_seconds(league_events, now)
        if cache and not force_refresh and self._club_cache_fresh(
            cache, cache_seconds, now_utc
        ):
            return cache["payload"], "STANDINGS CACHE", cache.get("fetched_at")

        try:
            payload = self._football_data_get_json(
                f"/competitions/{league_code}/standings",
                {},
                api_key,
                settings,
                now_utc,
            )
            if not isinstance(payload, Mapping) or not isinstance(
                payload.get("standings"), list
            ):
                raise ValueError("football-data.org standings response is invalid")
            fetched_at = now_utc.isoformat()
            payload = dict(payload)
            payload["fetched_at"] = fetched_at
            self._club_write_cached_payload(
                current_path,
                last_good_path,
                CLUB_FOOTBALL_STANDINGS_CACHE_VERSION,
                league_code,
                payload,
                fetched_at,
            )
            return payload, "STANDINGS LIVE", fetched_at
        except Exception:
            if cache:
                return cache["payload"], "STANDINGS STALE", cache.get("fetched_at")
            raise

    def _load_club_football_data(
        self,
        settings,
        device_config,
        timezone_info,
        now,
    ):
        enabled_leagues = self._club_football_enabled_leagues(settings)
        api_key = self._football_data_key(settings, device_config)
        by_league = {}
        standings = {}
        freshness_values = []
        fresh_provider_count = 0
        usable_event_count = 0
        expected_provider_count = len(enabled_leagues) * 2

        for league_code in enabled_leagues:
            espn_payload = {}
            football_payload = {}
            try:
                espn_payload, espn_state, espn_fetched_at = (
                    self._load_club_espn_league_payload(
                        league_code, settings, timezone_info, now
                    )
                )
                if espn_state.endswith("LIVE"):
                    fresh_provider_count += 1
                if espn_fetched_at:
                    freshness_values.append(espn_fetched_at)
            except Exception as exc:
                logger.warning(
                    "Club football ESPN %s failed: %s",
                    league_code,
                    _safe_exception_text(exc),
                )

            if api_key:
                try:
                    football_payload, football_state, football_fetched_at = (
                        self._load_club_football_data_league_payload(
                            league_code,
                            settings,
                            api_key,
                            timezone_info,
                            now,
                        )
                    )
                    if football_state.endswith("LIVE"):
                        fresh_provider_count += 1
                    if football_fetched_at:
                        freshness_values.append(football_fetched_at)
                except Exception as exc:
                    logger.warning(
                        "Club football football-data.org %s failed: %s",
                        league_code,
                        _safe_exception_text(exc),
                    )
                football_events = self._parse_club_football_data_events(
                    league_code,
                    football_payload.get("matches") or [],
                    timezone_info,
                )
                try:
                    standings_payload, _standings_state, standings_fetched_at = (
                        self._load_club_standings_league_payload(
                            league_code,
                            settings,
                            api_key,
                            timezone_info,
                            now,
                            football_events,
                        )
                    )
                    standings[league_code] = standings_payload.get("standings") or []
                    if standings_fetched_at:
                        freshness_values.append(standings_fetched_at)
                except Exception as exc:
                    standings[league_code] = []
                    logger.warning(
                        "Club football standings %s failed: %s",
                        league_code,
                        _safe_exception_text(exc),
                    )
            else:
                football_events = []
                standings[league_code] = []

            espn_events = self._parse_club_espn_events(
                league_code, espn_payload, timezone_info
            )
            merged_events = self._merge_club_football_events(
                football_events, espn_events
            )
            by_league[league_code] = merged_events
            usable_event_count += len(merged_events)

        if (
            expected_provider_count
            and fresh_provider_count == expected_provider_count
        ):
            source_state = "CLUB LIVE"
        elif usable_event_count and fresh_provider_count:
            source_state = "CLUB PARTIAL"
        elif usable_event_count:
            source_state = "CLUB STALE"
        else:
            source_state = "CLUB UNAVAILABLE"

        fetched_at = max(freshness_values) if freshness_values else None
        return by_league, standings, source_state, fetched_at


    def _club_api_football_odds_cache_path(self):
        return self._sports_dashboard_cache_dir() / "club_api_football_odds.json"

    def _club_api_football_odds_state_path(self):
        return self._sports_dashboard_cache_dir() / "club_api_football_odds_state.json"

    def _club_api_football_odds_calls_left(self, settings, now_utc):
        limit = self._int_setting(
            settings,
            "clubFootballOddsDailyLimit",
            DEFAULT_CLUB_FOOTBALL_API_ODDS_DAILY_LIMIT,
            1,
            80,
        )
        try:
            state = self._read_json_file(self._club_api_football_odds_state_path())
        except (OSError, ValueError, TypeError):
            state = {}
        if state.get("date") != now_utc.date().isoformat():
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_club_api_football_odds_call(self, settings, now_utc):
        path = self._club_api_football_odds_state_path()
        try:
            state = self._read_json_file(path)
        except (OSError, ValueError, TypeError):
            state = {}
        today = now_utc.date().isoformat()
        try:
            count = int(state.get("count") or 0) if state.get("date") == today else 0
        except (TypeError, ValueError):
            count = 0
        self._write_json_file(
            path,
            {
                "version": CLUB_FOOTBALL_API_ODDS_CACHE_VERSION,
                "date": today,
                "count": count + 1,
                "updated_at": now_utc.isoformat(),
            },
        )

    def _club_api_football_get_json(
        self, path, params, api_key, settings, now_utc
    ):
        if self._club_api_football_odds_calls_left(settings, now_utc) <= 0:
            raise RuntimeError("Club API-Football odds daily request limit reached")
        session = get_http_session()
        try:
            response = session.get(
                f"{API_FOOTBALL_BASE_URL}{path}",
                params=params,
                headers={"x-apisports-key": api_key, "Accept": "application/json"},
                timeout=25,
            )
        finally:
            self._record_club_api_football_odds_call(settings, now_utc)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("API-Football returned an invalid payload")
        if payload.get("errors"):
            raise RuntimeError(f"API-Football returned errors: {payload.get('errors')}")
        return payload

    @staticmethod
    def _club_api_football_season(start_utc):
        start = SportsDashboard._club_parse_utc(start_utc)
        if start is None:
            return ""
        return str(start.year if start.month >= 7 else start.year - 1)

    def _load_club_api_football_odds_for_event(
        self,
        event,
        settings,
        api_key,
        timezone_info,
        now,
    ):
        now_utc = SportsDashboard._club_parse_utc(now) or datetime.now(timezone.utc)
        event_start = SportsDashboard._club_parse_utc((event or {}).get("start_utc"))
        event_key = str((event or {}).get("event_key") or "").strip()
        if not event_key:
            event_key = SportsDashboard._club_event_key(
                (event or {}).get("league_code"),
                event_start,
                (event or {}).get("home_name"),
                (event or {}).get("away_name"),
            )
        if not event_key:
            return {}

        path = self._club_api_football_odds_cache_path()
        try:
            cache = self._read_json_file(path)
        except (OSError, ValueError, TypeError):
            cache = {}
        if cache.get("version") != CLUB_FOOTBALL_API_ODDS_CACHE_VERSION:
            cache = {}
        entries = dict(cache.get("events") or {})
        cached_entry = entries.get(event_key) or {}
        cached_odds = dict(cached_entry.get("odds") or {})
        fetched_at = SportsDashboard._club_parse_utc(cached_entry.get("fetched_at"))
        cache_hours = self._int_setting(
            settings,
            "clubFootballOddsCacheHours",
            DEFAULT_CLUB_FOOTBALL_API_ODDS_CACHE_HOURS,
            1,
            12,
        )
        force_refresh = self._force_refresh_requested(settings)
        if (
            not force_refresh
            and fetched_at is not None
            and timedelta(0) <= now_utc - fetched_at < timedelta(hours=cache_hours)
        ):
            return cached_odds

        if event_start is None:
            return cached_odds
        if not (
            now_utc - CLUB_FOOTBALL_API_ODDS_LOOKBACK
            <= event_start
            <= now_utc + CLUB_FOOTBALL_API_ODDS_LOOKAHEAD
        ):
            return cached_odds
        if self._club_api_football_odds_calls_left(settings, now_utc) < 2:
            return cached_odds

        league_code = str((event or {}).get("league_code") or "").upper()
        league_id = CLUB_FOOTBALL_LEAGUES.get(league_code, {}).get("api_football_id")
        if not league_id:
            return cached_odds
        fixtures_payload = self._club_api_football_get_json(
            "/fixtures",
            {
                "league": league_id,
                "season": self._club_api_football_season(event_start),
                "date": event_start.date().isoformat(),
                "timezone": "UTC",
            },
            api_key,
            settings,
            now_utc,
        )
        fixture_id = SportsDashboard._club_api_football_fixture_id(
            event, fixtures_payload
        )
        odds = {}
        if fixture_id:
            odds_payload = self._club_api_football_get_json(
                "/odds",
                {"fixture": fixture_id, "bet": 1},
                api_key,
                settings,
                now_utc,
            )
            odds = SportsDashboard._club_parse_api_football_odds(odds_payload)

        entries[event_key] = {
            "fetched_at": now_utc.isoformat(),
            "fixture_id": fixture_id,
            "odds": odds,
        }
        if len(entries) > 100:
            entries = dict(
                sorted(
                    entries.items(),
                    key=lambda item: str((item[1] or {}).get("fetched_at") or ""),
                )[-100:]
            )
        self._write_json_file(
            path,
            {
                "version": CLUB_FOOTBALL_API_ODDS_CACHE_VERSION,
                "fetched_at": now_utc.isoformat(),
                "events": entries,
            },
        )
        return odds

    def _attach_club_api_football_odds(
        self,
        selected,
        settings,
        device_config,
        timezone_info,
        now,
    ):
        if not isinstance(selected, Mapping) or not self._bool_setting(
            settings, "clubFootballApiOddsEnabled", True
        ):
            return selected
        api_key = self._api_sports_key(settings, device_config)
        if not api_key:
            return selected

        candidates = list(selected.get("rail") or []) + [selected.get("focus")]
        seen = set()
        for event in candidates:
            if not isinstance(event, dict) or event.get("no_schedule"):
                continue
            identity = event.get("event_key") or id(event)
            if identity in seen:
                continue
            seen.add(identity)
            if SportsDashboard._club_event_has_complete_odds(event):
                continue
            try:
                fallback = self._load_club_api_football_odds_for_event(
                    event,
                    settings,
                    api_key,
                    timezone_info,
                    now,
                )
            except Exception as exc:
                logger.warning(
                    "Club API-Football odds fallback failed for %s: %s",
                    event.get("event_key") or event.get("league_code"),
                    _safe_exception_text(exc),
                )
                continue
            merged = SportsDashboard._club_apply_odds_fallback(event, fallback)
            event.clear()
            event.update(merged)
        return selected

    @staticmethod
    def _club_standings_cache_seconds(events, now):
        if not isinstance(now, datetime):
            return CLUB_FOOTBALL_NORMAL_CACHE_SECONDS
        for event in events or []:
            start = (event or {}).get("start_utc")
            if isinstance(start, datetime) and start.astimezone(now.tzinfo).date() == now.date():
                return 60 * 60
        return CLUB_FOOTBALL_NORMAL_CACHE_SECONDS

    @staticmethod
    def _club_event_selection_priority(event, now):
        status = str((event or {}).get("status") or "").upper()
        confirmed = bool((event or {}).get("provider_status_confirmed"))
        start = (event or {}).get("start_utc")
        if status == "LIVE" and confirmed:
            return 0
        if status == "SCHEDULED" and isinstance(start, datetime):
            if start >= now or start + CLUB_FOOTBALL_DEFAULT_MATCH_WINDOW > now:
                return 1
        if status == "FINAL":
            return 2
        return 3

    @staticmethod
    def _select_club_league_event(events, now):
        if not isinstance(now, datetime):
            return None
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now_utc = now.astimezone(timezone.utc)
        candidates = []
        for raw_event in events or []:
            if not isinstance(raw_event, Mapping):
                continue
            event = dict(raw_event)
            start = event.get("start_utc")
            if isinstance(start, datetime):
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                start = start.astimezone(timezone.utc)
                event["start_utc"] = start
            status = str(event.get("status") or "").upper()
            inferred = bool(
                status == "SCHEDULED"
                and isinstance(start, datetime)
                and start <= now_utc < start + CLUB_FOOTBALL_DEFAULT_MATCH_WINDOW
            )
            event["inferred_live_window"] = inferred
            priority = ClubFootballMixin._club_event_selection_priority(event, now_utc)
            event["_selection_priority"] = priority
            if isinstance(start, datetime):
                if priority == 0:
                    order_value = -start.timestamp()
                elif priority == 1:
                    order_value = abs((start - now_utc).total_seconds())
                elif priority == 2:
                    order_value = -start.timestamp()
                else:
                    order_value = abs((start - now_utc).total_seconds())
            else:
                order_value = float("inf")
            candidates.append((priority, order_value, str(event.get("event_id") or ""), event))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[:3])
        return candidates[0][3]

    @staticmethod
    def _select_club_football_events(by_league, enabled_leagues, now, rotation_seed):
        rail = []
        focus_candidates = []
        for league_code in enabled_leagues or ():
            league = CLUB_FOOTBALL_LEAGUES.get(league_code, {})
            event = ClubFootballMixin._select_club_league_event(
                (by_league or {}).get(league_code) or [], now
            )
            if event is None:
                event = {
                    "event_id": "",
                    "league_code": league_code,
                    "league_name": league.get("name") or league_code,
                    "status": "NO SCHEDULE",
                    "provider_status_confirmed": False,
                    "inferred_live_window": False,
                    "no_schedule": True,
                    "_selection_priority": 99,
                }
            else:
                event["league_code"] = league_code
                event.setdefault("league_name", league.get("name") or league_code)
                event["no_schedule"] = False
                focus_candidates.append(event)
            rail.append(event)

        if not focus_candidates:
            return {"focus": None, "rail": rail, "priority": "NO SCHEDULE"}

        best_priority = min(event.get("_selection_priority", 3) for event in focus_candidates)
        equal_priority = [
            event
            for event in focus_candidates
            if event.get("_selection_priority", 3) == best_priority
        ]
        focus_index = int(rotation_seed or 0) % len(equal_priority)
        previous_league = getattr(rotation_seed, "previous_league", None)
        if (
            previous_league
            and len(equal_priority) > 1
            and equal_priority[focus_index].get("league_code") == previous_league
        ):
            focus_index = (focus_index + 1) % len(equal_priority)
        focus = equal_priority[focus_index]
        priority_labels = {0: "LIVE", 1: "UPCOMING", 2: "FINAL", 3: "OTHER"}
        return {
            "focus": focus,
            "rail": rail,
            "priority": priority_labels.get(best_priority, "OTHER"),
        }

    def _club_football_rotation_state_path(self):
        return self._sports_dashboard_cache_dir() / "club_football_rotation_state.json"

    def _club_football_rotation_seed(self, now):
        current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current_utc = current.astimezone(timezone.utc)
        base_seed = int(current_utc.timestamp() // CLUB_FOOTBALL_MATCHDAY_CACHE_SECONDS)
        previous_league = None
        try:
            state = self._read_json_file(self._club_football_rotation_state_path())
        except (OSError, ValueError, TypeError):
            state = {}
        if isinstance(state, Mapping) and state.get("version") == CLUB_FOOTBALL_ROTATION_STATE_VERSION:
            candidate = str(state.get("last_focus_league") or "").upper()
            if candidate in CLUB_FOOTBALL_LEAGUES:
                previous_league = candidate
        return _ClubFootballRotationSeed(base_seed, previous_league)

    def _write_club_football_rotation_state(self, selected, now):
        focus = (selected or {}).get("focus") if isinstance(selected, Mapping) else None
        league_code = str((focus or {}).get("league_code") or "").upper()
        if league_code not in CLUB_FOOTBALL_LEAGUES:
            return
        current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        self._write_json_file(
            self._club_football_rotation_state_path(),
            {
                "version": CLUB_FOOTBALL_ROTATION_STATE_VERSION,
                "last_focus_league": league_code,
                "updated_at": current.astimezone(timezone.utc).isoformat(),
            },
        )

    @staticmethod
    def _club_football_live_activity(selected, now):
        if not isinstance(selected, Mapping) or not isinstance(now, datetime):
            return None, []
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now_utc = now.astimezone(timezone.utc)
        events = []
        seen = set()
        for event in list(selected.get("rail") or []) + [selected.get("focus")]:
            if not isinstance(event, Mapping) or event.get("no_schedule"):
                continue
            identity = event.get("event_id") or id(event)
            if identity in seen:
                continue
            seen.add(identity)
            start = event.get("start_utc")
            if isinstance(start, datetime):
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                start = start.astimezone(timezone.utc)
            status = str(event.get("status") or "").upper()
            confirmed_live = status == "LIVE" and bool(event.get("provider_status_confirmed"))
            if not isinstance(start, datetime) and not confirmed_live:
                continue
            event_end = (
                start + CLUB_FOOTBALL_DEFAULT_MATCH_WINDOW
                if isinstance(start, datetime)
                else now_utc + CLUB_FOOTBALL_LIVE_CACHE_SECONDS * timedelta(seconds=1)
            )
            if confirmed_live and event_end <= now_utc:
                event_end = now_utc + timedelta(seconds=CLUB_FOOTBALL_LIVE_CACHE_SECONDS)
            events.append(
                {
                    "league_code": str(event.get("league_code") or ""),
                    "start": start,
                    "end": event_end,
                    "confirmed_live": confirmed_live,
                    "scheduled": status == "SCHEDULED",
                }
            )

        refresh_until = None
        active_leagues = []
        for item in events:
            start = item["start"]
            starts_activity = item["confirmed_live"] or (
                item["scheduled"]
                and isinstance(start, datetime)
                and start - CLUB_FOOTBALL_PREGAME_WINDOW <= now_utc < item["end"]
            )
            if not starts_activity:
                continue
            if refresh_until is None or item["end"] > refresh_until:
                refresh_until = item["end"]
            if item["league_code"] and item["league_code"] not in active_leagues:
                active_leagues.append(item["league_code"])

        if refresh_until is None:
            return None, []

        changed = True
        while changed:
            changed = False
            for item in events:
                start = item["start"]
                if not isinstance(start, datetime):
                    continue
                if start - CLUB_FOOTBALL_PREGAME_WINDOW <= refresh_until and item["end"] > refresh_until:
                    refresh_until = item["end"]
                    changed = True
                    if item["league_code"] and item["league_code"] not in active_leagues:
                        active_leagues.append(item["league_code"])
        return refresh_until, active_leagues

    @staticmethod
    def _club_football_live_refresh_until(selected, now):
        return ClubFootballMixin._club_football_live_activity(selected, now)[0]

    def _club_football_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "club_football_live_state.json"

    def _write_club_football_live_state(self, selected, now, source_state, fetched_at):
        current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current_utc = current.astimezone(timezone.utc)
        live_until, active_leagues = self._club_football_live_activity(selected, current_utc)
        focus = (selected or {}).get("focus") if isinstance(selected, Mapping) else None
        focus = focus if isinstance(focus, Mapping) else {}
        start = focus.get("start_utc")
        if isinstance(start, datetime):
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            start = start.astimezone(timezone.utc).isoformat()
        selected_event = {
            "event_id": focus.get("event_id") or "",
            "league_code": focus.get("league_code") or "",
            "home_name": focus.get("home_name") or "",
            "away_name": focus.get("away_name") or "",
            "home_score": focus.get("home_score"),
            "away_score": focus.get("away_score"),
            "status": focus.get("status") or "",
            "start_utc": start,
            "provider_status_confirmed": bool(focus.get("provider_status_confirmed")),
            "inferred_live_window": bool(focus.get("inferred_live_window")),
        }
        payload = {
            "version": CLUB_FOOTBALL_LIVE_STATE_VERSION,
            "updated_at": current_utc.isoformat(),
            "source_state": str(source_state or ""),
            "has_live": isinstance(live_until, datetime) and current_utc <= live_until,
            "live_until": live_until.astimezone(timezone.utc).isoformat()
            if isinstance(live_until, datetime)
            else None,
            "active_leagues": active_leagues,
            "selected_event": selected_event,
            "provider": str(focus.get("provider") or ""),
            "fetched_at": fetched_at,
            "freshness": {
                "source_state": str(source_state or ""),
                "fetched_at": fetched_at,
            },
        }
        try:
            self._write_json_file(self._club_football_live_state_path(), payload)
            self._write_club_football_rotation_state(selected, current_utc)
        except OSError as exc:
            logger.warning("Failed to write club football live refresh state: %s", exc)
