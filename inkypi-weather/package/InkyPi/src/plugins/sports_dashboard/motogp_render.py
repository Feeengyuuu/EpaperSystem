"""MotoGP panel ("race weekend").

A banner names the Grand Prix (in Chinese with its sponsored name), its circuit and dates with the
host country's flag. Under it, three columns: the weekend's sessions with their local times (a
tick when run, a red dot while running), the latest race or sprint result as medal discs, flag
icons, rider numbers, names and gaps, and the championship standings with points.
"""

from datetime import datetime

from .common import *
from . import motogp
from .card_backdrops import card_backdrop_box, paint_card_backdrop
from .icon_marks import iso2_flag_url

SportsDashboard = None

WEEKDAYS_ZH = "一二三四五六日"
BANNER_HEIGHT = 38
COLUMN_GAP = 8
ROW_H = 19
RESULT_NAMES = {"RAC": "正赛成绩", "SPR": "冲刺赛成绩"}
# Each manufacturer's official mark (assets/logos), shown beside its riders.
MAKER_MARKS = {"ducati": "motogp_ducati", "ktm": "motogp_ktm", "aprilia": "motogp_aprilia", "honda": "motogp_honda",
               "yamaha": "motogp_yamaha"}


def maker_mark(constructor):
    text = str(constructor or "").lower()
    return next((key for name, key in MAKER_MARKS.items() if name in text), None)


def _local(value, now):
    moment = motogp._when(value)
    if moment is None:
        return None
    return moment.astimezone(now.tzinfo) if isinstance(now, datetime) and now.tzinfo else moment


def surname(name):
    parts = str(name or "").split()
    return parts[-1] if parts else ""


class MotoGPRenderMixin:
    def _draw_motogp_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        accent = COLORS["orange"]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), accent, COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "MOTOGP", card, source_state)
        left, top, right, bottom = x1 + 12, y1 + 56, x2 - 12, y2 - 6
        weekend = (card or {}).get("weekend")
        if not weekend:
            self._draw_centered_in_box(draw, (left, top, right, bottom), "暂无 MotoGP 大奖赛", self._font(14, True), COLORS["muted"])
            return
        self._draw_motogp_banner(image, draw, (left, top, right, top + BANNER_HEIGHT), weekend, card.get("status"), now, accent)
        body_top = top + BANNER_HEIGHT + 6
        widths = (138, 0, 170)
        middle_w = right - left - widths[0] - widths[2] - 2 * COLUMN_GAP
        sessions_box = (left, body_top, left + widths[0], bottom)
        result_box = (sessions_box[2] + COLUMN_GAP, body_top, sessions_box[2] + COLUMN_GAP + middle_w, bottom)
        standings_box = (result_box[2] + COLUMN_GAP, body_top, right, bottom)
        self._draw_motogp_sessions(draw, sessions_box, card, now, accent)
        self._draw_motogp_result(image, draw, result_box, card.get("result") or {}, accent)
        self._draw_motogp_standings(image, draw, standings_box, card.get("standings") or [], accent)

    def _draw_motogp_banner(self, image, draw, box, weekend, status, now, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        paint_card_backdrop(image, card_backdrop_box(x1, y1, x2, y2), "motorsport", COLORS["panel"])
        status = str(status or "NEXT").upper()
        start, end = motogp._day(weekend.get("start")), motogp._day(weekend.get("end"))
        dates = f"{start:%m/%d}–{end:%m/%d}" if start and end and end != start else (f"{start:%m/%d}" if start else "")
        pill = {"LIVE": "LIVE"}.get(status) or ("本周" if status == "RECENT" else dates.split("–")[0] or "NEXT")
        pill_font = self._font(11, True)
        pill_w = self._text_width(draw, pill, pill_font) + 14
        px1 = x2 - 8 - pill_w
        draw.rounded_rectangle((px1, y1 + 6, x2 - 8, y1 + 22), radius=4, fill=COLORS["red"] if status == "LIVE" else COLORS["panel_gold"],
                               outline=COLORS["border"], width=1)
        self._draw_centered_in_box(draw, (px1, y1 + 6, x2 - 8, y1 + 22), pill, pill_font,
                                   (255, 255, 255) if status == "LIVE" else COLORS["text"])
        flag = self._draw_flag_icon(image, draw, iso2_flag_url(weekend.get("iso")), (x1 + 10, y1 + 7, x1 + 40, y1 + 27),
                                    weekend.get("iso"))
        text_x = (flag[2] + 8) if flag else x1 + 12
        title = weekend.get("name_zh") or weekend.get("name") or "MotoGP"
        title_font = self._font(15, True)
        self._draw_text_in_box(draw, (text_x, y1 + 3, px1 - 8, y1 + 23), title, title_font, COLORS["text"])
        sponsor_x = text_x + self._text_width(draw, title, title_font) + 8
        sponsor, sponsor_font = self._fit_text_ellipsis(draw, str(weekend.get("sponsored") or ""), px1 - sponsor_x - 10, 9,
                                                        bold=False, min_size=7)
        self._draw_text_in_box(draw, (sponsor_x, y1 + 6, px1 - 8, y1 + 21), sponsor, sponsor_font, COLORS["muted"])
        sub_font = self._font(9, False)
        x = text_x
        if weekend.get("circuit"):
            x = self._draw_icon_text(draw, "VENUE", (x, y1 + 23, x2 - 90, y1 + 36), weekend["circuit"], sub_font,
                                     COLORS["text"], accent=accent) + 12
        if dates:
            self._draw_icon_text(draw, "CLOCK", (x, y1 + 23, x2 - 10, y1 + 36), dates, sub_font, COLORS["text"], accent=accent)

    def _draw_motogp_column_head(self, draw, box, title, accent):
        x1, y1, x2, _y2 = [int(value) for value in box]
        draw.rectangle((x1, y1 + 3, x1 + 5, y1 + 15), fill=accent, outline=COLORS["border"], width=1)
        title, font = self._fit_text_ellipsis(draw, title, x2 - x1 - 10, 11, bold=True, min_size=8)
        self._draw_text_in_box(draw, (x1 + 9, y1, x2, y1 + 18), title, font, COLORS["text"])
        draw.line((x1, y1 + 19, x2, y1 + 19), fill=COLORS["border"], width=1)
        return y1 + 22

    def _draw_motogp_sessions(self, draw, box, card, now, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        y = self._draw_motogp_column_head(draw, box, "赛程", accent)
        live = (card.get("live_session") or {}).get("id")
        upcoming = (card.get("next_session") or {}).get("id")
        # The warm-up is left out: the race keeps its row in the column.
        for session in [item for item in card.get("sessions") or [] if item.get("type") != "WUP"]:
            if y + ROW_H > y2:
                break
            local = _local(session.get("start"), now)
            is_live, is_next = session.get("id") == live, session.get("id") == upcoming
            done = not is_live and (session.get("status") == "FINISHED"
                                    or (local is not None and local + motogp._length(session) < now))
            mark = (x1 + 2, y + 5, x1 + 10, y + 13)
            if is_live:
                draw.ellipse(mark, fill=COLORS["red"], outline=COLORS["border"], width=1)
            elif done:
                draw.line((mark[0], mark[1] + 4, mark[0] + 3, mark[3]), fill=COLORS["green"], width=2)
                draw.line((mark[0] + 3, mark[3], mark[2], mark[1]), fill=COLORS["green"], width=2)
            else:
                draw.ellipse(mark, outline=COLORS["border"], width=1)
            self._draw_session_glyph(draw, session.get("type"), x1 + 15, y + 4, accent)
            label_font = self._font(10, is_live or is_next or session.get("type") in ("RAC", "SPR"))
            self._draw_text_in_box(draw, (x1 + 30, y, x1 + 72, y + ROW_H), session.get("label") or "", label_font,
                                   COLORS["muted"] if done else COLORS["text"])
            when = f"周{WEEKDAYS_ZH[local.weekday()]} {local:%H:%M}" if local else ""
            self._draw_text_in_box(draw, (x1 + 70, y, x2 - 2, y + ROW_H), when, self._font(10, is_next or is_live),
                                   COLORS["red"] if is_live else (COLORS["text"] if not done else COLORS["muted"]), align="right")
            y += ROW_H

    def _draw_motogp_rider_row(self, image, draw, box, row, value, *, place=None):
        x1, y1, x2, y2 = [int(value) for value in box]
        disc = y2 - y1 - 2
        self._draw_rank_medal(draw, (x1, y1 + 1, x1 + disc, y1 + 1 + disc), place if place is not None else row.get("pos"))
        flag_x = x1 + disc + 4
        self._draw_flag_icon(image, draw, iso2_flag_url(row.get("iso")), (flag_x, y1 + 3, flag_x + 18, y2 - 3), row.get("iso"))
        # The rider's manufacturer by its official mark; the race number only without one.
        number_x = flag_x + 22
        key = maker_mark(row.get("constructor"))
        mark = self._load_local_logo(os.path.join(LOCAL_TEAM_LOGO_DIR, f"{key}.png"), (26, y2 - y1 - 6),
                                     alpha_threshold=8) if key else None
        if mark:
            mark = SportsDashboard._night_inked_mark(mark)
            image.paste(mark, (number_x, y1 + (y2 - y1 - mark.height) // 2), mark)
            number_w = mark.width
        else:
            number = f"#{row['number']}" if row.get("number") is not None else ""
            number_font = self._font(9, True)
            number_w = self._text_width(draw, number, number_font) if number else 0
            self._draw_text_in_box(draw, (number_x, y1, number_x + number_w, y2), number, number_font, COLORS["muted"])
        value_font = self._font(10, True)
        value_w = self._text_width(draw, value, value_font) if value else 0
        self._draw_text_in_box(draw, (x2 - value_w - 2, y1, x2 - 2, y2), value, value_font, COLORS["text"], align="right")
        name_x = number_x + number_w + 4
        name, name_font = self._fit_text_ellipsis(draw, row.get("name") or "", x2 - value_w - name_x - 6, 10, bold=True, min_size=8)
        if self._text_width(draw, name, name_font) > x2 - value_w - name_x - 6 or name.endswith("…"):
            name, name_font = self._fit_text_ellipsis(draw, surname(row.get("name")), x2 - value_w - name_x - 6, 10, bold=True, min_size=8)
        self._draw_text_in_box(draw, (name_x, y1, x2 - value_w - 4, y2), name, name_font, COLORS["text"])

    def _draw_motogp_result(self, image, draw, box, result, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        rows = result.get("rows") or []
        place = result.get("event_zh") or result.get("event") or ""
        title = f"{RESULT_NAMES.get(result.get('session'), '最新成绩')}" + (f" · {place}" if place else "")
        y = self._draw_motogp_column_head(draw, box, title, accent)
        if not rows:
            self._draw_centered_in_box(draw, (x1, y, x2, y2), "尚无成绩", self._font(11, False), COLORS["muted"])
            return
        for row in rows:
            if y + ROW_H > y2:
                break
            value = row.get("gap") or row.get("time") or ""
            self._draw_motogp_rider_row(image, draw, (x1, y, x2, y + ROW_H), row, value)
            y += ROW_H + 1

    def _draw_motogp_standings(self, image, draw, box, standings, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        y = self._draw_motogp_column_head(draw, box, "车手积分榜", accent)
        for row in standings:
            if y + ROW_H > y2:
                break
            points = row.get("points")
            value = f"{int(points)}" if isinstance(points, (int, float)) else ""
            self._draw_motogp_rider_row(image, draw, (x1, y, x2, y + ROW_H), row, value)
            y += ROW_H + 1
