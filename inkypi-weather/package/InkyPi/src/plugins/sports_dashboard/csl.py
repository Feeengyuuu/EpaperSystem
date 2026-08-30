import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from .common import ESPN_SITE_API_BASE_URL, get_http_session


CSL_SCOREBOARD_LOOKBACK_DAYS = 7
CSL_SCOREBOARD_LOOKAHEAD_DAYS = 7
CSL_VISIBLE_MATCH_LIMIT = 4
CSL_SCOREBOARD_EVENT_LIMIT = 100
CSL_SCOREBOARD_URL = f"{ESPN_SITE_API_BASE_URL}/sports/soccer/chn.1/scoreboard"
CSL_SCOREBOARD_STATE_VERSION = "sports-dashboard-csl-scoreboard-v1"
CSL_REQUEST_STATE_VERSION = "sports-dashboard-csl-requests-v1"
CSL_LIVE_STATE_VERSION = "sports-dashboard-csl-live-v1"
DEFAULT_CSL_CACHE_HOURS = 6
DEFAULT_CSL_MATCHDAY_CACHE_MINUTES = 15
DEFAULT_CSL_LIVE_REFRESH_SECONDS = 60
DEFAULT_CSL_DAILY_LIMIT = 720
CSL_LIVE_PREGAME_WINDOW = timedelta(minutes=30)
CSL_MATCH_WINDOW = timedelta(hours=3)
CSL_CONFIRMED_LIVE_ROLLING_WINDOW = timedelta(minutes=5)
CSL_CONFIRMED_LIVE_SOURCE_MAX_AGE = timedelta(minutes=15)
CSL_CONFIRMED_LIVE_SOURCE_FUTURE_SKEW = timedelta(minutes=5)
CSL_LIVE_STATES = {"1H", "2H", "HT", "LIVE"}
CSL_FINISHED_STATES = {"FT"}

CSL_TEAM_NAMES_ZH = {
    "2052": "北京国安",
    "21355": "成都蓉城",
    "131704": "重庆铜梁龙",
    "22537": "大连英博",
    "8240": "河南队",
    "131705": "辽宁铁人",
    "21910": "青岛海牛",
    "22198": "青岛西海岸",
    "7521": "山东泰山",
    "15515": "上海海港",
    "977": "上海申花",
    "22199": "深圳新鹏城",
    "8239": "天津津门虎",
    "21506": "武汉三镇",
    "22536": "云南玉昆",
    "18203": "浙江队",
}

CSL_TEAM_CODES = {
    "2052": "BG",
    "21355": "CHE",
    "131704": "CHO",
    "22537": "DYI",
    "8240": "HEN",
    "131705": "LIA",
    "21910": "QIN",
    "22198": "QWC",
    "7521": "SHT",
    "15515": "SIPG",
    "977": "SHE",
    "22199": "SHX",
    "8239": "TIG",
    "21506": "WTT",
    "22536": "YUN",
    "18203": "ZHE",
}

CSL_TEAM_LOGO_FALLBACKS = {
    "131704": (
        "https://cdn.sanity.io/images/11hmdf08/production/"
        "c035439e1db9e8dcdf65c1693067f9c0c50f492c-800x800.png"
        "?fm=webp&q=80&w=800"
    ),
    "131705": (
        "https://cdn.sanity.io/images/11hmdf08/production/"
        "50f2cb350ae1447fa1f18814a6435be9a2c880cd-800x800.png"
        "?fm=webp&q=80&w=800"
    ),
}


class CSLMixin:
    @staticmethod
    def _csl_scoreboard_date_range(timezone_info, now_utc):
        local_date = now_utc.astimezone(timezone_info).date()
        return (
            local_date - timedelta(days=CSL_SCOREBOARD_LOOKBACK_DAYS),
            local_date + timedelta(days=CSL_SCOREBOARD_LOOKAHEAD_DAYS),
        )

    def _load_csl_scoreboard(self, settings, timezone_info, now=None):
        now_utc = CSLMixin._csl_normalize_utc(now)
        cache_path = self._csl_scoreboard_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._csl_scoreboard_cache_key(timezone_info, now_utc)
        has_compatible_cache = (
            isinstance(cache, Mapping)
            and cache.get("version") == CSL_SCOREBOARD_STATE_VERSION
            and cache.get("cache_key") == cache_key
            and self._csl_scoreboard_payload_is_valid(cache.get("scoreboard"))
        )
        if (
            has_compatible_cache
            and not self._force_refresh_requested(settings)
            and self._csl_scoreboard_cache_is_fresh(
                cache,
                settings,
                timezone_info,
                now_utc,
            )
        ):
            return (
                cache["scoreboard"],
                "CSL ESPN CACHE",
                cache.get("fetched_at"),
            )
        fallback_cache = cache if self._csl_cache_has_scoreboard(cache) else None
        if fallback_cache is None:
            last_good = self._read_json_file(self._csl_scoreboard_last_good_path())
            if self._csl_cache_has_scoreboard(last_good):
                fallback_cache = last_good
        return self._fetch_and_cache_csl_scoreboard(
            settings,
            timezone_info,
            now_utc,
            cache_key,
            fallback_cache,
        )

    def _csl_scoreboard_cache_path(self):
        return self._sports_dashboard_cache_dir() / "csl_espn.json"

    def _csl_scoreboard_last_good_path(self):
        return self._sports_dashboard_cache_dir() / "csl_espn.last_good.json"

    def _csl_request_state_path(self):
        return self._sports_dashboard_cache_dir() / "csl_espn_requests.json"

    def _csl_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "csl_live_state.json"

    @staticmethod
    def _csl_scoreboard_payload_is_valid(scoreboard):
        return isinstance(scoreboard, Mapping) and isinstance(
            scoreboard.get("events"),
            list,
        )

    @staticmethod
    def _csl_cache_has_scoreboard(cache):
        return bool(
            isinstance(cache, Mapping)
            and cache.get("version") == CSL_SCOREBOARD_STATE_VERSION
            and CSLMixin._csl_scoreboard_payload_is_valid(cache.get("scoreboard"))
        )

    def _fetch_and_cache_csl_scoreboard(
        self,
        settings,
        timezone_info,
        now_utc,
        cache_key,
        fallback_cache,
    ):
        if self._csl_calls_left(settings, now_utc) <= 0:
            if fallback_cache is not None:
                return (
                    fallback_cache["scoreboard"],
                    "CSL ESPN STALE",
                    fallback_cache.get("fetched_at"),
                )
            return {}, "CSL ESPN LIMIT", None

        start_date, end_date = self._csl_scoreboard_date_range(
            timezone_info,
            now_utc,
        )
        try:
            session = self._csl_http_session()
            try:
                response = session.get(
                    CSL_SCOREBOARD_URL,
                    params={
                        "dates": (f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"),
                        "limit": str(CSL_SCOREBOARD_EVENT_LIMIT),
                    },
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "InkyPi/1.0",
                    },
                    timeout=20,
                )
                response.raise_for_status()
                scoreboard = response.json()
            finally:
                self._record_csl_call(now_utc)
            if not self._csl_scoreboard_payload_is_valid(scoreboard):
                raise ValueError("CSL ESPN scoreboard response has no valid events list")
        except Exception:
            if fallback_cache is not None:
                return (
                    fallback_cache["scoreboard"],
                    "CSL ESPN STALE",
                    fallback_cache.get("fetched_at"),
                )
            raise

        wrapper = {
            "version": CSL_SCOREBOARD_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "range_start": start_date.isoformat(),
            "range_end": end_date.isoformat(),
            "scoreboard": dict(scoreboard),
        }
        self._write_json_file(self._csl_scoreboard_cache_path(), wrapper)
        self._write_json_file(self._csl_scoreboard_last_good_path(), wrapper)
        return wrapper["scoreboard"], "CSL ESPN LIVE", wrapper["fetched_at"]

    def _csl_http_session(self):
        return get_http_session()

    def _csl_calls_left(self, settings, now_utc):
        limit = CSLMixin._csl_setting_int(
            settings,
            "cslEspnDailyLimit",
            DEFAULT_CSL_DAILY_LIMIT,
            1,
            1440,
        )
        state = self._read_json_file(self._csl_request_state_path())
        if state.get("version") != CSL_REQUEST_STATE_VERSION or state.get("date") != now_utc.date().isoformat():
            return limit
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return max(0, limit - count)

    def _record_csl_call(self, now_utc):
        path = self._csl_request_state_path()
        state = self._read_json_file(path)
        today = now_utc.date().isoformat()
        if state.get("version") != CSL_REQUEST_STATE_VERSION or state.get("date") != today:
            count = 0
        else:
            try:
                count = int(state.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
        self._write_json_file(
            path,
            {
                "version": CSL_REQUEST_STATE_VERSION,
                "date": today,
                "count": count + 1,
                "updated_at": now_utc.isoformat(),
            },
        )

    @staticmethod
    def _csl_live_refresh_until(
        selected,
        now,
        source_state="",
        fetched_at=None,
    ):
        if not isinstance(selected, Mapping) or not isinstance(now, datetime):
            return None
        now_utc = CSLMixin._csl_normalize_utc(now)
        can_roll_live_confirmation = (
            CSLMixin._csl_source_has_fresh_live_confirmation(
                source_state,
                fetched_at,
                now_utc,
            )
        )
        refresh_until = None
        for event in selected.get("live") or []:
            start = CSLMixin._csl_normalize_event_start(event.get("start"))
            event_until = start + CSL_MATCH_WINDOW if start is not None else now_utc + CSL_MATCH_WINDOW
            if can_roll_live_confirmation:
                event_until = max(
                    event_until,
                    now_utc + CSL_CONFIRMED_LIVE_ROLLING_WINDOW,
                )
            if refresh_until is None or event_until > refresh_until:
                refresh_until = event_until

        for event in selected.get("upcoming") or []:
            start = CSLMixin._csl_normalize_event_start(event.get("start"))
            if start is None:
                continue
            event_until = start + CSL_MATCH_WINDOW
            if refresh_until is None:
                if start - CSL_LIVE_PREGAME_WINDOW <= now_utc < event_until:
                    refresh_until = event_until
                continue
            if start - CSL_LIVE_PREGAME_WINDOW <= refresh_until and event_until > refresh_until:
                refresh_until = event_until
        return refresh_until

    def _write_csl_live_state(self, selected, now, source_state, fetched_at):
        now_utc = CSLMixin._csl_normalize_utc(now)
        live = (selected or {}).get("live") or []
        event = live[0] if live else None
        live_until = CSLMixin._csl_live_refresh_until(
            selected,
            now_utc,
            source_state,
            fetched_at,
        )
        payload = {
            "version": CSL_LIVE_STATE_VERSION,
            "updated_at": now_utc.isoformat(),
            "source_state": str(source_state or ""),
            "fetched_at": fetched_at,
            "has_live": bool(isinstance(live_until, datetime) and now_utc <= live_until),
            "live_until": live_until.isoformat() if isinstance(live_until, datetime) else None,
        }
        if event:
            start = CSLMixin._csl_normalize_event_start(event.get("start"))
            score_a = event.get("wins_a")
            score_b = event.get("wins_b")
            score = f"{score_a}-{score_b}" if score_a is not None and score_b is not None else "vs"
            payload.update(
                {
                    "event_id": str(event.get("event_id") or ""),
                    "team_a": str(event.get("team_a") or ""),
                    "team_b": str(event.get("team_b") or ""),
                    "score": score,
                    "state": str(event.get("state") or ""),
                    "status": str(event.get("status") or ""),
                    "started_at": start.isoformat() if start else None,
                    "provider": str(event.get("provider") or ""),
                    "score_source": str(event.get("score_source") or ""),
                    "provider_status_confirmed": bool(event.get("provider_status_confirmed")),
                    "score_confirmed": bool(event.get("score_confirmed")),
                }
            )
        self._write_json_file(self._csl_live_state_path(), payload)

    @staticmethod
    def _csl_normalize_event_start(value):
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _csl_scoreboard_cache_key(timezone_info, now_utc):
        start_date, end_date = CSLMixin._csl_scoreboard_date_range(
            timezone_info,
            now_utc,
        )
        return "|".join(
            [
                CSL_SCOREBOARD_STATE_VERSION,
                CSL_SCOREBOARD_URL,
                start_date.isoformat(),
                end_date.isoformat(),
                getattr(timezone_info, "key", str(timezone_info)),
                str(CSL_SCOREBOARD_EVENT_LIMIT),
            ]
        )

    def _csl_scoreboard_cache_is_fresh(
        self,
        cache,
        settings,
        timezone_info,
        now_utc,
    ):
        fetched_at = CSLMixin._csl_parse_utc(cache.get("fetched_at"))
        if fetched_at is None or fetched_at > now_utc:
            return False
        events = CSLMixin._parse_csl_espn_events(
            cache.get("scoreboard") or {},
            timezone_info,
        )
        local_now = now_utc.astimezone(timezone_info)
        has_live_poll_candidate = any(
            (str(event.get("state") or "").upper() in CSL_LIVE_STATES and event.get("provider_status_confirmed"))
            or (
                str(event.get("state") or "").upper() not in CSL_FINISHED_STATES
                and event["start"] - CSL_LIVE_PREGAME_WINDOW <= local_now < event["start"] + CSL_MATCH_WINDOW
            )
            for event in events
        )
        if has_live_poll_candidate:
            seconds = CSLMixin._csl_setting_int(
                settings,
                "cslLiveRefreshSeconds",
                DEFAULT_CSL_LIVE_REFRESH_SECONDS,
                30,
                900,
            )
        elif any(event["start"].date() == local_now.date() for event in events):
            seconds = (
                CSLMixin._csl_setting_int(
                    settings,
                    "cslMatchdayCacheMinutes",
                    DEFAULT_CSL_MATCHDAY_CACHE_MINUTES,
                    1,
                    60,
                )
                * 60
            )
        else:
            seconds = (
                CSLMixin._csl_setting_int(
                    settings,
                    "cslCacheHours",
                    DEFAULT_CSL_CACHE_HOURS,
                    1,
                    24,
                )
                * 60
                * 60
            )
        return now_utc - fetched_at <= timedelta(seconds=seconds)

    @staticmethod
    def _csl_normalize_utc(value):
        current = value if isinstance(value, datetime) else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    @staticmethod
    def _csl_parse_utc(value):
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _csl_source_has_fresh_live_confirmation(
        source_state,
        fetched_at,
        now,
    ):
        state = str(source_state or "").strip().upper()
        if state not in {"CSL ESPN LIVE", "CSL ESPN CACHE"}:
            return False
        fetched_utc = CSLMixin._csl_parse_utc(fetched_at)
        if fetched_utc is None:
            return False
        now_utc = CSLMixin._csl_normalize_utc(now)
        age = now_utc - fetched_utc
        return (
            -CSL_CONFIRMED_LIVE_SOURCE_FUTURE_SKEW
            <= age
            <= CSL_CONFIRMED_LIVE_SOURCE_MAX_AGE
        )

    @staticmethod
    def _csl_setting_int(settings, key, default, minimum, maximum):
        try:
            value = int((settings or {}).get(key) or default)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _parse_csl_espn_events(payload, timezone_info):
        league_logo_url = ""
        for league in (payload or {}).get("leagues") or []:
            if str((league or {}).get("slug") or "") == "chn.1":
                league_logo_url = CSLMixin._csl_first_logo(league)
                break
        if not league_logo_url and (payload or {}).get("leagues"):
            league_logo_url = CSLMixin._csl_first_logo(payload["leagues"][0])

        parsed = []
        for raw_event in (payload or {}).get("events") or []:
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

            start = CSLMixin._csl_parse_start(
                competition.get("date") or raw_event.get("date"),
                timezone_info,
            )
            if start is None:
                continue

            status = competition.get("status") or raw_event.get("status") or {}
            state = CSLMixin._csl_espn_event_state(status)
            show_score = state in CSL_LIVE_STATES.union(CSL_FINISHED_STATES)
            home_info = CSLMixin._csl_team_info(home, show_score)
            away_info = CSLMixin._csl_team_info(away, show_score)
            source_url = CSLMixin._csl_event_url(raw_event, competition)
            season = str(((raw_event or {}).get("season") or {}).get("year") or start.year)
            detail = str(
                (status.get("type") or {}).get("shortDetail") or (status.get("type") or {}).get("detail") or state
            ).strip()
            elapsed = CSLMixin._csl_elapsed(status)
            row = {
                "event_id": str((raw_event or {}).get("id") or competition.get("id") or "").strip(),
                "start": start,
                "state": state,
                "status": detail if show_score else start.strftime("%H:%M"),
                "elapsed": elapsed,
                "team_a": home_info["display_name"],
                "team_b": away_info["display_name"],
                "team_a_tla": home_info["code"],
                "team_b_tla": away_info["code"],
                "team_a_source_name": home_info["source_name"],
                "team_b_source_name": away_info["source_name"],
                "team_a_source_aliases": home_info["aliases"],
                "team_b_source_aliases": away_info["aliases"],
                "team_a_flag": home_info["logo_url"],
                "team_b_flag": away_info["logo_url"],
                "wins_a": home_info["score"],
                "wins_b": away_info["score"],
                "block": CSLMixin._csl_event_block(raw_event, competition),
                "score_source": "ESPN",
                "provider": "ESPN",
                "source_url": source_url,
                "provider_status_confirmed": state in CSL_LIVE_STATES.union(CSL_FINISHED_STATES),
                "score_confirmed": bool(
                    show_score and home_info["score"] is not None and away_info["score"] is not None
                ),
                "league_logo_url": league_logo_url,
                "season": season,
            }
            odds = CSLMixin._csl_espn_moneyline_odds(competition)
            if odds:
                row["odds"] = odds
            parsed.append(row)
        return sorted(parsed, key=lambda item: item["start"])

    @staticmethod
    def _csl_event_is_current_live(
        event,
        now,
        source_state="",
        fetched_at=None,
    ):
        if not isinstance(event, Mapping):
            return False
        if str(event.get("state") or "").upper() not in CSL_LIVE_STATES:
            return False
        if not bool(event.get("provider_status_confirmed")):
            return False
        start = CSLMixin._csl_normalize_event_start(event.get("start"))
        if start is None:
            return False
        now_utc = CSLMixin._csl_normalize_utc(now)
        if (
            start - CSL_LIVE_PREGAME_WINDOW
            <= now_utc
            < start + CSL_MATCH_WINDOW
        ):
            return True
        if not CSLMixin._csl_source_has_fresh_live_confirmation(
            source_state,
            fetched_at,
            now_utc,
        ):
            return False
        return (
            now_utc - timedelta(days=CSL_SCOREBOARD_LOOKBACK_DAYS)
            <= start
            <= now_utc
        )

    @staticmethod
    def _select_csl_event_sections(
        events,
        now,
        visible_matches=4,
        *,
        source_state="",
        fetched_at=None,
    ):
        candidates = [
            event for event in events or [] if isinstance(event, Mapping) and isinstance(event.get("start"), datetime)
        ]
        live = sorted(
            [
                event
                for event in candidates
                if CSLMixin._csl_event_is_current_live(
                    event,
                    now,
                    source_state,
                    fetched_at,
                )
            ],
            key=lambda event: event["start"],
            reverse=True,
        )
        upcoming = sorted(
            [
                event
                for event in candidates
                if event not in live
                and str(event.get("state") or "").upper() not in CSL_FINISHED_STATES
                and event["start"] + CSL_MATCH_WINDOW > now
            ],
            key=lambda event: event["start"],
        )
        recent = sorted(
            [
                event
                for event in candidates
                if event not in live
                and event not in upcoming
                and str(event.get("state") or "").upper() in CSL_FINISHED_STATES
            ],
            key=lambda event: event["start"],
            reverse=True,
        )
        main = live[0] if live else (upcoming[0] if upcoming else (recent[0] if recent else None))
        visible = max(1, min(CSL_VISIBLE_MATCH_LIMIT, int(visible_matches or 4)))
        season = next(
            (str(event.get("season")) for event in candidates if str(event.get("season") or "").isdigit()),
            str(now.year),
        )
        league_logo_url = next(
            (
                str(event.get("league_logo_url") or "")
                for event in candidates
                if str(event.get("league_logo_url") or "")
            ),
            "",
        )
        return {
            "live": live,
            "upcoming": upcoming,
            "recent": recent,
            "main": main,
            "visible_matches": visible,
            "season": season,
            "presentation": {
                "competition": "csl",
                "title": f"{season} 中超联赛",
                "league_logo_url": league_logo_url,
                "team_asset_kind": "logo",
                "empty_schedule_text": "暂无中超赛程",
                "upcoming_empty_text": "暂无后续中超赛程",
                "recent_empty_text": "暂无近期赛果",
                "show_worldcup_banner": False,
                "show_five_leagues_filler": False,
                "upcoming_max_rows": 2,
                "upcoming_row_gap": 0,
                "main_team_logo_scale": 1.4,
                "main_team_name_max_size": 14,
                "main_team_name_min_size": 7,
                "main_team_points_offset": 11,
                "main_team_odds_offset": 6,
            },
        }

    @staticmethod
    def _csl_schedule_summary(
        events,
        now,
        *,
        source_state="",
        fetched_at=None,
    ):
        selected = CSLMixin._select_csl_event_sections(
            events,
            now,
            source_state=source_state,
            fetched_at=fetched_at,
        )
        candidates = [
            event for event in events or [] if isinstance(event, Mapping) and isinstance(event.get("start"), datetime)
        ]
        if not candidates:
            return None
        ordered = sorted(candidates, key=lambda event: event["start"])
        upcoming = selected.get("upcoming") or []
        recent = selected.get("recent") or []
        main = selected.get("main") or {}
        now_utc = CSLMixin._csl_normalize_utc(now)
        relevant_start = now_utc - timedelta(days=CSL_SCOREBOARD_LOOKBACK_DAYS)
        relevant_end = now_utc + timedelta(days=CSL_SCOREBOARD_LOOKAHEAD_DAYS)
        has_relevant_events = False
        for event in candidates:
            start = CSLMixin._csl_normalize_event_start(event.get("start"))
            state = str(event.get("state") or "").upper()
            if state in CSL_LIVE_STATES:
                is_relevant = event in (selected.get("live") or [])
            elif state in CSL_FINISHED_STATES:
                is_relevant = relevant_start <= start <= now_utc
            else:
                is_relevant = (
                    now_utc < start + CSL_MATCH_WINDOW
                    and start <= relevant_end
                )
            if is_relevant:
                has_relevant_events = True
                break
        return {
            "active": has_relevant_events,
            "has_relevant_events": has_relevant_events,
            "has_live": bool(selected.get("live")),
            "main_event_id": str(main.get("event_id") or ""),
            "main_start": main.get("start"),
            "selection_priority": (
                "LIVE"
                if main in (selected.get("live") or [])
                else (
                    "UPCOMING"
                    if main in upcoming
                    else ("FINAL" if main in recent else "OTHER")
                )
            ),
            "first_start": ordered[0]["start"],
            "last_start": ordered[-1]["start"],
            "final_end": ordered[-1]["start"] + CSL_MATCH_WINDOW,
            "next_start": upcoming[0]["start"] if upcoming else None,
            "latest_result_start": recent[0]["start"] if recent else None,
            "source_state": str(source_state or ""),
            "fetched_at": fetched_at,
        }

    @staticmethod
    def _csl_parse_start(value, timezone_info):
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone_info)

    @staticmethod
    def _csl_first_logo(item):
        direct = str((item or {}).get("logo") or "").strip()
        if direct:
            return direct
        for logo in (item or {}).get("logos") or []:
            href = str((logo or {}).get("href") or "").strip()
            if href:
                return href
        return ""

    @staticmethod
    def _csl_espn_moneyline_odds(competition):
        for offer in (competition or {}).get("odds") or []:
            if not isinstance(offer, Mapping):
                continue
            moneyline = offer.get("moneyline") or {}
            if not isinstance(moneyline, Mapping):
                continue

            def side_odds(side):
                side_data = moneyline.get(side) or {}
                for snapshot in ("close", "open"):
                    value = (side_data.get(snapshot) or {}).get("odds")
                    formatted = CSLMixin._csl_decimal_odds_from_american(value)
                    if formatted:
                        return formatted
                return ""

            team_a = side_odds("home")
            team_b = side_odds("away")
            if not team_a or not team_b:
                continue
            draw = side_odds("draw")
            if not draw:
                draw = CSLMixin._csl_decimal_odds_from_american((offer.get("drawOdds") or {}).get("moneyLine"))
            provider = offer.get("provider") or {}
            return {
                "team_a": team_a,
                "draw": draw,
                "team_b": team_b,
                "bookmaker": str(provider.get("displayName") or provider.get("name") or "ESPN").strip(),
            }
        return {}

    @staticmethod
    def _csl_decimal_odds_from_american(value):
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            number = float(text)
        except (TypeError, ValueError):
            return ""
        if number == 0:
            return ""
        if text.startswith(("+", "-")) or abs(number) >= 100:
            decimal = 1 + number / 100 if number > 0 else 1 + 100 / abs(number)
        else:
            decimal = number
        return f"{decimal:.2f}" if decimal > 0 else ""

    @staticmethod
    def _csl_team_info(competitor, show_score):
        team = (competitor or {}).get("team") or {}
        team_id = str(team.get("id") or (competitor or {}).get("id") or "").strip()
        source_name = str(team.get("displayName") or team.get("name") or team.get("shortDisplayName") or "TBD").strip()
        short_name = str(team.get("shortDisplayName") or "").strip()
        code = CSLMixin._csl_team_code(team, team_id, source_name)
        aliases = []
        for value in (source_name, short_name, code):
            if value and value not in aliases:
                aliases.append(value)
        return {
            "display_name": CSL_TEAM_NAMES_ZH.get(team_id, source_name),
            "source_name": source_name,
            "aliases": aliases,
            "code": code,
            "logo_url": CSLMixin._csl_first_logo(team) or CSL_TEAM_LOGO_FALLBACKS.get(team_id, ""),
            "score": CSLMixin._csl_int((competitor or {}).get("score")) if show_score else None,
        }

    @staticmethod
    def _csl_team_code(team, team_id, source_name):
        mapped = CSL_TEAM_CODES.get(team_id)
        if mapped:
            return mapped
        raw = re.sub(r"[^A-Za-z0-9]", "", str((team or {}).get("abbreviation") or ""))
        if raw:
            return raw.upper()[:4]
        words = re.findall(r"[A-Za-z0-9]+", source_name or "")
        if len(words) > 1:
            return "".join(word[0] for word in words).upper()[:4]
        if words:
            return words[0].upper()[:3]
        return "TBD"

    @staticmethod
    def _csl_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _csl_espn_event_state(status):
        status_type = (status or {}).get("type") or {}
        state = str(status_type.get("state") or "").strip().lower()
        name = str(status_type.get("name") or "").strip().upper()
        detail = (
            str(status_type.get("shortDetail") or status_type.get("detail") or status_type.get("description") or "")
            .strip()
            .upper()
        )
        combined = " ".join(part for part in (name, detail) if part)
        if (
            status_type.get("completed") is True
            or state == "post"
            or "FULL_TIME" in combined
            or detail in {"FT", "FINAL"}
        ):
            return "FT"
        if "POSTPONED" in combined:
            return "POSTPONED"
        in_progress = state in {"in", "live"} or "IN_PROGRESS" in name
        if not in_progress:
            return "TIMED"
        if "HALFTIME" in combined or "HALF_TIME" in combined or detail == "HT":
            return "HT"
        period = CSLMixin._csl_int((status or {}).get("period"))
        if period == 1:
            return "1H"
        if period == 2:
            return "2H"
        return "LIVE"

    @staticmethod
    def _csl_elapsed(status):
        detail = str(
            ((status or {}).get("type") or {}).get("shortDetail")
            or ((status or {}).get("type") or {}).get("detail")
            or ""
        )
        match = re.search(r"\b(\d{1,3})(?:\+\d{1,2})?['’]?", detail)
        return CSLMixin._csl_int(match.group(1)) if match else None

    @staticmethod
    def _csl_event_url(event, competition):
        for source in (event, competition):
            for link in (source or {}).get("links") or []:
                href = str((link or {}).get("href") or "").strip()
                if href:
                    return href
        event_id = str((event or {}).get("id") or (competition or {}).get("id") or "")
        if event_id:
            return f"https://www.espn.com/soccer/match/_/gameId/{event_id}"
        return ""

    @staticmethod
    def _csl_event_block(event, competition):
        for source in (competition, event):
            for key in ("round", "week", "matchday"):
                value = (source or {}).get(key)
                if isinstance(value, Mapping):
                    for nested_key in (
                        "number",
                        "value",
                        "round",
                        "week",
                        "matchday",
                        "text",
                        "name",
                    ):
                        number = CSLMixin._csl_round_number(value.get(nested_key))
                        if number is not None:
                            return f"中超 · 第{number}轮"
                else:
                    number = CSLMixin._csl_round_number(value)
                    if number is not None:
                        return f"中超 · 第{number}轮"
        return "中超联赛"

    @staticmethod
    def _csl_round_number(value):
        if isinstance(value, bool) or value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            number = int(value)
            return number if number > 0 else None
        match = re.search(r"\b(\d{1,2})\b", str(value))
        if not match:
            return None
        number = int(match.group(1))
        return number if number > 0 else None
