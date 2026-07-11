from .common import *
from .common import _ACTIVE_COLORS, _safe_exception_text, _normalize_country_alias
from security.ssrf import validate_browser_target

SportsDashboard = None


class WorldCupMixin:
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
                validator=validate_browser_target,
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

    def _write_worldcup_live_state(self, selected, now, source_state):
        live_events = (selected or {}).get("live") or []
        event = live_events[0] if live_events else None
        live_until = self._worldcup_live_refresh_until(selected, now)
        payload = {
            "version": WORLD_CUP_LIVE_STATE_VERSION,
            "updated_at": now.astimezone(timezone.utc).isoformat(),
            "source_state": source_state,
            "has_live": isinstance(live_until, datetime) and now <= live_until,
            "live_until": None,
        }
        if isinstance(live_until, datetime):
            payload["live_until"] = live_until.astimezone(timezone.utc).isoformat()
        if event:
            start = event.get("start")
            inferred_live = bool(event.get("inferred_live"))
            source_state_text = str(source_state or "").upper()
            provider = str(event.get("provider") or "").strip()
            score_source = str(event.get("score_source") or "").strip()
            if not provider and ("ESPN" in source_state_text or score_source.upper() == "ESPN"):
                provider = "ESPN"
            payload.update(
                {
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

    @staticmethod
    def _worldcup_live_refresh_until(selected, now):
        if not isinstance(selected, Mapping) or not isinstance(now, datetime):
            return None

        def event_refresh_end(event):
            start = (event or {}).get("start")
            if isinstance(start, datetime):
                return start + WORLD_CUP_INFERRED_LIVE_WINDOW
            return now + WORLD_CUP_INFERRED_LIVE_WINDOW

        refresh_until = None
        for event in selected.get("live") or []:
            event_until = event_refresh_end(event)
            if isinstance(event_until, datetime) and (refresh_until is None or event_until > refresh_until):
                refresh_until = event_until

        for event in selected.get("upcoming") or []:
            start = (event or {}).get("start")
            if not isinstance(start, datetime):
                continue
            event_until = start + WORLD_CUP_INFERRED_LIVE_WINDOW
            if refresh_until is None:
                if start - WORLD_CUP_LIVE_PREGAME_WINDOW <= now < event_until:
                    refresh_until = event_until
                continue
            if start - WORLD_CUP_LIVE_PREGAME_WINDOW <= refresh_until and event_until > refresh_until:
                refresh_until = event_until

        return refresh_until

    def _worldcup_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "worldcup_live_state.json"

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
                    "limit": str(WORLD_CUP_SCOREBOARD_EVENT_LIMIT),
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
                str(WORLD_CUP_SCOREBOARD_EVENT_LIMIT),
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

    @staticmethod
    def _worldcup_cache_is_fresh(cache, cache_hours, now_utc):
        fetched_at = SportsDashboard._parse_cached_utc(cache.get("fetched_at"))
        if fetched_at is None:
            return False
        return now_utc - fetched_at <= timedelta(hours=cache_hours)

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
            extra_a, extra_b = SportsDashboard._worldcup_score_pair_from_score(
                score,
                "extraTime",
                "extratime",
                "extra_time",
            )
            penalty_a, penalty_b = SportsDashboard._worldcup_score_pair_from_score(
                score,
                "penalties",
                "penalty",
                "penaltyShootout",
                "shootout",
            )
            row = {
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
            SportsDashboard._write_worldcup_period_score(row, "extra_time_score", extra_a, extra_b)
            SportsDashboard._write_worldcup_period_score(row, "penalty_score", penalty_a, penalty_b)
            parsed.append(row)
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
            fulltime = score.get("fulltime") or score.get("fullTime") or {}
            extra_a, extra_b = SportsDashboard._worldcup_score_pair_from_score(
                score,
                "extratime",
                "extraTime",
                "extra_time",
            )
            penalty_a, penalty_b = SportsDashboard._worldcup_score_pair_from_score(
                score,
                "penalty",
                "penalties",
                "penaltyShootout",
                "shootout",
            )
            row = {
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
            SportsDashboard._write_worldcup_period_score(row, "extra_time_score", extra_a, extra_b)
            SportsDashboard._write_worldcup_period_score(row, "penalty_score", penalty_a, penalty_b)
            parsed.append(row)
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
            extra_a, extra_b, penalty_a, penalty_b = SportsDashboard._worldcup_espn_period_scores(
                event,
                competition,
                home,
                away,
                state,
            )
            row = {
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
                "team_a_advance": SportsDashboard._espn_competitor_advance(home),
                "team_b_advance": SportsDashboard._espn_competitor_advance(away),
                "wins_a": wins_a,
                "wins_b": wins_b,
                "block": SportsDashboard._worldcup_espn_event_block(event, competition),
                "score_source": "ESPN",
                "provider": "ESPN",
                "source_url": source_url,
                "provider_status_confirmed": state in WORLD_CUP_LIVE_STATES.union(WORLD_CUP_FINISHED_STATES),
                "score_confirmed": show_score and wins_a is not None and wins_b is not None,
            }
            odds = SportsDashboard._worldcup_espn_moneyline_odds(competition)
            if odds:
                row["odds"] = odds
            SportsDashboard._write_worldcup_period_score(row, "extra_time_score", extra_a, extra_b)
            SportsDashboard._write_worldcup_period_score(row, "penalty_score", penalty_a, penalty_b)
            parsed.append(row)
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
    def _worldcup_espn_moneyline_odds(competition):
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
                    formatted = SportsDashboard._worldcup_decimal_odds_from_american(value)
                    if formatted:
                        return formatted
                return ""

            team_a = side_odds("home")
            team_b = side_odds("away")
            if not team_a or not team_b:
                continue
            draw = side_odds("draw")
            if not draw:
                draw = SportsDashboard._worldcup_decimal_odds_from_american(
                    (offer.get("drawOdds") or {}).get("moneyLine")
                )
            provider = offer.get("provider") or {}
            return {
                "team_a": team_a,
                "draw": draw,
                "team_b": team_b,
                "bookmaker": str(
                    provider.get("displayName") or provider.get("name") or "ESPN"
                ).strip(),
            }
        return {}

    @staticmethod
    def _worldcup_decimal_odds_from_american(value):
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
        return SportsDashboard._format_decimal_odds(decimal)

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
    def _worldcup_espn_period_scores(event, competition, home, away, state):
        extra_a = SportsDashboard._worldcup_espn_competitor_period_score(
            home,
            "extraTimeScore",
            "extra_time_score",
            "overtimeScore",
            "overtime_score",
        )
        extra_b = SportsDashboard._worldcup_espn_competitor_period_score(
            away,
            "extraTimeScore",
            "extra_time_score",
            "overtimeScore",
            "overtime_score",
        )
        penalty_a = SportsDashboard._worldcup_espn_competitor_period_score(
            home,
            "penaltyKickScore",
            "penaltyScore",
            "penalty_score",
            "penaltyShootoutScore",
            "shootoutScore",
            "shootout_score",
        )
        penalty_b = SportsDashboard._worldcup_espn_competitor_period_score(
            away,
            "penaltyKickScore",
            "penaltyScore",
            "penalty_score",
            "penaltyShootoutScore",
            "shootoutScore",
            "shootout_score",
        )
        detail_extra_a, detail_extra_b, detail_penalty_a, detail_penalty_b = SportsDashboard._worldcup_espn_detail_period_scores(
            event,
            competition,
            home,
            away,
            state,
        )
        if extra_a is None or extra_b is None:
            extra_a, extra_b = detail_extra_a, detail_extra_b
        if penalty_a is None or penalty_b is None:
            penalty_a, penalty_b = detail_penalty_a, detail_penalty_b
        return extra_a, extra_b, penalty_a, penalty_b

    @staticmethod
    def _worldcup_espn_competitor_period_score(competitor, *keys):
        competitor = competitor or {}
        for key in keys:
            value = competitor.get(key)
            parsed = SportsDashboard._worldcup_score_value(value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _worldcup_espn_detail_period_scores(event, competition, home, away, state):
        details = list((competition or {}).get("details") or [])
        details.extend((event or {}).get("details") or [])
        if not details:
            return None, None, None, None
        team_side = {}
        for team_id in SportsDashboard._worldcup_espn_competitor_ids(home):
            team_side[team_id] = "a"
        for team_id in SportsDashboard._worldcup_espn_competitor_ids(away):
            team_side[team_id] = "b"
        extra_counts = {"a": 0, "b": 0}
        penalty_counts = {"a": 0, "b": 0}
        saw_extra_context = str(state or "").upper() in {"AET", "PEN", "ET", "P"}
        saw_extra_score = False
        saw_penalty_score = False
        for detail in details:
            side = SportsDashboard._worldcup_espn_detail_side(detail, team_side)
            in_extra_time = SportsDashboard._worldcup_espn_detail_is_extra_time(detail)
            saw_extra_context = saw_extra_context or in_extra_time
            if not side or not SportsDashboard._worldcup_espn_detail_is_scoring_play(detail):
                continue
            value = SportsDashboard._worldcup_score_value(detail.get("scoreValue"), 1) or 1
            if bool((detail or {}).get("shootout")):
                penalty_counts[side] += value
                saw_penalty_score = True
            elif in_extra_time:
                extra_counts[side] += value
                saw_extra_score = True
        extra_a = extra_counts["a"] if saw_extra_score or saw_extra_context else None
        extra_b = extra_counts["b"] if saw_extra_score or saw_extra_context else None
        penalty_a = penalty_counts["a"] if saw_penalty_score else None
        penalty_b = penalty_counts["b"] if saw_penalty_score else None
        return extra_a, extra_b, penalty_a, penalty_b

    @staticmethod
    def _worldcup_espn_competitor_ids(competitor):
        competitor = competitor or {}
        team = competitor.get("team") or {}
        ids = []
        for value in (competitor.get("id"), competitor.get("uid"), team.get("id"), team.get("uid")):
            text = str(value or "").strip()
            if text and text not in ids:
                ids.append(text)
        return ids

    @staticmethod
    def _worldcup_espn_detail_side(detail, team_side):
        detail = detail or {}
        team = detail.get("team") or {}
        for value in (detail.get("teamId"), detail.get("team_id"), team.get("id"), team.get("uid")):
            side = team_side.get(str(value or "").strip())
            if side:
                return side
        return ""

    @staticmethod
    def _worldcup_espn_detail_is_scoring_play(detail):
        detail = detail or {}
        if detail.get("scoringPlay") is True:
            return True
        return SportsDashboard._worldcup_score_value(detail.get("scoreValue")) not in (None, 0)

    @staticmethod
    def _worldcup_espn_detail_is_extra_time(detail):
        detail = detail or {}
        period = SportsDashboard._worldcup_score_value(detail.get("period"), detail.get("periodNumber"))
        if period is not None and period > 2:
            return True
        clock = detail.get("clock") or {}
        display = str(clock.get("displayValue") or detail.get("displayClock") or "").strip()
        match = re.search(r"\b(\d{1,3})(?:\+\d{1,2})?['’]?", display)
        if match:
            minute = SportsDashboard._worldcup_score_value(match.group(1))
            return minute is not None and minute > 90
        return False

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
            if "team_b_advance" in scoreboard_event:
                event["team_a_advance"] = scoreboard_event.get("team_b_advance")
            if "team_a_advance" in scoreboard_event:
                event["team_b_advance"] = scoreboard_event.get("team_a_advance")
        else:
            event["wins_a"] = scoreboard_event.get("wins_a")
            event["wins_b"] = scoreboard_event.get("wins_b")
            event["team_a_flag"] = event.get("team_a_flag") or scoreboard_event.get("team_a_flag", "")
            event["team_b_flag"] = event.get("team_b_flag") or scoreboard_event.get("team_b_flag", "")
            if "team_a_advance" in scoreboard_event:
                event["team_a_advance"] = scoreboard_event.get("team_a_advance")
            if "team_b_advance" in scoreboard_event:
                event["team_b_advance"] = scoreboard_event.get("team_b_advance")
        for prefix in ("extra_time_score", "penalty_score"):
            source_a = f"{prefix}_b" if reversed_order else f"{prefix}_a"
            source_b = f"{prefix}_a" if reversed_order else f"{prefix}_b"
            if source_a in scoreboard_event and source_b in scoreboard_event:
                SportsDashboard._write_worldcup_period_score(
                    event,
                    prefix,
                    scoreboard_event.get(source_a),
                    scoreboard_event.get(source_b),
                )
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
    def _worldcup_score_value(*values):
        for value in values:
            parsed = SportsDashboard._lpl_int_value(value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _worldcup_score_pair_from_score(score, *keys):
        if not isinstance(score, Mapping):
            return None, None
        for key in keys:
            block = score.get(key)
            if not isinstance(block, Mapping):
                continue
            home = SportsDashboard._worldcup_score_value(
                block.get("home"),
                block.get("Home"),
                block.get("homeScore"),
                block.get("home_score"),
            )
            away = SportsDashboard._worldcup_score_value(
                block.get("away"),
                block.get("Away"),
                block.get("awayScore"),
                block.get("away_score"),
            )
            if home is not None or away is not None:
                return home, away
        return None, None

    @staticmethod
    def _write_worldcup_period_score(event, prefix, score_a, score_b):
        score_a = SportsDashboard._worldcup_score_value(score_a)
        score_b = SportsDashboard._worldcup_score_value(score_b)
        if score_a is None or score_b is None:
            return
        event[f"{prefix}_a"] = score_a
        event[f"{prefix}_b"] = score_b

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
    def _worldcup_team_eliminated(event, side):
        if not SportsDashboard._worldcup_is_knockout_stage_event(event):
            return False
        if not SportsDashboard._is_worldcup_finished_event(event):
            return False
        side_key = "a" if side == "a" else "b"
        return (event or {}).get(f"team_{side_key}_advance") is False

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

































































