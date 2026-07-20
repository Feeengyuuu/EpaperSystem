from collections.abc import Mapping
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageOps

from .common import COLORS, LOCAL_CLUB_LEAGUE_WORDMARK_PATHS


SportsDashboard = None

FOCUS_BOX = (4, 4, 294, 235)
RAIL_BOX = (300, 4, 531, 235)
RAIL_HEADER_HEIGHT = 27
RAIL_ROW_HEIGHT = 40

CLUB_LEAGUE_ACCENT_KEYS = {
    "PL": "blue",
    "PD": "red",
    "BL1": "red",
    "SA": "blue",
    "FL1": "cyan",
}


class ClubFootballRenderMixin:
    @staticmethod
    def _club_header_palette():
        return {
            "fill": COLORS["panel"],
            "border": COLORS["border"],
            "divider": COLORS["line"],
            "text": COLORS["text"],
            "muted": COLORS["muted"],
        }

    @staticmethod
    def _club_panel_layout(dimensions):
        width, height = [max(1, int(value)) for value in dimensions]
        left_padding = FOCUS_BOX[0]
        right_padding = 536 - RAIL_BOX[2]
        top_padding = FOCUS_BOX[1]
        bottom_padding = 240 - FOCUS_BOX[3]
        column_gap = RAIL_BOX[0] - FOCUS_BOX[2]
        available_width = max(2, width - left_padding - right_padding - column_gap)
        reference_focus_width = FOCUS_BOX[2] - FOCUS_BOX[0]
        reference_rail_width = RAIL_BOX[2] - RAIL_BOX[0]
        focus_width = round(
            available_width
            * reference_focus_width
            / (reference_focus_width + reference_rail_width)
        )
        focus_width = max(1, min(available_width - 1, focus_width))
        focus_right = left_padding + focus_width
        rail_left = focus_right + column_gap
        rail_right = max(rail_left + 1, width - right_padding)
        panel_bottom = max(top_padding + 1, height - bottom_padding)
        panel_height = panel_bottom - top_padding
        reference_panel_height = FOCUS_BOX[3] - FOCUS_BOX[1]
        rail_header_height = max(
            24,
            min(
                RAIL_HEADER_HEIGHT,
                round(RAIL_HEADER_HEIGHT * panel_height / reference_panel_height),
            ),
        )
        rail_bottom_padding = 4
        rail_row_height = max(
            1,
            (panel_height - rail_header_height - rail_bottom_padding) // 5,
        )
        return {
            "focus_box": (left_padding, top_padding, focus_right, panel_bottom),
            "rail_box": (rail_left, top_padding, rail_right, panel_bottom),
            "rail_header_height": rail_header_height,
            "rail_row_height": rail_row_height,
        }

    @staticmethod
    def _club_league_accent(league_code):
        return COLORS[CLUB_LEAGUE_ACCENT_KEYS.get(str(league_code or "").upper(), "blue")]

    def _club_team_display_name(self, event, side):
        if not isinstance(event, Mapping):
            return "待定球队"
        localized = str(event.get(f"{side}_name_zh") or "").strip()
        if localized:
            return localized
        return self._club_team_zh_name(
            event.get("league_code"),
            event.get(f"{side}_name"),
            team_id=event.get(f"{side}_team_id"),
        )

    @staticmethod
    def _club_rail_text_anchors(box):
        left, top, right, bottom = [int(value) for value in box]
        compact = bottom - top < 36
        logo_top = top + (5 if compact else 7)
        team_logo_size = 18 if compact else 20
        league_logo_top = top + (6 if compact else 8)
        league_logo_height = 15 if compact else 17
        return {
            "home_align": "left",
            "away_align": "right",
            "home_x": left + 47,
            "score_left": left + 99,
            "score_right": left + 130,
            "away_x": right - 45,
            "home_logo_box": (
                left + 23,
                logo_top,
                left + 23 + team_logo_size,
                logo_top + team_logo_size,
            ),
            "away_logo_box": (
                right - 21 - team_logo_size,
                logo_top,
                right - 21,
                logo_top + team_logo_size,
            ),
            "league_logo_box": (
                left + 2,
                league_logo_top,
                left + 19,
                league_logo_top + league_logo_height,
            ),
        }

    @staticmethod
    def _draw_left_aligned(draw, xy, text, font, color):
        draw.text(xy, text, font=font, fill=color)

    @staticmethod
    def _club_logo_fallback_text(value):
        compact = "".join(character for character in str(value or "") if character.isalnum())
        return compact[:3].upper() or "?"

    def _draw_club_logo_contained(self, image, logo_url, box, fallback_text, cache_dir):
        left, top, right, bottom = [int(value) for value in box]
        box_width = max(1, right - left)
        box_height = max(1, bottom - top)
        logo = None
        try:
            logo = self._load_team_logo(
                logo_url,
                min(box_width, box_height),
                cache_dir=cache_dir,
            )
        except Exception:
            logo = None
        if logo is not None:
            logo = ImageOps.contain(
                logo.convert("RGBA"),
                (box_width, box_height),
                Image.Resampling.LANCZOS,
            )
            paste_x = left + (box_width - logo.width) // 2
            paste_y = top + (box_height - logo.height) // 2
            image.paste(logo, (paste_x, paste_y), logo)
            return logo.size

        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (left, top, right - 1, bottom - 1),
            radius=max(2, min(box_width, box_height) // 4),
            fill=COLORS["panel2"],
            outline=COLORS["border"],
            width=1,
        )
        label = self._club_logo_fallback_text(fallback_text)
        label, font = self._fit_text(draw, label, box_width - 4, min(11, box_height - 3), bold=True, min_size=6)
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        draw.text(
            (
                left + (box_width - text_width) / 2 - text_box[0],
                top + (box_height - text_height) / 2 - text_box[1],
            ),
            label,
            font=font,
            fill=COLORS["text"],
        )
        return (0, 0)

    def _draw_club_league_wordmark(self, image, league_code, box):
        path = LOCAL_CLUB_LEAGUE_WORDMARK_PATHS.get(
            str(league_code or "").upper()
        )
        if not path:
            return False
        left, top, right, bottom = [int(value) for value in box]
        wordmark = self._load_local_logo(
            path,
            (max(1, right - left), max(1, bottom - top)),
            alpha_threshold=8,
        )
        if not wordmark:
            return False
        paste_y = top + max(0, (bottom - top - wordmark.height) // 2)
        image.paste(wordmark, (left, paste_y), wordmark)
        return True

    @staticmethod
    def _club_source_freshness_label(source_state):
        source = str(source_state or "").upper()
        if "UNAVAILABLE" in source:
            return "UNAVAILABLE"
        if "PARTIAL" in source:
            return "PARTIAL DATA"
        if "STALE" in source or "CACHE" in source:
            return "CACHED"
        if "LIVE" in source:
            return "LIVE DATA"
        return "CACHED"

    @staticmethod
    def _club_event_local_start(event, now):
        if not isinstance(event, Mapping):
            return None
        start = event.get("start_utc")
        if not isinstance(start, datetime):
            return None
        current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return start.astimezone(current.tzinfo)

    @staticmethod
    def _club_event_date_label(event, now):
        local_start = ClubFootballRenderMixin._club_event_local_start(event, now)
        return local_start.strftime("%m/%d") if local_start else "--/--"

    @staticmethod
    def _club_focus_schedule_labels(event, now):
        local_start = ClubFootballRenderMixin._club_event_local_start(event, now)
        date_time = (
            local_start.strftime("%m/%d %H:%M")
            if local_start
            else "\u65f6\u95f4\u5f85\u5b9a"
        )
        venue = str((event or {}).get("venue") or "").strip()
        return date_time, venue

    @staticmethod
    def _club_event_score_or_time(event, now):
        if not isinstance(event, Mapping) or event.get("no_schedule"):
            return "\u2014"
        home_score = event.get("home_score")
        away_score = event.get("away_score")
        status = str(event.get("status") or "").upper()
        if home_score is not None and away_score is not None and status in {"LIVE", "FINAL"}:
            return f"{home_score}\u2013{away_score}"
        local_start = ClubFootballRenderMixin._club_event_local_start(event, now)
        return local_start.strftime("%H:%M") if local_start else "VS"

    @staticmethod
    def _club_worldcup_odds_triplet(event):
        if not SportsDashboard._club_event_has_complete_odds(event):
            return None
        home = float(event.get("odds_home_decimal"))
        draw = float(event.get("odds_draw_decimal"))
        away = float(event.get("odds_away_decimal"))
        return (
            f"{home:.2f}",
            f"X / {draw:.2f}",
            f"{away:.2f}",
        )

    @staticmethod
    def _club_odds_source_labels(event):
        source = str((event or {}).get("odds_source") or "ESPN").strip().upper()
        source_label = "API-F" if source.startswith("API") else "ESPN"
        source_short = "A" if source.startswith("API") else "E"
        provider = str((event or {}).get("odds_provider_short") or "").strip().upper()
        attribution = f"{source_label}/{provider}" if provider else source_label
        return attribution, source_short

    def _draw_club_focus(
        self,
        panel,
        draw,
        selected,
        source_state,
        fetched_at,
        now,
        cache_dir,
        focus_box,
    ):
        left, top, right, bottom = focus_box
        reference_height = FOCUS_BOX[3] - FOCUS_BOX[1]
        height_scale = (bottom - top) / reference_height

        def y(offset):
            return top + round(offset * height_scale)

        focus = (selected or {}).get("focus") if isinstance(selected, Mapping) else None
        focus = focus if isinstance(focus, Mapping) else None
        league_code = str((focus or {}).get("league_code") or "CLUB").upper()
        palette = self._club_header_palette()
        accent = self._club_league_accent(league_code)
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=max(8, min(12, round(12 * height_scale))),
            fill=palette["fill"],
            outline=palette["border"],
            width=1,
        )
        draw.line(
            (left + 1, y(38), right - 1, y(38)),
            fill=palette["divider"],
            width=1,
        )
        draw.rectangle((left + 5, y(8), left + 7, y(31)), fill=accent)

        league_logo_box = (left + 10, y(7), left + 36, y(33))
        self._draw_club_logo_contained(
            panel,
            (focus or {}).get("league_logo_url"),
            league_logo_box,
            league_code,
            cache_dir,
        )
        title_box = (left + 42, y(7), left + 146, y(33))
        if not self._draw_club_league_wordmark(panel, league_code, title_box):
            league_name = str((focus or {}).get("league_name") or "五大联赛")
            league_name, league_font = self._fit_text_ellipsis(
                draw, league_name, 104, 17, bold=True, min_size=11
            )
            draw.text(
                (left + 42, y(8)),
                league_name,
                font=league_font,
                fill=palette["text"],
            )
        freshness = self._club_source_freshness_label(source_state)
        freshness, freshness_font = self._fit_text(
            draw, freshness, 78, 9, bold=True, min_size=7
        )
        self._draw_right_aligned(
            draw,
            (right - 9, y(12)),
            freshness,
            freshness_font,
            palette["muted"],
        )

        if focus is None:
            empty_title, empty_font = self._fit_text(
                draw, "暂无可用赛程", 240, 24, bold=True, min_size=16
            )
            self._draw_centered(
                draw,
                ((left + right) / 2, y(102)),
                empty_title,
                empty_font,
                COLORS["text"],
            )
            empty_sub, empty_sub_font = self._fit_text(
                draw, "数据源恢复后会自动刷新", 240, 13, bold=True, min_size=10
            )
            self._draw_centered(
                draw,
                ((left + right) / 2, y(139)),
                empty_sub,
                empty_sub_font,
                COLORS["muted"],
            )
            return

        logo_top = y(58)
        logo_size = max(52, y(128) - logo_top)
        home_logo_box = (left + 24, logo_top, left + 24 + logo_size, logo_top + logo_size)
        away_logo_box = (right - 24 - logo_size, logo_top, right - 24, logo_top + logo_size)
        self._draw_club_logo_contained(
            panel,
            focus.get("home_logo_url"),
            home_logo_box,
            self._club_team_display_name(focus, "home"),
            cache_dir,
        )
        self._draw_club_logo_contained(
            panel,
            focus.get("away_logo_url"),
            away_logo_box,
            self._club_team_display_name(focus, "away"),
            cache_dir,
        )

        display_value = self._club_event_score_or_time(focus, now)
        score_font = self._font(30 if "–" in display_value else 22, bold=True)
        self._draw_centered(
            draw,
            ((left + right) / 2, y(88)),
            display_value,
            score_font,
            COLORS["text"],
        )
        confirmed_live = (
            str(focus.get("status") or "").upper() == "LIVE"
            and bool(focus.get("provider_status_confirmed"))
        )
        if confirmed_live:
            draw.rounded_rectangle(
                ((left + right) / 2 - 23, y(116), (left + right) / 2 + 23, y(135)),
                radius=8,
                fill=COLORS["red"],
            )
            live_font = self._font(10, bold=True)
            self._draw_centered(
                draw,
                ((left + right) / 2, y(125)),
                "LIVE",
                live_font,
                COLORS["paper_text"],
            )
        else:
            status = "FINAL" if str(focus.get("status") or "").upper() == "FINAL" else "NEXT"
            status_font = self._font(9, bold=True)
            self._draw_centered(
                draw,
                ((left + right) / 2, y(124)),
                status,
                status_font,
                COLORS["muted"],
            )

        home_name, home_font = self._fit_text_ellipsis(
            draw,
            self._club_team_display_name(focus, "home"),
            116,
            14,
            bold=True,
            min_size=9,
        )
        away_name, away_font = self._fit_text_ellipsis(
            draw,
            self._club_team_display_name(focus, "away"),
            116,
            14,
            bold=True,
            min_size=9,
        )
        self._draw_centered(
            draw, (left + 70, y(151)), home_name, home_font, COLORS["text"]
        )
        self._draw_centered(
            draw, (right - 70, y(151)), away_name, away_font, COLORS["text"]
        )

        schedule_label, venue = self._club_focus_schedule_labels(focus, now)
        schedule_box = (left + 65, y(171), right - 65, y(203))
        draw.rounded_rectangle(
            schedule_box,
            radius=8,
            fill=COLORS["panel2"],
            outline=accent,
            width=2,
        )
        schedule_label, schedule_font = self._fit_text_ellipsis(
            draw,
            schedule_label,
            schedule_box[2] - schedule_box[0] - 14,
            13,
            bold=True,
            min_size=10,
        )
        self._draw_centered(
            draw,
            ((left + right) / 2, y(182)),
            schedule_label,
            schedule_font,
            COLORS["text"],
        )
        if venue:
            venue, venue_font = self._fit_text_ellipsis(
                draw,
                venue,
                schedule_box[2] - schedule_box[0] - 14,
                8,
                bold=True,
                min_size=7,
            )
            self._draw_centered(
                draw,
                ((left + right) / 2, y(197)),
                venue,
                venue_font,
                COLORS["muted"],
            )
        odds_triplet = self._club_worldcup_odds_triplet(focus)
        if odds_triplet:
            attribution, _ = self._club_odds_source_labels(focus)
            attribution, attribution_font = self._fit_text(
                draw, attribution, 74, 7, bold=True, min_size=6
            )
            self._draw_right_aligned(
                draw,
                (right - 8, y(207)),
                attribution,
                attribution_font,
                COLORS["muted"],
            )
            odds_boxes = (
                (left + 35, y(216), left + 105, y(229)),
                ((left + right) / 2 - 28, y(216), (left + right) / 2 + 28, y(229)),
                (right - 105, y(216), right - 35, y(229)),
            )
            for box, text in zip(odds_boxes, odds_triplet):
                self._draw_worldcup_odds_text(
                    draw, box, text, max_size=9
                )
        else:
            footer_text = str(focus.get("provider") or "数据源待恢复")
            footer_text, footer_font = self._fit_text_ellipsis(
                draw, footer_text, right - left - 30, 9, bold=True, min_size=7
            )
            self._draw_centered(
                draw,
                ((left + right) / 2, y(220)),
                footer_text,
                footer_font,
                COLORS["muted"],
            )

    def _draw_club_rail(
        self,
        panel,
        draw,
        selected,
        now,
        cache_dir,
        rail_box,
        rail_header_height,
        rail_row_height,
    ):
        left, top, right, bottom = rail_box
        palette = self._club_header_palette()
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=10,
            fill=palette["fill"],
            outline=palette["border"],
            width=1,
        )
        draw.line(
            (left + 1, top + rail_header_height, right - 1, top + rail_header_height),
            fill=palette["divider"],
            width=1,
        )
        draw.rectangle(
            (left + 5, top + 6, left + 7, top + rail_header_height - 5),
            fill=COLORS["blue"],
        )
        header_font = self._font(12, bold=True)
        draw.text(
            (left + 11, top + 5),
            "五大联赛追踪",
            font=header_font,
            fill=palette["text"],
        )
        odds_header_font = self._font(8, bold=True)
        self._draw_right_aligned(
            draw,
            (right - 9, top + 8),
            "ESPN/API-F \u8d54\u7387",
            odds_header_font,
            palette["muted"],
        )
        rail = list((selected or {}).get("rail") or []) if isinstance(selected, Mapping) else []
        for row_index in range(5):
            row_top = top + rail_header_height + row_index * rail_row_height
            row_bottom = min(bottom, row_top + rail_row_height)
            compact = rail_row_height < RAIL_ROW_HEIGHT
            if row_index:
                draw.line(
                    (left + 7, row_top, right - 7, row_top),
                    fill=palette["divider"],
                    width=1,
                )
            event = rail[row_index] if row_index < len(rail) else {
                "league_code": "",
                "league_name": "",
                "no_schedule": True,
                "status": "NO SCHEDULE",
            }
            anchors = self._club_rail_text_anchors((left + 2, row_top + 1, right, row_bottom - 1))
            league_code = str(event.get("league_code") or "").upper()
            self._draw_club_logo_contained(
                panel,
                event.get("league_logo_url"),
                anchors["league_logo_box"],
                league_code,
                cache_dir,
            )
            if event.get("no_schedule"):
                league_label = str(event.get("league_name") or league_code or "联赛")
                label, label_font = self._fit_text_ellipsis(
                    draw, f"{league_label}  暂无赛程", 176, 11, bold=True, min_size=8
                )
                draw.text(
                    (left + 28, row_top + (11 if compact else 14)),
                    label,
                    font=label_font,
                    fill=COLORS["muted"],
                )
                continue

            self._draw_club_logo_contained(
                panel,
                event.get("home_logo_url"),
                anchors["home_logo_box"],
                self._club_team_display_name(event, "home"),
                cache_dir,
            )
            self._draw_club_logo_contained(
                panel,
                event.get("away_logo_url"),
                anchors["away_logo_box"],
                self._club_team_display_name(event, "away"),
                cache_dir,
            )
            home_width = max(12, anchors["score_left"] - anchors["home_x"] - 4)
            away_left = anchors["score_right"] + 4
            away_width = max(12, anchors["away_x"] - away_left)
            home_text, home_font = self._fit_text_ellipsis(
                draw,
                self._club_team_display_name(event, "home"),
                home_width,
                10,
                bold=True,
                min_size=7,
            )
            away_text, away_font = self._fit_text_ellipsis(
                draw,
                self._club_team_display_name(event, "away"),
                away_width,
                10,
                bold=True,
                min_size=7,
            )
            text_y = row_top + (6 if compact else 9)
            self._draw_left_aligned(
                draw, (anchors["home_x"], text_y), home_text, home_font, COLORS["text"]
            )
            self._draw_right_aligned(
                draw, (anchors["away_x"], text_y), away_text, away_font, COLORS["text"]
            )
            score_width = anchors["score_right"] - anchors["score_left"]
            score_center_x = (anchors["score_left"] + anchors["score_right"]) / 2
            date_label = self._club_event_date_label(event, now)
            date_text, date_font = self._fit_text(
                draw, date_label, score_width, 7, bold=True, min_size=6
            )
            self._draw_centered(
                draw,
                (score_center_x, row_top + (6 if compact else 8)),
                date_text,
                date_font,
                COLORS["text"],
            )
            score = self._club_event_score_or_time(event, now)
            score_text, score_font = self._fit_text(
                draw, score, score_width, 9 if compact else 10, bold=True, min_size=7
            )
            self._draw_centered(
                draw,
                (score_center_x, row_top + (18 if compact else 22)),
                score_text,
                score_font,
                COLORS["text"],
            )
            odds_triplet = self._club_worldcup_odds_triplet(event)
            if odds_triplet:
                odds_top = row_bottom - (11 if compact else 13)
                odds_bottom = row_bottom - 1
                odds_boxes = (
                    (
                        anchors["home_x"],
                        odds_top,
                        anchors["score_left"] - 3,
                        odds_bottom,
                    ),
                    (
                        anchors["score_left"] - 3,
                        odds_top,
                        anchors["score_right"] + 3,
                        odds_bottom,
                    ),
                    (
                        anchors["score_right"] + 3,
                        odds_top,
                        anchors["away_x"],
                        odds_bottom,
                    ),
                )
                for box, text in zip(odds_boxes, odds_triplet):
                    self._draw_worldcup_odds_text(
                        draw, box, text, max_size=6 if compact else 7
                    )
                _, source_short = self._club_odds_source_labels(event)
                source_font = self._font(6, bold=True)
                self._draw_right_aligned(
                    draw,
                    (right - 5, row_bottom - (9 if compact else 11)),
                    source_short,
                    source_font,
                    COLORS["muted"],
                )

    def _render_club_football_panel(self, dimensions, selected, source_state, fetched_at, now):
        width, height = [max(1, int(value)) for value in dimensions]
        panel = Image.new("RGB", (width, height), COLORS["paper"])
        draw = ImageDraw.Draw(panel)
        layout = self._club_panel_layout((width, height))
        cache_dir = self._sports_dashboard_cache_dir() / "team_logos"
        self._draw_club_focus(
            panel,
            draw,
            selected,
            source_state,
            fetched_at,
            now,
            cache_dir,
            layout["focus_box"],
        )
        self._draw_club_rail(
            panel,
            draw,
            selected,
            now,
            cache_dir,
            layout["rail_box"],
            layout["rail_header_height"],
            layout["rail_row_height"],
        )
        return panel
