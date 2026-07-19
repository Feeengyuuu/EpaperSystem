from .common import *
from .common import _ACTIVE_COLORS, _safe_exception_text, _normalize_country_alias

SportsDashboard = None


class EsportsRenderMixin:
    @staticmethod
    def _load_lpl_msi_next_filler(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_LPL_MSI_NEXT_FILLER_PATH
        cache_key = (path, (width, height), "lpl-msi-next-filler-v1")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                filler = source.convert("RGB")
            if filler.size != (width, height):
                filler = ImageOps.fit(
                    filler,
                    (width, height),
                    method=Image.LANCZOS,
                    centering=(0.5, 0.5),
                )
            TEAM_LOGO_CACHE[cache_key] = filler
            return filler
        except Exception as exc:
            logger.warning("Failed to load LPL MSI next filler %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _lpl_msi_offseason_filler_paths():
        paths = tuple(
            path for path in LOCAL_LPL_MSI_OFFSEASON_FILLER_PATHS
            if os.path.exists(path)
        )
        if paths:
            return paths
        if os.path.exists(LOCAL_LPL_MSI_OFFSEASON_FILLER_PATH):
            return (LOCAL_LPL_MSI_OFFSEASON_FILLER_PATH,)
        return ()

    @staticmethod
    def _lpl_msi_offseason_filler_index(rotation_seed, count):
        if count <= 1:
            return 0
        try:
            if isinstance(rotation_seed, datetime):
                bucket = int(rotation_seed.timestamp()) // 60
            elif rotation_seed is not None:
                bucket = int(rotation_seed)
            else:
                bucket = int(datetime.now(timezone.utc).timestamp()) // 60
        except (TypeError, ValueError, OSError, OverflowError):
            bucket = int(datetime.now(timezone.utc).timestamp()) // 60
        digest = hashlib.sha1(f"lpl-msi-offseason:{bucket}".encode("ascii")).digest()
        return digest[0] % count

    @staticmethod
    def _load_lpl_msi_offseason_filler(size, rotation_seed=None):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        paths = SportsDashboard._lpl_msi_offseason_filler_paths()
        if not paths:
            return None
        path = paths[SportsDashboard._lpl_msi_offseason_filler_index(rotation_seed, len(paths))]
        cache_key = (path, (width, height), "lpl-msi-offseason-filler-v2-alpha")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        try:
            with Image.open(path) as source:
                filler = source.convert("RGBA")
            if filler.size != (width, height):
                filler = ImageOps.fit(
                    filler,
                    (width, height),
                    method=Image.LANCZOS,
                    centering=(0.5, 0.5),
                )
            TEAM_LOGO_CACHE[cache_key] = filler
            return filler
        except Exception as exc:
            logger.warning("Failed to load LPL MSI offseason filler %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _lpl_msi_card_accent_paths():
        paths = []
        if os.path.isdir(LOCAL_LPL_MSI_CARD_ACCENT_DIR):
            try:
                paths = sorted(
                    str(path)
                    for path in Path(LOCAL_LPL_MSI_CARD_ACCENT_DIR).glob("*.png")
                    if path.is_file()
                )
            except OSError as exc:
                logger.warning("Failed to list LPL MSI card accent pool %s: %s", LOCAL_LPL_MSI_CARD_ACCENT_DIR, exc)
        if paths:
            return tuple(paths)
        if os.path.exists(LOCAL_LPL_MSI_CARD_ACCENT_PATH):
            return (LOCAL_LPL_MSI_CARD_ACCENT_PATH,)
        return ()

    @staticmethod
    def _lpl_msi_card_accent_index(rotation_seed, count):
        if count <= 1:
            return 0
        try:
            if isinstance(rotation_seed, datetime):
                return int(rotation_seed.timestamp()) % count
            if rotation_seed is not None:
                return int(rotation_seed) % count
        except (TypeError, ValueError, OSError, OverflowError):
            pass
        return int(datetime.now(timezone.utc).timestamp()) % count

    @staticmethod
    def _load_lpl_msi_card_accent(size, rotation_seed=None):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        paths = SportsDashboard._lpl_msi_card_accent_paths()
        if not paths:
            return None
        path = paths[SportsDashboard._lpl_msi_card_accent_index(rotation_seed, len(paths))]
        cache_key = (path, (width, height), "lpl-msi-card-accent-v2")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        try:
            with Image.open(path) as source:
                accent = source.convert("RGBA")
            bbox = accent.getbbox()
            if bbox:
                accent = accent.crop(bbox)
            accent.thumbnail((width, height), Image.LANCZOS)
            fitted = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            fitted.alpha_composite(accent, ((width - accent.width) // 2, height - accent.height))
            accent = fitted
            TEAM_LOGO_CACHE[cache_key] = accent
            return accent
        except Exception as exc:
            logger.warning("Failed to load LPL MSI card accent %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    def _draw_mlb_title_wordmark(self, image, x, y, max_width, max_height):
        wordmark = self._load_local_logo(
            LOCAL_MLB_TITLE_WORDMARK_PATH,
            (int(max_width), int(max_height)),
            alpha_threshold=8,
        )
        if not wordmark:
            return False
        paste_x = int(x)
        paste_y = int(y + (int(max_height) - wordmark.height) / 2)
        image.paste(wordmark, (paste_x, paste_y), wordmark)
        return True

    def _draw_pga_title_wordmark(self, image, x, y, max_width, max_height):
        wordmark = self._load_local_logo(
            LOCAL_PGA_TITLE_WORDMARK_PATH,
            (int(max_width), int(max_height)),
            alpha_threshold=8,
        )
        if not wordmark:
            return False
        paste_x = int(x)
        paste_y = int(y + (int(max_height) - wordmark.height) / 2)
        image.paste(wordmark, (paste_x, paste_y), wordmark)
        return True

    @staticmethod
    def _football_live_drive_title(sport):
        return "COLLEGE DRIVE" if str(sport or "").upper() == "NCAA" else "LIVE DRIVE"

    @staticmethod
    def _hub_event_time_label(event, now):
        if not event or not event.get("start"):
            return "TBD"
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            return str(event.get("status_text") or "LIVE").upper()[:14]
        if state == "final":
            return "FINAL"
        start = event["start"]
        if start.date() == now.date():
            return SportsDashboard._format_time(start)
        return start.strftime("%m/%d")

    @staticmethod
    def _tint_alpha_art(source, color):
        alpha = source.getchannel("A")
        tinted = Image.new("RGBA", source.size, tuple(color) + (255,))
        tinted.putalpha(alpha)
        return tinted

    def _draw_ewc_sidebar(self, image, left_width, selected, source_state, now):
        draw = ImageDraw.Draw(image)
        width, height = image.size
        right_x = left_width + LPL_SEPARATOR_WIDTH
        right_w = width - right_x
        draw.rectangle((left_width, 0, right_x - 1, height), fill=COLORS["paper"])
        draw.line((left_width, 0, left_width, height), fill=COLORS["border"], width=1)
        if LPL_SEPARATOR_WIDTH > 2:
            draw.line((left_width + 2, 0, left_width + 2, height), fill=COLORS["line"], width=1)
        draw.rectangle((right_x, 0, width - 1, height - 1), fill=COLORS["panel"])
        self._draw_halftone(draw, (right_x, 0, width - 1, height - 1), COLORS["ewc_shadow"], COLORS["panel"], 20, 1)
        draw.line((right_x, 0, right_x, height), fill=COLORS["border"], width=1)

        header_y = 12
        self._draw_ewc_logo(image, draw, right_x + 12, header_y + 5, 92, 35)
        source_label = self._ewc_source_label(source_state)
        source_label, source_font = self._fit_text_ellipsis(draw, source_label, 58, 9, bold=True, min_size=7)
        self._draw_text_in_box(
            draw,
            (right_x + 108, header_y + 10, right_x + right_w - 92, header_y + 31),
            source_label,
            source_font,
            COLORS["muted"],
            align="center",
        )
        selected = selected or {}
        live_matches = list(selected.get("live_matches") or [])
        main_match = selected.get("main_match") or None
        live = selected.get("live") or []
        main_event = main_match or selected.get("main") or None
        is_live = False
        is_active_event = False
        if main_match:
            is_live = any(main_match.get("event_id") == match.get("event_id") for match in live_matches)
        else:
            is_active_event = bool(
                main_event
                and any(
                    main_event.get("event_id") == event.get("event_id")
                    for event in live
                )
            )
        is_recent_match = bool(
            main_match
            and not is_live
            and str(main_match.get("status") or "").strip().upper() in {"COMPLETED", "FINAL", "FINISHED"}
        )
        status_label = "LIVE" if is_live else ("RECENT" if is_recent_match else ("ACTIVE" if is_active_event else "NEXT"))
        self._draw_status_pill(draw, right_x + right_w - 88, header_y + 8, status_label, is_live)
        draw.line((right_x + 14, 66, right_x + right_w - 14, 66), fill=COLORS["border"], width=1)

        if main_match:
            self._draw_ewc_match_focus_card(image, draw, right_x, right_w, 78, main_match, now, is_live)
            main_id = main_match.get("event_id")
            upcoming_matches = [match for match in (selected.get("upcoming_matches") or []) if match.get("event_id") != main_id]
            recent_matches = [match for match in (selected.get("recent_matches") or []) if match.get("event_id") != main_id]
            self._draw_ewc_match_rows(
                image,
                draw,
                right_x,
                right_w,
                244,
                "UPCOMING",
                upcoming_matches[:2],
                now,
                "No more EWC matches",
                placeholder_event=main_match,
            )
            self._draw_ewc_match_rows(
                image,
                draw,
                right_x,
                right_w,
                374,
                "RECENT",
                recent_matches[:2],
                now,
                "No recent EWC results",
                compact=True,
                placeholder_event=main_match,
            )
            return

        self._draw_ewc_focus_card(
            image,
            draw,
            right_x,
            right_w,
            78,
            main_event,
            now,
            is_active_event,
        )
        upcoming = list(selected.get("upcoming") or [])
        if main_event:
            main_id = main_event.get("event_id")
            upcoming = [event for event in upcoming if event.get("event_id") != main_id]
        if upcoming:
            self._draw_ewc_event_rows(image, draw, right_x, right_w, 252, "UPCOMING", upcoming[:4], now, "No more EWC events")
            return
        recent = list(selected.get("recent") or [])
        self._draw_ewc_event_rows(image, draw, right_x, right_w, 252, "RECENT", recent[:4], now, "No EWC schedule")
    def _draw_ewc_logo(self, image, draw, x, y, width, height):
        logo = self._load_local_logo(LOCAL_EWC_LOGO_PATH, (int(width), int(height)), alpha_threshold=8)
        if logo:
            alpha = logo.getchannel("A")
            recolored = Image.new("RGBA", logo.size, COLORS["text"] + (0,))
            recolored.putalpha(alpha)
            paste_x = int(x) + (int(width) - logo.width) // 2
            paste_y = int(y) + (int(height) - logo.height) // 2
            image.paste(recolored, (paste_x, paste_y), recolored)
            return
        draw.rounded_rectangle((x, y, x + width, y + height), radius=5, fill=COLORS["ewc_tag"], outline=COLORS["border"], width=2)
        draw.rectangle((x + 5, y + 5, x + 14, y + height - 5), fill=COLORS["ewc_accent"], outline=COLORS["border"], width=1)
        text, font = self._fit_text_ellipsis(draw, "EWC", width - 26, 18, bold=True, min_size=13)
        self._draw_centered(draw, (x + width / 2 + 4, y + height / 2), text, font, COLORS["text"])

    def _draw_ewc_match_focus_card(self, image, draw, right_x, right_w, y, match, now, is_live):
        card_x1 = right_x + 12
        card_x2 = right_x + right_w - 12
        card_y2 = y + 154
        accent = COLORS["ewc_live"] if is_live else COLORS["ewc_accent"]
        draw.rounded_rectangle((card_x1 + 4, y + 4, card_x2 + 4, card_y2 + 4), radius=6, fill=COLORS["ewc_shadow"])
        draw.rounded_rectangle((card_x1, y, card_x2, card_y2), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((card_x1 + 1, y + 1, card_x1 + 8, card_y2 - 1), fill=accent)
        if not match:
            draw.text((card_x1 + 20, y + 58), "No EWC match", font=self._font(19, True), fill=COLORS["text"])
            return

        is_recent = bool(
            not is_live
            and str(match.get("status") or "").strip().upper() in {"COMPLETED", "FINAL", "FINISHED"}
        )
        tag = "LIVE MATCH" if is_live else ("RECENT RESULT" if is_recent else "NEXT MATCH")
        tag_w = 108 if is_live else 108
        tag_text, tag_font = self._fit_text_ellipsis(draw, tag, tag_w - 10, 12, bold=True, min_size=8)
        draw.rectangle((card_x1 + 16, y + 12, card_x1 + 16 + tag_w, y + 31), fill=COLORS["ewc_live"] if is_live else COLORS["ewc_tag"], outline=COLORS["border"], width=1)
        draw.text((card_x1 + 21, y + 13), tag_text, font=tag_font, fill=COLORS["text"])
        time_label = self._ewc_match_time_label(match, now)
        time_label, time_font = self._fit_text_ellipsis(draw, time_label, 88, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (card_x2 - 12, y + 14), time_label, time_font, COLORS["muted"])

        self._draw_ewc_match_game_identity(image, draw, card_x1, card_x2, y + 36, match)

        if self._is_ewc_multi_competitor_match(match):
            self._draw_ewc_multi_competitor_focus(draw, card_x1, card_x2, y, match, accent)
            return

        logo_size = 44
        left_logo_x = card_x1 + 26
        right_logo_x = card_x2 - 26 - logo_size
        logo_y = y + 66
        team_a = str(match.get("team_a") or "TBD").strip() or "TBD"
        team_b = str(match.get("team_b") or "TBD").strip() or "TBD"
        logo_label_a = self._ewc_compact_team_name(match, "a")
        logo_label_b = self._ewc_compact_team_name(match, "b")
        self._draw_team_logo(image, draw, self._ewc_team_logo_url(match, "a"), left_logo_x, logo_y, logo_size, logo_label_a)
        self._draw_team_logo(image, draw, self._ewc_team_logo_url(match, "b"), right_logo_x, logo_y, logo_size, logo_label_b)

        center_x = right_x + right_w / 2
        score = self._ewc_match_score_label(match)
        score_text, score_font = self._fit_text_ellipsis(draw, score, 64, 24, bold=True, min_size=15)
        self._draw_centered_in_box(draw, (center_x - 34, y + 72, center_x + 34, y + 100), score_text, score_font, COLORS["text"])

        left_box = (card_x1 + 15, y + 114, center_x - 10, y + 134)
        right_box = (center_x + 10, y + 114, card_x2 - 15, y + 134)
        team_a_text, team_a_font = self._fit_text_ellipsis(draw, team_a, left_box[2] - left_box[0], 13, bold=True, min_size=8)
        team_b_text, team_b_font = self._fit_text_ellipsis(draw, team_b, right_box[2] - right_box[0], 13, bold=True, min_size=8)
        self._draw_text_in_box(draw, left_box, team_a_text, team_a_font, COLORS["text"])
        self._draw_text_in_box(draw, right_box, team_b_text, team_b_font, COLORS["text"], align="right")

        stage = str(match.get("stage") or "MATCH").upper()
        stage, stage_font = self._fit_text_ellipsis(draw, stage, card_x2 - card_x1 - 34, 9, bold=True, min_size=6)
        self._draw_centered_in_box(draw, (card_x1 + 17, y + 136, card_x2 - 17, y + 151), stage, stage_font, accent)

    @staticmethod
    def _is_ewc_multi_competitor_match(match):
        match = match or {}
        if bool(match.get("multi_competitor")):
            return True
        try:
            return int(match.get("participant_count") or 0) > 2
        except (TypeError, ValueError):
            return False

    def _draw_ewc_multi_competitor_focus(self, draw, card_x1, card_x2, y, match, accent):
        try:
            count = max(0, int((match or {}).get("participant_count") or 0))
        except (TypeError, ValueError):
            count = 0
        count_label = f"{count} CLUBS" if count else "MULTI-TEAM ROUND"
        count_text, count_font = self._fit_text_ellipsis(draw, count_label, card_x2 - card_x1 - 52, 22, bold=True, min_size=13)
        self._draw_centered_in_box(draw, (card_x1 + 20, y + 67, card_x2 - 20, y + 96), count_text, count_font, COLORS["text"])

        leader = str((match or {}).get("leader") or "").strip()
        if leader:
            leader_label = f"#1 {leader}"
        else:
            game_count = len((match or {}).get("games") or [])
            leader_label = f"{game_count} ROUNDS" if game_count else "OFFICIAL SCHEDULE"
        leader_text, leader_font = self._fit_text_ellipsis(draw, leader_label, card_x2 - card_x1 - 48, 13, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (card_x1 + 20, y + 101, card_x2 - 20, y + 124), leader_text, leader_font, COLORS["text"])

        stage = str((match or {}).get("stage") or "ROUND").upper()
        stage, stage_font = self._fit_text_ellipsis(draw, stage, card_x2 - card_x1 - 34, 9, bold=True, min_size=6)
        self._draw_centered_in_box(draw, (card_x1 + 17, y + 136, card_x2 - 17, y + 151), stage, stage_font, accent)

    def _draw_ewc_match_game_identity(self, image, draw, card_x1, card_x2, y, match):
        logo_box = (int(card_x1 + 18), int(y), int(card_x1 + 72), int(y + 21))
        name_box = (int(card_x1 + 78), int(y), int(card_x2 - 18), int(y + 21))
        logo = self._load_ewc_game_logo(match, (logo_box[2] - logo_box[0] - 8, logo_box[3] - logo_box[1] - 5))
        if logo:
            logo_x = int(logo_box[0] + (logo_box[2] - logo_box[0] - logo.width) / 2)
            logo_y = int(logo_box[1] + (logo_box[3] - logo_box[1] - logo.height) / 2)
            image.paste(logo, (logo_x, logo_y), logo)
        else:
            fallback = str((match or {}).get("game") or "EWC").strip()[:4].upper() or "EWC"
            fallback_text, fallback_font = self._fit_text_ellipsis(draw, fallback, logo_box[2] - logo_box[0] - 6, 9, bold=True, min_size=6)
            self._draw_centered_in_box(draw, (logo_box[0] + 3, logo_box[1], logo_box[2] - 3, logo_box[3]), fallback_text, fallback_font, COLORS["text"])

        game_name = str((match or {}).get("game") or "EWC").strip() or "EWC"
        game_text, game_font = self._fit_text_ellipsis(draw, game_name, name_box[2] - name_box[0] - 10, 11, bold=True, min_size=7)
        self._draw_text_in_box(
            draw,
            (name_box[0] + 5, name_box[1], name_box[2] - 5, name_box[3]),
            game_text,
            game_font,
            COLORS["text"],
            align="right",
        )

    def _draw_ewc_match_rows(
        self,
        image,
        draw,
        right_x,
        right_w,
        y,
        title,
        matches,
        now,
        empty_text,
        compact=False,
        placeholder_event=None,
    ):
        self._draw_section_header(draw, right_x, right_w, y, title, COLORS["ewc_accent"])
        if not matches and not placeholder_event:
            empty_y = y + (34 if compact else 38)
            draw.text((right_x + 18, empty_y), empty_text, font=self._font(12 if compact else 14, True), fill=COLORS["muted"])
            return
        if compact:
            row_y = y + 28
            for index, match in enumerate(matches[:2]):
                self._draw_ewc_recent_match_row(image, draw, right_x, right_w, row_y + index * 40, match)
            for index in range(min(2, len(matches)), 2):
                self._draw_ewc_game_placeholder(
                    image,
                    draw,
                    right_x,
                    right_w,
                    row_y + index * 40,
                    placeholder_event,
                )
            return
        row_y = y + 30
        for index, match in enumerate(matches[:2]):
            self._draw_ewc_match_row(image, draw, right_x, right_w, row_y + index * 45, match, now)
        for index in range(min(2, len(matches)), 2):
            self._draw_ewc_game_placeholder(
                image,
                draw,
                right_x,
                right_w,
                row_y + index * 45,
                placeholder_event,
            )

    def _draw_ewc_game_placeholder(self, image, draw, right_x, right_w, y, event):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        row_h = 38
        art = self._load_ewc_game_placeholder(
            event,
            (max(1, row_x2 - row_x1 - 8), row_h),
        )
        if not art:
            label = str((event or {}).get("game") or "EWC").strip() or "EWC"
            label, font = self._fit_text_ellipsis(
                draw,
                f"MORE {label} SOON",
                row_x2 - row_x1 - 20,
                10,
                bold=True,
                min_size=7,
            )
            self._draw_centered_in_box(
                draw,
                (row_x1 + 8, y, row_x2 - 8, y + row_h),
                label,
                font,
                COLORS["muted"],
            )
            return
        art_x = int(row_x1 + (row_x2 - row_x1 - art.width) / 2)
        art_y = int(y + (row_h - art.height) / 2)
        image.paste(art, (art_x, art_y), art)

    def _draw_ewc_match_row(self, image, draw, right_x, right_w, y, match, now):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        draw.rounded_rectangle((row_x1, y, row_x2, y + 40), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((row_x1 + 1, y + 1, row_x1 + 5, y + 39), fill=COLORS["ewc_accent"])
        if self._is_ewc_multi_competitor_match(match):
            date_text, date_font = self._fit_text_ellipsis(draw, self._ewc_match_time_label(match, now), 70, 8, bold=True, min_size=6)
            draw.text((row_x1 + 10, y + 4), date_text, font=date_font, fill=COLORS["muted"])
            stage = str((match or {}).get("stage") or "ROUND").upper()
            stage, stage_font = self._fit_text_ellipsis(draw, stage, row_x2 - row_x1 - 96, 10, bold=True, min_size=6)
            draw.text((row_x1 + 10, y + 20), stage, font=stage_font, fill=COLORS["text"])
            summary = self._ewc_match_score_label(match)
            summary, summary_font = self._fit_text_ellipsis(draw, summary, 82, 9, bold=True, min_size=6)
            self._draw_right_aligned(draw, (row_x2 - 10, y + 20), summary, summary_font, COLORS["ewc_accent"])
            return
        logo_size = 17
        team_a = self._ewc_compact_team_name(match, "a")
        team_b = self._ewc_compact_team_name(match, "b")
        self._draw_team_logo(image, draw, self._ewc_team_logo_url(match, "a"), row_x1 + 10, y + 19, logo_size, team_a)
        self._draw_team_logo(image, draw, self._ewc_team_logo_url(match, "b"), row_x2 - 27, y + 19, logo_size, team_b)

        date_text, date_font = self._fit_text_ellipsis(draw, self._ewc_match_time_label(match, now), 62, 8, bold=True, min_size=6)
        self._draw_centered_in_box(draw, (row_x1 + 34, y + 3, row_x2 - 34, y + 14), date_text, date_font, COLORS["muted"])
        score = self._ewc_match_score_label(match)
        score_text, score_font = self._fit_text_ellipsis(draw, score, 38, 11, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (row_x1 + 92, y + 17, row_x2 - 92, y + 31), score_text, score_font, COLORS["text"])

        center_x = (row_x1 + row_x2) / 2
        team_a_text, team_a_font = self._fit_text_ellipsis(draw, team_a, max(24, center_x - (row_x1 + 32) - 25), 9, bold=True, min_size=6)
        team_b_text, team_b_font = self._fit_text_ellipsis(draw, team_b, max(24, (row_x2 - 32) - center_x - 25), 9, bold=True, min_size=6)
        self._draw_text_in_box(draw, (row_x1 + 32, y + 18, center_x - 24, y + 32), team_a_text, team_a_font, COLORS["text"])
        self._draw_text_in_box(draw, (center_x + 24, y + 18, row_x2 - 32, y + 32), team_b_text, team_b_font, COLORS["text"], align="right")
        stage = str((match or {}).get("stage") or "MATCH").upper()
        stage, stage_font = self._fit_text_ellipsis(draw, stage, row_x2 - row_x1 - 22, 6, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (row_x1 + 12, y + 31, row_x2 - 12, y + 39), stage, stage_font, COLORS["muted"])

    def _draw_ewc_recent_match_row(self, image, draw, right_x, right_w, y, match):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        draw.line((row_x1, y - 5, row_x2, y - 5), fill=COLORS["line"], width=1)
        date_label = (match or {}).get("start")
        date_text = date_label.strftime("%m/%d") if isinstance(date_label, datetime) else "TBD"
        draw.text((row_x1 + 2, y + 7), date_text, font=self._font(10, True), fill=COLORS["text"])
        if self._is_ewc_multi_competitor_match(match):
            stage = str((match or {}).get("stage") or "ROUND").upper()
            stage, stage_font = self._fit_text_ellipsis(draw, stage, 82, 9, bold=True, min_size=6)
            draw.text((row_x1 + 40, y + 7), stage, font=stage_font, fill=COLORS["text"])
            result = self._ewc_match_score_label(match)
            result, result_font = self._fit_text_ellipsis(draw, result, 82, 9, bold=True, min_size=6)
            self._draw_right_aligned(draw, (row_x2 - 2, y + 7), result, result_font, COLORS["ewc_accent"])
            return

        team_a = self._ewc_compact_team_name(match, "a")
        team_b = self._ewc_compact_team_name(match, "b")
        score = self._ewc_match_score_label(match)
        score_w = 34
        match_x1 = row_x1 + 42
        score_x = int((match_x1 + row_x2) / 2 - score_w / 2)
        logo_size = 15
        left_logo_x = match_x1
        self._draw_team_logo(image, draw, self._ewc_team_logo_url(match, "a"), left_logo_x, y + 7, logo_size, team_a)
        left_text, left_font = self._fit_text_ellipsis(draw, team_a, max(22, score_x - left_logo_x - logo_size - 10), 9, bold=True, min_size=6)
        self._draw_text_in_box(draw, (left_logo_x + logo_size + 4, y, score_x - 5, y + 30), left_text, left_font, COLORS["text"])
        score_text, score_font = self._fit_text_ellipsis(draw, score, score_w, 11, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (score_x, y, score_x + score_w, y + 30), score_text, score_font, COLORS["text"])
        right_logo_x = row_x2 - logo_size
        self._draw_team_logo(image, draw, self._ewc_team_logo_url(match, "b"), right_logo_x, y + 7, logo_size, team_b)
        right_text, right_font = self._fit_text_ellipsis(draw, team_b, max(22, right_logo_x - (score_x + score_w) - 10), 9, bold=True, min_size=6)
        self._draw_text_in_box(draw, (score_x + score_w + 5, y, right_logo_x - 4, y + 30), right_text, right_font, COLORS["text"], align="right")


    @staticmethod
    def _ewc_resized_image_url(logo_url, width=128):
        parsed = urlparse(str(logo_url or ""))
        host = parsed.hostname.lower() if parsed.hostname else ""
        if parsed.scheme not in {"http", "https"}:
            return ""
        official_hosts = {"esportsworldcup.com", "www.esportsworldcup.com"}
        if host.endswith(".esportsworldcup.com") or host in official_hosts:
            if parsed.path != "/_next/image":
                return str(logo_url)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            source_url = query.get("url")
            if not source_url:
                return str(logo_url)
            query["url"] = source_url
            query["w"] = str(int(width))
            query["q"] = query.get("q") or "50"
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", urlencode(query), ""))
        return urlunparse(
            (
                "https",
                "www.esportsworldcup.com",
                "/_next/image",
                "",
                urlencode({"url": str(logo_url), "w": str(int(width)), "q": "50"}),
                "",
            )
        )

    @staticmethod
    def _ewc_team_logo_url(match, side):
        key = "team_a_logo" if side == "a" else "team_b_logo"
        logo_url = str((match or {}).get(key) or "").strip()
        if not logo_url:
            return ""
        parsed = urlparse(logo_url)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if parsed.scheme not in {"http", "https"}:
            return ""
        if host.endswith(".esportsworldcup.com") or host in {"esportsworldcup.com", "www.esportsworldcup.com"}:
            return SportsDashboard._ewc_resized_image_url(logo_url)
        if host == "tds-cdn.ewc.efg.gg" or host.endswith(".ewc.efg.gg"):
            return SportsDashboard._ewc_resized_image_url(logo_url)
        if host.endswith(".cloudfront.net"):
            return SportsDashboard._ewc_resized_image_url(logo_url)
        if host in {"prosettings.net", "www.prosettings.net", "nigmagalaxy.com", "www.nigmagalaxy.com", "teamapexgaming.com", "www.teamapexgaming.com"}:
            return logo_url
        return ""

    @staticmethod
    def _ewc_compact_team_name(match, side):
        match = match or {}
        key = "team_a" if str(side).lower() == "a" else "team_b"
        short_key = f"{key}_short"
        short = str(match.get(short_key) or "").strip()
        if short and len(short) <= 12:
            return short
        full = str(match.get(key) or "TBD").strip()
        return full or "TBD"

    @staticmethod
    def _ewc_match_score_label(match):
        match = match or {}
        if SportsDashboard._is_ewc_multi_competitor_match(match):
            leader = str(match.get("leader") or "").strip()
            if leader:
                return f"#1 {leader}"
            try:
                count = int(match.get("participant_count") or 0)
            except (TypeError, ValueError):
                count = 0
            return f"{count} CLUBS" if count else "ROUND"
        score_a = match.get("score_a")
        score_b = match.get("score_b")
        if score_a is not None and score_b is not None:
            return f"{score_a}-{score_b}"
        return "VS"

    @staticmethod
    def _ewc_match_time_label(match, now):
        start = (match or {}).get("start")
        if not isinstance(start, datetime):
            return "TBD"
        if isinstance(now, datetime) and start.date() == now.date():
            return start.strftime("%I:%M %p").lstrip("0")
        return start.strftime("%b %d %H:%M").upper()
    @staticmethod
    def _ewc_source_label(source_state):
        state = str(source_state or "").strip().upper()
        if "FALLBACK" in state:
            return "SCHEDULE FALLBACK"
        if "STALE" in state:
            return "STALE DATA"
        if "CACHE" in state:
            return "OFFICIAL CACHE"
        if state.startswith("EWC"):
            return "OFFICIAL DATA"
        return state or "OFFICIAL DATA"

    def _draw_ewc_focus_card(self, image, draw, right_x, right_w, y, event, now, is_active):
        card_x1 = right_x + 12
        card_x2 = right_x + right_w - 12
        card_y2 = y + 154
        accent = COLORS["ewc_accent"]
        draw.rounded_rectangle((card_x1 + 4, y + 4, card_x2 + 4, card_y2 + 4), radius=6, fill=COLORS["ewc_shadow"])
        draw.rounded_rectangle((card_x1, y, card_x2, card_y2), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((card_x1 + 1, y + 1, card_x1 + 8, card_y2 - 1), fill=accent)
        if not event:
            draw.text((card_x1 + 20, y + 58), "No EWC schedule", font=self._font(19, True), fill=COLORS["text"])
            return

        tag = "CURRENT EVENT" if is_active else "NEXT EVENT"
        tag_w = 116 if is_active else 98
        tag_text, tag_font = self._fit_text_ellipsis(draw, tag, tag_w - 10, 12, bold=True, min_size=8)
        draw.rectangle((card_x1 + 16, y + 12, card_x1 + 16 + tag_w, y + 31), fill=COLORS["ewc_tag"], outline=COLORS["border"], width=1)
        draw.text((card_x1 + 21, y + 13), tag_text, font=tag_font, fill=COLORS["text"])
        date_text = self._ewc_date_label(event)
        date_text, date_font = self._fit_text_ellipsis(draw, date_text, 82, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (card_x2 - 12, y + 14), date_text, date_font, COLORS["muted"])

        game_logo = self._load_ewc_game_logo(event, (112, 34))
        if game_logo:
            logo_x = int(right_x + right_w / 2 - game_logo.width / 2)
            logo_y = int(y + 40 + (34 - game_logo.height) / 2)
            image.paste(game_logo, (logo_x, logo_y), game_logo)
            title_y = y + 80
            status_y = y + 104
            detail_y = y + 126
        else:
            title_y = y + 58
            status_y = y + 88
            detail_y = y + 115

        title, title_font = self._fit_text_ellipsis(draw, event.get("game") or "EWC", card_x2 - card_x1 - 34, 18, bold=True, min_size=12)
        self._draw_centered(draw, (right_x + right_w / 2, title_y), title, title_font, COLORS["text"])
        status_text = "EVENT IN PROGRESS" if is_active else self._ewc_countdown_label(event, now)
        status_text, status_font = self._fit_text_ellipsis(draw, status_text, card_x2 - card_x1 - 40, 15, bold=True, min_size=10)
        self._draw_centered(draw, (right_x + right_w / 2, status_y), status_text, status_font, accent)

        detail = self._ewc_detail_label(event)
        detail, detail_font = self._fit_text_ellipsis(draw, detail, card_x2 - card_x1 - 32, 11, bold=True, min_size=7)
        self._draw_centered(draw, (right_x + right_w / 2, detail_y), detail, detail_font, COLORS["muted"])
        source = str(event.get("source_url") or DEFAULT_EWC_COMPETITIONS_URL).replace("https://", "")
        source = source.split("/")[0].upper()
        source, source_font = self._fit_text_ellipsis(draw, source, card_x2 - card_x1 - 34, 8, bold=True, min_size=6)
        self._draw_centered(draw, (right_x + right_w / 2, y + 138), source, source_font, COLORS["muted"])

    def _draw_ewc_event_rows(self, image, draw, right_x, right_w, y, title, events, now, empty_text):
        self._draw_section_header(draw, right_x, right_w, y, title, COLORS["ewc_accent"])
        if not events:
            draw.text((right_x + 18, y + 38), empty_text, font=self._font(14, True), fill=COLORS["muted"])
            return
        row_y = y + 30
        for index, event in enumerate(events[:4]):
            self._draw_ewc_event_row(image, draw, right_x, right_w, row_y + index * 45, event, now)

    def _draw_ewc_event_row(self, image, draw, right_x, right_w, y, event, now):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        draw.rounded_rectangle((row_x1, y, row_x2, y + 40), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((row_x1 + 1, y + 1, row_x1 + 5, y + 39), fill=COLORS["ewc_accent"])
        logo = self._load_ewc_game_logo(event, (27, 24))
        text_left = row_x1 + 12
        if logo:
            logo_x = row_x1 + 11 + (27 - logo.width) // 2
            logo_y = y + 8 + (24 - logo.height) // 2
            image.paste(logo, (logo_x, logo_y), logo)
            text_left = row_x1 + 44
        date_text, date_font = self._fit_text_ellipsis(draw, self._ewc_date_label(event), 54, 9, bold=True, min_size=7)
        self._draw_right_aligned(draw, (row_x2 - 8, y + 3), date_text, date_font, COLORS["muted"])
        text_width_available = max(36, row_x2 - text_left - 66)
        game, game_font = self._fit_text_ellipsis(draw, event.get("game") or "EWC", text_width_available, 13, bold=True, min_size=8)
        self._draw_text_in_box(draw, (text_left, y + 2, row_x2 - 66, y + 19), game, game_font, COLORS["text"])
        detail = self._ewc_detail_label(event, compact=True)
        detail, detail_font = self._fit_text_ellipsis(draw, detail, row_x2 - text_left - 10, 8, bold=True, min_size=6)
        self._draw_text_in_box(draw, (text_left, y + 21, row_x2 - 8, y + 38), detail, detail_font, COLORS["muted"])

    @staticmethod
    def _ewc_date_label(event):
        start = (event or {}).get("start")
        end = (event or {}).get("end")
        if isinstance(start, datetime) and isinstance(end, datetime) and start.date() != end.date():
            if start.month == end.month:
                return f"{start.strftime('%b %d')}-{end.strftime('%d')}".upper()
            return f"{start.strftime('%b %d')}-{end.strftime('%b %d')}".upper()
        if isinstance(start, datetime):
            return start.strftime("%b %d").upper()
        return "--"

    @staticmethod
    def _ewc_countdown_label(event, now):
        start = (event or {}).get("start")
        if not isinstance(start, datetime) or not isinstance(now, datetime):
            return "MAIN EVENT"
        delta_days = (start.date() - now.date()).days
        if delta_days <= 0:
            return "STARTS TODAY"
        if delta_days == 1:
            return "STARTS TOMORROW"
        return f"STARTS IN {delta_days} DAYS"

    @staticmethod
    def _ewc_detail_label(event, compact=False):
        event = event or {}
        parts = []
        participants = SportsDashboard._ewc_participants_label(event)
        if participants:
            parts.append(participants)
        prize = str(event.get("prize_pool") or "").strip()
        if prize:
            parts.append(prize if compact else f"PRIZE {prize}")
        if not compact:
            duration = SportsDashboard._ewc_duration_label(event)
            if duration:
                parts.append(duration)
        return "  |  ".join(parts) or "MAIN EVENT"

    @staticmethod
    def _ewc_duration_label(event):
        start = (event or {}).get("start")
        end = (event or {}).get("end")
        if isinstance(start, datetime) and isinstance(end, datetime):
            days = max(1, (end.date() - start.date()).days + 1)
            return f"{days} DAYS"
        return ""

    @staticmethod
    def _ewc_participants_label(event):
        count = (event or {}).get("participant_count")
        label = str((event or {}).get("participant_label") or "").strip().upper()
        if count is None:
            return ""
        label = label or "ENTRIES"
        return f"{count} {label}"

    def _draw_valve_esports_sidebar(self, image, left_width, selected, source_state, now):
        draw = ImageDraw.Draw(image)
        width, height = image.size
        right_x = left_width + LPL_SEPARATOR_WIDTH
        right_w = width - right_x
        primary = (selected or {}).get("primary") or {}
        main_event = primary.get("main") or {}
        recent = primary.get("recent") or []
        status = str(primary.get("status") or "ACTIVE").upper()
        accent = self._valve_series_accent(primary, status)

        draw.rectangle((left_width, 0, right_x - 1, height), fill=COLORS["paper"])
        draw.line((left_width, 0, left_width, height), fill=COLORS["border"], width=1)
        if LPL_SEPARATOR_WIDTH > 2:
            draw.line((left_width + 2, 0, left_width + 2, height), fill=COLORS["line"], width=1)
        draw.rectangle((right_x, 0, width - 1, height - 1), fill=COLORS["panel"])
        self._draw_halftone(draw, (right_x, 0, width - 1, height - 1), self._valve_series_shadow(primary), COLORS["panel"], 20, 1)
        draw.line((right_x, 0, right_x, height), fill=COLORS["border"], width=1)

        header_y = 10
        panel_left = right_x + 12
        panel_right = right_x + right_w - 12
        series = str(primary.get("series") or "").upper()
        header_title = {"CS": "Counter-Strike 2", "TI": "Dota 2"}.get(series, "")
        status_text = self._valve_status_pill_text(primary)
        if header_title:
            logo_size = 40
            logo_x = panel_left + 2
            logo_y = header_y + 3
            title_left = logo_x + logo_size + 9
            badge_width = 58
            badge_x = panel_right - badge_width
            self._draw_valve_esports_logo(image, draw, logo_x, logo_y, logo_size, logo_size, primary)
            title_text, title_font = self._fit_text_ellipsis(
                draw,
                header_title,
                max(1, panel_right - title_left),
                15,
                bold=True,
                min_size=10,
            )
            self._draw_text_in_box(
                draw,
                (title_left, header_y + 4, panel_right, header_y + 25),
                title_text,
                title_font,
                COLORS["text"],
                align="left",
            )
            source_label = self._source_label(source_state)
            source_label, source_font = self._fit_text_ellipsis(
                draw,
                source_label,
                max(1, badge_x - title_left - 6),
                8,
                bold=True,
                min_size=6,
            )
            self._draw_text_in_box(
                draw,
                (title_left, header_y + 30, badge_x - 6, header_y + 46),
                source_label,
                source_font,
                COLORS["muted"],
                align="left",
            )
            self._draw_valve_status_badge(draw, badge_x, header_y + 29, badge_width, 18, status_text, status == "LIVE")
        else:
            self._draw_valve_esports_logo(image, draw, panel_left + 1, header_y + 4, 70, 40, primary)
            source_label = self._source_label(source_state)
            source_label, source_font = self._fit_text_ellipsis(draw, source_label, 68, 9, bold=True, min_size=7)
            self._draw_text_in_box(
                draw,
                (right_x + 88, header_y + 9, panel_right - 68, header_y + 31),
                source_label,
                source_font,
                COLORS["muted"],
                align="center",
            )
            self._draw_valve_status_badge(draw, panel_right - 58, header_y + 14, 58, 18, status_text, status == "LIVE")
        draw.line((panel_left + 2, 66, panel_right - 2, 66), fill=COLORS["border"], width=1)

        self._draw_valve_esports_focus_card(image, draw, right_x, right_w, 78, primary, main_event, now, accent)
        rows = [event for event in recent if event is not main_event][:3]
        self._draw_valve_esports_recent_rows(image, draw, right_x, right_w, 282, rows, primary, accent)

    @staticmethod
    def _valve_series_key(primary):
        series = str((primary or {}).get("series") or "").strip().upper()
        return "ti" if series == "TI" else "cs"

    @staticmethod
    def _valve_series_accent(primary, status=None):
        if str(status or (primary or {}).get("status") or "").strip().upper() == "LIVE":
            return COLORS["red"]
        return COLORS["valve_ti_accent"] if SportsDashboard._valve_series_key(primary) == "ti" else COLORS["valve_cs_accent"]

    @staticmethod
    def _valve_series_tag_fill(primary):
        return COLORS["valve_ti_tag"] if SportsDashboard._valve_series_key(primary) == "ti" else COLORS["valve_cs_tag"]

    @staticmethod
    def _valve_series_shadow(primary):
        if SportsDashboard._valve_series_key(primary) == "ti":
            return COLORS["valve_shadow"]
        return COLORS["valve_cs_accent"]

    @staticmethod
    def _valve_focus_header_layout(card_x1, card_x2, y):
        return {
            "tag_box": (card_x1 + 16, y + 12, card_x2 - 16, y + 30),
            "date_box": (card_x2 - 92, y + 32, card_x2 - 16, y + 42),
            "title_box": (card_x1 + 18, y + 46, card_x2 - 20, y + 64),
            "subtitle_box": (card_x1 + 19, y + 70, card_x2 - 20, y + 81),
        }

    def _draw_valve_esports_logo(self, image, draw, x, y, width, height, primary):
        logo_path = (primary or {}).get("logo_path") or ""
        logo = self._load_local_logo(logo_path, (int(width), int(height)), alpha_threshold=8)
        if logo:
            image.paste(logo, (int(x) + (int(width) - logo.width) // 2, int(y) + (int(height) - logo.height) // 2), logo)
            return
        fallback_text = "CS" if str((primary or {}).get("series") or "").upper() == "CS" else "D2"
        draw.rounded_rectangle((x, y, x + width, y + height), radius=5, fill=self._valve_series_tag_fill(primary), outline=COLORS["border"], width=2)
        draw.rectangle((x + 5, y + 5, x + 13, y + height - 5), fill=self._valve_series_accent(primary), outline=COLORS["border"], width=1)
        text, font = self._fit_text_ellipsis(draw, fallback_text, width - 28, max(16, int(height * 0.62)), bold=True, min_size=13)
        self._draw_centered(draw, (x + width / 2 + 4, y + height / 2), text, font, COLORS["text"])

    def _draw_valve_status_badge(self, draw, x, y, width, height, text, is_live):
        color = COLORS["red"] if is_live else COLORS["green"]
        draw.rounded_rectangle((x, y, x + width, y + height), radius=4, outline=COLORS["border"], fill=COLORS["panel"], width=1)
        dot_size = max(6, min(9, int(height * 0.46)))
        dot_y = y + (height - dot_size) // 2
        draw.rectangle((x + 5, dot_y, x + 5 + dot_size, dot_y + dot_size), fill=color, outline=COLORS["border"], width=1)
        value, value_font = self._fit_text_ellipsis(draw, text, width - dot_size - 14, 9, bold=True, min_size=7)
        self._draw_text_in_box(draw, (x + dot_size + 10, y + 1, x + width - 4, y + height - 1), value, value_font, COLORS["text"])

    @staticmethod
    def _valve_status_pill_text(primary):
        status = str((primary or {}).get("status") or "ACTIVE").strip().upper()
        if status == "LIVE":
            return "LIVE"
        if status == "NEXT":
            return "NEXT"
        if status == "RECENT":
            return "RECENT"
        return "ACTIVE"

    def _draw_valve_esports_focus_card(self, image, draw, right_x, right_w, y, primary, event, now, accent):
        card_x1 = right_x + 12
        card_x2 = right_x + right_w - 12
        card_y2 = y + 188
        draw.rounded_rectangle((card_x1 + 4, y + 4, card_x2 + 4, card_y2 + 4), radius=6, fill=COLORS["valve_shadow"])
        draw.rounded_rectangle((card_x1, y, card_x2, card_y2), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((card_x1 + 1, y + 1, card_x1 + 8, card_y2 - 1), fill=accent)

        if not event:
            draw.text((card_x1 + 20, y + 58), "No Valve event", font=self._font(19, True), fill=COLORS["text"])
            return

        header = self._valve_focus_header_layout(card_x1, card_x2, y)
        tag_box = header["tag_box"]
        tag = str((primary or {}).get("sport") or "VALVE").upper()
        tag_text, tag_font = self._fit_text_ellipsis(draw, tag, tag_box[2] - tag_box[0] - 12, 11, bold=True, min_size=7)
        draw.rectangle(tag_box, fill=self._valve_series_tag_fill(primary), outline=COLORS["border"], width=1)
        self._draw_text_in_box(draw, (tag_box[0] + 6, tag_box[1], tag_box[2] - 6, tag_box[3]), tag_text, tag_font, COLORS["text"])

        date_label = self._valve_event_date_label(primary, event)
        date_box = header["date_box"]
        date_label, date_font = self._fit_text_ellipsis(draw, date_label, date_box[2] - date_box[0], 9, bold=True, min_size=7)
        self._draw_text_in_box(draw, date_box, date_label, date_font, COLORS["muted"], align="right")

        title = str((primary or {}).get("event_name") or "Valve Event").strip() or "Valve Event"
        title_box = header["title_box"]
        title, title_font = self._fit_text_ellipsis(draw, title, title_box[2] - title_box[0], 18, bold=True, min_size=11)
        self._draw_text_in_box(draw, title_box, title, title_font, COLORS["text"])
        subtitle = f"{event.get('source') or primary.get('source') or 'Valve'} TRACK"
        subtitle_box = header["subtitle_box"]
        subtitle, subtitle_font = self._fit_text_ellipsis(draw, subtitle, subtitle_box[2] - subtitle_box[0], 9, bold=True, min_size=7)
        self._draw_text_in_box(draw, subtitle_box, subtitle, subtitle_font, accent)

        center_x = (card_x1 + card_x2) / 2
        board_y1 = y + 88
        board_y2 = y + 153
        draw.rounded_rectangle((card_x1 + 16, board_y1, card_x2 - 16, board_y2), radius=5, fill=COLORS["panel_blue"], outline=COLORS["border"], width=1)
        logo_size = 30
        left_area = (card_x1 + 24, center_x - 35)
        right_area = (center_x + 35, card_x2 - 24)
        left_logo_x = int((left_area[0] + left_area[1] - logo_size) / 2)
        right_logo_x = int((right_area[0] + right_area[1] - logo_size) / 2)
        logo_y = int(board_y1 + 7)
        self._draw_valve_team_icon(image, draw, event, "a", left_logo_x, logo_y, logo_size)
        self._draw_valve_team_icon(image, draw, event, "b", right_logo_x, logo_y, logo_size)

        score = self._valve_score_label(event)
        score, score_font = self._fit_text_ellipsis(draw, score, 68, 25, bold=True, min_size=16)
        self._draw_centered_in_box(draw, (center_x - 34, board_y1 + 6, center_x + 34, board_y1 + 35), score, score_font, COLORS["text"])
        score_kind = str(event.get("score_kind") or "").strip().upper()
        if score_kind:
            kind_text, kind_font = self._fit_text_ellipsis(draw, score_kind, 62, 8, bold=True, min_size=6)
            self._draw_centered_in_box(draw, (center_x - 31, board_y1 + 36, center_x + 31, board_y1 + 49), kind_text, kind_font, COLORS["muted"])

        team_a_label = self._valve_team_display_name(event, "a")
        team_b_label = self._valve_team_display_name(event, "b")
        team_a, font_a = self._fit_text_ellipsis(draw, team_a_label, left_area[1] - left_area[0], 13, bold=True, min_size=8)
        team_b, font_b = self._fit_text_ellipsis(draw, team_b_label, right_area[1] - right_area[0], 13, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (left_area[0], board_y2 - 17, left_area[1], board_y2 - 3), team_a, font_a, COLORS["text"])
        self._draw_centered_in_box(draw, (right_area[0], board_y2 - 17, right_area[1], board_y2 - 3), team_b, font_b, COLORS["text"])

        detail = self._valve_match_detail_label(event)
        detail, detail_font = self._fit_text_ellipsis(draw, detail, card_x2 - card_x1 - 44, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (card_x1 + 20, y + 160, card_x2 - 20, y + 176), detail, detail_font, COLORS["muted"])

    @staticmethod
    def _valve_team_display_name(event, side):
        event = event or {}
        if side == "a":
            return str(event.get("team_a_tag") or event.get("team_a") or "TBD").strip() or "TBD"
        return str(event.get("team_b_tag") or event.get("team_b") or "TBD").strip() or "TBD"

    def _draw_valve_team_icon(self, image, draw, event, side, x, y, size):
        logo_url = str((event or {}).get("team_a_logo" if side == "a" else "team_b_logo") or "").strip()
        name = str((event or {}).get("team_a" if side == "a" else "team_b") or "TBD").strip() or "TBD"
        team_id = (event or {}).get("team_a_id" if side == "a" else "team_b_id")
        series = str((event or {}).get("series") or "").strip().upper()
        logo = self._load_team_logo(logo_url, int(size)) if logo_url else None
        if not logo:
            logo = self._load_valve_local_team_logo(name, team_id, int(size), series)
        if logo:
            image.paste(logo, (int(x) + (int(size) - logo.width) // 2, int(y) + (int(size) - logo.height) // 2), logo)
            return
        fill, stripe = self._valve_team_icon_colors(name, team_id)
        draw.rounded_rectangle((x, y, x + size, y + size), radius=5, fill=fill, outline=COLORS["border"], width=1)
        draw.rectangle((x + 3, y + 3, x + 7, y + size - 3), fill=stripe)
        initials = self._valve_team_initials(name)
        initials, font = self._fit_text(draw, initials, max(12, size - 13), max(12, int(size * 0.42)), bold=True, min_size=8)
        self._draw_centered(draw, (x + size / 2 + 3, y + size / 2), initials, font, COLORS["text"])

    @staticmethod
    def _load_valve_local_team_logo(name, team_id, size, series=None):
        for path in SportsDashboard._valve_local_team_logo_candidates(name, team_id, series):
            logo = SportsDashboard._load_local_logo(path, (size, size))
            if logo:
                return logo
        return None

    @staticmethod
    def _valve_local_team_logo_dirs(series):
        series = str(series or "").strip().upper()
        if series == "TI":
            return [LOCAL_DOTA2_TEAM_LOGO_DIR]
        if series == "CS":
            return [LOCAL_CS2_TEAM_LOGO_DIR]
        return [LOCAL_CS2_TEAM_LOGO_DIR, LOCAL_DOTA2_TEAM_LOGO_DIR]

    @staticmethod
    def _valve_local_team_logo_candidates(name, team_id, series=None):
        candidates = []
        logo_dirs = SportsDashboard._valve_local_team_logo_dirs(series)
        team_id_value = SportsDashboard._lpl_int_value(team_id)
        if team_id_value:
            for logo_dir in logo_dirs:
                candidates.extend(
                    os.path.join(logo_dir, f"{team_id_value}{extension}")
                    for extension in (".png", ".webp", ".jpg", ".jpeg")
                )
        slug = SportsDashboard._valve_team_logo_slug(name)
        if slug:
            for logo_dir in logo_dirs:
                candidates.extend(
                    os.path.join(logo_dir, f"{slug}{extension}")
                    for extension in (".png", ".webp", ".jpg", ".jpeg")
                )
        return candidates

    @staticmethod
    def _valve_team_logo_slug(name):
        normalized = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
        return "".join(ch for ch in normalized.lower() if ch.isalnum())

    @staticmethod
    def _valve_team_initials(name):
        words = [part for part in str(name or "").replace("_", " ").replace("-", " ").split() if part]
        if not words:
            return "?"
        if len(words) >= 2:
            return "".join(part[0] for part in words[:3]).upper()
        letters = "".join(ch for ch in words[0].upper() if ch.isalnum())
        return (letters[:3] or "?")

    @staticmethod
    def _valve_team_icon_colors(name, team_id=None):
        palette = [
            ((34, 73, 128), COLORS["amber"]),
            ((92, 38, 116), COLORS["cyan"]),
            ((34, 104, 89), COLORS["orange"]),
            ((126, 48, 54), COLORS["amber"]),
            ((72, 79, 96), COLORS["green"]),
            ((44, 92, 147), COLORS["red"]),
        ]
        seed_text = f"{name or ''}:{team_id or ''}"
        seed = sum((index + 1) * ord(ch) for index, ch in enumerate(seed_text))
        return palette[seed % len(palette)]

    def _draw_valve_esports_recent_rows(self, image, draw, right_x, right_w, y, events, primary, accent):
        self._draw_section_header(draw, right_x, right_w, y, "RECENT", accent)
        if not events:
            draw.text((right_x + 18, y + 36), "No more Valve results", font=self._font(14, True), fill=COLORS["muted"])
            return
        row_y = y + 29
        for index, event in enumerate(events[:3]):
            top = row_y + index * 55
            self._draw_valve_esports_recent_row(image, draw, right_x, right_w, top, event, accent)

    def _draw_valve_esports_recent_row(self, image, draw, right_x, right_w, y, event, accent):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        row_h = 50
        draw.rounded_rectangle((row_x1, y, row_x2, y + row_h), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((row_x1 + 1, y + 1, row_x1 + 5, y + row_h - 1), fill=accent)
        date_label = event["start"].strftime("%m/%d") if isinstance(event.get("start"), datetime) else "--/--"
        date_label, date_font = self._fit_text_ellipsis(draw, date_label, 40, 8, bold=True, min_size=6)
        draw.text((row_x1 + 10, y + 4), date_label, font=date_font, fill=COLORS["muted"])
        score = self._valve_score_label(event)
        score, score_font = self._fit_text_ellipsis(draw, score, 40, 13, bold=True, min_size=9)
        self._draw_centered_in_box(draw, (row_x1 + 91, y + 4, row_x2 - 91, y + 20), score, score_font, COLORS["text"])
        icon_size = 17
        team_y1 = y + 21
        icon_y = y + 21
        center_x = (row_x1 + row_x2) / 2
        self._draw_valve_team_icon(image, draw, event, "a", row_x1 + 12, icon_y, icon_size)
        self._draw_valve_team_icon(image, draw, event, "b", row_x2 - 29, icon_y, icon_size)
        left_name_box = (row_x1 + 33, team_y1 - 1, center_x - 24, team_y1 + 18)
        right_name_box = (center_x + 24, team_y1 - 1, row_x2 - 33, team_y1 + 18)
        team_a, team_a_font = self._fit_text_ellipsis(draw, self._valve_team_display_name(event, "a"), left_name_box[2] - left_name_box[0], 10, bold=True, min_size=7)
        team_b, team_b_font = self._fit_text_ellipsis(draw, self._valve_team_display_name(event, "b"), right_name_box[2] - right_name_box[0], 10, bold=True, min_size=7)
        self._draw_text_in_box(draw, left_name_box, team_a, team_a_font, COLORS["text"])
        self._draw_text_in_box(draw, right_name_box, team_b, team_b_font, COLORS["text"], align="right")
        detail = self._valve_match_detail_label(event, compact=True)
        detail, detail_font = self._fit_text_ellipsis(draw, detail, row_x2 - row_x1 - 22, 7, bold=True, min_size=6)
        self._draw_centered_in_box(draw, (row_x1 + 10, y + 38, row_x2 - 10, y + row_h - 1), detail, detail_font, COLORS["muted"])

    @staticmethod
    def _valve_event_date_label(primary, event):
        start = (primary or {}).get("start") or (event or {}).get("start")
        end = (primary or {}).get("latest")
        if isinstance(start, datetime) and isinstance(end, datetime) and start.date() != end.date():
            return f"{start.strftime('%m/%d')}-{end.strftime('%m/%d')}"
        if isinstance(start, datetime):
            return start.strftime("%m/%d")
        return "--/--"

    @staticmethod
    def _valve_match_detail_label(event, compact=False):
        event = event or {}
        maps = event.get("maps") or []
        if maps:
            parts = []
            for item in maps[:3 if compact else 4]:
                left = SportsDashboard._lpl_int_value(item.get("team_a_score"))
                right = SportsDashboard._lpl_int_value(item.get("team_b_score"))
                score = f" {left}-{right}" if left is not None and right is not None else ""
                parts.append(f"{item.get('name') or 'Map'}{score}")
            return "  |  ".join(parts)
        duration = SportsDashboard._lpl_int_value(event.get("duration"))
        best_of = SportsDashboard._lpl_int_value(event.get("best_of"))
        bits = []
        if best_of:
            bits.append(f"BO{best_of}")
        if duration:
            bits.append(f"{max(1, duration // 60)}m")
        return "  |  ".join(bits) or str(event.get("source") or "Valve")

    @staticmethod
    def _lol_sidebar_config(league_key):
        key = str(league_key or "LPL").strip().upper()
        if key == "LCK":
            return {
                "key": "LCK",
                "name": "LCK",
                "logo_path": LOCAL_LCK_LOGO_PATH,
                "accent": "lck_accent",
                "live": "lck_live",
                "tag": "lck_tag",
                "shadow": "lck_shadow",
                "empty_schedule": "No LCK schedule",
                "empty_upcoming": "No more LCK schedule",
            }
        if key == "MSI":
            return {
                "key": "MSI",
                "name": "MSI",
                "logo_path": LOCAL_MSI_LOGO_PATH,
                "accent": "msi_accent",
                "live": "msi_live",
                "tag": "msi_tag",
                "shadow": "msi_shadow",
                "empty_schedule": "No MSI schedule",
                "empty_upcoming": "No more MSI schedule",
            }
        return {
            "key": "LPL",
            "name": "LPL",
            "logo_path": LOCAL_LPL_LOGO_PATH,
            "accent": "lpl_accent",
            "live": "lpl_live",
            "tag": "lpl_tag",
            "shadow": "lpl_shadow",
            "empty_schedule": "No LPL schedule",
            "empty_upcoming": "No more LPL schedule",
        }

    @staticmethod
    def _lol_sidebar_color(league_key, role):
        config = SportsDashboard._lol_sidebar_config(league_key)
        return COLORS[config.get(role, "lpl_accent")]

    def _draw_lpl_sidebar(self, image, left_width, selected, source_state, now, league_key="LPL"):
        config = self._lol_sidebar_config(league_key)
        draw = ImageDraw.Draw(image)
        width, height = image.size
        right_x = left_width + LPL_SEPARATOR_WIDTH
        right_w = width - right_x
        draw.rectangle((left_width, 0, right_x - 1, height), fill=COLORS["paper"])
        draw.line((left_width, 0, left_width, height), fill=COLORS["border"], width=1)
        if LPL_SEPARATOR_WIDTH > 2:
            draw.line((left_width + 2, 0, left_width + 2, height), fill=COLORS["line"], width=1)
        draw.rectangle((right_x, 0, width - 1, height - 1), fill=COLORS["panel"])
        self._draw_halftone(draw, (right_x, 0, width - 1, height - 1), COLORS[config["shadow"]], COLORS["panel"], 20, 1)
        draw.line((right_x, 0, right_x, height), fill=COLORS["border"], width=1)

        live = selected.get("live") or []
        upcoming = selected.get("upcoming") or []
        recent = selected.get("recent") or []
        featured_event = selected.get("featured_event") or None
        featured_event_page = bool(selected.get("featured_event_page"))
        main_event = live[0] if live else (upcoming[0] if upcoming else selected.get("main"))
        remaining_upcoming = [event for event in upcoming if event is not main_event][:2]
        logo_path = featured_event.get("logo_path") if (featured_event and (featured_event_page or live or upcoming)) else (None if config["key"] == "LPL" else config["logo_path"])

        header_y = 12

        logo_x = right_x + 13
        logo_y = header_y + 5
        logo_w, logo_h = LOL_HEADER_LOGO_SIZE
        if logo_path == LOCAL_MSI_LOGO_PATH:
            logo_w, logo_h = MSI_HEADER_LOGO_SIZE
            logo_x += (LOL_HEADER_LOGO_SIZE[0] - logo_w) // 2
            logo_y += (LOL_HEADER_LOGO_SIZE[1] - logo_h) // 2
        self._draw_lpl_logo(image, draw, logo_x, logo_y, logo_w, logo_h, logo_path=logo_path, fallback_text=config["key"])
        source_label = "MSI WATCH" if featured_event_page and config["key"] == "LPL" else self._source_label(source_state)
        source_label, source_font = self._fit_text(draw, source_label, 62, 10, bold=True, min_size=8)
        self._draw_text_in_box(
            draw,
            (right_x + 90, header_y + 9, right_x + right_w - 92, header_y + 32),
            source_label,
            source_font,
            COLORS["muted"],
            align="center",
        )
        if live:
            pill_text = "LIVE"
        elif featured_event_page:
            pill_text = self._lpl_featured_event_pill_text(featured_event)
        else:
            pill_text = "NEXT"
        self._draw_status_pill(draw, right_x + right_w - 88, header_y + 8, pill_text, bool(live))
        draw.line((right_x + 14, 66, right_x + right_w - 14, 66), fill=COLORS["border"], width=1)

        if featured_event_page:
            self._draw_lpl_featured_event_panel(image, draw, right_x, right_w, 78, height - 1, selected, now)
            return

        msi_next_filler_event = self._lpl_msi_next_filler_event(now, featured_event)
        self._draw_lpl_focus_card(image, draw, right_x, right_w, 78, main_event, now, bool(live), league_key=league_key)
        self._draw_lpl_next_rows(
            image,
            draw,
            right_x,
            right_w,
            244,
            remaining_upcoming,
            now,
            bool(live),
            msi_next_filler=bool(msi_next_filler_event),
            msi_next_start=(msi_next_filler_event or {}).get("start"),
            league_key=league_key,
        )
        self._draw_lpl_recent_rows(image, draw, right_x, right_w, 374, recent[:2], league_key=league_key)

    def _draw_lpl_logo(self, image, draw, x, y, width, height, logo_path=None, fallback_text=None):
        x = int(x)
        y = int(y)
        width = int(width)
        height = int(height)
        logo_path = logo_path or LOCAL_LPL_LOGO_PATH
        logo = self._load_local_logo(logo_path, (width, height), alpha_threshold=8)
        if logo:
            image.paste(logo, (x + (width - logo.width) // 2, y + (height - logo.height) // 2), logo)
            return
        fallback_text = fallback_text or ("MSI" if logo_path == LOCAL_MSI_LOGO_PATH else "LPL")
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=5,
            fill=COLORS["panel_gold"],
            outline=COLORS["border"],
            width=2,
        )
        stripe_w = max(7, int(width * 0.16))
        draw.rectangle((x + 5, y + 5, x + 5 + stripe_w, y + height - 5), fill=COLORS["red"], outline=COLORS["border"], width=1)
        draw.polygon(
            [
                (x + width - 12, y + 5),
                (x + width - 5, y + 5),
                (x + width - 5, y + height - 5),
                (x + width - 18, y + height - 5),
            ],
            fill=COLORS["blue"],
            outline=COLORS["border"],
        )
        text, font = self._fit_text(draw, fallback_text, width - stripe_w - 22, max(16, int(height * 0.62)), bold=True, min_size=13)
        self._draw_centered(draw, (x + width / 2 + 3, y + height / 2), text, font, COLORS["text"])

    @staticmethod
    def _lpl_featured_event_pill_text(featured_event):
        featured_event = featured_event or {}
        if featured_event.get("phase") == "countdown":
            days = SportsDashboard._lpl_int_value(featured_event.get("countdown_days"))
            if days and days > 0:
                return f"D-{days}"
            return "TODAY"
        return str(featured_event.get("name") or "MSI" or "NEXT").strip()[:6]

    def _draw_lpl_featured_event_panel(self, image, draw, right_x, right_w, y1, y2, selected, now):
        featured = (selected or {}).get("featured_event") or {}
        phase = str(featured.get("phase") or "").strip().lower()
        is_countdown = phase == "countdown"
        card_x1 = right_x + 12
        card_x2 = right_x + right_w - 12
        card_y2 = min(y2 - 96, y1 + 196)
        if card_y2 < y1 + 158:
            card_y2 = min(y2, y1 + 158)
        accent = COLORS["lpl_accent"]
        draw.rounded_rectangle((card_x1 + 4, y1 + 4, card_x2 + 4, card_y2 + 4), radius=6, fill=COLORS["lpl_shadow"])
        draw.rounded_rectangle((card_x1, y1, card_x2, card_y2), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((card_x1 + 1, y1 + 1, card_x1 + 8, card_y2 - 1), fill=accent)

        tag = "OFFSEASON" if is_countdown else "FEATURED"
        tag_w = 92 if is_countdown else 82
        tag_text, tag_font = self._fit_text(draw, tag, tag_w - 10, 11, bold=True, min_size=7)
        draw.rectangle((card_x1 + 16, y1 + 12, card_x1 + 16 + tag_w, y1 + 30), fill=COLORS["lpl_tag"], outline=COLORS["border"], width=1)
        draw.text((card_x1 + 21, y1 + 13), tag_text, font=tag_font, fill=COLORS["text"])

        start = featured.get("start")
        end = featured.get("end")
        date_text = start.strftime("%m/%d") if isinstance(start, datetime) else "06/28"
        date_text, date_font = self._fit_text(draw, date_text, 54, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (card_x2 - 13, y1 + 13), date_text, date_font, COLORS["muted"])

        title = "\u4f11\u8d5b\u671f" if is_countdown else "MSI\u8fdb\u884c\u4e2d"
        title, title_font = self._fit_text(draw, title, card_x2 - card_x1 - 94, 29, bold=True, min_size=20)
        title_y = y1 + 41 if is_countdown else y1 + 44
        draw.text((card_x1 + 18, title_y), title, font=title_font, fill=COLORS["text"])
        subtitle = "LPL SEASON BREAK" if is_countdown else "MID-SEASON INVITATIONAL"
        subtitle, subtitle_font = self._fit_text(draw, subtitle, card_x2 - card_x1 - 102, 10, bold=True, min_size=7)
        draw.text((card_x1 + 19, y1 + 77), subtitle, font=subtitle_font, fill=COLORS["lpl_accent"])
        card_accent = self._load_lpl_msi_card_accent((94, 68), now)
        if card_accent:
            image.paste(card_accent, (card_x2 - 112, y1 + 28), card_accent)

        next_y1 = y1 + 103
        next_y2 = min(card_y2 - 14, next_y1 + 58)
        draw.rounded_rectangle((card_x1 + 16, next_y1, card_x2 - 16, next_y2), radius=5, fill=COLORS["panel_blue"], outline=COLORS["border"], width=1)
        label = "\u4e0b\u4e00\u7ad9 MSI" if is_countdown else "MSI STATUS"
        label, label_font = self._fit_text(draw, label, 92, 10, bold=True, min_size=7)
        draw.text((card_x1 + 25, next_y1 + 5), label, font=label_font, fill=COLORS["muted"])
        primary = self._lpl_featured_event_pill_text(featured) if is_countdown else "LIVE"
        primary, primary_font = self._fit_text(draw, primary, 78, 24, bold=True, min_size=16)
        self._draw_right_aligned(draw, (card_x2 - 26, next_y1 + 1), primary, primary_font, COLORS["text"])
        if is_countdown:
            secondary = f"{date_text} \u5f00\u8d5b"
        elif isinstance(start, datetime) and isinstance(end, datetime):
            secondary = f"{start.strftime('%m/%d')}-{end.strftime('%m/%d')}"
        else:
            secondary = "\u8d5b\u7a0b\u8fdb\u884c\u4e2d"
        secondary, secondary_font = self._fit_text(draw, secondary, card_x2 - card_x1 - 50, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (card_x1 + 24, next_y1 + 34, card_x2 - 24, next_y2 - 3), secondary, secondary_font, COLORS["muted"])

        watch_y = card_y2 + 16
        if watch_y + 30 < y2:
            self._draw_section_header(draw, right_x, right_w, watch_y, "MSI WATCH", COLORS["lpl_accent"])
            row_y = watch_y + 30
            visible_count = 0
            for index, (date_label, title_label) in enumerate(self._lpl_featured_watch_items(featured, is_countdown)):
                top = row_y + index * 32
                if top + 25 > y2:
                    break
                visible_count += 1
                row_x1 = right_x + 14
                row_x2 = right_x + right_w - 14
                draw.rounded_rectangle((row_x1, top, row_x2, top + 25), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
                draw.rectangle((row_x1 + 1, top + 1, row_x1 + 5, top + 24), fill=COLORS["lpl_accent"])
                date_label, date_font = self._fit_text(draw, date_label, 46, 10, bold=True, min_size=7)
                draw.text((row_x1 + 10, top + 3), date_label, font=date_font, fill=COLORS["muted"])
                title_label, title_font = self._fit_text(draw, title_label, row_x2 - row_x1 - 66, 11, bold=True, min_size=7)
                self._draw_right_aligned(draw, (row_x2 - 9, top + 3), title_label, title_font, COLORS["text"])
            filler_top = row_y + visible_count * 32 + 4
            self._draw_lpl_featured_event_filler(image, right_x, right_x + right_w - 1, filler_top, y2, now)

    @staticmethod
    def _lpl_featured_watch_items(featured, is_countdown):
        start = (featured or {}).get("start")
        end = (featured or {}).get("end")
        start_label = start.strftime("%m/%d") if isinstance(start, datetime) else "06/28"
        end_label = end.strftime("%m/%d") if isinstance(end, datetime) else "07/12"
        if is_countdown:
            return [
                (start_label, "MSI \u5f00\u8d5b"),
                (end_label, "MSI FINAL"),
                ("TBD", "LPL \u540e\u7eed\u8d5b\u7a0b"),
            ]
        return [
            ("NOW", "MSI \u8fdb\u884c\u4e2d"),
            (end_label, "MSI FINAL"),
            ("TBD", "LPL \u540e\u7eed\u8d5b\u7a0b"),
        ]

    def _draw_lpl_featured_event_filler(self, image, x1, x2, y1, y2, rotation_seed=None):
        x1 = int(x1)
        x2 = int(x2)
        y1 = int(y1)
        y2 = int(y2)
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        if width < 80 or height < 24:
            return
        overfill = min(LPL_MSI_OFFSEASON_FILLER_BOTTOM_OVERFILL, max(0, height - 1))
        target_height = height + overfill
        source_width = max(width, int(width * LPL_MSI_OFFSEASON_FILLER_ZOOM + 0.999))
        source_height = max(target_height, int(target_height * LPL_MSI_OFFSEASON_FILLER_ZOOM + 0.999))
        filler = self._load_lpl_msi_offseason_filler((source_width, source_height), rotation_seed)
        if filler:
            if filler.size[0] >= width and filler.size[1] >= target_height:
                crop_x = (filler.size[0] - width) // 2
                crop_y = filler.size[1] - target_height
                filler = filler.crop((crop_x, crop_y, crop_x + width, crop_y + target_height))
            elif filler.size != (width, target_height):
                filler = ImageOps.fit(filler, (width, target_height), method=Image.LANCZOS, centering=(0.5, 1.0))
            if filler.size[1] > height:
                crop_top = min(
                    LPL_MSI_OFFSEASON_FILLER_VERTICAL_CROP_OFFSET,
                    filler.size[1] - height,
                )
                filler = filler.crop((0, crop_top, width, crop_top + height))
            if filler.mode == "RGBA":
                image.paste(filler, (x1, y1), filler)
            else:
                image.paste(filler, (x1, y1))

    def _draw_lpl_odds_text(self, draw, box, text, max_size=11, align="center"):
        text = str(text or "").strip()
        if not text:
            return
        left, top, right, bottom = [int(value) for value in box]
        fitted, font = self._fit_text(draw, text, max(1, right - left), max_size, bold=True, min_size=7)
        if align == "center":
            self._draw_centered_in_box(draw, (left, top, right, bottom), fitted, font, COLORS["text"])
        else:
            self._draw_text_in_box(draw, (left, top, right, bottom), fitted, font, COLORS["text"], align=align)

    def _draw_lpl_focus_card(self, image, draw, right_x, right_w, y, event, now, is_live, league_key="LPL"):
        card_x1 = right_x + 12
        card_x2 = right_x + right_w - 12
        card_y2 = y + 154
        config = self._lol_sidebar_config(league_key)
        accent = COLORS[config["live"]] if is_live else COLORS[config["accent"]]
        draw.rounded_rectangle((card_x1 + 4, y + 4, card_x2 + 4, card_y2 + 4), radius=6, fill=COLORS[config["shadow"]])
        draw.rounded_rectangle((card_x1, y, card_x2, card_y2), radius=6, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((card_x1 + 1, y + 1, card_x1 + 8, card_y2 - 1), fill=accent)

        if not event:
            draw.text((card_x1 + 20, y + 58), config["empty_schedule"], font=self._font(19, True), fill=COLORS["text"])
            return

        tag = self._lpl_focus_tag(is_live)
        tag_w = 112 if is_live else 86
        tag_text, tag_font = self._fit_text(draw, tag, tag_w - 10, 12, bold=True, min_size=8)
        tag_fill = COLORS[config["live"]] if is_live else COLORS[config["tag"]]
        draw.rectangle((card_x1 + 16, y + 12, card_x1 + 16 + tag_w, y + 31), fill=tag_fill, outline=COLORS["border"], width=1)
        draw.text((card_x1 + 21, y + 13), tag_text, font=tag_font, fill=COLORS["text"])
        date_text = event["start"].strftime("%m/%d")
        date_text, date_font = self._fit_text(draw, date_text, 54, 12, bold=True, min_size=9)
        self._draw_right_aligned(draw, (card_x2 - 12, y + 13), date_text, date_font, COLORS["muted"])

        center_x = right_x + right_w / 2
        time_text = "IN PROGRESS" if is_live else self._format_time(event["start"])
        time_text, time_font = self._fit_text(draw, time_text, card_x2 - card_x1 - 58, 19, bold=True, min_size=13)
        self._draw_centered(draw, (center_x, y + 44), time_text, time_font, COLORS["text"])

        logo_size = 42
        left_area = (card_x1 + 22, center_x - 18)
        right_area = (center_x + 18, card_x2 - 22)
        left_logo_x = int((left_area[0] + left_area[1] - logo_size) / 2)
        right_logo_x = int((right_area[0] + right_area[1] - logo_size) / 2)
        logo_y = y + 61
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, logo_y, logo_size, event["team_a"])
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, logo_y, logo_size, event["team_b"])
        score_text = self._score_label(event).upper()
        center_score = score_text if is_live and score_text != "VS" else "VS"
        team_a_label = self._lpl_display_team_from_event(event, "a", league_key=league_key)
        team_b_label = self._lpl_display_team_from_event(event, "b", league_key=league_key)

        stage = self._lpl_stage_label(event, league_key=league_key)
        stage_is_lower_badge = config["key"] == "MSI"
        if not is_live and not stage_is_lower_badge:
            stage_text, stage_font = self._fit_text(draw, stage, 88, 12, bold=True, min_size=7)
            self._draw_centered_in_box(
                draw,
                (center_x - 44, y + 76, center_x + 44, y + 88),
                stage_text,
                stage_font,
                COLORS[config["accent"]],
            )
        self._draw_centered(draw, (center_x, y + (98 if not is_live else 86)), center_score, self._font(13, True), COLORS["text"])
        if is_live:
            self._draw_lpl_little_round(draw, center_x, y, event)

        team_y = y + 116
        team_a, font_a = self._fit_text(draw, team_a_label, left_area[1] - left_area[0], 22, bold=True, min_size=13)
        team_b, font_b = self._fit_text(draw, team_b_label, right_area[1] - right_area[0], 22, bold=True, min_size=13)
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, font_a, COLORS["text"])
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, font_b, COLORS["text"])

        if not is_live and stage_is_lower_badge:
            stage_text, stage_font = self._fit_text(draw, stage, card_x2 - card_x1 - 42, 11, bold=True, min_size=7)
            self._draw_centered_in_box(
                draw,
                (card_x1 + 21, y + 138, card_x2 - 21, y + 152),
                stage_text,
                stage_font,
                COLORS[config["accent"]],
            )

        odds = event.get("odds") or {}
        has_odds = bool(odds.get("team_a") and odds.get("team_b"))
        if has_odds:
            self._draw_lpl_odds_text(draw, (left_area[0], y + 132, left_area[1], y + 144), odds.get("team_a"), max_size=11)
            self._draw_lpl_odds_text(draw, (right_area[0], y + 132, right_area[1], y + 144), odds.get("team_b"), max_size=11)
        elif is_live:
            block_text, block_font = self._fit_text(draw, stage, card_x2 - card_x1 - 34, 12, bold=True, min_size=8)
            draw.text((card_x1 + 17, y + 136), block_text, font=block_font, fill=COLORS[config["accent"]])

    def _draw_lpl_little_round(self, draw, center_x, y, event):
        little_round = (event or {}).get("little_round") or {}
        if not little_round:
            return
        score = str(little_round.get("score") or "").strip()
        if not score:
            return
        label, label_font = self._fit_text(draw, "Little Round", 78, 9, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (center_x - 40, y + 96, center_x + 40, y + 106), label, label_font, COLORS["muted"])
        score_text, score_font = self._fit_text(draw, score, 48, 12, bold=True, min_size=9)
        self._draw_centered_in_box(draw, (center_x - 24, y + 106, center_x + 24, y + 119), score_text, score_font, COLORS["amber"])

    @staticmethod
    def _lpl_focus_tag(is_live):
        return "NOW PLAYING" if is_live else "NEXT MATCH"

    def _draw_lpl_next_rows(self, image, draw, right_x, right_w, y, events, now, is_live, msi_next_filler=False, msi_next_start=None, league_key="LPL"):
        config = self._lol_sidebar_config(league_key)
        self._draw_section_header(draw, right_x, right_w, y, "UPCOMING", COLORS[config["accent"]])
        if not events:
            draw.text((right_x + 18, y + 38), config["empty_upcoming"], font=self._font(14, True), fill=COLORS["muted"])
            self._draw_lpl_empty_upcoming_filler(
                image,
                right_x,
                right_w,
                y,
                0,
                msi_next_filler=msi_next_filler,
                msi_next_start=msi_next_start,
            )
            return
        row_y = y + 30
        visible_events = events[:2]
        for index, event in enumerate(visible_events):
            self._draw_lpl_next_row(image, draw, right_x, right_w, row_y + index * 48, event, now, league_key=league_key)
        self._draw_lpl_empty_upcoming_filler(
            image,
            right_x,
            right_w,
            y,
            len(visible_events),
            msi_next_filler=msi_next_filler,
            msi_next_start=msi_next_start,
        )

    def _draw_lpl_empty_upcoming_filler(self, image, right_x, right_w, section_y, visible_count, msi_next_filler=False, msi_next_start=None):
        if visible_count >= 2:
            return
        x1 = int(right_x + 14)
        x2 = int(right_x + right_w - 14)
        y1 = int(section_y + (76 if visible_count <= 0 else 30 + visible_count * 48))
        y2 = int(section_y + 124)
        width = x2 - x1
        height = y2 - y1
        if width < 80 or height < 24:
            return
        if msi_next_filler:
            filler = self._load_lpl_msi_next_filler((width, height))
            if filler:
                image.paste(filler, (x1, y1))
                self._draw_lpl_msi_next_label(image, x1, y1, width, height, msi_next_start)
                return
        filler = self._load_lpl_sidebar_filler((width, height))
        if filler:
            image.paste(filler, (x1, y1), filler)

    def _draw_lpl_msi_next_label(self, image, x, y, width, height, start):
        date_label = start.strftime("%m/%d") if isinstance(start, datetime) else "TBD"
        label = f"MSI NEXT {date_label}"
        draw = ImageDraw.Draw(image)
        inset_x = max(18, int(width * 0.10))
        left = int(x + inset_x)
        right = int(x + width - inset_x - 1)
        top = int(y + max(13, height * 0.32))
        bottom = int(min(y + height - 4, top + max(22, int(height * 0.50))))
        if right - left < 80 or bottom - top < 14:
            return
        draw.rounded_rectangle((left, top, right, bottom), radius=3, fill=(5, 13, 26), outline=(221, 173, 82), width=1)
        draw.line((left + 5, top + 1, right - 5, top + 1), fill=(255, 227, 128), width=1)
        fitted, font = self._fit_text(draw, label, right - left - 8, 11, bold=True, min_size=8)
        self._draw_centered_in_box(draw, (left + 2, top, right - 2, bottom), fitted, font, (255, 239, 181))

    def _draw_lpl_next_row(self, image, draw, right_x, right_w, y, event, now, league_key="LPL"):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        draw.rounded_rectangle(
            (row_x1, y, row_x2, y + 44),
            radius=6,
            fill=COLORS["panel"],
            outline=COLORS["border"],
            width=1,
        )
        draw.rectangle((row_x1 + 1, y + 1, row_x1 + 5, y + 43), fill=self._lol_sidebar_color(league_key, "accent"))
        date_text, date_font = self._fit_text(draw, event["start"].strftime("%m/%d"), 44, 11, bold=True, min_size=8)
        draw.text((row_x1 + 12, y + 1), date_text, font=date_font, fill=COLORS["muted"])
        time_text, time_font = self._fit_text(draw, self._format_time(event["start"]), 76, 12, bold=True, min_size=9)
        self._draw_centered(draw, (right_x + right_w / 2, y + 7), time_text, time_font, COLORS["text"])

        logo_size = 19
        center_x = right_x + right_w / 2
        team_top = y + 16
        team_bottom = y + 32
        logo_y = int(team_top + (team_bottom - team_top - logo_size) / 2)
        left_logo_x = row_x1 + 12
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, logo_y, logo_size, event["team_a"])
        team_a_label = self._lpl_display_team_from_event(event, "a", league_key=league_key)
        team_a, font_a = self._fit_text(draw, team_a_label, 45, 13, bold=True, min_size=8)
        self._draw_text_in_box(draw, (row_x1 + 36, team_top, center_x - 16, team_bottom), team_a, font_a, COLORS["text"])
        self._draw_centered_in_box(draw, (center_x - 13, team_top, center_x + 13, team_bottom), "VS", self._font(10, True), COLORS["muted"])
        logo_x = row_x2 - 12 - logo_size
        self._draw_team_logo(image, draw, event.get("team_b_logo"), logo_x, logo_y, logo_size, event["team_b"])
        team_b_label = self._lpl_display_team_from_event(event, "b", league_key=league_key)
        team_b, font_b = self._fit_text(draw, team_b_label, 45, 13, bold=True, min_size=8)
        self._draw_text_in_box(draw, (center_x + 16, team_top, logo_x - 5, team_bottom), team_b, font_b, COLORS["text"], align="right")
        odds = event.get("odds") or {}
        if odds.get("team_a") and odds.get("team_b"):
            self._draw_lpl_odds_text(draw, (row_x1 + 36, y + 31, center_x - 16, y + 43), odds.get("team_a"), max_size=9, align="left")
            self._draw_lpl_odds_text(draw, (center_x + 16, y + 31, logo_x - 5, y + 43), odds.get("team_b"), max_size=9, align="right")

    def _draw_lpl_recent_rows(self, image, draw, right_x, right_w, y, events, league_key="LPL"):
        self._draw_section_header(draw, right_x, right_w, y, "RECENT", self._lol_sidebar_color(league_key, "accent"))
        if not events:
            draw.text((right_x + 18, y + 42), "No recent results", font=self._font(16, True), fill=COLORS["text"])
            return
        row_y = y + 28
        for index, event in enumerate(events[:2]):
            self._draw_lpl_recent_result_row(image, draw, right_x, right_w, row_y + index * 40, event, league_key=league_key)

    def _draw_lpl_recent_result_row(self, image, draw, right_x, right_w, y, event, league_key="LPL"):
        row_x1 = right_x + 14
        row_x2 = right_x + right_w - 14
        draw.line((row_x1, y - 6, row_x2, y - 6), fill=COLORS["line"], width=1)
        row_h = 30
        draw.text((row_x1 + 2, y + 8), event["start"].strftime("%m/%d"), font=self._font(11, True), fill=COLORS["text"])
        logo_size = 16
        score_w = 34
        match_x1 = row_x1 + 50
        score_x = int((match_x1 + row_x2) / 2 - score_w / 2)
        left_logo_x = match_x1
        left_text_x = left_logo_x + logo_size + 5
        left_text_w = max(22, score_x - left_text_x - 6)
        self._draw_team_logo(image, draw, event.get("team_a_logo"), left_logo_x, y + 7, logo_size, event["team_a"])
        team_a_label = self._lpl_display_team_from_event(event, "a", league_key=league_key)
        team_a, font_a = self._fit_text(draw, team_a_label, left_text_w, 12, bold=True, min_size=8)
        self._draw_text_in_box(draw, (left_text_x, y, score_x - 6, y + row_h), team_a, font_a, COLORS["text"])
        score = self._score_label(event)
        score_text, score_font = self._fit_text(draw, score, score_w, 12, bold=True, min_size=9)
        self._draw_centered_in_box(draw, (score_x, y, score_x + score_w, y + row_h), score_text, score_font, COLORS["text"])
        right_logo_x = row_x2 - logo_size
        right_text_x2 = right_logo_x - 5
        right_text_x1 = score_x + score_w + 6
        right_text_w = max(22, right_text_x2 - right_text_x1)
        self._draw_team_logo(image, draw, event.get("team_b_logo"), right_logo_x, y + 7, logo_size, event["team_b"])
        team_b_label = self._lpl_display_team_from_event(event, "b", league_key=league_key)
        team_b, font_b = self._fit_text(draw, team_b_label, right_text_w, 12, bold=True, min_size=8)
        self._draw_text_in_box(draw, (right_text_x1, y, right_text_x2, y + row_h), team_b, font_b, COLORS["text"], align="right")

    @staticmethod
    def _lpl_display_team_name(value):
        text = str(value or "").strip()
        if not text:
            return "TBD"
        code = text.upper()
        if code in LPL_TEAM_ZH_NAMES:
            return LPL_TEAM_ZH_NAMES[code]
        normalized = SportsDashboard._normalize_odds_team_name(text)
        for team_code, aliases in LPL_ODDS_TEAM_ALIASES.items():
            normalized_aliases = {
                SportsDashboard._normalize_odds_team_name(alias)
                for alias in (team_code, *aliases)
                if SportsDashboard._normalize_odds_team_name(alias)
            }
            if normalized in normalized_aliases:
                return LPL_TEAM_ZH_NAMES.get(team_code, team_code)
        return text

    @staticmethod
    def _lpl_display_team_from_event(event, side, league_key="LPL"):
        key = "team_a" if side == "a" else "team_b"
        value = (event or {}).get(key)
        if str(league_key or "LPL").strip().upper() == "LPL":
            return SportsDashboard._lpl_display_team_name(value)
        text = str(value or "").strip()
        return text or "TBD"

    @staticmethod
    def _lpl_stage_label(event, league_key="LPL"):
        event = event or {}
        for key in ("stage_label", "round_label", "stage", "round", "phase", "block"):
            value = event.get(key)
            label = SportsDashboard._canonical_lpl_stage_label(value)
            if label:
                return label
        for key in ("stage_label", "round_label", "stage", "round", "phase", "block"):
            value = event.get(key)
            if value:
                return SportsDashboard._format_lpl_stage_label(value)
        return SportsDashboard._lol_sidebar_config(league_key)["key"]

    def _draw_lpl_main_card(self, draw, right_x, right_w, y, event, now, is_live, league_key="LPL"):
        draw.rounded_rectangle(
            (right_x + 12, y, right_x + right_w - 12, y + 130),
            radius=6,
            fill=COLORS["panel2"],
            outline=COLORS["border"],
            width=1,
        )
        if not event:
            draw.text((right_x + 24, y + 42), "No LPL data", font=self._font(20, True), fill=COLORS["text"])
            return

        day_text = self._day_text(event["start"], now)
        day_text, day_font = self._fit_text(draw, day_text, right_w - 126, 17, bold=True, min_size=12)
        draw.text((right_x + 24, y + 14), day_text, font=day_font, fill=COLORS["amber"])
        self._draw_right_aligned(
            draw,
            (right_x + right_w - 25, y + 14),
            self._format_time(event["start"]),
            self._font(17, True),
            COLORS["text"],
        )

        if is_live and event.get("wins_a") is not None and event.get("wins_b") is not None:
            center = f"{event['wins_a']}-{event['wins_b']}"
        else:
            center = "vs"
        team_col_w = max(64, int((right_w - 78) / 2))
        team_a_label = self._lpl_display_team_from_event(event, "a", league_key=league_key)
        team_b_label = self._lpl_display_team_from_event(event, "b", league_key=league_key)
        team_a, font_a = self._fit_text(draw, team_a_label, team_col_w, 31, bold=True, min_size=18)
        team_b, font_b = self._fit_text(draw, team_b_label, team_col_w, 31, bold=True, min_size=18)
        center_x = right_x + right_w / 2
        draw.text((right_x + 25, y + 49), team_a, font=font_a, fill=COLORS["text"])
        self._draw_centered(draw, (center_x, y + 66), center, self._font(15, True), COLORS["muted"])
        self._draw_right_aligned(draw, (right_x + right_w - 25, y + 49), team_b, font_b, COLORS["text"])

        block = self._lpl_stage_label(event)[:18]
        draw.text((right_x + 25, y + 100), block, font=self._font(14), fill=COLORS["lpl_accent"])

    def _draw_lpl_upcoming(self, draw, right_x, right_w, y, events):
        self._draw_section_header(draw, right_x, right_w, y, "UPCOMING", COLORS["lpl_accent"])
        for index, event in enumerate(events):
            row_y = y + 34 + index * 42
            self._draw_schedule_row(draw, right_x, right_w, row_y, event)

    def _draw_lpl_recent(self, draw, right_x, right_w, y, events, league_key="LPL"):
        self._draw_section_header(draw, right_x, right_w, y, "RECENT", self._lol_sidebar_color(league_key, "accent"))
        for index, event in enumerate(events):
            row_y = y + 32 + index * 32
            draw.line((right_x + 14, row_y - 7, right_x + right_w - 14, row_y - 7), fill=COLORS["line"], width=1)
            draw.text((right_x + 16, row_y), event["start"].strftime("%m/%d"), font=self._font(14), fill=COLORS["muted"])
            label, label_font = self._fit_text(draw, self._result_label(event), right_w - 104, 17, bold=True, min_size=12)
            draw.text((right_x + 82, row_y - 1), label, font=label_font, fill=COLORS["text"])

    @staticmethod
    def _format_time(match_time):
        return match_time.strftime("%I:%M %p").lstrip("0")

    @staticmethod
    def _format_time_24h(match_time):
        return match_time.strftime("%H:%M")
