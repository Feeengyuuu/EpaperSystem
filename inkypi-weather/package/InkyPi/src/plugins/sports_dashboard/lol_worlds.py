"""League of Legends World Championship (Worlds) card for the esports sidebar.

Worlds reuses the lolesports schedule and live pipeline shared with LPL, LCK
and MSI. Inside the official tournament window the card is a sidebar
candidate, and on match days it is pinned: while a Worlds match is live or the
next one starts within WORLDS_PIN_LEAD it takes the right esports sidebar
ahead of CS2, TI, EWC and the regional LoL leagues. On rest days it competes
like any other league (live first, then the earliest next match). Outside the
window the card stays off the sidebar and its schedule is not requested; the
sidebar override can still force it for a preview.
"""

from runtime.refresh_contracts import TaskCancelled

from .common import *
from .common import _safe_exception_text


SportsDashboard = None

WORLDS_LEAGUE_KEY = "WORLDS"
WORLDS_SIDEBAR_PRIORITY = 3
WORLDS_SCHEDULE_MARGIN = timedelta(days=1)
WORLDS_PIN_LEAD = timedelta(hours=24)


class LolWorldsMixin:
    def _load_worlds_sidebar_card(self, settings, timezone_info, now, force=False):
        events, source_state, tournament = self._load_worlds_events(
            settings,
            timezone_info,
            now,
            force=force,
        )
        selected = self._select_worlds_events(events, now)
        window_start, window_end = self._worlds_tournament_window(tournament, events)
        in_window = bool(
            self._worlds_window_contains(window_start, window_end, now)
            and self._lol_selected_has_displayable_event(selected)
        )
        pinned_until = (
            SportsDashboard._worlds_match_day_pin_until(selected, now, window_end)
            if in_window
            else None
        )
        return {
            "league_key": WORLDS_LEAGUE_KEY,
            "selected": selected,
            "source_state": source_state,
            "priority": WORLDS_SIDEBAR_PRIORITY,
            "in_window": in_window,
            "sidebar_pinned": pinned_until is not None,
            "pinned_until": pinned_until,
        }

    @staticmethod
    def _worlds_match_day_pin_until(selected, now, window_end):
        """When the match-day pin ends, or None on a rest day.

        Worlds is pinned while a match is live or the next one starts within
        WORLDS_PIN_LEAD; the pin lasts through the inferred live window of the
        latest such match, never past the tournament window.
        """
        selected = selected or {}
        anchors = [
            event["start"]
            for event in selected.get("live") or []
            if isinstance(event.get("start"), datetime)
        ]
        anchors += [
            event["start"]
            for event in selected.get("upcoming") or []
            if isinstance(event.get("start"), datetime)
            and event["start"] - now <= WORLDS_PIN_LEAD
        ]
        if not anchors:
            return None
        until = max(max(anchors) + LPL_INFERRED_LIVE_WINDOW, now)
        if isinstance(window_end, datetime):
            until = min(until, window_end)
        return until

    def _load_worlds_events(self, settings, timezone_info, now, force=False):
        tournament = None
        try:
            tournament = self._load_worlds_tournament(settings, timezone_info, now)
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning(
                "Worlds tournament metadata fetch failed: %s",
                _safe_exception_text(exc),
            )
        if not force and not self._worlds_schedule_window_open(tournament, now):
            # Outside the tournament the card is never shown; skip the request.
            return [], "WORLDS IDLE", tournament
        try:
            events = self._fetch_worlds_events(
                settings,
                timezone_info,
                now,
                tournament=tournament,
            )
            if events:
                return events, "WORLDS LIVE DATA", tournament
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("Worlds schedule fetch failed: %s", _safe_exception_text(exc))
        return [], "WORLDS NO DATA", tournament

    def _fetch_worlds_events(self, settings, timezone_info, now, tournament=None):
        # The league schedule also carries the previous edition; keep only the
        # selected tournament's matches.
        events = self._fetch_lol_esports_events(
            settings,
            timezone_info,
            "worldsLeagueId",
            DEFAULT_WORLDS_LEAGUE_ID,
            WORLDS_LEAGUE_KEY,
            force_live_endpoint=self._msi_tournament_live_window_active(tournament, now),
            now=now,
        )
        return self._filter_msi_events_to_tournament(events, tournament, now)

    def _load_worlds_tournament(self, settings, timezone_info, now):
        league_id = str(settings.get("worldsLeagueId") or DEFAULT_WORLDS_LEAGUE_ID).strip()
        path = self._worlds_tournament_state_path()
        cache = self._read_json_file(path)
        cache_hours = self._int_setting(
            settings,
            "worldsTournamentCacheHours",
            DEFAULT_WORLDS_TOURNAMENT_CACHE_HOURS,
            1,
            48,
        )
        now_utc = (
            now if isinstance(now, datetime) else datetime.now(timezone.utc)
        ).astimezone(timezone.utc)
        cached_tournament = self._cached_lol_league_tournament(
            cache,
            league_id,
            timezone_info,
            WORLDS_TOURNAMENT_STATE_VERSION,
        )
        if cached_tournament and self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return cached_tournament
        try:
            tournament = self._fetch_lol_league_tournament(
                league_id,
                timezone_info,
                now,
                "Worlds",
            )
        except Exception:
            if cached_tournament:
                return cached_tournament
            raise
        self._write_lol_league_tournament_state(
            path,
            WORLDS_TOURNAMENT_STATE_VERSION,
            tournament,
            league_id,
            now_utc,
        )
        return tournament

    @staticmethod
    def _select_worlds_events(events, now):
        events = [
            event
            for event in (events or [])
            if SportsDashboard._is_lol_displayable_match_event(event)
        ]
        return SportsDashboard._select_lol_events(events, now, include_lpl_featured=False)

    @staticmethod
    def _worlds_tournament_window(tournament, events):
        start = (tournament or {}).get("start")
        end = (tournament or {}).get("end")
        if isinstance(start, datetime) and isinstance(end, datetime):
            return start, end
        # Without tournament metadata, the fetched matches bound the window.
        starts = [
            event["start"]
            for event in events or []
            if isinstance(event.get("start"), datetime)
        ]
        if not starts:
            return None, None
        return min(starts), max(starts) + LPL_INFERRED_LIVE_WINDOW

    @staticmethod
    def _worlds_schedule_window_open(tournament, now):
        start = (tournament or {}).get("start")
        end = (tournament or {}).get("end")
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            # Unknown dates: the fetched matches decide the window instead.
            return True
        if not isinstance(now, datetime):
            return False
        return start - WORLDS_SCHEDULE_MARGIN <= now <= end + WORLDS_SCHEDULE_MARGIN

    @staticmethod
    def _worlds_window_contains(start, end, now):
        if not all(isinstance(value, datetime) for value in (start, end, now)):
            return False
        return start <= now <= end

    def _worlds_sidebar_pin_marker_active(self, current_dt=None):
        state = self._read_json_file(self._worlds_live_state_path())
        if state.get("version") != WORLDS_LIVE_STATE_VERSION:
            return False
        pinned_until = self._parse_live_state_datetime(state.get("sidebar_pinned_until"))
        if pinned_until is None:
            return False
        current = current_dt if isinstance(current_dt, datetime) else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc) <= pinned_until

    def _release_worlds_sidebar_pin(self):
        path = self._worlds_live_state_path()
        state = self._read_json_file(path)
        if not state.get("sidebar_pinned_until"):
            return
        released = dict(state)
        released["sidebar_pinned_until"] = None
        try:
            self._write_json_file(path, released)
        except OSError as exc:
            logger.warning("Failed to release the Worlds sidebar pin: %s", exc)

    def _worlds_tournament_state_path(self):
        return self._sports_dashboard_cache_dir() / "worlds_tournament_state.json"

    def _worlds_live_state_path(self):
        return self._sports_dashboard_cache_dir() / "worlds_live_state.json"
