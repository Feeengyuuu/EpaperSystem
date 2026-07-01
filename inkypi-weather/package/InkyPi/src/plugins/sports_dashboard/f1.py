from .common import *
from .common import _ACTIVE_COLORS, _safe_exception_text, _normalize_country_alias

SportsDashboard = None


class F1Mixin:
    @staticmethod
    def _should_show_f1_panel(settings, nba_selected):
        mode = str((settings or {}).get("f1PanelMode") or "auto").strip().lower()
        if mode in {"off", "false", "disabled", "disable", "none", "nba"}:
            return False
        if mode in {"always", "on", "true", "f1"}:
            return True
        return bool((nba_selected or {}).get("offseason"))

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
        force_refresh = self._force_refresh_requested(settings)
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

    @staticmethod
    def _should_poll_f1_data(parsed, now):
        for race in (parsed or {}).get("races") or []:
            for session in race.get("sessions") or []:
                if SportsDashboard._is_f1_live_session(session, now):
                    return True
                start = session.get("start")
                if isinstance(start, datetime) and start - F1_SESSION_PREGAME_WINDOW <= now < start:
                    return True
        return False

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

    @staticmethod
    def _parse_f1_jolpica_bundle(payload, timezone_info):
        races = [
            SportsDashboard._parse_f1_jolpica_race(race, timezone_info)
            for race in SportsDashboard._f1_races_from_payload((payload or {}).get("schedule") or payload)
        ]
        races = [race for race in races if race]
        races.sort(key=lambda item: item.get("race_start") or datetime.max.replace(tzinfo=timezone.utc))
        return {
            "races": races,
            "last_result": SportsDashboard._parse_f1_last_result((payload or {}).get("results"), timezone_info),
            "driver_standings": SportsDashboard._parse_f1_driver_standings((payload or {}).get("driver_standings")),
            "constructor_standings": SportsDashboard._parse_f1_constructor_standings((payload or {}).get("constructor_standings")),
        }

    @staticmethod
    def _f1_races_from_payload(payload):
        mr_data = (payload or {}).get("MRData") or {}
        race_table = mr_data.get("RaceTable") or {}
        races = race_table.get("Races") or []
        return races if isinstance(races, list) else []

    @staticmethod
    def _parse_f1_jolpica_race(race, timezone_info):
        if not isinstance(race, Mapping):
            return None
        circuit = race.get("Circuit") or {}
        location = circuit.get("Location") or {}
        race_start = SportsDashboard._parse_f1_date_time(race.get("date"), race.get("time"), timezone_info)
        sessions = []
        for source_key, label, title, duration in SportsDashboard._f1_session_specs():
            if source_key == "Race":
                start = race_start
            else:
                source = race.get(source_key) or {}
                start = SportsDashboard._parse_f1_date_time(source.get("date"), source.get("time"), timezone_info)
            if not start:
                continue
            sessions.append(
                {
                    "key": source_key,
                    "label": label,
                    "title": title,
                    "start": start,
                    "duration": duration,
                }
            )
        sessions.sort(key=lambda item: item["start"])
        return {
            "season": str(race.get("season") or ""),
            "round": str(race.get("round") or ""),
            "race_name": str(race.get("raceName") or "Formula 1").strip() or "Formula 1",
            "circuit_name": str(circuit.get("circuitName") or "").strip(),
            "locality": str(location.get("locality") or "").strip(),
            "country": str(location.get("country") or "").strip(),
            "race_start": race_start,
            "sessions": sessions,
        }

    @staticmethod
    def _f1_session_specs():
        return (
            ("FirstPractice", "FP1", "FP1", timedelta(hours=2)),
            ("SecondPractice", "FP2", "FP2", timedelta(hours=2)),
            ("ThirdPractice", "FP3", "FP3", timedelta(hours=2)),
            ("SprintQualifying", "SQ", "SPRINT Q", timedelta(hours=2)),
            ("SprintShootout", "SQ", "SPRINT Q", timedelta(hours=2)),
            ("Sprint", "SPRINT", "SPRINT", timedelta(hours=2)),
            ("Qualifying", "Q", "QUALIFYING", timedelta(hours=2)),
            ("Race", "RACE", "RACE", timedelta(hours=4)),
        )

    @staticmethod
    def _parse_f1_date_time(date_value, time_value, timezone_info):
        date_text = str(date_value or "").strip()
        if not date_text:
            return None
        time_text = str(time_value or "00:00:00Z").strip() or "00:00:00Z"
        if time_text.endswith("Z"):
            time_text = f"{time_text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(f"{date_text}T{time_text}")
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone_info)

    @staticmethod
    def _parse_f1_last_result(payload, timezone_info):
        races = SportsDashboard._f1_races_from_payload(payload)
        if not races:
            return None
        race = races[0]
        top = []
        for item in (race.get("Results") or [])[:5]:
            driver = item.get("Driver") or {}
            constructor = item.get("Constructor") or {}
            top.append(
                {
                    "position": SportsDashboard._lpl_int_value(item.get("position")) or len(top) + 1,
                    "driver_code": SportsDashboard._f1_driver_code(driver),
                    "driver_name": SportsDashboard._f1_driver_name(driver),
                    "constructor": str(constructor.get("name") or "").strip(),
                    "gap": SportsDashboard._f1_result_gap(item),
                    "status": str(item.get("status") or "").strip(),
                }
            )
        return {
            "round": str(race.get("round") or ""),
            "race_name": str(race.get("raceName") or "").strip(),
            "start": SportsDashboard._parse_f1_date_time(race.get("date"), race.get("time"), timezone_info),
            "top": top,
        }

    @staticmethod
    def _parse_f1_driver_standings(payload):
        standings = SportsDashboard._f1_standings_list(payload, "DriverStandings")
        result = []
        for item in standings[:5]:
            driver = item.get("Driver") or {}
            result.append(
                {
                    "position": SportsDashboard._lpl_int_value(item.get("position")) or len(result) + 1,
                    "driver_code": SportsDashboard._f1_driver_code(driver),
                    "points": str(item.get("points") or "0"),
                    "wins": str(item.get("wins") or "0"),
                }
            )
        return result

    @staticmethod
    def _parse_f1_constructor_standings(payload):
        standings = SportsDashboard._f1_standings_list(payload, "ConstructorStandings")
        result = []
        for item in standings[:5]:
            constructors = item.get("Constructor") or item.get("Constructors") or {}
            result.append(
                {
                    "position": SportsDashboard._lpl_int_value(item.get("position")) or len(result) + 1,
                    "constructor": str(constructors.get("name") or "").strip(),
                    "points": str(item.get("points") or "0"),
                    "wins": str(item.get("wins") or "0"),
                }
            )
        return result

    @staticmethod
    def _f1_standings_list(payload, key):
        mr_data = (payload or {}).get("MRData") or {}
        table = mr_data.get("StandingsTable") or {}
        lists = table.get("StandingsLists") or []
        if not lists:
            return []
        standings = (lists[0] or {}).get(key) or []
        return standings if isinstance(standings, list) else []

    @staticmethod
    def _f1_driver_code(driver):
        code = str((driver or {}).get("code") or (driver or {}).get("name_acronym") or "").strip().upper()
        if code:
            return code[:3]
        family_name = str((driver or {}).get("familyName") or (driver or {}).get("last_name") or "").strip().upper()
        if len(family_name) >= 3:
            return family_name[:3]
        given_name = str((driver or {}).get("givenName") or (driver or {}).get("first_name") or "").strip().upper()
        return (family_name or given_name or "DRV")[:3]

    @staticmethod
    def _f1_driver_name(driver):
        given = str((driver or {}).get("givenName") or (driver or {}).get("first_name") or "").strip()
        family = str((driver or {}).get("familyName") or (driver or {}).get("last_name") or "").strip()
        full = str((driver or {}).get("full_name") or "").strip()
        return full or " ".join(part for part in (given, family) if part) or SportsDashboard._f1_driver_code(driver)

    @staticmethod
    def _f1_result_gap(item):
        time_info = (item or {}).get("Time") or {}
        if time_info.get("time"):
            return str(time_info.get("time")).strip()
        status = str((item or {}).get("status") or "").strip()
        return status or "-"

    @staticmethod
    def _select_f1_events(data, now):
        races = (data or {}).get("races") or []
        sessions = []
        for race in races:
            for session in race.get("sessions") or []:
                entry = dict(session)
                entry["race"] = race
                sessions.append(entry)
        sessions.sort(key=lambda item: item["start"])
        live_sessions = [session for session in sessions if SportsDashboard._is_f1_live_session(session, now)]
        upcoming_sessions = [session for session in sessions if session.get("start") and session["start"] >= now]
        recent_sessions = sorted(
            [session for session in sessions if session.get("start") and session["start"] < now],
            key=lambda item: item["start"],
            reverse=True,
        )
        next_session = upcoming_sessions[0] if upcoming_sessions else None
        live_session = live_sessions[0] if live_sessions else None
        weekend_race = SportsDashboard._f1_weekend_race(races, now)
        next_race = SportsDashboard._f1_next_race(races, now)
        recent_race = SportsDashboard._f1_recent_race(races, now)
        main_race = (live_session or {}).get("race") or weekend_race or next_race or recent_race
        if live_session:
            status = "LIVE"
        elif next_session or next_race:
            status = "NEXT"
        elif (data or {}).get("last_result"):
            status = "RECENT"
        else:
            status = "BREAK"
        weekend_sessions = list((main_race or {}).get("sessions") or [])
        return {
            "status": status,
            "live_session": live_session,
            "next_session": next_session,
            "recent_session": recent_sessions[0] if recent_sessions else None,
            "main_race": main_race,
            "next_race": next_race,
            "recent_race": recent_race,
            "weekend_sessions": weekend_sessions,
            "last_result": (data or {}).get("last_result"),
            "driver_standings": (data or {}).get("driver_standings") or [],
            "constructor_standings": (data or {}).get("constructor_standings") or [],
            "leaderboard": [],
            "weather": None,
        }

    @staticmethod
    def _is_f1_live_session(session, now):
        start = (session or {}).get("start")
        duration = (session or {}).get("duration") or timedelta(hours=2)
        if not isinstance(start, datetime) or now is None:
            return False
        return start - F1_SESSION_PREGAME_WINDOW <= now < start + duration

    @staticmethod
    def _f1_weekend_race(races, now):
        for race in races or []:
            sessions = race.get("sessions") or []
            starts = [session.get("start") for session in sessions if isinstance(session.get("start"), datetime)]
            if not starts:
                continue
            start = min(starts) - timedelta(hours=12)
            end = max(starts) + F1_SESSION_RESULT_WINDOW
            if start <= now <= end:
                return race
        return None

    @staticmethod
    def _f1_next_race(races, now):
        for race in races or []:
            sessions = race.get("sessions") or []
            starts = [session.get("start") for session in sessions if isinstance(session.get("start"), datetime)]
            candidate = min(starts) if starts else race.get("race_start")
            if isinstance(candidate, datetime) and candidate >= now:
                return race
        return None

    @staticmethod
    def _f1_recent_race(races, now):
        recent = []
        for race in races or []:
            race_start = race.get("race_start")
            if isinstance(race_start, datetime) and race_start < now:
                recent.append(race)
        return sorted(recent, key=lambda item: item.get("race_start"), reverse=True)[0] if recent else None

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
        force_refresh = self._force_refresh_requested(settings)
        has_compatible_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("snapshot"), dict)
        if (
            has_compatible_cache
            and not force_refresh
            and self._cache_is_fresh_seconds(cache, self._f1_live_refresh_seconds(settings), now_utc)
        ):
            return cache["snapshot"], "OPENF1 CACHE", cache.get("fetched_at")

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

    def _draw_f1_compact_panel(self, image, draw, bounds, selected, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        panel_w = x2 - x1 + 1
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["f1_accent"], COLORS["panel"], 22, 1)
        header_y = y1 + 8
        self._draw_f1_logo(image, draw, x1 + 15, header_y - 2, 74, 34)
        title, title_font = self._fit_text(draw, "FORMULA 1", 142, 17, bold=True, min_size=12)
        draw.text((x1 + 96, header_y + 1), title, font=title_font, fill=COLORS["text"])
        source_label = self._f1_source_label(selected, source_state)
        source_label, source_font = self._fit_text(draw, source_label, 124, 10, bold=True, min_size=7)
        draw.text((x1 + 97, header_y + 22), source_label, font=source_font, fill=COLORS["muted"])
        self._draw_f1_header_track_strip(draw, x1 + 214, header_y + 2, x2 - 92, y1 + 47)

        status = str((selected or {}).get("status") or "BREAK").upper()
        pill_text = "LIVE" if status == "LIVE" else ("NEXT" if status == "NEXT" else ("RESULT" if status == "RECENT" else "BREAK"))
        self._draw_status_pill(draw, x2 - 84, header_y + 4, pill_text, status == "LIVE")
        draw.line((x1 + 12, y1 + 48, x2 - 12, y1 + 48), fill=COLORS["border"], width=1)

        content_y = y1 + 58
        content_bottom = y2 - 8
        split_x = x1 + max(260, min(310, int(panel_w * 0.54)))
        left_x1 = x1 + 12
        left_x2 = split_x - 10
        right_x1 = split_x + 4
        right_x2 = x2 - 12
        draw.line((split_x - 3, content_y - 5, split_x - 3, content_bottom), fill=COLORS["border"], width=1)
        draw.line((split_x - 1, content_y - 5, split_x - 1, content_bottom), fill=COLORS["line"], width=1)

        self._draw_f1_main_card(image, draw, left_x1, content_y, left_x2, content_bottom, selected, now)
        self._draw_f1_side_column(draw, right_x1, right_x2, content_y, content_bottom, selected, now)

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

    def _draw_f1_main_card(self, image, draw, x1, y1, x2, y2, selected, now):
        status = str((selected or {}).get("status") or "BREAK").upper()
        race = (selected or {}).get("main_race") or {}
        live_session = (selected or {}).get("live_session")
        next_session = (selected or {}).get("next_session")
        focus_session = live_session or next_session
        accent = COLORS["f1_live"] if status == "LIVE" else COLORS["f1_accent"]
        draw.rounded_rectangle((x1 + 3, y1 + 3, x2 + 3, y2 + 3), radius=5, fill=COLORS["f1_shadow"])
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, y2 - 1), fill=accent)

        if status == "LIVE":
            tag = "\u6bd4\u8d5b\u4e2d"
        elif status == "RECENT":
            tag = "\u6700\u8fd1\u8d5b\u679c"
        elif status == "BREAK":
            tag = "\u7b49\u5f85\u8d5b\u5386"
        else:
            tag = "\u4e0b\u4e00\u7ad9"
        tag_text, tag_font = self._fit_text(draw, tag, 92, 11, bold=True, min_size=7)
        draw.rectangle((x1 + 14, y1 + 10, x1 + 112, y1 + 28), fill=COLORS["f1_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 19, y1 + 11), tag_text, font=tag_font, fill=COLORS["text"])

        date_source = (focus_session or {}).get("start") or race.get("race_start")
        date_text = date_source.strftime("%m/%d") if isinstance(date_source, datetime) else "--"
        date_text, date_font = self._fit_text(draw, date_text, 56, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 12), date_text, date_font, COLORS["muted"])

        if not race:
            title, title_font = self._fit_text(draw, "\u7b49\u5f85 F1 \u8d5b\u5386", x2 - x1 - 42, 22, bold=True, min_size=14)
            self._draw_centered_in_box(draw, (x1 + 18, y1 + 52, x2 - 18, y1 + 84), title, title_font, COLORS["text"])
            return

        race_name, race_font = self._fit_text(draw, race.get("race_name") or "Formula 1", x2 - x1 - 36, 22, bold=True, min_size=14)
        self._draw_centered_in_box(draw, (x1 + 18, y1 + 39, x2 - 18, y1 + 66), race_name, race_font, COLORS["text"])
        place = self._f1_place_label(race)
        place, place_font = self._fit_text(draw, place, x2 - x1 - 42, 11, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (x1 + 20, y1 + 67, x2 - 20, y1 + 83), place, place_font, COLORS["muted"])

        session_box_y1 = max(y1 + 103, y2 - 70)
        session_box_y2 = min(y2 - 18, session_box_y1 + 46)
        draw.rounded_rectangle((x1 + 16, session_box_y1, x2 - 16, session_box_y2), radius=5, fill=COLORS["panel_blue"], outline=COLORS["border"], width=1)
        label, label_font = self._fit_text(draw, "\u4e0b\u4e00\u8282" if status != "LIVE" else "\u5f53\u524d\u8d5b\u6bb5", 76, 10, bold=True, min_size=7)
        draw.text((x1 + 25, session_box_y1 + 5), label, font=label_font, fill=COLORS["muted"])
        if focus_session:
            session_text = self._f1_session_status_label(focus_session, now, status == "LIVE")
            secondary = self._f1_countdown_label(focus_session.get("start"), now) if status != "LIVE" else "LIVE TIMING"
        else:
            last_result = (selected or {}).get("last_result") or {}
            session_text = (last_result.get("top") or [{}])[0].get("driver_code") or "\u7b49\u5f85\u6570\u636e"
            secondary = "\u5168\u573a\u7ed3\u679c" if status == "RECENT" else "\u8d5b\u7a0b\u66f4\u65b0\u4e2d"
        session_text, session_font = self._fit_text(draw, session_text, x2 - x1 - 138, 16, bold=True, min_size=10)
        self._draw_right_aligned(draw, (x2 - 24, session_box_y1 + 4), session_text, session_font, COLORS["text"])
        secondary, secondary_font = self._fit_text(draw, secondary, x2 - x1 - 52, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 24, session_box_y1 + 27, x2 - 24, session_box_y2 - 3), secondary, secondary_font, COLORS["muted"])

        if (selected or {}).get("weather"):
            weather = self._f1_weather_label(selected.get("weather"))
            weather, weather_font = self._fit_text(draw, weather, x2 - x1 - 34, 9, bold=True, min_size=7)
            draw.text((x1 + 16, y2 - 15), weather, font=weather_font, fill=COLORS["f1_accent"])

    @staticmethod
    def _f1_place_label(race):
        parts = [
            str((race or {}).get("circuit_name") or "").strip(),
            str((race or {}).get("country") or "").strip(),
        ]
        return " / ".join(part for part in parts if part) or "Formula 1"

    def _f1_session_status_label(self, session, now, is_live):
        label = str((session or {}).get("label") or "F1").strip()
        start = (session or {}).get("start")
        if is_live:
            return f"{label} LIVE"
        if isinstance(start, datetime):
            return f"{label} {self._format_time(start)}"
        return label

    @staticmethod
    def _f1_countdown_label(start, now):
        if not isinstance(start, datetime) or not isinstance(now, datetime):
            return "\u8d5b\u7a0b\u66f4\u65b0\u4e2d"
        days = max(0, (start.date() - now.date()).days)
        if days > 0:
            return f"D-{days}"
        return "\u4eca\u65e5"

    @staticmethod
    def _f1_weather_label(weather):
        if not weather:
            return ""
        air = (weather or {}).get("air")
        track = (weather or {}).get("track")
        if air is None and track is None:
            return ""
        return f"\u5929\u6c14 AIR {air if air is not None else '-'} / TRACK {track if track is not None else '-'}"

    def _draw_f1_side_column(self, draw, x1, x2, y1, y2, selected, now):
        self._draw_f1_mini_section_header(draw, x1, x2, y1, "F1 WEEKEND")
        rows = self._f1_visible_weekend_sessions(selected, now)
        leaderboard = (selected or {}).get("leaderboard") or ((selected or {}).get("last_result") or {}).get("top") or []
        row_y = y1 + 26
        row_h = 28
        max_session_rows = 2 if leaderboard else 4
        visible_rows = rows[:max_session_rows]
        for index, session in enumerate(visible_rows):
            self._draw_f1_session_row(draw, x1, x2, row_y + index * row_h, session, now)
        used_rows = max(1, len(visible_rows))
        lower_y = row_y + used_rows * row_h + 15
        if leaderboard:
            title = "LIVE TIMING" if str((selected or {}).get("status") or "").upper() == "LIVE" else "LAST RACE"
            self._draw_f1_mini_section_header(draw, x1, x2, lower_y, title)
            available = max(0, y2 - (lower_y + 25))
            max_rows = max(1, min(3, available // 23))
            for index, row in enumerate(leaderboard[:max_rows]):
                top = lower_y + 25 + index * 23
                if top + 18 > y2:
                    break
                self._draw_f1_leaderboard_row(draw, x1, x2, top, row)
        else:
            self._draw_f1_mini_section_header(draw, x1, x2, lower_y, "STANDINGS")
            standings = (selected or {}).get("driver_standings") or []
            if not standings:
                text, text_font = self._fit_text(draw, "\u7b49\u5f85\u6570\u636e", x2 - x1 - 20, 11, bold=True, min_size=8)
                draw.text((x1 + 10, lower_y + 24), text, font=text_font, fill=COLORS["muted"])
                return
            for index, row in enumerate(standings[:4]):
                top = lower_y + 23 + index * 23
                if top + 18 > y2:
                    break
                self._draw_f1_standing_row(draw, x1, x2, top, row)

    @staticmethod
    def _f1_visible_weekend_sessions(selected, now):
        sessions = list((selected or {}).get("weekend_sessions") or [])
        if not sessions:
            candidate = (selected or {}).get("next_session") or (selected or {}).get("live_session")
            return [candidate] if candidate else []
        future_or_live = [
            session for session in sessions
            if SportsDashboard._is_f1_live_session(session, now)
            or (isinstance(session.get("start"), datetime) and session["start"] >= now)
        ]
        recent = [
            session for session in sessions
            if isinstance(session.get("start"), datetime) and session["start"] < now
        ][-2:]
        return (recent + future_or_live)[:4] if future_or_live else sessions[-4:]

    def _draw_f1_mini_section_header(self, draw, x1, x2, y, title):
        draw.rectangle((x1, y + 2, x1 + 8, y + 17), fill=COLORS["f1_accent"], outline=COLORS["border"], width=1)
        draw.text((x1 + 13, y - 2), title, font=self._font(13, True), fill=COLORS["text"])
        draw.line((x1, y + 19, x2, y + 19), fill=COLORS["border"], width=1)

    def _draw_f1_session_row(self, draw, x1, x2, y, session, now):
        if not session:
            return
        is_live = self._is_f1_live_session(session, now)
        start = session.get("start")
        row_h = 23
        draw.rounded_rectangle((x1, y, x2, y + row_h), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + row_h - 1), fill=COLORS["f1_live"] if is_live else COLORS["f1_accent"])
        label, label_font = self._fit_text(draw, str(session.get("label") or "F1"), 50, 10, bold=True, min_size=7)
        draw.text((x1 + 10, y + 3), label, font=label_font, fill=COLORS["muted"])
        time_text = "LIVE" if is_live else (self._format_time(start) if isinstance(start, datetime) else "--")
        time_text, time_font = self._fit_text(draw, time_text, 58, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 9, y + 3), time_text, time_font, COLORS["text"] if is_live else COLORS["muted"])

    def _draw_f1_leaderboard_row(self, draw, x1, x2, y, row):
        position = str((row or {}).get("position") or "-")
        driver = str((row or {}).get("driver_code") or "DRV")
        gap = str((row or {}).get("gap") or "-")
        team_color = (row or {}).get("team_color") or COLORS["f1_accent"]
        draw.rectangle((x1 + 1, y + 2, x1 + 5, y + 16), fill=team_color)
        draw.text((x1 + 10, y), f"P{position}", font=self._font(10, True), fill=COLORS["muted"])
        driver, driver_font = self._fit_text(draw, driver, 44, 12, bold=True, min_size=8)
        draw.text((x1 + 46, y - 1), driver, font=driver_font, fill=COLORS["text"])
        gap, gap_font = self._fit_text(draw, gap, x2 - x1 - 100, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 8, y), gap, gap_font, COLORS["muted"])

    def _draw_f1_standing_row(self, draw, x1, x2, y, row):
        position = str((row or {}).get("position") or "-")
        driver = str((row or {}).get("driver_code") or "DRV")
        points = str((row or {}).get("points") or "0")
        draw.text((x1 + 8, y), f"P{position}", font=self._font(10, True), fill=COLORS["muted"])
        driver, driver_font = self._fit_text(draw, driver, 58, 12, bold=True, min_size=8)
        draw.text((x1 + 44, y - 1), driver, font=driver_font, fill=COLORS["text"])
        points, points_font = self._fit_text(draw, f"{points} PTS", 70, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 8, y), points, points_font, COLORS["muted"])
