"""PGA TOUR panel ("leaderboard board").

A tournament banner (event, course, dates, round status) over a two-column
leaderboard of the top eight: position badge, player, round-by-round scores
and the total to par, under par in red as on a course scoreboard. Before the
first tee time the board shows tee-off / dates / course tiles and the next
events on tour.
"""

from .common import *
from .scorebug_kit import ScorebugSpec

SportsDashboard = None

PGA_BANNER_HEIGHT = 42
PGA_ROWS_PER_COLUMN = 4


class GolfRenderMixin:
    def _draw_pga_standalone_panel(self, image, draw, bounds, card, source_state, now):
        spec = ScorebugSpec("PGA", "pga_accent", "pga_live", "pga_tag", "pga_course_tint")
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS[spec.accent], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "PGA", card, source_state)
        card = card or {}
        event = card.get("main") or {}
        left, top, right, bottom = x1 + 12, y1 + 57, x2 - 12, y2 - 7
        if not event:
            self._draw_kit_empty_card(draw, (left, top, right, bottom), spec, "No PGA TOUR event")
            return
        state = SportsDashboard._hub_event_state(event)
        self._draw_pga_banner(image, draw, (left, top, right, top + PGA_BANNER_HEIGHT), event, state, now, spec)
        board = (left, top + PGA_BANNER_HEIGHT + 6, right, bottom)
        rows = list(event.get("leaderboard") or [])
        if rows:
            self._draw_pga_board(draw, board, rows, state, spec)
        else:
            self._draw_pga_preview(draw, board, event, card.get("calendar") or [], now, spec)

    def _draw_pga_banner(self, image, draw, box, event, state, now, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, COLORS[spec.live] if state == "live" else COLORS[spec.accent])
        pill = SportsDashboard._pga_pill_label(event, state, now)
        pill, pill_font = self._fit_text(draw, pill, 150, 11, bold=True, min_size=8)
        marker = 9 if state == "live" else 0
        pill_w = self._text_width(draw, pill, pill_font) + 14 + marker
        px1 = x2 - 8 - pill_w
        fill = COLORS["text"] if state == "final" else (COLORS["panel"] if state == "live" else COLORS[spec.tag])
        draw.rounded_rectangle((px1, y1 + 6, x2 - 8, y1 + 22), radius=4, fill=fill, outline=COLORS["border"], width=1)
        if marker:
            draw.rectangle((px1 + 5, y1 + 10, px1 + 10, y1 + 18), fill=COLORS[spec.live], outline=COLORS["border"], width=1)
        self._draw_centered_in_box(draw, (px1 + marker, y1 + 6, x2 - 8, y1 + 22), pill, pill_font, COLORS["paper_text"] if state == "final" else COLORS["text"])
        mark = self._draw_event_mark(image, SportsDashboard._pga_event_mark_path(event), (x1 + 14, y1 + 3, x1 + 78, y2 - 3), align="center")
        text_x1 = mark[2] + 8 if mark else x1 + 16
        name = str(event.get("name") or event.get("name_en") or "PGA TOUR").strip()
        name, name_font = self._fit_text(draw, name, px1 - text_x1 - 10, 16, bold=True, min_size=10)
        self._draw_text_in_box(draw, (text_x1, y1 + 3, px1 - 8, y1 + 24), name, name_font, COLORS["text"])
        sub = "  ·  ".join(part for part in (str(event.get("venue") or "").strip(), SportsDashboard._pga_dates_label(event)) if part)
        if sub:
            sub, sub_font = self._fit_text_ellipsis(draw, sub, x2 - text_x1 - 14, 9, bold=False, min_size=8)
            self._draw_text_in_box(draw, (text_x1, y1 + 25, x2 - 10, y1 + 37), sub, sub_font, COLORS["text"])

    def _draw_pga_board(self, draw, box, rows, state, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        gap = 8
        column_w = (x2 - x1 - gap) // 2
        row_h = (y2 - y1 - 3 * 4) // PGA_ROWS_PER_COLUMN
        for index, row in enumerate(rows[: PGA_ROWS_PER_COLUMN * 2]):
            column, slot = divmod(index, PGA_ROWS_PER_COLUMN)
            left = x1 + column * (column_w + gap)
            top = y1 + slot * (row_h + 4)
            self._draw_pga_board_row(draw, (left, top, left + column_w, top + row_h), row, index == 0, state, spec)

    def _draw_pga_board_row(self, draw, box, row, leader, state, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=2 if leader else 1)
        badge = (x1 + 5, y1 + 5, x1 + 37, y1 + 21)
        draw.rounded_rectangle(badge, radius=3, fill=COLORS["amber"] if leader else COLORS[spec.tag], outline=COLORS["border"], width=1)
        position = str(row.get("position_label") or row.get("position") or "-")
        self._draw_centered_in_box(draw, badge, position, self._font(11 if len(position) < 3 else 9, True), COLORS["text"])
        country = str(row.get("country") or "").strip().upper()[:3]
        if country:
            self._draw_centered_in_box(draw, (badge[0], y2 - 13, badge[2], y2 - 2), country, self._font(8, False), COLORS["text"])
        score = str(row.get("score") or "E").strip() or "E"
        score_font = self._font(18 if leader else 16, True)
        score_w = self._text_width(draw, score, score_font)
        under_par = score.startswith("-")
        self._draw_text_in_box(draw, (x2 - 6 - score_w, y1, x2 - 6, y2), score, score_font, COLORS[spec.live] if under_par else COLORS["text"], align="right")
        name_x1, name_x2 = x1 + 43, x2 - score_w - 12
        name, name_font = self._fit_text_ellipsis(draw, str(row.get("name") or "Player"), name_x2 - name_x1, 12, bold=True, min_size=9)
        self._draw_text_in_box(draw, (name_x1, y1 + 3, name_x2, y1 + 19), name, name_font, COLORS["text"])
        detail = SportsDashboard._pga_row_rounds_label(row, state)
        if detail:
            detail, detail_font = self._fit_text_ellipsis(draw, detail, name_x2 - name_x1, 9, bold=False, min_size=7)
            self._draw_text_in_box(draw, (name_x1, y1 + 20, name_x2, y2 - 3), detail, detail_font, COLORS["text"])

    def _draw_pga_preview(self, draw, box, event, calendar, now, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        tiles = [
            ("TEE OFF", SportsDashboard._kit_countdown_label(event.get("start"), now)),
            ("DATES", SportsDashboard._pga_dates_label(event) or "TBD"),
            ("ROUNDS", "4 × 18"),
        ]
        y = self._draw_kit_tiles(draw, (x1, y1, x2, y2), tiles, spec, tile_h=40) + 8
        y = self._draw_kit_column_header(draw, x1, y, x2, spec, "ON TOUR")
        for item in calendar:
            if y + 16 > y2:
                break
            name = str(item.get("name") or item.get("name_en") or "").strip()
            dates = SportsDashboard._pga_dates_label(item)
            dates_font = self._font(9, True)
            dates_w = self._text_width(draw, dates, dates_font)
            self._draw_text_in_box(draw, (x1 + 2, y, x1 + 2 + dates_w, y + 15), dates, dates_font, COLORS["text"])
            name, name_font = self._fit_text_ellipsis(draw, name, x2 - x1 - dates_w - 16, 10, bold=False, min_size=8)
            self._draw_text_in_box(draw, (x1 + dates_w + 14, y, x2, y + 15), name, name_font, COLORS["text"])
            y += 17

    # Labels ------------------------------------------------------------------

    @staticmethod
    def _pga_pill_label(event, state, now):
        event = event or {}
        leader = event.get("leader") or {}
        round_number = SportsDashboard._lpl_int_value(leader.get("round"))
        if state == "live":
            return f"ROUND {round_number}" if round_number else "LIVE"
        if state == "final":
            return "FINAL"
        start = event.get("start")
        if not isinstance(start, datetime):
            return "NEXT"
        day = start.astimezone(timezone.utc)  # tournament day, see _pga_dates_label
        return f"{day.strftime('%a').upper()} {day.strftime('%m/%d')}"

    @staticmethod
    def _pga_dates_label(event):
        """'10/08–10/11'. ESPN stamps golf days at 04:00Z (midnight Eastern),
        so the UTC date is the tournament day."""
        event = event or {}
        start, end = event.get("start"), event.get("end")
        first = start.astimezone(timezone.utc).date() if isinstance(start, datetime) else None
        last = end.astimezone(timezone.utc).date() if isinstance(end, datetime) else None
        if first and last and last != first:
            return f"{first.strftime('%m/%d')}–{last.strftime('%m/%d')}"
        return first.strftime("%m/%d") if first else ""

    @staticmethod
    def _pga_row_rounds_label(row, state):
        row = row or {}
        rounds = [value for value in row.get("rounds") or [] if value]
        thru = str(row.get("thru") or "").strip()
        if state == "live" and thru and thru != "F":
            today = str(row.get("today") or "").strip()
            return "  ".join(part for part in (f"THRU {thru}", f"TODAY {today}" if today else "") if part)
        if rounds:
            return "  ".join(f"R{index + 1} {value}" for index, value in enumerate(rounds))
        return ""
