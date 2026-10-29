from .common import *
from .common import _ACTIVE_COLORS, _safe_exception_text, _normalize_country_alias

SportsDashboard = None


class OffseasonRenderMixin:
    def _draw_offseason_hub_compact_panel(self, image, draw, bounds, selected, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        primary = (selected or {}).get("primary") or {}
        sport = str(primary.get("sport") or "MLB").upper()
        if sport == "WNBA":
            self._draw_wnba_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        elif sport == "NHL":
            self._draw_nhl_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        elif sport == "PGA":
            self._draw_pga_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        elif sport == "NFL":
            self._draw_nfl_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        elif sport == "NCAA":
            self._draw_ncaa_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        else:
            self._draw_mlb_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)

    def _draw_nfl_title_wordmark(self, image, x, y, max_width, max_height):
        wordmark = self._load_local_logo(
            LOCAL_NFL_TITLE_WORDMARK_PATH,
            (int(max_width), int(max_height)),
            alpha_threshold=8,
        )
        if not wordmark:
            return False
        image.paste(
            wordmark,
            (int(x), int(y + (int(max_height) - wordmark.height) / 2)),
            wordmark,
        )
        return True

    @staticmethod
    def _hub_sport_accent(sport):
        sport = str(sport or "").upper()
        if sport == "WNBA":
            return COLORS["wnba_accent"]
        if sport == "NHL":
            return COLORS["nhl_accent"]
        if sport == "PGA":
            return COLORS["pga_accent"]
        if sport == "NFL":
            return COLORS["nfl_accent"]
        if sport == "NCAA":
            return COLORS["ncaa_accent"]
        return COLORS["mlb_accent"]

    def _draw_hub_card_shell(self, draw, x1, y1, x2, y2, accent):
        draw.rounded_rectangle((x1 + 3, y1 + 3, x2 + 3, y2 + 3), radius=5, fill=self._blend(accent, COLORS["border"], 0.28))
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, y2 - 1), fill=accent)

    @staticmethod
    def _football_live_context_row(event):
        rows = SportsDashboard._football_compact_context_rows(event)
        return rows[0] if rows else None

    @staticmethod
    def _football_compact_context_rows(event, combine_line_with_tv=False):
        event = event or {}
        rows = []
        broadcast = str(event.get("broadcast") or "").strip()
        line = SportsDashboard._football_line_label(event)
        if broadcast:
            if combine_line_with_tv:
                rows.append(("TV", " / ".join(part for part in (broadcast, line) if part)))
                line = ""
            else:
                rows.append(("TV", broadcast))
        if line:
            rows.append(("SPREAD", line))
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue:
            rows.append(("VENUE", venue))
        return rows

    @staticmethod
    def _football_line_label(event):
        event = event or {}
        spread = str(event.get("spread") or "").strip()
        total = str(event.get("over_under") or "").strip()
        return " / ".join(part for part in (spread, total) if part)

    @staticmethod
    def _football_final_winner_label(event, sport):
        winner_side = SportsDashboard._football_winner_side(event)
        if not winner_side:
            return ""
        winner = SportsDashboard._football_display_team(event, winner_side, sport, full=True)
        score_a = SportsDashboard._coerce_int((event or {}).get("wins_a"))
        score_b = SportsDashboard._coerce_int((event or {}).get("wins_b"))
        if score_a is not None and score_b is not None and score_a != score_b:
            return f"{winner} \u80dc{abs(score_a - score_b)}\u5206"
        return f"{winner} \u80dc"

    @staticmethod
    def _football_winner_side(event):
        if SportsDashboard._hub_event_state(event) != "final":
            return ""
        event = event or {}
        if event.get("winner_a") is True and event.get("winner_b") is not True:
            return "a"
        if event.get("winner_b") is True and event.get("winner_a") is not True:
            return "b"
        score_a = SportsDashboard._coerce_int(event.get("wins_a"))
        score_b = SportsDashboard._coerce_int(event.get("wins_b"))
        if score_a is None or score_b is None or score_a == score_b:
            return ""
        return "a" if score_a > score_b else "b"

    @staticmethod
    def _ncaa_school_label(event, side, include_rank=False, full=False):
        prefix = "team_a" if side == "a" else "team_b"
        raw_event = event or {}
        code = str(raw_event.get(f"{prefix}_code") or raw_event.get(prefix) or "TBD").strip()
        fallback = str(raw_event.get(f"{prefix}_zh") or raw_event.get(prefix) or raw_event.get(f"{prefix}_name") or code or "TBD").strip()
        aliases = [
            raw_event.get(f"{prefix}_name"),
            raw_event.get(prefix),
            code,
        ]
        school = SportsDashboard._ncaa_display_school_name(
            code, fallback=fallback, aliases=aliases, full=full,
            team_id=raw_event.get(f"{prefix}_id"),
        )
        if include_rank:
            rank = (event or {}).get(f"{prefix}_rank")
            if rank:
                return f"#{rank} {school}"
        return school

    @staticmethod
    def _ncaa_matchup_label(event):
        away = SportsDashboard._ncaa_school_label(event, "a", include_rank=True)
        home = SportsDashboard._ncaa_school_label(event, "b", include_rank=True)
        if away and home:
            connector = "VS" if (event or {}).get("neutral_site") else "@"
            return f"{away} {connector} {home}"
        return ""

    @staticmethod
    def _ncaa_record_matchup_label(event):
        away = SportsDashboard._ncaa_school_label(event, "a", include_rank=True)
        home = SportsDashboard._ncaa_school_label(event, "b", include_rank=True)
        record_a = str((event or {}).get("record_a") or "").strip()
        record_b = str((event or {}).get("record_b") or "").strip()
        if record_a and record_b:
            return f"{away} {record_a} / {home} {record_b}"
        if record_a:
            return f"{away} {record_a}"
        if record_b:
            return f"{home} {record_b}"
        return ""

    @staticmethod
    def _ncaa_header_badge_label(event):
        event = event or {}
        ranks = [
            rank
            for rank in (
                SportsDashboard._lpl_int_value(event.get("team_a_rank")),
                SportsDashboard._lpl_int_value(event.get("team_b_rank")),
            )
            if rank and rank < 100
        ]
        if len(ranks) >= 2 and max(ranks) <= 25:
            return "TOP 25"
        if ranks:
            return "RANKED"
        if event.get("neutral_site"):
            return "NEUTRAL"
        return ""

    @staticmethod
    def _ncaa_meta_label(event):
        event = event or {}
        parts = []
        if event.get("neutral_site"):
            parts.append("NEUTRAL SITE")
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue:
            parts.append(venue)
        broadcast = str(event.get("broadcast") or "").strip()
        spread = str(event.get("spread") or "").strip()
        total = str(event.get("over_under") or "").strip()
        line_parts = []
        if broadcast:
            line_parts.append(f"TV {broadcast}")
        if spread:
            line_parts.append(f"SPREAD {spread}")
        if total:
            line_parts.append(total)
        if line_parts:
            parts.append(" / ".join(line_parts))
        return "  |  ".join(parts[:3]) or "COLLEGE FOOTBALL"

    @staticmethod
    def _football_possession_side_fill_key(event, side):
        event = event or {}
        if SportsDashboard._hub_event_state(event) != "live":
            return "text"
        prefix = "team_a" if side == "a" else "team_b"
        possession = str(event.get("possession") or "").strip().upper()
        if not possession:
            return "text"
        values = [
            event.get(f"{prefix}_code"),
            event.get(prefix),
            event.get(f"{prefix}_name"),
        ]
        for value in values:
            if possession == str(value or "").strip().upper():
                return "amber"
        return "text"

    @staticmethod
    def _football_display_team(event, side, sport, full=False):
        prefix = "team_a" if side == "a" else "team_b"
        raw_event = event or {}
        code = str(raw_event.get(f"{prefix}_code") or raw_event.get(prefix) or "TBD").strip()
        fallback = str(raw_event.get(prefix) or code or "TBD").strip() or "TBD"
        aliases = [
            raw_event.get(f"{prefix}_name"),
            raw_event.get(prefix),
            code,
        ]
        if sport == "NCAA":
            return SportsDashboard._ncaa_school_label(raw_event, side, include_rank=True, full=full)
        if sport == "NFL":
            team = str(raw_event.get(f"{prefix}_zh") or "").strip()
            if full:
                team = SportsDashboard._football_display_team_name(code, fallback=fallback, sport=sport, aliases=aliases, full=True)
            elif not team:
                team = SportsDashboard._football_display_team_name(code, fallback=fallback, sport=sport, aliases=aliases)
            return team
        return fallback

    @staticmethod
    def _mlb_winner_side(event):
        if SportsDashboard._hub_event_state(event) != "final":
            return ""
        event = event or {}
        score_a = SportsDashboard._coerce_int(event.get("wins_a"))
        score_b = SportsDashboard._coerce_int(event.get("wins_b"))
        if score_a is None or score_b is None or score_a == score_b:
            return ""
        return "a" if score_a > score_b else "b"

    def _draw_pga_leader_scorecard_strip(self, draw, x1, y, x2, leader):
        items = SportsDashboard._pga_leader_scorecard_items(leader)
        if not items:
            self._draw_sport_info_icon(draw, "SCORE", x1, y + 2, COLORS["pga_accent"])
            label, label_font = self._fit_text(draw, "SCORECARD", x2 - x1 - 18, 8, bold=True, min_size=6)
            draw.text((x1 + 15, y + 2), label, font=label_font, fill=COLORS["muted"])
            return
        gap = 3
        count = len(items)
        cell_w = max(34, int((x2 - x1 - gap * (count - 1)) / count))
        for index, (label, value, icon, accent) in enumerate(items):
            left = int(x1 + index * (cell_w + gap))
            right = int(x2 if index == count - 1 else left + cell_w)
            self._draw_pga_leader_scorecard_cell(draw, (left, y, right, y + 12), label, value, icon, accent)

    def _draw_pga_leader_scorecard_cell(self, draw, box, label, value, icon, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, icon, x1 + 3, y1 + 2, accent)
        label_text, label_font = self._fit_text(draw, label, 20, 6, bold=True, min_size=5)
        draw.text((x1 + 15, y1 + 2), label_text, font=label_font, fill=accent)
        value_text, value_font = self._fit_text(draw, str(value or "TBD"), max(14, x2 - x1 - 39), 7, bold=True, min_size=5)
        self._draw_right_aligned(draw, (x2 - 3, y1 + 2), value_text, value_font, COLORS["text"])

    @staticmethod
    def _pga_leader_scorecard_items(leader):
        leader = leader or {}
        items = []
        country = str(leader.get("country") or "").strip()
        country_label = SportsDashboard._pga_country_display_label(country)
        if country_label:
            items.append(("NAT", country_label, "GOLF", COLORS["pga_accent"]))
        round_no = SportsDashboard._lpl_int_value(leader.get("round"))
        if round_no:
            items.append(("RND", f"R{round_no}", "PERIOD", COLORS["pga_accent"]))
        today = str(leader.get("today") or "").strip()
        if today:
            items.append(("DAY", today, "SCORE", COLORS["amber"] if today.startswith("-") else COLORS["pga_accent"]))
        strokes = str(leader.get("strokes") or "").strip()
        if strokes:
            items.append(("CARD", strokes, "SCORE", COLORS["pga_accent"]))
        return items[:4]

    def _draw_pga_compact_event_info_section(self, draw, x1, x2, y1, y2, event, now):
        self._draw_hub_section_header(draw, x1, x2, y1, "EVENT INFO", COLORS["pga_accent"])
        row_y = y1 + 22
        for index, (icon, label, value) in enumerate(self._pga_compact_event_info_rows(event, now)):
            top = row_y + index * 17
            if top + 14 > y2:
                break
            self._draw_pga_info_row(draw, x1, x2, top, icon, label, value)

    @staticmethod
    def _pga_compact_event_info_rows(event, now):
        event = event or {}
        rows = []
        name = str(event.get("name") or "").strip()
        if name:
            rows.append(("GOLF", "EVENT", name))
        status = str(event.get("status_text") or "").strip()
        if not status:
            status = SportsDashboard._hub_event_time_label(event, now)
        if status == "TBD":
            status = ""
        venue = str(event.get("venue") or "").strip()
        detail = " / ".join(part for part in (status, venue) if part)
        if not detail:
            detail = SportsDashboard._pga_event_window_label(event)
        if detail:
            rows.append(("CLOCK", "STATUS", detail))
        return rows[:2]

    def _draw_pga_info_row(self, draw, x1, x2, y, icon, label, value):
        draw.line((x1, y - 2, x2, y - 2), fill=COLORS["line"], width=1)
        self._draw_sport_info_icon(draw, icon, x1 + 2, y + 1, COLORS["pga_accent"])
        label, label_font = self._fit_text(draw, label, 48, 8, bold=True, min_size=6)
        draw.text((x1 + 15, y), label, font=label_font, fill=COLORS["muted"])
        value, value_font = self._fit_text(draw, value or "TBD", x2 - x1 - 72, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 2, y), value, value_font, COLORS["text"])

    @staticmethod
    def _pga_event_window_label(event):
        start = (event or {}).get("start")
        end = (event or {}).get("end")
        if start and end:
            if start.date() == end.date():
                return f"{start.strftime('%m/%d')} {SportsDashboard._format_time(start)}"
            if start.month == end.month:
                return f"{start.strftime('%m/%d')}-{end.strftime('%d')}"
            return f"{start.strftime('%m/%d')}-{end.strftime('%m/%d')}"
        if start:
            return f"{start.strftime('%m/%d')} {SportsDashboard._format_time(start)}"
        if end:
            return f"THRU {end.strftime('%m/%d')}"
        return ""

    def _draw_hub_section_header(self, draw, x1, x2, y, title, accent):
        draw.rectangle((x1, y + 2, x1 + 8, y + 17), fill=accent, outline=COLORS["border"], width=1)
        title, title_font = self._fit_text(draw, title, x2 - x1 - 18, 13, bold=True, min_size=8)
        draw.text((x1 + 13, y - 2), title, font=title_font, fill=COLORS["text"])
        draw.line((x1, y + 19, x2, y + 19), fill=COLORS["border"], width=1)
