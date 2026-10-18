"""NFL standalone panel: a broadcast-style scorebug plus the week's scoreboard.

The featured game (live first, then the next kickoff, then the latest final)
fills the left card as a scorebug: quarter scores, timeouts, possession, a
drive tracker with the line to gain, and live win odds. Every other game of
the week is listed on the right with its own clock, so a busy Sunday shows all
live scores at once instead of only the next two kickoffs.

Yard values follow ESPN's frame (yards from the home goal line). On screen the
away end zone is on the left and the home end zone on the right.
"""

from .common import *

SportsDashboard = None

NFL_FEATURED_CARD_MAX_WIDTH = 318
NFL_SCOREBUG_ROW_HEIGHT = 33
NFL_SCOREBUG_SCORE_WIDTH = 44
NFL_SCOREBUG_PERIOD_WIDTH = 17
NFL_SCOREBOARD_ROW_HEIGHT = 26
NFL_SCOREBOARD_STATE_WIDTH = 44
NFL_TIMEOUTS_PER_HALF = 3
# Pure panel inks a team colour may map to; white is left out so every team
# stripe stays visible on paper.
NFL_TEAM_INK_KEYS = ("red", "blue", "green", "amber", "text")


class NflRenderMixin:
    def _draw_nfl_standalone_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["nfl_accent"], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "NFL", card, source_state)
        content_y = y1 + 57
        bottom = y2 - 7
        card_w = max(240, min(NFL_FEATURED_CARD_MAX_WIDTH, int((x2 - x1 + 1) * 0.575)))
        split_x = x1 + 12 + card_w + 8
        self._draw_vertical_split(draw, split_x, content_y - 5, bottom)
        self._draw_nfl_featured_card(image, draw, (x1 + 12, content_y, split_x - 9, bottom), card, now)
        self._draw_nfl_scoreboard_column(image, draw, (split_x + 6, content_y - 2, x2 - 12, bottom), card, now)

    # Featured card -----------------------------------------------------------

    def _draw_nfl_featured_card(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        state = self._hub_event_state(event) if event else "scheduled"
        accent = COLORS["nfl_live"] if state == "live" else COLORS["nfl_accent"]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        if not event:
            text, font = self._fit_text(draw, "No NFL schedule", x2 - x1 - 40, 14, bold=True, min_size=10)
            self._draw_centered_in_box(draw, (x1 + 12, y1, x2, y2), text, font, COLORS["muted"])
            return
        left = x1 + 16
        right = x2 - 10
        self._draw_nfl_card_status_row(draw, left, y1 + 8, right, card, event, now, state)
        rows_bottom = self._draw_nfl_scorebug(image, draw, left, y1 + 31, right, event, state)
        details = (left, rows_bottom + 6, right, y2 - 7)
        if state == "live":
            self._draw_nfl_live_details(draw, details, event)
        elif state == "final":
            self._draw_nfl_final_details(image, draw, details, event)
        else:
            self._draw_nfl_pregame_details(image, draw, details, event, now)

    def _draw_nfl_card_status_row(self, draw, x1, y, x2, card, event, now, state):
        # Live keeps the dashboard's LIVE-pill idiom (light pill, red block,
        # black text): solid red behind small text dithers into specks.
        marker = 9 if state == "live" else 0
        if state == "final":
            fill, text_fill = COLORS["text"], COLORS["paper_text"]
        else:
            fill, text_fill = (COLORS["panel"] if state == "live" else COLORS["nfl_tag"]), COLORS["text"]
        pill, pill_font = self._fit_text(draw, self._nfl_status_pill_label(event, now), 132, 12, bold=True, min_size=9)
        pill_w = self._text_width(draw, pill, pill_font) + 16 + marker
        draw.rounded_rectangle((x1, y, x1 + pill_w, y + 18), radius=4, fill=fill, outline=COLORS["border"], width=1)
        if marker:
            draw.rectangle((x1 + 5, y + 4, x1 + 10, y + 14), fill=COLORS["nfl_live"], outline=COLORS["border"], width=1)
        self._draw_centered_in_box(draw, (x1 + marker, y, x1 + pill_w, y + 18), pill, pill_font, text_fill)
        week_w = 0
        week = self._football_header_week_label(card, event)
        if week:
            week, week_font = self._fit_text(draw, week, 92, 11, bold=False, min_size=8)
            week_w = self._text_width(draw, week, week_font)
            self._draw_text_in_box(draw, (x2 - week_w, y, x2, y + 18), week, week_font, COLORS["text"], align="right")
        context = self._nfl_status_context_label(event, state)
        budget = x2 - week_w - 10 - (x1 + pill_w + 8)
        if context and budget > 16:
            context, context_font = self._fit_text_ellipsis(draw, context, budget, 11, bold=True, min_size=8)
            self._draw_text_in_box(draw, (x1 + pill_w + 8, y, x1 + pill_w + 8 + budget, y + 18), context, context_font, COLORS["text"])

    def _draw_nfl_scorebug(self, image, draw, x1, y, x2, event, state):
        periods = self._nfl_scorebug_period_count(event) if state != "scheduled" else 0
        lines_x1 = x2 - NFL_SCOREBUG_SCORE_WIDTH - 4 - periods * NFL_SCOREBUG_PERIOD_WIDTH
        top = y
        if periods:
            current = self._lpl_int_value(event.get("period")) if state == "live" else None
            for index in range(periods):
                left = lines_x1 + index * NFL_SCOREBUG_PERIOD_WIDTH
                font = self._font(8, current == index + 1)
                self._draw_centered_in_box(draw, (left, y, left + NFL_SCOREBUG_PERIOD_WIDTH, y + 9), self._nfl_period_label(index + 1), font, COLORS["text"])
            top = y + 10
        for index, side in enumerate(("a", "b")):
            row_y = top + index * NFL_SCOREBUG_ROW_HEIGHT
            if index:
                draw.line((x1, row_y - 1, x2, row_y - 1), fill=COLORS["line"], width=1)
            self._draw_nfl_scorebug_row(image, draw, (x1, row_y, x2, row_y + NFL_SCOREBUG_ROW_HEIGHT - 2), event, side, state, lines_x1, periods)
        return top + 2 * NFL_SCOREBUG_ROW_HEIGHT

    def _draw_nfl_scorebug_row(self, image, draw, box, event, side, state, lines_x1, periods):
        x1, y1, x2, y2 = box
        prefix = "team_a" if side == "a" else "team_b"
        winner = SportsDashboard._football_winner_side(event) if state == "final" else ""
        dimmed = bool(winner) and winner != side
        draw.rectangle((x1, y1 + 3, x1 + 3, y2 - 3), fill=self._nfl_team_ink(event, side))
        logo_size = 24
        self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), x1 + 8, y1 + (y2 - y1 - logo_size) // 2, logo_size, SportsDashboard._event_team_logo_fallback(event, side, "NFL"))
        name_x = x1 + 38
        has_ball = state == "live" and SportsDashboard._nfl_possession_side(event) == side
        name_budget = lines_x1 - 8 - name_x - (15 if has_ball else 0)
        name = SportsDashboard._football_display_team(event, side, "NFL", full=True)
        name, name_font = self._fit_text(draw, name, name_budget, 16, bold=not dimmed, min_size=11)
        self._draw_text_in_box(draw, (name_x, y1, name_x + name_budget, y1 + 19), name, name_font, COLORS["text"])
        if has_ball:
            self._draw_nfl_football_icon(draw, name_x + self._text_width(draw, name, name_font) + 5, y1 + 6)
        sub_x = name_x
        timeouts = self._lpl_int_value(event.get(f"timeouts_{side}")) if state == "live" else None
        if timeouts is not None:
            sub_x = self._draw_nfl_timeouts(draw, sub_x, y1 + 23, timeouts) + 7
        record = str(event.get(f"record_{side}") or "").strip()
        if record and lines_x1 - 8 - sub_x > 16:
            record, record_font = self._fit_text(draw, record, lines_x1 - 8 - sub_x, 9, bold=False, min_size=7)
            self._draw_text_in_box(draw, (sub_x, y1 + 20, lines_x1 - 8, y1 + 30), record, record_font, COLORS["text"])
        scores = list(event.get(f"linescore_{side}") or [])
        current = self._lpl_int_value(event.get("period")) if state == "live" else None
        for index in range(periods):
            value = scores[index] if index < len(scores) else None
            if value is None:
                continue
            left = lines_x1 + index * NFL_SCOREBUG_PERIOD_WIDTH
            period_font = self._font(10, current == index + 1)
            self._draw_centered_in_box(draw, (left, y1, left + NFL_SCOREBUG_PERIOD_WIDTH, y2), str(value), period_font, COLORS["text"])
        score_right = x2 - 9 if winner else x2
        score_box = (score_right - NFL_SCOREBUG_SCORE_WIDTH + 6, y1, score_right, y2)
        if state == "scheduled":
            line = SportsDashboard._nfl_spread_for_side(event, side)
            if line:
                line, line_font = self._fit_text(draw, line, NFL_SCOREBUG_SCORE_WIDTH, 13, bold=True, min_size=9)
                self._draw_text_in_box(draw, score_box, line, line_font, COLORS["text"], align="right")
            return
        score = event.get(f"wins_{side}")
        score_text, score_font = self._fit_text(draw, "-" if score is None else str(score), score_box[2] - score_box[0], 26, bold=not dimmed, min_size=16)
        self._draw_text_in_box(draw, score_box, score_text, score_font, COLORS["text"], align="right")
        if winner == side:
            middle = (y1 + y2) // 2
            draw.polygon([(x2, middle - 5), (x2, middle + 5), (x2 - 6, middle)], fill=COLORS["text"])

    def _draw_nfl_timeouts(self, draw, x, y, remaining):
        remaining = max(0, min(NFL_TIMEOUTS_PER_HALF, int(remaining)))
        for index in range(NFL_TIMEOUTS_PER_HALF):
            left = x + index * 10
            if index < remaining:
                draw.rectangle((left, y, left + 7, y + 3), fill=COLORS["amber"], outline=COLORS["border"], width=1)
            else:
                draw.rectangle((left, y, left + 7, y + 3), outline=COLORS["border"], width=1)
        return x + NFL_TIMEOUTS_PER_HALF * 10 - 3

    def _draw_nfl_football_icon(self, draw, x, y, width=11, height=7):
        x, y = int(x), int(y)
        draw.ellipse((x, y, x + width, y + height), fill=COLORS["amber"], outline=COLORS["border"], width=1)
        middle = y + height // 2
        draw.line((x + width // 2 - 2, middle, x + width // 2 + 2, middle), fill=COLORS["border"], width=1)

    # Live details ------------------------------------------------------------

    def _draw_nfl_live_details(self, draw, box, event):
        x1, y1, x2, y2 = [int(value) for value in box]
        self._draw_nfl_drive_tracker(draw, (x1, y1, x2, y1 + 22), event)
        y = y1 + 27
        if self._draw_nfl_situation_row(draw, (x1, y, x2, y + 16), event):
            y += 20
        last_play = str(event.get("last_play") or "").strip()
        if last_play and y + 13 <= y2:
            label_font = self._font(8, False)
            self._draw_text_in_box(draw, (x1, y, x1 + 28, y + 13), "LAST", label_font, COLORS["text"])
            play, play_font = self._fit_text_ellipsis(draw, last_play, x2 - x1 - 30, 10, bold=True, min_size=9)
            self._draw_text_in_box(draw, (x1 + 30, y, x2, y + 13), play, play_font, COLORS["text"])
            y += 19
        if y + 12 <= y2:
            self._draw_nfl_win_probability(draw, (x1, y, x2, y + 12), event)

    def _draw_nfl_drive_tracker(self, draw, box, event):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["nfl_field_tint"], outline=COLORS["border"], width=1)
        zone_w = max(14, int((x2 - x1) * 0.08))
        field_x1 = x1 + zone_w
        field_x2 = x2 - zone_w
        code_font = self._font(8, True)
        for side, (zone_x1, zone_x2) in (("a", (x1 + 1, field_x1)), ("b", (field_x2, x2 - 1))):
            ink_key = self._nfl_team_ink_key(event, side)
            draw.rectangle((zone_x1, y1 + 1, zone_x2, y2 - 1), fill=COLORS[ink_key])
            code = str(event.get(f"team_{side}_code") or "")[:3]
            text_fill = COLORS["text"] if ink_key in {"amber", "nfl_accent"} else COLORS["panel"]
            self._draw_centered_in_box(draw, (zone_x1, y1, zone_x2, y2), code, code_font, text_fill)

        def field_x(yards_from_away_goal):
            return int(round(field_x1 + (field_x2 - field_x1) * max(0, min(100, yards_from_away_goal)) / 100))

        for yard in range(10, 100, 10):
            x = field_x(yard)
            if yard == 50:
                draw.line((x, y1 + 2, x, y2 - 2), fill=COLORS["border"], width=1)
            else:
                draw.line((x, y1 + 2, x, y1 + 5), fill=COLORS["border"], width=1)
                draw.line((x, y2 - 5, x, y2 - 2), fill=COLORS["border"], width=1)
        ball_yard = event.get("ball_yard")
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

    def _draw_nfl_situation_row(self, draw, box, event):
        x1, y1, x2, y2 = [int(value) for value in box]
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
            chip_fill = COLORS["nfl_live"] if red_zone else COLORS["nfl_tag"]
            draw.rounded_rectangle((x1, y1, x1 + chip_w, y2), radius=3, fill=chip_fill, outline=COLORS["border"], width=1)
            self._draw_centered_in_box(draw, (x1, y1, x1 + chip_w, y2), down, down_font, COLORS["paper_text"] if red_zone else COLORS["text"])
            cursor = x1 + chip_w + 6
        if spot:
            spot, spot_font = self._fit_text(draw, spot, 76, 12, bold=True, min_size=9)
            self._draw_text_in_box(draw, (cursor, y1, cursor + 76, y2), spot, spot_font, COLORS["nfl_live"] if red_zone else COLORS["text"])
            cursor += self._text_width(draw, spot, spot_font) + 8
        if red_zone:
            drive = " · ".join(part for part in ("RED ZONE", drive) if part)
        if drive and x2 - cursor > 24:
            drive, drive_font = self._fit_text_ellipsis(draw, drive, x2 - cursor, 9, bold=red_zone, min_size=8)
            self._draw_text_in_box(draw, (cursor, y1, x2, y2), drive, drive_font, COLORS["nfl_live"] if red_zone else COLORS["text"], align="right")
        return True

    def _draw_nfl_win_probability(self, draw, box, event):
        x1, y1, x2, y2 = [int(value) for value in box]
        away = event.get("win_pct_a")
        home = event.get("win_pct_b")
        if away is None or home is None or away + home <= 0:
            return False
        away_share = away / (away + home)
        away_font = self._font(9, away >= home)
        home_font = self._font(9, home > away)
        away_label = f"{event.get('team_a_code') or 'AWAY'} {round(away * 100)}%"
        home_label = f"{round(home * 100)}% {event.get('team_b_code') or 'HOME'}"
        self._draw_text_in_box(draw, (x1, y1, x1 + 28, y2), "WIN", self._font(8, False), COLORS["text"])
        away_w = self._text_width(draw, away_label, away_font)
        home_w = self._text_width(draw, home_label, home_font)
        self._draw_text_in_box(draw, (x1 + 28, y1, x1 + 28 + away_w, y2), away_label, away_font, COLORS["text"])
        self._draw_text_in_box(draw, (x2 - home_w, y1, x2, y2), home_label, home_font, COLORS["text"], align="right")
        bar_x1 = x1 + 28 + away_w + 6
        bar_x2 = x2 - home_w - 6
        if bar_x2 - bar_x1 < 20:
            return True
        middle = (y1 + y2) // 2
        split = int(round(bar_x1 + (bar_x2 - bar_x1) * away_share))
        away_ink = self._nfl_team_ink_key(event, "a")
        home_ink = self._nfl_team_ink_key(event, "b")
        draw.rectangle((bar_x1, middle - 3, bar_x2, middle + 3), fill=COLORS["panel"], outline=COLORS["border"], width=1)
        if split > bar_x1:
            draw.rectangle((bar_x1, middle - 3, split, middle + 3), fill=COLORS[away_ink], outline=COLORS["border"], width=1)
        if bar_x2 > split and home_ink != away_ink:
            draw.rectangle((split, middle - 3, bar_x2, middle + 3), fill=COLORS[home_ink], outline=COLORS["border"], width=1)
        return True

    # Final / pregame details -------------------------------------------------

    def _draw_nfl_final_details(self, image, draw, box, event):
        x1, y1, x2, y2 = [int(value) for value in box]
        leaders = list(event.get("leaders") or [])
        if leaders:
            self._draw_nfl_leader_rows(image, draw, x1, y1, x2, y2 - 14, event, leaders)
        else:
            winner = SportsDashboard._football_final_winner_label(event, "NFL")
            if winner:
                winner, winner_font = self._fit_text(draw, winner, x2 - x1, 14, bold=True, min_size=10)
                self._draw_text_in_box(draw, (x1, y1, x2, y1 + 20), winner, winner_font, COLORS["text"])
        self._draw_nfl_footer(draw, (x1, y2 - 12, x2, y2), SportsDashboard._nfl_venue_footer_label(event))

    def _draw_nfl_pregame_details(self, image, draw, box, event, now):
        x1, y1, x2, y2 = [int(value) for value in box]
        tiles = [
            ("KICKOFF", SportsDashboard._nfl_kickoff_countdown_label(event, now)),
            ("TV", str(event.get("broadcast") or "").strip() or "TBD"),
            ("O/U", str(event.get("over_under") or "").replace("O/U", "").strip() or "--"),
        ]
        gap = 6
        tile_w = (x2 - x1 - gap * (len(tiles) - 1)) // len(tiles)
        tile_h = 36
        label_font = self._font(8, False)
        for index, (label, value) in enumerate(tiles):
            left = x1 + index * (tile_w + gap)
            right = left + tile_w
            draw.rounded_rectangle((left, y1, right, y1 + tile_h), radius=4, fill=COLORS["nfl_field_tint"], outline=COLORS["border"], width=1)
            self._draw_text_in_box(draw, (left + 7, y1 + 3, right - 4, y1 + 13), label, label_font, COLORS["text"])
            value, value_font = self._fit_text_ellipsis(draw, value, tile_w - 12, 15, bold=True, min_size=9)
            self._draw_text_in_box(draw, (left + 7, y1 + 14, right - 4, y1 + tile_h - 3), value, value_font, COLORS["text"])
        # Before kickoff ESPN's leaders are season totals for the two teams.
        self._draw_nfl_leader_rows(image, draw, x1, y1 + tile_h + 6, x2, y2 - 14, event, list(event.get("leaders") or [])[:2])
        footer = " · ".join(
            part for part in (SportsDashboard._nfl_venue_footer_label(event, include_tv=False), str(event.get("headline") or "").strip()) if part
        )
        self._draw_nfl_footer(draw, (x1, y2 - 12, x2, y2), footer)

    def _draw_nfl_leader_rows(self, image, draw, x1, y, x2, y_limit, event, leaders):
        label_font = self._font(8, True)
        for leader in leaders[:3]:
            if y + 14 > y_limit:
                break
            draw.rounded_rectangle((x1, y + 1, x1 + 30, y + 13), radius=3, fill=COLORS["nfl_tag"], outline=COLORS["border"], width=1)
            self._draw_centered_in_box(draw, (x1, y + 1, x1 + 30, y + 13), leader.get("label") or "", label_font, COLORS["text"])
            side = leader.get("side")
            name_x = x1 + 36
            if side in {"a", "b"}:
                prefix = f"team_{side}"
                self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), name_x, y + 1, 12, SportsDashboard._event_team_logo_fallback(event, side, "NFL"))
                name_x += 16
            value, value_font = self._fit_text_ellipsis(draw, str(leader.get("value") or ""), (x2 - x1) // 2, 9, bold=False, min_size=8)
            value_w = self._text_width(draw, value, value_font)
            self._draw_text_in_box(draw, (x2 - value_w, y, x2, y + 14), value, value_font, COLORS["text"], align="right")
            name, name_font = self._fit_text_ellipsis(draw, leader.get("name") or "", x2 - value_w - 8 - name_x, 10, bold=True, min_size=8)
            self._draw_text_in_box(draw, (name_x, y, x2 - value_w - 8, y + 14), name, name_font, COLORS["text"])
            y += 17
        return y

    def _draw_nfl_footer(self, draw, box, text):
        if not text:
            return
        x1, y1, x2, y2 = box
        text, font = self._fit_text_ellipsis(draw, text, x2 - x1, 9, bold=False, min_size=8)
        self._draw_centered_in_box(draw, box, text, font, COLORS["text"])

    # Scoreboard column -------------------------------------------------------

    def _draw_nfl_scoreboard_column(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        games = SportsDashboard._nfl_scoreboard_events(card)
        live_count = sum(1 for event in games if SportsDashboard._hub_event_state(event) == "live")
        draw.rectangle((x1, y1 + 2, x1 + 8, y1 + 17), fill=COLORS["nfl_accent"], outline=COLORS["border"], width=1)
        counter = f"{live_count} LIVE" if live_count else ""
        counter_font = self._font(10, True)
        counter_w = self._text_width(draw, counter, counter_font) if counter else 0
        title, title_font = self._fit_text(draw, "SCOREBOARD", x2 - x1 - 22 - counter_w, 13, bold=True, min_size=9)
        draw.text((x1 + 13, y1 - 2), title, font=title_font, fill=COLORS["text"])
        if counter:
            self._draw_text_in_box(draw, (x2 - counter_w, y1 + 1, x2, y1 + 17), counter, counter_font, COLORS["nfl_live"], align="right")
        draw.line((x1, y1 + 19, x2, y1 + 19), fill=COLORS["border"], width=1)
        row_y = y1 + 23
        if not games:
            draw.text((x1 + 10, row_y + 6), "No other NFL games", font=self._font(10, True), fill=COLORS["muted"])
            return
        capacity = max(0, (y2 - row_y + 1) // NFL_SCOREBOARD_ROW_HEIGHT)
        for index, event in enumerate(games[:capacity]):
            top = row_y + index * NFL_SCOREBOARD_ROW_HEIGHT
            self._draw_nfl_scoreboard_row(image, draw, (x1, top, x2, top + NFL_SCOREBOARD_ROW_HEIGHT - 1), event, now, last=index == min(capacity, len(games)) - 1)

    def _draw_nfl_scoreboard_row(self, image, draw, box, event, now, last=False):
        x1, y1, x2, y2 = [int(value) for value in box]
        state = SportsDashboard._hub_event_state(event)
        top_label, bottom_label, top_key = SportsDashboard._nfl_scoreboard_state_labels(event, now)
        state_x2 = x1 + NFL_SCOREBOARD_STATE_WIDTH
        top_label, top_font = self._fit_text(draw, top_label, NFL_SCOREBOARD_STATE_WIDTH - 4, 10, bold=True, min_size=8)
        self._draw_text_in_box(draw, (x1 + 2, y1 + 1, state_x2, y1 + 12), top_label, top_font, COLORS[top_key])
        if bottom_label:
            bottom_label, bottom_font = self._fit_text(draw, bottom_label, NFL_SCOREBOARD_STATE_WIDTH - 4, 9, bold=False, min_size=7)
            self._draw_text_in_box(draw, (x1 + 2, y1 + 13, state_x2, y1 + 24), bottom_label, bottom_font, COLORS["text"])
        if state == "live" and event.get("red_zone"):
            draw.rectangle((state_x2 - 1, y1 + 3, state_x2 + 1, y2 - 3), fill=COLORS["nfl_live"])
        winner = SportsDashboard._football_winner_side(event) if state == "final" else ""
        offense = SportsDashboard._nfl_possession_side(event) if state == "live" else ""
        team_x = state_x2 + 6
        for index, side in enumerate(("a", "b")):
            line_y1 = y1 + 1 + index * 12
            line_y2 = line_y1 + 12
            prefix = f"team_{side}"
            dimmed = bool(winner) and winner != side
            self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), team_x, line_y1, 11, SportsDashboard._event_team_logo_fallback(event, side, "NFL"))
            score = event.get(f"wins_{side}") if state != "scheduled" else None
            score_text = "" if score is None else str(score)
            score_font = self._font(11, not dimmed)
            score_w = self._text_width(draw, score_text, score_font) if score_text else 0
            name_x = team_x + 15
            name_budget = x2 - 2 - score_w - 6 - name_x - (9 if offense == side else 0)
            name, font = self._fit_text_ellipsis(draw, SportsDashboard._football_display_team(event, side, "NFL"), name_budget, 11, bold=not dimmed, min_size=9)
            fill = COLORS["text"]
            self._draw_text_in_box(draw, (name_x, line_y1, name_x + name_budget, line_y2), name, font, fill)
            if offense == side:
                dot_x = name_x + self._text_width(draw, name, font) + 4
                draw.ellipse((dot_x, line_y1 + 4, dot_x + 5, line_y1 + 9), fill=COLORS["amber"], outline=COLORS["border"], width=1)
            if score_text:
                self._draw_text_in_box(draw, (x2 - 2 - score_w, line_y1, x2 - 2, line_y2), score_text, score_font, fill, align="right")
        if not last:
            draw.line((x1, y2, x2, y2), fill=COLORS["line"], width=1)

    # Labels and geometry -----------------------------------------------------

    @staticmethod
    def _nfl_scoreboard_events(card):
        card = card or {}
        main = card.get("main") or {}
        main_id = str(main.get("event_id") or "")
        events = [
            event for event in card.get("events") or []
            if event is not main and not (main_id and str(event.get("event_id") or "") == main_id)
        ]

        def start_key(event):
            start = event.get("start")
            return start.timestamp() if isinstance(start, datetime) else float("inf")

        by_state = {"live": [], "scheduled": [], "final": []}
        for event in events:
            by_state.setdefault(SportsDashboard._hub_event_state(event), []).append(event)
        live = sorted(by_state["live"], key=lambda event: (start_key(event), str(event.get("event_id") or "")))
        upcoming = sorted(by_state["scheduled"], key=start_key)
        finals = sorted(by_state["final"], key=start_key, reverse=True)
        return live + upcoming + finals

    @staticmethod
    def _nfl_scoreboard_state_labels(event, now):
        """(top, bottom, colour key) for the scoreboard clock column."""
        event = event or {}
        state = SportsDashboard._hub_event_state(event)
        start = event.get("start")
        if state == "live":
            clock = SportsDashboard._nfl_live_clock_label(event)
            if clock == "HALFTIME":
                return "HALF", "", "nfl_live"
            head, _separator, tail = clock.partition(" ")
            return head, tail, "nfl_live"
        if state == "final":
            detail = str(event.get("status_detail") or "").upper()
            bottom = "OT" if "OT" in detail else (start.strftime("%m/%d") if isinstance(start, datetime) else "")
            return "FINAL", bottom, "text"
        if not isinstance(start, datetime):
            return "TBD", "", "text"
        time_text = SportsDashboard._format_time(start)
        if start.date() == now.date():
            return time_text, str(event.get("broadcast") or "").strip(), "text"
        return start.strftime("%a").upper(), time_text, "text"

    @staticmethod
    def _nfl_status_pill_label(event, now):
        event = event or {}
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            return SportsDashboard._nfl_live_clock_label(event)
        if state == "final":
            return "FINAL/OT" if "OT" in str(event.get("status_detail") or "").upper() else "FINAL"
        start = event.get("start")
        if not isinstance(start, datetime):
            return "NEXT"
        time_text = SportsDashboard._format_time(start)
        days = (start.date() - now.date()).days
        if days == 0:
            return f"TODAY {time_text}"
        if 0 < days < 7:
            return f"{start.strftime('%a').upper()} {time_text}"
        return f"{start.strftime('%m/%d')} {time_text}"

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
        event = event or {}
        detail = str(event.get("status_detail") or "").strip().upper()
        period = SportsDashboard._lpl_int_value(event.get("period"))
        clock = str(event.get("clock") or "").strip()
        if "HALF" in detail:
            return "HALFTIME"
        if detail.startswith("END"):
            return f"END {SportsDashboard._nfl_period_label(period)}" if period else detail
        if period:
            label = SportsDashboard._nfl_period_label(period)
            return f"{label} {clock}" if clock else label
        return str(event.get("status_text") or "LIVE").strip().upper() or "LIVE"

    @staticmethod
    def _nfl_period_label(period):
        period = int(period or 0)
        if period > 4:
            return "OT" if period == 5 else f"{period - 4}OT"
        return f"Q{period}" if period > 0 else ""

    @staticmethod
    def _nfl_scorebug_period_count(event):
        event = event or {}
        played = max(len(event.get("linescore_a") or []), len(event.get("linescore_b") or []))
        if not played:
            return 0
        return max(4, min(6, played))

    @staticmethod
    def _nfl_possession_side(event):
        for side in ("a", "b"):
            if SportsDashboard._football_possession_side_fill_key(event, side) == "amber":
                return side
        return ""

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
    def _nfl_spread_for_side(event, side):
        """The point spread from this team's side, e.g. "-12.5" / "+12.5"."""
        event = event or {}
        match = re.fullmatch(r"\s*([A-Za-z]{2,4})\s+([+-]?\d+(?:\.\d+)?)\s*", str(event.get("spread") or ""))
        if not match:
            return ""
        code = match.group(1).upper()
        value = float(match.group(2))
        codes = {
            "a": str(event.get("team_a_code") or "").upper(),
            "b": str(event.get("team_b_code") or "").upper(),
        }
        if code not in codes.values():
            return ""
        own = value if codes[side] == code else -value
        text = f"{own:+.1f}".replace(".0", "")
        return "PK" if own == 0 else text

    @staticmethod
    def _nfl_kickoff_countdown_label(event, now):
        start = (event or {}).get("start")
        if not isinstance(start, datetime):
            return "TBD"
        minutes = int((start - now).total_seconds() // 60)
        if minutes <= 0:
            return "SOON"
        if minutes < 60:
            return f"IN {minutes}M"
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return f"IN {hours}H {minutes:02d}M"
        days, hours = divmod(hours, 24)
        return f"IN {days}D {hours}H"

    @staticmethod
    def _nfl_venue_footer_label(event, include_tv=True):
        event = event or {}
        parts = []
        venue = str(event.get("venue") or "").strip()
        city = str(event.get("city") or "").strip()
        if venue:
            parts.append(venue)
        if city and city not in venue:
            parts.append(city)
        broadcast = str(event.get("broadcast") or "").strip()
        if include_tv and broadcast:
            parts.append(f"TV {broadcast}")
        return "  ·  ".join(parts)

    @staticmethod
    def _nfl_team_ink_key(event, side):
        color = str((event or {}).get(f"team_{side}_color") or "").strip().lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", color):
            return "nfl_accent"
        rgb = tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))
        return min(
            NFL_TEAM_INK_KEYS,
            key=lambda key: sum((channel - ink) ** 2 for channel, ink in zip(rgb, DAY_COLORS[key])),
        )

    @staticmethod
    def _nfl_team_ink(event, side):
        return COLORS[SportsDashboard._nfl_team_ink_key(event, side)]
