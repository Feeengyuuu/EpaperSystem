from .common import *
from .common import _ACTIVE_COLORS, _safe_exception_text, _normalize_country_alias

SportsDashboard = None


class NBAMixin:
    def _load_nba_events(self, settings, timezone_info):
        try:
            payload, source_state, _fetched_at = self._load_nba_scoreboard(settings, timezone_info)
            events = self._parse_nba_espn_events(payload, timezone_info)
            if events:
                return events, source_state
        except Exception as exc:
            logger.warning("NBA scoreboard fetch failed: %s", exc)
        return self._fallback_nba_events(timezone_info), "NBA FALLBACK"

    @staticmethod
    def _wnba_scoreboard_url(settings):
        value = str((settings or {}).get("wnbaScoreboardUrl") or DEFAULT_WNBA_SCOREBOARD_URL).strip()
        return value or DEFAULT_WNBA_SCOREBOARD_URL

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

        if not force_refresh and self._nba_scoreboard_calls_left(settings, now_utc) <= 0:
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
                "ODDS_API_IO_KEY",
                "Odds_API_IO_KEY",
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
            "ODDS_API_IO_KEY",
            "Odds_API_IO_KEY",
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

    def _nba_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "nba_live_state.json"

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

    def _draw_wnba_title_wordmark(self, image, x, y, max_width, max_height):
        wordmark = self._load_local_logo(
            LOCAL_WNBA_TITLE_WORDMARK_PATH,
            (int(max_width), int(max_height)),
            alpha_threshold=8,
        )
        if not wordmark:
            return False
        paste_x = int(x)
        paste_y = int(y + (int(max_height) - wordmark.height) / 2)
        image.paste(wordmark, (paste_x, paste_y), wordmark)
        return True

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
