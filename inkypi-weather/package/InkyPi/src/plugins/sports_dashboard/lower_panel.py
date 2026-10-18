"""Which league owns the lower-left panel.

Live first: an F1 session in progress wins; otherwise any live game (NBA or
one of the hub leagues: MLB, WNBA, PGA, NFL, college football) takes the
panel, rotating every few minutes when several are live. With nothing live,
an F1 race weekend (first practice to the end of the race) comes next, then
the NBA during its season. In the NBA offseason the hub leagues rotate and
the next Grand Prix joins the rotation.

``nbaOffseasonPanelMode`` keeps its meaning: ``nba``/``off`` pins the NBA,
``hub``/``always`` always uses the hub rotation. ``f1PanelMode`` ``off``
removes F1 and ``always`` pins it.
"""

from runtime.refresh_contracts import TaskCancelled

from .common import *
from .common import _safe_exception_text

SportsDashboard = None

LOWER_LIVE_ROTATION_MINUTES = 10
_NBA_ONLY_MODES = {"off", "false", "disabled", "disable", "none", "nba"}
_HUB_ALWAYS_MODES = {"always", "on", "true", "hub", "live-hub", "offseason"}


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
        choice = SportsDashboard._choose_lower_panel(settings, nba_selected, hub_selected, hub_source_state, f1_selected, now)
        # Every loaded league records its live state so the live refresh
        # cadence follows whichever source is live, shown or not.
        if hub_selected is not None:
            self._write_offseason_hub_state(hub_selected, now, hub_source_state)
        if f1_selected is not None:
            self._write_f1_live_state(f1_selected, now, f1_source_state)
        kind = choice.get("kind")
        try:
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
    def _choose_lower_panel(settings, nba_selected, hub_selected, hub_source_state, f1_selected, now):
        """{"kind": "nba" | "hub" | "f1", "card": hub card or None}."""
        settings = settings or {}
        nba_selected = nba_selected or {}
        mode = str(settings.get("nbaOffseasonPanelMode") or "auto").strip().lower()
        f1_mode = SportsDashboard._f1_panel_mode(settings)
        nba_offseason = bool(nba_selected.get("offseason"))
        f1 = f1_selected if f1_mode != "off" else None
        f1_status = str((f1 or {}).get("status") or "").upper()
        if f1 is not None and f1_mode == "always":
            return {"kind": "f1", "card": None}
        if mode in _NBA_ONLY_MODES:
            return {"kind": "nba", "card": None}
        if f1_status == "LIVE":
            return {"kind": "f1", "card": None}
        # Placeholder hub data (fetch failed) never pre-empts the NBA.
        trusted_hub = hub_selected if hub_selected and (nba_offseason or hub_source_state != "HUB FALLBACK") else None
        hub_cards = list((trusted_hub or {}).get("cards") or [])
        live = []
        if not nba_offseason and nba_selected.get("live"):
            live.append({"kind": "nba", "card": None})
        live.extend({"kind": "hub", "card": card} for card in hub_cards if str(card.get("status") or "").upper() == "LIVE")
        if live:
            slot = int(now.timestamp() // (LOWER_LIVE_ROTATION_MINUTES * 60))
            return live[slot % len(live)]
        if f1 is not None and f1.get("weekend"):
            return {"kind": "f1", "card": None}
        if not nba_offseason and mode not in _HUB_ALWAYS_MODES:
            return {"kind": "nba", "card": None}
        if not trusted_hub:
            return {"kind": "f1", "card": None} if f1 is not None and f1_status in {"NEXT", "RECENT"} else {"kind": "nba", "card": None}
        primary = trusted_hub.get("primary")
        by_sport = {card.get("sport"): card for card in hub_cards}
        pool = [by_sport[sport] for sport in trusted_hub.get("rotation_pool") or [] if sport in by_sport]
        if any(SportsDashboard._offseason_hub_is_urgent_next_card(card, now) for card in pool):
            return {"kind": "hub", "card": primary}
        entries = [{"kind": "hub", "card": card} for card in pool]
        if f1 is not None and f1_status == "NEXT":
            entries.append({"kind": "f1", "card": None})
        if not entries:
            return {"kind": "hub", "card": primary} if primary else {"kind": "nba", "card": None}
        slot = int(now.timestamp() // max(1, OFFSEASON_HUB_ROTATION_MINUTES * 60))
        return entries[slot % len(entries)]
