"""NFL and college football panels: broadcast scorebug plus the week's scoreboard.

The featured game (live first, then the next kickoff, then the latest final)
fills the left card: quarter scores, timeouts, possession, a drive tracker
with the line to gain and live win odds. Every other game is listed on the
right with its own clock; college games put ranked matchups first.

Yard values follow ESPN's frame (yards from the home goal line). On screen the
away end zone is on the left and the home end zone on the right.
"""

from .common import *
from .scorebug_kit import KIT_SCOREBOARD_ROW_HEIGHT, ScorebugSpec

SportsDashboard = None

FOOTBALL_TIMEOUTS_PER_HALF = 3
NCAA_LIST_WIDTH = 214
FOOTBALL_SPEC_KEYS = {
    "NFL": ("nfl_accent", "nfl_live", "nfl_tag", "nfl_field_tint"),
    "NCAA": ("ncaa_accent", "ncaa_live", "ncaa_tag", "ncaa_field_tint"),
}


class FootballRenderMixin:
    def _draw_nfl_standalone_panel(self, image, draw, bounds, card, source_state, now):
        self._draw_football_scorebug_panel(image, draw, bounds, card, source_state, now, "NFL")

    def _draw_ncaa_standalone_panel(self, image, draw, bounds, card, source_state, now):
        """College layout: the ranked slate on the left, the featured ranked
        matchup (rank badges, school names, drive) on the right."""
        spec = self._football_spec("NCAA")
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS[spec.accent], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "NCAA", card, source_state)
        top, bottom = y1 + 57, y2 - 7
        list_x2 = x1 + 12 + NCAA_LIST_WIDTH
        self._draw_vertical_split(draw, list_x2 + 9, top - 5, bottom)
        games = SportsDashboard._football_scoreboard_events(card, "NCAA")
        shown = games[: max(0, (bottom - top - 21) // KIT_SCOREBOARD_ROW_HEIGHT)]
        title = "AP TOP 25" if any(SportsDashboard._ncaa_game_is_ranked(event) for event in shown) else "SCOREBOARD"
        self._draw_kit_scoreboard_column(image, draw, (x1 + 12, top - 2, list_x2, bottom), games, now, spec, title=title, empty="No other college games")
        self._draw_ncaa_matchup_card(image, draw, (list_x2 + 18, top, x2 - 12, bottom), card, now, spec)

    def _draw_ncaa_matchup_card(self, image, draw, box, card, now, spec):
        event = SportsDashboard._football_featured_event(card, "NCAA")
        if not event:
            self._draw_kit_empty_card(draw, box, spec, "No college schedule")
            return
        state = SportsDashboard._hub_event_state(event)
        left, top, right, bottom = self._draw_kit_card(draw, box, spec, state)
        self._draw_kit_status_row(
            draw, left, top + 8, right, spec, state,
            SportsDashboard._nfl_status_pill_label(event, now),
            SportsDashboard._nfl_status_context_label(event, state),
            SportsDashboard._ncaa_header_badge_label(event) or self._football_header_week_label(card, event),
        )
        y = top + 32
        for index, side in enumerate(("a", "b")):
            if index:
                draw.line((left, y - 2, right, y - 2), fill=COLORS["line"], width=1)
            self._draw_ncaa_rank_row(image, draw, (left, y, right, y + 32), event, side, state, spec)
            y += 34
        details = (left, y + 4, right, bottom - 7)
        if state == "live":
            self._draw_nfl_live_details(draw, details, event, "NCAA")
        elif state == "final":
            self._draw_nfl_final_details(image, draw, details, event, "NCAA")
        else:
            self._draw_nfl_pregame_details(image, draw, details, event, now, "NCAA")

    def _draw_ncaa_rank_row(self, image, draw, box, event, side, state, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        prefix = f"team_{side}"
        winner = SportsDashboard._football_winner_side(event) if state == "final" else ""
        dimmed = bool(winner) and winner != side
        ink_key = self._kit_team_ink_key(event.get(f"{prefix}_color"), spec.accent)
        rank = SportsDashboard._lpl_int_value(event.get(f"{prefix}_rank"))
        badge = (x1, y1 + 5, x1 + 22, y1 + 27)
        if rank:
            draw.ellipse(badge, fill=COLORS[ink_key], outline=COLORS["border"], width=1)
            self._draw_centered_in_box(draw, badge, str(rank), self._font(11 if rank < 10 else 10, True), COLORS[self._kit_ink_text_key(ink_key)])
        else:
            draw.ellipse(badge, fill=COLORS["panel"], outline=COLORS["border"], width=1)
            self._draw_centered_in_box(draw, badge, "NR", self._font(7, False), COLORS["text"])
        self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), x1 + 28, y1 + 4, 24, SportsDashboard._event_team_logo_fallback(event, side, "NCAA"))
        name_x = x1 + 58
        has_ball = state == "live" and SportsDashboard._nfl_possession_side(event) == side
        score_w = 46
        name_budget = x2 - score_w - 8 - name_x - (15 if has_ball else 0)
        name = SportsDashboard._ncaa_school_label(event, side, include_rank=False, full=True)
        name, name_font = self._fit_text(draw, name, name_budget, 15, bold=not dimmed, min_size=10)
        self._draw_text_in_box(draw, (name_x, y1, name_x + name_budget, y1 + 19), name, name_font, COLORS["text"])
        if has_ball:
            self._draw_nfl_football_icon(draw, name_x + self._text_width(draw, name, name_font) + 5, y1 + 6)
        sub_x = name_x
        if state == "live":
            timeouts = SportsDashboard._lpl_int_value(event.get(f"timeouts_{side}"))
            if timeouts is not None:
                sub_x = self._draw_kit_pips(draw, sub_x, y1 + 23, timeouts, FOOTBALL_TIMEOUTS_PER_HALF) + 7
        record = str(event.get(f"record_{side}") or "").strip()
        if record:
            record, record_font = self._fit_text(draw, record, x2 - score_w - 8 - sub_x, 9, bold=False, min_size=7)
            self._draw_text_in_box(draw, (sub_x, y1 + 20, x2 - score_w - 8, y1 + 30), record, record_font, COLORS["text"])
        score_right = x2 - 9 if winner else x2
        score_box = (score_right - score_w, y1, score_right, y2)
        if state == "scheduled":
            line = SportsDashboard._kit_spread_for_side(event, side)
            if line:
                self._draw_text_in_box(draw, score_box, line, self._font(13, True), COLORS["text"], align="right")
            return
        score = event.get(f"wins_{side}")
        text, font = self._fit_text(draw, "-" if score is None else str(score), score_w, 24, bold=not dimmed, min_size=14)
        self._draw_text_in_box(draw, score_box, text, font, COLORS["text"], align="right")
        if winner == side:
            middle = (y1 + y2) // 2
            draw.polygon([(x2, middle - 5), (x2, middle + 5), (x2 - 6, middle)], fill=COLORS["text"])

    def _draw_football_scorebug_panel(self, image, draw, bounds, card, source_state, now, sport):
        spec = self._football_spec(sport)
        card_box, column_box = self._draw_kit_panel_frame(image, draw, bounds, spec, card, source_state)
        self._draw_football_featured_card(image, draw, card_box, card, now, sport)
        games = SportsDashboard._football_scoreboard_events(card, sport)
        self._draw_kit_scoreboard_column(image, draw, column_box, games, now, spec, empty="No other NFL games")

    def _football_spec(self, sport):
        sport = "NCAA" if str(sport or "").upper() == "NCAA" else "NFL"
        accent, live, tag, tint = FOOTBALL_SPEC_KEYS[sport]
        football = SportsDashboard

        def team_name(event, side, full):
            if sport == "NCAA":
                return football._ncaa_school_label(event, side, include_rank=True, full=full)
            return football._football_display_team(event, side, sport, full=full)

        def row_widget(kit, draw, x, y, event, side):
            timeouts = football._lpl_int_value(event.get(f"timeouts_{side}"))
            if timeouts is None:
                return x
            return kit._draw_kit_pips(draw, x, y, timeouts, FOOTBALL_TIMEOUTS_PER_HALF) + 7

        return ScorebugSpec(
            sport,
            accent,
            live,
            tag,
            tint,
            team_name=team_name,
            logo_fallback=lambda event, side: football._event_team_logo_fallback(event, side, sport),
            winner_side=football._football_winner_side,
            possession_side=football._nfl_possession_side,
            possession_icon=lambda kit, draw, x, y: kit._draw_nfl_football_icon(draw, x, y),
            period_count=lambda event: football._kit_period_count(event.get("linescore_a"), event.get("linescore_b")),
            period_label=football._kit_quarter_label,
            current_period=lambda event: football._lpl_int_value(event.get("period")),
            linescore=lambda event, side: event.get(f"linescore_{side}"),
            row_widget=row_widget,
            pregame_value=football._kit_spread_for_side,
            state_labels=football._nfl_scoreboard_state_labels,
            row_flag=lambda event: bool(event.get("red_zone")),
        )

    # Featured card -----------------------------------------------------------

    def _draw_football_featured_card(self, image, draw, bounds, card, now, sport):
        spec = self._football_spec(sport)
        event = SportsDashboard._football_featured_event(card, sport)
        if not event:
            self._draw_kit_empty_card(draw, bounds, spec, "No college schedule" if sport == "NCAA" else "No NFL schedule")
            return
        state = self._hub_event_state(event)
        left, top, right, bottom = self._draw_kit_card(draw, bounds, spec, state)
        right_label = self._football_header_week_label(card, event)
        if sport == "NCAA":
            right_label = SportsDashboard._ncaa_header_badge_label(event) or right_label
        self._draw_kit_status_row(
            draw, left, top + 8, right, spec, state,
            SportsDashboard._nfl_status_pill_label(event, now),
            SportsDashboard._nfl_status_context_label(event, state),
            right_label,
        )
        rows_bottom = self._draw_kit_scorebug(image, draw, left, top + 31, right, event, state, spec)
        details = (left, rows_bottom + 6, right, bottom - 7)
        if state == "live":
            self._draw_nfl_live_details(draw, details, event, sport)
        elif state == "final":
            self._draw_nfl_final_details(image, draw, details, event, sport)
        else:
            self._draw_nfl_pregame_details(image, draw, details, event, now, sport)

    def _draw_nfl_football_icon(self, draw, x, y, width=11, height=7):
        x, y = int(x), int(y)
        draw.ellipse((x, y, x + width, y + height), fill=COLORS["amber"], outline=COLORS["border"], width=1)
        middle = y + height // 2
        draw.line((x + width // 2 - 2, middle, x + width // 2 + 2, middle), fill=COLORS["border"], width=1)

    # Live details ------------------------------------------------------------

    def _draw_nfl_live_details(self, draw, box, event, sport="NFL"):
        x1, y1, x2, y2 = [int(value) for value in box]
        self._draw_nfl_drive_tracker(draw, (x1, y1, x2, y1 + 22), event, sport)
        y = y1 + 27
        if self._draw_nfl_situation_row(draw, (x1, y, x2, y + 16), event, sport):
            y += 20
        last_play = str(event.get("last_play") or "").strip()
        if last_play and y + 13 <= y2:
            self._draw_kit_labeled_line(draw, (x1, y, x2, y + 13), "LAST", last_play)
            y += 19
        if y + 12 <= y2:
            self._draw_nfl_win_probability(draw, (x1, y, x2, y + 12), event, sport)

    def _draw_nfl_drive_tracker(self, draw, box, event, sport="NFL"):
        x1, y1, x2, y2 = [int(value) for value in box]
        spec = self._football_spec(sport)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS[spec.tint], outline=COLORS["border"], width=1)
        zone_w = max(14, int((x2 - x1) * 0.08))
        field_x1 = x1 + zone_w
        field_x2 = x2 - zone_w
        code_font = self._font(8, True)
        for side, (zone_x1, zone_x2) in (("a", (x1 + 1, field_x1)), ("b", (field_x2, x2 - 1))):
            ink_key = self._kit_team_ink_key(event.get(f"team_{side}_color"), spec.accent)
            draw.rectangle((zone_x1, y1 + 1, zone_x2, y2 - 1), fill=COLORS[ink_key])
            code = str(event.get(f"team_{side}_code") or "")[:4]
            code, fitted_font = self._fit_text(draw, code, zone_x2 - zone_x1 - 2, 8, bold=True, min_size=6)
            self._draw_centered_in_box(draw, (zone_x1, y1, zone_x2, y2), code, fitted_font or code_font, COLORS[self._kit_ink_text_key(ink_key)])

        def field_x(yards_from_away_goal):
            return int(round(field_x1 + (field_x2 - field_x1) * max(0, min(100, yards_from_away_goal)) / 100))

        for yard in range(10, 100, 10):
            x = field_x(yard)
            if yard == 50:
                draw.line((x, y1 + 2, x, y2 - 2), fill=COLORS["border"], width=1)
            else:
                draw.line((x, y1 + 2, x, y1 + 5), fill=COLORS["border"], width=1)
                draw.line((x, y2 - 5, x, y2 - 2), fill=COLORS["border"], width=1)
        ball_yard = SportsDashboard._nfl_ball_yard(event)
        offense = SportsDashboard._nfl_possession_side(event)
        if ball_yard is None or not offense:
            return
        ball = 100 - ball_yard
        middle = (y1 + y2) // 2
        start_yard = event.get("drive_start_yard")
        if start_yard is not None:
            start_x = field_x(100 - start_yard)
            ball_x = field_x(ball)
            draw.rectangle((min(start_x, ball_x), middle - 2, max(start_x, ball_x), middle + 2), fill=COLORS["amber"], outline=COLORS["border"], width=1)
            draw.line((start_x, y1 + 4, start_x, y2 - 4), fill=COLORS["border"], width=1)
        target = SportsDashboard._nfl_line_to_gain(event, ball, offense)
        if target is not None:
            target_x = field_x(target)
            draw.rectangle((target_x - 2, y1 + 1, target_x + 2, y2 - 1), fill=COLORS["amber"], outline=COLORS["border"], width=1)
        self._draw_nfl_football_icon(draw, field_x(ball) - 6, middle - 4, 12, 8)

    def _draw_nfl_situation_row(self, draw, box, event, sport="NFL"):
        x1, y1, x2, y2 = [int(value) for value in box]
        spec = self._football_spec(sport)
        down = SportsDashboard._nfl_short_down_label(event)
        spot = str(event.get("yard_line") or "").strip()
        drive = SportsDashboard._nfl_drive_summary_label(event.get("drive_summary"))
        red_zone = bool(event.get("red_zone"))
        if not (down or spot or drive):
            return False
        cursor = x1
        if down:
            down, down_font = self._fit_text(draw, down, 100, 12, bold=True, min_size=9)
            chip_w = self._text_width(draw, down, down_font) + 12
            chip_fill = COLORS[spec.live] if red_zone else COLORS[spec.tag]
            draw.rounded_rectangle((x1, y1, x1 + chip_w, y2), radius=3, fill=chip_fill, outline=COLORS["border"], width=1)
            self._draw_centered_in_box(draw, (x1, y1, x1 + chip_w, y2), down, down_font, COLORS["paper_text"] if red_zone else COLORS["text"])
            cursor = x1 + chip_w + 6
        if spot:
            spot, spot_font = self._fit_text(draw, spot, 76, 12, bold=True, min_size=9)
            self._draw_text_in_box(draw, (cursor, y1, cursor + 76, y2), spot, spot_font, COLORS[spec.live] if red_zone else COLORS["text"])
            cursor += self._text_width(draw, spot, spot_font) + 8
        if red_zone:
            drive = " · ".join(part for part in ("RED ZONE", drive) if part)
        if drive and x2 - cursor > 24:
            drive, drive_font = self._fit_text_ellipsis(draw, drive, x2 - cursor, 9, bold=red_zone, min_size=8)
            self._draw_text_in_box(draw, (cursor, y1, x2, y2), drive, drive_font, COLORS[spec.live] if red_zone else COLORS["text"], align="right")
        return True

    def _draw_nfl_win_probability(self, draw, box, event, sport="NFL"):
        away = event.get("win_pct_a")
        home = event.get("win_pct_b")
        if away is None or home is None or away + home <= 0:
            return False
        spec = self._football_spec(sport)
        self._draw_kit_share_bar(
            draw,
            box,
            f"{event.get('team_a_code') or 'AWAY'} {round(away * 100)}%",
            f"{round(home * 100)}% {event.get('team_b_code') or 'HOME'}",
            away / (away + home),
            self._kit_team_ink_key(event.get("team_a_color"), spec.accent),
            self._kit_team_ink_key(event.get("team_b_color"), spec.accent),
        )
        return True

    # Final / pregame details -------------------------------------------------

    def _draw_nfl_final_details(self, image, draw, box, event, sport="NFL"):
        x1, y1, x2, y2 = [int(value) for value in box]
        spec = self._football_spec(sport)
        leaders = list(event.get("leaders") or [])
        if leaders:
            self._draw_kit_leader_rows(image, draw, x1, y1, x2, y2 - 14, event, leaders, spec)
        else:
            winner = SportsDashboard._football_final_winner_label(event, sport)
            if winner:
                winner, winner_font = self._fit_text(draw, winner, x2 - x1, 14, bold=True, min_size=10)
                self._draw_text_in_box(draw, (x1, y1, x2, y1 + 20), winner, winner_font, COLORS["text"])
        self._draw_kit_footer(draw, (x1, y2 - 12, x2, y2), SportsDashboard._football_footer_label(event, sport))

    def _draw_nfl_pregame_details(self, image, draw, box, event, now, sport="NFL"):
        x1, y1, x2, y2 = [int(value) for value in box]
        spec = self._football_spec(sport)
        tiles = [
            ("KICKOFF", SportsDashboard._kit_countdown_label(event.get("start"), now)),
            ("TV", str(event.get("broadcast") or "").strip() or "TBD"),
            ("O/U", str(event.get("over_under") or "").replace("O/U", "").strip() or "--"),
        ]
        tiles_bottom = self._draw_kit_tiles(draw, (x1, y1, x2, y2), tiles, spec)
        # Before kickoff ESPN's leaders are season totals for the two teams.
        self._draw_kit_leader_rows(image, draw, x1, tiles_bottom + 6, x2, y2 - 14, event, event.get("leaders"), spec, max_rows=2)
        self._draw_kit_footer(draw, (x1, y2 - 12, x2, y2), SportsDashboard._football_footer_label(event, sport, include_tv=False))

    # Labels and geometry -----------------------------------------------------

    @staticmethod
    def _football_featured_event(card, sport="NFL"):
        """The card's main game. College features a ranked matchup: live, then
        one kicking off within three days, then one decided in the last two."""
        card = card or {}
        main = card.get("main") or {}
        if str(sport or "").upper() != "NCAA" or not main:
            return main
        ranked = SportsDashboard._ncaa_game_is_ranked
        live = [event for event in card.get("live") or [] if ranked(event)]
        if live:
            return live[0]
        if SportsDashboard._hub_event_state(main) == "live" or ranked(main):
            return main
        anchor = main.get("start") if isinstance(main.get("start"), datetime) else None
        if anchor is None:
            return main
        soon = [
            event for event in card.get("upcoming") or []
            if ranked(event) and isinstance(event.get("start"), datetime) and event["start"] - anchor <= timedelta(days=3)
        ]
        if soon:
            return soon[0]
        recent = [
            event for event in card.get("recent") or []
            if ranked(event) and isinstance(event.get("start"), datetime) and anchor - event["start"] <= timedelta(days=4)
        ]
        return recent[0] if recent else main

    @staticmethod
    def _football_scoreboard_events(card, sport="NFL"):
        card = card or {}
        main = SportsDashboard._football_featured_event(card, sport)
        games = SportsDashboard._kit_scoreboard_order(card.get("events"), main)
        if str(sport or "").upper() != "NCAA":
            return games
        # College slates run to dozens of games: live first, then ranked
        # matchups (upcoming, then results), then everything else.
        def rank_key(event):
            state = SportsDashboard._hub_event_state(event)
            ranked = SportsDashboard._ncaa_game_is_ranked(event)
            if state == "live":
                return (0, 0 if ranked else 1)
            if ranked:
                return (1, 0 if state == "scheduled" else 1)
            return (2, 0 if state == "scheduled" else 1)

        return sorted(games, key=rank_key)

    @staticmethod
    def _ncaa_game_is_ranked(event):
        return bool((event or {}).get("team_a_rank") or (event or {}).get("team_b_rank"))

    @staticmethod
    def _nfl_scoreboard_state_labels(event, now):
        """(top, bottom, colour key) for the scoreboard clock column."""

        def live_labels(live_event):
            clock = SportsDashboard._nfl_live_clock_label(live_event)
            if clock == "HALFTIME":
                return "HALF", ""
            head, _separator, tail = clock.partition(" ")
            return head, tail

        def final_bottom(final_event):
            return "OT" if "OT" in str(final_event.get("status_detail") or "").upper() else ""

        live_key = "ncaa_live" if str((event or {}).get("sport") or "").upper() == "NCAA" else "nfl_live"
        return SportsDashboard._kit_schedule_state_labels(event, now, live_labels, live_key, final_bottom)

    @staticmethod
    def _nfl_status_pill_label(event, now):
        event = event or {}
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            return SportsDashboard._nfl_live_clock_label(event)
        if state == "final":
            return "FINAL/OT" if "OT" in str(event.get("status_detail") or "").upper() else "FINAL"
        return SportsDashboard._kit_start_label(event.get("start"), now)

    @staticmethod
    def _nfl_status_context_label(event, state):
        event = event or {}
        if state == "final":
            start = event.get("start")
            return start.strftime("%m/%d") if isinstance(start, datetime) else ""
        # Before kickoff the TV channel has its own tile.
        return str(event.get("broadcast") or "").strip() if state == "live" else ""

    @staticmethod
    def _nfl_live_clock_label(event):
        return SportsDashboard._kit_clock_label(event, SportsDashboard._kit_quarter_label)

    @staticmethod
    def _nfl_possession_side(event):
        for side in ("a", "b"):
            if SportsDashboard._football_possession_side_fill_key(event, side) == "amber":
                return side
        return ""

    @staticmethod
    def _nfl_ball_yard(event):
        """Ball spot in yards from the home goal line; falls back to the
        "ATL 19" spot text when ESPN leaves yardLine out."""
        event = event or {}
        if event.get("ball_yard") is not None:
            return event.get("ball_yard")
        match = re.fullmatch(r"\s*([A-Za-z]{2,4})?\s*(\d{1,2})\s*", str(event.get("yard_line") or ""))
        if not match:
            return None
        yard = int(match.group(2))
        code = str(match.group(1) or "").upper()
        if yard == 50:
            return 50
        if code and code == str(event.get("team_b_code") or "").upper():
            return yard
        if code and code == str(event.get("team_a_code") or "").upper():
            return 100 - yard
        return None

    @staticmethod
    def _nfl_short_down_label(event):
        """"3RD & 17" without the spot, which has its own label."""
        event = event or {}
        short = str(event.get("down_distance_short") or "").strip().upper()
        if short:
            return short
        return str(event.get("down_distance") or "").strip().upper().split(" AT ")[0].strip()

    @staticmethod
    def _nfl_line_to_gain(event, ball, offense):
        """Yards from the away goal line to the first-down marker, or None."""
        distance = SportsDashboard._lpl_int_value((event or {}).get("distance"))
        down = SportsDashboard._nfl_short_down_label(event)
        if not distance or distance <= 0 or "GOAL" in down:
            return None
        target = ball + distance if offense == "a" else ball - distance
        return target if 0 < target < 100 else None

    @staticmethod
    def _nfl_drive_summary_label(summary):
        parts = [part.strip() for part in str(summary or "").split(",") if part.strip()]
        labels = []
        for part in parts:
            part = re.sub(r"\byards\b", "YDS", part, flags=re.IGNORECASE)
            part = re.sub(r"\byard\b", "YD", part, flags=re.IGNORECASE)
            labels.append(part.upper())
        return " · ".join(labels)

    @staticmethod
    def _football_footer_label(event, sport, include_tv=True):
        event = event or {}
        parts = []
        if str(sport or "").upper() == "NCAA" and event.get("neutral_site"):
            parts.append("NEUTRAL SITE")
        venue = SportsDashboard._kit_venue_label(event, include_tv=include_tv)
        if venue:
            parts.append(venue)
        headline = str(event.get("headline") or "").strip()
        if headline and not include_tv:
            parts.append(headline)
        return "  ·  ".join(parts)
