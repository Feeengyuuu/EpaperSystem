"""NFL and college football panels: broadcast scorebug plus the week's scoreboard.

The featured game (live first, then the next kickoff, then the latest final)
fills the left card: quarter scores, timeouts, possession, a drive tracker
with the line to gain and live win odds. Every other game is listed on the
right with its own clock; college games put ranked matchups first.

Yard values follow ESPN's frame (yards from the home goal line). On screen the
away end zone is on the left and the home end zone on the right.
"""

import math

from .common import *
from .card_backdrops import paint_list_backdrop
from .scorebug_kit import KIT_SCOREBOARD_ROW_HEIGHT, KIT_TILE_HEIGHT, ScorebugSpec

SportsDashboard = None

FOOTBALL_TIMEOUTS_PER_HALF = 3
NCAA_LIST_WIDTH = 214
# Team logos in the live details: the end zones, the ball spot, the win odds.
FOOTBALL_ZONE_LOGO = 18
FOOTBALL_SPOT_LOGO = 18
FOOTBALL_WIN_LOGO = 16
# Before kick-off ESPN's matchup predictor (nfl_insights) reads as a win-share bar under the tiles,
# then one line with the matchup quality and the projected margin; each team's row adds its FPI rank
# and its playoff and title chances after the record.
FOOTBALL_PREDICTOR_ROW = 12
# That line's matchup-quality meter and the room its right end keeps for ESPN's mark ("ESPN 模型").
FOOTBALL_QUALITY_METER = 24
FOOTBALL_PREDICTOR_CREDIT = 50
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
        paint_list_backdrop(image, (x1 + 12, top - 2, list_x2, bottom), "gridiron", COLORS["panel"], (COLORS["paper"], COLORS["panel"]))
        self._draw_ncaa_matchup_card(image, draw, (list_x2 + 18, top, x2 - 12, bottom), card, now, spec)

    def _draw_ncaa_matchup_card(self, image, draw, box, card, now, spec):
        event = SportsDashboard._football_featured_event(card, "NCAA")
        if not event:
            self._draw_kit_empty_card(draw, box, spec, "No college schedule")
            return
        state = SportsDashboard._hub_event_state(event)
        left, top, right, bottom = self._draw_kit_card(draw, box, spec, state, image=image)
        self._draw_kit_status_row(
            draw, left, top + 8, right, spec, state,
            SportsDashboard._nfl_status_pill_label(event, now),
            "" if state == "live" else SportsDashboard._nfl_status_context_label(event, state),
            SportsDashboard._ncaa_header_badge_label(event) or self._football_header_week_label(card, event),
            image=image, broadcast=SportsDashboard._nfl_status_context_label(event, state) if state == "live" else "",
        )
        y = top + 32
        for index, side in enumerate(("a", "b")):
            if index:
                draw.line((left, y - 2, right, y - 2), fill=COLORS["line"], width=1)
            self._draw_ncaa_rank_row(image, draw, (left, y, right, y + 32), event, side, state, spec)
            y += 34
        details = (left, y + 4, right, bottom - 7)
        if state == "live":
            self._draw_nfl_live_details(image, draw, details, event, "NCAA")
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
        self._draw_ncaa_rank_football(image, draw, (x1 - 1, y1 + 8, x1 + 24, y1 + 24), rank, ink_key)
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
        paint_list_backdrop(image, column_box, "gridiron", COLORS["panel"], (COLORS["paper"], COLORS["panel"]))

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
            row_note=lambda event, side, state: football._football_row_note(event, side),
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
        left, top, right, bottom = self._draw_kit_card(draw, bounds, spec, state, image=image)
        right_label = self._football_header_week_label(card, event)
        if sport == "NCAA":
            right_label = SportsDashboard._ncaa_header_badge_label(event) or right_label
        self._draw_kit_status_row(
            draw, left, top + 8, right, spec, state,
            SportsDashboard._nfl_status_pill_label(event, now),
            "" if state == "live" else SportsDashboard._nfl_status_context_label(event, state),
            right_label,
            image=image, broadcast=SportsDashboard._nfl_status_context_label(event, state) if state == "live" else "",
        )
        rows_bottom = self._draw_kit_scorebug(image, draw, left, top + 31, right, event, state, spec)
        details = (left, rows_bottom + 6, right, bottom - 7)
        if state == "live":
            self._draw_nfl_live_details(image, draw, details, event, sport)
        elif state == "final":
            self._draw_nfl_final_details(image, draw, details, event, sport)
        else:
            self._draw_nfl_pregame_details(image, draw, details, event, now, sport)

    def _draw_ncaa_rank_football(self, image, draw, box, rank, ink_key):
        """A team's poll rank as a college football filling ``box``: in the team's colour with
        the rank on it, or, unranked, a leather ball with its laces; both with the white
        stripes near the tips that college balls carry."""
        x1, y1, x2, y2 = [int(value) for value in box]
        width, height, scale = x2 - x1, y2 - y1, 4
        fill = COLORS[ink_key] if rank else COLORS["amber"]
        layer = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        span, half = width * scale - 1, (height * scale - 1) / 2
        top = [(span * step / 24, half - half * math.sin(math.pi * step / 24) ** 0.7) for step in range(25)]
        ball = top + [(x, 2 * half - y) for x, y in reversed(top[1:-1])]
        pen.polygon(ball, fill=(*fill[:3], 255), outline=(*COLORS["text"][:3], 255), width=scale)
        white = (255, 255, 255, 255)
        for share in (0.2, 0.8):
            stripe_x = span * share
            reach = half * math.sin(math.pi * share) ** 0.7 - 1.5 * scale
            pen.line((stripe_x, half - reach, stripe_x, half + reach), fill=white, width=int(1.4 * scale))
        if not rank:
            pen.line((span * 0.36, half, span * 0.64, half), fill=white, width=scale)
            for share in (0.4, 0.5, 0.6):
                pen.line((span * share, half - 1.5 * scale, span * share, half + 1.5 * scale), fill=white, width=scale)
        layer = layer.resize((width, height), Image.Resampling.LANCZOS)
        image.paste(layer, (x1, y1), layer)
        if rank:
            text_ink = COLORS[self._kit_ink_text_key(ink_key)]
            self._draw_centered_in_box(draw, (x1, y1, x2 + 1, y2), str(rank), self._font(11 if rank < 10 else 10, True), text_ink)

    def _draw_nfl_football_icon(self, draw, x, y, width=11, height=7):
        x, y = int(x), int(y)
        draw.ellipse((x, y, x + width, y + height), fill=COLORS["amber"], outline=COLORS["border"], width=1)
        middle = y + height // 2
        draw.line((x + width // 2 - 2, middle, x + width // 2 + 2, middle), fill=COLORS["border"], width=1)

    # Live details ------------------------------------------------------------

    def _draw_nfl_live_details(self, image, draw, box, event, sport="NFL"):
        x1, y1, x2, y2 = [int(value) for value in box]
        self._draw_nfl_drive_tracker(image, draw, (x1, y1, x2, y1 + 22), event, sport)
        y = y1 + 27
        if self._draw_nfl_situation_row(image, draw, (x1, y, x2, y + 16), event, sport):
            y += 20
        last_play = str(event.get("last_play") or "").strip()
        if last_play and y + 13 <= y2:
            self._draw_kit_labeled_line(draw, (x1, y, x2, y + 13), "LAST", last_play)
            y += 19
        if y + 12 <= y2:
            self._draw_nfl_win_probability(image, draw, (x1, y, x2, y + 12), event, sport)

    def _football_team_logo(self, event, side, sport, size):
        """The team's own logo fitted into ``size`` pixels, or None when it has none."""
        fallback = SportsDashboard._event_team_logo_fallback(event, side, sport)
        return self._load_local_team_logo(fallback, size) or self._load_team_logo_for_render(event.get(f"team_{side}_logo"), size)

    def _football_inline_logo(self, event, side, sport, size):
        """The team's logo for a line of text on the panel, its white strokes inked on a light page."""
        logo = self._football_team_logo(event, side, sport, size)
        return self._team_logo_for_panel(logo) if logo is not None else None

    @staticmethod
    def _football_short_team(event, side, sport):
        """The team's short name: the nickname (酋长) in the NFL, the school in college."""
        if str(sport or "").upper() == "NCAA":
            return SportsDashboard._ncaa_school_label(event, side, include_rank=False, full=False)
        return SportsDashboard._football_display_team(event, side, "NFL")

    def _draw_nfl_drive_tracker(self, image, draw, box, event, sport="NFL"):
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
            # The end zone is painted with the team's logo, outlined in the zone's text ink so it
            # stands out from its own colour; the team code only when it has no logo.
            logo = self._football_team_logo(event, side, sport, min(FOOTBALL_ZONE_LOGO, zone_x2 - zone_x1 - 2, y2 - y1 - 4))
            if logo is not None:
                left = (zone_x1 + zone_x2 + 1 - logo.width) // 2
                top = (y1 + y2 + 1 - logo.height) // 2
                halo = SportsDashboard._event_mark_halo(logo, COLORS[self._kit_ink_text_key(ink_key)], pad=1)
                image.paste(halo, (left - 1, top - 1), halo)
                image.paste(logo, (left, top), logo)
                continue
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

    def _draw_nfl_situation_row(self, image, draw, box, event, sport="NFL"):
        x1, y1, x2, y2 = [int(value) for value in box]
        spec = self._football_spec(sport)
        down = SportsDashboard._nfl_short_down_label(event)
        spot = str(event.get("yard_line") or "").strip()
        spot_side, spot_yard = SportsDashboard._nfl_spot_parts(event)
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
        if spot_side:
            # "DEN 49" reads as the half's owner: its logo and nickname, then the yard line.
            logo = self._football_inline_logo(event, spot_side, sport, FOOTBALL_SPOT_LOGO)
            if logo is not None:
                image.paste(logo, (cursor, (y1 + y2 + 1 - logo.height) // 2), logo)
                cursor += logo.width + 3
            spot = f"{self._football_short_team(event, spot_side, sport)} {spot_yard}".strip()
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

    def _draw_nfl_win_probability(self, image, draw, box, event, sport="NFL"):
        away = event.get("win_pct_a")
        home = event.get("win_pct_b")
        if away is None or home is None or away + home <= 0:
            return False
        spec = self._football_spec(sport)
        self._draw_kit_share_bar(
            draw,
            box,
            f"{self._football_short_team(event, 'a', sport)} {round(away * 100)}%",
            f"{round(home * 100)}% {self._football_short_team(event, 'b', sport)}",
            away / (away + home),
            self._kit_team_ink_key(event.get("team_a_color"), spec.accent),
            self._kit_team_ink_key(event.get("team_b_color"), spec.accent),
            image=image,
            logos=tuple(self._football_inline_logo(event, side, sport, FOOTBALL_WIN_LOGO) for side in ("a", "b")),
        )
        return True

    # Final / pregame details -------------------------------------------------

    def _draw_nfl_final_details(self, image, draw, box, event, sport="NFL"):
        x1, y1, x2, y2 = [int(value) for value in box]
        spec = self._football_spec(sport)
        leaders = list(event.get("leaders") or [])
        recap = str(event.get("recap") or "").strip()
        if recap:
            # ESPN's one-line recap over the foot line; the leader rows close up above it.
            self._draw_kit_recap_line(draw, (x1, y2 - 28, x2, y2 - 16), recap)
        if leaders:
            self._draw_kit_leader_rows(image, draw, x1, y1, x2, y2 - (30 if recap else 14), event, leaders, spec)
        else:
            winner = SportsDashboard._football_final_winner_label(event, sport)
            if winner:
                winner, winner_font = self._fit_text(draw, winner, x2 - x1, 14, bold=True, min_size=10)
                self._draw_text_in_box(draw, (x1, y1, x2, y1 + 20), winner, winner_font, COLORS["text"])
        self._draw_football_footer(image, draw, (x1, y2 - 12, x2, y2), event, sport)

    def _draw_nfl_pregame_details(self, image, draw, box, event, now, sport="NFL"):
        x1, y1, x2, y2 = [int(value) for value in box]
        spec = self._football_spec(sport)
        tiles = [
            ("KICKOFF", SportsDashboard._kit_countdown_label(event.get("start"), now)),
            ("TV", str(event.get("broadcast") or "").strip() or "TBD"),
            ("O/U", str(event.get("over_under") or "").replace("O/U", "").strip() or "--"),
        ]
        predictor = event.get("predictor") or {}
        has_predictor = isinstance(predictor.get("win_a"), (int, float)) and isinstance(predictor.get("win_b"), (int, float))
        # ESPN's projection takes the room of the second season leader, and a little of the tiles'.
        tiles_bottom = self._draw_kit_tiles(draw, (x1, y1, x2, y2), tiles, spec, image=image, glass=True,
                                            tile_h=KIT_TILE_HEIGHT - (4 if has_predictor else 0))
        leaders_top = tiles_bottom + 6
        if has_predictor:
            self._draw_nfl_predictor(image, draw, (x1, tiles_bottom + 4, x2, tiles_bottom + 4 + FOOTBALL_PREDICTOR_ROW),
                                     event, predictor, sport)
            leaders_top = tiles_bottom + 4 + 2 * FOOTBALL_PREDICTOR_ROW + 5
        # Before kickoff ESPN's leaders are season totals for the two teams.
        self._draw_kit_leader_rows(image, draw, x1, leaders_top, x2, y2 - 14, event, event.get("leaders"), spec, max_rows=2)
        self._draw_football_footer(image, draw, (x1, y2 - 12, x2, y2), event, sport, include_tv=False)

    def _draw_nfl_predictor(self, image, draw, box, event, predictor, sport):
        """The two win chances as a share bar, then '对阵质量 89 · 预计 海鹰 +1.8'."""
        x1, y1, x2, y2 = [int(value) for value in box]
        spec = self._football_spec(sport)
        win_a, win_b = float(predictor["win_a"]), float(predictor["win_b"])
        share = win_a / (win_a + win_b) if win_a + win_b > 0 else 0.5
        self._draw_kit_share_bar(
            draw, box,
            f"{self._football_short_team(event, 'a', sport)} {round(win_a * 100)}%",
            f"{round(win_b * 100)}% {self._football_short_team(event, 'b', sport)}", share,
            self._kit_team_ink_key(event.get("team_a_color"), spec.accent),
            self._kit_team_ink_key(event.get("team_b_color"), spec.accent),
            image=image, logos=tuple(self._football_inline_logo(event, side, sport, FOOTBALL_PREDICTOR_ROW) for side in ("a", "b")),
        )
        line = (x1 + 28, y2 + 2, x2, y2 + 2 + FOOTBALL_PREDICTOR_ROW)
        if self._draw_nfl_predictor_line(image, draw, line, event, predictor, sport):
            return
        caption = SportsDashboard._nfl_predictor_caption(event, predictor, sport)
        if caption:
            caption, font = self._fit_text_ellipsis(draw, caption, x2 - x1 - 28, 9, bold=False, min_size=8)
            self._draw_text_in_box(draw, line, caption, font, COLORS["text"])

    def _draw_nfl_predictor_line(self, image, draw, box, event, predictor, sport):
        """The line under the win share: '对阵质量 79' with a short meter of it, a hairline, '预计 [队标]
        美洲虎 净胜 9.0 分', and at the right end ESPN's mark naming the model. False (nothing drawn) without a
        page to paste the logos on or when the pieces do not fit; the plain caption stands in."""
        if image is None:
            return False
        x1, y1, x2, y2 = [int(value) for value in box]
        middle = (y1 + y2) // 2
        font, strong = self._font(9, False), self._font(9, True)
        runs = []
        quality = predictor.get("quality")
        if isinstance(quality, (int, float)):
            runs += [("text", "对阵质量 ", font), ("text", str(round(quality)), strong),
                     ("meter", max(0.0, min(100.0, float(quality))) / 100, FOOTBALL_QUALITY_METER + 8)]
        margin = predictor.get("margin_a")
        if isinstance(margin, (int, float)) and abs(margin) >= 0.05:
            side = "a" if margin > 0 else "b"
            if runs:
                runs.append(("rule", None, 12))
            runs.append(("text", "预计 ", font))
            logo = self._football_inline_logo(event, side, sport, FOOTBALL_PREDICTOR_ROW)
            if logo is not None:
                runs.append(("logo", logo, logo.width + 3))
            team = SportsDashboard._football_short_team(event, side, sport)
            runs += [("text", f"{team} 净胜 ", font), ("text", f"{abs(margin):.1f}", strong), ("text", " 分", font)]
        if not runs:
            return False
        widths = [run[2] if run[0] != "text" else math.ceil(self._text_width(draw, run[1], run[2])) for run in runs]
        credit = x2 - x1 - sum(widths) >= FOOTBALL_PREDICTOR_CREDIT + 6
        if sum(widths) > x2 - x1:
            return False
        x = x1
        for (kind, content, extra), width in zip(runs, widths):
            if kind == "text":
                self._draw_text_in_box(draw, (x, y1, x + width, y2), content, extra, COLORS["text"])
            elif kind == "logo":
                image.paste(content, (x, middle - content.height // 2), content)
            elif kind == "rule":
                # A hairline between the matchup's quality and the projection.
                draw.line((x + width // 2 - 1, middle - 4, x + width // 2 - 1, middle + 4), fill=COLORS["border"])
            else:
                left, right = x + 4, x + 4 + FOOTBALL_QUALITY_METER
                draw.rectangle((left, middle - 2, right, middle + 2), fill=COLORS["panel"], outline=COLORS["border"])
                filled = left + round((right - left) * content)
                if filled > left:
                    draw.rectangle((left, middle - 2, filled, middle + 2), fill=COLORS["text"])
            x += width
        if credit:
            self._draw_source_credit(image, draw, (x2 - FOOTBALL_PREDICTOR_CREDIT, y1, x2, y2), "ESPN 模型",
                                     COLORS["text"], size=9, align="right")
        return True

    @staticmethod
    def _nfl_predictor_caption(event, predictor, sport="NFL"):
        """'对阵质量 89 · 预计 海鹰 +1.8' (the favourite's projected margin)."""
        parts = []
        quality = predictor.get("quality")
        if isinstance(quality, (int, float)):
            parts.append(f"对阵质量 {round(quality)}")
        margin = predictor.get("margin_a")
        if isinstance(margin, (int, float)) and abs(margin) >= 0.05:
            side = "a" if margin > 0 else "b"
            parts.append(f"预计 {SportsDashboard._football_short_team(event, side, sport)} +{abs(margin):.1f}")
        return " · ".join(parts)

    @staticmethod
    def _football_row_note(event, side):
        """A team row's line under its name: the record, then its FPI rank and its chances of the
        playoffs and the title ('5-2 · FPI#3 · 季后赛98% · 夺冠13%')."""
        record = str((event or {}).get(f"record_{side}") or "").strip()
        fpi = (event or {}).get(f"fpi_{side}") or {}
        if not isinstance(fpi, Mapping) or not isinstance(fpi.get("rank"), int):
            return record

        def percent(value):
            value = float(value) * 100
            return "<1%" if 0 < value < 1 else f"{value:.0f}%"

        parts = [record, f"FPI#{fpi['rank']}"]
        if isinstance(fpi.get("playoffs"), (int, float)):
            parts.append(f"季后赛{percent(fpi['playoffs'])}")
        if isinstance(fpi.get("title"), (int, float)):
            parts.append(f"夺冠{percent(fpi['title'])}")
        return " · ".join(part for part in parts if part)

    def _draw_football_footer(self, image, draw, box, event, sport, include_tv=True):
        """The card's foot line, the stadium as its official logo where it has one, else as the
        football stadium icon and its name; a showcase game's note ('NFL Munich Game') leads it as
        that game's logo."""
        venue = str((event or {}).get("venue") or "").strip()
        note = SportsDashboard._sport_note_mark(sport, event)
        labelled = {**(event or {}), "venue": "", **({"headline": note[1]} if note else {})}
        rest = SportsDashboard._football_footer_label(labelled, sport, include_tv=include_tv)
        weather = SportsDashboard._football_weather_label(event)
        rest = "  ·  ".join(part for part in (rest, weather) if part)
        if not self._draw_kit_venue_line(image, draw, box, venue, after=rest, kind="football",
                                         mark=note[0] if note else None):
            label = SportsDashboard._football_footer_label(event, sport, include_tv=include_tv)
            self._draw_kit_footer(draw, box, "  ·  ".join(part for part in (label, weather) if part))

    @staticmethod
    def _football_weather_label(event):
        """'8°C 晴间多云' for an outdoor game's forecast, else ''.

        ESPN's forecast (and the cached event) stays in Fahrenheit; the card shows Celsius."""
        weather = (event or {}).get("weather") or {}
        if not isinstance(weather.get("temp"), int) or isinstance(weather.get("temp"), bool):
            return ""
        celsius = round((weather["temp"] - 32) * 5 / 9)
        return f"{celsius}°C {weather.get('condition') or ''}".strip()

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
        side, yard = SportsDashboard._nfl_spot_parts(event)
        if yard == "50":
            return 50
        if side == "b":
            return int(yard)
        if side == "a":
            return 100 - int(yard)
        return None

    @staticmethod
    def _nfl_spot_parts(event):
        """("a" or "b", yard text) for an "ATL 19" spot: whose half the ball is in and the yard
        line; the side is empty at midfield or when the code is neither team's."""
        match = re.fullmatch(r"\s*([A-Za-z]{2,4})?\s*(\d{1,2})\s*", str((event or {}).get("yard_line") or ""))
        if not match:
            return "", ""
        yard, code = match.group(2), str(match.group(1) or "").upper()
        if yard == "50" or not code:
            return "", yard
        for side in ("a", "b"):
            if code == str((event or {}).get(f"team_{side}_code") or "").upper():
                return side, yard
        return "", yard

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
