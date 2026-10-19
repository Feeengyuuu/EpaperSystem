from .common import (
    COLORS,
    DEFAULT_F1_CACHE_HOURS,
    DEFAULT_F1_DAILY_LIMIT,
    DEFAULT_F1_JOLPICA_BASE_URL,
    DEFAULT_F1_LIVE_REFRESH_SECONDS,
    DEFAULT_F1_OPENF1_BASE_URL,
    DEFAULT_F1_OPENF1_DAILY_LIMIT,
    DEFAULT_TIMEZONE,
    F1_JOLPICA_STATE_VERSION,
    F1_LIVE_STATE_VERSION,
    F1_OPENF1_STATE_VERSION,
    LOCAL_F1_LOGO_PATH,
    LOCAL_F1_TITLE_WORDMARK_PATH,
    datetime,
    get_http_session,
    logger,
    timedelta,
    timezone,
)
from . import f1_domain

SportsDashboard = None


class F1Mixin:
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
        cache_only = self._provider_cache_only(settings)
        force_refresh = self._force_refresh_requested(settings) and not cache_only
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
        if cache_only:
            if has_compatible_cache:
                return cache, "JOLPICA STALE", cache.get("fetched_at")
            return {}, "JOLPICA NO DATA", None

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

    _should_poll_f1_data = staticmethod(f1_domain.should_poll)

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

    _parse_f1_jolpica_bundle = staticmethod(f1_domain.parse_bundle)

    _f1_races_from_payload = staticmethod(f1_domain._f1_races_from_payload)

    _parse_f1_jolpica_race = staticmethod(f1_domain._parse_f1_jolpica_race)

    _f1_session_specs = staticmethod(f1_domain._f1_session_specs)

    _parse_f1_date_time = staticmethod(f1_domain._parse_f1_date_time)

    _parse_f1_last_result = staticmethod(f1_domain._parse_f1_last_result)

    _parse_f1_driver_standings = staticmethod(f1_domain._parse_f1_driver_standings)

    _parse_f1_constructor_standings = staticmethod(f1_domain._parse_f1_constructor_standings)

    _f1_standings_list = staticmethod(f1_domain._f1_standings_list)

    _f1_driver_code = staticmethod(f1_domain._f1_driver_code)

    _f1_driver_name = staticmethod(f1_domain._f1_driver_name)

    _f1_result_gap = staticmethod(f1_domain._f1_result_gap)

    _select_f1_events = staticmethod(f1_domain.select_events)

    _is_f1_live_session = staticmethod(f1_domain._is_f1_live_session)

    _f1_weekend_race = staticmethod(f1_domain._f1_weekend_race)

    _f1_next_race = staticmethod(f1_domain._f1_next_race)

    _f1_recent_race = staticmethod(f1_domain._f1_recent_race)

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
        cache_only = self._provider_cache_only(settings)
        force_refresh = self._force_refresh_requested(settings) and not cache_only
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("snapshot"), dict)
        if (
            has_compatible_cache
            and not force_refresh
            and self._cache_is_fresh_seconds(cache, self._f1_live_refresh_seconds(settings), now_utc)
        ):
            return cache["snapshot"], "OPENF1 CACHE", cache.get("fetched_at")
        if cache_only:
            if has_compatible_cache:
                return cache["snapshot"], "OPENF1 STALE", cache.get("fetched_at")
            return {}, "OPENF1 NO DATA", None

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

    def _f1_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "f1_live_state.json"

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
