from .common import *
from .common import _ACTIVE_COLORS, _safe_exception_text, _normalize_country_alias

SportsDashboard = None


class WorldCupRenderMixin:
    def _draw_worldcup_compact_panel(self, image, draw, bounds, selected, source_state, fetched_at, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        panel_w = x2 - x1 + 1
        panel_h = y2 - y1 + 1
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["paper"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["worldcup_accent"], COLORS["paper"], 20, 1)

        header_y = y1 + 8
        logo_size = 30
        self._draw_worldcup_logo(image, draw, x1 + 14, header_y - 1, logo_size)
        title_year = self._worldcup_title_year(selected)
        source_y = header_y + 24
        title_drawn = False
        if str(title_year) == "2026":
            title_drawn = self._draw_worldcup_title_wordmark(
                image,
                x1 + 52,
                header_y - 2,
                178,
                27,
            )
        if not title_drawn:
            title, title_font = self._fit_text(draw, f"{title_year} World Cup", 178, 20, bold=True, min_size=15)
            draw.text((x1 + 52, header_y + 1), title, font=title_font, fill=COLORS["text"])
        source = self._worldcup_api_source_label(source_state, fetched_at)
        source_text, source_font = self._fit_text(draw, source, 140, 9, bold=True, min_size=7)
        draw.text((x1 + 52, source_y), source_text, font=source_font, fill=COLORS["muted"])
        self._draw_worldcup_header_banner(image, x1 + 225, y1, x2 - 90, y1 + 47)

        live = selected.get("live") or []
        upcoming = selected.get("upcoming") or []
        recent = selected.get("recent") or []
        main_event = selected.get("main")
        main_mode = self._worldcup_main_mode(selected, main_event)
        is_live = main_mode == "live"
        pill_label = "LIVE" if is_live else ("RECENT" if main_mode == "recent" else "NEXT")
        self._draw_status_pill(draw, x2 - 84, header_y + 4, pill_label, is_live)
        draw.line((x1 + 12, y1 + 48, x2 - 12, y1 + 48), fill=COLORS["border"], width=1)

        content_y = y1 + 57
        content_bottom = y2 - 8
        split_x = x1 + max(250, min(296, int(panel_w * 0.50)))
        left_x1 = x1 + 12
        left_x2 = split_x - 10
        right_x1 = split_x + 4
        right_x2 = x2 - 12
        if panel_h < 190:
            content_y = y1 + 53
            content_bottom = y2 - 6
        draw.line((split_x - 3, content_y - 5, split_x - 3, content_bottom), fill=COLORS["border"], width=1)
        draw.line((split_x - 1, content_y - 5, split_x - 1, content_bottom), fill=COLORS["line"], width=1)

        self._draw_worldcup_main_card(image, draw, left_x1, content_y, left_x2, content_bottom, main_event, now, main_mode)

        visible_matches = max(1, int(selected.get("visible_matches") or DEFAULT_WORLD_CUP_VISIBLE_MATCHES))
        upcoming_rows = [event for event in upcoming if event is not main_event]
        recent_rows = [event for event in recent if event is not main_event]
        if recent_rows:
            upcoming_rows = upcoming_rows[:2]
            recent_rows = recent_rows[:1]
        else:
            upcoming_rows = upcoming_rows[: max(0, visible_matches - 1)]

        upcoming_y = content_y
        upcoming_used_bottom = self._draw_worldcup_mini_rows(
            image,
            draw,
            right_x1,
            right_x2,
            upcoming_y,
            content_bottom,
            "UPCOMING",
            upcoming_rows,
            show_time=True,
        )
        if recent_rows:
            recent_y = max(upcoming_used_bottom + 1, content_bottom - 53)
            self._draw_worldcup_pitch_strip_in_gap(
                image,
                draw,
                right_x1,
                right_x2,
                upcoming_used_bottom + 1,
                recent_y - 1,
            )
            self._draw_worldcup_recent_rows(
                image,
                draw,
                right_x1,
                right_x2,
                recent_y,
                content_bottom,
                recent_rows,
            )
        else:
            self._draw_worldcup_tactics_strip(image, draw, right_x1, right_x2, upcoming_used_bottom + 2, content_bottom, main_event)

    def _attach_worldcup_standings_points(self, events, settings):
        """Overlay authoritative cumulative group PTS from ESPN's standings feed.

        Writes the ``team_{side}_standing_points`` slot, which the points label
        prefers over the locally-tallied points. Fails soft: on any error the
        local 3/1/0 tally in ``_annotate_worldcup_group_points`` still applies.
        """
        if not events:
            return events
        try:
            payload = self._load_worldcup_standings(settings)
            lookup = self._parse_worldcup_standings(payload)
            record_lookup = self._parse_worldcup_standings_records(payload)
            if lookup or record_lookup:
                self._apply_worldcup_standings(events, lookup, record_lookup)
        except Exception as exc:
            logger.warning("World Cup standings overlay failed: %s", _safe_exception_text(exc))
        return events

    def _load_worldcup_standings(self, settings):
        now_utc = datetime.now(timezone.utc)
        cache_path = self._sports_dashboard_cache_dir() / "worldcup_standings.json"
        cache = self._read_json_file(cache_path)
        url = str(settings.get("worldCupStandingsUrl") or DEFAULT_WORLD_CUP_STANDINGS_URL).strip() or DEFAULT_WORLD_CUP_STANDINGS_URL
        cache_key = "|".join([WORLD_CUP_STANDINGS_STATE_VERSION, url])
        has_cache = cache.get("cache_key") == cache_key and isinstance(cache.get("standings"), Mapping)
        cache_hours = self._int_setting(settings, "worldCupStandingsCacheHours", DEFAULT_WORLD_CUP_STANDINGS_CACHE_HOURS, 1, 24)
        if has_cache and not self._force_refresh_requested(settings) and self._worldcup_cache_is_fresh(cache, cache_hours, now_utc):
            return cache["standings"]
        try:
            response = get_http_session().get(
                url,
                params={"season": DEFAULT_WORLD_CUP_SEASON},
                headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"},
                timeout=20,
            )
            response.raise_for_status()
            payload = {
                "version": WORLD_CUP_STANDINGS_STATE_VERSION,
                "cache_key": cache_key,
                "fetched_at": now_utc.isoformat(),
                "standings": response.json(),
            }
        except Exception as exc:
            logger.warning("ESPN World Cup standings fetch failed: %s", _safe_exception_text(exc))
            return cache["standings"] if has_cache else {}
        try:
            self._write_json_file(cache_path, payload)
        except OSError as exc:
            logger.warning("Failed to write ESPN World Cup standings cache: %s", exc)
        return payload["standings"]

    @staticmethod
    def _parse_worldcup_standings(payload):
        """Build {(group_key, team_alias_upper): points} from the ESPN standings feed."""
        lookup = {}
        if not isinstance(payload, Mapping):
            return lookup
        for group in payload.get("children") or []:
            group_key = SportsDashboard._worldcup_explicit_group_key({"block": str((group or {}).get("name") or "")})
            if not group_key:
                continue
            entries = ((group or {}).get("standings") or {}).get("entries") or []
            for entry in entries:
                team = (entry or {}).get("team") or {}
                points = None
                for stat in (entry or {}).get("stats") or []:
                    if str((stat or {}).get("name") or "") == "points":
                        points = stat.get("value")
                        if points is None:
                            points = stat.get("displayValue")
                        break
                if points is None:
                    continue
                try:
                    points_int = int(round(float(points)))
                except (TypeError, ValueError):
                    continue
                for alias in (team.get("abbreviation"), team.get("displayName"), team.get("shortDisplayName"), team.get("name")):
                    alias = str(alias or "").strip().upper()
                    if alias:
                        lookup[(group_key, alias)] = points_int
        return lookup

    @staticmethod
    def _worldcup_standings_stat_int(stats, names):
        wanted = {str(name).strip().lower() for name in names}
        for stat in stats or []:
            stat = stat or {}
            candidates = {
                str(stat.get("name") or "").strip().lower(),
                str(stat.get("abbreviation") or "").strip().lower(),
                str(stat.get("displayName") or "").strip().lower(),
                str(stat.get("shortDisplayName") or "").strip().lower(),
            }
            if not candidates.intersection(wanted):
                continue
            for key in ("value", "displayValue"):
                value = stat.get(key)
                if value is None:
                    continue
                try:
                    return int(round(float(str(value).strip())))
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _parse_worldcup_standings_records(payload):
        """Build {(group_key, team_alias_upper): "W-D-L"} from ESPN standings."""
        lookup = {}
        if not isinstance(payload, Mapping):
            return lookup
        for group in payload.get("children") or []:
            group_key = SportsDashboard._worldcup_explicit_group_key({"block": str((group or {}).get("name") or "")})
            if not group_key:
                continue
            entries = ((group or {}).get("standings") or {}).get("entries") or []
            for entry in entries:
                team = (entry or {}).get("team") or {}
                stats = (entry or {}).get("stats") or []
                wins = SportsDashboard._worldcup_standings_stat_int(stats, ("wins", "win", "w"))
                draws = SportsDashboard._worldcup_standings_stat_int(stats, ("draws", "draw", "ties", "tie", "d", "t"))
                losses = SportsDashboard._worldcup_standings_stat_int(stats, ("losses", "loss", "l"))
                if wins is None or draws is None or losses is None:
                    continue
                record = f"{wins}-{draws}-{losses}"
                for alias in (team.get("abbreviation"), team.get("displayName"), team.get("shortDisplayName"), team.get("name")):
                    alias = str(alias or "").strip().upper()
                    if alias:
                        lookup[(group_key, alias)] = record
        return lookup

    @staticmethod
    def _apply_worldcup_standings(events, lookup, record_lookup=None):
        """Write authoritative PTS and W-D-L onto team standing slots."""
        lookup = lookup or {}
        record_lookup = record_lookup or {}
        if not lookup and not record_lookup:
            return
        for event in events or []:
            if not isinstance(event, MutableMapping):
                continue
            group_key = SportsDashboard._worldcup_explicit_group_key(event)
            if not group_key:
                continue
            for side in ("a", "b"):
                team_key = SportsDashboard._worldcup_group_team_key(event, side)
                if not team_key:
                    continue
                points = lookup.get((group_key, team_key))
                if points is not None:
                    event[f"team_{side}_standing_points"] = points
                record = record_lookup.get((group_key, team_key))
                if record:
                    event[f"team_{side}_standing_record"] = record

    @staticmethod
    def _annotate_worldcup_group_points(events):
        group_points = {}
        group_records = {}
        for event in events or []:
            group_key = SportsDashboard._worldcup_explicit_group_key(event)
            if not group_key:
                continue
            for side in ("a", "b"):
                team_key = SportsDashboard._worldcup_group_team_key(event, side)
                if team_key:
                    group_points.setdefault((group_key, team_key), 0)
                    group_records.setdefault((group_key, team_key), [0, 0, 0])

        for event in events or []:
            group_key = SportsDashboard._worldcup_explicit_group_key(event)
            if not group_key:
                continue
            if not SportsDashboard._is_worldcup_finished_event(event):
                continue
            wins_a = event.get("wins_a")
            wins_b = event.get("wins_b")
            if wins_a is None or wins_b is None:
                continue
            try:
                wins_a = int(wins_a)
                wins_b = int(wins_b)
            except (TypeError, ValueError):
                continue
            team_a_key = SportsDashboard._worldcup_group_team_key(event, "a")
            team_b_key = SportsDashboard._worldcup_group_team_key(event, "b")
            if not team_a_key or not team_b_key:
                continue
            record_a = group_records.setdefault((group_key, team_a_key), [0, 0, 0])
            record_b = group_records.setdefault((group_key, team_b_key), [0, 0, 0])
            if wins_a > wins_b:
                group_points[(group_key, team_a_key)] = group_points.get((group_key, team_a_key), 0) + 3
                record_a[0] += 1
                record_b[2] += 1
            elif wins_b > wins_a:
                group_points[(group_key, team_b_key)] = group_points.get((group_key, team_b_key), 0) + 3
                record_b[0] += 1
                record_a[2] += 1
            else:
                group_points[(group_key, team_a_key)] = group_points.get((group_key, team_a_key), 0) + 1
                group_points[(group_key, team_b_key)] = group_points.get((group_key, team_b_key), 0) + 1
                record_a[1] += 1
                record_b[1] += 1

        for event in events or []:
            group_key = SportsDashboard._worldcup_explicit_group_key(event)
            if not group_key:
                continue
            for side in ("a", "b"):
                team_key = SportsDashboard._worldcup_group_team_key(event, side)
                if not team_key:
                    continue
                if SportsDashboard._worldcup_group_points_value(event, side) is None:
                    points = group_points.get((group_key, team_key))
                    if points is not None:
                        event[f"team_{side}_group_points"] = points
                if not SportsDashboard._worldcup_group_record_value(event, side):
                    record = group_records.get((group_key, team_key))
                    if record is not None:
                        event[f"team_{side}_group_record"] = f"{record[0]}-{record[1]}-{record[2]}"

    @staticmethod
    def _worldcup_explicit_group_key(event):
        stage = SportsDashboard._clean_worldcup_stage((event or {}).get("block"))
        match = re.search(r"\bGroup\s+([A-L])\b", stage, re.IGNORECASE)
        if not match:
            return ""
        return f"Group {match.group(1).upper()}"

    @staticmethod
    def _worldcup_group_team_key(event, side):
        event = event or {}
        side = "a" if side == "a" else "b"
        for key in (f"team_{side}_tla", f"team_{side}_source_name", f"team_{side}"):
            value = str(event.get(key) or "").strip().upper()
            if value and value != "TBD":
                return value
        return ""

    @staticmethod
    def _worldcup_main_mode(selected, main_event):
        selected = selected or {}
        live = selected.get("live") or []
        upcoming = selected.get("upcoming") or []
        recent = selected.get("recent") or []
        if main_event and any(event is main_event for event in live):
            return "live"
        if main_event and any(event is main_event for event in recent) and not upcoming:
            return "recent"
        return "next"

    @staticmethod
    def _worldcup_title_year(selected, fallback=DEFAULT_WORLD_CUP_SEASON):
        if isinstance(selected, Mapping):
            configured = str(selected.get("season") or "").strip()
            if configured.isdigit() and len(configured) == 4:
                return configured
            candidates = [selected.get("main")]
            for key in ("live", "upcoming", "recent"):
                candidates.extend(selected.get(key) or [])
        else:
            candidates = list(selected or [])
        for event in candidates:
            start = (event or {}).get("start") if isinstance(event, Mapping) else None
            if isinstance(start, datetime):
                return str(start.year)
        return fallback

    def _draw_worldcup_main_card(self, image, draw, x1, y1, x2, y2, event, now, main_mode):
        is_live = main_mode == "live"
        is_recent = main_mode == "recent"
        if is_live:
            accent = COLORS["worldcup_live"]
        elif is_recent and event:
            accent = self._worldcup_status_color(event)
        else:
            accent = COLORS["worldcup_accent"]
        draw.rounded_rectangle((x1 + 3, y1 + 3, x2 + 3, y2 + 3), radius=5, fill=COLORS["worldcup_shadow"])
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["border"], width=2)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, y2 - 1), fill=accent)
        if not event:
            message, message_font = self._fit_text(draw, "No World Cup schedule", x2 - x1 - 36, 15, bold=True, min_size=10)
            self._draw_centered(draw, ((x1 + x2) / 2, (y1 + y2) / 2), message, message_font, COLORS["text"])
            return

        tag = "NOW PLAYING" if is_live else ("RECENT RESULT" if is_recent else "NEXT MATCH")
        tag_w = 112 if is_recent else (104 if is_live else 88)
        tag_text, tag_font = self._fit_text(draw, tag, tag_w - 8, 11, bold=True, min_size=7)
        tag_fill = COLORS["worldcup_live"] if is_live else (COLORS["green"] if is_recent else COLORS["worldcup_tag"])
        draw.rectangle((x1 + 14, y1 + 9, x1 + 14 + tag_w, y1 + 27), fill=tag_fill, outline=COLORS["border"], width=1)
        draw.text((x1 + 18, y1 + 10), tag_text, font=tag_font, fill=COLORS["text"])
        date_text = event["start"].strftime("%m/%d")
        date_text, date_font = self._fit_text(draw, date_text, 52, 11, bold=True, min_size=8)
        self._draw_right_aligned(draw, (x2 - 12, y1 + 11), date_text, date_font, COLORS["muted"])

        stage = self._clean_worldcup_stage(event.get("block"))
        stage_text, stage_font = self._fit_text(draw, stage, x2 - x1 - 34, 8, bold=True, min_size=6)
        draw.text((x1 + 18, y1 + 30), stage_text, font=stage_font, fill=COLORS["worldcup_accent"])

        status_text = self._worldcup_main_status_label(event, now)
        status_text, status_font = self._fit_text(draw, status_text, x2 - x1 - 42, 16, bold=True, min_size=10)
        self._draw_centered(draw, ((x1 + x2) / 2, y1 + 41), status_text, status_font, COLORS["text"])

        center_x = (x1 + x2) / 2
        flag_h, flag_cap = 27, 54
        left_area = (x1 + 18, center_x - 21)
        right_area = (center_x + 21, x2 - 18)
        flag_y = y1 + 58
        left_flag_x = int((left_area[0] + left_area[1] - flag_cap) / 2)
        right_flag_x = int((right_area[0] + right_area[1] - flag_cap) / 2)
        self._draw_worldcup_flag(image, draw, event.get("team_a_flag"), left_flag_x, flag_y, flag_cap, flag_h, event.get("team_a_tla"), align="center")
        self._draw_worldcup_flag(image, draw, event.get("team_b_flag"), right_flag_x, flag_y, flag_cap, flag_h, event.get("team_b_tla"), align="center")
        center_label = self._worldcup_score_or_vs(event)
        center_label, center_font = self._fit_text(draw, center_label, 64, 17, bold=True, min_size=11)
        self._draw_centered(draw, (center_x, flag_y + flag_h / 2 + 1), center_label, center_font, COLORS["text"])
        if is_recent:
            detail_y1 = flag_y + flag_h + 1
            detail_y2 = detail_y1 + 12
            self._draw_worldcup_score_detail_chip(
                draw,
                (center_x - 88, detail_y1, center_x - 34, detail_y2),
                self._worldcup_side_period_score_label(event, "a"),
                align="right",
            )
            self._draw_worldcup_score_detail_chip(
                draw,
                (center_x + 34, detail_y1, center_x + 88, detail_y2),
                self._worldcup_side_period_score_label(event, "b"),
            )

        team_y = flag_y + flag_h + 16
        team_a, team_a_font = self._fit_text(draw, event.get("team_a"), left_area[1] - left_area[0], 16, bold=True, min_size=9)
        team_b, team_b_font = self._fit_text(draw, event.get("team_b"), right_area[1] - right_area[0], 16, bold=True, min_size=9)
        self._draw_centered(draw, ((left_area[0] + left_area[1]) / 2, team_y), team_a, team_a_font, COLORS["text"])
        self._draw_centered(draw, ((right_area[0] + right_area[1]) / 2, team_y), team_b, team_b_font, COLORS["text"])

        points_y = team_y + 13
        left_meta = self._worldcup_team_points_meta(event, "a")
        right_meta = self._worldcup_team_points_meta(event, "b")
        left_meta_left, left_meta_right = int(left_area[0]), int(left_area[1])
        right_meta_left, right_meta_right = int(right_area[0]), int(right_area[1])
        meta_width = max(1, min(left_meta_right - left_meta_left, right_meta_right - right_meta_left))
        self._draw_worldcup_odds_text(draw, (left_meta_left, points_y, left_meta_left + meta_width, points_y + 11), left_meta, max_size=8)
        self._draw_worldcup_odds_text(draw, (right_meta_right - meta_width, points_y, right_meta_right, points_y + 11), right_meta, max_size=8)

        if self._worldcup_event_has_odds(event):
            odds_y = points_y + 11
            odds = event.get("odds") or {}
            self._draw_worldcup_odds_text(draw, (left_area[0], odds_y, left_area[1], odds_y + 12), (event.get("odds") or {}).get("team_a"), max_size=9)
            if odds.get("draw"):
                self._draw_worldcup_odds_text(draw, (center_x - 26, odds_y, center_x + 26, odds_y + 12), f"X {odds.get('draw')}", max_size=9)
            self._draw_worldcup_odds_text(draw, (right_area[0], odds_y, right_area[1], odds_y + 12), (event.get("odds") or {}).get("team_b"), max_size=9)

    def _draw_worldcup_mini_rows(self, image, draw, x1, x2, y, bottom, title, events, show_time):
        self._draw_worldcup_mini_section_header(image, draw, x1, x2, y, title)
        if not events:
            message = "No more World Cup schedule" if title == "UPCOMING" else "No recent results"
            message, message_font = self._fit_text(draw, message, x2 - x1 - 16, 10, bold=True, min_size=7)
            draw.text((x1 + 10, y + 23), message, font=message_font, fill=COLORS["muted"])
            return y + 38
        row_y = y + 21
        row_h = 33
        max_rows = max(1, (bottom - row_y + 1) // (row_h + 2))
        rows = events[:max_rows]
        for index, event in enumerate(rows):
            center_text = "VS" if show_time else self._worldcup_score_or_vs(event)
            self._draw_worldcup_mini_match_row(
                image,
                draw,
                x1,
                x2,
                row_y + index * (row_h + 2),
                row_h,
                event,
                center_text,
                show_time=show_time,
            )
        return row_y + len(rows) * (row_h + 2) - 2

    def _draw_worldcup_mini_section_header(self, image, draw, x1, x2, y, title):
        draw.rectangle((x1, y + 2, x1 + 8, y + 17), fill=COLORS["worldcup_accent"], outline=COLORS["border"], width=1)
        draw.text((x1 + 13, y - 2), title, font=self._font(13, True), fill=COLORS["text"])
        draw.line((x1, y + 19, x2, y + 19), fill=COLORS["border"], width=1)

    def _draw_worldcup_recent_rows(self, image, draw, x1, x2, y, bottom, events):
        if bottom - y < 45:
            return y
        self._draw_worldcup_mini_section_header(image, draw, x1, x2, y, "RECENT")
        row_y = y + 20
        available = bottom - row_y + 1
        row_h = min(32, available)
        if row_h < 30:
            return row_y
        row_gap = 3
        max_rows = 1 + max(0, (available - row_h) // (row_h + row_gap))
        visible_events = list(events or [])[: min(2, max_rows)]
        if not visible_events:
            message, message_font = self._fit_text(draw, "No recent results", x2 - x1 - 16, 10, bold=True, min_size=7)
            draw.text((x1 + 10, y + 23), message, font=message_font, fill=COLORS["muted"])
            return row_y
        for index, event in enumerate(visible_events):
            self._draw_worldcup_recent_match_row(
                image,
                draw,
                x1,
                x2,
                row_y + index * (row_h + row_gap),
                row_h,
                event,
            )
        return row_y + len(visible_events) * (row_h + row_gap) - row_gap

    def _draw_worldcup_recent_match_row(self, image, draw, x1, x2, y, row_h, event):
        draw.rounded_rectangle((x1, y, x2, y + row_h), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + row_h - 1), fill=self._worldcup_status_color(event))

        center_x = (x1 + x2) / 2
        left_label = self._worldcup_side_period_score_label(event, "a")
        right_label = self._worldcup_side_period_score_label(event, "b")
        score_w = 44 if left_label or right_label else 48
        detail_w = 43
        detail_gap = 4
        left_detail_w = detail_w if left_label else 0
        right_detail_w = detail_w if right_label else 0
        team_y1 = y + 8
        team_y2 = min(y + row_h - 10, y + 20)
        left_score_edge = center_x - score_w / 2
        right_score_edge = center_x + score_w / 2
        left_area = (x1 + 8, left_score_edge - left_detail_w - detail_gap - 4)
        right_area = (right_score_edge + right_detail_w + detail_gap + 4, x2 - 8)
        self._draw_worldcup_recent_team_identity(image, draw, event, "a", left_area, team_y1, team_y2)
        if left_label:
            self._draw_worldcup_score_detail_chip(
                draw,
                (left_score_edge - left_detail_w - detail_gap, team_y1 + 1, left_score_edge - detail_gap, team_y2 - 1),
                left_label,
                align="right",
            )
        score = self._worldcup_score_or_vs(event)
        score, score_font = self._fit_text(draw, score, score_w, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (left_score_edge, team_y1, right_score_edge, team_y2), score, score_font, COLORS["text"])
        if right_label:
            self._draw_worldcup_score_detail_chip(
                draw,
                (right_score_edge + detail_gap, team_y1 + 1, right_score_edge + detail_gap + right_detail_w, team_y2 - 1),
                right_label,
            )
        self._draw_worldcup_recent_team_identity(image, draw, event, "b", right_area, team_y1, team_y2)
        points_y = y + row_h - 10
        date_text, date_font = self._fit_text(draw, event["start"].strftime("%m/%d"), score_w - 4, 7, bold=True, min_size=6)
        self._draw_centered_in_box(draw, (center_x - score_w / 2, points_y, center_x + score_w / 2, y + row_h - 1), date_text, date_font, COLORS["muted"])
        left_meta = self._worldcup_team_points_meta(event, "a")
        right_meta = self._worldcup_team_points_meta(event, "b")
        self._draw_worldcup_odds_text(draw, (left_area[0], points_y, left_area[1], y + row_h - 1), left_meta, max_size=7)
        self._draw_worldcup_odds_text(draw, (right_area[0], points_y, right_area[1], y + row_h - 1), right_meta, max_size=7)

    def _draw_worldcup_score_detail_chip(self, draw, box, text, align="left"):
        text = str(text or "").strip()
        if not text:
            return
        left, top, right, bottom = [int(round(value)) for value in box]
        if right - left < 20 or bottom - top < 7:
            return
        text, font = self._fit_text(draw, text, max(1, right - left), 9, bold=True, min_size=6)
        self._draw_text_in_box(draw, (left, top - 1, right, bottom + 1), text, font, COLORS["text"], align=align)

    def _draw_worldcup_recent_team_identity(self, image, draw, event, side, area, y1, y2):
        left, right = [int(value) for value in area]
        area_w = max(1, right - left)
        flag_h, flag_cap = 13, 26
        gap = 5
        side_key = "a" if side == "a" else "b"
        label = event.get(f"team_{side_key}")
        fallback = event.get(f"team_{side_key}_tla")
        flag_url = event.get(f"team_{side_key}_flag")
        flag_w = self._worldcup_flag_display_size(flag_url, fallback, flag_cap, flag_h)[0]
        max_text_w = max(24, area_w - flag_w - gap)
        label, font = self._fit_text(draw, label, max_text_w, 11, bold=True, min_size=7)
        text_w = min(max_text_w, self._text_width(draw, label, font))
        flag_y = int(y1 + max(0, ((y2 - y1) - flag_h) / 2))
        if side_key == "b":
            flag_x = int(right - flag_w)
            text_right = int(flag_x - gap)
            text_left = max(left, int(text_right - text_w))
            strike_left = text_left
            strike_right = flag_x + flag_w
            self._draw_text_in_box(
                draw,
                (text_left, y1, text_right, y2),
                label,
                font,
                COLORS["text"],
                align="right",
            )
        else:
            flag_x = int(left)
            text_left = int(flag_x + flag_w + gap)
            text_right = min(right, int(text_left + text_w))
            strike_left = flag_x
            strike_right = text_right
            self._draw_text_in_box(
                draw,
                (text_left, y1, text_right, y2),
                label,
                font,
                COLORS["text"],
            )
        self._draw_worldcup_flag(image, draw, flag_url, flag_x, flag_y, flag_w, flag_h, fallback)
        if self._worldcup_team_eliminated(event, side_key):
            self._draw_worldcup_elimination_strike(draw, strike_left, strike_right, y1, y2)

    def _draw_worldcup_elimination_strike(self, draw, x1, x2, y1, y2):
        overflow = 3
        x1 = int(x1) - overflow
        x2 = int(x2) + overflow
        if x2 <= x1:
            return
        strike_y = int(round((y1 + y2) / 2)) + 1
        draw.line((x1, strike_y, x2, strike_y), fill=COLORS["red"], width=2)

    def _draw_worldcup_mini_match_row(self, image, draw, x1, x2, y, row_h, event, center_text, show_time=False):
        draw.rounded_rectangle((x1, y, x2, y + row_h), radius=4, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y + 1, x1 + 5, y + row_h - 1), fill=self._worldcup_status_color(event))
        date_text, date_font = self._fit_text(draw, event["start"].strftime("%m/%d"), 36, 9, bold=True, min_size=7)
        draw.text((x1 + 9, y + 1), date_text, font=date_font, fill=COLORS["muted"])
        stage = self._clean_worldcup_stage(event.get("block"))
        stage_text, stage_font = self._fit_text(draw, stage, 78, 9, bold=True, min_size=7)
        draw.text((x1 + 47, y + 1), stage_text, font=stage_font, fill=COLORS["muted"])
        if show_time:
            time_text, time_font = self._fit_text(draw, self._worldcup_event_time_label(event), 48, 9, bold=True, min_size=7)
            self._draw_right_aligned(draw, (x2 - 8, y + 1), time_text, time_font, COLORS["text"])
        self._draw_worldcup_row_lineup(image, draw, x1 + 9, x2 - 8, y + 13, event, center_text)

    def _draw_worldcup_row_lineup(self, image, draw, x1, x2, y, event, center_text):
        center_x = (x1 + x2) / 2
        flag_h, flag_cap = 14, 28
        left_flag_x = x1 + 1
        has_odds = center_text == "VS" and self._worldcup_event_has_odds(event)
        team_bottom = y + 11
        flag_y = y - 1
        left_w = self._draw_worldcup_flag(image, draw, event.get("team_a_flag"), left_flag_x, flag_y, flag_cap, flag_h, event.get("team_a_tla"), align="left")
        left_text_x = left_flag_x + left_w + 4
        team_a, font_a = self._fit_text(draw, event.get("team_a"), max(20, center_x - left_text_x - 4), 10, bold=True, min_size=7)
        self._draw_text_in_box(draw, (left_text_x, y - 1, center_x - 28, team_bottom), team_a, font_a, COLORS["text"])
        center_text, center_font = self._fit_text(draw, center_text, 52, 10, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (center_x - 26, y - 1, center_x + 26, team_bottom), center_text, center_font, COLORS["text"])
        right_w = self._draw_worldcup_flag(image, draw, event.get("team_b_flag"), x2 - flag_cap - 1, flag_y, flag_cap, flag_h, event.get("team_b_tla"), align="right")
        right_text_x = x2 - 1 - right_w - 4
        team_b, font_b = self._fit_text(draw, event.get("team_b"), max(20, right_text_x - center_x - 28), 10, bold=True, min_size=7)
        self._draw_text_in_box(draw, (center_x + 28, y - 1, right_text_x, team_bottom), team_b, font_b, COLORS["text"], align="right")
        left_meta = self._worldcup_team_points_meta(event, "a", include_odds=has_odds)
        right_meta = self._worldcup_team_points_meta(event, "b", include_odds=has_odds)
        meta_margin = flag_cap + 5
        self._draw_worldcup_odds_text(draw, (x1 + meta_margin, y + 11, center_x - 28, y + 20), left_meta, max_size=7)
        self._draw_worldcup_odds_text(draw, (center_x + 28, y + 11, x2 - meta_margin, y + 20), right_meta, max_size=7)
        if has_odds:
            odds = event.get("odds") or {}
            if odds.get("draw"):
                self._draw_worldcup_odds_text(draw, (center_x - 21, y + 11, center_x + 21, y + 20), f"X / {odds.get('draw')}", max_size=7)

    def _draw_worldcup_tactics_strip(self, image, draw, x1, x2, y1, y2, event):
        x1 = int(x1)
        x2 = int(x2)
        y1 = int(y1)
        y2 = int(y2)
        if y2 - y1 < 12 or x2 - x1 < 80:
            return
        formation_pair = self._worldcup_formation_pair(event)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 5, y2 - 1), fill=COLORS["worldcup_accent"])
        if formation_pair:
            left_text, right_text = formation_pair
            center_x = (x1 + x2) / 2
            text_y = y1 + max(1, (y2 - y1 - 10) // 2)
            label, label_font = self._fit_text(draw, "\u9996\u53d1", 28, 9, bold=True, min_size=7)
            draw.text((x1 + 10, text_y), label, font=label_font, fill=COLORS["amber"])
            vs_text, vs_font = self._fit_text(draw, "VS", 18, 9, bold=True, min_size=7)
            self._draw_centered(draw, (center_x, text_y + 5), vs_text, vs_font, COLORS["text"])
            left_width = max(24, int(center_x - (x1 + 43) - 10))
            right_width = max(24, int((x2 - 8) - (center_x + 14)))
            left_text, left_font = self._fit_text(draw, left_text, left_width, 9, bold=True, min_size=6)
            draw.text((x1 + 43, text_y), left_text, font=left_font, fill=COLORS["text"])
            right_text, right_font = self._fit_text(draw, right_text, right_width, 9, bold=True, min_size=6)
            self._draw_right_aligned(draw, (x2 - 8, text_y), right_text, right_font, COLORS["text"])
            return
        self._draw_worldcup_pitch_strip(image, draw, x1 + 6, y1 + 2, x2 - 1, y2)

    @staticmethod
    def _worldcup_formation_summary(event):
        formation_a = str((event or {}).get("formation_a") or "").strip()
        formation_b = str((event or {}).get("formation_b") or "").strip()
        if not formation_a or not formation_b:
            return ""
        return f"{formation_a} VS {formation_b}"

    @staticmethod
    def _worldcup_formation_pair(event):
        formation_a = str((event or {}).get("formation_a") or "").strip()
        formation_b = str((event or {}).get("formation_b") or "").strip()
        if not formation_a or not formation_b:
            return None
        team_a = SportsDashboard._worldcup_tactics_team_label(event, "a")
        team_b = SportsDashboard._worldcup_tactics_team_label(event, "b")
        return f"{team_a} {formation_a}", f"{formation_b} {team_b}"

    @staticmethod
    def _worldcup_tactics_team_label(event, side):
        team = str((event or {}).get(f"team_{side}") or "").strip()
        tla = str((event or {}).get(f"team_{side}_tla") or "").strip().upper()
        if team and len(team) <= 5:
            return team
        if tla:
            return tla
        return team[:5] if team else "TBD"

    def _draw_worldcup_pitch_strip_in_gap(self, image, draw, x1, x2, gap_top, gap_bottom):
        x1 = int(x1)
        x2 = int(x2)
        gap_top = int(gap_top)
        gap_bottom = int(gap_bottom)
        strip_width = 248
        strip_height = 13
        available_width = x2 - x1 + 1
        available_height = gap_bottom - gap_top + 1
        if available_width < strip_width or available_height < strip_height:
            return False
        strip_x1 = x1 + (available_width - strip_width) // 2
        strip_y1 = gap_top + (available_height - strip_height) // 2
        self._draw_worldcup_pitch_strip(
            image,
            draw,
            strip_x1,
            strip_y1,
            strip_x1 + strip_width - 1,
            strip_y1 + strip_height - 1,
        )
        return True

    def _draw_worldcup_pitch_strip(self, image, draw, x1, y1, x2, y2):
        x1 = int(x1)
        y1 = int(y1)
        x2 = int(x2)
        y2 = int(y2)
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        if width < 16 or height < 5:
            return
        pitch_strip = self._load_worldcup_pitch_strip((width, height))
        if pitch_strip:
            image.paste(pitch_strip, (x1, y1))
            return

        white = (255, 255, 255)
        black = (0, 0, 0)
        field = Image.new("RGB", (width, height), black)
        field_draw = ImageDraw.Draw(field)
        right = width - 1
        bottom = height - 1

        field_draw.rectangle((0, 0, right, bottom), outline=white, width=1)
        mid_x = width // 2
        field_draw.line((mid_x, 1, mid_x, bottom - 1), fill=white, width=1)
        field_draw.point((mid_x, height // 2), fill=white)
        if width >= 52 and height >= 9:
            circle_r = max(2, min(height // 3, width // 18))
            field_draw.ellipse(
                (mid_x - circle_r, height // 2 - circle_r, mid_x + circle_r, height // 2 + circle_r),
                outline=white,
                width=1,
            )

        box_w = max(5, min(width // 7, 24))
        box_h = max(3, height - 4)
        box_y1 = max(1, (height - box_h) // 2)
        box_y2 = min(bottom - 1, box_y1 + box_h)
        field_draw.rectangle((1, box_y1, box_w, box_y2), outline=white, width=1)
        field_draw.rectangle((right - box_w, box_y1, right - 1, box_y2), outline=white, width=1)
        if width >= 84 and height >= 10:
            six_w = max(3, box_w // 2)
            six_h = max(2, box_h // 2)
            six_y1 = max(1, (height - six_h) // 2)
            field_draw.rectangle((1, six_y1, six_w, six_y1 + six_h), outline=white, width=1)
            field_draw.rectangle((right - six_w, six_y1, right - 1, six_y1 + six_h), outline=white, width=1)

        player_size = 1 if height <= 12 else 2
        players = (
            (0.18, 0.38),
            (0.26, 0.68),
            (0.36, 0.28),
            (0.45, 0.58),
            (0.57, 0.35),
            (0.66, 0.72),
            (0.76, 0.45),
            (0.86, 0.64),
        )
        for px_frac, py_frac in players:
            px = max(2, min(right - 2, int(width * px_frac)))
            py = max(2, min(bottom - 2, int(height * py_frac)))
            if player_size == 1:
                field_draw.point((px, py), fill=white)
                field_draw.point((px, max(1, py - 1)), fill=white)
            else:
                field_draw.rectangle((px - 1, py - 1, px + 1, py + 1), fill=white)
                field_draw.point((px, max(1, py - 2)), fill=white)
        ball_x = max(3, min(right - 3, int(width * 0.31)))
        ball_y = max(2, min(bottom - 2, int(height * 0.48)))
        field_draw.point((ball_x, ball_y), fill=white)

        image.paste(field, (x1, y1))

    @staticmethod
    def _load_worldcup_pitch_strip(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_WORLDCUP_PITCH_STRIP_PATH
        cache_key = (path, (width, height), "worldcup-pitch-strip-v3")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                strip = source.convert("RGB")
            if strip.size != (width, height):
                strip = strip.resize((width, height), Image.NEAREST)
            TEAM_LOGO_CACHE[cache_key] = strip
            return strip
        except Exception as exc:
            logger.warning("Failed to load World Cup pitch strip %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    @staticmethod
    def _load_worldcup_header_banner(size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = LOCAL_WORLDCUP_HEADER_BANNER_PATH
        cache_key = (path, (width, height), "worldcup-header-banner-v1")
        if cache_key in TEAM_LOGO_CACHE:
            return TEAM_LOGO_CACHE[cache_key]
        if not os.path.exists(path):
            TEAM_LOGO_CACHE[cache_key] = None
            return None
        try:
            with Image.open(path) as source:
                banner = source.convert("RGBA")
            if banner.size != (width, height):
                banner = banner.resize((width, height), Image.LANCZOS)
            TEAM_LOGO_CACHE[cache_key] = banner
            return banner
        except Exception as exc:
            logger.warning("Failed to load World Cup header banner %s: %s", path, exc)
            TEAM_LOGO_CACHE[cache_key] = None
            return None

    def _draw_worldcup_header_banner(self, image, x1, y1, x2, y2):
        x1 = int(x1)
        y1 = int(y1)
        x2 = int(x2)
        y2 = int(y2)
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        if width < 80 or height < 18:
            return
        banner = self._load_worldcup_header_banner((width, height))
        if banner:
            image.paste(banner, (x1, y1), banner)

    @staticmethod
    def _worldcup_event_has_odds(event):
        odds = (event or {}).get("odds") or {}
        return bool(odds.get("team_a") and odds.get("team_b"))

    @staticmethod
    def _worldcup_score_or_vs(event):
        if (event or {}).get("wins_a") is None or (event or {}).get("wins_b") is None:
            return "VS"
        return f"{event['wins_a']}-{event['wins_b']}"

    @staticmethod
    def _worldcup_side_period_score_label(event, side):
        event = event or {}
        side = "a" if side == "a" else "b"
        extra_score = SportsDashboard._worldcup_score_value(event.get(f"extra_time_score_{side}"))
        penalty_score = SportsDashboard._worldcup_score_value(event.get(f"penalty_score_{side}"))
        extra_label = f"ET {extra_score}" if extra_score is not None else ""
        penalty_label = f"P{penalty_score}" if penalty_score is not None else ""
        parts = [extra_label, penalty_label] if side == "a" else [penalty_label, extra_label]
        return "/".join(part for part in parts if part)

    @staticmethod
    def _worldcup_group_points_value(event, side):
        if not isinstance(event, Mapping):
            return None
        side = "a" if side == "a" else "b"
        team_key = f"team_{side}"
        keys = (
            f"{team_key}_group_points",
            f"{team_key}_standing_points",
            f"group_points_{side}",
            f"standing_points_{side}",
        )
        for key in keys:
            value = event.get(key)
            if value is None:
                continue
            value = str(value).strip()
            if value:
                return value
        return None

    @staticmethod
    def _worldcup_group_points_label(event, side):
        value = SportsDashboard._worldcup_group_points_value(event, side)
        return f"PTS {value}" if value is not None else "PTS -"

    @staticmethod
    def _worldcup_group_record_value(event, side):
        if not isinstance(event, Mapping):
            return ""
        side = "a" if side == "a" else "b"
        team_key = f"team_{side}"
        keys = (
            f"{team_key}_group_record",
            f"{team_key}_standing_record",
            f"group_record_{side}",
            f"standing_record_{side}",
            f"record_{side}",
        )
        for key in keys:
            value = str(event.get(key) or "").strip()
            if not value:
                continue
            match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\s*", value)
            if match:
                return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        return ""

    @staticmethod
    def _worldcup_group_record_label(event, side):
        value = SportsDashboard._worldcup_group_record_value(event, side)
        if value:
            return value
        if SportsDashboard._worldcup_explicit_group_key(event) and SportsDashboard._worldcup_group_team_key(event, side):
            return "0-0-0"
        return ""

    @staticmethod
    def _worldcup_normalized_stage_text(event):
        if not isinstance(event, Mapping):
            return ""
        values = []
        for key in ("block", "stage", "stage_label", "round", "round_label", "phase", "group"):
            value = event.get(key)
            if value is not None:
                values.append(SportsDashboard._clean_worldcup_stage(value))
        text = " ".join(str(value or "") for value in values)
        return re.sub(r"[^A-Za-z0-9]+", " ", text).strip().lower()

    @staticmethod
    def _worldcup_is_group_stage_event(event):
        normalized = SportsDashboard._worldcup_normalized_stage_text(event)
        if not normalized:
            return False
        return bool(
            re.search(r"\bgroup\s+[a-l]\b", normalized)
            or re.search(r"\bgroup\s+stage\b", normalized)
        )

    @staticmethod
    def _worldcup_is_knockout_stage_event(event):
        normalized = SportsDashboard._worldcup_normalized_stage_text(event)
        if not normalized:
            return False
        if SportsDashboard._worldcup_is_group_stage_event(event):
            return False
        return bool(
            re.search(r"\bround\s+of\s+\d+\b", normalized)
            or re.search(r"\bround\s+\d+\b", normalized)
            or re.search(r"\blast\s+\d+\b", normalized)
            or re.search(r"\bknock\s*out\b", normalized)
            or re.search(r"\bknockout\b", normalized)
            or re.search(r"\bquarter\b", normalized)
            or re.search(r"\bquarter\s*finals?\b", normalized)
            or re.search(r"\bsemi\b", normalized)
            or re.search(r"\bsemi\s*finals?\b", normalized)
            or re.search(r"\b(?:third|3rd)\s+place\b", normalized)
            or re.search(r"\bfinals?\b", normalized)
        )

    @staticmethod
    def _worldcup_team_points_meta(event, side, include_odds=False):
        include_group_meta = SportsDashboard._worldcup_is_group_stage_event(event)
        if not include_group_meta and not SportsDashboard._worldcup_is_knockout_stage_event(event):
            include_group_meta = (
                SportsDashboard._worldcup_group_points_value(event, side) is not None
                or bool(SportsDashboard._worldcup_group_record_value(event, side))
            )
        points = SportsDashboard._worldcup_group_points_label(event, side) if include_group_meta else ""
        record = SportsDashboard._worldcup_group_record_label(event, side) if include_group_meta else ""
        odds_key = "team_a" if side == "a" else "team_b"
        event_data = event if isinstance(event, Mapping) else {}
        odds_value = (event_data.get("odds") or {}).get(odds_key) if include_odds else None
        if side == "a":
            parts = [points]
            if record:
                parts.append(record)
            if odds_value:
                parts.append(str(odds_value))
        else:
            parts = []
            if odds_value:
                parts.append(str(odds_value))
            if record:
                parts.append(record)
            parts.append(points)
        return " / ".join(part for part in parts if part)

    def _draw_worldcup_logo(self, image, draw, x, y, size):
        x = int(x)
        y = int(y)
        size = int(size)
        logo = self._load_local_logo(LOCAL_WORLDCUP_LOGO_PATH, (size, size), alpha_threshold=16)
        if logo:
            image.paste(logo, (x + (size - logo.width) // 2, y + (size - logo.height) // 2), logo)
            return
        draw.ellipse((x, y, x + size, y + size), fill=COLORS["panel"], outline=COLORS["border"], width=2)
        seam = max(3, size // 6)
        draw.arc((x + seam, y + 3, x + size - seam, y + size - 3), 90, 270, fill=COLORS["blue"], width=2)
        draw.arc((x + seam, y + 3, x + size - seam, y + size - 3), 270, 90, fill=COLORS["green"], width=2)
        draw.line((x + 4, y + size / 2, x + size - 4, y + size / 2), fill=COLORS["red"], width=2)
        text, font = self._fit_text(draw, "WC", max(10, size - 8), max(10, int(size * 0.42)), bold=True, min_size=8)
        self._draw_centered(draw, (x + size / 2, y + size / 2), text, font, COLORS["text"])

    def _draw_worldcup_title_wordmark(self, image, x, y, max_width, max_height):
        wordmark = self._load_local_logo(
            LOCAL_WORLDCUP_TITLE_WORDMARK_PATH,
            (int(max_width), int(max_height)),
            alpha_threshold=8,
        )
        if not wordmark:
            return False
        paste_x = int(x)
        paste_y = int(y + (int(max_height) - wordmark.height) / 2)
        image.paste(wordmark, (paste_x, paste_y), wordmark)
        return True

    def _draw_worldcup_header_brand(self, image, draw, width, compact=False):

        logo_size = 28 if compact else 36
        gap = 10 if compact else 14
        title = "2026 World Cup"
        title_text, title_font = self._fit_text(draw, title, max(150, width - 150), 18 if compact else 22, bold=True, min_size=14)
        text_box = draw.textbbox((0, 0), title_text, font=title_font)
        title_w = text_box[2] - text_box[0]
        title_h = text_box[3] - text_box[1]
        total_w = logo_size + gap + title_w
        group_x = int((width - total_w) / 2)
        center_y = 29 if compact else 40
        logo_y = int(center_y - logo_size / 2)
        title_x = group_x + logo_size + gap - text_box[0]
        title_y = center_y - title_h / 2 - text_box[1]
        self._draw_worldcup_logo(image, draw, group_x, logo_y, logo_size)
        draw.text((title_x, title_y), title_text, font=title_font, fill=COLORS["text"])

    @staticmethod
    def _worldcup_row_regions(width):
        row_left = 16
        row_right = width - 22
        group_width = 96 if width >= 500 else 84
        group_x1 = row_left + 10
        group_x2 = min(group_x1 + group_width, row_right - 210)
        time_width = 56 if width >= 500 else 50
        date_width = 52 if width >= 500 else 48
        right_gap = 4
        date_x2 = row_right - 8
        date_x1 = date_x2 - date_width
        time_x2 = date_x1 - right_gap
        time_x1 = time_x2 - time_width
        match_x1 = group_x2 + 14
        match_x2 = max(match_x1 + 1, time_x1 - 10)
        return {
            "group": (int(group_x1), int(group_x2)),
            "match": (int(match_x1), int(match_x2)),
            "date": (int(date_x1), int(date_x2)),
            "time": (int(time_x1), int(time_x2)),
        }

    @staticmethod
    def _worldcup_right_info_x_ranges(width):
        regions = SportsDashboard._worldcup_row_regions(width)
        return regions["date"], regions["time"]

    @staticmethod
    def _worldcup_matchup_row_offset(row_height):
        return max(8, int((row_height - 21) / 2))

    def _draw_worldcup_matchup(self, image, draw, event, x, y, max_width, row_height=None):
        max_width = max(1, int(max_width))
        row_height = int(row_height) if row_height is not None else None
        odds = event.get("odds") or {}
        has_team_odds = bool(odds.get("team_a") and odds.get("team_b"))
        compact_odds = has_team_odds and row_height is not None and row_height <= 38
        line_y = int(y)
        if row_height is not None:
            line_y += 3 if compact_odds else self._worldcup_matchup_row_offset(row_height)
        if has_team_odds:
            center_width = 34
            side_gap = 8
            center_x = x + max_width / 2
            side_width = max(44, int((max_width - center_width - side_gap * 2) / 2))
            left_country_x = int(center_x - center_width / 2 - side_gap - side_width)
            right_country_x = int(center_x + center_width / 2 + side_gap)
            if compact_odds:
                odds_bottom = int(y + row_height - 2)
                odds_top = max(line_y + 16, odds_bottom - 11)
                side_odds_size = 9
                draw_odds_size = 8
                vs_y = line_y + 7
            else:
                odds_top = line_y + 20
                odds_bottom = line_y + 35
                side_odds_size = 11
                draw_odds_size = 10
                vs_y = line_y + 8
            self._draw_worldcup_country(
                image,
                draw,
                event.get("team_a_flag"),
                event.get("team_a"),
                event.get("team_a_tla"),
                left_country_x,
                line_y,
                side_width,
                "left",
                compact=compact_odds,
            )
            self._draw_worldcup_odds_text(
                draw,
                (left_country_x, odds_top, left_country_x + side_width, odds_bottom),
                odds.get("team_a"),
                max_size=side_odds_size,
            )
            self._draw_centered(draw, (center_x, vs_y), "VS", self._font(10 if compact_odds else 11, True), COLORS["text"])
            if odds.get("draw"):
                self._draw_worldcup_odds_text(
                    draw,
                    (center_x - 24, odds_top, center_x + 24, odds_bottom),
                    f"X {odds.get('draw')}",
                    max_size=draw_odds_size,
                )
            self._draw_worldcup_country(
                image,
                draw,
                event.get("team_b_flag"),
                event.get("team_b"),
                event.get("team_b_tla"),
                right_country_x,
                line_y,
                side_width,
                "right",
                compact=compact_odds,
            )
            self._draw_worldcup_odds_text(
                draw,
                (right_country_x, odds_top, right_country_x + side_width, odds_bottom),
                odds.get("team_b"),
                max_size=side_odds_size,
            )
            return

        center_x = x + max_width / 2
        side_width = max(30, int((max_width - 42) / 2))
        self._draw_worldcup_country(
            image,
            draw,
            event.get("team_a_flag"),
            event.get("team_a"),
            event.get("team_a_tla"),
            x,
            line_y,
            side_width,
            "left",
        )
        self._draw_centered(draw, (center_x, line_y + 11), "VS", self._font(12, True), COLORS["text"])
        self._draw_worldcup_country(
            image,
            draw,
            event.get("team_b_flag"),
            event.get("team_b"),
            event.get("team_b_tla"),
            int(center_x + 21),
            line_y,
            side_width,
            "right",
        )

    def _draw_worldcup_odds_box(self, draw, box, text, max_size=10):
        text = str(text or "").strip()
        if not text:
            return
        left, top, right, bottom = [int(value) for value in box]
        draw.rectangle((left, top, right, bottom), fill=COLORS["panel_gold"], outline=COLORS["border"], width=1)
        fitted, font = self._fit_text(draw, text, max(1, right - left - 4), max_size, bold=True, min_size=7)
        self._draw_centered_in_box(draw, (left + 1, top, right - 1, bottom), fitted, font, COLORS["text"])

    def _draw_worldcup_odds_text(self, draw, box, text, max_size=11):
        text = str(text or "").strip()
        if not text:
            return
        left, top, right, bottom = [int(value) for value in box]
        fitted, font = self._fit_text(draw, text, max(1, right - left), max_size, bold=True, min_size=5)
        self._draw_centered_in_box(draw, (left, top, right, bottom), fitted, font, COLORS["text"])

    def _draw_worldcup_points_text(self, draw, box, event, side, max_size=8):
        text = self._worldcup_group_points_label(event, side)
        left, top, right, bottom = [int(value) for value in box]
        fitted, font = self._fit_text(draw, text, max(1, right - left), max_size, bold=True, min_size=6)
        fill = COLORS["green"] if self._worldcup_group_points_value(event, side) is not None else COLORS["muted"]
        self._draw_centered_in_box(draw, (left, top, right, bottom), fitted, font, fill)

    def _draw_worldcup_country(self, image, draw, flag_url, label, fallback_text, x, y, width, align, compact=False):
        flag_h, flag_cap = (14, 28) if compact else (18, 36)
        flag_w = self._worldcup_flag_display_size(flag_url, fallback_text, flag_cap, flag_h)[0]
        text_gap = 4 if compact else 5
        label_max_size = 14 if compact else 17
        label_min_size = 7 if compact else 8
        label = str(label or fallback_text or "TBD")
        row_h = max(flag_h, 17 if compact else 20)
        flag_y = int(y + (row_h - flag_h) / 2)
        if align == "right":
            text_max = max(16, width - flag_w - text_gap)
            label_text, label_font = self._fit_text(draw, label, text_max, label_max_size, bold=True, min_size=label_min_size)
            text_w = self._text_width(draw, label_text, label_font)
            total_w = min(width, flag_w + text_gap + text_w)
            start_x = int(x + max(0, width - total_w))
            text_x = start_x
            flag_x = int(text_x + text_w + text_gap)
            self._draw_text_in_box(draw, (text_x, y, text_x + text_w, y + row_h), label_text, label_font, COLORS["text"], align="right")
        else:
            text_max = max(16, width - flag_w - text_gap)
            label_text, label_font = self._fit_text(draw, label, text_max, label_max_size, bold=True, min_size=label_min_size)
            text_w = self._text_width(draw, label_text, label_font)
            total_w = min(width, flag_w + text_gap + text_w)
            start_x = int(x)
            flag_x = start_x
            text_x = int(flag_x + flag_w + text_gap)
            self._draw_text_in_box(draw, (text_x, y, text_x + text_w, y + row_h), label_text, label_font, COLORS["text"])
        self._draw_worldcup_flag(image, draw, flag_url, flag_x, flag_y, flag_w, flag_h, fallback_text)

    def _draw_worldcup_flag(self, image, draw, flag_url, x, y, max_width, height, fallback_text, align="left"):
        display_w, display_h = self._worldcup_flag_display_size(flag_url, fallback_text, max_width, height)
        flag = self._load_flag_image(flag_url, (display_w, display_h))
        if align == "right":
            slot_x = int(x + max_width - display_w)
        elif align == "center":
            slot_x = int(x + (max_width - display_w) / 2)
        else:
            slot_x = int(x)
        paste_y = int(y + (height - display_h) / 2)
        if flag:
            image.paste(flag, (slot_x + (display_w - flag.width) // 2, paste_y + (display_h - flag.height) // 2), flag)
            return display_w
        draw.rectangle((slot_x, paste_y, slot_x + display_w, paste_y + display_h), fill=COLORS["panel"], outline=COLORS["border"], width=1)
        fallback = str(fallback_text or "?").strip().upper()[:2] or "?"
        fallback_text, fallback_font = self._fit_text(draw, fallback, max(4, display_w - 3), 9, bold=True, min_size=7)
        self._draw_centered(draw, (slot_x + display_w / 2, paste_y + display_h / 2), fallback_text, fallback_font, COLORS["muted"])
        return display_w

    @staticmethod
    def _worldcup_flag_display_size(flag_url, fallback_text, width, height):
        width = max(1, int(width))
        height = max(1, int(height))
        aspect_ratio = SportsDashboard._worldcup_flag_aspect_ratio(flag_url, fallback_text)
        slot_ratio = width / height
        if aspect_ratio >= slot_ratio:
            display_w = width
            display_h = max(1, min(height, int(round(width / aspect_ratio))))
        else:
            display_h = height
            display_w = max(1, min(width, int(round(height * aspect_ratio))))
        return display_w, display_h

    @staticmethod
    def _worldcup_flag_aspect_ratio(flag_url, fallback_text):
        country_code = SportsDashboard._worldcup_flag_country_code(flag_url, fallback_text)
        return ISO_FLAG_ASPECT_RATIOS.get(country_code, DEFAULT_FLAG_ASPECT_RATIO)

    @staticmethod
    def _worldcup_flag_country_code(flag_url, fallback_text):
        for value in (fallback_text,):
            text = str(value or "").strip().upper()
            if not text:
                continue
            tla = SportsDashboard._canonical_country_tla(text)
            if tla in LOCAL_WORLDCUP_FLAG_TLAS:
                return tla
            country_code = FIFA_TLA_TO_FLAGS_API_CODE.get(tla)
            if country_code:
                return country_code
            if re.fullmatch(r"[A-Z]{2}", text):
                return text
        url = str(flag_url or "")
        local_match = re.fullmatch(rf"{re.escape(LOCAL_WORLDCUP_FLAG_URL_PREFIX)}([a-z]{{3}})", url, re.IGNORECASE)
        if local_match:
            local_tla = local_match.group(1).upper()
            if local_tla in LOCAL_WORLDCUP_FLAG_TLAS:
                return local_tla
        match = re.search(r"flagcdn\.com/(?:[^/]+/)?([A-Z]{2})\.(?:png|svg|webp)", url, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        match = re.search(r"/([A-Z]{2})/flat/", url, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        return ""

    @staticmethod
    def _render_local_worldcup_flag(flag_url, size):
        url = str(flag_url or "").strip().lower()
        if url != f"{LOCAL_WORLDCUP_FLAG_URL_PREFIX}sco":
            return None
        width, height = size
        flag = Image.new("RGBA", (width, height), (0, 94, 184, 255))
        local_draw = ImageDraw.Draw(flag)
        band_width = max(2, int(round(min(width, height) * 0.22)))
        local_draw.line((0, 0, width - 1, height - 1), fill=(255, 255, 255, 255), width=band_width)
        local_draw.line((0, height - 1, width - 1, 0), fill=(255, 255, 255, 255), width=band_width)
        return flag

    @staticmethod
    def _worldcup_api_source_label(source_state, fetched_at):
        fetched = SportsDashboard._parse_cached_utc(fetched_at)
        time_text = fetched.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%I:%M %p").lstrip("0") if fetched else ""
        state = str(source_state or "API").upper()
        if "ESPN" in state and "FOOTBALL" in state:
            prefix = "FD+ESPN"
        elif "ESPN" in state and state.startswith("API"):
            prefix = "API+ESPN"
        elif state == "ESPN LIVE":
            prefix = "ESPN DATA"
        elif state == "ESPN CACHE":
            prefix = "ESPN CACHE"
        elif state == "ESPN STALE":
            prefix = "ESPN STALE"
        elif state == "ESPN LIMIT":
            prefix = "ESPN LIMIT"
        elif state == "FOOTBALL LIVE":
            prefix = "FOOTBALL DATA"
        elif state == "FOOTBALL CACHE":
            prefix = "FD CACHE"
        elif state == "FOOTBALL STALE":
            prefix = "FD STALE"
        elif state == "FOOTBALL LIMIT":
            prefix = "FD LIMIT"
        elif state == "API LIVE":
            prefix = "API DATA"
        elif state == "API STALE":
            prefix = "STALE CACHE"
        elif state == "API LIMIT":
            prefix = "API LIMIT"
        elif state in {"NO DATA", "NO HISTORICAL DATA"}:
            prefix = "NO DATA"
        elif state in {"BACKUP SCHEDULE", "FALLBACK"}:
            prefix = "BACKUP VIEW"
        else:
            prefix = "API CACHE"
        return f"{prefix} {time_text}".strip()

    @staticmethod
    def _clean_worldcup_stage(value):
        text = str(value or "World Cup").strip()
        for prefix in ("World Cup - ", "FIFA World Cup - "):
            if text.startswith(prefix):
                text = text[len(prefix):]
        return text or "World Cup"

    @staticmethod
    def _worldcup_status_color(event, now=None):
        state = str(event.get("state") or "").upper()
        if SportsDashboard._worldcup_is_display_live(event, now):
            return COLORS["worldcup_live"]
        if state in {"FT", "AET", "PEN", "FINISHED", "AWARDED"}:
            return COLORS["green"]
        return COLORS["worldcup_accent"]

    @staticmethod
    def _worldcup_event_status_label(event, now):
        state = str(event.get("state") or "").upper()
        if state in {"FT", "AET", "PEN", "FINISHED", "AWARDED"} and event.get("wins_a") is not None and event.get("wins_b") is not None:
            return f"{event['wins_a']}-{event['wins_b']}"
        if SportsDashboard._worldcup_is_display_live(event, now):
            score = SportsDashboard._score_label(event)
            status = str(event.get("status") or "").strip()
            status_upper = status.upper()
            if event.get("inferred_live") and status_upper in {"", "NS", "NOT STARTED", "SCHEDULED", "TIMED", "INFERRED LIVE"}:
                status = ""
                status_upper = ""
            if score != "vs":
                if status and status_upper not in {"LIVE", "IN PLAY", "IN_PROGRESS", "FIRST HALF", "SECOND HALF"}:
                    return f"{status} {score}"
                return f"LIVE {score}"
            return "LIVE"
        return SportsDashboard._format_time_24h(event["start"])

    @staticmethod
    def _worldcup_main_status_label(event, now):
        label = SportsDashboard._worldcup_event_status_label(event, now)
        if not event or event.get("inferred_live"):
            return label
        is_live_or_final = SportsDashboard._worldcup_is_display_live(event, now) or SportsDashboard._is_worldcup_finished_event(event)
        if not is_live_or_final:
            return label
        if not (event.get("score_confirmed") or event.get("provider_status_confirmed")):
            return label
        source = SportsDashboard._worldcup_status_source_label(event)
        if not source:
            return label
        return f"{source} {label}"

    @staticmethod
    def _worldcup_status_source_label(event):
        source = str((event or {}).get("score_source") or (event or {}).get("provider") or "").strip()
        normalized = source.upper().replace("_", " ").replace("-", " ")
        if "ESPN" in normalized:
            return "ESPN"
        if "FOOTBALL" in normalized or normalized in {"FD", "FOOTBALLDATA"}:
            return "FD"
        if source:
            return source.upper()[:6]
        return ""

    @staticmethod
    def _worldcup_event_time_label(event):
        return SportsDashboard._format_time_24h(event["start"])

    def _render_worldcup_fallback(self, dimensions, visible_matches=DEFAULT_WORLD_CUP_VISIBLE_MATCHES, season=DEFAULT_WORLD_CUP_SEASON):
        visible_matches = max(1, min(WORLD_CUP_VISIBLE_MATCH_LIMIT, int(visible_matches or DEFAULT_WORLD_CUP_VISIBLE_MATCHES)))
        timezone_info = ZoneInfo(DEFAULT_TIMEZONE)
        season = str(season or DEFAULT_WORLD_CUP_SEASON).strip()
        if not season.isdigit() or len(season) != 4:
            season = DEFAULT_WORLD_CUP_SEASON
        if season != DEFAULT_WORLD_CUP_SEASON:
            selected = {
                "live": [],
                "upcoming": [],
                "recent": [],
                "main": None,
                "visible_matches": visible_matches,
                "season": season,
            }
            return self._render_worldcup_api_panel(
                dimensions,
                selected,
                "NO HISTORICAL DATA",
                None,
                visible_matches,
                datetime.now(timezone_info),
            )
        labels = self._worldcup_local_time_labels()
        fallback_events = []
        for index in range(visible_matches):
            hour, minute = [int(part) for part in labels[index].split(":")]
            fallback_events.append(
                {
                    "start": datetime(2026, 6, 11 + index, hour, minute, tzinfo=timezone_info),
                    "state": "SCHEDULED",
                    "status": "Scheduled",
                    "team_a": "Teams",
                    "team_b": "TBD",
                    "team_a_tla": "TBD",
                    "team_b_tla": "TBD",
                    "team_a_flag": "",
                    "team_b_flag": "",
                    "wins_a": None,
                    "wins_b": None,
                    "block": "Opening Match" if index == 0 else "Group Stage",
                }
            )
        selected = self._select_worldcup_event_sections(
            fallback_events,
            datetime.now(timezone_info),
            visible_matches,
        )
        return self._render_worldcup_api_panel(
            dimensions,
            selected,
            "BACKUP SCHEDULE",
            None,
            visible_matches,
            datetime.now(timezone_info),
        )

    def _overlay_worldcup_local_times(self, image, left_width, timezone_info, visible_matches, content_box):
        if self._timezone_key(timezone_info) != DEFAULT_TIMEZONE:
            return
        draw = ImageDraw.Draw(image)
        crop_height = self._worldcup_crop_height(visible_matches)
        content_left, content_top, content_right, content_bottom = content_box
        content_height = max(1, content_bottom - content_top)
        scale_y = content_height / crop_height
        row_times = self._worldcup_local_time_labels()
        row_y = [61, 122, 183, 244, 305, 366, 427]
        font_size = max(14, min(22, int(14 * min(scale_y, 1.6))))
        rect_height = max(23, min(34, int(23 * min(scale_y, 1.6))))
        rect_width = 112 if visible_matches <= 3 else 96
        for y, text in zip(row_y[:visible_matches], row_times[:visible_matches]):
            y1 = content_top + int(y * scale_y)
            y2 = y1 + rect_height
            x1 = max(content_left + 2, min(left_width - rect_width, content_right - rect_width))
            x2 = min(left_width - 2, content_right - 2)
            draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"], outline=COLORS["border"], width=1)
            draw.rectangle((x1, y1, x1 + 5, y2), fill=COLORS["worldcup_accent"])
            value, value_font = self._fit_text(draw, text, x2 - x1 - 8, font_size, bold=True, min_size=11)
            self._draw_right_aligned(draw, (x2 - 6, y1 + 4), value, value_font, COLORS["text"])

    @staticmethod
    def _worldcup_local_time_labels():
        return ["12:00", "19:00", "12:00", "18:00", "12:00", "15:00", "18:00"]
