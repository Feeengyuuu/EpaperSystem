"""League of Legends World Championship (Worlds) card for the esports sidebar.

Worlds reuses the lolesports schedule and live pipeline shared with LPL, LCK
and MSI. Inside the official tournament window the card is a sidebar
candidate, and on match days it is pinned: while a Worlds match is live or the
next one starts within WORLDS_PIN_LEAD it takes the right esports sidebar
ahead of CS2, TI, EWC and the regional LoL leagues. On rest days it competes
like any other league (live first, then the earliest next match). Outside the
window the card stays off the sidebar and its schedule is not requested; the
sidebar override can still force it for a preview.

Every LoL league's matches (Worlds, LPL, LCK, MSI) also take the crowd odds
Polymarket prices them at: one keyless request for its LoL moneyline markets
(about 50 KB), cached for half an hour, gives each priced match its decimal
odds, win shares, best-of and traded volume. Bookmaker odds already on a match
are kept.
"""

import json

from runtime.refresh_contracts import TaskCancelled

from .common import *
from .common import _safe_exception_text


SportsDashboard = None

WORLDS_LEAGUE_KEY = "WORLDS"
WORLDS_SIDEBAR_PRIORITY = 3
WORLDS_SCHEDULE_MARGIN = timedelta(days=1)
WORLDS_PIN_LEAD = timedelta(hours=24)
POLYMARKET_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
# Polymarket's "league of legends" tag: its open moneyline markets are the LoL matches it prices.
POLYMARKET_LOL_TAG_ID = 65
POLYMARKET_CACHE_SECONDS = 30 * 60
POLYMARKET_MARKETS_LIMIT = 40
# lolesports team codes and the names Polymarket gives those teams, where neither the code nor
# the initials of the name already match.
POLYMARKET_TEAM_NAMES = {
    "BLG": ("Bilibili Gaming",), "GEN": ("Gen.G",), "HLE": ("Hanwha Life Esports",), "DK": ("Dplus KIA", "Dplus"),
    "KT": ("KT Rolster",), "MKOI": ("Movistar KOI",), "FLY": ("FlyQuest",), "G2": ("G2 Esports",),
    "FNC": ("Fnatic",), "TES": ("Top Esports",), "JDG": ("JD Gaming",), "IG": ("Invictus Gaming",),
    "WBG": ("Weibo Gaming",), "NIP": ("Ninjas in Pyjamas",), "LNG": ("LNG Esports",), "PSG": ("PSG Talon",),
    "GAM": ("GAM Esports",), "VKS": ("Vivo Keyd Stars",), "SHG": ("Fukuoka SoftBank HAWKS gaming",),
    "C9": ("Cloud9",), "100": ("100 Thieves",), "TH": ("Team Heretics",), "VIT": ("Team Vitality",),
    "BDS": ("Team BDS",), "NS": ("Nongshim RedForce", "NS RedForce"), "BRO": ("OKSavingsBank BRION", "BRION"),
    "DNF": ("DN Freecs",), "FOX": ("BNK FearX", "FearX"), "AL": ("Anyone's Legend",),
}


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
                events = self._attach_polymarket_odds(events, self._polymarket_lol_offers(settings, timezone_info))
                return events, "WORLDS LIVE DATA", tournament
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("Worlds schedule fetch failed: %s", _safe_exception_text(exc))
        return [], "WORLDS NO DATA", tournament

    def _polymarket_lol_offers(self, settings, timezone_info):
        """Polymarket's priced LoL matches as odds offers, from a half-hour cache or one request;
        [] when there are none or the market list is unavailable."""
        path = self._sports_dashboard_cache_dir() / "polymarket_lol.json"
        cache = self._read_json_file(path)
        markets = cache.get("markets") if isinstance(cache.get("markets"), list) else None
        now_utc = datetime.now(timezone.utc)
        try:
            fetched = datetime.fromisoformat(str(cache.get("fetched_at") or ""))
        except ValueError:
            fetched = None
        fresh = markets is not None and fetched is not None and (now_utc - fetched).total_seconds() < POLYMARKET_CACHE_SECONDS
        if not fresh and not self._provider_cache_only(settings):
            try:
                response = get_http_session().get(
                    POLYMARKET_MARKETS_URL,
                    params={"tag_id": POLYMARKET_LOL_TAG_ID, "closed": "false", "sports_market_types": "moneyline",
                            "limit": POLYMARKET_MARKETS_LIMIT, "order": "startDate", "ascending": "false"},
                    headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"},
                    timeout=10,
                )
                response.raise_for_status()
                rows = response.json()
                markets = [SportsDashboard._polymarket_compact(row) for row in rows if isinstance(row, dict)] \
                    if isinstance(rows, list) else []
                self._write_json_file(path, {"fetched_at": now_utc.isoformat(), "markets": markets})
            except TaskCancelled:
                raise
            except Exception as exc:
                logger.warning("Polymarket LoL odds unavailable: %s", _safe_exception_text(exc))
        offers = [SportsDashboard._polymarket_offer(market, timezone_info) for market in markets or []]
        return [offer for offer in offers if offer]

    @staticmethod
    def _polymarket_compact(row):
        """A market as kept in the cache: its question, two outcomes and prices, start and volume."""
        def listed(value):
            if isinstance(value, list):
                return value
            try:
                parsed = json.loads(str(value or "[]"))
            except ValueError:
                return []
            return parsed if isinstance(parsed, list) else []

        try:
            volume = max(0.0, float(row.get("volume") or 0))
        except (TypeError, ValueError):
            volume = 0.0
        return {"question": str(row.get("question") or "")[:160], "outcomes": [str(v)[:60] for v in listed(row.get("outcomes"))],
                "prices": [str(v)[:12] for v in listed(row.get("outcomePrices"))],
                "start": str(row.get("gameStartTime") or row.get("startDate") or "")[:40], "volume": volume}

    @staticmethod
    def _polymarket_offer(market, timezone_info):
        """An odds offer from a two-way moneyline: decimal odds (1 / price), the win shares, the
        best-of from the question and the traded volume; None for anything else."""
        outcomes, prices = market.get("outcomes") or [], market.get("prices") or []
        if len(outcomes) != 2 or len(prices) != 2:
            return None
        try:
            shares = [float(price) for price in prices]
        except (TypeError, ValueError):
            return None
        if not all(0.0 < share < 1.0 for share in shares):
            return None
        start_text = str(market.get("start") or "").strip().replace(" ", "T", 1)
        if re.search(r"[+-]\d{2}$", start_text):
            start_text += ":00"
        best_of = re.search(r"\(BO(\d)\)", str(market.get("question") or ""), flags=re.IGNORECASE)
        return {
            "start": SportsDashboard._parse_start_time(start_text, timezone_info) if start_text else None,
            "home_team": outcomes[0], "away_team": outcomes[1],
            "home_odds": f"{1 / shares[0]:.2f}", "away_odds": f"{1 / shares[1]:.2f}",
            "home_share": shares[0], "away_share": shares[1],
            "best_of": int(best_of.group(1)) if best_of else None,
            "volume": market.get("volume") or 0.0,
        }

    def _attach_polymarket_odds(self, events, offers):
        """``events`` with Polymarket's odds on each match it prices (by both teams, within the
        odds feeds' time window); bookmaker odds already on a match stay."""
        if not offers:
            return events
        priced = []
        for event in events or []:
            match = None
            if not (event.get("odds") or {}).get("team_a"):
                match = next(((offer, swapped) for offer in offers for swapped in (False, True)
                              if SportsDashboard._lpl_odds_time_matches(event.get("start"), offer.get("start"))
                              and SportsDashboard._polymarket_team_is(event.get("team_b" if swapped else "team_a"),
                                                                      offer["home_team"])
                              and SportsDashboard._polymarket_team_is(event.get("team_a" if swapped else "team_b"),
                                                                      offer["away_team"])), None)
            if match is None:
                priced.append(event)
                continue
            offer, swapped = match
            side_a, side_b = ("away", "home") if swapped else ("home", "away")
            priced.append({**event, "odds": {
                "team_a": offer[f"{side_a}_odds"], "team_b": offer[f"{side_b}_odds"],
                "share_a": offer[f"{side_a}_share"], "share_b": offer[f"{side_b}_share"],
                "best_of": offer["best_of"], "volume": offer["volume"], "bookmaker": "Polymarket",
            }})
        return priced

    @staticmethod
    def _polymarket_team_is(code, name):
        """Whether a lolesports team (its code) is the team Polymarket names: the same name, a
        known Polymarket name for the code, or a name whose initials spell the code."""
        code_key = SportsDashboard._normalize_odds_team_name(code)
        name_key = SportsDashboard._normalize_odds_team_name(name)
        if not code_key or not name_key or code_key == "tbd":
            return False
        if code_key == name_key:
            return True
        aliases = {SportsDashboard._normalize_odds_team_name(alias)
                   for alias in POLYMARKET_TEAM_NAMES.get(str(code or "").strip().upper(), ())}
        if name_key in aliases:
            return True
        initials = "".join(word[0] for word in re.findall(r"[A-Za-z0-9]+", str(name or ""))).lower()
        return len(code_key) >= 2 and initials == code_key

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
