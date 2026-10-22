"""Shared building blocks for the lower-left sports panels.

Every lower-left panel (NFL, NCAA, NBA, WNBA, MLB, PGA, F1) uses the same
layout: the sport header, a featured card on the left and a list column on
the right. The featured card for team sports is a broadcast-style scorebug
(status pill, two team rows with a line score, then sport details); the
right column lists the other games with their own clocks.

Sport specifics come from a :class:`ScorebugSpec`. Text on the six-ink panel
stays legible only in pure inks, so secondary text is black and the
hierarchy is carried by weight and size; the muted tone and solid red behind
small text dither into specks.
"""

from .common import *

SportsDashboard = None

KIT_FEATURED_CARD_MAX_WIDTH = 318
KIT_SCOREBUG_ROW_HEIGHT = 33
KIT_SCOREBUG_SCORE_WIDTH = 44
KIT_SCOREBUG_PERIOD_WIDTH = 17
KIT_SCOREBOARD_ROW_HEIGHT = 26
KIT_SCOREBOARD_STATE_WIDTH = 44
KIT_TILE_HEIGHT = 36
KIT_TICKER_ROW_HEIGHT = 17
KIT_TICKER_LOGO_SIZE = 12
KIT_TICKER_LOGO_GAP = 3
# Pure panel inks a team colour may map to; white is left out so every team
# stripe stays visible on paper.
KIT_TEAM_INK_KEYS = ("red", "blue", "green", "amber", "text")


class ScorebugSpec:
    """Per-sport hooks for the shared scorebug and scoreboard."""

    def __init__(self, sport, accent, live, tag, tint, **hooks):
        self.sport = sport
        self.accent = accent
        self.live = live
        self.tag = tag
        self.tint = tint
        self.hooks = hooks

    def hook(self, name, *args, default=None):
        function = self.hooks.get(name)
        return function(*args) if function else default


class ScorebugKitMixin:
    # Frame ---------------------------------------------------------------------

    def _draw_kit_panel_frame(self, image, draw, bounds, spec, card, source_state, header=None):
        """Panel background and header; returns (featured card box, list column box)."""
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS[spec.accent], COLORS["panel"], 22, 1)
        if header is None:
            self._draw_standalone_sport_header(image, draw, x1, y1, x2, spec.sport, card, source_state)
        else:
            header(image, draw, (x1, y1, x2, y2))
        content_y = y1 + 57
        bottom = y2 - 7
        card_w = max(240, min(KIT_FEATURED_CARD_MAX_WIDTH, int((x2 - x1 + 1) * 0.575)))
        split_x = x1 + 12 + card_w + 8
        self._draw_vertical_split(draw, split_x, content_y - 5, bottom)
        return (x1 + 12, content_y, split_x - 9, bottom), (split_x + 6, content_y - 2, x2 - 12, bottom)

    def _draw_kit_card(self, draw, bounds, spec, state):
        """Card shell; returns the inner (left, top, right, bottom) content box."""
        x1, y1, x2, y2 = [int(value) for value in bounds]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, COLORS[spec.live] if state == "live" else COLORS[spec.accent])
        return x1 + 16, y1, x2 - 10, y2

    def _draw_kit_empty_card(self, draw, bounds, spec, text):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        self._draw_kit_card(draw, bounds, spec, "scheduled")
        text, font = self._fit_text(draw, text, x2 - x1 - 40, 14, bold=True, min_size=10)
        self._draw_centered_in_box(draw, (x1 + 12, y1, x2, y2), text, font, COLORS["muted"])

    # Status row ---------------------------------------------------------------

    def _draw_kit_status_row(self, draw, x1, y, x2, spec, state, pill, context="", right=""):
        # Live keeps the dashboard's LIVE-pill idiom (light pill, red block,
        # black text): solid red behind small text dithers into specks.
        marker = 9 if state == "live" else 0
        if state == "final":
            fill, text_fill = COLORS["text"], COLORS["paper_text"]
        else:
            fill, text_fill = (COLORS["panel"] if state == "live" else COLORS[spec.tag]), COLORS["text"]
        pill, pill_font = self._fit_text(draw, pill, 132, 12, bold=True, min_size=9)
        pill_w = self._text_width(draw, pill, pill_font) + 16 + marker
        draw.rounded_rectangle((x1, y, x1 + pill_w, y + 18), radius=4, fill=fill, outline=COLORS["border"], width=1)
        if marker:
            draw.rectangle((x1 + 5, y + 4, x1 + 10, y + 14), fill=COLORS[spec.live], outline=COLORS["border"], width=1)
        self._draw_centered_in_box(draw, (x1 + marker, y, x1 + pill_w, y + 18), pill, pill_font, text_fill)
        right_w = 0
        if right:
            right, right_font = self._fit_text(draw, right, 110, 11, bold=False, min_size=8)
            right_w = self._text_width(draw, right, right_font)
            self._draw_text_in_box(draw, (x2 - right_w, y, x2, y + 18), right, right_font, COLORS["text"], align="right")
        budget = x2 - right_w - 10 - (x1 + pill_w + 8)
        if context and budget > 16:
            context, context_font = self._fit_text_ellipsis(draw, context, budget, 11, bold=True, min_size=8)
            self._draw_text_in_box(draw, (x1 + pill_w + 8, y, x1 + pill_w + 8 + budget, y + 18), context, context_font, COLORS["text"])

    # Scorebug -----------------------------------------------------------------

    def _draw_kit_scorebug(self, image, draw, x1, y, x2, event, state, spec):
        """Two team rows with a line score; returns the y below the rows."""
        periods = spec.hook("period_count", event, default=0) if state != "scheduled" else 0
        lines_x1 = x2 - KIT_SCOREBUG_SCORE_WIDTH - 4 - periods * KIT_SCOREBUG_PERIOD_WIDTH
        top = y
        if periods:
            current = spec.hook("current_period", event) if state == "live" else None
            for index in range(periods):
                left = lines_x1 + index * KIT_SCOREBUG_PERIOD_WIDTH
                font = self._font(8, current == index + 1)
                label = spec.hook("period_label", index + 1, default=str(index + 1))
                self._draw_centered_in_box(draw, (left, y, left + KIT_SCOREBUG_PERIOD_WIDTH, y + 9), label, font, COLORS["text"])
            top = y + 10
        for index, side in enumerate(("a", "b")):
            row_y = top + index * KIT_SCOREBUG_ROW_HEIGHT
            if index:
                draw.line((x1, row_y - 1, x2, row_y - 1), fill=COLORS["line"], width=1)
            self._draw_kit_scorebug_row(image, draw, (x1, row_y, x2, row_y + KIT_SCOREBUG_ROW_HEIGHT - 2), event, side, state, lines_x1, periods, spec)
        return top + 2 * KIT_SCOREBUG_ROW_HEIGHT

    def _draw_kit_scorebug_row(self, image, draw, box, event, side, state, lines_x1, periods, spec):
        x1, y1, x2, y2 = box
        prefix = f"team_{side}"
        winner = spec.hook("winner_side", event, default="") if state == "final" else ""
        dimmed = bool(winner) and winner != side
        draw.rectangle((x1, y1 + 3, x1 + 3, y2 - 3), fill=COLORS[self._kit_team_ink_key(event.get(f"{prefix}_color"), spec.accent)])
        logo_size = 24
        self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), x1 + 8, y1 + (y2 - y1 - logo_size) // 2, logo_size, spec.hook("logo_fallback", event, side, default=event.get(f"{prefix}_code")))
        name_x = x1 + 38
        has_ball = state == "live" and spec.hook("possession_side", event, default="") == side
        name_budget = lines_x1 - 8 - name_x - (15 if has_ball else 0)
        name = spec.hook("team_name", event, side, True, default=event.get(prefix) or "TBD")
        name, name_font = self._fit_text(draw, name, name_budget, 16, bold=not dimmed, min_size=11)
        self._draw_text_in_box(draw, (name_x, y1, name_x + name_budget, y1 + 19), name, name_font, COLORS["text"])
        if has_ball:
            spec.hook("possession_icon", self, draw, name_x + self._text_width(draw, name, name_font) + 5, y1 + 6)
        sub_x = name_x
        if state == "live":
            sub_x = spec.hook("row_widget", self, draw, sub_x, y1 + 23, event, side, default=sub_x)
        sub = spec.hook("row_note", event, side, state, default=str(event.get(f"record_{side}") or "").strip())
        if sub and lines_x1 - 8 - sub_x > 16:
            sub, sub_font = self._fit_text_ellipsis(draw, sub, lines_x1 - 8 - sub_x, 9, bold=False, min_size=7)
            self._draw_text_in_box(draw, (sub_x, y1 + 20, lines_x1 - 8, y1 + 30), sub, sub_font, COLORS["text"])
        scores = list(spec.hook("linescore", event, side, default=[]) or [])
        current = spec.hook("current_period", event) if state == "live" else None
        for index in range(periods):
            value = scores[index] if index < len(scores) else None
            if value is None:
                continue
            left = lines_x1 + index * KIT_SCOREBUG_PERIOD_WIDTH
            period_font = self._font(10, current == index + 1)
            self._draw_centered_in_box(draw, (left, y1, left + KIT_SCOREBUG_PERIOD_WIDTH, y2), str(value), period_font, COLORS["text"])
        score_right = x2 - 9 if winner else x2
        score_box = (score_right - KIT_SCOREBUG_SCORE_WIDTH + 6, y1, score_right, y2)
        if state == "scheduled":
            line = spec.hook("pregame_value", event, side, default="")
            if line:
                line, line_font = self._fit_text(draw, line, KIT_SCOREBUG_SCORE_WIDTH, 13, bold=True, min_size=9)
                self._draw_text_in_box(draw, score_box, line, line_font, COLORS["text"], align="right")
            return
        score = event.get(f"wins_{side}")
        score_text, score_font = self._fit_text(draw, "-" if score is None else str(score), score_box[2] - score_box[0], 26, bold=not dimmed, min_size=16)
        self._draw_text_in_box(draw, score_box, score_text, score_font, COLORS["text"], align="right")
        if winner == side:
            middle = (y1 + y2) // 2
            draw.polygon([(x2, middle - 5), (x2, middle + 5), (x2 - 6, middle)], fill=COLORS["text"])

    def _draw_kit_pips(self, draw, x, y, filled, total, fill_key="amber"):
        """Timeout / foul style pips; returns the x after the last pip."""
        filled = max(0, min(total, int(filled)))
        for index in range(total):
            left = x + index * 10
            if index < filled:
                draw.rectangle((left, y, left + 7, y + 3), fill=COLORS[fill_key], outline=COLORS["border"], width=1)
            else:
                draw.rectangle((left, y, left + 7, y + 3), outline=COLORS["border"], width=1)
        return x + total * 10 - 3

    # Details ------------------------------------------------------------------

    def _draw_kit_tiles(self, draw, box, tiles, spec, tile_h=KIT_TILE_HEIGHT):
        x1, y1, x2, _y2 = [int(value) for value in box]
        tiles = [tile for tile in tiles if tile]
        if not tiles:
            return y1
        gap = 6
        tile_w = (x2 - x1 - gap * (len(tiles) - 1)) // len(tiles)
        label_font = self._font(8, False)
        for index, (label, value) in enumerate(tiles):
            left = x1 + index * (tile_w + gap)
            right = left + tile_w
            draw.rounded_rectangle((left, y1, right, y1 + tile_h), radius=4, fill=COLORS[spec.tint], outline=COLORS["border"], width=1)
            self._draw_text_in_box(draw, (left + 7, y1 + 3, right - 4, y1 + 13), label, label_font, COLORS["text"])
            value, value_font = self._fit_text_ellipsis(draw, str(value or "--"), tile_w - 12, 15, bold=True, min_size=9)
            self._draw_text_in_box(draw, (left + 7, y1 + 14, right - 4, y1 + tile_h - 3), value, value_font, COLORS["text"])
        return y1 + tile_h

    def _draw_kit_leader_rows(self, image, draw, x1, y, x2, y_limit, event, leaders, spec, max_rows=3):
        label_font = self._font(8, True)
        for leader in list(leaders or [])[:max_rows]:
            if y + 14 > y_limit:
                break
            draw.rounded_rectangle((x1, y + 1, x1 + 30, y + 13), radius=3, fill=COLORS[spec.tag], outline=COLORS["border"], width=1)
            self._draw_centered_in_box(draw, (x1, y + 1, x1 + 30, y + 13), leader.get("label") or "", label_font, COLORS["text"])
            side = leader.get("side")
            name_x = x1 + 36
            if side in {"a", "b"}:
                prefix = f"team_{side}"
                self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), name_x, y + 1, 12, spec.hook("logo_fallback", event, side, default=event.get(f"{prefix}_code")))
                name_x += 16
            value, value_font = self._fit_text_ellipsis(draw, str(leader.get("value") or ""), (x2 - x1) // 2, 9, bold=False, min_size=8)
            value_w = self._text_width(draw, value, value_font)
            self._draw_text_in_box(draw, (x2 - value_w, y, x2, y + 14), value, value_font, COLORS["text"], align="right")
            name, name_font = self._fit_text_ellipsis(draw, leader.get("name") or "", x2 - value_w - 8 - name_x, 10, bold=True, min_size=8)
            self._draw_text_in_box(draw, (name_x, y, x2 - value_w - 8, y + 14), name, name_font, COLORS["text"])
            y += 17
        return y

    def _draw_kit_labeled_line(self, draw, box, label, text, bold=True):
        """A "LABEL  value" line in pure black, value truncated with an ellipsis."""
        x1, y1, x2, y2 = [int(value) for value in box]
        label_w = 0
        if label:
            label_font = self._font(8, False)
            label_w = self._text_width(draw, label, label_font) + 6
            self._draw_text_in_box(draw, (x1, y1, x1 + label_w, y2), label, label_font, COLORS["text"])
        text, font = self._fit_text_ellipsis(draw, text, x2 - x1 - label_w, 10, bold=bold, min_size=8)
        self._draw_text_in_box(draw, (x1 + label_w, y1, x2, y2), text, font, COLORS["text"])

    def _draw_kit_footer(self, draw, box, text):
        if not text:
            return
        x1, y1, x2, y2 = box
        text, font = self._fit_text_ellipsis(draw, text, x2 - x1, 9, bold=False, min_size=8)
        self._draw_centered_in_box(draw, box, text, font, COLORS["text"])

    def _draw_kit_share_bar(self, draw, box, left_label, right_label, left_share, left_ink, right_ink, label="WIN"):
        """Two-team share bar (win probability); labels in pure black."""
        x1, y1, x2, y2 = [int(value) for value in box]
        left_bold = left_share >= 0.5
        left_font = self._font(9, left_bold)
        right_font = self._font(9, not left_bold)
        label_w = 0
        if label:
            label_w = 28
            self._draw_text_in_box(draw, (x1, y1, x1 + label_w, y2), label, self._font(8, False), COLORS["text"])
        left_w = self._text_width(draw, left_label, left_font)
        right_w = self._text_width(draw, right_label, right_font)
        self._draw_text_in_box(draw, (x1 + label_w, y1, x1 + label_w + left_w, y2), left_label, left_font, COLORS["text"])
        self._draw_text_in_box(draw, (x2 - right_w, y1, x2, y2), right_label, right_font, COLORS["text"], align="right")
        bar_x1 = x1 + label_w + left_w + 6
        bar_x2 = x2 - right_w - 6
        if bar_x2 - bar_x1 < 20:
            return
        middle = (y1 + y2) // 2
        split = int(round(bar_x1 + (bar_x2 - bar_x1) * max(0.0, min(1.0, left_share))))
        draw.rectangle((bar_x1, middle - 3, bar_x2, middle + 3), fill=COLORS["panel"], outline=COLORS["border"], width=1)
        if split > bar_x1:
            draw.rectangle((bar_x1, middle - 3, split, middle + 3), fill=COLORS[left_ink], outline=COLORS["border"], width=1)
        if bar_x2 > split and right_ink != left_ink:
            draw.rectangle((split, middle - 3, bar_x2, middle + 3), fill=COLORS[right_ink], outline=COLORS["border"], width=1)

    # Scoreboard column --------------------------------------------------------

    def _draw_kit_column_header(self, draw, x1, y1, x2, spec, title, counter=""):
        draw.rectangle((x1, y1 + 2, x1 + 8, y1 + 17), fill=COLORS[spec.accent], outline=COLORS["border"], width=1)
        counter_font = self._font(10, True)
        counter_w = self._text_width(draw, counter, counter_font) if counter else 0
        title, title_font = self._fit_text(draw, title, x2 - x1 - 22 - counter_w, 13, bold=True, min_size=9)
        self._draw_text_in_box(draw, (x1 + 13, y1, x2 - counter_w - 4, y1 + 18), title, title_font, COLORS["text"])
        if counter:
            self._draw_text_in_box(draw, (x2 - counter_w, y1 + 1, x2, y1 + 17), counter, counter_font, COLORS[spec.live], align="right")
        draw.line((x1, y1 + 19, x2, y1 + 19), fill=COLORS["border"], width=1)
        return y1 + 23

    def _draw_kit_scoreboard_column(self, image, draw, bounds, games, now, spec, title="SCOREBOARD", empty="No other games"):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        live_count = sum(1 for event in games if SportsDashboard._hub_event_state(event) == "live")
        row_y = self._draw_kit_column_header(draw, x1, y1, x2, spec, title, f"{live_count} LIVE" if live_count else "")
        if not games:
            self._draw_text_in_box(draw, (x1 + 10, row_y + 2, x2, row_y + 18), empty, self._font(10, True), COLORS["muted"])
            return
        capacity = max(0, (y2 - row_y + 1) // KIT_SCOREBOARD_ROW_HEIGHT)
        shown = games[:capacity]
        for index, event in enumerate(shown):
            top = row_y + index * KIT_SCOREBOARD_ROW_HEIGHT
            self._draw_kit_scoreboard_row(image, draw, (x1, top, x2, top + KIT_SCOREBOARD_ROW_HEIGHT - 1), event, now, spec, last=index == len(shown) - 1)

    def _draw_kit_scoreboard_row(self, image, draw, box, event, now, spec, last=False):
        x1, y1, x2, y2 = [int(value) for value in box]
        state = SportsDashboard._hub_event_state(event)
        top_label, bottom_label, top_key = spec.hook("state_labels", event, now, default=("", "", "text"))
        state_x2 = x1 + KIT_SCOREBOARD_STATE_WIDTH
        top_label, top_font = self._fit_text(draw, top_label, KIT_SCOREBOARD_STATE_WIDTH - 4, 10, bold=True, min_size=8)
        self._draw_text_in_box(draw, (x1 + 2, y1 + 1, state_x2, y1 + 12), top_label, top_font, COLORS[top_key])
        if bottom_label:
            bottom_label, bottom_font = self._fit_text(draw, bottom_label, KIT_SCOREBOARD_STATE_WIDTH - 4, 9, bold=False, min_size=7)
            self._draw_text_in_box(draw, (x1 + 2, y1 + 13, state_x2, y1 + 24), bottom_label, bottom_font, COLORS["text"])
        if state == "live" and spec.hook("row_flag", event, default=False):
            draw.rectangle((state_x2 - 1, y1 + 3, state_x2 + 1, y2 - 3), fill=COLORS[spec.live])
        winner = spec.hook("winner_side", event, default="") if state == "final" else ""
        offense = spec.hook("possession_side", event, default="") if state == "live" else ""
        team_x = state_x2 + 6
        for index, side in enumerate(("a", "b")):
            line_y1 = y1 + 1 + index * 12
            line_y2 = line_y1 + 12
            prefix = f"team_{side}"
            dimmed = bool(winner) and winner != side
            self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), team_x, line_y1, 11, spec.hook("logo_fallback", event, side, default=event.get(f"{prefix}_code")))
            score = spec.hook("row_score", event, side, default=event.get(f"wins_{side}")) if state != "scheduled" else None
            score_text = "" if score is None else str(score)
            score_font = self._font(11, not dimmed)
            score_w = self._text_width(draw, score_text, score_font) if score_text else 0
            name_x = team_x + 15
            name_budget = x2 - 2 - score_w - 6 - name_x - (9 if offense == side else 0)
            name, font = self._kit_team_name(draw, spec, event, side, name_budget, 11, not dimmed)
            self._draw_text_in_box(draw, (name_x, line_y1, name_x + name_budget, line_y2), name, font, COLORS["text"])
            if offense == side:
                dot_x = name_x + self._text_width(draw, name, font) + 4
                draw.ellipse((dot_x, line_y1 + 4, dot_x + 5, line_y1 + 9), fill=COLORS["amber"], outline=COLORS["border"], width=1)
            if score_text:
                self._draw_text_in_box(draw, (x2 - 2 - score_w, line_y1, x2 - 2, line_y2), score_text, score_font, COLORS["text"], align="right")
        if not last:
            draw.line((x1, y2, x2, y2), fill=COLORS["line"], width=1)

    def _kit_team_name(self, draw, spec, event, side, budget, size, bold, min_size=None):
        """The team's full name (新英格兰爱国者) when it fits ``budget`` at ``size`` or one
        point smaller, else its short name (爱国者) shrunk or clipped to fit.

        Both teams of a game switch together: a full name is used only when the
        opponent's full name fits the same room, so a matchup never mixes the two."""
        short = spec.hook("team_name", event, side, False, default=None) or event.get(f"team_{side}") or "TBD"
        full = spec.hook("team_name", event, side, True, default=None)
        other = spec.hook("team_name", event, "b" if side == "a" else "a", True, default=None)
        if full and full != short:
            for candidate in (size, size - 1):
                font = self._font(candidate, bold)
                if all(self._text_width(draw, name, font) <= budget for name in (full, other) if name):
                    return full, font
        return self._fit_text_ellipsis(draw, short, budget, size, bold=bold, min_size=min_size or max(7, size - 3))

    def _kit_team_pair_text(self, draw, spec, event, middle, budget, size, bold):
        """"A 3-1 B" with full names when the whole line fits, else with short names."""
        for full in (True, False):
            names = [spec.hook("team_name", event, side, full, default=None) or event.get(f"team_{side}") or "TBD"
                     for side in ("a", "b")]
            text = f"{names[0]} {middle} {names[1]}"
            if full and self._text_width(draw, text, self._font(size, bold)) > budget:
                continue
            return self._fit_text_ellipsis(draw, text, budget, size, bold=bold, min_size=max(7, size - 2))

    def _draw_kit_ticker(self, image, draw, box, games, now, spec, title=None):
        """Compact one-line rows for the other games: clock, teams, score."""
        x1, y1, x2, y2 = [int(value) for value in box]
        live_count = sum(1 for event in games if SportsDashboard._hub_event_state(event) == "live")
        if title is None:
            title = "TONIGHT" if any(SportsDashboard._hub_event_state(event) != "final" for event in games[:4]) else "RESULTS"
        y = self._draw_kit_column_header(draw, x1, y1, x2, spec, title, f"{live_count} LIVE" if live_count else "")
        if not games:
            self._draw_text_in_box(draw, (x1 + 4, y, x2, y + 14), f"No other {spec.sport} games", self._font(9, True), COLORS["muted"])
            return
        capacity = max(0, (y2 - y + 1) // KIT_TICKER_ROW_HEIGHT)
        for index, event in enumerate(games[:capacity]):
            top = y + index * KIT_TICKER_ROW_HEIGHT
            state = SportsDashboard._hub_event_state(event)
            clock, key = SportsDashboard._kit_ticker_clock(event, now, spec)
            clock, clock_font = self._fit_text(draw, clock, 52, 9, bold=True, min_size=7)
            self._draw_text_in_box(draw, (x1, top, x1 + 54, top + 15), clock, clock_font, COLORS[key])
            if state == "live" and spec.hook("row_flag", event, default=False):
                draw.rectangle((x1 + 55, top + 3, x1 + 56, top + 12), fill=COLORS[spec.live])
            winner = spec.hook("winner_side", event, default="") if state == "final" else ""
            if state == "scheduled":
                middle_text = "vs"
            else:
                middle_text = f"{event.get('wins_a') if event.get('wins_a') is not None else '-'}-{event.get('wins_b') if event.get('wins_b') is not None else '-'}"
            middle_font = self._font(10, True)
            middle_w = self._text_width(draw, middle_text, middle_font)
            team_x1 = x1 + 60
            center = (team_x1 + x2) // 2
            self._draw_text_in_box(draw, (center - middle_w // 2 - 1, top, center + middle_w // 2 + 2, top + 15), middle_text, middle_font, COLORS["text"], align="center")
            # Each team's logo sits on the outer side of its name.
            logo_room = KIT_TICKER_LOGO_SIZE + KIT_TICKER_LOGO_GAP
            for side, box_side, align in (
                ("a", (team_x1 + logo_room, top, center - middle_w // 2 - 5, top + 15), "right"),
                ("b", (center + middle_w // 2 + 5, top, x2 - logo_room, top + 15), "left"),
            ):
                text, font = self._kit_team_name(draw, spec, event, side, box_side[2] - box_side[0], 10,
                                                 winner in ("", side), min_size=8)
                self._draw_text_in_box(draw, box_side, text, font, COLORS["text"], align=align)
                text_w = self._text_width(draw, text, font)
                if side == "a":
                    logo_x = box_side[2] - text_w - KIT_TICKER_LOGO_GAP - KIT_TICKER_LOGO_SIZE
                else:
                    logo_x = box_side[0] + text_w + KIT_TICKER_LOGO_GAP
                fallback = spec.hook("logo_fallback", event, side, default=event.get(f"team_{side}_code"))
                self._draw_team_logo(image, draw, event.get(f"team_{side}_logo"), logo_x, top + 2, KIT_TICKER_LOGO_SIZE, fallback)
            if index < min(capacity, len(games)) - 1:
                draw.line((x1, top + KIT_TICKER_ROW_HEIGHT - 1, x2, top + KIT_TICKER_ROW_HEIGHT - 1), fill=COLORS["line"], width=1)

    # Shared labels ------------------------------------------------------------

    @staticmethod
    def _kit_ticker_clock(event, now, spec):
        """One-line clock: 'Q4 2:31', 'FINAL', '4:00 PM' today, 'TUE 6:30PM' later."""
        state = SportsDashboard._hub_event_state(event)
        top, bottom, key = spec.hook("state_labels", event, now)
        if state == "live":
            return f"{top} {bottom}".strip(), key
        if state == "final":
            return ("FINAL/OT" if bottom == "OT" else "FINAL"), key
        start = (event or {}).get("start")
        if not isinstance(start, datetime):
            return "TBD", key
        time_text = SportsDashboard._format_time(start)
        if start.date() == now.date():
            return time_text, key
        return f"{start.strftime('%a').upper()} {time_text.replace(':00', '').replace(' ', '')}", key


    @staticmethod
    def _kit_scoreboard_order(events, main=None):
        """Other games: live first, then upcoming by start, then finals newest first."""
        main = main or {}
        main_id = str(main.get("event_id") or "")
        events = [
            event for event in events or []
            if event is not main and not (main_id and str(event.get("event_id") or "") == main_id)
        ]

        def start_key(event):
            start = event.get("start")
            return start.timestamp() if isinstance(start, datetime) else float("inf")

        by_state = {"live": [], "scheduled": [], "final": []}
        for event in events:
            by_state.setdefault(SportsDashboard._hub_event_state(event), []).append(event)
        live = sorted(by_state["live"], key=lambda event: (start_key(event), str(event.get("event_id") or "")))
        upcoming = sorted(by_state["scheduled"], key=start_key)
        finals = sorted(by_state["final"], key=start_key, reverse=True)
        return live + upcoming + finals

    @staticmethod
    def _kit_start_label(start, now):
        """'TODAY 1:05 PM', 'MON 5:15 PM' within a week, else '10/27 11:25 AM'."""
        if not isinstance(start, datetime):
            return "NEXT"
        time_text = SportsDashboard._format_time(start)
        days = (start.date() - now.date()).days
        if days == 0:
            return f"TODAY {time_text}"
        if 0 < days < 7:
            return f"{start.strftime('%a').upper()} {time_text}"
        return f"{start.strftime('%m/%d')} {time_text}"

    @staticmethod
    def _kit_countdown_label(start, now):
        if not isinstance(start, datetime):
            return "TBD"
        minutes = int((start - now).total_seconds() // 60)
        if minutes <= 0:
            return "SOON"
        if minutes < 60:
            return f"IN {minutes}M"
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return f"IN {hours}H {minutes:02d}M"
        days, hours = divmod(hours, 24)
        return f"IN {days}D {hours}H"

    @staticmethod
    def _kit_schedule_state_labels(event, now, live_labels, live_key, final_bottom=None):
        """(top, bottom, colour key) for a scoreboard row's clock column."""
        event = event or {}
        state = SportsDashboard._hub_event_state(event)
        start = event.get("start")
        if state == "live":
            top, bottom = live_labels(event)
            return top, bottom, live_key
        if state == "final":
            bottom = final_bottom(event) if final_bottom else ""
            if not bottom and isinstance(start, datetime):
                bottom = start.strftime("%m/%d")
            return "FINAL", bottom, "text"
        if not isinstance(start, datetime):
            return "TBD", "", "text"
        time_text = SportsDashboard._format_time(start)
        if start.date() == now.date():
            return time_text, str(event.get("broadcast") or "").strip(), "text"
        return start.strftime("%a").upper(), time_text, "text"

    @staticmethod
    def _kit_clock_label(event, period_label):
        """Live clock for timed periods: 'Q2 2:00', 'HALFTIME', 'END Q1', 'OT 3:12'."""
        event = event or {}
        detail = str(event.get("status_detail") or "").strip().upper()
        period = SportsDashboard._lpl_int_value(event.get("period"))
        clock = str(event.get("clock") or "").strip()
        if "HALF" in detail:
            return "HALFTIME"
        if detail.startswith("END"):
            return f"END {period_label(period)}" if period else detail
        if period:
            label = period_label(period)
            return f"{label} {clock}" if clock else label
        return str(event.get("status_text") or "LIVE").strip().upper() or "LIVE"

    @staticmethod
    def _kit_quarter_label(period):
        period = int(period or 0)
        if period > 4:
            return "OT" if period == 5 else f"{period - 4}OT"
        return f"Q{period}" if period > 0 else ""

    @staticmethod
    def _kit_period_count(*lines, regulation=4, maximum=6):
        played = max((len(line or []) for line in lines), default=0)
        if not played:
            return 0
        return max(regulation, min(maximum, played))

    @staticmethod
    def _kit_spread_for_side(event, side):
        """The point spread from this team's side, e.g. "-12.5" / "+12.5"."""
        event = event or {}
        match = re.fullmatch(r"\s*([A-Za-z]{2,4})\s+([+-]?\d+(?:\.\d+)?)\s*", str(event.get("spread") or ""))
        if not match:
            return ""
        code = match.group(1).upper()
        value = float(match.group(2))
        codes = {
            "a": str(event.get("team_a_code") or "").upper(),
            "b": str(event.get("team_b_code") or "").upper(),
        }
        if code not in codes.values():
            return ""
        own = value if codes[side] == code else -value
        text = f"{own:+.1f}".replace(".0", "")
        return "PK" if own == 0 else text

    @staticmethod
    def _kit_venue_label(event, include_tv=True):
        event = event or {}
        parts = []
        venue = str(event.get("venue") or "").strip()
        city = str(event.get("city") or "").strip()
        if venue:
            parts.append(venue)
        if city and city not in venue:
            parts.append(city)
        broadcast = str(event.get("broadcast") or "").strip()
        if include_tv and broadcast:
            parts.append(f"TV {broadcast}")
        return "  ·  ".join(parts)

    @staticmethod
    def _kit_team_ink_key(color, fallback):
        color = str(color or "").strip().lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", color):
            return fallback
        rgb = tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))
        return min(
            KIT_TEAM_INK_KEYS,
            key=lambda key: sum((channel - ink) ** 2 for channel, ink in zip(rgb, DAY_COLORS[key])),
        )

    @staticmethod
    def _kit_ink_text_key(ink_key):
        """Text colour readable on a solid ink block (judged on the day palette)."""
        red, green, blue = DAY_COLORS.get(ink_key, DAY_COLORS["text"])[:3]
        return "text" if 0.299 * red + 0.587 * green + 0.114 * blue > 150 else "paper_text"
