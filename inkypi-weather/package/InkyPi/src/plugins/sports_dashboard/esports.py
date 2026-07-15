from .common import *
from .common import _ACTIVE_COLORS, _safe_exception_text, _normalize_country_alias

SportsDashboard = None


class EsportsMixin:
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

    def _load_lpl_events(self, settings, timezone_info):
        try:
            events = self._fetch_lpl_events(settings, timezone_info)
            if events:
                return events, "LIVE DATA"
        except Exception as exc:
            logger.warning("LPL schedule fetch failed: %s", exc)
        return self._fallback_lpl_events(timezone_info), "LPL FALLBACK"

    def _load_lck_events(self, settings, timezone_info):
        try:
            events = self._fetch_lck_events(settings, timezone_info)
            if events:
                return events, "LCK LIVE DATA"
        except Exception as exc:
            logger.warning("LCK schedule fetch failed: %s", exc)
        return [], "LCK NO DATA"

    def _load_msi_events(self, settings, timezone_info, now):
        tournament = None
        try:
            tournament = self._load_msi_tournament(settings, timezone_info, now)
        except Exception as exc:
            logger.warning("MSI tournament metadata fetch failed: %s", _safe_exception_text(exc))
        featured_event = self._msi_featured_event_from_tournament(tournament, now) or self._lpl_msi_featured_event(now)
        try:
            events = self._fetch_msi_events(settings, timezone_info, now, tournament=tournament)
            if events:
                return events, "MSI LIVE DATA", featured_event
            if featured_event:
                return [], "MSI WATCH", featured_event
        except Exception as exc:
            logger.warning("MSI schedule fetch failed: %s", _safe_exception_text(exc))
        return [], "MSI NO DATA", featured_event

    def _fetch_msi_events(self, settings, timezone_info, now, tournament=None):
        force_live_endpoint = self._msi_tournament_live_window_active(tournament, now)
        events = self._fetch_lol_esports_events(
            settings,
            timezone_info,
            "msiLeagueId",
            DEFAULT_MSI_LEAGUE_ID,
            "MSI",
            force_live_endpoint=force_live_endpoint,
            now=now,
        )
        return self._filter_msi_events_to_tournament(events, tournament, now)

    def _load_msi_tournament(self, settings, timezone_info, now):
        league_id = str(settings.get("msiLeagueId") or DEFAULT_MSI_LEAGUE_ID).strip()
        cache = self._read_json_file(self._msi_tournament_state_path())
        cache_hours = self._int_setting(
            settings,
            "msiTournamentCacheHours",
            DEFAULT_MSI_TOURNAMENT_CACHE_HOURS,
            1,
            48,
        )
        now_utc = (now if isinstance(now, datetime) else datetime.now(timezone.utc)).astimezone(timezone.utc)
        cached_tournament = self._cached_msi_tournament(cache, league_id, timezone_info)
        if cached_tournament and self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return cached_tournament
        try:
            tournament = self._fetch_msi_tournament(settings, timezone_info, now)
        except Exception:
            if cached_tournament:
                return cached_tournament
            raise
        self._write_msi_tournament_state(tournament, league_id, now_utc)
        return tournament

    def _fetch_msi_tournament(self, settings, timezone_info, now):
        league_id = str(settings.get("msiLeagueId") or DEFAULT_MSI_LEAGUE_ID).strip()
        url = LOLESPORTS_TOURNAMENTS_URL.format(league_id=league_id)
        session = get_http_session()
        response = session.get(
            url,
            headers={"x-api-key": LOLESPORTS_API_KEY, "Accept": "application/json"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        leagues_payload = payload.get("data", {}).get("leagues", {})
        if isinstance(leagues_payload, list):
            tournaments = []
            for league in leagues_payload:
                if isinstance(league, Mapping):
                    tournaments.extend(league.get("tournaments") or [])
        elif isinstance(leagues_payload, Mapping):
            tournaments = leagues_payload.get("tournaments", [])
        else:
            tournaments = []
        tournament = self._select_msi_tournament(tournaments, timezone_info, now)
        if not tournament:
            raise ValueError("LoLEsports returned no MSI tournament metadata")
        return tournament

    @staticmethod
    def _select_msi_tournament(tournaments, timezone_info, now):
        parsed = []
        for item in tournaments or []:
            start = SportsDashboard._parse_lolesports_date(item.get("startDate"), timezone_info, end_of_day=False)
            end = SportsDashboard._parse_lolesports_date(item.get("endDate"), timezone_info, end_of_day=True)
            if not start or not end:
                continue
            parsed.append(
                {
                    "id": str(item.get("id") or "").strip(),
                    "slug": str(item.get("slug") or "").strip(),
                    "start": start,
                    "end": end,
                }
            )
        if not parsed:
            return None
        current = now if isinstance(now, datetime) else datetime.now(timezone_info)
        current_date = current.astimezone(timezone_info).date()
        active_or_future = [item for item in parsed if item["end"].date() >= current_date]
        if active_or_future:
            return sorted(active_or_future, key=lambda item: item["start"])[0]
        return sorted(parsed, key=lambda item: item["end"], reverse=True)[0]

    @staticmethod
    def _parse_lolesports_date(value, timezone_info, end_of_day=False):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone_info)
        else:
            parsed = parsed.astimezone(timezone_info)
        if end_of_day:
            return parsed.replace(hour=23, minute=59, second=59, microsecond=0)
        return parsed.replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _cached_msi_tournament(cache, league_id, timezone_info):
        if not isinstance(cache, Mapping):
            return None
        if cache.get("version") != MSI_TOURNAMENT_STATE_VERSION:
            return None
        if str(cache.get("league_id") or "").strip() != str(league_id or "").strip():
            return None
        tournament = cache.get("tournament") or {}
        start = SportsDashboard._parse_lolesports_date(tournament.get("start_date"), timezone_info, end_of_day=False)
        end = SportsDashboard._parse_lolesports_date(tournament.get("end_date"), timezone_info, end_of_day=True)
        if not start or not end:
            return None
        return {
            "id": str(tournament.get("id") or "").strip(),
            "slug": str(tournament.get("slug") or "").strip(),
            "start": start,
            "end": end,
        }

    def _write_msi_tournament_state(self, tournament, league_id, now_utc):
        if not tournament:
            return
        payload = {
            "version": MSI_TOURNAMENT_STATE_VERSION,
            "league_id": str(league_id or "").strip(),
            "fetched_at": now_utc.astimezone(timezone.utc).isoformat(),
            "tournament": {
                "id": tournament.get("id") or "",
                "slug": tournament.get("slug") or "",
                "start_date": tournament["start"].date().isoformat() if isinstance(tournament.get("start"), datetime) else "",
                "end_date": tournament["end"].date().isoformat() if isinstance(tournament.get("end"), datetime) else "",
            },
        }
        self._write_json_file(self._msi_tournament_state_path(), payload)

    @staticmethod
    def _msi_tournament_live_window_active(tournament, now):
        if not tournament or not isinstance(now, datetime):
            return False
        start = tournament.get("start")
        end = tournament.get("end")
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            return False
        return start - LPL_LIVE_PREGAME_WINDOW <= now <= end + LPL_INFERRED_LIVE_WINDOW

    @staticmethod
    def _filter_msi_events_to_tournament(events, tournament, now):
        filtered = []
        start = (tournament or {}).get("start")
        end = (tournament or {}).get("end")
        if isinstance(start, datetime) and isinstance(end, datetime):
            lower = start - timedelta(days=1)
            upper = end + timedelta(days=1)
            for event in events or []:
                event_start = event.get("start")
                if isinstance(event_start, datetime) and lower <= event_start <= upper:
                    filtered.append(event)
            return filtered
        cutoff = now - timedelta(days=2) if isinstance(now, datetime) else None
        for event in events or []:
            event_start = event.get("start")
            if cutoff is None or (isinstance(event_start, datetime) and event_start >= cutoff):
                filtered.append(event)
        return filtered

    @staticmethod
    def _msi_featured_event_from_tournament(tournament, now):
        if not tournament:
            return None
        featured = SportsDashboard._lpl_msi_featured_event(
            now,
            start=tournament.get("start"),
            end=tournament.get("end"),
        )
        if featured:
            featured["tournament_id"] = tournament.get("id") or ""
            featured["tournament_slug"] = tournament.get("slug") or ""
        return featured

    def _load_ewc_sidebar_card(self, settings, timezone_info, now):
        events, source_state = self._load_ewc_events(settings, timezone_info)
        window_days = self._int_setting(
            settings,
            "ewcUpcomingWindowDays",
            DEFAULT_EWC_UPCOMING_WINDOW_DAYS,
            1,
            90,
        )
        detail_matches, detail_source_state = self._load_ewc_detail_matches(settings, timezone_info, events, now, window_days)
        selected = self._select_ewc_events([*events, *detail_matches], now, window_days, rotation_seed=now)
        if not self._ewc_selected_has_displayable_event(selected):
            return None
        if selected.get("main_match") and detail_source_state:
            source_state = detail_source_state
        return {
            "selected": selected,
            "source_state": source_state,
            "priority": self._right_sidebar_ewc_priority(),
        }
    def _load_ewc_events(self, settings, timezone_info):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._ewc_competitions_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._ewc_competitions_cache_key(settings, timezone_info)
        force_refresh = self._force_refresh_requested(settings)
        cache_hours = self._int_setting(settings, "ewcCacheHours", DEFAULT_EWC_CACHE_HOURS, 1, 48)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("events"), list)
        if has_compatible_cache and not force_refresh and self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return self._decode_ewc_events(cache.get("events"), timezone_info), "EWC CACHE"
        try:
            payload = self._fetch_ewc_competitions_payload(settings, timezone_info, cache_key, now_utc)
        except Exception as exc:
            logger.warning("EWC competitions fetch failed: %s", _safe_exception_text(exc))
            if has_compatible_cache:
                return self._decode_ewc_events(cache.get("events"), timezone_info), "EWC STALE"
            return self._fallback_ewc_events(timezone_info), "EWC FALLBACK"
        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write EWC cache: %s", exc)
        return self._decode_ewc_events(payload.get("events"), timezone_info), "EWC LIVE"

    def _fetch_ewc_competitions_payload(self, settings, timezone_info, cache_key, now_utc):
        url = str(settings.get("ewcCompetitionsUrl") or DEFAULT_EWC_COMPETITIONS_URL).strip() or DEFAULT_EWC_COMPETITIONS_URL
        session = get_http_session()
        response = session.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": "EpaperSystem/SportsDashboard EWC",
            },
            timeout=25,
        )
        response.raise_for_status()
        events = self._parse_ewc_competitions_html(response.text, timezone_info, url)
        if not events:
            raise ValueError("EWC competitions page did not contain parseable event cards")
        return {
            "version": EWC_STATE_VERSION,
            "cache_key": cache_key,
            "fetched_at": now_utc.isoformat(),
            "source_url": url,
            "events": self._encode_ewc_events(events),
        }

    def _load_ewc_detail_matches(self, settings, timezone_info, events, now, window_days):
        candidates = self._ewc_detail_candidate_events(events, now, window_days)
        if not candidates:
            return [], ""
        now_utc = (now if isinstance(now, datetime) else datetime.now(timezone.utc)).astimezone(timezone.utc)
        cache_path = self._ewc_detail_cache_path()
        cache = self._read_json_file(cache_path)
        cache_key = self._ewc_detail_cache_key(settings, timezone_info)
        force_refresh = self._force_refresh_requested(settings)
        cache_seconds = self._int_setting(
            settings,
            "ewcDetailCacheSeconds",
            DEFAULT_EWC_DETAIL_CACHE_SECONDS,
            60,
            6 * 60 * 60,
        )
        has_compatible_cache = (
            cache.get("version") == EWC_DETAIL_STATE_VERSION
            and cache.get("cache_key") == cache_key
            and isinstance(cache.get("pages"), Mapping)
        )
        cached_pages = dict(cache.get("pages") or {}) if has_compatible_cache else {}
        candidate_keys = {
            self._ewc_detail_page_key(event)
            for event in candidates
            if self._ewc_detail_page_key(event)
        }
        pages = {
            key: page
            for key, page in cached_pages.items()
            if key in candidate_keys and isinstance(page, Mapping)
        }
        events_to_fetch = []
        for event in candidates:
            page_key = self._ewc_detail_page_key(event)
            page = pages.get(page_key) if page_key else None
            page_matches = self._decode_ewc_events((page or {}).get("matches") or [], timezone_info)
            page_cache_seconds = self._ewc_detail_effective_cache_seconds(page_matches, now, cache_seconds)
            if (
                force_refresh
                or not isinstance(page, Mapping)
                or not self._cache_is_fresh_seconds(page, page_cache_seconds, now_utc)
            ):
                events_to_fetch.append(event)
        if not events_to_fetch:
            cached_matches = self._decode_ewc_events(
                self._ewc_detail_cached_matches({"pages": pages}, candidates),
                timezone_info,
            )
            return cached_matches, "EWC DETAIL CACHE" if cached_matches else ""

        fetched_any = False
        stale_any = False
        for event in events_to_fetch:
            try:
                page = self._fetch_ewc_detail_page(event, timezone_info, now_utc)
            except Exception as exc:
                stale_any = True
                logger.warning(
                    "EWC detail fetch failed for %s: %s",
                    event.get("slug") or event.get("game") or "unknown",
                    _safe_exception_text(exc),
                )
                continue
            old_page = pages.get(page.get("page_key"))
            if not page.get("matches") and isinstance(old_page, Mapping) and old_page.get("matches"):
                stale_any = True
                logger.warning(
                    "EWC detail fetch returned no matches for %s; preserving non-empty cached page",
                    event.get("slug") or event.get("game") or "unknown",
                )
                continue
            if not page.get("matches"):
                stale_any = True
                logger.warning(
                    "EWC detail fetch returned no matches for %s",
                    event.get("slug") or event.get("game") or "unknown",
                )
                continue
            pages[page["page_key"]] = page
            fetched_any = True

        if fetched_any:
            payload = {
                "version": EWC_DETAIL_STATE_VERSION,
                "cache_key": cache_key,
                "fetched_at": now_utc.isoformat(),
                "pages": pages,
            }
            try:
                self._write_json_file(cache_path, payload)
            except OSError as exc:
                logger.warning("Failed to write EWC detail cache: %s", exc)
            cache = payload
        elif has_compatible_cache:
            matches = self._decode_ewc_events(self._ewc_detail_cached_matches({"pages": pages}, candidates), timezone_info)
            return matches, "EWC DETAIL STALE" if matches else ""

        matches = self._decode_ewc_events(self._ewc_detail_cached_matches(cache, candidates), timezone_info)
        if stale_any and matches:
            return matches, "EWC DETAIL STALE"
        return matches, "EWC DETAIL" if fetched_any and matches else ("EWC DETAIL CACHE" if matches else "")

    def _fetch_ewc_detail_page(self, event, timezone_info, now_utc):
        source_url = str((event or {}).get("source_url") or "").strip()
        if not source_url:
            raise ValueError("EWC event has no detail source_url")
        slug = str((event or {}).get("slug") or "").strip().lower()
        game = str((event or {}).get("game") or self._ewc_game_name(slug)).strip() or "EWC"
        year = str((event or {}).get("year") or self._ewc_year_from_url(source_url) or "").strip()
        session = get_http_session()
        response = session.get(
            source_url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": "EpaperSystem/SportsDashboard EWC Detail",
            },
            timeout=25,
        )
        response.raise_for_status()
        matches = self._parse_ewc_detail_schedule_html(response.text, timezone_info, slug, game, source_url, year=year)
        page_key = f"{year or self._ewc_year_from_url(source_url) or 'unknown'}:{slug or source_url}"
        return {
            "page_key": page_key,
            "source_url": source_url,
            "slug": slug,
            "game": game,
            "year": year,
            "fetched_at": now_utc.isoformat(),
            "matches": self._encode_ewc_events(matches),
        }

    @staticmethod
    def _ewc_detail_candidate_events(events, now, window_days):
        if not isinstance(now, datetime):
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        lookahead_days = min(
            max(1, int(window_days or DEFAULT_EWC_UPCOMING_WINDOW_DAYS)),
            DEFAULT_EWC_DETAIL_LOOKAHEAD_DAYS,
        )
        lower = now - timedelta(days=DEFAULT_EWC_EVENT_ACTIVE_AFTER_DAYS)
        upper = now + timedelta(days=lookahead_days)
        active = []
        future = []
        recent = []
        seen = set()
        for event in events or []:
            if not isinstance(event, Mapping):
                continue
            source_url = str(event.get("source_url") or "").strip()
            slug = str(event.get("slug") or "").strip().lower()
            if not source_url or not slug or slug in seen:
                continue
            start = event.get("start")
            end = event.get("end")
            if not isinstance(start, datetime):
                continue
            start = start.replace(tzinfo=now.tzinfo) if start.tzinfo is None else start.astimezone(now.tzinfo)
            if isinstance(end, datetime):
                end = end.replace(tzinfo=now.tzinfo) if end.tzinfo is None else end.astimezone(now.tzinfo)
            else:
                end = start + timedelta(days=1)
            if end < lower or start > upper:
                continue
            candidate = dict(event)
            if start <= now <= end:
                active.append(candidate)
            elif start > now:
                future.append(candidate)
            else:
                recent.append(candidate)
            seen.add(slug)
        active.sort(key=lambda item: (item.get("end") or now, item.get("game") or ""))
        future.sort(key=lambda item: (item.get("start") or now, item.get("game") or ""))
        recent.sort(key=lambda item: (item.get("end") or item.get("start") or now), reverse=True)
        remaining = max(0, DEFAULT_EWC_DETAIL_MAX_PAGES - len(active))
        return active + (future + recent)[:remaining]

    @staticmethod
    def _ewc_detail_page_key(event):
        event = event or {}
        slug = str(event.get("slug") or "").strip().lower()
        year = str(event.get("year") or SportsDashboard._ewc_year_from_url(event.get("source_url")) or "").strip()
        return f"{year or 'unknown'}:{slug}" if slug else ""

    @staticmethod
    def _ewc_detail_cached_matches(cache, candidates):
        pages = cache.get("pages") if isinstance(cache, Mapping) else {}
        if not isinstance(pages, Mapping):
            return []
        candidate_keys = set()
        for event in candidates or []:
            if not isinstance(event, Mapping):
                continue
            slug = str(event.get("slug") or "").strip().lower()
            year = str(event.get("year") or SportsDashboard._ewc_year_from_url(event.get("source_url")) or "").strip()
            if slug:
                candidate_keys.add(f"{year or 'unknown'}:{slug}")
        matches = []
        for key, page in pages.items():
            if candidate_keys and key not in candidate_keys:
                continue
            if isinstance(page, Mapping) and isinstance(page.get("matches"), list):
                matches.extend(page.get("matches") or [])
        return matches

    def _ewc_detail_cache_path(self):
        return self._sports_dashboard_cache_dir() / "ewc_detail_matches.json"

    def _ewc_detail_cache_key(self, settings, timezone_info):
        url = str((settings or {}).get("ewcCompetitionsUrl") or DEFAULT_EWC_COMPETITIONS_URL).strip() or DEFAULT_EWC_COMPETITIONS_URL
        return hashlib.sha1(f"detail|{url}|{self._timezone_key(timezone_info)}".encode("utf-8")).hexdigest()

    @staticmethod
    def _ewc_detail_effective_cache_seconds(matches, now, configured_seconds):
        try:
            configured = max(60, int(configured_seconds))
        except (TypeError, ValueError):
            configured = DEFAULT_EWC_DETAIL_CACHE_SECONDS
        current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        for match in matches or []:
            if not isinstance(match, Mapping):
                continue
            if SportsDashboard._is_ewc_live_event(match, current):
                return min(configured, DEFAULT_EWC_LIVE_REFRESH_SECONDS)
            start = match.get("start")
            if not isinstance(start, datetime):
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=current.tzinfo)
            else:
                start = start.astimezone(current.tzinfo)
            if current <= start <= current + EWC_LIVE_PREGAME_WINDOW:
                return min(configured, DEFAULT_EWC_LIVE_REFRESH_SECONDS)
        return configured

    @staticmethod
    def _extract_ewc_initial_structures(html_text):
        html = str(html_text or "")
        search_from = 0
        decoder = json.JSONDecoder()
        collected = []
        seen_series = set()
        while True:
            marker = html.find("initialStructures", search_from)
            if marker < 0:
                return collected
            script_start = html.rfind("<script", 0, marker)
            script_content_start = html.find(">", script_start) + 1 if script_start >= 0 else -1
            script_end = html.find("</script>", marker)
            if script_content_start <= 0 or script_end < 0:
                search_from = marker + len("initialStructures")
                continue
            script = html[script_content_start:script_end].strip()
            push_marker = "self.__next_f.push("
            push_start = script.find(push_marker)
            if push_start < 0:
                search_from = marker + len("initialStructures")
                continue
            try:
                pushed, _ = decoder.raw_decode(script[push_start + len(push_marker):])
            except (TypeError, ValueError, json.JSONDecodeError):
                search_from = marker + len("initialStructures")
                continue
            if not isinstance(pushed, list) or len(pushed) < 2 or not isinstance(pushed[1], str):
                search_from = marker + len("initialStructures")
                continue
            flight = pushed[1]
            flight_marker = flight.find("initialStructures")
            if flight_marker < 0:
                search_from = marker + len("initialStructures")
                continue
            value_start = flight.find(":", flight_marker)
            if value_start < 0:
                search_from = marker + len("initialStructures")
                continue
            try:
                structures, _ = decoder.raw_decode(flight, value_start + 1)
            except (TypeError, ValueError, json.JSONDecodeError):
                search_from = marker + len("initialStructures")
                continue
            if isinstance(structures, list):
                for structure in structures:
                    if not isinstance(structure, Mapping):
                        continue
                    series_ids = tuple(
                        str(series.get("id") or "")
                        for series in (structure.get("series") or [])
                        if isinstance(series, Mapping)
                    )
                    signature = series_ids or (json.dumps(structure, sort_keys=True, default=str),)
                    if signature in seen_series:
                        continue
                    seen_series.add(signature)
                    collected.append(structure)
            search_from = script_end + len("</script>")

    @staticmethod
    def _ewc_structured_status(series):
        state = str((series or {}).get("state") or "").strip().upper()
        result_status = str(((series or {}).get("result") or {}).get("status") or "").strip().upper()
        if state in {"COMPLETED", "FINAL", "FINISHED"} or result_status == "FINAL":
            return "COMPLETED"
        if state in {"LIVE", "STARTED", "IN_PROGRESS", "RUNNING", "ONGOING", "ACTIVE"}:
            return "LIVE"
        return "UPCOMING"

    @staticmethod
    def _ewc_club_logo_url(club_id):
        value = str(club_id or "").strip()
        if not value:
            return ""
        fallback = EWC_CLUB_LOGO_FALLBACKS.get(value)
        if fallback:
            return fallback
        return f"https://tds-cdn.ewc.efg.gg/assets/clubs/{value}/LOGO_LIGHT.png"

    @staticmethod
    def _ewc_structured_participant(slot, result_slots):
        slot = slot if isinstance(slot, Mapping) else {}
        competitor = slot.get("competitor") if isinstance(slot.get("competitor"), Mapping) else {}
        team = competitor.get("team") if isinstance(competitor.get("team"), Mapping) else {}
        club = competitor.get("club") if isinstance(competitor.get("club"), Mapping) else {}
        person = competitor.get("person") if isinstance(competitor.get("person"), Mapping) else {}
        source = slot.get("source") if isinstance(slot.get("source"), Mapping) else {}
        name = str(
            team.get("name")
            or club.get("name")
            or person.get("nickname")
            or person.get("name")
            or competitor.get("nickname")
            or competitor.get("name")
            or source.get("label")
            or "TBD"
        ).strip() or "TBD"
        short_name = str(
            team.get("short_name")
            or club.get("short_name")
            or person.get("nickname")
            or name
        ).strip() or name
        slot_number = SportsDashboard._lpl_int_value(slot.get("slot"))
        result = result_slots.get(slot_number, {}) if slot_number is not None else {}
        score = result.get("score") if result.get("score") is not None else competitor.get("score")
        placement = result.get("placement") if result.get("placement") is not None else competitor.get("placement")
        roster = []
        for member in competitor.get("roster") or []:
            if not isinstance(member, Mapping):
                continue
            role = str(member.get("role") or "").strip().upper()
            if role and role not in {"PLAYER", "SUBSTITUTE"}:
                continue
            nickname = str(member.get("nickname") or member.get("name") or "").strip()
            if nickname:
                roster.append(nickname)
        return {
            "slot": slot_number,
            "name": name,
            "short_name": short_name,
            "logo_url": SportsDashboard._ewc_club_logo_url(club.get("id")),
            "score": SportsDashboard._lpl_int_value(score),
            "placement": SportsDashboard._lpl_int_value(placement),
            "is_winner": bool(result.get("is_winner")) or str(result.get("outcome") or "").upper() == "WIN",
            "roster": roster,
        }

    @staticmethod
    def _ewc_structured_game_summary(game):
        game = game if isinstance(game, Mapping) else {}
        result = game.get("result") if isinstance(game.get("result"), Mapping) else {}
        result_slots = {
            SportsDashboard._lpl_int_value(item.get("slot")): item
            for item in (result.get("slots") or [])
            if isinstance(item, Mapping) and SportsDashboard._lpl_int_value(item.get("slot")) is not None
        }
        return {
            "id": str(game.get("id") or "").strip(),
            "name": str(game.get("name") or "").strip(),
            "state": str(game.get("state") or "").strip().upper(),
            "sequence": SportsDashboard._lpl_int_value(game.get("sequence_number")),
            "actual_start": str(game.get("actual_start") or "").strip(),
            "actual_end": str(game.get("actual_end") or "").strip(),
            "score_a": SportsDashboard._lpl_int_value((result_slots.get(1) or {}).get("score")),
            "score_b": SportsDashboard._lpl_int_value((result_slots.get(2) or {}).get("score")),
            "winner_slots": [
                value for value in (
                    SportsDashboard._lpl_int_value(item)
                    for item in (result.get("winner_slots") or [])
                ) if value is not None
            ],
        }

    @staticmethod
    def _parse_ewc_next_flight_matches(html_text, timezone_info, slug, game, source_url, year_value):
        structures = SportsDashboard._extract_ewc_initial_structures(html_text)
        if not structures:
            return []
        parsed = []
        parsed_indexes = {}
        for structure_payload in structures:
            if not isinstance(structure_payload, Mapping):
                continue
            phase = structure_payload.get("phase") if isinstance(structure_payload.get("phase"), Mapping) else {}
            groups = {
                str(group.get("id") or ""): group
                for group in (structure_payload.get("groups") or [])
                if isinstance(group, Mapping) and group.get("id")
            }
            for series in structure_payload.get("series") or []:
                if not isinstance(series, Mapping):
                    continue
                start = SportsDashboard._parse_start_time(
                    series.get("scheduled_start") or series.get("actual_start"),
                    timezone_info,
                )
                if not start:
                    continue
                actual_end = SportsDashboard._parse_start_time(series.get("actual_end"), timezone_info)
                status = SportsDashboard._ewc_structured_status(series)
                end = actual_end or (start + EWC_MATCH_DEFAULT_DURATION)
                result = series.get("result") if isinstance(series.get("result"), Mapping) else {}
                result_slots = {
                    SportsDashboard._lpl_int_value(item.get("slot")): item
                    for item in (result.get("slots") or [])
                    if isinstance(item, Mapping) and SportsDashboard._lpl_int_value(item.get("slot")) is not None
                }
                slots = sorted(
                    [item for item in (series.get("slots") or []) if isinstance(item, Mapping)],
                    key=lambda item: SportsDashboard._lpl_int_value(item.get("slot")) or 999,
                )
                participants = [
                    SportsDashboard._ewc_structured_participant(item, result_slots)
                    for item in slots
                ]
                series_structure = series.get("structure") if isinstance(series.get("structure"), Mapping) else {}
                group_ids = [str(item) for item in (series_structure.get("group_ids") or []) if item]
                group_names = [str((groups.get(group_id) or {}).get("name") or "").strip() for group_id in group_ids]
                group_names = [name for name in group_names if name]
                stage = str(
                    series_structure.get("label")
                    or series_structure.get("round_name")
                    or (" / ".join(group_names) if group_names else "")
                    or phase.get("name")
                    or "MATCH"
                ).strip() or "MATCH"
                format_payload = series.get("format") if isinstance(series.get("format"), Mapping) else {}
                streams = [item for item in (series.get("streams") or []) if isinstance(item, Mapping) and item.get("url")]
                preferred_streams = sorted(
                    streams,
                    key=lambda item: (
                        0 if str(item.get("language") or "").lower() == "en" else 1,
                        0 if str(item.get("platform") or "").upper() in {"YOUTUBE", "TWITCH"} else 1,
                    ),
                )
                event_id = str(series.get("id") or "").strip()
                if not event_id:
                    event_id = f"ewc-{year_value}-{slug}-{start.strftime('%Y%m%d-%H%M')}-{len(parsed) + 1}"
                event = {
                    "kind": "match",
                    "event_id": event_id,
                    "match_id": event_id,
                    "series_id": event_id,
                    "game": game,
                    "slug": slug,
                    "year": str(year_value),
                    "source_url": source_url,
                    "start": start,
                    "end": end,
                    "status": status,
                    "stage": stage,
                    "phase": str(phase.get("name") or "").strip(),
                    "group": " / ".join(group_names),
                    "round": str(series_structure.get("round_name") or "").strip(),
                    "format": str(format_payload.get("type") or "").strip().upper(),
                    "best_of": SportsDashboard._lpl_int_value(format_payload.get("best_of")),
                    "participants": participants,
                    "participant_count": len(participants),
                    "games": [SportsDashboard._ewc_structured_game_summary(item) for item in (series.get("games") or [])],
                    "stream_url": str((preferred_streams[0] if preferred_streams else {}).get("url") or "").strip(),
                }
                if len(participants) == 2:
                    event.update(
                        {
                            "multi_competitor": False,
                            "team_a": participants[0]["name"],
                            "team_b": participants[1]["name"],
                            "team_a_short": participants[0]["short_name"],
                            "team_b_short": participants[1]["short_name"],
                            "team_a_logo": participants[0]["logo_url"],
                            "team_b_logo": participants[1]["logo_url"],
                            "team_a_roster": participants[0]["roster"],
                            "team_b_roster": participants[1]["roster"],
                            "score_a": participants[0]["score"],
                            "score_b": participants[1]["score"],
                        }
                    )
                else:
                    leader = next(
                        (
                            item for item in participants
                            if item.get("is_winner") or item.get("placement") == 1
                        ),
                        None,
                    )
                    event.update(
                        {
                            "multi_competitor": True,
                            "leader": (leader or {}).get("name") or "",
                            "leader_logo": (leader or {}).get("logo_url") or "",
                            "leader_score": (leader or {}).get("score"),
                            "leader_placement": (leader or {}).get("placement"),
                        }
                    )
                existing_index = parsed_indexes.get(event_id)
                if existing_index is None:
                    parsed_indexes[event_id] = len(parsed)
                    parsed.append(event)
                else:
                    parsed[existing_index] = event
        return sorted(parsed, key=lambda item: (item["start"], item.get("stage") or "", item.get("event_id") or ""))

    @staticmethod
    def _parse_ewc_detail_schedule_html(html_text, timezone_info, slug, game, source_url, year=None):
        if not html_text:
            return []
        slug = str(slug or "").strip().lower()
        game = str(game or SportsDashboard._ewc_game_name(slug)).strip() or "EWC"
        source_url = str(source_url or DEFAULT_EWC_COMPETITIONS_URL).strip() or DEFAULT_EWC_COMPETITIONS_URL
        year_text = str(year or SportsDashboard._ewc_year_from_url(source_url) or "").strip()
        if not year_text:
            year_text = str(datetime.now(timezone_info).year)
        try:
            year_value = int(year_text)
        except ValueError:
            year_value = datetime.now(timezone_info).year

        structured_matches = SportsDashboard._parse_ewc_next_flight_matches(
            html_text,
            timezone_info,
            slug,
            game,
            source_url,
            year_value,
        )
        if structured_matches:
            return structured_matches

        time_pattern = re.compile(
            r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*[A-Za-z]{3}\.?\s+\d{1,2}(?:st|nd|rd|th)?\s+-\s+\d{1,2}:\d{2}(?:\s*[AP]M)?\b",
            re.IGNORECASE,
        )
        matches = list(time_pattern.finditer(str(html_text)))
        sides = []
        for index, match in enumerate(matches):
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(str(html_text))
            chunk = str(html_text)[match.start():next_start]
            side = SportsDashboard._parse_ewc_detail_match_side(chunk, match.group(0), timezone_info, year_value, source_url)
            if side:
                sides.append(side)

        grouped = {}
        for side in sides:
            key = (side["start"].isoformat(), side["status"], side["stage"])
            grouped.setdefault(key, []).append(side)

        parsed_matches = []
        for group in grouped.values():
            for pair_index in range(0, len(group) - 1, 2):
                left = group[pair_index]
                right = group[pair_index + 1]
                stage_slug = re.sub(r"[^a-z0-9]+", "-", left["stage"].lower()).strip("-") or f"match-{pair_index // 2 + 1}"
                event_id = f"ewc-{year_value}-{slug}-{left['start'].strftime('%Y%m%d-%H%M')}-{stage_slug}"
                parsed_matches.append(
                    {
                        "kind": "match",
                        "event_id": event_id,
                        "match_id": event_id,
                        "game": game,
                        "slug": slug,
                        "year": str(year_value),
                        "source_url": source_url,
                        "start": left["start"],
                        "end": left["start"] + EWC_MATCH_DEFAULT_DURATION,
                        "status": left["status"],
                        "stage": left["stage"],
                        "team_a": left["team"],
                        "team_b": right["team"],
                        "team_a_logo": left.get("logo_url") or "",
                        "team_b_logo": right.get("logo_url") or "",
                        "score_a": left.get("score"),
                        "score_b": right.get("score"),
                    }
                )
        return sorted(parsed_matches, key=lambda item: (item["start"], item.get("stage") or "", item.get("team_a") or ""))

    @staticmethod
    def _parse_ewc_detail_match_side(chunk, time_label, timezone_info, year_value, source_url):
        start = SportsDashboard._parse_ewc_match_time_label(time_label, timezone_info, year_value)
        if not start:
            return None
        team_match = re.search(r"<h[1-6]\b[^>]*>(?P<team>.*?)</h[1-6]>", chunk, flags=re.IGNORECASE | re.DOTALL)
        if not team_match:
            return None
        team = SportsDashboard._html_text(team_match.group("team"))
        if not team:
            return None
        text = SportsDashboard._html_text(chunk)
        status_match = re.search(r"\b(upcoming|ongoing|live|completed)\b", text, flags=re.IGNORECASE)
        status = status_match.group(1).upper() if status_match else "UPCOMING"
        if status == "ONGOING":
            status = "LIVE"
        stage = "MATCH"
        if status_match:
            after_status = text[status_match.end():]
            team_index = after_status.find(team)
            if team_index >= 0:
                stage_candidate = after_status[:team_index]
                stage_candidate = re.sub(r"\s+", " ", stage_candidate).strip(" -")
                if stage_candidate:
                    stage = stage_candidate
        score = None
        after_team = text.split(team, 1)[1] if team in text else ""
        score_match = re.search(r"(?:^|\s)(-|\d+)(?:\s|$)", after_team)
        if score_match:
            raw_score = score_match.group(1)
            score = None if raw_score == "-" else int(raw_score)
        logo_url = ""
        img_match = re.search(r"<img\b[^>]*?>", chunk, flags=re.IGNORECASE | re.DOTALL)
        if img_match:
            logo_url = SportsDashboard._ewc_image_url_from_tag(img_match.group(0), source_url)
        return {
            "start": start,
            "status": status,
            "stage": stage,
            "team": team,
            "score": score,
            "logo_url": logo_url,
        }

    @staticmethod
    def _parse_ewc_match_time_label(label, timezone_info, year_value):
        match = re.search(
            r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*([A-Za-z]{3})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s+-\s+(\d{1,2}):(\d{2})(?:\s*([AP]M))?\b",
            str(label or ""),
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        month = EWC_MONTHS.get(match.group(1).upper())
        if not month:
            return None
        hour = int(match.group(3))
        minute = int(match.group(4))
        meridiem = str(match.group(5) or "").upper()
        if meridiem == "PM" and hour < 12:
            hour += 12
        elif meridiem == "AM" and hour == 12:
            hour = 0
        return datetime(int(year_value), month, int(match.group(2)), hour, minute, tzinfo=timezone_info)

    @staticmethod
    def _ewc_year_from_url(source_url):
        match = re.search(r"/competitions/(20\d{2})(?:/|$)", str(source_url or ""))
        return match.group(1) if match else ""

    @staticmethod
    def _ewc_image_url_from_tag(img_tag, source_url=DEFAULT_EWC_COMPETITIONS_URL):
        tag = str(img_tag or "")
        for attribute in ("src", "data-src", "srcset"):
            attr_match = re.search(fr"\b{attribute}=(?P<quote>[\"'])(?P<value>.*?)(?P=quote)", tag, flags=re.IGNORECASE | re.DOTALL)
            if not attr_match:
                continue
            value = html_lib.unescape(attr_match.group("value")).strip()
            if not value:
                continue
            if attribute == "srcset":
                value = value.split(",", 1)[0].strip().split(" ", 1)[0]
            absolute_url = urljoin(str(source_url or DEFAULT_EWC_COMPETITIONS_URL), value)
            next_match = re.search(r"[?&]url=([^&]+)", absolute_url)
            if next_match:
                return unquote(next_match.group(1))
            return absolute_url
        return ""
    def _ewc_competitions_cache_path(self):
        return self._sports_dashboard_cache_dir() / "ewc_competitions.json"

    def _ewc_competitions_cache_key(self, settings, timezone_info):
        url = str((settings or {}).get("ewcCompetitionsUrl") or DEFAULT_EWC_COMPETITIONS_URL).strip() or DEFAULT_EWC_COMPETITIONS_URL
        return hashlib.sha1(f"{url}|{self._timezone_key(timezone_info)}".encode("utf-8")).hexdigest()

    @staticmethod
    def _encode_ewc_events(events):
        encoded = []
        for event in events or []:
            item = dict(event or {})
            for key in ("start", "end"):
                value = item.get(key)
                if isinstance(value, datetime):
                    item[key] = value.isoformat()
            encoded.append(item)
        return encoded

    @staticmethod
    def _decode_ewc_events(events, timezone_info):
        decoded = []
        for event in events or []:
            if not isinstance(event, Mapping):
                continue
            item = dict(event)
            for key in ("start", "end"):
                value = item.get(key)
                if isinstance(value, datetime):
                    parsed = value
                elif value:
                    try:
                        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    except ValueError:
                        parsed = None
                else:
                    parsed = None
                if parsed is not None:
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone_info)
                    else:
                        parsed = parsed.astimezone(timezone_info)
                    item[key] = parsed
            if isinstance(item.get("start"), datetime):
                decoded.append(item)
        return sorted(decoded, key=lambda item: item["start"])

    @staticmethod
    def _parse_ewc_competitions_html(html_text, timezone_info, source_url=DEFAULT_EWC_COMPETITIONS_URL):
        if not html_text:
            return []
        link_pattern = re.compile(
            r"href=(?P<quote>[\"'])(?P<href>(?:https?://[^\"']+)?/[a-z]{2}/competitions/(?P<year>20\d{2})/(?P<slug>[^\"'/?#]+))(?P=quote)",
            re.IGNORECASE,
        )
        events = []
        seen_slugs = set()
        for match in link_pattern.finditer(str(html_text)):
            slug = match.group("slug").strip().lower()
            if not slug or slug in seen_slugs:
                continue
            href = match.group("href")
            prefix = str(html_text)[max(0, match.start() - 2400):match.start()]
            card_text = SportsDashboard._html_text(prefix)
            card = SportsDashboard._parse_ewc_card_text(card_text, timezone_info)
            if not card:
                continue
            seen_slugs.add(slug)
            source_href = href if href.startswith("http") else f"https://esportsworldcup.com{href}"
            card.update(
                {
                    "game": SportsDashboard._ewc_game_name(slug),
                    "slug": slug,
                    "year": match.group("year"),
                    "source_url": source_href,
                    "logo_url": SportsDashboard._ewc_logo_url_from_card_html(prefix, source_url),
                    "event_id": f"ewc-{match.group('year')}-{slug}",
                }
            )
            events.append(card)
        return sorted(events, key=lambda item: (item["start"], item.get("game") or ""))

    @staticmethod
    def _ewc_logo_url_from_card_html(card_html, source_url=DEFAULT_EWC_COMPETITIONS_URL):
        img_tags = re.findall(r"<img\b[^>]*?>", str(card_html or ""), flags=re.IGNORECASE | re.DOTALL)
        for img_tag in reversed(img_tags):
            alt_match = re.search(r"alt=(?P<quote>[\"'])(?P<alt>.*?)(?P=quote)", img_tag, flags=re.IGNORECASE | re.DOTALL)
            alt_text = html_lib.unescape(alt_match.group("alt")) if alt_match else ""
            if alt_text and "competition logo" not in alt_text.lower():
                continue
            src_match = re.search(r"(?:src|data-src)=(?P<quote>[\"'])(?P<src>.*?)(?P=quote)", img_tag, flags=re.IGNORECASE | re.DOTALL)
            if not src_match:
                continue
            src = html_lib.unescape(src_match.group("src")).strip()
            if not src:
                continue
            absolute_url = urljoin(str(source_url or DEFAULT_EWC_COMPETITIONS_URL), src)
            next_match = re.search(r"[?&]url=([^&]+)", absolute_url)
            if next_match:
                return unquote(next_match.group(1))
            return absolute_url
        return ""

    @staticmethod
    def _parse_ewc_card_text(card_text, timezone_info):
        text = SportsDashboard._html_text(card_text)
        date_matches = list(
            re.finditer(
                r"(?:Main Event\s*)?([A-Za-z]{3})\s+(\d{1,2})\s*-\s*(?:([A-Za-z]{3})\s+)?(\d{1,2}),\s*(20\d{2})",
                text,
                re.IGNORECASE,
            )
        )
        if not date_matches:
            return None
        date_match = date_matches[-1]
        start_month = EWC_MONTHS.get(date_match.group(1).upper())
        end_month = EWC_MONTHS.get((date_match.group(3) or date_match.group(1)).upper())
        if not start_month or not end_month:
            return None
        year = int(date_match.group(5))
        start = datetime(year, start_month, int(date_match.group(2)), 0, 0, tzinfo=timezone_info)
        end = datetime(year, end_month, int(date_match.group(4)), 23, 59, tzinfo=timezone_info)
        if end < start:
            end = end.replace(year=year + 1)

        status = "UPCOMING"
        for status_match in re.finditer(r"\b(upcoming|ongoing|live|completed)\b", text, re.IGNORECASE):
            status = status_match.group(1).upper()

        prize_pool = ""
        prize_matches = list(re.finditer(r"Prize\s*Pool\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?)", text, re.IGNORECASE))
        if prize_matches:
            prize_pool = f"${prize_matches[-1].group(1)}"

        participant_count = None
        participant_label = ""
        participant_matches = list(
            re.finditer(r"Participating\s+(clubs|players|teams)\s+(\d+)", text, re.IGNORECASE)
        )
        if participant_matches:
            participant_label = participant_matches[-1].group(1).lower()
            try:
                participant_count = int(participant_matches[-1].group(2))
            except ValueError:
                participant_count = None

        return {
            "start": start,
            "end": end,
            "status": status,
            "prize_pool": prize_pool,
            "participant_count": participant_count,
            "participant_label": participant_label,
        }

    @staticmethod
    def _ewc_game_name(slug):
        slug = str(slug or "").strip().lower()
        if slug in EWC_GAME_NAME_OVERRIDES:
            return EWC_GAME_NAME_OVERRIDES[slug]
        words = [part for part in re.split(r"[-_]+", slug) if part]
        fixed = []
        for word in words:
            if word in {"fc", "pubg"}:
                fixed.append(word.upper())
            elif word == "dota2":
                fixed.append("Dota 2")
            else:
                fixed.append(word.capitalize())
        return " ".join(fixed) or "EWC"

    @staticmethod
    def _ewc_game_logo_slug(value):
        values = []
        if isinstance(value, Mapping):
            values.extend((value.get("slug"), value.get("game"), value.get("event_id")))
        else:
            values.append(value)
        for raw_value in values:
            raw_text = str(raw_value or "").strip().lower()
            if not raw_text:
                continue
            if raw_text.startswith("ewc-20"):
                parts = raw_text.split("-", 2)
                if len(parts) == 3:
                    raw_text = parts[2]
            slug = re.sub(r"[^a-z0-9]+", "-", raw_text).strip("-")
            if slug in EWC_GAME_LOGO_FILES:
                return slug
            alias = EWC_GAME_LOGO_ALIASES.get(slug)
            if alias:
                return alias
        return ""

    @staticmethod
    def _ewc_game_logo_path(event):
        slug = SportsDashboard._ewc_game_logo_slug(event)
        filename = EWC_GAME_LOGO_FILES.get(slug)
        if not filename:
            return ""
        return os.path.join(LOCAL_EWC_GAME_LOGO_DIR, filename)

    @staticmethod
    def _load_ewc_game_logo(event, size):
        return SportsDashboard._load_local_logo(
            SportsDashboard._ewc_game_logo_path(event),
            (int(size[0]), int(size[1])),
            alpha_threshold=8,
        )

    @staticmethod
    def _fallback_ewc_events(timezone_info):
        return [
            {
                "game": "EWC 2026",
                "slug": "ewc-2026",
                "event_id": "ewc-2026-series",
                "year": "2026",
                "start": datetime(2026, 7, 6, 0, 0, tzinfo=timezone_info),
                "end": datetime(2026, 8, 23, 23, 59, tzinfo=timezone_info),
                "status": "UPCOMING",
                "prize_pool": "",
                "participant_count": 25,
                "participant_label": "events",
                "source_url": DEFAULT_EWC_COMPETITIONS_URL,
            }
        ]

    @staticmethod
    def _select_ewc_events(events, now, upcoming_window_days=DEFAULT_EWC_UPCOMING_WINDOW_DAYS, rotation_seed=None):
        if not isinstance(now, datetime):
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        normalized_events = []
        for event in events or []:
            if not isinstance(event, Mapping):
                continue
            start = event.get("start")
            if not isinstance(start, datetime):
                continue
            item = dict(event)
            if start.tzinfo is None:
                item["start"] = start.replace(tzinfo=now.tzinfo)
            else:
                item["start"] = start.astimezone(now.tzinfo)
            end = item.get("end")
            if isinstance(end, datetime):
                item["end"] = end.replace(tzinfo=now.tzinfo) if end.tzinfo is None else end.astimezone(now.tzinfo)
            elif SportsDashboard._is_ewc_match_item(item):
                item["end"] = item["start"] + EWC_MATCH_DEFAULT_DURATION
            else:
                item["end"] = item["start"] + timedelta(days=1)
            normalized_events.append(item)
        normalized_events = sorted(
            normalized_events,
            key=lambda item: (item["start"], item.get("stage") or "", item.get("game") or "", item.get("team_a") or ""),
        )
        match_items = [event for event in normalized_events if SportsDashboard._is_ewc_match_item(event)]
        event_items = [event for event in normalized_events if not SportsDashboard._is_ewc_match_item(event)]

        live_matches = [event for event in match_items if SportsDashboard._is_ewc_live_event(event, now)]
        upcoming_matches = [event for event in match_items if event["start"] >= now and not SportsDashboard._is_ewc_finished_event(event, now)]
        recent_matches = sorted(
            [event for event in match_items if event["start"] < now and not SportsDashboard._is_ewc_live_event(event, now)],
            key=lambda item: item["start"],
            reverse=True,
        )
        live_events = [event for event in event_items if SportsDashboard._is_ewc_live_event(event, now)]
        upcoming_events = [event for event in event_items if event["start"] >= now and not SportsDashboard._is_ewc_finished_event(event, now)]
        recent_events = sorted(
            [event for event in event_items if event["start"] < now and not SportsDashboard._is_ewc_live_event(event, now)],
            key=lambda item: item["start"],
            reverse=True,
        )

        rotation_bucket = SportsDashboard._ewc_rotation_bucket(rotation_seed if rotation_seed is not None else now)
        selected_match_group = None
        if live_matches:
            selected_match_group = SportsDashboard._ewc_match_group_for_display(
                live_matches,
                live_matches,
                upcoming_matches,
                recent_matches,
                rotation_bucket,
            )
        elif upcoming_matches:
            selected_match_group = SportsDashboard._ewc_match_group_for_display(
                upcoming_matches,
                live_matches,
                upcoming_matches,
                recent_matches,
                rotation_bucket,
            )
        elif recent_matches:
            selected_match_group = SportsDashboard._ewc_match_group_for_display(
                recent_matches,
                live_matches,
                upcoming_matches,
                recent_matches,
                rotation_bucket,
            )

        main_match = None
        group_live_matches = []
        group_upcoming_matches = []
        group_recent_matches = []
        if selected_match_group:
            group_count = max(1, int(selected_match_group.get("rotation_group_count") or 1))
            group_bucket = rotation_bucket // group_count
            group_live_matches = list(selected_match_group.get("live_matches") or [])
            group_upcoming_matches = list(selected_match_group.get("upcoming_matches") or [])
            group_recent_matches = list(selected_match_group.get("recent_matches") or [])
            if group_live_matches:
                main_match = SportsDashboard._ewc_rotated_choice_from_bucket(group_live_matches, group_bucket)
            elif group_upcoming_matches:
                next_group_start = group_upcoming_matches[0]["start"]
                same_group_start = [event for event in group_upcoming_matches if event["start"] == next_group_start]
                main_match = SportsDashboard._ewc_rotated_choice_from_bucket(same_group_start, group_bucket)
            elif group_recent_matches:
                main_match = group_recent_matches[0]

        event_main = live_events[0] if live_events else (upcoming_events[0] if upcoming_events else (recent_events[0] if recent_events else None))
        main = main_match or event_main
        live = group_live_matches if selected_match_group else (live_matches if live_matches else live_events)
        upcoming = group_upcoming_matches if selected_match_group else (upcoming_matches if upcoming_matches else upcoming_events)
        recent = group_recent_matches if selected_match_group else (recent_matches if recent_matches else recent_events)

        display_window_active = bool(live_matches or live_events)
        if not display_window_active and upcoming_matches:
            window_days = max(1, int(upcoming_window_days or DEFAULT_EWC_UPCOMING_WINDOW_DAYS))
            display_window_active = upcoming_matches[0]["start"] - now <= timedelta(days=window_days)
        if not display_window_active and upcoming_events:
            window_days = max(1, int(upcoming_window_days or DEFAULT_EWC_UPCOMING_WINDOW_DAYS))
            display_window_active = upcoming_events[0]["start"] - now <= timedelta(days=window_days)
        if not display_window_active and recent_matches:
            display_window_active = now - recent_matches[0].get("end", recent_matches[0]["start"]) <= timedelta(days=DEFAULT_EWC_EVENT_ACTIVE_AFTER_DAYS)
        if not display_window_active and recent_events:
            display_window_active = now - recent_events[0].get("end", recent_events[0]["start"]) <= timedelta(days=DEFAULT_EWC_EVENT_ACTIVE_AFTER_DAYS)
        return {
            "live": live,
            "upcoming": upcoming,
            "recent": recent,
            "main": main,
            "main_match": main_match,
            "live_matches": group_live_matches if selected_match_group else live_matches,
            "upcoming_matches": group_upcoming_matches if selected_match_group else upcoming_matches,
            "recent_matches": group_recent_matches if selected_match_group else recent_matches,
            "all_live_matches": live_matches,
            "all_upcoming_matches": upcoming_matches,
            "all_recent_matches": recent_matches,
            "selected_match_group": selected_match_group,
            "display_window_active": display_window_active,
        }
    @staticmethod
    def _is_ewc_match_item(event):
        return str((event or {}).get("kind") or "").strip().lower() == "match" or bool(
            (event or {}).get("team_a") and (event or {}).get("team_b")
        )

    @staticmethod
    def _ewc_match_group_for_display(candidate_matches, live_matches, upcoming_matches, recent_matches, rotation_bucket):
        groups = SportsDashboard._ewc_grouped_match_choices(candidate_matches)
        if not groups:
            return None
        selected = groups[rotation_bucket % len(groups)]
        key = selected["key"]
        selected["rotation_group_count"] = len(groups)
        selected["live_matches"] = SportsDashboard._ewc_matches_for_group(live_matches, key)
        selected["upcoming_matches"] = SportsDashboard._ewc_matches_for_group(upcoming_matches, key)
        selected["recent_matches"] = SportsDashboard._ewc_matches_for_group(recent_matches, key)
        return selected

    @staticmethod
    def _ewc_grouped_match_choices(matches):
        groups = []
        by_key = {}
        for match in matches or []:
            key = SportsDashboard._ewc_match_group_key(match)
            if not key:
                continue
            group = by_key.get(key)
            if not group:
                group = {
                    "key": key,
                    "slug": str((match or {}).get("slug") or "").strip().lower(),
                    "game": str((match or {}).get("game") or "EWC").strip() or "EWC",
                    "matches": [],
                }
                by_key[key] = group
                groups.append(group)
            group["matches"].append(match)
        for group in groups:
            group["matches"] = sorted(
                group["matches"],
                key=lambda item: (item.get("start") or datetime.max.replace(tzinfo=timezone.utc), item.get("stage") or "", item.get("team_a") or ""),
            )
        return sorted(
            groups,
            key=lambda group: (
                group["matches"][0].get("start") if group["matches"] else datetime.max.replace(tzinfo=timezone.utc),
                str(group.get("game") or ""),
                str(group.get("key") or ""),
            ),
        )

    @staticmethod
    def _ewc_match_group_key(match):
        match = match or {}
        slug = str(match.get("slug") or "").strip().lower()
        if slug:
            return slug
        game = re.sub(r"[^a-z0-9]+", "-", str(match.get("game") or "").strip().lower()).strip("-")
        if game:
            return game
        source_url = str(match.get("source_url") or "").strip().lower()
        if source_url:
            return source_url
        return str(match.get("event_id") or match.get("match_id") or "").strip().lower()

    @staticmethod
    def _ewc_matches_for_group(matches, group_key):
        return [match for match in matches or [] if SportsDashboard._ewc_match_group_key(match) == group_key]

    @staticmethod
    def _ewc_rotation_bucket(rotation_seed=None):
        try:
            if isinstance(rotation_seed, datetime):
                return int(rotation_seed.timestamp()) // 60
            if rotation_seed is not None:
                return int(rotation_seed)
            return int(datetime.now(timezone.utc).timestamp()) // 60
        except (TypeError, ValueError, OSError, OverflowError):
            return 0

    @staticmethod
    def _ewc_rotated_choice_from_bucket(items, bucket):
        items = list(items or [])
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        try:
            index = int(bucket) % len(items)
        except (TypeError, ValueError, OverflowError):
            index = 0
        return items[index]

    @staticmethod
    def _ewc_rotated_choice(items, rotation_seed=None):
        return SportsDashboard._ewc_rotated_choice_from_bucket(
            items,
            SportsDashboard._ewc_rotation_bucket(rotation_seed),
        )
    @staticmethod
    def _is_ewc_live_event(event, now):
        event = event or {}
        status = str(event.get("status") or "").strip().upper()
        if status == "COMPLETED":
            return False
        start = event.get("start")
        end = event.get("end")
        if not isinstance(start, datetime):
            return False
        if not isinstance(end, datetime):
            end = start + timedelta(days=1)
        return start <= now <= end or status in {"ONGOING", "LIVE"}

    @staticmethod
    def _is_ewc_finished_event(event, now):
        event = event or {}
        status = str(event.get("status") or "").strip().upper()
        end = event.get("end")
        if isinstance(end, datetime) and now > end + timedelta(days=DEFAULT_EWC_EVENT_ACTIVE_AFTER_DAYS):
            return True
        return status == "COMPLETED" and (not isinstance(end, datetime) or now > end)

    @staticmethod
    def _ewc_selected_has_displayable_event(selected):
        selected = selected or {}
        return bool((selected.get("main_match") or selected.get("main")) and selected.get("display_window_active"))

    @staticmethod
    def _ewc_sidebar_candidate_phase(card):
        selected = (card or {}).get("selected") or {}
        if not selected.get("display_window_active"):
            return None
        if selected.get("live"):
            return 0
        if selected.get("upcoming"):
            return 1
        if selected.get("recent"):
            return 2
        return None

    @staticmethod
    def _right_sidebar_ewc_priority():
        return 2

    def _ewc_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "ewc_live_state.json"

    def _write_ewc_live_state(self, selected, now, source_state):
        selected = selected or {}
        current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current_utc = current.astimezone(timezone.utc)
        live_matches = [
            match
            for match in (selected.get("all_live_matches") or selected.get("live_matches") or [])
            if self._is_ewc_match_item(match)
        ]
        upcoming_matches = [
            match
            for match in (selected.get("all_upcoming_matches") or selected.get("upcoming_matches") or [])
            if self._is_ewc_match_item(match)
        ]
        pregame_matches = []
        for match in upcoming_matches:
            start = match.get("start")
            if not isinstance(start, datetime):
                continue
            start_local = start.replace(tzinfo=current.tzinfo) if start.tzinfo is None else start.astimezone(current.tzinfo)
            if current <= start_local <= current + EWC_LIVE_PREGAME_WINDOW:
                pregame_matches.append(match)
        pregame_matches.sort(key=lambda match: match.get("start") or datetime.max.replace(tzinfo=current.tzinfo))
        main_match = selected.get("main_match") or (live_matches[0] if live_matches else {})
        if main_match and not self._is_ewc_match_item(main_match):
            main_match = {}
        event = live_matches[0] if live_matches else (pregame_matches[0] if pregame_matches else (main_match or {}))
        event_start = event.get("start") if isinstance(event, Mapping) else None
        event_end = event.get("end") if isinstance(event, Mapping) else None
        if isinstance(event_start, datetime) and event_start.tzinfo is None:
            event_start = event_start.replace(tzinfo=current.tzinfo)
        if isinstance(event_end, datetime) and event_end.tzinfo is None:
            event_end = event_end.replace(tzinfo=current.tzinfo)
        if not isinstance(event_end, datetime) and isinstance(event_start, datetime):
            event_end = event_start + EWC_MATCH_DEFAULT_DURATION

        has_live = bool(live_matches or pregame_matches)
        live_until = event_end.astimezone(timezone.utc).isoformat() if has_live and isinstance(event_end, datetime) else None
        selected_group = selected.get("selected_match_group") or {}
        payload = {
            "version": EWC_LIVE_STATE_VERSION,
            "updated_at": current_utc.isoformat(),
            "source_state": source_state,
            "has_live": bool(has_live and event),
            "live_until": live_until,
            "group_key": selected_group.get("key") or "",
            "game": (event or {}).get("game") or selected_group.get("game") or "",
            "slug": (event or {}).get("slug") or selected_group.get("slug") or "",
        }
        if event:
            payload.update(
                {
                    "event_id": event.get("event_id") or event.get("match_id") or "",
                    "team_a": event.get("team_a") or "",
                    "team_b": event.get("team_b") or "",
                    "score": self._ewc_match_score_label(event),
                    "status": event.get("status") or "",
                    "stage": event.get("stage") or "",
                    "started_at": event_start.astimezone(timezone.utc).isoformat() if isinstance(event_start, datetime) else None,
                }
            )
        try:
            self._write_json_file(self._ewc_live_state_path(), payload)
        except OSError as exc:
            logger.warning("Failed to write EWC live refresh state: %s", exc)
    @staticmethod
    def _ewc_sidebar_main_timestamp(card, default):
        selected = (card or {}).get("selected") or {}
        event = selected.get("main")
        if not event and selected.get("upcoming"):
            event = selected["upcoming"][0]
        start = (event or {}).get("start")
        if isinstance(start, datetime):
            return start.timestamp()
        return default

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
        if self._bool_setting(settings, "msiEnabled", True):
            msi_events, msi_source_state, msi_featured_event = self._load_msi_events(settings, timezone_info, now)
            msi_selected = self._select_msi_events(msi_events, now, featured_event=msi_featured_event)
            cards.append(
                {
                    "league_key": "MSI",
                    "selected": msi_selected,
                    "source_state": msi_source_state,
                    "priority": 2,
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
        if configured in {"LPL", "LCK", "MSI"}:
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
    def _select_right_esports_sidebar(lol_cards, valve_selected, valve_source_state, now, ewc_card=None):
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

        ewc_phase = SportsDashboard._ewc_sidebar_candidate_phase(ewc_card)
        if ewc_phase is not None:
            ewc_selected = (ewc_card or {}).get("selected") or {}
            candidates.append(
                {
                    "kind": "ewc",
                    "selected": ewc_selected,
                    "source_state": (ewc_card or {}).get("source_state") or "EWC DATA",
                    "phase": ewc_phase,
                    "priority": SportsDashboard._right_sidebar_ewc_priority(),
                    "tie": SportsDashboard._ewc_sidebar_main_timestamp(ewc_card, float("inf")),
                }
            )

        for card in SportsDashboard._valve_esports_active_cards(valve_selected):
            candidates.append(
                {
                    "kind": "valve",
                    "selected": SportsDashboard._valve_esports_selected_for_card(valve_selected, card),
                    "source_state": card.get("source_state") or valve_source_state or "VALVE DATA",
                    "phase": 1,
                    "priority": SportsDashboard._right_sidebar_valve_priority(card),
                    "tie": str(card.get("event_name") or ""),
                }
            )

        if candidates:
            if not SportsDashboard._right_sidebar_has_active_competition(candidates):
                return {"kind": "lol", "choice": SportsDashboard._right_sidebar_default_lpl_choice(lol_cards, now)}
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
            return 3
        if series == "TI":
            return 4
        try:
            return 5 + int((card or {}).get("order") or 0)
        except (TypeError, ValueError):
            return 99

    @staticmethod
    def _right_sidebar_default_lpl_choice(cards, now):
        for card in cards or []:
            if str((card or {}).get("league_key") or "").strip().upper() == "LPL":
                return card
        return {
            "league_key": "LPL",
            "selected": SportsDashboard._select_lpl_events([], now),
            "source_state": "LPL FALLBACK",
            "priority": 0,
        }

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

    def _fetch_lol_esports_events(self, settings, timezone_info, league_setting_key, default_league_id, league_key, force_live_endpoint=False, now=None):
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
        now = now if isinstance(now, datetime) else datetime.now(timezone_info)
        live_endpoint_key = {
            "LPL": "lplLiveEndpointEnabled",
            "LCK": "lckLiveEndpointEnabled",
            "MSI": "msiLiveEndpointEnabled",
        }.get(str(league_key).upper(), "lolLiveEndpointEnabled")
        should_poll_live = force_live_endpoint or self._should_poll_lpl_live_endpoint(events, now)
        if self._bool_setting(settings, live_endpoint_key, True) and should_poll_live:
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
            source_match_id = str(match.get("id") or "").strip()
            event_id = str(event.get("id") or source_match_id or "").strip()
            block = str(event.get("blockName") or "").strip()
            parsed.append(
                {
                    "event_id": event_id,
                    "match_id": str(source_match_id or event_id).strip(),
                    "source_match_id": source_match_id,
                    "event_type": str(event.get("type") or "").strip().lower(),
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
    def _lol_event_stage_fallback(event):
        event = event or {}
        for key in ("league_key", "league_id", "league_slug", "league_name"):
            text = str(event.get(key) or "").strip()
            if not text:
                continue
            if text == DEFAULT_MSI_LEAGUE_ID:
                return "MSI"
            if text == DEFAULT_LCK_LEAGUE_ID:
                return "LCK"
            if text == DEFAULT_LPL_LEAGUE_ID:
                return "LPL"
            compact = "".join(ch for ch in text.lower() if ch.isalnum())
            if compact in {"msi", "midseasoninvitational"}:
                return "MSI"
            if compact in {"lck", "leagueoflegendschampionskorea"}:
                return "LCK"
            if compact in {"lpl", "leagueoflegendsproleague"}:
                return "LPL"

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
            stage_fallback = SportsDashboard._lol_event_stage_fallback(event)
            event["stage_label"] = SportsDashboard._format_lpl_stage_label(event.get("stage_label") or event.get("block") or stage_fallback)

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
    def _is_msi_displayable_match_event(event):
        event = event or {}
        if str(event.get("event_type") or "").strip().lower() == "show":
            return False
        source_match_id = str(event.get("source_match_id") or "").strip()
        team_a = str(event.get("team_a") or "").strip().upper()
        team_b = str(event.get("team_b") or "").strip().upper()
        if not source_match_id and team_a in {"", "TBD"} and team_b in {"", "TBD"}:
            return False
        return True

    @staticmethod
    def _select_lpl_events(events, now):
        return SportsDashboard._select_lol_events(events, now, include_lpl_featured=True)

    @staticmethod
    def _select_lck_events(events, now):
        return SportsDashboard._select_lol_events(events, now, include_lpl_featured=False)

    @staticmethod
    def _select_msi_events(events, now, featured_event=None):
        events = [event for event in (events or []) if SportsDashboard._is_msi_displayable_match_event(event)]
        selected = SportsDashboard._select_lol_events(events, now, include_lpl_featured=False)
        if (
            featured_event
            and not selected.get("live")
            and not selected.get("upcoming")
            and not selected.get("recent")
            and not selected.get("main")
        ):
            selected = dict(selected)
            selected["featured_event"] = featured_event
            selected["featured_event_page"] = True
            selected["offseason"] = featured_event.get("phase") == "countdown"
        return selected

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
    def _lpl_msi_featured_event(now, phase_override=None, start=None, end=None):
        if not isinstance(now, datetime):
            now = datetime.now(timezone.utc)
        tzinfo = now.tzinfo or timezone.utc
        start_at = start if isinstance(start, datetime) else datetime(*MSI_2026_START, 0, 0, tzinfo=tzinfo)
        end_at = end if isinstance(end, datetime) else datetime(*MSI_2026_END, 23, 59, tzinfo=tzinfo)
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

    def _write_lpl_live_state(self, selected, now, source_state):
        self._write_lol_live_state(selected, now, source_state, league_key="LPL")

    def _write_lck_live_state(self, selected, now, source_state):
        self._write_lol_live_state(selected, now, source_state, league_key="LCK")

    def _write_lol_live_state(self, selected, now, source_state, league_key="LPL"):
        key = str(league_key or "LPL").strip().upper()
        live_events = (selected or {}).get("live") or []
        event = live_events[0] if live_events else None
        payload = {
            "version": {"LCK": LCK_LIVE_STATE_VERSION, "MSI": MSI_LIVE_STATE_VERSION}.get(key, LPL_LIVE_STATE_VERSION),
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
            path = {"LCK": self._lck_live_state_path, "MSI": self._msi_live_state_path}.get(key, self._lpl_live_state_path)()
            self._write_json_file(path, payload)
        except OSError as exc:
            logger.warning("Failed to write %s live refresh state: %s", key, exc)

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

    def _msi_tournament_state_path(self):
        return self._sports_dashboard_cache_dir() / "msi_tournament_state.json"

    def _msi_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "msi_live_state.json"

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





























































