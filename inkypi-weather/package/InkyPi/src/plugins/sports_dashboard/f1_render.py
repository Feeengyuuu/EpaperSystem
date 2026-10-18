"""Formula 1 panel ("race weekend").

A band with the round, the Grand Prix (in Chinese), the circuit and a
countdown to the next session; a weekend timeline with one node per session
(done, live, next, later; the race node is chequered); the drivers'
championship as points bars in team colours; and either the live session
order or the last race's top finishers.
"""

from .common import COLORS, LOCAL_F1_TITLE_WORDMARK_PATH, datetime, re
from .scorebug_kit import ScorebugSpec

SportsDashboard = None

F1_BAND_HEIGHT = 52
F1_TIMELINE_HEIGHT = 50
F1_ROW_HEIGHT = 15
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


class F1RenderMixin:
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
        self._draw_f1_standings(draw, (left, lower, middle - 6, bottom), selected.get("driver_standings") or [], spec)
        leaderboard = selected.get("leaderboard") or []
        if live and leaderboard:
            session = (selected.get("live_session") or {}).get("label") or "LIVE"
            self._draw_f1_order(draw, (middle + 14, lower, right, bottom), f"LIVE · {session}", leaderboard, spec, live=True)
        else:
            result = selected.get("last_result") or {}
            title = SportsDashboard._f1_short_race_name(result.get("race_name")) or "LAST RACE"
            self._draw_f1_order(draw, (middle + 14, lower, right, bottom), title, result.get("top") or [], spec)

    def _draw_f1_header(self, image, draw, bounds, selected, source_state):
        x1, y1, x2, _y2 = [int(value) for value in bounds]
        header_y = y1 + 8
        self._draw_f1_logo(image, draw, x1 + 15, header_y - 2, 74, 34)
        if not self._draw_local_wordmark(image, LOCAL_F1_TITLE_WORDMARK_PATH, x1 + 96, header_y, 142, 20):
            title, title_font = self._fit_text(draw, "FORMULA 1", 142, 17, bold=True, min_size=12)
            draw.text((x1 + 96, header_y + 1), title, font=title_font, fill=COLORS["text"])
        source_label, source_font = self._fit_text(draw, self._f1_source_label(selected, source_state), 124, 10, bold=True, min_size=7)
        draw.text((x1 + 97, header_y + 22), source_label, font=source_font, fill=COLORS["muted"])
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
        circuit = self._draw_event_mark(
            image, SportsDashboard._f1_circuit_mark_path(race), (count_x1 - 96, y1 + 4, count_x1 - 8, y2 - 4), align="right", line_art=True
        )
        text_x2 = circuit[0] - 8 if circuit else count_x1 - 6
        name = SportsDashboard._f1_grand_prix_zh(race.get("race_name"))
        name, name_font = self._fit_text(draw, name, text_x2 - badge[2] - 10, 17, bold=True, min_size=11)
        self._draw_text_in_box(draw, (badge[2] + 10, y1 + 6, text_x2, y1 + 27), name, name_font, COLORS["text"])
        city = ", ".join(part for part in (race.get("locality"), race.get("country")) if part)
        round_label = f"ROUND {race.get('round')}" if flag and race.get("round") else ""
        place = "  ·  ".join(part for part in (round_label, race.get("circuit_name"), city) if part)
        place, place_font = self._fit_text_ellipsis(draw, place, text_x2 - badge[2] - 10, 9, bold=False, min_size=7)
        self._draw_text_in_box(draw, (badge[2] + 10, y1 + 29, text_x2, y1 + 41), place, place_font, COLORS["text"])
        label, value, value_key = SportsDashboard._f1_countdown_block(selected, now)
        self._draw_text_in_box(draw, (count_x1, y1 + 6, x2 - 10, y1 + 18), label, self._font(9, True), COLORS[spec.live] if live_session else COLORS["text"], align="right")
        value, value_font = self._fit_text(draw, value, x2 - 10 - count_x1, 17, bold=True, min_size=10)
        self._draw_text_in_box(draw, (count_x1, y1 + 19, x2 - 10, y1 + 42), value, value_font, COLORS[value_key], align="right")

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
            self._draw_centered_in_box(draw, (position - 26, y1, position + 26, y1 + 12), label, self._font(9, state in {"live", "next"} or label == "RACE"), COLORS[spec.live] if state == "live" else COLORS["text"])
            self._draw_f1_session_node(draw, position, axis_y, state, label == "RACE", spec)
            start = session.get("start")
            if isinstance(start, datetime):
                when = f"{start.strftime('%a').upper()} {SportsDashboard._format_time(start)}"
                when, when_font = self._fit_text(draw, when, int(step) - 2 if len(sessions) > 1 else 80, 9, bold=state == "next", min_size=7)
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

    def _draw_f1_standings(self, draw, box, standings, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        y = self._draw_kit_column_header(draw, x1, y1, x2, spec, "DRIVERS")
        if not standings:
            self._draw_text_in_box(draw, (x1 + 4, y, x2, y + 14), "积分榜更新中", self._font(9, True), COLORS["muted"])
            return
        leader_points = max(1.0, SportsDashboard._f1_points(standings[0].get("points")))
        for row in standings:
            if y + F1_ROW_HEIGHT > y2 + 1:
                break
            ink_key = SportsDashboard._f1_team_ink_key(row.get("constructor"))
            self._draw_text_in_box(draw, (x1, y, x1 + 14, y + 13), str(row.get("position") or ""), self._font(10, True), COLORS["text"])
            draw.rectangle((x1 + 17, y + 2, x1 + 19, y + 11), fill=COLORS[ink_key])
            self._draw_text_in_box(draw, (x1 + 23, y, x1 + 55, y + 13), str(row.get("driver_code") or ""), self._font(11, True), COLORS["text"])
            points = SportsDashboard._f1_points(row.get("points"))
            points_text = str(row.get("points") or "0").removesuffix(".0")
            points_font = self._font(10, True)
            points_w = self._text_width(draw, points_text, points_font)
            bar_x1, bar_x2 = x1 + 58, x2 - points_w - 6
            if bar_x2 - bar_x1 > 10:
                filled = bar_x1 + int((bar_x2 - bar_x1) * points / leader_points)
                draw.rectangle((bar_x1, y + 4, bar_x2, y + 9), fill=COLORS["panel"], outline=COLORS["line"], width=1)
                draw.rectangle((bar_x1, y + 4, max(bar_x1 + 1, filled), y + 9), fill=COLORS[ink_key], outline=COLORS["border"], width=1)
            self._draw_text_in_box(draw, (x2 - points_w, y, x2, y + 13), points_text, points_font, COLORS["text"], align="right")
            y += F1_ROW_HEIGHT

    def _draw_f1_order(self, draw, box, title, rows, spec, live=False):
        x1, y1, x2, y2 = [int(value) for value in box]
        y = self._draw_kit_column_header(draw, x1, y1, x2, spec, title)
        if not rows:
            self._draw_text_in_box(draw, (x1 + 4, y, x2, y + 14), "等待成绩", self._font(9, True), COLORS["muted"])
            return
        for index, row in enumerate(rows):
            if y + F1_ROW_HEIGHT > y2 + 1:
                break
            position = row.get("position") or index + 1
            ink_key = SportsDashboard._f1_team_ink_key(row.get("constructor") or row.get("team"))
            badge_fill = COLORS["amber"] if position == 1 else COLORS[spec.tag]
            draw.rounded_rectangle((x1, y + 1, x1 + 20, y + 13), radius=3, fill=badge_fill, outline=COLORS["border"], width=1)
            self._draw_centered_in_box(draw, (x1, y + 1, x1 + 20, y + 13), f"P{position}", self._font(8, True), COLORS["text"])
            draw.rectangle((x1 + 24, y + 2, x1 + 26, y + 11), fill=COLORS[ink_key])
            gap = SportsDashboard._f1_gap_label(row, position, live)
            gap_font = self._font(9, position == 1)
            gap_w = self._text_width(draw, gap, gap_font) if gap else 0
            name = str(row.get("driver_code") or "")
            surname = str(row.get("driver_name") or "").split(" ")[-1] if row.get("driver_name") else ""
            label = f"{name} {surname}".strip() if surname and surname.upper()[:3] != name else name
            label, label_font = self._fit_text_ellipsis(draw, label, x2 - gap_w - 8 - (x1 + 30), 10, bold=True, min_size=8)
            self._draw_text_in_box(draw, (x1 + 30, y, x2 - gap_w - 6, y + 13), label, label_font, COLORS["text"])
            if gap:
                self._draw_text_in_box(draw, (x2 - gap_w, y, x2, y + 13), gap, gap_font, COLORS["text"], align="right")
            y += F1_ROW_HEIGHT

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
    def _f1_short_race_name(name):
        text = SportsDashboard._f1_grand_prix_zh(name) if name else ""
        return f"上站 · {text}" if text else ""

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
    def _f1_gap_label(row, position, live):
        gap = str((row or {}).get("gap") or "").strip()
        if position == 1:
            return "LEADER" if live or gap in {"", "-", "LEADER"} or ":" not in gap else gap
        if not gap or gap == "-":
            return ""
        if re.fullmatch(r"\d+(\.\d+)?", gap):
            return f"+{gap}"
        return gap
