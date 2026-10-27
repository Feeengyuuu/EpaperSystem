"""Formula 1 panel ("race weekend").

The header's art is the eleven teams' cars on a staggered grid in
constructors' championship order, the leader on pole at the chequered line.
A band with the round, the Grand Prix (in Chinese), the circuit and a
countdown to the next session; a weekend timeline with one node per session
(done, live, next, later; the race node is chequered); the drivers'
championship with team logos, points bars told race by race (a segment per
scoring round: gold a win, team colour a podium, a tint of it other points)
and wins; and either the live
session order or the last race's top finishers with their teams' logos and
official side-view cars (set back by their gaps, so the leader is in front),
places gained from the grid and the fastest lap.
"""

from functools import lru_cache
import json
import math
import os

from PIL import Image, ImageDraw, ImageStat

from .common import COLORS, LOCAL_DECOR_DIR, LOCAL_F1_TITLE_WORDMARK_PATH, LOCAL_TEAM_LOGO_DIR, datetime, re
from .scorebug_kit import ScorebugSpec

SportsDashboard = None

F1_BAND_HEIGHT = 52
F1_TIMELINE_HEIGHT = 50
F1_ROW_HEIGHT = 15
F1_TEAM_LOGO_DIR = os.path.join(LOCAL_TEAM_LOGO_DIR, "f1_teams")
F1_GRID_DIR = os.path.join(LOCAL_DECOR_DIR, "f1_grid")
# The teams' official side views, for the live order and last race's rows.
F1_SIDE_DIR = os.path.join(LOCAL_DECOR_DIR, "f1_side")
F1_SIDE_SIZE = (64, 13)
# The least a car sits behind the one ahead, so the order reads even when the gaps are tiny.
F1_CAR_MIN_STEP = 6
# How much of the team colour a points bar's minor-points rounds keep (the rest is the panel).
F1_FORM_TINT = 0.45
F1_CIRCUIT_SHAPES = os.path.join(LOCAL_DECOR_DIR, "f1_circuits.json")
F1_LOGO_SIZE = (20, 12)
F1_PLACE_SIZE = (13, 10)
# F1's own colour for the fastest lap.
F1_FASTEST_LAP = (155, 60, 225)
# Constructor names (Jolpica, OpenF1) to the bundled team art; Racing Bulls before Red Bull.
F1_TEAM_KEYS = (
    ("mclaren", "mclaren"),
    ("ferrari", "ferrari"),
    ("racing bulls", "racing_bulls"),
    ("rb f1", "racing_bulls"),
    ("visa cash app", "racing_bulls"),
    ("red bull", "red_bull"),
    ("mercedes", "mercedes"),
    ("aston martin", "aston_martin"),
    ("alpine", "alpine"),
    ("williams", "williams"),
    ("haas", "haas"),
    ("audi", "audi"),
    ("sauber", "audi"),
    ("cadillac", "cadillac"),
)
# The grid's order before the constructors' standings arrive: 2025's championship, then Cadillac.
F1_DEFAULT_GRID = ("mclaren", "mercedes", "red_bull", "ferrari", "williams", "racing_bulls", "aston_martin", "haas",
                   "audi", "alpine", "cadillac")
# Livery colours by constructor name; mapped to the nearest panel ink.
F1_TEAM_COLORS = (
    ("mclaren", "ff8000"),
    ("ferrari", "e8002d"),
    ("red bull", "3671c6"),
    ("mercedes", "00a19c"),
    ("aston martin", "229971"),
    ("alpine", "0093cc"),
    ("williams", "1868db"),
    ("racing bulls", "6692ff"),
    ("rb f1", "6692ff"),
    ("sauber", "52e252"),
    ("audi", "bb0a30"),
    ("haas", "e6002b"),
    ("cadillac", "1e1e1e"),
)
F1_GRAND_PRIX_ZH = (
    ("abu dhabi", "阿布扎比"),
    ("australian", "澳大利亚"),
    ("austrian", "奥地利"),
    ("azerbaijan", "阿塞拜疆"),
    ("bahrain", "巴林"),
    ("barcelona", "巴塞罗那"),
    ("belgian", "比利时"),
    ("british", "英国"),
    ("canadian", "加拿大"),
    ("chinese", "中国"),
    ("dutch", "荷兰"),
    ("emilia", "艾米利亚-罗马涅"),
    ("hungarian", "匈牙利"),
    ("italian", "意大利"),
    ("japanese", "日本"),
    ("las vegas", "拉斯维加斯"),
    ("malaysian", "马来西亚"),
    ("mexico", "墨西哥城"),
    ("miami", "迈阿密"),
    ("monaco", "摩纳哥"),
    ("qatar", "卡塔尔"),
    ("saudi", "沙特阿拉伯"),
    ("singapore", "新加坡"),
    ("spanish", "西班牙"),
    ("são paulo", "圣保罗"),
    ("sao paulo", "圣保罗"),
    ("united states", "美国"),
)
# Each circuit (Jolpica's circuitId): its Chinese name, the city it is reached from, the lap's
# length in km and the Grand Prix distance in laps (None while not yet fixed).
F1_CIRCUITS = {
    "albert_park": ("阿尔伯特公园赛道", "墨尔本", 5.278, 58),
    "americas": ("美洲赛道", "奥斯汀", 5.513, 56),
    "bahrain": ("巴林国际赛道", "萨基尔", 5.412, 57),
    "baku": ("巴库城市赛道", "巴库", 6.003, 51),
    "catalunya": ("加泰罗尼亚赛道", "巴塞罗那", 4.657, 66),
    "hungaroring": ("亨格罗宁赛道", "布达佩斯", 4.381, 70),
    "imola": ("伊莫拉赛道", "伊莫拉", 4.909, 63),
    "interlagos": ("英特拉格斯赛道", "圣保罗", 4.309, 71),
    "jeddah": ("吉达滨海赛道", "吉达", 6.174, 50),
    "losail": ("卢赛尔国际赛道", "卢赛尔", 5.419, 57),
    "madring": ("马德林赛道", "马德里", 5.474, None),
    "marina_bay": ("滨海湾街道赛道", "新加坡", 4.940, 62),
    "miami": ("迈阿密国际赛道", "迈阿密", 5.412, 57),
    "monaco": ("摩纳哥赛道", "蒙特卡洛", 3.337, 78),
    "monza": ("蒙扎赛道", "蒙扎", 5.793, 53),
    "red_bull_ring": ("红牛环赛道", "斯皮尔伯格", 4.318, 71),
    "rodriguez": ("罗德里格斯兄弟赛道", "墨西哥城", 4.304, 71),
    "sepang": ("雪邦国际赛道", "吉隆坡", 5.543, 56),
    "shanghai": ("上海国际赛车场", "上海", 5.451, 56),
    "silverstone": ("银石赛道", "银石", 5.891, 52),
    "spa": ("斯帕赛道", "斯塔沃洛", 7.004, 44),
    "suzuka": ("铃鹿赛道", "铃鹿", 5.807, 53),
    "vegas": ("拉斯维加斯大道赛道", "拉斯维加斯", 6.201, 50),
    "villeneuve": ("维伦纽夫赛道", "蒙特利尔", 4.361, 70),
    "yas_marina": ("亚斯码头赛道", "阿布扎比", 5.281, 58),
    "zandvoort": ("赞德沃特赛道", "赞德沃特", 4.259, 72),
}
# The drivers' usual simplified Chinese names (mainland broadcasts), by their three-letter code.
F1_DRIVER_ZH = {
    "ALB": "阿尔本", "ALO": "阿隆索", "ANT": "安东内利", "BEA": "贝尔曼", "BOR": "博尔托莱托",
    "BOT": "博塔斯", "COL": "科拉平托", "DOO": "杜汉", "GAS": "加斯利", "HAD": "哈贾尔",
    "HAM": "汉密尔顿", "HUL": "霍肯伯格", "LAW": "劳森", "LEC": "勒克莱尔", "LIN": "林德布拉德",
    "NOR": "诺里斯", "OCO": "奥康", "PER": "佩雷兹", "PIA": "皮亚斯特里", "RUS": "拉塞尔",
    "SAI": "塞恩斯", "STR": "斯特罗尔", "TSU": "角田裕毅", "VER": "维斯塔潘", "ZHO": "周冠宇",
}


class F1RenderMixin:
    def _fit_f1_metric(self, draw, text, width, size, bold=False, *, max_height=13):
        """Use the private F1 face for complete Latin readouts, keeping Chinese legible."""
        text = str(text)
        if not text.isascii():
            return self._fit_text(draw, text, width, size, bold=bold, min_size=7)
        return self._fit_score(draw, text, width, size, bold=bold, sport="F1", max_height=max_height)

    def _draw_f1_compact_panel(self, image, draw, bounds, selected, source_state, now):
        selected = selected or {}
        spec = ScorebugSpec("F1", "f1_accent", "f1_live", "f1_tag", "panel_blue")
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["f1_accent"], COLORS["panel"], 22, 1)
        self._draw_f1_header(image, draw, (x1, y1, x2, y2), selected, source_state)
        left, top, right, bottom = x1 + 12, y1 + 57, x2 - 12, y2 - 7
        race = selected.get("main_race") or {}
        if not race:
            self._draw_kit_empty_card(draw, (left, top, right, bottom), spec, "等待 F1 赛历")
            return
        live = bool(selected.get("live_session"))
        self._draw_f1_race_band(image, draw, (left, top, right, top + F1_BAND_HEIGHT), race, selected, now, spec)
        timeline_y = top + F1_BAND_HEIGHT + 4
        self._draw_f1_weekend_timeline(draw, (left, timeline_y, right, timeline_y + F1_TIMELINE_HEIGHT), selected, now, spec)
        lower = timeline_y + F1_TIMELINE_HEIGHT + 4
        middle = (left + right) // 2
        self._draw_vertical_split(draw, middle + 4, lower + 2, bottom)
        self._draw_f1_standings(image, draw, (left, lower, middle - 6, bottom), selected.get("driver_standings") or [], spec)
        leaderboard = selected.get("leaderboard") or []
        if live and leaderboard:
            session = (selected.get("live_session") or {}).get("label") or "LIVE"
            self._draw_f1_order(image, draw, (middle + 14, lower, right, bottom), f"LIVE · {session}", leaderboard, spec,
                                live=True)
        else:
            result = selected.get("last_result") or {}
            title = SportsDashboard._f1_short_race_name(result.get("race_name")) or "LAST RACE"
            self._draw_f1_order(image, draw, (middle + 14, lower, right, bottom), title, result.get("top") or [], spec,
                                race=result)

    def _draw_f1_header(self, image, draw, bounds, selected, source_state):
        x1, y1, x2, _y2 = [int(value) for value in bounds]
        header_y = y1 + 8
        self._draw_f1_logo(image, draw, x1 + 15, header_y - 2, 74, 34)
        if not self._draw_local_wordmark(image, LOCAL_F1_TITLE_WORDMARK_PATH, x1 + 96, header_y, 142, 20):
            title, title_font = self._fit_f1_metric(draw, "FORMULA 1", 142, 17, bold=True, max_height=20)
            draw.text((x1 + 96, header_y + 1), title, font=title_font, fill=COLORS["text"])
        source_label = self._f1_source_label(selected, source_state)
        if not self._draw_source_credit(image, draw, (x1 + 97, header_y + 22, x1 + 242, header_y + 36), source_label, size=10):
            source_label, source_font = self._fit_text(draw, source_label, 145, 10, bold=True, min_size=7)
            draw.text((x1 + 97, header_y + 22), source_label, font=source_font, fill=COLORS["muted"])
        if not self._draw_f1_team_grid(image, draw, (x1 + 250, y1 + 4, x2 - 92, y1 + 46), selected.get("constructor_standings")):
            self._draw_f1_header_track_strip(draw, x1 + 250, header_y + 2, x2 - 92, y1 + 47)
        status = str(selected.get("status") or "BREAK").upper()
        pill = {"LIVE": "LIVE", "NEXT": "NEXT", "RECENT": "RESULT"}.get(status, "BREAK")
        self._draw_status_pill(draw, x2 - 84, header_y + 4, pill, status == "LIVE")
        draw.line((x1 + 12, y1 + 48, x2 - 12, y1 + 48), fill=COLORS["border"], width=1)

    def _draw_f1_race_band(self, image, draw, box, race, selected, now, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        live_session = selected.get("live_session")
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, COLORS[spec.live] if live_session else COLORS[spec.accent])
        # The host flag identifies the Grand Prix, as on F1's own race cards;
        # keep its proportions and outline it so white fields stay visible.
        flag = self._draw_event_mark(image, SportsDashboard._f1_country_flag_path(race), (x1 + 14, y1 + 9, x1 + 62, y2 - 9), align="center")
        if flag:
            draw.rectangle((flag[0] - 1, flag[1] - 1, flag[2], flag[3]), outline=COLORS["border"], width=1)
            badge = (x1 + 14, y1 + 8, flag[2], y2 - 9)
        else:
            badge = (x1 + 14, y1 + 8, x1 + 52, y2 - 9)
            draw.rounded_rectangle(badge, radius=4, fill=COLORS["text"], outline=COLORS["border"], width=1)
            round_text = f"R{race.get('round')}" if race.get("round") else "F1"
            self._draw_centered_in_box(draw, (badge[0], badge[1] + 1, badge[2], badge[1] + 21), round_text, self._font(13, True), COLORS["paper_text"])
            self._draw_centered_in_box(draw, (badge[0], badge[1] + 20, badge[2], badge[3] - 2), str(race.get("season") or ""), self._font(8, False), COLORS["paper_text"])
        count_x1 = x2 - 150
        shape = _circuit_shape(race.get("circuit_id"))
        if shape:
            # The words may run up to the track's minimum room; the track then takes the span
            # left between them and the countdown.
            text_x2 = count_x1 - 70
        else:
            circuit = self._draw_event_mark(
                image, SportsDashboard._f1_circuit_mark_path(race), (count_x1 - 96, y1 + 4, count_x1 - 8, y2 - 4), align="right", line_art=True
            )
            text_x2 = circuit[0] - 8 if circuit else count_x1 - 6
        name = SportsDashboard._f1_grand_prix_zh(race.get("race_name"))
        facts = F1_CIRCUITS.get(str(race.get("circuit_id") or ""))
        name_y = y1 + 3 if facts else y1 + 6
        name, name_font = self._fit_text(draw, name, text_x2 - badge[2] - 10, 17, bold=True, min_size=11)
        self._draw_text_in_box(draw, (badge[2] + 10, name_y, text_x2, name_y + 21), name, name_font, COLORS["text"])
        text_end = badge[2] + 10 + self._text_width(draw, name, name_font)
        if facts:
            text_end = max(text_end, self._draw_f1_circuit_facts(draw, (badge[2] + 10, y1 + 24, text_x2, y2 - 4), race, facts, flag))
        else:
            city = ", ".join(part for part in (race.get("locality"), race.get("country")) if part)
            round_label = f"ROUND {race.get('round')}" if flag and race.get("round") else ""
            place = "  ·  ".join(part for part in (round_label, race.get("circuit_name"), city) if part)
            place, place_font = self._fit_text_ellipsis(draw, place, text_x2 - badge[2] - 10, 9, bold=False, min_size=7)
            self._draw_text_in_box(draw, (badge[2] + 10, y1 + 29, text_x2, y1 + 41), place, place_font, COLORS["text"])
            text_end = max(text_end, badge[2] + 10 + self._text_width(draw, place, place_font))
        count_start = self._draw_f1_countdown(draw, (count_x1, y1, x2, y2), selected, now, spec, live_session)
        if shape:
            self._draw_f1_circuit_shape(image, shape, (int(text_end) + 12, y1 + 6, int(count_start) - 14, y2 - 6))

    def _draw_f1_countdown(self, draw, box, selected, now, spec, live_session):
        """The countdown at the band's right; returns where its words begin."""
        count_x1, y1, x2, _y2 = box
        label, value, value_key = SportsDashboard._f1_countdown_block(selected, now)
        label_font = self._font(9, True)
        self._draw_text_in_box(draw, (count_x1, y1 + 6, x2 - 10, y1 + 18), label, label_font, COLORS[spec.live] if live_session else COLORS["text"], align="right")
        value, value_font = self._fit_f1_metric(draw, value, x2 - 10 - count_x1, 17, bold=True, max_height=23)
        self._draw_text_in_box(draw, (count_x1, y1 + 19, x2 - 10, y1 + 42), value, value_font, COLORS[value_key], align="right")
        return x2 - 10 - max(self._text_width(draw, label, label_font), self._text_width(draw, value, value_font))

    def _draw_f1_circuit_shape(self, image, points, box):
        """The circuit's line as large as fits ``box`` and centred in it: a rounded ink line over
        a tinted offset shadow, drawn at four times the size and scaled down for smooth curves."""
        x1, y1, x2, y2 = box
        if x2 - x1 < 40 or y2 - y1 < 20:
            return False
        width, height = max(x for x, _y in points), max(y for _x, y in points)
        scale = min((x2 - x1) / width, (y2 - y1) / height)
        left, top = x1 + (x2 - x1 - width * scale) / 2, y1 + (y2 - y1 - height * scale) / 2
        factor, pad = 4, 3
        size = (round(width * scale) + 2 * pad, round(height * scale) + 2 * pad)
        mask = Image.new("L", (size[0] * factor, size[1] * factor), 0)
        line = [((pad + x * scale) * factor, (pad + y * scale) * factor) for x, y in points]
        stroke = 3 * factor
        canvas = ImageDraw.Draw(mask)
        canvas.line(line, fill=255, width=stroke, joint="curve")
        start_x, start_y = line[0]
        canvas.ellipse((start_x - stroke / 2, start_y - stroke / 2, start_x + stroke / 2, start_y + stroke / 2), fill=255)
        mask = mask.resize(size, Image.Resampling.LANCZOS)
        origin = (round(left) - pad, round(top) - pad)
        image.paste(COLORS["f1_track"], (origin[0] + 2, origin[1] + 2), mask)
        image.paste(COLORS["text"], origin, mask)
        return True

    def _draw_f1_weekend_timeline(self, draw, box, selected, now, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        sessions = list(selected.get("weekend_sessions") or [])[:6]
        if not sessions:
            return
        axis_y = y1 + 23
        margin = 26
        step = (x2 - x1 - 2 * margin) / max(1, len(sessions) - 1)
        positions = [int(round(x1 + margin + index * step)) for index in range(len(sessions))]
        states = [SportsDashboard._f1_session_state(session, selected, now) for session in sessions]
        for index in range(len(sessions) - 1):
            done = states[index + 1] in {"done", "live"}
            draw.line((positions[index], axis_y, positions[index + 1], axis_y), fill=COLORS["border"] if done else COLORS["line"], width=3 if done else 1)
        for position, session, state in zip(positions, sessions, states):
            label = str(session.get("label") or "").upper()
            label, label_font = self._fit_f1_metric(draw, label, 52, 9, state in {"live", "next"} or label == "RACE", max_height=12)
            self._draw_centered_in_box(draw, (position - 26, y1, position + 26, y1 + 12), label, label_font, COLORS[spec.live] if state == "live" else COLORS["text"])
            self._draw_f1_session_node(draw, position, axis_y, state, label == "RACE", spec)
            start = session.get("start")
            if isinstance(start, datetime):
                when = f"{start.strftime('%a').upper()} {SportsDashboard._format_time(start)}"
                when, when_font = self._fit_f1_metric(draw, when, min(60, int(step) - 2) if len(sessions) > 1 else 60, 9, bold=state == "next")
                self._draw_centered_in_box(draw, (position - 30, axis_y + 11, position + 30, axis_y + 24), when, when_font, COLORS["text"])

    def _draw_f1_session_node(self, draw, x, y, state, is_race, spec):
        radius = 8 if is_race else 6
        box = (x - radius, y - radius, x + radius, y + radius)
        if is_race and state != "done":
            draw.rectangle(box, fill=COLORS["panel"], outline=COLORS["border"], width=1)
            half = radius
            for row in range(2):
                for column in range(2):
                    if (row + column) % 2 == 0:
                        left = x - half + column * half
                        top = y - half + row * half
                        draw.rectangle((left, top, left + half, top + half), fill=COLORS["text"])
            if state in {"next", "live"}:
                draw.rectangle((x - radius - 3, y - radius - 3, x + radius + 3, y + radius + 3), outline=COLORS[spec.live if state == "live" else "border"], width=2)
            return
        if state == "done":
            draw.ellipse(box, fill=COLORS["text"], outline=COLORS["border"], width=1)
        elif state == "live":
            draw.ellipse((x - radius - 3, y - radius - 3, x + radius + 3, y + radius + 3), outline=COLORS[spec.live], width=2)
            draw.ellipse(box, fill=COLORS[spec.live], outline=COLORS["border"], width=1)
        elif state == "next":
            draw.ellipse(box, fill=COLORS["amber"], outline=COLORS["border"], width=2)
        else:
            draw.ellipse(box, fill=COLORS["panel"], outline=COLORS["border"], width=1)

    def _draw_f1_standings(self, image, draw, box, standings, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        y = self._draw_kit_column_header(draw, x1, y1, x2, spec, "DRIVERS")
        if not standings:
            self._draw_text_in_box(draw, (x1 + 4, y, x2, y + 14), "积分榜更新中", self._font(9, True), COLORS["muted"])
            return
        leader_points = max(1.0, SportsDashboard._f1_points(standings[0].get("points")))
        name_font, wins_font = self._font(11, True), self._font(9, False)
        names = [SportsDashboard._f1_driver_zh(row) for row in standings]
        # The bars start after the widest shown name, so a five-character name still fits.
        name_w = min(66, max([32] + [self._text_width(draw, name, name_font) for name in names]))
        wins = [SportsDashboard._f1_wins(row) for row in standings]
        # A wins column (n 胜) right of the points once anyone has won.
        wins_w = max([0] + [self._text_width(draw, f"{count}胜", wins_font) + 5 for count in wins if count])
        name_x = x1 + 18 + F1_LOGO_SIZE[0] + 3
        for row, name, count in zip(standings, names, wins):
            if y + F1_ROW_HEIGHT > y2 + 1:
                break
            ink_key = SportsDashboard._f1_team_ink_key(row.get("constructor"))
            position_text, position_font = self._fit_f1_metric(draw, str(row.get("position") or ""), 14, 10, True)
            self._draw_text_in_box(draw, (x1, y, x1 + 14, y + 13), position_text, position_font, COLORS["text"])
            self._paste_f1_team_logo(image, draw, row.get("constructor"), (x1 + 16, y + 1), ink_key)
            self._draw_text_in_box(draw, (name_x, y, name_x + name_w, y + 13), name, name_font, COLORS["text"])
            points = SportsDashboard._f1_points(row.get("points"))
            points_text = str(row.get("points") or "0").removesuffix(".0")
            points_x2 = x2 - wins_w
            points_text, points_font = self._fit_f1_metric(draw, points_text, max(18, points_x2 - name_x - name_w - 4), 10, True)
            points_w = self._text_width(draw, points_text, points_font)
            bar_x1, bar_x2 = name_x + 3 + name_w, points_x2 - points_w - 6
            if bar_x2 - bar_x1 > 10:
                # The bar alone, on the panel: no empty track behind the shorter ones.
                filled = bar_x1 + int((bar_x2 - bar_x1) * points / leader_points)
                self._draw_f1_points_bar(draw, (bar_x1, y + 4, max(bar_x1 + 1, filled), y + 9), row.get("form"), ink_key)
            self._draw_text_in_box(draw, (points_x2 - points_w, y, points_x2, y + 13), points_text, points_font, COLORS["text"], align="right")
            if count:
                self._draw_text_in_box(draw, (points_x2, y + 1, x2, y + 13), f"{count}胜", wins_font, COLORS["text"], align="right")
            y += F1_ROW_HEIGHT

    def _draw_f1_points_bar(self, draw, box, form, ink_key):
        """A driver's points as one bar in the team colour; with the season race by race, one
        segment per scoring round in calendar order, as long as its points: gold for a win, the
        team colour for the rest of the podium, a light tint of it for the other points, a
        hairline of the panel between rounds wide enough to show one."""
        x1, y1, x2, y2 = box
        team = COLORS[ink_key]
        scored = [(points, place) for _round, points, place in (form or []) if points > 0]
        total = sum(points for points, _place in scored)
        if not scored or x2 - x1 < len(scored):
            draw.rectangle(box, fill=team, outline=COLORS["border"], width=1)
            return
        tint = tuple(round(channel * F1_FORM_TINT + panel * (1 - F1_FORM_TINT)) for channel, panel in zip(team, COLORS["panel"]))
        done = 0.0
        for index, (points, place) in enumerate(scored):
            left = x1 + round((x2 - x1) * done / total)
            done += points
            right = x1 + round((x2 - x1) * done / total)
            if index == len(scored) - 1:
                end = x2
            else:
                end = max(left, right - (2 if right - left >= 3 else 1))
            fill = COLORS["amber"] if place == 1 else (team if place in (2, 3) else tint)
            draw.rectangle((left, y1, end, y2), fill=fill)
        draw.rectangle(box, outline=COLORS["border"], width=1)

    def _draw_f1_order(self, image, draw, box, title, rows, spec, live=False, race=None):
        x1, y1, x2, y2 = [int(value) for value in box]
        if race:
            y = self._draw_f1_race_header(image, draw, (x1, y1, x2), spec, title, race)
        else:
            y = self._draw_kit_column_header(draw, x1, y1, x2, spec, title)
        if not rows:
            self._draw_text_in_box(draw, (x1 + 4, y, x2, y + 14), "等待成绩", self._font(9, True), COLORS["muted"])
            return
        shown = rows[:max(0, (y2 + 1 - y) // F1_ROW_HEIGHT)]
        # Each driver's car (the team's official side view) races in the room the names leave,
        # the leader's nose before the widest gap.
        # Fit and measure the same font: longer official digits must not overlap names/cars.
        gap_readouts = [
            self._fit_f1_metric(draw, SportsDashboard._f1_gap_label(row, row.get("position") or index + 1, live),
                                max(28, int((x2 - x1) * 0.36)), 9, (row.get("position") or index + 1) == 1)
            for index, row in enumerate(shown)
        ]
        gap_widths = [self._text_width(draw, gap, font) for gap, font in gap_readouts if gap]
        car_x2 = x2 - max([0, *gap_widths]) - 8 - (12 if any(row.get("fastest_lap") for row in shown) else 0)
        cars = []
        for index, row in enumerate(rows):
            if y + F1_ROW_HEIGHT > y2 + 1:
                break
            position = row.get("position") or index + 1
            team = row.get("constructor") or row.get("team")
            ink_key = SportsDashboard._f1_team_ink_key(team)
            if not self._draw_f1_place_numeral(image, (x1, y), position):
                badge_fill = COLORS["amber"] if position == 1 else COLORS[spec.tag]
                draw.rounded_rectangle((x1, y + 1, x1 + 20, y + 13), radius=3, fill=badge_fill, outline=COLORS["border"], width=1)
                position_text, position_font = self._fit_f1_metric(draw, f"P{position}", 20, 8, True, max_height=12)
                self._draw_centered_in_box(draw, (x1, y + 1, x1 + 20, y + 13), position_text, position_font, COLORS["text"])
            self._paste_f1_team_logo(image, draw, team, (x1 + 23, y + 1), ink_key)
            gap, gap_font = gap_readouts[index]
            gap_w = self._text_width(draw, gap, gap_font) if gap else 0
            right = x2 - gap_w - 6
            if row.get("fastest_lap"):
                right -= 12
                self._draw_f1_fastest_lap(draw, right + 6, y + 7)
            gained = None if live else SportsDashboard._f1_places_gained(row, position)
            moved = f"{abs(gained)}" if gained else ""
            moved, moved_font = self._fit_f1_metric(draw, moved, 20, 8, True)
            moved_w = self._text_width(draw, moved, moved_font) + 10 if moved else 0
            name_x = x1 + 23 + F1_LOGO_SIZE[0] + 3
            label = SportsDashboard._f1_driver_zh(row)
            label, label_font = self._fit_text_ellipsis(draw, label, right - moved_w - 2 - name_x, 10, bold=True, min_size=8)
            self._draw_text_in_box(draw, (name_x, y, right - moved_w, y + 13), label, label_font, COLORS["text"])
            text_end = name_x + self._text_width(draw, label, label_font)
            if moved:
                mark_x = text_end + 4
                SportsDashboard._draw_f1_move_arrow(draw, mark_x, y + 7, gained > 0)
                self._draw_text_in_box(draw, (mark_x + 7, y + 1, mark_x + moved_w, y + 13), moved, moved_font, COLORS["text"])
                text_end = mark_x + moved_w
            cars.append((team, y, text_end, SportsDashboard._f1_gap_seconds(row, position)))
            if gap:
                self._draw_text_in_box(draw, (x2 - gap_w, y, x2, y + 13), gap, gap_font, COLORS["text"], align="right")
            y += F1_ROW_HEIGHT
        self._draw_f1_racing_cars(image, cars, car_x2)

    def _draw_f1_racing_cars(self, image, cars, nose_x2):
        """The rows' cars (team, row top, where the row's words end, gap in seconds) laid out as
        the race stands: the leader's nose at ``nose_x2`` and each car behind set back in
        proportion to its gap, at least a few pixels behind the car ahead so the order always
        reads (a lapped car furthest back), all clear of the longest name; none without room.
        Returns the noses' x positions, or [] when none were drawn."""
        if not cars:
            return []
        art = [self._f1_side_car(team) for team, _y, _end, _gap in cars]
        widths = [car.width for car in art if car is not None]
        lane_x1 = max(end for _team, _y, end, _gap in cars) + 6
        room = nose_x2 - max(widths or [0]) - lane_x1
        if not widths or room < 0:
            return []
        known = [gap for _team, _y, _end, gap in cars if gap is not None and gap != math.inf]
        longest = max(known or [0])
        step = min(F1_CAR_MIN_STEP, room / max(1, len(cars) - 1))
        backs, back = [], 0.0
        for index, (_team, _y, _end, gap) in enumerate(cars):
            if index:
                timed = gap is not None and gap != math.inf and longest > 0
                back = max(room * gap / longest if timed else back + step, back + step)
            backs.append(back)
        # The steps (and a lapped car one step behind) can run past the room: squeeze them all.
        scale = room / backs[-1] if backs[-1] > room else 1.0
        noses = [round(nose_x2 - back * scale) for back in backs]
        for (_team, y, _end, _gap), car, nose in zip(cars, art, noses):
            if car is not None:
                image.paste(car, (nose - car.width, y + (F1_ROW_HEIGHT - car.height) // 2), car)
        return noses

    def _draw_f1_race_header(self, image, draw, box, spec, title, race):
        """A finished race's column header: the host's flag and 上站 · the Grand Prix, with its
        circuit and date at the right."""
        x1, y1, x2 = box
        draw.rectangle((x1, y1 + 2, x1 + 8, y1 + 17), fill=COLORS[spec.accent], outline=COLORS["border"], width=1)
        x = x1 + 13
        flag = self._draw_event_mark(image, SportsDashboard._f1_country_flag_path(race), (x, y1 + 4, x + 21, y1 + 16), align="left")
        if flag:
            draw.rectangle((flag[0] - 1, flag[1] - 1, flag[2], flag[3]), outline=COLORS["border"], width=1)
            x = flag[2] + 5
        start = race.get("start")
        when = start.strftime("%m/%d") if isinstance(start, datetime) else ""
        circuit = (F1_CIRCUITS.get(str(race.get("circuit_id") or "")) or ("",))[0]
        meta_font = self._font(9, False)
        title_font = self._font(13, True)
        for meta in (" · ".join(part for part in (SportsDashboard._f1_circuit_short(circuit), when) if part), when):
            meta_w = self._text_width(draw, meta, meta_font) if meta else 0
            if self._text_width(draw, title, title_font) + meta_w + 8 <= x2 - x:
                break
        title, fitted = self._fit_text(draw, title, x2 - x - meta_w - 6, 13, bold=True, min_size=9)
        self._draw_text_in_box(draw, (x, y1, x2 - meta_w - 4, y1 + 18), title, fitted, COLORS["text"])
        if meta:
            self._draw_text_in_box(draw, (x2 - meta_w, y1 + 2, x2, y1 + 17), meta, meta_font, COLORS["text"], align="right")
        draw.line((x1, y1 + 19, x2, y1 + 19), fill=COLORS["border"], width=1)
        return y1 + 23

    def _draw_f1_circuit_facts(self, draw, box, race, facts, flag):
        """Under the Grand Prix: a chequered flag with the circuit's Chinese and official names,
        then a pin and the city, the lap's length, the race's laps and the round; returns where
        the words end."""
        x1, y1, x2, _y2 = [int(value) for value in box]
        circuit, city, length, laps = facts
        small = self._font(9, False)
        SportsDashboard._draw_f1_icon(draw, "flag", x1, y1 + 6)
        x = x1 + 14
        circuit, circuit_font = self._fit_text(draw, circuit, x2 - x, 10, bold=True, min_size=8)
        self._draw_text_in_box(draw, (x, y1, x2, y1 + 12), circuit, circuit_font, COLORS["text"])
        x += self._text_width(draw, circuit, circuit_font) + 6
        official = str(race.get("circuit_name") or "")
        if official and x2 - x > 40:
            official, official_font = self._fit_text_ellipsis(draw, official, x2 - x, 8, bold=False, min_size=7)
            self._draw_text_in_box(draw, (x, y1 + 1, x2, y1 + 12), official, official_font, COLORS["muted"])
        row_y = y1 + 13
        items = [("pin", city), ("length", f"{length:.3f} km")]
        if laps:
            items.append(("laps", f"{laps} 圈"))
        if race.get("round") and flag:
            items.append((None, f"第 {race['round']} 站"))
        end = x - 6 if not (official and x2 - x > 40) else x + self._text_width(draw, official, official_font)
        x = x1
        for icon, label in items:
            label_x = x + (14 if icon else 0)
            width = label_x - x + self._text_width(draw, label, small)
            if x + width > x2:
                break
            if icon:
                SportsDashboard._draw_f1_icon(draw, icon, x, row_y + 6)
            self._draw_text_in_box(draw, (label_x, row_y, x + width + 2, row_y + 12), label, small, COLORS["text"])
            end = max(end, x + width)
            x += width + 9
        return end

    @staticmethod
    def _draw_f1_icon(draw, kind, x, y):
        """An 11px pictogram from x, centred on the line at y: a chequered flag, a map pin, a
        measured span or a lap loop."""
        red, ink = COLORS["f1_accent"], COLORS["text"]
        if kind == "flag":
            draw.line((x + 1, y - 5, x + 1, y + 5), fill=ink, width=1)
            for row in range(2):
                for column in range(3):
                    fill = ink if (row + column) % 2 == 0 else COLORS["panel"]
                    left, top = x + 2 + column * 3, y - 5 + row * 3
                    draw.rectangle((left, top, left + 2, top + 2), fill=fill)
            draw.rectangle((x + 2, y - 5, x + 10, y + 1), outline=ink, width=1)
        elif kind == "pin":
            draw.ellipse((x + 1, y - 6, x + 9, y + 2), fill=red)
            draw.polygon(((x + 2, y), (x + 8, y), (x + 5, y + 6)), fill=red)
            draw.ellipse((x + 3, y - 4, x + 7, y), fill=COLORS["panel"])
        elif kind == "length":
            draw.line((x + 1, y - 4, x + 1, y + 4), fill=red, width=2)
            draw.line((x + 10, y - 4, x + 10, y + 4), fill=red, width=2)
            draw.line((x + 3, y, x + 8, y), fill=red, width=1)
            draw.polygon(((x + 2, y), (x + 5, y - 3), (x + 5, y + 3)), fill=red)
            draw.polygon(((x + 9, y), (x + 6, y - 3), (x + 6, y + 3)), fill=red)
        elif kind == "laps":
            draw.arc((x + 1, y - 5, x + 10, y + 4), start=60, end=340, fill=red, width=2)
            draw.polygon(((x + 7, y - 7), (x + 11, y - 4), (x + 7, y - 1)), fill=red)

    # Team art ----------------------------------------------------------------

    def _draw_f1_team_grid(self, image, draw, box, standings):
        """The teams' cars on a three-wide staggered grid in constructors' championship order,
        the leader on pole against the chequered line at the right, each in its slot; False when
        the cars are missing."""
        x1, y1, x2, y2 = [int(value) for value in box]
        lane_h = (y2 - y1) / 3
        car_h = int(lane_h) - 1
        cars = []
        for key in SportsDashboard._f1_grid_order(standings):
            car = self._load_local_logo(os.path.join(F1_GRID_DIR, f"{key}.png"), (car_h * 4, car_h), alpha_threshold=12)
            if car is not None:
                cars.append(car)
        if len(cars) < 3:
            return False
        car_w = max(car.width for car in cars)
        square = 3
        line_x = x2 - 2 * square
        # Cars three places apart share a lane, a car's length and a little more apart.
        step = min((car_w + 6) / 3, (line_x - 5 - car_w - x1) / max(1, len(cars) - 1))
        for row in range(int((y2 - y1) // square)):
            for column in range(2):
                fill = COLORS["text"] if (row + column) % 2 == 0 else COLORS["panel"]
                left, top = line_x + column * square, y1 + row * square
                draw.rectangle((left, top, left + square - 1, top + square - 1), fill=fill)
        for index, car in enumerate(cars):
            nose = line_x - 5 - index * step
            top = y1 + (index % 3) * lane_h + (lane_h - car.height) / 2
            slot_x, slot_y1, slot_y2 = round(nose + 2), round(top - 1), round(top + car.height)
            draw.line((slot_x - 4, slot_y1, slot_x, slot_y1, slot_x, slot_y2, slot_x - 4, slot_y2), fill=COLORS["muted"], width=1)
            image.paste(car, (round(nose - car.width), round(top)), car)
        return True

    def _draw_f1_place_numeral(self, image, xy, position):
        """The finishing place as its italic numeral, straight on the row: gold, silver and
        bronze on the podium, the theme's ink below it; centred in the 20px badge slot at
        ``xy``; False beyond fifth or without the art."""
        x, y = xy
        try:
            place = int(position)
        except (TypeError, ValueError):
            return False
        if not 1 <= place <= 5:
            return False
        numeral = self._load_local_logo(os.path.join(F1_GRID_DIR, f"place_{place}.png"), F1_PLACE_SIZE, alpha_threshold=12)
        if numeral is None:
            return False
        if place > 3:
            ink = Image.new("RGBA", numeral.size, (*COLORS["text"], 255))
            ink.putalpha(numeral.getchannel("A"))
            numeral = ink
        image.paste(numeral, (x + (20 - numeral.width) // 2, y + (14 - numeral.height) // 2), numeral)
        return True

    def _f1_team_logo(self, team):
        """The team's official mark at the row size; its white mark where the colour one is too
        dark for the panel (night)."""
        key = SportsDashboard._f1_team_key(team)
        if not key:
            return None
        logo = self._load_local_logo(os.path.join(F1_TEAM_LOGO_DIR, f"{key}.png"), F1_LOGO_SIZE)
        if logo is not None and _logo_contrast(logo, COLORS["panel"]) < 2.0:
            light = self._load_local_logo(os.path.join(F1_TEAM_LOGO_DIR, f"{key}_light.png"), F1_LOGO_SIZE)
            if light is not None and _logo_contrast(light, COLORS["panel"]) > _logo_contrast(logo, COLORS["panel"]):
                return light
        return logo

    def _paste_f1_team_logo(self, image, draw, team, xy, ink_key):
        """The team's logo centred in an F1_LOGO_SIZE slot at ``xy``, else a bar in its colour."""
        x, y = xy
        logo = self._f1_team_logo(team)
        if logo is None:
            draw.rectangle((x + 7, y + 1, x + 9, y + 10), fill=COLORS[ink_key])
            return
        width, height = F1_LOGO_SIZE
        image.paste(logo, (x + (width - logo.width) // 2, y + (height - logo.height) // 2), logo)

    def _f1_side_car(self, team):
        """The team's car in side view at the row size, nose to the right; None without the art."""
        key = SportsDashboard._f1_team_key(team)
        if not key:
            return None
        return self._load_local_logo(os.path.join(F1_SIDE_DIR, f"{key}.png"), F1_SIDE_SIZE, alpha_threshold=12)

    @staticmethod
    def _draw_f1_fastest_lap(draw, x, y):
        """A purple stopwatch, F1's mark for the fastest lap, centred at (x, y)."""
        draw.ellipse((x - 5, y - 4, x + 5, y + 6), fill=F1_FASTEST_LAP)
        draw.rectangle((x - 1, y - 7, x + 1, y - 5), fill=F1_FASTEST_LAP)
        draw.line((x, y + 1, x, y - 2), fill=(255, 255, 255), width=1)
        draw.line((x, y + 1, x + 2, y + 1), fill=(255, 255, 255), width=1)

    @staticmethod
    def _draw_f1_move_arrow(draw, x, y, gained):
        """A small solid triangle, green up for places gained from the grid, red down for lost."""
        if gained:
            draw.polygon(((x, y + 3), (x + 6, y + 3), (x + 3, y - 3)), fill=COLORS["green"])
        else:
            draw.polygon(((x, y - 3), (x + 6, y - 3), (x + 3, y + 3)), fill=COLORS["red"])

    # Labels ------------------------------------------------------------------

    @staticmethod
    def _f1_grand_prix_zh(name):
        text = str(name or "").strip()
        lowered = text.lower()
        for key, zh in F1_GRAND_PRIX_ZH:
            if key in lowered:
                return f"{zh}大奖赛"
        return text or "Formula 1"

    @staticmethod
    def _f1_driver_zh(row):
        """The driver's simplified Chinese name, else the code, else the surname."""
        code = str(row.get("driver_code") or "").strip().upper()
        if code in F1_DRIVER_ZH:
            return F1_DRIVER_ZH[code]
        surname = str(row.get("driver_name") or "").split(" ")[-1]
        return code or surname

    @staticmethod
    def _f1_short_race_name(name):
        text = SportsDashboard._f1_grand_prix_zh(name) if name else ""
        return f"上站 · {text}" if text else ""

    @staticmethod
    def _f1_circuit_short(name):
        """滨海湾街道赛道 -> 滨海湾: the circuit's name without its kind."""
        return re.sub(r"(街道|国际|城市|大道)?(赛道|赛车场)$", "", str(name or "")) or str(name or "")

    @staticmethod
    def _f1_team_key(team):
        """The bundled team art's key for a constructor's name, else None."""
        lowered = str(team or "").lower()
        for name, key in F1_TEAM_KEYS:
            if name in lowered:
                return key
        return None

    @staticmethod
    def _f1_grid_order(standings):
        """Every team once, in constructors' championship order, then any not yet ranked."""
        order = []
        for row in standings or []:
            key = SportsDashboard._f1_team_key((row or {}).get("constructor"))
            if key and key not in order:
                order.append(key)
        return order + [key for key in F1_DEFAULT_GRID if key not in order]

    @staticmethod
    def _f1_wins(row):
        try:
            return max(0, int(float((row or {}).get("wins") or 0)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _f1_places_gained(row, position):
        """Places made up from the grid (negative when lost); None from the pit lane or unknown."""
        grid = (row or {}).get("grid")
        try:
            finish = int(position)
        except (TypeError, ValueError):
            return None
        if not isinstance(grid, int) or grid <= 0:
            return None
        return grid - finish

    @staticmethod
    def _f1_team_ink_key(constructor):
        lowered = str(constructor or "").lower()
        for key, color in F1_TEAM_COLORS:
            if key in lowered:
                return SportsDashboard._kit_team_ink_key(color, "f1_accent")
        return "text"

    @staticmethod
    def _f1_points(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _f1_session_state(session, selected, now):
        live = (selected or {}).get("live_session") or {}
        start = (session or {}).get("start")
        if live and start == live.get("start") and session.get("label") == live.get("label"):
            return "live"
        next_session = (selected or {}).get("next_session") or {}
        if next_session and start == next_session.get("start") and session.get("label") == next_session.get("label"):
            return "next"
        if isinstance(start, datetime) and start < now:
            return "done"
        return "later"

    @staticmethod
    def _f1_countdown_block(selected, now):
        """(label, value, colour key) for the band's right side."""
        selected = selected or {}
        live = selected.get("live_session")
        if live:
            return "LIVE NOW", str(live.get("title") or live.get("label") or "SESSION").upper(), "f1_live"
        next_session = selected.get("next_session")
        if next_session:
            label = str(next_session.get("title") or next_session.get("label") or "NEXT").upper()
            return f"NEXT · {label}", SportsDashboard._kit_countdown_label(next_session.get("start"), now), "text"
        return "SEASON", "完赛", "text"

    @staticmethod
    def _f1_gap_seconds(row, position):
        """The gap to the leader in seconds: 0 for the leader, infinity a lap or more down,
        None when not given ("+13.078", "1:02.5" and "+5.2s" read as seconds)."""
        if str(position) == "1":
            return 0.0
        gap = str((row or {}).get("gap") or "").strip().lstrip("+").strip()
        if "lap" in gap.lower():
            return math.inf
        match = re.fullmatch(r"(?:(\d+):)?(\d+(?:\.\d+)?)s?", gap)
        if not match:
            return None
        minutes, seconds = match.groups()
        return int(minutes or 0) * 60 + float(seconds)

    @staticmethod
    def _f1_gap_label(row, position, live):
        gap = str((row or {}).get("gap") or "").strip()
        if position == 1:
            return "LEADER" if live or gap in {"", "-", "LEADER"} or ":" not in gap else gap
        if not gap or gap == "-":
            return ""
        if re.fullmatch(r"\d+(\.\d+)?", gap):
            return f"+{gap}"
        return gap


@lru_cache(maxsize=1)
def _circuit_shapes():
    """Jolpica circuitId -> the circuit's line (longer side 1, y down), from the bundled file."""
    try:
        with open(F1_CIRCUIT_SHAPES, encoding="utf-8") as handle:
            circuits = json.load(handle).get("circuits") or {}
    except (OSError, ValueError):
        return {}
    return {key: [tuple(point) for point in value.get("points") or []] for key, value in circuits.items()}


def _circuit_shape(circuit_id):
    points = _circuit_shapes().get(str(circuit_id or ""))
    return points if points and len(points) > 3 else None


def _luminance(rgb):
    """WCAG relative luminance of an RGB colour."""
    channels = [value / 255 for value in rgb[:3]]
    linear = [value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _logo_contrast(logo, background):
    """Contrast between a logo's mean colour (where it is mostly opaque) and ``background``;
    thin-lined marks count too, which an opaque-only average would miss."""
    mask = logo.getchannel("A").point(lambda value: 255 if value > 96 else 0)
    if not mask.getbbox():
        return 1.0
    mean = ImageStat.Stat(logo.convert("RGB"), mask=mask).mean
    high, low = sorted((_luminance(mean), _luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)
