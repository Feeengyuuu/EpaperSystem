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

Once the knockout stage is drawn, the card's lower half becomes the bracket:
lolesports' standings for the tournament (about 23 KB, cached for twenty
minutes) give the quarter-finals, semi-finals and final with each side's
games won, the schedule adds each match's day and live score, and Polymarket's
"Worlds <year>: Winner" event (about 80 KB, half an hour) adds each team still
in the running's chance of the title. Either one missing leaves the card's
usual upcoming and recent rows.
"""

import json

from runtime.refresh_contracts import TaskCancelled

from .common import *
from .common import _safe_exception_text
from .scorebug_kit import _kit_result_ink


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
LOLESPORTS_STANDINGS_URL = "https://esports-api.lolesports.com/persisted/gw/getStandings"
WORLDS_BRACKET_CACHE_SECONDS = 20 * 60
# The knockout stage's matches in lolesports' order: four quarter-finals, two semi-finals, the final.
WORLDS_BRACKET_MATCHES = 7
WORLDS_KNOCKOUT_BLOCKS = ("quarter", "semi", "final", "knockout", "playoff", "bracket")
POLYMARKET_EVENTS_URL = "https://gamma-api.polymarket.com/events"
WORLDS_TITLE_EVENT_SLUG = "worlds-{year}-winner"
WORLDS_TITLE_ODDS_LIMIT = 4
# The bracket on the card: column captions, a match box's team rows and logos, the gaps between the
# boxes of a pair and between columns, and the title-odds strip under it.
WORLDS_BRACKET_CAPTIONS = ("八强", "半决赛", "决赛")
WORLDS_BRACKET_ROW, WORLDS_BRACKET_LOGO, WORLDS_BRACKET_PAIR_GAP, WORLDS_BRACKET_COLUMN_GAP = 13, 11, 4, 7
WORLDS_TITLE_STRIP_HEIGHT, WORLDS_TITLE_LOGO = 40, 15
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
        if (in_window or force) and events:
            selected = self._attach_worlds_knockouts(selected, events, settings, tournament, now)
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

    # Knockout bracket and title odds ---------------------------------------------------------

    def _attach_worlds_knockouts(self, selected, events, settings, tournament, now):
        """``selected`` with the knockout bracket and the title odds once the bracket has teams."""
        tournament_id = str((tournament or {}).get("id") or "").strip()
        if not tournament_id or not SportsDashboard._worlds_knockouts_drawn(events):
            return selected
        try:
            matches = self._worlds_knockout_matches(settings, tournament_id)
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("Worlds knockout bracket unavailable: %s", _safe_exception_text(exc))
            return selected
        if len(matches) < WORLDS_BRACKET_MATCHES or not any(
                team["code"] != "TBD" for match in matches for team in match["teams"]):
            return selected
        by_match = {str(event.get("source_match_id") or ""): event for event in events or [] if event.get("source_match_id")}
        bracket = [SportsDashboard._worlds_bracket_match(match, by_match.get(match["id"])) for match in matches]
        try:
            title_odds = self._worlds_title_odds(settings, now)
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("Worlds title odds unavailable: %s", _safe_exception_text(exc))
            title_odds = []
        teams = {team["code"]: team for match in bracket for team in match["teams"] if team["code"] != "TBD"}
        priced = []
        for offer in title_odds:
            code = next((code for code in teams if SportsDashboard._polymarket_team_is(code, offer["name"])), None)
            if code:
                priced.append({**offer, "code": code, "logo": teams[code]["logo"]})
        return {**selected, "bracket": bracket, "title_odds": priced[:WORLDS_TITLE_ODDS_LIMIT]}

    @staticmethod
    def _worlds_knockouts_drawn(events):
        """Whether the schedule has knockout matches and every group-stage match is played, so the
        bracket is set (no standings request before that)."""
        knockout = False
        for event in events or []:
            block = str(event.get("block") or "").lower()
            if any(word in block for word in WORLDS_KNOCKOUT_BLOCKS):
                knockout = True
            elif str(event.get("state") or "").lower() not in {"completed", "final", "post"}:
                return False
        return knockout

    def _worlds_knockout_matches(self, settings, tournament_id):
        """The knockout stage's matches from a twenty-minute cache or one standings request."""
        path = self._sports_dashboard_cache_dir() / "worlds_knockouts.json"
        cache = self._read_json_file(path)
        now_utc = datetime.now(timezone.utc)
        cached = cache.get("matches") if cache.get("tournament_id") == tournament_id else None
        if isinstance(cached, list) and (self._provider_cache_only(settings)
                                         or self._cache_is_fresh_seconds(cache, WORLDS_BRACKET_CACHE_SECONDS, now_utc)):
            return cached
        if self._provider_cache_only(settings):
            return []
        try:
            response = get_http_session().get(
                LOLESPORTS_STANDINGS_URL, params={"hl": "en-US", "tournamentId": tournament_id},
                headers={"x-api-key": LOLESPORTS_API_KEY, "Accept": "application/json"}, timeout=15,
            )
            response.raise_for_status()
            matches = SportsDashboard._parse_worlds_knockouts(response.json())
        except TaskCancelled:
            raise
        except Exception:
            if isinstance(cached, list):
                return cached
            raise
        self._write_json_file(path, {"tournament_id": tournament_id, "fetched_at": now_utc.isoformat(), "matches": matches})
        return matches

    @staticmethod
    def _parse_worlds_knockouts(payload):
        """[{"id", "state", "teams": [{"code", "logo", "wins", "outcome"}]}] of the knockout stage."""
        standings = ((payload or {}).get("data") or {}).get("standings") or []
        for standing in standings if isinstance(standings, list) else []:
            for stage in (standing or {}).get("stages") or []:
                name = f"{stage.get('slug') or ''} {stage.get('name') or ''}".lower()
                if "knockout" not in name and "bracket" not in name and "playoff" not in name:
                    continue
                matches = []
                for section in stage.get("sections") or []:
                    for match in (section or {}).get("matches") or []:
                        if not isinstance(match, Mapping):
                            continue
                        teams = match.get("teams") or []
                        sides = []
                        for index in range(2):
                            code, wins, logo = SportsDashboard._team_info(teams, index)
                            outcome = str(((teams[index] or {}).get("result") or {}).get("outcome") or "") if index < len(teams) else ""
                            sides.append({"code": code[:8], "logo": logo, "wins": wins, "outcome": outcome.lower()})
                        matches.append({"id": str(match.get("id") or ""), "state": str(match.get("state") or "").lower(),
                                        "teams": sides})
                return matches
        return []

    @staticmethod
    def _worlds_bracket_match(match, event):
        """A bracket match for the card: lolesports' teams and games won, with the schedule's day
        and, while it is played, its live games won."""
        event = event or {}
        state = str(match.get("state") or "")
        live = str(event.get("state") or "").lower() in {"inprogress", "in_progress", "live"} or state == "inprogress"
        teams = [dict(team) for team in match.get("teams") or []]
        if live:
            for team, side in zip(teams, ("a", "b")):
                if event.get(f"wins_{side}") is not None and str(event.get(f"team_{side}") or "") == team["code"]:
                    team["wins"] = event.get(f"wins_{side}")
        return {"id": match.get("id"), "state": "live" if live else ("final" if state == "completed" else "scheduled"),
                "start": event.get("start") if isinstance(event.get("start"), datetime) else None, "teams": teams}

    def _worlds_title_odds(self, settings, now):
        """[{"name", "share", "week"}] of Polymarket's title market for the edition, best first: only
        the open markets of teams still in it (its "Team I" / "Other" placeholders are inactive)."""
        year = getattr(now, "year", None) or datetime.now(timezone.utc).year
        slug = WORLDS_TITLE_EVENT_SLUG.format(year=year)
        path = self._sports_dashboard_cache_dir() / "polymarket_worlds_winner.json"
        cache = self._read_json_file(path)
        now_utc = datetime.now(timezone.utc)
        cached = cache.get("teams") if cache.get("slug") == slug else None
        if isinstance(cached, list) and (self._provider_cache_only(settings)
                                         or self._cache_is_fresh_seconds(cache, POLYMARKET_CACHE_SECONDS, now_utc)):
            return cached
        if self._provider_cache_only(settings):
            return []
        try:
            response = get_http_session().get(
                POLYMARKET_EVENTS_URL, params={"slug": slug},
                headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"}, timeout=10,
            )
            response.raise_for_status()
            teams = SportsDashboard._parse_worlds_title_odds(response.json())
        except TaskCancelled:
            raise
        except Exception:
            if isinstance(cached, list):
                return cached
            raise
        self._write_json_file(path, {"slug": slug, "fetched_at": now_utc.isoformat(), "teams": teams})
        return teams

    @staticmethod
    def _parse_worlds_title_odds(payload):
        events = payload if isinstance(payload, list) else [payload]
        teams = []
        for event in events:
            for market in (event or {}).get("markets") or [] if isinstance(event, Mapping) else []:
                if not isinstance(market, Mapping) or not market.get("active") or market.get("closed"):
                    continue
                prices = LolWorldsMixin._polymarket_compact({"outcomePrices": market.get("outcomePrices")})["prices"]
                try:
                    share = float(prices[0])
                    week = float(market.get("oneWeekPriceChange") or 0)
                except (IndexError, TypeError, ValueError):
                    continue
                name = str(market.get("groupItemTitle") or "").strip()[:60]
                if name and 0.0 < share < 1.0:
                    teams.append({"name": name, "share": share, "week": week})
        return sorted(teams, key=lambda team: -team["share"])[:12]

    def _draw_worlds_knockouts(self, image, draw, right_x, right_w, y, bottom, selected, now):
        """The card's lower half in the knockout stage: the bracket, then the title odds."""
        accent = self._lol_sidebar_color("WORLDS", "accent")
        self._draw_section_header(draw, right_x, right_w, y, "KNOCKOUTS", accent)
        x1, x2 = right_x + 14, right_x + right_w - 14
        odds = list(selected.get("title_odds") or [])
        strip = WORLDS_TITLE_STRIP_HEIGHT if odds else 0
        bracket_bottom = bottom - 6 - strip
        self._draw_worlds_bracket(image, draw, (x1, y + 32, x2, bracket_bottom), selected.get("bracket") or [], accent)
        if odds:
            self._draw_worlds_title_odds(image, draw, (x1, bracket_bottom + 4, x2, bottom - 4), odds, accent)

    def _draw_worlds_bracket(self, image, draw, box, bracket, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        gap = WORLDS_BRACKET_COLUMN_GAP
        width = x2 - x1 - 2 * gap
        columns = [round(width * 0.37), round(width * 0.33)]
        columns.append(width - sum(columns))
        lefts = [x1, x1 + columns[0] + gap, x1 + columns[0] + columns[1] + 2 * gap]
        caption_font = self._font(8, False)
        for caption, left, column in zip(WORLDS_BRACKET_CAPTIONS, lefts, columns):
            self._draw_centered_in_box(draw, (left, y1, left + column, y1 + 10), caption, caption_font, COLORS["text"])
        top = y1 + 13
        box_h = 2 * WORLDS_BRACKET_ROW + 2
        spare = (y2 - top) - 4 * box_h - 2 * WORLDS_BRACKET_PAIR_GAP
        pair_gap = max(WORLDS_BRACKET_PAIR_GAP, spare)
        tops = [top, top + box_h + WORLDS_BRACKET_PAIR_GAP]
        tops += [tops[1] + box_h + pair_gap, tops[1] + 2 * box_h + pair_gap + WORLDS_BRACKET_PAIR_GAP]
        centers = [value + box_h // 2 for value in tops]
        semis = [(centers[0] + centers[1]) // 2, (centers[2] + centers[3]) // 2]
        final = (semis[0] + semis[1]) // 2
        layout = [(0, center) for center in centers] + [(1, center) for center in semis] + [(2, final)]
        for (column, center), match in zip(layout, bracket[:WORLDS_BRACKET_MATCHES]):
            left = lefts[column]
            right = left + columns[column]
            self._draw_worlds_bracket_match(image, draw, (left, center - box_h // 2, right, center - box_h // 2 + box_h),
                                            match, accent, final=column == 2)
        # Each pair's winners meet in the next column.
        for column, pairs in ((0, ((centers[0], centers[1], semis[0]), (centers[2], centers[3], semis[1]))),
                              (1, ((semis[0], semis[1], final),))):
            right = lefts[column] + columns[column]
            middle = right + gap // 2
            for upper, lower, target in pairs:
                draw.line((right, upper, middle, upper), fill=COLORS["border"], width=1)
                draw.line((right, lower, middle, lower), fill=COLORS["border"], width=1)
                draw.line((middle, upper, middle, lower), fill=COLORS["border"], width=1)
                draw.line((middle, target, lefts[column + 1], target), fill=COLORS["border"], width=1)

    def _draw_worlds_bracket_match(self, image, draw, box, match, accent, final=False):
        """A match as two team rows (logo, code, games won: the winner's emerald, the loser's
        crimson); a match still to play shows its day, a live one is outlined in the live colour, the
        final in the card's accent."""
        x1, y1, x2, y2 = [int(value) for value in box]
        state = match.get("state")
        live = state == "live"
        outline = self._lol_sidebar_color("WORLDS", "live") if live else (accent if final else COLORS["border"])
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=outline, width=2 if live or final else 1)
        teams = list(match.get("teams") or [])[:2]
        start = match.get("start")
        day = f"{start.month}/{start.day}" if state == "scheduled" and isinstance(start, datetime) else ""
        day_w = self._text_width(draw, day, self._font(8, False)) + 3 if day else 0
        if day:
            self._draw_text_in_box(draw, (x2 - 3 - day_w, y1, x2 - 3, y2), day, self._font(8, False), COLORS["text"], align="right")
        winner = next((index for index, team in enumerate(teams) if team.get("outcome") == "win"), None)
        if state == "final" and winner is None:
            wins = [SportsDashboard._lpl_int_value(team.get("wins")) or 0 for team in teams]
            winner = 0 if wins[0] > wins[1] else 1 if wins[1] > wins[0] else None
        for index, team in enumerate(teams):
            top = y1 + 1 + index * WORLDS_BRACKET_ROW
            bottom = top + WORLDS_BRACKET_ROW
            code = str(team.get("code") or "TBD")
            if code == "TBD":
                self._draw_text_in_box(draw, (x1 + 4, top, x2 - 4, bottom), "TBD", self._font(8, False), COLORS["muted"])
                continue
            self._draw_team_logo(image, draw, team.get("logo"), x1 + 3, top + (WORLDS_BRACKET_ROW - WORLDS_BRACKET_LOGO) // 2,
                                 WORLDS_BRACKET_LOGO, code)
            lost = winner is not None and winner != index
            score_w = 0
            if state in {"live", "final"} and team.get("wins") is not None:
                score = str(team.get("wins"))
                score_font = self._font(10, True)
                score_w = self._text_width(draw, score, score_font) + 2
                ink = COLORS["text"] if winner is None else _kit_result_ink("loss" if lost else "win")
                self._draw_text_in_box(draw, (x2 - 3 - score_w, top, x2 - 3, bottom), score, score_font, ink, align="right")
            name_right = x2 - 4 - max(score_w, day_w)
            name, font = self._fit_text(draw, code, max(1, name_right - (x1 + 17)), 9, bold=not lost, min_size=7)
            self._draw_text_in_box(draw, (x1 + 17, top, name_right, bottom), name, font, COLORS["text"])

    def _draw_worlds_title_odds(self, image, draw, box, odds, accent):
        """'夺冠' and Polymarket's mark, then the favourites: logo, title chance and a bar for it."""
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.line((x1, y1, x2, y1), fill=COLORS["line"], width=1)
        caption_font = self._font(9, True)
        self._draw_text_in_box(draw, (x1, y1 + 2, x2, y1 + 13), "夺冠概率", caption_font, COLORS["text"])
        logo = self._load_local_logo(os.path.join(LOCAL_TEAM_LOGO_DIR, "polymarket.png"), (80, 9), alpha_threshold=8)
        if logo is not None:
            tinted = Image.new("RGBA", logo.size, (*COLORS["text"][:3], 255))
            tinted.putalpha(logo.getchannel("A"))
            image.paste(tinted, (x2 - tinted.width, y1 + 3 + (10 - tinted.height) // 2), tinted)
        count = len(odds)
        width = (x2 - x1) / max(1, count)
        top = y1 + 16
        for index, team in enumerate(odds):
            left = round(x1 + index * width)
            right = round(x1 + (index + 1) * width) - 4
            self._draw_team_logo(image, draw, team.get("logo"), left, top, WORLDS_TITLE_LOGO, team.get("code"))
            share = max(0.0, min(1.0, float(team.get("share") or 0)))
            label = f"{share * 100:.0f}%" if share >= 0.01 else "<1%"
            self._draw_text_in_box(draw, (left + WORLDS_TITLE_LOGO + 3, top, right, top + WORLDS_TITLE_LOGO), label,
                                   self._font(11, index == 0), COLORS["text"])
            bar_top = top + WORLDS_TITLE_LOGO + 3
            draw.rectangle((left, bar_top, right, bar_top + 3), fill=COLORS["line"])
            draw.rectangle((left, bar_top, left + max(1, round((right - left) * share)), bar_top + 3), fill=accent)

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
