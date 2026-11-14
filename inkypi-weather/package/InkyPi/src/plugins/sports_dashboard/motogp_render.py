"""MotoGP panel ("race weekend").

One card holds the weekend: a title line with the host country's flag, the Grand Prix in Chinese
(its sponsored name when there is room), the circuit and the dates; under it, the weekend's sessions
on a line like F1's timeline (a node each: done, running, next or to come; the session above it and
its local start under it, a finished race's winner in its place). Then two columns, the latest race or sprint result and the championship
standings, a row per rider: place, the rider's official face, flag, the manufacturer's mark, race
number and surname, then the gap (result) or race wins and points over a bar of their points in the
team's colour (standings). Riders' names and every figure are set in MotoGP's own MGP Text and MGP
Display when the owner installed them (sports-motogp-*-score), Barlow otherwise.
"""

from datetime import datetime

from .common import *
from . import motogp, motogp_bikes, motogp_riders
from .icon_marks import iso2_flag_url
from .scorebug_kit import _kit_inked_where_faint

SportsDashboard = None

WEEKDAYS_ZH = "一二三四五六日"
# The weekend card: its title line, then the session line; the rows below it.
TITLE_ROW = 24
TIMELINE_HEIGHT = 34
CARD_HEIGHT = TITLE_ROW + TIMELINE_HEIGHT + 2
SECTION_GAP = 6
HEAD_HEIGHT = 17
ROW_PITCH = 20
FACE = 20
FLAG_W, FLAG_H = 16, 11
MARK_SLOT = (24, 11)
RESULT_NAMES = {"RAC": "正赛成绩", "SPR": "冲刺赛成绩"}
# Each manufacturer's official mark (assets/logos), shown beside its riders.
MAKER_MARKS = {"ducati": "motogp_ducati", "ktm": "motogp_ktm", "aprilia": "motogp_aprilia", "honda": "motogp_honda",
               "yamaha": "motogp_yamaha"}
# A rider without their team's colour (no profile yet) takes the manufacturer's.
MAKER_COLOURS = {"ducati": (204, 0, 0), "ktm": (255, 102, 0), "aprilia": (64, 64, 64), "honda": (200, 16, 46),
                 "yamaha": (0, 61, 165)}
# The least contrast a team colour keeps against the panel before the bar takes the page's ink.
TEAM_COLOUR_CONTRAST = 1.8
# Three lanes, as on the F1 header: the front row is nearest the chequered line.
GRID_LANES = 3
GRID_SLOT_WIDTH = 40
GRID_SLOT_MIN_WIDTH = 23
GRID_SLOT_GAP = 2
GRID_NUMBER_GAP = 1


def maker_mark(constructor):
    text = str(constructor or "").lower()
    return next((key for name, key in MAKER_MARKS.items() if name in text), None)


def _local(value, now):
    moment = motogp._when(value)
    if moment is None:
        return None
    return moment.astimezone(now.tzinfo) if isinstance(now, datetime) and now.tzinfo else moment


def surname(name):
    return motogp_riders.surname(name)


def rider_surname(row, profile):
    """The rider's surname as MotoGP's timing shows it: upper case, from their profile when kept."""
    return str((profile or {}).get("surname") or surname(row.get("name"))).upper()


def _luminance(rgb):
    channels = [value / 255 for value in rgb[:3]]
    linear = [value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def team_colour(row, profile):
    """The rider's team colour (their profile's), else their manufacturer's; the page's ink where
    that colour would vanish into the panel (a black livery at night)."""
    colour = None
    value = str((profile or {}).get("color") or "")
    if len(value) == 7 and value.startswith("#"):
        try:
            colour = tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
        except ValueError:
            colour = None
    if colour is None:
        key = next((name for name in MAKER_COLOURS if name in str(row.get("constructor") or "").lower()), None)
        colour = MAKER_COLOURS.get(key)
    if colour is None:
        return COLORS["text"]
    high, low = sorted((_luminance(colour), _luminance(COLORS["panel"])), reverse=True)
    return colour if (high + 0.05) / (low + 0.05) >= TEAM_COLOUR_CONTRAST else COLORS["text"]


class MotoGPRenderMixin:
    def _motogp_type(self, draw, text, width, size, *, display=False, min_size=7):
        """``text`` in MGP Display (figures) or MGP Text (names), fitted to ``width``; Chinese in the UI font."""
        text = str(text)
        if not text.isascii():
            return self._fit_text(draw, text, width, size, bold=True, min_size=min_size)
        return self._fit_score(draw, text, width, size, bold=True, min_size=min_size,
                               sport="MOTOGP" if display else "MOTOGP_TEXT", max_height=size + 3)

    def _draw_motogp_starting_grid(self, image, draw, box, card):
        """Official starting positions beside generated side-view team-livery schematics.

        The provider owns completeness and race identity. A missing/ambiguous grid keeps
        the normal header art; official gaps stay gaps, never renumbered to close them.
        """
        grid = (card or {}).get("grid") or {}
        weekend = (card or {}).get("weekend") or {}
        if (not isinstance(grid, dict) or grid.get("complete") is not True or grid.get("kind") != "official"
                or grid.get("target_type") != "RAC" or not grid.get("session_id")
                or not weekend.get("id") or grid.get("event_id") != weekend["id"]):
            return False
        if not any(session.get("id") == grid.get("session_id") and session.get("type") == "RAC"
                   for session in (card or {}).get("sessions") or [] if isinstance(session, dict)):
            return False
        rows = grid.get("rows") or []
        if not rows:
            return False
        placed, places, numbers = [], set(), set()
        for row in rows:
            if not isinstance(row, dict):
                return False
            number = row.get("number")
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0 or number in numbers:
                return False
            numbers.add(number)
            if row.get("pit_lane"):
                # The current provider verifies normal RAC grid places only. Do not put an
                # unverified pit-lane starter into an ordinary starting box.
                return False
            position = row.get("pos")
            if isinstance(position, bool) or not isinstance(position, int) or position <= 0 or position in places:
                return False
            places.add(position)
            placed.append(row)
        if not placed:
            return False
        x1, y1, x2, y2 = [int(value) for value in box]
        if y2 - y1 < 43:
            return False
        slots = max(places)
        line_x = x2 - 6
        available = line_x - 5 - x1
        # Consecutive grid places advance one third of a car-length. Reserve a gap
        # between places three apart, which share a lane. Size for every position.
        slot_w = min(GRID_SLOT_WIDTH, int((GRID_LANES * available - (slots - 1) * GRID_SLOT_GAP)
                                          / (slots + GRID_LANES - 1)))
        if slot_w < GRID_SLOT_MIN_WIDTH:
            return False
        event_start = motogp._when(weekend.get("start"))
        season = grid.get("season", event_start.year if event_start else None)
        if type(season) is not int or (event_start and season != event_start.year):
            season = None
        lane_h = (y2 - y1) / GRID_LANES
        bike_h = min(14, int(lane_h) - 1)
        position_font = self._font(8, True)
        number_w = max(self._text_width(draw, str(row["pos"]), position_font) for row in placed)
        step = (available - slot_w) / max(1, slots - 1)
        bikes = []
        for row in placed:
            index = row["pos"] - 1
            nose = round(line_x - 5 - index * step)
            position = str(row["pos"])
            # Reserve the complete number (including double digits) at the tail. Only
            # the remaining width belongs to the bike. Every rider gets the same box,
            # so a two-digit place cannot make only the rear bikes smaller.
            art = motogp_bikes.load_bike(row, season, (slot_w - number_w - GRID_NUMBER_GAP, bike_h), COLORS["panel"])
            if art.width < 16 or art.height < 10:
                return False
            top = round(y1 + (index % GRID_LANES) * lane_h + (lane_h - art.height) / 2)
            number_box = (nose - art.width - GRID_NUMBER_GAP - number_w, top,
                          nose - art.width - GRID_NUMBER_GAP, top + art.height)
            bikes.append((art, nose - art.width, top, nose, position, number_box))
        for row_index in range(int((y2 - y1) // 3)):
            for column in range(2):
                fill = COLORS["text"] if (row_index + column) % 2 == 0 else COLORS["panel"]
                left, tile_top = line_x + column * 3, y1 + row_index * 3
                draw.rectangle((left, tile_top, left + 2, tile_top + 2), fill=fill)
        for art, left, top, nose, position, number_box in bikes:
            # A right-facing starting bracket and the chequered line show pole direction.
            right, bottom = nose + 2, top + art.height - 1
            draw.line((right - 2, top, right, top, right, bottom, right - 2, bottom),
                      fill=COLORS["muted"], width=1)
            self._draw_text_in_box(draw, number_box, position, position_font, COLORS["text"], align="right")
            image.paste(art, (left, top), art)
        return True

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
        self._draw_motogp_banner(image, draw, (left, top, right, top + CARD_HEIGHT), weekend, accent)
        line_top = top + TITLE_ROW
        self._draw_motogp_timeline(draw, (left + 12, line_top, right - 4, line_top + TIMELINE_HEIGHT), card, now, accent)
        # The card's shadow (3 px) and a gap before the columns.
        body_top = top + CARD_HEIGHT + SECTION_GAP
        middle = (left + right) // 2
        self._draw_vertical_split(draw, middle + 2, body_top + 2, bottom)
        riders = card.get("riders") or {}
        self._draw_motogp_result(image, draw, (left, body_top, middle - 6, bottom), card.get("result") or {}, riders, accent)
        self._draw_motogp_standings(image, draw, (middle + 8, body_top, right, bottom), card.get("standings") or [], riders,
                                    accent)

    def _draw_motogp_banner(self, image, draw, box, weekend, accent):
        """The weekend card and its title line: flag, the Grand Prix in Chinese, its sponsored name when
        the line has room, and the circuit and dates at the right, every Latin word in MGP Text."""
        x1, y1, x2, y2 = [int(value) for value in box]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        middle = y1 + TITLE_ROW // 2 + 1
        flag = self._draw_flag_icon(image, draw, iso2_flag_url(weekend.get("iso")), (x1 + 14, middle - 7, x1 + 40, middle + 7),
                                    weekend.get("iso"))
        text_x = (flag[2] + 7) if flag else x1 + 14
        start, end = motogp._day(weekend.get("start")), motogp._day(weekend.get("end"))
        dates = f"{start:%m/%d}–{end:%m/%d}" if start and end and end != start else (f"{start:%m/%d}" if start else "")
        # Keep the dates at the right edge; a known circuit uses its own mark.
        x = x2 - 8
        for kind, text, width in (("CLOCK", dates, 80), ("VENUE", weekend.get("circuit") or "", 180)):
            if not text:
                continue
            logo = self._kit_venue_logo(text, (130, 18)) if kind == "VENUE" else None
            if logo is not None:
                # Lusail's bright mint wordmark needs the page ink on a light card.
                logo = _kit_inked_where_faint(logo, COLORS["panel"])
                image.paste(logo, (x - logo.width, middle - logo.height // 2), logo)
                x -= logo.width + 12
                continue
            text, font = self._motogp_type(draw, text, width, 9)
            text_w = self._text_width(draw, text, font)
            self._draw_text_in_box(draw, (x - text_w, middle - 7, x, middle + 7), text, font, COLORS["text"])
            self._draw_sport_info_icon(draw, kind, x - text_w - 12, middle - 4, accent)
            x -= text_w + 12 + 12
        title = weekend.get("name_zh") or weekend.get("name") or "MotoGP"
        title, title_font = self._fit_text_ellipsis(draw, title, max(40, x - text_x - 4), 15, bold=True, min_size=11)
        self._draw_text_in_box(draw, (text_x, middle - 11, x, middle + 10), title, title_font, COLORS["text"])
        sponsor_x = text_x + self._text_width(draw, title, title_font) + 8
        # The sponsored name only at its full size: never shrunk to fit.
        sponsor, sponsor_font = self._motogp_type(draw, str(weekend.get("sponsored") or ""), 10_000, 9)
        if sponsor and self._text_width(draw, sponsor, sponsor_font) <= x - sponsor_x - 6:
            self._draw_text_in_box(draw, (sponsor_x, middle - 7, x, middle + 7), sponsor, sponsor_font, COLORS["muted"])
        draw.line((x1 + 14, y1 + TITLE_ROW, x2 - 8, y1 + TITLE_ROW), fill=COLORS["line"], width=1)

    def _draw_motogp_timeline(self, draw, box, card, now, accent):
        """The weekend's sessions on one line: the session over its node, its local start (周五 06:45)
        under it; the node filled when run, red while running, amber for the next, a chequer for the race."""
        x1, y1, x2, _y2 = [int(value) for value in box]
        # The warm-up is left out: the race keeps its place on the line.
        sessions = [item for item in card.get("sessions") or [] if item.get("type") != "WUP"][:8]
        if not sessions:
            return
        live = (card.get("live_session") or {}).get("id")
        upcoming = (card.get("next_session") or {}).get("id")
        result = card.get("result") or {}
        weekend = card.get("weekend") or {}
        winner = ((result.get("rows") or [None])[0] if result.get("event") == weekend.get("short") else None)
        axis = y1 + 17
        margin = 24
        step = (x2 - x1 - 2 * margin) / max(1, len(sessions) - 1)
        positions = [round(x1 + margin + index * step) for index in range(len(sessions))]
        states = []
        for session in sessions:
            local = _local(session.get("start"), now)
            if session.get("id") == live:
                states.append("live")
            elif session.get("status") == "FINISHED" or (local is not None and local + motogp._length(session) < now):
                states.append("done")
            else:
                states.append("next" if session.get("id") == upcoming else "later")
        for index in range(len(sessions) - 1):
            run = states[index + 1] in ("done", "live")
            draw.line((positions[index], axis, positions[index + 1], axis), fill=COLORS["border"] if run else COLORS["line"],
                      width=3 if run else 1)
        half = int(step / 2) - 1
        for position, session, state in zip(positions, sessions, states):
            emphasis = state in ("live", "next") or session.get("type") in ("RAC", "SPR")
            label, label_font = self._motogp_type(draw, session.get("label") or "", 2 * half, 9, display=True)
            if not emphasis and not label.isascii():
                label_font = self._font(9, False)
            self._draw_centered_in_box(draw, (position - half, y1 + 2, position + half, y1 + 12), label, label_font,
                                       COLORS["red"] if state == "live" else COLORS["text"])
            self._draw_motogp_node(draw, position, axis, state, session.get("type") == "RAC")
            if state == "done" and winner and session.get("type") == result.get("session"):
                # A finished race or sprint shows its winner where its start time was.
                name, font = self._motogp_type(draw, rider_surname(winner, (card.get("riders") or {}).get(winner.get("uuid"))),
                                               2 * half, 9)
                self._draw_centered_in_box(draw, (position - half, axis + 7, position + half, axis + 17), name, font,
                                           COLORS["text"])
                continue
            local = _local(session.get("start"), now)
            if local is not None:
                self._draw_motogp_when(draw, (position - half, axis + 7, position + half, axis + 17), local,
                                       COLORS["red"] if state == "live" else COLORS["text"] if state != "done" else COLORS["muted"])

    def _draw_motogp_node(self, draw, x, y, state, is_race):
        """A session's node, kept inside the line's band: a running one edged in red, the next one
        amber with a heavier edge."""
        radius = 5 if is_race else 4
        box = (x - radius, y - radius, x + radius, y + radius)
        edge = (COLORS["red"], 2) if state == "live" else (COLORS["border"], 2 if state == "next" else 1)
        if is_race and state != "done":
            draw.rectangle(box, fill=COLORS["panel"])
            for row in range(2):
                for column in range(2):
                    if (row + column) % 2 == 0:
                        draw.rectangle((x - radius + column * radius, y - radius + row * radius,
                                        x + column * radius, y + row * radius), fill=COLORS["text"])
            draw.rectangle(box, outline=edge[0], width=edge[1])
            return
        fill = {"done": COLORS["text"], "live": COLORS["red"], "next": COLORS["amber"]}.get(state, COLORS["panel"])
        draw.ellipse(box, fill=fill, outline=edge[0], width=edge[1])

    def _draw_motogp_when(self, draw, box, local, colour):
        """周五 06:45 centred in ``box``: the weekday in the UI font, the time in MGP Text."""
        x1, y1, x2, y2 = box
        day = f"周{WEEKDAYS_ZH[local.weekday()]}"
        day_font = self._font(9, False)
        clock, clock_font = self._motogp_type(draw, f"{local:%H:%M}", x2 - x1 - 24, 9)
        day_w, clock_w = self._text_width(draw, day, day_font), self._text_width(draw, clock, clock_font)
        x = (x1 + x2 - day_w - 3 - clock_w) // 2
        self._draw_text_in_box(draw, (x, y1, x + day_w, y2), day, day_font, colour)
        self._draw_text_in_box(draw, (x + day_w + 3, y1, x2, y2), clock, clock_font, colour)

    def _draw_motogp_column_head(self, draw, box, title, accent):
        x1, y1, x2, _y2 = [int(value) for value in box]
        draw.rectangle((x1, y1 + 3, x1 + 4, y1 + 14), fill=accent, outline=COLORS["border"], width=1)
        title, font = self._fit_text_ellipsis(draw, title, x2 - x1 - 10, 11, bold=True, min_size=8)
        self._draw_text_in_box(draw, (x1 + 9, y1, x2, y1 + HEAD_HEIGHT), title, font, COLORS["text"])
        draw.line((x1, y1 + HEAD_HEIGHT, x2, y1 + HEAD_HEIGHT), fill=COLORS["border"], width=1)
        return y1 + HEAD_HEIGHT + 3

    def _motogp_face(self, profile):
        path = (profile or {}).get("photo")
        return self._load_local_logo(path, (FACE, FACE)) if path else None

    def _draw_motogp_rider_row(self, image, draw, box, row, profile, value_right):
        """Place, face, flag, mark, number and surname from the row's left; returns where the name
        ends. ``value_right`` is the room the row keeps at its right for its figures."""
        x1, y1, x2, y2 = [int(value) for value in box]
        disc = 15
        self._draw_rank_medal(draw, (x1, y1 + (y2 - y1 - disc) // 2, x1 + disc, y1 + (y2 - y1 - disc) // 2 + disc), row.get("pos"))
        face = self._motogp_face(profile)
        face_x = x1 + disc + 3
        if face is not None:
            # The cut-out stands on the row's foot, its own transparent ground over the panel.
            image.paste(face, (face_x + (FACE - face.width) // 2, y2 - face.height), face)
        flag_x = face_x + FACE + 3
        self._draw_flag_icon(image, draw, iso2_flag_url(row.get("iso")),
                             (flag_x, y1 + (y2 - y1 - FLAG_H) // 2, flag_x + FLAG_W, y1 + (y2 - y1 - FLAG_H) // 2 + FLAG_H),
                             row.get("iso"))
        mark_x = flag_x + FLAG_W + 4
        key = maker_mark(row.get("constructor"))
        mark = self._load_local_logo(os.path.join(LOCAL_TEAM_LOGO_DIR, f"{key}.png"), MARK_SLOT, alpha_threshold=8) if key else None
        if mark:
            mark = SportsDashboard._night_inked_mark(mark)
            image.paste(mark, (mark_x + (MARK_SLOT[0] - mark.width) // 2, y1 + (y2 - y1 - mark.height) // 2), mark)
        number_x = mark_x + MARK_SLOT[0] + 4
        number = str(row.get("number")) if row.get("number") is not None else ""
        number, number_font = self._motogp_type(draw, number, 16, 10, display=True)
        self._draw_text_in_box(draw, (number_x, y1, number_x + 16, y2 - 1), number, number_font, COLORS["text"], align="right")
        name_x = number_x + 20
        name, name_font = self._motogp_type(draw, rider_surname(row, profile), x2 - value_right - name_x - 4, 10)
        self._draw_text_in_box(draw, (name_x, y1, x2 - value_right, y2 - 1), name, name_font, COLORS["text"])
        return name_x, name_x + self._text_width(draw, name, name_font)

    def _draw_motogp_result(self, image, draw, box, result, riders, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        rows = result.get("rows") or []
        place = result.get("event_zh") or result.get("event") or ""
        title = f"{RESULT_NAMES.get(result.get('session'), '最新成绩')}" + (f" · {place}" if place else "")
        y = self._draw_motogp_column_head(draw, box, title, accent)
        if not rows:
            self._draw_centered_in_box(draw, (x1, y, x2, y2), "尚无成绩", self._font(11, False), COLORS["muted"])
            return
        values = [self._motogp_type(draw, row.get("gap") or row.get("time") or "", 60, 10) for row in rows]
        value_w = max([0] + [self._text_width(draw, text, font) for text, font in values if text])
        for row, (value, value_font) in zip(rows, values):
            if y + ROW_PITCH - 1 > y2:
                break
            self._draw_motogp_rider_row(image, draw, (x1, y, x2, y + ROW_PITCH - 1), row, riders.get(row.get("uuid")),
                                        value_w + 6)
            self._draw_text_in_box(draw, (x2 - value_w - 2, y, x2, y + ROW_PITCH - 2), value, value_font, COLORS["text"],
                                   align="right")
            y += ROW_PITCH

    def _draw_motogp_standings(self, image, draw, box, standings, riders, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        y = self._draw_motogp_column_head(draw, box, "车手积分榜", accent)
        leader = max([1.0] + [float(row.get("points") or 0) for row in standings])
        points = [self._motogp_type(draw, f"{int(row['points'])}" if isinstance(row.get("points"), (int, float)) else "", 30, 11,
                                    display=True) for row in standings]
        points_w = max([0] + [self._text_width(draw, text, font) for text, font in points if text])
        wins_w = 24 if any(row.get("wins") for row in standings) else 0
        for row, (value, value_font) in zip(standings, points):
            if y + ROW_PITCH - 1 > y2:
                break
            profile = riders.get(row.get("uuid"))
            right = points_w + wins_w + 6
            name_x, _name_end = self._draw_motogp_rider_row(image, draw, (x1, y, x2, y + ROW_PITCH - 1), row, profile, right)
            # The season's points as a bar in the team's colour under the name, the leader's full.
            bar_end = x2 - right - 2
            filled = name_x + round((bar_end - name_x) * float(row.get("points") or 0) / leader)
            if filled > name_x:
                draw.rectangle((name_x, y + ROW_PITCH - 4, filled, y + ROW_PITCH - 3), fill=team_colour(row, profile))
            if row.get("wins"):
                self._draw_motogp_wins(draw, (x2 - points_w - 4 - wins_w, y, x2 - points_w - 4, y + ROW_PITCH - 2), row["wins"])
            self._draw_text_in_box(draw, (x2 - points_w, y, x2, y + ROW_PITCH - 2), value, value_font, COLORS["text"],
                                   align="right")
            y += ROW_PITCH

    def _draw_motogp_wins(self, draw, box, wins):
        """The rider's race wins, right-aligned in ``box``: the count in MGP Display, then 胜."""
        x1, y1, x2, y2 = box
        unit_font = self._font(9, False)
        unit_w = self._text_width(draw, "胜", unit_font)
        count, count_font = self._motogp_type(draw, str(wins), x2 - x1 - unit_w - 1, 10, display=True)
        self._draw_text_in_box(draw, (x2 - unit_w, y1, x2, y2), "胜", unit_font, COLORS["text"])
        self._draw_text_in_box(draw, (x1, y1, x2 - unit_w - 1, y2), count, count_font, COLORS["text"], align="right")
