from .common import *
from .nba_free_odds import attach_free_odds
from .common import _ACTIVE_COLORS, _safe_exception_text, _normalize_country_alias
from .nba_calendar import load_calendar

SportsDashboard = None


class NBAMixin:
    def _load_nba_events(self, settings, timezone_info):
        try:
            payload, source_state, _fetched_at = self._load_nba_scoreboard(settings, timezone_info)
            events = self._parse_nba_espn_events(payload, timezone_info)
            return events, source_state
        except Exception as exc:
            logger.warning("NBA scoreboard fetch failed: %s", exc)
        return [], "NBA NO DATA"

    @staticmethod
    def _wnba_scoreboard_url(settings):
        value = str((settings or {}).get("wnbaScoreboardUrl") or DEFAULT_WNBA_SCOREBOARD_URL).strip()
        return normalize_espn_site_api_url(value or DEFAULT_WNBA_SCOREBOARD_URL)

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
    def _wnba_series_event_id(payload):
        """The scoreboard's playoff game whose summary lists its series: the live or next one,
        else the latest; "" when no playoff series is on the board."""
        candidates = []
        for event in (payload or {}).get("events") or []:
            competition = (((event or {}).get("competitions") or [{}])[0]) or {}
            series = competition.get("series") if isinstance(competition.get("series"), Mapping) else {}
            event_id = str((event or {}).get("id") or "")
            if series.get("type") != "playoff" or not event_id.isdigit():
                continue
            status = competition.get("status") or event.get("status") or {}
            finished = str((status.get("type") or {}).get("state") or "") == "post"
            candidates.append((finished, str(competition.get("date") or event.get("date") or ""), event_id))
        ahead = sorted(item for item in candidates if not item[0])
        if ahead:
            return ahead[0][2]
        return max(candidates, key=lambda item: item[1])[2] if candidates else ""

    @staticmethod
    def _compact_wnba_series(summary, event_id):
        """The playoff series holding ``event_id`` from an ESPN game summary, kept small: its
        games in order, each with its date, state and both teams' code, name, score and result."""
        for series in (summary or {}).get("seasonseries") or []:
            events = [event for event in (series or {}).get("events") or [] if isinstance(event, Mapping)]
            if (series or {}).get("type") != "playoff" or not any(str(event.get("id")) == event_id for event in events):
                continue
            games = []
            for event in events[:7]:
                teams = []
                for competitor in event.get("competitors") or []:
                    if isinstance(competitor, Mapping):
                        team = competitor.get("team") or {}
                        teams.append({"code": str(team.get("abbreviation") or ""), "name": str(team.get("displayName") or ""),
                                      "score": str(competitor.get("score") or ""), "winner": competitor.get("winner") is True})
                games.append({"id": str(event.get("id") or ""), "date": str(event.get("date") or ""),
                              "state": str((event.get("statusType") or {}).get("state") or event.get("status") or ""),
                              "teams": teams[:2]})
            return {"title": str(series.get("title") or ""), "summary": str(series.get("summary") or ""), "games": games}
        return {}

    @staticmethod
    def _parse_wnba_series(series, timezone_info):
        """The stored series' games: [{number, start, state (final/live/scheduled), codes,
        scores by code, winner code}], with the scoreboard's team codes."""
        parsed = []
        for number, game in enumerate((series or {}).get("games") or [], start=1):
            teams = [team for team in (game or {}).get("teams") or [] if isinstance(team, Mapping)]
            start = SportsDashboard._parse_start_time(game.get("date"), timezone_info) if isinstance(game, Mapping) else None
            if not start or len(teams) != 2:
                continue
            state = {"post": "final", "in": "live"}.get(str(game.get("state") or ""), "scheduled")
            codes = [SportsDashboard._wnba_normalized_team_code(team.get("code"), team.get("name")) for team in teams]
            parsed.append({
                "number": number, "event_id": str(game.get("id") or ""), "start": start, "state": state, "codes": codes,
                "scores": ({code: SportsDashboard._lpl_int_value(team.get("score")) for code, team in zip(codes, teams)}
                           if state != "scheduled" else {}),
                "winner": next((code for code, team in zip(codes, teams) if team.get("winner")), "") if state == "final" else "",
            })
        return parsed

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

        # The merged scoreboard duplicates the calendar's object graph. Keep
        # the durable fallback on disk while loading/updating that calendar.
        # In a small worker, holding both can trip the system resource guard.
        cache = None

        def fallback_cache():
            old = self._read_json_file(cache_path)
            return old if (old.get("cache_key") == cache_key
                           and isinstance(old.get("scoreboard"), dict)) else {}

        try:
            payload = self._fetch_nba_scoreboard_payload(settings, timezone_info, cache_key, now_utc)
        except Exception:
            old = fallback_cache() if has_compatible_cache else {}
            if old:
                return old["scoreboard"], "ESPN STALE", old.get("fetched_at")
            raise

        if payload.get("source_state") == "NBA NO DATA":
            old = fallback_cache() if has_compatible_cache else {}
            if old:
                return old["scoreboard"], "ESPN STALE", old.get("fetched_at")
            return {}, "NBA NO DATA", None
        try:
            if not self._bool_setting(settings, "_inkypi_ewc_cache_only", False):
                self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write NBA scoreboard cache: %s", exc)
        return payload["scoreboard"], payload.get("source_state", "ESPN LIVE"), payload.get("fetched_at")

    def _fetch_nba_scoreboard_payload(self, settings, timezone_info, cache_key, now_utc):
        payload = load_calendar(self, settings, timezone_info, now_utc, get_http_session())
        return {
            **payload,
            "version": NBA_SCOREBOARD_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
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
                    **SportsDashboard._basketball_scorebug_fields(event, competition, away, home, state),
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
    def _basketball_scorebug_fields(event, competition, away, home, state):
        """Scorebug details from the ESPN scoreboard: clock, team colours,
        game leaders, the event headline and the playoff series line."""
        status = (competition or {}).get("status") or (event or {}).get("status") or {}
        status_type = status.get("type") or {}
        series = (competition or {}).get("series") if isinstance((competition or {}).get("series"), Mapping) else {}
        return {
            "clock": str(status.get("displayClock") or "").strip() if state == "inprogress" else "",
            "status_detail": str(status_type.get("shortDetail") or status_type.get("detail") or "").strip(),
            "team_a_color": SportsDashboard._football_team_color(away),
            "team_b_color": SportsDashboard._football_team_color(home),
            "leaders": SportsDashboard._basketball_game_leaders(away, home) if state != "unstarted" else [],
            "team_leaders_a": SportsDashboard._basketball_team_leaders(away) if state != "unstarted" else [],
            "team_leaders_b": SportsDashboard._basketball_team_leaders(home) if state != "unstarted" else [],
            "headline": SportsDashboard._football_event_headline(event, competition),
            "series_summary": str((series or {}).get("summary") or "").strip(),
            "series_length": SportsDashboard._lpl_int_value((series or {}).get("totalCompetitions")),
        }

    @staticmethod
    def _basketball_team_leaders(competitor):
        """This team's points / rebounds / assists leaders."""
        leaders = []
        groups = {
            group.get("name"): group
            for group in (competitor or {}).get("leaders") or []
            if isinstance(group, Mapping)
        }
        for category, label in (("points", "PTS"), ("rebounds", "REB"), ("assists", "AST")):
            top = (((groups.get(category) or {}).get("leaders") or [None])[0]) or {}
            name = str((top.get("athlete") or {}).get("shortName") or "").strip()
            if name:
                leaders.append({"label": label, "name": name, "value": str(top.get("displayValue") or "").strip()})
        return leaders

    @staticmethod
    def _basketball_game_leaders(away, home):
        """The game's best scorer, rebounder and passer across both teams."""
        leaders = []
        for category, label in (("points", "PTS"), ("rebounds", "REB"), ("assists", "AST")):
            best = None
            for side, competitor in (("a", away), ("b", home)):
                for group in (competitor or {}).get("leaders") or []:
                    if not isinstance(group, Mapping) or group.get("name") != category:
                        continue
                    top = ((group.get("leaders") or [None])[0]) or {}
                    try:
                        value = float(top.get("value"))
                    except (TypeError, ValueError):
                        continue
                    name = str((top.get("athlete") or {}).get("shortName") or "").strip()
                    if name and (best is None or value > best[0]):
                        best = (value, {"label": label, "name": name, "value": str(top.get("displayValue") or int(value)), "side": side})
            if best:
                leaders.append(best[1])
        return leaders

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
        guest = NBA_GUEST_TEAMS.get(str(team.get("id") or "").strip()) or {}
        display_name = guest.get("zh_name") or SportsDashboard._nba_display_team_name(code, name, aliases)
        logo = SportsDashboard._espn_team_logo(team) or SportsDashboard._nba_guest_team_logo_url(guest)
        score = SportsDashboard._lpl_int_value((competitor or {}).get("score")) if show_score else None
        periods = SportsDashboard._nba_period_scores(competitor) if show_score else []
        record = SportsDashboard._nba_record_label((competitor or {}).get("records") or [])
        winner = SportsDashboard._espn_competitor_winner(competitor)
        return display_name, name, code, logo, score, periods, record, winner

    @staticmethod
    def _nba_guest_team_logo_url(guest):
        key = (guest or {}).get("logo_key")
        return f"{LOCAL_NBA_GUEST_TEAM_LOGO_SCHEME}{key}" if key else ""

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
        # A game past tip-off that the feed has not confirmed live or final is
        # still pending, not a result: keep it upcoming for the inferred live
        # window so RECENT only ever shows scored, finished games.
        upcoming = [
            event for event in events
            if not SportsDashboard._is_nba_live_event(event)
            and not SportsDashboard._is_nba_finished_event(event)
            and not SportsDashboard._is_nba_series_decided_placeholder(event)
            and event["start"] + NBA_INFERRED_LIVE_WINDOW > now
        ]
        recent = sorted(
            [event for event in events if SportsDashboard._is_nba_finished_event(event)],
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
        return normalize_espn_site_api_url(value or DEFAULT_NBA_SCOREBOARD_URL)

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
        if cache.get("source_state") in {"ESPN STALE", "NBA NO DATA"}:
            return False
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
            return attach_free_odds(self, events, settings)
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

    def _draw_nba_mini_section_header(self, draw, x1, x2, y, title):
        draw.rectangle((x1, y + 2, x1 + 8, y + 17), fill=COLORS["nba_accent"], outline=COLORS["border"], width=1)
        draw.text((x1 + 13, y - 2), title, font=self._font(13, True), fill=COLORS["text"])
        draw.line((x1, y + 19, x2, y + 19), fill=COLORS["border"], width=1)

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
        team_a_label = self._nba_display_team_from_event(event, "a", full=True)
        team_b_label = self._nba_display_team_from_event(event, "b", full=True)
        team_a, font_a = self._fit_text(draw, team_a_label, max(24, team_a_box[2] - team_a_box[0]), team_font_size, bold=True, min_size=7)
        team_a_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "a")]
        team_b_fill = COLORS[SportsDashboard._nba_team_side_fill_key(event, "b")]
        self._draw_text_in_box(draw, team_a_box, team_a, font_a, team_a_fill)
        center_text, center_font = self._fit_text(draw, center_text, score_w, team_font_size, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (center_x - score_w / 2, y - 1, center_x + score_w / 2, team_bottom), center_text, center_font, COLORS["text"])
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, y, logo_size, event["team_b"])
        team_b, font_b = self._fit_text(draw, team_b_label, max(24, team_b_box[2] - team_b_box[0]), team_font_size, bold=True, min_size=7)
        self._draw_text_in_box(draw, team_b_box, team_b, font_b, team_b_fill, align="right")
        if has_odds:
            odds = event.get("odds") or {}
            self._draw_nba_odds_text(draw, (team_a_box[0], y + 11, team_a_box[2], y + 21), odds.get("team_a"), max_size=8, align="left")
            self._draw_nba_odds_text(draw, (team_b_box[0], y + 11, team_b_box[2], y + 21), odds.get("team_b"), max_size=8, align="right")

    @staticmethod
    def _nba_event_has_moneyline_odds(event):
        odds = (event or {}).get("odds") or {}
        return bool(odds.get("team_a") and odds.get("team_b"))

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
        team_a_label = self._nba_display_team_from_event(event, "a", full=True)
        team_b_label = self._nba_display_team_from_event(event, "b", full=True)
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, y, logo_size, event["team_a"])
        team_a, font_a = self._fit_text(
            draw,
            team_a_label,
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
            team_b_label,
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
