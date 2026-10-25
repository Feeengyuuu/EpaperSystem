"""Live score wall: every game in play across the lower-panel leagues at once.

Two or three games get a card each, side by side. Four or more become a
two-column board of scoreboard rows; when the best game is a final or a
playoff game it keeps a full-width strip on top. Rows reuse each sport's
ScorebugSpec, so clocks, team names and logos match the league panels.
Text stays in pure black on the six-ink panel.
"""

from .common import *

SportsDashboard = None

WALL_HEADER_HEIGHT = 57
WALL_ROW_HEIGHT = 26
WALL_TAG_WIDTH = 26
WALL_FEATURE_HEIGHT = 56
WALL_COLUMN_GAP = 8
# Below this width the control-room strip in the header is left out rather than shrunk.
WALL_CONTROL_ROOM_MIN_WIDTH = 170
# Up to six remaining games get a small card each (three per row); more use rows.
WALL_TILE_LIMIT = 6
WALL_TILE_COLUMNS = 3


class LowerWallRenderMixin:
    def _draw_lower_live_wall(self, image, draw, bounds, games, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS["red"], COLORS["panel"], 22, 1)
        games = list(games or [])
        content = (x1 + 12, y1 + WALL_HEADER_HEIGHT, x2 - 12, y2 - 7)
        if len(games) <= 3:
            shown = len(games)
            self._draw_wall_columns(image, draw, content, games, now)
        else:
            shown = self._draw_wall_board(image, draw, content, games, now)
        self._draw_wall_header(image, draw, (x1, y1, x2, y2), games, shown)

    # Header ---------------------------------------------------------------------

    def _draw_wall_header(self, image, draw, bounds, games, shown):
        x1, y1, x2, _y2 = bounds
        header_y = y1 + 8
        title_right = self._paste_wall_art(image, LOCAL_LIVE_NOW_WORDMARK_PATH, (x1 + 14, header_y - 3, x1 + 182, header_y + 22))
        if title_right is None:
            draw.rectangle((x1 + 14, header_y + 3, x1 + 24, header_y + 27), fill=COLORS["red"], outline=COLORS["border"], width=1)
            title, title_font = self._fit_text(draw, "LIVE NOW", 150, 22, bold=True, min_size=16)
            self._draw_text_in_box(draw, (x1 + 32, header_y - 2, x1 + 182, header_y + 24), title, title_font, COLORS["text"])
            title_right = x1 + 182
        sports = list(dict.fromkeys(game["sport"] for game in games))
        hidden = len(games) - shown
        summary_right = self._draw_wall_summary(image, draw, (x1 + 16, header_y + 24, x1 + 262, header_y + 39), games, sports,
                                                hidden)
        logo_x = x2 - 96
        for sport in reversed(sports[:4]):
            logo_x -= 34
            self._draw_sport_logo(image, draw, sport, logo_x, header_y + 2, 30, 26)
        # The control room watching its wall of screens fills the header between the title and the
        # logos, only when there is room to show it at a readable size.
        self._paste_wall_art(image, LOCAL_LIVE_WALL_CONTROL_ROOM_PATH,
                             (max(title_right, summary_right) + 12, header_y - 5, logo_x - 8, header_y + 39), align="center",
                             min_width=WALL_CONTROL_ROOM_MIN_WIDTH)
        self._draw_status_pill(draw, x2 - 84, header_y + 3, "LIVE", True)
        draw.line((x1 + 12, y1 + 48, x2 - 12, y1 + 48), fill=COLORS["border"], width=1)

    def _draw_wall_summary(self, image, draw, box, games, sports, hidden):
        """"9 场进行中" then each league's logo with its count; returns where the line ends."""
        x1, y1, x2, y2 = box
        lead = f"{len(games)} 场进行中" + (f"（另有 {hidden} 场未列出）" if hidden > 0 else "")
        lead, font = self._fit_text_ellipsis(draw, lead, x2 - x1, 11, bold=True, min_size=8)
        self._draw_text_in_box(draw, (x1, y1, x2, y2), lead, font, COLORS["text"])
        x = x1 + self._text_width(draw, lead, font) + 8
        counts = {sport: sum(1 for game in games if game["sport"] == sport) for sport in sports}
        for sport in sports:
            count = str(counts[sport])
            if x + 18 + self._text_width(draw, count, font) > x2:
                break
            self._draw_sport_logo(image, draw, sport, x, y1, 16, y2 - y1)
            self._draw_text_in_box(draw, (x + 18, y1, x2, y2), count, font, COLORS["text"])
            x += 18 + self._text_width(draw, count, font) + 8
        return int(x)

    def _paste_wall_art(self, image, path, box, align="left", min_width=0):
        """Paste a decor image contained in box; returns its right edge, or None when it is missing or too narrow."""
        x1, y1, x2, y2 = [int(value) for value in box]
        if x2 - x1 < max(min_width, 1):
            return None
        art = self._load_local_logo(path, (x2 - x1, y2 - y1), alpha_threshold=8)
        if art is None:
            return None
        left = x1 + ((x2 - x1 - art.width) // 2 if align == "center" else 0)
        image.paste(art, (left, y1 + (y2 - y1 - art.height) // 2), art)
        return left + art.width

    # Two or three games: one card each ------------------------------------------

    def _draw_wall_columns(self, image, draw, box, games, now):
        x1, y1, x2, y2 = box
        count = max(1, len(games))
        width = (x2 - x1 - WALL_COLUMN_GAP * (count - 1)) // count
        for index, game in enumerate(games):
            left = x1 + index * (width + WALL_COLUMN_GAP)
            self._draw_wall_card(image, draw, (left, y1, left + width, y2), game, now)

    def _draw_wall_card(self, image, draw, box, game, now):
        x1, y1, x2, y2 = box
        sport, event = game["sport"], game["event"]
        spec = self._wall_spec(sport)
        accent = COLORS[spec.accent] if spec else self._hub_sport_accent(sport)
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        inner_x1, inner_x2 = x1 + 16, x2 - 9
        detail = SportsDashboard._wall_detail_label(event) if spec else ""
        footer = str(event.get("broadcast") or "").strip() if spec else ""
        bottom = y2 - (24 if footer else 8) - (18 if detail else 0)
        row_height = max(30, min(46, (bottom - y1 - 56) // 2))
        rows_top = y1 + 56 + max(0, (bottom - y1 - 56 - 2 * row_height) // 2)
        context = self._wall_context_label(sport, event)
        if spec is not None:
            self._draw_wall_team_colours(draw, (x1, y1, x2, y2), event, accent, y1 + 30, rows_top + row_height)
        # The league's logo is the card's title: bare on the card's colours, at its left edge.
        self._draw_sport_logo(image, draw, sport, inner_x1, y1 + 4, 40, 24, align="left")
        if context:
            context, font = self._fit_text_ellipsis(draw, context, inner_x2 - inner_x1 - 46, 11, bold=True, min_size=8)
            if spec is not None:
                context_w = self._text_width(draw, context, font)
                draw.rounded_rectangle((inner_x2 - context_w - 6, y1 + 7, inner_x2 + 4, y1 + 25), radius=4, fill=COLORS["panel"])
            self._draw_text_in_box(draw, (inner_x1 + 40, y1 + 6, inner_x2, y1 + 26), context, font, COLORS["text"], align="right")
        if spec is None:
            self._draw_wall_golf_card(draw, (inner_x1, y1 + 32, inner_x2, y2 - 6), event)
            return
        clock = self._wall_clock_label(event, now, spec)
        draw.rectangle((inner_x1, y1 + 35, inner_x1 + 5, y1 + 47), fill=COLORS[spec.live], outline=COLORS["border"], width=1)
        clock, clock_font = self._fit_text_ellipsis(draw, clock, inner_x2 - inner_x1 - 12, 13, bold=True, min_size=9)
        self._draw_text_in_box(draw, (inner_x1 + 11, y1 + 32, inner_x2, y1 + 50), clock, clock_font, COLORS["text"])
        for index, side in enumerate(("a", "b")):
            top = rows_top + index * row_height
            if index:
                draw.line((inner_x1, top - 2, inner_x2, top - 2), fill=COLORS["line"], width=1)
            self._draw_wall_team_line(image, draw, (inner_x1, top, inner_x2, top + row_height - 4), event, side, spec)
        if detail:
            detail_top = rows_top + 2 * row_height
            detail, detail_font = self._fit_text_ellipsis(draw, detail, inner_x2 - inner_x1, 11, bold=True, min_size=8)
            self._draw_text_in_box(draw, (inner_x1, detail_top, inner_x2, detail_top + 16), detail, detail_font, COLORS["text"])
        if footer:
            self._draw_kit_footer(draw, (inner_x1, y2 - 20, inner_x2, y2 - 6), f"TV {footer}")

    def _draw_wall_team_line(self, image, draw, box, event, side, spec):
        x1, y1, x2, y2 = box
        logo = min(28, y2 - y1)
        fallback = spec.hook("logo_fallback", event, side, default=event.get(f"team_{side}_code"))
        self._draw_team_logo(image, draw, event.get(f"team_{side}_logo"), x1, y1 + (y2 - y1 - logo) // 2, logo, fallback)
        score = event.get(f"wins_{side}")
        score_text = "-" if score is None else str(score)
        score_text, score_font = self._fit_text(draw, score_text, 44, 24, bold=True, min_size=14)
        score_w = self._text_width(draw, score_text, score_font)
        self._draw_text_in_box(draw, (x2 - score_w, y1, x2, y2), score_text, score_font, COLORS["text"], align="right")
        name, name_font = self._kit_team_name(draw, spec, event, side, x2 - score_w - 8 - (x1 + logo + 6), 14, True, min_size=9)
        self._draw_text_in_box(draw, (x1 + logo + 6, y1, x2 - score_w - 8, y2), name, name_font, COLORS["text"])

    def _draw_wall_golf_card(self, draw, box, event):
        x1, y1, x2, y2 = box
        name = str(event.get("name") or event.get("name_en") or "PGA TOUR")
        name, font = self._fit_text_ellipsis(draw, name, x2 - x1, 12, bold=True, min_size=9)
        self._draw_text_in_box(draw, (x1, y1, x2, y1 + 16), name, font, COLORS["text"])
        top = y1 + 22
        for row in list(event.get("leaderboard") or [])[:5]:
            if top + 16 > y2:
                break
            line = SportsDashboard._wall_golf_line(row)
            line, line_font = self._fit_text_ellipsis(draw, line, x2 - x1, 11, bold=False, min_size=8)
            self._draw_text_in_box(draw, (x1, top, x2, top + 15), line, line_font, COLORS["text"])
            top += 17

    # Four or more: a board of rows ----------------------------------------------

    def _draw_wall_board(self, image, draw, box, games, now):
        """Draw as many games as fit; returns how many were drawn."""
        x1, y1, x2, y2 = box
        drawn = 0
        top = y1
        if games and games[0].get("tier") in {"S", "A"}:
            self._draw_wall_feature(image, draw, (x1, top, x2, top + WALL_FEATURE_HEIGHT), games[0], now)
            top += WALL_FEATURE_HEIGHT + 6
            drawn = 1
        sports = list(dict.fromkeys(game["sport"] for game in games))
        if len(games) - drawn <= WALL_TILE_LIMIT:
            return drawn + self._draw_wall_tiles(image, draw, (x1, top, x2, y2), games[drawn:], now, sports)
        column_w = (x2 - x1 - WALL_COLUMN_GAP) // 2
        capacity = max(0, (y2 - top + 1) // WALL_ROW_HEIGHT)
        # Fill both columns evenly rather than the left one first.
        rows = min(capacity, -(-(len(games) - drawn) // 2))
        if rows:
            self._draw_vertical_split(draw, x1 + column_w + WALL_COLUMN_GAP // 2 + 2, top, top + rows * WALL_ROW_HEIGHT - 2)
        band_top = top + rows * WALL_ROW_HEIGHT + 4
        if y2 - band_top >= 28:
            self._draw_wall_placeholder(image, draw, (x1, band_top, x2, y2), sports[0])
        for column in range(2):
            left = x1 + column * (column_w + WALL_COLUMN_GAP)
            for row in range(rows):
                if drawn >= len(games):
                    return drawn
                row_top = top + row * WALL_ROW_HEIGHT
                last = row == rows - 1 or drawn == len(games) - 1
                self._draw_wall_row(image, draw, (left, row_top, left + column_w, row_top + WALL_ROW_HEIGHT - 1),
                                    games[drawn], now, last)
                drawn += 1
        return drawn

    def _draw_wall_tiles(self, image, draw, box, games, now, sports=()):
        """Small cards, three per row, empty slots decorated; returns how many games were drawn."""
        x1, y1, x2, y2 = box
        if not games:
            return 0
        columns = min(WALL_TILE_COLUMNS, max(2, len(games)))
        rows = -(-len(games) // columns)
        width = (x2 - x1 - WALL_COLUMN_GAP * (columns - 1)) // columns
        height = (y2 - y1 - 6 * (rows - 1)) // rows
        decor = list(sports or [game["sport"] for game in games])
        for index in range(rows * columns):
            left = x1 + (index % columns) * (width + WALL_COLUMN_GAP)
            top = y1 + (index // columns) * (height + 6)
            tile = (left, top, left + width, top + height)
            if index < len(games):
                self._draw_wall_tile(image, draw, tile, games[index], now)
            else:
                self._draw_wall_placeholder(image, draw, tile, decor[(index - len(games)) % len(decor)])
        return len(games)

    def _draw_wall_placeholder(self, image, draw, box, sport):
        """A quiet slot with the league's player art, so an empty space still reads as sport."""
        x1, y1, x2, y2 = [int(value) for value in box]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"], outline=COLORS["line"], width=1)
        room = (max(1, x2 - x1 - 12), max(1, y2 - y1 - 10))
        sport = str(sport or "").upper()
        art = None
        scene = LOCAL_WALL_SLOT_FILLER_PATHS.get(sport)
        if scene and os.path.exists(scene):
            art = self._load_local_logo(scene, room, alpha_threshold=8)
        elif sport == "NBA":
            art = self._load_local_logo(LOCAL_NBA_EMPTY_SLOT_FILLER_PATH, room, alpha_threshold=8)
        else:
            path = SportsDashboard._sport_header_cutout_path(sport)
            cutout = self._load_local_logo(path, room, alpha_threshold=8) if path else None
            if cutout is not None:
                spec = self._wall_spec(sport)
                accent = COLORS[spec.accent] if spec else self._hub_sport_accent(sport)
                art = self._tint_alpha_art(cutout, self._blend(accent, COLORS["text"], 0.58))
        if art is None:
            self._draw_sport_logo(image, draw, sport, x1 + 6, y1 + 5, room[0], room[1])
            return
        image.paste(art, (x1 + (x2 - x1 - art.width) // 2, y1 + (y2 - y1 - art.height) // 2), art)

    def _draw_wall_tile(self, image, draw, box, game, now):
        x1, y1, x2, y2 = box
        sport, event = game["sport"], game["event"]
        spec = self._wall_spec(sport)
        accent = COLORS[spec.accent] if spec else self._hub_sport_accent(sport)
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, accent)
        inner_x1, inner_x2 = x1 + 14, x2 - 7
        line_h = max(16, (y2 - y1 - 24) // 2)
        if spec is not None:
            self._draw_wall_team_colours(draw, (x1, y1, x2, y2), event, accent, y1 + 20, y1 + 20 + line_h)
        self._draw_sport_logo(image, draw, sport, inner_x1, y1 + 4, 26, 14, align="left")
        if spec is None:
            leader = (list(event.get("leaderboard") or []) or [{}])[0]
            line = SportsDashboard._wall_golf_line(leader) or str(event.get("name") or "PGA TOUR")
            line, font = self._fit_text_ellipsis(draw, line, inner_x2 - inner_x1, 11, bold=True, min_size=8)
            self._draw_text_in_box(draw, (inner_x1, y1 + 22, inner_x2, y2 - 4), line, font, COLORS["text"])
            return
        clock = self._wall_clock_label(event, now, spec)
        clock, clock_font = self._fit_text_ellipsis(draw, clock, inner_x2 - inner_x1 - 40, 10, bold=True, min_size=8)
        clock_w = self._text_width(draw, clock, clock_font)
        draw.rounded_rectangle((inner_x2 - clock_w - 5, y1 + 3, inner_x2 + 3, y1 + 18), radius=4, fill=COLORS["panel"])
        self._draw_text_in_box(draw, (inner_x1 + 30, y1 + 3, inner_x2, y1 + 18), clock, clock_font, COLORS["text"], align="right")
        for index, side in enumerate(("a", "b")):
            top = y1 + 21 + index * line_h
            self._draw_wall_tile_team(image, draw, (inner_x1, top, inner_x2, top + line_h - 2), event, side, spec)

    def _draw_wall_tile_team(self, image, draw, box, event, side, spec):
        x1, y1, x2, y2 = box
        logo = min(18, y2 - y1)
        fallback = spec.hook("logo_fallback", event, side, default=event.get(f"team_{side}_code"))
        self._draw_team_logo(image, draw, event.get(f"team_{side}_logo"), x1, y1 + (y2 - y1 - logo) // 2, logo, fallback)
        score = event.get(f"wins_{side}")
        score_text, score_font = self._fit_text(draw, "-" if score is None else str(score), 34, 17, bold=True, min_size=11)
        score_w = self._text_width(draw, score_text, score_font)
        self._draw_text_in_box(draw, (x2 - score_w, y1, x2, y2), score_text, score_font, COLORS["text"], align="right")
        name, name_font = self._kit_team_name(draw, spec, event, side, x2 - score_w - 6 - (x1 + logo + 5), 12, True, min_size=8)
        self._draw_text_in_box(draw, (x1 + logo + 5, y1, x2 - score_w - 6, y2), name, name_font, COLORS["text"])

    def _draw_wall_row(self, image, draw, box, game, now, last):
        x1, y1, x2, y2 = box
        sport, event = game["sport"], game["event"]
        self._draw_sport_logo(image, draw, sport, x1, y1 + 3, WALL_TAG_WIDTH - 4, y2 - y1 - 6)
        spec = self._wall_spec(sport)
        if spec is None:
            leader = (list(event.get("leaderboard") or []) or [{}])[0]
            line = SportsDashboard._wall_golf_line(leader) or str(event.get("name") or "PGA TOUR")
            line, font = self._fit_text_ellipsis(draw, line, x2 - x1 - WALL_TAG_WIDTH, 11, bold=True, min_size=8)
            self._draw_text_in_box(draw, (x1 + WALL_TAG_WIDTH, y1, x2, y2), line, font, COLORS["text"])
            if not last:
                draw.line((x1, y2, x2, y2), fill=COLORS["line"], width=1)
            return
        self._draw_kit_scoreboard_row(image, draw, (x1 + WALL_TAG_WIDTH, y1, x2, y2), event, now, spec, last=True)
        if not last:
            draw.line((x1, y2, x2, y2), fill=COLORS["line"], width=1)

    def _draw_wall_feature(self, image, draw, box, game, now):
        x1, y1, x2, y2 = box
        sport, event = game["sport"], game["event"]
        spec = self._wall_spec(sport)
        if spec is None:
            self._draw_wall_card(image, draw, box, game, now)
            return
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, COLORS[spec.accent])
        self._draw_sport_logo(image, draw, sport, x1 + 14, y1 + 6, 40, 22)
        context = self._wall_context_label(sport, event) or sport
        context, font = self._fit_text_ellipsis(draw, context, 150, 11, bold=True, min_size=8)
        self._draw_text_in_box(draw, (x1 + 14, y1 + 31, x1 + 170, y1 + 47), context, font, COLORS["text"])
        clock = self._wall_clock_label(event, now, spec)
        clock, clock_font = self._fit_text_ellipsis(draw, clock, 110, 13, bold=True, min_size=9)
        clock_w = self._text_width(draw, clock, clock_font)
        draw.rectangle((x2 - 18 - clock_w, y1 + 21, x2 - 13 - clock_w, y1 + 33), fill=COLORS[spec.live], outline=COLORS["border"], width=1)
        self._draw_text_in_box(draw, (x2 - 10 - clock_w, y1 + 17, x2 - 8, y1 + 37), clock, clock_font, COLORS["text"], align="right")
        middle_x1, middle_x2 = x1 + 176, x2 - 26 - clock_w
        center = (middle_x1 + middle_x2) // 2
        score = f"{event.get('wins_a') if event.get('wins_a') is not None else '-'}" \
                f" - {event.get('wins_b') if event.get('wins_b') is not None else '-'}"
        score_font = self._font(22, True)
        score_w = self._text_width(draw, score, score_font)
        self._draw_text_in_box(draw, (center - score_w // 2 - 1, y1 + 12, center + score_w // 2 + 2, y2 - 12),
                               score, score_font, COLORS["text"], align="center")
        for side, left, right in (("a", middle_x1, center - score_w // 2 - 6),
                                  ("b", center + score_w // 2 + 6, middle_x2)):
            logo = 26
            fallback = spec.hook("logo_fallback", event, side, default=event.get(f"team_{side}_code"))
            name, name_font = self._kit_team_name(draw, spec, event, side, max(20, right - left - logo - 6), 13, True, min_size=8)
            name_w = self._text_width(draw, name, name_font)
            if side == "a":
                logo_x = right - name_w - 6 - logo
                self._draw_text_in_box(draw, (right - name_w, y1, right, y2), name, name_font, COLORS["text"], align="right")
            else:
                logo_x = left + name_w + 6
                self._draw_text_in_box(draw, (left, y1, left + name_w, y2), name, name_font, COLORS["text"])
            self._draw_team_logo(image, draw, event.get(f"team_{side}_logo"), logo_x, y1 + (y2 - y1 - logo) // 2, logo, fallback)

    # Team colours ---------------------------------------------------------------

    def _draw_wall_team_colours(self, draw, box, event, accent, split_y, band_bottom_or_split):
        """A game's own colours on its card: a band across the top cut diagonally between
        the away and home colours, and the left edge split the same way beside each
        team's row (``band_bottom_or_split`` is where the second row starts)."""
        x1, y1, x2, y2 = [int(value) for value in box]
        colour_a = self._wall_team_colour(event, "a", accent)
        colour_b = self._wall_team_colour(event, "b", self._blend(accent, COLORS["border"], 0.6))
        band_bottom = split_y - 2
        middle = (x1 + 9 + x2) // 2
        lean = max(4, (band_bottom - y1) // 2)
        draw.polygon([(x1 + 9, y1 + 2), (middle + lean, y1 + 2), (middle - lean, band_bottom), (x1 + 9, band_bottom)], fill=colour_a)
        draw.polygon([(middle + lean, y1 + 2), (x2 - 2, y1 + 2), (x2 - 2, band_bottom), (middle - lean, band_bottom)], fill=colour_b)
        second_row = int(band_bottom_or_split)
        draw.rectangle((x1 + 1, y1 + 1, x1 + 8, second_row - 2), fill=colour_a)
        draw.rectangle((x1 + 1, second_row - 1, x1 + 8, y2 - 1), fill=colour_b)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, outline=COLORS["border"], width=2)
        draw.line((x1 + 9, band_bottom + 1, x2 - 2, band_bottom + 1), fill=COLORS["border"], width=1)

    def _wall_team_colour(self, event, side, fallback):
        """The team's ESPN colour; a near-white one is darkened so it still reads on paper."""
        value = str((event or {}).get(f"team_{side}_color") or "").strip().lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
            return fallback
        colour = tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))
        if 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2] > 205:
            colour = self._blend(colour, COLORS["border"], 0.6)
        return colour

    # Labels -----------------------------------------------------------------------

    def _wall_spec(self, sport):
        sport = str(sport or "").upper()
        if sport in {"NBA", "WNBA"}:
            return self._basketball_spec(sport)
        if sport in {"NFL", "NCAA"}:
            return self._football_spec(sport)
        if sport == "MLB":
            return self._mlb_spec()
        return None

    @staticmethod
    def _wall_clock_label(event, now, spec):
        top, bottom, _key = spec.hook("state_labels", event, now, default=("LIVE", "", "text"))
        return " ".join(part for part in (str(top or "").strip(), str(bottom or "").strip()) if part) or "LIVE"

    @staticmethod
    def _wall_context_label(sport, event):
        """'WORLD SERIES G1', 'FINALS G4', 'WEEK 7': what this game is."""
        event = event or {}
        text = str(event.get("series_game") or event.get("headline") or event.get("week_label") or "").strip()
        text = re.sub(r"\s*-\s*Game\s*(\d+)", r" G\1", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+Game\s+(\d+)", r" G\1", text, flags=re.IGNORECASE)
        text = re.sub(r"^(WNBA|NBA)\s+", "", text, flags=re.IGNORECASE)
        return text.upper()[:32]

    @staticmethod
    def _wall_detail_label(event):
        """Series state or the teams' records under a game card."""
        event = event or {}
        summary = str(event.get("series_summary") or "").strip()
        if summary:
            return summary
        records = [str(event.get(f"record_{side}") or "").strip() for side in ("a", "b")]
        # A postseason feed reports 0-0 records; they say nothing.
        if not all(records) or all(re.fullmatch(r"0-0(-0)?", record) for record in records):
            return ""
        return "  ·  ".join(records)

    @staticmethod
    def _wall_golf_line(row):
        row = row or {}
        name = str(row.get("name") or "").strip()
        if not name:
            return ""
        position = str(row.get("position_label") or row.get("position") or "").strip()
        score = str(row.get("score") or "").strip()
        thru = str(row.get("thru") or "").strip()
        parts = [part for part in (position, name, score, f"({thru})" if thru else "") if part]
        return " ".join(parts)
