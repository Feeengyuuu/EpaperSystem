from .common import *
from .common import _ACTIVE_COLORS, _safe_exception_text, _normalize_country_alias

SportsDashboard = None


class OffseasonHubMixin:
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




























































































































































