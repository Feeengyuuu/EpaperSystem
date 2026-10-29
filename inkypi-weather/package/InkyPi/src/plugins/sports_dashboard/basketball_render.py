"""NBA, WNBA and NHL panels.

NBA ("face-off"): a full-width matchup hero with the two teams facing each
other around the score and clock, a quarter strip, the two teams' scoring /
rebound / assist leaders side by side, and a compact ticker of the other
games tonight.

WNBA during a playoff series ("series tracker"): the series score with a
best-of-N game tracker and the list of series games on the left, and the
current or next game on the right. Outside a series the WNBA uses the
face-off layout.

The NHL uses the face-off layout on its own ice art, with periods (P1-P3, OT) in place of
quarters and goals / assists / points leaders (hockey.py reads the games).

The NBA offseason page is drawn by nba.py and is unchanged.
"""

from .common import *
from .card_backdrops import card_backdrop_box, paint_card_backdrop, paint_list_backdrop
from .scorebug_kit import KIT_TILE_HEIGHT, ScorebugSpec, _kit_inked_where_faint, stat_icon_path
from . import hockey

SportsDashboard = None

BASKETBALL_SPEC_KEYS = {
    "NBA": ("nba_accent", "nba_live", "nba_tag", "panel_blue"),
    "WNBA": ("wnba_accent", "wnba_live", "wnba_tag", "wnba_court"),
    "NHL": ("nhl_accent", "nhl_live", "nhl_tag", "nhl_ice"),
}
# Each sport's card art, and the leader categories its duel shows.
FACEOFF_BACKDROPS = {"NHL": "hockey"}
LEADER_DUEL_LABELS = {"NHL": hockey.LEADER_LABELS}
# A duel names its categories by icon (scorebug_kit.STAT_ICON_SETS). The NHL's also heads each
# side with the team's logo and Chinese name; each leader carries the team's small logo on the outside.
LEADER_DUEL_TEAM_HEADS = ("NHL",)
# A category's icon fits this box in the duel's middle column (a wide pass icon takes its width).
DUEL_ICON_BOX = (24, 17)
DUEL_TEAM_LOGO_SIZE, DUEL_ROW_LOGO_SIZE = 13, 11
# A face-off's networks under its clock, as their logos this tall.
FACEOFF_TV_LOGO_HEIGHT = 11
# A live game this close in the fourth quarter or overtime is flagged.
BASKETBALL_CLUTCH_MARGIN = 5
FACEOFF_HERO_HEIGHT = 88
FACEOFF_CENTER_HALF_WIDTH = 72
# Each side's logo fills most of the face-off's height; its name, as large as fits, leans
# toward the centre so the logo, name and clock read as one line.
FACEOFF_LOGO_SIZE, FACEOFF_NAME_SIZE = 62, 20
# Below this height the leader area stays plain when no player stats exist yet.
LEADERS_PENDING_MIN_HEIGHT = 40
# The series list's roomy rows (date in large type) and their pitch, used when every game fits.
WNBA_SERIES_ROW_HEIGHT, WNBA_SERIES_ROW_PITCH = 20, 22
# The game card's networks, as logos under its tiles.
WNBA_CARD_TV_LOGO_HEIGHT = 18
# Where the series tracker's scores come from (the hub reads ESPN's scoreboard and summaries), as
# that network's logo (assets/logos/tv) in the tracker's top right corner.
WNBA_DATA_SOURCE, WNBA_SOURCE_LOGO_HEIGHT = "espn", 16
# The series tracker: each game's slot over its caption, and a played game's winner logo.
WNBA_TRACKER_HEIGHT, WNBA_SLOT_LOGO_SIZE = 40, 12
# A series game is marked by a tank top with its number on the chest (in place of "G1"): its
# outline in a 100 x 100 box (straps, a scooped neck, deep armholes), and its size on a slot
# and on a schedule row.
GAME_JERSEY_OUTLINE = (
    (24, 2), (37, 2), (39, 9), (43, 15), (50, 18), (57, 15), (61, 9), (63, 2), (76, 2),
    (78, 13), (83, 24), (90, 31), (98, 35), (98, 98), (2, 98), (2, 35), (10, 31), (17, 24), (22, 13),
)
GAME_JERSEY_SLOT_SIZE, GAME_JERSEY_ROW_SIZE = (13, 14), (18, 17)


class BasketballRenderMixin:
    # Panels ------------------------------------------------------------------

    def _draw_nba_compact_panel(self, image, draw, bounds, selected, source_state, now):
        selected = selected or {}
        spec = self._basketball_spec("NBA")
        live = selected.get("live") or []
        pill = "LIVE" if live else ("OFF" if selected.get("offseason") else "NEXT")
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS[spec.accent], COLORS["panel"], 22, 1)
        self._draw_nba_compact_header(image, draw, (x1, y1, x2, y2), source_state, pill, bool(live))
        if selected.get("offseason"):
            # The offseason page (nba.py) uses the whole content area.
            self._draw_nba_compact_offseason_panel(image, draw, x1 + 12, y1 + 58, x2 - 12, y2 - 8, selected, now)
            return
        main = SportsDashboard._nba_featured_event(selected)
        events = [*live, *(selected.get("upcoming") or []), *(selected.get("recent") or [])]
        others = SportsDashboard._kit_scoreboard_order(events, main)
        self._draw_basketball_faceoff(image, draw, (x1 + 12, y1 + 57, x2 - 12, y2 - 7), main, others, now, spec)

    def _draw_wnba_standalone_panel(self, image, draw, bounds, card, source_state, now):
        spec = self._basketball_spec("WNBA")
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS[spec.accent], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "WNBA", card, source_state)
        card = card or {}
        main = card.get("main") or {}
        content = (x1 + 12, y1 + 57, x2 - 12, y2 - 7)
        series = SportsDashboard._basketball_series_games(card.get("events"), main)
        history = SportsDashboard._wnba_series_slots(card.get("series_history"), main)
        if main and (len(series) >= 2 or history):
            self._draw_wnba_series_tracker(image, draw, content, main, series, now, spec, history)
            return
        others = SportsDashboard._kit_scoreboard_order(card.get("events"), main)
        self._draw_basketball_faceoff(image, draw, content, main, others, now, spec)

    def _draw_nhl_standalone_panel(self, image, draw, bounds, card, source_state, now):
        """The NHL's game in the face-off layout, the other games of these days in its ticker."""
        spec = self._basketball_spec("NHL")
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS[spec.accent], COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "NHL", card, source_state)
        card = card or {}
        main = card.get("main") or {}
        others = SportsDashboard._kit_scoreboard_order(card.get("events"), main)
        self._draw_basketball_faceoff(image, draw, (x1 + 12, y1 + 57, x2 - 12, y2 - 7), main, others, now, spec)

    def _draw_nba_compact_header(self, image, draw, bounds, source_state, pill, is_live):
        x1, y1, x2, _y2 = [int(value) for value in bounds]
        header_y = y1 + 8
        self._draw_nba_logo(image, draw, x1 + 14, header_y - 2, 30, 34)
        if not self._draw_local_wordmark(image, LOCAL_NBA_TITLE_WORDMARK_PATH, x1 + 52, header_y - 1, 86, 22):
            title, title_font = self._fit_text(draw, "NBA", 86, 22, bold=True, min_size=17)
            draw.text((x1 + 52, header_y), title, font=title_font, fill=COLORS["text"])
        source_label = self._source_label(source_state)
        if not self._draw_source_credit(image, draw, (x1 + 52, header_y + 22, x1 + 148, header_y + 36), source_label, size=10):
            source_label, source_font = self._fit_text(draw, source_label, 96, 10, bold=True, min_size=7)
            draw.text((x1 + 52, header_y + 22), source_label, font=source_font, fill=COLORS["muted"])
        self._draw_nba_header_court_strip(image, x1 + 150, header_y + 2, x2 - 92, y1 + 47)
        self._draw_status_pill(draw, x2 - 84, header_y + 4, pill, is_live)
        draw.line((x1 + 12, y1 + 48, x2 - 12, y1 + 48), fill=COLORS["border"], width=1)

    def _basketball_spec(self, sport):
        sport = str(sport or "").upper() if str(sport or "").upper() in {"WNBA", "NHL"} else "NBA"
        accent, live, tag, tint = BASKETBALL_SPEC_KEYS[sport]
        basketball = SportsDashboard

        def team_name(event, side, full):
            if sport == "NHL":
                return hockey.display_team(event, side, full=full)
            if sport == "WNBA":
                return basketball._wnba_display_team_from_event(event, side, full=full)
            return basketball._nba_display_team_from_event(event, side, full=full)

        def logo_fallback(event, side):
            if sport == "WNBA":
                return basketball._wnba_logo_fallback(event, side)
            return event.get(f"team_{side}_code") or event.get(f"team_{side}")

        return ScorebugSpec(
            sport,
            accent,
            live,
            tag,
            tint,
            team_name=team_name,
            logo_fallback=logo_fallback,
            winner_side=basketball._nba_winner_side,
            pregame_value=basketball._kit_spread_for_side,
            state_labels=basketball._basketball_state_labels,
            row_flag=basketball._basketball_is_clutch,
        )

    # Face-off layout ---------------------------------------------------------

    def _draw_basketball_faceoff(self, image, draw, box, event, others, now, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        event = event or {}
        if not event:
            self._draw_kit_empty_card(draw, box, spec, f"No {spec.sport} schedule")
            return
        state = SportsDashboard._hub_event_state(event)
        hero_y2 = y1 + FACEOFF_HERO_HEIGHT
        self._draw_hub_card_shell(draw, x1, y1, x2, hero_y2, COLORS[spec.live] if state == "live" else COLORS[spec.accent])
        paint_card_backdrop(image, card_backdrop_box(x1, y1, x2, hero_y2), FACEOFF_BACKDROPS.get(spec.sport, "basketball"),
                            COLORS["panel"])
        center_x = (x1 + 8 + x2) // 2
        self._draw_faceoff_team(image, draw, (x1 + 14, y1 + 4, center_x - FACEOFF_CENTER_HALF_WIDTH, hero_y2 - 4), event, "a", state, spec)
        self._draw_faceoff_team(image, draw, (center_x + FACEOFF_CENTER_HALF_WIDTH, y1 + 4, x2 - 8, hero_y2 - 4), event, "b", state, spec)
        self._draw_faceoff_center(image, draw, (center_x - FACEOFF_CENTER_HALF_WIDTH, y1 + 6, center_x + FACEOFF_CENTER_HALF_WIDTH, hero_y2 - 6), event, state, now, spec)

        strip_y1 = hero_y2 + 6
        strip_y2 = strip_y1 + 20
        self._draw_basketball_period_strip(draw, (x1, strip_y1, x2, strip_y2), event, state, spec, image=image)

        lower_y1 = strip_y2 + 7
        split_x = x1 + 316
        self._draw_vertical_split(draw, split_x + 6, lower_y1, y2)
        if state == "scheduled":
            tiles = [
                ("FACE-OFF" if spec.sport == "NHL" else "TIP-OFF", SportsDashboard._kit_countdown_label(event.get("start"), now)),
                ("TV", str(event.get("broadcast") or "").strip() or "TBD"),
                ("O/U", str(event.get("over_under") or "").replace("O/U", "").strip() or "--"),
            ]
            # The venue already sits on the strip above the tiles.
            self._draw_kit_tiles(draw, (x1, lower_y1 + 4, split_x, y2), tiles, spec, tile_h=min(KIT_TILE_HEIGHT + 10, y2 - lower_y1 - 8), image=image)
        else:
            self._draw_basketball_leader_duel(draw, (x1, lower_y1, split_x, y2), event, spec, image=image)
        self._draw_kit_ticker(image, draw, (split_x + 14, lower_y1 - 2, x2, y2), others, now, spec)
        paint_list_backdrop(image, (split_x + 14, lower_y1 - 2, x2, y2), FACEOFF_BACKDROPS.get(spec.sport, "basketball"),
                            COLORS["panel"], (COLORS["paper"], COLORS["panel"]))

    def _draw_faceoff_team(self, image, draw, box, event, side, state, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        prefix = f"team_{side}"
        winner = SportsDashboard._nba_winner_side(event) if state == "final" else ""
        dimmed = bool(winner) and winner != side
        ink = COLORS[self._kit_team_ink_key(event.get(f"{prefix}_color"), spec.accent)]
        logo_size = min(FACEOFF_LOGO_SIZE, y2 - y1 - 14)
        logo_y = y1 + (y2 - y1 - logo_size) // 2
        name = spec.hook("team_name", event, side, True, default=event.get(prefix) or "TBD")
        record = str(event.get(f"record_{side}") or "").strip()
        name_top = y1 + (y2 - y1 - 40) // 2
        if side == "a":
            draw.rectangle((x1, y1 + 10, x1 + 3, y2 - 10), fill=ink)
            logo_x = x1 + 8
            text_x1, text_x2 = logo_x + logo_size + 6, x2
            align = "right"
        else:
            draw.rectangle((x2 - 3, y1 + 10, x2, y2 - 10), fill=ink)
            logo_x = x2 - 8 - logo_size
            text_x1, text_x2 = x1, logo_x - 6
            align = "left"
        self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), logo_x, logo_y, logo_size, spec.hook("logo_fallback", event, side))
        name, name_font = self._fit_text(draw, name, text_x2 - text_x1, FACEOFF_NAME_SIZE, bold=not dimmed, min_size=10)
        self._draw_text_in_box(draw, (text_x1, name_top, text_x2, name_top + 25), name, name_font, COLORS["text"], align=align)
        note = record
        if state == "scheduled":
            spread = spec.hook("pregame_value", event, side, default="")
            note = "  ".join(part for part in (record, spread) if part)
        if note:
            note, note_font = self._fit_text(draw, note, text_x2 - text_x1, 11, bold=False, min_size=8)
            self._draw_text_in_box(draw, (text_x1, name_top + 27, text_x2, name_top + 40), note, note_font, COLORS["text"], align=align)

    def _draw_faceoff_center(self, image, draw, box, event, state, now, spec):
        x1, y1, x2, y2 = [int(value) for value in box]
        center_x = (x1 + x2) // 2
        pill = SportsDashboard._basketball_pill_label(event, now)
        marker = 9 if state == "live" else 0
        pill, pill_font = self._fit_text(draw, pill, x2 - x1 - 20, 11, bold=True, min_size=8)
        pill_w = self._text_width(draw, pill, pill_font) + 14 + marker
        px1 = center_x - pill_w // 2
        fill = COLORS["text"] if state == "final" else (COLORS["panel"] if state == "live" else COLORS[spec.tag])
        draw.rounded_rectangle((px1, y1, px1 + pill_w, y1 + 16), radius=4, fill=fill, outline=COLORS["border"], width=1)
        if marker:
            draw.rectangle((px1 + 5, y1 + 4, px1 + 10, y1 + 12), fill=COLORS[spec.live], outline=COLORS["border"], width=1)
        self._draw_centered_in_box(draw, (px1 + marker, y1, px1 + pill_w, y1 + 16), pill, pill_font, COLORS["paper_text"] if state == "final" else COLORS["text"])
        if state == "scheduled":
            start = event.get("start")
            time_text = SportsDashboard._format_time(start) if isinstance(start, datetime) else "TBD"
            time_text, time_font = self._fit_text(draw, time_text, x2 - x1, 24, bold=True, min_size=14)
            self._draw_centered_in_box(draw, (x1, y1 + 20, x2, y1 + 50), time_text, time_font, COLORS["text"])
            sub = str(event.get("broadcast") or "").strip()
            networks = sub
        else:
            winner = SportsDashboard._nba_winner_side(event) if state == "final" else ""
            for side, (left, right, align) in (("a", (x1, center_x - 9, "right")), ("b", (center_x + 9, x2, "left"))):
                score = event.get(f"wins_{side}")
                text, font = self._fit_score(draw, "-" if score is None else str(score), right - left, 30,
                                             bold=winner in ("", side), min_size=16, sport=spec.sport, max_height=32)
                self._draw_text_in_box(draw, (left, y1 + 20, right, y1 + 52), text, font, COLORS["text"], align=align)
            draw.line((center_x - 4, y1 + 36, center_x + 4, y1 + 36), fill=COLORS["border"], width=2)
            series = SportsDashboard._basketball_series_label(event)
            networks = "" if series or state != "live" else str(event.get("broadcast") or "").strip()
            sub = series or (networks if state == "live" else SportsDashboard._basketball_context_label(event, state))
        cup = SportsDashboard._nba_event_mark_path(event) if spec.sport == "NBA" else None
        if cup and self._draw_event_mark(image, cup, (x1 + 8, y1 + 53, x2 - 8, y2), align="center"):
            return
        if networks and sub == networks and self._draw_centered_tv_logos(image, draw, (x1, y1 + 56, x2, y1 + 68), networks):
            return
        if sub:
            sub, sub_font = self._fit_text_ellipsis(draw, sub, x2 - x1, 9, bold=False, min_size=8)
            self._draw_centered_in_box(draw, (x1, y1 + 56, x2, y1 + 68), sub, sub_font, COLORS["text"])

    def _draw_centered_tv_logos(self, image, draw, box, label):
        """A broadcast's networks as their logos, centred in ``box``; False when none has a logo."""
        x1, y1, x2, y2 = [int(value) for value in box]
        height = min(FACEOFF_TV_LOGO_HEIGHT, y2 - y1)
        strip = Image.new("RGBA", (x2 - x1, height), (0, 0, 0, 0))
        if not self._draw_kit_tv_logos(strip, ImageDraw.Draw(strip), (0, 0, strip.width, height), label, COLORS["panel"]):
            return False
        bounds = strip.getchannel("A").getbbox()
        if not bounds:
            return False
        used = strip.crop((bounds[0], 0, bounds[2], height))
        image.paste(used, (x1 + (x2 - x1 - used.width) // 2, y1 + (y2 - y1 - height) // 2), used)
        return True

    def _draw_basketball_period_strip(self, draw, box, event, state, spec, image=None):
        x1, y1, x2, y2 = [int(value) for value in box]
        if state == "scheduled":
            headline = str(event.get("headline") or "").strip()
            venue = str(event.get("venue") or "").strip()
            # The arena as its official logo after the headline (and the city after it).
            rest = SportsDashboard._kit_venue_label({**event, "venue": ""}, include_tv=False)
            if image is not None and self._draw_kit_venue_line(image, draw, (x1 + 4, y1, x2 - 4, y2), venue, headline, rest, size=10,
                                         logo_height=min(14, y2 - y1)):
                return
            parts = [
                headline,
                SportsDashboard._kit_venue_label(event, include_tv=False),
            ]
            text = "  ·  ".join(part for part in parts if part)
            if text:
                text, font = self._fit_text_ellipsis(draw, text, x2 - x1 - 8, 10, bold=False, min_size=8)
                self._draw_centered_in_box(draw, (x1, y1, x2, y2), text, font, COLORS["text"])
            return
        scores_a = list(event.get("period_scores_a") or [])
        scores_b = list(event.get("period_scores_b") or [])
        hockey_game = spec.sport == "NHL"
        regulation = hockey.REGULATION_PERIODS if hockey_game else 4
        count = SportsDashboard._kit_period_count(scores_a, scores_b, regulation=regulation, maximum=7) or regulation
        current = SportsDashboard._lpl_int_value(event.get("period")) if state == "live" else None
        gap = 4
        cell_w = (x2 - x1 - gap * (count - 1)) / count
        for index in range(count):
            left = int(x1 + index * (cell_w + gap))
            right = int(left + cell_w)
            active = current == index + 1
            fill = COLORS[spec.tag] if active else COLORS["panel"]
            draw.rounded_rectangle((left, y1, right, y2), radius=3, fill=fill, outline=COLORS["border"], width=2 if active else 1)
            label = hockey.period_label(index + 1) if hockey_game else SportsDashboard._kit_quarter_label(index + 1)
            a = scores_a[index] if index < len(scores_a) else None
            b = scores_b[index] if index < len(scores_b) else None
            value = f"{a}-{b}" if a is not None and b is not None else "—"
            self._draw_text_in_box(draw, (left + 6, y1, left + 26, y2), label, self._font(8, active), COLORS["text"])
            value, value_font = self._fit_score(draw, value, right - left - 31, 11,
                                               bold=active or state == "final", min_size=8,
                                               sport=spec.sport, max_height=y2 - y1 - 2)
            self._draw_text_in_box(draw, (left + 26, y1, right - 5, y2), value, value_font, COLORS["text"], align="right")

    def _draw_basketball_leader_duel(self, draw, box, event, spec, image=None):
        """Each category's leader for both teams, facing each other."""
        x1, y1, x2, y2 = [int(value) for value in box]
        middle = (x1 + x2) // 2
        header_font = self._font(9, True)
        heads = image is not None and spec.sport in LEADER_DUEL_TEAM_HEADS
        for side, align, left, right in (("a", "left", x1 + 2, middle - 20), ("b", "right", middle + 20, x2 - 2)):
            if heads:
                # The team's logo at the outer edge, its Chinese name beside it.
                logo_x = left if side == "a" else right - DUEL_TEAM_LOGO_SIZE
                self._draw_team_logo(image, draw, event.get(f"team_{side}_logo"), logo_x, y1 - 1, DUEL_TEAM_LOGO_SIZE,
                                     spec.hook("logo_fallback", event, side))
                name = spec.hook("team_name", event, side, False, default=event.get(f"team_{side}") or "")
                name_box = ((left + DUEL_TEAM_LOGO_SIZE + 4, y1, right, y1 + 12) if side == "a"
                            else (left, y1, right - DUEL_TEAM_LOGO_SIZE - 4, y1 + 12))
                self._draw_text_in_box(draw, name_box, name, self._font(10, True), COLORS["text"], align=align)
                continue
            code = str(event.get(f"team_{side}_code") or "").upper()
            self._draw_text_in_box(draw, (left, y1, right, y1 + 12), code, header_font, COLORS["text"], align=align)
        self._draw_centered_in_box(draw, (middle - 20, y1, middle + 20, y1 + 12), "LEADERS", self._font(8, False), COLORS["text"])
        draw.line((x1, y1 + 14, x2, y1 + 14), fill=COLORS["border"], width=1)
        leaders = {
            side: {item.get("label"): item for item in event.get(f"team_leaders_{side}") or []}
            for side in ("a", "b")
        }
        y = y1 + 18
        for label in LEADER_DUEL_LABELS.get(spec.sport, ("PTS", "REB", "AST")):
            if y + 16 > y2:
                break
            a = leaders["a"].get(label) or {}
            b = leaders["b"].get(label) or {}
            if not a and not b:
                continue
            path = stat_icon_path(spec.sport, label) if image is not None else None
            icon = self._load_local_logo(path, DUEL_ICON_BOX, alpha_threshold=8) if path else None
            if icon:
                image.paste(icon, (middle - icon.width // 2, y + 8 - icon.height // 2), icon)
            else:
                draw.rounded_rectangle((middle - 15, y + 1, middle + 15, y + 14), radius=3, fill=COLORS[spec.tag],
                                       outline=COLORS["border"], width=1)
                self._draw_centered_in_box(draw, (middle - 15, y + 1, middle + 15, y + 14), label, self._font(8, True),
                                           COLORS["text"])
            values = (SportsDashboard._lpl_int_value(a.get("value")), SportsDashboard._lpl_int_value(b.get("value")))
            for side, item, align in (("a", a, "right"), ("b", b, "left")):
                if not item:
                    continue
                value = str(item.get("value") or "")
                other = values[1] if side == "a" else values[0]
                own = values[0] if side == "a" else values[1]
                value_font = self._font(12, own is not None and (other is None or own >= other))
                value_w = self._text_width(draw, value, value_font)
                # Under team heads, each leader carries the team's small logo at the outer edge.
                outer = DUEL_ROW_LOGO_SIZE + 4 if heads else 0
                if side == "a":
                    value_box = (middle - 21 - value_w, y, middle - 21, y + 15)
                    name_box = (x1 + 2 + outer, y, value_box[0] - 6, y + 15)
                else:
                    value_box = (middle + 21, y, middle + 21 + value_w, y + 15)
                    name_box = (value_box[2] + 6, y, x2 - 2 - outer, y + 15)
                if outer:
                    logo_x = x1 + 2 if side == "a" else x2 - 2 - DUEL_ROW_LOGO_SIZE
                    self._draw_team_logo(image, draw, event.get(f"team_{side}_logo"), logo_x, y + 2, DUEL_ROW_LOGO_SIZE,
                                         spec.hook("logo_fallback", event, side))
                self._draw_text_in_box(draw, value_box, value, value_font, COLORS["text"], align=align)
                name, name_font = self._fit_text_ellipsis(draw, item.get("name") or "", name_box[2] - name_box[0], 10, bold=False, min_size=8)
                self._draw_text_in_box(draw, name_box, name, name_font, COLORS["text"], align=align)
            y += 19
        if y == y1 + 18:
            margin = SportsDashboard._basketball_margin_label(event)
            if margin:
                self._draw_kit_labeled_line(draw, (x1, y, x2, y + 14), "", margin)
                y += 18
            if y2 - y >= LEADERS_PENDING_MIN_HEIGHT:
                self._draw_basketball_leaders_pending(draw, (x1, y, x2, y2), event, spec)

    def _draw_basketball_leaders_pending(self, draw, box, event, spec):
        """Before any player stats: the two teams' jerseys passing a ball across."""
        x1, y1, x2, y2 = [int(value) for value in box]
        caption_y = y2 - 12
        jersey_h = min(40, caption_y - y1 - 2)
        jersey_w = round(jersey_h * 0.9)
        jersey_top = caption_y - 2 - jersey_h
        centers = (round(x1 + (x2 - x1) * 0.22), round(x1 + (x2 - x1) * 0.78))
        for side, cx in zip(("a", "b"), centers):
            ink = self._kit_jersey_ink_key(event.get(f"team_{side}_color"), spec.accent)
            code = str(event.get(f"team_{side}_code") or "").upper()[:3]
            self._draw_basketball_jersey(draw, cx, jersey_top, jersey_w, jersey_h, ink, code)
        start = (centers[0] + jersey_w // 2 + 8, jersey_top + jersey_h * 0.45)
        end = (centers[1] - jersey_w // 2 - 8, start[1])
        lift = max(6, min(jersey_h * 0.5, start[1] - y1 - 10))
        steps = max(8, int(end[0] - start[0]) // 7)
        for step in range(0, steps + 1, 2):
            t = step / steps
            x = start[0] + (end[0] - start[0]) * t
            y = start[1] - lift * 4 * t * (1 - t)
            draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=COLORS["text"])
        t = 0.6
        ball_x = start[0] + (end[0] - start[0]) * t
        ball_y = start[1] - lift * 4 * t * (1 - t)
        self._draw_basketball_ball(draw, ball_x, ball_y, 7)
        for offset in (0, 5):
            draw.line((ball_x - 12 - offset * 2, ball_y + 1 + offset, ball_x - 20 - offset * 2, ball_y + 3 + offset),
                      fill=COLORS["text"], width=1)
        caption_box = (centers[0] + jersey_w // 2 + 4, caption_y, centers[1] - jersey_w // 2 - 4, y2)
        caption, font = self._fit_text(draw, "球员数据稍后更新", caption_box[2] - caption_box[0], 9, bold=True, min_size=8)
        self._draw_centered_in_box(draw, caption_box, caption, font, COLORS["text"])

    @staticmethod
    def _kit_jersey_ink_key(color, fallback):
        """A jersey ink: grey, silver and white team colours print as a black jersey."""
        value = str(color or "").strip().lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{6}", value):
            rgb = [int(value[index:index + 2], 16) for index in (0, 2, 4)]
            if max(rgb) - min(rgb) <= 40:
                return "text"
        return SportsDashboard._kit_team_ink_key(color, fallback)

    def _draw_basketball_jersey(self, draw, cx, top, width, height, ink_key, code):
        """A tank-top jersey: narrow straps, deep armholes, V neck and the team code."""
        def at(dx, dy):
            return (cx + width * dx, top + height * dy)

        armhole = ((0.30, 0.0), (0.33, 0.14), (0.40, 0.28), (0.5, 0.38))
        right = [at(dx, dy) for dx, dy in armhole]
        left = [at(-dx, dy) for dx, dy in reversed(armhole)]
        points = [at(-0.30, 0.0), at(-0.15, 0.0), at(0.0, 0.24), at(0.15, 0.0), *right, at(0.5, 1.0), at(-0.5, 1.0), *left[:-1]]
        draw.polygon(points, fill=COLORS[ink_key], outline=COLORS["border"])
        trim = COLORS[self._kit_ink_text_key(ink_key)]
        draw.line([at(-0.15, 0.04), at(0.0, 0.27), at(0.15, 0.04)], fill=trim, width=1)
        for sign in (-1, 1):
            draw.line([at(sign * (dx - 0.06), dy + 0.04) for dx, dy in armhole[1:]], fill=trim, width=1)
        draw.line([at(-0.46, 0.88), at(0.46, 0.88)], fill=trim, width=1)
        if code:
            font = self._font(max(8, round(height * 0.24)), True)
            self._draw_centered_in_box(draw, (cx - width / 2, top + height * 0.36, cx + width / 2, top + height * 0.86), code, font, trim)

    @staticmethod
    def _draw_basketball_ball(draw, cx, cy, radius):
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=COLORS["amber"], outline=COLORS["border"], width=1)
        draw.line((cx - radius, cy, cx + radius, cy), fill=COLORS["border"], width=1)
        draw.line((cx, cy - radius, cx, cy + radius), fill=COLORS["border"], width=1)
        draw.arc((cx - radius * 1.6, cy - radius, cx - radius * 0.2, cy + radius), 300, 60, fill=COLORS["border"])
        draw.arc((cx + radius * 0.2, cy - radius, cx + radius * 1.6, cy + radius), 120, 240, fill=COLORS["border"])

    # Series tracker layout (WNBA playoffs) -----------------------------------

    def _draw_wnba_series_tracker(self, image, draw, box, main, series, now, spec, history=None):
        x1, y1, x2, y2 = [int(value) for value in box]
        left_x2 = x1 + 300
        state = SportsDashboard._hub_event_state(main)
        self._draw_hub_card_shell(draw, x1, y1, left_x2, y2, COLORS[spec.accent])
        paint_card_backdrop(image, card_backdrop_box(x1, y1, left_x2, y2), "basketball", COLORS["panel"])
        inner_x1, inner_x2 = x1 + 16, left_x2 - 10
        length = SportsDashboard._basketball_series_length(main, series)
        mark = self._draw_event_mark(image, SportsDashboard._wnba_series_mark_path(main), (inner_x1, y1 + 4, inner_x1 + 72, y1 + 48), align="left")
        title = SportsDashboard._basketball_series_title(main)
        # Where the scores come from: ESPN's logo in the card's top right corner.
        source_left = self._draw_wnba_data_source(image, (inner_x1 + 120, y1 + 10, inner_x2, y1 + 38))
        if mark:
            self._draw_text_in_box(draw, (mark[2] + 10, y1 + 8, min(inner_x2, source_left - 6), y1 + 24), title,
                                   self._font(13, True), COLORS["text"])
            self._draw_text_in_box(draw, (mark[2] + 10, y1 + 26, inner_x2, y1 + 40), f"BEST OF {length}", self._font(9, False), COLORS["text"])
            score_y = y1 + 52
        else:
            title, title_font = self._fit_text(draw, title, inner_x2 - inner_x1 - 70, 13, bold=True, min_size=9)
            self._draw_text_in_box(draw, (inner_x1, y1 + 6, inner_x2 - 70, y1 + 22), title, title_font, COLORS["text"])
            self._draw_text_in_box(draw, (inner_x2 - 70, y1 + 6, inner_x2, y1 + 22), f"BEST OF {length}", self._font(9, False), COLORS["text"], align="right")
            score_y = y1 + 28

        wins = SportsDashboard._basketball_series_wins(main, series)
        middle = (inner_x1 + inner_x2) // 2
        for side, align in (("a", "right"), ("b", "left")):
            prefix = f"team_{side}"
            logo_x = inner_x1 if side == "a" else inner_x2 - 30
            self._draw_team_logo(image, draw, main.get(f"{prefix}_logo"), logo_x, score_y + 2, 30, spec.hook("logo_fallback", main, side))
            name_box = (inner_x1 + 36, score_y + 4, middle - 34, score_y + 32) if side == "a" else (middle + 34, score_y + 4, inner_x2 - 36, score_y + 32)
            name, name_font = self._kit_team_name(draw, spec, main, side, name_box[2] - name_box[0], 14, True, min_size=9)
            self._draw_text_in_box(draw, name_box, name, name_font, COLORS["text"], align="left" if side == "a" else "right")
            win_box = (middle - 30, score_y, middle - 8, score_y + 34) if side == "a" else (middle + 8, score_y, middle + 30, score_y + 34)
            value, font = self._fit_score(draw, str(wins[side]), win_box[2] - win_box[0], 28,
                                          bold=wins[side] >= wins["b" if side == "a" else "a"],
                                          sport=spec.sport, max_height=34)
            self._draw_text_in_box(draw, win_box, value, font, COLORS["text"], align=align)
        draw.line((middle - 4, score_y + 17, middle + 4, score_y + 17), fill=COLORS["border"], width=2)

        tracker_y = score_y + 36
        self._draw_basketball_series_slots(image, draw, (inner_x1, tracker_y, inner_x2, tracker_y + WNBA_TRACKER_HEIGHT),
                                           main, series, length, spec, history, now)

        # The games still to play, each led by its date; the results are in the slots above.
        ahead = [event for event in series if SportsDashboard._hub_event_state(event) != "final"]
        list_y = tracker_y + WNBA_TRACKER_HEIGHT + 4
        # Roomy rows with the date large when the whole list fits, else compact ones.
        tall = len(ahead) * WNBA_SERIES_ROW_PITCH - 2 <= y2 - 6 - list_y
        row_h, pitch = (WNBA_SERIES_ROW_HEIGHT, WNBA_SERIES_ROW_PITCH) if tall else (14, 16)
        for event in ahead:
            if list_y + row_h > y2 - 6:
                break
            self._draw_wnba_series_game_row(image, draw, (inner_x1, list_y, inner_x2, list_y + row_h), event, now, spec)
            list_y += pitch
        # Room left under a short series list takes the league's mascot scene.
        self._draw_kit_spare_filler(image, (inner_x1, list_y + 2, inner_x2, y2 - 6), spec)

        self._draw_wnba_series_game_card(image, draw, (left_x2 + 10, y1, x2, y2), main, now, spec, state)

    def _draw_wnba_data_source(self, image, box):
        """The scores' source (ESPN) as its official logo WNBA_SOURCE_LOGO_HEIGHT tall, right-aligned and
        centred in ``box``, inked where it would fade into the card; the logo's left edge, or the box's
        right edge without it."""
        x1, y1, x2, y2 = [int(value) for value in box]
        path = os.path.join(LOCAL_TV_LOGO_DIR, f"{WNBA_DATA_SOURCE}.png")
        logo = self._load_local_logo(path, (x2 - x1, WNBA_SOURCE_LOGO_HEIGHT), alpha_threshold=8) if os.path.exists(path) else None
        if not logo:
            return x2
        logo = _kit_inked_where_faint(logo, COLORS["panel"])
        image.paste(logo, (x2 - logo.width, (y1 + y2 - logo.height) // 2), logo)
        return x2 - logo.width

    def _draw_basketball_series_slots(self, image, draw, box, main, series, length, spec, history=None, now=None):
        """One slot per game, its number at the top left, over its date. A played game shows
        the winner's logo beside the number under a band in their colour, and the score,
        winner's first; the game on now or next up is filled (LIVE and its score, or 今天 and
        the tip-off); a later one shows its weekday and tip-off."""
        x1, y1, x2, y2 = [int(value) for value in box]
        gap = 4
        slot_w = (x2 - x1 - gap * (length - 1)) / max(1, length)
        slots = {**SportsDashboard._wnba_slots_from_events(series, main), **(history or {})}
        main_number = SportsDashboard._basketball_game_number(main)
        sides = {str(main.get("team_a_code") or ""): "a", str(main.get("team_b_code") or ""): "b"}
        caption_font, line_font, bold_font = self._font(8, False), self._font(9, False), self._font(9, True)
        bottom = y2 - 11
        for number in range(1, length + 1):
            left = int(x1 + (number - 1) * (slot_w + gap))
            right = int(left + slot_w)
            game = slots.get(number) or {}
            state, start = game.get("state") or "scheduled", game.get("start")
            dated = isinstance(start, datetime)
            if dated:
                self._draw_centered_in_box(draw, (left - 2, bottom + 1, right + 2, y2), f"{start:%m/%d}", caption_font, COLORS["text"])
            scores = game.get("scores") or {}
            side = sides.get(game.get("winner") or "")
            label_box = (left + 3, y1 + 4, right, y1 + 16)
            if state == "final" and side:
                draw.rounded_rectangle((left, y1, right, bottom), radius=3, fill=COLORS["panel"], outline=COLORS["border"], width=1)
                ink_key = self._kit_team_ink_key(main.get(f"team_{side}_color"), spec.accent)
                draw.rectangle((left + 1, y1 + 1, right - 1, y1 + 3), fill=COLORS[ink_key])
                # The game's tank top in the winner's colour.
                self._draw_game_jersey(image, draw, label_box[:2], GAME_JERSEY_SLOT_SIZE, number, COLORS[ink_key],
                                       COLORS[self._kit_ink_text_key(ink_key)], spec)
                size = WNBA_SLOT_LOGO_SIZE
                self._draw_team_logo(image, draw, main.get(f"team_{side}_logo"), right - size - 3, y1 + 5, size,
                                     spec.hook("logo_fallback", main, side))
                points = sorted((value for value in scores.values() if value is not None), reverse=True)
                if len(points) == 2:
                    value, font = self._fit_score(draw, f"{points[0]}-{points[1]}", right - left - 4, 9, bold=True,
                                                  sport=spec.sport, max_height=bottom - y1 - 18)
                    self._draw_centered_in_box(draw, (left, y1 + 17, right, bottom - 1), value, font,
                                               COLORS["text"])
                continue
            current = state == "live" or number == main_number
            draw.rounded_rectangle((left, y1, right, bottom), radius=3, fill=COLORS[spec.tag] if current else COLORS["panel"],
                                   outline=COLORS["border"], width=2 if current else 1)
            if state == "live":
                status = "LIVE"
                detail = "-".join(str(scores.get(str(main.get(f"team_{key}_code") or ""), "")) for key in ("a", "b"))
            elif dated:
                status = "今天" if now is not None and start.date() == now.date() else "周" + "一二三四五六日"[start.weekday()]
                detail = f"{start.hour % 12 or 12}:{start:%M}"
            else:
                status, detail = "", "—"
            self._draw_game_jersey(image, draw, label_box[:2], GAME_JERSEY_SLOT_SIZE, number, COLORS["panel"],
                                   COLORS["text"], spec)
            if status:
                status_box = (left + 4 + GAME_JERSEY_SLOT_SIZE[0], y1 + 4, right - 2, y1 + 16)
                status, status_font = self._fit_text(draw, status, status_box[2] - status_box[0], 9 if current else 8,
                                                     bold=current, min_size=7)
                self._draw_centered_in_box(draw, status_box, status, status_font, COLORS["text"])
            detail_font = bold_font if current else line_font
            if state == "live":
                detail, detail_font = self._fit_score(draw, detail, right - left - 4, 9, bold=current,
                                                      sport=spec.sport, max_height=bottom - y1 - 18)
            self._draw_centered_in_box(draw, (left, y1 + 17, right, bottom - 1), detail, detail_font,
                                       COLORS["text"])

    @staticmethod
    def _wnba_slots_from_events(series, main):
        """{game number: slot} from the scoreboard's series games (state, start, winner's code,
        scores by code)."""
        slots = {}
        for event in series or []:
            number = SportsDashboard._basketball_game_number(event)
            if not number:
                continue
            state = SportsDashboard._hub_event_state(event)
            side = SportsDashboard._nba_winner_side(event) if state == "final" else ""
            codes = {key: str(event.get(f"team_{key}_code") or "") for key in ("a", "b")}
            slots[number] = {"state": state, "start": event.get("start"), "winner": codes.get(side, ""),
                             "scores": {codes[key]: SportsDashboard._lpl_int_value(event.get(f"wins_{key}")) for key in ("a", "b")}}
        return slots

    @staticmethod
    def _wnba_series_slots(history, main):
        """{game number: slot} from the stored series history when it is the main game's series."""
        teams = {str((main or {}).get("team_a_code") or ""), str((main or {}).get("team_b_code") or "")}
        games = [game for game in history or [] if set(game.get("codes") or []) == teams]
        return {game["number"]: game for game in games} if games and len(games) == len(history) else {}

    def _draw_game_jersey(self, image, draw, origin, size, number, fill, ink, spec):
        """Series game ``number`` as a tank top ``size`` big at ``origin``: ``fill`` inside an ink
        outline, the number on its chest in the league's score type in ``ink``."""
        x, y = [int(value) for value in origin]
        width, height = [int(value) for value in size]
        scale = 4
        layer = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        outline = [((px / 100) * (width * scale - 1), (py / 100) * (height * scale - 1)) for px, py in GAME_JERSEY_OUTLINE]
        ImageDraw.Draw(layer).polygon(outline, fill=(*fill[:3], 255), outline=(*COLORS["text"][:3], 255), width=scale)
        layer = layer.resize((width, height), Image.Resampling.LANCZOS)
        image.paste(layer, (x, y), layer)
        if not number:
            return
        chest = (x + 1, y + round(height * 0.3), x + width - 1, y + height - 1)
        text, font = self._fit_score(draw, str(number), chest[2] - chest[0], max(7, chest[3] - chest[1] + 2), bold=True,
                                     sport=spec.sport, max_height=chest[3] - chest[1])
        self._draw_centered_in_box(draw, chest, text, font, ink)

    def _draw_wnba_series_game_row(self, image, draw, box, event, now, spec):
        """A series game as a schedule row led by its date: the game tag (filled for today's
        game), the date in bold, the weekday and tip-off time (or the live clock, or FINAL)
        after it, then the networks' logos or the result at the right."""
        x1, y1, x2, y2 = [int(value) for value in box]
        tall = y2 - y1 >= WNBA_SERIES_ROW_HEIGHT
        state = SportsDashboard._hub_event_state(event)
        number = SportsDashboard._basketball_game_number(event)
        start = event.get("start")
        today = isinstance(start, datetime) and start.date() == now.date()
        # The game's tank top, filled for today's game or the one on now.
        width, height = GAME_JERSEY_ROW_SIZE if tall else (GAME_JERSEY_ROW_SIZE[0] - 3, y2 - y1)
        self._draw_game_jersey(image, draw, (x1 + 2, y1 + (y2 - y1 - height) // 2), (width, height), number,
                               COLORS[spec.tag] if today or state == "live" else COLORS["panel"], COLORS["text"], spec)
        x = x1 + 27
        if isinstance(start, datetime):
            date_font = self._font(13 if tall else 10, True)
            date = f"{start:%m/%d}"
            self._draw_text_in_box(draw, (x, y1, x + 44, y2), date, date_font, COLORS["text"])
            x += self._text_width(draw, date, date_font) + 5
        top, bottom, key = spec.hook("state_labels", event, now)
        if state == "live":
            when = f"{top} {bottom}".strip()
        elif state == "final":
            when = "FINAL" + (f" {bottom}" if bottom in ("OT",) else "")
        elif isinstance(start, datetime):
            when = f"{'今天' if today else '周' + '一二三四五六日'[start.weekday()]} {SportsDashboard._format_time(start)}"
        else:
            when = top
        when, when_font = self._fit_text(draw, when, x1 + 132 - x, 10 if tall else 9, bold=state == "live", min_size=7)
        self._draw_text_in_box(draw, (x, y1, x1 + 132, y2), when, when_font, COLORS[key])
        if state == "scheduled":
            # The networks stay small beside the date: logos of a compact row's height.
            inset = (y2 - y1 - 14) // 2
            self._draw_series_row_broadcast(image, draw, (x1 + 134, y1 + inset, x2, y2 - inset), event)
            return
        winner = SportsDashboard._nba_winner_side(event) if state == "final" else ""
        logo_room = 16 if winner else 0
        text, font = self._kit_team_pair_text(draw, spec, event, f"{event.get('wins_a')}-{event.get('wins_b')}",
                                              x2 - x1 - 136 - logo_room, 10, True)
        self._draw_text_in_box(draw, (x1 + 134, y1, x2, y2), text, font, COLORS["text"], align="right")
        if winner:
            self._draw_series_row_winner_logo(image, draw, event, winner, x2 - self._text_width(draw, text, font) - logo_room, y1, y2, spec)

    def _draw_wnba_series_game_card(self, image, draw, box, event, now, spec, state):
        x1, y1, x2, y2 = [int(value) for value in box]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, COLORS[spec.live] if state == "live" else COLORS[spec.accent])
        left, right = x1 + 16, x2 - 8
        number = SportsDashboard._basketball_game_number(event)
        pill = SportsDashboard._basketball_pill_label(event, now)
        self._draw_kit_status_row(draw, left, y1 + 8, right, spec, state, pill, "", f"GAME {number}" if number else "")
        y = y1 + 34
        winner = SportsDashboard._nba_winner_side(event) if state == "final" else ""
        for side in ("a", "b"):
            prefix = f"team_{side}"
            dimmed = bool(winner) and winner != side
            draw.rectangle((left, y + 3, left + 3, y + 25), fill=COLORS[self._kit_team_ink_key(event.get(f"{prefix}_color"), spec.accent)])
            self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), left + 8, y + 3, 22, spec.hook("logo_fallback", event, side))
            if state == "scheduled":
                value = spec.hook("pregame_value", event, side, default="")
                value_font = self._font(12, True)
            else:
                value = "-" if event.get(f"wins_{side}") is None else str(event.get(f"wins_{side}"))
                value, value_font = self._fit_score(draw, value, 62, 22, bold=not dimmed,
                                                    sport=spec.sport, max_height=28)
            value_w = self._text_width(draw, value, value_font) if value else 0
            name, name_font = self._kit_team_name(draw, spec, event, side, right - value_w - 8 - (left + 36), 14,
                                                  not dimmed, min_size=9)
            self._draw_text_in_box(draw, (left + 36, y, right - value_w - 8, y + 28), name, name_font, COLORS["text"])
            if value:
                self._draw_text_in_box(draw, (right - value_w, y, right, y + 28), value, value_font, COLORS["text"], align="right")
            y += 31
        if state == "scheduled":
            tiles = [
                ("TIP-OFF", SportsDashboard._kit_countdown_label(event.get("start"), now)),
                ("O/U", str(event.get("over_under") or "").replace("O/U", "").strip() or "--"),
            ]
            tiles_bottom = self._draw_kit_tiles(draw, (left, y + 4, right, y2), tiles, spec)
            # The networks as their official logos in the room under the tiles.
            self._draw_kit_broadcast_logos(image, draw, (left, tiles_bottom + 4, right, y2 - 15),
                                           str(event.get("broadcast") or "").strip(), WNBA_CARD_TV_LOGO_HEIGHT)
        else:
            leaders = list(event.get("leaders") or [])[:2]
            # With room for them, the leaders are set large: icon, name and the value with its unit.
            self._draw_kit_leader_rows(image, draw, left, y + 6, right, y2 - 14, event, leaders, spec, max_rows=2,
                                       large=y2 - 14 - (y + 6) >= 2 * 28)
        self._draw_kit_venue(image, draw, (left, y2 - 14, right, y2 - 2), str(event.get("venue") or "").strip())

    # Labels ------------------------------------------------------------------

    @staticmethod
    def _nba_featured_event(selected):
        selected = selected or {}
        live = selected.get("live") or []
        upcoming = selected.get("upcoming") or []
        return live[0] if live else (upcoming[0] if upcoming else selected.get("main"))

    @staticmethod
    def _basketball_series_games(events, main):
        """Games between the main matchup's two teams that belong to its series."""
        main = main or {}
        if not main or not (main.get("series_summary") or (main.get("series_length") or 0) > 1):
            return []
        teams = {str(main.get("team_a_code") or ""), str(main.get("team_b_code") or "")}
        games = [
            event for event in events or []
            if {str(event.get("team_a_code") or ""), str(event.get("team_b_code") or "")} == teams
            and (event.get("series_summary") or SportsDashboard._basketball_game_number(event))
        ]
        return sorted(games, key=lambda event: event.get("start") or datetime.max.replace(tzinfo=timezone.utc))

    @staticmethod
    def _basketball_game_number(event):
        match = re.search(r"\bGame\s+(\d+)", str((event or {}).get("headline") or ""), flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def _basketball_series_title(event):
        headline = str((event or {}).get("headline") or "").strip()
        title = re.split(r"\s+-\s+Game\s+\d+", headline, flags=re.IGNORECASE)[0].strip()
        return (title or "PLAYOFF SERIES").upper()

    @staticmethod
    def _basketball_series_length(main, series):
        length = SportsDashboard._lpl_int_value((main or {}).get("series_length"))
        numbers = [SportsDashboard._basketball_game_number(event) or 0 for event in series]
        return max(3, min(7, length or max(numbers + [5])))

    @staticmethod
    def _basketball_series_game_winner(main, event):
        """'a'/'b' in the main game's orientation for a finished series game."""
        winner = SportsDashboard._nba_winner_side(event)
        if not winner:
            return ""
        code = str(event.get(f"team_{winner}_code") or "")
        return "a" if code == str(main.get("team_a_code") or "") else "b"

    @staticmethod
    def _basketball_series_wins(main, series):
        wins = {"a": 0, "b": 0}
        for event in series:
            if SportsDashboard._hub_event_state(event) == "final":
                side = SportsDashboard._basketball_series_game_winner(main, event)
                if side:
                    wins[side] += 1
        recorded = (SportsDashboard._lpl_int_value(main.get("series_wins_a")), SportsDashboard._lpl_int_value(main.get("series_wins_b")))
        if all(value is not None for value in recorded) and sum(recorded) > sum(wins.values()):
            wins = {"a": recorded[0], "b": recorded[1]}
        return wins

    @staticmethod
    def _basketball_live_clock_label(event):
        label = SportsDashboard._kit_clock_label(event, SportsDashboard._kit_quarter_label)
        if label in {"LIVE", ""}:
            text = str((event or {}).get("status_text") or "").strip().upper()
            return text or "LIVE"
        return label

    @staticmethod
    def _basketball_pill_label(event, now):
        state = SportsDashboard._hub_event_state(event)
        if state == "live":
            return SportsDashboard._basketball_live_clock_label(event)
        if state == "final":
            detail = str((event or {}).get("status_detail") or (event or {}).get("status_text") or "").upper()
            return "FINAL/OT" if "OT" in detail else "FINAL"
        return SportsDashboard._kit_start_label((event or {}).get("start"), now)

    @staticmethod
    def _basketball_context_label(event, state):
        event = event or {}
        if state == "final":
            start = event.get("start")
            return start.strftime("%m/%d") if isinstance(start, datetime) else ""
        return str(event.get("broadcast") or "").strip() if state == "live" else ""

    @staticmethod
    def _basketball_series_label(event):
        """Playoff series line, e.g. 'GS LEADS 1-0'."""
        event = event or {}
        summary = str(event.get("series_summary") or "").strip()
        if summary:
            return summary.upper().replace("LEADS SERIES", "LEADS").replace("SERIES ", "")
        wins_a = SportsDashboard._lpl_int_value(event.get("series_wins_a"))
        wins_b = SportsDashboard._lpl_int_value(event.get("series_wins_b"))
        if wins_a is None or wins_b is None or (wins_a == 0 and wins_b == 0):
            return ""
        return f"SERIES {wins_a}-{wins_b}"

    @staticmethod
    def _basketball_margin_label(event):
        winner = SportsDashboard._nba_winner_side(event)
        if not winner:
            return ""
        score_a = SportsDashboard._lpl_int_value((event or {}).get("wins_a"))
        score_b = SportsDashboard._lpl_int_value((event or {}).get("wins_b"))
        if score_a is None or score_b is None:
            return ""
        team = SportsDashboard._nba_display_team_from_event(event, winner, full=True)
        return f"{team} 胜{abs(score_a - score_b)}分"

    @staticmethod
    def _basketball_is_clutch(event):
        event = event or {}
        period = SportsDashboard._lpl_int_value(event.get("period")) or 0
        score_a = SportsDashboard._lpl_int_value(event.get("wins_a"))
        score_b = SportsDashboard._lpl_int_value(event.get("wins_b"))
        if period < 4 or score_a is None or score_b is None:
            return False
        return abs(score_a - score_b) <= BASKETBALL_CLUTCH_MARGIN

    @staticmethod
    def _basketball_state_labels(event, now):
        def live_labels(live_event):
            clock = SportsDashboard._basketball_live_clock_label(live_event)
            if clock == "HALFTIME":
                return "HALF", ""
            head, _separator, tail = clock.partition(" ")
            return head, tail

        def final_bottom(final_event):
            detail = str(final_event.get("status_detail") or final_event.get("status_text") or "").upper()
            return "OT" if "OT" in detail else ""

        live_key = {"WNBA": "wnba_live", "NHL": "nhl_live"}.get(str((event or {}).get("sport") or "").upper(), "nba_live")
        return SportsDashboard._kit_schedule_state_labels(event, now, live_labels, live_key, final_bottom)
