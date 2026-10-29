"""Which content owns the lower-left panel.

Every league with something to show (NBA, the hub leagues MLB, WNBA, NHL, PGA,
NFL and college football, and F1) is scored by event_ranking: live first, then
how soon a game starts or how recently it ended, with finals and the
postseason above the regular season. So the World Series opener a day away
outranks tonight's regular-season NBA game.

When two or more games are live at once the panel becomes a live score wall
showing all of them (lower_wall_render); a single live game keeps its
league's own layout. An F1 session in progress wins when it outranks the
best live game. Otherwise the best-scored league is shown, and a league shown
at the last display gives way to a close runner-up (display_rotation).

``nbaOffseasonPanelMode`` keeps its meaning: ``nba``/``off`` pins the NBA,
``hub``/``always`` leaves the NBA out of the ranking. ``f1PanelMode`` ``off``
removes F1 and ``always`` pins it.
"""

from runtime.refresh_contracts import TaskCancelled

from .common import *
from .common import _safe_exception_text
from . import event_ranking

SportsDashboard = None

_NBA_ONLY_MODES = {"off", "false", "disabled", "disable", "none", "nba"}
_HUB_ALWAYS_MODES = {"always", "on", "true", "hub", "live-hub", "offseason"}
_HUB_TEAM_SPORTS = ("MLB", "WNBA", "NHL", "NFL", "NCAA")
# An F1 weekend whose session times are unknown still sits within hours of running.
_F1_WEEKEND_FALLBACK_POINTS = 50.0


class LowerPanelMixin:
    def _draw_lower_sports_region(self, image, draw, settings, device_config, timezone_info, now, box):
        settings = settings or {}
        nba_events, nba_source_state = self._load_nba_events(settings, timezone_info)
        nba_selected = self._select_nba_events(nba_events, now)
        hub_selected, hub_source_state = None, ""
        if SportsDashboard._lower_hub_wanted(settings):
            try:
                hub_selected, hub_source_state = self._load_offseason_hub(settings, timezone_info, now)
            except TaskCancelled:
                raise
            except Exception as exc:
                logger.warning("Lower panel hub load failed: %s", _safe_exception_text(exc))
        f1_selected, f1_source_state = self._load_lower_f1(settings, timezone_info, now)
        choice = SportsDashboard._choose_lower_panel(
            settings, nba_selected, hub_selected, hub_source_state, f1_selected, now,
            recent_shown=self._region_recent_shown(settings, "lower"),
        )
        # Every loaded league records its live state so the live refresh
        # cadence follows whichever source is live, shown or not.
        if hub_selected is not None:
            self._write_offseason_hub_state(hub_selected, now, hub_source_state)
        if f1_selected is not None:
            self._write_f1_live_state(f1_selected, now, f1_source_state)
        kind = choice.get("kind")
        self._record_region_choice("lower", now, [choice.get("key") or kind])
        try:
            if kind == "wall":
                self._draw_lower_live_wall(image, draw, box, choice["games"], now, settings)
                self._write_nba_live_state(nba_selected, now, nba_source_state)
                return SportsDashboard._lower_wall_provenance(
                    choice["games"], nba_source_state, hub_source_state,
                )
            if kind == "f1":
                if f1_selected.get("live_session"):
                    self._attach_f1_openf1_snapshot(f1_selected, settings)
                self._draw_f1_compact_panel(image, draw, box, f1_selected, f1_source_state, now)
                self._write_nba_live_state(nba_selected, now, nba_source_state)
                return self._sports_source_state_provenance(f1_source_state)
            if kind == "hub":
                self._draw_offseason_hub_compact_panel(image, draw, box, {**hub_selected, "primary": choice["card"]}, hub_source_state, now)
                self._write_nba_live_state(nba_selected, now, nba_source_state)
                return self._sports_source_state_provenance(hub_source_state)
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("Lower %s panel failed, falling back to NBA panel: %s", kind, _safe_exception_text(exc))
        nba_events = self._attach_nba_odds(nba_events, settings, device_config, timezone_info)
        nba_selected = self._select_nba_events(nba_events, now)
        self._write_nba_live_state(nba_selected, now, nba_source_state)
        self._draw_nba_compact_panel(image, draw, box, nba_selected, nba_source_state, now)
        return self._sports_source_state_provenance(nba_source_state)

    def _load_lower_f1(self, settings, timezone_info, now):
        if SportsDashboard._f1_panel_mode(settings) == "off":
            return None, ""
        try:
            data, source_state = self._load_f1_events(settings, timezone_info)
            return SportsDashboard._select_f1_events(data, now), source_state
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("Lower panel F1 load failed: %s", _safe_exception_text(exc))
            return None, ""

    @staticmethod
    def _lower_hub_wanted(settings):
        mode = str((settings or {}).get("nbaOffseasonPanelMode") or "auto").strip().lower()
        return mode not in _NBA_ONLY_MODES

    @staticmethod
    def _f1_panel_mode(settings):
        mode = str((settings or {}).get("f1PanelMode") or "auto").strip().lower()
        if mode in {"off", "false", "disabled", "disable", "none", "nba"}:
            return "off"
        if mode in {"always", "on", "true", "f1"}:
            return "always"
        return "auto"

    @staticmethod
    def _choose_lower_panel(settings, nba_selected, hub_selected, hub_source_state, f1_selected, now, recent_shown=()):
        """{"kind": "nba" | "hub" | "f1" | "wall", "key", "card", "games"}."""
        settings = settings or {}
        nba_selected = nba_selected or {}
        mode = str(settings.get("nbaOffseasonPanelMode") or "auto").strip().lower()
        f1_mode = SportsDashboard._f1_panel_mode(settings)
        nba_offseason = bool(nba_selected.get("offseason"))
        f1 = f1_selected if f1_mode != "off" else None
        if f1 is not None and f1_mode == "always":
            return {"kind": "f1", "key": "F1", "card": None}
        if mode in _NBA_ONLY_MODES:
            return {"kind": "nba", "key": "NBA", "card": None}
        # Placeholder hub data (fetch failed) never pre-empts the NBA.
        trusted_hub = hub_selected if hub_selected and (nba_offseason or hub_source_state != "HUB FALLBACK") else None
        hub_cards = [card for card in (trusted_hub or {}).get("cards") or [] if card]
        include_nba = not nba_offseason and mode not in _HUB_ALWAYS_MODES

        live_games = SportsDashboard._lower_live_games(nba_selected if include_nba else {}, hub_cards, now)
        f1_candidate = SportsDashboard._lower_f1_candidate(f1, now)
        f1_live = f1_candidate is not None and str((f1 or {}).get("status") or "").upper() == "LIVE"
        # The wall has no F1 row, so a session in progress that outranks every
        # live game keeps its own panel; otherwise F1 is ranked like any league.
        if len(live_games) >= 2 and not (f1_live and f1_candidate["score"] >= live_games[0]["score"]):
            return {"kind": "wall", "key": "WALL", "card": None, "games": live_games}

        candidates = []
        if include_nba:
            candidates.append(SportsDashboard._lower_nba_candidate(nba_selected, now))
        for card in hub_cards:
            candidate = SportsDashboard._lower_hub_candidate(card, now)
            if candidate is not None:
                candidates.append(candidate)
        if f1_candidate is not None:
            candidates.append(f1_candidate)
        if not candidates:
            return {"kind": "nba", "key": "NBA", "card": None}
        best = event_ranking.rank(candidates, recent_shown)[0]
        return {"kind": best["kind"], "key": best["key"], "card": best.get("card")}

    # Candidates ---------------------------------------------------------------

    @staticmethod
    def _lower_event_state(event):
        state = SportsDashboard._hub_event_state(event)
        return state if state in {"live", "final"} else "scheduled"

    @staticmethod
    def _lower_event_score(sport, event, now):
        event = event or {}
        state = SportsDashboard._lower_event_state(event)
        start = event.get("start")
        if sport == "PGA" and state == "live" and not event_ranking.golf_in_play(event):
            # Between rounds: worth a look, not a live game.
            state, start = "scheduled", now + timedelta(hours=8)
        tier = event_ranking.team_sport_tier(sport, event)
        return event_ranking.score(state, start, now, tier, end=event.get("end")), state, tier

    @staticmethod
    def _lower_live_games(nba_selected, hub_cards, now):
        """Every game in play across the lower-panel leagues, best first."""
        games = []
        for event in (nba_selected or {}).get("live") or []:
            score, _state, tier = SportsDashboard._lower_event_score("NBA", event, now)
            games.append({"sport": "NBA", "event": event, "score": score, "tier": tier})
        for card in hub_cards:
            sport = str(card.get("sport") or "").upper()
            for event in card.get("live") or []:
                score, state, tier = SportsDashboard._lower_event_score(sport, event, now)
                if state == "live":
                    games.append({"sport": sport, "event": event, "score": score, "tier": tier})
        order = {"NBA": 0, "NFL": 1, "MLB": 2, "WNBA": 3, "NHL": 4, "NCAA": 5, "PGA": 6}

        def start_value(game):
            start = game["event"].get("start")
            return start.timestamp() if isinstance(start, datetime) else 0.0

        return sorted(games, key=lambda game: (-game["score"], order.get(game["sport"], 9), -start_value(game)))

    @staticmethod
    def _lower_nba_candidate(nba_selected, now):
        # The season calendar holds every game; only the next week can matter.
        horizon = now + timedelta(days=7)
        upcoming = [event for event in nba_selected.get("upcoming") or []
                    if isinstance(event.get("start"), datetime) and event["start"] <= horizon]
        events = [*(nba_selected.get("live") or []), *upcoming, *(nba_selected.get("recent") or [])[:3]]
        best = max((SportsDashboard._lower_event_score("NBA", event, now)[0] for event in events), default=0.0)
        return {"kind": "nba", "key": "NBA", "card": None, "score": best, "order": 0}

    @staticmethod
    def _lower_hub_candidate(card, now):
        sport = str(card.get("sport") or "").upper()
        if str(card.get("status") or "").upper() == "BREAK" or not card.get("main"):
            return None
        events = [*(card.get("live") or []), *(card.get("upcoming") or [])[:6], *(card.get("recent") or [])[:3]]
        if not events:
            events = [card["main"]]
        best = max(SportsDashboard._lower_event_score(sport, event, now)[0] for event in events)
        order = 1 + (SportsDashboard._coerce_int(card.get("order")) or 0)
        return {"kind": "hub", "key": sport, "card": card, "score": best, "order": order}

    @staticmethod
    def _lower_f1_candidate(f1, now):
        if not f1:
            return None
        status = str(f1.get("status") or "").upper()
        live_session = f1.get("live_session")
        if status == "LIVE":
            tier = event_ranking.f1_session_tier((live_session or {}).get("title") or (live_session or {}).get("key")) \
                if live_session else "B"
            return {"kind": "f1", "key": "F1", "card": None, "order": 6,
                    "score": event_ranking.score("live", None, now, tier)}
        scores = []
        race = f1.get("main_race") or f1.get("next_race") or {}
        race_start = race.get("race_start")
        if isinstance(race_start, datetime):
            state = "final" if race_start < now else "scheduled"
            scores.append(event_ranking.score(state, race_start, now, "A", end=race_start + timedelta(hours=2)))
        session = f1.get("next_session") or {}
        if isinstance(session.get("start"), datetime):
            tier = event_ranking.f1_session_tier(session.get("title") or session.get("key"))
            scores.append(event_ranking.score("scheduled", session["start"], now, tier))
        if not scores:
            if f1.get("weekend"):
                scores.append(_F1_WEEKEND_FALLBACK_POINTS + event_ranking.tier_points("A"))
            else:
                return None
        return {"kind": "f1", "key": "F1", "card": None, "score": max(scores), "order": 6}

    @staticmethod
    def _lower_wall_provenance(games, nba_source_state, hub_source_state):
        states = []
        if any(game["sport"] == "NBA" for game in games):
            states.append(nba_source_state)
        if any(game["sport"] != "NBA" for game in games):
            states.append(hub_source_state)
        provenances = [SportsDashboard._sports_source_state_provenance(state) for state in states]
        order = [SourceProvenance.LOCAL_FALLBACK, SourceProvenance.STALE_CACHE,
                 SourceProvenance.FRESH_CACHE, SourceProvenance.LIVE]
        known = [value for value in provenances if value in order]
        return min(known, key=order.index) if known else SourceProvenance.LOCAL_FALLBACK
