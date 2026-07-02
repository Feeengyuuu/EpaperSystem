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
        elif sport == "PGA":
            self._draw_pga_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        elif sport == "NFL":
            self._draw_nfl_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        elif sport == "NCAA":
            self._draw_ncaa_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)
        else:
            self._draw_mlb_standalone_panel(image, draw, (x1, y1, x2, y2), primary, source_state, now)

    def _draw_mlb_standalone_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["mlb_accent"], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "MLB", card, source_state)
        content_y = y1 + 58
        bottom = y2 - 8
        split_x = x1 + max(300, min(330, int((x2 - x1 + 1) * 0.59)))
        left = (x1 + 12, content_y, split_x - 10, bottom)
        right = (split_x + 6, content_y, x2 - 12, bottom)
        self._draw_vertical_split(draw, split_x, content_y - 6, bottom)
        self._draw_mlb_main_card(image, draw, left, card, now)
        self._draw_mlb_side_column(image, draw, right, card, now)

    def _draw_pga_standalone_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["pga_accent"], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "PGA", card, source_state)
        content_y = y1 + 58
        bottom = y2 - 8
        split_x = x1 + max(252, min(286, int((x2 - x1 + 1) * 0.50)))
        left = (x1 + 12, content_y, split_x - 10, bottom)
        right = (split_x + 6, content_y, x2 - 12, bottom)
        self._draw_vertical_split(draw, split_x, content_y - 6, bottom)
        self._draw_pga_event_card(image, draw, left, card, now)
        self._draw_pga_leaderboard_column(image, draw, right, card, now)

    def _draw_nfl_standalone_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["nfl_accent"], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "NFL", card, source_state)
        content_y = y1 + 58
        bottom = y2 - 8
        split_x = x1 + max(304, min(334, int((x2 - x1 + 1) * 0.60)))
        left = (x1 + 12, content_y, split_x - 10, bottom)
        right = (split_x + 6, content_y, x2 - 12, bottom)
        self._draw_vertical_split(draw, split_x, content_y - 6, bottom)
        self._draw_nfl_main_card(image, draw, left, card, now)
        self._draw_football_side_column(image, draw, right, card, now, "NFL")

    def _draw_ncaa_standalone_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["ncaa_accent"], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "NCAA", card, source_state)
        content_y = y1 + 58
        bottom = y2 - 8
        split_x = x1 + max(304, min(334, int((x2 - x1 + 1) * 0.60)))
        left = (x1 + 12, content_y, split_x - 10, bottom)
        right = (split_x + 6, content_y, x2 - 12, bottom)
        self._draw_vertical_split(draw, split_x, content_y - 6, bottom)
        self._draw_ncaa_main_card(image, draw, left, card, now)
        self._draw_ncaa_side_column(image, draw, right, card, now)

    def _draw_ncaa_main_card(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        status = str((card or {}).get("status") or "NEXT").upper()
        accent = COLORS["ncaa_live"] if status == "LIVE" else COLORS["ncaa_accent"]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        tag, tag_font = self._fit_text(draw, f"NCAA {status}", 98, 12, bold=True, min_size=8)
        draw.rectangle((x1 + 18, y1 + 11, x1 + 120, y1 + 29), fill=COLORS["ncaa_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 23, y1 + 12), tag, font=tag_font, fill=COLORS["text"])
        week = self._football_header_week_label(card, event)
        week, week_font = self._fit_text(draw, week or self._hub_event_time_label(event, now), 82, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 13), week, week_font, COLORS["muted"])
        header_badge = SportsDashboard._ncaa_header_badge_label(event)
        if header_badge:
            badge, badge_font = self._fit_text(draw, header_badge, 62, 9, bold=True, min_size=7)
            draw.text((x1 + 127, y1 + 16), badge, font=badge_font, fill=COLORS["amber"])

        board_y1 = y1 + 39
        board_y2 = y2 - 58
        board_fill = self._blend(COLORS["ncaa_accent"], COLORS["panel"], 0.18)
        draw.rounded_rectangle((x1 + 16, board_y1, x2 - 16, board_y2), radius=5, fill=board_fill, outline=COLORS["border"], width=1)
        center_x = (x1 + x2) / 2
        self._draw_ncaa_field_backdrop(draw, x1 + 16, board_y1, x2 - 16, board_y2)

        away_fill = COLORS[SportsDashboard._football_team_side_fill_key(event, "a")]
        home_fill = COLORS[SportsDashboard._football_team_side_fill_key(event, "b")]
        self._draw_ncaa_team_block(
            image,
            draw,
            x1 + 28,
            board_y1 + 12,
            center_x - 28,
            event,
            "a",
            team_fill=away_fill,
        )
        self._draw_ncaa_team_block(
            image,
            draw,
            center_x + 28,
            board_y1 + 12,
            x2 - 28,
            event,
            "b",
            align="right",
            team_fill=home_fill,
        )
        score = self._hub_score_label(event)
        score, score_font = self._fit_text(draw, score, 86, 29 if score != "VS" else 23, bold=True, min_size=16)
        self._draw_centered(draw, (center_x, board_y1 + 44), score, score_font, COLORS["text"])
        if self._hub_event_state(event) != "scheduled":
            live_label = str(event.get("status_text") or self._hub_event_time_label(event, now)).upper()
            live_label, live_font = self._fit_text(draw, live_label, 86, 12, bold=True, min_size=8)
            self._draw_centered(draw, (center_x, board_y1 + 18), live_label, live_font, COLORS["amber"] if status == "LIVE" else COLORS["muted"])

        if self._hub_event_state(event) == "final":
            self._draw_ncaa_final_context(draw, x1, y2, x2, board_y2, event)
            return
        if self._hub_event_state(event) == "scheduled":
            self._draw_ncaa_pregame_context(draw, x1, y2, x2, board_y2, event, now)
            return
        self._draw_ncaa_live_context(draw, x1, y2, x2, board_y2, event)

    def _draw_ncaa_field_backdrop(self, draw, x1, y1, x2, y2):
        center_x = (x1 + x2) / 2
        accent = self._blend(COLORS["ncaa_accent"], COLORS["panel"], 0.42)
        gold = self._blend(COLORS["amber"], COLORS["panel"], 0.38)
        draw.line((center_x, y1 + 7, center_x, y2 - 7), fill=COLORS["line"], width=1)
        for y in range(y1 + 14, y2 - 8, 18):
            draw.line((x1 + 8, y, x1 + 16, y), fill=COLORS["line"], width=1)
            draw.line((x2 - 16, y, x2 - 8, y), fill=COLORS["line"], width=1)
        for index, top in enumerate(range(y1 + 7, y2 - 16, 15)):
            color = gold if index % 2 == 0 else accent
            draw.line((x1 + 7, top, x1 + 18, top + 7), fill=color, width=1)
            draw.line((x2 - 18, top + 7, x2 - 7, top), fill=color, width=1)
        badge_y = max(y1 + 56, y2 - 25)
        draw.rounded_rectangle((center_x - 24, badge_y, center_x + 24, badge_y + 13), radius=3, outline=accent, width=1)
        label, label_font = self._fit_text(draw, "CFB", 36, 8, bold=True, min_size=6)
        self._draw_centered(draw, (center_x, badge_y + 2), label, label_font, COLORS["ncaa_accent"])

    def _draw_ncaa_team_block(self, image, draw, x1, y, x2, event, side, align="left", team_fill=None):
        prefix = "team_a" if side == "a" else "team_b"
        code = str((event or {}).get(f"{prefix}_code") or (event or {}).get(prefix) or "TBD").strip().upper()
        rank = (event or {}).get(f"{prefix}_rank")
        school = self._ncaa_school_label(event, side, full=True)
        record = str((event or {}).get("record_a" if side == "a" else "record_b") or "").strip()
        logo_size = 20
        text_fill = team_fill or COLORS["text"]
        rank_fill = team_fill or COLORS["ncaa_accent"]
        if align == "right":
            logo_x = int(x2 - logo_size)
            self._draw_team_logo(image, draw, (event or {}).get(f"{prefix}_logo"), logo_x, y, logo_size, code)
            text_x2 = logo_x - 4
            rank_text = f"#{rank}" if rank else code
            rank_text, rank_font = self._fit_text(draw, rank_text, 36, 10, bold=True, min_size=7)
            self._draw_right_aligned(draw, (text_x2, y), rank_text, rank_font, rank_fill)
            school_text, school_font = self._fit_text(draw, school, max(36, text_x2 - x1), 17, bold=True, min_size=10)
            self._draw_right_aligned(draw, (text_x2, y + 15), school_text, school_font, text_fill)
            code_text = " / ".join(part for part in (code, record) if part)
            code_text, code_font = self._fit_text(draw, code_text, max(36, text_x2 - x1), 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (text_x2, y + 36), code_text, code_font, COLORS["muted"])
            return
        self._draw_team_logo(image, draw, (event or {}).get(f"{prefix}_logo"), x1, y, logo_size, code)
        text_x1 = x1 + logo_size + 5
        rank_text = f"#{rank}" if rank else code
        rank_text, rank_font = self._fit_text(draw, rank_text, 36, 10, bold=True, min_size=7)
        draw.text((text_x1, y), rank_text, font=rank_font, fill=rank_fill)
        school_text, school_font = self._fit_text(draw, school, max(36, x2 - text_x1), 17, bold=True, min_size=10)
        draw.text((text_x1, y + 15), school_text, font=school_font, fill=text_fill)
        code_text = " / ".join(part for part in (code, record) if part)
        code_text, code_font = self._fit_text(draw, code_text, max(36, x2 - text_x1), 8, bold=True, min_size=6)
        draw.text((text_x1, y + 36), code_text, font=code_font, fill=COLORS["muted"])

    def _draw_ncaa_live_context(self, draw, x1, y2, x2, board_y2, event):
        last_play = str((event or {}).get("last_play") or "").strip()
        has_play = bool(last_play)
        context_y = max(board_y2 + (4 if has_play else 7), y2 - (58 if has_play else 51))
        box_bottom = context_y + (43 if has_play else 31)
        draw.rounded_rectangle((x1 + 16, context_y, x2 - 16, box_bottom), radius=4, fill=COLORS["ncaa_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "DOWN", x1 + 25, context_y + 6, COLORS["ncaa_accent"])
        label, label_font = self._fit_text(draw, "COLLEGE DRIVE", 104, 9, bold=True, min_size=7)
        draw.text((x1 + 40, context_y + 4), label, font=label_font, fill=COLORS["ncaa_accent"])
        possession = SportsDashboard._football_possession_display_label(event, "NCAA")
        if possession:
            pos, pos_font = self._fit_text(draw, f"POS {possession}", 70, 9, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 25, context_y + 4), pos, pos_font, COLORS["amber"])
        self._draw_ncaa_live_drive_chips(draw, x1 + 24, context_y + 16, x2 - 24, event)
        if has_play:
            self._draw_ncaa_last_play_strip(draw, x1 + 24, context_y + 32, x2 - 24, last_play)
        meta = self._ncaa_main_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        meta_box = (x1 + 20, y2 - 12, x2 - 20, y2 - 2) if has_play else (x1 + 20, y2 - 18, x2 - 20, y2 - 4)
        self._draw_centered_in_box(draw, meta_box, meta, meta_font, COLORS["muted"])

    def _draw_ncaa_live_drive_chips(self, draw, x1, y, x2, event):
        down = str((event or {}).get("down_distance") or "").strip().upper()
        field = str((event or {}).get("yard_line") or (event or {}).get("note") or "").strip()
        last_play = str((event or {}).get("last_play") or "").strip()
        items = []
        if down:
            items.append(("DOWN", down, COLORS["amber"]))
        if field:
            items.append(("FIELD", field, COLORS["ncaa_accent"]))
        if not items:
            if last_play:
                items.append(("PLAY", "LAST PLAY", COLORS["amber"]))
            else:
                items.append(("DOWN", "LIVE DRIVE", COLORS["amber"]))
        if len(items) == 1:
            self._draw_ncaa_live_drive_chip(draw, (x1, y, x2, y + 14), items[0][0], items[0][1], items[0][2])
            return
        gap = 6
        mid = int((x1 + x2) / 2)
        self._draw_ncaa_live_drive_chip(draw, (x1, y, mid - gap // 2, y + 14), items[0][0], items[0][1], items[0][2])
        self._draw_ncaa_live_drive_chip(draw, (mid + gap // 2, y, x2, y + 14), items[1][0], items[1][1], items[1][2])

    def _draw_ncaa_live_drive_chip(self, draw, box, label, value, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, label, x1 + 5, y1 + 3, accent)
        label_text, label_font = self._fit_text(draw, label, 34, 6, bold=True, min_size=5)
        draw.text((x1 + 18, y1 + 3), label_text, font=label_font, fill=accent)
        value_text, value_font = self._fit_text(draw, str(value or "TBD"), max(24, x2 - x1 - 58), 8, bold=True, min_size=6)
        self._draw_right_aligned(draw, (x2 - 5, y1 + 3), value_text, value_font, COLORS["text"])

    def _draw_ncaa_last_play_strip(self, draw, x1, y, x2, play):
        play = str(play or "").strip()
        if not play:
            return
        y = int(y)
        draw.rounded_rectangle((x1, y, x2, y + 10), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "PLAY", x1 + 5, y + 1, COLORS["ncaa_accent"])
        label, label_font = self._fit_text(draw, "PLAY", 28, 6, bold=True, min_size=5)
        draw.text((x1 + 18, y + 1), label, font=label_font, fill=COLORS["ncaa_accent"])
        play_text, play_font = self._fit_text(draw, play, max(44, x2 - x1 - 58), 7, bold=True, min_size=5)
        self._draw_right_aligned(draw, (x2 - 5, y + 1), play_text, play_font, COLORS["text"])

    def _draw_ncaa_pregame_context(self, draw, x1, y2, x2, board_y2, event, now):
        context_y = max(board_y2 + 7, y2 - 51)
        draw.rounded_rectangle((x1 + 16, context_y, x2 - 16, context_y + 29), radius=4, fill=COLORS["ncaa_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "KICK", x1 + 25, context_y + 6, COLORS["ncaa_accent"])
        label, label_font = self._fit_text(draw, "KICKOFF", 72, 10, bold=True, min_size=7)
        draw.text((x1 + 40, context_y + 5), label, font=label_font, fill=COLORS["ncaa_accent"])
        kick = SportsDashboard._football_kick_label(event, now)
        kick, kick_font = self._fit_text(draw, kick, 92, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, context_y + 5), kick, kick_font, COLORS["text"])
        site = SportsDashboard._football_ncaa_site_label(event)
        if site:
            site, site_font = self._fit_text(draw, site, x2 - x1 - 56, 8, bold=True, min_size=6)
            self._draw_centered(draw, ((x1 + x2) / 2, context_y + 21), site, site_font, COLORS["muted"])
        meta = self._ncaa_main_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_ncaa_final_context(self, draw, x1, y2, x2, board_y2, event):
        context_y = max(board_y2 + 7, y2 - 51)
        draw.rounded_rectangle((x1 + 16, context_y, x2 - 16, context_y + 29), radius=4, fill=COLORS["ncaa_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "SCORE", x1 + 25, context_y + 6, COLORS["ncaa_accent"])
        label, label_font = self._fit_text(draw, "FINAL", 64, 11, bold=True, min_size=8)
        draw.text((x1 + 40, context_y + 5), label, font=label_font, fill=COLORS["ncaa_accent"])
        date_label = ((event or {}).get("start").strftime("%m/%d") if (event or {}).get("start") else "RESULT")
        date_label, date_font = self._fit_text(draw, date_label, 76, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, context_y + 5), date_label, date_font, COLORS["text"])
        site = SportsDashboard._football_ncaa_site_label(event)
        if site:
            site, site_font = self._fit_text(draw, site, x2 - x1 - 56, 8, bold=True, min_size=6)
            self._draw_centered(draw, ((x1 + x2) / 2, context_y + 21), site, site_font, COLORS["muted"])
        meta = self._ncaa_main_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_ncaa_side_column(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        main = (card or {}).get("main") or {}
        upcoming = list((card or {}).get("upcoming") or [])
        recent = list((card or {}).get("recent") or [])
        main_state = self._hub_event_state(main)
        ranked_upcoming = sorted(upcoming, key=SportsDashboard._ncaa_ranked_watch_sort_key)
        if main_state == "live" and (upcoming or recent):
            self._draw_football_info_section(
                draw,
                x1,
                x2,
                y1,
                "COLLEGE DRIVE",
                self._football_live_drive_rows(main, include_context=True, sport="NCAA"),
                COLORS["ncaa_accent"],
            )
            second_y = min(y2 - 76, y1 + 104)
            if upcoming:
                self._draw_hub_section_header(draw, x1, x2, second_y, "RANKED WATCH", COLORS["ncaa_accent"])
                row_y = second_y + 24
                for index, event in enumerate(ranked_upcoming[:2]):
                    self._draw_ncaa_small_row(image, draw, x1, x2, row_y + index * 31, event, True)
            else:
                self._draw_hub_section_header(draw, x1, x2, second_y, "RECENT", COLORS["ncaa_accent"])
                row_y = second_y + 24
                for index, event in enumerate(recent[:2]):
                    self._draw_ncaa_small_row(image, draw, x1, x2, row_y + index * 28, event, False)
            return
        if not upcoming and not recent and main_state == "live":
            self._draw_ncaa_live_side_fallback(draw, x1, y1, x2, y2, main, now)
            return
        if not upcoming and not recent and main_state == "final":
            self._draw_football_info_section(draw, x1, x2, y1, "FINAL SNAP", self._football_final_snap_rows(main, "NCAA"), COLORS["ncaa_accent"])
            return
        self._draw_hub_section_header(draw, x1, x2, y1, "RANKED WATCH", COLORS["ncaa_accent"])
        row_y = y1 + 24
        if upcoming:
            for index, event in enumerate(ranked_upcoming[:3]):
                self._draw_ncaa_small_row(image, draw, x1, x2, row_y + index * 31, event, True)
        else:
            draw.text((x1 + 10, row_y + 6), "No NCAA schedule", font=self._font(10, True), fill=COLORS["muted"])
        recent_y = min(y2 - 76, row_y + max(1, min(3, len(upcoming))) * 31 + 10)
        if recent:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", COLORS["ncaa_accent"])
            recent_row_y = recent_y + 24
            for index, event in enumerate(recent[:2]):
                self._draw_ncaa_small_row(image, draw, x1, x2, recent_row_y + index * 28, event, False)
        elif main_state == "live":
            self._draw_football_info_section(draw, x1, x2, recent_y, "COLLEGE DRIVE", self._football_live_drive_rows(main, include_context=True, sport="NCAA"), COLORS["ncaa_accent"])
        elif main_state == "scheduled":
            self._draw_football_info_section(draw, x1, x2, recent_y, "GAME INFO", self._ncaa_game_info_rows(main, now), COLORS["ncaa_accent"])
        elif main_state == "final":
            self._draw_football_info_section(draw, x1, x2, recent_y, "FINAL SNAP", self._football_final_snap_rows(main, "NCAA"), COLORS["ncaa_accent"])
        else:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", COLORS["ncaa_accent"])
            draw.text((x1 + 10, recent_y + 30), "No recent results", font=self._font(10, True), fill=COLORS["muted"])

    def _draw_ncaa_live_side_fallback(self, draw, x1, y1, x2, y2, event, now):
        self._draw_football_info_section(
            draw,
            x1,
            x2,
            y1,
            "COLLEGE DRIVE",
            self._football_live_drive_rows(event, sport="NCAA"),
            COLORS["ncaa_accent"],
        )
        self._draw_football_info_section(
            draw,
            x1,
            x2,
            min(y2 - 92, y1 + 104),
            "GAME INFO",
            self._football_live_game_info_rows(event, "NCAA"),
            COLORS["ncaa_accent"],
        )

    def _draw_ncaa_small_row(self, image, draw, x1, x2, y, event, show_time):
        draw.rounded_rectangle((x1, y, x2, y + 25), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + 24), fill=COLORS["ncaa_accent"])
        left = self._format_time(event["start"]) if show_time and event.get("start") else (event["start"].strftime("%m/%d") if event.get("start") else "TBD")
        left, left_font = self._fit_text(draw, left, 42, 9, bold=True, min_size=7)
        draw.text((x1 + 10, y + 3), left, font=left_font, fill=COLORS["muted"])
        logo_size = 11
        matchup = self._ncaa_score_matchup_label(event)
        matchup, matchup_font = self._fit_text(draw, matchup, x2 - x1 - 96, 10, bold=True, min_size=7)
        self._draw_small_row_team_logos(image, draw, x1, x2, y + 3, event, matchup, matchup_font, x2 - 25, logo_size)
        self._draw_right_aligned(draw, (x2 - 25, y + 3), matchup, matchup_font, COLORS["text"])
        note = self._football_small_note_label(event)
        if note:
            note, note_font = self._fit_text(draw, note, x2 - x1 - 84, 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 25, y + 15), note, note_font, COLORS["muted"])

    @staticmethod
    def _hub_sport_accent(sport):
        sport = str(sport or "").upper()
        if sport == "WNBA":
            return COLORS["wnba_accent"]
        if sport == "PGA":
            return COLORS["pga_accent"]
        if sport == "NFL":
            return COLORS["nfl_accent"]
        if sport == "NCAA":
            return COLORS["ncaa_accent"]
        return COLORS["mlb_accent"]

    @staticmethod
    def _football_context_fill_key(sport):
        sport = str(sport or "").upper()
        if sport == "NFL":
            return "nfl_field_tint"
        if sport == "NCAA":
            return "ncaa_field_tint"
        return "panel_blue"

    def _draw_hub_card_shell(self, draw, x1, y1, x2, y2, accent):
        draw.rounded_rectangle((x1 + 3, y1 + 3, x2 + 3, y2 + 3), radius=5, fill=self._blend(accent, COLORS["border"], 0.28))
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, y2 - 1), fill=accent)

    def _draw_mlb_main_card(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        accent = COLORS["mlb_live"] if card.get("status") == "LIVE" else COLORS["mlb_accent"]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        tag, tag_font = self._fit_text(draw, f"MLB {card.get('status') or 'NEXT'}", 92, 12, bold=True, min_size=8)
        draw.rectangle((x1 + 18, y1 + 11, x1 + 112, y1 + 29), fill=COLORS["mlb_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 23, y1 + 12), tag, font=tag_font, fill=COLORS["text"])
        right_label = self._hub_event_time_label(event, now)
        right_label, right_font = self._fit_text(draw, right_label, 94, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 13), right_label, right_font, COLORS["muted"])
        center_x = (x1 + x2) / 2
        top_y = y1 + 45
        away_label = self._mlb_display_team_from_event(event, "a", full=True)
        home_label = self._mlb_display_team_from_event(event, "b", full=True)
        away_fill = COLORS[SportsDashboard._mlb_team_side_fill_key(event, "a")]
        home_fill = COLORS[SportsDashboard._mlb_team_side_fill_key(event, "b")]
        self._draw_hub_team_score(
            draw,
            x1 + 18,
            top_y,
            center_x - 24,
            away_label,
            event.get("wins_a"),
            event.get("record_a"),
            image=image,
            logo_url=event.get("team_a_logo"),
            logo_size=20,
            logo_fallback=SportsDashboard._event_team_logo_fallback(event, "a", "MLB"),
            team_fill=away_fill,
            score_fill=away_fill,
        )
        self._draw_hub_team_score(
            draw,
            center_x + 24,
            top_y,
            x2 - 18,
            home_label,
            event.get("wins_b"),
            event.get("record_b"),
            align="right",
            image=image,
            logo_url=event.get("team_b_logo"),
            logo_size=20,
            logo_fallback=SportsDashboard._event_team_logo_fallback(event, "b", "MLB"),
            team_fill=home_fill,
            score_fill=home_fill,
        )
        score = self._hub_score_label(event)
        score, score_font = self._fit_text(draw, score, 80, 29 if score != "VS" else 23, bold=True, min_size=16)
        self._draw_centered(draw, (center_x, top_y + 29), score, score_font, COLORS["text"])
        if self._hub_event_state(event) == "scheduled":
            self._draw_mlb_pregame_context(draw, x1, y1, x2, y2, event, now)
            return
        if self._hub_event_state(event) == "final":
            self._draw_mlb_final_context(draw, x1, y1, x2, y2, event)
            return
        info_y = y1 + 103
        draw.rounded_rectangle((x1 + 16, info_y, x2 - 16, info_y + 31), radius=4, fill=COLORS["mlb_field_tint"], outline=COLORS["border"], width=1)
        inning = self._mlb_inning_label(event)
        inning, inning_font = self._fit_text(draw, inning, 92, 12, bold=True, min_size=8)
        draw.text((x1 + 25, info_y + 5), inning, font=inning_font, fill=COLORS["mlb_accent"])
        self._draw_mlb_base_diamond(draw, int(center_x - 16), info_y + 2, event.get("bases") or "")
        if not self._draw_mlb_bso_board(draw, x2 - 116, info_y + 6, x2 - 25, info_y + 25, event):
            count = self._mlb_count_label(event)
            count, count_font = self._fit_text(draw, count, 96, 11, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 25, info_y + 6), count, count_font, COLORS["text"])
        self._draw_mlb_rhe_line(draw, x1 + 16, y2 - 58, x2 - 16, event)
        self._draw_mlb_live_matchup_strip(draw, x1 + 20, y2 - 25, x2 - 20, event)

    def _draw_mlb_live_matchup_strip(self, draw, x1, y, x2, event):
        batter = str((event or {}).get("current_batter") or "").strip()
        pitcher = str((event or {}).get("current_pitcher") or "").strip()
        if not (batter or pitcher):
            pitch_text = self._mlb_live_main_meta_label(event)
            pitch_text, pitch_font = self._fit_text(draw, pitch_text, x2 - x1, 10, bold=True, min_size=7)
            self._draw_centered_in_box(draw, (x1, y, x2, y + 16), pitch_text, pitch_font, COLORS["muted"])
            return
        if not batter:
            self._draw_mlb_live_matchup_cell(draw, (x1, y, x2, y + 16), "P", pitcher, COLORS["mlb_accent"])
            return
        if not pitcher:
            self._draw_mlb_live_matchup_cell(draw, (x1, y, x2, y + 16), "BAT", batter, COLORS["amber"])
            return

        gap = 6
        mid = int((x1 + x2) / 2)
        self._draw_mlb_live_matchup_cell(draw, (x1, y, mid - gap // 2, y + 16), "BAT", batter, COLORS["amber"])
        self._draw_mlb_live_matchup_cell(draw, (mid + gap // 2, y, x2, y + 16), "P", pitcher, COLORS["mlb_accent"])

    def _draw_mlb_live_matchup_cell(self, draw, box, label, value, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, label, x1 + 5, y1 + 4, accent)
        label_text, label_font = self._fit_text(draw, label, 22, 7, bold=True, min_size=5)
        draw.text((x1 + 18, y1 + 3), label_text, font=label_font, fill=accent)
        value_text, value_font = self._fit_text(draw, str(value or "TBD"), max(28, x2 - x1 - 47), 8, bold=True, min_size=6)
        self._draw_right_aligned(draw, (x2 - 5, y1 + 4), value_text, value_font, COLORS["text"])

    def _draw_mlb_bso_board(self, draw, x1, y1, x2, y2, event):
        event = event or {}
        if SportsDashboard._hub_event_state(event) != "live":
            return False
        raw_values = (event.get("balls"), event.get("strikes"), event.get("outs"))
        if all(value is None for value in raw_values):
            return False
        values = (
            ("B", raw_values[0], COLORS["mlb_accent"]),
            ("S", raw_values[1], COLORS["amber"]),
            ("O", raw_values[2], COLORS["red"]),
        )
        x1, y1, x2, y2 = [int(value) for value in (x1, y1, x2, y2)]
        gap = 3
        cell_width = max(24, int((x2 - x1 - gap * 2) / 3))
        for index, (label, raw_value, accent) in enumerate(values):
            parsed = SportsDashboard._lpl_int_value(raw_value)
            value = "-" if parsed is None else str(parsed)
            cell_x1 = x1 + index * (cell_width + gap)
            cell_x2 = x2 if index == 2 else cell_x1 + cell_width
            outs = raw_value if label == "O" else None
            self._draw_mlb_bso_cell(draw, (cell_x1, y1, cell_x2, y2), label, value, accent, outs=outs)
        return True

    def _draw_mlb_bso_cell(self, draw, box, label, value, accent, outs=None):
        x1, y1, x2, y2 = [int(value) for value in box]
        fill = self._blend(accent, COLORS["panel"], 0.2)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=fill, outline=accent, width=1)
        label_text, label_font = self._fit_text(draw, str(label), 8, 7, bold=True, min_size=5)
        draw.text((x1 + 3, y1 + 2), label_text, font=label_font, fill=accent)
        value_text, value_font = self._fit_text(draw, str(value), 10, 9, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 4, y1 + 3), value_text, value_font, COLORS["text"])
        if label != "O":
            return
        out_count = SportsDashboard._lpl_int_value(outs)
        out_count = 0 if out_count is None else max(0, min(3, out_count))
        for index in range(3):
            cx = x1 + 6 + index * 5
            dot_fill = COLORS["red"] if index < out_count else COLORS["panel"]
            dot_outline = COLORS["amber"] if index < out_count else COLORS["border"]
            draw.ellipse((cx, y2 - 5, cx + 2, y2 - 3), fill=dot_fill, outline=dot_outline, width=1)

    def _draw_mlb_pregame_context(self, draw, x1, y1, x2, y2, event, now):
        info_y = y1 + 103
        draw.rounded_rectangle((x1 + 16, info_y, x2 - 16, info_y + 31), radius=4, fill=COLORS["mlb_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "FIRST", x1 + 25, info_y + 10, COLORS["mlb_accent"])
        label, label_font = self._fit_text(draw, "FIRST PITCH", 92, 10, bold=True, min_size=7)
        draw.text((x1 + 40, info_y + 8), label, font=label_font, fill=COLORS["mlb_accent"])
        first = self._mlb_first_pitch_label(event, now)
        first, first_font = self._fit_text(draw, first, 94, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, info_y + 8), first, first_font, COLORS["text"])

        details = SportsDashboard._mlb_pregame_detail_rows(event)
        row_y = y2 - 63
        for label, value in details:
            self._draw_mlb_pregame_detail_row(draw, x1 + 16, row_y, x2 - 16, label, value)
            row_y += 19

    def _draw_mlb_pregame_detail_row(self, draw, x1, y, x2, label, value):
        draw.rounded_rectangle((x1, y, x2, y + 16), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, label, x1 + 8, y + 4, COLORS["mlb_accent"])
        label_text, label_font = self._fit_text(draw, str(label), 34, 7, bold=True, min_size=5)
        draw.text((x1 + 23, y + 4), label_text, font=label_font, fill=COLORS["mlb_accent"])
        value_text, value_font = self._fit_text(draw, str(value), x2 - x1 - 64, 9, bold=True, min_size=6)
        self._draw_right_aligned(draw, (x2 - 6, y + 4), value_text, value_font, COLORS["text"])

    def _draw_mlb_final_context(self, draw, x1, y1, x2, y2, event):
        info_y = y1 + 103
        draw.rounded_rectangle((x1 + 16, info_y, x2 - 16, info_y + 31), radius=4, fill=COLORS["mlb_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "SCORE", x1 + 25, info_y + 10, COLORS["mlb_accent"])
        label, label_font = self._fit_text(draw, "FINAL RESULT", 102, 10, bold=True, min_size=7)
        draw.text((x1 + 40, info_y + 8), label, font=label_font, fill=COLORS["mlb_accent"])
        date_label = self._mlb_final_date_label(event)
        date_label, date_font = self._fit_text(draw, date_label, 72, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, info_y + 8), date_label, date_font, COLORS["text"])

        self._draw_mlb_rhe_line(draw, x1 + 16, y2 - 58, x2 - 16, event)
        meta = self._mlb_final_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 25, x2 - 20, y2 - 9), meta, meta_font, COLORS["muted"])

    def _draw_mlb_side_column(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        main = (card or {}).get("main") or {}
        upcoming = list((card or {}).get("upcoming") or [])
        recent = list((card or {}).get("recent") or [])
        main_state = self._hub_event_state(main)
        if main_state == "live":
            if not upcoming and not recent:
                self._draw_mlb_live_state_section(draw, x1, x2, y1, y2, main)
                return
            secondary_y = min(y2 - 54, y1 + 136)
            self._draw_mlb_live_state_section(draw, x1, x2, y1, max(y1 + 88, secondary_y - 2), main)
            if upcoming:
                self._draw_hub_section_header(draw, x1, x2, secondary_y, "UPCOMING", COLORS["mlb_accent"])
                row_y = secondary_y + 24
                for index, event in enumerate(upcoming[:1]):
                    self._draw_mlb_small_row(image, draw, x1, x2, row_y + index * 31, event, now, show_time=True)
            else:
                self._draw_hub_section_header(draw, x1, x2, secondary_y, "RECENT", COLORS["mlb_accent"])
                row_y = secondary_y + 24
                for index, event in enumerate(recent[:1]):
                    self._draw_mlb_small_row(image, draw, x1, x2, row_y + index * 28, event, now, show_time=False)
            return
        if not upcoming and not recent and self._hub_event_state(main) == "final":
            self._draw_mlb_final_snap_section(draw, x1, x2, y1, y2, main)
            return
        self._draw_hub_section_header(draw, x1, x2, y1, "UPCOMING", COLORS["mlb_accent"])
        row_y = y1 + 24
        if upcoming:
            for index, event in enumerate(upcoming[:3]):
                self._draw_mlb_small_row(image, draw, x1, x2, row_y + index * 31, event, now, show_time=True)
        else:
            draw.text((x1 + 10, row_y + 6), "No MLB schedule", font=self._font(10, True), fill=COLORS["muted"])
        recent_y = min(y2 - 76, row_y + max(1, min(3, len(upcoming))) * 31 + 10)
        if recent:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", COLORS["mlb_accent"])
            recent_row_y = recent_y + 24
            for index, event in enumerate(recent[:2]):
                self._draw_mlb_small_row(image, draw, x1, x2, recent_row_y + index * 28, event, now, show_time=False)
        elif main_state == "live":
            self._draw_mlb_live_state_section(draw, x1, x2, recent_y, y2, main)
        elif main_state == "scheduled":
            self._draw_mlb_game_info_section(draw, x1, x2, recent_y, y2, main, now)
        elif main_state == "final":
            self._draw_mlb_final_snap_section(draw, x1, x2, recent_y, y2, main)
        else:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", COLORS["mlb_accent"])
            recent_row_y = recent_y + 24
            draw.text((x1 + 10, recent_row_y + 6), "No recent results", font=self._font(10, True), fill=COLORS["muted"])

    def _draw_mlb_live_state_section(self, draw, x1, x2, y, y2, event):
        self._draw_hub_section_header(draw, x1, x2, y, "LIVE STATE", COLORS["mlb_accent"])
        row_y = y + 24
        for index, (label, value) in enumerate(self._mlb_live_state_rows(event)):
            top = row_y + index * 18
            if top + 14 > y2 - 4:
                break
            draw.line((x1, top - 2, x2, top - 2), fill=COLORS["line"], width=1)
            self._draw_sport_info_icon(draw, label, x1 + 3, top + 1, COLORS["mlb_accent"])
            label, label_font = self._fit_text(draw, label, 40, 8, bold=True, min_size=6)
            draw.text((x1 + 17, top), label, font=label_font, fill=COLORS["muted"])
            value, value_font = self._fit_text(draw, value, x2 - x1 - 70, 10, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 3, top), value, value_font, COLORS["text"])

    def _draw_mlb_final_snap_section(self, draw, x1, x2, y, y2, event):
        self._draw_hub_section_header(draw, x1, x2, y, "FINAL SNAP", COLORS["mlb_accent"])
        row_y = y + 24
        rows = self._mlb_final_snap_rows(event)
        if not rows:
            draw.text((x1 + 10, row_y + 6), "No recent results", font=self._font(10, True), fill=COLORS["muted"])
            return
        for index, (label, value) in enumerate(rows):
            top = row_y + index * 18
            if top + 14 > y2 - 4:
                break
            draw.line((x1, top - 2, x2, top - 2), fill=COLORS["line"], width=1)
            self._draw_sport_info_icon(draw, label, x1 + 3, top + 1, COLORS["mlb_accent"])
            label, label_font = self._fit_text(draw, label, 40, 8, bold=True, min_size=6)
            draw.text((x1 + 17, top), label, font=label_font, fill=COLORS["muted"])
            value, value_font = self._fit_text(draw, value, x2 - x1 - 70, 10, bold=True, min_size=7)
            value_fill = COLORS["mlb_accent"] if str(label).upper() == "WIN" else COLORS["text"]
            self._draw_right_aligned(draw, (x2 - 3, top), value, value_font, value_fill)

    def _draw_mlb_game_info_section(self, draw, x1, x2, y, y2, event, now):
        self._draw_hub_section_header(draw, x1, x2, y, "GAME INFO", COLORS["mlb_accent"])
        row_y = y + 24
        for index, (label, value) in enumerate(self._mlb_game_info_rows(event, now)):
            top = row_y + index * 18
            if top + 14 > y2 - 4:
                break
            draw.line((x1, top - 2, x2, top - 2), fill=COLORS["line"], width=1)
            self._draw_sport_info_icon(draw, label, x1 + 3, top + 1, COLORS["mlb_accent"])
            label, label_font = self._fit_text(draw, label, 40, 8, bold=True, min_size=6)
            draw.text((x1 + 17, top), label, font=label_font, fill=COLORS["muted"])
            value, value_font = self._fit_text(draw, value, x2 - x1 - 70, 10, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 3, top), value, value_font, COLORS["text"])

    def _draw_mlb_small_row(self, image, draw, x1, x2, y, event, now, show_time):
        draw.rounded_rectangle((x1, y, x2, y + 25), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + 24), fill=COLORS["mlb_accent"])
        left = self._format_time(event["start"]) if show_time and event.get("start") else (event["start"].strftime("%m/%d") if event.get("start") else "TBD")
        left, left_font = self._fit_text(draw, left, 42, 9, bold=True, min_size=7)
        draw.text((x1 + 10, y + 2), left, font=left_font, fill=COLORS["muted"])
        logo_size = 11
        label = f"{self._mlb_display_team_from_event(event, 'a')} {self._hub_score_label(event)} {self._mlb_display_team_from_event(event, 'b')}"
        label, label_font = self._fit_text(draw, label, x2 - x1 - 96, 10, bold=True, min_size=7)
        self._draw_small_row_team_logos(image, draw, x1, x2, y + 3, event, label, label_font, x2 - 25, logo_size)
        self._draw_right_aligned(draw, (x2 - 25, y + 2), label, label_font, COLORS["text"])
        note = self._mlb_small_note_label(event)
        if note:
            is_live = SportsDashboard._hub_event_state(event) == "live"
            has_base_icon = is_live and bool(str(event.get("bases") or "").strip())
            count_chip = SportsDashboard._mlb_count_chip_label(event) if is_live else ""
            has_count_chip = bool(count_chip)
            if has_base_icon and has_count_chip:
                note_width = x2 - x1 - 148
            elif has_count_chip:
                note_width = x2 - x1 - 120
            else:
                note_width = x2 - x1 - (112 if has_base_icon else 84)
            if has_base_icon:
                self._draw_mlb_mini_base_diamond(draw, x1 + 58, y + 9, event.get("bases"))
            if has_count_chip:
                self._draw_mlb_count_chip(draw, x1 + (84 if has_base_icon else 58), y + 16, count_chip, event.get("outs"))
            note, note_font = self._fit_text(draw, note, note_width, 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 25, y + 15), note, note_font, COLORS["muted"])

    def _draw_football_main_card(self, image, draw, bounds, card, now, sport):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        accent = COLORS["red"] if card.get("status") == "LIVE" else self._hub_sport_accent(sport)
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        tag = f"{sport} {card.get('status') or 'NEXT'}" if sport == "NFL" else f"NCAA {card.get('status') or 'NEXT'}"
        tag, tag_font = self._fit_text(draw, tag, 96, 12, bold=True, min_size=8)
        tag_fill = COLORS["nfl_tag"] if sport == "NFL" else COLORS["ncaa_tag"]
        draw.rectangle((x1 + 18, y1 + 11, x1 + 116, y1 + 29), fill=tag_fill, outline=COLORS["border"], width=1)
        draw.text((x1 + 23, y1 + 12), tag, font=tag_font, fill=COLORS["text"])
        week = self._football_header_week_label(card, event)
        week, week_font = self._fit_text(draw, week or self._hub_event_time_label(event, now), 84, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 13), week, week_font, COLORS["muted"])
        if sport == "NCAA":
            header_badge = SportsDashboard._ncaa_header_badge_label(event)
            if header_badge:
                badge, badge_font = self._fit_text(draw, header_badge, 82, 9, bold=True, min_size=7)
                draw.text((x1 + 123, y1 + 16), badge, font=badge_font, fill=COLORS["amber"])

        field_y1 = y1 + 39
        field_y2 = y2 - 58
        self._draw_football_field(draw, x1 + 17, field_y1, x2 - 17, field_y2, sport, event)
        center_x = (x1 + x2) / 2
        away_label = self._football_display_team(event, "a", sport, full=True)
        home_label = self._football_display_team(event, "b", sport, full=True)
        away_fill = COLORS[SportsDashboard._football_score_side_fill_key(event, "a", sport)]
        home_fill = COLORS[SportsDashboard._football_score_side_fill_key(event, "b", sport)]
        self._draw_hub_team_score(draw, x1 + 28, field_y1 + 13, center_x - 28, away_label, event.get("wins_a"), event.get("record_a"), image=image, logo_url=event.get("team_a_logo"), logo_size=20, logo_fallback=SportsDashboard._event_team_logo_fallback(event, "a", sport), team_fill=away_fill, score_fill=away_fill)
        self._draw_hub_team_score(draw, center_x + 28, field_y1 + 13, x2 - 28, home_label, event.get("wins_b"), event.get("record_b"), align="right", image=image, logo_url=event.get("team_b_logo"), logo_size=20, logo_fallback=SportsDashboard._event_team_logo_fallback(event, "b", sport), team_fill=home_fill, score_fill=home_fill)
        score = self._hub_score_label(event)
        score, score_font = self._fit_text(draw, score, 84, 29 if score != "VS" else 23, bold=True, min_size=16)
        self._draw_centered(draw, (center_x, field_y1 + 44), score, score_font, COLORS["text"])
        if self._hub_event_state(event) != "scheduled":
            status = str(event.get("status_text") or self._hub_event_time_label(event, now)).upper()
            status, status_font = self._fit_text(draw, status, 86, 12, bold=True, min_size=8)
            self._draw_centered(draw, (center_x, field_y1 + 19), status, status_font, COLORS["amber"] if card.get("status") == "LIVE" else COLORS["muted"])
        if self._hub_event_state(event) == "final":
            self._draw_football_final_context(draw, x1, y2, x2, field_y2, event, sport)
            return
        if self._hub_event_state(event) == "scheduled":
            self._draw_football_pregame_context(draw, x1, y2, x2, field_y2, event, sport, now)
            return

        situation_y = max(field_y2 + 7, y2 - 51)
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, situation_y + 28), radius=4, fill=COLORS[self._football_context_fill_key(sport)], outline=COLORS["border"], width=1)
        down = str(event.get("down_distance") or ("NEUTRAL SITE" if event.get("neutral_site") else event.get("note") or "SCHEDULED")).upper()
        down, down_font = self._fit_text(draw, down, 98, 11, bold=True, min_size=7)
        draw.text((x1 + 25, situation_y + 5), down, font=down_font, fill=self._hub_sport_accent(sport))
        yard = str(event.get("yard_line") or event.get("note") or event.get("city") or "").strip()
        yard, yard_font = self._fit_text(draw, yard, x2 - x1 - 150, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 24, situation_y + 6), yard, yard_font, COLORS["text"])
        possession = SportsDashboard._football_possession_display_label(event, sport)
        if possession:
            pos, pos_font = self._fit_text(draw, f"POS {possession}", 70, 9, bold=True, min_size=7)
            self._draw_centered(draw, (center_x, situation_y + 20), pos, pos_font, COLORS["amber"])

        meta = self._football_meta_label(event, sport)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_nfl_main_card(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        status = str((card or {}).get("status") or "NEXT").upper()
        accent = COLORS["nfl_live"] if status == "LIVE" else COLORS["nfl_accent"]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        tag, tag_font = self._fit_text(draw, f"NFL {status}", 94, 12, bold=True, min_size=8)
        draw.rectangle((x1 + 18, y1 + 11, x1 + 116, y1 + 29), fill=COLORS["nfl_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 23, y1 + 12), tag, font=tag_font, fill=COLORS["text"])
        week = self._football_header_week_label(card, event)
        week, week_font = self._fit_text(draw, week or self._hub_event_time_label(event, now), 84, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 13), week, week_font, COLORS["muted"])

        field_y1 = y1 + 39
        field_y2 = y2 - 62
        self._draw_nfl_field(draw, x1 + 17, field_y1, x2 - 17, field_y2, event)
        center_x = (x1 + x2) / 2
        away_label = self._football_display_team(event, "a", "NFL", full=True)
        home_label = self._football_display_team(event, "b", "NFL", full=True)
        away_fill = COLORS[SportsDashboard._football_score_side_fill_key(event, "a", "NFL")]
        home_fill = COLORS[SportsDashboard._football_score_side_fill_key(event, "b", "NFL")]
        self._draw_hub_team_score(
            draw,
            x1 + 28,
            field_y1 + 13,
            center_x - 28,
            away_label,
            event.get("wins_a"),
            event.get("record_a"),
            image=image,
            logo_url=event.get("team_a_logo"),
            logo_size=20,
            logo_fallback=event.get("team_a_code") or event.get("team_a"),
            team_fill=away_fill,
            score_fill=away_fill,
        )
        self._draw_hub_team_score(
            draw,
            center_x + 28,
            field_y1 + 13,
            x2 - 28,
            home_label,
            event.get("wins_b"),
            event.get("record_b"),
            align="right",
            image=image,
            logo_url=event.get("team_b_logo"),
            logo_size=20,
            logo_fallback=event.get("team_b_code") or event.get("team_b"),
            team_fill=home_fill,
            score_fill=home_fill,
        )
        score = self._hub_score_label(event)
        score, score_font = self._fit_text(draw, score, 84, 29 if score != "VS" else 23, bold=True, min_size=16)
        self._draw_centered(draw, (center_x, field_y1 + 44), score, score_font, COLORS["text"])
        if self._hub_event_state(event) != "scheduled":
            status_text = str(event.get("status_text") or self._hub_event_time_label(event, now)).upper()
            status_text, status_font = self._fit_text(draw, status_text, 86, 12, bold=True, min_size=8)
            self._draw_centered(draw, (center_x, field_y1 + 18), status_text, status_font, COLORS["amber"] if status == "LIVE" else COLORS["muted"])

        if self._hub_event_state(event) == "final":
            self._draw_nfl_final_context(draw, x1, y2, x2, field_y2, event)
            return
        if self._hub_event_state(event) == "scheduled":
            self._draw_nfl_pregame_context(draw, x1, y2, x2, field_y2, event, now)
            return
        self._draw_nfl_live_drive_context(draw, x1, y2, x2, field_y2, event)

    def _draw_nfl_pregame_context(self, draw, x1, y2, x2, field_y2, event, now):
        situation_y = max(field_y2 + 7, y2 - 55)
        accent = COLORS["nfl_accent"]
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, situation_y + 34), radius=4, fill=COLORS["nfl_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "KICK", x1 + 25, situation_y + 6, accent)
        label, label_font = self._fit_text(draw, "NFL KICK", 78, 9, bold=True, min_size=7)
        draw.text((x1 + 40, situation_y + 4), label, font=label_font, fill=accent)
        kick = SportsDashboard._football_kick_label(event, now)
        kick, kick_font = self._fit_text(draw, kick, 92, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, situation_y + 4), kick, kick_font, COLORS["text"])
        line = SportsDashboard._nfl_broadcast_line_label(event)
        line, line_font = self._fit_text(draw, line, x2 - x1 - 58, 10, bold=True, min_size=7)
        draw.text((x1 + 25, situation_y + 19), line, font=line_font, fill=COLORS["amber"] if line != "NFL PREGAME" else COLORS["muted"])
        meta = SportsDashboard._nfl_venue_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_nfl_final_context(self, draw, x1, y2, x2, field_y2, event):
        situation_y = max(field_y2 + 7, y2 - 55)
        accent = COLORS["nfl_accent"]
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, situation_y + 34), radius=4, fill=COLORS["nfl_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "SCORE", x1 + 25, situation_y + 6, accent)
        label, label_font = self._fit_text(draw, "FINAL SCORE", 92, 9, bold=True, min_size=7)
        draw.text((x1 + 40, situation_y + 4), label, font=label_font, fill=accent)
        date_label = ""
        start = (event or {}).get("start")
        if start:
            date_label = start.strftime("%m/%d")
        date_label, date_font = self._fit_text(draw, date_label or "RESULT", 76, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, situation_y + 4), date_label, date_font, COLORS["text"])
        line = SportsDashboard._nfl_broadcast_line_label(event)
        line, line_font = self._fit_text(draw, line, x2 - x1 - 58, 10, bold=True, min_size=7)
        draw.text((x1 + 25, situation_y + 19), line, font=line_font, fill=COLORS["amber"] if line != "NFL FINAL" else COLORS["muted"])
        meta = SportsDashboard._nfl_venue_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_nfl_live_drive_context(self, draw, x1, y2, x2, field_y2, event):
        last_play = str((event or {}).get("last_play") or "").strip()
        has_play = bool(last_play)
        situation_y = max(field_y2 + 4, y2 - (58 if has_play else 55))
        accent = COLORS["nfl_accent"]
        box_bottom = situation_y + (43 if has_play else 34)
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, box_bottom), radius=4, fill=COLORS["nfl_field_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "DOWN", x1 + 25, situation_y + 6, accent)
        label, label_font = self._fit_text(draw, "NFL DRIVE", 78, 9, bold=True, min_size=7)
        draw.text((x1 + 40, situation_y + 4), label, font=label_font, fill=accent)
        possession = SportsDashboard._football_possession_display_label(event, "NFL")
        if possession:
            pos, pos_font = self._fit_text(draw, f"POS {possession}", 68, 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 25, situation_y + 4), pos, pos_font, COLORS["amber"])
        self._draw_nfl_live_drive_chips(draw, x1 + 24, situation_y + 17, x2 - 24, event)
        if has_play:
            self._draw_nfl_last_play_strip(draw, x1 + 24, situation_y + 32, x2 - 24, last_play)
        meta = self._football_meta_label(event, "NFL")
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        meta_box = (x1 + 20, y2 - 12, x2 - 20, y2 - 2) if has_play else (x1 + 20, y2 - 18, x2 - 20, y2 - 4)
        self._draw_centered_in_box(draw, meta_box, meta, meta_font, COLORS["muted"])

    def _draw_nfl_last_play_strip(self, draw, x1, y, x2, play):
        play = str(play or "").strip()
        if not play:
            return
        y = int(y)
        draw.rounded_rectangle((x1, y, x2, y + 10), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "PLAY", x1 + 5, y + 1, COLORS["nfl_accent"])
        label, label_font = self._fit_text(draw, "PLAY", 28, 6, bold=True, min_size=5)
        draw.text((x1 + 18, y + 1), label, font=label_font, fill=COLORS["nfl_accent"])
        play_text, play_font = self._fit_text(draw, play, max(44, x2 - x1 - 58), 7, bold=True, min_size=5)
        self._draw_right_aligned(draw, (x2 - 5, y + 1), play_text, play_font, COLORS["text"])

    def _draw_nfl_live_drive_chips(self, draw, x1, y, x2, event):
        down = str((event or {}).get("down_distance") or "").strip().upper()
        field = str((event or {}).get("yard_line") or (event or {}).get("note") or "").strip()
        last_play = str((event or {}).get("last_play") or "").strip()
        items = []
        if down:
            items.append(("DOWN", down, COLORS["amber"]))
        if field:
            items.append(("FIELD", field, COLORS["nfl_accent"]))
        if not items:
            if last_play:
                items.append(("PLAY", "LAST PLAY", COLORS["amber"]))
            else:
                items.append(("DOWN", "LIVE DRIVE", COLORS["amber"]))
        if len(items) == 1:
            self._draw_nfl_live_drive_chip(draw, (x1, y, x2, y + 14), items[0][0], items[0][1], items[0][2])
            return
        gap = 6
        mid = int((x1 + x2) / 2)
        self._draw_nfl_live_drive_chip(draw, (x1, y, mid - gap // 2, y + 14), items[0][0], items[0][1], items[0][2])
        self._draw_nfl_live_drive_chip(draw, (mid + gap // 2, y, x2, y + 14), items[1][0], items[1][1], items[1][2])

    def _draw_nfl_live_drive_chip(self, draw, box, label, value, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, label, x1 + 5, y1 + 3, accent)
        label_text, label_font = self._fit_text(draw, label, 34, 6, bold=True, min_size=5)
        draw.text((x1 + 18, y1 + 3), label_text, font=label_font, fill=accent)
        value_text, value_font = self._fit_text(draw, str(value or "TBD"), max(24, x2 - x1 - 58), 8, bold=True, min_size=6)
        self._draw_right_aligned(draw, (x2 - 5, y1 + 3), value_text, value_font, COLORS["text"])

    def _draw_football_pregame_context(self, draw, x1, y2, x2, field_y2, event, sport, now):
        situation_y = max(field_y2 + 7, y2 - 51)
        accent = self._hub_sport_accent(sport)
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, situation_y + 29), radius=4, fill=COLORS[self._football_context_fill_key(sport)], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "KICK", x1 + 25, situation_y + 6, accent)
        label, label_font = self._fit_text(draw, "KICKOFF", 72, 10, bold=True, min_size=7)
        draw.text((x1 + 40, situation_y + 5), label, font=label_font, fill=accent)
        kick = SportsDashboard._football_kick_label(event, now)
        kick, kick_font = self._fit_text(draw, kick, 92, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, situation_y + 5), kick, kick_font, COLORS["text"])
        headline = SportsDashboard._football_pregame_headline(event, sport)
        if headline:
            headline, headline_font = self._fit_text(draw, headline, x2 - x1 - 56, 8, bold=True, min_size=6)
            self._draw_centered(draw, ((x1 + x2) / 2, situation_y + 21), headline, headline_font, COLORS["muted"])
        meta = SportsDashboard._football_pregame_meta_label(event)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_football_final_context(self, draw, x1, y2, x2, field_y2, event, sport):
        situation_y = max(field_y2 + 7, y2 - 51)
        accent = self._hub_sport_accent(sport)
        draw.rounded_rectangle((x1 + 16, situation_y, x2 - 16, situation_y + 29), radius=4, fill=COLORS[self._football_context_fill_key(sport)], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "SCORE", x1 + 25, situation_y + 6, accent)
        label, label_font = self._fit_text(draw, "FINAL", 64, 11, bold=True, min_size=8)
        draw.text((x1 + 40, situation_y + 5), label, font=label_font, fill=accent)
        date_label = ""
        start = (event or {}).get("start")
        if start:
            date_label = start.strftime("%m/%d")
        date_label, date_font = self._fit_text(draw, date_label or "RESULT", 76, 11, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 25, situation_y + 5), date_label, date_font, COLORS["text"])
        headline = SportsDashboard._football_final_headline(event, sport)
        if headline:
            headline, headline_font = self._fit_text(draw, headline, x2 - x1 - 56, 8, bold=True, min_size=6)
            self._draw_centered(draw, ((x1 + x2) / 2, situation_y + 21), headline, headline_font, COLORS["muted"])
        meta = SportsDashboard._football_final_meta_label(event, sport)
        meta, meta_font = self._fit_text(draw, meta, x2 - x1 - 42, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1 + 20, y2 - 18, x2 - 20, y2 - 4), meta, meta_font, COLORS["muted"])

    def _draw_football_side_column(self, image, draw, bounds, card, now, sport):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        main = (card or {}).get("main") or {}
        upcoming = list((card or {}).get("upcoming") or [])
        recent = list((card or {}).get("recent") or [])
        if sport == "NCAA":
            upcoming = sorted(upcoming, key=SportsDashboard._ncaa_ranked_watch_sort_key)
        if self._hub_event_state(main) == "live" and (upcoming or recent):
            accent = self._hub_sport_accent(sport)
            self._draw_football_info_section(
                draw,
                x1,
                x2,
                y1,
                self._football_live_drive_title(sport),
                self._football_live_drive_rows(main, include_context=True, sport=sport),
                accent,
            )
            second_y = min(y2 - 76, y1 + 104)
            if upcoming:
                title = "RANKED WATCH" if sport == "NCAA" else "UPCOMING"
                self._draw_hub_section_header(draw, x1, x2, second_y, title, accent)
                row_y = second_y + 24
                for index, event in enumerate(upcoming[:2]):
                    self._draw_football_small_row(image, draw, x1, x2, row_y + index * 31, event, True, sport)
            else:
                self._draw_hub_section_header(draw, x1, x2, second_y, "RECENT", accent)
                row_y = second_y + 24
                for index, event in enumerate(recent[:2]):
                    self._draw_football_small_row(image, draw, x1, x2, row_y + index * 28, event, False, sport)
            return
        if not upcoming and not recent and self._hub_event_state(main) == "live":
            self._draw_football_live_side_fallback(draw, x1, y1, x2, y2, main, sport)
            return
        if not upcoming and not recent and self._hub_event_state(main) == "final":
            self._draw_football_info_section(draw, x1, x2, y1, "FINAL SNAP", self._football_final_snap_rows(main, sport), self._hub_sport_accent(sport))
            return
        title = "RANKED WATCH" if sport == "NCAA" else "UPCOMING"
        self._draw_hub_section_header(draw, x1, x2, y1, title, self._hub_sport_accent(sport))
        row_y = y1 + 24
        if upcoming:
            for index, event in enumerate(upcoming[:3]):
                self._draw_football_small_row(image, draw, x1, x2, row_y + index * 31, event, True, sport)
        else:
            empty = "No NCAA schedule" if sport == "NCAA" else "No NFL schedule"
            draw.text((x1 + 10, row_y + 6), empty, font=self._font(10, True), fill=COLORS["muted"])
        recent_y = min(y2 - 76, row_y + max(1, min(3, len(upcoming))) * 31 + 10)
        if recent:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", self._hub_sport_accent(sport))
            recent_row_y = recent_y + 24
            for index, event in enumerate(recent[:2]):
                self._draw_football_small_row(image, draw, x1, x2, recent_row_y + index * 28, event, False, sport)
        elif self._hub_event_state(main) == "live":
            self._draw_football_info_section(draw, x1, x2, recent_y, self._football_live_drive_title(sport), self._football_live_drive_rows(main, include_context=True, sport=sport), self._hub_sport_accent(sport))
        elif self._hub_event_state(main) == "scheduled":
            self._draw_football_info_section(draw, x1, x2, recent_y, "GAME INFO", self._football_game_info_rows(main, sport, now), self._hub_sport_accent(sport))
        elif self._hub_event_state(main) == "final":
            self._draw_football_info_section(draw, x1, x2, recent_y, "FINAL SNAP", self._football_final_snap_rows(main, sport), self._hub_sport_accent(sport))
        else:
            self._draw_hub_section_header(draw, x1, x2, recent_y, "RECENT", self._hub_sport_accent(sport))
            recent_row_y = recent_y + 24
            draw.text((x1 + 10, recent_row_y + 6), "No recent results", font=self._font(10, True), fill=COLORS["muted"])

    def _draw_football_live_side_fallback(self, draw, x1, y1, x2, y2, event, sport):
        accent = self._hub_sport_accent(sport)
        self._draw_football_info_section(draw, x1, x2, y1, self._football_live_drive_title(sport), self._football_live_drive_rows(event, sport=sport), accent)
        self._draw_football_info_section(draw, x1, x2, min(y2 - 92, y1 + 104), "GAME INFO", self._football_live_game_info_rows(event, sport), accent)

    @staticmethod
    def _football_live_drive_rows(event, include_context=False, sport=None):
        line = str((event or {}).get("yard_line") or "").strip()
        possession = SportsDashboard._football_possession_display_label(event, sport or (event or {}).get("sport") or "")
        field = line
        if possession:
            field = f"{line} / POS {possession}" if line else f"POS {possession}"
        rows = []
        status = str((event or {}).get("status_text") or "").strip().upper()
        down = str((event or {}).get("down_distance") or "").strip().upper()
        if status:
            rows.append(("QTR", status))
        if down:
            rows.append(("DOWN", down))
        if field:
            rows.append(("FIELD", field))
        last_play = str((event or {}).get("last_play") or "").strip()
        if last_play:
            rows.append(("PLAY", last_play))
        if include_context:
            rows.extend(SportsDashboard._football_compact_context_rows(event, combine_line_with_tv=True))
        return [(label, value) for label, value in rows if str(value or "").strip()][:5]

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
    def _football_live_game_info_rows(event, sport):
        event = event or {}
        sport = str(sport or event.get("sport") or "").upper()
        rows = []
        context_rows = SportsDashboard._football_compact_context_rows(event, combine_line_with_tv=True)
        venue_rows = [row for row in context_rows if row[0] == "VENUE"]
        rows.extend(row for row in context_rows if row[0] != "VENUE")
        if sport == "NCAA":
            site = SportsDashboard._football_ncaa_site_label(event)
            venue = str(event.get("venue") or event.get("city") or "").strip()
            if site and venue and venue not in site:
                site = f"{site} / {venue}"
            if site:
                rows.append(("SITE", site))
            elif venue_rows:
                rows.append(venue_rows[0])
        elif venue_rows:
            rows.append(venue_rows[0])
        record = SportsDashboard._football_record_matchup_label(event, sport)
        if record:
            rows.append(("RECORD", record))
        return rows[:5]

    @staticmethod
    def _football_line_label(event):
        event = event or {}
        spread = str(event.get("spread") or "").strip()
        total = str(event.get("over_under") or "").strip()
        return " / ".join(part for part in (spread, total) if part)

    @staticmethod
    def _football_game_info_rows(event, sport, now):
        event = event or {}
        rows = []
        kick = SportsDashboard._football_kick_label(event, now)
        if kick:
            rows.append(("KICK", kick))
        matchup = SportsDashboard._football_matchup_label(event, sport)
        if matchup:
            rows.append(("MATCH", matchup))
        context_rows = SportsDashboard._football_compact_context_rows(event, combine_line_with_tv=True)
        venue_rows = [row for row in context_rows if row[0] == "VENUE"]
        rows.extend(row for row in context_rows if row[0] != "VENUE")
        if sport == "NCAA":
            site = SportsDashboard._football_ncaa_site_label(event)
            venue = str(event.get("venue") or event.get("city") or "").strip()
            if site and venue and venue not in site:
                site = f"{site} / {venue}"
            if site:
                rows.append(("SITE", site))
            elif venue_rows:
                rows.append(venue_rows[0])
        elif venue_rows:
            rows.append(venue_rows[0])
        record = SportsDashboard._football_record_matchup_label(event, sport)
        if record:
            rows.append(("RECORD", record))
        return rows[:5]

    @staticmethod
    def _football_final_snap_rows(event, sport):
        event = event or {}
        sport = str(sport or event.get("sport") or "").upper()
        rows = []
        winner = SportsDashboard._football_final_winner_label(event, sport)
        if winner:
            rows.append(("WIN", winner))
        score = SportsDashboard._football_score_line_label(event, sport)
        if score:
            rows.append(("SCORE", score))
        record = SportsDashboard._football_record_matchup_label(event, sport)
        if record:
            rows.append(("RECORD", record))
        if sport == "NCAA":
            site = SportsDashboard._football_ncaa_site_label(event)
            venue = str(event.get("venue") or event.get("city") or "").strip()
            if site and venue and venue not in site:
                site = f"{site} / {venue}"
            if site:
                rows.append(("SITE", site))
        rows.extend(SportsDashboard._football_compact_context_rows(event, combine_line_with_tv=True))
        return rows[:5]

    @staticmethod
    def _football_pregame_headline(event, sport):
        event = event or {}
        if sport == "NCAA" and event.get("neutral_site"):
            site = SportsDashboard._football_ncaa_site_label(event)
            if site:
                return site
        parts = []
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast:
            parts.append(f"TV {broadcast}")
        spread = str(event.get("spread") or "").strip()
        if spread:
            parts.append(spread)
        total = str(event.get("over_under") or "").strip()
        if total and len(parts) < 2:
            parts.append(total)
        note = str(event.get("note") or "").strip()
        if note and len(parts) < 2:
            parts.append(note)
        return " / ".join(parts[:2])

    @staticmethod
    def _football_pregame_meta_label(event):
        event = event or {}
        parts = []
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue:
            parts.append(venue)
        total = str(event.get("over_under") or "").strip()
        if total:
            parts.append(total)
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast and len(parts) < 2:
            parts.append(f"TV {broadcast}")
        spread = str(event.get("spread") or "").strip()
        if spread and len(parts) < 2:
            parts.append(f"SPREAD {spread}")
        return "  |  ".join(parts[:2]) or "PREGAME INFO"

    @staticmethod
    def _football_final_headline(event, sport):
        event = event or {}
        if sport == "NCAA" and event.get("neutral_site"):
            site = SportsDashboard._football_ncaa_site_label(event)
            if site:
                return site
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue:
            return venue
        note = str(event.get("note") or "").strip()
        if note:
            return note
        return "RESULT FINAL"

    @staticmethod
    def _football_final_meta_label(event, sport):
        event = event or {}
        if sport == "NCAA":
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
            return "  |  ".join(parts[:3]) or "FINAL RESULT"

        parts = []
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast:
            parts.append(f"TV {broadcast}")
        spread = str(event.get("spread") or "").strip()
        if spread:
            parts.append(f"SPREAD {spread}")
        total = str(event.get("over_under") or "").strip()
        if total:
            parts.append(total)
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue and len(parts) < 2:
            parts.append(venue)
        return "  |  ".join(parts[:3]) or "FINAL RESULT"

    @staticmethod
    def _nfl_broadcast_line_label(event):
        event = event or {}
        parts = []
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast:
            parts.append(f"TV {broadcast}")
        line = SportsDashboard._football_line_label(event)
        if line:
            parts.append(line)
        fallback = "NFL FINAL" if SportsDashboard._hub_event_state(event) == "final" else "NFL PREGAME"
        return " / ".join(parts[:2]) or fallback

    @staticmethod
    def _nfl_venue_label(event):
        venue = str((event or {}).get("venue") or (event or {}).get("city") or "").strip()
        return venue or "NFL GAME INFO"

    @staticmethod
    def _football_kick_label(event, now):
        start = (event or {}).get("start")
        if start:
            return f"{start.strftime('%m/%d')} {SportsDashboard._format_time(start)}"
        return SportsDashboard._hub_event_time_label(event, now)

    @staticmethod
    def _football_matchup_label(event, sport):
        away = SportsDashboard._football_display_team(event, "a", sport)
        home = SportsDashboard._football_display_team(event, "b", sport)
        if away and home:
            connector = "VS" if sport == "NCAA" and (event or {}).get("neutral_site") else "@"
            return f"{away} {connector} {home}"
        return ""

    @staticmethod
    def _football_record_matchup_label(event, sport):
        away = SportsDashboard._football_display_team(event, "a", sport)
        home = SportsDashboard._football_display_team(event, "b", sport)
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
    def _football_score_line_label(event, sport):
        score_a = (event or {}).get("wins_a")
        score_b = (event or {}).get("wins_b")
        if score_a is None or score_b is None:
            return ""
        away = SportsDashboard._football_display_team(event, "a", sport)
        home = SportsDashboard._football_display_team(event, "b", sport)
        return f"{away} {score_a} / {home} {score_b}"

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
        school = str(raw_event.get(f"{prefix}_zh") or "").strip()
        code = str(raw_event.get(f"{prefix}_code") or raw_event.get(prefix) or "TBD").strip()
        fallback = str(raw_event.get(prefix) or raw_event.get(f"{prefix}_name") or code or "TBD").strip()
        aliases = [
            raw_event.get(f"{prefix}_name"),
            raw_event.get(prefix),
            code,
        ]
        if full:
            full_school = SportsDashboard._ncaa_display_school_name(code, fallback=fallback, aliases=aliases, full=True)
            if full_school and full_school != "TBD":
                school = full_school
        if not school:
            school = SportsDashboard._ncaa_display_school_name(code, fallback=fallback, aliases=aliases)
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
    def _ncaa_score_matchup_label(event):
        away = SportsDashboard._ncaa_school_label(event, "a", include_rank=True)
        home = SportsDashboard._ncaa_school_label(event, "b", include_rank=True)
        return f"{away} {SportsDashboard._hub_score_label(event)} {home}"

    @staticmethod
    def _ncaa_ranked_watch_sort_key(event):
        ranks = []
        for key in ("team_a_rank", "team_b_rank"):
            rank = SportsDashboard._lpl_int_value((event or {}).get(key))
            if rank and rank < 100:
                ranks.append(rank)
        start = (event or {}).get("start")
        if isinstance(start, datetime):
            sort_start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc)
        else:
            sort_start = datetime.max.replace(tzinfo=timezone.utc)
        return (
            0 if ranks else 1,
            0 if len(ranks) >= 2 else 1,
            min(ranks) if ranks else 999,
            0 if (event or {}).get("neutral_site") else 1,
            sort_start,
        )

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
    def _ncaa_game_info_rows(event, now):
        return SportsDashboard._football_game_info_rows(event, "NCAA", now)

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
    def _ncaa_main_meta_label(event):
        event = event or {}
        site = SportsDashboard._football_ncaa_site_label(event)
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if site and venue and venue not in site:
            return f"{site} / {venue}"
        if site:
            return site
        if venue:
            return venue
        return SportsDashboard._football_record_matchup_label(event, "NCAA") or "COLLEGE FOOTBALL"

    @staticmethod
    def _football_ncaa_site_label(event):
        if not (event or {}).get("neutral_site"):
            return ""
        note = str((event or {}).get("note") or "").strip()
        venue = str((event or {}).get("venue") or (event or {}).get("city") or "").strip()
        if note and venue:
            return f"NEUTRAL / {note}"
        if note:
            return f"NEUTRAL / {note}"
        if venue:
            return f"NEUTRAL / {venue}"
        return "NEUTRAL SITE"

    def _draw_football_info_section(self, draw, x1, x2, y, title, rows, accent):
        self._draw_hub_section_header(draw, x1, x2, y, title, accent)
        row_y = y + 24
        compact_rows = [(label, value) for label, value in rows if str(value or "").strip()]
        if not compact_rows:
            draw.text((x1 + 10, row_y + 6), "No live details", font=self._font(10, True), fill=COLORS["muted"])
            return
        for index, (label, value) in enumerate(compact_rows[:5]):
            top = row_y + index * 18
            draw.line((x1, top - 2, x2, top - 2), fill=COLORS["line"], width=1)
            self._draw_sport_info_icon(draw, label, x1 + 3, top + 1, accent)
            label, label_font = self._fit_text(draw, label, 40, 8, bold=True, min_size=6)
            draw.text((x1 + 17, top), label, font=label_font, fill=COLORS["muted"])
            value, value_font = self._fit_text(draw, value, x2 - x1 - 70, 10, bold=True, min_size=7)
            value_fill = accent if str(label).upper() == "WIN" else COLORS["text"]
            self._draw_right_aligned(draw, (x2 - 3, top), value, value_font, value_fill)

    def _draw_football_small_row(self, image, draw, x1, x2, y, event, show_time, sport):
        draw.rounded_rectangle((x1, y, x2, y + 25), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + 24), fill=self._hub_sport_accent(sport))
        left = self._format_time(event["start"]) if show_time and event.get("start") else (event["start"].strftime("%m/%d") if event.get("start") else "TBD")
        left, left_font = self._fit_text(draw, left, 42, 9, bold=True, min_size=7)
        draw.text((x1 + 10, y + 3), left, font=left_font, fill=COLORS["muted"])
        logo_size = 11
        matchup = f"{self._football_display_team(event, 'a', sport)} {self._hub_score_label(event)} {self._football_display_team(event, 'b', sport)}"
        matchup, matchup_font = self._fit_text(draw, matchup, x2 - x1 - 96, 10, bold=True, min_size=7)
        self._draw_small_row_team_logos(image, draw, x1, x2, y + 3, event, matchup, matchup_font, x2 - 25, logo_size)
        self._draw_right_aligned(draw, (x2 - 25, y + 3), matchup, matchup_font, COLORS["text"])
        note = self._football_small_note_label(event)
        if note:
            is_live = SportsDashboard._hub_event_state(event) == "live"
            has_field_marker = (
                is_live
                and SportsDashboard._football_field_marker_fraction(event) is not None
            )
            down_chip = SportsDashboard._football_down_chip_label(event) if is_live else ""
            has_down_chip = bool(down_chip)
            if has_field_marker and has_down_chip:
                note_width = x2 - x1 - 148
            elif has_down_chip:
                note_width = x2 - x1 - 120
            else:
                note_width = x2 - x1 - (112 if has_field_marker else 84)
            if has_field_marker:
                self._draw_football_mini_field_marker(draw, x1 + 58, y + 16, 24, sport, event)
            if has_down_chip:
                self._draw_football_down_chip(draw, x1 + (86 if has_field_marker else 58), y + 16, down_chip, event, sport)
            note, note_font = self._fit_text(draw, note, note_width, 8, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 25, y + 15), note, note_font, COLORS["muted"])

    def _draw_football_down_chip(self, draw, x, y, label, event, sport):
        label = str(label or "").strip()
        if not label:
            return
        x = int(x)
        y = int(y)
        accent = self._hub_sport_accent(sport)
        fill = self._blend(accent, COLORS["panel"], 0.24)
        draw.rounded_rectangle((x, y, x + 35, y + 9), radius=2, fill=fill, outline=accent, width=1)
        label, label_font = self._fit_text(draw, label, 17, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (x + 2, y + 1, x + 19, y + 8), label, label_font, COLORS["text"])
        current_down = SportsDashboard._football_down_number(event)
        for index in range(4):
            left = x + 22 + index * 3
            fill_color = COLORS["amber"] if current_down and index + 1 <= current_down else COLORS["panel"]
            outline = accent if current_down and index + 1 == current_down else COLORS["border"]
            draw.rectangle((left, y + 3, left + 1, y + 6), fill=fill_color, outline=outline, width=1)

    def _draw_football_mini_field_marker(self, draw, x, y, width, sport, event):
        fraction = SportsDashboard._football_field_marker_fraction(event)
        if fraction is None:
            return
        x = int(x)
        y = int(y)
        width = max(18, int(width))
        accent = self._hub_sport_accent(sport)
        field_fill = self._blend(accent, COLORS["panel"], 0.18)
        draw.rounded_rectangle((x, y, x + width, y + 6), radius=2, fill=field_fill, outline=COLORS["border"], width=1)
        draw.line((x + width // 2, y + 1, x + width // 2, y + 5), fill=COLORS["line"], width=1)
        marker_x = max(x + 2, min(x + width - 2, int(round(x + width * fraction))))
        draw.line((marker_x, y + 1, marker_x, y + 5), fill=accent, width=1)
        draw.ellipse((marker_x - 2, y + 2, marker_x + 2, y + 6), fill=COLORS["amber"], outline=COLORS["text"], width=1)

    def _draw_football_field(self, draw, x1, y1, x2, y2, sport, event=None):
        field_fill = COLORS["panel_blue"] if sport == "NFL" else self._blend(self._hub_sport_accent(sport), COLORS["panel"], 0.18)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=field_fill, outline=COLORS["border"], width=1)
        for index in range(1, 5):
            x = x1 + int((x2 - x1) * index / 5)
            draw.line((x, y1 + 4, x, y2 - 4), fill=COLORS["line"], width=1)
        for x in range(x1 + 14, x2 - 12, 18):
            draw.line((x, y1 + 8, x + 6, y1 + 8), fill=COLORS["line"], width=1)
            draw.line((x, y2 - 8, x + 6, y2 - 8), fill=COLORS["line"], width=1)
        marker_fraction = self._football_field_marker_fraction(event)
        if marker_fraction is None:
            return
        marker_x = int(round(x1 + (x2 - x1) * marker_fraction))
        accent = self._hub_sport_accent(sport)
        draw.line((marker_x, y1 + 5, marker_x, y2 - 5), fill=self._blend(accent, field_fill, 0.48), width=1)
        ball_y = y2 - 15
        draw.ellipse((marker_x - 6, ball_y - 4, marker_x + 6, ball_y + 4), fill=COLORS["amber"], outline=COLORS["text"], width=1)
        draw.line((marker_x - 3, ball_y - 2, marker_x + 3, ball_y + 2), fill=COLORS["text"], width=1)
        draw.line((marker_x - 3, ball_y + 2, marker_x + 3, ball_y - 2), fill=COLORS["text"], width=1)

    def _draw_nfl_field(self, draw, x1, y1, x2, y2, event=None):
        accent = COLORS["nfl_accent"]
        field_fill = self._blend(accent, COLORS["panel"], 0.22)
        red_zone = self._blend(COLORS["red"], field_fill, 0.42)
        line_color = self._blend(accent, COLORS["line"], 0.28)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=field_fill, outline=COLORS["border"], width=1)
        zone_w = max(16, int((x2 - x1) * 0.12))
        draw.rectangle((x1 + 1, y1 + 2, x1 + zone_w, y2 - 2), fill=red_zone)
        draw.rectangle((x2 - zone_w, y1 + 2, x2 - 1, y2 - 2), fill=red_zone)
        for index in range(1, 5):
            x = x1 + int((x2 - x1) * index / 5)
            draw.line((x, y1 + 4, x, y2 - 4), fill=line_color, width=1)
        for x in range(x1 + 14, x2 - 12, 18):
            draw.line((x, y1 + 8, x + 6, y1 + 8), fill=COLORS["line"], width=1)
            draw.line((x, y2 - 8, x + 6, y2 - 8), fill=COLORS["line"], width=1)
        center_x = (x1 + x2) / 2
        badge_y = max(y1 + 56, y2 - 25)
        draw.rounded_rectangle((center_x - 24, badge_y, center_x + 24, badge_y + 13), radius=3, outline=accent, width=1)
        label, label_font = self._fit_text(draw, "NFL", 36, 8, bold=True, min_size=6)
        self._draw_centered(draw, (center_x, badge_y + 2), label, label_font, accent)
        marker_fraction = self._football_field_marker_fraction(event)
        if marker_fraction is None:
            return
        marker_x = int(round(x1 + (x2 - x1) * marker_fraction))
        draw.line((marker_x, y1 + 5, marker_x, y2 - 5), fill=self._blend(accent, field_fill, 0.52), width=1)
        ball_y = y2 - 15
        draw.ellipse((marker_x - 6, ball_y - 4, marker_x + 6, ball_y + 4), fill=COLORS["amber"], outline=COLORS["text"], width=1)
        draw.line((marker_x - 3, ball_y - 2, marker_x + 3, ball_y + 2), fill=COLORS["text"], width=1)
        draw.line((marker_x - 3, ball_y + 2, marker_x + 3, ball_y - 2), fill=COLORS["text"], width=1)

    @staticmethod
    def _football_field_marker_fraction(event):
        event = event or {}
        line = str(event.get("yard_line") or "").strip().upper()
        if not line:
            return None
        away_code = str(event.get("team_a_code") or event.get("team_a") or "").strip().upper()
        home_code = str(event.get("team_b_code") or event.get("team_b") or "").strip().upper()
        possession = str(event.get("possession") or "").strip().upper()
        if line in {"50", "MIDFIELD"}:
            return 0.5
        match = re.search(r"([A-Z0-9]+)?\s*(\d{1,2})", line)
        if not match:
            return None
        code = str(match.group(1) or "").strip().upper()
        yard = SportsDashboard._coerce_int(match.group(2))
        if yard is None:
            return None
        yard = max(0, min(50, int(yard)))
        if code == home_code:
            return max(0.0, min(1.0, (100 - yard) / 100))
        if code == away_code:
            return max(0.0, min(1.0, yard / 100))
        if code == possession and possession == home_code:
            return max(0.0, min(1.0, (100 - yard) / 100))
        if code == possession and possession == away_code:
            return max(0.0, min(1.0, yard / 100))
        return 0.5 if yard == 50 else max(0.0, min(1.0, yard / 100))

    @staticmethod
    def _football_down_number(event):
        text = str((event or {}).get("down_distance") or "").strip().upper()
        match = re.search(r"\b([1-4])(?:ST|ND|RD|TH)?\b", text)
        if match:
            return SportsDashboard._coerce_int(match.group(1))
        return None

    @staticmethod
    def _football_down_chip_label(event):
        if not event or SportsDashboard._hub_event_state(event) != "live":
            return ""
        text = str((event or {}).get("down_distance") or "").strip().upper()
        if not text:
            return ""
        match = re.search(r"\b([1-4])(?:ST|ND|RD|TH)?\s*&\s*(GOAL|[0-9]{1,2})\b", text)
        if match:
            distance = "G" if match.group(2) == "GOAL" else match.group(2)
            return f"{match.group(1)}&{distance}"
        down = SportsDashboard._football_down_number(event)
        return f"{down}D" if down else ""

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
    def _football_team_side_fill_key(event, side):
        event = event or {}
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            return SportsDashboard._football_possession_side_fill_key(event, side)
        if state != "final":
            return "text"
        winner_side = SportsDashboard._football_winner_side(event)
        return "amber" if winner_side == side else "text"

    @staticmethod
    def _football_score_side_fill_key(event, side, sport):
        event = event or {}
        if str(sport or event.get("sport") or "").upper() == "NFL" and SportsDashboard._hub_event_state(event) == "live":
            return "text"
        return SportsDashboard._football_team_side_fill_key(event, side)

    @staticmethod
    def _football_possession_display_label(event, sport):
        event = event or {}
        possession = str(event.get("possession") or "").strip()
        if not possession:
            return ""
        sport = str(sport or event.get("sport") or "").upper()
        normalized = possession.upper()
        for side in ("a", "b"):
            prefix = "team_a" if side == "a" else "team_b"
            values = [
                event.get(f"{prefix}_code"),
                event.get(prefix),
                event.get(f"{prefix}_name"),
            ]
            for value in values:
                if normalized == str(value or "").strip().upper():
                    if sport == "NCAA":
                        return SportsDashboard._ncaa_school_label(event, side)
                    return SportsDashboard._football_display_team(event, side, sport)
        if sport == "NCAA":
            return SportsDashboard._ncaa_display_school_name(possession, fallback=possession, aliases=[possession])
        if sport == "NFL":
            return SportsDashboard._football_display_team_name(possession, fallback=possession, sport=sport, aliases=[possession])
        return possession

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
        rank = (event or {}).get(f"{prefix}_rank")
        if sport == "NCAA":
            if full:
                team = SportsDashboard._ncaa_display_school_name(code, fallback=fallback, aliases=aliases, full=True)
            else:
                team = str(raw_event.get(f"{prefix}_zh") or "").strip()
            if not team or team == "TBD":
                team = SportsDashboard._ncaa_display_school_name(code, fallback=fallback, aliases=aliases)
            if rank:
                return f"#{rank} {team}"
            return team
        if sport == "NFL":
            team = str(raw_event.get(f"{prefix}_zh") or "").strip()
            if full:
                team = SportsDashboard._football_display_team_name(code, fallback=fallback, sport=sport, aliases=aliases, full=True)
            elif not team:
                team = SportsDashboard._football_display_team_name(code, fallback=fallback, sport=sport, aliases=aliases)
            return team
        return fallback

    @staticmethod
    def _mlb_small_note_label(event):
        if SportsDashboard._hub_event_state(event) == "live":
            parts = []
            inning = SportsDashboard._mlb_inning_label(event)
            if inning and inning != "SCHEDULED":
                parts.append(inning)
            bases = SportsDashboard._mlb_bases_label((event or {}).get("bases") or "")
            if bases and bases != "EMPTY":
                parts.append(bases)
            outs = SportsDashboard._mlb_outs_label(event)
            if outs:
                parts.append(outs)
            count = SportsDashboard._mlb_balls_strikes_label(event)
            if count and len(parts) < 3:
                parts.append(count)
            if parts:
                return " / ".join(parts[:3])
        if SportsDashboard._hub_event_state(event) == "final":
            venue = str((event or {}).get("venue") or "").strip()
            return " / ".join(part for part in ("FINAL", venue) if part)
        return SportsDashboard._mlb_pitching_compact_label(event) or str((event or {}).get("venue") or "").strip()

    @staticmethod
    def _mlb_live_state_rows(event):
        rows = [
            ("INNING", SportsDashboard._mlb_inning_label(event)),
            ("COUNT", SportsDashboard._mlb_count_label(event)),
        ]
        bases = SportsDashboard._mlb_bases_label((event or {}).get("bases") or "")
        if bases and bases != "EMPTY":
            rows.append(("BASES", bases))
        matchup = SportsDashboard._mlb_current_matchup_label(event)
        if matchup:
            rows.append(("B/P", matchup))
        rhe = SportsDashboard._mlb_compact_rhe_label(event)
        if rhe:
            rows.append(("RHE", rhe))
        venue = str((event or {}).get("venue") or "").strip()
        if venue:
            rows.append(("VENUE", venue))
        return rows

    @staticmethod
    def _mlb_game_info_rows(event, now):
        event = event or {}
        rows = []
        first = SportsDashboard._mlb_first_pitch_label(event, now)
        if first:
            rows.append(("FIRST", first))
        matchup = SportsDashboard._mlb_matchup_label(event)
        if matchup:
            rows.append(("MATCH", matchup))
        probable = SportsDashboard._mlb_probable_pitching_label(event)
        if probable:
            rows.append(("SP", probable))
        venue = str(event.get("venue") or "").strip()
        if venue:
            rows.append(("VENUE", venue))
        record = SportsDashboard._mlb_record_matchup_label(event)
        if record:
            rows.append(("RECORD", record))
        return rows[:5]

    @staticmethod
    def _mlb_pregame_detail_rows(event):
        event = event or {}
        rows = []
        probable = SportsDashboard._mlb_probable_pitching_label(event)
        if probable:
            rows.append(("SP", probable))
        venue = str(event.get("venue") or "").strip()
        if venue:
            rows.append(("VENUE", venue))
        record = SportsDashboard._mlb_record_matchup_label(event)
        if record:
            rows.append(("RECORD", record))
        return rows[:3]

    @staticmethod
    def _mlb_final_snap_rows(event):
        event = event or {}
        rows = []
        winner = SportsDashboard._mlb_final_winner_label(event)
        if winner:
            rows.append(("WIN", winner))
        score = SportsDashboard._mlb_score_line_label(event)
        if score:
            rows.append(("SCORE", score))
        rhe = SportsDashboard._mlb_compact_rhe_label(event)
        if rhe:
            rows.append(("RHE", rhe))
        record = SportsDashboard._mlb_record_matchup_label(event)
        if record:
            rows.append(("RECORD", record))
        venue = str(event.get("venue") or "").strip()
        if venue:
            rows.append(("VENUE", venue))
        return rows[:5]

    @staticmethod
    def _mlb_final_date_label(event):
        start = (event or {}).get("start")
        if start:
            return start.strftime("%m/%d")
        return "FINAL"

    @staticmethod
    def _mlb_final_meta_label(event):
        event = event or {}
        parts = []
        winner = SportsDashboard._mlb_final_winner_label(event)
        if winner:
            parts.append(winner)
        venue = str(event.get("venue") or "").strip()
        if venue:
            parts.append(venue)
        return " / ".join(parts[:2]) or "FINAL RESULT"

    @staticmethod
    def _mlb_first_pitch_label(event, now):
        start = (event or {}).get("start")
        if start:
            return f"{start.strftime('%m/%d')} {SportsDashboard._format_time(start)}"
        return SportsDashboard._hub_event_time_label(event, now)

    @staticmethod
    def _mlb_matchup_label(event):
        away = SportsDashboard._mlb_display_team_from_event(event, "a")
        home = SportsDashboard._mlb_display_team_from_event(event, "b")
        return f"{away} @ {home}"

    @staticmethod
    def _mlb_record_matchup_label(event):
        away = SportsDashboard._mlb_display_team_from_event(event, "a")
        home = SportsDashboard._mlb_display_team_from_event(event, "b")
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
    def _mlb_score_line_label(event):
        score_a = (event or {}).get("wins_a")
        score_b = (event or {}).get("wins_b")
        if score_a is None or score_b is None:
            return ""
        away = SportsDashboard._mlb_display_team_from_event(event, "a")
        home = SportsDashboard._mlb_display_team_from_event(event, "b")
        return f"{away} {score_a} / {home} {score_b}"

    @staticmethod
    def _mlb_final_winner_label(event):
        winner_side = SportsDashboard._mlb_winner_side(event)
        if not winner_side:
            return "TIE" if SportsDashboard._hub_event_state(event) == "final" else ""
        winner = SportsDashboard._mlb_display_team_from_event(event, winner_side)
        score_a = SportsDashboard._coerce_int((event or {}).get("wins_a"))
        score_b = SportsDashboard._coerce_int((event or {}).get("wins_b"))
        if score_a is not None and score_b is not None and score_a != score_b:
            return f"{winner} \u80dc{abs(score_a - score_b)}\u5206"
        return f"{winner} \u80dc"

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

    @staticmethod
    def _mlb_probable_pitching_label(event):
        away = SportsDashboard._mlb_short_pitcher_name((event or {}).get("probable_a"))
        home = SportsDashboard._mlb_short_pitcher_name((event or {}).get("probable_b"))
        if away and home:
            return f"{away} / {home}"
        if away:
            return f"{SportsDashboard._mlb_display_team_from_event(event, 'a')} {away}"
        if home:
            return f"{SportsDashboard._mlb_display_team_from_event(event, 'b')} {home}"
        return ""

    @staticmethod
    def _mlb_current_matchup_label(event):
        batter = str((event or {}).get("current_batter") or "").strip()
        pitcher = str((event or {}).get("current_pitcher") or "").strip()
        if batter and pitcher:
            return f"{batter} / {pitcher}"
        if batter:
            return f"BAT {batter}"
        if pitcher:
            return f"P {pitcher}"
        return ""

    @staticmethod
    def _mlb_live_main_meta_label(event):
        batter = str((event or {}).get("current_batter") or "").strip()
        pitcher = str((event or {}).get("current_pitcher") or "").strip()
        if batter and pitcher:
            return f"B/P {batter} / {pitcher}"
        if batter:
            return f"BAT {batter}"
        if pitcher:
            return f"P {pitcher}"
        return SportsDashboard._mlb_pitching_label(event)

    @staticmethod
    def _mlb_batting_side_fill_key(event, side):
        if SportsDashboard._hub_event_state(event) != "live":
            return "text"
        inning_state = str((event or {}).get("inning_state") or "").strip().lower()
        if side == "a" and inning_state.startswith("top"):
            return "amber"
        if side == "b" and inning_state.startswith("bottom"):
            return "amber"
        return "text"

    @staticmethod
    def _mlb_team_side_fill_key(event, side):
        event = event or {}
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            return SportsDashboard._mlb_batting_side_fill_key(event, side)
        if state != "final":
            return "text"
        winner_side = SportsDashboard._mlb_winner_side(event)
        return "amber" if winner_side == side else "text"

    @staticmethod
    def _mlb_bases_label(bases):
        labels = []
        text = str(bases or "").upper()
        if "1B" in text or "1" in text:
            labels.append("1B")
        if "2B" in text or "2" in text:
            labels.append("2B")
        if "3B" in text or "3" in text:
            labels.append("3B")
        return " ".join(labels) if labels else "EMPTY"

    @staticmethod
    def _mlb_compact_rhe_label(event):
        away = ((event or {}).get("away_line") or {})
        home = ((event or {}).get("home_line") or {})
        if not (
            SportsDashboard._mlb_line_has_rhe(away)
            or SportsDashboard._mlb_line_has_rhe(home)
        ):
            return ""
        team_a = SportsDashboard._mlb_display_team_from_event(event, "a")
        team_b = SportsDashboard._mlb_display_team_from_event(event, "b")

        def compact(line):
            values = []
            for key in ("runs", "hits", "errors"):
                value = line.get(key)
                values.append("-" if value is None else str(value))
            return "/".join(values)

        return f"{team_a} {compact(away)}  {team_b} {compact(home)}"

    @staticmethod
    def _mlb_line_has_rhe(line):
        if not isinstance(line, Mapping):
            return False
        return any(line.get(key) is not None for key in ("runs", "hits", "errors"))

    @staticmethod
    def _football_small_note_label(event):
        event = event or {}
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            parts = []
            status = str(event.get("status_text") or "").strip().upper()
            if status:
                parts.append(status)
            down = str(event.get("down_distance") or "").strip().upper()
            if down:
                parts.append(down)
            yard = str(event.get("yard_line") or "").strip()
            possession = SportsDashboard._football_possession_display_label(event, event.get("sport") or "")
            if yard and possession:
                parts.append(f"{yard} / POS {possession}")
            elif yard:
                parts.append(yard)
            elif possession:
                parts.append(f"POS {possession}")
            if parts:
                return " / ".join(parts[:3])

        if state == "final":
            status = str(event.get("status_text") or "FINAL").strip().upper() or "FINAL"
            note = str(event.get("note") or "").strip()
            venue = str(event.get("venue") or event.get("city") or "").strip()
            return " / ".join(part for part in (status, note, venue) if part)

        parts = []
        for key in ("broadcast", "spread", "over_under"):
            value = str(event.get(key) or "").strip()
            if value:
                parts.append(value)
        note = str(event.get("note") or "").strip()
        if note and len(parts) < 2:
            parts.append(note)
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue and len(parts) < 3:
            parts.append(venue)
        return " / ".join(parts[:3])

    @staticmethod
    def _football_meta_label(event, sport):
        event = event or {}
        parts = []
        if sport == "NCAA" and (event or {}).get("neutral_site"):
            parts.append("NEUTRAL SITE")
        broadcast = str(event.get("broadcast") or "").strip()
        if broadcast:
            parts.append(f"TV {broadcast}")
        spread = str(event.get("spread") or "").strip()
        if spread:
            parts.append(f"SPREAD {spread}")
        total = str(event.get("over_under") or "").strip()
        if total:
            parts.append(total)
        venue = str(event.get("venue") or event.get("city") or "").strip()
        if venue and len(parts) < 2:
            parts.append(venue)
        return "  |  ".join(parts[:3]) or "FOOTBALL DATA"

    def _draw_pga_event_card(self, image, draw, bounds, card, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        accent = COLORS["pga_live"] if card.get("status") == "LIVE" else COLORS["pga_accent"]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        self._draw_sport_logo(image, draw, "PGA", x1 + 18, y1 + 11, 36, 48)
        tag, tag_font = self._fit_text(draw, f"PGA {card.get('status') or 'NEXT'}", 90, 12, bold=True, min_size=8)
        draw.rectangle((x1 + 64, y1 + 13, x1 + 156, y1 + 31), fill=COLORS["pga_tag"], outline=COLORS["border"], width=1)
        draw.text((x1 + 69, y1 + 14), tag, font=tag_font, fill=COLORS["text"])
        name = str(event.get("name") or "PGA TOUR").strip() or "PGA TOUR"
        name, name_font = self._fit_text(draw, name, x2 - x1 - 42, 18, bold=True, min_size=10)
        draw.text((x1 + 20, y1 + 60), name, font=name_font, fill=COLORS["text"])
        status = str(event.get("status_text") or "").strip() or self._hub_event_time_label(event, now)
        status, status_font = self._fit_text(draw, status, x2 - x1 - 42, 12, bold=True, min_size=8)
        draw.text((x1 + 20, y1 + 84), status, font=status_font, fill=COLORS["pga_accent"])
        if not (event.get("leader") or {}):
            self._draw_pga_schedule_summary(draw, x1 + 20, y1 + 106, x2 - 20, y1 + 148, event, now)
        else:
            self._draw_pga_leader_summary(draw, x1 + 20, y1 + 106, x2 - 20, y1 + 148, event.get("leader") or {})
        venue = str(event.get("venue") or "").strip()
        if venue:
            venue, venue_font = self._fit_text(draw, venue, x2 - x1 - 42, 10, bold=True, min_size=7)
            draw.text((x1 + 20, y1 + 154), venue, font=venue_font, fill=COLORS["muted"])
        self._draw_pga_fairway(image, draw, x1 + 20, y2 - 49, x2 - 20, y2 - 13)

    def _draw_pga_schedule_summary(self, draw, x1, y1, x2, y2, event, now):
        event_state = SportsDashboard._hub_event_state(event)
        is_scheduled = event_state == "scheduled"
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["pga_course_tint"], outline=COLORS["border"], width=1)
        icon = "TEE" if is_scheduled else "CLOCK"
        label_text = "TEE WINDOW" if is_scheduled else ("FINAL STATUS" if event_state == "final" else "EVENT STATUS")
        self._draw_sport_info_icon(draw, icon, x1 + 8, y1 + 5, COLORS["pga_accent"])
        label, label_font = self._fit_text(draw, label_text, 78, 8, bold=True, min_size=6)
        draw.text((x1 + 23, y1 + 5), label, font=label_font, fill=COLORS["muted"])
        if is_scheduled:
            tee = SportsDashboard._pga_tee_label(event, now)
        else:
            tee = str((event or {}).get("status_text") or "").strip() or SportsDashboard._pga_event_window_label(event) or SportsDashboard._pga_tee_label(event, now)
        tee, tee_font = self._fit_text(draw, tee, 84, 16, bold=True, min_size=10)
        self._draw_right_aligned(draw, (x2 - 8, y1 + 13), tee, tee_font, COLORS["pga_accent"])
        window = SportsDashboard._pga_event_window_label(event)
        venue = str((event or {}).get("venue") or "").strip()
        detail = " / ".join(part for part in (window, venue) if part)
        if not detail:
            return
        self._draw_sport_info_icon(draw, "VENUE", x1 + 8, y2 - 13, COLORS["pga_accent"])
        detail, detail_font = self._fit_text(draw, detail, x2 - x1 - 33, 8, bold=True, min_size=6)
        draw.text((x1 + 23, y2 - 12), detail, font=detail_font, fill=COLORS["muted"])

    def _draw_pga_leader_summary(self, draw, x1, y1, x2, y2, leader):
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["pga_course_tint"], outline=COLORS["border"], width=1)
        self._draw_sport_info_icon(draw, "GOLF", x1 + 8, y1 + 5, COLORS["pga_accent"])
        label, label_font = self._fit_text(draw, "LEADER", 54, 8, bold=True, min_size=6)
        draw.text((x1 + 23, y1 + 5), label, font=label_font, fill=COLORS["muted"])
        if not leader:
            fallback, fallback_font = self._fit_text(draw, "EVENT INFO", x2 - x1 - 18, 11, bold=True, min_size=8)
            self._draw_centered_in_box(draw, (x1 + 8, y1 + 17, x2 - 8, y2 - 4), fallback, fallback_font, COLORS["text"])
            return
        name_x = x1 + 8
        name_budget = x2 - x1 - 74
        country_code = SportsDashboard._pga_country_badge_code(leader)
        if country_code:
            self._draw_pga_country_badge(draw, name_x, y1 + 18, country_code, COLORS["pga_accent"])
            name_x += 26
            name_budget = max(42, x2 - name_x - 52)
        name, name_font = self._fit_text(draw, leader.get("name") or "Leader", name_budget, 13, bold=True, min_size=8)
        draw.text((name_x, y1 + 17), name, font=name_font, fill=COLORS["text"])
        score, score_font = self._fit_text(draw, str(leader.get("score") or "E"), 44, 17, bold=True, min_size=11)
        self._draw_right_aligned(draw, (x2 - 8, y1 + 15), score, score_font, COLORS["pga_accent"])

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

    def _draw_pga_fairway(self, image, draw, x1, y1, x2, y2):
        base_width = max(1, int(x2 - x1 + 1))
        base_height = max(1, int(y2 - y1))
        strip_size = (max(1, int(round(base_width * 1.34))), max(1, int(round(base_height * 1.34))))
        strip = self._load_pga_fairway_strip(strip_size)
        if strip is not None:
            paste_x = int(round(x1 - (strip_size[0] - base_width) / 2))
            paste_y = int(round(y1 + 10))
            paste_x = min(max(0, paste_x), max(0, image.width - strip_size[0]))
            paste_y = min(max(0, paste_y), max(0, image.height - strip_size[1]))
            image.paste(strip, (paste_x, paste_y), strip)
            return
        fallback_y1 = y1 + 10
        fallback_y2 = y2 + 10
        draw.arc((x1 + 4, fallback_y1 - 6, x2 - 10, fallback_y2 + 18), 200, 342, fill=COLORS["pga_accent"], width=2)
        draw.ellipse((x2 - 36, fallback_y1 + 7, x2 - 10, fallback_y1 + 28), outline=COLORS["pga_accent"], width=2)
        draw.line((x2 - 22, fallback_y1 + 7, x2 - 22, fallback_y1 - 8), fill=COLORS["red"], width=2)

    def _draw_pga_leaderboard_column(self, image_or_draw, draw_or_bounds, bounds_or_card, card_or_now, now=None):
        if now is None:
            image = None
            draw = image_or_draw
            bounds = draw_or_bounds
            card = bounds_or_card
            now = card_or_now
        else:
            image = image_or_draw
            draw = draw_or_bounds
            bounds = bounds_or_card
            card = card_or_now
        x1, y1, x2, y2 = [int(value) for value in bounds]
        event = (card or {}).get("main") or {}
        rows = list(event.get("leaderboard") or [])
        if not rows:
            self._draw_pga_event_info_section(draw, x1, x2, y1, y2 - 28, event, now)
        else:
            leaderboard_y = y1
            self._draw_hub_section_header(draw, x1, x2, leaderboard_y, "LEADERBOARD", COLORS["pga_accent"])
            row_y = leaderboard_y + 27
            snap_y = y2 - 38
            drawn_rows = 0
            leader_score = rows[0].get("score") if rows else ""
            for index, row in enumerate(rows[:7]):
                top = row_y + index * 21
                if top + 18 > snap_y - 4:
                    break
                self._draw_pga_leaderboard_row(draw, x1, x2, top, row, index, leader_score=leader_score)
                drawn_rows += 1
            self._draw_pga_event_snap_row(draw, x1, x2, snap_y, event, now)
        next_label = self._pga_next_label(card, now)
        next_label, next_font = self._fit_text(draw, next_label, x2 - x1 - 8, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (x1, y2 - 19, x2, y2 - 4), next_label, next_font, COLORS["muted"])

    def _draw_pga_compact_event_info_section(self, draw, x1, x2, y1, y2, event, now):
        self._draw_hub_section_header(draw, x1, x2, y1, "EVENT INFO", COLORS["pga_accent"])
        row_y = y1 + 22
        for index, (icon, label, value) in enumerate(self._pga_compact_event_info_rows(event, now)):
            top = row_y + index * 17
            if top + 14 > y2:
                break
            self._draw_pga_info_row(draw, x1, x2, top, icon, label, value)

    def _draw_pga_event_info_section(self, draw, x1, x2, y1, y2, event, now):
        self._draw_hub_section_header(draw, x1, x2, y1, "EVENT INFO", COLORS["pga_accent"])
        rows = self._pga_event_info_rows(event, now)
        row_y = y1 + 27
        for index, (icon, label, value) in enumerate(rows):
            top = row_y + index * 21
            if top + 16 > y2:
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

    def _draw_pga_event_snap_row(self, draw, x1, x2, y, event, now):
        draw.line((x1, y - 2, x2, y - 2), fill=COLORS["line"], width=1)
        self._draw_sport_info_icon(draw, "PGA", x1 + 2, y + 1, COLORS["pga_accent"])
        label, label_font = self._fit_text(draw, "SNAP", 42, 8, bold=True, min_size=6)
        draw.text((x1 + 15, y), label, font=label_font, fill=COLORS["muted"])
        value = SportsDashboard._pga_event_snap_label(event, now)
        value, value_font = self._fit_text(draw, value, x2 - x1 - 64, 9, bold=True, min_size=6)
        self._draw_right_aligned(draw, (x2 - 2, y), value, value_font, COLORS["text"])

    @staticmethod
    def _pga_event_info_rows(event, now):
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
        if status:
            rows.append(("CLOCK", "STATUS", status))
        venue = str(event.get("venue") or "").strip()
        if venue:
            rows.append(("VENUE", "COURSE", venue))
        window = SportsDashboard._pga_event_window_label(event)
        if window:
            rows.append(("PERIOD", "WINDOW", window))
        if not rows:
            rows.append(("PGA", "BOARD", "PGA TOUR"))
        return rows[:5]

    @staticmethod
    def _pga_event_snap_label(event, now):
        event = event or {}
        parts = []
        leader = event.get("leader") or {}
        if leader:
            leader_name = str(leader.get("name") or "").strip()
            leader_score = str(leader.get("score") or "").strip()
            leader_label = " ".join(part for part in ("LEADER", leader_name, leader_score) if part)
            if leader_label != "LEADER":
                parts.append(leader_label)
        status = str(event.get("status_text") or "").strip()
        if not status:
            status = SportsDashboard._hub_event_time_label(event, now)
        if status == "TBD":
            status = ""
        if status:
            parts.append(status.upper())
        venue = str(event.get("venue") or "").strip()
        if venue:
            parts.append(venue)
        window = SportsDashboard._pga_event_window_label(event)
        if window:
            parts.append(window)
        return " / ".join(parts[:3]) or "PGA TOUR"

    def _draw_pga_leaderboard_row(self, draw, x1, x2, y, row, index, leader_score=None):
        rank_color_key = self._pga_leaderboard_rank_color_key(row, index)
        rank_accent = COLORS[rank_color_key]
        position = SportsDashboard._lpl_int_value((row or {}).get("position")) or index + 1
        podium = position in {1, 2, 3}
        draw.line((x1, y - 2, x2, y - 2), fill=COLORS["line"], width=1)
        self._draw_sport_info_icon(draw, "GOLF", x1 + 2, y + 1, rank_accent)
        pos = SportsDashboard._pga_position_label(row, index)
        pos, pos_font = self._fit_text(draw, pos, 24, 10, bold=True, min_size=7)
        draw.text((x1 + 15, y), pos, font=pos_font, fill=rank_accent if podium else COLORS["muted"])
        name_x = x1 + 43
        detail_x = name_x
        name_budget = x2 - x1 - 94
        country_code = SportsDashboard._pga_country_badge_code(row)
        if country_code:
            self._draw_pga_country_badge(draw, name_x, y + 1, country_code, rank_accent if podium else COLORS["pga_accent"])
            name_x += 26
            detail_x = name_x
            name_budget = max(42, x2 - name_x - 50)
        name, name_font = self._fit_text(draw, row.get("name") or "Player", name_budget, 10, bold=True, min_size=7)
        draw.text((name_x, y), name, font=name_font, fill=COLORS["text"])
        score = str(row.get("score") or "E")
        score, score_font = self._fit_text(draw, score, 48, 10, bold=True, min_size=7)
        self._draw_right_aligned(draw, (x2 - 2, y), score, score_font, rank_accent if podium else COLORS["text"])
        gap_chip = SportsDashboard._pga_gap_chip_label(row, leader_score)
        round_chip = SportsDashboard._pga_round_chip_label(row)
        detail = self._pga_row_detail_label(row, leader_score=leader_score)
        if detail:
            if gap_chip and round_chip:
                detail_reserve = 112
            elif gap_chip or round_chip:
                detail_reserve = 78
            else:
                detail_reserve = 48
            detail, detail_font = self._fit_text(
                draw,
                detail,
                max(42, x2 - detail_x - detail_reserve),
                8,
                bold=True,
                min_size=6,
            )
            draw.text((detail_x, y + 11), detail, font=detail_font, fill=COLORS["muted"])
        if round_chip:
            round_x = x2 - (66 if gap_chip else 31)
            self._draw_pga_round_chip(draw, round_x, y + 11, round_chip, row, rank_accent)
        if gap_chip:
            self._draw_pga_gap_chip(draw, x2 - 31, y + 11, gap_chip, rank_accent)

    def _draw_pga_country_badge(self, draw, x, y, code, accent):
        code = str(code or "").strip().upper()[:3]
        if not code:
            return
        box = (int(x), int(y), int(x) + 21, int(y) + 9)
        fill = self._blend(accent, COLORS["panel"], 0.22)
        draw.rounded_rectangle(box, radius=2, fill=fill, outline=accent, width=1)
        text, text_font = self._fit_text(draw, code, 17, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1), text, text_font, COLORS["text"])

    def _draw_pga_gap_chip(self, draw, x, y, label, accent):
        label = str(label or "").strip()
        if not label:
            return
        x = int(x)
        y = int(y)
        box = (x, y, x + 29, y + 9)
        fill = self._blend(accent, COLORS["panel"], 0.26)
        draw.rounded_rectangle(box, radius=2, fill=fill, outline=accent, width=1)
        draw.line((x + 5, y + 2, x + 5, y + 7), fill=COLORS["border"], width=1)
        draw.polygon((x + 6, y + 2, x + 11, y + 4, x + 6, y + 5), fill=accent)
        label, label_font = self._fit_text(draw, label, 15, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (x + 12, y + 1, x + 28, y + 8), label, label_font, COLORS["text"])

    def _draw_pga_round_chip(self, draw, x, y, label, row, accent):
        label = str(label or "").strip()
        if not label:
            return
        x = int(x)
        y = int(y)
        current = max(1, min(4, SportsDashboard._lpl_int_value((row or {}).get("round")) or 1))
        fill = self._blend(accent, COLORS["panel"], 0.22)
        draw.rounded_rectangle((x, y, x + 31, y + 9), radius=2, fill=fill, outline=accent, width=1)
        label, label_font = self._fit_text(draw, label, 12, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (x + 2, y + 1, x + 14, y + 8), label, label_font, COLORS["text"])
        for index in range(4):
            left = x + 18 + index * 3
            fill_color = accent if index + 1 <= current else COLORS["panel"]
            outline = accent if index + 1 == current else COLORS["border"]
            draw.rectangle((left, y + 3, left + 1, y + 6), fill=fill_color, outline=outline, width=1)

    @staticmethod
    def _pga_leaderboard_rank_color_key(row, index):
        position = SportsDashboard._lpl_int_value((row or {}).get("position")) or index + 1
        if position == 1:
            return "pga_leader"
        if position == 2:
            return "pga_accent"
        if position == 3:
            return "orange"
        return "pga_accent"

    @staticmethod
    def _pga_position_label(row, index):
        label = str((row or {}).get("position_label") or "").strip().upper()
        if label:
            return label
        position = SportsDashboard._lpl_int_value((row or {}).get("position")) or index + 1
        return f"P{position}"

    @staticmethod
    def _pga_country_badge_code(row):
        return SportsDashboard._pga_country_code((row or {}).get("country"))[:3]

    @staticmethod
    def _pga_row_detail_label(row, leader_score=None):
        parts = []
        country = str((row or {}).get("country") or "").strip()
        country_label = SportsDashboard._pga_country_display_label(country)
        round_no = (row or {}).get("round")
        strokes = str((row or {}).get("strokes") or "").strip()
        today = str((row or {}).get("today") or "").strip()
        if country_label:
            parts.append(country_label)
        if round_no:
            round_label = f"R{round_no}"
            if strokes:
                round_label = f"{round_label} {strokes}"
            parts.append(round_label)
        elif strokes:
            parts.append(strokes)
        if today:
            parts.append(today)
        gap = SportsDashboard._pga_gap_to_leader_label((row or {}).get("score"), leader_score)
        if gap:
            parts.append(gap)
        return " / ".join(parts)

    @staticmethod
    def _pga_gap_chip_label(row, leader_score):
        gap = SportsDashboard._pga_gap_to_leader_label((row or {}).get("score"), leader_score)
        match = re.search(r"\+(\d+)", gap)
        if not match:
            return ""
        return f"+{match.group(1)}"

    @staticmethod
    def _pga_round_chip_label(row):
        round_no = SportsDashboard._lpl_int_value((row or {}).get("round"))
        if not round_no or round_no <= 0:
            return ""
        return f"R{round_no}"

    @staticmethod
    def _pga_gap_to_leader_label(score, leader_score):
        score_value = SportsDashboard._pga_score_to_par(score)
        leader_value = SportsDashboard._pga_score_to_par(leader_score)
        if score_value is None or leader_value is None:
            return ""
        gap = score_value - leader_value
        if gap <= 0:
            return ""
        return f"GAP +{gap}"

    @staticmethod
    def _pga_score_to_par(value):
        text = str(value or "").strip().upper()
        if not text:
            return None
        if text in {"E", "EVEN"}:
            return 0
        match = re.search(r"^[+-]?\d+$", text)
        if not match:
            return None
        return int(text)

    @staticmethod
    def _pga_tee_label(event, now):
        start = (event or {}).get("start")
        if start:
            if now and start.date() == now.date():
                return SportsDashboard._format_time(start)
            return f"{start.strftime('%m/%d')} {SportsDashboard._format_time(start)}"
        return str((event or {}).get("status_text") or "").strip()

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

    def _draw_hub_team_score(self, draw, x1, y, x2, team, score, record="", align="left", image=None, logo_url="", logo_size=0, logo_fallback=None, team_fill=None, score_fill=None):
        logo_size = int(logo_size or 0)
        team_fill = team_fill or COLORS["text"]
        score_fill = score_fill or COLORS["text"]
        text_x1 = x1
        text_x2 = x2
        has_logo = image is not None and logo_size > 0
        if has_logo and align != "right":
            fallback = logo_fallback if logo_fallback is not None else team
            logo_x = int(x1)
            self._draw_team_logo(image, draw, logo_url, logo_x, y, logo_size, fallback)
            text_x1 = logo_x + logo_size + 5
        team_width_budget = max(32, text_x2 - text_x1)
        if has_logo and align == "right":
            team_width_budget = max(32, team_width_budget - logo_size - 5)
        team_text, team_font = self._fit_text(draw, str(team or "TBD"), team_width_budget, 19, bold=True, min_size=11)
        if has_logo and align == "right":
            fallback = logo_fallback if logo_fallback is not None else team
            team_width = self._text_width(draw, team_text, team_font)
            logo_x = max(int(x1), int(text_x2 - team_width - logo_size - 5))
            self._draw_team_logo(image, draw, logo_url, logo_x, y, logo_size, fallback)
        score_text = "-" if score is None else str(score)
        score_text, score_font = self._fit_text(draw, score_text, 38, 19, bold=True, min_size=11)
        record_text = str(record or "").strip()
        record_text, record_font = self._fit_text(draw, record_text, max(32, text_x2 - text_x1), 9, bold=True, min_size=7)
        if align == "right":
            self._draw_right_aligned(draw, (text_x2, y), team_text, team_font, team_fill)
            self._draw_right_aligned(draw, (text_x2, y + 22), score_text, score_font, score_fill)
            if record_text:
                self._draw_right_aligned(draw, (text_x2, y + 43), record_text, record_font, COLORS["muted"])
        else:
            draw.text((text_x1, y), team_text, font=team_font, fill=team_fill)
            draw.text((text_x1, y + 22), score_text, font=score_font, fill=score_fill)
            if record_text:
                draw.text((text_x1, y + 43), record_text, font=record_font, fill=COLORS["muted"])

    def _draw_mlb_base_diamond(self, draw, x, y, bases):
        occupied = set(str(bases or ""))
        points = {"2": (x + 16, y + 3), "1": (x + 29, y + 16), "3": (x + 3, y + 16)}
        for base, (cx, cy) in points.items():
            fill = COLORS["amber"] if base in occupied else COLORS["panel"]
            draw.polygon([(cx, cy - 5), (cx + 5, cy), (cx, cy + 5), (cx - 5, cy)], fill=fill, outline=COLORS["border"])
        label, label_font = self._fit_text(draw, "1B 2B 3B", 42, 7, bold=True, min_size=6)
        self._draw_centered(draw, (x + 16, y + 30), label, label_font, COLORS["muted"])

    def _draw_mlb_mini_base_diamond(self, draw, x, y, bases):
        occupied = set(str(bases or ""))
        points = {"2": (x + 7, y), "1": (x + 14, y + 7), "3": (x, y + 7)}
        for base, (cx, cy) in points.items():
            fill = COLORS["amber"] if base in occupied else COLORS["panel"]
            draw.polygon([(cx, cy - 3), (cx + 3, cy), (cx, cy + 3), (cx - 3, cy)], fill=fill, outline=COLORS["border"])

    def _draw_mlb_count_chip(self, draw, x, y, label, outs=None):
        label = str(label or "").strip()
        if not label:
            return
        x = int(x)
        y = int(y)
        fill = self._blend(COLORS["mlb_accent"], COLORS["panel"], 0.26)
        draw.rounded_rectangle((x, y, x + 35, y + 9), radius=2, fill=fill, outline=COLORS["mlb_accent"], width=1)
        label, label_font = self._fit_text(draw, label, 18, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (x + 2, y + 1, x + 21, y + 8), label, label_font, COLORS["text"])
        out_count = SportsDashboard._lpl_int_value(outs)
        out_count = 0 if out_count is None else max(0, min(3, out_count))
        for index in range(3):
            cx = x + 24 + index * 4
            dot_fill = COLORS["red"] if index < out_count else COLORS["panel"]
            dot_outline = COLORS["amber"] if index < out_count else COLORS["border"]
            draw.ellipse((cx, y + 3, cx + 2, y + 5), fill=dot_fill, outline=dot_outline, width=1)

    def _draw_mlb_rhe_line(self, draw, x1, y, x2, event):
        away = event.get("away_line") or {}
        home = event.get("home_line") or {}
        if not (SportsDashboard._mlb_line_has_rhe(away) or SportsDashboard._mlb_line_has_rhe(home)):
            return

        draw.rounded_rectangle((x1, y, x2, y + 31), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.line((x1 + 6, y + 16, x2 - 6, y + 16), fill=COLORS["line"], width=1)
        value_x1 = x2 - 82
        rows = (
            (self._mlb_display_team_from_event(event, "a"), away, y + 2, y + 15),
            (self._mlb_display_team_from_event(event, "b"), home, y + 17, y + 30),
        )
        for team, line, row_y1, row_y2 in rows:
            team_text, team_font = self._fit_text(draw, team, max(42, value_x1 - x1 - 18), 10, bold=True, min_size=7)
            self._draw_text_in_box(draw, (x1 + 9, row_y1, value_x1 - 6, row_y2), team_text, team_font, COLORS["text"])
            values = []
            for key in ("runs", "hits", "errors"):
                value = line.get(key)
                values.append("-" if value is None else str(value))
            value_text, value_font = self._fit_text(draw, f"R/H/E {'/'.join(values)}", 76, 10, bold=True, min_size=7)
            self._draw_text_in_box(draw, (value_x1, row_y1, x2 - 8, row_y2), value_text, value_font, COLORS["text"], align="right")

    @staticmethod
    def _hub_score_label(event):
        if (event or {}).get("wins_a") is None or (event or {}).get("wins_b") is None:
            return "VS"
        return f"{event.get('wins_a')}-{event.get('wins_b')}"

    @staticmethod
    def _mlb_inning_label(event):
        if not event:
            return "SCHEDULED"
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            inning = str(event.get("inning_label") or event.get("inning") or "").strip()
            half = str(event.get("inning_state") or "").strip().upper()
            return f"{half} {inning}".strip() or "LIVE"
        if state == "final":
            return "FINAL"
        return str(event.get("status_text") or "SCHEDULED").upper()[:18]

    @staticmethod
    def _mlb_count_label(event):
        if not event:
            return "MLB"
        if SportsDashboard._hub_event_state(event) != "live":
            return str(event.get("venue") or "MLB").strip()[:18]
        count = SportsDashboard._mlb_balls_strikes_label(event) or "B-S ---"
        outs = SportsDashboard._lpl_int_value(event.get("outs"))
        outs = "-" if outs is None else str(outs)
        return f"{count} OUT {outs}"

    @staticmethod
    def _mlb_balls_strikes_label(event):
        if not event or SportsDashboard._hub_event_state(event) != "live":
            return ""
        balls = event.get("balls")
        strikes = event.get("strikes")
        if balls is None and strikes is None:
            return ""
        balls = "-" if balls is None else str(balls)
        strikes = "-" if strikes is None else str(strikes)
        return f"B-S {balls}-{strikes}"

    @staticmethod
    def _mlb_count_chip_label(event):
        if not event or SportsDashboard._hub_event_state(event) != "live":
            return ""
        balls = event.get("balls")
        strikes = event.get("strikes")
        if balls is None and strikes is None:
            return ""
        balls = "-" if balls is None else str(balls)
        strikes = "-" if strikes is None else str(strikes)
        return f"{balls}-{strikes}"

    @staticmethod
    def _mlb_outs_label(event):
        if not event or SportsDashboard._hub_event_state(event) != "live":
            return ""
        outs = SportsDashboard._lpl_int_value(event.get("outs"))
        if outs is None:
            return ""
        return f"{outs} OUT"

    @staticmethod
    def _mlb_pitching_label(event):
        if not event:
            return "MLB GAME INFO"
        away = str(event.get("probable_a") or "").strip()
        home = str(event.get("probable_b") or "").strip()
        if away and home:
            return f"SP {away} / {home}"
        if away:
            return f"SP {SportsDashboard._mlb_display_team_from_event(event, 'a')} {away}"
        if home:
            return f"SP {SportsDashboard._mlb_display_team_from_event(event, 'b')} {home}"
        return str(event.get("venue") or "MLB GAME INFO").strip()

    @staticmethod
    def _mlb_pitching_compact_label(event):
        if not event:
            return ""
        away = SportsDashboard._mlb_short_pitcher_name((event or {}).get("probable_a"))
        home = SportsDashboard._mlb_short_pitcher_name((event or {}).get("probable_b"))
        if away or home:
            return f"SP {away or 'TBD'} / {home or 'TBD'}"
        return ""

    @staticmethod
    def _mlb_short_pitcher_name(name):
        value = str(name or "").strip()
        if not value:
            return ""
        parts = [part for part in value.replace(".", " ").split() if part]
        if len(parts) < 2:
            return value[:14]
        return f"{parts[0][0]}. {' '.join(parts[1:])}"[:18]

    @staticmethod
    def _pga_next_label(card, now):
        upcoming = list((card or {}).get("upcoming") or [])
        if upcoming:
            event = upcoming[0]
            start = event.get("start")
            date_text = start.strftime("%m/%d") if start else "TBD"
            name = str(event.get("name") or event.get("name_en") or "").strip()
            return f"NEXT TEE {date_text} / {name}" if name else f"NEXT TEE {date_text}"
        recent = list((card or {}).get("recent") or [])
        if recent:
            name = str(recent[0].get("name") or recent[0].get("name_en") or "").strip()
            return f"RECENT / {name}" if name else "RECENT TOURNAMENT"
        return "TOUR CALENDAR"
