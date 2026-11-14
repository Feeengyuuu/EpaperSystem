"""UFC panel ("fight card").

A banner names the card (UFC 334 / UFC Fight Night, the headline bout, venue and start), the main
event faces off under it (headshots, names, records and flag icons either side, the weight class
and scheduled rounds between them, or the winner and how it ended), and the next bouts of the main
card follow one per row.
"""

from datetime import datetime

from .common import *
from . import ufc
from .icon_marks import espn_flag_url
from utils.ufc_font import ufc_font

SportsDashboard = None

WEEKDAYS_ZH = "一二三四五六日"
BANNER_HEIGHT = 38
FACE_OFF_HEIGHT = 92
HEADSHOT_WIDTH = 110


def local_label(value, now):
    """"11/14 周六 17:00" in the page's time zone."""
    moment = ufc._date(value)
    if moment is None:
        return ""
    local = moment.astimezone(now.tzinfo) if isinstance(now, datetime) and now.tzinfo else moment
    return f"{local:%m/%d} 周{WEEKDAYS_ZH[local.weekday()]} {local:%H:%M}"


class UfcRenderMixin:
    def _ufc_font(self, size, bold=False):
        """UFC Sans for Latin and digits, with the panel face for unsupported glyphs."""
        return ufc_font(size, bold, self._font(size, bold))

    def _ufc_fit(self, draw, text, max_width, size, bold=False, min_size=11):
        """Measure and fit in the actual UFC face, including its Chinese fallback."""
        text, max_width = str(text or ""), max(1, int(max_width))
        for step in range(int(size), int(min_size) - 1, -1):
            font = self._ufc_font(step, bold)
            if self._text_width(draw, text, font) <= max_width:
                return text, font
        font = self._ufc_font(min_size, bold)
        low, high, best = 0, len(text), ""
        while low <= high:
            middle = (low + high) // 2
            candidate = text[:middle].rstrip() + "..."
            if self._text_width(draw, candidate, font) <= max_width:
                best, low = candidate, middle + 1
            else:
                high = middle - 1
        return best, font

    def _draw_ufc_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        accent = COLORS["red"]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), accent, COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "UFC", card, source_state)
        event = (card or {}).get("main") or {}
        left, top, right, bottom = x1 + 12, y1 + 56, x2 - 12, y2 - 6
        if not event.get("fights"):
            self._draw_centered_in_box(draw, (left, top, right, bottom), "暂无 UFC 赛事", self._ufc_font(14, True), COLORS["muted"])
            return
        self._draw_ufc_banner(image, draw, (left, top, right, top + BANNER_HEIGHT), event, card.get("status"), now, accent)
        face = (left, top + BANNER_HEIGHT + 5, right, top + BANNER_HEIGHT + 5 + FACE_OFF_HEIGHT)
        self._draw_ufc_face_off(image, draw, face, event["fights"][0], accent)
        rows_top = face[3] + 5
        rows = event["fights"][1:]
        row_h = 20
        for index, fight in enumerate(rows):
            y = rows_top + index * (row_h + 2)
            if y + row_h > bottom:
                break
            self._draw_ufc_row(image, draw, (left, y, right, y + row_h), fight, now)

    def _draw_ufc_banner(self, image, draw, box, event, status, now, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        status = str(status or "NEXT").upper()
        pill = {"LIVE": "LIVE", "RECENT": "FINAL"}.get(status) or local_label(event.get("main_start"), now).split(" ")[0]
        pill, pill_font = self._ufc_fit(draw, pill, 90, 11, bold=True, min_size=8)
        pill_w = self._text_width(draw, pill, pill_font) + 14
        px1 = x2 - 8 - pill_w
        fill = accent if status == "LIVE" else (COLORS["text"] if status == "RECENT" else COLORS["panel_gold"])
        ink = (255, 255, 255) if status == "LIVE" else (COLORS["paper_text"] if status == "RECENT" else COLORS["text"])
        draw.rounded_rectangle((px1, y1 + 6, x2 - 8, y1 + 22), radius=4, fill=fill, outline=COLORS["border"], width=1)
        self._draw_centered_in_box(draw, (px1, y1 + 6, x2 - 8, y1 + 22), pill, pill_font, ink)
        label = str(event.get("label") or "UFC")
        label_font = self._ufc_font(15, True)
        self._draw_text_in_box(draw, (x1 + 12, y1 + 3, px1 - 8, y1 + 22), label, label_font, accent)
        headline_x = x1 + 12 + self._text_width(draw, label, label_font) + 8
        headline, headline_font = self._ufc_fit(draw, str(event.get("headline") or ""), px1 - headline_x - 10, 14,
                                                          bold=True, min_size=10)
        self._draw_text_in_box(draw, (headline_x, y1 + 3, px1 - 8, y1 + 22), headline, headline_font, COLORS["text"])
        # Arena branding replaces only the venue name; keep the city and start legible.
        start = local_label(event.get("start"), now)
        broadcast = next((fight.get("broadcast") for fight in event.get("fights") or [] if fight.get("broadcast")), "")
        end = x2 - 10
        if broadcast and self._draw_kit_small_broadcast(image, (x2 - 110, y1 + 23, x2 - 10, y1 + 36), broadcast, 12,
                                                        align="right"):
            end = x2 - 116
        sub_font = self._ufc_font(9, False)
        x = x1 + 12
        start_label = f"{start} 开打" if start else ""
        time_w = self._text_width(draw, start_label, sub_font) + 12 if start else 0
        venue_end = max(x, end - time_w - 12) if start else end
        venue = "  ·  ".join(part for part in (event.get("venue"), event.get("city")) if part)
        logo = self._kit_venue_logo(event.get("venue"), (min(105, max(0, venue_end - x)), 14)) if venue_end > x else None
        if logo is not None:
            image.paste(logo, (x, y1 + 23 + (13 - logo.height) // 2), logo)
            x += logo.width + 6
            venue = str(event.get("city") or "")
        if venue:
            if logo is not None:
                city, font = self._ufc_fit(draw, venue, max(0, venue_end - x), 9, bold=False, min_size=8)
                self._draw_text_in_box(draw, (x, y1 + 23, venue_end, y1 + 36), city, font, COLORS["text"])
            else:
                self._draw_icon_text(draw, "VENUE", (x, y1 + 23, venue_end, y1 + 36), venue, sub_font,
                                     COLORS["text"], accent=accent)
        if start:
            self._draw_icon_text(draw, "CLOCK", (end - time_w, y1 + 23, end, y1 + 36), start_label, sub_font, COLORS["text"],
                                 accent=accent)

    def _draw_ufc_face_off(self, image, draw, box, fight, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        middle = (x1 + x2) // 2
        centre = (middle - 42, y1 + 4, middle + 42, y2 - 4)
        decided = fight.get("state") == "post"
        for side, fighter in enumerate(fight.get("fighters") or []):
            self._draw_ufc_fighter(image, draw, (x1 + 4, y1 + 3, centre[0], y2 - 3) if side == 0
                                   else (centre[2], y1 + 3, x2 - 4, y2 - 3), fighter, side, decided)
        weight = fight.get("weight_zh") or fight.get("weight") or ""
        weight, weight_font = self._ufc_fit(draw, weight, centre[2] - centre[0] - 4, 14, bold=True, min_size=11)
        self._draw_centered_in_box(draw, (centre[0], y1 + 8, centre[2], y1 + 26), weight, weight_font, COLORS["text"])
        if decided or fight.get("state") == "in":
            result = ufc.result_label(fight)
            text, font = self._ufc_fit(draw, result, centre[2] - centre[0] - 8, 20, bold=True, min_size=12)
            self._draw_centered_in_box(draw, (centre[0], y1 + 28, centre[2], y1 + 58), text, font, accent)
        else:
            self._draw_centered_in_box(draw, (centre[0], y1 + 26, centre[2], y1 + 60), "VS", self._ufc_font(28, True), accent)
        rounds = f"{fight.get('rounds') or 3} 回合 · 主赛"
        self._draw_centered_in_box(draw, (centre[0], y2 - 24, centre[2], y2 - 6), rounds, self._ufc_font(11, False), COLORS["text"])

    def _draw_ufc_fighter(self, image, draw, box, fighter, side, decided):
        """Full-height portraits flank centred names and records, leaving the bout result clear."""
        x1, y1, x2, y2 = [int(value) for value in box]
        height = y2 - y1
        shot_x = x1 if side == 0 else x2 - HEADSHOT_WIDTH
        headshot = self._load_team_logo_for_render(fighter.get("headshot"), height * 2) if fighter.get("headshot") else None
        if headshot is not None:
            # ESPN cut-outs are wider than tall. Fill the height and trim shoulders,
            # keeping the top of the head instead of shrinking the portrait to a square.
            portrait = ImageOps.fit(headshot, (HEADSHOT_WIDTH, height), Image.LANCZOS, centering=(0.5, 0.0))
            image.paste(portrait, (shot_x, y1), portrait)
            portrait.close()
        text_x1, text_x2 = (shot_x + HEADSHOT_WIDTH + 6, x2) if side == 0 else (x1, shot_x - 6)
        text_width = text_x2 - text_x1
        won = decided and fighter.get("winner")
        lost = decided and not fighter.get("winner")
        name = str(fighter.get("name") or "TBD")
        lines = [name]
        words = name.split()
        name_font = self._ufc_font(17, True)
        if len(words) > 1 and self._text_width(draw, name, name_font) > text_width:
            split = min(range(1, len(words)), key=lambda index: max(
                self._text_width(draw, " ".join(words[:index]), name_font),
                self._text_width(draw, " ".join(words[index:]), name_font)))
            lines = [" ".join(words[:split]), " ".join(words[split:])]
        name_top = y1 + (5 if len(lines) == 2 else 15)
        for index, line in enumerate(lines):
            label, font = self._ufc_fit(draw, line, text_width, 17, bold=True, min_size=13)
            top = name_top + index * 20
            self._draw_centered_in_box(draw, (text_x1, top, text_x2, top + 22), label, font,
                                       COLORS["muted"] if lost else COLORS["text"])
        record = fighter.get("record") or ""
        flag_w, flag_h, gap = 26, 18, 6
        record, record_font = self._ufc_fit(draw, record, text_width - flag_w - gap, 14, bold=True, min_size=10)
        record_w = self._text_width(draw, record, record_font)
        group_x = text_x1 + (text_width - flag_w - gap - record_w) // 2
        flag_x = group_x if side == 0 else group_x + record_w + gap
        self._draw_flag_icon(image, draw, fighter.get("flag") or espn_flag_url(fighter.get("country")),
                             (flag_x, y1 + 48, flag_x + flag_w, y1 + 48 + flag_h), fighter.get("country"))
        record_x = flag_x + flag_w + gap if side == 0 else group_x
        self._draw_text_in_box(draw, (record_x, y1 + 46, record_x + record_w, y1 + 68), record, record_font, COLORS["text"])
        if won:
            badge_w = 32
            bx1 = (text_x1 + text_x2 - badge_w) // 2
            draw.rounded_rectangle((bx1, y1 + 70, bx1 + badge_w, y1 + 84), radius=4, fill=COLORS["green"],
                                   outline=COLORS["border"], width=1)
            self._draw_centered_in_box(draw, (bx1, y1 + 70, bx1 + badge_w, y1 + 84), "胜", self._ufc_font(11, True), (255, 255, 255))

    def _draw_ufc_row(self, image, draw, box, fight, now):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=4, fill=COLORS["panel"], outline=COLORS["line"], width=1)
        weight = fight.get("weight_zh") or fight.get("weight") or ""
        chip_w = 66
        draw.rounded_rectangle((x1 + 3, y1 + 3, x1 + chip_w, y2 - 3), radius=3, fill=COLORS["panel_gold"],
                               outline=COLORS["border"], width=1)
        weight, weight_font = self._ufc_fit(draw, weight, chip_w - 8, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 3, y1 + 3, x1 + chip_w, y2 - 3), weight, weight_font, COLORS["text"])
        result = ufc.result_label(fight)
        result_font = self._ufc_font(10, True)
        result_w = 64
        if result:
            self._draw_text_in_box(draw, (x2 - result_w - 6, y1, x2 - 6, y2), result, result_font,
                                   COLORS["red"] if fight.get("state") == "in" else COLORS["text"], align="right")
        else:
            clock = local_label(fight.get("date"), now).split(" ")[-1]
            self._draw_icon_text(draw, "CLOCK", (x2 - result_w + 14, y1, x2 - 6, y2), clock, result_font, COLORS["text"])
        middle = (x1 + chip_w + x2 - result_w) // 2
        decided = fight.get("state") == "post"
        self._draw_centered_in_box(draw, (middle - 12, y1, middle + 12, y2), "vs", self._ufc_font(9, False), COLORS["muted"])
        for side, fighter in enumerate(fight.get("fighters") or []):
            area = (x1 + chip_w + 6, middle - 14) if side == 0 else (middle + 14, x2 - result_w - 10)
            flag_box = (area[0], y1 + 5, area[0] + 15, y2 - 5) if side == 0 else (area[1] - 15, y1 + 5, area[1], y2 - 5)
            self._draw_flag_icon(image, draw, fighter.get("flag"), flag_box, fighter.get("country"))
            text = fighter.get("name") or "TBD"
            if fighter.get("record"):
                text = f"{text} ({fighter['record']})"
            text_box = (flag_box[2] + 4, y1, area[1], y2) if side == 0 else (area[0], y1, flag_box[0] - 4, y2)
            text, font = self._ufc_fit(draw, text, text_box[2] - text_box[0], 10,
                                                 bold=bool(decided and fighter.get("winner")), min_size=8)
            colour = COLORS["muted"] if decided and not fighter.get("winner") else COLORS["text"]
            self._draw_text_in_box(draw, text_box, text, font, colour, align="left" if side == 0 else "right")
