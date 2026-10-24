"""MLB panel ("ballpark scoreboard").

The left card is a stadium-style line score: an inning-by-inning grid with
runs, hits and errors, the current inning lit and the batting team marked.
Under it: the pitcher/batter matchup while live, the winning/losing/saving
pitchers after the game, or the probable starters with first-pitch and TV
tiles before it. The right column shows the live situation (bases diamond
and ball/strike/out lights) during a game, otherwise the postseason series
game by game, then the other games of the day.
"""

from .baseball_icons import BASEBALL_ICON_SIZE, BASEBALL_ICONS, baseball_icon
from .common import *
from .scorebug_kit import ScorebugSpec

SportsDashboard = None

MLB_CARD_WIDTH = 340
MLB_TABLE_ROW_HEIGHT = 24
MLB_TABLE_HEADER_HEIGHT = 14
MLB_TABLE_MIN_INNING_WIDTH = 13


class BaseballRenderMixin:
    def _draw_mlb_standalone_panel(self, image, draw, bounds, card, source_state, now):
        spec = self._mlb_spec()
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS[spec.accent], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "MLB", card, source_state)
        card = card or {}
        main = card.get("main") or {}
        top, bottom = y1 + 57, y2 - 7
        card_x2 = x1 + 12 + MLB_CARD_WIDTH
        self._draw_vertical_split(draw, card_x2 + 9, top - 5, bottom)
        if not main:
            self._draw_kit_empty_card(draw, (x1 + 12, top, card_x2, bottom), spec, "No MLB schedule")
            return
        self._draw_mlb_scoreboard_card(image, draw, (x1 + 12, top, card_x2, bottom), main, now, spec)
        column = (card_x2 + 16, top - 2, x2 - 12, bottom)
        others = SportsDashboard._kit_scoreboard_order(card.get("events"), main)
        series = SportsDashboard._mlb_series_games(card.get("events"), main)
        state = SportsDashboard._hub_event_state(main)
        y = column[1]
        if state == "live":
            y = self._draw_mlb_situation(draw, (column[0], y, column[2], y + 96), main, spec) + 6
        elif series:
            y = self._draw_mlb_series_list(image, draw, (column[0], y, column[2], column[3]), main, series, now, spec) + 6
            others = [event for event in others if event not in series]
        if column[3] - y >= 40:
            self._draw_kit_ticker(image, draw, (column[0], y, column[2], column[3]), others, now, spec, title="AROUND MLB")

    def _mlb_spec(self):
        baseball = SportsDashboard
        return ScorebugSpec(
            "MLB",
            "mlb_accent",
            "mlb_live",
            "mlb_tag",
            "mlb_field_tint",
            team_name=lambda event, side, full: baseball._mlb_display_team_from_event(event, side, full=full),
            logo_fallback=lambda event, side: baseball._event_team_logo_fallback(event, side, "MLB"),
            winner_side=baseball._mlb_winner_side,
            state_labels=baseball._mlb_state_labels,
            row_flag=lambda event: bool(set(str(event.get("bases") or "")) & {"2", "3"}),
        )

    # Left card: stadium line score ---------------------------------------------

    def _draw_mlb_scoreboard_card(self, image, draw, box, event, now, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        state = SportsDashboard._hub_event_state(event)
        left, top, right, bottom = self._draw_kit_card(draw, box, spec, state)
        context = str(event.get("broadcast") or "").strip() if state == "live" else SportsDashboard._mlb_context_label(event, state)
        series = self._mlb_first_fitting(draw, [SportsDashboard._mlb_series_label(event, full) for full in (True, False)], 110, 11, bold=False)
        series_side = SportsDashboard._mlb_series_side(event)
        series_logo = (lambda x, y: self._draw_mlb_team_logo(image, draw, event, series_side, x, y, 14, spec)) if series_side else None
        self._draw_kit_status_row(draw, left, top + 8, right, spec, state, SportsDashboard._mlb_pill_label(event, now), context, series, series_logo)
        table_bottom = self._draw_mlb_line_table(image, draw, (left, top + 33, right), event, state, spec)
        y = table_bottom + 8
        if state in {"live", "final"}:
            people = self._draw_mlb_matchup if state == "live" else self._draw_mlb_decisions
            people(image, draw, (left, y, right, y + 16), event, spec)
            self._draw_mlb_ballpark(image, (left, y + 20, right, bottom - 22), event)
        else:
            budget = right - left - self._text_width(draw, "SP", self._font(8, False)) - 6
            probables = self._mlb_first_fitting(draw, [SportsDashboard._mlb_probables_label(event, full) for full in (True, False)], budget, 10)
            if probables:
                self._draw_kit_labeled_line(draw, (left, y, right, y + 14), "SP", probables)
                y += 20
            tiles = [
                ("FIRST PITCH", SportsDashboard._kit_countdown_label(event.get("start"), now)),
                ("TV", str(event.get("broadcast") or "").strip() or "TBD"),
            ]
            self._draw_kit_tiles(draw, (left, y, right, bottom - 22), tiles, spec, tile_h=min(36, bottom - 22 - y), image=image)
        self._draw_kit_footer(draw, (left, bottom - 19, right, bottom - 7), SportsDashboard._mlb_footer_label(event, state))

    def _draw_mlb_ballpark(self, image, box, event):
        """The card's spare band: the postseason series mark on the right and
        the ballpark strip filling the room left of it."""
        x1, y1, x2, y2 = [int(value) for value in box]
        mark = self._draw_event_mark(image, SportsDashboard._mlb_series_mark_path(event), (x2 - 110, y1, x2, y2), align="right")
        if mark:
            x2 = mark[0] - 10
        strip = self._load_local_logo(LOCAL_MLB_BALLPARK_STRIP_PATH, (x2 - x1, y2 - y1), alpha_threshold=8) if x2 - x1 >= 80 and y2 - y1 >= 24 else None
        if strip:
            image.paste(strip, (x1 + (x2 - x1 - strip.width) // 2, y2 - strip.height), strip)

    def _draw_mlb_line_table(self, image, draw, box, event, state, spec):
        """Inning grid with R/H/E; returns the table bottom."""
        x1, y1, x2 = [int(value) for value in box]
        innings = list(event.get("innings") or [])
        count = max(int(event.get("scheduled_innings") or 9), len(innings))
        live = state == "live"
        total_w = 23
        totals_x1 = x2 - 3 * total_w
        slot = 14 if live else 6
        names, name_size, team_w = self._mlb_table_names(draw, event, spec, slot, count, totals_x1 - 4 - x1)
        col_w = (totals_x1 - 4 - (x1 + team_w)) / max(1, count)
        y2 = y1 + MLB_TABLE_HEADER_HEIGHT + 2 * MLB_TABLE_ROW_HEIGHT
        draw.rounded_rectangle((x1, y1, x2, y2), radius=4, fill=COLORS[spec.tint], outline=COLORS["border"], width=1)
        current = len(innings) if live else 0
        if live and current:
            left = int(x1 + team_w + (current - 1) * col_w)
            draw.rectangle((left + 1, y1 + 1, int(left + col_w) - 1, y2 - 1), fill=COLORS[spec.tag])
        draw.rectangle((totals_x1 - 2, y1 + 1, x2 - 1, y2 - 1), fill=COLORS["panel"])
        header_y2 = y1 + MLB_TABLE_HEADER_HEIGHT
        for index in range(count):
            left = int(x1 + team_w + index * col_w)
            right = int(x1 + team_w + (index + 1) * col_w)
            self._draw_centered_in_box(draw, (left, y1, right, header_y2), str(index + 1), self._font(8, live and index + 1 == current), COLORS["text"])
        for index, label in enumerate(("R", "H", "E")):
            left = totals_x1 + index * total_w
            self._draw_centered_in_box(draw, (left, y1, left + total_w, header_y2), label, self._font(8, True), COLORS["text"])
        draw.line((x1 + 3, header_y2, x2 - 3, header_y2), fill=COLORS["border"], width=1)
        draw.line((totals_x1 - 2, y1 + 2, totals_x1 - 2, y2 - 2), fill=COLORS["border"], width=1)
        batting = SportsDashboard._mlb_batting_side(event)
        winner = SportsDashboard._mlb_winner_side(event) if state == "final" else ""
        for row, side in enumerate(("a", "b")):
            row_y1 = header_y2 + row * MLB_TABLE_ROW_HEIGHT
            row_y2 = row_y1 + MLB_TABLE_ROW_HEIGHT
            if row:
                draw.line((x1 + 3, row_y1, x2 - 3, row_y1), fill=COLORS["line"], width=1)
            prefix = f"team_{side}"
            ink = COLORS[self._kit_team_ink_key(event.get(f"{prefix}_color"), spec.accent)]
            draw.rectangle((x1 + 3, row_y1 + 4, x1 + 5, row_y2 - 4), fill=ink)
            self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), x1 + 9, row_y1 + 4, 16, spec.hook("logo_fallback", event, side))
            dimmed = bool(winner) and winner != side
            name, font = self._fit_text_ellipsis(draw, names[row], team_w - 28 - slot, name_size, bold=not dimmed, min_size=8)
            self._draw_text_in_box(draw, (x1 + 28, row_y1, x1 + team_w - slot, row_y2), name, font, COLORS["text"])
            if batting == side:
                self._draw_mlb_ball_icon(draw, x1 + team_w - 10, row_y1 + 8)
            for index in range(count):
                runs = innings[index][0 if side == "a" else 1] if index < len(innings) else None
                if runs is None:
                    text = "X" if (state == "final" and side == "b" and index < len(innings)) else ""
                else:
                    text = str(runs)
                if text:
                    left = int(x1 + team_w + index * col_w)
                    right = int(x1 + team_w + (index + 1) * col_w)
                    self._draw_centered_in_box(draw, (left, row_y1, right, row_y2), text, self._font(12, live and index + 1 == current), COLORS["text"])
            if state == "scheduled":
                continue
            line = event.get("away_line" if side == "a" else "home_line") or {}
            for index, key in enumerate(("runs", "hits", "errors")):
                value = line.get(key)
                if key == "runs" and value is None:
                    value = event.get(f"wins_{side}")
                left = totals_x1 + index * total_w
                font = self._font(15 if key == "runs" else 11, key == "runs" and not dimmed)
                self._draw_centered_in_box(draw, (left, row_y1, left + total_w, row_y2), "-" if value is None else str(value), font, COLORS["text"])
        return y2

    def _mlb_table_names(self, draw, event, spec, slot, count, room):
        """Both rows' names, their size and the team column width.

        Full names (坦帕湾光芒) at 12 or 11 points while every inning column keeps
        MLB_TABLE_MIN_INNING_WIDTH, else the short names (光芒); both rows switch
        together. ``slot`` is the room kept right of the name for the bat icon."""
        for full in (True, False):
            names = [spec.hook("team_name", event, side, full, default=None) or str(event.get(f"team_{side}_code") or "TBD")
                     for side in ("a", "b")]
            for size in (12, 11):
                widest = max(self._text_width(draw, name, self._font(size, True)) for name in names)
                team_w = max(60, 28 + widest + slot)
                if room - team_w >= count * MLB_TABLE_MIN_INNING_WIDTH:
                    return names, size, team_w
        return names, 11, 60

    def _mlb_first_fitting(self, draw, texts, budget, size, bold=True):
        """The first of ``texts`` (full names, then short) that fits ``budget`` at
        ``size`` or a point smaller, as the kit's team names do; else the last."""
        for text in texts[:-1]:
            if any(self._text_width(draw, text, self._font(candidate, bold)) <= budget for candidate in (size, size - 1)):
                return text
        return texts[-1]

    def _draw_mlb_decisions(self, image, draw, box, event, spec):
        """W/L/S pitchers; W and S pitch for the winner, L for the loser."""
        winner = SportsDashboard._mlb_winner_side(event)
        loser = {"a": "b", "b": "a"}.get(winner, "")
        people = [(str(item.get("label") or ""), str(item.get("name") or ""), loser if item.get("label") == "L" else winner)
                  for item in event.get("decisions") or []]
        self._draw_mlb_people(image, draw, box, event, people, spec)

    def _draw_mlb_matchup(self, image, draw, box, event, spec):
        """The pitcher (fielding side) facing the batter (batting side)."""
        batting = SportsDashboard._mlb_batting_side(event)
        fielding = {"a": "b", "b": "a"}.get(batting, "")
        people = [("P", str(event.get("current_pitcher") or "").strip(), fielding),
                  ("AB", str(event.get("current_batter") or "").strip(), batting)]
        self._draw_mlb_people(image, draw, box, event, people, spec)

    def _draw_mlb_people(self, image, draw, box, event, people, spec):
        """``(label, name, side)`` as the label's icon (trophy, arrow, lifebuoy,
        pitch, bat), the name and then that side's team logo, sharing the row
        evenly."""
        x1, y1, x2, y2 = [int(value) for value in box]
        people = [person for person in people if person[1] and person[0] in BASEBALL_ICONS]
        logo = y2 - y1 - 2
        cursor = x1
        for index, (label, name, side) in enumerate(people):
            logo_w = logo + 3 if side else 0
            room = (x2 - cursor) // (len(people) - index)
            if room < 40:
                break
            icon = baseball_icon(label, tuple(COLORS["border"]))
            image.paste(icon, (cursor, y1 + (y2 - y1 - BASEBALL_ICON_SIZE) // 2), icon)
            name, font = self._fit_text_ellipsis(draw, name, min(110, room - BASEBALL_ICON_SIZE - 4 - logo_w - 8), 10, bold=True, min_size=8)
            self._draw_text_in_box(draw, (cursor + BASEBALL_ICON_SIZE + 4, y1, x2, y2), name, font, COLORS["text"])
            cursor += BASEBALL_ICON_SIZE + 4 + self._text_width(draw, name, font)
            if side:
                self._draw_mlb_team_logo(image, draw, event, side, cursor + 3, y1 + 1, logo, spec)
            cursor += logo_w + 10

    def _draw_mlb_team_logo(self, image, draw, event, side, x, y, size, spec):
        self._draw_team_logo(image, draw, event.get(f"team_{side}_logo"), x, y, size, spec.hook("logo_fallback", event, side))

    # Right column -------------------------------------------------------------

    def _draw_mlb_situation(self, draw, box, event, spec):
        """Bases diamond plus ball/strike/out lights; returns the bottom."""
        x1, y1, x2, y2 = [int(value) for value in box]
        self._draw_kit_column_header(draw, x1, y1, x2, spec, "ON FIELD")
        bases = set(str(event.get("bases") or ""))
        center_x = x1 + 46
        center_y = y1 + 58
        size = 11
        draw.line((center_x - 26, center_y + 4, center_x, center_y + 30, center_x + 26, center_y + 4), fill=COLORS["border"], width=1)
        for base, (dx, dy) in {"2": (0, -22), "1": (24, 2), "3": (-24, 2)}.items():
            cx, cy = center_x + dx, center_y + dy
            points = [(cx, cy - size), (cx + size, cy), (cx, cy + size), (cx - size, cy)]
            draw.polygon(points, fill=COLORS["amber"] if base in bases else COLORS["panel"], outline=COLORS["border"])
        draw.polygon([(center_x - 5, center_y + 26), (center_x + 5, center_y + 26), (center_x + 5, center_y + 31), (center_x, center_y + 35), (center_x - 5, center_y + 31)], fill=COLORS["panel"], outline=COLORS["border"])
        lights_x = x1 + 98
        rows = (
            ("B", SportsDashboard._lpl_int_value(event.get("balls")), 4, "text"),
            ("S", SportsDashboard._lpl_int_value(event.get("strikes")), 3, "text"),
            ("O", SportsDashboard._lpl_int_value(event.get("outs")), 3, spec.live),
        )
        for index, (label, value, total, ink) in enumerate(rows):
            row_y = y1 + 30 + index * 18
            self._draw_text_in_box(draw, (lights_x, row_y, lights_x + 12, row_y + 14), label, self._font(11, True), COLORS["text"])
            for dot in range(total - (1 if label == "B" else 0)):
                left = lights_x + 16 + dot * 15
                filled = value is not None and dot < value
                draw.ellipse((left, row_y + 2, left + 10, row_y + 12), fill=COLORS[ink] if filled else COLORS["panel"], outline=COLORS["border"], width=1)
        return y2

    def _draw_mlb_series_list(self, image, draw, box, main, series, now, spec):
        """The postseason series game by game; returns the bottom."""
        x1, y1, x2, y2 = [int(value) for value in box]
        title = str(main.get("series_game") or "").split(" Game")[0].strip().upper() or "SERIES"
        status = self._mlb_first_fitting(draw, [SportsDashboard._mlb_series_label(main, full) for full in (True, False)], (x2 - x1) // 2, 11)
        mark = self._draw_event_mark(image, SportsDashboard._mlb_series_mark_path(main), (x1, y1, x1 + 96, y1 + 34), align="left")
        if mark:
            side = SportsDashboard._mlb_series_side(main)
            status, status_font = self._fit_text(draw, status, x2 - mark[2] - 6 - (18 if side else 0), 11, bold=True, min_size=8)
            self._draw_text_in_box(draw, (mark[2] + 6, y1, x2, y1 + 34), status, status_font, COLORS[spec.live], align="right")
            if side and status:
                self._draw_mlb_team_logo(image, draw, main, side, x2 - self._text_width(draw, status, status_font) - 18, y1 + 10, 14, spec)
            draw.line((x1, y1 + 37, x2, y1 + 37), fill=COLORS["border"], width=1)
            y = y1 + 41
        else:
            y = self._draw_kit_column_header(draw, x1, y1, x2, spec, title, status)
        for event in series:
            if y + 15 > y2 - 44:
                break
            state = SportsDashboard._hub_event_state(event)
            number = SportsDashboard._mlb_game_number(event)
            self._draw_text_in_box(draw, (x1, y, x1 + 22, y + 15), f"G{number}" if number else "G", self._font(9, True), COLORS["text"])
            if state == "scheduled":
                # When on the left, the networks as logos on the right.
                top, bottom, _key = SportsDashboard._mlb_state_labels(event, now)
                text = f"{top} {bottom}".strip() if not top.endswith("M") else top
                text, font = self._fit_text_ellipsis(draw, text, (x2 - x1) // 2, 10, bold=event is main, min_size=8)
                self._draw_text_in_box(draw, (x1 + 24, y, x2, y + 15), text, font, COLORS["text"])
                broadcast_x1 = x1 + 30 + self._text_width(draw, text, font)
                self._draw_series_row_broadcast(image, draw, (broadcast_x1, y, x2, y + 15), event)
                y += 16
                continue
            winner = SportsDashboard._mlb_winner_side(event)
            runs = sorted((event.get("wins_a") or 0, event.get("wins_b") or 0), reverse=True)
            text = SportsDashboard._mlb_inning_label(event)
            logo_room = 0
            if state == "final" and winner:
                logo_room = 16
                text = self._mlb_first_fitting(draw, [f"{SportsDashboard._mlb_display_team_from_event(event, winner, full=full)} {runs[0]}-{runs[1]}"
                                                     for full in (True, False)], x2 - x1 - 26 - logo_room, 10)
            text, font = self._fit_text_ellipsis(draw, text, x2 - x1 - 26 - logo_room, 10, bold=True, min_size=8)
            self._draw_text_in_box(draw, (x1 + 24, y, x2, y + 15), text, font, COLORS["mlb_live" if state == "live" else "text"], align="right")
            if logo_room:
                self._draw_series_row_winner_logo(image, draw, event, winner, x2 - self._text_width(draw, text, font) - logo_room, y, y + 15, spec)
            y += 16
        return y

    def _draw_mlb_ball_icon(self, draw, x, y, size=8):
        x, y = int(x), int(y)
        draw.ellipse((x, y, x + size, y + size), fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.arc((x - size // 2 + 1, y + 1, x + size // 2 + 1, y + size - 1), -60, 60, fill=COLORS["mlb_live"], width=1)
        draw.arc((x + size // 2 - 1, y + 1, x + size + size // 2 - 1, y + size - 1), 120, 240, fill=COLORS["mlb_live"], width=1)

    # Labels ------------------------------------------------------------------

    @staticmethod
    def _mlb_series_games(events, main):
        main = main or {}
        series_name = str(main.get("series_game") or "").split(" Game")[0].strip()
        if not series_name or not main.get("series_summary"):
            return []
        teams = {str(main.get("team_a_code") or ""), str(main.get("team_b_code") or "")}
        games = [
            event for event in events or []
            if {str(event.get("team_a_code") or ""), str(event.get("team_b_code") or "")} == teams
            and str(event.get("series_game") or "").startswith(series_name)
            and str(event.get("status_detail") or "").lower() != "postponed"
        ]
        return sorted(games, key=lambda event: event.get("start") or datetime.max.replace(tzinfo=timezone.utc))

    @staticmethod
    def _mlb_game_number(event):
        match = re.search(r"\bGame\s+(\d+)", str((event or {}).get("series_game") or ""), flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def _mlb_batting_side(event):
        event = event or {}
        if SportsDashboard._hub_event_state(event) != "live":
            return ""
        half = str(event.get("inning_state") or "").strip().lower()
        if half in {"middle", "end"}:
            return ""
        if half == "top" or (not half and event.get("is_top_inning") is True):
            return "a"
        if half == "bottom" or (not half and event.get("is_top_inning") is False):
            return "b"
        return ""

    @staticmethod
    def _mlb_inning_label(event):
        """'TOP 7', 'BOT 7', 'MID 7', 'END 7'."""
        event = event or {}
        inning = SportsDashboard._lpl_int_value(event.get("inning"))
        half = str(event.get("inning_state") or "").strip().lower()
        prefix = {"top": "TOP", "bottom": "BOT", "middle": "MID", "end": "END"}.get(half, "")
        if not inning:
            return str(event.get("status_text") or "LIVE").strip().upper() or "LIVE"
        return f"{prefix} {inning}".strip()

    @staticmethod
    def _mlb_pill_label(event, now):
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            label = SportsDashboard._mlb_inning_label(event)
            outs = SportsDashboard._lpl_int_value((event or {}).get("outs"))
            if outs is not None and SportsDashboard._mlb_batting_side(event):
                return f"{label} · {outs} OUT"
            return label
        if state == "final":
            innings = len((event or {}).get("innings") or [])
            scheduled = int((event or {}).get("scheduled_innings") or 9)
            return f"FINAL/{innings}" if innings > scheduled else "FINAL"
        return SportsDashboard._kit_start_label((event or {}).get("start"), now)

    @staticmethod
    def _mlb_context_label(event, state):
        if state == "final":
            start = (event or {}).get("start")
            return start.strftime("%m/%d") if isinstance(start, datetime) else ""
        return ""

    @staticmethod
    def _mlb_series_status(event):
        """statsapi's series result ('Series tied 2-2', 'CLE leads 2-1',
        'MIL wins 4-0') as (verb, team code, games): verb is 'tied', 'leads',
        'wins' or '' when the text has another shape."""
        summary = str((event or {}).get("series_summary") or "").strip()
        tied = re.match(r"(?:series\s+)?tied\s+(?:series\s+)?(\d+-\d+)$", summary, flags=re.IGNORECASE)
        if tied:
            return "tied", "", tied.group(1)
        match = re.match(r"(\S+)\s+(leads?|wins?|won)\s+(?:series\s+)?(\d+-\d+)$", summary, flags=re.IGNORECASE)
        if match:
            return ("leads" if match.group(2).lower().startswith("lead") else "wins"), match.group(1).upper(), match.group(3)
        return "", "", ""

    @staticmethod
    def _mlb_series_side(event):
        """The side ('a'/'b') of the team the series result names, else ''."""
        _verb, code, _games = SportsDashboard._mlb_series_status(event)
        for side in ("a", "b"):
            if code and code == str((event or {}).get(f"team_{side}_code") or "").strip().upper():
                return side
        return ""

    @staticmethod
    def _mlb_series_label(event, full=False):
        """'2-2 平' / '守护者 2-1 领先' / '酿酒人 4-0 胜出'; statsapi's own text
        when the team is unknown."""
        verb, code, games = SportsDashboard._mlb_series_status(event)
        if verb == "tied":
            return f"{games} 平"
        side = SportsDashboard._mlb_series_side(event)
        if side:
            team = SportsDashboard._mlb_display_team_from_event(event, side, full=full)
        else:
            team = SportsDashboard._mlb_display_team_name(code, full=full) if code in MLB_TEAM_ZH_NAMES else ""
        if verb and team:
            return f"{team} {games} {'领先' if verb == 'leads' else '胜出'}"
        return str((event or {}).get("series_summary") or "").strip().upper().replace("SERIES ", "")

    @staticmethod
    def _mlb_probables_label(event, full=False):
        event = event or {}
        away = str(event.get("probable_a") or "").strip() or "TBD"
        home = str(event.get("probable_b") or "").strip() or "TBD"
        if away == "TBD" and home == "TBD":
            return ""
        teams = [SportsDashboard._mlb_display_team_from_event(event, side, full=full) for side in ("a", "b")]
        return f"{away} ({teams[0]})  vs  {home} ({teams[1]})"

    @staticmethod
    def _mlb_footer_label(event, state):
        event = event or {}
        parts = []
        game = str(event.get("series_game") or "").strip()
        if game:
            parts.append(game)
        venue = str(event.get("venue") or "").strip()
        if venue:
            parts.append(venue)
        broadcast = str(event.get("broadcast") or "").strip()
        if state == "final" and broadcast:
            parts.append(f"TV {broadcast}")
        return "  ·  ".join(parts)

    @staticmethod
    def _mlb_state_labels(event, now):
        def live_labels(live_event):
            label = SportsDashboard._mlb_inning_label(live_event)
            outs = SportsDashboard._lpl_int_value(live_event.get("outs"))
            bottom = f"{outs} OUT" if outs is not None and SportsDashboard._mlb_batting_side(live_event) else ""
            return label, bottom

        def final_bottom(final_event):
            innings = len(final_event.get("innings") or [])
            scheduled = int(final_event.get("scheduled_innings") or 9)
            return f"F/{innings}" if innings > scheduled else ""

        return SportsDashboard._kit_schedule_state_labels(event, now, live_labels, "mlb_live", final_bottom)
